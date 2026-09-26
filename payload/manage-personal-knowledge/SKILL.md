---
name: manage-personal-knowledge
description: Configure and coordinate a movable local personal-knowledge root containing an Obsidian Vault, federated structured question collections, and a PDF library, and resolve stable KB resource IDs for long-lived files outside the Vault. Use when the host agent needs to configure or relink the root, discover, import, audit, or search question collections, register, resolve, audit, move, or retire a KB resource, repair file references in notes, search ordinary PDF content with page evidence, locate where an algorithm, theorem, definition, result, table, figure, or claim is stated in local papers, read a named PDF, combine Vault and library retrieval, profile search latency or compare controller models, write approved findings back through obsidian-vault-notes, or verify the local whole-PDF OCR toolchain.
version: v1.0.0
---

## Skill Run Observation

<!-- skill-observer:v2 -->
- As the first execution action after selecting this Skill, run `& $env:SKILL_OBSERVER_CLI begin --skill manage-personal-knowledge --quiet --fail-open; & $env:SKILL_OBSERVER_CLI phase set --skill manage-personal-knowledge --phase workflow.startup --quiet --fail-open` when `SKILL_OBSERVER_CLI` is available.
- At each actual workflow transition, append the matching fail-open `phase set --skill manage-personal-knowledge` call to the same PowerShell tool invocation that starts the next business action. Use only `retrieve`, `verify_live`, `plan_change`, `mutate`, `validate`, `version_control`, and `final_response`; do not add a model-tool round trip only to mark a phase.
- When invoking a bundled production script from this Skill, use the matching `scripts/observer_run.py`, `observer_run.ps1`, or `observer_run.mjs` wrapper when that wrapper exists, and record the matching production-entry phase from `references/observer-phases.json`; use `manage-personal-knowledge.script.run` only when the target entry is not listed. Wrappers must preserve the child process output and exit code.
- In the final business-tool invocation, set `final_response`; then, before the final response, run `& $env:SKILL_OBSERVER_CLI end --skill manage-personal-knowledge --status success --quiet --fail-open`. Use `failed` or `cancelled` instead of `success` when that outcome is known.
- Observation is fail-open and silent: never ask the user to repair it, never expose routine telemetry in the response, and never let an observer failure block the Skill.
- Do not pass prompts, file contents, tool inputs, tool outputs, secrets, or personal data to the observer. Host stop/session-end hooks, when available, close runs left open by interruption.
- Phase definitions and allowed fields live in [references/observer-data-dictionary.md](references/observer-data-dictionary.md); load it only when instrumenting or analyzing this Skill.

# Manage Personal Knowledge

## Role

Act as the single entry point for the user's local knowledge root. Keep source ownership explicit:

- route Vault note and organized-domain-record work to `$obsidian-vault-notes`
- route layout-sensitive reading of a named PDF to `$pdf`
- route locating where an algorithm, theorem, definition, result, table, figure, or claim is stated in local papers to `$pdf-paper-search`
- route a genuinely needed experiment, training/evaluation run, profiling measurement, or symbolic/solver/formal check on retrieved evidence to `$cs-ai-computation`
- use the bundled CLI for knowledge-root configuration and general PDF-library indexing/search
- use the bundled registry commands for files inside the knowledge root but outside the Vault
- use OCR commands only when the user explicitly names a PDF for OCR; OCR never runs during indexing or search

Do not copy these specialist workflows into this Skill. Do not treat invocation as permission to read arbitrary Vault bodies or every PDF body.

## Local Command

```powershell
python "<SKILL_ROOT>\scripts\manage_kb.py" <command> [options]
```

All commands emit UTF-8 JSON when `--json` is available. Read [references/configuration.md](references/configuration.md) before setup, relinking, forgetting a source, or diagnosing a missing path. Read [references/question-database.md](references/question-database.md) before creating, refreshing, or searching the structured question index. Read [references/resource-registry.md](references/resource-registry.md) before registering, resolving, auditing, moving, retiring, restoring, or rewriting references for a resource. Read [references/pdf-library.md](references/pdf-library.md) before indexing, searching, paper-location handoff, or OCR. Read [references/search-evaluation-contract.md](references/search-evaluation-contract.md) before creating or extending a search dataset or comparing baseline and candidate retrieval behavior. Read [references/writeback-handoff.md](references/writeback-handoff.md) before coordinating a finding into a Vault note.

For latency diagnosis, harness benchmarking, or model A/B tests, read [references/performance-benchmark.md](references/performance-benchmark.md) and use `scripts/benchmark_search.py`. Keep local script timing separate from complete host-agent timing; the latter includes model, network, tool dispatch, and process overhead.

## First Run

