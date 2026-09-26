"""Control flow.

The whole design turns on one invariant, stated once here and enforced by the
method shapes rather than by convention:

    A checkpoint, a turn, a review and an attempt close advance **execution**.
    Only a promotion or a terminal completion advances **authority** — and only
    after the guard has passed.

Because authority is unreachable from the execution-only methods, "the model
changed the conclusion by continuing to work" is not a rule anyone has to
remember; it is not expressible. A denied operation writes nothing, so the
previous authority revision is still the live one.
"""

from __future__ import annotations

import uuid
from typing import Any, Sequence

from . import constants as C
from .assets import AssetError, build_context_packet
from . import intake as intake_module
from .backend import (
    BackendError,
    StructuredBackend,
    UnavailableBackend,
    validate_completion_review,
    validate_draft,
    validate_guard_verdict,
)
from .broker import Broker, OperationRefused, validate_request
from .coverage import CoverageError, check as check_coverage, make_plan as make_coverage_plan, require_complete
from .layers import select_layer
from .maintenance import dependency_drift, dependency_revisions, mechanical_impact
from .guard import Guard, GuardDenied
from .model import (
    ModelError,
    assessment_is_current,
    digest,
    make_assessment,
    make_record,
    objective_core,
    scope_is_declared,
    short,
    validate_claim,
    validate_grade_sufficient,
    validate_objective,
    validate_route_portfolio,
)
from .render import INTAKE_ASSUMPTION_PHRASE, RenderError, Renderer
from .store import Store, StoreError

DEFAULT_CONTRACT: dict[str, Any] = {
    "objective": None,
    "delegation": {"tools": [], "capabilities": []},
    "scope": {},
    "open_obligations": [],
}

MAX_INPUT_CHARS = 40000

# One bounded repair. A structural defect is worth exactly one retry: the second
# attempt either fixes it or the failure is real. An unbounded retry loop would
# let a backend search for a lenient path, and a zero-retry policy would fail on
# a fixable mistake. Both extremes are worse than one.
MAX_REPAIRS = 1


class EngineError(RuntimeError):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


# --------------------------------------------------------------------------
# Failure codes
# --------------------------------------------------------------------------
SAFE_CODE_RE = __import__("re").compile(r"\A[a-z0-9_]{1,48}\Z")


def safe_code(code: Any) -> str:
    """Only a token-shaped code may cross the failure boundary.

    A backend is free to build an exception message out of anything, including
    a candidate. The code is therefore screened before it is stored or
    returned; anything that is not a short lowercase token becomes
    `unsafe_code`.
    """

    if isinstance(code, str) and SAFE_CODE_RE.match(code):
        return code
    return "unsafe_code"


# --------------------------------------------------------------------------
# Input gate
# --------------------------------------------------------------------------
CONTROL_CHARS = ("\u0000", "\u001b")


def gate_input(text: Any) -> dict[str, Any]:
    """The deterministic gate every turn passes before a model is consulted.

    It rejects shape, not meaning: emptiness, size, control characters and
    command-looking prefixes. Judging meaning is the guard's job and the
    Skill does not claim to be able to do it in general.
    """

    if not isinstance(text, str) or not text.strip():
        return {"ok": False, "code": C.ERR_INPUT_REJECTED, "reason": "empty"}
    if len(text) > MAX_INPUT_CHARS:
        return {"ok": False, "code": C.ERR_INPUT_REJECTED, "reason": "oversized"}
    if any(char in text for char in CONTROL_CHARS):
        return {"ok": False, "code": C.ERR_INPUT_REJECTED, "reason": "control_character"}
    return {"ok": True, "code": None, "reason": None}


