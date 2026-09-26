from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

from .types import SearchDiagnostic


REQUIRED_PDF_TABLES = {"pdf_docs", "pdf_pages", "pdf_page_fts"}


def infer_skill_root(script_path: Path | None = None) -> Path:
    path = (script_path or Path(__file__)).resolve()
    for parent in [path.parent, *path.parents]:
        if (parent / "SKILL.md").exists() and (parent / "scripts").exists():
            return parent
    return path.parents[1]


def infer_vault_root(script_path: Path | None = None) -> Path | None:
    configured_root = os.environ.get("OBSIDIAN_VAULT_ROOT")
    if configured_root:
        return Path(configured_root).expanduser().resolve()
    return None


def sibling_project_root(script_path: Path | None = None) -> Path:
    """Locate the obsidian_local_kb project shipped by the sibling note skill."""

    skill_root = infer_skill_root(script_path)
    return skill_root.parent / "obsidian-vault-notes" / "scripts" / "obsidian_local_kb"


def resolve_existing_path(candidates: list[Path | None]) -> Path:
    normalized = [Path(path).expanduser().resolve() for path in candidates if path is not None]
    for path in normalized:
        if path.exists():
            return path
    if not normalized:
        raise FileNotFoundError("No candidate paths were provided.")
    return normalized[0]


def resolve_project_root(script_path: Path | None = None) -> Path:
    vault_root = infer_vault_root(script_path)
    env_root = os.environ.get("OBSIDIAN_LOCAL_KB_ROOT")
    pdf_search_root = os.environ.get("PDF_PAPER_SEARCH_ROOT")
    pdf_search_project = Path(pdf_search_root) / "scripts" / "obsidian_local_kb" if pdf_search_root else None
    vault_project = vault_root / "scripts" / "obsidian_local_kb" if vault_root else None
    return resolve_existing_path(
        [
            Path(env_root) if env_root else None,
            pdf_search_project,
            vault_project,
            sibling_project_root(script_path),
        ]
    )


def resolve_data_root(script_path: Path | None = None, project_root: Path | None = None) -> Path:
    env_data_root = os.environ.get("OBSIDIAN_LOCAL_KB_DATA_ROOT")
    project = project_root or resolve_project_root(script_path)
    vault_root = infer_vault_root(script_path)
    vault_data = vault_root / "scripts" / "obsidian_local_kb" / "data" if vault_root else None
    candidates = [
        Path(env_data_root) if env_data_root else None,
        project / "data",
        vault_data,
    ]
    normalized = [Path(path).expanduser().resolve() for path in candidates if path is not None]
    for path in normalized:
        if path.exists() and any(path.glob("pdf*.sqlite3")):
            return path
    for path in normalized:
        if path.exists():
            return path
    if not normalized:
        raise FileNotFoundError("No data root candidates were available.")
    return normalized[0]


def ensure_project_on_path(project_root: Path) -> None:
    value = str(project_root)
    if value not in sys.path:
        sys.path.insert(0, value)


def discover_sqlite_dbs(explicit: list[str | Path], data_root: Path) -> tuple[list[Path], list[SearchDiagnostic]]:
    diagnostics: list[SearchDiagnostic] = []
    if explicit:
        paths = [Path(item).expanduser().resolve() for item in explicit]
    else:
        paths = []
        if data_root.exists():
            for db_path in sorted(data_root.glob("pdf*.sqlite3")):
                if db_path.name.startswith("_bench"):
                    continue
                paths.append(db_path.resolve())
        else:
            diagnostics.append(
                SearchDiagnostic(
                    level="error",
                    stage="db_discovery",
                    code="missing_data_root",
                    message=f"Data root does not exist: {data_root}",
                    path=str(data_root),
                )
            )
    for db_path in paths:
        diagnostics.extend(validate_sqlite_db(db_path))
    return paths, diagnostics


def sqlite_table_counts(db_path: Path) -> dict[str, int]:
    con = sqlite3.connect(db_path)
    try:
        counts: dict[str, int] = {}
        for table in ("pdf_docs", "pdf_pages", "pdf_page_fts"):
            counts[table] = int(con.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
        return counts
    finally:
        con.close()


def sqlite_tables(db_path: Path) -> set[str]:
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')").fetchall()
        return {str(row[0]) for row in rows}
    finally:
        con.close()


def validate_sqlite_db(db_path: Path) -> list[SearchDiagnostic]:
    diagnostics: list[SearchDiagnostic] = []
    if not db_path.exists():
        return [
            SearchDiagnostic(
                level="error",
                stage="db_validate",
                code="missing_db",
                message=f"SQLite database does not exist: {db_path}",
                db=str(db_path),
            )
        ]
    try:
        tables = sqlite_tables(db_path)
        missing_tables = sorted(REQUIRED_PDF_TABLES - tables)
        if missing_tables:
            diagnostics.append(
                SearchDiagnostic(
                    level="error",
                    stage="db_validate",
                    code="schema_error",
                    message=f"Missing required tables: {', '.join(missing_tables)}",
                    db=str(db_path),
                )
            )
            return diagnostics
        counts = sqlite_table_counts(db_path)
        if any(counts.get(table, 0) <= 0 for table in REQUIRED_PDF_TABLES):
            diagnostics.append(
                SearchDiagnostic(
                    level="error",
                    stage="db_validate",
                    code="empty_db",
                    message=f"One or more required tables are empty: {counts}",
                    db=str(db_path),
                )
            )
    except Exception as exc:
        diagnostics.append(
            SearchDiagnostic(
                level="error",
                stage="db_validate",
                code="schema_error",
                message=str(exc),
                db=str(db_path),
                exception_type=type(exc).__name__,
            )
        )
    return diagnostics


def db_report(db_path: Path) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "path": str(db_path),
        "exists": db_path.exists(),
        "tables_ok": False,
        "doc_count": 0,
        "page_count": 0,
        "fts_count": 0,
    }
    if not db_path.exists():
        payload["error"] = "missing_db"
        return payload
    try:
        tables = sqlite_tables(db_path)
        missing = sorted(REQUIRED_PDF_TABLES - tables)
        if missing:
            payload["error"] = f"missing tables: {', '.join(missing)}"
            return payload
        counts = sqlite_table_counts(db_path)
        payload.update(
            {
                "tables_ok": True,
                "doc_count": counts["pdf_docs"],
                "page_count": counts["pdf_pages"],
                "fts_count": counts["pdf_page_fts"],
            }
        )
    except Exception as exc:
        payload["error"] = f"{type(exc).__name__}: {exc}"
    return payload

