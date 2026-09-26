from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "computation_record.py"
if str(SCRIPT_PATH.parent) not in sys.path:
    sys.path.insert(0, str(SCRIPT_PATH.parent))
SPEC = importlib.util.spec_from_file_location("computation_record", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ComputationRecordTests(unittest.TestCase):
    def make_record(
        self,
        root: Path,
        *,
        selected_backend: str = "python",
        mode: str = "exact",
        evidence_grade: str = "exact_reproduction",
    ) -> Path:
        task = root / "task.md"
        code = root / "evaluate.py"
        result = root / "result.json"
        config = root / "config.json"
        dataset = root / "data.csv"
        record_path = root / "computation-record.json"
        task.write_text("Evaluate a synthetic classifier on a fixed split.\n", encoding="utf-8")
        code.write_text("print(6 * 7)\n", encoding="utf-8")
        result.write_text('{"accuracy": 0.75}\n', encoding="utf-8")
        config.write_text('{"threshold": 0.5}\n', encoding="utf-8")
        dataset.write_text("x,y\n0,0\n1,1\n", encoding="utf-8")

        process = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "init",
                "--task-file",
                str(task),
                "--record",
                str(record_path),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["task"]["object"] = "synthetic threshold classifier evaluation"
        record["task"]["deliverables"] = ["evaluate.py", "result.json"]
        record["problem_context"]["assumptions"] = ["fixed evaluation split"]
        record["problem_context"]["domain"] = "binary labels on the synthetic split"
        record["problem_context"]["declared_range"] = "one dataset version, seeds 0-2"
        record["problem_context"]["precision"] = {
            "mode": mode,
            "working_digits": None,
            "target_tolerance": "1e-6 absolute on accuracy" if mode != "exact" else "",
        }
        record["implementation_discovery"] = {
            "python": {
                "candidate_implementations": ["standard-library metric"],
                "existence_evidence": "documented synthetic candidate",
                "local_availability": "available",
                "availability_evidence": "synthetic python probe",
            },
            "pytorch": {
                "candidate_implementations": [],
                "existence_evidence": "targeted check found no need for a framework",
                "local_availability": "unavailable",
                "availability_evidence": "synthetic inventory record",
            },
        }
        record["decision"] = {
            "selected_backend": selected_backend,
            "backend_version": "test-version",
            "selection_reason": "selected for the synthetic fixture",
            "fallback_reason": "not-required",
        }
        record["environment"] = {
            "os": "test-os",
            "python": "3.x",
            "packages": {"numpy": "0.0-test"},
            "accelerator_runtime": "none",
            "driver": "",
        }
        record["hardware"] = {"cpu": "test cpu", "accelerators": [], "memory": "8 GiB"}
        record["seeds"] = {"values": [0, 1, 2], "policy": "fixed seeds; deterministic evaluation"}
        record["datasets"] = [
            {"id": "synthetic-split", "version": "v1", "sha256": file_hash(dataset), "path": "data.csv"},
            {"id": "external-reference", "version": "2024-01", "sha256": "a" * 64},
        ]
        record["configuration"] = {"summary": "threshold 0.5", "file": "config.json", "sha256": file_hash(config)}
        record["execution"] = {
            "status": "complete",
            "interface": "local process",
            "command_or_input": "python evaluate.py --config config.json",
            "code_artifact": "evaluate.py",
        }
        record["artifacts"] = [
            {"role": "code", "path": "evaluate.py", "sha256": file_hash(code)},
            {"role": "result", "path": "result.json", "sha256": file_hash(result)},
        ]
        record["result"] = {
            "status": "complete",
            "summary": "Accuracy 0.75 on the synthetic split.",
            "result_artifact": "result.json",
            "metrics": {"accuracy": 0.75},
        }
        record["verification"] = {
            "methods": ["rerun with identical seeds"],
            "evidence_grade": evidence_grade,
            "checker": "",
            "residual_or_error": "max abs difference 0" if mode != "exact" else "",
            "limitations": ["synthetic fixture"],
        }
        record_path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return record_path

    def mutate(self, record_path: Path, change) -> None:
        record = json.loads(record_path.read_text(encoding="utf-8"))
        change(record)
        record_path.write_text(json.dumps(record), encoding="utf-8")

    def test_valid_experiment_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            record_path = self.make_record(Path(temporary))
            result = MODULE.validate_record(record_path, None)
            self.assertTrue(result["ok"])
            self.assertEqual(result["selected_backend"], "python")
            self.assertEqual(result["evidence_grade"], "exact_reproduction")
            self.assertEqual(len(result["verified_artifacts"]), 2)
            self.assertEqual(result["dataset_count"], 2)
            self.assertEqual(result["unverified_dataset_hashes"], 1)

    def test_valid_numerical_record_requires_precision_and_residual(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            record_path = self.make_record(
                Path(temporary), mode="fp32", evidence_grade="numerical_evidence"
            )
            result = MODULE.validate_record(record_path, None)
            self.assertEqual(result["evidence_grade"], "numerical_evidence")

    def test_rejects_legacy_evidence_label(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            record_path = self.make_record(Path(temporary))
            self.mutate(record_path, lambda r: r["verification"].update(evidence_grade="exact-check"))
            with self.assertRaisesRegex(MODULE.RecordError, "evidence_grade"):
                MODULE.validate_record(record_path, None)

    def test_certificate_and_formal_grades_require_checker(self) -> None:
        for grade in ("certificate", "formal"):
            with tempfile.TemporaryDirectory() as temporary:
                record_path = self.make_record(Path(temporary), evidence_grade=grade)
                with self.assertRaisesRegex(MODULE.RecordError, "checker"):
                    MODULE.validate_record(record_path, None)
                self.mutate(record_path, lambda r: r["verification"].update(checker="z3 4.13 model substitution"))
                self.assertEqual(MODULE.validate_record(record_path, None)["evidence_grade"], grade)

    def test_bounded_empirical_requires_declared_range(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            record_path = self.make_record(Path(temporary), evidence_grade="bounded_empirical")
            self.assertEqual(MODULE.validate_record(record_path, None)["evidence_grade"], "bounded_empirical")
            self.mutate(record_path, lambda r: r["problem_context"].update(declared_range=""))
            with self.assertRaisesRegex(MODULE.RecordError, "declared_range"):
                MODULE.validate_record(record_path, None)

    def test_rejects_bad_artifact_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            record_path = self.make_record(Path(temporary))
            self.mutate(record_path, lambda r: r["artifacts"][0].update(sha256="0" * 64))
            with self.assertRaisesRegex(MODULE.RecordError, "does not match"):
                MODULE.validate_record(record_path, None)

    def test_rejects_bad_local_dataset_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            record_path = self.make_record(Path(temporary))
            self.mutate(record_path, lambda r: r["datasets"][0].update(sha256="0" * 64))
            with self.assertRaisesRegex(MODULE.RecordError, "datasets\\[0\\].sha256 does not match"):
                MODULE.validate_record(record_path, None)

    def test_rejects_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            record_path = self.make_record(Path(temporary))
            self.mutate(record_path, lambda r: r["artifacts"][0].update(path="../outside.py"))
            with self.assertRaisesRegex(MODULE.RecordError, "escapes"):
                MODULE.validate_record(record_path, None)

    def test_rejects_missing_fallback_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            record_path = self.make_record(Path(temporary))
            self.mutate(record_path, lambda r: r["decision"].update(fallback_reason=""))
            with self.assertRaisesRegex(MODULE.RecordError, "fallback_reason"):
                MODULE.validate_record(record_path, None)

    def test_rejects_unavailable_or_undiscovered_selected_backend(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            record_path = self.make_record(Path(temporary), selected_backend="pytorch")
            with self.assertRaisesRegex(MODULE.RecordError, "local_availability=available"):
                MODULE.validate_record(record_path, None)
            self.mutate(record_path, lambda r: r["decision"].update(selected_backend="jax"))
            with self.assertRaisesRegex(MODULE.RecordError, "implementation_discovery entry"):
                MODULE.validate_record(record_path, None)

    def test_rejects_missing_seed_policy_and_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            record_path = self.make_record(Path(temporary))
            self.mutate(record_path, lambda r: r["seeds"].update(policy=""))
            with self.assertRaisesRegex(MODULE.RecordError, "seeds.policy"):
                MODULE.validate_record(record_path, None)
        with tempfile.TemporaryDirectory() as temporary:
            record_path = self.make_record(Path(temporary))
            self.mutate(record_path, lambda r: r["environment"].update(accelerator_runtime=""))
            with self.assertRaisesRegex(MODULE.RecordError, "accelerator_runtime"):
                MODULE.validate_record(record_path, None)

    def test_rejects_numerical_record_without_residual(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            record_path = self.make_record(
                Path(temporary), mode="bf16", evidence_grade="numerical_evidence"
            )
            self.mutate(record_path, lambda r: r["verification"].update(residual_or_error=""))
            with self.assertRaisesRegex(MODULE.RecordError, "residual_or_error"):
                MODULE.validate_record(record_path, None)

    def test_cli_validation_returns_fixed_code(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            record_path = self.make_record(Path(temporary))
            process = subprocess.run(
                [sys.executable, str(SCRIPT_PATH), "validate", "--record", str(record_path)],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(process.returncode, 0, process.stdout)
            self.assertTrue(json.loads(process.stdout)["ok"])
            self.mutate(record_path, lambda r: r["result"].update(metrics={"accuracy": True}))
            process = subprocess.run(
                [sys.executable, str(SCRIPT_PATH), "validate", "--record", str(record_path)],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(process.returncode, 1)
            self.assertEqual(json.loads(process.stdout)["error"], "record_validation_failed")


if __name__ == "__main__":
    unittest.main()
