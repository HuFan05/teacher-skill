from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = SKILL_ROOT / "scripts" / "check_mcp_policy.py"
if str(MODULE_PATH.parent) not in sys.path:
    sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("check_mcp_policy", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class CheckMcpPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.skill_text = MODULE.load_policy_text(SKILL_ROOT / "SKILL.md")
        cls.openai_text = (SKILL_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")

    def assert_blocked(self, label: str, skill_text: str | None = None, openai_text: str | None = None) -> None:
        errors = MODULE.validate(
            self.skill_text if skill_text is None else skill_text,
            self.openai_text if openai_text is None else openai_text,
        )
        self.assertIn(label, errors)

    def test_current_skill_passes(self) -> None:
        self.assertEqual(MODULE.validate(self.skill_text, self.openai_text), [])

    def test_cli_passes_on_current_skill(self) -> None:
        process = subprocess.run(
            [sys.executable, str(MODULE_PATH), "--skill-file", str(SKILL_ROOT / "SKILL.md"), "--openai-file", str(SKILL_ROOT / "agents" / "openai.yaml")],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(process.returncode, 0, process.stdout)
        self.assertEqual(json.loads(process.stdout)["code"], "mcp_policy_valid")

    def test_missing_efficiency_stop_rule_is_blocked(self) -> None:
        self.assert_blocked("missing: explicit stop rule", self.skill_text.replace("Stop as soon as", "Continue after"))

    def test_missing_completion_gate_is_blocked(self) -> None:
        self.assert_blocked(
            "missing: mandatory execution",
            self.skill_text.replace("The model has no discretion to omit it", "The model may choose whether to omit it", 1),
        )

    def test_missing_user_termination_authority_is_blocked(self) -> None:
        self.assert_blocked(
            "missing: user termination authority",
            self.skill_text.replace("The user retains the right to terminate it", "Termination is automatic", 1),
        )

    def test_missing_mcp_gate_is_still_blocked(self) -> None:
        self.assert_blocked(
            "missing: mandatory gate heading",
            self.skill_text.replace("## Mandatory Computation-Tool MCP Gate", "## Tool Gate"),
        )

    def test_missing_platform_policy_is_blocked(self) -> None:
        self.assert_blocked(
            "missing: no silent platform switch",
            self.skill_text.replace("do not silently switch between local, container, and remote environments", "switch environments freely"),
        )

    def test_missing_verified_boundary_rule_is_blocked(self) -> None:
        broken = self.skill_text.replace("verifies only the boundary checked; it is not a correctness proof", "proves correctness")
        self.assert_blocked("missing: verified-boundary rule", broken)
        self.assert_blocked(
            "missing: verified-boundary prompt",
            openai_text=self.openai_text.replace("verifies only the boundary checked; it is not a correctness proof", "proves correctness"),
        )

    def test_missing_complexity_fit_boundary_is_blocked(self) -> None:
        self.assert_blocked(
            "missing: no complexity proof from fits",
            self.skill_text.replace("A scaling fit is at most `bounded_empirical`, never a complexity proof", "A scaling fit proves complexity"),
        )

    def test_missing_fastest_completion_parallel_gate_is_blocked(self) -> None:
        self.assert_blocked(
            "missing: bounded parallel attempt",
            self.skill_text.replace("attempt an appropriate bounded parallel implementation", "run an implementation"),
        )

    def test_missing_parallel_serial_fallback_is_blocked(self) -> None:
        self.assert_blocked(
            "missing: serial fallback",
            self.skill_text.replace("return to the best serial implementation", "continue without a fallback", 1),
        )

    def test_missing_fastest_completion_prompt_is_blocked(self) -> None:
        self.assert_blocked(
            "missing: fastest-completion prompt",
            openai_text=self.openai_text.replace("explicitly requests the fastest possible completion or minimum wall time", "requests a computation"),
        )

    def test_missing_call_window_heading_is_blocked(self) -> None:
        self.assert_blocked(
            "missing: call-window heading",
            self.skill_text.replace("## Tool Call-Window and Long-Run Routing", "## Long-Run Routing"),
        )

    def test_missing_long_run_local_route_is_blocked(self) -> None:
        self.assert_blocked(
            "missing: durable local route",
            self.skill_text.replace("use an actually callable local executable or monitorable process for the full run", "keep using the same tool call", 1),
        )

    def test_missing_training_checkpoint_rule_is_blocked(self) -> None:
        self.assert_blocked(
            "missing: complete training checkpoints",
            self.skill_text.replace("A training checkpoint must include every state the resumed run needs", "Save weights"),
        )

    def test_missing_backend_readiness_heading_is_blocked(self) -> None:
        self.assert_blocked(
            "missing: backend readiness heading",
            self.skill_text.replace("## Backend Readiness Gate", "## Backend Notes"),
        )

    def test_cache_hit_backend_start_is_blocked(self) -> None:
        self.assert_blocked(
            "missing: cache-hit no-start rule",
            self.skill_text.replace(
                "On a cache hit, do not start Python, PyTorch, JAX, TensorFlow, an accelerator query, a solver, a proof assistant, a profiler, or any MCP tool",
                "On a cache hit, start every backend",
                1,
            ),
        )

    def test_missing_targeted_invalidation_is_blocked(self) -> None:
        self.assert_blocked(
            "missing: targeted invalidation",
            self.skill_text.replace("`backend_inventory.py --mode Invalidate --backend <name> --reason-code <code>`", "refresh everything"),
        )

    def test_missing_inventory_prompt_is_blocked(self) -> None:
        self.assert_blocked(
            "missing: inventory prompt",
            openai_text=self.openai_text.replace("read or create the persistent local backend inventory first", "select a backend first"),
        )

    def test_persisted_mcp_authority_is_blocked(self) -> None:
        self.assert_blocked(
            "missing: session MCP authority prompt",
            openai_text=self.openai_text.replace("Treat persisted MCP information as historical only", "Treat persisted MCP information as live", 1),
        )

    def test_missing_timeout_inspection_prompt_is_blocked(self) -> None:
        self.assert_blocked(
            "missing: timeout inspection prompt",
            openai_text=self.openai_text.replace("inspect whether work is still running before retrying after a timeout", "retry after a timeout", 1),
        )

    def test_missing_probabilistic_filter_boundary_is_blocked(self) -> None:
        self.assert_blocked(
            "missing: probabilistic filter boundary",
            self.skill_text.replace(
                "A probabilistic filter may reduce exact-verifier work but cannot support an exact final claim by itself",
                "A probabilistic filter is enough",
            ),
        )

    def test_missing_exclusive_scope_is_blocked(self) -> None:
        self.assert_blocked(
            "missing: exclusive computation scope",
            self.skill_text.replace("This Skill handles only computer-science and AI computation and experiment tasks.", "This Skill handles tasks.", 1),
        )

    def test_missing_exclusive_scope_prompt_is_blocked(self) -> None:
        self.assert_blocked(
            "missing: exclusive scope prompt",
            openai_text=self.openai_text.replace(
                "only for computer-science and AI computation and experiment tasks; do not route unrelated work to it or perform work outside that scope",
                "for computation tasks",
            ),
        )

    def test_enumerated_unrelated_tasks_are_blocked(self) -> None:
        self.assert_blocked("forbidden: Vault task enumeration", self.skill_text + "\nThis Skill does not handle Vault writes.\n")
        self.assert_blocked("forbidden: entry-routing task enumeration", self.skill_text + "\nThis Skill does not do Teacher entry routing.\n")

    def test_old_overbroad_interface_prompt_is_blocked(self) -> None:
        self.assert_blocked(
            "missing: English short description",
            openai_text=self.openai_text.replace(
                'short_description: "Route computations efficiently and verify proportionately"',
                'short_description: "Prefer one framework and deliver reproducible computation"',
            ),
        )

    def test_reference_entrance_must_remain_mandatory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "skill"
            shutil.copytree(SKILL_ROOT / "references", root / "references")
            text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
            (root / "SKILL.md").write_text(
                text.replace("you must read [Tool Call-Window and Long-Run Routing]", "you may read [Tool Call-Window and Long-Run Routing]"),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "required_policy_entrance_invalid"):
                MODULE.load_policy_text(root / "SKILL.md")


if __name__ == "__main__":
    unittest.main()
