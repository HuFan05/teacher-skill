"""Recoverable cross-component operations for the personal-knowledge root.

This module deliberately sits above :mod:`registry` and :mod:`config`.  It is
the only place where a resource path, the registry, its recovery manifest, and
managed Obsidian links are changed as one guarded operation.

Both public operations are dry-run by default.  A write is accepted only when
the caller supplies the SHA-256 of the freshly recomputed plan.  Tests can
inject the Obsidian batch runner and configuration writers; normal execution
uses ``MPK_OBSIDIAN_BATCH_EDITOR`` and atomic JSON writes.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing, contextmanager
from pathlib import Path, PurePosixPath
from typing import Callable, Iterator, Mapping, Sequence

from . import config as config_module
from . import library as library_module
from .references import parse_resource_references, rewrite_managed_reference_paths
from .registry import (
    RegistryError,
    RegistryGenerationError,
    RegistryPlanError,
    RegistrySafetyError,
    _absolute_directory,
    _connect,
    _export_now,
    _increment_generation,
    _is_reparse_point,
    _meta_get,
    _normalise_relative_text,
    _safe_registered_path,
    _utc_now,
    classify_relative_path,
    make_plan,
    manifest_state,
    validate_resource_id,
    validate_root_id,
)


BatchRunner = Callable[[Path, Path, bool], Mapping[str, object]]
ManageConfigWriter = Callable[[Mapping[str, object]], None]
ReceiverConfigWriter = Callable[[Path], None]
PostRelinkCheck = Callable[[], Mapping[str, object]]
PostMoveCheck = Callable[[], Mapping[str, object]]


class OperationError(RegistryError):
    """A coordinated operation could not be completed safely."""


class BatchEditError(OperationError):
    """The Obsidian transactional editor rejected or only partly wrote a plan."""

    def __init__(self, message: str, payload: Mapping[str, object] | None = None):
        super().__init__(message)
        self.payload = dict(payload or {})


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _atomic_write_bytes(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _atomic_write_json(path: Path, value: Mapping[str, object]) -> None:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode(
        "utf-8"
    ) + b"\n"
    _atomic_write_bytes(path, raw)


def _read_json_object(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OperationError(f"Cannot read {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise OperationError(f"{label} must contain a JSON object: {path}")
    return value


def _confirm_plan(plan: Mapping[str, object], expected: str | None) -> None:
    actual = str(plan.get("plan_sha256") or "")
    if not expected or not secrets.compare_digest(expected, actual):
        raise RegistryPlanError(
            "Write requires --expect-plan-sha256 matching the current dry-run plan"
        )


def _root_identity(root: Path, database_path: os.PathLike[str] | str) -> str:
    marker_path = _safe_registered_path(root, ".mpk/root.json", must_exist=True)
    if not marker_path.is_file() or _is_reparse_point(marker_path):
        raise RegistrySafetyError(f"Knowledge root marker is missing: {marker_path}")
    marker = _read_json_object(marker_path, "knowledge-root marker")
    marker_id = validate_root_id(str(marker.get("knowledge_root_id") or ""))
    with _connect(database_path) as connection:
        database_id = validate_root_id(str(_meta_get(connection, "knowledge_root_id") or ""))
    if marker_id != database_id:
        raise RegistrySafetyError(
            "Knowledge-root marker identity does not match the registry database"
        )
    return marker_id


def _require_synced_manifest(
    root: Path,
    database_path: os.PathLike[str] | str,
    manifest_path: os.PathLike[str] | str | None,
) -> Path:
    state = manifest_state(root, database_path, manifest_path=manifest_path)
    if state["state"] != "in_sync":
        raise RegistryGenerationError(
            "Registry write is blocked until registry-export repairs the manifest "
            f"({state['state']})"
        )
    return Path(str(state["manifest_path"]))


def _root_relative_target(root: Path, target: os.PathLike[str] | str) -> tuple[Path, str]:
    raw = Path(target).expanduser()
    candidate = raw if raw.is_absolute() else root / raw
    try:
        resolved = candidate.resolve(strict=False)
        relative = resolved.relative_to(root).as_posix()
    except (OSError, RuntimeError, ValueError) as exc:
        raise RegistrySafetyError(
            f"Move destination is outside the configured knowledge root: {candidate}"
        ) from exc
    relative = _normalise_relative_text(relative)
    # Existing parents are checked one by one.  A missing tail is allowed, but
    # no existing ancestor may be a symlink or another reparse point.
    current = root
    for part in PurePosixPath(relative).parts[:-1]:
        current = current / part
        if current.exists() and _is_reparse_point(current):
            raise RegistrySafetyError(
                f"Move destination traverses a reparse point: {current}"
            )
    return resolved, relative


def _operation_lock_path(database_path: os.PathLike[str] | str) -> Path:
    database = Path(database_path).expanduser().resolve(strict=False)
    return database.with_name(f"{database.name}.operations.lock")


@contextmanager
def _operation_lock(database_path: os.PathLike[str] | str) -> Iterator[None]:
    path = _operation_lock_path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise OperationError(
            f"Another registry operation is active; inspect the lock before retrying: {path}"
        ) from exc
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as stream:
            stream.write(str(os.getpid()))
            stream.flush()
        yield
    finally:
        path.unlink(missing_ok=True)


def _write_operation_log(
    database_path: os.PathLike[str] | str,
    operation_id: str,
    operation: str,
    plan_sha256: str,
    state: str,
    recovery: Mapping[str, object],
) -> None:
    now = _utc_now()
    with _connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO operation_log(operation_id, operation, plan_sha256, state, "
            "recovery_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(operation_id) DO UPDATE SET state = excluded.state, "
            "recovery_json = excluded.recovery_json, updated_at = excluded.updated_at",
            (
                operation_id,
                operation,
                plan_sha256,
                state,
                json.dumps(recovery, ensure_ascii=False, sort_keys=True),
                now,
                now,
            ),
        )
        connection.commit()


def _backup_database(database_path: os.PathLike[str] | str, backup_path: Path) -> None:
    database = Path(database_path).expanduser().resolve(strict=True)
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    # sqlite3.Connection's context manager commits/rolls back but does not
    # close the handle.  Explicit closing matters on Windows because an open
    # recovery database cannot be replaced, deleted, or cleaned up.
    with closing(sqlite3.connect(database)) as source, closing(
        sqlite3.connect(backup_path)
    ) as target:
        source.backup(target)
        target.commit()


def _restore_database(database_path: os.PathLike[str] | str, backup_path: Path) -> None:
    database = Path(database_path).expanduser().resolve(strict=False)
    temporary = database.with_name(f".{database.name}.restore.tmp")
    shutil.copy2(backup_path, temporary)
    for suffix in ("-wal", "-shm"):
        Path(f"{database}{suffix}").unlink(missing_ok=True)
    os.replace(temporary, database)


def _recovery_directory(
    database_path: os.PathLike[str] | str, operation_id: str
) -> Path:
    database = Path(database_path).expanduser().resolve(strict=False)
    return database.parent / "operation-recovery" / operation_id


def _normalise_batch_payload(payload: Mapping[str, object], *, write: bool) -> dict[str, object]:
    value = dict(payload)
    status = str(value.get("transaction_status") or "")
    ok = value.get("ok")
    accepted = status == ("applied" if write else "dry_run")
    if ok is False or not accepted:
        raise BatchEditError(
            f"Obsidian batch editor did not complete ({status or 'unknown status'})",
            value,
        )
    return value


def _subprocess_batch_runner(
    vault_root: Path, manifest_path: Path, write: bool
) -> Mapping[str, object]:
    configured = os.environ.get("MPK_OBSIDIAN_BATCH_EDITOR")
    if configured:
        editor = Path(configured).expanduser().resolve(strict=False)
    else:
        # Supported installations keep the two Skills as siblings in one
        # skills directory; the synthetic tests use the same layout.
        editor = (
            Path(__file__).resolve().parents[3]
            / "obsidian-vault-notes"
            / "scripts"
            / "vault_batch_edit.py"
        ).resolve(strict=False)
    if not editor.is_file():
        raise OperationError(
            "Managed note links require the sibling obsidian-vault-notes Skill; "
            "install it beside manage-personal-knowledge or set "
            f"MPK_OBSIDIAN_BATCH_EDITOR (looked for {editor})"
        )
    command = [
        sys.executable,
        str(editor),
        "--vault-root",
        str(vault_root),
        "--manifest",
        str(manifest_path),
    ]
    if write:
        command.append("--write")
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"},
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise BatchEditError(
            "Obsidian batch editor returned non-JSON output",
            {
                "returncode": completed.returncode,
                "stdout": completed.stdout[-2000:],
                "stderr": completed.stderr[-2000:],
            },
        ) from exc
    if not isinstance(payload, dict):
        raise BatchEditError("Obsidian batch editor returned a non-object payload")
    if completed.returncode != 0:
        raise BatchEditError(
            f"Obsidian batch editor exited with code {completed.returncode}", payload
        )
    return payload


def _run_batch(
    vault_root: Path,
    manifest: Mapping[str, object],
    *,
    write: bool,
    runner: BatchRunner | None,
    manifest_path: Path | None = None,
) -> dict[str, object]:
    if not manifest.get("operations"):
        return {
            "ok": True,
            "transaction_status": "applied" if write else "dry_run",
            "operation_count": 0,
            "results": [],
        }
    if manifest_path is not None:
        _atomic_write_json(manifest_path, manifest)
        payload = (runner or _subprocess_batch_runner)(vault_root, manifest_path, write)
        return _normalise_batch_payload(payload, write=write)
    with tempfile.TemporaryDirectory(prefix="mpk-batch-preview-") as temporary:
        path = Path(temporary) / "manifest.json"
        _atomic_write_json(path, manifest)
        payload = (runner or _subprocess_batch_runner)(vault_root, path, write)
    return _normalise_batch_payload(payload, write=write)


def _vault_and_exclusions(
    connection: sqlite3.Connection,
) -> tuple[str | None, tuple[str, ...]]:
    vault = (_meta_get(connection, "vault_relative_path", "") or "").strip()
    try:
        excluded_value = json.loads(
            _meta_get(connection, "excluded_relative_paths", "[]") or "[]"
        )
    except json.JSONDecodeError as exc:
        raise OperationError("Registry contains invalid exclusion metadata") from exc
    if not isinstance(excluded_value, list):
        raise OperationError("Registry exclusion metadata must be a JSON list")
    return (vault or None), tuple(str(item) for item in excluded_value)


def _decode_markdown(raw: bytes, path: Path) -> tuple[str, bool]:
    had_bom = raw.startswith(b"\xef\xbb\xbf")
    payload = raw[3:] if had_bom else raw
    try:
        return payload.decode("utf-8"), had_bom
    except UnicodeDecodeError as exc:
        raise OperationError(f"Vault note is not valid UTF-8: {path}: {exc}") from exc


class NoteBodyLimit(OperationError):
    def __init__(self, boundary: str):
        super().__init__("Reference body scan limit exceeded")
        self.boundary = boundary


def _stable_note_bytes(path: Path, *, max_bytes: int | None = None) -> tuple[bytes, os.stat_result]:
    """Read one note while detecting replacement or mutation during the read."""

    try:
        before = path.stat()
        if max_bytes is None:
            raw = path.read_bytes()
        else:
            if type(max_bytes) is not int or max_bytes < 0:
                raise ValueError("Invalid note byte limit")
            if before.st_size > max_bytes:
                raise NoteBodyLimit("body_bytes")
            with path.open("rb") as stream:
                raw = stream.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise NoteBodyLimit("body_bytes")
        after = path.stat()
    except OSError as exc:
        raise OperationError(f"Cannot read Vault note safely: {path}: {exc}") from exc
    before_identity = (
        before.st_size,
        before.st_mtime_ns,
        getattr(before, "st_ctime_ns", None),
        getattr(before, "st_ino", None),
    )
    after_identity = (
        after.st_size,
        after.st_mtime_ns,
        getattr(after, "st_ctime_ns", None),
        getattr(after, "st_ino", None),
    )
    if before_identity != after_identity or len(raw) != after.st_size:
        raise OperationError(f"Vault note changed while it was being read: {path}")
    return raw, after


class NoteScanLimit(OperationError):
    """Owned signal: the metadata scan stopped before a complete inventory."""


def _safe_markdown_paths(vault_root: Path, *, max_entries: int | None = None) -> tuple[list[Path], list[str]]:
    """Walk a Vault without entering any symlink, junction, or reparse point."""

    if max_entries is not None and (type(max_entries) is not int or max_entries < 1):
        raise ValueError("Invalid metadata scan limit")
    entries_seen = 0
    notes: list[Path] = []
    skipped: list[str] = []
    pending = [vault_root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as scanner:
                entries = []
                for entry in scanner:
                    entries_seen += 1
                    if max_entries is not None and entries_seen > max_entries:
                        raise NoteScanLimit("Vault metadata scan limit exceeded")
                    entries.append(entry)
                entries.sort(key=lambda item: item.name.casefold())
        except OSError as exc:
            raise OperationError(f"Cannot enumerate Vault directory: {directory}: {exc}") from exc
        child_directories: list[Path] = []
        for entry in entries:
            path = Path(entry.path)
            relative = path.relative_to(vault_root).as_posix()
            if _is_reparse_point(path, entry):
                skipped.append(relative)
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    child_directories.append(path)
                elif entry.is_file(follow_symlinks=False) and path.suffix.casefold() == ".md":
                    notes.append(path)
            except OSError as exc:
                raise OperationError(f"Cannot inspect Vault entry safely: {path}: {exc}") from exc
        pending.extend(reversed(child_directories))
    notes.sort(key=lambda item: item.relative_to(vault_root).as_posix().casefold())
    skipped.sort(key=str.casefold)
    return notes, skipped


def _cached_note_path_state(vault_root: Path, note_relative: str) -> tuple[str, Path]:
    """Classify a cached path lexically, without following an unsafe component."""

    relative = _normalise_relative_text(note_relative)
    current = vault_root
    parts = PurePosixPath(relative).parts
    for index, part in enumerate(parts):
        current = current / part
        if current.is_symlink():
            return "unsafe", current
        if not current.exists():
            return "missing", current
        if _is_reparse_point(current):
            return "unsafe", current
        if index < len(parts) - 1 and not current.is_dir():
            return "missing", current
    return ("file" if current.is_file() else "missing"), current


def _cacheable_references(
    text: str,
    *,
    active_resource_ids: set[str],
    knowledge_root: Path,
) -> tuple[list[dict[str, object]], str]:
    """Return the complete cacheable reference set parsed from one Markdown body."""

    parsed = parse_resource_references(text, knowledge_root=knowledge_root)
    references: list[dict[str, object]] = []
    diagnostic = bool(parsed["invalid_markers"] or parsed["unmanaged_path_references"])
    for raw in parsed["references"]:
        if not raw.get("valid_id"):
            diagnostic = True
            continue
        identifier = str(raw["resource_id"])
        if identifier not in active_resource_ids:
            diagnostic = True
            continue
        references.append(dict(raw))
    return references, "diagnostic" if diagnostic else "ok"


def _reference_signature(
    references: Sequence[Mapping[str, object]],
) -> tuple[tuple[str, str, int | None], ...]:
    return tuple(
        (
            str(item.get("resource_id") or ""),
            str(item.get("target_uri") or ""),
            int(item["line"]) if item.get("line") is not None else None,
        )
        for item in references
    )


def _expected_rewritten_sha256(
    text: str,
    *,
    had_bom: bool,
    uri_by_resource_id: Mapping[str, str],
) -> str:
    rewritten = rewrite_managed_reference_paths(text, uri_by_resource_id)
    raw = str(rewritten["text"]).encode("utf-8")
    if had_bom:
        raw = b"\xef\xbb\xbf" + raw
    return hashlib.sha256(raw).hexdigest()


def _note_state_for_ids(
    connection: sqlite3.Connection,
    knowledge_root: Path,
    vault_root: Path,
    uri_by_resource_id: Mapping[str, str],
) -> tuple[
    dict[str, object],
    list[dict[str, object]],
    dict[str, object],
]:
    """Build note edits and an in-memory incremental cache refresh from Markdown."""

    target_ids = set(uri_by_resource_id)
    active_resource_ids = {
        str(row["resource_id"])
        for row in connection.execute(
            "SELECT resource_id FROM resources WHERE status = 'active'"
        )
    }
    documents = {
        str(row["note_relative_path"]): {
            "sha256": str(row["note_sha256"]).lower(),
            "mtime_ns": row["mtime_ns"],
        }
        for row in connection.execute(
            "SELECT note_relative_path, note_sha256, mtime_ns FROM note_documents"
        )
    }
    cached_references: dict[str, list[dict[str, object]]] = {}
    for row in connection.execute(
        "SELECT note_relative_path, resource_id, target_uri, line "
        "FROM note_references ORDER BY note_relative_path, ordinal"
    ):
        cached_references.setdefault(str(row["note_relative_path"]), []).append(
            {
                "resource_id": str(row["resource_id"]),
                "target_uri": row["target_uri"],
                "line": row["line"],
            }
        )

    paths, skipped_points = _safe_markdown_paths(vault_root)
    seen: set[str] = set()
    parsed_notes: dict[str, dict[str, object]] = {}
    changed_notes: set[str] = set()
    for note_path in paths:
        note_relative = note_path.relative_to(vault_root).as_posix()
        seen.add(note_relative)
        raw, info = _stable_note_bytes(note_path)
        digest = hashlib.sha256(raw).hexdigest()
        cached_document = documents.get(note_relative)
        cached_items = cached_references.get(note_relative, [])
        cached_target_ids = {
            str(item["resource_id"])
            for item in cached_items
            if str(item["resource_id"]) in target_ids
        }
        current_mtime = int(info.st_mtime_ns)
        changed = (
            cached_document is None
            or str(cached_document["sha256"]) != digest
            or cached_document["mtime_ns"] != current_mtime
        )
        if not changed and not cached_target_ids:
            continue
        text, had_bom = _decode_markdown(raw, note_path)
        references, scan_status = _cacheable_references(
            text,
            active_resource_ids=active_resource_ids,
            knowledge_root=knowledge_root,
        )
        cache_disagrees = _reference_signature(cached_items) != _reference_signature(
            references
        )
        if changed or cache_disagrees:
            changed_notes.add(note_relative)
        parsed_notes[note_relative] = {
            "note_relative_path": note_relative,
            "path": note_path,
            "pre_sha256": digest,
            "mtime_ns": current_mtime,
            "text": text,
            "had_bom": had_bom,
            "references": references,
            "scan_status": scan_status,
        }

    deleted_notes: list[dict[str, object]] = []
    skipped_cached: list[str] = []
    for note_relative, cached_document in sorted(documents.items()):
        if note_relative in seen:
            continue
        state, path = _cached_note_path_state(vault_root, note_relative)
        if state == "unsafe":
            skipped_cached.append(note_relative)
            continue
        if state == "file":
            raise OperationError(
                "Vault changed while its Markdown files were enumerated: "
                f"{note_relative}"
            )
        deleted_notes.append(
            {
                "note_relative_path": note_relative,
                "cached_sha256": str(cached_document["sha256"]),
            }
        )

    operations: list[dict[str, object]] = []
    preconditions: list[dict[str, object]] = []
    refresh_notes: dict[str, dict[str, object]] = {
        relative: parsed_notes[relative] for relative in changed_notes
    }
    for note_relative, snapshot in sorted(parsed_notes.items()):
        identifiers = sorted(
            {
                str(item["resource_id"])
                for item in snapshot["references"]
                if str(item["resource_id"]) in target_ids
            }
        )
        if not identifiers:
            continue
        refresh_notes[note_relative] = snapshot
        for identifier in identifiers:
            expected = str(snapshot["pre_sha256"])
            preconditions.append(
                {
                    "note_relative_path": note_relative,
                    "resource_id": identifier,
                    "expected_sha256": expected,
                }
            )
            operations.append(
                {
                    "file": note_relative,
                    "operation": "replace-managed-resource-uri",
                    "resource_id": identifier,
                    "new_uri": uri_by_resource_id[identifier],
                    "expected_sha256": expected,
                }
            )

    refresh_preconditions: list[dict[str, object]] = []
    for note_relative, snapshot in sorted(refresh_notes.items()):
        expected_final = _expected_rewritten_sha256(
            str(snapshot["text"]),
            had_bom=bool(snapshot["had_bom"]),
            uri_by_resource_id=uri_by_resource_id,
        )
        snapshot["expected_final_sha256"] = expected_final
        refresh_preconditions.append(
            {
                "note_relative_path": note_relative,
                "expected_sha256": snapshot["pre_sha256"],
                "expected_final_sha256": expected_final,
                "reference_count": len(snapshot["references"]),
            }
        )

    skipped = sorted(set(skipped_points) | set(skipped_cached), key=str.casefold)
    summary = {
        "scanned_markdown_notes": len(paths),
        "changed_notes": len(changed_notes),
        "refresh_notes": len(refresh_notes),
        "deleted_notes": len(deleted_notes),
        "skipped_reparse_points": len(skipped),
    }
    refresh_state: dict[str, object] = {
        "notes": [refresh_notes[key] for key in sorted(refresh_notes)],
        "deleted_notes": deleted_notes,
        "preconditions": refresh_preconditions,
        "summary": summary,
    }
    return {"version": 2, "operations": operations}, preconditions, refresh_state


def _update_note_reference_cache(
    database_path: os.PathLike[str] | str,
    knowledge_root: Path,
    vault_root: Path,
    refresh_state: Mapping[str, object],
) -> None:
    """Replace each refreshed note's cache from its verified final disk body."""

    raw_notes = refresh_state.get("notes")
    raw_deleted = refresh_state.get("deleted_notes")
    if not isinstance(raw_notes, Sequence) or not isinstance(raw_deleted, Sequence):
        raise OperationError("Reference refresh state is malformed")
    if not raw_notes and not raw_deleted:
        return
    with _connect(database_path) as connection:
        active_resource_ids = {
            str(row["resource_id"])
            for row in connection.execute(
                "SELECT resource_id FROM resources WHERE status = 'active'"
            )
        }

    final_notes: list[dict[str, object]] = []
    for raw_snapshot in raw_notes:
        if not isinstance(raw_snapshot, Mapping):
            raise OperationError("Reference refresh contains a non-object note")
        note_relative = _normalise_relative_text(
            str(raw_snapshot.get("note_relative_path") or "")
        )
        state, note_path = _cached_note_path_state(vault_root, note_relative)
        if state != "file":
            raise OperationError(
                "A refreshed note changed location or became unsafe during the operation: "
                f"{note_relative}"
            )
        raw, info = _stable_note_bytes(note_path)
        digest = hashlib.sha256(raw).hexdigest()
        expected = str(raw_snapshot.get("expected_final_sha256") or "").lower()
        if not expected or digest != expected:
            raise OperationError(
                "A Vault note changed concurrently after the accepted plan was built: "
                f"{note_relative}"
            )
        text, _ = _decode_markdown(raw, note_path)
        references, scan_status = _cacheable_references(
            text,
            active_resource_ids=active_resource_ids,
            knowledge_root=knowledge_root,
        )
        final_notes.append(
            {
                "note_relative_path": note_relative,
                "sha256": digest,
                "mtime_ns": int(info.st_mtime_ns),
                "scan_status": scan_status,
                "references": references,
            }
        )

    deleted_notes: list[str] = []
    for raw_deleted_note in raw_deleted:
        if not isinstance(raw_deleted_note, Mapping):
            raise OperationError("Reference refresh contains a non-object deletion")
        note_relative = _normalise_relative_text(
            str(raw_deleted_note.get("note_relative_path") or "")
        )
        state, _ = _cached_note_path_state(vault_root, note_relative)
        if state != "missing":
            raise OperationError(
                "A deleted Vault note reappeared or became unsafe during the operation: "
                f"{note_relative}"
            )
        deleted_notes.append(note_relative)

    scanned_at = _utc_now()
    with _connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        for note_relative in deleted_notes:
            connection.execute(
                "DELETE FROM note_references WHERE note_relative_path = ?",
                (note_relative,),
            )
            connection.execute(
                "DELETE FROM note_documents WHERE note_relative_path = ?",
                (note_relative,),
            )
        for final in final_notes:
            note_relative = str(final["note_relative_path"])
            connection.execute(
                "INSERT INTO note_documents(note_relative_path, note_sha256, mtime_ns, "
                "scan_status, scanned_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(note_relative_path) DO UPDATE SET "
                "note_sha256 = excluded.note_sha256, mtime_ns = excluded.mtime_ns, "
                "scan_status = excluded.scan_status, scanned_at = excluded.scanned_at",
                (
                    note_relative,
                    final["sha256"],
                    final["mtime_ns"],
                    final["scan_status"],
                    scanned_at,
                ),
            )
            connection.execute(
                "DELETE FROM note_references WHERE note_relative_path = ?",
                (note_relative,),
            )
            rows: list[tuple[object, ...]] = []
            for ordinal, reference in enumerate(final["references"]):
                identifier = validate_resource_id(str(reference["resource_id"]))
                rows.append(
                    (
                        note_relative,
                        ordinal,
                        identifier,
                        reference.get("target_uri"),
                        reference.get("line"),
                    )
                )
            connection.executemany(
                "INSERT INTO note_references(note_relative_path, ordinal, resource_id, "
                "target_uri, line) VALUES (?, ?, ?, ?, ?)",
                rows,
            )
        # Recheck while the cache transaction is still uncommitted.  A human
        # edit after the first parse must not leave a newly committed stale
        # cache behind.
        for final in final_notes:
            note_relative = str(final["note_relative_path"])
            state, note_path = _cached_note_path_state(vault_root, note_relative)
            if state != "file" or _sha256_file(note_path) != final["sha256"]:
                raise OperationError(
                    "A Vault note changed concurrently during cache replacement: "
                    f"{note_relative}"
                )
        for note_relative in deleted_notes:
            state, _ = _cached_note_path_state(vault_root, note_relative)
            if state != "missing":
                raise OperationError(
                    "A deleted Vault note changed concurrently during cache replacement: "
                    f"{note_relative}"
                )
        connection.commit()


