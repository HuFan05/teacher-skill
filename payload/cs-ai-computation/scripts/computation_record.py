#!/usr/bin/env python3
"""Initialize and validate reproducible CS/AI computation and experiment records."""

from __future__ import annotations

import argparse
from computation_output import SafeParser, add_output_arguments, configure_output, public_main, emit_result as emit_public
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "1.0"
AVAILABILITY = {"available", "unavailable", "unknown", "not-applicable"}
PRECISION_MODES = {"exact", "fp64", "fp32", "tf32", "bf16", "fp16", "fp8", "int8", "mixed", "arbitrary", "interval"}
# Strongest first. Formal and certificate grades require a named checker.
EVIDENCE_GRADES = (
    "formal",
    "certificate",
    "exact_reproduction",
    "bounded_empirical",
    "numerical_evidence",
)
CHECKED_GRADES = {"formal", "certificate"}
BACKEND_NAME = re.compile(r"^[a-z][a-z0-9_.+-]{0,63}$")
HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class RecordError(ValueError):
    """A computation record is invalid."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def relative_inside(path: Path, base_dir: Path, label: str) -> str:
    resolved_path = path.resolve()
    resolved_base = base_dir.resolve()
    try:
        relative = resolved_path.relative_to(resolved_base)
    except ValueError as exc:
        raise RecordError(f"{label} must be inside the record base directory") from exc
    return relative.as_posix()


def resolve_safe_relative(base_dir: Path, raw_path: Any, label: str) -> Path:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise RecordError(f"{label} must be a nonempty relative path")
    candidate = Path(raw_path)
    if candidate.is_absolute():
        raise RecordError(f"{label} must not be absolute")
    resolved_base = base_dir.resolve()
    resolved = (resolved_base / candidate).resolve()
    try:
        resolved.relative_to(resolved_base)
    except ValueError as exc:
        raise RecordError(f"{label} escapes the base directory") from exc
    return resolved


def require_mapping(parent: dict[str, Any], key: str, label: str) -> dict[str, Any]:
    value = parent.get(key)
    if not isinstance(value, dict):
        raise RecordError(f"{label}.{key} must be an object")
    return value


def require_nonempty_string(parent: dict[str, Any], key: str, label: str) -> str:
    value = parent.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RecordError(f"{label}.{key} must be a nonempty string")
    return value.strip()


def require_string(parent: dict[str, Any], key: str, label: str) -> str:
    value = parent.get(key)
    if not isinstance(value, str):
        raise RecordError(f"{label}.{key} must be a string")
    return value


def require_string_list(parent: dict[str, Any], key: str, label: str) -> list[str]:
    value = parent.get(key)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise RecordError(f"{label}.{key} must be a list of strings")
    return value


def validate_hash(raw_hash: Any, label: str) -> str:
    if not isinstance(raw_hash, str) or not HEX_SHA256.fullmatch(raw_hash):
        raise RecordError(f"{label} must be a lowercase SHA-256 hex digest")
    return raw_hash


def check_local_file(base: Path, raw_path: str, expected_hash: str, label: str) -> None:
    path = resolve_safe_relative(base, raw_path, f"{label}.path")
    if not path.is_file():
        raise RecordError(f"{label}.path does not exist: {path}")
    if sha256_file(path) != expected_hash:
        raise RecordError(f"{label}.sha256 does not match {raw_path}")


def init_record(task_file: Path, record_path: Path, force: bool) -> None:
    task_file = task_file.resolve()
    record_path = record_path.resolve()
    if not task_file.is_file():
        raise RecordError(f"task file does not exist: {task_file}")
    record_path.parent.mkdir(parents=True, exist_ok=True)
    if record_path.exists() and not force:
        raise RecordError(f"record already exists: {record_path}; pass --force to replace it")
    task_relative = relative_inside(task_file, record_path.parent, "task file")

    record = {
        "schema_version": SCHEMA_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "task": {
            "file": task_relative,
            "sha256": sha256_file(task_file),
            "object": "",
            "deliverables": [],
        },
        "problem_context": {
            "assumptions": [],
            "domain": "",
            "declared_range": "",
            "precision": {
                "mode": "exact",
                "working_digits": None,
                "target_tolerance": "",
            },
        },
        "implementation_discovery": {
            "python": {
                "candidate_implementations": [],
                "existence_evidence": "",
                "local_availability": "unknown",
                "availability_evidence": "",
            },
        },
        "decision": {
            "selected_backend": "",
            "backend_version": "",
            "selection_reason": "",
            "fallback_reason": "",
        },
        "environment": {
            "os": "",
            "python": "",
            "packages": {},
            "accelerator_runtime": "",
            "driver": "",
        },
        "hardware": {
            "cpu": "",
            "accelerators": [],
            "memory": "",
        },
        "seeds": {
            "values": [],
            "policy": "",
        },
        "datasets": [],
        "configuration": {
            "summary": "",
            "file": "",
            "sha256": "",
        },
        "execution": {
            "status": "planned",
            "interface": "",
            "command_or_input": "",
            "code_artifact": "",
        },
        "artifacts": [],
        "result": {
            "status": "pending",
            "summary": "",
            "result_artifact": "",
            "metrics": {},
        },
        "verification": {
            "methods": [],
            "evidence_grade": "",
            "checker": "",
            "residual_or_error": "",
            "limitations": [],
        },
    }
    write_json(record_path, record)
    emit_public({"ok": True, "record": str(record_path)})


def validate_record(record_path: Path, base_dir: Path | None) -> dict[str, Any]:
    record_path = record_path.resolve()
    if not record_path.is_file():
        raise RecordError(f"record does not exist: {record_path}")
    base = (base_dir or record_path.parent).resolve()
    if not base.is_dir():
        raise RecordError(f"base directory does not exist: {base}")

    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RecordError(f"cannot read record JSON: {exc}") from exc
    if not isinstance(record, dict):
        raise RecordError("record root must be an object")
    if record.get("schema_version") != SCHEMA_VERSION:
        raise RecordError(f"schema_version must be {SCHEMA_VERSION}")

    task = require_mapping(record, "task", "record")
    require_nonempty_string(task, "object", "task")
    deliverables = require_string_list(task, "deliverables", "task")
    if not deliverables:
        raise RecordError("task.deliverables must contain at least one item")
    task_path = resolve_safe_relative(base, task.get("file"), "task.file")
    if not task_path.is_file():
        raise RecordError(f"task.file does not exist: {task_path}")
    task_hash = validate_hash(task.get("sha256"), "task.sha256")
    if sha256_file(task_path) != task_hash:
        raise RecordError("task.sha256 does not match task.file")

    context = require_mapping(record, "problem_context", "record")
    require_string_list(context, "assumptions", "problem_context")
    require_nonempty_string(context, "domain", "problem_context")
    declared_range = require_string(context, "declared_range", "problem_context")
    precision = require_mapping(context, "precision", "problem_context")
    mode = require_nonempty_string(precision, "mode", "problem_context.precision")
    if mode not in PRECISION_MODES:
        raise RecordError(f"problem_context.precision.mode must be one of {sorted(PRECISION_MODES)}")
    working_digits = precision.get("working_digits")
    if working_digits is not None and (type(working_digits) is not int or working_digits <= 0):
        raise RecordError("problem_context.precision.working_digits must be null or a positive integer")
    target_tolerance = require_string(precision, "target_tolerance", "problem_context.precision")
    if mode != "exact" and not target_tolerance.strip():
        raise RecordError("numerical precision modes require problem_context.precision.target_tolerance")

    discovery = require_mapping(record, "implementation_discovery", "record")
    if not discovery:
        raise RecordError("implementation_discovery must name at least one considered backend")
    for backend, entry in discovery.items():
        if not isinstance(backend, str) or not BACKEND_NAME.fullmatch(backend):
            raise RecordError("implementation_discovery keys must be lowercase backend names")
        if not isinstance(entry, dict):
            raise RecordError(f"implementation_discovery.{backend} must be an object")
        require_string_list(entry, "candidate_implementations", f"implementation_discovery.{backend}")
        require_nonempty_string(entry, "existence_evidence", f"implementation_discovery.{backend}")
        availability = require_nonempty_string(
            entry, "local_availability", f"implementation_discovery.{backend}"
        )
        if availability not in AVAILABILITY:
            raise RecordError(
                f"implementation_discovery.{backend}.local_availability must be one of {sorted(AVAILABILITY)}"
            )
        require_nonempty_string(
            entry, "availability_evidence", f"implementation_discovery.{backend}"
        )

    decision = require_mapping(record, "decision", "record")
    selected_backend = require_nonempty_string(decision, "selected_backend", "decision")
    if selected_backend not in discovery:
        raise RecordError("decision.selected_backend must name an implementation_discovery entry")
    if discovery[selected_backend].get("local_availability") != "available":
        raise RecordError("the selected backend must have local_availability=available")
    require_nonempty_string(decision, "backend_version", "decision")
    require_nonempty_string(decision, "selection_reason", "decision")
    require_nonempty_string(decision, "fallback_reason", "decision")

    environment = require_mapping(record, "environment", "record")
    require_nonempty_string(environment, "os", "environment")
    require_nonempty_string(environment, "python", "environment")
    packages = require_mapping(environment, "packages", "environment")
    if any(not isinstance(name, str) or not isinstance(version, str) or not version.strip() for name, version in packages.items()):
        raise RecordError("environment.packages must map package names to nonempty version strings")
    require_nonempty_string(environment, "accelerator_runtime", "environment")
    require_string(environment, "driver", "environment")

    hardware = require_mapping(record, "hardware", "record")
    require_nonempty_string(hardware, "cpu", "hardware")
    require_string_list(hardware, "accelerators", "hardware")
    require_string(hardware, "memory", "hardware")

    seeds = require_mapping(record, "seeds", "record")
    values = seeds.get("values")
    if not isinstance(values, list) or any(type(value) is not int for value in values):
        raise RecordError("seeds.values must be a list of integers")
    require_nonempty_string(seeds, "policy", "seeds")

    datasets = record.get("datasets")
    if not isinstance(datasets, list):
        raise RecordError("datasets must be a list")
    unverified_dataset_hashes = 0
    for index, dataset in enumerate(datasets):
        label = f"datasets[{index}]"
        if not isinstance(dataset, dict):
            raise RecordError(f"{label} must be an object")
        require_nonempty_string(dataset, "id", label)
        require_nonempty_string(dataset, "version", label)
        dataset_hash = validate_hash(dataset.get("sha256"), f"{label}.sha256")
        raw_path = dataset.get("path", "")
        if not isinstance(raw_path, str):
            raise RecordError(f"{label}.path must be a string")
        if raw_path.strip():
            check_local_file(base, raw_path, dataset_hash, label)
        else:
            unverified_dataset_hashes += 1

    configuration = require_mapping(record, "configuration", "record")
    require_nonempty_string(configuration, "summary", "configuration")
    config_file = require_string(configuration, "file", "configuration")
    if config_file.strip():
        check_local_file(base, config_file, validate_hash(configuration.get("sha256"), "configuration.sha256"), "configuration")

    execution = require_mapping(record, "execution", "record")
    if require_nonempty_string(execution, "status", "execution") != "complete":
        raise RecordError("execution.status must be complete")
    require_nonempty_string(execution, "interface", "execution")
    require_nonempty_string(execution, "command_or_input", "execution")
    code_artifact = require_nonempty_string(execution, "code_artifact", "execution")

    artifacts = record.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise RecordError("artifacts must be a nonempty list")
    roles: set[str] = set()
    artifact_paths: set[str] = set()
    verified_artifacts = []
    for index, artifact in enumerate(artifacts):
        label = f"artifacts[{index}]"
        if not isinstance(artifact, dict):
            raise RecordError(f"{label} must be an object")
        role = require_nonempty_string(artifact, "role", label)
        raw_path = require_nonempty_string(artifact, "path", label)
        expected_hash = validate_hash(artifact.get("sha256"), f"{label}.sha256")
        check_local_file(base, raw_path, expected_hash, label)
        roles.add(role)
        artifact_paths.add(Path(raw_path).as_posix())
        verified_artifacts.append(
            {"role": role, "path": Path(raw_path).as_posix(), "sha256": expected_hash}
        )
    if "code" not in roles or "result" not in roles:
        raise RecordError("artifacts must include both code and result roles")
    if Path(code_artifact).as_posix() not in artifact_paths:
        raise RecordError("execution.code_artifact must name a hashed artifact")

    result = require_mapping(record, "result", "record")
    if require_nonempty_string(result, "status", "result") != "complete":
        raise RecordError("result.status must be complete")
    require_nonempty_string(result, "summary", "result")
    result_artifact = require_nonempty_string(result, "result_artifact", "result")
    if Path(result_artifact).as_posix() not in artifact_paths:
        raise RecordError("result.result_artifact must name a hashed artifact")
    metrics = require_mapping(result, "metrics", "result")
    for name, value in metrics.items():
        if not isinstance(name, str) or not name.strip() or isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise RecordError("result.metrics must map metric names to numbers or strings")

    verification = require_mapping(record, "verification", "record")
    methods = require_string_list(verification, "methods", "verification")
    if not methods or any(not method.strip() for method in methods):
        raise RecordError("verification.methods must contain at least one nonempty method")
    evidence_grade = require_nonempty_string(verification, "evidence_grade", "verification")
    if evidence_grade not in EVIDENCE_GRADES:
        raise RecordError(f"verification.evidence_grade must be one of {list(EVIDENCE_GRADES)}")
    checker = require_string(verification, "checker", "verification")
    if evidence_grade in CHECKED_GRADES and not checker.strip():
        raise RecordError("formal and certificate grades require verification.checker")
    if evidence_grade == "bounded_empirical" and not declared_range.strip():
        raise RecordError("bounded_empirical requires problem_context.declared_range")
    residual_or_error = require_string(verification, "residual_or_error", "verification")
    if mode != "exact" and not residual_or_error.strip():
        raise RecordError("numerical precision modes require verification.residual_or_error")
    require_string_list(verification, "limitations", "verification")

    return {
        "ok": True,
        "record": str(record_path),
        "base_dir": str(base),
        "selected_backend": selected_backend,
        "evidence_grade": evidence_grade,
        "verified_artifacts": verified_artifacts,
        "dataset_count": len(datasets),
        "unverified_dataset_hashes": unverified_dataset_hashes,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = SafeParser(
        description="Initialize or validate a computation-record.json file."
    )
    add_output_arguments(parser)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="initialize a record from a task file")
    init_parser.add_argument("--task-file", required=True, type=Path)
    init_parser.add_argument("--record", required=True, type=Path)
    init_parser.add_argument("--force", action="store_true")

    validate_parser = subparsers.add_parser("validate", help="validate a completed record")
    validate_parser.add_argument("--record", required=True, type=Path)
    validate_parser.add_argument("--base-dir", type=Path)
    return parser


@public_main
def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_output(args)
    try:
        if args.command == "init":
            init_record(args.task_file, args.record, args.force)
            return 0
        result = validate_record(args.record, args.base_dir)
        emit_public(result)
        return 0
    except (OSError, RecordError) as exc:
        emit_public({"ok": False, "error": "record_validation_failed" if isinstance(exc, RecordError) else "record_io_failed", "raw_diagnostics_returned": False})
        return 1


if __name__ == "__main__":
    sys.exit(main())
