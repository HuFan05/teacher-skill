"""Closed vocabularies for the Teacher Skill.

This module is the single source of truth for every enum, closed key set and
fixed code this Skill accepts. Nothing outside this file may introduce a new
state, relation, grade or risk code: the validators reject unknown values, and
the deterministic renderer only understands what is declared here.

Design rule: the model's output channel is a bounded structure. Free text is
never a rendered surface. Therefore every field the model may influence is
either an enum, a validated identifier, a boolean, a bounded count, or a
byte-verified quote. Workflow, checking and stage decisions are never the
model's to make: they live in this package as deterministic rules.
"""

from __future__ import annotations

VERSION = "1.0.0"

SCHEMA_META = "th-meta/v1"
SCHEMA_HEAD = "th-head/v1"
SCHEMA_SNAPSHOT = "th-snapshot/v1"
SCHEMA_OPERATION = "th-operation/v1"
SCHEMA_EVENT = "th-event/v1"
SCHEMA_RECORD = "th-record/v1"
SCHEMA_ASSET = "th-asset/v1"
SCHEMA_ASSESSMENT = "th-assessment/v1"
SCHEMA_RECEIPT = "th-receipt/v1"
SCHEMA_RENDER = "th-render/v1"
SCHEMA_DRAFT = "th-draft/v1"

# --------------------------------------------------------------------------
# Two heads. Authority and execution advance under different gates.
#   execution: window/attempt/checkpoint/queue/session progress.
#   authority: accepted claims, evidence registry, route review, research map.
# A checkpoint or an attempt ending may advance execution only.
# --------------------------------------------------------------------------
HEAD_AUTHORITY = "authority"
HEAD_EXECUTION = "execution"
HEADS = (HEAD_AUTHORITY, HEAD_EXECUTION)

# --------------------------------------------------------------------------
# Evidence grades, strongest first. The name of a grade fixes how strong a
# statement it may support; a grade never implies a stronger one.
# --------------------------------------------------------------------------
GRADE_FORMAL = "formal"
GRADE_CERTIFICATE = "certificate"
GRADE_EXACT_REPRODUCTION = "exact_reproduction"
GRADE_BOUNDED_EMPIRICAL = "bounded_empirical"
GRADE_NUMERICAL = "numerical_evidence"

GRADES = (
    GRADE_FORMAL,
    GRADE_CERTIFICATE,
    GRADE_EXACT_REPRODUCTION,
    GRADE_BOUNDED_EMPIRICAL,
    GRADE_NUMERICAL,
)
GRADE_RANK = {grade: index for index, grade in enumerate(GRADES)}

# --------------------------------------------------------------------------
# Claim strength. A universal claim needs formal verification or a checkable
# certificate; a bounded claim needs a declared bound.
# A bounded claim needs a declared bound. Numerical evidence supports an
# observation, never a claim.
# --------------------------------------------------------------------------
STRENGTH_UNIVERSAL = "universal"
STRENGTH_CONDITIONAL = "conditional"
STRENGTH_BOUNDED = "bounded"
STRENGTH_OBSERVATION = "observation"

STRENGTHS = (
    STRENGTH_UNIVERSAL,
    STRENGTH_CONDITIONAL,
    STRENGTH_BOUNDED,
    STRENGTH_OBSERVATION,
)

# Minimum grade required per claimed strength. This table is the rule the
# guard enforces; it is deliberately conservative.
MINIMUM_GRADE_BY_STRENGTH = {
    STRENGTH_UNIVERSAL: (GRADE_FORMAL, GRADE_CERTIFICATE),
    STRENGTH_CONDITIONAL: (GRADE_FORMAL, GRADE_CERTIFICATE),
    STRENGTH_BOUNDED: (GRADE_FORMAL, GRADE_CERTIFICATE, GRADE_EXACT_REPRODUCTION,
                       GRADE_BOUNDED_EMPIRICAL),
    STRENGTH_OBSERVATION: GRADES,
}

# --------------------------------------------------------------------------
# Record kinds.
# --------------------------------------------------------------------------
KIND_OBJECTIVE = "objective"
KIND_ATTEMPT = "attempt"
KIND_CLAIM = "claim"
KIND_OBSERVATION = "observation"
KIND_FAILURE = "failure"
KIND_CORRECTION = "correction"
KIND_METHOD = "method"
KIND_ARTIFACT = "artifact"
KIND_ASSET = "asset_note"
KIND_REVIEW = "review"

