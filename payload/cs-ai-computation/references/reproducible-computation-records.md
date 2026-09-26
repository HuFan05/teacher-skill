## Computation Record

Read [computation-record.md](computation-record.md), then initialize a record beside the deliverables:

```powershell
python "<SKILL_ROOT>\scripts\computation_record.py" init --task-file "<DELIVERY_DIR>\task.md" --record "<DELIVERY_DIR>\computation-record.json"
```

Fill the record after execution, compute the artifact hashes, and validate it:

```powershell
python "<SKILL_ROOT>\scripts\computation_record.py" validate --record "<DELIVERY_DIR>\computation-record.json"
```

Validation checks the task, backend decision, candidate implementations, domain, declared range and assumptions, precision, environment, hardware, seeds, datasets, configuration, metrics, fallback reason, verification method and evidence grade, and the paths and SHA-256 hashes of code, result, and locally present configuration and dataset files. It does not rerun the computation, prove the algorithmic claim, or establish that a measured effect has the stated cause.

A `chat` task does not require `computation-record.json` solely because the computation is long or uses a temporary program. Require the record when the user requests reproducible/file delivery, when artifacts are handed off, or under the `high-assurance` profile. Still name the backend and distinguish exact calculation from numerical evidence when that distinction matters.
