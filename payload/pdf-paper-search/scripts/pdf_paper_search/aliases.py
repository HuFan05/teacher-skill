from __future__ import annotations

import re

from .normalization import (
    BENCHMARK_DATASETS,
    BENCHMARK_METRICS,
    MODEL_VARIANT_RE,
    exact_anchor_fingerprints,
    normalize_paper_query,
)


WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")

SHORT_SIGNATURE_TERMS = {
    "kl",
    "lr",
    "mu",
    "nu",
    "pi",
    "qk",
    "rl",
    "xi",
}

# Hard concepts are distinctions that must survive search and verification.
# Patterns run on the canonical form; contrast partners are guarded so that
# `nonconvex` never counts as `convex` and `self-supervised` never counts as
# `supervised`.
HARD_CONCEPT_PATTERNS: dict[str, re.Pattern[str]] = {
    name: re.compile(pattern)
    for name, pattern in {
        "upper_bound": r"\bupper[\s-]bounds?\b",
        # Omega-notation states a lower bound even when the words are absent.
        "lower_bound": r"\blower[\s-]bounds?\b|(?<![a-z0-9_])omega\s*\(",
        "worst_case": r"\bworst[\s-]case\b",
        "average_case": r"\baverage[\s-]case\b",
        "amortized": r"\bamorti[sz]ed\b",
        "deterministic": r"(?<!non-)(?<!non )\bdeterministic\b",
        "randomized": r"\brandomi[sz]ed\b",
        "time_complexity": r"\btime(?:\s+and\s+space)?[\s-]complexit(?:y|ies)\b|\brunning[\s-]time\b",
        "space_complexity": r"\b(?:time\s+and\s+)?(?:space|memory)[\s-]complexit(?:y|ies)\b",
        "np_hard": r"\bnp[\s-]hard(?:ness)?\b",
        "np_complete": r"\bnp[\s-]complete(?:ness)?\b",
        "approximation_algorithm": r"\bapproximation[\s-](?:algorithms?|ratios?|factors?|schemes?)\b",
        "polynomial_time": r"\bpolynomial[\s-]time\b",
        "self_attention": r"\bself[\s-]attention\b",
        "cross_attention": r"\bcross[\s-]attention\b|\bencoder[\s-]decoder[\s-]attention\b",
        "multi_head_attention": r"\bmulti[\s-]?head(?:ed)?[\s-]attention\b",
        "batch_normalization": r"\bbatch[\s-]?norm(?:ali[sz]ation)?\b",
        "layer_normalization": r"\blayer[\s-]?norm(?:ali[sz]ation)?\b",
        "residual_connection": r"\bresidual[\s-](?:connections?|blocks?|learning|mappings?)\b|\bskip[\s-]connections?\b",
        "backpropagation": r"\bback[\s-]?propagation\b|\bbackprop\b",
        "vanishing_gradient": r"\bvanishing[\s-]gradients?\b|\bgradients?[\s-]vanish",
        "exploding_gradient": r"\bexploding[\s-]gradients?\b|\bgradients?[\s-]explod",
        "stochastic_gradient_descent": r"\bstochastic[\s-]gradient[\s-]descent\b|\bsgd\b",
        "convex": r"(?<!non-)(?<!non )\bconvex\b",
        "nonconvex": r"\bnon[\s-]?convex\b",
        "cross_entropy": r"\bcross[\s-]entropy\b",
        "kl_divergence": r"\bkl[\s-]divergence\b|\bkullback[\s-]leibler\b",
        "pretraining": r"\bpre[\s-]?train(?:ing|ed)?\b",
        "fine_tuning": r"\bfine[\s-]?tun(?:ing|ed|e)\b",
        "zero_shot": r"\bzero[\s-]shot\b",
        "few_shot": r"\bfew[\s-]shot\b",
        "supervised": r"(?<!self-)(?<!self )(?<!semi-)(?<!semi )\bsupervised\b",
        "self_supervised": r"\bself[\s-]supervis(?:ed|ion)\b",
        "unsupervised": r"\bunsupervised\b",
        "encoder_only": r"\bencoder[\s-]only\b",
        "decoder_only": r"\bdecoder[\s-]only\b",
        "reinforcement_learning": r"\breinforcement[\s-]learning\b",
        "on_policy": r"\bon-policy\b",
        "off_policy": r"\boff[\s-]policy\b",
        "model_based": r"\bmodel-based\b",
        "model_free": r"\bmodel[\s-]free\b",
        "policy_gradient": r"\bpolicy[\s-]gradients?\b",
        "regret": r"\bregret\b",
        "top1_accuracy": r"\btop[\s-]?1\b",
        "top5_accuracy": r"\btop[\s-]?5\b",
    }.items()
}

