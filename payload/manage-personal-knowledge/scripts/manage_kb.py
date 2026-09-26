from __future__ import annotations

from mpk_public_output import SafeParser, public_main

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any

from mpk_views import configuration_view, library_status_view, question_result_groups, ocr_preflight_view, suite_status_view, registry_status_view, transaction_result_view, configuration_result_view, library_search_view, paper_search_result_view, library_index_view, question_coverage_view, question_index_result_view, question_inventory_options, question_status_view, question_discovery_view, ocr_one_view, resource_audit_view, AUDIT_CATEGORIES, registry_configuration_view

from manage_personal_knowledge import (
    config,
    discovery,
    integrations,
    library,
    ocr,
    operations,
    questions,
    question_collections,
    references,
    registry,
)
from manage_personal_knowledge.text import find_pdftotext


def configure_streams() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def emit(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def state_dir() -> Path:
    return config.get_state_dir()


def library_db() -> Path:
    return state_dir() / "library.sqlite3"


def registry_db() -> Path:
    return state_dir() / "registry.sqlite3"


def questions_db() -> Path:
    return state_dir() / "questions.sqlite3"


def load_required_config(config_path: str | None) -> dict[str, object]:
    payload = config.load_config(config_path, required=True)
    assert payload is not None
    return payload


def registry_context(config_path: str | None) -> tuple[dict[str, object], Path, Path, Path]:
    saved = load_required_config(config_path)
    if int(saved.get("schema_version", 0)) != 2:
        raise RuntimeError("The resource registry is not initialized; run registry-init and confirm its dry-run plan")
    root = Path(str(saved["knowledge_root"])).expanduser().resolve(strict=False)
    if not root.is_dir():
        raise RuntimeError("The configured knowledge root is missing; use root-relink for an initialized root")
    registry_config = saved.get("registry")
    if not isinstance(registry_config, dict):
        raise RuntimeError("Schema v2 config is missing registry settings")
    relative = str(registry_config.get("manifest_relative_path", ".mpk/resources.jsonl"))
    manifest = (root / Path(relative)).resolve(strict=False)
    try:
        manifest.relative_to(root)
    except ValueError as exc:
        raise RuntimeError("Configured registry manifest escapes the knowledge root") from exc
    database = registry_db()
    marker_path = root / ".mpk" / "root.json"
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Knowledge-root identity marker is missing or unreadable: {marker_path}: {exc}") from exc
    configured_id = str(saved.get("knowledge_root_id") or "")
    marker_id = str(marker.get("knowledge_root_id") or "")
    if marker_id != configured_id:
        raise RuntimeError("Config knowledge_root_id does not match .mpk/root.json")
    with registry._connect_read_only(database) as connection:
        if not registry._table_exists(connection, "registry_meta"):
            raise RuntimeError("Registry database is not initialized")
        database_id = str(registry._meta_get(connection, "knowledge_root_id") or "")
    if database_id != configured_id:
        raise RuntimeError("Config knowledge_root_id does not match registry.sqlite3")
    return saved, root, database, manifest


def library_identity(saved: dict[str, object]) -> tuple[str | None, str | None]:
    if int(saved.get("schema_version", 0)) != 2:
        return None, None
    sources = saved.get("sources")
    library_source = sources.get("library") if isinstance(sources, dict) else None
    if not isinstance(library_source, dict):
        return None, None
    return str(saved.get("knowledge_root_id")), str(library_source.get("relative_path"))


def require_healthy_source(config_path: str | None, source_name: str) -> tuple[dict[str, object], Path]:
    report = config.status(config_path)
    if not report.get("configured"):
        raise RuntimeError("manage-personal-knowledge is not configured")
    source_report = report.get("sources", {}).get(source_name, {})
    if not report.get("healthy") or source_report.get("state") != "healthy":
        missing = ", ".join(str(item) for item in report.get("missing", [])) or source_name
        raise RuntimeError(
            f"Configured source is unavailable ({missing}); run status and obtain an explicit relink, forget, or keep decision"
        )
    saved = load_required_config(config_path)
    return saved, config.resolve_source(saved, source_name, strict=True)


def question_context(config_path: str | None) -> tuple[dict[str, object], Path, Path, str]:
    saved, vault_root = require_healthy_source(config_path, "vault")
    if int(saved.get("schema_version", 0)) != 2:
        raise RuntimeError("Question collection management requires schema-v2 knowledge-root identity")
    root_id = str(saved.get("knowledge_root_id") or "")
    knowledge_root = Path(str(saved["knowledge_root"])).expanduser().resolve(strict=True)
    if not root_id:
        raise RuntimeError("Question collection management requires knowledge_root_id")
    return saved, knowledge_root, vault_root, root_id


def require_matching_library_index(config_path: str | None) -> tuple[dict[str, object], Path]:
    saved, root = require_healthy_source(config_path, "library")
    root_id, relative_path = library_identity(saved)
    coverage = library.library_status(
        library_db(),
        root,
        knowledge_root_id=root_id,
        library_relative_path=relative_path,
    )
    if not coverage.get("database_exists"):
        raise RuntimeError("The PDF library index does not exist; run index --resume first")
    if coverage.get("root_matches_config") is not True:
        raise RuntimeError(
            "The PDF index belongs to a different library root; run index --resume for the confirmed library before searching"
        )
    return saved, root


def require_current_library_search_format(
    config_path: str | None,
) -> tuple[dict[str, object], Path, dict[str, object]]:
    saved, root = require_matching_library_index(config_path)
    root_id, relative_path = library_identity(saved)
    coverage = library.library_status(
        library_db(),
        root,
        knowledge_root_id=root_id,
        library_relative_path=relative_path,
    )
    if coverage.get("search_format_current") is not True:
        raise RuntimeError(
            "The PDF search index uses an outdated lexical format; run "
            "`index --resume` to rebuild FTS from existing extracted page text "
            "without re-extracting PDFs"
        )
    return saved, root, coverage


def obsidian_receiver_paths() -> tuple[Path, Path]:
    if sys.platform.startswith("win"):
        appdata = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        appdata = local = Path.home() / "Library" / "Application Support"
    else:
        appdata = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        local = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return (
        (appdata / "obsidian-vault-notes" / "config.json").resolve(),
        (local / "obsidian-vault-notes").resolve(),
    )


def command_discover(args: argparse.Namespace) -> dict[str, object]:
    limit=getattr(args,"limit",20);offset=getattr(args,"offset",0)
    if not 1<=limit<=100 or offset<0:raise ValueError("Invalid discovery page")
    result=discovery.discover_sources(args.root,max_directories=getattr(args,"max_directories",5000),max_entries=getattr(args,"max_entries",50000))
    for key in ("vault_candidates","library_candidates"):
        result[key]=result[key][offset:offset+limit]
    result["candidate_page"]={"offset":offset,"limit_per_kind":limit,
        "complete": result["scan_complete"] and offset==0 and max(result["counts"]["vault_candidates"],result["counts"]["library_candidates"])<=limit}
    return result


def command_configure(args: argparse.Namespace) -> dict[str, object]:
    saved = config.configure(
        args.root,
        args.vault,
        args.library,
        confirmed=args.yes,
        config_path=args.config,
    )
    result: dict[str, object] = {
        "ok": True,
        "status": "configured",
        "config_path": str(config.get_config_path() if args.config is None else Path(args.config).resolve()),
        "config": saved,
    }
    if args.configure_obsidian:
        try:
            receiver_config, receiver_state = obsidian_receiver_paths()
            if args.obsidian_config:
                receiver_config = Path(args.obsidian_config).expanduser().resolve()
            if args.obsidian_state:
                receiver_state = Path(args.obsidian_state).expanduser().resolve()
            result["obsidian"] = integrations.configure_obsidian(
                config.resolve_source(saved, "vault", strict=True),
                receiver_config,
                receiver_state,
                build_index=args.build_vault_index,
                confirmed_replace=args.yes,
            )
            if not result["obsidian"].get("ok", False):
                result["ok"] = False
                result["status"] = "configured_with_obsidian_error"
        except Exception:
            # configure() has returned after saving; a receiver failure cannot undo that fact.
            result['ok'] = False
            result['obsidian'] = {'ok':False, 'error':{'code':'integration_error'}}
    return configuration_result_view(result)


def command_status(args: argparse.Namespace) -> dict[str, object]:
    diagnostic = getattr(args, "diagnostics", False)
    purpose = getattr(args, "diagnostic_purpose", None)
    if diagnostic and (not isinstance(purpose, str) or not purpose.strip() or len(purpose) > 512):
        raise ValueError("Diagnostic expansion requires a specific purpose")
    if not diagnostic and purpose is not None:
        raise ValueError("Diagnostic purpose requires --diagnostics")
    source_status = config.status(args.config)
    if not diagnostic:
        root = None
        root_id = relative_path = None
        if source_status.get("configured"):
            saved = config.load_config(args.config, required=True)
            root_id, relative_path = library_identity(saved)
            if source_status.get("sources", {}).get("library", {}).get("state") == "healthy":
                root = config.resolve_source(saved, "library")
        coverage = library_status_view(library.library_status(library_db(), root, knowledge_root_id=root_id, library_relative_path=relative_path))
        healthy = source_status.get("healthy") is True
        format_ok = not (coverage.get("database_exists") is True and coverage.get("search_format_current") is not True)
        identity_ok = coverage.get("identity_matches_config") is not False and coverage.get("root_matches_config") is not False
        return {"ok": healthy and format_ok and identity_ok,
                "status": "healthy" if healthy and format_ok and identity_ok else "attention_required",
                "status_scope": "configured_sources_and_library_index",
                "configuration": configuration_view(source_status), "index_coverage": coverage,
                "diagnostics_included": False, "question_coverage_checked": False,
                "next_checks": ["question-status for question coverage", "ocr-preflight for OCR", "status --diagnostics with a purpose for full suite diagnostics"]}

    pdftotext_path = find_pdftotext()
    integration_status = integrations.validate_integrations()
    ocr_status = ocr_preflight_view(ocr.preflight_ocr(state_dir()))
    result: dict[str, object] = {
        "ok": False,
        "status": "attention_required",
        "configuration": source_status,
        "state_dir": str(state_dir()),
        "library_db": str(library_db()),
        "questions_db": str(questions_db()),
        "dependencies": {
            "pdftotext": {
                "ok": pdftotext_path is not None,
                "path": str(pdftotext_path) if pdftotext_path is not None else None,
            }
        },
        "integrations": integration_status,
        "ocr": ocr_status,
        "obsidian": None,
    }
    if source_status.get("configured"):
        saved = config.load_config(args.config, required=True)
        assert saved is not None
        sources = source_status.get("sources", {})
        library_state = sources.get("library", {}) if isinstance(sources, dict) else {}
        root = None
        if isinstance(library_state, dict) and library_state.get("state") == "healthy":
            root = config.resolve_source(saved, "library")
        root_id, relative_path = library_identity(saved)
        result["index_coverage"] = library.library_status(
            library_db(),
            root,
            knowledge_root_id=root_id,
            library_relative_path=relative_path,
        )
        vault_state = sources.get("vault", {}) if isinstance(sources, dict) else {}
        if isinstance(vault_state, dict) and vault_state.get("state") == "healthy":
            receiver_config, receiver_state = obsidian_receiver_paths()
            result["obsidian"] = integrations.check_obsidian(
                config.resolve_source(saved, "vault"),
                receiver_config,
                state_dir=receiver_state,
            )
        try:
            result["question_index"] = command_question_status(
                argparse.Namespace(
                    config=args.config,
                    discovery_relative=question_collections.DEFAULT_DISCOVERY_RELATIVE,
                    max_directories=5000,
                )
            )
        except Exception as exc:
            result["question_index"] = {
                "ok": False,
                "status": "coverage_gap",
                "error": str(exc),
            }
    else:
        result["index_coverage"] = library.library_status(library_db())
        result["question_index"] = questions.question_status(questions_db())
    if source_status.get("configured") and int(source_status.get("schema_version", 0)) == 2 and registry_db().is_file():
        try:
            _, registry_root, _, manifest = registry_context(args.config)
            result["resource_registry"] = registry.registry_status(
                registry_root,
                registry_db(),
                manifest_path=manifest,
                include_inventory=False,
            )
        except Exception as exc:
            result["resource_registry"] = {"ok": False, "error": str(exc)}
    else:
        result["resource_registry"] = {"initialized": False}
    obsidian_ok = result["obsidian"] is None or result["obsidian"].get("ok", False)
    index_coverage = result.get("index_coverage")
    search_format_ok = not (
        isinstance(index_coverage, dict)
        and index_coverage.get("database_exists") is True
        and index_coverage.get("search_format_current") is not True
    )
    question_index = result.get("question_index")
    question_coverage_ok = not isinstance(question_index, dict) or question_index.get("status") in {
        "ready",
        "not_initialized",
    }
    result["ok"] = bool(
        source_status.get("healthy")
        and integration_status.get("ok")
        and pdftotext_path is not None
        and ocr_status.get("ok")
        and obsidian_ok
        and search_format_ok
        and question_coverage_ok
    )
    result["status"] = "healthy" if result["ok"] else "attention_required"
    return suite_status_view(result)


def command_question_index(args: argparse.Namespace) -> dict[str, object]:
    saved, vault_root = require_healthy_source(args.config, "vault")
    root_id = str(saved.get("knowledge_root_id") or "")
    if not root_id:
        raise RuntimeError("Question indexing requires schema-v2 knowledge-root identity")
    result = questions.index_questions(
        vault_root,
        questions_db(),
        source_relative=args.source_relative,
        knowledge_root_id=root_id,
    )
    return question_index_result_view(result)


def command_question_status(args: argparse.Namespace) -> dict[str, object]:
    include_inventory, page_limit, page_offset = question_inventory_options(args)
    _, knowledge_root, _, root_id = question_context(args.config)
    discovery_relative = getattr(args, "discovery_relative", question_collections.DEFAULT_DISCOVERY_RELATIVE)
    max_directories = getattr(args, "max_directories", 5000)
    legacy = questions.question_status(questions_db(), knowledge_root_id=root_id)
    collections = question_collections.collection_status(
        questions_db(),
        knowledge_root,
        knowledge_root_id=root_id,
        discovery_relative=discovery_relative,
        max_directories=max_directories,
        max_entries=getattr(args, "max_entries", 50000),
    )
    coverage_complete = bool(
        legacy.get("coverage_complete") is True
        and collections.get("coverage_complete") is True
        and legacy.get("root_matches_config") is True
        and collections.get("root_matches_config") is True
    )
    result = {
        "ok": bool(legacy.get("ok") and collections.get("ok")),
        "status": "ready" if coverage_complete else "coverage_gap",
        "schema_version": "mpk-question-federation/v1",
        "root_matches_config": bool(
            legacy.get("root_matches_config") is True and collections.get("root_matches_config") is True
        ),
        "docs": int(legacy.get("docs", 0)) + int(collections.get("documents", 0)),
        "questions": int(legacy.get("questions", 0)) + int(collections.get("questions", 0)),
        "sources": legacy.get("sources", []),
        "document_index": legacy,
        "question_collections": collections,
        "discovered_unimported": collections.get("discovered_unimported", []),
        "coverage_complete": coverage_complete,
        "coverage_scope": {
            "vault_registered_sources": True,
            "knowledge_root_discovery_relative": discovery_relative,
            "discovered_external_collections": True,
        },
    }

    return question_status_view(result, include_inventory=include_inventory, limit=page_limit, offset=page_offset)


def command_question_discover(args: argparse.Namespace) -> dict[str, object]:
    _, page_limit, page_offset = question_inventory_options(args)
    _, knowledge_root, _, _ = question_context(args.config)
    result = question_collections.discover_collections(
        knowledge_root,
        questions_db(),
        discovery_relative=args.discovery_relative,
        max_directories=args.max_directories,
        max_entries=getattr(args, "max_entries", 50000),
    )

    return question_discovery_view(result, limit=page_limit, offset=page_offset)


def command_question_import(args: argparse.Namespace) -> dict[str, object]:
    _, knowledge_root, vault_root, root_id = question_context(args.config)
    if args.scope == "vault":
        return questions.guarded_index_questions(
            vault_root,
            questions_db(),
            source_relative=args.source_relative,
            knowledge_root_id=root_id,
            write=args.write,
            expect_plan_sha256=args.expect_plan_sha256,
        )
    return question_collections.import_collection(
        knowledge_root,
        questions_db(),
        source_relative=args.source_relative,
        knowledge_root_id=root_id,
        write=args.write,
        expect_plan_sha256=args.expect_plan_sha256,
    )


def command_question_import_probe(args: argparse.Namespace) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="mpk-question-probe-") as temporary:
        # Resolve the temporary root: it can sit below a platform symlink.
        root = Path(temporary).resolve() / "knowledge-root"
        collection = root / "数据" / "题库" / "probe"
        collection.mkdir(parents=True)
        (collection / "probe.md").write_text(
            "# Probe\n\n## Section\n\n### 1\n\nBound the running time of one merge step.\n",
            encoding="utf-8",
        )
        (collection / "source-manifest.json").write_text("{}\n", encoding="utf-8")
        database = Path(temporary).resolve() / "state" / "questions.sqlite3"
        root_id = "KBROOT-AAAAAAAAAAAAAAAAAAAAAAAAAA"
        relative = collection.relative_to(root).as_posix()
        plan = question_collections.import_collection(
            root, database, source_relative=relative, knowledge_root_id=root_id
        )
        if args.case == "fault":
            try:
                question_collections.import_collection(
                    root,
                    database,
                    source_relative=relative,
                    knowledge_root_id=root_id,
                    write=True,
                    expect_plan_sha256="0" * 64,
                )
            except ValueError:
                return {
                    "ok": True,
                    "status": "probe_recovered",
                    "terminal_state": "recovered",
                    "blocked_path": "stale_plan_hash",
                }
            raise RuntimeError("Question import probe failed to block a stale plan hash")
        applied = question_collections.import_collection(
            root,
            database,
            source_relative=relative,
            knowledge_root_id=root_id,
            write=True,
            expect_plan_sha256=str(plan["plan_sha256"]),
        )
        results = question_collections.search_collections(
            database, "merge step", knowledge_root_id=root_id
        )
        if not applied.get("applied") or len(results) != 1:
            raise RuntimeError("Question import production probe did not reach searchable state")
        return {
            "ok": True,
            "status": "probe_success",
            "terminal_state": "success",
            "questions": int(applied["question_count"]),
        }


