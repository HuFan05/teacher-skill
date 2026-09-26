from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Any

from _common import (
    DEFAULT_DB_PATH,
    DEFAULT_KB_ROOT,
    DEFAULT_VAULT_ROOT,
    configure_stdio,
)
from _observer import flush as flush_observer
from _observer import observed
from _observer import phase


SCHEMA_VERSION = 2
DEFAULT_QUERY_MAX_TOTAL_CHARS = 20000
DEFAULT_EXACT_NOTE_MAX_TOTAL_CHARS = 30000
DEFAULT_MAX_RESPONSE_CHARS = 64000
MAX_RESPONSE_CHARS = 256000
SMALL_READ_MAX_NOTES = 3
SMALL_READ_MAX_SECTIONS_PER_NOTE = 3
SMALL_READ_MAX_TOTAL_CHARS = 12000
DEFAULT_MAX_CHARS_PER_SECTION = 1200


class InputValidationError(ValueError):
    """Raised before any retrieval subprocess is allowed to run."""


class IndexRefreshError(RuntimeError):
    """Raised when an automatic refresh was attempted but did not complete."""

    def __init__(self, message: str, *, refresh: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.refresh = refresh or index_refresh_payload(
            attempted=True,
            refreshed=False,
            reason="refresh_failed_query_not_executed",
        )


def build_parser() -> argparse.ArgumentParser:
    parser = SafeArgumentParser(
        description="Recall relevant notes from the local Obsidian vault.",
    )
    parser.add_argument("--max-response-chars", type=int, default=DEFAULT_MAX_RESPONSE_CHARS,
                        help="Whole serialized response ceiling, 1024..256000 characters; default 64000. This is not a cumulative task limit.")
    mode = parser.add_mutually_exclusive_group(required=False)
    mode.add_argument("--query", help="Topic, question, or keyword query.")
    mode.add_argument("--note", help="Exact note title or relative path.")
    parser.add_argument(
        "--status-only",
        action="store_true",
        help="Only report local index status. Does not run query/show or emit note content.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report planned retrieval actions and index status without running query/show.",
    )
    parser.add_argument("--anchor", help="Optional anchor note for graph-aware retrieval.")
    parser.add_argument("--section", help="Optional section filter for direct note inspection.")
    parser.add_argument(
        "--read-package",
        choices=("metadata", "small", "custom"),
        default="metadata",
        help=(
            "Bounded read package. metadata returns metadata unless explicit content "
            "flags are supplied; small expands at most 3 notes x 3 sections under a shared "
            "12000-character budget; custom uses the explicit limits."
        ),
    )
    parser.add_argument("--limit", type=int, default=5, help="Maximum query matches to return.")
    parser.add_argument(
        "--expand",
        type=int,
        default=0,
        help="After explicit permission, how many distinct notes to expand after a query.",
    )
    parser.add_argument(
        "--section-limit",
        type=int,
        default=3,
        help="Maximum sections to include for each expanded note.",
    )
    parser.add_argument(
        "--link-depth",
        type=int,
        choices=(1, 2),
        default=1,
        help="Depth for linked-neighbor expansion.",
    )
    parser.add_argument(
        "--links-limit",
        type=int,
        default=6,
        help="Maximum linked-note metadata entries to include.",
    )
    parser.add_argument(
        "--max-chars-per-section",
        type=int,
        default=None,
        help=(
            "Maximum characters to emit for each selected section excerpt. Defaults to the "
            "shared 12000-character budget for small and 1200 otherwise."
        ),
    )
    parser.add_argument(
        "--max-total-chars",
        type=int,
        default=None,
        help=(
            "Shared maximum body characters for the entire response. Defaults to 30000 for "
            "an exact note, 12000 for --read-package small, and 20000 otherwise."
        ),
    )
    parser.add_argument(
        "--whole-note",
        action="store_true",
        help="After explicit content_read permission, include the whole note up to --max-total-chars.",
    )
    parser.add_argument(
        "--include-snippets",
        action="store_true",
        help="Include query result snippets from the index output.",
    )
    parser.add_argument(
        "--include-links",
        action="store_true",
        help="After explicit permission, include linked-neighbor metadata for expanded notes.",
    )
    parser.add_argument(
        "--no-linked-metadata",
        action="store_true",
        help="For --note, omit direct linked-note metadata.",
    )
    parser.add_argument(
        "--include-local-paths",
        action="store_true",
        help="Include local absolute paths and internal kb/db paths in JSON output.",
    )
    parser.add_argument(
        "--no-auto-refresh",
        action="store_true",
        help="Do not refresh a stale index before query mode (diagnostic use only).",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Accepted without effect; errors remain minimized and never include raw command output.",
    )
    parser.add_argument(
        "--kb-root",
        default=str(DEFAULT_KB_ROOT),
        help="Root of the obsidian_local_kb project.",
    )
    parser.add_argument(
        "--vault-root",
        default=str(DEFAULT_VAULT_ROOT),
        help="Root of the Obsidian vault.",
    )
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB_PATH),
        help="SQLite database path for the note index.",
    )
    return parser


def absolute_note_path(vault_root: Path, relative_path: str) -> str:
    return str((vault_root / Path(relative_path)).resolve())


def add_absolute_path_if_allowed(
    payload: dict[str, Any],
    vault_root: Path,
    relative_path: str,
    include_local_paths: bool,
) -> dict[str, Any]:
    if include_local_paths:
        payload["absolute_path"] = absolute_note_path(vault_root, relative_path)
    return payload


def collapse_whitespace(text: str) -> str:
    return " ".join(text.split())


def truncate_text(text: str, max_chars: int) -> str:
    collapsed = collapse_whitespace(text)
    if max_chars <= 0:
        return ""
    if len(collapsed) <= max_chars:
        return collapsed
    if max_chars == 1:
        return "…"
    return collapsed[: max_chars - 1].rstrip() + "…"


