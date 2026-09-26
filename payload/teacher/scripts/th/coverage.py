"""Coverage: this Skill's quantifiable metrics.

A Skill cannot judge whether an answer is good. What it can do is require that
the answer *accounts for* a countable set, and then report the numbers. That is
the whole idea here, and it is more useful than a quality score because the
numbers are checkable.

The metrics, all integers or booleans:

  * `required`   how many members the plan says must be accounted for
  * `decided`    how many have a disposition other than pending
  * `pending`    the ones that do not, listed
  * `omitted`    how many candidates the deterministic screening did **not**
                 look at — a count of what this pass did not cover
  * `exact_read_declared`  members whose decision declares which exact
                 revision it read

Two statements accompany the numbers, and they are the reason the numbers can be
trusted:

    software_cannot_authenticate_reading: True
    semantic_completeness_proven: False

This Skill requires a declaration that a record was read; it states plainly that
it cannot verify the reading happened. Anything else would be a false claim of
assurance, and a false claim is worse than a stated gap.

A plan is bound to a snapshot and to its own deterministic recomputation, so a
decision cannot be attached to a trimmed-down plan — `plan_changed` refuses it.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from . import constants as C
from .model import digest

DISPOSITION_PENDING = "pending"
DISPOSITION_ADVANCED = "advanced"
DISPOSITION_ACCEPTED = "accepted"
DISPOSITION_REJECTED = "rejected"
DISPOSITION_UNRELATED = "unrelated"
DISPOSITION_BLOCKED = "blocked"

DISPOSITIONS = (
    DISPOSITION_PENDING,
    DISPOSITION_ADVANCED,
    DISPOSITION_ACCEPTED,
    DISPOSITION_REJECTED,
    DISPOSITION_UNRELATED,
    DISPOSITION_BLOCKED,
)

DECIDED = tuple(item for item in DISPOSITIONS if item != DISPOSITION_PENDING)

MEMBER_KINDS = ("claim", "obligation", "attempt")

SCREENING_LIMIT_DEFAULT = 12
MAX_READ_DECLARATIONS = 100

PLAN_KEYS = (
    "schema",
    "heads",
    "members",
    "pairs",
    "screening",
    "plan_sha256",
)


class CoverageError(RuntimeError):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


# --------------------------------------------------------------------------
# Plan
# --------------------------------------------------------------------------
def _members(store: Any) -> list[dict[str, str]]:
    """The required set, derived — never supplied by a caller."""

    members: list[dict[str, str]] = []
    for node in store.nodes():
        if node["kind"] in (C.NODE_BOTTLENECK, C.NODE_OBSTACLE) and node["status"] == "open":
            members.append({"id": node["node_id"], "kind": "obligation", "label": node["label"]})
    for record in store.records(kind=C.KIND_CLAIM):
        if store.review_state(record["record_id"]) != C.REVIEW_ACCEPTED:
            members.append({"id": record["record_id"], "kind": "claim", "label": record["title"]})
    for attempt in store.attempts():
        if attempt["status"] != C.ATTEMPT_CLOSED:
            members.append({"id": attempt["attempt_id"], "kind": "attempt", "label": attempt["route_id"]})
    return sorted(members, key=lambda item: (item["kind"], item["id"]))


def _pairs(store: Any) -> list[dict[str, str]]:
    """Adjacencies that need a decision: strong relations and claim-to-anchor links."""

    pairs: list[dict[str, str]] = []
    for edge in store.all_edges():
        if edge["relation"] in C.STRONG_RELATIONS:
            pairs.append({"a": edge["src"], "b": edge["dst"], "signal": "strong_relation"})
    for record in store.records(kind=C.KIND_CLAIM):
        claim = record["body"].get("claim", {})
        for anchor_id in claim.get("anchor_ids", []) or []:
            pairs.append({"a": record["record_id"], "b": str(anchor_id), "signal": "claim_anchor"})
    unique: dict[tuple[str, str], dict[str, str]] = {}
    for pair in pairs:
        a, b = sorted((pair["a"], pair["b"]))
        unique.setdefault((a, b), {"a": a, "b": b, "signal": pair["signal"]})
    return [unique[key] for key in sorted(unique)]


def make_plan(store: Any, *, screening_limit: int = SCREENING_LIMIT_DEFAULT) -> dict[str, Any]:
    """Build the plan. Deterministic: the same store always yields the same plan."""

    if not isinstance(screening_limit, int) or not 1 <= screening_limit <= 100:
        raise CoverageError("screening_limit_invalid", "use a limit between 1 and 100")

    members = _members(store)
    pairs = _pairs(store)
    # The screening is deterministic and unfiltered here: every open obligation
    # is in scope. `omitted` therefore counts candidates beyond the declared
    # limit, which is 0 unless a limit was applied to a larger adjacency set.
    adjacency_total = len(pairs)
    omitted = max(0, adjacency_total - screening_limit) if adjacency_total > screening_limit else 0
    body = {
        "schema": "th-coverage-plan/v1",
        "heads": store.heads(),
        "members": members,
        "pairs": pairs if not omitted else pairs,
        "screening": {
            "method": "deterministic_derivation_from_store",
            "screening_limit": screening_limit,
            "adjacency_total": adjacency_total,
            "omitted_candidates": omitted,
            "semantic_completeness_proven": False,
            "software_cannot_authenticate_reading": True,
        },
    }
    return {**body, "plan_sha256": digest(body)}


def validate_plan(store: Any, plan: Mapping[str, Any]) -> dict[str, Any]:
    """Recompute and compare. A trimmed plan is refused, not repaired."""

    missing = [key for key in PLAN_KEYS if key not in plan]
    if missing:
        raise CoverageError("plan_invalid", f"missing keys: {missing}")
    expected = make_plan(store, screening_limit=plan["screening"]["screening_limit"])
    if digest({k: v for k, v in plan.items() if k != "plan_sha256"}) != plan["plan_sha256"]:
        raise CoverageError("plan_changed", "the plan does not match its own hash")
    recorded = {key: plan[key] for key in PLAN_KEYS if key != "plan_sha256"}
    recomputed = {key: expected[key] for key in PLAN_KEYS if key != "plan_sha256"}
    if recorded != recomputed:
        raise CoverageError(
            "plan_changed",
            "the plan does not match a fresh derivation; generate a new plan rather than trimming it",
        )
    return expected


def decisions_template(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Every member starts `pending`. Nothing is decided by default."""

    return {
        "schema": "th-coverage-decisions/v1",
        "plan_sha256": plan["plan_sha256"],
        "members": [
            {"id": member["id"], "disposition": DISPOSITION_PENDING, "read_revisions": [], "reason": ""}
            for member in plan["members"]
        ],
        "pairs": [
            {"a": pair["a"], "b": pair["b"], "disposition": DISPOSITION_PENDING, "reason": ""}
            for pair in plan["pairs"]
        ],
    }


