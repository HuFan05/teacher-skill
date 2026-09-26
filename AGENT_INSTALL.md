# Vendor-neutral installation runbook

Read `package-manifest.json`. Keep `payload/` immutable. The suite has six Skills: `teacher` (primary; the need-discovery rule and the answer gate under `scripts/`), `cs-ai-research-solve`, `cs-ai-computation`, `pdf-paper-search`, `manage-personal-knowledge`, `obsidian-vault-notes`. Distinguish installation compatibility from optional backends (`pdftotext`, accelerators, solvers, proof assistants) and from agent availability. No model endpoint is configured or needed: the Skills run inside the host agent.

1. Verify and discover: `python3 install.py --dry-run --agent generic --target-root "<AGENT_SKILLS_ROOT>"`. On Windows use `python` or `INSTALL_WINDOWS.cmd`. Dry-run reports runtime, OS, architecture, disk space and local paths without installing or making network requests.
2. Choose the target: `--agent codex` (`${CODEX_HOME:-~/.codex}/skills`), `--agent claude` (`${CLAUDE_CONFIG_DIR:-~/.claude}/skills`), or `--agent generic --target-root "<AGENT_SKILLS_ROOT>"`. For automation always supply `--yes`.
3. Optional knowledge root: ask the receiver for the knowledge root and the exact relative Vault and PDF library directories, then add `--knowledge-root "<KNOWLEDGE_ROOT>" --vault "<VAULT_RELATIVE_PATH>" --library "<PDF_LIBRARY_RELATIVE_PATH>"`. The two directories must not be nested. Do not index or register the corpus during installation.
4. Optional local read scope: add `--read-root "<DIR>"` (repeatable) and `--network on|off`. These bound where the teacher Skill re-reads a cited excerpt and whether a cited URL is re-fetched. They are optional; without them the Skill still runs and simply counts unreachable excerpts as declared.
5. Install: `python3 install.py --agent <codex|claude|generic> [--target-root ...] [options from 3–4] --yes`.
6. Doctor: `python3 install.py --doctor` with the same agent and path arguments. Doctor is read-only. Exit 51 means installed files changed; review adaptations before replacement. For a missing configuration, rerun the same install arguments plus `--setup`.
7. Upgrade: existing targets stop with 21. After reviewing them and obtaining replacement authorization, rerun the install command with `--replace`. Targets and managed configurations roll back together after caught setup or validation errors. Power loss and arbitrary third-party side effects are not covered. Exit 52 means recovery needs attention: preserve backups and inspect the local report before another update.
8. Uninstall: `python3 install.py --uninstall` with the same agent and path arguments and `--yes`. All suite targets are removed from discovery; configuration, research library and backups remain. Purging is a separate, user-authorized `--purge-local-state --yes` operation. Do not delete research data.

Exit codes: 0 success; 2 missing input or cancellation; 10 missing Python; 11 old Python; 21 existing installation protected; 31 unsupported platform; 40 permissions, disk or process failure; 50 checksum, setup or validation failure; 51 installed file mismatch; 52 rollback recovery required.

Keep `--agent`, `--target-root`, `--config`, `--knowledge-state-dir`, `--teacher-config-dir` and `--state-dir` identical for every later doctor, setup, replace and uninstall command.

Adaptation contract: preserve the distributed payload, record the mismatch and the smallest receiver-local change, and rerun doctor. Quoting, line endings, executable permissions and metadata mapping may be adapted. Ask before downloads, system-wide package installation, privileges, security or proxy changes, replacement, indexing, or destructive actions. Missing optional backends are not a failed installation. Report the actual tested OS and architecture, configuration and report locations, limitations, and the need to start a new agent session so the Skills are discovered.

Paths containing spaces and non-ASCII characters must be quoted. No sudo, Homebrew, apt, winget or network bootstrap is run automatically.
