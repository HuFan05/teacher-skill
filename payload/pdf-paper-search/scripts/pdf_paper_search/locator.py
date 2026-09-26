from __future__ import annotations

import copy
import importlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from .diagnostics import summarize_diagnostics
from .normalization import PAPER_CANONICALIZER_VERSION, matching_exact_anchors
from .page_roles import NEVER_EXACT_ROLES
from .query_spec import QuerySpec, build_query_spec


PAPER_LOCATE_SCHEMA_VERSION = "paper-locate/v1"
_REQUIRED_TABLES = {"pdf_docs", "pdf_pages", "pdf_page_fts"}
_CLASS_ORDER = {"exact hit": 0, "near-exact": 1, "nearby material": 2}
MAX_WIRE_BYTES = 12 * 1024
MAX_VERIFIED_PDFS_PER_PASS = 8
MAX_ANCHOR_RESCUE_PAGES = 96
MAX_SIGNATURE_RESCUE_PAGES = 96


def _backend_or_default(backend: Any | None) -> Any:
    return backend if backend is not None else importlib.import_module("paper_search")


def _compact_text(text: str, limit: int) -> str:
    collapsed = " ".join(str(text).split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: max(0, limit - 3)].rstrip() + "..."


def _db_coverage(db_path: Path) -> tuple[dict[str, int], str | None]:
    if not db_path.is_file():
        return {}, "missing_db"
    try:
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        try:
            tables = {
                str(row[0])
                for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
                ).fetchall()
            }
            if not _REQUIRED_TABLES.issubset(tables):
                return {}, "schema_error"
            counts = {
                str(row["status"]): int(row["count"])
                for row in con.execute(
                    "SELECT status, count(*) AS count FROM pdf_docs GROUP BY status"
                ).fetchall()
            }
            return counts, None
        finally:
            con.close()
    except sqlite3.Error:
        return {}, "schema_error"