def truncate_preserving_whitespace(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    if max_chars == 1:
        return "…"
    return text[: max_chars - 1] + "…"


def requested_mode(args: argparse.Namespace) -> str:
    if args.status_only:
        return "status"
    if args.query is not None:
        return "query"
    if args.note is not None:
        return "note"
    return "unknown"


@observed("obsidian.retrieval.validate")
def validate_arguments(args: argparse.Namespace) -> None:
    numeric_fields = {
        "limit": args.limit,
        "expand": args.expand,
        "section_limit": args.section_limit,
        "links_limit": args.links_limit,
    }
    if args.max_chars_per_section is not None:
        numeric_fields["max_chars_per_section"] = args.max_chars_per_section
    if args.max_total_chars is not None:
        numeric_fields["max_total_chars"] = args.max_total_chars
    negative = [name for name, value in numeric_fields.items() if value < 0]
    if negative:
        raise InputValidationError(
            "numeric arguments must be non-negative: " + ", ".join(sorted(negative))
        )

    if args.read_package == "small":
        if args.expand > SMALL_READ_MAX_NOTES:
            raise InputValidationError(
                f"--read-package small permits at most {SMALL_READ_MAX_NOTES} expanded notes"
            )
        if args.section_limit > SMALL_READ_MAX_SECTIONS_PER_NOTE:
            raise InputValidationError(
                "--read-package small permits at most "
                f"{SMALL_READ_MAX_SECTIONS_PER_NOTE} sections per note"
            )
        if (
            args.max_total_chars is not None
            and args.max_total_chars > SMALL_READ_MAX_TOTAL_CHARS
        ):
            raise InputValidationError(
                f"--read-package small permits at most {SMALL_READ_MAX_TOTAL_CHARS} body characters"
            )
        if (
            args.max_chars_per_section is not None
            and args.max_chars_per_section > SMALL_READ_MAX_TOTAL_CHARS
        ):
            raise InputValidationError(
                "--read-package small cannot assign one section more than its shared "
                f"{SMALL_READ_MAX_TOTAL_CHARS}-character budget"
            )
        if args.whole_note:
            raise InputValidationError(
                "--whole-note is incompatible with --read-package small; use custom"
            )


def effective_limits(args: argparse.Namespace) -> dict[str, int]:
    mode = requested_mode(args)
    if args.max_total_chars is not None:
        max_total_chars = args.max_total_chars
    elif mode == "note":
        max_total_chars = DEFAULT_EXACT_NOTE_MAX_TOTAL_CHARS
    elif args.read_package == "small":
        max_total_chars = SMALL_READ_MAX_TOTAL_CHARS
    else:
        max_total_chars = DEFAULT_QUERY_MAX_TOTAL_CHARS

    expand = args.expand
    if mode == "query" and args.read_package == "small" and expand == 0:
        expand = SMALL_READ_MAX_NOTES

    if args.max_chars_per_section is not None:
        max_chars_per_section = args.max_chars_per_section
    elif args.read_package == "small":
        max_chars_per_section = SMALL_READ_MAX_TOTAL_CHARS
    else:
        max_chars_per_section = DEFAULT_MAX_CHARS_PER_SECTION

    return {
        "expand": expand,
        "section_limit": args.section_limit,
        "links_limit": args.links_limit,
        "max_chars_per_section": max_chars_per_section,
        "max_total_chars": max_total_chars,
    }


def permission_scope_payload(
    args: argparse.Namespace,
    *,
    mode: str,
    content_mode: str,
    limits: dict[str, int] | None = None,
) -> dict[str, Any]:
    resolved = limits or {
        "expand": args.expand,
        "section_limit": args.section_limit,
        "links_limit": args.links_limit,
        "max_chars_per_section": args.max_chars_per_section,
        "max_total_chars": args.max_total_chars,
    }
    effective_package = args.read_package
    package_source = "explicit_or_default"
    if mode == "note":
        effective_package = "exact_note"
        package_source = "exact_note_scope"
    elif args.read_package == "metadata" and (
        args.include_snippets or args.expand > 0 or args.whole_note
    ):
        # Explicit content flags without a named package are treated as an
        # implicit custom package so metadata itself remains body-free.
        effective_package = "custom"
        package_source = "explicit_content_flags"
    return {
        "read_package": effective_package,
        "requested_read_package": args.read_package,
        "package_source": package_source,
        "mode": mode,
        "body_read": content_mode not in {"metadata", "none"},
        "exact_note_scope": mode == "note",
        "max_notes": 1 if mode == "note" else resolved["expand"],
        "max_sections_per_note": resolved["section_limit"],
        "max_chars_per_section": resolved["max_chars_per_section"],
        "max_total_chars": resolved["max_total_chars"],
        "linked_metadata": bool(
            args.include_links or (mode == "note" and not args.no_linked_metadata)
        ),
        "linked_bodies": False,
    }


def index_refresh_payload(
    *,
    attempted: bool = False,
    refreshed: bool = False,
    reason: str,
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "attempted": attempted,
        "refreshed": refreshed,
        "reason": reason,
    }
    if isinstance(result, dict):
        for key in ("schema_version", "added", "modified", "deleted", "unchanged"):
            value = result.get(key)
            if type(value) is int and 0 <= value <= 10000000:
                payload[key] = value
        if type(result.get("rebuilt")) is bool:
            payload["rebuilt"] = result["rebuilt"]
        value = result.get("indexed_at_utc")
        if isinstance(value, str) and re.fullmatch(r"[0-9TZ:+. -]{0,40}", value):
            payload["indexed_at_utc"] = value
        warnings = result.get("warnings")
        if isinstance(warnings, list):
            payload["warning_count"] = len(warnings)
    return payload


def audit_refresh_summary(refresh: dict[str, Any]) -> dict[str, Any]:
    return {
        key: refresh[key]
        for key in (
            "attempted",
            "refreshed",
            "reason",
            "added",
            "modified",
            "deleted",
            "unchanged",
            "rebuilt",
            "indexed_at_utc",
        )
        if key in refresh
    }


def read_audit_payload(
    notes: list[dict[str, Any]],
    refresh: dict[str, Any],
) -> dict[str, Any]:
    merged: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for note in notes:
        relative_path = str(note.get("relative_path") or "")
        if not relative_path:
            continue
        entry = merged.setdefault(
            relative_path,
            {
                "relative_path": relative_path,
                "sections": [],
                "chars": 0,
                "truncated": False,
            },
        )
        for heading in note.get("sections") or []:
            if heading not in entry["sections"]:
                entry["sections"].append(heading)
        entry["chars"] += int(note.get("chars") or 0)
        entry["truncated"] = bool(entry["truncated"] or note.get("truncated"))
    return {
        "notes": list(merged.values()),
        "index_refresh": audit_refresh_summary(refresh),
    }


def run_kb_json(kb_root: Path, command: list[str], debug: bool = False) -> Any:
    full_command = [sys.executable, "-m", "obsidian_local_kb", *command]
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        full_command,
        cwd=str(kb_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    if completed.returncode != 0:
        if not debug:
            raise RuntimeError(
                "obsidian_local_kb command failed. Rerun with --debug for command output."
            )
        raise RuntimeError(
            "obsidian_local_kb command failed.\n"
            f"Command: {' '.join(full_command)}\n"
            f"STDERR:\n{completed.stderr.strip()}\n"
            f"STDOUT:\n{completed.stdout.strip()}"
        )

    stdout = completed.stdout.strip()
    if not stdout:
        if not debug:
            raise RuntimeError(
                "obsidian_local_kb returned no JSON output. Rerun with --debug for command details."
            )
        raise RuntimeError(
            "obsidian_local_kb returned no JSON output.\n"
            f"Command: {' '.join(full_command)}"
        )

    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        if not debug:
            raise RuntimeError(
                "Failed to parse JSON from obsidian_local_kb. Rerun with --debug for raw output."
            ) from exc
        raise RuntimeError(
            "Failed to parse JSON from obsidian_local_kb.\n"
            f"Command: {' '.join(full_command)}\n"
            f"STDOUT:\n{stdout}"
        ) from exc


@observed("obsidian.retrieval.index.status")
def index_status_payload(
    *,
    kb_root: Path,
    db_path: Path,
    vault_root: Path,
    include_local_paths: bool,
    debug: bool,
) -> dict[str, Any]:
    command = [
        "status",
        "--db",
        str(db_path),
        "--vault",
        str(vault_root),
        "--json",
    ]
    if include_local_paths or debug:
        command.append("--include-reindex-command")
    raw = run_kb_json(kb_root, command, debug=debug)
    if not isinstance(raw, dict):
        raise ValueError("Invalid index status object.")
    # The provider is local, but its extra fields are not a return policy.
    payload: dict[str, Any] = {"index_stale": True, "schema_mismatch": True}
    for key in ("db_exists", "index_stale", "schema_mismatch"):
        if type(raw.get(key)) is bool:
            payload[key] = raw[key]
    for key in ("schema_version", "vault_markdown_count", "indexed_note_count",
                "newer_markdown_count", "missing_indexed_count"):
        value = raw.get(key)
        if value is None and key == "schema_version":
            payload[key] = None
        elif type(value) is int and 0 <= value <= 10000000:
            payload[key] = value
    method = raw.get("status_method")
    if isinstance(method, str) and method in {"missing_db", "stored_file_metadata", "db_mtime_fallback"}:
        payload["status_method"] = method
    else:
        payload["status_method"] = "status_unavailable"
    date = raw.get("indexed_at_utc")
    if isinstance(date, str) and re.fullmatch(r"[0-9TZ:+. -]{0,40}", date):
        payload["indexed_at_utc"] = date
    payload["reindex_hint"] = "Run the refresh command before broad retrieval." if payload["index_stale"] else ""
    # Reconstruct an explicitly requested command from configured arguments,
    # never relay an arbitrary command string supplied by the provider.
    if include_local_paths:
        payload["reindex_command"] = f'python -m obsidian_local_kb refresh --vault "{vault_root}" --db "{db_path}"'
    return payload


@observed("obsidian.retrieval.index.refresh.classify")
def refresh_query_index_if_needed(
    *,
    args: argparse.Namespace,
    kb_root: Path,
    db_path: Path,
    vault_root: Path,
    status: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not status.get("index_stale"):
        return status, index_refresh_payload(reason="index_fresh")
    if args.no_auto_refresh:
        return status, index_refresh_payload(reason="disabled_by_no_auto_refresh")

    try:
        refresh_result = run_kb_json(
            kb_root,
            [
                "refresh",
                "--vault",
                str(vault_root),
                "--db",
                str(db_path),
                "--json",
            ],
            debug=args.debug,
        )
        if not isinstance(refresh_result, dict):
            raise TypeError("refresh did not return an object")
    except Exception as exc:
        raise IndexRefreshError(
            "Automatic index refresh failed; the stale query was not executed."
        ) from exc
    refresh_payload = index_refresh_payload(
        attempted=True,
        refreshed=bool(refresh_result.get("refreshed", True)),
        reason="stale_index",
        result=refresh_result,
    )
    try:
        refreshed_status = index_status_payload(
            kb_root=kb_root,
            db_path=db_path,
            vault_root=vault_root,
            include_local_paths=args.include_local_paths,
            debug=args.debug,
        )
        if not isinstance(refreshed_status, dict):
            raise TypeError("post-refresh status did not return an object")
    except Exception as exc:
        failed_audit = dict(refresh_payload)
        failed_audit.update(
            {"refreshed": False, "reason": "post_refresh_status_failed_query_not_executed"}
        )
        raise IndexRefreshError(
            "Index refresh completed, but freshness could not be verified; the query was not executed.",
            refresh=failed_audit,
        ) from exc
    if refreshed_status.get("index_stale"):
        stale_audit = dict(refresh_payload)
        stale_audit.update(
            {"refreshed": False, "reason": "post_refresh_index_still_stale_query_not_executed"}
        )
        raise IndexRefreshError(
            "Index remained stale after refresh; the query was not executed.",
            refresh=stale_audit,
        )
    return refreshed_status, refresh_payload


HEADING_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)\s*$")
FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


@observed("obsidian.retrieval.index.refresh.enumerate")
def _vault_markdown_files(vault_root: Path) -> list[Path]:
    resolved_root = vault_root.resolve()
    files: list[Path] = []
    for file_path in vault_root.rglob("*.md"):
        safe_path = _path_inside_vault(resolved_root, file_path)
        if safe_path is None:
            continue
        rel_parts = safe_path.relative_to(resolved_root).parts
        if any(part.startswith(".") for part in rel_parts):
            continue
        files.append(safe_path)
    return sorted(files, key=lambda item: item.relative_to(resolved_root).as_posix().casefold())


def _path_inside_vault(vault_root: Path, candidate: Path) -> Path | None:
    root = vault_root.resolve()
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return None
    return resolved if resolved.is_file() and resolved.suffix.casefold() == ".md" else None


def _direct_note_path(vault_root: Path, note_spec: str) -> Path | None:
    raw = note_spec.strip().strip('"').replace("\\", "/")
    supplied = Path(raw)
    candidates: list[Path] = []
    if supplied.is_absolute():
        candidates.append(supplied)
        if supplied.suffix.casefold() != ".md":
            candidates.append(supplied.with_suffix(".md"))
    else:
        candidates.append(vault_root / supplied)
        if supplied.suffix.casefold() != ".md":
            candidates.append(vault_root / supplied.with_suffix(".md"))
    for candidate in candidates:
        safe_path = _path_inside_vault(vault_root, candidate)
        if safe_path is not None:
            return safe_path
    return None


def _is_explicit_path_spec(note_spec: str) -> bool:
    raw = note_spec.strip().strip('"')
    supplied = Path(raw)
    return (
        supplied.is_absolute()
        or "/" in raw
        or "\\" in raw
        or supplied.suffix.casefold() == ".md"
    )


def _unquote_yaml_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            decoded = json.loads(value)
            return str(decoded)
        except json.JSONDecodeError:
            return value[1:-1]
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    return value


def current_frontmatter_aliases(file_path: Path) -> set[str]:
    raw = file_path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return set()
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return set()
    end = next(
        (index for index in range(1, len(lines)) if lines[index].strip() in {"---", "..."}),
        None,
    )
    if end is None:
        return set()

    aliases: list[str] = []
    index = 1
    while index < end:
        line = lines[index]
        match = re.match(r"^(aliases?|alias)[ \t]*:[ \t]*(.*)$", line, re.IGNORECASE)
        if not match:
            index += 1
            continue
        inline = match.group(2).strip()
        if inline.startswith("[") and inline.endswith("]"):
            aliases.extend(
                _unquote_yaml_scalar(item)
                for item in inline[1:-1].split(",")
                if item.strip()
            )
        elif inline:
            aliases.append(_unquote_yaml_scalar(inline))
        index += 1
        while index < end and lines[index].startswith((" ", "\t")):
            stripped = lines[index].strip()
            if stripped.startswith("- ") and stripped[2:].strip():
                aliases.append(_unquote_yaml_scalar(stripped[2:]))
            index += 1
    return {alias.strip().casefold() for alias in aliases if alias.strip()}


@observed("obsidian.retrieval.note.resolve")
def resolve_disk_note(
    *,
    kb_root: Path,
    db_path: Path,
    vault_root: Path,
    note_spec: str,
    debug: bool,
    use_index_resolution: bool,
) -> tuple[Path, str]:
    if _is_explicit_path_spec(note_spec):
        direct = _direct_note_path(vault_root, note_spec)
        if direct is None:
            raise FileNotFoundError(f"Explicit note path not found inside the Vault: {note_spec}")
        return direct, "direct_path_disk_content"

    normalized_title = note_spec.strip().strip('"').casefold()
    markdown_files = _vault_markdown_files(vault_root)
    title_matches = [
        file_path for file_path in markdown_files if file_path.stem.casefold() == normalized_title
    ]
    if len(title_matches) > 1:
        choices = [path.relative_to(vault_root).as_posix() for path in title_matches]
        raise InputValidationError(
            f"Ambiguous note title {note_spec!r}; use a relative path: " + ", ".join(choices)
        )
    if len(title_matches) == 1:
        return title_matches[0], "unique_title_disk_content"

    indexed_path: Path | None = None
    if use_index_resolution and db_path.exists():
        try:
            indexed = run_kb_json(
                kb_root,
                ["show", "--db", str(db_path), "--note", note_spec, "--json"],
                debug=debug,
            )
            indexed_path = _direct_note_path(vault_root, str(indexed.get("path") or ""))
        except Exception:
            indexed_path = None

    alias_matches = [
        file_path
        for file_path in markdown_files
        if normalized_title in current_frontmatter_aliases(file_path)
    ]
    if not alias_matches:
        raise FileNotFoundError(f"Note not found on disk: {note_spec}")
    if len(alias_matches) > 1:
        choices = [path.relative_to(vault_root).as_posix() for path in alias_matches]
        raise InputValidationError(
            f"Ambiguous current note alias {note_spec!r}; use a relative path: " + ", ".join(choices)
        )
    alias_path = alias_matches[0]
    if indexed_path is not None and indexed_path == alias_path:
        return alias_path, "index_alias_verified_disk_content"
    return alias_path, "current_alias_disk_content"


def _body_lines_preserving_newlines(text: str) -> list[str]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return lines
    for index in range(1, len(lines)):
        if lines[index].strip() in {"---", "..."}:
            return lines[index + 1 :]
    return lines


@observed("obsidian.retrieval.note.live_read")
def parse_disk_note(vault_root: Path, file_path: Path) -> dict[str, Any]:
    raw = file_path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        relative = file_path.relative_to(vault_root).as_posix()
        raise UnicodeError(f"Note is not valid UTF-8: {relative}") from exc

    body_lines = _body_lines_preserving_newlines(text)
    sections: list[dict[str, Any]] = []
    stack: list[tuple[int, str]] = []
    current_heading: str | None = None
    current_level = 0
    current_lines: list[str] = []
    fence_character: str | None = None
    fence_length = 0

    def flush_section() -> None:
        nonlocal current_lines
        content = "".join(current_lines)
        heading_path = " > ".join(item[1] for item in stack)
        if content or current_heading is not None or not sections:
            sections.append(
                {
                    "heading_path": heading_path,
                    "level": current_level,
                    "content": content,
                    "snippet": collapse_whitespace(content),
                }
            )
        current_lines = []

    for line in body_lines:
        without_eol = line.rstrip("\r\n")
        fence_match = FENCE_RE.match(without_eol)
        if fence_character is not None:
            current_lines.append(line)
            if (
                fence_match
                and fence_match.group(1)[0] == fence_character
                and len(fence_match.group(1)) >= fence_length
                and not without_eol[fence_match.end() :].strip()
            ):
                fence_character = None
                fence_length = 0
            continue
        if fence_match:
            fence_character = fence_match.group(1)[0]
            fence_length = len(fence_match.group(1))
            current_lines.append(line)
            continue
        match = HEADING_RE.match(without_eol)
        if match:
            flush_section()
            level = len(match.group(1))
            heading = re.sub(r"[ \t]+#+[ \t]*$", "", match.group(2)).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, heading))
            current_heading = heading
            current_level = level
            continue
        current_lines.append(line)
    flush_section()

    return {
        "title": file_path.stem,
        "path": file_path.relative_to(vault_root).as_posix(),
        "sections": sections,
    }


