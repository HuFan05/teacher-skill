from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .aliases import (
    detect_hard_concepts,
    detect_soft_concepts,
    exact_anchor_queries,
    signature_rescue_queries,
    signature_terms,
    source_location_aliases,
    unique_keep_order,
)
from .normalization import exact_anchor_fingerprints, normalize_paper_query
from .page_roles import (
    ALGORITHM_ROLES,
    CLAIM_ROLES,
    DEFINITION_ROLES,
    EQUATION_ROLES,
    FIGURE_ROLES,
    METHOD_ROLES,
    RESULT_ROLES,
    THEOREM_ROLES,
)
from .types import SearchDiagnostic


QueryType = Literal[
    "algorithm_lookup",
    "theorem_lookup",
    "definition_lookup",
    "equation_lookup",
    "result_table_lookup",
    "figure_lookup",
    "claim_lookup",
    "named_method_lookup",
    "source_location_lookup",
    "unknown",
]

STATEMENT_REF_RE = re.compile(
    r"\b(?:algorithm|alg|table|tab|figure|fig|theorem|thm|lemma|lem|proposition|prop|"
    r"corollary|cor|definition|def|equation|eq)\.?\s*\(?\s*\d{1,3}(?:\.\d{1,3})*[a-z]?\)?"
)
RESULT_WORD_RE = re.compile(
    r"\b(?:accuracy|bleu|f1|top-?1|top-?5|perplexity|benchmark|results?|score|sota|"
    r"state[\s-]of[\s-]the[\s-]art)\b"
)
RESULT_TABLE_PHRASE_RE = re.compile(
    r"\b(?:results?\s+table|table\s+of\s+results|benchmark\s+results?|ablation\s+(?:study|table))\b"
)
EQUATION_WORD_RE = re.compile(
    r"\b(?:loss|objective|equation|eq\.|update rule|formula|softmax|argmax|argmin|gradient of)\b"
)
NAMED_METHOD_WORD_RE = re.compile(
    r"\b(?:architecture|layer|block|module|mechanism|optimizer|encoder|decoder|network|model|method)\b"
)
THEORY_CONCEPTS = frozenset(
    {
        "upper_bound",
        "lower_bound",
        "worst_case",
        "average_case",
        "amortized",
        "time_complexity",
        "space_complexity",
        "np_hard",
        "np_complete",
        "approximation_algorithm",
        "polynomial_time",
        "regret",
        "convex",
        "nonconvex",
    }
)
FORMAL_MARKER_RE = re.compile(
    r"\b(?:every|each|any|all|if|then|there exists|there is|is|are|has|have|"
    r"requires?|needs?|runs? in|takes?|admits?|converges?)\b"
)
CLAIM_WORD_RE = re.compile(
    r"\b(?:we show|we prove|we find|we demonstrate|we observe|outperforms?|improves?|"
    r"achieves?|claims?|converges?|reduces?)\b"
)


def formula_symbol_count(text: str) -> int:
    return len(re.findall(r"[=^_\\∑∇√]|softmax|argmax|argmin", text))


