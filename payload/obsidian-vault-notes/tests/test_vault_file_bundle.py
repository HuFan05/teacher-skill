from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from vault_file_bundle import (  # noqa: E402
    BundleError,
    build_plan,
    execute_plan,
    require_windows_publication_owner,
)


class VaultFileBundleTests(unittest.TestCase):
    def test_groups_multiple_files_in_one_child_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = root / "vault"
            parent = vault / "数据"
            work = root / "work"
            parent.mkdir(parents=True)
            work.mkdir()
            chart = work / "chart.png"
            data = work / "data.csv"
            chart.write_bytes(b"png")
            data.write_text("year,count\n2025,1\n", encoding="utf-8")

            plan = build_plan(
                str(vault),
                "数据",
                "arXiv机器学习论文统计",
                [str(chart), str(data)],
            )
            execute_plan(plan)

            target = parent / "arXiv机器学习论文统计"
            self.assertEqual(sorted(path.name for path in target.iterdir()), ["chart.png", "data.csv"])
            self.assertFalse((parent / "chart.png").exists())
            self.assertTrue(chart.exists())
            self.assertTrue(data.exists())

    def test_rejects_existing_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = root / "vault"
            parent = vault / "数据"
            target = parent / "已有任务"
            work = root / "work"
            target.mkdir(parents=True)
            work.mkdir()
            first = work / "chart.png"
            second = work / "data.csv"
            first.write_bytes(b"png")
            second.write_text("x", encoding="utf-8")

            with self.assertRaisesRegex(BundleError, "already exists"):
                build_plan(str(vault), "数据", "已有任务", [str(first), str(second)])

    def test_rejects_path_like_bundle_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = root / "vault"
            parent = vault / "数据"
            work = root / "work"
            parent.mkdir(parents=True)
            work.mkdir()
            first = work / "chart.png"
            second = work / "data.csv"
            first.write_bytes(b"png")
            second.write_text("x", encoding="utf-8")

            with self.assertRaisesRegex(BundleError, "path separators"):
                build_plan(str(vault), "数据", "../逃逸", [str(first), str(second)])

    def test_windows_owner_match_allows_publication(self) -> None:
        require_windows_publication_owner(
            Path("C:/synthetic/vault-parent"),
            platform_name="nt",
            path_owner_sid=lambda _path: "S-1-5-21-owner",
            current_user_sid=lambda: "S-1-5-21-owner",
        )

    def test_windows_owner_mismatch_blocks_before_staging(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = root / "vault"
            parent = vault / "数据"
            work = root / "work"
            parent.mkdir(parents=True)
            work.mkdir()
            archive = work / "archive.zip"
            checksum = work / "archive.zip.sha256"
            archive.write_bytes(b"zip")
            checksum.write_text("digest  archive.zip\n", encoding="utf-8")
            plan = build_plan(
                str(vault), "数据", "blocked", [str(archive), str(checksum)]
            )

            with mock.patch(
                "vault_file_bundle.require_windows_publication_owner",
                side_effect=BundleError("rerun through the sandbox approval mechanism"),
            ):
                with self.assertRaisesRegex(BundleError, "sandbox approval mechanism"):
                    execute_plan(plan)

            self.assertFalse((parent / "blocked").exists())
            self.assertEqual(list(parent.glob(".blocked.bundle-stage-*")), [])


if __name__ == "__main__":
    unittest.main()
