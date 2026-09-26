"""Parse and rewrite ID-tagged local-resource references in Markdown notes."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Mapping, Sequence
from urllib.parse import unquote, urlsplit

from .registry import RESOURCE_ID_RE, RegistryError, validate_resource_id


MARKER_RE = re.compile(r"<!--\s*mpk-resource:(?P<resource_id>[^\s>]+)\s*-->", re.IGNORECASE)
FILE_URI_RE = re.compile(r"file:///[^\s<>\"')\]}]+", re.IGNORECASE)
WINDOWS_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_:/])(?P<path>[A-Za-z]:[\\/][^<>\r\n\"`]+?\.[A-Za-z0-9]{1,12})(?=$|[\s)\]},;:!?])"
)
ROOT_RELATIVE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_:/\\])(?P<path>(?:[^\s<>\r\n\"'`(){}\[\]:]+[\\/])+"
    r"[^\s<>\r\n\"'`(){}\[\]:]+\.[A-Za-z0-9]{1,16})(?=$|[\s)\]},;:!?，。；：！？、])"
)

MARKDOWN_LINK_RE = re.compile(
    r"(?P<link>!?\[[^\]\r\n]*\]\(\s*<?(?P<target>file:///[^\s<>\"')\]}]+)>?(?:\s+[\"'][^\r\n]*?[\"'])?\s*\))",
    re.IGNORECASE,
)
HTML_MEDIA_RE = re.compile(
    r"(?P<link><(?P<tag>video|audio|source|img)\b[^>\r\n]*?\bsrc\s*=\s*(?P<quote>[\"'])(?P<target>file:///.*?)(?P=quote)[^>\r\n]*>(?:\s*</(?P=tag)\s*>)?)",
    re.IGNORECASE,
)


def _mask_inline_code(line: str) -> str:
    chars = list(line)
    index = 0
    while index < len(line):
        if line[index] != "`":
            index += 1
            continue
        end_ticks = index
        while end_ticks < len(line) and line[end_ticks] == "`":
            end_ticks += 1
        delimiter = line[index:end_ticks]
        close = line.find(delimiter, end_ticks)
        if close < 0:
            index = end_ticks
            continue
        for position in range(index, close + len(delimiter)):
            chars[position] = " "
        index = close + len(delimiter)
    return "".join(chars)


def mask_markdown_code(text: str) -> str:
    """Replace fenced and inline code with spaces while preserving offsets."""

    output: list[str] = []
    fence_char: str | None = None
    fence_length = 0
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        match = re.match(r"^[ ]{0,3}(`{3,}|~{3,})", content)
        if fence_char is None and match:
            opening = match.group(1)
            fence_char = opening[0]
            fence_length = len(opening)
            output.append("".join("\n" if char == "\n" else "\r" if char == "\r" else " " for char in line))
            continue
        if fence_char is not None:
            output.append("".join("\n" if char == "\n" else "\r" if char == "\r" else " " for char in line))
            stripped = content.lstrip(" ")
            if re.fullmatch(rf"{re.escape(fence_char)}{{{fence_length},}}[ \t]*", stripped):
                fence_char = None
                fence_length = 0
            continue
        output.append(_mask_inline_code(line))
    return "".join(output)


def _line_column(text: str, position: int) -> tuple[int, int]:
    line = text.count("\n", 0, position) + 1
    prior = text.rfind("\n", 0, position)
    column = position + 1 if prior < 0 else position - prior
    return line, column


def _uri_to_path(uri: str) -> str:
    parsed = urlsplit(uri)
    path = unquote(parsed.path)
    if re.match(r"^/[A-Za-z]:/", path):
        path = path[1:]
    return path.replace("/", "\\")


def _find_managed(masked: str, original: str) -> tuple[list[dict[str, object]], list[tuple[int, int]], set[tuple[int, int]]]:
    links = sorted(
        list(MARKDOWN_LINK_RE.finditer(masked)) + list(HTML_MEDIA_RE.finditer(masked)),
        key=lambda item: item.start(),
    )
    references: list[dict[str, object]] = []
    managed_spans: list[tuple[int, int]] = []
    used_markers: set[tuple[int, int]] = set()
    for match in links:
        line_end = masked.find("\n", match.end())
        if line_end < 0:
            line_end = len(masked)
        tail = masked[match.end():line_end]
        whitespace = re.match(r"[ \t]*", tail)
        assert whitespace is not None
        marker = MARKER_RE.match(tail, whitespace.end())
        if marker is None:
            continue
        absolute_start = match.end() + marker.start()
        absolute_end = match.end() + marker.end()
        resource_id = marker.group("resource_id")
        target_start, target_end = match.span("target")
        target = original[target_start:target_end]
        line, column = _line_column(original, match.start())
        valid = bool(RESOURCE_ID_RE.fullmatch(resource_id))
        references.append(
            {
                "resource_id": resource_id,
                "target_uri": target,
                "absolute_path": _uri_to_path(target),
                "kind": "html_media" if match.re is HTML_MEDIA_RE else ("markdown_image" if original[match.start():].startswith("!") else "markdown_link"),
                "line": line,
                "column": column,
                "valid_id": valid,
                "span": [match.start(), absolute_end],
                "target_span": [target_start, target_end],
            }
        )
        managed_spans.append((match.start(), absolute_end))
        used_markers.add((absolute_start, absolute_end))
    return references, managed_spans, used_markers


def _inside(position: int, spans: Sequence[tuple[int, int]]) -> bool:
    return any(start <= position < end for start, end in spans)


def parse_resource_references(
    text: str,
    *,
    knowledge_root: str | Path | None = None,
    known_relative_paths: Sequence[str] = (),
) -> dict[str, object]:
    """Parse managed references and report unmanaged local paths.

    Code fences and inline-code examples are ignored.  Bare paths are only
    diagnosed; this function never guesses a replacement.
    """

    masked = mask_markdown_code(text)
    references, managed_spans, used_markers = _find_managed(masked, text)
    unmanaged: list[dict[str, object]] = []
    invalid_markers: list[dict[str, object]] = []
    seen: set[tuple[int, str]] = set()

    for reference in references:
        if not reference["valid_id"]:
            invalid_markers.append(
                {
                    "resource_id": reference["resource_id"],
                    "line": reference["line"],
                    "column": reference["column"],
                    "reason": "invalid_resource_id",
                }
            )

    for marker in MARKER_RE.finditer(masked):
        if marker.span() in used_markers:
            continue
        line, column = _line_column(text, marker.start())
        invalid_markers.append(
            {
                "resource_id": marker.group("resource_id"),
                "line": line,
                "column": column,
                "reason": "orphan_resource_marker",
            }
        )

    for uri in FILE_URI_RE.finditer(masked):
        if _inside(uri.start(), managed_spans):
            continue
        target = text[uri.start():uri.end()].rstrip(".,;:!?)]}")
        key = (uri.start(), target)
        if key in seen:
            continue
        seen.add(key)
        line, column = _line_column(text, uri.start())
        unmanaged.append(
            {
                "reference": target,
                "kind": "file_uri",
                "reason": "missing_resource_id",
                "line": line,
                "column": column,
            }
        )

    for path_match in WINDOWS_PATH_RE.finditer(masked):
        if _inside(path_match.start(), managed_spans) or masked[max(0, path_match.start() - 8):path_match.start()].casefold().endswith("file:///"):
            continue
        target = text[path_match.start("path"):path_match.end("path")]
        key = (path_match.start(), target)
        if key in seen:
            continue
        if knowledge_root is not None:
            root_text = str(knowledge_root).replace("/", "\\").rstrip("\\").casefold()
            if not target.replace("/", "\\").casefold().startswith(root_text + "\\"):
                continue
        seen.add(key)
        line, column = _line_column(text, path_match.start())
        unmanaged.append(
            {
                "reference": target,
                "kind": "absolute_windows_path",
                "reason": "unmanaged_path_reference",
                "line": line,
                "column": column,
            }
        )

    for relative in sorted(set(known_relative_paths), key=len, reverse=True):
        if not relative:
            continue
        variants = {relative, relative.replace("/", "\\"), relative.replace("\\", "/")}
        for variant in variants:
            start = 0
            while True:
                position = masked.find(variant, start)
                if position < 0:
                    break
                start = position + len(variant)
                if _inside(position, managed_spans):
                    continue
                key = (position, variant)
                if key in seen:
                    continue
                seen.add(key)
                line, column = _line_column(text, position)
                unmanaged.append(
                    {
                        "reference": variant,
                        "kind": "knowledge_root_relative_path",
                        "reason": "unmanaged_path_reference",
                        "line": line,
                        "column": column,
                    }
                )

    if knowledge_root is not None:
        root = Path(knowledge_root).expanduser().resolve(strict=False)
        known = {
            str(item).replace("\\", "/").strip("/").casefold()
            for item in known_relative_paths
            if str(item).strip()
        }
        for candidate in ROOT_RELATIVE_PATH_RE.finditer(masked):
            if _inside(candidate.start(), managed_spans):
                continue
            raw = text[candidate.start("path"):candidate.end("path")]
            normalised = raw.replace("\\", "/").strip("/")
            parts = tuple(part for part in normalised.split("/") if part not in ("", "."))
            if not parts or any(part == ".." for part in parts):
                continue
            resolved = root.joinpath(*parts).resolve(strict=False)
            try:
                resolved.relative_to(root)
            except ValueError:
                continue
            if not resolved.is_file() and normalised.casefold() not in known:
                continue
            key = (candidate.start(), raw)
            if key in seen:
                continue
            seen.add(key)
            line, column = _line_column(text, candidate.start())
            unmanaged.append(
                {
                    "reference": raw,
                    "kind": "knowledge_root_relative_path",
                    "reason": "unmanaged_path_reference",
                    "line": line,
                    "column": column,
                }
            )

    references.sort(key=lambda item: (int(item["line"]), int(item["column"])))
    unmanaged.sort(key=lambda item: (int(item["line"]), int(item["column"]), str(item["reference"])))
    invalid_markers.sort(key=lambda item: (int(item["line"]), int(item["column"])))
    return {
        "references": references,
        "unmanaged_path_references": unmanaged,
        "invalid_markers": invalid_markers,
        "counts": {
            "managed": len(references),
            "unmanaged": len(unmanaged),
            "invalid_markers": len(invalid_markers),
        },
    }


def diff_resource_references(
    before: Sequence[Mapping[str, object]],
    after: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Compare occurrence counts, preserving repeated references to one ID."""

    before_counts = Counter(str(item["resource_id"]) for item in before)
    after_counts = Counter(str(item["resource_id"]) for item in after)
    identifiers = sorted(set(before_counts) | set(after_counts))
    added: list[dict[str, object]] = []
    removed: list[dict[str, object]] = []
    retained: list[dict[str, object]] = []
    for identifier in identifiers:
        common = min(before_counts[identifier], after_counts[identifier])
        if common:
            retained.append({"resource_id": identifier, "count": common})
        if after_counts[identifier] > before_counts[identifier]:
            added.append({"resource_id": identifier, "count": after_counts[identifier] - before_counts[identifier]})
        if before_counts[identifier] > after_counts[identifier]:
            removed.append({"resource_id": identifier, "count": before_counts[identifier] - after_counts[identifier]})
    return {"added": added, "removed": removed, "retained": retained}


