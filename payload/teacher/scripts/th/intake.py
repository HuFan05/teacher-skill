"""Requirement intake: this Skill asks first.

A requester rarely arrives with a well-formed objective. They arrive with a
vague dissatisfaction: "this training run feels unstable", "I want to know
whether this method is worth using". Turning that into the six constitutive
objective fields is work, and if it is left to the requester they will guess.

This module makes intake a Skill-driven loop instead:

  * this Skill decides **which** field is missing, from a fixed order;
  * this Skill writes **the question**, from a fixed table;
  * the model may only point at the requester's own words (byte-verified) or
    name a value from a closed vocabulary;
  * this Skill decides when the shape is complete and refuses to commit
    before it is.

The model never authors a question and never authors an objective value that
reaches a surface. Its contribution is a *pointer into the requester's own
text* plus closed enums. That is what keeps the no-free-text property intact
while still making this Skill useful at the point where the requester is most
stuck: before they know how to ask.

Rounds are bounded. Exhausting them is a reportable outcome, not a licence to
lower the bar: an incomplete intake can never commit an objective.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from . import constants as C
from .model import ModelError, digest, new_id, utc_now

SCHEMA_INTAKE = "th-intake/v1"
SCHEMA_INTAKE_PACKET = "th-intake-packet/v1"

STATUS_OPEN = "open"
STATUS_READY = "ready"
STATUS_COMMITTED = "committed"
STATUS_ABANDONED = "abandoned"

INTAKE_STATUSES = (STATUS_OPEN, STATUS_READY, STATUS_COMMITTED, STATUS_ABANDONED)

ORIGIN_QUOTE = "quote"
ORIGIN_OPERATOR = "operator"

FIELD_LABEL = {
    "statement": "要判定的陈述",
    "domain": "领域与任务边界",
    "claim_scope": "结论覆盖的范围（数据集/模型/规模/种子与指标，平均还是最坏情况）",
    "assumptions": "前提假设",
    "evidence_standard": "证据标准",
    "completion_standard": "终局完成条件",
}

# The closed vocabulary this Skill is willing to offer as a default. Each code
# maps to fixed text in the renderer, so offering a default adds no model text.
ASSUMPTION_CODES = C.INTAKE_ASSUMPTIONS


class IntakeError(RuntimeError):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------
def new_state(brief: str) -> dict[str, Any]:
    if not isinstance(brief, str) or not brief.strip():
        raise IntakeError(C.ERR_INPUT_REJECTED, "a brief is required")
    return {
        "schema": SCHEMA_INTAKE,
        "intake_id": new_id("IN"),
        "brief": brief,
        "fields": {},
        "absent": [],
        "assumed_defaults": [],
        "round": 0,
        "status": STATUS_OPEN,
        "history": [],
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }


def next_gap(state: Mapping[str, Any]) -> str | None:
    """The first unfilled field, in the declared order. Never model-chosen."""

    fields = state.get("fields", {})
    for field in C.INTAKE_ORDER:
        if not fields.get(field):
            return field
    return None


def missing_fields(state: Mapping[str, Any]) -> list[str]:
    fields = state.get("fields", {})
    return [field for field in C.INTAKE_ORDER if not fields.get(field)]


def completeness(state: Mapping[str, Any]) -> dict[str, Any]:
    missing = missing_fields(state)
    return {
        "schema": "th-intake-completeness/v1",
        "required": len(C.INTAKE_ORDER),
        "filled": len(C.INTAKE_ORDER) - len(missing),
        "missing": missing,
        "complete": not missing,
        "evidence_standard": state.get("fields", {}).get("evidence_standard"),
        "round": state.get("round", 0),
        "rounds_remaining": max(0, C.INTAKE_MAX_ROUNDS - int(state.get("round", 0))),
    }


def packet(state: Mapping[str, Any]) -> dict[str, Any]:
    """What the model is allowed to see. Closed values plus the brief itself."""

    gap = next_gap(state)
    return {
        "schema": SCHEMA_INTAKE_PACKET,
        "brief": state.get("brief", ""),
        "filled_fields": sorted(state.get("fields", {})),
        "missing_fields": missing_fields(state),
        "gap": gap,
        "round": state.get("round", 0),
        "offered_assumptions": list(ASSUMPTION_CODES),
        "evidence_standards": list(C.GRADES),
        "instruction": (
            "只能指出 brief 里逐字出现的内容，或给出封闭词表里的值。"
            "不要撰写新的表述；你写的任何散文都会被丢弃。"
        ),
    }


# --------------------------------------------------------------------------
# Draft validation. Everything here is decidable without a model.
# --------------------------------------------------------------------------
def validate_intake_draft(
    draft: Any,
    *,
    brief: str,
    gap: str | None,
    state: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(draft, dict):
        raise ModelError(f"{C.RISK_SCHEMA}: intake draft must be an object")
    unknown = set(draft) - set(C.INTAKE_KEYS)
    if unknown:
        raise ModelError(f"{C.RISK_SCHEMA}: intake draft carries undeclared keys: {sorted(unknown)}")
    missing = [key for key in C.INTAKE_KEYS if key not in draft]
    if missing:
        raise ModelError(f"{C.RISK_SCHEMA}: intake draft missing keys: {sorted(missing)}")

    decision = draft["decision"]
    if decision not in C.INTAKE_DECISIONS:
        raise ModelError(f"{C.RISK_SCHEMA}: unknown intake decision: {decision!r}")

    # The gap is this Skill's business. A model that answers a different field
    # is not helping; it is choosing the workflow.
    field = draft["field"]
    if field is not None:
        if field not in C.INTAKE_ORDER:
            raise ModelError(f"{C.RISK_SCHEMA}: unknown intake field: {field!r}")
        if field != gap:
            raise ModelError(f"{C.ERR_INTAKE_FIELD_MISMATCH}: expected {gap!r}, got {field!r}")

    quote = draft["quote"]
    if quote is not None:
        if not isinstance(quote, str) or not quote.strip():
            raise ModelError(f"{C.RISK_SCHEMA}: quote must be a non-empty string or null")
        if len(quote) > C.INTAKE_MAX_QUOTE_CHARS:
            raise ModelError(f"{C.RISK_SCHEMA}: quote exceeds {C.INTAKE_MAX_QUOTE_CHARS} characters")
        if quote not in brief:
            # The one thing a model may contribute is a pointer into the
            # requester's own words. An unverified pointer is worthless.
            raise ModelError(f"{C.ERR_INTAKE_QUOTE_UNVERIFIED}: quote is not verbatim brief text")

    if not isinstance(draft["absent"], bool):
        raise ModelError(f"{C.RISK_SCHEMA}: absent must be a boolean")

    standard = draft["evidence_standard"]
    if standard is not None and standard not in C.GRADES:
        raise ModelError(f"{C.RISK_SCHEMA}: unknown evidence standard: {standard!r}")

    defaults = draft["assumed_defaults"]
    if not isinstance(defaults, list) or len(defaults) > C.INTAKE_MAX_ASSUMPTIONS:
        raise ModelError(f"{C.RISK_SCHEMA}: assumed_defaults must be a bounded list")
    for code in defaults:
        if code not in ASSUMPTION_CODES:
            raise ModelError(f"{C.RISK_SCHEMA}: unknown assumed default: {code!r}")

    blocking = draft["blocking_code"]
    if blocking is not None:
        if blocking not in ("insufficient_context", "objective_conflict", "out_of_scope"):
            raise ModelError(f"{C.RISK_SCHEMA}: unknown intake blocking code: {blocking!r}")

    # A model cannot declare readiness. Only a complete shape is ready.
    if decision == "ready" and missing_fields(state):
        raise ModelError(f"{C.ERR_INTAKE_PREMATURE_READY}: readiness is computed, not declared")

    return draft


# --------------------------------------------------------------------------
# Applying a draft
# --------------------------------------------------------------------------
def apply_draft(state: Mapping[str, Any], draft: Mapping[str, Any]) -> dict[str, Any]:
    """Fill whatever this round resolved. Never invent; never reorder."""

    updated = dict(state)
    fields = dict(updated.get("fields", {}))
    history = list(updated.get("history", []))
    gap = next_gap(updated)

    defaults = [str(item) for item in draft.get("assumed_defaults", [])]
    merged_defaults = sorted(set(list(updated.get("assumed_defaults", [])) + defaults))

    quote = draft.get("quote")
    field = draft.get("field")
    if quote and field == gap:
        fields[field] = [quote] if field == "assumptions" else quote
        updated["fields"] = fields
    elif draft.get("evidence_standard") and gap == "evidence_standard":
        fields[gap] = draft["evidence_standard"]
        updated["fields"] = fields
    elif draft.get("absent") and field is not None and field not in updated.get("absent", []):
        updated["absent"] = sorted(set(list(updated.get("absent", [])) + [field]))

    updated["assumed_defaults"] = merged_defaults
    updated["round"] = int(updated.get("round", 0)) + 1
    updated["status"] = STATUS_READY if not missing_fields(updated) else STATUS_OPEN
    updated["updated_at"] = utc_now()
    history.append(
        {
            "round": updated["round"],
            "field": field,
            "decision": draft.get("decision"),
            "filled": bool(quote and field == gap),
            "declared_absent": bool(draft.get("absent")),
            "quote_sha256": digest(quote) if quote else None,
        }
    )
    updated["history"] = history
    return updated


def set_field(state: Mapping[str, Any], field: str, value: Any) -> dict[str, Any]:
    """The operator fills a field directly. This is the one path that carries
    authored text into an objective, and it is a human path."""

    if field not in C.INTAKE_ORDER:
        raise IntakeError(C.ERR_INTAKE_FIELD_MISMATCH, f"unknown field {field!r}")
    if isinstance(value, str):
        value = [value] if field == "assumptions" else value
    if not value:
        raise IntakeError(C.ERR_INTAKE_INCOMPLETE, f"{field} must not be empty")
    updated = dict(state)
    fields = dict(updated.get("fields", {}))
    fields[field] = value
    updated["fields"] = fields
    updated["absent"] = [item for item in updated.get("absent", []) if item != field]
    updated["status"] = STATUS_READY if not missing_fields(updated) else STATUS_OPEN
    updated["updated_at"] = utc_now()
    return updated


def set_standard(state: Mapping[str, Any], standard: str) -> dict[str, Any]:
    """The evidence standard is one of the six fields, and it is closed."""

    if standard not in C.GRADES:
        raise IntakeError(C.ERR_INTAKE_INCOMPLETE, f"unknown evidence standard {standard!r}")
    return set_field(state, "evidence_standard", standard)


# --------------------------------------------------------------------------
# Committing
# --------------------------------------------------------------------------
def to_objective(state: Mapping[str, Any], *, assumption_phrases: Mapping[str, str]) -> dict[str, Any]:
    """Build the six constitutive fields. Refuses an incomplete shape."""

    missing = missing_fields(state)
    if missing:
        raise IntakeError(C.ERR_INTAKE_INCOMPLETE, f"objective is missing: {sorted(missing)}")
    assumptions = list(state["fields"].get("assumptions") or [])
    assumptions.extend(
        assumption_phrases[code] for code in sorted(state.get("assumed_defaults", []))
        if code in assumption_phrases
    )
    if not assumptions:
        raise IntakeError(C.ERR_INTAKE_INCOMPLETE, "an objective needs at least one assumption")
    standard = state["fields"].get("evidence_standard")
    if standard not in C.GRADES:
        raise IntakeError(C.ERR_INTAKE_INCOMPLETE, "the evidence standard must be a declared grade")
    return {
        "statement": state["fields"]["statement"],
        "domain": state["fields"]["domain"],
        "claim_scope": state["fields"]["claim_scope"],
        "assumptions": assumptions,
        "evidence_standard": standard,
        "completion_standard": state["fields"]["completion_standard"],
    }
