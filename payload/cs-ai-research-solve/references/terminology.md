# CRS terminology

These definitions govern the research-data contract. Read them before assigning identities or changing evidence/review labels. Global IDs use `teacher:cs-ai-research-solve#canonical_id`; surface wording is not a license to merge meanings.

A canonical definition change requires explicit semantic review, a registry history entry, and an explicit update of the affected record and data contract. Existing research bytes are preserved.

<a id="research_identity"></a>
## `research_identity`

**显示名称：** 研究身份

### 简要定义

One stable opaque ID identifies one research item across revisions and projects.
### 规范定义

Preserve the same identity for the same imported item; create a distinct identity for another result or byproduct.
### 构成字段

`id`, `previous`.
### 权威等级

Research-data contract; only explicit applicable evidence review can support research use.
### 生命周期规则

Capture exact material, preserve revisions, assess applicable evidence, and retain any later correction/history.
### 允许的变化

New explicit revisions, provenance, reviews, references, and derived views under their field contracts.
### 禁止的变化

Silent identity/meaning replacement, fabricated assurance, overwriting historical bytes, or automatic trust transfer.
### 不得混淆

Same title, author name, or arrival order does not establish identity.
### 完成关系

Creating or validating this object alone does not complete the research objective; inspect usable/exportable status and the actual research evidence.
### 机器绑定

`record.id; Store.ingest/select`.

<a id="project_objective"></a>
## `project_objective`

**显示名称：** 项目研究目标

### 简要定义

The stable research question defining this project's primary objective.

### 规范定义

Keep one immutable research identity formed by six explicit fields. Store the first primary objective revision as the project anchor. New directions have distinct research IDs and explicit links; pursuing them does not replace the primary objective. A semantic change requires a new project or explicit fork. Metadata may grow without changing the six fields.

### 构成字段

`statement`, `domain`, `claim_scope`, `assumptions`, `evidence_standard`, `completion_standard`. `assumptions` is a list of strings; the other five fields are nonempty strings. `claim_scope` names the population the claim quantifies over (datasets, tasks, models, scales, seeds or inputs), its aggregation (for example mean±sd over N seeds, worst case, all inputs of size n, or with high probability) and its metric. A complete objective record stores them in `project_objective`; its `statement`, `scope`, and `assumptions` agree with the identity's statement, domain, and assumptions.

### 权威等级

`immutable_research_identity`. Identity fixes the question and its declared evidence/completion standards; it does not certify that the question has been solved.

### 生命周期规则

Create a complete objective or retain an incomplete imported description with its missing fields visible. Freeze its known statement, scope, and assumptions; never invent an absent claim scope or standard. Completing that description requires an explicit binding whose known fields agree. Preserve the original anchor and all revisions; the completed six-field identity then remains fixed.

### 允许的变化

Add action, feedback, evidence, provenance, limitations, reviews and related questions. Metadata revisions preserve the bound identity. Explicit completion adds previously unspecified fields without rewriting earlier research bytes or inheriting review.

### 禁止的变化

Changing the bound identity fields, silently selecting another primary question, or letting a received package's preferred revision replace the local objective. A distinct useful result or question is preserved under its own identity.

### 不得混淆

It is not Product Goal or project.json: a host task goal and a storage file are different from this research identity. The fixed primary question does not impose a fixed agent count, execution window, research method, or obligation to discard unrelated byproducts.

### 完成关系

Use the declared evidence and completion standards to assess an actual result. Creating an objective, binding missing fields, or passing software validation does not solve it.

### 机器绑定

`crs_model.OBJECTIVE_FIELDS`, `objective_identity`, `objective_complete` (an objective `crs-record/v1` carrying `project_objective`), and `Store.ingest/select/bind_objective`. `snapshot.objective` preserves the initial anchor; an explicit `objective_binding` preserves a completed identity. `init --objective-file` and `bind-objective` use the same live six-field validator.

<a id="research_record"></a>
## `research_record`

**显示名称：** 研究记录

### 简要定义

A closed structured account of a research item and its evidence relationships.
### 规范定义

Preserve actions, feedback, evidence, limits, and sources sufficient to reopen the finding, including useful byproducts.
### 构成字段

`id`, `kind`, `statement`, `scope`, `action`, `feedback`, `evidence`, `dependencies`.
### 权威等级

Research-data contract; only explicit applicable evidence review can support research use.
### 生命周期规则

