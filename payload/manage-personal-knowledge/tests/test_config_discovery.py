from __future__ import annotations

import json
import sys
import tempfile
from os.path import realpath as _realpath

# The platform temporary root can be a symlink (for example on macOS); resolve
# it so synthetic knowledge roots match the resolved paths the code compares.
tempfile.tempdir = _realpath(tempfile.gettempdir())
import unittest
from pathlib import Path
from unittest import mock


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from manage_personal_knowledge.config import (  # noqa: E402
    ConfigError,
    configure,
    forget_source,
    get_config_path,
    get_state_dir,
    load_config,
    migrate_to_schema_v2,
    relink,
    relink_source,
    resolve_source,
    save_config,
    status,
)
from manage_personal_knowledge.discovery import discover_sources  # noqa: E402


def touch(path: Path, content: bytes = b"%PDF-test") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


class DiscoveryTests(unittest.TestCase):
    def test_unique_discovery_excludes_vault_pdfs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vault = root / "MEGA" / "Obsidian Vault"
            (vault / ".obsidian").mkdir(parents=True)
            touch(vault / "attachments" / "not-library.pdf")
            library = root / "数字图书馆"
            touch(library / "papers" / "one.pdf")
            touch(library / "two.PDF")

            result = discover_sources(root)

            self.assertTrue(result["confirmation_required"])
            self.assertEqual(result["counts"]["vault_candidates"], 1)
            self.assertEqual(result["counts"]["library_candidates"], 1)
            self.assertEqual(result["counts"]["pdf_files_excluding_vaults"], 2)
            self.assertEqual(result["vault_candidates"][0]["relative_path"], "MEGA/Obsidian Vault")
            self.assertEqual(result["library_candidates"][0]["relative_path"], "数字图书馆")
            self.assertEqual(result["library_candidates"][0]["pdf_count"], 2)
            json.dumps(result, ensure_ascii=False)

    def test_ambiguous_discovery_reports_all_independent_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("Vault A", "Vault B"):
                (root / name / ".obsidian").mkdir(parents=True)
            touch(root / "Books A" / "a.pdf")
            touch(root / "Books B" / "b.pdf")

            result = discover_sources(root)

            self.assertTrue(result["ambiguous"]["vault"])
            self.assertTrue(result["ambiguous"]["library"])
            self.assertEqual(result["counts"]["vault_candidates"], 2)
            self.assertEqual(result["counts"]["library_candidates"], 2)
            self.assertEqual(
                {item["relative_path"] for item in result["library_candidates"]},
                {"Books A", "Books B"},
            )


