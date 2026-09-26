from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path, PurePosixPath


CLASSIFICATION_SCHEMA = "obsidian-large-edit-classification/v1"
INVENTORY_SCHEMA = "obsidian-large-edit-candidate-inventory/v1"
RECEIPT_SCHEMA = "obsidian-large-edit-verifier-receipt/v1"
DETERMINISTIC_SCHEMA = "obsidian-large-edit-deterministic-receipt/v1"
STATE_SCHEMA = "obsidian-large-edit-gate-state/v1"
STATE_NAME = ".obsidian-large-edit-gate-state.json"
HEX64 = set("0123456789abcdef")
CHECK_KEYS = {
    "meaning_preservation",
    "language_and_author_voice",
    "terminology_and_notation",
    "format_and_reader_coherence",
}
CHECK_VALUES = {"PASS", "FAIL"}
EXCLUDED_CLASSES = {
    "attachments",
    "credentials",
    "external_resources",
    "linked_note_bodies",
    "raw_logs",
    "unrelated_frontmatter",
    "unrelated_sections",
    "unmanaged_absolute_paths",
}
FINDING_KEYS = {"relative_path", "location", "category", "severity", "message"}
DETERMINISTIC_CHECK_KEYS = {
    "utf8_and_path_containment",
    "markdown_and_frontmatter",
    "formulas",
    "wikilinks",
    "resource_references",
    "task_specific_lint",
    "diff_and_semantic_sentinels",
}


def emit(payload: dict, output: str | None = None) -> int:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if output:
        destination = Path(output).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
        temporary.write_text(text, encoding="utf-8", newline="\n")
        os.replace(temporary, destination)
    print(text, end="")
    return 0 if payload.get("ok") else 1


def failure(code: str, message: str, recovery: str, **details: object) -> dict:
    return {"ok": False, "code": code, "message": message, "recovery": recovery, "details": details}


def read_json(path: str) -> object:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def closed_object(value: object, keys: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"{label} must contain exactly {sorted(keys)}")
    return value


def classify(path: str) -> dict:
    keys = {
        "schema", "explicit_project_scale", "target_count", "substantive_prose_target_count",
        "authorized_scope_non_whitespace_chars", "changed_non_whitespace_chars",
        "changed_ratio_max", "top_level_sections_affected", "whole_scope_coherence_required",
        "mechanical_only", "single_paragraph_only", "few_formula_lines_only",
        "single_local_subsection_only", "narrow_repair_only",
    }
    try:
        data = closed_object(read_json(path), keys, "classification input")
        if data["schema"] != CLASSIFICATION_SCHEMA:
            raise ValueError("unsupported classification schema")
        bool_keys = {
            "explicit_project_scale", "whole_scope_coherence_required", "mechanical_only",
            "single_paragraph_only", "few_formula_lines_only", "single_local_subsection_only",
            "narrow_repair_only",
        }
        if any(type(data[key]) is not bool for key in bool_keys):
            raise ValueError("classification flags must be booleans")
        int_keys = {
            "target_count", "substantive_prose_target_count",
            "authorized_scope_non_whitespace_chars", "changed_non_whitespace_chars",
            "top_level_sections_affected",
        }
        if any(type(data[key]) is not int or data[key] < 0 for key in int_keys):
            raise ValueError("classification counts must be non-negative integers")
        ratio = data["changed_ratio_max"]
        if not isinstance(ratio, (int, float)) or isinstance(ratio, bool) or not 0 <= ratio <= 1:
            raise ValueError("changed_ratio_max must be between 0 and 1")
    except (OSError, json.JSONDecodeError, ValueError) as error:
        return failure("classification_invalid", str(error), "Repair the closed classification input and retry.")

    exclusions = [
        key for key in (
            "mechanical_only", "single_paragraph_only", "few_formula_lines_only",
            "single_local_subsection_only", "narrow_repair_only",
        ) if data[key]
    ]
    thresholds = []
    if data["target_count"] >= 3 and data["substantive_prose_target_count"] >= 2:
        thresholds.append("multi_note_substantive")
    if (
        data["authorized_scope_non_whitespace_chars"] >= 6000
        and data["changed_non_whitespace_chars"] >= 2500
        and ratio >= 0.30
    ):
        thresholds.append("large_single_scope")
    if data["whole_scope_coherence_required"] and data["target_count"] >= 5:
        thresholds.append("five_note_coherence")
    if (
        data["whole_scope_coherence_required"]
        and data["top_level_sections_affected"] >= 3
        and data["changed_non_whitespace_chars"] >= 2500
    ):
        thresholds.append("multi_section_coherence")
    is_large = bool(data["explicit_project_scale"] and thresholds and not exclusions)
    return {
        "ok": True,
        "code": "classification_complete",
        "classification": "large_edit" if is_large else "ordinary_edit",
        "explicit_project_scale": data["explicit_project_scale"],
        "matched_thresholds": thresholds,
        "override_exclusions": exclusions,
        "verifier_required": is_large,
    }


