from __future__ import annotations

from contextlib import closing
import hashlib
import os
from pathlib import Path
import json
import sqlite3
import sys
import tempfile
from os.path import realpath as _realpath

# The platform temporary root can be a symlink (for example on macOS); resolve
# it so synthetic knowledge roots match the resolved paths the code compares.
tempfile.tempdir = _realpath(tempfile.gettempdir())
import unittest
from unittest import mock


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from manage_personal_knowledge import question_collections, questions  # noqa: E402


def _database_fingerprint(path: Path) -> tuple[str, tuple[tuple[str, str], ...]]:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with closing(sqlite3.connect(path)) as connection:
        schema = tuple(connection.execute(
            "SELECT type,name FROM sqlite_master ORDER BY type,name"
        ).fetchall())
    return digest, schema


class QuestionDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.vault = self.base / "Vault"
        self.source = self.vault / "题库"
        self.source.mkdir(parents=True)
        self.database = self.base / "state" / "questions.sqlite3"
        (self.source / "2024算法测试卷.md").write_text(
            "# 2024算法测试卷\n\n## 选择题\n\n### 1\n\n对长度为 $n$ 的数组做归并排序，说明最坏情况比较次数为 $O(n\\log n)$。\n\n### 2\n\n给出 Dijkstra 算法在二叉堆实现下的时间复杂度。\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_index_and_search_preserve_markdown_and_locators(self) -> None:
        indexed = questions.index_questions(
            self.vault,
            self.database,
            source_relative="题库",
            knowledge_root_id="KBROOT-AAAAAAAAAAAAAAAAAAAAAAAAAA",
        )
        self.assertTrue(indexed["ok"])
        self.assertEqual(indexed["questions_accounted"], 2)
        result = questions.search_questions(
            self.database,
            "归并排序 比较次数",
            knowledge_root_id="KBROOT-AAAAAAAAAAAAAAAAAAAAAAAAAA",
        )
        self.assertEqual(result["status"], "candidate_hit")
        self.assertIn("$O(n\\log n)$", result["results"][0]["content_md"])
        self.assertEqual(result["results"][0]["subject"], "cs-ai")
        self.assertEqual(result["results"][0]["question_number"], "1")
        self.assertEqual(result["fallback_recommended"], "none")

    def test_complete_miss_recommends_fallback_without_claiming_absence(self) -> None:
        questions.index_questions(
            self.vault,
            self.database,
            source_relative="题库",
            knowledge_root_id="KBROOT-AAAAAAAAAAAAAAAAAAAAAAAAAA",
        )
        result = questions.search_questions(
            self.database,
            "区块链共识",
            knowledge_root_id="KBROOT-AAAAAAAAAAAAAAAAAAAAAAAAAA",
        )
        self.assertEqual(result["status"], "not_found_in_indexed_questions")
        self.assertEqual(result["fallback_recommended"], "vault_or_pdf")

    def test_missing_index_is_coverage_gap(self) -> None:
        result = questions.search_questions(self.database, "任意题目")
        self.assertEqual(result["status"], "coverage_gap")
        self.assertEqual(result["stop_reason"], "question_index_missing")

    def test_source_cannot_escape_vault_or_be_whole_vault(self) -> None:
        with self.assertRaisesRegex(ValueError, "escapes"):
            questions.index_questions(
                self.vault,
                self.database,
                source_relative="../outside",
                knowledge_root_id="KBROOT-AAAAAAAAAAAAAAAAAAAAAAAAAA",
            )
        with self.assertRaisesRegex(ValueError, "entire Vault"):
            questions.index_questions(
                self.vault,
                self.database,
                source_relative=".",
                knowledge_root_id="KBROOT-AAAAAAAAAAAAAAAAAAAAAAAAAA",
            )

    def test_vault_import_requires_matching_preview_hash(self) -> None:
        plan = questions.guarded_index_questions(
            self.vault,
            self.database,
            source_relative="题库",
            knowledge_root_id="KBROOT-AAAAAAAAAAAAAAAAAAAAAAAAAA",
        )
        self.assertEqual(plan["status"], "import_preview")
        with self.assertRaisesRegex(ValueError, "expect-plan-sha256"):
            questions.guarded_index_questions(
                self.vault,
                self.database,
                source_relative="题库",
                knowledge_root_id="KBROOT-AAAAAAAAAAAAAAAAAAAAAAAAAA",
                write=True,
                expect_plan_sha256="0" * 64,
            )
        applied = questions.guarded_index_questions(
            self.vault,
            self.database,
            source_relative="题库",
            knowledge_root_id="KBROOT-AAAAAAAAAAAAAAAAAAAAAAAAAA",
            write=True,
            expect_plan_sha256=plan["plan_sha256"],
        )
        self.assertTrue(applied["applied"])
        self.assertEqual(applied["questions_accounted"], 2)

    def test_vault_import_writes_the_bytes_bound_after_preview(self) -> None:
        plan = questions.guarded_index_questions(
            self.vault,
            self.database,
            source_relative="题库",
            knowledge_root_id="KBROOT-AAAAAAAAAAAAAAAAAAAAAAAAAA",
        )
        original_connect = questions._connect

        def mutate_then_connect(path: Path):
            (self.source / "2024算法测试卷.md").write_text(
                "# changed\n\n### 1\n\nnew token\n\n### 2\n\nnew token two\n\n### 3\n\nnew token three\n",
                encoding="utf-8",
            )
            return original_connect(path)

        with mock.patch.object(questions, "_connect", side_effect=mutate_then_connect):
            applied = questions.guarded_index_questions(
                self.vault,
                self.database,
                source_relative="题库",
                knowledge_root_id="KBROOT-AAAAAAAAAAAAAAAAAAAAAAAAAA",
                write=True,
                expect_plan_sha256=plan["plan_sha256"],
            )
        self.assertEqual(applied["question_count"], 2)
        self.assertEqual(applied["questions_accounted"], 2)
        old = questions.search_questions(
            self.database, "归并排序 比较次数", knowledge_root_id="KBROOT-AAAAAAAAAAAAAAAAAAAAAAAAAA"
        )
        new = questions.search_questions(
            self.database, "new token", knowledge_root_id="KBROOT-AAAAAAAAAAAAAAAAAAAAAAAAAA"
        )
        self.assertEqual(old["status"], "candidate_hit")
        self.assertEqual(new["status"], "not_found_in_indexed_questions")

    def test_intermediate_vault_reparse_component_is_rejected(self) -> None:
        with mock.patch.object(
            questions,
            "_is_reparse",
            side_effect=lambda path: path == self.source,
        ):
            with self.assertRaisesRegex(ValueError, "reparse"):
                questions.plan_question_index(
                    self.vault,
                    self.database,
                    source_relative="题库",
                    knowledge_root_id="KBROOT-AAAAAAAAAAAAAAAAAAAAAAAAAA",
                )

    @unittest.skipIf(os.name == "nt", "Creating test symlinks is not reliably permitted on Windows")
    def test_source_symlink_is_rejected(self) -> None:
        outside = self.base / "outside"
        outside.mkdir()
        (self.vault / "linked").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "real directory|symlink or reparse point"):
            questions.index_questions(
                self.vault,
                self.database,
                source_relative="linked",
                knowledge_root_id="KBROOT-AAAAAAAAAAAAAAAAAAAAAAAAAA",
            )


class QuestionCollectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "知识根"
        self.vault = self.root / "Vault"
        (self.vault / ".obsidian").mkdir(parents=True)
        self.collection = self.root / "数据" / "题库" / "机器学习" / "测试题集"
        (self.collection / "work").mkdir(parents=True)
        (self.collection / "测试题集.md").write_text(
            "# 测试题集\n\n## 优化\n\n### 1\n\n设损失 $L(\\theta)$ 为 $\\beta$-光滑，给出梯度下降的步长条件。\n\n"
            "### 2\n\n解释学习率预热如何缓解训练初期的不稳定。\n",
            encoding="utf-8",
        )
        (self.collection / "source-manifest.json").write_text(
            json.dumps({"title": "测试题集"}, ensure_ascii=False), encoding="utf-8"
        )
        (self.collection / "work" / "receipt.json").write_text(
            json.dumps(
                {"status": "verified", "problem_count": 2, "verified_count": 2},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.database = Path(self.temporary.name) / "state" / "questions.sqlite3"
        self.root_id = "KBROOT-AAAAAAAAAAAAAAAAAAAAAAAAAA"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_external_collection_import_search_and_coverage(self) -> None:
        relative = self.collection.relative_to(self.root).as_posix()
        plan = question_collections.import_collection(
            self.root,
            self.database,
            source_relative=relative,
            knowledge_root_id=self.root_id,
        )
        self.assertEqual(plan["question_count"], 2)
        self.assertEqual(plan["verification_status"], "verified")
        with self.assertRaisesRegex(ValueError, "expect-plan-sha256"):
            question_collections.import_collection(
                self.root,
                self.database,
                source_relative=relative,
                knowledge_root_id=self.root_id,
                write=True,
                expect_plan_sha256="f" * 64,
            )
        applied = question_collections.import_collection(
            self.root,
            self.database,
            source_relative=relative,
            knowledge_root_id=self.root_id,
            write=True,
            expect_plan_sha256=plan["plan_sha256"],
        )
        self.assertTrue(applied["applied"])
        results = question_collections.search_collections(
            self.database,
            "学习率 预热",
            knowledge_root_id=self.root_id,
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["question_number"], "2")
        status = question_collections.collection_status(
            self.database, self.root, knowledge_root_id=self.root_id
        )
        self.assertTrue(status["coverage_complete"])
        self.assertEqual(status["discovered_unimported"], [])

    def test_external_first_database_keeps_legacy_adapter_queryable(self) -> None:
        relative = self.collection.relative_to(self.root).as_posix()
        plan = question_collections.import_collection(
            self.root, self.database, source_relative=relative, knowledge_root_id=self.root_id
        )
        question_collections.import_collection(
            self.root,
            self.database,
            source_relative=relative,
            knowledge_root_id=self.root_id,
            write=True,
            expect_plan_sha256=plan["plan_sha256"],
        )
        status = questions.question_status(self.database, knowledge_root_id=self.root_id)
        self.assertEqual(status["status"], "not_initialized")
        self.assertFalse(status["schema_initialized"])
        search = questions.search_questions(
            self.database, "任意题目", knowledge_root_id=self.root_id
        )
        self.assertEqual(search["status"], "coverage_gap")

    def test_external_import_rejects_legacy_database_from_another_root(self) -> None:
        vault_questions = self.vault / "题库"
        vault_questions.mkdir()
        (vault_questions / "测试.md").write_text("# 测试\n\n### 1\n\n题目。\n", encoding="utf-8")
        questions.index_questions(
            self.vault,
            self.database,
            source_relative="题库",
            knowledge_root_id=self.root_id,
        )
        relative = self.collection.relative_to(self.root).as_posix()
        other_root_id = "KBROOT-BBBBBBBBBBBBBBBBBBBBBBBBBB"
        plan = question_collections.import_collection(
            self.root, self.database, source_relative=relative, knowledge_root_id=other_root_id
        )
        before = _database_fingerprint(self.database)
        with self.assertRaisesRegex(RuntimeError, "different knowledge root"):
            question_collections.import_collection(
                self.root,
                self.database,
                source_relative=relative,
                knowledge_root_id=other_root_id,
                write=True,
                expect_plan_sha256=plan["plan_sha256"],
            )
        self.assertEqual(_database_fingerprint(self.database), before)

    def test_legacy_import_rejects_external_database_from_another_root(self) -> None:
        relative = self.collection.relative_to(self.root).as_posix()
        plan = question_collections.import_collection(
            self.root, self.database, source_relative=relative, knowledge_root_id=self.root_id
        )
        question_collections.import_collection(
            self.root,
            self.database,
            source_relative=relative,
            knowledge_root_id=self.root_id,
            write=True,
            expect_plan_sha256=plan["plan_sha256"],
        )
        vault_questions = self.vault / "题库"
        vault_questions.mkdir()
        (vault_questions / "测试.md").write_text("# 测试\n\n### 1\n\n题目。\n", encoding="utf-8")
        before = _database_fingerprint(self.database)
        with self.assertRaisesRegex(RuntimeError, "different knowledge root"):
            questions.index_questions(
                self.vault,
                self.database,
                source_relative="题库",
                knowledge_root_id="KBROOT-BBBBBBBBBBBBBBBBBBBBBBBBBB",
            )
        self.assertEqual(_database_fingerprint(self.database), before)

    def test_external_import_writes_the_bytes_bound_after_preview(self) -> None:
        source_file = self.collection / "测试题集.md"
        source_file.write_text("# 测试题集\n\n### 1\n\nold token\n", encoding="utf-8")
        relative = self.collection.relative_to(self.root).as_posix()
        plan = question_collections.import_collection(
            self.root, self.database, source_relative=relative, knowledge_root_id=self.root_id
        )
        original_connect = question_collections._connect

        def mutate_then_connect(path: Path):
            source_file.write_text(
                "# changed\n\n### 1\n\nnew token\n\n### 2\n\nnew token two\n",
                encoding="utf-8",
            )
            return original_connect(path)

        with mock.patch.object(question_collections, "_connect", side_effect=mutate_then_connect):
            applied = question_collections.import_collection(
                self.root,
                self.database,
                source_relative=relative,
                knowledge_root_id=self.root_id,
                write=True,
                expect_plan_sha256=plan["plan_sha256"],
            )
        self.assertEqual(applied["question_count"], 1)
        old = question_collections.search_collections(
            self.database, "old token", knowledge_root_id=self.root_id
        )
        new = question_collections.search_collections(
            self.database, "new token", knowledge_root_id=self.root_id
        )
        self.assertEqual(len(old), 1)
        self.assertEqual(new, [])

    def test_intermediate_external_reparse_component_is_rejected(self) -> None:
        relative = self.collection.relative_to(self.root).as_posix()
        reparse_component = self.root / "数据"
        with mock.patch.object(
            question_collections,
            "_is_reparse",
            side_effect=lambda path: path == reparse_component,
        ):
            with self.assertRaisesRegex(ValueError, "reparse"):
                question_collections.plan_import(
                    self.root,
                    self.database,
                    source_relative=relative,
                    knowledge_root_id=self.root_id,
                )

    def test_discovered_unimported_collection_forces_coverage_gap(self) -> None:
        other = self.root / "数据" / "题库" / "数据结构" / "未导入题集"
        (other / "work").mkdir(parents=True)
        (other / "未导入题集.md").write_text("## 例题\n\n### 1\n\n实现一个栈。\n", encoding="utf-8")
        (other / "source-manifest.json").write_text("{}", encoding="utf-8")
        status = question_collections.collection_status(
            self.database, self.root, knowledge_root_id=self.root_id
        )
        self.assertEqual(status["status"], "coverage_gap")
        self.assertEqual(len(status["discovered_unimported"]), 2)

    def test_external_source_escape_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "knowledge-root-relative"):
            question_collections.plan_import(
                self.root,
                self.database,
                source_relative="../outside",
                knowledge_root_id=self.root_id,
            )


if __name__ == "__main__":
    unittest.main()
