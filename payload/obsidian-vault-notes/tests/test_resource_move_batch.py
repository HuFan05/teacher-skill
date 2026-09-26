from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


SKILL_ROOT = Path(__file__).resolve().parents[1]
BATCH = SKILL_ROOT / "scripts" / "vault_batch_edit.py"
RESOURCE_ID = "KB-" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


FAKE_HARNESS = r'''
from __future__ import annotations
import hashlib
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
command = args[0]
if command == "resource-resolve":
    mode = os.environ.get("BATCH_RESOURCE_MODE", "active")
    print(json.dumps({
        "resource_id": args[args.index("--id") + 1],
        "file_uri": os.environ["BATCH_EXPECTED_URI"],
        "status": mode,
        "exists": mode == "active",
    }))
    raise SystemExit(0)
if command == "reference-refresh":
    content = Path(args[args.index("--content-file") + 1]).read_text(encoding="utf-8")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    plan = hashlib.sha256((args[args.index("--note") + 1] + digest).encode("utf-8")).hexdigest()
    payload = {"plan_sha256": plan, "diagnostics": [], "parsed_counts": {"managed": content.count("mpk-resource:")}}
    if "--write" not in args:
        print(json.dumps(payload))
        raise SystemExit(0)
    fail_once = os.environ.get("BATCH_SYNC_FAIL_ONCE")
    if fail_once:
        marker = Path(fail_once)
        if not marker.exists():
            marker.write_text("failed", encoding="utf-8")
            print(json.dumps({"error": "synthetic batch sync failure"}))
            raise SystemExit(2)
    payload.update({"applied": True, "generation": 9})
    print(json.dumps(payload))
    raise SystemExit(0)
print(json.dumps({"error": "unsupported"}))
raise SystemExit(2)
'''


class ManagedResourceBatchTests(unittest.TestCase):
    def test_batch_rewrites_only_id_paired_uri_and_preserves_code_example(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp) / "vault"
            vault.mkdir()
            note = vault / "note.md"
            old_uri = "file:///C:/Knowledge/old.pdf"
            new_uri = "file:///C:/Knowledge/new.pdf"
            note.write_text(
                f"[live]({old_uri})<!-- mpk-resource:{RESOURCE_ID} -->\n"
                f"`[example]({old_uri})<!-- mpk-resource:{RESOURCE_ID} -->`\n",
                encoding="utf-8",
            )
            original_sha = hashlib.sha256(note.read_bytes()).hexdigest()
            manifest = Path(temp) / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "operations": [
                            {
                                "file": "note.md",
                                "operation": "replace-managed-resource-uri",
                                "resource_id": RESOURCE_ID,
                                "new_uri": new_uri,
                                "expected_sha256": original_sha,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            harness = Path(temp) / "fake_harness.py"
            harness.write_text(textwrap.dedent(FAKE_HARNESS), encoding="utf-8", newline="\n")
            environment = os.environ.copy()
            environment.update(
                {
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONUTF8": "1",
                    "MPK_HARNESS": str(harness),
                    "BATCH_EXPECTED_URI": new_uri,
                }
            )
            dry_run = subprocess.run(
                [sys.executable, "-B", str(BATCH), "--manifest", str(manifest), "--vault-root", str(vault)],
                text=True,
                encoding="utf-8",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                check=False,
            )
            self.assertEqual(dry_run.returncode, 0, dry_run.stderr)
            self.assertEqual(json.loads(dry_run.stdout)["transaction_status"], "dry_run")
            self.assertIn(old_uri, note.read_text(encoding="utf-8"))

            written = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(BATCH),
                    "--manifest",
                    str(manifest),
                    "--vault-root",
                    str(vault),
                    "--write",
                ],
                text=True,
                encoding="utf-8",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                check=False,
            )
            self.assertEqual(written.returncode, 0, written.stderr)
            final = note.read_text(encoding="utf-8")
            self.assertIn(f"[live]({new_uri})", final)
            self.assertIn(f"`[example]({old_uri})", final)
            payload = json.loads(written.stdout)
            self.assertEqual(payload["results"][0]["resource_references"]["sync"]["status"], "synchronized")

    def test_batch_blocks_unmanaged_reference_before_any_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp) / "vault"
            vault.mkdir()
            note = vault / "note.md"
            note.write_text("alpha\n", encoding="utf-8", newline="\n")
            manifest = Path(temp) / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "operations": [
                            {
                                "file": "note.md",
                                "operation": "append-section",
                                "text": "[bad](file:///C:/kb/unmanaged.pdf)",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment.update({"PYTHONDONTWRITEBYTECODE": "1", "MPK_HARNESS": str(Path(temp) / "missing.py")})
            completed = subprocess.run(
                [sys.executable, "-B", str(BATCH), "--manifest", str(manifest), "--vault-root", str(vault), "--write"],
                text=True,
                encoding="utf-8",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                check=False,
            )
            payload = json.loads(completed.stdout)
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(payload["transaction_status"], "preflight_failed")
            self.assertEqual(note.read_text(encoding="utf-8"), "alpha\n")

    def test_batch_sync_failure_rolls_back_note_and_reports_pending(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp) / "vault"
            vault.mkdir()
            note = vault / "note.md"
            note.write_text("alpha\n", encoding="utf-8", newline="\n")
            uri = "file:///C:/kb/resource.pdf"
            link = f"[resource]({uri})<!-- mpk-resource:{RESOURCE_ID} -->"
            manifest = Path(temp) / "manifest.json"
            manifest.write_text(
                json.dumps({"operations": [{"file": "note.md", "operation": "append-section", "text": link}]}),
                encoding="utf-8",
            )
            harness = Path(temp) / "fake_harness.py"
            harness.write_text(textwrap.dedent(FAKE_HARNESS), encoding="utf-8", newline="\n")
            environment = os.environ.copy()
            environment.update(
                {
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "MPK_HARNESS": str(harness),
                    "BATCH_EXPECTED_URI": uri,
                    "BATCH_SYNC_FAIL_ONCE": str(Path(temp) / "failed-once.marker"),
                }
            )
            completed = subprocess.run(
                [sys.executable, "-B", str(BATCH), "--manifest", str(manifest), "--vault-root", str(vault), "--write"],
                text=True,
                encoding="utf-8",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                check=False,
            )
            payload = json.loads(completed.stdout)
            self.assertEqual(completed.returncode, 3)
            self.assertEqual(payload["transaction_status"], "reference_sync_pending")
            self.assertEqual(note.read_text(encoding="utf-8"), "alpha\n")
            audit = payload["results"][0]["resource_references"]
            self.assertEqual(audit["sync"]["status"], "reference_sync_pending")
            self.assertEqual(audit["rollback_sync"]["status"], "synchronized")


if __name__ == "__main__":
    unittest.main()
