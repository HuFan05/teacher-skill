"""Teacher Skill: a local, control-boundary Skill for CS/AI research.

Public surface:

    Store      durable state; two heads, compare-and-swap commits
    Engine     control flow; the only path that can advance authority
    Renderer   deterministic renderer; the only writer of visible text
    Guard      independent checks; deterministic first, model may only veto

The package names no model vendor. A backend satisfying
`backend.StructuredBackend` is injected at construction.
"""

from . import constants as C
from .assets import (
    apply_index_plan,
    build_context_packet,
    detect_stale,
    estimate_tokens,
    locate_by_content,
    make_index_plan,
    read_packet,
)
from .backend import (
    BackendError,
    BackendTimeout,
    BackendUnavailable,
    ScriptedBackend,
    StructuredBackend,
    SubprocessBackend,
    UnavailableBackend,
    empty_patch,
    make_draft,
    validate_draft,
    validate_guard_verdict,
    validate_patch,
)
from .acceptance import (
    MAX_ROUNDS,
    AcceptanceError,
    AcceptanceGate,
    classify,
    deterministic_receipt,
    inventory,
    verifier_receipt,
)
from .broker import (
    OPERATION_ARGUMENTS,
    OPERATIONS,
    Broker,
    OperationRefused,
    OperationRequest,
    confine,
    validate_request,
)
from .coverage import (
    DISPOSITIONS,
    check as check_coverage,
    decisions_template,
    make_plan as make_coverage_plan,
    require_complete,
    validate_plan as validate_coverage_plan,
)
from .engine import Engine, EngineError, gate_input
from .guard import DeterministicGuard, Guard, GuardDenied, ModelGuard
from .layers import (
    DEFAULT_CEILING_TOKENS,
    DEFAULT_TARGET_TOKENS,
    LAYERS,
    PROTECTED_FIELDS,
    compression_gain,
    select_layer,
    validate_cognition,
)
from .maintenance import (
    ACTIONS as MAINTENANCE_ACTIONS,
    apply_decisions as apply_maintenance,
    coalesce_events,
    dependency_drift,
    dependency_revisions,
    mechanical_impact,
)
from .model import (
    ModelError,
    canonical_bytes,
    digest,
    digest_bytes,
    make_assessment,
    make_record,
    objective_core,
    utc_now,
    validate_claim,
    validate_objective,
    validate_route_portfolio,
    validate_scope,
)
from .render import Renderer, RenderError
from .session import Session
from .store import Store, StoreError

__all__ = [
    "C",
    "Store",
    "StoreError",
    "Engine",
    "EngineError",
    "gate_input",
    "Session",
    "Broker",
    "OperationRefused",
    "OperationRequest",
    "OPERATIONS",
    "OPERATION_ARGUMENTS",
    "validate_request",
    "confine",
    "AcceptanceGate",
    "AcceptanceError",
    "MAX_ROUNDS",
    "classify",
    "inventory",
    "deterministic_receipt",
    "verifier_receipt",
    "check_coverage",
    "make_coverage_plan",
    "validate_coverage_plan",
    "decisions_template",
    "require_complete",
    "DISPOSITIONS",
    "select_layer",
    "validate_cognition",
    "compression_gain",
    "LAYERS",
    "PROTECTED_FIELDS",
    "DEFAULT_TARGET_TOKENS",
    "DEFAULT_CEILING_TOKENS",
    "mechanical_impact",
    "dependency_drift",
    "dependency_revisions",
    "coalesce_events",
    "apply_maintenance",
    "MAINTENANCE_ACTIONS",
    "Renderer",
    "RenderError",
    "Guard",
    "GuardDenied",
    "ModelGuard",
    "DeterministicGuard",
    "ScriptedBackend",
    "SubprocessBackend",
    "UnavailableBackend",
    "StructuredBackend",
    "BackendError",
    "BackendTimeout",
    "BackendUnavailable",
    "make_draft",
    "empty_patch",
    "validate_draft",
    "validate_patch",
    "validate_guard_verdict",
    "ModelError",
    "canonical_bytes",
    "digest",
    "digest_bytes",
    "make_record",
    "make_assessment",
    "objective_core",
    "utc_now",
    "validate_claim",
    "validate_objective",
    "validate_scope",
    "validate_route_portfolio",
    "make_index_plan",
    "apply_index_plan",
    "detect_stale",
    "locate_by_content",
    "build_context_packet",
    "read_packet",
    "estimate_tokens",
]

__version__ = C.VERSION
