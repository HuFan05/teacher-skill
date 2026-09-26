from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_SOURCE_TOPK = 1
DEFAULT_SEMANTIC_TOPK = 3
DEFAULT_SEARCH_MODE = "indexed"
SUPPORTED_SEARCH_MODES = {DEFAULT_SEARCH_MODE, "direct_pdf"}


def ensure_reference_list(value: Any, field_name: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, dict):
        refs = [value]
    elif isinstance(value, list):
        refs = value
    else:
        raise ValueError(f"`{field_name}` must be an object or a list of objects.")
    return [normalize_reference(ref, field_name) for ref in refs]


def normalize_reference(ref: dict[str, Any], field_name: str = "reference") -> dict[str, Any]:
    if not isinstance(ref, dict):
        raise ValueError(f"Each `{field_name}` entry must be an object.")
    if "title" not in ref or "page_number" not in ref:
        raise ValueError(f"Each `{field_name}` entry must contain `title` and `page_number`.")

    normalized = dict(ref)
    normalized["title"] = str(ref["title"])
    normalized["page_number"] = int(ref["page_number"])

    for optional_key in ("db", "path", "anchor_text", "printed_page", "label", "role", "notes"):
        if optional_key in normalized and normalized[optional_key] is not None:
            normalized[optional_key] = str(normalized[optional_key])
    return normalized


