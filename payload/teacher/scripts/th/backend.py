"""The model boundary.

Nothing in this package imports a vendor SDK. This Skill talks to whatever
satisfies `StructuredBackend`, which is injected at construction. Two
implementations ship here:

  * `ScriptedBackend`  - a deterministic fake used by the entire test suite, so
                         every gate can be exercised without a model;
  * `SubprocessBackend` - a JSONL adapter that spawns a child process. The
                         protocol is line-delimited JSON on stdin/stdout and
                         names no vendor.

The contract that makes the rest of the design possible: a backend returns a
**closed structure**, never prose. `validate_draft` rejects any key outside
`DRAFT_KEYS`, any enum value outside the declared vocabulary, and any quote that
is not byte-identical to text the caller already supplied. Free model text
therefore has no path into the renderer.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any, Protocol, Sequence, runtime_checkable

from . import constants as C
from .model import ModelError, validate_enum

# --------------------------------------------------------------------------
# Closed draft vocabulary
# --------------------------------------------------------------------------
DRAFT_KEYS = (
    "decision",
    "scope",
    "route_progress",
    "certainty",
    "focus_anchor_id",
    "focus_quote",
    "blocking_code",
    "graph_patch",
    "requested_operation",
)

DECISIONS = ("proceed", "blocked", "needs_input")
SCOPES = ("objective", "method", "evidence", "route", "off_topic", "safety")
ROUTE_PROGRESS = ("progressed", "stalled", "unclear", "not_applicable")
CERTAINTY = ("high", "medium", "low")

BLOCKING_CODES = (
    "insufficient_context",
    "objective_conflict",
    "resource_unavailable",
    "reproduction_blocked",
    "evidence_unavailable",
    "out_of_scope",
)

PATCH_KEYS = ("nodes", "edges")
PATCH_NODE_KEYS = ("kind", "label_code", "status")
PATCH_EDGE_KEYS = ("src", "relation", "dst")

MAX_QUOTE_CHARS = 600
MAX_NODES_PER_PATCH = 12
MAX_EDGES_PER_PATCH = 24

EMPTY_PATCH = {"nodes": [], "edges": []}


def empty_patch() -> dict[str, Any]:
    return {"nodes": [], "edges": []}


class BackendError(RuntimeError):
    """The backend failed. This Skill never inspects the failure text."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class BackendUnavailable(BackendError):
    pass


class BackendTimeout(BackendError):
    pass


# --------------------------------------------------------------------------
# Draft validation
# --------------------------------------------------------------------------
def validate_draft(draft: Any, *, supplied_text: str = "", known_anchor_ids: set[str] | None = None) -> dict[str, Any]:
    """Reject anything the renderer could not safely consume.

    `supplied_text` is the caller's own text for this turn. `focus_quote` must
    appear in it verbatim, so a backend cannot introduce new material by
    labelling it a quote.
    """

    if not isinstance(draft, dict):
        raise ModelError(f"{C.RISK_SCHEMA}: draft must be an object")
    unknown = set(draft) - set(DRAFT_KEYS)
    if unknown:
        raise ModelError(f"{C.RISK_SCHEMA}: draft carries undeclared keys: {sorted(unknown)}")
    missing = [key for key in DRAFT_KEYS if key not in draft]
    if missing:
        raise ModelError(f"{C.RISK_SCHEMA}: draft missing keys: {sorted(missing)}")

    validate_enum("decision", draft["decision"], DECISIONS)
    validate_enum("scope", draft["scope"], SCOPES)
    validate_enum("route_progress", draft["route_progress"], ROUTE_PROGRESS)
    validate_enum("certainty", draft["certainty"], CERTAINTY)

    blocking = draft["blocking_code"]
    if blocking is not None:
        validate_enum("blocking_code", blocking, BLOCKING_CODES)

    anchor = draft["focus_anchor_id"]
    if anchor is not None:
        if not isinstance(anchor, str) or not anchor:
            raise ModelError(f"{C.RISK_SCHEMA}: focus_anchor_id must be a string or null")
        if known_anchor_ids is not None and anchor not in known_anchor_ids:
            raise ModelError(f"{C.RISK_UNKNOWN_ANCHOR}: {anchor}")

    quote = draft["focus_quote"]
    if quote is not None:
        if not isinstance(quote, str):
            raise ModelError(f"{C.RISK_SCHEMA}: focus_quote must be a string or null")
        if len(quote) > MAX_QUOTE_CHARS:
            raise ModelError(f"{C.RISK_SCHEMA}: focus_quote exceeds {MAX_QUOTE_CHARS} characters")
        if quote and supplied_text and quote not in supplied_text:
            raise ModelError(f"{C.RISK_SCHEMA}: focus_quote is not verbatim caller text")

    validate_patch(draft["graph_patch"])

    # A model cannot act; it can only ask. The request is shape-checked here and
    # scope-checked by the broker at execution time, so an out-of-scope request
    # is refused by the party that owns the roots, not by the party that wants
    # to reach them.
    from .broker import OperationRefused, validate_request

    requested = draft["requested_operation"]
    if requested is not None:
        try:
            validate_request(requested)
        except OperationRefused as exc:
            raise ModelError(f"{C.RISK_SCHEMA}: requested_operation rejected: {exc.code}") from exc
    return draft


