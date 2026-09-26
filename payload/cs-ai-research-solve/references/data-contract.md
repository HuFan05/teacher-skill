# CRS data contract

Software release: v1.0.0. Data schemas are versioned independently of the software release. Every research record uses `crs-record/v1`; a complete objective record additionally carries `project_objective`, while an incomplete objective description omits it. A tool upgrade does not rewrite unchanged research evidence.

## Bytes, identities, and references

`crs_model.canonical` produces sorted compact UTF-8 JSON, with string keys and finite numbers. `parse_json` rejects duplicate keys and invalid UTF-8; SHA-256 identifies exact stored bytes. JSON field order/whitespace is normalized before hashing records and reviews. Raw code/data/proof bytes are not silently normalized.

A research `id` is an opaque stable identity. A revision is the SHA-256 of one complete record. `previous` links a new revision of that identity. A different byproduct uses a different identity and typed dependencies. The same record arriving from another source adds provenance. It does not become independent corroboration merely by arriving twice.

Cross-project reuse retains the original ID and revision when it is the same research object. Import preserves reported origins but does not authenticate authors or inherit review authority. A conflicting revision remains stored until an explicit selection resolves the preferred revision; do not equate the latest arrival with the preferred result.

## Record: `crs-record/v1`

All fields below are required; unlisted fields are rejected.

| Fields | Contract |
| --- | --- |
| `schema`, `id` | Exact schema value and nonempty opaque identity. |
| `kind` | `objective`, `attempt`, `result`, `hypothesis`, `observation`, `failure`, `definition`, or `correction`. |
| `title`, `statement`, `scope` | Nonempty accurate text; scope includes the relevant domain and claim scope (population, aggregation and metric). |
| `action`, `feedback` | Nonempty descriptions of method/action and outcome; objective/definition may use empty text. |
| `epistemic` | `result` uses `established` or `refuted`; `objective` uses `hypothesis`; every other kind uses its own name. The objective label expresses an unresolved research question, not evidence that an answer is true. |
| `assumptions`, `limitations`, `reopen`, `sources` | Arrays of nonempty strings; empty arrays are allowed. Use meaningful reopening conditions and source descriptions. |
| `evidence` | Array of the exact evidence objects below. |
| `dependencies` | Array of the exact dependency objects below. |
| `conditional_on` | Unique dependency revision hashes explicitly relied on conditionally; each belongs to a `premise`, `input`, or `term` dependency. |
| `previous` | Prior revision hash or `null`. |
| `correction` | `null` except for a correction record, where the correction object below is required. |

An `established` result is supported by a proof or correctness argument (CS theory) or by reproduction evidence within its declared claim scope (empirical work). A `hypothesis` that held in experiments remains a bounded `observation`, not a general result; a rejected proof or experiment is not a refutation of its claim.

Evidence object: `{sha256, bytes, name, role, summary, locator}`. Hash is lowercase SHA-256; bytes is a nonnegative integer. Name and role are nonempty strings. Summary/locator are strings and may be empty only when the full evidence is available and will not need omission. Multiple logical references may share one byte identity. Roles are descriptive (for example `proof`, `argument`, `code`, `configuration`, `environment`, `input`, `evaluation`, `certificate`, `verifier`, `report`), not research verdicts. A missing large asset can be represented by an exact hash, expected size, reviewed summary, and locator; availability and assurance must remain explicit. Reference a dataset by identifier and exact hash unless the retained claim needs its bytes; keep a checkpoint only when a retained claim cannot be reproduced without it.

Dependency object: `{id, revision, relation, reason}`. `relation` is `premise`, `input`, `term`, `background`, `extends`, `corrects`, or `supersedes`. Give a nonempty reason. The referenced record must match both identity and revision. The first three relations carry research dependency effects. Background and extension links do not automatically propagate invalidity, although an exchange still requires its structural record references to close.

Correction object: `{target, effect, reason, replacement}`. Target is a record revision; effect is `presentation`, `invalid`, or `narrow`; reason is nonempty; replacement is another revision or `null`. The correction is a separately reviewed record. A replacement never inherits acceptance automatically. A later applicable correction of a mistaken correction may restore the original record's usability.