def detect_query_type(
    text: str,
    normalized: str,
    hard_concepts: set[str],
    symbolic_terms: tuple[str, ...],
    exact_anchors: tuple[str, ...] = (),
) -> QueryType:
    lowered = normalized.casefold()
    anchor_families = {anchor.split(":", 1)[0] for anchor in exact_anchors}
    if re.search(r"\b(?:algorithm|alg)\.?\s*\d", lowered) or re.search(
        r"\b(?:pseudocode|pseudo-code)\b", lowered
    ):
        return "algorithm_lookup"
    if re.search(r"\b(?:table|tab)\.?\s*\d", lowered):
        return "result_table_lookup"
    if re.search(r"\b(?:figure|fig)\.?\s*\d", lowered):
        return "figure_lookup"
    if re.search(r"\b(?:theorem|lemma|proposition|corollary)\b", lowered):
        return "theorem_lookup"
    if re.search(r"\b(?:definition|we define|is defined as)\b", lowered):
        return "definition_lookup"
    theory_statement = bool(hard_concepts & THEORY_CONCEPTS) or "complexity" in anchor_families
    if theory_statement and (
        re.search(r"\b(?:prove|proof|show that)\b", lowered) or FORMAL_MARKER_RE.search(lowered)
    ):
        return "theorem_lookup"
    if (
        {"benchmark", "number"} & anchor_families
        or RESULT_TABLE_PHRASE_RE.search(lowered)
        or (RESULT_WORD_RE.search(lowered) and re.search(r"\d", lowered))
    ):
        return "result_table_lookup"
    if "attention" in anchor_families or EQUATION_WORD_RE.search(lowered) or formula_symbol_count(text) >= 3:
        return "equation_lookup"
    if re.search(r"\balgorithm\b", lowered):
        return "algorithm_lookup"
    if "model" in anchor_families or (
        NAMED_METHOD_WORD_RE.search(lowered) and (hard_concepts or detect_soft_concepts(text))
    ):
        return "named_method_lookup"
    if CLAIM_WORD_RE.search(lowered):
        return "claim_lookup"
    if re.search(r"\b(?:where|which|source|page|locate)\b", lowered):
        return "source_location_lookup"
    if len(hard_concepts) >= 2 and (
        re.search(r"\b(?:every|each|any|all|if|then|is|are|has|there exists|there is)\b", lowered)
        or "=>" in lowered
        or "implies" in lowered
    ):
        return "claim_lookup"
    return "unknown"


def detect_statement_ref(normalized: str) -> str | None:
    match = STATEMENT_REF_RE.search(normalized.casefold())
    return match.group(0) if match else None


def default_required_page_roles(query_type: QueryType) -> frozenset[str]:
    return {
        "algorithm_lookup": ALGORITHM_ROLES,
        "theorem_lookup": THEOREM_ROLES,
        "definition_lookup": DEFINITION_ROLES,
        "equation_lookup": EQUATION_ROLES,
        "result_table_lookup": RESULT_ROLES,
        "figure_lookup": FIGURE_ROLES,
        "claim_lookup": CLAIM_ROLES,
        "named_method_lookup": METHOD_ROLES,
    }.get(query_type, frozenset())


@dataclass(slots=True, frozen=True)
class QuerySpec:
    raw: str
    extra_aliases: tuple[str, ...]
    normalized: str
    query_type: QueryType
    statement_ref: str | None
    hard_concepts: frozenset[str]
    soft_concepts: frozenset[str]
    signature_terms: tuple[str, ...]
    exact_anchors: tuple[str, ...]
    exact_anchor_queries: tuple[str, ...]
    signature_rescue_queries: tuple[str, ...]
    aliases: tuple[str, ...]
    required_page_roles: frozenset[str]
    diagnostics: tuple[SearchDiagnostic, ...] = ()


def build_query_spec(query: str, extra_aliases: list[str] | tuple[str, ...] = ()) -> QuerySpec:
    normalized = normalize_paper_query(query)
    extras = tuple(str(item) for item in extra_aliases)
    hard = detect_hard_concepts(" ".join([query, normalized]))
    soft = detect_soft_concepts(" ".join([query, normalized]))
    symbolic = signature_terms([query, normalized, *extras], limit=18)
    exact_anchors = exact_anchor_fingerprints(query)
    query_type = detect_query_type(query, normalized, hard, symbolic, exact_anchors)
    anchor_queries = exact_anchor_queries(query)
    rescue_queries = signature_rescue_queries(query)
    aliases = unique_keep_order(
        [
            query,
            normalized,
            *source_location_aliases(query),
            *anchor_queries,
            *extras,
        ]
    )
    return QuerySpec(
        raw=query,
        extra_aliases=extras,
        normalized=normalized,
        query_type=query_type,
        statement_ref=detect_statement_ref(normalized),
        hard_concepts=frozenset(hard),
        soft_concepts=frozenset(soft),
        signature_terms=symbolic,
        exact_anchors=exact_anchors,
        exact_anchor_queries=anchor_queries,
        signature_rescue_queries=rescue_queries,
        aliases=tuple(aliases[:10]),
        required_page_roles=default_required_page_roles(query_type),
    )
