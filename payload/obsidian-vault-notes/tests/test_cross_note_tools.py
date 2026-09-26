from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


SKILL_ROOT = Path(__file__).resolve().parents[1]
VAULT_IMPACT = SKILL_ROOT / "scripts" / "vault_impact.py"
VAULT_BATCH_EDIT = SKILL_ROOT / "scripts" / "vault_batch_edit.py"
VAULT_STATS = SKILL_ROOT / "scripts" / "vault_stats.py"
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import vault_batch_edit as vault_batch_edit_module
import vault_impact as vault_impact_module
import vault_stats as vault_stats_module


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class CrossNoteToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name).resolve()
        self.vault = self.root / "vault"
        self.vault.mkdir()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def run_json(self, command: list[str]) -> tuple[int, dict[str, object], str]:
        completed = subprocess.run(
            command,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        return completed.returncode, json.loads(completed.stdout), completed.stderr

    def test_vault_impact_reports_backlinks_broken_links_and_suggested_reads(self) -> None:
        (self.vault / "A.md").write_text("## Head\nTarget body\n", encoding="utf-8", newline="\n")
        (self.vault / "B.md").write_text(
            "See [[A#Head]] and [[Missing]].\nkeyword line for inspection\n",
            encoding="utf-8",
            newline="\n",
        )

        code, payload, stderr = self.run_json(
            [
                sys.executable,
                str(VAULT_IMPACT),
                "--vault-root",
                str(self.vault),
                "--target-note",
                "A",
                "--heading",
                "Head",
                "--keyword",
                "keyword",
            ]
        )

        self.assertEqual(code, 0, stderr)
        self.assertFalse(payload["write"])
        self.assertTrue(payload["direct_backlinks"])
        self.assertTrue(payload["heading_or_block_references"])
        self.assertTrue(payload["broken_links"])
        self.assertTrue(payload["suggested_reads"])

    def test_vault_batch_edit_dry_run_and_write_with_hash_guard(self) -> None:
        note = self.vault / "A.md"
        original = "See [[Old]].\n"
        note.write_text(original, encoding="utf-8", newline="\n")
        manifest = self.root / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "operations": [
                        {
                            "file": "A.md",
                            "operation": "replace-wikilink",
                            "old_wikilink": "[[Old]]",
                            "new_wikilink": "[[New]]",
                            "expected_sha256": sha256_text(original),
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
            newline="\n",
        )

        command = [
            sys.executable,
            str(VAULT_BATCH_EDIT),
            "--vault-root",
            str(self.vault),
            "--manifest",
            str(manifest),
        ]
        code, payload, stderr = self.run_json(command)
        self.assertEqual(code, 0, stderr)
        self.assertTrue(payload["results"][0]["changed"])
        self.assertFalse(payload["write"])
        self.assertEqual(note.read_text(encoding="utf-8"), original)

        code, payload, stderr = self.run_json([*command, "--write"])
        self.assertEqual(code, 0, stderr)
        self.assertTrue(payload["write"])
        self.assertEqual(note.read_text(encoding="utf-8"), "See [[New]].\n")

        note.write_text(original, encoding="utf-8", newline="\n")
        bad_manifest = self.root / "bad_manifest.json"
        bad_manifest.write_text(
            json.dumps(
                {
                    "operations": [
                        {
                            "file": "A.md",
                            "operation": "replace-text",
                            "find": "Old",
                            "replacement": "New",
                            "expected_sha256": "0" * 64,
                        }
                    ]
                }
            ),
            encoding="utf-8",
            newline="\n",
        )
        code, payload, stderr = self.run_json(
            [
                sys.executable,
                str(VAULT_BATCH_EDIT),
                "--vault-root",
                str(self.vault),
                "--manifest",
                str(bad_manifest),
                "--write",
            ]
        )
        self.assertEqual(code, 2, stderr)
        self.assertFalse(payload["results"][0]["expected_hash_ok"])
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["transaction_status"], "preflight_failed")
        self.assertEqual(note.read_text(encoding="utf-8"), original)

    def test_batch_assertions_check_final_text_and_block_every_write_on_failure(self) -> None:
        note = self.vault / "A.md"
        original = "Status: `Full resolution`\n^keep\n"
        note.write_text(original, encoding="utf-8", newline="\n")
        manifest = self.root / "assertions-ok.json"
        manifest.write_text(
            json.dumps(
                {
                    "version": 2,
                    "operations": [
                        {
                            "file": "A.md",
                            "operation": "replace-text",
                            "find": "Full resolution",
                            "replacement": "Full answer",
                        }
                    ],
                    "assertions": [
                        {
                            "file": "A.md",
                            "must_contain": [
                                {"text": "^keep", "min_count": 1, "max_count": 1},
                                {"text": "Full answer", "min_count": 1, "max_count": 1},
                            ],
                            "must_not_contain": [r"\`"],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
            newline="\n",
        )
        command = [
            sys.executable,
            str(VAULT_BATCH_EDIT),
            "--vault-root",
            str(self.vault),
            "--manifest",
            str(manifest),
        ]
        code, payload, stderr = self.run_json(command)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(payload["assertion_count"], 1)
        self.assertTrue(payload["assertions"][0]["ok"])
        self.assertEqual(note.read_text(encoding="utf-8"), original)

        code, payload, stderr = self.run_json([*command, "--write"])
        self.assertEqual(code, 0, stderr)
        self.assertTrue(payload["assertions"][0]["ok"])
        self.assertEqual(note.read_text(encoding="utf-8"), "Status: `Full answer`\n^keep\n")

        note.write_text(original, encoding="utf-8", newline="\n")
        failing_manifest = self.root / "assertions-fail.json"
        failing_manifest.write_text(
            json.dumps(
                {
                    "version": 2,
                    "operations": [
                        {
                            "file": "A.md",
                            "operation": "replace-text",
                            "find": "Full resolution",
                            "replacement": r"Full\` resolution",
                        }
                    ],
                    "assertions": [
                        {"file": "A.md", "must_contain": [{"text": "^missing", "min_count": 1}]},
                        {"file": "A.md", "must_contain": ["Full resolution"]},
                        {"file": "A.md", "must_not_contain": [r"\`"]},
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
            newline="\n",
        )
        failed_command = [
            sys.executable,
            str(VAULT_BATCH_EDIT),
            "--vault-root",
            str(self.vault),
            "--manifest",
            str(failing_manifest),
        ]
        code, payload, stderr = self.run_json(failed_command)
        self.assertEqual(code, 2, stderr)
        self.assertEqual(payload["transaction_status"], "preflight_failed")
        self.assertEqual(payload["assertion_count"], 3)
        self.assertEqual(note.read_text(encoding="utf-8"), original)
        self.assertTrue(any(item["error"] == "assertion_failed" for item in payload["errors"]))

        code, payload, stderr = self.run_json([*failed_command, "--write"])
        self.assertEqual(code, 2, stderr)
        self.assertEqual(payload["transaction_status"], "preflight_failed")
        self.assertEqual(note.read_text(encoding="utf-8"), original)

    def test_batch_preflights_all_operations_and_merges_same_file_in_order(self) -> None:
        note = self.vault / "A.md"
        original = "See [[Old]].  \r\n"
        note.write_bytes(original.encode("utf-8"))
        original_sha = hashlib.sha256(note.read_bytes()).hexdigest()
        manifest = self.root / "manifest-v2.json"
        manifest.write_text(
            json.dumps(
                {
                    "version": 2,
                    "operations": [
                        {
                            "file": "A.md",
                            "operation": "replace-wikilink",
                            "old_wikilink": "[[Old]]",
                            "new_wikilink": "[[Middle]]",
                            "expected_sha256": original_sha,
                        },
                        {
                            "file": "A.md",
                            "operation": "replace-wikilink",
                            "old_wikilink": "[[Middle]]",
                            "new_wikilink": "[[New]]",
                            "expected_sha256": original_sha,
                        },
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        code, payload, stderr = self.run_json(
            [
                sys.executable,
                str(VAULT_BATCH_EDIT),
                "--vault-root",
                str(self.vault),
                "--manifest",
                str(manifest),
                "--write",
            ]
        )
        self.assertEqual(code, 0, stderr)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["transaction_status"], "applied")
        self.assertEqual(payload["applied_count"], 2)
        self.assertEqual(note.read_bytes(), "See [[New]].  \r\n".encode("utf-8"))

        note.write_bytes(original.encode("utf-8"))
        invalid_manifest = self.root / "preflight-failure.json"
        invalid_manifest.write_text(
            json.dumps(
                {
                    "version": 2,
                    "operations": [
                        {
                            "file": "A.md",
                            "operation": "replace-wikilink",
                            "old_wikilink": "[[Old]]",
                            "new_wikilink": "[[New]]",
                            "expected_sha256": original_sha,
                        },
                        {
                            "file": "missing.md",
                            "operation": "replace-text",
                            "find": "x",
                            "replacement": "y",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        code, payload, _stderr = self.run_json(
            [
                sys.executable,
                str(VAULT_BATCH_EDIT),
                "--vault-root",
                str(self.vault),
                "--manifest",
                str(invalid_manifest),
                "--write",
            ]
        )
        self.assertEqual(code, 2)
        self.assertEqual(payload["transaction_status"], "preflight_failed")
        self.assertEqual(note.read_bytes(), original.encode("utf-8"))

    def test_batch_span_edits_preserve_repeated_mixed_line_endings(self) -> None:
        note = self.vault / "A.md"
        note.write_bytes(b"A\nX\nA\nA\r\n")
        manifest = self.root / "repeated-lines.json"
        manifest.write_text(
            json.dumps(
                {
                    "version": 2,
                    "operations": [
                        {"file": "A.md", "operation": "replace-text", "find": "X", "replacement": "A"}
                    ],
                }
            ),
            encoding="utf-8",
        )
        code, payload, stderr = self.run_json(
            [
                sys.executable,
                str(VAULT_BATCH_EDIT),
                "--vault-root",
                str(self.vault),
                "--manifest",
                str(manifest),
                "--write",
            ]
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(payload["transaction_status"], "applied")
        self.assertEqual(note.read_bytes(), b"A\nA\nA\nA\r\n")

    def test_batch_rolls_back_files_when_later_replace_fails(self) -> None:
        first = self.vault / "A.md"
        second = self.vault / "B.md"
        first.write_text("old A\n", encoding="utf-8", newline="\n")
        second.write_text("old B\n", encoding="utf-8", newline="\n")
        manifest = self.root / "rollback.json"
        manifest.write_text(
            json.dumps(
                {
                    "version": 2,
                    "operations": [
                        {"file": "A.md", "operation": "replace-text", "find": "old", "replacement": "new"},
                        {"file": "B.md", "operation": "replace-text", "find": "old", "replacement": "new"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        real_write = vault_batch_edit_module.write_bytes_atomic
        calls = 0

        def fail_second_forward_write(
            path: Path,
            payload: bytes,
            *,
            expected_current_sha256: str,
        ) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated replace failure")
            real_write(path, payload, expected_current_sha256=expected_current_sha256)

        stdout = io.StringIO()
        with mock.patch.object(
            vault_batch_edit_module,
            "write_bytes_atomic",
            side_effect=fail_second_forward_write,
        ):
            with redirect_stdout(stdout):
                code = vault_batch_edit_module.main(
                    [
                        "--vault-root",
                        str(self.vault),
                        "--manifest",
                        str(manifest),
                        "--write",
                    ]
                )
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(payload["transaction_status"], "rolled_back")
        self.assertEqual(first.read_text(encoding="utf-8"), "old A\n")
        self.assertEqual(second.read_text(encoding="utf-8"), "old B\n")

    def test_batch_returns_three_when_rollback_is_incomplete(self) -> None:
        first = self.vault / "A.md"
        second = self.vault / "B.md"
        first.write_text("old A\n", encoding="utf-8", newline="\n")
        second.write_text("old B\n", encoding="utf-8", newline="\n")
        manifest = self.root / "partial-rollback.json"
        manifest.write_text(
            json.dumps(
                {
                    "version": 2,
                    "operations": [
                        {"file": "A.md", "operation": "replace-text", "find": "old", "replacement": "new"},
                        {"file": "B.md", "operation": "replace-text", "find": "old", "replacement": "new"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        real_write = vault_batch_edit_module.write_bytes_atomic
        calls = 0

        def fail_forward_and_rollback(
            path: Path,
            payload: bytes,
            *,
            expected_current_sha256: str,
        ) -> None:
            nonlocal calls
            calls += 1
            if calls in {2, 3}:
                raise OSError("simulated write or rollback failure")
            real_write(path, payload, expected_current_sha256=expected_current_sha256)

        stdout = io.StringIO()
        with mock.patch.object(
            vault_batch_edit_module,
            "write_bytes_atomic",
            side_effect=fail_forward_and_rollback,
        ):
            with redirect_stdout(stdout):
                code = vault_batch_edit_module.main(
                    [
                        "--vault-root",
                        str(self.vault),
                        "--manifest",
                        str(manifest),
                        "--write",
                    ]
                )
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 3)
        self.assertEqual(payload["transaction_status"], "partial_write")
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["transaction_log"])
        self.assertEqual(first.read_text(encoding="utf-8"), "new A\n")
        self.assertEqual(second.read_text(encoding="utf-8"), "old B\n")

    def test_batch_does_not_overwrite_external_change_during_rollback(self) -> None:
        first = self.vault / "A.md"
        second = self.vault / "B.md"
        first.write_text("old A\n", encoding="utf-8", newline="\n")
        second.write_text("old B\n", encoding="utf-8", newline="\n")
        manifest = self.root / "external-change.json"
        manifest.write_text(
            json.dumps(
                {
                    "version": 2,
                    "operations": [
                        {"file": "A.md", "operation": "replace-text", "find": "old", "replacement": "new"},
                        {"file": "B.md", "operation": "replace-text", "find": "old", "replacement": "new"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        real_write = vault_batch_edit_module.write_bytes_atomic
        calls = 0

        def external_change_then_fail(
            path: Path,
            payload: bytes,
            *,
            expected_current_sha256: str,
        ) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                first.write_text("user A\n", encoding="utf-8", newline="\n")
                raise OSError("simulated later-file failure")
            real_write(path, payload, expected_current_sha256=expected_current_sha256)

        stdout = io.StringIO()
        with mock.patch.object(
            vault_batch_edit_module,
            "write_bytes_atomic",
            side_effect=external_change_then_fail,
        ):
            with redirect_stdout(stdout):
                code = vault_batch_edit_module.main(
                    [
                        "--vault-root",
                        str(self.vault),
                        "--manifest",
                        str(manifest),
                        "--write",
                    ]
                )
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 3)
        self.assertEqual(payload["transaction_status"], "partial_write")
        self.assertEqual(first.read_text(encoding="utf-8"), "user A\n")
        self.assertEqual(second.read_text(encoding="utf-8"), "old B\n")
        self.assertTrue(any(item["error"] == "concurrent_change" for item in payload["errors"]))

    def test_batch_refuses_forward_replace_when_target_changes_after_temp_preparation(self) -> None:
        note = self.vault / "A.md"
        note.write_text("old\n", encoding="utf-8", newline="\n")
        manifest = self.root / "temp-race.json"
        manifest.write_text(
            json.dumps(
                {
                    "version": 2,
                    "operations": [
                        {"file": "A.md", "operation": "replace-text", "find": "old", "replacement": "new"}
                    ],
                }
            ),
            encoding="utf-8",
        )
        real_prepare = vault_batch_edit_module.prepare_atomic_temp

        def prepare_then_race(path: Path, payload: bytes) -> Path:
            temp_path = real_prepare(path, payload)
            path.write_text("user version\n", encoding="utf-8", newline="\n")
            return temp_path

        stdout = io.StringIO()
        with mock.patch.object(
            vault_batch_edit_module,
            "prepare_atomic_temp",
            side_effect=prepare_then_race,
        ):
            with redirect_stdout(stdout):
                code = vault_batch_edit_module.main(
                    [
                        "--vault-root",
                        str(self.vault),
                        "--manifest",
                        str(manifest),
                        "--write",
                    ]
                )
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 3)
        self.assertEqual(payload["transaction_status"], "partial_write")
        self.assertEqual(payload["results"][0]["race_check"], "failed")
        self.assertEqual(note.read_text(encoding="utf-8"), "user version\n")
        self.assertTrue(any(item["error"] == "concurrent_change_after_preparation" for item in payload["errors"]))

    def test_batch_refuses_rollback_replace_when_target_changes_after_temp_preparation(self) -> None:
        first = self.vault / "A.md"
        second = self.vault / "B.md"
        first.write_text("old A\n", encoding="utf-8", newline="\n")
        second.write_text("old B\n", encoding="utf-8", newline="\n")
        manifest = self.root / "rollback-temp-race.json"
        manifest.write_text(
            json.dumps(
                {
                    "version": 2,
                    "operations": [
                        {"file": "A.md", "operation": "replace-text", "find": "old", "replacement": "new"},
                        {"file": "B.md", "operation": "replace-text", "find": "old", "replacement": "new"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        real_prepare = vault_batch_edit_module.prepare_atomic_temp
        calls = 0

        def fail_second_then_race_rollback(path: Path, payload: bytes) -> Path:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated later-file preparation failure")
            temp_path = real_prepare(path, payload)
            if calls == 3:
                first.write_text("user during rollback\n", encoding="utf-8", newline="\n")
            return temp_path

        stdout = io.StringIO()
        with mock.patch.object(
            vault_batch_edit_module,
            "prepare_atomic_temp",
            side_effect=fail_second_then_race_rollback,
        ):
            with redirect_stdout(stdout):
                code = vault_batch_edit_module.main(
                    [
                        "--vault-root",
                        str(self.vault),
                        "--manifest",
                        str(manifest),
                        "--write",
                    ]
                )
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 3)
        self.assertEqual(payload["transaction_status"], "partial_write")
        self.assertEqual(first.read_text(encoding="utf-8"), "user during rollback\n")
        self.assertEqual(second.read_text(encoding="utf-8"), "old B\n")
        self.assertTrue(any(item["error"] == "concurrent_change_after_preparation" for item in payload["errors"]))

    def test_batch_prepare_failure_cleans_transaction_materials(self) -> None:
        note = self.vault / "A.md"
        note.write_text("old\n", encoding="utf-8", newline="\n")
        manifest = self.root / "prepare-failure.json"
        manifest.write_text(
            json.dumps(
                {
                    "version": 2,
                    "operations": [
                        {"file": "A.md", "operation": "replace-text", "find": "old", "replacement": "new"}
                    ],
                }
            ),
            encoding="utf-8",
        )
        transaction_dir = self.root / "prepared-materials"

        def create_known_transaction_dir(_vault_root: Path) -> Path:
            transaction_dir.mkdir()
            return transaction_dir

        stdout = io.StringIO()
        with mock.patch.object(
            vault_batch_edit_module,
            "transaction_directory",
            side_effect=create_known_transaction_dir,
        ):
            with mock.patch.object(
                vault_batch_edit_module,
                "write_journal",
                side_effect=OSError("simulated journal failure"),
            ):
                with redirect_stdout(stdout):
                    code = vault_batch_edit_module.main(
                        [
                            "--vault-root",
                            str(self.vault),
                            "--manifest",
                            str(manifest),
                            "--write",
                        ]
                    )
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(payload["transaction_status"], "prepare_failed")
        self.assertFalse(payload["ok"])
        self.assertFalse(transaction_dir.exists())
        self.assertEqual(note.read_text(encoding="utf-8"), "old\n")

    def test_batch_rejects_invalid_utf8(self) -> None:
        note = self.vault / "A.md"
        note.write_bytes(b"old\xff\n")
        manifest = self.root / "invalid-utf8.json"
        manifest.write_text(
            json.dumps(
                {
                    "operations": [
                        {"file": "A.md", "operation": "replace-text", "find": "old", "replacement": "new"}
                    ]
                }
            ),
            encoding="utf-8",
        )
        code, payload, _stderr = self.run_json(
            [
                sys.executable,
                str(VAULT_BATCH_EDIT),
                "--vault-root",
                str(self.vault),
                "--manifest",
                str(manifest),
                "--write",
            ]
        )
        self.assertEqual(code, 2)
        self.assertEqual(payload["transaction_status"], "preflight_failed")
        self.assertEqual("invalid_encoding", payload["errors"][0]["error"])
        self.assertEqual(note.read_bytes(), b"old\xff\n")

    def test_vault_impact_rejects_ambiguous_title(self) -> None:
        (self.vault / "one").mkdir()
        (self.vault / "two").mkdir()
        (self.vault / "one" / "Same.md").write_text("one\n", encoding="utf-8")
        (self.vault / "two" / "Same.md").write_text("two\n", encoding="utf-8")
        code, payload, stderr = self.run_json(
            [
                sys.executable,
                str(VAULT_IMPACT),
                "--vault-root",
                str(self.vault),
                "--target-note",
                "Same",
            ]
        )
        self.assertEqual(code, 2, stderr)
        self.assertEqual(payload["error"], "ambiguous_target")
        self.assertEqual(payload["target"]["target_candidates"], ["one/Same.md", "two/Same.md"])

    def test_vault_impact_casefolds_note_keys_filters_heading_and_masks_fences(self) -> None:
        (self.vault / "A.md").write_text("# Old\nbody\n", encoding="utf-8", newline="\n")
        (self.vault / "B.md").write_text(
            "[[A#Old]]\n[[A#New]]\n```markdown\n[[A#Old]]\n[[Missing]]\n```\n",
            encoding="utf-8",
            newline="\n",
        )
        code, payload, stderr = self.run_json(
            [
                sys.executable,
                str(VAULT_IMPACT),
                "--vault-root",
                str(self.vault),
                "--target-note",
                "a#old",
            ]
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(payload["target"]["target_relative_path"], "A.md")
        self.assertEqual([item["link"] for item in payload["direct_backlinks"]], ["A#Old"])
        self.assertEqual(
            [item["link"] for item in payload["heading_or_block_references"]],
            ["A#Old"],
        )
        self.assertEqual(payload["broken_links"], [])

    def test_vault_impact_old_wikilink_heading_does_not_match_other_headings(self) -> None:
        (self.vault / "A.md").write_text("# Old\n# New\n", encoding="utf-8", newline="\n")
        (self.vault / "B.md").write_text(
            "[[A#Old]]\n[[A#New]]\n",
            encoding="utf-8",
            newline="\n",
        )
        code, payload, stderr = self.run_json(
            [
                sys.executable,
                str(VAULT_IMPACT),
                "--vault-root",
                str(self.vault),
                "--target-note",
                "A",
                "--old-wikilink",
                "[[A#Old]]",
                "--new-wikilink",
                "[[A#Renamed]]",
            ]
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(
            [item["link"] for item in payload["suspected_link_updates"]],
            ["A#Old"],
        )

    def test_vault_impact_limits_broken_links_to_impact_related_files(self) -> None:
        (self.vault / "A.md").write_text(
            "# A\n[[MissingFromTarget]]\n",
            encoding="utf-8",
            newline="\n",
        )
        (self.vault / "Related.md").write_text(
            "[[A]] and [[MissingFromRelated]]\n",
            encoding="utf-8",
            newline="\n",
        )
        (self.vault / "Keyword.md").write_text(
            "needle [[MissingFromKeyword]]\n",
            encoding="utf-8",
            newline="\n",
        )
        (self.vault / "Unrelated.md").write_text(
            "[[MissingFromUnrelated]]\n",
            encoding="utf-8",
            newline="\n",
        )
        code, payload, stderr = self.run_json(
            [
                sys.executable,
                str(VAULT_IMPACT),
                "--vault-root",
                str(self.vault),
                "--target-note",
                "A",
                "--keyword",
                "needle",
            ]
        )
        self.assertEqual(code, 0, stderr)
        broken = {item["link"] for item in payload["broken_links"]}
        self.assertEqual(
            broken,
            {"MissingFromTarget", "MissingFromRelated", "MissingFromKeyword"},
        )
        self.assertNotIn("MissingFromUnrelated", broken)

    def test_vault_tools_skip_markdown_symlink_that_resolves_outside_vault(self) -> None:
        (self.vault / "A.md").write_text("# A\n", encoding="utf-8", newline="\n")
        outside = self.root / "outside.md"
        outside.write_text("[[A]]\n![[outside.png]]\n", encoding="utf-8", newline="\n")
        linked = self.vault / "OutsideLink.md"
        try:
            linked.symlink_to(outside)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"file symlinks are unavailable: {exc}")

        code, impact, stderr = self.run_json(
            [
                sys.executable,
                str(VAULT_IMPACT),
                "--vault-root",
                str(self.vault),
                "--target-note",
                "A",
            ]
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(impact["direct_backlinks"], [])

        code, stats, stderr = self.run_json(
            [
                sys.executable,
                str(VAULT_STATS),
                "--vault-root",
                str(self.vault),
                "--kb-root",
                str(self.root / "missing-kb"),
                "--db",
                str(self.root / "missing.sqlite3"),
                "--json",
            ]
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(stats["note_count"], 1)
        self.assertEqual(stats["image_embed_count"], 0)

    def test_markdown_iterators_reject_entry_whose_resolved_path_is_outside(self) -> None:
        candidate = self.vault / "Escapes.md"
        candidate.write_text("outside simulation\n", encoding="utf-8", newline="\n")
        outside = self.root / "outside.md"
        outside.write_text("outside\n", encoding="utf-8", newline="\n")
        original_resolve = Path.resolve

        def fake_resolve(path: Path, *args, **kwargs) -> Path:
            if path == candidate:
                return outside
            return original_resolve(path, *args, **kwargs)

        with mock.patch.object(Path, "resolve", fake_resolve):
            impact_files = vault_impact_module.iter_markdown(self.vault)
            stats_files = vault_stats_module.iter_markdown_files(
                self.vault,
                self.vault,
                set(),
            )
        self.assertEqual(impact_files, [])
        self.assertEqual(stats_files, [])

    def test_vault_stats_counts_html_images_parenthesized_links_and_ignores_fences(self) -> None:
        note = self.vault / "stats.md"
        note.write_text(
            "\n".join(
                [
                    "![local](assets/a_(1).png)",
                    '<img src="assets/b.png" alt="b">',
                    "[real](https://example.com/path_(x))",
                    "```markdown",
                    "![fake](assets/fake.png)",
                    '<img src="assets/fake-html.png">',
                    "[fake](https://fake.example/path)",
                    "```",
                ]
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        code, payload, stderr = self.run_json(
            [
                sys.executable,
                str(VAULT_STATS),
                "--vault-root",
                str(self.vault),
                "--kb-root",
                str(self.root / "missing-kb"),
                "--db",
                str(self.root / "missing.sqlite3"),
                "--json",
            ]
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(payload["image_embed_count"], 2)
        self.assertEqual(payload["distinct_image_target_count"], 2)
        self.assertEqual(payload["markdown_link_count"], 1)
        self.assertEqual(payload["external_url_count"], 0)
        self.assertEqual(payload["total_link_count"], 1)

    def test_vault_stats_handles_dot_frontmatter_and_normalizes_titled_image_targets(self) -> None:
        note = self.vault / "stats.md"
        note.write_text(
            """---
cover: ![[frontmatter-only.png]]
...
![[Assets/My Image.PNG|300]]
![same](<assets/My%20Image.png> "caption")
<img src="./ASSETS/My%20Image.png">
![[assets/simple.png]]
![simple](assets/simple.png 'optional title')
""",
            encoding="utf-8",
            newline="\n",
        )
        code, payload, stderr = self.run_json(
            [
                sys.executable,
                str(VAULT_STATS),
                "--vault-root",
                str(self.vault),
                "--kb-root",
                str(self.root / "missing-kb"),
                "--db",
                str(self.root / "missing.sqlite3"),
                "--json",
            ]
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(payload["image_embed_count"], 5)
        self.assertEqual(payload["distinct_image_target_count"], 2)


if __name__ == "__main__":
    unittest.main()
