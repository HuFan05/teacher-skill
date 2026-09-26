# CS/AI Note Style

Use this reference for CS/AI notes (algorithms, complexity statements, correctness arguments, experiment results, code, and formulas), technical sections in essays, and review of nontrivial technical claims.

## Rigor

- For nontrivial CS/AI results, such as complexity bounds, algorithm guarantees, published benchmark numbers, or claims about model behaviour, preserve or add source attribution for results that are not widely known.
- When adding or reorganizing source attribution, follow `references/source-citation-style.md`: direct online sources should usually appear as inline Markdown links at the claim they support, while print or non-direct historical sources belong in a final `## 资料来源` bullet list.
- If a derivation, complexity bound, correctness argument, experiment result, or remembered statement of a published result is uncertain, mark it explicitly. Do not smooth uncertainty into a confident correctness argument or a settled empirical finding.
- If you cannot establish or verify a claim, say so directly and leave it as a check item.
- Separate what is already in the note, what comes from retrieved adjacent notes, and what is a new inference.
- State the evidence grade a claim actually rests on, using the suite-wide names from strongest to weakest: `formal`, `certificate`, `exact_reproduction`, `bounded_empirical`, `numerical_evidence`. Code that ran or tests that passed verify only the boundary they checked; they never by themselves verify a scientific claim.
- Keep an experiment result next to what is needed to reproduce it: code revision, dataset and split, configuration and hyperparameters, seeds and seed count, metric definition, and software or hardware versions when they affect the number. Report the spread across seeds when more than one run exists; a single-seed number is one observation, not a stable result.

## External Computation

Use an external computation only when the technical check genuinely needs one. For mental arithmetic, a short direct derivation, or a claim that still needs a correctness argument rather than an experiment, do not add a tool call merely to create an appearance of verification.

- Load `$cs-ai-computation` for running code, an experiment, a benchmark, a complexity measurement, or a nontrivial symbolic or numerical calculation. Let it perform local-availability checks, backend and environment selection, execution, and independent verification.
- Preserve the Vault boundary: pass only the note material needed for the computation. `$cs-ai-computation` does not read or edit the Vault independently, and this Skill remains responsible for the guarded write and final note audit.
- Preserve reproducibility in the note or a linked computation record: identify the selected backend and version, exact input or code and its revision, environment and dependency versions, dataset and split, configuration, seeds, output, verification method, evidence grade, fallback reason, and technical interpretation.
- A floating-point, sampled, or single-run check is evidence only. A computation record validated for hashes and fields still does not establish that the technical claim or its evidence grade is correct.
- Independently inspect the tool output. Do not copy a successful run, a passing test suite, or a tool result into the note without checking that its configuration, inputs, and measured quantity match the claim.

## Claim State Labels

Use claim-state labels only when the status is genuinely useful for the reader, especially for argument sketches, unchecked claims, conjectures, unreproduced results, or newly added uncertain derivations. For a completed correctness argument, or a result whose evidence is already stated next to it, normally no label is needed.

Do not put lines such as `状态：已论证` inside formal result callouts like `[!note] 定义`, `[!note] 算法`, `[!note] 命题`, `[!note] 复杂度`, or equivalent local variants. A formal result callout should contain the statement, algorithm, or bound itself; put uncertainty notes or status notes in surrounding prose only when they prevent a real misunderstanding.

When a status label is needed outside the formal result callout, use one of:

- `已论证`
- `论证草图`
- `待检查`
- `待复现`
- `猜想/启发式`

The goal is to prevent a future reader from mistaking a heuristic, a sketch, or a single reported run for an established result.

## Formatting

