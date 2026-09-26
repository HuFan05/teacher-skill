---
name: pdf-paper-search
description: Locate CS/AI algorithms, theorems, definitions, equations, results, and claims in local paper and book PDF shelves. Prefer indexed SQLite search when the right shelf is known, and fall back to narrowed direct-PDF search when the exact source may not be indexed. Use when the host agent needs to find which local PDF page states an algorithm, theorem or lemma, definition, model or architecture description, loss or objective equation, results table, figure, benchmark number, or claim, and return the best verified source location. Trigger on requests such as "$pdf-paper-search", "pdf_paper_search", "find this algorithm/theorem/result in my local PDFs", or "locate the source page of this OCR-corrupted equation or table".
version: v1.0.0
---

## Skill Run Observation

<!-- skill-observer:v2 -->
- As the first execution action after selecting this Skill, run `& $env:SKILL_OBSERVER_CLI begin --skill pdf-paper-search --quiet --fail-open; & $env:SKILL_OBSERVER_CLI phase set --skill pdf-paper-search --phase workflow.startup --quiet --fail-open` when `SKILL_OBSERVER_CLI` is available.
- At each actual workflow transition, append the matching fail-open `phase set --skill pdf-paper-search` call to the same PowerShell tool invocation that starts the next business action. Use only `retrieve`, `verify_live`, `plan_change`, `mutate`, `validate`, `version_control`, and `final_response`; do not add a model-tool round trip only to mark a phase.
- When invoking a bundled production script from this Skill, use the matching `scripts/observer_run.py`, `observer_run.ps1`, or `observer_run.mjs` wrapper when that wrapper exists, and record the matching production-entry phase from `references/observer-phases.json`; use `pdf-paper-search.script.run` only when the target entry is not listed. Wrappers must preserve the child process output and exit code.
- In the final business-tool invocation, set `final_response`; then, before the final response, run `& $env:SKILL_OBSERVER_CLI end --skill pdf-paper-search --status success --quiet --fail-open`. Use `failed` or `cancelled` instead of `success` when that outcome is known.
- Observation is fail-open and silent: never ask the user to repair it, never expose routine telemetry in the response, and never let an observer failure block the Skill.
- Do not pass prompts, file contents, tool inputs, tool outputs, secrets, or personal data to the observer. Host stop/session-end hooks, when available, close runs left open by interruption.
- Phase definitions and allowed fields live in [references/observer-data-dictionary.md](references/observer-data-dictionary.md); load it only when instrumenting or analyzing this Skill.

# PDF Paper Search

## Skill Maintenance Note

- Update rationale and maintenance: the Teacher package maintenance manual, docs/MAINTENANCE.md.

## Overview

Use this skill to locate the best local PDF page for a CS/AI algorithm, theorem or lemma, definition, model or architecture description, loss or objective equation, results table, figure, benchmark number, or claim.

Default output:

1. source
2. location
3. match type
4. why
5. confidence
6. gap

Report PDF page and printed page when both can be verified.

## Workflow

Follow this order:

1. Identify the query type: algorithm lookup, theorem lookup, definition lookup, equation lookup, result-table lookup, figure lookup, claim lookup, named-method lookup, or source-location lookup.
2. Preserve hard concepts and let the shared `QuerySpec` deterministically build compact bilingual aliases plus structural anchors (complexity bounds, formulas, model variants, dataset–metric pairs, reported numbers). Add manual aliases only when diagnostics show a missing concept or source hint.
3. Narrow the corpus before broad search. Prefer likely shelves, filenames, or topic and venue folders over `all pdf*.sqlite3` and over scanning all of `~/Documents`.
4. If the indexed shelf already has survey metadata, consult document/section survey matches before trusting page-level BM25 alone.
5. When one trusted SQLite index is known, run `paper_locate.py` for the normal answer-ready route. It binds that explicit database, verifies at most eight different PDFs and adjacent pages per pass, selects the labeled segment (statement or definition box, algorithm box, table or figure caption, numbered equation) with the strongest query evidence instead of assuming the first item on a page is the target, performs bounded structural-anchor and signature-coverage rescue before one alias expansion, and stops on a verified exact hit.
6. Use `paper_search.py` compact top-3 output for ranking diagnostics or compatibility work.
7. If indexed results are noisy, off-domain, or the likely source PDF is not indexed, switch to the direct-PDF fallback on a narrowed file set. When filenames are opaque, prefer a prebuilt library survey inventory for the target root.
8. Verify the top pages by reading the local statement rather than trusting ranking alone. Use `--verify-rank N` when compact evidence is not enough.
9. Report the best verified match as `exact hit`, `near-exact`, or `nearby material`.
10. If a user correction exposes a regression, freeze it as an external benchmark case through the Regression Workflow; never store the real case in this Skill tree.

## Query Normalization

