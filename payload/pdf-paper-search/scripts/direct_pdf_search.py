from __future__ import annotations

import argparse
from pdf_paper_search.public_output import SafeArgumentParser, parse_public_args, guarded_main
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

from pdf_paper_search.aliases import (
    detect_hard_concepts,
    signature_terms as shared_signature_terms,
    structural_cue_terms,
)
from pdf_paper_search.diagnostics import (
    compact_exception,
    diagnostics_to_dicts,
    get_diagnostics,
    record_diagnostic,
    reset_diagnostics,
    summarize_diagnostics,
)
from pdf_paper_search.features import build_page_evidence
from pdf_paper_search.paths import (
    ensure_project_on_path,
    resolve_project_root as shared_resolve_project_root,
)
from pdf_paper_search.normalization import matching_exact_anchors
from pdf_paper_search.query_spec import build_query_spec
from pdf_paper_search.scorer import score_breakdown as build_score_breakdown
from pdf_paper_search.types import PageEvidence, SearchDiagnostic


SCRIPT_PATH = Path(__file__).resolve()
DEFAULT_ROOT = Path.home() / "Documents"
DEFAULT_CACHE_DIR = DEFAULT_ROOT / ".pdf-paper-search-cache"
DEFAULT_PROJECT_ROOT = shared_resolve_project_root(SCRIPT_PATH)
ensure_project_on_path(DEFAULT_PROJECT_ROOT)

from obsidian_local_kb.pdf_library_survey import default_inventory_path, load_pdf_library_survey  # noqa: E402
from obsidian_local_kb.pdf_survey import build_pdf_doc_survey  # noqa: E402

GENERIC_WORDS = {
    "all",
    "and",
    "approach",
    "are",
    "based",
    "be",
    "by",
    "for",
    "from",
    "if",
    "in",
    "into",
    "is",
    "let",
    "method",
    "model",
    "most",
    "of",
    "one",
    "our",
    "paper",
    "propose",
    "proposed",
    "prove",
    "result",
    "results",
    "show",
    "that",
    "the",
    "theorem",
    "theorems",
    "then",
    "use",
    "using",
    "with",
}
DOMAIN_HINT_TERMS = {
    "algorithm",
    "algorithms",
    "attention",
    "complexity",
    "compiler",
    "cryptography",
    "database",
    "deep",
    "diffusion",
    "distributed",
    "generative",
    "graph",
    "language",
    "learning",
    "neural",
    "optimization",
    "reinforcement",
    "retrieval",
    "robotics",
    "systems",
    "theory",
    "transformer",
    "vision",
}
LOCAL_STATEMENT_ROLES = {"theorem", "definition", "algorithm", "results_table", "figure_caption"}
NEVER_EXACT_ROLES = {"contents", "references", "proof", "related_work", "abstract"}
NOISY_PAGE_TERMS = (
    "contents",
    "table of contents",
    "bibliography",
    "references",
    "index",
)
WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
LAST_FILE_SELECTION: dict[str, object] = {}


@dataclass(slots=True)
class PageHit:
    final_score: float
    title: str
    path: str
    page_number: int
    snippet: str
    classification: str
    reasons: list[str] = field(default_factory=list)
    file_score: float = 0.0
    page_score: float = 0.0
    features: dict[str, object] = field(default_factory=dict)
    score_breakdown: dict[str, float] = field(default_factory=dict)
    statement_window: str = ""


@dataclass(slots=True)
class SurveyMatch:
    score: float
    reasons: list[str] = field(default_factory=list)
    matched_ranges: list[tuple[int, int, float, str]] = field(default_factory=list)


