from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "validate_obsidian_formulas.py"
SPEC = importlib.util.spec_from_file_location("validate_obsidian_formulas", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
VALIDATOR = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = VALIDATOR
SPEC.loader.exec_module(VALIDATOR)


class ObsidianFormulaValidatorTests(unittest.TestCase):
    def test_extracts_inline_and_display_formulas_but_ignores_code(self) -> None:
        text = "行内公式$x^2+1$。\n$$a=b$$\n`$not_a_formula$`\n```tex\n$also_not_a_formula$\n```\n"
        formulas, errors = VALIDATOR.extract_formulas(text)
        self.assertEqual(errors, [])
        self.assertEqual([item.display for item in formulas], [False, True])

    def test_rejects_unsupported_obsidian_delimiters(self) -> None:
        formulas, errors = VALIDATOR.extract_formulas(r"错误写法：\(x+1\) 和 \[y=2\]")
        self.assertEqual(formulas, [])
        self.assertTrue(errors)
        self.assertTrue(all(error.code == "unsupported_delimiter" for error in errors))

    def test_rejects_unclosed_formula(self) -> None:
        _, errors = VALIDATOR.extract_formulas("这里有一个未闭合公式$x+1。\n")
        self.assertEqual([error.code for error in errors], ["unclosed_inline_formula"])

    def test_rejects_unclosed_tex_group_offline(self) -> None:
        formulas, errors = VALIDATOR.extract_formulas("$\\frac{x}{y$")
        self.assertEqual(errors, [])
        self.assertEqual([error.code for error in VALIDATOR.validate_tex_structure(formulas)], ["unclosed_group"])

    def test_rejects_control_word_shadow_after_lost_backslash(self) -> None:
        formulas, errors = VALIDATOR.extract_formulas(
            r"$a\qquad b$ and $X:=x,qquad Y:=y$"
        )
        self.assertEqual(errors, [])
        transport_errors = VALIDATOR.validate_tex_transport(formulas)
        self.assertEqual(
            [error.code for error in transport_errors],
            ["suspicious_bare_tex_command"],
        )
        self.assertIn(r"\qquad", transport_errors[0].message)

    def test_transport_check_allows_unrelated_multiletter_identifiers(self) -> None:
        formulas, errors = VALIDATOR.extract_formulas(
            r"$abc+rank(A)$ and $\qquad$"
        )
        self.assertEqual(errors, [])
        self.assertEqual(VALIDATOR.validate_tex_transport(formulas), [])

    def test_batches_stay_within_cli_limit(self) -> None:
        formulas = [VALIDATOR.Formula("x^2+1", False, line) for line in range(1, 101)]
        batches = VALIDATOR.make_batches(formulas)
        self.assertGreater(len(batches), 1)
        self.assertEqual(sum(map(len, batches)), len(formulas))
        self.assertTrue(all(len(VALIDATOR.build_eval_code(batch).encode("utf-8")) <= VALIDATOR.MAX_EVAL_BYTES for batch in batches))

    @patch.object(VALIDATOR.subprocess, "run")
    def test_live_obsidian_renderer_success(self, run_mock) -> None:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='=> "[{\\"ok\\":true,\\"line\\":1}]"\n', stderr=""
        )
        results, batches = VALIDATOR.run_mathjax(
            [VALIDATOR.Formula("x^2+1", False, 1)], Path("Obsidian.com"), 30
        )
        self.assertEqual(results, [{"ok": True, "line": 1}])
        self.assertEqual(batches, 1)
        command = run_mock.call_args.args[0]
        self.assertIn("MathJax.tex2chtml", command[2])
        self.assertNotIn("require('obsidian')", command[2])

    @patch.object(VALIDATOR.subprocess, "run")
    def test_live_obsidian_renderer_blocked(self, run_mock) -> None:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="CLI is unable to find Obsidian"
        )
        with self.assertRaisesRegex(RuntimeError, "unable to find Obsidian"):
            VALIDATOR.run_mathjax(
                [VALIDATOR.Formula("x", False, 1)], Path("Obsidian.com"), 30
            )

    def test_cli_delimiter_blocked_path_returns_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            note = Path(temp_name) / "bad.md"
            note.write_text(r"错误：\[x=1\]", encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, "-B", str(SCRIPT), str(note)],
                capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
            )
        self.assertNotEqual(completed.returncode, 0)
        payload = json.loads(completed.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["stage"], "delimiter")
        self.assertIn("recovery", payload)

    def test_cli_transport_blocked_path_returns_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            note = Path(temp_name) / "transport.md"
            note.write_text(
                r"$a\qquad b$ and $X:=x,qquad Y:=y$",
                encoding="utf-8",
            )
            completed = subprocess.run(
                [sys.executable, "-B", str(SCRIPT), str(note), "--fail-on-transport-shadow"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", check=False,
            )
        self.assertNotEqual(completed.returncode, 0)
        payload = json.loads(completed.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["stage"], "offline_transport")
        self.assertEqual(
            payload["errors"][0]["code"],
            "suspicious_bare_tex_command",
        )
        self.assertIn("raw or literal transport", payload["recovery"])

    def test_default_cli_reports_transport_shadow_without_breaking_existing_note(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            note = Path(temp_name) / "existing.md"
            note.write_text(
                r"$a\qquad b$ and $X:=x,qquad Y:=y$",
                encoding="utf-8",
            )
            completed = subprocess.run(
                [sys.executable, "-B", str(SCRIPT), str(note)],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", check=False,
            )
        self.assertEqual(completed.returncode, 0)
        payload = json.loads(completed.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["transport_audit"]["warning_count"], 1)

    def test_default_mode_passes_offline_without_obsidian(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            note = Path(temp_name) / "offline.md"
            note.write_text("$x^2+1$", encoding="utf-8")
            completed = subprocess.run([sys.executable, "-B", str(SCRIPT), str(note)], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
        self.assertEqual(completed.returncode, 0)
        payload = json.loads(completed.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["backend"]["status"], "offline_structure_passed")
        self.assertEqual(payload["obsidian"]["reason"], "live_mode_off")

    @patch.object(VALIDATOR, "discover_obsidian_cli", side_effect=FileNotFoundError("missing"))
    def test_required_live_failure_forbids_gui_recovery(self, _discover) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            note = Path(temp_name) / "required.md"
            note.write_text("$x$", encoding="utf-8")
            with patch.object(sys, "argv", [str(SCRIPT), str(note), "--live-mode", "required"]):
                with patch("builtins.print") as output:
                    code = VALIDATOR.main()
        self.assertEqual(code, 3)
        payload = json.loads(output.call_args.args[0])
        self.assertEqual(payload["obsidian"]["reason"], "live_check_unavailable")
        self.assertIn("Do not launch", payload["recovery"])

    def test_validator_has_no_edge_dependency(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8").lower()
        self.assertNotIn("msedge", source)
        self.assertNotIn("headless edge", source)


if __name__ == "__main__":
    unittest.main()
