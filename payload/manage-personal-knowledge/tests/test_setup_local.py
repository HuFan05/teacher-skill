from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from os.path import realpath as _realpath

# The platform temporary root can be a symlink (for example on macOS); resolve
# it so synthetic knowledge roots match the resolved paths the code compares.
tempfile.tempdir = _realpath(tempfile.gettempdir())
import unittest


SKILL_ROOT = Path(__file__).resolve().parents[1]
SETUP = SKILL_ROOT / "scripts" / "setup_local.py"


class SetupLocalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "知识库"
        self.vault = self.root / "Notes"
        self.library = self.root / "PDF Library"
        (self.vault / ".obsidian").mkdir(parents=True)
        self.library.mkdir(parents=True)
        (self.library / "sample.pdf").write_bytes(b"%PDF-synthetic")
        self.config = self.base / "config" / "config.json"
        self.state = self.base / "state"
        self.skills_root = self.base / "skills"
        receiver_scripts = self.skills_root / "obsidian-vault-notes" / "scripts"
        receiver_module = receiver_scripts / "obsidian_local_kb" / "obsidian_local_kb"
        receiver_module.mkdir(parents=True)
        (receiver_module / "__main__.py").write_text("", encoding="utf-8")
        (receiver_scripts / "setup_local.py").write_text(
            """import argparse, json\nfrom pathlib import Path\np=argparse.ArgumentParser(); p.add_argument('mode'); p.add_argument('--vault-root'); p.add_argument('--config'); p.add_argument('--state-dir'); p.add_argument('--build-index', action='store_true'); p.add_argument('--yes', action='store_true'); a=p.parse_args()\nconfig=Path(a.config); state=Path(a.state_dir)\nif a.mode == 'configure':\n config.parent.mkdir(parents=True, exist_ok=True); state.mkdir(parents=True, exist_ok=True); config.write_text(json.dumps({'schema_version':1,'vault_root':a.vault_root,'db_path':str(state/'vault.sqlite3')}), encoding='utf-8')\nprint(json.dumps({'status':'configured','config':str(config),'state_dir':str(state)}))\n""",
            encoding="utf-8",
        )
        self.env = os.environ.copy()
        self.env.update(
            {
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUTF8": "1",
                "MPK_SKILLS_ROOT": str(self.skills_root),
            }
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_setup(self, *parts: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-B", str(SETUP), *parts, "--config", str(self.config), "--state-dir", str(self.state)],
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self.env,
            check=False,
        )

    def test_check_requires_setup_without_writing(self) -> None:
        result = self.run_setup("check")
        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "setup_required")
        self.assertFalse(self.config.exists())

    def test_noninteractive_setup_requires_exact_sources(self) -> None:
        result = self.run_setup("setup", "--knowledge-root", str(self.root), "--yes")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.config.exists())

    def test_noninteractive_setup_configures_without_index_or_registry(self) -> None:
        result = self.run_setup(
            "setup", "--knowledge-root", str(self.root), "--vault", "Notes",
            "--library", "PDF Library", "--yes",
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "configured")
        self.assertFalse(payload["pdf_index_built"])
        self.assertFalse(payload["registry_initialized"])
        self.assertFalse((self.root / ".mpk").exists())
        healthy = self.run_setup("check")
        self.assertEqual(healthy.returncode, 0, healthy.stdout)

    def test_missing_root_requires_user_supplied_repair(self) -> None:
        configured = self.run_setup(
            "setup", "--knowledge-root", str(self.root), "--vault", "Notes",
            "--library", "PDF Library", "--yes",
        )
        self.assertEqual(configured.returncode, 0, configured.stdout)
        moved = self.base / "Moved Knowledge"
        self.root.rename(moved)
        report = self.run_setup("check")
        self.assertEqual(report.returncode, 2)
        self.assertEqual(json.loads(report.stdout)["status"], "setup_required")
        repaired = self.run_setup(
            "repair", "--knowledge-root", str(moved), "--vault", "Notes",
            "--library", "PDF Library", "--yes",
        )
        self.assertEqual(repaired.returncode, 0, repaired.stderr + repaired.stdout)

    def test_remove_preserves_by_default_and_purges_suite_owned_state_explicitly(self) -> None:
        configured = self.run_setup(
            "setup", "--knowledge-root", str(self.root), "--vault", "Notes",
            "--library", "PDF Library", "--yes",
        )
        self.assertEqual(configured.returncode, 0, configured.stderr + configured.stdout)
        receiver_config = self.config.parent / "obsidian-vault-notes.json"
        self.assertTrue(receiver_config.is_file())
        preserved = self.run_setup("remove")
        self.assertEqual(preserved.returncode, 0, preserved.stdout)
        self.assertTrue(self.config.is_file())
        self.assertTrue(receiver_config.is_file())
        purged = self.run_setup("remove", "--purge", "--yes")
        self.assertEqual(purged.returncode, 0, purged.stderr + purged.stdout)
        self.assertFalse(self.config.exists())
        self.assertFalse(receiver_config.exists())
        self.assertFalse(self.state.exists())


if __name__ == "__main__":
    unittest.main()
