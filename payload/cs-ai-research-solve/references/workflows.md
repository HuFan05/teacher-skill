# CRS workflows

## Choose only the requested operation

For an existing project, ordinary research starts with `status` and bounded `query`/`record`/`asset` reads. Use external files for any map view, working notes and computation outputs. Do not initialize a replacement project or run the capture-through-delivery examples as an automatic sequence.

Automatic CRS research rounds are disabled by default. Keep the host agent's native reasoning flow, effort level and task-based collaborators; do not require round counts, periodic records or fixed thought steps. An explicit user request for continued research or an active Goal still governs continuation. Preserve useful observable attempts and feedback in lightweight external drafts, without a private reasoning transcript or mandatory record JSON. At research end, summarize the material available and ask whether to archive it. Keep useful drafts while awaiting that decision; they have not changed the formal map.

The user may already have requested archival work. Reuse that authorization within its scope without asking again. Formal writes (`init`, `bind-objective`, `asset --put`, `ingest`, review submission, `select`, `import`, `adopt`, `replay`, recovery or cold moves) belong to the corresponding authorized operation. A review check or external map alone does not authorize its submission or handoff. `export` and `finish` require a request for that delivery; saving a result alone does not imply either. These are agent routing instructions, not a CLI permission-token system.

## Command setup

Use the resolved Skill directory. Commands return one JSON envelope: `ok`, `status`, `data` on success, plus elapsed time. A nonzero process exit or `ok:false` is a blocked operation; read its code and reason. Use `template --out` to save a raw template directly; without it, the envelope's `data` contains the template, not the whole stdout document.

The following initialization example is only for an explicitly requested new project. Examples below use PowerShell; on macOS or Linux run the same arguments from the active shell, with `python3` where `python` does not name Python 3 and the same names as shell variables. Set `$crsSkillRoot` to the actual Skill location and choose an ordinary new project directory. Keep working inputs within their declared scope or external source location; all generated map/delivery exports and packaging scratch stay outside the entire enclosing project. Python 3.10+ is sufficient for these tools.

```powershell
$crsCli = Join-Path $crsSkillRoot 'scripts/crs.py'
$crsFolder = Join-Path (Get-Location) 'ResearchProject'
$crsProject = Join-Path $crsFolder '研究数据'
$crsWork = Join-Path $crsFolder '工作材料'
New-Item -ItemType Directory -Path $crsFolder -Force | Out-Null
New-Item -ItemType Directory -Path $crsWork -Force | Out-Null
python -B $crsCli --help
python -B $crsCli template record --kind objective --out (Join-Path $crsWork 'objective.json')
# Fill the actual question and all six project_objective fields before initialization.
python -B $crsCli init $crsProject --title 'Sorting experiment' --objective-file (Join-Path $crsWork 'objective.json')
python -B $crsCli status $crsProject
```

An existing project starts with `status` and bounded reading, not `init`. The template is a complete objective record, not a bare six-field object: fill `project_objective` with the actual statement, domain, claim scope (population, aggregation and metric), assumptions, evidence standard and completion standard, and keep its top-level statement/scope/assumptions consistent. Initialization records the question but does not solve it or approve its record for export.

The short `init --objective ... --scope ...` form records only a statement and scope. It creates a visibly `incomplete` description; it cannot express all six fields and is not the normal new-project entry. A foreign archive can deliver the same incomplete shape. To complete such a description, read its exact record, retain its ID and known statement/scope/assumptions, prepare an explicitly linked complete revision, and bind it:

```powershell
python -B $crsCli bind-objective $crsProject --objective-file (Join-Path $crsWork 'completed-objective.json') --origin 'Explicit completion of the recorded question' --operation 'bind-objective-001'
python -B $crsCli status $crsProject
```

