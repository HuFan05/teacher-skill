from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .util import compact_snippet, make_search_blob, normalize_text


LINE_WS_RE = re.compile(r"\s+")
DECIMAL_SECTION_RE = re.compile(
    r"^(?P<number>\d+(?:\.\d+){1,4})\.?\s+(?P<title>[A-Za-z][^\n]{3,120})$",
    flags=re.IGNORECASE,
)
CHAPTER_RE = re.compile(
    r"^(?P<label>chapter|chap\.?|part)\s+(?P<number>[0-9ivxlcdm]+)(?:[.: -]+(?P<title>[^\n]{2,120}))?$",
    flags=re.IGNORECASE,
)
LECTURE_RE = re.compile(
    r"^(?P<label>lecture|lec\.?)\s+(?P<number>[0-9ivxlcdm-]+)(?:[.: -]+(?P<title>[^\n]{2,120}))?$",
    flags=re.IGNORECASE,
)
TEXTBOOK_STOP_PREFIXES = (
    "theorem",
    "lemma",
    "proposition",
    "corollary",
    "exercise",
    "problem",
    "example",
    "remark",
    "definition",
    "proof",
    "figure",
    "table",
)
NOISY_HEADING_MARKERS = (
    "contents",
    "table of contents",
    "index",
    "bibliography",
    "references",
    "preface",
    "copyright",
)
DOC_TYPE_HINTS: dict[str, tuple[str, ...]] = {
    "paper": ("arxiv", "proceedings of", "related work", "we propose", "our method"),
    "solutions": ("solution", "solutions", "hints", "answer key"),
    "problem_book": ("problem", "problems", "qual", "exam", "pset", "hw", "worksheet"),
    "notes": ("lecture", "lectures", "notes", "course", "seminar", "lec "),
    "survey": ("survey", "introduction", "overview", "guide"),
}
SUBJECT_HINTS: dict[str, tuple[str, ...]] = {
    "algorithms": (
        "algorithm design",
        "analysis of algorithms",
        "dynamic programming",
        "greedy algorithm",
        "divide and conquer",
        "shortest path",
        "minimum spanning tree",
        "network flow",
        "approximation algorithm",
        "randomized algorithm",
    ),
    "data_structures": (
        "data structure",
        "hash table",
        "binary search tree",
        "priority queue",
        "linked list",
        "union find",
        "amortized analysis",
    ),
    "theory_of_computation": (
        "theory of computation",
        "computational complexity",
        "np-complete",
        "np-hard",
        "polynomial time",
        "turing machine",
        "decidability",
        "finite automata",
        "context-free grammar",
        "reduction",
    ),
    "machine_learning": (
        "machine learning",
        "supervised learning",
        "unsupervised learning",
        "generalization",
        "overfitting",
        "cross-validation",
        "support vector machine",
        "kernel method",
        "pac learning",
    ),
    "deep_learning": (
        "deep learning",
        "neural network",
        "backpropagation",
        "convolutional",
        "transformer",
        "self-attention",
        "batch normalization",
        "residual connection",
        "dropout",
    ),
    "reinforcement_learning": (
        "reinforcement learning",
        "markov decision process",
        "policy gradient",
        "q-learning",
        "value function",
        "actor-critic",
        "multi-armed bandit",
    ),
    "natural_language_processing": (
        "natural language processing",
        "language model",
        "tokenization",
        "machine translation",
        "word embedding",
        "named entity recognition",
        "question answering",
    ),
    "computer_vision": (
        "computer vision",
        "image classification",
        "object detection",
        "semantic segmentation",
        "optical flow",
        "image generation",
        "diffusion model",
    ),
    "optimization": (
        "optimization",
        "convex optimization",
        "gradient descent",
        "stochastic gradient",
        "learning rate",
        "lagrangian",
        "linear programming",
    ),
    "probability_statistics": (
        "probability",
        "random variable",
        "expectation",
        "bayesian inference",
        "concentration inequality",
        "maximum likelihood",
        "hypothesis testing",
    ),
    "linear_algebra": (
        "linear algebra",
        "vector space",
        "matrix",
        "eigenvalue",
        "singular value decomposition",
        "linear map",
    ),
    "systems": (
        "operating system",
        "virtual memory",
        "file system",
        "distributed system",
        "consensus protocol",
        "fault tolerance",
        "concurrency control",
    ),
    "databases": (
        "database system",
        "query optimization",
        "relational algebra",
        "transaction processing",
        "sql",
    ),
    "networking": (
        "computer network",
        "congestion control",
        "routing protocol",
        "packet switching",
        "tcp/ip",
    ),
    "programming_languages": (
        "programming language",
        "type system",
        "lambda calculus",
        "operational semantics",
        "compiler",
        "static analysis",
        "program verification",
    ),
    "security": (
        "computer security",
        "cryptography",
        "encryption",
        "authentication",
        "adversarial example",
        "threat model",
    ),
}