# --------------------------------------------------------------------------
# Coverage
# --------------------------------------------------------------------------
def check(store: Any, plan: Mapping[str, Any], decisions: Mapping[str, Any]) -> dict[str, Any]:
    """Compute coverage and refuse a decision set that is malformed.

    Refusals are about shape and binding, never about meaning. This Skill has no
    opinion on whether a disposition was the right call.
    """

    validate_plan(store, plan)
    if decisions.get("schema") != "th-coverage-decisions/v1":
        raise CoverageError("decision_schema_invalid", "unsupported decisions schema")
    if decisions.get("plan_sha256") != plan["plan_sha256"]:
        raise CoverageError("decision_binding", "decisions must bind the exact unmodified plan")

    member_map = {member["id"]: member for member in plan["members"]}
    decided_members = {item.get("id"): item for item in decisions.get("members", [])}
    unknown_members = set(decided_members) - set(member_map)
    if unknown_members:
        raise CoverageError("decision_unknown_member", f"not in the plan: {sorted(unknown_members)}")

    pending_members: list[str] = []
    read_declared: list[str] = []
    over_declared: list[str] = []
    for member_id in sorted(member_map):
        item = decided_members.get(member_id)
        if item is None:
            pending_members.append(member_id)
            continue
        disposition = item.get("disposition")
        if disposition not in DISPOSITIONS:
            raise CoverageError("disposition_unknown", f"{member_id}: {disposition!r}")
        if disposition == DISPOSITION_PENDING:
            pending_members.append(member_id)
            continue
        revisions = item.get("read_revisions") or []
        if not isinstance(revisions, list) or not all(isinstance(rev, str) and rev for rev in revisions):
            raise CoverageError("exact_read_required", f"{member_id}: read_revisions must be a list of strings")
        if not revisions:
            raise CoverageError(
                "exact_read_required",
                f"{member_id}: an assessed member must declare its exact read; "
                "software cannot authenticate that reading",
            )
        if len(revisions) > MAX_READ_DECLARATIONS:
            over_declared.append(member_id)
        read_declared.append(member_id)
    if over_declared:
        raise CoverageError("exact_read_required", f"too many declared reads: {sorted(over_declared)}")

    pair_keys = {(pair["a"], pair["b"]) for pair in plan["pairs"]}
    decided_pairs = {(item.get("a"), item.get("b")): item for item in decisions.get("pairs", [])}
    unknown_pairs = set(decided_pairs) - pair_keys
    if unknown_pairs:
        raise CoverageError("decision_unknown_pair", f"not in the plan: {sorted(unknown_pairs)[:3]}")
    pending_pairs: list[list[str]] = []
    for a, b in sorted(pair_keys):
        item = decided_pairs.get((a, b))
        if item is None:
            pending_pairs.append([a, b])
            continue
        disposition = item.get("disposition")
        if disposition not in DISPOSITIONS:
            raise CoverageError("disposition_unknown", f"{a}/{b}: {disposition!r}")
        if disposition == DISPOSITION_PENDING:
            pending_pairs.append([a, b])

    member_complete = not pending_members
    pair_complete = not pending_pairs
    return {
        "schema": "th-coverage/v1",
        "plan_sha256": plan["plan_sha256"],
        "member_coverage": {
            "required": len(member_map),
            "decided": len(member_map) - len(pending_members),
            "pending": pending_members,
            "complete": member_complete,
        },
        "pair_coverage": {
            "required": len(pair_keys),
            "decided": len(pair_keys) - len(pending_pairs),
            "pending": pending_pairs,
            "complete": pair_complete,
        },
        "exact_read_declared": sorted(read_declared),
        "omitted_candidates": int(plan["screening"].get("omitted_candidates", 0)),
        "complete": member_complete and pair_complete,
        # Two statements that keep the numbers honest.
        "software_cannot_authenticate_reading": True,
        "semantic_completeness_proven": False,
    }


def require_complete(coverage: Mapping[str, Any]) -> None:
    """Raise unless every required member and pair has a disposition."""

    if coverage["member_coverage"]["pending"]:
        raise CoverageError(
            "record_coverage",
            f"{len(coverage['member_coverage']['pending'])} members have no disposition",
        )
    if coverage["pair_coverage"]["pending"]:
        raise CoverageError(
            "pair_coverage",
            f"{len(coverage['pair_coverage']['pending'])} relationships have no disposition",
        )


def summary_line(coverage: Mapping[str, Any]) -> str:
    """One line an operator can read."""

    return (
        f"members {coverage['member_coverage']['decided']}/{coverage['member_coverage']['required']}，"
        f"pairs {coverage['pair_coverage']['decided']}/{coverage['pair_coverage']['required']}，"
        f"遗漏候选 {coverage['omitted_candidates']}"
    )
