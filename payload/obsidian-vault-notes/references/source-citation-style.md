# Source Citation Style

Use this reference when adding, rewriting, or reorganizing source attribution in Obsidian notes. It is especially relevant for CS/AI technical notes, paper-reading notes, source-backed essays, and article revisions where the reader needs to see which claim comes from which source.

## Default Rule

- Put directly reachable online sources in the body as Markdown links at the sentence or clause they support.
- Use a final `## 资料来源` section for print, historical, unavailable, or non-direct sources.
- Do not use bare bracket citation labels such as `[Vas17]`, `[HZRS16]`, or `[SO4521]` as the primary citation system in Obsidian notes.
- Markdown links like `[arXiv:1706.03762](https://arxiv.org/abs/1706.03762)` are allowed; the restriction is against opaque citation tags that look like Obsidian syntax and do not show the claim-source relation.

## Inline Links

Use inline links when a source has a stable direct target, for example:

- arXiv abstracts, DOI landing pages, proceedings pages (for example ACL Anthology, PMLR, or OpenReview), official documentation, code repositories at a fixed release or commit, benchmark leaderboards, Stack Exchange questions or answers, project pages, and stable PDF pages;
- direct source sentences such as "Vaswani et al.'s Transformer paper reports ...";
- status claims such as "the official leaderboard lists ..." or "a Stack Exchange discussion asks whether ...".

The link should sit on the source name or source-bearing phrase, not at the end of an unrelated paragraph. Prefer:

```markdown
[Attention Is All You Need](https://arxiv.org/abs/1706.03762) reports the BLEU scores of the base and big models on WMT 2014 English-German.
```

Avoid:

```markdown
The reported BLEU scores are as follows. [Vas17]
```

## Final Source List

Use `## 资料来源`, not `## 参考文献`, unless the note already has a strong local reason to keep the older heading.

Use bullet items without bracket labels:

```markdown
## 资料来源

- Thomas H. Cormen, Charles E. Leiserson, Ronald L. Rivest, and Clifford Stein, *Introduction to Algorithms*, ...
- Donald E. Knuth, *The Art of Computer Programming*, Vol. 3, ...
```

For online sources already linked in the body, do not repeat them at the end unless the user asks for a consolidated bibliography or the note's local convention already keeps one. If you repeat them, use ordinary linked titles or source names in bullet items, not bracket keys.

## Source Status

Make the status of the source visible in the prose when it matters:

- "the published paper proves ...";
- "the arXiv preprint reports ...";
- "the official leaderboard lists ...";
- "a Stack Exchange answer gives ...";
- "a blog post claims ...".

Do not make a community source, leaderboard entry, or blog post sound like a peer-reviewed result, and do not make a reported number sound reproduced when nobody reran it. For technical claims that are not widely known, follow `references/cs-ai-note-style.md`: add sources, mark uncertainty and evidence grade, and do not smooth an unchecked derivation or a single reported run into an established result.

## Checks

Before finishing an edit that changes citations:

- search for leftover bare citation labels with a pattern like `\[[A-Za-z0-9]+[0-9]{2,}\]`;
- verify important inline links still point to the intended source;
- check whether sources without direct links are present as bullet items under `## 资料来源`;
- avoid adding `\(...\)` or `\[...\]` formula delimiters while editing citation text.