## Stable primary objective

A complete objective is a `crs-record/v1` objective record with the additional required `project_objective` object containing exactly `statement`, `domain`, `claim_scope`, `assumptions`, `evidence_standard`, and `completion_standard`. `assumptions` is a list of nonempty strings; the other fields are nonempty strings. `claim_scope` states over which population the claim quantifies (datasets, tasks, models, scales, seeds or inputs), with which aggregation (for example mean±sd over N seeds, worst case, all inputs of size n, or with high probability) and with which metric. Its top-level `statement`, `scope`, and `assumptions` equal the identity's statement, domain, and assumptions respectively. Other record kinds never carry `project_objective`.

`init --receive` creates an explicitly unset receiving library. Its first import adopts the package's declared primary hash, including an unresolved hash when its unreviewed body was omitted. Receiving an existing question does not manufacture another objective record. Importing into a project that already has a primary objective keeps that existing identity.

The snapshot's `objective` preserves the initial primary revision hash. The research identity remains fixed while metadata, evidence, feedback and reviews may grow. A different direction uses its own ID and an explicit `extends` or `background` link; it cannot become the primary objective by selecting it or importing its package. A project whose purpose changes needs a new project or an explicit fork. Record selection chooses a revision of an item, not a replacement project question.

`objective_identity` reports `completeness`, `identity_sha256`, `known_fields` and `missing_fields`. An objective description without `project_objective`, for example one received from a foreign archive or created with the short `init --objective` form, is `incomplete`: preserve its known statement, scope and assumptions and show absent identity fields. The initial anchor can be `unresolved` when an incoming package identifies it but properly omits its unreviewed body; that does not authorize choosing another included objective. When its exact body arrives, the identity can be assessed.

When an initially omitted primary body arrives, its exact registered hash determines the question. If earlier same-ID material describes a different question, preserve those bytes, restore the selected primary revision to the registered anchor, and report `objective_conflicts` and `objective_anchor_resolved` in the ingestion classification. The derived identity and map retain `conflicting_revisions` so later readers can investigate the identity collision. This is an identity conflict, not a refutation of either claim; a success response must not hide the changed selection.

`bind-objective` explicitly completes an incomplete description using a complete objective record with the same ID and unchanged known fields. The snapshot's optional `objective_binding` identifies this explicit completed revision while preserving the original anchor. Completion does not inherit an earlier revision's review. Once complete, the six fields remain fixed. Import alone never authorizes this binding. Selecting a different primary identity is blocked with a specific reason. Identified identity conflicts may be retained from a package with the same primary anchor when its reported selection does not select the conflicting question; incoming reviews still remain foreign. Missing details are never manufactured by intake or adoption.

## Review: `crs-review/v1`

Required exact fields: `schema`, `id`, `target`, `decision`, `coverage`, `method`, `scope`, `findings`, `limitations`, `materials`, `report`, `reviewer`, `created_at`, `supersedes`.

- `id`, `method`, `scope`, `findings`, and `created_at` are nonempty text. Record a real timestamp in `created_at`; it never determines the winner of conflicting reviews.
- `target` binds one exact record revision. `decision` is `accept`, `reject`, or `inconclusive`.
- `coverage` is a unique list drawn from `record_fidelity`, `argument` (a proof or correctness argument was checked), `reproduction` (the claimed result was reproduced within its declared scope), `computation` (code, a computation or an experiment run was checked), `source`, and `terminology`. Declare what was actually checked.
- `materials` lists unique exact hashes and must cover all target evidence and dependency revisions. For corrections it also covers target and replacement, even if not duplicated in dependencies.
- `report` is the stored SHA-256 of the actual substantive review report. A target record is not its own independent review report. Store reopens it before admission.
- `reviewer` is exactly `{identity, independence, basis}`. Independence is `independent`, `self`, or `unknown`. These are claims about the method's provenance, not authenticated identities. An independent argument/reproduction/source acceptance needs a nonempty specific basis.
- `limitations` is an array of strings. `supersedes` is a unique list of prior review hashes explicitly replaced by this review. Prior reviews must be present in the locally trusted set and have the same target. Self-reference, missing predecessors, cross-target replacement, and cycles are rejected. Empty `[]` means no replacement.

