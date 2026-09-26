from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
SCRIPT_PATH = SCRIPTS_DIR / "backend_inventory.py"
POWERSHELL_SCRIPT_PATH = SCRIPTS_DIR / "backend_inventory.ps1"
SCHEMA_PATH = SCRIPTS_DIR.parent / "references" / "backend-inventory.schema.json"
ALL_BACKENDS = ["python", "accelerators", "smt_solvers", "proof_assistants", "model_checkers", "profilers"]
GUIDED_LIBRARIES = ("torch", "jax", "tensorflow", "scipy", "z3", "hypothesis")


def _architecture() -> str:
    value = platform.machine().lower()
    return {"amd64": "x86_64", "x64": "x86_64", "aarch64": "arm64", "i386": "x86", "i686": "x86"}.get(value, value)


def _tool(path: str | None = None, status: str = "unavailable") -> dict:
    return {
        "status": status,
        "requested_command": "tool",
        "discovery_source": "path",
        "path": path,
        "version_output": "1.0.0" if status == "available" else "",
        "exit_code": 0 if status == "available" else None,
        "error": "",
    }


def _probe_data(z3_path: str | None = None, z3_status: str = "unavailable") -> dict:
    return {
        "schema_version": "1.0",
        "probed_at_utc": datetime.now(timezone.utc).isoformat(),
        "host": {"system": platform.system(), "architecture": _architecture()},
        "hardware": {"logical_cpus": 4, "memory_bytes": 8 * 1024 ** 3, "cpu_model": "test cpu"},
        "python": {
            "status": "available",
            "requested_command": "python",
            "path": sys.executable,
            "version": sys.version.split()[0],
            "libraries": {"torch": {"available": True, "version": "2.4.0"}},
            "exit_code": 0,
            "error": "",
            "wsl": {"status": "not_requested"},
        },
        "accelerators": {
            "nvidia_smi": {**_tool(), "gpus": []},
            "frameworks": {"torch": {"status": "available", "version": "2.4.0", "cuda_available": False, "mps_available": False}},
            "apple_silicon": False,
        },
        "smt_solvers": {"z3": _tool(z3_path, z3_status), "cvc5": _tool()},
        "proof_assistants": {"lean": _tool(), "coq": _tool(), "isabelle": _tool()},
        "model_checkers": {"spin": _tool(), "cbmc": _tool()},
        "profilers": {"hyperfine": _tool(), "py_spy": _tool(), "perf": _tool(), "nsys": _tool(), "ncu": _tool()},
    }


