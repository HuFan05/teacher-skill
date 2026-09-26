#!/usr/bin/env python3
"""Validate Markdown formulas offline, with optional live Obsidian rendering.

Delimiter and group-structure checks always run locally. Live rendering is an
explicit additional check and never authorizes GUI automation.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import time
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


MAX_EVAL_BYTES = 2048
CONTROL_WORD_RE = re.compile(r"\\([A-Za-z]+)")
BARE_WORD_RE = re.compile(r"(?<![\\A-Za-z])([A-Za-z]{3,})(?![A-Za-z])")


@dataclass(frozen=True)
class Formula:
    tex: str
    display: bool
    line: int


@dataclass(frozen=True)
class ValidationError:
    code: str
    line: int | None
    message: str


def line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def is_escaped(text: str, offset: int) -> bool:
    backslashes = 0
    cursor = offset - 1
    while cursor >= 0 and text[cursor] == "\\":
        backslashes += 1
        cursor -= 1
    return backslashes % 2 == 1


def mask_inline_code(line: str) -> str:
    chars = list(line)
    cursor = 0
    while cursor < len(line):
        if line[cursor] != "`":
            cursor += 1
            continue
        run_end = cursor
        while run_end < len(line) and line[run_end] == "`":
            run_end += 1
        marker = line[cursor:run_end]
        close = line.find(marker, run_end)
        if close < 0:
            cursor = run_end
            continue
        for index in range(cursor, close + len(marker)):
            if chars[index] not in "\r\n":
                chars[index] = " "
        cursor = close + len(marker)
    return "".join(chars)


def mask_code(text: str) -> str:
    output: list[str] = []
    fence_char: str | None = None
    fence_length = 0
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        match = re.match(r"(`{3,}|~{3,})", stripped)
        if fence_char is not None:
            output.append("".join("\n" if c == "\n" else "\r" if c == "\r" else " " for c in line))
            if match and match.group(1)[0] == fence_char and len(match.group(1)) >= fence_length:
                fence_char = None
                fence_length = 0
            continue
        if match:
            fence_char = match.group(1)[0]
            fence_length = len(match.group(1))
            output.append("".join("\n" if c == "\n" else "\r" if c == "\r" else " " for c in line))
            continue
        output.append(mask_inline_code(line))
    return "".join(output)


def find_unescaped(text: str, marker: str, start: int, stop_at_newline: bool) -> int:
    cursor = start
    while cursor < len(text):
        if stop_at_newline and text[cursor] in "\r\n":
            return -1
        found = text.find(marker, cursor)
        if found < 0 or (stop_at_newline and "\n" in text[cursor:found]):
            return -1
        if not is_escaped(text, found):
            if marker == "$" and ((found > 0 and text[found - 1] == "$") or (found + 1 < len(text) and text[found + 1] == "$")):
                cursor = found + 1
                continue
            return found
        cursor = found + len(marker)
    return -1


def extract_formulas(text: str) -> tuple[list[Formula], list[ValidationError]]:
    masked = mask_code(text)
    formulas: list[Formula] = []
    errors: list[ValidationError] = []
    for marker in (r"\(", r"\)", r"\[", r"\]"):
        cursor = 0
        while True:
            found = masked.find(marker, cursor)
            if found < 0:
                break
            if not is_escaped(masked, found):
                errors.append(ValidationError("unsupported_delimiter", line_number(masked, found), f"Use Obsidian delimiters $...$ or $$...$$ instead of {marker}."))
            cursor = found + len(marker)
    cursor = 0
    while cursor < len(masked):
        if masked[cursor] != "$" or is_escaped(masked, cursor):
            cursor += 1
            continue
        display = masked.startswith("$$", cursor)
        marker = "$$" if display else "$"
        content_start = cursor + len(marker)
        close = find_unescaped(masked, marker, content_start, stop_at_newline=not display)
        if close < 0:
            errors.append(ValidationError("unclosed_display_formula" if display else "unclosed_inline_formula", line_number(masked, cursor), f"Unclosed {marker} formula delimiter."))
            cursor += len(marker)
            continue
        tex = text[content_start:close]
        if not tex.strip():
            errors.append(ValidationError("empty_formula", line_number(masked, cursor), f"Empty {marker} formula span."))
        else:
            formulas.append(Formula(tex.strip(), display, line_number(masked, cursor)))
        cursor = close + len(marker)
    return formulas, errors


def validate_tex_structure(formulas: list[Formula]) -> list[ValidationError]:
    errors: list[ValidationError] = []
    for formula in formulas:
        depth = 0
        for offset, char in enumerate(formula.tex):
            if char not in "{}" or is_escaped(formula.tex, offset):
                continue
            if char == "{":
                depth += 1
            elif depth == 0:
                errors.append(ValidationError("unexpected_group_close", formula.line, "Unexpected unescaped } in formula span."))
                break
            else:
                depth -= 1
        if depth:
            errors.append(ValidationError("unclosed_group", formula.line, f"Formula span has {depth} unclosed group(s)."))
    return errors


def validate_tex_transport(formulas: list[Formula]) -> list[ValidationError]:
    """Detect likely backslashes lost before TeX reached the validator.

    A final TeX parser cannot reconstruct a deleted backslash because a bare
    alphabetic token remains valid TeX. The strongest
    low-false-positive offline signal available from one note is a
    control-word shadow: the note contains both a TeX control word and the
    same alphabetic token without its backslash.
    """
    control_words = {
        match.group(1)
        for formula in formulas
        for match in CONTROL_WORD_RE.finditer(formula.tex)
    }
    if not control_words:
        return []
    errors: list[ValidationError] = []
    seen: set[tuple[int, str]] = set()
    for formula in formulas:
        for match in BARE_WORD_RE.finditer(formula.tex):
            name = match.group(1)
            key = (formula.line, name)
            if name not in control_words or key in seen:
                continue
            seen.add(key)
            errors.append(
                ValidationError(
                    "suspicious_bare_tex_command",
                    formula.line,
                    f"Bare formula token '{name}' shadows TeX control sequence '\\{name}' used in this note; a transport layer may have removed its backslash.",
                )
            )
    return errors


def tex_transport_shadow_counts(text: str) -> Counter[str]:
    formulas, _delimiter_errors = extract_formulas(text)
    control_words = {
        match.group(1)
        for formula in formulas
        for match in CONTROL_WORD_RE.finditer(formula.tex)
    }
    counts: Counter[str] = Counter()
    seen: set[tuple[int, str]] = set()
    for formula in formulas:
        for match in BARE_WORD_RE.finditer(formula.tex):
            name = match.group(1)
            key = (formula.line, name)
            if name in control_words and key not in seen:
                seen.add(key)
                counts[name] += 1
    return counts


def discover_obsidian_cli(explicit: Path | None) -> Path:
    if explicit is not None:
        if not explicit.is_file():
            raise FileNotFoundError(f"Obsidian CLI not found: {explicit}")
        return explicit.resolve()
    candidates: list[Path] = []
    localappdata = os.environ.get("LOCALAPPDATA")
    if localappdata:
        candidates.extend([
            Path(localappdata) / "Obsidian" / "Obsidian.com",
            Path(localappdata) / "Programs" / "Obsidian" / "Obsidian.com",
        ])
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    found = shutil.which("Obsidian.com") or shutil.which("obsidian")
    if found:
        return Path(found).resolve()
    raise FileNotFoundError("Obsidian CLI was not found; pass --obsidian-cli.")


def build_eval_code(formulas: list[Formula]) -> str:
    items = json.dumps([asdict(item) for item in formulas], ensure_ascii=False, separators=(",", ":"))
    return (
        "(()=>{const a=" + items + ";"
        "return JSON.stringify(a.map(x=>{try{const n=MathJax.tex2chtml(x.tex,{display:x.display});"
        "const e=n.matches?.('mjx-merror,.math-error,[data-mjx-error]')||"
        "n.querySelector?.('mjx-merror,.math-error,[data-mjx-error]');"
        "return e?{ok:false,line:x.line,tex:x.tex,message:e.title||e.textContent||'MathJax render error'}:"
        "{ok:true,line:x.line};}catch(e){return {ok:false,line:x.line,tex:x.tex,message:String(e)}}}));})()"
    )


def make_batches(formulas: list[Formula]) -> list[list[Formula]]:
    batches: list[list[Formula]] = []
    current: list[Formula] = []
    for formula in formulas:
        candidate = [*current, formula]
        if len(build_eval_code(candidate).encode("utf-8")) <= MAX_EVAL_BYTES:
            current = candidate
            continue
        if not current:
            raise ValueError(f"Formula on line {formula.line} is too large for the bounded Obsidian CLI request.")
        batches.append(current)
        current = [formula]
        if len(build_eval_code(current).encode("utf-8")) > MAX_EVAL_BYTES:
            raise ValueError(f"Formula on line {formula.line} is too large for the bounded Obsidian CLI request.")
    if current:
        batches.append(current)
    return batches


def parse_cli_json(stdout: str) -> list[dict[str, Any]]:
    candidates = [line.strip() for line in stdout.splitlines() if line.strip()]
    for candidate in reversed(candidates):
        if candidate.startswith("=>"):
            candidate = candidate[2:].strip()
        try:
            decoded = json.loads(candidate)
            if isinstance(decoded, str):
                decoded = json.loads(decoded)
            if isinstance(decoded, list):
                return decoded
        except (json.JSONDecodeError, TypeError):
            continue
    raise RuntimeError(f"Obsidian CLI returned no parseable render result: {stdout[-500:]}")


def run_mathjax(formulas: list[Formula], cli: Path, timeout_seconds: int) -> tuple[list[dict[str, Any]], int]:
    results: list[dict[str, Any]] = []
    batches = make_batches(formulas)
    for batch in batches:
        code = build_eval_code(batch)
        completed = subprocess.run([str(cli), "eval", f"code={code}"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout_seconds, check=False)
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(f"Obsidian CLI eval failed (exit {completed.returncode}): {detail[-500:]}")
        results.extend(parse_cli_json(completed.stdout))
    return results, len(batches)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Validate Markdown formulas offline, with optional live Obsidian rendering.")
    result.add_argument("file", type=Path, help="Markdown note to validate.")
    result.add_argument("--obsidian-cli", type=Path, help="Explicit Obsidian.com path.")
    result.add_argument("--live-mode", choices=("off", "auto", "required"), default="off", help="Live Obsidian rendering is opt-in; required is only for an explicit application-level verification request.")
    result.add_argument("--fail-on-transport-shadow", action="store_true", help="Fail when a bare formula token shadows a TeX control word in the same note. Existing-note edits normally enforce only newly introduced shadows in vault_edit.py.")
    result.add_argument("--timeout-seconds", type=int, default=30)
    return result


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


# Shared output support is resolved from this script, including importlib callers.
import sys as _output_sys
from pathlib import Path as _OutputPath
_output_dir = str(_OutputPath(__file__).resolve().parent)
if _output_dir not in _output_sys.path:
    _output_sys.path.insert(0, _output_dir)
from note_public_output import public_entry

@public_entry('validate_obsidian_formulas')
def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    started = time.perf_counter()
    note = args.file.resolve()
    base: dict[str, Any] = {"schema_version": 4, "validator": "obsidian-formulas", "file": str(note), "live_mode": args.live_mode}
    try:
        text = note.read_text(encoding="utf-8-sig")
    except Exception as exc:
        emit({**base, "ok": False, "stage": "read", "errors": [{"message": str(exc)}]})
        return 2
    formulas, delimiter_errors = extract_formulas(text)
    counts = {"total": len(formulas), "inline": sum(not x.display for x in formulas), "display": sum(x.display for x in formulas)}
    if delimiter_errors:
        emit({**base, "ok": False, "stage": "delimiter", "formula_counts": counts, "errors": [asdict(e) for e in delimiter_errors], "recovery": "Repair the Markdown formula delimiters, then rerun this validator."})
        return 2
    structure_errors = validate_tex_structure(formulas)
    if structure_errors:
        emit({**base, "ok": False, "stage": "offline_structure", "formula_counts": counts, "errors": [asdict(e) for e in structure_errors], "recovery": "Repair the reported TeX group structure, then rerun this validator."})
        return 2
    transport_errors = validate_tex_transport(formulas)
    if transport_errors and args.fail_on_transport_shadow:
        emit({**base, "ok": False, "stage": "offline_transport", "formula_counts": counts, "errors": [asdict(e) for e in transport_errors], "recovery": "Compare the staged payload with the intended TeX, restore any lost backslashes, and resend it through a raw or literal transport channel."})
        return 2
    transport_audit = {"warnings": [asdict(e) for e in transport_errors], "warning_count": len(transport_errors)}
    if not formulas:
        emit({**base, "ok": True, "stage": "complete", "formula_counts": counts, "transport_audit": transport_audit, "backend": {"status": "not_needed_no_formulas"}, "obsidian": {"checked": False, "reason": "not_needed_no_formulas"}, "elapsed_ms": round((time.perf_counter() - started) * 1000, 3)})
        return 0
    if args.live_mode == "off":
        emit({**base, "ok": True, "stage": "complete", "formula_counts": counts, "transport_audit": transport_audit, "backend": {"status": "offline_structure_passed"}, "obsidian": {"checked": False, "reason": "live_mode_off"}, "elapsed_ms": round((time.perf_counter() - started) * 1000, 3)})
        return 0
    try:
        cli = discover_obsidian_cli(args.obsidian_cli)
        render_results, batch_count = run_mathjax(formulas, cli, args.timeout_seconds)
    except Exception as exc:
        live = {"ok": False, "checked": False, "reason": "live_check_unavailable", "detail": str(exc)}
        recovery = "Report live_check_unavailable. Do not launch, inspect, or configure Obsidian through GUI automation. Run required mode later only when the user explicitly requests application-level verification."
        emit({**base, "ok": args.live_mode == "auto", "stage": "complete" if args.live_mode == "auto" else "live_backend", "formula_counts": counts, "backend": {"status": "offline_structure_passed"}, "obsidian": live, "recovery": recovery, "elapsed_ms": round((time.perf_counter() - started) * 1000, 3)})
        return 0 if args.live_mode == "auto" else 3
    failures = [item for item in render_results if not item.get("ok")]
    rendered = len(render_results) - len(failures)
    ok = not failures and len(render_results) == len(formulas)
    emit({**base, "ok": ok, "stage": "complete" if ok else "render", "formula_counts": counts, "rendered": rendered, "errors": failures, "backend": {"status": "offline_structure_passed"}, "obsidian": {"ok": ok, "checked": True, "engine": "Obsidian global MathJax.tex2chtml", "transport": "Obsidian CLI eval", "cli": str(cli), "batches": batch_count}, "elapsed_ms": round((time.perf_counter() - started) * 1000, 3), **({} if ok else {"recovery": "Repair every reported formula and rerun this validator."})})
    return 0 if ok else 4


if __name__ == "__main__":
    raise SystemExit(main())
