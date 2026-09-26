# Local Workflow

## Paths And Configuration

- Vault: `<VAULT_ROOT>`
- installed Skill: `<SKILL_ROOT>`
- bundled retrieval project: `<SKILL_ROOT>\scripts\obsidian_local_kb`
- local config: the platform user-config directory, or `OBSIDIAN_VAULT_NOTES_CONFIG`
- note index and reports: the platform user-state directory

Run `python "<SKILL_ROOT>\scripts\setup_local.py" configure --vault-root "<VAULT_ROOT>" --build-index --yes` after installation. The scripts honor `OBSIDIAN_VAULT_NOTES_CONFIG`, `OBSIDIAN_VAULT_ROOT`, `OBSIDIAN_LOCAL_KB_ROOT`, and `OBSIDIAN_LOCAL_KB_DB`. Command-line arguments and environment variables override local config; the bundled index module is used when no external module is selected.

## Retrieval

Status without content:

```powershell
python "<SKILL_ROOT>\scripts\recall_notes.py" --status-only
```

Metadata-only query:

```powershell
python "<SKILL_ROOT>\scripts\recall_notes.py" `
  --query "example topic"
```

Bounded content query after an explicit Vault topic request:

```powershell
python "<SKILL_ROOT>\scripts\recall_notes.py" `
  --query "example topic" `
  --read-package small `
  --anchor "Synthetic example note"
```

The `small` package enforces three notes, three sections per note, and 12,000 emitted body characters shared across the response. Do not emulate it by assigning a separate 12,000-character budget to every note.

Read the current exact note:

```powershell
python "<SKILL_ROOT>\scripts\recall_notes.py" `
  --note "Synthetic example note" `
  --whole-note
```

Exact reads use the current Markdown file and default to a 30,000-character total cap. Add `--section "1.2"` to restrict headings or `--no-linked-metadata` to omit direct link metadata.

Use custom expansion only inside an explicitly authorized scope:

```powershell
python "<SKILL_ROOT>\scripts\recall_notes.py" `
  --query "topic" `
  --read-package custom `
  --expand 2 `
  --section-limit 4 `
  --max-total-chars 16000
```

Broad queries refresh stale indexes automatically. For lower-level diagnosis:

```powershell
python -m obsidian_local_kb refresh `
  --vault "<VAULT_ROOT>" `
  --db "<LOCAL_STATE>\vault.sqlite3" `
  --json
```

Use `--no-auto-refresh` only when investigating stale-index behavior. Do not use it for a result presented as complete.

## Single-Note Editing

Run from any directory with an absolute target path. Every operation is a dry-run unless `--write` is passed.

```powershell
python "<SKILL_ROOT>\scripts\vault_edit.py" `
  check --file "C:\path\to\note.md"

python "<SKILL_ROOT>\scripts\vault_edit.py" `
  insert-before-heading `
  --file "C:\path\to\note.md" `
  --heading "## Target" `
  --text-file "C:\path\to\payload.md"

python "<SKILL_ROOT>\scripts\vault_edit.py" `
  replace-section `
  --file "C:\path\to\note.md" `
  --heading "## Target" `
  --text-file "C:\path\to\replacement.md"
```

Other supported operations remain `insert-after-heading`, `prepend-section`, `append-section`, `delete-section`, `rename-heading`, `set-frontmatter`, `add-frontmatter-list-item`, `delete-frontmatter-key`, `replace-text`, and explicit `normalize`.

Inspect the dry-run `pre_sha256`, then pass it back on the write:

```powershell
python "<SKILL_ROOT>\scripts\vault_edit.py" `
  append-section `
  --file "C:\path\to\note.md" `
  --text-file "C:\path\to\payload.md" `
  --expect-sha256 "<dry-run pre_sha256>" `
  --write
```

A successful write reports `applied=true`, distinct `pre_sha256` and `post_sha256` when content changed, and `race_check=passed`. Hash, mtime, UTF-8, frontmatter-complexity, or concurrent-change errors return nonzero.

Non-`normalize` operations preserve bytes outside the target span. `normalize` is intentionally whole-file and reports `semantic_change_possible=true`.

## Cross-Note Work

Analyze first:

```powershell
python "<SKILL_ROOT>\scripts\vault_impact.py" `
  --target-note "快速排序的期望比较次数" `
  --heading "命题1.1"
```

An ambiguous title returns `ambiguous_target`; rerun with the exact relative path. Impact analysis never writes.

Run a reviewed manifest as dry-run, then write with the same preconditions:

```powershell
python "<SKILL_ROOT>\scripts\vault_batch_edit.py" `
  --manifest "C:\path\to\manifest.json"

python "<SKILL_ROOT>\scripts\vault_batch_edit.py" `
  --manifest "C:\path\to\manifest.json" `
  --write
```

Batch output reports `transaction_status`, operation-level `applied_count`, per-file hashes, errors, rollback status, and retained recovery material when needed. `preflight_failed`, `prepare_failed`, `rolled_back`, and `partial_write` are failures, not successful changes.

## Statistics And Verification

```powershell
python "<SKILL_ROOT>\scripts\vault_stats.py" --json
python "<SKILL_ROOT>\scripts\vault_stats.py" --markdown
```

Before editing, inspect `git status --short` and the target's own narrow diff. After editing, run task lint, a narrow diff, and `git diff --check`. Commit only when explicitly authorized; never push.

For Skill maintenance, edit a staging copy and follow `references/skill-maintenance.md`.
