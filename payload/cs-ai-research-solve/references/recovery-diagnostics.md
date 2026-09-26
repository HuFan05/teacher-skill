# Recovery and diagnostics

Read for replay, interrupted publication, long archival workflows or platform/compatibility diagnosis. The main Skill authorization and evidence rules still apply. Read only the relevant commands in [workflows](workflows.md). Preserve the original operation identity; inspection and recovery do not authorize unrelated publication.

## Reopening and diagnosis

- Use `asset` to reopen an exact object. The `replay` command stores outputs and a report in the formal library, so use it only within authorized archival/review work. Before archival authorization, inspect and run code with ordinary computation tools in an external work directory, preserving useful outputs there. For authorized `replay`, use only inspected local code with explicit inputs, command arguments, outputs to preserve, and a suitable timeout. The runner is not an operating-system sandbox.

- For an authorized multi-step archive, review, adoption, or handoff, begin one external `workflow` session before source inspection and keep that same start through commands, review, repairs, and waiting. Attach commands with global `--workflow SESSION_JSON`, record actual phase changes with `workflow checkpoint`, and run the bounded foreground `workflow watch` in a parallel tool process. End the session when the task ends; do not reset it at each command.

- At 20 minutes the session records one reminder with observed phase/command times. Inspect the cause and record the actual diagnosis in a checkpoint note. Watching ends after that reminder or session end. Observation does not choose whether research continues, stops or starts another round; follow the user request and native research flow. It never lowers evidence requirements. A replay timeout is a separate execution bound.

## Current implementation boundary

The implementation supports directory/ZIP adoption of external research material, scoped reuse of large evidence, new contributions and revisions, dependency corrections, interrupted reproduction, and total-task observation. Validate each actual intake and report its evidence and timing boundaries; software checks are not evidence for a research claim or a universal speed guarantee. Replay supervision uses a Windows Job Object or a POSIX process group. Guarded entrance publication (`crs_project.py upgrade --publish`) and the supervised recipient replay route (`replay-delivery`) require Windows; on other platforms candidates can be prepared and inspected, and recipient code must be run under independent supervision. On macOS the OS-owned root aliases `/tmp`, `/var` and `/etc` are accepted as ordinary ancestors; every other link or reparse point is rejected. An uncertain supervisor or system failure preserves its registered workspace for diagnosis. See [the data contract](data-contract.md#external-sources).


## External recovery and recursive coverage

Full reproduction/recovery workspaces and entrance backups stay outside the entire enclosing project. Windows cooperating writers use kernel mutexes so recursive scanning can read every stored file; no locked file is excluded. Resume the same operation and preserve concurrent bytes. A content coverage failure is pending consolidation/diagnosis, not permission to delete evidence, change HEAD, exclude history or publish a success receipt. An indexed ZIP set must retain every volume; verify/import reconstruct only in external owned scratch.
