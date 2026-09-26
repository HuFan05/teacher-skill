from __future__ import annotations

import json
import os
import re
import sqlite3
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

import manage_personal_knowledge.registry as registry_module  # noqa: E402
from manage_personal_knowledge.registry import (  # noqa: E402
    RegistryGenerationError,
    RegistryPlanError,
    RegistrySafetyError,
    generate_resource_id,
    registry_export,
    registry_hash,
    registry_init,
    registry_restore,
    registry_scan,
    registry_status,
    resource_audit,
    resource_register,
    resource_resolve,
    resource_retire,
    resource_search,
)


def apply(operation, *args, **kwargs):
    preview = operation(*args, **kwargs)
    return operation(
        *args,
        **kwargs,
        write=True,
        expect_plan_sha256=preview["plan_sha256"],
    )


class RegistryFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.root = self.base / "knowledge"
        self.root.mkdir()
        self.vault = self.root / "Vault"
        (self.vault / ".obsidian").mkdir(parents=True)
        self.database = self.base / "state" / "registry.sqlite3"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def initialise(self):
        return apply(
            registry_init,
            self.root,
            self.database,
            vault_relative_path="Vault",
            excluded_relative_paths=["scratch"],
        )

    def scan(self, **kwargs):
        return apply(registry_scan, self.root, self.database, **kwargs)


class IdentityAndInitialisationTests(RegistryFixture):
    def test_ids_are_valid_random_base32(self) -> None:
        values = {generate_resource_id() for _ in range(100)}
        self.assertEqual(len(values), 100)
        self.assertTrue(all(re.fullmatch(r"KB-[A-Z2-7]{26}", item) for item in values))

    def test_init_requires_exact_plan_and_creates_empty_synced_manifest(self) -> None:
        preview = registry_init(
            self.root,
            self.database,
            vault_relative_path="Vault",
            excluded_relative_paths=["scratch"],
        )
        self.assertEqual(preview["inventory_preview"]["eligible_file_count"], 0)
        self.assertIn("Vault", {item["relative_path"] for item in preview["inventory_preview"]["excluded_roots"]})
        self.assertFalse(self.database.exists())
        self.assertFalse((self.root / ".mpk").exists())
        with self.assertRaises(RegistryPlanError):
            registry_init(
                self.root,
                self.database,
                vault_relative_path="Vault",
                excluded_relative_paths=["scratch"],
                write=True,
                expect_plan_sha256="0" * 64,
            )
        result = registry_init(
            self.root,
            self.database,
            vault_relative_path="Vault",
            excluded_relative_paths=["scratch"],
            write=True,
            expect_plan_sha256=preview["plan_sha256"],
        )
        marker = json.loads((self.root / ".mpk" / "root.json").read_text(encoding="utf-8"))
        self.assertEqual(marker["knowledge_root_id"], result["knowledge_root_id"])
        report = registry_status(self.root, self.database)
        self.assertEqual(report["total"], 0)
        self.assertEqual(report["manifest"]["state"], "in_sync")

    def test_init_dry_run_does_not_create_schema_in_existing_sqlite(self) -> None:
        self.database.parent.mkdir(parents=True)
        connection = sqlite3.connect(self.database)
        connection.close()
        before = self.database.read_bytes()
        preview = registry_init(
            self.root,
            self.database,
            vault_relative_path="Vault",
            excluded_relative_paths=["scratch"],
        )
        self.assertTrue(preview["dry_run"])
        self.assertEqual(self.database.read_bytes(), before)
        connection = sqlite3.connect(f"file:{self.database.as_posix()}?mode=ro", uri=True)
        try:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0],
                0,
            )
        finally:
            connection.close()


