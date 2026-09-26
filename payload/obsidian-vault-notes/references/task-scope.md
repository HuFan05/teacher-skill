# Task Scope Packages

Use a task-scope package when the user wants the agent to perform a bounded Vault workflow without asking for every small expansion step.

Task scopes do not weaken the permission model. They predeclare exact limits for the current task.

## Package Fields

Record these fields before broad work:

- `metadata_queries`: number of allowed metadata-only searches.
- `candidate_expansions`: maximum candidate notes that may be expanded.
- `sections_per_note`: maximum sections per expanded note.
- `max_total_chars`: maximum emitted note text in one wrapper response; the agent also tracks what the active task has already consumed.
- `linked_metadata`: whether linked-note metadata may be shown.
- `linked_bodies`: which linked-note bodies may be read, if any.
- `local_paths`: whether absolute local paths may be shown.
- `dry_run_edits`: whether edit dry-runs may be prepared.
- `write`: whether real files may be changed.
- `commit`: whether a narrow local Git commit may be created.

## Common Packages

Search-only triage:

```text
metadata_queries=1
candidate_expansions=0
linked_metadata=false
linked_bodies=none
local_paths=false
write=false
commit=false
```

Small reading task:

```text
metadata_queries=1
candidate_expansions=3
sections_per_note=3
max_total_chars=12000
linked_metadata=true
linked_bodies=allowed only when selected inside the same three-note response
local_paths=false
write=false
commit=false
```

Use `--read-package small` to enforce this package in `recall_notes.py`. The 12,000-character limit is shared across every expanded note in that response; it is not multiplied by note count. The wrapper is stateless between invocations. Do not reset the package by opening a linked note through a new `--note` call; stop unless the linked body was selected inside the current response or the user grants a separate scope.

Author-voice corpus analysis after the user explicitly names or authorizes a folder or file set:

```text
scope_paths=explicit named folder or enumerated files
include_markdown_only=true
recursive=only when the user includes subfolders or enumeration confirms none exist
excluded_targets=explicit target article and any other named exclusions
linked_metadata=false unless requested
linked_bodies=none
local_paths=false in the answer unless requested
max_total_chars=the enumerated corpus total accepted by the user's scope
dry_run_edits=false
write=false
commit=false
```

Enumerate the scope before reading bodies and freeze the resulting file list. Record file count, total characters, subfolder handling, and exclusions. Direct quotations, copied source prose, code, formulas, production markers, and obvious assistant residue may be read as part of the authorized files but must not automatically become voice evidence. A saved profile produced from the corpus does not keep the original body-read permission alive for later tasks.

Single-note edit planning:

```text
target_note=explicit
whole_note=true if needed
max_total_chars=30000
linked_metadata=true
linked_bodies=none unless named
dry_run_edits=true
write=true when the user's request explicitly names this target and asks to modify, update, or write it back
commit=false until requested
```

Cross-note maintenance planning:

```text
impact_analysis=true
candidate_bodies=none until confirmed
batch_manifest=dry-run then matching guarded write
write=true only after the requested target set has been resolved; no duplicate confirmation is needed when that set was already explicit
commit=false until requested
```

## Reporting

The wrapper must return `permission_scope` and `read_audit` so the limits are machine-checked and visible after retrieval. If the task needs to exceed a limit, stop and ask for broader scope instead of silently expanding it.
