#!/usr/bin/env python3
"""Cross-platform local backend probe for cs-ai-computation; never installs anything."""

from __future__ import annotations

import argparse
from computation_projection import BACKENDS, local_inventory
from computation_output import InputError, SafeParser, add_output_arguments, configure_output, public_main, emit_result as emit_public
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


VERSION_TIMEOUT_SECONDS = 20
PYTHON_TIMEOUT_SECONDS = 30
FRAMEWORK_TIMEOUT_SECONDS = 120
DEFAULT_VERSION_PATTERN = r"(?<![\w.])\d{1,4}(?:\.\d{1,4}){1,4}(?![\w.])"

# group -> (tool key, candidate commands, version arguments, version pattern)
TOOL_SPECS: dict[str, tuple[tuple[str, tuple[str, ...], tuple[str, ...], str], ...]] = {
    "smt_solvers": (
        ("z3", ("z3",), ("--version",), DEFAULT_VERSION_PATTERN),
        ("cvc5", ("cvc5",), ("--version",), DEFAULT_VERSION_PATTERN),
    ),
    "proof_assistants": (
        ("lean", ("lean",), ("--version",), DEFAULT_VERSION_PATTERN),
        ("coq", ("coqc", "rocq"), ("--version",), DEFAULT_VERSION_PATTERN),
        ("isabelle", ("isabelle",), ("version",), r"Isabelle(\d{4}(?:-\d+)?)"),
    ),
    "model_checkers": (
        ("spin", ("spin",), ("-V",), DEFAULT_VERSION_PATTERN),
        ("cbmc", ("cbmc",), ("--version",), DEFAULT_VERSION_PATTERN),
    ),
    "profilers": (
        ("hyperfine", ("hyperfine",), ("--version",), DEFAULT_VERSION_PATTERN),
        ("py_spy", ("py-spy",), ("--version",), DEFAULT_VERSION_PATTERN),
        ("perf", ("perf",), ("--version",), DEFAULT_VERSION_PATTERN),
        ("nsys", ("nsys",), ("--version",), DEFAULT_VERSION_PATTERN),
        ("ncu", ("ncu",), ("--version",), DEFAULT_VERSION_PATTERN),
    ),
}
OVERRIDABLE_TOOLS = frozenset({"nvidia_smi", *(spec[0] for specs in TOOL_SPECS.values() for spec in specs)})
FRAMEWORKS = ("torch", "jax", "tensorflow")

PYTHON_PROBE_CODE = r'''
import importlib.metadata, importlib.util, json, pathlib, sys
sys.path[:] = [entry for entry in sys.path if entry not in ("", ".")]
vendor_root = sys.argv[1] if len(sys.argv) > 1 else ""
if vendor_root:
    vendor = pathlib.Path(vendor_root).expanduser().resolve()
    if vendor.is_dir(): sys.path.insert(0, str(vendor))
modules = {"numpy":["numpy"],"scipy":["scipy"],"sympy":["sympy"],"pandas":["pandas"],"sklearn":["scikit-learn"],"statsmodels":["statsmodels"],"hypothesis":["hypothesis"],"torch":["torch"],"jax":["jax"],"jaxlib":["jaxlib"],"tensorflow":["tensorflow","tensorflow-cpu","tensorflow-macos","tf-nightly"],"z3":["z3-solver"],"cvc5":["cvc5"],"mpmath":["mpmath"]}
libraries = {}
for module_name, distributions in modules.items():
    try: available = importlib.util.find_spec(module_name) is not None
    except (ImportError, ValueError): available = False
    version = None
    if available:
        for distribution in distributions:
            try:
                version = importlib.metadata.version(distribution)
                break
            except importlib.metadata.PackageNotFoundError:
                continue
    libraries[module_name] = {"available": available, "version": version}
print(json.dumps({"python_version": sys.version.split()[0], "executable": sys.executable, "libraries": libraries}, ensure_ascii=False))
'''