Capture exact material, preserve revisions, assess applicable evidence, and retain any later correction/history.
### 允许的变化

New explicit revisions, provenance, reviews, references, and derived views under their field contracts.
### 禁止的变化

Silent identity/meaning replacement, fabricated assurance, overwriting historical bytes, or automatic trust transfer.
### 不得混淆

A record is not a chat transcript, execution receipt, or automatic research verdict.
### 完成关系

Creating or validating this object alone does not complete the research objective; inspect usable/exportable status and the actual research evidence.
### 机器绑定

`crs-record/v1; validate_record`.

<a id="record_revision"></a>
## `record_revision`

**显示名称：** 记录修订

### 简要定义

The SHA-256 of the canonical bytes of one complete research record.
### 规范定义

Changed record content receives a new revision; an explicit previous link preserves the item's history.
### 构成字段

`id`, `previous`.
### 权威等级

Research-data contract; only explicit applicable evidence review can support research use.
### 生命周期规则

Capture exact material, preserve revisions, assess applicable evidence, and retain any later correction/history.
### 允许的变化

New explicit revisions, provenance, reviews, references, and derived views under their field contracts.
### 禁止的变化

Silent identity/meaning replacement, fabricated assurance, overwriting historical bytes, or automatic trust transfer.
### 不得混淆

A software release version and a record revision are independent identities.
### 完成关系

Creating or validating this object alone does not complete the research objective; inspect usable/exportable status and the actual research evidence.
### 机器绑定

`canonical; digest; record.previous`.

<a id="research_asset"></a>
## `research_asset`

**显示名称：** 研究资产

### 简要定义

Exact preserved proof or argument, code, configuration, data, certificate, evaluation output, source, or report bytes, or an explicitly referenced exact asset.
### 规范定义

Equal hashes identify equal content; logical names and provenance may vary without copying bytes.
### 构成字段

`sha256`, `bytes`, `name`, `role`, `summary`, `locator`.
### 权威等级

Research-data contract; only explicit applicable evidence review can support research use.
### 生命周期规则

Capture exact material, preserve revisions, assess applicable evidence, and retain any later correction/history.
### 允许的变化

New explicit revisions, provenance, reviews, references, and derived views under their field contracts.
### 禁止的变化

Silent identity/meaning replacement, fabricated assurance, overwriting historical bytes, or automatic trust transfer.
### 不得混淆

Byte identity does not establish research validity or permit deleting merely similar code.
### 完成关系

Creating or validating this object alone does not complete the research objective; inspect usable/exportable status and the actual research evidence.
### 机器绑定

`record.evidence; Store.resolve/put_file`.

<a id="review_record"></a>
## `review_record`

**显示名称：** 审核记录

### 简要定义

An explicit review of specified material by a declared method and scope.
### 规范定义

Bind the exact target, materials, actual report, reviewer basis, and any deliberately replaced prior review.
### 构成字段

`target`, `decision`, `coverage`, `method`, `scope`, `findings`, `materials`, `report`, `reviewer`, `supersedes`.
### 权威等级

Research-data contract; only explicit applicable evidence review can support research use.
### 生命周期规则

Capture exact material, preserve revisions, assess applicable evidence, and retain any later correction/history.
### 允许的变化

New explicit revisions, provenance, reviews, references, and derived views under their field contracts.
### 禁止的变化

Silent identity/meaning replacement, fabricated assurance, overwriting historical bytes, or automatic trust transfer.
### 不得混淆

Foreign review, a PASS string, a different name, and self-review do not create independent local acceptance.
### 完成关系

Creating or validating this object alone does not complete the research objective; inspect usable/exportable status and the actual research evidence.
### 机器绑定

`crs-review/v1; Store.review; assess`.

<a id="evidence_status"></a>
## `evidence_status`

**显示名称：** 证据状态

### 简要定义

The separate review, claim-kind, dependency-effect, current-use, and history-delivery assessment.
### 规范定义

Read all relevant axes and reasons; preserve hypotheses as hypotheses and invalidated history as history.
### 构成字段

`review`, `epistemic`, `effective_epistemic`, `effect`, `exportable`, `usable`, `reasons`, `review_ids`.
### 权威等级

Research-data contract; only explicit applicable evidence review can support research use.
### 生命周期规则

Capture exact material, preserve revisions, assess applicable evidence, and retain any later correction/history.
### 允许的变化

