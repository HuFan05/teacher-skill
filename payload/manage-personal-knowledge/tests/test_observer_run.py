from __future__ import annotations

from pathlib import Path
import sys
import unittest


SCRIPTS_ROOT = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import observer_run  # noqa: E402


class ObserverOperationTests(unittest.TestCase):
    def test_manage_kb_subcommand_is_the_privacy_safe_operation(self) -> None:
        self.assertEqual(
            observer_run._operation_name(
                ["python", str(Path("skills") / "manage_kb.py"), "paper-locate", "--query", "secret"]
            ),
            "paper-locate",
        )

    def test_query_is_never_used_as_operation(self) -> None:
        self.assertEqual(
            observer_run._operation_name(
                ["python", str(Path("skills") / "manage_kb.py"), "--query", "secret"]
            ),
            "manage_kb",
        )


if __name__ == "__main__":
    unittest.main()
