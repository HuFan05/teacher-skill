"""Portable resource registry for a local personal-knowledge root.

The SQLite database is the only writable registry.  ``resources.jsonl`` is a
generated recovery manifest and is never merged back into a live database.
All filesystem mutations use a deterministic dry-run plan followed by an
explicit plan-hash confirmation.
"""

from __future__ import annotations

import base64
import fnmatch
import hashlib
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import stat
import tempfile
import uuid
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Iterator, Mapping, Sequence


REGISTRY_SCHEMA_VERSION = 1
MANIFEST_SCHEMA_VERSION = 1
RESOURCE_ID_RE = re.compile(r"^KB-[A-Z2-7]{26}$")
ROOT_ID_RE = re.compile(r"^KBROOT-[A-Z2-7]{26}$")

DEFAULT_SOFT_EXCLUDED_DIRS = frozenset(
    {".git", ".svn", "node_modules", "__pycache__", ".venv"}
)
DEFAULT_SOFT_EXCLUDED_FILES = (
    "*.tmp",
    "*.part",
    "~$*",
    "*.lock",
    "*.lck",
    "*.sqlite-wal",
    "*.sqlite-shm",
    "*-wal",
    "*-shm",
    ".megaignore",
    "desktop.ini",
    "Thumbs.db",
)


class RegistryError(RuntimeError):
    """Base class for registry failures."""


class RegistrySafetyError(RegistryError):
    """Raised when a path or operation is outside the permitted boundary."""


class RegistryPlanError(RegistryError):
    """Raised when a write lacks the exact dry-run plan confirmation."""


