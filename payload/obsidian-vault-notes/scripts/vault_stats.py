from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
from collections import Counter, OrderedDict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from _common import (
    DEFAULT_DB_PATH,
    DEFAULT_KB_ROOT,
    DEFAULT_VAULT_ROOT,
    configure_stdio,
)
from _observer import flush as flush_observer
from _observer import observed


DEFAULT_EXCLUDED_DIRS = {".obsidian", ".trash"}

FRONTMATTER_RE = re.compile(
    r"\A---[ \t]*\r?\n.*?\r?\n(?:---|\.\.\.)[ \t]*(?:\r?\n|\Z)",
    re.DOTALL,
)
WIKI_IMAGE_RE = re.compile(r"!\[\[([^\]]+)\]\]")
WIKILINK_RE = re.compile(r"(?<!!)\[\[([^\]]+)\]\]")
HTML_IMAGE_RE = re.compile(
    r"<img\b[^>]*?\bsrc\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s\"'=<>`]+))",
    re.IGNORECASE,
)
FENCE_OPEN_RE = re.compile(r"^[ ]{0,3}(`{3,}|~{3,}).*$")
URL_RE = re.compile(r"https?://[^\s<>\]\)]+")


@dataclass(frozen=True)
class MarkdownLink:
    start: int
    end: int
    target: str
    is_image: bool


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Return aggregate statistics for a local Obsidian vault without emitting note bodies.",
    )
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true", help="Print JSON statistics. This is the default.")
    output.add_argument("--markdown", action="store_true", help="Print a compact Markdown report.")
    parser.add_argument(
        "--chart",
        choices=("svg", "png"),
        help="Write a deterministic chart from the computed statistics.",
    )
    parser.add_argument("--output", help="Output path for --chart.")
    parser.add_argument("--scope", help="Relative Vault folder to scan instead of the whole vault.")
    parser.add_argument("--top-folders", type=int, default=10, help="Number of top folders to include.")
    parser.add_argument("--largest-notes", type=int, default=10, help="Number of largest notes to include.")
    parser.add_argument(
        "--include-frontmatter",
        action="store_true",
        help="Include YAML frontmatter in body_chars, CJK, image, and link counts.",
    )
    parser.add_argument(
        "--exclude-dir",
        action="append",
        default=[],
        help="Additional directory name to exclude. May be passed multiple times.",
    )
    parser.add_argument("--vault-root", default=str(DEFAULT_VAULT_ROOT), help="Root of the Obsidian vault.")
    parser.add_argument("--kb-root", default=str(DEFAULT_KB_ROOT), help="Root of the obsidian_local_kb project.")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite database path for the note index.")
    parser.add_argument(
        "--include-local-paths",
        action="store_true",
        help="Include local absolute vault/kb/db paths in counting_scope.",
    )
    parser.add_argument("--debug", action="store_true", help="Include status command failures in index_status.")
    return parser


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


def strip_frontmatter(text: str) -> str:
    return FRONTMATTER_RE.sub("", text, count=1)


def is_cjk_ideograph(char: str) -> bool:
    codepoint = ord(char)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x20000 <= codepoint <= 0x2A6DF
        or 0x2A700 <= codepoint <= 0x2B73F
        or 0x2B740 <= codepoint <= 0x2B81F
        or 0x2B820 <= codepoint <= 0x2CEAF
        or 0x2CEB0 <= codepoint <= 0x2EBEF
        or 0x30000 <= codepoint <= 0x3134F
    )


def cjk_char_count(text: str) -> int:
    return sum(1 for char in text if is_cjk_ideograph(char))


def markdown_destination_target(target: str) -> str:
    target = target.strip()
    if target.startswith("<"):
        escaped = False
        for index, char in enumerate(target[1:], start=1):
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == ">":
                return target[1:index].strip()
        return target

    destination: list[str] = []
    escaped = False
    for char in target:
        if escaped:
            destination.append(char)
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char.isspace():
            break
        destination.append(char)
    if escaped:
        destination.append("\\")
    return "".join(destination).strip()


