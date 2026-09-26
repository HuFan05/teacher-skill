# Large-Edit Verification

Use this workflow only for a genuinely large, non-mechanical Vault edit. Its canonical lifecycle object is `large_edit_verification_gate`.

## Trigger Boundary

The gate activates only when both conditions hold:

1. the user explicitly requests a large, batch, project-wide, comprehensive, book-scale, or systematic revision; and
2. the frozen candidate meets at least one substantive threshold:
   - at least three Markdown targets, with substantive prose changes in at least two;
   - one authorized prose scope contains at least 6,000 non-whitespace characters, while the candidate changes at least 2,500 non-whitespace characters and at least 30% of that scope;
   - whole-scope coherence must be judged across at least five notes; or
   - whole-scope coherence must be judged across at least three top-level sections and at least 2,500 changed non-whitespace characters.

The following exclusions override the thresholds: a single paragraph; a few formula lines; one local subsection; a narrow heading, link, citation, frontmatter, or typo repair; and a mechanical batch whose correctness is decided by exact replacements and deterministic validators. Project importance, a long source note, or the presence of dense formulas or code does not make an edit large.

Use `scripts/large_edit_review.py classify --input <metrics.json>` for the deterministic part of this decision. Record the result and trigger evidence in the `EditPlan`. Borderline cases stay on the ordinary guarded-write path unless the user explicitly asks to invoke the verifier for that task.

## Candidate First

Do not draft the large edit directly into the live Vault. Prepare the complete candidate in a dedicated task-local staging directory, preserving relative Markdown paths and placing no unrelated Markdown there. Start one persistent gate with `large_edit_review.py begin --candidate-root <current-candidate>`, then freeze an ordered candidate inventory with `large_edit_review.py inventory`. Inventory discovers every Markdown file recursively; callers cannot omit a staged Markdown target. It contains relative paths, byte counts, file hashes, and one aggregate hash, never note bodies.

Before any subagent call, run every applicable deterministic gate against the candidate:

- strict UTF-8 and path containment;
- Markdown/frontmatter and task-specific assertions;
- `validate_obsidian_formulas.py --live-mode off`;
- `validate_obsidian_links.py --live-mode off` against an appropriate staged/Vault resolution plan;
- resource-reference checks;
- article, formula, or source lint;
- narrow diff, semantic sentinels, and `git diff --check` or an equivalent staged diff check.

A mechanically invalid candidate does not consume a verifier round. Repair it locally first. Record the passing or justified `NOT_APPLICABLE` result and a local evidence hash for every closed deterministic check in an `obsidian-large-edit-deterministic-receipt/v1` receipt bound to that round's candidate-inventory hash.

## Independent Verifier

Spawn one independent subagent as verifier. Do not give it the drafting agent's diagnosis, preferred verdict, previous review narrative, or hidden answer. Give it:

- the frozen acceptance checklist;
- changed spans with adjacent paragraphs by default;
- exact terminology definitions needed by those spans;
- the candidate inventory hash and round number;
- the original and candidate text needed to judge meaning preservation.

Use the complete user-authorized style scope only when whole-scope coherence is an acceptance gate. State that reason in the receipt. Never give the verifier independent Vault tools or permission, linked-note bodies, unrelated sections, attachments, credentials, raw logs, external resources, unmanaged absolute paths, or unrelated frontmatter. The verifier is read-only and cannot edit the live Vault.

The verifier checks:

- facts, formulas, quotations, source status, negation, causality, chronology, and epistemic strength are preserved;
- Chinese prose matches the note's genre and reliable author voice;
- established Chinese terms are used where stable, while uncertain or rare forced translations remain in English with a short first-use gloss;
- canonical labels and technical notation are consistent across the reviewed scope;
- headings, lists, callouts, wikilinks, embeds, formula delimiters, code blocks, and source boundaries remain readable and intentional;
- the candidate makes sense without the drafting chat and does not expose internal workflow jargon to the reader.

Formatting checks reported by the verifier are semantic review, not a replacement for the deterministic validators.

## Three-Round Loop

Each verifier receipt is bound to one exact candidate-inventory hash. The fixed `.obsidian-large-edit-gate-state.json` inside the candidate directory records a random gate identity, the next round, prior candidate hashes and verdicts, and the terminal state. It contains no note body. Do not delete, replace, move, or recreate this state to escape a prior verdict.