Completion preserves the original anchor and does not inherit review. After binding, the six fields cannot be changed. New directions use distinct IDs with `extends` or `background` dependencies; selecting a related question or receiving another project's material never replaces the primary question. An incoming package may identify an anchor whose unreviewed body was omitted: its state is `unresolved` until that exact body is supplied. Do not fill the gap by choosing a different question. When the exact body arrives, inspect any reported objective conflicts: a conflicting earlier selection is restored to the originally registered anchor, and conflicting record bytes remain visible for investigation.



Objective query matches (full or brief) include applicable reviewed `presentation_corrections` beside the immutable objective text. Each note retains its original statement, scope, exact target revision and record/review/report reading commands. Read these notes together; they do not overwrite the objective, choose among multiple corrections, or change downstream research status. Unreviewed or unusable corrections are not presented as approved expression notes.

## Observe a complete workflow

Before source inspection for an authorized multi-step archive, review, adoption or handoff, begin one session outside the formal project. Keep the same file through review, command gaps and repairs; do not restart the timer at each step:

```powershell
$crsSession = Join-Path $crsWork 'archive-session.json'
python -B $crsCli workflow begin $crsSession --phase 'Source inspection'
python -B $crsCli --workflow $crsSession query $crsProject --text 'failure' --limit 10 --brief
python -B $crsCli workflow checkpoint $crsSession --phase 'Review' --note 'Reading the original argument, configuration and declared assumptions.'
```

Run the next command in a parallel tool process or a second terminal so that it can observe time spent reading or waiting without blocking the working command stream:

```powershell
python -B $crsCli workflow watch $crsSession
```

`watch` is a bounded foreground observer. It exits after session end or the first 20-minute attention event; it does not become a background service or stop research. The production threshold is fixed at 1200 seconds. One session preserves its original start time, phase history, command starts/finishes and one attention record. That record includes phase wall time, summed completed-command time and unfinished command markers; overlapping durations are not exclusive categories, and an unfinished marker does not prove a process is alive. Investigate the actual computation, I/O, review or repair cause and record the diagnosis. This observer does not decide whether research continues or launches another round; follow the user request without lowering evidence requirements:

```powershell
python -B $crsCli workflow checkpoint $crsSession --phase 'Source reference repair' --note 'Record the observed cause and what was checked here.'
python -B $crsCli --workflow $crsSession audit $crsProject
```

Only when the entire task has actually ended, close its session with the next command. The research examples below assume the session is still active; do not close it merely because this documentation section ends.

```powershell
python -B $crsCli workflow end $crsSession --note 'Record the actual outcome and remaining limits here.'
```

The global `--workflow` option precedes the research subcommand. Missing, corrupt, ended or unwritable observation sessions do not block the research command: the business command runs once and keeps its actual result and exit status. Observation failures add `observation_warnings` with phase/code/message and the existing `workflow_tracking_error` lookup; `workflow_command_observation_complete: false` marks incomplete observation for this command. The top-level `elapsed_seconds_scope: command_invocation` covers this invocation only, not complete workflow time. Do not repeat a research mutation merely to repair its timing entry. Explicit `workflow begin/checkpoint/end/watch` commands still report their own errors normally. The external session and its small OS lock file are diagnostic records, not research evidence or an additional authority store.

## Read and continue

```powershell
python -B $crsCli query $crsProject --text 'bound failure' --limit 10 --brief
python -B $crsCli query $crsProject --kind hypothesis --state accepted --limit 20 --brief
python -B $crsCli record $crsProject --revision $crsRevision
python -B $crsCli map $crsProject --out (Join-Path $crsFolder '研究地图.md')
python -B $crsCli asset $crsProject --sha256 $crsAssetHash
python -B $crsCli audit $crsProject
```