def normalize_link_target(
    target: str,
    *,
    markdown_destination: bool = False,
    wikilink: bool = False,
) -> str:
    if markdown_destination:
        target = markdown_destination_target(target)
    else:
        target = target.strip()
        if target.startswith("<") and target.endswith(">"):
            target = target[1:-1].strip()
    if wikilink:
        target = target.split("|", 1)[0].strip()
    if not target:
        return ""

    windows_absolute = bool(re.match(r"^[A-Za-z]:[\\/]", target))
    external = bool(re.match(r"^(?:[A-Za-z][A-Za-z0-9+.-]*:|//)", target)) and not windows_absolute
    if external:
        return target

    target = unquote(target).replace("\\", "/")
    while target.startswith("./"):
        target = target[2:]
    return target.casefold()


def fence_closes(line: str, fence_char: str, fence_len: int) -> bool:
    escaped = re.escape(fence_char)
    return bool(re.match(rf"^[ ]{{0,3}}{escaped}{{{fence_len},}}[ \t]*$", line))


def mask_fenced_code(text: str) -> str:
    output: list[str] = []
    fence_char: str | None = None
    fence_len = 0
    for raw_line in text.splitlines(keepends=True):
        if raw_line.endswith("\r\n"):
            content, ending = raw_line[:-2], "\r\n"
        elif raw_line.endswith(("\n", "\r")):
            content, ending = raw_line[:-1], raw_line[-1:]
        else:
            content, ending = raw_line, ""
        masked = False
        if fence_char is not None:
            masked = True
            if fence_closes(content, fence_char, fence_len):
                fence_char = None
                fence_len = 0
        else:
            match = FENCE_OPEN_RE.match(content)
            if match:
                marker = match.group(1)
                fence_char = marker[0]
                fence_len = len(marker)
                masked = True
        output.append((" " * len(content) if masked else content) + ending)
    return "".join(output)


def find_label_end(text: str, opening: int) -> int | None:
    depth = 1
    index = opening + 1
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char in "\r\n":
            return None
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def find_destination_end(text: str, opening: int) -> int | None:
    depth = 1
    in_angle = False
    index = opening + 1
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char in "\r\n":
            return None
        if char == "<" and depth == 1:
            in_angle = True
        elif char == ">" and in_angle:
            in_angle = False
        elif not in_angle and char == "(":
            depth += 1
        elif not in_angle and char == ")":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def parse_markdown_links(text: str) -> list[MarkdownLink]:
    matches: list[MarkdownLink] = []
    index = 0
    while index < len(text):
        is_image = text.startswith("![", index)
        if is_image:
            start = index
            opening = index + 1
        elif text[index] == "[" and (index == 0 or text[index - 1] not in {"!", "\\"}):
            start = index
            opening = index
        else:
            index += 1
            continue
        label_end = find_label_end(text, opening)
        if label_end is None:
            index += 1
            continue
        destination_open = label_end + 1
        while destination_open < len(text) and text[destination_open] in " \t":
            destination_open += 1
        if destination_open >= len(text) or text[destination_open] != "(":
            index = label_end + 1
            continue
        destination_end = find_destination_end(text, destination_open)
        if destination_end is None:
            index = label_end + 1
            continue
        matches.append(
            MarkdownLink(
                start=start,
                end=destination_end + 1,
                target=text[destination_open + 1 : destination_end],
                is_image=is_image,
            )
        )
        index = destination_end + 1
    return matches


def html_image_target(match: re.Match[str]) -> str:
    return next((group for group in match.groups() if group is not None), "")


def masked_text(text: str, ranges: list[tuple[int, int]]) -> str:
    if not ranges:
        return text
    chars = list(text)
    for start, end in ranges:
        for index in range(start, end):
            chars[index] = " "
    return "".join(chars)


