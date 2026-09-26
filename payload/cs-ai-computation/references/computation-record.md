# Computation record

Use `computation-record.json` for program delivery, experiments, long calculations such as training runs, and any task for which the user requests files or reproducibility evidence.

## Required content

- the task file and its SHA-256;
- the computational object (algorithm, model, dataset, query, or proof obligation) and requested deliverables;
- assumptions, input domain, declared range of datasets, scales, seeds, or hyper-parameters, and precision policy;
- targeted candidate implementations for each considered backend, including existence and local-availability evidence;
- the selected backend, version, interface, selection reason, and a nonempty fallback reason or `not-required`;
- the environment: operating system, Python version or `not-applicable`, relevant package versions such as PyTorch, JAX, TensorFlow, NumPy, or the solver, the accelerator runtime such as the CUDA or MPS runtime or `none`, and the accelerator driver when one is used;
- the hardware: processor, accelerators, and memory;
- the seeds and the determinism policy, or `not-applicable` with the reason;
- every dataset with its identifier, version, and SHA-256, plus a safe relative path when the file is delivered with the record;
- the configuration summary, plus a hashed configuration file when one exists;
- exact input, command, or code entry point;
- relative paths and SHA-256 hashes for at least one code artifact and one result artifact;
- a completed result summary and the reported metrics, when the task produces metrics;
- one or more verification methods, an evidence grade, the checker identity for `formal` and `certificate` grades, residual or error information when the precision mode is numerical, and known limitations.

The five evidence grades, strongest first, are:

- `formal`: an identified proof assistant, model checker, or exhaustive state-space verification checked the stated property under recorded assumptions, axioms, and library versions;
- `certificate`: the artifact is a machine-checkable certificate or counterexample, such as a satisfying model, an unsatisfiability proof, a failing input, or an interval enclosure, recorded together with the checker that validated it, and its sufficiency for the claim is explained;
- `exact_reproduction`: rerunning with fixed data, environment, configuration, and seeds reproduced the recorded result identically or within the declared tolerance;
- `bounded_empirical`: the result holds within the declared range of datasets, scales, seeds, or hyper-parameters, and the record claims nothing outside that range;
- `numerical_evidence`: loss curves, metric agreement, sampled, simulated, or approximate evidence; suggestive only.

The label reports the strongest justified status of the computation, not the importance of the result. A program running successfully, a test passing, or a metric improving verifies only the boundary checked; it is not a correctness proof and not evidence that a mechanism causes an effect. An empirical scaling fit is at most `bounded_empirical` and never a complexity proof.

## Artifact paths

Keep artifact, configuration, and dataset paths relative to the record directory or the explicit `--base-dir`. The validator rejects absolute paths and paths that escape that directory. Hash the final files after all edits.

Do not include `computation-record.json` as one of its own hashed artifacts. That would create a circular hash dependency.

## Validator scope

`computation_record.py validate` checks schema fields, safe relative paths, file existence, and hashes. A dataset hash without a local path is recorded but cannot be recomputed; the validator reports how many such hashes it did not check. It does not execute code, inspect algorithmic or statistical semantics, or prove that the chosen evidence grade is correct. Review those claims separately.