def safe_relative(value: str) -> PurePosixPath:
    normalized = value.replace("\\", "/")
    relative = PurePosixPath(normalized)
    if (
        relative.is_absolute()
        or not relative.parts
        or ":" in relative.parts[0]
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError(f"unsafe relative target: {value}")
    if relative.suffix.lower() != ".md":
        raise ValueError(f"target is not Markdown: {value}")
    return relative


def inventory(candidate_root: str) -> dict:
    try:
        root = Path(candidate_root).resolve(strict=True)
        if not root.is_dir():
            raise ValueError("candidate root must be a directory")
        discovered = []
        for target in root.rglob("*"):
            if target.is_file() and target.suffix.lower() == ".md":
                resolved = target.resolve(strict=True)
                relative = resolved.relative_to(root)
                discovered.append(relative.as_posix())
        if not discovered:
            raise ValueError("candidate root contains no Markdown files")
        entries = []
        seen = set()
        for raw in discovered:
            relative = safe_relative(raw)
            key = relative.as_posix().casefold()
            if key in seen:
                raise ValueError(f"duplicate target: {relative.as_posix()}")
            seen.add(key)
            target = root.joinpath(*relative.parts).resolve(strict=True)
            target.relative_to(root)
            raw_bytes = target.read_bytes()
            raw_bytes.decode("utf-8-sig", errors="strict")
            entries.append({
                "relative_path": relative.as_posix(),
                "sha256": hashlib.sha256(raw_bytes).hexdigest(),
                "bytes": len(raw_bytes),
            })
        entries.sort(key=lambda item: item["relative_path"].casefold())
        canonical = json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (OSError, UnicodeError, ValueError) as error:
        return failure("inventory_invalid", str(error), "Repair candidate paths or UTF-8 content and retry.")
    return {
        "ok": True,
        "schema": INVENTORY_SCHEMA,
        "candidate_inventory_sha256": hashlib.sha256(canonical).hexdigest(),
        "target_count": len(entries),
        "targets": entries,
    }


def state_path(candidate_root: str) -> Path:
    root = Path(candidate_root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("candidate root must be a directory")
    return root / STATE_NAME


def write_state(path: Path, state: dict) -> None:
    text = json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def begin(candidate_root: str) -> dict:
    try:
        initial_inventory = inventory(candidate_root)
        if initial_inventory.get("ok") is not True:
            raise ValueError(initial_inventory.get("message", "invalid candidate"))
        path = state_path(candidate_root)
        if path.exists():
            raise ValueError("a large-edit gate state already exists for this candidate root")
        state = {
            "schema": STATE_SCHEMA,
            "gate_id": uuid.uuid4().hex,
            "status": "active",
            "next_round": 1,
            "history": [],
        }
        write_state(path, state)
    except (OSError, UnicodeError, ValueError) as error:
        return failure("verification_gate_begin_failed", str(error), "Use the existing state or prepare a distinct user-authorized task candidate.")
    return {
        "ok": True,
        "code": "verification_gate_started",
        "gate_id": state["gate_id"],
        "next_round": 1,
        "candidate_inventory_sha256": initial_inventory["candidate_inventory_sha256"],
    }


def load_state(candidate_root: str) -> tuple[Path, dict]:
    path = state_path(candidate_root)
    if not path.is_file():
        raise ValueError("large-edit gate state is missing; run begin once before review")
    state = closed_object(
        read_json(str(path)),
        {"schema", "gate_id", "status", "next_round", "history"},
        "large-edit gate state",
    )
    if state["schema"] != STATE_SCHEMA or not isinstance(state["gate_id"], str) or len(state["gate_id"]) != 32:
        raise ValueError("large-edit gate state identity is invalid")
    if state["status"] not in {"active", "passed", "accepted_after_three_reviews"}:
        raise ValueError("large-edit gate state status is invalid")
    if type(state["next_round"]) is not int or not 1 <= state["next_round"] <= 3:
        raise ValueError("large-edit gate next_round is invalid")
    if not isinstance(state["history"], list):
        raise ValueError("large-edit gate history is inconsistent")
    for expected_round, entry in enumerate(state["history"], 1):
        item = closed_object(entry, {"round", "candidate_inventory_sha256", "verdict"}, "gate history entry")
        if item["round"] != expected_round or not valid_hash(item["candidate_inventory_sha256"]):
            raise ValueError("large-edit gate history entry is invalid")
        if item["verdict"] not in {"PASS", "FAIL"}:
            raise ValueError("large-edit gate history verdict is invalid")
    if state["status"] == "active":
        if len(state["history"]) != state["next_round"] - 1 or any(
            item["verdict"] != "FAIL" for item in state["history"]
        ):
            raise ValueError("active gate history is inconsistent")
    elif state["status"] == "passed":
        if len(state["history"]) != state["next_round"] or state["history"][-1]["verdict"] != "PASS":
            raise ValueError("passed gate history is inconsistent")
    elif (
        state["next_round"] != 3
        or len(state["history"]) != 3
        or any(item["verdict"] != "FAIL" for item in state["history"])
    ):
        raise ValueError("accepted_after_three_reviews gate history is inconsistent")
    return path, state


def valid_hash(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= HEX64


def validate_inventory(data: object) -> dict:
    inventory_data = closed_object(
        data,
        {"ok", "schema", "candidate_inventory_sha256", "target_count", "targets"},
        "candidate inventory",
    )
    if inventory_data["ok"] is not True or inventory_data["schema"] != INVENTORY_SCHEMA:
        raise ValueError("invalid candidate inventory")
    targets = inventory_data["targets"]
    if not isinstance(targets, list) or not targets:
        raise ValueError("candidate inventory targets must be a non-empty list")
    normalized = []
    seen = set()
    for item in targets:
        entry = closed_object(item, {"relative_path", "sha256", "bytes"}, "inventory target")
        relative = safe_relative(entry["relative_path"]).as_posix()
        key = relative.casefold()
        if key in seen:
            raise ValueError(f"duplicate inventory target: {relative}")
        seen.add(key)
        if not valid_hash(entry["sha256"]):
            raise ValueError(f"invalid target hash: {relative}")
        if type(entry["bytes"]) is not int or entry["bytes"] < 0:
            raise ValueError(f"invalid target byte count: {relative}")
        normalized.append({"relative_path": relative, "sha256": entry["sha256"], "bytes": entry["bytes"]})
    ordered = sorted(normalized, key=lambda item: item["relative_path"].casefold())
    if normalized != ordered or inventory_data["target_count"] != len(ordered):
        raise ValueError("inventory order or target_count is invalid")
    canonical = json.dumps(ordered, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    expected_hash = hashlib.sha256(canonical).hexdigest()
    if inventory_data["candidate_inventory_sha256"] != expected_hash:
        raise ValueError("candidate inventory aggregate hash is invalid")
    return inventory_data


def validate_deterministic_receipt(
    data: object, expected_gate_id: str, expected_hash: str, expected_round: int
) -> dict:
    receipt = closed_object(
        data,
        {"schema", "gate_id", "round", "candidate_inventory_sha256", "checks"},
        "deterministic receipt",
    )
    if receipt["schema"] != DETERMINISTIC_SCHEMA:
        raise ValueError("unsupported deterministic receipt schema")
    if receipt["gate_id"] != expected_gate_id:
        raise ValueError("deterministic receipt belongs to another persistent gate")
    if receipt["round"] != expected_round:
        raise ValueError(f"expected deterministic receipt round {expected_round}")
    if receipt["candidate_inventory_sha256"] != expected_hash:
        raise ValueError("deterministic receipt is stale or belongs to another candidate inventory")
    checks = closed_object(receipt["checks"], DETERMINISTIC_CHECK_KEYS, "deterministic checks")
    for name, raw in checks.items():
        check = closed_object(raw, {"status", "evidence_sha256", "reason"}, f"deterministic check {name}")
        if check["status"] not in {"PASS", "NOT_APPLICABLE"}:
            raise ValueError(f"deterministic check did not pass: {name}")
        if not valid_hash(check["evidence_sha256"]):
            raise ValueError(f"deterministic evidence hash is invalid: {name}")
        if not isinstance(check["reason"], str):
            raise ValueError(f"deterministic reason must be a string: {name}")
        if check["status"] == "NOT_APPLICABLE" and not check["reason"].strip():
            raise ValueError(f"NOT_APPLICABLE requires a reason: {name}")
    return receipt


def validate_receipt(
    data: object,
    expected_gate_id: str,
    expected_hash: str,
    expected_round: int,
    allowed_paths: set[str],
) -> dict:
    keys = {
        "schema", "gate_id", "round", "candidate_inventory_sha256", "verdict",
        "independent_subagent", "context", "checks", "findings",
    }
    receipt = closed_object(data, keys, "verifier receipt")
    if receipt["schema"] != RECEIPT_SCHEMA:
        raise ValueError("unsupported verifier receipt schema")
    if receipt["gate_id"] != expected_gate_id:
        raise ValueError("verifier receipt belongs to another persistent gate")
    if receipt["round"] != expected_round:
        raise ValueError(f"expected round {expected_round}")
    if receipt["candidate_inventory_sha256"] != expected_hash:
        raise ValueError("receipt is stale or belongs to another candidate inventory")
    if receipt["verdict"] not in {"PASS", "FAIL"}:
        raise ValueError("verdict must be PASS or FAIL")
    if receipt["independent_subagent"] is not True:
        raise ValueError("verifier must be an independent subagent")
    context = closed_object(receipt["context"], {"mode", "whole_scope_reason", "excluded_data_classes"}, "context")
    if context["mode"] not in {"changed_spans", "whole_requested_scope"}:
        raise ValueError("invalid context mode")
    if not isinstance(context["whole_scope_reason"], str):
        raise ValueError("whole_scope_reason must be a string")
    if context["mode"] == "whole_requested_scope" and not context["whole_scope_reason"].strip():
        raise ValueError("whole requested scope requires a reason")
    if set(context["excluded_data_classes"]) != EXCLUDED_CLASSES:
        raise ValueError("excluded_data_classes must equal the closed privacy set")
    checks = closed_object(receipt["checks"], CHECK_KEYS, "checks")
    if any(value not in CHECK_VALUES for value in checks.values()):
        raise ValueError("invalid semantic check value")
    findings = receipt["findings"]
    if not isinstance(findings, list):
        raise ValueError("findings must be a list")
    for finding in findings:
        item = closed_object(finding, FINDING_KEYS, "finding")
        if item["severity"] not in {"blocking", "advisory"}:
            raise ValueError("invalid finding severity")
        if not all(isinstance(item[key], str) for key in FINDING_KEYS - {"severity"}):
            raise ValueError("finding fields must be strings")
        relative_path = safe_relative(item["relative_path"]).as_posix()
        if relative_path not in allowed_paths:
            raise ValueError("finding relative_path is not in the complete candidate inventory")
    blocking = any(item["severity"] == "blocking" for item in findings)
    all_pass = all(value == "PASS" for value in checks.values())
    if receipt["verdict"] == "PASS" and (not all_pass or blocking):
        raise ValueError("PASS requires all semantic checks PASS and no blocking finding")
    if receipt["verdict"] == "FAIL" and all_pass and not blocking:
        raise ValueError("FAIL requires a non-passing check or blocking finding")
    return receipt


def gate(candidate_root: str, inventory_path: str, deterministic_path: str, receipt_path: str) -> dict:
    try:
        path, state = load_state(candidate_root)
        if state["status"] in {"passed", "accepted_after_three_reviews"}:
            raise ValueError("this gate is already write-eligible; use its existing state for the guarded write")
        round_number = state["next_round"]
        candidate_inventory = validate_inventory(read_json(inventory_path))
        candidate_hash = candidate_inventory["candidate_inventory_sha256"]
        if state["history"] and state["history"][-1]["candidate_inventory_sha256"] == candidate_hash:
            raise ValueError("a new round after FAIL requires changed candidate bytes and a new inventory")
        deterministic_receipt = validate_deterministic_receipt(
            read_json(deterministic_path), state["gate_id"], candidate_hash, round_number
        )
        allowed_paths = {item["relative_path"] for item in candidate_inventory["targets"]}
        receipt = validate_receipt(
            read_json(receipt_path), state["gate_id"], candidate_hash, round_number, allowed_paths
        )
        current = inventory(candidate_root)
        if current.get("ok") is not True or current["targets"] != candidate_inventory["targets"]:
            raise ValueError("all current candidate Markdown bytes must match the complete inventory")
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
        return failure("verification_gate_invalid", str(error), "Repair the inventory or receipt chain; do not write the candidate.")
    if receipt["verdict"] == "PASS":
        final_state, eligible = "passed", True
    elif round_number == 3:
        final_state, eligible = "accepted_after_three_reviews", True
    else:
        final_state, eligible = "revise", False
    state["history"].append({
        "round": round_number,
        "candidate_inventory_sha256": candidate_hash,
        "verdict": receipt["verdict"],
    })
    if final_state == "revise":
        state["next_round"] = round_number + 1
    else:
        state["status"] = final_state
    try:
        write_state(path, state)
    except OSError as error:
        return failure(
            "verification_gate_state_write_failed",
            str(error),
            "Do not write the candidate; repair the task-local gate state storage and rerun this round.",
        )
    return {
        "ok": True,
        "code": "verification_gate_complete",
        "gate_id": state["gate_id"],
        "candidate_inventory_sha256": candidate_hash,
        "round_count": len(state["history"]),
        "deterministic_receipt_count": len(state["history"]),
        "final_state": final_state,
        "write_eligible": eligible,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Classify and validate independent review for large Obsidian edits.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    classify_parser = subparsers.add_parser("classify")
    classify_parser.add_argument("--input", required=True)
    classify_parser.add_argument("--output")
    begin_parser = subparsers.add_parser("begin")
    begin_parser.add_argument("--candidate-root", required=True)
    begin_parser.add_argument("--output")
    inventory_parser = subparsers.add_parser("inventory")
    inventory_parser.add_argument("--candidate-root", required=True)
    inventory_parser.add_argument("--output")
    gate_parser = subparsers.add_parser("gate")
    gate_parser.add_argument("--candidate-root", required=True)
    gate_parser.add_argument("--inventory", required=True)
    gate_parser.add_argument("--deterministic-receipt", required=True)
    gate_parser.add_argument("--receipt", required=True)
    gate_parser.add_argument("--output")
    args = parser.parse_args()
    if args.command == "classify":
        result = classify(args.input)
    elif args.command == "begin":
        result = begin(args.candidate_root)
    elif args.command == "inventory":
        result = inventory(args.candidate_root)
    else:
        result = gate(args.candidate_root, args.inventory, args.deterministic_receipt, args.receipt)
    return emit(result, args.output)


if __name__ == "__main__":
    sys.exit(main())
