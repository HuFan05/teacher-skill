from __future__ import annotations

import argparse
import importlib
import json
import os
import platform
import shutil
import sys
from pathlib import Path

from case_schema import ensure_external_case_path, load_cases
from pdf_paper_search.diagnostics import compact_exception, diagnostics_to_dicts
from pdf_paper_search.paths import (
    db_report,
    ensure_project_on_path,
    infer_skill_root,
    infer_vault_root,
    resolve_data_root,
    resolve_project_root,
)
from pdf_paper_search.types import SearchDiagnostic


SCRIPT_PATH = Path(__file__).resolve()
SCRIPT_DIR = SCRIPT_PATH.parent
SKILL_ROOT = SCRIPT_DIR.parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diagnose the local pdf-paper-search environment.")
    parser.add_argument("--cases", required=True, help="External regression JSONL file.")
    parser.add_argument("--data-root", help="Canonical obsidian_local_kb data root.")
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    return parser.parse_args()


def resolve_cases_path(raw_path: str) -> Path:
    return ensure_external_case_path(Path(raw_path), SKILL_ROOT)


def case_db_reports(cases: list[dict[str, object]], data_root: Path | None) -> list[dict[str, object]]:
    reports: list[dict[str, object]] = []
    for case in cases:
        if case.get("search_mode", "indexed") != "indexed":
            continue
        for raw_db in case.get("dbs", []):
            db_path = Path(str(raw_db)).expanduser()
            mapped = data_root / db_path.name if data_root else db_path
            selected = mapped if data_root and mapped.exists() else db_path
            report = db_report(selected)
            report["case_id"] = str(case.get("id", ""))
            report["raw_db"] = str(db_path)
            if selected != db_path:
                report["mapped_from"] = str(db_path)
            reports.append(report)
    return reports


def dependency_import_reports(project_root: Path) -> tuple[list[dict[str, object]], list[SearchDiagnostic]]:
    ensure_project_on_path(project_root)
    modules = [
        "obsidian_local_kb.pdf_db",
        "obsidian_local_kb.pdf_survey",
        "obsidian_local_kb.pdf_library_survey",
    ]
    reports: list[dict[str, object]] = []
    diagnostics: list[SearchDiagnostic] = []
    for module_name in modules:
        try:
            importlib.import_module(module_name)
            reports.append({"module": module_name, "ok": True})
        except Exception as exc:
            reports.append(
                {
                    "module": module_name,
                    "ok": False,
                    "exception_type": type(exc).__name__,
                    "message": compact_exception(exc),
                }
            )
            diagnostics.append(
                SearchDiagnostic(
                    level="error",
                    stage="dependency_import",
                    code="dependency_import_failed",
                    message=compact_exception(exc),
                    path=str(project_root),
                    exception_type=type(exc).__name__,
                )
            )
    return reports, diagnostics