RECORD_KINDS = (
    KIND_OBJECTIVE,
    KIND_ATTEMPT,
    KIND_CLAIM,
    KIND_OBSERVATION,
    KIND_FAILURE,
    KIND_CORRECTION,
    KIND_METHOD,
    KIND_ARTIFACT,
    KIND_ASSET,
    KIND_REVIEW,
)

# --------------------------------------------------------------------------
# Relations allowed between records. Only the strong three propagate impact.
# --------------------------------------------------------------------------
REL_PREMISE = "premise"
REL_INPUT = "input"
REL_SUPPORTS = "supports"
REL_EXTENDS = "extends"
REL_BACKGROUND = "background"
REL_CORRECTS = "corrects"

RELATIONS = (REL_PREMISE, REL_INPUT, REL_SUPPORTS, REL_EXTENDS, REL_BACKGROUND, REL_CORRECTS)
STRONG_RELATIONS = (REL_PREMISE, REL_INPUT, REL_SUPPORTS)

# --------------------------------------------------------------------------
# Record effect, derived from review + dependency state. Never set by hand.
# --------------------------------------------------------------------------
EFFECT_CURRENT = "current"
EFFECT_HISTORICAL = "historical"
EFFECT_NEEDS_REVIEW = "needs_review"
EFFECT_INVALID = "invalid"

EFFECTS = (EFFECT_CURRENT, EFFECT_HISTORICAL, EFFECT_NEEDS_REVIEW, EFFECT_INVALID)

REVIEW_UNREVIEWED = "unreviewed"
REVIEW_INCONCLUSIVE = "inconclusive"
REVIEW_ACCEPTED = "accepted"
REVIEW_REJECTED = "rejected"

REVIEW_DECISIONS = (REVIEW_ACCEPTED, REVIEW_REJECTED)

# Who reviewed. Only these kinds count toward a review state; `self` and
# `foreign` are recorded and never counted.
REVIEWER_KINDS = ("local", "human", "independent_model", "declared_independent", "self", "foreign")
COUNTED_REVIEWERS = ("local", "human", "independent_model", "declared_independent")
AUTHENTICATED_REVIEWERS = ("local", "human", "independent_model")
REVIEW_STATES = (REVIEW_UNREVIEWED, REVIEW_INCONCLUSIVE, REVIEW_ACCEPTED, REVIEW_REJECTED)

# --------------------------------------------------------------------------
# Attempt outcome describes how an attempt ended, not whether the claim or the
# method is true.
# --------------------------------------------------------------------------
OUTCOME_CANDIDATE_FOUND = "candidate_found"
OUTCOME_NO_CANDIDATE = "no_candidate"
OUTCOME_INCONCLUSIVE = "inconclusive"
OUTCOME_AWAITING_INPUT = "awaiting_input"
OUTCOME_FAILED = "failed"

OUTCOMES = (
    OUTCOME_CANDIDATE_FOUND,
    OUTCOME_NO_CANDIDATE,
    OUTCOME_INCONCLUSIVE,
    OUTCOME_AWAITING_INPUT,
    OUTCOME_FAILED,
)

# --------------------------------------------------------------------------
# Attempt status.
# --------------------------------------------------------------------------
ATTEMPT_PREPARED = "prepared"
ATTEMPT_OPEN = "open"
ATTEMPT_CLOSED = "closed"

ATTEMPT_STATUSES = (ATTEMPT_PREPARED, ATTEMPT_OPEN, ATTEMPT_CLOSED)

# --------------------------------------------------------------------------
# Completion status. A local summary never triggers completion.
# --------------------------------------------------------------------------
COMPLETION_NOT_COMPLETE = "NOT_COMPLETE"
COMPLETION_ROUTE_READY = "ROUTE_READY"
COMPLETION_REPRODUCIBLE = "REPRODUCIBLE"
COMPLETION_VERIFIED = "VERIFIED"

COMPLETION_STATUSES = (
    COMPLETION_NOT_COMPLETE,
    COMPLETION_ROUTE_READY,
    COMPLETION_REPRODUCIBLE,
    COMPLETION_VERIFIED,
)

