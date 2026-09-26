from __future__ import annotations

import sys
import tempfile
from os.path import realpath as _realpath

# The platform temporary root can be a symlink (for example on macOS); resolve
# it so synthetic knowledge roots match the resolved paths the code compares.
tempfile.tempdir = _realpath(tempfile.gettempdir())
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from manage_personal_knowledge.references import (  # noqa: E402
    diff_resource_references,
    format_resource_link,
    parse_resource_references,
    rewrite_managed_reference_paths,
)


ID_A = "KB-" + ("A" * 26)
ID_B = "KB-" + ("B" * 26)


class ReferenceParsingTests(unittest.TestCase):
    def test_parses_links_images_and_html_media_with_same_line_markers(self) -> None:
        text = (
            f"[论文](file:///C:/KB/paper.pdf)<!-- mpk-resource:{ID_A} -->\n"
            f"![图](file:///C:/KB/image.png) <!-- mpk-resource:{ID_B} -->\n"
            f'<video controls src="file:///C:/KB/movie.mp4"></video><!-- mpk-resource:{ID_A} -->\n'
        )
        parsed = parse_resource_references(text)

        self.assertEqual(parsed["counts"], {"managed": 3, "unmanaged": 0, "invalid_markers": 0})
        self.assertEqual(
            [item["kind"] for item in parsed["references"]],
            ["markdown_link", "markdown_image", "html_media"],
        )
        self.assertEqual([item["line"] for item in parsed["references"]], [1, 2, 3])

    def test_ignores_code_and_reports_real_unmanaged_paths(self) -> None:
        text = (
            "`file:///C:/KB/example.pdf`\n"
            "```markdown\n"
            f"[示例](file:///C:/KB/fenced.pdf)<!-- mpk-resource:{ID_A} -->\n"
            "```\n"
            "正文 file:///C:/KB/no-id.pdf\n"
            "另见 C:\\KB\\裸路径.pdf\n"
            "以及 Library/relative.pdf\n"
        )
        parsed = parse_resource_references(
            text,
            knowledge_root=r"C:\KB",
            known_relative_paths=["Library/relative.pdf"],
        )

        self.assertEqual(parsed["counts"]["managed"], 0)
        self.assertEqual(
            {(item["kind"], item["line"]) for item in parsed["unmanaged_path_references"]},
            {
                ("file_uri", 5),
                ("absolute_windows_path", 6),
                ("knowledge_root_relative_path", 7),
            },
        )

    def test_orphan_and_invalid_markers_are_diagnostics(self) -> None:
        text = (
            "[错误](file:///C:/KB/a.pdf)<!-- mpk-resource:KB-bad -->\n"
            f"孤立 <!-- mpk-resource:{ID_A} -->\n"
        )
        parsed = parse_resource_references(text)

        self.assertEqual(parsed["counts"]["managed"], 1)
        self.assertEqual(parsed["counts"]["invalid_markers"], 2)
        self.assertEqual(
            {item["reason"] for item in parsed["invalid_markers"]},
            {"invalid_resource_id", "orphan_resource_marker"},
        )

    def test_reports_existing_root_relative_path_without_a_registry_hint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            current = root / "Library" / "current.pdf"
            current.parent.mkdir(parents=True)
            current.write_bytes(b"pdf")
            parsed = parse_resource_references(
                "current: Library/current.pdf\nordinary: docs/example.pdf\n",
                knowledge_root=root,
            )
        self.assertEqual(
            [item["reference"] for item in parsed["unmanaged_path_references"]],
            ["Library/current.pdf"],
        )

    def test_shorter_fence_does_not_close_a_longer_code_block(self) -> None:
        text = (
            "````markdown\n"
            "```\n"
            "file:///C:/KB/example.pdf\n"
            "````\n"
            "file:///C:/KB/real.pdf\n"
        )
        parsed = parse_resource_references(text)
        self.assertEqual(
            [item["reference"] for item in parsed["unmanaged_path_references"]],
            ["file:///C:/KB/real.pdf"],
        )


class ReferenceRewriteTests(unittest.TestCase):
    def test_rewrites_only_managed_target_and_preserves_display_text(self) -> None:
        source = (
            f"[原显示名](file:///C:/old/paper.pdf)<!-- mpk-resource:{ID_A} -->\n"
            "裸链接 file:///C:/old/unmanaged.pdf\n"
        )
        result = rewrite_managed_reference_paths(
            source,
            {ID_A: "file:///D:/new/paper.pdf"},
        )

        self.assertTrue(result["changed"])
        self.assertIn(f"[原显示名](file:///D:/new/paper.pdf)<!-- mpk-resource:{ID_A} -->", result["text"])
        self.assertIn("file:///C:/old/unmanaged.pdf", result["text"])

    def test_diff_counts_repeated_occurrences(self) -> None:
        before = [{"resource_id": ID_A}, {"resource_id": ID_A}]
        after = [{"resource_id": ID_A}, {"resource_id": ID_B}, {"resource_id": ID_B}]
        result = diff_resource_references(before, after)

        self.assertEqual(result["retained"], [{"resource_id": ID_A, "count": 1}])
        self.assertEqual(result["removed"], [{"resource_id": ID_A, "count": 1}])
        self.assertEqual(result["added"], [{"resource_id": ID_B, "count": 2}])

    def test_formats_standard_links(self) -> None:
        link = format_resource_link("资料", "file:///C:/KB/a.pdf", ID_A)
        image = format_resource_link("图", "file:///C:/KB/a.png", ID_B, kind="markdown_image")

        self.assertEqual(link, f"[资料](file:///C:/KB/a.pdf)<!-- mpk-resource:{ID_A} -->")
        self.assertEqual(image, f"![图](file:///C:/KB/a.png)<!-- mpk-resource:{ID_B} -->")


if __name__ == "__main__":
    unittest.main()
