"""Check that the computation-tool policy anchors of this Skill are still present.

The checker validates policy text structure only: required anchors in SKILL.md,
its required references, and agents/openai.yaml, plus forbidden enumerations of
other Skills' tasks. It does not execute any backend or MCP tool.
"""

from __future__ import annotations

import argparse
from pathlib import Path


REQUIRED_TEXT = {
    "external verification trigger": "external-tool or MCP verification explicitly requested by the user",
    "mandatory gate heading": "## Mandatory Computation-Tool MCP Gate",
    "context tool": "exposes a documentation, context, or capability-listing tool",
    "execution tool": "execute the requested computation through that MCP tool",
    "actual execution return": "the execution call itself must return",
    "no local-script substitution": "Do not substitute a local interpreter, a generated script",
    "MCP-specific failure rule": "a non-MCP fallback does not fulfill the request",
    "protocol version separation": "never infer the protocol version from host, server, client, or runtime versions",
    "execution profiles": "## Execution Profiles",
    "default chat profile": "`chat` is the default",
    "problem-structure precheck": "run one bounded problem-structure precheck",
    "single full-range default": "Run at most one full-range primary computation by default",
    "second full-range justification": "A second full-range run requires",
    "explicit stop rule": "Stop as soon as",
    "local artifact preflight": "Before asking MCP to inspect a generated local artifact",
    "platform policy heading": "## Execution Platform Policy",
    "no silent platform switch": "do not silently switch between local, container, and remote environments",
    "call-window heading": "## Tool Call-Window and Long-Run Routing",
    "outer deadline separation": "A larger inner time limit does not extend an outer tool deadline",
    "representative calibration": "benchmark representative slices from the low, middle, and high-cost regions",
    "call-window safety margin": "target no more than one third of the shortest known call window",
    "durable local route": "use an actually callable local executable or monitorable process for the full run",
    "coverage checkpoints": "prove complete non-overlapping coverage",
    "complete training checkpoints": "A training checkpoint must include every state the resumed run needs",
    "health-based waiting": "Continue waiting while progress advances, resource use remains safe",
    "timeout duplicate prevention": "Do not launch a duplicate full-range computation while cancellation is uncertain",
    "probabilistic filter boundary": "A probabilistic filter may reduce exact-verifier work but cannot support an exact final claim by itself",
    "compact progress output": "Keep long progress output out of the conversation",
    "chat record scope": "A `chat` task does not require `computation-record.json` solely because the computation is long",
    "feasibility gate": "## Mandatory Feasibility and Completion Gate",
    "estimate first": "Estimate feasibility first",
    "expensive workloads": "especially training runs, large evaluation or ablation grids, and exhaustive enumerations",
    "mandatory execution": "The model has no discretion to omit it",
    "actual result delivery": "Return the actual computed result",
    "no extra verification": "does not require unrequested cross-validation",
    "long runtime warning": "If expected runtime is long, warn the user",
    "user termination authority": "The user retains the right to terminate it",
    "duration not stop condition": "duration alone is not a stopping condition",
    "fastest-completion heading": "## User-Requested Fastest-Completion Mode",
    "explicit speed trigger": "only when the user explicitly asks to finish the computation as fast as possible",
    "parallel overhead gate": "safely decomposable and large enough to repay process, kernel-launch, data-transfer, synchronization, and memory overhead",
    "capacity inspection": "Inspect the selected execution environment's actual capacity",
    "bounded parallel attempt": "attempt an appropriate bounded parallel implementation",
    "serial fallback": "return to the best serial implementation",
    "parallel correctness boundary": "Preserve exactness, precision, deterministic seeds where applicable",
    "evidence grades": "Classify the evidence as `formal`, `certificate`, `exact_reproduction`, `bounded_empirical`, or `numerical_evidence`",
    "verified-boundary rule": "A program running successfully, a test passing, or a metric improving verifies only the boundary checked; it is not a correctness proof and not evidence that a mechanism causes an effect",
    "no complexity proof from fits": "A scaling fit is at most `bounded_empirical`, never a complexity proof",
    "exclusive computation scope": "This Skill handles only computer-science and AI computation and experiment tasks. Do not route any other task to it, and do not perform work outside this scope.",
    "backend readiness heading": "## Backend Readiness Gate",
    "inventory read-or-create": "`python scripts/backend_inventory.py --mode ReadOrCreate`",
    "PowerShell inventory compatibility": "`scripts/backend_inventory.ps1 -Mode ReadOrCreate`",
    "cache-hit no-start rule": "On a cache hit, do not start Python, PyTorch, JAX, TensorFlow, an accelerator query, a solver, a proof assistant, a profiler, or any MCP tool",
    "session MCP overlay": "Build a current-session MCP overlay",
    "historical MCP boundary": "A historical MCP result never proves current callability",
    "primary and fallback readiness": "choose one primary route and a concrete fallback",
    "capability runner": "`python scripts/run_python_capability.py --capability <capability> -- <child-script> [args...]`",
    "targeted invalidation": "`backend_inventory.py --mode Invalidate --backend <name> --reason-code <code>`",
    "cache-hit performance threshold": "above two seconds as a performance fault",
    "English Skill heading": "# CS/AI Computation & Experiments",
}

