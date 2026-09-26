from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
from typing import Any, Iterable

from .text import compact_snippet, make_match_query, make_search_text
from .questions import QUESTION_SUBJECT, assert_question_database_root


COLLECTION_SCHEMA_VERSION = "mpk-question-collections/v1"
IMPORT_PLAN_SCHEMA_VERSION = "mpk-question-import-plan/v1"
DEFAULT_DISCOVERY_RELATIVE = "数据/题库"
_QUESTION_HEADING = re.compile(r"^###\s+(\d+(?:[.．]\d+)*)\s*$")
_SECTION_HEADING = re.compile(r"^##\s+(.+?)\s*$")
_TITLE_HEADING = re.compile(r"^#\s+(.+?)\s*$")
_IMAGE_REF = re.compile(r"!\[[^\]]*\]\(([^)]+)\)|!\[\[([^\]]+)\]\]")
_SOURCE_PAGE = re.compile(r"<!--\s*source-page:\s*([^>]+?)\s*-->")
_YEAR = re.compile(r"(?<!\d)(20\d{2})(?!\d)")


SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS question_collection_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS question_collections (
    collection_id TEXT PRIMARY KEY,
    source_scope TEXT NOT NULL,
    source_relative TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    source_format TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    verification_status TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    document_count INTEGER NOT NULL,
    question_count INTEGER NOT NULL,
    imported_at TEXT NOT NULL,
    scan_complete INTEGER NOT NULL,
    scan_errors_json TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS collection_docs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collection_id TEXT NOT NULL REFERENCES question_collections(collection_id) ON DELETE CASCADE,
    relative_path TEXT NOT NULL,
    title TEXT NOT NULL,
    year TEXT,
    paper TEXT,
    subject TEXT,
    file_size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    question_count INTEGER NOT NULL,
    indexed_at TEXT NOT NULL,
    UNIQUE(collection_id, relative_path)
);
CREATE TABLE IF NOT EXISTS collection_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL REFERENCES collection_docs(id) ON DELETE CASCADE,
    stable_key TEXT NOT NULL UNIQUE,
    question_number TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    section TEXT,
    content_md TEXT NOT NULL,
    image_refs_json TEXT NOT NULL DEFAULT '[]',
    source_pages_json TEXT NOT NULL DEFAULT '[]',
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS collection_question_fts USING fts5(
    question_id UNINDEXED,
    search_text,
    title_text,
    metadata_text,
    tokenize='unicode61 remove_diacritics 2'
);
CREATE INDEX IF NOT EXISTS idx_collection_docs_collection ON collection_docs(collection_id);
CREATE INDEX IF NOT EXISTS idx_collection_questions_doc ON collection_questions(doc_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _is_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return path.is_symlink() or bool(int(getattr(info, "st_file_attributes", 0) or 0) & flag)


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _has_reparse_component(root: Path, relative: Path) -> bool:
    current = root
    for part in relative.parts:
        if part in {"", "."}:
            continue
        current = current / part
        if current.exists() and _is_reparse(current):
            return True
    return False


def _safe_relative(root: Path, relative: str, *, allow_file: bool = True) -> tuple[Path, str]:
    root = root.resolve(strict=True)
    raw = Path(relative)
    if raw.is_absolute() or ".." in raw.parts:
        raise ValueError("Question collection source must be knowledge-root-relative")
    if _has_reparse_component(root, raw):
        raise ValueError("Question collection source cannot contain a symlink or reparse point")
    candidate = (root / raw).resolve(strict=True)
    if candidate == root or not _within(candidate, root):
        raise ValueError("Question collection source must stay below the configured knowledge root")
    if _is_reparse(candidate):
        raise ValueError("Question collection source cannot be a symlink or reparse point")
    if not candidate.is_dir() and not (allow_file and candidate.is_file()):
        raise ValueError("Question collection source must be a Markdown file or directory")
    return candidate, candidate.relative_to(root).as_posix()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _collection_id(knowledge_root_id: str, source_relative: str) -> str:
    digest = hashlib.sha256(f"{knowledge_root_id}\n{source_relative.casefold()}".encode("utf-8")).hexdigest()
    return f"QCOL-{digest[:24].upper()}"


def _markdown_files(source: Path) -> list[Path]:
    if source.is_file():
        if source.suffix.casefold() != ".md":
            raise ValueError("Question collection file must use the .md extension")
        return [source]
    files = [
        path for path in sorted(source.glob("*.md"), key=lambda item: item.name.casefold())
        if not path.name.startswith("_") and not _is_reparse(path)
    ]
    if not files:
        raise ValueError("Question collection directory contains no top-level Markdown source")
    if len(files) > 1000:
        raise ValueError("Question collection contains more than 1000 Markdown source files")
    return files


def _parse_note(text: str, fallback_title: str) -> tuple[str, str | None, list[dict[str, Any]]]:
    lines = text.splitlines()
    title = fallback_title
    section: str | None = None
    starts: list[tuple[int, str, str | None]] = []
    for index, line in enumerate(lines):
        title_match = _TITLE_HEADING.match(line)
        if title_match and title == fallback_title:
            title = title_match.group(1).strip()
        section_match = _SECTION_HEADING.match(line)
        if section_match:
            section = section_match.group(1).strip()
        question_match = _QUESTION_HEADING.match(line)
        if question_match:
            starts.append((index, question_match.group(1), section))
    parsed: list[dict[str, Any]] = []
    for ordinal, (start, number, current_section) in enumerate(starts, start=1):
        end = starts[ordinal][0] if ordinal < len(starts) else len(lines)
        content = "\n".join(lines[start:end]).strip()
        refs = [left or right for left, right in _IMAGE_REF.findall(content)]
        pages = [match.strip() for match in _SOURCE_PAGE.findall(content)]
        parsed.append({
            "number": number,
            "ordinal": ordinal,
            "section": current_section,
            "content": content,
            "image_refs": refs,
            "source_pages": pages,
            "start_line": start + 1,
            "end_line": end,
        })
    year_match = _YEAR.search(title) or _YEAR.search(fallback_title)
    return title, year_match.group(1) if year_match else None, parsed


def _verification(source: Path, question_count: int) -> tuple[str, dict[str, Any] | None]:
    directory = source if source.is_dir() else source.parent
    receipt_path = directory / "work" / "receipt.json"
    if not receipt_path.is_file() or _is_reparse(receipt_path):
        return "unverified", None
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "receipt_invalid", None
    declared = receipt.get("problem_count")
    verified = receipt.get("verified_count")
    status = str(receipt.get("status") or "")
    if declared == question_count and verified == question_count and status == "verified":
        return "verified", {"problem_count": declared, "verified_count": verified, "status": status}
    return "receipt_mismatch", {"problem_count": declared, "verified_count": verified, "status": status}


def _connect(database: Path) -> sqlite3.Connection:
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA_SQL)
    return connection