def analyze_links(text: str) -> tuple[dict[str, Any], set[str]]:
    searchable = mask_fenced_code(text)
    wiki_images = list(WIKI_IMAGE_RE.finditer(searchable))
    wikilinks = list(WIKILINK_RE.finditer(searchable))
    markdown = parse_markdown_links(searchable)
    markdown_images = [match for match in markdown if match.is_image]
    markdown_links = [match for match in markdown if not match.is_image]
    html_images = list(HTML_IMAGE_RE.finditer(searchable))

    protected_ranges = [
        (match.start(), match.end())
        for match in [*wiki_images, *wikilinks, *html_images]
    ]
    protected_ranges.extend((match.start, match.end) for match in markdown)
    url_text = masked_text(searchable, protected_ranges)
    external_urls = list(URL_RE.finditer(url_text))

    image_targets = [
        normalize_link_target(match.group(1), wikilink=True)
        for match in wiki_images
        if normalize_link_target(match.group(1), wikilink=True)
    ]
    image_targets.extend(
        normalize_link_target(match.target, markdown_destination=True)
        for match in markdown_images
        if normalize_link_target(match.target, markdown_destination=True)
    )
    image_targets.extend(
        normalize_link_target(html_image_target(match))
        for match in html_images
        if normalize_link_target(html_image_target(match))
    )
    distinct_targets = set(image_targets)
    return {
        "image_embed_count": len(wiki_images) + len(markdown_images) + len(html_images),
        "distinct_image_target_count": len(distinct_targets),
        "wikilink_count": len(wikilinks),
        "markdown_link_count": len(markdown_links),
        "external_url_count": len(external_urls),
        "total_link_count": len(wikilinks) + len(markdown_links) + len(external_urls),
    }, distinct_targets


def count_links(text: str) -> dict[str, Any]:
    counts, _targets = analyze_links(text)
    return counts


def resolve_scan_root(vault_root: Path, scope: str | None) -> tuple[Path, str]:
    vault_root = vault_root.expanduser().resolve()
    if scope is None:
        return vault_root, ""
    scope_path = Path(scope)
    if scope_path.is_absolute():
        raise ValueError("--scope must be a relative path inside the Vault.")
    scan_root = (vault_root / scope_path).resolve()
    try:
        scan_root.relative_to(vault_root)
    except ValueError as exc:
        raise ValueError("--scope must stay inside the Vault root.") from exc
    if not scan_root.exists():
        raise FileNotFoundError(f"Scope path does not exist: {scope}")
    return scan_root, scope_path.as_posix().strip("/")


@observed("obsidian.stats.enumerate")
def iter_markdown_files(vault_root: Path, scan_root: Path, excluded_dirs: set[str]) -> list[Path]:
    vault_root = vault_root.resolve()
    files: list[Path] = []
    for file_path in scan_root.rglob("*.md"):
        try:
            rel_parts = file_path.relative_to(vault_root).parts
            resolved = file_path.resolve(strict=True)
            resolved.relative_to(vault_root)
        except (OSError, ValueError):
            continue
        if not resolved.is_file():
            continue
        if any(part.startswith(".") for part in rel_parts):
            continue
        if any(part in excluded_dirs for part in rel_parts[:-1]):
            continue
        files.append(file_path)
    files.sort(key=lambda path: path.relative_to(vault_root).as_posix().casefold())
    return files


def first_folder(relative_path: str) -> str:
    parts = Path(relative_path).parts
    return parts[0] if len(parts) > 1 else "根目录"


