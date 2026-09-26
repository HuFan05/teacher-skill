# Complete browser reading

Use `assets/research-map/` and the public `scripts/crs_project.py upgrade` route
for a requested complete project/map upgrade. `crs.py map` and `finish` retain
their Markdown behavior; their pending browser state cannot complete that
request. An explicit user choice of another format or design takes precedence.

## Shared reading structure

Keep the light paper, green-grey sidebar, restrained cards, content width,
responsive navigation and printing in the bundled template. Use this order:
research home (`index`), motivation and routes (`route-organization`), manuscripts
(`manuscripts`), goals (`goals`), overview (`overview`), attempts (`attempts`), records
(`records`), assets (`assets`), reviews (`reviews`), sources (`sources`). Empty
categories explain what is missing. Project material never belongs in the
reusable template or its tests.

The homepage first explains the actual research question and its complete
domain, claim scope (population, aggregation and metric) and assumptions. Its
fixed sections are question, completion, conclusions, progress, obstacles,
existing next directions and complete material. Cite exact supporting records or
manuscript sections for substantive claims. Separate established, conditional,
observed and open status; identify source-fidelity or self-review limits. Record
counts, hashes and release notes do not replace research information. Missing
evidence is stated explicitly, without inventing a conclusion or a future
research direction.

The enclosing launcher opens this research homepage directly. The Markdown
entrance is generated from the same home model; the AI manifest binds that
delivery and the existing sole formal library. See [project layout](project-layout.md).

## Prepared model

`crs-web-content-complete/v1` has exactly `schema`, `title`, `subtitle`, `snapshot`,
`status`, `pages`, `home`, `manuscript_pages`, and `notation_review`.

Each page has exactly `id`, `title`, `group`, `body`, `body_zh`. The first body
is the complete English manuscript for a manuscript page; the second is its
complete Chinese reading translation. Manuscripts are proofs and correctness
arguments, algorithm descriptions and experiment reports. Preserve original
manuscripts, definitions, conditions, derivations, algorithms and pseudocode,
experiment configurations, result tables and figures, failures, sources and
review scope. Historical source-language evidence remains distinct from later
reading translations. Non-manuscript source/record pages may retain their source
language.

`home` has exactly `question`, `completion`, `conclusions`, `progress`,
`obstacles`, `next_steps`, `materials`. Each contains complete `en` and `zh`
semantic HTML with a specific supporting link. Generate the two index bodies
using `crs_home.render_home`; the builder checks them against this model.
`manuscript_pages` lists every complete manuscript page. IDs beginning
`manuscript-` or ending `-manuscript` cannot be silently omitted; include other
manuscript IDs too. Every listed manuscript requires both bodies and retrievable
source links.

Use semantic HTML, headings below h1, lists, tables, figures (`figure`, `img`,
`figcaption`) and relative local links. No scripts, inline styles, event
attributes or remote embeds belong in content. Use
`<span class="formula" data-tex="…"></span>` for an inline formula and
additionally `data-display="true"` for a display formula. Escape HTML attributes
without changing the TeX. Put code and pseudocode in `pre` or `code`; their text
is literal presentation and is not scanned as formula prose. Full original
attachments remain under `sources/`; accepted types are Markdown, text, JSON,
CSV, PDF, PNG, JPG and ZIP. Never hide a proof, argument or experiment report in
a generic attachment in order to avoid complete reading or translation.

## Source-faithful formula, code and table preparation

Inspect unmarked formula notation, including headings, lists, tables and
both languages. Existing marked formulas and zero renderer errors do not
account for expressions the renderer never received. Check whether Markdown
has already interpreted a subscript or star as emphasis. Return to the exact
source before repairing such damage.

Prepare exact reviewed edits outside the Skill. `crs_web_formula.apply_exact_edits`
accepts `{before, after, count, reason}` and rejects occurrence drift. It does
not infer meaning. Review complex expressions and conditions against their
source; do not apply guessed global substitutions or change formal bytes.