def _read_only(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return row is not None


def _stored_root_id(connection: sqlite3.Connection, table: str) -> str | None:
    if not _table_exists(connection, table):
        return None
    row = connection.execute(
        f"SELECT value FROM {table} WHERE key='knowledge_root_id'"
    ).fetchone()
    return None if row is None else str(row[0])


def _prepare_import(
    knowledge_root: Path,
    database: Path,
    *,
    source_relative: str,
    knowledge_root_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    source, canonical_source = _safe_relative(knowledge_root, source_relative)
    files = _markdown_files(source)
    documents: list[dict[str, Any]] = []
    prepared: list[dict[str, Any]] = []
    total_questions = 0
    source_digest = hashlib.sha256()
    for path in files:
        before = path.stat()
        raw = path.read_bytes()
        after = path.stat()
        if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
            raise RuntimeError(f"Question source changed while reading {path.name}; preview again")
        text = raw[3:].decode("utf-8") if raw.startswith(b"\xef\xbb\xbf") else raw.decode("utf-8")
        title, year, parsed = _parse_note(text, path.stem)
        if not parsed:
            raise ValueError(f"No numbered level-three questions found in {path.name}")
        relative = path.relative_to(knowledge_root).as_posix()
        digest = hashlib.sha256(raw).hexdigest()
        source_digest.update(relative.encode("utf-8"))
        source_digest.update(bytes.fromhex(digest))
        documents.append({
            "relative_path": relative,
            "title": title,
            "year": year,
            "paper": path.parent.name,
            "subject": QUESTION_SUBJECT,
            "file_size": len(raw),
            "mtime_ns": int(after.st_mtime_ns),
            "sha256": digest,
            "question_count": len(parsed),
        })
        prepared.append({
            "path": path,
            "relative_path": relative,
            "raw": raw,
            "title": title,
            "year": year,
            "paper": path.parent.name,
            "mtime_ns": int(after.st_mtime_ns),
            "parsed": parsed,
        })
        total_questions += len(parsed)
    verification_status, receipt_summary = _verification(source, total_questions)
    collection_id = _collection_id(knowledge_root_id, canonical_source)
    action = "replace" if collection_exists(database, collection_id) else "create"
    plan: dict[str, Any] = {
        "schema_version": IMPORT_PLAN_SCHEMA_VERSION,
        "ok": True,
        "status": "import_preview",
        "applied": False,
        "collection_id": collection_id,
        "action": action,
        "source_scope": "knowledge_root",
        "source_relative": canonical_source,
        "source_format": "numbered-markdown/v1",
        "parser_version": "numbered-heading-parser/v1",
        "display_name": source.stem if source.is_file() else source.name,
        "source_sha256": source_digest.hexdigest(),
        "document_count": len(documents),
        "question_count": total_questions,
        "verification_status": verification_status,
        "receipt_summary": receipt_summary,
        "documents": documents,
    }
    plan["plan_sha256"] = _canonical_sha256(plan)
    return plan, prepared


def plan_import(
    knowledge_root: Path,
    database: Path,
    *,
    source_relative: str,
    knowledge_root_id: str,
) -> dict[str, Any]:
    plan, _ = _prepare_import(
        knowledge_root,
        database,
        source_relative=source_relative,
        knowledge_root_id=knowledge_root_id,
    )
    return plan


def collection_exists(database: Path, collection_id: str) -> bool:
    if not database.is_file():
        return False
    with closing(_read_only(database)) as connection:
        if not _table_exists(connection, "question_collections"):
            return False
        return connection.execute(
            "SELECT 1 FROM question_collections WHERE collection_id=?", (collection_id,)
        ).fetchone() is not None


def import_collection(
    knowledge_root: Path,
    database: Path,
    *,
    source_relative: str,
    knowledge_root_id: str,
    write: bool = False,
    expect_plan_sha256: str | None = None,
) -> dict[str, Any]:
    plan, prepared = _prepare_import(
        knowledge_root,
        database,
        source_relative=source_relative,
        knowledge_root_id=knowledge_root_id,
    )
    if not write:
        return plan
    if not expect_plan_sha256 or expect_plan_sha256 != plan["plan_sha256"]:
        raise ValueError("--expect-plan-sha256 must match the current question-import preview")
    assert_question_database_root(database, knowledge_root_id)
    now = _now()
    with closing(_connect(database)) as connection, connection:
        stored_root_ids = {
            value for value in (
                _stored_root_id(connection, "question_collection_meta"),
                _stored_root_id(connection, "question_meta"),
            ) if value is not None
        }
        if any(value != knowledge_root_id for value in stored_root_ids):
            raise RuntimeError("Question index belongs to a different knowledge root")
        connection.execute(
            "INSERT INTO question_collection_meta(key,value) VALUES ('schema_version',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (COLLECTION_SCHEMA_VERSION,),
        )
        connection.execute(
            "INSERT INTO question_collection_meta(key,value) VALUES ('knowledge_root_id',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (knowledge_root_id,),
        )
        existing_docs = connection.execute(
            "SELECT id FROM collection_docs WHERE collection_id=?", (plan["collection_id"],)
        ).fetchall()
        for row in existing_docs:
            connection.execute(
                "DELETE FROM collection_question_fts WHERE question_id IN "
                "(SELECT id FROM collection_questions WHERE doc_id=?)", (int(row[0]),)
            )
        connection.execute("DELETE FROM collection_docs WHERE collection_id=?", (plan["collection_id"],))
        connection.execute(
            "INSERT INTO question_collections(collection_id,source_scope,source_relative,display_name,source_format,"
            "parser_version,verification_status,source_sha256,document_count,question_count,imported_at,scan_complete,scan_errors_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(collection_id) DO UPDATE SET source_scope=excluded.source_scope,source_relative=excluded.source_relative,"
            "display_name=excluded.display_name,source_format=excluded.source_format,parser_version=excluded.parser_version,"
            "verification_status=excluded.verification_status,source_sha256=excluded.source_sha256,document_count=excluded.document_count,"
            "question_count=excluded.question_count,imported_at=excluded.imported_at,scan_complete=excluded.scan_complete,"
            "scan_errors_json=excluded.scan_errors_json",
            (
                plan["collection_id"], "knowledge_root", plan["source_relative"], plan["display_name"],
                plan["source_format"], plan["parser_version"], plan["verification_status"], plan["source_sha256"],
                plan["document_count"], plan["question_count"], now, 1, "[]",
            ),
        )
        for document in prepared:
            raw = document["raw"]
            title = str(document["title"])
            year = document["year"]
            parsed = document["parsed"]
            relative = str(document["relative_path"])
            paper = str(document["paper"])
            cursor = connection.execute(
                "INSERT INTO collection_docs(collection_id,relative_path,title,year,paper,subject,file_size,mtime_ns,question_count,indexed_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    plan["collection_id"], relative, title, year, paper, QUESTION_SUBJECT, len(raw),
                    int(document["mtime_ns"]), len(parsed), now,
                ),
            )
            doc_id = int(cursor.lastrowid)
            for item in parsed:
                stable_key = f"collection:{plan['collection_id']}:{relative}#q{item['number']}"
                cursor = connection.execute(
                    "INSERT INTO collection_questions(doc_id,stable_key,question_number,ordinal,section,content_md,image_refs_json,"
                    "source_pages_json,start_line,end_line) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        doc_id, stable_key, item["number"], item["ordinal"], item["section"], item["content"],
                        json.dumps(item["image_refs"], ensure_ascii=False),
                        json.dumps(item["source_pages"], ensure_ascii=False), item["start_line"], item["end_line"],
                    ),
                )
                question_id = int(cursor.lastrowid)
                metadata = " ".join(filter(None, (year, paper, QUESTION_SUBJECT, item["section"], item["number"])))
                connection.execute(
                    "INSERT INTO collection_question_fts(question_id,search_text,title_text,metadata_text) VALUES (?,?,?,?)",
                    (question_id, make_search_text(item["content"], title, metadata), make_search_text(title), make_search_text(metadata)),
                )
    return {**plan, "ok": True, "status": "imported", "applied": True}


