# Git Workflow

Use this reference whenever the host agent creates, edits, restores, or explains changes to notes in the local Obsidian Vault.

## Expected Setup

Vault root:

```text
<VAULT_ROOT>
```

Local Git repository:

```text
<VAULT_ROOT>\.git
```

Obsidian Git plugin:

```text
<VAULT_ROOT>\.obsidian\plugins\obsidian-git
```

Recommended plugin policy:

```text
autoSaveInterval: 30
autoPushInterval: 0
autoPullInterval: 0
autoPullOnBoot: false
disablePush: true
autoBackupAfterFileChange: true
```

Meaning: local automatic commits are enabled; push and pull are disabled.

## Agent Editing Protocol

Before editing:

```powershell
Set-Location -LiteralPath "<VAULT_ROOT>"
git status --short
```

If the target file already has uncommitted changes, inspect the relevant diff before editing:

```powershell
git diff -- "relative/path/to/note.md"
```

Do not commit unrelated dirty files. If unrelated files are dirty, leave them alone.

After editing, always inspect a narrow diff:

```powershell
git diff -- "relative/path/to/note.md"
```

Create a commit only when the user explicitly asks for commit/versioning, or when the active task has clearly authorized `commit_allowed`. Add only touched files:

```powershell
git add -- "relative/path/to/note.md"
git commit -m "note: explain the change purpose"
```

For multiple files touched by the same task, add only those files:

```powershell
git add -- "path/one.md" "path/two.md"
git commit -m "note: explain the related change"
```

Do not push. If a commit was not authorized, leave changes unstaged and report that they are uncommitted.

## Commit Message Style

Use short messages that say what changed and why:

```text
note: add local Git workflow guidance
algo: refine quicksort invariant notes
draft: create article draft on AI-assisted learning
fix: correct broken wikilinks in Transformer note
```

If the user's reason is known, include it in the message or in a short commit body:

```powershell
git commit -m "ml: clarify Adam optimizer assumptions" -m "Motivation: make the note safer to reuse during paper searches."
```

## Restore Guidance

Prefer file-level restore over whole-repo reset.

Inspect history for one file:

```powershell
git log -- "relative/path/to/note.md"
```

Restore one file from an older commit:

```powershell
git restore --source <commit> -- "relative/path/to/note.md"
git commit -m "restore: recover note from earlier snapshot"
```

Avoid these unless explicitly requested and carefully inspected:

```powershell
git reset --hard
git restore .
git clean -fd
```

## Fallback Backup Rule

Use file backups under:

```text
<VAULT_ROOT>\笔记草稿\_note_backups
```

only when Git is unavailable, unsafe for the current operation, or the user explicitly asks for a separate rollback copy.
