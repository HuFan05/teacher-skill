from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from os.path import realpath as _realpath

# The platform temporary root can be a symlink (for example on macOS); resolve
# it so synthetic knowledge roots match the resolved paths the code compares.
tempfile.tempdir = _realpath(tempfile.gettempdir())
import unittest


SKILL_ROOT = Path(__file__).resolve().parents[1]
CLI = SKILL_ROOT / "scripts" / "manage_kb.py"
if str(SKILL_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from manage_personal_knowledge.library import index_library  # noqa: E402


class RegistryCliIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "Knowledge"
        self.vault = self.root / "Vault"
        self.library = self.root / "Library"
        self.vault.mkdir(parents=True)
        (self.vault / ".obsidian").mkdir()
        self.library.mkdir()
        self.document = self.library / "sample.pdf"
        self.document.write_bytes(b"synthetic-pdf")
        self.config = self.base / "config.json"
        self.state = self.base / "state"
        self.environment = os.environ.copy()
        self.environment.update(
            {
                "MPK_CONFIG_PATH": str(self.config),
                "MPK_STATE_DIR": str(self.state),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUTF8": "1",
            }
        )
        sibling_batch_editor = (
            SKILL_ROOT.parent
            / "obsidian-vault-notes"
            / "scripts"
            / "vault_batch_edit.py"
        )
        if sibling_batch_editor.is_file():
            self.environment["MPK_OBSIDIAN_BATCH_EDITOR"] = str(
                sibling_batch_editor
            )
        configured = self.run_cli(
            "configure",
            "--root",
            str(self.root),
            "--vault",
            "Vault",
            "--library",
            "Library",
            "--yes",
        )
        self.assertEqual(configured.returncode, 0, configured.stderr)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-B", str(CLI), *arguments],
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self.environment,
            check=False,
        )

    def payload(self, completed: subprocess.CompletedProcess[str]) -> dict[str, object]:
        self.assertTrue(completed.stdout.strip(), completed.stderr)
        value = json.loads(completed.stdout)
        self.assertIsInstance(value, dict)
        return value

    def apply_plan(self, command: str, arguments: list[str]) -> dict[str, object]:
        preview = self.run_cli(command, *arguments)
        self.assertEqual(preview.returncode, 0, preview.stderr)
        plan = self.payload(preview)
        written = self.run_cli(
            command,
            *arguments,
            "--write",
            "--expect-plan-sha256",
            str(plan["plan_sha256"]),
        )
        self.assertEqual(written.returncode, 0, written.stderr)
        return self.payload(written)

    def test_init_scan_resolve_and_reference_refresh_are_two_phase(self) -> None:
        preview = self.run_cli("registry-init")
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertFalse((self.root / ".mpk").exists())
        self.assertEqual(json.loads(self.config.read_text(encoding="utf-8"))["schema_version"], 1)

        initialized = self.apply_plan("registry-init", [])
        self.assertTrue(initialized["applied"])
        self.assertEqual(json.loads(self.config.read_text(encoding="utf-8"))["schema_version"], 2)
        self.assertTrue((self.root / ".mpk" / "root.json").is_file())

        scanned = self.apply_plan("registry-scan", ["--max-files", "20"])
        self.assertEqual(len(scanned["registered"]), 1)
        resource_id = str(scanned["registered"][0])
        resolved_call = self.run_cli("resource-resolve", "--id", resource_id)
        self.assertEqual(resolved_call.returncode, 0, resolved_call.stderr)
        resolved = self.payload(resolved_call)
        self.assertEqual(resolved["relative_path"], "Library/sample.pdf")
        self.assertEqual(resolved["recommended_skill"], "pdf")

        note = self.vault / "note.md"
        note.write_text(
            f"[sample]({self.document.resolve().as_uri()})<!-- mpk-resource:{resource_id} -->\n",
            encoding="utf-8",
        )
        post_sha = hashlib.sha256(note.read_bytes()).hexdigest()
        refreshed = self.apply_plan(
            "reference-refresh",
            ["--note", str(note), "--post-sha256", post_sha],
        )
        self.assertEqual(refreshed["reference_sync_status"], "synchronized")
        connection = sqlite3.connect(self.state / "registry.sqlite3")
        try:
            count = connection.execute("SELECT COUNT(*) FROM note_references").fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(count, 1)

        note.unlink()
        deleted = self.apply_plan(
            "reference-refresh",
            ["--changed", "--max-notes", "20"],
        )
        self.assertEqual(deleted["deleted_note_caches"], 1)
        connection = sqlite3.connect(self.state / "registry.sqlite3")
        try:
            document_count = connection.execute(
                "SELECT COUNT(*) FROM note_documents"
            ).fetchone()[0]
            reference_count = connection.execute(
                "SELECT COUNT(*) FROM note_references"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual((document_count, reference_count), (0, 0))

    def test_wrong_plan_hash_never_mutates(self) -> None:
        initialized = self.run_cli(
            "registry-init",
            "--write",
            "--expect-plan-sha256",
            "0" * 64,
        )
        self.assertNotEqual(initialized.returncode, 0)
        self.assertFalse((self.root / ".mpk").exists())
        self.assertEqual(json.loads(self.config.read_text(encoding="utf-8"))["schema_version"], 1)

    def test_registry_scan_uses_existing_pdf_inventory_as_first_batch(self) -> None:
        (self.root / "a-ordinary.txt").write_text("ordinary", encoding="utf-8")
        (self.library / "z-second.pdf").write_bytes(b"second")
        self.apply_plan("registry-init", [])
        current_config = json.loads(self.config.read_text(encoding="utf-8"))
        index_library(
            self.library,
            self.state / "library.sqlite3",
            knowledge_root_id=str(current_config["knowledge_root_id"]),
            library_relative_path="Library",
            extractor=lambda path: f"indexed {path.name}",
        )

        first = self.apply_plan("registry-scan", ["--max-files", "1"])
        self.assertEqual(first["pdf_index_priority_count"], 2)
        first_id = str(first["registered"][0])
        first_resolved = self.payload(
            self.run_cli("resource-resolve", "--id", first_id)
        )
        self.assertEqual(first_resolved["relative_path"], "Library/sample.pdf")

        second = self.apply_plan(
            "registry-scan", ["--resume", "--max-files", "1"]
        )
        second_id = str(second["registered"][0])
        second_resolved = self.payload(
            self.run_cli("resource-resolve", "--id", second_id)
        )
        self.assertEqual(second_resolved["relative_path"], "Library/z-second.pdf")

    def test_resource_move_and_root_relink_update_links_and_refresh_index(self) -> None:
        self.apply_plan("registry-init", [])
        scanned = self.apply_plan("registry-scan", ["--max-files", "20"])
        resource_id = str(scanned["registered"][0])
        current_config = json.loads(self.config.read_text(encoding="utf-8"))
        index_library(
            self.library,
            self.state / "library.sqlite3",
            knowledge_root_id=str(current_config["knowledge_root_id"]),
            library_relative_path="Library",
            extractor=lambda _: "searchable CLI PDF body",
        )
        note = self.vault / "note.md"
        note.write_text(
            f"[sample]({self.document.resolve().as_uri()})<!-- mpk-resource:{resource_id} -->\n",
            encoding="utf-8",
        )
        post_sha = hashlib.sha256(note.read_bytes()).hexdigest()
        self.apply_plan(
            "reference-refresh",
            ["--note", str(note), "--post-sha256", post_sha],
        )

        receiver_config = self.base / "obsidian.json"
        kb_root = SKILL_ROOT.parent / "obsidian-vault-notes" / "scripts" / "obsidian_local_kb"
        receiver_config.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "vault_root": str(self.vault.resolve()),
                    "kb_root": str(kb_root.resolve()),
                    "db_path": str((self.base / "vault.sqlite3").resolve()),
                }
            ),
            encoding="utf-8",
        )

        moved_resource = self.apply_plan(
            "resource-move",
            [
                "--id",
                resource_id,
                "--to",
                "Library/renamed.pdf",
                "--obsidian-config",
                str(receiver_config),
            ],
        )
        self.assertEqual(moved_resource["state"], "applied")
        self.assertTrue(moved_resource["vault_index_refresh"]["ok"])
        self.assertTrue(moved_resource["pdf_index_update"]["preserves_extracted_text"])
        renamed = self.library / "renamed.pdf"
        self.assertIn(renamed.resolve().as_uri(), note.read_text(encoding="utf-8"))
        search = self.payload(
            self.run_cli("library-search", "--query", "searchable CLI PDF body")
        )
        self.assertEqual(search["results"][0]["path"], "renamed.pdf")
        page = self.payload(
            self.run_cli("page", "--id", resource_id, "--page", "1")
        )
        self.assertEqual(page["path"], "renamed.pdf")

        new_root = self.base / "MovedKnowledge"
        shutil.move(str(self.root), str(new_root))
        relinked = self.apply_plan(
            "root-relink",
            ["--root", str(new_root), "--obsidian-config", str(receiver_config)],
        )
        self.assertEqual(relinked["state"], "applied")
        self.assertTrue(relinked["vault_index_refresh"]["ok"])
        self.assertFalse(relinked["pdf_index_update"]["extracts_pdf_text"])
        current_note = new_root / "Vault" / "note.md"
        current_resource = new_root / "Library" / "renamed.pdf"
        self.assertIn(current_resource.resolve().as_uri(), current_note.read_text(encoding="utf-8"))
        current_config = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(Path(str(current_config["knowledge_root"])), new_root.resolve())
        resolved = self.payload(self.run_cli("resource-resolve", "--id", resource_id))
        self.assertEqual(Path(str(resolved["absolute_path"])), current_resource.resolve())
        after_relink = self.payload(
            self.run_cli("library-search", "--query", "searchable CLI PDF body")
        )
        self.assertEqual(after_relink["results"][0]["path"], "renamed.pdf")


if __name__ == "__main__":
    unittest.main()
