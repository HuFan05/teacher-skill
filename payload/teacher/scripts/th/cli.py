"""Command line surface.

The CLI is this Skill's real entry point: an agent drives the system by
running these subcommands, and the subcommands are the only place guards sit on
a write path. There is no flag that skips a gate.

Writes that touch the filesystem require a plan hash. Writes that touch
authority require a passing assessment.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import constants as C
from .assets import (
    AssetError,
    apply_index_plan,
    detect_stale,
    locate_by_content,
    make_index_plan,
    read_packet,
)
from .backend import ScriptedBackend, SubprocessBackend, UnavailableBackend
from .engine import Engine, EngineError, gate_input
from .guard import GuardDenied
from .model import ModelError
from .render import Renderer, render_json
from .store import Store, StoreError


def _emit(payload: object, *, as_json: bool) -> int:
    if as_json:
        print(render_json(payload if isinstance(payload, dict) else {"result": payload}))
    else:
        print(payload if isinstance(payload, str) else render_json({"result": payload}))
    return 0


def _plain_engine(store: Store) -> Engine:
    """An engine with no backend. Enough for the mechanisms that only read state."""

    return Engine(store, backend=None, contract={})


def _engine(args: argparse.Namespace) -> Engine:
    store = Store(args.store)
    store.init()
    if getattr(args, "backend_command", None):
        backend = SubprocessBackend(args.backend_command.split(), timeout=args.timeout)
    else:
        backend = UnavailableBackend()
    contract: dict = {}
    if getattr(args, "contract", None):
        contract = json.loads(Path(args.contract).read_text(encoding="utf-8"))
    return Engine(store, backend=backend, contract=contract, guard_timeout=args.timeout)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="th", description="Teacher Skill control surface")
    parser.add_argument("--store", required=True, help="path to this Skill store (SQLite file)")
    parser.add_argument("--contract", help="path to a JSON contract file")
    parser.add_argument("--backend-command", help="command that speaks the structured JSONL protocol")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--json", action="store_true", help="emit machine-readable output")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="heads, counts, claim effects and the guard audit trail")

    obj = sub.add_parser("open-objective", help="bind the objective commitment (advances authority)")
    obj.add_argument("objective", help="path to a JSON objective file")

    win = sub.add_parser("open-window", help="freeze three routes and open their attempts")
    win.add_argument("routes", help="path to a JSON file containing exactly three routes")
    win.add_argument("--binding", default="genesis", choices=["genesis", "validated_map"])

    turn = sub.add_parser("turn", help="submit one turn through gate, backend, guard and renderer")
    turn.add_argument("text")
    turn.add_argument("--actor", default="worker", choices=list(C.ACTORS))
    turn.add_argument("--purpose", default="research")
    turn.add_argument("--attempt-id")

    cp = sub.add_parser("checkpoint", help="record local progress (advances execution only)")
    cp.add_argument("attempt_id")
    cp.add_argument("--note-code", default="checkpoint.local_progress")
    cp.add_argument("--verified", action="store_true",
                    help="request verification; granted only if every --evidence-id resolves to verified evidence")
    cp.add_argument("--evidence-id", action="append", default=[])

    end = sub.add_parser("close-attempt", help="close an attempt with a declared outcome")
    end.add_argument("attempt_id")
    end.add_argument("--outcome", required=True, choices=list(C.OUTCOMES))

    claim = sub.add_parser("submit-claim", help="submit a claim (execution; not yet authority)")
    claim.add_argument("claim", help="path to a JSON claim file")
    claim.add_argument("--title", default="claim")

    rev = sub.add_parser("review-claim", help="attach a review; self-reviews are recorded but never counted")
    rev.add_argument("record_id")
    rev.add_argument("--decision", choices=list(C.REVIEW_DECISIONS))
    rev.add_argument("--reviewer-kind", default="self", choices=["human", "declared_independent", "self", "foreign"])
    rev.add_argument("--independent", action="store_true",
                     help="let this Skill call an isolated reviewer on the claim and its evidence")

    prm = sub.add_parser("promote", help="move a reviewed claim into authority (guarded)")
    prm.add_argument("record_id")

    assess = sub.add_parser("assess", help="produce a completion assessment (closed values only)")
    assess.add_argument("--record-id")
    assess.add_argument("--consult", action="store_true", help="also ask an independent reviewer (veto only)")

    ib = sub.add_parser("intake-begin", help="open a requirement intake session from a rough brief")
    ib.add_argument("brief")

    sub.add_parser("intake-turn", help="run one Skill-driven intake round")

    iset = sub.add_parser("intake-set", help="fill an intake field directly")
    iset.add_argument("field", choices=list(C.INTAKE_ORDER))
    iset.add_argument("value")

    istd = sub.add_parser("intake-standard", help="set the evidence standard for the intake")
    istd.add_argument("standard", choices=list(C.GRADES))

    sub.add_parser("intake-state", help="show what the intake still needs")

    sub.add_parser("intake-commit", help="close intake and emit the objective (does not bind it)")

    conf = sub.add_parser("confirm", help="terminally confirm a passing, still-current assessment")
    conf.add_argument("assessment_id")

    plan = sub.add_parser("index-plan", help="dry run an index of a root; writes nothing")
    plan.add_argument("root")

    apply_ = sub.add_parser("index-apply", help="apply a planned index; requires --expect-plan-sha256")
    apply_.add_argument("plan", help="path to the plan JSON emitted by index-plan")
    apply_.add_argument("--expect-plan-sha256", required=True)

    stale = sub.add_parser("stale", help="compare stored anchor metadata against a live root")
    stale.add_argument("root")

    loc = sub.add_parser("locate", help="resolve a relocated file by content")
    loc.add_argument("root")
    loc.add_argument("relative_path")

    read = sub.add_parser("read", help="bounded read packet")
    read.add_argument("record_id", nargs="+")
    read.add_argument("--profile", default="small", choices=["metadata", "small", "custom"])
    read.add_argument("--max-chars", type=int)

    sub.add_parser("chain", help="verify the hash chain of both heads")

    # -- the mechanisms the operator drives ------------------------------
    layer = sub.add_parser("reading-layer", help="render a cognition at the densest layer that fits")
    layer.add_argument("cognition", help="path to a JSON cognition file")
    layer.add_argument("--target", type=int, help="target token count")
    layer.add_argument("--ceiling", type=int, help="hard token ceiling")
    layer.add_argument("--layer", choices=[*C.LEVELS, "normal"], help="force one layer")

    sub.add_parser("coverage-plan", help="derive the required set that must be accounted for")

    cov = sub.add_parser("coverage-check", help="compute coverage against a plan and a decision set")
    cov.add_argument("plan")
    cov.add_argument("decisions")

    sub.add_parser("coverage-template", help="emit a decision template where every member is pending")

    sub.add_parser("maintenance", help="report what changed: drift, stale anchors, open obligations")

    maint = sub.add_parser("maintain", help="apply explicit semantic decisions")
    maint.add_argument("decisions", help="path to a JSON list of decisions")

    sub.add_parser("events", help="coalesced event stream, grouped by the asset it touched")

    cls = sub.add_parser("classify", help="decide whether work needs the staged acceptance gate")
    cls.add_argument("metrics", help="path to a JSON metrics file")
    cls.add_argument("--operator-requested", action="store_true")

    gb = sub.add_parser("gate-begin", help="open a staged acceptance gate over a candidate directory")
    gb.add_argument("candidate")

    gi = sub.add_parser("gate-inventory", help="freeze a candidate inventory")
    gi.add_argument("gate_id")

    ga = sub.add_parser("gate-advance", help="apply one round to a gate")
    ga.add_argument("gate_id")
    ga.add_argument("round_number", type=int)
    ga.add_argument("--deterministic", required=True, help="path to a deterministic receipt")
    ga.add_argument("--verifier", help="path to a verifier receipt; omit to record unusable verification")

    sub.add_parser("gates", help="list acceptance gates and their states")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _dispatch(args)
    except (StoreError, AssetError, ModelError, EngineError, GuardDenied) as exc:
        code = getattr(exc, "code", type(exc).__name__)
        payload = {"schema": "th-error/v1", "error": code, "message": str(exc)}
        if getattr(exc, "risk_codes", None):
            payload["risk_codes"] = exc.risk_codes
        print(render_json(payload))
        return 1
    except OSError as exc:
        print(render_json({"schema": "th-error/v1", "error": type(exc).__name__, "message": str(exc)}))
        return 1


def _dispatch(args: argparse.Namespace) -> int:
    store = Store(args.store)
    store.init()

    if args.command == "status":
        backend = UnavailableBackend()
        engine = Engine(store, backend=backend, contract={})
        return _emit(engine.status(), as_json=True)

    if args.command == "chain":
        return _emit({name: store.verify_chain(name) for name in C.HEADS}, as_json=True)

    if args.command == "index-plan":
        return _emit(make_index_plan(store, args.root), as_json=True)

    if args.command == "index-apply":
        plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
        return _emit(
            apply_index_plan(store, plan, expect_plan_sha256=args.expect_plan_sha256), as_json=True
        )

    if args.command == "stale":
        return _emit(detect_stale(store, args.root), as_json=True)

    if args.command == "locate":
        result = locate_by_content(store, args.root, args.relative_path)
        if result is None:
            return _emit({"found": False, "reason": "no anchor matches the bytes at that path"}, as_json=True)
        return _emit({"found": True, **result}, as_json=True)

    if args.command == "read":
        return _emit(
            read_packet(store, record_ids=args.record_id, profile=args.profile, max_chars=args.max_chars),
            as_json=True,
        )

    # -- requirement intake ----------------------------------------------
    if args.command == "intake-begin":
        return _emit(_engine(args).intake_begin(args.brief), as_json=True)

    if args.command == "intake-turn":
        return _emit(_engine(args).intake_turn(), as_json=True)

    if args.command == "intake-set":
        return _emit(_engine(args).intake_set(args.field, args.value), as_json=True)

    if args.command == "intake-standard":
        return _emit(_engine(args).intake_standard(args.standard), as_json=True)

    if args.command == "intake-state":
        return _emit(_engine(args).intake_state(), as_json=True)

    if args.command == "intake-commit":
        return _emit(_engine(args).intake_commit(), as_json=True)

    # -- mechanisms that do not need a backend ----------------------------
    if args.command == "reading-layer":
        from .layers import select_layer

        cognition = json.loads(Path(args.cognition).read_text(encoding="utf-8"))
        kwargs: dict = {}
        if args.target is not None:
            kwargs["target_tokens"] = args.target
        if args.ceiling is not None:
            kwargs["ceiling_tokens"] = args.ceiling
        if args.layer and args.layer in ("normal", "compact", "minimal_safe"):
            kwargs["preferred"] = args.layer
        return _emit(select_layer(cognition, **kwargs), as_json=True)

    if args.command == "coverage-plan":
        return _emit(_plain_engine(store).coverage_plan(), as_json=True)

    if args.command == "coverage-template":
        plan = _plain_engine(store).coverage_plan()
        from .coverage import decisions_template

        return _emit(decisions_template(plan), as_json=True)

    if args.command == "coverage-check":
        from .coverage import check as check_coverage

        plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
        decisions = json.loads(Path(args.decisions).read_text(encoding="utf-8"))
        return _emit(check_coverage(store, plan, decisions), as_json=True)

    if args.command == "maintenance":
        return _emit(_plain_engine(store).maintenance_plan(), as_json=True)

    if args.command == "maintain":
        decisions = json.loads(Path(args.decisions).read_text(encoding="utf-8"))
        return _emit(_plain_engine(store).apply_maintenance(decisions), as_json=True)

    if args.command == "events":
        from .maintenance import coalesce_events

        return _emit(coalesce_events(store), as_json=True)

    if args.command == "classify":
        from .acceptance import classify

        metrics = json.loads(Path(args.metrics).read_text(encoding="utf-8"))
        return _emit(classify(metrics, operator_requested=args.operator_requested), as_json=True)

    if args.command == "gate-begin":
        from .acceptance import AcceptanceGate

        gate = AcceptanceGate(store)
        result = gate.begin(args.candidate)
        return _emit(
            {
                "gate": result["gate"],
                "inventory": {k: v for k, v in result["inventory"].items() if k != "targets"},
                "target_count": result["inventory"]["target_count"],
                "aggregate_sha256": result["inventory"]["aggregate_sha256"],
            },
            as_json=True,
        )

    if args.command == "gate-inventory":
        from .acceptance import AcceptanceGate

        snapshot = AcceptanceGate(store).current_inventory(args.gate_id)
        return _emit(
            {k: v for k, v in snapshot.items() if k != "targets"} | {"targets": snapshot["targets"]},
            as_json=True,
        )

    if args.command == "gate-advance":
        from .acceptance import AcceptanceGate

        gate = AcceptanceGate(store)
        deterministic = json.loads(Path(args.deterministic).read_text(encoding="utf-8"))
        verifier = json.loads(Path(args.verifier).read_text(encoding="utf-8")) if args.verifier else None
        result = gate.advance(
            args.gate_id, round_number=args.round_number, deterministic=deterministic, verifier=verifier
        )
        return _emit(result, as_json=True)

    if args.command == "gates":
        return _emit({"gates": store.gates()}, as_json=True)

    engine = _engine(args)

    if args.command == "open-objective":
        objective = json.loads(Path(args.objective).read_text(encoding="utf-8"))
        return _emit(engine.open_objective(objective), as_json=True)

    if args.command == "open-window":
        routes = json.loads(Path(args.routes).read_text(encoding="utf-8"))
        result = engine.open_window(routes, binding=args.binding)
        if args.json:
            return _emit(result, as_json=True)
        return _emit(result["rendered"], as_json=False)

    if args.command == "turn":
        result = engine.turn(args.text, actor=args.actor, purpose=args.purpose, attempt_id=args.attempt_id)
        if result["status"] != "ok":
            # A failed or refused turn is always a non-zero exit, in either
            # output mode: an agent that only checks the exit status must still
            # see the failure.
            _emit(result, as_json=True)
            return 1
        if args.json:
            return _emit(result, as_json=True)
        return _emit(result["visible"], as_json=False)

    if args.command == "checkpoint":
        return _emit(
            engine.checkpoint(
                args.attempt_id,
                body={"note_code": args.note_code},
                verified=args.verified,
                evidence_ids=args.evidence_id,
            ),
            as_json=True,
        )

    if args.command == "close-attempt":
        return _emit(engine.close_attempt(args.attempt_id, outcome=args.outcome), as_json=True)

    if args.command == "submit-claim":
        claim = json.loads(Path(args.claim).read_text(encoding="utf-8"))
        return _emit(engine.submit_claim(claim, title=args.title), as_json=True)

    if args.command == "review-claim":
        if args.independent:
            return _emit(engine.review_claim_independent(args.record_id), as_json=True)
        if not args.decision:
            return _emit({"schema": "th-error/v1", "error": "decision_required"}, as_json=True)
        return _emit(
            engine.review_claim(args.record_id, decision=args.decision, reviewer_kind=args.reviewer_kind),
            as_json=True,
        )

    if args.command == "promote":
        return _emit(engine.promote(args.record_id), as_json=True)

    if args.command == "assess":
        result = engine.assess_completion(record_id=args.record_id, consult=args.consult)
        if args.json:
            return _emit(result, as_json=True)
        return _emit(result["rendered"], as_json=False)

    if args.command == "confirm":
        return _emit(engine.confirm_completion(args.assessment_id), as_json=True)

    raise AssetError(C.ERR_INPUT_REJECTED, f"unhandled command {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
