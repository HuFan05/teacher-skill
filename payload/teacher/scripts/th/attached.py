"""The two commands a host agent calls, and the rules behind them.

When Teacher is installed as Skills inside an existing agent, that agent does
its own investigation with its own tools. This Skill cannot stop it from
writing prose elsewhere — that is a behavioural constraint, and it is stated
as one. What this Skill *can* do is apply its deterministic rules to what the
agent hands it:

    frame-check    the agent's framing of a request goes through the frame
                   validator and the ask/proceed rule, and receives the fixed
                   clarification text to show
    check-answer   the agent's answer goes through the answer gate; each
                   evidence excerpt that names a local file is re-read from disk
                   and each that names a URL is re-fetched (when the network
                   policy allows) — excerpts this Skill could not confirm are
                   counted as `declared`, not `verified`; the rendered text is
                   what the agent must deliver; the ask and its archive
                   candidates are committed like any other.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from . import constants as C
from . import need as need_module
from .answer import validate_answer
from .archive import candidates_from_answer
from .model import ModelError, digest, new_id, utc_now
from .render import Renderer
from .store import Store

EVIDENCE_KINDS = ("retrieved_source", "executed_check")


def frame_check(store: Store, brief: str, draft: Mapping[str, Any]) -> dict[str, Any]:
    frame = need_module.new_frame(brief)
    need_module.validate_frame_draft(dict(draft), brief=brief)
    decision = need_module.decide(frame, draft)
    updated = need_module.apply_draft(frame, draft, decision)
    renderer = Renderer()
    visible = renderer.render_clarification(updated) if decision == need_module.DECISION_ASK else None
    return {
        "schema": "th-frame-check/v1",
        "decision": decision,
        "ask_requester": decision == need_module.DECISION_ASK,
        "investigate_first": decision == need_module.DECISION_INVESTIGATE_FIRST,
        "proceed_with_reading": 1 if decision == need_module.DECISION_PROCEED else None,
        "visible": visible,
        "frame": updated,
    }


def _verify_excerpt(item: Mapping[str, Any], *, roots: Sequence[str], network: Mapping[str, Any]) -> str:
    """`verified` when this Skill itself found the excerpt at its locator."""

    locator = str(item.get("locator", ""))
    text = str(item.get("text", ""))
    if not text.strip():
        return "empty"
    path_part = locator.split("#", 1)[0]
    if locator.startswith(("http://", "https://")):
        if not network.get("enabled"):
            return "declared"
        from .web import WebRefused, fetch_url

        try:
            fetched = fetch_url(path_part, network, max_chars=2_000_000)
        except WebRefused:
            return "declared"
        haystack = " ".join(fetched["text"].split())
        return "verified" if " ".join(text.split()) in haystack else "mismatch"
    candidate = Path(path_part).expanduser()
    paths = [candidate] if candidate.is_absolute() else [Path(root).expanduser() / path_part for root in roots]
    for path in paths:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if not resolved.is_file():
            continue
        if not any(resolved == Path(root).expanduser().resolve() or Path(root).expanduser().resolve() in resolved.parents
                   for root in roots) and not candidate.is_absolute():
            continue
        if resolved.suffix.casefold() == ".pdf":
            from .retrieval import extract_sections

            sections, _status = extract_sections(resolved)
            body = "\n".join(section for _heading, section in sections)
        else:
            body = resolved.read_text(encoding="utf-8", errors="replace")
        return "verified" if " ".join(text.split()) in " ".join(body.split()) else "mismatch"
    return "declared"


def check_answer(
    store: Store,
    *,
    brief: str,
    kind: str,
    answer: Mapping[str, Any],
    evidence: Sequence[Mapping[str, Any]],
    roots: Sequence[str],
    network: Mapping[str, Any],
    steps: int = 1,
) -> dict[str, Any]:
    if kind not in C.REQUEST_KINDS:
        raise ModelError(f"{C.RISK_SCHEMA}: kind must be one of {list(C.REQUEST_KINDS)}")
    evidence_map: dict[str, dict[str, Any]] = {}
    verification: dict[str, str] = {}
    for index, item in enumerate(evidence, start=1):
        ref = str(item.get("id") or f"E{index}")
        if item.get("kind") not in EVIDENCE_KINDS:
            raise ModelError(f"{C.RISK_SCHEMA}: evidence kind must be one of {list(EVIDENCE_KINDS)}")
        state = _verify_excerpt(item, roots=roots, network=network)
        verification[ref] = state
        if state == "mismatch":
            # An excerpt that is not where it claims to be is not evidence.
            continue
        evidence_map[ref] = {
            "kind": item["kind"],
            "locator": str(item.get("locator", ""))[:300],
            "text": str(item.get("text", "")),
            "returncode": item.get("returncode"),
            "checker": None,
        }
    has_source = any(entry["kind"] == "retrieved_source" for entry in evidence_map.values())
    investigation = {
        "steps": steps,
        "budget": steps,
        "operations": len(evidence),
        "refused": 0,
        "candidates": len(evidence),
        "omitted": 0,
        "budget_exhausted": False,
        "sources_unavailable": kind in C.EVIDENCE_REQUIRED_KINDS and not has_source,
        "minimum_met": has_source or kind not in C.EVIDENCE_REQUIRED_KINDS,
    }
    metrics = validate_answer(dict(answer), kind=kind, evidence=evidence_map, user_text=brief, investigation=investigation)
    metrics["excerpts_verified_by_skill"] = sum(1 for state in verification.values() if state == "verified")
    metrics["excerpts_declared_only"] = sum(1 for state in verification.values() if state == "declared")
    metrics["excerpts_mismatched"] = sum(1 for state in verification.values() if state == "mismatch")

    frame = need_module.new_frame(brief)
    frame.update({"kind": kind, "status": need_module.STATUS_ANSWERED})
    visible = Renderer().render_answer(
        answer, metrics=metrics,
        evidence={ref: {"kind": item["kind"], "locator": item["locator"]} for ref, item in evidence_map.items()},
        frame=frame,
    )
    ask_id = new_id("ASK")
    ids = {ref: f"{ask_id}-{ref}" for ref in evidence_map}
    stored_answer = {**answer, "points": [{**point, "evidence": [ids[ref] for ref in point["evidence"]]} for point in answer["points"]]}
    ask = {"schema": "th-ask/v1", "ask_id": ask_id, "frame_id": frame["frame_id"], "status": "answered",
           "kind": kind, "reading": None, "answer": stored_answer, "metrics": metrics,
           "investigation": investigation, "surface": "attached", "created_at": utc_now()}
    candidates = candidates_from_answer(ask, {ids[ref]: item for ref, item in evidence_map.items()}, store)

    def mutate(connection: Any) -> dict[str, Any]:
        for ref, item in evidence_map.items():
            store.put_evidence(connection, {"evidence_id": ids[ref], "ask_id": ask_id, "kind": item["kind"],
                                            "locator": item["locator"], "text": item["text"],
                                            "sha256": digest(item["text"]), "verified": verification[ref] == "verified"})
        store.put_frame(connection, frame)
        store.put_ask(connection, ask)
        for candidate in candidates:
            store.put_candidate(connection, candidate)
        return {"action": "attached_answer_checked", "notes": [ask_id]}

    store.transact(operation_id=f"ASK-{ask_id}", head=C.HEAD_EXECUTION, expected=store.head(C.HEAD_EXECUTION),
                   request={"ask_id": ask_id, "answer_sha256": digest(stored_answer)}, mutate=mutate)
    return {"schema": "th-check-answer/v1", "ok": True, "ask_id": ask_id, "metrics": metrics,
            "verification": verification, "visible": visible, "candidates": len(candidates)}
