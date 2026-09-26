from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "extract_recall_markdown.py"
SPEC = importlib.util.spec_from_file_location("extract_recall_markdown_test", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def payload(relative_path: str = "ML/Example.md") -> dict:
    return {
        "ok": True,
        "mode": "note",
        "content_mode": "whole_note",
        "whole_note_requested": True,
        "truncated": False,
        "expanded_notes": [{
            "relative_path": relative_path,
            "content_source": "disk",
            "truncated": False,
        }],
    }


class ExtractRecallMarkdownTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.vault = self.root / "vault"
        self.note = self.vault / "ML" / "Example.md"
        self.note.parent.mkdir(parents=True)
        self.raw = b"---\ntags: [test]\n---\n# A > B\n\n## Empty\n\n## Work\n$x=2$\n"
        self.note.write_bytes(self.raw)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_copies_exact_current_live_file_bytes(self) -> None:
        markdown, receipt = MODULE.extract_complete_markdown(payload(), self.vault, "ML/Example.md")
        self.assertEqual(markdown, self.raw)
        self.assertEqual(receipt["relative_path"], "ML/Example.md")
        self.assertEqual(receipt["byte_count"], len(self.raw))

    def test_rejects_truncated_non_whole_or_escape_payload(self) -> None:
        candidates = []
        truncated = payload()
        truncated["expanded_notes"][0]["truncated"] = True
        candidates.append(truncated)
        selected = payload()
        selected["content_mode"] = "selected_sections"
        candidates.append(selected)
        candidates.append(payload("../outside.md"))
        for candidate in candidates:
            with self.subTest(candidate=candidate), self.assertRaises(MODULE.RecallContractError):
                MODULE.extract_complete_markdown(candidate, self.vault)

    def test_loads_utf8_bom_and_utf16_recall_json(self) -> None:
        text = json.dumps(payload(), ensure_ascii=False)
        for name, raw in (
            ("utf8-bom.json", b"\xef\xbb\xbf" + text.encode("utf-8")),
            ("utf16.json", text.encode("utf-16")),
        ):
            path = self.root / name
            path.write_bytes(raw)
            self.assertEqual(MODULE.load_json(path)["mode"], "note")

    def test_atomic_write_preserves_existing_output_when_replace_fails(self) -> None:
        output = self.root / "output.md"
        output.write_bytes(b"old")
        original_replace = MODULE.os.replace
        try:
            MODULE.os.replace = lambda *_args: (_ for _ in ()).throw(OSError("blocked"))
            with self.assertRaises(OSError):
                MODULE.atomic_write(output, b"new")
        finally:
            MODULE.os.replace = original_replace
        self.assertEqual(output.read_bytes(), b"old")


if __name__ == "__main__":
    unittest.main()
