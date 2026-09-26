from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
import sys

from .db import (
    CURRENT_SCHEMA_VERSION,
    connect,
    note_links,
    note_sections,
    query_results_to_json,
    query_sections,
    rebuild_index,
    refresh_index,
    resolve_note_spec,
)
from .markdown import parse_markdown_note
from .pdf_db import (
    connect_pdf,
    pdf_doc_sections,
    pdf_doc_survey,
    pdf_doc_summary,
    pdf_page_content,
    query_pdf_doc_surveys,
    query_pdf_pages,
    query_pdf_sections,
    query_pdf_results_to_json,
    rebuild_pdf_surveys,
    rebuild_pdf_index,
    resolve_pdf_doc_spec,
)
from .pdf_extract import DEFAULT_PDFTOTEXT, iter_pdf_files, parse_pdf_document
from .pdf_library_survey import build_pdf_library_survey, default_inventory_path


DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "vault.sqlite3"
DEFAULT_PDF_DB = Path(__file__).resolve().parent.parent / "data" / "pdf.sqlite3"
DEFAULT_EXCLUDED_DIRS = {".obsidian", ".trash"}


def resolved_markdown_inside_vault(vault_root: Path, file_path: Path) -> Path | None:
    try:
        resolved = file_path.resolve(strict=True)
        resolved.relative_to(vault_root)
    except (OSError, ValueError):
        return None
    return resolved if resolved.is_file() and resolved.suffix.casefold() == ".md" else None


def iter_markdown_files(vault_path: Path, excluded_dirs: set[str]) -> list[Path]:
    vault_path = vault_path.resolve()
    files_by_resolved_path: dict[str, Path] = {}
    for file_path in vault_path.rglob("*.md"):
        resolved = resolved_markdown_inside_vault(vault_path, file_path)
        if resolved is None:
            continue
        rel_parts = resolved.relative_to(vault_path).parts
        # Treat dot-prefixed names as hidden/system content and skip them by default.
        if any(part.startswith(".") for part in rel_parts):
            continue
        if any(part in excluded_dirs for part in rel_parts[:-1]):
            continue
        files_by_resolved_path.setdefault(str(resolved).casefold(), resolved)
    return sorted(
        files_by_resolved_path.values(),
        key=lambda path: path.relative_to(vault_path).as_posix().casefold(),
    )


def require_existing_db(db_path: Path) -> None:
    if not db_path.exists():
        raise FileNotFoundError(
            f"Index database not found: {db_path}. Run the `refresh` command first."
        )


