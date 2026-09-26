# Configuration And Source Discovery

## State Locations

- Windows config: `%APPDATA%\manage-personal-knowledge\config.json`
- Windows state: `%LOCALAPPDATA%\manage-personal-knowledge`
- Test/diagnostic overrides: `MPK_CONFIG_PATH`, `MPK_STATE_DIR`

Schema v1 stores an absolute `knowledge_root` and source descriptors with `kind` and root-relative paths. It remains readable and is never silently migrated. A confirmed `registry-init` writes schema v2, adding `knowledge_root_name`, a stable `knowledge_root_id`, and registry settings containing the manifest path and user exclusions. Resolve every relative path only after confirming that the final path remains inside the knowledge root.

Schema v2 also writes `.mpk/root.json` inside the root. The absolute root belongs only in the local config and clickable file URIs; the registry, manifest, source identity, and PDF index use root-relative paths plus `knowledge_root_id`.

## Discovery Rules

- Search only below the root supplied by the user.
- A Vault candidate is a directory containing `.obsidian`.
- Exclude confirmed Vault subtrees from PDF-library ranking.
- Rank PDF-library candidates by a library-like name, shallow depth, recursive PDF count, and coverage of PDFs outside the Vault.
- Keep every discovered plausible candidate in the bounded local result with evidence; the CLI returns pages (20 per kind by default, maximum 100). Counts and ambiguity use the whole discovered set, not just the displayed page. A high score is not confirmation.

## Configuration Rules

`configure` requires explicit Vault and library relative paths. Validate existence, kind, containment, and source markers before the atomic config write. When requested, call the installed `obsidian-vault-notes/scripts/setup_local.py` with an explicit Vault root and build its local index.

`relink` changes only explicitly supplied fields. `forget` removes one source descriptor or all configuration only when the caller passes the acknowledgement flag required by the CLI.

Use `root-relink`, not ordinary `relink`, after moving an initialized schema-v2 root. It verifies the root marker and coordinates both receiver configs and note URI updates. If old and new roots both exist, require a move-versus-copy decision; a writable copy receives a new root ID.

## Missing-Path Status

Use these states:

- `healthy`: configured path exists and matches its source kind
- `missing_root`: knowledge root is absent; require a new root or explicit forgetting
- `missing_source`: one child source is absent; rediscover under the existing root without writing
- `unconfigured`: no usable configuration exists
- `invalid_config`: JSON, schema, containment, or source-kind validation failed

Never turn rediscovery output into a config update without a user decision.


Ordinary `status` reports current configured source health without rediscovery or unrelated suite probes. Use explicit `discover --root ...` when replacement candidates are needed. The local configuration API may request `status(..., rediscover=True)` for that same already-authorized discovery operation; the default is false. Neither result permits a configuration write. Full CLI suite diagnostics use `status --diagnostics --diagnostic-purpose "<specific fault>"`; do not treat that option as indexing or OCR authorization.

Discovery shares a finite directory/entry budget across Vault detection and PDF metadata counting. Defaults are 5,000 directory visits and 50,000 directory entries. `--max-directories` and `--max-entries` permit explicit adjustment up to 100,000 and 1,000,000; narrow the named root first. No PDF body is opened. If Vault discovery is incomplete, PDF candidate ranking is deferred to avoid treating an undiscovered Vault as a library. `scan_complete=false` and `counts_complete=false` mean counts are partial; ambiguity remains true even if one candidate is visible. `--offset`/`--limit` page the discovered set without enlarging the scan budget. Internal APIs and interactive setup keep the bounded local candidate set for exact user selection.

Full suite diagnostics return a closed component overview: readiness, known dependency checks, index identity/coverage, OCR conditions, question coverage and unimported counts, and resource conflict/reference counts. Raw child commands, paths, logs, unknown fields and record samples are omitted before the owned CLI returns; failed diagnostics retain the same typed conditions. Use the named component operation for justified detail. Integration JSON with explicit `ok: false` remains a failure even when the process exits zero; successful JSON without an `ok` field is also accepted. This output filter does not restrict the underlying diagnostic scan or establish a cumulative task budget.