def configure_streams() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def parse_args() -> argparse.Namespace:
    parser = SafeArgumentParser(
        description="Fallback search over local PDFs by filename inventory plus cached pdftotext extraction."
    )
    parser.add_argument("--query", required=True, help="Primary search query.")
    parser.add_argument(
        "--alias",
        action="append",
        default=[],
        help="Additional alias or reformulation. Can be passed multiple times.",
    )
    parser.add_argument(
        "--root",
        action="append",
        default=[],
        help="Directory root to scan recursively for PDFs. Defaults to ~/Documents.",
    )
    parser.add_argument(
        "--pdf-file",
        action="append",
        default=[],
        help="Explicit PDF file path. Can be passed multiple times.",
    )
    parser.add_argument("--limit", type=int, default=8, help="Maximum results to print.")
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Emit a compact top-3 evidence summary for model context.",
    )
    parser.add_argument(
        "--compact-limit",
        type=int,
        default=3,
        help="Maximum results to include with --compact.",
    )
    parser.add_argument(
        "--evidence-chars",
        type=int,
        default=220,
        help="Maximum snippet characters per compact result.",
    )
    parser.add_argument(
        "--why-limit",
        type=int,
        default=4,
        help="Maximum reason strings per compact result.",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=8,
        help="Maximum candidate PDFs to open after filename/path scoring.",
    )
    parser.add_argument(
        "--file-candidate-limit",
        type=int,
        default=0,
        help="Limit the scored PDF candidate list before opening files. Default keeps existing behavior.",
    )
    parser.add_argument(
        "--file-ranking-limit",
        type=int,
        default=5,
        help="Maximum file-ranking entries to include when --show-file-ranking is used.",
    )
    parser.add_argument(
        "--show-file-ranking",
        action="store_true",
        help="Include scored PDF file-selection details in JSON/compact output.",
    )
    parser.add_argument(
        "--score-files-only",
        action="store_true",
        help="Score and report candidate PDF files without extracting page text.",
    )
    parser.add_argument(
        "--cache-dir",
        default=str(DEFAULT_CACHE_DIR),
        help="Directory for cached pdftotext output.",
    )
    parser.add_argument(
        "--inventory",
        action="append",
        default=[],
        help="Optional JSON survey inventory path. Can be passed multiple times.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    parser.add_argument(
        "--alias-mode",
        choices=("expanded", "core", "full", "auto"),
        default="expanded",
        help="Alias expansion mode. Default keeps the expanded alias set.",
    )
    parser.add_argument(
        "--verify-rank",
        type=int,
        default=0,
        help="Include the local statement/evidence window for this 1-based rank.",
    )
    return parse_public_args(parser, route='direct')


