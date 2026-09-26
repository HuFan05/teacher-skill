"""Independent guard.

Two layers, and the ordering is the point:

  1. `DeterministicGuard`  - pure code. Evidence grades, declared scope,
                             `cannot_imply` presence, anchor existence and
                             provenance, patch references, dependency closure.
  2. `ModelGuard`          - an optional second model call that may **veto**.

A model guard can never allow what the deterministic layer denied, and its
verdict is not a surface: it returns only a boolean and closed risk codes. The
guard is not a writer, so approving through it does not make text safe.
"""

from __future__ import annotations

from typing import Any

from . import constants as C
from .backend import ScriptedBackend, StructuredBackend, validate_guard_verdict
from .model import ModelError, digest, scope_is_declared, validate_grade_sufficient
from .store import Store


class GuardDenied(RuntimeError):
    def __init__(self, risk_codes: list[str]) -> None:
        super().__init__(f"{C.ERR_GUARD_DENIED}: {risk_codes}")
        self.code = C.ERR_GUARD_DENIED
        self.risk_codes = list(risk_codes)


class DeterministicGuard:
    """Every check here is a rule this Skill can decide without a model."""

    def __init__(self, store: Store) -> None:
        self.store = store

    # -- turn --------------------------------------------------------------
    def check_turn(
        self,
        *,
        draft: dict[str, Any],
        patch_anchor_ids: set[str] | None = None,
        patch_node_ids: set[str] | None = None,
    ) -> list[str]:
        risks: list[str] = []

        if draft["decision"] == "blocked" and not draft["blocking_code"]:
            risks.append(C.RISK_UNSUPPORTED_TRANSITION)

        anchor = draft["focus_anchor_id"]
        if anchor is not None:
            try:
                self.store.anchor(anchor)
            except Exception:
                risks.append(C.RISK_UNKNOWN_ANCHOR)

        if patch_anchor_ids:
            known = {item["anchor_id"] for item in self.store.anchors()}
            if patch_anchor_ids - known:
                risks.append(C.RISK_UNKNOWN_ANCHOR)

        if patch_node_ids:
            known_nodes = self.store.node_ids()
            if patch_node_ids - known_nodes:
                risks.append(C.RISK_UNKNOWN_ANCHOR)

        patch = draft["graph_patch"]
        patch_labels = {node["label_code"] for node in patch.get("nodes", [])}
        known_labels = set(self.store.node_ids_by_label())
        for edge in patch.get("edges", []):
            for endpoint in (edge["src"], edge["dst"]):
                if endpoint not in patch_labels and endpoint not in known_labels:
                    # The patch names something that does not exist and is not
                    # introduced by the same patch.
                    risks.append(C.RISK_UNKNOWN_ANCHOR)
        for node in patch.get("nodes", []):
            if node["kind"] in (C.NODE_CLAIM, C.NODE_RESULT) and node["status"] == "resolved":
                # A resolved claim node must be backed by an accepted claim
                # record; the guard refuses to let graph resolution stand in
                # for evidence.
                if not self._any_accepted_claim():
                    risks.append(C.RISK_EVIDENCE_INSUFFICIENT)

        return sorted(set(risks))

    def _any_accepted_claim(self) -> bool:
        for record in self.store.records(kind=C.KIND_CLAIM):
            if self.store.review_state(record["record_id"]) == C.REVIEW_ACCEPTED:
                return True
        return False

    # -- claim -------------------------------------------------------------
    def check_claim(self, claim: dict[str, Any]) -> list[str]:
        """Shape is assumed validated. This checks provenance and closure."""

        risks: list[str] = []
        if not scope_is_declared(claim.get("scope")):
            risks.append(C.RISK_SCOPE_UNDECLARED)
        if not claim.get("cannot_imply"):
            risks.append(C.RISK_CANNOT_IMPLY_MISSING)
        if not claim.get("strength"):
            risks.append(C.RISK_UNTYPED_CLAIM)
        try:
            validate_grade_sufficient(claim["grade"], claim["strength"])
        except ModelError:
            risks.append(C.RISK_EVIDENCE_INSUFFICIENT)
        risks.extend(self.check_claim_anchors(claim))
        return sorted(set(risks))

    def check_claim_anchors(self, claim: dict[str, Any]) -> list[str]:
        """Two separate defects: an anchor that does not exist, and one that
        exists but is not local. Collapsing them would hide the second."""

        risks: list[str] = []
        for anchor_id in claim.get("anchor_ids", []) or []:
            try:
                anchor = self.store.anchor(anchor_id)
            except Exception:
                risks.append(C.RISK_UNKNOWN_ANCHOR)
                continue
            if anchor["hash_state"] == "stale":
                risks.append(C.RISK_FOREIGN_ANCHOR)
        return risks

    def check_dependencies(self, record_id: str) -> list[str]:
        closure = self.store.dependency_closure(record_id)
        if not closure["closed"]:
            return [C.RISK_EVIDENCE_INSUFFICIENT]
        return []

    def check_promotion(self, record_id: str) -> list[str]:
        """Everything a claim must satisfy before it may reach authority."""

        record = self.store.record(record_id)
        if record["kind"] != C.KIND_CLAIM:
            return [C.RISK_SCHEMA]
        claim = record["body"].get("claim")
        if not isinstance(claim, dict):
            return [C.RISK_SCHEMA]
        risks = self.check_claim(claim)
        if self.store.review_state(record_id) != C.REVIEW_ACCEPTED:
            risks.append(C.RISK_ASSESSMENT_INCONSISTENT)
        return sorted(set(risks))


