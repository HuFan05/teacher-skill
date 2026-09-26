from __future__ import annotations

import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SKILL = SKILL_ROOT / "SKILL.md"
HANDOFF = SKILL_ROOT / "references" / "writeback-handoff.md"


class WritebackHandoffContractTests(unittest.TestCase):
    def test_handoff_keeps_source_status_and_protected_markdown_separate(self) -> None:
        text = HANDOFF.read_text(encoding="utf-8")
        for required in (
            "目标与授权",
            "截至日期",
            "形式化验证",
            "同行评审发表",
            "证据等级",
            "exact_reproduction",
            "不可改动项",
            "块标识",
            "语义哨兵",
            "EditAudit",
            "lint通过",
        ):
            self.assertIn(required, text)

    def test_manager_routes_writeback_to_obsidian_without_copying_edit_implementation(self) -> None:
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn("$obsidian-vault-notes", text)
        self.assertIn("does not implement Markdown escaping", text)
        self.assertIn("Controlled write-back handoff", text)


if __name__ == "__main__":
    unittest.main()
