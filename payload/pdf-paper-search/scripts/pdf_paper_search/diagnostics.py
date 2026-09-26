from __future__ import annotations

import traceback
from collections import Counter
from typing import Iterable

from .types import SearchDiagnostic, diagnostic_dicts, diagnostics_summary


_DIAGNOSTICS: list[SearchDiagnostic] = []


def reset_diagnostics() -> None:
    _DIAGNOSTICS.clear()


def record_diagnostic(diagnostic: SearchDiagnostic) -> None:
    _DIAGNOSTICS.append(diagnostic)


def get_diagnostics() -> list[SearchDiagnostic]:
    return list(_DIAGNOSTICS)


def compact_exception(exc: BaseException, *, limit: int = 500) -> str:
    message = str(exc).strip()
    if not message:
        message = "".join(traceback.format_exception_only(type(exc), exc)).strip()
    message = " ".join(message.split())
    if len(message) <= limit:
        return message
    return message[: max(0, limit - 3)].rstrip() + "..."


def diagnostics_to_dicts(diagnostics: Iterable[SearchDiagnostic]) -> list[dict[str, object]]:
    return diagnostic_dicts(list(diagnostics))


def summarize_diagnostics(diagnostics: Iterable[SearchDiagnostic]) -> dict[str, int]:
    return diagnostics_summary(list(diagnostics))


def diagnostic_buckets(diagnostics: Iterable[SearchDiagnostic]) -> dict[str, int]:
    counts = Counter(diagnostic.code for diagnostic in diagnostics)
    return dict(sorted(counts.items()))


def diagnostic_failure_bucket(diagnostics: Iterable[SearchDiagnostic], *, no_hits: bool = False) -> str | None:
    codes = {diagnostic.code for diagnostic in diagnostics if diagnostic.level == "error"}
    if "missing_data_root" in codes:
        return "data-root-error"
    if "missing_db" in codes:
        return "missing-db"
    if "empty_db" in codes:
        return "empty-db"
    if "schema_error" in codes:
        return "schema-error"
    if "query_exception" in codes or "alias_query_failed" in codes:
        return "query-exception"
    if "pdf_extraction_failed" in codes or "missing_pdf" in codes:
        return "pdf-extraction-error" if no_hits else None
    return None
