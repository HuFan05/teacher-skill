# Formula, code and table map presentation

Use obsidian-vault-notes for map writing that carries formulas, and for final
validation. Formal records are immutable evidence; presentation replacements are
local derived notation and never constitute a research correction or review.

## Before generation

Use $...$ and $$...$$ for complete TeX: complexity bounds, losses, update rules,
probabilities and other formulas. Preserve existing TeX byte-for-byte: never run
Markdown backslash escaping or character truncation inside it. Keep code
identifiers, pseudocode, command lines, configuration keys and file names literal
in backtick spans or fenced blocks; they are code, not formulas, and are not
scanned as prose. Keep titles/link aliases plain and concise; put formulas in the
linked body. Avoid raw vertical bars in table formulas; use TeX delimiters such
as \lvert. A result table keeps each formula intact inside its cell; a figure is
reached through its reading-view object and exact hash, never redrawn in prose.

For plain-text formula notation in stored records (for example `x_t>=0`,
`softmax(z)_i` or `sum_{i} w_i` written without delimiters), prepare a UTF-8
JSON list outside the formal library. Each object has exactly source and tex
strings. Source is an exact formula span from the current record; tex is reviewed
equivalent LaTeX without dollars. Pass --formula-replacements to local map --out
or finish. Review constants, signs, indices, bounds, asymptotic notation and
claim scopes against the source. The tool does not infer equivalence or convert
arbitrary ASCII. Do not insert project-specific replacement tables into the Skill.

Replacements apply before summaries, outside existing formulas, code and links.
A formula is atomic during summarization: retain it whole or stop before it.
Never repair a clipped formula by guessing its missing suffix.
Unresolved plain-text formula notation, malformed delimiters/groups, escaped
entities and double-escaped control words block output. Repair the presentation,
then retry; do not hide research formulas in code or alter immutable source
objects. Code samples, pseudocode, object identifiers and source links remain
literal.

## Final acceptance

The map writer rereads final bytes, checks structure and reports the exact
map hash, formula count, rendering=not_checked and live_obsidian_checked=false.
Zero is legitimate only for prose-only maps. Recognizable formula notation
without delimiters is rejected even when no formulas were extracted.
Detection is heuristic: unrecognized notation still requires editorial review;
passing structure alone never certifies readable formulas.

After writing the exact map, run the obsidian-vault-notes formula (TeX) validator
with --live-mode off and its local links validator. Render every extracted
formula using local MathJax; bind results to the final map SHA-256 and require
no errors and equal extracted/rendered counts. Inspect a preview containing real
prose, code blocks and table cells, including long formulas. Recheck after any
repair. Keep reports and screenshots outside the Skill and formal research store.
Rendering validates display syntax, not the research claim.

Only explicit application-level verification authorizes --live-mode required.
An offline preview is not a check of the running Obsidian application.
Report missing rendering or visual evidence as unfinished delivery; never
describe zero formulas or structure-only success as successful rendering.

Generic Markdown exchange output retains its archival representation and does
not acquire a rendering claim. Evidence and review semantics are unchanged;
local finish applies the same formula gate as local map.