def utc_from_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def table_exists(con: sqlite3.Connection, table_name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def table_columns(con: sqlite3.Connection, table_name: str) -> set[str]:
    if not table_exists(con, table_name):
        return set()
    return {row["name"] for row in con.execute(f"PRAGMA table_info({table_name})").fetchall()}


def build_index_status(
    *,
    db_path: Path,
    vault_path: Path,
    excluded_dirs: set[str],
    include_reindex_command: bool = False,
) -> dict[str, object]:
    markdown_files = iter_markdown_files(vault_path, excluded_dirs=excluded_dirs) if vault_path.exists() else []
    vault_paths = {file_path.relative_to(vault_path).as_posix(): file_path for file_path in markdown_files}
    payload: dict[str, object] = {
        "db_exists": db_path.exists(),
        "status_method": "missing_db",
        "indexed_at_utc": "",
        "schema_version": None,
        "schema_mismatch": True,
        "vault_markdown_count": len(markdown_files),
        "indexed_note_count": 0,
        "newer_markdown_count": 0,
        "missing_indexed_count": len(markdown_files),
        "index_stale": True,
        "reindex_hint": "Index database is missing; run the refresh command before retrieval.",
    }
    if include_reindex_command:
        payload["reindex_command"] = f'python -m obsidian_local_kb refresh --vault "{vault_path}" --db "{db_path}"'
    if not db_path.exists():
        return payload

    con = connect(db_path, read_only=True)
    try:
        notes_columns = table_columns(con, "notes")
        indexed_rows = con.execute("SELECT path FROM notes").fetchall() if table_exists(con, "notes") else []
        indexed_paths = {row["path"] for row in indexed_rows}
        missing_in_index = set(vault_paths) - indexed_paths
        deleted_from_vault = indexed_paths - set(vault_paths)
        indexed_at_utc = utc_from_timestamp(db_path.stat().st_mtime)
        status_method = "db_mtime_fallback"

        if table_exists(con, "index_meta"):
            meta_rows = con.execute(
                "SELECT key, value FROM index_meta WHERE key IN ('indexed_at_utc', 'schema_version')"
            ).fetchall()
            meta = {str(row["key"]): str(row["value"]) for row in meta_rows}
            indexed_at_utc = meta.get("indexed_at_utc", indexed_at_utc)
            if "schema_version" in meta:
                try:
                    payload["schema_version"] = int(meta["schema_version"])
                except ValueError:
                    payload["schema_version"] = meta["schema_version"]

        if {"source_mtime_ns", "source_size", "indexed_at_utc"}.issubset(notes_columns):
            status_method = "stored_file_metadata"
            newer_markdown_count = 0
            source_rows = con.execute(
                "SELECT path, source_mtime_ns, source_size FROM notes"
            ).fetchall()
            source_by_path = {row["path"]: row for row in source_rows}
            for rel_path, file_path in vault_paths.items():
                row = source_by_path.get(rel_path)
                if row is None:
                    continue
                stat = file_path.stat()
                if stat.st_mtime_ns != int(row["source_mtime_ns"]) or stat.st_size != int(row["source_size"]):
                    newer_markdown_count += 1
        else:
            db_mtime_ns = db_path.stat().st_mtime_ns
            newer_markdown_count = sum(
                1 for file_path in vault_paths.values() if file_path.stat().st_mtime_ns > db_mtime_ns
            )

        schema_mismatch = payload["schema_version"] != CURRENT_SCHEMA_VERSION
        stale_count = newer_markdown_count + len(missing_in_index) + len(deleted_from_vault) + int(schema_mismatch)
        payload.update(
            {
                "status_method": status_method,
                "indexed_at_utc": indexed_at_utc,
                "indexed_note_count": len(indexed_paths),
                "newer_markdown_count": newer_markdown_count,
                "missing_indexed_count": len(missing_in_index) + len(deleted_from_vault),
                "schema_mismatch": schema_mismatch,
                "index_stale": stale_count > 0,
                "reindex_hint": (
                    "Index appears stale; run the refresh command before relying on broad retrieval."
                    if stale_count > 0
                    else ""
                ),
            }
        )
    finally:
        con.close()
    return payload


def cmd_index(args: argparse.Namespace) -> int:
    vault_path = Path(args.vault).expanduser().resolve()
    db_path = Path(args.db).expanduser().resolve()
    excluded_dirs = set(DEFAULT_EXCLUDED_DIRS)
    excluded_dirs.update(args.exclude_dir)

    markdown_files = iter_markdown_files(vault_path, excluded_dirs=excluded_dirs)
    notes = [parse_markdown_note(vault_path, file_path) for file_path in markdown_files]
    rebuild_index(db_path=db_path, notes=notes)
    print(f"Indexed {len(notes)} notes into {db_path}")
    for note in notes:
        for warning in note.warnings:
            print(f"warning: {note.path}: {warning}", file=sys.stderr)
    return 0


def cmd_refresh(args: argparse.Namespace) -> int:
    vault_path = Path(args.vault).expanduser().resolve()
    db_path = Path(args.db).expanduser().resolve()
    if not vault_path.is_dir():
        raise NotADirectoryError(f"Vault root does not exist or is not a directory: {vault_path}")
    excluded_dirs = set(DEFAULT_EXCLUDED_DIRS)
    excluded_dirs.update(args.exclude_dir)
    markdown_files = iter_markdown_files(vault_path, excluded_dirs=excluded_dirs)
    payload = refresh_index(db_path=db_path, vault_root=vault_path, markdown_files=markdown_files)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    action = "rebuilt" if payload["rebuilt"] else ("refreshed" if payload["refreshed"] else "unchanged")
    print(
        f"Index {action}: +{payload['added']} ~{payload['modified']} "
        f"-{payload['deleted']} ={payload['unchanged']} (schema {payload['schema_version']})"
    )
    for warning in payload["warnings"]:
        print(f"warning: {warning}", file=sys.stderr)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    vault_path = Path(args.vault).expanduser().resolve()
    db_path = Path(args.db).expanduser().resolve()
    excluded_dirs = set(DEFAULT_EXCLUDED_DIRS)
    excluded_dirs.update(args.exclude_dir)
    payload = build_index_status(
        db_path=db_path,
        vault_path=vault_path,
        excluded_dirs=excluded_dirs,
        include_reindex_command=args.include_reindex_command,
    )
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"DB exists: {payload['db_exists']}")
    print(f"Status method: {payload['status_method']}")
    print(f"Indexed at: {payload['indexed_at_utc'] or '(unknown)'}")
    print(f"Vault Markdown files: {payload['vault_markdown_count']}")
    print(f"Indexed notes: {payload['indexed_note_count']}")
    print(f"Newer Markdown files: {payload['newer_markdown_count']}")
    print(f"Index path mismatches: {payload['missing_indexed_count']}")
    print(f"Index stale: {payload['index_stale']}")
    if payload["reindex_hint"]:
        print(payload["reindex_hint"])
    if "reindex_command" in payload:
        print(payload["reindex_command"])
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    db_path = Path(args.db).expanduser().resolve()
    require_existing_db(db_path)
    con = connect(db_path, read_only=True)
    anchor_note_id = None
    if args.anchor:
        anchor_row = resolve_note_spec(con, args.anchor)
        anchor_note_id = int(anchor_row["id"])

    results = query_sections(
        con,
        query=args.query,
        limit=args.limit,
        anchor_note_id=anchor_note_id,
    )

    if args.json:
        print(query_results_to_json(results))
        return 0

    if args.anchor:
        print(f"Anchor: {args.anchor}")
    print(f"Query: {args.query}")
    print()
    for idx, item in enumerate(results, start=1):
        heading = item.heading_path or "(document root)"
        print(f"{idx}. [{item.score:.2f}] {item.title} :: {heading}")
        print(f"   path: {item.path}")
        print(f"   why: {', '.join(item.reasons)}")
        print(f"   snippet: {item.snippet}")
        print()
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    db_path = Path(args.db).expanduser().resolve()
    require_existing_db(db_path)
    con = connect(db_path, read_only=True)
    note_row = resolve_note_spec(con, args.note)
    sections = note_sections(con, int(note_row["id"]))

    filtered = sections
    if args.section:
        needle = args.section.strip().lower()
        filtered = [
            row
            for row in sections
            if needle in (row["heading_path"] or "").lower()
        ]

    if args.json:
        payload = {
            "title": note_row["title"],
            "path": note_row["path"],
            "sections": [
                {
                    "heading_path": row["heading_path"],
                    "level": row["level"],
                    "snippet": row["snippet"],
                    "content": row["content"],
                }
                for row in filtered
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"{note_row['title']}")
    print(note_row["path"])
    print()
    for row in filtered:
        heading = row["heading_path"] or "(document root)"
        print(f"## {heading}")
        print(row["content"])
        print()
    return 0


def cmd_links(args: argparse.Namespace) -> int:
    db_path = Path(args.db).expanduser().resolve()
    require_existing_db(db_path)
    con = connect(db_path, read_only=True)
    note_row = resolve_note_spec(con, args.note)
    links = note_links(con, int(note_row["id"]), depth=args.depth)

    if args.json:
        print(json.dumps(links, ensure_ascii=False, indent=2))
        return 0

    print(f"Anchor: {note_row['title']}")
    print(note_row["path"])
    print()
    for idx, link in enumerate(links, start=1):
        print(f"{idx}. [depth={link['depth']}] {link['title']}")
        print(f"   path: {link['path']}")
        print(f"   why: {link['reason']}")
        print()
    return 0


def cmd_pdf_index(args: argparse.Namespace) -> int:
    library_path = Path(args.library).expanduser().resolve()
    db_path = Path(args.db).expanduser().resolve()
    excluded_dirs = set(DEFAULT_EXCLUDED_DIRS)
    excluded_dirs.update(args.exclude_dir)

    pdf_files = iter_pdf_files(
        library_path,
        excluded_dirs=excluded_dirs,
        path_contains=args.path_contains,
        max_files=args.max_files,
    )
    if not pdf_files:
        print("No PDF files matched the requested scope.")
        return 0

    warnings = 0
    failures = 0

    def iter_docs():
        nonlocal warnings, failures
        total = len(pdf_files)
        for index, file_path in enumerate(pdf_files, start=1):
            try:
                doc = parse_pdf_document(
                    library_root=library_path,
                    file_path=file_path,
                    pdftotext_exe=args.pdftotext or DEFAULT_PDFTOTEXT,
                )
            except Exception as exc:
                failures += 1
                rel_path = file_path.relative_to(library_path).as_posix()
                print(f"[{index}/{total}] FAILED {rel_path}: {exc}")
                continue
            if doc.extraction_warning:
                warnings += 1
            print(
                f"[{index}/{total}] {doc.path} "
                f"(pages={doc.page_count}, text_pages={doc.extracted_page_count})"
            )
            yield doc

    stats = rebuild_pdf_index(db_path=db_path, docs=iter_docs())
    print()
    print(
        "Indexed "
        f"{stats['docs']} PDFs, {stats['pages_with_text']} text-bearing pages "
        f"out of {stats['pages']} total pages into {db_path}"
    )
    if warnings:
        print(f"Warnings on extraction: {warnings} PDFs")
    if failures:
        print(f"Failed PDFs skipped: {failures}")
    return 0


def cmd_pdf_library_survey(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser().resolve()
    output_path = (
        Path(args.output).expanduser().resolve()
        if args.output
        else default_inventory_path(root)
    )
    stats = build_pdf_library_survey(
        root=root,
        output_path=output_path,
        excluded_dirs=set(DEFAULT_EXCLUDED_DIRS).union(args.exclude_dir),
        path_contains=args.path_contains,
        max_files=args.max_files,
        pdftotext_exe=args.pdftotext or DEFAULT_PDFTOTEXT,
    )
    if args.json:
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return 0

    print(f"Surveyed PDF library under {stats['root']}")
    print(f"Output: {stats['output_path']}")
    print(f"Matched PDFs: {stats['pdf_count']}")
    print(f"Surveyed PDFs: {stats['surveyed_count']}")
    print(f"Warnings: {stats['warning_count']}")
    return 0


def cmd_pdf_query(args: argparse.Namespace) -> int:
    db_path = Path(args.db).expanduser().resolve()
    require_existing_db(db_path)
    con = connect_pdf(db_path)
    results = query_pdf_pages(con, query=args.query, limit=args.limit)

    if args.json:
        print(query_pdf_results_to_json(results))
        return 0

    print(f"Query: {args.query}")
    print()
    for idx, item in enumerate(results, start=1):
        print(f"{idx}. [{item.score:.2f}] {item.title} :: page {item.page_number}")
        print(f"   path: {item.path}")
        print(f"   why: {', '.join(item.reasons)}")
        print(f"   snippet: {item.snippet}")
        print()
    return 0


def cmd_pdf_survey(args: argparse.Namespace) -> int:
    db_path = Path(args.db).expanduser().resolve()
    require_existing_db(db_path)
    con = connect_pdf(db_path)
    stats = rebuild_pdf_surveys(con, doc_specs=args.doc or None)

    if args.json:
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return 0

    scope = f"{stats['docs']} PDFs"
    if args.doc:
        scope = f"{stats['docs']} selected PDFs"
    print(f"Rebuilt survey metadata for {scope} in {db_path}")
    print(f"Sections indexed: {stats['sections']}")
    return 0


def cmd_pdf_survey_query(args: argparse.Namespace) -> int:
    db_path = Path(args.db).expanduser().resolve()
    require_existing_db(db_path)
    con = connect_pdf(db_path)
    doc_results = query_pdf_doc_surveys(con, query=args.query, limit=args.doc_limit)
    section_results = query_pdf_sections(con, query=args.query, limit=args.section_limit)

    if args.json:
        payload = {
            "query": args.query,
            "doc_results": [
                {
                    "score": round(item.score, 3),
                    "title": item.title,
                    "path": item.path,
                    "doc_type": item.doc_type,
                    "subject_tags": item.subject_tags,
                    "summary": item.summary,
                    "reasons": item.reasons,
                }
                for item in doc_results
            ],
            "section_results": [
                {
                    "score": round(item.score, 3),
                    "title": item.title,
                    "path": item.path,
                    "doc_type": item.doc_type,
                    "subject_tags": item.subject_tags,
                    "summary": item.summary,
                    "section_title": item.section_title,
                    "heading_path": item.heading_path,
                    "page_start": item.page_start,
                    "page_end": item.page_end,
                    "preview": item.preview,
                    "reasons": item.reasons,
                }
                for item in section_results
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"Query: {args.query}")
    print()
    print("Document survey matches:")
    if not doc_results:
        print("  (none)")
    for idx, item in enumerate(doc_results, start=1):
        tags = ", ".join(item.subject_tags) or "none"
        print(f"{idx}. [{item.score:.2f}] {item.title}")
        print(f"   path: {item.path}")
        print(f"   type: {item.doc_type}")
        print(f"   tags: {tags}")
        print(f"   why: {', '.join(item.reasons)}")
        print(f"   summary: {item.summary}")
        print()

    print("Section survey matches:")
    if not section_results:
        print("  (none)")
    for idx, item in enumerate(section_results, start=1):
        tags = ", ".join(item.subject_tags) or "none"
        print(
            f"{idx}. [{item.score:.2f}] {item.title} :: "
            f"{item.section_title} ({item.page_start}-{item.page_end})"
        )
        print(f"   path: {item.path}")
        print(f"   type: {item.doc_type}")
        print(f"   tags: {tags}")
        print(f"   heading: {item.heading_path}")
        print(f"   why: {', '.join(item.reasons)}")
        print(f"   preview: {item.preview}")
        print()
    return 0


def cmd_pdf_show(args: argparse.Namespace) -> int:
    db_path = Path(args.db).expanduser().resolve()
    require_existing_db(db_path)
    con = connect_pdf(db_path)
    doc_row = resolve_pdf_doc_spec(con, args.doc)
    summary = pdf_doc_summary(con, int(doc_row["id"]))
    survey_row = pdf_doc_survey(con, int(doc_row["id"])) if args.survey else None
    survey_sections = (
        pdf_doc_sections(con, int(doc_row["id"]))[: args.section_limit]
        if args.survey
        else []
    )

    if args.page is None:
        payload = {
            "title": summary["title"],
            "path": summary["path"],
            "page_count": summary["page_count"],
            "extracted_page_count": summary["extracted_page_count"],
            "extraction_warning": summary["extraction_warning"],
        }
        if survey_row is not None:
            payload["survey"] = {
                "doc_type": survey_row["doc_type"],
                "subject_tags": json.loads(survey_row["subject_tags_json"] or "[]"),
                "summary": survey_row["summary"],
                "section_count": survey_row["section_count"],
                "confidence": survey_row["confidence"],
                "sections": [
                    {
                        "title": row["title"],
                        "level": row["level"],
                        "page_start": row["page_start"],
                        "page_end": row["page_end"],
                        "heading_path": row["heading_path"],
                        "preview": row["preview"],
                        "keywords": json.loads(row["keywords_json"] or "[]"),
                    }
                    for row in survey_sections
                ],
            }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0

        print(summary["title"])
        print(summary["path"])
        print(
            f"pages: {summary['page_count']}, "
            f"text_pages: {summary['extracted_page_count']}"
        )
        if summary["extraction_warning"]:
            print(f"warning: {summary['extraction_warning']}")
        if survey_row is not None:
            tags = ", ".join(json.loads(survey_row["subject_tags_json"] or "[]")) or "none"
            print(f"type: {survey_row['doc_type']}")
            print(f"tags: {tags}")
            print(f"survey confidence: {float(survey_row['confidence']):.2f}")
            print(f"summary: {survey_row['summary']}")
            if survey_sections:
                print("sections:")
                for row in survey_sections:
                    print(
                        f"  - {row['title']} [{row['page_start']}-{row['page_end']}]"
                    )
        return 0

    page_row = pdf_page_content(con, int(doc_row["id"]), args.page)
    if page_row is None:
        raise ValueError(
            f"No extracted text for page {args.page} in {summary['path']}. "
            "This page may be image-only or outside the indexed range."
        )

    if args.json:
        payload = {
            "title": summary["title"],
            "path": summary["path"],
            "page_count": summary["page_count"],
            "page_number": page_row["page_number"],
            "char_count": page_row["char_count"],
            "snippet": page_row["snippet"],
            "content": page_row["content"],
            "extraction_warning": summary["extraction_warning"],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"{summary['title']} :: page {page_row['page_number']}")
    print(summary["path"])
    print()
    print(page_row["content"])
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local retrieval CLI for an Obsidian vault.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    index_parser = subparsers.add_parser("index", help="Build the local SQLite index.")
    index_parser.add_argument("--vault", required=True, help="Path to the Obsidian vault root.")
    index_parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite database path.")
    index_parser.add_argument(
        "--exclude-dir",
        action="append",
        default=[],
        help="Directory name to exclude while indexing. Can be passed multiple times.",
    )
    index_parser.set_defaults(func=cmd_index)

    refresh_parser = subparsers.add_parser(
        "refresh",
        help="Incrementally refresh a current index, or atomically rebuild a missing index or one with a different schema.",
    )
    refresh_parser.add_argument("--vault", required=True, help="Path to the Obsidian vault root.")
    refresh_parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite database path.")
    refresh_parser.add_argument(
        "--exclude-dir",
        action="append",
        default=[],
        help="Directory name to exclude while refreshing. Can be passed multiple times.",
    )
    refresh_parser.add_argument("--json", action="store_true", help="Return JSON output.")
    refresh_parser.set_defaults(func=cmd_refresh)

    status_parser = subparsers.add_parser("status", help="Check note-index freshness without reading note content.")
    status_parser.add_argument("--vault", required=True, help="Path to the Obsidian vault root.")
    status_parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite database path.")
    status_parser.add_argument(
        "--exclude-dir",
        action="append",
        default=[],
        help="Directory name to exclude while checking freshness. Can be passed multiple times.",
    )
    status_parser.add_argument(
        "--include-reindex-command",
        action="store_true",
        help="Include the local reindex command in the status payload.",
    )
    status_parser.add_argument("--json", action="store_true", help="Return JSON output.")
    status_parser.set_defaults(func=cmd_status)

    query_parser = subparsers.add_parser("query", help="Run a graph-aware local query.")
    query_parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite database path.")
    query_parser.add_argument("--query", required=True, help="Natural language or keyword query.")
    query_parser.add_argument("--anchor", help="Optional note spec to boost graph neighbors.")
    query_parser.add_argument("--limit", type=int, default=8, help="Maximum number of results.")
    query_parser.add_argument("--json", action="store_true", help="Return JSON output.")
    query_parser.set_defaults(func=cmd_query)

    show_parser = subparsers.add_parser("show", help="Show the content of a note or section.")
    show_parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite database path.")
    show_parser.add_argument("--note", required=True, help="Note title, relative path, or path key.")
    show_parser.add_argument("--section", help="Optional substring filter for heading path.")
    show_parser.add_argument("--json", action="store_true", help="Return JSON output.")
    show_parser.set_defaults(func=cmd_show)

    links_parser = subparsers.add_parser("links", help="Show linked neighbors around a note.")
    links_parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite database path.")
    links_parser.add_argument("--note", required=True, help="Note title, relative path, or path key.")
    links_parser.add_argument("--depth", type=int, default=1, choices=(1, 2), help="Neighborhood depth.")
    links_parser.add_argument("--json", action="store_true", help="Return JSON output.")
    links_parser.set_defaults(func=cmd_links)

    pdf_index_parser = subparsers.add_parser("pdf-index", help="Build a page-level SQLite index over PDFs.")
    pdf_index_parser.add_argument("--library", required=True, help="Path to the PDF library root.")
    pdf_index_parser.add_argument("--db", default=str(DEFAULT_PDF_DB), help="SQLite database path.")
    pdf_index_parser.add_argument(
        "--exclude-dir",
        action="append",
        default=[],
        help="Directory name to exclude while indexing. Can be passed multiple times.",
    )
    pdf_index_parser.add_argument("--path-contains", help="Optional substring filter on relative paths.")
    pdf_index_parser.add_argument("--max-files", type=int, help="Optional cap on number of PDFs to index.")
    pdf_index_parser.add_argument("--pdftotext", help="Optional explicit path to pdftotext executable.")
    pdf_index_parser.set_defaults(func=cmd_pdf_index)

    pdf_library_survey_parser = subparsers.add_parser(
        "pdf-library-survey",
        help="Build a book-level survey inventory for a PDF folder, even without a SQLite index.",
    )
    pdf_library_survey_parser.add_argument("--root", required=True, help="Path to the PDF library root.")
    pdf_library_survey_parser.add_argument(
        "--output",
        help="Optional JSON output path. Defaults to obsidian_local_kb/data/pdf_library_surveys/...",
    )
    pdf_library_survey_parser.add_argument(
        "--exclude-dir",
        action="append",
        default=[],
        help="Directory name to exclude while surveying. Can be passed multiple times.",
    )
    pdf_library_survey_parser.add_argument("--path-contains", help="Optional substring filter on relative paths.")
    pdf_library_survey_parser.add_argument("--max-files", type=int, help="Optional cap on number of PDFs to survey.")
    pdf_library_survey_parser.add_argument("--pdftotext", help="Optional explicit path to pdftotext executable.")
    pdf_library_survey_parser.add_argument("--json", action="store_true", help="Return JSON output.")
    pdf_library_survey_parser.set_defaults(func=cmd_pdf_library_survey)

    pdf_query_parser = subparsers.add_parser("pdf-query", help="Run a local query against indexed PDF pages.")
    pdf_query_parser.add_argument("--db", default=str(DEFAULT_PDF_DB), help="SQLite database path.")
    pdf_query_parser.add_argument("--query", required=True, help="Natural language or keyword query.")
    pdf_query_parser.add_argument("--limit", type=int, default=8, help="Maximum number of results.")
    pdf_query_parser.add_argument("--json", action="store_true", help="Return JSON output.")
    pdf_query_parser.set_defaults(func=cmd_pdf_query)

    pdf_survey_parser = subparsers.add_parser(
        "pdf-survey",
        help="Build or refresh book-level survey metadata for indexed PDFs.",
    )
    pdf_survey_parser.add_argument("--db", default=str(DEFAULT_PDF_DB), help="SQLite database path.")
    pdf_survey_parser.add_argument(
        "--doc",
        action="append",
        default=[],
        help="Optional PDF title, relative path, or path key. Can be passed multiple times.",
    )
    pdf_survey_parser.add_argument("--json", action="store_true", help="Return JSON output.")
    pdf_survey_parser.set_defaults(func=cmd_pdf_survey)

    pdf_survey_query_parser = subparsers.add_parser(
        "pdf-survey-query",
        help="Search book/section survey metadata for indexed PDFs.",
    )
    pdf_survey_query_parser.add_argument("--db", default=str(DEFAULT_PDF_DB), help="SQLite database path.")
    pdf_survey_query_parser.add_argument("--query", required=True, help="Natural language or keyword query.")
    pdf_survey_query_parser.add_argument("--doc-limit", type=int, default=8, help="Maximum number of document matches.")
    pdf_survey_query_parser.add_argument(
        "--section-limit",
        type=int,
        default=12,
        help="Maximum number of section matches.",
    )
    pdf_survey_query_parser.add_argument("--json", action="store_true", help="Return JSON output.")
    pdf_survey_query_parser.set_defaults(func=cmd_pdf_survey_query)

    pdf_show_parser = subparsers.add_parser("pdf-show", help="Show metadata or extracted text for an indexed PDF.")
    pdf_show_parser.add_argument("--db", default=str(DEFAULT_PDF_DB), help="SQLite database path.")
    pdf_show_parser.add_argument("--doc", required=True, help="PDF title, relative path, or path key.")
    pdf_show_parser.add_argument("--page", type=int, help="Optional page number to display.")
    pdf_show_parser.add_argument("--survey", action="store_true", help="Include survey metadata when available.")
    pdf_show_parser.add_argument(
        "--section-limit",
        type=int,
        default=12,
        help="Maximum number of survey sections to show with --survey.",
    )
    pdf_show_parser.add_argument("--json", action="store_true", help="Return JSON output.")
    pdf_show_parser.set_defaults(func=cmd_pdf_show)

    return parser


def main(argv: list[str] | None = None) -> int:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
