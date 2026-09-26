# Image Embed Repair

Use this reference when the user asks about missing images, broken image embeds, image moves, or image wikilinks in Vault notes.

## Scope

- Treat image repair as a link-integrity task, not a prose rewrite.
- Preserve surrounding text and only change image elements unless the user asks for broader edits.
- Audit all relevant image element forms, not only Obsidian embeds: `![[...]]`, `![](...)`, `![](<...>)`, and HTML `<img src="...">`.
- Exclude `.trash`, agent temporary folders, and Skill source folders stored inside the Vault from normal article audits unless the user explicitly includes them.

## Target Resolution

Resolve an image target in this order:

1. path relative to the note;
2. path relative to the Vault root;
3. the note's top-level attachment folder, such as `图片`;
4. a full-Vault unique basename match.

If there are multiple same-name candidates, do not guess. If the image file cannot be found in the current Vault or Git history, do not point the note at an unrelated image and do not fabricate a replacement.

## Rewrite Rules

- Prefer Markdown image links for repaired article images. Use a path relative to the note, for example `图片/example.png` or `../ml/图片/example.png`.
- If a Markdown image path contains spaces, parentheses, or apostrophes, wrap the destination in angle brackets. For example, use the destination `<../ml/图片/ResNet_block_(cropped).png>`.
- If an Obsidian image embed uses a size suffix such as `![[diagram.svg|675]]`, preserve the intended display width with HTML, for example `<img src="图片/diagram.svg" width="675">`.
- If the image file cannot be found, replace the broken image element with an HTML comment that preserves the original target, for example `<!-- missing_image: original="270.png"; status=file_not_found_in_vault_or_git_history -->`.

## Final Checks

- Re-audit the same scope and report counts: total image elements, broken image elements, and remaining image wikilinks.
- If the user requires that image double-links be eliminated, the remaining image wikilink count must be zero.
- Delete temporary audit scripts after use.
- Run `git diff --check`.
- Rebuild `obsidian_local_kb` so future `$obsidian-vault-notes` reads see the repaired links.
