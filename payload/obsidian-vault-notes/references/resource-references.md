# Managed Resource References

Use this reference whenever a note contains or may gain a local-file reference outside the Obsidian Vault.

## Standard Forms

```markdown
[Display name](file:///C:/current/path.pdf)<!-- mpk-resource:KB-... -->
![Description](file:///C:/current/path.png)<!-- mpk-resource:KB-... -->
<video src="file:///C:/current/path.mp4"></video><!-- mpk-resource:KB-... -->
```

The resource marker must immediately follow the link or HTML media element on the same line. The URI is for human clicks; the ID is the stable identity used by agents. The display text is not an identity and is not changed automatically during a file move.

Ignore fenced code and inline code. Report untagged or bare `file:` URIs, drive-letter paths using either slash direction, UNC paths, and confirmed knowledge-root-relative bare paths as `unmanaged_path_reference`. Invalid and orphan `mpk-resource` comments are diagnostics. Never repair a bare path by filename matching. One HTML element with multiple local-file attributes, such as both `src` and `poster`, is ambiguous: one adjacent marker must not silently identify both files.

## Guarded Edit Flow

1. Parse resource references from the current disk text before producing the edit.
2. Parse the prospective text and compute added, removed, retained, changed, unmanaged, and malformed references.
3. Ask the resource harness for dry-run diagnostics for both the current and prospective text. This confirms root-relative paths against the configured knowledge root and registry without mistaking ordinary Vault-relative attachments for external resources. Compare diagnostics without treating line-number shifts as new references. If a relative file path changed and the harness cannot complete that classification, stop rather than assume it is a Vault attachment.
4. If there is no external-reference delta, preserve any existing unmanaged references and report them without blocking unrelated work. Harness discovery, preview, or synchronization failures also remain non-blocking for such an ordinary edit.
5. For every added or changed ID, call `resource-resolve --id` and fail closed on incomplete resolver output. Stop before writing when the returned identity is wrong, the ID is unknown, the resource is `missing` or `retired`, existence is not confirmed, or any URI component differs, including authority, path, query, or fragment.
6. Treat a newly written external link without an adjacent ID as a hard error. A link into a cache or other excluded location must first be moved to an eligible long-term resource location and registered.
7. Keep the existing SHA-256, mtime, dry-run, and concurrent-change checks. Resource validation does not replace them.
8. After the note write, re-read the current disk bytes, verify `post_sha256`, and call `reference-refresh` with that final text. Replace the note's cached reference set; never merge it with a stale pre-write set. Preserve dry-run and write-stage diagnostics in `EditAudit`.
9. Add the reference delta and synchronization result to `EditAudit`.

The helper locates the installed harness or accepts an explicit `MPK_HARNESS` path for tests and controlled environments. If it cannot run the harness, an edit with no external-reference delta may proceed. Any added, removed, or changed external reference must stop. If the file was written successfully but post-write synchronization fails, return `reference_sync_pending` and do not claim complete success.

`vault_batch_edit.py` performs this preflight for every final per-file candidate before its first write. After all note files are written, it synchronizes each cache from the re-read final text and hash before committing the batch. A synchronization failure triggers the same guarded rollback as a write failure, followed by an attempted cache restoration from each original note. A complete note rollback is reported as `reference_sync_pending`; an incomplete file or cache restoration is `partial_write`. Neither state is success.

## Human Edits

Do not install a watcher. On the next related query, note write, resource move, or audit, compare Markdown mtime and SHA-256 and refresh changed notes. Unknown IDs, deleted markers, and changed URIs produce diagnostics only; do not rewrite human edits automatically.

Markdown remains the final source of truth. The registry's note-reference table is a local, rebuildable cache and must not contain note bodies.

## EditAudit Fields

Report at least:

- `added`, `removed`, and `retained` resource IDs;
- `unknown`, `missing`, `retired`, `uri_mismatch`, and `unmanaged` references;
- harness diagnostics before and after the edit, their added/removed delta, and post-sync dry-run/write diagnostics;
- whether the resource harness was available;
- `reference_sync_status`: `not_needed`, `synchronized`, `blocked`, or `reference_sync_pending`;
- the final `post_sha256` used for synchronization.

Do not collapse `reference_sync_pending` into a warning on an otherwise successful result. The note write succeeded, but the full workflow did not.
