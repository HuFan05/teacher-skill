from __future__ import annotations

import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
SCRIPT_PATH = SCRIPTS_DIR / "probe_backends.py"
POWERSHELL_SCRIPT_PATH = SCRIPTS_DIR / "probe_backends.ps1"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _load_module():
    spec = importlib.util.spec_from_file_location("probe_backends_module", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(*arguments: str) -> tuple[int, dict]:
    # `--framework-devices` without values keeps tests from importing any
    # installed deep-learning framework.
    process = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--framework-devices", *arguments],
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )
    return process.returncode, json.loads(process.stdout)


class ProbeBackendsTests(unittest.TestCase):
    def test_probe_returns_stable_json_shape_without_implicit_wsl(self) -> None:
        code, data = _run()
        self.assertEqual(code, 0, data)
        self.assertEqual(data["schema_version"], "1.0")
        self.assertEqual(data["mcp"]["status"], "requires_agent_probe")
        self.assertIn(data["host"]["system"], {"Windows", "Darwin", "Linux"})
        self.assertTrue(data["host"]["architecture"])
        self.assertIn("logical_cpus", data["hardware"])
        self.assertIn(data["python"]["status"], {"available", "unavailable", "probe_failed"})
        self.assertEqual(data["python"]["wsl"]["status"], "not_requested")
        if data["python"]["status"] == "available":
            for name in ("numpy", "torch", "jax", "tensorflow", "z3", "hypothesis"):
                self.assertIsInstance(data["python"]["libraries"][name]["available"], bool)
        accelerators = data["accelerators"]
        self.assertIn(accelerators["nvidia_smi"]["status"], {"available", "unavailable", "probe_failed"})
        self.assertIsInstance(accelerators["nvidia_smi"]["gpus"], list)
        for name in ("torch", "jax", "tensorflow"):
            self.assertEqual(accelerators["frameworks"][name]["status"], "not_requested")
        self.assertEqual(accelerators["apple_silicon"], platform.system() == "Darwin" and platform.machine().lower() == "arm64")
        for group, names in {
            "smt_solvers": ("z3", "cvc5"),
            "proof_assistants": ("lean", "coq", "isabelle"),
            "model_checkers": ("spin", "cbmc"),
            "profilers": ("hyperfine", "py_spy", "perf", "nsys", "ncu"),
        }.items():
            for name in names:
                self.assertIn(data[group][name]["status"], {"available", "unavailable", "probe_failed"})
                self.assertFalse(data[group][name].get("version_banner_returned", False))

    def test_only_selected_backends_are_probed(self) -> None:
        code, data = _run("--only", "smt_solvers")
        self.assertEqual(code, 0, data)
        self.assertIn("smt_solvers", data)
        for name in ("python", "accelerators", "proof_assistants", "model_checkers", "profilers"):
            self.assertNotIn(name, data)

    def test_explicit_tool_probe_success(self) -> None:
        code, data = _run("--only", "smt_solvers", "--tool-command", f"z3={sys.executable}")
        self.assertEqual(code, 0, data)
        record = data["smt_solvers"]["z3"]
        self.assertEqual(record["status"], "available")
        self.assertEqual(record["discovery_source"], "explicit")
        self.assertEqual(record["version_output"], platform.python_version())
        self.assertFalse(record["version_banner_returned"])

    def test_explicit_missing_tool_does_not_fall_through(self) -> None:
        missing = Path(__file__).resolve().parent / "definitely-missing-z3.exe"
        code, data = _run("--only", "smt_solvers", "--tool-command", f"z3={missing}")
        self.assertEqual(code, 0, data)
        record = data["smt_solvers"]["z3"]
        self.assertEqual(record["status"], "unavailable")
        self.assertEqual(record["discovery_source"], "explicit")
        self.assertIsNone(record["path"])

    def test_unknown_tool_override_is_rejected(self) -> None:
        code, data = _run("--tool-command", "not_a_tool=/bin/true")
        self.assertNotEqual(code, 0)
        self.assertEqual(data["code"], "invalid_request")

    def test_wsl_request_is_rejected_as_unsupported_off_windows(self) -> None:
        module = _load_module()
        data = module.probe_wsl("Example", "python3", "Darwin")
        self.assertEqual(data["status"], "unsupported_platform")

    def test_framework_device_probe_skips_absent_library(self) -> None:
        module = _load_module()
        self.assertEqual(module.probe_framework("torch", sys.executable, {"torch": {"available": False}}, True, ""), {"status": "unavailable"})
        self.assertEqual(module.probe_framework("jax", sys.executable, {"jax": {"available": True}}, False, ""), {"status": "not_requested"})

    def test_elan_proxy_maps_to_default_toolchain_without_running_elan(self) -> None:
        module = _load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            (home / "bin").mkdir()
            proxy = home / "bin" / "lean"
            proxy.write_text("proxy must not run", encoding="utf-8")
            toolchain = home / "toolchains" / "leanprover--lean4---v4.9.0" / "bin"
            toolchain.mkdir(parents=True)
            (toolchain / "lean").write_text("toolchain binary", encoding="utf-8")
            (home / "settings.toml").write_text('default_toolchain = "leanprover/lean4:v4.9.0"\n', encoding="utf-8")
            mapped = module.resolve_elan_proxy(str(proxy), home)
            self.assertEqual(mapped["manager"], "elan")
            self.assertEqual(mapped["toolchain"], "leanprover/lean4:v4.9.0")
            self.assertEqual(Path(mapped["path"]), (toolchain / "lean").resolve())

            (home / "settings.toml").write_text('default_toolchain = "leanprover/lean4:v9.9.9"\n', encoding="utf-8")
            unresolved = module.resolve_elan_proxy(str(proxy), home)
            self.assertIsNone(unresolved["path"])
            self.assertIsNone(module.resolve_elan_proxy(sys.executable, home))

    def test_probe_never_writes_into_the_working_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            process = subprocess.run(
                [sys.executable, str(SCRIPT_PATH), "--framework-devices"],
                text=True, capture_output=True, check=False, timeout=120, cwd=temp_dir,
            )
            self.assertEqual(process.returncode, 0, process.stdout)
            self.assertEqual(os.listdir(temp_dir), [])


@unittest.skipUnless(shutil.which("pwsh"), "PowerShell 7 is unavailable")
class PowerShellCompatibilityTests(unittest.TestCase):
    def test_powershell_entry_keeps_the_same_shape(self) -> None:
        process = subprocess.run(
            ["pwsh", "-NoLogo", "-NoProfile", "-File", str(POWERSHELL_SCRIPT_PATH), "-FrameworkDevices", "none"],
            text=True,
            capture_output=True,
            check=False,
            timeout=120,
        )
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        data = json.loads(process.stdout)
        self.assertEqual(data["schema_version"], "1.0")
        self.assertIn(data["host"]["system"], {"Windows", "Darwin", "Linux"})
        self.assertIn("z3", data["smt_solvers"])
        self.assertEqual(data["accelerators"]["frameworks"]["torch"]["status"], "not_requested")


if __name__ == "__main__":
    unittest.main()