# Soft concepts are architecture or setting families. Missing one lowers the
# score, but it cannot by itself turn a direct statement into a different claim.
SOFT_CONCEPT_PATTERNS: dict[str, re.Pattern[str]] = {
    name: re.compile(pattern)
    for name, pattern in {
        "transformer": r"\btransformers?\b",
        "convolutional": r"\bconvolution(?:al)?\b|\bcnns?\b",
        "recurrent": r"\brecurrent\b|\brnns?\b|\blstms?\b|\bgrus?\b",
        "graph_neural_network": r"\bgraph[\s-]neural[\s-]networks?\b|\bgnns?\b",
        "diffusion": r"\bdiffusion\b",
        "generative_adversarial": r"\bgenerative[\s-]adversarial\b|\bgans?\b",
        "language_model": r"\blanguage[\s-]models?\b|\bllms?\b",
    }.items()
}

CONCEPT_LABELS = {
    "upper_bound": "upper bound",
    "lower_bound": "lower bound",
    "worst_case": "worst case",
    "average_case": "average case",
    "amortized": "amortized",
    "deterministic": "deterministic",
    "randomized": "randomized",
    "time_complexity": "time complexity",
    "space_complexity": "space complexity",
    "np_hard": "NP-hard",
    "np_complete": "NP-complete",
    "approximation_algorithm": "approximation algorithm",
    "polynomial_time": "polynomial time",
    "self_attention": "self-attention",
    "cross_attention": "cross-attention",
    "multi_head_attention": "multi-head attention",
    "batch_normalization": "batch normalization",
    "layer_normalization": "layer normalization",
    "residual_connection": "residual connection",
    "backpropagation": "backpropagation",
    "vanishing_gradient": "vanishing gradient",
    "exploding_gradient": "exploding gradient",
    "stochastic_gradient_descent": "stochastic gradient descent",
    "convex": "convex",
    "nonconvex": "nonconvex",
    "cross_entropy": "cross-entropy",
    "kl_divergence": "KL divergence",
    "pretraining": "pretraining",
    "fine_tuning": "fine-tuning",
    "zero_shot": "zero-shot",
    "few_shot": "few-shot",
    "supervised": "supervised",
    "self_supervised": "self-supervised",
    "unsupervised": "unsupervised",
    "encoder_only": "encoder-only",
    "decoder_only": "decoder-only",
    "reinforcement_learning": "reinforcement learning",
    "on_policy": "on-policy",
    "off_policy": "off-policy",
    "model_based": "model-based",
    "model_free": "model-free",
    "policy_gradient": "policy gradient",
    "regret": "regret",
    "top1_accuracy": "top-1",
    "top5_accuracy": "top-5",
}

CONCEPT_RESCUE_TERMS = {
    name: tuple(
        part
        for part in re.split(r"[\s-]+", label.casefold())
        if part and part not in {"1", "5"}
    )
    or (label.casefold(),)
    for name, label in CONCEPT_LABELS.items()
}
CONCEPT_RESCUE_TERMS["top1_accuracy"] = ("top-1",)
CONCEPT_RESCUE_TERMS["top5_accuracy"] = ("top-5",)

RESCUE_SIGNAL_TERMS = {
    "argmax",
    "argmin",
    "kl",
    "log",
    "pi",
    "softmax",
    "sqrt",
}

NUMBERED_LABEL_RE = re.compile(
    r"\b(algorithm|alg|table|tab|figure|fig|theorem|thm|lemma|lem|definition|def|"
    r"proposition|prop|corollary|cor|equation|eq)\.?\s*\(?\s*(\d{1,3}(?:\.\d{1,3})*)\s*\)?"
)
LABEL_CANONICAL = {
    "alg": "algorithm",
    "tab": "table",
    "fig": "figure",
    "thm": "theorem",
    "lem": "lemma",
    "def": "definition",
    "prop": "proposition",
    "cor": "corollary",
    "eq": "equation",
}
ARXIV_ID_RE = re.compile(r"(?<![\d.])(\d{4}\.\d{4,5})(?:v\d+)?(?![\d])")


