# Research continuation and historical omissions

Use this workflow within authorized archive/import work or an explicit project
relationship audit. Ordinary research reasoning does not require a plan,
fixed round, periodic writing, or formal publication. The checker observes asset
handling; it does not prescribe a research method or authenticate semantic work.

## Inspect and preserve

After saving an intake, distinguish the returned `materials_saved` success from
its pending continuation check. Read the returned baseline snapshot and generate
one external plan with explicit `--scope affected`, including on the first intake.
This bounds work to incoming and affected relationships; it does not certify
untouched history. Existing edges are candidates for content inspection,
not automatically correct. Keep the plan, decisions and receipts beside the
project's work files, outside the formal library.

```powershell
python -B scripts/crs.py continuity-plan PROJECT --scope affected --since BASELINE_HASH --out NEW_PLAN.json
python -B scripts/crs.py continuity-template PROJECT --plan NEW_PLAN.json --out NEW_DECISIONS.json
```

An explicit whole-history audit uses `--scope all`; without an intake it omits `--since`. All stored
revisions remain in its scope, including unreviewed history, informative failures
and records without existing links. Omitted package bodies cannot be inspected;
show that limitation rather than claiming the missing record is independent.

The plan contains exact record revisions, suggested pairs, existing relations,
an input digest of newly stored revisions, and its snapshot. The CLI emits only
compact counts. Use `record --revision HASH` and `asset --sha256 HASH` to reopen
the needed actual content. Examine statement, scope, assumptions, action,
feedback, limitations and reopening conditions. Local lexical matching is a
candidate screen, not semantic classification; omitted ranked candidates and
common-word filtering mean it never proves all implicit relationships were found.
Add an overlooked pair to the decisions when both records are in the planned
snapshot and the pair touches the scope. Expand the plan if the needed records
are outside that scope. Do not equate an empty query with independence.

Reuse applicable earlier decisions and receipts without requiring a prior full
audit. Ordinary intakes use `--scope affected --since BASELINE_HASH`. Explicit
`--seed HASH` values add relevant records, including unresolved earlier cases.
The affected scope includes incoming and changed selections, lexical neighbors,
and their existing connected routes, so old-to-old omissions can be examined too.
The CLI does not authenticate a claimed prior full audit and reports the exact
narrower scope. Resolve uncertainty through relevant seeds and bounded reads;
uncertainty does not automatically authorize `all`. Keep unresolved items pending.
Do not silently treat an affected check as a completed project audit.

## Decide relationships from the content

Give every scoped record and suggested pair a disposition. Newly generated
templates are pending, not filled-in evidence of completed reading.

- Same research item, revised: keep the ID and use `previous`.
- Distinct development: use `extends`; explain which earlier obstacle, method
  or conclusion it advances and what was added.
- Research reliance (a premise, an input result, dataset or configuration, or a
  definition): use `premise`, `input` or `term` as applicable. An
  extension alone does not carry strong invalidation effects. If both meanings
  apply, preserve both dependencies in the record; inspect both in the actual
  content even when a pair disposition identifies its principal relationship.
- Background similarity: use `background`; do not turn it into a progression.
- A demonstrably wrong existing relationship: mark `incorrect`, retain a pending
  repair, and use the existing explicit revision/correction workflow. A rejected
  premise does not make every descendant false. A later plan must inspect the
  replacement and preserve the labelled historical relationship.
- Uncertain: retain `pending` with what remains to inspect. `no_match` means only
  no justified relationship found in the stated scope; `unrelated` explains why
  this particular candidate pair is parallel, not a universal independence claim.

For a confirmed pair, orient exact successor/predecessor hashes, choose an
existing relation type, identify the stored successor revision and cite the
inspected evidence revisions. Distinguish `documented_development` from
`research_extension`: research applicability does not show that the
author historically used the earlier work. Use `revision_history`,
`research_dependency`, or `thematic` for their corresponding relation types.
State stronger hypotheses, narrowed ranges, unresolved conditions and the exact
advancement in the stored dependency reason and record action/feedback.

Preserve imported originals and foreign reviews. To add a missing relationship,
ingest an explicit same-identity successor revision carrying the dependency; do
not rewrite the imported bytes or silently retarget descendants. The revision
receives its own evidence assessment. Reuse applicable unchanged research
review only according to the existing review contract; link confirmation never
grants claim acceptance, independence or inherited source trust.

## Validate and report

```powershell
python -B scripts/crs.py continuity-check PROJECT --plan NEW_PLAN.json --decisions NEW_DECISIONS.json --out NEW_RECEIPT.json
python -B scripts/crs.py routes PROJECT
```

The checker recomputes the exact plan, requires every scoped record and proposed
pair to be accounted for, checks cited revisions, verifies confirmed edges in
the successor or its explicit revision chain, preserves originals, and rejects
unrelated snapshots or changed HEAD. Report `completed_with_pending` when reads,
relationships, disputed old links, missing references or ordering remain open.
`completed_for_declared_scope` is neither semantic exhaustiveness nor research
verification. Software cannot establish that an asserted content read happened
or that its semantic judgment is sound. Keep those limits explicit.

New records created while repairing links are enumerated in the receipt. Inspect
their new research content and any newly introduced relationships; if a
repair creates another research result, include it in a fresh plan instead of
claiming the earlier plan covers it.

The map's development view orders explicit non-background relations from
predecessors to successors and exposes branches, merges, feedback and evidence
states. Background links remain available in record details. Cycles or descendants
whose ordering cannot be resolved remain visible without a fabricated sequence.
Browser editions derive their routes from the same exact records; retain links
to complete manuscripts, conditions and sources. Refresh reading views after saved
relationships change, under the existing presentation-validation rules.

Archive/report completion should identify scope and snapshot, saved materials,
confirmed developments, old omissions or wrong links found, unresolved items,
screening limits, and exact plan/decision/receipt locations. Do not describe an
operational import alone as completed semantic integration.