def filter_note_sections(
    sections: list[dict[str, Any]],
    section: str | None,
) -> list[dict[str, Any]]:
    if not section:
        return sections
    needle = section.strip().casefold()
    return [
        row
        for row in sections
        if needle in str(row.get("heading_path") or "").casefold()
    ]


def summarize_reasons(results: list[dict[str, Any]]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for item in results:
        for reason in item.get("reasons") or []:
            summary[reason] = summary.get(reason, 0) + 1
    return dict(sorted(summary.items()))


def graph_boost_seen(results: list[dict[str, Any]]) -> bool:
    graph_reason_fragments = (
        "anchor note",
        "graph anchor",
        "direct outlink",
        "direct backlink",
        "two-hop link",
    )
    for item in results:
        reasons = item.get("reasons") or []
        if any(fragment in str(reason) for reason in reasons for fragment in graph_reason_fragments):
            return True
    return False


def retrieval_log_payload(
    *,
    mode: str,
    query: str | None,
    anchor: str | None,
    results: list[dict[str, Any]],
    grouped_note_count: int,
    expanded_count: int,
    snippets_included: bool,
    links_metadata_included: bool,
    local_paths_included: bool,
) -> dict[str, Any]:
    return {
        "original_query": query or "",
        "anchor": anchor,
        "mode": mode,
        "candidate_count": len(results),
        "grouped_note_count": grouped_note_count,
        "expanded_count": expanded_count,
        "snippets_included": snippets_included,
        "links_metadata_included": links_metadata_included,
        "local_paths_included": local_paths_included,
        "graph_boost_seen": graph_boost_seen(results),
        "reason_summary": summarize_reasons(results),
    }


def planned_command(args: argparse.Namespace, *, db_path: Path, vault_root: Path, include_local_paths: bool) -> list[str]:
    path_db = str(db_path) if include_local_paths else "<db>"
    path_vault = str(vault_root) if include_local_paths else "<vault>"
    if args.status_only:
        return [
            "python",
            "-m",
            "obsidian_local_kb",
            "status",
            "--db",
            path_db,
            "--vault",
            path_vault,
            "--json",
        ]
    if args.query is not None:
        command = [
            "python",
            "-m",
            "obsidian_local_kb",
            "query",
            "--db",
            path_db,
            "--query",
            args.query,
            "--limit",
            str(args.limit),
            "--json",
        ]
        if args.anchor:
            command.extend(["--anchor", args.anchor])
        return command
    if args.note is not None:
        command = [
            "python",
            "-m",
            "obsidian_local_kb",
            "show",
            "--db",
            path_db,
            "--note",
            args.note,
            "--json",
        ]
        if args.section:
            command.extend(["--section", args.section])
        return command
    return []


def make_section_preview(
    row: dict[str, Any],
    max_chars: int,
) -> dict[str, Any]:
    source_text = row.get("content") or row.get("snippet") or ""
    excerpt = truncate_text(source_text, max_chars)
    return {
        "heading_path": row.get("heading_path") or "",
        "level": row.get("level", 0),
        "excerpt": excerpt,
        "emitted_chars": len(excerpt),
        "source_chars": len(collapse_whitespace(source_text)),
        "truncated": len(collapse_whitespace(source_text)) > len(excerpt),
    }


def section_index(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "heading_path": row.get("heading_path") or "",
            "level": row.get("level", 0),
            "has_content": bool((row.get("content") or row.get("snippet") or "").strip()),
        }
        for row in sections
    ]


