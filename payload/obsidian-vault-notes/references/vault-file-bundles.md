# Grouped Vault File Deliveries

Use this reference when one task produces two or more related files and the set includes an image, dataset, script, or another non-`.md` file.

## Destination Meaning

- Treat the directory named by the user as the parent directory.
- Create one concise child folder named for the task or artifact.
- Put the complete deliverable set inside that child folder.
- Do not spread the files directly across the named parent directory.
- A request for one file is not changed by this rule.

For example, if the user asks to save a chart, CSV, and reproduction script under `数据/`, use a layout such as:

```text
数据/
└── arXiv机器学习论文统计/
    ├── arxiv_cs_lg_2016_2026.png
    ├── arxiv_cs_lg_2016_2026.csv
    └── reproduce_chart.py
```

## Workflow

1. Resolve the configured Vault root and verify that the named parent is inside it.
2. Choose a short child-folder name from the task. Ask only when different names would imply materially different scope.
3. Assemble every output outside the Vault and finish content checks there.
4. Run `scripts/vault_file_bundle.py` without `--execute` and inspect the JSON plan.
5. Run the same command with `--execute`. On Windows, the executing process must own the
   named Vault parent. If the script reports an owner mismatch, rerun that exact command
   through the sandbox approval mechanism; do not bypass the check or copy the files with
   an ad hoc command.
6. Verify the returned destination, file count, byte sizes, and SHA-256 hashes.
7. Insert or update note links only after the bundle exists.

## Command

```powershell
python "<SKILL_ROOT>\scripts\vault_file_bundle.py" `
  --vault-root "<VAULT_ROOT>" `
  --parent "数据" `
  --bundle-name "arXiv机器学习论文统计" `
  --source "<WORK_DIR>\arxiv_cs_lg_2016_2026.png" `
  --source "<WORK_DIR>\arxiv_cs_lg_2016_2026.csv"

python "<SKILL_ROOT>\scripts\vault_file_bundle.py" `
  --vault-root "<VAULT_ROOT>" `
  --parent "数据" `
  --bundle-name "arXiv机器学习论文统计" `
  --source "<WORK_DIR>\arxiv_cs_lg_2016_2026.png" `
  --source "<WORK_DIR>\arxiv_cs_lg_2016_2026.csv" `
  --execute
```

## Safety Boundaries

- `--parent` must be a relative Vault path.
- `--bundle-name` must be one direct child name, not a path.
- The parent must already exist.
- The final child folder must not already exist.
- Source basenames must be unique.
- Copying is staged and hash-verified before the staging directory is renamed to the final child folder.
- On Windows, `--execute` fails before creating the staging directory when the current
  process SID differs from the Vault parent's owner SID. This prevents a sandbox-owned,
  inheritance-blocked directory from being atomically published into the Vault.
- The command copies source files; it does not delete or move them.
- Reorganizing files from an earlier task requires a separate user request.
