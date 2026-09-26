from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import sys
import time
from dataclasses import asdict
from pathlib import Path

from case_schema import (
    collect_matches,
    ensure_external_case_path,
    first_match_rank,
    load_cases,
    validate_case,
)
from pdf_paper_search.diagnostics import (
    compact_exception,
    diagnostic_buckets,
    diagnostic_failure_bucket,
    diagnostics_to_dicts,
    reset_diagnostics,
    get_diagnostics,
)
from pdf_paper_search.paths import validate_sqlite_db
from pdf_paper_search.types import SearchDiagnostic


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
SEARCH_SCRIPT = SCRIPT_DIR / "paper_search.py"
DIRECT_SEARCH_SCRIPT = SCRIPT_DIR / "direct_pdf_search.py"


def configure_streams() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")


def load_search_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run local regression cases for pdf-paper-search.")
    parser.add_argument("--cases", required=True, help="External JSONL benchmark file; real cases must not live in the Skill tree.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    parser.add_argument(
        "--case-id",
        action="append",
        dest="case_ids",
        help="Run only the specified case id. May be passed multiple times.",
    )
    parser.add_argument(
        "--split",
        action="append",
        dest="splits",
        help="Run only cases whose `split` matches one of the provided values.",
    )
    parser.add_argument(
        "--data-root",
        default="",
        help="Canonical data root used to remap missing indexed case DBs by basename.",
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="Allow zero selected cases to return success while still reporting no-cases.",
    )
    return parser.parse_args()


def resolve_cases_path(raw_path: str) -> Path:
    return ensure_external_case_path(Path(raw_path), SKILL_ROOT)


def failure_bucket(
    hits: list,
    source_rank: int | None,
    semantic_rank: int | None,
    forbidden_hits: list[dict],
    diagnostics: list[SearchDiagnostic],
) -> str:
    if forbidden_hits:
        return "forbidden-topk"
    diagnostic_bucket = diagnostic_failure_bucket(diagnostics, no_hits=not bool(hits))
    if diagnostic_bucket:
        return diagnostic_bucket
    if not hits:
        return "no-results"
    if semantic_rank is None:
        return "missed-gold"
    if source_rank is None:
        return "semantic-only"
    if source_rank != 1:
        return "source-not-top1"
    return "pass"


def resolve_case_dbs(case: dict, data_root: Path | None) -> tuple[list[Path], list[SearchDiagnostic]]:
    targets: list[Path] = []
    diagnostics: list[SearchDiagnostic] = []
    for item in case["dbs"]:
        original = Path(item).expanduser()
        path = original.resolve()
        if data_root is not None:
            mapped = (data_root / original.name).expanduser().resolve()
            if mapped.exists():
                path = mapped
            elif not path.exists():
                path = mapped
        targets.append(path)
        diagnostics.extend(validate_sqlite_db(path))
    return targets, diagnostics


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[position]


def summarize(results: list[dict]) -> dict:
    total = len(results)
    latencies = [item["latency_ms"] for item in results]
    summary = {
        "passed": sum(1 for item in results if item["passed"]),
        "passed_source": sum(1 for item in results if item["passed_source"]),
        "total": total,
        "source_at_1": sum(1 for item in results if item["source_top1_ok"]),
        "source_at_3": sum(1 for item in results if item["source_top3_ok"]),
        "semantic_at_1": sum(1 for item in results if item["semantic_top1_ok"]),
        "semantic_at_3": sum(1 for item in results if item["semantic_top3_ok"]),
        "median_latency_ms": round(statistics.median(latencies), 3) if latencies else 0.0,
        "p90_latency_ms": round(percentile(latencies, 0.9), 3),
        "max_latency_ms": round(max(latencies), 3) if latencies else 0.0,
        "results": results,
    }
    summary["failure_buckets"] = {}
    for item in results:
        bucket = item["failure_bucket"]
        summary["failure_buckets"][bucket] = summary["failure_buckets"].get(bucket, 0) + 1
    summary["diagnostic_buckets"] = {}
    for item in results:
        for bucket, count in item.get("diagnostic_buckets", {}).items():
            summary["diagnostic_buckets"][bucket] = summary["diagnostic_buckets"].get(bucket, 0) + count
    return summary


def no_cases_summary(
    *,
    diagnostics: list[SearchDiagnostic],
    cases_path: Path,
    data_root: Path | None,
    failure_bucket_name: str = "no-cases",
) -> dict:
    summary = summarize([])
    summary["failure_buckets"] = {failure_bucket_name: 1}
    summary["diagnostic_buckets"] = diagnostic_buckets(diagnostics)
    summary["diagnostics"] = diagnostics_to_dicts(diagnostics)
    summary["environment"] = {
        "cases": str(cases_path),
        "data_root": str(data_root) if data_root else "",
        "search_script": str(SEARCH_SCRIPT),
        "direct_search_script": str(DIRECT_SEARCH_SCRIPT),
    }
    return summary


