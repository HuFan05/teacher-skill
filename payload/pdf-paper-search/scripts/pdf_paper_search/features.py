from __future__ import annotations

from .aliases import detect_hard_concepts
from .types import HitFeatures, PageEvidence


def minimal_features(**kwargs: object) -> dict[str, object]:
    fields = HitFeatures.__dataclass_fields__
    features = HitFeatures(**{key: value for key, value in kwargs.items() if key in fields})
    return features.to_dict()


def concept_coverage(required: set[str] | frozenset[str] | tuple[str, ...], text: str) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    required_tuple = tuple(sorted(str(item) for item in required if str(item).strip()))
    found_set = detect_hard_concepts(text)
    found = tuple(item for item in required_tuple if item in found_set)
    missing = tuple(item for item in required_tuple if item not in found_set)
    return required_tuple, found, missing


def build_page_evidence(
    *,
    page_role: str,
    local_statement: bool,
    direct_statement: bool,
    hard_concepts_required: set[str] | frozenset[str] | tuple[str, ...],
    evidence_text: str,
    statement_window: str,
    overview_reference: bool = False,
    hint_solution: bool = False,
    proof_ingredient: bool = False,
    noisy_page: bool = False,
) -> PageEvidence:
    required, found, missing = concept_coverage(hard_concepts_required, evidence_text)
    return PageEvidence(
        page_role=page_role or "unknown",
        local_statement=local_statement,
        direct_statement=direct_statement,
        hard_concepts_required=required,
        hard_concepts_found=found,
        hard_concepts_missing=missing,
        statement_window=statement_window,
        overview_reference=overview_reference,
        hint_solution=hint_solution,
        proof_ingredient=proof_ingredient,
        noisy_page=noisy_page,
    )
