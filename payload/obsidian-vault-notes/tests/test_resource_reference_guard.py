from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
VAULT_EDIT = SCRIPTS_ROOT / "vault_edit.py"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from resource_reference_guard import (  # noqa: E402
    HarnessClient,
    ResourceReferenceError,
    preflight_resource_references,
    rewrite_managed_resource_uri,
    scan_resource_references,
)


RESOURCE_ID = "KB-" + ("A" * 26)
SECOND_RESOURCE_ID = "KB-" + ("B" * 26)


FAKE_HARNESS_SOURCE = r'''
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path


def emit(payload, code=0):
    print(json.dumps(payload, ensure_ascii=True, separators=(",", ":")))
    raise SystemExit(code)


args = sys.argv[1:]
log_path = Path(os.environ["FAKE_HARNESS_LOG"])
with log_path.open("a", encoding="utf-8", newline="\n") as handle:
    handle.write(json.dumps(args, ensure_ascii=True) + "\n")

if not args:
    emit({"error": "missing command"}, 2)

command = args[0]
if command == "resource-resolve":
    resource_id = args[args.index("--id") + 1]
    mode = os.environ.get("FAKE_RESOURCE_MODE", "active")
    if mode == "unknown":
        emit({"error": "unknown resource_id"}, 2)
    status = mode if mode in {"missing", "retired"} else "active"
    payload = {
        "resource_id": resource_id,
        "relative_path": "library/resource.pdf",
        "absolute_path": os.environ.get("FAKE_RESOURCE_PATH"),
        "file_uri": os.environ["FAKE_RESOURCE_URI"],
        "status": status,
        "exists": mode != "missing",
        "recommended_skill": "pdf",
    }
    if mode == "incomplete":
        payload.pop("exists")
    if os.environ.get("FAKE_DISAPPEAR_AFTER_RESOLVE") == "1":
        Path(__file__).unlink()
    emit(payload)

if command == "reference-refresh":
    content_path = Path(args[args.index("--content-file") + 1])
    content = content_path.read_text(encoding="utf-8")
    post_sha = (
        args[args.index("--post-sha256") + 1]
        if "--post-sha256" in args
        else hashlib.sha256(content.encode("utf-8")).hexdigest()
    )
    if not content_path.is_file() or len(post_sha) != 64:
        emit({"error": "invalid refresh input"}, 2)
    if os.environ.get("FAKE_UNCONFIGURED") == "1":
        emit({"error": "The resource registry is not initialized; run registry-init"}, 2)
    diagnostics = []
    if "library/resource.pdf" in content:
        diagnostics.append({
            "reference": "library/resource.pdf",
            "kind": "knowledge_root_relative_path",
            "reason": "unmanaged_path_reference",
            "line": 1,
        })
    if os.environ.get("FAKE_DIAGNOSTIC") == "1":
        diagnostics.append({"reason": "existing_synthetic_diagnostic", "line": 1})
    plan = hashlib.sha256((str(Path(args[args.index("--note") + 1])) + post_sha).encode("utf-8")).hexdigest()
    if "--write" not in args:
        emit({"changed": True, "plan_sha256": plan, "diagnostics": diagnostics, "parsed_counts": {"managed": 1}})
    if os.environ.get("FAKE_SYNC_FAIL") == "1":
        emit({"error": "synthetic sync failure"}, 2)
    expected = args[args.index("--expect-plan-sha256") + 1]
    if expected != plan:
        emit({"error": "plan mismatch"}, 2)
    emit({"applied": True, "generation": 7, "plan_sha256": plan, "diagnostics": diagnostics})

emit({"error": "unsupported command"}, 2)
'''