Applicable acceptance always needs `record_fidelity`. Established/refuted results also need `argument`, `reproduction` or `source` and declared independence with a concrete basis. Observations need `computation`, `reproduction` or `source`. Definitions also need `terminology`. Other record kinds require fidelity; the reviewer remains responsible for whether the declared method is sufficient for the content.

State the strongest evidence grade actually reached in the review `method` or `findings`, using the suite grades strongest first: `formal` (proof assistant or model checker), `certificate` (a checkable certificate with its verifier and inputs), `exact_reproduction` (fixed data, environment and seeds reproduce the result exactly or within a declared tolerance), `bounded_empirical` (holds within the declared datasets, scales, seeds or hyperparameter ranges) and `numerical_evidence` (suggestive numbers only). `numerical_evidence` alone supports only an observation. Running code, passing tests or matching hashes verify only the boundary they checked.

Store's explicit review operation reopens provided material hashes. For an unavailable evidence asset, `source` coverage plus an exact summary/locator can record citation-based assurance. The caller must actually check that reference and state the assurance limit in the report; software does not retrieve the evidence or verify the claim automatically. Supplying a report or asserting PASS is insufficient by itself.

## Assessment and complete maps

`assess(records, reviews, selected, excluded_records=None)` takes only explicitly locally admitted reviews and returns a status per revision:

`{review, epistemic, effective_epistemic, effect, exportable, usable, reasons, review_ids}`.

`review` is `accepted`, `rejected`, `inconclusive`, or `unreviewed`. `epistemic` preserves the record's claim label; `effective_epistemic` can additionally be `conditional`. `effect` is `current`, `historical`, `invalid`, or `needs_review`.

`usable` and `exportable` are distinct. Previously accepted records can be exported as accurately labelled history after withdrawal or review conflict while being unusable as premises. An unreviewed record has no such permission. Broken identity/dependency closure and circular justification block export. Undeclared reliance on hypotheses also blocks export. Read reasons instead of treating a single status as universal PASS.

Unresolved current reject/inconclusive decisions block current use; only explicit valid review replacement resolves them. Current review decisions do not erase applicable historical acceptance. `review_ids` retains applicable historical and current review identifiers. A research failure propagates along strong dependencies as `needs_review`, not as a claim that every descendant is false. Presentation corrections alone do not trigger that propagation.

The local map covers every stored record revision, evidence description, scope, action, feedback, conditions, sources, dependency, correction, and review-report link. It is a derived view of one snapshot, not another authority store. The named enclosing project folder holds this map above the nested formal library. Local Obsidian reading views are derived presentation bytes outside that library, carry the original SHA-256, and never inherit a new review. Generic exchange-map rendering and review rules remain unchanged. Query returns bounded matching records and their statuses; the answering agent must distinguish retrieved evidence from inference and unknowns.

An optional `excluded_records` list contains only `{sha256, id, reason:"not_exportable"}` for explicitly omitted record bodies. It permits a reviewed correction to identify an unreviewed replacement without exporting that replacement. It is a locator, not a substitute research record or review. A strong premise/input/term cannot become usable through such a locator. Maps and packages must identify the missing body and keep its status distinct from included reviewed evidence.

## Storage, recovery, and exchange

The formal project contains content-addressed `objects`, an atomic `HEAD`, immutable snapshots, operation records, and optional cold-object locations. Snapshot fields include project identity/title/objective, record hashes, selected revisions, locally trusted and foreign review hashes, reported origins, parent, operation ID, request hash, and optional excluded-record locators. These are operational storage objects; direct edits are unsupported.

