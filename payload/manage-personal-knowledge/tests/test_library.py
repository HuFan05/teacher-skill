from __future__ import annotations

import hashlib
import json
from pathlib import Path
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
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from manage_personal_knowledge.library import (  # noqa: E402
    ExtractionResult,
    apply_indexed_pdf_move,
    get_page,
    indexed_pdf_priority_paths,
    index_library,
    library_status,
    plan_indexed_pdf_move,
    preview_library_inventory_refresh,
    refresh_library_inventory,
    search_library,
)
from manage_personal_knowledge.text import (  # noqa: E402
    PdfTextExtractionError,
    SEARCH_FORMAT_VERSION,
    extract_pdf_text,
    search_tokens,
)


def _write_fake_pdf(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _source_digest(path: Path) -> tuple[int, int, str]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest()


class PdfTextExtractionTests(unittest.TestCase):
    def test_nonzero_pdftotext_exit_is_never_indexed_as_text(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "input.pdf"
            source.write_bytes(b"%PDF-test")
            failed = mock.Mock(
                returncode=1,
                stdout="diagnostic text accidentally written to stdout",
                stderr="pdftotext failed",
            )
            with mock.patch(
                "manage_personal_knowledge.text.subprocess.run",
                return_value=failed,
            ):
                with self.assertRaisesRegex(PdfTextExtractionError, "pdftotext failed"):
                    extract_pdf_text(
                        source,
                        pdftotext_exe="pdftotext",
                        temp_root=root / "ascii-temp",
                    )


class LibraryIndexTests(unittest.TestCase):
    ROOT_ID = "KBROOT-" + ("A" * 26)
    OTHER_ROOT_ID = "KBROOT-" + ("B" * 26)

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "数字图书馆"
        self.root.mkdir()
        self.db = Path(self.temp.name) / "state" / "library.sqlite3"

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def text_extractor(path: Path) -> ExtractionResult:
        source = path.read_text(encoding="utf-8")
        return ExtractionResult(source, method="test-extractor")

    def test_resume_max_files_and_unchanged_skip(self) -> None:
        for name in ("alpha.pdf", "beta.pdf", "gamma.pdf"):
            _write_fake_pdf(self.root / name, f"text from {name}\fsecond page")

        calls: list[str] = []

        def extractor(path: Path) -> str:
            calls.append(path.name)
            return path.read_text(encoding="utf-8")

        first = index_library(self.root, self.db, extractor=extractor, max_files=1)
        self.assertEqual(first["selected"], 1)
        self.assertEqual(first["processed"], 1)
        self.assertEqual(first["status"]["status_counts"]["pending"], 2)

        second = index_library(self.root, self.db, extractor=extractor, max_files=1)
        self.assertEqual(second["processed"], 1)
        self.assertEqual(second["status"]["status_counts"]["pending"], 1)

        third = index_library(self.root, self.db, extractor=extractor)
        self.assertEqual(third["processed"], 1)
        self.assertEqual(third["status"]["status_counts"]["indexed"], 3)
        self.assertEqual(len(calls), 3)

        unchanged = index_library(self.root, self.db, extractor=extractor)
        self.assertEqual(unchanged["selected"], 0)
        self.assertEqual(unchanged["processed"], 0)
        self.assertEqual(len(calls), 3)
        self.assertEqual(unchanged["journal_mode"], "delete")

    def test_search_format_upgrade_rebuilds_fts_without_pdf_reextraction(self) -> None:
        source = self.root / "paper.pdf"
        pages = ["Nyström θ ← θ − η∇L(θ), arg max, and √d_k"] + [
            f"bounded migration page {number}" for number in range(1, 261)
        ]
        _write_fake_pdf(source, "\f".join(pages))
        index_library(self.root, self.db, extractor=self.text_extractor)
        connection = sqlite3.connect(self.db)
        try:
            connection.execute(
                "UPDATE library_meta SET value = 'stale-search-format' "
                "WHERE key = 'search_format_version'"
            )
            connection.execute("DELETE FROM pdf_page_fts")
            page_rows = connection.execute("SELECT id FROM pdf_pages").fetchall()
            connection.executemany(
                "INSERT INTO pdf_page_fts(page_id, search_text, title_text, path_text, page_text) "
                "VALUES (?, 'legacy tokens', 'legacy', 'legacy', 'legacy')",
                page_rows,
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaisesRegex(RuntimeError, "index --resume"):
            search_library(self.db, "nystrom")

        def forbidden_extractor(path: Path) -> str:
            raise AssertionError(f"unexpected PDF extraction: {path}")

        upgraded = index_library(self.root, self.db, extractor=forbidden_extractor)
        self.assertEqual(upgraded["processed"], 0)
        self.assertTrue(upgraded["search_format_upgrade"]["changed"])
        self.assertEqual(upgraded["search_format_upgrade"]["rebuilt_pages"], 261)
        self.assertTrue(upgraded["search_format_upgrade"]["preserves_extracted_text"])
        self.assertEqual(
            upgraded["status"]["search_format_version"], SEARCH_FORMAT_VERSION
        )
        result = search_library(
            self.db,
            "nystrom theta eta nabla argmax sqrt",
        )
        self.assertEqual(result["results"][0]["path"], "paper.pdf")

    def test_search_format_upgrade_rolls_back_on_normalizer_failure(self) -> None:
        source = self.root / "paper.pdf"
        _write_fake_pdf(source, "Nyström θ")
        index_library(self.root, self.db, extractor=self.text_extractor)
        connection = sqlite3.connect(self.db)
        try:
            connection.execute(
                "UPDATE library_meta SET value = 'stale-search-format' "
                "WHERE key = 'search_format_version'"
            )
            before = connection.execute(
                "SELECT COUNT(*) FROM pdf_page_fts"
            ).fetchone()[0]
            connection.commit()
        finally:
            connection.close()

        with mock.patch(
            "manage_personal_knowledge.library.make_search_text",
            side_effect=RuntimeError("synthetic normalizer failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "synthetic normalizer failure"):
                index_library(self.root, self.db, extractor=self.text_extractor)

        connection = sqlite3.connect(self.db)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT value FROM library_meta WHERE key = 'search_format_version'"
                ).fetchone()[0],
                "stale-search-format",
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM pdf_page_fts").fetchone()[0],
                before,
            )
        finally:
            connection.close()

    def test_paper_anchor_index_tokens_cover_unicode_and_ocr_forms(self) -> None:
        contract = json.loads(
            (SKILL_ROOT / "references" / "paper-anchor-contract-v1.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(contract["contract_version"], "ascii-paper-anchor/v1")
        self.assertEqual(
            contract["query_canonicalizer_version"], "paper-canonical/v1"
        )
        self.assertNotEqual(SEARCH_FORMAT_VERSION, contract["query_canonicalizer_version"])
        for vector in contract["vectors"]:
            with self.subTest(vector=vector["id"]):
                tokens = set(search_tokens(vector["input"]))
                self.assertTrue(set(vector["required_tokens"]).issubset(tokens))
        extra_tokens = set(search_tokens(r"\arg\max_θ, x != y, \infty, 渭, 蠁, 蟽, 蟺"))
        self.assertTrue(
            {"argmax", "theta", "neq", "infinity", "mu", "phi", "sigma", "pi"}.issubset(
                extra_tokens
            )
        )
        self.assertNotIn("zeta", set(search_tokens("这意味着")))

    def test_library_identity_arguments_are_paired_and_validated(self) -> None:
        with self.assertRaises(ValueError):
            library_status(self.db, self.root, knowledge_root_id=self.ROOT_ID)
        with self.assertRaises(ValueError):
            library_status(
                self.db,
                self.root,
                knowledge_root_id="KBROOT-invalid",
                library_relative_path="数字图书馆",
            )
        with self.assertRaises(ValueError):
            library_status(
                self.db,
                self.root,
                knowledge_root_id=self.ROOT_ID,
                library_relative_path="../outside",
            )

    def test_changed_reindexed_deleted_pruned_and_fts_cleaned(self) -> None:
        keep = self.root / "keep.pdf"
        remove = self.root / "remove.pdf"
        _write_fake_pdf(keep, "obsoletephrase")
        _write_fake_pdf(remove, "deletedphrase")
        index_library(self.root, self.db, extractor=self.text_extractor)

        _write_fake_pdf(keep, "replacement phrase with a different file size")
        remove.unlink()
        result = index_library(self.root, self.db, extractor=self.text_extractor)

        self.assertEqual(result["inventory"]["changed"], 1)
        self.assertEqual(result["inventory"]["pruned"], 1)
        self.assertEqual(result["status"]["docs"], 1)
        self.assertFalse(search_library(self.db, "obsoletephrase")["results"])
        self.assertFalse(search_library(self.db, "deletedphrase")["results"])
        self.assertEqual(search_library(self.db, "replacement")["results"][0]["path"], "keep.pdf")

        con = sqlite3.connect(self.db)
        try:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM pdf_page_fts").fetchone()[0], 1)
        finally:
            con.close()

    def test_no_text_and_error_are_visible_in_coverage(self) -> None:
        _write_fake_pdf(self.root / "empty.pdf", "empty marker")
        _write_fake_pdf(self.root / "broken.pdf", "error marker")

        def extractor(path: Path) -> str:
            if path.name == "broken.pdf":
                raise RuntimeError("synthetic extraction failure")
            return "\f"

        result = index_library(self.root, self.db, extractor=extractor)
        counts = result["status"]["status_counts"]
        self.assertEqual(counts["no_text"], 1)
        self.assertEqual(counts["error"], 1)
        self.assertEqual(result["no_text"], 1)
        self.assertEqual(result["errors"], 1)
        self.assertEqual(result["remaining"], 0)

        resumed = index_library(self.root, self.db, extractor=extractor)
        self.assertEqual(resumed["selected"], 0)
        retried = index_library(self.root, self.db, extractor=extractor, retry_errors=True)
        self.assertEqual(retried["selected"], 1)

        search = search_library(self.db, "anything")
        self.assertTrue(search["diagnostics"]["incomplete"])
        self.assertIn("conclusive", search["diagnostics"]["warning"])

    def test_chinese_english_alias_search_and_page_lookup(self) -> None:
        chinese = self.root / "人工智能导论.pdf"
        english = self.root / "computing" / "ml-handbook.pdf"
        _write_fake_pdf(chinese, "前言\f本章讨论人工智能与本地知识管理。")
        _write_fake_pdf(english, "overview\fmachine learning systems and neural networks")
        index_library(self.root, self.db, extractor=self.text_extractor)

        zh = search_library(self.db, "人工智能")
        self.assertEqual(zh["results"][0]["path"], "人工智能导论.pdf")
        self.assertEqual(zh["results"][0]["page_number"], 2)

        en = search_library(self.db, "neural nets", aliases=["machine learning"])
        self.assertEqual(en["results"][0]["path"], "computing/ml-handbook.pdf")
        self.assertIn("machine learning", en["diagnostics"]["matched_aliases"])
        self.assertIn("neural nets", en["diagnostics"]["unmatched_aliases"])

        page = get_page(self.db, "人工智能导论.pdf", 2)
        self.assertEqual(page["page_number"], 2)
        self.assertIn("本地知识管理", page["content"])
        self.assertEqual(page["method"], "test-extractor")

    def test_search_snippet_is_centered_on_a_late_match(self) -> None:
        source = self.root / "late-match.pdf"
        _write_fake_pdf(source, ("unrelated preface " * 80) + "needle-at-the-end")
        index_library(self.root, self.db, extractor=self.text_extractor)

        result = search_library(self.db, "needle-at-the-end")["results"][0]
        self.assertIn("needle-at-the-end", result["snippet"])
        self.assertTrue(result["snippet"].startswith("…"))

    def test_source_pdf_size_mtime_and_hash_do_not_change(self) -> None:
        source = self.root / "只读资料.pdf"
        _write_fake_pdf(source, "page one\fpage two")
        before = _source_digest(source)
        index_library(self.root, self.db, extractor=self.text_extractor, batch_size=1)
        after = _source_digest(source)
        self.assertEqual(after, before)

    def test_schema_remains_pdf_paper_search_compatible(self) -> None:
        _write_fake_pdf(self.root / "paper.pdf", "Bellman optimality equation")
        index_library(self.root, self.db, extractor=self.text_extractor)
        con = sqlite3.connect(self.db)
        try:
            doc_columns = {row[1] for row in con.execute("PRAGMA table_info(pdf_docs)")}
            page_columns = {row[1] for row in con.execute("PRAGMA table_info(pdf_pages)")}
            self.assertTrue(
                {
                    "id",
                    "path",
                    "path_key",
                    "title",
                    "title_norm",
                    "page_count",
                    "extracted_page_count",
                    "extraction_warning",
                }.issubset(doc_columns)
            )
            self.assertTrue({"id", "doc_id", "page_number", "content", "snippet", "char_count"}.issubset(page_columns))
            row = con.execute(
                """
                SELECT d.title, d.path, p.page_number, p.snippet, p.content,
                       bm25(pdf_page_fts, 3.0, 2.5, 0.5, 1.0)
                FROM pdf_page_fts
                JOIN pdf_pages p ON p.id = CAST(pdf_page_fts.page_id AS INTEGER)
                JOIN pdf_docs d ON d.id = p.doc_id
                WHERE pdf_page_fts MATCH 'bellman'
                """
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[1], "paper.pdf")
        finally:
            con.close()

    def test_legacy_database_adds_status_before_creating_its_index(self) -> None:
        _write_fake_pdf(self.root / "legacy.pdf", "legacy schema text")
        self.db.parent.mkdir(parents=True)
        con = sqlite3.connect(self.db)
        try:
            con.execute(
                """
                CREATE TABLE pdf_docs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL UNIQUE,
                    path_key TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    title_norm TEXT NOT NULL,
                    page_count INTEGER NOT NULL DEFAULT 0,
                    extracted_page_count INTEGER NOT NULL DEFAULT 0,
                    extraction_warning TEXT
                )
                """
            )
            con.execute(
                """
                INSERT INTO pdf_docs(
                    path, path_key, title, title_norm, page_count,
                    extracted_page_count, extraction_warning
                ) VALUES ('legacy.pdf', 'legacy', 'legacy', 'legacy', 0, 0, NULL)
                """
            )
            con.commit()
        finally:
            con.close()

        rebuilt = index_library(
            self.root,
            self.db,
            knowledge_root_id=self.ROOT_ID,
            library_relative_path="数字图书馆",
            extractor=self.text_extractor,
        )
        self.assertEqual(rebuilt["processed"], 1)
        con = sqlite3.connect(self.db)
        try:
            columns = {row[1] for row in con.execute("PRAGMA table_info(pdf_docs)")}
            indexes = {row[1] for row in con.execute("PRAGMA index_list(pdf_docs)")}
        finally:
            con.close()
        self.assertIn("status", columns)
        self.assertIn("idx_pdf_docs_status", indexes)
        self.assertEqual(
            search_library(self.db, "legacy schema text")["results"][0]["path"],
            "legacy.pdf",
        )

    def test_failed_scan_does_not_prune_existing_documents(self) -> None:
        source = self.root / "book.pdf"
        _write_fake_pdf(source, "retained text")
        index_library(self.root, self.db, extractor=self.text_extractor)

        from manage_personal_knowledge.library import ScanResult, inventory_library, connect_library

        con = connect_library(self.db)
        try:
            stats = inventory_library(
                con,
                self.root,
                scan=ScanResult(files=[], errors=["synthetic scan failure"], success=False),
            )
        finally:
            con.close()
        self.assertEqual(stats["pruned"], 0)
        self.assertEqual(library_status(self.db)["docs"], 1)

    def test_relinked_root_invalidates_old_index(self) -> None:
        _write_fake_pdf(self.root / "old.pdf", "old library text")
        index_library(self.root, self.db, extractor=self.text_extractor)
        replacement = Path(self.temp.name) / "replacement-library"
        replacement.mkdir()
        _write_fake_pdf(replacement / "new.pdf", "new library text")

        before = library_status(self.db, replacement)
        self.assertFalse(before["root_matches_config"])
        rebuilt = index_library(replacement, self.db, extractor=self.text_extractor)
        self.assertTrue(rebuilt["inventory"]["root_changed"])
        self.assertFalse(search_library(self.db, "old library text")["results"])
        self.assertEqual(search_library(self.db, "new library text")["results"][0]["path"], "new.pdf")
        self.assertTrue(library_status(self.db, replacement)["root_matches_config"])

    def test_stable_root_identity_reuses_index_after_absolute_root_move(self) -> None:
        source = self.root / "book.pdf"
        _write_fake_pdf(source, "portable indexed text")
        calls: list[Path] = []

        def extractor(path: Path) -> ExtractionResult:
            calls.append(path)
            return self.text_extractor(path)

        first = index_library(
            self.root,
            self.db,
            knowledge_root_id=self.ROOT_ID,
            library_relative_path="数字图书馆",
            extractor=extractor,
        )
        self.assertEqual(first["processed"], 1)

        moved = Path(self.temp.name) / "moved" / "数字图书馆"
        moved.parent.mkdir()
        self.root.rename(moved)
        before_refresh = library_status(
            self.db,
            moved,
            knowledge_root_id=self.ROOT_ID,
            library_relative_path="数字图书馆",
        )
        self.assertTrue(before_refresh["root_matches_config"])
        self.assertFalse(before_refresh["root_path_matches_config"])
        self.assertTrue(before_refresh["identity_matches_config"])

        refreshed = index_library(
            moved,
            self.db,
            knowledge_root_id=self.ROOT_ID,
            library_relative_path="数字图书馆",
            extractor=extractor,
        )
        self.assertTrue(refreshed["inventory"]["root_changed"])
        self.assertTrue(refreshed["inventory"]["identity_matches"])
        self.assertTrue(refreshed["inventory"]["index_reused_after_root_move"])
        self.assertEqual(refreshed["processed"], 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            search_library(self.db, "portable indexed text")["results"][0]["path"],
            "book.pdf",
        )

    def test_targeted_pdf_rename_preserves_pages_and_rebuilds_fts_path(self) -> None:
        source = self.root / "old-name.pdf"
        _write_fake_pdf(source, "stable searchable body")
        index_library(
            self.root,
            self.db,
            knowledge_root_id=self.ROOT_ID,
            library_relative_path="数字图书馆",
            extractor=self.text_extractor,
        )
        target = self.root / "nested" / "new-name.pdf"
        target.parent.mkdir()
        source.rename(target)
        preview = plan_indexed_pdf_move(
            self.db,
            self.root,
            "数字图书馆/old-name.pdf",
            "数字图书馆/nested/new-name.pdf",
            knowledge_root_id=self.ROOT_ID,
            library_relative_path="数字图书馆",
        )
        self.assertEqual(preview["action"], "rename_preserve_text")
        result = apply_indexed_pdf_move(
            self.db,
            self.root,
            "数字图书馆/old-name.pdf",
            "数字图书馆/nested/new-name.pdf",
            knowledge_root_id=self.ROOT_ID,
            library_relative_path="数字图书馆",
            expected_plan=preview,
        )
        self.assertTrue(result["preserves_extracted_text"])
        self.assertEqual(
            search_library(self.db, "stable searchable body")["results"][0]["path"],
            "nested/new-name.pdf",
        )
        self.assertEqual(
            search_library(self.db, "new-name")["results"][0]["path"],
            "nested/new-name.pdf",
        )
        page = get_page(self.db, "nested/new-name.pdf", 1)
        self.assertEqual(page["method"], "test-extractor")
        with self.assertRaises(LookupError):
            get_page(self.db, "old-name.pdf", 1)

    def test_targeted_move_out_removes_index_and_move_in_adds_pending(self) -> None:
        source = self.root / "indexed.pdf"
        _write_fake_pdf(source, "indexed body")
        index_library(
            self.root,
            self.db,
            knowledge_root_id=self.ROOT_ID,
            library_relative_path="数字图书馆",
            extractor=self.text_extractor,
        )
        outside = Path(self.temp.name) / "Archive" / "indexed.pdf"
        outside.parent.mkdir()
        source.rename(outside)
        removed_plan = plan_indexed_pdf_move(
            self.db,
            self.root,
            "数字图书馆/indexed.pdf",
            "Archive/indexed.pdf",
            knowledge_root_id=self.ROOT_ID,
            library_relative_path="数字图书馆",
        )
        removed = apply_indexed_pdf_move(
            self.db,
            self.root,
            "数字图书馆/indexed.pdf",
            "Archive/indexed.pdf",
            knowledge_root_id=self.ROOT_ID,
            library_relative_path="数字图书馆",
            expected_plan=removed_plan,
        )
        self.assertEqual(removed["action"], "remove_from_index")
        self.assertEqual(library_status(self.db)["docs"], 0)

        incoming = Path(self.temp.name) / "Incoming" / "fresh.pdf"
        _write_fake_pdf(incoming, "not extracted yet")
        target = self.root / "fresh.pdf"
        incoming.rename(target)
        added_plan = plan_indexed_pdf_move(
            self.db,
            self.root,
            "Incoming/fresh.pdf",
            "数字图书馆/fresh.pdf",
            knowledge_root_id=self.ROOT_ID,
            library_relative_path="数字图书馆",
        )
        added = apply_indexed_pdf_move(
            self.db,
            self.root,
            "Incoming/fresh.pdf",
            "数字图书馆/fresh.pdf",
            knowledge_root_id=self.ROOT_ID,
            library_relative_path="数字图书馆",
            expected_plan=added_plan,
        )
        self.assertEqual(added["action"], "add_pending")
        self.assertEqual(library_status(self.db)["status_counts"]["pending"], 1)
        self.assertFalse(search_library(self.db, "not extracted yet")["results"])

    def test_metadata_only_root_refresh_reuses_extracted_pages(self) -> None:
        source = self.root / "portable.pdf"
        _write_fake_pdf(source, "no second extraction needed")
        index_library(
            self.root,
            self.db,
            knowledge_root_id=self.ROOT_ID,
            library_relative_path="数字图书馆",
            extractor=self.text_extractor,
        )
        moved = Path(self.temp.name) / "Moved" / "数字图书馆"
        moved.parent.mkdir()
        self.root.rename(moved)
        preview = preview_library_inventory_refresh(
            self.db,
            moved,
            knowledge_root_id=self.ROOT_ID,
            library_relative_path="数字图书馆",
        )
        self.assertTrue(preview["root_changed"])
        self.assertTrue(preview["extracts_pdf_text"] is False)
        refreshed = refresh_library_inventory(
            self.db,
            moved,
            knowledge_root_id=self.ROOT_ID,
            library_relative_path="数字图书馆",
            expected_preview=preview,
        )
        self.assertEqual(refreshed["inventory"]["unchanged"], 1)
        self.assertEqual(
            search_library(self.db, "no second extraction needed")["results"][0]["path"],
            "portable.pdf",
        )
        self.assertTrue(
            library_status(
                self.db,
                moved,
                knowledge_root_id=self.ROOT_ID,
                library_relative_path="数字图书馆",
            )["root_path_matches_config"]
        )

    def test_indexed_pdf_priority_paths_are_root_relative(self) -> None:
        _write_fake_pdf(self.root / "b.pdf", "b")
        _write_fake_pdf(self.root / "nested" / "a.pdf", "a")
        index_library(
            self.root,
            self.db,
            knowledge_root_id=self.ROOT_ID,
            library_relative_path="数字图书馆",
            extractor=self.text_extractor,
        )
        self.assertEqual(
            indexed_pdf_priority_paths(
                self.db,
                self.root,
                knowledge_root_id=self.ROOT_ID,
                library_relative_path="数字图书馆",
            ),
            ["数字图书馆/b.pdf", "数字图书馆/nested/a.pdf"],
        )

    def test_identity_mismatch_invalidates_index_even_at_same_absolute_path(self) -> None:
        _write_fake_pdf(self.root / "book.pdf", "identity-sensitive text")
        calls: list[Path] = []

        def extractor(path: Path) -> ExtractionResult:
            calls.append(path)
            return self.text_extractor(path)

        index_library(
            self.root,
            self.db,
            knowledge_root_id=self.ROOT_ID,
            library_relative_path="数字图书馆",
            extractor=extractor,
        )
        before = library_status(
            self.db,
            self.root,
            knowledge_root_id=self.OTHER_ROOT_ID,
            library_relative_path="数字图书馆",
        )
        self.assertFalse(before["root_matches_config"])
        self.assertTrue(before["root_path_matches_config"])
        self.assertFalse(before["identity_matches_config"])

        rebuilt = index_library(
            self.root,
            self.db,
            knowledge_root_id=self.OTHER_ROOT_ID,
            library_relative_path="数字图书馆",
            extractor=extractor,
        )
        self.assertFalse(rebuilt["inventory"]["root_changed"])
        self.assertFalse(rebuilt["inventory"]["identity_matches"])
        self.assertEqual(rebuilt["processed"], 1)
        self.assertEqual(len(calls), 2)

    def test_document_index_uses_absolute_root_before_identity_adoption(self) -> None:
        _write_fake_pdf(self.root / "legacy.pdf", "legacy index text")
        index_library(self.root, self.db, extractor=self.text_extractor)

        same_root = library_status(
            self.db,
            self.root,
            knowledge_root_id=self.ROOT_ID,
            library_relative_path="数字图书馆",
        )
        self.assertTrue(same_root["root_matches_config"])
        self.assertIsNone(same_root["identity_matches_config"])

        moved = Path(self.temp.name) / "legacy-moved"
        self.root.rename(moved)
        moved_status = library_status(
            self.db,
            moved,
            knowledge_root_id=self.ROOT_ID,
            library_relative_path="数字图书馆",
        )
        self.assertFalse(moved_status["root_matches_config"])
        rebuilt = index_library(
            moved,
            self.db,
            knowledge_root_id=self.ROOT_ID,
            library_relative_path="数字图书馆",
            extractor=self.text_extractor,
        )
        self.assertFalse(rebuilt["inventory"]["index_reused_after_root_move"])
        self.assertEqual(rebuilt["processed"], 1)

    def test_document_index_is_adopted_without_rebuild_at_same_root(self) -> None:
        _write_fake_pdf(self.root / "legacy.pdf", "legacy adoption text")
        index_library(self.root, self.db, extractor=self.text_extractor)

        adopted = index_library(
            self.root,
            self.db,
            knowledge_root_id=self.ROOT_ID,
            library_relative_path=r"数字图书馆",
            extractor=self.text_extractor,
        )
        self.assertEqual(adopted["processed"], 0)
        after = library_status(
            self.db,
            self.root,
            knowledge_root_id=self.ROOT_ID,
            library_relative_path="数字图书馆",
        )
        self.assertTrue(after["identity_matches_config"])
        self.assertEqual(after["knowledge_root_id"], self.ROOT_ID)
        self.assertEqual(after["library_relative_path"], "数字图书馆")


if __name__ == "__main__":
    unittest.main()
