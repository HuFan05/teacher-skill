from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


DiagnosticLevel = Literal["info", "warning", "error"]


@dataclass(slots=True)
class SearchDiagnostic:
    level: DiagnosticLevel
    stage: str
    code: str
    message: str
    path: str | None = None
    db: str | None = None
    alias: str | None = None
    exception_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value not in (None, "")}


@dataclass(slots=True)
class SearchResultBundle:
    query: str
    aliases: list[str]
    hits: list[Any]
    diagnostics: list[SearchDiagnostic] = field(default_factory=list)


@dataclass(slots=True)
class HitFeatures:
    bm25_best: float | None = None
    bm25_sum: float | None = None
    alias_count: int = 0
    alias_weight_sum: float = 0.0
    file_score: float = 0.0
    survey_doc_bonus: float = 0.0
    survey_section_bonus: float = 0.0
    page_role: str = "unknown"
    local_statement: bool = False
    direct_statement: bool = False
    hard_concepts_required: tuple[str, ...] = ()
    hard_concepts_found: tuple[str, ...] = ()
    hard_concepts_missing: tuple[str, ...] = ()
    concept_span: int | None = None
    overview_penalty: float = 0.0
    hint_solution_penalty: float = 0.0
    proof_ingredient_penalty: float = 0.0
    noisy_page_penalty: float = 0.0
    classification_bonus: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value not in (None, "", (), 0.0, 0, False)}


@dataclass(slots=True)
class PageEvidence:
    page_role: str = "unknown"
    local_statement: bool = False
    direct_statement: bool = False
    hard_concepts_required: tuple[str, ...] = ()
    hard_concepts_found: tuple[str, ...] = ()
    hard_concepts_missing: tuple[str, ...] = ()
    statement_window: str = ""
    overview_reference: bool = False
    hint_solution: bool = False
    proof_ingredient: bool = False
    noisy_page: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value not in (None, "", (), False)}

    def to_features(self, *, classification: str = "") -> dict[str, Any]:
        payload = self.to_dict()
        if classification:
            payload["classification"] = classification
        return payload


def diagnostic_dicts(diagnostics: list[SearchDiagnostic]) -> list[dict[str, Any]]:
    return [diagnostic.to_dict() for diagnostic in diagnostics]


def diagnostics_summary(diagnostics: list[SearchDiagnostic]) -> dict[str, int]:
    summary = {"errors": 0, "warnings": 0, "info": 0}
    for diagnostic in diagnostics:
        if diagnostic.level == "error":
            summary["errors"] += 1
        elif diagnostic.level == "warning":
            summary["warnings"] += 1
        else:
            summary["info"] += 1
    return summary
