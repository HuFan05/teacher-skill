# Improvement Strategy

Use this reference when a regression fails and you need to improve the searcher.

## Goal

Improve ranking quality while keeping both the script and the skill structurally simple enough to maintain.

Let the benchmark grow as memory; do not let `SKILL.md` grow into a catalog of past mistakes.

Do not treat each bad result as a new special case. Treat it as evidence that one of the existing layers is under-specified.

## Improvement Loop

1. Freeze the failure as a benchmark case.
   If the failure came from a user correction, do this in the same turn before anything else.
2. Identify the failing layer.
3. Prefer a reusable fix over a benchmark-specific fix.
4. Update `SKILL.md` only if a workflow invariant changed.
5. Validate the corrected case first; rerun the full regression set only for broader ranking changes.
6. If the code or instructions became more tangled, simplify before stopping.

## Failure Layers

### Corpus coverage / inventory

Symptoms:

- the exact source exists locally, but the current SQLite shelves do not contain it
- the search spends time in clearly wrong shelves before reaching the right folder
- direct local filenames or folder names strongly suggest the right source, but the workflow still starts broad

Preferred fixes:

- narrow the candidate shelves before indexed search
- add or improve direct-PDF fallback
- cache direct text extraction so repeated searches are fast
- add the relevant shelf to the indexed corpus later if the source recurs often

### Query normalization

Symptoms:

- the right page is not retrieved at all
- aliases are noisy or too literal
- OCR variants are missed

Preferred fixes:

- add or improve normalization
- improve formula-to-words conversion
- refine query-type detection

### Page understanding

Symptoms:

- related-work, abstract, or overview pages outrank statement pages
- proof or appendix pages outrank theorem, algorithm, or table pages
- a page that merely mentions or cites the result outranks the page that states it
- a page that uses the result as a step or baseline outranks the page that states the target result
- a page that proves the result indirectly is reported as the direct source

Preferred fixes:

- improve page-role detection
- improve local-statement detection
- improve direct-statement detection
- separate direct-source features from proof-ingredient and citation features

### Scoring

Symptoms:

- the right page is in the candidate set but ranked too low
- two pages are both relevant, but the wrong one wins consistently

Preferred fixes:

- add a reusable feature
- adjust scorer weights
- split one overloaded feature into two cleaner features

## Preferred Abstractions

When generalizing from feedback, prefer adding to one of these buckets:

- corpus inventory
- query type
- page role
- hard concept
- soft concept
- direct-statement feature
- proof-ingredient penalty
- overview/related-work penalty
- statement-page bonus

If a proposed fix does not fit any bucket, first ask whether a missing bucket should exist. Only after that consider a narrow exception.

## Escalation Rule

Use a narrow exception only when all of the following hold:

- the failure is real and reproducible
- the right fix cannot be expressed as a reusable feature without excessive collateral damage
- the exception is local and easy to explain
- a regression case is added for it

If multiple narrow exceptions begin to accumulate around the same phenomenon, replace them with a higher-level abstraction.

## Refactor Triggers

Refactor instead of extending in these cases:

- the same query intent is handled in several separate helper functions
- the same page distinction is encoded by several unrelated regexes
- a new benchmark requires touching many distant `if` branches
- old definitions are shadowed by later redefinitions
- score terms cannot be explained in a small number of reusable feature families

## Desired Architecture

Move the script toward this shape over time:

- corpus inventory and candidate narrowing
- query normalization and query typing
- page-role and local-statement extraction
- reusable feature extraction
- one unified scorer
- regression-driven tuning

This does not require machine learning, but it should look like a feature-based ranker rather than a bag of case-specific patches.