1. Run `python "<SKILL_ROOT>\scripts\setup_local.py" check`. If it returns `setup_required`, ask the user for the local knowledge-root location; never guess it from an old path or a unique-looking candidate.
2. Run `setup_local.py setup` interactively, or pass `--knowledge-root`, `--vault`, and `--library` together for a reviewed non-interactive setup. The root must contain one Obsidian Vault and a separate, non-nested PDF library.
3. Show the proposed Vault and PDF-library paths, their evidence, and PDF counts before confirmation. A unique candidate is still evidence, not permission to persist it.
4. Ask separately whether to build the Vault and PDF indexes. Use `--build-vault-index` and `--build-pdf-index` only for choices the user accepted. PDF indexing commits document by document and can resume after interruption.
5. Run `status --json` after setup. Extraction errors are accounted states and are retried only with the explicit `--retry-errors` option or after the file changes.
6. Return `registry-init --json` only as a next action. During Skill installation, never create `.mpk`, initialize the registry, scan the real knowledge root, or assign `KB-...` IDs. Later, only after the user accepts the exact preview may the matching `--write --expect-plan-sha256 ...` command initialize it.

Configuration belongs under the platform config directory; indexes and tool state belong under the platform state directory. Never put receiver configuration, SQLite databases, OCR models, caches, or reports inside the distributed Skill.

## Route Requests

### Question search

When the user asks to find a problem or question without naming a search range, use the federated structured-question interface as the first retrieval tier:

1. Run `question-status --json` when current whole-corpus coverage matters. Its top-level `coverage_complete` is authoritative; a source adapter's local completeness is not.
2. Run `question-search --query "..." --alias "..." --json` with only precise aliases and any metadata the user supplied. It searches registered Vault sources and imported knowledge-root collections and keeps adapter result groups distinct.
3. If it returns candidates, inspect their full `content_md`, collection/source identity, line bounds, verification status, and coverage. A lexical candidate is not automatically an exact duplicate; verify the requested problem structure (task, setting, assumptions, and constraints) before reporting it.
4. If it returns `not_found_in_indexed_questions` or `coverage_gap`, continue with `$obsidian-vault-notes` for unindexed notes and `$pdf-paper-search`/`paper-locate` for problems or results stated only in PDFs as appropriate. Keep the result groups and source locators distinct.
5. Phrase an empty result as “not found in covered structured questions,” never “the problem does not exist.” If `discovered_unimported` is nonempty, name that coverage gap before broader fallback.

If the user explicitly limits the search to the question database, Vault notes, PDFs, a named folder, year, course, venue, or other range, honor that scope and do not force the default question-database-first route outside it. The database is a derived local index, not a second canonical record store; source Markdown remains owned by `$obsidian-vault-notes`.

### Question collection import

Read [references/question-database.md](references/question-database.md) before importing or auditing structured question collections.

1. Run `question-discover --json` to inventory marker-bearing collections under the configured question-collection root. Discovery never imports automatically.
2. Run `question-import --scope vault|knowledge-root --source-relative "..." --json` without `--write`. Review the resolved scope, source format, document/question counts, verification status, source hash, and `plan_sha256`.
3. Apply only the unchanged preview with `--write --expect-plan-sha256 ...`. A stale hash, path escape, reparse point, root mismatch, scan error, or changed source must stop before SQLite replacement.
4. Run `question-status --json` and a narrow `question-search` after import. Completion requires the imported collection count, source locator, adapter group, and coverage state to agree.

Never execute collection-owned scripts or treat a collection-owned SQLite index as canonical import evidence. Parse the canonical Markdown locally, keep originals read-only, and preserve `unverified` or receipt-mismatch status instead of upgrading it by inference. `question-index` is a direct refresh entry for Vault automation; prefer the guarded `question-import` workflow for imports.

### Vault notes and organized records

Load `$obsidian-vault-notes` and obey its authorization packages. The Vault includes ordinary notes and the user's organized domain records; do not invent a second record store. Exact-note reads must use the current Markdown file. Use guarded writes only when the user explicitly asks to create or modify a note.

### General library search

1. Run `status` and inspect `index_coverage` and source-path health.
2. Run `library-search --query "..." --alias "..." --json`. Keep aliases precise; use a few Chinese/English variants rather than broad related words.
3. Report the relative PDF path, one-based PDF page, excerpt, match reasons, extraction state, and coverage warning.
4. If pending, no-text, or failed PDFs remain, phrase an empty result as “not found in indexed text,” not “absent from the library.”

For a broad request to search the personal knowledge base, search Vault notes through `$obsidian-vault-notes` and PDFs through `library-search`, then keep the two result groups and source locators distinct.

### Paper source location

