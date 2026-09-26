# Editing Protocol

Use this reference when creating or modifying real Vault Markdown.

## Before Editing

- Confirm `edit_allowed` and the exact target path.
- Read the current target from disk; do not use indexed text as an editing baseline.
- From the Vault root, inspect `git status --short` and the target's own narrow diff.
- Treat every pre-existing target hunk as user-owned.
- For non-mechanical work, record an `EditPlan`: targets, sections read, sections changed, linked bodies, checks, commit status, and `style_scope`. An explicit naturalization request uses the scope the user actually named: a whole article or section covers that complete target, while a named sentence or paragraph stays local except for adjacent transition repair. For an ordinary content edit, limit it to the changed span, adjacent paragraphs, and necessary bridge sentences.

An explicit request to modify, update, write back, or create a clearly identified note authorizes the dry-run and matching guarded write. Ask again only when the file scope, body-read scope, or commit scope must expand.

## Large-Edit Boundary

Before a non-mechanical write, classify the requested change under [large-edit-verification.md](large-edit-verification.md). Do not infer a large edit from the mere presence of formulas or code, long source notes, or a generally important project. A single paragraph, a few formula lines, one local subsection, a narrow heading/link repair, or a small mechanical replacement stays on the ordinary guarded-write path.

For a qualifying large edit, prepare the complete candidate outside the live Vault, run every deterministic check against that candidate, and obtain a hash- and gate-bound independent subagent verdict before the first live write. The verifier cannot edit the live Vault. A passing round makes the candidate eligible immediately. After three completed failed rounds, the third-round candidate becomes `accepted_after_three_reviews` and may enter the guarded write flow; retain all unresolved findings and do not describe the fallback as verifier approval.

## Mechanical Editor Contract

Use `scripts/vault_edit.py` for stable operations: section insertion, prepend/append, section replacement or deletion, heading rename, exact text replacement, simple frontmatter property changes, and explicit normalization.

The script must:

- default to dry-run;
- refuse targets outside the configured Vault unless a deliberate test passes `--allow-outside-vault`;
- accept only Markdown unless a deliberate test passes `--allow-non-md`;
- decode the target as strict UTF-8 or UTF-8 with BOM; invalid bytes are a hard error;
- calculate text, SHA-256, BOM, mtime, and line-ending metadata from one raw-byte snapshot;
- preserve all bytes outside the edited span for every non-`normalize` operation;
- preserve CRLF, mixed line endings, Markdown hard-break spaces, frontmatter, media, wikilinks, block ids, and unrelated dirty content;
- normalize generated payload boundaries only, using the nearby line-ending style;
- recheck the current target immediately before replacement and abort if it changed;
- write through a same-directory temporary file and atomic replacement;
- return structured JSON without note bodies.

`normalize` is the only whole-file normalization operation. It must report `semantic_change_possible=true`; do not use it as an incidental step in another edit.

## Guarded Write Flow

1. For TeX-bearing payloads, use the raw/literal transport and staged-byte reread required by `references/cs-ai-note-style.md`. Record the changed span's TeX control sequences as exact post-write sentinels.
2. Run the operation without `--write`.
3. Inspect `changed`, line counts, heading matches, warnings, `pre_sha256`, mtime, line endings, BOM, and the planned target.
4. Inspect the narrow Git diff that the proposed edit should produce when practical.
5. Rerun the same operation with `--expect-sha256 <pre_sha256> --write`.
6. Require `applied=true`, `race_check=passed`, and a valid `post_sha256`.
7. Reopen or check the target, verify the recorded TeX sentinels, and inspect the final narrow diff.

Hash mismatch, mtime mismatch, invalid UTF-8, complex frontmatter, ambiguous heading, duplicate protected insertion, or concurrent change must return nonzero and leave the target unchanged.

## Sandbox Approval Path

The dry-run and the matching guarded write are already one authorized Vault edit when `edit_allowed` is active. If the Vault is outside the current writable roots:

1. Keep the dry-run result and `pre_sha256`.
2. Issue the platform's narrow approval or escalation request for the exact write command that includes `--expect-sha256 <pre_sha256> --write`.
3. In automatic-review or `approve for me` mode, allow the platform reviewer to decide the request and continue when approved.
4. If an un-escalated attempt already failed because of the sandbox, rerun the same scoped write with escalation instead of converting the failure into a user-facing claim of no permission.
5. Report the task as blocked only when the escalation interface is unavailable, the request is explicitly rejected, or the escalated command fails. Distinguish that failure from missing Vault content authorization.

Do not broaden the command, target, note scope, or commit scope merely to make approval easier.

## Frontmatter Boundary

Mechanical frontmatter operations support only:

- simple scalar values;
- simple block lists.

Reject the operation when the selected property or frontmatter contains a block scalar, nested mapping, flow mapping, YAML anchor, alias, explicit tag, or another construct the editor cannot round-trip safely. Report the reason and use a reviewed custom patch instead of guessing.

## Dirty Target Protection

The current worktree is the source of truth. `HEAD`, an index row, a copied file, or earlier tool output may be used only as a narrow comparison source.

