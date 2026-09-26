from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
from os.path import realpath as _realpath

# The platform temporary root can be a symlink (for example on macOS); resolve
# it so synthetic knowledge roots match the resolved paths the code compares.
tempfile.tempdir = _realpath(tempfile.gettempdir())
import unittest
from collections import defaultdict
from pathlib import Path
from unittest import mock


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from manage_personal_knowledge.config import load_config, save_config  # noqa: E402
from manage_personal_knowledge.library import (  # noqa: E402
    ExtractionResult,
    get_page,
    index_library,
    library_status,
    search_library,
)
import manage_personal_knowledge.operations as operations_module  # noqa: E402
import manage_personal_knowledge.registry as registry_module  # noqa: E402
from manage_personal_knowledge.operations import (  # noqa: E402
    resource_move,
    root_relink,
)
from manage_personal_knowledge.references import (  # noqa: E402
    rewrite_managed_reference_paths,
)
from manage_personal_knowledge.registry import (  # noqa: E402
    RegistryPlanError,
    RegistrySafetyError,
    generate_root_id,
    manifest_state,
    registry_init,
    replace_note_references,
    resource_register,
    resource_resolve,
)


def apply(operation, *args, **kwargs):
    preview = operation(*args, **kwargs)
    return operation(
        *args,
        **kwargs,
        write=True,
        expect_plan_sha256=preview["plan_sha256"],
    )


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FakeBatchRunner:
    """Small transactional stand-in for vault_batch_edit.py."""

    def __init__(self, *, fail_status: str | None = None) -> None:
        self.fail_status = fail_status
        self.calls: list[dict[str, object]] = []

    def __call__(self, vault_root: Path, manifest_path: Path, write: bool):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        operations = manifest["operations"]
        self.calls.append({"write": write, "operations": operations})
        by_note: dict[str, list[dict[str, object]]] = defaultdict(list)
        for operation in operations:
            by_note[str(operation["file"])].append(operation)

        outputs: dict[Path, bytes] = {}
        results: list[dict[str, object]] = []
        for relative, note_operations in by_note.items():
            note = vault_root / relative
            raw = note.read_bytes()
            had_bom = raw.startswith(b"\xef\xbb\xbf")
            before = (raw[3:] if had_bom else raw).decode("utf-8")
            before_digest = sha256_file(note)
            expected = {str(item["expected_sha256"]) for item in note_operations}
            if expected != {before_digest}:
                return {
                    "ok": False,
                    "transaction_status": "preflight_failed",
                    "errors": [{"error": "expected_sha256 mismatch"}],
                }
            uri_by_id = {
                str(item["resource_id"]): str(item["new_uri"])
                for item in note_operations
            }
            result = rewrite_managed_reference_paths(before, uri_by_id)
            final_raw = str(result["text"]).encode("utf-8")
            outputs[note] = (b"\xef\xbb\xbf" + final_raw) if had_bom else final_raw
            results.append(
                {
                    "file": relative,
                    "changed": bool(result["changed"]),
                    "pre_sha256": before_digest,
                }
            )

        if self.fail_status is not None:
            if write and self.fail_status == "partial_write" and outputs:
                note, raw = next(iter(outputs.items()))
                note.write_bytes(raw)
            return {
                "ok": False,
                "transaction_status": self.fail_status,
                "results": results,
            }
        if write:
            for note, raw in outputs.items():
                note.write_bytes(raw)
        return {
            "ok": True,
            "transaction_status": "applied" if write else "dry_run",
            "results": results,
        }


class OperationsFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.root = self.base / "knowledge"
        self.vault = self.root / "Vault"
        self.library = self.root / "Library"
        (self.vault / ".obsidian").mkdir(parents=True)
        self.library.mkdir(parents=True)
        self.database = self.base / "state" / "registry.sqlite3"
        self.library_database = self.base / "state" / "library.sqlite3"
        self.config = self.base / "config" / "manage.json"
        self.receiver_config = self.base / "config" / "obsidian.json"
        self.root_id = generate_root_id()
        apply(
            registry_init,
            self.root,
            self.database,
            knowledge_root_id=self.root_id,
            vault_relative_path="Vault",
            excluded_relative_paths=["cache"],
        )
        save_config(
            {
                "schema_version": 2,
                "knowledge_root": str(self.root),
                "knowledge_root_name": "knowledge",
                "knowledge_root_id": self.root_id,
                "registry": {
                    "manifest_relative_path": ".mpk/resources.jsonl",
                    "excluded_relative_paths": ["cache"],
                },
                "sources": {
                    "vault": {
                        "kind": "obsidian-vault",
                        "relative_path": "Vault",
                    },
                    "library": {
                        "kind": "pdf-library",
                        "relative_path": "Library",
                    },
                },
            },
            self.config,
        )
        self.receiver_config.parent.mkdir(parents=True, exist_ok=True)
        self.receiver_config.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "vault_root": str(self.vault),
                    "kb_root": str(self.vault / "scripts" / "obsidian_local_kb"),
                    "db_path": str(self.base / "vault.sqlite3"),
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def add_resource(self, relative: str = "Library/book.pdf") -> tuple[str, Path]:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"stable resource content")
        result = apply(resource_register, self.root, self.database, path)
        return str(result["resource_id"]), path

    def add_reference(self, identifier: str, resource: Path) -> tuple[Path, str]:
        note = self.vault / "Notes" / "A.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        text = (
            f"[book]({resource.as_uri()})"
            f"<!-- mpk-resource:{identifier} -->\n"
        )
        note.write_text(text, encoding="utf-8", newline="")
        replace_note_references(
            self.database,
            "Notes/A.md",
            sha256_file(note),
            [
                {
                    "resource_id": identifier,
                    "target_uri": resource.as_uri(),
                    "line": 1,
                }
            ],
            mtime_ns=note.stat().st_mtime_ns,
        )
        return note, text

    def cached_note(self, relative: str = "Notes/A.md") -> tuple[str, list[tuple[str, str, int]]]:
        connection = sqlite3.connect(self.database)
        try:
            document = connection.execute(
                "SELECT note_sha256 FROM note_documents WHERE note_relative_path = ?",
                (relative,),
            ).fetchone()
            rows = connection.execute(
                "SELECT resource_id, target_uri, line FROM note_references "
                "WHERE note_relative_path = ? ORDER BY ordinal",
                (relative,),
            ).fetchall()
        finally:
            connection.close()
        return (str(document[0]) if document else "", [tuple(row) for row in rows])