def cap_total_chars(sections: list[dict[str, Any]], max_total_chars: int) -> tuple[list[dict[str, Any]], bool]:
    if max_total_chars <= 0:
        return [], bool(sections)
    remaining = max_total_chars
    capped: list[dict[str, Any]] = []
    truncated = False
    for section in sections:
        excerpt = section.get("excerpt") or ""
        if remaining <= 0:
            truncated = True
            break
        if len(excerpt) > remaining:
            shortened = truncate_text(excerpt, remaining)
            item = dict(section)
            item["excerpt"] = shortened
            item["emitted_chars"] = len(shortened)
            item["truncated"] = True
            capped.append(item)
            truncated = True
            break
        capped.append(section)
        remaining -= len(excerpt)
    if len(capped) < len(sections):
        truncated = True
    return capped, truncated


def select_sections(
    sections: list[dict[str, Any]],
    matched_headings: list[str],
    section_limit: int,
    max_chars: int,
    max_total_chars: int,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()

    def add_section(row: dict[str, Any]) -> None:
        key = (row.get("heading_path") or "", int(row.get("level", 0)))
        if key in seen:
            return
        text = row.get("content") or row.get("snippet") or ""
        if not text.strip():
            return
        selected.append(make_section_preview(row, max_chars=max_chars))
        seen.add(key)

    for row in sections:
        if (row.get("heading_path") or "") == "":
            add_section(row)
            break

    wanted_headings = [heading for heading in matched_headings if heading]
    if wanted_headings:
        for row in sections:
            row_heading = row.get("heading_path") or ""
            for target in wanted_headings:
                if row_heading == target or row_heading.startswith(target + " >"):
                    add_section(row)
                    break
            if len(selected) >= section_limit:
                capped, _truncated = cap_total_chars(selected[:section_limit], max_total_chars)
                return capped

    for row in sections:
        if len(selected) >= section_limit:
            break
        add_section(row)

    capped, _truncated = cap_total_chars(selected[:section_limit], max_total_chars)
    return capped


def whole_note_sections(
    sections: list[dict[str, Any]],
    *,
    max_chars_per_section: int,
    max_total_chars: int,
) -> tuple[list[dict[str, Any]], bool]:
    # max_chars_per_section is part of the shared section-builder signature,
    # but whole-note mode intentionally ignores it. Only the response-wide budget
    # may truncate whole-note content.
    del max_chars_per_section
    source_sections = [
        row
        for row in sections
        if (row.get("content") or row.get("snippet") or "")
    ]
    if max_total_chars <= 0:
        return [], bool(source_sections)

    remaining = max_total_chars
    emitted: list[dict[str, Any]] = []
    truncated = False
    for index, row in enumerate(source_sections):
        source_text = str(row.get("content") or row.get("snippet") or "")
        if remaining <= 0:
            truncated = True
            break
        excerpt = truncate_preserving_whitespace(source_text, remaining)
        item = {
            "heading_path": row.get("heading_path") or "",
            "level": row.get("level", 0),
            "excerpt": excerpt,
            "emitted_chars": len(excerpt),
            "source_chars": len(source_text),
            "truncated": len(source_text) > len(excerpt),
        }
        emitted.append(item)
        remaining -= len(excerpt)
        if item["truncated"]:
            truncated = True
            break
        if remaining == 0 and index + 1 < len(source_sections):
            truncated = True
            break
    if len(emitted) < len(source_sections):
        truncated = True
    return emitted, truncated


@observed("obsidian.retrieval.note.link_metadata")
def summarize_links(
    links: list[dict[str, Any]],
    vault_root: Path,
    links_limit: int,
    include_local_paths: bool,
) -> list[dict[str, Any]]:
    summarized: list[dict[str, Any]] = []
    for link in links[:links_limit]:
        item = {
            "title": link["title"],
            "relative_path": link["path"],
            "depth": link["depth"],
            "direction": link.get("direction") or "",
            "reason": link["reason"],
        }
        summarized.append(
            add_absolute_path_if_allowed(
                item,
                vault_root=vault_root,
                relative_path=link["path"],
                include_local_paths=include_local_paths,
            )
        )
    return summarized


def expand_note(
    *,
    kb_root: Path,
    db_path: Path,
    vault_root: Path,
    note_spec: str,
    matched_results: list[dict[str, Any]],
    matched_headings: list[str],
    section: str | None,
    link_depth: int,
    links_limit: int,
    section_limit: int,
    max_chars: int,
    max_total_chars: int,
    whole_note: bool,
    include_links: bool,
    include_local_paths: bool,
    debug: bool,
    use_index_resolution: bool = True,
) -> dict[str, Any]:
    note_path, resolution_source = resolve_disk_note(
        kb_root=kb_root,
        db_path=db_path,
        vault_root=vault_root,
        note_spec=note_spec,
        debug=debug,
        use_index_resolution=use_index_resolution,
    )
    note_payload = parse_disk_note(vault_root, note_path)
    note_payload["sections"] = filter_note_sections(note_payload["sections"], section)

    links_payload: list[dict[str, Any]] = []
    links_unavailable = False
    if include_links and links_limit > 0:
        try:
            links_command = [
                "links",
                "--db",
                str(db_path),
                "--note",
                note_payload["path"],
                "--depth",
                str(link_depth),
                "--json",
            ]
            links_payload = run_kb_json(kb_root, links_command, debug=debug)
        except Exception:
            # Exact-note reads must remain usable when the note has not reached the
            # index yet. Linked metadata is optional and never authorizes a body read.
            links_unavailable = True

    payload = {
        "title": note_payload["title"],
        "relative_path": note_payload["path"],
        "content_source": "disk",
        "resolution_source": resolution_source,
        "section_index": section_index(note_payload["sections"]),
        "matched_results": matched_results,
    }
    add_absolute_path_if_allowed(
        payload,
        vault_root=vault_root,
        relative_path=note_payload["path"],
        include_local_paths=include_local_paths,
    )
    if include_links:
        payload["links"] = summarize_links(
            links_payload,
            vault_root=vault_root,
            links_limit=links_limit,
            include_local_paths=include_local_paths,
        )
        if links_unavailable:
            payload["linked_metadata_unavailable"] = True
    if whole_note:
        sections_for_whole, truncated = whole_note_sections(
            note_payload["sections"],
            max_chars_per_section=max_chars,
            max_total_chars=max_total_chars,
        )
        payload["whole_note_sections"] = sections_for_whole
        content_sections = sections_for_whole
    else:
        selected = select_sections(
            note_payload["sections"],
            matched_headings=matched_headings,
            section_limit=section_limit,
            max_chars=max_chars,
            max_total_chars=max_total_chars,
        )
        payload["selected_sections"] = selected
        content_sections = selected
        nonempty_source_count = sum(
            1
            for row in note_payload["sections"]
            if str(row.get("content") or row.get("snippet") or "").strip()
        )
        truncated = any(bool(item.get("truncated")) for item in selected)
        if nonempty_source_count > len(selected):
            truncated = True

    payload["emitted_chars"] = sum(
        len(str(item.get("excerpt") or "")) for item in content_sections
    )
    payload["truncated"] = bool(truncated)
    payload["max_total_chars"] = max_total_chars
    return payload


def group_query_results(
    results: list[dict[str, Any]],
    vault_root: Path,
    include_snippets: bool,
    include_local_paths: bool,
) -> list[dict[str, Any]]:
    grouped: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for item in results:
        relative_path = item["path"]
        entry = grouped.setdefault(
            relative_path,
            {
                "title": item["title"],
                "relative_path": relative_path,
                "matched_results": [],
                "matched_headings": [],
            },
        )
        add_absolute_path_if_allowed(
            entry,
            vault_root=vault_root,
            relative_path=relative_path,
            include_local_paths=include_local_paths,
        )
        result_item = {
            "score": item["score"],
            "heading_path": item.get("heading_path") or "",
            "reasons": item.get("reasons") or [],
        }
        # Candidate snippets have one canonical location: note_candidates.
        # Keeping them out of matched_results prevents duplicate body output when
        # the same candidate is expanded.
        entry["matched_results"].append(result_item)
        heading_path = item.get("heading_path") or ""
        if heading_path and heading_path not in entry["matched_headings"]:
            entry["matched_headings"].append(heading_path)
    return list(grouped.values())


@observed("obsidian.retrieval.result.build.candidates")
def query_candidates_with_budget(
    *,
    results: list[dict[str, Any]],
    vault_root: Path,
    include_snippets: bool,
    include_local_paths: bool,
    max_chars: int,
    suppress_snippet_paths: set[str] | None = None,
    allowed_snippet_paths: set[str] | None = None,
) -> tuple[list[dict[str, Any]], int, bool, list[dict[str, Any]]]:
    suppressed = suppress_snippet_paths or set()
    remaining = max_chars
    emitted_chars = 0
    truncated = False
    audits: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for item in results:
        candidate = {
            "score": item["score"],
            "title": item["title"],
            "relative_path": item["path"],
            "heading_path": item.get("heading_path") or "",
            "reasons": item.get("reasons") or [],
        }
        snippet_allowed = allowed_snippet_paths is None or item["path"] in allowed_snippet_paths
        if include_snippets and snippet_allowed and item["path"] not in suppressed:
            source = str(item.get("snippet") or "")
            snippet = truncate_preserving_whitespace(source, remaining)
            candidate["snippet"] = snippet
            snippet_truncated = len(source) > len(snippet)
            emitted = len(snippet)
            emitted_chars += emitted
            remaining -= emitted
            truncated = bool(truncated or snippet_truncated)
            audits.append(
                {
                    "relative_path": item["path"],
                    "sections": [item.get("heading_path") or ""],
                    "chars": emitted,
                    "truncated": snippet_truncated,
                }
            )
        add_absolute_path_if_allowed(
            candidate,
            vault_root=vault_root,
            relative_path=item["path"],
            include_local_paths=include_local_paths,
        )
        candidates.append(candidate)
    return candidates, emitted_chars, truncated, audits


def require_existing_db(db_path: Path, vault_root: Path, debug: bool = False) -> None:
    if db_path.exists():
        return
    if not debug:
        raise FileNotFoundError(
            "Index database not found. Rerun with --debug for resolved local paths and the reindex command."
        )
    reindex_command = (
        f'python -m obsidian_local_kb index --vault "{vault_root}" --db "{db_path}"'
    )
    raise FileNotFoundError(
        f"Index database not found: {db_path}\n"
        f"Run this from {DEFAULT_KB_ROOT}:\n{reindex_command}"
    )


@observed("obsidian.retrieval.result.build")
def build_query_payload(args: argparse.Namespace) -> dict[str, Any]:
    kb_root = Path(args.kb_root).expanduser().resolve()
    vault_root = Path(args.vault_root).expanduser().resolve()
    db_path = Path(args.db).expanduser().resolve()
    limits = effective_limits(args)
    status = safe_index_status(args)
    status, index_refresh = refresh_query_index_if_needed(
        args=args,
        kb_root=kb_root,
        db_path=db_path,
        vault_root=vault_root,
        status=status,
    )
    require_existing_db(db_path, vault_root, debug=args.debug)

    query_command = [
        "query",
        "--db",
        str(db_path),
        "--query",
        args.query,
        "--limit",
        str(args.limit),
    ]
    if args.anchor:
        query_command.extend(["--anchor", args.anchor])
    query_command.append("--json")

    with phase("obsidian.retrieval.query") as query_fields:
        results = run_kb_json(kb_root, query_command, debug=args.debug)
        query_fields["result_count"] = len(results)
    grouped = group_query_results(
        results,
        vault_root=vault_root,
        include_snippets=args.include_snippets,
        include_local_paths=args.include_local_paths,
    )
    requested_groups = grouped[: limits["expand"]]

    note_candidates, candidate_chars, candidate_truncated, snippet_audits = (
        query_candidates_with_budget(
            results=results,
            vault_root=vault_root,
            include_snippets=args.include_snippets,
            include_local_paths=args.include_local_paths,
            max_chars=limits["max_total_chars"],
            suppress_snippet_paths={group["relative_path"] for group in requested_groups},
            allowed_snippet_paths=(
                {group["relative_path"] for group in requested_groups}
                if args.read_package == "small"
                else None
            ),
        )
    )
    remaining = max(0, limits["max_total_chars"] - candidate_chars)
    expanded_notes: list[dict[str, Any]] = []
    expanded_audits: list[dict[str, Any]] = []
    truncated = candidate_truncated
    for group in requested_groups:
        if remaining <= 0:
            truncated = True
            break
        expanded = expand_note(
            kb_root=kb_root,
            db_path=db_path,
            vault_root=vault_root,
            note_spec=group["relative_path"],
            matched_results=group["matched_results"],
            matched_headings=group["matched_headings"],
            section=None,
            link_depth=args.link_depth,
            links_limit=limits["links_limit"],
            section_limit=limits["section_limit"],
            max_chars=limits["max_chars_per_section"],
            max_total_chars=remaining,
            whole_note=args.whole_note,
            include_links=args.include_links,
            include_local_paths=args.include_local_paths,
            debug=args.debug,
            use_index_resolution=False,
        )
        expanded_notes.append(expanded)
        emitted = int(expanded.get("emitted_chars") or 0)
        remaining = max(0, remaining - emitted)
        chosen_sections = expanded.get("whole_note_sections") or expanded.get("selected_sections") or []
        expanded_audits.append(
            {
                "relative_path": expanded["relative_path"],
                "sections": [item.get("heading_path") or "" for item in chosen_sections],
                "chars": emitted,
                "truncated": bool(expanded.get("truncated")),
            }
        )
        truncated = bool(truncated or expanded.get("truncated"))
    if len(expanded_notes) < len(requested_groups):
        truncated = True

    emitted_chars = candidate_chars + sum(
        int(note.get("emitted_chars") or 0) for note in expanded_notes
    )
    if expanded_notes:
        content_mode = "whole_note" if args.whole_note else "selected_sections"
    elif args.include_snippets:
        content_mode = "snippets"
    else:
        content_mode = "metadata"

    payload = {
        "schema_version": SCHEMA_VERSION,
        "ok": True,
        "mode": "query",
        "query": args.query,
        "anchor": args.anchor,
        "content_mode": content_mode,
        "permission_scope": permission_scope_payload(
            args,
            mode="query",
            content_mode=content_mode,
            limits=limits,
        ),
        "index_refresh": index_refresh,
        "index_status": status,
        "retrieval_log": retrieval_log_payload(
            mode="query",
            query=args.query,
            anchor=args.anchor,
            results=results,
            grouped_note_count=len(grouped),
            expanded_count=len(expanded_notes),
            snippets_included=any(bool(item.get("snippet")) for item in note_candidates),
            links_metadata_included=any(bool(note.get("links")) for note in expanded_notes),
            local_paths_included=args.include_local_paths,
        ),
        "note_candidates": note_candidates,
        "expanded_notes": expanded_notes,
        "read_audit": read_audit_payload(
            [*snippet_audits, *expanded_audits],
            index_refresh,
        ),
        "emitted_chars": emitted_chars,
        "truncated": truncated,
    }
    if args.include_local_paths:
        payload.update(
            {
                "kb_root": str(kb_root),
                "vault_root": str(vault_root),
                "db_path": str(db_path),
            }
        )
    return payload


@observed("obsidian.retrieval.result.build")
def build_note_payload(args: argparse.Namespace) -> dict[str, Any]:
    kb_root = Path(args.kb_root).expanduser().resolve()
    vault_root = Path(args.vault_root).expanduser().resolve()
    db_path = Path(args.db).expanduser().resolve()
    limits = effective_limits(args)
    status = safe_index_status(args)
    index_refresh = index_refresh_payload(reason="exact_note_uses_disk_content")

    expanded_note = expand_note(
        kb_root=kb_root,
        db_path=db_path,
        vault_root=vault_root,
        note_spec=args.note,
        matched_results=[],
        matched_headings=[],
        section=args.section,
        link_depth=args.link_depth,
        links_limit=limits["links_limit"],
        section_limit=limits["section_limit"],
        max_chars=limits["max_chars_per_section"],
        max_total_chars=limits["max_total_chars"],
        whole_note=args.whole_note,
        include_links=args.include_links or not args.no_linked_metadata,
        include_local_paths=args.include_local_paths,
        debug=args.debug,
    )
    content_mode = "whole_note" if args.whole_note else "selected_sections"
    content_sections = (
        expanded_note.get("whole_note_sections")
        or expanded_note.get("selected_sections")
        or []
    )
    audit_notes = [
        {
            "relative_path": expanded_note["relative_path"],
            "sections": [item.get("heading_path") or "" for item in content_sections],
            "chars": int(expanded_note.get("emitted_chars") or 0),
            "truncated": bool(expanded_note.get("truncated")),
        }
    ]

    payload = {
        "schema_version": SCHEMA_VERSION,
        "ok": True,
        "mode": "note",
        "note": args.note,
        "section": args.section,
        "content_mode": content_mode,
        "permission_scope": permission_scope_payload(
            args,
            mode="note",
            content_mode=content_mode,
            limits=limits,
        ),
        "index_refresh": index_refresh,
        "index_status": status,
        "retrieval_log": retrieval_log_payload(
            mode="note",
            query=None,
            anchor=args.anchor,
            results=[],
            grouped_note_count=1,
            expanded_count=1,
            snippets_included=False,
            links_metadata_included=bool(expanded_note.get("links")),
            local_paths_included=args.include_local_paths,
        ),
        "whole_note_requested": bool(args.whole_note),
        "expanded_notes": [expanded_note],
        "read_audit": read_audit_payload(audit_notes, index_refresh),
        "emitted_chars": int(expanded_note.get("emitted_chars") or 0),
        "truncated": bool(expanded_note.get("truncated")),
    }
    if args.include_local_paths:
        payload.update(
            {
                "kb_root": str(kb_root),
                "vault_root": str(vault_root),
                "db_path": str(db_path),
            }
        )
    return payload


def build_status_payload(args: argparse.Namespace) -> dict[str, Any]:
    kb_root = Path(args.kb_root).expanduser().resolve()
    vault_root = Path(args.vault_root).expanduser().resolve()
    db_path = Path(args.db).expanduser().resolve()
    status = index_status_payload(
        kb_root=kb_root,
        db_path=db_path,
        vault_root=vault_root,
        include_local_paths=args.include_local_paths,
        debug=args.debug,
    )
    index_refresh = index_refresh_payload(reason="status_only")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "ok": True,
        "mode": "status",
        "content_mode": "metadata",
        "permission_scope": permission_scope_payload(
            args,
            mode="status",
            content_mode="metadata",
            limits=effective_limits(args),
        ),
        "index_refresh": index_refresh,
        "index_status": status,
        "retrieval_log": retrieval_log_payload(
            mode="status",
            query=None,
            anchor=None,
            results=[],
            grouped_note_count=0,
            expanded_count=0,
            snippets_included=False,
            links_metadata_included=False,
            local_paths_included=args.include_local_paths,
        ),
        "read_audit": read_audit_payload([], index_refresh),
        "emitted_chars": 0,
        "truncated": False,
    }
    if args.include_local_paths:
        payload.update(
            {
                "kb_root": str(kb_root),
                "vault_root": str(vault_root),
                "db_path": str(db_path),
            }
        )
    return payload


