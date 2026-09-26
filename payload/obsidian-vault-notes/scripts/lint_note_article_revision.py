#!/usr/bin/env python3
"""Lightweight lint for Obsidian article revisions.

This catches common signs that chat wording or formulaic AI style leaked into article prose.
It is intentionally conservative: findings guide contextual agent rewriting and never edit files.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass

from _observer import flush as flush_observer
from _observer import observed


CHAT_RESIDUE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("chat_question_residue", re.compile(r"你问|你刚才|上一轮|本轮|当前对话|用户问|用户要求")),
    ("glossy_answer_voice", re.compile(r"可以直白理解|换句话说|这个(?:词|术语)(?:指|的意思|可以理解|可理解为|是指)")),
    ("premature_source_term_gloss", re.compile(r"这里的`?[A-Za-z][A-Za-z0-9 _-]{2,}`?\s*指")),
    ("english_term_answer_voice", re.compile(r"`?[A-Za-z][A-Za-z0-9 _-]{2,}`?\s*(?:可以直白理解|不是指|指的是)")),
    ("anti_preamble_contrast", re.compile(r"(?:不是|并非|不在于)[^。；！？\n]{0,80}(?:不是|并非|也不是|更不是|不在于)[^。；！？\n]{0,120}(?:而是|其实是|真正|关键在于)")),
    ("negative_instruction_preamble", re.compile(r"(?:不要|别|不必)[^。；！？\n]{0,80}(?:也不要|也别|更不要|也不必)[^。；！？\n]{0,120}(?:而要|而应该|而需要)")),
    ("template_explanation_voice", re.compile(r"核心直觉|当然也可以|这只是|第一种想法|好处在于|常见写法|不会碰到")),
    ("assistant_offer_residue", re.compile(r"希望这(?:对你|能)(?:有帮助|帮到你)|如果你愿意|需要我(?:继续|补充)|请告诉我")),
    ("tool_marker_residue", re.compile(r"turn\d+(?:search|image|news|file)\d+|oai_citation|contentReference|attributableIndex|:::writing")),
]

WIKILINK_WITH_ALIAS = re.compile(r"\[\[[^\]|]+\|([^\]]+)\]\]")
WIKILINK_SIMPLE = re.compile(r"\[\[[^\]]+\]\]")
MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
SENTENCE_BOUNDARY = re.compile(r"[。；！？\n]")
ABSOLUTE_CONGLAI_TRIGGER = re.compile(r"从来不(?:是|在于|靠|取决于|来自|只是)")
DISPLAY_FORMULA_DELIMITER = re.compile(r"^\s*\$\$\s*$")
SINGLE_LINE_DISPLAY_FORMULA = re.compile(r"^\s*\$\$(.+?)\$\$\s*$")
INLINE_FORMULA_SPAN = re.compile(r"(?<!\\)(?<!\$)\$([^$\n]*?)(?<!\\)\$(?!\$)")
NON_DOLLAR_FORMULA_DELIMITER = re.compile(r"\\\(|\\\)|\\\[|\\\]")
INLINE_CODE_SPAN = re.compile(r"`[^`\n]+`")
WIKILINK_SPAN = re.compile(r"!?\[\[[^\]\n]+\]\]")
MARKDOWN_LINK_SPAN = re.compile(r"!?\[[^\]\n]*\]\([^)\n]+\)")
BLOCK_ID_SPAN = re.compile(r"(?m)(?<!\S)\^[A-Za-z0-9_-]+\s*$")
FENCED_CODE_BLOCK = re.compile(r"(?ms)^[ \t]*(`{3,}|~{3,})[^\n]*\n.*?^[ \t]*\1[ \t]*$")
HEADING_LINE = re.compile(r"(?m)^\s{0,3}#{1,6}\s+.*$")
FRONTMATTER_TITLE_LINE = re.compile(r"(?m)^\s*(?:title|aliases)\s*:.*$")
INLINE_QUOTATION = re.compile(r"“[^”\n]*”|‘[^’\n]*’")
REFERENCE_LINK_SPAN = re.compile(r"!?\[[^\]\n]+\]\[[^\]\n]*\]")
REFERENCE_LINK_DEFINITION_LINE = re.compile(r"(?m)^\s*\[[^\]\n]+\]:\s*\S.*$")
BARE_URL = re.compile(r"(?<!\()(?:https?://|file://)[^\s<>()\[\]{}\"'，。；！？]+")
CALLOUT_MARKER_LINE = re.compile(r"(?m)^\s*>\s*\[![^\]\n]+\].*$")
NUMBER_TOKEN = re.compile(r"(?<![A-Za-z0-9_])(?:\d{4}年|\d+(?:\.\d+)?%?)(?![A-Za-z0-9_])")
EPISTEMIC_TOKEN = re.compile(r"尚不明确|尚未|未必|不一定|可能|或许|大概|似乎|据称|推测|至少|至多|必然|一定|从不|总是|并非|不是|没有|无法")
CONFIDENCE_LEVELS = {"high", "medium", "low"}
HIGH_CONFIDENCE_CODES = {
    "chat_question_residue",
    "assistant_offer_residue",
    "tool_marker_residue",
    "heading_restatement",
    "inline_header_list",
    "semantic_sentinel_changed",
}
LOW_CONFIDENCE_CODES = {"staccato_run", "decorative_emoji", "boldface_cluster"}
COMPACT_DISPLAY_EXCLUDED_TEX = re.compile(
    r"\\begin\{|\}\\\\|\\\\|&|\\tag\{|\\label\{|\\intertext\{|"
    r"\\(?:begin|end)\{(?:aligned|align|gather|split|cases|matrix|pmatrix|bmatrix|vmatrix|array)\}"
)
LITERAL_CHANGZAI_CONTEXT = re.compile(
    r"(?:草|树|花|植物|庄稼|作物|蔬菜|苔藓|蘑菇|根|枝|叶|果实|头发|毛发|眉毛|睫毛|胡子|"
    r"痣|痘|疤|斑|肿瘤|结节|器官|牙|骨|肉|皮肤|细胞|胎儿)"
    r"[^。；！？\n]{0,12}长在|长在[^。；！？\n]{0,16}"
    r"(?:土里|地里|水里|树上|枝上|根上|身上|脸上|头上|手上|脚上|皮肤|肺|胃|肝|肾|"
    r"心脏|身体|体内|子宫|骨头|牙龈)"
)
LITERAL_DRIFT_CONTEXT = re.compile(
    r"(?:汽车|赛车|车|轮胎|方向盘|车辆|船|飞机|无人机|物体|零件|部件|镜头|图像|图片|像素|"
    r"坐标|位置|轨迹|传感器|陀螺仪|加速度计|读数|测量值|电压|电流|频率|相位|基线|"
    r"drift)"
    r"[^。；！？\n]{0,16}漂移|漂移[^。；！？\n]{0,16}"
    r"(?:位置|距离|角度|坐标|像素|轨迹|drift)"
)
ESTABLISHED_DRIFT_TERM = re.compile(
    r"(?:时钟|概念|数据|模型|分布|基线|相位|频率|信号|传感器|位置|坐标|像素|读数|测量值|电压|电流|时间|轨迹)漂移|"
    r"漂移(?:检测|校准|补偿|误差|速率|率|模型)"
)
CALLOUT_HEADER = re.compile(r"^\s*>\s*\[!(?P<kind>[A-Za-z0-9_-]+)\]", re.IGNORECASE)
QUOTE_CALLOUT_KINDS = {"quote", "cite"}
CONTRAST_FRAME = re.compile(r"(?:不是|并非|不在于)[^。！？\n]{1,56}(?:而是|而在于)")
PROJECT_TERM = re.compile(r"赋能|抓手|闭环|沉淀|落地|协同|打通|对齐|链路|生态位|知识资产|能力建设")
PROJECT_VERB = re.compile(r"构建|打造|形成|建立|推进|实现|沉淀")
PROJECT_TECHNICAL_CONTEXT = re.compile(r"控制系统|反馈控制|通信链路|调用链路|传感器|协议|数据流|网络拓扑|软件服务|时钟同步")
PROMOTIONAL_OPENING = re.compile(
    r"^(?:(?:在|随着)[^。！？\n]{0,32}"
    r"(?:日新月异|飞速发展|蓬勃发展|浩瀚|璀璨|波澜壮阔|前所未有)"
    r"[^。！？\n]{0,20}[，,]|(?:欢迎(?:来到|走进)|今天(?:我们)?(?:来|要)(?:认识|了解|探索)))"
)
QUESTION_UNIT = re.compile(r"[^。！？\n]{2,40}？")
QUESTION_CHAIN_LEAD = re.compile(r"需要回答|问题包括|问题会变成|逐项检查")
QUESTION_SPECIFIC_CONTEXT = re.compile(r"例如|比如|假设|具体|\d|\$|\[\[")
FOLLOWTHROUGH_ACTION = re.compile(r"计算|检索|查找|核对|尝试|试验|实验|比较|验证|回答|发现|得到|因此|所以")


@dataclass
class CandidateLine:
    line_no: int
    text: str
    source: str


@dataclass
class WarningItem:
    line_no: int
    code: str
    text: str
    source: str
    rule_id: str | None = None
    category: str | None = None
    confidence: str | None = None
    message: str = ""


@dataclass
class StyleTerms:
    suspicious_terms: list[str]
    allowed_terms: list[str]


@dataclass
class LongformStyleRule:
    rule_id: str
    category: str
    message: str
    pattern: re.Pattern[str]
    confidence: str


@dataclass
class LongformStyleProfile:
    name: str
    rules: list[LongformStyleRule]
    allowed_phrases: list[str]


def has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def is_source_quote_line(text: str) -> bool:
    stripped = text.lstrip()
    return stripped.startswith("> “") or stripped.startswith('> "') or stripped.startswith("> `")


def style_protected_quote_keys(lines: list[CandidateLine]) -> set[tuple[str, int]]:
    """Protect ordinary blockquotes plus quote/cite callouts from prose-style rules."""
    protected: set[tuple[str, int]] = set()
    by_source: dict[str, list[CandidateLine]] = {}
    for item in lines:
        by_source.setdefault(item.source, []).append(item)

    for source_lines in by_source.values():
        mode: str | None = None
        previous_line = 0
        for item in sorted(source_lines, key=lambda entry: entry.line_no):
            if previous_line and item.line_no and item.line_no != previous_line + 1:
                mode = None
            stripped = item.text.lstrip()
            if not stripped.startswith(">"):
                mode = None
            else:
                header = CALLOUT_HEADER.match(item.text)
                if header:
                    mode = "protected" if header.group("kind").lower() in QUOTE_CALLOUT_KINDS else "callout"
                elif mode is None:
                    mode = "protected"
                if mode == "protected":
                    protected.add((item.source, item.line_no))
            previous_line = item.line_no
    return protected


def protected_source_quotes(text: str) -> Counter[str]:
    candidates = [
        CandidateLine(line_no, line, "sentinel")
        for line_no, line in enumerate(text.splitlines(), start=1)
    ]
    protected = style_protected_quote_keys(candidates)
    return Counter(
        item.text
        for item in candidates
        if (item.source, item.line_no) in protected and item.text.strip()
    )


def fenced_code_line_numbers(file_path: pathlib.Path) -> set[int]:
    text = file_path.read_text(encoding="utf-8", errors="replace")
    fenced: set[int] = set()
    fence_char: str | None = None
    fence_len = 0
    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.lstrip()
        match = re.match(r"(`{3,}|~{3,})", stripped)
        if fence_char is None:
            if match:
                marker = match.group(1)
                fence_char = marker[0]
                fence_len = len(marker)
                fenced.add(line_no)
            continue
        fenced.add(line_no)
        if re.match(rf"{re.escape(fence_char)}{{{fence_len},}}\s*$", stripped):
            fence_char = None
            fence_len = 0
    return fenced


@observed("obsidian.lint.acquire_scope")
def read_file_candidates(file_path: pathlib.Path, source: str = "file") -> list[CandidateLine]:
    text = file_path.read_text(encoding="utf-8", errors="replace")
    fenced_lines = fenced_code_line_numbers(file_path)
    return [
        CandidateLine(index, line, source)
        for index, line in enumerate(text.splitlines(), start=1)
        if index not in fenced_lines
    ]


def filter_fenced_candidates(lines: list[CandidateLine], file_path: pathlib.Path) -> list[CandidateLine]:
    fenced_lines = fenced_code_line_numbers(file_path)
    return [item for item in lines if item.line_no == 0 or item.line_no not in fenced_lines]


def normalize_for_english_term_scan(text: str) -> str:
    text = WIKILINK_WITH_ALIAS.sub(r"\1", text)
    text = WIKILINK_SIMPLE.sub("", text)
    return MARKDOWN_LINK.sub(r"\1", text)


def sentence_around(text: str, index: int) -> str:
    before = text[:index]
    after = text[index:]
    left_matches = list(SENTENCE_BOUNDARY.finditer(before))
    left = left_matches[-1].end() if left_matches else 0
    right_match = SENTENCE_BOUNDARY.search(after)
    right = index + right_match.start() if right_match else len(text)
    return text[left:right].strip()


def is_quoted_term(text: str, index: int, term: str) -> bool:
    left = text[index - 1] if index > 0 else ""
    right_index = index + len(term)
    right = text[right_index] if right_index < len(text) else ""
    return (left, right) in {("`", "`"), ('"', '"'), ("“", "”"), ("'", "'")}


def is_inside_inline_quote(text: str, index: int) -> bool:
    for mark in ("`", '"', "'"):
        if text[:index].count(mark) % 2 == 1:
            return True
    for opening, closing in (("“", "”"), ("《", "》")):
        last_open = text.rfind(opening, 0, index)
        last_close = text.rfind(closing, 0, index)
        if last_open > last_close:
            return True
    return False


def is_inside_pattern_span(text: str, index: int, pattern: re.Pattern[str]) -> bool:
    return any(match.start() <= index < match.end() for match in pattern.finditer(text))


def is_protected_inline_span(text: str, index: int) -> bool:
    if is_inside_inline_quote(text, index):
        return True
    return any(
        is_inside_pattern_span(text, index, pattern)
        for pattern in (INLINE_FORMULA_SPAN, INLINE_CODE_SPAN, WIKILINK_SPAN, MARKDOWN_LINK_SPAN)
    )


def has_suspicious_absolute_conglai(text: str) -> bool:
    for match in ABSOLUTE_CONGLAI_TRIGGER.finditer(text):
        if is_inside_inline_quote(text, match.start()):
            continue
        sentence = sentence_around(text, match.start())
        local_index = sentence.find(match.group(0))
        if local_index < 0:
            continue
        before = sentence[:local_index]
        after = sentence[local_index + len(match.group(0)):]
        has_suspended_subject = bool(re.search(r"的[，,、]?\s*$", before))
        has_reversal = bool(re.search(r"[，,、]?\s*(?:而是|而在于|而在|真正|关键在于)", after))
        if has_suspended_subject or has_reversal:
            return True
    return False


def has_suspicious_changzai(text: str) -> bool:
    for match in re.finditer("长在", text):
        if is_quoted_term(text, match.start(), "长在"):
            continue
        if is_inside_inline_quote(text, match.start()):
            continue
        sentence = sentence_around(text, match.start())
        if not LITERAL_CHANGZAI_CONTEXT.search(sentence):
            return True
    return False


def load_style_terms(path: pathlib.Path | None) -> StyleTerms:
    if path is None or not path.exists():
        return StyleTerms([], [])
    data = path.read_text(encoding="utf-8", errors="replace")
    current: str | None = None
    values: dict[str, list[str]] = {"suspicious_terms": [], "allowed_terms": []}
    for raw in data.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith(":"):
            key = line[:-1].strip()
            current = key if key in values else None
            continue
        if current and line.startswith("-"):
            term = line[1:].strip().strip("'\"")
            if term:
                values[current].append(term)
    return StyleTerms(values["suspicious_terms"], values["allowed_terms"])


def longform_profile_path() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[1] / "references" / "natural-chinese-lint.json"


def load_longform_style_profile(name: str | None) -> LongformStyleProfile | None:
    if name is None:
        return None
    if name != "chinese-longform":
        raise ValueError(f"Unsupported style profile: {name}")
    path = longform_profile_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Could not read style profile {name}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid style profile JSON {path}: {exc}") from exc
    if data.get("profile") != name:
        raise ValueError(f"Style profile file does not declare {name}: {path}")

    default_confidence = data.get("default_confidence", "medium")
    if default_confidence not in CONFIDENCE_LEVELS:
        raise ValueError(f"Style profile {name} has invalid default_confidence: {default_confidence}")

    rules: list[LongformStyleRule] = []
    for field, is_pattern in (("phrases", False), ("patterns", True)):
        raw_rules = data.get(field, [])
        if not isinstance(raw_rules, list):
            raise ValueError(f"Style profile {name} field {field} must be a list")
        for raw_rule in raw_rules:
            if not isinstance(raw_rule, dict):
                raise ValueError(f"Style profile {name} contains a non-object rule")
            rule_id = raw_rule.get("id")
            category = raw_rule.get("category")
            message = raw_rule.get("message")
            confidence = raw_rule.get("confidence", default_confidence)
            expression = raw_rule.get("regex") if is_pattern else raw_rule.get("text")
            if not all(isinstance(value, str) and value for value in (rule_id, category, message, expression)):
                raise ValueError(f"Style profile {name} has an incomplete {field} rule")
            if confidence not in CONFIDENCE_LEVELS:
                raise ValueError(f"Style profile {name} rule {rule_id} has invalid confidence: {confidence}")
            try:
                compiled = re.compile(expression if is_pattern else re.escape(expression))
            except re.error as exc:
                raise ValueError(f"Style profile {name} rule {rule_id} has invalid regex: {exc}") from exc
            rules.append(LongformStyleRule(rule_id, category, message, compiled, confidence))

    allowed = data.get("allowed_phrases", [])
    if not isinstance(allowed, list) or not all(isinstance(value, str) for value in allowed):
        raise ValueError(f"Style profile {name} allowed_phrases must be a list of strings")
    return LongformStyleProfile(name, rules, allowed)


def is_profile_match_protected(
    text: str,
    match: re.Match[str],
    profile: LongformStyleProfile,
) -> bool:
    if is_protected_inline_span(text, match.start()):
        return True
    matched = match.group(0)
    if matched in profile.allowed_phrases:
        return True
    return any(
        phrase and match.start() >= start and match.end() <= start + len(phrase)
        for phrase in profile.allowed_phrases
        for start in [text.find(phrase)]
        if start >= 0
    )


def has_suspicious_drift(text: str) -> bool:
    for match in re.finditer("漂移", text):
        if is_quoted_term(text, match.start(), match.group(0)):
            continue
        if is_protected_inline_span(text, match.start()):
            continue
        sentence = sentence_around(text, match.start())
        if LITERAL_DRIFT_CONTEXT.search(sentence) or ESTABLISHED_DRIFT_TERM.search(sentence):
            continue
        return True
    return False


def is_plain_prose_line(text: str) -> bool:
    stripped = text.strip()
    if not stripped or not has_cjk(stripped):
        return False
    if stripped.startswith(("#", ">", "-", "*", "+", "|", "`", "$$", "<", "!", "::")):
        return False
    return True


def is_short_single_line_display_formula(formula: str) -> bool:
    stripped = formula.strip()
    if not stripped or len(stripped) > 90:
        return False
    if COMPACT_DISPLAY_EXCLUDED_TEX.search(stripped):
        return False
    return True


def is_formula_spacing_neighbor(char: str) -> bool:
    return (
        "\u4e00" <= char <= "\u9fff"
        or char in "，。；：！？、（）《》“”‘’【】"
    )


def has_cjk_spacing_around_formula(text: str, start: int, end: int) -> bool:
    if start > 0 and text[start - 1].isspace():
        index = start - 2
        while index >= 0 and text[index].isspace():
            index -= 1
        if index >= 0 and is_formula_spacing_neighbor(text[index]):
            return True

    if end < len(text) and text[end].isspace():
        index = end + 1
        while index < len(text) and text[index].isspace():
            index += 1
        if index < len(text) and is_formula_spacing_neighbor(text[index]):
            return True

    return False


def scan_formula_delimiters_and_spacing(lines: list[CandidateLine]) -> list[WarningItem]:
    warnings: list[WarningItem] = []
    seen: set[tuple[str, int, str]] = set()
    protected_quotes = style_protected_quote_keys(lines)
    for item in lines:
        text = item.text
        if not text.strip() or is_source_quote_line(text) or (item.source, item.line_no) in protected_quotes:
            continue

        if NON_DOLLAR_FORMULA_DELIMITER.search(text):
            warnings.append(
                WarningItem(
                    item.line_no,
                    "non_dollar_formula_delimiter",
                    "Use $...$ or $$...$$ for formulas; do not use \\(...\\) or \\[...\\].",
                    item.source,
                )
            )

        display_match = SINGLE_LINE_DISPLAY_FORMULA.match(text)
        if display_match and display_match.group(1) != display_match.group(1).strip():
            warnings.append(
                WarningItem(
                    item.line_no,
                    "loose_display_formula_inner_space",
                    "Do not pad display formulas immediately inside $$...$$.",
                    item.source,
                )
            )

        for match in INLINE_FORMULA_SPAN.finditer(text):
            if is_inside_inline_quote(text, match.start()):
                continue
            content = match.group(1)
            if content != content.strip():
                warnings.append(
                    WarningItem(
                        item.line_no,
                        "loose_inline_formula_inner_space",
                        "Do not pad inline formulas immediately inside $...$.",
                        item.source,
                    )
                )
            if has_cjk_spacing_around_formula(text, match.start(), match.end()):
                key = (item.source, item.line_no, "cjk_inline_formula_spacing")
                if key not in seen:
                    seen.add(key)
                    warnings.append(
                        WarningItem(
                            item.line_no,
                            "cjk_inline_formula_spacing",
                            "Remove spaces between Chinese prose/punctuation and adjacent inline formulas.",
                            item.source,
                        )
                    )
    return warnings


def scan_compact_display_formula(lines: list[CandidateLine]) -> list[WarningItem]:
    warnings: list[WarningItem] = []
    seen: set[tuple[str, int]] = set()
    by_source: dict[str, list[CandidateLine]] = {}
    for item in lines:
        by_source.setdefault(item.source, []).append(item)

    for source, source_lines in by_source.items():
        by_line = {item.line_no: item.text for item in source_lines}
        for item in sorted(source_lines, key=lambda entry: entry.line_no):
            single_line_match = SINGLE_LINE_DISPLAY_FORMULA.match(item.text)
            if single_line_match:
                formula_line = single_line_match.group(1)
                next_probe_line_no = item.line_no + 1
            else:
                if not DISPLAY_FORMULA_DELIMITER.match(item.text):
                    continue

                formula_line = by_line.get(item.line_no + 1)
                close_line = by_line.get(item.line_no + 2)
                if formula_line is None or close_line is None:
                    continue
                if not DISPLAY_FORMULA_DELIMITER.match(close_line):
                    continue
                next_probe_line_no = item.line_no + 3

            formula_text = formula_line.strip()
            if not is_short_single_line_display_formula(formula_text):
                continue

            prev_line_no = item.line_no - 1
            while prev_line_no in by_line and not by_line[prev_line_no].strip():
                prev_line_no -= 1
            next_line_no = next_probe_line_no
            while next_line_no in by_line and not by_line[next_line_no].strip():
                next_line_no += 1

            prev_line = by_line.get(prev_line_no, "")
            next_line = by_line.get(next_line_no, "")
            prev_is_prose = is_plain_prose_line(prev_line)
            next_is_prose = is_plain_prose_line(next_line)
            if not prev_is_prose:
                continue
            if prev_line.strip().endswith(("。", "；", "！", "？")) and not next_is_prose:
                continue

            key = (source, item.line_no)
            if key in seen:
                continue
            seen.add(key)
            compact = f"{prev_line.strip()}$${formula_text}$${next_line.strip()}"
            warnings.append(
                WarningItem(
                    item.line_no,
                    "loose_short_display_formula",
                    f"Short display formula is split from adjacent prose; prefer compact paragraph form when it renders correctly, e.g. {compact[:180]}",
                    source,
                )
            )
    return warnings


def repo_relative_path(repo: pathlib.Path, file_path: pathlib.Path) -> tuple[pathlib.Path | None, WarningItem | None]:
    if not repo.exists():
        return None, WarningItem(0, "repo_not_found", f"Repo path does not exist: {repo}", "repo")

    try:
        rel = file_path.resolve().relative_to(repo.resolve())
    except ValueError:
        return None, WarningItem(0, "file_outside_repo", f"File is outside repo: {file_path}", "repo")

    return rel, None


def run_git_diff(repo: pathlib.Path, rel: pathlib.Path, *, cached: bool = False) -> tuple[list[CandidateLine], WarningItem | None]:
    source = "git-diff-cached" if cached else "git-diff"

    command = ["git", "diff", "--no-ext-diff", "--unified=0"]
    if cached:
        command.append("--cached")
    command.extend(["--", rel.as_posix()])

    proc = subprocess.run(
        command,
        cwd=str(repo),
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        detail = " ".join((proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}").split())
        return [], WarningItem(0, "git_diff_failed", f"git diff failed for {rel.as_posix()}: {detail[:240]}", source)

    candidates: list[CandidateLine] = []
    current_new_line = 0
    for raw in proc.stdout.splitlines():
        if raw.startswith("@@"):
            match = re.search(r"\+(\d+)(?:,(\d+))?", raw)
            if match:
                current_new_line = int(match.group(1))
            continue
        if raw.startswith("+++") or raw.startswith("---"):
            continue
        if raw.startswith("+"):
            candidates.append(CandidateLine(current_new_line, raw[1:], source))
            current_new_line += 1
        elif raw.startswith("-"):
            continue
        elif current_new_line:
            current_new_line += 1
    return candidates, None


def git_path_is_tracked(repo: pathlib.Path, rel: pathlib.Path) -> tuple[bool, WarningItem | None]:
    proc = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", rel.as_posix()],
        cwd=str(repo),
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode == 0:
        return True, None
    if proc.returncode == 1:
        return False, None
    if proc.returncode != 0:
        detail = " ".join((proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}").split())
        return False, WarningItem(0, "git_ls_files_failed", f"git ls-files failed for {rel.as_posix()}: {detail[:240]}", "git")
    return False, None


def confidence_for_code(code: str) -> str:
    if code in HIGH_CONFIDENCE_CODES:
        return "high"
    if code in LOW_CONFIDENCE_CODES:
        return "low"
    return "medium"


def category_for_code(code: str) -> str:
    if code.startswith(("chat_", "assistant_", "tool_", "glossy_", "premature_", "english_term_", "template_explanation")):
        return "chat_residue"
    if code.startswith(("non_dollar_", "loose_inline_", "loose_display_", "cjk_inline_", "loose_short_display_")):
        return "formula_format"
    if code.startswith(("git_", "repo_")):
        return "tooling"
    if code in {"heading_restatement", "inline_header_list", "decorative_emoji", "boldface_cluster"}:
        return "formatting_fingerprint"
    if code == "staccato_run":
        return "manufactured_rhythm"
    if code == "repeated_contrast_frames":
        return "zh_template"
    if code == "abstract_project_cluster":
        return "zh_project_jargon"
    if code == "promotional_opening_frame":
        return "zh_promotional"
    if code == "question_chain_without_followthrough":
        return "zh_question_chain"
    if code == "semantic_sentinel_changed":
        return "semantic_invariant"
    return code


def build_warning(
    item: CandidateLine,
    code: str,
    *,
    message: str = "",
    text: str | None = None,
    confidence: str | None = None,
) -> WarningItem:
    return WarningItem(
        item.line_no,
        code,
        item.text.strip() if text is None else text,
        item.source,
        rule_id=code,
        category=category_for_code(code),
        confidence=confidence or confidence_for_code(code),
        message=message,
    )


@observed("obsidian.lint.scan")
def scan_lines(
    lines: list[CandidateLine],
    style_terms: StyleTerms | None = None,
    style_profile: LongformStyleProfile | None = None,
) -> list[WarningItem]:
    warnings: list[WarningItem] = []
    style_terms = style_terms or StyleTerms([], [])
    protected_quotes = style_protected_quote_keys(lines)
    for item in lines:
        text = item.text
        if not text.strip() or is_source_quote_line(text) or (item.source, item.line_no) in protected_quotes:
            continue

        for code, pattern in CHAT_RESIDUE_PATTERNS:
            match = pattern.search(text)
            if match and not is_protected_inline_span(text, match.start()):
                warnings.append(build_warning(item, code))

        if has_suspicious_absolute_conglai(text):
            warnings.append(WarningItem(item.line_no, "absolute_conglai_contrast", text.strip(), item.source))

        if has_suspicious_changzai(text):
            warnings.append(WarningItem(item.line_no, "abstract_changzai_metaphor", text.strip(), item.source))

        if has_suspicious_drift(text):
            warnings.append(WarningItem(item.line_no, "abstract_drift_metaphor", text.strip(), item.source))

        for term in style_terms.suspicious_terms:
            if term in style_terms.allowed_terms:
                continue
            index = text.find(term)
            if index >= 0 and not is_protected_inline_span(text, index):
                warnings.append(build_warning(item, "suspicious_style_term"))
                break

        if style_profile is not None:
            for rule in style_profile.rules:
                for match in rule.pattern.finditer(text):
                    if is_profile_match_protected(text, match, style_profile):
                        continue
                    warnings.append(
                        WarningItem(
                            item.line_no,
                            rule.category,
                            text.strip(),
                            item.source,
                            rule_id=rule.rule_id,
                            category=rule.category,
                            confidence=rule.confidence,
                            message=rule.message,
                        )
                    )
                    break

        if "中文译意" in text and has_cjk(text):
            scan_text = normalize_for_english_term_scan(text)
            english_terms = re.findall(r"`?[A-Za-z][A-Za-z0-9_-]*(?:\s+[A-Za-z][A-Za-z0-9_-]*)+`?", scan_text)
            if english_terms and "英文原文" not in text:
                warnings.append(
                    WarningItem(
                        item.line_no,
                        "english_term_in_chinese_source_note",
                        text.strip(),
                        item.source,
                    )
                )
    return warnings


def cjk_length(text: str) -> int:
    return sum(1 for char in text if "\u4e00" <= char <= "\u9fff")


def prose_sentences(text: str) -> list[str]:
    return [part.strip() for part in re.findall(r"[^。！？\n]+[。！？]", text) if part.strip()]


def scan_structural_patterns(lines: list[CandidateLine]) -> list[WarningItem]:
    warnings: list[WarningItem] = []
    by_source: dict[str, list[CandidateLine]] = {}
    for item in lines:
        by_source.setdefault(item.source, []).append(item)

    for source_lines in by_source.values():
        ordered = sorted(source_lines, key=lambda entry: entry.line_no)
        for index, item in enumerate(ordered):
            stripped = item.text.strip()
            if re.match(r"^\s*[-*+]\s+\*\*[^*]{1,24}[：:]\*\*", item.text):
                warnings.append(build_warning(item, "inline_header_list", message="机械的粗体标签列表：确认列表是否承担真实分类或步骤功能"))

            if len(re.findall(r"\*\*[^*\n]+\*\*", item.text)) >= 3:
                warnings.append(build_warning(item, "boldface_cluster", message="同一行粗体过密：保留真正需要强调的内容"))

            if re.match(r"^\s*(?:#{1,6}|[-*+])\s*[^\n]*[\U0001F300-\U0001FAFF]", item.text):
                warnings.append(build_warning(item, "decorative_emoji", message="标题或列表中的装饰性表情：按笔记本地格式判断是否删除"))

            heading_match = re.match(r"^\s*#{1,6}\s+(.+?)\s*$", item.text)
            if heading_match:
                heading = re.sub(r"[`*_~]", "", heading_match.group(1)).strip()
                for next_item in ordered[index + 1:index + 5]:
                    next_text = next_item.text.strip()
                    if not next_text:
                        continue
                    if next_text.startswith("#"):
                        break
                    announces = bool(re.search(r"(?:本节|这一节|下面|接下来).{0,16}(?:讨论|介绍|分析|说明)", next_text))
                    repeats = bool(heading and heading in next_text)
                    if announces and repeats:
                        warnings.append(build_warning(next_item, "heading_restatement", message="标题后的首句只预告或复述标题：直接进入具体内容"))
                    break

        paragraph: list[CandidateLine] = []

        def flush_paragraph() -> None:
            if not paragraph:
                return
            combined = "".join(entry.text.strip() for entry in paragraph)
            sentences = prose_sentences(combined)
            run = 0
            for sentence in sentences:
                run = run + 1 if 0 < cjk_length(sentence) <= 12 else 0
                if run >= 4:
                    warnings.append(
                        build_warning(
                            paragraph[0],
                            "staccato_run",
                            message="连续短句可能在制造戏剧节奏：结合段落功能冷读，不机械拉长",
                            text=combined[:240],
                        )
                    )
                    break
            paragraph.clear()

        previous_line_no = 0
        for item in ordered:
            if previous_line_no and item.line_no and item.line_no != previous_line_no + 1:
                flush_paragraph()
            stripped = item.text.strip()
            if not stripped or stripped.startswith(("#", ">", "- ", "* ", "+ ", "|", "$$", "```", "~~~")):
                flush_paragraph()
                previous_line_no = item.line_no
                continue
            paragraph.append(item)
            previous_line_no = item.line_no
        flush_paragraph()

    return warnings


def paragraph_groups(lines: list[CandidateLine]) -> list[list[CandidateLine]]:
    paragraphs: list[list[CandidateLine]] = []
    current: list[CandidateLine] = []
    previous_line_no = 0
    for item in sorted(lines, key=lambda entry: entry.line_no):
        if previous_line_no and item.line_no and item.line_no != previous_line_no + 1:
            if current:
                paragraphs.append(current)
                current = []
        stripped = item.text.strip()
        if not stripped or stripped.startswith(("#", ">", "- ", "* ", "+ ", "|", "$$", "```", "~~~")):
            if current:
                paragraphs.append(current)
                current = []
            previous_line_no = item.line_no
            continue
        current.append(item)
        previous_line_no = item.line_no
    if current:
        paragraphs.append(current)
    return paragraphs


def first_body_prose_line(lines: list[CandidateLine]) -> CandidateLine | None:
    ordered = sorted(lines, key=lambda entry: entry.line_no)
    in_frontmatter = False
    frontmatter_closed = False
    for index, item in enumerate(ordered):
        stripped = item.text.strip()
        if index == 0 and stripped == "---":
            in_frontmatter = True
            continue
        if in_frontmatter:
            if stripped == "---":
                in_frontmatter = False
                frontmatter_closed = True
            continue
        if frontmatter_closed and not stripped:
            continue
        if is_plain_prose_line(item.text):
            return item
    return None


def unprotected_matches(pattern: re.Pattern[str], text: str) -> list[re.Match[str]]:
    return [match for match in pattern.finditer(text) if not is_protected_inline_span(text, match.start())]


def scan_longform_context_patterns(lines: list[CandidateLine]) -> list[WarningItem]:
    """Low-confidence, context-dependent prompts for the Chinese long-form profile."""
    warnings: list[WarningItem] = []
    by_source: dict[str, list[CandidateLine]] = {}
    for item in lines:
        by_source.setdefault(item.source, []).append(item)

    for source, source_lines in by_source.items():
        if source in {"file", "file-untracked"}:
            opening = first_body_prose_line(source_lines)
            if opening and PROMOTIONAL_OPENING.search(opening.text.strip()):
                warnings.append(
                    build_warning(
                        opening,
                        "promotional_opening_frame",
                        confidence="low",
                        message="通用宣传开场可能替代了具体起点；普通文章可直接写事件、测试或问题，视频稿和真实历史背景按体裁保留",
                    )
                )

        paragraphs = paragraph_groups(source_lines)
        for paragraph_index, paragraph in enumerate(paragraphs):
            combined = "".join(entry.text.strip() for entry in paragraph)

            contrast_matches = unprotected_matches(CONTRAST_FRAME, combined)
            if len(contrast_matches) >= 2:
                warnings.append(
                    build_warning(
                        paragraph[0],
                        "repeated_contrast_frames",
                        confidence="low",
                        message="同段反复使用否定反转；逐项检查被否定的理解是否真实存在，定义、反例或分类需要这些对照时保留",
                        text=combined[:240],
                    )
                )

            for sentence in re.findall(r"[^。！？\n]+(?:[。！？]|$)", combined):
                term_matches = unprotected_matches(PROJECT_TERM, sentence)
                terms = {match.group(0) for match in term_matches}
                has_project_verb = bool(unprotected_matches(PROJECT_VERB, sentence))
                if len(terms) >= 3 or (len(terms) >= 2 and has_project_verb):
                    if PROJECT_TECHNICAL_CONTEXT.search(sentence):
                        continue
                    warnings.append(
                        build_warning(
                            paragraph[0],
                            "abstract_project_cluster",
                            confidence="low",
                            message="项目词在同句成串；先展开实际动作、对象和结果，控制理论、软件链路或真实工作流程中的准确术语应保留",
                            text=sentence.strip()[:240],
                        )
                    )
                    break

            questions = unprotected_matches(QUESTION_UNIT, combined)
            if len(questions) < 4:
                continue
            if QUESTION_CHAIN_LEAD.search(combined) or QUESTION_SPECIFIC_CONTEXT.search(combined):
                continue
            next_text = ""
            if paragraph_index + 1 < len(paragraphs):
                next_text = "".join(entry.text.strip() for entry in paragraphs[paragraph_index + 1])
            followthrough = combined[questions[-1].end():] + next_text
            if FOLLOWTHROUGH_ACTION.search(followthrough):
                continue
            warnings.append(
                build_warning(
                    paragraph[0],
                    "question_chain_without_followthrough",
                    confidence="low",
                    message="连续问句没有就近展开；保留会引向计算、检索、试验、比较或开放研究问题的问句，其余可合并为直接说明",
                    text=combined[:240],
                )
            )

    return warnings


def extract_semantic_sentinels(text: str) -> dict[str, Counter[str]]:
    display_formulas = Counter(match.group(0) for match in re.finditer(r"(?s)\$\$.*?\$\$", text))
    without_display = re.sub(r"(?s)\$\$.*?\$\$", "", text)
    inline_formulas = Counter(match.group(0) for match in INLINE_FORMULA_SPAN.finditer(without_display))
    return {
        "headings": Counter(HEADING_LINE.findall(text)),
        "frontmatter_titles": Counter(FRONTMATTER_TITLE_LINE.findall(text)),
        "numbers": Counter(NUMBER_TOKEN.findall(text)),
        "display_formulas": display_formulas,
        "inline_formulas": inline_formulas,
        "wikilinks": Counter(WIKILINK_SPAN.findall(text)),
        "markdown_links": Counter(MARKDOWN_LINK_SPAN.findall(text)),
        "reference_links": Counter(REFERENCE_LINK_SPAN.findall(text)),
        "reference_link_definitions": Counter(REFERENCE_LINK_DEFINITION_LINE.findall(text)),
        "bare_urls": Counter(BARE_URL.findall(text)),
        "block_ids": Counter(BLOCK_ID_SPAN.findall(text)),
        "inline_code_and_labels": Counter(INLINE_CODE_SPAN.findall(text)),
        "inline_quotations": Counter(INLINE_QUOTATION.findall(text)),
        "callout_markers": Counter(CALLOUT_MARKER_LINE.findall(text)),
        "source_quotes": protected_source_quotes(text),
        "fenced_code_blocks": Counter(match.group(0) for match in FENCED_CODE_BLOCK.finditer(text)),
        "epistemic_tokens": Counter(EPISTEMIC_TOKEN.findall(text)),
    }


def summarize_counter_delta(before: Counter[str], after: Counter[str]) -> str:
    removed = list((before - after).elements())
    added = list((after - before).elements())

    def compact(values: list[str]) -> list[str]:
        return [" ".join(value.split())[:80] for value in values[:5]]

    return f"removed={compact(removed)} added={compact(added)}"


@observed("obsidian.lint.semantic_sentinels")
def compare_semantic_sentinels(baseline_path: pathlib.Path, file_path: pathlib.Path) -> list[WarningItem]:
    baseline = baseline_path.read_text(encoding="utf-8", errors="replace")
    current = file_path.read_text(encoding="utf-8", errors="replace")
    before = extract_semantic_sentinels(baseline)
    after = extract_semantic_sentinels(current)
    warnings: list[WarningItem] = []
    for sentinel_name in before:
        if before[sentinel_name] == after[sentinel_name]:
            continue
        warnings.append(
            WarningItem(
                0,
                "semantic_sentinel_changed",
                f"{sentinel_name}: {summarize_counter_delta(before[sentinel_name], after[sentinel_name])}",
                "baseline",
                rule_id=f"sentinel_{sentinel_name}",
                category="semantic_invariant",
                confidence="high",
                message="Pre-edit and post-edit semantic sentinels differ; verify the change was intentional.",
            )
        )
    return warnings


def detect_line_endings(file_path: pathlib.Path) -> list[WarningItem]:
    data = file_path.read_bytes()
    if b"\r\n" not in data:
        return []
    return [
        WarningItem(
            0,
            "crlf_line_endings",
            "File contains CRLF line endings; check that this was intentional and did not cause a whole-file diff.",
            "file",
        )
    ]


def warning_payload(warning: WarningItem) -> dict[str, object]:
    rule_id = warning.rule_id or warning.code
    category = warning.category or category_for_code(warning.code)
    confidence = warning.confidence or confidence_for_code(warning.code)
    return {
        "id": rule_id,
        "category": category,
        "confidence": confidence,
        "line": warning.line_no,
        "source": warning.source,
        "message": warning.message,
        "text": warning.text,
    }


def json_result(
    warnings: list[WarningItem],
    *,
    file_path: pathlib.Path,
    scan_scope: str,
    profile: str | None,
    baseline: pathlib.Path | None,
) -> dict[str, object]:
    findings = [warning_payload(warning) for warning in warnings]
    category_counts = Counter(str(item["category"]) for item in findings)
    confidence_counts = Counter(str(item["confidence"]) for item in findings)
    return {
        "schema_version": 1,
        "ok": True,
        "has_findings": bool(findings),
        "file": str(file_path),
        "scan_scope": scan_scope,
        "profile": profile,
        "baseline": str(baseline) if baseline is not None else None,
        "category_counts": dict(sorted(category_counts.items())),
        "confidence_counts": dict(sorted(confidence_counts.items())),
        "findings": findings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Lint Obsidian article revisions for chat wording leakage.")
    parser.add_argument("--file", required=True, help="Absolute or relative path to the edited Markdown file.")
    parser.add_argument("--repo", help="Vault Git repository root. When set, staged and unstaged added diff lines are scanned.")
    parser.add_argument("--all", action="store_true", help="Scan the whole file instead of only git diff additions.")
    parser.add_argument(
        "--style-terms",
        help="Optional YAML-like file with suspicious_terms and allowed_terms lists.",
    )
    parser.add_argument(
        "--style-profile",
        choices=["chinese-longform"],
        help="Optional conservative profile for long Chinese prose; emits review prompts only.",
    )
    parser.add_argument(
        "--baseline",
        help="Optional pre-edit Markdown snapshot used to compare semantic sentinels after rewriting.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit structured JSON while preserving the existing exit-code contract.",
    )
    args = parser.parse_args()

    file_path = pathlib.Path(args.file)
    if not file_path.exists():
        if args.json_output:
            print(json.dumps({"schema_version": 1, "ok": False, "error": f"file not found: {file_path}"}, ensure_ascii=False))
        else:
            print(f"error: file not found: {file_path}", file=sys.stderr)
        return 2

    baseline_path = pathlib.Path(args.baseline) if args.baseline else None
    if baseline_path is not None and not baseline_path.exists():
        if args.json_output:
            print(json.dumps({"schema_version": 1, "ok": False, "error": f"baseline not found: {baseline_path}"}, ensure_ascii=False))
        else:
            print(f"error: baseline not found: {baseline_path}", file=sys.stderr)
        return 2

    candidates: list[CandidateLine] = []
    warnings: list[WarningItem] = []
    used_git_diff = bool(args.repo and not args.all)
    if used_git_diff:
        repo_path = pathlib.Path(args.repo)
        rel_path, repo_warning = repo_relative_path(repo_path, file_path)
        if repo_warning:
            warnings.append(repo_warning)
            candidates = read_file_candidates(file_path)
        else:
            for cached in (False, True):
                diff_candidates, diff_warning = run_git_diff(repo_path, rel_path, cached=cached)
                candidates.extend(diff_candidates)
                if diff_warning:
                    warnings.append(diff_warning)

            if not candidates:
                is_tracked, tracked_warning = git_path_is_tracked(repo_path, rel_path)
                if tracked_warning:
                    warnings.append(tracked_warning)
                if not is_tracked:
                    candidates = read_file_candidates(file_path, source="file-untracked")
                elif warnings:
                    candidates = read_file_candidates(file_path)

    if not candidates and not used_git_diff:
        candidates = read_file_candidates(file_path)

    candidates = filter_fenced_candidates(candidates, file_path)
    default_style_terms = pathlib.Path(__file__).resolve().parents[1] / "references" / "style_terms.yml"
    style_terms_path = pathlib.Path(args.style_terms) if args.style_terms else default_style_terms
    try:
        style_profile = load_longform_style_profile(args.style_profile)
    except ValueError as exc:
        if args.json_output:
            print(json.dumps({"schema_version": 1, "ok": False, "error": f"profile error: {exc}"}, ensure_ascii=False))
        else:
            print(f"article revision lint: profile error: {exc}")
        return 2
    warnings.extend(scan_lines(candidates, load_style_terms(style_terms_path), style_profile))
    warnings.extend(scan_structural_patterns(candidates))
    if style_profile is not None:
        warnings.extend(scan_longform_context_patterns(candidates))
    warnings.extend(scan_formula_delimiters_and_spacing(candidates))
    warnings.extend(scan_compact_display_formula(candidates))
    warnings.extend(detect_line_endings(file_path))
    if baseline_path is not None:
        warnings.extend(compare_semantic_sentinels(baseline_path, file_path))

    scan_scope = "whole_file" if args.all else ("git_diff" if used_git_diff else "file")
    if args.json_output:
        print(
            json.dumps(
                json_result(
                    warnings,
                    file_path=file_path,
                    scan_scope=scan_scope,
                    profile=args.style_profile,
                    baseline=baseline_path,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1 if warnings else 0

    if not warnings:
        print("article revision lint: no warnings")
        return 0

    print("article revision lint: warnings")
    for warning in warnings:
        location = f"line {warning.line_no}" if warning.line_no else "file"
        rule_id = warning.rule_id or warning.code
        prefix = f"{warning.message} " if warning.message else ""
        rule_suffix = f"[{rule_id}]: " if rule_id != warning.code else ""
        print(f"- [{warning.code}] {location} ({warning.source}): {prefix}{rule_suffix}{warning.text}")
    return 1


if __name__ == "__main__":
    code = main()
    flush_observer()
    raise SystemExit(code)
