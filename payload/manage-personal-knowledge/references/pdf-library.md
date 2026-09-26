# PDF Library Index And Handoffs

## Index Contract

The state database is `library.sqlite3`. Keep `pdf_docs`, `pdf_pages`, and `pdf_page_fts` compatible with `$pdf-paper-search`. Extra columns may track source size, mtime, extraction status, method, warning, and timestamps.

Index only the existing PDF text layer. Use `pdftotext -enc UTF-8 -layout`, preserve form-feed page separators, and use an ASCII temporary input path when a tool cannot handle the source path. Never edit the input PDF.

Document states are:

- `pending`: discovered but not yet extracted
- `indexed`: at least one page has useful text
- `no_text`: extraction succeeded but produced no useful page text
- `error`: the extraction failed; keep the diagnostic

Use file size and nanosecond mtime for incremental invalidation. Reindex changed documents, skip unchanged ones, and remove missing database rows only after a complete successful filesystem scan.

When a PDF is registered, search results include its `resource_id`; `page` may resolve either `--id` or a relative `--path`. Registration does not change PDF text extraction or page numbering.

`index --resume` processes only `pending` documents. `no_text` and `error` are completed inventory states, so a permanently damaged PDF does not make every resume retry it. Use `--retry-errors` only after repairing the extractor or when the user explicitly asks to retry failures. For schema v1 configurations and indexes without root identity, an absolute library-root mismatch still stops search and page commands until a confirmed rebuild. For schema v2, an unchanged `knowledge_root_id` plus unchanged root-relative library path permits whole-root relocation: verify current files and metadata without re-extracting every unchanged PDF.

## Search Contract

Use FTS5 with versioned normalized ASCII/notation terms (Unicode and TeX Greek letters, operators such as `argmax`, and a Chinese CS/AI term bridge kept identical to the `$pdf-paper-search` query canonicalizer) plus CJK runs and bigrams. Merge the primary query and repeatable aliases, then apply modest title/path boosts. Return the relative path and title, one-based PDF page, excerpt, score, match reasons, extraction state, index coverage, and warnings.

An empty FTS result is conclusive only for the successfully indexed portion. Always include pending, no-text, and error counts.

The derived FTS records `search_format_version`. When the lexical contract
changes, search fails closed until `index --resume` transactionally rebuilds FTS
from existing `pdf_pages.content`. This upgrade preserves extracted page rows
and never invokes `pdftotext` for unchanged PDFs. `status` exposes the installed
and expected versions plus `search_rebuild_required`.

## Paper-Location Handoff

For normal source location, run `paper-locate --query "..." --alias "..." --json`.
It calls `$pdf-paper-search`'s dedicated `paper_locate.py` with exactly one
explicit unified database. The bounded `paper-locate/v1` package contains status,
at most three verified candidates for where an algorithm, theorem, definition,
result, table, figure, or claim is stated, one-based PDF pages, local evidence,
matched/missing hard concepts, coverage, stop reason, and stage timings.
Evidence uses `origin` (`retrieved` or `adjacent`), `statement_window`, and an
`extraction` object containing status, method, and warning. `printed_page`
remains `null` unless separately verified. A valid `coverage_gap` is a handled,
truthful result (`ok: true`) at this managed boundary, not a reason to start an
unbounded fallback; `failed` alone uses `ok: false`. Keep `paper-search` only for
ranking diagnosis.

Set `OBSIDIAN_LOCAL_KB_ROOT` before launching the specialist so its shared query
and PDF modules resolve from the installed `obsidian-vault-notes` Skill. Never
expose subprocess command lines because they contain the user's query.

## OCR Boundary

OCRmyPDF and Tesseract are a prepared standalone toolchain. The isolated `tessdata` directory contains the selected trained-data files plus Tesseract's local `configs` and `tessconfigs`, which OCRmyPDF needs for outputs such as hOCR. `ocr-one` creates a new searchable PDF and sidecar text, never overwrites the input, and does not update `library.sqlite3`. Default languages are `eng+chi_sim`; use `eng+chi_tra` or all three only when the document requires them. Formula, pseudocode, and table recognition remain unverified.

Owned `library-search` accepts 1–100 results, a nonblank query up to 1,024 characters and at most eight nonblank aliases of the same maximum length. It validates these before index access. Selected snippets, paths, page numbers, resource IDs and extraction-quality metadata remain exact; broad index paths and raw scan diagnostics are replaced by typed coverage/counts. Missing or incomplete coverage cannot support a library-wide absence claim. Page reads retain the explicitly requested full page text within the common response budget; search snippets are not replacements for source verification.

Owned `paper-search` uses the same 100-result, 1,024-character query and eight-alias input ceilings. Its outer result retains the configured `pdf-paper-search` public evidence package under `data`, including selected statements, provenance, coverage and typed downstream diagnostics. It omits the process command, raw stderr and duplicated diagnostic wrapper, while retaining return code and known failure code. This is delegated evidence from the installed specialist, not a claim that arbitrary replacement producers are schema-verified; the common whole-response cap still applies.

The owned `index` completion view reports selected/processed/indexed/error/no-text/remaining counts, inventory changes and scan-error count, lexical-format rebuild conditions and typed final coverage. It omits raw scanner errors and broad index-root fields. Indexing processes the explicitly configured local library according to the resume/max-files options; the output projection is not a source-processing or task-disclosure quota. Source files and extracted page bytes remain unchanged by projection.
