from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
from typing import Any, Iterable

from .text import compact_snippet, make_match_query, make_search_text


QUESTION_SCHEMA_VERSION = "mpk-questions/v1"
QUESTION_INDEX_PLAN_VERSION = "mpk-question-import-plan/v1"
_QUESTION_HEADING = re.compile(r"^###\s+(\d+(?:[.．]\d+)*)\s*$")
_SECTION_HEADING = re.compile(r"^##\s+(.+?)\s*$")
_TITLE_HEADING = re.compile(r"^#\s+(.+?)\s*$")
_IMAGE_REF = re.compile(r"!\[[^\]]*\]\(([^)]+)\)|!\[\[([^\]]+)\]\]")
_YEAR = re.compile(r"(?<!\d)(20\d{2})(?!\d)")
QUESTION_SUBJECT = "cs-ai"
_SUBJECT_MARKERS = ("计算机", "人工智能", "机器学习", "深度学习", "强化学习", "算法", "数据结构")
_SUBJECT_WORDS = re.compile(
    r"(?<![a-z0-9])(?:cs|ai|ml|nlp|algorithms?|machine learning|deep learning)(?![a-z0-9])",
    re.IGNORECASE,
)


def question_subject(title: str, paper: str) -> str | None:
    """Tag a Vault question document as CS/AI when its title or folder says so."""

    text = f"{title} {paper}"
    if any(marker in text for marker in _SUBJECT_MARKERS) or _SUBJECT_WORDS.search(text):
        return QUESTION_SUBJECT
    return None


SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS question_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    relative_path TEXT NOT NULL UNIQUE,
    indexed_at TEXT,
    scan_complete INTEGER NOT NULL DEFAULT 0,
    scan_errors_json TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS question_docs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES question_sources(id) ON DELETE CASCADE,
    relative_path TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    year TEXT,
    paper TEXT,
    subject TEXT,
    file_size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    question_count INTEGER NOT NULL DEFAULT 0,
    indexed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL REFERENCES question_docs(id) ON DELETE CASCADE,
    stable_key TEXT NOT NULL UNIQUE,
    question_number TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    section TEXT,
    content_md TEXT NOT NULL,
    image_refs_json TEXT NOT NULL DEFAULT '[]',
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS question_fts USING fts5(
    question_id UNINDEXED,
    search_text,
    title_text,
    metadata_text,
    tokenize='unicode61 remove_diacritics 2'
);
CREATE TABLE IF NOT EXISTS question_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_question_docs_source ON question_docs(source_id);
CREATE INDEX IF NOT EXISTS idx_questions_doc ON questions(doc_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _is_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return path.is_symlink() or bool(int(getattr(info, "st_file_attributes", 0) or 0) & flag)


def _has_reparse_component(root: Path, relative: Path) -> bool:
    current = root
    for part in relative.parts:
        if part in {"", "."}:
            continue
        current = current / part
        if current.exists() and _is_reparse(current):
            return True
    return False


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA_SQL)
    return con