def _note_refresh_has_changes(refresh_state: Mapping[str, object]) -> bool:
    summary = refresh_state.get("summary")
    if not isinstance(summary, Mapping):
        raise OperationError("Reference refresh summary is malformed")
    return bool(summary.get("refresh_notes") or summary.get("deleted_notes"))


def _expected_refresh_hashes(
    refresh_state: Mapping[str, object],
) -> dict[str, str]:
    raw_notes = refresh_state.get("notes")
    if not isinstance(raw_notes, Sequence):
        raise OperationError("Reference refresh note list is malformed")
    expected: dict[str, str] = {}
    for raw in raw_notes:
        if not isinstance(raw, Mapping):
            raise OperationError("Reference refresh contains a non-object note")
        relative = _normalise_relative_text(
            str(raw.get("note_relative_path") or "")
        )
        digest = str(raw.get("expected_final_sha256") or "").lower()
        if not digest:
            raise OperationError(f"Reference refresh has no final hash: {relative}")
        expected[relative] = digest
    return expected


def _reverse_note_manifest(
    vault_root: Path,
    forward_manifest: Mapping[str, object],
    old_uri_by_resource_id: Mapping[str, str],
    *,
    expected_current_sha256_by_note: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Build a hash-guarded inverse from the notes currently on disk."""

    raw_operations = forward_manifest.get("operations")
    if not isinstance(raw_operations, Sequence):
        raise OperationError("Forward batch manifest has no operations list")
    note_hashes: dict[str, str] = {}
    reverse: list[dict[str, object]] = []
    for raw in raw_operations:
        if not isinstance(raw, Mapping):
            raise OperationError("Forward batch manifest contains a non-object operation")
        note_relative = _normalise_relative_text(str(raw.get("file") or ""))
        if note_relative not in note_hashes:
            state, note_path = _cached_note_path_state(vault_root, note_relative)
            if state != "file":
                raise OperationError(
                    f"Cannot build note rollback; note is missing or unsafe: {note_relative}"
                )
            current_digest = _sha256_file(note_path)
            expected_digest = (
                expected_current_sha256_by_note.get(note_relative)
                if expected_current_sha256_by_note is not None
                else None
            )
            if expected_digest is not None and current_digest != expected_digest:
                raise OperationError(
                    "Cannot roll back note links over a concurrent human edit: "
                    f"{note_relative}"
                )
            note_hashes[note_relative] = current_digest
        identifier = validate_resource_id(str(raw.get("resource_id") or ""))
        old_uri = old_uri_by_resource_id.get(identifier)
        if old_uri is None or not old_uri.casefold().startswith("file:///"):
            raise OperationError(
                f"Cannot build note rollback URI for managed resource {identifier}"
            )
        reverse.append(
            {
                "file": note_relative,
                "operation": "replace-managed-resource-uri",
                "resource_id": identifier,
                "new_uri": old_uri,
                "expected_sha256": note_hashes[note_relative],
            }
        )
    return {"version": 2, "operations": reverse}


def _resource_move_preview(
    knowledge_root: os.PathLike[str] | str,
    database_path: os.PathLike[str] | str,
    resource_id: str,
    to: os.PathLike[str] | str,
    *,
    vault_root: os.PathLike[str] | str | None,
    manifest_path: os.PathLike[str] | str | None,
    batch_runner: BatchRunner | None,
    library_database_path: os.PathLike[str] | str | None,
    library_relative_path: str | None,
    post_move_check_required: bool,
) -> tuple[dict[str, object], dict[str, object]]:
    root = _absolute_directory(knowledge_root)
    root_id = _root_identity(root, database_path)
    portable_manifest = _require_synced_manifest(root, database_path, manifest_path)
    identifier = validate_resource_id(resource_id)
    target, target_relative = _root_relative_target(root, to)
    with _connect(database_path) as connection:
        row = connection.execute(
            "SELECT * FROM resources WHERE resource_id = ?", (identifier,)
        ).fetchone()
        if row is None:
            raise RegistryError(f"Unknown resource_id: {identifier}")
        if row["status"] != "active":
            raise RegistrySafetyError(
                f"Only an active resource can be moved: {identifier} ({row['status']})"
            )
        source_relative = str(row["relative_path"])
        source = _safe_registered_path(root, source_relative, must_exist=True)
        if not source.is_file() or _is_reparse_point(source):
            raise RegistrySafetyError(f"Resource source is missing or unsafe: {source}")
        if source == target:
            no_change = True
        else:
            no_change = False
            if target.exists():
                raise RegistrySafetyError(f"Move destination already exists: {target}")
            collision = connection.execute(
                "SELECT resource_id FROM resources WHERE relative_path = ?",
                (target_relative,),
            ).fetchone()
            if collision is not None:
                raise RegistrySafetyError(
                    "Move destination is already registered to "
                    f"{collision['resource_id']}: {target_relative}"
                )
            vault_relative, exclusions = _vault_and_exclusions(connection)
            classification = classify_relative_path(
                target_relative,
                vault_relative_path=vault_relative,
                excluded_relative_paths=exclusions,
            )
            if not classification["included"]:
                raise RegistrySafetyError(
                    f"Move destination is excluded ({classification['reason']}): {target_relative}"
                )
        source_digest = _sha256_file(source)
        stored_digest = row["sha256"]
        if stored_digest and str(stored_digest).lower() != source_digest:
            raise RegistrySafetyError(
                "Resource content no longer matches its registered SHA-256; hash/version "
                f"refresh is required before moving {identifier}"
            )
        configured_vault = vault_root
        if configured_vault is None:
            vault_relative, _ = _vault_and_exclusions(connection)
            if vault_relative:
                configured_vault = root.joinpath(*PurePosixPath(vault_relative).parts)
        vault = (
            Path(configured_vault).expanduser().resolve(strict=False)
            if configured_vault is not None
            else None
        )
        uri_map = {} if no_change else {identifier: target.as_uri()}
        if vault is None:
            referenced = connection.execute(
                "SELECT 1 FROM note_references WHERE resource_id = ? LIMIT 1",
                (identifier,),
            ).fetchone()
            if referenced:
                raise OperationError(
                    "The resource has note references but no configured Vault path"
                )
            note_manifest, note_preconditions = {"version": 2, "operations": []}, []
            note_refresh: dict[str, object] = {
                "notes": [],
                "deleted_notes": [],
                "preconditions": [],
                "summary": {
                    "scanned_markdown_notes": 0,
                    "changed_notes": 0,
                    "refresh_notes": 0,
                    "deleted_notes": 0,
                    "skipped_reparse_points": 0,
                },
            }
        else:
            if not vault.is_dir() or not (vault / ".obsidian").is_dir():
                raise RegistrySafetyError(f"Configured Vault is missing or invalid: {vault}")
            note_manifest, note_preconditions, note_refresh = _note_state_for_ids(
                connection, root, vault, uri_map
            )
    if (library_database_path is None) != (library_relative_path is None):
        raise OperationError(
            "library_database_path and library_relative_path must be supplied together"
        )
    pdf_index: dict[str, object]
    library_database: Path | None = None
    library_root: Path | None = None
    if library_database_path is None:
        pdf_index = {
            "database_exists": False,
            "action": "not_configured",
            "changes_required": False,
        }
    else:
        library_database = Path(library_database_path).expanduser().resolve(strict=False)
        assert library_relative_path is not None
        library_root = root.joinpath(*PurePosixPath(library_relative_path).parts)
        pdf_index = library_module.plan_indexed_pdf_move(
            library_database,
            library_root,
            source_relative,
            target_relative,
            knowledge_root_id=root_id,
            library_relative_path=library_relative_path,
        )

    actions: list[dict[str, object]]
    if no_change:
        actions = [
            {
                "action": "already_at_destination",
                "resource_id": identifier,
                "relative_path": source_relative,
            }
        ]
    else:
        actions = [
            {
                "action": "move_file",
                "resource_id": identifier,
                "from": source_relative,
                "to": target_relative,
            },
            {
                "action": "update_registry_path_and_history",
                "resource_id": identifier,
            },
            {
                "action": "export_recovery_manifest",
                "path": str(portable_manifest),
            },
            {
                "action": "rewrite_managed_note_links",
                "operation_count": len(note_manifest["operations"]),
            },
        ]
        if pdf_index.get("changes_required"):
            actions.insert(
                3,
                {
                    "action": "update_pdf_index_without_text_extraction",
                    "index_action": pdf_index.get("action"),
                    "old_library_path": pdf_index.get("old_library_path"),
                    "new_library_path": pdf_index.get("new_library_path"),
                },
            )
    refresh_summary = dict(note_refresh["summary"])
    if refresh_summary["refresh_notes"] or refresh_summary["deleted_notes"]:
        actions.append(
            {
                "action": "refresh_note_reference_cache",
                "note_count": refresh_summary["refresh_notes"],
                "deleted_note_count": refresh_summary["deleted_notes"],
            }
        )
    if post_move_check_required:
        actions.append(
            {
                "action": "refresh_and_verify_indexes_after_move",
                "mode": "incremental",
            }
        )
    plan = make_plan(
        "resource-move",
        actions,
        knowledge_root_id=root_id,
        resource_id=identifier,
        source_sha256=source_digest,
        note_preconditions=note_preconditions,
        note_refresh_preconditions=note_refresh["preconditions"],
        deleted_note_preconditions=note_refresh["deleted_notes"],
        reference_refresh=refresh_summary,
        new_file_uri=target.as_uri(),
        pdf_index=pdf_index,
        post_move_check_required=post_move_check_required,
    )
    # The installed batch guard resolves every candidate URI through the live
    # registry.  During dry-run the registry still truthfully points at the
    # source URI, so asking it to approve the future URI would always fail.
    # The exact note set, original/final hashes, and ID-scoped rewrites above
    # remain part of the plan hash.  The full external guard runs after the
    # registry/PDF transition and immediately before any note write.
    plan["batch_guard_preflight"] = (
        "deferred_until_registry_transition"
        if note_manifest["operations"]
        else "not_needed"
    )
    context = {
        "root": root,
        "root_id": root_id,
        "source": source,
        "source_relative": source_relative,
        "target": target,
        "target_relative": target_relative,
        "source_sha256": source_digest,
        "old_file_uri": source.as_uri(),
        "portable_manifest": portable_manifest,
        "vault": vault,
        "note_manifest": note_manifest,
        "note_refresh": note_refresh,
        "no_change": no_change,
        "library_database": library_database,
        "library_root": library_root,
        "library_relative_path": library_relative_path,
        "pdf_index": pdf_index,
    }
    return plan, context


def resource_move(
    knowledge_root: os.PathLike[str] | str,
    database_path: os.PathLike[str] | str,
    resource_id: str,
    to: os.PathLike[str] | str,
    *,
    vault_root: os.PathLike[str] | str | None = None,
    manifest_path: os.PathLike[str] | str | None = None,
    batch_runner: BatchRunner | None = None,
    library_database_path: os.PathLike[str] | str | None = None,
    library_relative_path: str | None = None,
    post_move_check: PostMoveCheck | None = None,
    write: bool = False,
    expect_plan_sha256: str | None = None,
) -> dict[str, object]:
    """Move or rename one registered file and update every managed note URI.

    Failures after the first write return a structured ``rolled_back`` or
    ``partial_write`` result.  Preflight and safety failures raise without
    changing any persistent state.
    """

    plan, context = _resource_move_preview(
        knowledge_root,
        database_path,
        resource_id,
        to,
        vault_root=vault_root,
        manifest_path=manifest_path,
        batch_runner=batch_runner,
        library_database_path=library_database_path,
        library_relative_path=library_relative_path,
        post_move_check_required=post_move_check is not None,
    )
    if not write:
        return plan
    _confirm_plan(plan, expect_plan_sha256)
    if (
        context["no_change"]
        and not _note_refresh_has_changes(context["note_refresh"])
        and post_move_check is None
    ):
        return {**plan, "dry_run": False, "applied": True, "changed": False}

    with _operation_lock(database_path):
        # Rebuild the plan under the lock so a note, file, or registry change
        # cannot be hidden behind a previously accepted hash.
        plan, context = _resource_move_preview(
            knowledge_root,
            database_path,
            resource_id,
            to,
            vault_root=vault_root,
            manifest_path=manifest_path,
            batch_runner=batch_runner,
            library_database_path=library_database_path,
            library_relative_path=library_relative_path,
            post_move_check_required=post_move_check is not None,
        )
        _confirm_plan(plan, expect_plan_sha256)
        if context["no_change"]:
            vault = context["vault"]
            if vault is not None:
                _update_note_reference_cache(
                    database_path,
                    Path(context["root"]),
                    Path(vault),
                    context["note_refresh"],
                )
            post_check_payload = (
                dict(post_move_check()) if post_move_check is not None else None
            )
            if (
                post_check_payload is not None
                and post_check_payload.get("ok") is not True
            ):
                return {
                    **plan,
                    "dry_run": False,
                    "applied": False,
                    "changed": _note_refresh_has_changes(context["note_refresh"]),
                    "state": "partial_write",
                    "post_move_check": post_check_payload,
                    "error": "The required index refresh or verification failed",
                }
            return {
                **plan,
                "dry_run": False,
                "applied": True,
                "changed": (
                    _note_refresh_has_changes(context["note_refresh"])
                    or post_check_payload is not None
                ),
                "state": "applied",
                "post_move_check": post_check_payload,
            }
        operation_id = f"resource-move-{plan['plan_sha256'][:24]}"
        recovery_dir = _recovery_directory(database_path, operation_id)
        recovery_dir.mkdir(parents=True, exist_ok=True)
        database_backup = recovery_dir / "registry-before.sqlite3"
        library_backup = recovery_dir / "pdf-index-before.sqlite3"
        manifest_backup = recovery_dir / "resources-before.jsonl"
        batch_manifest_path = recovery_dir / "vault-forward.json"
        reverse_manifest_path = recovery_dir / "vault-reverse.json"
        recovery_path = recovery_dir / "recovery.json"
        recovery: dict[str, object] = {
            "operation_id": operation_id,
            "operation": "resource-move",
            "plan_sha256": plan["plan_sha256"],
            "state": "prepared",
            "source": str(context["source"]),
            "target": str(context["target"]),
            "database_backup": str(database_backup),
            "pdf_index_backup": str(library_backup),
            "manifest_backup": str(manifest_backup),
            "batch_manifest": str(batch_manifest_path),
            "reverse_batch_manifest": str(reverse_manifest_path),
        }
        _write_operation_log(
            database_path,
            operation_id,
            "resource-move",
            str(plan["plan_sha256"]),
            "prepared",
            recovery,
        )
        _backup_database(database_path, database_backup)
        if context["pdf_index"].get("changes_required"):
            assert context["library_database"] is not None
            _backup_database(Path(context["library_database"]), library_backup)
        shutil.copy2(Path(context["portable_manifest"]), manifest_backup)
        _atomic_write_json(recovery_path, recovery)

        file_moved = False
        database_changed = False
        pdf_index_changed = False
        pdf_index_update: dict[str, object] | None = None
        batch_payload: dict[str, object] | None = None
        post_check_payload: dict[str, object] | None = None
        try:
            target = Path(context["target"])
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(Path(context["source"]), target)
            file_moved = True
            moved_digest = _sha256_file(target)
            if moved_digest != context["source_sha256"]:
                raise OperationError(
                    "Resource content changed after the accepted move plan was built"
                )
            target_info = target.stat()
            with _connect(database_path) as connection:
                connection.execute("BEGIN IMMEDIATE")
                current = connection.execute(
                    "SELECT * FROM resources WHERE resource_id = ?",
                    (resource_id,),
                ).fetchone()
                if current is None or current["relative_path"] != context["source_relative"]:
                    raise OperationError("Registry resource path changed during resource-move")
                now = _utc_now()
                connection.execute(
                    "UPDATE path_history SET valid_to = ? WHERE resource_id = ? AND valid_to IS NULL",
                    (now, resource_id),
                )
                connection.execute(
                    "INSERT INTO path_history(resource_id, relative_path, valid_from, valid_to) "
                    "VALUES (?, ?, ?, NULL)",
                    (resource_id, context["target_relative"], now),
                )
                content_version = int(current["content_version"] or 0)
                if not current["sha256"] or content_version == 0:
                    content_version = 1
                    existing_version = connection.execute(
                        "SELECT sha256 FROM resource_versions WHERE resource_id = ? AND version = 1",
                        (resource_id,),
                    ).fetchone()
                    if existing_version is None:
                        connection.execute(
                            "INSERT INTO resource_versions(resource_id, version, sha256, size, "
                            "mtime_ns, observed_at) VALUES (?, 1, ?, ?, ?, ?)",
                            (
                                resource_id,
                                context["source_sha256"],
                                int(target_info.st_size),
                                int(target_info.st_mtime_ns),
                                now,
                            ),
                        )
                    elif str(existing_version["sha256"]).casefold() != str(
                        context["source_sha256"]
                    ).casefold():
                        raise OperationError(
                            "Resource version history conflicts with the moved file digest"
                        )
                connection.execute(
                    "UPDATE resources SET relative_path = ?, display_name = ?, status = 'active', "
                    "size = ?, mtime_ns = ?, sha256 = ?, hash_state = 'verified', "
                    "content_version = ?, updated_at = ? "
                    "WHERE resource_id = ?",
                    (
                        context["target_relative"],
                        target.name,
                        int(target_info.st_size),
                        int(target_info.st_mtime_ns),
                        context["source_sha256"],
                        content_version,
                        now,
                        resource_id,
                    ),
                )
                generation = _increment_generation(connection)
                connection.commit()
            database_changed = True
            _export_now(
                Path(context["root"]), database_path, manifest_path=manifest_path
            )
            if context["pdf_index"].get("changes_required"):
                assert context["library_database"] is not None
                assert context["library_root"] is not None
                assert context["library_relative_path"] is not None
                pdf_index_update = library_module.apply_indexed_pdf_move(
                    Path(context["library_database"]),
                    Path(context["library_root"]),
                    str(context["source_relative"]),
                    str(context["target_relative"]),
                    knowledge_root_id=str(context["root_id"]),
                    library_relative_path=str(context["library_relative_path"]),
                    expected_plan=context["pdf_index"],
                )
                pdf_index_changed = bool(pdf_index_update.get("changed"))
            vault = context["vault"]
            if vault is not None:
                batch_payload = _run_batch(
                    Path(vault),
                    context["note_manifest"],
                    write=True,
                    runner=batch_runner,
                    manifest_path=batch_manifest_path,
                )
                _update_note_reference_cache(
                    database_path,
                    Path(context["root"]),
                    Path(vault),
                    context["note_refresh"],
                )
            if post_move_check is not None:
                post_check_payload = dict(post_move_check())
                if post_check_payload.get("ok") is not True:
                    raise OperationError(
                        "The required post-move index refresh or verification failed"
                    )
            recovery.update(
                {
                    "state": "applied",
                    "generation": generation,
                    "batch_status": (batch_payload or {}).get(
                        "transaction_status", "not_needed"
                    ),
                    "pdf_index_update": pdf_index_update,
                    "post_move_check": post_check_payload,
                }
            )
            _atomic_write_json(recovery_path, recovery)
            _write_operation_log(
                database_path,
                operation_id,
                "resource-move",
                str(plan["plan_sha256"]),
                "applied",
                recovery,
            )
            database_backup.unlink(missing_ok=True)
            library_backup.unlink(missing_ok=True)
            manifest_backup.unlink(missing_ok=True)
            return {
                **plan,
                "dry_run": False,
                "applied": True,
                "changed": True,
                "state": "applied",
                "operation_id": operation_id,
                "generation": generation,
                "relative_path": context["target_relative"],
                "file_uri": Path(context["target"]).as_uri(),
                "pdf_index_update": pdf_index_update or context["pdf_index"],
                "post_move_check": post_check_payload,
                "recovery_log": str(recovery_path),
            }
        except BaseException as exc:
            rollback_errors: list[str] = []
            batch_status = (
                str(exc.payload.get("transaction_status") or "")
                if isinstance(exc, BatchEditError)
                else ""
            )
            batch_was_applied = bool(
                batch_payload
                and batch_payload.get("transaction_status") == "applied"
            )
            # Restore the resource truth first.  The Obsidian guard resolves
            # the requested URI against the registry, so an inverse link batch
            # is valid only after the registry, manifest, PDF index, and file
            # all point at the old URI again.
            if database_changed or database_backup.is_file():
                try:
                    _restore_database(database_path, database_backup)
                except BaseException as rollback_exc:
                    rollback_errors.append(f"database: {rollback_exc}")
            if pdf_index_changed or library_backup.is_file():
                try:
                    assert context["library_database"] is not None
                    _restore_database(
                        Path(context["library_database"]), library_backup
                    )
                except BaseException as rollback_exc:
                    rollback_errors.append(f"pdf_index: {rollback_exc}")
            if manifest_backup.is_file():
                try:
                    _atomic_write_bytes(
                        Path(context["portable_manifest"]), manifest_backup.read_bytes()
                    )
                except BaseException as rollback_exc:
                    rollback_errors.append(f"manifest: {rollback_exc}")
            if file_moved:
                try:
                    source = Path(context["source"])
                    target = Path(context["target"])
                    source.parent.mkdir(parents=True, exist_ok=True)
                    if source.exists():
                        raise OperationError(f"Rollback source is occupied: {source}")
                    os.replace(target, source)
                except BaseException as rollback_exc:
                    rollback_errors.append(f"file: {rollback_exc}")
            if (
                batch_was_applied
                and context["vault"] is not None
                and context["note_manifest"].get("operations")
            ):
                try:
                    reverse_manifest = _reverse_note_manifest(
                        Path(context["vault"]),
                        context["note_manifest"],
                        {resource_id: str(context["old_file_uri"])},
                        expected_current_sha256_by_note=_expected_refresh_hashes(
                            context["note_refresh"]
                        ),
                    )
                    _run_batch(
                        Path(context["vault"]),
                        reverse_manifest,
                        write=True,
                        runner=batch_runner,
                        manifest_path=reverse_manifest_path,
                    )
                except BaseException as rollback_exc:
                    rollback_errors.append(f"notes: {rollback_exc}")
            partial = (
                bool(rollback_errors)
                or batch_status == "partial_write"
                or (
                    post_check_payload is not None
                    and post_check_payload.get("ok") is not True
                )
            )
            state = "partial_write" if partial else "rolled_back"
            recovery.update(
                {
                    "state": state,
                    "error": f"{type(exc).__name__}: {exc}",
                    "batch_status": batch_status or None,
                    "post_move_check": post_check_payload,
                    "rollback_errors": rollback_errors,
                }
            )
            try:
                _atomic_write_json(recovery_path, recovery)
                _write_operation_log(
                    database_path,
                    operation_id,
                    "resource-move",
                    str(plan["plan_sha256"]),
                    state,
                    recovery,
                )
            except BaseException as log_exc:
                rollback_errors.append(f"operation_log: {log_exc}")
                state = "partial_write"
            return {
                **plan,
                "dry_run": False,
                "applied": False,
                "changed": state == "partial_write",
                "state": state,
                "operation_id": operation_id,
                "error": str(exc),
                "post_move_check": post_check_payload,
                "rollback_errors": rollback_errors,
                "recovery_log": str(recovery_path),
            }


def _default_receiver_config_path() -> Path:
    override = os.environ.get("OBSIDIAN_VAULT_NOTES_CONFIG")
    if override:
        return Path(override).expanduser().resolve(strict=False)
    appdata = os.environ.get("APPDATA")
    base = Path(appdata).expanduser() if appdata else Path.home() / "AppData" / "Roaming"
    return (base / "obsidian-vault-notes" / "config.json").resolve(strict=False)


def _root_relink_preview(
    new_root: os.PathLike[str] | str,
    database_path: os.PathLike[str] | str,
    *,
    config_path: os.PathLike[str] | str | None,
    obsidian_receiver_config_path: os.PathLike[str] | str | None,
    batch_runner: BatchRunner | None,
    manage_config_writer: ManageConfigWriter | None,
    receiver_config_writer: ReceiverConfigWriter | None,
    library_database_path: os.PathLike[str] | str | None,
    post_relink_check_required: bool,
) -> tuple[dict[str, object], dict[str, object]]:
    config_file = (
        config_module.get_config_path()
        if config_path is None
        else Path(config_path).expanduser().resolve(strict=False)
    )
    current_config = config_module.load_config(config_file, required=True)
    assert current_config is not None
    if current_config["schema_version"] != config_module.SCHEMA_VERSION:
        raise RegistrySafetyError("root-relink requires an initialised schema-v2 config")
    old_root = Path(str(current_config["knowledge_root"])).expanduser().resolve(
        strict=False
    )
    candidate = _absolute_directory(new_root)
    same_location = old_root == candidate
    if old_root.exists() and not same_location:
        raise RegistrySafetyError(
            "Both the configured old root and the proposed new root exist; "
            "decide whether this is a move or an independent copy before relinking"
        )
    root_id = _root_identity(candidate, database_path)
    if root_id != current_config["knowledge_root_id"]:
        raise RegistrySafetyError(
            "The new root identity does not match the configured knowledge_root_id"
        )
    portable_manifest = _require_synced_manifest(candidate, database_path, None)
    sources = current_config["sources"]
    assert isinstance(sources, dict)
    resolved_sources: dict[str, Path] = {}
    for name in ("vault", "library"):
        entry = sources.get(name)
        if not isinstance(entry, dict):
            raise RegistrySafetyError(f"Configured source is missing: {name}")
        relative = _normalise_relative_text(str(entry.get("relative_path") or ""))
        path = candidate.joinpath(*PurePosixPath(relative).parts).resolve(strict=False)
        try:
            path.relative_to(candidate)
        except ValueError as exc:
            raise RegistrySafetyError(f"Configured source escapes new root: {name}") from exc
        if not path.is_dir() or _is_reparse_point(path):
            raise RegistrySafetyError(f"Configured source is missing at new root: {path}")
        if name == "vault" and not (path / ".obsidian").is_dir():
            raise RegistrySafetyError(f"Vault lacks .obsidian at new root: {path}")
        resolved_sources[name] = path

    receiver_file = (
        _default_receiver_config_path()
        if obsidian_receiver_config_path is None
        else Path(obsidian_receiver_config_path).expanduser().resolve(strict=False)
    )
    receiver_config: dict[str, object] | None = None
    if receiver_config_writer is None:
        if not receiver_file.is_file():
            raise OperationError(
                "obsidian-vault-notes receiver config is required for root-relink: "
                f"{receiver_file}"
            )
        receiver_config = _read_json_object(receiver_file, "Obsidian receiver config")
        if receiver_config.get("schema_version") != 1:
            raise OperationError("Obsidian receiver config must use schema_version 1")

    proposed_config = json.loads(json.dumps(current_config, ensure_ascii=False))
    proposed_config["knowledge_root"] = str(candidate)
    # Validate the complete replacement now.  Saving is deferred until write.
    config_module._validate_config(proposed_config)

    proposed_receiver = None
    if receiver_config is not None:
        proposed_receiver = dict(receiver_config)
        # Receiver-local paths may include a bundled index root beneath the
        # Vault.  Preserve outside state paths, but rebase every absolute path
        # that belonged to the moved knowledge root.
        for key, raw_value in list(proposed_receiver.items()):
            if not isinstance(raw_value, str) or not raw_value.strip():
                continue
            old_value = Path(raw_value).expanduser()
            if not old_value.is_absolute():
                continue
            old_value = old_value.resolve(strict=False)
            try:
                relative_value = old_value.relative_to(old_root)
            except ValueError:
                continue
            proposed_receiver[key] = str(candidate / relative_value)
        proposed_receiver["vault_root"] = str(resolved_sources["vault"])

    uri_map: dict[str, str] = {}
    old_uri_map: dict[str, str] = {}
    with _connect(database_path) as connection:
        rows = connection.execute(
            "SELECT resource_id, relative_path FROM resources "
            "WHERE status = 'active' ORDER BY resource_id"
        ).fetchall()
        for row in rows:
            relative = str(row["relative_path"])
            relative_parts = PurePosixPath(relative).parts
            path = _safe_registered_path(candidate, relative, must_exist=True)
            if not path.is_file() or _is_reparse_point(path):
                raise RegistrySafetyError(
                    f"Active registered resource is missing or unsafe at new root: {path}"
                )
            identifier = str(row["resource_id"])
            uri_map[identifier] = path.as_uri()
            old_uri_map[identifier] = old_root.joinpath(*relative_parts).as_uri()
        note_manifest, note_preconditions, note_refresh = _note_state_for_ids(
            connection,
            candidate,
            resolved_sources["vault"],
            uri_map if not same_location else {},
        )
    library_relative = _normalise_relative_text(
        str(sources["library"]["relative_path"])
    )
    library_database = (
        Path(library_database_path).expanduser().resolve(strict=False)
        if library_database_path is not None
        else None
    )
    pdf_index = (
        library_module.preview_library_inventory_refresh(
            library_database,
            resolved_sources["library"],
            knowledge_root_id=root_id,
            library_relative_path=library_relative,
        )
        if library_database is not None
        else {
            "database_exists": False,
            "action": "not_configured",
            "changes_required": False,
        }
    )

    actions: list[dict[str, object]]
    if same_location:
        actions = [{"action": "already_linked", "knowledge_root": str(candidate)}]
    else:
        actions = [
            {
                "action": "update_manage_config",
                "path": str(config_file),
                "knowledge_root": str(candidate),
            },
            {
                "action": "update_obsidian_receiver_config",
                "path": str(receiver_file),
                "vault_root": str(resolved_sources["vault"]),
            },
            {
                "action": "rewrite_managed_note_links",
                "operation_count": len(note_manifest["operations"]),
            },
            {
                "action": (
                    "refresh_vault_index_after_relink"
                    if post_relink_check_required
                    else "caller_must_refresh_vault_index"
                ),
                "mode": "incremental",
            },
            {
                "action": "refresh_pdf_inventory_metadata_only",
                "knowledge_root_id": root_id,
                "library_relative_path": library_relative,
                "inventory_digest": pdf_index.get("inventory_digest"),
                "extracts_pdf_text": False,
            },
            {
                "action": "verify_registry_manifest",
                "path": str(portable_manifest),
            },
        ]
    if same_location and post_relink_check_required:
        actions.append(
            {
                "action": "refresh_and_verify_indexes_at_current_root",
                "mode": "incremental",
            }
        )
    if same_location and pdf_index.get("changes_required"):
        actions.append(
            {
                "action": "refresh_pdf_inventory_metadata_only",
                "knowledge_root_id": root_id,
                "library_relative_path": library_relative,
                "inventory_digest": pdf_index.get("inventory_digest"),
                "extracts_pdf_text": False,
            }
        )
    refresh_summary = dict(note_refresh["summary"])
    if refresh_summary["refresh_notes"] or refresh_summary["deleted_notes"]:
        actions.append(
            {
                "action": "refresh_note_reference_cache",
                "note_count": refresh_summary["refresh_notes"],
                "deleted_note_count": refresh_summary["deleted_notes"],
            }
        )
    plan = make_plan(
        "root-relink",
        actions,
        knowledge_root_id=root_id,
        old_knowledge_root=str(old_root),
        new_knowledge_root=str(candidate),
        vault_relative_path=str(sources["vault"]["relative_path"]),
        library_relative_path=library_relative,
        manage_config_sha256=(
            _sha256_file(config_file) if config_file.is_file() else None
        ),
        receiver_config_sha256=(
            _sha256_file(receiver_file)
            if receiver_config_writer is None and receiver_file.is_file()
            else None
        ),
        note_preconditions=note_preconditions,
        note_refresh_preconditions=note_refresh["preconditions"],
        deleted_note_preconditions=note_refresh["deleted_notes"],
        reference_refresh=refresh_summary,
        pdf_index=pdf_index,
        post_relink_check_required=post_relink_check_required,
    )
    plan["batch_guard_preflight"] = (
        "deferred_until_registry_transition"
        if note_manifest["operations"]
        else "not_needed"
    )
    return plan, {
        "old_root": old_root,
        "new_root": candidate,
        "config_file": config_file,
        "receiver_file": receiver_file,
        "proposed_config": proposed_config,
        "proposed_receiver": proposed_receiver,
        "vault": resolved_sources["vault"],
        "note_manifest": note_manifest,
        "note_refresh": note_refresh,
        "uri_map": uri_map,
        "old_uri_map": old_uri_map,
        "same_location": same_location,
        "manage_config_writer": manage_config_writer,
        "receiver_config_writer": receiver_config_writer,
        "library_database": library_database,
        "library_root": resolved_sources["library"],
        "library_relative_path": library_relative,
        "pdf_index": pdf_index,
    }


def root_relink(
    new_root: os.PathLike[str] | str,
    database_path: os.PathLike[str] | str,
    *,
    config_path: os.PathLike[str] | str | None = None,
    obsidian_receiver_config_path: os.PathLike[str] | str | None = None,
    batch_runner: BatchRunner | None = None,
    manage_config_writer: ManageConfigWriter | None = None,
    receiver_config_writer: ReceiverConfigWriter | None = None,
    library_database_path: os.PathLike[str] | str | None = None,
    post_relink_check: PostRelinkCheck | None = None,
    write: bool = False,
    expect_plan_sha256: str | None = None,
) -> dict[str, object]:
    """Relink a root that was moved as a whole, preserving its root identity."""

    plan, context = _root_relink_preview(
        new_root,
        database_path,
        config_path=config_path,
        obsidian_receiver_config_path=obsidian_receiver_config_path,
        batch_runner=batch_runner,
        manage_config_writer=manage_config_writer,
        receiver_config_writer=receiver_config_writer,
        library_database_path=library_database_path,
        post_relink_check_required=post_relink_check is not None,
    )
    if not write:
        return plan
    _confirm_plan(plan, expect_plan_sha256)
    if (
        context["same_location"]
        and not _note_refresh_has_changes(context["note_refresh"])
        and not context["pdf_index"].get("changes_required")
        and post_relink_check is None
    ):
        return {**plan, "dry_run": False, "applied": True, "changed": False}

    with _operation_lock(database_path):
        plan, context = _root_relink_preview(
            new_root,
            database_path,
            config_path=config_path,
            obsidian_receiver_config_path=obsidian_receiver_config_path,
            batch_runner=batch_runner,
            manage_config_writer=manage_config_writer,
            receiver_config_writer=receiver_config_writer,
            library_database_path=library_database_path,
            post_relink_check_required=post_relink_check is not None,
        )
        _confirm_plan(plan, expect_plan_sha256)
        operation_id = f"root-relink-{plan['plan_sha256'][:24]}"
        recovery_dir = _recovery_directory(database_path, operation_id)
        recovery_dir.mkdir(parents=True, exist_ok=True)
        database_backup = recovery_dir / "registry-before.sqlite3"
        library_backup = recovery_dir / "pdf-index-before.sqlite3"
        config_backup = recovery_dir / "manage-config-before.json"
        receiver_backup = recovery_dir / "obsidian-config-before.json"
        forward_manifest_path = recovery_dir / "vault-forward.json"
        reverse_manifest_path = recovery_dir / "vault-reverse.json"
        recovery_path = recovery_dir / "recovery.json"
        config_file = Path(context["config_file"])
        receiver_file = Path(context["receiver_file"])
        if config_file.is_file():
            shutil.copy2(config_file, config_backup)
        if receiver_file.is_file():
            shutil.copy2(receiver_file, receiver_backup)
        recovery: dict[str, object] = {
            "operation_id": operation_id,
            "operation": "root-relink",
            "plan_sha256": plan["plan_sha256"],
            "state": "prepared",
            "old_root": str(context["old_root"]),
            "new_root": str(context["new_root"]),
            "database_backup": str(database_backup),
            "pdf_index_backup": str(library_backup),
            "manage_config_backup": str(config_backup),
            "receiver_config_backup": str(receiver_backup),
            "forward_manifest": str(forward_manifest_path),
            "reverse_manifest": str(reverse_manifest_path),
        }
        _write_operation_log(
            database_path,
            operation_id,
            "root-relink",
            str(plan["plan_sha256"]),
            "prepared",
            recovery,
        )
        _backup_database(database_path, database_backup)
        if context["pdf_index"].get("changes_required"):
            assert context["library_database"] is not None
            _backup_database(Path(context["library_database"]), library_backup)
        _atomic_write_json(recovery_path, recovery)

        callbacks_used = bool(manage_config_writer or receiver_config_writer)
        batch_payload: dict[str, object] | None = None
        pdf_index_update: dict[str, object] | None = None
        post_check_payload: dict[str, object] | None = None
        try:
            if not context["same_location"]:
                if manage_config_writer is None:
                    config_module.save_config(dict(context["proposed_config"]), config_file)
                else:
                    manage_config_writer(context["proposed_config"])
                if receiver_config_writer is None:
                    assert context["proposed_receiver"] is not None
                    _atomic_write_json(receiver_file, context["proposed_receiver"])
                else:
                    receiver_config_writer(Path(context["vault"]))
            if context["pdf_index"].get("changes_required"):
                assert context["library_database"] is not None
                pdf_index_update = library_module.refresh_library_inventory(
                    Path(context["library_database"]),
                    Path(context["library_root"]),
                    knowledge_root_id=str(plan["context"]["knowledge_root_id"]),
                    library_relative_path=str(context["library_relative_path"]),
                    expected_preview=context["pdf_index"],
                )
            batch_payload = _run_batch(
                Path(context["vault"]),
                context["note_manifest"],
                write=True,
                runner=batch_runner,
                manifest_path=forward_manifest_path,
            )
            _update_note_reference_cache(
                database_path,
                Path(context["new_root"]),
                Path(context["vault"]),
                context["note_refresh"],
            )
            if post_relink_check is not None:
                post_check_payload = dict(post_relink_check())
                if post_check_payload.get("ok") is not True:
                    raise OperationError(
                        "The required post-relink index refresh or verification failed"
                    )
            recovery.update(
                {
                    "state": "applied",
                    "batch_status": batch_payload.get(
                        "transaction_status", "not_needed"
                    ),
                    "pdf_index_update": pdf_index_update,
                    "post_relink_check": post_check_payload,
                }
            )
            _atomic_write_json(recovery_path, recovery)
            _write_operation_log(
                database_path,
                operation_id,
                "root-relink",
                str(plan["plan_sha256"]),
                "applied",
                recovery,
            )
            database_backup.unlink(missing_ok=True)
            library_backup.unlink(missing_ok=True)
            config_backup.unlink(missing_ok=True)
            receiver_backup.unlink(missing_ok=True)
            return {
                **plan,
                "dry_run": False,
                "applied": True,
                "changed": True,
                "state": "applied",
                "operation_id": operation_id,
                "knowledge_root": str(context["new_root"]),
                "vault_root": str(context["vault"]),
                "pdf_index_update": pdf_index_update or context["pdf_index"],
                "post_relink_check": post_check_payload,
                "recovery_log": str(recovery_path),
            }
        except BaseException as exc:
            rollback_errors: list[str] = []
            batch_status = (
                str(exc.payload.get("transaction_status") or "")
                if isinstance(exc, BatchEditError)
                else ""
            )
            # If a later step fails after the batch reports success, restore the
            # old URIs with current note hashes.  Most batch failures are already
            # transactional and require no reverse call.
            if batch_payload and batch_payload.get("transaction_status") == "applied":
                try:
                    reverse_manifest = _reverse_note_manifest(
                        Path(context["vault"]),
                        context["note_manifest"],
                        context["old_uri_map"],
                        expected_current_sha256_by_note=_expected_refresh_hashes(
                            context["note_refresh"]
                        ),
                    )
                    _run_batch(
                        Path(context["vault"]),
                        reverse_manifest,
                        write=True,
                        runner=batch_runner,
                        manifest_path=reverse_manifest_path,
                    )
                except BaseException as rollback_exc:
                    rollback_errors.append(f"notes: {rollback_exc}")
            if not callbacks_used:
                try:
                    if config_backup.is_file():
                        _atomic_write_bytes(config_file, config_backup.read_bytes())
                    if receiver_backup.is_file():
                        _atomic_write_bytes(receiver_file, receiver_backup.read_bytes())
                except BaseException as rollback_exc:
                    rollback_errors.append(f"config: {rollback_exc}")
            else:
                # An injected callback can perform arbitrary external work.  We
                # cannot claim full rollback without a corresponding inverse.
                rollback_errors.append("injected config callback has no verified rollback")
            try:
                _restore_database(database_path, database_backup)
            except BaseException as rollback_exc:
                rollback_errors.append(f"database: {rollback_exc}")
            if library_backup.is_file():
                try:
                    assert context["library_database"] is not None
                    _restore_database(
                        Path(context["library_database"]), library_backup
                    )
                except BaseException as rollback_exc:
                    rollback_errors.append(f"pdf_index: {rollback_exc}")
            partial = (
                bool(rollback_errors)
                or batch_status == "partial_write"
                or callbacks_used
                or (
                    post_check_payload is not None
                    and post_check_payload.get("ok") is not True
                )
            )
            state = "partial_write" if partial else "rolled_back"
            recovery.update(
                {
                    "state": state,
                    "error": f"{type(exc).__name__}: {exc}",
                    "batch_status": batch_status or None,
                    "post_relink_check": post_check_payload,
                    "rollback_errors": rollback_errors,
                }
            )
            try:
                _atomic_write_json(recovery_path, recovery)
                _write_operation_log(
                    database_path,
                    operation_id,
                    "root-relink",
                    str(plan["plan_sha256"]),
                    state,
                    recovery,
                )
            except BaseException as log_exc:
                rollback_errors.append(f"operation_log: {log_exc}")
                state = "partial_write"
            return {
                **plan,
                "dry_run": False,
                "applied": False,
                "changed": state == "partial_write",
                "state": state,
                "operation_id": operation_id,
                "error": str(exc),
                "post_relink_check": post_check_payload,
                "rollback_errors": rollback_errors,
                "recovery_log": str(recovery_path),
            }


__all__ = [
    "BatchEditError",
    "OperationError",
    "resource_move",
    "root_relink",
]