def unique_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = value.strip()
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def detect_hard_concepts(text: str) -> set[str]:
    normalized = normalize_paper_query(text).casefold()
    return {
        name
        for name, pattern in HARD_CONCEPT_PATTERNS.items()
        if pattern.search(normalized)
    }


def detect_soft_concepts(text: str) -> set[str]:
    normalized = normalize_paper_query(text).casefold()
    return {
        name
        for name, pattern in SOFT_CONCEPT_PATTERNS.items()
        if pattern.search(normalized)
    }


def concept_positions(name: str, normalized: str) -> list[int]:
    pattern = HARD_CONCEPT_PATTERNS.get(name) or SOFT_CONCEPT_PATTERNS.get(name)
    if pattern is None:
        return []
    return [match.start() for match in pattern.finditer(normalized)]


def arxiv_ids(text: str) -> list[str]:
    return unique_keep_order([match.group(1) for match in ARXIV_ID_RE.finditer(str(text))])


def structural_cue_terms(text: str) -> list[str]:
    """Return numbered objects, identifiers, and variants that locate a source."""

    normalized = normalize_paper_query(text).casefold()
    terms: list[str] = []
    for match in NUMBERED_LABEL_RE.finditer(normalized):
        label = LABEL_CANONICAL.get(match.group(1), match.group(1))
        terms.append(f"{label} {match.group(2)}")
    terms.extend(arxiv_ids(text))
    for match in MODEL_VARIANT_RE.finditer(normalized):
        terms.append(f"{match.group(1)}-{match.group(2)}")
    for forms in (*BENCHMARK_DATASETS.values(), *BENCHMARK_METRICS.values()):
        for form in forms:
            if len(form) < 3:
                continue
            if re.search(rf"(?<![a-z0-9]){re.escape(form)}(?![a-z0-9])", normalized):
                terms.append(form)
                break
    return unique_keep_order(terms)


def term_variants(term: str) -> tuple[str, ...]:
    """Bridge hyphen/space/underscore spellings such as ResNet-50 vs ResNet50."""

    lowered = term.casefold()
    variants = [lowered]
    if "-" in lowered:
        variants.extend([lowered.replace("-", ""), lowered.replace("-", " ")])
    if "_" in lowered:
        variants.extend([lowered.replace("_", " "), lowered.replace("_", "")])
    return tuple(dict.fromkeys(variants))


def _expression_words(expression: str) -> str:
    return " ".join(re.findall(r"log|sqrt|epsilon|[a-z]\d*|\d+", expression))


# One FTS chunk per form: `a/b` becomes an OR group in the local match builder,
# which bridges typographic variants without widening the conjunctive packet.
FTS_VARIANTS = {
    "log": "log/lg",
    "imagenet": "imagenet",
    "cifar10": "cifar-10/cifar10",
    "cifar100": "cifar-100/cifar100",
    "coco": "coco/mscoco",
    "wmt14": "wmt14/newstest2014/wmt",
    "top1": "top-1/top1",
    "top5": "top-5/top5",
    "perplexity": "perplexity/ppl",
    "accuracy": "accuracy/acc",
}


def _fts_expression(expression: str) -> str:
    return " ".join(FTS_VARIANTS.get(word, word) for word in _expression_words(expression).split())


def source_location_aliases(text: str) -> list[str]:
    normalized = normalize_paper_query(text).casefold()
    hard_concepts = detect_hard_concepts(text)
    anchors = exact_anchor_fingerprints(text)
    aliases: list[str] = []
    for anchor in anchors:
        family, _, value = anchor.partition(":")
        if family == "attention":
            aliases.append("scaled dot-product attention softmax")
            aliases.append("softmax qk sqrt")
        elif family == "model":
            name, _, variant = value.partition("-")
            aliases.append(f"{name}-{variant.replace('/', ' ')}")
            if re.fullmatch(r"\d+(?:\.\d)?", variant):
                aliases.append(f"{name}{variant}")
        elif family == "benchmark":
            dataset, _, metric = value.partition(":")
            dataset_form = BENCHMARK_DATASETS.get(dataset, (dataset,))[0]
            metric_form = BENCHMARK_METRICS.get(metric, (metric,))[0]
            aliases.append(f"{dataset_form} {metric_form}")
        elif family == "complexity":
            _kind, _, expression = value.partition(":")
            words = _expression_words(expression)
            bound_terms = [
                CONCEPT_LABELS[name]
                for name in ("lower_bound", "upper_bound", "worst_case", "time_complexity")
                if name in hard_concepts
            ]
            if words and bound_terms:
                aliases.append(f"{' '.join(bound_terms[:2])} {words}")
    if "scaled dot-product" in normalized or "scaled dot product" in normalized:
        aliases.append("scaled dot-product attention")
    for identifier in arxiv_ids(text):
        aliases.append(f"arxiv {identifier}")
    ordered = [name for name in CONCEPT_LABELS if name in hard_concepts]
    if len(ordered) >= 2:
        aliases.append(" ".join(CONCEPT_LABELS[name] for name in ordered[:3]))
    return unique_keep_order(aliases)