class ConfigurationTests(unittest.TestCase):
    ROOT_ID = "KBROOT-" + ("A" * 26)

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.root = self.base / "knowledge"
        self.vault = self.root / "MEGA" / "Vault"
        self.library = self.root / "Library"
        (self.vault / ".obsidian").mkdir(parents=True)
        self.library.mkdir(parents=True)
        self.config_path = self.base / "appdata" / "config.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_environment_paths_and_confirmed_persistence(self) -> None:
        env = {"APPDATA": str(self.base / "roaming"), "LOCALAPPDATA": str(self.base / "local")}
        with mock.patch("manage_personal_knowledge.config.sys.platform", "win32"):
            self.assertEqual(
                get_config_path(env),
                (self.base / "roaming" / "manage-personal-knowledge" / "config.json").resolve(),
            )
            self.assertEqual(get_state_dir(env), (self.base / "local" / "manage-personal-knowledge").resolve())
        self.assertEqual(
            get_config_path({"MPK_CONFIG_PATH": str(self.base / "custom.json")}),
            (self.base / "custom.json").resolve(),
        )
        self.assertEqual(
            get_state_dir({"MPK_STATE_DIR": str(self.base / "custom-state")}),
            (self.base / "custom-state").resolve(),
        )

        with mock.patch("manage_personal_knowledge.config.sys.platform", "linux"), mock.patch(
            "manage_personal_knowledge.config.Path.home", return_value=self.base
        ):
            self.assertEqual(
                get_config_path({"XDG_CONFIG_HOME": str(self.base / "xdg-config")}),
                (self.base / "xdg-config" / "manage-personal-knowledge" / "config.json").resolve(),
            )
            self.assertEqual(
                get_state_dir({"XDG_STATE_HOME": str(self.base / "xdg-state")}),
                (self.base / "xdg-state" / "manage-personal-knowledge").resolve(),
            )

        with self.assertRaises(ConfigError):
            configure(self.root, self.vault, self.library, config_path=self.config_path)
        self.assertFalse(self.config_path.exists())

        written = configure(
            self.root, self.vault, self.library, confirmed=True, config_path=self.config_path
        )
        self.assertEqual(written["schema_version"], 1)
        self.assertEqual(written["sources"]["vault"]["kind"], "obsidian-vault")
        self.assertEqual(written["sources"]["vault"]["relative_path"], "MEGA/Vault")
        self.assertEqual(written["sources"]["library"]["kind"], "pdf-library")
        self.assertEqual(load_config(self.config_path), written)
        self.assertEqual(resolve_source(written, "library", strict=True), self.library.resolve())
        self.assertEqual(list(self.config_path.parent.glob("*.tmp")), [])

    def test_configure_accepts_root_relative_source_paths(self) -> None:
        written = configure(
            self.root,
            "MEGA/Vault",
            "Library",
            confirmed=True,
            config_path=self.config_path,
        )

        self.assertEqual(resolve_source(written, "vault", strict=True), self.vault.resolve())
        self.assertEqual(resolve_source(written, "library", strict=True), self.library.resolve())
        self.assertEqual(written["sources"]["vault"]["relative_path"], "MEGA/Vault")
        self.assertEqual(written["sources"]["library"]["relative_path"], "Library")

    def test_schema_v2_migration_is_explicit_and_preserves_sources(self) -> None:
        legacy = configure(
            self.root, self.vault, self.library, confirmed=True, config_path=self.config_path
        )
        with self.assertRaises(ConfigError):
            migrate_to_schema_v2(
                knowledge_root_id=self.ROOT_ID,
                config_path=self.config_path,
            )
        self.assertEqual(load_config(self.config_path), legacy)

        migrated = migrate_to_schema_v2(
            knowledge_root_id=self.ROOT_ID,
            manifest_relative_path=r".mpk\resources.jsonl",
            excluded_relative_paths=[r"generated\cache", "Archive"],
            confirmed=True,
            config_path=self.config_path,
        )

        self.assertEqual(migrated["schema_version"], 2)
        self.assertEqual(migrated["knowledge_root_name"], self.root.name)
        self.assertEqual(migrated["knowledge_root_id"], self.ROOT_ID)
        self.assertEqual(
            migrated["registry"],
            {
                "manifest_relative_path": ".mpk/resources.jsonl",
                "excluded_relative_paths": ["generated/cache", "Archive"],
            },
        )
        self.assertEqual(resolve_source(migrated, "vault"), self.vault.resolve())
        self.assertEqual(resolve_source(migrated, "library"), self.library.resolve())
        self.assertFalse((self.root / ".mpk").exists())
        report = status(self.config_path)
        self.assertEqual(report["knowledge_root_id"], self.ROOT_ID)
        self.assertEqual(report["registry"], migrated["registry"])

    def test_schema_v2_validation_rejects_invalid_identity_registry_and_source(self) -> None:
        base = {
            "schema_version": 2,
            "knowledge_root": str(self.root.resolve()),
            "knowledge_root_name": "本地知识库",
            "knowledge_root_id": self.ROOT_ID,
            "registry": {
                "manifest_relative_path": ".mpk/resources.jsonl",
                "excluded_relative_paths": [],
            },
            "sources": {
                "vault": {"kind": "obsidian-vault", "relative_path": "MEGA/Vault"},
                "library": {"kind": "pdf-library", "relative_path": "Library"},
            },
        }
        invalid_changes = (
            ("root name", {"knowledge_root_name": "../knowledge"}),
            ("root id", {"knowledge_root_id": "KBROOT-invalid"}),
            ("registry missing", {"registry": {"excluded_relative_paths": []}}),
            (
                "registry escape",
                {
                    "registry": {
                        "manifest_relative_path": "../resources.jsonl",
                        "excluded_relative_paths": [],
                    }
                },
            ),
            (
                "duplicate exclusions",
                {
                    "registry": {
                        "manifest_relative_path": ".mpk/resources.jsonl",
                        "excluded_relative_paths": ["Cache", "cache"],
                    }
                },
            ),
            (
                "source escape",
                {
                    "sources": {
                        "vault": {
                            "kind": "obsidian-vault",
                            "relative_path": "../Vault",
                        }
                    }
                },
            ),
        )
        for label, change in invalid_changes:
            candidate = json.loads(json.dumps(base))
            candidate.update(change)
            with self.subTest(label=label), self.assertRaises(ConfigError):
                save_config(candidate, self.config_path)

    def test_schema_v2_cannot_be_bypassed_by_legacy_config_mutators(self) -> None:
        configure(
            self.root,
            self.vault,
            self.library,
            confirmed=True,
            config_path=self.config_path,
        )
        migrated = migrate_to_schema_v2(
            knowledge_root_id=self.ROOT_ID,
            confirmed=True,
            config_path=self.config_path,
        )
        replacement = self.root / "Replacement Library"
        replacement.mkdir()
        moved_root = self.base / "Copied Knowledge"
        copied_vault = moved_root / "Vault"
        copied_library = moved_root / "Library"
        (copied_vault / ".obsidian").mkdir(parents=True)
        copied_library.mkdir(parents=True)

        blocked = (
            lambda: configure(
                moved_root,
                copied_vault,
                copied_library,
                confirmed=True,
                config_path=self.config_path,
            ),
            lambda: relink_source(
                "library",
                replacement,
                confirmed=True,
                config_path=self.config_path,
            ),
            lambda: relink(
                knowledge_root=moved_root,
                vault_path=copied_vault,
                library_path=copied_library,
                confirmed=True,
                config_path=self.config_path,
            ),
            lambda: forget_source(
                "library",
                confirmed=True,
                config_path=self.config_path,
            ),
        )
        for operation in blocked:
            with self.assertRaises(ConfigError):
                operation()
            self.assertEqual(load_config(self.config_path), migrated)

    def test_sources_must_be_contained_by_root(self) -> None:
        outside = self.base / "outside-library"
        outside.mkdir()
        with self.assertRaises(ConfigError):
            configure(
                self.root, self.vault, outside, confirmed=True, config_path=self.config_path
            )
        self.assertFalse(self.config_path.exists())

        unsafe = {
            "schema_version": 1,
            "knowledge_root": str(self.root.resolve()),
            "sources": {"library": {"kind": "pdf-library", "relative_path": "../outside-library"}},
        }
        self.config_path.parent.mkdir(parents=True)
        self.config_path.write_text(json.dumps(unsafe), encoding="utf-8")
        with self.assertRaises(ConfigError):
            load_config(self.config_path)

    def test_vault_and_library_cannot_be_nested_in_either_direction(self) -> None:
        outer_library = self.root / "Outer Library"
        nested_vault = outer_library / "Vault"
        (nested_vault / ".obsidian").mkdir(parents=True)
        with self.assertRaises(ConfigError):
            configure(
                self.root,
                nested_vault,
                outer_library,
                confirmed=True,
                config_path=self.config_path,
            )

    def test_missing_source_reports_rediscovery_without_rewriting(self) -> None:
        configure(
            self.root, self.vault, self.library, confirmed=True, config_path=self.config_path
        )
        original_bytes = self.config_path.read_bytes()
        moved_library = self.root / "Moved Library"
        self.library.rename(moved_library)
        touch(moved_library / "book.pdf")

        with mock.patch("manage_personal_knowledge.discovery.discover_sources", side_effect=AssertionError("unexpected discovery")):
            initial = status(self.config_path)
        self.assertIsNone(initial["rediscovery"])
        self.assertIn("library", initial["missing"])
        report = status(self.config_path, rediscover=True)

        self.assertFalse(report["healthy"])
        self.assertIn("library", report["missing"])
        self.assertEqual(report["sources"]["library"]["state"], "missing")
        self.assertIsNotNone(report["rediscovery"])
        self.assertIn(
            "Moved Library",
            {item["relative_path"] for item in report["rediscovery"]["library_candidates"]},
        )
        self.assertEqual(self.config_path.read_bytes(), original_bytes)

    def test_relink_and_forget_are_explicit(self) -> None:
        configure(
            self.root, self.vault, self.library, confirmed=True, config_path=self.config_path
        )
        replacement = self.root / "Replacement Library"
        replacement.mkdir()

        with self.assertRaises(ConfigError):
            relink_source("library", replacement, config_path=self.config_path)
        current = load_config(self.config_path)
        self.assertIsNotNone(current)
        self.assertEqual(resolve_source(current, "library"), self.library.resolve())

        relinked = relink_source(
            "library", replacement, confirmed=True, config_path=self.config_path
        )
        self.assertEqual(resolve_source(relinked, "library"), replacement.resolve())

        with self.assertRaises(ConfigError):
            forget_source("library", config_path=self.config_path)
        forgotten = forget_source("library", confirmed=True, config_path=self.config_path)
        self.assertNotIn("library", forgotten["sources"])
        report = status(self.config_path)
        self.assertIn("library", report["missing"])
        self.assertEqual(report["sources"]["library"]["state"], "unconfigured")


if __name__ == "__main__":
    unittest.main()
