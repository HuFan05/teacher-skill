from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
VAULT_ROOT = PROJECT_ROOT.parents[1]
RECALL_SCRIPT = PROJECT_ROOT.parent / "recall_notes.py"


class ObsidianLocalKbStatusAndRecallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()
        self.db = self.root / "vault.sqlite3"
        self.write_note(
            "Dijkstra 方法.md",
            "### Dijkstra relaxation\nDijkstra relaxation connects to [[邻居]] and priority queue.\n",
        )
        self.write_note(
            "邻居.md",
            "### priority queue\npriority queue is a graph neighbor for Dijkstra methods.\n",
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def write_note(self, relative_path: str, text: str) -> None:
        path = self.vault / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def run_kb(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        return subprocess.run(
            [sys.executable, "-m", "obsidian_local_kb", *args],
            cwd=PROJECT_ROOT,
            env=env,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=True,
        )

    def run_recall(self, *args: str) -> dict[str, object]:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        completed = subprocess.run(
            [
                sys.executable,
                str(RECALL_SCRIPT),
                *args,
                "--kb-root",
                str(PROJECT_ROOT),
                "--vault-root",
                str(self.vault),
                "--db",
                str(self.db),
            ],
            cwd=VAULT_ROOT,
            env=env,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=True,
        )
        return json.loads(completed.stdout)

    def index_vault(self) -> None:
        self.run_kb("index", "--vault", str(self.vault), "--db", str(self.db))

    def test_status_is_fresh_after_new_index(self) -> None:
        self.index_vault()
        completed = self.run_kb(
            "status",
            "--vault",
            str(self.vault),
            "--db",
            str(self.db),
            "--include-reindex-command",
            "--json",
        )
        payload = json.loads(completed.stdout)
        self.assertTrue(payload["db_exists"])
        self.assertEqual(payload["status_method"], "stored_file_metadata")
        self.assertEqual(payload["vault_markdown_count"], 2)
        self.assertEqual(payload["indexed_note_count"], 2)
        self.assertFalse(payload["index_stale"])

    def test_status_detects_modified_and_new_markdown(self) -> None:
        self.index_vault()
        time.sleep(0.02)
        self.write_note(
            "Dijkstra 方法.md",
            "### Dijkstra relaxation\nDijkstra relaxation was edited after indexing.\n",
        )
        self.write_note("新增.md", "### 新增\nnew file after indexing\n")
        completed = self.run_kb(
            "status",
            "--vault",
            str(self.vault),
            "--db",
            str(self.db),
            "--include-reindex-command",
            "--json",
        )
        payload = json.loads(completed.stdout)
        self.assertTrue(payload["index_stale"])
        self.assertGreaterEqual(payload["newer_markdown_count"], 1)
        self.assertGreaterEqual(payload["missing_indexed_count"], 1)
        self.assertIn("refresh command", payload["reindex_hint"])
        self.assertIn("obsidian_local_kb refresh", payload["reindex_command"])

    def test_status_missing_database_recommends_refresh(self) -> None:
        completed = self.run_kb(
            "status",
            "--vault",
            str(self.vault),
            "--db",
            str(self.db),
            "--include-reindex-command",
            "--json",
        )
        payload = json.loads(completed.stdout)
        self.assertFalse(payload["db_exists"])
        self.assertIn("refresh command", payload["reindex_hint"])
        self.assertIn("obsidian_local_kb refresh", payload["reindex_command"])

    def test_status_falls_back_for_old_schema(self) -> None:
        con = sqlite3.connect(self.db)
        with con:
            con.execute(
                "CREATE TABLE notes(id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL UNIQUE, path_key TEXT NOT NULL UNIQUE, title TEXT NOT NULL, title_norm TEXT NOT NULL)"
            )
            con.execute(
                "INSERT INTO notes(path, path_key, title, title_norm) VALUES (?, ?, ?, ?)",
                ("Dijkstra 方法.md", "dijkstra 方法", "Dijkstra 方法", "dijkstra 方法"),
            )
        con.close()
        completed = self.run_kb(
            "status",
            "--vault",
            str(self.vault),
            "--db",
            str(self.db),
            "--json",
        )
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["status_method"], "db_mtime_fallback")
        self.assertEqual(payload["indexed_note_count"], 1)
        self.assertTrue(payload["schema_mismatch"])
        self.assertTrue(payload["index_stale"])

    def test_recall_query_privacy_defaults(self) -> None:
        self.index_vault()
        payload = self.run_recall("--query", "Dijkstra")
        self.assertEqual(payload["mode"], "query")
        self.assertIn("index_status", payload)
        self.assertIn("retrieval_log", payload)
        self.assertEqual(payload["expanded_notes"], [])
        self.assertGreaterEqual(len(payload["note_candidates"]), 1)
        for item in payload["note_candidates"]:
            self.assertNotIn("snippet", item)
            self.assertNotIn("absolute_path", item)

    def test_recall_explicit_flags_expand_payload(self) -> None:
        self.index_vault()
        payload = self.run_recall(
            "--query",
            "Dijkstra",
            "--include-snippets",
            "--expand",
            "1",
            "--include-links",
            "--include-local-paths",
        )
        self.assertIn("absolute_path", payload["note_candidates"][0])
        self.assertNotIn("snippet", payload["note_candidates"][0])
        self.assertEqual(len(payload["expanded_notes"]), 1)
        expanded = payload["expanded_notes"][0]
        self.assertIn("selected_sections", expanded)
        self.assertIn("links", expanded)
        self.assertIn("absolute_path", expanded)
        self.assertTrue(payload["retrieval_log"]["snippets_included"])
        self.assertTrue(payload["retrieval_log"]["links_metadata_included"])
        self.assertTrue(payload["retrieval_log"]["local_paths_included"])

    def test_anchor_query_records_graph_boost(self) -> None:
        self.index_vault()
        payload = self.run_recall("--query", "priority", "--anchor", "Dijkstra 方法")
        self.assertTrue(payload["retrieval_log"]["graph_boost_seen"])

    def test_dry_run_does_not_require_existing_index(self) -> None:
        payload = self.run_recall("--dry-run", "--query", "Dijkstra")
        self.assertEqual(payload["mode"], "dry_run")
        self.assertFalse(payload["index_status"]["db_exists"])
        self.assertEqual(payload["planned_command"][0:4], ["python", "-m", "obsidian_local_kb", "query"])
        self.assertEqual(payload["retrieval_log"]["candidate_count"], 0)


if __name__ == "__main__":
    unittest.main()