FRAMEWORK_PROBE_CODE = {
    "torch": r'''
import json, pathlib, sys
sys.path[:] = [entry for entry in sys.path if entry not in ("", ".")]
vendor_root = sys.argv[1] if len(sys.argv) > 1 else ""
if vendor_root and pathlib.Path(vendor_root).expanduser().is_dir(): sys.path.insert(0, str(pathlib.Path(vendor_root).expanduser().resolve()))
try:
    import torch
    cuda_available = bool(torch.cuda.is_available())
    info = {"status": "available", "version": getattr(torch, "__version__", None), "cuda_built": getattr(torch.version, "cuda", None), "hip_built": getattr(torch.version, "hip", None), "cuda_available": cuda_available, "device_count": int(torch.cuda.device_count()) if cuda_available else 0}
    try: cudnn = torch.backends.cudnn.version() if cuda_available else None
    except Exception: cudnn = None
    info["cudnn_version"] = None if cudnn is None else str(cudnn)
    mps = getattr(torch.backends, "mps", None)
    info["mps_built"] = bool(mps is not None and mps.is_built())
    info["mps_available"] = bool(mps is not None and mps.is_available())
except Exception as error:
    info = {"status": "probe_failed", "error": f"{type(error).__name__}: {error}"[:500]}
print(json.dumps(info))
''',
    "jax": r'''
import json, pathlib, sys
sys.path[:] = [entry for entry in sys.path if entry not in ("", ".")]
vendor_root = sys.argv[1] if len(sys.argv) > 1 else ""
if vendor_root and pathlib.Path(vendor_root).expanduser().is_dir(): sys.path.insert(0, str(pathlib.Path(vendor_root).expanduser().resolve()))
try:
    import jax
    devices = jax.devices()
    info = {"status": "available", "version": getattr(jax, "__version__", None), "default_backend": jax.default_backend(), "platforms": sorted({device.platform for device in devices}), "device_count": len(devices)}
except Exception as error:
    info = {"status": "probe_failed", "error": f"{type(error).__name__}: {error}"[:500]}
print(json.dumps(info))
''',
    "tensorflow": r'''
import json, pathlib, sys
sys.path[:] = [entry for entry in sys.path if entry not in ("", ".")]
vendor_root = sys.argv[1] if len(sys.argv) > 1 else ""
if vendor_root and pathlib.Path(vendor_root).expanduser().is_dir(): sys.path.insert(0, str(pathlib.Path(vendor_root).expanduser().resolve()))
try:
    import tensorflow as tf
    devices = tf.config.list_physical_devices()
    info = {"status": "available", "version": getattr(tf, "__version__", None), "device_types": sorted({device.device_type for device in devices}), "device_count": len(devices)}
except Exception as error:
    info = {"status": "probe_failed", "error": f"{type(error).__name__}: {error}"[:500]}
print(json.dumps(info))
''',
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalized_architecture() -> str:
    value = platform.machine().lower()
    return {
        "amd64": "x86_64",
        "x64": "x86_64",
        "aarch64": "arm64",
        "arm64": "arm64",
        "i386": "x86",
        "i686": "x86",
        "x86": "x86",
    }.get(value, value or "unknown")


def neutral_directory() -> str:
    # Version probes run outside any project so toolchain files there cannot
    # redirect a tool manager to a different, possibly uninstalled, toolchain.
    return tempfile.gettempdir()


def resolve_command(name: str) -> str | None:
    candidate = Path(name).expanduser()
    if candidate.is_file():
        return str(candidate.resolve())
    return shutil.which(name)


def run_bounded(arguments: list[str], timeout: int, environment: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        arguments,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
        cwd=neutral_directory(),
        stdin=subprocess.DEVNULL,
        env=environment,
    )


def version_probe(executable: str, arguments: list[str] | tuple[str, ...], pattern: str = DEFAULT_VERSION_PATTERN) -> dict:
    try:
        process = run_bounded([executable, *arguments], VERSION_TIMEOUT_SECONDS)
        output = "\n".join(part.strip() for part in (process.stdout, process.stderr) if part.strip())
        match = re.search(pattern, output)
        version = (match.group(1) if match.groups() else match.group()) if match else None
        return {
            "status": "available" if process.returncode == 0 else "probe_failed",
            "version": version,
            "version_output": output,
            "exit_code": process.returncode,
            "error": "",
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "status": "probe_failed",
            "version": None,
            "version_output": "",
            "exit_code": None,
            "error": f"{type(exc).__name__}: {exc}",
        }


def elan_home() -> Path:
    configured = os.environ.get("ELAN_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".elan"


def elan_toolchain_directory(name: str) -> str:
    # elan stores `owner/repo:release` as `owner--repo---release`.
    return name.replace("/", "--").replace(":", "---")


def resolve_elan_proxy(path: str, home: Path | None = None) -> dict | None:
    """Map an elan proxy to its default toolchain binary without executing elan."""
    home = elan_home() if home is None else home
    try:
        proxy = Path(path).resolve()
        if proxy.parent != (home / "bin").resolve():
            return None
    except OSError:
        return None
    default = None
    try:
        match = re.search(r'(?m)^\s*default_toolchain\s*=\s*"([^"]+)"', (home / "settings.toml").read_text(encoding="utf-8"))
        default = match.group(1) if match else None
    except (OSError, UnicodeError):
        default = None
    if default:
        candidate = home / "toolchains" / elan_toolchain_directory(default) / "bin" / proxy.name
        if candidate.is_file():
            return {"path": str(candidate.resolve()), "manager": "elan", "toolchain": default}
    return {"path": None, "manager": "elan", "toolchain": default, "proxy_path": str(proxy)}


def probe_tool(key: str, commands: tuple[str, ...], arguments: tuple[str, ...], pattern: str, explicit: str = "") -> dict:
    if explicit:
        requested, source = explicit, "explicit"
        path = resolve_command(explicit)
    else:
        requested, source, path = commands[0], "path", None
        for command in commands:
            path = resolve_command(command)
            if path:
                requested = command
                break
    record: dict = {"requested_command": requested, "discovery_source": source, "path": path}
    if path and key == "lean" and source == "path":
        mapped = resolve_elan_proxy(path)
        if mapped is not None:
            record.update(manager=mapped["manager"], toolchain=mapped["toolchain"])
            if mapped["path"] is None:
                record.update(path=mapped["proxy_path"], status="probe_failed", version=None, version_output="", exit_code=None, error="elan_default_toolchain_not_resolved")
                return record
            record.update(path=mapped["path"], discovery_source="elan_default_toolchain")
            path = mapped["path"]
    if not path:
        return {**record, "status": "unavailable", "version": None, "version_output": "", "exit_code": None, "error": ""}
    return {**record, **version_probe(path, arguments, pattern)}


def probe_tool_group(group: str, overrides: dict[str, str]) -> dict:
    return {key: probe_tool(key, commands, arguments, pattern, overrides.get(key, "")) for key, commands, arguments, pattern in TOOL_SPECS[group]}


def probe_nvidia_smi(system: str, explicit: str = "") -> dict:
    if explicit:
        requested, source, path = explicit, "explicit", resolve_command(explicit)
    else:
        requested, source, path = "nvidia-smi", "path", resolve_command("nvidia-smi")
        if not path and system == "Windows":
            root = os.environ.get("ProgramFiles")
            candidate = Path(root, "NVIDIA Corporation", "NVSMI", "nvidia-smi.exe") if root else None
            if candidate and candidate.is_file():
                path = str(candidate.resolve())
    record: dict = {"requested_command": requested, "discovery_source": source, "path": path, "gpus": []}
    if not path:
        return {**record, "status": "unavailable", "version": None, "version_output": "", "exit_code": None, "error": ""}
    try:
        process = run_bounded([path, "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader,nounits"], VERSION_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as exc:
        return {**record, "status": "probe_failed", "version": None, "version_output": "", "exit_code": None, "error": f"{type(exc).__name__}: {exc}"}
    gpus = []
    for line in process.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 3 or not parts[0]:
            continue
        try:
            memory = int(float(parts[2]))
        except ValueError:
            memory = None
        gpus.append({"name": parts[0][:256], "driver_version": parts[1][:64], "memory_total_mib": memory})
    driver = gpus[0]["driver_version"] if gpus else None
    return {
        **record,
        "gpus": gpus,
        "status": "available" if process.returncode == 0 else "probe_failed",
        "version": driver,
        "version_output": driver or "",
        "exit_code": process.returncode,
        "error": "" if process.returncode == 0 else (process.stderr or process.stdout).strip()[:500],
    }


def framework_environment(vendor_root: str) -> dict:
    environment = os.environ.copy()
    # Count CUDA devices through NVML so the query does not create a CUDA context.
    environment.setdefault("PYTORCH_NVML_BASED_CUDA_CHECK", "1")
    environment.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
    if vendor_root:
        resolved = str(Path(vendor_root).expanduser().resolve())
        prior = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = resolved + (os.pathsep + prior if prior else "")
    return environment


def probe_framework(name: str, python_path: str | None, libraries: dict, requested: bool, vendor_root: str) -> dict:
    if not requested:
        return {"status": "not_requested"}
    library = libraries.get(name) if isinstance(libraries, dict) else None
    if not python_path or not isinstance(library, dict) or library.get("available") is not True:
        return {"status": "unavailable"}
    try:
        process = run_bounded([python_path, "-c", FRAMEWORK_PROBE_CODE[name], vendor_root], FRAMEWORK_TIMEOUT_SECONDS, framework_environment(vendor_root))
        lines = [line for line in process.stdout.splitlines() if line.strip()]
        data = json.loads(lines[-1]) if lines else {}
        if process.returncode != 0 or not isinstance(data, dict) or "status" not in data:
            return {"status": "probe_failed", "error": (process.stderr or process.stdout).strip()[:500] or "framework_probe_failed"}
        return data
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        return {"status": "probe_failed", "error": f"{type(exc).__name__}: {exc}"}


def probe_wsl(distro: str, command: str, system: str) -> dict:
    record = {
        "status": "not_requested",
        "distro": distro,
        "requested_command": command,
        "version_output": "",
        "exit_code": None,
        "error": "",
    }
    if not distro:
        return record
    if system != "Windows":
        record["status"] = "unsupported_platform"
        return record
    wsl_path = resolve_command("wsl.exe")
    if not wsl_path:
        record["status"] = "wsl_unavailable"
        return record
    record.update(version_probe(wsl_path, ["-d", distro, "--", command, "--version"]))
    return record


def probe_python(command: str, vendor_root: str = "") -> dict:
    path = resolve_command(command)
    base = {"requested_command": command, "path": path}
    if not path:
        return {"status": "unavailable", **base, "version": "", "libraries": {}, "exit_code": None, "error": ""}
    try:
        process = run_bounded([path, "-c", PYTHON_PROBE_CODE, vendor_root], PYTHON_TIMEOUT_SECONDS)
        if process.returncode != 0:
            return {"status": "probe_failed", **base, "version": "", "libraries": {}, "exit_code": process.returncode, "error": (process.stderr or process.stdout).strip()}
        data = json.loads(process.stdout)
        return {"status": "available", "requested_command": command, "path": data["executable"], "version": data["python_version"], "libraries": data["libraries"], "exit_code": 0, "error": ""}
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, KeyError, TypeError) as exc:
        return {"status": "probe_failed", **base, "version": "", "libraries": {}, "exit_code": None, "error": f"{type(exc).__name__}: {exc}"}


def hardware_facts(system: str) -> dict:
    facts: dict = {"logical_cpus": os.cpu_count(), "memory_bytes": None, "cpu_model": None}
    try:
        if system == "Windows":
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatus()
            status.dwLength = ctypes.sizeof(MemoryStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                facts["memory_bytes"] = int(status.ullTotalPhys)
        else:
            pages, size = os.sysconf("SC_PHYS_PAGES"), os.sysconf("SC_PAGE_SIZE")
            if pages > 0 and size > 0:
                facts["memory_bytes"] = int(pages * size)
    except (AttributeError, OSError, ValueError):
        pass
    model = None
    try:
        if system == "Linux":
            for line in Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace").splitlines():
                if line.lower().startswith(("model name", "hardware", "cpu model")) and ":" in line:
                    model = line.split(":", 1)[1].strip()
                    break
        elif system == "Darwin":
            process = run_bounded(["sysctl", "-n", "machdep.cpu.brand_string"], 5)
            model = process.stdout.strip() if process.returncode == 0 else None
        elif system == "Windows":
            model = os.environ.get("PROCESSOR_IDENTIFIER") or platform.processor() or None
    except (OSError, subprocess.SubprocessError):
        model = None
    facts["cpu_model"] = model[:256] if model else None
    return facts


def parse_overrides(values: list[str]) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for value in values:
        name, separator, command = value.partition("=")
        name = name.strip().replace("-", "_")
        if not separator or name not in OVERRIDABLE_TOOLS or not command.strip() or name in overrides:
            raise InputError()
        overrides[name] = command.strip()
    return overrides


def build_parser() -> argparse.ArgumentParser:
    parser = SafeParser(description=__doc__)
    add_output_arguments(parser)
    parser.add_argument("--python-command", default=sys.executable)
    parser.add_argument("--python-vendor-root", default=os.environ.get("CS_AI_COMPUTATION_VENDOR", ""))
    parser.add_argument("--tool-command", action="append", default=[], metavar="NAME=PATH")
    parser.add_argument("--framework-devices", nargs="*", choices=(*FRAMEWORKS, "none"), default=["torch"])
    parser.add_argument("--only", nargs="+", choices=("all", *BACKENDS), default=["all"])
    parser.add_argument("--wsl-distro", default="")
    parser.add_argument("--wsl-command", default="python3")
    return parser


@public_main
def main() -> int:
    args = build_parser().parse_args()
    configure_output(args)
    overrides = parse_overrides(args.tool_command)
    selected = list(BACKENDS) if "all" in args.only else [name for name in BACKENDS if name in args.only]
    system = platform.system()
    result: dict = {
        "schema_version": "1.0",
        "probed_at_utc": utc_now(),
        "host": {"system": system, "architecture": normalized_architecture(), "python_implementation": platform.python_implementation()},
        "hardware": hardware_facts(system),
    }
    python_record = None
    if "python" in selected or "accelerators" in selected:
        python_record = probe_python(args.python_command, args.python_vendor_root)
    if "python" in selected:
        result["python"] = {**python_record, "wsl": probe_wsl(args.wsl_distro, args.wsl_command, system)}
    if "accelerators" in selected:
        python_path = python_record.get("path") if python_record and python_record.get("status") == "available" else None
        libraries = python_record.get("libraries", {}) if python_record else {}
        result["accelerators"] = {
            "nvidia_smi": probe_nvidia_smi(system, overrides.get("nvidia_smi", "")),
            "frameworks": {
                name: probe_framework(name, python_path, libraries, name in args.framework_devices, args.python_vendor_root)
                for name in FRAMEWORKS
            },
            "apple_silicon": system == "Darwin" and normalized_architecture() == "arm64",
        }
    for group in TOOL_SPECS:
        if group in selected:
            result[group] = probe_tool_group(group, overrides)
    emit_public({"ok": True, **local_inventory(result, {}, include_mcp=True)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