Load `$pdf-paper-search`, then use `paper-locate --query "..." --json`. This one bounded call passes the configured unified index explicitly, runs core retrieval, bounded structural/signature rescue, and at most one expansion, checks adjacent pages when needed, and returns verified evidence plus a stop reason. Stop on `verified_hit`. For `ambiguous`, present the result compactly unless a top candidate already has the required page role and hard concepts but direct-statement verification is uncertain because of extraction damage or a crowded page; in that narrow case, load `$pdf` and visually inspect only the reported page for at most three candidates before deciding whether the page is exact. Report `not_found_in_indexed_text` or `coverage_gap` without starting a broad Vault, raw-SQL, or filesystem search. Use `paper-search` only for ranking diagnosis. If the command reports an outdated search format, run `index --resume` once to rebuild only FTS from existing extracted page text; it must not re-extract unchanged PDFs.

### Computation handoff

When a retrieval or source-reading task also requires a new experiment, training or evaluation run, profiling measurement, or symbolic/solver/formal check, finish the relevant source and page verification first, then load `$cs-ai-computation` with the exact claim, definitions, setting (data, model, hardware, or input domain), assumptions, source locators, and requested evidence grade (`formal`, `certificate`, `exact_reproduction`, `bounded_empirical`, or `numerical_evidence`). Do not invoke it merely to restate a calculation or result already present in a source.

This Skill remains responsible for retrieval coverage and source locators. `$cs-ai-computation` owns backend discovery, code, results, and computation records. Code that runs or tests that pass verify only the boundary they checked, never a scientific claim by itself. Any approved write-back still returns through `$obsidian-vault-notes`; the computation handoff does not grant Vault read or write authority.

### Search performance diagnosis

Run the local benchmark first to measure `status`, Vault retrieval, general PDF search, and paper-location PDF search separately. Then run the external host-agent benchmark when the task requires total response time or a model comparison. Use a fixed prompt and image, identical reasoning effort and service tier, at least one warmup for local scripts, and at least three measured runs. Compare medians and P90; keep failed runs visible.

Treat `model_orchestration_residual_ms` as an observed remainder, not pure inference time. It also contains queueing, network transfer, agent decisions, and event serialization. Do not recommend a faster model until the candidate meets the same source-location and page-verification requirements.

### Formal search evaluation

For search-dataset construction, blind-test freezing, or a baseline/candidate
comparison, apply
[references/search-evaluation-contract.md](references/search-evaluation-contract.md)
as the domain contract of an optional, user-run evaluation. This Skill ships
that contract and the latency benchmark only; it ships no blind-evaluation
runner, gold store, or scorer, so never report a formal evaluation result from
this Skill alone. The main task is source retrieval and evidence
location; do not turn arbitrary evidence into a derivation exercise merely to
create questions.

Keep real runner cases, private evidence, gold, source paths, query IDs, and
incident details in the isolated experiment directory. Performance benchmarks
and ranking smoke tests can diagnose latency or obvious regressions,
but they cannot by themselves establish retrieval quality.

### Named PDF reading

Resolve the PDF inside the configured library. If a title or stem resolves to more than one file, list candidates and ask instead of guessing. Prefer an indexed page for text-only inspection; load `$pdf` and render pages when layout, figures, or pagination matter.

### Stable resource IDs

When the user provides `KB-...`, run `resource-resolve --id ...` before using a path. Use the returned current path and recommended Skill. If the ID is unknown or `missing`, run an audit and report the failure; do not download a replacement or bind a different file by name.

For a new long-lived file inside the configured root and outside the Vault, use `resource-register --path ...`. Files in caches, temporary/tool-state paths, or the Vault must first be moved to an eligible long-term location. A folder, note, Vault attachment, symlink, or reparse point never receives a resource ID.

Use `resource-move --id ... --to ...` for an authorized rename or move. It must refresh note references, validate the source hash and target, preview the exact note edits, and use the matching plan hash for the write. Never substitute a raw filesystem move. Use `root-relink --root ...` after the whole knowledge root moved. If both old and new roots exist, stop for a move-versus-copy decision.

### Controlled write-back handoff

Only when the user asks to record a finding, prepare a handoff for `$obsidian-vault-notes`; do not create a note after every search. This applies to PDF findings, Vault-and-PDF synthesis, source-backed research notes, and other results coordinated by this Skill.

The handoff must state:

- the exact target note and the user's write authorization;
- every source locator, the applicable as-of date, and any coverage or extraction warning;
- the claim's verification status. Keep an unpublished preprint or technical report, independent reproduction, formal verification, and peer-reviewed publication distinct, and state the evidence grade (`formal`, `certificate`, `exact_reproduction`, `bounded_empirical`, or `numerical_evidence`); code that runs or tests that pass verify only the boundary they checked;
- items that must not change, such as frontmatter, embeds, wikilinks, block ids, quoted source labels, or a linked detailed note;
- Markdown semantic sentinels to check after the edit, such as a required link, status tag, block id, or literal delimiter;
- the article type, intended reader, and whether long-Chinese naturalness review is required.
- for a request to compress, continue, or bridge article prose: the target paragraph or section, its precise role in the argument, the requested length, and an instruction to return the smallest text that fulfils that role. Do not add generic background or a broader explanation unless the user asks for it.

