#!/usr/bin/env python3
"""Small, deterministic Markdown editor for Obsidian Vault notes.

This script is intentionally conservative:
- it only edits existing Markdown files by default;
- every operation is a dry run unless --write is passed;
- local edits preserve unchanged bytes, BOM, and existing line endings;
- normalize is the only operation allowed to rewrite whole-file whitespace;
- it prints compact JSON so agents can inspect results without dumping note text.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

from _common import DEFAULT_VAULT_ROOT
from _observer import flush as flush_observer
from _observer import observed
from _observer import phase
from resource_reference_guard import (
    HarnessClient,
    ResourceReferenceError,
    preflight_resource_references,
    synchronize_resource_references,
)
from validate_obsidian_formulas import tex_transport_shadow_counts


OPERATIONS = (
    "check",
    "normalize",
    "insert-before-heading",
    "insert-after-heading",
    "prepend-section",
    "append-section",
    "replace-section",
    "delete-section",
    "rename-heading",
    "set-frontmatter",
    "add-frontmatter-list-item",
    "delete-frontmatter-key",
    "replace-text",
)

HEADING_RE = re.compile(r"^[ ]{0,3}(#{1,6})[ \t]+(.+?)[ \t]*$")
FENCE_OPEN_RE = re.compile(r"^[ ]{0,3}(`{3,}|~{3,}).*$")
RAW_ARGV: list[str] = []


def print_json(data: dict[str, object]) -> None:
    # Terminal transport metadata never upgrades a failed or uncertain write.
    result = dict(data)
    if not data.get("error") and type(data.get("applied")) is bool:
        result["terminal_state"] = "success"
    elif data.get("applied") is False and (
        data.get("expected_sha256_ok") is False
        or data.get("expected_mtime_ok") is False
        or data.get("race_check") == "failed"
    ):
        result["terminal_state"] = "blocked"
    else:
        result["terminal_state"] = "unresolved"
    print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))


def infer_cli_context(argv: Sequence[str]) -> tuple[str | None, str | None, bool]:
    operation: str | None = None
    file_arg: str | None = None
    write = False
    for idx, token in enumerate(argv):
        if operation is None and token in OPERATIONS:
            operation = token
        if token == "--write":
            write = True
        if token == "--file" and idx + 1 < len(argv):
            file_arg = argv[idx + 1]
        elif token.startswith("--file="):
            file_arg = token.split("=", 1)[1]
    return operation, file_arg, write


def error_result(message: str, *, argv: Sequence[str] | None = None, args: argparse.Namespace | None = None, path: Path | None = None, matches: Sequence["Heading"] | None = None, warnings: Sequence[str] | None = None) -> dict[str, object]:
    operation, file_arg, write = infer_cli_context(argv if argv is not None else RAW_ARGV)
    if args is not None:
        operation = getattr(args, "operation", operation)
        write = bool(getattr(args, "write", write))
        file_arg = getattr(args, "file", file_arg)
    if path is not None:
        file_arg = str(path)
    warning_list = list(warnings or [message])
    return {
        "operation": operation,
        "file": file_arg,
        "changed": False,
        "inserted_lines": 0,
        "removed_lines": 0,
        "heading_matches": [h.as_json() for h in (matches or [])],
        "warnings": warning_list,
        "backup_path": None,
        "line_ending": None,
        "trailing_whitespace_count": None,
        "had_bom": None,
        "write": write,
        "applied": False,
        "pre_sha256": None,
        "post_sha256": None,
        "race_check": "not_run",
        "semantic_change_possible": operation == "normalize",
        "resource_references": None,
        "error": message,
    }


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        print_json(error_result(message, argv=RAW_ARGV))
        raise SystemExit(2)


class VaultEditError(Exception):
    def __init__(self, message: str, *, exit_code: int = 2, matches: Sequence["Heading"] | None = None, warnings: Sequence[str] | None = None) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.matches = list(matches or [])
        self.warnings = list(warnings or [message])


@dataclass(frozen=True)
class Heading:
    line_index: int
    line_number: int
    level: int
    title: str
    raw: str

    def as_json(self) -> dict[str, object]:
        return {
            "line": self.line_number,
            "level": self.level,
            "title": self.title,
            "raw": self.raw,
        }


@dataclass(frozen=True)
class FileSnapshot:
    raw: bytes
    text: str
    sha256: str
    mtime: float
    line_ending: str
    had_bom: bool


@dataclass(frozen=True)
class LogicalDocument:
    original: str
    logical: str
    boundaries: tuple[int, ...]


@dataclass(frozen=True)
class TextEdit:
    start: int
    end: int
    replacement: str


def detect_line_ending(raw: bytes) -> str:
    if not raw:
        return "none"
    crlf = raw.count(b"\r\n")
    without_crlf = raw.replace(b"\r\n", b"")
    lf = without_crlf.count(b"\n")
    cr = without_crlf.count(b"\r")
    kinds = sum(1 for n in (crlf, lf, cr) if n)
    if kinds > 1:
        return "mixed"
    if crlf:
        return "crlf"
    if lf:
        return "lf"
    if cr:
        return "cr"
    return "none"


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


@observed("obsidian.edit.snapshot")
def read_snapshot(path: Path) -> FileSnapshot:
    raw = path.read_bytes()
    line_ending = detect_line_ending(raw)
    had_bom = raw.startswith(b"\xef\xbb\xbf")
    payload = raw[3:] if had_bom else raw
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise VaultEditError(f"File is not valid UTF-8: {exc}") from exc
    return FileSnapshot(
        raw=raw,
        text=text,
        sha256=sha256_bytes(raw),
        mtime=path.stat().st_mtime,
        line_ending=line_ending,
        had_bom=had_bom,
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def introduced_tex_transport_shadows(old_text: str, new_text: str) -> dict[str, int]:
    before = tex_transport_shadow_counts(old_text)
    after = tex_transport_shadow_counts(new_text)
    return {
        name: count - before.get(name, 0)
        for name, count in after.items()
        if count > before.get(name, 0)
    }


def normalize_text(text: str, *, trim_trailing: bool = True) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if trim_trailing:
        text = "\n".join(line.rstrip(" \t") for line in text.split("\n"))
    return text


def build_logical_document(text: str) -> LogicalDocument:
    logical: list[str] = []
    boundaries = [0]
    index = 0
    while index < len(text):
        if text[index] == "\r":
            index += 2 if index + 1 < len(text) and text[index + 1] == "\n" else 1
            logical.append("\n")
        else:
            logical.append(text[index])
            index += 1
        boundaries.append(index)
    return LogicalDocument(text, "".join(logical), tuple(boundaries))


def nearby_line_ending(document: LogicalDocument, start: int, end: int) -> str:
    physical_start = document.boundaries[start]
    physical_end = document.boundaries[end]
    inside = re.search(r"\r\n|\r|\n", document.original[physical_start:physical_end])
    if inside:
        return inside.group(0)
    after = re.search(r"\r\n|\r|\n", document.original[physical_start:])
    if after:
        return after.group(0)
    before = list(re.finditer(r"\r\n|\r|\n", document.original[:physical_start]))
    return before[-1].group(0) if before else "\n"


def apply_text_edits(old_text: str, edits: Sequence[TextEdit]) -> str:
    if not edits:
        return old_text
    document = build_logical_document(old_text)
    ordered = sorted(edits, key=lambda edit: (edit.start, edit.end))
    cursor = 0
    rendered: list[str] = []
    for edit in ordered:
        if edit.start < cursor or edit.end < edit.start or edit.end > len(document.logical):
            raise VaultEditError("Internal edit spans overlap or fall outside the current snapshot.")
        rendered.append(document.original[document.boundaries[cursor] : document.boundaries[edit.start]])
        line_ending = nearby_line_ending(document, edit.start, edit.end)
        rendered.append(normalize_text(edit.replacement, trim_trailing=False).replace("\n", line_ending))
        cursor = edit.end
    rendered.append(document.original[document.boundaries[cursor] :])
    return "".join(rendered)


def find_text_edits(logical: str, needle: str, replacement: str, *, replace_all: bool) -> list[TextEdit]:
    positions: list[int] = []
    cursor = 0
    while True:
        position = logical.find(needle, cursor)
        if position < 0:
            break
        positions.append(position)
        cursor = position + len(needle)
        if not replace_all:
            break
    return [TextEdit(position, position + len(needle), replacement) for position in positions]


def line_offset(lines: Sequence[str], final_newline: bool, index: int) -> int:
    if index < 0 or index > len(lines):
        raise VaultEditError("Internal line span is outside the current snapshot.")
    offset = 0
    for line_index in range(index):
        offset += len(lines[line_index])
        if line_index < len(lines) - 1 or final_newline:
            offset += 1
    return offset


def line_span_edit(
    logical: str,
    lines: Sequence[str],
    final_newline: bool,
    *,
    start_line: int,
    end_line: int,
    desired: str,
) -> TextEdit:
    start = line_offset(lines, final_newline, start_line)
    end = line_offset(lines, final_newline, end_line)
    prefix = logical[:start]
    suffix = logical[end:]
    if not desired.startswith(prefix) or (suffix and not desired.endswith(suffix)):
        raise VaultEditError("Internal edit escaped its resolved target span.")
    replacement_end = len(desired) - len(suffix) if suffix else len(desired)
    return TextEdit(start, end, desired[len(prefix) : replacement_end])


def split_body(text: str) -> tuple[list[str], bool]:
    had_final_newline = text.endswith("\n")
    body = text[:-1] if had_final_newline else text
    if body == "":
        return [], had_final_newline
    return body.split("\n"), had_final_newline


def split_frontmatter(lines: Sequence[str]) -> tuple[list[str], list[str], list[str]]:
    if not lines or lines[0] != "---":
        return [], [], list(lines)
    for idx in range(1, len(lines)):
        if lines[idx] in {"---", "..."}:
            return list(lines[: idx + 1]), list(lines[1:idx]), list(lines[idx + 1 :])
    return [], [], list(lines)


def build_frontmatter(body_lines: Sequence[str]) -> list[str]:
    return ["---", *body_lines, "---"]


def join_body(lines: Sequence[str], *, final_newline: bool) -> str:
    text = "\n".join(lines)
    if final_newline:
        text += "\n"
    return text


def count_trailing_whitespace(text: str) -> int:
    return sum(1 for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n") if re.search(r"[ \t]+$", line))


def opening_fence(line: str) -> tuple[str, int] | None:
    match = FENCE_OPEN_RE.match(line)
    if not match:
        return None
    marker = match.group(1)
    return marker[0], len(marker)


def closes_fence(line: str, fence_char: str, fence_len: int) -> bool:
    escaped = re.escape(fence_char)
    return bool(re.match(rf"^[ ]{{0,3}}{escaped}{{{fence_len},}}[ \t]*$", line))


def clean_heading_title(raw_title: str) -> str:
    title = raw_title.strip()
    title = re.sub(r"[ \t]+#+[ \t]*$", "", title)
    return title.strip()


def iter_headings(lines: Sequence[str]) -> Iterable[Heading]:
    fence_char: str | None = None
    fence_len = 0
    for idx, line in enumerate(lines):
        if fence_char is not None:
            if closes_fence(line, fence_char, fence_len):
                fence_char = None
                fence_len = 0
            continue
        fence = opening_fence(line)
        if fence is not None:
            fence_char, fence_len = fence
            continue
        match = HEADING_RE.match(line)
        if not match:
            continue
        title = clean_heading_title(match.group(2))
        yield Heading(idx, idx + 1, len(match.group(1)), title, line)


def heading_matches(heading: Heading, needle: str, *, level: int | None, mode: str) -> bool:
    if level is not None and heading.level != level:
        return False
    needle = needle.strip()
    haystacks = (heading.raw.strip(), heading.title)
    if mode == "contains":
        return any(needle in haystack for haystack in haystacks)
    return any(needle == haystack for haystack in haystacks)


def select_heading(args: argparse.Namespace, lines: Sequence[str]) -> tuple[Heading, list[Heading], list[str]]:
    warnings: list[str] = []
    matches = [
        h
        for h in iter_headings(lines)
        if heading_matches(h, args.heading, level=getattr(args, "heading_level", None), mode=getattr(args, "heading_match", "exact"))
    ]
    if not matches:
        raise VaultEditError(f"Heading not found: {args.heading!r}", matches=matches)
    occurrence = getattr(args, "occurrence", None)
    if occurrence is None:
        if len(matches) > 1:
            raise VaultEditError(f"Heading is ambiguous: {len(matches)} matches. Pass --occurrence N.", matches=matches)
        return matches[0], matches, warnings
    if occurrence < 1 or occurrence > len(matches):
        raise VaultEditError(f"--occurrence {occurrence} is outside 1..{len(matches)}", matches=matches)
    if len(matches) > 1:
        warnings.append(f"selected heading occurrence {occurrence} of {len(matches)}")
    return matches[occurrence - 1], matches, warnings


def reject_duplicate_inserted_headings(args: argparse.Namespace, existing_lines: Sequence[str], insert_text: str) -> None:
    if getattr(args, "allow_duplicate_heading", False):
        return
    inserted_lines, _final_newline = split_body(normalize_text(insert_text))
    inserted = list(iter_headings(inserted_lines))
    if not inserted:
        return
    existing = {(heading.level, heading.title) for heading in iter_headings(existing_lines)}
    duplicates = [
        f"{'#' * heading.level} {heading.title}"
        for heading in inserted
        if (heading.level, heading.title) in existing
    ]
    if duplicates:
        raise VaultEditError(
            "Inserted text contains a heading that already exists. Pass --allow-duplicate-heading to confirm: "
            + "; ".join(duplicates)
        )


def find_section_end(lines: Sequence[str], heading: Heading) -> int:
    for h in iter_headings(lines[heading.line_index + 1 :]):
        absolute_idx = heading.line_index + 1 + h.line_index
        if h.level <= heading.level:
            return absolute_idx
    return len(lines)


def text_source(args: argparse.Namespace, *, required: bool = True) -> str:
    sources = [bool(getattr(args, "text", None) is not None), bool(getattr(args, "text_file", None)), bool(getattr(args, "stdin", False))]
    if sum(sources) > 1:
        raise VaultEditError("Use only one of --text, --text-file, or --stdin.")
    if getattr(args, "text", None) is not None:
        return normalize_text(args.text)
    if getattr(args, "text_file", None):
        path = Path(args.text_file)
        try:
            return normalize_text(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError) as exc:
            raise VaultEditError(f"Could not read --text-file as UTF-8: {exc}") from exc
    if getattr(args, "stdin", False):
        return normalize_text(sys.stdin.read())
    if required:
        raise VaultEditError("This operation requires --text, --text-file, or --stdin.")
    return ""


def find_source(args: argparse.Namespace) -> str:
    has_find = getattr(args, "find", None) is not None
    has_find_file = getattr(args, "find_file", None) is not None
    if has_find and has_find_file:
        raise VaultEditError("Use only one of --find or --find-file.")
    if has_find:
        return normalize_text(args.find, trim_trailing=False)
    if has_find_file:
        try:
            return normalize_text(
                Path(args.find_file).read_text(encoding="utf-8-sig"),
                trim_trailing=False,
            )
        except (OSError, UnicodeDecodeError) as exc:
            raise VaultEditError(f"Could not read --find-file as UTF-8: {exc}") from exc
    raise VaultEditError("replace-text requires --find or --find-file.")


def block_lines(block: str) -> list[str]:
    block = normalize_text(block).strip("\n")
    if block == "":
        return []
    return block.split("\n")


def require_non_empty_payload(insert: str) -> None:
    if not block_lines(insert):
        raise VaultEditError("Insert payload is empty.")


def frontmatter_key_pattern(key: str) -> re.Pattern[str]:
    return re.compile(rf"^({re.escape(key)})[ \t]*:(.*)$")


def find_frontmatter_key(body_lines: Sequence[str], key: str) -> tuple[int, int] | None:
    pattern = frontmatter_key_pattern(key)
    for idx, line in enumerate(body_lines):
        match = pattern.match(line)
        if not match:
            continue
        end = idx + 1
        while end < len(body_lines) and body_lines[end].startswith((" ", "\t")):
            end += 1
        return idx, end
    return None


def validate_simple_yaml_scalar(value: str) -> None:
    stripped = value.strip()
    if not stripped:
        raise VaultEditError("Complex frontmatter is not supported: empty list item.")
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {"'", '"'}:
        return
    if stripped[0] in "[{|>&*!":
        raise VaultEditError("Complex frontmatter is not supported: flow, block, anchor, alias, or tagged YAML value.")
    if any(char in stripped for char in "[]{}"):
        raise VaultEditError("Complex frontmatter is not supported: flow collections are not editable.")
    if re.search(r"(?:^|[ \t])[&*!][^ \t]+", stripped):
        raise VaultEditError("Complex frontmatter is not supported: anchors, aliases, and YAML tags are not editable.")
    if re.search(r":(?:[ \t]|$)", stripped):
        raise VaultEditError("Complex frontmatter is not supported: nested key/value items are not editable.")


def validate_simple_frontmatter(body_lines: Sequence[str]) -> None:
    """Accept only top-level scalar keys and simple indented scalar lists."""
    active_list = False
    seen_keys: set[str] = set()
    for line in body_lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith((" ", "\t")):
            stripped = line.strip()
            if not active_list or not stripped.startswith("- "):
                raise VaultEditError("Complex frontmatter is not supported: nested mappings are not editable.")
            validate_simple_yaml_scalar(stripped[2:])
            continue
        match = re.match(r"^([^:#][^:]*?):(?:[ \t]*(.*))$", line)
        if not match:
            raise VaultEditError("Complex frontmatter is not supported: expected a top-level key/value pair.")
        key = match.group(1).strip()
        value = match.group(2).strip()
        if key in seen_keys:
            raise VaultEditError(f"Complex frontmatter is not supported: duplicate key {key!r}.")
        seen_keys.add(key)
        if value:
            validate_simple_yaml_scalar(value)
            active_list = False
        else:
            active_list = True


def frontmatter_payload_lines(args: argparse.Namespace, *, require_payload: bool = True) -> list[str]:
    payload = text_source(args, required=require_payload)
    if require_payload and payload.strip() == "":
        raise VaultEditError("Frontmatter value is empty.")
    return block_lines(payload)


def quote_frontmatter_scalar(value: str) -> str:
    if value == "":
        return '""'
    if re.search(r"[:#\[\]\{\},&*!|>'\"%@`]", value) or value != value.strip() or value.lower() in {"true", "false", "null", "~"}:
        return json.dumps(value, ensure_ascii=False)
    return value


def render_frontmatter_assignment(key: str, values: Sequence[str], *, as_list: bool) -> list[str]:
    clean_values = [v.strip() for v in values if v.strip()]
    if as_list:
        return [f"{key}:"] + [f"  - {quote_frontmatter_scalar(v)}" for v in clean_values]
    if not clean_values:
        return [f"{key}: \"\""]
    if len(clean_values) > 1:
        return [f"{key}:"] + [f"  - {quote_frontmatter_scalar(v)}" for v in clean_values]
    return [f"{key}: {quote_frontmatter_scalar(clean_values[0])}"]


def parse_frontmatter_values(block: Sequence[str], key: str) -> list[str]:
    if not block:
        return []
    pattern = frontmatter_key_pattern(key)
    first = pattern.match(block[0])
    if not first:
        return []
    inline = first.group(2).strip()
    values: list[str] = []
    if inline:
        if inline.startswith("[") and inline.endswith("]"):
            items = [item.strip().strip("\"'") for item in inline[1:-1].split(",")]
            values.extend(item for item in items if item)
        else:
            values.append(inline.strip("\"'"))
    for line in block[1:]:
        stripped = line.strip()
        if stripped.startswith("- "):
            values.append(stripped[2:].strip().strip("\"'"))
    return values


def merge_frontmatter(body_lines: Sequence[str], rest: Sequence[str], *, final_newline: bool) -> str:
    if body_lines:
        new_lines = build_frontmatter(body_lines) + list(rest)
    else:
        new_lines = list(rest)
    return join_body(new_lines, final_newline=final_newline)


def first_heading_title(block: str) -> str | None:
    lines, _ = split_body(normalize_text(block))
    return next((h.title for h in iter_headings(lines)), None)


def guard_idempotent(args: argparse.Namespace, text: str, insert: str, warnings: list[str]) -> bool:
    marker = getattr(args, "idempotency_marker", None)
    if marker and marker in text:
        warnings.append("idempotency marker already present; no change")
        return True
    incoming_heading = first_heading_title(insert)
    if incoming_heading and not getattr(args, "allow_duplicate_heading", False):
        lines, _ = split_body(normalize_text(text))
        existing = [h for h in iter_headings(lines) if h.title == incoming_heading]
        if existing:
            warnings.append(f"heading from inserted text already present: {incoming_heading!r}; no change")
            return True
    return False


def block_already_after(lines: Sequence[str], idx: int, insert: str) -> bool:
    block = block_lines(insert)
    if not block:
        return True
    start = idx
    while start < len(lines) and lines[start] == "":
        start += 1
    return list(lines[start : start + len(block)]) == block


def block_already_before(lines: Sequence[str], idx: int, insert: str) -> bool:
    block = block_lines(insert)
    if not block:
        return True
    end = idx
    while end > 0 and lines[end - 1] == "":
        end -= 1
    start = end - len(block)
    return start >= 0 and list(lines[start:end]) == block


def insert_at(lines: list[str], idx: int, insert: str) -> list[str]:
    block = block_lines(insert)
    if not block:
        return lines[:]
    new_lines = list(lines[:idx])
    if new_lines and new_lines[-1] != "":
        new_lines.append("")
    new_lines.extend(block)
    if idx < len(lines) and lines[idx] != "":
        new_lines.append("")
    new_lines.extend(lines[idx:])
    return new_lines


def body_start_index(lines: Sequence[str]) -> int:
    frontmatter, _body, _rest = split_frontmatter(lines)
    if frontmatter:
        return len(frontmatter)
    return 0


def join_with_boundary_gap(prefix: Sequence[str], tail: Sequence[str]) -> list[str]:
    new_lines = list(prefix)
    if new_lines and tail and new_lines[-1] != "" and tail[0] != "":
        new_lines.append("")
    new_lines.extend(tail)
    return new_lines


def diff_stats(old: str, new: str) -> tuple[int, int]:
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    inserted = 0
    removed = 0
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in {"replace", "delete"}:
            removed += i2 - i1
        if tag in {"replace", "insert"}:
            inserted += j2 - j1
    return inserted, removed


def validate_target(path: Path, *, allow_non_md: bool, vault_root: Path, allow_outside_vault: bool) -> None:
    if not path.exists():
        raise VaultEditError(f"File does not exist: {path}")
    if not path.is_file():
        raise VaultEditError(f"Path is not a file: {path}")
    if not allow_non_md and path.suffix.lower() != ".md":
        raise VaultEditError("Refusing to edit non-.md file without --allow-non-md.")
    if not allow_outside_vault:
        try:
            path.resolve().relative_to(vault_root.resolve())
        except ValueError as exc:
            raise VaultEditError(
                f"Refusing to edit a file outside the Vault root: {path}. Pass --allow-outside-vault only for deliberate tests."
            ) from exc


def make_backup(path: Path, raw: bytes) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup = path.with_name(f"{path.name}.{stamp}.bak")
    counter = 1
    while backup.exists():
        backup = path.with_name(f"{path.name}.{stamp}-{counter}.bak")
        counter += 1
    backup.write_bytes(raw)
    return backup


def operation_result(
    args: argparse.Namespace,
    path: Path,
    snapshot: FileSnapshot,
    new_text: str,
    warnings: list[str],
    matches: list[Heading] | None = None,
) -> dict[str, object]:
    inserted, removed = diff_stats(snapshot.text, new_text)
    expected_sha256 = getattr(args, "expect_sha256", None)
    expected_mtime = getattr(args, "expect_mtime", None)
    return {
        "operation": args.operation,
        "file": str(path),
        "changed": snapshot.text != new_text,
        "inserted_lines": inserted,
        "removed_lines": removed,
        "heading_matches": [h.as_json() for h in (matches or [])],
        "warnings": warnings,
        "backup_path": None,
        "sha256": snapshot.sha256,
        "mtime": snapshot.mtime,
        "expected_sha256_ok": None if not expected_sha256 else snapshot.sha256.lower() == expected_sha256.lower(),
        "expected_mtime_ok": None if expected_mtime is None else abs(snapshot.mtime - float(expected_mtime)) < 0.000001,
        "line_ending": snapshot.line_ending,
        "trailing_whitespace_count": count_trailing_whitespace(snapshot.text),
        "had_bom": snapshot.had_bom,
        "write": bool(getattr(args, "write", False)),
        "applied": False,
        "pre_sha256": snapshot.sha256,
        "post_sha256": snapshot.sha256,
        "race_check": "not_run",
        "semantic_change_possible": args.operation == "normalize",
        "resource_references": None,
    }


def handle_prepend_section(
    args: argparse.Namespace,
    normalized: str,
    lines: list[str],
    final_newline: bool,
) -> tuple[str, list[str], list[Heading]]:
    warnings: list[str] = []
    matches: list[Heading] = []
    insert = text_source(args)
    require_non_empty_payload(insert)
    reject_duplicate_inserted_headings(args, lines, insert)
    if guard_idempotent(args, normalized, insert, warnings):
        return normalized, warnings, matches
    idx = body_start_index(lines)
    if block_already_after(lines, idx, insert):
        warnings.append("inserted text already present at target; no change")
        return normalized, warnings, matches
    return join_body(insert_at(lines, idx, insert), final_newline=final_newline), warnings, matches


def handle_insert_near_heading(
    args: argparse.Namespace,
    normalized: str,
    lines: list[str],
    final_newline: bool,
) -> tuple[str, list[str], list[Heading]]:
    warnings: list[str] = []
    insert = text_source(args)
    heading, matches, extra = select_heading(args, lines)
    warnings.extend(extra)
    require_non_empty_payload(insert)
    reject_duplicate_inserted_headings(args, lines, insert)
    if guard_idempotent(args, normalized, insert, warnings):
        return normalized, warnings, matches
    idx = heading.line_index if args.operation == "insert-before-heading" else heading.line_index + 1
    already_present = (
        block_already_before(lines, idx, insert)
        if args.operation == "insert-before-heading"
        else block_already_after(lines, idx, insert)
    )
    if already_present:
        warnings.append("inserted text already present at target; no change")
        return normalized, warnings, matches
    return join_body(insert_at(lines, idx, insert), final_newline=final_newline), warnings, matches


def handle_append_section(args: argparse.Namespace, normalized: str, lines: list[str]) -> tuple[str, list[str], list[Heading]]:
    warnings: list[str] = []
    matches: list[Heading] = []
    insert = text_source(args)
    require_non_empty_payload(insert)
    reject_duplicate_inserted_headings(args, lines, insert)
    if guard_idempotent(args, normalized, insert, warnings):
        return normalized, warnings, matches
    if block_already_before(lines, len(lines), insert):
        warnings.append("inserted text already present at target; no change")
        return normalized, warnings, matches
    return join_body(insert_at(lines, len(lines), insert), final_newline=True), warnings, matches


def handle_rename_heading(
    args: argparse.Namespace,
    lines: list[str],
    final_newline: bool,
) -> tuple[str, list[str], list[Heading]]:
    warnings: list[str] = []
    heading, matches, extra = select_heading(args, lines)
    warnings.extend(extra)
    new_heading = args.new_heading.strip()
    if not new_heading:
        raise VaultEditError("--new-heading must not be empty.", matches=matches)
    if not new_heading.startswith("#"):
        new_heading = f"{'#' * heading.level} {new_heading}"
    new_lines = list(lines)
    new_lines[heading.line_index] = new_heading
    return join_body(new_lines, final_newline=final_newline), warnings, matches


def handle_set_frontmatter(
    args: argparse.Namespace,
    lines: list[str],
    final_newline: bool,
) -> tuple[str, list[str], list[Heading]]:
    warnings: list[str] = []
    matches: list[Heading] = []
    _frontmatter, fm_body, rest = split_frontmatter(lines)
    validate_simple_frontmatter(fm_body)
    values = frontmatter_payload_lines(args)
    replacement = render_frontmatter_assignment(args.key, values, as_list=args.list)
    span = find_frontmatter_key(fm_body, args.key)
    if span:
        start, end = span
        fm_body = fm_body[:start] + replacement + fm_body[end:]
    else:
        fm_body = list(fm_body) + replacement
    return merge_frontmatter(fm_body, rest, final_newline=final_newline), warnings, matches


def handle_add_frontmatter_list_item(
    args: argparse.Namespace,
    lines: list[str],
    final_newline: bool,
) -> tuple[str, list[str], list[Heading]]:
    warnings: list[str] = []
    matches: list[Heading] = []
    _frontmatter, fm_body, rest = split_frontmatter(lines)
    validate_simple_frontmatter(fm_body)
    additions = frontmatter_payload_lines(args)
    span = find_frontmatter_key(fm_body, args.key)
    existing: list[str] = []
    if span:
        start, end = span
        existing = parse_frontmatter_values(fm_body[start:end], args.key)
    merged = list(existing)
    for item in additions:
        item = item.strip()
        if item and item not in merged:
            merged.append(item)
    replacement = render_frontmatter_assignment(args.key, merged, as_list=True)
    if span:
        start, end = span
        fm_body = fm_body[:start] + replacement + fm_body[end:]
    else:
        fm_body = list(fm_body) + replacement
    return merge_frontmatter(fm_body, rest, final_newline=final_newline), warnings, matches


def handle_delete_frontmatter_key(
    args: argparse.Namespace,
    normalized: str,
    lines: list[str],
    final_newline: bool,
) -> tuple[str, list[str], list[Heading]]:
    warnings: list[str] = []
    matches: list[Heading] = []
    frontmatter, fm_body, rest = split_frontmatter(lines)
    if not frontmatter:
        warnings.append("frontmatter not present; no change")
        return normalized, warnings, matches
    validate_simple_frontmatter(fm_body)
    span = find_frontmatter_key(fm_body, args.key)
    if not span:
        warnings.append(f"frontmatter key not present: {args.key!r}; no change")
        return normalized, warnings, matches
    start, end = span
    fm_body = fm_body[:start] + fm_body[end:]
    return merge_frontmatter(fm_body, rest, final_newline=final_newline), warnings, matches


def handle_replace_section(
    args: argparse.Namespace,
    lines: list[str],
    final_newline: bool,
) -> tuple[str, list[str], list[Heading]]:
    warnings: list[str] = []
    replacement = text_source(args, required=False)
    heading, matches, extra = select_heading(args, lines)
    warnings.extend(extra)
    section_end = find_section_end(lines, heading)
    repl = block_lines(replacement)
    tail = lines[section_end:]
    if args.include_heading:
        new_lines = join_with_boundary_gap(lines[: heading.line_index] + repl, tail)
    else:
        new_lines = lines[: heading.line_index + 1]
        if repl:
            new_lines.append("")
            new_lines.extend(repl)
        new_lines = join_with_boundary_gap(new_lines, tail)
    return join_body(new_lines, final_newline=final_newline), warnings, matches


def handle_delete_section(
    args: argparse.Namespace,
    lines: list[str],
    final_newline: bool,
) -> tuple[str, list[str], list[Heading]]:
    warnings: list[str] = []
    heading, matches, extra = select_heading(args, lines)
    warnings.extend(extra)
    section_end = find_section_end(lines, heading)
    if args.keep_heading:
        new_lines = join_with_boundary_gap(lines[: heading.line_index + 1], lines[section_end:])
    else:
        start = heading.line_index
        end = section_end
        if start > 0 and end < len(lines) and lines[start - 1] == "" and lines[end] == "":
            end += 1
        new_lines = lines[:start] + lines[end:]
    return join_body(new_lines, final_newline=final_newline), warnings, matches


def handle_replace_text(args: argparse.Namespace, normalized: str) -> tuple[str, list[str], list[Heading]]:
    warnings: list[str] = []
    matches: list[Heading] = []
    needle = find_source(args)
    replacement = text_source(args, required=False)
    if needle == "":
        raise VaultEditError("Refusing to replace empty text.")
    count = normalized.count(needle)
    if count == 0:
        raise VaultEditError("Text to replace was not found.")
    if count > 1 and not args.all:
        raise VaultEditError(f"Text appears {count} times. Pass --all to replace every occurrence.")
    maxreplace = -1 if args.all else 1
    return normalized.replace(needle, normalize_text(replacement), maxreplace), warnings, matches


@observed("obsidian.edit.transform")
def perform(args: argparse.Namespace, path: Path, old_text: str) -> tuple[str, list[str], list[Heading]]:
    document = build_logical_document(old_text)
    normalized = document.logical
    lines, final_newline = split_body(normalized)
    matches: list[Heading] = []
    warnings: list[str] = []

    if args.operation == "check":
        return old_text, warnings, matches
    if args.operation == "normalize":
        return normalize_text(old_text, trim_trailing=True), warnings, matches

    edits: list[TextEdit] = []
    if args.operation == "prepend-section":
        start_line = body_start_index(lines)
        desired, warnings, matches = handle_prepend_section(args, normalized, lines, final_newline)
        if desired != normalized:
            edits.append(
                line_span_edit(
                    normalized,
                    lines,
                    final_newline,
                    start_line=start_line,
                    end_line=start_line,
                    desired=desired,
                )
            )
    elif args.operation in {"insert-before-heading", "insert-after-heading"}:
        heading, _resolved_matches, _extra = select_heading(args, lines)
        start_line = heading.line_index if args.operation == "insert-before-heading" else heading.line_index + 1
        desired, warnings, matches = handle_insert_near_heading(args, normalized, lines, final_newline)
        if desired != normalized:
            edits.append(
                line_span_edit(
                    normalized,
                    lines,
                    final_newline,
                    start_line=start_line,
                    end_line=start_line,
                    desired=desired,
                )
            )
    elif args.operation == "append-section":
        desired, warnings, matches = handle_append_section(args, normalized, lines)
        if desired != normalized:
            edits.append(
                line_span_edit(
                    normalized,
                    lines,
                    final_newline,
                    start_line=len(lines),
                    end_line=len(lines),
                    desired=desired,
                )
            )
    elif args.operation == "rename-heading":
        heading, _resolved_matches, _extra = select_heading(args, lines)
        desired, warnings, matches = handle_rename_heading(args, lines, final_newline)
        if desired != normalized:
            desired_lines, _desired_final = split_body(desired)
            start = line_offset(lines, final_newline, heading.line_index)
            edits.append(TextEdit(start, start + len(lines[heading.line_index]), desired_lines[heading.line_index]))
    elif args.operation == "set-frontmatter":
        frontmatter, fm_body, _rest = split_frontmatter(lines)
        span = find_frontmatter_key(fm_body, args.key)
        if frontmatter:
            start_line = 1 + span[0] if span else len(frontmatter) - 1
            end_line = 1 + span[1] if span else start_line
        else:
            start_line = end_line = 0
        desired, warnings, matches = handle_set_frontmatter(args, lines, final_newline)
        if desired != normalized:
            edits.append(
                line_span_edit(
                    normalized,
                    lines,
                    final_newline,
                    start_line=start_line,
                    end_line=end_line,
                    desired=desired,
                )
            )
    elif args.operation == "add-frontmatter-list-item":
        frontmatter, fm_body, _rest = split_frontmatter(lines)
        span = find_frontmatter_key(fm_body, args.key)
        if frontmatter:
            start_line = 1 + span[0] if span else len(frontmatter) - 1
            end_line = 1 + span[1] if span else start_line
        else:
            start_line = end_line = 0
        desired, warnings, matches = handle_add_frontmatter_list_item(args, lines, final_newline)
        if desired != normalized:
            edits.append(
                line_span_edit(
                    normalized,
                    lines,
                    final_newline,
                    start_line=start_line,
                    end_line=end_line,
                    desired=desired,
                )
            )
    elif args.operation == "delete-frontmatter-key":
        frontmatter, fm_body, _rest = split_frontmatter(lines)
        span = find_frontmatter_key(fm_body, args.key)
        desired, warnings, matches = handle_delete_frontmatter_key(
            args,
            normalized,
            lines,
            final_newline,
        )
        if desired != normalized and frontmatter and span:
            edits.append(
                line_span_edit(
                    normalized,
                    lines,
                    final_newline,
                    start_line=1 + span[0],
                    end_line=1 + span[1],
                    desired=desired,
                )
            )
    elif args.operation == "replace-section":
        heading, _resolved_matches, _extra = select_heading(args, lines)
        section_end = find_section_end(lines, heading)
        start_line = heading.line_index if args.include_heading else heading.line_index + 1
        desired, warnings, matches = handle_replace_section(args, lines, final_newline)
        if desired != normalized:
            edits.append(
                line_span_edit(
                    normalized,
                    lines,
                    final_newline,
                    start_line=start_line,
                    end_line=section_end,
                    desired=desired,
                )
            )
    elif args.operation == "delete-section":
        heading, _resolved_matches, _extra = select_heading(args, lines)
        section_end = find_section_end(lines, heading)
        if args.keep_heading:
            start_line = heading.line_index + 1
            end_line = section_end
        else:
            start_line = heading.line_index
            end_line = section_end
            if start_line > 0 and end_line < len(lines) and lines[start_line - 1] == "" and lines[end_line] == "":
                end_line += 1
        desired, warnings, matches = handle_delete_section(args, lines, final_newline)
        if desired != normalized:
            edits.append(
                line_span_edit(
                    normalized,
                    lines,
                    final_newline,
                    start_line=start_line,
                    end_line=end_line,
                    desired=desired,
                )
            )
    elif args.operation == "replace-text":
        needle = find_source(args)
        replacement = normalize_text(text_source(args, required=False))
        if needle == "":
            raise VaultEditError("Refusing to replace empty text.")
        count = normalized.count(needle)
        if count == 0:
            raise VaultEditError("Text to replace was not found.")
        if count > 1 and not args.all:
            raise VaultEditError(f"Text appears {count} times. Pass --all to replace every occurrence.")
        edits = find_text_edits(normalized, needle, replacement, replace_all=args.all)
    else:
        raise VaultEditError(f"Unsupported operation: {args.operation}")
    return apply_text_edits(old_text, edits), warnings, matches


def encode_output(text: str, *, had_bom: bool) -> bytes:
    payload = text.encode("utf-8")
    return (b"\xef\xbb\xbf" + payload) if had_bom else payload


def prepare_temp_file(path: Path, payload: bytes) -> Path:
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.tmp")
    temp_path.write_bytes(payload)
    return temp_path


@observed("obsidian.edit.atomic_write")
def write_if_requested(
    args: argparse.Namespace,
    path: Path,
    snapshot: FileSnapshot,
    result: dict[str, object],
    new_text: str,
) -> None:
    if not getattr(args, "write", False):
        return
    if result.get("expected_sha256_ok") is False:
        raise VaultEditError("Refusing to write because --expect-sha256 does not match the current file.")
    if result.get("expected_mtime_ok") is False:
        raise VaultEditError("Refusing to write because --expect-mtime does not match the current file.")
    if not result["changed"]:
        return
    output = encode_output(new_text, had_bom=snapshot.had_bom)
    temp_path = prepare_temp_file(path, output)
    try:
        current_raw = path.read_bytes()
        if sha256_bytes(current_raw) != snapshot.sha256:
            result["race_check"] = "failed"
            raise VaultEditError("Refusing to write because the file changed after the initial snapshot.")
        result["race_check"] = "passed"
        if getattr(args, "backup", False):
            result["backup_path"] = str(make_backup(path, snapshot.raw))
        temp_path.replace(path)
        result["applied"] = True
        result["post_sha256"] = sha256_bytes(output)
        result["mtime"] = path.stat().st_mtime
    finally:
        if temp_path.exists():
            temp_path.unlink()


def add_common_file_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--file", required=True, help="Target Markdown file.")
    parser.add_argument("--vault-root", default=str(DEFAULT_VAULT_ROOT), help="Root of the Obsidian vault.")
    parser.add_argument("--write", action="store_true", help="Actually write changes. Default is dry-run.")
    parser.add_argument("--backup", action="store_true", help="Create a timestamped .bak file before writing.")
    parser.add_argument("--allow-non-md", action="store_true", help="Allow editing non-.md files.")
    parser.add_argument("--allow-outside-vault", action="store_true", help="Allow editing a file outside --vault-root.")
    parser.add_argument("--expect-sha256", help="Refuse --write if the file SHA-256 no longer matches this value.")
    parser.add_argument("--expect-mtime", type=float, help="Refuse --write if the file mtime no longer matches this value.")


def add_text_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--text", help="Text payload.")
    group.add_argument("--text-file", help="UTF-8 file containing the text payload.")
    group.add_argument("--stdin", action="store_true", help="Read text payload from stdin.")


def add_duplicate_heading_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--allow-duplicate-heading",
        action="store_true",
        help="Allow inserted text to contain a heading already present in the target file.",
    )


def add_heading_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--heading", required=True, help="Heading title or full heading line to match.")
    parser.add_argument("--heading-level", type=int, choices=range(1, 7), help="Restrict match to a heading level.")
    parser.add_argument("--heading-match", choices=("exact", "contains"), default="exact", help="Heading match mode.")
    parser.add_argument("--occurrence", type=int, help="Use Nth match when a heading is repeated.")


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(description="Deterministic editor for Obsidian Markdown notes.")
    sub = parser.add_subparsers(dest="operation", required=True, parser_class=JsonArgumentParser)

    for op in ("check", "normalize"):
        p = sub.add_parser(op)
        add_common_file_args(p)

    for op in ("insert-before-heading", "insert-after-heading"):
        p = sub.add_parser(op)
        add_common_file_args(p)
        add_heading_args(p)
        add_text_args(p)
        add_duplicate_heading_arg(p)
        p.add_argument("--idempotency-marker", help="If present in the file, skip insertion.")

    p = sub.add_parser("prepend-section")
    add_common_file_args(p)
    add_text_args(p)
    add_duplicate_heading_arg(p)
    p.add_argument("--idempotency-marker", help="If present in the file, skip prepend.")

    p = sub.add_parser("append-section")
    add_common_file_args(p)
    add_text_args(p)
    add_duplicate_heading_arg(p)
    p.add_argument("--idempotency-marker", help="If present in the file, skip append.")

    p = sub.add_parser("replace-section")
    add_common_file_args(p)
    add_heading_args(p)
    add_text_args(p)
    p.add_argument("--include-heading", action="store_true", help="Replace the heading line too. Default preserves it.")

    p = sub.add_parser("delete-section")
    add_common_file_args(p)
    add_heading_args(p)
    p.add_argument("--keep-heading", action="store_true", help="Delete only the section body.")

    p = sub.add_parser("rename-heading")
    add_common_file_args(p)
    add_heading_args(p)
    p.add_argument("--new-heading", required=True, help="New heading title or full ATX heading line.")

    p = sub.add_parser("set-frontmatter")
    add_common_file_args(p)
    p.add_argument("--key", required=True, help="Frontmatter key to set.")
    add_text_args(p)
    p.add_argument("--list", action="store_true", help="Write the value as a YAML list, one item per payload line.")

    p = sub.add_parser("add-frontmatter-list-item")
    add_common_file_args(p)
    p.add_argument("--key", required=True, help="Frontmatter key to append list items to.")
    add_text_args(p)

    p = sub.add_parser("delete-frontmatter-key")
    add_common_file_args(p)
    p.add_argument("--key", required=True, help="Frontmatter key to delete.")

    p = sub.add_parser("replace-text")
    add_common_file_args(p)
    find_group = p.add_mutually_exclusive_group(required=True)
    find_group.add_argument("--find", help="Exact text to replace.")
    find_group.add_argument("--find-file", help="UTF-8 file containing exact text to replace.")
    add_text_args(p)
    p.add_argument("--all", action="store_true", help="Replace every occurrence.")

    return parser


# Shared output support is resolved from this script, including importlib callers.
import sys as _output_sys
from pathlib import Path as _OutputPath
_output_dir = str(_OutputPath(__file__).resolve().parent)
if _output_dir not in _output_sys.path:
    _output_sys.path.insert(0, _output_dir)
from note_public_output import public_entry

@public_entry('vault_edit')
def main(argv: Sequence[str] | None = None) -> int:
    global RAW_ARGV
    RAW_ARGV = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(RAW_ARGV)
    path = Path(args.file).expanduser().resolve()
    vault_root = Path(args.vault_root).expanduser().resolve()
    try:
        validate_target(
            path,
            allow_non_md=args.allow_non_md,
            vault_root=vault_root,
            allow_outside_vault=args.allow_outside_vault,
        )
        snapshot = read_snapshot(path)
        new_text, warnings, matches = perform(args, path, snapshot.text)
        introduced_shadows = introduced_tex_transport_shadows(snapshot.text, new_text)
        if introduced_shadows:
            names = ", ".join(
                f"{name} (+{count})"
                for name, count in sorted(introduced_shadows.items())
            )
            raise VaultEditError(
                "Refusing edit because it introduces bare formula tokens that shadow TeX control words "
                f"already present in the prospective note: {names}. A transport layer may have removed "
                "backslashes; resend the payload through a raw or literal channel."
            )
        result = operation_result(args, path, snapshot, new_text, warnings, matches)
        harness = HarnessClient()
        try:
            result["resource_references"] = preflight_resource_references(
                snapshot.text,
                new_text,
                note_path=path,
                client=harness,
            )
        except ResourceReferenceError as exc:
            result["resource_references"] = exc.audit
            result["error"] = str(exc)
            result["warnings"] = list(dict.fromkeys([*result["warnings"], str(exc)]))
            print_json(result)
            return exc.exit_code
        try:
            write_if_requested(args, path, snapshot, result, new_text)
        except VaultEditError as exc:
            result["error"] = str(exc)
            result["warnings"] = list(dict.fromkeys([*result["warnings"], *exc.warnings]))
            print_json(result)
            return exc.exit_code
        except OSError as exc:
            result["error"] = f"File operation failed: {exc}"
            result["warnings"] = list(dict.fromkeys([*result["warnings"], result["error"]]))
            print_json(result)
            return 2
        if result["applied"]:
            try:
                with phase("obsidian.edit.verify"):
                    final_snapshot = read_snapshot(path)
            except (VaultEditError, OSError) as exc:
                result["status"] = "reference_sync_pending"
                result["error"] = f"reference_sync_pending: could not read the final note: {exc}"
                result["warnings"] = list(dict.fromkeys([*result["warnings"], result["error"]]))
                result["resource_references"]["sync"] = {
                    "status": "reference_sync_pending",
                    "error": str(exc),
                }
                print_json(result)
                return 3
            result["post_sha256"] = final_snapshot.sha256
            result["mtime"] = final_snapshot.mtime
            synchronized = synchronize_resource_references(
                note_path=path,
                final_text=final_snapshot.text,
                post_sha256=final_snapshot.sha256,
                audit=result["resource_references"],
                client=harness,
            )
            if not synchronized:
                sync = result["resource_references"].get("sync", {})
                detail = sync.get("error") if isinstance(sync, dict) else None
                result["status"] = "reference_sync_pending"
                result["error"] = f"reference_sync_pending: {detail or 'resource reference synchronization failed'}"
                result["warnings"] = list(dict.fromkeys([*result["warnings"], result["error"]]))
                print_json(result)
                return 3
        print_json(result)
        flush_observer()
        return 0
    except VaultEditError as exc:
        print_json(error_result(str(exc), args=args, path=path, matches=exc.matches, warnings=exc.warnings))
        return exc.exit_code
    except OSError as exc:
        print_json(error_result(f"File operation failed: {exc}", args=args, path=path))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
