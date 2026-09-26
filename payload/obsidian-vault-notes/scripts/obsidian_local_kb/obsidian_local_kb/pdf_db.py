from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .pdf_extract import PdfDocData, normalize_pdf_key, pdf_key_from_relative_path
from .pdf_survey import build_pdf_doc_survey, encode_json
from .util import build_match_query, make_search_blob, normalize_text


PDF_SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS pdf_docs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    path_key TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    title_norm TEXT NOT NULL,
    page_count INTEGER NOT NULL,
    extracted_page_count INTEGER NOT NULL,
    extraction_warning TEXT
);

CREATE TABLE IF NOT EXISTS pdf_pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL REFERENCES pdf_docs(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL,
    content TEXT NOT NULL,
    snippet TEXT NOT NULL,
    char_count INTEGER NOT NULL,
    UNIQUE(doc_id, page_number)
);

CREATE VIRTUAL TABLE IF NOT EXISTS pdf_page_fts USING fts5(
    page_id UNINDEXED,
    search_text,
    title_text,
    path_text,
    page_text,
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE INDEX IF NOT EXISTS idx_pdf_pages_doc_id ON pdf_pages(doc_id);
CREATE INDEX IF NOT EXISTS idx_pdf_pages_page_number ON pdf_pages(page_number);
"""

PDF_SURVEY_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pdf_doc_surveys (
    doc_id INTEGER PRIMARY KEY REFERENCES pdf_docs(id) ON DELETE CASCADE,
    doc_type TEXT NOT NULL,
    subject_tags_json TEXT NOT NULL,
    summary TEXT NOT NULL,
    summary_norm TEXT NOT NULL,
    search_text TEXT NOT NULL,
    section_count INTEGER NOT NULL,
    confidence REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS pdf_sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL REFERENCES pdf_docs(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    title_norm TEXT NOT NULL,
    level TEXT NOT NULL,
    page_start INTEGER NOT NULL,
    page_end INTEGER NOT NULL,
    heading_path TEXT NOT NULL,
    preview TEXT NOT NULL,
    keywords_json TEXT NOT NULL,
    search_text TEXT NOT NULL,
    UNIQUE(doc_id, title_norm, page_start, page_end)
);

CREATE VIRTUAL TABLE IF NOT EXISTS pdf_doc_survey_fts USING fts5(
    doc_id UNINDEXED,
    search_text,
    title_text,
    summary_text,
    subject_text,
    doc_type_text,
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE VIRTUAL TABLE IF NOT EXISTS pdf_section_fts USING fts5(
    section_id UNINDEXED,
    search_text,
    title_text,
    heading_text,
    preview_text,
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE INDEX IF NOT EXISTS idx_pdf_sections_doc_id ON pdf_sections(doc_id);
CREATE INDEX IF NOT EXISTS idx_pdf_sections_page_start ON pdf_sections(doc_id, page_start);
CREATE INDEX IF NOT EXISTS idx_pdf_sections_page_end ON pdf_sections(doc_id, page_end);
"""


@dataclass(slots=True)
class PdfQueryResult:
    score: float
    doc_id: int
    title: str
    path: str
    page_number: int
    snippet: str
    reasons: list[str]


@dataclass(slots=True)
class PdfSurveyDocResult:
    score: float
    doc_id: int
    title: str
    path: str
    doc_type: str
    subject_tags: list[str]
    summary: str
    reasons: list[str]


@dataclass(slots=True)
class PdfSurveySectionResult:
    score: float
    doc_id: int
    title: str
    path: str
    doc_type: str
    subject_tags: list[str]
    summary: str
    section_title: str
    heading_path: str
    page_start: int
    page_end: int
    preview: str
    reasons: list[str]


def connect_pdf(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.executescript(PDF_SCHEMA_SQL)
    con.executescript(PDF_SURVEY_SCHEMA_SQL)
    return con


def _table_exists(con: sqlite3.Connection, table_name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _clear_pdf_doc_survey(con: sqlite3.Connection, doc_id: int) -> None:
    if _table_exists(con, "pdf_doc_survey_fts"):
        con.execute("DELETE FROM pdf_doc_survey_fts WHERE doc_id = ?", (doc_id,))
    if _table_exists(con, "pdf_section_fts"):
        con.execute(
            """
            DELETE FROM pdf_section_fts
            WHERE section_id IN (SELECT id FROM pdf_sections WHERE doc_id = ?)
            """,
            (doc_id,),
        )
    if _table_exists(con, "pdf_sections"):
        con.execute("DELETE FROM pdf_sections WHERE doc_id = ?", (doc_id,))
    if _table_exists(con, "pdf_doc_surveys"):
        con.execute("DELETE FROM pdf_doc_surveys WHERE doc_id = ?", (doc_id,))


def _store_pdf_doc_survey(
    con: sqlite3.Connection,
    doc_id: int,
    *,
    title: str,
    path: str,
    pages: list[tuple[int, str]],
) -> None:
    survey = build_pdf_doc_survey(title=title, path=path, pages=pages)
    _clear_pdf_doc_survey(con, doc_id)
    con.execute(
        """
        INSERT INTO pdf_doc_surveys(
            doc_id,
            doc_type,
            subject_tags_json,
            summary,
            summary_norm,
            search_text,
            section_count,
            confidence
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            doc_id,
            survey.doc_type,
            encode_json(survey.subject_tags),
            survey.summary,
            survey.summary_norm,
            survey.search_text,
            len(survey.sections),
            survey.confidence,
        ),
    )
    con.execute(
        """
        INSERT INTO pdf_doc_survey_fts(doc_id, search_text, title_text, summary_text, subject_text, doc_type_text)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            doc_id,
            survey.search_text,
            make_search_blob(title),
            make_search_blob(survey.summary),
            make_search_blob(" ".join(tag.replace("_", " ") for tag in survey.subject_tags)),
            make_search_blob(survey.doc_type.replace("_", " ")),
        ),
    )
    for section in survey.sections:
        cur = con.execute(
            """
            INSERT INTO pdf_sections(
                doc_id,
                title,
                title_norm,
                level,
                page_start,
                page_end,
                heading_path,
                preview,
                keywords_json,
                search_text
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                doc_id,
                section.title,
                section.title_norm,
                section.level,
                section.page_start,
                section.page_end,
                section.heading_path,
                section.preview,
                encode_json(section.keywords),
                section.search_text,
            ),
        )
        section_id = int(cur.lastrowid)
        con.execute(
            """
            INSERT INTO pdf_section_fts(section_id, search_text, title_text, heading_text, preview_text)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                section_id,
                section.search_text,
                make_search_blob(section.title),
                make_search_blob(section.heading_path),
                make_search_blob(section.preview),
            ),
        )


def rebuild_pdf_index(db_path: Path, docs: Iterable[PdfDocData]) -> dict[str, int]:
    if db_path.exists():
        db_path.unlink()

    con = connect_pdf(db_path)
    stats = {
        "docs": 0,
        "pages": 0,
        "pages_with_text": 0,
    }

    with con:
        for doc in docs:
            stats["docs"] += 1
            stats["pages"] += doc.page_count
            stats["pages_with_text"] += doc.extracted_page_count

            cur = con.execute(
                """
                INSERT INTO pdf_docs(path, path_key, title, title_norm, page_count, extracted_page_count, extraction_warning)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    doc.path,
                    pdf_key_from_relative_path(doc.path),
                    doc.title,
                    doc.title_norm,
                    doc.page_count,
                    doc.extracted_page_count,
                    doc.extraction_warning,
                ),
            )
            doc_id = int(cur.lastrowid)

            for page in doc.pages:
                cur = con.execute(
                    """
                    INSERT INTO pdf_pages(doc_id, page_number, content, snippet, char_count)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        doc_id,
                        page.page_number,
                        page.content,
                        page.snippet,
                        page.char_count,
                    ),
                )
                page_id = int(cur.lastrowid)
                con.execute(
                    """
                    INSERT INTO pdf_page_fts(page_id, search_text, title_text, path_text, page_text)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        page_id,
                        make_search_blob(doc.title, doc.path, page.content),
                        make_search_blob(doc.title),
                        make_search_blob(doc.path),
                        make_search_blob(page.content),
                    ),
                )
            _store_pdf_doc_survey(
                con,
                doc_id,
                title=doc.title,
                path=doc.path,
                pages=[(page.page_number, page.content) for page in doc.pages],
            )

    con.close()
    return stats


def rebuild_pdf_surveys(
    con: sqlite3.Connection,
    *,
    doc_specs: Iterable[str] | None = None,
) -> dict[str, int]:
    con.executescript(PDF_SURVEY_SCHEMA_SQL)
    stats = {"docs": 0, "sections": 0}
    if doc_specs is None:
        doc_rows = con.execute(
            "SELECT id, title, path FROM pdf_docs ORDER BY id"
        ).fetchall()
    else:
        doc_rows = [resolve_pdf_doc_spec(con, spec) for spec in doc_specs]

    with con:
        for doc_row in doc_rows:
            doc_id = int(doc_row["id"])
            page_rows = con.execute(
                """
                SELECT page_number, content
                FROM pdf_pages
                WHERE doc_id = ?
                ORDER BY page_number
                """,
                (doc_id,),
            ).fetchall()
            _store_pdf_doc_survey(
                con,
                doc_id,
                title=str(doc_row["title"]),
                path=str(doc_row["path"]),
                pages=[(int(row["page_number"]), str(row["content"])) for row in page_rows],
            )
            section_count = con.execute(
                "SELECT COUNT(*) FROM pdf_sections WHERE doc_id = ?",
                (doc_id,),
            ).fetchone()[0]
            stats["docs"] += 1
            stats["sections"] += int(section_count)
    return stats


def pdf_has_survey_metadata(con: sqlite3.Connection) -> bool:
    if not _table_exists(con, "pdf_doc_surveys") or not _table_exists(con, "pdf_sections"):
        return False
    row = con.execute("SELECT COUNT(*) AS count FROM pdf_doc_surveys").fetchone()
    return row is not None and int(row["count"]) > 0


def resolve_pdf_doc_spec(con: sqlite3.Connection, spec: str) -> sqlite3.Row:
    norm = normalize_pdf_key(spec)
    rows = con.execute(
        """
        SELECT *
        FROM pdf_docs
        WHERE path = ?
           OR path_key = ?
           OR title_norm = ?
        ORDER BY CASE
            WHEN path = ? THEN 0
            WHEN path_key = ? THEN 1
            ELSE 2
        END
        """,
        (spec, norm, norm, spec, norm),
    ).fetchall()
    if not rows:
        raise ValueError(f"Could not resolve PDF spec: {spec}")
    if len(rows) > 1:
        titles = ", ".join(row["path"] for row in rows[:5])
        raise ValueError(f"Ambiguous PDF spec: {spec} -> {titles}")
    return rows[0]


def query_pdf_pages(
    con: sqlite3.Connection,
    query: str,
    limit: int = 8,
) -> list[PdfQueryResult]:
    match_query = build_match_query(query)
    rows = con.execute(
        """
        SELECT
            p.id AS page_id,
            d.id AS doc_id,
            d.title,
            d.path,
            p.page_number,
            p.snippet,
            bm25(pdf_page_fts, 3.0, 2.5, 0.5, 1.0) AS bm25_score
        FROM pdf_page_fts
        JOIN pdf_pages p ON p.id = CAST(pdf_page_fts.page_id AS INTEGER)
        JOIN pdf_docs d ON d.id = p.doc_id
        WHERE pdf_page_fts MATCH ?
        ORDER BY bm25_score
        LIMIT 120
        """,
        (match_query,),
    ).fetchall()

    query_norm = normalize_text(query)
    results: list[PdfQueryResult] = []
    seen_pages: set[int] = set()

    for row in rows:
        page_id = int(row["page_id"])
        if page_id in seen_pages:
            continue
        seen_pages.add(page_id)

        bm25_score = abs(float(row["bm25_score"]))
        score = (1.0 / (1.0 + bm25_score)) * 100.0
        reasons = [f"fts bm25={bm25_score:.3f}"]

        title_norm = normalize_pdf_key(row["title"])
        path_norm = normalize_pdf_key(row["path"])
        if query_norm and query_norm == title_norm:
            score += 35.0
            reasons.append("exact title match")
        elif query_norm and query_norm in title_norm:
            score += 18.0
            reasons.append("title contains query")

        if query_norm and query_norm in path_norm:
            score += 8.0
            reasons.append("path contains query")

        results.append(
            PdfQueryResult(
                score=score,
                doc_id=int(row["doc_id"]),
                title=row["title"],
                path=row["path"],
                page_number=int(row["page_number"]),
                snippet=row["snippet"],
                reasons=reasons,
            )
        )

    results.sort(key=lambda item: item.score, reverse=True)
    return results[:limit]


def query_pdf_doc_surveys(
    con: sqlite3.Connection,
    query: str,
    limit: int = 8,
) -> list[PdfSurveyDocResult]:
    if not pdf_has_survey_metadata(con):
        return []
    match_query = build_match_query(query)
    rows = con.execute(
        """
        SELECT
            d.id AS doc_id,
            d.title,
            d.path,
            ds.doc_type,
            ds.subject_tags_json,
            ds.summary,
            bm25(pdf_doc_survey_fts, 3.2, 2.4, 1.4, 1.0, 0.8) AS bm25_score
        FROM pdf_doc_survey_fts
        JOIN pdf_doc_surveys ds ON ds.doc_id = CAST(pdf_doc_survey_fts.doc_id AS INTEGER)
        JOIN pdf_docs d ON d.id = ds.doc_id
        WHERE pdf_doc_survey_fts MATCH ?
        ORDER BY bm25_score
        LIMIT ?
        """,
        (match_query, limit),
    ).fetchall()
    results: list[PdfSurveyDocResult] = []
    query_norm = normalize_text(query)
    for row in rows:
        bm25_score = abs(float(row["bm25_score"]))
        score = (100.0 / (1.0 + bm25_score))
        reasons = [f"doc survey bm25={bm25_score:.3f}"]
        summary_norm = normalize_text(str(row["summary"]))
        if query_norm and query_norm in summary_norm:
            score += 12.0
            reasons.append("summary contains query")
        results.append(
            PdfSurveyDocResult(
                score=score,
                doc_id=int(row["doc_id"]),
                title=str(row["title"]),
                path=str(row["path"]),
                doc_type=str(row["doc_type"]),
                subject_tags=json.loads(row["subject_tags_json"] or "[]"),
                summary=str(row["summary"]),
                reasons=reasons,
            )
        )
    return results


def query_pdf_sections(
    con: sqlite3.Connection,
    query: str,
    limit: int = 12,
) -> list[PdfSurveySectionResult]:
    if not pdf_has_survey_metadata(con):
        return []
    match_query = build_match_query(query)
    rows = con.execute(
        """
        SELECT
            d.id AS doc_id,
            d.title,
            d.path,
            ds.doc_type,
            ds.subject_tags_json,
            ds.summary,
            s.title AS section_title,
            s.heading_path,
            s.page_start,
            s.page_end,
            s.preview,
            bm25(pdf_section_fts, 3.8, 2.4, 1.6, 1.0) AS bm25_score
        FROM pdf_section_fts
        JOIN pdf_sections s ON s.id = CAST(pdf_section_fts.section_id AS INTEGER)
        JOIN pdf_docs d ON d.id = s.doc_id
        JOIN pdf_doc_surveys ds ON ds.doc_id = d.id
        WHERE pdf_section_fts MATCH ?
        ORDER BY bm25_score
        LIMIT ?
        """,
        (match_query, limit),
    ).fetchall()
    results: list[PdfSurveySectionResult] = []
    query_norm = normalize_text(query)
    for row in rows:
        bm25_score = abs(float(row["bm25_score"]))
        score = (100.0 / (1.0 + bm25_score))
        reasons = [f"section survey bm25={bm25_score:.3f}"]
        section_norm = normalize_text(str(row["section_title"]))
        if query_norm and query_norm in section_norm:
            score += 14.0
            reasons.append("section title contains query")
        results.append(
            PdfSurveySectionResult(
                score=score,
                doc_id=int(row["doc_id"]),
                title=str(row["title"]),
                path=str(row["path"]),
                doc_type=str(row["doc_type"]),
                subject_tags=json.loads(row["subject_tags_json"] or "[]"),
                summary=str(row["summary"]),
                section_title=str(row["section_title"]),
                heading_path=str(row["heading_path"]),
                page_start=int(row["page_start"]),
                page_end=int(row["page_end"]),
                preview=str(row["preview"]),
                reasons=reasons,
            )
        )
    return results


def pdf_doc_summary(con: sqlite3.Connection, doc_id: int) -> sqlite3.Row:
    row = con.execute(
        """
        SELECT id, title, path, page_count, extracted_page_count, extraction_warning
        FROM pdf_docs
        WHERE id = ?
        """,
        (doc_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Could not find PDF doc id: {doc_id}")
    return row


def pdf_doc_survey(con: sqlite3.Connection, doc_id: int) -> sqlite3.Row | None:
    if not pdf_has_survey_metadata(con):
        return None
    return con.execute(
        """
        SELECT doc_type, subject_tags_json, summary, section_count, confidence
        FROM pdf_doc_surveys
        WHERE doc_id = ?
        """,
        (doc_id,),
    ).fetchone()


def pdf_doc_sections(con: sqlite3.Connection, doc_id: int) -> list[sqlite3.Row]:
    if not pdf_has_survey_metadata(con):
        return []
    return con.execute(
        """
        SELECT title, level, page_start, page_end, heading_path, preview, keywords_json
        FROM pdf_sections
        WHERE doc_id = ?
        ORDER BY page_start, id
        """,
        (doc_id,),
    ).fetchall()


def pdf_page_content(con: sqlite3.Connection, doc_id: int, page_number: int) -> sqlite3.Row | None:
    return con.execute(
        """
        SELECT page_number, snippet, content, char_count
        FROM pdf_pages
        WHERE doc_id = ? AND page_number = ?
        """,
        (doc_id, page_number),
    ).fetchone()


def query_pdf_results_to_json(results: list[PdfQueryResult]) -> str:
    payload = [
        {
            "score": round(item.score, 3),
            "title": item.title,
            "path": item.path,
            "page_number": item.page_number,
            "snippet": item.snippet,
            "reasons": item.reasons,
        }
        for item in results
    ]
    return json.dumps(payload, ensure_ascii=False, indent=2)
