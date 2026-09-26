from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import direct_pdf_search


class PortablePathTests(unittest.TestCase):
    def test_windows_miktex_discovery_uses_receiver_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            local = Path(temporary) / "Local App Data"
            executable = local / "Programs" / "MiKTeX" / "miktex" / "bin" / "x64" / "pdftotext.exe"
            executable.parent.mkdir(parents=True)
            executable.touch()
            with mock.patch.object(direct_pdf_search.sys, "platform", "win32"), mock.patch.dict(
                os.environ, {"LOCALAPPDATA": str(local)}, clear=False
            ), mock.patch.object(direct_pdf_search.shutil, "which", return_value=None):
                self.assertEqual(Path(direct_pdf_search.find_pdftotext()), executable)

    def test_posix_discovery_uses_path_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "pdftotext"
            executable.touch()
            with mock.patch.object(direct_pdf_search.sys, "platform", "linux"), mock.patch.object(
                direct_pdf_search.shutil, "which", return_value=str(executable)
            ):
                self.assertEqual(Path(direct_pdf_search.find_pdftotext()), executable)


if __name__ == "__main__":
    unittest.main()