New explicit revisions, provenance, reviews, references, and derived views under their field contracts.
### 禁止的变化

Silent identity/meaning replacement, fabricated assurance, overwriting historical bytes, or automatic trust transfer.
### 不得混淆

Exportable does not mean usable; a rejected proof or experiment does not mean a refuted claim.
### 完成关系

Creating or validating this object alone does not complete the research objective; inspect usable/exportable status and the actual research evidence.
### 机器绑定

`assess return values`.

<a id="failure_memory"></a>
## `failure_memory`

**显示名称：** 失败记忆

### 简要定义

Preserved failed approaches or negative outcomes with their applicability and reopening conditions.
### 规范定义

Keep informative failure evidence so another researcher can avoid repeating it or retry when its conditions change.
### 构成字段

`action`, `feedback`, `scope`, `limitations`, `reopen`, `evidence`.
### 权威等级

Research-data contract; only explicit applicable evidence review can support research use.
### 生命周期规则

Capture exact material, preserve revisions, assess applicable evidence, and retain any later correction/history.
### 允许的变化

New explicit revisions, provenance, reviews, references, and derived views under their field contracts.
### 禁止的变化

Silent identity/meaning replacement, fabricated assurance, overwriting historical bytes, or automatic trust transfer.
### 不得混淆

Failure within one tested scope is not impossibility everywhere.
### 完成关系

Creating or validating this object alone does not complete the research objective; inspect usable/exportable status and the actual research evidence.
### 机器绑定

`kind=failure; record.reopen; map/query`.

<a id="correction_record"></a>
## `correction_record`

**显示名称：** 更正记录

### 简要定义

A separately reviewed presentation, invalidating, or narrowing change to the interpretation of an earlier revision.
### 规范定义

Preserve the old revision; propagate justified effects through strong dependencies and review replacements separately.
### 构成字段

`target`, `effect`, `reason`, `replacement`.
### 权威等级

Research-data contract; only explicit applicable evidence review can support research use.
### 生命周期规则

Capture exact material, preserve revisions, assess applicable evidence, and retain any later correction/history.
### 允许的变化

New explicit revisions, provenance, reviews, references, and derived views under their field contracts.
### 禁止的变化

Silent identity/meaning replacement, fabricated assurance, overwriting historical bytes, or automatic trust transfer.
### 不得混淆

Presentation fixes do not automatically invalidate dependent research; suspected descendants are not automatically false.
### 完成关系

Creating or validating this object alone does not complete the research objective; inspect usable/exportable status and the actual research evidence.
### 机器绑定

`record.correction; assess influence graph`.

<a id="research_map"></a>
## `research_map`

**显示名称：** 研究地图

### 简要定义

A complete readable view of the project snapshot with exact object navigation and evidence states.
### 规范定义

Show all stored revisions locally; an exchange map shows its closed reviewed projection with correction context.
### 构成字段

`records`, `reviews`, `selected`, `origins`.
### 权威等级

Research-data contract; only explicit applicable evidence review can support research use.
### 生命周期规则

Capture exact material, preserve revisions, assess applicable evidence, and retain any later correction/history.
### 允许的变化

New explicit revisions, provenance, reviews, references, and derived views under their field contracts.
### 禁止的变化

Silent identity/meaning replacement, fabricated assurance, overwriting historical bytes, or automatic trust transfer.
### 不得混淆

A derived map is not a second authority store or another verification of research claims.
### 完成关系

Creating or validating this object alone does not complete the research objective; inspect usable/exportable status and the actual research evidence.
### 机器绑定

`crs_exchange.render_map; crs.py map/query`.

<a id="project_snapshot"></a>
## `project_snapshot`

**显示名称：** 项目快照

### 简要定义

One immutable project view selected by HEAD.
### 规范定义

Read map, queries, and assessment against a fixed snapshot; publish changes through one recoverable operation.
### 构成字段

`project_id`, `objective`, `record_hashes`, `selected`, `reviews`, `foreign_reviews`, `origins`, `parent`.
### 权威等级

Research-data contract; only explicit applicable evidence review can support research use.
### 生命周期规则

Capture exact material, preserve revisions, assess applicable evidence, and retain any later correction/history.
### 允许的变化

New explicit revisions, provenance, reviews, references, and derived views under their field contracts.
### 禁止的变化

Silent identity/meaning replacement, fabricated assurance, overwriting historical bytes, or automatic trust transfer.
### 不得混淆

