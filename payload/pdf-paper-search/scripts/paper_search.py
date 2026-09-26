from __future__ import annotations

"""PDF paper search reranker.

Improvement rule for future edits:
- add regression cases first
- diagnose the failing layer
- prefer reusable query/page features and scorer terms
- avoid benchmark-specific title/path rules unless no reusable abstraction works
"""

import argparse
from pdf_paper_search.public_output import SafeArgumentParser, parse_public_args, guarded_main
import json
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from pdf_paper_search.diagnostics import (
    compact_exception,
    diagnostics_to_dicts,
    get_diagnostics,
    record_diagnostic,
    reset_diagnostics,
    summarize_diagnostics,
)
from pdf_paper_search.paths import (
    discover_sqlite_dbs,
    ensure_project_on_path,
    resolve_data_root as shared_resolve_data_root,
    resolve_project_root as shared_resolve_project_root,
)
from pdf_paper_search.features import build_page_evidence
from pdf_paper_search.aliases import (
    CONCEPT_LABELS,
    HARD_CONCEPT_PATTERNS,
    SOFT_CONCEPT_PATTERNS,
    concept_positions,
    detect_hard_concepts,
    structural_cue_terms,
    term_variants,
)
from pdf_paper_search.normalization import (
    BENCHMARK_DATASETS,
    MODEL_FAMILIES,
    matching_exact_anchors,
    normalize_paper_query as canonicalize_paper_query,
)
from pdf_paper_search.page_roles import (
    ALGORITHM_ROLES,
    FIGURE_ROLES,
    LOCAL_WINDOW_ROLES,
    RESULT_ROLES,
    STATEMENT_ROLES,
    THEOREM_ROLES,
)
from pdf_paper_search.query_spec import QuerySpec, build_query_spec
from pdf_paper_search.scorer import score_breakdown as build_score_breakdown
from pdf_paper_search.types import PageEvidence, SearchDiagnostic


SCRIPT_PATH = Path(__file__).resolve()
DEFAULT_PROJECT_ROOT = shared_resolve_project_root(SCRIPT_PATH)
DEFAULT_DATA_ROOT = DEFAULT_PROJECT_ROOT / "data"  # No corpus discovery during import.
ensure_project_on_path(DEFAULT_PROJECT_ROOT)

from obsidian_local_kb.pdf_db import query_pdf_doc_surveys, query_pdf_sections  # noqa: E402
from obsidian_local_kb.util import build_match_query, normalize_text  # noqa: E402


GENERIC_WORDS = {
    "all",
    "and",
    "any",
    "approach",
    "are",
    "based",
    "chapter",
    "each",
    "exists",
    "for",
    "following",
    "from",
    "have",
    "here",
    "into",
    "method",
    "model",
    "our",
    "page",
    "paper",
    "propose",
    "proposed",
    "result",
    "results",
    "sect",
    "section",
    "see",
    "show",
    "some",
    "that",
    "the",
    "then",
    "there",
    "this",
    "use",
    "used",
    "using",
    "we",
    "where",
    "which",
    "with",
}

NOISY_HEADINGS = (
    "contents",
    "table of contents",
    "index",
    "subject index",
    "author index",
    "references",
    "bibliography",
)
NOISY_PAGE_TERMS = (
    "table of contents",
    "course announcement",
)

STATEMENT_LABELS = (
    "theorem",
    "lemma",
    "proposition",
    "corollary",
    "definition",
    "algorithm",
    "table",
    "figure",
    "equation",
)
LABEL_ROLE = {
    "theorem": "theorem",
    "lemma": "lemma",
    "proposition": "proposition",
    "corollary": "corollary",
    "definition": "definition",
    "algorithm": "algorithm",
    "table": "results_table",
    "figure": "figure_caption",
    "equation": "method",
}
LABEL_ALIASES = {
    "thm": "theorem",
    "lem": "lemma",
    "prop": "proposition",
    "cor": "corollary",
    "def": "definition",
    "alg": "algorithm",
    "tab": "table",
    "fig": "figure",
    "eq": "equation",
}
ROLE_LABELS = {
    "theorem": ("theorem",),
    "lemma": ("lemma",),
    "proposition": ("proposition",),
    "corollary": ("corollary",),
    "definition": ("definition",),
    "algorithm": ("algorithm",),
    "results_table": ("table",),
    "figure_caption": ("figure",),
    "method": ("equation",),
}
QUERY_TYPE_LABELS = {
    "algorithm_lookup": ("algorithm",),
    "result_table_lookup": ("table",),
    "figure_lookup": ("figure",),
    "definition_lookup": ("definition",),
}
ROLE_DESCRIPTIONS = {
    "theorem": "labeled theorem statement",
    "lemma": "labeled lemma statement",
    "proposition": "labeled proposition statement",
    "corollary": "labeled corollary statement",
    "definition": "definition box",
    "algorithm": "algorithm box/pseudocode",
    "results_table": "results table caption",
    "figure_caption": "figure caption",
    "method": "method/objective description",
    "abstract": "abstract/introduction summary",
    "body": "body text",
}

WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
NUMBER_PATTERN = r"(?P<number>(?:\d\s*){1,3}(?:\.\s*(?:\d\s*){1,3})*)"
LABEL_PATTERN = (
    r"(?P<label>theorem|thm|lemma|lem|proposition|prop|corollary|cor|definition|def|"
    r"algorithm|alg|table|tab|figure|fig|equation|eq)"
)
STATEMENT_REF_RE = re.compile(rf"\b{LABEL_PATTERN}\.?\s*\(?\s*{NUMBER_PATTERN}\s*\)?")
PAREN_SUBPART_REF_RE = re.compile(
    r"\b(?P<label>table|figure|fig)\.?\s+(?P<number>\d{1,3})(?=\s*\(\s*[a-z]\s*\))",
    re.IGNORECASE,
)
RANGE_REFERENCE_RE = re.compile(
    r"\b(?:tables|figures|theorems|lemmas|algorithms|sections|equations|eqs)\s+"
    r"(?:\d\s*){1,3}(?:\.\d+)?\s*(?:-|to|and)\s*(?:\d\s*){1,3}\b"
)
LOCAL_STATEMENT_HEAD_RE = re.compile(
    r"(?:^|[\n\f]|[.!?])\s*(?P<label>theorem|lemma|proposition|corollary|definition)\s+"
    rf"{NUMBER_PATTERN}\b"
)
LOCAL_ALT_STATEMENT_HEAD_RE = re.compile(
    rf"(?:^|[\n\f]|[.!?])\s*{NUMBER_PATTERN}\s+"
    r"(?P<label>theorem|lemma|proposition|corollary|definition)\b"
)
LOCAL_ALGORITHM_HEAD_RE = re.compile(
    rf"(?:^|[\n\f])\s*(?P<label>algorithm)\s+{NUMBER_PATTERN}\b"
)
LOCAL_TABLE_HEAD_RE = re.compile(
    rf"(?:^|[\n\f])\s*(?P<label>table|tab)\.?\s+{NUMBER_PATTERN}\s*[:.]"
)
LOCAL_FIGURE_HEAD_RE = re.compile(
    rf"(?:^|[\n\f])\s*(?P<label>figure|fig)\.?\s+{NUMBER_PATTERN}\s*[:.]"
)
EQUATION_BODY = r"(?:=|\bsum\b|\bargm(?:in|ax)\b|\bsoftmax\b|\blog\b|\bexp\b)[^\n]{0,160}"
LOCAL_EQUATION_RE = re.compile(
    EQUATION_BODY + r"\(\s*(?P<number>\d{1,3})\s*\)\s*(?=$|[\n\f])"
)
PROOF_HEAD_RE = re.compile(
    r"(?:^|[\n\f])\s*(?:[a-z]\.\d+\s+)?proofs?(?:\s+of\s+(?:theorem|lemma|proposition|corollary)"
    r"\s+[\d.]+)?\s*[.:]"
)
RELATED_WORK_HEAD_RE = re.compile(
    r"(?:^|[\n\f])\s*(?:\d+(?:\.\d+)*\.?\s+)?(?:related work|prior work|previous work|"
    r"background and related work|literature review)\b"
)
ABSTRACT_HEAD_RE = re.compile(r"(?:^|[\n\f])\s*abstract\b")
INTRO_SUMMARY_RE = re.compile(
    r"(?:^|[\n\f])\s*(?:1\.?\s+)?introduction\b[\s\S]{0,1400}\b(?:our contributions|"
    r"we propose|in this paper|in this work|we introduce|we present)\b"
)
METHOD_HEAD_RE = re.compile(
    r"(?:^|[\n\f])\s*(?:\d+(?:\.\d+)*\.?\s+)?(?:method|methods|methodology|approach|our approach|"
    r"proposed method|model architecture|architecture|training objective|objective|"
    r"loss function|problem formulation|preliminaries|model)\s*(?=$|[\n\f])"
)
DEFINE_MARKER_RE = re.compile(
    r"\b(?:we define|is defined as|we propose|we introduce|our objective|"
    r"we minimize|we maximize|is given by|is computed as)\b"
)
CITATION_ATTRIBUTION_RE = re.compile(
    r"(?:\bet al\b\.?|\[\s*\d{1,3}(?:\s*[,-]\s*\d{1,3})*\s*\])[^.\n]{0,80}"
    r"\b(?:propos|introduc|show|present|develop|stud|describ)\w*"
    r"|\b(?:propos|introduc|present|develop|describ)\w*\s+(?:by|in)\s+[^.\n]{0,60}"
    r"(?:\bet al\b|\[\s*\d)"
    r"|\b(?:following|as in|similar to|prior work|previous work|related work)\b[^.\n]{0,60}"
    r"(?:\bet al\b|\[\s*\d)"
)
USAGE_MENTION_RE = re.compile(
    r"\b(?:by (?:theorem|lemma|proposition|corollary)|using (?:theorem|lemma)|we apply|"
    r"applying|as a baseline|baselines?|we adopt|we follow|we reuse|plugging)\b"
)
ORIGINAL_SOURCE_RE = re.compile(r"(?<![\d.])\d{4}\.\d{4,5}(?:v\d+)?(?!\d)|\barxiv\b|\bproceedings\b")
SECONDARY_SOURCE_RE = re.compile(
    r"\b(?:survey|review|tutorial|slides?|lecture notes?|lectures?|blog|cheat ?sheet|"
    r"reading notes?|study notes?)\b"
)


@dataclass(slots=True)
class AliasResult:
    page_id: int
    doc_id: int
    title: str
    path: str
    page_number: int
    snippet: str
    content: str
    bm25_score: float


@dataclass(slots=True)
class PageHit:
    final_score: float
    best_score: float
    hit_count: int
    alias_count: int
    aliases: list[str] = field(default_factory=list)
    db: str = ""
    title: str = ""
    path: str = ""
    page_number: int = 0
    snippet: str = ""
    classification: str = ""
    reasons: list[str] = field(default_factory=list)
    features: dict[str, object] = field(default_factory=dict)
    score_breakdown: dict[str, float] = field(default_factory=dict)
    statement_window: str = ""


@dataclass(slots=True)
class SurveyContext:
    allowed_doc_ids: set[int] = field(default_factory=set)
    doc_boosts: dict[int, float] = field(default_factory=dict)
    section_ranges: dict[int, list[tuple[int, int, float, str]]] = field(default_factory=dict)
    reasons: dict[int, list[str]] = field(default_factory=dict)