class ModelGuard:
    """A second, independent backend call. It can only subtract."""

    def __init__(self, backend: StructuredBackend, *, timeout: float = 20.0) -> None:
        self.backend = backend
        self.timeout = timeout

    def verdict(self, *, context_packet: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
        raw = self.backend.guard_turn(context_packet, candidate, timeout=self.timeout)
        return validate_guard_verdict(raw)


class Guard:
    """Deterministic first. Deterministic denial is final.

    If the deterministic layer denies, the model layer is not consulted: there
    is no reason to spend a call, and no way for a lenient model to reopen a
    closed structural defect.
    """

    def __init__(self, store: Store, backend: StructuredBackend | None = None, *, timeout: float = 20.0) -> None:
        self.deterministic = DeterministicGuard(store)
        self.model = ModelGuard(backend, timeout=timeout) if backend is not None else None
        self.audit: list[dict[str, Any]] = []

    def _record(self, stage: str, risks: list[str], payload: dict[str, Any]) -> None:
        self.audit.append(
            {
                "stage": stage,
                "risks": sorted(set(risks)),
                "allowed": not risks,
                "payload_sha256": digest(payload),
            }
        )

    def review_turn(self, *, draft: dict[str, Any], context_packet: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        risks = self.deterministic.check_turn(draft=draft, **kwargs)
        if risks:
            self._record("guard.turn.deterministic", risks, draft)
            raise GuardDenied(risks)
        if self.model is not None:
            verdict = self.model.verdict(context_packet=context_packet, candidate=draft)
            if not verdict["allow"]:
                self._record("guard.turn.model", list(verdict["risk_codes"]), draft)
                raise GuardDenied(list(verdict["risk_codes"]))
        self._record("guard.turn", [], draft)
        return {"allow": True, "risk_codes": []}

    def review_claim(self, claim: dict[str, Any]) -> dict[str, Any]:
        risks = self.deterministic.check_claim(claim)
        if risks:
            self._record("guard.claim", risks, claim)
            raise GuardDenied(risks)
        return {"allow": True, "risk_codes": []}

    def review_promotion(self, record_id: str) -> dict[str, Any]:
        risks = self.deterministic.check_promotion(record_id)
        if risks:
            self._record("guard.promotion", risks, {"record_id": record_id})
            raise GuardDenied(risks)
        return {"allow": True, "risk_codes": []}

    def audit_trail(self) -> list[dict[str, Any]]:
        return list(self.audit)