class ScanHashAndAuditTests(RegistryFixture):
    def test_scan_excludes_vault_management_cache_and_configured_paths(self) -> None:
        (self.root / "资料" / "sub").mkdir(parents=True)
        (self.root / "资料" / "sub" / "book.pdf").write_bytes(b"pdf-one")
        (self.root / "资料" / "picture.png").write_bytes(b"png")
        (self.vault / "attachment.pdf").write_bytes(b"vault")
        (self.root / ".git").mkdir()
        (self.root / ".git" / "index").write_bytes(b"state")
        (self.root / "scratch").mkdir()
        (self.root / "scratch" / "draft.txt").write_text("draft", encoding="utf-8")
        (self.root / "unfinished.part").write_bytes(b"part")
        (self.root / ".megaignore").write_text("/Vault", encoding="utf-8")
        self.initialise()

        result = self.scan(max_files=10)
        self.assertEqual(len(result["registered"]), 2)
        found = resource_search(self.database, "资料")
        self.assertEqual({item["relative_path"] for item in found}, {"资料/picture.png", "资料/sub/book.pdf"})
        status = registry_status(self.root, self.database)
        self.assertEqual(status["total"], 2)
        self.assertGreaterEqual(status["excluded_count"], 5)
        self.assertEqual(status["conflict_count"], 0)
        self.assertEqual(status["reference_sync_pending_count"], 0)
        self.assertGreaterEqual(status["inventory_preview"]["excluded_root_count"], 3)
        self.assertGreaterEqual(status["inventory_preview"]["excluded_file_count"], 2)

    def test_scan_resume_is_bounded_and_preserves_ids(self) -> None:
        for index in range(5):
            (self.root / f"file-{index}.txt").write_text(str(index), encoding="utf-8")
        self.initialise()

        first = self.scan(max_files=2)
        self.assertTrue(first["has_more"])
        second = self.scan(resume=True, max_files=2)
        self.assertTrue(second["has_more"])
        third = self.scan(resume=True, max_files=2)
        self.assertFalse(third["has_more"])
        ids_before = {item["relative_path"]: item["resource_id"] for item in resource_search(self.database, "file-")}

        self.scan(max_files=10)
        ids_after = {item["relative_path"]: item["resource_id"] for item in resource_search(self.database, "file-")}
        self.assertEqual(ids_after, ids_before)

    def test_scan_resume_cursor_matches_global_path_order(self) -> None:
        (self.root / "a").mkdir()
        (self.root / "a" / "z.pdf").write_bytes(b"nested")
        (self.root / "a.pdf").write_bytes(b"sibling")
        (self.root / "b.pdf").write_bytes(b"later")
        self.initialise()

        while True:
            result = self.scan(resume=True, max_files=1)
            if not result["has_more"]:
                break

        self.assertEqual(
            {item["relative_path"] for item in resource_search(self.database, ".pdf")},
            {"a.pdf", "a/z.pdf", "b.pdf"},
        )

    def test_scan_prioritizes_indexed_pdfs_and_resumes_across_priority_phases(self) -> None:
        (self.root / "Library").mkdir()
        (self.root / "Library" / "z.pdf").write_bytes(b"z")
        (self.root / "Library" / "y.pdf").write_bytes(b"y")
        (self.root / "a-first.txt").write_text("a", encoding="utf-8")
        self.initialise()
        priority = ["Library/z.pdf", "Library/y.pdf"]

        first = self.scan(max_files=1, priority_relative_paths=priority)
        self.assertTrue(first["has_more"])
        self.assertEqual(
            [item["relative_path"] for item in resource_search(self.database, "y.pdf")],
            ["Library/y.pdf"],
        )
        self.assertFalse(resource_search(self.database, "a-first.txt"))

        second = self.scan(
            resume=True,
            max_files=1,
            priority_relative_paths=priority,
        )
        self.assertTrue(second["has_more"])
        self.assertEqual(
            [item["relative_path"] for item in resource_search(self.database, "z.pdf")],
            ["Library/z.pdf"],
        )
        third = self.scan(
            resume=True,
            max_files=1,
            priority_relative_paths=priority,
        )
        self.assertFalse(third["has_more"])
        self.assertEqual(
            [item["relative_path"] for item in resource_search(self.database, "a-first.txt")],
            ["a-first.txt"],
        )

    def test_scan_resume_stops_if_pdf_priority_digest_changes(self) -> None:
        (self.root / "Library").mkdir()
        (self.root / "Library" / "one.pdf").write_bytes(b"one")
        (self.root / "Library" / "two.pdf").write_bytes(b"two")
        (self.root / "ordinary.txt").write_text("ordinary", encoding="utf-8")
        self.initialise()
        self.scan(
            max_files=1,
            priority_relative_paths=["Library/one.pdf", "Library/two.pdf"],
        )
        with self.assertRaisesRegex(RegistrySafetyError, "priority changed"):
            registry_scan(
                self.root,
                self.database,
                resume=True,
                max_files=1,
                priority_relative_paths=["Library/two.pdf"],
            )

    def test_hash_versions_content_and_reports_duplicates_without_merging(self) -> None:
        (self.root / "one.bin").write_bytes(b"same")
        (self.root / "two.bin").write_bytes(b"same")
        self.initialise()
        self.scan(max_files=10)
        hashed = apply(registry_hash, self.root, self.database, max_files=10)
        self.assertEqual(len(hashed["verified"]), 2)
        audit = resource_audit(self.root, self.database)
        self.assertEqual(len(audit["duplicate_candidates"]), 1)
        first_id = resource_search(self.database, "one.bin")[0]["resource_id"]
        before = resource_resolve(self.root, self.database, first_id)
        self.assertEqual(before["content_version"], 1)

        (self.root / "one.bin").write_bytes(b"changed")
        self.scan(max_files=10)
        apply(registry_hash, self.root, self.database, max_files=10)
        after = resource_resolve(self.root, self.database, first_id)
        self.assertEqual(after["content_version"], 2)
        self.assertEqual(len(after["versions"]), 2)

    def test_hash_rechecks_registered_path_after_plan_confirmation(self) -> None:
        original = self.root / "original.bin"
        replacement = self.root / "replacement.bin"
        original.write_bytes(b"same")
        replacement.write_bytes(b"same")
        self.initialise()
        self.scan(max_files=1)
        identifier = resource_search(self.database, "original.bin")[0]["resource_id"]
        preview = registry_hash(self.root, self.database, max_files=10)
        original_confirm = registry_module._confirm_plan

        def mutate_registry(plan, expected):
            original_confirm(plan, expected)
            connection = sqlite3.connect(self.database)
            try:
                connection.execute(
                    "UPDATE resources SET relative_path = ? WHERE resource_id = ?",
                    ("replacement.bin", identifier),
                )
                connection.commit()
            finally:
                connection.close()

        with mock.patch.object(
            registry_module, "_confirm_plan", side_effect=mutate_registry
        ), self.assertRaises(RegistryPlanError):
            registry_hash(
                self.root,
                self.database,
                max_files=10,
                write=True,
                expect_plan_sha256=preview["plan_sha256"],
            )

    def test_verify_all_detects_content_change_with_preserved_size_and_mtime(self) -> None:
        path = self.root / "preserved.bin"
        path.write_bytes(b"aaaa")
        self.initialise()
        self.scan(max_files=10)
        first = apply(registry_hash, self.root, self.database, max_files=10)
        identifier = first["verified"][0]
        recorded_mtime = path.stat().st_mtime_ns

        path.write_bytes(b"bbbb")
        os.utime(path, ns=(recorded_mtime, recorded_mtime))
        ordinary = registry_hash(self.root, self.database, max_files=10)
        self.assertEqual(ordinary["processed"], 0)

        verified = apply(
            registry_hash,
            self.root,
            self.database,
            max_files=10,
            verify_all=True,
        )
        self.assertEqual(verified["verified"], [identifier])
        resolved = resource_resolve(self.root, self.database, identifier)
        self.assertEqual(resolved["content_version"], 2)
        self.assertEqual(len(resolved["versions"]), 2)

    def test_missing_and_unique_hash_manual_move_are_only_audit_candidates(self) -> None:
        original = self.root / "original.pdf"
        original.write_bytes(b"move-me")
        self.initialise()
        self.scan(max_files=10)
        apply(registry_hash, self.root, self.database, max_files=10)
        identifier = resource_search(self.database, "original")[0]["resource_id"]
        moved = self.root / "moved.pdf"
        original.rename(moved)

        audit = resource_audit(self.root, self.database)
        self.assertIn("moved.pdf", audit["unregistered"])
        self.assertIn(identifier, audit["missing"])
        self.assertEqual(audit["manual_move_candidates"][0]["resource_id"], identifier)
        self.assertEqual(resource_resolve(self.root, self.database, identifier)["relative_path"], "original.pdf")