`query --brief` returns a `summary` projection, `record_complete: false`, counts/previews of evidence and dependencies, truncation markers, current selection, review status, and an executable `read_command` for the exact full record. Start project question answering here, then read the required records before making claims. Equally relevant current selections appear before history; this does not make them reviewed or usable. Historical and unreviewed matches remain visible. Omit `--brief` when the complete query response is explicitly needed. `query` matches words in stored content and provenance, not semantic truth. `--state` matches review, effect, or effective-epistemic labels. Without a query match, say the project does not currently supply that answer. Map output uses a new file path; existing output files and its companion reading directory are not overwritten. With `--out`, the default `--format obsidian` creates native block links and a sibling `<map-name>-阅读对象/` directory of Markdown views. UTF-8 text up to 2 MiB is displayed verbatim in inert code fences; binary or larger objects have an explicit metadata-only view. These are derived displays and do not replace the formal evidence. Choose `--format markdown` for the generic Markdown presentation. Without `--out`, stdout remains the generic map and creates no files. A named outer project folder contains the map and the nested formal data directory; do not scatter these across the parent Vault folder. Exact object links and revision hashes permit drilling into the evidence.

Choose research actions from the user's request and the evidence without CRS-imposed rounds. Preserve important negative results and useful byproducts with their scope, explanatory links and conditions for retrying. During ordinary research these may remain concise external drafts; formally publish them only after scoped archival authorization.

## Capture a contribution

Once the user has authorized saving this contribution, generate an incomplete record, fill it using the real work, and save UTF-8 JSON. `template --out` avoids Windows PowerShell's UTF-16 redirection default. The helper below writes assembled intake JSON as UTF-8:

```powershell
function Write-CrsJson($crsValue, $crsPath) {
    [System.IO.File]::WriteAllText($crsPath, ($crsValue | ConvertTo-Json -Depth 100), [System.Text.UTF8Encoding]::new($false))
}
python -B $crsCli template record --kind attempt --out (Join-Path $crsWork 'record.json')
```

Edit `record.json` to state the actual title, claim, scope, action, feedback, assumptions, limitations, sources, and reopening conditions. Choose the correct kind/epistemic pair. Use a new opaque ID for a distinct record. To revise the same research item, keep its ID and set `previous` to the prior exact hash. Add typed dependencies rather than relying on filenames or the reader remembering earlier conversations.

Archive evidence or include its source path in the intake. `asset --put` copies exact bytes into content-addressed storage and returns hash/size; it does not create a research record or review:

```powershell
$crsEvidence = (python -B $crsCli asset $crsProject --put (Join-Path $crsWork 'evidence.txt') | ConvertFrom-Json).data
$crsIntake = (python -B $crsCli template intake | ConvertFrom-Json).data
$crsIntake.origin = 'Research contribution with documented source'
$crsIntake.records = @((Get-Content -LiteralPath (Join-Path $crsWork 'record.json') -Raw | ConvertFrom-Json))
Write-CrsJson $crsIntake (Join-Path $crsWork 'intake.json')
python -B $crsCli ingest $crsProject --submission (Join-Path $crsWork 'intake.json') --operation 'capture-001'
```

Before ingestion, insert the returned hash/size in the record's evidence object. The intake is exactly `{schema:"crs-intake/v1",origin,records,assets,foreign_reviews}`. `assets` maps hash to file path resolved from the intake directory; use `{}` for evidence already stored or explicitly described external references. `foreign_reviews` defaults to `[]` and never grants local trust. Inspect returned new/existing/extensions/conflicts classifications. Use the same operation ID only for the same exact request.

## Review exact material in one batch

For authorized archival review, persist the research records and required evidence in the final project first. For a review of existing material, reuse its stored evidence; do not expand that request into unrelated ingestion. Keep proofs, useful failures and byproducts there; do not postpone their preservation until a temporary archive is copied back. Inspect their exact revisions and sources, then perform the argument, reproduction or computational review appropriate to each scope. Write actual methods, inputs, findings, limitations and assurance boundaries in the reports. Reuse an earlier review only when its exact material hashes, dependencies and scope still apply; a new software version alone does not require replaying the evidence.

