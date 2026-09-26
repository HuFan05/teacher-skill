from __future__ import annotations

import argparse
import importlib.util
import random
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

from case_schema import ensure_external_case_path, validate_case, write_cases


SCRIPT_DIR = Path(__file__).resolve().parent
SEARCH_SCRIPT = SCRIPT_DIR / "paper_search.py"
NUMBER_PATTERN = r"(?P<number>(?:\d\s*){1,3}(?:\.\s*(?:\d\s*){1,3})*)"
KIND_ROLES = {
    "statement": {"theorem", "lemma", "proposition", "corollary", "definition"},
    "algorithm": {"algorithm"},
    "result": {"results_table", "figure_caption"},
}
LOCAL_ANCHOR_RES = {
    "statement": (
        re.compile(
            r"(?:^|[\n\f]|[.!?])\s*(?P<label>Theorem|Lemma|Proposition|Corollary|Definition)\s+"
            + NUMBER_PATTERN
            + r"\b",
            flags=re.IGNORECASE,
        ),
    ),
    "algorithm": (
        re.compile(
            r"(?:^|[\n\f])\s*(?P<label>Algorithm)\s+" + NUMBER_PATTERN + r"\b",
            flags=re.IGNORECASE,
        ),
    ),
    "result": (
        re.compile(
            r"(?:^|[\n\f])\s*(?P<label>Table|Figure|Fig\.)\s*" + NUMBER_PATTERN + r"\s*[:.]",
            flags=re.IGNORECASE,
        ),
    ),
}
ANCHOR_PROMPT_RE = re.compile(
    r"\b(?:let|suppose|given|for any|for every|if|there exists|we define|input|require|"
    r"procedure|initialize|accuracy|bleu|results?|comparison|ablation|architecture)\b",
    flags=re.IGNORECASE,
)
NEXT_LABELED_ITEM_RE = re.compile(
    r"[\n\f]\s*(?:Theorem|Lemma|Proposition|Corollary|Definition|Algorithm|Table|Figure)\s+"
    r"(?:\d\s*){1,3}(?:\.\s*(?:\d\s*){1,3})*\b",
    flags=re.IGNORECASE,
)
STRONG_CROSS_REFERENCE_MARKERS = (
    "related work",
    "et al.",
    "proof.",
    "proof of",
    "see section",
    "as shown in",
)
ANCHOR_SELECTION_PENALTIES = (
    "see table",
    "see figure",
    "see theorem",
    "see algorithm",
    "cf.",
    *STRONG_CROSS_REFERENCE_MARKERS,
)
ANCHOR_GENERIC_WORDS = {
    "theorem",
    "lemma",
    "proposition",
    "corollary",
    "definition",
    "algorithm",
    "table",
    "figure",
    "for",
    "with",
    "let",
    "suppose",
    "show",
    "prove",
    "that",
    "then",
    "the",
    "and",
}
DEDUPLICATE_ANCHOR_PATTERNS = (
    re.compile(
        r"^(?P<label>Theorem|Lemma|Definition|Algorithm|Table|Figure)\s+(?P<number>\d+)[.:]\s+"
        r"(?P=label)\s+(?P=number)[.:)]?\s*",
        flags=re.IGNORECASE,
    ),
)
NOISY_DOC_MARKERS = (
    "course announcement",
    "wordpress",
    "http://",
    "https://",
    "solutions manual",
    " slides ",
)


def configure_streams() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")


def load_paper_search_module():
    spec = importlib.util.spec_from_file_location("paper_search_sampler_target", SEARCH_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sample draft statement/algorithm/result search cases from explicit indexed PDF shelves."
    )
    parser.add_argument(
        "--db",
        action="append",
        default=[],
        help="Explicit SQLite PDF database path. May be repeated; each shelf is one sampling stratum.",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="External output JSONL path. Real sampled cases must not be written into the Skill tree.",
    )
    parser.add_argument("--seed", type=int, default=1729, help="Random seed.")
    parser.add_argument("--statement-count", type=int, default=30, help="Number of theorem/definition cases.")
    parser.add_argument("--algorithm-count", type=int, default=20, help="Number of algorithm-box cases.")
    parser.add_argument("--result-count", type=int, default=30, help="Number of table/figure cases.")
    parser.add_argument(
        "--dev-fraction",
        type=float,
        default=0.65,
        help="Fraction of each sampled kind assigned to the development split.",
    )
    parser.add_argument(
        "--max-per-doc",
        type=int,
        default=6,
        help="Maximum sampled pages per document within one kind.",
    )
    parser.add_argument(
        "--min-char-count",
        type=int,
        default=500,
        help="Minimum extracted character count for a page to be considered.",
    )
    parser.add_argument("--limit", type=int, default=10, help="Stored case result limit.")
    return parser.parse_args()


def resolve_dbs(explicit: list[str]) -> tuple[list[Path], dict[str, str]]:
    paths = [Path(item).expanduser().resolve() for item in explicit]
    return paths, {str(path): path.stem for path in paths}