class CommandsAndRecoveryTests(RegistryFixture):
    def test_register_resolve_search_and_retire(self) -> None:
        path = self.root / "papers" / "named.pdf"
        path.parent.mkdir()
        path.write_bytes(b"paper")
        self.initialise()
        result = apply(
            resource_register,
            self.root,
            self.database,
            path,
            doi="10.1000/test",
            source_url="https://example.test/paper",
        )
        resolved = resource_resolve(self.root, self.database, result["resource_id"])
        self.assertTrue(resolved["exists"])
        self.assertEqual(resolved["relative_path"], "papers/named.pdf")
        self.assertTrue(resolved["file_uri"].startswith("file:///"))
        self.assertEqual(resource_search(self.database, "10.1000/test")[0]["resource_id"], result["resource_id"])
        retired = apply(resource_retire, self.root, self.database, result["resource_id"])
        self.assertTrue(retired["changed"])
        self.assertEqual(resource_resolve(self.root, self.database, result["resource_id"])["status"], "retired")
        self.assertTrue(path.exists())

    def test_register_rechecks_file_after_plan_confirmation(self) -> None:
        path = self.root / "new-resource.bin"
        path.write_bytes(b"initial")
        self.initialise()
        preview = resource_register(self.root, self.database, path)
        original_confirm = registry_module._confirm_plan

        def replace_file(plan, expected):
            original_confirm(plan, expected)
            path.write_bytes(b"changed-after-preview")

        with mock.patch.object(
            registry_module, "_confirm_plan", side_effect=replace_file
        ), self.assertRaises(RegistryPlanError):
            resource_register(
                self.root,
                self.database,
                path,
                write=True,
                expect_plan_sha256=preview["plan_sha256"],
            )
        self.assertFalse(resource_search(self.database, "new-resource.bin"))

    def test_generation_mismatch_blocks_writes_and_export_repairs_it(self) -> None:
        file_path = self.root / "first.txt"
        file_path.write_text("one", encoding="utf-8")
        self.initialise()
        self.scan(max_files=10)
        manifest = self.root / ".mpk" / "resources.jsonl"
        lines = manifest.read_text(encoding="utf-8").splitlines()
        header = json.loads(lines[0])
        header["generation"] = -1
        manifest.write_text("\n".join([json.dumps(header), *lines[1:]]) + "\n", encoding="utf-8")
        (self.root / "second.txt").write_text("two", encoding="utf-8")

        with self.assertRaises(RegistryGenerationError):
            registry_scan(self.root, self.database)
        apply(registry_export, self.root, self.database)
        self.assertEqual(registry_status(self.root, self.database)["manifest"]["state"], "in_sync")

    def test_restore_rebuilds_a_new_database_from_manifest(self) -> None:
        (self.root / "book.pdf").write_bytes(b"book")
        self.initialise()
        self.scan(max_files=10)
        apply(registry_hash, self.root, self.database, max_files=10)
        old = resource_search(self.database, "book.pdf")[0]["resource_id"]
        restored_database = self.base / "other-state" / "registry.sqlite3"

        result = apply(registry_restore, self.root, restored_database)
        self.assertEqual(result["restored"], 1)
        restored = resource_resolve(self.root, restored_database, old)
        self.assertEqual(restored["sha256"], resource_resolve(self.root, self.database, old)["sha256"])
        self.assertEqual(registry_status(self.root, restored_database)["manifest"]["state"], "in_sync")

    def test_restore_dry_run_is_read_only_and_manifest_change_invalidates_plan(self) -> None:
        (self.root / "book.pdf").write_bytes(b"book")
        self.initialise()
        self.scan(max_files=10)
        before = self.database.read_bytes()
        before_mtime = self.database.stat().st_mtime_ns
        preview = registry_restore(self.root, self.database, replace=True)
        self.assertEqual(self.database.read_bytes(), before)
        self.assertEqual(self.database.stat().st_mtime_ns, before_mtime)

        manifest = self.root / ".mpk" / "resources.jsonl"
        lines = manifest.read_text(encoding="utf-8").splitlines()
        record = json.loads(lines[1])
        record["display_name"] = "changed-after-plan.pdf"
        manifest.write_text(
            "\n".join([lines[0], json.dumps(record, ensure_ascii=False, sort_keys=True)]) + "\n",
            encoding="utf-8",
        )
        with self.assertRaises(RegistryPlanError):
            registry_restore(
                self.root,
                self.database,
                replace=True,
                write=True,
                expect_plan_sha256=preview["plan_sha256"],
            )

    def test_restore_recreates_missing_root_marker_from_manifest(self) -> None:
        (self.root / "book.pdf").write_bytes(b"book")
        initialized = self.initialise()
        self.scan(max_files=10)
        (self.root / ".mpk" / "root.json").unlink()
        restored_database = self.base / "fresh-state" / "registry.sqlite3"

        result = apply(registry_restore, self.root, restored_database)

        marker = json.loads((self.root / ".mpk" / "root.json").read_text(encoding="utf-8"))
        self.assertEqual(marker["knowledge_root_id"], initialized["knowledge_root_id"])
        self.assertEqual(result["restored"], 1)

    def test_init_refuses_to_overwrite_portable_manifest_when_database_is_missing(self) -> None:
        (self.root / "book.pdf").write_bytes(b"book")
        self.initialise()
        self.scan(max_files=10)
        self.database.unlink()

        with self.assertRaises(RegistrySafetyError):
            registry_init(
                self.root,
                self.database,
                vault_relative_path="Vault",
                excluded_relative_paths=["scratch"],
            )

        manifest_lines = (self.root / ".mpk" / "resources.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(manifest_lines), 2)

    def test_restore_rejects_resource_inside_vault_or_excluded_path(self) -> None:
        (self.root / "book.pdf").write_bytes(b"book")
        self.initialise()
        self.scan(max_files=10)
        manifest = self.root / ".mpk" / "resources.jsonl"
        lines = manifest.read_text(encoding="utf-8").splitlines()
        for unsafe_path in ("Vault/attachment.pdf", "scratch/cache.pdf", ".mpk/tool.json"):
            tampered = json.loads(lines[1])
            tampered["relative_path"] = unsafe_path
            tampered["path_history"][-1]["relative_path"] = unsafe_path
            manifest.write_text(
                "\n".join([lines[0], json.dumps(tampered, ensure_ascii=False, sort_keys=True)]) + "\n",
                encoding="utf-8",
            )
            with self.subTest(path=unsafe_path), self.assertRaises(RegistrySafetyError):
                registry_restore(self.root, self.base / f"restore-{unsafe_path.split('/')[0]}.sqlite3")
        manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_soft_excluded_file_cannot_be_registered_directly(self) -> None:
        path = self.root / "download.part"
        path.write_bytes(b"partial")
        self.initialise()
        with self.assertRaises(RegistrySafetyError):
            resource_register(self.root, self.database, path)

    def test_retired_id_is_not_reused_when_its_old_path_is_registered_again(self) -> None:
        path = self.root / "reused-name.txt"
        path.write_text("first physical file", encoding="utf-8")
        self.initialise()
        first = apply(resource_register, self.root, self.database, path)["resource_id"]
        apply(resource_retire, self.root, self.database, first)
        path.write_text("replacement physical file", encoding="utf-8")

        second = apply(resource_register, self.root, self.database, path)["resource_id"]

        self.assertNotEqual(first, second)
        self.assertEqual(resource_resolve(self.root, self.database, first)["status"], "retired")
        self.assertEqual(resource_resolve(self.root, self.database, second)["status"], "active")


if __name__ == "__main__":
    unittest.main()
