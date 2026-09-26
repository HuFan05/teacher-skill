from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from obsidian_local_kb import cli
from obsidian_local_kb.db import (
    CURRENT_SCHEMA_VERSION,
    connect,
    query_sections,
    rebuild_index,
    refresh_index,
    resolve_note_spec,
)
from obsidian_local_kb.markdown import parse_markdown_note


class RefreshRankingMarkdownTests(unittest.TestCase):
    def test_markdown_scan_skips_entry_resolving_outside_vault(self) -> None:
        included = self.write_note("Included.md", "inside\n")
        excluded = self.write_note("Excluded.md", "pretend symlink\n")
        original = cli.resolved_markdown_inside_vault

        def guarded(root: Path, candidate: Path) -> Path | None:
            if candidate.name == excluded.name:
                return None
            return original(root, candidate)

        with patch("obsidian_local_kb.cli.resolved_markdown_inside_vault", side_effect=guarded):
            self.assertEqual(
                cli.iter_markdown_files(self.vault, excluded_dirs=set()),
                [included.resolve()],
            )

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()
        self.db = self.root / "index.sqlite3"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def write_note(self, relative_path: str, text: str) -> Path:
        path = self.vault / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def markdown_files(self) -> list[Path]:
        return sorted(self.vault.rglob("*.md"))

    def build(self) -> None:
        notes = [parse_markdown_note(self.vault, path) for path in self.markdown_files()]
        rebuild_index(self.db, notes)

    def test_frontmatter_aliases_tags_and_fenced_code_are_parsed_safely(self) -> None:
        path = self.write_note(
            "Meta.md",
            """---
aliases:
  - Gamma Name
tags: [ml, "#optimization"]
...
   # Real heading ###
See [[Real Target]].

   ```python
   # Fake heading
   [[Ghost Target]]
   ```
""",
        )
        note = parse_markdown_note(self.vault, path)
        self.assertEqual(note.aliases, ["Gamma Name"])
        self.assertEqual(note.tags, ["ml", "optimization"])
        self.assertEqual([section.heading for section in note.sections if section.heading], ["Real heading"])
        self.assertEqual([link.target_note_raw for link in note.links], ["Real Target"])

    def test_complex_frontmatter_is_ignored_with_warning(self) -> None:
        path = self.write_note(
            "Complex.md",
            "---\naliases: {short: long}\ntags: &shared [ml]\n---\nbody\n",
        )
        note = parse_markdown_note(self.vault, path)
        self.assertEqual(note.aliases, [])
        self.assertEqual(note.tags, [])
        self.assertEqual(len(note.warnings), 2)

    def test_refresh_adds_modifies_deletes_and_then_becomes_noop(self) -> None:
        first = self.write_note("A.md", "# Old\nold token\n")
        second = self.write_note("B.md", "# B\nremove me\n")
        self.build()
        time.sleep(0.01)
        first.write_text("# New\nnew token\n", encoding="utf-8")
        second.unlink()
        self.write_note("C.md", "# C\nadded token\n")

        payload = refresh_index(self.db, self.vault, self.markdown_files())
        self.assertEqual(
            {key: payload[key] for key in ("added", "modified", "deleted", "unchanged")},
            {"added": 1, "modified": 1, "deleted": 1, "unchanged": 0},
        )
        self.assertTrue(payload["refreshed"])
        self.assertFalse(payload["rebuilt"])

        con = connect(self.db, read_only=True)
        try:
            paths = {row["path"] for row in con.execute("SELECT path FROM notes")}
            results = query_sections(con, "new token")
        finally:
            con.close()
        self.assertEqual(paths, {"A.md", "C.md"})
        self.assertEqual(results[0].path, "A.md")

        noop = refresh_index(self.db, self.vault, self.markdown_files())
        self.assertFalse(noop["refreshed"])
        self.assertEqual(noop["unchanged"], 2)

    def test_existing_read_only_connection_sees_committed_wal_refresh(self) -> None:
        path = self.write_note("Visible.md", "# Visible\nold marker\n")
        self.build()
        reader = connect(self.db, read_only=True)
        try:
            self.assertEqual(query_sections(reader, "old marker")[0].path, "Visible.md")
            time.sleep(0.01)
            path.write_text("# Visible\nnew marker\n", encoding="utf-8")
            refresh_index(self.db, self.vault, self.markdown_files())
            self.assertEqual(query_sections(reader, "new marker")[0].path, "Visible.md")
            self.assertEqual(query_sections(reader, "old marker"), [])
        finally:
            reader.close()

    def test_refresh_rebuilds_mismatched_schema_atomically(self) -> None:
        self.write_note("Current.md", "---\naliases: [Current Alias]\n---\n# Current\nbody\n")
        con = sqlite3.connect(self.db)
        with con:
            con.execute(
                "CREATE TABLE notes(id INTEGER PRIMARY KEY, path TEXT, path_key TEXT, title TEXT, title_norm TEXT)"
            )
            con.execute("CREATE TABLE index_meta(key TEXT PRIMARY KEY, value TEXT)")
            con.execute("INSERT INTO index_meta(key, value) VALUES ('schema_version', '2')")
        con.close()

        payload = refresh_index(self.db, self.vault, self.markdown_files())
        self.assertTrue(payload["rebuilt"])
        self.assertEqual(payload["schema_version"], CURRENT_SCHEMA_VERSION)
        con = connect(self.db, read_only=True)
        try:
            resolved = resolve_note_spec(con, "Current Alias")
            columns = [row["name"] for row in con.execute("PRAGMA table_info(section_fts)")]
        finally:
            con.close()
        self.assertEqual(resolved["path"], "Current.md")
        self.assertEqual(
            columns,
            ["search_text", "title_text", "heading_text", "path_text", "alias_text", "tag_text", "section_id"],
        )

    def test_failed_atomic_replace_preserves_existing_database(self) -> None:
        self.write_note("A.md", "# A\nbody\n")
        con = sqlite3.connect(self.db)
        with con:
            con.execute("CREATE TABLE sentinel(value TEXT)")
            con.execute("INSERT INTO sentinel(value) VALUES ('keep')")
        con.close()
        before = self.db.read_bytes()
        note = parse_markdown_note(self.vault, self.vault / "A.md")
        with patch("obsidian_local_kb.db.os.replace", side_effect=OSError("replace failed")):
            with self.assertRaises(OSError):
                rebuild_index(self.db, [note])
        self.assertEqual(self.db.read_bytes(), before)

    def test_failed_incremental_refresh_rolls_back_all_changes(self) -> None:
        first = self.write_note("A.md", "# A\nold first\n")
        second = self.write_note("B.md", "# B\nold second\n")
        self.build()
        time.sleep(0.01)
        first.write_text("# A\nnew first\n", encoding="utf-8")
        second.write_text("# B\nnew second\n", encoding="utf-8")

        from obsidian_local_kb import db as db_module

        original_insert = db_module._insert_note_children
        calls = 0

        def fail_on_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("injected refresh failure")
            return original_insert(*args, **kwargs)

        with patch("obsidian_local_kb.db._insert_note_children", side_effect=fail_on_second):
            with self.assertRaises(RuntimeError):
                refresh_index(self.db, self.vault, self.markdown_files())

        con = connect(self.db, read_only=True)
        try:
            contents = {
                row["path"]: row["content"]
                for row in con.execute(
                    "SELECT n.path, s.content FROM notes n JOIN sections s ON s.note_id = n.id"
                )
            }
        finally:
            con.close()
        self.assertIn("old first", contents["A.md"])
        self.assertIn("old second", contents["B.md"])

    def test_deterministic_ranking_uses_alias_tag_title_and_graph_signals(self) -> None:
        self.write_note(
            "Alpha Method.md",
            "---\naliases: [Beta Method]\ntags: [topology]\n---\n# Overview\nshared commonword\n",
        )
        self.write_note("Other.md", "# Notes\nAlpha Method shared commonword\n")
        self.write_note("Anchor.md", "# Anchor\n[[Alpha Method]]\n")
        self.build()
        con = connect(self.db, read_only=True)
        try:
            exact_title = query_sections(con, "Alpha Method")
            exact_alias = query_sections(con, "Beta Method")
            tag = query_sections(con, "topology")
            anchor_id = int(resolve_note_spec(con, "Anchor")["id"])
            graph = query_sections(con, "shared", anchor_note_id=anchor_id)
            common = query_sections(con, "commonword")
        finally:
            con.close()

        self.assertEqual(exact_title[0].path, "Alpha Method.md")
        self.assertTrue(any("title/path exact +100" in reason for reason in exact_title[0].reasons))
        self.assertEqual(exact_alias[0].path, "Alpha Method.md")
        self.assertTrue(any("alias exact +80" in reason for reason in exact_alias[0].reasons))
        self.assertEqual(tag[0].path, "Alpha Method.md")
        self.assertTrue(any("tag" in reason for reason in tag[0].reasons))
        self.assertEqual(graph[0].path, "Alpha Method.md")
        self.assertTrue(any("graph direct" in reason for reason in graph[0].reasons))
        self.assertIn("lexical rank +80", common[0].reasons[0])
        self.assertIn("lexical rank +79", common[1].reasons[0])

    def test_better_bm25_candidate_keeps_the_higher_lexical_rank(self) -> None:
        self.write_note("Strong.md", "# Result\nneedleterm needleterm needleterm needleterm\n")
        self.write_note("Weak.md", "# Result\n" + ("filler " * 120) + "needleterm\n")
        self.build()
        con = connect(self.db, read_only=True)
        try:
            results = query_sections(con, "needleterm")
        finally:
            con.close()
        self.assertEqual([item.path for item in results[:2]], ["Strong.md", "Weak.md"])
        self.assertIn("lexical rank +80", results[0].reasons[0])
        self.assertIn("lexical rank +79", results[1].reasons[0])

    def test_refresh_cli_json_shape(self) -> None:
        self.write_note("A.md", "# A\nbody\n")
        stdout = StringIO()
        with redirect_stdout(stdout):
            exit_code = cli.main(
                ["refresh", "--vault", str(self.vault), "--db", str(self.db), "--json"]
            )
        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(
            set(payload),
            {
                "schema_version",
                "added",
                "modified",
                "deleted",
                "unchanged",
                "refreshed",
                "rebuilt",
                "indexed_at_utc",
                "warnings",
            },
        )


if __name__ == "__main__":
    unittest.main()
