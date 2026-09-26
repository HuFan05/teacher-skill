"""Apply manifest-driven Markdown edits as a guarded multi-file transaction."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from _common import DEFAULT_VAULT_ROOT, configure_stdio
from _observer import flush as flush_observer
from _observer import observed
from _observer import phase
from resource_reference_guard import (
    HarnessClient,
    ResourceReferenceError,
    preflight_resource_references,
    rewrite_managed_resource_uri,
    synchronize_resource_references,
)
from vault_edit import TextEdit, apply_text_edits, build_logical_document, find_text_edits, normalize_text


class HashRaceError(RuntimeError):
    """The target changed after its atomic replacement file was prepared."""


class ReferenceSyncTransactionError(RuntimeError):
    """A post-write resource cache update failed inside the batch transaction."""


@dataclass
class FileState:
    path: Path
    relative_path: str
    raw: bytes
    text: str
    sha256: str
    mtime: float
    had_bom: bool
    current_text: str
    final_raw: bytes


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dry-run or apply a batch edit manifest inside an Obsidian vault.")
    parser.add_argument("--manifest", required=True, help="JSON manifest with an operations list.")
    parser.add_argument("--vault-root", default=str(DEFAULT_VAULT_ROOT), help="Root of the Obsidian vault.")
    parser.add_argument("--write", action="store_true", help="Actually write changes. Default is dry-run.")
    parser.add_argument("--backup", action="store_true", help="Create .bak files before writing changed files.")
    parser.add_argument("--allow-outside-vault", action="store_true", help="Allow target files outside --vault-root for deliberate tests.")
    return parser


@observed("obsidian.batch.manifest")
def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("manifest root must be a JSON object")
    return value


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def resolve_target(vault_root: Path, value: str, allow_outside: bool) -> Path:
    raw = Path(value).expanduser()
    path = raw.resolve() if raw.is_absolute() else (vault_root / raw).resolve()
    if not allow_outside:
        try:
            path.relative_to(vault_root)
        except ValueError as exc:
            raise ValueError(f"Refusing to edit a file outside the Vault root: {path}") from exc
    if not path.exists():
        raise FileNotFoundError(f"File does not exist: {path}")
    if not path.is_file():
        raise IsADirectoryError(f"Path is not a file: {path}")
    if path.suffix.lower() != ".md":
        raise ValueError(f"Refusing to edit non-.md file: {path}")
    return path


def decode_utf8(raw: bytes, path: Path) -> tuple[str, bool]:
    had_bom = raw.startswith(b"\xef\xbb\xbf")
    payload = raw[3:] if had_bom else raw
    try:
        return payload.decode("utf-8"), had_bom
    except UnicodeDecodeError as exc:
        raise ValueError(f"File is not valid UTF-8: {path}: {exc}") from exc


def encode_utf8(text: str, had_bom: bool) -> bytes:
    payload = text.encode("utf-8")
    return (b"\xef\xbb\xbf" + payload) if had_bom else payload


def load_state(vault_root: Path, path: Path) -> FileState:
    raw = path.read_bytes()
    text, had_bom = decode_utf8(raw, path)
    relative_path = path.relative_to(vault_root).as_posix() if path.is_relative_to(vault_root) else str(path)
    return FileState(
        path=path,
        relative_path=relative_path,
        raw=raw,
        text=text,
        sha256=sha256_bytes(raw),
        mtime=path.stat().st_mtime,
        had_bom=had_bom,
        current_text=text,
        final_raw=raw,
    )


def diff_stats(old: str, new: str) -> tuple[int, int]:
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    inserted = 0
    removed = 0
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in {"replace", "delete"}:
            removed += i2 - i1
        if tag in {"replace", "insert"}:
            inserted += j2 - j1
    return inserted, removed


def apply_operation(text: str, operation: dict[str, Any]) -> tuple[str, list[str]]:
    op = operation.get("operation")
    warnings: list[str] = []
    logical = build_logical_document(text).logical
    edits: list[TextEdit]
    if op == "replace-text":
        find = normalize_text(str(operation.get("find", "")), trim_trailing=False)
        replacement = normalize_text(str(operation.get("replacement", operation.get("text", ""))), trim_trailing=True)
        replace_all = bool(operation.get("all", False))
        if not find:
            raise ValueError("replace-text requires nonempty find")
        count = logical.count(find)
        if count == 0:
            warnings.append("text not found; no change")
            return text, warnings
        if count > 1 and not replace_all:
            raise ValueError(f"text appears {count} times; set all=true to replace every occurrence")
        edits = find_text_edits(logical, find, replacement, replace_all=replace_all)
    elif op == "replace-wikilink":
        old = operation.get("old_wikilink") or operation.get("find")
        new = operation.get("new_wikilink") or operation.get("replacement")
        if not old or not new:
            raise ValueError("replace-wikilink requires old_wikilink and new_wikilink")
        count = logical.count(str(old))
        if count == 0:
            warnings.append("old wikilink not found; no change")
            return text, warnings
        edits = find_text_edits(logical, str(old), str(new), replace_all=True)
    elif op == "append-section":
        payload = normalize_text(str(operation.get("text", "")), trim_trailing=True).strip("\n")
        if not payload.strip():
            raise ValueError("append-section requires nonempty text")
        if payload in logical:
            warnings.append("payload already present; no change")
            return text, warnings
        separator = "" if logical.endswith("\n") or not logical else "\n"
        edits = [TextEdit(len(logical), len(logical), separator + payload + "\n")]
    elif op == "replace-managed-resource-uri":
        resource_id = operation.get("resource_id")
        new_uri = operation.get("new_uri")
        if not resource_id or not new_uri:
            raise ValueError("replace-managed-resource-uri requires resource_id and new_uri")
        rewritten = rewrite_managed_resource_uri(text, str(resource_id), str(new_uri))
        if not rewritten["changed"]:
            warnings.append("managed resource URI already current; no change")
        elif int(rewritten["count"]) > 1:
            warnings.append(f"updated {rewritten['count']} occurrences for one resource ID")
        return str(rewritten["text"]), warnings
    else:
        raise ValueError(f"Unsupported manifest operation: {op}")
    return apply_text_edits(text, edits), warnings


def base_result(index: int, operation: dict[str, Any], state: FileState) -> dict[str, Any]:
    expected_sha = operation.get("expected_sha256")
    expected_mtime = operation.get("expected_mtime")
    return {
        "index": index,
        "operation": operation.get("operation"),
        "file": str(state.path),
        "relative_path": state.relative_path,
        "changed": False,
        "inserted_lines": 0,
        "removed_lines": 0,
        "warnings": [],
        "sha256": state.sha256,
        "mtime": state.mtime,
        "expected_hash_ok": None if not expected_sha else state.sha256.lower() == str(expected_sha).lower(),
        "expected_mtime_ok": None if expected_mtime is None else abs(state.mtime - float(expected_mtime)) < 0.000001,
        "write": False,
        "applied": False,
        "backup_path": None,
        "pre_sha256": state.sha256,
        "post_sha256": state.sha256,
        "prospective_sha256": state.sha256,
        "race_check": "not_run",
        "resource_references": None,
    }


@observed("obsidian.batch.preflight")
def preflight(
    vault_root: Path,
    operations: list[Any],
    *,
    allow_outside: bool,
) -> tuple[dict[Path, FileState], list[dict[str, Any]], list[dict[str, Any]]]:
    states: dict[Path, FileState] = {}
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for index, raw_operation in enumerate(operations):
        operation = raw_operation if isinstance(raw_operation, dict) else {}
        try:
            if not isinstance(raw_operation, dict):
                raise ValueError("manifest operation must be an object")
            target_value = operation.get("file") or operation.get("relative_path")
            if not target_value:
                raise ValueError("manifest operation requires file or relative_path")
            path = resolve_target(vault_root, str(target_value), allow_outside)
            state = states.get(path)
            if state is None:
                state = load_state(vault_root, path)
                states[path] = state
            result = base_result(index, operation, state)
            if result["expected_hash_ok"] is False:
                result["warnings"].append("expected_sha256 does not match the original file")
                results.append(result)
                raise ValueError("expected_sha256 does not match the original file")
            if result["expected_mtime_ok"] is False:
                result["warnings"].append("expected_mtime does not match the original file")
                results.append(result)
                raise ValueError("expected_mtime does not match the original file")
            before = state.current_text
            after, warnings = apply_operation(before, operation)
            inserted, removed = diff_stats(before, after)
            result.update(
                {
                    "changed": before != after,
                    "inserted_lines": inserted,
                    "removed_lines": removed,
                    "warnings": warnings,
                    "prospective_sha256": sha256_bytes(encode_utf8(after, state.had_bom)),
                }
            )
            state.current_text = after
            results.append(result)
        except Exception as exc:
            errors.append(
                {
                    "index": index,
                    "operation": operation.get("operation"),
                    "file": operation.get("file") or operation.get("relative_path"),
                    "error": str(exc),
                }
            )
    for state in states.values():
        state.final_raw = encode_utf8(state.current_text, state.had_bom)
    return states, results, errors


def preflight_resource_reference_states(
    states: dict[Path, FileState],
    results: list[dict[str, Any]],
    *,
    client: HarnessClient | None = None,
) -> tuple[dict[Path, dict[str, object]], list[dict[str, Any]]]:
    """Guard each file's final candidate before any file in the batch is written."""

    harness = client or HarnessClient()
    audits: dict[Path, dict[str, object]] = {}
    errors: list[dict[str, Any]] = []
    mapped = result_indexes_by_path(results)
    for state in states.values():
        try:
            audit = preflight_resource_references(
                state.text,
                state.current_text,
                note_path=state.path,
                client=harness,
            )
        except ResourceReferenceError as exc:
            audit = exc.audit
            errors.append(
                {
                    "index": None,
                    "phase": "resource_reference_preflight",
                    "file": state.relative_path,
                    "error": str(exc),
                    "resource_references": audit,
                }
            )
        audits[state.path] = audit
        for result_index in mapped.get(state.path, []):
            results[result_index]["resource_references"] = audit
    return audits, errors


