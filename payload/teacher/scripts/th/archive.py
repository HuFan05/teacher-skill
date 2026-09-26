"""Assets: automatic upkeep, one decision to archive.

Two kinds of work, handled differently.

**Automatic, no one asked.** After every answer this Skill extracts archive
candidates — claims with checked evidence, failed checks, cited sources, and
declared unknowns — deduplicates them by content, and scores them with a
deterministic rule (evidence basis, grade, verified quote, novelty). Reasoning
without evidence is never a claim candidate: it stays in the answer, visibly
labelled, and does not enter the library. Dependency drift, stale anchors and
the core cognition are recomputed from state, so they need no upkeep either.

**One decision, the requester's.** What enters the formal library is a value
judgement — scarce, trustworthy, worth reusing — so it stays human. This Skill
reduces it to a single closed choice over a ranked proposal
(`verified_only` / `all` / `none`), bound to a plan hash so the decision
applies to exactly the list that was shown.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Mapping, Sequence

from . import constants as C
from .model import digest, new_id, utc_now
from .store import Store

BASIS_WEIGHT = {"executed_check": 1.0, "retrieved_source": 0.8, "user_supplied": 0.5}
GRADE_WEIGHT = {
    C.GRADE_FORMAL: 1.0,
    C.GRADE_CERTIFICATE: 0.95,
    C.GRADE_EXACT_REPRODUCTION: 0.85,
    C.GRADE_BOUNDED_EMPIRICAL: 0.7,
    C.GRADE_NUMERICAL: 0.4,
}
PROPOSAL_LIMIT = 12


class ArchiveError(RuntimeError):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


def _norm(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r"[\s\W_]+", "", folded)


def _content_sha(kind: str, text: str) -> str:
    return digest({"kind": kind, "text": _norm(text)})


def _candidate(ask_id: str, kind: str, text: str, score: float, **extra: Any) -> dict[str, Any]:
    return {
        "schema": "th-candidate/v1",
        "candidate_id": new_id("CAND"),
        "ask_id": ask_id,
        "kind": kind,
        "state": "pending",
        "text": text,
        "score": round(score, 3),
        "content_sha": _content_sha(kind, text),
        "created_at": utc_now(),
        **extra,
    }


def candidates_from_answer(ask: Mapping[str, Any], evidence: Mapping[str, Mapping[str, Any]], store: Store) -> list[dict[str, Any]]:
    """Deterministic extraction. Known content is not proposed twice."""

    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(candidate: dict[str, Any]) -> None:
        if candidate["content_sha"] in seen or store.candidate_by_content(candidate["content_sha"]) is not None:
            return
        seen.add(candidate["content_sha"])
        out.append(candidate)

    answer = ask["answer"]
    for point in answer["points"]:
        basis = point["basis"]
        if basis == "reasoning":
            continue
        score = BASIS_WEIGHT.get(basis, 0.3) * GRADE_WEIGHT.get(point.get("grade") or "", 0.5)
        if point.get("quote"):
            score += 0.1
        add(_candidate(
            ask["ask_id"], "claim", point["text"], min(score, 1.0),
            basis=basis, grade=point.get("grade"), strength=point["strength"],
            cannot_imply=point.get("cannot_imply"), evidence=list(point["evidence"]),
            quote=point.get("quote"), verified=basis in ("executed_check", "retrieved_source"),
        ))
        for ref in point["evidence"]:
            item = evidence.get(ref)
            if item and item["kind"] == "retrieved_source" and item.get("locator"):
                add(_candidate(ask["ask_id"], "source", item["locator"], 0.5, locator=item["locator"], verified=True))
    for ref, item in evidence.items():
        if item["kind"] == "executed_check" and item.get("returncode") not in (0, None):
            add(_candidate(ask["ask_id"], "failure", f"{item['locator']}", 0.6,
                           locator=item["locator"], verified=True))
    for unknown in answer["unknowns"]:
        add(_candidate(ask["ask_id"], "open_question", unknown, 0.3, verified=False))
    return out


# --------------------------------------------------------------------------
# Proposal and the one decision
# --------------------------------------------------------------------------
def propose(store: Store, *, limit: int = PROPOSAL_LIMIT) -> dict[str, Any]:
    pending = store.candidates(state="pending")
    shown = pending[:limit]
    body = [{"candidate_id": item["candidate_id"], "content_sha": item["content_sha"]} for item in shown]
    return {
        "schema": "th-archive-proposal/v1",
        "items": shown,
        "shown": len(shown),
        "pending_total": len(pending),
        "not_shown": max(0, len(pending) - len(shown)),
        "verified_count": sum(1 for item in shown if item.get("verified")),
        "plan_sha256": digest(body),
        "choices": list(C.ARCHIVE_CHOICES),
        "default": "verified_only",
    }


def apply(store: Store, proposal: Mapping[str, Any], *, choice: str, expect_plan_sha256: str) -> dict[str, Any]:
    """Apply the requester's decision to exactly the proposal that was shown."""

    if choice not in C.ARCHIVE_CHOICES:
        raise ArchiveError(C.ERR_INPUT_REJECTED, f"choice must be one of {list(C.ARCHIVE_CHOICES)}")
    current = propose(store, limit=len(proposal.get("items", [])) or PROPOSAL_LIMIT)
    if proposal.get("plan_sha256") != expect_plan_sha256 or current["plan_sha256"] != expect_plan_sha256:
        raise ArchiveError(C.ERR_PLAN_HASH, "the pending list changed since it was shown; show it again")

    items = list(proposal["items"])
    if choice == "none":
        chosen, declined = [], items
    elif choice == "verified_only":
        chosen = [item for item in items if item.get("verified")]
        declined = [item for item in items if not item.get("verified") and item["kind"] != "open_question"]
    else:
        chosen, declined = items, []

    def mutate(connection: Any) -> dict[str, Any]:
        records: list[str] = []
        for item in chosen:
            archived = {**item, "state": "archived", "archived_at": utc_now()}
            store.put_candidate(connection, archived)
            text = item["text"]
            if item.get("cannot_imply"):
                text += f"\n不能推出：{item['cannot_imply']}"
            if item.get("locator") and item["kind"] != "source":
                text += f"\n来源：{item['locator']}"
            store.put_library_text(connection, item_id=item["candidate_id"], kind=item["kind"], text=text)
            records.append(item["candidate_id"])
        for item in declined:
            store.put_candidate(connection, {**item, "state": "declined"})
        return {"action": "library_archived", "records": records, "notes": [choice]}

    before = store.head(C.HEAD_AUTHORITY)
    result = store.transact(
        operation_id=f"ARCHIVE-{expect_plan_sha256[:16]}-{choice}",
        head=C.HEAD_AUTHORITY,
        expected=before,
        request={"plan_sha256": expect_plan_sha256, "choice": choice},
        mutate=mutate,
    )
    return {
        "schema": "th-archive-receipt/v1",
        "choice": choice,
        "archived": len(chosen),
        "declined": len(declined),
        "kept_pending": len(items) - len(chosen) - len(declined),
        "authority_revision": result["revision"],
        "previous_authority_revision": before,
    }


def library_status(store: Store) -> dict[str, Any]:
    counts: dict[str, dict[str, int]] = {}
    for item in store.candidates():
        counts.setdefault(item["state"], {}).setdefault(item["kind"], 0)
        counts[item["state"]][item["kind"]] += 1
    return {"schema": "th-library-status/v1", "counts": counts}