def _load_page_band(db_path: Path, path: str, page_number: int) -> list[dict[str, Any]]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    try:
        rows = con.execute(
            """
            SELECT d.title, d.path, d.status, d.extraction_method,
                   d.extraction_warning, p.page_number, p.content, p.snippet
            FROM pdf_docs d
            JOIN pdf_pages p ON p.doc_id = d.id
            WHERE d.path = ? AND p.page_number BETWEEN ? AND ?
            ORDER BY p.page_number
            """,
            (path, max(1, page_number - 1), page_number + 1),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        con.close()


def _is_verified_exact(spec: QuerySpec, match_type: str, features: dict[str, Any]) -> bool:
    page_role = str(features.get("page_role", "unknown"))
    negative_page = any(
        bool(features.get(key))
        for key in ("overview_reference", "hint_solution", "proof_ingredient", "noisy_page")
    )
    roles_match = not spec.required_page_roles or page_role in spec.required_page_roles
    # An abstract or introduction summary restates a result; it is an original
    # source only for claim queries that explicitly admit that role.
    abstract_blocked = page_role == "abstract" and "abstract" not in spec.required_page_roles
    anchors_match = not spec.exact_anchors or not features.get("exact_anchors_missing")
    classification_matches = match_type == "exact hit" or (
        bool(spec.exact_anchors) and match_type == "near-exact"
    )
    return bool(
        classification_matches
        and features.get("local_statement")
        and features.get("direct_statement")
        and not features.get("hard_concepts_missing")
        and anchors_match
        and roles_match
        and not negative_page
        and not abstract_blocked
        and page_role not in NEVER_EXACT_ROLES
    )


def _with_anchor_features(
    spec: QuerySpec, features: dict[str, Any], evidence_text: str
) -> dict[str, Any]:
    enriched = dict(features)
    if not spec.exact_anchors:
        return enriched
    found = matching_exact_anchors(spec.exact_anchors, evidence_text)
    missing = tuple(anchor for anchor in spec.exact_anchors if anchor not in found)
    enriched.update(
        {
            "exact_anchors_required": list(spec.exact_anchors),
            "exact_anchors_found": list(found),
            "exact_anchors_missing": list(missing),
        }
    )
    return enriched


def _with_source_features(
    backend: Any,
    spec: QuerySpec,
    features: dict[str, Any],
    title: str,
    path: str,
) -> dict[str, Any]:
    enriched = dict(features)
    source_scorer = getattr(backend, "original_source_score", None)
    if spec.query_type != "unknown" and callable(source_scorer):
        source_delta, _ = source_scorer(title, path)
        enriched["original_paper_source"] = source_delta > 0
    return enriched


def _result(
    *,
    title: str,
    path: str,
    page_number: int,
    seed_page: int,
    match_type: str,
    reasons: list[str],
    evidence: str,
    features: dict[str, Any],
    score: float,
    extraction_status: str = "unknown",
    extraction_method: str | None = None,
    extraction_warning: str | None = None,
) -> dict[str, Any]:
    offset = page_number - seed_page
    confidence = "medium" if match_type in {"exact hit", "near-exact"} else "low"
    return {
        "title": title,
        "path": path,
        "pdf_page": page_number,
        "printed_page": None,
        "match_type": match_type,
        "confidence": confidence,
        "verified": False,
        "origin": "retrieved" if offset == 0 else "adjacent",
        "seed_page": seed_page,
        "adjacent_offset": offset,
        "statement_window": evidence,  # Public envelope decides sufficiency without discarding conditions.
        "why": reasons[:4],
        "extraction": {
            "status": extraction_status,
            "method": extraction_method,
            "warning": _compact_text(extraction_warning or "", 240) or None,
        },
        "features": {
            key: value
            for key, value in features.items()
            if key
            in {
                "page_role",
                "local_statement",
                "direct_statement",
                "hard_concepts_required",
                "hard_concepts_found",
                "hard_concepts_missing",
                "exact_anchors_required",
                "exact_anchors_found",
                "exact_anchors_missing",
                "original_paper_source",
                "overview_reference",
                "hint_solution",
                "proof_ingredient",
                "noisy_page",
            }
        },
        "_selection_score": float(score),
    }


def _verify_hits(
    *,
    backend: Any,
    spec: QuerySpec,
    query: str,
    hits: list[Any],
    verified_pages: set[tuple[str, str, int]],
    max_pdfs: int = MAX_VERIFIED_PDFS_PER_PASS,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], int]:
    seeds: list[Any] = []
    seen_pdfs: set[tuple[str, str]] = set()
    for hit in hits:
        key = (str(hit.db), str(hit.path))
        if key in seen_pdfs:
            continue
        seen_pdfs.add(key)
        seeds.append(hit)
        if len(seeds) == max_pdfs:
            break

    alternatives: list[dict[str, Any]] = []
    exact_candidates: list[dict[str, Any]] = []
    adjacent_count = 0
    for hit in seeds:
        try:
            band = _load_page_band(Path(hit.db), str(hit.path), int(hit.page_number))
        except sqlite3.Error:
            band = []
        seed_row = next(
            (page for page in band if int(page["page_number"]) == int(hit.page_number)),
            None,
        )
        seed_evidence = str(hit.statement_window or hit.snippet)
        seed_features = _with_source_features(
            backend,
            spec,
            _with_anchor_features(spec, dict(hit.features), seed_evidence),
            str(hit.title),
            str(hit.path),
        )
        seed_key = (str(hit.db), str(hit.path), int(hit.page_number))
        verified_pages.add(seed_key)
        seed_result = _result(
            title=str(hit.title),
            path=str(hit.path),
            page_number=int(hit.page_number),
            seed_page=int(hit.page_number),
            match_type=str(hit.classification),
            reasons=list(hit.reasons),
            evidence=seed_evidence,
            features=seed_features,
            score=float(hit.final_score),
            extraction_status=str(seed_row["status"]) if seed_row else "unknown",
            extraction_method=str(seed_row["extraction_method"]) if seed_row and seed_row["extraction_method"] else None,
            extraction_warning=str(seed_row["extraction_warning"]) if seed_row and seed_row["extraction_warning"] else None,
        )
        if _is_verified_exact(spec, str(hit.classification), seed_features):
            if seed_result["match_type"] != "exact hit":
                seed_result["match_type"] = "exact hit"
                seed_result["why"] = [
                    "exact structural anchor",
                    *seed_result["why"],
                ][:4]
            seed_result["verified"] = True
            seed_result["confidence"] = "high"
            exact_candidates.append(seed_result)
            continue

        per_pdf = [seed_result]
        for page in band:
            page_number = int(page["page_number"])
            if page_number == int(hit.page_number):
                continue
            key = (str(hit.db), str(hit.path), page_number)
            if key in verified_pages:
                continue
            verified_pages.add(key)
            adjacent_count += 1
            delta, reasons, match_type, evidence = backend.rerank_bonus(
                search_spec=query,
                title=str(page["title"]),
                path=str(page["path"]),
                snippet=str(page["snippet"]),
                content=str(page["content"]),
            )
            neighbor_evidence = str(evidence.statement_window or page["content"])
            features = _with_source_features(
                backend,
                spec,
                _with_anchor_features(
                    spec,
                    evidence.to_features(classification=match_type),
                    neighbor_evidence,
                ),
                str(page["title"]),
                str(page["path"]),
            )
            neighbor_result = _result(
                title=str(page["title"]),
                path=str(page["path"]),
                page_number=page_number,
                seed_page=int(hit.page_number),
                match_type=str(match_type),
                reasons=list(reasons),
                evidence=neighbor_evidence,
                features=features,
                score=float(delta),
                extraction_status=str(page["status"]),
                extraction_method=str(page["extraction_method"]) if page["extraction_method"] else None,
                extraction_warning=str(page["extraction_warning"]) if page["extraction_warning"] else None,
            )
            if _is_verified_exact(spec, str(match_type), features):
                if neighbor_result["match_type"] != "exact hit":
                    neighbor_result["match_type"] = "exact hit"
                    neighbor_result["why"] = [
                        "exact structural anchor",
                        *neighbor_result["why"],
                    ][:4]
                neighbor_result["verified"] = True
                neighbor_result["confidence"] = "high"
                exact_candidates.append(neighbor_result)
                break
            per_pdf.append(neighbor_result)
        per_pdf.sort(
            key=lambda item: (
                _CLASS_ORDER.get(str(item["match_type"]), 3),
                -float(item["_selection_score"]),
                abs(int(item["adjacent_offset"])),
            )
        )
        alternatives.append(per_pdf[0])
    if exact_candidates:
        exact_candidates.sort(
            key=lambda item: (
                not bool(item["features"].get("original_paper_source")),
                -float(item["_selection_score"]),
                str(item["path"]).casefold(),
                int(item["pdf_page"]),
            )
        )
        return exact_candidates[0], alternatives, adjacent_count
    return None, alternatives, adjacent_count


