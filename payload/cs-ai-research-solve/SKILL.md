---
name: cs-ai-research-solve
description: Preserve, continue, review, map, query, and exchange durable computer-science and AI research records (algorithms, theory of computation, ML/DL, systems, NLP/CV/RL), evidence, failed approaches, and contributions from different researchers. Use for sustained research projects and research-asset management.
version: v1.0.0
---

## Skill Run Observation

<!-- skill-observer:v2 -->
- As the first execution action after selecting this Skill, run `& $env:SKILL_OBSERVER_CLI begin --skill cs-ai-research-solve --quiet --fail-open; & $env:SKILL_OBSERVER_CLI phase set --skill cs-ai-research-solve --phase workflow.startup --quiet --fail-open` when `SKILL_OBSERVER_CLI` is available. On POSIX invoke the same executable through the active shell. Do not require PowerShell only for observation.
- At each actual workflow transition, append the matching fail-open `phase set --skill cs-ai-research-solve` call to the same shell invocation that starts the next business action. Use only `retrieve`, `verify_live`, `plan_change`, `mutate`, `validate`, `version_control`, and `final_response`; do not add a model-tool round trip only to mark a phase.
- In the final business-tool invocation, set `final_response`; then run `& $env:SKILL_OBSERVER_CLI end --skill cs-ai-research-solve --status success --quiet --fail-open`. Use `failed` or `cancelled` when known.
- Observation is fail-open and silent: never ask the user to repair it, never expose routine telemetry, and never let observer failure block the Skill.
- Do not pass prompts, file contents, tool inputs, tool outputs, secrets, or personal data to the observer. Also do not pass paths, research content, task content, or raw artifacts. Host stop/session-end hooks, when available, close interrupted runs.

This optional host Skill timing is separate from the CRS research workflow observer and its 20-minute diagnostic threshold. It adds no research stopping condition, review authority, or publication gate.

# CS/AI Research Solve

CRS is research data infrastructure. Help the user pursue CS/AI research freely while preserving what was tried, what happened, what supports each conclusion, and how later work can reopen it. Keep correctness proofs and arguments, code at a frozen commit, configurations, seeds, data references, certificates, evaluation outputs, useful byproducts, source references, and informative failures connected.

## Reuse research assets as needs arise

When research needs a result, method, example, computation, source, or failure experience, first check the project's existing research assets for relevant or analogous material. Use a bounded query and reopen the exact records or assets needed; check their assumptions, scope, current evidence status, and applicability before reuse. A brief match alone is not enough, and an empty bounded query does not prove the whole library has no relevant material.

If existing material is absent or insufficient, search the web for the missing information (for example arXiv, OpenReview, the ACL Anthology, conference proceedings, official documentation and code repositories), verify the relevant sources and conditions, including the exact paper version, release or commit that supports the claim, and combine what was found with the available assets in native research reasoning. Revisit this principle whenever a new information need arises within the authorized research. Reuse context already inspected when it remains applicable; do not repeat searches mechanically or require web searches when existing evidence suffices. If retrieval is unavailable, state the gap and continue only as the available evidence supports.

This is an information-use principle, not a Harness-prescribed thought sequence, fixed research round, or requirement to externalize reasoning. Retain useful new material under the existing pending-work rules; adding it to the formal library still follows the existing archival authorization, evidence, and substantive review requirements. Harnesses govern asset access, publication integrity, and verifiable storage boundaries, not how the model thinks or whether a research claim is true.

## Research first; archive when requested

- Automatic CRS research rounds are off by default. Invoking this Skill or matching an existing project authorizes the requested research and reading its context; it does not itself request repeated rounds or formal archival work.
- Preserve the host agent's native reasoning flow at every effort level, including ordinary reasoning, medium and ultra. Do not impose CRS thought steps, researcher topology, effort settings, attempt quotas or periodic writing interruptions. An unresolved question or a useful byproduct is not a trigger to launch another round. Honor an explicit user request for continued research or an active Goal according to its scope.
- Retain useful observable attempts and feedback with sufficient selected evidence to understand and reopen them. Apply the retention policy below; do not keep every intermediate result, log or generated file. Use concise summaries and necessary artifacts, not a transcript of private internal reasoning. Keep pending work outside the formal library; structured archival JSON is unnecessary during ordinary research.
- At the end of research, give the answer, briefly identify the valuable material available to archive and its location when saved, and ask whether the user wants it archived. Until authorized, do not publish records, evidence objects, reviews or project changes, or automatically create a handoff package. Pending material is not yet part of the formal research map. Preserve its only useful copies while awaiting the decision; clean owned disposable copies only after their needed outputs are safe.
- Earlier explicit authorization remains effective. A request to archive, import, review or export authorizes the work necessary for that operation without another confirmation. Keep to that scope: saving a contribution does not automatically request a full review and ZIP, and reviewing existing material does not automatically request new research or unrelated archival work. The capture, review and delivery instructions below apply when that work is authorized.

