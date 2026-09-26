# Backend routing

Use this file only for a nontrivial computation that still needs a backend. First identify the closest task class, then read that route and combine it with the current backend inventory. Do not inspect every installed system before each calculation.

## How to use the catalog

Keep evidence obligation, task fit, and availability separate:

1. Classify the claim by the evidence grade it needs: exploration (`numerical_evidence`), a claim within a declared range (`bounded_empirical`), exact reproduction of a frozen run (`exact_reproduction`), a machine-checkable certificate or counterexample (`certificate`), a formal check (`formal`), or another explicit evidence obligation.
2. Find the matching task class and its preferred implementation strategy.
3. Check whether the required backend is callable on the current machine and in the current session.
4. Use the fallback when the primary route is unavailable or its stated conditions do not hold.
5. If no row matches, apply the general principles below and record the missing category for later evaluation.

Accelerator speed is a shared feature, not the deciding criterion. PyTorch, JAX, and TensorFlow are general frameworks; `z3`, `hypothesis`, and `mpmath` are Python libraries whose suitability depends on the selected interpreter, import path, semantics, and the evidence the result must support.

`heuristic` means the route reflects current engineering judgment and practice, not a controlled benchmark. `benchmarked` is allowed only when `evidence_ids` cites an entry in [backend-routing-evidence.md](backend-routing-evidence.md). A `heuristic` route never names its primary or fallback as the best, fastest, optimal, or superior choice.

## Routing catalog

| route_id | task_class | conditions | primary | fallback | decision_metrics | evidence_status | evidence_ids |
| --- | --- | --- | --- | --- | --- | --- | --- |
| general-computation | General numerical, data-processing, and scripting computation | A documented library implementation exists and its semantics fit the requested domain and precision | Python with NumPy, SciPy, or pandas, or the standard library when no third-party module is needed | Standard-library Python on a reduced declared input when a required module is unavailable | correctness, exactness, wall time, memory, reproducibility | heuristic | none |
| training-run | Train or fine-tune a model, including resumed runs | Model code, dataset version, configuration, seeds, and budget are fixed before the full run; the calibrated run fits accelerator and host memory | The framework of the existing code or checkpoint (PyTorch, JAX, or TensorFlow) on a live-checked accelerator, as a monitored local process with validated checkpoints | The same framework on CPU or on a smaller declared configuration, reported as a changed experiment rather than the requested run | correctness invariants, wall time, throughput, accelerator and host memory, recoverability, reproducibility | heuristic | none |
| evaluation-run | Evaluate a fixed model or checkpoint on fixed datasets and metrics | Checkpoint, dataset version and split, preprocessing, and metric implementation are identified and hashed where available | The framework and metric implementation that produced or will consume the reported numbers, in evaluation mode with fixed seeds | A second established metric implementation applied to the same saved predictions as a cross-check | metric agreement, determinism, coverage of the declared split, wall time, reproducibility | heuristic | none |
| ablation-grid | Grid or sweep over configurations, components, or hyper-parameters | Every cell's configuration, seed set, and budget are declared before execution and cells are independent | Checkpointed per-cell local runs with a completed-cell ledger that proves non-overlapping coverage | A reduced grid with fewer cells or seeds only after the user's decision, with the reduced range declared | coverage, per-cell reproducibility, seed variance, wall time, recoverability | heuristic | none |
| benchmark-profile | Wall-time, throughput, latency, or memory benchmarking and profiling | Hardware, software versions, input sizes, warm-up, and repetition count are fixed and recorded; competing load is controlled when isolation is required | A repeated-measurement harness with warm-up and accelerator synchronization, framework profilers such as `torch.profiler`, and `hyperfine`, `py-spy`, `perf`, or Nsight when installed | Plain repeated wall-clock timing with the same warm-up and repetition protocol | measurement variance, warm-up handling, synchronization, profiler overhead, reproducibility | heuristic | none |
| statistical-inference | Significance tests, confidence intervals, bootstrap, permutation tests, multiple-comparison correction, and effect sizes over runs or examples | The unit of analysis, pairing, test, and correction are chosen before the results are inspected | SciPy (`scipy.stats`) or statsmodels with explicit resampling seeds | A NumPy or standard-library implementation of the same declared procedure, cross-checked on a small case | fidelity to the declared procedure, test assumptions, resampling stability, reproducibility | heuristic | none |
| numerical-linear-algebra | Decompositions, solves, eigenproblems, conditioning, and numerical stability analysis | Matrix size, structure, dtype, and required accuracy are known | NumPy or SciPy (LAPACK and BLAS backed), or framework linear algebra on an accelerator when the data already lives there | Higher precision (`float64` or `mpmath`) or an interval enclosure through `mpmath.iv` and `run_python_capability.py` when a checkable error bound is required | residual and backward error, condition number, precision scaling, wall time, memory | heuristic | none |
| symbolic-derivation | Closed-form gradients, Jacobians, recurrence solutions, series, and simplification | The expression, variables, domain assumptions, and simplification target are explicit | SymPy with declared assumptions | Automatic differentiation or finite differences compared with the symbolic result at sample points, which is `numerical_evidence` only | correctness by substitution, domain fidelity, expression growth, wall time | heuristic | none |
| sat-smt-query | Satisfiability, bounded verification, counterexample search, and constraint solving | The encoding, logic or theory, bounds, and the meaning of sat, unsat, and unknown answers are explicit | Z3 through its Python binding or executable, or cvc5 | The other SMT solver on the same SMT-LIB encoding; a satisfying model is always checked by substitution into the original constraints | answer agreement, model validation, unsat-proof or unsat-core availability, wall time, memory | heuristic | none |
| formal-proof-check | Machine-check a proof, a verified-program obligation, or an exhaustive finite-state property | The statement, library versions, axioms, and checked files are fixed; for model checking, the model and state bounds are declared | Lean, Coq, or Isabelle for proofs; SPIN, CBMC, or another installed model checker for finite-state properties | Report the obligation as unchecked; a failed or unavailable check is never replaced by a prose argument labeled `formal` | checker verdict, trusted base including axioms and absence of `sorry` or `admit`, library versions, reproducibility | heuristic | none |
| algorithm-correctness-test | Test an implementation against a specification with property-based testing, brute-force cross-checks on small inputs, and edge cases | A reference implementation or checkable property exists and the exhaustive small-input range is declared | Hypothesis property tests plus a brute-force oracle over every input up to the declared size | Seeded random differential testing against the reference implementation | counterexample discovery, shrinking, declared input coverage, reproducibility | heuristic | none |
| complexity-measurement | Empirical scaling measurements and fits of time or memory against input size | Input sizes span a declared range with repetitions and the fit models are chosen before fitting; the claim is `numerical_evidence` or `bounded_empirical`, never a complexity proof | Timed runs under the benchmark protocol with log-log or model-comparison fits in NumPy or SciPy | Instrumented operation counts reported over the same declared range | fit residuals, range coverage, variance, constant-factor sensitivity | heuristic | none |
| frozen-run-replay | Replay or extend a saved run, checkpoint, certificate, or counterexample | The artifact freezes an implementation, environment, seeds, data version, precision policy, or serialized format | Reproduce the original implementation, environment, and version first | Use a second live-verified backend only as independent evidence, not as a byte-equivalent replay | reproducibility within the declared tolerance, semantic fidelity, auditability, exact artifact identity | heuristic | none |

