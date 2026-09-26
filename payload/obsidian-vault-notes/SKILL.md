---
name: obsidian-vault-notes
description: Read, retrieve, revise, and Git-version notes in the user's local Obsidian Vault through bounded retrieval, live-file reads, guarded Markdown edits, author-voice calibration, contextual AI-style removal, cross-note impact analysis, and narrow audits, while validating stable KB resource IDs on external file references. Use when the user asks to find related Vault notes, inspect an exact note, connect current work to prior notes, create or update a Vault note, automatically naturalize or comprehensively polish named Vault prose in the author's voice, maintain wikilinks or managed file links, or inspect note history.
version: v1.0.0
---

## Skill Run Observation

<!-- skill-observer:v2 -->
- As the first execution action after selecting this Skill, run `& $env:SKILL_OBSERVER_CLI begin --skill obsidian-vault-notes --quiet --fail-open; & $env:SKILL_OBSERVER_CLI phase set --skill obsidian-vault-notes --phase workflow.startup --quiet --fail-open` when `SKILL_OBSERVER_CLI` is available.
- At each actual workflow transition, append the matching fail-open `phase set --skill obsidian-vault-notes` call to the same PowerShell tool invocation that starts the next business action. Use only `retrieve`, `verify_live`, `plan_change`, `mutate`, `validate`, `version_control`, and `final_response`; do not add a model-tool round trip only to mark a phase.
- When invoking a bundled production script from this Skill, use the matching `scripts/observer_run.py`, `observer_run.ps1`, or `observer_run.mjs` wrapper when that wrapper exists, and record `obsidian-vault-notes.script.run`; wrappers must preserve the child process output and exit code.
- In the final business-tool invocation, set `final_response`; then, before the final response, run `& $env:SKILL_OBSERVER_CLI end --skill obsidian-vault-notes --status success --quiet --fail-open`. Use `failed` or `cancelled` instead of `success` when that outcome is known.
- Observation is fail-open and silent: never ask the user to repair it, never expose routine telemetry in the response, and never let an observer failure block the Skill.
- Do not pass prompts, file contents, tool inputs, tool outputs, secrets, or personal data to the observer. Host stop/session-end hooks, when available, close runs left open by interruption.
- Phase definitions and allowed fields live in [references/observer-data-dictionary.md](references/observer-data-dictionary.md); load it only when instrumenting or analyzing this Skill.

# Obsidian Vault Notes

## Skill Maintenance Note

- Update rationale and maintenance: the Teacher package maintenance manual, docs/MAINTENANCE.md.

## Role

Work against the real local Vault. Use the local index to locate candidates, but read an exact note from its current Markdown file so a stale index cannot replace disk truth. The retrieval wrapper is `scripts/recall_notes.py`; the self-contained index module is `scripts/obsidian_local_kb`. Receiver configuration stays outside the Skill payload.

Invocation routes a task; it does not by itself authorize arbitrary Vault reading. Infer the smallest package that the user's wording already authorizes, enforce that package in the wrapper, and ask only when the task must expand beyond it.

## Authorization Packages

- `no_vault_access`: no search, content read, edit, or Git action.
- `metadata`: candidate titles, relative paths, headings, scores, reasons, index status, read audit, and aggregate statistics; no note body.
- `exact_note`: a named title or path authorizes that note's current body, with a default cap of 30,000 emitted characters. A larger custom cap needs explicit scope. Linked-note bodies remain outside scope.
- `small_read`: an explicit request to search, recall, compare, or answer from the Vault authorizes at most three candidate notes, three selected sections per note, and 12,000 emitted body characters in one retrieval response. Direct linked metadata is allowed. A linked body must be selected inside that same three-note response; otherwise stop instead of opening it through a fresh exact-note call.
- `custom_read`: an explicitly named folder, file set, or higher limit may be read only within the user's stated scope. For author-voice corpus work, enumerate and freeze the Markdown file list before reading, exclude linked bodies by default, and return an aggregate read audit.
- `edit_allowed`: an explicit request to modify, update, write back, or create a clearly identified note authorizes guarded writes to that target. Dry-run and the matching hash-protected write are one workflow and do not require duplicate confirmation.
- `commit_allowed`: create a narrow local commit only when the user explicitly requests commit or versioning. Never push.

