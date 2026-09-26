from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "run_python_capability.py"


def _runner(capability: str, vendor_root: Path, child: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--capability",
            capability,
            "--python-command",
            sys.executable,
            "--vendor-root",
            str(vendor_root),
            "--",
            str(child),
        ],
        text=True,
        capture_output=True,
        check=False,
    )


class PythonCapabilityRunnerTests(unittest.TestCase):
    def test_vendor_capability_and_child_use_the_same_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "mpmath"
            package.mkdir()
            (package / "__init__.py").write_text(
                '__version__ = "test-vendor"\n'
                "class _Value:\n"
                "    def __add__(self, other): return self\n"
                "class _IV:\n"
                "    @staticmethod\n"
                "    def mpf(value): return _Value()\n"
                "iv = _IV()\n",
                encoding="utf-8",
            )
            child_dir = root / "independent-child"
            child_dir.mkdir()
            child = child_dir / "child.py"
            child.write_text(
                "import json, mpmath, os, sys\n"
                "print(json.dumps({'version': mpmath.__version__, 'executable': sys.executable, 'vendor': os.environ.get('CS_AI_COMPUTATION_VENDOR')}))\n",
                encoding="utf-8",
            )
            process = _runner("mpmath_iv", root, child)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            envelope = json.loads(process.stdout)
            self.assertTrue(envelope["result_complete"])
            payload = envelope["result"]
            self.assertEqual(payload["version"], "test-vendor")
            self.assertEqual(Path(payload["executable"]).resolve(), Path(sys.executable).resolve())
            self.assertEqual(Path(payload["vendor"]).resolve(), root.resolve())

    def test_framework_capability_checks_the_selected_device(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "torch"
            package.mkdir()
            # A stand-in framework whose CPU path works but whose accelerator is absent.
            (package / "__init__.py").write_text(
                '__version__ = "test-framework"\n'
                "class _Tensor:\n"
                "    def __init__(self, value): self.value = value\n"
                "    def __add__(self, other): return _Tensor(self.value + (other.value if isinstance(other, _Tensor) else other))\n"
                "    def sum(self): return _Tensor(self.value)\n"
                "    def item(self): return self.value\n"
                "def ones(size, device='cpu'): return _Tensor(size)\n"
                "class cuda:\n"
                "    @staticmethod\n"
                "    def is_available(): return False\n",
                encoding="utf-8",
            )
            marker = root / "child-ran.txt"
            child = root / "child.py"
            child.write_text(
                "from pathlib import Path\n"
                f"Path({str(marker)!r}).write_text('ran', encoding='utf-8')\n"
                "print('{}')\n",
                encoding="utf-8",
            )
            cpu = _runner("torch_cpu", root, child)
            self.assertEqual(cpu.returncode, 0, cpu.stdout + cpu.stderr)
            self.assertTrue(marker.exists())
            marker.unlink()

            cuda = _runner("torch_cuda", root, child)
            self.assertEqual(cuda.returncode, 3)
            self.assertFalse(marker.exists())
            payload = json.loads(cuda.stdout)
            self.assertEqual(payload["capability"], "torch_cuda")
            self.assertFalse(payload["smoke_test"])

    def test_failed_capability_blocks_child_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "mpmath"
            package.mkdir()
            (package / "__init__.py").write_text("raise RuntimeError('blocked-test')\n", encoding="utf-8")
            marker = root / "child-ran.txt"
            child_dir = root / "independent-child"
            child_dir.mkdir()
            child = child_dir / "child.py"
            child.write_text(
                "from pathlib import Path\n"
                f"Path({str(marker)!r}).write_text('ran', encoding='utf-8')\n",
                encoding="utf-8",
            )
            process = _runner("mpmath_iv", root, child)
            self.assertEqual(process.returncode, 3)
            self.assertFalse(marker.exists())
            payload = json.loads(process.stdout)
            self.assertEqual(payload["status"], "unavailable")
            self.assertFalse(payload["smoke_test"])
            self.assertNotIn("blocked-test", process.stdout + process.stderr)
            self.assertTrue(payload["error"])
            self.assertNotIn("Traceback", process.stderr)

    def test_unknown_capability_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child = root / "child.py"
            child.write_text("print(1)\n", encoding="utf-8")
            process = _runner("not_a_capability", root, child)
            self.assertNotEqual(process.returncode, 0)
            self.assertEqual(json.loads(process.stdout)["code"], "invalid_request")


if __name__ == "__main__":
    unittest.main()
