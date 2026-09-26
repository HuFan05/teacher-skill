# Resource Registry

Use this protocol for every long-lived file inside the configured knowledge root and outside the Obsidian Vault.

## Identity And Scope

- Assign one permanent `KB-` plus 26-character Base32 ID to each eligible physical file.
- Preserve the ID across rename, move, and content updates. Record content changes as versions. Never reuse an ID.
- Keep byte-identical files separate and report them only as duplicate candidates.
- Mark a disappeared file `missing`; use `retired` only after the user confirms that the resource is no longer maintained.
- Never assign IDs to directories, Vault Markdown, Vault attachments, `.mpk`, symlinks, junctions, other reparse points, caches, indexes, temporary files, or tool state.

Hard exclusions are the configured Vault subtree, `.mpk`, targets outside the root, non-files, and reparse points. Default soft exclusions include `.git`, `.svn`, `node_modules`, `__pycache__`, `.venv`, download/lock/SQLite sidecar files, `.megaignore`, `desktop.ini`, `Thumbs.db`, and configured relative exclusions. A note that needs a soft-excluded file must first move it to an eligible long-term location.

Inventory must use the bundled Python scanner, which stays inside the configured root, prunes exclusions, never follows reparse points, and commits registration in bounded batches. Do not inventory the real knowledge root with a recursive PowerShell or cross-shell file walk; this avoids uncontrolled traversal and reduces Defender false positives.

## Storage Contract

- Active database: `%LOCALAPPDATA%\manage-personal-knowledge\registry.sqlite3` or the `MPK_STATE_DIR` override.
- Portable recovery manifest: `<knowledge_root>\.mpk\resources.jsonl`.
- Root marker: `<knowledge_root>\.mpk\root.json`.
- SQLite is the only normal writable source. The harness exports JSONL atomically; never merge user-edited JSONL back into SQLite.
- Both stores carry a generation number. A mismatch permits resolve, search, status, and audit, but blocks registry mutations until `registry-export` repairs the manifest.
- The manifest carries resource identity, current/path history, versions, and external identifiers. It excludes note bodies, note-reference cache, and operation journals.

## Mutation Protocol

Every mutation is a dry-run by default. Inspect its targets, preconditions, reference changes, warnings, and `plan_sha256`. Apply only the same plan:

```text
--write --expect-plan-sha256 <plan_sha256>
```

Reject a write when the plan hash, root identity, source hash, generation, or target preconditions changed. Do not treat a new dry-run plan as implicit approval for a materially different operation.

Use these commands:

- `registry-init`: preview exclusions and eligible files; after confirmation, write schema v2, root marker, SQLite, and the first manifest. It does not register the whole corpus.
- `registry-scan --resume --max-files N`: register a bounded sorted batch. Resume from the stored cursor and reconcile missing files only after a complete pass.
- `registry-hash --resume --max-files N`: hash a bounded batch, record versions, and report duplicate candidates without merging. Add `--verify-all` for an explicit full digest check when size and mtime may have been preserved.
- `registry-status`: report registered, unregistered, excluded, unhashed, missing, retired, conflict, and reference-sync counts.
- `resource-register --path`: register one eligible file.
- `resource-resolve --id`: return current relative/absolute paths, file URI, type, status, hash state, external IDs, and the recommended Skill.
- `resource-search`: search title, path, URL, DOI, and ISBN.
- `resource-audit`: inspect missing files, manual moves, unmanaged note paths, unknown IDs, stale references, and duplicate candidates.
- `resource-retire --id`: change registry state only; never delete the file.
- `registry-export` and `registry-restore`: maintain or rebuild the portable registry state.
- `reference-refresh --changed`: incrementally rebuild the note-reference cache from current Markdown.

## Note Reference Contract

The visible URI lets a person click; the adjacent ID establishes resource identity:

```markdown
[Display name](file:///C:/current/path.pdf)<!-- mpk-resource:KB-... -->
![Description](file:///C:/current/path.png)<!-- mpk-resource:KB-... -->
<video src="file:///C:/current/path.mp4"></video><!-- mpk-resource:KB-... -->
```

The marker must follow the target on the same line. Ignore fenced and inline code. Treat untagged `file:///` links, absolute Windows paths, and configured-root-relative bare paths as `unmanaged_path_reference`. Report them; never replace them by filename guesswork.

Markdown is the source of truth for references. SQLite stores a rebuildable cache only. A sync failure does not authorize changing note text by inference.

## Controlled Moves

`resource-move --id ... --to ...` must:

1. refresh changed notes;
2. validate ID, source path/hash, target containment, exclusions, and name conflicts;
3. dry-run every exact note-link replacement;
4. create an operation journal and recovery material;
5. move the file;
6. update SQLite and export the manifest;
7. call the installed `obsidian-vault-notes` transactional batch editor with original note hashes;
8. verify file, registry, manifest, links, and PDF index metadata.

On failure, restore all parts whose current hashes still match the transaction. Return `partial_write` and retain recovery material when complete rollback is impossible.

For a manual move, a unique SHA-256 match may produce a rebind plan. Name, size, and mtime produce candidates only and require user confirmation.

