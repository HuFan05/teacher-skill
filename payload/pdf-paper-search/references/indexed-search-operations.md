## Indexed Search

Use the indexed searcher first when the relevant SQLite shelf is known or easy to narrow.

For the normal one-call route, require one explicit database and use the
Observer wrapper:

```powershell
python "<SKILL_ROOT>/scripts/observer_run.py" --skill pdf-paper-search --catalog pdf-paper-search/v1 --phase pdf-paper-search.script.paper_locate -- python "<SKILL_ROOT>/scripts/paper_locate.py" --query "..." --db "<SHELF_ROOT>/pdf-library.sqlite3" --json
```

The command always emits the bounded `paper-locate/v1` JSON wire format.
`--db` is mandatory; missing/incompatible index state returns
`coverage_gap`, not an automatic scan of unrelated databases. Each core or
expanded stage checks at most eight different PDFs, reads `±1` pages when the
seed is not already verified, and performs at most one expanded-alias pass.
Queries with a recognized exact structural anchor (complexity bound, formula,
model variant, dataset–metric pair, or reported number) may additionally inspect
at most 96 FTS-retrieved anchor pages; this is a bounded rescue, never a
whole-library scan.

Prefer compact top-3 output while investigating. It keeps the search limit for ranking, but only sends the top three evidence summaries into model context:

```powershell
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
python "<SKILL_ROOT>/scripts/paper_search.py" --query "..." --alias "..." --db "<SHELF_ROOT>/pdf-library.sqlite3" --limit 12 --compact
```

Notes:

- add multiple `--alias` flags when needed
- require explicit `--db` paths or an explicit `--data-root` before discovering SQLite shelves
- use full `--json` instead of `--compact` only for ranking debug, regression work, or when the compact top 3 is genuinely ambiguous
- inspect `diagnostics_summary` before treating an empty result as a real miss
- full `--json` includes diagnostics, key features, and score breakdown for ranking/debug work
- use `--verify-rank N` to inspect the candidate's `PageEvidence` and local statement window before reporting a fragile exact hit
- environment defaults do not replace the explicit `--db` or `--data-root` source requirement at this public entrypoint
- if `--db` is omitted, supply `--data-root` to constrain discovery to the selected data root; without either explicit source, the public entrypoint rejects the request before discovery
- use `python -m obsidian_local_kb pdf-survey --db "...sqlite3"` to backfill survey metadata for an existing shelf
- use `python -m obsidian_local_kb pdf-survey-query --db "...sqlite3" --query "..."` to inspect likely papers, books, and sections before page verification