# --------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------
class Engine:
    def __init__(
        self,
        store: Store,
        *,
        backend: StructuredBackend | None = None,
        guard_backend: StructuredBackend | None = None,
        contract: dict[str, Any] | None = None,
        renderer: Renderer | None = None,
        broker: Broker | None = None,
        guard_timeout: float = 20.0,
        use_model_guard: bool = True,
    ) -> None:
        self.store = store
        self.backend: StructuredBackend = backend if backend is not None else UnavailableBackend()
        # The guard is a separate role. `use_model_guard=False` leaves the
        # deterministic layer alone in charge: every hard rule is still checked,
        # and no second model call is spent on a veto the deterministic layer
        # already covers.
        if not use_model_guard:
            self.guard_backend: StructuredBackend | None = None
        else:
            self.guard_backend = guard_backend if guard_backend is not None else backend
        self.contract = dict(DEFAULT_CONTRACT)
        if contract:
            self.contract.update(contract)
        self.renderer = renderer or Renderer()
        # The broker owns every capability. The model holds none: it may only
        # name an operation, and this object decides whether that happens.
        self.broker = broker if broker is not None else Broker.from_contract(self.contract, timeout=guard_timeout)
        self.guard = Guard(store, self.guard_backend, timeout=guard_timeout)
        self.pending_operation_results: list[dict[str, Any]] = []
        self._last_operation_receipt: dict[str, Any] | None = None

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def _op(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex[:16]}"

    def _execution(self) -> str:
        return self.store.head(C.HEAD_EXECUTION)

    def _authority(self) -> str:
        return self.store.head(C.HEAD_AUTHORITY)

    def _refresh_contract(self) -> dict[str, Any]:
        """Open obligations are derived from live state, never typed in."""

        obligations: list[str] = []
        for record in self.store.records(kind=C.KIND_CLAIM):
            state = self.store.review_state(record["record_id"])
            if state != C.REVIEW_ACCEPTED:
                obligations.append(C.ISSUE_NO_VERIFIED_CLAIM)
                break
        for attempt in self.store.attempts():
            if attempt["status"] != C.ATTEMPT_CLOSED:
                obligations.append(f"open_attempt:{attempt['attempt_id']}")
        self.contract["open_obligations"] = obligations
        return self.contract

    # -- objective --------------------------------------------------------
    def open_objective(self, objective: dict[str, Any], *, label: str = "objective") -> dict[str, Any]:
        """Bind the objective. Advances authority, because it fixes what the
        project is about; the binding is a digest of exactly six fields, so a
        later schema or directory change cannot move the goal."""

        validate_objective(objective)
        core = objective_core(objective)
        commitment = digest(core)
        record = make_record(kind=C.KIND_OBJECTIVE, title=label, body={"objective": objective, "commitment": commitment})

        def mutate(connection: Any) -> dict[str, Any]:
            self.store.put_record(connection, record)
            return {"action": "objective_opened", "records": [record["record_id"]]}

        result = self.store.transact(
            operation_id=self._op("objective"),
            head=C.HEAD_AUTHORITY,
            expected=self._authority(),
            request={"objective": core},
            mutate=mutate,
        )
        self.contract["objective"] = core
        return {"commitment": commitment, "record_id": record["record_id"], "revision": result["revision"]}

    # -- window -----------------------------------------------------------
    def open_window(self, routes: Sequence[dict[str, Any]], *, binding: str = "genesis") -> dict[str, Any]:
        """Freeze exactly three mutually distinct routes and open their attempts.

        Atomicity is the whole point: either all three attempts exist or none
        do, implemented as one compare-and-swap on the execution head rather
        than as several writes that are hoped to succeed together.
        """

        validated = validate_route_portfolio(list(routes))
        window_id = self._op("WIN")
        attempts: list[dict[str, Any]] = []
        for route in validated:
            cognition = {
                "objective_commitment": digest(self.contract.get("objective") or {}),
                "route": route,
                "binding": binding,
            }
            attempts.append(
                {
                    "attempt_id": self._op("ATT"),
                    "route_id": route["route_id"],
                    "cognition_sha256": digest(cognition),
                    "cognition": cognition,
                }
            )

        def mutate(connection: Any) -> dict[str, Any]:
            self.store.put_window(
                connection,
                window_id=window_id,
                binding=binding,
                body={"routes": validated, "attempts": [item["attempt_id"] for item in attempts]},
            )
            for item in attempts:
                self.store.put_attempt(
                    connection,
                    attempt_id=item["attempt_id"],
                    window_id=window_id,
                    route_id=item["route_id"],
                    cognition_sha256=item["cognition_sha256"],
                    body=item["cognition"],
                )
            return {"action": "window_opened", "notes": [window_id]}

        result = self.store.transact(
            operation_id=window_id,
            head=C.HEAD_EXECUTION,
            expected=self._execution(),
            request={"window_id": window_id, "binding": binding, "routes": validated},
            mutate=mutate,
        )
        return {
            "window_id": window_id,
            "attempts": [item["attempt_id"] for item in attempts],
            "routes": validated,
            "rendered": self.renderer.render_routes(validated),
            "revision": result["revision"],
        }

    # -- turn -------------------------------------------------------------
    def turn(
        self,
        text: str,
        *,
        actor: str = "worker",
        purpose: str = "research",
        attempt_id: str | None = None,
    ) -> dict[str, Any]:
        """One model turn, end to end.

        Failure is fail-closed: nothing is rendered from the failed candidate,
        and only a receipt carrying a risk code and a digest is persisted.
        """

        gate = gate_input(text)
        if not gate["ok"]:
            return {
                "status": "refused",
                "code": gate["code"],
                "reason": gate["reason"],
                "safe_visible": C.REFUSE_INPUT,
                "revision": self._execution(),
            }

        self._last_operation_receipt = None

        known_anchor_ids = {anchor["anchor_id"] for anchor in self.store.anchors()}
        pending_results = self.pending_operation_results[-5:]
        self.pending_operation_results = []
        repair_note: dict[str, Any] | None = None
        risks: list[str] = []

        for attempt_index in range(MAX_REPAIRS + 1):
            packet = build_context_packet(
                self.store,
                contract=self._refresh_contract(),
                actor=actor,
                purpose=purpose,
                record_ids=self.store.record_ids(),
                max_tokens=4000,
                operation_results=pending_results,
                repair=repair_note,
                turn_text=text,
            )
            try:
                raw = self.backend.review_turn(packet, timeout=20.0)
                draft = validate_draft(raw, supplied_text=text, known_anchor_ids=known_anchor_ids)
                self.guard.review_turn(draft=draft, context_packet=packet)

                # The model can only ask. This Skill decides, executes and
                # receipts. Broker operations are read-only by construction, so
                # running them after the guard and before the render cannot leave
                # a partial change behind if the render then fails.
                operation_outcome = self._run_requested_operation(draft["requested_operation"])

                # Render before committing. The renderer is the only writer of
                # visible text, so if it cannot produce a surface then nothing is
                # written at all.
                rendered = self.renderer.render_turn(
                    draft,
                    supplied_text=text,
                    known_anchor_ids=known_anchor_ids,
                    quote_verified=bool(draft["focus_quote"]),
                    operation_outcome=operation_outcome,
                )
                break
            except (BackendError, ModelError, GuardDenied, RenderError, CoverageError) as exc:
                risks = self._risks_from(exc)
                code = safe_code(getattr(exc, "code", "turn_failed"))
                # One bounded repair. The backend is told only the closed risk
                # codes: enough to fix a structural defect, not enough to learn
                # what a reviewer saw.
                if attempt_index < MAX_REPAIRS and isinstance(exc, (ModelError, GuardDenied)):
                    repair_note = {"attempt": attempt_index + 2, "risk_codes": risks}
                    self._record_repair(code=code, risks=risks, attempt=attempt_index + 2)
                    continue
                self._record_failure(kind="turn", code=code, risks=risks, attempt_id=attempt_id)
                return {
                    "status": "failed",
                    "code": code,
                    "risk_codes": risks,
                    "repairs_used": attempt_index,
                    "safe_visible": self.renderer.render_failure(),
                    "revision": self._execution(),
                }
            except Exception as exc:  # noqa: BLE001 - the boundary must never leak
                # An unexpected failure is still a failure. Only the exception's
                # type name crosses the boundary; its message never does.
                code = safe_code(f"unexpected_{type(exc).__name__.lower()}")
                risks = self._risks_from(exc)
                self._record_failure(kind="turn", code=code, risks=risks, attempt_id=attempt_id)
                return {
                    "status": "failed",
                    "code": code,
                    "risk_codes": risks,
                    "repairs_used": attempt_index,
                    "safe_visible": self.renderer.render_failure(),
                    "revision": self._execution(),
                }

        # A `needs_input` decision is recorded as an escalation, and the receipt
        # states how much investigation preceded it. That makes "asked before
        # looking" visible rather than merely discouraged.
        escalation = self._record_escalation(draft, attempt_index=attempt_index)
        revision = self._commit_turn(draft=draft, attempt_id=attempt_id)
        return {
            "status": "ok",
            "draft": draft,
            "visible": rendered,
            "operation": operation_outcome,
            "escalation": escalation,
            "repairs_used": attempt_index,
            "revision": revision,
            "revision_kind": "execution",
        }

    # -- declared operations ----------------------------------------------
    def _run_requested_operation(self, requested: Any) -> dict[str, Any] | None:
        """Execute a model-requested operation, or refuse it.

        A refusal is an outcome, not an exception: the model asked for something
        it may not have, and that is worth reporting back to it. An unknown
        operation never reaches here, because `validate_draft` rejects it at the
        boundary.
        """

        if requested is None:
            return None
        try:
            request = validate_request(requested)
        except OperationRefused as exc:
            return {"outcome": "refused", "operation": requested.get("operation", "operation_unknown"), "code": exc.code}
        try:
            result = self.broker.execute(request)
        except OperationRefused as exc:
            return {"outcome": "refused", "operation": request.operation, "code": exc.code}
        # Only bounded values are retained, and only for the next packet.
        self.pending_operation_results.append(
            {"operation": request.operation, "value": result.get("value"), "receipt": result.get("receipt")}
        )
        self._last_operation_receipt = result["receipt"]
        return {"outcome": "executed", "operation": request.operation, "receipt_id": result["receipt"]["request_sha256"][:16]}

    def available_operations(self) -> dict[str, Any]:
        """What a model may ask for, and under what limits."""

        return self.broker.available()

    def _commit_turn(self, *, draft: dict[str, Any], attempt_id: str | None) -> str:
        """Persist the patch. Label codes are resolved here, which is why a
        backend never handles a store identifier."""

        patch = draft["graph_patch"]

        def mutate(connection: Any) -> dict[str, Any]:
            labels = self.store.node_ids_by_label()
            nodes: list[str] = []
            for node in patch.get("nodes", []):
                node_id = self.store.put_node(
                    connection,
                    kind=node["kind"],
                    label=node["label_code"],
                    status=node["status"],
                    body={"source": "turn"},
                )
                labels.setdefault(node["label_code"], node_id)
                nodes.append(node_id)
            edges: list[str] = []
            for edge in patch.get("edges", []):
                src = labels.get(edge["src"])
                dst = labels.get(edge["dst"])
                if src is None or dst is None:
                    # Unreachable: the guard refuses unknown endpoints before
                    # this runs. Kept because silently dropping an edge would be
                    # worse than failing.
                    raise StoreError(
                        C.ERR_UNKNOWN_ANCHOR,
                        f"edge endpoint not resolvable: {edge['src']!r} -> {edge['dst']!r}",
                    )
                edges.append(
                    self.store.add_relation(
                        connection,
                        src=src,
                        relation=edge["relation"],
                        dst=dst,
                        reason="turn patch",
                    )
                )
            # A receipt for anything this Skill executed on the model's behalf.
            if self._last_operation_receipt is not None:
                receipt = self._last_operation_receipt
                self.store.put_receipt(
                    connection,
                    receipt_id=self._op("RCT"),
                    kind="operation",
                    target=str(receipt.get("operation")),
                    sha256=str(receipt.get("result_sha256")),
                    body={
                        "operation": receipt.get("operation"),
                        "request_sha256": receipt.get("request_sha256"),
                        "executed_by": receipt.get("executed_by"),
                    },
                )
            return {
                "action": "turn_committed",
                "nodes": nodes,
                "edges": edges,
                "notes": [attempt_id or "no-attempt"],
            }

        result = self.store.transact(
            operation_id=self._op("TURN"),
            head=C.HEAD_EXECUTION,
            expected=self._execution(),
            request={"draft": draft, "attempt_id": attempt_id},
            mutate=mutate,
        )
        return result["revision"]

    def _risks_from(self, exc: BaseException) -> list[str]:
        """Map an exception to closed risk codes without reading its message."""

        declared = list(getattr(exc, "risk_codes", []) or [])
        if declared:
            return [code for code in declared if code in C.RISK_CODES] or [C.RISK_SCHEMA]
        return [C.RISK_SCHEMA]

    def _record_failure(self, *, kind: str, code: str, risks: list[str], attempt_id: str | None) -> None:
        """Persist only what is safe: a code, a digest and closed risk codes.

        A failed turn changed no state, so this is recorded as an observation
        and no head moves.
        """

        self.store.observe(
            event_type=f"{kind}_failed",
            payload={"code": code, "risk_codes": sorted(risks), "attempt_id": attempt_id},
            mutate=lambda connection: self.store.put_receipt(
                connection,
                receipt_id=self._op("RCT"),
                kind=f"{kind}_failure",
                target=attempt_id or "none",
                sha256=digest({"code": code, "risks": sorted(risks)}),
                body={"code": code, "risk_codes": sorted(risks), "attempt_id": attempt_id},
            ),
        )

    # -- requirement intake ------------------------------------------------
    def intake_begin(self, brief: str) -> dict[str, Any]:
        """Open an intake session. Advances execution; intake is not authority."""

        state = intake_module.new_state(brief)

        def mutate(connection: Any) -> dict[str, Any]:
            self.store.put_intake(connection, state)
            return {"action": "intake_opened", "notes": [state["intake_id"]]}

        result = self.store.transact(
            operation_id=self._op("INTAKE"),
            head=C.HEAD_EXECUTION,
            expected=self._execution(),
            request={"brief_sha256": digest(brief)},
            mutate=mutate,
        )
        return {
            "intake_id": state["intake_id"],
            "visible": self.renderer.render_intake(state),
            "completeness": intake_module.completeness(state),
            "revision": result["revision"],
            "revision_kind": "execution",
        }

    def _load_intake(self) -> dict[str, Any]:
        state = self.store.latest_intake()
        if state is None:
            raise EngineError(C.ERR_INTAKE_UNKNOWN, "no intake session has been opened")
        return state

    def intake_state(self) -> dict[str, Any]:
        state = self._load_intake()
        return {
            "intake": state,
            "completeness": intake_module.completeness(state),
            "visible": self.renderer.render_intake(state),
        }

    def intake_turn(self) -> dict[str, Any]:
        """One intake round, driven entirely by this Skill.

        This Skill picks the field, writes the question and decides whether the
        round resolved anything. The model may only point at the requester's own
        words or name a closed value; it cannot choose the field and cannot
        declare the shape ready.
        """

        state = self._load_intake()
        if state["status"] == intake_module.STATUS_COMMITTED:
            raise EngineError(C.ERR_INTAKE_UNKNOWN, "this intake has already been committed")
        gap = intake_module.next_gap(state)
        if gap is None:
            return {
                "status": "complete",
                "intake": state,
                "completeness": intake_module.completeness(state),
                "visible": self.renderer.render_intake(state),
                "revision": self._execution(),
            }
        if int(state.get("round", 0)) >= C.INTAKE_MAX_ROUNDS:
            raise EngineError(
                C.ERR_INTAKE_ROUNDS_EXHAUSTED,
                f"intake allows at most {C.INTAKE_MAX_ROUNDS} rounds; fill a field directly",
            )

        packet = intake_module.packet(state)
        draft: dict[str, Any] | None = None
        for attempt_index in range(MAX_REPAIRS + 1):
            try:
                raw = self.backend.review_intake(packet, timeout=20.0)
                candidate = intake_module.validate_intake_draft(
                    raw, brief=state["brief"], gap=gap, state=state
                )
                if self.guard_backend is not None:
                    verdict = validate_guard_verdict(
                        self.guard_backend.guard_intake(packet, candidate, timeout=20.0)
                    )
                    if not verdict["allow"]:
                        raise GuardDenied(list(verdict["risk_codes"]))
                draft = candidate
                break
            except (BackendError, ModelError, GuardDenied) as exc:
                risks = self._risks_from(exc)
                code = safe_code(getattr(exc, "code", "intake_failed"))
                if attempt_index < MAX_REPAIRS and isinstance(exc, (ModelError, GuardDenied)):
                    self._record_repair(code=code, risks=risks, attempt=attempt_index + 2)
                    continue
                self._record_failure(kind="intake", code=code, risks=risks, attempt_id=None)
                return {
                    "status": "failed",
                    "code": code,
                    "risk_codes": risks,
                    "safe_visible": self.renderer.render_failure(),
                    "revision": self._execution(),
                }
            except Exception as exc:  # noqa: BLE001 - the boundary must never leak
                code = safe_code(f"unexpected_{type(exc).__name__.lower()}")
                risks = self._risks_from(exc)
                self._record_failure(kind="intake", code=code, risks=risks, attempt_id=None)
                return {
                    "status": "failed",
                    "code": code,
                    "risk_codes": risks,
                    "safe_visible": self.renderer.render_failure(),
                    "revision": self._execution(),
                }

        updated = intake_module.apply_draft(state, draft or {})
        outcome = {
            "field": gap,
            "quote": draft.get("quote") if draft else None,
            "declared_absent": bool(draft.get("absent")) if draft else False,
            "assumed_defaults": list(draft.get("assumed_defaults", [])) if draft else [],
            "evidence_standard": draft.get("evidence_standard") if draft else None,
        }
        # Render before committing: the renderer is the only writer, so if it
        # cannot produce a surface nothing is written.
        visible = self.renderer.render_intake(updated, outcome=outcome)

        def mutate(connection: Any) -> dict[str, Any]:
            self.store.put_intake(connection, updated)
            return {"action": "intake_advanced", "notes": [updated["intake_id"], str(updated["round"])]}

        result = self.store.transact(
            operation_id=self._op("INTAKETURN"),
            head=C.HEAD_EXECUTION,
            expected=self._execution(),
            request={"intake_id": updated["intake_id"], "round": updated["round"]},
            mutate=mutate,
        )
        return {
            "status": "ok",
            "intake": updated,
            "completeness": intake_module.completeness(updated),
            "visible": visible,
            "revision": result["revision"],
            "revision_kind": "execution",
        }

    def intake_extend(self, text: str) -> dict[str, Any]:
        """Add to the brief. Intake is a conversation, not a single sentence."""

        state = self._load_intake()
        if not isinstance(text, str) or not text.strip():
            raise EngineError(C.ERR_INPUT_REJECTED, "empty")
        updated = dict(state)
        updated["brief"] = (state.get("brief", "") + "\n" + text.strip()).strip()
        updated["updated_at"] = state.get("updated_at")

        def mutate(connection: Any) -> dict[str, Any]:
            self.store.put_intake(connection, updated)
            return {"action": "intake_brief_extended", "notes": [state["intake_id"]]}

        result = self.store.transact(
            operation_id=self._op("INTAKEEXT"),
            head=C.HEAD_EXECUTION,
            expected=self._execution(),
            request={"intake_id": state["intake_id"]},
            mutate=mutate,
        )
        return {
            "intake": updated,
            "completeness": intake_module.completeness(updated),
            "visible": self.renderer.render_intake(updated),
            "revision": result["revision"],
        }

    def intake_set(self, field: str, value: Any) -> dict[str, Any]:
        """The requester fills a field directly. This is the human path."""

        state = self._load_intake()
        updated = intake_module.set_field(state, field, value)

        def mutate(connection: Any) -> dict[str, Any]:
            self.store.put_intake(connection, updated)
            return {"action": "intake_field_set", "notes": [field]}

        result = self.store.transact(
            operation_id=self._op("INTAKESET"),
            head=C.HEAD_EXECUTION,
            expected=self._execution(),
            request={"field": field},
            mutate=mutate,
        )
        return {
            "intake": updated,
            "completeness": intake_module.completeness(updated),
            "visible": self.renderer.render_intake(updated),
            "revision": result["revision"],
        }

    def intake_standard(self, standard: str) -> dict[str, Any]:
        state = self._load_intake()
        updated = intake_module.set_standard(state, standard)

        def mutate(connection: Any) -> dict[str, Any]:
            self.store.put_intake(connection, updated)
            return {"action": "intake_standard_set", "notes": [standard]}

        result = self.store.transact(
            operation_id=self._op("INTAKESTD"),
            head=C.HEAD_EXECUTION,
            expected=self._execution(),
            request={"evidence_standard": standard},
            mutate=mutate,
        )
        return {
            "intake": updated,
            "completeness": intake_module.completeness(updated),
            "visible": self.renderer.render_intake(updated),
            "revision": result["revision"],
        }

    def intake_commit(self) -> dict[str, Any]:
        """Close intake and produce the objective. It does not bind it.

        Committing is the one place an incomplete shape could slip into a
        project, so completeness is re-checked here rather than trusted from the
        last round.
        """

        state = self._load_intake()
        objective = intake_module.to_objective(state, assumption_phrases=INTAKE_ASSUMPTION_PHRASE)
        validate_objective(objective)
        commitment = digest(objective_core(objective))
        committed = dict(state)
        committed["status"] = intake_module.STATUS_COMMITTED
        committed["objective_commitment"] = commitment
        committed["updated_at"] = state.get("updated_at")

        def mutate(connection: Any) -> dict[str, Any]:
            self.store.put_intake(connection, committed)
            return {"action": "intake_committed", "notes": [state["intake_id"]]}

        result = self.store.transact(
            operation_id=self._op("INTAKECOMMIT"),
            head=C.HEAD_EXECUTION,
            expected=self._execution(),
            request={"intake_id": state["intake_id"], "commitment": commitment},
            mutate=mutate,
        )
        return {
            "objective": objective,
            "commitment": commitment,
            "next": "open-objective",
            "revision": result["revision"],
            "revision_kind": "execution",
        }

    # -- attempts and checkpoints -----------------------------------------
    def checkpoint(
        self,
        attempt_id: str,
        *,
        body: dict[str, Any],
        verified: bool = False,
        evidence_ids: Sequence[str] = (),
    ) -> dict[str, Any]:
        """Local progress. Advances execution only.

        There is no branch here that could alter a claim's review state, which
        is why a checkpoint can never turn a working claim into a verified
        result.
        """

        self.store.attempt(attempt_id)  # raises if unknown
        checkpoint_id = self._op("CP")
        cited = sorted(str(item) for item in evidence_ids)
        # "Verified" is computed, not asserted: every cited id must resolve to
        # evidence this Skill itself verified, or to an accepted claim. A
        # request to mark a checkpoint verified without that is recorded as
        # unverifiable rather than believed.
        resolvable = bool(cited) and all(self._evidence_is_verified(item) for item in cited)
        record_body = {
            "note_code": body.get("note_code", "checkpoint.local_progress"),
            "verified": bool(verified) and resolvable,
            "verification": "verified" if bool(verified) and resolvable else ("unverifiable" if verified else "not_requested"),
            "evidence_ids": cited,
            "blocked_on": body.get("blocked_on"),
        }

        def mutate(connection: Any) -> dict[str, Any]:
            self.store.put_checkpoint(
                connection,
                checkpoint_id=checkpoint_id,
                attempt_id=attempt_id,
                body=record_body,
                revision=self._execution(),
            )
            return {"action": "checkpoint_recorded", "notes": [checkpoint_id]}

        result = self.store.transact(
            operation_id=checkpoint_id,
            head=C.HEAD_EXECUTION,
            expected=self._execution(),
            request={"attempt_id": attempt_id, "body": record_body},
            mutate=mutate,
        )
        return {
            "checkpoint_id": checkpoint_id,
            "verified": record_body["verified"],
            "revision": result["revision"],
            "advanced": "execution",
            "authority_unchanged": self._authority(),
        }

    def _evidence_is_verified(self, evidence_id: str) -> bool:
        try:
            return bool(self.store.evidence_item(evidence_id)["verified"])
        except StoreError:
            pass
        try:
            record = self.store.record(evidence_id)
        except StoreError:
            return False
        return record["kind"] == C.KIND_CLAIM and self.store.review_state(evidence_id) == C.REVIEW_ACCEPTED

    def review_claim_independent(self, record_id: str) -> dict[str, Any]:
        """An isolated reviewer this Skill itself calls, on a packet holding
        only the claim and the evidence excerpts it cites. PASS plus the
        deterministic claim checks records an accepted `independent_model`
        review; FAIL records a rejection; INCONCLUSIVE or an unavailable
        reviewer records nothing — infrastructure failure is not a verdict."""

        record = self.store.record(record_id)
        claim = record["body"].get("claim", {})
        excerpts = []
        for evidence_id in claim.get("evidence_ids", []):
            try:
                item = self.store.evidence_item(evidence_id)
            except StoreError:
                continue
            excerpts.append({"id": evidence_id, "locator": item["locator"], "text": item["text"][:3000]})
        packet = {"schema": "th-claim-review/v1", "claim": claim, "evidence": excerpts,
                  "verdicts": ["PASS", "FAIL", "INCONCLUSIVE"]}
        try:
            raw = self.backend.review_claim_independent(packet, timeout=60.0)
        except BackendError:
            return {"state": "unavailable", "recorded": False}
        if not isinstance(raw, dict) or raw.get("verdict") not in ("PASS", "FAIL", "INCONCLUSIVE"):
            return {"state": "malformed", "recorded": False}
        if raw["verdict"] == "INCONCLUSIVE":
            return {"state": "inconclusive", "recorded": False}
        deterministic = self.guard.deterministic.check_claim(claim) if raw["verdict"] == "PASS" else []
        decision = C.REVIEW_ACCEPTED if raw["verdict"] == "PASS" and not deterministic else C.REVIEW_REJECTED
        result = self.review_claim(record_id, decision=decision, reviewer_kind="independent_model",
                                   findings={"verdict": raw["verdict"], "deterministic_risks": deterministic,
                                             "evidence_excerpts": len(excerpts)})
        return {"state": "recorded", "recorded": True, "verdict": raw["verdict"], **result}

    def close_attempt(self, attempt_id: str, *, outcome: str) -> dict[str, Any]:
        self.store.attempt(attempt_id)

        def mutate(connection: Any) -> dict[str, Any]:
            self.store.close_attempt(connection, attempt_id=attempt_id, outcome=outcome)
            return {"action": "attempt_closed", "notes": [attempt_id, outcome]}

        result = self.store.transact(
            operation_id=self._op("ATTEND"),
            head=C.HEAD_EXECUTION,
            expected=self._execution(),
            request={"attempt_id": attempt_id, "outcome": outcome},
            mutate=mutate,
        )
        return {"attempt_id": attempt_id, "outcome": outcome, "revision": result["revision"]}

    # -- claims -----------------------------------------------------------
    def submit_claim(self, claim: dict[str, Any], *, title: str = "claim") -> dict[str, Any]:
        """Create a claim record. Advances execution; a claim is not authority
        until it is reviewed and promoted."""

        validate_claim(claim)
        self.guard.review_claim(claim)
        record = make_record(kind=C.KIND_CLAIM, title=title or short(claim["statement"]), body={"claim": claim})

        def mutate(connection: Any) -> dict[str, Any]:
            self.store.put_record(connection, record)
            return {"action": "claim_submitted", "records": [record["record_id"]]}

        result = self.store.transact(
            operation_id=self._op("CLAIM"),
            head=C.HEAD_EXECUTION,
            expected=self._execution(),
            request={"claim": claim, "record_id": record["record_id"]},
            mutate=mutate,
        )
        return {"record_id": record["record_id"], "revision": result["revision"], "review_state": self.store.review_state(record["record_id"])}

    def review_claim(
        self,
        record_id: str,
        *,
        decision: str,
        reviewer_kind: str = "local",
        findings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.store.record(record_id)
        review_id = self._op("RV")
        # Record what this decision was taken against. Without it, a later
        # dependency change could not be detected, and "the claim is still
        # current" would be an assumption rather than a computation.
        body = dict(findings or {})
        body.setdefault("target_revision", self.store.record_revision(record_id))
        body.setdefault("dependency_revisions", dependency_revisions(self.store, record_id))

        def mutate(connection: Any) -> dict[str, Any]:
            self.store.put_review(
                connection,
                review_id=review_id,
                target_id=record_id,
                decision=decision,
                reviewer_kind=reviewer_kind,
                body=body,
            )
            return {"action": "claim_reviewed", "records": [record_id]}

        result = self.store.transact(
            operation_id=review_id,
            head=C.HEAD_EXECUTION,
            expected=self._execution(),
            request={"record_id": record_id, "decision": decision, "reviewer_kind": reviewer_kind},
            mutate=mutate,
        )
        return {
            "review_id": review_id,
            "review_state": self.store.review_state(record_id),
            "revision": result["revision"],
        }

    def record_effect(self, record_id: str) -> str:
        """Derived, never assigned.

        A record's effect is a function of its local reviews, its dependency
        closure **and whether its dependencies have moved since it was reviewed**.
        Nothing can set it directly, so no amount of continued work can promote a
        claim on its own — and no one has to remember to demote one whose
        premises changed.
        """

        state = self.store.review_state(record_id)
        if state == C.REVIEW_REJECTED:
            return C.EFFECT_INVALID
        closure = self.store.dependency_closure(record_id)
        if not closure["closed"]:
            return C.EFFECT_NEEDS_REVIEW
        if state == C.REVIEW_ACCEPTED:
            drift = dependency_drift(self.store, record_id)
            if drift.get("drifted"):
                # Automatic demotion. This is the maintenance rule that does not
                # depend on anyone noticing.
                return C.EFFECT_NEEDS_REVIEW
            return C.EFFECT_CURRENT
        if state == C.REVIEW_INCONCLUSIVE:
            return C.EFFECT_NEEDS_REVIEW
        return C.EFFECT_HISTORICAL

    def drift_report(self) -> dict[str, Any]:
        """Every claim whose dependencies moved. Computed, not reported by anyone."""

        return mechanical_impact(self.store)

    # -- promotion: the only path to authority ----------------------------
    def promote(self, record_id: str) -> dict[str, Any]:
        """Move a reviewed claim into authority.

        The guard runs first and outside the transaction. If it denies, this
        method raises and the authority head is untouched — there is no partial
        write to roll back because nothing was written.
        """

        before = self._authority()
        self.guard.review_promotion(record_id)
        record = self.store.record(record_id)
        effect = self.record_effect(record_id)
        claim = record["body"].get("claim", {})

        def mutate(connection: Any) -> dict[str, Any]:
            promoted = make_record(
                kind=C.KIND_REVIEW,
                title=f"promotion:{record_id}",
                body={
                    "target_id": record_id,
                    "effect": effect,
                    "grade": claim.get("grade"),
                    "strength": claim.get("strength"),
                    "cannot_imply": list(claim.get("cannot_imply", [])),
                    "authority_parent": before,
                },
            )
            self.store.put_record(connection, promoted)
            return {"action": "claim_promoted", "records": [record_id, promoted["record_id"]]}

        result = self.store.transact(
            operation_id=self._op("PROMOTE"),
            head=C.HEAD_AUTHORITY,
            expected=before,
            request={"record_id": record_id, "effect": effect},
            mutate=mutate,
        )
        return {
            "record_id": record_id,
            "effect": effect,
            "authority_revision": result["revision"],
            "previous_authority_revision": before,
        }

    # -- completion -------------------------------------------------------
    def assess_completion(
        self,
        *,
        scope: str = "claim",
        record_id: str | None = None,
        coverage: dict[str, Any] | None = None,
        consult: bool = False,
    ) -> dict[str, Any]:
        """Produce an assessment from closed values only.

        The returned object has no prose field, so an assessment has no display
        path before confirmation.

        When a coverage report is supplied, incomplete coverage blocks completion.
        That is how a countable obligation becomes a gate rather than a note in a
        document.

        `consult` asks an independent reviewer. A reviewer can only subtract:
        its `sufficient` opinion is recorded and never read by the status rule,
        and its guard may deny. An unavailable reviewer neither blocks nor
        approves — infrastructure failure is not a completion decision.
        """

        issues = self._completion_issues(record_id=record_id)
        if coverage is not None and not coverage.get("complete", False):
            issues = sorted({*issues, C.ISSUE_COVERAGE_INCOMPLETE})

        review: dict[str, Any] | None = None
        if consult:
            review = self._consult_completion(issues=issues, record_id=record_id)
            if review.get("denied"):
                issues = sorted({*issues, C.ISSUE_REVIEW_DENIED})

        status = self._completion_status(issues=issues, record_id=record_id)
        assessment = make_assessment(
            scope=scope,
            status=status,
            issue_codes=[] if status != C.COMPLETION_NOT_COMPLETE else issues,
            execution_revision=self._execution(),
            authority_revision=self._authority(),
        )
        if review is not None:
            assessment["completion_review"] = {
                "state": review["state"],
                "denied": bool(review.get("denied")),
                # Recorded, and deliberately never read by the status rule.
                "advisory_sufficient": review.get("advisory_sufficient"),
                "risk_codes": sorted(review.get("risk_codes", [])),
            }
        if coverage is not None:
            assessment["coverage"] = {
                "member_coverage": coverage["member_coverage"],
                "pair_coverage": coverage["pair_coverage"],
                "omitted_candidates": coverage["omitted_candidates"],
                "software_cannot_authenticate_reading": coverage["software_cannot_authenticate_reading"],
                "semantic_completeness_proven": coverage["semantic_completeness_proven"],
            }

        # An assessment observes state; it does not change it. Storing it must
        # not advance either head, or the observation would be stale the moment
        # it was recorded.
        self.store.observe(
            event_type="assessment_recorded",
            payload={"assessment_id": assessment["assessment_id"], "status": status},
            mutate=lambda connection: self.store.put_assessment(connection, assessment),
        )
        return {
            "assessment": assessment,
            "rendered": self.renderer.render_completion(assessment),
            "visible": status != C.COMPLETION_NOT_COMPLETE,
        }

    def _consult_completion(self, *, issues: list[str], record_id: str | None) -> dict[str, Any]:
        """Ask an independent reviewer about a closed packet. Never about prose."""

        packet = {
            "schema": "th-completion-packet/v1",
            "issues": sorted(set(issues)),
            "record_id": record_id,
            "counts": {
                "checkpoints": len(self.store.checkpoints()),
                "claims": len(self.store.records(kind=C.KIND_CLAIM)),
                "open_obligations": len(
                    [
                        node
                        for node in self.store.nodes()
                        if node["kind"] in (C.NODE_BOTTLENECK, C.NODE_OBSTACLE) and node["status"] == "open"
                    ]
                ),
            },
            "revisions": {
                "execution": self._execution(),
                "authority": self._authority(),
            },
        }
        try:
            raw = self.backend.review_completion(packet, timeout=20.0)
            review = validate_completion_review(raw)
            denied = False
            risk_codes = list(review.get("risk_codes", []))
            if self.guard_backend is not None:
                verdict = validate_guard_verdict(
                    self.guard_backend.guard_completion(packet, review, timeout=20.0)
                )
                if not verdict["allow"]:
                    denied = True
                    risk_codes = list(verdict["risk_codes"])
        except BackendError:
            # An unavailable reviewer is not a verdict in either direction.
            return {"state": "unavailable", "denied": False, "risk_codes": []}
        except (ModelError, GuardDenied) as exc:
            return {"state": "consulted", "denied": True, "risk_codes": self._risks_from(exc)}
        return {
            "state": "consulted",
            "denied": denied,
            "advisory_sufficient": review.get("sufficient"),
            "risk_codes": risk_codes,
        }

    def _completion_issues(self, *, record_id: str | None) -> list[str]:
        issues: list[str] = []

        verified_checkpoint = any(
            checkpoint["body"].get("verified") for checkpoint in self.store.checkpoints()
        )
        if not verified_checkpoint:
            issues.append(C.ISSUE_NO_VERIFIED_CHECKPOINT)

        if record_id is None:
            issues.append(C.ISSUE_NO_VERIFIED_CLAIM)
            return sorted(set(issues))

        record = self.store.record(record_id)
        if record["kind"] != C.KIND_CLAIM:
            issues.append(C.ISSUE_UNREVIEWED_INSIGHT)
            return sorted(set(issues))
        claim = record["body"].get("claim", {})

        if self.store.review_state(record_id) != C.REVIEW_ACCEPTED:
            issues.append(C.ISSUE_NO_VERIFIED_CLAIM)
        if not claim.get("cannot_imply"):
            issues.append(C.ISSUE_CANNOT_IMPLY_MISSING)
        if not scope_is_declared(claim.get("scope")):
            issues.append(C.ISSUE_SCOPE_UNDECLARED)
        try:
            validate_grade_sufficient(claim.get("grade"), claim.get("strength"))
        except ModelError:
            issues.append(C.ISSUE_GRADE_INSUFFICIENT)
        closure = self.store.dependency_closure(record_id)
        if not closure["closed"]:
            issues.append(C.ISSUE_DEPENDENCY_OPEN)

        open_nodes = [
            node for node in self.store.nodes()
            if node["kind"] in (C.NODE_BOTTLENECK, C.NODE_OBSTACLE) and node["status"] == "open"
        ]
        if open_nodes:
            issues.append(C.ISSUE_ROUTE_PORTFOLIO_INVALID)
        return sorted(set(issues))

    def _completion_status(self, *, issues: list[str], record_id: str | None) -> str:
        if issues or record_id is None:
            return C.COMPLETION_NOT_COMPLETE
        grade = self.store.record(record_id)["body"]["claim"]["grade"]
        if grade in (C.GRADE_FORMAL, C.GRADE_CERTIFICATE):
            return C.COMPLETION_VERIFIED
        if grade == C.GRADE_EXACT_REPRODUCTION:
            return C.COMPLETION_REPRODUCIBLE
        return C.COMPLETION_ROUTE_READY

    def confirm_completion(self, assessment_id: str) -> dict[str, Any]:
        """The terminal gate.

        Three things must all hold: the assessment passed, both revisions it was
        taken against are still current, and the authority transition succeeds.
        A stale assessment is refused, which is what stops an old pass from
        being spent on new work.
        """

        assessment = self.store.assessment(assessment_id)
        if assessment["status"] == C.COMPLETION_NOT_COMPLETE:
            raise EngineError(C.ERR_COMPLETION_BLOCKED, "assessment did not pass")
        if not assessment_is_current(
            assessment, execution_revision=self._execution(), authority_revision=self._authority()
        ):
            raise EngineError(
                C.ISSUE_ASSESSMENT_STALE,
                "state advanced after this assessment was taken",
            )
        before = self._authority()

        def mutate(connection: Any) -> dict[str, Any]:
            terminal = make_record(
                kind=C.KIND_REVIEW,
                title=f"completion:{assessment_id}",
                body={
                    "assessment_id": assessment_id,
                    "status": assessment["status"],
                    "execution_revision": assessment["execution_revision"],
                    "authority_parent": before,
                },
            )
            self.store.put_record(connection, terminal)
            self.store.put_receipt(
                connection,
                receipt_id=self._op("RCT"),
                kind="completion",
                target=assessment_id,
                sha256=digest(assessment),
                body={"status": assessment["status"], "assessment_id": assessment_id},
            )
            return {"action": "completion_confirmed", "notes": [assessment_id, assessment["status"]]}

        result = self.store.transact(
            operation_id=self._op("COMPLETE"),
            head=C.HEAD_AUTHORITY,
            expected=before,
            request={"assessment_id": assessment_id},
            mutate=mutate,
        )
        return {
            "assessment_id": assessment_id,
            "status": assessment["status"],
            "authority_revision": result["revision"],
            "previous_authority_revision": before,
        }

    # -- introspection ----------------------------------------------------
    def _record_repair(self, *, code: str, risks: list[str], attempt: int) -> None:
        """Record that a repair was attempted. Observational: no head moves."""

        self.store.observe(
            event_type="turn_repair",
            payload={"code": code, "risk_codes": sorted(risks), "attempt": attempt},
            mutate=lambda connection: self.store.put_receipt(
                connection,
                receipt_id=self._op("RCT"),
                kind="repair",
                target=f"attempt-{attempt}",
                sha256=digest({"code": code, "risks": sorted(risks)}),
                body={"code": code, "risk_codes": sorted(risks), "attempt": attempt},
            ),
        )

    def _record_escalation(self, draft: dict[str, Any], *, attempt_index: int) -> dict[str, Any] | None:
        """Record a `needs_input` decision together with its investigation count.

        This Skill cannot force a model to investigate before asking. It can make
        the count visible, and a receipt saying `uninvestigated` is a factual
        statement rather than a judgement.
        """

        if draft["decision"] != "needs_input":
            return None
        investigations = sum(
            1
            for receipt in self.store.events()
            if receipt["type"] in {"execution_advanced"} and receipt["payload"].get("action") == "turn_committed"
        )
        operations_run = len(
            [event for event in self.store.events() if event["type"] == "turn_committed"]
        )
        finding = {
            "blocking_code": draft["blocking_code"],
            "investigation_turns": investigations,
            "turns_committed": operations_run,
            "investigated": investigations > 0,
            # Stated as an observation, not as a refusal: this Skill does not
            # know whether investigation was possible.
            "assessment": "investigated" if investigations > 0 else "uninvestigated",
        }
        self.store.observe(
            event_type="escalation_recorded",
            payload={
                "blocking_code": draft["blocking_code"],
                "assessment": finding["assessment"],
                "investigation_turns": investigations,
            },
            mutate=lambda connection: self.store.put_receipt(
                connection,
                receipt_id=self._op("RCT"),
                kind="escalation",
                target=str(draft["blocking_code"]),
                sha256=digest(finding),
                body=finding,
            ),
        )
        return finding

    # -- the mechanisms the operator drives --------------------------------
    def reading_layer(
        self,
        cognition: dict[str, Any],
        *,
        target_tokens: int | None = None,
        ceiling_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Render the cognition at the densest layer that fits. Never fails for size."""

        kwargs: dict[str, Any] = {}
        if target_tokens is not None:
            kwargs["target_tokens"] = target_tokens
        if ceiling_tokens is not None:
            kwargs["ceiling_tokens"] = ceiling_tokens
        return select_layer(cognition, **kwargs)

    def coverage_plan(self, *, screening_limit: int = 12) -> dict[str, Any]:
        return make_coverage_plan(self.store, screening_limit=screening_limit)

    def coverage(self, plan: dict[str, Any], decisions: dict[str, Any]) -> dict[str, Any]:
        return check_coverage(self.store, plan, decisions)

    def maintenance_plan(self) -> dict[str, Any]:
        return mechanical_impact(self.store)

    def apply_maintenance(self, decisions: list[dict[str, Any]]) -> dict[str, Any]:
        from .maintenance import apply_decisions

        return apply_decisions(self.store, decisions, operation_id=self._op("MAINT"))

    def acceptance_gate(self) -> Any:
        from .acceptance import AcceptanceGate

        return AcceptanceGate(self.store)

    def status(self) -> dict[str, Any]:
        base = self.store.status()
        base["claims"] = [
            {
                "record_id": record["record_id"],
                "review_state": self.store.review_state(record["record_id"]),
                "effect": self.record_effect(record["record_id"]),
                "grade": record["body"].get("claim", {}).get("grade"),
                "strength": record["body"].get("claim", {}).get("strength"),
            }
            for record in self.store.records(kind=C.KIND_CLAIM)
        ]
        base["guard_audit"] = self.guard.audit_trail()
        return base
