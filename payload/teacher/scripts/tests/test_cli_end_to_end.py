"""End-to-end through the command line.

The CLI is the real entry point: an agent drives this Skill by running these
subcommands, so the gates have to hold at this level, not only in-process. These
tests spawn the CLI as a child process and assert on exit codes and stdout,
because that is what an agent actually sees.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable


def run(store: Path, *args: str, backend: str | None = None, as_json: bool = True) -> tuple[int, str]:
    """Invoke the CLI as a child process.

    Global options must precede the subcommand, so `--json` is inserted here
    rather than repeated in every call.
    """

    positional = [item for item in args if item != "--json"]
    command = [PYTHON, "-B", "-m", "th.cli", "--store", str(store)]
    if as_json:
        command.append("--json")
    if backend:
        command += ["--backend-command", backend]
    command += positional
    completed = subprocess.run(
        command,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return completed.returncode, completed.stdout + completed.stderr


def payload(output: str) -> dict:
    start = output.find("{")
    if start < 0:
        raise AssertionError(f"no JSON payload in output: {output!r}")
    return json.loads(output[start:])


class CliEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = Path(self._tmp.name) / "store.sqlite3"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, name: str, data: object) -> Path:
        path = Path(self._tmp.name) / name
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return path

    def test_status_starts_from_a_clean_pair_of_heads(self) -> None:
        code, output = run(self.store, "status", "--json")
        self.assertEqual(code, 0)
        body = payload(output)
        self.assertEqual(body["heads"], {"authority": "authority-genesis", "execution": "execution-genesis"})

    def test_binding_an_objective_requires_a_well_formed_objective(self) -> None:
        bad = self._write("bad.json", {"statement": "只有一句话"})
        code, output = run(self.store, "open-objective", str(bad), "--json")
        self.assertEqual(code, 1)
        self.assertIn("objective", payload(output)["message"])

        good = self._write(
            "good.json",
            {
                "statement": "判断训练目标是否改善指标",
                "domain": "machine learning",
                "claim_scope": "accuracy on the declared split",
                "assumptions": ["fixed split"],
                "evidence_standard": "exact_reproduction",
                "completion_standard": "closed chain",
            },
        )
        code, output = run(self.store, "open-objective", str(good), "--json")
        self.assertEqual(code, 0)
        self.assertIn("commitment", payload(output))

    def test_a_window_needs_three_distinct_routes(self) -> None:
        def route(index: int, axes: dict[str, str]) -> dict:
            return {
                "route_id": f"RT-{index}",
                "label_code": f"route.candidate_{index}",
                "axes": axes,
                "hypothesis": f"h{index}",
                "falsifier": f"f{index}",
            }

        axes = [
            {"learning_signal": "axis.a", "architecture_family": "axis.b", "compute_regime": "axis.c"},
            {"learning_signal": "axis.d", "architecture_family": "axis.e", "compute_regime": "axis.f"},
            {"learning_signal": "axis.g", "architecture_family": "axis.h", "compute_regime": "axis.i"},
        ]
        two = self._write("two.json", [route(0, axes[0]), route(1, axes[1])])
        code, output = run(self.store, "open-window", str(two), "--json")
        self.assertEqual(code, 1)
        self.assertIn("route_portfolio", output)

        cloned = self._write("cloned.json", [route(0, axes[0]), route(1, axes[0]), route(2, axes[2])])
        code, output = run(self.store, "open-window", str(cloned), "--json")
        self.assertEqual(code, 1)
        self.assertIn("route_portfolio", output)

        three = self._write("three.json", [route(0, axes[0]), route(1, axes[1]), route(2, axes[2])])
        code, output = run(self.store, "open-window", str(three), "--json")
        self.assertEqual(code, 0)
        self.assertEqual(len(payload(output)["attempts"]), 3)

    def test_a_claim_without_cannot_imply_is_refused(self) -> None:
        claim = {
            "statement": "s",
            "strength": "bounded",
            "grade": "exact_reproduction",
            "scope": {
                "datasets": ["d"],
                "models": ["m"],
                "compute": ["c"],
                "seeds": ["0"],
                "assumptions": ["a"],
            },
            "evidence_ids": ["EV-1"],
            "cannot_imply": [],
        }
        path = self._write("claim.json", claim)
        code, output = run(self.store, "submit-claim", str(path), "--json")
        self.assertEqual(code, 1)
        self.assertIn("cannot_imply", output)

    def test_the_completion_gate_blocks_and_then_permits(self) -> None:
        # A verified checkpoint cannot be recorded without an attempt, which
        # requires a window; this walks the whole path.
        def route(index: int, axes: dict[str, str]) -> dict:
            return {
                "route_id": f"RT-{index}",
                "label_code": f"route.candidate_{index}",
                "axes": axes,
                "hypothesis": f"h{index}",
                "falsifier": f"f{index}",
            }

        axes = [
            {"learning_signal": "axis.a", "architecture_family": "axis.b", "compute_regime": "axis.c"},
            {"learning_signal": "axis.d", "architecture_family": "axis.e", "compute_regime": "axis.f"},
            {"learning_signal": "axis.g", "architecture_family": "axis.h", "compute_regime": "axis.i"},
        ]
        window = self._write("window.json", [route(0, axes[0]), route(1, axes[1]), route(2, axes[2])])
        code, output = run(self.store, "open-window", str(window), "--json")
        self.assertEqual(code, 0)
        attempt = payload(output)["attempts"][0]

        # Without a verified checkpoint the gate blocks.
        code, output = run(self.store, "assess", "--json")
        self.assertEqual(code, 0)
        self.assertIn("NO_VERIFIED_CHECKPOINT", output)

        # Asserting verification is not enough: nothing verified is cited.
        code, output = run(self.store, "checkpoint", attempt, "--verified", "--json")
        self.assertEqual(code, 0)
        self.assertFalse(payload(output)["verified"])
        code, output = run(self.store, "assess", "--json")
        self.assertIn("NO_VERIFIED_CHECKPOINT", output)

        # Evidence the harness itself verified makes it a verified checkpoint.
        sys.path.insert(0, str(ROOT))
        from th.model import digest
        from th.store import Store

        store = Store(self.store)
        store.observe(event_type="evidence_recorded", payload={}, mutate=lambda connection: store.put_evidence(connection, {
            "evidence_id": "EV-cli", "ask_id": "ASK-cli", "kind": "executed_check", "locator": "run:tests:returncode=0",
            "text": "ok", "sha256": digest("ok"), "verified": True}))
        store.close()
        code, output = run(self.store, "checkpoint", attempt, "--verified", "--evidence-id", "EV-cli", "--json")
        self.assertEqual(code, 0)
        self.assertTrue(payload(output)["verified"])

        claim = {
            "statement": "在声明范围内该目标改善指标",
            "strength": "universal",
            "grade": "certificate",
            "scope": {
                "datasets": ["d"],
                "models": ["m"],
                "compute": ["c"],
                "seeds": ["0"],
                "assumptions": ["a"],
            },
            "evidence_ids": ["EV-1"],
            "cannot_imply": ["不能推出在其他数据集上同样成立"],
        }
        claim_path = self._write("claim.json", claim)
        code, output = run(self.store, "submit-claim", str(claim_path), "--json")
        self.assertEqual(code, 0)
        record_id = payload(output)["record_id"]

        # Submitted but unreviewed: still blocked.
        code, output = run(self.store, "assess", "--record-id", record_id, "--json")
        self.assertEqual(code, 0)
        self.assertIn("NO_VERIFIED_CLAIM", output)

        # A review with no stated reviewer is a self-review: recorded, never counted.
        code, output = run(self.store, "review-claim", record_id, "--decision", "accepted", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(payload(output)["review_state"], "unreviewed")

        code, output = run(self.store, "review-claim", record_id, "--decision", "accepted",
                           "--reviewer-kind", "human", "--json")
        self.assertEqual(code, 0)

        code, output = run(self.store, "assess", "--record-id", record_id, "--json")
        self.assertEqual(code, 0)
        assessment = payload(output)["assessment"]
        self.assertEqual(assessment["status"], "VERIFIED")

        code, output = run(self.store, "confirm", assessment["assessment_id"], "--json")
        self.assertEqual(code, 0)
        self.assertEqual(payload(output)["status"], "VERIFIED")

        # Confirm a second time: the assessment is now stale.
        code, output = run(self.store, "confirm", assessment["assessment_id"], "--json")
        self.assertEqual(code, 1)
        self.assertIn("ASSESSMENT_STALE", output)

    def test_a_foreign_review_alone_never_permits_promotion(self) -> None:
        """An imported review is recorded but does not make a claim usable."""

        def route(index: int, axes: dict[str, str]) -> dict:
            return {
                "route_id": f"RT-{index}",
                "label_code": f"route.candidate_{index}",
                "axes": axes,
                "hypothesis": "h",
                "falsifier": "f",
            }

        axes = [
            {"learning_signal": "axis.a", "architecture_family": "axis.b", "compute_regime": "axis.c"},
            {"learning_signal": "axis.d", "architecture_family": "axis.e", "compute_regime": "axis.f"},
            {"learning_signal": "axis.g", "architecture_family": "axis.h", "compute_regime": "axis.i"},
        ]
        window = self._write("window.json", [route(0, axes[0]), route(1, axes[1]), route(2, axes[2])])
        run(self.store, "open-window", str(window), "--json")

        claim = {
            "statement": "在声明范围内该目标改善指标",
            "strength": "bounded",
            "grade": "exact_reproduction",
            "scope": {
                "datasets": ["d"],
                "models": ["m"],
                "compute": ["c"],
                "seeds": ["0"],
                "assumptions": ["a"],
            },
            "evidence_ids": ["EV-1"],
            "cannot_imply": ["不能推出在其他数据集上同样成立"],
        }
        claim_path = self._write("claim.json", claim)
        record_id = payload(run(self.store, "submit-claim", str(claim_path), "--json")[1])["record_id"]

        run(self.store, "review-claim", record_id, "--decision", "accepted",
            "--reviewer-kind", "foreign", "--json")
        code, output = run(self.store, "promote", record_id, "--json")
        self.assertEqual(code, 1)
        self.assertIn("guard_denied", output)

        # A self-review does not make it promotable either.
        run(self.store, "review-claim", record_id, "--decision", "accepted", "--json")
        code, output = run(self.store, "promote", record_id, "--json")
        self.assertEqual(code, 1)

        # A human review is what makes it promotable.
        run(self.store, "review-claim", record_id, "--decision", "accepted", "--reviewer-kind", "human", "--json")
        code, output = run(self.store, "promote", record_id, "--json")
        self.assertEqual(code, 0)
        self.assertEqual(payload(output)["effect"], "current")

    def test_index_apply_requires_the_exact_plan_hash(self) -> None:
        corpus = Path(self._tmp.name) / "corpus"
        corpus.mkdir()
        (corpus / "a.md").write_text("# A\n内容\n", encoding="utf-8")

        code, output = run(self.store, "index-plan", str(corpus), "--json")
        self.assertEqual(code, 0)
        plan = payload(output)
        plan_path = self._write("plan.json", plan)

        code, output = run(
            self.store, "index-apply", str(plan_path), "--expect-plan-sha256", "0" * 64, "--json"
        )
        self.assertEqual(code, 1)
        self.assertIn("plan_hash", output)

        code, output = run(
            self.store,
            "index-apply",
            str(plan_path),
            "--expect-plan-sha256",
            plan["plan_sha256"],
            "--json",
        )
        self.assertEqual(code, 0)
        self.assertEqual(len(payload(output)["registered"]), 1)

    def test_chain_reports_a_clean_pair_after_work(self) -> None:
        def route(index: int, axes: dict[str, str]) -> dict:
            return {
                "route_id": f"RT-{index}",
                "label_code": f"route.candidate_{index}",
                "axes": axes,
                "hypothesis": "h",
                "falsifier": "f",
            }

        axes = [
            {"learning_signal": "axis.a", "architecture_family": "axis.b", "compute_regime": "axis.c"},
            {"learning_signal": "axis.d", "architecture_family": "axis.e", "compute_regime": "axis.f"},
            {"learning_signal": "axis.g", "architecture_family": "axis.h", "compute_regime": "axis.i"},
        ]
        window = self._write("window.json", [route(0, axes[0]), route(1, axes[1]), route(2, axes[2])])
        run(self.store, "open-window", str(window), "--json")

        code, output = run(self.store, "chain", "--json")
        self.assertEqual(code, 0)
        body = payload(output)
        self.assertEqual(body["execution"]["problems"], [])
        self.assertEqual(body["authority"]["problems"], [])
        self.assertGreater(body["execution"]["length"], 0)
        self.assertEqual(body["authority"]["length"], 0)

    def test_turn_without_a_backend_fails_closed_with_a_fixed_string(self) -> None:
        code, output = run(self.store, "turn", "一个提交", "--json")
        self.assertEqual(code, 1)
        body = payload(output)
        self.assertEqual(body["status"], "failed")
        self.assertIn("本轮反馈无法在安全边界内生成", body["safe_visible"])


if __name__ == "__main__":
    unittest.main()