```powershell
python -B $crsCli template review-batch --out (Join-Path $crsWork 'review-batch.json')
```

The batch is exactly `{schema:"crs-review-batch/v1",items:[{review,report}]}`. Each `review` is an existing `crs-review/v1` object, with target, decision, coverage, method, scope, findings, material hashes and truthful reviewer independence. Each `report` is a path relative to the batch file's directory, or `null` to reuse the exact report already stored under `review.report`. Use a portable logical reviewer name. `supersedes` may cite only an already admitted local review of the same target; it cannot replace a foreign review or another item inside this batch. See the [review contract](data-contract.md#review-crs-reviewv1).

After filling the actual reviews and reports, check the whole batch:

```powershell
$crsCheck = python -B $crsCli --workflow $crsSession review-batch $crsProject --submission (Join-Path $crsWork 'review-batch.json') --check-only | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or -not $crsCheck.ok) { throw 'Read all batch findings and correct the affected drafts before continuing.' }
$crsPreparedBatch = $crsCheck.data
```

Preflight collects item findings, binds the actual report bytes, and writes no project objects, HEAD or operation receipt. It neither admits reviews nor establishes research claims. Once a human or AI has completed the substantive review and checked these findings, explicitly submit the checked batch:

```powershell
$crsCommit = python -B $crsCli --workflow $crsSession review-batch $crsProject --submission (Join-Path $crsWork 'review-batch.json') --expected-batch $crsPreparedBatch.batch_sha256 --expected-head $crsPreparedBatch.snapshot --operation 'reviews-001' | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or -not $crsCommit.ok) { throw 'Review submission did not complete; inspect its exact error.' }
$crsArchiveHead = $crsCommit.data.snapshot
```

The batch advances one transaction. Reuse its operation ID only for the same request; changed report bytes or a changed HEAD require inspecting the change and preparing again. Successful admission does not upgrade a hypothesis or computation to an established result. Inspect `usable` and `exportable` and the reported scope of reopened materials versus source citations.

The single-review interface remains available for individual work:

```powershell
python -B $crsCli review $crsProject --review (Join-Path $crsWork 'review.json') --report (Join-Path $crsWork 'review-report.md') --check-only
python -B $crsCli review $crsProject --review (Join-Path $crsWork 'review.json') --report (Join-Path $crsWork 'review-report.md') --operation 'review-001'
```

For an incorrect argument, preserve the earlier record and create an appropriately reviewed correction. Use `presentation` for a presentation-only correction, `invalid` for an invalidating defect, or `narrow` for a reduced scope. Investigate strong dependants without labelling all of them false. New replacement material needs its own review. To resolve a revision selection conflict after inspection:

```powershell
python -B $crsCli select $crsProject --id $crsRecordId --revision $crsRevision --operation 'select-001' --expected-head $crsPreparedHead
```

## Reopen code and preserve outputs

The `replay` command writes captured outputs and a report into the formal project. Use it only when that preservation is within the authorized archival or review task. For ordinary research before archival consent, inspect and run the code with ordinary computation tools in an external work directory, retaining useful outputs there. Do not delete the only useful copies while awaiting the decision. For an authorized replay, inspect research code before running it. A replay specification is exactly:

```json
{"files":{"solver.py":"0000000000000000000000000000000000000000000000000000000000000000"},"argv":["{python}","solver.py"],"capture":["result.json"],"purpose":"Check the archived computation under its recorded assumptions."}
```

The zero hash is an illustrative slot, not actual evidence. Replace it with the stored code hash, declare every required input file, use explicit command arguments, and list meaningful output files in `capture`. The runner does not provide an OS security sandbox. Choose the timeout from the computation rather than treating the 20-minute observation threshold as a universal cutoff:

```powershell
python -B $crsCli --workflow $crsSession replay $crsProject --spec (Join-Path $crsWork 'replay.json') --operation 'replay-001' --timeout 3600 --temp-root $crsWork
```

