from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from case_schema import ensure_external_case_path  # noqa: E402


class ExternalBenchmarkBoundaryTests(unittest.TestCase):
    def test_real_case_path_inside_skill_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "outside the Skill tree"):
            ensure_external_case_path(SKILL_ROOT / "fixtures" / "case.jsonl", SKILL_ROOT)

    def test_external_case_path_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "cases.jsonl"
            self.assertEqual(ensure_external_case_path(path, SKILL_ROOT), path.resolve())

    def test_case_consumers_require_explicit_external_path(self) -> None:
        for script_name in ("evaluate_paper_search.py", "doctor_paper_search.py"):
            completed = subprocess.run(
                [sys.executable, str(SCRIPT_DIR / script_name)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 2, script_name)
            self.assertIn("--cases", completed.stderr)

    def test_sampler_requires_explicit_output(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT_DIR / "sample_paper_search_cases.py")],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("--out", completed.stderr)

    def test_no_real_benchmark_assets_remain(self) -> None:
        benchmark_root = SKILL_ROOT / "benchmarks"
        files = list(benchmark_root.rglob("*")) if benchmark_root.exists() else []
        self.assertFalse([path for path in files if path.is_file()])


if __name__ == "__main__":
    unittest.main()
