from __future__ import annotations

import json
import re
import unittest

from pdf_paper_search.query_spec import build_query_spec


CASES = [
    {
        "name": "comparison-sort lower bound",
        "query": "Every comparison-based sorting algorithm has an Ω(n log n) lower bound in the worst case",
        "hard": {"lower_bound", "worst_case"},
        "anchors": {"complexity:omega:nlogn"},
        "query_type": "theorem_lookup",
    },
    {
        "name": "upper vs lower bound distinction",
        "query": "An upper bound is not the same as a lower bound",
        "hard": {"upper_bound", "lower_bound"},
        "query_type": "theorem_lookup",
    },
    {
        "name": "on-policy vs off-policy distinction",
        "query": "on-policy policy gradient is unbiased but off-policy estimators are not",
        "hard": {"on_policy", "off_policy", "policy_gradient"},
        "query_type": "claim_lookup",
    },
    {
        "name": "self-supervised does not imply supervised",
        "query": "self-supervised contrastive learning of visual representations",
        "hard": {"self_supervised"},
        "absent": {"supervised"},
    },
    {
        "name": "self-supervised vs supervised pretraining",
        "query": "self-supervised pretraining outperforms supervised pretraining",
        "hard": {"self_supervised", "supervised", "pretraining"},
        "query_type": "claim_lookup",
    },
    {
        "name": "nonconvex does not imply convex",
        "query": "SGD converges to a stationary point for nonconvex objectives",
        "hard": {"nonconvex", "stochastic_gradient_descent"},
        "absent": {"convex"},
        "query_type": "theorem_lookup",
    },
    {
        "name": "scaled dot-product attention formula",
        "query": "softmax(QK^T/√d_k)V scaled dot-product attention in the Transformer",
        "anchors": {"attention:scaled-dot-product"},
        "symbols": {"softmax", "d_k"},
        "query_type": "equation_lookup",
    },
    {
        "name": "numbered algorithm box",
        "query": "Algorithm 1 Adam stochastic optimization pseudocode",
        "symbols": {"algorithm 1"},
        "query_type": "algorithm_lookup",
    },
    {
        "name": "results table with model variant and benchmark number",
        "query": "Table 2 ResNet-50 top-1 accuracy on ImageNet 76.1",
        "hard": {"top1_accuracy"},
        "anchors": {"model:resnet-50", "benchmark:imagenet:top1", "number:76.1"},
        "symbols": {"table 2", "resnet-50", "imagenet"},
        "query_type": "result_table_lookup",
        "alias": "resnet50",
    },
    {
        "name": "Chinese attention complexity",
        "query": "证明自注意力的时间复杂度是 O(n^2)",
        "hard": {"self_attention", "time_complexity"},
        "anchors": {"complexity:o:n2"},
        "query_type": "theorem_lookup",
    },
    {
        "name": "Chinese reinforcement-learning contrast",
        "query": "强化学习中同策略与异策略方法的区别",
        "hard": {"reinforcement_learning", "on_policy", "off_policy"},
    },
    {
        "name": "Chinese numbered figure",
        "query": "图 3 Transformer 模型架构",
        "symbols": {"figure 3"},
        "query_type": "figure_lookup",
    },
    {
        "name": "OCR damaged algorithm number",
        "query": "Algorlthm 2.l off-policy Q-learning with experience replay",
        "hard": {"off_policy"},
    },
    {
        "name": "arXiv identifier",
        "query": "arXiv 1706.03762 multi-head attention",
        "hard": {"multi_head_attention"},
        "symbols": {"1706.03762"},
        "alias": "arxiv 1706.03762",
    },
    {
        "name": "Big-O with set sizes",
        "query": "Dijkstra with a Fibonacci heap runs in O(|E| + |V| lg |V|) time",
        "anchors": {"complexity:o:e+vlogv"},
        "query_type": "theorem_lookup",
    },
]


def assert_no_noisy_standalone_aliases(aliases: tuple[str, ...]) -> None:
    for alias in aliases:
        cleaned = alias.strip()
        if re.fullmatch(r"[A-Za-z]", cleaned):
            raise AssertionError(f"noisy standalone alias: {alias!r}")
        if re.fullmatch(r"[_\\^{}()[\\].,;:=-]+", cleaned):
            raise AssertionError(f"symbol-only alias: {alias!r}")


def run_cases() -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for case in CASES:
        spec = build_query_spec(case["query"])
        expected_hard = set(case.get("hard", set()))
        missing_hard = sorted(expected_hard - set(spec.hard_concepts))
        if missing_hard:
            raise AssertionError(f"{case['name']}: missing hard concepts {missing_hard}")
        leaked = sorted(set(case.get("absent", set())) & set(spec.hard_concepts))
        if leaked:
            raise AssertionError(f"{case['name']}: contrast concept leaked {leaked}")
        expected_symbols = set(case.get("symbols", set()))
        missing_symbols = sorted(expected_symbols - set(spec.signature_terms))
        if missing_symbols:
            raise AssertionError(f"{case['name']}: missing structural terms {missing_symbols}")
        expected_anchors = set(case.get("anchors", set()))
        missing_anchors = sorted(expected_anchors - set(spec.exact_anchors))
        if missing_anchors:
            raise AssertionError(f"{case['name']}: missing exact anchors {missing_anchors}")
        expected_type = case.get("query_type")
        if expected_type and spec.query_type != expected_type:
            raise AssertionError(f"{case['name']}: expected {expected_type}, got {spec.query_type}")
        expected_alias = case.get("alias")
        if expected_alias and expected_alias not in spec.aliases:
            raise AssertionError(
                f"{case['name']}: missing compact alias {expected_alias!r}"
            )
        if len(spec.aliases) > 10:
            raise AssertionError(f"{case['name']}: alias count exceeded 10")
        assert_no_noisy_standalone_aliases(spec.aliases)
        results.append(
            {
                "name": case["name"],
                "query_type": spec.query_type,
                "hard_concepts": sorted(spec.hard_concepts),
                "signature_terms": list(spec.signature_terms),
                "exact_anchors": list(spec.exact_anchors),
                "alias_count": len(spec.aliases),
            }
        )
    return results


class QuerySpecParityTests(unittest.TestCase):
    def test_query_spec_cases(self) -> None:
        self.assertEqual(len(run_cases()), len(CASES))


def main() -> int:
    results = run_cases()
    print(json.dumps({"passed": len(results), "results": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
