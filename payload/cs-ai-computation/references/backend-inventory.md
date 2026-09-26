# Backend Inventory Contract

Use this reference when reading, refreshing, or diagnosing the local backend capability snapshot.

## Authority layers

The capability model has two layers that must not be conflated:

1. The persistent local snapshot records installed executable paths, versions, selected Python modules and frameworks, accelerator discovery, host hardware, and the time of the last successful local probe.
2. The current-session overlay records which MCP tools the agent can discover now and whether the selected MCP call actually succeeds now.

An MCP result in a prior run is historical evidence only. The persistent file never proves that an MCP server is installed, exposed, authenticated, healthy, or callable in the current session.

## State location

`scripts/backend_inventory.py` is the platform-neutral entry. Use it with the current Python 3 interpreter on Windows, macOS, or Linux. If Python is unavailable but PowerShell 7 is callable, use `scripts/backend_inventory.ps1`; it implements the same cache contract. Do not require PowerShell merely to discover whether a computation backend exists.

Both entries resolve the state file in this order:

1. explicit `--state-file` or `-StateFile`;
2. `CS_AI_BACKEND_INVENTORY`;
3. `<platform temporary directory>/Teacher/cs-ai-computation/backend-inventory.json` through the platform temporary-directory API.

The fixed temporary-directory path is reused across tasks but may be removed by operating-system cleanup; absence is therefore a normal cache miss, not an error. Set `CS_AI_BACKEND_INVENTORY` when a more durable writable location is available. The snapshot is runtime state, not a versioned file inside the Skill package. Writes use a temporary file in the destination directory followed by atomic replacement.

The local snapshot records the operating-system family and normalized architecture. A snapshot with missing or different host identity is refreshed instead of being trusted across machines. OS-specific discovery is bounded: PATH and explicit `--tool-command` overrides work on every platform, an elan-managed Lean is resolved to its installed default toolchain, and Apple silicon is recorded on macOS. WSL probing is Windows-only and runs only when the caller names a distribution.

The local backend records are:

| Backend | Contents |
| --- | --- |
| `python` | interpreter path and version, installed-library metadata for NumPy, SciPy, SymPy, pandas, scikit-learn, statsmodels, Hypothesis, PyTorch, JAX, TensorFlow, `z3`, `cvc5`, and `mpmath`, and an optional WSL record |
| `accelerators` | `nvidia-smi` identity with GPU names, driver version, and memory; a bounded PyTorch device query (CUDA, ROCm, and MPS); optional JAX and TensorFlow device queries; Apple silicon flag |
| `smt_solvers` | `z3` and `cvc5` executables |
| `proof_assistants` | `lean`, `coqc` or `rocq`, and `isabelle` |
| `model_checkers` | `spin` and `cbmc` |
| `profilers` | `hyperfine`, `py-spy`, `perf`, `nsys`, and `ncu` |

The snapshot also records host hardware: logical processor count, total memory, and processor model when the platform reports it.

## Fast path and refresh rules

- `--mode ReadOrCreate` (PowerShell: `-Mode ReadOrCreate`) reads a valid snapshot, checks only its recorded local executable paths and host identity, and returns immediately when the snapshot is unexpired. It must not start Python, a framework, an accelerator query, a solver, a proof assistant, a profiler, or any MCP tool on this cache-hit path.
- A missing file, invalid schema, expired snapshot, or missing recorded path triggers a local probe. Missing paths refresh only the affected records in the stored snapshot; expiry and invalid schema refresh all records.
- `--mode Refresh --backend <name>` (PowerShell: `-Mode Refresh -Backend <name>`) explicitly refreshes one or more records. `all` refreshes every local record. A targeted refresh probes only the named backends.
- `--mode Invalidate --backend <name> --reason-code <code>` (PowerShell: `-Mode Invalidate -Backend <name> -ReasonCode <code>`) records a bounded failure reason and immediately refreshes the selected local record. Use it after a selected executable, framework, or accelerator fails, changes version unexpectedly, or disappears. Typical reason codes are `user_requested`, `path_missing`, `execution_failed`, `version_mismatch`, `capability_missing`, and `device_unavailable`.
- A failed atomic write does not erase a successful probe result. The command returns the live result with `cache.status = write_failed`; the next run may retry persistence.
- `--no-write` (PowerShell: `-NoWrite`) runs the same read or probe path without creating or updating the state file and reports `cache.status = not_persisted` after a probe. Use it for diagnostics where the snapshot must stay untouched; `RecordMcp` rejects it.