def collapse_ws(text: str, limit: int | None = None) -> str:
    collapsed = re.sub(r"\s+", " ", text).strip()
    if limit is None or len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3].rstrip() + "..."


def normalize_number(text: str) -> str:
    return re.sub(r"\s+", "", text).strip().strip(".")


def sanitize_anchor_text(anchor_text: str) -> str:
    cleaned = collapse_ws(anchor_text, limit=220)
    for pattern in DEDUPLICATE_ANCHOR_PATTERNS:
        cleaned = pattern.sub(
            lambda match: f"{match.group('label').title()} {match.group('number')}. ",
            cleaned,
            count=1,
        )
    return cleaned


def anchor_penalty(anchor_text: str) -> int:
    lowered = anchor_text.casefold()
    penalty = 0
    if any(marker in lowered for marker in ANCHOR_SELECTION_PENALTIES):
        penalty += 30
    return penalty


def trim_anchor_source(source: str, start: int, *, window: int = 320) -> str:
    segment = source[start : start + window]
    boundary = NEXT_LABELED_ITEM_RE.search(segment[40:])
    if boundary is None:
        return segment
    return segment[: 40 + boundary.start()]


def extract_anchor_text(content: str, snippet: str, kind: str) -> tuple[str, bool]:
    source = content[:6000]
    best_anchor: str | None = None
    best_score: int | None = None
    best_start: int | None = None

    for pattern in LOCAL_ANCHOR_RES[kind]:
        for match in pattern.finditer(source):
            anchor = sanitize_anchor_text(trim_anchor_source(source, match.start()))
            score = 0
            prefix = source[max(0, match.start() - 3) : match.start()]
            if match.start() == 0 or "\n" in prefix or "\f" in prefix:
                score += 8
            if ANCHOR_PROMPT_RE.search(anchor):
                score += 6
            score -= anchor_penalty(anchor)
            if (
                best_score is None
                or score > best_score
                or (
                    score == best_score
                    and best_start is not None
                    and match.start() < best_start
                )
            ):
                best_score = score
                best_start = match.start()
                best_anchor = anchor

    if best_anchor is not None:
        return best_anchor, (best_score or 0) >= 0
    return collapse_ws(snippet or content, limit=220), False


def informative_word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z]{3,}", text))


def anchor_content_words(text: str) -> list[str]:
    words = re.findall(r"[A-Za-z]{3,}", text.casefold())
    return [word for word in words if word not in ANCHOR_GENERIC_WORDS]


def alpha_density(text: str) -> float:
    if not text:
        return 0.0
    alpha_chars = sum(1 for char in text if char.isalpha())
    return alpha_chars / len(text)


def should_keep_candidate(title: str, path: str, snippet: str, anchor_text: str, kind: str) -> bool:
    combined = " ".join([title, path, snippet, anchor_text]).casefold()
    if any(marker in combined for marker in NOISY_DOC_MARKERS):
        return False
    if any(marker in anchor_text.casefold() for marker in STRONG_CROSS_REFERENCE_MARKERS):
        return False
    if informative_word_count(anchor_text) < 3:
        return False
    if len(anchor_text) < 32:
        return False
    if len(anchor_content_words(anchor_text)) < 2:
        return False
    if kind != "result" and alpha_density(anchor_text) < 0.45:
        return False
    return True


def load_candidates(
    module,
    db_paths: list[Path],
    *,
    db_subjects: dict[str, str],
    min_char_count: int,
) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {kind: [] for kind in KIND_ROLES}

    for db_path in db_paths:
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                """
                SELECT
                    d.title,
                    d.path,
                    p.page_number,
                    p.snippet,
                    p.content,
                    p.char_count
                FROM pdf_pages p
                JOIN pdf_docs d ON d.id = p.doc_id
                WHERE p.char_count >= ?
                ORDER BY d.title, p.page_number
                """,
                (min_char_count,),
            ).fetchall()
        finally:
            con.close()

        for row in rows:
            title = str(row["title"])
            path = str(row["path"])
            page_number = int(row["page_number"])
            snippet = str(row["snippet"])
            content = str(row["content"])
            role = module.detect_page_role(title, path, content)
            kind = next((name for name, roles in KIND_ROLES.items() if role in roles), None)
            if kind is None:
                continue

            anchor_text, has_local_anchor = extract_anchor_text(
                content=content, snippet=snippet, kind=kind
            )
            if not has_local_anchor:
                continue
            candidate = {
                "db": str(db_path),
                "subject": db_subjects.get(str(db_path), "unclassified"),
                "title": title,
                "path": path,
                "page_number": page_number,
                "doc_key": f"{db_path}::{title}",
                "role": role,
                "char_count": int(row["char_count"]),
                "anchor_text": anchor_text,
                "query": anchor_text,
                "snippet": collapse_ws(snippet, limit=240),
            }
            if not should_keep_candidate(title, path, candidate["snippet"], anchor_text, kind):
                continue
            grouped[kind].append(candidate)
    return grouped