# Issue codes that block a completion claim, with the obligation each names.
ISSUE_NO_VERIFIED_CHECKPOINT = "NO_VERIFIED_CHECKPOINT"
ISSUE_NO_VERIFIED_CLAIM = "NO_VERIFIED_CLAIM"
ISSUE_DEPENDENCY_OPEN = "DEPENDENCY_OPEN"
ISSUE_CANNOT_IMPLY_MISSING = "CANNOT_IMPLY_MISSING"
ISSUE_GRADE_INSUFFICIENT = "GRADE_INSUFFICIENT"
ISSUE_SCOPE_UNDECLARED = "SCOPE_UNDECLARED"
ISSUE_REPRODUCTION_BLOCKED = "REPRODUCTION_BLOCKED"
ISSUE_UNREVIEWED_INSIGHT = "UNREVIEWED_INSIGHT"
ISSUE_ROUTE_PORTFOLIO_INVALID = "ROUTE_PORTFOLIO_INVALID"
ISSUE_ASSESSMENT_STALE = "ASSESSMENT_STALE"
ISSUE_COVERAGE_INCOMPLETE = "COVERAGE_INCOMPLETE"
# A consulted reviewer may veto. It may never turn a blocked completion into a
# passing one, so this code can only be added, never removed by a model.
ISSUE_REVIEW_DENIED = "REVIEW_DENIED"

COMPLETION_ISSUE_CODES = (
    ISSUE_NO_VERIFIED_CHECKPOINT,
    ISSUE_NO_VERIFIED_CLAIM,
    ISSUE_DEPENDENCY_OPEN,
    ISSUE_CANNOT_IMPLY_MISSING,
    ISSUE_GRADE_INSUFFICIENT,
    ISSUE_SCOPE_UNDECLARED,
    ISSUE_REPRODUCTION_BLOCKED,
    ISSUE_UNREVIEWED_INSIGHT,
    ISSUE_ROUTE_PORTFOLIO_INVALID,
    ISSUE_ASSESSMENT_STALE,
    ISSUE_COVERAGE_INCOMPLETE,
    ISSUE_REVIEW_DENIED,
)

# --------------------------------------------------------------------------
# Risk codes the guard may return. The guard can veto; it can never approve a
# surface, because it is not the writer.
# --------------------------------------------------------------------------
RISK_SCHEMA = "schema_risk"
RISK_EVIDENCE_INSUFFICIENT = "evidence_grade_insufficient"
RISK_CANNOT_IMPLY_MISSING = "cannot_imply_missing"
RISK_SCOPE_UNDECLARED = "scope_undeclared"
RISK_UNKNOWN_ANCHOR = "unknown_anchor"
RISK_FOREIGN_ANCHOR = "non_local_anchor"
RISK_NEW_OBJECT = "unregistered_object"
RISK_ASSESSMENT_INCONSISTENT = "assessment_inconsistent"
RISK_UNSUPPORTED_TRANSITION = "unsupported_transition"
RISK_UNTYPED_CLAIM = "claim_without_strength"

RISK_CODES = (
    RISK_SCHEMA,
    RISK_EVIDENCE_INSUFFICIENT,
    RISK_CANNOT_IMPLY_MISSING,
    RISK_SCOPE_UNDECLARED,
    RISK_UNKNOWN_ANCHOR,
    RISK_FOREIGN_ANCHOR,
    RISK_NEW_OBJECT,
    RISK_ASSESSMENT_INCONSISTENT,
    RISK_UNSUPPORTED_TRANSITION,
    RISK_UNTYPED_CLAIM,
    # Answer gate. Every one of these is decided by code, not by a model.
    "evidence_unresolved",
    "quote_unverified",
    "length_exceeded",
    "explanation_slots_missing",
    "unknowns_missing",
    "reasoning_overclaim",
    "investigation_minimum_unmet",
    "duplicate_points",
    "confidence_unsupported",
    "interpretation_invalid",
)

# --------------------------------------------------------------------------
# Route portfolio: exactly three routes that differ on all three axes.
# --------------------------------------------------------------------------
CARDINALITY_ROUTES = 3

