# Local Operation Privacy

Use the smallest local content needed for the task.

## Defaults

- Prefer local deterministic inspection, parsing, filtering, validation, and transformation before model reasoning.
- Do not put full local files into context when a path, heading, line number, small excerpt, count, hash, schema shape, or diff summary is enough.
- Query mode defaults to metadata-only. Snippets, expansions, linked metadata, linked bodies, and absolute paths require separate flags or permission.
- When the user explicitly asks to search, recall, compare, or answer from the Vault, use the code-enforced `small_read` package: at most three notes, three sections per note, and 12,000 emitted body characters shared across one response. Do not evade it with a second exact-note call for a linked body.
- Exact-note reads use the current Markdown file and a default 30,000-character response cap.
- Aggregate statistics default to counts and relative groupings only. Use `--include-local-paths` only when the task needs local `vault_root`, `kb_root`, or `db_path`.
- Every content read must return a `permission_scope` and a `read_audit` containing relative paths, headings, emitted counts, and truncation state without duplicating body text.
- A task-scope package may pre-authorize bounded reads or dry-runs. The wrapper enforces note, section, and shared-character limits within each invocation; the agent must preserve the active scope across invocations.
- Do not transmit note contents outside the local workflow unless the user clearly asks for a remote operation.

## Large-Edit Subagent Verification

The standing preference encoded by this Skill authorizes a subagent verifier only for a qualifying large edit as defined in `references/large-edit-verification.md`. This is not permission to broaden the Vault read package. Before transfer, announce that minimized candidate text will be reviewed by a subagent.

Send changed spans with adjacent context and the frozen checklist by default. Send the complete user-authorized style scope only when whole-scope coherence is itself an acceptance gate. Never send linked-note bodies, unrelated sections, attachments, credentials, raw logs, external resources, unmanaged absolute paths, or unrelated frontmatter. The verifier receives no independent Vault access and cannot write to the Vault.

Local indexing does not by itself guarantee that emitted excerpts remain on the device. Once body text is returned to an agent, it may enter that agent's context. Describe this boundary accurately; do not call bounded retrieval end-to-end local privacy.

## Redaction

Automatic redaction is not enabled by default because replacing names, paths, formulas, or source terms can break correctness. When the user explicitly requests redaction and the task supports local restoration, replace sensitive values locally with stable random placeholders such as:

- `PERSON_8f3a91`
- `SECRET_5c9e22`
- `PATH_a812ff`
- `EMAIL_29d41c`
- `HOST_71c09e`

Keep the placeholder map and raw sensitive artifacts local only. Restore placeholders locally after model output is produced.

If redaction would break correctness, say so and ask before exposing the sensitive content.

## Scope Expansion

Permission to inspect one note does not imply permission to read linked-note bodies, folder contents, the whole Vault, browser state, media files, logs, or credentials. Expand scope only when the user explicitly authorizes it or the target file/path is named.