def _candidate_directories(discovery_root: Path, *, max_directories: int, max_entries: int = 50000) -> tuple[list[Path], list[str], bool]:
    """Bound enumeration before sorting; one lookahead detects an exhausted budget."""
    found: list[Path] = []
    errors: list[str] = []
    visited = entries = 0
    pending = [discovery_root]
    while pending:
        if visited >= max_directories:
            return found, errors, True
        current = pending.pop()
        if _is_reparse(current):
            continue
        visited += 1
        dirs, files = [], set()
        try:
            with os.scandir(current) as iterator:
                for entry in iterator:
                    if entries >= max_entries:
                        return found, errors, True
                    entries += 1
                    path = current / entry.name
                    if _is_reparse(path):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if not entry.name.startswith('.'):
                            dirs.append(entry.name)
                    elif entry.is_file(follow_symlinks=False):
                        files.add(entry.name)
        except OSError as error:
            errors.append(str(error))
            continue
        visible_md = any(name.casefold().endswith('.md') and not name.startswith('_') for name in files)
        work = current / 'work'
        work_marker = False
        if 'work' in dirs and not _is_reparse(work):
            for name in ('receipt.json', 'collection_index.sqlite3'):
                marker = work / name
                if not _is_reparse(marker) and marker.is_file():
                    work_marker = True
                    break
        if visible_md and ('source-manifest.json' in files or work_marker):
            found.append(current)
        else:
            pending.extend(current / name for name in sorted(dirs, reverse=True))
    return found, errors, False