Selected pointers do not erase historical revisions or authenticate reported sources.
### 完成关系

Creating or validating this object alone does not complete the research objective; inspect usable/exportable status and the actual research evidence.
### 机器绑定

`crs-snapshot/v1; Store.transact/recover`.

<a id="formal_library"></a>
## `formal_library`

**显示名称：** 正式研究库

### 简要定义

The sole formal research authority and its registered cold evidence, nested within the complete research project.
### 规范定义

Store equal content once. Uniqueness covers the entire enclosing project, not only this authority directory, including all roles and recursively decoded archive members. Each external delivery set is an independent complete scope. Preserve all provenance as references; full temporary copies, delivery and original forensic containers stay outside the enclosing project. Incomplete decoding blocks conformity; neither hardlinks nor changed padding are exceptions.
### 构成字段

`objects`, `HEAD`, `locations`.
### 权威等级

Research-data contract; only explicit applicable evidence review can support research use.
### 生命周期规则

Capture exact material, preserve revisions, assess applicable evidence, and retain any later correction/history.
### 允许的变化

New explicit revisions, provenance, reviews, references, and derived views under their field contracts.
### 禁止的变化

Silent identity/meaning replacement, fabricated assurance, overwriting historical bytes, or automatic trust transfer.
### 不得混淆

Similarity is not equality; cleanup must not delete the sole evidence copy.
### 完成关系

Creating or validating this object alone does not complete the research objective; inspect usable/exportable status and the actual research evidence.
### 机器绑定

`Store.audit/cold_move; objects; locations.json`.

<a id="source_reference"></a>
## `source_reference`

**显示名称：** 摘要来源引用

### 简要定义

An exact evidence reference with a reviewed summary and locator when full evidence is not included or replayed.
### 规范定义

State the conclusion, conditions, assurance scope, expected hash/size, and how to retrieve the evidence.
### 构成字段

`sha256`, `bytes`, `summary`, `locator`.
### 权威等级

Research-data contract; only explicit applicable evidence review can support research use.
### 生命周期规则

Capture exact material, preserve revisions, assess applicable evidence, and retain any later correction/history.
### 允许的变化

New explicit revisions, provenance, reviews, references, and derived views under their field contracts.
### 禁止的变化

Silent identity/meaning replacement, fabricated assurance, overwriting historical bytes, or automatic trust transfer.
### 不得混淆

Absence of bytes does not imply absence of review; reference checking is not claimed full replay.
### 完成关系

Creating or validating this object alone does not complete the research objective; inspect usable/exportable status and the actual research evidence.
### 机器绑定

`record.evidence; source coverage; exchange availability=referenced`.

<a id="exchange_bundle"></a>
## `exchange_bundle`

**显示名称：** 已审研究交接包

### 简要定义

A closed reviewed projection with navigation, tools, exact identities, and evidence-availability declarations.
### 规范定义

Preserve source states and applicable correction/review history; incoming review remains foreign until explicit local admission.
### 构成字段

`source_snapshot`, `record_hashes`, `reviews`, `assets`, `statuses`, `origins`, `assurance`.
### 权威等级

Research-data contract; only explicit applicable evidence review can support research use.
### 生命周期规则

Capture exact material, preserve revisions, assess applicable evidence, and retain any later correction/history.
### 允许的变化

New explicit revisions, provenance, reviews, references, and derived views under their field contracts.
### 禁止的变化

Silent identity/meaning replacement, fabricated assurance, overwriting historical bytes, or automatic trust transfer.
### 不得混淆

Package integrity is not evidence for a research claim, authenticated authorship, or automatic receipt of later withdrawals.
### 完成关系

Creating or validating this object alone does not complete the research objective; inspect usable/exportable status and the actual research evidence.
### 机器绑定

`crs-exchange/v1; export_bundle/verify_bundle/import_bundle`.

<a id="temporary_replay"></a>
## `temporary_replay`

**显示名称：** 临时重开复现

### 简要定义

Reopening exact evidence into an owned external workspace for inspected local computation.
### 规范定义

Declare inputs, command, timeout, and outputs to preserve; store required outputs before cleanup and report actual outcomes.
### 构成字段

`files`, `argv`, `capture`, `purpose`.
### 权威等级

Research-data contract; only explicit applicable evidence review can support research use.
### 生命周期规则

Capture exact material, preserve revisions, assess applicable evidence, and retain any later correction/history.
### 允许的变化