def command_question_search(args: argparse.Namespace) -> dict[str, object]:
    aliases = args.alias or []
    if type(args.limit) is not int or not 1 <= args.limit <= 100 or not isinstance(args.query, str) or not args.query.strip() or len(args.query) > 1024 or len(aliases) > 8 or any(not isinstance(alias, str) or not alias.strip() or len(alias) > 1024 for alias in aliases):
        raise ValueError("Question search requires bounded query, aliases and result count")
    _, knowledge_root, _, root_id = question_context(args.config)
    legacy = questions.search_questions(
        questions_db(),
        args.query,
        aliases=args.alias,
        limit=args.limit,
        year=args.year,
        paper=args.paper,
        knowledge_root_id=root_id,
    )
    external = question_collections.search_collections(
        questions_db(),
        args.query,
        aliases=args.alias,
        limit=args.limit,
        year=args.year,
        paper=args.paper,
        knowledge_root_id=root_id,
    )
    legacy_results = []
    for item in legacy.get("results", []):
        legacy_results.append({**item, "source_adapter": "vault_question_index"})
    merged = sorted([*legacy_results, *external], key=lambda item: float(item.get("score", 0.0)))[: args.limit]
    coverage = question_coverage_view(command_question_status(args))
    status = "candidate_hit" if merged else (
        "not_found_in_indexed_questions" if coverage.get("coverage_complete") is True else "coverage_gap"
    )
    return {
        "ok": True,
        "status": status,
        "query": args.query,
        "aliases": list(args.alias),
        "filters": {"year": args.year, "paper": args.paper},
        "results": merged,
        "result_groups": question_result_groups(merged),
        "result_groups_mode": "zero_based_indexes_into_results",
        "candidate_counts": {"vault_question_index": len(legacy_results), "question_collections": len(external)},
        "coverage": coverage,
        "stop_reason": "indexed_candidates" if merged else "no_indexed_match",
        "fallback_recommended": "none" if merged else "vault_or_pdf",
    }


