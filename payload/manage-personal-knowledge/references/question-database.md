# Unified Question Collections

The structured question interface is a local federation of CS/AI problems and research questions (for example algorithm and data-structure exercises, course problem sets, interview problems, or open research questions) over two compatible adapters:

- registered numbered Markdown folders inside the configured Vault;
- imported structured Markdown collections anywhere below the configured `knowledge_root`.

Source Markdown remains canonical and read-only. SQLite/FTS5 is derived state under `%LOCALAPPDATA%\manage-personal-knowledge` (or `MPK_STATE_DIR`), never a second record store.

## Canonical scope

`question-search` searches every source reported by `question-status`. A result preserves its adapter, collection or source identity, source-relative Markdown path, question number, line bounds, full `content_md`, image references, and source-page markers when present.

Coverage has two distinct parts:

1. every registered source completed its last scan or import;
2. bounded discovery under the configured question-collection root found no structured collection that remains unimported.

`coverage_complete=true` is legal only when both parts pass. It never means that arbitrary PDFs have been OCRed or converted into structured questions.

The default discovery root is `数据/题库` relative to `knowledge_root`. A candidate directory is recognized when it has a top-level numbered Markdown source and at least one collection marker: `source-manifest.json`, `work/receipt.json`, or `work/collection_index.sqlite3`. Discovery is read-only, bounded, skips hidden and reparse-point directories, and reports truncation or scan errors as `coverage_gap`.

## Import workflow

Use the same public workflow for Vault folders and knowledge-root collections:

```powershell
python "<SKILL_ROOT>\scripts\manage_kb.py" question-discover --json

# Vault folder: source path is Vault-relative
python "<SKILL_ROOT>\scripts\manage_kb.py" question-import `
  --scope vault `
  --source-relative "数据/题库/2020—2025年算法面试真题逐题库" `
  --json

# External collection: source path is knowledge-root-relative
python "<SKILL_ROOT>\scripts\manage_kb.py" question-import `
  --scope knowledge-root `
  --source-relative "数据/题库/机器学习/2024/某题集" `
  --json
```

The default call is a dry-run `question_import_plan`. It resolves the source inside the declared root, rejects path escape and reparse points, parses numbered level-three headings, hashes every accepted Markdown source, counts documents and questions, checks an optional `work/receipt.json`, and returns `plan_sha256` without writing the index.

Apply only the unchanged plan:

```powershell
python "<SKILL_ROOT>\scripts\manage_kb.py" question-import `
  --scope knowledge-root `
  --source-relative "数据/题库/机器学习/2024/某题集" `
  --write `
  --expect-plan-sha256 "<preview plan_sha256>" `
  --json
```

The write re-resolves, re-reads, and re-hashes the source before opening one SQLite transaction. A missing or stale plan hash, changed source bytes, root mismatch, incomplete scan, or malformed UTF-8 source stops before replacement. Re-import replaces only that collection's derived rows. It never executes scripts shipped inside the collection, trusts a collection-owned SQLite index as authority, moves originals, or rewrites Markdown.

### Verification status

- `verified`: `work/receipt.json` states `status=verified`, and both `problem_count` and `verified_count` equal the parsed question count.
- `unverified`: no receipt was supplied; import is allowed but the status remains explicit.
- `receipt_mismatch`: receipt counts or status disagree with parsed content; import may be previewed but must not be described as verified.
- `receipt_invalid`: a receipt exists but cannot be read as valid JSON.

Verification status is source metadata, not evidence that every problem statement, reference answer, or transcription is correct.

## Compatibility

The direct refresh command is also available:

```powershell
python "<SKILL_ROOT>\scripts\manage_kb.py" question-index `
  --source-relative "数据/题库/2020—2025年算法面试真题逐题库" `
  --json
```

It directly refreshes a Vault source for automation. Interactive workflows should prefer `question-import --scope vault`, which adds the hash-bound preview/write gate before invoking the same incremental indexer.

## Status and search

```powershell
python "<SKILL_ROOT>\scripts\manage_kb.py" question-status --json
python "<SKILL_ROOT>\scripts\manage_kb.py" question-search `
  --query "自注意力 复杂度" `
  --alias "self-attention complexity" `
  --alias "序列长度 二次" `
  --json
```

`question-status` returns the Vault adapter, imported collections, discovered-but-unimported candidates, total documents/questions, the discovery scope, and federation-level coverage. Do not interpret a source adapter's local `coverage_complete` as federation-level completeness.

`question-search` merges adapter results and keeps `result_groups` separate for audit. A lexical candidate still requires verification of the requested problem structure (task, setting, assumptions, and constraints). An empty result is:

- `not_found_in_indexed_questions` only when federation-level coverage is complete;
- otherwise `coverage_gap`.

The fallback remains Vault/PDF retrieval. Phrase every empty result as “not found in covered structured questions,” never “the problem does not exist.”

## Supported Markdown shape

A question begins at `### 12` or `### 12.1` and ends before the next numbered level-three heading. The enclosing level-two heading becomes its section. Markdown, TeX, code blocks, image references, optional `<!-- source-page: ... -->` markers, line bounds, and source filenames are retained.

Vault imports may contain multiple Markdown files recursively, matching the Vault source contract. Knowledge-root collection imports accept one Markdown file or the top-level non-underscore Markdown files in one collection directory. This prevents an explicit collection import from recursively absorbing unrelated work files, renders, OCR output, or review artifacts.

Owned question-search validates a nonblank query up to 1,024 characters, at most eight bounded aliases and 1–100 selected results before source access. Its coverage attachment contains federation/adapter coverage, root matching, discovery completeness/error count and unimported count; it does not repeat full source or unimported-candidate inventories. Use question-status or question-discover when those inventories are needed. Full selected question Markdown, assumptions, image/page markers and provenance are returned in full.

Direct `question-index` can commit derived rows despite an incomplete source scan. Its owned completion view therefore retains `index_committed=true`, source scan completeness, selected source, document/question counts and scan-error count. A partial result returns a nonzero exit with `index_committed_with_coverage_gap`, not an unapplied-operation claim. Raw scan errors and the internal database path are omitted. These flags describe this source/index operation, not federation-wide coverage. The hash-bound `question-import` workflow is separate.


### Bounded question inventory views

`question-status` returns counts, root/adapter coverage and discovery conditions by default. Use `--inventory --inventory-purpose PURPOSE` for source orientation, with `--inventory-limit` (default 20, maximum 100) and `--inventory-offset` applied separately to each inventory category. `question-discover` returns the same bounded candidate page directly. Counts and `has_more` describe local scanned inventories; pagination does not establish complete discovery. Raw scan errors, full nested inventories and question bodies are omitted. These are per-response disclosure limits; local status aggregation still evaluates registered source metadata and discovery still uses its directory budget. No cross-invocation quota is claimed. Discovery additionally limits total enumerated directory entries with `--max-entries` (default 50,000; maximum 1,000,000). Enumeration stops before sorting an unbounded directory; one extra entry is observed to detect exhaustion. A limit hit reports incomplete discovery, so narrow the named discovery scope or justify a larger budget before relying on absence. Reparse entries and marker paths are excluded.
