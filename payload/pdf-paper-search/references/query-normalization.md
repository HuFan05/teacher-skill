# Query Normalization

Use this reference when a CS/AI query contains notation, bilingual phrasing, OCR noise, model or dataset names, or a paper-style statement sentence.

## Canonicalization Contract

`paper-canonical/v1` is the query-side canonicalizer. The PDF index has its own
format version; interoperability is tested through
`paper-anchor-contract-v1.json` (`ascii-paper-anchor/v1`) rather than by falsely
claiming that independent implementations are byte-identical.

The bridge preserves at least `theta`, `eta`, `nabla`, `mu`, `beta`, `epsilon`,
`lambda`, `alpha`, `omega`, `softmax`, `sqrt`, and digit-bearing tokens such as
`n2`. It folds diacritics, maps Unicode/TeX Greek variants, and applies a small
Chinese↔English CS/AI term bridge. Contextual interpretation, such as reading
`Ω(·)` as a lower bound, belongs to this Skill and is never injected into
ordinary library text.

## 1. Preserve Hard Concepts

Do not relax the query too early.

Examples of distinctions that must be preserved during search and verification:

- `lower bound` vs `upper bound`
- `worst case` vs `average case` vs `amortized`
- `deterministic` vs `randomized`
- `NP-hard` vs `NP-complete`
- `self-attention` vs `cross-attention`
- `on-policy` vs `off-policy`, `model-based` vs `model-free`
- `self-supervised` vs `supervised` vs `unsupervised`
- `convex` vs `nonconvex`
- `top-1` vs `top-5`, and one model variant vs another (`ResNet-50` vs `ResNet-101`)

If a candidate only states a weaker or different claim, it is not an `exact hit`.

## 2. Build a Small Alias Set

Default alias set:

- raw user query
- one normalized ASCII rendering
- one English rendering if the query is in Chinese
- `1-4` domain aliases

Avoid noisy standalone tokens such as `n`, `x`, `the`, `1`, `f`, or `model`.

For theorem/lemma/definition location queries, preserve a clause from the head and a clause from the tail of the statement when possible. Typical anchors are:

- the bound direction (`lower bound`, `upper bound`)
- the case (`worst case`, `amortized`)
- the computational model (`comparison-based`, `oracle`, `streaming`)
- the setting (`on-policy`, `convex`)

For numbered-object source-location queries, preserve the label and number rather than flattening them into bare words. Typical anchors are:

- `Algorithm 2`
- `Table 3`
- `Figure 1`
- `Eq. (4)`
- an arXiv identifier such as `1706.03762`

For named-method queries, keep the method name and its component or result family glued together. Example:

- `scaled dot-product attention`
- not bare `attention`

For result queries, keep the model variant, dataset, and metric together, such as:

- `ResNet-50 ImageNet top-1`
- `Transformer WMT14 BLEU`

but keep the total alias count compact.

When the query contains a recognized structural identity, also retain a
structural fingerprint. The current bounded bridge covers complexity bounds
(`O(·)`, `Θ(·)`, `Ω(·)`), the scaled dot-product attention formula
`softmax(QK^T/√d_k)V`, model variants such as `ResNet-50` or `BERT-base`,
dataset–metric pairs such as `ImageNet` + `top-1`, and reported decimal numbers
next to a dataset or metric. A candidate may only use that fingerprint as exact
evidence when the local statement, hard concepts, and requested page role also
match.

## 3. OCR and Encoding Tolerance

Expect at least these failure modes:

- Chinese text may appear either correctly or as mojibake
- numbers may appear as `28.4` or `2 8 . 4`, and labels as `Table 2` or `Tab. 2`
- superscripts and subscripts may be lost: `n²` becomes `n2`, `d_k` becomes `dk`
- `lg` and `log` both denote a logarithm in complexity statements
- fractions may break across lines, so `QK^T/√d_k` can extract as `QK T √ dk`
- subscripted model names may merge, so `BERT_BASE` can extract as `BERTBASE`

The search layer should normalize these when possible, but the verification step must still read the page itself.

## 4. Formula-to-Words

For formulas or paper statements, add one verbal rendering when helpful.

Examples:

- `softmax(QK^T/√d_k)V` -> `scaled dot-product attention`
- `Ω(n log n) comparisons` -> `comparison sorting lower bound` plus the structural fingerprint `complexity:omega:nlogn`
- `O(n^2)` for self-attention -> `self-attention quadratic time complexity` plus `complexity:o:n2`
- `θ ← θ − η ∇θ L(θ)` -> `gradient descent update`
- `-Σ y log p` -> `cross-entropy loss`
- `ResNet-50 76.1 top-1` -> `ResNet-50 ImageNet top-1 accuracy` plus `model:resnet-50` and `number:76.1`

## 5. Verification Rules

A result is stronger when:

- the page contains the same labeled theorem/lemma/definition statement
- or the page contains the same numbered algorithm box, table caption, figure caption, or numbered equation
- the key concepts occur in one local statement, not just on the same page
- the page is the original statement page rather than a related-work mention, abstract summary, proof page, or table of contents
- when duplicate statements are otherwise exact, the original paper outranks a
  survey, tutorial, slide deck, or lecture-note copy; a proof or appendix page
  does not outrank the statement it proves
- PDF page and printed page can both be reported

Downgrade confidence when:

- the match is driven by generic words only
- the page only lists a range like `Tables 2-4` or `Theorems 1-3`
- the page mixes key concepts that belong to different statements
- the query says `theorem`, but the page only gives a nearby lemma or a weaker variant
- the page cites or attributes the statement to other work
- OCR is poor enough that the statement cannot be verified directly

## 6. Regression Examples

Keep standing regressions for at least these phenomena:

- bound-direction and case distinctions (`lower bound` vs `upper bound`, `worst case` vs `amortized`)
- statement-page vs related-work or abstract distinctions for formal results
- model-variant, dataset, and metric distinctions in results tables
- direct-PDF fallback cases where the exact source exists locally but is outside the current SQLite shelves

## 7. Reporting Template

Keep the final report compact:

- source: paper or book title
- location: section if visible, PDF page, printed page if visible
- match type: `exact hit` / `near-exact` / `nearby material`
- why: labeled statement, numbered object, exact sentence, or closest verified relation
- confidence: high / medium / low
- gap: OCR limitation, ranking caveat, or missing-index note if relevant