def command_index(args: argparse.Namespace) -> dict[str, object]:
    saved, library_root = require_healthy_source(args.config, "library")
    root_id, relative_path = library_identity(saved)

    def progress(event: dict[str, object]) -> None:
        if args.progress:
            print(json.dumps(event, ensure_ascii=False), file=sys.stderr, flush=True)

    result = library.index_library(
        library_root,
        library_db(),
        pdftotext_exe=args.pdftotext,
        resume=args.resume,
        retry_errors=args.retry_errors,
        max_files=args.max_files,
        batch_size=args.batch_size,
        progress=progress,
        knowledge_root_id=root_id,
        library_relative_path=relative_path,
    )
    return library_index_view(result)


def command_library_search(args: argparse.Namespace) -> dict[str, object]:
    if type(args.limit) is not int or not 1 <= args.limit <= 100:
        raise ValueError("Library result limit must be between 1 and 100")
    aliases = args.alias or []
    if not isinstance(args.query, str) or not args.query.strip() or len(args.query) > 1024 or len(aliases) > 8 or any(not isinstance(alias, str) or not alias.strip() or len(alias) > 1024 for alias in aliases):
        raise ValueError("Library search requires a bounded query and at most eight bounded aliases")
    saved, _, _ = require_current_library_search_format(args.config)
    result = library.search_library(
        library_db(),
        args.query,
        aliases=args.alias,
        limit=args.limit,
    )
    _attach_resource_ids(result, saved)
    return library_search_view(result)


def command_paper_search(args: argparse.Namespace) -> dict[str, object]:
    aliases = args.alias or []
    if type(args.limit) is not int or not 1 <= args.limit <= 100 or not isinstance(args.query, str) or not args.query.strip() or len(args.query) > 1024 or len(aliases) > 8 or any(not isinstance(alias, str) or not alias.strip() or len(alias) > 1024 for alias in aliases):
        raise ValueError("Paper search requires bounded query, aliases and result count")
    saved, _, _ = require_current_library_search_format(args.config)
    output_mode = "json" if args.json else args.output_mode
    result = integrations.run_paper_search(
        args.query,
        library_db(),
        aliases=args.alias,
        output_mode=output_mode,
        limit=args.limit,
    )
    if isinstance(result, dict):
        _attach_resource_ids(result, saved)
    return paper_search_result_view(result)


def _compact_local_index_coverage(coverage: dict[str, object]) -> dict[str, object]:
    counts = coverage.get("status_counts")
    safe_counts = counts if isinstance(counts, dict) else {}
    return {
        "docs": int(coverage.get("docs", 0)),
        "searchable_docs": int(coverage.get("searchable_docs", 0)),
        "pending_docs": int(safe_counts.get("pending", 0)),
        "no_text_docs": int(safe_counts.get("no_text", 0)),
        "error_docs": int(safe_counts.get("error", 0)),
        "coverage_percent": float(coverage.get("coverage_percent", 0.0)),
        "search_format_version": coverage.get("search_format_version"),
        "search_format_current": coverage.get("search_format_current") is True,
    }


def _managed_paper_package_bytes(payload: dict[str, object]) -> int:
    return len(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


def _paper_locate_preflight_error(error: BaseException) -> dict[str, object]:
    lowered = str(error).casefold()
    if isinstance(error, FileNotFoundError) or "not configured" in lowered:
        code = "missing_configuration"
        message = "The personal knowledge root is not configured."
    elif "outdated lexical format" in lowered:
        code = "search_format_rebuild_required"
        message = (
            "The PDF search format is outdated; run `index --resume` to rebuild "
            "FTS from existing extracted page text."
        )
    elif "index does not exist" in lowered:
        code = "missing_index"
        message = "The configured PDF index is missing; run `index --resume`."
    elif "source is unavailable" in lowered or "library root" in lowered:
        code = "library_source_unavailable"
        message = "The configured PDF library is unavailable or does not match its index."
    else:
        code = "preflight_failed"
        message = "The configured PDF library could not pass the bounded locator preflight."
    return {
        "schema_version": "paper-locate/v1",
        "diagnostic_view": "paper_locate_preflight",
        "canonicalizer_version": "paper-canonical/v1",
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
        "managed_by": "manage-personal-knowledge",
        "error": {"code": code, "message": message},
    }


def _bound_managed_paper_package(payload: dict[str, object]) -> dict[str, object]:
    if _managed_paper_package_bytes(payload) <= 12 * 1024:
        return payload
    # Statement windows and verification features are indivisible evidence.
    # The adapter already bounds ordinary packages; do not relabel truncated evidence verified.
    coverage = payload.get("coverage")
    return {
        "schema_version": "paper-locate/v1",
        "canonicalizer_version": "paper-canonical/v1",
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
            "stop_reason": "managed_output_too_large",
        },
        "results": [],
        "coverage": {
            "incomplete": True,
            "local_index": (
                coverage.get("local_index")
                if isinstance(coverage, dict)
                else {}
            ),
            "warnings": [],
        },
        "timing_ms": {"core": 0, "expanded": 0, "verify": 0, "total": 0},
        "managed_by": "manage-personal-knowledge",
        "error": {
            "code": "managed_output_too_large",
            "message": "The complete evidence package exceeded the 12 KiB boundary. Narrow the query or request fewer results; no truncated statement is presented as verified.",
        },
    }


def command_paper_locate(args: argparse.Namespace) -> dict[str, object]:
    try:
        saved, _, coverage = require_current_library_search_format(args.config)
    except Exception as exc:
        return _paper_locate_preflight_error(exc)
    result = integrations.run_paper_locate(
        args.query,
        library_db(),
        aliases=args.alias,
        limit=args.limit,
    )
    if not isinstance(result, dict):
        raise RuntimeError("paper-locate integration returned an invalid package")
    _attach_resource_ids(result, saved)
    result["managed_by"] = "manage-personal-knowledge"
    result_coverage = result.get("coverage")
    if not isinstance(result_coverage, dict):
        result_coverage = {}
        result["coverage"] = result_coverage
    local_coverage = _compact_local_index_coverage(coverage)
    result_coverage["local_index"] = local_coverage
    incomplete = any(
        int(local_coverage[key]) > 0
        for key in ("pending_docs", "no_text_docs", "error_docs")
    )
    if incomplete:
        result_coverage["incomplete"] = True
        warnings = result_coverage.get("warnings")
        if not isinstance(warnings, list):
            warnings = []
            result_coverage["warnings"] = warnings
        warnings.append(
            "Search coverage is incomplete; unindexed, no-text, or extraction-error "
            "PDFs cannot support a library-wide negative conclusion."
        )
    result = _bound_managed_paper_package(result)
    result["diagnostic_view"] = "managed_paper_locate"
    return result


def command_page(args: argparse.Namespace) -> dict[str, object]:
    saved, _ = require_matching_library_index(args.config)
    path = args.path
    resource_id = None
    if args.id:
        _, root, database, _ = registry_context(args.config)
        resolved = registry.resource_resolve(root, database, args.id)
        if resolved.get("status") != "active" or not resolved.get("exists"):
            raise RuntimeError(
                "page --id requires an active, present resource; run resource-audit "
                "before using cached PDF pages"
            )
        if Path(str(resolved["relative_path"])).suffix.casefold() != ".pdf":
            raise RuntimeError("page --id requires a registered PDF resource")
        sources = saved.get("sources")
        library_source = sources.get("library") if isinstance(sources, dict) else None
        if not isinstance(library_source, dict):
            raise RuntimeError("Configured PDF library is missing")
        library_relative = Path(str(library_source["relative_path"]))
        full_relative = Path(str(resolved["relative_path"]))
        try:
            path = full_relative.relative_to(library_relative).as_posix()
        except ValueError as exc:
            raise RuntimeError("The registered PDF is outside the configured PDF library") from exc
        resource_id = str(resolved["resource_id"])
    result = library.get_page(library_db(), str(path), args.page)
    result["resource_id"] = resource_id or _resource_id_for_library_path(saved, str(path))
    return result


def _resource_id_for_library_path(saved: dict[str, object], library_path: str) -> str | None:
    if int(saved.get("schema_version", 0)) != 2 or not registry_db().is_file():
        return None
    sources = saved.get("sources")
    library_source = sources.get("library") if isinstance(sources, dict) else None
    if not isinstance(library_source, dict):
        return None
    full = (Path(str(library_source["relative_path"])) / Path(library_path)).as_posix()
    try:
        candidates = registry.resource_search(registry_db(), full, limit=100)
    except Exception:
        return None
    for item in candidates:
        if str(item.get("relative_path", "")).casefold() == full.casefold():
            return str(item["resource_id"])
    return None