class RegistryGenerationError(RegistryError):
    """Raised when SQLite and its generated manifest are out of sync."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _random_base32() -> str:
    return base64.b32encode(secrets.token_bytes(16)).decode("ascii").rstrip("=")


def generate_resource_id() -> str:
    """Return a random, non-sequential 128-bit resource identifier."""

    return f"KB-{_random_base32()}"


def generate_root_id() -> str:
    return f"KBROOT-{_random_base32()}"


def validate_resource_id(resource_id: str) -> str:
    if not isinstance(resource_id, str) or not RESOURCE_ID_RE.fullmatch(resource_id):
        raise RegistryError(f"Invalid resource_id: {resource_id!r}")
    return resource_id


def validate_root_id(root_id: str) -> str:
    if not isinstance(root_id, str) or not ROOT_ID_RE.fullmatch(root_id):
        raise RegistryError(f"Invalid knowledge_root_id: {root_id!r}")
    return root_id


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def make_plan(operation: str, actions: Sequence[Mapping[str, object]], **context: object) -> dict[str, object]:
    """Build a JSON-serialisable deterministic write plan."""

    body: dict[str, object] = {
        "operation": operation,
        "actions": [dict(item) for item in actions],
        "context": context,
    }
    digest = hashlib.sha256(_json_bytes(body)).hexdigest()
    return {
        **body,
        "dry_run": True,
        "write_required": True,
        "plan_sha256": digest,
    }


def _confirm_plan(plan: Mapping[str, object], expect_plan_sha256: str | None) -> None:
    expected = plan.get("plan_sha256")
    if not expect_plan_sha256 or not secrets.compare_digest(str(expected), expect_plan_sha256):
        raise RegistryPlanError(
            "Write requires --expect-plan-sha256 matching the current dry-run plan"
        )


def _applied(plan: Mapping[str, object], **values: object) -> dict[str, object]:
    return {**plan, "dry_run": False, "applied": True, **values}


def _absolute_directory(path: os.PathLike[str] | str) -> Path:
    candidate = Path(path).expanduser()
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise RegistrySafetyError(f"Knowledge root cannot be resolved: {candidate}") from exc
    if not resolved.is_dir():
        raise RegistrySafetyError(f"Knowledge root is not a directory: {resolved}")
    if _is_reparse_point(candidate):
        raise RegistrySafetyError(f"Knowledge root cannot be a reparse point: {candidate}")
    return resolved


def _is_reparse_point(path: Path, entry: os.DirEntry[str] | None = None) -> bool:
    try:
        if entry is not None and entry.is_symlink():
            return True
        if path.is_symlink():
            return True
        info = entry.stat(follow_symlinks=False) if entry is not None else path.lstat()
    except OSError:
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _normalise_relative_text(value: os.PathLike[str] | str) -> str:
    text = os.fspath(value).replace("\\", "/").strip()
    pure = PurePosixPath(text)
    if not text or text.startswith("/") or pure.is_absolute() or any(part == ".." for part in pure.parts):
        raise RegistrySafetyError(f"Path must be root-relative and cannot escape: {value}")
    normalised = pure.as_posix()
    if normalised in ("", "."):
        raise RegistrySafetyError("A resource path cannot name the knowledge root")
    return normalised


def _relative_path(root: Path, path: os.PathLike[str] | str, *, must_exist: bool = True) -> tuple[Path, str]:
    raw = Path(path).expanduser()
    candidate = raw if raw.is_absolute() else root / raw
    try:
        if must_exist and _is_reparse_point(candidate):
            raise RegistrySafetyError(f"Reparse points are excluded: {candidate}")
        resolved = candidate.resolve(strict=must_exist)
        relative = resolved.relative_to(root).as_posix()
    except RegistrySafetyError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise RegistrySafetyError(f"Path is missing or outside knowledge root: {candidate}") from exc
    if not relative or relative == ".":
        raise RegistrySafetyError("A resource path cannot name the knowledge root")
    return resolved, _normalise_relative_text(relative)


def _safe_registered_path(
    root: Path,
    relative_path: os.PathLike[str] | str,
    *,
    must_exist: bool = False,
) -> Path:
    """Resolve a stored root-relative path without traversing reparse points."""

    relative = _normalise_relative_text(relative_path)
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.exists() and _is_reparse_point(current):
            raise RegistrySafetyError(f"Registered path traverses a reparse point: {current}")
    try:
        resolved = current.resolve(strict=must_exist)
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise RegistrySafetyError(
            f"Registered path escapes or is missing from knowledge_root: {relative}"
        ) from exc
    return resolved


def _require_external_database(root: Path, database_path: os.PathLike[str] | str) -> Path:
    database = Path(database_path).expanduser().resolve(strict=False)
    try:
        database.relative_to(root)
    except ValueError:
        return database
    raise RegistrySafetyError(
        "The writable SQLite registry must be outside knowledge_root (normally under LOCALAPPDATA)"
    )


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _atomic_write_json(path: Path, value: Mapping[str, object]) -> None:
    _atomic_write_text(
        path,
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    )


@contextmanager
def _connect(database_path: os.PathLike[str] | str, *, create: bool = False) -> Iterator[sqlite3.Connection]:
    path = Path(database_path).expanduser().resolve(strict=False)
    if not create and not path.is_file():
        raise RegistryError(f"Registry database does not exist: {path}")
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        yield connection
    finally:
        connection.close()


@contextmanager
def _connect_read_only(database_path: os.PathLike[str] | str) -> Iterator[sqlite3.Connection]:
    path = Path(database_path).expanduser().resolve(strict=False)
    if not path.is_file():
        raise RegistryError(f"Registry database does not exist: {path}")
    try:
        connection = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
        connection.execute("PRAGMA schema_version").fetchone()
    except sqlite3.Error as exc:
        try:
            connection.close()
        except UnboundLocalError:
            pass
        raise RegistryError(f"Registry database is unreadable: {path}: {exc}") from exc
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        yield connection
    finally:
        connection.close()


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone() is not None


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS registry_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS resources (
            resource_id TEXT PRIMARY KEY,
            relative_path TEXT NOT NULL,
            display_name TEXT NOT NULL,
            media_type TEXT,
            status TEXT NOT NULL CHECK(status IN ('active', 'missing', 'retired')),
            size INTEGER,
            mtime_ns INTEGER,
            sha256 TEXT,
            hash_state TEXT NOT NULL CHECK(hash_state IN ('unverified', 'verified', 'stale', 'error')),
            content_version INTEGER NOT NULL DEFAULT 0,
            source_url TEXT,
            doi TEXT,
            isbn TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_seen_scan TEXT
        );
        CREATE INDEX IF NOT EXISTS resources_display_name_idx ON resources(display_name);
        CREATE INDEX IF NOT EXISTS resources_sha256_idx ON resources(sha256);
        CREATE INDEX IF NOT EXISTS resources_status_idx ON resources(status);
        CREATE UNIQUE INDEX IF NOT EXISTS resources_live_path_idx
            ON resources(relative_path) WHERE status != 'retired';
        CREATE TABLE IF NOT EXISTS path_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            resource_id TEXT NOT NULL REFERENCES resources(resource_id) ON DELETE RESTRICT,
            relative_path TEXT NOT NULL,
            valid_from TEXT NOT NULL,
            valid_to TEXT,
            UNIQUE(resource_id, relative_path, valid_from)
        );
        CREATE TABLE IF NOT EXISTS resource_versions (
            resource_id TEXT NOT NULL REFERENCES resources(resource_id) ON DELETE RESTRICT,
            version INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            size INTEGER NOT NULL,
            mtime_ns INTEGER NOT NULL,
            observed_at TEXT NOT NULL,
            PRIMARY KEY(resource_id, version)
        );
        CREATE TABLE IF NOT EXISTS note_documents (
            note_relative_path TEXT PRIMARY KEY,
            note_sha256 TEXT NOT NULL,
            mtime_ns INTEGER,
            scan_status TEXT NOT NULL,
            scanned_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS note_references (
            note_relative_path TEXT NOT NULL REFERENCES note_documents(note_relative_path) ON DELETE CASCADE,
            ordinal INTEGER NOT NULL,
            resource_id TEXT NOT NULL,
            target_uri TEXT,
            line INTEGER,
            PRIMARY KEY(note_relative_path, ordinal)
        );
        CREATE INDEX IF NOT EXISTS note_references_resource_idx ON note_references(resource_id);
        CREATE TABLE IF NOT EXISTS operation_log (
            operation_id TEXT PRIMARY KEY,
            operation TEXT NOT NULL,
            plan_sha256 TEXT NOT NULL,
            state TEXT NOT NULL,
            recovery_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )


def _meta_get(connection: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = connection.execute("SELECT value FROM registry_meta WHERE key = ?", (key,)).fetchone()
    return default if row is None else str(row[0])


def _meta_set(connection: sqlite3.Connection, key: str, value: object) -> None:
    connection.execute(
        "INSERT INTO registry_meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


def _generation(connection: sqlite3.Connection) -> int:
    return int(_meta_get(connection, "generation", "0") or 0)


def _increment_generation(connection: sqlite3.Connection) -> int:
    value = _generation(connection) + 1
    _meta_set(connection, "generation", value)
    return value


def _manifest_path(root: Path, connection: sqlite3.Connection | None = None, manifest_path: os.PathLike[str] | str | None = None) -> Path:
    if manifest_path is not None:
        candidate = Path(manifest_path).expanduser()
        candidate = candidate if candidate.is_absolute() else root / candidate
        try:
            lexical = Path(os.path.abspath(candidate))
            relative = lexical.relative_to(root).as_posix()
        except ValueError as exc:
            raise RegistrySafetyError(f"Recovery manifest must remain inside knowledge_root: {candidate}") from exc
        return _safe_registered_path(root, relative, must_exist=False)
    relative = ".mpk/resources.jsonl"
    if connection is not None:
        relative = _meta_get(connection, "manifest_relative_path", relative) or relative
    return _safe_registered_path(root, relative, must_exist=False)


def _root_marker_path(root: Path) -> Path:
    return _safe_registered_path(root, ".mpk/root.json", must_exist=False)


def _read_manifest_header(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as stream:
            first = stream.readline()
        value = json.loads(first)
    except (OSError, json.JSONDecodeError) as exc:
        raise RegistryGenerationError(f"Cannot read recovery manifest header: {path}: {exc}") from exc
    if not isinstance(value, dict) or value.get("record_type") != "manifest":
        raise RegistryGenerationError(f"Invalid recovery manifest header: {path}")
    return value


def manifest_state(
    knowledge_root: os.PathLike[str] | str,
    database_path: os.PathLike[str] | str,
    *,
    manifest_path: os.PathLike[str] | str | None = None,
) -> dict[str, object]:
    root = _absolute_directory(knowledge_root)
    with _connect(database_path) as connection:
        database_generation = _generation(connection)
        root_id = _meta_get(connection, "knowledge_root_id")
        path = _manifest_path(root, connection, manifest_path)
    marker_path = _root_marker_path(root)
    marker_id: str | None = None
    marker_error: str | None = None
    if marker_path.is_file():
        try:
            marker_value = json.loads(marker_path.read_text(encoding="utf-8"))
            marker_id = str(marker_value.get("knowledge_root_id"))
            validate_root_id(marker_id)
        except (OSError, json.JSONDecodeError, RegistryError) as exc:
            marker_error = str(exc)
    else:
        marker_error = "root_marker_missing"
    header = _read_manifest_header(path)
    if marker_error:
        state = "root_marker_invalid" if marker_path.exists() else "root_marker_missing"
        manifest_generation = header.get("generation") if header else None
        manifest_root_id = header.get("knowledge_root_id") if header else None
    elif marker_id != root_id:
        state = "root_identity_mismatch"
        manifest_generation = header.get("generation") if header else None
        manifest_root_id = header.get("knowledge_root_id") if header else None
    elif header is None:
        state = "missing"
        manifest_generation = None
        manifest_root_id = None
    else:
        manifest_generation = header.get("generation")
        manifest_root_id = header.get("knowledge_root_id")
        state = (
            "in_sync"
            if manifest_generation == database_generation and manifest_root_id == root_id
            else "generation_mismatch"
        )
    return {
        "state": state,
        "database_generation": database_generation,
        "manifest_generation": manifest_generation,
        "knowledge_root_id": root_id,
        "manifest_root_id": manifest_root_id,
        "manifest_path": str(path),
        "root_marker_path": str(marker_path),
        "root_marker_id": marker_id,
        "root_marker_error": marker_error,
    }


def _require_manifest_sync(root: Path, database_path: os.PathLike[str] | str, manifest_path: os.PathLike[str] | str | None = None) -> None:
    state = manifest_state(root, database_path, manifest_path=manifest_path)
    if state["state"] != "in_sync":
        remedy = "run registry-export" if state["state"] in {"missing", "generation_mismatch"} else "restore or relink the root identity"
        raise RegistryGenerationError(
            f"Registry writes are blocked ({state['state']}); {remedy} first"
        )


def _resource_payload(connection: sqlite3.Connection, row: sqlite3.Row, *,
                      include_history: bool = True, history_limit: int | None = None,
                      history_offset: int = 0) -> dict[str, object]:
    # Full history remains the recovery-manifest/internal compatibility default.
    # Public resolution explicitly requests no history or a bounded page.
    histories: dict[str, object] = {}
    if include_history:
        for key, columns, table, order in (
            ("path_history", "relative_path, valid_from, valid_to", "path_history", "id"),
            ("versions", "version, sha256, size, mtime_ns, observed_at", "resource_versions", "version"),
        ):
            sql = f"SELECT {columns} FROM {table} WHERE resource_id = ? ORDER BY {order}"
            parameters = (row["resource_id"],)
            if history_limit is not None:
                sql += " LIMIT ? OFFSET ?"
                parameters += (history_limit + 1, history_offset)
            items = [dict(item) for item in connection.execute(sql, parameters)]
            histories[key] = items if history_limit is None else items[:history_limit]
            if history_limit is not None:
                histories.setdefault("history_page", {"offset": history_offset, "limit_per_kind": history_limit})[key + "_has_more"] = len(items) > history_limit
    keys = (
        "resource_id",
        "relative_path",
        "display_name",
        "media_type",
        "status",
        "size",
        "mtime_ns",
        "sha256",
        "hash_state",
        "content_version",
        "source_url",
        "doi",
        "isbn",
        "created_at",
        "updated_at",
    )
    return {
        "record_type": "resource",
        **{key: row[key] for key in keys},
        **histories,
    }


def _manifest_text(connection: sqlite3.Connection) -> str:
    header = {
        "record_type": "manifest",
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "registry_schema_version": REGISTRY_SCHEMA_VERSION,
        "knowledge_root_id": _meta_get(connection, "knowledge_root_id"),
        "knowledge_root_name": _meta_get(connection, "knowledge_root_name"),
        "vault_relative_path": _meta_get(connection, "vault_relative_path", ""),
        "excluded_relative_paths": json.loads(_meta_get(connection, "excluded_relative_paths", "[]") or "[]"),
        "manifest_relative_path": _meta_get(connection, "manifest_relative_path", ".mpk/resources.jsonl"),
        "generation": _generation(connection),
    }
    lines = [json.dumps(header, ensure_ascii=False, sort_keys=True)]
    rows = connection.execute("SELECT * FROM resources ORDER BY resource_id").fetchall()
    lines.extend(
        json.dumps(_resource_payload(connection, row), ensure_ascii=False, sort_keys=True)
        for row in rows
    )
    return "\n".join(lines) + "\n"


def _export_now(root: Path, database_path: os.PathLike[str] | str, manifest_path: os.PathLike[str] | str | None = None) -> Path:
    with _connect(database_path) as connection:
        path = _manifest_path(root, connection, manifest_path)
        text = _manifest_text(connection)
    _atomic_write_text(path, text)
    return path


def registry_init(
    knowledge_root: os.PathLike[str] | str,
    database_path: os.PathLike[str] | str,
    *,
    knowledge_root_name: str | None = None,
    knowledge_root_id: str | None = None,
    vault_relative_path: str | None = None,
    manifest_relative_path: str = ".mpk/resources.jsonl",
    excluded_relative_paths: Sequence[str] = (),
    write: bool = False,
    expect_plan_sha256: str | None = None,
) -> dict[str, object]:
    """Preview or initialise a registry without scanning any real content."""

    root = _absolute_directory(knowledge_root)
    database = _require_external_database(root, database_path)
    marker = _root_marker_path(root)
    manifest_relative_path = _normalise_relative_text(manifest_relative_path)
    vault_relative = _normalise_relative_text(vault_relative_path) if vault_relative_path else None
    exclusions = sorted({_normalise_relative_text(item) for item in excluded_relative_paths})
    inventory_preview = preview_inventory(
        root,
        vault_relative_path=vault_relative,
        excluded_relative_paths=exclusions,
    )
    chosen_root_id = validate_root_id(knowledge_root_id) if knowledge_root_id else None
    existing_marker: dict[str, object] | None = None
    if marker.is_file():
        try:
            existing_marker = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RegistryError(f"Cannot read root marker: {marker}") from exc
        marker_id = existing_marker.get("knowledge_root_id")
        validate_root_id(str(marker_id))
        if chosen_root_id and chosen_root_id != marker_id:
            raise RegistrySafetyError("Requested root identity conflicts with .mpk/root.json")
        chosen_root_id = str(marker_id)
    database_has_schema = False
    if database.exists():
        with _connect_read_only(database) as connection:
            database_has_schema = _table_exists(connection, "registry_meta")
            database_id = (
                _meta_get(connection, "knowledge_root_id")
                if database_has_schema
                else None
            )
        if database_id:
            if chosen_root_id and database_id != chosen_root_id:
                raise RegistrySafetyError("Database identity conflicts with knowledge root")
            chosen_root_id = database_id

    portable_manifest = root.joinpath(*PurePosixPath(manifest_relative_path).parts)
    if portable_manifest.is_file():
        portable_header, _portable_records = _read_manifest(portable_manifest)
        portable_id = validate_root_id(str(portable_header.get("knowledge_root_id") or ""))
        if chosen_root_id and portable_id != chosen_root_id:
            raise RegistrySafetyError("Portable manifest identity conflicts with the selected root")
        chosen_root_id = portable_id
        if not database_has_schema:
            raise RegistrySafetyError(
                "A portable registry manifest already exists but the local SQLite registry "
                "is missing or uninitialized; run registry-restore so resource identities "
                "are not overwritten"
            )

    actions: list[dict[str, object]] = []
    if not marker.exists():
        actions.append({"action": "create_root_marker", "path": str(marker)})
    if not database.exists():
        actions.append({"action": "create_registry_database", "path": str(database)})
    elif not database_has_schema:
        actions.append({"action": "initialize_registry_schema", "path": str(database)})
    actions.append({"action": "export_recovery_manifest", "relative_path": manifest_relative_path})
    plan = make_plan(
        "registry-init",
        actions,
        knowledge_root=str(root),
        knowledge_root_name=knowledge_root_name or root.name,
        knowledge_root_id=chosen_root_id or "generate-on-write",
        vault_relative_path=vault_relative,
        excluded_relative_paths=exclusions,
        inventory_preview=inventory_preview,
    )
    # Keep the complete preview both in the hashed context and at top level so
    # callers can show the first-run inventory without unpacking plan internals.
    plan["inventory_preview"] = inventory_preview
    if not write:
        return plan
    _confirm_plan(plan, expect_plan_sha256)
    root_id = chosen_root_id or generate_root_id()
    marker_value = {
        "schema_version": 1,
        "knowledge_root_id": root_id,
        "knowledge_root_name": knowledge_root_name or root.name,
    }
    if existing_marker is None:
        _atomic_write_json(marker, marker_value)
    with _connect(database, create=True) as connection:
        _create_schema(connection)
        current_id = _meta_get(connection, "knowledge_root_id")
        if current_id and current_id != root_id:
            raise RegistrySafetyError("Database identity conflicts with knowledge root")
        _meta_set(connection, "registry_schema_version", REGISTRY_SCHEMA_VERSION)
        _meta_set(connection, "knowledge_root_id", root_id)
        _meta_set(connection, "knowledge_root_name", knowledge_root_name or root.name)
        _meta_set(connection, "manifest_relative_path", manifest_relative_path)
        _meta_set(connection, "vault_relative_path", vault_relative or "")
        _meta_set(connection, "excluded_relative_paths", json.dumps(exclusions, ensure_ascii=False))
        if _meta_get(connection, "generation") is None:
            _meta_set(connection, "generation", 0)
        connection.commit()
    manifest = _export_now(root, database)
    return _applied(
        plan,
        knowledge_root_id=root_id,
        database_path=str(database),
        manifest_path=str(manifest),
    )


def _excluded_prefixes(connection: sqlite3.Connection) -> tuple[str | None, tuple[str, ...]]:
    vault = _meta_get(connection, "vault_relative_path", "") or None
    raw = _meta_get(connection, "excluded_relative_paths", "[]") or "[]"
    try:
        values = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RegistryError("Registry excluded_relative_paths metadata is invalid") from exc
    if not isinstance(values, list):
        raise RegistryError("Registry excluded_relative_paths metadata is invalid")
    return vault, tuple(str(item) for item in values)


def _has_prefix(relative: str, prefix: str | None) -> bool:
    if not prefix:
        return False
    folded_relative = relative.casefold()
    folded_prefix = prefix.rstrip("/").casefold()
    return folded_relative == folded_prefix or folded_relative.startswith(folded_prefix + "/")


def classify_relative_path(
    relative_path: str,
    *,
    vault_relative_path: str | None = None,
    excluded_relative_paths: Sequence[str] = (),
    is_directory: bool = False,
) -> dict[str, object]:
    """Classify a normalised relative path using machine-executable exclusions."""

    relative = _normalise_relative_text(relative_path)
    parts = PurePosixPath(relative).parts
    if parts and parts[0].casefold() == ".mpk":
        return {"included": False, "kind": "hard", "reason": "management_directory"}
    if _has_prefix(relative, vault_relative_path):
        return {"included": False, "kind": "hard", "reason": "vault"}
    for excluded in excluded_relative_paths:
        if _has_prefix(relative, excluded):
            return {"included": False, "kind": "soft", "reason": "configured_exclusion"}
    directory_parts = parts if is_directory else parts[:-1]
    if any(part.casefold() in {item.casefold() for item in DEFAULT_SOFT_EXCLUDED_DIRS} for part in directory_parts if part):
        return {"included": False, "kind": "soft", "reason": "excluded_directory"}
    name = parts[-1] if parts else relative
    if name.casefold() in {"desktop.ini", "thumbs.db"} or any(
        fnmatch.fnmatch(name.casefold(), pattern.casefold()) for pattern in DEFAULT_SOFT_EXCLUDED_FILES
    ):
        return {"included": False, "kind": "soft", "reason": "temporary_or_tool_state"}
    if is_directory:
        return {"included": False, "kind": "hard", "reason": "directory"}
    return {"included": True, "kind": None, "reason": None}


def preview_inventory(
    knowledge_root: os.PathLike[str] | str,
    *,
    vault_relative_path: str | None = None,
    excluded_relative_paths: Sequence[str] = (),
    max_entries: int = 200000,
    sample_limit: int = 200,
    include_paths: bool = False,
) -> dict[str, object]:
    """Count eligible files and explain pruned paths without reading file bodies."""

    if max_entries <= 0 or sample_limit < 0:
        raise RegistryError("preview limits must be nonnegative and max_entries must be positive")
    root = _absolute_directory(knowledge_root)
    vault = _normalise_relative_text(vault_relative_path) if vault_relative_path else None
    exclusions = tuple(_normalise_relative_text(item) for item in excluded_relative_paths)
    stack = [root]
    enumerated = 0
    examined = 0
    eligible_count = 0
    eligible_sample: list[str] = []
    eligible_paths: list[str] = []
    excluded_roots: list[dict[str, object]] = []
    excluded_files: list[dict[str, object]] = []
    reparse_points: list[str] = []
    errors: list[dict[str, str]] = []
    unrecognized: list[str] = []
    excluded_root_count = 0
    excluded_file_count = 0
    reparse_point_count = 0
    unrecognized_count = 0
    truncated = False

    while stack and not truncated:
        directory = stack.pop()
        try:
            entries = []
            with os.scandir(directory) as scanner:
                for entry in scanner:
                    if enumerated >= max_entries:
                        truncated = True
                        break
                    enumerated += 1
                    entries.append(entry)
            entries.sort(key=lambda item: item.name.casefold(), reverse=True)
        except OSError as exc:
            errors.append({"path": str(directory), "error": str(exc)})
            continue
        for entry in entries:
            examined += 1
            if examined > max_entries:
                truncated = True
                break
            path = Path(entry.path)
            try:
                relative = path.relative_to(root).as_posix()
            except ValueError:
                errors.append({"path": str(path), "error": "scanner escaped knowledge_root"})
                continue
            if _is_reparse_point(path, entry):
                reparse_point_count += 1
                if len(reparse_points) < sample_limit:
                    reparse_points.append(relative)
                continue
            try:
                is_directory = entry.is_dir(follow_symlinks=False)
                is_file = entry.is_file(follow_symlinks=False)
            except OSError as exc:
                errors.append({"path": relative, "error": str(exc)})
                continue
            decision = classify_relative_path(
                relative,
                vault_relative_path=vault,
                excluded_relative_paths=exclusions,
                is_directory=is_directory,
            )
            if is_directory:
                if decision["reason"] == "directory":
                    stack.append(path)
                else:
                    excluded_root_count += 1
                    if len(excluded_roots) < sample_limit:
                        excluded_roots.append(
                            {"relative_path": relative, "kind": decision["kind"], "reason": decision["reason"]}
                        )
                continue
            if is_file:
                if decision["included"]:
                    eligible_count += 1
                    if include_paths:
                        eligible_paths.append(relative)
                    if len(eligible_sample) < sample_limit:
                        eligible_sample.append(relative)
                else:
                    excluded_file_count += 1
                    if len(excluded_files) < sample_limit:
                        excluded_files.append(
                            {"relative_path": relative, "kind": decision["kind"], "reason": decision["reason"]}
                        )
                continue
            unrecognized_count += 1
            if len(unrecognized) < sample_limit:
                unrecognized.append(relative)

    result: dict[str, object] = {
        "examined_entries": min(examined, max_entries),
        "eligible_file_count": eligible_count,
        "excluded_root_count": excluded_root_count,
        "excluded_file_count": excluded_file_count,
        "excluded_entry_count": excluded_root_count + excluded_file_count + reparse_point_count,
        "reparse_point_count": reparse_point_count,
        "unrecognized_entry_count": unrecognized_count,
        "error_count": len(errors),
        "eligible_file_sample": sorted(eligible_sample, key=str.casefold),
        "excluded_roots": sorted(excluded_roots, key=lambda item: str(item["relative_path"]).casefold()),
        "excluded_files": sorted(excluded_files, key=lambda item: str(item["relative_path"]).casefold()),
        "reparse_points": sorted(reparse_points, key=str.casefold),
        "unrecognized_entries": sorted(unrecognized, key=str.casefold),
        "errors": errors,
        "truncated": truncated,
        "max_entries": max_entries,
    }
    if include_paths:
        result["eligible_paths"] = sorted(eligible_paths, key=str.casefold)
    return result


def _iter_inventory(
    root: Path,
    *,
    vault_relative_path: str | None,
    excluded_relative_paths: Sequence[str],
    after: str = "",
    after_sort_key: tuple[int, str, str] | None = None,
    priority_relative_paths: Sequence[str] = (),
    limit: int | None = None,
) -> Iterator[tuple[Path, str, os.stat_result]]:
    """Yield globally sorted ordinary files without following links.

    The materialized sort is intentional: a depth-first traversal order is not
    compatible with a single lexical resume cursor (for example ``a.pdf`` and
    ``a/z.pdf``).  Any uncertain directory-entry error aborts the scan so a
    complete pass can never mark an unreadable resource missing.
    """

    discovered: list[tuple[Path, str, os.stat_result]] = []

    def walk(directory: Path) -> None:
        try:
            entries = sorted(os.scandir(directory), key=lambda item: item.name.casefold())
        except OSError as exc:
            raise RegistrySafetyError(f"Cannot scan directory: {directory}: {exc}") from exc
        for entry in entries:
            path = Path(entry.path)
            try:
                relative = path.relative_to(root).as_posix()
            except ValueError as exc:
                raise RegistrySafetyError(f"Scanner escaped knowledge root: {path}") from exc
            if _is_reparse_point(path, entry):
                continue
            try:
                is_directory = entry.is_dir(follow_symlinks=False)
                is_file = entry.is_file(follow_symlinks=False)
            except OSError as exc:
                raise RegistrySafetyError(f"Cannot classify directory entry: {path}: {exc}") from exc
            decision = classify_relative_path(
                relative,
                vault_relative_path=vault_relative_path,
                excluded_relative_paths=excluded_relative_paths,
                is_directory=is_directory,
            )
            if is_directory:
                # Ordinary directories are containers, not resources.  Recurse
                # through them, while pruning every hard/soft excluded subtree.
                if decision["reason"] != "directory":
                    continue
                walk(path)
            elif not decision["included"]:
                continue
            elif is_file:
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError as exc:
                    raise RegistrySafetyError(f"Cannot stat directory entry: {path}: {exc}") from exc
                discovered.append((path, relative, info))

    walk(root)
    priority_keys = {
        _normalise_relative_text(value).casefold()
        for value in priority_relative_paths
    }

    def sort_key(item: tuple[Path, str, os.stat_result]) -> tuple[int, str, str]:
        relative = item[1]
        return (
            0 if relative.casefold() in priority_keys else 1,
            relative.casefold(),
            relative,
        )

    discovered.sort(key=sort_key)
    if after_sort_key is None:
        # Backward-compatible private-call behaviour when no structured scan
        # cursor was supplied.
        after_sort_key = (1, after.casefold(), after) if after else (-1, "", "")
    selected = (item for item in discovered if sort_key(item) > after_sort_key)
    for index, item in enumerate(selected):
        if limit is not None and index >= limit:
            break
        yield item


def _scan_priority_digest(priority_relative_paths: Sequence[str]) -> tuple[list[str], str]:
    normalized = sorted(
        {_normalise_relative_text(value) for value in priority_relative_paths},
        key=lambda value: (value.casefold(), value),
    )
    digest = hashlib.sha256(
        json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return normalized, digest


def _encode_scan_cursor(sort_key: tuple[int, str, str], priority_digest: str) -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "priority_digest": priority_digest,
            "priority_phase": int(sort_key[0]),
            "relative_path": sort_key[2],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _decode_scan_cursor(value: str, priority_digest: str) -> tuple[int, str, str]:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise RegistrySafetyError(
            "A registry-scan cursor without PDF priority cannot be resumed with PDF priority; "
            "start a new scan without --resume"
        ) from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise RegistrySafetyError("Registry scan cursor is invalid")
    if payload.get("priority_digest") != priority_digest:
        raise RegistrySafetyError(
            "PDF-index priority changed during registry-scan; start a new scan "
            "without --resume"
        )
    relative = _normalise_relative_text(str(payload.get("relative_path") or ""))
    phase = int(payload.get("priority_phase", -1))
    if phase not in (0, 1):
        raise RegistrySafetyError("Registry scan cursor priority phase is invalid")
    return phase, relative.casefold(), relative


def _insert_resource(
    connection: sqlite3.Connection,
    relative: str,
    info: os.stat_result,
    *,
    resource_id: str | None = None,
    source_url: str | None = None,
    doi: str | None = None,
    isbn: str | None = None,
    last_seen_scan: str | None = None,
) -> str:
    now = _utc_now()
    identifier = validate_resource_id(resource_id) if resource_id else generate_resource_id()
    while connection.execute("SELECT 1 FROM resources WHERE resource_id = ?", (identifier,)).fetchone():
        if resource_id:
            raise RegistryError(f"resource_id already exists: {identifier}")
        identifier = generate_resource_id()
    media_type = mimetypes.guess_type(relative)[0] or "application/octet-stream"
    connection.execute(
        "INSERT INTO resources(resource_id, relative_path, display_name, media_type, status, "
        "size, mtime_ns, sha256, hash_state, content_version, source_url, doi, isbn, created_at, "
        "updated_at, last_seen_scan) VALUES (?, ?, ?, ?, 'active', ?, ?, NULL, 'unverified', 0, "
        "?, ?, ?, ?, ?, ?)",
        (
            identifier,
            relative,
            PurePosixPath(relative).name,
            media_type,
            int(info.st_size),
            int(info.st_mtime_ns),
            source_url,
            doi,
            isbn,
            now,
            now,
            last_seen_scan,
        ),
    )
    connection.execute(
        "INSERT INTO path_history(resource_id, relative_path, valid_from) VALUES (?, ?, ?)",
        (identifier, relative, now),
    )
    return identifier


def registry_scan(
    knowledge_root: os.PathLike[str] | str,
    database_path: os.PathLike[str] | str,
    *,
    resume: bool = False,
    max_files: int = 1000,
    priority_relative_paths: Sequence[str] = (),
    manifest_path: os.PathLike[str] | str | None = None,
    write: bool = False,
    expect_plan_sha256: str | None = None,
) -> dict[str, object]:
    """Register a bounded deterministic batch of eligible files."""

    if max_files <= 0:
        raise RegistryError("max_files must be positive")
    root = _absolute_directory(knowledge_root)
    _require_manifest_sync(root, database_path, manifest_path)
    normalized_priority, priority_digest = _scan_priority_digest(
        priority_relative_paths
    )
    priority_keys = {value.casefold() for value in normalized_priority}
    with _connect(database_path) as connection:
        vault, exclusions = _excluded_prefixes(connection)
        cursor = (_meta_get(connection, "scan_cursor", "") or "") if resume else ""
        active_scan = (_meta_get(connection, "active_scan_id", "") or "") if resume else ""
        stored_priority_digest = (
            _meta_get(connection, "scan_priority_digest", "") or ""
        ) if resume else ""
        if active_scan and stored_priority_digest != priority_digest:
            raise RegistrySafetyError(
                "PDF-index priority changed during registry-scan; start a new scan "
                "without --resume"
            )
        cursor_sort_key = (
            _decode_scan_cursor(cursor, priority_digest)
            if cursor
            else (-1, "", "")
        )
        items = list(
            _iter_inventory(
                root,
                vault_relative_path=vault,
                excluded_relative_paths=exclusions,
                after_sort_key=cursor_sort_key,
                priority_relative_paths=normalized_priority,
                limit=max_files + 1,
            )
        )
        batch, overflow = items[:max_files], items[max_files:]
        has_more = bool(overflow)
        existing = {
            str(row["relative_path"]): row
            for row in connection.execute("SELECT * FROM resources WHERE status != 'retired'")
        }
        actions: list[dict[str, object]] = []
        for _, relative, info in batch:
            row = existing.get(relative)
            if row is None:
                actions.append(
                    {"action": "register", "relative_path": relative, "size": int(info.st_size), "mtime_ns": int(info.st_mtime_ns)}
                )
            elif row["size"] != info.st_size or row["mtime_ns"] != info.st_mtime_ns or row["status"] == "missing":
                actions.append(
                    {"action": "refresh_metadata", "resource_id": row["resource_id"], "relative_path": relative, "size": int(info.st_size), "mtime_ns": int(info.st_mtime_ns)}
                )
        if has_more and batch:
            last_relative = batch[-1][1]
            next_cursor = _encode_scan_cursor(
                (
                    0 if last_relative.casefold() in priority_keys else 1,
                    last_relative.casefold(),
                    last_relative,
                ),
                priority_digest,
            )
        else:
            next_cursor = ""
        planned_missing: list[str] = []
        if not has_more:
            seen_paths = {relative for _, relative, _ in batch}
            if resume and active_scan:
                seen_paths.update(
                    str(row[0])
                    for row in connection.execute(
                        "SELECT relative_path FROM resources WHERE last_seen_scan = ?",
                        (active_scan,),
                    )
                )
            planned_missing = sorted(
                str(row["resource_id"])
                for row in existing.values()
                if row["status"] != "retired" and row["relative_path"] not in seen_paths
            )
            actions.extend(
                {"action": "mark_missing", "resource_id": identifier}
                for identifier in planned_missing
            )
        actions.append(
            {"action": "save_scan_progress", "processed": len(batch), "has_more": has_more, "next_cursor": next_cursor}
        )
        plan = make_plan(
            "registry-scan",
            actions,
            knowledge_root_id=_meta_get(connection, "knowledge_root_id"),
            resume=resume,
            cursor=cursor,
            max_files=max_files,
            priority_digest=priority_digest,
            priority_count=len(normalized_priority),
        )
    if not write:
        return {**plan, "processed": len(batch), "has_more": has_more, "next_cursor": next_cursor}
    _confirm_plan(plan, expect_plan_sha256)
    scan_id = active_scan or uuid.uuid4().hex
    changed = False
    registered: list[str] = []
    refreshed: list[str] = []
    missing: list[str] = []
    now = _utc_now()
    with _connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        if not resume or not active_scan:
            _meta_set(connection, "active_scan_id", scan_id)
            _meta_set(connection, "scan_cursor", "")
            _meta_set(connection, "scan_priority_digest", priority_digest)
        for planned_path, relative, info in batch:
            try:
                current_info = planned_path.stat()
            except OSError as exc:
                raise RegistryPlanError(f"Resource disappeared after dry-run: {relative}") from exc
            if _is_reparse_point(planned_path) or current_info.st_size != info.st_size or current_info.st_mtime_ns != info.st_mtime_ns:
                raise RegistryPlanError(f"Resource changed after dry-run: {relative}")
            row = connection.execute(
                "SELECT * FROM resources WHERE relative_path = ? AND status != 'retired'",
                (relative,),
            ).fetchone()
            if row is None:
                identifier = _insert_resource(connection, relative, info, last_seen_scan=scan_id)
                registered.append(identifier)
                changed = True
                continue
            hash_state = row["hash_state"]
            metadata_changed = row["size"] != info.st_size or row["mtime_ns"] != info.st_mtime_ns
            if metadata_changed and row["sha256"]:
                hash_state = "stale"
            if metadata_changed or row["status"] == "missing":
                changed = True
                refreshed.append(str(row["resource_id"]))
            connection.execute(
                "UPDATE resources SET size = ?, mtime_ns = ?, status = CASE WHEN status = 'retired' "
                "THEN status ELSE 'active' END, hash_state = ?, updated_at = CASE WHEN size != ? OR "
                "mtime_ns != ? OR status = 'missing' THEN ? ELSE updated_at END, last_seen_scan = ? "
                "WHERE resource_id = ?",
                (
                    int(info.st_size),
                    int(info.st_mtime_ns),
                    hash_state,
                    int(info.st_size),
                    int(info.st_mtime_ns),
                    now,
                    scan_id,
                    row["resource_id"],
                ),
            )
        if has_more:
            _meta_set(connection, "scan_cursor", next_cursor)
            _meta_set(connection, "active_scan_id", scan_id)
            _meta_set(connection, "scan_priority_digest", priority_digest)
        else:
            rows = connection.execute(
                "SELECT resource_id FROM resources WHERE status != 'retired' "
                "AND COALESCE(last_seen_scan, '') != ?",
                (scan_id,),
            ).fetchall()
            missing = [str(row[0]) for row in rows]
            if missing:
                changed = True
                connection.executemany(
                    "UPDATE resources SET status = 'missing', updated_at = ? WHERE resource_id = ?",
                    [(now, identifier) for identifier in missing],
                )
            _meta_set(connection, "scan_cursor", "")
            _meta_set(connection, "active_scan_id", "")
            _meta_set(connection, "scan_priority_digest", "")
        generation = _increment_generation(connection) if changed else _generation(connection)
        connection.commit()
    if changed:
        _export_now(root, database_path, manifest_path)
    return _applied(
        plan,
        processed=len(batch),
        has_more=has_more,
        next_cursor=next_cursor,
        registered=registered,
        refreshed=refreshed,
        missing=missing,
        generation=generation,
        priority_digest=priority_digest,
        priority_count=len(normalized_priority),
    )


def _sha256_file(path: Path) -> tuple[str, os.stat_result]:
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    after = path.stat()
    before_identity = (
        before.st_size,
        before.st_mtime_ns,
        getattr(before, "st_ctime_ns", None),
        getattr(before, "st_dev", None),
        getattr(before, "st_ino", None),
    )
    after_identity = (
        after.st_size,
        after.st_mtime_ns,
        getattr(after, "st_ctime_ns", None),
        getattr(after, "st_dev", None),
        getattr(after, "st_ino", None),
    )
    if before_identity != after_identity:
        raise RegistrySafetyError(f"File changed while hashing: {path}")
    return digest.hexdigest(), after


def registry_hash(
    knowledge_root: os.PathLike[str] | str,
    database_path: os.PathLike[str] | str,
    *,
    resume: bool = False,
    max_files: int = 100,
    verify_all: bool = False,
    manifest_path: os.PathLike[str] | str | None = None,
    write: bool = False,
    expect_plan_sha256: str | None = None,
) -> dict[str, object]:
    """Hash a bounded batch and record immutable content-version observations."""

    if max_files <= 0:
        raise RegistryError("max_files must be positive")
    root = _absolute_directory(knowledge_root)
    _require_manifest_sync(root, database_path, manifest_path)
    with _connect(database_path) as connection:
        cursor = (_meta_get(connection, "hash_cursor", "") or "") if resume else ""
        requested_mode = "all" if verify_all else "pending"
        stored_mode = (_meta_get(connection, "hash_mode", "") or "") if resume else ""
        if cursor and stored_mode and stored_mode != requested_mode:
            raise RegistrySafetyError(
                "Hash resume mode changed; restart registry-hash without --resume"
            )
        state_filter = "" if verify_all else "AND hash_state != 'verified' "
        rows = connection.execute(
            "SELECT * FROM resources WHERE status = 'active' "
            f"{state_filter}AND resource_id > ? ORDER BY resource_id LIMIT ?",
            (cursor, max_files + 1),
        ).fetchall()
        batch, overflow = rows[:max_files], rows[max_files:]
        has_more = bool(overflow)
        observations: list[dict[str, object]] = []
        for row in batch:
            path = _safe_registered_path(root, str(row["relative_path"]), must_exist=False)
            if not path.is_file() or _is_reparse_point(path):
                observations.append({"action": "mark_missing", "resource_id": row["resource_id"], "relative_path": row["relative_path"]})
                continue
            digest, info = _sha256_file(path)
            observations.append(
                {"action": "verify_hash", "resource_id": row["resource_id"], "relative_path": row["relative_path"], "sha256": digest, "size": int(info.st_size), "mtime_ns": int(info.st_mtime_ns)}
            )
        next_cursor = str(batch[-1]["resource_id"]) if has_more and batch else ""
        actions = observations + [
            {"action": "save_hash_progress", "processed": len(batch), "has_more": has_more, "next_cursor": next_cursor}
        ]
        plan = make_plan(
            "registry-hash",
            actions,
            knowledge_root_id=_meta_get(connection, "knowledge_root_id"),
            resume=resume,
            cursor=cursor,
            max_files=max_files,
            verify_all=verify_all,
            hash_mode=requested_mode,
        )
    if not write:
        return {**plan, "processed": len(batch), "has_more": has_more, "next_cursor": next_cursor}
    _confirm_plan(plan, expect_plan_sha256)
    changed = False
    verified: list[str] = []
    missing: list[str] = []
    now = _utc_now()
    with _connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        for observation in observations:
            identifier = str(observation["resource_id"])
            current = connection.execute("SELECT * FROM resources WHERE resource_id = ?", (identifier,)).fetchone()
            if current is None:
                raise RegistrySafetyError(f"Resource disappeared from registry during hash: {identifier}")
            observed_relative = str(observation["relative_path"])
            if current["status"] != "active" or str(current["relative_path"]) != observed_relative:
                raise RegistryPlanError(
                    f"Resource registry state changed after dry-run: {identifier}"
                )
            if observation["action"] == "mark_missing":
                planned_path = _safe_registered_path(
                    root, observed_relative, must_exist=False
                )
                if planned_path.is_file() and not _is_reparse_point(planned_path):
                    raise RegistryPlanError(
                        f"Resource appeared after the missing-file dry-run: {identifier}"
                    )
                if current["status"] != "missing":
                    connection.execute("UPDATE resources SET status = 'missing', updated_at = ? WHERE resource_id = ?", (now, identifier))
                    changed = True
                missing.append(identifier)
                continue
            path = _safe_registered_path(root, observed_relative, must_exist=True)
            if not path.is_file() or _is_reparse_point(path):
                raise RegistryPlanError(
                    f"Resource became missing or unsafe after dry-run: {identifier}"
                )
            digest, info = _sha256_file(path)
            verified_path = _safe_registered_path(root, observed_relative, must_exist=True)
            if verified_path != path or not verified_path.is_file():
                raise RegistryPlanError(
                    f"Resource path changed while hashing: {identifier}"
                )
            if digest != observation["sha256"] or info.st_size != observation["size"] or info.st_mtime_ns != observation["mtime_ns"]:
                raise RegistryPlanError(f"Resource changed after dry-run: {identifier}")
            old_digest = current["sha256"]
            version = int(current["content_version"])
            if old_digest != digest:
                version = version + 1 if version else 1
                connection.execute(
                    "INSERT INTO resource_versions(resource_id, version, sha256, size, mtime_ns, observed_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (identifier, version, digest, int(info.st_size), int(info.st_mtime_ns), now),
                )
            if old_digest != digest or current["hash_state"] != "verified" or current["size"] != info.st_size or current["mtime_ns"] != info.st_mtime_ns:
                changed = True
            connection.execute(
                "UPDATE resources SET sha256 = ?, hash_state = 'verified', content_version = ?, size = ?, mtime_ns = ?, updated_at = ? WHERE resource_id = ?",
                (digest, version, int(info.st_size), int(info.st_mtime_ns), now, identifier),
            )
            verified.append(identifier)
        _meta_set(connection, "hash_cursor", next_cursor if has_more else "")
        _meta_set(connection, "hash_mode", requested_mode if has_more else "")
        generation = _increment_generation(connection) if changed else _generation(connection)
        connection.commit()
    if changed:
        _export_now(root, database_path, manifest_path)
    return _applied(
        plan,
        processed=len(batch),
        has_more=has_more,
        next_cursor=next_cursor,
        verified=verified,
        missing=missing,
        generation=generation,
    )


def _classification_for_existing_path(root: Path, connection: sqlite3.Connection, path: os.PathLike[str] | str) -> tuple[Path, str, os.stat_result]:
    raw = Path(path).expanduser()
    candidate = raw if raw.is_absolute() else root / raw
    try:
        lexical = Path(os.path.abspath(candidate))
        relative = _normalise_relative_text(lexical.relative_to(root).as_posix())
    except ValueError as exc:
        raise RegistrySafetyError(
            f"Path is missing or outside knowledge root: {candidate}"
        ) from exc
    resolved = _safe_registered_path(root, relative, must_exist=True)
    if not resolved.is_file() or _is_reparse_point(resolved):
        raise RegistrySafetyError(f"Resource must be an ordinary file: {resolved}")
    vault, exclusions = _excluded_prefixes(connection)
    decision = classify_relative_path(relative, vault_relative_path=vault, excluded_relative_paths=exclusions)
    if not decision["included"]:
        raise RegistrySafetyError(f"Resource is excluded ({decision['reason']}): {relative}")
    return resolved, relative, resolved.stat()


def resource_register(
    knowledge_root: os.PathLike[str] | str,
    database_path: os.PathLike[str] | str,
    path: os.PathLike[str] | str,
    *,
    source_url: str | None = None,
    doi: str | None = None,
    isbn: str | None = None,
    manifest_path: os.PathLike[str] | str | None = None,
    write: bool = False,
    expect_plan_sha256: str | None = None,
) -> dict[str, object]:
    root = _absolute_directory(knowledge_root)
    _require_manifest_sync(root, database_path, manifest_path)
    with _connect(database_path) as connection:
        _, relative, info = _classification_for_existing_path(root, connection, path)
        existing = connection.execute(
            "SELECT resource_id FROM resources WHERE relative_path = ? AND status != 'retired'",
            (relative,),
        ).fetchone()
        actions = (
            [{"action": "already_registered", "relative_path": relative, "resource_id": existing[0]}]
            if existing
            else [{
                "action": "register",
                "relative_path": relative,
                "size": int(info.st_size),
                "mtime_ns": int(info.st_mtime_ns),
                "ctime_ns": int(getattr(info, "st_ctime_ns", 0)),
                "device": int(getattr(info, "st_dev", 0)),
                "file_id": int(getattr(info, "st_ino", 0)),
            }]
        )
        plan = make_plan(
            "resource-register",
            actions,
            knowledge_root_id=_meta_get(connection, "knowledge_root_id"),
            source_url=source_url,
            doi=doi,
            isbn=isbn,
        )
    if not write:
        return plan
    _confirm_plan(plan, expect_plan_sha256)
    with _connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        resolved_now, relative_now, info_now = _classification_for_existing_path(
            root, connection, path
        )
        existing_now = connection.execute(
            "SELECT resource_id FROM resources WHERE relative_path = ? AND status != 'retired'",
            (relative_now,),
        ).fetchone()
        if existing:
            if existing_now is None or str(existing_now[0]) != str(existing[0]):
                raise RegistryPlanError("Resource registration changed after dry-run")
            connection.rollback()
            return _applied(plan, resource_id=str(existing[0]), changed=False)
        if existing_now is not None:
            raise RegistryPlanError(
                f"Resource was registered concurrently: {relative_now}"
            )
        action = actions[0]
        current_identity = (
            relative_now,
            int(info_now.st_size),
            int(info_now.st_mtime_ns),
            int(getattr(info_now, "st_ctime_ns", 0)),
            int(getattr(info_now, "st_dev", 0)),
            int(getattr(info_now, "st_ino", 0)),
        )
        planned_identity = (
            str(action["relative_path"]),
            int(action["size"]),
            int(action["mtime_ns"]),
            int(action["ctime_ns"]),
            int(action["device"]),
            int(action["file_id"]),
        )
        if current_identity != planned_identity or resolved_now != _safe_registered_path(
            root, relative, must_exist=True
        ):
            raise RegistryPlanError(f"Resource changed after dry-run: {relative}")
        identifier = _insert_resource(
            connection,
            relative_now,
            info_now,
            source_url=source_url,
            doi=doi,
            isbn=isbn,
        )
        generation = _increment_generation(connection)
        connection.commit()
    _export_now(root, database_path, manifest_path)
    return _applied(plan, resource_id=identifier, changed=True, generation=generation)


def resource_resolve(
    knowledge_root: os.PathLike[str] | str,
    database_path: os.PathLike[str] | str,
    resource_id: str,
    *,
    include_history: bool = True,
    history_limit: int | None = None,
    history_offset: int = 0,
) -> dict[str, object]:
    if type(include_history) is not bool:
        raise RegistryError("Invalid history selection")
    if history_limit is not None and (type(history_limit) is not int or not 1 <= history_limit <= 100):
        raise RegistryError("Invalid history page limit")
    if type(history_offset) is not int or history_offset < 0:
        raise RegistryError("Invalid history offset")
    root = _absolute_directory(knowledge_root)
    identifier = validate_resource_id(resource_id)
    with _connect(database_path) as connection:
        row = connection.execute("SELECT * FROM resources WHERE resource_id = ?", (identifier,)).fetchone()
        if row is None:
            raise RegistryError(f"Unknown resource_id: {identifier}")
        payload = _resource_payload(connection, row, include_history=include_history, history_limit=history_limit, history_offset=history_offset)
    path = _safe_registered_path(root, str(row["relative_path"]), must_exist=False)
    exists = path.is_file() and not _is_reparse_point(path)
    suffix = path.suffix.casefold()
    recommended_skill = "pdf" if suffix == ".pdf" else "manage-personal-knowledge"
    return {
        **payload,
        "absolute_path": str(path),
        "file_uri": path.as_uri(),
        "exists": exists,
        "recommended_skill": recommended_skill,
    }


def resource_search(
    database_path: os.PathLike[str] | str,
    query: str,
    *,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, object]]:
    if not query.strip():
        raise RegistryError("Search query cannot be empty")
    if limit <= 0 or limit > 1000:
        raise RegistryError("limit must be between 1 and 1000")
    if type(offset) is not int or offset < 0:
        raise RegistryError("offset must be nonnegative")
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"
    with _connect(database_path) as connection:
        rows = connection.execute(
            "SELECT resource_id, relative_path, display_name, media_type, status, hash_state, "
            "source_url, doi, isbn FROM resources WHERE display_name LIKE ? ESCAPE '\\' OR "
            "relative_path LIKE ? ESCAPE '\\' OR source_url LIKE ? ESCAPE '\\' OR doi LIKE ? "
            "ESCAPE '\\' OR isbn LIKE ? ESCAPE '\\' ORDER BY CASE status WHEN 'active' THEN 0 "
            "WHEN 'missing' THEN 1 ELSE 2 END, display_name, resource_id LIMIT ? OFFSET ?",
            (pattern, pattern, pattern, pattern, pattern, limit, offset),
        ).fetchall()
    return [dict(row) for row in rows]


def registry_status(
    knowledge_root: os.PathLike[str] | str,
    database_path: os.PathLike[str] | str,
    *,
    manifest_path: os.PathLike[str] | str | None = None,
    include_inventory: bool = True,
    max_entries: int = 200000,
) -> dict[str, object]:
    if type(include_inventory) is not bool or type(max_entries) is not int or not 1 <= max_entries <= 1000000:
        raise RegistryError("Invalid inventory scope")
    root = _absolute_directory(knowledge_root)
    with _connect(database_path) as connection:
        vault, exclusions = _excluded_prefixes(connection)
        status_counts = Counter(
            {str(row["status"]): int(row["count"]) for row in connection.execute("SELECT status, COUNT(*) AS count FROM resources GROUP BY status")}
        )
        hash_counts = {
            str(row["hash_state"]): int(row["count"])
            for row in connection.execute("SELECT hash_state, COUNT(*) AS count FROM resources GROUP BY hash_state")
        }
        total = int(connection.execute("SELECT COUNT(*) FROM resources").fetchone()[0])
        scan_cursor = _meta_get(connection, "scan_cursor", "") or ""
        hash_cursor = _meta_get(connection, "hash_cursor", "") or ""
        registered_paths = {
            str(row["relative_path"])
            for row in (connection.execute("SELECT relative_path FROM resources WHERE status != 'retired'") if include_inventory else ())
        }
        reference_counts = {
            "notes": int(connection.execute("SELECT COUNT(*) FROM note_documents").fetchone()[0]),
            "references": int(connection.execute("SELECT COUNT(*) FROM note_references").fetchone()[0]),
            "diagnostic_notes": int(connection.execute("SELECT COUNT(*) FROM note_documents WHERE scan_status != 'ok'").fetchone()[0]),
            "sync_pending_notes": int(connection.execute("SELECT COUNT(*) FROM note_documents WHERE scan_status = 'reference_sync_pending'").fetchone()[0]),
        }
        incomplete_operations = int(
            connection.execute(
                "SELECT COUNT(*) FROM operation_log WHERE state NOT IN ('applied', 'rolled_back')"
            ).fetchone()[0]
        )
    if not include_inventory:
        return {"status_scope": "registered_database_counts", "inventory_checked": False,
                "manifest_checked": False, "total": total,
                "status_counts": {name: status_counts.get(name, 0) for name in ("active", "missing", "retired")},
                "hash_counts": hash_counts, "reference_cache": reference_counts,
                "reference_sync_pending_count": reference_counts["sync_pending_notes"],
                "incomplete_operations": incomplete_operations,
                "scan_resume_available": bool(scan_cursor), "hash_resume_available": bool(hash_cursor)}
    preview = preview_inventory(
        root,
        vault_relative_path=vault,
        excluded_relative_paths=exclusions,
        include_paths=True,
        max_entries=max_entries,
    )
    eligible_paths = set(str(item) for item in preview.pop("eligible_paths", []))
    unregistered = sorted(eligible_paths - registered_paths, key=str.casefold)
    manifest = manifest_state(root, database_path, manifest_path=manifest_path)
    conflicts: list[dict[str, object]] = []
    if manifest["state"] != "in_sync":
        conflicts.append({"kind": "registry_manifest", "state": manifest["state"]})
    if incomplete_operations:
        conflicts.append(
            {"kind": "incomplete_operations", "count": incomplete_operations}
        )
    return {
        "knowledge_root": str(root),
        "total": total,
        "status_counts": {name: status_counts.get(name, 0) for name in ("active", "missing", "retired")},
        "hash_counts": hash_counts,
        "scan_resume_available": bool(scan_cursor),
        "hash_resume_available": bool(hash_cursor),
        "eligible_file_count": int(preview["eligible_file_count"]),
        "excluded_count": int(preview["excluded_entry_count"]),
        "unregistered_count": len(unregistered),
        "unregistered_sample": unregistered[:200],
        "inventory_preview": preview,
        "reference_cache": reference_counts,
        "reference_sync_pending_count": reference_counts["sync_pending_notes"],
        "conflict_count": sum(int(item.get("count", 1)) for item in conflicts),
        "conflicts": conflicts,
        "manifest": manifest,
    }


def resource_audit(
    knowledge_root: os.PathLike[str] | str,
    database_path: os.PathLike[str] | str,
    *,
    max_files: int = 10000,
    manifest_path: os.PathLike[str] | str | None = None,
) -> dict[str, object]:
    """Read-only audit; no inferred move is applied automatically."""

    if max_files <= 0:
        raise RegistryError("max_files must be positive")
    root = _absolute_directory(knowledge_root)
    with _connect(database_path) as connection:
        vault, exclusions = _excluded_prefixes(connection)
        rows = connection.execute("SELECT * FROM resources").fetchall()
        by_path = {
            str(row["relative_path"]): row for row in rows if row["status"] != "retired"
        }
        inventory = list(
            _iter_inventory(
                root,
                vault_relative_path=vault,
                excluded_relative_paths=exclusions,
                limit=max_files + 1,
            )
        )
        truncated = len(inventory) > max_files
        inventory = inventory[:max_files]
        present_paths = {relative for _, relative, _ in inventory}
        unregistered = sorted(relative for relative in present_paths if relative not in by_path)
        missing = [] if truncated else sorted(
            {str(row["resource_id"]) for row in rows if row["status"] != "retired" and row["relative_path"] not in present_paths}
        )
        metadata_drift = []
        for _, relative, info in inventory:
            row = by_path.get(relative)
            if row and (row["size"] != info.st_size or row["mtime_ns"] != info.st_mtime_ns):
                metadata_drift.append({"resource_id": row["resource_id"], "relative_path": relative})
        identifiers_by_digest: dict[str, list[str]] = defaultdict(list)
        for row in rows:
            if row["sha256"]:
                identifiers_by_digest[str(row["sha256"])].append(str(row["resource_id"]))
        duplicate_groups = [
            {"sha256": digest, "resource_ids": sorted(identifiers)}
            for digest, identifiers in identifiers_by_digest.items()
            if len(identifiers) > 1
        ]
        unique_hash_rebind_candidates: list[dict[str, object]] = []
        weak_rebind_candidates: list[dict[str, object]] = []
        missing_rows = [row for row in rows if row["resource_id"] in missing and row["sha256"]]
        if not truncated and missing_rows and unregistered:
            inventory_info = {
                relative: info for _, relative, info in inventory if relative in unregistered
            }
            missing_sizes = {
                int(row["size"]) for row in missing_rows if row["size"] is not None
            }
            unregistered_by_digest: dict[str, list[str]] = defaultdict(list)
            for relative in unregistered:
                info = inventory_info[relative]
                if int(info.st_size) not in missing_sizes:
                    continue
                path = root.joinpath(*PurePosixPath(relative).parts)
                digest, _ = _sha256_file(path)
                unregistered_by_digest[digest].append(relative)
            missing_by_digest: dict[str, list[sqlite3.Row]] = defaultdict(list)
            for row in missing_rows:
                missing_by_digest[str(row["sha256"])].append(row)
            for digest, matching_missing_rows in missing_by_digest.items():
                matches = unregistered_by_digest.get(digest, [])
                if len(matches) == 1 and len(matching_missing_rows) == 1:
                    row = matching_missing_rows[0]
                    unique_hash_rebind_candidates.append(
                        {"resource_id": row["resource_id"], "old_relative_path": row["relative_path"], "candidate_relative_path": matches[0], "requires_confirmation": True}
                    )
            for row in missing_rows:
                old_name = PurePosixPath(str(row["relative_path"])).name.casefold()
                for relative in unregistered:
                    info = inventory_info[relative]
                    if row["size"] is None or int(row["size"]) != int(info.st_size):
                        continue
                    signals = ["size"]
                    if PurePosixPath(relative).name.casefold() == old_name:
                        signals.append("file_name")
                    if row["mtime_ns"] is not None and int(row["mtime_ns"]) == int(info.st_mtime_ns):
                        signals.append("mtime_ns")
                    weak_rebind_candidates.append(
                        {
                            "resource_id": str(row["resource_id"]),
                            "old_relative_path": str(row["relative_path"]),
                            "candidate_relative_path": relative,
                            "signals": signals,
                            "requires_confirmation": True,
                            "identity_strength": "weak",
                        }
                    )
                    if len(weak_rebind_candidates) >= 200:
                        break
                if len(weak_rebind_candidates) >= 200:
                    break
    return {
        "manifest": manifest_state(root, database_path, manifest_path=manifest_path),
        "scanned_files": len(inventory),
        "truncated": truncated,
        "unregistered": unregistered,
        "missing": missing,
        "metadata_drift": metadata_drift,
        "duplicate_candidates": sorted(duplicate_groups, key=lambda item: str(item["sha256"])),
        "manual_move_candidates": unique_hash_rebind_candidates,
        "weak_manual_move_candidates": weak_rebind_candidates,
    }


def resource_retire(
    knowledge_root: os.PathLike[str] | str,
    database_path: os.PathLike[str] | str,
    resource_id: str,
    *,
    manifest_path: os.PathLike[str] | str | None = None,
    write: bool = False,
    expect_plan_sha256: str | None = None,
) -> dict[str, object]:
    root = _absolute_directory(knowledge_root)
    _require_manifest_sync(root, database_path, manifest_path)
    identifier = validate_resource_id(resource_id)
    with _connect(database_path) as connection:
        row = connection.execute("SELECT status, relative_path FROM resources WHERE resource_id = ?", (identifier,)).fetchone()
        if row is None:
            raise RegistryError(f"Unknown resource_id: {identifier}")
        action = "already_retired" if row["status"] == "retired" else "retire"
        plan = make_plan(
            "resource-retire",
            [{"action": action, "resource_id": identifier, "relative_path": row["relative_path"]}],
            knowledge_root_id=_meta_get(connection, "knowledge_root_id"),
        )
    if not write:
        return plan
    _confirm_plan(plan, expect_plan_sha256)
    if action == "already_retired":
        return _applied(plan, changed=False)
    with _connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("UPDATE resources SET status = 'retired', updated_at = ? WHERE resource_id = ?", (_utc_now(), identifier))
        generation = _increment_generation(connection)
        connection.commit()
    _export_now(root, database_path, manifest_path)
    return _applied(plan, changed=True, generation=generation)


def registry_export(
    knowledge_root: os.PathLike[str] | str,
    database_path: os.PathLike[str] | str,
    *,
    manifest_path: os.PathLike[str] | str | None = None,
    write: bool = False,
    expect_plan_sha256: str | None = None,
) -> dict[str, object]:
    root = _absolute_directory(knowledge_root)
    with _connect(database_path) as connection:
        path = _manifest_path(root, connection, manifest_path)
        generation = _generation(connection)
        count = int(connection.execute("SELECT COUNT(*) FROM resources").fetchone()[0])
        root_id = _meta_get(connection, "knowledge_root_id")
    plan = make_plan(
        "registry-export",
        [{"action": "replace_recovery_manifest", "path": str(path), "resource_count": count}],
        knowledge_root_id=root_id,
        generation=generation,
    )
    if not write:
        return plan
    _confirm_plan(plan, expect_plan_sha256)
    written = _export_now(root, database_path, manifest_path)
    return _applied(plan, manifest_path=str(written), generation=generation, resource_count=count)


def _read_manifest(path: Path) -> tuple[dict[str, object], list[dict[str, object]]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise RegistryError(f"Cannot read recovery manifest: {path}") from exc
    if not lines:
        raise RegistryError("Recovery manifest is empty")
    try:
        values = [json.loads(line) for line in lines]
    except json.JSONDecodeError as exc:
        raise RegistryError(f"Recovery manifest contains invalid JSON: {exc}") from exc
    header = values[0]
    if not isinstance(header, dict) or header.get("record_type") != "manifest" or header.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise RegistryError("Unsupported recovery manifest header")
    resources = values[1:]
    if any(not isinstance(item, dict) or item.get("record_type") != "resource" for item in resources):
        raise RegistryError("Recovery manifest contains an invalid resource record")
    return header, resources


def _validate_restore_manifest(
    header: Mapping[str, object],
    records: Sequence[Mapping[str, object]],
) -> tuple[str, tuple[str, ...]]:
    if header.get("registry_schema_version") != REGISTRY_SCHEMA_VERSION:
        raise RegistrySafetyError("Recovery manifest registry_schema_version is incompatible")
    try:
        generation = int(header.get("generation", -1))
    except (TypeError, ValueError) as exc:
        raise RegistrySafetyError("Recovery manifest generation is invalid") from exc
    if generation < 0:
        raise RegistrySafetyError("Recovery manifest generation cannot be negative")
    vault_raw = str(header.get("vault_relative_path") or "")
    if not vault_raw:
        raise RegistrySafetyError("Recovery manifest is missing vault_relative_path")
    vault = _normalise_relative_text(vault_raw)
    excluded_raw = header.get("excluded_relative_paths")
    if not isinstance(excluded_raw, list):
        raise RegistrySafetyError("Recovery manifest excluded_relative_paths must be a list")
    exclusions = tuple(_normalise_relative_text(str(item)) for item in excluded_raw)
    if len({item.casefold() for item in exclusions}) != len(exclusions):
        raise RegistrySafetyError("Recovery manifest contains duplicate exclusions")
    _normalise_relative_text(
        str(header.get("manifest_relative_path") or ".mpk/resources.jsonl")
    )

    seen_ids: set[str] = set()
    live_paths: set[str] = set()
    for record in records:
        identifier = validate_resource_id(str(record.get("resource_id") or ""))
        if identifier in seen_ids:
            raise RegistrySafetyError(f"Recovery manifest repeats resource_id: {identifier}")
        seen_ids.add(identifier)
        relative = _normalise_relative_text(str(record.get("relative_path") or ""))
        decision = classify_relative_path(
            relative,
            vault_relative_path=vault,
            excluded_relative_paths=exclusions,
        )
        if not decision["included"]:
            raise RegistrySafetyError(
                f"Recovery manifest resource is excluded ({decision['reason']}): {relative}"
            )
        status = str(record.get("status") or "")
        if status not in {"active", "missing", "retired"}:
            raise RegistrySafetyError(f"Recovery manifest status is invalid for {identifier}")
        path_key = relative.casefold()
        if status != "retired":
            if path_key in live_paths:
                raise RegistrySafetyError(f"Recovery manifest repeats a live path: {relative}")
            live_paths.add(path_key)
        hash_state = str(record.get("hash_state") or "")
        if hash_state not in {"unverified", "verified", "stale", "error"}:
            raise RegistrySafetyError(f"Recovery manifest hash_state is invalid for {identifier}")
        digest = record.get("sha256")
        if digest is not None and re.fullmatch(r"[0-9a-fA-F]{64}", str(digest)) is None:
            raise RegistrySafetyError(f"Recovery manifest SHA-256 is invalid for {identifier}")
        try:
            resource_size = int(record.get("size"))
            resource_mtime = int(record.get("mtime_ns"))
        except (TypeError, ValueError) as exc:
            raise RegistrySafetyError(
                f"Recovery resource metadata is invalid for {identifier}"
            ) from exc
        if resource_size < 0 or resource_mtime < 0:
            raise RegistrySafetyError(
                f"Recovery resource metadata is invalid for {identifier}"
            )
        if not isinstance(record.get("display_name"), str) or not str(
            record.get("display_name")
        ):
            raise RegistrySafetyError(
                f"Recovery display_name is invalid for {identifier}"
            )
        if not isinstance(record.get("created_at"), str) or not isinstance(
            record.get("updated_at"), str
        ):
            raise RegistrySafetyError(
                f"Recovery timestamps are invalid for {identifier}"
            )

        history = record.get("path_history")
        versions = record.get("versions")
        if not isinstance(history, list) or not isinstance(versions, list):
            raise RegistrySafetyError(f"Recovery manifest history/version arrays are invalid for {identifier}")
        open_paths: list[str] = []
        for item in history:
            if not isinstance(item, dict) or not isinstance(item.get("valid_from"), str):
                raise RegistrySafetyError(f"Recovery path history is invalid for {identifier}")
            historical = _normalise_relative_text(str(item.get("relative_path") or ""))
            historical_decision = classify_relative_path(
                historical,
                vault_relative_path=vault,
                excluded_relative_paths=exclusions,
            )
            if not historical_decision["included"]:
                raise RegistrySafetyError(f"Recovery path history is excluded for {identifier}")
            valid_to = item.get("valid_to")
            if valid_to is not None and not isinstance(valid_to, str):
                raise RegistrySafetyError(f"Recovery path-history end is invalid for {identifier}")
            if valid_to is None:
                open_paths.append(historical)
        if open_paths != [relative]:
            raise RegistrySafetyError(
                f"Recovery path history must contain one open current path for {identifier}"
            )
        version_numbers: set[int] = set()
        for item in versions:
            if not isinstance(item, dict):
                raise RegistrySafetyError(f"Recovery version is invalid for {identifier}")
            try:
                version = int(item.get("version"))
                size = int(item.get("size"))
                mtime_ns = int(item.get("mtime_ns"))
            except (TypeError, ValueError) as exc:
                raise RegistrySafetyError(f"Recovery version fields are invalid for {identifier}") from exc
            if version <= 0 or size < 0 or mtime_ns < 0 or version in version_numbers:
                raise RegistrySafetyError(f"Recovery version fields are invalid for {identifier}")
            version_numbers.add(version)
            if re.fullmatch(r"[0-9a-fA-F]{64}", str(item.get("sha256") or "")) is None:
                raise RegistrySafetyError(f"Recovery version SHA-256 is invalid for {identifier}")
            if not isinstance(item.get("observed_at"), str):
                raise RegistrySafetyError(f"Recovery version timestamp is invalid for {identifier}")
        try:
            content_version = int(record.get("content_version", 0))
        except (TypeError, ValueError) as exc:
            raise RegistrySafetyError(
                f"Recovery content_version is invalid for {identifier}"
            ) from exc
        expected_versions = set(range(1, content_version + 1))
        if version_numbers != expected_versions:
            raise RegistrySafetyError(f"Recovery content_version is inconsistent for {identifier}")
        if hash_state == "verified" and digest is None:
            raise RegistrySafetyError(
                f"Verified recovery resource lacks SHA-256 for {identifier}"
            )
        if content_version:
            latest = next(
                item for item in versions if int(item["version"]) == content_version
            )
            if digest is None or str(latest["sha256"]).casefold() != str(digest).casefold():
                raise RegistrySafetyError(
                    f"Recovery current SHA-256 does not match latest version for {identifier}"
                )
        elif digest is not None:
            raise RegistrySafetyError(
                f"Recovery SHA-256 has no content version for {identifier}"
            )
    return vault, exclusions


def _existing_database_snapshot(database: Path) -> tuple[int, dict[str, object]]:
    if not database.is_file():
        return 0, {"state": "missing"}
    info = database.stat()
    with _connect_read_only(database) as connection:
        has_meta = _table_exists(connection, "registry_meta")
        has_resources = _table_exists(connection, "resources")
        if not has_meta or not has_resources:
            return 0, {
                "state": "uninitialized",
                "size": int(info.st_size),
                "mtime_ns": int(info.st_mtime_ns),
            }
        rows = [
            dict(row)
            for row in connection.execute(
                "SELECT resource_id, relative_path, status, sha256, updated_at "
                "FROM resources ORDER BY resource_id"
            )
        ]
        metadata = {
            str(row["key"]): str(row["value"])
            for row in connection.execute(
                "SELECT key, value FROM registry_meta WHERE key IN "
                "('knowledge_root_id', 'generation', 'manifest_relative_path') ORDER BY key"
            )
        }
    return len(rows), {
        "state": "initialized",
        "resource_count": len(rows),
        "metadata": metadata,
        "resource_digest": hashlib.sha256(_json_bytes(rows)).hexdigest(),
    }


def registry_restore(
    knowledge_root: os.PathLike[str] | str,
    database_path: os.PathLike[str] | str,
    *,
    manifest_path: os.PathLike[str] | str | None = None,
    replace: bool = False,
    write: bool = False,
    expect_plan_sha256: str | None = None,
) -> dict[str, object]:
    """Rebuild SQLite from JSONL; never merge manifest edits into live data."""

    root = _absolute_directory(knowledge_root)
    marker_path = _root_marker_path(root)
    if manifest_path:
        candidate = Path(manifest_path).expanduser()
        candidate = candidate if candidate.is_absolute() else root / candidate
        try:
            lexical = Path(os.path.abspath(candidate))
            relative_manifest = lexical.relative_to(root).as_posix()
        except ValueError as exc:
            raise RegistrySafetyError(f"Recovery manifest must remain inside knowledge_root: {candidate}") from exc
        path = _safe_registered_path(root, relative_manifest, must_exist=True)
    else:
        path = _safe_registered_path(
            root, ".mpk/resources.jsonl", must_exist=True
        )
    manifest_bytes = path.read_bytes()
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    header, records = _read_manifest(path)
    restored_vault, restored_exclusions = _validate_restore_manifest(header, records)
    marker_id = validate_root_id(str(header.get("knowledge_root_id") or ""))
    marker_missing = not marker_path.is_file()
    if not marker_missing:
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RegistryError("Cannot read .mpk/root.json") from exc
        existing_marker_id = validate_root_id(str(marker.get("knowledge_root_id")))
        if existing_marker_id != marker_id:
            raise RegistrySafetyError("Manifest identity does not match .mpk/root.json")
    database = _require_external_database(root, database_path)
    current_count, database_snapshot = _existing_database_snapshot(database)
    if current_count and not replace:
        raise RegistrySafetyError("Refusing to merge recovery manifest into a populated database")
    actions = []
    if marker_missing:
        actions.append(
            {
                "action": "recreate_root_marker_from_manifest",
                "path": str(marker_path),
                "knowledge_root_id": marker_id,
            }
        )
    actions.append(
        {
            "action": "rebuild_registry_database",
            "path": str(database),
            "resource_count": len(records),
            "replace_existing": database.is_file(),
        }
    )
    plan = make_plan(
        "registry-restore",
        actions,
        knowledge_root_id=marker_id,
        knowledge_root_name=str(header.get("knowledge_root_name") or root.name),
        vault_relative_path=restored_vault,
        excluded_relative_paths=list(restored_exclusions),
        manifest_relative_path=str(
            header.get("manifest_relative_path")
            or path.relative_to(root).as_posix()
        ),
        manifest_generation=header.get("generation"),
        manifest_sha256=manifest_sha256,
        current_database=database_snapshot,
    )
    plan["restore_metadata"] = {
        "knowledge_root_id": marker_id,
        "knowledge_root_name": str(header.get("knowledge_root_name") or root.name),
        "vault_relative_path": restored_vault,
        "excluded_relative_paths": list(restored_exclusions),
        "manifest_relative_path": str(
            header.get("manifest_relative_path")
            or path.relative_to(root).as_posix()
        ),
    }
    if not write:
        return plan
    _confirm_plan(plan, expect_plan_sha256)
    database.parent.mkdir(parents=True, exist_ok=True)
    temporary = database.with_name(f".{database.name}.{uuid.uuid4().hex}.restore.tmp")
    backup: Path | None = None
    marker_created = False
    try:
        if marker_missing:
            _atomic_write_json(
                marker_path,
                {
                    "schema_version": 1,
                    "knowledge_root_id": marker_id,
                    "knowledge_root_name": str(header.get("knowledge_root_name") or root.name),
                },
            )
            marker_created = True
        with _connect(temporary, create=True) as connection:
            _create_schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM registry_meta")
            _meta_set(connection, "registry_schema_version", REGISTRY_SCHEMA_VERSION)
            _meta_set(connection, "knowledge_root_id", marker_id)
            _meta_set(connection, "knowledge_root_name", header.get("knowledge_root_name") or root.name)
            _meta_set(
                connection,
                "manifest_relative_path",
                header.get("manifest_relative_path")
                or (path.relative_to(root).as_posix() if path.is_relative_to(root) else ".mpk/resources.jsonl"),
            )
            _meta_set(connection, "vault_relative_path", restored_vault)
            _meta_set(
                connection,
                "excluded_relative_paths",
                json.dumps(list(restored_exclusions), ensure_ascii=False),
            )
            _meta_set(connection, "generation", int(header.get("generation", 0)))
            for record in records:
                identifier = validate_resource_id(str(record.get("resource_id")))
                relative = _normalise_relative_text(str(record.get("relative_path")))
                connection.execute(
                    "INSERT INTO resources(resource_id, relative_path, display_name, media_type, status, size, mtime_ns, sha256, hash_state, content_version, source_url, doi, isbn, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        identifier, relative, record.get("display_name") or PurePosixPath(relative).name,
                        record.get("media_type"), record.get("status", "active"), record.get("size"),
                        record.get("mtime_ns"), record.get("sha256"), record.get("hash_state", "unverified"),
                        int(record.get("content_version", 0)), record.get("source_url"), record.get("doi"),
                        record.get("isbn"), record.get("created_at") or _utc_now(), record.get("updated_at") or _utc_now(),
                    ),
                )
                for item in record.get("path_history") or []:
                    connection.execute(
                        "INSERT INTO path_history(resource_id, relative_path, valid_from, valid_to) VALUES (?, ?, ?, ?)",
                        (identifier, item["relative_path"], item["valid_from"], item.get("valid_to")),
                    )
                for item in record.get("versions") or []:
                    connection.execute(
                        "INSERT INTO resource_versions(resource_id, version, sha256, size, mtime_ns, observed_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (identifier, item["version"], item["sha256"], item["size"], item["mtime_ns"], item["observed_at"]),
                    )
            connection.commit()
        if database.exists():
            backup = database.with_name(f"{database.name}.restore-backup-{uuid.uuid4().hex}")
            os.replace(database, backup)
        try:
            os.replace(temporary, database)
        except BaseException:
            if backup is not None and backup.exists() and not database.exists():
                os.replace(backup, database)
            raise
    except BaseException:
        if marker_created:
            marker_path.unlink(missing_ok=True)
        raise
    finally:
        temporary.unlink(missing_ok=True)
    return _applied(
        plan,
        restored=len(records),
        generation=int(header.get("generation", 0)),
        knowledge_root_id=marker_id,
        knowledge_root_name=str(header.get("knowledge_root_name") or root.name),
        vault_relative_path=restored_vault,
        excluded_relative_paths=list(restored_exclusions),
        manifest_relative_path=str(
            header.get("manifest_relative_path")
            or path.relative_to(root).as_posix()
        ),
        replaced_database_backup=str(backup) if backup is not None else None,
    )


def replace_note_references(
    database_path: os.PathLike[str] | str,
    note_relative_path: str,
    note_sha256: str,
    references: Sequence[Mapping[str, object]],
    *,
    mtime_ns: int | None = None,
    scan_status: str = "ok",
) -> dict[str, object]:
    """Replace the rebuildable note-reference cache from final Markdown text."""

    note_path = _normalise_relative_text(note_relative_path)
    if not re.fullmatch(r"[0-9a-fA-F]{64}", note_sha256):
        raise RegistryError("note_sha256 must be a SHA-256 hex digest")
    normalised: list[dict[str, object]] = []
    for ordinal, item in enumerate(references):
        identifier = validate_resource_id(str(item.get("resource_id")))
        normalised.append(
            {
                "ordinal": ordinal,
                "resource_id": identifier,
                "target_uri": item.get("target_uri") or item.get("file_uri"),
                "line": item.get("line"),
            }
        )
    with _connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO note_documents(note_relative_path, note_sha256, mtime_ns, scan_status, scanned_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(note_relative_path) DO UPDATE SET note_sha256 = excluded.note_sha256, "
            "mtime_ns = excluded.mtime_ns, scan_status = excluded.scan_status, scanned_at = excluded.scanned_at",
            (note_path, note_sha256.lower(), mtime_ns, scan_status, _utc_now()),
        )
        connection.execute("DELETE FROM note_references WHERE note_relative_path = ?", (note_path,))
        connection.executemany(
            "INSERT INTO note_references(note_relative_path, ordinal, resource_id, target_uri, line) VALUES (?, ?, ?, ?, ?)",
            [(note_path, item["ordinal"], item["resource_id"], item["target_uri"], item["line"]) for item in normalised],
        )
        connection.commit()
    return {"note_relative_path": note_path, "reference_count": len(normalised), "scan_status": scan_status}


__all__ = [
    "DEFAULT_SOFT_EXCLUDED_DIRS",
    "DEFAULT_SOFT_EXCLUDED_FILES",
    "MANIFEST_SCHEMA_VERSION",
    "REGISTRY_SCHEMA_VERSION",
    "RegistryError",
    "RegistryGenerationError",
    "RegistryPlanError",
    "RegistrySafetyError",
    "classify_relative_path",
    "generate_resource_id",
    "generate_root_id",
    "make_plan",
    "manifest_state",
    "preview_inventory",
    "registry_export",
    "registry_hash",
    "registry_init",
    "registry_restore",
    "registry_scan",
    "registry_status",
    "replace_note_references",
    "resource_audit",
    "resource_register",
    "resource_resolve",
    "resource_retire",
    "resource_search",
    "validate_resource_id",
    "validate_root_id",
]
