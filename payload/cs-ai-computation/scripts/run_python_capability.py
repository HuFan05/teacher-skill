"""Live-check one Python capability, then run a child in the same interpreter and search path."""

from __future__ import annotations

import argparse
from computation_child import run_computation
from computation_projection import CAPABILITIES, capability
from computation_output import SafeParser, add_output_arguments, configure_output, public_main, emit_result as emit_public, _limit
import json
import os
import subprocess
import sys
from pathlib import Path


# Each smoke test imports the checked module, exercises one representative
# operation, and returns (version, device). Passing establishes callability
# only; it says nothing about correctness, determinism, or performance.
SMOKE_TESTS = {
    "numpy_linalg": (
        "numpy",
        "import numpy as np\n"
        "x = np.linalg.solve(np.array([[2.0, 0.0], [0.0, 4.0]]), np.array([2.0, 8.0]))\n"
        "assert abs(float(x[0]) - 1.0) < 1e-12 and abs(float(x[1]) - 2.0) < 1e-12\n"
        "RESULT = (np.__version__, 'cpu')\n",
    ),
    "scipy_stats": (
        "scipy",
        "import scipy\nfrom scipy import stats\n"
        "value = stats.ttest_rel([1.0, 2.0, 3.0, 4.0], [1.5, 2.5, 3.0, 4.5])\n"
        "assert 0.0 <= float(value.pvalue) <= 1.0\n"
        "RESULT = (scipy.__version__, 'cpu')\n",
    ),
    "sympy_diff": (
        "sympy",
        "import sympy as sp\nx = sp.Symbol('x')\n"
        "assert sp.simplify(sp.diff(x ** 3 + 2 * x, x) - (3 * x ** 2 + 2)) == 0\n"
        "RESULT = (sp.__version__, 'cpu')\n",
    ),
    "mpmath_iv": (
        "mpmath",
        "import mpmath\nfrom mpmath import iv\n"
        "sample = iv.mpf([1, 2]) + iv.mpf([3, 4])\n"
        "if sample is None:\n    raise RuntimeError('interval smoke test returned no value')\n"
        "RESULT = (getattr(mpmath, '__version__', None), 'cpu')\n",
    ),
    "z3_smt": (
        "z3",
        "import z3\nx = z3.Int('x')\nsolver = z3.Solver()\nsolver.add(x > 1, x < 3)\n"
        "assert solver.check() == z3.sat and solver.model()[x].as_long() == 2\n"
        "RESULT = (z3.get_version_string(), 'cpu')\n",
    ),
    "hypothesis_pbt": (
        "hypothesis",
        "import hypothesis\nfrom hypothesis import given, settings, strategies as st\n"
        "@settings(max_examples=5, database=None, deadline=None)\n@given(st.integers())\n"
        "def _property(value):\n    assert value + 0 == value\n"
        "_property()\n"
        "RESULT = (hypothesis.__version__, 'cpu')\n",
    ),
    "torch_cpu": (
        "torch",
        "import torch\nassert int((torch.ones(2) + torch.ones(2)).sum().item()) == 4\n"
        "RESULT = (torch.__version__, 'cpu')\n",
    ),
    "torch_cuda": (
        "torch",
        "import torch\nassert torch.cuda.is_available(), 'cuda unavailable'\n"
        "value = torch.ones(2, device='cuda') + 1\ntorch.cuda.synchronize()\n"
        "assert int(value.sum().item()) == 4\n"
        "RESULT = (torch.__version__, 'cuda:' + str(torch.version.cuda or torch.version.hip))\n",
    ),
    "torch_mps": (
        "torch",
        "import torch\nassert torch.backends.mps.is_available(), 'mps unavailable'\n"
        "value = torch.ones(2, device='mps') + 1\nassert int(value.sum().item()) == 4\n"
        "RESULT = (torch.__version__, 'mps')\n",
    ),
    "jax_default": (
        "jax",
        "import jax\nimport jax.numpy as jnp\n"
        "assert int(jnp.sum(jnp.ones(2) + 1)) == 4\n"
        "RESULT = (jax.__version__, jax.default_backend())\n",
    ),
    "tensorflow_default": (
        "tensorflow",
        "import tensorflow as tf\nassert int(tf.reduce_sum(tf.ones(2) + 1).numpy()) == 4\n"
        "gpus = tf.config.list_physical_devices('GPU')\n"
        "RESULT = (tf.__version__, 'gpu' if gpus else 'cpu')\n",
    ),
}
assert tuple(SMOKE_TESTS) == CAPABILITIES


