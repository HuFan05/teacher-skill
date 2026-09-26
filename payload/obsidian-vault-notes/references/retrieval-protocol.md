# Retrieval Protocol

Use `scripts/recall_notes.py` before raw Vault searches. It returns JSON schema v2, reads exact-note bodies from current Markdown, and enforces bounded packages.

## Modes And Packages

```powershell
python "<SKILL_ROOT>\scripts\recall_notes.py" --status-only
python "<SKILL_ROOT>\scripts\recall_notes.py" --query "query"
python "<SKILL_ROOT>\scripts\recall_notes.py" --query "query" --read-package small
python "<SKILL_ROOT>\scripts\recall_notes.py" --note "note title" --whole-note
```

- `metadata`: default query package; returns candidates and audits without body text.
- `small`: for an explicitly authorized Vault topic task; expands at most three notes, three sections per note, with 12,000 body characters shared across the response.
- `custom`: use explicit `--expand`, `--section-limit`, and `--max-total-chars` values inside a user-authorized scope.
- Exact `--note` reads the current file and defaults to a 30,000-character total cap. `--whole-note` preserves line breaks and returns no duplicate preview body.

All numeric limits must be nonnegative. The wrapper rejects a `small` package that requests more than its fixed note, section, or character limits.

## Index Refresh

Before a broad query, the wrapper checks index freshness. If stale, it runs the local lower-level `refresh` command and reports its result in `index_refresh`.

- Use `--no-auto-refresh` only for diagnosis. A stale query run with this flag must remain visibly stale.
- Exact-note content does not require refresh because the wrapper reads the current Markdown after resolving its path.
- A refresh failure is a hard query error. The previous index remains available for later recovery, but the failed command must not present its result as complete.
- A missing or old-schema database triggers an atomic full rebuild; later refreshes update added, modified, and deleted notes in a SQLite transaction.

## Explicit Author-Voice Corpora

Use `custom_read` when the user explicitly authorizes a named folder or file set for author-voice analysis and the scope exceeds the `small` package. Before reading bodies:

1. enumerate the current Markdown files inside the exact scope;
2. record exclusions, recursion, file count, and total characters;
3. freeze the list and confirm that linked bodies remain outside scope.

When `recall_notes.py` cannot express the authorized folder as one bounded response, direct current-file reads are allowed for that frozen list. Do not broaden the folder, follow wikilinks, reuse an index snippet as current prose, or expose absolute paths in the answer unless requested. Return an aggregate read audit and treat the resulting voice profile as distilled guidance, not continuing permission to reopen the corpus.

## Content And Link Flags

- `--whole-note`: emit `whole_note_sections` only.
- Preview mode: emit `selected_sections` only.
- `--section "heading"`: restrict an exact-note read to matching heading paths.
- `--include-links`: include linked-note metadata for expanded query notes.
- Direct exact-note reads include direct linked metadata unless `--no-linked-metadata` is passed.
- `--include-local-paths`: expose absolute paths only when local-file inspection or editing has been authorized.
- `--include-snippets`: include index snippets in candidate metadata; it does not authorize note bodies.

Linked metadata never contains linked-note bodies. In `small`, a linked body must be chosen as one of the expanded notes in the same response. The wrapper does not persist a budget ledger between processes, so the agent must stop instead of reopening the linked note through a fresh exact-note allowance.

## JSON Schema V2

Every successful response includes:

- `schema_version`: `2`;
- `mode` and `content_mode`;
- `permission_scope`: active package and enforced limits;
- `index_status` and `index_refresh`;
- `retrieval_log`;
- `note_candidates` and `expanded_notes` when applicable;
- `read_audit`: relative paths, selected headings, emitted characters, and truncation state, without duplicate body text;
- top-level `emitted_chars` and `truncated`.

Body-bearing section items preserve Markdown line breaks and report their own `source_chars`, `emitted_chars`, and `truncated` values. The sum of emitted body characters across every expanded note must not exceed the active shared budget.

Handled failures return `ok=false`, fixed `error`/`error_type` categories, zero emitted body characters, an unchecked index-status indicator and a recovery hint. They do not echo the request or exception, and do not run another index query. Invalid limits, ambiguous note resolution, refresh failure, missing current files, and malformed UTF-8 are failures rather than empty successful results.

## Interpretation

- A stale index makes broad retrieval incomplete; auto-refresh or stop.
- An index miss is not proof that a note does not exist. Exact current-file resolution may still find a unique title or relative path.
- `truncated=false` means neither a section nor the shared response budget cut emitted body text.
- Retrieval scores are explainable components: lexical rank, exact path/title, alias, tag, heading, anchor, and link-neighborhood contributions.
- Absolute paths, raw commands, and debug output stay hidden unless explicitly requested.

## Whole-response and error limits

The public CLI buffers JSON before return. `--max-response-chars` limits the complete envelope and final newline (default 64000, range 1024..256000), with `response_chars` reporting the measured size. Body budgets apply separately. On overflow, no source content is returned; narrow notes, sections or metadata, or use a justified larger envelope within the existing task authorization. This permits necessary exact evidence and broader asset discovery without silently reading everything.

`--debug` does not expose raw diagnostics. Unknown errors are classified without echoing exception strings, request values or provider output. Consumers should branch on the stable category and coverage, not on raw exception text. This error-detail minimization is intentional privacy hardening. No raw diagnostic file is created.

This limit applies to one `recall_notes.py` response only. It does not establish a persistent cumulative ledger, constrain arbitrary shell/provider calls or prove semantic relevance. Preserve the active task budget across retries and expansion in the calling workflow; other scripts require separate operation review.
