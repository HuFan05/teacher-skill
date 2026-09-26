# Cross-Note Maintenance

Use this workflow when one change may affect wikilinks, headings, block anchors, tags, or repeated text in other notes.

## Workflow

1. Run `vault_impact.py`; it never writes.
2. Resolve the target uniquely. A duplicate title returns `ambiguous_target`; rerun with the exact relative path.
3. Review candidate relative paths, reasons, links, and smallest useful snippets. Full candidate bodies require the active read package.
4. Convert approved mechanical changes into an explicit manifest.
5. Run `vault_batch_edit.py` without `--write` and inspect every precondition and planned result.
6. Inspect a narrow Git diff or equivalent preview.
7. Run the same manifest with `--write` only under `edit_allowed`.
8. Require `transaction_status=applied`; inspect the final diff and run checks.

Do not batch-edit fuzzy matches and do not treat a backlink as permission to read an entire note.

## Impact Analysis

```powershell
python "<SKILL_ROOT>\scripts\vault_impact.py" `
  --target-note "快速排序的期望比较次数" `
  --heading "命题1.1" `
  --block-anchor "^59709c"
```

The analyzer ignores fenced-code pseudo-links and reports:

- `direct_backlinks`;
- `outlinks`;
- `heading_or_block_references`;
- `suspected_link_updates`;
- `broken_links` found in the target or candidate notes examined for this impact request;
- `suggested_reads`.

If a title or basename resolves to several paths, output the candidates and stop. Do not bind the title to the first sorted file.

## Manifest

Version 2 is preferred; a versionless manifest is also accepted.

```json
{
  "version": 2,
  "operations": [
    {
      "file": "相对路径/笔记.md",
      "operation": "replace-wikilink",
      "old_wikilink": "[[旧笔记#旧标题]]",
      "new_wikilink": "[[新笔记#新标题]]",
      "expected_sha256": "hash of the original file"
    }
  ],
  "assertions": [
    {
      "file": "相对路径/笔记.md",
      "must_contain": [
        {"text": "^block-id", "min_count": 1, "max_count": 1},
        "[[目标笔记|短别名]]"
      ],
      "must_not_contain": ["\\`"]
    }
  ]
}
```

Supported operations are `replace-text`, `replace-wikilink`, and `append-section`. Use the single-file editor or a reviewed dedicated patch for more complex prose or structure.

Multiple operations for one file are applied in manifest order to one in-memory copy. Every expected hash for that file refers to the same original snapshot; conflicting preconditions are errors.

`assertions` is optional. It checks the final in-memory text after all operations, both in dry-run and when `--write` is supplied. Each assertion needs `file` or `relative_path`. `must_contain` accepts a string, or an object with `text`, `min_count`, and optional `max_count`; a plain string means at least one occurrence. `must_not_contain` is a list of literal strings that must occur zero times. Assertions can guard block ids, source-status labels, wikilinks, exact Markdown delimiters, and occurrence counts. If one assertion fails, the whole batch stops before any target is written.

## Transaction Semantics

The batch editor has two phases.

### Preflight

- resolve every target inside the Vault;
- read strict UTF-8 snapshots;
- validate hashes and mtimes;
- validate all operation arguments;
- compute every final file in memory;
- check all assertions against that final text;
- write nothing if any item fails.

Preflight failure returns nonzero, `ok=false`, `transaction_status=preflight_failed`, `applied_count=0`, and per-item errors.

### Apply And Rollback

- create a transaction journal and raw-byte recovery copies;
- recheck each current file before replacement;
- replace changed files through same-directory temporary files;
- on failure, restore every file already replaced and verify original hashes.

Status meanings:

- `preflight_failed`: target resolution, decoding, precondition, or in-memory transformation failed before recovery preparation; no target was written;
- `applied`: all planned changed files were written and verified;
- `prepare_failed`: recovery material could not be prepared, so no target replacement began; nonzero exit;
- `rolled_back`: a write failed and every prior replacement was restored; nonzero exit;
- `partial_write`: rollback could not restore every file; nonzero exit and retained recovery evidence.

Hash mismatch is an error, not a skipped successful change. `changed_count` and `applied_count` count manifest operations; an operation is never counted as applied unless the complete transaction succeeds. File-level results identify the distinct files written.

## Boundaries

- Cross-file replacement cannot provide a native filesystem transaction. The implementation provides complete preflight, a journal, atomic per-file replacement, and hash-guarded compensating rollback. If a file changes externally after this transaction writes it, rollback refuses to overwrite that newer content and reports `partial_write` with recovery evidence.
- Do not use whole-Vault replacements, unreviewed regular expressions, or fuzzy candidate writes.
- Do not convert the single-file editor into a hidden multi-file tool.
- Do not delete recovery evidence after `partial_write`.
- Commit only after a successful transaction, narrow diff review, and explicit `commit_allowed`. Never push.
