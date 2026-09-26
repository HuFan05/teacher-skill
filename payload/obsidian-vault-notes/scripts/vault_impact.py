"""Analyze cross-note impact in an Obsidian vault without writing files."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from _common import DEFAULT_VAULT_ROOT, configure_stdio
from _observer import flush as flush_observer
from _observer import observed


WIKILINK_RE = re.compile(r"(?<!!)\[\[([^\]\n]+)\]\]")
HEADING_RE = re.compile(r"^[ ]{0,3}(#{1,6})[ \t]+(.+?)[ \t]*$")
BLOCK_ANCHOR_RE = re.compile(r"\^([A-Za-z0-9_-]+)")
FENCE_OPEN_RE = re.compile(r"^[ ]{0,3}(`{3,}|~{3,}).*$")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze Obsidian cross-note link impact without writing files.")
    parser.add_argument("--vault-root", default=str(DEFAULT_VAULT_ROOT), help="Root of the Obsidian vault.")
    parser.add_argument("--target-note", help="Target note title or relative path.")
    parser.add_argument("--heading", help="Target heading to inspect, with or without leading #.")
    parser.add_argument("--block-anchor", help="Target block anchor, with or without leading ^.")
    parser.add_argument("--old-wikilink", help="Old wikilink text or target to find.")
    parser.add_argument("--new-wikilink", help="New wikilink target for suggested replacements.")
    parser.add_argument("--keyword", action="append", default=[], help="Keyword to search. May be passed multiple times.")
    parser.add_argument("--regex", help="Regular expression to search.")
    parser.add_argument("--limit", type=int, default=200, help="Maximum findings per category.")
    parser.add_argument("--snippet-chars", type=int, default=220, help="Maximum characters per suggested snippet.")
    parser.add_argument("--json", action="store_true", help="Print JSON. This is the default.")
    return parser


def normalize_slashes(value: str) -> str:
    return value.replace("\\", "/").strip()


def note_key(value: str) -> str:
    target = normalize_slashes(value)
    target = target.split("|", 1)[0].split("#", 1)[0].split("^", 1)[0].strip()
    if target.lower().endswith(".md"):
        target = target[:-3]
    return target.strip("/").casefold()


def clean_heading(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"^[# \t]+", "", value).strip()


def clean_anchor(value: str | None) -> str:
    if not value:
        return ""
    return value.strip().lstrip("^")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def is_markdown_inside_vault(vault_root: Path, path: Path) -> bool:
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(vault_root)
    except (OSError, ValueError):
        return False
    return resolved.is_file() and path.suffix.casefold() == ".md"


@observed("obsidian.impact.enumerate")
def iter_markdown(vault_root: Path) -> list[Path]:
    vault_root = vault_root.resolve()
    excluded = {".obsidian", ".trash"}
    files: list[Path] = []
    for path in vault_root.rglob("*.md"):
        try:
            rel_parts = path.relative_to(vault_root).parts
        except ValueError:
            continue
        if any(part in excluded for part in rel_parts):
            continue
        if not is_markdown_inside_vault(vault_root, path):
            continue
        files.append(path)
    return sorted(files, key=lambda p: p.relative_to(vault_root).as_posix().casefold())


@observed("obsidian.impact.build_index")
def note_index(vault_root: Path, files: list[Path]) -> dict[str, list[Path]]:
    index: dict[str, list[Path]] = {}

    def add(key: str, path: Path) -> None:
        normalized = note_key(key)
        if not normalized:
            return
        candidates = index.setdefault(normalized, [])
        if path not in candidates:
            candidates.append(path)

    for path in files:
        rel = path.relative_to(vault_root).as_posix()
        stem_rel = rel[:-3] if rel.lower().endswith(".md") else rel
        add(stem_rel, path)
        add(path.stem, path)
    return index


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


def parse_wikilinks(text: str) -> list[str]:
    return WIKILINK_RE.findall(mask_fenced_code(text))


def link_target_parts(link: str) -> tuple[str, str, str]:
    target = link.split("|", 1)[0].strip()
    anchor = ""
    heading = ""
    if "#^" in target:
        target, anchor = target.split("#^", 1)
    elif "^" in target and "#" not in target:
        target, anchor = target.split("^", 1)
    elif "#" in target:
        target, heading = target.split("#", 1)
    return note_key(target), clean_heading(heading), clean_anchor(anchor)


def unwrap_wikilink_spec(value: str) -> str:
    target = value.strip()
    if target.startswith("![[") and target.endswith("]]"):
        return target[3:-2].strip()
    if target.startswith("[[") and target.endswith("]]"):
        return target[2:-2].strip()
    return target


def file_record(vault_root: Path, path: Path) -> dict[str, Any]:
    return {"relative_path": path.relative_to(vault_root).as_posix(), "title": path.stem}


def snippet(line: str, max_chars: int) -> str:
    text = " ".join(line.split())
    if len(text) <= max_chars:
        return text
    if max_chars <= 1:
        return "…"
    return text[: max_chars - 1].rstrip() + "…"


def line_findings(
    *,
    vault_root: Path,
    path: Path,
    text: str,
    reason: str,
    predicate: Any,
    limit: int,
    snippet_chars: int,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if predicate(line):
            item = file_record(vault_root, path)
            item.update({"line": line_no, "reason": reason, "snippet": snippet(line, snippet_chars)})
            findings.append(item)
            if len(findings) >= limit:
                break
    return findings


# Shared output support is resolved from this script, including importlib callers.
import sys as _output_sys
from pathlib import Path as _OutputPath
_output_dir = str(_OutputPath(__file__).resolve().parent)
if _output_dir not in _output_sys.path:
    _output_sys.path.insert(0, _output_dir)
from note_public_output import public_entry

@public_entry('vault_impact')
@observed("obsidian.impact.scan")
def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    args = build_parser().parse_args(argv)
    vault_root = Path(args.vault_root).expanduser().resolve()
    files = iter_markdown(vault_root)
    notes = note_index(vault_root, files)
    target_key, target_spec_heading, target_spec_anchor = link_target_parts(args.target_note or "")
    target_heading = clean_heading(args.heading) or target_spec_heading
    target_anchor = clean_anchor(args.block_anchor) or target_spec_anchor
    target_candidates = notes.get(target_key, []) if target_key else []
    if len(target_candidates) > 1:
        payload = {
            "mode": "impact",
            "write": False,
            "error": "ambiguous_target",
            "target": {
                "target_note": args.target_note,
                "target_found": False,
                "target_relative_path": "",
                "target_candidates": [path.relative_to(vault_root).as_posix() for path in target_candidates],
                "heading": target_heading,
                "block_anchor": target_anchor,
                "old_wikilink": (args.old_wikilink or "").strip(),
                "new_wikilink": (args.new_wikilink or "").strip(),
            },
            "direct_backlinks": [],
            "outlinks": [],
            "heading_or_block_references": [],
            "suspected_link_updates": [],
            "broken_links": [],
            "suggested_reads": [],
            "limits": {"limit": args.limit, "snippet_chars": args.snippet_chars},
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2
    target_path = target_candidates[0] if target_candidates else None
    old_link_text = (args.old_wikilink or "").strip()
    old_link_spec = unwrap_wikilink_spec(old_link_text) if old_link_text else ""
    old_link_key, old_link_heading, old_link_anchor = (
        link_target_parts(old_link_spec) if old_link_spec else ("", "", "")
    )
    new_link = (args.new_wikilink or "").strip()
    regex = re.compile(args.regex) if args.regex else None

    direct_backlinks: list[dict[str, Any]] = []
    heading_or_block_refs: list[dict[str, Any]] = []
    suggested_reads: list[dict[str, Any]] = []
    suspected_link_updates: list[dict[str, Any]] = []
    broken_links: list[dict[str, Any]] = []
    outlinks: list[dict[str, Any]] = []

    for path in files:
        text = read_text(path)
        links = parse_wikilinks(text)
        file_is_related = bool(target_path and path == target_path)
        file_broken_links: list[dict[str, Any]] = []
        if target_path and path == target_path:
            seen: set[str] = set()
            for link in links:
                linked_key, heading, anchor = link_target_parts(link)
                key = linked_key or ""
                exists = bool(key and key in notes)
                if link not in seen:
                    outlinks.append({"link": link, "target_key": key, "heading": heading, "block_anchor": anchor, "exists": exists})
                    seen.add(link)

        for link in links:
            linked_key, heading, anchor = link_target_parts(link)
            target_note_matches = bool(
                target_key and linked_key in {target_key, Path(target_key).stem.casefold()}
            )
            heading_matches = bool(
                target_heading and clean_heading(heading).casefold() == target_heading.casefold()
            )
            anchor_matches = bool(
                target_anchor and clean_anchor(anchor).casefold() == target_anchor.casefold()
            )
            target_matches = bool(
                target_note_matches
                and (
                    (not target_heading and not target_anchor)
                    or heading_matches
                    or anchor_matches
                )
            )
            old_matches = bool(
                old_link_key
                and linked_key == old_link_key
                and (not old_link_heading or clean_heading(heading).casefold() == old_link_heading.casefold())
                and (not old_link_anchor or clean_anchor(anchor).casefold() == old_link_anchor.casefold())
                and ("|" not in old_link_spec or link == old_link_spec)
            )
            if target_matches:
                file_is_related = True
                item = file_record(vault_root, path)
                item.update({"link": link, "reason": "direct_backlink"})
                direct_backlinks.append(item)
                if heading_matches or anchor_matches:
                    ref = dict(item)
                    ref["reason"] = "heading_or_block_reference"
                    heading_or_block_refs.append(ref)
            if old_matches:
                file_is_related = True
                item = file_record(vault_root, path)
                item.update({"link": link, "reason": "old_wikilink_match", "suggested_replacement": new_link})
                suspected_link_updates.append(item)
            if linked_key and linked_key not in notes:
                item = file_record(vault_root, path)
                item.update({"link": link, "target_key": linked_key, "reason": "broken_wikilink"})
                file_broken_links.append(item)

        suggested_count_before = len(suggested_reads)
        if args.keyword:
            for keyword in args.keyword:
                suggested_reads.extend(
                    line_findings(
                        vault_root=vault_root,
                        path=path,
                        text=text,
                        reason=f"keyword:{keyword}",
                        predicate=lambda line, keyword=keyword: keyword in line,
                        limit=max(args.limit - len(suggested_reads), 0),
                        snippet_chars=args.snippet_chars,
                    )
                )
        if regex:
            suggested_reads.extend(
                line_findings(
                    vault_root=vault_root,
                    path=path,
                    text=text,
                    reason=f"regex:{args.regex}",
                    predicate=lambda line: bool(regex.search(line)),
                    limit=max(args.limit - len(suggested_reads), 0),
                    snippet_chars=args.snippet_chars,
                )
            )
        if len(suggested_reads) > suggested_count_before:
            file_is_related = True
        if file_is_related:
            broken_links.extend(file_broken_links)

    payload = {
        "mode": "impact",
        "write": False,
        "target": {
            "target_note": args.target_note,
            "target_found": bool(target_path),
            "target_relative_path": target_path.relative_to(vault_root).as_posix() if target_path else "",
            "target_candidates": [path.relative_to(vault_root).as_posix() for path in target_candidates],
            "heading": target_heading,
            "block_anchor": target_anchor,
            "old_wikilink": old_link_text,
            "new_wikilink": new_link,
        },
        "direct_backlinks": direct_backlinks[: args.limit],
        "outlinks": outlinks[: args.limit],
        "heading_or_block_references": heading_or_block_refs[: args.limit],
        "suspected_link_updates": suspected_link_updates[: args.limit],
        "broken_links": broken_links[: args.limit],
        "suggested_reads": suggested_reads[: args.limit],
        "limits": {"limit": args.limit, "snippet_chars": args.snippet_chars},
    }
    collections = {
        'direct_backlinks': direct_backlinks,
        'outlinks': outlinks,
        'heading_or_block_references': heading_or_block_refs,
        'suspected_link_updates': suspected_link_updates,
        'broken_links': broken_links,
        'suggested_reads': suggested_reads,
    }
    payload['source_coverage'] = {
        name: {
            'observed': len(rows),
            'returned': len(payload[name]),
            'complete': len(rows) <= args.limit if name != 'suggested_reads' else len(rows) < args.limit,
            'count_basis': 'total' if name != 'suggested_reads' else 'lower_bound',
        } for name, rows in collections.items()
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    code = main()
    flush_observer()
    raise SystemExit(code)