AXIS_LEARNING_SIGNAL = "learning_signal"
AXIS_ARCHITECTURE = "architecture_family"
AXIS_COMPUTE = "compute_regime"

ROUTE_AXES = (AXIS_LEARNING_SIGNAL, AXIS_ARCHITECTURE, AXIS_COMPUTE)

# --------------------------------------------------------------------------
# Node kinds for the route graph.
# --------------------------------------------------------------------------
NODE_ROUTE = "route"
NODE_STEP = "step"
NODE_BOTTLENECK = "bottleneck"
NODE_OBSTACLE = "obstacle"
NODE_CHECKPOINT = "checkpoint"
NODE_CLAIM = "claim"
NODE_RESULT = "result"
NODE_ENVIRONMENT = "environment"

NODE_KINDS = (
    NODE_ROUTE, NODE_STEP, NODE_BOTTLENECK, NODE_OBSTACLE, NODE_CHECKPOINT,
    NODE_CLAIM, NODE_RESULT, NODE_ENVIRONMENT,
)

NODE_STATUSES = ("open", "resolved", "blocked", "abandoned")

# --------------------------------------------------------------------------
# Disclosure levels. A packet is bounded; protected task context is always
# carried in full or the packet is refused.
# --------------------------------------------------------------------------
LEVEL_INDEX = "index"
LEVEL_SYNOPSIS = "synopsis"
LEVEL_SECTION = "section"
LEVEL_EVIDENCE = "evidence"

LEVELS = (LEVEL_INDEX, LEVEL_SYNOPSIS, LEVEL_SECTION, LEVEL_EVIDENCE)
LEVEL_RANK = {level: index for index, level in enumerate(LEVELS)}

ACTOR_DEFAULTS = {
    "router": LEVEL_INDEX,
    "worker": LEVEL_SYNOPSIS,
    "verifier": LEVEL_SECTION,
    "curator": LEVEL_INDEX,
}
ACTORS = tuple(ACTOR_DEFAULTS)

# Levels a "small" read packet may use. Bounded by construction.
READ_PACKET_SMALL_MAX_RECORDS = 3
READ_PACKET_SMALL_MAX_SECTIONS = 3
READ_PACKET_SMALL_MAX_CHARS = 12000
READ_PACKET_DEFAULT_MAX_CHARS = 64000

# --------------------------------------------------------------------------
# Fixed refusal strings. These are the only user-visible text a failure may
# produce, so a failure can never leak a candidate.
# --------------------------------------------------------------------------
SAFE_FAILURE_RESPONSE = (
    "本轮反馈无法在安全边界内生成。请把当前步骤拆小一些，并写明所用依据。"
)
SAFE_POST_REVIEW_FAILURE = (
    "本轮复核无法在安全边界内生成。既有的权威记录保持不变。"
)
REFUSE_INPUT = "输入未通过结构门，本次不进入研究窗口。"
REFUSE_COMPLETION = "完成门未通过。以下是仍未闭合的义务。"

# --------------------------------------------------------------------------
# Error codes. Fixed strings, deliberately stable for tests and receipts.
# --------------------------------------------------------------------------
ERR_OPERATION_REUSED = "operation_reused"
ERR_STALE_SNAPSHOT = "stale_snapshot"
ERR_RECOVERY_CONFLICT = "recovery_conflict"
ERR_COMMIT_READBACK_FAILED = "commit_readback_failed"
ERR_UNKNOWN_HEAD = "unknown_head"
ERR_UNKNOWN_RECORD = "unknown_record"
ERR_UNKNOWN_ANCHOR = "unknown_anchor"
ERR_UNSUPPORTED_TRANSITION = "unsupported_transition"
ERR_INPUT_REJECTED = "input_rejected"
ERR_COMPLETION_BLOCKED = "completion_blocked"
ERR_EVIDENCE_INSUFFICIENT = "evidence_insufficient"
ERR_CANNOT_IMPLY_MISSING = "cannot_imply_missing"
ERR_SCOPE_UNDECLARED = "scope_undeclared"
ERR_GUARD_DENIED = "guard_denied"
ERR_BUDGET_EXCEEDED = "budget_exceeded"
ERR_PLAN_HASH = "plan_hash"
ERR_PLAN_STALE = "plan_stale"
ERR_RECEIPT_MISMATCH = "receipt_mismatch"
ERR_ROUTE_PORTFOLIO = "route_portfolio"
ERR_ASSET_UNMANAGED = "asset_unmanaged"
ERR_CONTINUITY_BINDING = "continuity_binding"

