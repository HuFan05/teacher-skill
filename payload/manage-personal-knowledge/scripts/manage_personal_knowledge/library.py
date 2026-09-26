from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import sqlite3
import stat
from typing import Any, Mapping
import unicodedata

from .text import (
    ExtractionResult,
    SEARCH_FORMAT_VERSION,
    compact_snippet,
    extract_pdf_text,
    make_match_query,
    make_search_text,
    normalize_text,
    split_pdf_pages,
)


SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS pdf_docs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    path_key TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    title_norm TEXT NOT NULL,
    page_count INTEGER NOT NULL DEFAULT 0,
    extracted_page_count INTEGER NOT NULL DEFAULT 0,
    extraction_warning TEXT,
    file_size INTEGER NOT NULL DEFAULT 0,
    mtime_ns INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    extraction_method TEXT,
    indexed_at TEXT,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS pdf_pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL REFERENCES pdf_docs(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL,
    content TEXT NOT NULL,
    snippet TEXT NOT NULL,
    char_count INTEGER NOT NULL,
    UNIQUE(doc_id, page_number)
);

CREATE VIRTUAL TABLE IF NOT EXISTS pdf_page_fts USING fts5(
    page_id UNINDEXED,
    search_text,
    title_text,
    path_text,
    page_text,
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS library_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pdf_pages_doc_id ON pdf_pages(doc_id);
CREATE INDEX IF NOT EXISTS idx_pdf_pages_page_number ON pdf_pages(page_number);
"""


_DOC_MIGRATION_COLUMNS = {
    "file_size": "INTEGER NOT NULL DEFAULT 0",
    "mtime_ns": "INTEGER NOT NULL DEFAULT 0",
    "status": "TEXT NOT NULL DEFAULT 'indexed'",
    "extraction_method": "TEXT",
    "indexed_at": "TEXT",
    "error_message": "TEXT",
}
_STATUSES = ("pending", "indexed", "no_text", "error")
_ROOT_ID_PATTERN = re.compile(r"^KBROOT-[A-Z2-7]{26}$")
_FTS_REBUILD_BATCH_SIZE = 250


@dataclass(frozen=True, slots=True)
class ScannedPdf:
    file_path: Path
    relative_path: str
    size: int
    mtime_ns: int


@dataclass(frozen=True, slots=True)
class ScanResult:
    files: list[ScannedPdf]
    errors: list[str]
    success: bool


Extractor = Callable[[Path], ExtractionResult | str | tuple[Any, ...]]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _path_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value.replace("\\", "/")).casefold().strip("/")
    if normalized.endswith(".pdf"):
        normalized = normalized[:-4]
    return normalized


def _is_reparse_point(path: Path) -> bool:
    """Return whether *path* is a link, junction, or other reparse point.

    ``Path.is_symlink`` does not cover every Windows junction.  PDF inventory
    must never follow one because the configured library is the only permitted
    scan boundary.
    """

    try:
        info = path.lstat()
    except OSError:
        return False
    attributes = int(getattr(info, "st_file_attributes", 0) or 0)
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return path.is_symlink() or bool(attributes & reparse_flag)


def _title_norm(value: str) -> str:
    return _path_key(value)


def connect_library(db_path: Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA_SQL)
    present = {str(row[1]) for row in con.execute("PRAGMA table_info(pdf_docs)")}
    for name, declaration in _DOC_MIGRATION_COLUMNS.items():
        if name not in present:
            con.execute(f"ALTER TABLE pdf_docs ADD COLUMN {name} {declaration}")
    # ``status`` did not exist in the earliest compatible database.  Create
    # its index only after the additive column migration has completed.
    con.execute("CREATE INDEX IF NOT EXISTS idx_pdf_docs_status ON pdf_docs(status)")
    con.commit()
    return con


def _connect_read_only(db_path: Path) -> sqlite3.Connection:
    path = Path(db_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    try:
        con = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        con.execute("PRAGMA schema_version").fetchone()
    except sqlite3.OperationalError:
        # A read-only caller may be unable to create SQLite's shared-memory
        # sidecar even though the completed database itself is readable.
        try:
            con.close()
        except UnboundLocalError:
            pass
        con = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


def discover_pdf_files(library_root: Path) -> ScanResult:
    root = Path(library_root).resolve()
    if not root.is_dir():
        return ScanResult(files=[], errors=[f"Library root is not a directory: {root}"], success=False)

    files: list[ScannedPdf] = []
    errors: list[str] = []

    def record_walk_error(error: OSError) -> None:
        errors.append(f"scan: {error}")

    for current, dir_names, file_names in os.walk(
        root, topdown=True, onerror=record_walk_error, followlinks=False
    ):
        dir_names[:] = sorted(
            name
            for name in dir_names
            if not name.startswith(".")
            and not _is_reparse_point(Path(current) / name)
        )
        for file_name in sorted(file_names):
            if not file_name.casefold().endswith(".pdf"):
                continue
            file_path = Path(current) / file_name
            if _is_reparse_point(file_path):
                continue
            try:
                stat = file_path.stat()
                relative_path = file_path.relative_to(root).as_posix()
            except (OSError, ValueError) as exc:
                errors.append(f"stat {file_path}: {exc}")
                continue
            if not file_path.is_file():
                continue
            files.append(
                ScannedPdf(
                    file_path=file_path,
                    relative_path=relative_path,
                    size=int(stat.st_size),
                    mtime_ns=int(stat.st_mtime_ns),
                )
            )
    files.sort(key=lambda item: item.relative_path.casefold())
    return ScanResult(files=files, errors=errors, success=not errors)


def _set_meta(con: sqlite3.Connection, key: str, value: Any) -> None:
    encoded = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    con.execute(
        "INSERT INTO library_meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, encoded),
    )


def _require_current_search_format(con: sqlite3.Connection) -> None:
    current = _read_meta(con).get("search_format_version")
    if current != SEARCH_FORMAT_VERSION:
        raise RuntimeError(
            "PDF search index format is outdated "
            f"(found={current or 'unversioned'}, expected={SEARCH_FORMAT_VERSION}); "
            "run `index --resume` to rebuild FTS from the existing extracted page text"
        )


def _upgrade_search_format(con: sqlite3.Connection) -> dict[str, Any]:
    """Rebuild only derived FTS rows when the lexical contract changes."""

    previous = _read_meta(con).get("search_format_version")
    if previous == SEARCH_FORMAT_VERSION:
        return {
            "changed": False,
            "from_version": previous,
            "to_version": SEARCH_FORMAT_VERSION,
            "rebuilt_pages": 0,
            "preserves_extracted_text": True,
        }

    rebuilt_pages = 0
    last_page_id = 0
    with con:
        con.execute("DELETE FROM pdf_page_fts")
        while True:
            pages = con.execute(
                """
                SELECT p.id AS page_id, p.content, d.title, d.path
                FROM pdf_pages p
                JOIN pdf_docs d ON d.id = p.doc_id
                WHERE p.id > ?
                ORDER BY p.id
                LIMIT ?
                """,
                (last_page_id, _FTS_REBUILD_BATCH_SIZE),
            ).fetchall()
            if not pages:
                break
            rows: list[tuple[int, str, str, str, str]] = []
            for page in pages:
                title = str(page["title"])
                path = str(page["path"])
                content = str(page["content"])
                rows.append(
                    (
                        int(page["page_id"]),
                        make_search_text(title, path, content),
                        make_search_text(title),
                        make_search_text(path),
                        make_search_text(content),
                    )
                )
            con.executemany(
                "INSERT INTO pdf_page_fts(page_id, search_text, title_text, path_text, page_text) "
                "VALUES (?, ?, ?, ?, ?)",
                rows,
            )
            rebuilt_pages += len(rows)
            last_page_id = int(pages[-1]["page_id"])
        _set_meta(con, "search_format_version", SEARCH_FORMAT_VERSION)
        _set_meta(con, "search_format_upgraded_at", _utc_now())
    return {
        "changed": True,
        "from_version": previous,
        "to_version": SEARCH_FORMAT_VERSION,
        "rebuilt_pages": rebuilt_pages,
        "preserves_extracted_text": True,
    }


def _delete_fts_for_doc(con: sqlite3.Connection, doc_id: int) -> None:
    con.execute(
        "DELETE FROM pdf_page_fts WHERE page_id IN "
        "(SELECT id FROM pdf_pages WHERE doc_id = ?)",
        (doc_id,),
    )


def _clear_doc_pages(con: sqlite3.Connection, doc_id: int) -> None:
    _delete_fts_for_doc(con, doc_id)
    con.execute("DELETE FROM pdf_pages WHERE doc_id = ?", (doc_id,))


def _normalise_library_relative_path(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("library_relative_path must be a non-empty root-relative path")
    windows_path = PureWindowsPath(value)
    if windows_path.is_absolute() or windows_path.drive:
        raise ValueError("library_relative_path must be root-relative")
    parts = tuple(
        part for part in value.replace("\\", "/").split("/") if part not in ("", ".")
    )
    if not parts or any(part == ".." for part in parts):
        raise ValueError("library_relative_path must remain inside the knowledge root")
    return "/".join(parts)


def _configured_library_identity(
    knowledge_root_id: str | None,
    library_relative_path: str | None,
) -> tuple[str, str] | None:
    if knowledge_root_id is None and library_relative_path is None:
        return None
    if (
        not isinstance(knowledge_root_id, str)
        or _ROOT_ID_PATTERN.fullmatch(knowledge_root_id) is None
    ):
        raise ValueError(
            "knowledge_root_id must be KBROOT- followed by 26 Base32 characters, "
            "and library_relative_path must be supplied with it"
        )
    if library_relative_path is None:
        raise ValueError(
            "knowledge_root_id and library_relative_path must be supplied together"
        )
    return knowledge_root_id, _normalise_library_relative_path(library_relative_path)


def _relative_identity_key(value: str) -> str:
    return unicodedata.normalize("NFKC", value.replace("\\", "/")).casefold().strip("/")


def _identity_matches(
    indexed_root_id: str | None,
    indexed_relative_path: str | None,
    configured_identity: tuple[str, str] | None,
) -> bool | None:
    if configured_identity is None:
        return None
    if indexed_root_id is None or indexed_relative_path is None:
        return None
    root_id, relative_path = configured_identity
    return indexed_root_id == root_id and _relative_identity_key(
        indexed_relative_path
    ) == _relative_identity_key(relative_path)


def inventory_library(
    con: sqlite3.Connection,
    library_root: Path,
    *,
    knowledge_root_id: str | None = None,
    library_relative_path: str | None = None,
    scan: ScanResult | None = None,
) -> dict[str, Any]:
    root = Path(library_root).resolve()
    configured_identity = _configured_library_identity(
        knowledge_root_id, library_relative_path
    )
    scan_result = scan or discover_pdf_files(root)
    stats: dict[str, Any] = {
        "found": len(scan_result.files),
        "new": 0,
        "changed": 0,
        "unchanged": 0,
        "pruned": 0,
        "scan_success": scan_result.success,
        "scan_errors": list(scan_result.errors),
        "root_changed": False,
        "identity_matches": None,
        "index_reused_after_root_move": False,
    }
    seen_paths = {item.relative_path for item in scan_result.files}

    meta = _read_meta(con)
    previous_root = meta.get("library_root")
    previous_root_id = meta.get("knowledge_root_id")
    previous_relative_path = meta.get("library_relative_path")
    root_changed = bool(previous_root and not _same_path(previous_root, root))
    identity_matches = _identity_matches(
        previous_root_id, previous_relative_path, configured_identity
    )
    stats["root_changed"] = root_changed
    stats["identity_matches"] = identity_matches

    existing_docs = int(con.execute("SELECT COUNT(*) FROM pdf_docs").fetchone()[0])
    should_clear = False
    if configured_identity is None:
        should_clear = root_changed
    elif identity_matches is not None:
        should_clear = not identity_matches
        stats["index_reused_after_root_move"] = root_changed and identity_matches
    else:
        # A legacy database has no identity metadata.  It may be adopted only
        # when its recorded absolute root still matches; otherwise the corpus
        # identity is unknowable and the index must be rebuilt.
        absolute_matches = bool(previous_root and _same_path(previous_root, root))
        should_clear = existing_docs > 0 and not absolute_matches

    if should_clear:
        with con:
            con.execute("DELETE FROM pdf_page_fts")
            con.execute("DELETE FROM pdf_pages")
            con.execute("DELETE FROM pdf_docs")

    with con:
        for item in scan_result.files:
            row = con.execute(
                "SELECT id, file_size, mtime_ns FROM pdf_docs WHERE path = ?",
                (item.relative_path,),
            ).fetchone()
            if row is None:
                con.execute(
                    """
                    INSERT INTO pdf_docs(
                        path, path_key, title, title_norm, page_count,
                        extracted_page_count, extraction_warning, file_size,
                        mtime_ns, status, extraction_method, indexed_at, error_message
                    ) VALUES (?, ?, ?, ?, 0, 0, NULL, ?, ?, 'pending', NULL, NULL, NULL)
                    """,
                    (
                        item.relative_path,
                        _path_key(item.relative_path),
                        item.file_path.stem,
                        _title_norm(item.file_path.stem),
                        item.size,
                        item.mtime_ns,
                    ),
                )
                stats["new"] += 1
                continue

            if int(row["file_size"]) == item.size and int(row["mtime_ns"]) == item.mtime_ns:
                stats["unchanged"] += 1
                continue

            doc_id = int(row["id"])
            _clear_doc_pages(con, doc_id)
            con.execute(
                """
                UPDATE pdf_docs
                SET path_key = ?, title = ?, title_norm = ?, page_count = 0,
                    extracted_page_count = 0, extraction_warning = NULL,
                    file_size = ?, mtime_ns = ?, status = 'pending',
                    extraction_method = NULL, indexed_at = NULL, error_message = NULL
                WHERE id = ?
                """,
                (
                    _path_key(item.relative_path),
                    item.file_path.stem,
                    _title_norm(item.file_path.stem),
                    item.size,
                    item.mtime_ns,
                    doc_id,
                ),
            )
            stats["changed"] += 1

        if scan_result.success:
            existing = con.execute("SELECT id, path FROM pdf_docs").fetchall()
            for row in existing:
                if str(row["path"]) in seen_paths:
                    continue
                doc_id = int(row["id"])
                _clear_doc_pages(con, doc_id)
                con.execute("DELETE FROM pdf_docs WHERE id = ?", (doc_id,))
                stats["pruned"] += 1

        now = _utc_now()
        _set_meta(con, "library_root", str(root))
        if configured_identity is not None:
            root_id, relative_path = configured_identity
            _set_meta(con, "knowledge_root_id", root_id)
            _set_meta(con, "library_relative_path", relative_path)
        _set_meta(con, "last_scan_at", now)
        _set_meta(con, "last_scan_success", scan_result.success)
        _set_meta(con, "last_scan_errors", scan_result.errors)
        if scan_result.success:
            _set_meta(con, "last_successful_scan_at", now)
    return stats


def _normalise_root_relative_path(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("resource path must be a non-empty root-relative path")
    windows_path = PureWindowsPath(value)
    if windows_path.is_absolute() or windows_path.drive:
        raise ValueError("resource path must be root-relative")
    parts = tuple(
        part for part in value.replace("\\", "/").split("/") if part not in ("", ".")
    )
    if not parts or any(part == ".." for part in parts):
        raise ValueError("resource path must remain inside the knowledge root")
    return "/".join(parts)


def _library_local_pdf_path(
    root_relative_path: str,
    library_relative_path: str,
) -> str | None:
    resource = _normalise_root_relative_path(root_relative_path)
    library = _normalise_library_relative_path(library_relative_path)
    resource_parts = resource.split("/")
    library_parts = library.split("/")
    if len(resource_parts) <= len(library_parts):
        return None
    if [part.casefold() for part in resource_parts[: len(library_parts)]] != [
        part.casefold() for part in library_parts
    ]:
        return None
    local = "/".join(resource_parts[len(library_parts) :])
    if PureWindowsPath(local).suffix.casefold() != ".pdf":
        return None
    return local


def _require_reusable_index_identity(
    con: sqlite3.Connection,
    library_root: Path,
    configured_identity: tuple[str, str],
) -> dict[str, Any]:
    meta = _read_meta(con)
    indexed_root = meta.get("library_root")
    indexed_root_id = meta.get("knowledge_root_id")
    indexed_relative_path = meta.get("library_relative_path")
    identity_matches = _identity_matches(
        indexed_root_id,
        indexed_relative_path,
        configured_identity,
    )
    docs = int(con.execute("SELECT COUNT(*) FROM pdf_docs").fetchone()[0])
    if identity_matches is False:
        raise ValueError(
            "PDF index identity does not match the configured knowledge root and library"
        )
    if identity_matches is None and docs:
        # A legacy index can be adopted only while it still points at this
        # exact library.  Once the absolute root moved there is no safe way to
        # prove that the legacy corpus is the same one.
        if not indexed_root or not _same_path(indexed_root, library_root):
            raise ValueError(
                "Legacy PDF index cannot be rebound after a root move because it has no "
                "knowledge_root_id; run a deliberate PDF index rebuild"
            )
    return {
        "docs": docs,
        "indexed_root": indexed_root,
        "indexed_root_id": indexed_root_id,
        "indexed_relative_path": indexed_relative_path,
        "identity_matches": identity_matches,
    }


def indexed_pdf_priority_paths(
    db_path: Path,
    library_root: Path,
    *,
    knowledge_root_id: str,
    library_relative_path: str,
) -> list[str]:
    """Return root-relative PDFs already inventoried by the local index.

    The result is used only to order the initial registry scan.  It does not
    create resources or trust a mismatched/stale index identity.
    """

    path = Path(db_path)
    if not path.is_file():
        return []
    identity = _configured_library_identity(
        knowledge_root_id, library_relative_path
    )
    assert identity is not None
    con = _connect_read_only(path)
    try:
        _require_reusable_index_identity(con, Path(library_root).resolve(), identity)
        prefix = _normalise_library_relative_path(library_relative_path)
        return [
            f"{prefix}/{str(row['path']).replace(chr(92), '/')}"
            for row in con.execute("SELECT path FROM pdf_docs ORDER BY path COLLATE NOCASE")
        ]
    finally:
        con.close()


def plan_indexed_pdf_move(
    db_path: Path,
    library_root: Path,
    old_root_relative_path: str,
    new_root_relative_path: str,
    *,
    knowledge_root_id: str,
    library_relative_path: str,
) -> dict[str, Any]:
    """Describe a targeted PDF-index update without modifying the index."""

    old_local = _library_local_pdf_path(
        old_root_relative_path, library_relative_path
    )
    new_local = _library_local_pdf_path(
        new_root_relative_path, library_relative_path
    )
    database = Path(db_path)
    base: dict[str, Any] = {
        "database_exists": database.is_file(),
        "old_library_path": old_local,
        "new_library_path": new_local,
        "changes_required": False,
        "preserves_extracted_text": False,
    }
    if not database.is_file():
        return {**base, "action": "not_initialized"}
    if old_local is None and new_local is None:
        return {**base, "action": "not_applicable"}

    identity = _configured_library_identity(
        knowledge_root_id, library_relative_path
    )
    assert identity is not None
    con = _connect_read_only(database)
    try:
        identity_state = _require_reusable_index_identity(
            con, Path(library_root).resolve(), identity
        )
        old_row = (
            con.execute("SELECT * FROM pdf_docs WHERE path = ?", (old_local,)).fetchone()
            if old_local is not None
            else None
        )
        new_row = (
            con.execute(
                "SELECT * FROM pdf_docs WHERE path = ? OR path_key = ?",
                (new_local, _path_key(new_local)),
            ).fetchone()
            if new_local is not None
            else None
        )
        if new_row is not None and (
            old_row is None or int(new_row["id"]) != int(old_row["id"])
        ):
            raise ValueError(
                f"PDF index destination is already occupied: {new_local}"
            )

        if old_local == new_local:
            action = "already_current"
        elif old_row is not None and new_local is not None:
            action = "rename_preserve_text"
        elif old_row is not None:
            action = "remove_from_index"
        elif new_local is not None:
            action = "add_pending"
        else:
            action = "not_applicable"
        changes_required = action in {
            "rename_preserve_text",
            "remove_from_index",
            "add_pending",
        }
        return {
            **base,
            "action": action,
            "changes_required": changes_required,
            "preserves_extracted_text": action == "rename_preserve_text",
            "indexed_before": old_row is not None,
            "indexed_status_before": str(old_row["status"]) if old_row else None,
            "indexed_page_count_before": int(old_row["page_count"]) if old_row else 0,
            "identity_matches_before": identity_state["identity_matches"],
        }
    finally:
        con.close()


def _rebuild_fts_metadata_for_doc(
    con: sqlite3.Connection,
    doc_id: int,
    title: str,
    path: str,
) -> None:
    pages = con.execute(
        "SELECT id, content FROM pdf_pages WHERE doc_id = ? ORDER BY page_number",
        (doc_id,),
    ).fetchall()
    _delete_fts_for_doc(con, doc_id)
    for page in pages:
        content = str(page["content"])
        con.execute(
            "INSERT INTO pdf_page_fts(page_id, search_text, title_text, path_text, page_text) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                int(page["id"]),
                make_search_text(title, path, content),
                make_search_text(title),
                make_search_text(path),
                make_search_text(content),
            ),
        )


def apply_indexed_pdf_move(
    db_path: Path,
    library_root: Path,
    old_root_relative_path: str,
    new_root_relative_path: str,
    *,
    knowledge_root_id: str,
    library_relative_path: str,
    expected_plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply one targeted metadata/FTS path update without extracting PDF text."""

    current = plan_indexed_pdf_move(
        db_path,
        library_root,
        old_root_relative_path,
        new_root_relative_path,
        knowledge_root_id=knowledge_root_id,
        library_relative_path=library_relative_path,
    )
    if expected_plan is not None:
        for key in (
            "database_exists",
            "old_library_path",
            "new_library_path",
            "action",
            "indexed_before",
            "indexed_status_before",
            "indexed_page_count_before",
        ):
            if current.get(key) != expected_plan.get(key):
                raise RuntimeError(
                    f"PDF index move plan changed before write ({key})"
                )
    if not current["changes_required"]:
        return {**current, "applied": True, "changed": False}

    database = Path(db_path)
    root = Path(library_root).resolve()
    identity = _configured_library_identity(
        knowledge_root_id, library_relative_path
    )
    assert identity is not None
    con = connect_library(database)
    try:
        _require_reusable_index_identity(con, root, identity)
        action = str(current["action"])
        old_local = current["old_library_path"]
        new_local = current["new_library_path"]
        with con:
            if action == "rename_preserve_text":
                row = con.execute(
                    "SELECT * FROM pdf_docs WHERE path = ?", (old_local,)
                ).fetchone()
                if row is None:
                    raise RuntimeError("Indexed PDF disappeared before path update")
                target = root.joinpath(*str(new_local).split("/"))
                info = target.stat()
                if not target.is_file() or _is_reparse_point(target):
                    raise RuntimeError(f"Moved PDF is missing or unsafe: {target}")
                title = target.stem
                doc_id = int(row["id"])
                con.execute(
                    "UPDATE pdf_docs SET path = ?, path_key = ?, title = ?, title_norm = ?, "
                    "file_size = ?, mtime_ns = ? WHERE id = ?",
                    (
                        new_local,
                        _path_key(str(new_local)),
                        title,
                        _title_norm(title),
                        int(info.st_size),
                        int(info.st_mtime_ns),
                        doc_id,
                    ),
                )
                _rebuild_fts_metadata_for_doc(
                    con, doc_id, title, str(new_local)
                )
            elif action == "remove_from_index":
                row = con.execute(
                    "SELECT id FROM pdf_docs WHERE path = ?", (old_local,)
                ).fetchone()
                if row is None:
                    raise RuntimeError("Indexed PDF disappeared before removal")
                doc_id = int(row["id"])
                _clear_doc_pages(con, doc_id)
                con.execute("DELETE FROM pdf_docs WHERE id = ?", (doc_id,))
            elif action == "add_pending":
                target = root.joinpath(*str(new_local).split("/"))
                info = target.stat()
                if not target.is_file() or _is_reparse_point(target):
                    raise RuntimeError(f"Moved PDF is missing or unsafe: {target}")
                title = target.stem
                con.execute(
                    "INSERT INTO pdf_docs(path, path_key, title, title_norm, page_count, "
                    "extracted_page_count, extraction_warning, file_size, mtime_ns, status, "
                    "extraction_method, indexed_at, error_message) "
                    "VALUES (?, ?, ?, ?, 0, 0, NULL, ?, ?, 'pending', NULL, NULL, NULL)",
                    (
                        new_local,
                        _path_key(str(new_local)),
                        title,
                        _title_norm(title),
                        int(info.st_size),
                        int(info.st_mtime_ns),
                    ),
                )
            else:
                raise RuntimeError(f"Unsupported PDF index move action: {action}")
            _set_meta(con, "library_root", str(root))
            _set_meta(con, "knowledge_root_id", identity[0])
            _set_meta(con, "library_relative_path", identity[1])
            _set_meta(con, "last_targeted_update_at", _utc_now())
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        con.execute("PRAGMA journal_mode=DELETE").fetchone()
        return {**current, "applied": True, "changed": True}
    finally:
        con.close()


def _inventory_digest(scan: ScanResult) -> str:
    payload = [
        [item.relative_path, int(item.size), int(item.mtime_ns)]
        for item in scan.files
    ]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _preview_library_inventory_refresh(
    db_path: Path,
    library_root: Path,
    *,
    knowledge_root_id: str,
    library_relative_path: str,
    scan: ScanResult | None = None,
) -> tuple[dict[str, Any], ScanResult | None]:
    database = Path(db_path)
    root = Path(library_root).resolve()
    if not database.is_file():
        return (
            {
                "database_exists": False,
                "action": "not_initialized",
                "changes_required": False,
                "inventory_digest": None,
            },
            None,
        )
    active_scan = scan or discover_pdf_files(root)
    if not active_scan.success:
        raise RuntimeError(
            "PDF inventory scan is incomplete: " + "; ".join(active_scan.errors)
        )
    identity = _configured_library_identity(
        knowledge_root_id, library_relative_path
    )
    assert identity is not None
    con = _connect_read_only(database)
    try:
        identity_state = _require_reusable_index_identity(con, root, identity)
        existing = {
            str(row["path"]): row
            for row in con.execute(
                "SELECT path, file_size, mtime_ns, status FROM pdf_docs"
            )
        }
        scanned = {item.relative_path: item for item in active_scan.files}
        new_count = sum(path not in existing for path in scanned)
        changed_count = sum(
            path in existing
            and (
                int(existing[path]["file_size"]) != int(item.size)
                or int(existing[path]["mtime_ns"]) != int(item.mtime_ns)
            )
            for path, item in scanned.items()
        )
        unchanged_count = len(scanned) - new_count - changed_count
        pruned_count = sum(path not in scanned for path in existing)
        root_changed = bool(
            identity_state["indexed_root"]
            and not _same_path(str(identity_state["indexed_root"]), root)
        )
        identity_adoption = identity_state["identity_matches"] is None
        changes_required = bool(
            root_changed
            or identity_adoption
            or new_count
            or changed_count
            or pruned_count
        )
        return (
            {
                "database_exists": True,
                "action": "refresh_metadata_only",
                "changes_required": changes_required,
                "inventory_digest": _inventory_digest(active_scan),
                "found": len(active_scan.files),
                "new": new_count,
                "changed": changed_count,
                "unchanged": unchanged_count,
                "pruned": pruned_count,
                "root_changed": root_changed,
                "identity_matches_before": identity_state["identity_matches"],
                "preserves_unchanged_extracted_text": True,
                "extracts_pdf_text": False,
            },
            active_scan,
        )
    finally:
        con.close()


def preview_library_inventory_refresh(
    db_path: Path,
    library_root: Path,
    *,
    knowledge_root_id: str,
    library_relative_path: str,
) -> dict[str, Any]:
    preview, _ = _preview_library_inventory_refresh(
        db_path,
        library_root,
        knowledge_root_id=knowledge_root_id,
        library_relative_path=library_relative_path,
    )
    return preview


def refresh_library_inventory(
    db_path: Path,
    library_root: Path,
    *,
    knowledge_root_id: str,
    library_relative_path: str,
    expected_preview: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Refresh PDF inventory metadata without invoking a text extractor."""

    preview, scan = _preview_library_inventory_refresh(
        db_path,
        library_root,
        knowledge_root_id=knowledge_root_id,
        library_relative_path=library_relative_path,
    )
    if expected_preview is not None:
        for key in (
            "database_exists",
            "action",
            "inventory_digest",
            "found",
            "new",
            "changed",
            "unchanged",
            "pruned",
            "root_changed",
            "identity_matches_before",
        ):
            if preview.get(key) != expected_preview.get(key):
                raise RuntimeError(
                    f"PDF inventory changed after dry-run ({key})"
                )
    if not preview["database_exists"]:
        return {**preview, "applied": True, "changed": False}
    if not preview["changes_required"]:
        return {**preview, "applied": True, "changed": False}
    assert scan is not None
    database = Path(db_path)
    con = connect_library(database)
    try:
        stats = inventory_library(
            con,
            Path(library_root),
            knowledge_root_id=knowledge_root_id,
            library_relative_path=library_relative_path,
            scan=scan,
        )
        verify = discover_pdf_files(Path(library_root))
        if not verify.success or _inventory_digest(verify) != preview["inventory_digest"]:
            raise RuntimeError("PDF inventory changed while metadata was being refreshed")
        status = _status_from_con(
            con,
            Path(library_root),
            knowledge_root_id=knowledge_root_id,
            library_relative_path=library_relative_path,
        )
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        con.execute("PRAGMA journal_mode=DELETE").fetchone()
        return {
            **preview,
            "applied": True,
            "changed": True,
            "inventory": stats,
            "status": status,
        }
    finally:
        con.close()


def _coerce_extraction(value: ExtractionResult | str | tuple[Any, ...]) -> ExtractionResult:
    if isinstance(value, ExtractionResult):
        return value
    if isinstance(value, str):
        return ExtractionResult(text=value, method="injected")
    if isinstance(value, tuple):
        if len(value) == 2:
            text, warning = value
            return ExtractionResult(text=str(text), method="injected", warning=warning)
        if len(value) == 3:
            text, method, warning = value
            return ExtractionResult(text=str(text), method=str(method), warning=warning)
    raise TypeError("Extractor must return ExtractionResult, str, (text, warning), or (text, method, warning)")


def _store_extraction(
    con: sqlite3.Connection,
    doc: sqlite3.Row,
    result: ExtractionResult,
) -> str:
    doc_id = int(doc["id"])
    raw_pages = split_pdf_pages(result.text)
    page_rows = [
        (number, raw_page.strip())
        for number, raw_page in enumerate(raw_pages, start=1)
        if raw_page.strip()
    ]
    status = "indexed" if page_rows else "no_text"
    warning = result.warning
    if status == "no_text" and not warning:
        warning = "No extractable PDF text layer; OCR may be required"

    with con:
        _clear_doc_pages(con, doc_id)
        for page_number, content in page_rows:
            cursor = con.execute(
                """
                INSERT INTO pdf_pages(doc_id, page_number, content, snippet, char_count)
                VALUES (?, ?, ?, ?, ?)
                """,
                (doc_id, page_number, content, compact_snippet(content), len(content)),
            )
            page_id = int(cursor.lastrowid)
            con.execute(
                """
                INSERT INTO pdf_page_fts(page_id, search_text, title_text, path_text, page_text)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    page_id,
                    make_search_text(str(doc["title"]), str(doc["path"]), content),
                    make_search_text(str(doc["title"])),
                    make_search_text(str(doc["path"])),
                    make_search_text(content),
                ),
            )
        con.execute(
            """
            UPDATE pdf_docs
            SET page_count = ?, extracted_page_count = ?, extraction_warning = ?,
                status = ?, extraction_method = ?, indexed_at = ?, error_message = NULL
            WHERE id = ?
            """,
            (
                len(raw_pages),
                len(page_rows),
                warning,
                status,
                result.method,
                _utc_now(),
                doc_id,
            ),
        )
    return status


