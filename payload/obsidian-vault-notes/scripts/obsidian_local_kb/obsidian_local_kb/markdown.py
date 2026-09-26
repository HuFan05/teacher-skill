from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path

from .util import compact_snippet, normalize_note_key, normalize_text, relative_markdown_path


HEADING_RE = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+|$)(.*)$")
FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
WIKILINK_RE = re.compile(r"!?\\?\\?\[\[([^\]]+)\]\]")
FRONTMATTER_KEY_RE = re.compile(r"^([A-Za-z0-9_-]+)[ \t]*:[ \t]*(.*)$")
BLOCK_ITEM_RE = re.compile(r"^[ \t]+-[ \t]+(.+?)\s*$")


@dataclass(slots=True)
class LinkData:
    source_section_key: int
    target_raw: str
    target_note_raw: str | None
    target_note_norm: str | None
    target_heading_raw: str | None
    target_heading_norm: str | None


@dataclass(slots=True)
class SectionData:
    section_key: int
    heading: str | None
    heading_path: str
    heading_norm: str | None
    level: int
    content: str
    snippet: str


@dataclass(slots=True)
class NoteData:
    path: str
    title: str
    title_norm: str
    source_mtime_ns: int
    source_size: int
    aliases: list[str]
    tags: list[str]
    warnings: list[str]
    sections: list[SectionData]
    links: list[LinkData]


def read_markdown(file_path: Path) -> str:
    encodings = ("utf-8-sig", "utf-8", "utf-16", "gb18030")
    last_error: Exception | None = None
    for encoding in encodings:
        try:
            return file_path.read_text(encoding=encoding)
        except UnicodeError as exc:
            last_error = exc
    if last_error is not None:
        return file_path.read_text(encoding="utf-8", errors="replace")
    return file_path.read_text()


def split_frontmatter(text: str) -> tuple[str, str]:
    if not text.startswith("---"):
        return "", text
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return "", text
    for idx in range(1, len(lines)):
        if lines[idx].strip() in {"---", "..."}:
            frontmatter = "\n".join(lines[: idx + 1])
            body = "\n".join(lines[idx + 1 :])
            return frontmatter, body
    return "", text


def _unquote_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        quote = value[0]
        value = value[1:-1]
        if quote == '"':
            value = value.replace(r'\"', '"').replace(r"\\", "\\")
        else:
            value = value.replace("''", "'")
    return value.strip()


def _split_inline_list(value: str) -> list[str] | None:
    if not (value.startswith("[") and value.endswith("]")):
        return None
    inner = value[1:-1].strip()
    if not inner:
        return []
    items: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escaped = False
    for char in inner:
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == "\\" and quote == '"':
            current.append(char)
            escaped = True
            continue
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
            current.append(char)
            continue
        if char in "[{":
            return None
        if char in "]}":
            return None
        if char == ",":
            item = _unquote_scalar("".join(current))
            if item:
                items.append(item)
            current = []
            continue
        current.append(char)
    if quote:
        return None
    item = _unquote_scalar("".join(current))
    if item:
        items.append(item)
    return items


def _simple_frontmatter_values(
    frontmatter: str,
    keys: set[str],
    *,
    strip_hash: bool = False,
) -> tuple[list[str], list[str]]:
    if not frontmatter:
        return [], []
    lines = frontmatter.splitlines()[1:-1]
    values: list[str] = []
    warnings: list[str] = []
    index = 0
    while index < len(lines):
        match = FRONTMATTER_KEY_RE.match(lines[index])
        if not match or match.group(1).lower() not in keys:
            index += 1
            continue
        key = match.group(1).lower()
        raw_value = match.group(2).strip()
        parsed: list[str] | None = None
        if raw_value:
            if raw_value[:1] in {"|", ">", "{", "&", "*", "!"}:
                warnings.append(f"ignored complex frontmatter field: {key}")
            elif raw_value.startswith("["):
                parsed = _split_inline_list(raw_value)
                if parsed is None:
                    warnings.append(f"ignored complex frontmatter field: {key}")
            else:
                parsed = [_unquote_scalar(raw_value)]
        else:
            parsed = []
            cursor = index + 1
            while cursor < len(lines):
                if not lines[cursor].strip():
                    cursor += 1
                    continue
                item_match = BLOCK_ITEM_RE.match(lines[cursor])
                if item_match:
                    item = _unquote_scalar(item_match.group(1))
                    if item[:1] in {"{", "[", "&", "*", "!", "|", ">"}:
                        parsed = None
                        warnings.append(f"ignored complex frontmatter field: {key}")
                        break
                    parsed.append(item)
                    cursor += 1
                    continue
                if lines[cursor][:1].isspace():
                    parsed = None
                    warnings.append(f"ignored complex frontmatter field: {key}")
                break
            index = cursor - 1
        if parsed is not None:
            for item in parsed:
                item = item.strip()
                if strip_hash:
                    item = item.lstrip("#")
                if item and item not in values:
                    values.append(item)
        index += 1
    return values, warnings


