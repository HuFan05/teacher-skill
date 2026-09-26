from __future__ import annotations

import re
from pathlib import Path


ASCII_WORD_RE = re.compile(r"[A-Za-z0-9_]+(?:[-'][A-Za-z0-9_]+)*")
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
WHITESPACE_RE = re.compile(r"\s+")


def normalize_text(value: str) -> str:
    value = value.replace("\\", "/").strip().lower()
    value = WHITESPACE_RE.sub(" ", value)
    return value


def normalize_note_key(value: str) -> str:
    value = normalize_text(value)
    if value.endswith(".md"):
        value = value[:-3]
    return value.strip("/")


def tokenize_search_text(text: str) -> list[str]:
    if not text:
        return []

    lowered = normalize_text(text)
    tokens: list[str] = []
    seen: set[str] = set()

    for match in ASCII_WORD_RE.finditer(lowered):
        token = match.group(0)
        if token not in seen:
            seen.add(token)
            tokens.append(token)

    for match in CJK_RE.finditer(lowered):
        run = match.group(0)
        if run and run not in seen:
            seen.add(run)
            tokens.append(run)
        if len(run) == 1:
            continue
        for idx in range(len(run) - 1):
            token = run[idx : idx + 2]
            if token not in seen:
                seen.add(token)
                tokens.append(token)

    return tokens


def build_match_query(text: str) -> str:
    chunks = [chunk for chunk in WHITESPACE_RE.split(normalize_text(text)) if chunk]
    if not chunks:
        raise ValueError("Query produced no searchable tokens.")

    groups: list[str] = []
    for chunk in chunks[:8]:
        tokens = tokenize_search_text(chunk)
        if not tokens:
            continue
        limited = tokens[:6]
        escaped = [token.replace('"', '""') for token in limited]
        if len(escaped) == 1:
            groups.append(f'"{escaped[0]}"')
        else:
            groups.append("(" + " OR ".join(f'"{token}"' for token in escaped) + ")")

    if not groups:
        raise ValueError("Query produced no searchable tokens.")
    return " AND ".join(groups)


def make_search_blob(*parts: str) -> str:
    tokens: list[str] = []
    seen: set[str] = set()
    for part in parts:
        for token in tokenize_search_text(part):
            if token not in seen:
                seen.add(token)
                tokens.append(token)
    return " ".join(tokens)


def compact_snippet(text: str, limit: int = 240) -> str:
    text = WHITESPACE_RE.sub(" ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def relative_markdown_path(vault_root: Path, file_path: Path) -> str:
    rel = file_path.relative_to(vault_root).as_posix()
    return rel


def note_key_from_relative_path(rel_path: str) -> str:
    path = rel_path.replace("\\", "/")
    if path.lower().endswith(".md"):
        path = path[:-3]
    return normalize_note_key(path)