## General selection principles

- Prefer a documented direct implementation over rebuilding the algorithm.
- Select by the evidence obligation before comparing speed or convenience.
- Match the framework to the existing code, checkpoint, or deliverable. Framework and hardware preferences remain `heuristic` until a registered benchmark supports them.
- Verify function semantics, input domain, exactness, dtype and numeric precision, determinism settings, and required modules only for the selected route.
- Do not infer callability from model memory, an installation directory, a recorded accelerator, or an old inventory record.
- If the preferred implementation is unavailable, state the limitation and why the fallback is adequate.
- Prefer exact arithmetic, exhaustive checks, or solver-based checks for exact and finite claims. Use numerical methods only at a precision and with an error check suitable for the requested claim.
- Prefer a monitorable local process with checkpoints over a single tool call when the calibrated material workload cannot fit its call window.

## Verification guide

- Exact symbolic output: substitution, normalization, a checkable certificate, or an independently derived identity.
- Numerical linear algebra: residuals, backward error, conditioning, precision escalation, or certified bounds.
- Training and evaluation: sanity baselines, invariants such as finite loss, shape and dtype checks, and no overlap between training and evaluation data, convergence checks, fixed seeds, and seed variation when applicable.
- Discrete algorithms: boundary cases, property-based tests, and brute-force cross-checks on every input up to a declared size.
- Solver answers: substitute a satisfying model into the original constraints; treat an unsat answer as trusted solver output unless a proof or certificate is checked; treat unknown or a timeout as no answer.
- Statistics: state the test, the unit of analysis, the number of runs, and whether intervals are per comparison or corrected for multiple comparisons.

Use a second implementation only when it materially strengthens the requested evidence. Two calls to the same underlying algorithm are not independent, and two runs that differ only in seed are replicates, not independent implementations.

## Maintaining the catalog

Do not promote anecdotes, one-off timings, or model preference into a `benchmarked` rule. Run controlled comparisons through the Skill evaluation workflow, keep raw artifacts in the evaluation archive, and add only the promoted conclusion and its limitations to [backend-routing-evidence.md](backend-routing-evidence.md). Update the matching catalog row only after that evidence entry exists.

Do not install software, download datasets or model weights, start a network service, use a cloud kernel or remote accelerator, or transmit local inputs without explicit authorization.