def validate_patch(patch: Any) -> dict[str, Any]:
    if not isinstance(patch, dict):
        raise ModelError(f"{C.RISK_SCHEMA}: graph_patch must be an object")
    unknown = set(patch) - set(PATCH_KEYS)
    if unknown:
        raise ModelError(f"{C.RISK_SCHEMA}: graph_patch carries undeclared keys: {sorted(unknown)}")
    nodes = patch.get("nodes", [])
    edges = patch.get("edges", [])
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise ModelError(f"{C.RISK_SCHEMA}: graph_patch nodes and edges must be lists")
    if len(nodes) > MAX_NODES_PER_PATCH:
        raise ModelError(f"{C.RISK_SCHEMA}: graph_patch exceeds {MAX_NODES_PER_PATCH} nodes")
    if len(edges) > MAX_EDGES_PER_PATCH:
        raise ModelError(f"{C.RISK_SCHEMA}: graph_patch exceeds {MAX_EDGES_PER_PATCH} edges")
    for node in nodes:
        if not isinstance(node, dict):
            raise ModelError(f"{C.RISK_SCHEMA}: graph_patch node must be an object")
        unknown_node = set(node) - set(PATCH_NODE_KEYS)
        if unknown_node:
            raise ModelError(f"{C.RISK_SCHEMA}: graph_patch node keys not allowed: {sorted(unknown_node)}")
        missing = [key for key in PATCH_NODE_KEYS if not node.get(key)]
        if missing:
            raise ModelError(f"{C.RISK_SCHEMA}: graph_patch node missing: {missing}")
        validate_enum("node kind", node["kind"], C.NODE_KINDS)
        validate_enum("node status", node["status"], C.NODE_STATUSES)
        validate_label_code(node["label_code"])
    for edge in edges:
        if not isinstance(edge, dict):
            raise ModelError(f"{C.RISK_SCHEMA}: graph_patch edge must be an object")
        unknown_edge = set(edge) - set(PATCH_EDGE_KEYS)
        if unknown_edge:
            raise ModelError(f"{C.RISK_SCHEMA}: graph_patch edge keys not allowed: {sorted(unknown_edge)}")
        missing = [key for key in PATCH_EDGE_KEYS if not edge.get(key)]
        if missing:
            raise ModelError(f"{C.RISK_SCHEMA}: graph_patch edge missing: {missing}")
        validate_enum("edge relation", edge["relation"], C.RELATIONS)
        # Endpoints are label codes, not internal identifiers. A backend never
        # sees or supplies a store id, so it cannot name an object this Skill
        # did not create, and the renderer can only print a validated token.
        validate_label_code(edge["src"])
        validate_label_code(edge["dst"])
    return patch


LABEL_CODE_PREFIXES = (
    "route.", "step.", "bottleneck.", "obstacle.", "checkpoint.",
    "claim.", "result.", "environment.", "axis.",
)


