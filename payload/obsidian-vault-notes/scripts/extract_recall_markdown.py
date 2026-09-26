from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


class RecallContractError(ValueError):
    """Raised when a recall response cannot authorize one complete exact-note reread."""


def load_json(path: Path) -> Any:
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = raw.decode("utf-16")
    else:
        text = raw.decode("utf-8-sig")
    return json.loads(text)


def resolve_live_note(payload: Any, vault_root: Path, expected_note: str | None = None) -> tuple[Path, str]:
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RecallContractError("recall payload must be a successful JSON object")
    if payload.get("mode") != "note":
        raise RecallContractError("only exact-note recall payloads are accepted")
    if payload.get("content_mode") != "whole_note" or payload.get("whole_note_requested") is not True:
        raise RecallContractError("recall must use --whole-note")
    if payload.get("truncated") is not False:
        raise RecallContractError("top-level recall response is truncated or lacks a false truncation flag")

    notes = payload.get("expanded_notes")
    if not isinstance(notes, list) or len(notes) != 1 or not isinstance(notes[0], dict):
        raise RecallContractError("exact-note recall must contain exactly one expanded note")
    note = notes[0]
    if note.get("content_source") != "disk":
        raise RecallContractError("note content must come from the live disk file")
    if note.get("truncated") is not False:
        raise RecallContractError("expanded note is truncated or lacks a false truncation flag")
    relative_path = note.get("relative_path")
    if not isinstance(relative_path, str) or not relative_path:
        raise RecallContractError("expanded note lacks relative_path")
    normalized_relative = relative_path.replace("\\", "/")
    if expected_note is not None and normalized_relative.casefold() != expected_note.replace("\\", "/").casefold():
        raise RecallContractError(f"resolved note {relative_path!r} does not match expected note {expected_note!r}")

    root = vault_root.expanduser().resolve()
    source = (root / Path(normalized_relative)).resolve()
    try:
        source.relative_to(root)
    except ValueError as exc:
        raise RecallContractError("resolved note escapes the declared Vault root") from exc
    if source.suffix.casefold() != ".md" or not source.is_file():
        raise RecallContractError("resolved note is not an existing Markdown file")
    return source, normalized_relative


def extract_complete_markdown(
    payload: Any, vault_root: Path, expected_note: str | None = None
) -> tuple[bytes, dict[str, Any]]:
    source, relative_path = resolve_live_note(payload, vault_root, expected_note)
    markdown = source.read_bytes()
    receipt = {
        "schema_version": "obsidian-recall-markdown/v1",
        "relative_path": relative_path,
        "source": "current-live-disk-file-reread",
        "byte_count": len(markdown),
        "markdown_sha256": hashlib.sha256(markdown).hexdigest(),
    }
    return markdown, receipt


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False)
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Copy one recall-authorized current Markdown note from the live Vault")
    parser.add_argument("--recall-json", required=True, type=Path)
    parser.add_argument("--vault-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--expected-note", help="Expected vault-relative Markdown path")
    return parser


# Shared output support is resolved from this script, including importlib callers.
import sys as _output_sys
from pathlib import Path as _OutputPath
_output_dir = str(_OutputPath(__file__).resolve().parent)
if _output_dir not in _output_sys.path:
    _output_sys.path.insert(0, _output_dir)
from note_public_output import public_entry

@public_entry('extract_recall_markdown')
def main() -> int:
    args = build_parser().parse_args()
    try:
        markdown, receipt = extract_complete_markdown(load_json(args.recall_json), args.vault_root, args.expected_note)
        source, _ = resolve_live_note(load_json(args.recall_json), args.vault_root, args.expected_note)
        if args.output.resolve() == source:
            raise RecallContractError("output must not overwrite the live Vault source note")
        atomic_write(args.output, markdown)
        print(json.dumps({"ok": True, **receipt}, ensure_ascii=False, sort_keys=True))
        return 0
    except (RecallContractError, OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