The command stores stdout/stderr, declared outputs, and a replay report before temporary cleanup. Link useful outputs/report hashes from a new research record and review the result as appropriate. A successful process alone does not establish its claim. If execution or cleanup fails, retain and report the real error; do not claim successful preservation or delete the only evidence copy to hide it.

For the exact registered replay job, use `replay-status` to inspect it, `replay-cancel` to request cancellation, and `recover` to resume its registered recovery. Preserve the returned operation ID when one was generated automatically:

```powershell
python -B $crsCli replay-status $crsProject --operation 'replay-001'
python -B $crsCli replay-cancel $crsProject --operation 'replay-001'
python -B $crsCli recover $crsProject --operation 'replay-001'
```

Cancellation and recovery apply to that registered job; do not guess process IDs or delete an unknown temporary directory. Windows replays run inside a Job Object and POSIX replays in their own process group; `process_tree_policy` reports which applied.

## Finish an archive, receive, and return

When the user has requested archive delivery and the applicable reviews are admitted, publish one new local archive directory outside the formal project. Ordinary research or saving a contribution alone does not trigger this section:

```powershell
$crsDelivery = Join-Path $crsWork 'ArchiveDelivery-001'
python -B $crsCli --workflow $crsSession finish $crsProject --out $crsDelivery --max-asset-bytes 16777216 --expected-head $crsArchiveHead
```

`finish` holds the same HEAD throughout audit, map and package creation. It publishes the complete directory only after successful checks, refuses an existing destination or a changed HEAD, and cleans its own scratch directory. It does not clean arbitrary user work directories. The output contains:

- `map.md`: the complete local research map, potentially including unreviewed or withdrawn history and local evidence links.
- `reviewed-handoff.zip`: the reviewed projection for external research handoff.
- `receipt.json`: the fixed snapshot, audit and package results, including `not_locally_available` evidence.
- `README.md`: explains the local archive and which file to share.

Keep the containing directory local; share only the reviewed ZIP after the normal content/privacy inspection. The map is a view, not a second copy of the full evidence library. Preserve necessary outputs and clean any other owned reproduction copies before ending the workflow; `finish` alone does not claim the entire user task or its cleanup has ended.

The individual `map`, `audit` and `export` commands remain available. For a package alone:

```powershell
python -B $crsCli export $crsProject --out (Join-Path $crsWork 'reviewed-handoff.zip') --asset-budget 16777216
```

`export` already verifies the package it creates, and `finish` reuses that result. Do not run a second `verify-bundle` on the same unchanged output. Use `verify-bundle` when independently checking a received or changed package. Neither operation establishes a research claim.

The default budget is 16 MiB of included evidence assets; object metadata and tools add to total size. Review reports cannot be omitted. Larger evidence can use its reviewed summary, conclusion, applicability conditions, hash, byte count, and portable locator. This supports continued research by reference without claiming local replay of that evidence. If export fails for unreviewed or unclosed material, complete the relevant review or create a faithful reviewed derivative; do not remove dependency/correction context to force a pass.

The package includes README, research map, exact objects/reports, manifests, and Python tools including inventory/adoption support. Start with the README/map and inspect tool help. To receive that research in a new library, create an explicitly unset receiver and import the original question identity:

```powershell
$crsReceivedProject = Join-Path (Get-Location) 'ReceivedResearch'
python -B $crsCli init $crsReceivedProject --title 'Received research' --receive
python -B $crsCli import $crsReceivedProject (Join-Path $crsDelivery 'reviewed-handoff.zip') --origin 'Received source snapshot' --operation 'receive-001'
python -B $crsCli status $crsReceivedProject
```

The source primary hash is preserved. If its unreviewed body was omitted, the receiver shows that original hash as unresolved. Check the imported identity and its review status before continuing. Incoming reviews remain foreign until assessed and explicitly admitted locally.

