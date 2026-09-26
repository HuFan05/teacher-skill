from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
from os.path import realpath as _realpath

# The platform temporary root can be a symlink (for example on macOS); resolve
# it so synthetic knowledge roots match the resolved paths the code compares.
tempfile.tempdir = _realpath(tempfile.gettempdir())
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from manage_personal_knowledge.performance import (  # noqa: E402
    analyze_agent_records,
    build_agent_command,
    interval_union_ms,
    metric_stats,
    percentile_nearest_rank,
    run_agent_process,
    run_timed_command,
)


class PerformanceTests(unittest.TestCase):
    def test_statistics_use_nearest_rank_p90(self) -> None:
        self.assertEqual(percentile_nearest_rank([1, 2, 3, 4, 5], 90), 5)
        stats = metric_stats([10, 20, 30])
        self.assertEqual(stats["median_ms"], 20.0)
        self.assertEqual(stats["p90_ms"], 30.0)

    def test_interval_union_does_not_double_count_parallel_tools(self) -> None:
        self.assertEqual(interval_union_ms([(10, 20), (15, 30), (40, 45)]), 25)

    def test_agent_analysis_splits_tool_time_from_residual(self) -> None:
        records = [
            {"stream": "stdout", "at_ms": 100.0, "event": {"type": "turn.started"}},
            {
                "stream": "stdout",
                "at_ms": 200.0,
                "event": {
                    "type": "item.started",
                    "item": {"id": "tool-1", "type": "command_execution", "command": "python manage_kb.py status"},
                },
            },
            {
                "stream": "stdout",
                "at_ms": 500.0,
                "event": {
                    "type": "item.completed",
                    "item": {"id": "tool-1", "type": "command_execution", "exit_code": 0},
                },
            },
            {
                "stream": "stdout",
                "at_ms": 700.0,
                "event": {"type": "item.completed", "item": {"id": "msg-1", "type": "agent_message"}},
            },
            {"stream": "stdout", "at_ms": 800.0, "event": {"type": "turn.completed", "usage": {"x": 1}}},
        ]
        result = analyze_agent_records(records, process_wall_ms=900.0, return_code=0)
        self.assertEqual(result["turn_wall_ms"], 700.0)
        self.assertEqual(result["tool_union_ms"], 300.0)
        self.assertEqual(result["model_orchestration_residual_ms"], 400.0)
        self.assertEqual(result["first_agent_message_after_turn_ms"], 600.0)
        self.assertEqual(result["shutdown_after_turn_ms"], 100.0)
        self.assertIsNone(result["final_error"])

    def test_timed_command_redacts_query_and_counts_results(self) -> None:
        payload = json.dumps({"ok": True, "results": [1, 2]}, ensure_ascii=False)
        command = [sys.executable, "-c", f"print({payload!r})", "--query", "private words"]
        event = run_timed_command("probe", command, timeout_seconds=10)
        self.assertEqual(event["return_code"], 0)
        self.assertEqual(event["result_count"], 2)
        self.assertIn("<redacted>", event["command"])
        self.assertNotIn("private words", event["command"])

    def test_timed_command_can_keep_output_for_local_debugging(self) -> None:
        with tempfile.TemporaryDirectory() as _:
            event = run_timed_command(
                "probe",
                [sys.executable, "-c", "print('hello')"],
                timeout_seconds=10,
                keep_output=True,
            )
        self.assertEqual(event["stdout"].strip(), "hello")

    def test_agent_process_timeout_returns_without_waiting_for_open_pipe(self) -> None:
        # The streaming helper's pure event analysis remains usable for a killed run.
        result = analyze_agent_records([], process_wall_ms=1234.5, return_code=-1)
        self.assertEqual(result["process_wall_ms"], 1234.5)
        self.assertIsNone(result["turn_wall_ms"])

    def test_agent_command_template_requires_model_placeholder(self) -> None:
        self.assertEqual(
            build_agent_command(["agent", "--model", "{model}", "--json"], "m-1"),
            ["agent", "--model", "m-1", "--json"],
        )
        with self.assertRaises(ValueError):
            build_agent_command(["agent", "--json"], "m-1")
        with self.assertRaises(ValueError):
            build_agent_command([], "m-1")

    def test_agent_process_reads_prompt_on_stdin_and_times_event_stream(self) -> None:
        agent = (
            "import json, sys\n"
            "prompt = sys.stdin.read()\n"
            "model = sys.argv[1]\n"
            "events = [\n"
            "    {'type': 'turn.started'},\n"
            "    {'type': 'item.started', 'item': {'id': 't1', 'type': 'command_execution'}},\n"
            "    {'type': 'item.completed', 'item': {'id': 't1', 'type': 'command_execution', 'exit_code': 0}},\n"
            "    {'type': 'item.completed', 'item': {'id': 'm1', 'type': 'agent_message'}},\n"
            "    {'type': 'turn.completed', 'usage': {'prompt_chars': len(prompt), 'model': model}},\n"
            "]\n"
            "for event in events:\n"
            "    print(json.dumps(event), flush=True)\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = root / "fake_agent.py"
            script.write_text(agent, encoding="utf-8")
            trace = root / "trace.jsonl"
            result = run_agent_process(
                command_template=[sys.executable, str(script), "{model}"],
                prompt="fixed prompt",
                model="model-a",
                working_dir=root,
                timeout_seconds=30,
                keep_raw_events=False,
                trace_path=trace,
            )
            compact = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(result["return_code"], 0)
        self.assertFalse(result["timed_out"])
        self.assertEqual(result["model"], "model-a")
        self.assertEqual(result["usage"], {"prompt_chars": 12, "model": "model-a"})
        self.assertEqual(len(result["tool_events"]), 1)
        self.assertIsNotNone(result["turn_wall_ms"])
        self.assertNotIn("raw", compact[0])


if __name__ == "__main__":
    unittest.main()