Read [references/query-normalization.md](references/query-normalization.md) whenever the query contains formulas, Chinese text, OCR noise, mojibake, theorem-style prose, model or dataset names, or notation variants.

Core rules:

- preserve hard concepts such as `lower bound` vs `upper bound`, `worst case` vs `average case`, `on-policy` vs `off-policy`, `self-supervised` vs `supervised`, `convex` vs `nonconvex`, `top-1` vs `top-5`, and statement page vs related-work or abstract page
- keep alias count small
- keep named methods glued to their component or result family, such as `scaled dot-product attention` or `ResNet-50 ImageNet top-1`
- add one verbal alias for a formula-heavy query when that makes matching easier
- make FTS aliases short conjunctive packets; a long paraphrase must not require every descriptive word to survive PDF extraction
- preserve digit-bearing structural tokens such as `n2`, `top-1`, and `resnet50`, and bridge variant spellings such as `ResNet-50` versus `ResNet50`, `lg` versus `log`, or `d_k` versus `d k`
- normalize reusable bilingual CS/AI terms before alias construction; never encode a benchmark title, path, or gold answer as a query rule
- when normal top-N retrieval misses, derive a few strict rescue packets from hard concepts, short formula identifiers, digit-bearing tokens, and formula operators; rank rescued pages by target statement role and local signature coverage rather than whole-page BM25 alone
- keep recognized complexity bounds, attention formulas, model variants, dataset–metric pairs, and reported benchmark numbers as structural anchors; notation/OCR variants must match the same fingerprint before they can upgrade a result to `exact hit`
- avoid noisy standalone symbols and generic words
- indexed search and direct PDF fallback must derive aliases/signature terms from the same `QuerySpec`
- use `--alias-mode core|expanded|full|auto` only when debugging query breadth; default `expanded` is the regression-tested breadth
- numbered-object source-location queries, such as `Algorithm 2`, `Table 3`, `Eq. (4)`, or an arXiv identifier, should keep the label, number, and identifier before generic words

## Indexed Search

Before indexed search or compatibility diagnostics, you must read [Indexed Search](references/indexed-search-operations.md). Select an explicit database (or an explicit data root for the compatibility route), retain bounded verified-page checks and preserve necessary statement conditions.

## Direct-PDF Fallback

Before using the direct-PDF route, you must read [Direct-PDF Fallback](references/direct-pdf-operations.md), including extraction recovery. Use it when indexed coverage is missing or unsuitable; first narrow to an explicit PDF or likely folder. Extraction failure is a tooling failure, never evidence that the source lacks the statement.

## Verification Rules

Do not trust top-1 blindly. In normal runs, inspect the compact top 3 before reporting a source.

Prefer pages where:

- the labeled theorem/lemma/proposition/corollary or definition is on the page itself
- the numbered algorithm box, results table caption, figure caption, or numbered equation is on the page itself
- the key concepts occur in one local statement, not scattered across unrelated lines
- when a page contains several labeled statements, the selected window is the segment with the strongest hard-concept, structural-anchor, and signature overlap, not simply the first statement

Downgrade pages that are mainly:

- tables of contents, indexes, or reference lists
- range references such as `Tables 2-4`
- proof or appendix-proof pages that restate the statement
- related-work mentions or citations: a cited or attributed mention is not the original statement
- abstract or introduction summaries, except when the query asks where a claim is made
- pages that use the result as a step or baseline
- nearby variants that drop or weaken a hard concept from the query, such as a different model size, dataset, metric, or bound direction

If OCR or encoding damage prevents direct verification, say so explicitly and lower confidence.

If the direct fallback was needed because the source PDF was not indexed, say so explicitly in `gap`.

When compact evidence is insufficient, rerun with `--verify-rank N` or full `--json` and inspect the candidate's `PageEvidence`: page role, local/direct statement flags, hard concepts found/missing, and local statement window. Do not report `exact hit` when hard concepts are missing from the local evidence window.

## Post-location Computation Handoff

This Skill locates and verifies source pages; it does not compute extensions of the located statement. If the user also asks to check a complexity claim, verify an equation, reproduce a table or benchmark number, or explore a consequence, first complete page verification and report the exact source locator. Then load `$cs-ai-computation` with the verified statement, definitions, domain, assumptions, and requested deliverables, including the dataset, metric, and model variant when a result is involved.

Keep the two evidence types separate. The PDF page supports what the source states; the computation record supports only the calculation or run it documents, at its own evidence grade (such as `exact_reproduction` or `bounded_empirical`). A computed agreement does not upgrade a nearby source page to an exact hit, and a source citation does not validate new code.

## Reporting Pattern

Keep the final report compact:

- source: paper or book title
- location: section if visible, PDF page, printed page if visible
- match type: `exact hit` / `near-exact` / `nearby material`
- why: labeled statement, algorithm/table/figure/equation number, exact sentence, or closest verified relation
- confidence: `high` / `medium` / `low`
- gap: OCR limitation, page-role ambiguity, ranking caveat, or missing-index note when relevant

