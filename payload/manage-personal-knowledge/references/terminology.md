# manage-personal-knowledge canonical terminology

These definitions are normative for this Skill. Files and JSON fields are storage carriers, not the semantic objects themselves.

## `knowledge_root`

### 简要定义

The movable local root that owns the Vault, question index, PDF library, and resource registry.

### 规范定义

It has a stable identity independent of its current filesystem path and is changed only through guarded setup, relink, repair, or forget plans.

### 构成字段

`root_id`, `current_path`, `components`, `schema_version`, `state_hash`.

### 权威等级

A Skill-owned canonical concept. Its validated registry entry and source-bound artifacts are authoritative for workflow decisions; summaries and UI labels are derived.

### 生命周期规则

It is created only at its documented workflow boundary, changes through the owning validated transition, and retains enough prior identity and evidence for recovery and audit.

### 允许的变化

Status, evidence pointers, derived views, and implementation carriers may change through the owning workflow while identity, scope, authority, and recorded history remain explicit.

### 禁止的变化

Do not silently rename it, broaden its scope or authority, erase history, lower its evidence/completion rule, or reuse a deprecated label for a different concept.

### 不得混淆

It is not Vault path or home directory. Matching prose or filenames do not make two concepts identical.

### 完成关系

The object reaches its local completed state only when every constitutive field and owning validator succeeds. That local completion does not by itself complete the user's larger project.

### 机器绑定

Global identity `personal:manage-personal-knowledge#knowledge_root`, the terminology asset hash, owning source tree hash, and workflow-specific IDs/hashes.

## `kb_resource_id`

### 简要定义

A stable identifier for a long-lived file outside the Obsidian Vault.

### 规范定义

It resolves through the resource registry to one active artifact and preserves move/relink history without embedding unstable absolute paths in notes.

### 构成字段

`resource_id`, `status`, `target`, `history`, `integrity`.

### 权威等级

A Skill-owned canonical concept. Its validated registry entry and source-bound artifacts are authoritative for workflow decisions; summaries and UI labels are derived.

### 生命周期规则

It is created only at its documented workflow boundary, changes through the owning validated transition, and retains enough prior identity and evidence for recovery and audit.

### 允许的变化

Status, evidence pointers, derived views, and implementation carriers may change through the owning workflow while identity, scope, authority, and recorded history remain explicit.

### 禁止的变化

Do not silently rename it, broaden its scope or authority, erase history, lower its evidence/completion rule, or reuse a deprecated label for a different concept.

### 不得混淆

It is not file path or wikilink. Matching prose or filenames do not make two concepts identical.

### 完成关系

The object reaches its local completed state only when every constitutive field and owning validator succeeds. That local completion does not by itself complete the user's larger project.

### 机器绑定

Global identity `personal:manage-personal-knowledge#kb_resource_id`, the terminology asset hash, owning source tree hash, and workflow-specific IDs/hashes.

## `question_collection`

### 简要定义

A registered, locally searchable collection of structured CS/AI problems or research questions whose canonical source remains inside the configured knowledge root.

### 规范定义

It is one logical question source exposed through the federated question interface. A collection may use the Vault-folder adapter or the knowledge-root Markdown adapter, but its source scope, parser contract, verification status, counts, and current source fingerprint remain explicit.

### 构成字段

`collection_id`, `source_scope`, `source_relative`, `source_format`, `parser_version`, `verification_status`, `source_sha256`, `document_count`, `question_count`, `scan_state`.

### 权威等级

The source Markdown is canonical content; the collection record and FTS rows are Skill-owned derived state used for discovery, coverage, and retrieval.

### 生命周期规则

It begins as a discovered candidate or an explicitly named source, becomes registered only through a matching `question_import_plan`, is refreshed by re-import, and remains queryable while its root identity and source scan are valid.

### 允许的变化

Derived counts, hashes, verification status, parser version, and indexed rows may change through a new guarded import of the same source identity.

### 禁止的变化

Do not execute collection-owned code, silently change source scope, import a path outside `knowledge_root`, infer verified status, or rewrite the source during import.

### 不得混淆

It is not a PDF, a filesystem folder, a resource-registry entry, or an arbitrary standalone SQLite index. Those objects may provide provenance or storage but do not themselves constitute the searchable collection.

### 完成关系

One collection is imported when the matching plan is applied atomically and post-import status/search agree. Its completion does not establish whole-corpus `question_coverage`.

### 机器绑定

Global identity `personal:manage-personal-knowledge#question_collection`, stable `collection_id`, configured `knowledge_root_id`, source-relative identity, current source hash, and collection Schema version.

## `question_import_plan`

### 简要定义

The hash-bound dry-run that authorizes one exact structured-question import.

### 规范定义

It is a locally generated preview over a resolved source scope and current source bytes. It records the parser contract, source fingerprint, document/question counts, verification status, intended create-or-replace action, and `plan_sha256` consumed by the write command.

### 构成字段

`schema_version`, `source_scope`, `source_relative`, `source_format`, `parser_version`, `source_sha256`, `document_count`, `question_count`, `verification_status`, `action`, `plan_sha256`.

### 权威等级

It is Skill-generated transaction evidence. The live source reread and matching plan hash are authoritative for import; a copied command, old receipt, or collection-owned index is not.

### 生命周期规则

It is created without writes, remains valid only while every constitutive source field is unchanged, and is consumed idempotently by one matching import attempt or becomes stale.

### 允许的变化

A changed source or import choice may generate a new plan with a new hash after renewed review.

### 禁止的变化

Do not edit the plan, reuse it for another source or root, apply it after source drift, or treat preview success as completed import.

### 不得混淆

It is not an import receipt, verification receipt, collection manifest, or user authorization to modify source files.

### 完成关系

The plan itself never completes an import. Completion requires the matching guarded write plus post-import status and search validation.

### 机器绑定

Global identity `personal:manage-personal-knowledge#question_import_plan`, canonical JSON hash, configured root identity, resolved source identity, and source-byte fingerprints.

## `question_coverage`

### 简要定义

The quantified status of which structured question collections the federated interface currently covers.

### 规范定义

It combines registered-source scan health with bounded discovery of marker-bearing collections under the declared discovery root. Whole-corpus completeness is true only when root identities match, every registered adapter is complete, discovery is complete, and no discovered candidate remains unimported.

### 构成字段

`knowledge_root_id`, `discovery_relative`, `registered_sources`, `registered_collections`, `discovered_unimported`, `scan_errors`, `discovery_complete`, `coverage_complete`.

### 权威等级

It is a derived Skill-owned audit report over current index state and bounded live filesystem discovery. Adapter-local completeness is subordinate to the federation-level report.

### 生命周期规则

It is recomputed by `question-status`, changes when source scans, imports, discovery candidates, roots, or errors change, and is never persisted as an independent canonical record.

### 允许的变化

Counts, candidate lists, errors, and completeness may change as the knowledge root and derived indexes legitimately change.

### 禁止的变化

Do not promote local adapter completeness to whole-corpus completeness, suppress discovered-unimported candidates, or extend the claim to arbitrary unstructured PDFs.

### 不得混淆

It is not PDF-library coverage, Vault index freshness, solution correctness, or evidence that no other problem exists.

### 完成关系

`coverage_complete=true` requires every constitutive predicate to pass simultaneously. Any discovery truncation, root mismatch, scan error, or unimported candidate yields `coverage_gap`.

### 机器绑定

Global identity `personal:manage-personal-knowledge#question_coverage`, configured root identity, discovery scope, adapter Schema versions, and current status result.