def load_needed_modules(search_modes: set[str]) -> tuple[dict[str, object], list[SearchDiagnostic]]:
    modules: dict[str, object] = {}
    diagnostics: list[SearchDiagnostic] = []
    script_by_mode = {
        "indexed": SEARCH_SCRIPT,
        "direct_pdf": DIRECT_SEARCH_SCRIPT,
    }
    module_name_by_mode = {
        "indexed": "paper_search_eval_target_indexed",
        "direct_pdf": "paper_search_eval_target_direct",
    }
    for mode in sorted(search_modes):
        path = script_by_mode.get(mode)
        if path is None:
            diagnostics.append(
                SearchDiagnostic(
                    level="error",
                    stage="case_load",
                    code="unsupported_search_mode",
                    message=f"Unsupported search mode: {mode}",
                )
            )
            continue
        try:
            modules[mode] = load_search_module(path, module_name_by_mode[mode])
        except Exception as exc:
            diagnostics.append(
                SearchDiagnostic(
                    level="error",
                    stage="module_import",
                    code="module_import_failed",
                    message=compact_exception(exc),
                    path=str(path),
                    exception_type=type(exc).__name__,
                )
            )
    return modules, diagnostics


def run_case(modules: dict[str, object], raw_case: dict, data_root: Path | None = None) -> dict:
    case = validate_case(raw_case)
    search_mode = case["search_mode"]
    module = modules[search_mode]
    reset_diagnostics()
    case_diagnostics: list[SearchDiagnostic] = []
    aliases = module.build_aliases(case["query"], case.get("aliases", []))
    limit = int(case.get("limit", 10))

    started = time.perf_counter()
    if search_mode == "indexed":
        targets, case_diagnostics = resolve_case_dbs(case, data_root)
        hits = module.aggregate_hits(targets, aliases, limit, case["query"])
    elif search_mode == "direct_pdf":
        explicit_files = [Path(item) for item in case.get("pdf_files", [])]
        roots = [Path(item) for item in case.get("roots", [])]
        targets = module.discover_pdfs(
            explicit_files=explicit_files,
            roots=roots,
            query=case["query"],
            aliases=aliases,
            max_files=int(case.get("max_files", 8)),
        )
        cache_dir = Path(case["cache_dir"]) if case.get("cache_dir") else None
        hits = module.aggregate_hits(
            pdf_paths=targets,
            aliases=aliases,
            limit=limit,
            primary_query=case["query"],
            cache_dir=cache_dir,
        )
    else:
        raise ValueError(f"Unsupported search mode: {search_mode}")
    latency_ms = (time.perf_counter() - started) * 1000.0
    diagnostics = [*case_diagnostics, *get_diagnostics()]

    source_gold = case["source_gold"]
    semantic_gold = case["semantic_gold"]
    forbidden_topk = case["forbidden_topk"]

    source_rank = first_match_rank(hits, source_gold)
    semantic_rank = first_match_rank(hits, semantic_gold)
    source_topk = int(case["evaluation"]["source_topk"])
    semantic_topk = int(case["evaluation"]["semantic_topk"])

    source_top1_ok = source_rank == 1
    source_top3_ok = source_rank is not None and source_rank <= 3
    source_topk_ok = source_rank is not None and source_rank <= source_topk
    semantic_top1_ok = semantic_rank == 1
    semantic_top3_ok = semantic_rank is not None and semantic_rank <= 3
    semantic_topk_ok = semantic_rank is not None and semantic_rank <= semantic_topk

    forbidden_hits = collect_matches(hits, forbidden_topk, limit=int(case.get("forbidden_topk_limit", 5)))
    forbidden_ok = not forbidden_hits

    result = {
        "id": case["id"],
        "query": case["query"],
        "search_mode": search_mode,
        "split": case.get("split"),
        "sample_kind": case.get("sample_kind"),
        "aliases": aliases,
        "latency_ms": round(latency_ms, 3),
        "source_rank": source_rank,
        "semantic_rank": semantic_rank,
        "source_top1_ok": source_top1_ok,
        "source_top3_ok": source_top3_ok,
        "source_topk_ok": source_topk_ok,
        "semantic_top1_ok": semantic_top1_ok,
        "semantic_top3_ok": semantic_top3_ok,
        "semantic_topk_ok": semantic_topk_ok,
        "forbidden_ok": forbidden_ok,
        "forbidden_hits": forbidden_hits,
        "passed": semantic_topk_ok and forbidden_ok,
        "passed_source": source_topk_ok and forbidden_ok,
        "evaluation": case["evaluation"],
        "source_gold": source_gold,
        "semantic_gold": semantic_gold,
        "expected_top1": case["expected_top1"],
        "acceptable": case["acceptable"],
        "top_results": [asdict(hit) for hit in hits[:5]],
        "targets": [str(path) for path in targets],
        "diagnostics": diagnostics_to_dicts(diagnostics),
        "diagnostic_buckets": diagnostic_buckets(diagnostics),
    }
    result["failure_bucket"] = failure_bucket(
        hits=hits,
        source_rank=source_rank,
        semantic_rank=semantic_rank,
        forbidden_hits=forbidden_hits,
        diagnostics=diagnostics,
    )
    return result


