---
name: cs-ai-computation
description: Plan, implement, execute, verify, and deliver computer-science and AI computations and experiments with local Python (NumPy, SciPy, SymPy, pandas), PyTorch, JAX, TensorFlow, accelerators, SMT solvers, proof assistants, model checkers, or profilers. Use when the host agent needs numerical or symbolic calculation, especially training or evaluation runs, ablation grids, benchmarking or profiling, statistical significance or confidence-interval computation, numerical linear algebra, symbolic derivation, SAT/SMT queries, formal proof checks, algorithm correctness testing, complexity measurement, reproducible experiment code, a computation requested by another CS/AI Skill, or external-tool or MCP verification explicitly requested by the user. Estimate feasibility first; when an allowed callable backend has reasonable resources, actual computation and result delivery are mandatory. A successful run, a passing test, or an improved metric verifies only the boundary it checked; it is not a correctness proof and not evidence that a mechanism causes an effect.
version: v1.0.0
---

## Skill Run Observation

<!-- skill-observer:v2 -->
- As the first execution action after selecting this Skill, run `& $env:SKILL_OBSERVER_CLI begin --skill cs-ai-computation --quiet --fail-open; & $env:SKILL_OBSERVER_CLI phase set --skill cs-ai-computation --phase workflow.startup --quiet --fail-open` when `SKILL_OBSERVER_CLI` is available. In a POSIX shell, invoke the same commands as `"$SKILL_OBSERVER_CLI" ...`.
- At each actual workflow transition, append the matching fail-open `phase set --skill cs-ai-computation` call to the same shell tool invocation that starts the next business action. Use only `retrieve`, `verify_live`, `plan_change`, `mutate`, `validate`, `version_control`, and `final_response`; do not add a model-tool round trip only to mark a phase.
- When invoking a bundled production script from this Skill, use the matching `scripts/observer_run.py`, `observer_run.ps1`, or `observer_run.mjs` wrapper when that wrapper exists, and record the matching production-entry phase from `references/observer-phases.json`; use `cs-ai-computation.script.run` only when the target entry is not listed. Wrappers must preserve the child process output and exit code.
- In the final business-tool invocation, set `final_response`; then, before the final response, run `& $env:SKILL_OBSERVER_CLI end --skill cs-ai-computation --status success --quiet --fail-open`. Use `failed` or `cancelled` instead of `success` when that outcome is known.
- Observation is fail-open and silent: never ask the user to repair it, never expose routine telemetry in the response, and never let an observer failure block the Skill.
- Do not pass prompts, file contents, tool inputs, tool outputs, secrets, or personal data to the observer. Host stop/session-end hooks, when available, close runs left open by interruption.
- Phase definitions and allowed fields live in [references/observer-data-dictionary.md](references/observer-data-dictionary.md); load it only when instrumenting or analyzing this Skill.

# CS/AI Computation & Experiments

## Role

Own the computation and experiment portion of a computer-science or AI task. Select an appropriate callable local backend, produce reproducible code or exact input, verify the output at the strength the evidence supports, and deliver the requested files.

Do not replace algorithmic or theoretical reasoning with a program run. A program running successfully, a test passing, or a metric improving verifies only the boundary checked; it is not a correctness proof and not evidence that a mechanism causes an effect.

## Execution Profiles

Choose the least expensive profile that still supports the user's requested claim:

- `chat` is the default when the user asks only for an answer and does not request files, reproducibility, certificates, or unusually strong independent verification. Use one primary computation and the cheapest sufficient check.
- `reproducible` applies when the user requests code, data, saved files, rerunnable results, or a handoff. Produce the requested artifacts and a validated computation record.
- `high-assurance` applies when the user explicitly requests strong independent verification, certificates, audit material, or when the claim's risk requires it. Use additional implementations, seeds, or full-range reruns only when they materially strengthen the evidence.

Do not ask which profile to use when the request makes the default clear. A faster profile never licenses a stronger claim than the evidence supports.

## Backend Readiness Gate

Apply this gate after the computation object and problem structure are clear but before committing to a backend-dependent implementation. The gate must be fast enough that capability discovery does not dominate an ordinary computation request.

