from __future__ import annotations

import re
import unicodedata


PAPER_CANONICALIZER_VERSION = "paper-canonical/v1"

GREEK_REPLACEMENTS = {
    "\\varepsilon": "epsilon",
    "\\epsilon": "epsilon",
    "ϵ": "epsilon",
    "ε": "epsilon",
    "\\vartheta": "theta",
    "\\theta": "theta",
    "\\Theta": "theta",
    "ϑ": "theta",
    "θ": "theta",
    "Θ": "theta",
    "\\varphi": "phi",
    "\\phi": "phi",
    "ϕ": "phi",
    "φ": "phi",
    "\\alpha": "alpha",
    "α": "alpha",
    "\\beta": "beta",
    "β": "beta",
    "\\gamma": "gamma",
    "γ": "gamma",
    "\\delta": "delta",
    "\\Delta": "delta",
    "δ": "delta",
    "Δ": "delta",
    "\\eta": "eta",
    "η": "eta",
    "\\lambda": "lambda",
    "λ": "lambda",
    "\\mu": "mu",
    "μ": "mu",
    "µ": "mu",
    "\\nu": "nu",
    "ν": "nu",
    "\\pi": "pi",
    "Π": "pi",
    "π": "pi",
    "\\rho": "rho",
    "ρ": "rho",
    "\\sigma": "sigma",
    "σ": "sigma",
    "Σ": "sigma",
    "\\tau": "tau",
    "τ": "tau",
    "\\xi": "xi",
    "ξ": "xi",
    "\\omega": "omega",
    "\\Omega": "omega",
    "ω": "omega",
    "Ω": "omega",
    "\\nabla": "nabla",
    "∇": "nabla",
    "\\partial": "partial",
    "∂": "partial",
    "\\leq": "<=",
    "\\le": "<=",
    "\\geq": ">=",
    "\\ge": ">=",
    # Common mojibake produced by older PDF extraction paths.
    "蠁": "phi",
    "蠒": "phi",
    "渭": "mu",
    "蟽": "sigma",
    "蟺": "pi",
}

CHINESE_TERM_REPLACEMENTS = {
    "自注意力": "self-attention",
    "交叉注意力": "cross-attention",
    "多头注意力": "multi-head attention",
    "注意力": "attention",
    "反向传播": "backpropagation",
    "梯度消失": "vanishing gradient",
    "梯度爆炸": "exploding gradient",
    "随机梯度下降": "stochastic gradient descent",
    "梯度下降": "gradient descent",
    "强化学习": "reinforcement learning",
    "自监督": "self-supervised",
    "无监督": "unsupervised",
    "监督学习": "supervised learning",
    "时间复杂度": "time complexity",
    "空间复杂度": "space complexity",
    "近似算法": "approximation algorithm",
    "近似比": "approximation ratio",
    "多项式时间": "polynomial time",
    "NP难": "NP-hard",
    "NP完全": "NP-complete",
    "上界": "upper bound",
    "下界": "lower bound",
    "最坏情况": "worst case",
    "平均情况": "average case",
    "均摊": "amortized",
    "随机化": "randomized",
    "确定性": "deterministic",
    "损失函数": "loss function",
    "目标函数": "objective function",
    "交叉熵": "cross-entropy",
    "卷积神经网络": "convolutional neural network",
    "循环神经网络": "recurrent neural network",
    "残差连接": "residual connection",
    "批归一化": "batch normalization",
    "层归一化": "layer normalization",
    "预训练": "pretraining",
    "微调": "fine-tuning",
    "零样本": "zero-shot",
    "少样本": "few-shot",
    "同策略": "on-policy",
    "异策略": "off-policy",
    "无模型": "model-free",
    "基于模型": "model-based",
    "策略梯度": "policy gradient",
    "遗憾界": "regret bound",
    "编码器": "encoder",
    "解码器": "decoder",
    "非凸": "nonconvex",
    "准确率": "accuracy",
    "实验结果": "experimental results",
    "定理": "theorem",
    "引理": "lemma",
    "命题": "proposition",
    "推论": "corollary",
    "定义": "definition",
    "算法": "algorithm",
    "伪代码": "pseudocode",
    "证明": "prove",
    "出处": "source",
}