@observed("obsidian.stats.index_status")
def index_status_payload(
    *,
    kb_root: Path,
    db_path: Path,
    vault_root: Path,
    include_local_paths: bool,
    debug: bool,
) -> dict[str, Any]:
    command = [
        sys.executable,
        "-m",
        "obsidian_local_kb",
        "status",
        "--db",
        str(db_path),
        "--vault",
        str(vault_root),
        "--json",
    ]
    if include_local_paths or debug:
        command.append("--include-reindex-command")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        completed = subprocess.run(
            command,
            cwd=str(kb_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            check=False,
        )
        if completed.returncode != 0:
            payload: dict[str, Any] = {
                "db_exists": db_path.exists(),
                "status_method": "status_command_failed",
                "indexed_at_utc": "",
                "vault_markdown_count": 0,
                "indexed_note_count": 0,
                "newer_markdown_count": 0,
                "missing_indexed_count": 0,
                "index_stale": True,
                "reindex_hint": "Could not run obsidian_local_kb status; refresh the index before relying on index freshness.",
            }
            if debug:
                payload["status_error"] = completed.stderr.strip() or completed.stdout.strip()
            return payload
        return json.loads(completed.stdout)
    except Exception as exc:
        payload = {
            "db_exists": db_path.exists(),
            "status_method": "status_unavailable",
            "indexed_at_utc": "",
            "vault_markdown_count": 0,
            "indexed_note_count": 0,
            "newer_markdown_count": 0,
            "missing_indexed_count": 0,
            "index_stale": True,
            "reindex_hint": "Could not inspect index status; refresh the index before relying on index freshness.",
        }
        if debug:
            payload["status_error"] = str(exc)
        return payload


def empty_folder_stats() -> dict[str, int]:
    return {
        "note_count": 0,
        "body_chars": 0,
        "cjk_char_count": 0,
        "image_embed_count": 0,
        "total_link_count": 0,
    }


@observed("obsidian.stats.scan")
def build_stats(args: argparse.Namespace) -> OrderedDict[str, Any]:
    vault_root = Path(args.vault_root).expanduser().resolve()
    kb_root = Path(args.kb_root).expanduser().resolve()
    db_path = Path(args.db).expanduser().resolve()
    scan_root, scope_label = resolve_scan_root(vault_root, args.scope)
    excluded_dirs = set(DEFAULT_EXCLUDED_DIRS)
    excluded_dirs.update(args.exclude_dir)

    markdown_files = iter_markdown_files(vault_root, scan_root, excluded_dirs)
    folder_stats: dict[str, dict[str, int]] = {}
    largest_notes: list[dict[str, Any]] = []

    totals = Counter(
        {
            "note_count": 0,
            "raw_markdown_chars": 0,
            "body_chars": 0,
            "cjk_char_count": 0,
            "line_count": 0,
            "image_embed_count": 0,
            "distinct_image_target_count": 0,
            "wikilink_count": 0,
            "markdown_link_count": 0,
            "external_url_count": 0,
            "total_link_count": 0,
        }
    )
    all_image_targets: set[str] = set()

    for file_path in markdown_files:
        relative_path = file_path.relative_to(vault_root).as_posix()
        raw_text = read_markdown(file_path)
        counted_text = raw_text if args.include_frontmatter else strip_frontmatter(raw_text)
        link_counts, image_targets = analyze_links(counted_text)
        all_image_targets.update(image_targets)

        note_stats = {
            "raw_markdown_chars": len(raw_text),
            "body_chars": len(counted_text),
            "cjk_char_count": cjk_char_count(counted_text),
            "line_count": counted_text.count("\n") + (1 if counted_text else 0),
            **link_counts,
        }
        totals["note_count"] += 1
        for key, value in note_stats.items():
            if key != "distinct_image_target_count":
                totals[key] += int(value)

        folder = first_folder(relative_path)
        folder_entry = folder_stats.setdefault(folder, empty_folder_stats())
        folder_entry["note_count"] += 1
        folder_entry["body_chars"] += note_stats["body_chars"]
        folder_entry["cjk_char_count"] += note_stats["cjk_char_count"]
        folder_entry["image_embed_count"] += note_stats["image_embed_count"]
        folder_entry["total_link_count"] += note_stats["total_link_count"]

        largest_notes.append(
            {
                "relative_path": relative_path,
                "body_chars": note_stats["body_chars"],
                "cjk_char_count": note_stats["cjk_char_count"],
                "image_embed_count": note_stats["image_embed_count"],
                "total_link_count": note_stats["total_link_count"],
            }
        )

    totals["distinct_image_target_count"] = len(all_image_targets)
    top_folders = [
        {"folder": folder, **stats}
        for folder, stats in sorted(
            folder_stats.items(),
            key=lambda item: (item[1]["body_chars"], item[1]["note_count"], item[0]),
            reverse=True,
        )[: max(args.top_folders, 0)]
    ]
    largest_notes.sort(key=lambda item: (item["body_chars"], item["relative_path"]), reverse=True)

    return OrderedDict(
        [
            ("note_count", totals["note_count"]),
            ("raw_markdown_chars", totals["raw_markdown_chars"]),
            ("body_chars", totals["body_chars"]),
            ("cjk_char_count", totals["cjk_char_count"]),
            ("line_count", totals["line_count"]),
            ("image_embed_count", totals["image_embed_count"]),
            ("distinct_image_target_count", totals["distinct_image_target_count"]),
            ("wikilink_count", totals["wikilink_count"]),
            ("markdown_link_count", totals["markdown_link_count"]),
            ("external_url_count", totals["external_url_count"]),
            ("total_link_count", totals["total_link_count"]),
            ("top_folders", top_folders),
            ("largest_notes", largest_notes[: max(args.largest_notes, 0)]),
            (
                "index_status",
                index_status_payload(
                    kb_root=kb_root,
                    db_path=db_path,
                    vault_root=vault_root,
                    include_local_paths=args.include_local_paths,
                    debug=args.debug,
                ),
            ),
            (
                "counting_scope",
                {
                    "scope": scope_label,
                    "frontmatter_included": bool(args.include_frontmatter),
                    "excluded_dirs": sorted(excluded_dirs),
                    "generated_at_local": datetime.now().replace(microsecond=0).isoformat(),
                }
                | (
                    {
                        "vault_root": str(vault_root),
                        "kb_root": str(kb_root),
                        "db_path": str(db_path),
                    }
                    if args.include_local_paths
                    else {}
                ),
            ),
        ]
    )


def format_int(value: int) -> str:
    return f"{value:,}"


def markdown_report(stats: dict[str, Any]) -> str:
    scope = stats["counting_scope"]
    lines = [
        "# Obsidian Vault 统计",
        "",
        f"- 统计范围：{scope['scope'] or '整个 Vault'}",
        f"- 笔记数：{format_int(stats['note_count'])}",
        f"- 正文字符数：{format_int(stats['body_chars'])}",
        f"- Raw Markdown 字符数：{format_int(stats['raw_markdown_chars'])}",
        f"- 汉字数：{format_int(stats['cjk_char_count'])}",
        f"- 图片引用：{format_int(stats['image_embed_count'])} 次，去重目标 {format_int(stats['distinct_image_target_count'])} 个",
        f"- 链接总数：{format_int(stats['total_link_count'])}",
        f"- Wikilink：{format_int(stats['wikilink_count'])}",
        f"- Markdown 链接：{format_int(stats['markdown_link_count'])}",
        f"- 外部裸 URL：{format_int(stats['external_url_count'])}",
        f"- 索引是否过期：{stats['index_status'].get('index_stale')}",
        "",
        "## Top Folders",
        "",
        "| 文件夹 | 笔记数 | 正文字符 | 汉字 | 图片 | 链接 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in stats["top_folders"]:
        lines.append(
            "| {folder} | {note_count} | {body_chars} | {cjk_char_count} | {image_embed_count} | {total_link_count} |".format(
                folder=item["folder"],
                note_count=format_int(item["note_count"]),
                body_chars=format_int(item["body_chars"]),
                cjk_char_count=format_int(item["cjk_char_count"]),
                image_embed_count=format_int(item["image_embed_count"]),
                total_link_count=format_int(item["total_link_count"]),
            )
        )
    return "\n".join(lines)


def shorten_label(text: str, max_chars: int = 18) -> str:
    return text if len(text) <= max_chars else text[: max_chars - 1] + "…"


def svg_chart(stats: dict[str, Any]) -> str:
    width = 1200
    height = 720
    margin = 56
    title = "Obsidian Vault 统计"
    cards = [
        ("笔记", stats["note_count"]),
        ("正文字符", stats["body_chars"]),
        ("汉字", stats["cjk_char_count"]),
        ("图片引用", stats["image_embed_count"]),
        ("链接", stats["total_link_count"]),
    ]
    top_folders = stats["top_folders"][:8]
    max_chars = max([item["body_chars"] for item in top_folders] or [1])
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        "<title>Obsidian Vault statistics</title>",
        "<desc>Deterministic chart generated from vault_stats.py aggregate JSON.</desc>",
        "<style>",
        "text{font-family:'Microsoft YaHei','Segoe UI',Arial,sans-serif;fill:#172026}",
        ".muted{fill:#63717a}.card-title{font-size:18px;font-weight:600}.card-value{font-size:31px;font-weight:700}",
        ".axis{font-size:15px}.bar-label{font-size:15px}.small{font-size:13px}",
        "</style>",
        '<rect x="0" y="0" width="1200" height="720" fill="#f7f4ed"/>',
        '<rect x="0" y="0" width="1200" height="116" fill="#27343b"/>',
        f'<text x="{margin}" y="50" fill="#fffaf0" font-size="34" font-weight="700">{html.escape(title)}</text>',
        f'<text x="{margin}" y="92" fill="#d8e2e6" font-size="14">scope: {html.escape(stats["counting_scope"]["scope"] or "whole vault")} · index_stale: {html.escape(str(stats["index_status"].get("index_stale")))}</text>',
    ]

    card_w = 204
    card_h = 105
    gap = 18
    y = 148
    for index, (label, value) in enumerate(cards):
        x = margin + index * (card_w + gap)
        parts.append(f'<rect x="{x}" y="{y}" width="{card_w}" height="{card_h}" rx="8" fill="#ffffff" stroke="#d7d0c4"/>')
        parts.append(f'<text x="{x + 18}" y="{y + 35}" class="card-title">{html.escape(label)}</text>')
        parts.append(f'<text x="{x + 18}" y="{y + 78}" class="card-value">{html.escape(format_int(int(value)))}</text>')

    chart_x = margin
    chart_y = 330
    label_w = 220
    bar_x = chart_x + label_w
    bar_w = 820
    row_h = 42
    parts.append(f'<text x="{chart_x}" y="{chart_y - 24}" font-size="24" font-weight="700">正文字符最多的文件夹</text>')
    parts.append(f'<line x1="{bar_x}" y1="{chart_y - 8}" x2="{bar_x + bar_w}" y2="{chart_y - 8}" stroke="#a9b4b8"/>')

    for index, item in enumerate(top_folders):
        row_y = chart_y + index * row_h
        value = int(item["body_chars"])
        length = 1 if value == 0 else max(4, round(bar_w * value / max_chars))
        color = "#33658a" if index % 2 == 0 else "#6d8f3f"
        parts.append(f'<text x="{chart_x}" y="{row_y + 24}" class="bar-label">{html.escape(shorten_label(item["folder"]))}</text>')
        parts.append(f'<rect x="{bar_x}" y="{row_y + 6}" width="{length}" height="24" rx="4" fill="{color}"/>')
        parts.append(f'<text x="{bar_x + length + 10}" y="{row_y + 24}" class="axis">{html.escape(format_int(value))}</text>')

    footer = (
        "正文默认排除 YAML frontmatter；图片和链接为 Markdown 引用出现次数。"
        f" 生成时间：{stats['counting_scope']['generated_at_local']}"
    )
    parts.append(f'<text x="{margin}" y="{height - 38}" class="small muted">{html.escape(footer)}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def write_png_chart(stats: dict[str, Any], output_path: Path) -> None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise RuntimeError("PNG chart output requires Pillow. Use --chart svg or install Pillow.") from exc

    width, height = 1200, 720
    image = Image.new("RGB", (width, height), "#f7f4ed")
    draw = ImageDraw.Draw(image)
    font_path = Path(r"C:\Windows\Fonts\msyh.ttc")

    def font(size: int) -> Any:
        if font_path.exists():
            return ImageFont.truetype(str(font_path), size=size)
        return ImageFont.load_default()

    title_font = font(36)
    label_font = font(18)
    value_font = font(30)
    small_font = font(14)
    draw.rectangle((0, 0, width, 116), fill="#27343b")
    draw.text((56, 14), "Obsidian Vault 统计", fill="#fffaf0", font=title_font)
    draw.text(
        (56, 78),
        f"scope: {stats['counting_scope']['scope'] or 'whole vault'} · index_stale: {stats['index_status'].get('index_stale')}",
        fill="#d8e2e6",
        font=small_font,
    )

    cards = [
        ("笔记", stats["note_count"]),
        ("正文字符", stats["body_chars"]),
        ("汉字", stats["cjk_char_count"]),
        ("图片引用", stats["image_embed_count"]),
        ("链接", stats["total_link_count"]),
    ]
    card_w, card_h, gap, y = 204, 105, 18, 148
    for index, (label, value) in enumerate(cards):
        x = 56 + index * (card_w + gap)
        draw.rounded_rectangle((x, y, x + card_w, y + card_h), radius=8, fill="#ffffff", outline="#d7d0c4")
        draw.text((x + 18, y + 16), label, fill="#172026", font=label_font)
        draw.text((x + 18, y + 55), format_int(int(value)), fill="#172026", font=value_font)

    top_folders = stats["top_folders"][:8]
    max_chars = max([item["body_chars"] for item in top_folders] or [1])
    chart_x, chart_y, label_w, bar_w, row_h = 56, 330, 220, 820, 42
    draw.text((chart_x, chart_y - 50), "正文字符最多的文件夹", fill="#172026", font=font(24))
    for index, item in enumerate(top_folders):
        row_y = chart_y + index * row_h
        value = int(item["body_chars"])
        length = 1 if value == 0 else max(4, round(bar_w * value / max_chars))
        color = "#33658a" if index % 2 == 0 else "#6d8f3f"
        draw.text((chart_x, row_y + 6), shorten_label(item["folder"]), fill="#172026", font=small_font)
        draw.rounded_rectangle((chart_x + label_w, row_y + 6, chart_x + label_w + length, row_y + 30), radius=4, fill=color)
        draw.text((chart_x + label_w + length + 10, row_y + 6), format_int(value), fill="#172026", font=small_font)

    footer = "正文默认排除 YAML frontmatter；图片和链接为 Markdown 引用出现次数。"
    draw.text((56, height - 42), footer, fill="#63717a", font=small_font)
    image.save(output_path)


def write_chart(stats: dict[str, Any], chart_kind: str, output: str | None) -> None:
    if not output:
        raise ValueError("--output is required when --chart is used.")
    output_path = Path(output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if chart_kind == "svg":
        output_path.write_text(svg_chart(stats), encoding="utf-8")
        return
    if chart_kind == "png":
        write_png_chart(stats, output_path)
        return
    raise ValueError(f"Unsupported chart kind: {chart_kind}")


# Shared output support is resolved from this script, including importlib callers.
import sys as _output_sys
from pathlib import Path as _OutputPath
_output_dir = str(_OutputPath(__file__).resolve().parent)
if _output_dir not in _output_sys.path:
    _output_sys.path.insert(0, _output_dir)
from note_public_output import public_entry

@public_entry('vault_stats')
def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        stats = build_stats(args)
        if args.chart:
            write_chart(stats, args.chart, args.output)
    except Exception as exc:
        if args.debug:
            raise
        parser.exit(1, f"error: {exc}\n")

    if args.markdown:
        print(markdown_report(stats))
    else:
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    code = main()
    flush_observer()
    raise SystemExit(code)