`notation_review` has exactly `exemptions` and `page_reviews`. Each exemption
has the exact UTF-8 text `sha256` and a specific `reason` explaining a literal
identifier or path that must stay in prose. Do not exempt formula prose or turn
it into code; genuine code and pseudocode belong in `pre`/`code`. Each
page/language review has `page`, `language`, `body_sha256`, `formula_prose`
(boolean), and `reason`. Cover every active body; formula prose with zero
formulas is blocked. The high-signal detector (TeX control words, comparison
operators, subscripted or snake_case identifiers in prose, calls such as
`log(` or `softmax(`) rejects unresolved notation but does not recognize every
possible expression. Source-aligned editorial review remains necessary,
including short variables that no heuristic can reliably classify without
context.

Compare formulas, claim scopes and conditions across complete translations.
The complete contract binds the reviewed bodies rather than pretending equal
occurrence counts prove equivalent translations; the basic model requires equal
formula-token counts across languages. A repeated formula may occur a different
number of times in grammatical translations; this requires actual source
comparison, never padding formulas to satisfy a counter.

## Build, verify and deliver

Use local MathJax `tex-svg.js` and its required dependencies; no CDN or remote
translation service. A site without formulas needs no renderer. Body pages load
individually, and search loads on demand. Manuscripts default to English; a
chosen language is remembered across the current URL, reload, contents,
navigation and search. The root launcher is part of the tested path, not merely
an internal manuscript-page test.

`crs_web.py build/check` remain useful structural commands and explicitly
return `delivery_complete=false`, `rendering=not_checked`. The complete route
in `crs_project.py upgrade` invokes the bundled `crs_browser.cjs` with a local
Node/Playwright installation. It renders every formula-bearing, manuscript and
home page/language, checks extracted/rendered counts and actual MathJax errors,
verifies offline search, language reload/navigation, anchors, narrow layout and
print mode, and records representative screenshot hashes. It does not download
a browser. Pass `--browser-channel` (for example `chromium`) to `upgrade` on a
host without the default `msedge` channel.

Inspect actual screenshots and representative long-form prose, long formulas,
code blocks and tables in both languages. The visual review has schema
`crs-visual-review/v1`, exact `browser_report_sha256`, the screenshot filename
to hash map, `reviewer`, `limitations`, and checks `home_question_progress_obstacles`,
`inline_and_display_formulas`, `long_formula_and_table_layout`, `both_manuscript_languages`,
`desktop_narrow_and_print`. All checks must be `passed` before publication.
Missing or failed visual evidence leaves the same operation pending with a
nonzero result; it never grants research review authority.

Publish only after source, candidate and browser bindings pass. Reopen the
actual installed/project launchers afterward; verify source snapshots and
retained links. Report content completeness, display correctness and the
original research trust separately. Reuse unchanged source translations
and evidence; only changed dependencies invalidate an applicable prior check.

## Basic model and research boundaries

The basic `crs-web-content/v1` model (without `home`, `manuscript_pages` and
`notation_review`) is accepted by the low-level builder/checker. It cannot
satisfy the complete-upgrade entry. Formal research schemas, HEAD, immutable
objectives, source objects and review meanings are unaffected by either model.
Derived route order follows stored continuation/dependency links, including
disputes and gaps; a website conversion does not create relations, research
evidence, or a new research run.


## Reader-facing manuscript directory and inline layout

Complete deliveries distinguish full manuscripts and substantive review reports from
stored result summaries, intake material and history. The manuscripts page provides
one `section.reading-entry[data-page]` per `manuscript_pages` identity in each language:
a concrete distinct subject in `h3`, a source-bound `p.reading-scope`, an explicit
`p.reading-status`, and links to the complete manuscript and original `sources/`
file. This structure prevents empty or generic lists, but cannot certify the truth
or usefulness of the chosen summaries: inspect their sources and real reading.
Keep archival result choices under records. Display source context or page position
for generic archive titles. A heading mentioning independent verification grants
no review status; show the actual stored scope without promoting historical claims.

Short inline formulas remain in normal text flow without scroll controls.
Only formula SVG content wider than its containing text block gets a scroll
region and hint. The hint must never participate in the width decision. Verify
all affected project sites in both languages, narrow and desktop viewports, repeated
resize and zoom, and representative print layouts. Check ordinary short fractions
as well as genuinely wide expressions. A zero renderer-error result is insufficient.


Current research delivery uses external paths: provide the actual formal project to build/validate, resolve the whole enclosing scope, and keep complete site/source-copy output outside it. Project roles retain references. A structural web check alone is not recursive content conformity or completed browser/visual verification.
