from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from case_schema import ensure_external_case_path, load_cases, validate_case, write_cases


SCRIPT_DIR = Path(__file__).resolve().parent
EVALUATE_SCRIPT = SCRIPT_DIR / "evaluate_paper_search.py"
SKILL_ROOT = SCRIPT_DIR.parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Append or replace a pdf-paper-search regression case from user feedback."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--case-json",
        help="Single JSON object for one case. Prefer --case-file when shell quoting gets awkward.",
    )
    source.add_argument(
        "--case-file",
        help="Path to a UTF-8 JSON file containing one case object.",
    )
    parser.add_argument("--cases", required=True, help="External JSONL benchmark file; never write real feedback cases into the Skill tree.")
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Replace an existing case with the same id instead of failing.",
    )
    parser.add_argument(
        "--evaluate",
        action="store_true",
        help="Run the updated case through the regression harness immediately after updating the case file.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.case_json is not None:
        raw_case = json.loads(args.case_json)
    else:
        raw_case = json.loads(Path(args.case_file).expanduser().read_text(encoding="utf-8-sig"))
    case = validate_case(raw_case)

    cases_path = ensure_external_case_path(Path(args.cases), SKILL_ROOT)
    cases = load_cases(cases_path)

    existing_index = next(
        (index for index, item in enumerate(cases) if item.get("id") == case["id"]),
        None,
    )
    if existing_index is not None:
        if not args.replace:
            raise ValueError(
                f"Case id `{case['id']}` already exists. Pass --replace to overwrite it."
            )
        cases[existing_index] = case
    else:
        cases.append(case)

    write_cases(cases_path, cases)
    print(
        f"{'Replaced' if existing_index is not None else 'Added'} benchmark case "
        f"`{case['id']}` in {cases_path}"
    )
    if args.evaluate:
        completed = subprocess.run(
            [
                sys.executable,
                str(EVALUATE_SCRIPT),
                "--cases",
                str(cases_path),
                "--case-id",
                str(case["id"]),
            ]
        )
        if completed.returncode != 0:
            return completed.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
