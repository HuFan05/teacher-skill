# cs-ai-computation canonical terminology

These definitions are normative for this Skill. Files and JSON fields are storage carriers, not the semantic objects themselves.

## `computation_job`

### 简要定义

A reproducible computer-science or AI computation or experiment with frozen inputs, environment, seeds, and acceptance checks.

### 规范定义

It binds problem statement, exact/numeric policy, backend choice, environment and hardware, seeds, dataset versions, code, configuration, resource envelope, outputs and metrics, and independent verification.

### 构成字段

`job_id`, `inputs`, `precision_policy`, `backend`, `environment`, `seeds`, `code`, `config`, `resources`, `outputs`, `verification`.

### 权威等级

A Skill-owned canonical concept. Its validated registry entry and source-bound artifacts are authoritative for workflow decisions; summaries and UI labels are derived.

### 生命周期规则

It is created only at its documented workflow boundary, changes through the owning validated transition, and retains enough prior identity and evidence for recovery and audit.

### 允许的变化

Status, evidence pointers, derived views, and implementation carriers may change through the owning workflow while identity, scope, authority, and recorded history remain explicit.

### 禁止的变化

Do not silently rename it, broaden its scope or authority, erase history, lower its evidence/completion rule, or reuse a deprecated label for a different concept.

### 不得混淆

It is not one tool call, and it is not one training step or one seed of a multi-seed experiment. Matching prose or filenames do not make two concepts identical.

### 完成关系

The object reaches its local completed state only when every constitutive field and owning validator succeeds. That local completion does not by itself complete the user's larger project.

### 机器绑定

Global identity `personal:cs-ai-computation#computation_job`, the terminology asset hash, owning source tree hash, and workflow-specific IDs/hashes.

## `backend_snapshot`

### 简要定义

A mutable local inventory of available computation backends and their verified capabilities.

### 规范定义

It records executable identity, versions, installed frameworks, accelerator discovery, host hardware, health, supported operations, refresh time, and authority boundary and is never versioned as immutable Skill knowledge.

### 构成字段

`backend`, `executable`, `version`, `health`, `capabilities`, `refreshed_at`.

### 权威等级

A Skill-owned canonical concept. Its validated registry entry and source-bound artifacts are authoritative for workflow decisions; summaries and UI labels are derived.

### 生命周期规则

It is created only at its documented workflow boundary, changes through the owning validated transition, and retains enough prior identity and evidence for recovery and audit.

### 允许的变化

Status, evidence pointers, derived views, and implementation carriers may change through the owning workflow while identity, scope, authority, and recorded history remain explicit.

### 禁止的变化

Do not silently rename it, broaden its scope or authority, erase history, lower its evidence/completion rule, or reuse a deprecated label for a different concept.

### 不得混淆

It is not computation result, and a recorded accelerator is not a live device check. Matching prose or filenames do not make two concepts identical.

### 完成关系

The object reaches its local completed state only when every constitutive field and owning validator succeeds. That local completion does not by itself complete the user's larger project.

### 机器绑定

Global identity `personal:cs-ai-computation#backend_snapshot`, the terminology asset hash, owning source tree hash, and workflow-specific IDs/hashes.