1. Run `python scripts/backend_inventory.py --mode ReadOrCreate` for nontrivial work that still needs a computational backend. This is the platform-neutral Windows, macOS, and Linux entry. If Python is unavailable but PowerShell 7 is callable, use `scripts/backend_inventory.ps1 -Mode ReadOrCreate` as the compatibility entry. Use the current host's callable launcher; do not require or install PowerShell merely for inventory. Both entries read the same persistent capability snapshot and create it with one local scan when missing, invalid, expired, recorded for another host, or contradicted by a recorded path.
2. On a cache hit, do not start Python, PyTorch, JAX, TensorFlow, an accelerator query, a solver, a proof assistant, a profiler, or any MCP tool. The script checks only recorded executable paths, performs no state-file write, and should normally complete its internal work within 250 ms. Treat a cache-hit inventory operation above two seconds as a performance fault to diagnose, not normal preflight cost.
3. Treat the persistent snapshot as authoritative only for the recorded local facts at its stated time. Build a current-session MCP overlay from the tools advertised to the agent. A historical MCP result never proves current callability; live-check only the selected MCP backend when the route requires it.
4. Use the snapshot and current-session overlay to choose one primary route and a concrete fallback before writing backend-specific code. Do not probe every installed backend or MCP server merely to populate the table.
5. Classify the evidence obligation before selecting among Python, a deep-learning framework, an SMT solver, a model checker, and a proof assistant. Accelerator speed or framework familiarity alone is not a selector: distinguish exploration, a bounded empirical claim, exact reproduction of a frozen run, a machine-checkable certificate or counterexample, and formal verification. For a frozen run or certificate, replay its original implementation, environment, and version first; use another backend only as independent evidence.
6. Immediately before a material run, live-check the selected local executable, Python capability, accelerator, or MCP tool. For a Python library, framework, or accelerator capability such as `torch_cuda`, `torch_mps`, `jax_default`, `z3_smt`, `hypothesis_pbt`, or `mpmath_iv`, use `python scripts/run_python_capability.py --capability <capability> -- <child-script> [args...]`; it checks and runs through the selected interpreter and configured vendor search path. The child must not shadow the checked package in its script directory, alter that search path, or disable environment-based paths; such changes require a capability check inside the actual child environment. A cached library flag or a recorded accelerator is insufficient. If the selected capability is missing or execution fails, invalidate the affected local record with `backend_inventory.py --mode Invalidate --backend <name> --reason-code <code>`, or mark the MCP session overlay `degraded` or `unavailable`; then re-plan from the corrected state.
7. Read `python.libraries.torch`, `python.libraries.jax`, `python.libraries.tensorflow`, `python.libraries.scipy`, `python.libraries.z3`, and `python.libraries.hypothesis` as installed-library facts plus usage guidance, not as live execution evidence. Their emitted records state `purpose`, `evidence_boundary`, and `live_check_requirement`: the frameworks cover tensor computation, automatic differentiation, training, and evaluation; SciPy covers numerical optimization, linear algebra, and statistics; `z3` covers SMT queries; Hypothesis covers property-based testing. The presence of a library, framework, or accelerator never raises the evidence grade of a computation by itself.
8. Read [backend-inventory.md](references/backend-inventory.md) for the state-file contract, authority boundary, refresh rules, and schema link. The snapshot is mutable runtime state outside the Skill package; do not edit or version it as a Skill reference.

## Mandatory Feasibility and Completion Gate

Apply this gate to every computation problem, especially training runs, large evaluation or ablation grids, and exhaustive enumerations, to determine what “enough to answer” means for the user's requested deliverable. It complements the execution profiles and ordinary early-stop rule: when the user explicitly asks for a concrete computed value, metric, test outcome, solver answer, or checked proof, that result is part of the minimum sufficient answer; when the user asks only for a method, derivation, design, estimate, or bound, do not run an unrelated experiment.

