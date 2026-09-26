# One current project entrance

The enclosing folder is the common starting point for people and AI. Keep
`00-打开研究地图.html` as the browser launcher, opening the research homepage
directly; `00-项目入口.md` is its Obsidian counterpart. `.crs-project.json`
selects the same current reading and the sole formal library. An extra project
menu between the launcher and the research home is not the default route.

## Directory responsibilities

| Role | Default directory | Responsibility |
|---|---|---|
| formal_library | 研究数据 | Existing sole formal authority, written only by authorized CRS operations |
| reading | 阅读视图 | References to current external browser and Markdown delivery |
| sources | 原始材料 | Incoming source material and archival provenance |
| work | 工作区 | Pending attempts, computations and unresolved material |
| history | 历史记录 | Historical identities, provenance, intake/adoption and verification records; full backups external |
| deliveries | 交付包 | References to external requested exchange packages; no ZIP payloads |

Create directories when used. A valid existing formal-library name remains
valid: register it accurately instead of moving it merely for visual order.
Dates belong inside the relevant role directory. README and other existing
launchers link to the current entrance; preserve their original material in clearly
identified history and repair affected links. Do not append competing latest
entry announcements.

Before physical organization, inventory each existing path, responsibility,
intended destination and incoming references. Inspect formal cold-location references
and historical dependencies. Move safe material with guarded path and hash
checks and repair relative links. Record concrete retained exceptions;
labels alone do not establish physical consolidation. Never copy a second
formal authority, delete a unique source, change HEAD or upgrade review trust.
Preserve concurrent updates and refresh only affected bindings.

## Complete upgrade entry

Prepare the shared reading model described in [web reading](web-reading.md).
The external upgrade configuration has exactly `title`, `formal_library`,
`snapshot`, `markdown`, `roles`, `retained`. The Markdown binding contains `path`,
`sha256`, `snapshot`; the author establishes its correspondence to that source
snapshot. Roles use the IDs above, with the actual formal-library path. Retained
items contain `path`, `role`, `purpose` for every retained nonstandard root
entry. Do not hide a competing formal library behind a retained entry.

```text
python -B scripts/crs_project.py inspect PROJECT
python -B scripts/crs_project.py upgrade PROJECT --config PREPARED_CONFIG --content PREPARED_CONTENT --out NEW_CANDIDATE --renderer-dir LOCAL_MATHJAX --source-dir LOCAL_SOURCES --node LOCAL_NODE --playwright-module LOCAL_PLAYWRIGHT [--browser-channel chromium]
python -B scripts/crs_project.py upgrade PROJECT --out SAME_CANDIDATE --resume --visual-review EXACT_VISUAL_REVIEW --publish
python -B scripts/crs_project.py check PROJECT
python -B scripts/crs_project.py resolve PROJECT
```

The first command prepares and checks the candidate. It returns nonzero while
visual review or publication remains pending. After actual visual inspection,
resume that same operation; do not create a new operation to bypass a failure.
The final step publishes the checked site and its browser/visual evidence into
the external candidate’s `delivery/` child, then writes the stable launchers
and manifest with prior-byte guards. An existing unrelated output, changed
HEAD, changed entrance or stale evidence blocks the operation. Partial
publication is retained and resumed by exact file identity.

A complete `crs-project-navigation/v1` manifest retains the navigation fields and
adds `delivery`: `external_root`, `content_sha256`, `browser_report`, `visual_review`,
`tool_version`. Markdown, browser and report paths are absolute checked external paths. Each report binding has `path` and `sha256`; all reports must remain under the declared external delivery root. `browser.status`
must be `ready` and its path identifies the current research homepage.
`check` reopens current formal HEAD, exact site inventory, all language links,
source-bound home and manuscript inventory, actual browser coverage, visual evidence
and generated entrance bytes. It reports only derived-reading assurance.

## Navigation-only entrance

Direct formal-library arguments to `crs.py` never depend on an entrance.
`crs_project.py build/check/resolve` also accepts a navigation-only
`crs-project-navigation/v1` manifest without `delivery`; it proves navigation
consistency only. Internal reading copies are never treated as a complete
delivery. Only a manifest with `delivery` can report the externally located
complete delivery. Low-level Markdown or web structural success, a standalone
portal, or a pending browser state never completes an explicitly requested full
project upgrade.

Guarded publication currently requires Windows and external operation/backup directories on the same volume as the project. Recursive project and delivery checks run before and after publication; a project that still contains duplicates must first be consolidated explicitly. Whole prepared files are transferred by guarded renames. The original entrance is held exclusively in its registered backup while the complete replacement acquires the vacant name. Concurrent files and edited backups block recovery; prefixes never establish ownership. Linked entrances are rejected. Interrupted copy evidence remains outside the delivered site in the operation history; failed browser checks remain in the private operation. Other platforms can prepare and inspect candidates but cannot claim this guarded publication path.