def _store_error(con: sqlite3.Connection, doc_id: int, error: BaseException) -> None:
    message = compact_snippet(f"{type(error).__name__}: {error}", limit=700)
    with con:
        # Keep the compatibility tables truthful for consumers such as
        # pdf-paper-search, which do not know about our extended status field.
        _clear_doc_pages(con, doc_id)
        con.execute(
            """
            UPDATE pdf_docs
            SET status = 'error', extraction_warning = ?, error_message = ?, indexed_at = ?
            WHERE id = ?
            """,
            (message, message, _utc_now(), doc_id),
        )


def index_library(
    library_root: Path,
    db_path: Path,
    *,
    knowledge_root_id: str | None = None,
    library_relative_path: str | None = None,
    extractor: Extractor | None = None,
    pdftotext_exe: str | Path | None = None,
    temp_root: Path | None = None,
    resume: bool = True,
    retry_errors: bool = False,
    max_files: int | None = None,
    batch_size: int = 25,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    if max_files is not None and max_files < 0:
        raise ValueError("max_files must be nonnegative")
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")

    root = Path(library_root).resolve()
    con = connect_library(Path(db_path))
    try:
        inventory = inventory_library(
            con,
            root,
            knowledge_root_id=knowledge_root_id,
            library_relative_path=library_relative_path,
        )
        search_format_upgrade = _upgrade_search_format(con)
        if not resume:
            with con:
                con.execute("UPDATE pdf_docs SET status = 'pending'")

        candidate_statuses = ("pending", "error") if retry_errors else ("pending",)
        placeholders = ",".join("?" for _ in candidate_statuses)
        candidates = con.execute(
            f"SELECT * FROM pdf_docs WHERE status IN ({placeholders}) "
            "ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END, path COLLATE NOCASE",
            candidate_statuses,
        ).fetchall()
        if max_files is not None:
            candidates = candidates[:max_files]

        active_extractor: Extractor
        if extractor is None:
            active_extractor = lambda path: extract_pdf_text(
                path,
                pdftotext_exe=pdftotext_exe,
                temp_root=temp_root,
            )
        else:
            active_extractor = extractor

        stats: dict[str, Any] = {
            "inventory": inventory,
            "search_format_upgrade": search_format_upgrade,
            "selected": len(candidates),
            "processed": 0,
            "indexed": 0,
            "no_text": 0,
            "errors": 0,
            "progress_events": 0,
        }
        for doc in candidates:
            source = root / Path(str(doc["path"]))
            try:
                result = _coerce_extraction(active_extractor(source))
                outcome = _store_extraction(con, doc, result)
                stats[outcome] += 1
            except Exception as exc:  # one failed book must not stop the shelf
                _store_error(con, int(doc["id"]), exc)
                stats["errors"] += 1
            stats["processed"] += 1

            at_boundary = stats["processed"] % batch_size == 0
            at_end = stats["processed"] == len(candidates)
            if at_boundary or at_end:
                con.execute("PRAGMA wal_checkpoint(PASSIVE)")
                stats["progress_events"] += 1
                if progress is not None:
                    progress(dict(stats))

        final_status = _status_from_con(
            con,
            root,
            knowledge_root_id=knowledge_root_id,
            library_relative_path=library_relative_path,
        )
        stats["remaining"] = int(final_status["pending_docs"])
        stats["status"] = final_status
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        journal_row = con.execute("PRAGMA journal_mode=DELETE").fetchone()
        stats["journal_mode"] = str(journal_row[0]).casefold() if journal_row else None
        return stats
    finally:
        con.close()


def _read_meta(con: sqlite3.Connection) -> dict[str, str]:
    try:
        return {str(row["key"]): str(row["value"]) for row in con.execute("SELECT key, value FROM library_meta")}
    except sqlite3.OperationalError:
        return {}


def _same_path(left: str | Path, right: str | Path) -> bool:
    left_text = os.path.normcase(str(Path(left).expanduser().resolve(strict=False)))
    right_text = os.path.normcase(str(Path(right).expanduser().resolve(strict=False)))
    return left_text == right_text


def _status_from_con(
    con: sqlite3.Connection,
    library_root: Path | None = None,
    *,
    knowledge_root_id: str | None = None,
    library_relative_path: str | None = None,
) -> dict[str, Any]:
    status_counts = {status: 0 for status in _STATUSES}
    for row in con.execute("SELECT status, COUNT(*) AS count FROM pdf_docs GROUP BY status"):
        status_counts[str(row["status"])] = int(row["count"])
    aggregate = con.execute(
        """
        SELECT COUNT(*) AS docs,
               COALESCE(SUM(page_count), 0) AS pages,
               COALESCE(SUM(extracted_page_count), 0) AS pages_with_text
        FROM pdf_docs
        """
    ).fetchone()
    meta = _read_meta(con)
    indexed_root = meta.get("library_root")
    indexed_root_id = meta.get("knowledge_root_id")
    indexed_relative_path = meta.get("library_relative_path")
    configured_root = str(Path(library_root).resolve()) if library_root is not None else None
    root_path_matches_config = (
        _same_path(indexed_root, configured_root)
        if indexed_root is not None and configured_root is not None
        else None
    )
    configured_identity = _configured_library_identity(
        knowledge_root_id, library_relative_path
    )
    identity_matches_config = _identity_matches(
        indexed_root_id, indexed_relative_path, configured_identity
    )
    root_matches_config = (
        identity_matches_config
        if identity_matches_config is not None
        else root_path_matches_config
    )
    docs = int(aggregate["docs"])
    searchable = status_counts.get("indexed", 0)
    processed = searchable + status_counts.get("no_text", 0) + status_counts.get("error", 0)
    return {
        "database_exists": True,
        "root": indexed_root,
        "root_exists": Path(indexed_root).is_dir() if indexed_root else None,
        "configured_root": configured_root,
        "configured_root_exists": Path(configured_root).is_dir() if configured_root else None,
        "root_matches_config": root_matches_config,
        "root_path_matches_config": root_path_matches_config,
        "knowledge_root_id": indexed_root_id,
        "library_relative_path": indexed_relative_path,
        "configured_knowledge_root_id": (
            configured_identity[0] if configured_identity is not None else None
        ),
        "configured_library_relative_path": (
            configured_identity[1] if configured_identity is not None else None
        ),
        "identity_matches_config": identity_matches_config,
        "docs": docs,
        "pages": int(aggregate["pages"]),
        "pages_with_text": int(aggregate["pages_with_text"]),
        "status_counts": status_counts,
        "searchable_docs": searchable,
        "processed_docs": processed,
        "pending_docs": status_counts.get("pending", 0),
        "coverage_percent": round((searchable / docs * 100.0) if docs else 0.0, 2),
        "search_format_version": meta.get("search_format_version"),
        "expected_search_format_version": SEARCH_FORMAT_VERSION,
        "search_format_current": meta.get("search_format_version") == SEARCH_FORMAT_VERSION,
        "search_rebuild_required": meta.get("search_format_version") != SEARCH_FORMAT_VERSION,
        "last_scan_at": meta.get("last_scan_at"),
        "last_scan_success": _decode_meta_bool(meta.get("last_scan_success")),
        "last_successful_scan_at": meta.get("last_successful_scan_at"),
        "last_scan_errors": _decode_meta_json(meta.get("last_scan_errors"), []),
    }


def _decode_meta_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return value.casefold() == "true"
    return bool(decoded)


def _decode_meta_json(value: str | None, default: Any) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def library_status(
    db_path: Path,
    library_root: Path | None = None,
    *,
    knowledge_root_id: str | None = None,
    library_relative_path: str | None = None,
) -> dict[str, Any]:
    configured_identity = _configured_library_identity(
        knowledge_root_id, library_relative_path
    )
    path = Path(db_path)
    if not path.is_file():
        configured_root = str(Path(library_root).resolve()) if library_root is not None else None
        return {
            "database_exists": False,
            "root": None,
            "root_exists": None,
            "configured_root": configured_root,
            "configured_root_exists": Path(configured_root).is_dir() if configured_root else None,
            "root_matches_config": None,
            "root_path_matches_config": None,
            "knowledge_root_id": None,
            "library_relative_path": None,
            "configured_knowledge_root_id": (
                configured_identity[0] if configured_identity is not None else None
            ),
            "configured_library_relative_path": (
                configured_identity[1] if configured_identity is not None else None
            ),
            "identity_matches_config": None,
            "docs": 0,
            "pages": 0,
            "pages_with_text": 0,
            "status_counts": {status: 0 for status in _STATUSES},
            "searchable_docs": 0,
            "processed_docs": 0,
            "pending_docs": 0,
            "coverage_percent": 0.0,
            "search_format_version": None,
            "expected_search_format_version": SEARCH_FORMAT_VERSION,
            "search_format_current": False,
            "search_rebuild_required": True,
            "last_scan_at": None,
            "last_scan_success": None,
            "last_successful_scan_at": None,
            "last_scan_errors": [],
        }
    con = _connect_read_only(path)
    try:
        return _status_from_con(
            con,
            library_root,
            knowledge_root_id=knowledge_root_id,
            library_relative_path=library_relative_path,
        )
    finally:
        con.close()


def _dedupe_aliases(query: str, aliases: Sequence[str] | None) -> list[str]:
    values = [query, *(aliases or [])]
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        stripped = str(value).strip()
        key = normalize_text(stripped)
        if not stripped or key in seen:
            continue
        seen.add(key)
        result.append(stripped)
    return result


def _matched_snippet(content: str, aliases: Sequence[str], limit: int = 360) -> str:
    compact = " ".join(content.split())
    folded = compact.casefold()
    match_start = -1
    match_length = 0
    for alias in aliases:
        needle = " ".join(str(alias).split()).casefold()
        if not needle:
            continue
        position = folded.find(needle)
        if position >= 0:
            match_start = position
            match_length = len(needle)
            break
    if match_start < 0:
        for alias in aliases:
            for token in str(alias).split():
                position = folded.find(token.casefold())
                if position >= 0:
                    match_start = position
                    match_length = len(token)
                    break
            if match_start >= 0:
                break
    if match_start < 0 or len(compact) <= limit:
        return compact_snippet(compact, limit=limit)
    left_context = max(80, (limit - match_length) // 2)
    start = max(0, match_start - left_context)
    end = min(len(compact), start + limit)
    if end - start < limit:
        start = max(0, end - limit)
    excerpt = compact[start:end].strip()
    if start > 0:
        excerpt = "…" + excerpt
    if end < len(compact):
        excerpt += "…"
    return excerpt


def search_library(
    db_path: Path,
    query: str,
    *,
    aliases: Sequence[str] | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    if not query.strip():
        raise ValueError("query must not be empty")
    if limit < 1:
        raise ValueError("limit must be at least 1")
    variants = _dedupe_aliases(query, aliases)
    con = _connect_read_only(Path(db_path))
    try:
        _require_current_search_format(con)
        coverage = _status_from_con(con)
        accumulated: dict[int, dict[str, Any]] = {}
        matched_aliases: list[str] = []
        unmatched_aliases: list[str] = []
        fetch_limit = max(80, limit * 20)

        for alias in variants:
            try:
                match_query = make_match_query(alias)
            except ValueError:
                unmatched_aliases.append(alias)
                continue
            rows = con.execute(
                """
                SELECT p.id AS page_id, p.page_number, p.snippet, p.content,
                       d.title, d.title_norm, d.path, d.path_key, d.status,
                       d.extraction_method, d.extraction_warning,
                       bm25(pdf_page_fts, 3.0, 2.5, 0.5, 1.0) AS bm25_score
                FROM pdf_page_fts
                JOIN pdf_pages p ON p.id = CAST(pdf_page_fts.page_id AS INTEGER)
                JOIN pdf_docs d ON d.id = p.doc_id
                WHERE pdf_page_fts MATCH ? AND d.status = 'indexed'
                ORDER BY bm25_score
                LIMIT ?
                """,
                (match_query, fetch_limit),
            ).fetchall()
            if not rows:
                unmatched_aliases.append(alias)
                continue
            matched_aliases.append(alias)
            alias_norm = _path_key(alias)
            for row in rows:
                page_id = int(row["page_id"])
                raw_bm25 = float(row["bm25_score"])
                entry = accumulated.setdefault(
                    page_id,
                    {
                        "title": str(row["title"]),
                        "path": str(row["path"]),
                        "page_number": int(row["page_number"]),
                        "snippet": str(row["snippet"]),
                        "_content": str(row["content"]),
                        "score": 0.0,
                        "reasons": [],
                        "matched_aliases": [],
                        "status": str(row["status"]),
                        "method": row["extraction_method"],
                        "warning": row["extraction_warning"],
                    },
                )
                entry["score"] += max(0.0, -raw_bm25) + 1.0
                entry["matched_aliases"].append(alias)
                entry["reasons"].append(f"full-text match: {alias}")

                title_norm = str(row["title_norm"])
                path_norm = str(row["path_key"])
                if alias_norm == title_norm:
                    entry["score"] += 40.0
                    entry["reasons"].append("exact title match")
                elif alias_norm and alias_norm in title_norm:
                    entry["score"] += 20.0
                    entry["reasons"].append("title contains query")
                if alias_norm and alias_norm in path_norm:
                    entry["score"] += 8.0
                    entry["reasons"].append("path contains query")
                content_norm = normalize_text(str(row["content"]))
                literal_alias = normalize_text(alias).strip()
                if literal_alias and literal_alias in content_norm:
                    entry["score"] += 25.0
                    entry["reasons"].append("page contains query text")

        results = sorted(
            accumulated.values(),
            key=lambda item: (-float(item["score"]), str(item["path"]).casefold(), int(item["page_number"])),
        )[:limit]
        for item in results:
            item["snippet"] = _matched_snippet(
                str(item.pop("_content")),
                [str(value) for value in item["matched_aliases"]],
            )
            item["score"] = round(float(item["score"]), 6)
            item["reasons"] = list(dict.fromkeys(item["reasons"]))
            item["matched_aliases"] = list(dict.fromkeys(item["matched_aliases"]))

        counts = coverage["status_counts"]
        incomplete = any(int(counts.get(status, 0)) for status in ("pending", "no_text", "error"))
        warning = None
        if incomplete:
            warning = (
                "Search coverage is incomplete: pending, extraction-error, or no-text PDFs "
                "cannot support a conclusive library-wide miss"
            )
        return {
            "query": query,
            "aliases": variants,
            "results": results,
            "diagnostics": {
                "coverage": coverage,
                "matched_aliases": matched_aliases,
                "unmatched_aliases": unmatched_aliases,
                "incomplete": incomplete,
                "warning": warning,
            },
        }
    finally:
        con.close()


def get_page(db_path: Path, path: str, page_number: int) -> dict[str, Any]:
    if page_number < 1:
        raise ValueError("page_number must be at least 1")
    spec = str(path).replace("\\", "/")
    norm = _path_key(spec)
    con = _connect_read_only(Path(db_path))
    try:
        docs = con.execute(
            """
            SELECT * FROM pdf_docs
            WHERE path = ? OR path_key = ? OR title_norm = ?
            ORDER BY CASE WHEN path = ? THEN 0 WHEN path_key = ? THEN 1 ELSE 2 END
            """,
            (spec, norm, norm, spec, norm),
        ).fetchall()
        if not docs:
            raise LookupError(f"PDF was not found in the library index: {path}")
        if len(docs) > 1:
            candidates = ", ".join(str(row["path"]) for row in docs[:5])
            raise LookupError(f"Ambiguous PDF specification: {path} -> {candidates}")
        doc = docs[0]
        page = con.execute(
            "SELECT page_number, content, snippet, char_count FROM pdf_pages "
            "WHERE doc_id = ? AND page_number = ?",
            (int(doc["id"]), page_number),
        ).fetchone()
        if page is None:
            raise LookupError(
                f"PDF page is unavailable in the text index: {doc['path']} page {page_number} "
                f"(status={doc['status']})"
            )
        return {
            "title": str(doc["title"]),
            "path": str(doc["path"]),
            "page_number": int(page["page_number"]),
            "content": str(page["content"]),
            "snippet": str(page["snippet"]),
            "char_count": int(page["char_count"]),
            "status": str(doc["status"]),
            "method": doc["extraction_method"],
            "warning": doc["extraction_warning"],
        }
    finally:
        con.close()