def configure_streams() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def parse_args() -> argparse.Namespace:
    parser = SafeArgumentParser(
        description="Aggregate multi-alias search over local PDF indices."
    )
    parser.add_argument("--query", required=True, help="Primary search query.")
    parser.add_argument(
        "--alias",
        action="append",
        default=[],
        help="Additional alias or reformulation. Can be passed multiple times.",
    )
    parser.add_argument(
        "--db",
        action="append",
        default=[],
        help="Explicit SQLite PDF database path. Defaults to auto-discovered pdf*.sqlite3 files.",
    )
    parser.add_argument(
        "--data-root",
        default=str(DEFAULT_DATA_ROOT),
        help="Directory used when auto-discovering pdf*.sqlite3 databases.",
    )
    parser.add_argument("--limit", type=int, default=12, help="Maximum results to print.")
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Emit a compact top-3 evidence summary for model context.",
    )
    parser.add_argument(
        "--compact-limit",
        type=int,
        default=3,
        help="Maximum results to include with --compact.",
    )
    parser.add_argument(
        "--evidence-chars",
        type=int,
        default=220,
        help="Maximum snippet characters per compact result.",
    )
    parser.add_argument(
        "--why-limit",
        type=int,
        default=4,
        help="Maximum reason strings per compact result.",
    )
    parser.add_argument(
        "--verify-rank",
        type=int,
        default=0,
        help="Include the local statement/evidence window for this 1-based rank.",
    )
    parser.add_argument(
        "--alias-mode",
        choices=("expanded", "core", "full", "auto"),
        default="expanded",
        help="Alias expansion mode. Default keeps the expanded alias set.",
    )
    return parse_public_args(parser, route='indexed')


def unique_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = value.strip()
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def compact_text(text: str, limit: int) -> str:
    collapsed = re.sub(r"\s+", " ", text).strip()
    if limit <= 0 or len(collapsed) <= limit:
        return collapsed
    return collapsed[: max(0, limit - 3)].rstrip() + "..."


def compact_payload(
    *,
    query: str,
    aliases: list[str],
    db_paths: list[Path],
    hits: list[PageHit],
    result_limit: int,
    evidence_chars: int,
    why_limit: int,
    diagnostics: list[SearchDiagnostic] | None = None,
) -> dict[str, object]:
    diagnostics = diagnostics or []
    return {
        "query": query,
        "alias_count": len(aliases),
        "db_count": len(db_paths),
        "result_count": len(hits),
        "shown": min(max(0, result_limit), len(hits)),
        "diagnostics_summary": summarize_diagnostics(diagnostics),
        "warnings": [
            item
            for item in diagnostics_to_dicts(
                [diagnostic for diagnostic in diagnostics if diagnostic.level != "info"][:8]
            )
        ],
        "results": [
            {
                "rank": rank,
                "score": round(item.final_score, 3),
                "title": item.title,
                "path": item.path,
                "page_number": item.page_number,
                "match_type": item.classification,
                "why": item.reasons[: max(0, why_limit)],
                "evidence": compact_text(item.snippet, evidence_chars),
                "features": {
                    key: value
                    for key, value in item.features.items()
                    if key
                    in {
                        "page_role",
                        "local_statement",
                        "direct_statement",
                        "hard_concepts_missing",
                        "classification",
                    }
                },
            }
            for rank, item in enumerate(hits[: max(0, result_limit)], start=1)
        ],
    }


SIGNATURE_STOPWORDS = GENERIC_WORDS | set(STATEMENT_LABELS) | {
    "assume",
    "can",
    "clearly",
    "compute",
    "does",
    "find",
    "given",
    "how",
    "know",
    "number",
    "numbers",
    "prove",
    "shown",
    "shows",
    "similarly",
    "such",
    "sum_",
    "these",
    "what",
}
SHORT_SIGNAL_TOKENS = {
    "f1",
    "kl",
    "l1",
    "l2",
    "lr",
    "qk",
    "rl",
    "t5",
}
KEYWORD_ALIAS_STOPWORDS = SIGNATURE_STOPWORDS | {
    "give",
    "layer",
    "layers",
    "network",
    "networks",
    "proof",
    "set",
    "table",
    "training",
}
GREEK_ALIAS_NOISE = {
    "alpha",
    "beta",
    "delta",
    "epsilon",
    "eta",
    "gamma",
    "lambda",
    "mu",
    "nu",
    "omega",
    "phi",
    "pi",
    "rho",
    "sigma",
    "tau",
    "theta",
    "xi",
}
TWO_LETTER_FORMULA_WORDS = {
    "ab",
    "hw",
    "kv",
    "mn",
    "qk",
    "qv",
    "uv",
    "wx",
    "xy",
}
FORMULA_CONTEXT_RE = re.compile(
    r"\b(?:softmax|argmax|argmin|log|sqrt|exp|loss|nabla|sum|expectation)\b"
)

STATEMENT_PHRASES = (
    "scaled dot-product attention",
    "multi-head attention",
    "residual learning",
    "policy gradient theorem",
    "universal approximation",
    "master theorem",
    "dynamic programming",
    "approximation ratio",
    "lower bound",
    "upper bound",
    "time complexity",
)

FORMAL_STATEMENT_MARKERS = (
    "let ",
    "suppose",
    "assume",
    "given",
    "there exists",
    "such that",
    "for any",
    "for all",
    "with probability",
    "we define",
)


def normalize_paper_query(text: str) -> str:
    return canonicalize_paper_query(text)


def normalize_structured_paper_text(text: str) -> str:
    lines: list[str] = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n").split("\n"):
        lines.append(normalize_paper_query(raw_line))
    return "\n".join(lines).strip()


@lru_cache(maxsize=128)
def cached_query_spec(search_spec: str) -> QuerySpec:
    return build_query_spec(search_spec)


def canonical_label(label: str) -> str:
    lowered = label.casefold()
    return LABEL_ALIASES.get(lowered, lowered)


def informative_word_count(text: str) -> int:
    words = re.findall(r"[A-Za-z][A-Za-z0-9_-]*", normalize_paper_query(text).casefold())
    useful = {
        word
        for word in words
        if (len(word) >= 3 and word not in GENERIC_WORDS)
        or re.fullmatch(r"[a-z]\d+", word)
        or re.fullmatch(r"[a-z]\d+[a-z]", word)
        or re.fullmatch(r"[a-z]{2}\d+", word)
        or re.fullmatch(r"\d+[a-z]\d*[a-z]*", word)
    }
    return len(useful)


def should_keep_alias(text: str) -> bool:
    cleaned = normalize_paper_query(text)
    if not cleaned:
        return False
    if re.search(r"[{}<>/=\\]", cleaned) and informative_word_count(cleaned) < 2:
        return False
    if re.search(r"[\u3400-\u9fff]", text):
        return informative_word_count(cleaned) >= 2 and len(cleaned) <= 80
    if informative_word_count(cleaned) >= 2:
        return True
    words = WORD_RE.findall(cleaned.casefold())
    short_signal_count = sum(1 for word in words if word in SHORT_SIGNAL_TOKENS) + len(
        compact_symbolic_tokens(cleaned)
    )
    compact_word_count = sum(
        1
        for word in words
        if word in SHORT_SIGNAL_TOKENS
        or word in TWO_LETTER_FORMULA_WORDS
        or bool(re.fullmatch(r"[a-z]\d+", word))
        or bool(re.fullmatch(r"[a-z]\d+[a-z]", word))
        or bool(re.fullmatch(r"[a-z]{2}\d+", word))
        or bool(re.fullmatch(r"\d+[a-z]\d*[a-z]*", word))
    )
    numbered_alias = bool(re.match(r"^\d[\d.\s]*\s+", cleaned))
    structured_alias = (
        re.search(rf"\b(?:{'|'.join(STATEMENT_LABELS)})\b", cleaned)
        and re.search(r"\d", cleaned)
    ) or (numbered_alias and compact_word_count >= 2)
    return bool(structured_alias and (short_signal_count >= 2 or (numbered_alias and compact_word_count >= 2)))


def is_reference_alias(text: str) -> bool:
    lowered = normalize_paper_query(text).casefold()
    return bool(
        re.search(
            rf"\b(?:{'|'.join(STATEMENT_LABELS)})\s+(?:\d[\d.\s]*)\b",
            lowered,
        )
    )


def discover_dbs_with_diagnostics(
    explicit: list[str],
    data_root: Path,
) -> tuple[list[Path], list[SearchDiagnostic]]:
    return discover_sqlite_dbs(explicit=explicit, data_root=data_root)


def discover_dbs(explicit: list[str], data_root: Path) -> list[Path]:
    """Adapter used by the evaluation harness."""
    db_paths, diagnostics = discover_dbs_with_diagnostics(explicit, data_root)
    for diagnostic in diagnostics:
        record_diagnostic(diagnostic)
    return db_paths


