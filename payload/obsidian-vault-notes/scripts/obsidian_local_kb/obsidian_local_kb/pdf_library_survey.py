from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .pdf_extract import DEFAULT_PDFTOTEXT, extract_pdf_text, iter_pdf_files, split_pdf_pages
from .pdf_survey import PdfDocSurvey, PdfSurveySection, build_pdf_doc_survey


DEFAULT_EXCLUDED_DIRS = {".obsidian", ".trash"}
DEFAULT_SURVEY_DIR = Path(__file__).resolve().parent.parent / "data" / "pdf_library_surveys"


def inventory_filename(root: Path) -> str:
    digest = hashlib.sha1(str(root.resolve()).encode("utf-8")).hexdigest()[:12]
    return f"pdf-library-survey-{digest}.json"


def default_inventory_path(root: Path, base_dir: Path | None = None) -> Path:
    return (base_dir or DEFAULT_SURVEY_DIR).resolve() / inventory_filename(root)


def _serialize_section(section: PdfSurveySection) -> dict[str, object]:
    return asdict(section)


def _serialize_survey(
    *,
    root: Path,
    file_path: Path,
    page_count: int,
    survey: PdfDocSurvey,
) -> dict[str, object]:
    return {
        "path": str(file_path.resolve()),
        "relative_path": file_path.relative_to(root).as_posix(),
        "title": file_path.stem,
        "page_count": page_count,
        "doc_type": survey.doc_type,
        "subject_tags": survey.subject_tags,
        "summary": survey.summary,
        "summary_norm": survey.summary_norm,
        "search_text": survey.search_text,
        "confidence": survey.confidence,
        "section_count": len(survey.sections),
        "sections": [_serialize_section(section) for section in survey.sections],
    }


def build_pdf_library_survey(
    *,
    root: Path,
    output_path: Path | None = None,
    excluded_dirs: set[str] | None = None,
    path_contains: str | None = None,
    max_files: int | None = None,
    pdftotext_exe: str | None = None,
) -> dict[str, object]:
    root = root.expanduser().resolve()
    survey_path = (output_path or default_inventory_path(root)).expanduser().resolve()
    survey_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_files = iter_pdf_files(
        root,
        excluded_dirs=excluded_dirs or set(DEFAULT_EXCLUDED_DIRS),
        path_contains=path_contains,
        max_files=max_files,
    )

    entries: list[dict[str, object]] = []
    warnings: list[dict[str, str]] = []
    pdftotext_path = pdftotext_exe or DEFAULT_PDFTOTEXT

    for file_path in pdf_files:
        try:
            raw_text, warning = extract_pdf_text(file_path, pdftotext_exe=pdftotext_path)
        except Exception as exc:
            warnings.append(
                {
                    "path": str(file_path),
                    "warning": str(exc),
                }
            )
            continue

        raw_pages = split_pdf_pages(raw_text)
        survey = build_pdf_doc_survey(
            title=file_path.stem,
            path=str(file_path),
            pages=[
                (page_number, page_text.strip())
                for page_number, page_text in enumerate(raw_pages, start=1)
                if page_text.strip()
            ],
        )
        entry = _serialize_survey(
            root=root,
            file_path=file_path,
            page_count=len(raw_pages),
            survey=survey,
        )
        if warning:
            entry["extraction_warning"] = warning
        entries.append(entry)

    payload = {
        "root": str(root),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pdf_count": len(pdf_files),
        "surveyed_count": len(entries),
        "warning_count": len(warnings),
        "entries": entries,
        "warnings": warnings,
    }
    survey_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "root": str(root),
        "output_path": str(survey_path),
        "pdf_count": len(pdf_files),
        "surveyed_count": len(entries),
        "warning_count": len(warnings),
    }


def load_pdf_library_survey(path: Path) -> dict[str, object]:
    return json.loads(path.expanduser().resolve().read_text(encoding="utf-8"))
