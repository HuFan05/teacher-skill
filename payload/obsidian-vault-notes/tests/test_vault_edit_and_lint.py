from __future__ import annotations

import json
import hashlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


SKILL_ROOT = Path(__file__).resolve().parents[1]
VAULT_EDIT = SKILL_ROOT / "scripts" / "vault_edit.py"
ARTICLE_LINT = SKILL_ROOT / "scripts" / "lint_note_article_revision.py"
STYLE_FIXTURES = SKILL_ROOT / "tests" / "fixtures" / "style_rewrite"
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import vault_edit as vault_edit_module


class VaultEditAndArticleLintTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name).resolve()
        self.note = self.root / "note.md"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def write_note(self, text: str) -> None:
        self.note.write_text(text, encoding="utf-8", newline="\n")

    def run_edit(self, *args: str) -> tuple[int, dict[str, object], str]:
        command = [sys.executable, str(VAULT_EDIT), args[0], "--vault-root", str(self.root), *args[1:]]
        completed = subprocess.run(
            command,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={
                **os.environ,
                "PYTHONDONTWRITEBYTECODE": "1",
                "MPK_HARNESS": str(self.root / "synthetic-missing-harness.py"),
            },
            check=False,
        )
        payload = json.loads(completed.stdout)
        return completed.returncode, payload, completed.stderr

    def run_lint(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-B", str(ARTICLE_LINT), *args],
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            check=False,
        )

    def run_lint_json(self, *args: str) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
        completed = self.run_lint(*args, "--json")
        return completed, json.loads(completed.stdout)

    def test_dry_run_does_not_write_and_write_changes(self) -> None:
        self.write_note("alpha\n")

        code, payload, _stderr = self.run_edit(
            "append-section",
            "--file",
            str(self.note),
            "--text",
            "beta",
        )
        self.assertEqual(code, 0)
        self.assertTrue(payload["changed"])
        self.assertFalse(payload["write"])
        self.assertEqual(self.note.read_text(encoding="utf-8"), "alpha\n")

        code, payload, _stderr = self.run_edit(
            "append-section",
            "--file",
            str(self.note),
            "--text",
            "beta",
            "--write",
        )
        self.assertEqual(code, 0)
        self.assertTrue(payload["changed"])
        self.assertTrue(payload["write"])
        self.assertIn("beta", self.note.read_text(encoding="utf-8"))

    def test_dry_run_rejects_new_tex_control_word_shadow(self) -> None:
        self.write_note("$a\\qquad b$\n")
        code, payload, _stderr = self.run_edit(
            "append-section",
            "--file",
            str(self.note),
            "--text",
            "$X:=x,qquad Y:=y$",
        )
        self.assertEqual(code, 2)
        self.assertFalse(payload["applied"])
        self.assertEqual(payload["error"], "formula_transport_check")

    def test_dry_run_allows_preexisting_shadow_when_edit_adds_none(self) -> None:
        self.write_note("$a\\qquad b$\n$X:=x,qquad Y:=y$\n")
        code, payload, _stderr = self.run_edit(
            "append-section",
            "--file",
            str(self.note),
            "--text",
            "ordinary prose",
        )
        self.assertEqual(code, 0, payload)
        self.assertTrue(payload["changed"])

    def test_repeated_heading_requires_occurrence(self) -> None:
        self.write_note("## Same\nA\n\n## Same\nB\n")

        code, payload, _stderr = self.run_edit(
            "delete-section",
            "--file",
            str(self.note),
            "--heading",
            "Same",
        )

        self.assertEqual(code, 2)
        self.assertIn("ambiguous", str(payload["error"]))
        self.assertEqual(len(payload["heading_matches"]), 2)

    def test_public_operations_return_stable_json(self) -> None:
        self.write_note("---\nstatus: draft\ntags:\n  - old\n---\n\n## Target\nold old\n")

        cases = [
            (
                "append-section",
                ["--file", str(self.note), "--text", "tail"],
            ),
            (
                "delete-section",
                ["--file", str(self.note), "--heading", "Target", "--keep-heading"],
            ),
            (
                "delete-frontmatter-key",
                ["--file", str(self.note), "--key", "status"],
            ),
            (
                "replace-text",
                ["--file", str(self.note), "--find", "old", "--text", "new", "--all"],
            ),
        ]

        for operation, args in cases:
            with self.subTest(operation=operation):
                code, payload, _stderr = self.run_edit(operation, *args)
                self.assertEqual(code, 0)
                self.assertEqual(payload["operation"], operation)
                self.assertEqual(payload["file"], str(self.note))
                self.assertIn("changed", payload)
                self.assertIn("inserted_lines", payload)
                self.assertIn("removed_lines", payload)
                self.assertIn("warnings", payload)
                self.assertFalse(payload["write"])

    def test_article_lint_flags_style_risks_and_skips_literal_or_quoted_uses(self) -> None:
        self.write_note(
            "\n".join(
                [
                    "真正困难的，从来不是工具不够多，而是不知道如何判断结果。",
                    "便宜只长在疼的地方。",
                    "判断标准逐渐漂移。",
                    "“漂移”这个词在这里只是被引用。",
                    "这更像是短线波动现金流，而不是稳定投资收益。",
                    "蒸馏更像一种有损压缩，教师模型的行为被压缩进更小的学生模型。",
                    "“更像”这个词在这里只是被引用。",
                    "树长在土里。",
                ]
            )
            + "\n"
        )

        completed = self.run_lint("--file", str(self.note), "--all")

        self.assertEqual(completed.returncode, 1)
        self.assertIn("absolute_conglai_contrast", completed.stdout)
        self.assertIn("abstract_changzai_metaphor", completed.stdout)
        self.assertIn("abstract_drift_metaphor", completed.stdout)
        self.assertNotIn("loose_gengxiang_comparison", completed.stdout)
        self.assertNotIn("line 4", completed.stdout)
        self.assertNotIn("line 7", completed.stdout)
        self.assertNotIn("line 8", completed.stdout)

    def test_vault_edit_rejects_outside_vault_and_expect_sha_mismatch(self) -> None:
        outside = self.root.parent / "outside.md"
        outside.write_text("outside\n", encoding="utf-8", newline="\n")
        completed = subprocess.run(
            [
                sys.executable,
                str(VAULT_EDIT),
                "check",
                "--vault-root",
                str(self.root),
                "--file",
                str(outside),
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        payload = json.loads(completed.stdout)
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(payload["error"], "outside_authorized_root")

        self.write_note("alpha\n")
        code, payload, _stderr = self.run_edit(
            "append-section",
            "--file",
            str(self.note),
            "--text",
            "beta",
            "--expect-sha256",
            "0" * 64,
            "--write",
        )
        self.assertEqual(code, 2)
        self.assertEqual(payload["error"], "hash_guard_or_binding_failed")
        self.assertEqual(self.note.read_text(encoding="utf-8"), "alpha\n")

    def test_local_edit_preserves_bom_mixed_endings_and_markdown_hard_breaks(self) -> None:
        original = (
            b"\xef\xbb\xbfoutside  \r\n"
            b"## Target\n"
            b"old\r\n"
            b"## Tail\r\n"
            b"keep  \r\n"
        )
        self.note.write_bytes(original)

        code, payload, stderr = self.run_edit(
            "replace-text",
            "--file",
            str(self.note),
            "--find",
            "old",
            "--text",
            "new",
            "--write",
        )

        self.assertEqual(code, 0, stderr)
        self.assertTrue(payload["applied"])
        self.assertTrue(payload["had_bom"])
        self.assertEqual(payload["race_check"], "passed")
        self.assertEqual(
            self.note.read_bytes(),
            original.replace(b"old", b"new"),
        )

    def test_repeated_logical_lines_do_not_realign_mixed_line_endings(self) -> None:
        original = b"A\nX\nA\nA\r\n"
        self.note.write_bytes(original)

        code, payload, stderr = self.run_edit(
            "replace-text",
            "--file",
            str(self.note),
            "--find",
            "X",
            "--text",
            "A",
            "--write",
        )

        self.assertEqual(code, 0, stderr)
        self.assertTrue(payload["applied"])
        self.assertEqual(self.note.read_bytes(), b"A\nA\nA\nA\r\n")

        repeated_sections = b"## Same\r\nbody\r\n\r\n## Same\nbody\n\n## Tail\r\nx\r\n"
        self.note.write_bytes(repeated_sections)
        code, payload, stderr = self.run_edit(
            "delete-section",
            "--file",
            str(self.note),
            "--heading",
            "Same",
            "--occurrence",
            "2",
            "--write",
        )
        self.assertEqual(code, 0, stderr)
        self.assertTrue(payload["applied"])
        self.assertEqual(self.note.read_bytes(), b"## Same\r\nbody\r\n\r\n## Tail\r\nx\r\n")

    def test_frontmatter_edit_preserves_missing_final_newline(self) -> None:
        original = b"---\r\nstatus: old\r\n---\r\nbody"
        self.note.write_bytes(original)
        code, payload, stderr = self.run_edit(
            "set-frontmatter",
            "--file",
            str(self.note),
            "--key",
            "status",
            "--text",
            "new",
            "--write",
        )
        self.assertEqual(code, 0, stderr)
        self.assertTrue(payload["applied"])
        self.assertEqual(self.note.read_bytes(), b"---\r\nstatus: new\r\n---\r\nbody")

    def test_noop_write_still_enforces_hash_and_mtime_preconditions(self) -> None:
        self.write_note("alpha\nbeta\n")
        for guard in (
            ("--expect-sha256", "0" * 64),
            ("--expect-mtime", "0"),
        ):
            with self.subTest(guard=guard[0]):
                code, payload, _stderr = self.run_edit(
                    "append-section",
                    "--file",
                    str(self.note),
                    "--text",
                    "beta",
                    guard[0],
                    guard[1],
                    "--write",
                )
                self.assertEqual(code, 2)
                self.assertFalse(payload["applied"])
                self.assertIn(payload["error"], {"hash_guard_or_binding_failed", "concurrent_change"})
                self.assertEqual(self.note.read_text(encoding="utf-8"), "alpha\nbeta\n")

    def test_edit_rejects_invalid_utf8_and_complex_frontmatter(self) -> None:
        self.note.write_bytes(b"valid\n\xff\n")
        code, payload, _stderr = self.run_edit("check", "--file", str(self.note))
        self.assertEqual(code, 2)
        self.assertEqual(payload["error"], "invalid_encoding")

        complex_frontmatter = [
            "---\nsummary: |\n  line one\n  line two\n---\nbody\n",
            "---\nmetadata:\n  owner: agent\n---\nbody\n",
            "---\ntags: [one, two]\n---\nbody\n",
            "---\nshared: &base value\n---\nbody\n",
            "---\ntyped: !custom value\n---\nbody\n",
        ]
        for original in complex_frontmatter:
            with self.subTest(frontmatter=original.splitlines()[1]):
                self.write_note(original)
                code, payload, _stderr = self.run_edit(
                    "set-frontmatter",
                    "--file",
                    str(self.note),
                    "--key",
                    "status",
                    "--text",
                    "draft",
                    "--write",
                )
                self.assertEqual(code, 2)
                self.assertEqual(payload["error"], "operation_validation_failed")
                self.assertEqual(self.note.read_text(encoding="utf-8"), original)

    def test_edit_audit_reports_pre_and_post_hashes(self) -> None:
        self.write_note("alpha\n")
        original_sha = hashlib.sha256(self.note.read_bytes()).hexdigest()

        code, dry_run, stderr = self.run_edit(
            "append-section",
            "--file",
            str(self.note),
            "--text",
            "beta",
        )
        self.assertEqual(code, 0, stderr)
        self.assertFalse(dry_run["applied"])
        self.assertEqual(dry_run["pre_sha256"], original_sha)
        self.assertEqual(dry_run["post_sha256"], original_sha)
        self.assertEqual(dry_run["race_check"], "not_run")

        code, written, stderr = self.run_edit(
            "append-section",
            "--file",
            str(self.note),
            "--text",
            "beta",
            "--expect-sha256",
            original_sha,
            "--write",
        )
        self.assertEqual(code, 0, stderr)
        self.assertTrue(written["applied"])
        self.assertEqual(written["pre_sha256"], original_sha)
        self.assertEqual(written["post_sha256"], hashlib.sha256(self.note.read_bytes()).hexdigest())
        self.assertNotEqual(written["pre_sha256"], written["post_sha256"])

    def test_edit_detects_change_between_snapshot_and_replace(self) -> None:
        self.write_note("alpha\n")
        original = self.note.read_bytes()
        real_prepare = vault_edit_module.prepare_temp_file

        def prepare_then_race(path: Path, payload: bytes) -> Path:
            temp_path = real_prepare(path, payload)
            path.write_bytes(b"user change\n")
            return temp_path

        stdout = io.StringIO()
        with mock.patch.dict(os.environ, {"MPK_HARNESS": str(self.root / "synthetic-missing-harness.py")}):
            with mock.patch.object(vault_edit_module, "prepare_temp_file", side_effect=prepare_then_race):
                with redirect_stdout(stdout):
                    code = vault_edit_module.main(
                        [
                            "append-section",
                            "--vault-root",
                            str(self.root),
                            "--file",
                            str(self.note),
                            "--text",
                            "beta",
                            "--write",
                        ]
                    )
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 2)
        self.assertFalse(payload["applied"])
        self.assertEqual(payload["race_check"], "failed")
        self.assertEqual(payload["error"], "concurrent_change")
        self.assertEqual(self.note.read_bytes(), b"user change\n")
        self.assertNotEqual(self.note.read_bytes(), original)

    def test_heading_in_fenced_code_is_ignored_and_duplicate_heading_requires_flag(self) -> None:
        self.write_note("```markdown\n## Same\n```\n\n## Same\nbody\n")

        code, payload, _stderr = self.run_edit(
            "delete-section",
            "--file",
            str(self.note),
            "--heading",
            "Same",
        )
        self.assertEqual(code, 0)
        self.assertEqual(len(payload["heading_matches"]), 1)

        self.write_note("## Existing\nbody\n")
        code, payload, _stderr = self.run_edit(
            "append-section",
            "--file",
            str(self.note),
            "--text",
            "## Existing\nnew body\n",
        )
        self.assertEqual(code, 2)
        self.assertEqual(payload["error"], "duplicate_target")

        code, payload, _stderr = self.run_edit(
            "append-section",
            "--file",
            str(self.note),
            "--text",
            "## Existing\nnew body\n",
            "--allow-duplicate-heading",
        )
        self.assertEqual(code, 0)
        self.assertTrue(payload["changed"])

    def test_article_lint_skips_fenced_code_and_quoted_changzai_and_uses_style_terms(self) -> None:
        terms = self.root / "style_terms.yml"
        terms.write_text("suspicious_terms:\n  - 抓手\nallowed_terms:\n  - FTS5\n", encoding="utf-8", newline="\n")
        self.write_note(
            "\n".join(
                [
                    "```markdown",
                    "真正困难的，从来不是工具不够多，而是不知道如何判断结果。",
                    "便宜只长在疼的地方。",
                    "```",
                    "“便宜只长在疼的地方”这句话只是被引用。",
                    "这个抓手需要删掉。",
                ]
            )
            + "\n"
        )

        completed = self.run_lint("--file", str(self.note), "--all", "--style-terms", str(terms))

        self.assertEqual(completed.returncode, 1)
        self.assertIn("suspicious_style_term", completed.stdout)
        self.assertNotIn("absolute_conglai_contrast", completed.stdout)
        self.assertNotIn("abstract_changzai_metaphor", completed.stdout)

    def test_chinese_longform_profile_flags_categories_and_preserves_source_labels(self) -> None:
        self.write_note(
            "\n".join(
                [
                    "AI把工具接到最短路问题上。",
                    "候选内容还没有经过核查。",
                    "正式收录前，问题被查找旧文献和测试特殊情形。",
                    "初步筛查变得更便宜。",
                    "真正困难的，从来不是工具不够多，而是不知道如何判断结果。",
                    "状态标签是`Accepted (Oral)`、`SOTA`、`REPRODUCED`和`NOT REPRODUCED`。",
                    "TLA+可以做形式化验证。",
                    "```markdown",
                    "候选内容和本质上是都不应扫描。",
                    "```",
                ]
            )
            + "\n"
        )

        default_completed = self.run_lint("--file", str(self.note), "--all")
        self.assertNotIn("zh_calque", default_completed.stdout)
        self.assertNotIn("zh_empty_noun", default_completed.stdout)

        completed = self.run_lint(
            "--file",
            str(self.note),
            "--all",
            "--style-profile",
            "chinese-longform",
        )

        self.assertEqual(completed.returncode, 1)
        self.assertIn("zh_calque", completed.stdout)
        self.assertIn("zh_empty_noun", completed.stdout)
        self.assertIn("zh_collocation", completed.stdout)
        self.assertIn("zh_project_jargon", completed.stdout)
        self.assertIn("zh_template", completed.stdout)
        self.assertNotIn("line 6", completed.stdout)
        self.assertNotIn("line 7", completed.stdout)
        self.assertNotIn("line 9", completed.stdout)

    def test_chinese_longform_profile_regression_has_conservative_positive_and_negative_cases(self) -> None:
        positives = [
            "结果被置于形式化验证的范围内。",
            "单纯交付一个正确结论不够。",
            "AI开始进入研究工作。",
            "研究者把方法写成pipeline。",
            "工具可以协助形式化。",
            "候选内容需要检查。",
            "局部工作已经完成。",
            "AI的渗透程度尚不明确。",
            "这是一段可靠的研究工作。",
            "这提供的信号很强。",
            "原文对量词的意图有歧义。",
            "这属于反例工作。",
            "问题得到过去没有的尝试次数和路线。",
            "这会降低对这类工作的难度评价。",
            "初步筛查变得更便宜。",
            "这是一批长尾公开问题。",
            "这是面向共同体的清单。",
            "这本质上是一个检索问题。",
            "核心在于来源。",
            "从三个维度来看这个问题。",
            "这背后反映了某种变化。",
            "它形成了一种新的逻辑。",
            "AI把工具接到最短路问题上。",
            "模型形成候选论证。",
            "问题被查找旧文献。",
            "任务可以落到一段旧论证。",
            "这条路线能够被继续检查。",
            "筛查的成本更低。",
            "真正困难的，从来不是工具不够多，而是不知道如何判断结果。",
            "它更像是短线收益，而不是稳定收益。",
            "我们该怎么办？又该如何选择？难道只能等待？未来又会怎样？",
        ]
        negatives = [
            "近线性上界是渐近估计。",
            "增长阶由定义确定。",
            "量词歧义需要单独说明。",
            "显式反例给出具体构造。",
            "候选补丁仍待核查。",
            "形式化验证会检查形式细节。",
            "形式化核查不能代替解释。",
            "依赖图标出局部不变式之间的关系。",
            "验证与证伪都可以帮助研究。",
            "标签是`Accepted (Oral)`。",
            "标签是`SOTA`。",
            "页面显示`NOT REPRODUCED`。",
            "页面显示`REPRODUCED`。",
            "TLA+是形式化规约语言。",
            "pipeline首次出现时可作简短说明。",
            "工程师推进了这次重构。",
            "这个过程形成一个新的例子。",
            "研究者参与讨论。",
            "问题仍然开放。",
            "这项工作已经完成。",
            "正确性论证使用了归纳法。",
            "反例来自一个有限构造。",
            "研究任务有明确边界。",
            "公开题目需要来源。",
            "检查前先读原文。",
            "网站记录了解答。",
            "分析工具可以使用。",
            "文献需要核对。",
            "结构可以解释结果。",
            "这个判断有明确依据。",
            "研究者保存了讨论记录。",
        ]
        self.assertEqual(len(positives), 31)
        self.assertEqual(len(negatives), 31)
        self.write_note("\n".join([*positives, *negatives]) + "\n")

        completed = self.run_lint(
            "--file",
            str(self.note),
            "--all",
            "--style-profile",
            "chinese-longform",
        )

        self.assertEqual(completed.returncode, 1)
        for rule_id in [
            "calque_scope",
            "calque_deliver",
            "calque_enter_work",
            "calque_pipeline",
            "calque_formalize",
            "empty_candidate_content",
            "empty_local_work",
            "empty_penetration",
            "empty_research_segment",
            "empty_signal",
            "collocation_quantifier_intent",
            "collocation_counterexample_work",
            "collocation_question_route",
            "collocation_difficulty_evaluation",
            "collocation_cheap_screening",
            "jargon_long_tail",
            "jargon_community",
            "template_essence",
            "template_core",
            "template_three_dimensions",
            "template_reflect",
            "template_logic",
            "calque_connect",
            "calque_form_candidate",
            "collocation_problem_passive",
            "collocation_task_fall",
            "collocation_route_checked",
            "project_cheap",
            "template_absolute",
            "template_like",
        ]:
            self.assertIn(f"[{rule_id}]", completed.stdout)
        for line_no in range(len(positives) + 1, len(positives) + len(negatives) + 1):
            self.assertNotIn(f"line {line_no} ", completed.stdout)

    def test_article_lint_json_reports_findings_without_ai_probability_or_score(self) -> None:
        self.write_note("如果你愿意，我可以继续补充。\n让我们深入探讨这个问题。\n")

        completed, payload = self.run_lint_json(
            "--file",
            str(self.note),
            "--all",
            "--style-profile",
            "chinese-longform",
        )

        self.assertEqual(completed.returncode, 1)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["scan_scope"], "whole_file")
        self.assertEqual(payload["profile"], "chinese-longform")
        self.assertTrue(payload["has_findings"])
        self.assertNotIn("ai_probability", payload)
        self.assertNotIn("score", payload)
        findings = payload["findings"]
        self.assertTrue(findings)
        for item in findings:
            self.assertEqual(
                set(item),
                {"id", "category", "confidence", "line", "source", "message", "text"},
            )
            self.assertIn(item["confidence"], {"high", "medium", "low"})
        self.assertIn("chat_residue", payload["category_counts"])
        self.assertIn("high", payload["confidence_counts"])

    def test_longform_context_rules_are_low_confidence_and_profile_only(self) -> None:
        self.write_note(
            "\n".join(
                [
                    "---",
                    "title: 合成样例",
                    "---",
                    "# 合成样例",
                    "",
                    "在人工智能飞速发展的今天，学习正在迎来前所未有的变化。",
                    "",
                    "我逐渐意识到，真正重要的是建立自己的学习体系。",
                    "",
                    "学习不是积累知识，而是改造能力。训练也不是完成任务，而是建立系统。",
                    "",
                    "构建反馈闭环，沉淀知识资产，再打通行动链路。",
                    "",
                    "我们该怎么办？又该如何选择？难道只能等待？未来又会怎样？",
                ]
            )
            + "\n"
        )

        default_completed, default_payload = self.run_lint_json("--file", str(self.note), "--all")
        default_ids = {item["id"] for item in default_payload["findings"]}
        for rule_id in {
            "promotional_opening_frame",
            "first_person_evaluative_preamble",
            "repeated_contrast_frames",
            "abstract_project_cluster",
            "question_chain_without_followthrough",
        }:
            self.assertNotIn(rule_id, default_ids, default_completed.stdout)

        completed, payload = self.run_lint_json(
            "--file",
            str(self.note),
            "--all",
            "--style-profile",
            "chinese-longform",
        )
        findings = {item["id"]: item for item in payload["findings"]}
        for rule_id in {
            "promotional_opening_frame",
            "first_person_evaluative_preamble",
            "repeated_contrast_frames",
            "abstract_project_cluster",
            "question_chain_without_followthrough",
        }:
            self.assertIn(rule_id, findings, (rule_id, completed.stdout))
            self.assertEqual(findings[rule_id]["confidence"], "low")

    def test_author_analogy_and_concrete_technical_terms_do_not_trigger_default_word_rules(self) -> None:
        self.write_note(
            "\n".join(
                [
                    "蒸馏更像一种有损压缩，教师模型的行为被压缩进更小的学生模型。",
                    "控制系统通过反馈闭环修正误差。",
                    "软件调用链路记录了请求经过的服务。",
                    "控制系统建立反馈闭环后，通过通信链路对齐传感器数据。",
                    "实验数据沉淀在逐次保存的记录中。",
                    "时钟漂移需要校准。",
                    "[[概念漂移]]是机器学习中的技术术语。",
                    "在连续三次检验失败后，我意识到原来的假设不成立。",
                ]
            )
            + "\n"
        )

        completed, payload = self.run_lint_json(
            "--file",
            str(self.note),
            "--all",
            "--style-profile",
            "chinese-longform",
        )
        found = {item["id"] for item in payload["findings"]}
        self.assertNotIn("loose_gengxiang_comparison", found)
        self.assertNotIn("template_like", found)
        self.assertNotIn("suspicious_style_term", found)
        self.assertNotIn("abstract_project_cluster", found)
        self.assertNotIn("first_person_evaluative_preamble", found)
        self.assertNotIn("abstract_drift_metaphor", found)
        self.assertEqual(completed.returncode, 0, payload)

    def test_git_diff_does_not_merge_noncontiguous_added_lines_into_one_paragraph(self) -> None:
        self.write_note("开头。\n占位甲。\n中间原文。\n占位乙。\n结尾。\n")
        for command in (
            ["git", "init"],
            ["git", "config", "user.email", "tests@example.invalid"],
            ["git", "config", "user.name", "Skill Tests"],
            ["git", "add", "note.md"],
            ["git", "commit", "-m", "baseline"],
        ):
            completed = subprocess.run(command, cwd=self.root, capture_output=True, text=True, check=False)
            self.assertEqual(completed.returncode, 0, (command, completed.stderr))
        self.write_note(
            "开头。\n学习不是积累数量，而是检查能否迁移。\n中间原文。\n"
            "训练不是完成任务，而是记录失败位置。\n结尾。\n"
        )

        completed, payload = self.run_lint_json(
            "--file",
            str(self.note),
            "--repo",
            str(self.root),
            "--style-profile",
            "chinese-longform",
        )
        found = {item["id"] for item in payload["findings"]}
        self.assertNotIn("repeated_contrast_frames", found, completed.stdout)

    def test_style_rewrite_fixtures_cover_five_note_types_and_protected_contexts(self) -> None:
        expected_ids = {
            "reflective.md": {"fake_candid_opener", "template_absolute", "empty_future_bright"},
            "explanatory.md": {"heading_restatement", "vague_authority", "significance_pivotal"},
            "algorithm.md": set(),
            "source-commentary.md": {"vague_authority"},
            "technical.md": {"diff_anchored_prose", "inline_header_list", "staccato_run"},
        }

        for name, expected in expected_ids.items():
            with self.subTest(name=name):
                completed, payload = self.run_lint_json(
                    "--file",
                    str(STYLE_FIXTURES / name),
                    "--all",
                    "--style-profile",
                    "chinese-longform",
                )
                found = {item["id"] for item in payload["findings"]}
                self.assertTrue(expected <= found, (name, expected - found, completed.stderr))
                if name == "algorithm.md":
                    self.assertEqual(completed.returncode, 0, payload)
                if name == "source-commentary.md":
                    self.assertEqual(sum(item["id"] == "vague_authority" for item in payload["findings"]), 1)
                if name == "technical.md":
                    self.assertNotIn("tool_marker_residue", found)

    def test_profile_rules_skip_quotes_code_formulas_links_and_source_labels(self) -> None:
        self.write_note(
            "\n".join(
                [
                    "“让我们深入探讨”只是原文引语。",
                    "代码值是`turn1search0`。",
                    "公式标签是$x_{\\text{未来可期}}$。",
                    "链接文字是[让我们深入探讨](https://example.com)。",
                    "> “业内人士普遍认为，未来可期。”",
                    "> 我逐渐意识到，真正重要的是建立反馈闭环。",
                    "> [!quote]",
                    "> 在人工智能飞速发展的今天，真正重要的是赋能学习。",
                    "> 原文使用\\(x\\)，这里必须保持原样。",
                    "状态标签是`REPRODUCED`。",
                    "正确性论证分成不变式、归纳步骤和终止性三部分。",
                    "不变式由归纳法维持——这里的破折号承担补充说明。",
                ]
            )
            + "\n"
        )

        completed, payload = self.run_lint_json(
            "--file",
            str(self.note),
            "--all",
            "--style-profile",
            "chinese-longform",
        )

        self.assertEqual(completed.returncode, 0, payload)
        self.assertEqual(payload["findings"], [])

    def test_baseline_semantic_sentinels_detect_only_changed_protected_meaning(self) -> None:
        baseline = self.root / "baseline.md"
        baseline.write_text(
            "\n".join(
                [
                    "---",
                    "title: 旧标题",
                    "---",
                    "# 旧标题",
                    "样本来自2024年，占10%。可能仍有遗漏。",
                    "行内引文是“原始说法甲”。",
                    "行内公式是$x=1$。",
                    "$$y=2$$",
                    "参见[[旧笔记]]和[来源](https://example.com/a)。 ^old",
                    "另见[旧来源][src-a]。",
                    "[src-a]: https://example.com/ref-a",
                    "裸地址是https://example.com/plain-a",
                    "状态是`DRAFT`。",
                    "> “原始引文甲。”",
                    "> 普通块引用甲。",
                    "> [!quote]",
                    "> 引用型callout甲。",
                    "> [!note] 旧说明",
                    "```python",
                    "value = 1",
                    "```",
                ]
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        self.write_note(
            "\n".join(
                [
                    "---",
                    "title: 新标题",
                    "---",
                    "# 新标题",
                    "样本来自2025年，占20%。一定没有遗漏。",
                    "行内引文是“原始说法乙”。",
                    "行内公式是$x=2$。",
                    "$$y=3$$",
                    "参见[[新笔记]]和[来源](https://example.com/b)。 ^new",
                    "另见[新来源][src-b]。",
                    "[src-b]: https://example.com/ref-b",
                    "裸地址是https://example.com/plain-b",
                    "状态是`FINAL`。",
                    "> “原始引文乙。”",
                    "> 普通块引用乙。",
                    "> [!quote]",
                    "> 引用型callout乙。",
                    "> [!note] 新说明",
                    "```python",
                    "value = 2",
                    "```",
                ]
            )
            + "\n"
        )

        completed, payload = self.run_lint_json(
            "--file",
            str(self.note),
            "--all",
            "--baseline",
            str(baseline),
        )

        self.assertEqual(completed.returncode, 1)
        semantic_ids = {
            item["id"]
            for item in payload["findings"]
            if item["category"] == "semantic_invariant"
        }
        self.assertEqual(
            semantic_ids,
            {
                "sentinel_headings",
                "sentinel_frontmatter_titles",
                "sentinel_numbers",
                "sentinel_display_formulas",
                "sentinel_inline_formulas",
                "sentinel_wikilinks",
                "sentinel_markdown_links",
                "sentinel_reference_links",
                "sentinel_reference_link_definitions",
                "sentinel_bare_urls",
                "sentinel_block_ids",
                "sentinel_inline_code_and_labels",
                "sentinel_inline_quotations",
                "sentinel_callout_markers",
                "sentinel_source_quotes",
                "sentinel_fenced_code_blocks",
                "sentinel_epistemic_tokens",
            },
        )
        self.assertTrue(
            all(
                item["confidence"] == "high"
                for item in payload["findings"]
                if item["category"] == "semantic_invariant"
            )
        )

        self.note.write_bytes(baseline.read_bytes())
        completed, payload = self.run_lint_json(
            "--file",
            str(self.note),
            "--all",
            "--baseline",
            str(baseline),
        )
        semantic = [item for item in payload["findings"] if item["category"] == "semantic_invariant"]
        self.assertEqual(semantic, [])

    def test_article_lint_enforces_compact_formula_formatting(self) -> None:
        self.write_note(
            "\n".join(
                [
                    "若 $x\\in[0,1)$ 满足条件。",
                    "这里写成$ x $也不行。",
                    "令 \\(x=1\\)。",
                    "于是：",
                    "",
                    "$$x+y=z$$",
                    "",
                    "当条件成立。",
                    "正确写法是若$x\\in[0,1)$满足条件。",
                    "$$\\begin{aligned}a&=b+c\\\\&=d\\end{aligned}$$",
                ]
            )
            + "\n"
        )

        completed = self.run_lint("--file", str(self.note), "--all")

        self.assertEqual(completed.returncode, 1)
        self.assertIn("cjk_inline_formula_spacing", completed.stdout)
        self.assertIn("loose_inline_formula_inner_space", completed.stdout)
        self.assertIn("non_dollar_formula_delimiter", completed.stdout)
        self.assertIn("loose_short_display_formula", completed.stdout)
        self.assertNotIn("line 9", completed.stdout)
        self.assertNotIn("line 10", completed.stdout)


if __name__ == "__main__":
    unittest.main()