class BackendInventoryTests(unittest.TestCase):
    def assert_python_library_guidance(self, data: dict) -> None:
        libraries = data["local"]["python"]["libraries"]
        for name in GUIDED_LIBRARIES:
            self.assertIn(name, libraries)
            for field in ("purpose", "evidence_boundary", "live_check_requirement"):
                self.assertTrue(libraries[name][field])
        self.assertTrue(libraries["torch"]["available"])
        self.assertFalse(libraries["jax"]["available"])
        self.assertIn("not_a_correctness_proof", libraries["torch"]["evidence_boundary"])
        self.assertEqual(
            libraries["z3"]["live_check_requirement"],
            "use_run_python_capability_z3_smt_before_material_queries",
        )

    def _run(self, state: Path, fixture: Path, *extra: str) -> tuple[dict, float]:
        command = [
            sys.executable,
            str(SCRIPT_PATH),
            "--state-file",
            str(state),
            "--probe-json-file",
            str(fixture),
            *extra,
        ]
        started = time.perf_counter()
        process = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )
        elapsed = time.perf_counter() - started
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        return json.loads(process.stdout), elapsed

    def test_create_then_cache_hit_starts_no_backend_and_meets_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state = root / "state" / "inventory.json"
            fixture = root / "probe.json"
            fixture.write_text(json.dumps(_probe_data()), encoding="utf-8")

            created, _ = self._run(state, fixture)
            self.assertEqual(created["cache"]["status"], "created")
            self.assertTrue(created["cache"]["backend_started"])
            self.assertNotIn("mcp", created["local"])
            self.assertEqual(created["local"]["hardware"]["logical_cpus"], 4)
            self.assertEqual(created["local"]["accelerators"]["frameworks"]["torch"]["status"], "available")
            self.assert_python_library_guidance(created)
            stored_before_hit = state.read_bytes()

            fixture.write_text("this must not be read on a cache hit", encoding="utf-8")
            hit, wall_seconds = self._run(state, fixture)
            self.assertEqual(hit["cache"]["status"], "hit")
            self.assertFalse(hit["cache"]["backend_started"])
            self.assert_python_library_guidance(hit)
            self.assertEqual(state.read_bytes(), stored_before_hit)
            self.assertLessEqual(hit["cache"]["elapsed_ms"], 250)
            self.assertLess(wall_seconds, 2.0)

    def test_missing_recorded_path_refreshes_only_affected_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            executable = root / "z3.exe"
            executable.write_bytes(b"test")
            state = root / "inventory.json"
            fixture = root / "probe.json"
            fixture.write_text(json.dumps(_probe_data(str(executable), "available")), encoding="utf-8")
            self._run(state, fixture)

            executable.unlink()
            fixture.write_text(json.dumps(_probe_data()), encoding="utf-8")
            refreshed, _ = self._run(state, fixture)
            self.assertEqual(refreshed["cache"]["status"], "refreshed")
            self.assertEqual(refreshed["cache"]["invalid_path_backends"], ["smt_solvers"])
            self.assertEqual(refreshed["cache"]["refreshed_backends"], ["smt_solvers"])
            self.assertEqual(refreshed["local"]["smt_solvers"]["z3"]["status"], "unavailable")

    def test_explicit_invalidation_records_reason_then_refreshes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state = root / "inventory.json"
            fixture = root / "probe.json"
            fixture.write_text(json.dumps(_probe_data()), encoding="utf-8")
            self._run(state, fixture)

            refreshed, _ = self._run(
                state,
                fixture,
                "--mode",
                "Invalidate",
                "--backend",
                "accelerators",
                "--reason-code",
                "device_unavailable",
            )
            self.assertEqual(refreshed["cache"]["refreshed_backends"], ["accelerators"])
            stored = json.loads(state.read_text(encoding="utf-8"))
            self.assertEqual(stored["invalidations"][-1]["backend"], "accelerators")
            self.assertEqual(stored["invalidations"][-1]["reason"], "device_unavailable")

    def test_invalidation_rejects_unbounded_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state = root / "inventory.json"
            fixture = root / "probe.json"
            fixture.write_text(json.dumps(_probe_data()), encoding="utf-8")
            self._run(state, fixture)
            process = subprocess.run(
                [sys.executable, str(SCRIPT_PATH), "--state-file", str(state), "--probe-json-file", str(fixture),
                 "--mode", "Invalidate", "--backend", "python", "--reason-code", "Not A Code"],
                text=True, capture_output=True, check=False, timeout=15,
            )
            self.assertNotEqual(process.returncode, 0)

    def test_no_write_probes_without_creating_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state = root / "state" / "inventory.json"
            fixture = root / "probe.json"
            fixture.write_text(json.dumps(_probe_data()), encoding="utf-8")
            result, _ = self._run(state, fixture, "--no-write")
            self.assertEqual(result["cache"]["status"], "not_persisted")
            self.assertTrue(result["cache"]["backend_started"])
            self.assertFalse(state.exists())
            self.assertFalse(state.parent.exists())

    def test_schema_declares_session_mcp_as_historical(self) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        mcp = schema["properties"]["mcp"]["properties"]
        self.assertEqual(mcp["persisted_status"]["const"], "historical_only")
        self.assertEqual(
            mcp["authority"]["const"],
            "current_session_tool_discovery_and_call",
        )
        self.assertIn("host", schema["properties"]["local"]["required"])
        self.assertEqual(sorted(schema["properties"]["local"]["required"][4:]), sorted(ALL_BACKENDS))
        observation = mcp["observations"]["additionalProperties"]
        self.assertIn("protocol_version", observation["properties"])
        self.assertIn("runtime_version", observation["properties"])

    def test_record_mcp_persists_protocol_and_distinct_versions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state = root / "inventory.json"
            fixture = root / "probe.json"
            fixture.write_text(json.dumps(_probe_data()), encoding="utf-8")
            self._run(state, fixture)
            recorded, _ = self._run(
                state,
                fixture,
                "--mode", "RecordMcp",
                "--mcp-server-name", "example-code-sandbox",
                "--mcp-protocol-version", "2025-06-18",
                "--mcp-server-version", "0.9.1",
                "--mcp-runtime-version", "Python 3.12.4",
            )
            observations = recorded["mcp"]["recorded_mcp_observations"]
            self.assertEqual(recorded["cache"]["status"], "mcp_recorded")
            self.assertFalse(recorded["cache"]["backend_started"])
            self.assertEqual(len(observations), 1)
            self.assertEqual(observations[0]["protocol_version"], "2025-06-18")
            self.assertEqual(observations[0]["server_version"], "0.9.1")
            self.assertEqual(observations[0]["runtime_version"], "Python 3.12.4")
            stored = json.loads(state.read_text(encoding="utf-8"))
            self.assertEqual(stored["mcp"]["persisted_status"], "historical_only")
            self.assertEqual(stored["mcp"]["observations"]["example-code-sandbox"]["evidence"], "initialize_handshake_and_execution_call")

    def test_record_mcp_rejects_partial_or_non_protocol_version(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state = root / "inventory.json"
            fixture = root / "probe.json"
            fixture.write_text(json.dumps(_probe_data()), encoding="utf-8")
            self._run(state, fixture)
            for extra in (
                ["--mcp-server-name", "example-code-sandbox"],
                ["--mcp-server-name", "example-code-sandbox", "--mcp-protocol-version", "0.9.1", "--mcp-server-version", "0.9.1", "--mcp-runtime-version", "3.12.4"],
            ):
                process = subprocess.run(
                    [sys.executable, str(SCRIPT_PATH), "--state-file", str(state), "--probe-json-file", str(fixture), "--mode", "RecordMcp", *extra],
                    text=True, capture_output=True, check=False, timeout=15,
                )
                self.assertNotEqual(process.returncode, 0)

    def test_snapshot_from_another_host_is_refreshed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state = root / "inventory.json"
            fixture = root / "probe.json"
            probe = _probe_data()
            fixture.write_text(json.dumps(probe), encoding="utf-8")
            self._run(state, fixture)

            stored = json.loads(state.read_text(encoding="utf-8"))
            stored["local"]["host"]["system"] = "Darwin" if probe["host"]["system"] != "Darwin" else "Linux"
            state.write_text(json.dumps(stored), encoding="utf-8")
            refreshed, _ = self._run(state, fixture)
            self.assertEqual(refreshed["cache"]["status"], "refreshed")
            self.assertEqual(refreshed["cache"]["refreshed_backends"], ALL_BACKENDS)

    def test_snapshot_missing_a_backend_record_is_rebuilt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state = root / "inventory.json"
            fixture = root / "probe.json"
            fixture.write_text(json.dumps(_probe_data()), encoding="utf-8")
            self._run(state, fixture)
            stored = json.loads(state.read_text(encoding="utf-8"))
            del stored["local"]["profilers"]
            state.write_text(json.dumps(stored), encoding="utf-8")
            rebuilt, _ = self._run(state, fixture)
            self.assertEqual(rebuilt["cache"]["status"], "created")
            self.assertIn("profilers", rebuilt["local"])


@unittest.skipUnless(shutil.which("pwsh"), "PowerShell 7 is unavailable")
class PowerShellInventoryCompatibilityTests(unittest.TestCase):
    def test_powershell_reads_python_created_snapshot_without_starting_backends(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state = root / "inventory.json"
            fixture = root / "probe.json"
            fixture.write_text(json.dumps(_probe_data()), encoding="utf-8")
            create = subprocess.run(
                [sys.executable, str(SCRIPT_PATH), "--state-file", str(state), "--probe-json-file", str(fixture)],
                text=True,
                capture_output=True,
                check=False,
                timeout=15,
            )
            self.assertEqual(create.returncode, 0, create.stdout + create.stderr)
            fixture.write_text("cache hit must not read this", encoding="utf-8")
            hit = subprocess.run(
                ["pwsh", "-NoLogo", "-NoProfile", "-File", str(POWERSHELL_SCRIPT_PATH), "-StateFile", str(state), "-ProbeJsonFile", str(fixture)],
                text=True,
                capture_output=True,
                check=False,
                timeout=15,
            )
            self.assertEqual(hit.returncode, 0, hit.stdout + hit.stderr)
            data = json.loads(hit.stdout)
            self.assertEqual(data["cache"]["status"], "hit")
            self.assertFalse(data["cache"]["backend_started"])
            libraries = data["local"]["python"]["libraries"]
            for name in GUIDED_LIBRARIES:
                self.assertIn("live_check_requirement", libraries[name])
            self.assertEqual(libraries["z3"]["purpose"], "smt_solving_models_and_unsat_answers_through_the_python_binding")

    def test_powershell_records_negotiated_mcp_protocol_version(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state = root / "inventory.json"
            fixture = root / "probe.json"
            fixture.write_text(json.dumps(_probe_data()), encoding="utf-8")
            create = subprocess.run(
                [sys.executable, str(SCRIPT_PATH), "--state-file", str(state), "--probe-json-file", str(fixture)],
                text=True, capture_output=True, check=False, timeout=15,
            )
            self.assertEqual(create.returncode, 0, create.stdout + create.stderr)
            record = subprocess.run(
                [
                    "pwsh", "-NoLogo", "-NoProfile", "-File", str(POWERSHELL_SCRIPT_PATH),
                    "-Mode", "RecordMcp", "-StateFile", str(state),
                    "-McpServerName", "example-code-sandbox",
                    "-McpProtocolVersion", "2025-06-18",
                    "-McpServerVersion", "0.9.1",
                    "-McpRuntimeVersion", "Python 3.12.4",
                ],
                text=True, capture_output=True, check=False, timeout=15,
            )
            self.assertEqual(record.returncode, 0, record.stdout + record.stderr)
            data = json.loads(record.stdout)
            observation = data["mcp"]["recorded_mcp_observations"][0]
            self.assertEqual(observation["protocol_version"], "2025-06-18")
            self.assertEqual(observation["server_version"], "0.9.1")


if __name__ == "__main__":
    unittest.main()
