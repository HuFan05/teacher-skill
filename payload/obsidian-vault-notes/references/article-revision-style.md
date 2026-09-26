# Article Revision Style

Use this reference when revising an existing article, essay, reflection note, or other long-form prose note in the Vault.

## Contents

- [Purpose](#purpose)
- [Required Local Context](#required-local-context)
- [Automatic Style Scope](#automatic-style-scope)
- [Author Voice Calibration](#author-voice-calibration)
- [Chat Leakage To Avoid](#chat-leakage-to-avoid)
- [Source And Foreign Terms](#source-and-foreign-terms)
- [Style Review Pass](#style-review-pass)
- [Final Audit](#final-audit)

## Purpose

Article edits should read as part of the article, not as an answer to the current chat. The future reader usually will not see the conversation that caused the edit, so new prose must be grounded in the article's own sequence of ideas.

When this reference says to explain a "mechanism", it means naming the concrete relation, cause, condition, tradeoff, or procedure. It does not mean the Chinese article prose should mechanically use the word "机制".

## Required Local Context

Before changing article prose, inspect:

- the target paragraph or callout;
- the immediately preceding and following paragraphs;
- the section heading and the section's argumentative role;
- whether the note is explanatory prose, reflective prose, source commentary, a draft essay, a CS/AI technical note, or an operational reference.
- which spans are reliable local author prose and which are quotations, copied sources, production instructions, placeholders, or suspected assistant expansion.

Do not use the user's latest wording as the opening frame for inserted prose unless the article itself is written as Q&A or dialogue.

## Automatic Style Scope

When the user explicitly asks to remove AI-style writing, naturalize, polish, or comprehensively revise the prose, load `references/ai-style-rewrite.md` and automatically rewrite the style scope the user named. A whole article or section request covers that complete target. A sentence or paragraph request stays local except for adjacent text needed to repair its transition. A request to inspect only overrides automatic writing.

For an ordinary content edit, automatically improve only the changed span, the immediately adjacent paragraphs, and bridge sentences needed to keep the edit coherent. Continue to cold-read the whole active style scope, but do not use a local content request as permission to rewrite unrelated sections.

Automatic rewriting does not require sentence-by-sentence confirmation. Preserve a sentence only when a safe rewrite would require a factual, technical, source-status, or author-position decision that the current task does not authorize; record that boundary in the final audit.

## Author Voice Calibration

For the user's personal Chinese long-form prose, read `references/author-voice-profile.md`. When the user has a saved profile, use its stable traits as a secondary guide after meaning, source safety, target genre, paragraph role, and reliable unedited prose in the current note.

The saved profile does not authorize reopening the corpus used to build it. Read another note body only when the user supplied it or the active task separately authorizes that exact note, folder, or file set. If the user authorizes new corpus sampling, use the `custom_read` package, enumerate and freeze the files first, do not follow links, and report which material was excluded from voice evidence.

Do not equate frequency with voice worth preserving. Separate:

- `stable`: a repeated functional habit that helps the author's reasoning;
- `genre-only`: useful in a test log, short reflection, source commentary, or video script but not transferable everywhere;
- `do-not-imitate`: old errors, assistant shells, production residue, copied prose, abstract packaging, or repeated template emphasis.

When the target note itself contains substantial AI-style prose, do not average the entire note into one voice. Calibrate from reliable local spans plus the saved profile, when one exists. Record the selected genre and `voice_basis` before drafting.

## Chat Leakage To Avoid

Common failure modes:

- starting with a term from the current chat before the article has introduced it;
- writing "this means...", "this term means...", or "can be understood as..." because the user asked what a word means;
- importing the user's objection as if it were already part of the article;
- adding second-person prose such as "you asked" or "you can understand this as";
- using meta-editing language such as "here I explain", "the previous version", or "this revision";
- using first-person perception filler such as "我越来越感觉到", "我逐渐意识到", "我突然发现", or "我开始觉得" when the sentence can state the claim directly;
- using evenly paced rhetorical question strings such as "问题从哪里来，谁需要结果，结果怎样验证，怎样保存和迭代" or "A是什么，B在哪里，C如何发生，D怎样延续". These fixed-width chained questions feel formulaic; replace them with one concrete sentence that names the actual relation, decision, or constraint.

Safer pattern:

1. Start from the article's existing claim or concrete relation.
2. Explain it in the article's own vocabulary.
3. Only then attach exact source wording, foreign terms, or a quotation.

For reflective essays, first-person statements are allowed when they carry real autobiographical information or a concrete experience. Do not use first-person feeling verbs as padding before a general claim. Prefer `能从新工具里获益的，往往是...` over `我越来越感觉到，真正能从新工具里获益的，往往是...`.

## Source And Foreign Terms

When a quoted English source contains a term that needs glossing, do not begin the Chinese prose by naming the English term unless the preceding article text already introduced that term.

Weak pattern:

```markdown
中文译意：这里的bitter lesson指...
```

Stronger pattern:

```markdown
中文译意：Sutton先回顾一种反复出现的情况：研究者把人对问题的理解写进方法，短期内效果更好，但随着算力增长，依靠通用搜索和学习的方法最终胜出...
```

The English quote can then supply the exact phrase `bitter lesson`.

Before keeping or inventing Chinese wording for a foreign professional term, apply this gate:

- If the field has a stable Chinese translation that careful readers recognize, use it, as with `gradient` -> `梯度` in a machine-learning context.
- If there is no widely accepted Chinese equivalent, do not invent or keep an awkward literal Chinese noun phrase just to make the paragraph look fully Chinese. Keep the original term, optionally with a short Chinese gloss on first mention, as with `cookie` in web/security prose.
- If a stiff Chinese phrase may be an English calque, decide which branch above applies before treating it as ordinary style cleanup.

Report this decision in the final audit for long-form article edits.

## Style Review Pass

After drafting or revising article prose, run one style pass over the edited scope. The items below are review prompts, not automatic proof that the prose is wrong. Keep a flagged pattern only when it blocks a real misunderstanding, preserves a necessary domain distinction, matches a source term, or fits an intentional local metaphor already prepared by the article.

### Full-Scope Chinese Cold Read

For a Chinese article, essay, reflection note, long summary, or technical explanation with three prose paragraphs or roughly 400 Chinese characters, review the whole requested scope after drafting. Do not inspect only the inserted lines.

For every sentence that looks stiff, decide one of two things:

- rewrite it so the actor, action, object, relation, or condition is explicit; or
- retain it with a short reason: stable technical term, exact source label, quotation, code, necessary foreign term, or a locally prepared metaphor.

Do not replace words mechanically. In particular, `推进`, `形成`, `参与`, `问题`, and `工作` are not forbidden words. They need attention only when a particular sentence hides the action, creates an unnatural collocation, or imports a fixed English or project-management phrase. Preserve exact labels such as `Accepted (Oral)`, `SOTA`, `REPRODUCED`, and `NOT REPRODUCED`; explain them in ordinary Chinese instead of translating them into stronger claims.

Use the article lint as a prompt list, not a verdict. When this full review applies, run it with `--all --style-profile chinese-longform` in addition to the normal checks. The profile does not auto-rewrite text and its absence of warnings does not prove natural prose.

The base lint remains available without the profile for chat residue, generic formatting fingerprints, and formula-format checks. Author-voice signals and the context rules for repeated contrast, project-word clusters, promotional openings, and question chains run only with `chinese-longform`.

For an automatic AI-style rewrite, run this lint before drafting to build a findings list and after the second cold read to detect unresolved or newly introduced risks. The agent performs the rewrite from context; the lint must never apply regex replacements to the note. When a pre-edit snapshot is available, use the lint's baseline comparison to check semantic sentinels after rewriting.

An unchanged sentinel set is supporting evidence, not proof that every meaning invariant survived. Always inspect the narrow diff and cold-read the final scope.

The second cold read must check semantic paraphrases as well as exact lint hits. Replacing `未来可期` with `值得期待`, or `开启新篇章` with `进入新的阶段`, leaves the same empty ending in place even when the literal rule no longer fires.

### Author-Like Reasoning Check

For personal prose, use the saved profile, when one exists, to inspect the reasoning shape as well as individual phrases:

- Does a broad claim have a concrete problem, experience, example, counterexample, experiment, test, or source behind it?
- Does first person contribute evidence, limitation, preference, or a real change of mind?
- Does each question lead to an answer, experiment, comparison, or next action?
- Does a metaphor explain stable correspondences, or does it only decorate the sentence?
- Did the rewrite preserve direct verdicts and real exceptions instead of smoothing them into generic neutrality?
- Did ordinary action verbs survive where project language was unnecessary?
- Did defined anchor terms remain consistent instead of being replaced through synonym cycling?

Do not invent missing experiences or examples. If the source paragraph contains only an unsupported abstraction, state the relation already present, delete empty packaging, or retain the claim for source judgment.

### Contrastive Preambles

Search for both `不是.*而是` and `不要.*也不要.*而要` shapes.

High-risk shapes:

```markdown
不是A，也不是B，更不是C，而是D。
不要A，也不要B，而要D。
不要只A，也不要只B，而要D。
```

This pattern becomes worse when A, B, and C are long, parallel, or not grounded in a real confusion already present in the note. The "不要...也不要...而要..." version has the same problem: it often delays the actual instruction behind a staged contrast. If the negated alternatives are only explanatory padding, replace the sentence with a direct positive statement of D.

Prefer:

```markdown
这里的“小扰动”指输入在$\ell_\infty$范数下变化不超过$\epsilon$。
```

over:

```markdown
这里的“小扰动”不是指图像内容发生了变化，也不是指模型参数被修改，而是指输入在$\ell_\infty$范数下的有界变化。
```

Keep a contrastive sentence when it rules out a genuine nearby misunderstanding, distinguishes two technical objects that the article has already put in tension, or prevents an actual false inference. When keeping it, compress the negated side and avoid polished parallel lists.

### Contextual "更像" Comparison Frames

Do not treat bare `更像` or `更像是` as an error. Developed analogies often use this wording to explain how two processes correspond. Inspect the comparison only when it gives no actual correspondence, merely softens a direct judgment, or uses a polished `更像是...而不是...` frame to manufacture contrast.

When the sentence is making a judgment, name the judgment directly:

```markdown
所以这只能按短线挖矿收益看，不能直接当成稳定投资收益来外推。
```

instead of:

```markdown
所以它更像短线波动现金流，不适合直接当成稳定投资收益来外推。
```

Keep `更像` when the comparison does real work: it maps parts of one process to another, distinguishes technical objects, describes a visible similarity, or preserves a source's comparison. If no correspondence appears in the sentence or surrounding paragraph and deleting `更像` leaves the same claim, write the judgment directly.

### Absolute "从来不是" Reversals

In abstract, analytical, or reflective Chinese prose, treat sentences shaped like "X的，从来不是Y" or "X的，从来不是Y，而是Z" as suspicious. The risk is not merely stylistic: "从来" claims a scope the paragraph often has not justified, while "不是Y，而是Z" can introduce a weak opposing view only to dismiss it.

High-risk shapes:

```markdown
真正困难的，从来不是工具不够多，而是不知道如何判断结果。
能改变人的，从来不是知识本身，而是知识进入真实行动的方式。
```

Before keeping the sentence, ask whether "从来" is justified by the article's evidence, whether the rejected side is a real nearby misconception, whether the phrase before "的" names a concrete subject, and whether the claim can be stated as a direct relation, condition, source, or tradeoff.

Prefer:

```markdown
问题主要不在工具数量，而在结果怎样判断。
知识只有进入真实行动，才会改变人的判断和习惯。
```

Keep the pattern only when the article is explicitly rebutting an actual prior claim, the absolute scope is defensible, and the sentence fits the note's local voice. Even then, prefer a compressed contrast over a polished slogan.

### Unprepared Metaphors And Technical-Looking Verbs

In abstract, analytical, explanatory, or reflective Chinese prose, inspect metaphorical uses of "长在" and "漂移".

For "长在", first ask what relation the sentence is actually asserting:

- source or cause: prefer "来自", "源于", or a full causal sentence;
- dependence or condition: prefer "依赖", "取决于", "需要", or "受制于";
- manifestation: prefer "体现在", "表现为", or "可以从...看出";
- tradeoff or cost: prefer "对应", "伴随", or an explicit cost sentence;
- argumentative location: prefer naming the step, premise, evidence, or constraint directly.

Do not mechanically replace "长在" with another image such as "藏在"; that can preserve the same vague relation. Keep "长在" when it is literal growth, a body/location description, or an intentional metaphor developed in the surrounding paragraph.

Weak pattern:

```markdown
便宜只长在疼的地方。
```

Stronger pattern:

```markdown
便宜通常对应某种你愿意承受的代价。
```

For "漂移", keep the word in literal movement, measured position, or established technical contexts such as sensor drift, clock drift, or concept and distribution drift in machine learning when the surrounding note actually needs that term. Do not use it loosely for a changed idea, judgment, attention, value, goal, relation, meaning, or system state. Name the actual change instead:

- timekeeping: "时钟会慢慢不准", "走时会产生误差", or "本地时间会和标准时间出现差距";
- judgment or position: "判断变了", "立场改变", or "判断逐渐离开原来的依据";
- attention or topic: "注意力转到...", "话题转向...", or "讨论离开了原来的问题";
- meaning or concept: "含义变了", "概念边界变得不清楚", or an explicit sentence naming what changed;
- data or measurement: "数值变大/变小", "误差扩大", or "读数和真实值不一致", unless "漂移" is the established technical term in that domain.

This pass does not target "偏移". Review "偏移" only when the sentence is unclear for some other reason.

### Rare-Word Naturalness

Scan the whole edited article once for uncommon low-frequency wording that is not an established term in the article's domain. This pass targets awkward AI-coined abstractions and unnatural calques, not legitimate technical, bibliographic, product, or local-project names.

Treat a word or phrase as suspicious when several of these are true:

- it is not a normal professional term in the relevant field;
- it is rare in ordinary Chinese essays and common explanatory writing;
- it compresses a simple meaning into a stiff abstraction, metaphor, or noun phrase;
- it would make a careful reader ask "what exactly does this mean here?";
- replacing it with plainer Chinese preserves the claim and improves the sentence.

For each suspicious term, inspect the sentence, the previous sentence, and the next sentence before changing it. First infer the intended meaning in context, then replace the term with natural Chinese that says that meaning directly. Do not merely substitute a synonym. If the term is an established domain term, an exact source term, a necessary local label, or an intentional metaphor that the article has already prepared, keep it.

Common rewrite patterns:

- replace abstract "X机制/模型/链/通道/期权/赋能" language with the concrete relation it names;
- replace improvised hierarchy labels such as "路线较高/较低" with concrete tradeoffs such as "更可控但门槛更高";
- replace duplicated nominalizations such as "时间试错/token试错" with the underlying cost or action;
- replace business-style words such as "沉淀" when the local meaning is simply "积累、保存、写入笔记、以后复用";
- avoid using "费用" in abstract explanatory prose unless the context is a concrete transaction, price, money, accounting, or economic calculation. In technical or methodological prose, name the actual quantity instead, such as "总量", "时间复杂度", "上界", "估计量", "代价", or "维护成本", depending on the sentence.
- replace forced translations of English technical terms with the accepted Chinese term when one exists; if no accepted translation exists, keep the English term and explain it briefly on first mention instead of coining or retaining an uncommon Chinese label.

## Final Audit

After editing, report the checks that affected the actual revision:

- the full sentence-review scope, including sections read rather than only lines changed;
- counts by risk category, how many sentences were changed, and which sentences were retained with a reason;
- any sentence that still needs the user's or a subject specialist's judgment;
- whether the inserted or revised paragraph makes sense to a reader who never saw the chat;
- whether the first sentence follows from the preceding paragraph;
- whether the edit introduced any phrase from the user's request before the article prepared it;
- whether the edit preserved the note's local voice, heading structure, wikilinks, callouts, and source attribution;
- the selected genre, `voice_basis`, stable author traits preserved, and corpus patterns deliberately rejected as non-voice;
- which suspicious contrastive preambles, comparison frames without actual correspondences, repeated reversals, unsupported metaphors, loose "漂移" uses, rare words, or forced translations were replaced;
- if none were replaced, explicitly say that the scan found no suspicious non-domain terms and no forced-translation issue.
- for automatic style rewriting, report whether the scope came from an explicit whole-article or section request, or from the local mixed-scope rule for ordinary content edits;
- report pre-edit and post-edit lint counts, the second cold-read result, semantic-sentinel differences, and any sentence retained because source or meaning safety prevented an automatic rewrite.

Run `<SKILL_ROOT>\scripts\lint_note_article_revision.py` for mechanical checks. With `--repo`, it scans both unstaged and staged added lines from Git diff, scans untracked files as full-file candidates, and warns when the repo/path check itself is unreliable. Treat warnings as review prompts, not automatic proof that the prose is wrong. A final answer that only says lint passed is incomplete for article prose.
