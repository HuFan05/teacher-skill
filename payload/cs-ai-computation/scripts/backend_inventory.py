#!/usr/bin/env python3
"""Read, refresh, or invalidate the cross-platform backend inventory."""

from __future__ import annotations

import argparse
from computation_output import SafeParser, add_output_arguments, configure_output, public_main, emit_result as emit_public
from computation_projection import BACKENDS, TOOL_GROUPS, local_inventory, fields as project_fields
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


REASON_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
MCP_PROTOCOL_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_MCP_OBSERVATIONS = 16
PROBE_TIMEOUT_SECONDS = 600
FRAMEWORK_BOUNDARY = "a_completed_run_or_metric_is_numerical_evidence_or_bounded_empirical_not_a_correctness_proof"
PYTHON_LIBRARY_GUIDANCE = {
    "torch": {
        "purpose": "tensor_computation_autodiff_training_and_evaluation_on_cpu_cuda_rocm_or_mps",
        "evidence_boundary": FRAMEWORK_BOUNDARY,
        "live_check_requirement": "use_run_python_capability_torch_cpu_torch_cuda_or_torch_mps_before_material_runs",
    },
    "jax": {
        "purpose": "tensor_computation_autodiff_and_jit_compiled_training_on_cpu_gpu_or_tpu",
        "evidence_boundary": FRAMEWORK_BOUNDARY,
        "live_check_requirement": "use_run_python_capability_jax_default_before_material_runs",
    },
    "tensorflow": {
        "purpose": "tensor_computation_autodiff_training_and_evaluation",
        "evidence_boundary": FRAMEWORK_BOUNDARY,
        "live_check_requirement": "use_run_python_capability_tensorflow_default_before_material_runs",
    },
    "scipy": {
        "purpose": "numerical_optimization_linear_algebra_and_statistics",
        "evidence_boundary": "numerical_evidence_by_default_not_a_certificate",
        "live_check_requirement": "use_run_python_capability_scipy_stats_or_smoke_test_the_selected_operation_before_material_use",
    },
    "z3": {
        "purpose": "smt_solving_models_and_unsat_answers_through_the_python_binding",
        "evidence_boundary": "a_sat_model_is_checkable_by_substitution_an_unsat_answer_is_trusted_solver_output_unless_a_proof_is_checked",
        "live_check_requirement": "use_run_python_capability_z3_smt_before_material_queries",
    },
    "hypothesis": {
        "purpose": "property_based_testing_with_counterexample_shrinking",
        "evidence_boundary": "passing_properties_verify_only_the_generated_inputs_not_correctness",
        "live_check_requirement": "use_run_python_capability_hypothesis_pbt_before_material_test_runs",
    },
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


def default_state_file() -> Path:
    override = os.environ.get("CS_AI_BACKEND_INVENTORY")
    if override:
        return Path(override).expanduser()
    return Path(tempfile.gettempdir(), "Teacher", "cs-ai-computation", "backend-inventory.json")


def read_inventory(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("inventory_schema_version") != "1.0" or data.get("local", {}).get("schema_version") != "1.0":
            return None
        if any(not isinstance(data["local"].get(name), dict) for name in BACKENDS):
            return None
        return data
    except (OSError, json.JSONDecodeError, TypeError, AttributeError):
        return None


def selected_backends(names: list[str]) -> list[str]:
    if "all" in names:
        return list(BACKENDS)
    return [name for name in BACKENDS if name in names]


def invoke_probe(args: argparse.Namespace, names: list[str]) -> dict:
    if args.probe_json_file:
        data = json.loads(Path(args.probe_json_file).read_text(encoding="utf-8"))
    else:
        probe_script = Path(args.probe_script)
        if not probe_script.is_file():
            raise RuntimeError("Backend probe script is unavailable.")
        command = [
            sys.executable,
            str(probe_script),
            "--max-response-bytes",
            "1048576",
            "--response-reason",
            "inventory_snapshot_capture",
            "--python-command",
            args.python_command,
            "--wsl-command",
            args.wsl_command,
            "--only",
            *selected_backends(names),
            "--framework-devices",
            *args.framework_devices,
        ]
        if args.python_vendor_root:
            command.extend(["--python-vendor-root", args.python_vendor_root])
        if args.wsl_distro:
            command.extend(["--wsl-distro", args.wsl_distro])
        for value in args.tool_command:
            command.extend(["--tool-command", value])
        process = subprocess.run(command, text=True, capture_output=True, check=False, timeout=PROBE_TIMEOUT_SECONDS)
        if process.returncode != 0:
            raise RuntimeError(f"Backend probe failed with exit code {process.returncode}.")
        data = json.loads(process.stdout)
    if not isinstance(data, dict) or data.get("schema_version") != "1.0":
        raise SystemExit("Unsupported backend probe schema.")
    for transport_key in ("ok", "response_complete", "mcp", "stderr_diagnostics_suppressed"):
        data.pop(transport_key, None)
    return data


def new_inventory(local: dict) -> dict:
    now = utc_now()
    return {
        "inventory_schema_version": "1.0",
        "created_at_utc": now,
        "updated_at_utc": now,
        "local": local,
        "mcp": {
            "authority": "current_session_tool_discovery_and_call",
            "persisted_status": "historical_only",
            "required_action": "Build a current-session overlay and live-check only the selected MCP backend.",
        },
        "invalidations": [],
    }


def mcp_observation(args: argparse.Namespace) -> dict:
    values = {
        "server_name": args.mcp_server_name,
        "protocol_version": args.mcp_protocol_version,
        "server_version": args.mcp_server_version,
        "runtime_version": args.mcp_runtime_version,
    }
    missing = [name for name, value in values.items() if not value.strip()]
    if missing:
        raise SystemExit("RecordMcp requires: " + ", ".join(missing))
    if not MCP_PROTOCOL_PATTERN.fullmatch(values["protocol_version"]):
        raise SystemExit("MCP protocol version must use the negotiated YYYY-MM-DD form.")
    if len(values["server_name"]) > 128:
        raise SystemExit("MCP server name is too long.")
    observed_at = args.mcp_observed_at_utc or utc_now()
    try:
        datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SystemExit("MCP observation time must be an ISO-8601 timestamp.") from exc
    return {
        **values,
        "observed_at_utc": observed_at,
        "evidence": "initialize_handshake_and_execution_call",
    }


def store_mcp_observation(inventory: dict, observation: dict) -> None:
    observations = inventory["mcp"].get("observations")
    observations = observations if isinstance(observations, dict) else {}
    observations[observation["server_name"]] = observation
    if len(observations) > MAX_MCP_OBSERVATIONS:
        ordered = sorted(observations.values(), key=lambda item: str(item.get("observed_at_utc", "")))
        observations = {item["server_name"]: item for item in ordered[-MAX_MCP_OBSERVATIONS:]}
    inventory["mcp"]["observations"] = observations


def write_inventory_atomic(inventory: dict, path: Path) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".backend-inventory-{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def recorded_paths(local: dict) -> list[tuple[str, object]]:
    paths: list[tuple[str, object]] = [
        ("python", local.get("python", {}).get("path")),
        ("accelerators", local.get("accelerators", {}).get("nvidia_smi", {}).get("path")),
    ]
    for group, names in TOOL_GROUPS.items():
        records = local.get(group, {})
        for name in names:
            record = records.get(name) if isinstance(records, dict) else None
            if isinstance(record, dict):
                paths.append((group, record.get("path")))
    return paths


def missing_backend_paths(inventory: dict) -> list[str]:
    missing: set[str] = set()
    for name, value in recorded_paths(inventory["local"]):
        if isinstance(value, str) and value and not Path(value).is_file():
            missing.add(name)
    return [name for name in BACKENDS if name in missing]


def inventory_expired(inventory: dict, max_age_hours: int) -> bool:
    if max_age_hours <= 0:
        return False
    try:
        updated = datetime.fromisoformat(inventory["updated_at_utc"].replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - updated.astimezone(timezone.utc)).total_seconds() >= max_age_hours * 3600
    except (KeyError, TypeError, ValueError):
        return True


def host_changed(inventory: dict) -> bool:
    host = inventory.get("local", {}).get("host")
    if not isinstance(host, dict):
        return True
    return host.get("system") != platform.system() or host.get("architecture") != normalized_architecture()


def merge_backends(inventory: dict, fresh_local: dict, names: list[str]) -> dict:
    for name in selected_backends(names):
        if name in fresh_local:
            inventory["local"][name] = fresh_local[name]
    inventory["local"]["probed_at_utc"] = fresh_local["probed_at_utc"]
    inventory["local"]["host"] = fresh_local.get("host", {})
    inventory["local"]["hardware"] = fresh_local.get("hardware", {})
    inventory["updated_at_utc"] = utc_now()
    return inventory


def projected_observations(inventory: dict) -> list[dict]:
    observations = inventory.get("mcp", {}).get("observations")
    observations = observations if isinstance(observations, dict) else {}
    names = ("server_name", "protocol_version", "server_version", "runtime_version", "observed_at_utc")
    return [project_fields(item, names) for item in list(observations.values())[:MAX_MCP_OBSERVATIONS]]


def emit_result(
    inventory: dict,
    state_file: Path,
    started: float,
    cache_status: str,
    refreshed: list[str] | None = None,
    invalid_paths: list[str] | None = None,
    backend_started: bool = False,
    write_error: str = "",
) -> None:
    projected_local = local_inventory(inventory["local"], PYTHON_LIBRARY_GUIDANCE)
    output = {
        "inventory_schema_version": inventory["inventory_schema_version"],
        "snapshot_updated_at_utc": inventory["updated_at_utc"],
        "cache": {
            "status": cache_status,
            "state_file": str(state_file),
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "backend_started": backend_started,
            "refreshed_backends": refreshed or [],
            "invalid_path_backends": invalid_paths or [],
            "write_error": write_error,
        },
        "local": projected_local,
        "mcp": {
            "status": "session_probe_required",
            "authority": "current_session_tool_discovery_and_call",
            "note": "The persisted snapshot is not evidence that an MCP tool is callable in this session.",
            "recorded_mcp_observations": projected_observations(inventory),
        },
    }
    emit_public({"ok": True, **output})


def build_parser() -> argparse.ArgumentParser:
    parser = SafeParser(description=__doc__)
    add_output_arguments(parser)
    parser.add_argument("--mode", choices=("ReadOrCreate", "Refresh", "Invalidate", "RecordMcp"), default="ReadOrCreate")
    parser.add_argument("--state-file", default="")
    parser.add_argument("--backend", choices=("all", *BACKENDS), nargs="+", default=["all"])
    parser.add_argument("--reason-code", default="")
    parser.add_argument("--max-age-hours", type=int, default=168)
    parser.add_argument("--probe-script", default=str(Path(__file__).with_name("probe_backends.py")))
    parser.add_argument("--probe-json-file", default="")
    parser.add_argument("--python-command", default=sys.executable)
    parser.add_argument("--python-vendor-root", default=os.environ.get("CS_AI_COMPUTATION_VENDOR", ""))
    parser.add_argument("--tool-command", action="append", default=[], metavar="NAME=PATH")
    parser.add_argument("--framework-devices", nargs="*", choices=("torch", "jax", "tensorflow", "none"), default=["torch"])
    parser.add_argument("--wsl-distro", default="")
    parser.add_argument("--wsl-command", default="python3")
    parser.add_argument("--mcp-server-name", default="")
    parser.add_argument("--mcp-protocol-version", default="")
    parser.add_argument("--mcp-server-version", default="")
    parser.add_argument("--mcp-runtime-version", default="")
    parser.add_argument("--mcp-observed-at-utc", default="")
    parser.add_argument("--no-write", action="store_true", help="Read or probe without creating or updating the state file.")
    return parser


@public_main
def main() -> int:
    started = time.perf_counter()
    args = build_parser().parse_args()
    configure_output(args)
    if args.mode == "Invalidate" and not REASON_PATTERN.fullmatch(args.reason_code):
        raise SystemExit("Invalidate mode requires a bounded lowercase reason code.")
    state_file = Path(args.state_file).expanduser() if args.state_file else default_state_file()
    state_file = state_file.resolve()
    inventory = read_inventory(state_file)
    missing: list[str] = []

    if args.mode == "RecordMcp":
        if args.no_write:
            raise SystemExit("RecordMcp persists an observation and cannot run with --no-write.")
        observation = mcp_observation(args)
        backend_started = False
        if inventory is None:
            inventory = new_inventory(invoke_probe(args, ["all"]))
            backend_started = True
        store_mcp_observation(inventory, observation)
        inventory["updated_at_utc"] = utc_now()
        write_inventory_atomic(inventory, state_file)
        emit_result(inventory, state_file, started, "mcp_recorded", backend_started=backend_started)
        return 0

    if args.mode == "ReadOrCreate" and inventory:
        missing = missing_backend_paths(inventory)
        if not missing and not inventory_expired(inventory, args.max_age_hours) and not host_changed(inventory):
            emit_result(inventory, state_file, started, "hit")
            return 0

    refreshed = list(BACKENDS)
    if inventory is not None and not host_changed(inventory):
        if args.mode in {"Refresh", "Invalidate"}:
            refreshed = selected_backends(args.backend)
        elif missing and not inventory_expired(inventory, args.max_age_hours):
            refreshed = missing
    fresh_local = invoke_probe(args, refreshed)

    cache_status = "created"
    if inventory is None:
        if any(name not in fresh_local for name in BACKENDS):
            raise SystemExit("A new inventory requires a probe of every backend.")
        inventory = new_inventory(fresh_local)
        refreshed = list(BACKENDS)
    else:
        cache_status = "refreshed"
        if args.mode == "Invalidate":
            inventory["invalidations"] = (inventory.get("invalidations", []) + [
                {"backend": name, "reason": args.reason_code, "recorded_at_utc": utc_now()}
                for name in refreshed
            ])[-20:]
        inventory = merge_backends(inventory, fresh_local, refreshed)

    write_error = ""
    if args.no_write:
        cache_status = "not_persisted"
    else:
        try:
            write_inventory_atomic(inventory, state_file)
        except OSError:
            write_error = "cache_write_failed"
            cache_status = "write_failed"
    emit_result(inventory, state_file, started, cache_status, refreshed, missing, True, write_error)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