@dataclass(slots=True)
class PdfSurveySection:
    title: str
    title_norm: str
    level: str
    page_start: int
    page_end: int
    heading_path: str
    preview: str
    keywords: list[str]
    search_text: str


@dataclass(slots=True)
class PdfDocSurvey:
    doc_type: str
    subject_tags: list[str]
    summary: str
    summary_norm: str
    search_text: str
    confidence: float
    sections: list[PdfSurveySection]


def normalize_heading_text(text: str) -> str:
    return LINE_WS_RE.sub(" ", text).strip(" -:.\t")


def informative_words(text: str, limit: int = 8) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for word in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", normalize_text(text)):
        if word in seen:
            continue
        seen.add(word)
        result.append(word)
        if len(result) >= limit:
            break
    return result


def leading_lines(page_text: str, max_lines: int = 24) -> list[str]:
    lines: list[str] = []
    for raw_line in page_text.splitlines():
        line = normalize_heading_text(raw_line)
        if not line:
            continue
        if len(line) > 140:
            line = line[:140].rstrip()
        lines.append(line)
        if len(lines) >= max_lines:
            break
    return lines


def is_heading_noise(text: str) -> bool:
    lowered = normalize_text(text)
    if not lowered:
        return True
    if lowered.startswith(TEXTBOOK_STOP_PREFIXES):
        return True
    if any(marker in lowered for marker in NOISY_HEADING_MARKERS):
        return True
    if re.fullmatch(r"[0-9ivxlcdm.\- ]+", lowered):
        return True
    return False


def combine_heading_lines(first: str, second: str | None) -> str:
    if second and not is_heading_noise(second):
        combined = normalize_heading_text(f"{first} {second}")
        if len(combined) <= 140:
            return combined
    return normalize_heading_text(first)


def detect_page_heading(page_text: str) -> tuple[str, str] | None:
    lines = leading_lines(page_text)
    for index, line in enumerate(lines[:10]):
        if is_heading_noise(line):
            continue

        decimal_match = DECIMAL_SECTION_RE.match(line)
        if decimal_match:
            heading = normalize_heading_text(
                f"{decimal_match.group('number')} {decimal_match.group('title')}"
            )
            return heading, "section"

        chapter_match = CHAPTER_RE.match(line)
        if chapter_match:
            title = chapter_match.group("title")
            if not title and index + 1 < len(lines):
                title = lines[index + 1]
            heading = combine_heading_lines(
                f"{chapter_match.group('label').title()} {chapter_match.group('number')}",
                title,
            )
            return heading, "chapter"

        lecture_match = LECTURE_RE.match(line)
        if lecture_match:
            title = lecture_match.group("title")
            if not title and index + 1 < len(lines):
                title = lines[index + 1]
            heading = combine_heading_lines(
                f"{lecture_match.group('label').title()} {lecture_match.group('number')}",
                title,
            )
            return heading, "lecture"

        if 0 < index <= 2 and len(line) >= 8 and line.isupper() and re.search(r"[A-Z]", line):
            return line.title(), "chapter"
    return None


def build_section_keywords(title: str, preview: str) -> list[str]:
    words = informative_words(f"{title} {preview}", limit=10)
    return words[:6]


