from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
RECALL = SCRIPTS / "recall_notes.py"
VAULT_STATS = SCRIPTS / "vault_stats.py"
LOCAL_KB_ROOT = SCRIPTS / "obsidian_local_kb"


def load_module(name: str, path: Path):
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class RecallAndStatsTests(unittest.TestCase):
    def test_disk_title_scan_skips_paths_resolving_outside_vault(self) -> None:
        recall = load_module("recall_notes_for_resolved_path_test", RECALL)
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir) / "vault"
            vault.mkdir()
            included = vault / "Included.md"
            excluded = vault / "Excluded.md"
            included.write_text("inside", encoding="utf-8")
            excluded.write_text("pretend symlink", encoding="utf-8")
            original = recall._path_inside_vault

            def guarded(root: Path, candidate: Path) -> Path | None:
                if candidate.name == "Excluded.md":
                    return None
                return original(root, candidate)

            with patch.object(recall, "_path_inside_vault", side_effect=guarded):
                self.assertEqual(recall._vault_markdown_files(vault), [included.resolve()])

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name).resolve()
        self.vault = self.root / "vault"
        self.vault.mkdir()
        self.kb = self.root / "kb"
        self.kb.mkdir()
        self.db = self.root / "vault.sqlite3"
        self.db.write_text("", encoding="utf-8")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_graph_boost_seen_uses_reason_fragments(self) -> None:
        recall = load_module("recall_notes_for_graph_test", RECALL)
        self.assertTrue(recall.graph_boost_seen([{"reasons": ["direct backlink from anchor note"]}]))
        self.assertTrue(recall.graph_boost_seen([{"reasons": ["two-hop link via something"]}]))
        self.assertTrue(recall.graph_boost_seen([{"reasons": ["graph anchor +30"]}]))
        self.assertFalse(recall.graph_boost_seen([{"reasons": ["full-text match"]}]))

    def test_section_preview_and_whole_note_are_total_capped(self) -> None:
        recall = load_module("recall_notes_for_section_test", RECALL)
        sections = [
            {"heading_path": "", "level": 0, "content": "intro " * 20},
            {"heading_path": "A", "level": 2, "content": "alpha " * 20},
            {"heading_path": "B", "level": 2, "content": "beta " * 20},
        ]
        selected = recall.select_sections(sections, [], section_limit=3, max_chars=100, max_total_chars=80)
        self.assertLessEqual(sum(len(item["excerpt"]) for item in selected), 80)

        whole, truncated = recall.whole_note_sections(sections, max_chars_per_section=100, max_total_chars=80)
        self.assertTrue(truncated)
        self.assertLessEqual(sum(len(item["excerpt"]) for item in whole), 80)

        preserved, truncated = recall.whole_note_sections(
            [{"heading_path": "Lines", "level": 2, "content": "one\r\ntwo\r\nthree"}],
            max_chars_per_section=2,
            max_total_chars=100,
        )
        self.assertFalse(truncated)
        self.assertEqual(preserved[0]["excerpt"], "one\r\ntwo\r\nthree")
        self.assertEqual(preserved[0]["emitted_chars"], len(preserved[0]["excerpt"]))

    def test_exact_note_reads_current_disk_content_and_emits_one_body_shape(self) -> None:
        recall = load_module("recall_notes_for_live_disk_test", RECALL)
        note_path = self.vault / "Fresh.md"
        note_path.write_bytes(b"# Current\r\nnew disk text\r\nsecond line\r\n")
        original_status = recall.index_status_payload
        original_run = recall.run_kb_json
        try:
            recall.index_status_payload = lambda **_kwargs: {"index_stale": True}

            def fake_run(_kb_root, command, **_kwargs):
                self.assertEqual(command[0], "show")
                return {
                    "title": "Fresh",
                    "path": "Fresh.md",
                    "sections": [
                        {
                            "heading_path": "Old",
                            "level": 2,
                            "content": "stale index text",
                        }
                    ],
                }

            recall.run_kb_json = fake_run
            args = recall.build_parser().parse_args(
                [
                    "--note",
                    "Fresh",
                    "--whole-note",
                    "--no-linked-metadata",
                    "--vault-root",
                    str(self.vault),
                    "--kb-root",
                    str(self.kb),
                    "--db",
                    str(self.db),
                ]
            )
            recall.validate_arguments(args)
            payload = recall.build_note_payload(args)
        finally:
            recall.index_status_payload = original_status
            recall.run_kb_json = original_run

        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["content_mode"], "whole_note")
        self.assertEqual(payload["permission_scope"]["max_total_chars"], 30000)
        expanded = payload["expanded_notes"][0]
        self.assertNotIn("selected_sections", expanded)
        body = "".join(item["excerpt"] for item in expanded["whole_note_sections"])
        self.assertIn("new disk text\r\nsecond line", body)
        self.assertNotIn("stale index text", body)
        self.assertEqual(payload["emitted_chars"], len(body))
        self.assertEqual(payload["read_audit"]["notes"][0]["relative_path"], "Fresh.md")

    def test_exact_note_falls_back_to_unique_disk_title_when_index_misses(self) -> None:
        recall = load_module("recall_notes_for_fallback_test", RECALL)
        folder = self.vault / "Nested"
        folder.mkdir()
        (folder / "Only Here.md").write_text("# H\nlive\n", encoding="utf-8", newline="\n")
        original_status = recall.index_status_payload
        original_run = recall.run_kb_json
        try:
            recall.index_status_payload = lambda **_kwargs: {"index_stale": True}
            recall.run_kb_json = lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("stale index miss")
            )
            args = recall.build_parser().parse_args(
                [
                    "--note",
                    "Only Here",
                    "--whole-note",
                    "--no-linked-metadata",
                    "--vault-root",
                    str(self.vault),
                    "--kb-root",
                    str(self.kb),
                    "--db",
                    str(self.db),
                ]
            )
            payload = recall.build_note_payload(args)
        finally:
            recall.index_status_payload = original_status
            recall.run_kb_json = original_run

        expanded = payload["expanded_notes"][0]
        self.assertEqual(expanded["relative_path"], "Nested/Only Here.md")
        self.assertEqual(expanded["resolution_source"], "unique_title_disk_content")
        self.assertIn("live", expanded["whole_note_sections"][0]["excerpt"])

    def test_live_disk_parser_ignores_headings_inside_fenced_code(self) -> None:
        recall = load_module("recall_notes_for_disk_fence_test", RECALL)
        path = self.vault / "Fence.md"
        path.write_text(
            "# Real\n```markdown\n# Fake\n```\nafter\n",
            encoding="utf-8",
            newline="\n",
        )
        parsed = recall.parse_disk_note(self.vault, path)
        heading_paths = [item["heading_path"] for item in parsed["sections"]]
        self.assertIn("Real", heading_paths)
        self.assertNotIn("Fake", heading_paths)
        real = next(item for item in parsed["sections"] if item["heading_path"] == "Real")
        self.assertIn("# Fake", real["content"])

    def test_bare_title_detects_disk_ambiguity_but_explicit_path_reads_directly(self) -> None:
        recall = load_module("recall_notes_for_disk_ambiguity_test", RECALL)
        for folder_name, body in (("one", "first"), ("two", "second")):
            folder = self.vault / folder_name
            folder.mkdir()
            (folder / "Same.md").write_text(body + "\n", encoding="utf-8", newline="\n")
        original_run = recall.run_kb_json
        try:
            recall.run_kb_json = lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("bare title ambiguity must be decided from current disk files")
            )
            with self.assertRaises(recall.InputValidationError):
                recall.resolve_disk_note(
                    kb_root=self.kb,
                    db_path=self.db,
                    vault_root=self.vault,
                    note_spec="Same",
                    debug=False,
                    use_index_resolution=True,
                )
            path, source = recall.resolve_disk_note(
                kb_root=self.kb,
                db_path=self.db,
                vault_root=self.vault,
                note_spec="one/Same.md",
                debug=False,
                use_index_resolution=True,
            )
        finally:
            recall.run_kb_json = original_run
        self.assertEqual(path.relative_to(self.vault).as_posix(), "one/Same.md")
        self.assertEqual(source, "direct_path_disk_content")

    def test_index_alias_is_rechecked_against_current_frontmatter(self) -> None:
        recall = load_module("recall_notes_for_alias_recheck_test", RECALL)
        note = self.vault / "A.md"
        note.write_text(
            "---\naliases:\n  - Current Alias\n---\nbody\n",
            encoding="utf-8",
            newline="\n",
        )
        original_run = recall.run_kb_json
        try:
            recall.run_kb_json = lambda *_args, **_kwargs: {
                "title": "A",
                "path": "A.md",
                "sections": [],
            }
            path, source = recall.resolve_disk_note(
                kb_root=self.kb,
                db_path=self.db,
                vault_root=self.vault,
                note_spec="Current Alias",
                debug=False,
                use_index_resolution=True,
            )
            self.assertEqual(path, note.resolve())
            self.assertEqual(source, "index_alias_verified_disk_content")
            with self.assertRaises(FileNotFoundError):
                recall.resolve_disk_note(
                    kb_root=self.kb,
                    db_path=self.db,
                    vault_root=self.vault,
                    note_spec="Stale Alias",
                    debug=False,
                    use_index_resolution=True,
                )
        finally:
            recall.run_kb_json = original_run

    def test_explicit_note_path_survives_unavailable_index_status(self) -> None:
        recall = load_module("recall_notes_for_status_independent_path_test", RECALL)
        note = self.vault / "Direct.md"
        note.write_text("# H\nlive disk body\n", encoding="utf-8", newline="\n")
        original_status = recall.index_status_payload
        original_run = recall.run_kb_json
        try:
            recall.index_status_payload = lambda **_kwargs: (_ for _ in ()).throw(
                RuntimeError("broken status command")
            )
            recall.run_kb_json = lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("explicit path must not require the index")
            )
            args = recall.build_parser().parse_args(
                [
                    "--note",
                    "Direct.md",
                    "--whole-note",
                    "--no-linked-metadata",
                    "--vault-root",
                    str(self.vault),
                    "--kb-root",
                    str(self.kb),
                    "--db",
                    str(self.db),
                ]
            )
            payload = recall.build_note_payload(args)
        finally:
            recall.index_status_payload = original_status
            recall.run_kb_json = original_run
        self.assertEqual(payload["index_status"]["status_method"], "status_unavailable")
        self.assertIn(
            "live disk body",
            payload["expanded_notes"][0]["whole_note_sections"][0]["excerpt"],
        )

    def test_query_payload_privacy_defaults_and_local_path_flag(self) -> None:
        recall = load_module("recall_notes_for_payload_test", RECALL)
        original_status = recall.index_status_payload
        original_run = recall.run_kb_json
        original_require = recall.require_existing_db
        try:
            recall.index_status_payload = lambda **_kwargs: {"index_stale": False}
            recall.require_existing_db = lambda *_args, **_kwargs: None
            recall.run_kb_json = lambda *_args, **_kwargs: [
                {
                    "score": 1.0,
                    "title": "Note",
                    "path": "Note.md",
                    "heading_path": "Intro",
                    "reasons": ["full-text match"],
                    "snippet": "secret snippet",
                }
            ]
            parser = recall.build_parser()
            args = parser.parse_args(
                [
                    "--query",
                    "topic",
                    "--vault-root",
                    str(self.vault),
                    "--kb-root",
                    str(self.kb),
                    "--db",
                    str(self.db),
                ]
            )
            payload = recall.build_query_payload(args)
            self.assertNotIn("snippet", payload["note_candidates"][0])
            self.assertNotIn("absolute_path", payload["note_candidates"][0])
            self.assertEqual(payload["content_mode"], "metadata")
            self.assertFalse(payload["permission_scope"]["body_read"])
            self.assertEqual(payload["permission_scope"]["read_package"], "metadata")
            self.assertEqual(payload["expanded_notes"], [])
            self.assertEqual(payload["read_audit"]["notes"], [])
            self.assertEqual(payload["emitted_chars"], 0)

            args = parser.parse_args(
                [
                    "--query",
                    "topic",
                    "--include-snippets",
                    "--include-local-paths",
                    "--vault-root",
                    str(self.vault),
                    "--kb-root",
                    str(self.kb),
                    "--db",
                    str(self.db),
                ]
            )
            payload = recall.build_query_payload(args)
            self.assertIn("snippet", payload["note_candidates"][0])
            self.assertIn("absolute_path", payload["note_candidates"][0])
            self.assertTrue(payload["retrieval_log"]["snippets_included"])
            self.assertEqual(payload["permission_scope"]["read_package"], "custom")
            self.assertEqual(
                payload["permission_scope"]["package_source"],
                "explicit_content_flags",
            )
        finally:
            recall.index_status_payload = original_status
            recall.run_kb_json = original_run
            recall.require_existing_db = original_require

    def test_query_expansions_share_one_response_budget(self) -> None:
        recall = load_module("recall_notes_for_shared_budget_test", RECALL)
        for index in range(3):
            text = "".join(
                f"# {heading}\n" + (str(index) + heading) * 1300 + "\n"
                for heading in ("A", "B", "C")
            )
            (self.vault / f"N{index}.md").write_text(text, encoding="utf-8", newline="\n")

        results = [
            {
                "score": 100 - index,
                "title": f"N{index}",
                "path": f"N{index}.md",
                "heading_path": "A",
                "reasons": ["lexical rank"],
                "snippet": "candidate body",
            }
            for index in range(3)
        ]
        recall_status = recall.index_status_payload
        recall_run = recall.run_kb_json
        recall_require = recall.require_existing_db
        try:
            recall.index_status_payload = lambda **_kwargs: {"index_stale": False}
            recall.require_existing_db = lambda *_args, **_kwargs: None

            def fake_run(_kb_root, command, **_kwargs):
                if command[0] == "query":
                    return results
                raise AssertionError(f"unexpected command: {command}")

            recall.run_kb_json = fake_run
            args = recall.build_parser().parse_args(
                [
                    "--query",
                    "topic",
                    "--read-package",
                    "custom",
                    "--expand",
                    "3",
                    "--section-limit",
                    "3",
                    "--max-chars-per-section",
                    "5000",
                    "--max-total-chars",
                    "12000",
                    "--include-snippets",
                    "--vault-root",
                    str(self.vault),
                    "--kb-root",
                    str(self.kb),
                    "--db",
                    str(self.db),
                ]
            )
            payload = recall.build_query_payload(args)
        finally:
            recall.index_status_payload = recall_status
            recall.run_kb_json = recall_run
            recall.require_existing_db = recall_require

        self.assertEqual(payload["schema_version"], 2)
        self.assertLessEqual(payload["emitted_chars"], 12000)
        self.assertTrue(payload["truncated"])
        self.assertLess(len(payload["expanded_notes"]), 3)
        self.assertTrue(all("snippet" not in item for item in payload["note_candidates"]))
        self.assertFalse(payload["retrieval_log"]["snippets_included"])
        self.assertTrue(
            all("whole_note_sections" not in item for item in payload["expanded_notes"])
        )
        audited = sum(item["chars"] for item in payload["read_audit"]["notes"])
        self.assertEqual(audited, payload["emitted_chars"])

    def test_small_read_defaults_and_out_of_bounds_are_validated_before_retrieval(self) -> None:
        recall = load_module("recall_notes_for_package_validation_test", RECALL)
        args = recall.build_parser().parse_args(["--query", "topic", "--read-package", "small"])
        recall.validate_arguments(args)
        limits = recall.effective_limits(args)
        self.assertEqual(limits["expand"], 3)
        self.assertEqual(limits["section_limit"], 3)
        self.assertEqual(limits["max_total_chars"], 12000)
        self.assertEqual(limits["max_chars_per_section"], 12000)

        for extra in (
            ["--expand", "4"],
            ["--section-limit", "4"],
            ["--max-total-chars", "12001"],
            ["--max-chars-per-section", "12001"],
        ):
            with self.subTest(extra=extra), self.assertRaises(recall.InputValidationError):
                invalid = recall.build_parser().parse_args(
                    ["--query", "topic", "--read-package", "small", *extra]
                )
                recall.validate_arguments(invalid)

        command = [
            sys.executable,
            str(RECALL),
            "--query",
            "topic",
            "--limit",
            "-1",
            "--vault-root",
            str(self.vault),
            "--kb-root",
            str(self.kb),
            "--db",
            str(self.root / "must-not-be-opened.sqlite3"),
        ]
        completed = subprocess.run(
            command,
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(completed.returncode, 1, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["error_type"], "InputValidationError")
        self.assertFalse(payload["index_status"]["checked"])

    def test_stale_query_auto_refreshes_unless_disabled(self) -> None:
        recall = load_module("recall_notes_for_refresh_test", RECALL)
        original_status = recall.index_status_payload
        original_run = recall.run_kb_json
        original_require = recall.require_existing_db
        try:
            statuses = iter(
                [
                    {"index_stale": True, "db_exists": True},
                    {"index_stale": False, "db_exists": True},
                ]
            )
            recall.index_status_payload = lambda **_kwargs: next(statuses)
            recall.require_existing_db = lambda *_args, **_kwargs: None
            calls = []

            def fake_run(_kb_root, command, **_kwargs):
                calls.append(command)
                if command[0] == "refresh":
                    return {
                        "schema_version": 3,
                        "added": 1,
                        "modified": 2,
                        "deleted": 3,
                        "unchanged": 4,
                        "refreshed": True,
                        "rebuilt": False,
                        "indexed_at_utc": "2026-07-11T00:00:00Z",
                        "warnings": [],
                    }
                if command[0] == "query":
                    return []
                raise AssertionError(command)

            recall.run_kb_json = fake_run
            args = recall.build_parser().parse_args(
                [
                    "--query",
                    "topic",
                    "--vault-root",
                    str(self.vault),
                    "--kb-root",
                    str(self.kb),
                    "--db",
                    str(self.db),
                ]
            )
            payload = recall.build_query_payload(args)
            self.assertEqual([call[0] for call in calls], ["refresh", "query"])
            self.assertTrue(payload["index_refresh"]["attempted"])
            self.assertEqual(payload["index_refresh"]["modified"], 2)
            self.assertEqual(
                payload["read_audit"]["index_refresh"]["deleted"],
                3,
            )

            recall.index_status_payload = lambda **_kwargs: {
                "index_stale": True,
                "db_exists": True,
            }
            calls.clear()
            args = recall.build_parser().parse_args(
                [
                    "--query",
                    "topic",
                    "--no-auto-refresh",
                    "--vault-root",
                    str(self.vault),
                    "--kb-root",
                    str(self.kb),
                    "--db",
                    str(self.db),
                ]
            )
            payload = recall.build_query_payload(args)
            self.assertEqual([call[0] for call in calls], ["query"])
            self.assertEqual(
                payload["index_refresh"]["reason"],
                "disabled_by_no_auto_refresh",
            )
        finally:
            recall.index_status_payload = original_status
            recall.run_kb_json = original_run
            recall.require_existing_db = original_require

    def test_refresh_failure_stops_query_and_reports_attempt(self) -> None:
        recall = load_module("recall_notes_for_refresh_failure_test", RECALL)
        original_status = recall.index_status_payload
        original_run = recall.run_kb_json
        try:
            recall.index_status_payload = lambda **_kwargs: {
                "index_stale": True,
                "db_exists": True,
            }
            calls = []

            def failed_refresh(_kb_root, command, **_kwargs):
                calls.append(command[0])
                raise RuntimeError("refresh subprocess failed")

            recall.run_kb_json = failed_refresh
            args = recall.build_parser().parse_args(
                [
                    "--query",
                    "topic",
                    "--vault-root",
                    str(self.vault),
                    "--kb-root",
                    str(self.kb),
                    "--db",
                    str(self.db),
                ]
            )
            with self.assertRaises(recall.IndexRefreshError) as caught:
                recall.build_query_payload(args)
            error = recall.build_error_payload(args, caught.exception)
        finally:
            recall.index_status_payload = original_status
            recall.run_kb_json = original_run

        self.assertEqual(calls, ["refresh"])
        self.assertTrue(error["index_refresh"]["attempted"])
        self.assertFalse(error["index_refresh"]["refreshed"])
        self.assertEqual(
            error["index_refresh"]["reason"],
            "refresh_failed_query_not_executed",
        )

    def test_post_refresh_status_must_be_fresh_and_verifiable(self) -> None:
        recall = load_module("recall_notes_for_post_refresh_guard_test", RECALL)
        refresh_result = {
            "schema_version": 3,
            "added": 1,
            "modified": 0,
            "deleted": 0,
            "unchanged": 0,
            "refreshed": True,
            "rebuilt": False,
            "indexed_at_utc": "2026-07-11T00:00:00Z",
            "warnings": [],
        }
        for case in ("still_stale", "status_failed"):
            with self.subTest(case=case):
                original_status = recall.index_status_payload
                original_run = recall.run_kb_json
                original_require = recall.require_existing_db
                status_calls = 0

                def status(**_kwargs):
                    nonlocal status_calls
                    status_calls += 1
                    if status_calls == 1 or case == "still_stale":
                        return {"index_stale": True, "db_exists": True}
                    raise RuntimeError("post-refresh status failed")

                commands = []

                def run(_kb_root, command, **_kwargs):
                    commands.append(command[0])
                    if command[0] == "refresh":
                        return refresh_result
                    raise AssertionError("query must not run without verified freshness")

                try:
                    recall.index_status_payload = status
                    recall.run_kb_json = run
                    recall.require_existing_db = lambda *_args, **_kwargs: None
                    args = recall.build_parser().parse_args(
                        [
                            "--query",
                            "topic",
                            "--vault-root",
                            str(self.vault),
                            "--kb-root",
                            str(self.kb),
                            "--db",
                            str(self.db),
                        ]
                    )
                    with self.assertRaises(recall.IndexRefreshError) as caught:
                        recall.build_query_payload(args)
                    error = recall.build_error_payload(args, caught.exception)
                finally:
                    recall.index_status_payload = original_status
                    recall.run_kb_json = original_run
                    recall.require_existing_db = original_require

                self.assertEqual(commands, ["refresh"])
                self.assertTrue(error["index_refresh"]["attempted"])
                self.assertFalse(error["index_refresh"]["refreshed"])
                self.assertEqual(error["index_refresh"]["added"], 1)
                expected_reason = (
                    "post_refresh_index_still_stale_query_not_executed"
                    if case == "still_stale"
                    else "post_refresh_status_failed_query_not_executed"
                )
                self.assertEqual(error["index_refresh"]["reason"], expected_reason)

    def test_query_auto_refresh_integration_uses_lower_level_refresh_cli(self) -> None:
        if not (LOCAL_KB_ROOT / "obsidian_local_kb" / "__main__.py").exists():
            self.skipTest("bundled obsidian_local_kb module is unavailable")
        (self.vault / "Live.md").write_text(
            "# Alpha\n" + ("Alpha current searchable body " * 120) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        fresh_db = self.root / "fresh.sqlite3"
        completed = subprocess.run(
            [
                sys.executable,
                str(RECALL),
                "--query",
                "Alpha",
                "--read-package",
                "small",
                "--vault-root",
                str(self.vault),
                "--kb-root",
                str(LOCAL_KB_ROOT),
                "--db",
                str(fresh_db),
            ],
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["schema_version"], 2)
        self.assertTrue(payload["index_refresh"]["attempted"])
        self.assertTrue(payload["index_refresh"]["rebuilt"])
        self.assertEqual(payload["index_refresh"]["added"], 1)
        self.assertEqual(payload["note_candidates"][0]["relative_path"], "Live.md")
        self.assertEqual(payload["expanded_notes"][0]["content_source"], "disk")
        section = payload["expanded_notes"][0]["selected_sections"][0]
        self.assertGreater(section["emitted_chars"], 1200)
        self.assertEqual(section["emitted_chars"], len(section["excerpt"]))

    def test_error_payload_is_json_shaped(self) -> None:
        recall = load_module("recall_notes_for_error_test", RECALL)
        parser = recall.build_parser()
        args = parser.parse_args(
            [
                "--query",
                "topic",
                "--vault-root",
                str(self.vault),
                "--kb-root",
                str(self.kb),
                "--db",
                str(self.db),
            ]
        )
        payload = recall.build_error_payload(args, RuntimeError("boom"))
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["mode"], "query")
        self.assertIn("permission_relevant_flags", payload)

    def test_vault_stats_default_hides_paths_and_flag_includes_them(self) -> None:
        (self.vault / "A.md").write_text("# A\n正文 [[B]]\n", encoding="utf-8", newline="\n")
        command = [
            sys.executable,
            str(VAULT_STATS),
            "--vault-root",
            str(self.vault),
            "--kb-root",
            str(self.kb),
            "--db",
            str(self.db),
            "--json",
        ]
        completed = subprocess.run(command, text=True, encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertNotIn("vault_root", payload["counting_scope"])

        completed = subprocess.run([*command, "--include-local-paths"], text=True, encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["counting_scope"]["vault_root"], str(self.vault.resolve()))


if __name__ == "__main__":
    unittest.main()
