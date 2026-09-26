"""Bounded subprocess integrations for the skills used by this package.

The functions in this module deliberately invoke the installed runtime copies of
the dependent skills.  They never import their private Python modules and never
use a shell, so upgrades and argument boundaries remain visible in diagnostics.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable, Mapping, Sequence


@dataclass(frozen=True)
class IntegrationPaths:
    """Resolved entry points for installed dependent skills."""

    skills_root: Path
    obsidian_skill_root: Path
    obsidian_setup: Path
    obsidian_local_kb_root: Path
    pdf_paper_skill_root: Path
    pdf_paper_search: Path
    pdf_paper_locate: Path
    pdf_observer_run: Path

    def to_json(self) -> dict[str, str]:
        return {key: str(value) for key, value in asdict(self).items()}


def runtime_skills_root(skills_home: str | Path | None = None) -> Path:
    """Return the sibling Skill root without embedding a receiver path.

    Resolution order is an explicit argument (a skills directory, or a home
    directory containing ``skills``), ``MPK_SKILLS_ROOT``, then the installed
    Skill's sibling directory.  The sibling route keeps portable multi-Skill
    installations independent of any particular host agent.
    """

    if skills_home is None and os.environ.get("MPK_SKILLS_ROOT"):
        return Path(str(os.environ["MPK_SKILLS_ROOT"])).expanduser().resolve()
    root_value = skills_home
    if root_value is not None:
        root = Path(root_value).expanduser().resolve()
        return root if root.name.casefold() == "skills" else root / "skills"
    return Path(__file__).resolve().parents[3]


def locate_integrations(skills_home: str | Path | None = None) -> IntegrationPaths:
    skills_root = runtime_skills_root(skills_home)
    obsidian_root = skills_root / "obsidian-vault-notes"
    pdf_paper_root = skills_root / "pdf-paper-search"
    return IntegrationPaths(
        skills_root=skills_root,
        obsidian_skill_root=obsidian_root,
        obsidian_setup=obsidian_root / "scripts" / "setup_local.py",
        obsidian_local_kb_root=obsidian_root / "scripts" / "obsidian_local_kb",
        pdf_paper_skill_root=pdf_paper_root,
        pdf_paper_search=pdf_paper_root / "scripts" / "paper_search.py",
        pdf_paper_locate=pdf_paper_root / "scripts" / "paper_locate.py",
        pdf_observer_run=pdf_paper_root / "scripts" / "observer_run.py",
    )


def validate_integrations(
    skills_home: str | Path | None = None,
    *,
    required: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Check all integrations for status, or one operation's required subset."""

    paths = locate_integrations(skills_home)
    checks = {
        "skills_root": paths.skills_root.is_dir(),
        "obsidian_setup": paths.obsidian_setup.is_file(),
        "obsidian_local_kb": (
            paths.obsidian_local_kb_root / "obsidian_local_kb" / "__main__.py"
        ).is_file(),
        "pdf_paper_search": paths.pdf_paper_search.is_file(),
        "pdf_paper_locate": paths.pdf_paper_locate.is_file(),
        "pdf_observer_run": paths.pdf_observer_run.is_file(),
    }
    if required is None:
        required_names = set(checks)
    else:
        required_names = {"skills_root", *(str(name) for name in required)}
        unknown = required_names - set(checks)
        if unknown:
            raise ValueError(
                "Unknown integration requirement(s): " + ", ".join(sorted(unknown))
            )
    missing = [
        name
        for name, present in checks.items()
        if name in required_names and not present
    ]
    return {
        "ok": not missing,
        "status": "ready" if not missing else "missing_dependencies",
        "paths": paths.to_json(),
        "checks": checks,
        "required": sorted(required_names),
        "diagnostics": [
            {
                "level": "error",
                "code": "missing_integration",
                "component": name,
                "message": f"Required runtime integration is missing: {name}",
            }
            for name in missing
        ],
    }