def rewrite_managed_reference_paths(
    text: str,
    uri_by_resource_id: Mapping[str, str],
) -> dict[str, object]:
    """Rewrite only URI spans paired with a valid same-line resource marker."""

    parsed = parse_resource_references(text)
    replacements: list[tuple[int, int, str, str]] = []
    unknown: list[str] = []
    for reference in parsed["references"]:
        identifier = str(reference["resource_id"])
        if not reference["valid_id"]:
            unknown.append(identifier)
            continue
        validate_resource_id(identifier)
        uri = uri_by_resource_id.get(identifier)
        if uri is None:
            unknown.append(identifier)
            continue
        if not uri.casefold().startswith("file:///"):
            raise RegistryError(f"Replacement for {identifier} is not a file URI")
        start, end = reference["target_span"]
        if text[start:end] != uri:
            replacements.append((int(start), int(end), uri, identifier))
    output = text
    for start, end, uri, _ in sorted(replacements, reverse=True):
        output = output[:start] + uri + output[end:]
    return {
        "text": output,
        "changed": output != text,
        "updated_resource_ids": [item[3] for item in replacements],
        "unknown_resource_ids": sorted(set(unknown)),
    }


def format_resource_link(
    display_name: str,
    file_uri: str,
    resource_id: str,
    *,
    kind: str = "markdown_link",
    description: str | None = None,
) -> str:
    identifier = validate_resource_id(resource_id)
    if not file_uri.casefold().startswith("file:///"):
        raise RegistryError("file_uri must start with file:///")
    marker = f"<!-- mpk-resource:{identifier} -->"
    if kind == "markdown_link":
        return f"[{display_name}]({file_uri}){marker}"
    if kind == "markdown_image":
        return f"![{description or display_name}]({file_uri}){marker}"
    if kind in {"video", "audio", "img"}:
        return f'<{kind} src="{file_uri}"></{kind}>{marker}'
    if kind == "source":
        return f'<source src="{file_uri}">{marker}'
    raise RegistryError(f"Unsupported reference kind: {kind}")


__all__ = [
    "diff_resource_references",
    "format_resource_link",
    "mask_markdown_code",
    "parse_resource_references",
    "rewrite_managed_reference_paths",
]
