"""This Skill-owned turn loop.

This module exists to answer one question about the direction of control.

In a prompt-only deployment the model is the operator: it reads a document and
decides what to do. Here the roles are inverted. `Session` owns the loop:

    user text -> Skill gate -> Skill builds the packet
              -> Skill calls the model   (the model has no choice about when)
              -> Skill validates the returned structure
              -> Skill guards it
              -> Skill decides whether to execute a requested operation
              -> Skill renders the visible text
              -> Skill commits

At no point does the model hold a capability, a path, a command or a turn. It
receives a bounded packet and returns a closed structure. Everything else is the
the Skill's decision, and the transcript records what this Skill did rather than
what the model claimed it did.
"""

from __future__ import annotations

from typing import Any

from . import constants as C
from .engine import Engine
from .model import digest, utc_now
from .store import Store


class Session:
    """A driver around `Engine.turn` that keeps an auditable transcript."""

    def __init__(
        self,
        engine: Engine,
        *,
        actor: str = "worker",
        purpose: str = "research",
        attempt_id: str | None = None,
        transcript_limit: int = 200,
    ) -> None:
        if actor not in C.ACTORS:
            raise ValueError(f"unknown actor {actor}")
        self.engine = engine
        self.store: Store = engine.store
        self.actor = actor
        self.purpose = purpose
        self.attempt_id = attempt_id
        self.transcript_limit = transcript_limit
        self.transcript: list[dict[str, Any]] = []

    # -- the loop ---------------------------------------------------------
    def submit(self, text: str) -> dict[str, Any]:
        """One turn, driven entirely by this Skill."""

        result = self.engine.turn(
            text, actor=self.actor, purpose=self.purpose, attempt_id=self.attempt_id
        )
        entry = {
            "at": utc_now(),
            # The caller's own text is recorded because the caller wrote it.
            "input_sha256": digest(text),
            "input_chars": len(text),
            "status": result["status"],
            "visible": result.get("visible") or result.get("safe_visible"),
            "revision": result.get("revision"),
            "operation": result.get("operation"),
        }
        if result["status"] != "ok":
            entry["code"] = result.get("code")
            entry["risk_codes"] = result.get("risk_codes", [])
        self.transcript.append(entry)
        if len(self.transcript) > self.transcript_limit:
            del self.transcript[: len(self.transcript) - self.transcript_limit]
        # The returned object is exactly what a surface may display: the
        # renderer's text, a status, and closed codes.
        return {
            "status": result["status"],
            "visible": entry["visible"],
            "code": entry.get("code"),
            "risk_codes": entry.get("risk_codes", []),
            "operation": entry["operation"],
            "revision": entry["revision"],
        }

    # -- introspection ----------------------------------------------------
    def obligations(self, *, record_id: str | None = None) -> dict[str, Any]:
        return self.engine.assess_completion(record_id=record_id)["assessment"]

    def rendered_obligations(self, *, record_id: str | None = None) -> str:
        assessment = self.obligations(record_id=record_id)
        return self.engine.renderer.render_completion(assessment)

    def capabilities(self) -> dict[str, Any]:
        """What the model may ask for, and what it may never do."""

        return {
            "control_direction": "Skill controls model",
            "model_may": ["return a closed draft", "request one declared operation"],
            "model_may_not": [
                "run a process",
                "open a path",
                "reach the network",
                "write the visible text",
                "advance authority",
                "choose when it is called",
            ],
            "operations": self.engine.available_operations(),
        }

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.transcript[-limit:]
