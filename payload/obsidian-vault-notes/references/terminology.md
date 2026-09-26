# obsidian-vault-notes canonical terminology

These definitions are normative for this Skill. Files and JSON fields are storage carriers, not the semantic objects themselves.

## `bounded_retrieval`

### 简要定义

A planned, minimal search-and-read scope inside the Obsidian Vault.

### 规范定义

It starts from exact notes or index results, expands only through relevant links, and records why each live file is needed before mutation.

### 构成字段

`query_scope`, `candidate_notes`, `selected_notes`, `expansion_rules`, `privacy_boundary`.

### 权威等级

A Skill-owned canonical concept. Its validated registry entry and source-bound artifacts are authoritative for workflow decisions; summaries and UI labels are derived.

### 生命周期规则

It is created only at its documented workflow boundary, changes through the owning validated transition, and retains enough prior identity and evidence for recovery and audit.

### 允许的变化

Status, evidence pointers, derived views, and implementation carriers may change through the owning workflow while identity, scope, authority, and recorded history remain explicit.

### 禁止的变化

Do not silently rename it, broaden its scope or authority, erase history, lower its evidence/completion rule, or reuse a deprecated label for a different concept.

### 不得混淆

It is not full-Vault loading. Matching prose or filenames do not make two concepts identical.

### 完成关系

The object reaches its local completed state only when every constitutive field and owning validator succeeds. That local completion does not by itself complete the user's larger project.

### 机器绑定

Global identity `personal:obsidian-vault-notes#bounded_retrieval`, the terminology asset hash, owning source tree hash, and workflow-specific IDs/hashes.

## `guarded_edit_receipt`

### 简要定义

The evidence that an Obsidian Markdown edit applied to the expected live bytes and passed narrow validation.

### 规范定义

It binds pre-edit hash, exact mutation, post-edit hash, wikilink/formula checks, narrow diff, and Git status without implying a commit.

### 构成字段

`path`, `pre_hash`, `operation`, `post_hash`, `validators`, `diff_check`.

### 权威等级

A Skill-owned canonical concept. Its validated registry entry and source-bound artifacts are authoritative for workflow decisions; summaries and UI labels are derived.

### 生命周期规则

It is created only at its documented workflow boundary, changes through the owning validated transition, and retains enough prior identity and evidence for recovery and audit.

### 允许的变化

Status, evidence pointers, derived views, and implementation carriers may change through the owning workflow while identity, scope, authority, and recorded history remain explicit.

### 禁止的变化

Do not silently rename it, broaden its scope or authority, erase history, lower its evidence/completion rule, or reuse a deprecated label for a different concept.

### 不得混淆

It is not Git commit or prose confirmation. Matching prose or filenames do not make two concepts identical.

### 完成关系

The object reaches its local completed state only when every constitutive field and owning validator succeeds. That local completion does not by itself complete the user's larger project.

### 机器绑定

Global identity `personal:obsidian-vault-notes#guarded_edit_receipt`, the terminology asset hash, owning source tree hash, and workflow-specific IDs/hashes.

## `large_edit_verification_gate`

### 简要定义

The hash-bound independent review required only before a qualifying large-edit candidate may be written to the live Vault.

### 规范定义

It classifies a proposed edit by explicit project scale and measurable scope, excludes ordinary local edits, binds deterministic candidate checks and an independent subagent verdict to the persistent gate and exact candidate inventory, and permits at most three author-revision rounds. `PASS` makes the candidate eligible immediately; three completed `FAIL` rounds make the final candidate eligible under the explicit `accepted_after_three_reviews` fallback while preserving unresolved findings.

### 构成字段

`classification`, `trigger_evidence`, `gate_id`, `candidate_inventory_sha256`, `deterministic_checks`, `review_round`, `verifier_verdict`, `verdict_history`, `final_state`, `privacy_scope`.

### 权威等级

A Skill-owned canonical workflow gate. Its validated classification and hash-bound receipts control live-write eligibility; prose summaries and reviewer comments are derived evidence.

### 生命周期规则

It begins only after the large-edit classifier returns `large_edit`. One persistent task-local state records the gate identity, round, candidate hashes, verdict history, and terminal state. Each revision produces a new complete candidate inventory and invalidates earlier receipts as authorization for current bytes while retaining them as failure history. `PASS` makes the exact candidate write-eligible; `FAIL` in rounds one or two returns it for revision; `FAIL` in round three changes the gate to `accepted_after_three_reviews` and makes that exact final candidate write-eligible under the documented fallback.

### 允许的变化

Candidate text, inventory hash, deterministic evidence, findings, and round number may change through a documented revision round. After fallback acceptance, later correction requires a distinct user-authorized edit task.

### 禁止的变化

Do not trigger it for a single paragraph, a few formula lines, one local subsection, or a narrow heading/link repair. Do not reuse a stale or cross-gate receipt, let the verifier write the Vault, exceed three automatic rounds, count verifier unavailability as a completed failure, hide unresolved findings, or describe fallback acceptance as verifier approval.

### 不得混淆

It is not ordinary post-write lint, the `guarded_edit_receipt`, a Git review, or permission to read additional note bodies. Mechanical checks establish syntax and structure; the verifier judges meaning preservation, language style, terminology, and whole-scope coherence when authorized.

### 完成关系

The gate is locally complete only when the exact candidate inventory has passing deterministic checks and either a `PASS` verifier receipt or three completed hash- and gate-bound `FAIL` receipts ending in `accepted_after_three_reviews`. Either result makes the exact candidate eligible for the ordinary guarded-write flow; fallback acceptance retains unresolved findings and neither result proves the larger project complete.

### 机器绑定

Global identity `personal:obsidian-vault-notes#large_edit_verification_gate`, persistent gate UUID and state, complete candidate inventory SHA-256, ordered per-file SHA-256 values, review round, closed deterministic/verifier receipt schemas, terminology asset hash, and owning source tree hash.
