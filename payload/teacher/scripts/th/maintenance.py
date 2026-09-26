"""Asset maintenance.

Two kinds of change, handled differently, and the split is the whole design:

    mechanical   a dependency changed, a file's bytes changed, an event arrived
                 -> computed automatically, no one has to remember

    semantic     a claim's meaning, a route's decision, an evidence grade
                 -> only an explicit decision changes it, and this Skill states
                    that the decision is the authority

The failure mode this avoids is a system where everything is manual (so it rots)
or everything is automatic (so a script silently rewrites conclusions). The rule
is: **propagate what can be computed, refuse to infer what cannot.**

`dependency_drift` is the load-bearing one. A claim accepted against a set of
dependencies is only current while those dependencies are still the revisions it
was accepted against. When one of them is revised, the claim's effect becomes
`needs_review` without anyone marking it — and that is a computation, not a
judgement.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping, Sequence

from . import constants as C
from .model import digest

# --------------------------------------------------------------------------
# Explicit maintenance actions. Closed set.
# --------------------------------------------------------------------------
ACTION_MARK_NEEDS_REVIEW = "mark_needs_review"
ACTION_RESTORE_CURRENT = "restore_current"
ACTION_RETIRE = "retire"
ACTION_SET_ASSERTION = "set_assertion"

ACTIONS = (ACTION_MARK_NEEDS_REVIEW, ACTION_RESTORE_CURRENT, ACTION_RETIRE, ACTION_SET_ASSERTION)

LIFECYCLE_NEEDS_REVIEW = "needs_review"
LIFECYCLE_CURRENT = "current"
LIFECYCLE_RETIRED = "retired"

LIFECYCLES = (LIFECYCLE_CURRENT, LIFECYCLE_NEEDS_REVIEW, LIFECYCLE_RETIRED)


class MaintenanceError(RuntimeError):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


# --------------------------------------------------------------------------
# Event coalescing
# --------------------------------------------------------------------------
def coalesce_events(store: Any, *, after_sequence: int | None = None) -> dict[str, Any]:
    """Group events by the asset they touched.

    Repeat events for one asset collapse into one entry. Without this, a busy
    asset produces a queue of identical work items and the maintenance pass
    becomes proportional to noise rather than to change.
    """

    affected: dict[str, dict[str, Any]] = defaultdict(lambda: {"types": set(), "count": 0, "last_sequence": 0})
    seen = 0
    for event in store.events(after_sequence=after_sequence, limit=100000):
        seen += 1
        payload = event.get("payload", {})
        # An event may name one asset directly or list the assets it touched.
        touched: list[str] = []
        for key in ("id", "record_id", "target_id", "anchor_id"):
            value = payload.get(key)
            if value:
                touched.append(str(value))
        for key in ("records", "anchors", "nodes"):
            value = payload.get(key)
            if isinstance(value, (list, tuple)):
                touched.extend(str(item) for item in value if item)
        for asset_id in touched:
            entry = affected[asset_id]
            entry["types"].add(event["type"])
            entry["count"] += 1
            entry["last_sequence"] = max(entry["last_sequence"], event["sequence"])
    return {
        "schema": "th-event-coalescing/v1",
        "events_seen": seen,
        "assets_touched": len(affected),
        "affected": {
            key: {"types": sorted(value["types"]), "count": value["count"], "last_sequence": value["last_sequence"]}
            for key, value in sorted(affected.items())
        },
        "coalesced": True,
    }


# --------------------------------------------------------------------------
# Mechanical impact
# --------------------------------------------------------------------------
def strong_dependencies(store: Any, record_id: str) -> list[str]:
    return [
        edge["dst"]
        for edge in store.edges_from(record_id)
        if edge["relation"] in C.STRONG_RELATIONS
    ]


def dependency_revisions(store: Any, record_id: str) -> dict[str, str]:
    """The revisions a decision about this record would be taken against."""

    revisions: dict[str, str] = {}
    for dependency in sorted(set(strong_dependencies(store, record_id))):
        try:
            revisions[dependency] = store.record_revision(dependency)
        except Exception:
            revisions[dependency] = "missing"
    return revisions


def dependency_drift(store: Any, record_id: str) -> dict[str, Any]:
    """Compare the live dependency revisions with the ones a review recorded.

    Returns `drifted: True` when a dependency moved after the review that
    accepted this record. This is computed, so nothing has to remember to mark
    it, and nothing can forget to.
    """

    current = dependency_revisions(store, record_id)
    accepting = [
        review
        for review in store.reviews_for(record_id)
        if review["decision"] == C.REVIEW_ACCEPTED and review["reviewer_kind"] == "local"
    ]
    if not accepting:
        return {"record_id": record_id, "drifted": False, "reason": "no local accepting review"}

    latest = accepting[-1]
    recorded = latest["body"].get("dependency_revisions")
    if recorded is None:
        # A review taken before drift tracking existed cannot be checked. Say so
        # rather than assuming it is fine.
        return {
            "record_id": record_id,
            "drifted": False,
            "reason": "the accepting review predates dependency tracking; drift is unknown",
            "unknown": True,
        }

    moved = {
        key: {"recorded": recorded.get(key), "current": value}
        for key, value in current.items()
        if recorded.get(key) != value
    }
    added = sorted(set(current) - set(recorded))
    removed = sorted(set(recorded) - set(current))
    return {
        "record_id": record_id,
        "drifted": bool(moved or added),
        "moved": moved,
        "added": added,
        "removed": removed,
        "review_id": latest["review_id"],
    }


def mechanical_impact(store: Any) -> dict[str, Any]:
    """Everything that can be computed without deciding anything.

    Reports, does not write. The caller decides whether to act; the point is that
    the *detection* is automatic and cannot be skipped.
    """

    drifted: list[dict[str, Any]] = []
    for record in store.records(kind=C.KIND_CLAIM):
        drift = dependency_drift(store, record["record_id"])
        if drift.get("drifted"):
            drifted.append(drift)

    stale_anchors = [anchor["anchor_id"] for anchor in store.anchors() if anchor["hash_state"] == "stale"]
    unverified_anchors = [anchor["anchor_id"] for anchor in store.anchors() if anchor["hash_state"] == "unverified"]
    open_obligations = [
        node["node_id"]
        for node in store.nodes()
        if node["kind"] in (C.NODE_BOTTLENECK, C.NODE_OBSTACLE) and node["status"] == "open"
    ]
    unmoved = [attempt["attempt_id"] for attempt in store.attempts() if attempt["status"] != C.ATTEMPT_CLOSED]

    return {
        "schema": "th-maintenance-plan/v1",
        "dependency_drift": drifted,
        "stale_anchors": sorted(stale_anchors),
        "unverified_anchors": sorted(unverified_anchors),
        "open_obligations": sorted(open_obligations),
        "open_attempts": sorted(unmoved),
        "counts": {
            "drifted_claims": len(drifted),
            "stale_anchors": len(stale_anchors),
            "unverified_anchors": len(unverified_anchors),
            "open_obligations": len(open_obligations),
            "open_attempts": len(unmoved),
        },
        "semantic_action": "pending_owner_review",
        "note": "detection is automatic; changing what a claim means is not",
    }


# --------------------------------------------------------------------------
# Explicit decisions
# --------------------------------------------------------------------------
def validate_decisions(decisions: Any) -> list[dict[str, Any]]:
    if not isinstance(decisions, list):
        raise MaintenanceError("decisions_invalid", "decisions must be a list")
    for decision in decisions:
        if not isinstance(decision, dict):
            raise MaintenanceError("decisions_invalid", "each decision must be an object")
        action = decision.get("action")
        if action not in ACTIONS:
            raise MaintenanceError("action_unknown", str(action))
        if not decision.get("asset_id"):
            raise MaintenanceError("decisions_invalid", "each decision needs an asset_id")
        if not str(decision.get("reason", "")).strip():
            # A semantic change without a stated reason is how a system drifts.
            raise MaintenanceError("reason_required", f"{decision.get('asset_id')}: a decision must state why")
        if action == ACTION_SET_ASSERTION:
            assertion = decision.get("assertion_state")
            if assertion not in C.ASSERTIONS:
                raise MaintenanceError("assertion_unknown", str(assertion))
    return decisions


def apply_decisions(store: Any, decisions: Sequence[Mapping[str, Any]], *, operation_id: str) -> dict[str, Any]:
    """Apply explicit semantic decisions, receipted as such.

    Each decision changes one record's lifecycle or assertion state. The record's
    *effect* still cannot be set directly: it remains a function of its reviews
    and its dependencies, so an operator cannot promote a claim by editing a
    field.
    """

    validate_decisions(list(decisions))
    applied: list[dict[str, Any]] = []

    def mutate(connection: Any) -> dict[str, Any]:
        for decision in decisions:
            record = store.record(decision["asset_id"])
            action = decision["action"]
            if action == ACTION_MARK_NEEDS_REVIEW:
                lifecycle = LIFECYCLE_NEEDS_REVIEW
            elif action == ACTION_RESTORE_CURRENT:
                lifecycle = LIFECYCLE_CURRENT
            elif action == ACTION_RETIRE:
                lifecycle = LIFECYCLE_RETIRED
            else:
                lifecycle = record["body"].get("lifecycle", LIFECYCLE_CURRENT)
            body = dict(record["body"])
            body["lifecycle"] = lifecycle
            if action == ACTION_SET_ASSERTION:
                body["assertion_state"] = decision["assertion_state"]
            body["maintenance"] = {
                "action": action,
                "reason": decision["reason"],
                "decided_by": decision.get("decided_by", "operator"),
            }
            updated = {
                "schema": C.SCHEMA_RECORD,
                "record_id": record["record_id"],
                "kind": record["kind"],
                "title": record["title"],
                "body": body,
                "created_at": record["created_at"],
            }
            store.put_record(connection, updated)
            applied.append({"asset_id": record["record_id"], "action": action})
        return {"action": "maintenance_applied", "records": [item["asset_id"] for item in applied]}

    result = store.transact(
        operation_id=operation_id,
        head=C.HEAD_EXECUTION,
        expected=store.head(C.HEAD_EXECUTION),
        request={"decisions": list(decisions)},
        mutate=mutate,
    )
    return {
        "schema": "th-maintenance-receipt/v1",
        "applied": applied,
        "authority": "explicit_decision",
        "revision": result["revision"],
        "semantic_authority": "the decision, not the software",
    }


def digest_of_plan(plan: Mapping[str, Any]) -> str:
    return digest({key: value for key, value in plan.items() if key != "plan_sha256"})