def _attach_resource_ids(payload: dict[str, object], saved: dict[str, object]) -> None:
    def visit(value: object) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return
        candidate_path = value.get("path") or value.get("relative_path")
        if isinstance(candidate_path, str) and candidate_path.casefold().endswith(".pdf"):
            value["resource_id"] = _resource_id_for_library_path(saved, candidate_path)
        for child in value.values():
            if child is not value:
                visit(child)

    visit(payload)


def command_relink(args: argparse.Namespace) -> dict[str, object]:
    if args.source:
        if not args.path:
            raise ValueError("--path is required with --source")
        saved = config.relink_source(
            args.source,
            args.path,
            confirmed=args.yes,
            config_path=args.config,
        )
    else:
        if not (args.root and args.vault and args.library):
            raise ValueError("full relink requires --root, --vault, and --library")
        saved = config.relink(
            knowledge_root=args.root,
            vault_path=args.vault,
            library_path=args.library,
            confirmed=args.yes,
            config_path=args.config,
        )
    return {"ok": True, "status": "relinked", "configuration_persisted": True, "config": configuration_result_view({"config": saved})["config"]}


def command_forget(args: argparse.Namespace) -> dict[str, object]:
    if args.source == "all":
        removed = config.forget_config(confirmed=args.yes, config_path=args.config)
        return {"ok": True, "status": "forgotten" if removed else "already_absent", "source": "all"}
    saved = config.forget_source(args.source, confirmed=args.yes, config_path=args.config)
    return {"ok": True, "status": "source_forgotten", "source": args.source, "configuration_persisted": True, "config": configuration_result_view({"config": saved})["config"]}


def command_ocr_preflight(args: argparse.Namespace) -> dict[str, object]:
    return ocr_preflight_view(ocr.preflight_ocr(state_dir(), tesseract_path=args.tesseract))


def command_ocr_one(args: argparse.Namespace) -> dict[str, object]:
    saved = config.load_config(args.config)
    protected_library = None
    if saved is not None and "library" in saved.get("sources", {}):
        protected_library = config.resolve_source(saved, "library")
    result = ocr.run_ocr_one(
        args.input,
        args.output,
        args.sidecar,
        state_dir(),
        languages=args.languages,
        library_root=protected_library,
        tesseract_path=args.tesseract,
    )


    return ocr_one_view(result)


def _mutation_arguments(args: argparse.Namespace) -> dict[str, object]:
    return {
        "write": bool(getattr(args, "write", False)),
        "expect_plan_sha256": getattr(args, "expect_plan_sha256", None),
    }


def command_registry_init(args: argparse.Namespace) -> dict[str, object]:
    saved = load_required_config(args.config)
    root = Path(str(saved["knowledge_root"])).expanduser().resolve(strict=False)
    if not root.is_dir():
        raise RuntimeError("knowledge_root must exist before registry-init")
    sources = saved.get("sources")
    vault_source = sources.get("vault") if isinstance(sources, dict) else None
    if not isinstance(vault_source, dict):
        raise RuntimeError("Configured Vault source is missing")
    registry_config = saved.get("registry") if int(saved.get("schema_version", 0)) == 2 else None
    manifest_relative = (
        str(registry_config.get("manifest_relative_path", ".mpk/resources.jsonl"))
        if isinstance(registry_config, dict)
        else ".mpk/resources.jsonl"
    )
    exclusions = (
        list(registry_config.get("excluded_relative_paths", []))
        if isinstance(registry_config, dict)
        else list(args.exclude or [])
    )
    result = registry.registry_init(
        root,
        registry_db(),
        knowledge_root_name=str(saved.get("knowledge_root_name") or root.name),
        knowledge_root_id=(str(saved["knowledge_root_id"]) if saved.get("knowledge_root_id") else None),
        vault_relative_path=str(vault_source["relative_path"]),
        manifest_relative_path=manifest_relative,
        excluded_relative_paths=exclusions,
        **_mutation_arguments(args),
    )
    result["config_migration_required"] = int(saved.get("schema_version", 0)) == 1
    if args.write and result.get("applied"):
        migrated = config.migrate_to_schema_v2(
            knowledge_root_id=str(result["knowledge_root_id"]),
            knowledge_root_name=str(saved.get("knowledge_root_name") or root.name),
            manifest_relative_path=manifest_relative,
            excluded_relative_paths=exclusions,
            confirmed=True,
            config_path=args.config,
        )
        result["config"] = registry_configuration_view(migrated)
    return result


def command_registry_scan(args: argparse.Namespace) -> dict[str, object]:
    saved, root, database, manifest = registry_context(args.config)
    library_root = config.resolve_source(saved, "library", strict=True)
    root_id, library_relative = library_identity(saved)
    priority_paths = library.indexed_pdf_priority_paths(
        library_db(),
        library_root,
        knowledge_root_id=root_id,
        library_relative_path=library_relative,
    )
    result = registry.registry_scan(
        root,
        database,
        resume=args.resume,
        max_files=args.max_files,
        priority_relative_paths=priority_paths,
        manifest_path=manifest,
        **_mutation_arguments(args),
    )
    result["pdf_index_priority_count"] = len(priority_paths)
    return result


def command_registry_hash(args: argparse.Namespace) -> dict[str, object]:
    _, root, database, manifest = registry_context(args.config)
    return registry.registry_hash(
        root,
        database,
        resume=args.resume,
        max_files=args.max_files,
        verify_all=args.verify_all,
        manifest_path=manifest,
        **_mutation_arguments(args),
    )


def command_registry_status(args: argparse.Namespace) -> dict[str, object]:
    inventory = getattr(args, "inventory", False)
    purpose = getattr(args, "inventory_purpose", None)
    maximum = getattr(args, "max_entries", None)
    if inventory:
        if not isinstance(purpose, str) or not purpose.strip() or len(purpose) > 512:
            raise ValueError("Inventory requires a specific purpose")
        maximum = 50000 if maximum is None else maximum
        if type(maximum) is not int or not 1 <= maximum <= 1000000:
            raise ValueError("Invalid inventory limit")
    elif purpose is not None or maximum is not None:
        raise ValueError("Inventory options require --inventory")
    _, root, database, manifest = registry_context(args.config)
    raw = registry.registry_status(root, database, manifest_path=manifest,
                                   include_inventory=inventory, max_entries=maximum or 50000)
    return {"ok": True, **registry_status_view(raw)}


def command_resource_register(args: argparse.Namespace) -> dict[str, object]:
    _, root, database, manifest = registry_context(args.config)
    return registry.resource_register(
        root,
        database,
        args.path,
        source_url=args.url,
        doi=args.doi,
        isbn=args.isbn,
        manifest_path=manifest,
        **_mutation_arguments(args),
    )


def command_resource_resolve(args: argparse.Namespace) -> dict[str, object]:
    history = getattr(args, "history", False)
    purpose = getattr(args, "history_purpose", None)
    limit = getattr(args, "history_limit", None)
    offset = getattr(args, "history_offset", None)
    if history:
        if not isinstance(purpose, str) or not purpose.strip() or len(purpose) > 512:
            raise ValueError("History expansion requires a specific purpose")
        limit = 20 if limit is None else limit
        offset = 0 if offset is None else offset
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
            raise ValueError("Invalid history page")
    elif purpose is not None or limit is not None or offset is not None:
        raise ValueError("History options require --history")
    _, root, database, _ = registry_context(args.config)
    return {"ok": True, **registry.resource_resolve(root, database, args.id,
            include_history=history, history_limit=limit, history_offset=offset or 0),
            "history_included": history}


def command_resource_search(args: argparse.Namespace) -> dict[str, object]:
    offset = getattr(args, "offset", 0)
    if type(args.limit) is not int or not 1 <= args.limit <= 100 or type(offset) is not int or offset < 0:
        raise ValueError("Invalid resource search page")
    if not isinstance(args.query, str) or not args.query.strip() or len(args.query) > 1024:
        raise ValueError("Resource query must be nonblank and at most 1024 characters")
    _, _, database, _ = registry_context(args.config)
    rows = registry.resource_search(database, args.query, limit=args.limit + 1, offset=offset)
    return {"ok": True, "query": args.query, "results": rows[:args.limit],
            "page": {"offset": offset, "limit": args.limit, "has_more": len(rows) > args.limit}}


def command_resource_audit(args: argparse.Namespace) -> dict[str, object]:
    bounds = {name:getattr(args,name,default) for name,default in (('max_scan_entries',50000),('max_scan_bytes',67108864),('diagnostic_limit',20),('diagnostic_offset',0))}
    for name,minimum,maximum in (('max_scan_entries',1,1000000),('max_scan_bytes',1,1073741824),('diagnostic_limit',1,100),('diagnostic_offset',0,1000000000)):
        if type(bounds[name]) is not int or not minimum <= bounds[name] <= maximum:
            raise ValueError('Invalid resource audit boundary')
    if type(args.max_notes) is not int or not 1 <= args.max_notes <= 10000 or type(args.max_files) is not int or not 1 <= args.max_files <= 100000:
        raise ValueError('Invalid resource audit scope')
    category = getattr(args,'category',None)
    member_offset = getattr(args,'member_offset',0)
    if category is not None and category not in AUDIT_CATEGORIES or type(member_offset) is not int or member_offset < 0:
        raise ValueError('Invalid resource audit selection')
    saved, root, database, manifest = registry_context(args.config)
    report = registry.resource_audit(root, database, max_files=args.max_files, manifest_path=manifest)
    vault = config.resolve_source(saved, "vault", strict=True)
    report["note_references"] = _audit_note_references(root, database, vault, max_notes=args.max_notes, **bounds)
    return resource_audit_view(report, category=category, limit=bounds["diagnostic_limit"], offset=bounds["diagnostic_offset"], member_offset=member_offset)