## Select what needs to survive

Unless the user explicitly requests particular raw data or full-run preservation, do not retain or ingest everything a program produced. Choose by what would be lost if the material disappeared, not by file size or mere availability.

- Keep claim statements, correctness proofs or arguments, assumptions, claim scope, applicability, known gaps and corrections in full.
- Keep the code at a frozen commit, configurations, seeds, environment specification, evaluation outputs and final results needed to reproduce retained claims. Keep checkpoints only when a retained claim cannot be reproduced without them. Reference datasets by identifier and SHA-256 instead of copying them unless the claim requires those exact bytes. An entire environment image or every development checkout is not the default.
- Keep certificates required to check a conclusion, together with their verifier and inputs. Determine whether a database, checkpoint or cache is a certificate, a necessary research input, or regenerable state by inspecting its producer and consumer; its filename or size does not decide.
- Keep failed approaches with useful reasons, trigger conditions and the smallest sufficient reproduction. Retain additional experimental data only for a concrete verification obligation or identified follow-up experiment.
- Treat regenerable caches, intermediate databases, routine training and run logs, redundant exports and temporary working copies as disposable after their useful results have been extracted and checked. Temporary retention needs a concrete unresolved need and a cleanup condition; uncertainty alone is not a permanent-retention policy.

Before a substantial intake, make a brief selection by meaningful dataset or run, stating its use, what omission would lose, and what must remain. Reuse existing indexes; do not create a per-file preservation bureaucracy or rescan all history just to make this decision. Keep one sufficient evidence set with retrievable provenance. A full raw run may stay temporarily while extraction is unresolved; it is not automatically a second permanent archive. Faithful history requires the necessary content and relationships, not every incidental byte.

For an existing library, first reuse inventories, receipts and reference indexes to identify likely excess; inspect only the relevant producers, consumers and dependencies. Distinguish disposable work from referenced formal evidence. Do not unlink CAS objects or break historical references: retirement of already referenced evidence needs a supported, dependency-aware operation and an explicit account of lost availability. A request to inspect excess is not permission to delete the only required evidence. Report uncertain cases and the specific check that would settle them.

## Start from the research request

- Resolve the project and inspect `status`, `map`, or a bounded `query --brief` before continuing existing work. Use the user's language unless the project or user explicitly chooses another language.
- Read [terminology](references/terminology.md) before creating or changing research identities, evidence labels, or review states. Do not invent a replacement name for an existing concept or silently change its meaning.
- Read [the data contract](references/data-contract.md) when writing records/reviews or interpreting package assurance. Read [workflows](references/workflows.md) for actual commands.
- Choose research methods, tools, collaborators, and stopping conditions from the task. There is no prescribed collaborator count, attempt window, role sequence, or mandatory research direction.
- Keep the primary research question stable. When archiving a new direction, capture it as a distinct, explicitly linked question; preserve useful failures and byproducts. Metadata may grow, but importing or selecting another record must not switch the project's primary objective. Use a complete objective file for new projects; show the missing fields of an incomplete imported description and complete them only by an explicit binding.

