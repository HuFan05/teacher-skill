from __future__ import annotations

import argparse
import ast
import json
from collections import defaultdict
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_TARGETS = [
    SCRIPT_DIR / "paper_search.py",
    SCRIPT_DIR / "direct_pdf_search.py",
    *sorted((SCRIPT_DIR / "pdf_paper_search").glob("*.py")),
]


def duplicate_top_level_defs(path: Path) -> dict[str, list[int]]:
    module = ast.parse(path.read_text(encoding="utf-8"))
    locations: dict[str, list[int]] = defaultdict(list)
    for node in module.body:
        if isinstance(node, ast.FunctionDef):
            locations[node.name].append(node.lineno)
    return {name: lines for name, lines in sorted(locations.items()) if len(lines) > 1}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check for duplicate top-level function definitions.")
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    parser.add_argument("paths", nargs="*", help="Python files to inspect. Defaults to search scripts and package.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    targets = [Path(path).expanduser().resolve() for path in args.paths] if args.paths else DEFAULT_TARGETS
    results = []
    for target in targets:
        duplicates = duplicate_top_level_defs(target)
        results.append({"path": str(target), "duplicates": duplicates})
    has_duplicates = any(item["duplicates"] for item in results)
    if args.json:
        print(json.dumps({"ok": not has_duplicates, "results": results}, ensure_ascii=False, indent=2))
    else:
        for item in results:
            if item["duplicates"]:
                print(f"{item['path']}: duplicate definitions found")
                for name, lines in item["duplicates"].items():
                    print(f"  {name}: {lines}")
            else:
                print(f"{item['path']}: ok")
    return 1 if has_duplicates else 0


if __name__ == "__main__":
    raise SystemExit(main())

