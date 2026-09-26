from __future__ import annotations

from mpk_public_output import SafeParser, public_main

import argparse
import json
from pathlib import Path
import sys

from manage_personal_knowledge.performance import (
    default_output_root,
    run_local_benchmark,
    run_model_benchmark,
)


def configure_streams() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return parsed


def positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = SafeParser(
        description="Profile local knowledge-search scripts or complete host-agent search runs."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    local = subparsers.add_parser("local", help="Measure each read-only search script directly.")
    local.add_argument("--query", required=True)
    local.add_argument("--alias", action="append", default=[])
    local.add_argument(
        "--step",
        action="append",
        choices=("status", "vault", "library", "paper"),
        help="Repeat to select steps. Default: all four steps.",
    )
    local.add_argument("--mode", choices=("serial", "parallel"), default="serial")
    local.add_argument("--warmups", type=positive_int, default=1)
    local.add_argument("--runs", type=positive_int, default=3)
    local.add_argument("--timeout", type=positive_float, default=180.0)
    local.add_argument("--limit", type=int, default=12)
    local.add_argument("--python", default=sys.executable)
    local.add_argument("--manage-kb")
    local.add_argument("--obsidian-script")
    local.add_argument("--keep-output", action="store_true")
    local.add_argument("--output-dir", default=str(default_output_root()))
    local.set_defaults(handler=run_local_benchmark)

    model = subparsers.add_parser("model", help="Measure complete host-agent runs from outside the model process.")
    model.add_argument("--prompt-file", required=True)
    model.add_argument(
        "--agent-command",
        required=True,
        help=(
            "JSON array of the host agent's non-interactive argv. It must contain {model}, "
            "read the prompt on stdin, and write one JSON event per line on stdout."
        ),
    )
    model.add_argument("--model", action="append", required=True)
    model.add_argument("--working-dir", default=str(Path.cwd()))
    model.add_argument("--warmups", type=positive_int, default=0)
    model.add_argument("--runs", type=positive_int, default=3)
    model.add_argument("--timeout", type=positive_float, default=1200.0)
    model.add_argument("--keep-raw-events", action="store_true")
    model.add_argument("--output-dir", default=str(default_output_root()))
    model.set_defaults(handler=run_model_benchmark)
    return parser


@public_main
def main() -> int:
    configure_streams()
    args = build_parser().parse_args()
    if args.command == "local" and args.runs == 0:
        raise SystemExit("local --runs must be at least 1")
    if args.command == "model" and args.runs == 0:
        raise SystemExit("model --runs must be at least 1")
    try:
        summary = args.handler(args)
    except Exception as exc:
        print(
            json.dumps(
                {"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    print(
        json.dumps(
            {
                "ok": True,
                "kind": summary["kind"],
                "run_dir": summary["run_dir"],
                "summary": str(Path(summary["run_dir"]) / "summary.json"),
                "report": str(Path(summary["run_dir"]) / "report.md"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