def _base_environment(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment.setdefault("PYTHONUTF8", "1")
    if extra:
        environment.update({str(key): str(value) for key, value in extra.items()})
    return environment


def _decode_json(stdout: str) -> Any:
    text = stdout.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # setup_local.py may stream index progress before its final one-line JSON.
        for line in reversed(text.splitlines()):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None


def _run_json_process(
    command: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str] | None = None,
    operation: str,
) -> dict[str, Any]:
    safe_command = [str(part) for part in command]
    try:
        completed = subprocess.run(
            safe_command,
            cwd=str(cwd),
            env=_base_environment(environment),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        return {
            "ok": False,
            "status": "launch_failed",
            "operation": operation,
            "command": safe_command,
            "returncode": None,
            "data": None,
            "error": {
                "code": "subprocess_launch_failed",
                "type": type(exc).__name__,
                "message": str(exc),
            },
            "diagnostics": [],
        }

    payload = _decode_json(completed.stdout)
    child_failed = isinstance(payload, dict) and payload.get("ok") is False
    ok = completed.returncode == 0 and payload is not None and not child_failed
    if completed.returncode != 0:
        error_code = "subprocess_failed"
        message = completed.stderr.strip() or "The integration command returned a nonzero exit code."
    elif payload is None:
        error_code = "invalid_json_output"
        message = "The integration command did not return machine-readable JSON."
    elif child_failed:
        error_code = "child_reported_failure"
        message = "The integration command reported an unsuccessful operation."
    else:
        error_code = ""
        message = ""
    diagnostics: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        raw_diagnostics = payload.get("diagnostics")
        if isinstance(raw_diagnostics, list):
            diagnostics = [item for item in raw_diagnostics if isinstance(item, dict)]
    return {
        "ok": ok,
        "status": "completed" if ok else "failed",
        "operation": operation,
        "command": safe_command,
        "returncode": completed.returncode,
        "data": payload,
        "error": None if ok else {"code": error_code, "message": message},
        "stderr": completed.stderr.strip(),
        "diagnostics": diagnostics,
    }


def _argument_error(operation: str, code: str, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "invalid_request",
        "operation": operation,
        "command": [],
        "returncode": None,
        "data": None,
        "error": {"code": code, "message": message},
        "diagnostics": [],
    }


def configure_obsidian(
    vault_root: str | Path,
    config_path: str | Path,
    state_dir: str | Path,
    *,
    build_index: bool = False,
    confirmed_replace: bool = False,
    skills_home: str | Path | None = None,
    python_executable: str | Path | None = None,
) -> dict[str, Any]:
    """Configure the installed Vault skill using explicit receiver paths."""

    operation = "obsidian_configure"
    vault = Path(vault_root).expanduser().resolve()
    config = Path(config_path).expanduser().resolve()
    state = Path(state_dir).expanduser().resolve()
    if not vault.is_dir() or not (vault / ".obsidian").is_dir():
        return _argument_error(operation, "invalid_vault", "Vault root must contain a .obsidian directory.")
    validation = validate_integrations(
        skills_home,
        required={"obsidian_setup", "obsidian_local_kb"},
    )
    if not validation["ok"]:
        return {
            **_argument_error(operation, "missing_dependencies", "Required runtime skills are unavailable."),
            "diagnostics": validation["diagnostics"],
        }
    paths = locate_integrations(skills_home)
    command = [
        str(python_executable or sys.executable),
        "-B",
        str(paths.obsidian_setup),
        "configure",
        "--vault-root",
        str(vault),
        "--config",
        str(config),
        "--state-dir",
        str(state),
    ]
    if build_index:
        command.append("--build-index")
    if confirmed_replace:
        command.append("--yes")
    return _run_json_process(
        command,
        cwd=paths.obsidian_setup.parent,
        operation=operation,
        environment={"OBSIDIAN_VAULT_NOTES_CONFIG": str(config)},
    )


def check_obsidian(
    vault_root: str | Path,
    config_path: str | Path,
    *,
    state_dir: str | Path | None = None,
    skills_home: str | Path | None = None,
    python_executable: str | Path | None = None,
) -> dict[str, Any]:
    """Check the installed Vault integration without accepting a silent relink."""

    operation = "obsidian_check"
    vault = Path(vault_root).expanduser().resolve()
    config = Path(config_path).expanduser().resolve()
    if not vault.is_dir() or not (vault / ".obsidian").is_dir():
        return _argument_error(operation, "invalid_vault", "Vault root must contain a .obsidian directory.")
    if not config.is_file():
        return _argument_error(operation, "missing_config", f"Obsidian config is missing: {config}")
    try:
        configured = json.loads(config.read_text(encoding="utf-8-sig"))
        configured_vault = Path(configured["vault_root"]).expanduser().resolve()
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        return _argument_error(operation, "invalid_config", f"Obsidian config is unreadable: {exc}")
    if configured_vault != vault:
        return _argument_error(
            operation,
            "configured_vault_mismatch",
            f"Configured Vault does not match the confirmed Vault: {configured_vault}",
        )
    validation = validate_integrations(
        skills_home,
        required={"obsidian_setup", "obsidian_local_kb"},
    )
    if not validation["ok"]:
        return {
            **_argument_error(operation, "missing_dependencies", "Required runtime skills are unavailable."),
            "diagnostics": validation["diagnostics"],
        }
    paths = locate_integrations(skills_home)
    command = [
        str(python_executable or sys.executable),
        "-B",
        str(paths.obsidian_setup),
        "check",
        "--vault-root",
        str(vault),
        "--config",
        str(config),
    ]
    if state_dir is not None:
        command.extend(["--state-dir", str(Path(state_dir).expanduser().resolve())])
    return _run_json_process(
        command,
        cwd=paths.obsidian_setup.parent,
        operation=operation,
        environment={"OBSIDIAN_VAULT_NOTES_CONFIG": str(config)},
    )


def refresh_obsidian_index(
    vault_root: str | Path,
    config_path: str | Path,
    *,
    python_executable: str | Path | None = None,
) -> dict[str, Any]:
    """Incrementally refresh the receiver's existing relative-path note index."""

    operation = "obsidian_index_refresh"
    vault = Path(vault_root).expanduser().resolve()
    config = Path(config_path).expanduser().resolve()
    if not vault.is_dir() or not (vault / ".obsidian").is_dir():
        return _argument_error(operation, "invalid_vault", "Vault root must contain a .obsidian directory.")
    try:
        receiver = json.loads(config.read_text(encoding="utf-8-sig"))
        if not isinstance(receiver, dict) or receiver.get("schema_version") != 1:
            raise ValueError("schema_version must be 1")
        configured_vault = Path(str(receiver["vault_root"])).expanduser().resolve()
        kb_root = Path(str(receiver["kb_root"])).expanduser().resolve()
        database = Path(str(receiver["db_path"])).expanduser().resolve()
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        return _argument_error(operation, "invalid_config", f"Obsidian config is unreadable: {exc}")
    if configured_vault != vault:
        return _argument_error(
            operation,
            "configured_vault_mismatch",
            f"Configured Vault does not match the relinked Vault: {configured_vault}",
        )
    if not (kb_root / "obsidian_local_kb" / "__main__.py").is_file():
        return _argument_error(operation, "missing_indexer", f"Bundled note indexer is missing: {kb_root}")
    command = [
        str(python_executable or sys.executable),
        "-B",
        "-m",
        "obsidian_local_kb",
        "refresh",
        "--vault",
        str(vault),
        "--db",
        str(database),
        "--json",
    ]
    return _run_json_process(
        command,
        cwd=kb_root,
        operation=operation,
        environment={
            "OBSIDIAN_VAULT_NOTES_CONFIG": str(config),
            "PYTHONPATH": str(kb_root),
        },
    )


def run_paper_search(
    query: str,
    db_path: str | Path,
    *,
    aliases: Iterable[str] = (),
    output_mode: str = "compact",
    limit: int = 12,
    skills_home: str | Path | None = None,
    python_executable: str | Path | None = None,
) -> dict[str, Any]:
    """Run ``pdf-paper-search`` against one explicit compatible database."""

    operation = "paper_search"
    cleaned_query = query.strip()
    database = Path(db_path).expanduser().resolve()
    if not cleaned_query:
        return _argument_error(operation, "empty_query", "Paper search query must not be empty.")
    if not database.is_file():
        return _argument_error(operation, "missing_db", f"PDF index is missing: {database}")
    if output_mode not in {"compact", "json"}:
        return _argument_error(operation, "invalid_output_mode", "Output mode must be compact or json.")
    if not isinstance(limit, int) or limit < 1:
        return _argument_error(operation, "invalid_limit", "Search limit must be a positive integer.")
    validation = validate_integrations(
        skills_home,
        required={"obsidian_local_kb", "pdf_paper_search"},
    )
    if not validation["ok"]:
        return {
            **_argument_error(operation, "missing_dependencies", "Required runtime skills are unavailable."),
            "diagnostics": validation["diagnostics"],
        }
    paths = locate_integrations(skills_home)
    command = [
        str(python_executable or sys.executable),
        "-B",
        str(paths.pdf_paper_search),
        "--query",
        cleaned_query,
        "--db",
        str(database),
        "--limit",
        str(limit),
    ]
    seen_aliases: set[str] = set()
    for alias in aliases:
        cleaned = str(alias).strip()
        key = cleaned.casefold()
        if cleaned and key not in seen_aliases and cleaned != cleaned_query:
            seen_aliases.add(key)
            command.extend(["--alias", cleaned])
    command.append("--compact" if output_mode == "compact" else "--json")
    result = _run_json_process(
        command,
        cwd=paths.pdf_paper_search.parent,
        operation=operation,
        environment={"OBSIDIAN_LOCAL_KB_ROOT": str(paths.obsidian_local_kb_root)},
    )
    data = result.get("data")
    diagnostics = data.get("diagnostics", []) if isinstance(data, dict) else []
    downstream_errors = [
        item
        for item in diagnostics
        if isinstance(item, dict) and str(item.get("level", "")).casefold() == "error"
    ]
    if downstream_errors:
        result["ok"] = False
        result["status"] = "failed"
        result["error"] = {
            "code": "downstream_diagnostics",
            "message": f"pdf-paper-search reported {len(downstream_errors)} error diagnostic(s)",
        }
    return result


PAPER_LOCATE_SCHEMA_VERSION = "paper-locate/v1"
PAPER_CANONICALIZER_VERSION = "paper-canonical/v1"
_PAPER_LOCATE_STATUSES = {
    "verified_hit",
    "ambiguous",
    "not_found_in_indexed_text",
    "coverage_gap",
    "failed",
}
_PAPER_LOCATE_SUCCESS_STATUSES = _PAPER_LOCATE_STATUSES - {"failed"}
_PAPER_LOCATE_FEATURES = {
    "page_role",
    "local_statement",
    "direct_statement",
    "hard_concepts_required",
    "hard_concepts_found",
    "hard_concepts_missing",
    "overview_reference",
    "hint_solution",
    "proof_ingredient",
    "noisy_page",
    "classification",
}


def _bounded_text(value: object, limit: int) -> str:
    compact = " ".join(str(value or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 1)].rstrip() + "…"


def _bounded_string_list(value: object, *, count: int, chars: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        text
        for text in (_bounded_text(item, chars) for item in value[:count])
        if text
    ]


def _nonnegative_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return max(0, result)


def _optional_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _nonnegative_number(value: object) -> int | float:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    try:
        return round(max(0.0, float(value)), 3)
    except (TypeError, ValueError, OverflowError):
        return 0


def _safe_relative_pdf_path(value: object) -> str | None:
    spec = str(value or "").strip().replace("\\", "/")
    if not spec or ":" in spec:
        return None
    posix = PurePosixPath(spec)
    windows = PureWindowsPath(spec)
    if posix.is_absolute() or windows.is_absolute() or windows.drive:
        return None
    if any(part in {"", ".", ".."} for part in posix.parts):
        return None
    if posix.suffix.casefold() != ".pdf":
        return None
    return posix.as_posix()


def _compact_paper_features(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for key in _PAPER_LOCATE_FEATURES:
        item = value.get(key)
        if isinstance(item, bool) or item is None:
            result[key] = item
        elif isinstance(item, (int, float)):
            result[key] = item
        elif isinstance(item, list):
            result[key] = _bounded_string_list(item, count=12, chars=80)
        elif isinstance(item, str):
            result[key] = _bounded_text(item, 120)
    return result


def _compact_diagnostics_summary(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for key, item in list(value.items())[:12]:
        safe_key = _bounded_text(key, 64)
        if not safe_key:
            continue
        if isinstance(item, bool) or item is None or isinstance(item, (int, float)):
            result[safe_key] = item
        elif isinstance(item, str):
            result[safe_key] = _bounded_text(item, 160)
    return result


def _compact_paper_warning(value: object) -> dict[str, str] | str | None:
    if isinstance(value, str):
        return _bounded_text(value, 240)
    if not isinstance(value, dict):
        return None
    warning: dict[str, str] = {}
    for key in ("level", "stage", "code", "message"):
        item = value.get(key)
        if item is not None:
            warning[key] = _bounded_text(item, 240 if key == "message" else 80)
    return warning or None


def _paper_locate_error(code: str, message: str) -> dict[str, Any]:
    return {
        "schema_version": PAPER_LOCATE_SCHEMA_VERSION,
        "canonicalizer_version": PAPER_CANONICALIZER_VERSION,
        "ok": False,
        "status": "failed",
        "route": "explicit-index",
        "query": {"query_type": None, "hard_concepts": [], "signature_terms": []},
        "search": {
            "alias_mode": None,
            "expanded": False,
            "candidate_count": 0,
            "candidate_pdf_count": 0,
            "verified_page_count": 0,
            "adjacent_page_count": 0,
            "stop_reason": code,
        },
        "results": [],
        "coverage": {
            "document_status_counts": {},
            "incomplete": True,
            "diagnostics_summary": {},
            "warnings": [],
        },
        "timing_ms": {"core": 0, "expanded": 0, "verify": 0, "total": 0},
        "error": {"code": code, "message": message},
    }


def _compact_paper_locate_payload(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return _paper_locate_error(
            "invalid_paper_locate_contract",
            "pdf-paper-search did not return a JSON object.",
        )
    status = str(payload.get("status", ""))
    raw_results = payload.get("results")
    if (
        payload.get("schema_version") != PAPER_LOCATE_SCHEMA_VERSION
        or payload.get("canonicalizer_version") != PAPER_CANONICALIZER_VERSION
        or payload.get("route") != "explicit-index"
        or status not in _PAPER_LOCATE_STATUSES
        or not isinstance(raw_results, list)
        or len(raw_results) > 3
    ):
        return _paper_locate_error(
            "invalid_paper_locate_contract",
            "pdf-paper-search returned an incompatible paper-locate/v1 package.",
        )

    query = payload.get("query") if isinstance(payload.get("query"), dict) else {}
    search = payload.get("search") if isinstance(payload.get("search"), dict) else {}
    timing = payload.get("timing_ms") if isinstance(payload.get("timing_ms"), dict) else {}
    coverage = payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {}
    compact_results: list[dict[str, Any]] = []
    result_paths: set[str] = set()
    for raw_result in raw_results:
        if not isinstance(raw_result, dict):
            continue
        path = _safe_relative_pdf_path(raw_result.get("path"))
        page = raw_result.get("pdf_page")
        if not path or not isinstance(page, int) or isinstance(page, bool) or page < 1:
            return _paper_locate_error(
                "invalid_paper_locate_result",
                "A paper-locate result is missing an exact relative PDF path or one-based page.",
            )
        path_key = path.casefold()
        if path_key in result_paths:
            return _paper_locate_error(
                "duplicate_paper_locate_pdf",
                "A bounded answer package may contain at most one result per PDF.",
            )
        result_paths.add(path_key)
        if raw_result.get("printed_page") is not None:
            return _paper_locate_error(
                "unverified_printed_page",
                "A printed page must remain null unless a separate verifier establishes it.",
            )
        origin = raw_result.get("origin")
        statement_window = raw_result.get("statement_window")
        extraction = raw_result.get("extraction")
        seed_page = _optional_int(raw_result.get("seed_page"))
        adjacent_offset = _optional_int(raw_result.get("adjacent_offset"))
        if (
            origin not in {"retrieved", "adjacent"}
            or not isinstance(statement_window, str)
            or not statement_window.strip()
            or not isinstance(extraction, dict)
            or seed_page is None
            or seed_page < 1
            or adjacent_offset is None
            or (
                origin == "retrieved"
                and (adjacent_offset != 0 or seed_page != page)
            )
            or (
                origin == "adjacent"
                and (
                    abs(adjacent_offset) != 1
                    or seed_page + adjacent_offset != page
                )
            )
        ):
            return _paper_locate_error(
                "invalid_paper_locate_result",
                "A result must include origin, statement_window, and extraction evidence.",
            )
        compact_results.append(
            {
                "rank": _nonnegative_int(
                    raw_result.get("rank"), len(compact_results) + 1
                ),
                "title": _bounded_text(raw_result.get("title"), 300),
                "path": path,
                "pdf_page": page,
                "printed_page": None,
                "match_type": _bounded_text(raw_result.get("match_type"), 40),
                "confidence": _bounded_text(raw_result.get("confidence"), 24),
                "verified": raw_result.get("verified") is True,
                "origin": str(origin),
                "seed_page": seed_page,
                "adjacent_offset": adjacent_offset,
                "statement_window": _bounded_text(
                    statement_window, 1000
                ),
                "why": _bounded_string_list(raw_result.get("why"), count=4, chars=200),
                "extraction": {
                    "status": _bounded_text(
                        (
                            extraction.get("status")
                        ),
                        40,
                    )
                    or "unknown",
                    "method": _bounded_text(
                        (
                            extraction.get("method")
                        ),
                        80,
                    )
                    or None,
                    "warning": _bounded_text(
                        (
                            extraction.get("warning")
                        ),
                        240,
                    )
                    or None,
                },
                "features": _compact_paper_features(raw_result.get("features")),
            }
        )
    raw_warnings = coverage.get("warnings")
    warnings = []
    if isinstance(raw_warnings, list):
        for item in raw_warnings[:4]:
            compact = _compact_paper_warning(item)
            if compact is not None:
                warnings.append(compact)
    result = {
        "schema_version": PAPER_LOCATE_SCHEMA_VERSION,
        "canonicalizer_version": PAPER_CANONICALIZER_VERSION,
        "ok": status in _PAPER_LOCATE_SUCCESS_STATUSES,
        "status": status,
        "route": "explicit-index",
        "query": {
            "query_type": _bounded_text(query.get("query_type"), 80) or None,
            "hard_concepts": _bounded_string_list(
                query.get("hard_concepts"), count=12, chars=100
            ),
            "signature_terms": _bounded_string_list(
                query.get("signature_terms"), count=16, chars=100
            ),
        },
        "search": {
            "alias_mode": _bounded_text(search.get("alias_mode"), 40) or None,
            "expanded": bool(search.get("expanded", False)),
            "candidate_count": _nonnegative_int(search.get("candidate_count")),
            "candidate_pdf_count": _nonnegative_int(search.get("candidate_pdf_count")),
            "verified_page_count": _nonnegative_int(search.get("verified_page_count")),
            "adjacent_page_count": _nonnegative_int(search.get("adjacent_page_count")),
            "stop_reason": _bounded_text(search.get("stop_reason"), 160),
        },
        "results": compact_results,
        "coverage": {
            "document_status_counts": {
                key: _nonnegative_int(value)
                for key, value in (
                    coverage.get("document_status_counts", {}).items()
                    if isinstance(coverage.get("document_status_counts"), dict)
                    else []
                )
                if str(key) in {"pending", "indexed", "no_text", "error"}
            },
            "incomplete": bool(coverage.get("incomplete", False)),
            "diagnostics_summary": _compact_diagnostics_summary(
                coverage.get("diagnostics_summary")
            ),
            "warnings": warnings,
        },
        "timing_ms": {
            key: _nonnegative_number(timing.get(key))
            for key in ("core", "expanded", "verify", "total")
        },
    }
    if status == "verified_hit":
        if not compact_results:
            return _paper_locate_error(
                "invalid_verified_hit",
                "A verified_hit package must contain at least one verified result.",
            )
        primary = compact_results[0]
        features = primary["features"]
        if (
            primary["match_type"] != "exact hit"
            or primary["verified"] is not True
            or features.get("local_statement") is not True
            or features.get("direct_statement") is not True
            or bool(features.get("hard_concepts_missing"))
        ):
            return _paper_locate_error(
                "invalid_verified_hit",
                "A verified_hit must retain exact local/direct evidence with no missing hard concept.",
            )
    encoded_size = len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    if encoded_size > 10 * 1024:
        result["query"]["hard_concepts"] = [
            _bounded_text(value, 60)
            for value in result["query"]["hard_concepts"][:8]
        ]
        result["query"]["signature_terms"] = [
            _bounded_text(value, 60)
            for value in result["query"]["signature_terms"][:8]
        ]
        for item in compact_results:
            item["statement_window"] = _bounded_text(item["statement_window"], 360)
            item["why"] = item["why"][:2]
            features = item["features"]
            item["features"] = {
                key: features[key]
                for key in (
                    "page_role",
                    "local_statement",
                    "direct_statement",
                    "hard_concepts_required",
                    "hard_concepts_found",
                    "hard_concepts_missing",
                )
                if key in features
            }
            for key in ("hard_concepts_required", "hard_concepts_found"):
                values = item["features"].get(key)
                if isinstance(values, list):
                    item["features"][key] = [
                        _bounded_text(value, 60) for value in values[:8]
                    ]
        result["coverage"]["warnings"] = result["coverage"]["warnings"][:2]
        encoded_size = len(
            json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
    if encoded_size > 10 * 1024:
        for key in ("hard_concepts", "signature_terms"):
            result["query"][key] = [
                _bounded_text(value, 40) for value in result["query"][key][:4]
            ]
        for item in compact_results:
            item["statement_window"] = _bounded_text(item["statement_window"], 240)
            item["why"] = [
                _bounded_text(value, 100) for value in item["why"][:2]
            ]
            for key in ("hard_concepts_required", "hard_concepts_found"):
                values = item["features"].get(key)
                if isinstance(values, list):
                    item["features"][key] = [
                        _bounded_text(value, 40) for value in values[:4]
                    ]
        result["coverage"]["warnings"] = [
            {
                key: warning[key]
                for key in ("level", "stage", "code")
                if isinstance(warning, dict) and key in warning
            }
            if isinstance(warning, dict)
            else _bounded_text(warning, 80)
            for warning in result["coverage"]["warnings"][:2]
        ]
        encoded_size = len(
            json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
    if encoded_size > 10 * 1024:
        return _paper_locate_error(
            "paper_locate_output_too_large",
            "The bounded upstream package still exceeds its 10 KiB adapter budget.",
        )
    return result


def run_paper_locate(
    query: str,
    db_path: str | Path,
    *,
    aliases: Iterable[str] = (),
    limit: int = 12,
    skills_home: str | Path | None = None,
    python_executable: str | Path | None = None,
) -> dict[str, Any]:
    """Return one bounded answer-ready package from one explicit PDF index."""

    cleaned_query = query.strip()
    database = Path(db_path).expanduser().resolve()
    if not cleaned_query:
        return _paper_locate_error("empty_query", "Paper locate query must not be empty.")
    if not database.is_file():
        return _paper_locate_error("missing_db", "The configured PDF index is missing.")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
        return _paper_locate_error(
            "invalid_limit", "Paper locate limit must be an integer from 1 through 50."
        )
    validation = validate_integrations(
        skills_home,
        required={"pdf_paper_locate", "pdf_observer_run"},
    )
    if not validation["ok"]:
        return _paper_locate_error(
            "missing_dependencies",
            "The pdf-paper-search paper-locate entry or its Observer wrapper is unavailable.",
        )
    paths = locate_integrations(skills_home)
    command = [
        str(python_executable or sys.executable),
        "-B",
        str(paths.pdf_observer_run),
        "--skill",
        "pdf-paper-search",
        "--catalog",
        "pdf-paper-search/v1",
        "--phase",
        "pdf-paper-search.script.paper_locate",
        "--",
        str(python_executable or sys.executable),
        "-B",
        str(paths.pdf_paper_locate),
        "--query",
        cleaned_query,
        "--db",
        str(database),
        "--limit",
        str(limit),
    ]
    seen_aliases: set[str] = set()
    for alias in aliases:
        cleaned = str(alias).strip()
        key = cleaned.casefold()
        if cleaned and key not in seen_aliases and cleaned != cleaned_query:
            seen_aliases.add(key)
            command.extend(["--alias", cleaned])
    command.append("--json")
    completed = _run_json_process(
        command,
        cwd=paths.pdf_paper_locate.parent,
        operation="paper-locate",
        environment={"OBSIDIAN_LOCAL_KB_ROOT": str(paths.obsidian_local_kb_root)},
    )
    compact = _compact_paper_locate_payload(completed.get("data"))
    if compact.get("error") is not None:
        return compact
    status = compact["status"]
    expected_returncode = 2 if status == "coverage_gap" else 1 if status == "failed" else 0
    if completed.get("returncode") != expected_returncode:
        return _paper_locate_error(
            "paper_locate_exit_mismatch",
            "pdf-paper-search returned an exit code inconsistent with its answer status.",
        )
    return compact