def exact_anchor_queries(text: str) -> tuple[str, ...]:
    """Return a small FTS-ready rescue set for structural exact anchors."""

    hard_concepts = detect_hard_concepts(text)
    concept_terms: list[str] = []
    for concept in sorted(hard_concepts):
        concept_terms.extend(CONCEPT_RESCUE_TERMS.get(concept, ()))
    concept_head = " ".join(unique_keep_order(concept_terms)[:3])
    queries: list[str] = []
    anchors = exact_anchor_fingerprints(text)
    context_terms = [
        anchor.split(":", 2)[1]
        for anchor in anchors
        if anchor.startswith("benchmark:")
    ]
    for anchor in anchors:
        family, _, value = anchor.partition(":")
        if family == "complexity":
            kind, _, expression = value.partition(":")
            words = _fts_expression(expression)
            if words:
                queries.append(" ".join(part for part in (words, concept_head) if part))
                if kind in {"theta", "omega"}:
                    queries.append(f"{words} {kind}")
        elif family == "attention":
            queries.extend(["softmax qk sqrt", "scaled dot-product attention softmax"])
        elif family == "model":
            name, _, variant = value.partition("-")
            if "/" in variant:
                model_form = f"{name}-{variant.replace('/', ' ')}"
            else:
                model_form = f"{name}-{variant}/{name}{variant}"
            context = FTS_VARIANTS.get(context_terms[0], context_terms[0]) if context_terms else ""
            queries.append(" ".join(part for part in (model_form, context) if part))
        elif family == "benchmark":
            dataset, _, metric = value.partition(":")
            queries.append(f"{FTS_VARIANTS.get(dataset, dataset)} {FTS_VARIANTS.get(metric, metric)}")
        elif family == "number":
            metric_terms = [
                anchor.rsplit(":", 1)[1] for anchor in anchors if anchor.startswith("benchmark:")
            ]
            if metric_terms:
                queries.append(f"{value} {FTS_VARIANTS.get(metric_terms[0], metric_terms[0])}")
    return tuple(unique_keep_order(queries)[:3])


def signature_rescue_queries(text: str) -> tuple[str, ...]:
    """Build a few strict FTS packets from reusable concepts and formula tokens."""

    hard_concepts = detect_hard_concepts(text)
    concept_terms: list[str] = []
    for concept in sorted(hard_concepts):
        concept_terms.extend(CONCEPT_RESCUE_TERMS.get(concept, ()))
    concept_terms = unique_keep_order(concept_terms)

    signatures = list(signature_terms([text], limit=30))
    structural_terms = [
        term
        for term in signatures
        if (any(character.isdigit() for character in term) and "_" not in term and " " not in term)
        or term in RESCUE_SIGNAL_TERMS
    ]
    structural_terms = unique_keep_order(structural_terms)

    combined = unique_keep_order([*concept_terms, *structural_terms])
    if len(combined) < 3:
        return ()

    queries: list[str] = [" ".join(combined[:5])]
    if len(concept_terms) >= 2 and structural_terms:
        queries.append(" ".join(unique_keep_order([*concept_terms, structural_terms[0]])[:5]))
    if len(concept_terms) >= 3 and structural_terms:
        queries.append(
            " ".join(
                unique_keep_order([*concept_terms[:3], *structural_terms[:2]])[:5]
            )
        )
    return tuple(unique_keep_order(queries)[:3])


def signature_terms(texts: list[str], *, limit: int = 18) -> tuple[str, ...]:
    terms: list[str] = []
    for text in texts:
        normalized = normalize_paper_query(text).casefold()
        terms.extend(structural_cue_terms(text))
        terms.extend(
            word
            for word in WORD_RE.findall(normalized)
            if len(word) >= 3
            or word in SHORT_SIGNATURE_TERMS
            or any(character.isdigit() for character in word)
        )
    return tuple(unique_keep_order(terms)[:limit])