## Whole-Root Relink

`root-relink --root <new-root>` verifies `.mpk/root.json`, source-relative paths, and the configured `knowledge_root_id`; previews all URI changes; then updates manage-personal-knowledge config, the Obsidian receiver config/index, note URIs, registry state, and PDF index identity.

If the old and new roots both exist, stop. A moved root keeps its ID; a writable copy must receive a new root ID. On a new computer, verify the root marker and run `registry-restore` from the portable manifest.

## AI Resolution Rule

When input contains a `KB-...` ID, call `resource-resolve` once before opening or routing the file. Unknown or missing IDs trigger audit; they never trigger an automatic download.

## Resolution Views

The owned `resource-resolve --id` CLI returns current identity, paths, type, status, hashes and external IDs without querying path/version history. Use `--history --history-purpose "<specific question>"` only for a history-dependent decision; pages default to 20 rows per kind and allow `--history-limit` from 1 to 100 and a nonnegative `--history-offset`. Each kind reports whether a later page exists. An offset page is not the complete history. Resolve remains read-only; pagination is over live state, so avoid combining pages across known registry changes.

Recovery manifest generation and direct Python callers use full-history behavior. They are broader local interfaces, outside the CLI history limit. Whole-response limits still apply to every public page; task-level cumulative limits require the calling workflow to track its reads.

Owned `resource-search` defaults to 20 results, caps `--limit` at 100, and accepts a nonnegative `--offset`. `page.has_more` reports a later match without returning it. Queries must be nonblank and at most 1,024 characters. Fixed identity, location, status and external-identifier columns remain intact because matches can originate in any of those searchable fields. This searches registry metadata, not file bodies; a page is not a corpus-wide absence claim. Direct Python consumers use a 1,000-row maximum.

Single-note `reference-refresh` checks target containment and existing path components for reparse points before opening either the target body or its explicitly supplied candidate. New-note previews remain supported. Dry-run actions, source hashes, diagnostic conditions and `plan_sha256` stay intact for exact review and application; response limits do not authorize dropping preconditions. These checks do not bound the changed-note discovery scan.

Changed-note reference refresh bounds metadata enumeration with `--max-scan-entries` (default 50,000; maximum 1,000,000). One lookahead entry detects exhaustion. Exhaustion stops before any deletion plan or cache write and returns `reference_metadata_scan_limit`, `scan_complete=false`, and `applied=false`; increase the explicit bound or select one note. It never infers deleted notes from a partial inventory. `--max-notes` limits selected changes (1–10,000), not examined bodies. Other internal callers keep their own scanner defaults; this metadata guard itself does not bound note bytes.

Changed-note refresh also limits all examined bodies, including unchanged notes: `--max-examined-notes` defaults to 5,000 (maximum 100,000), and `--max-scan-bytes` defaults to 64 MiB (maximum 1 GiB). Reads share the remaining byte allowance; oversized files are rejected before opening, and a bounded one-byte lookahead detects growth during a read. Exhaustion returns `reference_body_scan_limit` with the limiting boundary and no applied cache changes. Successful previews include examined-note and byte counts. Exact UTF-8 source bytes still determine hashes. These are per-invocation local scan bounds; cross-invocation task disclosure still requires caller accounting.

`registry-status` defaults to registered database counts, with `inventory_checked=false` and `manifest_checked=false`. It does not enumerate the filesystem, read the recovery manifest or query all registered paths. For those checks, use `--inventory --inventory-purpose "<specific question>"` with `--max-entries` (default 50,000; maximum 1,000,000). The overview returns counts and manifest state without raw paths, samples or scanner errors. `inventory_counts_complete=false` means counts are partial, never proof of absence. Directory enumeration is capped before sorting, with one lookahead entry. Suite diagnostics also use counts-only registry status. Direct Python status callers default to the full inventory.

Resource move and root relink return failed CLI outcomes for `rolled_back` and `partial_write`. Their original actions, context, plan hash, operation ID and recovery-log locator remain available; they are recovery evidence, not permission to retry. Raw exception/rollback text is replaced by an error code and count, and post-operation index checks expose typed component conditions without raw commands, stdout or stderr. Full diagnostic material remains in the local operation journal.


### Bounded audit output

`resource-audit` defaults to category counts and a bounded note-diagnostic page. Select one file category with `--category`; `--diagnostic-limit` defaults to 20 (maximum 100), and `--diagnostic-offset` pages both that category and note diagnostics. Duplicate-group members use the same page size and a separate `--member-offset`. Source locators, identity signals and confirmation requirements remain available; raw diagnostic messages and unrelated manifest metadata are omitted. Counts describe observed inventory, not completeness when scanning stops. The weak-move producer cap is 200.

Note scanning uses `--max-scan-entries` (50,000 by default, maximum 1,000,000), `--max-scan-bytes` (64 MiB by default, maximum 1 GiB) and `--max-notes` (maximum 10,000). A stopped scan reports its reason and cannot establish clean references. These are local scan/per-response limits, not a cross-invocation disclosure quota. File audit hash work and inventory enumeration require their own scope assessment.