To bring returned contributions into an existing project, import directly into that project; its primary objective stays fixed:


```powershell
python -B $crsCli import $crsProject (Join-Path $crsWork 'returned-handoff.zip') --origin 'Returned contribution with source snapshot' --operation 'return-001'
python -B $crsCli query $crsProject --text 'failure' --limit 10 --brief
```

`verify-bundle` reports `canonical_data_verified` separately from `human_map_verified` and `readme_verified`. Canonical verification checks declared bytes, records, review bindings, dependencies and asset availability; it does not establish research claims or authenticate reviewers. A map/template mismatch is `needs_rebuild`, so a different view layout does not invalidate intact research data and an inconsistent page is not called verified. Import using trusted local tools, then run `map` to create a fresh view from imported records and locally admitted reviews. The provided map, README and tools are not imported as research evidence; the source ZIP remains unchanged. Tool paths and hashes are checked without requiring the sender's inventory to match the local runtime.

Package verification never executes its included tools or research programs. Inspect software before running code received from another party. Import preserves reported origins and saves reviews as foreign evidence. Locally assess and explicitly admit suitable review before exporting the returned material again. Preserve IDs/revisions for unchanged objects, use new IDs or explicit revisions for new work, and include source snapshot information in the source description. The receiver chooses their research direction. Offline packages do not automatically receive subsequent withdrawals.

## Recover and move cold evidence

```powershell
python -B $crsCli recover $crsProject --operation 'capture-001'
python -B $crsCli cold-move $crsProject --sha256 $crsAssetHash --cold-root $crsColdRoot
python -B $crsCli audit $crsProject
```

Recovery is for an existing prepared publication operation or an exactly registered replay job. Preserve a conflicting third HEAD and investigate the exact operation rather than clearing locks or rewriting authority files. Cold storage must be an ordinary external directory, separate from the entire enclosing project and its ancestors; retain the registered location and byte identity.

## Adopt external research material

Choose one bounded ordinary directory or ZIP, for example a colleague's experiment folder or a foreign research archive, as `$crsExternalSource`; keep inventory and mapping outside that source. Inventory does not execute any code in the source:

```powershell
python -B $crsCli inventory $crsExternalSource --out (Join-Path $crsWork 'inventory.json') --max-files 10000 --max-bytes 1073741824
python -B $crsCli template adoption --inventory (Join-Path $crsWork 'inventory.json') --out (Join-Path $crsWork 'mapping.json')
```

Inspect every inventoried entry. Fill the mapping's source, real research records, and a retain/omit decision with reason for every file. Attach every retained asset to its role in a record's evidence; retain proofs and correctness arguments, code at its frozen commit, configurations, seeds, certificates, evaluation outputs, observations, and informative failures as needed. Reference large datasets by identifier and hash instead of copying them unless a retained claim needs their bytes; treat logs as disposable once their useful results are extracted. Equal bytes need only one retained content identity. An omit decision excludes a copy from this adoption and does not delete the original. Do not convert foreign PASS or authority labels into local trusted reviews.

```powershell
python -B $crsCli adopt $crsProject --source $crsExternalSource --inventory (Join-Path $crsWork 'inventory.json') --mapping (Join-Path $crsWork 'mapping.json') --operation 'adopt-001'
python -B $crsCli audit $crsProject
python -B $crsCli query $crsProject --kind failure --limit 20 --brief
```