def discover_collections(
    knowledge_root: Path,
    database: Path,
    *,
    discovery_relative: str = DEFAULT_DISCOVERY_RELATIVE,
    max_directories: int = 5000,
    max_entries: int = 50000,
) -> dict[str, Any]:
    if type(max_entries) is not int or not 1 <= max_entries <= 1000000:
        raise ValueError('max_entries must be between 1 and 1000000')
    if max_directories < 1 or max_directories > 100000:
        raise ValueError("max_directories must be between 1 and 100000")
    try:
        discovery_root, canonical_discovery = _safe_relative(knowledge_root, discovery_relative, allow_file=False)
    except FileNotFoundError:
        return {
            "ok": True, "status": "discovery_root_missing", "discovery_relative": discovery_relative,
            "candidates": [], "errors": [], "truncated": False, "discovery_complete": False,
        }
    registered: set[str] = set()
    if database.is_file():
        with closing(_read_only(database)) as connection:
            if _table_exists(connection, "question_collections"):
                registered = {str(row[0]) for row in connection.execute("SELECT source_relative FROM question_collections")}
    paths, errors, truncated = _candidate_directories(discovery_root, max_directories=max_directories, max_entries=max_entries)
    candidates = []
    for path in paths:
        relative = path.relative_to(knowledge_root.resolve(strict=True)).as_posix()
        candidates.append({
            "source_relative": relative,
            "display_name": path.name,
            "source_format": "numbered-markdown/v1",
            "registered": relative in registered,
        })
    return {
        "ok": not errors and not truncated,
        "status": "complete" if not errors and not truncated else "coverage_gap",
        "discovery_relative": canonical_discovery,
        "candidates": candidates,
        "errors": errors,
        "truncated": truncated,
        "discovery_complete": not errors and not truncated,
    }


