# AI-Style Rewrite

Use this reference when the user asks to remove AI-style writing, naturalize prose, polish language, comprehensively revise an article, or make a Vault note sound like the author's own writing. This reference authorizes contextual automatic rewriting inside the active style scope; it does not authorize new note bodies, external research, or cross-note voice sampling.

## Contents

- [Scope And Automatic Action](#scope-and-automatic-action)
- [Meaning Invariants](#meaning-invariants)
- [Voice Calibration](#voice-calibration)
- [Author Profile Use](#author-profile-use)
- [Automatic Rewrite Targets](#automatic-rewrite-targets)
- [Protected Material](#protected-material)
- [Two-Pass Rewrite](#two-pass-rewrite)
- [Unsafe Source Cases](#unsafe-source-cases)
- [Hard Rules To Reject](#hard-rules-to-reject)
- [Final Audit](#final-audit)

## Scope And Automatic Action

Use the following precedence:

1. If the user explicitly asks for inspection without edits, report findings and do not write.
2. If the user asks to remove AI traces, naturalize, polish, or comprehensively revise prose, automatically rewrite the style scope the user actually named. A whole article or section request covers that complete target; a sentence or paragraph request remains local except for adjacent transition repair. Do not stop at a findings list and do not request sentence-by-sentence confirmation.
3. For an ordinary content edit, automatically improve only the changed span, the immediately adjacent paragraphs, and bridge sentences needed for coherence. Do not turn a local content task into a whole-article rewrite.
4. Use headings to resolve the boundary only after the user's wording has selected a section-level target. Do not promote a named sentence or paragraph into a section merely because it sits under a heading. Ask only when two materially different scopes remain possible.

There is no percentage cap on an explicitly requested style rewrite. Process every confirmed problem in the active scope. Automatic action still stops at the meaning and authorization boundaries below.

Chinese rule summary: 明确要求全面审校时，不设修改比例上限；不得新增事实，也不得改变判断强度。不能通过压缩内容伪造自然、干净的效果。以下保留边界同时禁止机械硬规则。

## Meaning Invariants

Before drafting, record or otherwise preserve these invariants from the live Markdown:

- facts, dates, numbers, names, institutions, places, and concrete events;
- formulas, quantifiers, hypotheses, conclusions, claim status, evidence grades, experiment configurations and seeds, and uncertainty labels;
- wikilinks, Markdown links, embeds, block ids, headings, callouts, code, quotations, and source boundaries;
- negation, causality, chronology, comparison direction, necessary/sufficient conditions, and scope words;
- epistemic strength, including `可能`, `似乎`, `推测`, `尚不明确`, `至少`, `至多`, `必然`, and explicit source status;
- the author's stated position, mixed feelings, autobiographical details, and intentional self-correction.

Do not invent a fact, date, statistic, survey, source, quotation, motive, scene, or personal reaction to make vague prose sound concrete. Do not silently convert possibility into fact, observation into cause, a source's classification into an established technical result, a single run into a stable finding, or an unchecked derivation into a correctness argument.

## Voice Calibration

Use reliable unedited prose in the current note as the first voice sample. Do not treat quotations, copied sources, production instructions, placeholders, chat residue, or the suspected AI-style span as author evidence. Observe how the note moves from a problem to a judgment, not only sentence length, punctuation, and vocabulary.

Do not read other note bodies merely to obtain a larger voice sample. Another note may be used only when the user supplied it or the active Vault read scope already authorizes it for this task.

For the user's personal Chinese long-form prose, load `references/author-voice-profile.md`. A saved profile, when one exists, is distilled guidance, not permission to reopen its source corpus. Match the target genre and paragraph job before transferring a cross-note trait. Another same-genre note body still requires explicit task scope.

Match reliable local prose and the saved profile, when one exists, instead of manufacturing a generic human voice. Do not add humor, slang, emotion, first person, rhetorical questions, tangents, deliberate disorder, experiences, or examples unless the source text supports them.

## Author Profile Use

Use the saved profile to preserve the author's way of reasoning along the dimensions it records. Apply only what the profile or the reliable local prose actually shows, for example:

- begin from a concrete problem, experience, failed route, example, experiment, or source when the note already contains one;
- let first person identify evidence, limitation, preference, error, or a real change of mind;
- keep diagnostic questions that lead to a calculation, experiment, comparison, source check, or next action;
- keep one developed analogy when its parts have explicit counterparts in the subject;
- preserve direct verdicts, real uncertainty, and specific exceptions;
- keep the author's verb register when project language hides what somebody actually did;
- preserve deliberate anchor-term repetition and uneven but functional rhythm.

Do not turn frequency into a writing rule. A recurring expression may be an old habit, genre marker, copied phrase, production residue, or assistant expansion. The profile's `do-not-imitate` findings override surface frequency, while meaning invariants override the profile.

## Automatic Rewrite Targets

Treat the following as contextual rewrite targets. A lint hit is a prompt to inspect the sentence; the agent decides the rewrite from the paragraph's actual job.

### Chat And Tool Residue

Remove assistant correspondence, offers to continue, praise of the user's question, knowledge-cutoff disclaimers, prompt language, and tool markers such as `turn0search0`, `oai_citation`, `contentReference`, `attributableIndex`, or `:::writing`. Preserve these strings when the note is explicitly documenting the marker itself.

### Empty Openings And Announcements

Delete or replace prose that announces the discussion instead of beginning it, including empty forms such as `下面我们将讨论`, `接下来让我们分析`, or a one-line paragraph that merely repeats the heading. Keep a real roadmap when later sections depend on it.

### First-Person Padding

Remove `我逐渐意识到`, `我越来越感觉到`, or similar perception frames when the sentence only states a general claim and supplies no experience. Keep first person when it records what the author tried, saw, misunderstood, checked, preferred, or changed. Never invent an episode merely to retain a personal tone.

### Inflated Significance And Promotional Language

Replace unsupported claims that an ordinary fact `标志着重要转折`, `彰显深远意义`, `反映更广泛趋势`, or creates a `崭新篇章`. State the concrete fact or relation already present in the note. Remove promotional adjectives that have no observable content. Keep evaluative language when the article argues for it with evidence or when it is an attributed source judgment.

### Vague Attribution

Replace `专家认为`, `业内人士指出`, `研究表明`, or similar authority claims with the named source already present in the note. If the sentence contains only empty praise and has no source, delete it. If it contains a substantive claim that cannot be safely retained or removed without source work, leave it unchanged and record `source_judgment_required` in the audit.

### Generic Conclusions

Remove conclusions such as `未来可期`, `迈出了重要一步`, or `开启新篇章` when they add no result, prediction, commitment, or next action. Do not preserve an empty conclusion by replacing it with a synonym such as `值得期待`, `新的阶段`, or `新的起点`. A generic closing formula is style packaging, not a protected claim merely because it sounds positive. When the surrounding text develops a real forecast, commitment, or personal judgment, preserve that actual claim in plain wording; otherwise delete the formula or close on an existing concrete detail.

### Formulaic Contrast And Question Chains

Rewrite polished `不是A，而是B`, absolute reversals, repeated negative alternatives, and evenly measured question chains when they delay a direct claim. Keep a short contrast that rules out a real nearby misunderstanding, distinguishes technical objects, or prevents a false inference.

### Abstract Process Packaging

Inspect clusters such as `构建反馈闭环`, `实现经验沉淀`, `优化能力结构`, or `形成迭代路径` when they replace an observable action. Recover the actor, object, attempt, failure, check, result, or cost already present in the paragraph. Keep `闭环`, `链路`, `沉淀`, `模型`, `结构`, and similar terms when they name a real technical object or a relation the article actually explains; no single term is an automatic rewrite command.

### Manufactured Depth

Rewrite aphorism formulas such as `X是Y的语言`, `X是一面镜子`, or `X成为陷阱` when the metaphor merely decorates an ordinary relation. Keep a source quotation or an intentional metaphor already developed by the surrounding prose.

### Manufactured Rhythm

Combine or vary a run of short declarative fragments when several consecutive sentences manufacture drama or make every line land as a slogan. A single short conclusion is valid. Do not force long-short alternation or rewrite a technical list merely to create rhythm.

### Calques, Empty Nouns, And Imported Jargon

Name the actor, action, object, relation, condition, or cost when English-shaped verbs, empty nouns, or business language hide them. Preserve established technical, bibliographic, software, policy, and project terms in their proper domains.

### Formatting Fingerprints

Remove newly introduced decorative emoji, mechanical bolding, repeated `**label:**` vertical lists, and excessive section fragmentation when they do not support navigation or meaning. Preserve the note family's established layout and any list that expresses a real classification, procedure, checklist, or comparison.

### Production And Script Residue

Treat BGM directions, shot notes, calls for likes, program welcomes, and audience-address hooks as genre-specific. Preserve them in a working video or character script; remove or rewrite them when they leaked into a finished essay. Do not use script rhetoric as a voice sample for an ordinary reflection or technical article.

## Protected Material

Do not apply ordinary style replacement inside:

- quotations, titles, code, formulas, raw data fields, URLs, link targets, and exact source labels;
- stable technical terminology;
- concrete personal details, dated references, genuine uncertainty, mixed feelings, and deliberate self-correction;
- diagnostic questions with a real downstream action, developed analogies, direct supported verdicts, ordinary action verbs, and deliberate anchor-term repetition;
- a three-item or longer list whose members form a real set;
- passive constructions whose actor is unknown, irrelevant, deliberately suppressed, or standard in the field;
- punctuation and sentence-length habits that match the local note and do not obstruct reading.

## Two-Pass Rewrite

### Pass 1: Remove Existing Problems

Read the heading, target paragraph, neighboring paragraphs, section role, and target genre. Select reliable local voice evidence and load the saved author profile when one exists; read same-genre notes only when separately authorized. Run the prose lint on the active scope. Rewrite every confirmed target while preserving the meaning invariants. Use the smallest span that solves each problem; restructure a paragraph only when sentence-level repairs cannot restore its argument or rhythm.

### Pass 2: Repair Rewrite Artifacts

Read the revised scope continuously without relying on the initial findings list. Check whether the rewrite introduced:

- uniform medium-length sentences or forced long-short alternation;
- stacked punchlines, abrupt fragments, or identical paragraph endings;
- a generic neutral voice that erased the author's position;
- repeated connectors, synonym cycling, or newly coined abstractions;
- a watched cliché that survived only as a synonym or paraphrase;
- broken transitions, lost referents, changed claim strength, or reduced factual detail;
- lost concrete problems, examples, failed routes, source checks, diagnostic questions, useful analogies, direct judgments, or genuine uncertainty;
- accidental transfer of video hooks, test-log cadence, short-note compression, or other genre-only habits.

Repair those artifacts, run the lint again, and compare the final text with the pre-edit semantic sentinels.

## Unsafe Source Cases

Style rewriting does not authorize external research. Use sources already present in the active note or already authorized by the task. Do not make an unsupported sentence appear sourced by attaching a nearby citation.

When safe prose repair depends on verifying a source, retain the sentence and report the exact claim and missing decision. This is the narrow exception to automatic rewriting; it protects factual and technical integrity rather than returning ordinary style choices to the user.

## Hard Rules To Reject

Never adopt these as automatic rules:

- delete all adverbs or transition words;
- ban passive voice, em dashes, rhetorical questions, first person, or long sentences;
- force every sentence to name a human actor;
- prefer two list items over three or four;
- alternate sentence length mechanically;
- convert every list into prose;
- add personality, humor, emotion, disorder, or autobiographical detail;
- replace every occurrence of a watched word;
- assign an AI probability or authenticity score from style alone.

## Final Audit

In addition to the normal `EditAudit`, report:

- the automatic style scope and why it was full-scope or local;
- pre-edit and post-edit lint category counts;
- sentences changed by category and sentences retained for meaning or source safety;
- whether facts, numbers, formulas, code, links, block ids, quotations, labels, negation, causality, chronology, and epistemic strength were preserved;
- whether concrete personal detail, genuine uncertainty, and local voice survived the rewrite;
- the target genre, `voice_basis`, author traits preserved, and observed corpus habits deliberately rejected as non-voice;
- whether any same-genre note body was read and which explicit task scope authorized it;
- any `source_judgment_required` or subject-specialist decision still outstanding;
- confirmation that the second cold read checked the rewrite for newly manufactured AI-style prose.
