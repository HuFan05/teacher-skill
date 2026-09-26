"""Staged acceptance with a bounded retry loop.

The mechanism this implements has four moving parts:

    classify    decide whether this work is large enough for the heavy gate
    inventory   freeze the candidate: relative paths, byte counts, hashes, and
                one aggregate hash — never bodies
    deterministic checks   run first; a mechanically invalid candidate does not
                consume a review round
    verifier    an independent reviewer judges one exact inventory hash

The retry loop has a declared ceiling and four rules that make it hard to game:

  1. **A FAIL in rounds one or two is not terminal.** Revise, take a new
     inventory, start the next round. A new round requires changed bytes.
  2. **A FAIL in round three is the fallback.** The gate becomes
     `accepted_after_three_reviews` and the candidate is write-eligible. This is
     the operator's explicit fallback rule, and it must never be reported as
     verifier approval.
  3. **Unusable verification does not count as a round.** A missing, malformed,
     stale or non-independent receipt leaves the gate active and write-ineligible.
     It does not advance the counter, because otherwise an unavailable verifier
     would burn the ceiling.
  4. **Never fabricate a FAIL to reach the fallback.** Rule 3 exists precisely so
     that reaching round three requires three real reviews.

The verifier itself is invoked by the caller, not by this module: the seam is a
receipt with a closed schema. In one deployment that caller spawns a fresh-context
agent; in another it asks a human. This module's job is to make the loop's rules
enforceable, not to pick the reviewer.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from . import constants as C
from .model import digest, digest_bytes, new_id, utc_now
from .store import Store

GATE_ACTIVE = "active"
GATE_PASSED = "passed"
GATE_FALLBACK = "accepted_after_three_reviews"

GATE_STATES = (GATE_ACTIVE, GATE_PASSED, GATE_FALLBACK)

VERDICT_PASS = "PASS"
VERDICT_FAIL = "FAIL"
VERDICTS = (VERDICT_PASS, VERDICT_FAIL)

MAX_ROUNDS = 3

# Trigger thresholds. A candidate enters the heavy gate only if it is large and
# non-mechanical, or if the operator asked for it explicitly.
THRESHOLD_TARGETS = 3
THRESHOLD_PROSE_TARGETS = 2
THRESHOLD_CHARS = 6000
THRESHOLD_CHANGED_CHARS = 2500
THRESHOLD_CHANGED_RATIO = 0.30

EXCLUSIONS = (
    "mechanical_only",
    "single_paragraph",
    "few_result_lines",
    "single_local_subsection",
    "narrow_repair",
)

CHECK_NAMES = (
    "utf8_and_path_containment",
    "schema_and_structure",
    "code_and_result_consistency",
    "references_and_links",
    "resource_references",
    "task_specific_lint",
    "diff_and_sentinels",
)

CHECK_STATUSES = ("PASS", "NOT_APPLICABLE")

HEX64 = re.compile(r"\A[0-9a-f]{64}\Z")

ERR_GATE_ACTIVE = "gate_active"
ERR_GATE_CLOSED = "already_write_eligible"
ERR_ROUNDS_EXHAUSTED = "rounds_exhausted"
ERR_VERIFIER_UNUSABLE = "verification_unusable"
ERR_VERIFIER_NOT_INDEPENDENT = "verifier_not_independent"
ERR_INVENTORY_STALE = "candidate_inventory_stale"
ERR_BYTES_UNCHANGED = "new_round_requires_changed_bytes"
ERR_MECHANICAL = "mechanical_candidate"
ERR_RECEIPT_BINDING = "receipt_binding"


class AcceptanceError(RuntimeError):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


# --------------------------------------------------------------------------
# Trigger
# --------------------------------------------------------------------------
def classify(metrics: Mapping[str, Any], *, operator_requested: bool = False) -> dict[str, Any]:
    """Decide whether the heavy gate applies. Exclusions override thresholds."""

    targets = int(metrics.get("targets", 0))
    prose_targets = int(metrics.get("prose_targets", 0))
    scope_chars = int(metrics.get("non_whitespace_scope_chars", 0))
    changed_chars = int(metrics.get("changed_non_whitespace_chars", 0))
    sections = int(metrics.get("top_level_sections", 0))
    exclusions = [name for name in EXCLUSIONS if metrics.get(name)]

    if exclusions:
        return {
            "schema": "th-acceptance-classification/v1",
            "applies": False,
            "reason": f"excluded: {', '.join(sorted(exclusions))}",
            "path": "ordinary",
        }

    reasons: list[str] = []
    if targets >= THRESHOLD_TARGETS and prose_targets >= THRESHOLD_PROSE_TARGETS:
        reasons.append("multi_target_substantive")
    ratio = (changed_chars / scope_chars) if scope_chars else 0.0
    if scope_chars >= THRESHOLD_CHARS and changed_chars >= THRESHOLD_CHANGED_CHARS and ratio >= THRESHOLD_CHANGED_RATIO:
        reasons.append("large_single_scope")
    if sections >= 3 and changed_chars >= THRESHOLD_CHANGED_CHARS:
        reasons.append("multi_section_coherence")

    if not reasons and not operator_requested:
        return {
            "schema": "th-acceptance-classification/v1",
            "applies": False,
            "reason": "below every threshold",
            "path": "ordinary",
        }
    if operator_requested and not reasons:
        reasons.append("operator_requested")
    return {
        "schema": "th-acceptance-classification/v1",
        "applies": True,
        "reasons": sorted(reasons),
        "path": "staged",
        "measured": {
            "targets": targets,
            "prose_targets": prose_targets,
            "scope_chars": scope_chars,
            "changed_chars": changed_chars,
            "changed_ratio": round(ratio, 4),
        },
    }


# --------------------------------------------------------------------------
# Inventory
# --------------------------------------------------------------------------
def inventory(candidate_root: str | Path, *, suffix: str = ".md") -> dict[str, Any]:
    """Freeze a candidate: paths, bytes, hashes, and one aggregate hash.

    Never bodies. That is what lets a receipt be shared without leaking the
    candidate, and what makes the aggregate hash a usable binding.
    """

    root = Path(candidate_root).expanduser().resolve()
    if not root.is_dir():
        raise AcceptanceError(ERR_INVENTORY_STALE, f"not a directory: {root}")
    entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        if suffix and path.suffix != suffix:
            continue
        # Containment is checked on the *resolved* path, so a symlink pointing
        # outside the candidate is refused rather than hashed as if it were part
        # of the candidate.
        resolved = path.resolve()
        if resolved != root and root not in resolved.parents:
            raise AcceptanceError("path_not_contained", str(path))
        relative = path.relative_to(root).as_posix()
        if relative.startswith("..") or Path(relative).is_absolute():
            raise AcceptanceError("path_not_contained", relative)
        raw = path.read_bytes()
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AcceptanceError("utf8_invalid", relative) from exc
        entries.append({"relative_path": relative, "bytes": len(raw), "sha256": digest_bytes(raw)})
    if not entries:
        raise AcceptanceError(ERR_INVENTORY_STALE, "no candidate files")
    body = {"targets": entries}
    return {
        "schema": "th-candidate-inventory/v1",
        "candidate_root": str(root),
        "target_count": len(entries),
        "targets": entries,
        "aggregate_sha256": digest(body),
    }


# --------------------------------------------------------------------------
# Receipts
# --------------------------------------------------------------------------
def deterministic_receipt(
    *,
    round_number: int,
    inventory_sha256: str,
    checks: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    if round_number < 1 or round_number > MAX_ROUNDS:
        raise AcceptanceError(ERR_ROUNDS_EXHAUSTED, str(round_number))
    normalized: dict[str, Any] = {}
    for name in CHECK_NAMES:
        entry = checks.get(name)
        if entry is None:
            raise AcceptanceError("deterministic_check_missing", name)
        status = entry.get("status")
        if status not in CHECK_STATUSES:
            raise AcceptanceError("deterministic_check_invalid", f"{name}: {status!r}")
        reason = str(entry.get("reason", ""))
        if status == "NOT_APPLICABLE" and not reason.strip():
            raise AcceptanceError("deterministic_check_invalid", f"{name}: NOT_APPLICABLE needs a reason")
        evidence = str(entry.get("evidence_sha256", ""))
        normalized[name] = {"status": status, "reason": reason, "evidence_sha256": evidence}
    return {
        "schema": "th-deterministic-receipt/v1",
        "round": round_number,
        "candidate_inventory_sha256": inventory_sha256,
        "checks": normalized,
    }


def deterministic_passed(receipt: Mapping[str, Any]) -> bool:
    return all(entry["status"] in CHECK_STATUSES for entry in receipt["checks"].values())


def verifier_receipt(
    *,
    round_number: int,
    inventory_sha256: str,
    verdict: str,
    independent: bool,
    findings: Sequence[Mapping[str, Any]] = (),
    checks: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    if verdict not in VERDICTS:
        raise AcceptanceError("verdict_unknown", str(verdict))
    normalized_findings = []
    for finding in findings:
        severity = finding.get("severity", "advisory")
        if severity not in ("blocking", "advisory"):
            raise AcceptanceError("finding_severity_unknown", str(severity))
        normalized_findings.append(
            {
                "relative_path": str(finding.get("relative_path", "")),
                "location": str(finding.get("location", "")),
                "category": str(finding.get("category", "")),
                "severity": severity,
                "message": str(finding.get("message", "")),
            }
        )
    supplied = dict(checks or {})
    for name in ("meaning_preservation", "language_and_voice", "terminology_and_conventions", "coherence"):
        supplied.setdefault(name, VERDICT_PASS)
        if supplied[name] not in VERDICTS:
            raise AcceptanceError("verifier_check_invalid", f"{name}: {supplied[name]!r}")
    if verdict == VERDICT_PASS and any(item["severity"] == "blocking" for item in normalized_findings):
        raise AcceptanceError("verdict_inconsistent", "a PASS cannot carry a blocking finding")
    return {
        "schema": "th-verifier-receipt/v1",
        "round": round_number,
        "candidate_inventory_sha256": inventory_sha256,
        "verdict": verdict,
        "independent": bool(independent),
        "checks": supplied,
        "findings": normalized_findings,
    }


def verification_usable(receipt: Any) -> tuple[bool, str]:
    """Is this receipt a completed review?

    Anything unusable is reported with a code and **does not advance the round
    counter**. Burning the ceiling on an unavailable verifier would turn an
    infrastructure problem into a completion decision.
    """

    if not isinstance(receipt, dict):
        return False, ERR_VERIFIER_UNUSABLE
    if receipt.get("schema") != "th-verifier-receipt/v1":
        return False, ERR_VERIFIER_UNUSABLE
    if receipt.get("verdict") not in VERDICTS:
        return False, ERR_VERIFIER_UNUSABLE
    if not receipt.get("independent"):
        return False, ERR_VERIFIER_NOT_INDEPENDENT
    return True, "ok"


# --------------------------------------------------------------------------
# Gate
# --------------------------------------------------------------------------
class AcceptanceGate:
    """A persistent gate. Its state lives in the store, not in a local variable."""

    def __init__(self, store: Store) -> None:
        self.store = store

    # -- lifecycle --------------------------------------------------------
    def begin(self, candidate_root: str | Path) -> dict[str, Any]:
        root = Path(candidate_root).expanduser().resolve()
        snapshot = inventory(root)
        gate = {
            "gate_id": new_id("GT"),
            "candidate_root": str(root),
            "round": 1,
            "state": GATE_ACTIVE,
            "write_eligible": False,
            "inventory_sha256": snapshot["aggregate_sha256"],
            "bytes_sha256": snapshot["aggregate_sha256"],
            "verdicts": [],
            "unresolved": [],
            "deterministic": [],
            "created_at": utc_now(),
        }
        self.store.observe(
            event_type="gate_opened",
            payload={"gate_id": gate["gate_id"], "round": 1},
            mutate=lambda connection: self.store.put_gate(connection, gate),
        )
        return {"gate": gate, "inventory": snapshot}

    def round_state(self, gate_id: str) -> dict[str, Any]:
        return self.store.gate(gate_id)

    def current_inventory(self, gate_id: str) -> dict[str, Any]:
        return inventory(self.store.gate(gate_id)["candidate_root"])

    # -- advance ----------------------------------------------------------
    def advance(
        self,
        gate_id: str,
        *,
        round_number: int,
        deterministic: Mapping[str, Any],
        verifier: Any | None,
    ) -> dict[str, Any]:
        """Apply one round. Order of checks mirrors the order of the rules."""

        gate = self.store.gate(gate_id)
        if gate["write_eligible"]:
            raise AcceptanceError(
                ERR_GATE_CLOSED, "this gate is already write-eligible; open a new gate to review more work"
            )
        if gate["state"] != GATE_ACTIVE:
            raise AcceptanceError(ERR_GATE_CLOSED, gate["state"])
        if round_number != gate["round"]:
            raise AcceptanceError(ERR_ROUNDS_EXHAUSTED, f"expected round {gate['round']}, got {round_number}")

        snapshot = inventory(gate["candidate_root"])
        if deterministic.get("candidate_inventory_sha256") != snapshot["aggregate_sha256"]:
            raise AcceptanceError(ERR_INVENTORY_STALE, "the deterministic receipt binds a different candidate")
        if deterministic.get("round") != round_number:
            raise AcceptanceError(ERR_RECEIPT_BINDING, "the deterministic receipt binds a different round")

        # Rule: a mechanically invalid candidate never reaches a reviewer, and
        # never costs a round.
        if not deterministic_passed(deterministic):
            failed = sorted(
                name for name, entry in deterministic["checks"].items() if entry["status"] not in CHECK_STATUSES
            )
            return {
                "gate": gate,
                "advanced": False,
                "reason": ERR_MECHANICAL,
                "failed_checks": failed,
                "round_consumed": False,
                "write_eligible": False,
                "note": "repair the candidate locally; this did not consume a review round",
            }

        if verifier is None:
            return self._inconclusive(gate, round_number, deterministic, ERR_VERIFIER_UNUSABLE)

        usable, code = verification_usable(verifier)
        if not usable:
            return self._inconclusive(gate, round_number, deterministic, code)

        if verifier.get("candidate_inventory_sha256") != snapshot["aggregate_sha256"]:
            raise AcceptanceError(ERR_INVENTORY_STALE, "the verifier receipt binds a different candidate")
        if verifier.get("round") != round_number:
            raise AcceptanceError(ERR_RECEIPT_BINDING, "the verifier receipt binds a different round")

        verdict = verifier["verdict"]
        blocking = [item for item in verifier.get("findings", []) if item["severity"] == "blocking"]
        verdicts = [*gate["verdicts"], {"round": round_number, "verdict": verdict}]
        unresolved = [*gate["unresolved"], *blocking] if verdict == VERDICT_FAIL else list(gate["unresolved"])

        if verdict == VERDICT_PASS:
            updated = {
                **gate,
                "round": round_number,
                "state": GATE_PASSED,
                "write_eligible": True,
                "inventory_sha256": snapshot["aggregate_sha256"],
                "verdicts": verdicts,
                "unresolved": unresolved,
                "deterministic": [*gate["deterministic"], round_number],
            }
            self._save(updated, "gate_passed")
            return {
                "gate": updated,
                "advanced": True,
                "reason": "verifier_pass",
                "round_consumed": True,
                "write_eligible": True,
                "approval": True,
            }

        # FAIL.
        if round_number < MAX_ROUNDS:
            updated = {
                **gate,
                "round": round_number + 1,
                "state": GATE_ACTIVE,
                "write_eligible": False,
                "inventory_sha256": snapshot["aggregate_sha256"],
                "verdicts": verdicts,
                "unresolved": unresolved,
                "deterministic": [*gate["deterministic"], round_number],
            }
            self._save(updated, "gate_round_failed")
            return {
                "gate": updated,
                "advanced": True,
                "reason": "fail_revise_and_recheck",
                "round_consumed": True,
                "next_round": round_number + 1,
                "write_eligible": False,
                "blocking_findings": blocking,
            }

        # Rule 2: the third failed round is the operator's fallback, and it is
        # reported as a fallback, never as approval.
        updated = {
            **gate,
            "round": MAX_ROUNDS,
            "state": GATE_FALLBACK,
            "write_eligible": True,
            "inventory_sha256": snapshot["aggregate_sha256"],
            "verdicts": verdicts,
            "unresolved": unresolved,
            "deterministic": [*gate["deterministic"], round_number],
        }
        self._save(updated, "gate_fallback_accepted")
        return {
            "gate": updated,
            "advanced": True,
            "reason": GATE_FALLBACK,
            "round_consumed": True,
            "write_eligible": True,
            "approval": False,
            "unresolved_findings": unresolved,
            "must_report_as": "fallback acceptance, not verifier approval",
        }

    def _inconclusive(
        self,
        gate: Mapping[str, Any],
        round_number: int,
        deterministic: Mapping[str, Any],
        code: str,
    ) -> dict[str, Any]:
        """Rule 3: an unusable verification leaves the gate where it was."""

        return {
            "gate": self.store.gate(gate["gate_id"]),
            "advanced": False,
            "reason": code,
            "round_consumed": False,
            "write_eligible": False,
            "note": "this round does not count; retry the same round when verification is available",
        }

    def _save(self, gate: Mapping[str, Any], event: str) -> None:
        self.store.observe(
            event_type=event,
            payload={"gate_id": gate["gate_id"], "round": gate["round"], "state": gate["state"]},
            mutate=lambda connection: self.store.put_gate(connection, dict(gate)),
        )

    # -- the last rule ----------------------------------------------------
    def assert_bytes_changed(self, gate_id: str, previous_inventory_sha256: str) -> None:
        """A new round requires changed bytes.

        Checking this is what makes "revise and retry" mean something: without it
        a caller could re-submit an untouched candidate until the fallback fired.
        """

        snapshot = inventory(self.store.gate(gate_id)["candidate_root"])
        if snapshot["aggregate_sha256"] == previous_inventory_sha256:
            raise AcceptanceError(ERR_BYTES_UNCHANGED, "the candidate is byte-identical to the failed round")
