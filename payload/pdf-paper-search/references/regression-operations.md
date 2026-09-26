## Regression Workflow

When a failure matters, follow this order:

1. explicitly save the failing case to an external draft JSONL with `register_feedback_case.py`, outside the Skill tree
2. accept and independently review the external case before activation
3. compile the active external manifest and validate the corrected case first
4. rerun all protected regressions for a medium or large change when the user approves the budget

Useful commands:

```powershell
python "<SKILL_ROOT>/scripts/doctor_paper_search.py" --cases "<external-cases.jsonl>" --json
```

```powershell
python "<SKILL_ROOT>/scripts/evaluate_paper_search.py" --cases "<external-cases.jsonl>" --json --data-root "<KB_DATA_ROOT>"
```

```powershell
python "<SKILL_ROOT>/scripts/register_feedback_case.py" --cases "<external-draft-cases.jsonl>" --case-file "<CASE_FILE>" --replace --evaluate
```

```powershell
python "<SKILL_ROOT>/scripts/test_query_spec_parity.py"
```

Read [references/improvement-strategy.md](improvement-strategy.md) when deciding whether a fix belongs in corpus coverage, query normalization, page understanding, or scoring.

Failure buckets such as `missing-db`, `empty-db`, `schema-error`, `query-exception`, and `pdf-extraction-error` are environment/tool/corpus failures. Do not count them as ranking failures when deciding whether to tune scorer weights.

If evaluation reports `no-cases`, first fix the benchmark path, filters, or case file. Do not treat an empty regression run as a passing ranking baseline.

