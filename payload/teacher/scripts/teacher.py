#!/usr/bin/env python3
"""Teacher: the command surface of the Skill.

The host agent investigates with its own tools; Teacher applies the checks and
keeps the research library:

    python3 teacher.py frame-check --brief "<原话>" --draft frame.json
    python3 teacher.py check-answer --brief "<原话>" --kind KIND --answer answer.json --evidence evidence.json

Library and state:

    python3 teacher.py index DIR            # plan; then --apply PLAN_SHA256
    python3 teacher.py archive              # show the proposal; then --choice ... --plan SHA
    python3 teacher.py status
    python3 teacher.py engine -- <args>     # objectives, claims, reviews, gates, coverage, maintenance
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from th import config as cfg  # noqa: E402
from th import constants as C  # noqa: E402
from th import archive, retrieval  # noqa: E402
from th.attached import check_answer, frame_check  # noqa: E402
from th.render import Renderer, render_json  # noqa: E402
from th.store import Store  # noqa: E402


def _store(config: dict, override: str | None) -> Store:
    path = Path(override or config["store"]).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    store = Store(path)
    store.init()
    return store


def _print(payload, as_json: bool) -> None:
    if as_json:
        print(render_json(payload if isinstance(payload, dict) else {"result": payload}))
    elif isinstance(payload, dict) and "visible" in payload and payload["visible"]:
        print(payload["visible"])
    else:
        print(render_json(payload if isinstance(payload, dict) else {"result": payload}))


def _status(store: Store) -> dict:
    status = store.status()
    status["library"] = archive.library_status(store)
    status["retrieval"] = retrieval.index_status(store)
    status["asks"] = len(store.asks(limit=100000))
    return status


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="teacher", description="Teacher: research teaching checks for CS/AI")
    parser.add_argument("--store", help="project store (SQLite); defaults to the configured store")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    sub = parser.add_subparsers(dest="command", required=True)

    setup = sub.add_parser("setup", help="write the receiver-local configuration")
    setup.add_argument("--read-root", action="append", default=None, help="a directory the Skill may read (repeatable)")
    setup.add_argument("--network", choices=("on", "off"))
    setup.add_argument("--show", action="store_true")

    fc = sub.add_parser("frame-check", help="apply the need-discovery rule to an agent's framing")
    fc.add_argument("--brief", required=True)
    fc.add_argument("--draft", required=True, help="JSON file with the frame draft")
    ca = sub.add_parser("check-answer", help="run the answer gate on an agent's answer")
    ca.add_argument("--brief", required=True)
    ca.add_argument("--kind", required=True, choices=C.REQUEST_KINDS)
    ca.add_argument("--answer", required=True, help="JSON file with the answer draft")
    ca.add_argument("--evidence", required=True, help="JSON file: list of {id, kind, locator, text}")
    ca.add_argument("--steps", type=int, default=1)

    index = sub.add_parser("index", help="plan (and with --apply, perform) local indexing")
    index.add_argument("root")
    index.add_argument("--apply", metavar="PLAN_SHA256")
    arc = sub.add_parser("archive", help="show the archive proposal, or apply a choice to it")
    arc.add_argument("--choice", choices=C.ARCHIVE_CHOICES)
    arc.add_argument("--plan", metavar="PLAN_SHA256")
    sub.add_parser("status", help="heads, library, index and asks")
    sub.add_parser("cognition", help="the current core cognition, as the producing assistant receives it")

    eng = sub.add_parser("engine", help="pass the remaining arguments to the research-state CLI")
    eng.add_argument("rest", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = cfg.load()

    if args.command == "setup":
        if args.show:
            print(render_json({"config_path": str(cfg.config_path()), **config}))
            return 0
        if args.read_root is not None:
            config["read_roots"] = sorted({str(Path(root).expanduser().resolve()) for root in args.read_root})
        if args.network:
            config["network"]["enabled"] = args.network == "on"
        path = cfg.save(config)
        print(f"配置已写入 {path}。")
        return 0

    if args.command == "engine":
        from th.cli import main as engine_main

        rest = [item for item in args.rest if item != "--"]
        # Global options of the research-state CLI precede its subcommand.
        leading = [item for item in rest if item == "--json"]
        rest = [item for item in rest if item != "--json"]
        store_args = ["--store", str(Path(args.store or config["store"]).expanduser())]
        return engine_main(store_args + leading + rest)

    store = _store(config, args.store)
    if args.command == "frame-check":
        draft = json.loads(Path(args.draft).read_text(encoding="utf-8"))
        _print(frame_check(store, args.brief, draft), args.json)
        return 0
    if args.command == "check-answer":
        answer = json.loads(Path(args.answer).read_text(encoding="utf-8"))
        evidence = json.loads(Path(args.evidence).read_text(encoding="utf-8"))
        try:
            result = check_answer(store, brief=args.brief, kind=args.kind, answer=answer, evidence=evidence,
                                  roots=config.get("read_roots") or [], network=config.get("network") or {},
                                  steps=args.steps)
        except Exception as exc:  # noqa: BLE001
            codes = sorted(set(getattr(exc, "risk_codes", []) or [C.RISK_SCHEMA]))
            print(render_json({"schema": "th-check-answer/v1", "ok": False, "risk_codes": codes}))
            return 3
        _print(result, args.json)
        return 0
    if args.command == "index":
        plan = retrieval.plan_index(args.root)
        if args.apply:
            _print(retrieval.apply_index(store, plan, expect_plan_sha256=args.apply), True)
        else:
            summary = {key: plan[key] for key in ("root", "files", "pdf_files", "pdf_extractor", "plan_sha256")}
            summary["next"] = f"teacher.py index {plan['root']} --apply {plan['plan_sha256']}"
            _print(summary, True)
        return 0
    if args.command == "archive":
        proposal = archive.propose(store)
        if args.choice:
            if not args.plan:
                print("应用归档需要 --plan（即先前展示的 plan_sha256）。")
                return 2
            receipt = archive.apply(store, proposal, choice=args.choice, expect_plan_sha256=args.plan)
            _print({**receipt, "visible": Renderer().render_archive_receipt(receipt)}, args.json)
        else:
            _print({**proposal, "visible": Renderer().render_archive_proposal(proposal)}, args.json)
        return 0
    if args.command == "status":
        _print(_status(store), True)
        return 0
    if args.command == "cognition":
        from th.cognition import current_cognition

        _print(current_cognition(store), True)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