class ResourceMoveTests(OperationsFixture):
    def test_dry_run_then_write_moves_file_registry_manifest_and_note(self) -> None:
        identifier, source = self.add_resource()
        note, _ = self.add_reference(identifier, source)
        runner = FakeBatchRunner()

        preview = resource_move(
            self.root,
            self.database,
            identifier,
            "Library/renamed.pdf",
            batch_runner=runner,
        )
        self.assertTrue(preview["dry_run"])
        self.assertEqual(
            preview["batch_guard_preflight"],
            "deferred_until_registry_transition",
        )
        self.assertTrue(source.is_file())
        self.assertEqual(runner.calls, [])

        result = resource_move(
            self.root,
            self.database,
            identifier,
            "Library/renamed.pdf",
            batch_runner=runner,
            write=True,
            expect_plan_sha256=preview["plan_sha256"],
        )

        target = self.root / "Library" / "renamed.pdf"
        self.assertEqual(result["state"], "applied")
        self.assertFalse(source.exists())
        self.assertTrue(target.is_file())
        resolved = resource_resolve(self.root, self.database, identifier)
        self.assertEqual(resolved["relative_path"], "Library/renamed.pdf")
        self.assertEqual(resolved["hash_state"], "verified")
        self.assertIn(target.as_uri(), note.read_text(encoding="utf-8"))
        self.assertEqual(
            manifest_state(self.root, self.database)["state"], "in_sync"
        )
        connection = sqlite3.connect(self.database)
        try:
            version = connection.execute(
                "SELECT content_version FROM resources WHERE resource_id = ?",
                (identifier,),
            ).fetchone()[0]
            versions = connection.execute(
                "SELECT version, sha256 FROM resource_versions WHERE resource_id = ?",
                (identifier,),
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual(version, 1)
        self.assertEqual(versions, [(1, sha256_file(target))])
        connection = sqlite3.connect(self.database)
        try:
            history = connection.execute(
                "SELECT relative_path, valid_to FROM path_history WHERE resource_id = ? "
                "ORDER BY id",
                (identifier,),
            ).fetchall()
            state = connection.execute(
                "SELECT state FROM operation_log WHERE operation_id = ?",
                (result["operation_id"],),
            ).fetchone()[0]
            note_cache = connection.execute(
                "SELECT nd.note_sha256, nr.target_uri FROM note_documents nd "
                "JOIN note_references nr USING(note_relative_path) "
                "WHERE nd.note_relative_path = 'Notes/A.md' AND nr.resource_id = ?",
                (identifier,),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual([item[0] for item in history], ["Library/book.pdf", "Library/renamed.pdf"])
        self.assertIsNotNone(history[0][1])
        self.assertIsNone(history[1][1])
        self.assertEqual(state, "applied")
        self.assertEqual(note_cache, (sha256_file(note), target.as_uri()))

    def test_changed_markdown_is_previewed_without_cache_writes_then_fully_cached(self) -> None:
        identifier, source = self.add_resource()
        note, _ = self.add_reference(identifier, source)
        other = self.root / "Library" / "other.pdf"
        other.write_bytes(b"different resource content")
        other_id = str(apply(resource_register, self.root, self.database, other)["resource_id"])
        edited = (
            f"[first]({source.as_uri()})<!-- mpk-resource:{identifier} -->\n"
            f"[second]({source.as_uri()})<!-- mpk-resource:{identifier} -->\n"
            f"[other]({other.as_uri()})<!-- mpk-resource:{other_id} -->\n"
            "human edit\n"
        )
        note.write_text(edited, encoding="utf-8", newline="")
        cache_before = self.cached_note()
        runner = FakeBatchRunner()

        preview = resource_move(
            self.root,
            self.database,
            identifier,
            "Library/renamed.pdf",
            batch_runner=runner,
        )

        self.assertEqual(self.cached_note(), cache_before)
        self.assertEqual(note.read_text(encoding="utf-8"), edited)
        self.assertEqual(preview["context"]["reference_refresh"]["changed_notes"], 1)
        rewrite_action = next(
            item
            for item in preview["actions"]
            if item["action"] == "rewrite_managed_note_links"
        )
        self.assertEqual(rewrite_action["operation_count"], 1)

        result = resource_move(
            self.root,
            self.database,
            identifier,
            "Library/renamed.pdf",
            batch_runner=runner,
            write=True,
            expect_plan_sha256=preview["plan_sha256"],
        )

        target = self.root / "Library" / "renamed.pdf"
        final_text = note.read_text(encoding="utf-8")
        self.assertEqual(result["state"], "applied")
        self.assertEqual(final_text.count(target.as_uri()), 2)
        digest, rows = self.cached_note()
        self.assertEqual(digest, sha256_file(note))
        self.assertEqual(
            rows,
            [
                (identifier, target.as_uri(), 1),
                (identifier, target.as_uri(), 2),
                (other_id, other.as_uri(), 3),
            ],
        )

    def test_deleted_target_reference_is_removed_from_cache_after_write(self) -> None:
        identifier, source = self.add_resource()
        note, _ = self.add_reference(identifier, source)
        other = self.root / "Library" / "other.pdf"
        other.write_bytes(b"different resource content")
        other_id = str(apply(resource_register, self.root, self.database, other)["resource_id"])
        edited = f"[other]({other.as_uri()})<!-- mpk-resource:{other_id} -->\n"
        note.write_text(edited, encoding="utf-8", newline="")
        runner = FakeBatchRunner()

        preview = resource_move(
            self.root,
            self.database,
            identifier,
            "Library/renamed.pdf",
            batch_runner=runner,
        )

        self.assertEqual(runner.calls, [])
        self.assertEqual(self.cached_note()[1][0][0], identifier)
        result = resource_move(
            self.root,
            self.database,
            identifier,
            "Library/renamed.pdf",
            batch_runner=runner,
            write=True,
            expect_plan_sha256=preview["plan_sha256"],
        )

        self.assertEqual(result["state"], "applied")
        self.assertEqual(note.read_text(encoding="utf-8"), edited)
        self.assertEqual(
            self.cached_note(),
            (sha256_file(note), [(other_id, other.as_uri(), 1)]),
        )

    def test_no_op_move_still_applies_the_incremental_cache_refresh(self) -> None:
        identifier, source = self.add_resource()
        note, _ = self.add_reference(identifier, source)
        note.write_text(
            note.read_text(encoding="utf-8") + "human edit\n",
            encoding="utf-8",
            newline="",
        )
        preview = resource_move(
            self.root,
            self.database,
            identifier,
            "Library/book.pdf",
            batch_runner=FakeBatchRunner(),
        )

        result = resource_move(
            self.root,
            self.database,
            identifier,
            "Library/book.pdf",
            batch_runner=FakeBatchRunner(),
            write=True,
            expect_plan_sha256=preview["plan_sha256"],
        )

        self.assertEqual(result["state"], "applied")
        self.assertTrue(result["changed"])
        self.assertTrue(source.is_file())
        self.assertEqual(
            self.cached_note(),
            (sha256_file(note), [(identifier, source.as_uri(), 1)]),
        )

    def test_note_edit_after_preview_invalidates_the_accepted_plan(self) -> None:
        identifier, source = self.add_resource()
        note, _ = self.add_reference(identifier, source)
        preview = resource_move(
            self.root,
            self.database,
            identifier,
            "Library/renamed.pdf",
            batch_runner=FakeBatchRunner(),
        )
        note.write_text(
            note.read_text(encoding="utf-8") + "concurrent human edit\n",
            encoding="utf-8",
            newline="",
        )

        with self.assertRaises(RegistryPlanError):
            resource_move(
                self.root,
                self.database,
                identifier,
                "Library/renamed.pdf",
                batch_runner=FakeBatchRunner(),
                write=True,
                expect_plan_sha256=preview["plan_sha256"],
            )
        self.assertTrue(source.is_file())
        self.assertFalse((self.root / "Library" / "renamed.pdf").exists())

    def test_edit_after_batch_write_is_not_overwritten_by_automatic_rollback(self) -> None:
        identifier, source = self.add_resource()
        note, _ = self.add_reference(identifier, source)
        preview = resource_move(
            self.root,
            self.database,
            identifier,
            "Library/renamed.pdf",
            batch_runner=FakeBatchRunner(),
        )

        def edit_after_batch(vault: Path, manifest: Path, write: bool):
            payload = FakeBatchRunner()(vault, manifest, write)
            if write:
                note.write_bytes(note.read_bytes() + b"concurrent human edit\n")
            return payload

        result = resource_move(
            self.root,
            self.database,
            identifier,
            "Library/renamed.pdf",
            batch_runner=edit_after_batch,
            write=True,
            expect_plan_sha256=preview["plan_sha256"],
        )

        self.assertEqual(result["state"], "partial_write")
        self.assertFalse(result["applied"])
        self.assertTrue(any(item.startswith("notes:") for item in result["rollback_errors"]))
        self.assertIn("concurrent human edit", note.read_text(encoding="utf-8"))
        self.assertTrue(source.is_file())

    def test_late_failure_restores_resource_truth_before_inverse_note_batch(self) -> None:
        identifier, source = self.add_resource()
        note, original = self.add_reference(identifier, source)
        delegate = FakeBatchRunner()

        def guarded_runner(vault: Path, manifest: Path, write: bool):
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            if write:
                resolved = resource_resolve(self.root, self.database, identifier)
                for item in payload["operations"]:
                    self.assertEqual(item["new_uri"], resolved["file_uri"])
            return delegate(vault, manifest, write)

        preview = resource_move(
            self.root,
            self.database,
            identifier,
            "Library/renamed.pdf",
            batch_runner=guarded_runner,
        )
        original_cache_update = operations_module._update_note_reference_cache
        calls = {"count": 0}

        def fail_once(*args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("synthetic late cache failure")
            return original_cache_update(*args, **kwargs)

        with mock.patch.object(
            operations_module, "_update_note_reference_cache", side_effect=fail_once
        ):
            result = resource_move(
                self.root,
                self.database,
                identifier,
                "Library/renamed.pdf",
                batch_runner=guarded_runner,
                write=True,
                expect_plan_sha256=preview["plan_sha256"],
            )
        self.assertEqual(result["state"], "rolled_back")
        self.assertTrue(source.is_file())
        self.assertEqual(note.read_text(encoding="utf-8"), original)
        self.assertEqual(len([call for call in delegate.calls if call["write"]]), 2)

    def test_indexed_pdf_move_preserves_search_and_page_without_reextracting(self) -> None:
        identifier, source = self.add_resource()
        index_library(
            self.library,
            self.library_database,
            knowledge_root_id=self.root_id,
            library_relative_path="Library",
            extractor=lambda _: ExtractionResult(
                "indexed operation body", method="synthetic"
            ),
        )
        preview = resource_move(
            self.root,
            self.database,
            identifier,
            "Library/renamed.pdf",
            library_database_path=self.library_database,
            library_relative_path="Library",
        )
        self.assertEqual(
            preview["context"]["pdf_index"]["action"],
            "rename_preserve_text",
        )
        result = resource_move(
            self.root,
            self.database,
            identifier,
            "Library/renamed.pdf",
            library_database_path=self.library_database,
            library_relative_path="Library",
            write=True,
            expect_plan_sha256=preview["plan_sha256"],
        )
        self.assertEqual(result["state"], "applied")
        self.assertTrue(result["pdf_index_update"]["preserves_extracted_text"])
        self.assertEqual(
            search_library(self.library_database, "indexed operation body")["results"][0]["path"],
            "renamed.pdf",
        )
        self.assertEqual(
            get_page(self.library_database, "renamed.pdf", 1)["method"],
            "synthetic",
        )

    def test_pdf_index_failure_rolls_back_file_registry_manifest_and_index(self) -> None:
        identifier, source = self.add_resource()
        index_library(
            self.library,
            self.library_database,
            knowledge_root_id=self.root_id,
            library_relative_path="Library",
            extractor=lambda _: "recoverable indexed text",
        )
        preview = resource_move(
            self.root,
            self.database,
            identifier,
            "Library/renamed.pdf",
            library_database_path=self.library_database,
            library_relative_path="Library",
        )

        def corrupt_then_fail(*args, **kwargs):
            connection = sqlite3.connect(self.library_database)
            try:
                connection.execute("DELETE FROM pdf_page_fts")
                connection.execute("DELETE FROM pdf_pages")
                connection.commit()
            finally:
                connection.close()
            raise RuntimeError("synthetic PDF index failure")

        with mock.patch.object(
            operations_module.library_module,
            "apply_indexed_pdf_move",
            side_effect=corrupt_then_fail,
        ):
            result = resource_move(
                self.root,
                self.database,
                identifier,
                "Library/renamed.pdf",
                library_database_path=self.library_database,
                library_relative_path="Library",
                write=True,
                expect_plan_sha256=preview["plan_sha256"],
            )
        self.assertEqual(result["state"], "rolled_back")
        self.assertTrue(source.is_file())
        self.assertEqual(
            resource_resolve(self.root, self.database, identifier)["relative_path"],
            "Library/book.pdf",
        )
        self.assertEqual(
            search_library(self.library_database, "recoverable indexed text")["results"][0]["path"],
            "book.pdf",
        )
        self.assertEqual(manifest_state(self.root, self.database)["state"], "in_sync")

    def test_preview_skips_reparse_subtrees_without_caching_them(self) -> None:
        identifier, _ = self.add_resource()
        linked = self.vault / "Linked"
        linked.mkdir()
        hidden_note = linked / "Hidden.md"
        hidden_note.write_text(
            f"[hidden](file:///C:/stale.pdf)<!-- mpk-resource:{identifier} -->\n",
            encoding="utf-8",
            newline="",
        )
        original = operations_module._is_reparse_point

        def mark_linked(path: Path, entry=None) -> bool:
            return path == linked or original(path, entry)

        runner = FakeBatchRunner()
        with mock.patch.object(
            operations_module, "_is_reparse_point", side_effect=mark_linked
        ):
            preview = resource_move(
                self.root,
                self.database,
                identifier,
                "Library/renamed.pdf",
                batch_runner=runner,
            )

        self.assertEqual(runner.calls, [])
        self.assertEqual(
            preview["context"]["reference_refresh"]["skipped_reparse_points"], 1
        )
        connection = sqlite3.connect(self.database)
        try:
            cached = connection.execute(
                "SELECT COUNT(*) FROM note_documents WHERE note_relative_path = 'Linked/Hidden.md'"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(cached, 0)

    def test_move_rejects_vault_cache_and_existing_targets(self) -> None:
        identifier, _ = self.add_resource()
        with self.assertRaises(RegistrySafetyError):
            resource_move(self.root, self.database, identifier, "Vault/book.pdf")
        with self.assertRaises(RegistrySafetyError):
            resource_move(self.root, self.database, identifier, "cache/book.pdf")
        target = self.root / "Library" / "occupied.pdf"
        target.write_bytes(b"occupied")
        with self.assertRaises(RegistrySafetyError):
            resource_move(self.root, self.database, identifier, target)

    def test_move_rejects_a_registered_source_beneath_a_later_reparse_parent(self) -> None:
        identifier, source = self.add_resource("Library/Nested/book.pdf")
        unsafe_parent = source.parent
        original = registry_module._is_reparse_point

        def mark_parent(path: Path, entry=None) -> bool:
            return path == unsafe_parent or original(path, entry)

        with mock.patch.object(
            registry_module, "_is_reparse_point", side_effect=mark_parent
        ):
            with self.assertRaisesRegex(RegistrySafetyError, "reparse point"):
                resource_move(
                    self.root,
                    self.database,
                    identifier,
                    "Library/renamed.pdf",
                    batch_runner=FakeBatchRunner(),
                )
        self.assertTrue(source.is_file())

    def test_move_rejects_a_root_marker_beneath_a_reparse_management_dir(self) -> None:
        identifier, source = self.add_resource()
        unsafe_management_dir = self.root / ".mpk"
        original = registry_module._is_reparse_point

        def mark_management_dir(path: Path, entry=None) -> bool:
            return path == unsafe_management_dir or original(path, entry)

        with mock.patch.object(
            registry_module, "_is_reparse_point", side_effect=mark_management_dir
        ):
            with self.assertRaisesRegex(RegistrySafetyError, "reparse point"):
                resource_move(
                    self.root,
                    self.database,
                    identifier,
                    "Library/renamed.pdf",
                )
        self.assertTrue(source.is_file())

    def test_changed_source_invalidates_the_accepted_plan_before_write(self) -> None:
        identifier, source = self.add_resource()
        preview = resource_move(
            self.root, self.database, identifier, "Library/renamed.pdf"
        )
        source.write_bytes(b"changed after preview")
        with self.assertRaises(RegistryPlanError):
            resource_move(
                self.root,
                self.database,
                identifier,
                "Library/renamed.pdf",
                write=True,
                expect_plan_sha256=preview["plan_sha256"],
            )
        self.assertTrue(source.is_file())
        self.assertFalse((self.root / "Library" / "renamed.pdf").exists())

    def test_batch_failure_restores_file_database_and_manifest(self) -> None:
        identifier, source = self.add_resource()
        note, original = self.add_reference(identifier, source)
        runner = FakeBatchRunner(fail_status="rolled_back")
        # Preview must succeed; make only the write call fail.
        good_runner = FakeBatchRunner()
        preview = resource_move(
            self.root,
            self.database,
            identifier,
            "Library/renamed.pdf",
            batch_runner=good_runner,
        )
        runner.fail_status = None
        # The write recomputes a dry-run before calling the failing write pass.
        calls = {"count": 0}

        def fail_on_write(vault: Path, manifest: Path, write: bool):
            calls["count"] += 1
            delegate = FakeBatchRunner(fail_status="rolled_back" if write else None)
            return delegate(vault, manifest, write)

        result = resource_move(
            self.root,
            self.database,
            identifier,
            "Library/renamed.pdf",
            batch_runner=fail_on_write,
            write=True,
            expect_plan_sha256=preview["plan_sha256"],
        )
        self.assertEqual(result["state"], "rolled_back")
        self.assertTrue(source.is_file())
        self.assertFalse((self.root / "Library" / "renamed.pdf").exists())
        self.assertEqual(
            resource_resolve(self.root, self.database, identifier)["relative_path"],
            "Library/book.pdf",
        )
        self.assertEqual(note.read_text(encoding="utf-8"), original)
        self.assertEqual(
            manifest_state(self.root, self.database)["state"], "in_sync"
        )
        self.assertTrue(Path(result["recovery_log"]).is_file())

    def test_partial_batch_write_is_never_reported_as_success(self) -> None:
        identifier, source = self.add_resource()
        note, _ = self.add_reference(identifier, source)
        preview = resource_move(
            self.root,
            self.database,
            identifier,
            "Library/renamed.pdf",
            batch_runner=FakeBatchRunner(),
        )

        def partial_on_write(vault: Path, manifest: Path, write: bool):
            return FakeBatchRunner(fail_status="partial_write" if write else None)(
                vault, manifest, write
            )

        result = resource_move(
            self.root,
            self.database,
            identifier,
            "Library/renamed.pdf",
            batch_runner=partial_on_write,
            write=True,
            expect_plan_sha256=preview["plan_sha256"],
        )
        self.assertEqual(result["state"], "partial_write")
        self.assertFalse(result["applied"])
        self.assertTrue(source.is_file())
        # The recovery log, not a success claim, is the hand-off for an
        # uncertain note-side write.
        self.assertTrue(Path(result["recovery_log"]).is_file())


class RootRelinkTests(OperationsFixture):
    def move_root_externally(self) -> Path:
        new_root = self.base / "moved-knowledge"
        shutil.move(str(self.root), str(new_root))
        return new_root

    def test_root_relink_updates_both_configs_and_managed_uris(self) -> None:
        identifier, resource = self.add_resource()
        note, _ = self.add_reference(identifier, resource)
        new_root = self.move_root_externally()
        new_note = new_root / note.relative_to(self.root)
        runner = FakeBatchRunner()

        preview = root_relink(
            new_root,
            self.database,
            config_path=self.config,
            obsidian_receiver_config_path=self.receiver_config,
            batch_runner=runner,
        )
        self.assertTrue(preview["dry_run"])
        self.assertEqual(load_config(self.config)["knowledge_root"], str(self.root))

        result = root_relink(
            new_root,
            self.database,
            config_path=self.config,
            obsidian_receiver_config_path=self.receiver_config,
            batch_runner=runner,
            write=True,
            expect_plan_sha256=preview["plan_sha256"],
        )
        self.assertEqual(result["state"], "applied")
        self.assertEqual(load_config(self.config)["knowledge_root"], str(new_root))
        receiver = json.loads(self.receiver_config.read_text(encoding="utf-8"))
        self.assertEqual(receiver["vault_root"], str(new_root / "Vault"))
        self.assertEqual(
            receiver["kb_root"],
            str(new_root / "Vault" / "scripts" / "obsidian_local_kb"),
        )
        new_resource = new_root / "Library" / "book.pdf"
        self.assertIn(new_resource.as_uri(), new_note.read_text(encoding="utf-8"))
        self.assertEqual(
            resource_resolve(new_root, self.database, identifier)["absolute_path"],
            str(new_resource),
        )
        self.assertEqual(
            manifest_state(new_root, self.database)["state"], "in_sync"
        )
        connection = sqlite3.connect(self.database)
        try:
            note_cache = connection.execute(
                "SELECT nd.note_sha256, nr.target_uri FROM note_documents nd "
                "JOIN note_references nr USING(note_relative_path) "
                "WHERE nd.note_relative_path = 'Notes/A.md' AND nr.resource_id = ?",
                (identifier,),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(note_cache, (sha256_file(new_note), new_resource.as_uri()))

    def test_root_relink_rejects_copy_ambiguity(self) -> None:
        copied = self.base / "copied-knowledge"
        shutil.copytree(self.root, copied)
        with self.assertRaisesRegex(RegistrySafetyError, "Both the configured old root"):
            root_relink(
                copied,
                self.database,
                config_path=self.config,
                obsidian_receiver_config_path=self.receiver_config,
                batch_runner=FakeBatchRunner(),
            )

    def test_root_relink_rejects_registered_paths_beneath_a_reparse_parent(self) -> None:
        identifier, resource = self.add_resource("Library/Nested/book.pdf")
        self.add_reference(identifier, resource)
        new_root = self.move_root_externally()
        unsafe_parent = new_root / "Library" / "Nested"
        original = registry_module._is_reparse_point

        def mark_parent(path: Path, entry=None) -> bool:
            return path == unsafe_parent or original(path, entry)

        with mock.patch.object(
            registry_module, "_is_reparse_point", side_effect=mark_parent
        ):
            with self.assertRaisesRegex(RegistrySafetyError, "reparse point"):
                root_relink(
                    new_root,
                    self.database,
                    config_path=self.config,
                    obsidian_receiver_config_path=self.receiver_config,
                    batch_runner=FakeBatchRunner(),
                )
        self.assertEqual(load_config(self.config)["knowledge_root"], str(self.root))

    def test_root_relink_rejects_a_missing_active_resource(self) -> None:
        _, resource = self.add_resource()
        new_root = self.move_root_externally()
        moved_resource = new_root / resource.relative_to(self.root)
        moved_resource.unlink()
        with self.assertRaisesRegex(RegistrySafetyError, "missing"):
            root_relink(
                new_root,
                self.database,
                config_path=self.config,
                obsidian_receiver_config_path=self.receiver_config,
                batch_runner=FakeBatchRunner(),
            )

    def test_root_relink_refreshes_pdf_metadata_without_reextracting(self) -> None:
        identifier, resource = self.add_resource()
        index_library(
            self.library,
            self.library_database,
            knowledge_root_id=self.root_id,
            library_relative_path="Library",
            extractor=lambda _: ExtractionResult(
                "portable root-relink text", method="single-extraction"
            ),
        )
        new_root = self.move_root_externally()
        preview = root_relink(
            new_root,
            self.database,
            config_path=self.config,
            obsidian_receiver_config_path=self.receiver_config,
            batch_runner=FakeBatchRunner(),
            library_database_path=self.library_database,
        )
        self.assertTrue(preview["context"]["pdf_index"]["root_changed"])
        result = root_relink(
            new_root,
            self.database,
            config_path=self.config,
            obsidian_receiver_config_path=self.receiver_config,
            batch_runner=FakeBatchRunner(),
            library_database_path=self.library_database,
            write=True,
            expect_plan_sha256=preview["plan_sha256"],
        )
        self.assertEqual(result["state"], "applied")
        self.assertFalse(result["pdf_index_update"]["extracts_pdf_text"])
        self.assertEqual(
            search_library(self.library_database, "portable root-relink text")["results"][0]["path"],
            "book.pdf",
        )
        self.assertEqual(
            get_page(self.library_database, "book.pdf", 1)["method"],
            "single-extraction",
        )
        self.assertTrue(
            library_status(
                self.library_database,
                new_root / "Library",
                knowledge_root_id=self.root_id,
                library_relative_path="Library",
            )["root_path_matches_config"]
        )
        self.assertEqual(
            resource_resolve(new_root, self.database, identifier)["absolute_path"],
            str(new_root / "Library" / "book.pdf"),
        )

    def test_root_relink_pdf_refresh_failure_restores_configs_and_index(self) -> None:
        self.add_resource()
        index_library(
            self.library,
            self.library_database,
            knowledge_root_id=self.root_id,
            library_relative_path="Library",
            extractor=lambda _: "root rollback searchable text",
        )
        new_root = self.move_root_externally()
        preview = root_relink(
            new_root,
            self.database,
            config_path=self.config,
            obsidian_receiver_config_path=self.receiver_config,
            batch_runner=FakeBatchRunner(),
            library_database_path=self.library_database,
        )

        def corrupt_then_fail(*args, **kwargs):
            connection = sqlite3.connect(self.library_database)
            try:
                connection.execute("DELETE FROM pdf_page_fts")
                connection.execute("DELETE FROM pdf_pages")
                connection.commit()
            finally:
                connection.close()
            raise RuntimeError("synthetic root PDF refresh failure")

        with mock.patch.object(
            operations_module.library_module,
            "refresh_library_inventory",
            side_effect=corrupt_then_fail,
        ):
            result = root_relink(
                new_root,
                self.database,
                config_path=self.config,
                obsidian_receiver_config_path=self.receiver_config,
                batch_runner=FakeBatchRunner(),
                library_database_path=self.library_database,
                write=True,
                expect_plan_sha256=preview["plan_sha256"],
            )
        self.assertEqual(result["state"], "rolled_back")
        self.assertEqual(load_config(self.config)["knowledge_root"], str(self.root))
        self.assertEqual(
            search_library(self.library_database, "root rollback searchable text")["results"][0]["path"],
            "book.pdf",
        )

    def test_root_relink_post_index_failure_is_not_reported_as_success(self) -> None:
        identifier, resource = self.add_resource()
        note, original = self.add_reference(identifier, resource)
        new_root = self.move_root_externally()
        moved_note = new_root / note.relative_to(self.root)

        def failed_post_check():
            return {
                "ok": False,
                "vault_index_refresh": {
                    "ok": False,
                    "error": "synthetic index refresh failure",
                },
            }

        preview = root_relink(
            new_root,
            self.database,
            config_path=self.config,
            obsidian_receiver_config_path=self.receiver_config,
            batch_runner=FakeBatchRunner(),
            post_relink_check=failed_post_check,
        )
        result = root_relink(
            new_root,
            self.database,
            config_path=self.config,
            obsidian_receiver_config_path=self.receiver_config,
            batch_runner=FakeBatchRunner(),
            post_relink_check=failed_post_check,
            write=True,
            expect_plan_sha256=preview["plan_sha256"],
        )

        self.assertFalse(result["applied"])
        self.assertIn(result["state"], {"rolled_back", "partial_write"})
        self.assertFalse(result["post_relink_check"]["ok"])
        self.assertTrue(Path(result["recovery_log"]).is_file())
        recovery = json.loads(Path(result["recovery_log"]).read_text(encoding="utf-8"))
        self.assertTrue(Path(recovery["database_backup"]).is_file())
        self.assertEqual(load_config(self.config)["knowledge_root"], str(self.root))
        if result["state"] == "rolled_back":
            self.assertEqual(moved_note.read_text(encoding="utf-8"), original)

    def test_batch_failure_restores_both_configs_after_relink_attempt(self) -> None:
        identifier, resource = self.add_resource()
        note, original = self.add_reference(identifier, resource)
        new_root = self.move_root_externally()
        moved_note = new_root / note.relative_to(self.root)
        preview = root_relink(
            new_root,
            self.database,
            config_path=self.config,
            obsidian_receiver_config_path=self.receiver_config,
            batch_runner=FakeBatchRunner(),
        )

        def fail_on_write(vault: Path, manifest: Path, write: bool):
            return FakeBatchRunner(fail_status="rolled_back" if write else None)(
                vault, manifest, write
            )

        result = root_relink(
            new_root,
            self.database,
            config_path=self.config,
            obsidian_receiver_config_path=self.receiver_config,
            batch_runner=fail_on_write,
            write=True,
            expect_plan_sha256=preview["plan_sha256"],
        )
        self.assertEqual(result["state"], "rolled_back")
        self.assertEqual(load_config(self.config)["knowledge_root"], str(self.root))
        receiver = json.loads(self.receiver_config.read_text(encoding="utf-8"))
        self.assertEqual(receiver["vault_root"], str(self.vault))
        self.assertEqual(moved_note.read_text(encoding="utf-8"), original)

    def test_root_relink_rejects_wrong_root_identity(self) -> None:
        new_root = self.move_root_externally()
        marker = new_root / ".mpk" / "root.json"
        payload = json.loads(marker.read_text(encoding="utf-8"))
        payload["knowledge_root_id"] = generate_root_id()
        marker.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(RegistrySafetyError):
            root_relink(
                new_root,
                self.database,
                config_path=self.config,
                obsidian_receiver_config_path=self.receiver_config,
                batch_runner=FakeBatchRunner(),
            )

    def test_changed_note_is_refreshed_automatically_during_relink(self) -> None:
        identifier, resource = self.add_resource()
        note, _ = self.add_reference(identifier, resource)
        new_root = self.move_root_externally()
        moved_note = new_root / note.relative_to(self.root)
        moved_note.write_text(
            moved_note.read_text(encoding="utf-8") + "human edit\n",
            encoding="utf-8",
        )
        runner = FakeBatchRunner()
        cache_before = self.cached_note()

        preview = root_relink(
            new_root,
            self.database,
            config_path=self.config,
            obsidian_receiver_config_path=self.receiver_config,
            batch_runner=runner,
        )
        self.assertEqual(self.cached_note(), cache_before)
        self.assertEqual(preview["context"]["reference_refresh"]["changed_notes"], 1)

        result = root_relink(
            new_root,
            self.database,
            config_path=self.config,
            obsidian_receiver_config_path=self.receiver_config,
            batch_runner=runner,
            write=True,
            expect_plan_sha256=preview["plan_sha256"],
        )
        new_resource = new_root / "Library" / "book.pdf"
        self.assertEqual(result["state"], "applied")
        self.assertIn("human edit", moved_note.read_text(encoding="utf-8"))
        self.assertIn(new_resource.as_uri(), moved_note.read_text(encoding="utf-8"))
        self.assertEqual(
            self.cached_note(),
            (sha256_file(moved_note), [(identifier, new_resource.as_uri(), 1)]),
        )


if __name__ == "__main__":
    unittest.main()