## Regression Workflow

Only for an explicitly requested regression-maintenance task, you must first read [Regression Workflow](references/regression-operations.md). Preserve external case acceptance, review, budget and failure-classification requirements. Real regression cases stay outside the Skill bundle.

## Common Failure Modes

- relaxing the query too early and losing a hard concept
- searching all shelves before narrowing the likely corpus
- trusting a high-ranked page without confirming page role
- assuming the correct PDF is already in the SQLite indices
- using direct extraction over all of `~/Documents` instead of a narrowed file set
- treating a `pdftotext`/MiKTeX initialization failure or empty extraction cache as a genuine PDF search miss
- treating a no-hit as conclusive while diagnostics contain unread DB/PDF or extraction errors
- treating related-work, abstract, proof, or overview pages as direct sources
- reporting a nearby variant as an exact source
- adding benchmark-specific hacks where a reusable feature is needed

## Maintenance Rule

Keep `SKILL.md` focused on workflow invariants.

Do not turn it into a catalog of regressions. Keep all real cases, gold, locators, filenames, pages, IDs, and evidence in the external Skill regression catalog. This Skill tree may contain only genuinely synthetic Harness fixtures that cannot identify or reconstruct a historical case; keep detailed general guidance in `references/`.

For maintainability, move reusable behavior toward shared package modules: `QuerySpec` for query understanding, diagnostics for environment/tool failures, feature vectors for page understanding, and scorer breakdowns for ranking explanations.

## Public retrieval boundaries

The three public search entrypoints require explicit sources: one index for `paper_locate.py`, explicit `--db` or `--data-root` for `paper_search.py`, and explicit `--pdf-file` or `--root` for `direct_pdf_search.py`. A single PDF does not implicitly load the default Documents inventory. Choose the narrowest sufficient shelf; a file or root argument is not permission to expand beyond the task.

Complete public-main output is filtered locally and limited to 12,288 UTF-8 bytes including its newline. This applies to compact, JSON and compatibility text returns. Diagnostic objects expose fixed categories rather than raw messages, aliases or paths. Invalid arguments, Python exceptions and unexpected diagnostic output return a fixed failure. Oversized content returns no evidence and an incomplete status; do not call it a verified result or evidence that the statement is absent.

First reduce candidate count or request one `--verify-rank`. If necessary conditions or adjoining statement text still do not fit, request a justified larger envelope with `--max-response-bytes N --response-reason "specific evidence gap"` (maximum 262,144 bytes). The reason stays local. Preserve assumptions, missing-concept indicators, page roles and coverage failures; do not discard them merely to make output fit. Full JSON is an explicit broader view with the same limit, not an unrestricted debugging bypass.

These are per-response Python public-main guards, not task-wide cumulative enforcement, OS-level stream interception or host isolation. Keep a running task record of selected shelves, PDFs, verified pages, emitted evidence and unresolved gaps across calls. Preserve the existing eight-PDF verification pass and one alias-expansion rules; do not reset scope by rerunning a command. Expand only for a named gap within authorization and stop when evidence suffices. Direct library imports, shell reads, extraction helpers and evaluation commands remain outside this return guard; keep their raw output local and select only needed evidence before returning it. Do not run evaluation archives during ordinary source search.

## Local Operation Privacy

When this skill touches local files, local commands, local browser state, local notes, repositories, documents, media, logs, screenshots, or credentials:

- Use the minimum local content needed for the task.
- Prefer local deterministic inspection, parsing, filtering, validation, and transformation before model reasoning.
- Do not put full local file contents into context when a path, line number, small excerpt, schema shape, count, hash, diff summary, or reduced command result is enough.
- Before remote reasoning, replace sensitive values locally with stable random placeholders such as `PERSON_8f3a91`, `SECRET_5c9e22`, `PATH_a812ff`, `EMAIL_29d41c`, or `HOST_71c09e`.
- Keep the placeholder map and raw sensitive artifacts local only.
- Restore placeholders locally after model output is produced.
- If redaction would break correctness, say so explicitly and ask before exposing the sensitive content.

## Canonical terminology

Read [canonical terminology](references/terminology.md) before changing a persistent object, schema, lifecycle, authority/evidence rule, stable interface, specialized behavior term, or hash-bound identity. Do not introduce synonyms, rename canonical terms, change constitutive fields, or reuse deprecated/reserved names without updating the terminology registry, version history, migration rule, and validator first.

`pdf_shelf` means a configured corpus and optional index used for bounded paper and book page search.; `page_verification` means the direct confirmation that a returned PDF page contains the requested statement, algorithm, equation, result, or claim.. These core distinctions are mandatory; the linked glossary is normative.
