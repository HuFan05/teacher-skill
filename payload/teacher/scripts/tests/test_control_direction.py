"""The direction of control: the harness controls the model, not the reverse.

A prompt-only deployment has the model as the operator. These tests pin down the
inversion:

  * the model holds no capability — the backend interface exposes no method that
    acts, only methods that return structures;
  * the model cannot choose when it runs — the harness calls it, and a refused
    input means it is never called at all;
  * the model cannot reach a path or a command — it names an entry from a closed
    vocabulary and the broker decides;
  * nothing the model asks for can advance authority.
"""

from __future__ import annotations

import inspect
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from th import Session  # noqa: E402
from th import constants as C  # noqa: E402
from th.backend import ScriptedBackend, StructuredBackend, make_draft  # noqa: E402
from th.broker import (  # noqa: E402
    OPERATIONS,
    OP_COUNT_LINES,
    OP_HASH,
    OP_RUN,
    OP_STAT,
    Broker,
    OperationRefused,
    validate_request,
)

from support import ALLOW, Harness, contract  # noqa: E402


# --------------------------------------------------------------------------
# The model holds no capability
# --------------------------------------------------------------------------
class NoActionMethodsTests(unittest.TestCase):
    def test_the_backend_protocol_exposes_no_acting_method(self) -> None:
        """Every method either returns a structure or releases nothing."""

        members = {
            name
            for name, _ in inspect.getmembers(StructuredBackend)
            if not name.startswith("_")
        }
        forbidden = {
            "run", "exec", "execute", "shell", "bash", "system",
            "read", "write", "open", "delete", "move",
            "request", "http", "fetch", "browse", "network",
            "tool", "tools", "call_tool",
        }
        self.assertFalse(members & forbidden, f"backend exposes {members & forbidden}")

    def test_operation_vocabulary_is_read_only_and_closed(self) -> None:
        """The vocabulary contains no mutating operation.

        This is a design invariant, not a coincidence: a model that cannot name
        a mutation cannot perform one.
        """

        from th.broker import INSPECTIONS, RETRIEVALS

        self.assertEqual(set(INSPECTIONS), {OP_HASH, OP_STAT, OP_COUNT_LINES, OP_RUN})
        self.assertEqual(set(OPERATIONS), set(INSPECTIONS) | set(RETRIEVALS))
        mutating = {"write_file", "delete_file", "move_file", "append_file", "chmod", "mkdir",
                    "write_note", "edit_note", "post_url", "upload", "shell"}
        self.assertFalse(mutating & set(OPERATIONS))
        # Every retrieval returns data for the model's packet; none writes.
        for name in RETRIEVALS:
            self.assertTrue(name.startswith(("search_", "read_", "arxiv_", "fetch_")), name)