def collection_status(
    database: Path,
    knowledge_root: Path,
    *,
    knowledge_root_id: str,
    discovery_relative: str = DEFAULT_DISCOVERY_RELATIVE,
    max_directories: int = 5000,
    max_entries: int = 50000,
) -> dict[str, Any]:
    collections: list[dict[str, Any]] = []
    root_match = True
    if database.is_file():
        with closing(_read_only(database)) as connection:
            if _table_exists(connection, "question_collections"):
                meta = {
                    str(row[0]): str(row[1])
                    for row in connection.execute("SELECT key,value FROM question_collection_meta")
                }
                root_match = not meta.get("knowledge_root_id") or meta.get("knowledge_root_id") == knowledge_root_id
                collections = [dict(row) for row in connection.execute(
                    "SELECT collection_id,source_scope,source_relative,display_name,source_format,parser_version,"
                    "verification_status,source_sha256,document_count,question_count,imported_at,scan_complete,scan_errors_json "
                    "FROM question_collections ORDER BY source_relative"
                )]
                for item in collections:
                    item["scan_complete"] = bool(item["scan_complete"])
                    item["scan_errors"] = json.loads(str(item.pop("scan_errors_json")))
    discovery = discover_collections(
        knowledge_root, database, discovery_relative=discovery_relative, max_directories=max_directories, max_entries=max_entries
    )
    unimported = [item for item in discovery["candidates"] if not item["registered"]]
    scans_complete = all(item["scan_complete"] for item in collections)
    coverage_complete = bool(discovery["discovery_complete"] and not unimported and scans_complete and root_match)
    return {
        "ok": root_match and discovery["ok"],
        "status": "ready" if coverage_complete else "coverage_gap",
        "schema_version": COLLECTION_SCHEMA_VERSION,
        "root_matches_config": root_match,
        "collections": collections,
        "collection_count": len(collections),
        "documents": sum(int(item["document_count"]) for item in collections),
        "questions": sum(int(item["question_count"]) for item in collections),
        "discovery": discovery,
        "discovered_unimported": unimported,
        "coverage_complete": coverage_complete,
    }


