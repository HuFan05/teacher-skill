"""Bounded reading layers.

Every long-running project hits one tension: exploration wants everything,
answering wants almost nothing. The resolution here is not to pick one. The same
structured cognition is rendered at three compression levels, a reading level is
selected by budget, and the choice is recorded in a receipt.

    normal        the full cognition
    compact       repetition, history expansion and link-followable detail dropped
    minimal_safe  everything droppable dropped

Compression may remove repetition, historical narrative and anything a reader
could follow a reference to. Compression may **never** remove a protected field.
If the protected fields alone exceed the ceiling, the attempt still runs: it does
not pause, does not change route, does not count as a failure and does not send
the user back to fix bookkeeping. Only a broken binding blocks work.

That last rule is the one most systems get wrong. A token overflow is a
*measurement*, and letting a measurement stop research is how bookkeeping starts
costing more than the work.

This module counts with a deterministic byte-based estimate rather than a real
tokenizer, because the package takes no dependency. The receipt therefore records
which estimator produced the number, and two counts are only comparable when
they come from the same estimator.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .model import digest

# --------------------------------------------------------------------------
# Layers
# --------------------------------------------------------------------------
LAYER_NORMAL = "normal"
LAYER_COMPACT = "compact"
LAYER_MINIMAL = "minimal_safe"

LAYERS = (LAYER_NORMAL, LAYER_COMPACT, LAYER_MINIMAL)
LAYER_RANK = {layer: index for index, layer in enumerate(LAYERS)}

DEFAULT_TARGET_TOKENS = 7000
DEFAULT_CEILING_TOKENS = 8192

ESTIMATOR = "deterministic_byte_estimate_v1"

# --------------------------------------------------------------------------
# Protected fields. These survive every layer. The list is a statement of what
# compression may never remove: an objective is an objective, a bottleneck is a
# bottleneck, an evidence boundary is an evidence boundary.
# --------------------------------------------------------------------------
FIELD_OBJECTIVE = "objective"
FIELD_ACCEPTANCE = "acceptance_standard"
FIELD_SOURCES = "method_sources"
FIELD_BACKBONE = "verified_backbone"
FIELD_BOTTLENECK = "bottleneck_causality"
FIELD_ROUTE = "route_rationale"
FIELD_BOUNDARY = "evidence_boundary"
FIELD_UNINSTANTIATED = "uninstantiated_objects"
FIELD_TRIGGERS = "retrieval_triggers"
FIELD_RESET = "reset_conditions"

PROTECTED_FIELDS = (
    FIELD_OBJECTIVE,
    FIELD_ACCEPTANCE,
    FIELD_SOURCES,
    FIELD_BACKBONE,
    FIELD_BOTTLENECK,
    FIELD_ROUTE,
    FIELD_BOUNDARY,
    FIELD_UNINSTANTIATED,
    FIELD_TRIGGERS,
    FIELD_RESET,
)

# Reducible at `compact`, and the first things to go at `minimal_safe`.
DROPPABLE_FIELDS = (
    "repetition",
    "history_narrative",
    "expandable_detail",
    "tried_and_abandoned",
    "decorative_examples",
)

FIELD_LABEL = {
    FIELD_OBJECTIVE: "目标",
    FIELD_ACCEPTANCE: "验收标准",
    FIELD_SOURCES: "方法来源",
    FIELD_BACKBONE: "已核验主干",
    FIELD_BOTTLENECK: "瓶颈因果",
    FIELD_ROUTE: "选路理由",
    FIELD_BOUNDARY: "证据边界",
    FIELD_UNINSTANTIATED: "尚未实例化",
    FIELD_TRIGGERS: "检索触发",
    FIELD_RESET: "重置条件",
    "repetition": "重复说明",
    "history_narrative": "历史",
    "expandable_detail": "可沿链接展开的细节",
    "tried_and_abandoned": "试过并放弃",
    "decorative_examples": "示例",
}


class LayerError(ValueError):
    pass


def estimate_tokens(value: Any) -> int:
    import json

    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return max(1, (len(value.encode("utf-8")) + 2) // 3)


# --------------------------------------------------------------------------
# Cognition
# --------------------------------------------------------------------------
def validate_cognition(cognition: Any) -> dict[str, Any]:
    """A cognition must carry every protected field, non-empty.

    This is what stops a layer from being the place where an inconvenient
    obligation quietly disappears.
    """

    if not isinstance(cognition, dict):
        raise LayerError("cognition must be an object")
    allowed = set(PROTECTED_FIELDS) | set(DROPPABLE_FIELDS)
    unknown = set(cognition) - allowed
    if unknown:
        raise LayerError(f"cognition carries undeclared fields: {sorted(unknown)}")
    missing = [field for field in PROTECTED_FIELDS if not cognition.get(field)]
    if missing:
        raise LayerError(f"cognition is missing protected fields: {sorted(missing)}")
    for field in PROTECTED_FIELDS:
        value = cognition[field]
        if isinstance(value, str):
            continue
        if isinstance(value, (list, tuple)) and value and all(str(item).strip() for item in value):
            continue
        raise LayerError(f"protected field {field!r} must be a non-empty string or list")
    return cognition


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------
# Per-layer policy for the droppable fields. The table is the design: `compact`
# must be strictly smaller than `normal` and strictly larger than
# `minimal_safe`, so the three names describe three behaviours rather than two.
# A layer name longer than another must never make a smaller layer bigger, which
# is why the layer is metadata and does not appear in the rendered text.
DROP = "drop"
TRUNCATE = "truncate"
KEEP = "keep"

TRUNCATE_CHARS = 120

LAYER_POLICY: dict[str, dict[str, str]] = {
    LAYER_NORMAL: {field: KEEP for field in DROPPABLE_FIELDS},
    LAYER_COMPACT: {
        "repetition": TRUNCATE,
        "tried_and_abandoned": TRUNCATE,
        "decorative_examples": TRUNCATE,
        "history_narrative": DROP,
        "expandable_detail": DROP,
    },
    LAYER_MINIMAL: {field: DROP for field in DROPPABLE_FIELDS},
}


def _flatten(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return "；".join(str(item) for item in value)
    return str(value)


def _render(cognition: Mapping[str, Any], layer: str) -> str:
    """Render the cognition at one layer.

    The layer name is deliberately absent from the text: it belongs in the
    receipt. Putting it in the body would let a longer name make a denser layer
    larger, which would break the ordering the selection relies on.
    """

    lines: list[str] = []
    for field in PROTECTED_FIELDS:
        lines.append(f"- {FIELD_LABEL[field]}：{_flatten(cognition[field])}")
    policy = LAYER_POLICY[layer]
    for field in DROPPABLE_FIELDS:
        value = cognition.get(field)
        if not value:
            continue
        decision = policy[field]
        if decision == DROP:
            continue
        body = _flatten(value)
        if decision == TRUNCATE and len(body) > TRUNCATE_CHARS:
            body = body[:TRUNCATE_CHARS] + "…"
        lines.append(f"- {FIELD_LABEL.get(field, field)}：{body}")
    return "\n".join(lines)


def make_receipt(
    *,
    layer: str,
    text: str,
    target: int,
    ceiling: int,
    dropped: Sequence[str],
    overflow: bool,
) -> dict[str, Any]:
    return {
        "schema": "th-reading-receipt/v1",
        "layer": layer,
        "level": LAYER_RANK[layer],
        "estimator": ESTIMATOR,
        "token_count": estimate_tokens(text),
        "target_tokens": target,
        "ceiling_tokens": ceiling,
        "text_sha256": digest(text),
        "dropped_fields": sorted(dropped),
        "protected_fields_present": list(PROTECTED_FIELDS),
        "overflow": overflow,
        # The rule that keeps bookkeeping from stopping research.
        "continues": True,
        "may_pause_work": False,
        "reason_for_continuing": (
            "a token count is a measurement; only a broken objective, evidence, "
            "route or hash binding may block an authorised attempt"
        ),
    }


def select_layer(
    cognition: dict[str, Any],
    *,
    target_tokens: int | None = DEFAULT_TARGET_TOKENS,
    ceiling_tokens: int = DEFAULT_CEILING_TOKENS,
    preferred: str | None = None,
) -> dict[str, Any]:
    """Pick the densest layer that fits, and never fail because none fits.

    Returns the rendered text, the chosen layer and a receipt. When even
    `minimal_safe` exceeds the ceiling the receipt records `overflow: True` and
    the text is still returned: the caller proceeds.

    `target_tokens=None` means "no separate target"; otherwise a target above the
    ceiling is incoherent and is refused. Passing only a ceiling is allowed, and
    the target is clamped to it.
    """

    validate_cognition(cognition)
    if ceiling_tokens < 1:
        raise LayerError("the ceiling must be positive")
    if target_tokens is None:
        effective_target = ceiling_tokens
    else:
        if target_tokens < 1:
            raise LayerError("token budgets must be positive")
        effective_target = min(target_tokens, ceiling_tokens)
    if preferred is not None and preferred not in LAYERS:
        raise LayerError(f"unknown layer {preferred!r}")

    order = LAYERS if preferred is None else (preferred,)
    for layer in order:
        text = _render(cognition, layer)
        count = estimate_tokens(text)
        if count <= ceiling_tokens:
            dropped = [field for field in DROPPABLE_FIELDS if cognition.get(field)]
            if layer == LAYER_MINIMAL:
                dropped = list(DROPPABLE_FIELDS)
            return {
                "layer": layer,
                "text": text,
                "receipt": make_receipt(
                    layer=layer,
                    text=text,
                    target=effective_target,
                    ceiling=ceiling_tokens,
                    dropped=dropped,
                    overflow=False,
                ),
            }

    # Nothing fit. Emit the smallest layer anyway and keep going.
    text = _render(cognition, LAYER_MINIMAL)
    return {
        "layer": LAYER_MINIMAL,
        "text": text,
        "receipt": make_receipt(
            layer=LAYER_MINIMAL,
            text=text,
            target=effective_target,
            ceiling=ceiling_tokens,
            dropped=list(DROPPABLE_FIELDS),
            overflow=True,
        ),
    }


def compression_gain(cognition: dict[str, Any]) -> dict[str, Any]:
    """What each layer costs, for diagnostics and for budget tuning."""

    sizes = {}
    for layer in LAYERS:
        sizes[layer] = estimate_tokens(_render(cognition, layer))
    return {
        "schema": "th-layer-sizes/v1",
        "estimator": ESTIMATOR,
        "sizes": sizes,
        "protected_fields": list(PROTECTED_FIELDS),
        "note": "only droppable fields are removed by compression",
    }
