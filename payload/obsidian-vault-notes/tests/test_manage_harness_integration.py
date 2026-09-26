from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest


OBSIDIAN_ROOT = Path(__file__).resolve().parents[1]
VAULT_EDIT = OBSIDIAN_ROOT / "scripts" / "vault_edit.py"
MANAGE_CLI = OBSIDIAN_ROOT.parent / "manage-personal-knowledge" / "scripts" / "manage_kb.py"


@unittest.skipUnless(MANAGE_CLI.is_file(), "sibling manage-personal-knowledge skill is unavailable")
class RealManageHarnessIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "Knowledge"
        self.vault = self.root / "Vault"
        self.library = self.root / "Library"
        self.vault.mkdir(parents=True)
        (self.vault / ".obsidian").mkdir()
        self.library.mkdir()
        self.resource = self.library / "source.pdf"
        self.resource.write_bytes(b"source")
        self.note = self.vault / "note.md"
        self.note.write_text("Initial text.\n", encoding="utf-8")
        self.config = self.base / "config.json"
        self.state = self.base / "state"
        self.environment = os.environ.copy()
        self.environment.update(
            {
                "MPK_CONFIG_PATH": str(self.config),
                "MPK_STATE_DIR": str(self.state),
                "MPK_HARNESS": str(MANAGE_CLI),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUTF8": "1",
            }
        )
        configured = self.run_manage(
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
        self.apply_manage_plan("registry-init")
        scanned = self.apply_manage_plan("registry-scan", "--max-files", "20")
        self.resource_id = str(scanned["registered"][0])

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_manage(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-B", str(MANAGE_CLI), *arguments],
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self.environment,
            check=False,
        )

    def apply_manage_plan(self, *arguments: str) -> dict[str, object]:
        preview = self.run_manage(*arguments)
        self.assertEqual(preview.returncode, 0, preview.stderr)
        plan = json.loads(preview.stdout)
        written = self.run_manage(
            *arguments,
            "--write",
            "--expect-plan-sha256",
            str(plan["plan_sha256"]),
        )
        self.assertEqual(written.returncode, 0, written.stderr)
        return json.loads(written.stdout)

    def run_edit(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-B", str(VAULT_EDIT), *arguments],
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self.environment,
            check=False,
        )

    def test_valid_external_link_is_resolved_written_and_synchronized(self) -> None:
        payload_file = self.base / "payload.md"
        payload_file.write_text(
            f"[source]({self.resource.resolve().as_uri()})<!-- mpk-resource:{self.resource_id} -->",
            encoding="utf-8",
        )
        dry_run = self.run_edit(
            "append-section",
            "--file",
            str(self.note),
            "--vault-root",
            str(self.vault),
            "--text-file",
            str(payload_file),
        )
        self.assertEqual(dry_run.returncode, 0, dry_run.stderr + dry_run.stdout)
        dry_payload = json.loads(dry_run.stdout)
        self.assertEqual(dry_payload["resource_references"]["preflight"], "passed")

        written = self.run_edit(
            "append-section",
            "--file",
            str(self.note),
            "--vault-root",
            str(self.vault),
            "--text-file",
            str(payload_file),
            "--expect-sha256",
            str(dry_payload["pre_sha256"]),
            "--write",
        )
        self.assertEqual(written.returncode, 0, written.stderr + written.stdout)
        result = json.loads(written.stdout)
        self.assertTrue(result["applied"])
        self.assertEqual(result["resource_references"]["sync"]["status"], "synchronized")
        connection = sqlite3.connect(self.state / "registry.sqlite3")
        try:
            count = connection.execute("SELECT COUNT(*) FROM note_references").fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