def build_dry_run_payload(args: argparse.Namespace) -> dict[str, Any]:
    kb_root = Path(args.kb_root).expanduser().resolve()
    vault_root = Path(args.vault_root).expanduser().resolve()
    db_path = Path(args.db).expanduser().resolve()
    status = index_status_payload(
        kb_root=kb_root,
        db_path=db_path,
        vault_root=vault_root,
        include_local_paths=args.include_local_paths,
        debug=args.debug,
    )
    requested_mode = "status"
    if args.query is not None:
        requested_mode = "query"
    elif args.note is not None:
        requested_mode = "note"
    limits = effective_limits(args)
    index_refresh = index_refresh_payload(
        reason="dry_run_would_refresh_stale_query"
        if requested_mode == "query" and status.get("index_stale") and not args.no_auto_refresh
        else "dry_run_no_refresh",
    )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "ok": True,
        "mode": "dry_run",
        "requested_mode": requested_mode,
        "query": args.query,
        "note": args.note,
        "section": args.section,
        "anchor": args.anchor,
        "permission_relevant_flags": {
            "include_snippets": args.include_snippets,
            "expand": args.expand,
            "include_links": args.include_links,
            "no_linked_metadata": args.no_linked_metadata,
            "include_local_paths": args.include_local_paths,
            "whole_note": args.whole_note,
            "max_chars_per_section": args.max_chars_per_section,
            "max_total_chars": limits["max_total_chars"],
            "read_package": args.read_package,
            "no_auto_refresh": args.no_auto_refresh,
        },
        "content_mode": "none",
        "permission_scope": permission_scope_payload(
            args,
            mode=requested_mode,
            content_mode="none",
            limits=limits,
        ),
        "index_refresh": index_refresh,
        "planned_command": planned_command(
            args,
            db_path=db_path,
            vault_root=vault_root,
            include_local_paths=args.include_local_paths,
        ),
        "index_status": status,
        "retrieval_log": retrieval_log_payload(
            mode="dry_run",
            query=args.query,
            anchor=args.anchor,
            results=[],
            grouped_note_count=0,
            expanded_count=0,
            snippets_included=False,
            links_metadata_included=False,
            local_paths_included=args.include_local_paths,
        ),
        "read_audit": read_audit_payload([], index_refresh),
        "emitted_chars": 0,
        "truncated": False,
    }
    if args.include_local_paths:
        payload.update(
            {
                "kb_root": str(kb_root),
                "vault_root": str(vault_root),
                "db_path": str(db_path),
            }
        )
    return payload


