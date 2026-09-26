# Permissions

This skill may be invoked implicitly for routing, but invocation is not consent to read the Vault. Choose the narrowest permission state that satisfies the request.

## States

- `no_vault_access`: no local Vault search, note read, linked-note inspection, edit, or Git action. Use this when the user asks a general question without naming this skill, a Vault note, or local retrieval.
- `metadata`: local index query and aggregate Vault statistics are allowed, but output is limited to candidate metadata, read audits, and aggregate counts. No snippets, note bodies, linked-note bodies, or absolute paths.
- `exact_note`: one explicitly named title or path may be read from its current Markdown, up to the package cap.
- `small_read`: an explicitly authorized Vault topic task may expand up to three notes and three sections per note under one shared 12,000-character body budget.
- `custom_read`: user-approved limits may be enforced for a task that does not fit `exact_note` or `small_read`. A named author-voice corpus must use an enumerated, frozen Markdown file set; links inside those files do not join the scope automatically.
- `linked_content_read`: linked-note bodies may be read only after the user authorizes the specific linked notes or a clearly bounded linked-note scope.
- `edit_allowed`: real Vault Markdown may be changed only after the target file or draft path is clear.
- `commit_allowed`: a narrow local Git commit may be created only when the user explicitly asks for commit/versioning or the active task explicitly includes committing. Never push.

## Content Authorization And Sandbox Access

These are separate layers:

- The authorization package answers what Vault content the user allowed the task to read or change.
- The host sandbox answers whether the current tool call may access that filesystem path directly.
- The platform approval mechanism may grant one narrow command access outside the current writable roots. It does not enlarge the note, folder, or commit scope.

When `edit_allowed` is active and the exact target is known, do not interpret an out-of-sandbox Vault path as missing user authorization. Complete the dry-run and safety checks, then issue the platform approval request for the exact guarded write. In `approve for me`, automatic-review, or `on-request` modes, approval is evaluated only after the agent issues that request; the agent must not wait for blanket access to appear.

If an un-escalated command fails with a sandbox or access error, retry the same scoped operation using the platform's escalation field and a concise justification. Do not ask the user to repeat the edit instruction. Stop and report a permission blocker only when escalation is unavailable, explicitly rejected, or the escalated retry also fails. State the failed layer and preserve the dry-run hash so the task can resume safely.

## Task Scope Packages

For multi-step work, define a bounded task package instead of asking at every small step. Use `references/task-scope.md` for package fields.

A package may allow a fixed number of metadata queries, candidate expansions, section reads, dry-run edits, or impact-analysis passes. It must still state whether linked-note bodies, absolute local paths, real writes, and commits are allowed. If a needed action exceeds the package, stop and ask for a broader scope.

## Routing Rules

- Exact note title or path grants `exact_note` for that target note only, with a default 30,000-character task cap.
- A topic query grants no Vault access unless the user explicitly asks to search/recall/connect Vault notes, names this skill, or otherwise clearly authorizes local note search.
- A request to write a new long-form draft with explicit `$obsidian-vault-notes` grants `edit_allowed` for a new draft under `笔记草稿\`, but not broad Vault search.
- An explicit request to search, recall, compare, or answer from the Vault grants the `small_read` package: three notes, three sections per note, and 12,000 emitted body characters shared across one wrapper response. A linked body may not obtain a fresh exact-note allowance unless the user separately authorizes it.
- Direct linked-note metadata may be shown during an authorized note inspection. Linked bodies must fit the active package or receive separate authorization.
- Aggregate statistics may scan Markdown files locally to count characters, images, and links, but the answer must not expose note body text. Relative paths are allowed only for top-list reporting and debugging the aggregate result.
- An explicit request to read a named folder or file set for author-voice analysis grants `custom_read` only for the enumerated Markdown files in that scope. Report the file count and total size before body analysis, do not follow links, exclude the target article when requested, and keep writes and commits disabled unless separately authorized.
- Whole-note reading requires explicit `content_read` for that note and uses one shared total cap. It must preserve line breaks, emit one copy of the body, and report truncation accurately.
- Cross-note impact analysis may return candidate metadata and short line snippets for triage. Reading full candidate bodies or batch-writing still needs explicit scope.
- Sandbox approval, command success, or automatic tool review never expands note scope.
- Conversely, a sandbox denial does not revoke an already active `edit_allowed` package; use the approval mechanism before declaring the write blocked.

## When To Ask

Ask before moving to a broader state when:

- a query needs to exceed the active note, section, or shared-character limit;
- linked metadata suggests adjacent notes are relevant but their bodies were not authorized;
- an edit would touch large numbered sections, external references, or many linked notes;
- redaction would break correctness and sensitive content would need to be exposed.

Do not ask when the requested scope is already explicit, for example an exact note review, a named section inspection, or a draft path creation.