def search_alias_in_db(
    con: sqlite3.Connection,
    alias: str,
    limit: int,
    allowed_doc_ids: set[int] | None = None,
) -> list[AliasResult]:
    match_query = build_match_query(alias)
    params: list[object] = [match_query]
    doc_filter_sql = ""
    if allowed_doc_ids:
        doc_ids = sorted(allowed_doc_ids)
        placeholders = ",".join("?" for _ in doc_ids)
        doc_filter_sql = f" AND d.id IN ({placeholders})"
        params.extend(doc_ids)
    params.append(limit)
    rows = con.execute(
        f"""
        SELECT
            p.id AS page_id,
            d.id AS doc_id,
            d.title,
            d.path,
            p.page_number,
            p.snippet,
            p.content,
            bm25(pdf_page_fts, 3.0, 2.5, 0.5, 1.0) AS bm25_score
        FROM pdf_page_fts
        JOIN pdf_pages p ON p.id = CAST(pdf_page_fts.page_id AS INTEGER)
        JOIN pdf_docs d ON d.id = p.doc_id
        WHERE pdf_page_fts MATCH ?
        {doc_filter_sql}
        ORDER BY bm25_score
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [
        AliasResult(
            page_id=int(row["page_id"]),
            doc_id=int(row["doc_id"]),
            title=str(row["title"]),
            path=str(row["path"]),
            page_number=int(row["page_number"]),
            snippet=str(row["snippet"]),
            content=str(row["content"]),
            bm25_score=abs(float(row["bm25_score"])),
        )
        for row in rows
    ]


def merge_alias_results(
    primary: list[AliasResult],
    secondary: list[AliasResult],
    limit: int,
) -> list[AliasResult]:
    merged = list(primary)
    seen_pages = {item.page_id for item in primary}
    for item in secondary:
        if item.page_id in seen_pages:
            continue
        seen_pages.add(item.page_id)
        merged.append(item)
        if len(merged) >= limit:
            break
    return merged


def survey_reason_bucket(
    reasons: dict[int, list[str]],
    doc_id: int,
) -> list[str]:
    return reasons.setdefault(doc_id, [])


def build_survey_context(con: sqlite3.Connection, alias: str) -> SurveyContext:
    context = SurveyContext()
    survey_query = strip_generic_words(alias) or alias
    try:
        doc_results = query_pdf_doc_surveys(con, survey_query, limit=6)
        section_results = query_pdf_sections(con, survey_query, limit=10)
    except Exception:
        return context

    for item in doc_results:
        context.allowed_doc_ids.add(item.doc_id)
        boost = min(18.0, 4.0 + 0.14 * item.score)
        current = context.doc_boosts.get(item.doc_id, 0.0)
        if boost > current:
            context.doc_boosts[item.doc_id] = boost
        reason_list = survey_reason_bucket(context.reasons, item.doc_id)
        label = f"survey document match: {item.doc_type}"
        if label not in reason_list:
            reason_list.append(label)

    for item in section_results:
        context.allowed_doc_ids.add(item.doc_id)
        ranges = context.section_ranges.setdefault(item.doc_id, [])
        ranges.append(
            (
                item.page_start,
                item.page_end,
                min(28.0, 6.0 + 0.18 * item.score),
                f"{item.section_title} ({item.page_start}-{item.page_end})",
            )
        )
        reason_list = survey_reason_bucket(context.reasons, item.doc_id)
        label = f"survey section match: {item.section_title}"
        if label not in reason_list:
            reason_list.append(label)
        current = context.doc_boosts.get(item.doc_id, 0.0)
        context.doc_boosts[item.doc_id] = max(current, min(14.0, 3.0 + 0.10 * item.score))

    for ranges in context.section_ranges.values():
        ranges.sort(key=lambda item: (-item[2], item[0], item[1], item[3]))
    return context


def merge_survey_contexts(contexts: list[SurveyContext]) -> SurveyContext:
    merged = SurveyContext()
    for context in contexts:
        merged.allowed_doc_ids.update(context.allowed_doc_ids)
        for doc_id, boost in context.doc_boosts.items():
            merged.doc_boosts[doc_id] = max(merged.doc_boosts.get(doc_id, 0.0), boost)
        for doc_id, ranges in context.section_ranges.items():
            merged.section_ranges.setdefault(doc_id, []).extend(ranges)
        for doc_id, reasons in context.reasons.items():
            bucket = merged.reasons.setdefault(doc_id, [])
            for reason in reasons:
                if reason not in bucket:
                    bucket.append(reason)
    for ranges in merged.section_ranges.values():
        ranges.sort(key=lambda item: (-item[2], item[0], item[1], item[3]))
    return merged


def survey_alias_candidates(primary_query: str, aliases: list[str]) -> list[str]:
    candidates = unique_keep_order([primary_query, *aliases])
    selected: list[str] = []
    for alias in candidates:
        stripped = strip_generic_words(alias)
        informative = stripped or alias
        count = informative_word_count(informative)
        if count < 3:
            continue
        if count >= 5 or detect_hard_concepts(alias) or structural_cue_terms(alias):
            selected.append(alias)
    if not selected:
        return candidates[:1]
    return selected[:4]


def survey_page_bonus(
    doc_id: int,
    page_number: int,
    context: SurveyContext,
) -> tuple[float, list[str]]:
    bonus = context.doc_boosts.get(doc_id, 0.0)
    reasons = list(context.reasons.get(doc_id, [])) if bonus > 0.0 else []
    best_section_bonus = 0.0
    best_section_reason: str | None = None
    near_section_bonus = 0.0
    near_section_reason: str | None = None

    for page_start, page_end, section_bonus, label in context.section_ranges.get(doc_id, []):
        if page_start <= page_number <= page_end:
            if section_bonus > best_section_bonus:
                best_section_bonus = section_bonus
                best_section_reason = f"page falls inside matched section: {label}"
        elif page_start - 1 <= page_number <= page_end + 1:
            edge_bonus = max(4.0, section_bonus * 0.35)
            if edge_bonus > near_section_bonus:
                near_section_bonus = edge_bonus
                near_section_reason = f"page is adjacent to matched section: {label}"

    if best_section_bonus > 0.0:
        bonus += best_section_bonus
        if best_section_reason is not None:
            reasons.append(best_section_reason)
    elif near_section_bonus > 0.0:
        bonus += near_section_bonus
        if near_section_reason is not None:
            reasons.append(near_section_reason)

    return bonus, reasons


def candidate_text(title: str, path: str, snippet: str, content: str) -> str:
    return " ".join([title, path, snippet, content])


def original_source_score(title: str, path: str) -> tuple[float, list[str]]:
    """Prefer an original paper over a secondary copy without encoding a gold path."""

    raw = f"{title} {path}"
    source = normalize_paper_query(raw).casefold()
    source = re.sub(r"[_/\\-]+", " ", source)
    if SECONDARY_SOURCE_RE.search(source):
        return -24.0, ["secondary source (survey/tutorial/slides), not the original paper"]
    if ORIGINAL_SOURCE_RE.search(raw.casefold()) or ORIGINAL_SOURCE_RE.search(source):
        return 24.0, ["original paper source (arXiv/proceedings)"]
    return 0.0, []


def noisy_page_role(title: str, path: str, content: str) -> str | None:
    meta = normalize_paper_query(" ".join([title, path])).casefold()
    if any(term in meta for term in NOISY_PAGE_TERMS):
        return "contents"
    head_lines = [
        re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", normalize_paper_query(line).casefold()).strip()
        for line in content.replace("\f", "\n").splitlines()[:12]
        if line.strip()
    ]
    for line in head_lines:
        if line in {"references", "bibliography"}:
            return "references"
        if line in NOISY_HEADINGS:
            return "contents"
    head = normalize_paper_query(content[:400]).casefold()
    if any(term in head for term in NOISY_PAGE_TERMS):
        return "contents"
    return None


def has_noisy_marker(title: str, path: str, content: str) -> bool:
    return noisy_page_role(title, path, content) is not None


def min_cover_span(position_groups: list[list[int]]) -> int | None:
    if not position_groups or any(not group for group in position_groups):
        return None
    best: int | None = None

    def dfs(index: int, chosen: list[int]) -> None:
        nonlocal best
        if best is not None and chosen:
            current = max(chosen) - min(chosen)
            if current >= best:
                return
        if index == len(position_groups):
            current = max(chosen) - min(chosen)
            if best is None or current < best:
                best = current
            return
        for value in position_groups[index][:12]:
            chosen.append(value)
            dfs(index + 1, chosen)
            chosen.pop()

    dfs(0, [])
    return best


def term_positions(text: str, terms: tuple[str, ...]) -> list[int]:
    positions: list[int] = []
    for term in terms:
        start = 0
        while True:
            idx = text.find(term, start)
            if idx == -1:
                break
            positions.append(idx)
            start = idx + len(term)
    positions.sort()
    return positions


def min_cover_window(position_groups: list[list[int]]) -> tuple[int, int, int] | None:
    if not position_groups or any(not group for group in position_groups):
        return None

    merged: list[tuple[int, int]] = []
    for group_index, group in enumerate(position_groups):
        for position in group:
            merged.append((position, group_index))
    merged.sort()

    counts = [0] * len(position_groups)
    have = 0
    left = 0
    best: tuple[int, int, int] | None = None

    for right, (position, group_index) in enumerate(merged):
        if counts[group_index] == 0:
            have += 1
        counts[group_index] += 1

        while have == len(position_groups):
            start_pos, left_group = merged[left]
            span = position - start_pos
            if best is None or span < best[0]:
                best = (span, start_pos, position)
            counts[left_group] -= 1
            if counts[left_group] == 0:
                have -= 1
            left += 1

    return best


def local_window(content: str, start: int, end: int, before: int = 100, after: int = 220) -> str:
    return content[max(0, start - before) : min(len(content), end + after)]


def role_head_patterns(page_role: str) -> list[re.Pattern[str]]:
    if page_role in STATEMENT_ROLES:
        return [LOCAL_STATEMENT_HEAD_RE, LOCAL_ALT_STATEMENT_HEAD_RE]
    if page_role in ALGORITHM_ROLES:
        return [LOCAL_ALGORITHM_HEAD_RE]
    if page_role in RESULT_ROLES:
        return [LOCAL_TABLE_HEAD_RE]
    if page_role in FIGURE_ROLES:
        return [LOCAL_FIGURE_HEAD_RE]
    if page_role == "method":
        return [METHOD_HEAD_RE, LOCAL_EQUATION_RE]
    return []


def role_window_bounds(page_role: str) -> tuple[int, int]:
    if page_role in STATEMENT_ROLES:
        return 40, 1400
    if page_role in ALGORITHM_ROLES:
        return 20, 1600
    if page_role in RESULT_ROLES:
        return 20, 1400
    if page_role in FIGURE_ROLES:
        return 200, 900
    if page_role == "method":
        return 400, 900
    return 80, 1200


def local_statement_window(content: str, page_role: str, fallback_chars: int = 1400) -> str:
    normalized = normalize_structured_paper_text(content).casefold()
    before, after = role_window_bounds(page_role)
    for pattern in role_head_patterns(page_role):
        match = pattern.search(normalized)
        if match:
            return local_window(normalized, match.start(), match.end(), before=before, after=after)
    return normalized[:fallback_chars]


def query_statement_window(
    content: str, page_role: str, query_ref: tuple[str, str] | None
) -> str | None:
    window, _start = query_statement_match(content, page_role, query_ref)
    return window


def query_statement_match(
    content: str, page_role: str, query_ref: tuple[str, str] | None
) -> tuple[str | None, int | None]:
    if query_ref is None:
        return None, None
    normalized = normalize_structured_paper_text(content).casefold()
    label, number = query_ref
    number_pattern = r"\s*\.\s*".join(re.escape(part) for part in number.split(".") if part)
    label_forms = [label, *[short for short, full in LABEL_ALIASES.items() if full == label]]
    label_pattern = "|".join(re.escape(form) for form in label_forms)
    patterns: list[re.Pattern[str]] = []
    if label == "equation":
        patterns.append(re.compile(EQUATION_BODY + rf"\(\s*{number_pattern}\s*\)\s*(?=$|[\n\f])"))
    else:
        patterns.extend(
            [
                re.compile(rf"(?:^|[\n\f])\s*(?:{label_pattern})\.?\s+{number_pattern}\s*[:.](?!\d)"),
                re.compile(rf"(?:^|[\n\f]|[.!?])\s*(?:{label_pattern})\.?\s+{number_pattern}\b(?!\.\d)"),
                re.compile(rf"\b(?:{label_pattern})\.?\s+{number_pattern}\b(?!\.\d)"),
            ]
        )
        if label in THEOREM_ROLES:
            patterns.append(re.compile(rf"\b{number_pattern}\s+{re.escape(label)}\b"))

    role = LABEL_ROLE.get(label, page_role)
    before, after = role_window_bounds(role)
    for pattern in patterns:
        match = pattern.search(normalized)
        if match:
            return (
                local_window(normalized, match.start(), match.end(), before=before, after=after),
                match.start(),
            )
    return None, None


def secondary_reference_score(search_spec: str, local_text: str) -> tuple[float, list[str]]:
    refs = extract_statement_refs(search_spec)
    if len(refs) < 2:
        return 0.0, []
    local_refs = set(extract_statement_refs(local_text))
    matched = [f"{label} {number}" for label, number in refs[1:4] if (label, number) in local_refs]
    if not matched:
        return 0.0, []
    return 8.0 + 2.0 * max(0, len(matched) - 1), [f"keeps secondary reference(s): {', '.join(matched)}"]


def has_overview_reference(text: str) -> bool:
    lowered = normalize_paper_query(text).casefold()
    return bool(
        RANGE_REFERENCE_RE.search(lowered)
        or "such as" in lowered
        or "we review" in lowered
        or "overview of" in lowered
        or "summarized in" in lowered
    )


def has_citation_mention(text: str) -> bool:
    """A cited or attributed mention is not the original statement."""

    lowered = normalize_paper_query(text).casefold()
    return bool(CITATION_ATTRIBUTION_RE.search(lowered) or RELATED_WORK_HEAD_RE.search(lowered))


def has_usage_mention(text: str) -> bool:
    lowered = normalize_paper_query(text).casefold()
    return bool(USAGE_MENTION_RE.search(lowered))


def compact_symbolic_tokens(text: str) -> list[str]:
    normalized = normalize_paper_query(text).casefold()
    words = WORD_RE.findall(normalized)
    symbolic_context = bool(
        re.search(r"[=|+\-/*^()<>]", text) or FORMULA_CONTEXT_RE.search(normalized)
    )
    result: list[str] = []
    seen: set[str] = set()
    repeated_two_letter_words: dict[str, int] = {}
    contextual_two_letter_words: set[str] = set()
    for word in words:
        if re.fullmatch(r"[a-z]{2}", word):
            if word in TWO_LETTER_FORMULA_WORDS or word in SHORT_SIGNAL_TOKENS:
                repeated_two_letter_words[word] = repeated_two_letter_words.get(word, 0) + 1
    for match in re.finditer(r"\b([A-Za-z]{2})\b", text):
        token = match.group(1).casefold()
        if (
            token not in TWO_LETTER_FORMULA_WORDS
            and token not in SHORT_SIGNAL_TOKENS
        ):
            continue
        start, end = match.span()
        window = text[max(0, start - 3) : min(len(text), end + 3)]
        if re.search(r"[(),=|+\-/*^]", window):
            contextual_two_letter_words.add(token)
    for word in words:
        if not (
            re.fullmatch(r"[a-z]\d+", word)
            or re.fullmatch(r"[a-z]\d+[a-z]", word)
            or re.fullmatch(r"[a-z]{2}\d+", word)
            or (
                symbolic_context
                and re.fullmatch(r"[a-z]{2}", word)
                and (
                    word in SHORT_SIGNAL_TOKENS
                    or word in contextual_two_letter_words
                    or repeated_two_letter_words.get(word, 0) >= 2
                )
            )
        ):
            continue
        if word in seen:
            continue
        seen.add(word)
        result.append(word)
    if symbolic_context:
        for piece in re.findall(r"\b\d+[a-z]\d*[a-z]*\b", normalized):
            if piece in seen:
                continue
            seen.add(piece)
            result.append(piece)
    return result


def symbolic_cluster_aliases(query: str, extra_aliases: list[str]) -> list[str]:
    text = " ".join([query, *extra_aliases])
    compact = compact_symbolic_tokens(text)
    if len(compact) < 2:
        return []

    aliases = [" ".join(compact[:2])]
    if len(compact) >= 3:
        aliases.append(" ".join(compact[:3]))
    if len(compact) >= 4:
        aliases.append(" ".join(compact[:4]))
    return unique_keep_order(aliases)


def split_symbol_token(token: str) -> str:
    if re.fullmatch(r"[a-z]\d+", token):
        return f"{token[0]} {token[1:]}"
    if re.fullmatch(r"[a-z]\d+[a-z]", token):
        return f"{token[0]} {token[1:-1]} {token[-1]}"
    return token


def symbolic_focus_aliases(query: str, extra_aliases: list[str]) -> list[str]:
    text = " ".join([query, *extra_aliases])
    tokens = compact_symbolic_tokens(text)
    if not tokens:
        return []

    aliases: list[str] = []
    digit_heavy = [token for token in tokens if any(ch.isdigit() for ch in token)]
    if len(digit_heavy) >= 3:
        aliases.append(" ".join(digit_heavy[:3]))
        if len(digit_heavy) >= 4:
            aliases.append(" ".join(digit_heavy[:4]))

    expanded_first = split_symbol_token(tokens[0])
    if expanded_first != tokens[0]:
        mixed = [expanded_first, *tokens[1:3]]
        aliases.append(" ".join(mixed))
        if len(tokens) >= 4:
            aliases.append(" ".join([expanded_first, *tokens[1:4]]))
    companion_short = [
        word
        for word in WORD_RE.findall(normalize_paper_query(text).casefold())
        if word in TWO_LETTER_FORMULA_WORDS and word not in tokens
    ]
    if expanded_first != tokens[0] and companion_short:
        aliases.append(" ".join([expanded_first, companion_short[0]]))
    return unique_keep_order(aliases)


FORMULA_VARIABLE_WORDS = {"a", "b", "d", "h", "k", "m", "n", "q", "r", "s", "t", "v", "w", "x", "y", "z"}


def variable_focus_aliases(query: str, extra_aliases: list[str]) -> list[str]:
    text = " ".join([query, *extra_aliases])
    compact = compact_symbolic_tokens(text)
    if not compact:
        return []
    variables: list[str] = []
    seen: set[str] = set()
    for word in WORD_RE.findall(normalize_paper_query(text).casefold()):
        if word not in FORMULA_VARIABLE_WORDS or word in seen:
            continue
        seen.add(word)
        variables.append(word)
    if len(variables) < 2:
        return []

    aliases: list[str] = []
    tail_symbol = compact[-1]
    aliases.append(" ".join([tail_symbol, *variables[:3]]))
    aliases.append(" ".join([*compact[:2], *variables[:2]]))
    return unique_keep_order(aliases)


def informative_words(text: str, limit: int | None = None) -> list[str]:
    words = WORD_RE.findall(normalize_paper_query(text).casefold())
    result: list[str] = []
    seen: set[str] = set()
    for word in words:
        keep = (
            (len(word) >= 3 and word not in GENERIC_WORDS)
            or bool(re.fullmatch(r"[a-z]\d+", word))
            or bool(re.fullmatch(r"[a-z]\d+[a-z]", word))
            or bool(re.fullmatch(r"[a-z]{2}\d+", word))
            or bool(re.fullmatch(r"\d+[a-z]\d*[a-z]*", word))
        )
        if not keep:
            continue
        if word in seen:
            continue
        seen.add(word)
        result.append(word)
        if limit is not None and len(result) >= limit:
            break
    if limit is None or len(result) < limit:
        for token in compact_symbolic_tokens(text):
            if token in seen:
                continue
            seen.add(token)
            result.append(token)
            if limit is not None and len(result) >= limit:
                break
    return result


def is_labeled_statement_query(text: str) -> bool:
    lowered = normalize_paper_query(text).casefold()
    return bool(re.search(r"\b(?:theorem|lemma|proposition|corollary)\b", lowered))


def statement_labels(text: str) -> list[str]:
    lowered = normalize_paper_query(text).casefold()
    return [label for label in ("theorem", "lemma", "proposition", "corollary") if label in lowered]


def normalize_statement_number(text: str) -> str:
    cleaned = re.sub(r"\s+", "", text).strip().strip(".")
    parts = [part for part in cleaned.split(".") if part]
    if len(parts) >= 2 and len(set(parts)) == 1:
        return parts[0]
    return cleaned


def unique_statement_refs(refs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    result: list[tuple[str, str]] = []
    for ref in refs:
        if ref in seen:
            continue
        seen.add(ref)
        result.append(ref)
    return result


def extract_statement_refs(text: str) -> list[tuple[str, str]]:
    matches: list[tuple[int, tuple[str, str]]] = []
    for match in PAREN_SUBPART_REF_RE.finditer(text):
        matches.append(
            (
                match.start(),
                (
                    canonical_label(match.group("label")),
                    normalize_statement_number(match.group("number")),
                ),
            )
        )
    lowered = normalize_paper_query(text).casefold()
    for match in STATEMENT_REF_RE.finditer(lowered):
        matches.append(
            (
                match.start(),
                (
                    canonical_label(match.group("label")),
                    normalize_statement_number(match.group("number")),
                ),
            )
        )
    matches.sort(key=lambda item: item[0])
    return unique_statement_refs([ref for _start, ref in matches])


def extract_local_statement_refs(text: str, page_role: str) -> list[tuple[str, str]]:
    lowered = normalize_structured_paper_text(text).casefold()
    refs: list[tuple[str, str]] = []
    for pattern in role_head_patterns(page_role):
        for match in pattern.finditer(lowered):
            groups = match.groupdict()
            number = groups.get("number")
            if not number:
                continue
            label = groups.get("label") or ROLE_LABELS.get(page_role, (page_role,))[0]
            refs.append(
                (
                    canonical_label(label),
                    normalize_statement_number(number),
                )
            )
    return unique_statement_refs(refs)


def primary_statement_ref(text: str) -> tuple[str, str] | None:
    refs = extract_statement_refs(text)
    return refs[0] if refs else None


def signature_terms(text: str, limit: int | None = None) -> list[str]:
    words = WORD_RE.findall(normalize_paper_query(text).casefold())
    result: list[str] = []
    seen: set[str] = set()
    for word in words:
        keep = (
            (len(word) >= 3 and word not in SIGNATURE_STOPWORDS)
            or bool(re.fullmatch(r"[a-z]\d+", word))
            or bool(re.fullmatch(r"\d+[a-z]\d*[a-z]*", word))
            or word in SHORT_SIGNAL_TOKENS
        )
        if not keep or word in seen:
            continue
        seen.add(word)
        result.append(word)
        if limit is not None and len(result) >= limit:
            break
    if limit is None or len(result) < limit:
        for token in compact_symbolic_tokens(text):
            if token in seen:
                continue
            seen.add(token)
            result.append(token)
            if limit is not None and len(result) >= limit:
                break
    return result


def short_signal_terms(text: str, limit: int | None = None) -> list[str]:
    words = WORD_RE.findall(normalize_paper_query(text).casefold())
    result: list[str] = []
    seen: set[str] = set()
    for word in words:
        if word not in SHORT_SIGNAL_TOKENS or word in seen:
            continue
        seen.add(word)
        result.append(word)
        if limit is not None and len(result) >= limit:
            break
    return result


def query_phrases(text: str) -> list[str]:
    lowered = normalize_paper_query(text).casefold()
    return [phrase for phrase in STATEMENT_PHRASES if phrase in lowered]


def content_phrases(text: str, min_size: int = 3, max_size: int = 5) -> list[str]:
    words = informative_words(text, limit=18)
    phrases: list[str] = []
    for size in range(max_size, min_size - 1, -1):
        if len(words) < size:
            continue
        for start in range(0, len(words) - size + 1):
            chunk = words[start : start + size]
            if len(set(chunk)) < size:
                continue
            phrases.append(" ".join(chunk))
    return unique_keep_order(phrases)


def matched_signature_terms(search_spec: str, text: str, limit: int = 14) -> list[str]:
    query_term_set = set(signature_terms(search_spec, limit=limit))
    if not query_term_set:
        return []
    lowered = normalize_paper_query(text).casefold()
    return sorted(
        term
        for term in query_term_set
        if any(variant in lowered for variant in term_variants(term))
    )


def query_relevant_statement_window(
    search_spec: str,
    content: str,
    page_role: str,
    fallback_chars: int = 1000,
) -> str:
    """Choose the labeled statement segment that best matches the query."""

    normalized = normalize_structured_paper_text(content).casefold()
    patterns = role_head_patterns(page_role)
    if not patterns:
        return normalized[:fallback_chars]

    starts = sorted(
        {
            match.start()
            for pattern in patterns
            for match in pattern.finditer(normalized)
        }
    )
    if not starts:
        return normalized[:fallback_chars]

    spec = cached_query_spec(search_spec)
    required = detect_concepts(search_spec)
    before, after = role_window_bounds(page_role)
    ranked: list[tuple[tuple[int, int, int], str]] = []
    for index, start in enumerate(starts):
        next_start = starts[index + 1] if index + 1 < len(starts) else len(normalized)
        window_start = max(0, start - before) if page_role in FIGURE_ROLES or page_role == "method" else start
        window = normalized[window_start : min(next_start, start + after)]
        matched_terms = len(matched_signature_terms(search_spec, window, limit=14))
        candidate_concepts = detect_concepts(window)
        matched_concepts = sum(
            1
            for name, needed in required.items()
            if needed and candidate_concepts.get(name)
        )
        anchor_matches = len(matching_exact_anchors(spec.exact_anchors, window))
        ranked.append(
            (
                (anchor_matches, matched_concepts, matched_terms),
                window,
            )
        )

    best_score, best_window = max(ranked, key=lambda item: item[0])
    if any(best_score):
        return best_window
    return local_statement_window(content, page_role, fallback_chars=fallback_chars)


def keyword_cluster_aliases(query: str, extra_aliases: list[str]) -> list[str]:
    words = informative_words(" ".join([query, *extra_aliases]), limit=20)
    stable = [
        word
        for word in words
        if word not in KEYWORD_ALIAS_STOPWORDS and word not in GREEK_ALIAS_NOISE
    ]
    aliases: list[str] = []
    if len(stable) >= 4:
        aliases.append(" ".join(stable[:3]))
        aliases.append(" ".join(stable[:4]))
        aliases.append(" ".join(stable[:5]))
        if len(stable) >= 5:
            aliases.append(" ".join(stable[1:5]))
        if len(stable) >= 6:
            aliases.append(" ".join(stable[-4:]))
        for size in (3, 4):
            if len(stable) < size + 2:
                continue
            for start in range(1, min(len(stable) - size + 1, 5)):
                aliases.append(" ".join(stable[start : start + size]))
    elif len(stable) >= 3:
        aliases.append(" ".join(stable[:3]))
    return unique_keep_order(aliases)


def statement_reference_aliases(query: str, extra_aliases: list[str]) -> list[str]:
    all_refs = extract_statement_refs(" ".join([query, *extra_aliases]))
    if not all_refs:
        return []
    query_ref = primary_statement_ref(query)
    refs = [query_ref] if query_ref else all_refs[:1]

    aliases: list[str] = []
    joined = " ".join([query, *extra_aliases])
    words = signature_terms(joined, limit=8)
    short_signal = short_signal_terms(joined, limit=4)
    symbolic_aliases = symbolic_cluster_aliases(query, extra_aliases)
    symbolic_focus = symbolic_focus_aliases(query, extra_aliases)
    variable_focus = variable_focus_aliases(query, extra_aliases)
    phrase_targets = content_phrases(joined, min_size=3, max_size=4)
    relaxed_words = [
        word
        for word in informative_words(joined, limit=10)
        if word not in GREEK_ALIAS_NOISE and word not in STATEMENT_LABELS
    ]
    relaxed_head = " ".join(relaxed_words[:4])
    head = " ".join(words[:4])
    tail = " ".join(words[-4:]) if len(words) > 4 else ""
    contentful_reference_query = bool(head or symbolic_aliases or short_signal)

    for label, number in refs[:2]:
        split_number = number.replace(".", " ")
        # A bare "Table 2" or "Theorem 1" occurs in nearly every paper; only a
        # dotted number or a content-free query may use the plain reference.
        allow_plain_reference = "." in number or not contentful_reference_query
        if allow_plain_reference:
            aliases.append(f"{label} {number}")
            if split_number != number:
                aliases.append(f"{label} {split_number}")
        for symbolic in [*symbolic_aliases[:3], *symbolic_focus[:2], *variable_focus[:2]]:
            aliases.append(f"{label} {number} {symbolic}")
            if split_number != number:
                aliases.append(f"{label} {split_number} {symbolic}")
        if short_signal and len(words) <= 4:
            compact_signal = " ".join(short_signal[:3])
            aliases.append(f"{label} {number} {compact_signal}")
        if head:
            aliases.append(f"{label} {number} {head}")
            if split_number != number:
                aliases.append(f"{label} {split_number} {head}")
        if tail and tail != head:
            aliases.append(f"{label} {number} {tail}")
        if relaxed_head and relaxed_head != head:
            aliases.append(f"{label} {number} {relaxed_head}")
        for phrase in phrase_targets[:2]:
            aliases.append(f"{label} {number} {phrase}")
            if label in THEOREM_ROLES:
                aliases.append(f"{phrase} {label} {number}")
    if len(all_refs) >= 2:
        primary_label, primary_number = all_refs[0]
        for secondary_label, secondary_number in all_refs[1:3]:
            aliases.append(f"{primary_label} {primary_number} {secondary_label} {secondary_number}")
            if head:
                aliases.append(
                    f"{primary_label} {primary_number} {secondary_label} {secondary_number} {head}"
                )
    return unique_keep_order(aliases)


def is_formal_statement_query(text: str) -> bool:
    if is_labeled_statement_query(text):
        return True
    lowered = normalize_paper_query(text).casefold()
    marker_count = sum(1 for marker in FORMAL_STATEMENT_MARKERS if marker in lowered)
    return marker_count >= 2 and len(signature_terms(text, limit=10)) >= 4


def statement_overlap(search_spec: str, text: str) -> tuple[float, list[str], bool]:
    query_term_set = set(signature_terms(search_spec, limit=14))
    query_phrase_set = set(query_phrases(search_spec))
    lowered = normalize_paper_query(text).casefold()
    matched_terms = matched_signature_terms(search_spec, text, limit=14)
    matched_phrases = sorted(phrase for phrase in query_phrase_set if phrase in lowered)

    if not query_term_set and not query_phrase_set:
        return 0.0, [], False

    score = 2.5 * len(matched_terms) + 8.0 * len(matched_phrases)
    reasons: list[str] = []
    if matched_phrases:
        reasons.append("matches statement phrase(s): " + ", ".join(matched_phrases[:3]))
    if matched_terms:
        reasons.append(
            f"matches {len(matched_terms)}/{max(1, len(query_term_set))} signature terms"
        )
    if len(query_term_set) >= 3 and len(matched_terms) == len(query_term_set):
        score += 8.0
        reasons.append("matches all signature terms")

    strong_overlap = (
        len(matched_phrases) >= 1 and len(matched_terms) >= 3
    ) or len(matched_terms) >= 5
    if len(query_term_set) >= 3 and len(matched_terms) == len(query_term_set):
        strong_overlap = True
    return score, reasons, strong_overlap


def content_phrase_score(search_spec: str, text: str) -> tuple[float, list[str], bool]:
    phrases = content_phrases(search_spec)
    if not phrases:
        return 0.0, [], False

    structured_text = " ".join(informative_words(text, limit=120))
    if not structured_text:
        return 0.0, [], False

    for phrase in phrases:
        if phrase not in structured_text:
            continue
        size = len(phrase.split())
        bonus = {5: 24.0, 4: 18.0, 3: 12.0}.get(size, 10.0)
        strong = size >= 4
        return bonus, [f"matches content phrase: {phrase}"], strong
    return 0.0, [], False


def numeric_anchor_score(search_spec: str, text: str) -> tuple[float, list[str]]:
    normalized_query = normalize_paper_query(search_spec)
    query_numbers = {
        number
        for number in re.findall(r"(?<![\w./-])\d+(?:\.\d+)?(?![\w-])", normalized_query)
        if "." in number or int(number) >= 20
    }
    labeled = {number for _label, number in extract_statement_refs(search_spec)}
    query_numbers -= labeled
    if not query_numbers:
        return 0.0, []
    text_numbers = set(re.findall(r"(?<![\w./-])\d+(?:\.\d+)?(?![\w-])", normalize_paper_query(text)))
    matched = sorted(query_numbers.intersection(text_numbers))
    if not matched:
        return -6.0, ["missing queried numeric value(s)"]
    bonus = min(24.0, 6.0 * len(matched))
    return bonus, [f"matches numeric anchors: {', '.join(matched[:4])}"]


def statement_reference_score(
    search_spec: str, page_role: str, local_text: str
) -> tuple[float, list[str], bool]:
    query_ref = primary_statement_ref(search_spec)
    local_refs = extract_local_statement_refs(local_text, page_role)
    if query_ref is None or not local_refs:
        return 0.0, [], False

    query_label, query_number = query_ref
    query_term_count = len(signature_terms(search_spec, limit=20))
    if (query_label, query_number) in local_refs:
        return (
            24.0,
            [f"matching local statement reference: {query_label} {query_number}"],
            True,
        )

    if query_label in THEOREM_ROLES:
        for local_label, local_number in local_refs:
            if local_number == query_number and local_label in THEOREM_ROLES:
                return (
                    10.0,
                    [f"matching local statement number in theorem family: {query_number}"],
                    False,
                )

    if any(local_label == query_label for local_label, _ in local_refs):
        if query_term_count >= 8:
            return (
                0.0,
                [f"relaxed statement-number mismatch for long noisy query: {query_label} {query_number}"],
                False,
            )
        return (
            -12.0,
            [f"different local statement number than queried {query_label} {query_number}"],
            False,
        )
    if query_term_count >= 8:
        return 0.0, [f"relaxed missing statement reference for long noisy query: {query_label} {query_number}"], False
    return -4.0, [f"missing queried statement reference {query_label} {query_number}"], False


def detect_concepts(text: str) -> dict[str, bool]:
    normalized = normalize_paper_query(text).casefold()
    concepts = {
        name: bool(pattern.search(normalized))
        for name, pattern in HARD_CONCEPT_PATTERNS.items()
    }
    concepts.update(
        {
            name: bool(pattern.search(normalized))
            for name, pattern in SOFT_CONCEPT_PATTERNS.items()
        }
    )
    return concepts


def required_hard_names(required: dict[str, bool]) -> list[str]:
    return [name for name in HARD_CONCEPT_PATTERNS if required.get(name)]


def required_soft_names(required: dict[str, bool]) -> list[str]:
    return [name for name in SOFT_CONCEPT_PATTERNS if required.get(name)]


def concept_aliases(query: str, extra_aliases: list[str]) -> list[str]:
    """Keep hard concepts glued together so contrast pairs survive retrieval."""

    text = " ".join([query, *extra_aliases])
    concepts = detect_concepts(text)
    hard = required_hard_names(concepts)
    soft = required_soft_names(concepts)
    aliases: list[str] = []
    labels = [CONCEPT_LABELS[name] for name in hard]
    if len(labels) >= 2:
        aliases.append(" ".join(labels[:2]))
        if len(labels) >= 3:
            aliases.append(" ".join(labels[:3]))
    if labels:
        anchor_words = [
            word
            for word in signature_terms(text, limit=10)
            if word not in " ".join(labels).casefold() and word not in GREEK_ALIAS_NOISE
        ]
        if anchor_words:
            aliases.append(" ".join([labels[0], *anchor_words[:2]]))
        if soft:
            aliases.append(f"{labels[0]} {soft[0].replace('_', ' ')}")
    return unique_keep_order(aliases)


def statement_alias_targets(query: str, extra_aliases: list[str]) -> list[str]:
    targets = concept_aliases(query, extra_aliases)
    if is_formal_statement_query(query):
        words = signature_terms(" ".join([query, *extra_aliases]), limit=10)
    else:
        words = informative_words(" ".join([query, *extra_aliases]), limit=10)
    words = [word for word in words if word not in STATEMENT_LABELS]
    if len(words) >= 4:
        targets.append(" ".join(words[:6]))
        if len(words) > 6:
            targets.append(" ".join(words[-6:]))
    elif len(words) >= 3:
        targets.append(" ".join(words[:3]))
    symbolic = compact_symbolic_tokens(" ".join([query, *extra_aliases]))
    if len(symbolic) >= 3:
        targets.append(" ".join(symbolic[:3]))
        if len(symbolic) >= 4:
            targets.append(" ".join(symbolic[:4]))
    targets.extend(keyword_cluster_aliases(query, extra_aliases))
    return unique_keep_order(targets)


def strip_generic_words(text: str, limit: int = 8) -> str:
    return " ".join(informative_words(text, limit=limit))


def build_core_aliases(query: str, extra_aliases: list[str]) -> list[str]:
    try:
        aliases = list(build_query_spec(query, extra_aliases).aliases)
    except Exception as exc:
        record_diagnostic(
            SearchDiagnostic(
                level="warning",
                stage="query_spec",
                code="query_spec_failed",
                message=compact_exception(exc),
                exception_type=type(exc).__name__,
            )
        )
        aliases = [normalize_paper_query(query), *extra_aliases]
    return [
        alias
        for alias in unique_keep_order(aliases)
        if should_keep_alias(alias) or is_reference_alias(alias)
    ][:10]


def target_labels_for_query(query: str) -> tuple[str, ...]:
    spec = cached_query_spec(query)
    if spec.query_type == "theorem_lookup":
        return tuple(statement_labels(query)) or ("theorem",)
    return QUERY_TYPE_LABELS.get(spec.query_type, ())


def build_aliases(query: str, extra_aliases: list[str], alias_mode: str = "expanded") -> list[str]:
    alias_mode = alias_mode if alias_mode in {"expanded", "core", "full", "auto"} else "expanded"
    if alias_mode == "core":
        return build_core_aliases(query, extra_aliases)
    normalized = normalize_paper_query(query)
    aliases: list[str] = build_core_aliases(query, extra_aliases)
    target_labels = target_labels_for_query(query)
    formal_statement_query = is_formal_statement_query(query)
    query_has_cjk = bool(re.search(r"[\u3400-\u9fff]", query))
    query_ref = primary_statement_ref(query)
    reference_focused_query = (
        query_ref is not None
        and len(signature_terms(query, limit=8)) <= 3
        and len(short_signal_terms(query, limit=4)) >= 2
    )

    for candidate in [query, normalized, *extra_aliases]:
        skip_raw_cjk = bool(target_labels or formal_statement_query) and query_has_cjk and candidate in {query, normalized}
        skip_long_statement_alias = (
            candidate in {query, normalized}
            and (formal_statement_query or query_ref is not None)
            and len(signature_terms(query, limit=12)) >= 5
        )
        if candidate in {query, normalized} and reference_focused_query:
            skip_long_statement_alias = True
        if not skip_raw_cjk and not skip_long_statement_alias and should_keep_alias(candidate):
            aliases.append(candidate)
        stripped = strip_generic_words(candidate)
        if (
            not skip_raw_cjk
            and not skip_long_statement_alias
            and stripped
            and should_keep_alias(stripped)
        ):
            aliases.append(stripped)

    aliases.extend(concept_aliases(query, extra_aliases))
    reference_aliases = statement_reference_aliases(query, extra_aliases)
    aliases.extend(reference_aliases)
    aliases.extend(symbolic_cluster_aliases(query, extra_aliases))
    statement_targets = statement_alias_targets(query, extra_aliases)
    if reference_focused_query:
        statement_targets = []
    aliases.extend(statement_targets)
    priority_aliases: list[str] = []
    if query_ref is not None:
        priority_aliases.extend(reference_aliases[:8])
        priority_aliases.extend(statement_targets[:2])

    structural_targets = statement_targets or ([] if reference_focused_query else [strip_generic_words(normalized)])
    for label in target_labels:
        for target in structural_targets[:4]:
            if not target:
                continue
            aliases.append(f"{label} {target}")

    filtered_aliases = [
        alias
        for alias in unique_keep_order(priority_aliases + aliases)
        if should_keep_alias(alias) or is_reference_alias(alias)
    ]
    if not filtered_aliases:
        fallback: list[str] = []
        refs = extract_statement_refs(query)
        if refs:
            label, number = refs[0]
            fallback.append(f"{label} {number}")
            split_number = number.replace(".", " ")
            if split_number != number:
                fallback.append(f"{label} {split_number}")
        words = signature_terms(query, limit=4)
        if words:
            fallback.append(" ".join(words))
        fallback.append(normalize_paper_query(query))
        filtered_aliases = [
            alias
            for alias in unique_keep_order(fallback)
            if should_keep_alias(alias) or is_reference_alias(alias)
        ]
    return filtered_aliases[:40] if alias_mode == "full" else filtered_aliases[:14]


def alias_weight(text: str) -> float:
    stripped = strip_generic_words(text)
    count = informative_word_count(stripped or text)
    lowered = normalize_paper_query(text).casefold()
    word_total = len(WORD_RE.findall(lowered))
    sig_terms = signature_terms(text, limit=6)
    short_terms = short_signal_terms(text, limit=6)
    weight = 0.6 if count <= 2 else 1.0
    if is_reference_alias(text) and word_total >= 8 and len(sig_terms) <= 3:
        weight *= 0.3
    if re.search(r"\b(?:table|figure|algorithm|equation)\s+\d+(?:\.\d+)?\b", lowered):
        if len(sig_terms) <= 2 and len(short_terms) <= 1 and count <= 3:
            weight *= 0.2
        elif len(sig_terms) <= 3 and len(short_terms) <= 1 and count <= 4:
            weight *= 0.45
    if len(sig_terms) <= 2 and not is_reference_alias(text) and not any(
        marker in lowered for marker in STATEMENT_LABELS
    ):
        weight *= 0.4
    if any(marker in lowered for marker in STATEMENT_LABELS):
        weight += 0.25
    if detect_hard_concepts(text):
        weight += 0.2
    if any(family in lowered for family in MODEL_FAMILIES) or any(
        form in lowered for forms in BENCHMARK_DATASETS.values() for form in forms
    ):
        weight += 0.15
    if FORMULA_CONTEXT_RE.search(lowered):
        weight += 0.1
    return weight


def role_heads(structured: str) -> list[tuple[int, str]]:
    """Return (position, role) for every labeled head on the page."""

    heads: list[tuple[int, str]] = []
    for pattern in (LOCAL_STATEMENT_HEAD_RE, LOCAL_ALT_STATEMENT_HEAD_RE):
        for match in pattern.finditer(structured):
            heads.append((match.start(), match.group("label")))
    for pattern, role in (
        (LOCAL_ALGORITHM_HEAD_RE, "algorithm"),
        (LOCAL_TABLE_HEAD_RE, "results_table"),
        (LOCAL_FIGURE_HEAD_RE, "figure_caption"),
    ):
        for match in pattern.finditer(structured):
            heads.append((match.start(), role))
    method_match = METHOD_HEAD_RE.search(structured)
    if method_match:
        heads.append((method_match.start(), "method"))
    equation_match = LOCAL_EQUATION_RE.search(structured)
    if equation_match:
        heads.append((equation_match.start(), "method"))
    heads.sort(key=lambda item: item[0])
    return heads


def detect_page_role(
    title: str,
    path: str,
    content: str,
    preferred_roles: frozenset[str] | set[str] = frozenset(),
) -> str:
    meta = normalize_paper_query(" ".join([title, path])).casefold()
    if re.search(r"\bsolutions?(?:\s+manual)?\b|\banswers?\b", meta):
        return "proof"
    noisy_role = noisy_page_role(title, path, content)
    if noisy_role:
        return noisy_role

    structured = normalize_structured_paper_text(content).casefold()
    head = structured[:1600]
    head_prefix = head[:240]

    heads = role_heads(structured)
    proof_match = PROOF_HEAD_RE.search(head_prefix)
    # A proof that follows its own statement on the same page does not make the
    # page a proof page; only a proof that opens the page (or precedes every
    # labeled head) does.
    if proof_match and (not heads or proof_match.start() < heads[0][0]):
        return "proof"
    if RELATED_WORK_HEAD_RE.search(head[:600]):
        return "related_work"

    if preferred_roles:
        for _position, role in heads:
            if role in preferred_roles:
                return role
    head_roles = [(position, role) for position, role in heads if position < 1600]
    if head_roles:
        statement_heads = [item for item in head_roles if item[1] in STATEMENT_ROLES]
        if statement_heads and (statement_heads[0][0] <= head_roles[0][0] or statement_heads[0][0] <= 260):
            return statement_heads[0][1]
        return head_roles[0][1]
    if ABSTRACT_HEAD_RE.search(head[:900]) or INTRO_SUMMARY_RE.search(head):
        return "abstract"
    if heads:
        return heads[0][1]
    if len(structured) >= 200:
        return "body"
    return "other"


def has_local_statement(text: str) -> bool:
    lowered = normalize_structured_paper_text(text).casefold()
    return bool(
        LOCAL_STATEMENT_HEAD_RE.search(lowered)
        or LOCAL_ALT_STATEMENT_HEAD_RE.search(lowered)
        or LOCAL_ALGORITHM_HEAD_RE.search(lowered)
        or LOCAL_TABLE_HEAD_RE.search(lowered)
        or LOCAL_FIGURE_HEAD_RE.search(lowered)
        or LOCAL_EQUATION_RE.search(lowered)
        or DEFINE_MARKER_RE.search(lowered)
    )


def concept_score(required: dict[str, bool], candidate: dict[str, bool]) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    for name in required_hard_names(required):
        label = CONCEPT_LABELS.get(name, name)
        if candidate.get(name):
            score += 8.0
            reasons.append(f"keeps {label}")
        else:
            score -= 18.0
            reasons.append(f"missing {label}")
    for name in required_soft_names(required):
        label = name.replace("_", " ")
        if candidate.get(name):
            score += 4.0
            reasons.append(f"keeps {label} setting")
        else:
            score -= 4.0
            reasons.append(f"missing {label} setting")
    return score, reasons


def concept_span_score(
    required: dict[str, bool], content: str
) -> tuple[float, list[str], bool, tuple[int, int] | None]:
    lowered = normalize_paper_query(content).casefold()
    groups = [
        concept_positions(name, lowered)
        for name in [*required_hard_names(required), *required_soft_names(required)]
    ]

    if len(groups) < 2:
        return 0.0, [], False, None

    best = min_cover_window(groups)
    if best is None:
        return 0.0, [], False, None
    span, start, end = best

    if span <= 100:
        return 18.0, [f"concepts cluster tightly (span={span})"], True, (start, end)
    if span <= 220:
        return 10.0, [f"concepts cluster reasonably (span={span})"], True, (start, end)
    if span <= 360:
        return 3.0, [f"concepts appear on the same page (span={span})"], False, (start, end)
    return -8.0, [f"concepts are too far apart (span={span})"], False, (start, end)


def has_direct_statement(
    search_spec: str,
    spec: QuerySpec,
    required: dict[str, bool],
    page_role: str,
    text: str,
    *,
    anchors_ok: bool,
) -> bool:
    if spec.exact_anchors and not anchors_ok:
        return False
    hard_needed = required_hard_names(required)
    candidate = detect_concepts(text)
    if not all(candidate.get(name) for name in hard_needed):
        return False
    _, _, strong_overlap = statement_overlap(search_spec, text)
    if strong_overlap:
        return True
    matched_terms = len(matched_signature_terms(search_spec, text, limit=14))
    if len(hard_needed) >= 2:
        _, _, compact_span, _ = concept_span_score(required, text)
        if compact_span and matched_terms >= 3:
            return True
    if spec.exact_anchors and anchors_ok and matched_terms >= 2:
        return True
    if page_role in RESULT_ROLES | FIGURE_ROLES and spec.statement_ref and matched_terms >= 2:
        return True
    return False


def classify_hit(
    *,
    required: dict[str, bool],
    candidate: dict[str, bool],
    spec: QuerySpec,
    page_role: str,
    compact_span: bool,
    local_statement: bool,
    direct_statement: bool,
    anchors_ok: bool,
    overview_reference: bool,
) -> str:
    hard_needed = required_hard_names(required)
    keeps_all_hard = all(candidate.get(name) for name in hard_needed)
    soft_missing = sum(1 for name in required_soft_names(required) if not candidate.get(name))
    role_ok = not spec.required_page_roles or page_role in spec.required_page_roles
    abstract_blocked = page_role == "abstract" and "abstract" not in spec.required_page_roles
    never_exact = page_role in {"proof", "related_work", "references", "contents", "other", "unknown"}
    span_ok = compact_span or len(hard_needed) < 2

    if (
        keeps_all_hard
        and anchors_ok
        and role_ok
        and not never_exact
        and not abstract_blocked
        and not overview_reference
        and local_statement
        and direct_statement
        and soft_missing == 0
        and span_ok
    ):
        return "exact hit"
    if page_role in {"related_work", "references", "contents"} or overview_reference:
        return "nearby material"
    if keeps_all_hard and direct_statement and soft_missing <= 1 and (role_ok or local_statement):
        return "near-exact"
    return "nearby material"


def rerank_bonus(
    search_spec: str, title: str, path: str, snippet: str, content: str
) -> tuple[float, list[str], str, PageEvidence]:
    spec = cached_query_spec(search_spec)
    target_roles = spec.required_page_roles
    page_role = detect_page_role(title, path, content, preferred_roles=target_roles)
    text = candidate_text(title, path, snippet, content)
    normalized_content = normalize_paper_query(content).casefold()
    page_head = normalize_structured_paper_text(content)[:900]
    required = detect_concepts(search_spec)
    candidate = detect_concepts(text)
    query_ref = primary_statement_ref(search_spec)
    noisy_page = has_noisy_marker(title, path, content)

    score = 0.0
    reasons: list[str] = []

    if spec.query_type != "unknown":
        source_delta, source_reasons = original_source_score(title, path)
        score += source_delta
        reasons.extend(source_reasons)

    concept_delta, concept_reasons = concept_score(required, candidate)
    score += concept_delta
    reasons.extend(concept_reasons)

    span_delta, span_reasons, compact_span, span_window = concept_span_score(required, content)
    score += span_delta
    reasons.extend(span_reasons)

    query_ref_text, query_ref_start = query_statement_match(content, page_role, query_ref)
    if query_ref_text is not None:
        local_text = query_ref_text
    elif page_role in LOCAL_WINDOW_ROLES:
        local_text = query_relevant_statement_window(
            search_spec, content, page_role, fallback_chars=1400
        )
    elif span_window:
        local_text = local_window(normalized_content, span_window[0], span_window[1], before=120, after=520)
    else:
        local_text = normalized_content[:420]
    local_statement = has_local_statement(local_text)
    citation_mention = has_citation_mention(local_text)
    overview_reference = (
        has_overview_reference(local_text) or citation_mention or page_role == "related_work"
    )
    overlap_delta, overlap_reasons, strong_overlap = statement_overlap(search_spec, local_text)
    phrase_delta, phrase_reasons, strong_phrase = content_phrase_score(search_spec, local_text)
    query_signature_count = len(signature_terms(search_spec, limit=10))
    matched_term_count = len(matched_signature_terms(search_spec, local_text, limit=14))
    score += overlap_delta
    reasons.extend(overlap_reasons)
    score += phrase_delta
    reasons.extend(phrase_reasons)
    if strong_overlap and query_signature_count >= 5:
        score += 16.0
        reasons.append("strong overlap with the queried statement")
    if strong_phrase:
        score += 8.0
        reasons.append("local statement keeps a long content phrase")

    anchors_found = matching_exact_anchors(spec.exact_anchors, local_text)
    anchors_missing = tuple(anchor for anchor in spec.exact_anchors if anchor not in anchors_found)
    anchors_ok = not anchors_missing
    if spec.exact_anchors and anchors_ok:
        score += 16.0
        reasons.append("keeps exact structural anchor(s)")
    elif spec.exact_anchors:
        score -= 14.0
        reasons.append("missing structural anchor(s): " + ", ".join(anchors_missing[:3]))

    if spec.query_type in {"result_table_lookup", "figure_lookup", "claim_lookup"}:
        numeric_delta, numeric_reasons = numeric_anchor_score(search_spec, local_text)
        score += numeric_delta
        reasons.extend(numeric_reasons)

    ref_delta, ref_reasons, exact_statement_ref = statement_reference_score(
        search_spec, page_role, local_text
    )
    score += ref_delta
    reasons.extend(ref_reasons)
    if exact_statement_ref and strong_overlap and query_signature_count >= 4:
        score += 14.0
        reasons.append("strong overlap with the numbered target")
    if (
        exact_statement_ref
        and query_signature_count >= 3
        and matched_term_count == query_signature_count
    ):
        score += 18.0
        reasons.append("numbered target matches all queried content terms")
    if (
        exact_statement_ref
        and query_signature_count >= 4
        and matched_term_count <= max(1, query_signature_count // 3)
        and not strong_phrase
    ):
        score -= 32.0
        reasons.append("number match without enough content overlap")
    if page_role == "proof" and exact_statement_ref:
        score -= 18.0
        reasons.append("proof page restates the target statement")
    secondary_ref_delta, secondary_ref_reasons = secondary_reference_score(search_spec, local_text)
    score += secondary_ref_delta
    reasons.extend(secondary_ref_reasons)
    if exact_statement_ref and query_ref_start is not None:
        if query_ref_start <= 40:
            score += 14.0
            reasons.append("statement starts at page head")
        elif query_ref_start <= 180:
            score += 10.0
            reasons.append("statement appears near page head")
        elif query_ref_start <= 600:
            score += 4.0
            reasons.append("statement appears in the early page body")
    if exact_statement_ref:
        head_phrase_delta, head_phrase_reasons, head_strong_phrase = content_phrase_score(
            search_spec, page_head
        )
        if head_phrase_delta > 0.0:
            score += min(12.0, 0.75 * head_phrase_delta)
            if head_strong_phrase:
                reasons.append("page head keeps a phrase around the exact statement reference")
            else:
                reasons.extend(head_phrase_reasons)

    direct_statement = has_direct_statement(
        search_spec, spec, required, page_role, local_text, anchors_ok=anchors_ok
    )
    proof_ingredient = (
        not direct_statement
        and page_role not in target_roles
        and has_usage_mention(local_text)
        and any(candidate.get(name) for name in required_hard_names(required))
    )

    if direct_statement and not overview_reference:
        score += 18.0
        reasons.append("contains the queried statement directly")
    if proof_ingredient:
        score -= 10.0
        reasons.append("uses the target as a step or baseline, not as the stated result")

    if target_roles and page_role in target_roles:
        score += 12.0
        reasons.append(f"matching {page_role.replace('_', ' ')} page")
    elif page_role == "proof":
        score -= 8.0
        reasons.append("proof/appendix page, not the original statement")
    elif page_role == "related_work":
        score -= 12.0
        reasons.append("related-work mention, not the original statement")
    elif page_role == "abstract":
        score -= 6.0
        reasons.append("abstract/introduction summary, not the original statement")
    elif target_roles:
        score -= 8.0
        reasons.append("not a " + "/".join(sorted(role.replace("_", " ") for role in target_roles)) + " page")

    if local_statement and not overview_reference:
        reasons.append(f"local {ROLE_DESCRIPTIONS.get(page_role, 'labeled statement')}")
        score += 12.0
    elif overview_reference:
        score -= 12.0
        if citation_mention:
            reasons.append("cited or attributed mention, not the original statement")
        else:
            reasons.append("overview/range reference, not a local statement")

    if noisy_page:
        score -= 20.0
        reasons.append("references/contents-like page")

    classification = classify_hit(
        required=required,
        candidate=candidate,
        spec=spec,
        page_role=page_role,
        compact_span=compact_span,
        local_statement=local_statement,
        direct_statement=direct_statement and (exact_statement_ref or query_ref is None),
        anchors_ok=anchors_ok,
        overview_reference=overview_reference,
    )
    evidence = build_page_evidence(
        page_role=page_role,
        local_statement=local_statement,
        direct_statement=direct_statement and (exact_statement_ref or query_ref is None),
        hard_concepts_required=set(required_hard_names(required)),
        evidence_text=local_text,
        statement_window=local_text,
        overview_reference=overview_reference,
        hint_solution=page_role == "proof",
        proof_ingredient=proof_ingredient,
        noisy_page=noisy_page,
    )
    return score, reasons, classification, evidence


def _anchor_needles(spec: QuerySpec) -> tuple[str, ...]:
    needles: list[str] = []
    for anchor in spec.exact_anchors:
        family, _, value = anchor.partition(":")
        if family == "complexity":
            needles.extend(["o(", "θ(", "ω(", "theta", "omega"])
        elif family == "attention":
            needles.append("softmax")
        elif family == "model":
            needles.append(value.partition("-")[0])
        elif family == "benchmark":
            dataset = value.partition(":")[0]
            needles.extend(BENCHMARK_DATASETS.get(dataset, (dataset,)))
        elif family == "number":
            needles.append(value)
    return tuple(dict.fromkeys(needles))


def _anchor_statement_window(content: str, spec: QuerySpec) -> str:
    lowered = content.casefold()
    starts = [lowered.find(needle) for needle in _anchor_needles(spec) if lowered.find(needle) >= 0]
    if not starts:
        return content[:1400]
    start = min(starts)
    return content[max(0, start - 320) : start + 1080]


def anchor_rescue_hits(
    *,
    db_path: Path,
    query: str,
    spec: QuerySpec,
    limit: int = 96,
) -> list[PageHit]:
    """Use exact structural anchors to recover a bounded set beyond normal rank."""

    safe_limit = min(96, max(12, int(limit)))
    if not spec.exact_anchors or not spec.exact_anchor_queries:
        return []
    try:
        con = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
    except Exception as exc:
        record_diagnostic(
            SearchDiagnostic(
                level="warning",
                stage="anchor_rescue",
                code="db_connect_failed",
                message=compact_exception(exc),
                db=str(db_path),
                exception_type=type(exc).__name__,
            )
        )
        return []

    rows: dict[tuple[str, int], AliasResult] = {}
    try:
        for alias in spec.exact_anchor_queries[:3]:
            try:
                for item in search_alias_in_db(con, alias, safe_limit):
                    rows.setdefault((item.path, item.page_number), item)
                    if len(rows) >= safe_limit:
                        break
            except Exception as exc:
                record_diagnostic(
                    SearchDiagnostic(
                        level="warning",
                        stage="anchor_rescue",
                        code="anchor_query_failed",
                        message=compact_exception(exc),
                        db=str(db_path),
                        alias=alias,
                        exception_type=type(exc).__name__,
                    )
                )
            if len(rows) >= safe_limit:
                break
    finally:
        con.close()

    hits: list[PageHit] = []
    for item in rows.values():
        statement_window = _anchor_statement_window(item.content, spec)
        anchor_found = matching_exact_anchors(spec.exact_anchors, statement_window)
        if len(anchor_found) != len(spec.exact_anchors):
            continue
        hard_found = detect_hard_concepts(item.content)
        hard_missing = tuple(sorted(set(spec.hard_concepts) - hard_found))
        delta, reasons, classification, evidence = rerank_bonus(
            search_spec=query,
            title=item.title,
            path=item.path,
            snippet=item.snippet,
            content=item.content,
        )
        source_delta, source_reasons = original_source_score(
            item.title, item.path
        )
        negative_page = any(
            (
                evidence.overview_reference,
                evidence.hint_solution,
                evidence.proof_ingredient,
                evidence.noisy_page,
            )
        )
        roles_match = (
            not spec.required_page_roles
            or evidence.page_role in spec.required_page_roles
        )
        strict_anchor_hit = bool(
            evidence.local_statement
            and not hard_missing
            and roles_match
            and not negative_page
            and evidence.page_role not in {"proof", "related_work", "references", "contents", "other", "unknown"}
        )
        if strict_anchor_hit:
            classification = "exact hit"
            reasons = [
                "exact structural anchor",
                *source_reasons,
                *reasons,
            ]
        final_score = (
            200.0
            + float(delta)
            + (2.0 * source_delta)
            + (60.0 if strict_anchor_hit else 0.0)
        )
        hits.append(
            PageHit(
                final_score=final_score,
                best_score=100.0 / (1.0 + item.bm25_score),
                hit_count=1,
                alias_count=1,
                aliases=list(spec.exact_anchor_queries),
                db=str(Path(db_path).resolve()),
                title=item.title,
                path=item.path,
                page_number=item.page_number,
                snippet=item.snippet,
                classification=classification,
                reasons=unique_keep_order(reasons),
                features={
                    **evidence.to_features(classification=classification),
                    "local_statement": bool(evidence.local_statement),
                    "direct_statement": strict_anchor_hit,
                    "hard_concepts_required": sorted(spec.hard_concepts),
                    "hard_concepts_found": sorted(
                        set(spec.hard_concepts) & hard_found
                    ),
                    "hard_concepts_missing": list(hard_missing),
                    "exact_anchors_required": list(spec.exact_anchors),
                    "exact_anchors_found": list(anchor_found),
                    "exact_anchors_missing": [],
                    "anchor_rescue": True,
                },
                score_breakdown={
                    "anchor_rescue": 200.0,
                    "page_feature": float(delta),
                    "source_role": 2.0 * source_delta,
                    "strict_anchor": 60.0 if strict_anchor_hit else 0.0,
                },
                statement_window=statement_window,
            )
        )
    hits.sort(
        key=lambda hit: (
            hit.classification != "exact hit",
            -hit.final_score,
            hit.title.casefold(),
            hit.page_number,
        )
    )
    return hits[:safe_limit]


def signature_rescue_hits(
    *,
    db_path: Path,
    query: str,
    spec: QuerySpec,
    limit: int = 96,
) -> list[PageHit]:
    """Recover pages with high local signature coverage beyond normal top-N."""

    safe_limit = min(96, max(12, int(limit)))
    if not spec.signature_rescue_queries:
        return []
    try:
        con = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
    except Exception as exc:
        record_diagnostic(
            SearchDiagnostic(
                level="warning",
                stage="signature_rescue",
                code="db_connect_failed",
                message=compact_exception(exc),
                db=str(db_path),
                exception_type=type(exc).__name__,
            )
        )
        return []

    rows: dict[tuple[str, int], AliasResult] = {}
    per_query_limit = max(24, safe_limit // len(spec.signature_rescue_queries))
    try:
        for alias in spec.signature_rescue_queries:
            try:
                for item in search_alias_in_db(con, alias, per_query_limit):
                    rows.setdefault((item.path, item.page_number), item)
            except Exception as exc:
                record_diagnostic(
                    SearchDiagnostic(
                        level="warning",
                        stage="signature_rescue",
                        code="signature_query_failed",
                        message=compact_exception(exc),
                        db=str(db_path),
                        alias=alias,
                        exception_type=type(exc).__name__,
                    )
                )
    finally:
        con.close()

    required_hard = set(spec.hard_concepts)
    hits: list[PageHit] = []
    for item in rows.values():
        delta, reasons, classification, evidence = rerank_bonus(
            search_spec=query,
            title=item.title,
            path=item.path,
            snippet=item.snippet,
            content=item.content,
        )
        statement_window = str(evidence.statement_window or item.content)
        hard_found = detect_hard_concepts(statement_window)
        hard_missing = tuple(sorted(required_hard - hard_found))
        matched_terms = matched_signature_terms(query, statement_window, limit=18)
        roles_match = (
            not spec.required_page_roles
            or evidence.page_role in spec.required_page_roles
        )
        source_delta, source_reasons = original_source_score(
            item.title, item.path
        )
        rescue_score = (
            float(delta)
            + source_delta
            + 10.0 * len(matched_terms)
            + (28.0 if evidence.local_statement else 0.0)
            + (24.0 if roles_match else 0.0)
            - 36.0 * len(hard_missing)
        )
        hits.append(
            PageHit(
                final_score=rescue_score,
                best_score=100.0 / (1.0 + item.bm25_score),
                hit_count=1,
                alias_count=1,
                aliases=list(spec.signature_rescue_queries),
                db=str(Path(db_path).resolve()),
                title=item.title,
                path=item.path,
                page_number=item.page_number,
                snippet=item.snippet,
                classification=classification,
                reasons=unique_keep_order(
                    [
                        f"matches {len(matched_terms)} signature terms",
                        *source_reasons,
                        *reasons,
                    ]
                ),
                features={
                    **evidence.to_features(classification=classification),
                    "hard_concepts_required": sorted(required_hard),
                    "hard_concepts_found": sorted(required_hard & hard_found),
                    "hard_concepts_missing": list(hard_missing),
                    "signature_terms_found": matched_terms,
                    "signature_rescue": True,
                },
                score_breakdown={
                    "signature_rescue": rescue_score,
                    "page_feature": float(delta),
                    "source_role": source_delta,
                    "signature_coverage": 10.0 * len(matched_terms),
                },
                statement_window=statement_window,
            )
        )
    hits.sort(
        key=lambda hit: (
            len(hit.features.get("hard_concepts_missing", [])),
            hit.features.get("page_role") not in spec.required_page_roles
            if spec.required_page_roles
            else False,
            not bool(hit.features.get("local_statement")),
            -len(hit.features.get("signature_terms_found", [])),
            -hit.final_score,
            hit.title.casefold(),
            hit.page_number,
        )
    )
    return hits[:safe_limit]


def aggregate_hits(
    db_paths: list[Path], aliases: list[str], limit: int, primary_query: str
) -> list[PageHit]:
    aggregated: dict[tuple[str, str, int], dict[str, object]] = {}
    per_query_limit = max(limit * 3, 24)
    survey_aliases = survey_alias_candidates(primary_query, aliases)

    for db_path in db_paths:
        if not db_path.exists():
            record_diagnostic(
                SearchDiagnostic(
                    level="error",
                    stage="db_validate",
                    code="missing_db",
                    message="SQLite PDF database does not exist.",
                    db=str(db_path),
                )
            )
            continue
        try:
            con = sqlite3.connect(db_path)
            con.row_factory = sqlite3.Row
        except Exception as exc:
            record_diagnostic(
                SearchDiagnostic(
                    level="error",
                    stage="db_connect",
                    code="schema_error",
                    message=compact_exception(exc),
                    db=str(db_path),
                    exception_type=type(exc).__name__,
                )
            )
            continue
        try:
            try:
                combined_survey_context = merge_survey_contexts(
                    [build_survey_context(con, alias) for alias in survey_aliases]
                )
            except Exception as exc:
                record_diagnostic(
                    SearchDiagnostic(
                        level="error",
                        stage="survey_context",
                        code="query_exception",
                        message=compact_exception(exc),
                        db=str(db_path),
                        exception_type=type(exc).__name__,
                    )
                )
                continue
            for alias in aliases:
                try:
                    results = search_alias_in_db(
                        con,
                        alias=alias,
                        limit=per_query_limit,
                        allowed_doc_ids=combined_survey_context.allowed_doc_ids or None,
                    )
                    if combined_survey_context.allowed_doc_ids and len(results) < max(10, limit):
                        fallback = search_alias_in_db(
                            con,
                            alias=alias,
                            limit=per_query_limit,
                        )
                        results = merge_alias_results(results, fallback, per_query_limit)
                except Exception as exc:
                    record_diagnostic(
                        SearchDiagnostic(
                            level="error",
                            stage="sqlite_fts_query",
                            code="alias_query_failed",
                            message=compact_exception(exc),
                            db=str(db_path),
                            alias=alias,
                            exception_type=type(exc).__name__,
                        )
                    )
                    continue
                for item in results:
                    key = (str(db_path), item.path, item.page_number)
                    effective_score = (100.0 / (1.0 + item.bm25_score)) * alias_weight(alias)
                    survey_bonus, survey_reasons = survey_page_bonus(
                        item.doc_id,
                        item.page_number,
                        combined_survey_context,
                    )
                    bucket = aggregated.setdefault(
                        key,
                        {
                            "best_score": 0.0,
                            "score_sum": 0.0,
                            "survey_bonus": 0.0,
                            "survey_reasons": [],
                            "hit_count": 0,
                            "aliases": [],
                            "db": str(db_path),
                            "doc_id": item.doc_id,
                            "title": item.title,
                            "path": item.path,
                            "page_number": item.page_number,
                            "snippet": item.snippet,
                            "content": item.content,
                        },
                    )
                    bucket["best_score"] = max(float(bucket["best_score"]), effective_score)
                    bucket["score_sum"] = float(bucket["score_sum"]) + effective_score
                    bucket["survey_bonus"] = max(float(bucket["survey_bonus"]), survey_bonus)
                    bucket["hit_count"] = int(bucket["hit_count"]) + 1
                    alias_list = bucket["aliases"]
                    if alias not in alias_list:
                        alias_list.append(alias)
                    survey_reason_list = bucket["survey_reasons"]
                    for reason in survey_reasons:
                        if reason not in survey_reason_list:
                            survey_reason_list.append(reason)
        finally:
            con.close()

    hits: list[PageHit] = []
    for bucket in aggregated.values():
        alias_list = list(bucket["aliases"])
        best_score = float(bucket["best_score"])
        score_sum = float(bucket["score_sum"])
        survey_bonus = float(bucket["survey_bonus"])
        alias_count = len(alias_list)
        hit_count = int(bucket["hit_count"])
        final_score = best_score + min(24.0, 5.0 * max(0, alias_count - 1)) + min(
            12.0, 0.18 * max(0.0, score_sum - best_score)
        )

        rerank_delta, reasons, classification, evidence = rerank_bonus(
            search_spec=primary_query,
            title=str(bucket["title"]),
            path=str(bucket["path"]),
            snippet=str(bucket["snippet"]),
            content=str(bucket["content"]),
        )
        classification_bonus = {
            "exact hit": 36.0,
            "near-exact": 18.0,
        }.get(classification, 0.0)
        final_score += survey_bonus + rerank_delta + classification_bonus
        combined_reasons = unique_keep_order(
            [*list(bucket["survey_reasons"]), *reasons]
        )

        hits.append(
            PageHit(
                final_score=final_score,
                best_score=best_score,
                hit_count=hit_count,
                alias_count=alias_count,
                aliases=alias_list,
                db=str(bucket["db"]),
                title=str(bucket["title"]),
                path=str(bucket["path"]),
                page_number=int(bucket["page_number"]),
                snippet=str(bucket["snippet"]),
                classification=classification,
                reasons=combined_reasons,
                features={
                    **evidence.to_features(classification=classification),
                    "alias_count": alias_count,
                    "hit_count": hit_count,
                },
                score_breakdown=build_score_breakdown(
                    bm25_component=best_score,
                    alias_component=min(24.0, 5.0 * max(0, alias_count - 1)),
                    bm25_sum_component=min(12.0, 0.18 * max(0.0, score_sum - best_score)),
                    survey_component=survey_bonus,
                    page_feature_component=rerank_delta,
                    classification_component=classification_bonus,
                ),
                statement_window=evidence.statement_window or str(bucket["snippet"]),
            )
        )

    hits.sort(
        key=lambda item: (
            -item.final_score,
            item.classification != "exact hit",
            item.classification != "near-exact",
            -item.alias_count,
            -item.best_score,
            item.title.casefold(),
            item.page_number,
        )
    )
    return hits[:limit]


@guarded_main
def main() -> int:
    configure_streams()
    reset_diagnostics()
    args = parse_args()
    data_root = Path(args.data_root).expanduser().resolve()
    db_paths, db_diagnostics = discover_dbs_with_diagnostics(args.db, data_root)
    for diagnostic in db_diagnostics:
        record_diagnostic(diagnostic)
    alias_mode = str(args.alias_mode)
    aliases = build_aliases(
        args.query,
        args.alias,
        "core" if alias_mode == "auto" else alias_mode,
    )

    if not db_paths:
        diagnostics = get_diagnostics()
        if args.compact:
            payload = compact_payload(
                query=args.query,
                aliases=aliases,
                db_paths=db_paths,
                hits=[],
                result_limit=args.compact_limit,
                evidence_chars=args.evidence_chars,
                why_limit=args.why_limit,
                diagnostics=diagnostics,
            )
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 1
        if args.json:
            payload = {
                "query": args.query,
                "aliases": aliases,
                "dbs": [],
                "results": [],
                "diagnostics": diagnostics_to_dicts(diagnostics),
                "diagnostics_summary": summarize_diagnostics(diagnostics),
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 1
        raise SystemExit(
            "No PDF SQLite databases found. Build one with "
            "`python -m obsidian_local_kb pdf-index ...` first."
        )

    hits = aggregate_hits(
        db_paths=db_paths,
        aliases=aliases,
        limit=args.limit,
        primary_query=args.query,
    )
    if alias_mode == "auto" and (not hits or all(hit.classification == "nearby material" for hit in hits[:3])):
        expanded_aliases = build_aliases(args.query, args.alias, "expanded")
        if expanded_aliases != aliases:
            aliases = expanded_aliases
            hits = aggregate_hits(
                db_paths=db_paths,
                aliases=aliases,
                limit=args.limit,
                primary_query=args.query,
            )
    diagnostics = get_diagnostics()
    verify_window = None
    if args.verify_rank > 0 and args.verify_rank <= len(hits):
        hit = hits[args.verify_rank - 1]
        verify_window = {
            "rank": args.verify_rank,
            "title": hit.title,
            "path": hit.path,
            "page_number": hit.page_number,
            "statement_window": hit.statement_window or hit.snippet,
            "evidence": hit.features,
        }

    if args.compact:
        payload = compact_payload(
            query=args.query,
            aliases=aliases,
            db_paths=db_paths,
            hits=hits,
            result_limit=args.compact_limit,
            evidence_chars=args.evidence_chars,
            why_limit=args.why_limit,
            diagnostics=diagnostics,
        )
        if verify_window:
            payload["verify_window"] = verify_window
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.json:
        payload = {
            "query": args.query,
            "aliases": aliases,
            "dbs": [str(path) for path in db_paths],
            "results": [
                {
                    "final_score": round(item.final_score, 3),
                    "best_score": round(item.best_score, 3),
                    "hit_count": item.hit_count,
                    "alias_count": item.alias_count,
                    "aliases": item.aliases,
                    "db": item.db,
                    "title": item.title,
                    "path": item.path,
                    "page_number": item.page_number,
                    "snippet": item.snippet,
                    "classification": item.classification,
                    "reasons": item.reasons,
                    "features": item.features,
                    "score_breakdown": item.score_breakdown,
                    "statement_window": item.statement_window,
                }
                for item in hits
            ],
            "diagnostics": diagnostics_to_dicts(diagnostics),
            "diagnostics_summary": summarize_diagnostics(diagnostics),
        }
        if verify_window:
            payload["verify_window"] = verify_window
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"Query: {args.query}")
    print(f"Aliases: {', '.join(aliases)}")
    print(f"Databases: {len(db_paths)}")
    if diagnostics:
        print(f"Diagnostics: {summarize_diagnostics(diagnostics)}")
    print()
    for idx, item in enumerate(hits, start=1):
        print(
            f"{idx}. [{item.final_score:.2f}] {item.title} :: page {item.page_number}"
        )
        print(f"   db: {item.db}")
        print(f"   path: {item.path}")
        print(f"   classification: {item.classification}")
        print(
            f"   why: alias_hits={item.alias_count}, total_hits={item.hit_count}, "
            f"best_score={item.best_score:.2f}"
        )
        print(f"   aliases: {', '.join(item.aliases)}")
        print(f"   reasons: {', '.join(item.reasons)}")
        print(f"   snippet: {item.snippet}")
        print()
    if verify_window:
        print("Verify window:")
        print(json.dumps(verify_window, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