def main() -> int:
    configure_streams()
    args = parse_args()
    cases_path = resolve_cases_path(args.cases)
    data_root = Path(args.data_root).expanduser().resolve() if args.data_root else None
    case_diagnostics: list[SearchDiagnostic] = []
    if not cases_path.exists():
        case_diagnostics.append(
            SearchDiagnostic(
                level="error",
                stage="case_load",
                code="missing_cases_file",
                message="Regression case file does not exist.",
                path=str(cases_path),
            )
        )
    cases = load_cases(cases_path)
    if args.case_ids:
        wanted = set(args.case_ids)
        cases = [case for case in cases if case.get("id") in wanted]
    if args.splits:
        wanted_splits = {item.casefold() for item in args.splits}
        cases = [
            case
            for case in cases
            if str(case.get("split", "")).casefold() in wanted_splits
        ]
    if not cases:
        case_diagnostics.append(
            SearchDiagnostic(
                level="warning",
                stage="case_load",
                code="no_cases",
                message="No regression cases were selected.",
                path=str(cases_path),
            )
        )
        summary = no_cases_summary(
            diagnostics=case_diagnostics,
            cases_path=cases_path,
            data_root=data_root,
        )
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        else:
            print("No regression cases selected.")
            if case_diagnostics:
                print(f"diagnostics: {diagnostic_buckets(case_diagnostics)}")
        return 0 if args.allow_empty else 1

    search_modes = {str(case.get("search_mode", "indexed")) for case in cases}
    modules, module_diagnostics = load_needed_modules(search_modes)
    if module_diagnostics:
        summary = no_cases_summary(
            diagnostics=[*case_diagnostics, *module_diagnostics],
            cases_path=cases_path,
            data_root=data_root,
            failure_bucket_name="module-import-failed",
        )
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        else:
            print("Search module import failed.")
            print(f"diagnostics: {diagnostic_buckets(module_diagnostics)}")
        return 1

    results = [run_case(modules, case, data_root=data_root) for case in cases]
    summary = summarize(results)
    summary["environment"] = {
        "cases": str(cases_path),
        "data_root": str(data_root) if data_root else "",
        "search_script": str(SEARCH_SCRIPT),
        "direct_search_script": str(DIRECT_SEARCH_SCRIPT),
    }

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if summary["passed"] == summary["total"] else 1

    print(
        "Passed "
        f"{summary['passed']}/{summary['total']} semantic cases; "
        f"source-pass {summary['passed_source']}/{summary['total']}"
    )
    print(
        "Summary: "
        f"source@1={summary['source_at_1']}, "
        f"source@3={summary['source_at_3']}, "
        f"semantic@1={summary['semantic_at_1']}, "
        f"semantic@3={summary['semantic_at_3']}, "
        f"median_latency_ms={summary['median_latency_ms']:.3f}"
    )
    for item in results:
        status = "PASS" if item["passed"] else "FAIL"
        top = item["top_results"][0] if item["top_results"] else None
        print(f"[{status}] {item['id']} ({item['failure_bucket']})")
        print(
            "  ranks: "
            f"source={item['source_rank']}, "
            f"semantic={item['semantic_rank']}, "
            f"latency_ms={item['latency_ms']:.3f}"
        )
        if top is None:
            print("  no results returned")
            if item["diagnostic_buckets"]:
                print(f"  diagnostics: {item['diagnostic_buckets']}")
            continue
        print(
            "  top1: "
            f"{top['title']} p.{top['page_number']} "
            f"({top['classification']}, score={top['final_score']:.3f})"
        )
        if item["forbidden_hits"]:
            violations = ", ".join(
                f"{entry['title']} p.{entry['page_number']}"
                for entry in item["forbidden_hits"]
            )
            print(f"  forbidden-topk violation: {violations}")
        if item["diagnostic_buckets"]:
            print(f"  diagnostics: {item['diagnostic_buckets']}")
    return 0 if summary["passed"] == summary["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
