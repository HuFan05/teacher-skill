# Need discovery

A requester rarely arrives with a well-formed question. They arrive with a
symptom ("training keeps oscillating"), a half-remembered name, or a feeling
that something in a paper does not add up. Asking them to "state the problem
more precisely" hands the hardest part of the work to the person least equipped
to do it. Need discovery runs on every new request, before any answer, and it
keeps the decisions in code.

## The division of labour

| Party | May do |
| --- | --- |
| Skill | decide whether to ask; write the question; allow at most one question per request; search the requester's own material first; record the assumption used when it proceeds |
| model | classify with closed values; propose at most three readings, each ≤80 characters and anchored by a verbatim fragment of the request; say whether the readings lead to different work and whether the requester's material could settle it |
| requester | pick a reading with one keystroke, press Enter for the default, or add a sentence |

A model may not write the question, choose to ask, or decide that framing is finished.

## The frame draft

Exactly eight keys:

```json
{
  "kind": "concept_explanation | paper_understanding | method_comparison | experiment_debugging | research_question | reproduction | literature_search | implementation_help | learning_path | other",
  "stuck_point": "concept | assumption_condition | method_choice | implementation | experiment_computation | source_location | unclear",
  "clarity": "clear | ambiguous | underspecified",
  "depth": "brief | standard | deep",
  "interpretations": [{"text": "≤80 chars", "quote": "verbatim fragment of the request", "differs_by": "goal | object | scope | depth | output_form"}],
  "material_ambiguity": false,
  "investigation_can_resolve": false,
  "defaults": ["closed codes"]
}
```

Refused: a reading whose quote is not in the request, more than three readings,
duplicate readings, a reading longer than 80 characters or containing a link,
`clear` with more than one reading, an unknown value or key.

## The rule

1. One reading, or readings that would not lead to different work → proceed on
   reading 1 and show "我的理解：…" with the alternatives and `/switch N`.
2. Different work, and the requester's own material could settle it → the Skill
   searches the project library and the local index with each reading's quote,
   hands the model the candidates, and frames once more (at most once).
3. Still different work → ask one closed choice with option 1 preselected.
4. A frame never asks twice. After its one question it proceeds and states the
   assumption.

A digit picks a reading; Enter takes the default; any other text is new
information that extends the request and re-opens framing.

## Closed defaults

`standard_definitions`, `mainstream_current_practice`, `single_gpu_budget`,
`python_pytorch_stack`, `no_prior_context`, `user_notes_relevant`,
`answer_in_chinese`, `depth_standard`. Each maps to fixed text shown at the top
of the answer ("暂按：…"), so adopting a default introduces no model prose and
is always visible.

## Proactive offers after the answer

- Each answer may carry at most two likely next questions, chosen by kind; the
  requester continues with `/ask N` or ignores them.
- When three consecutive answered requests share kind and stuck point with
  overlapping wording, the Skill offers to turn the line of questions into a
  research objective or learning path. It is an offer, never an interruption.

## From questions to a research objective

`teacher.py engine -- intake-begin` asks for the six constitutive fields —
`statement`, `domain`, `claim_scope`, `assumptions`, `evidence_standard`,
`completion_standard` — in a fixed order, drawing on the requester's recent
requests. The Skill checks the fields; the requester confirms once
(`intake-commit`) or fills one directly (`intake-set`). Binding the objective is
the only action that advances authority from a conversation. `claim_scope` states the
population the claim quantifies over (datasets/tasks, models/scales, seeds,
inputs), the aggregation (mean±sd over N seeds, worst case, for all inputs) and
the metric.

The full route is `intake-begin` → `intake-turn` (repeat) / `intake-set` →
`intake-commit`: every field value is either a verbatim fragment of the
requester's own words or typed by the requester; the model never authors a
value. It takes up to six rounds and never commits an incomplete shape.