New explicit revisions, provenance, reviews, references, and derived views under their field contracts.
### 禁止的变化

Silent identity/meaning replacement, fabricated assurance, overwriting historical bytes, or automatic trust transfer.
### 不得混淆

Process success does not establish a research claim; the runner is not an OS sandbox or a global evidence collector.
### 完成关系

Creating or validating this object alone does not complete the research objective; inspect usable/exportable status and the actual research evidence.
### 机器绑定

`crs.py replay; replay_workspace`.

<a id="continuation_check"></a>
## `continuation_check`

**显示名称：** 研究接续检查

### 简要定义
An external, snapshot-bound account of examined research relationships and unresolved links.
### 规范定义
Account for scoped records and candidate pairs, inspect exact content, verify that confirmed relations are saved, and preserve uncertainty and source history. Ordinary intake checks explicitly use the affected scope, including on first intake. Whole-project checks require an explicit historical-audit scope; absence of a prior audit does not enlarge the request.
### 构成字段
`snapshot`, `scope`, `record_revisions`, `pairs`, `plan_sha256`, `decisions_sha256`.
### 权威等级
Operational coverage only; no research review or authenticated semantic judgment.
### 生命周期规则
Generate an exact plan, inspect records, preserve dispositions, save justified explicit revisions, check the resulting snapshot, retain pending items and reopen affected work when inputs change.
### 允许的变化
Fresh plans and receipts, documented decisions, explicit formal record revisions through existing APIs, and derived route views.
### 禁止的变化
Rewriting imported originals, trimming a bound plan to hide omissions, declaring empty retrieval independent, or using receipt success to confer research acceptance.
### 不得混淆
A continuation candidate is not a confirmed relationship; a research extension is not evidence of historical authorship or influence. An affected-scope check is not a whole-project audit.
### 完成关系
Completion establishes only accounted-for coverage and stored links within the declared scope. Pending and disputed items remain visible; semantic exhaustiveness is never certified.
### 机器绑定
`crs_continuity.make_plan/check; crs.py continuity-plan/continuity-template/continuity-check/routes`.

<a id="project_navigation"></a>
## `project_navigation`

**显示名称：** 项目导航清单

### 简要定义
One derived navigation model selects the same current entrance and formal-library path for people and AI.

### 规范定义
The manifest locates existing material; it never changes research identity, HEAD, review or evidence authority.

### 构成字段
`formal_library`, `snapshot`, `markdown`, `browser`, `roles`, `retained`, `delivery` (complete delivery only).

### 权威等级
Locator only; formal_library remains the sole research authority.

### 生命周期规则
Inspect existing paths, explicitly adopt the layout, bind current views, generate entrances, publish and validate; refresh after archival changes.

### 允许的变化
Update navigation bindings and classify retained existing paths while preserving source bytes and existing links.

### 禁止的变化
Guessing current data from dates, silently switching formal libraries, or calling a portal a complete browser research map.

### 不得混淆
Project navigation is not project_objective, snapshot authority, a research review, or exchange_bundle.

### 完成关系
A navigation-only manifest proves navigation consistency only. A complete manifest additionally binds `delivery`: current full reading, bound browser and visual evidence, and generated entrances. Neither changes original research assurance. Pending delivery cannot complete a full upgrade.

### 机器绑定
`crs-project-navigation/v1`; scripts/crs_project.py build (navigation only), upgrade (complete delivery), check and resolve. Layout: references/project-layout.md.


## Scope and transport

