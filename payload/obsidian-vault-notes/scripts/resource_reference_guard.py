#!/usr/bin/env python3
"""Guard external knowledge-library references in Obsidian Markdown edits.

The module deliberately knows only the public ``manage-personal-knowledge``
CLI.  It never opens the resource registry itself.  A harness supplied through
``MPK_HARNESS`` takes precedence; otherwise the installed sibling skill is
located conservatively.
"""

from __future__ import annotations

import collections
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence
from urllib.parse import unquote, urlsplit

from _observer import observed


RESOURCE_ID_PATTERN = r"KB-[A-Z2-7]{26}"
RESOURCE_MARKER_RE = re.compile(
    rf"[ \t]*<!--\s*mpk-resource:(?P<resource_id>{RESOURCE_ID_PATTERN})\s*-->",
    re.IGNORECASE,
)
ANY_RESOURCE_MARKER_RE = re.compile(
    r"[ \t]*<!--\s*mpk-resource:(?P<resource_id>[^\r\n]*?)\s*-->",
    re.IGNORECASE,
)
MARKER_CANDIDATE_RE = re.compile(
    r"<!--\s*mpk-resource:(?P<resource_id>[^\r\n]*?)(?:-->|$)",
    re.IGNORECASE,
)
MARKDOWN_FILE_LINK_RE = re.compile(
    r"(?P<image>!)?\[[^\]\r\n]*\]\("
    r"(?P<target><file://[^>\r\n]+>|file://[^\s)\r\n]+)"
    r"(?:[ \t]+(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|\([^()\r\n]*\)))?"
    r"\)",
    re.IGNORECASE,
)
HTML_MEDIA_RE = re.compile(
    r"<(?P<tag>audio|video|source|img|track)\b(?P<attrs>[^>]*?)>",
    re.IGNORECASE,
)
HTML_FILE_ATTR_RE = re.compile(
    r"\b(?P<attribute>src|poster)\s*=\s*(?P<quote>['\"])(?P<target>file://.*?)(?P=quote)",
    re.IGNORECASE,
)
RAW_FILE_URI_RE = re.compile(
    r"(?<![A-Za-z0-9+.-])file:(?://)?[^\s<>\[\]()\"']+",
    re.IGNORECASE,
)
WEB_URI_RE = re.compile(
    r"(?<![A-Za-z0-9+.-])https?://[^\s<>\r\n\"']+",
    re.IGNORECASE,
)
WINDOWS_FILE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?P<target>"
    r"(?:[A-Za-z]:[\\/](?:[^\\/:*?\"<>|\r\n]+[\\/])*[^\\/:*?\"<>|\r\n]*"
    r"|\\\\[^\\/:*?\"<>|\r\n]+[\\/][^\\/:*?\"<>|\r\n]+(?:[\\/][^\\/:*?\"<>|\r\n]+)*"
    r"|//[^\\/:*?\"<>|\r\n]+/[^\\/:*?\"<>|\r\n]+(?:/[^\\/:*?\"<>|\r\n]+)*)"
    r"\.[A-Za-z0-9]{1,16})"
)
POSSIBLE_RELATIVE_FILE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_:/\\])(?P<target>(?:[^\s<>\r\n\"'`(){}\[\]:]+[\\/])+"
    r"[^\s<>\r\n\"'`(){}\[\]:]+\.[A-Za-z0-9]{1,16})"
    r"(?=$|[\s)\]},;:!?，。；：！？、])"
)
FENCE_OPEN_RE = re.compile(r"^[ ]{0,3}(?P<fence>`{3,}|~{3,})")
UNCONFIGURED_ERROR_FRAGMENTS = (
    "is not configured",
    "registry is not initialized",
    "configured knowledge root is missing",
    "missing registry settings",
    "configuration file does not exist",
)


class ResourceReferenceError(Exception):
    """A hard reference guard failure that must prevent a note write."""

    def __init__(self, message: str, audit: dict[str, object], *, exit_code: int = 2) -> None:
        super().__init__(message)
        self.audit = audit
        self.exit_code = exit_code


@dataclass(frozen=True)
class ResourceReference:
    kind: str
    target: str
    resource_id: str | None
    line: int
    issue: str | None = None

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.kind, self.target, self.resource_id or self.issue or "")

    def as_json(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "target": self.target,
            "resource_id": self.resource_id,
            "line": self.line,
            "issue": self.issue,
        }


@dataclass(frozen=True)
class ReferenceScan:
    managed: tuple[ResourceReference, ...]
    unmanaged: tuple[ResourceReference, ...]

    def as_json(self) -> dict[str, object]:
        return {
            "managed_count": len(self.managed),
            "unmanaged_count": len(self.unmanaged),
            "managed": [item.as_json() for item in self.managed],
            "unmanaged": [item.as_json() for item in self.unmanaged],
        }


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _mask_span(chars: list[str], start: int, end: int) -> None:
    for index in range(start, end):
        if chars[index] not in "\r\n":
            chars[index] = " "


def mask_markdown_code(text: str) -> str:
    """Replace fenced and inline code with spaces while preserving offsets."""

    chars = list(text)
    offset = 0
    fence_char: str | None = None
    fence_length = 0
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        opening = FENCE_OPEN_RE.match(content)
        if fence_char is not None:
            _mask_span(chars, offset, offset + len(line))
            stripped = content.lstrip(" ")
            closing = re.match(rf"{re.escape(fence_char)}{{{fence_length},}}[ \t]*$", stripped)
            if closing:
                fence_char = None
                fence_length = 0
            offset += len(line)
            continue
        if opening:
            fence = opening.group("fence")
            fence_char = fence[0]
            fence_length = len(fence)
            _mask_span(chars, offset, offset + len(line))
            offset += len(line)
            continue

        line_start = offset
        cursor = 0
        while cursor < len(content):
            if content[cursor] != "`":
                cursor += 1
                continue
            run_end = cursor + 1
            while run_end < len(content) and content[run_end] == "`":
                run_end += 1
            delimiter = content[cursor:run_end]
            closing_at = content.find(delimiter, run_end)
            if closing_at < 0:
                cursor = run_end
                continue
            _mask_span(chars, line_start + cursor, line_start + closing_at + len(delimiter))
            cursor = closing_at + len(delimiter)
        offset += len(line)
    return "".join(chars)


def _overlaps(start: int, end: int, occupied: Sequence[tuple[int, int]]) -> bool:
    return any(start < occupied_end and end > occupied_start for occupied_start, occupied_end in occupied)


def _web_uri_spans(masked: str) -> tuple[tuple[int, int], ...]:
    """Return HTTP(S) URI spans that local-path detectors must ignore."""

    return tuple(match.span() for match in WEB_URI_RE.finditer(masked))


def _attached_marker(
    masked: str,
    end: int,
) -> tuple[str | None, int, str | None, tuple[int, int] | None]:
    marker = ANY_RESOURCE_MARKER_RE.match(masked, end)
    if marker is None:
        return None, end, None, None
    raw_identifier = marker.group("resource_id").strip()
    comment_start = masked.find("<!--", marker.start(), marker.end())
    marker_span = (comment_start, marker.end())
    if re.fullmatch(RESOURCE_ID_PATTERN, raw_identifier, re.IGNORECASE):
        return raw_identifier.upper(), marker.end(), None, marker_span
    return None, marker.end(), "invalid_resource_marker", marker_span


def _html_attached_marker(
    masked: str,
    match: re.Match[str],
) -> tuple[str | None, int, str | None, tuple[int, int] | None]:
    marker_offset = match.end()
    tag = match.group("tag")
    if tag.lower() in {"audio", "video"}:
        closing = re.match(rf"[ \t]*</{re.escape(tag)}[ \t]*>", masked[marker_offset:], re.IGNORECASE)
        if closing is not None:
            marker_offset += closing.end()
    return _attached_marker(masked, marker_offset)


def _clean_target(target: str) -> str:
    if target.startswith("<") and target.endswith(">"):
        return target[1:-1]
    return target


def scan_resource_references(text: str) -> ReferenceScan:
    """Parse supported external references without inspecting code examples."""

    masked = mask_markdown_code(text)
    managed: list[ResourceReference] = []
    unmanaged: list[ResourceReference] = []
    occupied: list[tuple[int, int]] = list(_web_uri_spans(masked))
    used_markers: set[tuple[int, int]] = set()

    for match in MARKDOWN_FILE_LINK_RE.finditer(masked):
        resource_id, marker_end, marker_issue, marker_span = _attached_marker(masked, match.end())
        target = _clean_target(match.group("target"))
        reference = ResourceReference(
            kind="markdown_image" if match.group("image") else "markdown_link",
            target=target,
            resource_id=resource_id,
            line=_line_number(text, match.start()),
            issue=None if resource_id else "missing_resource_id",
        )
        (managed if resource_id else unmanaged).append(reference)
        occupied.append((match.start(), marker_end))
        if marker_span is not None:
            used_markers.add(marker_span)
        if marker_issue is not None:
            unmanaged.append(
                ResourceReference(
                    kind="resource_marker",
                    target=text[marker_span[0]:marker_span[1]] if marker_span else "",
                    resource_id=None,
                    line=_line_number(text, marker_span[0] if marker_span else match.end()),
                    issue=marker_issue,
                )
            )

    for match in HTML_MEDIA_RE.finditer(masked):
        attrs = list(HTML_FILE_ATTR_RE.finditer(match.group("attrs")))
        if not attrs:
            continue
        resource_id, marker_end, marker_issue, marker_span = _html_attached_marker(masked, match)
        if marker_span is not None:
            used_markers.add(marker_span)
        if len(attrs) > 1:
            for attr in attrs:
                unmanaged.append(
                    ResourceReference(
                        kind=f"html_{match.group('tag').lower()}_{attr.group('attribute').lower()}",
                        target=attr.group("target"),
                        resource_id=None,
                        line=_line_number(text, match.start()),
                        issue="ambiguous_html_file_targets",
                    )
                )
        else:
            attr = attrs[0]
            reference = ResourceReference(
                kind=f"html_{match.group('tag').lower()}",
                target=attr.group("target"),
                resource_id=resource_id,
                line=_line_number(text, match.start()),
                issue=None if resource_id else "missing_resource_id",
            )
            (managed if resource_id else unmanaged).append(reference)
        if marker_issue is not None:
            unmanaged.append(
                ResourceReference(
                    kind="resource_marker",
                    target=text[marker_span[0]:marker_span[1]] if marker_span else "",
                    resource_id=None,
                    line=_line_number(text, marker_span[0] if marker_span else match.end()),
                    issue=marker_issue,
                )
            )
        occupied.append((match.start(), marker_end))

    for marker in MARKER_CANDIDATE_RE.finditer(masked):
        if marker.span() in used_markers:
            continue
        raw_identifier = marker.group("resource_id").strip()
        valid = bool(re.fullmatch(RESOURCE_ID_PATTERN, raw_identifier, re.IGNORECASE))
        unmanaged.append(
            ResourceReference(
                kind="resource_marker",
                target=text[marker.start():marker.end()],
                resource_id=None,
                line=_line_number(text, marker.start()),
                issue="orphan_resource_marker" if valid else "invalid_resource_marker",
            )
        )
        occupied.append(marker.span())

    occupied.sort()
    for match in RAW_FILE_URI_RE.finditer(masked):
        if _overlaps(match.start(), match.end(), occupied):
            continue
        unmanaged.append(
            ResourceReference(
                kind="raw_file_uri",
                target=match.group(0).rstrip(" \t.,;:!?，。；：！？、）]】}"),
                resource_id=None,
                line=_line_number(text, match.start()),
                issue="unmanaged_path_reference",
            )
        )
        occupied.append((match.start(), match.end()))

    occupied.sort()
    for match in WINDOWS_FILE_PATH_RE.finditer(masked):
        if _overlaps(match.start(), match.end(), occupied):
            continue
        unmanaged.append(
            ResourceReference(
                kind="windows_path",
                target=match.group("target").rstrip(" \t.,;:!?，。；：！？、）]】}"),
                resource_id=None,
                line=_line_number(text, match.start()),
                issue="unmanaged_path_reference",
            )
        )

    return ReferenceScan(tuple(managed), tuple(unmanaged))


def _vault_root_for_note(note_path: Path) -> Path | None:
    candidate = note_path.expanduser().resolve(strict=False).parent
    for directory in (candidate, *candidate.parents):
        if (directory / ".obsidian").is_dir():
            return directory
    return None


def _possible_relative_file_paths(text: str, note_path: Path) -> tuple[ResourceReference, ...]:
    """Find uncertain relative file paths for fail-closed harness fallback.

    These are not declared unmanaged references on their own: a working
    manage-personal-knowledge harness decides whether they belong to the
    configured knowledge root.  Existing files that resolve inside the Vault
    are ordinary Vault attachments and are removed from this uncertainty set.
    """

    masked = mask_markdown_code(text)
    web_uri_spans = _web_uri_spans(masked)
    vault_root = _vault_root_for_note(note_path)
    note_parent = note_path.expanduser().resolve(strict=False).parent
    candidates: list[ResourceReference] = []
    for match in POSSIBLE_RELATIVE_FILE_PATH_RE.finditer(masked):
        if _overlaps(match.start(), match.end(), web_uri_spans):
            continue
        raw = match.group("target").rstrip(" \t.,;:!?，。；：！？、）]】}")
        normalised = raw.replace("\\", "/").strip("/")
        parts = tuple(part for part in normalised.split("/") if part not in ("", "."))
        if not parts:
            continue
        is_vault_attachment = False
        if vault_root is not None and all(part != ".." for part in parts):
            for base in (note_parent, vault_root):
                resolved = base.joinpath(*parts).resolve(strict=False)
                try:
                    resolved.relative_to(vault_root)
                except ValueError:
                    continue
                if resolved.is_file():
                    is_vault_attachment = True
                    break
        if is_vault_attachment:
            continue
        candidates.append(
            ResourceReference(
                kind="possible_relative_file_path",
                target=normalised,
                resource_id=None,
                line=_line_number(text, match.start()),
                issue="requires_harness_classification",
            )
        )
    return tuple(candidates)


def rewrite_managed_resource_uri(text: str, resource_id: str, new_uri: str) -> dict[str, object]:
    """Rewrite only file URIs immediately paired with one stable resource ID."""

    normalized_id = resource_id.upper()
    if not re.fullmatch(RESOURCE_ID_PATTERN, normalized_id):
        raise ValueError("resource_id must use the KB- plus 26-character Base32 format.")
    if not new_uri.lower().startswith("file:///") or any(char in new_uri for char in "\r\n\t <>\"'"):
        raise ValueError("new_uri must be a single encoded file:/// URI without whitespace or markup delimiters.")

    masked = mask_markdown_code(text)
    replacements: list[tuple[int, int, str]] = []
    old_uris: list[str] = []

    for match in MARKDOWN_FILE_LINK_RE.finditer(masked):
        marker_id, _marker_end, _marker_issue, _marker_span = _attached_marker(masked, match.end())
        if marker_id != normalized_id:
            continue
        target_start, target_end = match.span("target")
        old_uri = match.group("target")
        if old_uri.startswith("<") and old_uri.endswith(">"):
            target_start += 1
            target_end -= 1
            old_uri = old_uri[1:-1]
        if old_uri == new_uri:
            continue
        replacements.append((target_start, target_end, new_uri))
        old_uris.append(old_uri)

    for match in HTML_MEDIA_RE.finditer(masked):
        marker_id, _marker_end, _marker_issue, _marker_span = _html_attached_marker(masked, match)
        if marker_id != normalized_id:
            continue
        attrs = list(HTML_FILE_ATTR_RE.finditer(match.group("attrs")))
        if len(attrs) != 1:
            continue
        attr = attrs[0]
        target_start = match.start("attrs") + attr.start("target")
        target_end = match.start("attrs") + attr.end("target")
        old_uri = attr.group("target")
        if old_uri == new_uri:
            continue
        replacements.append((target_start, target_end, new_uri))
        old_uris.append(old_uri)

    rewritten = text
    for start, end, replacement in sorted(replacements, reverse=True):
        rewritten = rewritten[:start] + replacement + rewritten[end:]
    return {
        "text": rewritten,
        "changed": bool(replacements),
        "count": len(replacements),
        "old_uris": old_uris,
    }


def _expanded_diff(
    before: Sequence[ResourceReference],
    after: Sequence[ResourceReference],
) -> tuple[list[ResourceReference], list[ResourceReference], list[ResourceReference]]:
    before_by_key: dict[tuple[str, str, str], list[ResourceReference]] = collections.defaultdict(list)
    after_by_key: dict[tuple[str, str, str], list[ResourceReference]] = collections.defaultdict(list)
    for item in before:
        before_by_key[item.identity].append(item)
    for item in after:
        after_by_key[item.identity].append(item)
    added: list[ResourceReference] = []
    removed: list[ResourceReference] = []
    retained: list[ResourceReference] = []
    for key in sorted(set(before_by_key) | set(after_by_key)):
        old_items = before_by_key.get(key, [])
        new_items = after_by_key.get(key, [])
        common = min(len(old_items), len(new_items))
        retained.extend(new_items[:common])
        removed.extend(old_items[common:])
        added.extend(new_items[common:])
    return added, removed, retained


@observed("obsidian.edit.resource.preflight.scan_compare")
def compare_reference_scans(before: ReferenceScan, after: ReferenceScan) -> dict[str, object]:
    added, removed, retained = _expanded_diff(before.managed, after.managed)
    unmanaged_added, unmanaged_removed, unmanaged_retained = _expanded_diff(before.unmanaged, after.unmanaged)
    return {
        "before": before.as_json(),
        "after": after.as_json(),
        "added": [item.as_json() for item in added],
        "removed": [item.as_json() for item in removed],
        "retained": [item.as_json() for item in retained],
        "unmanaged_added": [item.as_json() for item in unmanaged_added],
        "unmanaged_removed": [item.as_json() for item in unmanaged_removed],
        "unmanaged_retained": [item.as_json() for item in unmanaged_retained],
        "external_change": bool(added or removed or unmanaged_added or unmanaged_removed),
        "unknown": [],
        "missing": [],
        "retired": [],
        "path_mismatch": [],
        "uri_mismatch": [],
        "harness_diagnostics": {
            "before": [],
            "after": [],
            "added": [],
            "removed": [],
        },
        "preflight": "not_run",
        "sync": {"status": "not_run"},
    }


def _candidate_harness_paths() -> Iterable[Path]:
    skill_root = Path(__file__).resolve().parents[1]
    yield skill_root.parent / "manage-personal-knowledge" / "scripts" / "manage_kb.py"


def discover_harness(environment: Mapping[str, str] | None = None) -> tuple[Path | None, str]:
    values = os.environ if environment is None else environment
    configured = values.get("MPK_HARNESS")
    if configured is not None:
        candidate = Path(os.path.expandvars(os.path.expanduser(configured))).resolve(strict=False)
        return (candidate, "environment") if candidate.is_file() else (None, "environment_missing")
    for candidate in _candidate_harness_paths():
        candidate = candidate.resolve(strict=False)
        if candidate.is_file():
            return candidate, "auto"
    return None, "not_found"


def _parse_json_output(output: str) -> dict[str, object]:
    stripped = output.strip()
    if stripped:
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(parsed, dict):
                return parsed
    for line in reversed([line.strip() for line in output.splitlines() if line.strip()]):
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("Harness did not return a JSON object.")


class HarnessClient:
    def __init__(self, path: Path | None = None, *, source: str | None = None) -> None:
        if path is None:
            path, discovered_source = discover_harness()
            source = source or discovered_source
        self.path = path
        self.source = source or "explicit"

    @property
    def available(self) -> bool:
        if self.path is None or not self.path.is_file():
            return False
        if self.source != "auto" or self.path.suffix.lower() != ".py":
            return True
        try:
            entrypoint = self.path.read_text(encoding="utf-8")
        except OSError:
            return False
        return "resource-resolve" in entrypoint and "reference-refresh" in entrypoint

    def describe(self) -> dict[str, object]:
        return {
            "available": self.available,
            "path": str(self.path) if self.path is not None else None,
            "source": self.source,
        }

    def _command(self, arguments: Sequence[str]) -> list[str]:
        if not self.available or self.path is None:
            raise FileNotFoundError("manage-personal-knowledge harness is unavailable")
        if self.path.suffix.lower() == ".py":
            return [sys.executable, str(self.path), *arguments]
        return [str(self.path), *arguments]

    def call(self, arguments: Sequence[str]) -> dict[str, object]:
        environment = os.environ.copy()
        environment.setdefault("PYTHONDONTWRITEBYTECODE", "1")
        completed = subprocess.run(
            self._command(arguments),
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            timeout=30,
            check=False,
        )
        try:
            payload = _parse_json_output(completed.stdout)
        except ValueError as exc:
            detail = completed.stderr.strip() or completed.stdout.strip() or str(exc)
            raise RuntimeError(detail) from exc
        if completed.returncode != 0:
            detail = str(payload.get("error") or payload.get("message") or completed.stderr.strip() or "Harness command failed.")
            raise RuntimeError(detail)
        return payload

    @observed("obsidian.edit.resource.preflight.resolve_ids")
    def resolve(self, resource_id: str) -> dict[str, object]:
        return self.call(["resource-resolve", "--id", resource_id])

    @observed("obsidian.edit.resource.sync.dry_run")
    def preview(self, *, note_path: Path, text: str) -> dict[str, object]:
        """Ask the registry harness to diagnose one in-memory note without writing."""

        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", suffix=".md", delete=False) as handle:
                handle.write(text)
                temporary = Path(handle.name)
            return self.call(
                [
                    "reference-refresh",
                    "--note",
                    str(note_path),
                    "--content-file",
                    str(temporary),
                ]
            )
        finally:
            if temporary is not None:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass

    def refresh(
        self,
        *,
        note_path: Path,
        final_text: str,
        post_sha256: str,
    ) -> tuple[dict[str, object], dict[str, object]]:
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", suffix=".md", delete=False) as handle:
                handle.write(final_text)
                temporary = Path(handle.name)
            base = [
                "reference-refresh",
                "--note",
                str(note_path),
                "--content-file",
                str(temporary),
                "--post-sha256",
                post_sha256,
            ]
            dry_run = self.call(base)
            plan_sha256 = dry_run.get("plan_sha256")
            if not isinstance(plan_sha256, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", plan_sha256):
                raise RuntimeError("reference-refresh dry-run did not return a valid plan_sha256.")
            written = self.call([*base, "--write", "--expect-plan-sha256", plan_sha256])
            return dry_run, written
        finally:
            if temporary is not None:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass


def _normal_file_uri(value: str) -> tuple[str, str, str, str, str]:
    parsed = urlsplit(value)
    if parsed.scheme.casefold() != "file":
        raise ValueError("reference target is not a file URI")
    path = unquote(parsed.path).replace("\\", "/")
    if re.match(r"^/[A-Za-z]:/", path):
        path = path[1:]
    return (
        parsed.scheme.casefold(),
        parsed.netloc.casefold(),
        path.rstrip("/").casefold(),
        parsed.query,
        parsed.fragment,
    )


def _resolved_payload(payload: dict[str, object]) -> dict[str, object]:
    nested = payload.get("resource")
    return nested if isinstance(nested, dict) else payload


def _diagnostics(payload: Mapping[str, object]) -> list[dict[str, object]]:
    value = payload.get("diagnostics", [])
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _diagnostic_key(item: Mapping[str, object]) -> str:
    stable = {
        key: value
        for key, value in item.items()
        if key not in {"line", "column", "note_relative_path", "message"}
    }
    return json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _diagnostic_diff(
    before: Sequence[Mapping[str, object]],
    after: Sequence[Mapping[str, object]],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    before_counts = collections.Counter(_diagnostic_key(item) for item in before)
    after_counts = collections.Counter(_diagnostic_key(item) for item in after)
    added: list[dict[str, object]] = []
    removed: list[dict[str, object]] = []
    for item in after:
        key = _diagnostic_key(item)
        if before_counts[key]:
            before_counts[key] -= 1
        else:
            added.append(dict(item))
    for item in before:
        key = _diagnostic_key(item)
        if after_counts[key]:
            after_counts[key] -= 1
        else:
            removed.append(dict(item))
    return added, removed


def _classify_harness_diagnostics(audit: dict[str, object], items: Sequence[Mapping[str, object]]) -> None:
    for raw in items:
        item = dict(raw)
        reason = str(item.get("reason") or "diagnostic").casefold()
        if reason == "unknown_resource_id":
            cast = audit["unknown"]
        elif reason == "missing":
            cast = audit["missing"]
        elif reason == "retired":
            cast = audit["retired"]
        elif reason == "uri_mismatch":
            cast = audit["uri_mismatch"]
            audit["path_mismatch"].append(item)
        else:
            cast = audit.setdefault("blocking_diagnostics", [])
        cast.append(item)


@observed("obsidian.edit.resource.preflight.harness_preview")
def preflight_resource_references(
    before_text: str,
    candidate_text: str,
    *,
    note_path: Path,
    client: HarnessClient | None = None,
) -> dict[str, object]:
    """Compare a candidate edit, resolve changed IDs, and return its audit."""

    before = scan_resource_references(before_text)
    after = scan_resource_references(candidate_text)
    audit = compare_reference_scans(before, after)
    possible_before = _possible_relative_file_paths(before_text, note_path)
    possible_after = _possible_relative_file_paths(candidate_text, note_path)
    possible_added, possible_removed, possible_retained = _expanded_diff(
        possible_before, possible_after
    )
    audit["possible_relative_paths"] = {
        "added": [item.as_json() for item in possible_added],
        "removed": [item.as_json() for item in possible_removed],
        "retained": [item.as_json() for item in possible_retained],
    }
    possible_relative_change = bool(possible_added or possible_removed)
    harness = client or HarnessClient()
    audit["harness"] = harness.describe()

    preview_error: str | None = None
    if before_text != candidate_text and harness.available:
        try:
            before_preview = harness.preview(note_path=note_path, text=before_text)
            after_preview = harness.preview(note_path=note_path, text=candidate_text)
            before_diagnostics = _diagnostics(before_preview)
            after_diagnostics = _diagnostics(after_preview)
            added_diagnostics, removed_diagnostics = _diagnostic_diff(before_diagnostics, after_diagnostics)
            audit["harness_diagnostics"] = {
                "before": before_diagnostics,
                "after": after_diagnostics,
                "added": added_diagnostics,
                "removed": removed_diagnostics,
                "before_plan_sha256": before_preview.get("plan_sha256"),
                "after_plan_sha256": after_preview.get("plan_sha256"),
            }
            if added_diagnostics or removed_diagnostics:
                audit["external_change"] = True
            if added_diagnostics:
                _classify_harness_diagnostics(audit, added_diagnostics)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            preview_error = str(exc)
            audit["harness_preview"] = {"status": "failed", "error": preview_error}

    # A relative path can be a Vault attachment or a knowledge-root resource.
    # When the harness cannot classify a newly changed candidate, fail closed;
    # when its two previews succeed, their diagnostic delta is authoritative.
    if possible_relative_change and (not harness.available or preview_error):
        audit["external_change"] = True

    unmanaged_added = list(audit["unmanaged_added"])
    if unmanaged_added:
        audit["preflight"] = "blocked"
        raise ResourceReferenceError(
            "Refusing to add an external file reference without a valid mpk-resource ID marker.",
            audit,
        )

    if audit.get("blocking_diagnostics"):
        audit["preflight"] = "blocked"
        raise ResourceReferenceError(
            "The registry harness found a new unmanaged or invalid local-resource reference.",
            audit,
        )

    if not audit["external_change"]:
        audit["preflight"] = "not_needed"
        if preview_error:
            audit.setdefault("warnings", []).append(
                "Resource harness diagnostics failed, but this edit did not change an external reference."
            )
        if after.unmanaged:
            audit.setdefault("warnings", []).append("Existing unmanaged external references were retained unchanged.")
        return audit

    if not harness.available:
        audit["preflight"] = "blocked"
        raise ResourceReferenceError(
            "External resource references changed, but the manage-personal-knowledge harness is unavailable.",
            audit,
        )
    if preview_error:
        audit["preflight"] = "blocked"
        raise ResourceReferenceError(
            "External resource references changed, but registry preflight diagnostics failed.",
            audit,
        )

    changed_ids = sorted(
        {
            str(item["resource_id"])
            for item in audit["added"]
            if isinstance(item, dict) and item.get("resource_id")
        }
    )
    resolved: dict[str, dict[str, object]] = {}
    for resource_id in changed_ids:
        try:
            payload = _resolved_payload(harness.resolve(resource_id))
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            audit["unknown"].append({"resource_id": resource_id, "error": str(exc)})
            continue
        resolved[resource_id] = payload
        returned_id = str(payload.get("resource_id") or resource_id)
        status = str(payload.get("status") or "unknown").lower()
        exists = payload.get("exists")
        expected_uri = payload.get("file_uri")
        if returned_id.upper() != resource_id.upper():
            audit["unknown"].append({"resource_id": resource_id, "status": "identity_mismatch"})
            continue
        if status == "retired":
            audit["retired"].append({"resource_id": resource_id, "status": status})
            continue
        if status == "missing" or exists is False:
            audit["missing"].append({"resource_id": resource_id, "status": status})
            continue
        if status != "active" or exists is not True or not isinstance(expected_uri, str):
            audit["unknown"].append({"resource_id": resource_id, "status": status})
            continue
        try:
            _normal_file_uri(expected_uri)
        except ValueError as exc:
            audit["unknown"].append({"resource_id": resource_id, "status": "invalid_file_uri", "error": str(exc)})

    for item in audit["added"]:
        if not isinstance(item, dict):
            continue
        resource_id = item.get("resource_id")
        payload = resolved.get(str(resource_id))
        if not payload or item.get("target") is None:
            continue
        expected_uri = payload.get("file_uri")
        if isinstance(expected_uri, str):
            try:
                mismatch = _normal_file_uri(str(item["target"])) != _normal_file_uri(expected_uri)
            except ValueError:
                mismatch = True
            if mismatch:
                detail = {
                    "resource_id": resource_id,
                    "note_target": item["target"],
                    "current_file_uri": expected_uri,
                }
                audit["path_mismatch"].append(detail)
                audit["uri_mismatch"].append(detail)

    if audit["unknown"] or audit["missing"] or audit["retired"] or audit["path_mismatch"]:
        audit["preflight"] = "blocked"
        raise ResourceReferenceError(
            "One or more changed resource references are unknown, missing, retired, or point at the wrong path.",
            audit,
        )

    audit["preflight"] = "passed"
    return audit


@observed("obsidian.edit.resource.sync.write")
def synchronize_resource_references(
    *,
    note_path: Path,
    final_text: str,
    post_sha256: str,
    audit: dict[str, object],
    client: HarnessClient | None = None,
) -> bool:
    """Replace the note's cached reference set through a two-phase harness call."""

    harness = client or HarnessClient()
    audit["harness"] = harness.describe()
    if not harness.available:
        if audit.get("external_change"):
            audit["sync"] = {
                "status": "reference_sync_pending",
                "error": "manage-personal-knowledge harness became unavailable after reference preflight",
            }
            return False
        audit["sync"] = {"status": "skipped_harness_unavailable"}
        return True
    try:
        dry_run, written = harness.refresh(
            note_path=note_path,
            final_text=final_text,
            post_sha256=post_sha256,
        )
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        detail = str(exc)
        if not audit.get("external_change"):
            status = (
                "skipped_harness_unconfigured"
                if any(fragment in detail.lower() for fragment in UNCONFIGURED_ERROR_FRAGMENTS)
                else "skipped_harness_error"
            )
            audit["sync"] = {"status": status, "error": detail}
            return True
        audit["sync"] = {"status": "reference_sync_pending", "error": detail}
        return False
    dry_diagnostics = _diagnostics(dry_run)
    written_diagnostics = _diagnostics(written)
    baseline = audit.get("harness_diagnostics", {})
    expected_diagnostics = baseline.get("after", []) if isinstance(baseline, dict) else []
    newly_observed, _removed = _diagnostic_diff(
        [item for item in expected_diagnostics if isinstance(item, dict)],
        dry_diagnostics,
    )
    audit["sync"] = {
        "status": "synchronized",
        "plan_sha256": dry_run.get("plan_sha256"),
        "generation": written.get("generation"),
        "dry_run_diagnostics": dry_diagnostics,
        "written_diagnostics": written_diagnostics,
        "new_diagnostics": newly_observed,
        "parsed_counts": dry_run.get("parsed_counts"),
    }
    if audit.get("external_change") and newly_observed:
        audit["sync"]["status"] = "reference_sync_pending"
        audit["sync"]["error"] = "New resource-reference diagnostics appeared after preflight."
        return False
    return True


__all__ = [
    "HarnessClient",
    "ReferenceScan",
    "ResourceReference",
    "ResourceReferenceError",
    "compare_reference_scans",
    "discover_harness",
    "mask_markdown_code",
    "preflight_resource_references",
    "rewrite_managed_resource_uri",
    "scan_resource_references",
    "synchronize_resource_references",
]