def validate_mode(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    del parser
    selected_modes = sum(
        1
        for selected in (
            args.query is not None,
            args.note is not None,
            args.status_only,
        )
        if selected
    )
    if selected_modes == 0:
        raise InputValidationError("one of --query, --note, or --status-only is required")
    if selected_modes > 1:
        raise InputValidationError("--query, --note, and --status-only are mutually exclusive")


def permission_flags(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "include_snippets": args.include_snippets,
        "expand": args.expand,
        "include_links": args.include_links,
        "no_linked_metadata": args.no_linked_metadata,
        "include_local_paths": args.include_local_paths,
        "whole_note": args.whole_note,
        "section_limit": args.section_limit,
        "max_chars_per_section": args.max_chars_per_section,
        "max_total_chars": args.max_total_chars,
        "read_package": args.read_package,
        "no_auto_refresh": args.no_auto_refresh,
    }


def safe_index_status(args: argparse.Namespace) -> dict[str, Any]:
    kb_root = Path(args.kb_root).expanduser().resolve()
    vault_root = Path(args.vault_root).expanduser().resolve()
    db_path = Path(args.db).expanduser().resolve()
    try:
        return index_status_payload(
            kb_root=kb_root,
            db_path=db_path,
            vault_root=vault_root,
            include_local_paths=args.include_local_paths,
            debug=args.debug,
        )
    except Exception as exc:
        payload: dict[str, Any] = {
            "db_exists": db_path.exists(),
            "status_method": "status_unavailable",
            "indexed_at_utc": "",
            "vault_markdown_count": 0,
            "indexed_note_count": 0,
            "newer_markdown_count": 0,
            "missing_indexed_count": 0,
            "index_stale": True,
            "reindex_hint": "Index status could not be read; refresh or inspect the index before relying on broad retrieval.",
        }
        if args.debug:
            payload["status_error"] = str(exc)
        return payload


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # argparse messages can contain caller values; never echo them.
        raise InputValidationError("Invalid retrieval arguments; consult --help.")


def build_error_payload(args: argparse.Namespace | None, exc: Exception) -> dict[str, Any]:
    # Do not re-query the index while constructing an error: it is unrelated
    # extra work and can introduce another exception or local-data disclosure.
    if isinstance(exc, InputValidationError):
        code, hint = "invalid_request", "Correct retrieval limits or arguments; consult --help."
    elif isinstance(exc, FileNotFoundError):
        code, hint = "source_unavailable", "Locate the requested note or local index before retrying."
    elif isinstance(exc, IndexRefreshError):
        code, hint = "refresh_failed", "Repair the local index refresh before querying; do not infer absence."
    elif isinstance(exc, json.JSONDecodeError):
        code, hint = "invalid_provider_response", "Check the local provider; raw output is not returned."
    else:
        code, hint = "retrieval_failed", "Check the local retrieval provider and requested scope; raw diagnostics are not returned."
    refresh = {"attempted": isinstance(exc, IndexRefreshError), "refreshed": False,
               "reason": "refresh_failed_query_not_executed" if isinstance(exc, IndexRefreshError) else "not_started"}
    if isinstance(exc, IndexRefreshError) and isinstance(exc.refresh, dict):
        allowed_reasons = {"refresh_failed_query_not_executed",
                           "post_refresh_index_still_stale_query_not_executed",
                           "post_refresh_status_failed_query_not_executed"}
        reason = exc.refresh.get("reason")
        if isinstance(reason, str) and reason in allowed_reasons:
            refresh["reason"] = reason
        for key in ("added", "modified", "deleted"):
            value = exc.refresh.get(key)
            if type(value) is int and 0 <= value <= 10000000:
                refresh[key] = value
    error_types = {"invalid_request": "InputValidationError", "source_unavailable": "FileNotFoundError",
                   "refresh_failed": "IndexRefreshError", "invalid_provider_response": "JSONDecodeError",
                   "retrieval_failed": "RuntimeError"}
    mode = requested_mode(args) if args is not None else "unknown"
    if mode not in {"note", "query", "status", "dry_run"}:
        mode = "unknown"
    return {"schema_version": SCHEMA_VERSION, "ok": False, "error": code,
            "mode": mode, "index_refresh": refresh,
            "permission_relevant_flags": {"raw_diagnostics": False},
            "error_type": error_types[code], "content_mode": "none", "emitted_chars": 0,
            "truncated": False, "hint": hint,
            "index_status": {"checked": False, "index_stale": None},
            "coverage": "no_content_returned", "raw_diagnostics_returned": False}


def emit_response(payload: dict[str, Any], limit: int) -> bool:
    # Account for the actual JSON envelope and final newline, not just body text.
    # A stable fixed-point accounts for the decimal length field itself.
    result = dict(payload)
    result["response_limit_chars"] = limit
    result["response_chars"] = 0
    result["response_limit_scope"] = "this_response_only"
    for _ in range(12):
        rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if result["response_chars"] == len(rendered):
            break
        result["response_chars"] = len(rendered)
    if len(rendered) > limit:
        result = {"schema_version": SCHEMA_VERSION, "ok": False,
                  "error": "response_limit_exceeded", "content_mode": "none",
                  "emitted_chars": 0, "truncated": True,
                  "hint": "Select fewer notes, headings or metadata; increase --max-response-chars only for a justified scope already authorized by the task.",
                  "response_limit_chars": limit, "response_limit_scope": "this_response_only",
                  "response_chars": 0}
        for _ in range(12):
            rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
            if result["response_chars"] == len(rendered):
                break
            result["response_chars"] = len(rendered)
        assert len(rendered) <= limit
        print(rendered, end="")
        return False
    print(rendered, end="")
    return True


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(newline="\n")
    args = None
    limit = DEFAULT_MAX_RESPONSE_CHARS
    try:
        parser = build_parser()
        args = parser.parse_args(argv)
        if not 1024 <= args.max_response_chars <= MAX_RESPONSE_CHARS:
            raise InputValidationError("Response limit must be 1024..256000.")
        limit = args.max_response_chars
        # Accept --debug without exposing raw lower-level failures.
        args.debug = False
        validate_mode(args, parser)
        validate_arguments(args)
        if args.dry_run:
            payload = build_dry_run_payload(args)
        elif args.status_only:
            payload = build_status_payload(args)
        else:
            payload = build_query_payload(args) if args.query is not None else build_note_payload(args)
        accepted = emit_response(payload, limit)
    except Exception as exc:
        emit_response(build_error_payload(args, exc), limit)
        flush_observer()
        return 1
    flush_observer()
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
