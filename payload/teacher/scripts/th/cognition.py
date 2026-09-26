"""Core cognition, maintained by this Skill rather than typed by anyone.

Every packet the model receives carries a compact picture of what the project
already knows, derived from the store at the moment the packet is built:

    objective              the bound objective, or the current reading of the need
    acceptance_standard    what "finished" means here
    method_sources         sources that archived claims rest on
    verified_backbone      archived claims, strongest evidence first
    bottleneck_causality   open questions recorded as unknown
    route_rationale        why the current reading was chosen
    evidence_boundary      what the archived claims do *not* imply
    uninstantiated_objects checks proposed but not yet run
    retrieval_triggers     terms that should send the model back to the library
    reset_conditions       what would invalidate this picture

It is rendered through the three reading layers, so the densest layer that fits
the budget is used, the ten protected fields survive every layer, and an
overflow is reported but never stops work. Because it is recomputed from the
store, it cannot drift from the library: nobody has to remember to update it.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any, Mapping

from . import constants as C
from .layers import select_layer
from .store import Store

EMPTY = "（暂无）"
PACKET_TARGET_TOKENS = 1200
PACKET_CEILING_TOKENS = 1600


def _archived(store: Store) -> list[dict[str, Any]]:
    return store.candidates(state="archived")


def derive(store: Store, *, frame: Mapping[str, Any] | None = None) -> dict[str, Any]:
    archived = _archived(store)
    claims = [item for item in archived if item["kind"] == "claim"]
    claims.sort(key=lambda item: (C.GRADE_RANK.get(item.get("grade") or "", 9), -item["score"]))
    sources = [item for item in archived if item["kind"] == "source"]
    questions = [item for item in store.candidates() if item["kind"] == "open_question" and item["state"] != "declined"]

    objectives = store.records(kind=C.KIND_OBJECTIVE)
    objective_text = EMPTY
    acceptance = "每条要点写明依据、证据等级与不能推出什么；未知项必须列出。"
    if objectives:
        bound = objectives[-1]["body"].get("objective", {})
        objective_text = f"{bound.get('statement', '')}｜范围：{bound.get('claim_scope', '')}"
        acceptance = str(bound.get("completion_standard") or acceptance)
    elif frame is not None:
        reading = next((item for item in frame.get("interpretations", []) if item.get("id") == frame.get("chosen")), None)
        objective_text = reading["text"] if reading else frame.get("brief", EMPTY)[:160]

    rationale = EMPTY
    if frame is not None and frame.get("kind"):
        rationale = f"请求类型 {frame.get('kind')}，卡点 {frame.get('stuck_point')}，深度 {frame.get('depth')}"

    checks: list[str] = []
    for ask in store.asks(limit=10):
        for check in ask.get("answer", {}).get("next_checks", []):
            checks.append(check["text"])

    words: Counter[str] = Counter()
    for item in claims + questions:
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_\-]{3,}", item.get("text", "")):
            words[token.casefold()] += 1
    for item in sources:
        heading = str(item.get("locator", "")).rsplit("#", 1)[-1]
        if heading and len(heading) <= 40:
            words[heading] += 2

    return {
        "objective": objective_text or EMPTY,
        "acceptance_standard": acceptance,
        "method_sources": [item["locator"] for item in sources[:6]] or EMPTY,
        "verified_backbone": [
            f"{item['text']}〔{item.get('grade') or item.get('basis')}〕" for item in claims[:6]
        ] or EMPTY,
        "bottleneck_causality": [item["text"] for item in questions[:4]] or EMPTY,
        "route_rationale": rationale,
        "evidence_boundary": [item["cannot_imply"] for item in claims[:6] if item.get("cannot_imply")] or EMPTY,
        "uninstantiated_objects": checks[:4] or EMPTY,
        "retrieval_triggers": [word for word, _ in words.most_common(8)] or EMPTY,
        "reset_conditions": "目标的六个构成字段任一改变；已归档结论的依赖发生漂移；来源被撤回或无法再读取。",
        "history_narrative": [f"已归档 {len(archived)} 项，其中结论 {len(claims)} 项"],
        "expandable_detail": [item["text"] for item in claims[6:12]],
    }


def current_cognition(store: Store, *, frame: Mapping[str, Any] | None = None,
                      target_tokens: int = PACKET_TARGET_TOKENS,
                      ceiling_tokens: int = PACKET_CEILING_TOKENS) -> dict[str, Any]:
    cognition = derive(store, frame=frame)
    cognition = {key: value for key, value in cognition.items() if value}
    return select_layer(cognition, target_tokens=target_tokens, ceiling_tokens=ceiling_tokens)


def as_json(store: Store) -> str:
    return json.dumps(derive(store), ensure_ascii=False, indent=2)