def _audit_note_references(root: Path, database: Path, vault: Path, *, max_notes: int, max_scan_entries: int = 50000, max_scan_bytes: int = 67108864, diagnostic_limit: int = 20, diagnostic_offset: int = 0) -> dict[str, object]:
    if max_notes <= 0:
        raise ValueError("max_notes must be positive")
    try:
        notes, skipped_reparse_points = operations._safe_markdown_paths(vault, max_entries=max_scan_entries)
    except operations.NoteScanLimit:
        return {'scanned_notes':0,'scanned_bytes':0,'managed_reference_count':0,'diagnostic_count':0,'diagnostics':[],
                'truncated':True,'scan_complete':False,'stop_reason':'metadata_limit',
                'diagnostic_page':{'offset':diagnostic_offset,'limit':diagnostic_limit,'has_more':False},
                'coverage_note':'Scan stopped before reading note bodies. A zero diagnostic count does not establish clean references.'}
    truncated = len(notes) > max_notes
    diagnostics: list[dict[str, object]] = []
    managed_count = scanned_notes = scanned_bytes = 0
    stop_reason = 'note_limit' if truncated else 'completed'
    with registry._connect(database) as connection:
        known_relative_paths = [
            str(row["relative_path"])
            for row in connection.execute("SELECT relative_path FROM resources WHERE status != 'retired'")
        ]
    for note in notes[:max_notes]:
        try:
            raw, _ = operations._stable_note_bytes(note, max_bytes=max_scan_bytes-scanned_bytes)
        except operations.NoteBodyLimit:
            truncated = True
            stop_reason = 'body_byte_limit'
            break
        scanned_notes += 1
        scanned_bytes += len(raw)
        payload = raw[3:] if raw.startswith(b"\xef\xbb\xbf") else raw
        content = payload.decode("utf-8")
        parsed = references.parse_resource_references(
            content,
            knowledge_root=root,
            known_relative_paths=known_relative_paths,
        )
        relative = note.relative_to(vault).as_posix()
        managed_count += int(parsed["counts"]["managed"])
        for item in parsed["invalid_markers"]:
            diagnostics.append({"note_relative_path": relative, **item})
        for item in parsed["unmanaged_path_references"]:
            diagnostics.append({"note_relative_path": relative, **item})
        for item in parsed["references"]:
            identifier = str(item["resource_id"])
            if not item.get("valid_id"):
                continue
            try:
                resolved_resource = registry.resource_resolve(root, database, identifier, include_history=False)
            except Exception as exc:
                diagnostics.append(
                    {
                        "note_relative_path": relative,
                        "line": item.get("line"),
                        "resource_id": identifier,
                        "reason": "unknown_resource_id",
                        "message": str(exc),
                    }
                )
                continue
            if resolved_resource.get("status") != "active" or not resolved_resource.get("exists"):
                diagnostics.append(
                    {
                        "note_relative_path": relative,
                        "line": item.get("line"),
                        "resource_id": identifier,
                        "reason": str(resolved_resource.get("status") or "missing"),
                    }
                )
            elif str(item.get("target_uri", "")).casefold() != str(resolved_resource["file_uri"]).casefold():
                diagnostics.append(
                    {
                        "note_relative_path": relative,
                        "line": item.get("line"),
                        "resource_id": identifier,
                        "reason": "uri_mismatch",
                        "target_uri": item.get("target_uri"),
                        "expected_uri": resolved_resource["file_uri"],
                    }
                )
    return {
        "scanned_notes": scanned_notes,
        "scanned_bytes": scanned_bytes,
        "scan_complete": not truncated,
        "stop_reason": stop_reason,
        "managed_reference_count": managed_count,
        "diagnostic_count": len(diagnostics),
        "diagnostics": [
            {key:item[key] for key in ('note_relative_path','line','resource_id','reason') if key in item}
            for item in diagnostics[diagnostic_offset:diagnostic_offset+diagnostic_limit]],
        "diagnostic_page": {"offset":diagnostic_offset,"limit":diagnostic_limit,"has_more":diagnostic_offset+diagnostic_limit < len(diagnostics)},
        "truncated": truncated,
        "skipped_reparse_count": len(skipped_reparse_points),
    }


def command_resource_retire(args: argparse.Namespace) -> dict[str, object]:
    _, root, database, manifest = registry_context(args.config)
    return registry.resource_retire(
        root,
        database,
        args.id,
        manifest_path=manifest,
        **_mutation_arguments(args),
    )


def command_resource_move(args: argparse.Namespace) -> dict[str, object]:
    saved, root, database, manifest = registry_context(args.config)
    vault = config.resolve_source(saved, "vault", strict=True)
    _, library_relative = library_identity(saved)
    receiver_config, _ = obsidian_receiver_paths()
    if getattr(args, "obsidian_config", None):
        receiver_config = Path(args.obsidian_config).expanduser().resolve(strict=False)

    def refresh_and_verify_indexes() -> dict[str, object]:
        index_refresh = integrations.refresh_obsidian_index(vault, receiver_config)
        library_root = config.resolve_source(saved, "library", strict=True)
        root_id, current_library_relative = library_identity(saved)
        pdf_status = library.library_status(
            library_db(),
            library_root,
            knowledge_root_id=root_id,
            library_relative_path=current_library_relative,
        )
        pdf_ok = (
            not pdf_status.get("database_exists")
            or pdf_status.get("root_matches_config") is True
        )
        return {
            "ok": bool(index_refresh.get("ok")) and pdf_ok,
            "vault_index_refresh": index_refresh,
            "pdf_index_verification": pdf_status,
        }

    result = operations.resource_move(
        root,
        database,
        args.id,
        args.to,
        vault_root=vault,
        manifest_path=manifest,
        library_database_path=library_db(),
        library_relative_path=library_relative,
        post_move_check=refresh_and_verify_indexes,
        **_mutation_arguments(args),
    )
    post_check = result.get("post_move_check")
    if isinstance(post_check, dict):
        result["vault_index_refresh"] = post_check.get("vault_index_refresh")
        result["pdf_index_verification"] = post_check.get(
            "pdf_index_verification"
        )
    return transaction_result_view(result)


def command_root_relink(args: argparse.Namespace) -> dict[str, object]:
    receiver_config, _ = obsidian_receiver_paths()
    if args.obsidian_config:
        receiver_config = Path(args.obsidian_config).expanduser().resolve(strict=False)

    def refresh_and_verify_indexes() -> dict[str, object]:
        saved = load_required_config(args.config)
        vault = config.resolve_source(saved, "vault", strict=True)
        index_refresh = integrations.refresh_obsidian_index(vault, receiver_config)
        library_root = config.resolve_source(saved, "library", strict=True)
        root_id, library_relative = library_identity(saved)
        pdf_status = library.library_status(
            library_db(),
            library_root,
            knowledge_root_id=root_id,
            library_relative_path=library_relative,
        )
        pdf_ok = (
            not pdf_status.get("database_exists")
            or pdf_status.get("root_matches_config") is True
        )
        return {
            "ok": bool(index_refresh.get("ok")) and pdf_ok,
            "vault_index_refresh": index_refresh,
            "pdf_index_verification": pdf_status,
        }

    result = operations.root_relink(
        args.root,
        registry_db(),
        config_path=args.config,
        obsidian_receiver_config_path=receiver_config,
        library_database_path=library_db(),
        post_relink_check=refresh_and_verify_indexes,
        **_mutation_arguments(args),
    )
    post_check = result.get("post_relink_check")
    if isinstance(post_check, dict):
        result["vault_index_refresh"] = post_check.get("vault_index_refresh")
        result["pdf_index_verification"] = post_check.get(
            "pdf_index_verification"
        )
    return transaction_result_view(result)


def command_registry_export(args: argparse.Namespace) -> dict[str, object]:
    _, root, database, manifest = registry_context(args.config)
    return registry.registry_export(root, database, manifest_path=manifest, **_mutation_arguments(args))


def command_registry_restore(args: argparse.Namespace) -> dict[str, object]:
    saved = load_required_config(args.config)
    root = Path(str(saved["knowledge_root"])).expanduser().resolve(strict=False)
    manifest = (root / ".mpk" / "resources.jsonl").resolve(strict=False)
    if int(saved.get("schema_version", 0)) == 2:
        registry_config = saved.get("registry")
        if isinstance(registry_config, dict):
            manifest = (root / str(registry_config.get("manifest_relative_path", ".mpk/resources.jsonl"))).resolve(strict=False)
    preview = registry.registry_restore(
        root,
        registry_db(),
        manifest_path=manifest,
        replace=args.replace,
        write=False,
    )
    metadata = preview.get("restore_metadata")
    if not isinstance(metadata, dict):
        raise RuntimeError("registry-restore did not return portable root metadata")
    sources = saved.get("sources")
    vault_source = sources.get("vault") if isinstance(sources, dict) else None
    if not isinstance(vault_source, dict):
        raise RuntimeError("Configured Vault source is missing")
    if str(vault_source.get("relative_path")) != str(metadata.get("vault_relative_path")):
        raise RuntimeError("Portable manifest Vault path does not match the configured Vault")
    if int(saved.get("schema_version", 0)) == 2:
        if str(saved.get("knowledge_root_id")) != str(metadata.get("knowledge_root_id")):
            raise RuntimeError("Portable manifest identity does not match schema-v2 config")
        registry_config = saved.get("registry")
        if not isinstance(registry_config, dict) or (
            str(registry_config.get("manifest_relative_path"))
            != str(metadata.get("manifest_relative_path"))
            or list(registry_config.get("excluded_relative_paths", []))
            != list(metadata.get("excluded_relative_paths", []))
        ):
            raise RuntimeError("Portable manifest registry settings do not match schema-v2 config")
    if not args.write:
        return preview
    result = registry.registry_restore(
        root,
        registry_db(),
        manifest_path=manifest,
        replace=args.replace,
        write=True,
        expect_plan_sha256=args.expect_plan_sha256,
    )
    if int(saved.get("schema_version", 0)) == 1 and result.get("applied"):
        result["config"] = config.migrate_to_schema_v2(
            knowledge_root_id=str(metadata["knowledge_root_id"]),
            knowledge_root_name=str(metadata["knowledge_root_name"]),
            manifest_relative_path=str(metadata["manifest_relative_path"]),
            excluded_relative_paths=list(metadata.get("excluded_relative_paths", [])),
            confirmed=True,
            config_path=args.config,
        )
    if "config" in result:
        result["config"] = registry_configuration_view(result["config"])
    return result