def build_payload(args: argparse.Namespace) -> dict[str, object]:
    diagnostics: list[SearchDiagnostic] = []
    explicit_data_root = bool(args.data_root)
    data_root = Path(args.data_root).expanduser().resolve() if explicit_data_root else resolve_data_root(SCRIPT_PATH)
    project_root = resolve_project_root(SCRIPT_PATH)
    skill_root = infer_skill_root(SCRIPT_PATH)
    vault_root = infer_vault_root(SCRIPT_PATH)
    cases_path = resolve_cases_path(args.cases)
    cases_file_exists = cases_path.exists()
    if not cases_file_exists:
        diagnostics.append(
            SearchDiagnostic(
                level="warning",
                stage="case_load",
                code="missing_cases_file",
                message="Regression case file does not exist.",
                path=str(cases_path),
            )
        )
    cases = load_cases(cases_path)
    case_count = len(cases)
    indexed_case_count = sum(1 for case in cases if case.get("search_mode", "indexed") == "indexed")
    direct_case_count = sum(1 for case in cases if case.get("search_mode", "indexed") == "direct_pdf")
    if case_count == 0:
        diagnostics.append(
            SearchDiagnostic(
                level="warning",
                stage="case_load",
                code="no_cases",
                message="No regression cases were found.",
                path=str(cases_path),
            )
        )
    if explicit_data_root and not data_root.exists():
        diagnostics.append(
            SearchDiagnostic(
                level="error",
                stage="data_root",
                code="missing_data_root",
                message="Explicit data root does not exist.",
                path=str(data_root),
            )
        )
    dbs = [db_report(path) for path in sorted(data_root.glob("pdf*.sqlite3"))] if data_root.exists() else []
    db_count_status = "ok"
    if len(dbs) == 0:
        db_count_status = "error" if indexed_case_count > 0 else "warning"
        diagnostics.append(
            SearchDiagnostic(
                level="error" if indexed_case_count > 0 else "warning",
                stage="db_discovery",
                code="no_pdf_databases",
                message="No pdf*.sqlite3 databases were found under the selected data root.",
                path=str(data_root),
            )
        )
    regression_reports = case_db_reports(cases, data_root if args.data_root else None)
    dependency_imports, dependency_diagnostics = dependency_import_reports(project_root)
    diagnostics.extend(dependency_diagnostics)
    pdftotext = shutil.which("pdftotext")
    cache_dir = Path.home() / "Documents" / ".pdf-paper-search-cache"
    cache_writable = False
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        probe = cache_dir / ".doctor-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        cache_writable = True
    except Exception:
        cache_writable = False

    db_errors = [
        report
        for report in regression_reports
        if not report.get("exists") or not report.get("tables_ok") or int(report.get("doc_count", 0)) <= 0
    ]
    for report in db_errors:
        diagnostics.append(
            SearchDiagnostic(
                level="error",
                stage="regression_db",
                code=str(report.get("error") or "regression_db_error"),
                message="Regression case database is missing, empty, or has an invalid schema.",
                db=str(report.get("path", "")),
            )
        )
    if not pdftotext:
        diagnostics.append(
            SearchDiagnostic(
                level="warning",
                stage="pdftotext",
                code="pdftotext_missing",
                message="pdftotext executable was not found on PATH.",
            )
        )
    if not cache_writable:
        diagnostics.append(
            SearchDiagnostic(
                level="warning",
                stage="cache",
                code="cache_not_writable",
                message="PDF extraction cache is not writable.",
                path=str(cache_dir),
            )
        )
    if any(diagnostic.level == "error" for diagnostic in diagnostics):
        status = "error"
    elif diagnostics:
        status = "warning"
    else:
        status = "ok"
    return {
        "status": status,
        "python": {
            "version": platform.python_version(),
            "executable": sys.executable,
        },
        "skill_root": str(skill_root),
        "package_root": str(SCRIPT_DIR / "pdf_paper_search"),
        "vault_root": str(vault_root) if vault_root else None,
        "env": {
            "PDF_PAPER_SEARCH_ROOT": os.environ.get("PDF_PAPER_SEARCH_ROOT"),
            "OBSIDIAN_LOCAL_KB_ROOT": os.environ.get("OBSIDIAN_LOCAL_KB_ROOT"),
            "OBSIDIAN_LOCAL_KB_DATA_ROOT": os.environ.get("OBSIDIAN_LOCAL_KB_DATA_ROOT"),
        },
        "project_root": str(project_root),
        "selected_data_root": str(data_root),
        "cases": {
            "path": str(cases_path),
            "exists": cases_file_exists,
            "case_count": case_count,
            "indexed_case_count": indexed_case_count,
            "direct_case_count": direct_case_count,
        },
        "db_count": len(dbs),
        "db_count_status": db_count_status,
        "dbs": dbs,
        "regression_db_reports": regression_reports,
        "dependency_imports": dependency_imports,
        "pdftotext": {
            "found": bool(pdftotext),
            "path": pdftotext,
        },
        "cache": {
            "dir": str(cache_dir),
            "writable": cache_writable,
        },
        "diagnostics": diagnostics_to_dicts(diagnostics),
    }


def main() -> int:
    args = parse_args()
    payload = build_payload(args)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"status: {payload['status']}")
        print(f"skill_root: {payload['skill_root']}")
        print(f"selected_data_root: {payload['selected_data_root']}")
        print(f"db_count: {payload['db_count']}")
        print(f"pdftotext: {payload['pdftotext']}")
        print(f"cache: {payload['cache']}")
    return 0 if payload["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