# --------------------------------------------------------------------------
# Requirement intake. This Skill decides which field is missing and what is
# asked; the model may only point at the requester's own words or name a value
# from a closed vocabulary. It never authors the question and never authors a
# value that reaches a surface.
# --------------------------------------------------------------------------
INTAKE_ORDER = (
    "statement",
    "domain",
    "claim_scope",
    "assumptions",
    "evidence_standard",
    "completion_standard",
)

INTAKE_DECISIONS = ("continue", "ready", "cannot_frame")

# Closed default assumptions this Skill may offer on the requester's behalf.
INTAKE_ASSUMPTIONS = (
    "fixed_compute_budget",
    "single_dataset",
    "fixed_seeds",
    "no_pretrained_weights",
    "fixed_hardware",
    "fixed_data_split",
    "fixed_training_steps",
    "no_external_data",
)

INTAKE_KEYS = (
    "decision",
    "field",
    "quote",
    "absent",
    "evidence_standard",
    "assumed_defaults",
    "blocking_code",
)

INTAKE_MAX_ROUNDS = 6
INTAKE_MAX_QUOTE_CHARS = 600
INTAKE_MAX_ASSUMPTIONS = 4

ERR_INTAKE_UNKNOWN = "intake_unknown"
ERR_INTAKE_FIELD_MISMATCH = "intake_field_mismatch"
ERR_INTAKE_ROUNDS_EXHAUSTED = "intake_rounds_exhausted"
ERR_INTAKE_INCOMPLETE = "intake_incomplete"
ERR_INTAKE_QUOTE_UNVERIFIED = "intake_quote_unverified"
ERR_INTAKE_PREMATURE_READY = "intake_premature_ready"


# --------------------------------------------------------------------------
# Need discovery. Every request passes through it before any answer is
# attempted. This Skill decides whether an ambiguity is material, whether
# investigation can settle it, and whether the requester is asked at all; the
# model may only classify with closed values and propose at most three short
# readings anchored in the requester's own words.
# --------------------------------------------------------------------------
REQUEST_KINDS = (
    "concept_explanation",
    "paper_understanding",
    "method_comparison",
    "experiment_debugging",
    "research_question",
    "reproduction",
    "literature_search",
    "implementation_help",
    "learning_path",
    "other",
)

# Kinds whose answer must rest on at least one retrieved source or an explicit
# record that every available source was tried and none held the material.
EVIDENCE_REQUIRED_KINDS = (
    "paper_understanding",
    "method_comparison",
    "research_question",
    "reproduction",
    "literature_search",
)

# Kinds whose answer must dissect its key steps: motivation, boundary of
# applicability, and what to do when the step fails.
EXPLANATION_REQUIRED_KINDS = (
    "concept_explanation",
    "method_comparison",
    "paper_understanding",
    "learning_path",
)

# Where the requester is stuck. The feedback is aimed at this category.
STUCK_POINTS = (
    "concept",
    "assumption_condition",
    "method_choice",
    "implementation",
    "experiment_computation",
    "source_location",
    "unclear",
)

CLARITY_LEVELS = ("clear", "ambiguous", "underspecified")
DIFFERS_BY = ("goal", "object", "scope", "depth", "output_form")
DEPTHS = ("brief", "standard", "deep")

# Closed defaults this Skill may adopt instead of asking. Each maps to fixed
# text in the renderer, so adopting one introduces no model-authored prose.
NEED_DEFAULTS = (
    "standard_definitions",
    "mainstream_current_practice",
    "single_gpu_budget",
    "python_pytorch_stack",
    "no_prior_context",
    "user_notes_relevant",
    "answer_in_chinese",
    "depth_standard",
)

FRAME_KEYS = (
    "kind",
    "stuck_point",
    "clarity",
    "depth",
    "interpretations",
    "material_ambiguity",
    "investigation_can_resolve",
    "defaults",
)
INTERPRETATION_KEYS = ("text", "quote", "differs_by")
MAX_INTERPRETATIONS = 3
MAX_INTERPRETATION_CHARS = 80
MAX_CLARIFICATIONS_PER_FRAME = 1
MAX_REFRAMES = 1