def normalize_text(text: str) -> str:
    value = unicodedata.normalize("NFKC", text)
    replacements = {
        "π": "pi",
        "Π": "pi",
        "→": "->",
        "−": "-",
        "–": "-",
        "—": "-",
        "∗": "*",
        "·": " ",
        "×": " x ",
    }
    for source, target in replacements.items():
        value = value.replace(source, target)
    value = value.casefold()
    value = re.sub(r"[^\w\s\-\(\)\[\]\{\}\*\.:/]", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def unique_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = value.strip()
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def compact_text(text: str, limit: int) -> str:
    collapsed = re.sub(r"\s+", " ", text).strip()
    if limit <= 0 or len(collapsed) <= limit:
        return collapsed
    return collapsed[: max(0, limit - 3)].rstrip() + "..."


def compact_payload(
    *,
    query: str,
    aliases: list[str],
    pdf_paths: list[Path],
    hits: list[PageHit],
    result_limit: int,
    evidence_chars: int,
    why_limit: int,
    diagnostics: list[SearchDiagnostic] | None = None,
    file_selection: dict[str, object] | None = None,
) -> dict[str, object]:
    diagnostics = diagnostics or []
    payload: dict[str, object] = {
        "query": query,
        "alias_count": len(aliases),
        "pdf_count": len(pdf_paths),
        "result_count": len(hits),
        "shown": min(max(0, result_limit), len(hits)),
        "diagnostics_summary": summarize_diagnostics(diagnostics),
        "warnings": [
            item
            for item in diagnostics_to_dicts(
                [diagnostic for diagnostic in diagnostics if diagnostic.level != "info"][:8]
            )
        ],
        "results": [
            {
                "rank": rank,
                "score": round(item.final_score, 3),
                "title": item.title,
                "path": item.path,
                "page_number": item.page_number,
                "match_type": item.classification,
                "why": item.reasons[: max(0, why_limit)],
                "evidence": compact_text(item.snippet, evidence_chars),
                "features": {
                    key: value
                    for key, value in item.features.items()
                    if key
                    in {
                        "page_role",
                        "local_statement",
                        "direct_statement",
                        "hard_concepts_missing",
                        "classification",
                    }
                },
            }
            for rank, item in enumerate(hits[: max(0, result_limit)], start=1)
        ],
    }
    if file_selection is not None:
        payload["file_selection"] = file_selection
    return payload


def informative_words(text: str) -> list[str]:
    words = WORD_RE.findall(normalize_text(text))
    return [
        word
        for word in words
        if len(word) >= 3 and word not in GENERIC_WORDS
    ]


def symbolic_terms(text: str) -> list[str]:
    """Structure cues: numbered objects, arXiv ids, model variants, datasets and metrics."""

    return unique_keep_order(list(structural_cue_terms(text)))


def build_core_aliases(query: str, extra_aliases: list[str]) -> list[str]:
    try:
        aliases = list(build_query_spec(query, extra_aliases).aliases)
    except Exception as exc:
        record_diagnostic(
            SearchDiagnostic(
                level="warning",
                stage="query_spec",
                code="query_spec_failed",
                message=compact_exception(exc),
                exception_type=type(exc).__name__,
            )
        )
        aliases = [query, normalize_text(query), *extra_aliases]
    return unique_keep_order(aliases)[:10]


def build_aliases(query: str, extra_aliases: list[str], alias_mode: str = "expanded") -> list[str]:
    alias_mode = alias_mode if alias_mode in {"expanded", "core", "full", "auto"} else "expanded"
    if alias_mode == "core":
        return build_core_aliases(query, extra_aliases)
    normalized = normalize_text(query)
    aliases: list[str] = build_core_aliases(query, extra_aliases)
    aliases.extend([query, normalized, *extra_aliases])
    aliases = unique_keep_order(aliases)
    return aliases[:30] if alias_mode == "full" else aliases[:10]


def signature_terms(query: str, aliases: list[str]) -> list[str]:
    words: list[str] = []
    words.extend(shared_signature_terms([query, *aliases], limit=18))
    for text in [query, *aliases]:
        words.extend(informative_words(text))
        words.extend(symbolic_terms(text))
    return unique_keep_order(words)[:18]


def domain_hint_terms(signatures: list[str]) -> list[str]:
    return [term for term in signatures if term in DOMAIN_HINT_TERMS]


def path_score(pdf_path: Path, signatures: list[str]) -> tuple[float, list[str]]:
    haystack = normalize_text(str(pdf_path))
    path_tokens = set(WORD_RE.findall(haystack))
    score = 0.0
    reasons: list[str] = []
    matched: list[str] = []
    for term in signatures:
        normalized = normalize_text(term)
        if len(normalized) < 3:
            continue
        if " " in normalized:
            if normalized in haystack:
                matched.append(term)
        elif normalized in path_tokens:
            matched.append(term)
    if matched:
        score += 8.0 * len(matched)
        reasons.append(f"path/title matches {len(matched)} signature terms")
    domain_hits = [term for term in domain_hint_terms(signatures) if term in path_tokens]
    if domain_hits:
        score += 5.0 * len(domain_hits)
        reasons.append(f"path suggests {'/'.join(domain_hits[:4])}")
    return score, reasons


def discover_pdfs(
    explicit_files: list[Path],
    roots: list[Path],
    query: str,
    aliases: list[str],
    inventory_entries: dict[str, dict[str, object]] | None = None,
    max_files: int = 8,
    file_candidate_limit: int = 0,
    show_file_ranking: bool = False,
    file_ranking_limit: int = 5,
) -> list[Path]:
    global LAST_FILE_SELECTION
    if explicit_files:
        selected: list[Path] = []
        for path in explicit_files:
            resolved = path.expanduser().resolve()
            if resolved.exists():
                selected.append(resolved)
            else:
                record_diagnostic(
                    SearchDiagnostic(
                        level="error",
                        stage="pdf_discovery",
                        code="missing_pdf",
                        message="Explicit PDF file does not exist.",
                        path=str(resolved),
                    )
                )
        LAST_FILE_SELECTION = {
            "root_count": 0,
            "considered_pdf_count": len(explicit_files),
            "selected_pdf_count": len(selected),
            "positive_score_count": len(selected),
            "all_scores_zero": False,
            "max_files": max_files,
        }
        return selected

    scan_roots = [path.expanduser().resolve() for path in roots] if roots else [DEFAULT_ROOT.resolve()]
    signatures = signature_terms(query, aliases)
    scored: list[tuple[float, Path]] = []
    seen: set[Path] = set()
    inventory_entries = inventory_entries or {}

    for root in scan_roots:
        if not root.exists():
            record_diagnostic(
                SearchDiagnostic(
                    level="warning",
                    stage="pdf_discovery",
                    code="missing_root",
                    message="PDF scan root does not exist.",
                    path=str(root),
                )
            )
            continue
        for pdf_path in root.rglob("*.pdf"):
            resolved = pdf_path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            score, _ = path_score(resolved, signatures)
            inventory_entry = inventory_entries.get(str(resolved).casefold())
            if inventory_entry is not None:
                survey_match = survey_match_score(
                    inventory_entry,
                    query=query,
                    aliases=aliases,
                    signatures=signatures,
                )
                score += survey_match.score
            scored.append((score, resolved))

    scored.sort(key=lambda item: (-item[0], str(item[1]).casefold()))
    considered_count = len(scored)
    if file_candidate_limit > 0:
        scored = scored[:file_candidate_limit]
    positive = [path for score, path in scored if score > 0]
    if positive:
        selected = positive[:max_files]
    else:
        selected = [path for _, path in scored[:max_files]]
    LAST_FILE_SELECTION = {
        "root_count": len(scan_roots),
        "considered_pdf_count": considered_count,
        "selected_pdf_count": len(selected),
        "positive_score_count": len(positive),
        "all_scores_zero": bool(scored) and not positive,
        "max_files": max_files,
    }
    if show_file_ranking:
        LAST_FILE_SELECTION["ranking"] = [
            {"score": round(score, 3), "path": str(path)}
            for score, path in scored[: max(0, file_ranking_limit)]
        ]
    return selected


def find_pdftotext() -> str:
    candidates: list[str | Path | None] = [shutil.which("pdftotext")]
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        program_files = os.environ.get("ProgramFiles")
        if local_app_data:
            candidates.append(Path(local_app_data) / "Programs" / "MiKTeX" / "miktex" / "bin" / "x64" / "pdftotext.exe")
        if program_files:
            candidates.append(Path(program_files) / "MiKTeX" / "miktex" / "bin" / "x64" / "pdftotext.exe")
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(candidate)
    raise FileNotFoundError("Could not locate `pdftotext`.")


def cache_path_for(pdf_path: Path, cache_dir: Path) -> Path:
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", pdf_path.stem)[:80]
    digest = hashlib.sha1(str(pdf_path).encode("utf-8")).hexdigest()[:12]
    return cache_dir / f"{safe_stem}-{digest}.txt"


def survey_cache_path_for(pdf_path: Path, cache_dir: Path) -> Path:
    return cache_path_for(pdf_path, cache_dir).with_suffix(".survey.json")


def extract_text(pdf_path: Path, cache_dir: Path, pdftotext_path: str) -> str:
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "logs").mkdir(parents=True, exist_ok=True)
    txt_path = cache_path_for(pdf_path, cache_dir)
    if not txt_path.exists() or txt_path.stat().st_mtime < pdf_path.stat().st_mtime:
        env = dict(os.environ)
        env["MIKTEX_LOG_DIR"] = str((cache_dir / "logs").resolve())
        completed = subprocess.run(
            [pdftotext_path, "-enc", "UTF-8", "-layout", str(pdf_path), str(txt_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        if completed.returncode != 0 and not txt_path.exists():
            stderr = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(f"pdftotext failed for {pdf_path}: {stderr}")
    return txt_path.read_text(encoding="utf-8", errors="replace")


def page_fragments(text: str) -> list[str]:
    pages = text.split("\f")
    if len(pages) > 1 and not pages[-1].strip():
        pages = pages[:-1]
    return pages if pages else [text]


def load_inventory_entries(
    roots: list[Path],
    explicit_inventory_paths: list[Path],
) -> dict[str, dict[str, object]]:
    inventory_paths: list[Path] = []
    seen: set[str] = set()
    for path in explicit_inventory_paths:
        resolved = path.expanduser().resolve()
        key = str(resolved).casefold()
        if key in seen or not resolved.exists():
            continue
        seen.add(key)
        inventory_paths.append(resolved)
    for root in roots:
        resolved_root = root.expanduser().resolve()
        auto_path = default_inventory_path(resolved_root)
        key = str(auto_path).casefold()
        if key in seen or not auto_path.exists():
            continue
        seen.add(key)
        inventory_paths.append(auto_path)

    entries: dict[str, dict[str, object]] = {}
    for inventory_path in inventory_paths:
        try:
            payload = load_pdf_library_survey(inventory_path)
        except Exception as exc:
            record_diagnostic(
                SearchDiagnostic(
                    level="warning",
                    stage="inventory_load",
                    code="inventory_load_failed",
                    message=compact_exception(exc),
                    path=str(inventory_path),
                    exception_type=type(exc).__name__,
                )
            )
            continue
        for entry in payload.get("entries", []):
            if not isinstance(entry, dict):
                continue
            path = str(entry.get("path", "")).strip()
            if not path:
                continue
            entries[path.casefold()] = entry
    return entries


def extract_survey(
    pdf_path: Path,
    cache_dir: Path,
    pdftotext_path: str,
) -> dict[str, object]:
    survey_path = survey_cache_path_for(pdf_path, cache_dir)
    if survey_path.exists() and survey_path.stat().st_mtime >= pdf_path.stat().st_mtime:
        return json.loads(survey_path.read_text(encoding="utf-8"))

    text = extract_text(pdf_path, cache_dir, pdftotext_path)
    pages = page_fragments(text)
    survey = build_pdf_doc_survey(
        title=pdf_path.stem,
        path=str(pdf_path),
        pages=[
            (page_number, page_text.strip())
            for page_number, page_text in enumerate(pages, start=1)
            if page_text.strip()
        ],
    )
    payload: dict[str, object] = {
        "path": str(pdf_path.resolve()),
        "title": pdf_path.stem,
        "page_count": len(pages),
        "doc_type": survey.doc_type,
        "subject_tags": survey.subject_tags,
        "summary": survey.summary,
        "summary_norm": survey.summary_norm,
        "search_text": survey.search_text,
        "confidence": survey.confidence,
        "section_count": len(survey.sections),
        "sections": [asdict(section) for section in survey.sections],
    }
    survey_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def survey_match_score(
    survey_entry: dict[str, object],
    *,
    query: str,
    aliases: list[str],
    signatures: list[str],
) -> SurveyMatch:
    title = str(survey_entry.get("title", ""))
    doc_type = str(survey_entry.get("doc_type", ""))
    subject_tags = [str(tag) for tag in survey_entry.get("subject_tags", []) if str(tag).strip()]
    summary = str(survey_entry.get("summary", ""))
    sections = [item for item in survey_entry.get("sections", []) if isinstance(item, dict)]
    doc_blob = normalize_text(
        " ".join(
            [
                title,
                doc_type.replace("_", " "),
                " ".join(tag.replace("_", " ") for tag in subject_tags),
                summary,
            ]
        )
    )

    score = 0.0
    reasons: list[str] = []
    matched_ranges: list[tuple[int, int, float, str]] = []

    for alias in [query, *aliases]:
        normalized = normalize_text(alias)
        if len(normalized) < 18:
            continue
        if normalized in doc_blob:
            score += 24.0
            reasons.append("survey summary contains a long alias phrase")
            break

    matched_terms = [term for term in signatures if term in doc_blob]
    if matched_terms:
        score += min(26.0, 3.5 * len(matched_terms))
        reasons.append(f"survey matches {len(matched_terms)}/{len(signatures)} signature terms")

    if subject_tags:
        tag_blob = normalize_text(" ".join(tag.replace("_", " ") for tag in subject_tags))
        tag_hits = [term for term in signatures if term in tag_blob]
        if tag_hits:
            score += min(10.0, 5.0 + 1.5 * len(tag_hits))
            reasons.append("survey subject tags match the query domain")

    for section in sections[:80]:
        section_title = str(section.get("title", ""))
        heading_path = str(section.get("heading_path", ""))
        keywords = [str(word) for word in section.get("keywords", []) if str(word).strip()]
        section_blob = normalize_text(
            " ".join([section_title, heading_path, " ".join(keywords), str(section.get("preview", ""))])
        )
        if not section_blob:
            continue
        section_hits = [term for term in signatures if term in section_blob]
        section_score = 0.0
        if section_hits:
            section_score += min(20.0, 5.0 + 2.8 * len(section_hits))
        for alias in [query, *aliases]:
            normalized = normalize_text(alias)
            if len(normalized) < 18:
                continue
            if normalized in section_blob:
                section_score += 16.0
                break
        if section_score <= 0.0:
            continue
        page_start = int(section.get("page_start", 0) or 0)
        page_end = int(section.get("page_end", page_start) or page_start)
        label = section_title or heading_path or "matched survey section"
        matched_ranges.append((page_start, page_end, min(16.0, 4.0 + 0.45 * section_score), label))

    if matched_ranges:
        score += max(boost for _, _, boost, _ in matched_ranges)
        reasons.append("survey section titles suggest the target chapter")

    return SurveyMatch(score=score, reasons=reasons, matched_ranges=matched_ranges)


def survey_page_range_bonus(
    page_number: int,
    matched_ranges: list[tuple[int, int, float, str]],
) -> tuple[float, list[str]]:
    best_bonus = 0.0
    best_reason: str | None = None
    near_bonus = 0.0
    near_reason: str | None = None
    for page_start, page_end, boost, label in matched_ranges:
        if page_start <= page_number <= page_end:
            if boost > best_bonus:
                best_bonus = boost
                best_reason = f"page falls inside matched survey section: {label}"
        elif page_start - 1 <= page_number <= page_end + 1:
            edge_boost = max(3.0, 0.35 * boost)
            if edge_boost > near_bonus:
                near_bonus = edge_boost
                near_reason = f"page is adjacent to matched survey section: {label}"
    if best_reason is not None:
        return best_bonus, [best_reason]
    if near_reason is not None:
        return near_bonus, [near_reason]
    return 0.0, []


def looks_like_contents_page(page_text: str) -> bool:
    head = normalize_text(page_text[:1500])
    chapter_count = len(re.findall(r"\bchapter\b", head))
    section_count = len(re.findall(r"\b\d+\.\d+\b", head))
    page_count = len(re.findall(r"\b\d{2,4}\b", head))
    return chapter_count >= 2 and section_count >= 3 and page_count >= 6


ROLE_HEAD_PATTERNS = {
    "theorem": r"\b(?:theorem|lemma|proposition|corollary)\s+\d+(?:\.\d+)*",
    "definition": r"\bdefinition\s+\d+(?:\.\d+)*",
    "algorithm": r"\balgorithm\s+\d+(?:\.\d+)*",
    "results_table": r"\b(?:table|tab\.)\s*\d+(?:\.\d+)*\s*[:.]",
    "figure_caption": r"\b(?:figure|fig\.)\s*\d+(?:\.\d+)*\s*[:.]",
}


def detect_page_role(page_text: str) -> str:
    head = normalize_text(page_text[:1600])
    if looks_like_contents_page(page_text):
        return "contents"
    if re.match(r"\s*(?:\d+\.?\s+)?(?:references|bibliography)\b", head):
        return "references"
    if re.search(r"(?:^|\s)proofs?(?:\s+of\s+(?:theorem|lemma|proposition|corollary)\s+\d+(?:\.\d+)*)?\s*[.:]", head[:240]):
        return "proof"
    if re.search(r"\b(?:solutions?|answers?)\s+(?:to|for|manual)\b", head[:240]):
        return "proof"
    if re.search(r"\b(?:related work|prior work)\b", head[:600]):
        return "related_work"
    for role, pattern in ROLE_HEAD_PATTERNS.items():
        if re.search(pattern, head):
            return role
    if re.search(r"\babstract\b", head[:900]):
        return "abstract"
    return "unknown"


def local_statement_window(page_text: str, signatures: list[str], *, fallback_chars: int = 900) -> str:
    role = detect_page_role(page_text)
    patterns: list[re.Pattern[str]] = []
    if role in ROLE_HEAD_PATTERNS:
        patterns.append(re.compile(ROLE_HEAD_PATTERNS[role] + r".*", re.I | re.S))
    for pattern in patterns:
        match = pattern.search(page_text)
        if match:
            start = max(0, match.start() - 40)
            return page_text[start : start + fallback_chars]
    return make_snippet(page_text, signatures)


def alias_matches(page_low: str, aliases: list[str]) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    for alias in aliases:
        normalized = normalize_text(alias)
        if len(normalized) < 20:
            continue
        if normalized in page_low:
            score += 40.0
            reasons.append("contains long alias phrase")
            break
    return score, reasons


def page_feature_score(
    page_text: str,
    query: str,
    aliases: list[str],
    signatures: list[str],
    exact_anchors: tuple[str, ...] = (),
) -> tuple[float, list[str], str]:
    page_low = normalize_text(page_text)
    raw_low = unicodedata.normalize("NFKC", page_text).casefold()
    head_low = normalize_text(page_text[:900])
    signature_set = {normalize_text(term) for term in signatures}
    score = 0.0
    reasons: list[str] = []

    alias_score, alias_reasons = alias_matches(page_low, [query, *aliases])
    score += alias_score
    reasons.extend(alias_reasons)

    matched_terms = [
        term
        for term in signatures
        if term in page_low or term in raw_low
    ]
    if matched_terms:
        score += 7.0 * len(matched_terms)
        reasons.append(f"matches {len(matched_terms)}/{len(signatures)} signature terms")

    cue_terms = [term for term in symbolic_terms(" ".join([query, *aliases])) if normalize_text(term) in signature_set]
    cue_hits = [term for term in cue_terms if normalize_text(term) in page_low or normalize_text(term).replace("-", "") in page_low]
    if cue_hits:
        score += min(42.0, 14.0 * len(cue_hits))
        reasons.append(f"keeps structural cue(s): {', '.join(cue_hits[:3])}")
    page_role = detect_page_role(page_text)
    if page_role in LOCAL_STATEMENT_ROLES:
        score += 8.0
        reasons.append(f"labeled {page_role.replace('_', ' ')} page")
    elif page_role in {"proof", "related_work"}:
        score -= 8.0
        reasons.append(f"{page_role.replace('_', ' ')} page, not the original statement")
    anchors_found = matching_exact_anchors(exact_anchors, page_text)
    anchors_ok = len(anchors_found) == len(exact_anchors)
    if exact_anchors and anchors_ok:
        score += 18.0
        reasons.append("keeps exact structural anchor(s)")
    elif exact_anchors:
        score -= 14.0
        reasons.append("missing structural anchor(s)")
    if any(term in head_low for term in NOISY_PAGE_TERMS):
        score -= 40.0
        reasons.append("index/contents-like page")
    if looks_like_contents_page(page_text):
        score -= 50.0
        reasons.append("dense chapter/section listing page")

    required_hard = detect_hard_concepts(" ".join([query, *aliases]))
    found_hard = detect_hard_concepts(local_statement_window(page_text, signatures))
    keeps_all_hard = required_hard.issubset(found_hard)
    long_alias_direct = alias_score > 0
    if (
        long_alias_direct
        and keeps_all_hard
        and anchors_ok
        and len(matched_terms) >= 5
        and page_role not in NEVER_EXACT_ROLES
        and not looks_like_contents_page(page_text)
    ):
        classification = "exact hit"
    elif len(matched_terms) >= 5 and anchors_ok and (keeps_all_hard or not required_hard):
        classification = "near-exact"
    else:
        classification = "nearby material"
    return score, reasons, classification


def make_snippet(page_text: str, signatures: list[str]) -> str:
    text = re.sub(r"\s+", " ", page_text).strip()
    lowered = text.casefold()
    start = 0
    for term in signatures:
        idx = lowered.find(term.casefold())
        if idx != -1:
            start = max(0, idx - 120)
            break
    snippet = text[start : start + 420]
    return snippet


def aggregate_hits(
    pdf_paths: list[Path],
    aliases: list[str],
    limit: int,
    primary_query: str,
    cache_dir: Path | None = None,
    inventory_entries: dict[str, dict[str, object]] | None = None,
) -> list[PageHit]:
    if not pdf_paths:
        return []

    try:
        pdftotext_path = find_pdftotext()
    except Exception as exc:
        record_diagnostic(
            SearchDiagnostic(
                level="error",
                stage="pdf_extract",
                code="pdf_extraction_failed",
                message=compact_exception(exc),
                exception_type=type(exc).__name__,
            )
        )
        return []
    signatures = signature_terms(primary_query, aliases)
    exact_anchors = build_query_spec(primary_query).exact_anchors
    cache_root = (cache_dir or DEFAULT_CACHE_DIR).expanduser().resolve()
    inventory_entries = inventory_entries or {}

    hits: list[PageHit] = []
    for pdf_path in pdf_paths:
        path_file_score, file_reasons = path_score(pdf_path, signatures)
        try:
            inventory_entry = inventory_entries.get(str(pdf_path.resolve()).casefold())
            if inventory_entry is None:
                inventory_entry = extract_survey(pdf_path, cache_root, pdftotext_path)
            survey_match = survey_match_score(
                inventory_entry,
                query=primary_query,
                aliases=aliases,
                signatures=signatures,
            )
            file_score = path_file_score + survey_match.score
            text = extract_text(pdf_path, cache_root, pdftotext_path)
        except Exception as exc:
            record_diagnostic(
                SearchDiagnostic(
                    level="error",
                    stage="pdf_extract",
                    code="pdf_extraction_failed",
                    message=compact_exception(exc),
                    path=str(pdf_path),
                    exception_type=type(exc).__name__,
                )
            )
            continue
        for page_number, page_text in enumerate(page_fragments(text), start=1):
            page_score, page_reasons, classification = page_feature_score(
                page_text=page_text,
                query=primary_query,
                aliases=aliases,
                signatures=signatures,
                exact_anchors=exact_anchors,
            )
            range_bonus, range_reasons = survey_page_range_bonus(
                page_number,
                survey_match.matched_ranges,
            )
            page_score += range_bonus
            if page_score <= 0:
                continue
            snippet = make_snippet(page_text, signatures)
            page_role = detect_page_role(page_text)
            statement_window = local_statement_window(page_text, signatures)
            evidence_text = statement_window or snippet
            required_hard = detect_hard_concepts(" ".join([primary_query, *aliases]))
            evidence = build_page_evidence(
                page_role=page_role,
                local_statement=page_role in LOCAL_STATEMENT_ROLES,
                direct_statement=classification == "exact hit",
                hard_concepts_required=required_hard,
                evidence_text=evidence_text,
                statement_window=statement_window,
                overview_reference=page_role == "related_work" or "overview" in normalize_text(evidence_text),
                hint_solution=page_role == "proof",
                proof_ingredient="proof" in normalize_text(evidence_text) and classification != "exact hit",
                noisy_page=page_role in {"contents", "references"} or looks_like_contents_page(page_text),
            )
            hits.append(
                PageHit(
                    final_score=file_score + page_score,
                    title=pdf_path.stem,
                    path=str(pdf_path),
                    page_number=page_number,
                    snippet=snippet,
                    classification=classification,
                    reasons=[*file_reasons, *survey_match.reasons, *range_reasons, *page_reasons],
                    file_score=file_score,
                    page_score=page_score,
                    features=evidence.to_features(classification=classification),
                    score_breakdown=build_score_breakdown(
                        file_component=file_score,
                        page_component=page_score,
                        survey_range_component=range_bonus,
                    ),
                    statement_window=evidence.statement_window or snippet,
                )
            )

    hits.sort(
        key=lambda item: (
            -item.final_score,
            item.classification != "exact hit",
            item.classification != "near-exact",
            item.title.casefold(),
            item.page_number,
        )
    )
    return hits[:limit]


@guarded_main
def main() -> int:
    configure_streams()
    reset_diagnostics()
    args = parse_args()
    alias_mode = str(args.alias_mode)
    aliases = build_aliases(
        args.query,
        args.alias,
        "core" if alias_mode == "auto" else alias_mode,
    )
    roots = [Path(item) for item in args.root]
    inventory_entries = load_inventory_entries(
        roots=roots,
        explicit_inventory_paths=[Path(item) for item in args.inventory],
    )
    pdf_paths = discover_pdfs(
        explicit_files=[Path(item) for item in args.pdf_file],
        roots=roots,
        query=args.query,
        aliases=aliases,
        inventory_entries=inventory_entries,
        max_files=args.max_files,
        file_candidate_limit=args.file_candidate_limit,
        show_file_ranking=args.show_file_ranking or args.score_files_only,
        file_ranking_limit=args.file_ranking_limit,
    )
    if args.score_files_only:
        diagnostics = get_diagnostics()
        payload = {
            "query": args.query,
            "aliases": aliases,
            "pdfs": [str(path) for path in pdf_paths],
            "file_selection": LAST_FILE_SELECTION,
            "diagnostics": diagnostics_to_dicts(diagnostics),
            "diagnostics_summary": summarize_diagnostics(diagnostics),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if pdf_paths else 1
    hits = aggregate_hits(
        pdf_paths=pdf_paths,
        aliases=aliases,
        limit=args.limit,
        primary_query=args.query,
        cache_dir=Path(args.cache_dir),
        inventory_entries=inventory_entries,
    )
    if alias_mode == "auto" and (not hits or all(hit.classification == "nearby material" for hit in hits[:3])):
        expanded_aliases = build_aliases(args.query, args.alias, "expanded")
        if expanded_aliases != aliases:
            aliases = expanded_aliases
            pdf_paths = discover_pdfs(
                explicit_files=[Path(item) for item in args.pdf_file],
                roots=roots,
                query=args.query,
                aliases=aliases,
                inventory_entries=inventory_entries,
                max_files=args.max_files,
                file_candidate_limit=args.file_candidate_limit,
                show_file_ranking=args.show_file_ranking,
                file_ranking_limit=args.file_ranking_limit,
            )
            hits = aggregate_hits(
                pdf_paths=pdf_paths,
                aliases=aliases,
                limit=args.limit,
                primary_query=args.query,
                cache_dir=Path(args.cache_dir),
                inventory_entries=inventory_entries,
            )
    diagnostics = get_diagnostics()
    verify_window = None
    if args.verify_rank > 0 and args.verify_rank <= len(hits):
        hit = hits[args.verify_rank - 1]
        verify_window = {
            "rank": args.verify_rank,
            "title": hit.title,
            "path": hit.path,
            "page_number": hit.page_number,
            "statement_window": hit.statement_window or hit.snippet,
            "evidence": hit.features,
        }

    if args.compact:
        payload = compact_payload(
            query=args.query,
            aliases=aliases,
            pdf_paths=pdf_paths,
            hits=hits,
            result_limit=args.compact_limit,
            evidence_chars=args.evidence_chars,
            why_limit=args.why_limit,
            diagnostics=diagnostics,
            file_selection=LAST_FILE_SELECTION,
        )
        if verify_window:
            payload["verify_window"] = verify_window
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.json:
        payload = {
            "query": args.query,
            "aliases": aliases,
            "pdfs": [str(path) for path in pdf_paths],
            "results": [asdict(hit) for hit in hits],
            "file_selection": LAST_FILE_SELECTION,
            "diagnostics": diagnostics_to_dicts(diagnostics),
            "diagnostics_summary": summarize_diagnostics(diagnostics),
        }
        if verify_window:
            payload["verify_window"] = verify_window
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"Query: {args.query}")
    print(f"Aliases: {', '.join(aliases)}")
    print(f"PDFs: {len(pdf_paths)}")
    if diagnostics:
        print(f"Diagnostics: {summarize_diagnostics(diagnostics)}")
    print()
    for index, hit in enumerate(hits, start=1):
        print(f"{index}. [{hit.final_score:.2f}] {hit.title} :: page {hit.page_number}")
        print(f"   path: {hit.path}")
        print(f"   classification: {hit.classification}")
        print(f"   reasons: {', '.join(hit.reasons)}")
        print(f"   snippet: {hit.snippet}")
        print()
    if verify_window:
        print("Verify window:")
        print(json.dumps(verify_window, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
