# Archive and review operations

Before source inspection for any authorized multi-step archive, review, adoption or handoff, read [recovery and diagnostics](recovery-diagnostics.md) and begin its required workflow session. Keep that session through the operation.

Read for explicitly authorized archive, import, formal review or continuity work. The main Skill research and authorization rules still apply. Load the [data contract](data-contract.md) for record/review writes or package assurance, and [terminology](terminology.md) before changing identities or evidence/review states. Use the relevant [workflow commands](workflows.md); do not load unrelated workflow branches.

## Preserve research continuation

Within authorized intake/archive work, check new-to-existing, within-batch and relevant old-to-old relationships using [continuation checks](continuity.md). Default to the incoming and affected records, using bounded retrieval of relevant existing records even when no prior history check exists. Lack of a prior check does not authorize a whole-history pass. Preserve unresolved relationships as pending; perform a whole-project relationship audit only when explicitly requested, and do not confuse relationship checking with recursive byte scanning. Reopen actual records and conditions, then distinguish revisions, developments, research dependencies, background and uncertainty. Save justified exact relationships through existing record revisions; preserve originals and do not confer research trust. A missing match is not independence. Use the external plan and checker to account for scoped records and candidate pairs, verify saved links and report pending items separately from material-save success. Explicit historical audits also check omitted and mistaken old links. Route views reflect stored relationships, including branches and merges, rather than arrival order.

## Evidence and review

- When archiving, capture `action → feedback → evidence` with exact claim, scope, assumptions, limitations, sources, and reopening conditions. Preserve the selected original material needed to investigate success and useful failure, following SKILL.md retention rules; every chat message, execution receipt, raw experiment run, training log and intermediate file is not required. Make selection before intake rather than archiving everything and relocating it later.

- Within an authorized archive/handoff, preserve records and required evidence in the final project before preparing handoff reviews. Use `review-batch --check-only` to check all exact review/report pairs without writing the project. After a human or AI has completed the substantive review, submit that batch with `--expected-batch` and `--expected-head` from the preflight. Preflight does not perform or authenticate research review. Use portable logical reviewer names; correct representation errors without repeating unchanged argument, reproduction or computational review. The single `review` entry remains available.

## Current use and history

- Incoming contribution reviews are foreign evidence. Import does not make them locally trusted. Explicitly review material or assess and document an applicable prior review before recording local acceptance.

## Predict before expensive intake

Run `python -B scripts/crs.py predict PROJECT --submission INTAKE.json --reviews PROPOSED_REVIEWS.json` before copying a large contribution. The optional review file is an array of already filled `crs-review/v1` declarations, including exact report hashes. It describes hypothetical local admission, not the intake's foreign reviews. Do not invent reviews or independence to obtain a desired prediction.

This read-only command reuses the intake selection/objective rules in an in-memory store and the same `assess` function used by current state queries. It opens existing project metadata, but never incoming asset/report paths and never writes CAS, HEAD, operations or scratch files. It returns the base snapshot, incoming revision states, counts, and reasons. Declared sizes, report availability, material integrity and admission are assumptions still requiring normal checks. Bind the subsequent ingest and review sequence to the corresponding current HEAD; changed input requires a new prediction. A prediction is not an ingest receipt, reusable integrity certificate or research review.

`accepted`, `usable`, `exportable`, and successful archival remain separate. An established/refuted record with only self-review can be saved faithfully and remain inconclusive. Strong dependencies propagate their actual status. Inspect concrete research premises before revising dependencies; asset provenance and developmental history must not silently become research premises. Preserve old revisions, and never weaken a real premise merely to remove a pending state.

Explicit review-batch preflight still verifies its materials. Submission freezes and rebinds report bytes, then verifies material admission once inside the locked transaction. It does not trust preflight cache entries, HEAD equality, size or mtime as proof of unchanged content. Report portability, review identity, supersession and report binding checks remain distinct. Corruption found inside the transaction blocks publication.


## Operation-local location validation

The native Store scopes ordinary ingest, review preparation/publication,
transactions and recovery; `review_batch` includes its preparation in one scope.
On Windows, the first actual location lookup leases one ordinary locations file
with read sharing only, parses its closed structure once, and keeps its identity
bound until scope exit. No lookup means no metadata read: committed retries,
unknown operations and stale snapshots retain native dispatch precedence.

Every requested digest validates its current hot, cold and pending-move paths.
Path-check success and content-hash success are never cached. Whole-table access
and pending-table snapshots validate all rows; returned snapshots are isolated.
Views expire at scope exit. The synchronous Store is not a shared-thread API.
Cold relocation and location writes are rejected before side effects in a read
scope; outside it, cold relocation and full audits keep their native behavior.
Other platforms, absent tables and unavailable read leases use native checks.

This reduces repeated position-metadata traversal, not research review or
research dependency analysis. New containers still receive recursive checks,
reused content is actually hashed, publication targets remain validated, and
foreign reviews do not gain local authority. A changed strong premise still
requires affected descendants to be reviewed. Unchecked relationships remain
pending; selected-object validation never proves all history is healthy.


## Bounded archive preparation

For already selected contributions and explicitly written reviews, run `prepare-archive PROJECT --submission INTAKE.json --review-batch BATCH.json --expected-head SHA --out NEW_EXTERNAL_CHECKLIST.json`, adding `--revision SHA` for each needed old record. It accepts the existing intake and review-batch formats, at most 100 contribution records, 100 exact old revisions, 1000 reviews, 10000 declared assets, and 8 MiB per input JSON. Reports must be explicit relative files under the batch directory. Use existing `prepare-report` to copy supplied report text to a new file when needed; it refuses overwrite. This operation neither writes report content for the author nor invents reviews.

The saved checklist binds one HEAD and includes exact requested old records, report binding/portability findings, missing review materials, qualification checks and predicted incoming states. All source metadata is loaded from one snapshot. Reports use the existing batch scanner and its 16 MiB per-report / 64 MiB aggregate limit; incoming evidence bytes remain unread. When report bindings are incomplete, `prediction_complete=false` and predicted states do not include those proposed reviews. Inspect the explicit gaps; unknown exceptions are failures, not valid predictions. A successful preparation command only means that its checklist was written.

The checklist is external, exclusive-create and advisory. Its `ready_for_declared_acceptance` concerns hypothetical review acceptance, not the truth of a research claim, evidence availability, usability, exportability or permission to publish. Review those distinct states explicitly. It is never an `expected-batch` admission receipt. Preserve unchanged substantive verification; after explicit ingest with the saved HEAD, run native `review-batch --check-only` against the new HEAD and then commit its exact batch/HEAD using a new operation identity. Locked byte checks remain mandatory. A stale HEAD requires reconciliation and a fresh preparation; do not reuse it as current authority.

Use the returned argument arrays as templates: replace marked operation and committed-HEAD values. Read all already selected final revisions in one existing `records` call. Put expanded response options before the subcommand and supply a meaningful `--response-reason`; the maximum remains 1048576 bytes. A bounded CLI summary points to the complete external checklist, never silently truncates it. Additional unknown relationships may be retrieved for a concrete gap. Continuation planning and pending relationships keep their original affected coverage.