def validate_label_code(label_code: Any) -> str:
    """A label code is a dotted token, not a sentence.

    Restricting the shape keeps model-authored labels out of rendered prose:
    the renderer looks the code up in a fixed table instead of printing it.
    """

    if not isinstance(label_code, str) or not label_code:
        raise ModelError(f"{C.RISK_SCHEMA}: label_code must be a non-empty string")
    if len(label_code) > 64:
        raise ModelError(f"{C.RISK_SCHEMA}: label_code exceeds 64 characters")
    if not all(part and part.isascii() and (part.replace("_", "").replace("-", "").isalnum()) for part in label_code.split(".")):
        raise ModelError(f"{C.RISK_SCHEMA}: label_code must be dotted ascii tokens")
    if not any(label_code.startswith(prefix) for prefix in LABEL_CODE_PREFIXES):
        raise ModelError(f"{C.RISK_SCHEMA}: label_code must start with one of {list(LABEL_CODE_PREFIXES)}")
    return label_code


# --------------------------------------------------------------------------
# Guard verdict
# --------------------------------------------------------------------------
GUARD_KEYS = ("allow", "risk_codes")


def validate_guard_verdict(verdict: Any) -> dict[str, Any]:
    if not isinstance(verdict, dict):
        raise ModelError(f"{C.RISK_SCHEMA}: guard verdict must be an object")
    unknown = set(verdict) - set(GUARD_KEYS)
    if unknown:
        raise ModelError(f"{C.RISK_SCHEMA}: guard verdict carries undeclared keys: {sorted(unknown)}")
    if "allow" not in verdict or not isinstance(verdict["allow"], bool):
        raise ModelError(f"{C.RISK_SCHEMA}: guard verdict needs a boolean allow")
    codes = verdict.get("risk_codes", [])
    if not isinstance(codes, list):
        raise ModelError(f"{C.RISK_SCHEMA}: risk_codes must be a list")
    for code in codes:
        validate_enum("risk code", code, C.RISK_CODES)
    if verdict["allow"] and codes:
        raise ModelError(f"{C.RISK_SCHEMA}: an allowing verdict must not carry risk codes")
    if not verdict["allow"] and not codes:
        raise ModelError(f"{C.RISK_SCHEMA}: a denying verdict must name at least one risk code")
    return verdict


# --------------------------------------------------------------------------
# Completion review
# --------------------------------------------------------------------------
COMPLETION_REVIEW_KEYS = ("sufficient", "risk_codes")


def validate_completion_review(review: Any) -> dict[str, Any]:
    """A consulted reviewer's opinion. Advisory in one direction only.

    `sufficient: True` grants nothing: it is recorded and never consulted when
    the completion status is computed. A guard's denial, by contrast, is a veto.
    """

    if not isinstance(review, dict):
        raise ModelError(f"{C.RISK_SCHEMA}: completion review must be an object")
    unknown = set(review) - set(COMPLETION_REVIEW_KEYS)
    if unknown:
        raise ModelError(f"{C.RISK_SCHEMA}: completion review carries undeclared keys: {sorted(unknown)}")
    if not isinstance(review.get("sufficient"), bool):
        raise ModelError(f"{C.RISK_SCHEMA}: sufficient must be a boolean")
    codes = review.get("risk_codes", [])
    if not isinstance(codes, list):
        raise ModelError(f"{C.RISK_SCHEMA}: risk_codes must be a list")
    for code in codes:
        if code not in C.RISK_CODES:
            raise ModelError(f"{C.RISK_SCHEMA}: unknown risk code: {code!r}")
    return review


# --------------------------------------------------------------------------
# The boundary
# --------------------------------------------------------------------------
@runtime_checkable
class StructuredBackend(Protocol):
    """Every method here answers a question. None of them acts.

    There is deliberately no method that lets a model advance a stage, open a
    window, promote a claim or pick the next step: those are deterministic
    decisions this package makes. The model is consulted, never deferred to.
    """

    def review_turn(self, context_packet: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]: ...

    def guard_turn(self, context_packet: dict[str, Any], candidate: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]: ...

    def review_intake(self, intake_packet: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]: ...

    def guard_intake(self, intake_packet: dict[str, Any], candidate: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]: ...

    def review_completion(self, completion_packet: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]: ...

    def guard_completion(self, completion_packet: dict[str, Any], candidate: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]: ...

    # Teacher: need discovery, investigation planning, answering. Each still
    # answers a question; this Skill decides what happens with the answer.
    def frame_request(self, frame_packet: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]: ...

    def plan_step(self, plan_packet: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]: ...

    def compose_answer(self, answer_packet: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]: ...

    def guard_answer(self, answer_packet: dict[str, Any], candidate: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]: ...

    def review_claim_independent(self, claim_packet: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]: ...

    def draft_objective(self, objective_packet: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]: ...







    def close(self) -> None: ...


