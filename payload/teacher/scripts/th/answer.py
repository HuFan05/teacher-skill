"""The answer gate: what a model may say, checked by code.

The model writes the content of an answer — that is what it is for — but only
into declared slots, and the slots are checked before anything is shown:

    conclusion      one short paragraph, first thing the requester reads
    points          1..5 claims, each with strength, basis, grade, evidence ids,
                    an optional verbatim quote and a `cannot_imply` statement
    explanation     step / motivation / boundary / alternative
    unknowns        what is not known or not finished
    next_checks     what would settle an open point
    followups       at most two likely next needs, offered, never forced

The checks are all decidable without a model. They do not certify that an
answer is *right*; they certify that every claim says what it rests on, that
every quoted source sentence really is in the source this Skill fetched, that
no claim is stronger than its evidence allows, that unknowns are declared when
the evidence is incomplete, and that the answer stays short. The metrics that
come out of the gate are counts the requester can check.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Mapping, Sequence

from . import constants as C
from .model import ModelError

URL_RE = re.compile(r"https?://\S+", re.I)
EVIDENCE_REF_RE = re.compile(r"\AE[1-9][0-9]{0,2}\Z")


class AnswerRejected(ModelError):
    """Carries closed risk codes and the field paths they apply to.

    A field path such as `points[2].quote` is a structural fact computed here;
    it tells a repair *where* the defect is without telling it anything a
    reviewer saw.
    """

    def __init__(self, risk_codes: Sequence[str], fields: Sequence[str] = ()) -> None:
        codes = sorted(set(risk_codes)) or [C.RISK_SCHEMA]
        super().__init__(f"answer rejected: {codes}")
        self.code = "answer_rejected"
        self.risk_codes = codes
        self.fields = sorted(set(fields))[:24]


def _norm(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r"[\s\W_]+", "", folded)


# --------------------------------------------------------------------------
# Investigation plan
# --------------------------------------------------------------------------
def validate_plan(draft: Any) -> dict[str, Any]:
    from .broker import OperationRefused, validate_request

    if not isinstance(draft, dict) or set(draft) != set(C.PLAN_KEYS):
        raise ModelError(f"{C.RISK_SCHEMA}: plan keys must be {list(C.PLAN_KEYS)}")
    if draft["action"] not in C.PLAN_ACTIONS:
        raise ModelError(f"{C.RISK_SCHEMA}: unknown plan action {draft['action']!r}")
    if draft["reason_code"] not in C.PLAN_REASON_CODES:
        raise ModelError(f"{C.RISK_SCHEMA}: unknown reason code {draft['reason_code']!r}")
    operations = draft["operations"]
    if not isinstance(operations, list):
        raise ModelError(f"{C.RISK_SCHEMA}: operations must be a list")
    if draft["action"] == "request_operations":
        if not 1 <= len(operations) <= C.OPS_PER_STEP:
            raise ModelError(f"{C.RISK_SCHEMA}: request between 1 and {C.OPS_PER_STEP} operations")
        for item in operations:
            try:
                validate_request(item)
            except OperationRefused as exc:
                raise ModelError(f"{C.RISK_SCHEMA}: operation rejected: {exc.code}") from exc
    elif operations:
        raise ModelError(f"{C.RISK_SCHEMA}: only request_operations may carry operations")
    return draft


# --------------------------------------------------------------------------
# Answer
# --------------------------------------------------------------------------
def _text(value: Any, limit: int, *, allow_empty: bool = False) -> bool:
    if not isinstance(value, str):
        return False
    if not value.strip() and not allow_empty:
        return False
    return len(value) <= limit


def validate_answer(
    draft: Any,
    *,
    kind: str,
    evidence: Mapping[str, Mapping[str, Any]],
    user_text: str,
    investigation: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate an answer draft against the evidence this ask actually gathered.

    `evidence` maps packet ids (E1, E2, …) to items with `kind`
    (`retrieved_source` / `executed_check`), `text`, and for checks
    `returncode`. `investigation` carries `steps`, `minimum_met`,
    `budget_exhausted`, `sources_unavailable` and `omitted`.

    Returns the metrics. Raises AnswerRejected with closed risk codes.
    """

    risks: list[str] = []
    fields: list[str] = []

    def flag(code: str, path: str) -> None:
        risks.append(code)
        fields.append(path)

    if not isinstance(draft, dict) or set(draft) != set(C.ANSWER_KEYS):
        raise AnswerRejected([C.RISK_SCHEMA], ["answer"])

    if not _text(draft["conclusion"], C.LIMIT_CONCLUSION):
        flag("length_exceeded" if isinstance(draft["conclusion"], str) and draft["conclusion"].strip() else C.RISK_SCHEMA, "conclusion")
    elif URL_RE.search(draft["conclusion"]) or draft["conclusion"].lstrip().startswith("#"):
        flag(C.RISK_SCHEMA, "conclusion")
    if draft["confidence"] not in C.CONFIDENCE_LEVELS:
        flag(C.RISK_SCHEMA, "confidence")

    points = draft["points"]
    if not isinstance(points, list) or not 1 <= len(points) <= C.MAX_POINTS:
        raise AnswerRejected([*risks, C.RISK_SCHEMA], [*fields, "points"])

    verified_points = 0
    reasoning_points = 0
    quotes_checked = 0
    quotes_verified = 0
    cited: set[str] = set()
    seen: set[str] = set()
    for index, point in enumerate(points):
        at = f"points[{index}]"
        if not isinstance(point, dict) or set(point) != set(C.POINT_KEYS):
            flag(C.RISK_SCHEMA, at)
            continue
        if not _text(point["text"], C.LIMIT_POINT):
            flag("length_exceeded", at + ".text")
        key = _norm(str(point["text"]))
        if key in seen:
            flag("duplicate_points", at + ".text")
        seen.add(key)
        strength, basis, grade = point["strength"], point["basis"], point["grade"]
        if strength not in C.STRENGTHS or basis not in C.BASES:
            flag(C.RISK_SCHEMA, at + (".strength" if strength not in C.STRENGTHS else ".basis"))
            continue
        if grade is not None and grade not in C.GRADES:
            flag(C.RISK_SCHEMA, at + ".grade")
            continue
        refs = point["evidence"]
        if not isinstance(refs, list) or any(not isinstance(ref, str) or not EVIDENCE_REF_RE.match(ref) for ref in refs):
            flag(C.RISK_SCHEMA, at + ".evidence")
            continue
        missing = [ref for ref in refs if ref not in evidence]
        if missing:
            flag("evidence_unresolved", at + ".evidence")
            continue

        # Basis and evidence must agree.
        if basis == "retrieved_source":
            if not refs or any(evidence[ref].get("kind") != "retrieved_source" for ref in refs):
                flag("evidence_unresolved", at + ".basis")
                continue
            if grade in (C.GRADE_FORMAL, C.GRADE_CERTIFICATE):
                # A text saying something was proved is not a checker having
                # checked it. Only a declared checker run can carry these.
                flag(C.RISK_EVIDENCE_INSUFFICIENT, at + ".grade")
        elif basis == "executed_check":
            if not refs or any(evidence[ref].get("kind") != "executed_check" for ref in refs):
                flag("evidence_unresolved", at + ".basis")
                continue
            if grade in (C.GRADE_FORMAL, C.GRADE_CERTIFICATE) and not all(
                evidence[ref].get("checker") in (C.GRADE_FORMAL, C.GRADE_CERTIFICATE) for ref in refs
            ):
                # A program that ran is not a proof checker. Only a command the
                # contract declares as a checker can carry these grades.
                flag(C.RISK_EVIDENCE_INSUFFICIENT, at + ".grade")
        elif basis == "user_supplied":
            if not point["quote"] or point["quote"] not in user_text:
                flag("quote_unverified", at + ".quote")
                continue
        else:  # reasoning
            reasoning_points += 1
            if refs:
                flag("evidence_unresolved", at + ".evidence")
            if grade is not None or strength == C.STRENGTH_UNIVERSAL:
                # Reasoning alone cannot be graded and cannot carry a universal
                # statement; it must say under which conditions it holds.
                flag("reasoning_overclaim", at + (".grade" if grade is not None else ".strength"))

        # Quotes are verified byte for byte against a cited excerpt.
        quote = point["quote"]
        if quote is not None:
            if not isinstance(quote, str) or not quote.strip() or len(quote) > C.MAX_QUOTE_IN_ANSWER:
                flag(C.RISK_SCHEMA, at + ".quote")
            elif basis == "user_supplied":
                quotes_checked += 1
                quotes_verified += 1
            else:
                quotes_checked += 1
                if any(quote in str(evidence[ref].get("text", "")) for ref in refs):
                    quotes_verified += 1
                else:
                    flag("quote_unverified", at + ".quote")

        # Strength and grade.
        if grade is not None:
            allowed = C.MINIMUM_GRADE_BY_STRENGTH[strength]
            if grade not in allowed:
                flag(C.RISK_EVIDENCE_INSUFFICIENT, at + ".grade")
        elif strength in (C.STRENGTH_UNIVERSAL, C.STRENGTH_BOUNDED) and basis != "reasoning":
            flag(C.RISK_UNTYPED_CLAIM, at + ".grade")

        # What a result does not imply.
        if strength != C.STRENGTH_OBSERVATION:
            if not _text(point["cannot_imply"], C.LIMIT_SHORT):
                flag(C.RISK_CANNOT_IMPLY_MISSING, at + ".cannot_imply")
        elif point["cannot_imply"] is not None and not _text(point["cannot_imply"], C.LIMIT_SHORT):
            flag(C.RISK_SCHEMA, at + ".cannot_imply")

        if basis in ("retrieved_source", "executed_check", "user_supplied"):
            verified_points += 1
            cited.update(refs)

    explanation = draft["explanation"]
    if not isinstance(explanation, list) or len(explanation) > C.MAX_EXPLANATION:
        flag(C.RISK_SCHEMA, "explanation")
        explanation = []
    for index, entry in enumerate(explanation):
        if not isinstance(entry, dict) or set(entry) != set(C.EXPLANATION_KEYS):
            flag(C.RISK_SCHEMA, f"explanation[{index}]")
            continue
        for key in C.EXPLANATION_KEYS:
            if not _text(entry[key], C.LIMIT_SLOT):
                flag("explanation_slots_missing", f"explanation[{index}].{key}")
    if kind in C.EXPLANATION_REQUIRED_KINDS and not explanation:
        # Explaining a step means: why it is taken, where it stops working, and
        # what to do then. Without the slots it is a summary, not an explanation.
        flag("explanation_slots_missing", "explanation")

    unknowns = draft["unknowns"]
    if not isinstance(unknowns, list) or len(unknowns) > C.MAX_UNKNOWNS or not all(_text(item, C.LIMIT_SHORT) for item in unknowns):
        flag(C.RISK_SCHEMA, "unknowns")
        unknowns = []
    honesty_triggers = (
        reasoning_points > 0
        or bool(investigation.get("budget_exhausted"))
        or bool(investigation.get("sources_unavailable"))
        or int(investigation.get("omitted", 0)) > 0
    )
    if honesty_triggers and not unknowns:
        flag("unknowns_missing", "unknowns")
    if draft["confidence"] == "high" and verified_points == 0:
        flag("confidence_unsupported", "confidence")

    if kind in C.EVIDENCE_REQUIRED_KINDS and not investigation.get("minimum_met"):
        flag("investigation_minimum_unmet", "points")

    checks = draft["next_checks"]
    if not isinstance(checks, list) or len(checks) > C.MAX_NEXT_CHECKS:
        flag(C.RISK_SCHEMA, "next_checks")
        checks = []
    from .broker import OPERATIONS

    for index, check in enumerate(checks):
        if not isinstance(check, dict) or set(check) != set(C.CHECK_KEYS) or not _text(check["text"], C.LIMIT_SHORT):
            flag(C.RISK_SCHEMA, f"next_checks[{index}]")
            continue
        if check["operation"] is not None and check["operation"] not in OPERATIONS:
            flag(C.RISK_SCHEMA, f"next_checks[{index}].operation")

    followups = draft["followups"]
    if not isinstance(followups, list) or len(followups) > C.MAX_FOLLOWUPS:
        flag(C.RISK_SCHEMA, "followups")
        followups = []
    for index, item in enumerate(followups):
        if not isinstance(item, dict) or set(item) != set(C.FOLLOWUP_KEYS):
            flag(C.RISK_SCHEMA, f"followups[{index}]")
            continue
        if not _text(item["text"], C.LIMIT_FOLLOWUP):
            flag("length_exceeded", f"followups[{index}].text")
        if item["kind"] not in C.REQUEST_KINDS:
            flag(C.RISK_SCHEMA, f"followups[{index}].kind")

    if risks:
        raise AnswerRejected(risks, fields)

    visible_chars = len(draft["conclusion"]) + sum(len(point["text"]) for point in points)
    visible_chars += sum(len(entry[key]) for entry in explanation for key in C.EXPLANATION_KEYS)
    visible_chars += sum(len(item) for item in unknowns)
    return {
        "schema": "th-answer-metrics/v1",
        "points": len(points),
        "points_with_checked_evidence": verified_points,
        "points_reasoning_only": reasoning_points,
        "evidence_items_available": len(evidence),
        "evidence_items_cited": len(cited),
        "quotes_checked": quotes_checked,
        "quotes_verified": quotes_verified,
        "unknowns": len(unknowns),
        "explanation_steps": len(explanation),
        "investigation_steps": int(investigation.get("steps", 0)),
        "investigation_budget": int(investigation.get("budget", 0)),
        "operations_executed": int(investigation.get("operations", 0)),
        "operations_refused": int(investigation.get("refused", 0)),
        "candidates_seen": int(investigation.get("candidates", 0)),
        "candidates_omitted": int(investigation.get("omitted", 0)),
        "budget_exhausted": bool(investigation.get("budget_exhausted")),
        "sources_unavailable": bool(investigation.get("sources_unavailable")),
        "visible_chars": visible_chars,
        "semantic_correctness_proven": False,
    }