def parse_frontmatter_metadata(frontmatter: str) -> tuple[list[str], list[str], list[str]]:
    aliases, alias_warnings = _simple_frontmatter_values(frontmatter, {"alias", "aliases"})
    tags, tag_warnings = _simple_frontmatter_values(frontmatter, {"tag", "tags"}, strip_hash=True)
    return aliases, tags, alias_warnings + tag_warnings


def _fence_marker(line: str) -> tuple[str, int] | None:
    match = FENCE_RE.match(line)
    if not match:
        return None
    marker = match.group(1)
    return marker[0], len(marker)


def _is_fence_close(line: str, char: str, minimum_length: int) -> bool:
    stripped = line.lstrip(" ")
    if len(line) - len(stripped) > 3 or not stripped.startswith(char * minimum_length):
        return False
    marker_length = len(stripped) - len(stripped.lstrip(char))
    return marker_length >= minimum_length and not stripped[marker_length:].strip()


def parse_link_target(raw_target: str, current_title: str) -> tuple[str | None, str | None, str | None, str | None]:
    target = raw_target.split("|", 1)[0].strip()
    note_part: str | None
    heading_part: str | None

    if target.startswith("#"):
        note_part = current_title
        heading_part = target[1:].strip() or None
    elif "#" in target:
        note_text, heading_text = target.split("#", 1)
        note_part = note_text.strip() or current_title
        heading_part = heading_text.strip() or None
    else:
        note_part = target or current_title
        heading_part = None

    note_norm = normalize_note_key(note_part) if note_part else None
    heading_norm = normalize_text(heading_part) if heading_part else None
    return note_part, note_norm, heading_part, heading_norm


def extract_links(section_text: str, section_key: int, current_title: str) -> list[LinkData]:
    results: list[LinkData] = []
    for match in WIKILINK_RE.finditer(section_text):
        raw = match.group(1).strip()
        note_raw, note_norm, heading_raw, heading_norm = parse_link_target(raw, current_title=current_title)
        results.append(
            LinkData(
                source_section_key=section_key,
                target_raw=raw,
                target_note_raw=note_raw,
                target_note_norm=note_norm,
                target_heading_raw=heading_raw,
                target_heading_norm=heading_norm,
            )
        )
    return results


def parse_markdown_note(vault_root: Path, file_path: Path) -> NoteData:
    rel_path = relative_markdown_path(vault_root, file_path)
    title = file_path.stem
    stat = file_path.stat()
    text = read_markdown(file_path)
    frontmatter, body = split_frontmatter(text)
    aliases, tags, warnings = parse_frontmatter_metadata(frontmatter)

    sections: list[SectionData] = []
    links: list[LinkData] = []
    stack: list[tuple[int, str]] = []
    current_heading: str | None = None
    current_level = 0
    current_lines: list[str] = []
    current_link_lines: list[str] = []
    section_key = 0
    fence_char: str | None = None
    fence_length = 0

    def flush_section() -> None:
        nonlocal section_key, current_lines, current_link_lines
        content = "\n".join(current_lines).strip()
        if not content and current_heading is None and sections:
            current_lines = []
            return
        heading_path = " > ".join(item[1] for item in stack)
        heading_norm = normalize_text(current_heading) if current_heading else None
        section = SectionData(
            section_key=section_key,
            heading=current_heading,
            heading_path=heading_path,
            heading_norm=heading_norm,
            level=current_level,
            content=content,
            snippet=compact_snippet(content or heading_path or title),
        )
        sections.append(section)
        link_text = "\n".join(current_link_lines)
        links.extend(extract_links(link_text, section_key=section_key, current_title=title))
        section_key += 1
        current_lines = []
        current_link_lines = []

    for line in body.splitlines():
        if fence_char is not None:
            current_lines.append(line)
            if _is_fence_close(line, fence_char, fence_length):
                fence_char = None
                fence_length = 0
            continue

        marker = _fence_marker(line)
        if marker is not None:
            fence_char, fence_length = marker
            current_lines.append(line)
            continue

        heading_match = HEADING_RE.match(line)
        if heading_match:
            flush_section()
            level = len(heading_match.group(1))
            heading_text = heading_match.group(2).strip()
            heading_text = re.sub(r"[ \t]+#+[ \t]*$", "", heading_text).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, heading_text))
            current_heading = heading_text
            current_level = level
            continue
        current_lines.append(line)
        current_link_lines.append(line)

    flush_section()

    if not sections:
        sections.append(
            SectionData(
                section_key=0,
                heading=None,
                heading_path="",
                heading_norm=None,
                level=0,
                content="",
                snippet=title,
            )
        )

    return NoteData(
        path=rel_path,
        title=title,
        title_norm=normalize_note_key(title),
        source_mtime_ns=stat.st_mtime_ns,
        source_size=stat.st_size,
        aliases=aliases,
        tags=tags,
        warnings=warnings,
        sections=sections,
        links=links,
    )
