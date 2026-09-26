"""Package integrity: the recorded inventory must match the shipped tree.

This is the detection half of the design. An agent with general tool access can
edit a file on disk; it cannot make the edit invisible, because the inventory
hash stops matching.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "tools"))

try:
    import checksums  # noqa: E402
except ImportError:  # installed as a Skill: the package tools are not present
    checksums = None


@unittest.skipIf(checksums is None, "package tools are only present in the distribution tree")
class InventoryTests(unittest.TestCase):
    def test_recorded_inventory_matches_the_tree(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-B", str(ROOT / "tools" / "checksums.py")],
            capture_output=True, text=True, cwd=str(ROOT), check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("verified", completed.stdout)

    def test_aggregate_hash_algorithm_is_stable(self) -> None:
        first = checksums.aggregate(checksums.files())
        second = checksums.aggregate(list(reversed(checksums.files())))
        self.assertEqual(first, second, "the aggregate must not depend on traversal order")

    def test_summary_agrees_with_the_manifest(self) -> None:
        summary = json.loads((ROOT / "checksums.aggregate.json").read_text(encoding="utf-8"))
        manifest = (ROOT / "checksums.sha256").read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(summary["file_count"], len(manifest))
        self.assertEqual(summary["aggregate_source_sha256"], checksums.aggregate(checksums.files()))

    def test_local_state_is_excluded(self) -> None:
        names = {path.name for path in checksums.files()}
        for excluded in ("checksums.sha256", "checksums.aggregate.json"):
            self.assertNotIn(excluded, names)
        self.assertFalse(any(path.suffix in (".pyc", ".sqlite3") for path in checksums.files()))

    def test_the_three_agent_entrypoints_agree(self) -> None:
        claude = (ROOT / "CLAUDE.md").read_bytes()
        gemini = (ROOT / "GEMINI.md").read_bytes()
        self.assertEqual(claude, gemini, "the pointer files must carry identical text")
        for name in ("AGENTS.md", "CLAUDE.md", "GEMINI.md"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("AGENT_INSTALL.md", text)


class VendorBoundaryTests(unittest.TestCase):
    """The Skill ships no model SDK, no endpoint and no wire protocol."""

    def _needles(self) -> tuple[str, ...]:
        # Assembled at runtime so this file does not contain them literally.
        vendors = ("open" + "ai", "anthro" + "pic", "google.gener" + "ativeai")
        return tuple([f"import {v}" for v in vendors] + [f"from {v}" for v in vendors])

    def test_no_model_sdk_is_imported(self) -> None:
        offenders = []
        for path in SCRIPTS.rglob("*.py"):
            if path == Path(__file__).resolve():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for needle in self._needles():
                if needle in text:
                    offenders.append(f"{path.relative_to(SCRIPTS)}: {needle}")
        self.assertEqual(offenders, [])

    def test_no_packaged_file_speaks_a_model_wire_protocol(self) -> None:
        pattern = re.compile(r"/chat/completions|Authorization")
        hits = sorted(
            str(path.relative_to(SCRIPTS))
            for path in SCRIPTS.rglob("*.py")
            if path != Path(__file__).resolve() and pattern.search(path.read_text(encoding="utf-8", errors="replace"))
        )
        self.assertEqual(hits, [])

    def test_no_endpoint_or_secret_is_shipped(self) -> None:
        secret = re.compile(r"sk-[A-Za-z0-9]{20,}")
        for path in SCRIPTS.rglob("*"):
            if path.is_file() and path.suffix in (".py", ".md", ".json", ".html", ".yaml"):
                self.assertIsNone(secret.search(path.read_text(encoding="utf-8", errors="replace")), str(path))


if __name__ == "__main__":
    unittest.main()
