# Canonical terminology

Read this before changing a persistent object, schema, lifecycle,
authority/evidence rule, stable interface, specialized behaviour term, or
hash-bound identity. Do not introduce synonyms or rename canonical terms.

## Requests and answers

- `need_frame`: the recorded classification (kind, stuck point, clarity, depth)
  and at most three anchored readings of one request, plus the Skill's
  decision: `proceed`, `investigate_then_reframe`, or `ask_one_choice`.
- `reading`: one interpretation of what the requester needs, ≤80 characters,
  anchored by a verbatim fragment of the request.
- `ask`: one answered request: its frame, answer, evidence items, metrics and
  investigation counts. Advances execution only.
- `evidence_item`: a bounded excerpt this Skill obtained itself
  (`retrieved_source` or `executed_check`), with its locator and digest.
- `basis`: what a point rests on — `retrieved_source`, `executed_check`,
  `user_supplied`, `reasoning`.
- `answer_gate`: the deterministic checks every answer passes before it is shown.
- `answer_metrics`: the counts the gate computes; `semantic_correctness_proven`
  is always false.

## Assets

- `archive_candidate`: material extracted automatically from a passed answer —
  `claim`, `source`, `failure`, `open_question`; states `pending`, `archived`,
  `declined`.
- `archive_proposal`: the ranked list shown to the requester, bound by
  `plan_sha256`; the one decision is `verified_only`, `all` or `none`.
- `library`: archived candidates; searched first by every investigation.
- `core_cognition`: the ten protected fields derived from the store and rendered
  through the reading layers `normal`, `compact`, `minimal_safe`.
- `section`: an indexed part of a local file; the index locates, reading
  re-opens the file.
- `anchor`: a random, path-independent identity for an indexed file.

## Heads

- `authority_head`: bound objectives, archived library, promoted claims,
  terminal completion.
- `execution_head`: frames, asks, evidence, candidates, windows, checkpoints.
  It may advance without changing authority.

## Objectives and claims

- `objective`: the six constitutive fields — `statement`, `domain`,
  `claim_scope`, `assumptions`, `evidence_standard`, `completion_standard`.
  Their digest is the objective commitment.
- `claim_scope`: the population a claim quantifies over (datasets/tasks,
  models/scales, seeds, inputs), its aggregation and its metric.
- `claim`: a statement with `strength`, `grade`, `scope`, `evidence_ids` and
  `cannot_imply`.
- `strength`: `universal`, `conditional`, `bounded`, `observation`.
- `grade`: `formal`, `certificate`, `exact_reproduction`, `bounded_empirical`,
  `numerical_evidence`.
- `effect`: derived, never assigned — `current`, `historical`, `needs_review`,
  `invalid`.
- `reviewer_kind`: `human`/`local` and `independent_model` count;
  `declared_independent` counts and stays distinguishable; `self` and `foreign`
  never count.

## Review and acceptance

- `verifier`: an independent reader of one frozen candidate and its complete
  dependencies. `PASS` grants eligibility, never acceptance.
- `cannot_imply`: the mandatory statement of what a result does not imply.
- `omitted_candidates`: how many candidates a search or screening did not show.
- `software_cannot_authenticate_reading`: always true.
- `semantic_completeness_proven`: always false.
