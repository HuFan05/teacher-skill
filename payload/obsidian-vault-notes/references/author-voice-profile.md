# Author Voice Profile

Use this reference when revising the user's Chinese essays, reflections, technical learning articles, source commentaries, or other personal long-form prose in the Vault. It defines how a saved voice profile is distilled from the current user's own writing, what the profile records, and how it must be used. Reading this file does not authorize reading the notes that were used to build a saved profile.

## Contents

- [Status And Evidence](#status-and-evidence)
- [Priority Order](#priority-order)
- [Profile Trait Template](#profile-trait-template)
- [Genre Routing](#genre-routing)
- [Do Not Imitate](#do-not-imitate)
- [Rewrite Decisions](#rewrite-decisions)
- [New Corpus Sampling](#new-corpus-sampling)
- [Voice Audit](#voice-audit)

## Status And Evidence

This Skill ships no personal profile. A saved profile exists only after the user authorizes a read of a named corpus of their own writing under [New Corpus Sampling](#new-corpus-sampling) and the resulting profile is saved at a location the user chooses. The profile is user data: keep it outside the distributed Skill payload, and read it only as that exact file. When no saved profile exists, skip step 4 of the priority order and calibrate from reliable local prose alone.

A saved profile records, for each trait, the corpus it came from (file count, folders, exclusions), whether the trait recurred across multiple notes, and whether it is `stable`, `genre-only`, or `do-not-imitate`. The analysis must separate repeated cross-note habits from genre-specific wording, drafts, video-production text, probable assistant residue, copied source prose, and ordinary errors.

Treat a saved profile as a revisable local model, not a claim that every note in the corpus was written in one voice. Frequency alone never makes a pattern desirable. A recurring habit such as `真正` or `不是……而是……` can still be a rewrite target when it only manufactures emphasis.

## Priority Order

Resolve conflicts in this order:

1. Preserve meaning invariants, sources, technical claims, and the author's actual position.
2. Preserve the target note's genre, intended reader, paragraph job, and local terminology.
3. Use clearly author-written, unedited passages in the current note when they are not themselves the suspected AI-style material.
4. Apply the stable traits in the user's saved profile, when one exists.
5. Use other same-genre note bodies only when the user supplied them or explicitly authorized that read scope.
6. Fall back to ordinary natural Chinese without manufacturing a new personality.

Do not average the whole target note when some sections are obvious chat residue, copied source prose, production instructions, or AI expansion. Calibrate from the reliable spans and record the voice basis in the audit.

Do not invent an experience, example, source check, uncertainty, joke, or verbal habit to make the revision resemble a profile.

## Profile Trait Template

A saved profile describes the user's voice along the dimensions below. For each dimension, record what the authorized corpus actually shows, with its classification and recurrence, and leave the dimension empty when the corpus gives no reliable evidence. Do not fill a dimension from general writing advice.

### Opening Material

Record whether the user tends to start from a concrete problem, personal experience, algorithm or system example, experiment result, failed attempt, or reader situation, or from a stated thesis. Use it to decide where a revised paragraph begins; never replace the user's actual opening material with an invented one.

### How Judgments Are Supported

Record what the user uses to make claims checkable: examples, counterexamples, formulas, code, experiments, benchmarks, source checks, failure locations, or explicit comparisons. Use it to keep that support attached to the claim it supports.

### Use Of Questions

Record whether the user's questions open a real next step, such as why a condition is needed, what fails without it, how a result can be checked, or which alternative should be tried. Use it to separate diagnostic questions from evenly paced rhythm questions.

### Use Of Analogy

Record whether and how the user develops analogies, and whether the correspondence between their parts is spelled out. Use it to keep a developed analogy and to avoid adding decorative or competing images.

### First Person

Record what the user's first person carries: an actual attempt, limitation, preference, error, change of mind, or source of evidence. Use it to keep informative first person and to remove perception padding the user does not normally write.

### Verdicts And Limits

Record how directly the user states verdicts (a correctness argument being wrong, an example failing, a method being too costly to check, a result remaining uncertain) and how the user states exceptions, scope conditions, and self-correction. Use it to avoid softening supported judgments or adding generic balancing lines.

### Verb Register

Record whether the user prefers ordinary action verbs or technical or formal vocabulary in each genre. Use it to keep technical terms where they are genuinely technical and to avoid replacing ordinary action with project language.

### Rhythm And Anchor Terms

Record sentence-length variation that carries conditions and causality, and whether the user deliberately repeats a defined anchor term such as a model name, algorithm, or data structure. Use it to avoid standardizing every sentence to medium length or cycling through synonyms.

## Genre Routing

- **Technical learning and method articles:** prioritize concrete problems, failed routes, conditions, examples, experiments, and the next action. When the profile has a genre-specific model for this genre, prefer it for revising a technical learning essay.
- **Long argument or source commentary:** use calmer transitions, exact attribution, explicit limits, and source-status labels. Preserve a direct thesis without adding promotional emphasis.
- **AI or tool test records:** keep the experimental order: task, changed condition, output, error location, evaluation, and follow-up test. Repetition may be necessary to show which variable changed.
- **Short reflections:** preserve compression and the one governing image or judgment. Do not inflate a short note into a standard essay.
- **Video or character scripts:** allow hooks, suspense, audience address, and production markers only while the target remains a script. Do not transfer that surface language into an essay.
- **Operational notes:** follow their specialist layout and technical precision rules before this personal essay profile.
- **English prose:** use it for argument order only; do not copy English syntax into Chinese voice calibration.

## Do Not Imitate

Do not learn these as author voice merely because they occur in the corpus:

- typos, malformed sentences, duplicated words, mixed punctuation, and unfinished placeholders;
- assistant-answer shells such as `你描述的过程`, `总结来说`, encyclopedic four-part expansions, or offers to continue;
- video promotion such as `欢迎来到`, `在今天的节目中`, or unsupported superlatives when the target is not a script;
- dense strings of `机制`, `路径`, `框架`, `系统`, `模型`, `逻辑`, `闭环`, `优化`, `赋能`, `沉淀`, and `重塑` that hide the actual action;
- repeated `不是A，而是B`, `真正……`, `核心在于……`, or absolute `从来` reversals used only to manufacture emphasis;
- generic era openings, significance inflation, inspirational conclusions, and ready-made aphorisms;
- exaggerated certainty, vague group attribution, and claims that become stronger merely because the prose became smoother;
- intentional disorder, slang, humor, or autobiographical detail invented to simulate humanity.

## Rewrite Decisions

Apply these tests sentence by sentence:

- **First person:** ask what event, attempt, limit, or change of mind it contributes. Keep that information; otherwise state the claim directly.
- **Contrast:** ask whether the rejected view is present nearby or is a likely technical misunderstanding. Keep a compressed contrast only when removing it loses a real distinction.
- **Abstract noun:** identify who did what, to which object, under what condition, and with what cost or result. Rewrite the sentence around that relation rather than swapping one abstraction for another.
- **Question:** identify the next paragraph or action that answers it. Keep diagnostic questions; merge unanswered rhythm questions.
- **Metaphor:** list the correspondences it explains. Keep one developed analogy; remove decorative or competing images.
- **List:** keep a real classification, procedure, experiment, or checklist even when it has three or more members. Remove only cosmetic symmetry or overlapping items.
- **Ending:** close on a result, remaining uncertainty, actual prediction, commitment, or next action. Delete generic optimism instead of paraphrasing it.
- **Anchor term:** keep deliberate repetition of a defined object. Do not create synonym churn merely to vary vocabulary.
- **Cadence:** read the paragraph continuously. Split an oral run-on at a change in claim, evidence, example, or limitation; do not turn it into a row of slogans.

Typical transformations:

```text
抽象包装：这一过程构建了反馈闭环，实现了经验沉淀与持续优化。
具体写法：把这次训练为什么发散、后来改了哪个超参数写下来；下次遇到相似问题时，就不用从头试一遍。

模板反转：真正重要的不是知识积累，而是能力的结构性跃迁。
具体写法：知识在写代码、跑实验和比较不同解法时被反复调用，才会逐渐变成以后能直接使用的经验。

问句造势：问题从哪里来？谁需要结果？怎样验证？如何沉淀？
具体写法：先记录问题的来源、失败路线、验证办法，以及这次经验以后还能用在哪里。
```

These examples demonstrate sentence work, not approved replacement text for a live note. Preserve the live note's exact meaning before applying the form.

## New Corpus Sampling

Do not read a new folder or group of notes for voice merely because this reference exists. Require the user to name or clearly authorize the corpus, and use only the user's own writing as voice evidence.

When a new corpus is authorized:

1. Enumerate the exact Markdown files, exclusions, total character count, and whether subfolders are included before reading bodies.
2. Freeze that file list. Do not follow wikilinks or external links unless separately authorized.
3. Exclude the target article, direct quotations from it, copied source passages, code, formula-only material, and production metadata from voice evidence.
4. Separate genres and probable provenance. Downweight rough fragments, promotional scripts, assistant residue, and text whose surface differs sharply from the rest of the corpus.
5. Require recurrence across multiple notes or a clear functional role before calling something stable style.
6. Distinguish `preserve`, `genre-only`, and `do-not-imitate` findings. Do not equate high frequency with preservation.
7. Record the findings along the [Profile Trait Template](#profile-trait-template) dimensions, together with the corpus audit, and save the profile only where the user asks.
8. Report the corpus audit without reproducing unnecessary note bodies.

## Voice Audit

For a full prose rewrite, add these fields to the normal audit:

- `voice_basis`: reliable current-note spans, the saved profile if one exists, and any separately authorized same-genre notes actually used;
- `genre`: the target genre and why that genre model was selected;
- `author_traits_preserved`: concrete evidence, diagnostic questions, developed analogy, direct verdict, real limitations, verb register, and useful anchor repetition that survived;
- `non_voice_rejected`: old errors, production residue, assistant shells, abstract packaging, template reversals, and unsupported certainty that were not imitated;
- `voice_conflicts`: any place where the saved profile lost to meaning, source, technical correctness, target genre, or a clear local author choice.

Do not claim that a text is natural merely because it matches a profile. The second continuous cold read remains required.