def require_count(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a nonnegative integer")
    return value


def normalize_must_contain(value: Any) -> tuple[str, int, int | None]:
    if isinstance(value, str):
        text = value
        min_count = 1
        max_count: int | None = None
    elif isinstance(value, dict):
        text = value.get("text")
        min_count = require_count(value.get("min_count", 1), "must_contain.min_count")
        raw_max = value.get("max_count")
        max_count = None if raw_max is None else require_count(raw_max, "must_contain.max_count")
    else:
        raise ValueError("must_contain entries must be strings or objects")
    if not isinstance(text, str) or not text:
        raise ValueError("must_contain text must be a nonempty string")
    if max_count is not None and max_count < min_count:
        raise ValueError("must_contain.max_count must be greater than or equal to min_count")
    return text, min_count, max_count


@observed("obsidian.batch.assertions")
def evaluate_assertions(
    vault_root: Path,
    assertions: list[Any],
    states: dict[Path, FileState],
    *,
    allow_outside: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for index, raw_assertion in enumerate(assertions):
        summary: dict[str, Any] = {
            "index": index,
            "file": None,
            "relative_path": None,
            "ok": False,
            "checks": [],
        }
        try:
            if not isinstance(raw_assertion, dict):
                raise ValueError("assertion must be an object")
            target_value = raw_assertion.get("file") or raw_assertion.get("relative_path")
            if not target_value:
                raise ValueError("assertion requires file or relative_path")
            path = resolve_target(vault_root, str(target_value), allow_outside)
            state = states.get(path)
            if state is None:
                state = load_state(vault_root, path)
                states[path] = state
            summary["file"] = str(state.path)
            summary["relative_path"] = state.relative_path
            must_contain = raw_assertion.get("must_contain", [])
            must_not_contain = raw_assertion.get("must_not_contain", [])
            if not isinstance(must_contain, list):
                raise ValueError("assertion must_contain must be a list")
            if not isinstance(must_not_contain, list):
                raise ValueError("assertion must_not_contain must be a list")
            if not must_contain and not must_not_contain:
                raise ValueError("assertion must contain at least one check")

            failed = False
            for raw_requirement in must_contain:
                text, min_count, max_count = normalize_must_contain(raw_requirement)
                count = state.current_text.count(text)
                ok = count >= min_count and (max_count is None or count <= max_count)
                summary["checks"].append(
                    {
                        "kind": "must_contain",
                        "text": text,
                        "count": count,
                        "min_count": min_count,
                        "max_count": max_count,
                        "ok": ok,
                    }
                )
                if not ok:
                    failed = True
                    upper = "unbounded" if max_count is None else str(max_count)
                    errors.append(
                        {
                            "index": None,
                            "assertion_index": index,
                            "file": state.relative_path,
                            "error": f"assertion must_contain failed for {text!r}: found {count}, expected {min_count}..{upper}",
                        }
                    )
            for text in must_not_contain:
                if not isinstance(text, str) or not text:
                    raise ValueError("must_not_contain entries must be nonempty strings")
                count = state.current_text.count(text)
                ok = count == 0
                summary["checks"].append(
                    {"kind": "must_not_contain", "text": text, "count": count, "ok": ok}
                )
                if not ok:
                    failed = True
                    errors.append(
                        {
                            "index": None,
                            "assertion_index": index,
                            "file": state.relative_path,
                            "error": f"assertion must_not_contain failed for {text!r}: found {count}",
                        }
                    )
            summary["ok"] = not failed
        except Exception as exc:
            errors.append(
                {
                    "index": None,
                    "assertion_index": index,
                    "file": summary.get("relative_path") or (raw_assertion.get("file") if isinstance(raw_assertion, dict) else None),
                    "error": str(exc),
                }
            )
        results.append(summary)
    for state in states.values():
        state.final_raw = encode_utf8(state.current_text, state.had_bom)
    return results, errors


def make_backup(path: Path, raw: bytes) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup = path.with_name(f"{path.name}.{stamp}.bak")
    counter = 1
    while backup.exists():
        backup = path.with_name(f"{path.name}.{stamp}-{counter}.bak")
        counter += 1
    backup.write_bytes(raw)
    return backup


@observed("obsidian.batch.prepare")
def prepare_atomic_temp(path: Path, payload: bytes) -> Path:
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.tmp")
    try:
        temp_path.write_bytes(payload)
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise
    return temp_path


def write_bytes_atomic(
    path: Path,
    payload: bytes,
    *,
    expected_current_sha256: str,
) -> None:
    temp_path = prepare_atomic_temp(path, payload)
    try:
        try:
            current_sha = sha256_bytes(path.read_bytes())
        except OSError as exc:
            raise HashRaceError(
                f"Target could not be re-read after temp preparation: {path}: {exc}"
            ) from exc
        if current_sha != expected_current_sha256:
            raise HashRaceError(
                "Target changed after temp preparation; atomic replacement refused: "
                f"{path}"
            )
        temp_path.replace(path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def transaction_directory(vault_root: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    path = vault_root / "笔记草稿" / "_note_backups" / f".vault_batch_edit_transaction-{stamp}-{os.getpid()}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def write_journal(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")


def result_indexes_by_path(results: list[dict[str, Any]]) -> dict[Path, list[int]]:
    mapped: dict[Path, list[int]] = {}
    for idx, result in enumerate(results):
        mapped.setdefault(Path(str(result["file"])), []).append(idx)
    return mapped


@observed("obsidian.batch.write")
def apply_transaction(
    vault_root: Path,
    states: dict[Path, FileState],
    results: list[dict[str, Any]],
    *,
    user_backup: bool,
    resource_audits: dict[Path, dict[str, object]] | None = None,
    reference_client: HarnessClient | None = None,
) -> tuple[str, int, list[dict[str, Any]], str | None]:
    changed_states = [state for state in states.values() if state.final_raw != state.raw]
    if not changed_states:
        return "applied", 0, [], None
    path_results = result_indexes_by_path(results)
    journal: dict[str, Any] = {
        "status": "prepared",
        "created_at": datetime.now().isoformat(),
        "files": [],
    }
    tx_dir: Path | None = None
    journal_path: Path | None = None
    try:
        tx_dir = transaction_directory(vault_root)
        journal_path = tx_dir / "transaction.json"
        for index, state in enumerate(changed_states):
            backup_path = tx_dir / f"{index:04d}-{re.sub(r'[^A-Za-z0-9._-]+', '_', state.path.name)}.original"
            backup_path.write_bytes(state.raw)
            journal["files"].append(
                {
                    "path": str(state.path),
                    "backup": str(backup_path),
                    "pre_sha256": state.sha256,
                    "post_sha256": sha256_bytes(state.final_raw),
                }
            )
        write_journal(journal_path, journal)
    except Exception as exc:
        prepare_errors = [{"phase": "prepare", "error": str(exc)}]
        retained_path: str | None = None
        if tx_dir is not None and tx_dir.exists():
            try:
                shutil.rmtree(tx_dir)
            except OSError as cleanup_exc:
                retained_path = str(journal_path or tx_dir)
                prepare_errors.append({"phase": "prepare_cleanup", "error": str(cleanup_exc)})
        for result in results:
            result["write"] = True
        return "prepare_failed", 2, prepare_errors, retained_path

    assert tx_dir is not None and journal_path is not None

    written: list[FileState] = []
    transaction_errors: list[dict[str, Any]] = []
    sync_failure = False
    harness = reference_client or HarnessClient()
    audits = resource_audits or {}
    try:
        for state in changed_states:
            current_sha = sha256_bytes(state.path.read_bytes())
            if current_sha != state.sha256:
                for result_index in path_results.get(state.path, []):
                    results[result_index]["race_check"] = "failed"
                raise RuntimeError(f"File changed after preflight: {state.relative_path}")
            if user_backup:
                backup_path = make_backup(state.path, state.raw)
                for result_index in path_results.get(state.path, []):
                    results[result_index]["backup_path"] = str(backup_path)
            try:
                write_bytes_atomic(
                    state.path,
                    state.final_raw,
                    expected_current_sha256=state.sha256,
                )
            except HashRaceError:
                for result_index in path_results.get(state.path, []):
                    results[result_index]["race_check"] = "failed"
                raise
            for result_index in path_results.get(state.path, []):
                results[result_index]["race_check"] = "passed"
            written.append(state)
            if sha256_bytes(state.path.read_bytes()) != sha256_bytes(state.final_raw):
                raise RuntimeError(f"Post-write hash verification failed: {state.relative_path}")
        for state in changed_states:
            final_raw = state.path.read_bytes()
            final_text, _had_bom = decode_utf8(final_raw, state.path)
            post_sha = sha256_bytes(final_raw)
            audit = audits.get(state.path)
            if audit is None:
                audit = {"external_change": False, "sync": {"status": "not_run"}}
                audits[state.path] = audit
            with phase("obsidian.batch.resource.sync"):
                synchronized = synchronize_resource_references(
                    note_path=state.path,
                    final_text=final_text,
                    post_sha256=post_sha,
                    audit=audit,
                    client=harness,
                )
            for result_index in path_results.get(state.path, []):
                results[result_index]["resource_references"] = audit
            if not synchronized:
                sync_failure = True
                detail = audit.get("sync", {})
                message = detail.get("error") if isinstance(detail, dict) else None
                raise ReferenceSyncTransactionError(
                    f"Reference cache synchronization failed for {state.relative_path}: "
                    f"{message or 'unknown synchronization error'}"
                )
        for state in changed_states:
            post_sha = sha256_bytes(state.final_raw)
            for result_index in path_results.get(state.path, []):
                result = results[result_index]
                result["write"] = True
                result["post_sha256"] = post_sha
                result["applied"] = bool(result["changed"])
        journal["status"] = "applied"
        write_journal(journal_path, journal)
        try:
            shutil.rmtree(tx_dir)
            transaction_log: str | None = None
        except OSError as cleanup_exc:
            transaction_log = str(journal_path)
            for result in results:
                result["warnings"].append(f"transaction cleanup failed: {cleanup_exc}")
        return "applied", 0, [], transaction_log
    except Exception as exc:
        transaction_errors.append(
            {"phase": "reference_sync" if sync_failure else "write", "error": str(exc)}
        )
        rollback_errors: list[dict[str, Any]] = []
        with phase("obsidian.batch.rollback"):
            for state in reversed(written):
                try:
                    current_sha = sha256_bytes(state.path.read_bytes())
                    final_sha = sha256_bytes(state.final_raw)
                    if current_sha == state.sha256:
                        continue
                    if current_sha != final_sha:
                        raise RuntimeError(
                            "external change detected after transaction write; automatic rollback refused"
                        )
                    write_bytes_atomic(
                        state.path,
                        state.raw,
                        expected_current_sha256=final_sha,
                    )
                    if sha256_bytes(state.path.read_bytes()) != state.sha256:
                        raise RuntimeError("restored hash does not match original")
                except Exception as rollback_exc:
                    rollback_errors.append({"file": state.relative_path, "error": str(rollback_exc)})
        all_original = True
        for state in changed_states:
            try:
                if sha256_bytes(state.path.read_bytes()) != state.sha256:
                    all_original = False
            except OSError:
                all_original = False
        restore_sync_errors: list[dict[str, Any]] = []
        if sync_failure and not rollback_errors and all_original:
            for state in changed_states:
                original_audit = audits.get(state.path, {})
                baseline = original_audit.get("harness_diagnostics", {})
                before_diagnostics = baseline.get("before", []) if isinstance(baseline, dict) else []
                rollback_audit: dict[str, object] = {
                    "external_change": True,
                    "harness_diagnostics": {
                        "before": before_diagnostics,
                        "after": before_diagnostics,
                        "added": [],
                        "removed": [],
                    },
                    "sync": {"status": "not_run"},
                }
                restored = synchronize_resource_references(
                    note_path=state.path,
                    final_text=state.text,
                    post_sha256=state.sha256,
                    audit=rollback_audit,
                    client=harness,
                )
                original_audit["rollback_sync"] = rollback_audit.get("sync")
                if not restored:
                    detail = rollback_audit.get("sync", {})
                    restore_sync_errors.append(
                        {
                            "file": state.relative_path,
                            "error": detail.get("error") if isinstance(detail, dict) else "cache restore failed",
                        }
                    )
        if rollback_errors or not all_original or restore_sync_errors:
            status = "partial_write"
        elif sync_failure:
            status = "reference_sync_pending"
        else:
            status = "rolled_back"
        journal["status"] = status
        journal["write_error"] = str(exc)
        journal["rollback_errors"] = rollback_errors
        journal["reference_sync_restore_errors"] = restore_sync_errors
        try:
            write_journal(journal_path, journal)
        except OSError as journal_exc:
            transaction_errors.append({"phase": "journal", "error": str(journal_exc)})
        transaction_errors.extend({"phase": "rollback", **item} for item in rollback_errors)
        transaction_errors.extend({"phase": "reference_sync_restore", **item} for item in restore_sync_errors)
        for result in results:
            result["write"] = True
            result["applied"] = False
            result["post_sha256"] = result["pre_sha256"] if status in {"rolled_back", "reference_sync_pending"} else None
        exit_code = 2 if status == "rolled_back" else 3
        return status, exit_code, transaction_errors, str(journal_path)


def payload_for(
    *,
    manifest_path: Path,
    version: int | None,
    write: bool,
    operation_count: int,
    results: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    transaction_status: str,
    transaction_log: str | None = None,
    assertions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "mode": "batch_edit",
        "manifest_version": version,
        "write": write,
        "manifest": str(manifest_path),
        "operation_count": operation_count,
        "assertion_count": len(assertions or []),
        "assertions": assertions or [],
        "changed_count": sum(1 for item in results if item.get("changed")),
        "applied_count": sum(1 for item in results if item.get("applied")),
        "transaction_status": transaction_status,
        "transaction_log": transaction_log,
        "results": results,
        "errors": errors,
        "ok": not errors and transaction_status in {"dry_run", "applied"},
    }


# Shared output support is resolved from this script, including importlib callers.
import sys as _output_sys
from pathlib import Path as _OutputPath
_output_dir = str(_OutputPath(__file__).resolve().parent)
if _output_dir not in _output_sys.path:
    _output_sys.path.insert(0, _output_dir)
from note_public_output import public_entry

@public_entry('vault_batch_edit')
def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    args = build_parser().parse_args(argv)
    vault_root = Path(args.vault_root).expanduser().resolve()
    manifest_path = Path(args.manifest).expanduser().resolve()
    try:
        manifest = read_json(manifest_path)
        version = manifest.get("version")
        if version not in {None, 2}:
            raise ValueError("manifest version must be omitted or equal to 2")
        operations = manifest.get("operations")
        if not isinstance(operations, list):
            raise ValueError("manifest must contain an operations list")
        assertions = manifest.get("assertions", [])
        if not isinstance(assertions, list):
            raise ValueError("manifest assertions must be a list when provided")
    except Exception as exc:
        payload = payload_for(
            manifest_path=manifest_path,
            version=None,
            write=bool(args.write),
            operation_count=0,
            results=[],
            errors=[{"index": None, "error": str(exc)}],
            transaction_status="preflight_failed",
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2

    states, results, errors = preflight(
        vault_root,
        operations,
        allow_outside=args.allow_outside_vault,
    )
    assertion_results: list[dict[str, Any]] = []
    if not errors:
        assertion_results, assertion_errors = evaluate_assertions(
            vault_root,
            assertions,
            states,
            allow_outside=args.allow_outside_vault,
        )
        errors.extend(assertion_errors)
    reference_client = HarnessClient()
    resource_audits: dict[Path, dict[str, object]] = {}
    if not errors:
        resource_audits, reference_errors = preflight_resource_reference_states(
            states,
            results,
            client=reference_client,
        )
        errors.extend(reference_errors)
    for result in results:
        result["write"] = bool(args.write)
    if errors:
        payload = payload_for(
            manifest_path=manifest_path,
            version=version,
            write=bool(args.write),
            operation_count=len(operations),
            results=results,
            errors=errors,
            transaction_status="preflight_failed",
            assertions=assertion_results,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2
    if not args.write:
        payload = payload_for(
            manifest_path=manifest_path,
            version=version,
            write=False,
            operation_count=len(operations),
            results=results,
            errors=[],
            transaction_status="dry_run",
            assertions=assertion_results,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    status, exit_code, transaction_errors, transaction_log = apply_transaction(
        vault_root,
        states,
        results,
        user_backup=bool(args.backup),
        resource_audits=resource_audits,
        reference_client=reference_client,
    )
    payload = payload_for(
        manifest_path=manifest_path,
        version=version,
        write=True,
        operation_count=len(operations),
        results=results,
        errors=transaction_errors,
        transaction_status=status,
        transaction_log=transaction_log,
        assertions=assertion_results,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    code = main()
    flush_observer()
    raise SystemExit(code)
