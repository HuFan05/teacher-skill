"""Canonical record model.

Every hash in this Skill is computed over `canonical_bytes()` of a structure,
so canonicalization must be total and stable. `validate_claim` is the single
place where claim shape is checked; the guard and the completion gate both
route through it rather than re-implementing rules.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import base64
from datetime import datetime, timezone
from typing import Any

from . import constants as C


class ModelError(ValueError):
    """A structure violates the declared model."""


# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------
def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(8)}"


def new_anchor_id() -> str:
    """Content-independent anchor identifier.

    `AN-` + base32 of 16 random bytes, so the identifier never encodes a path,
    a timestamp or a sequence position. Moving or renaming the underlying
    artifact cannot change it, and it cannot be predicted from the corpus.
    """

    return "AN-" + base64.b32encode(secrets.token_bytes(16)).decode("ascii")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def digest_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def short(text: str, limit: int = 160) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


# --------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------
def _require_enum(name: str, value: Any, allowed) -> None:
    if value not in allowed:
        raise ModelError(f"{name} is not declared: {value!r}")


def validate_enum(name: str, value: Any, allowed) -> None:
    _require_enum(name, value, allowed)


# --------------------------------------------------------------------------
# Scope. A claim without a declared scope cannot be promoted, because there is
# nothing to check a bound against.
# --------------------------------------------------------------------------
SCOPE_FIELDS = ("datasets", "models", "compute", "seeds", "assumptions")
OPTIONAL_SCOPE_FIELDS = ("metric", "environment", "hardware", "code_revision")


def validate_scope(scope: Any) -> dict[str, Any]:
    if not isinstance(scope, dict):
        raise ModelError("scope must be an object")
    unknown = set(scope) - set(SCOPE_FIELDS) - set(OPTIONAL_SCOPE_FIELDS)
    if unknown:
        raise ModelError(f"unknown scope fields: {sorted(unknown)}")
    missing = [field for field in SCOPE_FIELDS if not scope.get(field)]
    if missing:
        raise ModelError(f"{C.ERR_SCOPE_UNDECLARED}: scope fields missing: {missing}")
    for field in SCOPE_FIELDS:
        if not isinstance(scope[field], list) or not all(isinstance(item, str) and item.strip() for item in scope[field]):
            raise ModelError(f"scope.{field} must be a list of non-empty strings")
    return scope


def scope_is_declared(scope: Any) -> bool:
    if not isinstance(scope, dict):
        return False
    return all(scope.get(field) for field in SCOPE_FIELDS)


# --------------------------------------------------------------------------
# Claim
# --------------------------------------------------------------------------
CLAIM_FIELDS = ("statement", "strength", "grade", "scope", "evidence_ids", "cannot_imply")
OPTIONAL_CLAIM_FIELDS = ("anchor_ids", "notes_code")


def validate_claim(claim: Any) -> dict[str, Any]:
    """Validate claim shape and the grade/strength rule.

    Note the ordering: shape first, then the rule. A violation of the grade
    rule raises with the exact code the completion gate reports, so the two
    paths cannot drift.
    """

    if not isinstance(claim, dict):
        raise ModelError("claim must be an object")
    unknown = set(claim) - set(CLAIM_FIELDS) - set(OPTIONAL_CLAIM_FIELDS)
    if unknown:
        raise ModelError(f"unknown claim fields: {sorted(unknown)}")
    missing = [field for field in CLAIM_FIELDS if field not in claim]
    if missing:
        raise ModelError(f"claim missing fields: {sorted(missing)}")

    statement = claim["statement"]
    if not isinstance(statement, str) or not statement.strip():
        raise ModelError("claim statement must be non-empty")
    if len(statement) > 4000:
        raise ModelError("claim statement exceeds the declared limit")

    strength = claim["strength"]
    if strength is None:
        raise ModelError(f"{C.RISK_UNTYPED_CLAIM}: claim has no declared strength")
    validate_enum("strength", strength, C.STRENGTHS)
    validate_enum("grade", claim["grade"], C.GRADES)

    evidence_ids = claim["evidence_ids"]
    if not isinstance(evidence_ids, list) or not evidence_ids:
        raise ModelError("claim needs at least one evidence id")
    if not all(isinstance(item, str) and item.strip() for item in evidence_ids):
        raise ModelError("evidence_ids must be non-empty strings")

    cannot_imply = claim["cannot_imply"]
    if not isinstance(cannot_imply, list) or not cannot_imply:
        raise ModelError(f"{C.ERR_CANNOT_IMPLY_MISSING}: a claim must state what it does not imply")
    if not all(isinstance(item, str) and item.strip() for item in cannot_imply):
        raise ModelError("cannot_imply entries must be non-empty strings")

    validate_scope(claim["scope"])

    allowed = C.MINIMUM_GRADE_BY_STRENGTH[strength]
    if claim["grade"] not in allowed:
        raise ModelError(
            f"{C.ERR_EVIDENCE_INSUFFICIENT}: grade {claim['grade']!r} cannot support "
            f"strength {strength!r}; needs one of {list(allowed)}"
        )
    return claim


def validate_grade_sufficient(grade: str, strength: str) -> None:
    validate_enum("grade", grade, C.GRADES)
    validate_enum("strength", strength, C.STRENGTHS)
    if grade not in C.MINIMUM_GRADE_BY_STRENGTH[strength]:
        raise ModelError(
            f"{C.ERR_EVIDENCE_INSUFFICIENT}: grade {grade!r} cannot support strength {strength!r}"
        )


# --------------------------------------------------------------------------
# Objective. Immutable: six fields, nothing else. Any change to one of them is
# a new objective, not an update.
# --------------------------------------------------------------------------
OBJECTIVE_FIELDS = (
    "statement",
    "domain",
    "claim_scope",
    "assumptions",
    "evidence_standard",
    "completion_standard",
)


def validate_objective(objective: Any) -> dict[str, Any]:
    if not isinstance(objective, dict):
        raise ModelError("objective must be an object")
    unknown = set(objective) - set(OBJECTIVE_FIELDS)
    if unknown:
        raise ModelError(f"objective carries non-constitutive fields: {sorted(unknown)}")
    missing = [field for field in OBJECTIVE_FIELDS if not objective.get(field)]
    if missing:
        raise ModelError(f"objective fields missing: {missing}")
    validate_enum("evidence_standard", objective["evidence_standard"], C.GRADES)
    return objective


def objective_core(objective: dict[str, Any]) -> dict[str, Any]:
    """The exact six fields whose digest is the objective commitment."""

    return {field: objective[field] for field in OBJECTIVE_FIELDS}


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------
RECORD_REQUIRED = ("schema", "record_id", "kind", "title", "body", "created_at")


def make_record(*, kind: str, title: str, body: dict[str, Any], record_id: str | None = None) -> dict[str, Any]:
    validate_enum("record kind", kind, C.RECORD_KINDS)
    record = {
        "schema": C.SCHEMA_RECORD,
        "record_id": record_id or new_id("RC"),
        "kind": kind,
        "title": short(title),
        "body": body,
        "created_at": utc_now(),
    }
    validate_record(record)
    return record


def validate_record(record: Any) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ModelError("record must be an object")
    missing = [field for field in RECORD_REQUIRED if field not in record]
    if missing:
        raise ModelError(f"record missing fields: {sorted(missing)}")
    if record["schema"] != C.SCHEMA_RECORD:
        raise ModelError(f"unsupported record schema: {record['schema']!r}")
    validate_enum("record kind", record["kind"], C.RECORD_KINDS)
    if not isinstance(record["record_id"], str) or not record["record_id"]:
        raise ModelError("record_id must be nonempty")
    if not isinstance(record["title"], str) or not record["title"].strip():
        raise ModelError("record title must be nonempty")
    if not isinstance(record["body"], dict):
        raise ModelError("record body must be an object")
    return record


# --------------------------------------------------------------------------
# Assessment. Before confirmation it carries only closed values, so it has no
# display path for prose.
# --------------------------------------------------------------------------
ASSESSMENT_FIELDS = ("scope", "status", "issue_codes", "execution_revision", "authority_revision")


def make_assessment(
    *,
    scope: str,
    status: str,
    issue_codes: list[str],
    execution_revision: str,
    authority_revision: str,
    assessment_id: str | None = None,
) -> dict[str, Any]:
    validate_enum("completion status", status, C.COMPLETION_STATUSES)
    for code in issue_codes:
        validate_enum("issue code", code, C.COMPLETION_ISSUE_CODES)
    if status != C.COMPLETION_NOT_COMPLETE and issue_codes:
        raise ModelError("a passing assessment must not carry blocking issue codes")
    if status == C.COMPLETION_NOT_COMPLETE and not issue_codes:
        raise ModelError(f"a blocked assessment must name at least one of {list(C.COMPLETION_ISSUE_CODES)}")
    return {
        "schema": C.SCHEMA_ASSESSMENT,
        "assessment_id": assessment_id or new_id("AS"),
        "scope": scope,
        "status": status,
        "issue_codes": sorted(issue_codes),
        "execution_revision": execution_revision,
        "authority_revision": authority_revision,
        "created_at": utc_now(),
    }


def assessment_is_current(assessment: dict[str, Any], *, execution_revision: str, authority_revision: str) -> bool:
    """A passing assessment is only usable while both revisions are unchanged."""

    return (
        assessment.get("execution_revision") == execution_revision
        and assessment.get("authority_revision") == authority_revision
    )


# --------------------------------------------------------------------------
# Route portfolio
# --------------------------------------------------------------------------
ROUTE_FIELDS = ("route_id", "label_code", "axes", "hypothesis", "falsifier")


def validate_route(route: Any, known_labels: set[str]) -> dict[str, Any]:
    if not isinstance(route, dict):
        raise ModelError("route must be an object")
    unknown = set(route) - set(ROUTE_FIELDS)
    if unknown:
        raise ModelError(f"unknown route fields: {sorted(unknown)}")
    missing = [field for field in ROUTE_FIELDS if not route.get(field)]
    if missing:
        raise ModelError(f"route fields missing: {missing}")
    axes = route["axes"]
    if not isinstance(axes, dict):
        raise ModelError("route axes must be an object")
    unknown_axes = set(axes) - set(C.ROUTE_AXES)
    if unknown_axes:
        raise ModelError(f"unknown route axes: {sorted(unknown_axes)}")
    for axis in C.ROUTE_AXES:
        if not axes.get(axis):
            raise ModelError(f"route must declare axis {axis!r}")
    label = route["label_code"]
    if label in known_labels:
        raise ModelError(f"{C.ERR_ROUTE_PORTFOLIO}: duplicate route label {label!r}")
    if not route["falsifier"]:
        raise ModelError("route must declare what would falsify it")
    return route


def route_signature(route: dict[str, Any]) -> tuple:
    return tuple(route["axes"][axis] for axis in C.ROUTE_AXES)


def validate_route_portfolio(routes: Any) -> list[dict[str, Any]]:
    """Exactly CARDINALITY_ROUTES routes, pairwise distinct on the full axis triple.

    Requiring the whole triple to differ is what stops a rename from counting
    as a new route.
    """

    if not isinstance(routes, list):
        raise ModelError("route_portfolio must be a list")
    if len(routes) != C.CARDINALITY_ROUTES:
        raise ModelError(
            f"{C.ERR_ROUTE_PORTFOLIO}: a window freezes exactly {C.CARDINALITY_ROUTES} routes, got {len(routes)}"
        )
    seen_labels: set[str] = set()
    validated: list[dict[str, Any]] = []
    for route in routes:
        validated.append(validate_route(route, seen_labels))
        seen_labels.add(route["label_code"])
    signatures = [route_signature(route) for route in validated]
    if len(set(signatures)) != len(signatures):
        raise ModelError(
            f"{C.ERR_ROUTE_PORTFOLIO}: routes must differ on all of {list(C.ROUTE_AXES)}"
        )
    return validated