CJK_NUMBERED_LABELS = (
    (re.compile(r"表\s*(\d+)"), r" table \1 "),
    (re.compile(r"图\s*(\d+)"), r" figure \1 "),
)

UNICODE_REPLACEMENTS = {
    "\\infty": " infinity ",
    "\\sum": " sum ",
    "\\sqrt": " sqrt ",
    "\\log": " log ",
    "\\exp": " exp ",
    "\\ldots": " ",
    "\\cdots": " ",
    "\\cdot": " ",
    "\\top": " t ",
    "→": " -> ",
    "⇒": " implies ",
    "∞": " infinity ",
    "√": " sqrt ",
    "ℝ": " real ",
    "≤": " <= ",
    "≥": " >= ",
    "≠": " != ",
    "≈": " approx ",
    "−": " - ",
    "–": " - ",
    "—": " - ",
    "×": " times ",
    "·": " ",
    "∑": " sum ",
}

SUPERSCRIPT_TRANSLATION = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻", "0123456789+-")
SUBSCRIPT_TRANSLATION = str.maketrans("₀₁₂₃₄₅₆₇₈₉₊₋", "0123456789+-")
TEX_WRAPPER_RE = re.compile(
    r"\\(?:operatorname|mathrm|mathbf|mathcal|mathbb|boldsymbol|text|textbf|textit)\s*\{\s*([^{}]*)\}"
)

# Bounded structural anchors. Each family is small on purpose: a wider bridge
# would admit similar-but-different statements as exact evidence.
COMPLEXITY_BODY = r"\s*\(\s*([^()]{1,48}(?:\([^()]{0,16}\)[^()]{0,24})?)\)"
# Query side: Big-O notation is case sensitive, so a lowercase parameter such
# as f_theta(x) is never promoted to a complexity anchor.
COMPLEXITY_ANCHOR_RE = re.compile(r"(?<![A-Za-z])(O|Theta|Omega|Θ|Ω)" + COMPLEXITY_BODY)
# Candidate side: canonical page text is casefolded, so accept folded forms.
COMPLEXITY_FOLDED_RE = re.compile(
    r"(?<![a-z0-9_])(o|theta|omega|θ|ω)" + COMPLEXITY_BODY, re.IGNORECASE
)
COMPLEXITY_KINDS = {"o": "o", "theta": "theta", "θ": "theta", "omega": "omega", "ω": "omega"}
COMPLEXITY_EXPR_RE = re.compile(r"^[a-z0-9+\-/.,()]+$")
COMPLEXITY_VARIABLES = ("n", "m", "k", "v", "e", "t", "d", "1", "log", "sqrt", "epsilon")
ATTENTION_ANCHOR_RE = re.compile(
    r"softmax\(?qk\^?t(?:op)?/?(?:sqrt)?\(?d_?k\)?\)?v"
)
MODEL_FAMILIES = (
    "efficientnet",
    "mobilenet",
    "densenet",
    "roberta",
    "resnet",
    "llama",
    "bert",
    "deit",
    "swin",
    "gpt",
    "vgg",
    "vit",
    "t5",
)
MODEL_VARIANT_RE = re.compile(
    r"\b(" + "|".join(MODEL_FAMILIES) + r")[\s_-]?"
    r"(base|large|small|tiny|huge|xxl|xl|[bslh]/\d{1,2}|b\d|v\d|\d{1,3}(?:\.\d)?[bm]?)\b"
)
BENCHMARK_DATASETS = {
    "imagenet": ("imagenet",),
    "cifar10": ("cifar-10", "cifar 10", "cifar10"),
    "cifar100": ("cifar-100", "cifar 100", "cifar100"),
    "mnist": ("mnist",),
    "coco": ("coco",),
    "squad": ("squad",),
    "glue": ("glue",),
    "wmt14": ("wmt14", "wmt 2014", "wmt'14", "wmt 14", "newstest2014"),
}
BENCHMARK_METRICS = {
    "top1": ("top-1", "top 1", "top1"),
    "top5": ("top-5", "top 5", "top5"),
    "bleu": ("bleu",),
    "f1": ("f1",),
    "map": ("map", "mean average precision"),
    "perplexity": ("perplexity",),
    "accuracy": ("accuracy",),
}
RESULT_NUMBER_RE = re.compile(
    r"(?<![\w.])(\d{1,3}\.\d{1,2})(?![\w.])"
)
NUMBER_LABEL_BEFORE_RE = re.compile(
    r"(?:table|tab|figure|fig|section|sec|algorithm|alg|theorem|lemma|definition|"
    r"proposition|corollary|equation|eq|appendix|chapter|§)\.?\s*\(?$"
)


