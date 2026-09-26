#!/usr/bin/env python3
"""Validate the backend routing catalog and promoted evidence links."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


REQUIRED_COLUMNS = (
    "route_id",
    "task_class",
    "conditions",
    "primary",
    "fallback",
    "decision_metrics",
    "evidence_status",
    "evidence_ids",
)
VALID_STATUSES = {"heuristic", "benchmarked"}
# A preference that is not backed by a registered benchmark must not be worded
# as a measured ranking.
UNEVIDENCED_SUPERLATIVE = re.compile(r"\b(best|fastest|optimal|superior)\b", re.IGNORECASE)
SUPERLATIVE_FIELDS = ("conditions", "primary", "fallback")


class CatalogIssue(ValueError):
    def __init__(self, message, line=None):
        super().__init__(message)
        self.line=line


def parse_catalog(text: str) -> list[dict[str, str]]:
    lines = text.splitlines()
    header_index = next(
        (i for i, line in enumerate(lines) if line.strip().startswith("| route_id |")),
        None,
    )
    if header_index is None or header_index + 1 >= len(lines):
        raise CatalogIssue("routing catalog table is missing")

    headers = [cell.strip() for cell in lines[header_index].strip().strip("|").split("|")]
    if tuple(headers) != REQUIRED_COLUMNS:
        raise CatalogIssue(f"routing catalog columns must be: {', '.join(REQUIRED_COLUMNS)}")

    rows: list[dict[str, str]] = []
    for line_no, line in enumerate(lines[header_index + 2 :], header_index + 3):
        if not line.lstrip().startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != len(headers):
            raise CatalogIssue(f"catalog row has {len(cells)} cells instead of {len(headers)}: {line}", line_no)
        rows.append(dict(zip(headers, cells)))
    if not rows:
        raise CatalogIssue("routing catalog has no route records")
    return rows


def evidence_ids(text: str) -> set[str]:
    return set(re.findall(r"(?m)^- `evidence_id`:\s*`([^`]+)`\s*$", text))


def validate(routing_text: str, evidence_text: str, *, structured: bool = False) -> list:
    errors: list[str] = []
    try:
        rows = parse_catalog(routing_text)
    except ValueError as exc:
        return [{"code": "catalog_structure_invalid", "line": getattr(exc, "line", None)}] if structured else [str(exc)]

    known_evidence = evidence_ids(evidence_text)
    seen: set[str] = set()
    for row_index, row in enumerate(rows, 1):
        def add(code, message, field=None):
            errors.append({"code": code, "row": row_index, **({"field": field} if field else {})} if structured else message)
        route_id = row["route_id"]
        if not route_id:
            add("route_id_missing", "route_id must not be empty")
            continue
        if route_id in seen:
            add("route_id_duplicate", f"duplicate route_id: {route_id}")
        seen.add(route_id)

        for field in REQUIRED_COLUMNS[1:6]:
            if not row[field]:
                add("required_field_missing", f"{route_id}: {field} must not be empty", field)

        status = row["evidence_status"]
        ids = [item.strip() for item in row["evidence_ids"].split(",") if item.strip()]
        if status not in VALID_STATUSES:
            add("evidence_status_invalid", f"{route_id}: invalid evidence_status {status!r}")
        elif status == "heuristic" and ids != ["none"]:
            add("heuristic_evidence_invalid", f"{route_id}: heuristic routes must use evidence_ids 'none'")
        if status == "heuristic":
            for field in SUPERLATIVE_FIELDS:
                if UNEVIDENCED_SUPERLATIVE.search(row[field]):
                    add("unevidenced_superlative", f"{route_id}: heuristic {field} must not claim a best or fastest choice", field)
        elif status == "benchmarked":
            if not ids or ids == ["none"]:
                add("benchmark_evidence_missing", f"{route_id}: benchmarked route must cite evidence_ids")
            for item in ids:
                if item not in known_evidence:
                    add("evidence_id_unknown", f"{route_id}: unknown evidence_id {item}")
    return errors


def _main() -> int:
    from computation_output import SafeParser, add_output_arguments, configure_output, emit_result as emit_public
    parser = SafeParser()
    add_output_arguments(parser)
    parser.add_argument("--routing-file", type=Path, required=True)
    parser.add_argument("--evidence-file", type=Path, required=True)
    args = parser.parse_args()
    configure_output(args)

    errors = validate(
        args.routing_file.read_text(encoding="utf-8"),
        args.evidence_file.read_text(encoding="utf-8"),
        structured=True,
    )
    emit_public({"ok": not errors, "code": "routing_policy_invalid" if errors else "routing_policy_valid", "error_count": len(errors), "details": errors[:20], "details_complete": len(errors)<=20, "raw_diagnostics_returned": False})
    return 1 if errors else 0


def main() -> int:
    from computation_output import public_main
    return public_main(_main)()


if __name__ == "__main__":
    raise SystemExit(main())