- Use `$...$` for inline formulas and `$$...$$` for display formulas.
- Do not introduce `\(...\)` or `\[...\]`.
- After the final write, always run `scripts/validate_obsidian_formulas.py --live-mode off` on the exact disk file. Its mandatory offline gate checks Obsidian delimiters and TeX group structure without requiring the desktop app. Use `--live-mode required` only when the user explicitly requests rendering inside the currently running Obsidian application. CLI unavailability must be reported as `live_check_unavailable` and must not trigger computer use or other GUI automation unless the user separately requests GUI interaction.
- Keep formulas compact enough for Obsidian Reading Mode; avoid unnecessary spaces and line breaks inside TeX. `lint_note_article_revision.py` treats avoidable CJK spaces around inline formulas, padded `$...$`/`$$...$$`, `\(...\)`/`\[...\]`, and split-out short display formulas as hard warnings.
- Use compact paragraph display formulas when a short single formula is part of the surrounding sentence and Obsidian can render it correctly. Write `梯度下降按$$\theta_{t+1}=\theta_t-\eta\nabla L(\theta_t)$$更新参数，其中$\eta$是学习率` instead of splitting the same short formula into a separated block with blank lines. Keep separated display blocks for long formulas, aligned derivations, multi-line equations, important standalone statements, matrices, cases, or places where compact form would hurt rendering or readability.
- Use `$$\begin{aligned}...\end{aligned}$$` for longer aligned derivations.
- Keep explanatory prose outside formulas.
- Put code, commands, configuration files, and program output in fenced code blocks with a language tag where one applies. Keep them byte-exact; explain them in surrounding prose instead of paraphrasing code into the block.
- In abstract technical prose, avoid using "费用" unless the topic is genuinely about money, price, billing, accounting, economics, or an explicit source term that must be preserved. For complexity, resource, or approximation arguments, name the actual quantity being controlled, such as "时间复杂度", "比较次数", "显存占用", "通信量", "误差上界", or "估计量".

### TeX Payload Transport

- Treat every backslash in generated TeX as payload data. If an orchestration language interprets string escapes, send TeX through a raw or literal channel instead of an ordinary interpreted string. For example, JavaScript payloads use `String.raw` or an equivalent byte-preserving mechanism; an ordinary quoted JavaScript string is not allowed merely because most commands happened to be double-escaped.
- Stage TeX-bearing edit text before the Vault dry-run, reread the staged bytes, and exact-check the control sequences introduced by the changed span. The dry-run hash proves what the editor received, not what the agent intended before a host language interpreted the payload.
- The guarded `vault_edit.py` dry-run rejects any newly introduced control-word shadow: a formula token `name` appearing bare when `\name` occurs elsewhere in the prospective note. The post-write validator reports all such shadows in `transport_audit` and can reject them under `--fail-on-transport-shadow` for a new or intentionally cleaned note. Differential enforcement lets unrelated edits preserve pre-existing formulas without silently allowing a new transport defect. This is a general detector for likely backslash loss, not a replacement for literal transport: a uniquely occurring deleted backslash is information already lost and cannot always be reconstructed from final TeX alone.

## Callouts And Structure

- Preserve local labels such as `想法`, `算法`, `命题`, `例子`, `问题`, `反例`, `实验`, `技巧`, and `结论`.
- Preserve common `###`/`####` numbered structure.
- Put examples in `[!example]` callouts when the surrounding note uses example cards.
- Put counterexamples / 反例, including failing inputs, in `[!error]` callouts, not `[!example]`.
- Keep formal result callouts semantically pure. Do not place history, motivation, examples, or commentary inside callouts titled `定义`, `算法`, `命题`, `复杂度`, or `实验结果`; put those after the formal block.
- For an algorithm, keep the input/output contract, the pseudocode or code, the correctness argument, and the complexity statement as distinguishable parts, and state the cost model (for example comparisons, word-RAM operations, or GPU memory) that a complexity bound refers to.

## Numbering

- Maintain one coherent numbering scheme across the edited scope.
- Do not add an unnumbered technical item when surrounding comparable items are numbered.
- Choose numbers by logical position, not just the next visible line.
- If correct renumbering would touch a large portion of the note or risk breaking external references, pause and report the issue instead of silently creating inconsistent numbering.

## `tool_idea` Notes

Treat notes tagged `tool_idea` as method/strategy notes. Preserve the distinction between:

- the reusable method or idea itself;
- the concrete algorithms, systems, models, or problems used as examples;
- the current question's relation to that method.

Do not flatten a `tool_idea` note into an ordinary result or exercise summary unless the user explicitly asks.
