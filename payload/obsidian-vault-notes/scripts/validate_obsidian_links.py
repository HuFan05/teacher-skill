from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath


WIKILINK_RE = re.compile(r"(!?)\[\[([^\]\n]+)\]\]")
HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$")
BLOCK_RE = re.compile(r"(?:^|\s)\^([A-Za-z0-9-]+)\s*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
MAX_LIVE_CODE_BYTES = 2048


@dataclass(frozen=True)
class Link:
    raw: str
    target: str
    anchor_kind: str | None
    anchor: str | None
    line: int
    embed: bool


def _strip_code(text: str) -> str:
    output: list[str] = []
    fence: str | None = None
    for line in text.splitlines(keepends=True):
        marker = FENCE_RE.match(line)
        if marker:
            token = marker.group(1)
            fence = None if fence and token.startswith(fence[0]) else token
            output.append("\n" if line.endswith("\n") else "")
        elif fence:
            output.append("\n" if line.endswith("\n") else "")
        else:
            output.append(INLINE_CODE_RE.sub("", line))
    return "".join(output)


def _split_link(value: str, line: int, embed: bool) -> Link | None:
    core = value.split("|", 1)[0].strip()
    if not core or "://" in core:
        return None
    target, kind, anchor = core, None, None
    if "#^" in core:
        target, anchor = core.split("#^", 1)
        kind = "block"
    elif "#" in core:
        target, anchor = core.split("#", 1)
        kind = "heading"
    return Link(value, target.strip(), kind, anchor.strip() if anchor else None, line, embed)


def extract_links(text: str) -> list[Link]:
    clean = _strip_code(text)
    links: list[Link] = []
    for match in WIKILINK_RE.finditer(clean):
        parsed = _split_link(match.group(2), clean.count("\n", 0, match.start()) + 1, bool(match.group(1)))
        if parsed:
            links.append(parsed)
    return links


def _norm(value: str) -> str:
    value = re.sub(r"[*_~=`]", "", value).strip().casefold()
    return re.sub(r"\s+", " ", value)


def _vault_files(vault: Path) -> list[Path]:
    ignored = {".git", ".obsidian", ".trash"}
    return [p for p in vault.rglob("*") if p.is_file() and not any(part in ignored for part in p.relative_to(vault).parts)]


def _resolve_target(link: Link, source: Path, vault: Path, files: list[Path]) -> tuple[Path | None, str | None]:
    if not link.target:
        return source, None
    target = link.target.replace("\\", "/").strip("/")
    suffix = PurePosixPath(target).suffix
    candidates: list[Path] = []
    if "/" in target or suffix:
        variants = [target] if suffix else [target + ".md", target]
        for variant in variants:
            direct = (vault / Path(variant)).resolve()
            relative = (source.parent / Path(variant)).resolve()
            for item in (direct, relative):
                if item in files and item not in candidates:
                    candidates.append(item)
    else:
        wanted = target.casefold()
        for item in files:
            rel = item.relative_to(vault).as_posix()
            if item.stem.casefold() == wanted or rel.casefold() == wanted or rel.casefold() == (wanted + ".md"):
                candidates.append(item)
    if not candidates:
        return None, "missing_target"
    if len(candidates) > 1:
        return None, "ambiguous_target"
    return candidates[0], None


def filesystem_audit(note: Path, vault: Path) -> dict[str, object]:
    note = note.resolve()
    vault = vault.resolve()
    text = note.read_text(encoding="utf-8-sig")
    files = _vault_files(vault)
    issues: list[dict[str, object]] = []
    checked: list[dict[str, object]] = []
    for link in extract_links(text):
        target, error = _resolve_target(link, note, vault, files)
        item = {"raw": link.raw, "line": link.line, "target": None if target is None else target.relative_to(vault).as_posix()}
        if error:
            issues.append({**item, "code": error})
            continue
        if link.anchor and target and target.suffix.casefold() == ".md":
            target_text = target.read_text(encoding="utf-8-sig")
            if link.anchor_kind == "heading":
                headings = {_norm(m.group(1)) for line in target_text.splitlines() if (m := HEADING_RE.match(line))}
                if _norm(link.anchor) not in headings:
                    issues.append({**item, "code": "missing_heading", "anchor": link.anchor})
                    continue
            elif link.anchor_kind == "block":
                blocks = {m.group(1) for line in target_text.splitlines() if (m := BLOCK_RE.search(line))}
                if link.anchor not in blocks:
                    issues.append({**item, "code": "missing_block", "anchor": link.anchor})
                    continue
        checked.append(item)
    return {"ok": not issues, "mode": "filesystem", "file": note.relative_to(vault).as_posix(), "links": len(checked) + len(issues), "checked": checked, "issues": issues}


def _obsidian_cli() -> str | None:
    explicit = os.environ.get("OBSIDIAN_CLI")
    if explicit and Path(explicit).is_file():
        return explicit
    located = shutil.which("obsidian") or shutil.which("Obsidian.com")
    if located:
        return located
    if os.name == "nt":
        candidate = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Obsidian" / "Obsidian.com"
        if candidate.is_file():
            return str(candidate)
    return None


def _build_live_js(source: str, payload: list[dict[str, object]]) -> str:
    return (
        "JSON.stringify((" + json.dumps(payload, ensure_ascii=False) + ").map(x=>{"
        "const f=app.metadataCache.getFirstLinkpathDest(x.target," + json.dumps(source) + ");"
        "if(!f)return {...x,ok:false,reason:'unresolved_target'};"
        "const c=app.metadataCache.getFileCache(f);"
        "if(x.anchorKind==='heading'&&!((c?.headings)||[]).some(h=>h.heading===x.anchor))return {...x,ok:false,reason:'unresolved_heading'};"
        "if(x.anchorKind==='block'&&!(c?.blocks&&c.blocks[x.anchor]))return {...x,ok:false,reason:'unresolved_block'};"
        "return {...x,ok:true,path:f.path};}))"
    )


def _live_payload_batches(source: str, payload: list[dict[str, object]], max_code_bytes: int = MAX_LIVE_CODE_BYTES) -> list[list[dict[str, object]]]:
    batches: list[list[dict[str, object]]] = []
    current: list[dict[str, object]] = []
    for item in payload:
        candidate = [*current, item]
        if len(("code=" + _build_live_js(source, candidate)).encode("utf-8")) <= max_code_bytes:
            current = candidate
            continue
        if not current:
            raise ValueError("single_link_exceeds_live_code_budget")
        batches.append(current)
        current = [item]
        if len(("code=" + _build_live_js(source, current)).encode("utf-8")) > max_code_bytes:
            raise ValueError("single_link_exceeds_live_code_budget")
    if current:
        batches.append(current)
    return batches


def _parse_cli_json(stdout: str) -> object:
    text = stdout.strip()
    if text.startswith("=>"):
        text = text[2:].lstrip()
    return json.loads(text)


def live_audit(note: Path, vault: Path, links: list[Link]) -> dict[str, object]:
    cli = _obsidian_cli()
    if not cli:
        return {"ok": False, "checked": False, "reason": "live_check_unavailable", "cause": "obsidian_cli_not_found", "recovery": "Do not launch, inspect, or configure Obsidian through GUI automation. Retry only when the user explicitly requests application-level verification."}
    if not links:
        return {"ok": True, "checked": True, "issues": [], "links": 0}
    payload = [{"target": link.target, "anchorKind": link.anchor_kind, "anchor": link.anchor, "line": link.line} for link in links]
    source = note.relative_to(vault).as_posix()
    try:
        batches = _live_payload_batches(source, payload)
    except ValueError as exc:
        return {"ok": False, "checked": False, "reason": "obsidian_cli_request_too_large", "detail": str(exc)}
    rows: list[dict[str, object]] = []
    for batch in batches:
        js = _build_live_js(source, batch)
        try:
            completed = subprocess.run([cli, f"vault={vault.name}", "eval", f"code={js}"], text=True, encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"ok": False, "checked": False, "reason": "live_check_unavailable", "cause": "obsidian_cli_failed", "detail": str(exc), "recovery": "Do not launch, inspect, or configure Obsidian through GUI automation."}
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            cause = "obsidian_cli_disabled" if "not enabled" in detail.casefold() else "obsidian_cli_failed"
            return {"ok": False, "checked": False, "reason": "live_check_unavailable", "cause": cause, "detail": detail[:500], "recovery": "Do not launch, inspect, or configure Obsidian through GUI automation."}
        try:
            batch_rows = _parse_cli_json(completed.stdout)
        except json.JSONDecodeError:
            return {"ok": False, "checked": False, "reason": "obsidian_cli_invalid_json", "detail": completed.stdout[:500]}
        if not isinstance(batch_rows, list) or len(batch_rows) != len(batch) or not all(isinstance(row, dict) for row in batch_rows):
            return {"ok": False, "checked": False, "reason": "obsidian_cli_invalid_result", "detail": completed.stdout[:500]}
        rows.extend(batch_rows)
    failures = [row for row in rows if not row.get("ok")]
    return {"ok": not failures, "checked": True, "issues": failures, "links": len(rows)}


# Shared output support is resolved from this script, including importlib callers.
import sys as _output_sys
from pathlib import Path as _OutputPath
_output_dir = str(_OutputPath(__file__).resolve().parent)
if _output_dir not in _output_sys.path:
    _output_sys.path.insert(0, _output_dir)
from note_public_output import public_entry

@public_entry('validate_obsidian_links')
def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Obsidian wikilinks, headings, and block anchors.")
    parser.add_argument("file", type=Path)
    parser.add_argument("--vault-root", type=Path, required=True)
    parser.add_argument("--live-mode", choices=("off", "auto", "required"), default="off")
    args = parser.parse_args()
    result = filesystem_audit(args.file, args.vault_root)
    links = extract_links(args.file.read_text(encoding="utf-8-sig"))
    if args.live_mode != "off" and result["ok"]:
        result["obsidian"] = live_audit(args.file.resolve(), args.vault_root.resolve(), links)
        if args.live_mode == "required" and not result["obsidian"]["ok"]:
            result["ok"] = False
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