def stratified_sample(
    candidates: list[dict],
    count: int,
    max_per_doc: int,
    rng: random.Random,
) -> list[dict]:
    if count <= 0:
        return []
    by_subject: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for candidate in candidates:
        by_subject[candidate["subject"]][candidate["doc_key"]].append(candidate)

    subjects = list(by_subject)
    rng.shuffle(subjects)
    if not subjects:
        raise ValueError("No candidates available for sampling.")

    titles_by_subject: dict[str, list[str]] = {}
    title_positions: dict[str, dict[str, int]] = {}
    subject_indices: dict[str, int] = {}
    per_doc_counts: dict[str, int] = defaultdict(int)

    for subject, docs in by_subject.items():
        titles = list(docs)
        rng.shuffle(titles)
        titles_by_subject[subject] = titles
        title_positions[subject] = {}
        subject_indices[subject] = 0
        for title in titles:
            rng.shuffle(docs[title])
            title_positions[subject][title] = 0

    picked: list[dict] = []

    while len(picked) < count:
        progressed = False
        for subject in subjects:
            titles = titles_by_subject[subject]
            if not titles:
                continue
            attempts = 0
            while attempts < len(titles):
                title = titles[subject_indices[subject] % len(titles)]
                subject_indices[subject] += 1
                attempts += 1
                if per_doc_counts[title] >= max_per_doc:
                    continue
                items = by_subject[subject][title]
                position = title_positions[subject][title]
                if position >= len(items):
                    continue
                candidate = items[position]
                title_positions[subject][title] += 1
                picked.append(candidate)
                per_doc_counts[title] += 1
                progressed = True
                break
            if len(picked) >= count:
                break
        if not progressed:
            break

    if len(picked) < count:
        raise ValueError(
            f"Requested {count} samples but only found {len(picked)} after stratification."
        )
    return picked


def build_cases(
    sampled: list[dict],
    *,
    kind: str,
    dev_count: int,
    dbs: list[Path],
    seed: int,
    limit: int,
) -> list[dict]:
    cases: list[dict] = []
    counters = {"development": 0, "holdout": 0}

    for index, candidate in enumerate(sampled):
        split = "development" if index < dev_count else "holdout"
        counters[split] += 1
        reference = {
            "title": candidate["title"],
            "page_number": candidate["page_number"],
            "db": candidate["db"],
            "path": candidate["path"],
            "anchor_text": candidate["anchor_text"],
            "role": candidate["role"],
        }
        case = {
            "id": f"sample-{kind}-{split}-{counters[split]:03d}",
            "query": candidate["query"],
            "dbs": [str(path) for path in dbs],
            "limit": limit,
            "split": split,
            "sample_kind": kind,
            "source_gold": [dict(reference)],
            "semantic_gold": [dict(reference)],
            "notes": (
                "Auto-sampled draft case. Verify the page, compress the query if needed, "
                "and add semantic equivalents or forbidden near-misses after inspection."
            ),
            "sampling": {
                "seed": seed,
                "source_db": candidate["db"],
                "subject": candidate["subject"],
                "char_count": candidate["char_count"],
                "snippet": candidate["snippet"],
            },
        }
        cases.append(validate_case(case))
    return cases


def print_distribution(label: str, cases: list[dict]) -> None:
    counts: dict[str, int] = defaultdict(int)
    for case in cases:
        counts[case["source_gold"][0]["title"]] += 1
    print(f"{label}: {len(cases)} cases")
    for title, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        print(f"  {count:2d}  {title}")


def print_subject_distribution(cases: list[dict]) -> None:
    counts: dict[str, int] = defaultdict(int)
    for case in cases:
        counts[str(case.get("sampling", {}).get("subject", "unknown"))] += 1
    print("subject distribution:")
    for subject, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        print(f"  {count:2d}  {subject}")


def main() -> int:
    configure_streams()
    args = parse_args()
    out_path = ensure_external_case_path(Path(args.out), SCRIPT_DIR.parent)
    db_paths, db_subjects = resolve_dbs(args.db)
    if not db_paths:
        raise SystemExit("Pass at least one explicit --db shelf to sample from.")
    rng = random.Random(args.seed)
    module = load_paper_search_module()

    candidates = load_candidates(
        module,
        db_paths=db_paths,
        db_subjects=db_subjects,
        min_char_count=args.min_char_count,
    )
    counts = {
        "statement": args.statement_count,
        "algorithm": args.algorithm_count,
        "result": args.result_count,
    }
    cases: list[dict] = []
    for kind, count in counts.items():
        sample = stratified_sample(
            candidates[kind],
            count=count,
            max_per_doc=args.max_per_doc,
            rng=rng,
        )
        cases.extend(
            build_cases(
                sample,
                kind=kind,
                dev_count=round(len(sample) * max(0.0, min(1.0, args.dev_fraction))),
                dbs=db_paths,
                seed=args.seed,
                limit=args.limit,
            )
        )

    write_cases(out_path, cases)
    print(f"Wrote {len(cases)} draft cases to {out_path}")
    print_subject_distribution(cases)
    for kind in counts:
        print_distribution(kind, [case for case in cases if case["sample_kind"] == kind])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
