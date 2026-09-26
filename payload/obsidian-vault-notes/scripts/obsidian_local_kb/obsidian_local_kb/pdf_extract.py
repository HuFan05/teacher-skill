from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import uuid

from .util import compact_snippet, normalize_text


DEFAULT_PDFTOTEXT = shutil.which("pdftotext")


@dataclass(slots=True)
class PdfPageData:
    page_number: int
    content: str
    snippet: str
    char_count: int


@dataclass(slots=True)
class PdfDocData:
    path: str
    title: str
    title_norm: str
    page_count: int
    extracted_page_count: int
    pages: list[PdfPageData]
    extraction_warning: str | None = None


def iter_pdf_files(
    library_path: Path,
    excluded_dirs: set[str],
    path_contains: str | None = None,
    max_files: int | None = None,
) -> list[Path]:
    files: list[Path] = []
    path_filter = normalize_text(path_contains or "")

    for file_path in library_path.rglob("*.pdf"):
        rel_parts = file_path.relative_to(library_path).parts
        if any(part.startswith(".") for part in rel_parts):
            continue
        if any(part in excluded_dirs for part in rel_parts[:-1]):
            continue
        rel_path = file_path.relative_to(library_path).as_posix()
        if path_filter and path_filter not in normalize_text(rel_path):
            continue
        files.append(file_path)

    files.sort()
    if max_files is not None:
        return files[: max_files]
    return files


def parse_pdf_document(
    library_root: Path,
    file_path: Path,
    pdftotext_exe: str | None = None,
) -> PdfDocData:
    rel_path = file_path.relative_to(library_root).as_posix()
    title = file_path.stem
    raw_text, warning = extract_pdf_text(file_path, pdftotext_exe=pdftotext_exe)
    raw_pages = split_pdf_pages(raw_text)
    pages: list[PdfPageData] = []

    for page_number, raw_page in enumerate(raw_pages, start=1):
        content = raw_page.strip()
        if not content:
            continue
        pages.append(
            PdfPageData(
                page_number=page_number,
                content=content,
                snippet=compact_snippet(content),
                char_count=len(content),
            )
        )

    return PdfDocData(
        path=rel_path,
        title=title,
        title_norm=normalize_pdf_key(title),
        page_count=len(raw_pages),
        extracted_page_count=len(pages),
        pages=pages,
        extraction_warning=warning,
    )


def normalize_pdf_key(value: str) -> str:
    normalized = normalize_text(value).strip("/")
    if normalized.endswith(".pdf"):
        normalized = normalized[:-4]
    return normalized


def pdf_key_from_relative_path(rel_path: str) -> str:
    return normalize_pdf_key(rel_path.replace("\\", "/"))


def split_pdf_pages(raw_text: str) -> list[str]:
    pages = raw_text.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    if not pages and raw_text:
        return [raw_text]
    return pages


def extract_pdf_text(file_path: Path, pdftotext_exe: str | None = None) -> tuple[str, str | None]:
    executable = pdftotext_exe or DEFAULT_PDFTOTEXT
    if not executable:
        raise FileNotFoundError(
            "Could not find `pdftotext` on PATH. Install Poppler or MiKTeX's pdftotext first."
        )

    work_root = Path(__file__).resolve().parent.parent / "data" / "_pdf_tmp"
    work_root.mkdir(parents=True, exist_ok=True)
    temp_dir = work_root / f"pdf_kb_extract_{uuid.uuid4().hex}"
    temp_dir.mkdir(parents=True, exist_ok=False)

    proc: subprocess.CompletedProcess[str] | None = None
    try:
        linked_pdf_path = temp_dir / "input.pdf"
        try:
            os.link(file_path, linked_pdf_path)
        except OSError:
            shutil.copy2(file_path, linked_pdf_path)

        proc = subprocess.run(
            [executable, "-enc", "UTF-8", "-layout", str(linked_pdf_path), "-"],
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    if proc is None:
        raise RuntimeError(f"Failed to extract PDF text for {file_path}")

    stdout = proc.stdout or ""
    stderr = (proc.stderr or "").strip()

    if not stdout and proc.returncode != 0:
        message = stderr or f"pdftotext failed for {file_path}"
        raise RuntimeError(message)

    warning = summarize_pdftotext_warning(stderr=stderr, stdout=stdout)
    return stdout, warning


def summarize_pdftotext_warning(stderr: str, stdout: str) -> str | None:
    if not stderr:
        return None

    known_noisy_markers = (
        "log4cxx:",
        "did not succeed.",
        "the log file hopefully contains the information",
    )
    lowered = stderr.lower()
    if stdout and any(marker in lowered for marker in known_noisy_markers):
        return None
    return compact_snippet(stderr, limit=300)