One operation ID binds one request. Retrying the same input reuses its result; different input is rejected. Cooperating writers hold an OS lock. Recovery publishes the prepared candidate only when HEAD is the expected prior snapshot, recognizes an already published candidate, and refuses to overwrite a third HEAD. This is a process-interruption boundary, not a guarantee against arbitrary disk failure or malicious same-user code.

The formal project and its currently registered cold assets must not contain duplicate exact bytes. `audit` checks that scope, not every file on an external disk. `cold-move` verifies bytes before changing a registered location and removes only the exact former hot object owned by this project. A prior external cold source may be shared by another project: preserve it and report it in `retained_external_sources`; a local locator alone never authorizes its deletion. Cleaning such external copies requires separate evidence that no other project needs them. This does not authorize deleting similar code or the only evidence copy. Keep generated maps, outgoing ZIPs, and reproduction copies outside the formal project.

`crs-exchange/v1` contains a reviewed projection, source snapshot locator, record/review hashes, selected pointers, asset inclusion/reference inventory, exact assessment states, and reported origins. The ZIP includes README, research map, object files, standard-library tools including external-source inventory/adoption, replay and workflow observation modules, manifest, and namespace hashes. Required review reports are always present. Evidence may be referenced through an exact reviewed summary and portable locator. The asset budget is not a total compressed-ZIP size promise.

Exchange verification checks package structure, namespace, bytes, review bindings, and reproduced assessment. It does not run research code, authenticate reviewer/source identities, or redo proofs, arguments or experiments. Export preserves source correction states and requires the reviewed projection to close; do not work around rejection by relabelling an unreviewed record as an attachment. Import saves incoming reviews as foreign evidence. An offline package cannot learn later withdrawals automatically.

An external `crs-workflow/v1` session records one task start, phases, command boundaries, finish and a single 20-minute attention event. It measures wall time across commands and human/agent work; it is not part of the formal project, research evidence or authority. The foreground observer ends at the attention event or workflow end and never stops research.

<a id="external-sources"></a>
## Incomplete descriptions and external sources

An objective description received without `project_objective` retains its known fields and `incomplete` state. Moving evidence into the project does not silently populate the six-field identity or change the primary question. An explicit `bind-objective` records a compatible completion while preserving the original anchor and review history.

Foreign archives and external research directories are input, not authority. Their PASS labels, status fields or review claims do not create local review authority, and relabelling alone is not intake. Preserve useful exact proof/code/configuration/data/certificate/failure sources and explicit provenance; do not require every execution receipt or duplicate ZIP.

The `inventory` command scans a bounded ordinary directory or ZIP without executing its code. Its `crs-source-inventory/v1` records paths, exact hashes, sizes, skipped generated/control directories, totals, the frozen `source_limits` (`max_entries`, `max_expanded_bytes`, `max_metadata_bytes`) and the measured `source_usage` (`visited_entries`, `expanded_bytes`, `metadata_bytes`). Every visited directory, cache entry and ordinary file consumes the entry limit before any skip. ZIP central-directory metadata is checked with bounded reads before the archive member objects are allocated; directory traversal is streamed. The public inventory command exposes `--max-files`, `--max-bytes` and `--max-metadata-bytes`; increasing a limit is an explicit intake choice.

The `crs-adoption/v1` mapping has exactly `schema`, `inventory_sha256`, `origin`, `records`, and `dispositions`; each file requires one `{path,action,reason}` with action `retain` or `omit`. Records must describe actual research and each retained byte identity must have an evidence role. Omit decisions do not delete the original source.

`adopt` reuses the frozen limits, checks the measured usage, namespace and exact bytes again, ingests the mapped records/evidence, preserves sources, and records a `crs-adoption-receipt/v1`. It neither runs the source's code nor grants research review. Before claiming a particular adoption complete, inspect its coverage, unresolved records, preservation of evidence and failures, deduplication, review mapping, and package results. Existing field validation does not close a new intake automatically.

## CLI completion envelope

The JSON result retains `ok`, `status`, `data` or error details, and elapsed time. `terminal_state` reports operational `success`, successful `recover` as `recovered`, or a nonzero operational outcome as `blocked`. It never certifies a research result. Observation notes do not replace the completed research operation outcome.

