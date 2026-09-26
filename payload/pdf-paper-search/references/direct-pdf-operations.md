## Direct-PDF Fallback

Use the fallback when one or more of these hold:

- the exact source is likely in a local PDF folder that is not covered by the current SQLite shelves
- indexed top results are all `nearby material` or obviously off-domain
- a filename/path inventory strongly suggests a specific PDF or course folder
- the query is a long symbolic statement, equation, or table row whose best match is more likely to be found by local text extraction than by BM25 over mixed shelves

Always narrow first by likely folder, explicit PDF path, or arXiv identifier in the filename if possible.

Prefer compact top-3 output while investigating:

```powershell
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
python "<SKILL_ROOT>/scripts/direct_pdf_search.py" --query "..." --root "<PDF_ROOT>" --limit 8 --compact
```

Or target an explicit file:

```powershell
python "<SKILL_ROOT>/scripts/direct_pdf_search.py" --query "..." --pdf-file "<PDF_ROOT>/book.pdf" --limit 8 --compact
```

Notes:

- the direct fallback caches `pdftotext` output under `~/Documents/.pdf-paper-search-cache`
- if filenames are opaque, build a folder survey first with `python -m obsidian_local_kb pdf-library-survey --root "<PDF_ROOT>"`; `direct_pdf_search.py` will automatically use that inventory for the same root
- use full `--json` instead of `--compact` only for extraction/debug details, ranking regressions, or genuinely ambiguous compact top-3 results
- inspect `file_selection` to see how many PDFs were considered, selected, and positively scored before page search
- inspect `diagnostics_summary`; do not treat no-hit as conclusive if relevant DB/PDF/read errors exist
- use `--score-files-only` on broad folders to check file-level narrowing before expensive PDF extraction
- use `--file-ranking-limit N` with `--show-file-ranking` to keep path-heavy debug output small
- it is fast after the first extraction, but still much slower than indexed search if you point it at too many PDFs
- prefer a short filename/path inventory step before using it on a broad folder

### PDF Text Extraction Failures

Treat PDF text extraction failure as a tooling failure, not as evidence that the PDF lacks the queried statement, algorithm, equation, or result.

Before trusting an empty direct-PDF result for an explicit likely source PDF:

1. Verify that `pdftotext` actually produced cache text for that PDF and that the text has nontrivial length or page separators.
2. If stderr mentions MiKTeX first-run setup, `CreateDirectoryW`, `Access denied`, or a receiver-local MiKTeX profile path, rerun the extraction once with a writable output path and `MIKTEX_LOG_DIR` under the PDF-search cache or workspace.
3. If the sandbox blocks MiKTeX initialization under AppData, request escalation for the same narrowed extraction command. Do not broaden the search or report a miss before this recovery attempt.
4. If MiKTeX/`pdftotext` still fails, use a Python PDF text extractor such as PyMuPDF or pypdf when available, then search the extracted page text manually.
5. After extraction succeeds, rerun the narrowed direct search or inspect the extracted pages directly. Only then may a no-hit result count as search evidence.

Useful diagnostic pattern:

```powershell
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$env:MIKTEX_LOG_DIR = "<CACHE_ROOT>/logs"
pdftotext -layout -enc UTF-8 "<PDF_ROOT>/book.pdf" "<WORKSPACE>/book.txt"
```