# --------------------------------------------------------------------------
# Investigation. The model asks for operations from a closed list; this Skill
# decides, executes and counts. A budget is a measurement, never a reason to
# fabricate: exhausting it is reported in the answer.
# --------------------------------------------------------------------------
PLAN_ACTIONS = ("request_operations", "ready_to_answer", "cannot_answer")
PLAN_KEYS = ("action", "operations", "reason_code")
PLAN_REASON_CODES = (
    "need_source",
    "need_detail",
    "need_check",
    "enough_evidence",
    "no_source_available",
    "out_of_scope",
)
STEP_BUDGET = {"brief": 2, "standard": 4, "deep": 8}
OPS_PER_STEP = 3
EXCERPT_CHAR_BUDGET = {"brief": 12000, "standard": 12000, "deep": 24000}
MAX_EXCERPT_CHARS = 3000
MAX_CANDIDATES = 8

# --------------------------------------------------------------------------
# Answer gate.
# --------------------------------------------------------------------------
BASES = ("retrieved_source", "executed_check", "user_supplied", "reasoning")
ANSWER_KEYS = (
    "conclusion",
    "points",
    "explanation",
    "unknowns",
    "next_checks",
    "followups",
    "confidence",
)
POINT_KEYS = ("text", "strength", "basis", "grade", "evidence", "quote", "cannot_imply")
EXPLANATION_KEYS = ("step", "motivation", "boundary", "alternative")
CHECK_KEYS = ("text", "operation")
FOLLOWUP_KEYS = ("text", "kind")
CONFIDENCE_LEVELS = ("high", "medium", "low")

LIMIT_CONCLUSION = 240
LIMIT_POINT = 160
LIMIT_SLOT = 160
LIMIT_SHORT = 120
LIMIT_FOLLOWUP = 60
MAX_POINTS = 5
MAX_EXPLANATION = 4
MAX_UNKNOWNS = 4
MAX_NEXT_CHECKS = 3
MAX_FOLLOWUPS = 2
MAX_QUOTE_IN_ANSWER = 300

# --------------------------------------------------------------------------
# Assets. Pending material stays outside the formal library until the
# requester accepts a proposal; mechanical upkeep never waits for anyone.
# --------------------------------------------------------------------------
CANDIDATE_KINDS = ("claim", "failure", "source", "open_question")
ARCHIVE_CHOICES = ("verified_only", "all", "none")
CANDIDATE_STATES = ("pending", "archived", "declined")


# Fixed repair guidance, one sentence per risk code. It restates the rule that
# was broken; it never says anything about the rejected content.
REPAIR_GUIDANCE = {
    "schema_risk": "Use exactly the declared keys and only values from the declared vocabularies.",
    "evidence_unresolved": "Cite only evidence ids present in the packet, of the kind the basis requires; reasoning cites none.",
    "quote_unverified": "A quote must be copied byte for byte from a cited excerpt (or the request, for user_supplied); otherwise use null.",
    "length_exceeded": "Shorten the flagged field to its declared limit.",
    "explanation_slots_missing": "Each explanation entry needs non-empty step, motivation, boundary and alternative.",
    "unknowns_missing": "Declare at least one unknown when any point is reasoning-only, sources were missing, or the budget ran out.",
    "reasoning_overclaim": "A point with basis reasoning must have grade null and a strength other than universal.",
    "investigation_minimum_unmet": "This kind of request needs at least one retrieved source; cite it or state the gap in unknowns.",
    "duplicate_points": "Merge points that say the same thing.",
    "confidence_unsupported": "High confidence needs at least one point with retrieved or executed evidence; otherwise lower it.",
    "evidence_grade_insufficient": "Choose a grade the strength table allows for that strength, or weaken the strength; a retrieved source can never carry formal or certificate, so state what the source says as an observation or a bounded claim.",
    "cannot_imply_missing": "Every point that is not an observation needs a cannot_imply statement.",
    "claim_without_strength": "A universal or bounded point that rests on evidence needs a grade.",
    "interpretation_invalid": "Give one to three distinct readings of at most 80 characters, one line each, without links.",
}
