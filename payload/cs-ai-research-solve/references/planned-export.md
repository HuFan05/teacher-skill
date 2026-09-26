# Planned research export

Use this contract for ordinary handoff, material completeness, saved export plans and recipient acceptance. Planning and verification never execute research code or grant research review.

## Commands

`delivery-plan PROJECT --out PLAN.json [--contract CONTRACT.json]` performs cheap metadata checks first, then checks only selected material and freezes the resulting selection. The complete plan is an external create-only artifact. Its result reports the plan hash and any known blockers. A blocked plan is useful diagnosis, not export success.

`export PROJECT --out HANDOFF.zip --plan PLAN.json --expect-plan SHA256` consumes the exact plan. Source snapshot, selected records, review scope, material inventory and exporter identity must still agree. Explicit plans carry their own budget, priorities and portable-history choices. Do not silently replace an existing plan or mix it with new selection options.

Ordinary `export` without a saved plan uses the same planning and content-preparation functions internally; its `crs-exchange/v1` manifest then omits `delivery_plan`. An explicit plan or contract binds that plan into the manifest and namespace as `delivery_plan` (`delivery-plan.json`). The reader verifies both shapes. The plan is transport metadata, not a change to stored research records or reviews.

## Delivery contract

`crs-delivery-contract/v1` contains exactly `schema`, `scope`, `groups`, `limitations`. Scope is `reviewed_exchange` or `offline_reproduction`. A group declares `id`, exact `records`, `files`, `entrypoint`, `argv`, `expected_outputs`, `environment`, and `unresolved_dependencies`.

Files map portable relative names to `{sha256, role}`. Roles are `code`, `configuration`, `input`, `certificate`, or `optional`; the first four are necessary for offline reproduction. Every member must already be evidence of a declared reviewed group record. Do not add unrelated code to a reviewed package through this declaration. Distinct logical filenames may refer to the same packaged byte identity.

Environment contains `runtime`, `version`, `platform` and a `packages` string list. These declare recipient prerequisites; they do not install or verify them. Invocation names its entrypoint explicitly. Expected outputs map new relative filenames to exact byte identities. A zero exit code without the declared result checks does not establish replay success. Unknown dynamic dependencies stay explicit. Python import analysis reads only declared sources; it is conservative diagnosis, not a proof of runtime closure or permission to execute code.

## Evidence levels

Metadata selection does not verify asset bytes. Content preparation verifies chosen bytes and may use an already reviewed reference for optional material with machine-local paths. A required privacy, integrity or availability failure blocks that delivery scope. All final choices are frozen before compression. Packing rechecks consumed source bytes; a saved plan is not authority to trust changed files.

An omitted reference records content identity and the reviewed locator. It does not imply this export reopened those bytes or confirmed current availability. No filename extension or size alone decides whether material is research-essential.

Package verification recomputes material availability from actual members, but reports `replay_performed=false` and `offline_reproduction_verified=false`. Declared material completeness is only readiness for the separately authorized, inspected recipient replay. It is never a new research verdict.

## Compatibility and recovery

Normal additions to a research project do not invalidate earlier evidence solely because the software changed. Actual corrections, dependency changes and withdrawn premises still affect the relevant claims. Representation changes remain explicit, scoped operations; ordinary export never rewrites immutable history.

The local `finish` view does not run whole-project duplicate cleanup or a whole-history content audit. Its local map can include unreviewed history and unverified local file links; only its declared exchange artifact is shareable. Whole-project conformity remains a separate explicit audit. This is a consumer prerequisite boundary, not a claim that existing duplicate content was repaired.

Output is create-only. The temporary archive is verified before publication; stale source snapshots and mismatched plan hashes fail. A crashed process may leave owned scratch for inspection. No lock file or unfinished record alone proves a process is alive; inspect its actual handle before recovery. Do not relaunch a completed mutation just because its response was truncated.


## Recipient restoration and bounded replay

`restore-delivery BUNDLE --group ID --out NEW_DIRECTORY` uses only the final ZIP or complete parts directory. It validates the package with trusted local tools, restores included members by their declared relative names, and publishes a restoration receipt. Missing members and dependency findings remain visible; restoration success is not offline reproduction.

After inspecting the restored code, `replay-delivery RESTORED --out NEW_DIRECTORY --expect-receipt SHA256 --execute --timeout 60` binds the exact restoration receipt and rechecks every restored input. The built-in supervised route currently supports Windows, the installed Python version (exact version or a matching version prefix), standard-library-only declarations, and exactly `['{python}', entrypoint]`. Other runtimes, package environments and invocation forms remain explicitly unsupported by this route; verify them independently instead of changing the declared contract to obtain a pass.

Replay runs copied inputs with inherited Python paths and user-site loading disabled, checks exact declared output hashes and unchanged input bytes, and confirms the owned process tree stopped. A zero exit code alone cannot pass. This is an inspected-code execution route, not a filesystem or network sandbox. Test claims that sender resources were unavailable need separate environmental evidence. Logs, a started receipt and final results are retained outside the restored inputs. An interrupted run with no final result remains unresolved and never resumes automatically. A replay result establishes only this group and runtime's declared output checks; it adds no research or contributor authority.

`finish` accepts the same `--contract`, `--plan`, and `--expect-plan` bindings as export. It verifies and packages the reviewed delivery before preparing the local history map. Final publication rechecks the source snapshot under the store lock for single archives, multipart exports and finish directories.