For a high-risk write-back—research-claim status (a theoretical result, benchmark number, or reproduction claim), source-sensitive prose, a long article, or a file with protected Markdown structure—require `$obsidian-vault-notes` to return its `EditAudit`, including scope, source/status checks, sentinels, and sentence-level prose review. A lint result only reports mechanical warnings; never describe it as proof that the Chinese prose is natural.

This Skill owns evidence retrieval and the handoff package. It does not implement Markdown escaping, link validation, sentence rewriting, or low-level write operations; those remain with `$obsidian-vault-notes`.

### OCR readiness and one-PDF OCR

Use `ocr-preflight --json` to verify OCRmyPDF, Tesseract, `pypdfium2`, and `eng`, `chi_sim`, `chi_tra`, `osd` data. Run `ocr-one` only on an explicitly named input and a different output path. The command also produces a sidecar text file. Do not import OCR text into the library index, and do not claim reliable formula, pseudocode, or table recognition.

## Missing Sources

Every operation must validate the configured root and source directories. If a path is missing:

1. stop the dependent operation
2. run `setup_local.py check`; when it returns `setup_required`, ask the user to specify the knowledge-root location
3. run bounded rediscovery under the configured root when that root still exists
4. show candidates and ask whether the source moved, was deleted, or should remain configured temporarily
5. use `setup_local.py repair`, `relink`, or `forget` only after the user's explicit answer; schema-v2 whole-root repair must preserve the root identity and use the matching `root-relink` plan hash

Never rewrite a stored path merely because one candidate looks likely.

## Hard Boundaries

- Ordinary PDF indexing, search, page reads, and OCR treat originals as read-only. The only permitted rename or move is an explicitly authorized `resource-move` transaction with a matching dry-run plan hash, registry journal, note-link update, and rollback checks. Never delete, classify, or deduplicate originals automatically.
- Keep the page index local and incremental. Do not add embeddings or send the corpus to a remote service.
- Preserve one-based PDF page numbers. Report a printed page only when separately verified.
- Treat extraction errors as tool/corpus diagnostics, not negative search evidence.
- Do not let a missing specialist Skill silently fall back to copied or improvised behavior; report the missing dependency.

## Skill Maintenance Note

Update rationale and maintenance: the Teacher package maintenance manual, docs/MAINTENANCE.md.

## Canonical terminology

Read [canonical terminology](references/terminology.md) before changing a persistent object, schema, lifecycle, authority/evidence rule, stable interface, specialized behavior term, or hash-bound identity. Do not introduce synonyms, rename canonical terms, change constitutive fields, or reuse deprecated/reserved names without updating the terminology registry, version history, migration rule, and validator first.

`knowledge_root` means the movable local root that owns the Vault, question index, PDF library, and resource registry.; `kb_resource_id` means a stable identifier for a long-lived file outside the Obsidian Vault.. These core distinctions are mandatory; the linked glossary is normative.


## Operation-specific initial views

`status` checks only configured source health and the local PDF-index summary. It does not scan for replacement roots, probe OCR, inspect every question collection, or test integrations. Run `question-status` when question coverage matters and `ocr-preflight` for an OCR task. Broader suite diagnostics require `status --diagnostics --diagnostic-purpose "<specific fault>"`; this expansion does not authorize indexing, OCR execution or model benchmarks. Missing paths require explicit root-bounded `discover`, followed by the existing user-confirmed configuration workflow.

`question-search` preserves the complete selected question records in `results`, capped by the requested result limit. `result_groups` contains zero-based `result_index` references into that list, and `candidate_counts` reports each adapter's candidate count. It never repeats question bodies or adds an unselected question merely to show its adapter group. Keep each selected record's source adapter, provenance and coverage warning when handing off to another Skill.


The five owned JSON CLI entries (`manage_kb`, `setup_local`, `benchmark_search`, `suite_doctor`, `validate_terminology`) limit the complete returned UTF-8 response to 65,536 bytes by default, including parser failures and raw diagnostics. Expand only for a specific current need with `--max-response-bytes` (4096..1048576) and `--response-reason` above the default. A response-limit failure does not undo a successful operation; check current state before repeating a mutation. This transport bound does not replace operation-specific field selection or enforce a task-wide information quota.

Interactive `setup_local` in a human terminal keeps its prompts visible and is outside the automated JSON response guarantee. Automated callers must supply existing confirmation flags and explicit paths; never capture an interactive session as a shortcut to model context. Observer child passthrough, direct Python APIs, arbitrary shell and delegated tools remain broader routes. Select their sources and return only task-relevant fields before sending results to a model. Keep a cumulative record of already-read source sets, pages and fields; expand only to close a stated evidence gap.
