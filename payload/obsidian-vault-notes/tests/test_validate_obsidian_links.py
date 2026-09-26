from __future__ import annotations

import json
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate_obsidian_links.py"
SPEC = importlib.util.spec_from_file_location("validate_obsidian_links_under_test", SCRIPT)
VALIDATOR = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = VALIDATOR
SPEC.loader.exec_module(VALIDATOR)


class ValidateObsidianLinksTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name)
        (self.vault / "Target.md").write_text("# Valid heading\n\nText ^valid-block\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_validator(self, text: str) -> tuple[int, dict[str, object]]:
        note = self.vault / "Source.md"
        note.write_text(text, encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), str(note), "--vault-root", str(self.vault), "--live-mode", "off"],
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        return completed.returncode, json.loads(completed.stdout)

    def test_accepts_existing_note_heading_and_block(self) -> None:
        code, result = self.run_validator("[[Target]]\n[[Target#Valid heading]]\n[[Target#^valid-block]]\n")
        self.assertEqual(code, 0)
        self.assertTrue(result["ok"])
        self.assertEqual(result["links"], 3)

    def test_rejects_missing_target_and_anchor(self) -> None:
        code, result = self.run_validator("[[Missing]]\n[[Target#Wrong heading]]\n")
        self.assertNotEqual(code, 0)
        self.assertFalse(result["ok"])
        self.assertEqual({item["code"] for item in result["issues"]}, {"missing_target", "missing_heading"})

    def test_ignores_links_inside_code(self) -> None:
        code, result = self.run_validator("`[[Missing inline]]`\n```md\n[[Missing fenced]]\n```\n[[Target]]\n")
        self.assertEqual(code, 0)
        self.assertEqual(result["links"], 1)

    def test_live_payload_is_split_below_byte_budget(self) -> None:
        payload = [
            {"target": f"Folder/Target-{index}-" + ("x" * 80), "anchorKind": "heading", "anchor": "Section", "line": index}
            for index in range(40)
        ]
        batches = VALIDATOR._live_payload_batches("Folder/Source.md", payload, max_code_bytes=1200)
        self.assertGreater(len(batches), 1)
        self.assertEqual([item for batch in batches for item in batch], payload)
        self.assertTrue(all(len(("code=" + VALIDATOR._build_live_js("Folder/Source.md", batch)).encode("utf-8")) <= 1200 for batch in batches))

    def test_live_audit_accepts_prefixed_cli_json_and_aggregates_batches(self) -> None:
        links = [VALIDATOR.Link(f"Target-{index}", f"Target-{index}-" + ("x" * 180), None, None, index, False) for index in range(20)]
        calls: list[list[str]] = []

        def fake_run(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            calls.append(args)
            count = args[-1].count('"anchorKind"')
            rows = [{"ok": True, "path": f"Target-{index}.md"} for index in range(count)]
            return subprocess.CompletedProcess(args, 0, stdout="=> " + json.dumps(rows), stderr="")

        with mock.patch.object(VALIDATOR, "_obsidian_cli", return_value="Obsidian.com"), mock.patch.object(VALIDATOR.subprocess, "run", side_effect=fake_run):
            result = VALIDATOR.live_audit(self.vault / "Source.md", self.vault, links)

        self.assertTrue(result["ok"])
        self.assertTrue(result["checked"])
        self.assertEqual(result["links"], 20)
        self.assertGreater(len(calls), 1)

    def test_live_audit_blocks_one_link_over_budget_without_calling_cli(self) -> None:
        link = VALIDATOR.Link("Huge", "x" * 3000, None, None, 1, False)
        with mock.patch.object(VALIDATOR, "_obsidian_cli", return_value="Obsidian.com"), mock.patch.object(VALIDATOR.subprocess, "run") as run:
            result = VALIDATOR.live_audit(self.vault / "Source.md", self.vault, [link])
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "obsidian_cli_request_too_large")
        run.assert_not_called()

    def test_live_unavailable_forbids_gui_recovery(self) -> None:
        link = VALIDATOR.Link("Target", "Target", None, None, 1, False)
        with mock.patch.object(VALIDATOR, "_obsidian_cli", return_value=None):
            result = VALIDATOR.live_audit(self.vault / "Source.md", self.vault, [link])
        self.assertEqual(result["reason"], "live_check_unavailable")
        self.assertIn("Do not launch", result["recovery"])

    def test_default_cli_mode_is_off(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('default="off"', source)


if __name__ == "__main__":
    unittest.main()