def same_reference(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if left["title"] != right["title"] or int(left["page_number"]) != int(right["page_number"]):
        return False
    if left.get("db") and right.get("db") and left["db"] != right["db"]:
        return False
    if left.get("path") and right.get("path") and left["path"] != right["path"]:
        return False
    return True


def unique_references(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for ref in refs:
        if any(same_reference(ref, existing) for existing in result):
            continue
        result.append(ref)
    return result


def match_reference(ref: dict[str, Any], hit: dict[str, Any] | Any) -> bool:
    title = hit.get("title") if isinstance(hit, dict) else getattr(hit, "title", None)
    page_number = hit.get("page_number") if isinstance(hit, dict) else getattr(hit, "page_number", None)
    db = hit.get("db") if isinstance(hit, dict) else getattr(hit, "db", None)
    path = hit.get("path") if isinstance(hit, dict) else getattr(hit, "path", None)

    if title != ref["title"] or int(page_number) != int(ref["page_number"]):
        return False
    if ref.get("db") and db and ref["db"] != db:
        return False
    if ref.get("path") and path and ref["path"] != path:
        return False
    return True


def first_match_rank(hits: list[dict[str, Any]] | list[Any], refs: list[dict[str, Any]]) -> int | None:
    for index, hit in enumerate(hits, start=1):
        if any(match_reference(ref, hit) for ref in refs):
            return index
    return None


def collect_matches(
    hits: list[dict[str, Any]] | list[Any],
    refs: list[dict[str, Any]],
    limit: int | None = None,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, hit in enumerate(hits, start=1):
        if limit is not None and index > limit:
            break
        if any(match_reference(ref, hit) for ref in refs):
            result.append(
                {
                    "rank": index,
                    "title": hit.get("title") if isinstance(hit, dict) else getattr(hit, "title"),
                    "page_number": int(
                        hit.get("page_number") if isinstance(hit, dict) else getattr(hit, "page_number")
                    ),
                }
            )
    return result


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    if not path.exists():
        return cases
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            cases.append(json.loads(line))
    return cases


def write_cases(path: Path, cases: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for case in cases:
            handle.write(json.dumps(case, ensure_ascii=False))
            handle.write("\n")


def normalize_case(case: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(case, dict):
        raise ValueError("Case must be a JSON object.")
    normalized = dict(case)
    search_mode = str(case.get("search_mode", DEFAULT_SEARCH_MODE))
    if search_mode not in SUPPORTED_SEARCH_MODES:
        raise ValueError(
            f"`search_mode` must be one of {', '.join(sorted(SUPPORTED_SEARCH_MODES))}."
        )

    source_gold = ensure_reference_list(
        case.get("source_gold", case.get("expected_top1")),
        "source_gold",
    )
    if not source_gold:
        raise ValueError("Case must define `source_gold` or `expected_top1`.")

    semantic_gold = ensure_reference_list(case.get("semantic_gold"), "semantic_gold")
    if not semantic_gold:
        semantic_gold = source_gold + ensure_reference_list(case.get("acceptable"), "acceptable")
    semantic_gold = unique_references(source_gold + semantic_gold)

    forbidden_topk = ensure_reference_list(case.get("forbidden_topk"), "forbidden_topk")
    acceptable = [
        ref for ref in semantic_gold if not any(same_reference(ref, source_ref) for source_ref in source_gold)
    ]

    evaluation = dict(case.get("evaluation", {}))
    evaluation["source_topk"] = int(evaluation.get("source_topk", case.get("source_topk", DEFAULT_SOURCE_TOPK)))
    evaluation["semantic_topk"] = int(
        evaluation.get("semantic_topk", case.get("semantic_topk", DEFAULT_SEMANTIC_TOPK))
    )
    if evaluation["source_topk"] <= 0 or evaluation["semantic_topk"] <= 0:
        raise ValueError("`source_topk` and `semantic_topk` must be positive integers.")

    normalized["id"] = str(case["id"])
    normalized["query"] = str(case["query"])
    normalized["search_mode"] = search_mode
    normalized["dbs"] = [str(item) for item in case.get("dbs", [])]
    normalized["roots"] = [str(item) for item in case.get("roots", [])]
    normalized["pdf_files"] = [str(item) for item in case.get("pdf_files", [])]
    normalized["source_gold"] = source_gold
    normalized["semantic_gold"] = semantic_gold
    normalized["acceptable"] = acceptable
    normalized["expected_top1"] = source_gold[0]
    normalized["forbidden_topk"] = forbidden_topk
    normalized["evaluation"] = evaluation

    if "limit" in normalized:
        normalized["limit"] = int(normalized["limit"])
    if "max_files" in normalized:
        normalized["max_files"] = int(normalized["max_files"])
    if "aliases" in normalized:
        normalized["aliases"] = [str(item) for item in normalized["aliases"]]
    if "split" in normalized and normalized["split"] is not None:
        normalized["split"] = str(normalized["split"])
    if "sample_kind" in normalized and normalized["sample_kind"] is not None:
        normalized["sample_kind"] = str(normalized["sample_kind"])
    for optional_key in (
        "query_origin",
        "source_role_gold",
        "doc_fingerprint",
        "page_text_hash",
        "edition_note",
        "expected_failure_layer",
    ):
        if optional_key in normalized and normalized[optional_key] is not None:
            normalized[optional_key] = str(normalized[optional_key])
    if "query_damage" in normalized and normalized["query_damage"] is not None:
        if not isinstance(normalized["query_damage"], list):
            raise ValueError("`query_damage` must be a list when provided.")
        normalized["query_damage"] = [str(item) for item in normalized["query_damage"]]
    return normalized


def validate_case(case: dict[str, Any]) -> dict[str, Any]:
    required_keys = {"id", "query"}
    missing = sorted(required_keys - set(case))
    if missing:
        raise ValueError(f"Missing required case keys: {', '.join(missing)}")

    normalized = normalize_case(case)
    if normalized["search_mode"] == "indexed" and not normalized["dbs"]:
        raise ValueError("Indexed cases must define a non-empty `dbs` list.")
    if normalized["search_mode"] == "direct_pdf" and not (
        normalized["pdf_files"] or normalized["roots"]
    ):
        raise ValueError("Direct-PDF cases must define `pdf_files` or `roots`.")
    if not normalized["semantic_gold"]:
        raise ValueError("`semantic_gold` must not be empty after normalization.")
    return normalized


def ensure_external_case_path(path: Path, skill_root: Path) -> Path:
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(skill_root.expanduser().resolve())
    except ValueError:
        return resolved
    raise ValueError("Real benchmark cases must remain outside the Skill tree.")