def search_collections(
    database: Path,
    query: str,
    *,
    aliases: Iterable[str] = (),
    limit: int = 8,
    year: str | None = None,
    paper: str | None = None,
    knowledge_root_id: str | None = None,
) -> list[dict[str, Any]]:
    if not database.is_file() or limit < 1:
        return []
    with closing(_read_only(database)) as connection:
        if not _table_exists(connection, "collection_question_fts"):
            return []
        meta = {
            str(row[0]): str(row[1])
            for row in connection.execute("SELECT key,value FROM question_collection_meta")
        }
        if knowledge_root_id and meta.get("knowledge_root_id") not in {None, knowledge_root_id}:
            raise RuntimeError("Question collection index belongs to a different knowledge root")
        matches: dict[int, tuple[float, sqlite3.Row]] = {}
        for term in [query, *[value for value in aliases if value]]:
            clauses = ["collection_question_fts MATCH ?"]
            params: list[Any] = [make_match_query(term)]
            if year:
                clauses.append("d.year = ?")
                params.append(year)
            if paper:
                clauses.append("d.paper LIKE ?")
                params.append(f"%{paper}%")
            params.append(limit * 3)
            rows = connection.execute(
                f"""SELECT q.id,q.stable_key,q.question_number,q.section,q.content_md,q.image_refs_json,
                q.source_pages_json,q.start_line,q.end_line,d.relative_path,d.title,d.year,d.paper,d.subject,
                c.collection_id,c.display_name,c.source_relative,c.verification_status,
                bm25(collection_question_fts,8.0,3.0,4.0) AS score
                FROM collection_question_fts
                JOIN collection_questions q ON q.id=collection_question_fts.question_id
                JOIN collection_docs d ON d.id=q.doc_id
                JOIN question_collections c ON c.collection_id=d.collection_id
                WHERE {' AND '.join(clauses)} ORDER BY score LIMIT ?""",
                params,
            ).fetchall()
            for row in rows:
                qid = int(row["id"])
                score = float(row["score"])
                if qid not in matches or score < matches[qid][0]:
                    matches[qid] = (score, row)
    results: list[dict[str, Any]] = []
    for score, row in sorted(matches.values(), key=lambda item: item[0])[:limit]:
        results.append({
            "stable_key": row["stable_key"],
            "collection_id": row["collection_id"],
            "collection": row["display_name"],
            "verification_status": row["verification_status"],
            "title": row["title"],
            "year": row["year"],
            "paper": row["paper"],
            "subject": row["subject"],
            "question_number": row["question_number"],
            "section": row["section"],
            "source_relative_path": row["relative_path"],
            "collection_source_relative": row["source_relative"],
            "start_line": row["start_line"],
            "end_line": row["end_line"],
            "content_md": row["content_md"],
            "snippet": compact_snippet(str(row["content_md"]), 420),
            "image_refs": json.loads(str(row["image_refs_json"])),
            "source_pages": json.loads(str(row["source_pages_json"])),
            "score": score,
            "source_adapter": "question_collection",
        })
    return results


__all__ = [
    "COLLECTION_SCHEMA_VERSION",
    "DEFAULT_DISCOVERY_RELATIVE",
    "IMPORT_PLAN_SCHEMA_VERSION",
    "collection_status",
    "discover_collections",
    "import_collection",
    "plan_import",
    "search_collections",
]