FORBIDDEN_TEXT = {
    "Vault task enumeration": "Vault writes",
    "PDF task enumeration": "PDF source verification",
    "research task enumeration": "research-contract approval",
    "entry-routing task enumeration": "Teacher entry routing",
}

REQUIRED_OPENAI_TEXT = {
    "English display name": 'display_name: "CS/AI Computation & Experiments"',
    "English short description": 'short_description: "Route computations efficiently and verify proportionately"',
    "chat efficiency prompt": "Stop after the requested result and proportionate verification are secure",
    "feasibility prompt": "estimate feasibility first",
    "mandatory computation prompt": "actual execution and delivery of the computed result are mandatory",
    "user termination prompt": "only the user may choose to terminate",
    "evidence grade prompt": "Grade evidence as formal, certificate, exact_reproduction, bounded_empirical, or numerical_evidence",
    "verified-boundary prompt": "verifies only the boundary checked; it is not a correctness proof and not evidence that a mechanism causes an effect",
    "fastest-completion prompt": "explicitly requests the fastest possible completion or minimum wall time",
    "multicore fallback prompt": "fall back to the best serial route when parallelism is unavailable, unsafe, or slower",
    "call-window prompt": "Treat a backend's inner time limit and the outer MCP or shell tool-call deadline as separate limits",
    "local monitor prompt": "route a workload such as a training run that cannot fit tool call windows to a monitorable local process",
    "timeout inspection prompt": "inspect whether work is still running before retrying after a timeout",
    "exclusive scope prompt": "only for computer-science and AI computation and experiment tasks; do not route unrelated work to it or perform work outside that scope",
    "inventory prompt": "read or create the persistent local backend inventory first",
    "cache-hit no-start prompt": "A valid cache hit must not start Python, a framework, an accelerator query, a solver, a proof assistant, a profiler, or any MCP tool",
    "session MCP authority prompt": "Treat persisted MCP information as historical only",
    "targeted refresh prompt": "Refresh or invalidate only the affected local record",
    "MCP execution prompt": "do not substitute a local script for a callable MCP execution tool that the route requires",
    "platform policy prompt": "Follow the Execution Platform Policy in SKILL.md",
}


def validate(skill_text: str, openai_text: str) -> list[str]:
    errors = [
        f"missing: {label}"
        for label, required in REQUIRED_TEXT.items()
        if required not in skill_text
    ]
    errors.extend(
        f"missing: {label}"
        for label, required in REQUIRED_OPENAI_TEXT.items()
        if required not in openai_text
    )
    errors.extend(
        f"forbidden: {label}"
        for label, forbidden in FORBIDDEN_TEXT.items()
        if forbidden in skill_text
    )
    return errors



POLICY_REFERENCES = {'Tool Call-Window and Long-Run Routing': 'references/long-running-computations.md', 'User-Requested Fastest-Completion Mode': 'references/fastest-completion.md', 'Computation Record': 'references/reproducible-computation-records.md'}

def load_policy_text(skill_path: Path) -> str:
    """Validate fixed routing entrances before locally assembling policy evidence."""
    skill_path = Path(skill_path)
    main = skill_path.read_text(encoding="utf-8")
    parts = [main]
    root = skill_path.parent.resolve(strict=True)
    for heading, relative in POLICY_REFERENCES.items():
        title = "## " + heading
        start = main.find(title + "\n")
        if start < 0:
            raise ValueError("required_policy_entrance_missing")
        end = main.find("\n## ", start + len(title))
        entrance = main[start:end if end >= 0 else len(main)]
        if "you must read" not in entrance or "(" + relative + ")" not in entrance:
            raise ValueError("required_policy_entrance_invalid")
        reference = root / relative
        if reference.is_symlink() or reference.resolve(strict=True).parent != (root / "references").resolve(strict=True):
            raise ValueError("policy_reference_outside_scope")
        if (root / "references").is_symlink() or (root / "references").resolve(strict=True).parent != root:
            raise ValueError("policy_reference_parent_outside_scope")
        if reference.stat().st_size > 65536:
            raise ValueError("policy_reference_oversized")
        body = reference.read_text(encoding="utf-8")
        if not body.startswith(title + "\n"):
            raise ValueError("policy_reference_heading_mismatch")
        parts.append(body)
    return "\n".join(parts)

def _main() -> int:
    from computation_output import SafeParser, add_output_arguments, configure_output, emit_result as emit_public
    parser = SafeParser()
    add_output_arguments(parser)
    parser.add_argument("--skill-file", required=True)
    parser.add_argument("--openai-file", required=True)
    args = parser.parse_args()
    configure_output(args)

    skill_path = Path(args.skill_file)
    openai_path = Path(args.openai_file)
    errors = validate(
        load_policy_text(skill_path),
        openai_path.read_text(encoding="utf-8"),
    )
    emit_public({"ok": not errors, "code": "mcp_policy_invalid" if errors else "mcp_policy_valid", "error_count": len(errors), "details": errors[:20], "details_complete": len(errors)<=20, "raw_diagnostics_returned": False})
    return 1 if errors else 0


def main() -> int:
    from computation_output import public_main
    return public_main(_main)()


if __name__ == "__main__":
    raise SystemExit(main())