def _fold_diacritics(text: str) -> str:
    return "".join(
        char
        for char in unicodedata.normalize("NFKD", text)
        if unicodedata.category(char) != "Mn"
    )


def normalize_paper_query(text: str) -> str:
    """Return the versioned canonical form shared by query and index adapters."""

    value = unicodedata.normalize("NFKC", str(text).strip())
    value = re.sub(r"\\(?:left|right|big|Big|bigg|Bigg)(?![a-zA-Z])", " ", value)
    value = TEX_WRAPPER_RE.sub(r" \1 ", value)
    for source, target in sorted(GREEK_REPLACEMENTS.items(), key=lambda item: -len(item[0])):
        value = value.replace(source, f" {target} ")
    for source, target in sorted(CHINESE_TERM_REPLACEMENTS.items(), key=lambda item: -len(item[0])):
        value = value.replace(source, f" {target} ")
    for pattern, replacement in CJK_NUMBERED_LABELS:
        value = pattern.sub(replacement, value)
    for source, target in UNICODE_REPLACEMENTS.items():
        value = value.replace(source, target)
    value = value.translate(SUPERSCRIPT_TRANSLATION).translate(SUBSCRIPT_TRANSLATION)
    value = _fold_diacritics(value).casefold()
    value = re.sub(r"\^\s*\{\s*([a-z0-9]+)\s*\}", r"^\1", value)
    value = re.sub(r"_\s*\{\s*([a-z0-9]+)\s*\}", r"_\1", value)
    value = re.sub(r"\b([a-z]+)\s+_([a-z0-9]+)\b", r"\1_\2", value)
    value = re.sub(
        r"\\frac\s*\{\s*([^{}]+)\s*\}\s*\{\s*([^{}]+)\s*\}",
        r" \1 over \2 ",
        value,
    )
    value = re.sub(
        r"\b([a-z])\s*\^\s*(\d+)\b",
        lambda match: f"{match.group(1)}{match.group(2)}",
        value,
    )
    value = re.sub(
        r"\b([a-z])\s+(\d+)\b",
        lambda match: f"{match.group(1)}{match.group(2)}",
        value,
    )
    value = re.sub(
        r"\b([a-z0-9]+)\s*(?:!=|6=)\s*0\b",
        lambda match: f" {match.group(1)} nonzero ",
        value,
    )
    value = re.sub(r"\\([a-z]+)", r" \1 ", value)
    value = re.sub(r"\blg\b", "log", value)
    value = re.sub(r"(?<=[a-z0-9])[\^+=*](?=[a-z0-9])", " ", value)
    value = re.sub(r"[^0-9a-z_\u3400-\u9fff<>=./:+!()\[\]\-]+", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _anchor_surface(text: str) -> str:
    """Case-preserving surface for bound notation: Big-O is case sensitive."""

    surface = unicodedata.normalize("NFKC", str(text))
    surface = surface.translate(SUPERSCRIPT_TRANSLATION).translate(SUBSCRIPT_TRANSLATION)
    for source, target in (
        ("\\mathcal{O}", "O"),
        ("\\mathcal O", "O"),
        ("\\Theta", "Θ"),
        ("\\Omega", "Ω"),
        ("\\left(", "("),
        ("\\right)", ")"),
        ("\\left", ""),
        ("\\right", ""),
    ):
        surface = surface.replace(source, target)
    return surface


def _complexity_expression(raw: str) -> str | None:
    expression = raw.casefold()
    for source, target in (
        ("\\log", "log"),
        ("\\sqrt", "sqrt"),
        ("√", "sqrt"),
        ("\\epsilon", "epsilon"),
        ("\\varepsilon", "epsilon"),
        ("ε", "epsilon"),
        ("ϵ", "epsilon"),
        ("\\cdot", ""),
        ("·", ""),
        ("⋅", ""),
        ("∗", ""),
        ("−", "-"),
        ("–", "-"),
    ):
        expression = expression.replace(source, target)
    expression = re.sub(r"\blg\b", "log", expression)
    expression = re.sub(r"[\s{}|*^\\]+", "", expression)
    expression = expression.replace("()", "")
    if not expression or not COMPLEXITY_EXPR_RE.fullmatch(expression):
        return None
    if not any(variable in expression for variable in COMPLEXITY_VARIABLES):
        return None
    return expression


def _compact_surface(text: str) -> str:
    compact = unicodedata.normalize("NFKC", str(text))
    compact = compact.translate(SUPERSCRIPT_TRANSLATION).translate(SUBSCRIPT_TRANSLATION)
    compact = TEX_WRAPPER_RE.sub(r"\1", compact)
    compact = compact.replace("\\sqrt", "sqrt").replace("√", "sqrt").replace("\\top", "t")
    compact = compact.replace("ᵀ", "t").replace("⊤", "t")
    compact = _fold_diacritics(compact).casefold()
    compact = compact.replace("\\left", "").replace("\\right", "")
    compact = re.sub(r"[\s{}\\]+", "", compact)
    return compact


def _benchmark_keys(lowered: str, table: dict[str, tuple[str, ...]]) -> list[str]:
    keys: list[str] = []
    for key, forms in table.items():
        for form in forms:
            if re.search(rf"(?<![a-z0-9]){re.escape(form)}(?![a-z0-9])", lowered):
                keys.append(key)
                break
    return keys


def exact_anchor_fingerprints(text: str, *, case_sensitive: bool = True) -> tuple[str, ...]:
    """Extract bounded structural anchors that survive common PDF/OCR variants.

    Query anchors use the case-sensitive bound notation. Candidate evidence is
    matched with ``case_sensitive=False`` because page text may already be in
    canonical (casefolded) form.
    """

    anchors: list[str] = []
    surface = _anchor_surface(text)
    complexity_re = COMPLEXITY_ANCHOR_RE if case_sensitive else COMPLEXITY_FOLDED_RE
    for match in complexity_re.finditer(surface):
        kind = COMPLEXITY_KINDS.get(match.group(1).casefold())
        expression = _complexity_expression(match.group(2))
        if kind and expression:
            anchors.append(f"complexity:{kind}:{expression}")

    if ATTENTION_ANCHOR_RE.search(_compact_surface(text)):
        anchors.append("attention:scaled-dot-product")

    lowered = _fold_diacritics(unicodedata.normalize("NFKC", str(text))).casefold()
    for match in MODEL_VARIANT_RE.finditer(lowered):
        anchors.append(f"model:{match.group(1)}-{match.group(2)}")

    datasets = _benchmark_keys(lowered, BENCHMARK_DATASETS)
    metrics = _benchmark_keys(lowered, BENCHMARK_METRICS)
    if "top1" in metrics or "top5" in metrics:
        metrics = [metric for metric in metrics if metric != "accuracy"]
    for dataset in datasets:
        for metric in metrics:
            anchors.append(f"benchmark:{dataset}:{metric}")
    if datasets or metrics:
        for match in RESULT_NUMBER_RE.finditer(lowered):
            prefix = lowered[max(0, match.start() - 16) : match.start()]
            if NUMBER_LABEL_BEFORE_RE.search(prefix):
                continue
            anchors.append(f"number:{match.group(1)}")
    return tuple(dict.fromkeys(anchors))


def matching_exact_anchors(required: tuple[str, ...], text: str) -> tuple[str, ...]:
    """Return required structural anchors present in candidate text."""

    found = set(exact_anchor_fingerprints(text, case_sensitive=False))
    return tuple(anchor for anchor in required if anchor in found)