1. Estimate feasibility first. Consider dataset and model size, the number of runs (configurations times seeds), algorithm and implementation, time complexity, steps or operation counts, expected wall time, accelerator memory, host memory, disk, numeric precision, input-size growth, output size, and the current task's available resources. A guess that a run merely “looks too large” is not a feasibility estimate.
2. Apply the Backend Readiness Gate before committing to a backend-dependent plan. When an MCP computation tool is selected or explicitly requested, use the Mandatory Computation-Tool MCP Gate to discover it and, when callable, query it for relevant capability support and actual execution. Distinguish a cached local fact, a currently advertised MCP tool, a successfully live-checked backend, and an estimate that the current computation will fit available resources.
3. If the requested deliverable includes a concrete result and the estimate shows that an allowed local backend, a live-checked accelerator, or a callable MCP tool can reasonably complete the task with the available resources, execute the computation. The model has no discretion to omit it, return only code, say that it is computable, substitute an untested script, or provide an estimated or recalled number when an actual result was requested and feasible.
4. Return the actual computed result in the conversation or the requested deliverable. Include the full value, metric table, solver answer, counterexample, or checker verdict when its own output size is reasonable; if result size makes inline delivery unreasonable, save or provide it through an authorized result artifact and state exactly where the complete result is available. This required primary calculation does not authorize unrequested cross-validation, alternative full-range reruns, extra seeds, extra backends, or optional artifacts.
5. If expected runtime is long, warn the user with the estimate and continue the authorized computation. Do not cancel, time-limit, downgrade, or abandon it solely because it is slow. The user retains the right to terminate it. A platform failure, exhausted hard resource limit, or explicit user cancellation may stop execution; report that event and any recoverable progress honestly.
6. If the estimate shows that the task is not feasible with available resources, report the concrete limiting resource and estimate. Do not present model judgment, an untested script, or a recalled or extrapolated number as the requested computed result.

## Workflow

1. State the computational object (algorithm, model, dataset, query, or proof obligation), input domain and declared range, assumptions, exact-versus-numerical intent, numeric precision, seeds, requested deliverables, and selected execution profile. Ask only when an unresolved choice would materially change the computation.
2. Before building the computational pipeline, run one bounded problem-structure precheck. Look for a known closed form, a direct argument, a reduction to a solved problem, symmetry, monotonicity, an existing library implementation, an existing checkpoint or recorded result, or a smaller equivalent instance that could remove most or all computation. A complete structural solution may remove unnecessary pipeline work, but it does not replace actual evaluation and result delivery when the user requested a feasible computed value.
3. For nontrivial work that still needs computation, apply the Backend Readiness Gate and the Mandatory Feasibility and Completion Gate. Distinguish:
   - whether a framework, library, solver, model checker, or proof assistant has a suitable documented implementation;
   - whether the persistent snapshot records a compatible local implementation;
   - whether the selected local or MCP implementation passes a current live check.
4. For a nontrivial task, classify the computation using [backend-routing.md](references/backend-routing.md), then read only the matching route. Combine that rule with the backend inventory and current live-check; a good task fit does not prove local callability. If no route matches, use the general selection principles, record the uncovered task class, and do not invent a benchmark-backed preference.
5. Apply the Mandatory Computation-Tool MCP Gate below when an MCP computation tool is the selected primary backend, when the user explicitly requests MCP or external-tool execution, or before substituting a local fallback for an MCP route. When the user explicitly requests external-tool or MCP verification, actual external execution is required; reasoning alone does not satisfy the request.
6. Use `scripts/backend_inventory.py` as the normal cross-platform local readiness entry. Use `scripts/backend_inventory.ps1` only when Python is unavailable and PowerShell 7 is callable. Run the matching `probe_backends.py` or `probe_backends.ps1` directly only for explicit diagnostics or when bypassing the cache is justified. None of these scripts can certify MCP callability.
7. Select one primary implementation and one concrete fallback that match the evidence obligation, task, and readiness evidence:
   - normally consider Python with NumPy, SciPy, and pandas first for general numerical, statistical, and data computation, and SymPy for symbolic derivation such as gradients, Jacobians, or recurrence solutions;
   - use PyTorch, JAX, or TensorFlow when training, evaluation, large-scale automatic differentiation, or accelerator execution is required; match the framework to the existing code, checkpoint, or requested deliverable;
   - use an SMT solver such as Z3 or cvc5 for satisfiability, bounded verification, and counterexample search, a model checker for exhaustive finite-state verification, and a proof assistant such as Lean, Coq, or Isabelle when a machine-checked proof is required. Record the reason.