Verification of a bound delivery plan binds the actual bundled tool bytes, validates all byte limits and combined input/output path collisions, and recomputes dependency findings from included declared Python sources. A package without a bound plan remains an inert reviewed research exchange.


Prepared Python material checks include declared static file names, unresolved dynamic file construction, dynamic directory enumeration, and dynamic code evaluation. These conservative findings describe inspection gaps, not guessed dependencies or a complete static proof. Resolve a finding by inspecting and explicitly declaring the actual material route; do not remove the source operation merely to pass the export check. A blocked `delivery-plan` still saves its diagnostic report, but its public result has `ok=false` and a nonzero exit code. An ordinary reviewed projection retains its closure exclusion semantics; an explicit material group's record set cannot silently disappear from that projection.


## Continue an earlier delivery without rediscovering its choices

Keep the external prepared plan with the delivery receipt. After ordinary research changes the source snapshot, use `delivery-plan PROJECT --out NEW_PLAN.json --reuse-plan PREVIOUS_PLAN.json --expect-plan SHA256`. This explicitly carries the earlier scope, material declarations, priorities, budgets and portable replacement pairs. It validates the previous plan identity and source project, rebinds replacement intent to the new snapshot, and performs current projection, dependency, selected-byte and privacy checks again. New and removed record counts are reported. A changed required review, foreign project or incompatible historical replacement still blocks; no research approval is inferred.

The new plan carries only `previous_plan_sha256`, not a nested previous plan or duplicated historical payload. The previous plan remains unchanged. A plan without this optional lineage field has no declared predecessor. Export still requires the new exact plan hash; a stale old plan never becomes executable merely because a refresh was requested. To alter scope or budgets, explicitly prepare a fresh plan instead of mixing overrides into reuse. Ordinary export and refresh remain read-only against the research project.

The path classifier first checks whether the exact string contains any candidate local-path match. Strings without one skip TeX-span parsing. This is an invocation-local exact-text shortcut, not an mtime cache or a substitute for hashing selected source bytes.


## Selected container checks before freezing

Content preparation retains the existing recursive uniqueness guarantee. It checks selected materials with the same supported formats and aggregate scan ceilings, required materials first, against accepted material content plus canonical record/review and bundled tool identities. Only nonrequired material with an applicable reviewed reference can become `reference_after_container_check`; its reason remains explicit. Its rejected bytes never remain in the accepted-content index, while its consumed scan resources still count. Required conflicts or undecodable necessary containers block the requested plan; source bytes are never rewritten. Dependency availability and selected totals are recomputed after these decisions. A declared optional dependency that is actually absent cannot count as supplied.

Final generated map, manifest and namespace bytes do not yet exist during material preparation. Their combined uniqueness, final capacity, source continuity and archive integrity remain mandatory final checks; `ready_for_packaging` does not certify those later results. The final recursive verifier applies to every exchange. Material and output names reject Windows-forbidden characters before restoration or execution.


## Ordinary export maintenance

Before repairs, inspect the create-only preflight report's `direct_revision_discovery`. It examines exact direct predecessors and current review states, then validates the combined portable-history projection and its review-material closure. A null `validated_portable_history` means no reusable plan was established; conflicting branches, dependency remapping and missing fidelity reviews stay blocked. Reuse is explicit and snapshot-bound. This discovery cannot judge equivalent rewritten proof, argument or experiment content.

Declare full proofs or correctness arguments, experiment configurations and other necessary material in contract groups (`input` can hold nonexecutable proof or argument text). Every nonoptional member is required in both scopes. A reviewed-exchange text-only group may have null entrypoint and empty argv/outputs: its text can be complete without claiming executable replay. Required bytes may not silently become references after privacy or container checks. To use an equivalent representation, first establish its applicable review and evidence relationship and declare its exact identity; caller-supplied aliases confer no authority. An absent nonoptional reviewed-exchange member fails closed. A saved plan remains readable but must be refreshed when exporter bytes differ.

Use `prepare-report --source INSPECTED_REPORT --out NEW_REPORT` to preserve final UTF-8 bytes, including LF/CRLF, and obtain the hash for a review submission. It never changes an existing file, admits a review or checks the research content. Do not hash an in-memory string and then write it through newline translation.

Export holds the same per-store lock from entry through publication, so contention returns before material scanning or compression. It never retries publication automatically. Serialize same-store operation_io users in the calling workflow; unrelated stores and external rendering remain independent. A failed call keeps a compact receipt with unknown hashes represented by null. An oversized successful response retains `delivery_receipt`: terminal state, actual output, archive/plan identities, counts and `verify-bundle` read arguments. Use that read route after uncertain/truncated output; do not repeat export. Multipart outputs retain the part count and actual directory locator; their individual identities remain in the parts index.

Website validation deduplicates target existence checks within one call only; all language and anchor checks remain. New calls recheck actual files. Preflight path findings add structural context labels, never matched private text or automatic exemptions. Code/formula ambiguity stays blocking until inspected. Batch these findings with missing material, review binding and environment issues before changing research history. Reuse the saved-plan route and unchanged evidence; ordinary export does not request a full-history audit, a new research round or a complete browser-map upgrade.

`required_materials_included` reports exact required-byte availability separately from executable group completeness. For multipart output, `sha256` is null and `index_sha256` binds the final parts.json; its indexed hashes bind every volume. `verify-bundle` verifies the entire set. Finish holds the same store lock through its own final publication and relocates all delivery receipt locators to the final destination. File I/O exceptions preserve unresolved recovery context without inferring success from an existing path.
