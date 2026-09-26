# Skill Maintenance

Use this reference when editing this skill's `SKILL.md`, references, scripts, tests, or agent prompts.

## Two-Layer Rule

The machine-facing skill files and the human-readable maintenance manual serve different readers:

- `SKILL.md`, `references/`, and `scripts/` tell agents what to do.
- The Teacher package maintenance manual, `docs/MAINTENANCE.md`, explains to the user how the package is maintained, checked, and rechecked.

Do not copy long execution rules into the maintenance manual. Do not replace machine rules with a prose rationale note.

## Required Flow

1. Inspect the maintenance manual named in `SKILL.md`.
2. Make changes in a staging copy when the change is nontrivial.
3. Run this Skill's checks against the staging copy: `python3 -m unittest discover -s tests -p "test_*.py"`, the bundled index tests under `scripts/obsidian_local_kb/tests`, `python3 scripts/validate_terminology.py --skill-root <SKILL_ROOT>`, and `python3 scripts/check_computation_handoff.py --skill-file SKILL.md`.
4. Copy the verified staging copy to the installed Skill root and confirm that both copies match.
5. After an intentional package change, recompute and verify the package checksums as described in the maintenance manual.
6. If the user authorized updating the maintenance manual, add a short entry.
7. Report machine-rule changes and maintenance-manual changes separately.

## Maintenance Manual Entry Shape

Use these fields:

```text
更新内容：
为什么改：
怎样使用：
保留边界：
```

Use relative Markdown links back to the changed skill file or heading.

## Boundaries

- Do not record every tiny typo fix.
- Do not hide a behavior change by only updating code.
- Do not say the skill is updated until the staging and installed copies match, every check in the required flow passes, and the package checksums verify.
- Do not push. Commit only when the user grants `commit_allowed`.