1. `PASS`: require every closed semantic check to pass and no blocking finding. The exact candidate becomes eligible for the ordinary guarded live-write flow.
2. `FAIL` in round one or two: revise the candidate, rerun all deterministic checks, create a new inventory, and start the next round. Retain earlier inventory/check/verifier triples as failure history, but only the final triple can authorize the current candidate.
3. `FAIL` in round three: set `final_state=accepted_after_three_reviews`, retain every unresolved finding, and make that exact third-round candidate eligible for the guarded live-write flow. This is the user's explicit fallback rule; report it as fallback acceptance, never as verifier approval.
4. Missing, malformed, stale, non-independent, or unavailable verification does not count as a completed round and leaves the gate active and write-ineligible. Retry the same round when a verifier becomes available. Do not fabricate a `FAIL` receipt merely to reach the fallback.

Allow at most three automatic rounds. Do not ask the verifier to fix its own findings. The drafting agent applies revisions; a new independent review judges the new candidate.

Call the same persistent gate once per round: `large_edit_review.py gate --candidate-root <current-candidate> --inventory <this-round-inventory.json> --deterministic-receipt <this-round-checks.json> --receipt <this-round-review.json>`. The gate recomputes the inventory aggregate hash, binds both receipts to the expected round and candidate, rediscovers every current Markdown file, and rehashes all of them before it atomically advances the state. A new round after `FAIL` requires changed candidate bytes. A `write_eligible=true` result authorizes only the next ordinary guarded-write step; live hashes, race checks, post-write validators, and `EditAudit` still apply.

## Deterministic Receipt

Use the closed schema `obsidian-large-edit-deterministic-receipt/v1`. It contains `schema`, `gate_id`, `round`, `candidate_inventory_sha256`, and `checks`. `gate_id` must match the persistent task-local state. `checks` contains exactly `utf8_and_path_containment`, `markdown_and_frontmatter`, `formulas`, `wikilinks`, `resource_references`, `task_specific_lint`, and `diff_and_semantic_sentinels`. Each check contains exactly:

```json
{
  "status": "PASS",
  "evidence_sha256": "64 lowercase hex characters",
  "reason": ""
}
```

`status` is `PASS` or `NOT_APPLICABLE`; the latter requires a concrete reason. An evidence hash binds the locally retained machine output or reviewed diff receipt. It does not put note bodies or validator logs into the gate response.

## Verifier Receipt

Use the closed schema `obsidian-large-edit-verifier-receipt/v1`:

```json
{
  "schema": "obsidian-large-edit-verifier-receipt/v1",
  "gate_id": "32 lowercase hexadecimal characters",
  "round": 1,
  "candidate_inventory_sha256": "64 lowercase hex characters",
  "verdict": "PASS",
  "independent_subagent": true,
  "context": {
    "mode": "changed_spans",
    "whole_scope_reason": "",
    "excluded_data_classes": [
      "attachments",
      "credentials",
      "external_resources",
      "linked_note_bodies",
      "raw_logs",
      "unrelated_frontmatter",
      "unrelated_sections",
      "unmanaged_absolute_paths"
    ]
  },
  "checks": {
    "meaning_preservation": "PASS",
    "language_and_author_voice": "PASS",
    "terminology_and_notation": "PASS",
    "format_and_reader_coherence": "PASS"
  },
  "findings": []
}
```

Each finding uses exactly `relative_path`, `location`, `category`, `severity`, and `message`. `relative_path` must be a safe relative Markdown path present in the complete candidate inventory; absolute, parent-traversing, and unmanaged paths are rejected. `severity` is `blocking` or `advisory`. Receipt text must not contain note bodies or secrets; findings quote only the minimum phrase needed to locate a problem.

Each semantic check value is `PASS` or `FAIL`. `accepted_after_three_reviews` is a gate lifecycle state after the third completed failed round, not a verifier check value or a claim that the verifier passed the candidate.

## Audit

When the gate applies, add to the ordinary `EditAudit`: classification metrics, trigger evidence, candidate-inventory hash, deterministic gate results, context mode and excluded data classes, round count, each verdict, final state, unresolved findings, and whether the exact passing candidate was subsequently written. When it does not apply, record `ordinary_edit` and the exclusion or unmet threshold; do not spawn a verifier merely to confirm non-triggering.