# --------------------------------------------------------------------------
# The model can only ask
# --------------------------------------------------------------------------
class BrokerRefusalTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "inside.txt").write_text("a\nb\nc\n", encoding="utf-8")
        self.outside = Path(self._tmp.name).parent / "outside-teacher-harness.txt"
        self.outside.write_text("secret\n", encoding="utf-8")
        self.broker = Broker(
            read_roots=(self.root,),
            declared_commands={"lines": ("wc", "-l")},
            executor=lambda command, cwd, timeout: (0, "3\n", ""),
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()
        self.outside.unlink(missing_ok=True)

    def test_unknown_operation_is_refused(self) -> None:
        with self.assertRaises(OperationRefused) as caught:
            validate_request({"operation": "delete_everything", "arguments": {"relative_path": "x"}})
        self.assertEqual(caught.exception.code, "operation_unknown")

    def test_extra_argument_is_refused_rather_than_ignored(self) -> None:
        with self.assertRaises(OperationRefused) as caught:
            validate_request({"operation": OP_HASH, "arguments": {"relative_path": "a", "shell": "rm -rf /"}})
        self.assertEqual(caught.exception.code, "operation_arguments_invalid")

    def test_extra_request_key_is_refused(self) -> None:
        with self.assertRaises(OperationRefused):
            validate_request({"operation": OP_HASH, "arguments": {"relative_path": "a"}, "options": {"sudo": True}})

    def test_path_outside_the_declared_root_is_refused(self) -> None:
        request = validate_request(
            {"operation": OP_HASH, "arguments": {"relative_path": "../outside-teacher-harness.txt"}}
        )
        with self.assertRaises(OperationRefused) as caught:
            self.broker.execute(request)
        self.assertEqual(caught.exception.code, "operation_outside_scope")

    def test_absolute_path_is_confined_or_refused(self) -> None:
        request = validate_request(
            {"operation": OP_HASH, "arguments": {"relative_path": str(self.outside)}}
        )
        with self.assertRaises(OperationRefused) as caught:
            self.broker.execute(request)
        self.assertEqual(caught.exception.code, "operation_outside_scope")

    def test_undeclared_command_is_refused(self) -> None:
        """A model names an entry in the allow list. It cannot supply a command."""

        request = validate_request(
            {"operation": OP_RUN, "arguments": {"name": "rm", "arguments": ["-rf", "/"]}}
        )
        with self.assertRaises(OperationRefused) as caught:
            self.broker.execute(request)
        self.assertEqual(caught.exception.code, "operation_not_declared")

    def test_argument_shaped_like_a_shell_payload_is_refused(self) -> None:
        with self.assertRaises(OperationRefused):
            validate_request(
                {"operation": OP_RUN, "arguments": {"name": "lines", "arguments": ["; rm -rf /"]}}
            )
        with self.assertRaises(OperationRefused):
            validate_request(
                {"operation": OP_RUN, "arguments": {"name": "lines", "arguments": ["a\nrm -rf /"]}}
            )

    def test_declared_command_runs_without_shell_interpretation(self) -> None:
        seen: dict[str, object] = {}

        def executor(command, cwd, timeout):  # noqa: ANN001
            seen["command"] = command
            seen["cwd"] = cwd
            return 0, "3\n", ""

        broker = Broker(
            read_roots=(self.root,),
            declared_commands={"lines": ("wc", "-l")},
            executor=executor,
        )
        result = broker.execute(
            validate_request({"operation": OP_RUN, "arguments": {"name": "lines", "arguments": ["inside.txt"]}})
        )
        self.assertEqual(seen["command"], ["wc", "-l", "inside.txt"])
        # The broker resolves its roots, and on macOS /var is a symlink to
        # /private/var, so compare against the resolved path.
        self.assertEqual(seen["cwd"], self.root.resolve())
        # Raw output is withheld: only a size and a digest come back.
        self.assertNotIn("stdout", result["value"])
        self.assertEqual(result["value"]["stdout_bytes"], 2)
        self.assertIn("stdout_sha256", result["value"])

    def test_inspections_return_bounded_values_only(self) -> None:
        for operation in (OP_HASH, OP_STAT, OP_COUNT_LINES):
            result = self.broker.execute(
                validate_request({"operation": operation, "arguments": {"relative_path": "inside.txt"}})
            )
            self.assertTrue(result["bounded"])
            self.assertIn("receipt", result)
            self.assertEqual(result["receipt"]["executed_by"], "Skill")

    def test_every_refusal_leaves_the_filesystem_untouched(self) -> None:
        before = {path.name: path.read_bytes() for path in self.root.iterdir()}
        attempts = [
            {"operation": OP_HASH, "arguments": {"relative_path": "../outside-teacher-harness.txt"}},
            {"operation": OP_RUN, "arguments": {"name": "rm", "arguments": ["-rf", "."]}},
        ]
        for attempt in attempts:
            try:
                self.broker.execute(validate_request(attempt))
            except OperationRefused:
                pass
        after = {path.name: path.read_bytes() for path in self.root.iterdir()}
        self.assertEqual(before, after)


# --------------------------------------------------------------------------
# The harness decides, executes and receipts
# --------------------------------------------------------------------------
class HarnessExecutesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "paper.md").write_text("# paper\nline\nline\n", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _harness(self, **draft_overrides) -> Harness:
        base = contract()
        base["scope"] = {"read_roots": [str(self.root)], "roots": [str(self.root)]}
        base["delegation"] = {"tools": [], "capabilities": [], "commands": {"lines": ["wc", "-l"]}}
        harness = Harness(
            drafts=[make_draft(**draft_overrides)],
            verdicts=[ALLOW],
            contract_override=base,
        )
        # Replace the executor so the test never spawns a process.
        harness.engine.broker.executor = lambda command, cwd, timeout: (0, "3\n", "")
        return harness

    def test_a_requested_inspection_is_executed_by_the_harness(self) -> None:
        with self._harness(
            requested_operation={"operation": OP_HASH, "arguments": {"relative_path": "paper.md"}}
        ) as harness:
            result = harness.engine.turn("帮我算一下这篇的摘要", actor="worker")
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["operation"]["outcome"], "executed")
            self.assertEqual(result["operation"]["operation"], OP_HASH)
            # The visible text names the operation and marks it as bounded; it
            # does not contain the digest.
            self.assertIn("计算文件摘要", result["visible"])
            self.assertIn("原始输出不进入界面", result["visible"])
            self.assertNotIn("sha256", result["visible"])

    def test_an_out_of_scope_request_is_refused_as_an_outcome(self) -> None:
        with self._harness(
            requested_operation={"operation": OP_HASH, "arguments": {"relative_path": "../../etc/hosts"}}
        ) as harness:
            result = harness.engine.turn("读一下这个", actor="worker")
            self.assertEqual(result["status"], "ok", "a refusal is not a turn failure")
            self.assertEqual(result["operation"]["outcome"], "refused")
            self.assertEqual(result["operation"]["code"], "operation_outside_scope")
            self.assertIn("已被 Skill 拒绝", result["visible"])

    def test_an_undeclared_command_is_refused_as_an_outcome(self) -> None:
        with self._harness(
            requested_operation={"operation": OP_RUN, "arguments": {"name": "curl", "arguments": ["http://x"]}}
        ) as harness:
            result = harness.engine.turn("跑一下", actor="worker")
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["operation"]["outcome"], "refused")
            self.assertEqual(result["operation"]["code"], "operation_not_declared")

    def test_a_malformed_request_is_rejected_at_the_boundary(self) -> None:
        with self._harness(requested_operation={"operation": OP_HASH, "arguments": {"path": "paper.md"}}) as harness:
            result = harness.engine.turn("算一下", actor="worker")
            self.assertEqual(result["status"], "failed")
            self.assertIn(C.RISK_SCHEMA, result["risk_codes"])

    def test_execution_is_receipted_in_the_store(self) -> None:
        with self._harness(
            requested_operation={"operation": OP_COUNT_LINES, "arguments": {"relative_path": "paper.md"}}
        ) as harness:
            harness.engine.turn("数一下行数", actor="worker")
            receipts = [event for event in harness.store.events() if event["type"] == "execution_advanced"]
            self.assertTrue(receipts)

    def test_bounded_results_are_offered_to_the_next_packet(self) -> None:
        base = contract()
        base["scope"] = {"read_roots": [str(self.root)], "roots": [str(self.root)]}
        with Harness(
            drafts=[
                make_draft(requested_operation={"operation": OP_STAT, "arguments": {"relative_path": "paper.md"}}),
                make_draft(),
            ],
            verdicts=[ALLOW, ALLOW],
            contract_override=base,
        ) as harness:
            harness.engine.turn("看一下元信息", actor="worker")
            self.assertTrue(harness.engine.pending_operation_results)
            harness.engine.turn("继续", actor="worker")
            # The results were consumed by the second turn's packet.
            self.assertEqual(harness.engine.pending_operation_results, [])

    def test_no_operation_can_advance_authority(self) -> None:
        with self._harness(
            requested_operation={"operation": OP_HASH, "arguments": {"relative_path": "paper.md"}}
        ) as harness:
            before = harness.store.head(C.HEAD_AUTHORITY)
            harness.engine.turn("算一下", actor="worker")
            self.assertEqual(harness.store.head(C.HEAD_AUTHORITY), before)

    def test_available_operations_are_reported_with_limits(self) -> None:
        with self._harness() as harness:
            available = harness.engine.available_operations()
            self.assertEqual(sorted(available["operations"]), sorted(OPERATIONS))
            self.assertIn("limits", available)
            self.assertIn("output_bytes", available["limits"])


