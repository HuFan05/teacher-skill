from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
SETUP = SCRIPTS / "setup_local.py"
RECALL = SCRIPTS / "recall_notes.py"


def clean_environment(config: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONUTF8"] = "1"
    environment["OBSIDIAN_VAULT_NOTES_CONFIG"] = str(config)
    environment.pop("OBSIDIAN_VAULT_ROOT", None)
    environment.pop("OBSIDIAN_LOCAL_KB_ROOT", None)
    environment.pop("OBSIDIAN_LOCAL_KB_DB", None)
    return environment


class LocalSetupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "路径 含空格"
        self.root.mkdir(parents=True)
        self.vault = self.root / "合成 Vault"
        self.vault.mkdir()
        (self.vault / "合成笔记.md").write_text(
            "---\ntags: [synthetic]\n---\n### 合成章节\nportable setup marker\n",
            encoding="utf-8",
        )
        self.config = self.root / "config" / "config.json"
        self.state = self.root / "state"
        self.environment = clean_environment(self.config)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_setup(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-B", str(SETUP), *arguments],
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
            env=self.environment,
        )

    def test_configure_build_check_and_recall(self) -> None:
        configured = self.run_setup(
            "configure",
            "--vault-root",
            str(self.vault),
            "--config",
            str(self.config),
            "--state-dir",
            str(self.state),
            "--build-index",
            "--yes",
        )
        self.assertEqual(configured.returncode, 0, configured.stderr)
        self.assertTrue(self.config.is_file())
        self.assertTrue((self.state / "vault.sqlite3").is_file())
        payload = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(Path(payload["vault_root"]), self.vault.resolve())
        self.assertEqual(Path(payload["kb_root"]), (SCRIPTS / "obsidian_local_kb").resolve())

        checked = self.run_setup(
            "check", "--config", str(self.config), "--state-dir", str(self.state), "--yes"
        )
        self.assertEqual(checked.returncode, 0, checked.stderr)

        recalled = subprocess.run(
            [sys.executable, "-B", str(RECALL), "--note", "合成笔记", "--whole-note"],
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
            env=self.environment,
        )
        self.assertEqual(recalled.returncode, 0, recalled.stderr)
        self.assertIn("portable setup marker", recalled.stdout)

    def test_environment_overrides_config_and_bundled_kb_is_default(self) -> None:
        other_vault = self.root / "环境变量 Vault"
        other_vault.mkdir()
        self.config.parent.mkdir(parents=True)
        self.config.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "vault_root": str(self.vault),
                    "kb_root": str(SCRIPTS / "obsidian_local_kb"),
                    "db_path": str(self.state / "vault.sqlite3"),
                }
            ),
            encoding="utf-8",
        )
        environment = self.environment.copy()
        environment["OBSIDIAN_VAULT_ROOT"] = str(other_vault)
        code = (
            "import json,sys;sys.path.insert(0,r'" + str(SCRIPTS) + "');"
            "import _common;print(json.dumps({'vault':str(_common.DEFAULT_VAULT_ROOT),"
            "'kb':str(_common.DEFAULT_KB_ROOT)}))"
        )
        completed = subprocess.run(
            [sys.executable, "-B", "-c", code],
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
            env=environment,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(Path(payload["vault"]), other_vault.resolve())
        self.assertEqual(Path(payload["kb"]), (SCRIPTS / "obsidian_local_kb").resolve())

    def test_missing_config_fails_and_purge_preserves_vault(self) -> None:
        checked = self.run_setup("check", "--config", str(self.config), "--state-dir", str(self.state))
        self.assertNotEqual(checked.returncode, 0)
        self.assertIn("config is missing", checked.stderr)

        configured = self.run_setup(
            "configure",
            "--vault-root",
            str(self.vault),
            "--config",
            str(self.config),
            "--state-dir",
            str(self.state),
            "--build-index",
            "--yes",
        )
        self.assertEqual(configured.returncode, 0, configured.stderr)
        removed = self.run_setup(
            "remove",
            "--config",
            str(self.config),
            "--state-dir",
            str(self.state),
            "--purge",
            "--yes",
        )
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertFalse(self.config.exists())
        self.assertFalse(self.state.exists())
        self.assertTrue(self.vault.is_dir())


if __name__ == "__main__":
    unittest.main()