8. Execute with explicit assumptions, input domains, dataset versions, configurations, numeric precision, seeds, determinism settings, tolerances, and resource limits as applicable. Run at most one full-range primary computation by default.
9. Check special and boundary cases with the cheapest method sufficient for the intended evidence grade: substitution of a solver model, residuals, brute-force cross-checks on small inputs, invariants, sanity baselines, precision escalation, error bounds, seed variation, or a second backend when it materially increases confidence. A second full-range run requires a discrepancy, inadequate first-run evidence, explicit user request, or the `high-assurance` profile; record that reason. Changing parameters or seeds while calling the same underlying implementation is not an independent implementation.
10. Classify the evidence as `formal`, `certificate`, `exact_reproduction`, `bounded_empirical`, or `numerical_evidence`. Never promote a bounded or numerical check into a universal correctness claim, a complexity bound, or a causal conclusion. A scaling fit is at most `bounded_empirical`, never a complexity proof.
11. Stop as soon as the requested computed result or deliverable exists, the selected verification has passed, the evidence grade is stated honestly, and no unresolved discrepancy remains. Do not add another backend, seed, or rerun merely for reassurance. For a long feasible computation already authorized by the user, duration alone is not a stopping condition.
12. Return the result at the requested level. Produce code, result files, and a validated `computation-record.json` for `reproducible` and `high-assurance` work when the requested deliverable needs them; keep `chat` results in the conversation unless the user asks to save them.

## Execution Platform Policy

Ordinary computation and experiments run on the execution environment that the user or project designates, normally the local host. This includes calculations delegated by other Skills, exploratory research, checking results, and the `chat`, `reproducible`, and `high-assurance` profiles. A request to verify an answer does not by itself make the task a dedicated test.

Only a task explicitly dedicated to testing or evaluation uses the environment selected by the active test contract, such as a named container, virtual machine, or benchmark host. This includes Skill/Harness tests, controlled benchmarks and regression evaluations. The ordinary environment is not a substitute for the required test environment. A static test that does not need a computation backend must not start one just to satisfy this rule.

Select the required platform before discovery and the Mandatory Computation-Tool MCP Gate. Verify the actual launch configuration or trusted runtime metadata of the environment or MCP server: a label alone does not prove its operating system, accelerator, or container. If the required platform is unavailable, report that condition and apply only the existing, task-permitted fallback rules on that platform; do not silently switch between local, container, and remote environments, or between accelerator and CPU execution. Existing explicit MCP-only requirements still prohibit a non-MCP fallback. This rule governs platform selection, not the separate task-based choice of framework, solver or other backend. It does not authorize installing software, starting an unrelated test environment or interrupting running computations.

## Mandatory Computation-Tool MCP Gate

Apply this gate when an MCP computation tool, such as a code-execution, notebook, solver, proof-assistant, or job-submission server, is the selected primary backend, when the user explicitly requests MCP or external-tool execution, or before substituting a local fallback for an MCP route. Do not invoke an MCP tool merely because it could be an optional extra verifier after a local primary route already supports the intended claim.

1. Discover the current turn's callable tools. If the required MCP tools are not already visible, use the platform's tool-discovery mechanism to search for them.
2. If the server exposes a documentation, context, or capability-listing tool and the task needs API or function selection, call it before writing or executing nontrivial code for that server.
3. If the server's execution tool is callable, execute the requested computation through that MCP tool. A server entry, installed package, successful backend probe, or MCP handshake does not satisfy this step; the execution call itself must return.
4. Do not substitute a local interpreter, a generated script, a local solver or proof-assistant binary, or mental calculation for a callable MCP execution tool that the route requires. Those are fallbacks only after tool discovery or an actual MCP call establishes that the tool is unavailable or failed.
5. Record the MCP tool used, the negotiated MCP `protocolVersion` from the `initialize` handshake or trusted runtime metadata, the MCP server name/version, and the runtime version the execution tool reports (for example its interpreter, framework, solver, or proof-assistant version). Keep these version fields distinct; never infer the protocol version from host, server, client, or runtime versions. After both the handshake and execution call succeed, persist the observation through `backend_inventory.py --mode RecordMcp` (or the PowerShell equivalent). The persisted observation remains historical-only and never proves current callability. When falling back, record the discovery or call failure and why the fallback is adequate.
6. If the user specifically required MCP rather than merely external verification, a non-MCP fallback does not fulfill the request. Stop and report the MCP failure unless the user already authorized a fallback.
7. Before asking MCP to inspect a generated local artifact, determine whether that artifact may be transmitted and whether the callable tool can read it. If transmission is not authorized or supported, do not make a doomed file-read attempt; use an allowed local verifier, or stop when the user required MCP-only execution.