# --------------------------------------------------------------------------
# The harness owns the loop
# --------------------------------------------------------------------------
class HarnessOwnsTheLoopTests(unittest.TestCase):
    def test_the_harness_calls_the_model_not_the_reverse(self) -> None:
        with Harness(drafts=[make_draft()], verdicts=[ALLOW]) as harness:
            session = Session(harness.engine)
            self.assertEqual(harness.backend.calls, [])
            session.submit("一个提交")
            self.assertIn("review_turn", harness.backend.calls)
            self.assertIn("guard_turn", harness.backend.calls)

    def test_a_refused_input_means_the_model_is_never_called(self) -> None:
        with Harness(drafts=[make_draft()], verdicts=[ALLOW]) as harness:
            session = Session(harness.engine)
            result = session.submit("")
            self.assertEqual(result["status"], "refused")
            self.assertEqual(harness.backend.calls, [], "the harness decides whether the model runs")

    def test_a_failing_gate_stops_before_the_model(self) -> None:
        """When no guard verdict is queued the turn fails closed, and the model
        is not asked again."""

        with Harness(drafts=[make_draft()], verdicts=[]) as harness:
            session = Session(harness.engine)
            result = session.submit("一个提交")
            self.assertEqual(result["status"], "failed")
            self.assertEqual(harness.backend.calls, ["review_turn", "guard_turn"])

    def test_the_session_transcript_records_what_the_harness_did(self) -> None:
        with Harness(drafts=[make_draft()], verdicts=[ALLOW]) as harness:
            session = Session(harness.engine)
            session.submit("一个提交")
            entry = session.transcript[-1]
            self.assertIn("input_sha256", entry)
            self.assertNotIn("一个提交", str(entry), "the transcript records a digest, not the text")
            self.assertEqual(entry["status"], "ok")

    def test_capabilities_declare_the_control_direction(self) -> None:
        with Harness() as harness:
            session = Session(harness.engine)
            capabilities = session.capabilities()
            self.assertEqual(capabilities["control_direction"], "Skill controls model")
            self.assertIn("run a process", capabilities["model_may_not"])
            self.assertIn("choose when it is called", capabilities["model_may_not"])

    def test_the_model_never_writes_the_visible_text(self) -> None:
        """Even a draft full of prose cannot reach the surface."""

        draft = make_draft(focus_quote="我需要给你一个完整的解答")
        with Harness(drafts=[draft], verdicts=[ALLOW]) as harness:
            session = Session(harness.engine)
            result = session.submit("我需要给你一个完整的解答")
            self.assertIn("本段由确定性渲染器生成", result["visible"])
            # The only model-influenced fragment is the byte-verified quote,
            # and it is labelled as the caller's own words.
            self.assertIn("你的原话", result["visible"])


if __name__ == "__main__":
    unittest.main()