## Batched admission and local finish

The optional `crs-review-batch/v1` submission wraps `items:[{review,report}]`; each review retains the existing `crs-review/v1` schema. Report paths are relative to the submission directory; null reuses its exact stored report. Preflight freezes normalized review content and actual report bytes, reports per-item failures without writes, and returns `snapshot` and `batch_sha256`. Commit requires the matching `--expected-batch`; use `--expected-head` to bind that preflight snapshot. A changed batch or stale HEAD is refused. All items are checked again against one locked snapshot before one recoverable transaction admits them. Supersession can reference only previously locally admitted reviews for the same target, not another item in this batch. Preflight failure admits nothing; disk interruption retains the existing operation recovery boundary. Batching never supplies substantive review or research authority.

`finish` reads a stable HEAD, checks the local audit, derives the complete local map, and reuses the export operation's verification of its exact ZIP. A changed HEAD or occupied destination blocks publication. The new external output folder contains `map.md`, `reviewed-handoff.zip`, `receipt.json` (`crs-finish/v1`) and `README.md`. The local map can include unreviewed history and machine-local links; share only the ZIP. Missing required evidence without an explicit summary and locator blocks finish. Permitted references remain unavailable evidence, not new evidence. Finish neither changes reviews nor replays evidence.

Successful publication leaves no owned scratch path; ordinary failure cleans only that invocation's scratch. An uncatchable process termination can leave scratch for explicit owner inspection. Finish does not delete old reproduction workspaces or original evidence. Publication requires non-replacing directory rename (Windows, Linux renameat2, macOS renamex_np); otherwise use the existing separate map/export commands. Cooperative HEAD checks do not protect against arbitrary external same-user edits that bypass the project protocol.

<a id="continuation-check"></a>
## Continuation check (external operational evidence)

`crs-continuity-plan/v1`, `crs-continuity-decisions/v1` and
`crs-continuity-receipt/v1` are external workflow artifacts, not formal research
records or reviews. See [continuation](continuity.md). Plans bind a project
snapshot, optional ancestor intake baseline, the exact new-record set and its
digest, a declared all/affected scope, record revisions and bounded candidate
pairs. Recomputing screening checks exact coverage; it never verifies semantics.
Ordinary intake passes `--scope affected` explicitly, including the first
intake; the CLI default is `all`. Use `all` for an explicitly scoped historical
audit.
Dispositions account for every scoped record and suggested pair and may add
other pairs touching that scope. Confirmed edges must already exist in the
successor or an explicit same-identity revision. Receipts bind plan, decisions
and checked snapshot, enumerate pending/disputed items and new revisions, and
never affect `usable`, `exportable`, foreign review trust or research status.
Pending means unchecked or unresolved, not independent. Completion is only for
the declared scope, not proof that all implicit relations were discovered.

Import and ingest return an additive pending continuation notice. Explicit
same-identity revision chains are processed in ancestry order, independent of
input order. Sibling branches do not select a winner by arrival order; retain
the preceding selection until explicit selection. Original source selection
still applies to newly received identities under the existing source-selection
contract. Missing relationships are supplemented through explicit record
revisions, never byte replacement.


<a id="content-scope"></a>
## Content scope

Record/review/snapshot canonical schemas and every research identity are independent of content scope. The `crs-content-scope/v1` binding names the enclosing project relative to the formal store. Supply `--project-root` when discovery is insufficient; do not narrow a known scope. A complete `crs-project-navigation/v1` manifest uses checked absolute external Markdown/browser/report paths and a declared delivery.external_root; internal copies are never automatically moved or blessed. An authorized consolidation retains hashes and all origin/container/path/history mappings, externalizes required raw containers and full backups, and validates current project plus independent delivery bytes before success.

