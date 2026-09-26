from __future__ import annotations

import re
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SKILL_TEXT = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
CONTRACT = (
    SKILL_ROOT / "references" / "search-evaluation-contract.md"
).read_text(encoding="utf-8")


class SearchEvaluationContractTests(unittest.TestCase):
    def test_skill_routes_formal_evaluation_to_user_run_contract(self) -> None:
        self.assertIn("references/search-evaluation-contract.md", SKILL_TEXT)
        self.assertIn("optional, user-run evaluation", SKILL_TEXT)
        self.assertIn("ships no blind-evaluation", SKILL_TEXT)
        self.assertIn("cannot by themselves establish retrieval quality", SKILL_TEXT)

    def test_contract_preserves_locator_and_coverage_rules(self) -> None:
        required = [
            "knowledge-root-relative PDF path",
            "one-based PDF page",
            "Vault-relative note path",
            "group Vault results and PDF results",
            "`no_text`",
            "`error`",
            "currently indexed text",
            "`KB-...`",
        ]
        for phrase in required:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, CONTRACT)

    def test_multi_source_and_formal_routing_are_explicit(self) -> None:
        self.assertRegex(
            CONTRACT,
            re.compile(
                r"every source listed in gold must independently\s+"
                r"satisfy the complete query"
            ),
        )
        self.assertRegex(
            CONTRACT,
            re.compile(
                r"Use a separate, user-run evaluation for formal blind datasets,"
                r"\s+isolated\s+baseline/candidate runs"
            ),
        )
        self.assertIn("ordinary latency diagnosis", CONTRACT)

    def test_reference_contains_no_real_private_identifiers(self) -> None:
        self.assertNotIn(r"C:\Users", CONTRACT)
        self.assertNotRegex(
            CONTRACT,
            re.compile(
                r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
                r"[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
                re.IGNORECASE,
            ),
        )
        self.assertNotRegex(CONTRACT, re.compile(r"\b[0-9a-f]{64}\b"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