Absolute local paths, additional note bodies, broader folders, and commits are separate scope expansions. Tool approval or command success never expands content permission.

Vault content authorization and host sandbox access are different checks. When `edit_allowed` is already active and the exact Vault target is clear, a Vault path outside the current writable roots is not a reason to stop or claim that writing is impossible. Run the read-only preflight normally, then request sandbox escalation for the narrow hash-protected write command. An approval mode such as `approve for me`, automatic review, or `on-request` means the escalation request is reviewed when issued; it is not blanket write access granted in advance. Do not ask the user to authorize the same note edit again. Report a permission blocker only after escalation is unavailable, explicitly rejected, or the escalated retry fails, and identify which of those occurred.

## Route The Request

Chinese trigger summary: explicit `去除AI味` or prose review means `自动改写` inside the user-named style scope; explicit `只检查、不修改` remains read-only.

1. Exact note title, path, or `skill+《note title》`: use `--note`; read the current file and report ambiguity instead of guessing. ^skill-plus-note-marker
2. Explicit Vault topic task: use `--query ... --read-package small`. The wrapper refreshes a stale index locally before broad retrieval unless `--no-auto-refresh` is deliberately used for diagnosis.
3. Search-only triage: use the default `metadata` package and return no body content.
4. Existing-note review: inspect the named note; separate factual validity, source status, technical-correctness status, author voice, and prose/format issues. Do not edit unless requested. A request to remove AI-style writing (`去除AI味`), naturalize, polish, or comprehensively revise authorizes automatic rewriting inside the style scope the user actually named; a request to inspect only does not. If the named target is a whole article or section, revise that complete target. If the user names only a sentence or paragraph, keep the rewrite local except for adjacent text needed to repair the transition. For personal Chinese long-form prose, use the user's saved author profile, when one exists, as a secondary voice guide after meaning, genre, paragraph role, and reliable local prose. The saved profile does not authorize new note reads. For an ordinary content edit, automatically improve only the changed span, its adjacent paragraphs, and necessary bridge sentences. When the user asks only for a compressed replacement, continuation, or bridge, return the smallest reusable draft that fulfils the identified paragraph role and requested length; do not add generic background or a broader explanation unless it is needed for the draft or explicitly requested.
5. New long-form draft with explicit `$obsidian-vault-notes`: create it under `笔记草稿\` unless the user names another Vault path. This does not authorize broad retrieval.
6. Existing-note edit: inspect the current target and its narrow Git diff, prepare an `EditPlan` for non-mechanical work, apply a guarded local edit, run checks, and return an `EditAudit`. For a compression or local bridge, the plan must state the target paragraph's role and the intended length, and the edit must not add material outside that function. For a long Chinese article, classify the genre and record the voice basis, then audit the whole active style scope sentence by sentence after drafting; do not limit prose review to inserted lines. Freeze facts, numbers, formulas, links, quotations, source labels, proper names, negation, causality, chronology, and epistemic strength before rewriting. Run the prose lint before and after the rewrite, then cold-read the final scope once more for new uniform cadence, stacked punchlines, voice drift, lost concrete evidence, or broken transitions. Do not ask for sentence-by-sentence confirmation when automatic prose rewriting was requested; retain and audit only sentences whose safe rewrite would require changing facts, technical claims, source status, or the author's actual position.
   If the write is denied because the Vault lies outside the active sandbox, rerun that exact scoped write through the platform's approval mechanism. Do not replace an available escalation request with a prose claim that the agent has no permission.
   Before writing, compare the current and prospective external-file references as defined in [references/resource-references.md](references/resource-references.md). New or changed references require valid adjacent resource IDs. After a successful write, synchronize the final disk text by `post_sha256`; report `reference_sync_pending` instead of complete success when that cache update fails.
   When the requested work qualifies as a large edit, stop before the live write and follow [references/large-edit-verification.md](references/large-edit-verification.md). A single paragraph, a few formula lines, one local subsection, or a narrow heading/link repair does not qualify. A qualifying candidate must pass local deterministic checks and undergo up to three independent subagent reviews. A `PASS` makes the current candidate eligible immediately; after three completed `FAIL` rounds, the third-round candidate becomes `accepted_after_three_reviews` and is also eligible, with every unresolved finding preserved in the audit.
7. Cross-note maintenance: analyze impact first, resolve ambiguous titles, review an explicit manifest, then use the transactional batch editor only when writing is authorized.
8. Statistics: use `vault_stats.py`; report aggregate counts and relative groupings only.
9. Computation needed for a CS/AI note: when the user explicitly requests external-tool, code-execution, or MCP verification, or when the result depends on executed code, an experiment, a benchmark, or symbolic or numerical computation, loading `$cs-ai-computation` for that exact computation is mandatory and must not be replaced by ad hoc local scripts. Keep the current Vault authorization package here and pass only the minimum definitions, assumptions, and excerpt needed. That Skill does not receive independent Vault access. Bring its result and computation record back through this Skill's guarded write workflow when the user authorized a write.
10. Grouped file delivery: when the user asks to save two or more related files under a named Vault directory and the set includes an image, dataset, script, or any other non-Markdown file, treat the named directory as the parent. Create one concise task-named child folder and place the complete set inside it. Do not scatter the files directly in the named parent. Use `scripts/vault_file_bundle.py` for preflight and copying. A single-file request is unaffected. If the child folder already exists or any destination name collides, stop instead of overwriting.
    On Windows, `--execute` must run under the same owner SID as the Vault parent. An owner mismatch is a hard pre-publication failure: rerun the exact command through the sandbox approval mechanism instead of bypassing the script. This gate prevents a sandbox-owned directory from being renamed into the Vault with permissions that make Obsidian fail on `scandir`.
11. Paper and PDF-library search, stable resource IDs, and durable research records are handled by their specialist skills. This skill passes bounded context and writes approved results back to the Vault.

## Default Commands

```powershell
python "<SKILL_ROOT>\scripts\recall_notes.py" --status-only
python "<SKILL_ROOT>\scripts\recall_notes.py" --note "Synthetic example note" --whole-note
python "<SKILL_ROOT>\scripts\recall_notes.py" --query "example topic" --read-package small
python "<SKILL_ROOT>\scripts\validate_obsidian_formulas.py" "<VAULT_NOTE.md>" --live-mode off
python "<SKILL_ROOT>\scripts\validate_obsidian_links.py" "<VAULT_NOTE.md>" --vault-root "<VAULT_ROOT>" --live-mode off
```

When a downstream workflow needs a standalone byte-exact Markdown artifact, save the JSON from the exact-note `--whole-note` call and run `python scripts/extract_recall_markdown.py --recall-json recall.json --vault-root "<VAULT_ROOT>" --expected-note "relative/path.md" --output note.md`. The extractor accepts only one live-disk, whole-note, untruncated recall target, rejects path escape and source overwrite, then atomically copies the current Vault file bytes. The recall response authorizes and resolves the target; the final reread remains current disk truth and includes frontmatter, empty headings, and original heading syntax.

Use `setup_local.py` to configure or check the receiver's Vault and local index. Use `vault_edit.py` for deterministic single-note operations, `vault_impact.py` before cross-note work, `vault_batch_edit.py` for reviewed transactional manifests, `vault_stats.py` for aggregate statistics, `validate_obsidian_formulas.py` for the mandatory post-write formula gate, and `validate_obsidian_links.py` for the mandatory wikilink gate. Commands, JSON fields, and recovery behavior live in the task-specific references below.

## Reference Loading

- permissions and packages: [references/permissions.md](references/permissions.md), [references/task-scope.md](references/task-scope.md)
- retrieval, live reads, refresh, and JSON v2: [references/retrieval-protocol.md](references/retrieval-protocol.md), [references/local-workflow.md](references/local-workflow.md)
- single-note editing and audits: [references/editing-protocol.md](references/editing-protocol.md)
- large-edit classification, independent subagent review, and three-round escalation: [references/large-edit-verification.md](references/large-edit-verification.md)
- cross-note impact and transactional batches: [references/cross-note-maintenance.md](references/cross-note-maintenance.md)
- Git and restore: [references/git-workflow.md](references/git-workflow.md)
- article prose and full-scope Chinese review: [references/article-revision-style.md](references/article-revision-style.md); for the user's personal Chinese long-form prose, also load [references/author-voice-profile.md](references/author-voice-profile.md); when automatic naturalization or AI-style removal applies, also load [references/ai-style-rewrite.md](references/ai-style-rewrite.md); use `--style-profile chinese-longform` only when that review applies
- CS/AI notes (algorithms, complexity statements, correctness arguments, experiment results, code, formulas) and computation handoff: [references/cs-ai-note-style.md](references/cs-ai-note-style.md) and `$cs-ai-computation`
- sources: [references/source-citation-style.md](references/source-citation-style.md)
- image repair and media handoff: [references/image-embed-repair.md](references/image-embed-repair.md), [references/media-insertion.md](references/media-insertion.md)
- grouped file deliveries: [references/vault-file-bundles.md](references/vault-file-bundles.md)
- privacy: [references/privacy.md](references/privacy.md)
- Observer workflow phases and script-stage meanings: [references/observer-data-dictionary.md](references/observer-data-dictionary.md); load it only when instrumenting or analyzing this Skill
- Skill maintenance: [references/skill-maintenance.md](references/skill-maintenance.md)
- external knowledge-root files and stable IDs: [references/resource-references.md](references/resource-references.md) and `$manage-personal-knowledge`

## Retrieval return boundary

`recall_notes.py` checks the complete serialized response (including metadata and errors) before returning it. The default is 64,000 characters; `--max-response-chars` accepts 1,024..256,000. Prefer fewer notes, sections and metadata before requesting a larger envelope justified by the current task. Body limits remain separate. `response_chars` includes the final newline. Oversized results return no content and a scope-refinement hint; never read raw provider output as a fallback.

`--debug` is accepted but does not return raw errors, commands or paths. Failure responses use fixed categories and do not perform another index read. This is a per-response boundary for this wrapper, not cumulative task enforcement or host-wide isolation. The agent must preserve the task's remaining read scope across calls and stop when evidence is sufficient. Direct shell/provider access and other scripts are outside this wrapper guarantee; use their operation-specific boundaries.

## Hard Invariants

- Exact-note body text comes from the current Vault file; stale indexed content is never presented as current truth.
- All body text emitted by one retrieval call consumes one shared response budget. The agent must keep the active task's remaining scope when deciding whether another call is allowed. Whole-note mode preserves line breaks, emits one copy of content, and reports truncation accurately.
- Broad query refresh is local and reports added, modified, and deleted counts. A refresh failure leaves the previous index usable and makes the query failure visible.
- Path precedence is command-line override, environment variable, local config, then the bundled index module and the platform user-state default. Local config, index databases, reports, and backups never belong in the distributed Skill payload.
- Read audits contain relative paths, headings, counts, and truncation state, not duplicate note bodies.
- Non-`normalize` edits preserve bytes outside the requested span, including BOM, line endings, Markdown hard breaks, media, wikilinks, block ids, and user-owned dirty hunks.
- Complex frontmatter is rejected by mechanical property operations instead of being guessed.
- Hash or mtime mismatch, concurrent file change, ambiguous note resolution, invalid numeric limits, and partial rollback are hard errors with nonzero exit status.
- Batch writes preflight every operation before the first replacement; a write-stage failure triggers hash-guarded rollback and reports whether rollback was complete.
- A multi-file deliverable set that includes any non-Markdown file is written only through a task-named child folder under the user-specified Vault directory. The bundle preflight must reject path escape, duplicate names, an existing destination, and partial overwrite.
- Current Markdown is the source of truth for resource references. A new or changed knowledge-root file link with no valid adjacent `KB-...` marker, an unknown ID, or a missing resource is a pre-write hard error. Existing unmanaged links may remain when unrelated text changes, but the audit must report them.
- If the resource harness is unavailable, edits with no external-reference delta may proceed. Any edit that adds, removes, or changes an external reference must stop. A post-write synchronization failure is `reference_sync_pending`, not a fully successful edit.
- After every successful Markdown write, run `validate_obsidian_formulas.py --live-mode off` against the final disk file, even when no formula was intentionally changed. The mandatory offline gate rejects unsupported delimiters, unclosed spans, empty formulas, and unbalanced TeX groups; `vault_edit.py` also rejects newly introduced control-word shadows that indicate a transport layer may have removed a backslash, while the validator reports any pre-existing shadows in `transport_audit`. TeX-bearing edit payloads must follow the raw/literal transport and staged-byte reread rules in `references/cs-ai-note-style.md` because final TeX cannot reveal every uniquely deleted backslash. Repair failures through the guarded edit flow and rerun until `ok=true`. Live Obsidian MathJax rendering is an additional application-state check, not the default content-correctness gate; use `--live-mode required` only when the user explicitly asks for validation inside the currently running Obsidian app.
- After every successful Markdown write, run `validate_obsidian_links.py --live-mode off` against the final disk file. The mandatory filesystem gate rejects a missing or ambiguous target, a missing heading, and a missing block anchor, including links added or changed by the edit. Use `--live-mode required` only when the user explicitly asks the running Obsidian app to resolve the same targets through its metadata cache. Never describe an offline pass as live application verification.
- In a Markdown table cell, escape the separator of every Obsidian wikilink or image modifier as `\|`: write `[[target\|alias]]` and `![[image.png\|300]]`, not their unescaped forms. An unescaped separator is parsed as a table column boundary, so a filesystem-resolvable target can still render as broken text or a missing image. Check the final Markdown source as well as link-target resolution.
- Obsidian CLI unavailability is not permission to launch, inspect, click through, or configure Obsidian with computer use or other GUI automation. Report `live_check_unavailable` when live verification was explicitly requested; otherwise complete the task from the mandatory offline gates. The user must explicitly request GUI interaction before any such recovery action.
- A sandbox denial is a signal to issue the narrow platform approval request when the user has already authorized the edit; it is not evidence that the task lacks Vault content permission.
- Do not rebuild a dirty target from `HEAD`, an index copy, or an old staging file. Do not reset the Vault. Do not push.
- `large_edit_verification_gate` applies only to a qualifying large edit, never merely because one paragraph, a few formula lines, one local subsection, or a narrow heading/link repair changed. A qualifying candidate cannot reach the live Vault until its exact candidate hash either receives `PASS` or completes three hash-bound `FAIL` rounds. The latter state is `accepted_after_three_reviews`; it is an explicit fallback and must never be reported as verifier approval.

## Formatting And Verification

- Preserve local note voice, structure, frontmatter, callouts, numbering, wikilinks, embeds, highlights, and source boundaries.
- Treat frequency as evidence to inspect, not an instruction to imitate. Separate stable cross-note voice from genre-only wording, old errors, production residue, copied material, and probable assistant expansion.
- Automatic prose rewriting is contextual agent work, not regex replacement. A single word, punctuation mark, passive sentence, three-item list, or long sentence never requires rewriting by itself.
- Never invent a fact, date, statistic, source, quotation, or motive to replace vague prose. Preserve concrete personal detail, genuine uncertainty, intentional self-correction, exact technical language, and meaningful irregularity.
- New or rewritten notes do not repeat the filename as a first-level body heading. Use short wikilink aliases when needed.
- For nontrivial CS/AI claims, source results that are not widely known and label uncertain correctness arguments, complexity bounds, or experiment results honestly; a run that completed or tests that passed verify only the boundary they checked.
- After writing, run the mandatory offline formula and wikilink validators, task-specific lint, a narrow diff, `git diff --check`, and an `EditAudit`. If either offline validator fails, repair the note and rerun it before any success response. Include formula counts, offline backend status, and any explicitly requested live result; include the wikilink validator's checked-link count, issue list, filesystem result, and any explicitly requested live-cache result or unavailable reason. Also include resource references added, removed, retained, unknown, missing, unmanaged, and synchronization status. For a long Chinese article, distinguish sentence-level cold review from lint output; a clean lint alone does not establish natural prose. If no commit was authorized, report the changes as uncommitted.
- When changing this Skill, stage first, follow [references/skill-maintenance.md](references/skill-maintenance.md), and ask separately before updating the maintenance manual.

## Canonical terminology

Read [canonical terminology](references/terminology.md) before changing a persistent object, schema, lifecycle, authority/evidence rule, stable interface, specialized behavior term, or hash-bound identity. Do not introduce synonyms, rename canonical terms, change constitutive fields, or reuse deprecated/reserved names without updating the terminology registry, version history, migration rule, and validator first.

`bounded_retrieval` means a planned, minimal search-and-read scope inside the Obsidian Vault; `guarded_edit_receipt` means the evidence that an Obsidian Markdown edit applied to the expected live bytes and passed narrow validation; `large_edit_verification_gate` means the hash-bound independent review required only before a qualifying large-edit candidate may be written to the live Vault. These core distinctions are mandatory; the linked glossary is normative.

## Owned operation return boundary

The editor, batch editor, impact report, file bundle, statistics, formula/link validators and exact Markdown extractor bound complete public CLI responses to 65,536 UTF-8 bytes by default. Raw diagnostics are replaced by fixed categories. Source selection and authorization remain operation-specific; this is not a restriction on direct Python or arbitrary shell access.

Use `--max-response-bytes` (4096..1048576), `--details-limit` (1..200) and `--details-offset` for a justified evidence gap. Select one collection with `--details-path` using a path from `detail_coverage`; later pages require that exact collection path. Write/execute commands reject collection-path or offset requests before mutation; use the saved audit for later views. Above-default byte/detail limits or later pages require `--response-reason`. Inspect `response_complete`, `details_complete` and collection coverage; omission never proves that no problem exists or that a write was rolled back.

When detail is omitted, a hash-bound full operation audit may be retained in the local temporary directory. Use its returned `local_audit.read_arguments` through the shared helper to read selected details without repeating the original operation. Raw diagnostics in that local audit must not be dumped into model context. Keep the audit until required recovery evidence is safe; the temporary artifact is not a formal Vault note or permission to read unrelated sources.

Preserve `applied`, `transaction_status`, `race_check`, pre/post hashes and reference synchronization status. If the outcome is unknown, inspect the named operation or final artifact before any retry. Never repeat a write or grouped publication merely to obtain another output page. Exact extracted Markdown remains complete in its requested local artifact; the response guard applies to its receipt.

Impact `source_coverage` describes producer-side limits; suggested-read counts are lower bounds when the source search reaches its limit. `detail_coverage` describes the additional return window. Neither proves that all relevant Vault evidence has been found. Resource audit `available_detail_paths` lists justified saved-audit expansion targets; rollback synchronization retains its own status independently from forward synchronization.

The editor emits derived `terminal_state`: `success` means its requested operation completed (inspect `write`/`applied` to distinguish a dry run); `blocked` requires an explicit failed prewrite hash, mtime or race guard; other errors remain `unresolved`. An already applied edit with pending reference synchronization must never be classified as blocked or fully successful.