Adoption rechecks the frozen source namespace and content, preserves source files, and returns its receipt and ingestion classifications. Complete the applicable real review and verify maps/exchange before claiming the research is ready for delivery; see [external sources](data-contract.md#external-sources).

## Continuation after intake and historical audits

Material-save success is separate from continuation integration. Follow [continuation checks](continuity.md) after authorized ingestion, adoption or import, and for an explicit historical relationship audit. Ordinary intake explicitly uses affected scope, including the first intake. A whole-project relationship audit requires an explicit request; absence of an earlier audit does not enlarge the scope. Save justified relations as explicit revisions and inspect checker pending items before reporting integration complete. `routes PROJECT --revision HASH` reopens predecessors and successors without changing research state.

## Browser map delivery

When readable map creation or updating is authorized, follow [web reading](web-reading.md) and use the shared `scripts/crs_web.py` builder. The preceding Markdown `map` and `finish` examples do not create the browser edition. Keep their Markdown behavior and separately verify the project launcher, main Markdown link, exact source snapshot and real offline rendering before calling the map update complete.

## Organize or upgrade a project entrance

Follow [project layout](project-layout.md). For a complete project upgrade,
use `crs_project.py upgrade` with the prepared shared home/reading model,
current snapshot, source directory and local renderer/browser. Inspect actual
visual evidence, then resume the same candidate with `--visual-review` and
`--publish`. Pending visual review or publication returns nonzero and does not
complete the request. Reopen the actual project launcher, run `check`, and use
`resolve` for the registered formal-library path. Direct formal-library CLI
usage is unaffected; this route changes only derived reading and location.

## State prediction and fixed-snapshot batch reads

```text
python -B scripts/crs.py predict PROJECT --submission INTAKE.json --reviews PROPOSED_REVIEWS.json
python -B scripts/crs.py ingest PROJECT --submission INTAKE.json --operation UNIQUE_ID --expected-head BASE_SHA --metrics
python -B scripts/crs.py records PROJECT --expected-head CURRENT_SHA --revision RECORD_SHA_A --revision RECORD_SHA_B
```

`predict` uses declarations and existing metadata only. Read its assumptions and per-revision reasons before actual processing; neither predicted accept nor successful save means usable. Proposed reviews remain hypothetical until explicit normal admission. `records` accepts 1..100 distinct exact revisions from a fixed HEAD and returns their full records, state and provenance with one project-view load. A missing revision or stale base fails the entire batch. Output remains bounded by the global response contract; reduce batch size or justify a larger exact response instead of interpreting omitted output as absence. No asset-index rebuild or display cache is performed by these commands.


For current recursive scope and ranked 512 MB multi-volume delivery, use [storage and delivery operations](storage-delivery-operations.md#ranked-size-bounded-export). A finish receipt names either the ZIP or the complete parts directory; do not hard-code a ZIP path.

## Export with explicitly reviewed portable history

After source-bound portable derivatives have been archived and reviewed, create a new external JSON plan:

```json
{"schema":"crs-portable-history/v1","snapshot":"<current source snapshot SHA-256>","replacements":[{"original":"<original revision SHA-256>","replacement":"<reviewed direct revision SHA-256>"}]}
```

Use `export-preflight PROJECT --out EXTERNAL.json --portable-history PLAN.json`, then `export PROJECT --out EXTERNAL.zip --portable-history PLAN.json`. The plan is an explicit complete mapping, not a path-removal script or review declaration. Read the [portable history contract](data-contract.md#portable-history) and reconcile the resulting coverage. Exports without the option contain no portable replacement locators.


### Declared archive preparation and exact batch reads

Use `prepare-archive PROJECT --submission INTAKE.json --review-batch BATCH.json --expected-head SHA --out NEW_EXTERNAL_CHECKLIST.json` before expensive ingestion of already authored contributions and review reports. Repeat `--revision SHA` for known old records. See [bounded archive preparation](archive-review-operations.md#bounded-archive-preparation) for bounds, missing-material handling and native admission steps. The output is advisory; preserve actual review and transaction byte checks. Follow ingest, explicit batch preflight and exact commit with one `records --expected-head COMMITTED_SHA --revision SHA ...` call for known final revisions.

The `crs_continuity.check` API and CLI run inside one single-invocation read scope. Nested callers reuse that scope, and it releases handles on exit; native fallback and full plan/receipt semantics are unchanged. This does not introduce a general query or write cache.
