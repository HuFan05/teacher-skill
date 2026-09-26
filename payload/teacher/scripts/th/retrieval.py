"""Local retrieval with progressive disclosure.

The index only helps find things; it is never the source of truth. A search
returns *candidates* — path, heading, a short snippet and a match score — and
nothing more. Reading a candidate re-opens the file on disk, re-extracts the
section and checks it against the indexed digest, so an edit made after
indexing is read as it is now, and a section that no longer exists is reported
as stale instead of being served from the index.

Disclosure is bounded at every step:

    search      at most MAX_CANDIDATES candidates, each with a 200-char snippet,
                plus how many further matches were *not* shown
    read        one section, at most MAX_EXCERPT_CHARS characters
    per ask     this Skill charges every excerpt against a character budget

"Not found in the index" means exactly that. The result carries the indexed
roots and counts so an empty result is never reported as absence.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterable

from . import constants as C
from .model import digest, digest_bytes, new_id, utc_now
from .store import Store

TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".rst", ".tex", ".py", ".json", ".yaml", ".yml", ".toml", ".cfg"}
NOTEBOOK_SUFFIX = ".ipynb"
PDF_SUFFIX = ".pdf"
MAX_FILE_BYTES = 8 * 1024 * 1024
CHUNK_CHARS = 2400
SNIPPET_CHARS = 200
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".obsidian", ".trash"}


class RetrievalError(RuntimeError):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------
def _markdown_sections(text: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    heading = "（开头）"
    buffer: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
        match = None if in_fence else HEADING_RE.match(line)
        if match:
            if "".join(buffer).strip():
                sections.append((heading, "\n".join(buffer).strip()))
            heading = match.group(2).strip()
            buffer = []
            continue
        buffer.append(line)
    if "".join(buffer).strip():
        sections.append((heading, "\n".join(buffer).strip()))
    return _split_long(sections)


def _split_long(sections: list[tuple[str, str]]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for heading, body in sections:
        if len(body) <= CHUNK_CHARS:
            out.append((heading, body))
            continue
        for index in range(0, len(body), CHUNK_CHARS):
            part = index // CHUNK_CHARS + 1
            out.append((f"{heading}（第{part}段）", body[index:index + CHUNK_CHARS]))
    return out


def _plain_sections(text: str, label: str) -> list[tuple[str, str]]:
    return _split_long([(label, text.strip())]) if text.strip() else []


def _notebook_sections(raw: bytes) -> list[tuple[str, str]]:
    try:
        notebook = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return []
    sections: list[tuple[str, str]] = []
    for index, cell in enumerate(notebook.get("cells", []), start=1):
        source = cell.get("source", "")
        text = "".join(source) if isinstance(source, list) else str(source)
        if text.strip():
            sections.append((f"cell {index} ({cell.get('cell_type', 'cell')})", text.strip()))
    return _split_long(sections)


def pdf_available() -> bool:
    return shutil.which("pdftotext") is not None


def _pdf_sections(path: Path) -> tuple[list[tuple[str, str]], str]:
    """One section per page. Returns the sections and an extraction status."""

    if not pdf_available():
        return [], "extractor_unavailable"
    try:
        completed = subprocess.run(
            ["pdftotext", "-layout", "-enc", "UTF-8", str(path), "-"],
            capture_output=True, timeout=120, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return [], "extraction_failed"
    if completed.returncode != 0:
        return [], "extraction_failed"
    text = completed.stdout.decode("utf-8", errors="replace")
    pages = text.split("\f")
    sections = [(f"p.{number}", page.strip()) for number, page in enumerate(pages, start=1) if page.strip()]
    if not sections:
        return [], "no_text_layer"
    return _split_long(sections), "ok"


def extract_sections(path: Path) -> tuple[list[tuple[str, str]], str]:
    suffix = path.suffix.casefold()
    if suffix == PDF_SUFFIX:
        return _pdf_sections(path)
    raw = path.read_bytes()
    if suffix == NOTEBOOK_SUFFIX:
        return _notebook_sections(raw), "ok"
    text = raw.decode("utf-8", errors="replace")
    if suffix in {".md", ".markdown"}:
        return _markdown_sections(text), "ok"
    return _plain_sections(text, path.name), "ok"


def indexable(path: Path) -> bool:
    suffix = path.suffix.casefold()
    return suffix in TEXT_SUFFIXES or suffix in {NOTEBOOK_SUFFIX, PDF_SUFFIX}


def iter_files(root: Path, *, limit: int = 20000) -> Iterable[Path]:
    count = 0
    for path in sorted(root.rglob("*")):
        if any(part in SKIP_DIRS or part.startswith(".") for part in path.relative_to(root).parts[:-1]):
            continue
        if not path.is_file() or path.name.startswith("."):
            continue
        if not indexable(path):
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        yield path
        count += 1
        if count >= limit:
            return


# --------------------------------------------------------------------------
# Index: plan, then apply with the plan hash
# --------------------------------------------------------------------------
def plan_index(root: str | Path, *, limit: int = 20000) -> dict[str, Any]:
    """A dry run. Lists what would be indexed; writes nothing."""

    root_path = Path(root).expanduser().resolve()
    if not root_path.is_dir():
        raise RetrievalError(C.ERR_ASSET_UNMANAGED, f"not a directory: {root_path}")
    entries = []
    for path in iter_files(root_path, limit=limit):
        stat = path.stat()
        entries.append({
            "relative_path": str(path.relative_to(root_path)),
            "bytes": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        })
    body = {"root": str(root_path), "entries": entries}
    return {
        "schema": "th-retrieval-plan/v1",
        "root": str(root_path),
        "files": len(entries),
        "pdf_files": sum(1 for item in entries if item["relative_path"].casefold().endswith(".pdf")),
        "pdf_extractor": "available" if pdf_available() else "unavailable",
        "entries": entries,
        "plan_sha256": digest(body),
        "dry_run": True,
    }


def apply_index(store: Store, plan: dict[str, Any], *, expect_plan_sha256: str) -> dict[str, Any]:
    """Index exactly the planned files. Requires the plan hash quoted back."""

    body = {"root": plan["root"], "entries": plan["entries"]}
    actual = digest(body)
    if actual != plan.get("plan_sha256"):
        raise RetrievalError(C.ERR_PLAN_STALE, "plan body does not match its own hash")
    if actual != expect_plan_sha256:
        raise RetrievalError(C.ERR_PLAN_HASH, "indexing requires the exact plan hash")
    root = Path(plan["root"])
    counts = {"files": 0, "sections": 0, "skipped": 0, "no_text_layer": 0, "extraction_failed": 0,
              "extractor_unavailable": 0, "changed_since_plan": 0}

    def mutate(connection: Any) -> dict[str, Any]:
        for entry in plan["entries"]:
            path = root / entry["relative_path"]
            try:
                stat = path.stat()
            except OSError:
                counts["skipped"] += 1
                continue
            if stat.st_size != entry["bytes"] or stat.st_mtime_ns != entry["mtime_ns"]:
                counts["changed_since_plan"] += 1
            raw = path.read_bytes()
            file_sha = digest_bytes(raw)
            sections, status = extract_sections(path)
            if status != "ok":
                counts[status] = counts.get(status, 0) + 1
            connection.execute(
                "DELETE FROM sections_fts WHERE section_id IN"
                " (SELECT section_id FROM sections WHERE root = ? AND relative_path = ?)",
                (str(root), entry["relative_path"]),
            )
            connection.execute(
                "DELETE FROM sections WHERE root = ? AND relative_path = ?", (str(root), entry["relative_path"])
            )
            for ordinal, (heading, text) in enumerate(sections):
                section_id = new_id("SEC")
                connection.execute(
                    "INSERT INTO sections(section_id, root, relative_path, anchor_id, heading, ordinal, text,"
                    " sha256, file_sha256, indexed_at) VALUES(?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)",
                    (section_id, str(root), entry["relative_path"], heading, ordinal, text,
                     digest(text), file_sha, utc_now()),
                )
                connection.execute(
                    "INSERT INTO sections_fts(section_id, heading, text) VALUES(?, ?, ?)",
                    (section_id, heading, f"{entry['relative_path']}\n{text}"),
                )
                counts["sections"] += 1
            counts["files"] += 1
        return counts

    store.observe(
        event_type="retrieval_indexed",
        payload={"plan_sha256": actual, "root": plan["root"], "files": len(plan["entries"])},
        mutate=mutate,
    )
    return {"schema": "th-retrieval-receipt/v1", "plan_sha256": actual, "root": plan["root"], **counts}


def index_status(store: Store) -> dict[str, Any]:
    roots = store.rows(
        "SELECT root, COUNT(DISTINCT relative_path) AS files, COUNT(*) AS sections FROM sections GROUP BY root"
    )
    return {"schema": "th-retrieval-status/v1", "roots": roots, "pdf_extractor": "available" if pdf_available() else "unavailable"}


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------
def _fts_query(query: str) -> str:
    """Trigram MATCH needs terms of three characters or more; quote each."""

    terms = [term for term in re.split(r"[\s，。、；：,.;:!?！？()（）\[\]{}\"'`]+", query) if term]
    usable = [term.replace('"', '""') for term in terms if len(term) >= 3]
    return " OR ".join(f'"{term}"' for term in usable)


def _short_terms(query: str) -> list[str]:
    terms = [term for term in re.split(r"[\s，。、；：,.;:!?！？()（）\[\]{}\"'`]+", query) if term]
    return [term for term in terms if 0 < len(term) < 3]


def _snippet(text: str, query: str) -> str:
    lowered = text.casefold()
    position = -1
    for term in sorted(re.split(r"\s+", query.casefold()), key=len, reverse=True):
        if term and (position := lowered.find(term)) >= 0:
            break
    start = max(0, position - 60) if position >= 0 else 0
    return " ".join(text[start:start + SNIPPET_CHARS].split())


def search_sections(store: Store, query: str, *, limit: int = C.MAX_CANDIDATES) -> dict[str, Any]:
    if not isinstance(query, str) or not query.strip():
        raise RetrievalError(C.ERR_INPUT_REJECTED, "empty query")
    match = _fts_query(query)
    rows: list[dict[str, Any]] = []
    total = 0
    if match:
        total = store.rows("SELECT COUNT(*) AS n FROM sections_fts WHERE sections_fts MATCH ?", (match,))[0]["n"]
        rows = store.rows(
            "SELECT s.section_id, s.relative_path, s.heading, s.text, s.root, bm25(sections_fts) AS score"
            " FROM sections_fts JOIN sections s ON s.section_id = sections_fts.section_id"
            " WHERE sections_fts MATCH ? ORDER BY score LIMIT ?",
            (match, limit),
        )
    else:
        short = _short_terms(query)
        if short:
            pattern = f"%{short[0]}%"
            total = store.rows("SELECT COUNT(*) AS n FROM sections WHERE text LIKE ? OR heading LIKE ?", (pattern, pattern))[0]["n"]
            rows = store.rows(
                "SELECT section_id, relative_path, heading, text, root, 0.0 AS score FROM sections"
                " WHERE text LIKE ? OR heading LIKE ? LIMIT ?",
                (pattern, pattern, limit),
            )
    indexed = store.rows("SELECT COUNT(DISTINCT root || '/' || relative_path) AS n FROM sections")[0]["n"]
    candidates = [
        {
            "section_id": row["section_id"],
            "path": row["relative_path"],
            "heading": row["heading"],
            "snippet": _snippet(row["text"], query),
        }
        for row in rows
    ]
    return {
        "candidates": candidates,
        "total_matches": int(total),
        "omitted": max(0, int(total) - len(candidates)),
        "indexed_files": int(indexed),
        "coverage_note": "only indexed text was searched; absence here is not absence from your files",
    }


def read_section(store: Store, section_id: str, *, max_chars: int = C.MAX_EXCERPT_CHARS) -> dict[str, Any]:
    """Re-read the section from the current file on disk."""

    rows = store.rows("SELECT * FROM sections WHERE section_id = ?", (section_id,))
    if not rows:
        raise RetrievalError(C.ERR_UNKNOWN_RECORD, section_id)
    row = rows[0]
    path = Path(row["root"]) / row["relative_path"]
    if not path.is_file():
        return {"state": "missing", "path": row["relative_path"], "heading": row["heading"], "text": ""}
    raw = path.read_bytes()
    state = "current" if digest_bytes(raw) == row["file_sha256"] else "changed_since_index"
    sections, _status = extract_sections(path)
    live = None
    if state == "current" and 0 <= row["ordinal"] < len(sections):
        live = sections[row["ordinal"]][1]
    else:
        for heading, text in sections:
            if heading == row["heading"]:
                live = text
                break
    if live is None:
        return {"state": "stale", "path": row["relative_path"], "heading": row["heading"], "text": ""}
    return {
        "state": state,
        "path": row["relative_path"],
        "heading": row["heading"],
        "text": live[:max_chars],
        "truncated": len(live) > max_chars,
    }


def search_library(store: Store, query: str, *, limit: int = C.MAX_CANDIDATES) -> dict[str, Any]:
    """Search what the project already holds: archived answers, claims, failures."""

    match = _fts_query(query)
    rows: list[dict[str, Any]] = []
    total = 0
    if match:
        total = store.rows("SELECT COUNT(*) AS n FROM library_fts WHERE library_fts MATCH ?", (match,))[0]["n"]
        rows = store.rows(
            "SELECT item_id, kind, text, bm25(library_fts) AS score FROM library_fts"
            " WHERE library_fts MATCH ? ORDER BY score LIMIT ?",
            (match, limit),
        )
    items = store.rows("SELECT COUNT(*) AS n FROM library_fts")[0]["n"]
    return {
        "candidates": [
            {"item_id": row["item_id"], "kind": row["kind"], "snippet": _snippet(row["text"], query)} for row in rows
        ],
        "total_matches": int(total),
        "omitted": max(0, int(total) - len(rows)),
        "library_items": int(items),
    }


def library_item(store: Store, item_id: str, *, max_chars: int = C.MAX_EXCERPT_CHARS) -> dict[str, Any]:
    rows = store.rows("SELECT item_id, kind, text FROM library_fts WHERE item_id = ?", (item_id,))
    if not rows:
        raise RetrievalError(C.ERR_UNKNOWN_RECORD, item_id)
    text = rows[0]["text"]
    return {"state": "current", "path": f"library:{item_id}", "heading": rows[0]["kind"],
            "text": text[:max_chars], "truncated": len(text) > max_chars}