class ResourceReferenceParserTests(unittest.TestCase):
    def test_http_links_are_not_treated_as_local_resource_paths(self) -> None:
        text = "\n".join(
            [
                "[paper](https://www.labri.fr/perso/zvonkin/Research/BlockDesPolys.pdf)",
                "Bare HTTPS://example.com/archive/report.pdf?download=1#page=2",
                "<https://example.com/files/another.pdf>",
                "[site](http://example.com/C:/not-a-local-file.pdf)",
            ]
        )

        scan = scan_resource_references(text)
        self.assertFalse(scan.managed)
        self.assertFalse(scan.unmanaged)

        client = HarnessClient(path=Path("__synthetic_missing_harness__.py"), source="test_missing")
        audit = preflight_resource_references("alpha\n", text, note_path=Path("note.md"), client=client)
        self.assertEqual(audit["preflight"], "not_needed")

    def test_parser_recognizes_supported_references_and_ignores_code(self) -> None:
        text = "\n".join(
            [
                f"[paper](file:///C:/kb/paper.pdf)<!-- mpk-resource:{RESOURCE_ID} -->",
                f"![plot](file:///C:/kb/plot.png)<!-- mpk-resource:{SECOND_RESOURCE_ID} -->",
                f'<video src="file:///C:/kb/movie.mp4"></video><!-- mpk-resource:{RESOURCE_ID} -->',
                "[legacy](file:///C:/kb/legacy.pdf)",
                "Raw file:///C:/kb/raw.epub",
                r"Windows C:\资料\book.pdf",
                f"`[inline](file:///C:/kb/inline.pdf)<!-- mpk-resource:{RESOURCE_ID} -->`",
                "```markdown",
                f"[fenced](file:///C:/kb/fenced.pdf)<!-- mpk-resource:{RESOURCE_ID} -->",
                r"C:\ignored\fenced.pdf",
                "```",
            ]
        )

        scan = scan_resource_references(text)

        self.assertEqual([item.kind for item in scan.managed], ["markdown_link", "markdown_image", "html_video"])
        self.assertEqual(len(scan.unmanaged), 3)
        self.assertEqual(
            {item.kind for item in scan.unmanaged},
            {"markdown_link", "raw_file_uri", "windows_path"},
        )
        self.assertNotIn("inline.pdf", json.dumps(scan.as_json(), ensure_ascii=False))
        self.assertNotIn("fenced.pdf", json.dumps(scan.as_json(), ensure_ascii=False))

    def test_unavailable_harness_allows_plain_edit_but_blocks_reference_change(self) -> None:
        client = HarnessClient(path=Path("__synthetic_missing_harness__.py"), source="test_missing")
        audit = preflight_resource_references("alpha\n", "beta\n", note_path=Path("note.md"), client=client)
        self.assertEqual(audit["preflight"], "not_needed")

        candidate = f"[paper](file:///C:/kb/paper.pdf)<!-- mpk-resource:{RESOURCE_ID} -->\n"
        with self.assertRaises(ResourceReferenceError) as raised:
            preflight_resource_references("alpha\n", candidate, note_path=Path("note.md"), client=client)
        self.assertEqual(raised.exception.audit["preflight"], "blocked")
        self.assertFalse(raised.exception.audit["harness"]["available"])

        with self.assertRaises(ResourceReferenceError) as relative_raised:
            preflight_resource_references(
                "alpha\n",
                "knowledge-files/paper.pdf\n",
                note_path=Path("note.md"),
                client=client,
            )
        self.assertEqual(relative_raised.exception.audit["preflight"], "blocked")
        self.assertTrue(
            relative_raised.exception.audit["possible_relative_paths"]["added"]
        )

    def test_existing_vault_relative_attachment_does_not_require_resource_harness(self) -> None:
        client = HarnessClient(path=Path("__synthetic_missing_harness__.py"), source="test_missing")
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary)
            (vault / ".obsidian").mkdir()
            (vault / "attachments").mkdir()
            (vault / "attachments" / "figure.png").write_bytes(b"image")
            note = vault / "note.md"
            audit = preflight_resource_references(
                "alpha\n",
                "![figure](attachments/figure.png)\n",
                note_path=note,
                client=client,
            )
        self.assertEqual(audit["preflight"], "not_needed")
        self.assertFalse(audit["possible_relative_paths"]["added"])

    def test_new_unmanaged_reference_is_always_blocked(self) -> None:
        client = HarnessClient(path=Path(__file__), source="explicit")
        candidate = "[legacy](file:///C:/kb/legacy.pdf)\n"
        with self.assertRaises(ResourceReferenceError) as raised:
            preflight_resource_references("alpha\n", candidate, note_path=Path("note.md"), client=client)
        self.assertEqual(len(raised.exception.audit["unmanaged_added"]), 1)

    def test_rewrite_managed_uri_changes_only_matching_non_code_references(self) -> None:
        text = "\n".join(
            [
                f"[paper](file:///C:/old/paper.pdf)<!-- mpk-resource:{RESOURCE_ID} -->",
                f'![plot](file:///C:/old/plot.png)<!-- mpk-resource:{SECOND_RESOURCE_ID} -->',
                f'<video src="file:///C:/old/movie.mp4"></video><!-- mpk-resource:{RESOURCE_ID} -->',
                f"`[example](file:///C:/old/example.pdf)<!-- mpk-resource:{RESOURCE_ID} -->`",
                "```markdown",
                f"[fenced](file:///C:/old/fenced.pdf)<!-- mpk-resource:{RESOURCE_ID} -->",
                "```",
            ]
        )

        result = rewrite_managed_resource_uri(text, RESOURCE_ID, "file:///D:/new/resource.pdf")

        self.assertTrue(result["changed"])
        self.assertEqual(result["count"], 2)
        self.assertEqual(
            result["old_uris"],
            ["file:///C:/old/paper.pdf", "file:///C:/old/movie.mp4"],
        )
        self.assertIn(f"![plot](file:///C:/old/plot.png)<!-- mpk-resource:{SECOND_RESOURCE_ID} -->", result["text"])
        self.assertIn("file:///C:/old/example.pdf", result["text"])
        self.assertIn("file:///C:/old/fenced.pdf", result["text"])

    def test_parser_reports_forward_slash_unc_bare_uri_and_bad_markers(self) -> None:
        text = "\n".join(
            [
                "Drive C:/资料/book.pdf",
                r"UNC \\server\share\paper.pdf",
                "UNC2 //server/share/image.png",
                "Bare file:C:/资料/bare.epub",
                "Network file://server/share/media.mp4",
                "[bad](file:///C:/资料/bad.pdf)<!-- mpk-resource:BAD -->",
                f"orphan <!-- mpk-resource:{RESOURCE_ID} -->",
            ]
        )

        scan = scan_resource_references(text)
        kinds = [item.kind for item in scan.unmanaged]
        issues = [item.issue for item in scan.unmanaged]

        self.assertGreaterEqual(kinds.count("windows_path"), 3)
        self.assertGreaterEqual(kinds.count("raw_file_uri"), 2)
        self.assertIn("invalid_resource_marker", issues)
        self.assertIn("orphan_resource_marker", issues)

    def test_one_marker_cannot_cover_src_and_poster(self) -> None:
        text = (
            '<video src="file:///C:/kb/movie.mp4" poster="file:///C:/kb/poster.png"></video>'
            f'<!-- mpk-resource:{RESOURCE_ID} -->'
        )
        scan = scan_resource_references(text)
        self.assertFalse(scan.managed)
        self.assertEqual(
            [item.issue for item in scan.unmanaged].count("ambiguous_html_file_targets"),
            2,
        )


class VaultEditResourceReferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.note = self.root / "note.md"
        self.resource = self.root / "resource.pdf"
        self.resource.write_bytes(b"synthetic")
        self.fake_harness = self.root / "fake_harness.py"
        self.fake_harness.write_text(textwrap.dedent(FAKE_HARNESS_SOURCE), encoding="utf-8", newline="\n")
        self.log = self.root / "harness-log.jsonl"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def environment(self, **overrides: str) -> dict[str, str]:
        environment = os.environ.copy()
        environment.update(
            {
                "PYTHONDONTWRITEBYTECODE": "1",
                "MPK_HARNESS": str(self.fake_harness),
                "FAKE_HARNESS_LOG": str(self.log),
                "FAKE_RESOURCE_URI": self.resource.as_uri(),
                "FAKE_RESOURCE_PATH": str(self.resource),
            }
        )
        environment.update(overrides)
        return environment

    def run_edit(self, *args: str, environment: dict[str, str] | None = None) -> tuple[int, dict[str, object], str]:
        command = [sys.executable, str(VAULT_EDIT), args[0], "--vault-root", str(self.root), *args[1:]]
        completed = subprocess.run(
            command,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment or self.environment(),
            check=False,
        )
        payload = json.loads(completed.stdout)
        local_audit_tests = {
            'test_direct_https_pdf_link_passes_vault_edit_preflight',
            'test_harness_dry_run_blocks_new_root_relative_path',
            'test_post_sync_carries_harness_diagnostics_into_audit',
        }
        if self._testMethodName in local_audit_tests:
            import hashlib
            self.assertTrue(payload['fields_reduced'])
            self.assertFalse(payload['details_complete'])
            reference = payload['local_audit']
            raw = Path(reference['path']).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), reference['sha256'])
            record = json.loads(raw)
            self.assertEqual(record['schema'], 'note-operation-audit/v1')
            self.assertEqual(record['operation'], 'vault_edit')
            full = record['result']
            for key in ['ok', 'applied', 'status', 'post_sha256', 'race_check']:
                self.assertEqual(payload.get(key), full.get(key))
            # Original diagnostic assertions consume only the hash-bound local artifact.
            payload = full
        return completed.returncode, payload, completed.stderr

    def link(self) -> str:
        return f"[resource]({self.resource.as_uri()})<!-- mpk-resource:{RESOURCE_ID} -->"

    def read_log(self) -> list[list[str]]:
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def test_direct_https_pdf_link_passes_vault_edit_preflight(self) -> None:
        self.note.write_text("alpha\n", encoding="utf-8", newline="\n")
        link = "[paper](https://www.labri.fr/perso/zvonkin/Research/BlockDesPolys.pdf)"

        code, payload, stderr = self.run_edit(
            "append-section",
            "--file",
            str(self.note),
            "--text",
            link,
        )

        self.assertEqual(code, 0, stderr)
        self.assertEqual(payload["resource_references"]["preflight"], "not_needed")
        self.assertFalse(payload["resource_references"]["unmanaged_added"])
        self.assertFalse(payload["resource_references"]["possible_relative_paths"]["added"])
        self.assertEqual(self.note.read_text(encoding="utf-8"), "alpha\n")

    def test_valid_reference_is_resolved_and_post_write_sync_is_two_phase(self) -> None:
        self.note.write_text("alpha\n", encoding="utf-8", newline="\n")

        dry_code, dry, stderr = self.run_edit(
            "append-section",
            "--file",
            str(self.note),
            "--text",
            self.link(),
        )
        self.assertEqual(dry_code, 0, stderr)
        self.assertEqual(dry["resource_references"]["preflight"], "passed")
        self.assertEqual(self.note.read_text(encoding="utf-8"), "alpha\n")

        code, payload, stderr = self.run_edit(
            "append-section",
            "--file",
            str(self.note),
            "--text",
            self.link(),
            "--expect-sha256",
            dry["pre_sha256"],
            "--write",
        )

        self.assertEqual(code, 0, stderr)
        self.assertTrue(payload["applied"])
        self.assertEqual(payload["resource_references"]["sync"]["status"], "synchronized")
        self.assertIn(self.link(), self.note.read_text(encoding="utf-8"))
        calls = self.read_log()
        refresh_calls = [call for call in calls if call[0] == "reference-refresh"]
        self.assertEqual(len(refresh_calls), 6)
        self.assertNotIn("--write", refresh_calls[-2])
        self.assertIn("--write", refresh_calls[-1])
        self.assertIn("--expect-plan-sha256", refresh_calls[-1])

    def test_unknown_or_missing_id_stops_before_writing(self) -> None:
        for mode in ("unknown", "missing", "retired", "incomplete"):
            with self.subTest(mode=mode):
                self.note.write_text("alpha\n", encoding="utf-8", newline="\n")
                code, payload, _stderr = self.run_edit(
                    "append-section",
                    "--file",
                    str(self.note),
                    "--text",
                    self.link(),
                    "--write",
                    environment=self.environment(FAKE_RESOURCE_MODE=mode),
                )
                self.assertEqual(code, 2)
                self.assertFalse(payload["applied"])
                self.assertEqual(payload["resource_references"]["preflight"], "blocked")
                self.assertEqual(self.note.read_text(encoding="utf-8"), "alpha\n")

    def test_uri_authority_query_and_fragment_are_compared(self) -> None:
        self.note.write_text("alpha\n", encoding="utf-8", newline="\n")
        for expected in (
            "file://server/share/resource.pdf",
            self.resource.as_uri() + "?version=2",
            self.resource.as_uri() + "#page=3",
        ):
            with self.subTest(expected=expected):
                code, payload, _stderr = self.run_edit(
                    "append-section",
                    "--file",
                    str(self.note),
                    "--text",
                    self.link(),
                    "--write",
                    environment=self.environment(FAKE_RESOURCE_URI=expected),
                )
                self.assertEqual(code, 2)
                self.assertTrue(payload["resource_references"]["uri_mismatch"])
                self.assertEqual(self.note.read_text(encoding="utf-8"), "alpha\n")

    def test_path_mismatch_stops_before_writing(self) -> None:
        self.note.write_text("alpha\n", encoding="utf-8", newline="\n")
        environment = self.environment(FAKE_RESOURCE_URI=(self.root / "other.pdf").as_uri())
        code, payload, _stderr = self.run_edit(
            "append-section",
            "--file",
            str(self.note),
            "--text",
            self.link(),
            "--write",
            environment=environment,
        )
        self.assertEqual(code, 2)
        self.assertEqual(len(payload["resource_references"]["path_mismatch"]), 1)
        self.assertEqual(self.note.read_text(encoding="utf-8"), "alpha\n")

    def test_harness_unavailable_plain_edit_continues_and_reference_change_stops(self) -> None:
        self.note.write_text("alpha\n", encoding="utf-8", newline="\n")
        missing_environment = self.environment(MPK_HARNESS=str(self.root / "missing.py"))
        code, payload, stderr = self.run_edit(
            "append-section",
            "--file",
            str(self.note),
            "--text",
            "beta",
            "--write",
            environment=missing_environment,
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(payload["resource_references"]["sync"]["status"], "skipped_harness_unavailable")
        baseline = self.note.read_text(encoding="utf-8")

        code, payload, _stderr = self.run_edit(
            "append-section",
            "--file",
            str(self.note),
            "--text",
            self.link(),
            "--write",
            environment=missing_environment,
        )
        self.assertEqual(code, 2)
        self.assertEqual(payload["resource_references"]["preflight"], "blocked")
        self.assertEqual(self.note.read_text(encoding="utf-8"), baseline)

    def test_post_write_sync_failure_reports_pending_without_reverting_note(self) -> None:
        self.note.write_text("alpha\n", encoding="utf-8", newline="\n")
        code, payload, _stderr = self.run_edit(
            "append-section",
            "--file",
            str(self.note),
            "--text",
            self.link(),
            "--write",
            environment=self.environment(FAKE_SYNC_FAIL="1"),
        )

        self.assertEqual(code, 3)
        self.assertTrue(payload["applied"])
        self.assertEqual(payload["status"], "reference_sync_pending")
        self.assertEqual(payload["resource_references"]["sync"]["status"], "reference_sync_pending")
        self.assertIn(self.link(), self.note.read_text(encoding="utf-8"))

    def test_unconfigured_harness_does_not_block_plain_note_edit(self) -> None:
        self.note.write_text("alpha\n", encoding="utf-8", newline="\n")
        code, payload, stderr = self.run_edit(
            "append-section",
            "--file",
            str(self.note),
            "--text",
            "beta",
            "--write",
            environment=self.environment(FAKE_UNCONFIGURED="1"),
        )

        self.assertEqual(code, 0, stderr)
        self.assertTrue(payload["applied"])
        self.assertEqual(payload["resource_references"]["sync"]["status"], "skipped_harness_unconfigured")

    def test_any_harness_runtime_failure_is_nonblocking_when_references_do_not_change(self) -> None:
        self.note.write_text("alpha\n", encoding="utf-8", newline="\n")
        code, payload, stderr = self.run_edit(
            "append-section",
            "--file",
            str(self.note),
            "--text",
            "beta",
            "--write",
            environment=self.environment(FAKE_SYNC_FAIL="1"),
        )
        self.assertEqual(code, 0, stderr)
        self.assertTrue(payload["applied"])
        self.assertEqual(payload["resource_references"]["sync"]["status"], "skipped_harness_error")

    def test_harness_disappearing_after_external_preflight_reports_pending(self) -> None:
        self.note.write_text("alpha\n", encoding="utf-8", newline="\n")
        code, payload, _stderr = self.run_edit(
            "append-section",
            "--file",
            str(self.note),
            "--text",
            self.link(),
            "--write",
            environment=self.environment(FAKE_DISAPPEAR_AFTER_RESOLVE="1"),
        )
        self.assertEqual(code, 3)
        self.assertTrue(payload["applied"])
        self.assertEqual(payload["status"], "reference_sync_pending")

    def test_harness_dry_run_blocks_new_root_relative_path(self) -> None:
        self.note.write_text("alpha\n", encoding="utf-8", newline="\n")
        code, payload, _stderr = self.run_edit(
            "append-section",
            "--file",
            str(self.note),
            "--text",
            "library/resource.pdf",
            "--write",
        )
        self.assertEqual(code, 2)
        diagnostics = payload["resource_references"]["harness_diagnostics"]["added"]
        self.assertTrue(any(item.get("kind") == "knowledge_root_relative_path" for item in diagnostics))
        self.assertEqual(self.note.read_text(encoding="utf-8"), "alpha\n")

    def test_post_sync_carries_harness_diagnostics_into_audit(self) -> None:
        self.note.write_text("alpha\n", encoding="utf-8", newline="\n")
        code, payload, stderr = self.run_edit(
            "append-section",
            "--file",
            str(self.note),
            "--text",
            "beta",
            "--write",
            environment=self.environment(FAKE_DIAGNOSTIC="1"),
        )
        self.assertEqual(code, 0, stderr)
        sync = payload["resource_references"]["sync"]
        self.assertEqual(sync["status"], "synchronized")
        self.assertTrue(sync["dry_run_diagnostics"])
        self.assertTrue(sync["written_diagnostics"])


if __name__ == "__main__":
    unittest.main()
