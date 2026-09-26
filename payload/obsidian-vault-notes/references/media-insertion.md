# Media Insertion

Use this reference when generating or inserting images, GIFs, SVGs, PDFs, audio, video, or other local attachments into a Vault note.

## Workflow

1. Read only the target note section needed to choose placement.
2. Generate or receive the media file outside the note body first.
3. Place a single file inside an approved Vault attachment folder, such as `图片/`. When the deliverable contains two or more related files and any one of them is not Markdown, follow `references/vault-file-bundles.md`: create one task-specific subfolder under the user-named directory and place the complete set there.
4. Check that the file exists, has the expected extension, and has a reasonable size.
5. Insert the link with `vault_edit.py` after a reviewed dry-run.
6. Reopen or check the note to confirm the relative link text is correct.

## Link Forms

- Use `![[relative/path.ext]]` for ordinary Obsidian embeds.
- Use `<img src="relative/path.svg" width="...">` only when width control is needed and the surrounding note already accepts HTML.
- Use an ordinary Markdown link for non-visual files or files that should not render inline; keep its destination relative to the Vault.

Keep paths relative to the Vault. Do not insert absolute local paths into note prose.

## Media Checks

For every inserted file, record or inspect:

- relative Vault path;
- file extension;
- byte size;
- source tool or script when generated locally;
- whether the visual/technical meaning was checked.

For technical diagrams, plots, or animations, such as algorithm visualizations, architecture diagrams, or training curves, add a short local note or comment when verification is not complete:

```text
verification=unchecked
```

Change it to `verification=checked` only after the technical content, the data behind any plot, and the visual framing have been inspected.

## Boundaries

- Image repair belongs to `image-embed-repair.md`; new media insertion follows this file.
- Do not move large media folders during a note edit unless the user asks for attachment reorganization.
- Do not generate media outside the Vault and then link to the absolute path.
- If a generated asset came from an external tool, keep enough local provenance to reproduce it later.
