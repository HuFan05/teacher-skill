from __future__ import annotations

import argparse
from pathlib import Path


REQUIRED = (
    "user explicitly requests external-tool, code-execution, or MCP verification",
    "loading `$cs-ai-computation`",
    "is mandatory",
    "must not be replaced by ad hoc local scripts",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skill-file", required=True)
    args = parser.parse_args()

    text = Path(args.skill_file).read_text(encoding="utf-8")
    missing = [item for item in REQUIRED if item not in text]
    if missing:
        for item in missing:
            print(f"missing_required_policy={item}")
        return 1

    print(f"computation_handoff_policy_ok skill={args.skill_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