def compact_error(error: BaseException) -> str:
    return "capability_probe_failed"


def capability_probe(name: str, vendor_root: str) -> dict:
    base = {"capability": name, "version": None, "smoke_test": False}
    if vendor_root:
        vendor = Path(vendor_root).expanduser().resolve()
        if not vendor.is_dir():
            return {
                **base,
                "status": "unavailable",
                "source": "vendor_root",
                "vendor_root": str(vendor),
                "error": "vendor root is not a directory",
            }
        sys.path.insert(0, str(vendor))
        source = "vendor_root"
    else:
        vendor = None
        source = "interpreter_environment"
    module, code = SMOKE_TESTS[name]
    namespace: dict = {}
    try:
        exec(compile(code, f"<{name}-smoke-test>", "exec"), namespace)
        version, device = namespace["RESULT"]
        return {
            **base,
            "status": "available",
            "version": None if version is None else str(version),
            "device": str(device),
            "source": source,
            "vendor_root": str(vendor) if vendor else None,
            "smoke_test": True,
            "error": "",
        }
    except Exception as error:  # capability boundary: report, do not leak a traceback
        return {
            **base,
            "status": "unavailable",
            "source": source,
            "vendor_root": str(vendor) if vendor else None,
            "error": compact_error(error),
        }


def build_parser() -> argparse.ArgumentParser:
    parser = SafeParser(
        description="Live-check a Python library, framework, or accelerator capability and run a child with the selected interpreter and configured vendor search path."
    )
    add_output_arguments(parser)
    parser.add_argument("--capability", required=True, choices=list(CAPABILITIES))
    parser.add_argument("--python-command", default=sys.executable)
    parser.add_argument("--vendor-root", default=os.environ.get("CS_AI_COMPUTATION_VENDOR", ""))
    parser.add_argument("--result-parent", default=None, help="Parent for unique complete-result artifacts when needed; defaults to local temporary storage.")
    parser.add_argument("child", nargs=argparse.REMAINDER)
    return parser


def probe_in_selected_interpreter(name: str, python_command: str, vendor_root: str) -> tuple[dict, dict]:
    environment = os.environ.copy()
    if vendor_root:
        resolved_vendor = str(Path(vendor_root).expanduser().resolve())
        environment["CS_AI_COMPUTATION_VENDOR"] = resolved_vendor
        # Both processes receive the approved vendor search path.
        # CS_AI_COMPUTATION_VENDOR alone is metadata and does not affect
        # Python imports in the child.
        prior_path = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = resolved_vendor + (os.pathsep + prior_path if prior_path else "")
    else:
        environment.pop("CS_AI_COMPUTATION_VENDOR", None)
    probe = subprocess.run(
        [python_command, str(Path(__file__).resolve()), "--capability", name, "--python-command", python_command, "--vendor-root", vendor_root, "--"],
        text=True,
        capture_output=True,
        check=False,
        env=environment,
        stdin=subprocess.DEVNULL,
    )
    if probe.returncode != 0:
        return capability({
            "status": "unavailable", "capability": name,
            "source": "runner_probe", "smoke_test": False,
            "error": "capability_probe_failed",
        }), environment
    try:
        payload = json.loads(probe.stdout)
    except json.JSONDecodeError:
        payload = {
            "status": "unavailable",
            "capability": name,
            "version": None,
            "source": "runner_probe",
            "vendor_root": vendor_root or None,
            "smoke_test": False,
            "error": "capability_probe_invalid_response",
        }
    return capability(payload), environment


@public_main
def main() -> int:
    args = build_parser().parse_args()
    configure_output(args)
    child = args.child[1:] if args.child[:1] == ["--"] else args.child

    # An invocation ending at `--` is the internal probe path.
    if not child:
        emit_public(capability(capability_probe(args.capability, args.vendor_root)))
        return 0

    payload, environment = probe_in_selected_interpreter(args.capability, args.python_command, args.vendor_root)
    if payload.get("status") != "available" or payload.get("smoke_test") is not True:
        payload["action"] = f"Select a Python environment in which {args.capability} passes, or set CS_AI_COMPUTATION_VENDOR/--vendor-root to a stable local package root."
        emit_public({"ok": False, **payload})
        return 3

    result = run_computation([args.python_command, *child], environment, args.result_parent, _limit.get())
    emit_public(result)
    return result.get("child_exit_code", 2)


if __name__ == "__main__":
    raise SystemExit(main())