- Never rebuild the entire target from `HEAD` unless the user explicitly requests that restore.
- Repair the smallest damaged span after an agent error.
- When a whole-file rewrite is genuinely requested, account for every removed pre-existing hunk before writing.
- After writing, confirm that plan-external frontmatter, links, media, headings, block ids, highlights, and introductory metadata remain present.

## Note Formatting

- Preserve the local note family, voice, heading levels, callouts, active numbering, wikilinks, embeds, highlights, and source boundaries.
- Make the smallest change that satisfies the request unless a rewrite was requested.
- Do not begin a new or rewritten note with an H1 identical to its filename.
- Use `[[target|alias]]` when a long wikilink harms readability; preserve anchors and keep a Chinese alias within ten characters when practical.
- Inside a Markdown table cell, escape a wikilink alias or image-size separator: use `[[target\|alias]]` or `![[image.png\|300]]`. An unescaped `|` terminates the cell before Obsidian can interpret the complete link or image embed; target existence alone does not make the rendered result valid.
- Put internal numbered references in Chinese quotation marks, such as “命题1.1” and “第2节”.
- Follow `references/article-revision-style.md` for prose, `references/cs-ai-note-style.md` for CS/AI technical content, and `references/source-citation-style.md` when sources change.

Existing-note highlighting and provenance remain task-specific. Do not add or remove `==...==` or provenance markers unless the active request or established note workflow calls for them.

## After Editing

- Run `python "<SKILL_ROOT>\scripts\validate_obsidian_links.py" "<FINAL_NOTE.md>" --vault-root "<VAULT_ROOT>" --live-mode off` against every final Markdown file. The mandatory filesystem pass checks every wikilink and embed for a unique target and verifies any heading or block anchor. A broken or ambiguous link is a hard failure, including when the edit added or changed the link.
- When a changed link or image embed is inside a Markdown table, also inspect the final source and confirm every wikilink alias or image modifier separator is `\|`; the filesystem validator proves target resolution, not table-cell parsing.
- Run `python "<SKILL_ROOT>\scripts\validate_obsidian_formulas.py" "<FINAL_NOTE.md>" --live-mode off` against every final Markdown file after the last write. This is mandatory even when the edit did not intentionally touch formulas. The offline gate checks delimiters and TeX group structure, while `vault_edit.py` rejects newly introduced control-word shadows and the validator reports all remaining shadows in `transport_audit`. Use `--fail-on-transport-shadow` for a new note or an intentionally cleaned note that must contain none; a note with no formulas returns a passing `not_needed_no_formulas` result.
- Repair offline failures through the guarded write flow and repeat the validator. Delimiter counting by the agent, prose lint, or visual inspection is not a substitute for these programs.
- Use `--live-mode required` for either validator only when the user explicitly requests validation inside the currently running Obsidian app. A live pass confirms application-state resolution or rendering in addition to the offline result. If CLI is unavailable, report `live_check_unavailable`; do not launch, inspect, click through, or configure Obsidian with computer use or other GUI automation unless the user separately and explicitly requests GUI interaction.
- `--live-mode auto` is available for a deliberately requested opportunistic probe, but it is not part of the default post-write workflow. Never present an offline pass or unavailable live probe as live Obsidian verification.
- Run the appropriate article/formula/source lint.
- For a long Chinese article, perform the full-scope sentence review in `references/article-revision-style.md`; lint warnings are prompts, not a substitute for that review.
- For automatic AI-style rewriting, follow `references/ai-style-rewrite.md`, run the article lint before and after rewriting, and use a pre-edit snapshot with `--baseline` when practical to compare semantic sentinels. The linter reports differences; it never rewrites the note.
- Run `vault_edit.py check` when applicable.
- Inspect the narrow target diff and run `git diff --check`.
- If the target was dirty beforehand, explicitly verify preservation of plan-external regions.
- Commit only under `commit_allowed`; add only task files. Never push.

## EditAudit

Report:

- files and sections changed;
- large-edit classification, trigger evidence, verifier round count, final verifier state, and candidate-inventory hash when the gate applies;
- permission package and whether scope expanded;
- dry-run `pre_sha256`, final `post_sha256`, and race-check result;
- preservation of pre-existing dirty hunks and plan-external content;
- Markdown semantic sentinels required by the task, such as block ids, source labels, wikilinks, delimiters, and expected occurrence counts;
- checks and their results;
- wikilink validation: checked-link count, filesystem issues, whether the running Obsidian metadata cache was checked, and any unavailable reason or repair pass;
- formula validation: program path, formula counts, offline backend status, final `ok` status, each repair/revalidation pass when the first run failed, and any explicitly requested Obsidian MathJax/CLI result;
- for long Chinese prose: the scan scope, risk-category counts, sentences changed, retained-term reasons, and sentences still needing human judgment;
- for automatic AI-style rewriting: `style_scope`, pre-edit and post-edit category counts, second-pass cold-read status, semantic-sentinel differences, and any `source_judgment_required` item;
- unresolved technical, source, YAML, or rollback uncertainty;
- highlights or provenance changes, if any;
- commit hash, or that changes remain uncommitted.