The default maximum age is seven days. Set `--max-age-hours 0` (PowerShell: `-MaxAgeHours 0`) only for a controlled run that must disable age-based refresh.

## Planning rule

Read the snapshot before committing to a backend-dependent implementation. Merge it with the current turn's advertised tool list, choose a primary route and a concrete fallback, then live-check only the selected backend. If that live check contradicts the snapshot, invalidate or refresh the affected local record and re-plan from the corrected state.

For Python, library discovery and executable discovery remain historical snapshot facts; the probe reads package metadata without importing the libraries. Accelerator records are also historical: a recorded GPU or `mps_available = true` does not prove that the device is free, healthy, or callable now. Before material framework, accelerator, solver, property-testing, or interval work, use `scripts/run_python_capability.py --capability <capability> -- <child-script> [args...]`; the runner checks and launches the child with the same interpreter and import path, preventing a probe/run environment mismatch. Set `CS_AI_COMPUTATION_VENDOR` or pass `--vendor-root` for a stable local package root. Neither the probe nor runner installs packages, and a successful smoke test does not by itself certify correctness, determinism, performance, or rigorous outward rounding.

Every emitted inventory view includes `python.libraries.torch`, `python.libraries.jax`, `python.libraries.tensorflow`, `python.libraries.scipy`, `python.libraries.z3`, and `python.libraries.hypothesis`, even when the library is absent. Each record exposes `available`, `version`, `purpose`, `evidence_boundary`, and `live_check_requirement`. The frameworks are identified for tensor computation, automatic differentiation, training, and evaluation; their completed runs and metrics are `numerical_evidence` or `bounded_empirical`, not correctness proofs. SciPy is identified for numerical optimization, linear algebra, and statistics; its ordinary output is `numerical_evidence`, not a certificate. `z3` is identified for SMT queries; a satisfying model is checkable by substitution, while an unsat answer is trusted solver output unless a proof is checked. Hypothesis is identified for property-based testing; passing properties verify only the generated inputs. These guidance fields are an emitted view over snapshot schema `1.0`; a cache hit does not rewrite the stored snapshot.

For MCP, tool discovery supplies an `advertised` state and an actual selected tool call supplies `live`, `degraded`, or `unavailable`. Do not probe every MCP server merely to populate an inventory.

After a selected MCP computation server has both completed its `initialize` handshake and returned a live execution result, record the observation with `--mode RecordMcp`. Supply the negotiated `--mcp-protocol-version` separately from the MCP server implementation version and the runtime version reported by the execution tool. The protocol value must be the handshake's date-form version; never infer it from a host, server, client, or runtime version. `RecordMcp` also requires `--mcp-server-name`, `--mcp-server-version`, and `--mcp-runtime-version`; partial records fail closed. Observations are keyed by server name, at most sixteen are retained, and each remains `historical_only` and cannot establish current callability.

## Performance budget

On a valid cache hit, internal inventory work should normally stay below 250 ms, with no backend startup and no state-file write. Treat two seconds as a hard diagnostic threshold for the inventory operation itself: if a cache hit exceeds it, inspect filesystem or endpoint-security latency before allowing the readiness gate to dominate a computation request. Backend startup, framework imports, and a cache-miss scan are outside the cache-hit budget and should occur only under the refresh rules above.
