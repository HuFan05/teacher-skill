# Personal Knowledge Search Evaluation Contract

Read this contract before creating, extending, freezing, or running a formal
search evaluation for this Skill. Formal evaluation is optional and user-run.
This file defines the domain output and coverage requirements. This Skill does
not ship an evaluation runner: the user's own evaluation procedure owns dataset
isolation, runner/gold separation, review, baseline/candidate execution,
scoring, and any decision drawn from the scores.

## Task definition

The primary task is to retrieve a source and locate supporting evidence.
Dataset authors may ask for an explanation only when the frozen evidence
supports that explanation and it remains part of a realistic search request.
Do not convert a randomly selected passage into an unrelated derivation,
coding, or trivia question.

Freeze the intent before writing the query:

- target source or content claim;
- exact-source, whole-corpus, or fuzzy-recall mode;
- allowed title, author, topic, chapter, or quotation hints;
- answerability and expected coverage state;
- required locator and evidence fields;
- ambiguity and independently valid alternative sources;
- diagnostic role, kept evaluator-private.

## Required coverage

A representative dataset covers:

- exact-source lookup;
- whole-knowledge-base content retrieval;
- fuzzy recollection and paraphrase;
- multiple independently valid sources;
- scoped not-found behavior;
- PDF extraction states `no_text` and `error`;
- reproducible index anomalies or gaps;
- duplicate or same-named files;
- stable `KB-...` resource-ID resolution.

These categories do not have to share one score. Diagnostics remain separate
from the main retrieval score.

## Result contract

### PDF results

Each accepted PDF result supplies:

- knowledge-root-relative PDF path;
- one-based PDF page number or page range;
- exact supporting excerpt;
- match reason;
- extraction status;
- an index-coverage or extraction warning when coverage is incomplete.

A printed page label is optional and must not replace the one-based PDF page.

### Vault results

Each accepted Vault result supplies the note title, Vault-relative note path,
heading or section, a one-based line range when available, exact supporting
text, and match reason. Vault locators and PDF page locators are different
schemas.

When a request searches both sources, group Vault results and PDF results
separately. Do not place a PDF page number in a Vault record or use a note line
range as a PDF locator.

### Multiple sources

For a multiple-source case, every source listed in gold must independently
satisfy the complete query. Two passages that each supply only half of the
answer are not two valid sources unless the runner task explicitly requests a
cross-source synthesis.

### Empty results and coverage

An empty result may say only that the target was not found in the checked,
currently indexed text. It must preserve the checked scope, query variants,
corpus snapshot, and reproducible search log in evaluator-private records. It
must not claim that the knowledge base absolutely lacks the target.

`no_text`, `error`, pending extraction, and an index gap are coverage states,
not negative relevance judgments. A runner-visible query must not reveal that
the expected behavior is abstention.

### Duplicate names and resource IDs

A same-named-file case requires stable relative paths or resource identities;
the evaluator must not accept a filename-only guess when multiple current
targets exist. A `KB-...` case verifies current registry resolution, status,
recommended Skill, and the failure behavior for unknown or missing IDs without
exposing the expected registry status in the runner query.

## Review and privacy

Apply this retrieval quality gate in the user-run evaluation:

```text
intent contract -> generation -> runner-only cold read
-> independent evidence review -> incident ledger -> same-class audit
```

The evidence reviewer relocates every answerable item from the frozen corpus
snapshot and verifies hashes, locators, excerpts, extraction state, and minimum
answer points. A multi-source case requires every listed source to pass that
review independently.

Real notes, PDF passages, runner questions, gold records, query IDs, paths,
hashes, search logs, and private incident histories stay outside this Skill.
This reference may contain only sanitized rules and synthetic examples.

## Evaluation routing

- Use the bundled benchmark for ordinary latency diagnosis; it measures time
  and result counts, not ranking quality.
- Use a separate, user-run evaluation for formal blind datasets, isolated
  baseline/candidate runs, and human scoring; keep its runner, gold, and
  scores outside this Skill.

Smoke-test output is diagnostic evidence even when all cases pass. It cannot
be relabeled as formal evaluation evidence after the fact.
