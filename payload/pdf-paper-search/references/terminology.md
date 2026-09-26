# pdf-paper-search canonical terminology

These definitions are normative for this Skill. Files and JSON fields are storage carriers, not the semantic objects themselves.

## `pdf_shelf`

### 简要定义

A configured corpus and optional index used for bounded paper and book page search.

### 规范定义

It binds a shelf identity, source PDFs, index/database state, extraction backend, coverage, and freshness without treating missing index state as evidence that a statement is absent.

### 构成字段

`shelf_id`, `pdfs`, `index`, `coverage`, `freshness`, `backend`.

### 权威等级

A Skill-owned canonical concept. Its validated registry entry and source-bound artifacts are authoritative for workflow decisions; summaries and UI labels are derived.

### 生命周期规则

It is created only at its documented workflow boundary, changes through the owning validated transition, and retains enough prior identity and evidence for recovery and audit.

### 允许的变化

Status, evidence pointers, derived views, and implementation carriers may change through the owning workflow while identity, scope, authority, and recorded history remain explicit.

### 禁止的变化

Do not silently rename it, broaden its scope or authority, erase history, lower its evidence/completion rule, or reuse a deprecated label for a different concept.

### 不得混淆

It is not one PDF file. Matching prose or filenames do not make two concepts identical.

### 完成关系

The object reaches its local completed state only when every constitutive field and owning validator succeeds. That local completion does not by itself complete the user's larger project.

### 机器绑定

Global identity `personal:pdf-paper-search#pdf_shelf`, the terminology asset hash, owning source tree hash, and workflow-specific IDs/hashes.

## `page_verification`

### 简要定义

The direct confirmation that a returned PDF page contains the requested statement, algorithm, equation, result, or claim.

### 规范定义

It binds PDF identity, page number, extracted/rendered evidence, query match, and verification status and is required beyond ranking-only hits.

### 构成字段

`pdf_identity`, `page`, `evidence`, `match`, `status`.

### 权威等级

A Skill-owned canonical concept. Its validated registry entry and source-bound artifacts are authoritative for workflow decisions; summaries and UI labels are derived.

### 生命周期规则

It is created only at its documented workflow boundary, changes through the owning validated transition, and retains enough prior identity and evidence for recovery and audit.

### 允许的变化

Status, evidence pointers, derived views, and implementation carriers may change through the owning workflow while identity, scope, authority, and recorded history remain explicit.

### 禁止的变化

Do not silently rename it, broaden its scope or authority, erase history, lower its evidence/completion rule, or reuse a deprecated label for a different concept.

### 不得混淆

It is not database ranking result. Matching prose or filenames do not make two concepts identical.

### 完成关系

The object reaches its local completed state only when every constitutive field and owning validator succeeds. That local completion does not by itself complete the user's larger project.

### 机器绑定

Global identity `personal:pdf-paper-search#page_verification`, the terminology asset hash, owning source tree hash, and workflow-specific IDs/hashes.
