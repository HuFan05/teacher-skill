from __future__ import annotations

import argparse
import json
from pdf_paper_search.public_output import SafeArgumentParser, parse_public_args, guarded_main
import sys
from pathlib import Path

from pdf_paper_search.locator import (
    PAPER_LOCATE_SCHEMA_VERSION,
    bound_payload,
    bounded_json,
    locate_paper,
)
from pdf_paper_search.normalization import PAPER_CANONICALIZER_VERSION


def configure_streams() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def parse_args() -> argparse.Namespace:
    parser = SafeArgumentParser(
        description="Return an answer-ready, adjacent-page-verified CS/AI paper PDF locator."
    )
    parser.add_argument("--query", required=True, help="Algorithm, theorem, definition, equation, result, figure, claim, or source-location query.")
    parser.add_argument(
        "--alias",
        action="append",
        default=[],
        help="One explicit alias. May be repeated; internal expansion still runs at most once.",
    )
    parser.add_argument(
        "--db",
        required=True,
        help="One explicit SQLite PDF index. Automatic database discovery is forbidden.",
    )
    parser.add_argument("--limit", type=int, default=12, help="Candidate ranking limit (3-30).")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Accepted for compatibility; this answer-ready command always emits JSON.",
    )
    return parse_public_args(parser, route='locate')


@guarded_main
def main() -> int:
    configure_streams()
    args = parse_args()
    try:
        payload = locate_paper(
            query=args.query,
            db_path=Path(args.db),
            extra_aliases=args.alias,
            limit=args.limit,
        )
    except Exception as exc:
        payload = {
            "schema_version": PAPER_LOCATE_SCHEMA_VERSION,
            "canonicalizer_version": PAPER_CANONICALIZER_VERSION,
            "route": "explicit-index",
            "status": "failed",
            "query": {
                "query_type": None,
                "hard_concepts": [],
                "signature_terms": [],
            },
            "search": {
                "alias_mode": "core",
                "expanded": False,
                "candidate_count": 0,
                "candidate_pdf_count": 0,
                "verified_page_count": 0,
                "adjacent_page_count": 0,
                "stop_reason": "unexpected_error",
            },
            "results": [],
            "coverage": {
                "document_status_counts": {},
                "incomplete": True,
                "diagnostics_summary": {"errors": 1, "warnings": 0, "info": 0},
                "warnings": [
                    {
                        "level": "error",
                        "stage": "paper_locate",
                        "code": "unexpected_error",
                        "exception_type": type(exc).__name__,
                    }
                ],
            },
            "timing_ms": {"core": 0, "expanded": 0, "verify": 0, "total": 0},
        }
    wire_payload = payload
    print(json.dumps(wire_payload, ensure_ascii=False, separators=(',', ':')))
    status = str(wire_payload.get("status"))
    if status in {"verified_hit", "ambiguous", "not_found_in_indexed_text"}:
        return 0
    if status == "coverage_gap":
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