The `formal_library` remains the sole research data authority; it is not a boundary that excludes work/history from content uniqueness. The `exchange_bundle` may be transported as one ≤512,000,000-byte ZIP or one indexed set of such ZIP volumes. The complete set, with exact canonical records, required reports and provenance, is the bundle; a single volume is not independently complete. Ranking selects optional physical evidence, never research trust. The `project_navigation` binds external complete delivery paths. Contract: [data contract](data-contract.md#content-scope).

## Portable historical representation

An `exchange_bundle` may carry explicit portable replacement locators; its `source_reference` and immutable `record_revision` meanings are unchanged by them. An omitted original hash and an included direct derivative hash are distinct identities, never interchangeable bytes. The source exporter checks the declared relationship; the receiver checks available content and the explicit declaration, not unavailable originals. New record bytes require applicable review. See [portable history](data-contract.md#portable-history) for allowed fields, lifecycle, compatibility, dependencies and forbidden evidence aliases. `crs-portable-history/v1` is an external operation plan, not an authority store or a research verdict.

<a id="export_plan"></a>
## `export_plan`

**显示名称：** 研究导出计划

### 简要定义

A fixed, content-addressed choice of research records, review scope, material actions and delivery limits.

### 规范定义

Create from one source snapshot; collect metadata blockers, prepare selected content, then bind exact plan bytes. Execute only while source and exporter bindings remain applicable. A new selection is a new plan, never an implicit change during packing.

### 构成字段

Optional `previous_plan_sha256` binds the deliberately reused predecessor; its verification results confer no current authority.

`source_snapshot`, `toolset_sha256`, `scope`, `record_hashes`, `review_hashes`, `assets`, `contract`, `asset_budget`, `max_zip_bytes`.

### 权威等级

Delivery declaration only; no additional research or reviewer authority.

### 生命周期规则

Prepare, bind exact bytes, validate applicable source and evidence, execute the authorized consumer and report actual scope. Retain failed or stale outcomes; never promote them by relabeling.

### 允许的变化

New explicit declarations under new hashes; retained old declarations remain historical evidence.

### 禁止的变化

Silent scope reduction, fabricated completeness, source-history rewriting or automatic research trust transfer.

### 不得混淆

A metadata plan is not byte verification; a prepared plan is not completed delivery; complete material declarations are not actual replay.

### 完成关系

Only the declared operation can complete locally; the full user objective needs its separate recipient and research evidence.

### 机器绑定

`crs_export_plan.create_plan/prepare_plan/write_plan/validate_plan; crs.py delivery-plan/export`.

<a id="material_group"></a>
## `material_group`

**显示名称：** 研究运行材料组

### 简要定义

An explicit required-material declaration for a retained research run, algorithm or certificate.

### 规范定义

Bind every declared member to exact evidence of the declared records. Preserve unresolved dynamic requirements. Recompute included members at receipt; separately inspect and run the declared route before claiming actual reproducibility.

### 构成字段

`id`, `records`, `files`, `entrypoint`, `argv`, `expected_outputs`, `environment`, `unresolved_dependencies`.

### 权威等级

Delivery declaration only; no additional research or reviewer authority.

### 生命周期规则

Prepare, bind exact bytes, validate applicable source and evidence, execute the authorized consumer and report actual scope. Retain failed or stale outcomes; never promote them by relabeling.

### 允许的变化

New explicit declarations under new hashes; retained old declarations remain historical evidence.

### 禁止的变化

Silent scope reduction, fabricated completeness, source-history rewriting or automatic research trust transfer.

### 不得混淆

A flat evidence list, a successful process, an environment description and a research review are different from a complete tested material group.

### 完成关系

Only the declared operation can complete locally; the full user objective needs its separate recipient and research evidence.

### 机器绑定

`crs-delivery-contract/v1 groups; validate_contract/material_status/python_dependency_findings`.

## Planned delivery boundary

The export_plan and material_group declarations are transport metadata only. Research identities, source references, reviews and temporary_replay retain their own authority. Contract: references/data-contract.md#planned-transport.


### Recipient operation bindings

`temporary_replay` also covers the explicitly requested external recipient run: immutable restored inputs, a hash-bound invocation, an owned process tree, captured outputs and unresolved interruption state. `crs-restored-delivery/v1` is material restoration evidence; `crs-recipient-replay-start/v1` records an attempted invocation, and `crs-recipient-replay/v1` records actual bounded results. These receipts are neither research records nor reviews. `material_group` completeness is declared coverage plus checked included bytes and retained known dependency findings; actual output reproduction remains a separate claim.


### Explicit export-plan continuation

`export_plan` may carry the optional constitutive field `previous_plan_sha256`. It identifies the exact external predecessor plan whose declared delivery options were deliberately reused. The predecessor's readiness, content verification and review applicability do not transfer: refresh prepares a new plan under current source and tool identities. A predecessor link adds no authority and never embeds prior plans recursively. A plan without this field has no declared predecessor.


### Required material in ordinary exchange

`material_group` nonoptional members are required included bytes under both reviewed exchange and offline reproduction. Proof or argument text can be declared as input with no executable route. This concerns delivery completeness, not research or replay authority. A plan prepared with different exporter bytes must be refreshed before a new delivery. Missing nonoptional reviewed-exchange material blocks acceptance. See planned-export.md#ordinary-export-maintenance.