# --------------------------------------------------------------------------
# Scripted backend (tests)
# --------------------------------------------------------------------------
def make_draft(**overrides: Any) -> dict[str, Any]:
    draft = {
        "decision": "proceed",
        "scope": "objective",
        "route_progress": "progressed",
        "certainty": "medium",
        "focus_anchor_id": None,
        "focus_quote": None,
        "blocking_code": None,
        "graph_patch": empty_patch(),
        "requested_operation": None,
    }
    draft.update(overrides)
    return draft


SCRIPTED_QUEUES = (
    "frames", "plans", "answers", "answer_verdicts", "claim_reviews", "objectives",
)


class ScriptedBackend:
    """Deterministic backend. Every queue entry is a draft, a verdict, or an exception."""

    def __init__(
        self,
        *,
        drafts: Sequence[Any] | None = None,
        verdicts: Sequence[Any] | None = None,
        intakes: Sequence[Any] | None = None,
        intake_verdicts: Sequence[Any] | None = None,
        completion: Sequence[Any] | None = None,
        completion_verdicts: Sequence[Any] | None = None,
        **queues: Sequence[Any],
    ) -> None:
        self._drafts = list(drafts or [])
        self._verdicts = list(verdicts or [])
        self._intakes = list(intakes or [])
        self._intake_verdicts = list(intake_verdicts or [])
        self._completion = list(completion or [])
        self._completion_verdicts = list(completion_verdicts or [])
        unknown = set(queues) - set(SCRIPTED_QUEUES)
        if unknown:
            raise ValueError(f"unknown scripted queues: {sorted(unknown)}")
        self._queues = {name: list(queues.get(name) or []) for name in SCRIPTED_QUEUES}
        self.calls: list[str] = []
        self.packets: list[tuple[str, dict[str, Any]]] = []
        self.closed = False

    def _take(self, queue: list[Any], name: str) -> Any:
        self.calls.append(name)
        if not queue:
            raise BackendUnavailable(f"{name}_queue_empty")
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def review_turn(self, context_packet: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        return self._take(self._drafts, "review_turn")

    def guard_turn(self, context_packet: dict[str, Any], candidate: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        return self._take(self._verdicts, "guard_turn")

    def review_intake(self, intake_packet: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        return self._take(self._intakes, "review_intake")

    def guard_intake(self, intake_packet: dict[str, Any], candidate: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        return self._take(self._intake_verdicts, "guard_intake")

    def review_completion(self, completion_packet: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        return self._take(self._completion, "review_completion")

    def guard_completion(self, completion_packet: dict[str, Any], candidate: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        return self._take(self._completion_verdicts, "guard_completion")

    def _queued(self, queue: str, method: str, packet: dict[str, Any]) -> dict[str, Any]:
        self.packets.append((method, packet))
        return self._take(self._queues[queue], method)

    def frame_request(self, frame_packet: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        return self._queued("frames", "frame_request", frame_packet)

    def plan_step(self, plan_packet: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        return self._queued("plans", "plan_step", plan_packet)

    def compose_answer(self, answer_packet: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        return self._queued("answers", "compose_answer", answer_packet)

    def guard_answer(self, answer_packet: dict[str, Any], candidate: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        return self._queued("answer_verdicts", "guard_answer", answer_packet)

    def review_claim_independent(self, claim_packet: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        return self._queued("claim_reviews", "review_claim_independent", claim_packet)

    def draft_objective(self, objective_packet: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        return self._queued("objectives", "draft_objective", objective_packet)







    def close(self) -> None:
        self.closed = True


# --------------------------------------------------------------------------
# Subprocess backend
# --------------------------------------------------------------------------
class SubprocessBackend:
    """Line-delimited JSON over a child process. No vendor is named anywhere.

    A request is one JSON object per line; the child answers with one JSON
    object per line. Failure text from the child is discarded: only the risk
    code crosses the boundary, so a crash cannot leak a candidate.
    """

    def __init__(self, command: Sequence[str], *, timeout: float = 30.0, env: dict[str, str] | None = None) -> None:
        if not command:
            raise BackendUnavailable("empty_command")
        self.command = list(command)
        self.timeout = timeout
        self.env = env

    def _call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = json.dumps({"method": method, "payload": payload}, ensure_ascii=False, sort_keys=True)
        # The child process inherits nothing but the environment; the payload
        # travels on stdin, so it never appears in a process listing.
        try:
            completed = subprocess.run(
                self.command,
                input=request + "\n",
                capture_output=True,
                text=True,
                timeout=self.timeout,
                shell=False,
                check=False,
                env=self.env,
            )
        except subprocess.TimeoutExpired as exc:
            raise BackendTimeout(f"{method}_timeout") from exc
        except OSError as exc:
            raise BackendUnavailable(f"{method}_spawn_failed") from exc
        if completed.returncode != 0:
            raise BackendUnavailable(f"{method}_exit_{completed.returncode}")
        line = completed.stdout.strip().splitlines()
        if not line:
            raise BackendUnavailable(f"{method}_empty_response")
        try:
            response = json.loads(line[-1])
        except json.JSONDecodeError as exc:
            raise BackendUnavailable(f"{method}_malformed_response") from exc
        if not isinstance(response, dict) or "result" not in response:
            raise BackendUnavailable(f"{method}_missing_result")
        return response["result"]

    def review_turn(self, context_packet: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        return self._call("review_turn", context_packet)

    def guard_turn(self, context_packet: dict[str, Any], candidate: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        return self._call("guard_turn", {"context_packet": context_packet, "candidate": candidate})

    def review_intake(self, intake_packet: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        return self._call("review_intake", intake_packet)

    def guard_intake(self, intake_packet: dict[str, Any], candidate: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        return self._call("guard_intake", {"intake_packet": intake_packet, "candidate": candidate})

    def review_completion(self, completion_packet: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        return self._call("review_completion", completion_packet)

    def guard_completion(self, completion_packet: dict[str, Any], candidate: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        return self._call("guard_completion", {"completion_packet": completion_packet, "candidate": candidate})

    def frame_request(self, frame_packet: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        return self._call("frame_request", frame_packet)

    def plan_step(self, plan_packet: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        return self._call("plan_step", plan_packet)

    def compose_answer(self, answer_packet: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        return self._call("compose_answer", answer_packet)

    def guard_answer(self, answer_packet: dict[str, Any], candidate: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        return self._call("guard_answer", {"answer_packet": answer_packet, "candidate": candidate})

    def review_claim_independent(self, claim_packet: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        return self._call("review_claim_independent", claim_packet)

    def draft_objective(self, objective_packet: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        return self._call("draft_objective", objective_packet)







    def close(self) -> None:  # pragma: no cover - nothing to release
        return None


class UnavailableBackend:
    """A backend that always fails closed. Used when no model is configured."""

    def _fail(self, method: str) -> dict[str, Any]:
        raise BackendUnavailable(f"{method}_not_configured")

    def review_turn(self, context_packet: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        return self._fail("review_turn")

    def guard_turn(self, context_packet: dict[str, Any], candidate: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        return self._fail("guard_turn")

    def review_intake(self, intake_packet: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        return self._fail("review_intake")

    def guard_intake(self, intake_packet: dict[str, Any], candidate: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        return self._fail("guard_intake")

    def review_completion(self, completion_packet: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        return self._fail("review_completion")

    def guard_completion(self, completion_packet: dict[str, Any], candidate: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        return self._fail("guard_completion")

    def frame_request(self, frame_packet: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        return self._fail("frame_request")

    def plan_step(self, plan_packet: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        return self._fail("plan_step")

    def compose_answer(self, answer_packet: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        return self._fail("compose_answer")

    def guard_answer(self, answer_packet: dict[str, Any], candidate: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        return self._fail("guard_answer")

    def review_claim_independent(self, claim_packet: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        return self._fail("review_claim_independent")

    def draft_objective(self, objective_packet: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        return self._fail("draft_objective")







    def close(self) -> None:
        return None