def extract_sections_from_pages(
    title: str,
    path: str,
    pages: list[tuple[int, str]],
) -> list[PdfSurveySection]:
    sections: list[PdfSurveySection] = []
    last_key: tuple[str, int] | None = None

    for page_number, content in pages:
        heading = detect_page_heading(content)
        if heading is None:
            continue
        heading_text, level = heading
        title_norm = normalize_text(heading_text)
        key = (title_norm, page_number)
        if last_key == key:
            continue
        preview = compact_snippet(content, limit=240)
        keywords = build_section_keywords(heading_text, preview)
        search_text = make_search_blob(title, path, heading_text, preview, " ".join(keywords))
        sections.append(
            PdfSurveySection(
                title=heading_text,
                title_norm=title_norm,
                level=level,
                page_start=page_number,
                page_end=page_number,
                heading_path=heading_text,
                preview=preview,
                keywords=keywords,
                search_text=search_text,
            )
        )
        last_key = key

    for index, section in enumerate(sections):
        next_start = pages[-1][0] if pages else section.page_start
        if index + 1 < len(sections):
            next_start = sections[index + 1].page_start - 1
        section.page_end = max(section.page_start, next_start)
    return sections


def infer_doc_type(title: str, path: str, sections: list[PdfSurveySection], pages: list[tuple[int, str]]) -> str:
    haystack = normalize_text(" ".join([title, path]))
    early_text = normalize_text(" ".join(content for _, content in pages[:3]))
    section_text = normalize_text(" ".join(section.title for section in sections[:12]))
    combined = " ".join([haystack, early_text, section_text])

    for doc_type, hints in DOC_TYPE_HINTS.items():
        if any(hint in combined for hint in hints):
            return doc_type

    problem_hits = combined.count("exercise") + combined.count("problem")
    theorem_hits = combined.count("theorem") + combined.count("lemma") + combined.count("proposition")
    if problem_hits >= max(3, theorem_hits + 1):
        return "problem_book"
    return "textbook"


def infer_subject_tags(title: str, path: str, sections: list[PdfSurveySection], pages: list[tuple[int, str]]) -> list[str]:
    early_text = " ".join(content for _, content in pages[:4])
    heading_text = " ".join(section.title for section in sections[:16])
    combined = normalize_text(" ".join([title, path, heading_text, early_text]))

    scored: list[tuple[int, str]] = []
    for label, hints in SUBJECT_HINTS.items():
        score = 0
        for hint in hints:
            if hint in combined:
                score += 2 if " " in hint else 1
        if score:
            scored.append((score, label))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return [label for _, label in scored[:4]]


def build_doc_summary(doc_type: str, subject_tags: list[str], sections: list[PdfSurveySection]) -> str:
    parts: list[str] = [doc_type.replace("_", " ")]
    if subject_tags:
        parts.append("topics: " + ", ".join(tag.replace("_", " ") for tag in subject_tags[:3]))
    if sections:
        parts.append("sections: " + "; ".join(section.title for section in sections[:4]))
    return " | ".join(parts)


def build_pdf_doc_survey(title: str, path: str, pages: list[tuple[int, str]]) -> PdfDocSurvey:
    sections = extract_sections_from_pages(title, path, pages)
    doc_type = infer_doc_type(title, path, sections, pages)
    subject_tags = infer_subject_tags(title, path, sections, pages)
    summary = build_doc_summary(doc_type, subject_tags, sections)
    search_text = make_search_blob(
        title,
        path,
        doc_type.replace("_", " "),
        " ".join(tag.replace("_", " ") for tag in subject_tags),
        summary,
        " ".join(section.title for section in sections[:16]),
    )
    confidence = min(
        1.0,
        0.25 + 0.1 * min(4, len(subject_tags)) + 0.08 * min(6, len(sections)),
    )
    return PdfDocSurvey(
        doc_type=doc_type,
        subject_tags=subject_tags,
        summary=summary,
        summary_norm=normalize_text(summary),
        search_text=search_text,
        confidence=confidence,
        sections=sections,
    )


def encode_json(value: list[str]) -> str:
    return json.dumps(value, ensure_ascii=False)
