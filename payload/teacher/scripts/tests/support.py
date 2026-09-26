"""Shared test fixtures.

Every fixture injects a backend, so the whole suite runs without a model. That
is not a convenience: it is the reason the gates can be tested at all.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from th import Engine, ScriptedBackend, Store, make_draft
from th import constants as C

ALLOW: dict[str, Any] = {"allow": True, "risk_codes": []}
CANARY = "MODEL_CANARY_4f92c1::q_{918273}^7=r_{564738}::END"


def deny(*codes: str) -> dict[str, Any]:
    if not codes:
        raise ValueError("a denial must name at least one risk code")
    return {"allow": False, "risk_codes": list(codes)}


def contract() -> dict[str, Any]:
    return {
        "objective": {
            "statement": "判断该训练目标是否在声明的范围内改善指标",
            "domain": "machine learning",
            "claim_scope": "held-out accuracy over 3 seeds, mean",
            "assumptions": ["fixed dataset split"],
            "evidence_standard": C.GRADE_EXACT_REPRODUCTION,
            "completion_standard": "closed dependency chain with a reproduced result",
        },
        "delegation": {"tools": ["read", "write"], "capabilities": ["read"]},
        "scope": {"roots": ["."]},
    }


def scope(**overrides: Any) -> dict[str, Any]:
    base = {
        "datasets": ["synthetic-a"],
        "models": ["mlp-2x64"],
        "compute": ["single-cpu"],
        "seeds": ["0"],
        "assumptions": ["iid split"],
    }
    base.update(overrides)
    return base


def claim(**overrides: Any) -> dict[str, Any]:
    base = {
        "statement": "在声明范围内该目标改善 held-out accuracy",
        "strength": C.STRENGTH_BOUNDED,
        "grade": C.GRADE_EXACT_REPRODUCTION,
        "scope": scope(),
        "evidence_ids": ["EV-1"],
        "cannot_imply": ["不能推出在其他数据集或规模上同样改善"],
    }
    base.update(overrides)
    return base


def route(index: int, **overrides: Any) -> dict[str, Any]:
    families = [
        {"learning_signal": "axis.supervised_ce", "architecture_family": "axis.mlp", "compute_regime": "axis.single_cpu"},
        {"learning_signal": "axis.self_supervised", "architecture_family": "axis.transformer", "compute_regime": "axis.multi_gpu"},
        {"learning_signal": "axis.reinforcement", "architecture_family": "axis.graph_net", "compute_regime": "axis.cluster"},
    ]
    base = {
        "route_id": f"RT-{index}",
        "label_code": f"route.candidate_{index}",
        "axes": dict(families[index % 3]),
        "hypothesis": f"hypothesis {index}",
        "falsifier": f"falsifier {index}",
    }
    base.update(overrides)
    return base


def routes() -> list[dict[str, Any]]:
    return [route(0), route(1), route(2)]


class Harness:
    """A store, an engine and a scripted backend wired together."""

    def __init__(
        self,
        *,
        drafts: list[Any] | None = None,
        verdicts: list[Any] | None = None,
        intakes: list[Any] | None = None,
        intake_verdicts: list[Any] | None = None,
        completion: list[Any] | None = None,
        completion_verdicts: list[Any] | None = None,
        path: str | Path | None = None,
        contract_override: dict[str, Any] | None = None,
    ) -> None:
        self._tmp: tempfile.TemporaryDirectory[str] | None = None
        if path is None:
            self._tmp = tempfile.TemporaryDirectory()
            path = Path(self._tmp.name) / "store.sqlite3"
        self.path = Path(path)
        self.store = Store(self.path)
        self.store.init()
        self.backend = ScriptedBackend(
            drafts=drafts or [],
            verdicts=verdicts or [],
            intakes=intakes or [],
            intake_verdicts=intake_verdicts or [],
            completion=completion or [],
            completion_verdicts=completion_verdicts or [],
        )
        self.engine = Engine(
            self.store,
            backend=self.backend,
            guard_backend=self.backend,
            contract=contract_override or contract(),
        )

    def close(self) -> None:
        self.store.close()
        if self._tmp is not None:
            self._tmp.cleanup()

    def __enter__(self) -> "Harness":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- convenience ------------------------------------------------------
    def queue_turn(self, *, verdict: dict[str, Any] | None = None, **draft_overrides: Any) -> None:
        self.backend._drafts.append(make_draft(**draft_overrides))
        self.backend._verdicts.append(verdict if verdict is not None else ALLOW)

    def queue_intake(self, candidate: dict[str, Any], verdict: dict[str, Any] | None = None) -> None:
        self.backend._intakes.append(candidate)
        self.backend._intake_verdicts.append(verdict if verdict is not None else ALLOW)

    def queue_completion(self, candidate: dict[str, Any] | None = None, verdict: dict[str, Any] | None = None) -> None:
        self.backend._completion.append(candidate or {"status": "ok"})
        self.backend._completion_verdicts.append(verdict if verdict is not None else ALLOW)

    def open_window(self) -> dict[str, Any]:
        return self.engine.open_window(routes())

    def verified_evidence(self, text: str = "4 passed") -> str:
        """Evidence the harness itself verified, as an executed check would leave."""

        from th.model import digest, new_id

        evidence_id = new_id("EV")
        self.store.observe(
            event_type="evidence_recorded",
            payload={"evidence_id": evidence_id},
            mutate=lambda connection: self.store.put_evidence(connection, {
                "evidence_id": evidence_id, "ask_id": "ASK-test", "kind": "executed_check",
                "locator": "run:tests:returncode=0", "text": text, "sha256": digest(text), "verified": True,
            }),
        )
        return evidence_id

    def verified_checkpoint(self, attempt_id: str) -> dict[str, Any]:
        return self.engine.checkpoint(attempt_id, body={}, verified=True, evidence_ids=[self.verified_evidence()])

    def accepted_claim(self, **overrides: Any) -> str:
        result = self.engine.submit_claim(claim(**overrides))
        record_id = result["record_id"]
        self.engine.review_claim(record_id, decision=C.REVIEW_ACCEPTED)
        return record_id
