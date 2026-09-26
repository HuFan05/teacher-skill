from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import io
import json
import os
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
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import manage_kb  # noqa: E402
from manage_personal_knowledge import config, library  # noqa: E402


class CliSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.root = self.base / "knowledge"
        self.vault = self.root / "Vault"
        self.library = self.root / "Library"
        (self.vault / ".obsidian").mkdir(parents=True)
        self.library.mkdir(parents=True)
        (self.library / "book.pdf").write_text("searchable content", encoding="utf-8")
        self.config_path = self.base / "config.json"
        self.state = self.base / "state"
        self.environment = mock.patch.dict(
            os.environ,
            {"MPK_CONFIG_PATH": str(self.config_path), "MPK_STATE_DIR": str(self.state)},
        )
        self.environment.start()
        config.configure(
            self.root,
            self.vault,
            self.library,
            confirmed=True,
            config_path=self.config_path,
        )
        library.index_library(
            self.library,
            self.state / "library.sqlite3",
            extractor=lambda path: path.read_text(encoding="utf-8"),
        )

    def tearDown(self) -> None:
        self.environment.stop()
        self.temporary.cleanup()

    def search_args(self) -> argparse.Namespace:
        return argparse.Namespace(
            config=str(self.config_path),
            query="searchable",
            alias=[],
            limit=8,
        )

    def test_search_stops_when_library_disappears(self) -> None:
        self.assertTrue(manage_kb.command_library_search(self.search_args())["results"])
        self.library.rename(self.root / "Moved Library")
        with self.assertRaisesRegex(RuntimeError, "source is unavailable"):
            manage_kb.command_library_search(self.search_args())

    def test_search_stops_after_relink_until_index_is_rebuilt(self) -> None:
        replacement = self.root / "Replacement Library"
        replacement.mkdir()
        config.relink_source(
            "library",
            replacement,
            confirmed=True,
            config_path=self.config_path,
        )
        with self.assertRaisesRegex(RuntimeError, "different library root"):
            manage_kb.command_library_search(self.search_args())

    def test_search_stops_after_library_is_forgotten(self) -> None:
        config.forget_source("library", confirmed=True, config_path=self.config_path)
        with self.assertRaisesRegex(RuntimeError, "source is unavailable"):
            manage_kb.command_library_search(self.search_args())

    def test_paper_json_flag_selects_full_downstream_json(self) -> None:
        args = argparse.Namespace(
            config=str(self.config_path),
            query="Bellman equation",
            alias=[],
            output_mode="compact",
            limit=12,
            json=True,
        )
        with mock.patch.object(
            manage_kb.integrations,
            "run_paper_search",
            return_value={"ok": True, "data": {"results": []}},
        ) as run:
            manage_kb.command_paper_search(args)
        self.assertEqual(run.call_args.kwargs["output_mode"], "json")

    def test_paper_locate_returns_managed_coverage_and_calls_one_integration(self) -> None:
        args = argparse.Namespace(
            config=str(self.config_path),
            query="sorting lower bound",
            alias=["comparison sort"],
            limit=12,
            json=True,
        )
        answer = {
            "schema_version": "paper-locate/v1",
            "canonicalizer_version": "paper-canonical/v1",
            "ok": True,
            "status": "verified_hit",
            "route": "explicit-index",
            "query": {},
            "search": {},
            "results": [{"path": "book.pdf", "pdf_page": 1}],
            "coverage": {"warnings": []},
            "timing_ms": {},
        }
        with mock.patch.object(
            manage_kb.integrations,
            "run_paper_locate",
            return_value=answer,
        ) as run:
            result = manage_kb.command_paper_locate(args)
        run.assert_called_once_with(
            args.query,
            self.state / "library.sqlite3",
            aliases=args.alias,
            limit=12,
        )
        self.assertEqual(result["managed_by"], "manage-personal-knowledge")
        self.assertTrue(result["coverage"]["local_index"]["search_format_current"])

    def test_paper_locate_fails_closed_before_integration_on_old_search_format(self) -> None:
        connection = sqlite3.connect(self.state / "library.sqlite3")
        try:
            connection.execute(
                "UPDATE library_meta SET value = 'stale-search-format' "
                "WHERE key = 'search_format_version'"
            )
            connection.commit()
        finally:
            connection.close()
        args = argparse.Namespace(
            config=str(self.config_path),
            query="sorting lower bound",
            alias=[],
            limit=12,
            json=True,
        )
        with mock.patch.object(manage_kb.integrations, "run_paper_locate") as run:
            result = manage_kb.command_paper_locate(args)
        run.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(
            result["error"]["code"], "search_format_rebuild_required"
        )

    def test_paper_locate_cli_preflight_error_stays_in_stable_status_set(self) -> None:
        missing_config = self.base / "missing-config.json"
        stdout = io.StringIO()
        with (
            mock.patch.object(
                sys,
                "argv",
                [
                    "manage_kb.py",
                    "paper-locate",
                    "--query",
                    "sorting worst case",
                    "--config",
                    str(missing_config),
                    "--json",
                ],
            ),
            redirect_stdout(stdout),
        ):
            exit_code = manage_kb.main()
        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertEqual(payload["schema_version"], "paper-locate/v1")
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["error"]["code"], "missing_configuration")

    def test_paper_locate_preserves_indexed_text_not_found_with_coverage_warning(self) -> None:
        connection = sqlite3.connect(self.state / "library.sqlite3")
        try:
            connection.execute(
                """
                INSERT INTO pdf_docs(
                    path, path_key, title, title_norm, page_count,
                    extracted_page_count, extraction_warning, file_size,
                    mtime_ns, status
                ) VALUES (
                    'scan.pdf', 'scan', 'scan', 'scan', 1, 0,
                    'no text', 1, 1, 'no_text'
                )
                """
            )
            connection.commit()
        finally:
            connection.close()
        args = argparse.Namespace(
            config=str(self.config_path),
            query="absent theorem",
            alias=[],
            limit=12,
            json=True,
        )
        answer = {
            "schema_version": "paper-locate/v1",
            "canonicalizer_version": "paper-canonical/v1",
            "ok": True,
            "status": "not_found_in_indexed_text",
            "route": "explicit-index",
            "query": {},
            "search": {"stop_reason": "no_indexed_match"},
            "results": [],
            "coverage": {"warnings": []},
            "timing_ms": {},
        }
        with mock.patch.object(
            manage_kb.integrations,
            "run_paper_locate",
            return_value=answer,
        ):
            result = manage_kb.command_paper_locate(args)
        self.assertEqual(result["status"], "not_found_in_indexed_text")
        self.assertTrue(result["ok"])
        self.assertEqual(result["coverage"]["local_index"]["no_text_docs"], 1)
        self.assertTrue(result["coverage"]["warnings"])

    def test_managed_paper_locate_output_is_at_most_twelve_kibibytes(self) -> None:
        args = argparse.Namespace(
            config=str(self.config_path),
            query="bounded output",
            alias=[],
            limit=12,
            json=True,
        )
        answer = {
            "schema_version": "paper-locate/v1",
            "canonicalizer_version": "paper-canonical/v1",
            "ok": True,
            "status": "ambiguous",
            "route": "explicit-index",
            "query": {},
            "search": {},
            "results": [
                {
                    "path": f"book-{rank}.pdf",
                    "pdf_page": rank,
                    "statement_window": "证" * 6000,
                    "why": ["因" * 1000] * 4,
                    "features": {"hard_concepts_missing": ["x" * 1000]},
                }
                for rank in range(1, 4)
            ],
            "coverage": {"warnings": ["警告" * 2000] * 4},
            "timing_ms": {},
        }
        with mock.patch.object(
            manage_kb.integrations,
            "run_paper_locate",
            return_value=answer,
        ):
            result = manage_kb.command_paper_locate(args)
        self.assertLessEqual(
            len(
                json.dumps(
                    result, ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")
            ),
            12 * 1024,
        )

    def test_page_by_id_rejects_missing_resource_before_using_cached_text(self) -> None:
        args = argparse.Namespace(
            config=str(self.config_path),
            id="KB-" + ("A" * 26),
            path=None,
            page=1,
        )
        saved = {
            "schema_version": 2,
            "sources": {
                "library": {"kind": "pdf-library", "relative_path": "Library"}
            },
        }
        with (
            mock.patch.object(
                manage_kb,
                "require_matching_library_index",
                return_value=(saved, self.library),
            ),
            mock.patch.object(
                manage_kb,
                "registry_context",
                return_value=(saved, self.root, self.state / "registry.sqlite3", self.root / ".mpk" / "resources.jsonl"),
            ),
            mock.patch.object(
                manage_kb.registry,
                "resource_resolve",
                return_value={
                    "resource_id": args.id,
                    "relative_path": "Library/book.pdf",
                    "status": "missing",
                    "exists": False,
                },
            ),
            mock.patch.object(manage_kb.library, "get_page") as get_page,
        ):
            with self.assertRaisesRegex(RuntimeError, "active, present"):
                manage_kb.command_page(args)
        get_page.assert_not_called()


class QuestionImportProbeTests(unittest.TestCase):
    def test_probe_reaches_searchable_state_and_blocks_stale_plan(self) -> None:
        success = manage_kb.command_question_import_probe(argparse.Namespace(case="success"))
        self.assertEqual(success["status"], "probe_success")
        self.assertEqual(success["questions"], 1)
        fault = manage_kb.command_question_import_probe(argparse.Namespace(case="fault"))
        self.assertEqual(fault["status"], "probe_recovered")
        self.assertEqual(fault["blocked_path"], "stale_plan_hash")


if __name__ == "__main__":
    unittest.main()
