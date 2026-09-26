from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "observer_run.py"


class ObserverRunTests(unittest.TestCase):
    def _run(self, *child: str) -> subprocess.CompletedProcess:
        environment = os.environ.copy()
        # Port 9 on loopback is closed on ordinary hosts; delivery must fail open.
        environment["SKILL_OBSERVER_PHASE_ENDPOINT"] = "http://127.0.0.1:9/v1/phase-batches"
        environment["SKILL_OBSERVER_THREAD_ID"] = "test-thread"
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--skill", "cs-ai-computation",
                "--catalog", "cs-ai-computation/v1",
                "--phase", "cs-ai-computation.script.run",
                "--",
                *child,
            ],
            text=True,
            capture_output=True,
            check=False,
            env=environment,
            timeout=30,
        )

    def test_wrapper_preserves_child_output_and_exit_code(self) -> None:
        process = self._run(sys.executable, "-c", "import sys; print('child-output'); sys.exit(7)")
        self.assertEqual(process.returncode, 7)
        self.assertEqual(process.stdout.strip(), "child-output")

    def test_wrapper_succeeds_when_observer_is_unreachable(self) -> None:
        process = self._run(sys.executable, "-c", "print('ok')")
        self.assertEqual(process.returncode, 0)
        self.assertEqual(process.stdout.strip(), "ok")
        self.assertEqual(process.stderr, "")


if __name__ == "__main__":
    unittest.main()