def _warning(code: str, message: str) -> dict[str, str]:
    return {"level": "error", "stage": "paper_locate", "code": code, "message": message}


def _bounded_diagnostics(diagnostics: list[Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for diagnostic in diagnostics[:8]:
        payload = diagnostic.to_dict()
        results.append(
            {
                key: _compact_text(str(value), 240) if key == "message" else value
                for key, value in payload.items()
                if key in {"level", "stage", "code", "message", "exception_type"}
            }
        )
    return results


def bound_payload(payload: dict[str, Any], max_bytes: int = MAX_WIRE_BYTES) -> dict[str, Any]:
    """Return an accurate bounded package or a small structured failure package."""

    bounded = copy.deepcopy(payload)

    def byte_count(value: dict[str, Any]) -> int:
        return len(
            json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )

    if byte_count(bounded) <= max_bytes:
        return bounded
    bounded["output_truncated"] = True
    query = bounded.get("query", {})
    query["signature_terms"] = [
        _compact_text(str(item), 80) for item in query.get("signature_terms", [])[:8]
    ]
    query["hard_concepts"] = [
        _compact_text(str(item), 48) for item in query.get("hard_concepts", [])[:12]
    ]
    bounded.get("coverage", {}).update(
        {
            "warnings": [
                {
                    "level": item.get("level"),
                    "stage": item.get("stage"),
                    "code": item.get("code"),
                }
                for item in bounded.get("coverage", {}).get("warnings", [])[:4]
            ]
        }
    )
    for result in bounded.get("results", []):
        result["statement_window"] = _compact_text(
            result.get("statement_window", ""), 240
        )
        result["why"] = [
            _compact_text(str(item), 160) for item in result.get("why", [])[:2]
        ]
        result["title"] = _compact_text(result.get("title", ""), 240)
        extraction = result.get("extraction", {})
        extraction["warning"] = (
            _compact_text(extraction.get("warning") or "", 160) or None
        )
    if byte_count(bounded) <= max_bytes:
        return bounded
    bounded["results"] = bounded.get("results", [])[:1]
    if byte_count(bounded) <= max_bytes:
        return bounded
    return {
        "schema_version": bounded.get("schema_version", PAPER_LOCATE_SCHEMA_VERSION),
        "canonicalizer_version": bounded.get(
            "canonicalizer_version", PAPER_CANONICALIZER_VERSION
        ),
        "route": bounded.get("route", "explicit-index"),
        "status": "failed",
        "query": {
            "query_type": None,
            "hard_concepts": [],
            "signature_terms": [],
        },
        "search": {
            "alias_mode": bounded.get("search", {}).get("alias_mode", "core"),
            "expanded": bool(bounded.get("search", {}).get("expanded", False)),
            "candidate_count": int(bounded.get("search", {}).get("candidate_count", 0)),
            "candidate_pdf_count": int(
                bounded.get("search", {}).get("candidate_pdf_count", 0)
            ),
            "verified_page_count": int(
                bounded.get("search", {}).get("verified_page_count", 0)
            ),
            "adjacent_page_count": int(
                bounded.get("search", {}).get("adjacent_page_count", 0)
            ),
            "stop_reason": "output_too_large",
        },
        "results": [],
        "coverage": {
            "document_status_counts": {},
            "incomplete": True,
            "diagnostics_summary": {"errors": 1, "warnings": 0, "info": 0},
            "warnings": [
                {
                    "level": "error",
                    "stage": "paper_locate",
                    "code": "output_too_large",
                    "message": "The exact answer package exceeded the 12 KiB wire limit.",
                }
            ],
        },
        "timing_ms": bounded.get(
            "timing_ms", {"core": 0, "expanded": 0, "verify": 0, "total": 0}
        ),
    }


def bounded_json(payload: dict[str, Any], max_bytes: int = MAX_WIRE_BYTES) -> str:
    """Serialize a package with a strict UTF-8 byte ceiling."""

    bounded = bound_payload(payload, max_bytes=max_bytes)
    encoded = json.dumps(bounded, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > max_bytes:
        raise ValueError("The structured output_too_large payload exceeded max_bytes.")
    return encoded


def locate_paper(
    *,
    query: str,
    db_path: str | Path,
    extra_aliases: list[str] | tuple[str, ...] = (),
    limit: int = 12,
    backend: Any | None = None,
) -> dict[str, Any]:
    """Locate and verify one CS/AI source statement through one explicit SQLite database."""

    started = time.perf_counter()
    backend = _backend_or_default(backend)
    db = Path(db_path).expanduser().resolve()
    coverage_counts, db_error = _db_coverage(db)
    spec = build_query_spec(query, extra_aliases)
    base = {
        "schema_version": PAPER_LOCATE_SCHEMA_VERSION,
        "canonicalizer_version": PAPER_CANONICALIZER_VERSION,
        "route": "explicit-index",
        "query": {
            "query_type": spec.query_type,
            "hard_concepts": sorted(spec.hard_concepts),
            "signature_terms": list(spec.signature_terms),
            "exact_anchors": list(spec.exact_anchors),
            "signature_rescue_queries": list(spec.signature_rescue_queries),
        },
    }
    if db_error:
        warning = _warning(db_error, "The explicit PDF index is unavailable or incompatible.")
        return {
            **base,
            "status": "coverage_gap",
            "search": {
                "alias_mode": "core",
                "expanded": False,
                "candidate_count": 0,
                "candidate_pdf_count": 0,
                "verified_page_count": 0,
                "adjacent_page_count": 0,
                "stop_reason": db_error,
            },
            "results": [],
            "coverage": {
                "document_status_counts": coverage_counts,
                "diagnostics_summary": {"errors": 1, "warnings": 0, "info": 0},
                "warnings": [warning],
            },
            "timing_ms": {"core": 0, "expanded": 0, "verify": 0, "total": 0},
        }
    if not query.strip() or (
        not spec.hard_concepts and len(spec.signature_terms) < 2 and spec.statement_ref is None
    ):
        warning = _warning(
            "insufficient_query_anchors",
            "The query must contain a statement reference or at least two stable anchors.",
        )
        return {
            **base,
            "status": "failed",
            "search": {
                "alias_mode": "core",
                "expanded": False,
                "candidate_count": 0,
                "candidate_pdf_count": 0,
                "verified_page_count": 0,
                "adjacent_page_count": 0,
                "stop_reason": "insufficient_query_anchors",
            },
            "results": [],
            "coverage": {
                "document_status_counts": coverage_counts,
                "diagnostics_summary": {"errors": 1, "warnings": 0, "info": 0},
                "warnings": [warning],
            },
            "timing_ms": {"core": 0, "expanded": 0, "verify": 0, "total": 0},
        }

    backend.reset_diagnostics()
    safe_limit = min(30, max(3, int(limit)))
    verified_pages: set[tuple[str, str, int]] = set()
    all_alternatives: dict[str, dict[str, Any]] = {}

    def keep_best_alternative(item: dict[str, Any]) -> None:
        key = str(item["path"])
        current = all_alternatives.get(key)
        item_order = (
            _CLASS_ORDER.get(str(item["match_type"]), 3),
            -float(item["_selection_score"]),
        )
        if current is None:
            all_alternatives[key] = item
            return
        current_order = (
            _CLASS_ORDER.get(str(current["match_type"]), 3),
            -float(current["_selection_score"]),
        )
        if item_order < current_order:
            all_alternatives[key] = item

    core_started = time.perf_counter()
    core_aliases = backend.build_aliases(query, list(extra_aliases), "core")
    core_hits = backend.aggregate_hits([db], core_aliases, safe_limit, query)
    core_ms = round((time.perf_counter() - core_started) * 1000, 3)
    verify_started = time.perf_counter()
    exact, alternatives, adjacent_count = _verify_hits(
        backend=backend,
        spec=spec,
        query=query,
        hits=core_hits,
        verified_pages=verified_pages,
    )
    for item in alternatives:
        keep_best_alternative(item)
    verify_ms = (time.perf_counter() - verify_started) * 1000
    expanded = False
    expanded_ms = 0.0
    expanded_hits: list[Any] = []
    anchor_rescue_used = False
    anchor_rescue_ms = 0.0
    anchor_hits: list[Any] = []
    signature_rescue_used = False
    signature_rescue_ms = 0.0
    signature_hits: list[Any] = []
    stop_reason = "verified_core_hit" if exact else "core_not_verified"

    anchor_rescue = getattr(backend, "anchor_rescue_hits", None)
    if exact is None and spec.exact_anchors and callable(anchor_rescue):
        anchor_rescue_used = True
        anchor_started = time.perf_counter()
        anchor_hits = anchor_rescue(
            db_path=db,
            query=query,
            spec=spec,
            limit=MAX_ANCHOR_RESCUE_PAGES,
        )
        anchor_rescue_ms = (time.perf_counter() - anchor_started) * 1000
        verify_started = time.perf_counter()
        exact, alternatives, added_adjacent = _verify_hits(
            backend=backend,
            spec=spec,
            query=query,
            hits=anchor_hits,
            verified_pages=verified_pages,
        )
        adjacent_count += added_adjacent
        verify_ms += (time.perf_counter() - verify_started) * 1000
        for item in alternatives:
            keep_best_alternative(item)
        stop_reason = "verified_anchor_rescue_hit" if exact else "anchor_rescue_no_exact"

    signature_rescue = getattr(backend, "signature_rescue_hits", None)
    if exact is None and spec.signature_rescue_queries and callable(signature_rescue):
        signature_rescue_used = True
        signature_started = time.perf_counter()
        signature_hits = signature_rescue(
            db_path=db,
            query=query,
            spec=spec,
            limit=MAX_SIGNATURE_RESCUE_PAGES,
        )
        signature_rescue_ms = (time.perf_counter() - signature_started) * 1000
        verify_started = time.perf_counter()
        exact, alternatives, added_adjacent = _verify_hits(
            backend=backend,
            spec=spec,
            query=query,
            hits=signature_hits,
            verified_pages=verified_pages,
        )
        adjacent_count += added_adjacent
        verify_ms += (time.perf_counter() - verify_started) * 1000
        for item in alternatives:
            keep_best_alternative(item)
        stop_reason = (
            "verified_signature_rescue_hit"
            if exact
            else "signature_rescue_no_exact"
        )

    if exact is None:
        expanded_aliases = backend.build_aliases(query, list(extra_aliases), "expanded")
        if expanded_aliases != core_aliases:
            expanded = True
            expanded_started = time.perf_counter()
            expanded_hits = backend.aggregate_hits([db], expanded_aliases, safe_limit, query)
            expanded_ms = (time.perf_counter() - expanded_started) * 1000
            verify_started = time.perf_counter()
            exact, alternatives, added_adjacent = _verify_hits(
                backend=backend,
                spec=spec,
                query=query,
                hits=expanded_hits,
                verified_pages=verified_pages,
            )
            adjacent_count += added_adjacent
            verify_ms += (time.perf_counter() - verify_started) * 1000
            for item in alternatives:
                keep_best_alternative(item)
            stop_reason = "verified_expanded_hit" if exact else "expanded_once_no_exact"

    diagnostics = backend.get_diagnostics()
    diagnostics_summary = summarize_diagnostics(diagnostics)
    candidate_by_page: dict[tuple[str, str, int], Any] = {}
    for hit in [*core_hits, *anchor_hits, *signature_hits, *expanded_hits]:
        key = (str(hit.db), str(hit.path), int(hit.page_number))
        current = candidate_by_page.get(key)
        if current is None or float(hit.final_score) > float(current.final_score):
            candidate_by_page[key] = hit
    candidate_hits = sorted(
        candidate_by_page.values(),
        key=lambda hit: (
            _CLASS_ORDER.get(str(hit.classification), 3),
            -float(hit.final_score),
        ),
    )
    candidate_pdfs = {(str(hit.db), str(hit.path)) for hit in candidate_hits}
    incomplete_coverage = any(
        coverage_counts.get(status, 0) > 0 for status in ("pending", "no_text", "error")
    )
    if exact is not None:
        status = "verified_hit"
        results = [exact]
    elif candidate_hits:
        status = "ambiguous"
        results = sorted(
            all_alternatives.values(),
            key=lambda item: (
                _CLASS_ORDER.get(str(item["match_type"]), 3),
                -float(item["_selection_score"]),
            ),
        )[:3]
    elif diagnostics_summary["errors"] or coverage_counts.get("indexed", 0) == 0:
        status = "coverage_gap"
        results = []
        stop_reason = "incomplete_index_coverage"
    else:
        status = "not_found_in_indexed_text"
        results = []
        stop_reason = "no_indexed_match"

    for rank, item in enumerate(results, start=1):
        item.pop("_selection_score", None)
        item["rank"] = rank
    total_ms = (time.perf_counter() - started) * 1000
    return {
        **base,
        "status": status,
        "search": {
            "alias_mode": "expanded" if expanded else "core",
            "expanded": expanded,
            "anchor_rescue_used": anchor_rescue_used,
            "anchor_rescue_candidate_count": len(anchor_hits),
            "signature_rescue_used": signature_rescue_used,
            "signature_rescue_candidate_count": len(signature_hits),
            "candidate_count": len(candidate_hits),
            "candidate_pdf_count": len(candidate_pdfs),
            "verified_page_count": len(verified_pages),
            "adjacent_page_count": adjacent_count,
            "stop_reason": stop_reason,
        },
        "results": results,
        "coverage": {
            "document_status_counts": coverage_counts,
            "incomplete": incomplete_coverage,
            "diagnostics_summary": diagnostics_summary,
            "warnings": _bounded_diagnostics(
                [item for item in diagnostics if item.level != "info"]
            ),
        },
        "timing_ms": {
            "core": core_ms,
            "anchor_rescue": round(anchor_rescue_ms, 3),
            "signature_rescue": round(signature_rescue_ms, 3),
            "expanded": round(expanded_ms, 3),
            "verify": round(verify_ms, 3),
            "total": round(total_ms, 3),
        },
    }