## Tool Call-Window and Long-Run Routing

Before planning a computation or training run that may exceed a tool call window, or using chunks, monitoring or checkpoint recovery, you must read [Tool Call-Window and Long-Run Routing](references/long-running-computations.md) before proceeding. All original obligations and exceptions in that reference apply.

## Execution Efficiency

- Batch related availability checks, compilation, calibration, and result summarization into as few tool calls as practical without hiding failures.
- Keep long progress output out of the conversation. Write detailed step, epoch, or iteration logs to a file when needed and return compact status and final summaries.
- Reuse a backend, interpreter, or accelerator already verified during the current run. Do not try equivalent command-line front ends in sequence without a concrete failure or compatibility reason.
- Do not generate optional per-example prediction dumps, large tables, checkpoints, hashes, or delivery metadata for a `chat` request. Generate them only when they support the requested evidence or deliverable.
- Efficiency rules reduce avoidable work; they never authorize skipping a feasible requested computation or terminating a long run without the user's decision. Conversely, the completion gate does not require unrequested cross-validation, alternative full-range reruns, extra backends, or optional artifacts after the requested result is secure.

## User-Requested Fastest-Completion Mode

When the user explicitly requests fastest completion or minimum wall time, you must read [User-Requested Fastest-Completion Mode](references/fastest-completion.md) before proceeding. All original obligations and exceptions in that reference apply.

## Backend Probe

Run with Python 3 on Windows, macOS, or Linux:

```text
python "<SKILL_ROOT>/scripts/probe_backends.py"
```

If Python is unavailable and PowerShell 7 is callable, use the compatibility entry:

```powershell
pwsh -NoLogo -NoProfile -File "<SKILL_ROOT>\scripts\probe_backends.ps1"
```

Pass `--tool-command NAME=PATH` (PowerShell: `-ToolCommand 'NAME=PATH'`) only for a known native executable of a probed tool, for example `lean=/opt/lean/bin/lean`. On Windows, pass `--wsl-distro` and, if needed, `--wsl-command` to inspect one explicitly chosen WSL distribution. WSL is not attempted on macOS or Linux. Do not start or enumerate every WSL distribution merely to search for a backend.

Each tool resolves from `--tool-command` (PowerShell: `-ToolCommand`) first, then `PATH`, and for `nvidia-smi` on Windows the standard NVSMI directory. An explicit command is authoritative and does not silently fall through to another executable. For an elan-managed Lean, the probe reads the configured default toolchain and runs that toolchain's own `lean --version`, never the elan proxy, so probing cannot trigger a toolchain download. Accelerator discovery reads `nvidia-smi` when present and imports PyTorch only in a separate bounded child process when PyTorch is installed; JAX and TensorFlow device enumeration runs only when requested with `--framework-devices` (PowerShell: `-FrameworkDevices`), because importing those frameworks is slow and may reserve accelerator memory.

The command emits one JSON object. Preserve probe failures as availability evidence; do not turn them into installation actions.

For a stable local Python package root, set `CS_AI_COMPUTATION_VENDOR` or pass `--python-vendor-root` (PowerShell: `-PythonVendorRoot`). The probe reports installed-library metadata without importing the libraries. Before material framework, accelerator, solver, property-testing, or interval work, run the child through `scripts/run_python_capability.py`; its smoke test establishes callability only, not correctness, determinism, performance, or rigorous outward-rounding semantics.

## Policy Harness

Before synchronizing or packaging this Skill, run:

```powershell
python "<SKILL_ROOT>\scripts\check_mcp_policy.py" --skill-file "<SKILL_ROOT>\SKILL.md" --openai-file "<SKILL_ROOT>\agents\openai.yaml"
python "<SKILL_ROOT>\scripts\check_backend_routing.py" --routing-file "<SKILL_ROOT>\references\backend-routing.md" --evidence-file "<SKILL_ROOT>\references\backend-routing-evidence.md"
```