def _read_reference_candidate(note: Path, content_file: str | None, post_sha256: str | None) -> tuple[str, str, int]:
    if post_sha256:
        raw = note.read_bytes()
        actual = hashlib.sha256(raw).hexdigest()
        if actual.lower() != post_sha256.lower():
            raise RuntimeError("post_sha256 does not match the current note")
        payload = raw[3:] if raw.startswith(b"\xef\xbb\xbf") else raw
        return payload.decode("utf-8"), actual, note.stat().st_mtime_ns
    if not content_file:
        raise ValueError("reference-refresh --note requires --content-file or --post-sha256")
    content = Path(content_file).expanduser().resolve().read_text(encoding="utf-8-sig")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return content, digest, note.stat().st_mtime_ns if note.exists() else 0


def _reference_plan_for_note(
    root: Path,
    database: Path,
    vault: Path,
    note: Path,
    content: str,
    note_sha256: str,
    mtime_ns: int,
) -> tuple[dict[str, object], list[dict[str, object]], str]:
    try:
        note_relative = note.resolve(strict=False).relative_to(vault).as_posix()
    except ValueError as exc:
        raise RuntimeError("reference-refresh note must be inside the configured Vault") from exc
    with registry._connect(database) as connection:
        known_relative_paths = [
            str(row["relative_path"])
            for row in connection.execute("SELECT relative_path FROM resources WHERE status != 'retired'")
        ]
    parsed = references.parse_resource_references(
        content,
        knowledge_root=root,
        known_relative_paths=known_relative_paths,
    )
    valid: list[dict[str, object]] = []
    diagnostics: list[dict[str, object]] = list(parsed["invalid_markers"])
    for item in parsed["references"]:
        if not item.get("valid_id"):
            continue
        identifier = str(item["resource_id"])
        try:
            resolved = registry.resource_resolve(root, database, identifier)
        except Exception as exc:
            diagnostics.append({"resource_id": identifier, "reason": "unknown_resource_id", "message": str(exc)})
            continue
        if resolved.get("status") != "active" or not resolved.get("exists"):
            diagnostics.append({"resource_id": identifier, "reason": str(resolved.get("status") or "missing")})
            continue
        if str(item.get("target_uri", "")).casefold() != str(resolved["file_uri"]).casefold():
            diagnostics.append(
                {
                    "resource_id": identifier,
                    "reason": "uri_mismatch",
                    "target_uri": item.get("target_uri"),
                    "expected_uri": resolved["file_uri"],
                }
            )
        valid.append(item)
    diagnostics.extend(parsed["unmanaged_path_references"])
    scan_status = "ok" if not diagnostics else "diagnostic"
    plan = registry.make_plan(
        "reference-refresh",
        [{"action": "replace_note_reference_cache", "note_relative_path": note_relative, "reference_count": len(valid)}],
        note_sha256=note_sha256,
        scan_status=scan_status,
        diagnostics=diagnostics,
    )
    plan.update({"diagnostics": diagnostics, "parsed_counts": parsed["counts"], "note_relative_path": note_relative})
    return plan, valid, scan_status


def command_reference_refresh(args: argparse.Namespace) -> dict[str, object]:
    saved, root, database, _ = registry_context(args.config)
    vault = config.resolve_source(saved, "vault", strict=True)
    if args.changed:
        scan_limit = getattr(args, "max_scan_entries", 50000)
        if type(scan_limit) is not int or not 1 <= scan_limit <= 1000000:
            raise ValueError("Metadata scan limit must be between 1 and 1000000")
        if type(args.max_notes) is not int or not 1 <= args.max_notes <= 10000:
            raise ValueError("Changed-note limit must be between 1 and 10000")
        examined_limit = getattr(args, "max_examined_notes", 5000)
        byte_limit = getattr(args, "max_scan_bytes", 67108864)
        if type(examined_limit) is not int or not 1 <= examined_limit <= 100000:
            raise ValueError("Examined-note limit must be between 1 and 100000")
        if type(byte_limit) is not int or not 1 <= byte_limit <= 1073741824:
            raise ValueError("Body scan limit must be between 1 and 1073741824 bytes")
        try:
            return _refresh_changed_notes(args, root, database, vault)
        except operations.NoteBodyLimit as exc:
            return {"ok": False, "status": "attention_required", "diagnostic_view": "reference_body_limit",
                    "scan_complete": False, "applied": False, "boundary": exc.boundary,
                    "max_examined_notes": examined_limit, "max_scan_bytes": byte_limit}
        except operations.NoteScanLimit:
            return {"ok": False, "status": "attention_required", "diagnostic_view": "reference_scan_limit",
                    "scan_complete": False, "applied": False, "code": "reference_metadata_scan_limit",
                    "max_scan_entries": scan_limit,
                    "recovery": "Increase --max-scan-entries within the approved scope or refresh one named note; no deletion or reference-cache write was applied."}
    # Validate the named target before any candidate or note body is opened.
    vault = vault.resolve(strict=True)
    note = Path(os.path.abspath(Path(args.note).expanduser()))
    try:
        relative = note.relative_to(vault)
        note.resolve(strict=False).relative_to(vault)
    except ValueError as exc:
        raise RuntimeError("reference-refresh note must be inside the configured Vault") from exc
    current = vault
    for part in relative.parts:
        current = current / part
        try:
            current.lstat()
        except FileNotFoundError:
            # A named candidate may preview a not-yet-created note.
            continue
        if registry._is_reparse_point(current):
            raise RuntimeError("reference-refresh does not follow reparse points")
    content, digest, mtime_ns = _read_reference_candidate(note, args.content_file, args.post_sha256)
    plan, valid, scan_status = _reference_plan_for_note(root, database, vault, note, content, digest, mtime_ns)
    if not args.write:
        return plan
    if args.expect_plan_sha256 != plan["plan_sha256"]:
        raise registry.RegistryPlanError("--expect-plan-sha256 does not match the current reference plan")
    result = registry.replace_note_references(
        database,
        str(plan["note_relative_path"]),
        digest,
        valid,
        mtime_ns=mtime_ns,
        scan_status=scan_status,
    )
    return {**plan, **result, "ok": True, "applied": True, "reference_sync_status": "synchronized"}


