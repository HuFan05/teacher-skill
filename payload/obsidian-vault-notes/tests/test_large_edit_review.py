from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "large_edit_review.py"
EXCLUDED = [
    "attachments", "credentials", "external_resources", "linked_note_bodies",
    "raw_logs", "unrelated_frontmatter", "unrelated_sections", "unmanaged_absolute_paths",
]


class LargeEditReviewTests(unittest.TestCase):
    def run_tool(self, *arguments: str) -> tuple[int, dict]:
        completed = subprocess.run(
            [sys.executable, "-B", str(SCRIPT), *arguments],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
        )
        return completed.returncode, json.loads(completed.stdout)

    def write_json(self, path: Path, payload: dict) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def metrics(self, **updates: object) -> dict:
        payload = {
            "schema": "obsidian-large-edit-classification/v1",
            "explicit_project_scale": False,
            "target_count": 1,
            "substantive_prose_target_count": 1,
            "authorized_scope_non_whitespace_chars": 800,
            "changed_non_whitespace_chars": 300,
            "changed_ratio_max": 0.2,
            "top_level_sections_affected": 1,
            "whole_scope_coherence_required": False,
            "mechanical_only": False,
            "single_paragraph_only": True,
            "few_formula_lines_only": False,
            "single_local_subsection_only": False,
            "narrow_repair_only": False,
        }
        payload.update(updates)
        return payload

    def receipt(self, gate_id: str, candidate_hash: str, round_number: int, verdict: str) -> dict:
        checks = {
            "meaning_preservation": "PASS",
            "language_and_author_voice": "PASS",
            "terminology_and_notation": "PASS",
            "format_and_reader_coherence": "PASS",
        }
        findings = []
        if verdict == "FAIL":
            checks["language_and_author_voice"] = "FAIL"
            findings = [{
                "relative_path": "note.md", "location": "paragraph 2", "category": "style",
                "severity": "blocking", "message": "The sentence is not yet natural in context.",
            }]
        return {
            "schema": "obsidian-large-edit-verifier-receipt/v1",
            "gate_id": gate_id,
            "round": round_number,
            "candidate_inventory_sha256": candidate_hash,
            "verdict": verdict,
            "independent_subagent": True,
            "context": {"mode": "changed_spans", "whole_scope_reason": "", "excluded_data_classes": list(EXCLUDED)},
            "checks": checks,
            "findings": findings,
        }

    def deterministic_receipt(self, gate_id: str, candidate_hash: str, round_number: int) -> dict:
        checks = {}
        for index, name in enumerate((
            "utf8_and_path_containment", "markdown_and_frontmatter", "formulas", "wikilinks",
            "resource_references", "task_specific_lint", "diff_and_semantic_sentinels",
        ), 1):
            checks[name] = {"status": "PASS", "evidence_sha256": str(index) * 64, "reason": ""}
        return {
            "schema": "obsidian-large-edit-deterministic-receipt/v1",
            "gate_id": gate_id,
            "round": round_number,
            "candidate_inventory_sha256": candidate_hash,
            "checks": checks,
        }

    def test_local_paragraph_and_formula_edits_do_not_trigger(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, payload in (
                ("paragraph.json", self.metrics(explicit_project_scale=True)),
                ("formula.json", self.metrics(
                    explicit_project_scale=True, single_paragraph_only=False, few_formula_lines_only=True,
                    target_count=4, substantive_prose_target_count=3,
                )),
                ("subsection.json", self.metrics(
                    explicit_project_scale=True, single_paragraph_only=False, single_local_subsection_only=True,
                    target_count=4, substantive_prose_target_count=3,
                )),
                ("narrow.json", self.metrics(
                    explicit_project_scale=True, single_paragraph_only=False, narrow_repair_only=True,
                    target_count=4, substantive_prose_target_count=3,
                )),
                ("mechanical.json", self.metrics(
                    explicit_project_scale=True, single_paragraph_only=False, mechanical_only=True,
                    target_count=8, substantive_prose_target_count=6,
                )),
            ):
                path = root / name
                self.write_json(path, payload)
                code, result = self.run_tool("classify", "--input", str(path))
                self.assertEqual(code, 0)
                self.assertEqual(result["classification"], "ordinary_edit")
                self.assertFalse(result["verifier_required"])

    def test_explicit_multi_note_substantive_edit_triggers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "metrics.json"
            large_metrics = self.metrics(
                explicit_project_scale=True, single_paragraph_only=False,
                target_count=3, substantive_prose_target_count=2,
            )
            self.write_json(path, large_metrics)
            code, result = self.run_tool("classify", "--input", str(path))
            self.assertEqual(code, 0)
            self.assertEqual(result["classification"], "large_edit")
            self.assertTrue(result["verifier_required"])

            not_explicit = root / "not-explicit.json"
            large_metrics["explicit_project_scale"] = False
            self.write_json(not_explicit, large_metrics)
            code, result = self.run_tool("classify", "--input", str(not_explicit))
            self.assertEqual(code, 0)
            self.assertEqual(result["classification"], "ordinary_edit")
            self.assertFalse(result["verifier_required"])

    def test_gate_rehashes_every_staged_markdown_and_rejects_bad_receipts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = root / "candidate"
            candidate.mkdir()
            (candidate / "note.md").write_text("候选正文\n", encoding="utf-8")
            (candidate / "second.md").write_text("第二篇候选\n", encoding="utf-8")
            code, begin_result = self.run_tool("begin", "--candidate-root", str(candidate))
            self.assertEqual(code, 0)
            gate_id = begin_result["gate_id"]

            inventory_path = root / "inventory.json"
            code, inventory = self.run_tool(
                "inventory", "--candidate-root", str(candidate), "--output", str(inventory_path),
            )
            self.assertEqual(code, 0)
            self.assertEqual(inventory["target_count"], 2)
            candidate_hash = inventory["candidate_inventory_sha256"]
            deterministic = root / "deterministic.json"
            passing = root / "pass.json"
            self.write_json(deterministic, self.deterministic_receipt(gate_id, candidate_hash, 1))
            self.write_json(passing, self.receipt(gate_id, candidate_hash, 1, "PASS"))
            arguments = [
                "gate", "--candidate-root", str(candidate), "--inventory", str(inventory_path),
                "--deterministic-receipt", str(deterministic), "--receipt", str(passing),
            ]

            stale = root / "stale.json"
            self.write_json(stale, self.receipt(gate_id, "0" * 64, 1, "PASS"))
            code, result = self.run_tool(*arguments[:-1], str(stale))
            self.assertNotEqual(code, 0)
            self.assertEqual(result["code"], "verification_gate_invalid")

            overbroad = root / "overbroad.json"
            overbroad_receipt = self.receipt(gate_id, candidate_hash, 1, "PASS")
            overbroad_receipt["context"]["excluded_data_classes"].remove("linked_note_bodies")
            self.write_json(overbroad, overbroad_receipt)
            code, result = self.run_tool(*arguments[:-1], str(overbroad))
            self.assertNotEqual(code, 0)

            bad_checks = root / "bad-checks.json"
            bad_deterministic = self.deterministic_receipt(gate_id, candidate_hash, 1)
            bad_deterministic["checks"]["formulas"]["status"] = "FAIL"
            self.write_json(bad_checks, bad_deterministic)
            bad_arguments = list(arguments)
            bad_arguments[bad_arguments.index(str(deterministic))] = str(bad_checks)
            code, result = self.run_tool(*bad_arguments)
            self.assertNotEqual(code, 0)

            (candidate / "second.md").write_text("未列出的篡改不应漏过\n", encoding="utf-8")
            code, result = self.run_tool(*arguments)
            self.assertNotEqual(code, 0)
            self.assertEqual(result["code"], "verification_gate_invalid")

            absolute = root / "absolute-finding.json"
            absolute_receipt = self.receipt(gate_id, candidate_hash, 1, "FAIL")
            absolute_receipt["findings"][0]["relative_path"] = "C:\\Vault\\private.md"
            self.write_json(absolute, absolute_receipt)
            code, result = self.run_tool(*arguments[:-1], str(absolute))
            self.assertNotEqual(code, 0)

            missing = root / "unavailable-verifier.json"
            code, result = self.run_tool(*arguments[:-1], str(missing))
            self.assertNotEqual(code, 0)

            (candidate / "second.md").write_text("第二篇候选\n", encoding="utf-8")
            code, result = self.run_tool(*arguments)
            self.assertEqual(code, 0)
            self.assertTrue(result["write_eligible"])

            replay_candidate = root / "replay-candidate"
            replay_candidate.mkdir()
            (replay_candidate / "note.md").write_text("候选正文\n", encoding="utf-8")
            (replay_candidate / "second.md").write_text("第二篇候选\n", encoding="utf-8")
            code, replay_begin = self.run_tool("begin", "--candidate-root", str(replay_candidate))
            self.assertEqual(code, 0)
            self.assertNotEqual(replay_begin["gate_id"], gate_id)
            replay_inventory = root / "replay-inventory.json"
            code, replay_inventory_data = self.run_tool(
                "inventory", "--candidate-root", str(replay_candidate), "--output", str(replay_inventory),
            )
            self.assertEqual(code, 0)
            self.assertEqual(replay_inventory_data["candidate_inventory_sha256"], candidate_hash)
            code, result = self.run_tool(
                "gate", "--candidate-root", str(replay_candidate), "--inventory", str(replay_inventory),
                "--deterministic-receipt", str(deterministic), "--receipt", str(passing),
            )
            self.assertNotEqual(code, 0)

    def test_fail_revision_pass_and_third_failure_is_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = root / "revision-candidate"
            candidate.mkdir()
            (candidate / "note.md").write_text("第一轮\n", encoding="utf-8")
            code, begin_result = self.run_tool("begin", "--candidate-root", str(candidate))
            self.assertEqual(code, 0)
            gate_id = begin_result["gate_id"]
            for round_number, verdict in ((1, "FAIL"), (2, "PASS")):
                (candidate / "note.md").write_text(f"第{round_number}轮候选\n", encoding="utf-8")
                inventory_path = root / f"revision-inventory-{round_number}.json"
                code, inventory = self.run_tool(
                    "inventory", "--candidate-root", str(candidate), "--output", str(inventory_path),
                )
                self.assertEqual(code, 0)
                candidate_hash = inventory["candidate_inventory_sha256"]
                deterministic = root / f"revision-deterministic-{round_number}.json"
                review = root / f"revision-review-{round_number}.json"
                self.write_json(deterministic, self.deterministic_receipt(gate_id, candidate_hash, round_number))
                self.write_json(review, self.receipt(gate_id, candidate_hash, round_number, verdict))
                code, result = self.run_tool(
                    "gate", "--candidate-root", str(candidate), "--inventory", str(inventory_path),
                    "--deterministic-receipt", str(deterministic), "--receipt", str(review),
                )
                self.assertEqual(code, 0)
            self.assertEqual(result["round_count"], 2)
            self.assertTrue(result["write_eligible"])

            terminal = root / "terminal-candidate"
            terminal.mkdir()
            (terminal / "note.md").write_text("初稿\n", encoding="utf-8")
            code, begin_result = self.run_tool("begin", "--candidate-root", str(terminal))
            self.assertEqual(code, 0)
            terminal_gate_id = begin_result["gate_id"]
            for round_number in range(1, 4):
                (terminal / "note.md").write_text(f"失败候选{round_number}\n", encoding="utf-8")
                inventory_path = root / f"terminal-inventory-{round_number}.json"
                code, inventory = self.run_tool(
                    "inventory", "--candidate-root", str(terminal), "--output", str(inventory_path),
                )
                self.assertEqual(code, 0)
                candidate_hash = inventory["candidate_inventory_sha256"]
                deterministic = root / f"terminal-deterministic-{round_number}.json"
                review = root / f"terminal-review-{round_number}.json"
                self.write_json(deterministic, self.deterministic_receipt(terminal_gate_id, candidate_hash, round_number))
                self.write_json(review, self.receipt(terminal_gate_id, candidate_hash, round_number, "FAIL"))
                code, result = self.run_tool(
                    "gate", "--candidate-root", str(terminal), "--inventory", str(inventory_path),
                    "--deterministic-receipt", str(deterministic), "--receipt", str(review),
                )
                self.assertEqual(code, 0)
            self.assertEqual(result["final_state"], "accepted_after_three_reviews")
            self.assertTrue(result["write_eligible"])

            restart_checks = root / "restart-checks.json"
            restart_review = root / "restart-review.json"
            self.write_json(restart_checks, self.deterministic_receipt(terminal_gate_id, candidate_hash, 1))
            self.write_json(restart_review, self.receipt(terminal_gate_id, candidate_hash, 1, "PASS"))
            code, result = self.run_tool(
                "gate", "--candidate-root", str(terminal), "--inventory", str(inventory_path),
                "--deterministic-receipt", str(restart_checks), "--receipt", str(restart_review),
            )
            self.assertNotEqual(code, 0)
            self.assertIn("already write-eligible", result["message"])


if __name__ == "__main__":
    unittest.main()