`crs-exchange-parts/v1` is transport for one logical `crs-exchange/v1`, preserving whole member bytes. It declares each volume and member identity, actual size ceiling, ranking and completeness instructions. Reconstruction occurs outside the source project and delivered set. Local consumers validate reconstructed canonical data, do not execute included tools, and never automatically trust foreign reviews. A single ZIP input is accepted when it passes the complete recursive coverage rules. Very large indivisible required evidence needs an explicit reviewed reference or separately validated chunk protocol, not an implicit lossy conversion.

<a id="portable-history"></a>
## Explicit portable history exchange

The optional external `crs-portable-history/v1` plan has exactly `schema`, `snapshot`, `replacements`. Each nonempty replacement row is `{original, replacement}` with exact source record hashes. The source snapshot must match current HEAD. Plans are navigation and review-selection inputs, never research authority. Do not publish them as research records or automatically admit reviews.

Every original remains unchanged in the source store. Its replacement must be a retained direct `previous` revision of the same identity with its own applicable accepted review, usable and exportable. One-to-one replacements only; chains and cycles are rejected. The founding objective and its explicit binding cannot be omitted by this narrow route. An active applicable fidelity/source review of each retained derivative must include its original hash in materials and describe the old-to-new comparison in its included report. The matching previous hash is a citation-only review material after delivery, not an available original body. Protected research and historical fields must be byte-equivalent as JSON values. Evidence declarations retain hash, bytes, name, role and summary; only locators may change. Dependencies, conditional hashes and correction pointers may change only through the complete explicit mapping. Changes to action/source representation require substantive fidelity review; the structural comparison does not establish semantic equivalence.

A `crs-exchange/v1` manifest may contain the excluded locator variant `{sha256,id,reason:"portable_replacement",replacement}`. It explicitly identifies an original body not delivered and its included reviewed derivative. The map discloses that distinction. `not_exportable` locators keep their own meaning.

The exporter validates the source/derivative relationship against actual source records. The receiver cannot byte-check an omitted original body; it verifies the retained derivative, reviews, identifiers and declaration structure. It must not infer identical original bytes, independent review or new research evidence. Incoming reviews remain foreign after import. Receiving the exact original later removes that missing-body locator through normal intake; it does not replace history.

Retained strong premises/inputs/terms and correction targets still need their actual reviewed bodies. A supplied plan that would lose such retained records fails instead of silently narrowing closure. Omitted historical bodies cannot be evidence, reports or nonstructural review materials; background dependency references remain navigation only. Exported assessment must match the source's assessment of each retained exact revision. Re-export keeps explicit replacement references and requires applicable local review, as for every imported package.

The exporter first computes the ordinary source projection with its existing closure exclusions. Both mapping endpoints must occur in that baseline. Only then are mapped originals removed; every newly missing retained strong premise or correction target fails the operation. Existing exclusion reasons and source selection are preserved, with no automatic branch selection.

<a id="planned-transport"></a>
## Planned transport

The export plan does not rewrite `crs-record/v1` records, reviews, source bytes or project identity. A handoff exported from an explicit plan or material contract binds `delivery_plan` (`delivery-plan.json` and its SHA-256) in its `crs-exchange/v1` manifest; an ordinary handoff omits that field. The reader verifies both shapes without executing their contents. See [planned export](planned-export.md) for closed material declarations and the separate completeness/replay claims.


Recipient receipts `crs-restored-delivery/v1`, `crs-recipient-replay-start/v1`, and `crs-recipient-replay/v1` are external disposable operation evidence under the existing temporary_replay semantics. They do not enter the research store or confer review authority. A package without a bound delivery plan continues through the ordinary verifier/importer; it cannot be labelled as having material groups that were never declared. Restoration binds the final plan; replay additionally binds the restoration bytes, runtime, process outcome and exact result checks. Interrupted execution remains unresolved until independently diagnosed.


### Required material members

The planner and recipient verifier enforce nonoptional material-group members in reviewed exchange as well as offline reproduction. A plan prepared with a different exporter identity requires explicit refresh before a new export. A package missing a declared nonoptional member fails completeness acceptance without changing its source records or reviews. No new authority or stored-research change is introduced.