def _read_only(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError("The question index does not exist; run question-index first")
    con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _stored_root_id(connection: sqlite3.Connection, table: str) -> str | None:
    if not _table_exists(connection, table):
        return None
    row = connection.execute(
        f"SELECT value FROM {table} WHERE key='knowledge_root_id'"
    ).fetchone()
    return None if row is None else str(row[0])


def assert_question_database_root(database: Path, knowledge_root_id: str) -> None:
    """Reject a cross-root write before opening a schema-creating connection."""
    if not database.is_file():
        return
    with closing(_read_only(database)) as connection:
        stored_root_ids = {
            value for value in (
                _stored_root_id(connection, "question_meta"),
                _stored_root_id(connection, "question_collection_meta"),
            ) if value is not None
        }
    if any(value != knowledge_root_id for value in stored_root_ids):
        raise RuntimeError("Question index belongs to a different knowledge root")


def _safe_source(vault_root: Path, relative: str) -> tuple[Path, str]:
    root = vault_root.resolve(strict=True)
    raw = Path(relative)
    if raw.is_absolute() or ".." in raw.parts:
        raise ValueError("Question source escapes the configured Vault")
    if _has_reparse_component(root, raw):
        raise ValueError("Question source must not contain a symlink or reparse point")
    candidate = (root / raw).resolve(strict=False)
    try:
        canonical = candidate.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError("Question source escapes the configured Vault") from exc
    if candidate == root:
        raise ValueError("Question source must be a bounded folder inside the Vault, not the entire Vault")
    if not candidate.is_dir() or _is_reparse(candidate):
        raise ValueError("Question source must be a real directory inside the configured Vault")
    return candidate, canonical


def _discover_markdown(source: Path) -> tuple[list[Path], list[str]]:
    found: list[Path] = []
    errors: list[str] = []

    def onerror(error: OSError) -> None:
        errors.append(str(error))

    for current, dirs, files in os.walk(source, topdown=True, followlinks=False, onerror=onerror):
        dirs[:] = sorted(
            name for name in dirs
            if not name.startswith(".") and not _is_reparse(Path(current) / name)
        )
        for name in sorted(files):
            path = Path(current) / name
            if name.casefold().endswith(".md") and not _is_reparse(path):
                found.append(path)
    return found, errors


def _parse_note(text: str, fallback_title: str) -> tuple[str, str | None, list[dict[str, Any]]]:
    lines = text.splitlines()
    title = fallback_title
    section: str | None = None
    starts: list[tuple[int, str, str | None]] = []
    for index, line in enumerate(lines):
        title_match = _TITLE_HEADING.match(line)
        if title_match and title == fallback_title:
            title = title_match.group(1).strip()
        section_match = _SECTION_HEADING.match(line)
        if section_match:
            section = section_match.group(1).strip()
        question_match = _QUESTION_HEADING.match(line)
        if question_match:
            starts.append((index, question_match.group(1), section))
    questions: list[dict[str, Any]] = []
    for ordinal, (start, number, current_section) in enumerate(starts, start=1):
        end = starts[ordinal][0] if ordinal < len(starts) else len(lines)
        content = "\n".join(lines[start:end]).strip()
        refs = [a or b for a, b in _IMAGE_REF.findall(content)]
        questions.append({
            "number": number,
            "ordinal": ordinal,
            "section": current_section,
            "content": content,
            "image_refs": refs,
            "start_line": start + 1,
            "end_line": end,
        })
    year_match = _YEAR.search(title) or _YEAR.search(fallback_title)
    return title, year_match.group(1) if year_match else None, questions


def plan_question_index(
    vault_root: Path,
    database: Path,
    *,
    source_relative: str,
    knowledge_root_id: str,
) -> dict[str, Any]:
    source, canonical_source = _safe_source(vault_root, source_relative)
    paths, scan_errors = _discover_markdown(source)
    documents: list[dict[str, Any]] = []
    question_count = 0
    source_digest = hashlib.sha256()
    for path in paths:
        raw = path.read_bytes()
        text = raw[3:].decode("utf-8") if raw.startswith(b"\xef\xbb\xbf") else raw.decode("utf-8")
        _, _, parsed = _parse_note(text, path.stem)
        if not parsed:
            continue
        relative = path.relative_to(vault_root).as_posix()
        digest = hashlib.sha256(raw).hexdigest()
        source_digest.update(relative.encode("utf-8"))
        source_digest.update(bytes.fromhex(digest))
        documents.append({
            "relative_path": relative,
            "sha256": digest,
            "question_count": len(parsed),
        })
        question_count += len(parsed)
    payload: dict[str, Any] = {
        "schema_version": QUESTION_INDEX_PLAN_VERSION,
        "ok": not scan_errors,
        "status": "import_preview" if not scan_errors else "coverage_gap",
        "applied": False,
        "source_scope": "vault",
        "source_relative": canonical_source,
        "source_format": "numbered-markdown/v1",
        "knowledge_root_id": knowledge_root_id,
        "source_sha256": source_digest.hexdigest(),
        "document_count": len(documents),
        "question_count": question_count,
        "verification_status": "source_scan_complete" if not scan_errors else "scan_incomplete",
        "scan_errors": scan_errors,
        "documents": documents,
        "database_exists": database.is_file(),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload["plan_sha256"] = hashlib.sha256(encoded).hexdigest()
    return payload


def guarded_index_questions(
    vault_root: Path,
    database: Path,
    *,
    source_relative: str,
    knowledge_root_id: str,
    write: bool = False,
    expect_plan_sha256: str | None = None,
) -> dict[str, Any]:
    plan = plan_question_index(
        vault_root,
        database,
        source_relative=source_relative,
        knowledge_root_id=knowledge_root_id,
    )
    if not write:
        return plan
    if not expect_plan_sha256 or expect_plan_sha256 != plan["plan_sha256"]:
        raise ValueError("--expect-plan-sha256 must match the current question-import preview")
    if not plan["ok"]:
        raise RuntimeError("Question source scan is incomplete; repair scan errors before import")
    result = index_questions(
        vault_root,
        database,
        source_relative=source_relative,
        knowledge_root_id=knowledge_root_id,
        expected_documents={
            str(item["relative_path"]): str(item["sha256"])
            for item in plan["documents"]
        },
        force_snapshot=True,
    )
    return {**plan, **result, "status": "imported", "applied": True}


def index_questions(
    vault_root: Path,
    database: Path,
    *,
    source_relative: str,
    knowledge_root_id: str,
    expected_documents: dict[str, str] | None = None,
    force_snapshot: bool = False,
) -> dict[str, Any]:
    source, canonical_source = _safe_source(vault_root, source_relative)
    paths, scan_errors = _discover_markdown(source)
    snapshots: list[dict[str, Any]] = []
    parsed_hashes: dict[str, str] = {}
    for path in paths:
        before = path.stat()
        raw = path.read_bytes()
        after = path.stat()
        if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
            raise RuntimeError(f"Question source changed while reading {path.name}; preview again")
        text = raw[3:].decode("utf-8") if raw.startswith(b"\xef\xbb\xbf") else raw.decode("utf-8")
        title, year, parsed = _parse_note(text, path.stem)
        relative = path.relative_to(vault_root).as_posix()
        digest = hashlib.sha256(raw).hexdigest()
        snapshots.append({
            "path": path,
            "relative": relative,
            "raw": raw,
            "title": title,
            "year": year,
            "parsed": parsed,
            "mtime_ns": int(after.st_mtime_ns),
        })
        if parsed:
            parsed_hashes[relative] = digest
    if expected_documents is not None and parsed_hashes != expected_documents:
        raise RuntimeError("Question source bytes changed after preview; preview again before import")
    assert_question_database_root(database, knowledge_root_id)
    now = _now()
    indexed_docs = 0
    unchanged_docs = 0
    indexed_questions = 0
    seen: set[str] = set()
    with closing(_connect(database)) as con, con:
        stored_root_ids = {
            value for value in (
                _stored_root_id(con, "question_meta"),
                _stored_root_id(con, "question_collection_meta"),
            ) if value is not None
        }
        if any(value != knowledge_root_id for value in stored_root_ids):
            raise RuntimeError("Question index belongs to a different knowledge root")
        con.execute(
            "INSERT INTO question_sources(relative_path) VALUES (?) ON CONFLICT(relative_path) DO NOTHING",
            (canonical_source,),
        )
        source_id = int(con.execute(
            "SELECT id FROM question_sources WHERE relative_path = ?", (canonical_source,)
        ).fetchone()[0])
        for snapshot in snapshots:
            path = snapshot["path"]
            relative = str(snapshot["relative"])
            seen.add(relative)
            raw = snapshot["raw"]
            title = str(snapshot["title"])
            year = snapshot["year"]
            parsed = snapshot["parsed"]
            if not parsed:
                continue
            existing = con.execute(
                "SELECT id, file_size, mtime_ns, question_count FROM question_docs WHERE relative_path = ?",
                (relative,),
            ).fetchone()
            if (
                not force_snapshot
                and existing
                and int(existing["file_size"]) == len(raw)
                and int(existing["mtime_ns"]) == int(snapshot["mtime_ns"])
            ):
                unchanged_docs += 1
                indexed_questions += int(existing["question_count"])
                continue
            paper = path.parent.name
            subject = question_subject(title, paper)
            if existing:
                doc_id = int(existing["id"])
                con.execute("DELETE FROM question_fts WHERE question_id IN (SELECT id FROM questions WHERE doc_id = ?)", (doc_id,))
                con.execute("DELETE FROM questions WHERE doc_id = ?", (doc_id,))
                con.execute(
                    "UPDATE question_docs SET source_id=?, title=?, year=?, paper=?, subject=?, file_size=?, mtime_ns=?, question_count=?, indexed_at=? WHERE id=?",
                    (source_id, title, year, paper, subject, len(raw), int(snapshot["mtime_ns"]), len(parsed), now, doc_id),
                )
            else:
                cursor = con.execute(
                    "INSERT INTO question_docs(source_id,relative_path,title,year,paper,subject,file_size,mtime_ns,question_count,indexed_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (source_id, relative, title, year, paper, subject, len(raw), int(snapshot["mtime_ns"]), len(parsed), now),
                )
                doc_id = int(cursor.lastrowid)
            for item in parsed:
                stable_key = f"vault:{relative}#q{item['number']}"
                cursor = con.execute(
                    "INSERT INTO questions(doc_id,stable_key,question_number,ordinal,section,content_md,image_refs_json,start_line,end_line) VALUES (?,?,?,?,?,?,?,?,?)",
                    (doc_id, stable_key, item["number"], item["ordinal"], item["section"], item["content"], json.dumps(item["image_refs"], ensure_ascii=False), item["start_line"], item["end_line"]),
                )
                qid = int(cursor.lastrowid)
                metadata = " ".join(filter(None, (year, paper, subject, item["section"], item["number"])))
                con.execute(
                    "INSERT INTO question_fts(question_id,search_text,title_text,metadata_text) VALUES (?,?,?,?)",
                    (qid, make_search_text(item["content"], title, metadata), make_search_text(title), make_search_text(metadata)),
                )
            indexed_docs += 1
            indexed_questions += len(parsed)
        if not scan_errors:
            stale = con.execute(
                "SELECT id, relative_path FROM question_docs WHERE source_id = ?", (source_id,)
            ).fetchall()
            for row in stale:
                if str(row["relative_path"]) not in seen:
                    con.execute("DELETE FROM question_fts WHERE question_id IN (SELECT id FROM questions WHERE doc_id = ?)", (int(row["id"]),))
                    con.execute("DELETE FROM question_docs WHERE id = ?", (int(row["id"]),))
        con.execute(
            "UPDATE question_sources SET indexed_at=?, scan_complete=?, scan_errors_json=? WHERE id=?",
            (now, int(not scan_errors), json.dumps(scan_errors, ensure_ascii=False), source_id),
        )
        for key, value in (("schema_version", QUESTION_SCHEMA_VERSION), ("knowledge_root_id", knowledge_root_id)):
            con.execute("INSERT INTO question_meta(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    return {
        "ok": not scan_errors,
        "status": "indexed" if not scan_errors else "coverage_gap",
        "database": str(database),
        "source_relative": canonical_source,
        "indexed_docs": indexed_docs,
        "unchanged_docs": unchanged_docs,
        "questions_accounted": indexed_questions,
        "scan_errors": scan_errors,
        "source_files_discovered": len(paths),
    }


def question_status(database: Path, *, knowledge_root_id: str | None = None) -> dict[str, Any]:
    if not database.is_file():
        return {"ok": True, "status": "not_initialized", "database_exists": False, "schema_initialized": False, "root_matches_config": True, "coverage_complete": False, "docs": 0, "questions": 0, "sources": []}
    with closing(_read_only(database)) as con:
        required = {"question_meta", "question_sources", "question_docs", "questions"}
        if not all(_table_exists(con, table) for table in required):
            return {
                "ok": True,
                "status": "not_initialized",
                "database_exists": True,
                "schema_initialized": False,
                "schema_version": None,
                "root_matches_config": True,
                "coverage_complete": False,
                "docs": 0,
                "questions": 0,
                "sources": [],
            }
        meta = {str(row[0]): str(row[1]) for row in con.execute("SELECT key,value FROM question_meta")}
        sources = [dict(row) for row in con.execute("SELECT relative_path,indexed_at,scan_complete,scan_errors_json FROM question_sources ORDER BY relative_path")]
        for source in sources:
            source["scan_complete"] = bool(source["scan_complete"])
            source["scan_errors"] = json.loads(str(source.pop("scan_errors_json")))
        docs = int(con.execute("SELECT COUNT(*) FROM question_docs").fetchone()[0])
        questions = int(con.execute("SELECT COUNT(*) FROM questions").fetchone()[0])
    root_match = knowledge_root_id is None or meta.get("knowledge_root_id") == knowledge_root_id
    complete = bool(sources) and all(bool(source["scan_complete"]) for source in sources)
    return {
        "ok": root_match,
        "status": "ready" if root_match and complete else "coverage_gap",
        "database_exists": True,
        "schema_initialized": True,
        "schema_version": meta.get("schema_version"),
        "root_matches_config": root_match,
        "docs": docs,
        "questions": questions,
        "sources": sources,
        "coverage_complete": complete,
    }


def search_questions(
    database: Path,
    query: str,
    *,
    aliases: Iterable[str] = (),
    limit: int = 8,
    year: str | None = None,
    paper: str | None = None,
    knowledge_root_id: str | None = None,
) -> dict[str, Any]:
    if limit < 1 or limit > 50:
        raise ValueError("limit must be between 1 and 50")
    coverage = question_status(database, knowledge_root_id=knowledge_root_id)
    if not coverage.get("database_exists") or coverage.get("schema_initialized") is not True:
        return {"ok": True, "status": "coverage_gap", "results": [], "coverage": coverage, "fallback_recommended": "vault_or_pdf", "stop_reason": "question_index_missing"}
    if coverage.get("root_matches_config") is not True:
        raise RuntimeError("Question index belongs to a different knowledge root; rebuild it before searching")
    terms = [query, *[value for value in aliases if value]]
    matches: dict[int, tuple[float, sqlite3.Row]] = {}
    with closing(_read_only(database)) as con:
        for term in terms:
            match_query = make_match_query(term)
            clauses = ["question_fts MATCH ?"]
            params: list[Any] = [match_query]
            if year:
                clauses.append("d.year = ?")
                params.append(year)
            if paper:
                clauses.append("d.paper LIKE ?")
                params.append(f"%{paper}%")
            params.append(limit * 3)
            rows = con.execute(
                f"""SELECT q.id,q.stable_key,q.question_number,q.section,q.content_md,q.image_refs_json,q.start_line,q.end_line,d.relative_path,d.title,d.year,d.paper,d.subject,bm25(question_fts,8.0,3.0,4.0) AS score FROM question_fts JOIN questions q ON q.id=question_fts.question_id JOIN question_docs d ON d.id=q.doc_id WHERE {' AND '.join(clauses)} ORDER BY score LIMIT ?""",
                params,
            ).fetchall()
            for row in rows:
                qid = int(row["id"])
                score = float(row["score"])
                if qid not in matches or score < matches[qid][0]:
                    matches[qid] = (score, row)
    ordered = sorted(matches.values(), key=lambda item: item[0])[:limit]
    results = []
    for score, row in ordered:
        results.append({
            "stable_key": row["stable_key"],
            "title": row["title"],
            "year": row["year"],
            "paper": row["paper"],
            "subject": row["subject"],
            "question_number": row["question_number"],
            "section": row["section"],
            "source_relative_path": row["relative_path"],
            "start_line": row["start_line"],
            "end_line": row["end_line"],
            "content_md": row["content_md"],
            "snippet": compact_snippet(str(row["content_md"]), 420),
            "image_refs": json.loads(str(row["image_refs_json"])),
            "score": score,
        })
    complete = coverage.get("coverage_complete") is True
    status = "candidate_hit" if results else ("not_found_in_indexed_questions" if complete else "coverage_gap")
    return {
        "ok": True,
        "status": status,
        "query": query,
        "aliases": list(aliases),
        "filters": {"year": year, "paper": paper},
        "results": results,
        "coverage": coverage,
        "stop_reason": "indexed_candidates" if results else "no_indexed_match",
        "fallback_recommended": "none" if results else "vault_or_pdf",
    }
