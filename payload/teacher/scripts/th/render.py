"""Deterministic renderer: the only writer of user-visible text.

Every other module may only *propose*. This module turns a validated draft plus
validated store state into text, and it accepts nothing else:

  * enums from `constants` / `backend`, looked up in a fixed phrase table;
  * identifiers that the store has already confirmed exist;
  * booleans and bounded integers;
  * quotes that the engine verified byte-for-byte against caller-supplied text.

There is deliberately no `render(text)` entry point. If a value is not in this
module's vocabulary the renderer prints its *code*, never a free string, so a
model cannot place a sentence on a user-visible surface.

`render_*` is pure: identical inputs produce identical bytes.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from . import constants as C
from .backend import (
    BLOCKING_CODES,
    CERTAINTY,
    DECISIONS,
    ROUTE_PROGRESS,
    SCOPES,
    validate_draft,
    validate_label_code,
)

SCHEMA_RENDER = C.SCHEMA_RENDER

# --------------------------------------------------------------------------
# Fixed phrase tables. Nothing outside these tables reaches the screen as prose.
# --------------------------------------------------------------------------
DECISION_PHRASE = {
    "proceed": "按既定路线继续",
    "blocked": "被阻断",
    "needs_input": "需要你的一个决定",
}

SCOPE_PHRASE = {
    "objective": "范围：目标命题",
    "method": "范围：方法",
    "evidence": "范围：证据",
    "route": "范围：路线",
    "off_topic": "范围：与当前目标无关",
    "safety": "范围：安全事项",
}

PROGRESS_PHRASE = {
    "progressed": "路线推进：有进展",
    "stalled": "路线推进：停滞",
    "unclear": "路线推进：不明",
    "not_applicable": "路线推进：不适用",
}

CERTAINTY_PHRASE = {
    "high": "把握：高",
    "medium": "把握：中",
    "low": "把握：低",
}

BLOCKING_PHRASE = {
    "insufficient_context": "缺少足够上下文。",
    "objective_conflict": "目标之间存在实质冲突。",
    "resource_unavailable": "所需资源不可用。",
    "reproduction_blocked": "复现被阻断：环境或数据缺失。",
    "evidence_unavailable": "证据不可获得。",
    "out_of_scope": "超出当前授权范围。",
}

GRADE_PHRASE = {
    C.GRADE_FORMAL: "形式化验证",
    C.GRADE_CERTIFICATE: "可检查证书",
    C.GRADE_EXACT_REPRODUCTION: "精确复现",
    C.GRADE_BOUNDED_EMPIRICAL: "有限范围经验证据",
    C.GRADE_NUMERICAL: "数值证据",
}

STRENGTH_PHRASE = {
    C.STRENGTH_UNIVERSAL: "全称结论",
    C.STRENGTH_CONDITIONAL: "条件结论",
    C.STRENGTH_BOUNDED: "有界结论",
    C.STRENGTH_OBSERVATION: "观察记录",
}

COMPLETION_PHRASE = {
    C.COMPLETION_NOT_COMPLETE: "尚未完成",
    C.COMPLETION_ROUTE_READY: "路线已完备，仅剩常规表述",
    C.COMPLETION_REPRODUCIBLE: "已达可复现",
    C.COMPLETION_VERIFIED: "已核验并进入权威",
}

ISSUE_PHRASE = {
    C.ISSUE_NO_VERIFIED_CHECKPOINT: "缺少已核验的检查点",
    C.ISSUE_NO_VERIFIED_CLAIM: "缺少已核验的结论",
    C.ISSUE_DEPENDENCY_OPEN: "依赖链未闭合",
    C.ISSUE_CANNOT_IMPLY_MISSING: "未写明该结论不能推出什么",
    C.ISSUE_GRADE_INSUFFICIENT: "证据等级不足以支撑该强度的结论",
    C.ISSUE_SCOPE_UNDECLARED: "未声明适用范围",
    C.ISSUE_REPRODUCTION_BLOCKED: "复现被阻断",
    C.ISSUE_UNREVIEWED_INSIGHT: "存在未经核验的洞见被当作结论",
    C.ISSUE_ROUTE_PORTFOLIO_INVALID: "路线组合不满足三条互相区分的要求",
    C.ISSUE_ASSESSMENT_STALE: "评估早于最近一次状态推进，已失效",
    C.ISSUE_COVERAGE_INCOMPLETE: "仍有成员或关系没有得到处置",
    C.ISSUE_REVIEW_DENIED: "复核方否决了这次完成判定",
}

RISK_PHRASE = {
    C.RISK_SCHEMA: "结构不合规",
    C.RISK_EVIDENCE_INSUFFICIENT: "证据等级不足",
    C.RISK_CANNOT_IMPLY_MISSING: "缺少不可推出声明",
    C.RISK_SCOPE_UNDECLARED: "缺少适用范围",
    C.RISK_UNKNOWN_ANCHOR: "引用了不存在的锚点",
    C.RISK_FOREIGN_ANCHOR: "引用了非本地或已失效的锚点",
    C.RISK_NEW_OBJECT: "引入了未登记的对象",
    C.RISK_ASSESSMENT_INCONSISTENT: "评估与当前状态不一致",
    C.RISK_UNSUPPORTED_TRANSITION: "不允许的状态转换",
    C.RISK_UNTYPED_CLAIM: "结论未声明强度",
}

# Operation codes. The renderer names the operation; it never reports what the
# model said about it, and it never carries the operation's raw output.
OPERATION_PHRASE = {
    "hash_file": "计算文件摘要",
    "stat_file": "读取文件元信息",
    "count_lines": "统计文件行数",
    "run_declared": "运行已声明的命令",
}

OPERATION_OUTCOME_PHRASE = {
    "executed": "已由 Skill 执行",
    "refused": "已被 Skill 拒绝",
}

# Requirement intake. The Skill asks; these tables are the only source of the
# question text, so a model can never write a question.
INTAKE_FIELD_QUESTION = {
    "statement": "请用一句话写下你要判定什么。它应当是一个可以被证据支持或否定的陈述。",
    "domain": "这个陈述属于哪个任务与领域边界？（例如：视觉分类、检索增强生成、强化学习控制）",
    "claim_scope": "这个结论要在什么范围内成立？写明数据集/任务、模型与规模、种子数、用什么指标，以及按平均还是最坏情况。",
    "assumptions": "哪些前提是你准备接受的？（例如：固定算力预算、不引入预训练权重）",
    "evidence_standard": "你需要达到哪一档证据标准才算完成？",
    "completion_standard": "什么出现时，你会认为这件事做完了？",
}

INTAKE_ASSUMPTION_PHRASE = {
    "fixed_compute_budget": "算力预算固定",
    "single_dataset": "只在单一数据集上评估",
    "fixed_seeds": "随机种子固定",
    "no_pretrained_weights": "不引入预训练权重",
    "fixed_hardware": "硬件配置固定",
    "fixed_data_split": "数据划分固定",
    "fixed_training_steps": "训练步数固定",
    "no_external_data": "不引入外部数据",
}

INTAKE_DECISION_PHRASE = {
    "continue": "继续补充",
    "ready": "形状已完备",
    "cannot_frame": "无法在给定材料下成形",
}

INTAKE_TEXT = {
    "header": "需求调研",
    "round": "- 第 {round} 轮｜已补齐 {filled}/{required}",
    "gap": "- 还缺：{label}",
    "question": "- 问题（由 Skill 生成）：{question}",
    "quote": "- 你原话中被认定的部分：{quote}",
    "absent": "- 你原话中没有涉及：{label}",
    "defaults": "- 暂定的默认假设：{items}",
    "standard": "- 暂定证据标准：{standard}",
    "ready": "- 六项构成字段已齐备，可以绑定目标。",
    "blocked": "- {reason}",
    "note": "（本段由确定性渲染器生成，不含模型撰写的文字。）",
}

OPERATION_REFUSAL_PHRASE = {
    "operation_unknown": "该操作不在封闭词表内",
    "operation_arguments_invalid": "参数不符合声明",
    "operation_outside_scope": "目标不在已声明的范围内",
    "operation_not_declared": "该命令未被声明",
    "operation_input_too_large": "输入超出上限",
    "operation_timeout": "执行超时",
    "operation_failed": "执行失败",
}

TEXT = {
    "header": "本轮判定",
    "draft_line": "- {decision}{scope}；{progress}；{certainty}",
    "focus": "- 锚点：{anchor}",
    "quote": "- 你的原话：{quote}",
    "patch_nodes": "- 记录新增节点 {count} 个",
    "patch_edges": "- 记录新增依赖 {count} 条",
    "blocking": "- {blocking}",
    "refused": "- 风险码：{codes}",
    "completion": "完成判定：{status}",
    "obligations": "仍未闭合的义务：",
    "obligation_item": "{index}. {text}",
    "route_table": "本轮冻结的路线组合：",
    "route_row": "- {label}｜{axes}",
    "axes": "{learning_signal} / {architecture} / {compute}",
    "anchor_moved": "锚点 {anchor} 的路径已更新，标识不变；历史路径 {count} 条。",
    "stale": "以下锚点在源文件变化后已标记为过期：{anchors}",
    "operation": "- 操作：{operation}（{outcome}）",
    "operation_refused": "- 拒绝原因：{reason}",
    "operation_bounded": "- 返回为有界值，原始输出不进入界面。",
    "no_prose": "（本段由确定性渲染器生成，不含模型撰写的文字。）",
}



# --------------------------------------------------------------------------
# Teacher answer surface. Model-authored text appears only inside these
# labelled slots, after the answer gate has checked it; the labels, the order,
# the provenance footer and every number come from here.
# --------------------------------------------------------------------------
KIND_PHRASE = {
    "concept_explanation": "概念理解",
    "paper_understanding": "读懂论文",
    "method_comparison": "方法比较与选择",
    "experiment_debugging": "实验排错",
    "research_question": "研究问题",
    "reproduction": "复现",
    "literature_search": "文献调研",
    "implementation_help": "实现",
    "learning_path": "学习路线",
    "other": "其他",
}

STUCK_PHRASE = {
    "concept": "概念本身",
    "assumption_condition": "前提与条件",
    "method_choice": "方法选择",
    "implementation": "实现细节",
    "experiment_computation": "实验与计算",
    "source_location": "出处与来源",
    "unclear": "尚不明确",
}

DIFFERS_PHRASE = {
    "goal": "目标不同",
    "object": "对象不同",
    "scope": "范围不同",
    "depth": "深浅不同",
    "output_form": "产出形式不同",
}

BASIS_PHRASE = {
    "retrieved_source": "来源引用（未复现）",
    "executed_check": "本地执行核验",
    "user_supplied": "你提供的材料",
    "reasoning": "仅凭推理（未核验）",
}

CONFIDENCE_PHRASE = {"high": "高", "medium": "中", "low": "低"}

DEFAULT_PHRASE = {
    "standard_definitions": "按领域标准定义理解术语",
    "mainstream_current_practice": "按当前主流做法",
    "single_gpu_budget": "按单卡算力预算",
    "python_pytorch_stack": "按 Python / PyTorch 技术栈",
    "no_prior_context": "不假设你已读过相关材料",
    "user_notes_relevant": "优先使用你自己的笔记与资料",
    "answer_in_chinese": "用中文回答",
    "depth_standard": "按常规深度",
}

ANSWER_TEXT = {
    "reading": "我的理解：{reading}",
    "reading_alternatives": "（另有理解：{items}。如果你问的是它，输入 /switch 编号）",
    "defaults": "暂按：{items}",
    "conclusion": "结论：{text}",
    "points": "要点",
    "point": "{index}. {text}",
    "point_meta": "   〔依据：{basis}｜等级：{grade}｜强度：{strength}〕{refs}",
    "point_quote": "   原文：“{quote}”",
    "point_cannot": "   不能推出：{text}",
    "explanation": "为什么（逐步剖析）",
    "explanation_step": "- {step}",
    "explanation_slots": "  动机：{motivation}｜适用边界：{boundary}｜失效时：{alternative}",
    "unknowns": "还不知道 / 尚未完成",
    "item": "- {text}",
    "checks": "可以接着核验",
    "followups": "你可能接着想问（输入 /ask 编号）",
    "followup": "{index}) {text}",
    "sources": "来源：{items}（输入 /more 编号 查看原文片段）",
    "metrics": "〔本轮｜调查 {steps}/{budget} 步，执行 {ops} 项操作｜来源片段 {available} 条、被引用 {cited} 条｜原文核验 {qv}/{qc}｜仅凭推理 {reasoning} 条｜未展示候选 {omitted} 条{flags}〕",
    "flag_budget": "｜调查预算已用尽",
    "flag_attached": "｜片段由 Teacher 在原位置核对 {verified} 条、只能采信 agent {declared} 条",
    "flag_unavailable": "｜可用来源不足",
    "confidence": "把握：{level}",
    "provenance": "（结论与要点由模型撰写，已通过确定性检查：依据、等级、强度与“不能推出”逐条校验，原文引用逐字节核对。检查不证明内容正确。）",
}

CLARIFY_TEXT = {
    "header": "先确认一件事：你的问题可以有几种理解，它们会把回答带向不同方向。",
    "option": "{index}) {text}（{differs}）",
    "prompt": "回复编号即可；直接回车按 1)；都不是就直接补一句。",
    "note": "（选项由模型根据你的原话提出、已核对引文；是否需要问你由 Skill 的规则决定，每个问题最多问一次。）",
}

FAILURE_TEXT = {
    "frame": "这次没能在安全边界内理解你的请求。请换个说法再发一次，或者说说你卡在哪一步。",
    "answer": "这次的回答没有通过检查，已丢弃，没有写入任何记录。",
    "counts": "〔已调查 {steps}/{budget} 步，执行 {operations} 项操作，取得来源片段 {sources} 条、执行结果 {checks} 条〕",
}

ARCHIVE_TEXT = {
    "header": "待归档：{shown} 项（其中已核验 {verified} 项；另有 {hidden} 项未展示）",
    "item": "{index}. [{kind}{verified}] {text}",
    "prompt": "回复 1 = 只归档已核验的（默认）｜2 = 全部归档｜3 = 都不归档",
    "none": "目前没有待归档的内容。",
    "done": "已归档 {archived} 项，放弃 {declined} 项，保留待定 {kept} 项。",
}

CANDIDATE_KIND_PHRASE = {"claim": "结论", "failure": "失败记录", "source": "来源", "open_question": "未解问题"}


class RenderError(ValueError):
    pass


# --------------------------------------------------------------------------
# Bounded value helpers
# --------------------------------------------------------------------------
def _phrase(table: Mapping[str, str], value: Any, kind: str) -> str:
    if value in table:
        return table[value]
    # Not a declared value: print the code, never the raw string.
    return f"{kind}:{value!r}"


def _bounded_count(value: Any, limit: int = 100000) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RenderError("counts must be integers")
    if value < 0 or value > limit:
        raise RenderError("count is outside the declared range")
    return value


def _identifier(value: Any) -> str:
    """Identifiers are printed, but only after shape screening.

    A validated identifier is ascii, short and token-like, so it cannot carry a
    sentence even if a caller somehow obtained a hostile one.
    """

    if not isinstance(value, str) or not value or len(value) > 96:
        raise RenderError("identifier must be a short non-empty string")
    if not value.isascii():
        raise RenderError("identifier must be ascii")
    if not all(char.isalnum() or char in "-_." for char in value):
        raise RenderError("identifier must be token-shaped")
    return value


def _quote(value: Any, *, verified: bool) -> str:
    if not verified:
        raise RenderError("a quote may only be rendered after byte verification")
    if not isinstance(value, str):
        raise RenderError("quote must be a string")
    return value



def _slot(value: Any, limit: int) -> str:
    """Model-authored slot text. Already checked by the answer gate; checked
    again here, because the renderer does not trust its caller."""

    if not isinstance(value, str) or not value.strip():
        raise RenderError("slot text must be a non-empty string")
    if len(value) > limit:
        raise RenderError("slot text exceeds its limit")
    return " ".join(value.split())


def _locator(value: Any) -> str:
    if not isinstance(value, str):
        raise RenderError("locator must be a string")
    return " ".join(value.split())[:160]

# --------------------------------------------------------------------------
# Renderer
# --------------------------------------------------------------------------
class Renderer:
    """Pure functions. No store access, no backend access, no I/O."""

    def render_refusal(self, risk_codes: Sequence[str]) -> str:
        codes = sorted({_phrase(RISK_PHRASE, code, "risk") for code in risk_codes})
        if not codes:
            raise RenderError("a refusal must name at least one risk")
        return C.SAFE_FAILURE_RESPONSE + "\n" + TEXT["refused"].format(codes="、".join(codes))

    def render_failure(self) -> str:
        return C.SAFE_FAILURE_RESPONSE

    def render_post_review_failure(self) -> str:
        return C.SAFE_POST_REVIEW_FAILURE

    def render_turn(
        self,
        draft: Mapping[str, Any],
        *,
        supplied_text: str,
        known_anchor_ids: set[str] | None = None,
        quote_verified: bool = False,
        operation_outcome: Mapping[str, Any] | None = None,
    ) -> str:
        """Render one turn.

        The draft is re-validated here. The renderer is not allowed to trust
        its caller, even though the caller is the engine.

        `operation_outcome` carries only closed values: whether this Skill
        executed or refused, the operation code, and a refusal code. Raw output
        from an executed operation never reaches this function.
        """

        validate_draft(draft, supplied_text=supplied_text, known_anchor_ids=known_anchor_ids)

        lines: list[str] = [TEXT["header"]]
        lines.append(
            TEXT["draft_line"].format(
                decision=_phrase(DECISION_PHRASE, draft["decision"], "decision"),
                scope="" if draft["scope"] == "objective" else "｜" + _phrase(SCOPE_PHRASE, draft["scope"], "scope"),
                progress=_phrase(PROGRESS_PHRASE, draft["route_progress"], "progress"),
                certainty=_phrase(CERTAINTY_PHRASE, draft["certainty"], "certainty"),
            )
        )

        if draft["blocking_code"] is not None:
            lines.append(TEXT["blocking"].format(blocking=_phrase(BLOCKING_PHRASE, draft["blocking_code"], "blocking")))

        if draft["focus_anchor_id"] is not None:
            lines.append(TEXT["focus"].format(anchor=_identifier(draft["focus_anchor_id"])))

        if draft["focus_quote"]:
            lines.append(TEXT["quote"].format(quote=_quote(draft["focus_quote"], verified=quote_verified)))

        patch = draft["graph_patch"]
        node_count = _bounded_count(len(patch.get("nodes", [])), limit=12)
        edge_count = _bounded_count(len(patch.get("edges", [])), limit=24)
        if node_count:
            lines.append(TEXT["patch_nodes"].format(count=node_count))
            for node in patch["nodes"]:
                lines.append(f"  - [{validate_label_code(node['label_code'])}] {node['kind']}/{node['status']}")
        if edge_count:
            lines.append(TEXT["patch_edges"].format(count=edge_count))
            for edge in patch["edges"]:
                lines.append(
                    f"  - {_identifier(edge['src'])} -{edge['relation']}-> {_identifier(edge['dst'])}"
                )

        if operation_outcome is not None:
            outcome = operation_outcome.get("outcome")
            code = operation_outcome.get("operation")
            if outcome not in OPERATION_OUTCOME_PHRASE or code not in OPERATION_PHRASE:
                raise RenderError("operation outcome must be a closed value")
            lines.append(
                TEXT["operation"].format(
                    operation=_phrase(OPERATION_PHRASE, code, "operation"),
                    outcome=_phrase(OPERATION_OUTCOME_PHRASE, outcome, "outcome"),
                )
            )
            if outcome == "refused":
                refusal = operation_outcome.get("code")
                if refusal not in OPERATION_REFUSAL_PHRASE:
                    raise RenderError("operation refusal must be a closed value")
                lines.append(TEXT["operation_refused"].format(reason=_phrase(OPERATION_REFUSAL_PHRASE, refusal, "refusal")))
            else:
                lines.append(TEXT["operation_bounded"])

        lines.append(TEXT["no_prose"])
        return "\n".join(lines)

    def render_completion(self, assessment: Mapping[str, Any]) -> str:
        status = assessment.get("status")
        if status not in C.COMPLETION_STATUSES:
            raise RenderError("unknown completion status")
        lines = [TEXT["completion"].format(status=_phrase(COMPLETION_PHRASE, status, "status"))]
        codes = assessment.get("issue_codes") or []
        for code in codes:
            if code not in C.COMPLETION_ISSUE_CODES:
                raise RenderError("unknown issue code")
        if codes:
            lines.append(C.REFUSE_COMPLETION)
            lines.append(TEXT["obligations"])
            for index, code in enumerate(sorted(codes), start=1):
                lines.append(TEXT["obligation_item"].format(index=index, text=_phrase(ISSUE_PHRASE, code, "issue")))
        lines.append(TEXT["no_prose"])
        return "\n".join(lines)

    def render_routes(self, routes: Sequence[Mapping[str, Any]]) -> str:
        lines = [TEXT["route_table"]]
        for route in routes:
            label = _identifier(route["label_code"]) if route.get("label_code") else "route.unlabelled"
            axes = route.get("axes") or {}
            rendered_axes = TEXT["axes"].format(
                learning_signal=_identifier(str(axes.get(C.AXIS_LEARNING_SIGNAL, "axis.unknown"))),
                architecture=_identifier(str(axes.get(C.AXIS_ARCHITECTURE, "axis.unknown"))),
                compute=_identifier(str(axes.get(C.AXIS_COMPUTE, "axis.unknown"))),
            )
            lines.append(TEXT["route_row"].format(label=label, axes=rendered_axes))
        lines.append(TEXT["no_prose"])
        return "\n".join(lines)

    def render_anchor_history(self, *, anchor_id: str, history: Sequence[Mapping[str, str]]) -> str:
        return TEXT["anchor_moved"].format(
            anchor=_identifier(anchor_id), count=_bounded_count(len(history), limit=10000)
        )

    def render_intake(self, state: Mapping[str, Any], *, outcome: Mapping[str, Any] | None = None) -> str:
        """Render one intake round. The question text comes from a fixed table."""

        from .intake import FIELD_LABEL, completeness

        progress = completeness(state)
        lines = [
            INTAKE_TEXT["header"],
            INTAKE_TEXT["round"].format(
                round=_bounded_count(int(state.get("round", 0)), limit=1000),
                filled=_bounded_count(int(progress["filled"]), limit=100),
                required=_bounded_count(int(progress["required"]), limit=100),
            ),
        ]
        gap = progress["missing"][0] if progress["missing"] else None
        if gap:
            lines.append(INTAKE_TEXT["gap"].format(label=FIELD_LABEL[gap]))
            lines.append(INTAKE_TEXT["question"].format(question=INTAKE_FIELD_QUESTION[gap]))
        else:
            lines.append(INTAKE_TEXT["ready"])

        if outcome is not None:
            quote = outcome.get("quote")
            if quote:
                lines.append(INTAKE_TEXT["quote"].format(quote=quote))
            declared = outcome.get("declared_absent")
            if declared and outcome.get("field"):
                lines.append(INTAKE_TEXT["absent"].format(label=FIELD_LABEL[outcome["field"]]))
            defaults = outcome.get("assumed_defaults") or []
            if defaults:
                phrases = [
                    _phrase(INTAKE_ASSUMPTION_PHRASE, code, "assumption")
                    for code in sorted(set(defaults))
                ]
                lines.append(INTAKE_TEXT["defaults"].format(items="、".join(phrases)))
            standard = outcome.get("evidence_standard")
            if standard:
                lines.append(
                    INTAKE_TEXT["standard"].format(standard=_phrase(GRADE_PHRASE, standard, "grade"))
                )
            reason = outcome.get("reason")
            if reason:
                lines.append(INTAKE_TEXT["blocked"].format(reason=reason))
        lines.append(INTAKE_TEXT["note"])
        return "\n".join(lines)


    # -- Teacher surfaces -------------------------------------------------
    def render_clarification(self, frame: Mapping[str, Any]) -> str:
        options = frame.get("interpretations") or []
        if not options:
            raise RenderError("a clarification needs options")
        lines = [CLARIFY_TEXT["header"]]
        for option in options:
            lines.append(CLARIFY_TEXT["option"].format(
                index=_bounded_count(option["id"], limit=9),
                text=_slot(option["text"], C.MAX_INTERPRETATION_CHARS),
                differs=_phrase(DIFFERS_PHRASE, option["differs_by"], "differs"),
            ))
        lines.append(CLARIFY_TEXT["prompt"])
        lines.append(CLARIFY_TEXT["note"])
        return "\n".join(lines)

    def render_answer(
        self,
        answer: Mapping[str, Any],
        *,
        metrics: Mapping[str, Any],
        evidence: Mapping[str, Mapping[str, Any]],
        frame: Mapping[str, Any],
    ) -> str:
        lines: list[str] = []
        readings = frame.get("interpretations") or []
        chosen = frame.get("chosen")
        current = next((item for item in readings if item["id"] == chosen), None)
        if current is not None and len(readings) > 1:
            lines.append(ANSWER_TEXT["reading"].format(reading=_slot(current["text"], C.MAX_INTERPRETATION_CHARS)))
            others = [f"{item['id']}) {_slot(item['text'], C.MAX_INTERPRETATION_CHARS)}" for item in readings if item["id"] != chosen]
            lines.append(ANSWER_TEXT["reading_alternatives"].format(items="；".join(others)))
        defaults = frame.get("defaults") or []
        if defaults:
            lines.append(ANSWER_TEXT["defaults"].format(items="、".join(_phrase(DEFAULT_PHRASE, code, "default") for code in defaults)))
        lines.append(ANSWER_TEXT["conclusion"].format(text=_slot(answer["conclusion"], C.LIMIT_CONCLUSION)))
        lines.append(ANSWER_TEXT["confidence"].format(level=_phrase(CONFIDENCE_PHRASE, answer["confidence"], "confidence")))
        lines.append("")
        lines.append(ANSWER_TEXT["points"])
        cited: list[str] = []
        for index, point in enumerate(answer["points"], start=1):
            lines.append(ANSWER_TEXT["point"].format(index=index, text=_slot(point["text"], C.LIMIT_POINT)))
            refs = [ref for ref in point["evidence"] if ref in evidence]
            for ref in refs:
                if ref not in cited:
                    cited.append(ref)
            lines.append(ANSWER_TEXT["point_meta"].format(
                basis=_phrase(BASIS_PHRASE, point["basis"], "basis"),
                grade=_phrase(GRADE_PHRASE, point["grade"], "grade") if point["grade"] else "未分级",
                strength=_phrase(STRENGTH_PHRASE, point["strength"], "strength"),
                refs=("".join(f"[{_identifier(ref)}]" for ref in refs)),
            ))
            if point.get("quote"):
                lines.append(ANSWER_TEXT["point_quote"].format(quote=_slot(point["quote"], C.MAX_QUOTE_IN_ANSWER)))
            if point.get("cannot_imply"):
                lines.append(ANSWER_TEXT["point_cannot"].format(text=_slot(point["cannot_imply"], C.LIMIT_SHORT)))
        if answer["explanation"]:
            lines.append("")
            lines.append(ANSWER_TEXT["explanation"])
            for entry in answer["explanation"]:
                lines.append(ANSWER_TEXT["explanation_step"].format(step=_slot(entry["step"], C.LIMIT_SLOT)))
                lines.append(ANSWER_TEXT["explanation_slots"].format(
                    motivation=_slot(entry["motivation"], C.LIMIT_SLOT),
                    boundary=_slot(entry["boundary"], C.LIMIT_SLOT),
                    alternative=_slot(entry["alternative"], C.LIMIT_SLOT),
                ))
        if answer["unknowns"]:
            lines.append("")
            lines.append(ANSWER_TEXT["unknowns"])
            for item in answer["unknowns"]:
                lines.append(ANSWER_TEXT["item"].format(text=_slot(item, C.LIMIT_SHORT)))
        if answer["next_checks"]:
            lines.append("")
            lines.append(ANSWER_TEXT["checks"])
            for check in answer["next_checks"]:
                lines.append(ANSWER_TEXT["item"].format(text=_slot(check["text"], C.LIMIT_SHORT)))
        if cited:
            lines.append("")
            lines.append(ANSWER_TEXT["sources"].format(items="；".join(
                f"{_identifier(ref)} {_locator(evidence[ref]['locator'])}" for ref in cited
            )))
        if answer["followups"]:
            lines.append("")
            lines.append(ANSWER_TEXT["followups"])
            for index, item in enumerate(answer["followups"], start=1):
                lines.append(ANSWER_TEXT["followup"].format(index=index, text=_slot(item["text"], C.LIMIT_FOLLOWUP)))
        flags = ""
        if metrics.get("budget_exhausted"):
            flags += ANSWER_TEXT["flag_budget"]
        if metrics.get("sources_unavailable"):
            flags += ANSWER_TEXT["flag_unavailable"]
        if "excerpts_verified_by_skill" in metrics:
            flags += ANSWER_TEXT["flag_attached"].format(
                verified=_bounded_count(int(metrics["excerpts_verified_by_skill"]), limit=10000),
                declared=_bounded_count(int(metrics.get("excerpts_declared_only", 0)), limit=10000),
            )
        lines.append("")
        lines.append(ANSWER_TEXT["metrics"].format(
            steps=_bounded_count(int(metrics["investigation_steps"]), limit=1000),
            budget=_bounded_count(int(metrics["investigation_budget"]), limit=1000),
            ops=_bounded_count(int(metrics["operations_executed"]), limit=10000),
            available=_bounded_count(int(metrics["evidence_items_available"]), limit=10000),
            cited=_bounded_count(int(metrics["evidence_items_cited"]), limit=10000),
            qv=_bounded_count(int(metrics["quotes_verified"]), limit=1000),
            qc=_bounded_count(int(metrics["quotes_checked"]), limit=1000),
            reasoning=_bounded_count(int(metrics["points_reasoning_only"]), limit=1000),
            omitted=_bounded_count(int(metrics["candidates_omitted"]), limit=1000000),
            flags=flags,
        ))
        lines.append(ANSWER_TEXT["provenance"])
        return "\n".join(lines)

    def render_ask_failure(self, *, stage: str, counts: Mapping[str, Any]) -> str:
        lines = [FAILURE_TEXT["frame" if stage == "frame" else "answer"]]
        if counts:
            lines.append(FAILURE_TEXT["counts"].format(**{key: _bounded_count(int(counts.get(key, 0)), limit=100000)
                                                          for key in ("steps", "budget", "operations", "sources", "checks")}))
        return "\n".join(lines)

    def render_expand(self, item: Mapping[str, Any] | None) -> str:
        if item is None:
            return "没有这个来源编号。"
        kind = "执行输出（原文，截断）" if item["kind"] == "executed_check" else "来源片段（原文，未经模型改写）"
        return f"{_identifier(item['ref'])} {kind}｜{_locator(item['locator'])}\n{item['text']}"

    def render_objective_proposal(self, objective: Mapping[str, Any]) -> str:
        from .intake import FIELD_LABEL

        lines = ["起草的研究目标（由模型根据你前面的提问起草，已通过字段检查）："]
        for field in C.INTAKE_ORDER:
            value = objective[field]
            if field == "assumptions":
                value = "；".join(_slot(item, 160) for item in value)
            elif field == "evidence_standard":
                value = _phrase(GRADE_PHRASE, value, "grade")
            else:
                value = _slot(value, 300)
            lines.append(f"- {FIELD_LABEL[field]}：{value}")
        lines.append("确认绑定输入 /confirm；想改某一项输入 /set 字段 新内容 后再 /confirm；不需要就忽略。")
        return "\n".join(lines)

    def render_archive_proposal(self, proposal: Mapping[str, Any]) -> str:
        if not proposal["items"]:
            return ARCHIVE_TEXT["none"]
        lines = [ARCHIVE_TEXT["header"].format(
            shown=_bounded_count(proposal["shown"], limit=1000),
            verified=_bounded_count(proposal["verified_count"], limit=1000),
            hidden=_bounded_count(proposal["not_shown"], limit=1000000),
        )]
        for index, item in enumerate(proposal["items"], start=1):
            lines.append(ARCHIVE_TEXT["item"].format(
                index=index,
                kind=_phrase(CANDIDATE_KIND_PHRASE, item["kind"], "kind"),
                verified="·已核验" if item.get("verified") else "",
                text=_slot(item["text"], 200),
            ))
        lines.append(ARCHIVE_TEXT["prompt"])
        return "\n".join(lines)

    def render_archive_receipt(self, receipt: Mapping[str, Any]) -> str:
        return ARCHIVE_TEXT["done"].format(
            archived=_bounded_count(receipt["archived"], limit=100000),
            declined=_bounded_count(receipt["declined"], limit=100000),
            kept=_bounded_count(receipt["kept_pending"], limit=100000),
        )

    def render_stale(self, anchor_ids: Sequence[str]) -> str:
        if not anchor_ids:
            return ""
        return TEXT["stale"].format(anchors="、".join(_identifier(item) for item in sorted(set(anchor_ids))))


def render_bytes(text: str) -> bytes:
    """The exact bytes a surface must display. Used by the canary test."""

    return text.encode("utf-8")


def render_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