def _refresh_changed_notes(
    args: argparse.Namespace,
    root: Path,
    database: Path,
    vault: Path,
) -> dict[str, object]:
    with registry._connect(database) as connection:
        known = {
            str(row["note_relative_path"]): (str(row["note_sha256"]), row["mtime_ns"])
            for row in connection.execute("SELECT note_relative_path, note_sha256, mtime_ns FROM note_documents")
        }
    notes, skipped_reparse_points = operations._safe_markdown_paths(vault, max_entries=getattr(args, "max_scan_entries", 50000))
    current_paths = {path.relative_to(vault).as_posix() for path in notes}

    def beneath_skipped_path(note_relative: str) -> bool:
        folded = note_relative.casefold()
        return any(
            folded == skipped.casefold()
            or folded.startswith(skipped.rstrip("/").casefold() + "/")
            for skipped in skipped_reparse_points
        )

    deleted_notes = sorted(
        (
            note_relative
            for note_relative in known
            if note_relative not in current_paths and not beneath_skipped_path(note_relative)
        ),
        key=str.casefold,
    )
    selected_deleted = deleted_notes[: args.max_notes]
    remaining = args.max_notes - len(selected_deleted)
    candidates: list[tuple[Path, str, str, int, str, list[dict[str, object]]]] = []
    diagnostics: list[dict[str, object]] = []
    scan_truncated = False
    examined_notes = 0
    scanned_bytes = 0
    examined_limit = getattr(args, "max_examined_notes", 5000)
    byte_limit = getattr(args, "max_scan_bytes", 67108864)
    if remaining > 0:
        for index, resolved in enumerate(notes):
            relative = resolved.relative_to(vault).as_posix()
            if examined_notes >= examined_limit:
                raise operations.NoteBodyLimit("examined_notes")
            raw, info = operations._stable_note_bytes(resolved, max_bytes=byte_limit - scanned_bytes)
            examined_notes += 1
            scanned_bytes += len(raw)
            digest = hashlib.sha256(raw).hexdigest()
            mtime_ns = int(info.st_mtime_ns)
            if known.get(relative) == (digest, mtime_ns):
                continue
            payload = raw[3:] if raw.startswith(b"\xef\xbb\xbf") else raw
            content = payload.decode("utf-8")
            plan, valid, scan_status = _reference_plan_for_note(root, database, vault, resolved, content, digest, mtime_ns)
            diagnostics.extend(plan["diagnostics"])
            candidates.append((resolved, relative, digest, mtime_ns, scan_status, valid))
            if len(candidates) >= remaining:
                scan_truncated = index < len(notes) - 1
                break
    elif notes:
        scan_truncated = True
    actions = [
        {"action": "delete_note_reference_cache", "note_relative_path": relative}
        for relative in selected_deleted
    ] + [
        {"action": "replace_note_reference_cache", "note_relative_path": relative, "note_sha256": digest, "reference_count": len(valid)}
        for _, relative, digest, _, _, valid in candidates
    ]
    plan = registry.make_plan(
        "reference-refresh-changed",
        actions,
        max_notes=args.max_notes,
        diagnostics=diagnostics,
        skipped_reparse_points=skipped_reparse_points,
    )
    plan.update(
        {
            "examined_notes": examined_notes,
            "scanned_bytes": scanned_bytes,
            "changed_notes": len(candidates),
            "deleted_notes": len(selected_deleted),
            "has_more": len(deleted_notes) > len(selected_deleted) or scan_truncated,
            "diagnostics": diagnostics,
            "skipped_reparse_points": skipped_reparse_points,
        }
    )
    if not args.write:
        return plan
    if args.expect_plan_sha256 != plan["plan_sha256"]:
        raise registry.RegistryPlanError("--expect-plan-sha256 does not match the current changed-note plan")
    if selected_deleted:
        with registry._connect(database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.executemany(
                "DELETE FROM note_documents WHERE note_relative_path = ?",
                [(relative,) for relative in selected_deleted],
            )
            connection.commit()
    for _, relative, digest, mtime_ns, scan_status, valid in candidates:
        registry.replace_note_references(database, relative, digest, valid, mtime_ns=mtime_ns, scan_status=scan_status)
    return {
        **plan,
        "ok": True,
        "applied": True,
        "synchronized_notes": len(candidates),
        "deleted_note_caches": len(selected_deleted),
        "reference_sync_status": "synchronized",
    }


def add_config_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", help="Optional manage-personal-knowledge config path.")


def add_mutation_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--write", action="store_true", help="Apply the exact dry-run plan. Default is dry-run.")
    parser.add_argument(
        "--expect-plan-sha256",
        help="Required with --write; must match the current dry-run plan_sha256.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = SafeParser(description="Manage a configured local Vault and PDF library.")
    sub = parser.add_subparsers(dest="command", required=True)

    discover_parser = sub.add_parser("discover", help="Discover source candidates without writing config.")
    discover_parser.add_argument("--root", required=True)
    discover_parser.add_argument("--max-directories", type=int, default=5000)
    discover_parser.add_argument("--max-entries", type=int, default=50000)
    discover_parser.add_argument("--limit", type=int, default=20)
    discover_parser.add_argument("--offset", type=int, default=0)
    discover_parser.add_argument("--json", action="store_true")
    discover_parser.set_defaults(handler=command_discover)

    configure_parser = sub.add_parser("configure", help="Persist confirmed source paths.")
    configure_parser.add_argument("--root", required=True)
    configure_parser.add_argument("--vault", required=True)
    configure_parser.add_argument("--library", required=True)
    configure_parser.add_argument("--yes", action="store_true", help="Confirm the displayed paths.")
    configure_parser.add_argument("--configure-obsidian", action="store_true")
    configure_parser.add_argument("--build-vault-index", action="store_true")
    configure_parser.add_argument("--obsidian-config")
    configure_parser.add_argument("--obsidian-state")
    configure_parser.add_argument("--json", action="store_true")
    add_config_argument(configure_parser)
    configure_parser.set_defaults(handler=command_configure)

    status_parser = sub.add_parser("status", help="Check paths, integrations, index coverage, and OCR readiness.")
    status_parser.add_argument("--json", action="store_true")
    status_parser.add_argument("--diagnostics", action="store_true")
    status_parser.add_argument("--diagnostic-purpose")
    add_config_argument(status_parser)
    status_parser.set_defaults(handler=command_status)

    index_parser = sub.add_parser("index", help="Inventory and incrementally index PDF text layers.")
    resume_group = index_parser.add_mutually_exclusive_group()
    resume_group.add_argument(
        "--resume",
        dest="resume",
        action="store_true",
        default=True,
        help="Continue pending PDFs and skip accounted unchanged PDFs (default).",
    )
    resume_group.add_argument(
        "--rebuild",
        dest="resume",
        action="store_false",
        help="Explicitly mark every discovered PDF pending and rebuild its text rows.",
    )
    index_parser.add_argument("--retry-errors", action="store_true")
    index_parser.add_argument("--max-files", type=int)
    index_parser.add_argument("--batch-size", type=int, default=25)
    index_parser.add_argument("--pdftotext")
    index_parser.add_argument("--progress", action="store_true")
    index_parser.add_argument("--json", action="store_true")
    add_config_argument(index_parser)
    index_parser.set_defaults(handler=command_index)

    search_parser = sub.add_parser("library-search", help="Search indexed general PDF content.")
    search_parser.add_argument("--query", required=True)
    search_parser.add_argument("--alias", action="append", default=[])
    search_parser.add_argument("--limit", type=int, default=8)
    search_parser.add_argument("--json", action="store_true")
    add_config_argument(search_parser)
    search_parser.set_defaults(handler=command_library_search)

    question_index_parser = sub.add_parser(
        "question-index",
        help="Incrementally index numbered Markdown questions from one bounded Vault folder.",
    )
    question_index_parser.add_argument("--source-relative", required=True)
    question_index_parser.add_argument("--json", action="store_true")
    add_config_argument(question_index_parser)
    question_index_parser.set_defaults(handler=command_question_index)

    question_status_parser = sub.add_parser("question-status", help="Report local question-index coverage.")
    question_status_parser.add_argument(
        "--discovery-relative",
        default=question_collections.DEFAULT_DISCOVERY_RELATIVE,
        help="Knowledge-root-relative directory scanned for structured question collections.",
    )
    question_status_parser.add_argument("--max-directories", type=int, default=5000)
    question_status_parser.add_argument("--max-entries", type=int, default=50000)
    question_status_parser.add_argument("--inventory-limit", type=int, default=20)
    question_status_parser.add_argument("--inventory-offset", type=int, default=0)
    question_status_parser.add_argument("--inventory", action="store_true")
    question_status_parser.add_argument("--inventory-purpose")
    question_status_parser.add_argument("--json", action="store_true")
    add_config_argument(question_status_parser)
    question_status_parser.set_defaults(handler=command_question_status)

    question_discover_parser = sub.add_parser(
        "question-discover",
        help="Discover structured question collections that may need import.",
    )
    question_discover_parser.add_argument(
        "--discovery-relative", default=question_collections.DEFAULT_DISCOVERY_RELATIVE
    )
    question_discover_parser.add_argument("--max-directories", type=int, default=5000)
    question_discover_parser.add_argument("--max-entries", type=int, default=50000)
    question_discover_parser.add_argument("--inventory-limit", type=int, default=20)
    question_discover_parser.add_argument("--inventory-offset", type=int, default=0)
    question_discover_parser.add_argument("--json", action="store_true")
    add_config_argument(question_discover_parser)
    question_discover_parser.set_defaults(handler=command_question_discover)

    question_import_parser = sub.add_parser(
        "question-import",
        help="Preview or apply a hash-bound import of one structured question collection.",
    )
    question_import_parser.add_argument("--scope", choices=("vault", "knowledge-root"), required=True)
    question_import_parser.add_argument("--source-relative", required=True)
    question_import_parser.add_argument("--json", action="store_true")
    add_mutation_arguments(question_import_parser)
    add_config_argument(question_import_parser)
    question_import_parser.set_defaults(handler=command_question_import)

    question_probe_parser = sub.add_parser(
        "question-import-probe",
        help="Run the local synthetic success or stale-plan recovery probe.",
    )
    question_probe_parser.add_argument("--case", choices=("success", "fault"), required=True)
    question_probe_parser.add_argument("--json", action="store_true")
    question_probe_parser.set_defaults(handler=command_question_import_probe)

    question_search_parser = sub.add_parser(
        "question-search",
        help="Search the structured local question index before broader Vault/PDF fallback.",
    )
    question_search_parser.add_argument("--query", required=True)
    question_search_parser.add_argument("--alias", action="append", default=[])
    question_search_parser.add_argument("--year")
    question_search_parser.add_argument("--paper")
    question_search_parser.add_argument("--limit", type=int, default=8)
    question_search_parser.add_argument("--json", action="store_true")
    add_config_argument(question_search_parser)
    question_search_parser.set_defaults(handler=command_question_search)

    paper_parser = sub.add_parser("paper-search", help="Run pdf-paper-search against the unified index.")
    paper_parser.add_argument("--query", required=True)
    paper_parser.add_argument("--alias", action="append", default=[])
    paper_parser.add_argument("--limit", type=int, default=12)
    paper_parser.add_argument("--output-mode", choices=("compact", "json"), default="compact")
    paper_parser.add_argument("--json", action="store_true")
    add_config_argument(paper_parser)
    paper_parser.set_defaults(handler=command_paper_search)

    locate_parser = sub.add_parser(
        "paper-locate",
        help="Return one bounded, verified paper source-location package.",
    )
    locate_parser.add_argument("--query", required=True)
    locate_parser.add_argument("--alias", action="append", default=[])
    locate_parser.add_argument("--limit", type=int, default=12)
    locate_parser.add_argument("--json", action="store_true")
    add_config_argument(locate_parser)
    locate_parser.set_defaults(handler=command_paper_locate)

    page_parser = sub.add_parser("page", help="Read one indexed PDF page.")
    page_target = page_parser.add_mutually_exclusive_group(required=True)
    page_target.add_argument("--path")
    page_target.add_argument("--id")
    page_parser.add_argument("--page", type=int, required=True)
    page_parser.add_argument("--json", action="store_true")
    add_config_argument(page_parser)
    page_parser.set_defaults(handler=command_page)

    relink_parser = sub.add_parser("relink", help="Explicitly update a moved source or root.")
    relink_parser.add_argument("--source", choices=("vault", "library"))
    relink_parser.add_argument("--path")
    relink_parser.add_argument("--root")
    relink_parser.add_argument("--vault")
    relink_parser.add_argument("--library")
    relink_parser.add_argument("--yes", action="store_true")
    relink_parser.add_argument("--json", action="store_true")
    add_config_argument(relink_parser)
    relink_parser.set_defaults(handler=command_relink)

    forget_parser = sub.add_parser("forget", help="Explicitly forget one source or all configuration.")
    forget_parser.add_argument("--source", choices=("vault", "library", "all"), required=True)
    forget_parser.add_argument("--yes", action="store_true")
    forget_parser.add_argument("--json", action="store_true")
    add_config_argument(forget_parser)
    forget_parser.set_defaults(handler=command_forget)

    preflight_parser = sub.add_parser("ocr-preflight", help="Verify the prepared OCR toolchain.")
    preflight_parser.add_argument("--tesseract")
    preflight_parser.add_argument("--json", action="store_true")
    preflight_parser.set_defaults(handler=command_ocr_preflight)

    ocr_parser = sub.add_parser("ocr-one", help="OCR one explicit PDF to new output files.")
    ocr_parser.add_argument("--input", required=True)
    ocr_parser.add_argument("--output", required=True)
    ocr_parser.add_argument("--sidecar", required=True)
    ocr_parser.add_argument("--languages", default="eng+chi_sim")
    ocr_parser.add_argument("--tesseract")
    ocr_parser.add_argument("--json", action="store_true")
    add_config_argument(ocr_parser)
    ocr_parser.set_defaults(handler=command_ocr_one)

    registry_init_parser = sub.add_parser("registry-init", help="Preview or initialize stable resource identity.")
    registry_init_parser.add_argument("--exclude", action="append", default=[])
    registry_init_parser.add_argument("--json", action="store_true")
    add_mutation_arguments(registry_init_parser)
    add_config_argument(registry_init_parser)
    registry_init_parser.set_defaults(handler=command_registry_init)

    registry_scan_parser = sub.add_parser("registry-scan", help="Register one bounded batch of eligible files.")
    registry_scan_parser.add_argument("--resume", action="store_true")
    registry_scan_parser.add_argument("--max-files", type=int, default=1000)
    registry_scan_parser.add_argument("--json", action="store_true")
    add_mutation_arguments(registry_scan_parser)
    add_config_argument(registry_scan_parser)
    registry_scan_parser.set_defaults(handler=command_registry_scan)

    registry_hash_parser = sub.add_parser("registry-hash", help="Hash one bounded batch of registered files.")
    registry_hash_parser.add_argument("--resume", action="store_true")
    registry_hash_parser.add_argument("--max-files", type=int, default=100)
    registry_hash_parser.add_argument(
        "--verify-all",
        action="store_true",
        help="Re-hash active resources even when their size and mtime are unchanged.",
    )
    registry_hash_parser.add_argument("--json", action="store_true")
    add_mutation_arguments(registry_hash_parser)
    add_config_argument(registry_hash_parser)
    registry_hash_parser.set_defaults(handler=command_registry_hash)

    registry_status_parser = sub.add_parser("registry-status", help="Report registry, hash, manifest, and scan state.")
    registry_status_parser.add_argument("--json", action="store_true")
    registry_status_parser.add_argument("--inventory", action="store_true")
    registry_status_parser.add_argument("--inventory-purpose")
    registry_status_parser.add_argument("--max-entries", type=int)
    add_config_argument(registry_status_parser)
    registry_status_parser.set_defaults(handler=command_registry_status)

    register_parser = sub.add_parser("resource-register", help="Register one eligible long-lived file.")
    register_parser.add_argument("--path", required=True)
    register_parser.add_argument("--url")
    register_parser.add_argument("--doi")
    register_parser.add_argument("--isbn")
    register_parser.add_argument("--json", action="store_true")
    add_mutation_arguments(register_parser)
    add_config_argument(register_parser)
    register_parser.set_defaults(handler=command_resource_register)

    resolve_parser = sub.add_parser("resource-resolve", help="Resolve a stable resource ID to its current file.")
    resolve_parser.add_argument("--id", required=True)
    resolve_parser.add_argument("--history", action="store_true", help="Include a bounded history page for a stated purpose.")
    resolve_parser.add_argument("--history-purpose")
    resolve_parser.add_argument("--history-limit", type=int)
    resolve_parser.add_argument("--history-offset", type=int)
    resolve_parser.add_argument("--json", action="store_true")
    add_config_argument(resolve_parser)
    resolve_parser.set_defaults(handler=command_resource_resolve)

    resource_search_parser = sub.add_parser("resource-search", help="Search resource identity and external identifiers.")
    resource_search_parser.add_argument("--query", required=True)
    resource_search_parser.add_argument("--limit", type=int, default=20)
    resource_search_parser.add_argument("--offset", type=int, default=0)
    resource_search_parser.add_argument("--json", action="store_true")
    add_config_argument(resource_search_parser)
    resource_search_parser.set_defaults(handler=command_resource_search)

    audit_parser = sub.add_parser("resource-audit", help="Audit missing files, moves, references, and duplicates.")
    audit_parser.add_argument("--max-files", type=int, default=10000)
    audit_parser.add_argument("--max-notes", type=int, default=10000)
    audit_parser.add_argument("--max-scan-entries", type=int, default=50000)
    audit_parser.add_argument("--max-scan-bytes", type=int, default=67108864)
    audit_parser.add_argument("--diagnostic-limit", type=int, default=20)
    audit_parser.add_argument("--diagnostic-offset", type=int, default=0)
    audit_parser.add_argument("--category", choices=AUDIT_CATEGORIES)
    audit_parser.add_argument("--member-offset", type=int, default=0)
    audit_parser.add_argument("--json", action="store_true")
    add_config_argument(audit_parser)
    audit_parser.set_defaults(handler=command_resource_audit)

    retire_parser = sub.add_parser("resource-retire", help="Retire an ID without deleting its file.")
    retire_parser.add_argument("--id", required=True)
    retire_parser.add_argument("--json", action="store_true")
    add_mutation_arguments(retire_parser)
    add_config_argument(retire_parser)
    retire_parser.set_defaults(handler=command_resource_retire)

    move_parser = sub.add_parser(
        "resource-move",
        help="Move or rename one registered resource and update managed note links.",
    )
    move_parser.add_argument("--id", required=True)
    move_parser.add_argument("--to", required=True)
    move_parser.add_argument(
        "--obsidian-config",
        help="Optional obsidian-vault-notes receiver config path.",
    )
    move_parser.add_argument("--json", action="store_true")
    add_mutation_arguments(move_parser)
    add_config_argument(move_parser)
    move_parser.set_defaults(handler=command_resource_move)

    root_relink_parser = sub.add_parser(
        "root-relink",
        help="Relink a knowledge root that was moved as one directory.",
    )
    root_relink_parser.add_argument("--root", required=True)
    root_relink_parser.add_argument(
        "--obsidian-config",
        help="Optional obsidian-vault-notes receiver config path.",
    )
    root_relink_parser.add_argument("--json", action="store_true")
    add_mutation_arguments(root_relink_parser)
    add_config_argument(root_relink_parser)
    root_relink_parser.set_defaults(handler=command_root_relink)

    export_parser = sub.add_parser("registry-export", help="Atomically refresh the portable recovery manifest.")
    export_parser.add_argument("--json", action="store_true")
    add_mutation_arguments(export_parser)
    add_config_argument(export_parser)
    export_parser.set_defaults(handler=command_registry_export)

    restore_parser = sub.add_parser("registry-restore", help="Rebuild SQLite from the portable manifest.")
    restore_parser.add_argument("--replace", action="store_true", help="Replace an existing populated registry.")
    restore_parser.add_argument("--json", action="store_true")
    add_mutation_arguments(restore_parser)
    add_config_argument(restore_parser)
    restore_parser.set_defaults(handler=command_registry_restore)

    refresh_parser = sub.add_parser("reference-refresh", help="Validate and refresh rebuildable note reference state.")
    refresh_mode = refresh_parser.add_mutually_exclusive_group(required=True)
    refresh_mode.add_argument("--changed", action="store_true")
    refresh_mode.add_argument("--note")
    refresh_parser.add_argument("--content-file")
    refresh_parser.add_argument("--post-sha256")
    refresh_parser.add_argument("--max-notes", type=int, default=1000)
    refresh_parser.add_argument("--max-scan-entries", type=int, default=50000)
    refresh_parser.add_argument("--max-examined-notes", type=int, default=5000)
    refresh_parser.add_argument("--max-scan-bytes", type=int, default=67108864)
    refresh_parser.add_argument("--json", action="store_true")
    add_mutation_arguments(refresh_parser)
    add_config_argument(refresh_parser)
    refresh_parser.set_defaults(handler=command_reference_refresh)

    return parser


@public_main
def main() -> int:
    configure_streams()
    args = build_parser().parse_args()
    try:
        payload = args.handler(args)
    except Exception as exc:
        payload = {
            "ok": False,
            "status": "error",
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }
        emit(payload)
        return 2
    emit(payload)
    if isinstance(payload, dict) and payload.get("ok") is False:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