The identity rule is: project_objective means exactly the immutable research objective formed by statement, domain, claim_scope, assumptions, evidence_standard, and completion_standard. `claim_scope` states the population the claim quantifies over (datasets, tasks, models, scales, seeds or inputs), its aggregation (for example mean±sd over N seeds, worst case, all inputs of size n, or with high probability) and its metric. It is not Product Goal and not project.json; those refer to a host task goal and a storage file. See the [objective definition](references/terminology.md#project_objective) for explicit completion of incomplete descriptions and related questions.

## Preserve research continuation


For an explicitly assembled archive, prefer the bounded `prepare-archive` checklist and one exact batch read described in the archive operations reference. Before an expensive intake, use the read-only metadata prediction in the archive operations reference to distinguish expected review, usable and exportable states from save success. Prediction never admits reviews or verifies incoming bytes.

For authorized archive, import, formal review or continuity checks, read [archive and review operations](references/archive-review-operations.md). It preserves the continuation, review-batch and incoming-trust requirements; follow its relevant contract links before those operations.

## Evidence and review

- Label established/refuted results, hypotheses, observations, attempts, failures, definitions, and corrections accurately. A hypothesis that held in experiments remains a bounded observation, not a general result. A rejected proof or experiment is not a refutation of its claim.

- Name the strongest evidence grade actually reached with the suite grades, strongest first: `formal`, `certificate`, `exact_reproduction`, `bounded_empirical`, `numerical_evidence`. `numerical_evidence` alone supports only an observation; code that ran, tests that passed or hashes that match verify only the boundary they checked.

- Review the actual research content using a method appropriate to its claim and scope: a proof or correctness argument for a theoretical claim, reproduction evidence for an empirical one. Check material identities, assumptions, claim scope, seeds, configurations, data identities, arithmetic or code when relevant, and, for an authorized formal review, record substantive findings and limits in a stored report. Ordinary research still checks its reasoning without automatically submitting a review.

- Software success verifies only the boundary it actually checks. Valid JSON, matching hashes, a clean audit, successful execution, and package verification do not establish a research claim.

- Do not fabricate a review report, infer independence from a different name or platform, or label self-review independent. A claimed independent review needs a concrete basis. Software cannot authenticate that claim.

- Reuse a review of unchanged material when its method, scope, and dependencies still apply. A software version change alone does not invalidate research evidence. Changed material or corrected premises require assessing the affected review scope.

- Source-based review may rely on an accurately reviewed summary, conclusion, applicability conditions, and retrievable locator for large evidence. Report this as citation-based assurance when the full evidence was not replayed.

## Current use and history

- `usable` answers whether a record can currently support reasoning. `exportable` also permits faithfully reviewed history subsequently withdrawn or questioned. Inspect both fields and the reasons.

- Keep reviewed failures and withdrawn results on the history map with their corrections. Do not describe affected descendants as false merely because a premise failed; investigate them and mark the affected reasoning.

- A presentation correction does not automatically invalidate dependent research. An invalidating or narrowing correction affects strong dependencies; a replacement needs its own applicable review.

- An established result depending on a hypothesis must declare that revision in `conditional_on` and remain visibly conditional. Missing/mismatched dependencies or circular justification cannot establish an exportable result.

## Storage and delivery

- Use `python -B scripts/crs.py` (`python3` where `python` does not name Python 3) from the resolved Skill root; Python 3.10+ and the standard library support the storage/exchange tools. Research computations may need their own declared environment.

- Ordinary requests to verify, archive or update mean the requested claims, selected new or changed evidence, affected records and their current status. They do not by themselves request a whole-history audit, deduplication cleanup, full browser rebuild, full translation or exchange package. Reuse applicable unchanged evidence and do not repeat a completed check merely because another workflow step begins. Run full audits only for an explicitly requested audit/cleanup or a concrete integrity problem whose affected scope requires it; state that scope first.

- On Windows, ordinary intake, review batches and transaction recovery share a lazy operation-local location index. Validate each requested object’s live paths and actual bytes; full enumeration and explicit audits still check all registered locations. An unrelated historical path anomaly does not certify or block an otherwise valid incremental write. Cold relocation runs outside the read-only operation scope. Unsupported platforms and unavailable metadata leases retain native validation. See [location scope](references/archive-review-operations.md#operation-local-location-validation).

- Use CLI/API operations to publish project changes. Incremental intake reuses verified existing content-addressed objects, validates newly supplied bytes and recursively checks new containers. Historical duplicate cleanup and unrelated historical container decoding are not prerequisites for incremental intake, review, recovery or export. Keep complete recursive conformity as a separate audit and explicitly authorized consolidation goal: one physical copy per SHA-256 across the entire enclosing research project, including work, history, reading, tools, caches and recursively decoded containers. A successful incremental write does not certify that whole-project goal, nor detect every duplicate hidden in historical containers or outside the formal object store. Apply the same rule independently to each external delivery (including all volumes of one delivery set). Empty files and hardlinks have no exception. Preserve source paths and container/history relationships as references; never alter bytes to evade equality or discard unique evidence. Incomplete recursive coverage blocks a conformity claim. All exports, delivery trees, packaging scratch and full recovery copies belong outside the entire project.

- Prefer one ZIP of at most 512,000,000 final bytes, selecting optional evidence by importance and future research relevance while retaining exact reviewed references for omissions. If needed, deliver multiple ZIPs within that ceiling with their shared index and ranking. Required records, review reports and dependency closure cannot be silently sacrificed to size.

For a project handoff, establish coverage of the main algorithms, important results, full proofs or correctness arguments, reproduction materials and necessary verification materials before optimizing size. A valid exchange projection is not by itself a sufficient project handoff. Use the [delivery sufficiency check](references/storage-delivery-operations.md#delivery-sufficiency-and-readable-maps); reconcile excluded records, inspect actual included evidence and exercise the delivered map. If essential material is missing, report the incomplete scope prominently and continue the authorized repair. Neither a hash-only reference nor a successful ZIP check establishes offline reproducibility.

For ordinary delivery, use the [planned export contract](references/planned-export.md): complete cheap metadata checks and required-material selection before consuming selected content; execute a saved plan only with its exact hash. Exchange integrity, declared material completeness, actual replay and readable presentation are separate outcomes. Ordinary export and finish do not audit unrelated historical duplication.

Before repairing an export, run `export-preflight` once for the frozen source snapshot. Inspect its direct-revision discovery before creating new representations; only the currently validated replacement plan is eligible for explicit reuse. Declare necessary full proof or argument text, algorithms, code, configurations, inputs and certificates as nonoptional contract materials before content preparation. Same-store lock-taking steps run serially; export acquires its publication lock before compression. Independent stores and already detached rendering may run concurrently. Resolve its path findings, transitive strong dependencies and capacity warnings together before intake/review or delivery staging. Complete required evidence checks during export; metadata preflight is not evidence verification. See the detailed repair order in storage and delivery operations.

For library creation, organization, adoption of external material, complete maps or project delivery, read [storage and delivery operations](references/storage-delivery-operations.md). Load its layout, web and formula presentation dependencies only for the requested output. Complete project delivery still requires actual visual evidence.

## Reopening and diagnosis

- Keep reproducibility outcomes separate from research verdicts. Preserve meaningful failed execution evidence as well as successful outputs.

- Report what is established, conditional, observed, failed, missing, or disputed; cite the supporting record revisions. For project questions, start with `query --brief`, then follow its `read_command` for the exact records needed. Brief results are explicitly truncated summaries; match counts do not answer the question. Check current selection, review state, conditions and sources. Equally relevant current selections sort first, while historical and unreviewed records remain visible. Answer only what the exact records support and identify unknowns.

Before source inspection for any authorized multi-step archive, review, adoption or handoff, read [recovery and diagnostics](references/recovery-diagnostics.md) and begin its required workflow session. Also read it for replay, interrupted-operation recovery or compatibility diagnosis. Exact asset inspection remains available during ordinary research; replay and formal-library writes remain subject to archival authorization.

## Model-visible retrieval boundary

Use `status` and paged `query --brief --limit N --offset N` for project orientation. For several already selected exact revisions, use `records --expected-head SHA --revision SHA ...` to load one fixed project view; the existing response ceiling still applies. Brief sources and selection lists are previews with explicit totals/completeness flags. Conditions, failure feedback and reopening cues are legitimate navigation context; exact record reads still decide applicability. An empty page is not proof that the library lacks a relevant asset. Keep the project snapshot stable while paging; if HEAD changes, reconcile the new snapshot rather than treating pages as one unchanged inventory.

The public `crs.py`, `crs_project.py` and `crs_web.py` mains bound the complete UTF-8 response to 65,536 bytes including newline and suppress raw exception/observer diagnostics. Put global `--max-response-bytes N --response-reason "specific evidence gap"` before the command when a larger exact view is needed (up to 1,048,576 bytes). Prefer fewer candidates and exact revisions first. `status --full` explicitly requests the broader selection view and remains bounded. Complete maps remain complete local artifacts through `map --out`; never prune stored records or claim conditions to reduce model context.

Always inspect `response_complete`. A false value means the response was not delivered in full; it is not an empty search or a rollback. The envelope preserves the business outcome and bounded operation/snapshot identifiers when known. Never automatically repeat archive, replay, import or publication because its output was omitted; inspect the original operation and use its existing recovery route. If the business outcome is unknown, investigate it before any retry. A fixed error or omitted output does not confer research trust.

These are per-response Python CLI boundaries, not an OS sandbox or a task-wide cumulative quota. Preserve the authorized project, inspected revisions, useful context and remaining evidence gaps across calls. Reuse unchanged evidence; expand for a concrete need without imposing thought steps or fixed research rounds. Direct library/shell reads and generated local files are outside this transport guard and require their own bounded selection before entering model context.

Within one synchronous Windows continuity-check command, bounded JSON reads may reuse parsed content only while the exact object has a live read lease and its current ordinary path still identifies that file. Cache misses, unavailable leases, capacity limits and other platforms retain native byte checks. The cache is discarded at scope exit, does not change continuation coverage or review authority, and is not a persistent trust record.

## Current implementation boundary


Platform, adoption and supervisor limits are in [recovery and diagnostics](references/recovery-diagnostics.md#current-implementation-boundary); read them before relying on those capabilities.

## Skill Maintenance Note

Update rationale and maintenance: the Teacher package maintenance manual, docs/MAINTENANCE.md.