The first checker prevents accidental removal of the trigger, the mandatory computation-tool MCP gate, and the completion, long-run, and evidence-boundary rules. The routing checker requires every route to state its task class, conditions, primary route, fallback, decision metrics, and evidence status; a rule cannot be labeled `benchmarked` unless it cites a registered evidence entry, and a `heuristic` route cannot word its conditions, primary, or fallback as the best, fastest, optimal, or superior choice. These checks validate policy structure, not backend performance or actual MCP execution.

## Computation Record

When reproducible/file delivery, artifact handoff or the high-assurance profile requires a computation record, you must read [Computation Record](references/reproducible-computation-records.md) before proceeding. All original obligations and exceptions in that reference apply.

## Model-visible results and diagnostics

The Python inventory, backend probe, computation-record and capability-runner public mains return bounded UTF-8 responses, including newline, metadata, parser help and errors. The default is 65,536 bytes. Put global `--max-response-bytes N --response-reason "specific evidence gap"` before other command arguments to request a justified larger response, up to 1,048,576 bytes. Prefer selected fields and exact result artifacts before expanding. Always inspect `response_complete`; incomplete transport does not mean an empty result or a rolled-back computation.

The capability runner returns a structured result envelope on stdout. Small successful child results appear in `result`, with `result_format` identifying JSON or text; callers must read that field instead of treating the envelope as the computed value. Larger, non-UTF-8 or diagnostic-bearing outputs are retained in a unique local result directory, with exact byte counts and SHA-256 values in `artifacts`. `--result-parent` chooses the parent directory; otherwise local temporary storage is used. `result_complete` refers to the retained result, while `response_complete` refers only to this transport. Preserve the files until the requested answer/delivery has consumed their necessary evidence. Do not rerun a job because its result was not inlined.

Inventory views project known backend fields and bounded installation previews. Keep selected executable identities, installed-library facts and their evidence/live-check requirements; unknown fields and raw version banners are not planning context. Probe and child failures return fixed diagnostics; local raw error artifacts need their own scoped inspection. A successful process is not a correctness proof.

Before each result read, retain the current computation job, authorized input/artifact set, evidence already inspected and concrete remaining question. Expand for that question and reuse unchanged evidence; a per-response limit does not enforce a cumulative task quota. The public PowerShell inventory/probe entries provide native projected JSON with the same default byte ceiling; use `-MaxResponseBytes` and `-ResponseReason` for justified expansion. Invoke those public entries, not their internal implementation dependencies. Direct shell, generated computation code, external/MCP tools and development validators require their own reviewed return paths; do not describe this Python guard as host-wide isolation.

## Boundaries

- Do not install software, download datasets or model weights, enable a network service, use a cloud compute service, or transmit local data merely to satisfy backend preference.
- Do not invent backend availability from an installation path alone. Confirm executable availability with the probe, accelerator availability with a live capability check, and MCP availability with an actual tool call.
- Do not use a remote service, hosted notebook, or recalled result as an implicit substitute for a local backend.
- Do not report a model-derived function name as verified documentation.
- Do not hide a fallback. State why the preferred framework, solver, proof assistant, or accelerator was unsuitable or unavailable, including any CPU fallback for an accelerator route.
- Do not claim that a successful run, passing tests, random or property-based tests, plots, loss curves, metric improvements, or finite enumeration prove correctness, establish an asymptotic complexity bound, or show that a mechanism causes an effect; they verify only the boundary checked.
- This Skill handles only computer-science and AI computation and experiment tasks. Do not route any other task to it, and do not perform work outside this scope.

## Skill Maintenance Note

- Update rationale and maintenance: the Teacher package maintenance manual, docs/MAINTENANCE.md.

## Canonical terminology

Read [canonical terminology](references/terminology.md) before changing a persistent object, schema, lifecycle, authority/evidence rule, stable interface, specialized behavior term, or hash-bound identity. Do not introduce synonyms, rename canonical terms, change constitutive fields, or reuse deprecated/reserved names without updating the terminology registry, version history, migration rule, and validator first.

`computation_job` means a reproducible computer-science or AI computation or experiment with frozen inputs, environment, seeds, and acceptance checks.; `backend_snapshot` means a mutable local inventory of available computation backends and their verified capabilities.. These core distinctions are mandatory; the linked glossary is normative.
