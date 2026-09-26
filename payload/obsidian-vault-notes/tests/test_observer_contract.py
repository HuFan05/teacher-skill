from __future__ import annotations

import importlib.util
import json
import re
import sys
import time
import unittest
from unittest import mock
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
CATALOG = SKILL_ROOT / "references" / "observer-phases.json"
DICTIONARY = SKILL_ROOT / "references" / "observer-data-dictionary.md"


class ObserverContractTests(unittest.TestCase):
    def test_every_emitted_phase_is_registered_and_documented(self) -> None:
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
        registered = set(catalog["workflow_phases"]) | set(catalog["script_phases"])
        pattern = re.compile(r"""["']((?:workflow|obsidian)\.[A-Za-z0-9_.:-]+)["']""")
        emitted: set[str] = set()
        for path in SCRIPTS.rglob("*.py"):
            emitted.update(pattern.findall(path.read_text(encoding="utf-8")))
        self.assertEqual(emitted - registered, set())
        dictionary = DICTIONARY.read_text(encoding="utf-8")
        self.assertEqual(
            {code for code in registered if f"`{code}`" not in dictionary},
            set(),
        )
        self.assertEqual(
            {
                field
                for field in catalog["allowed_fields"]
                if f"`{field}`" not in dictionary
            },
            set(),
        )

    def test_phase_recorder_keeps_only_closed_numeric_fields(self) -> None:
        sys.path.insert(0, str(SCRIPTS))
        try:
            spec = importlib.util.spec_from_file_location(
                "observer_contract_probe",
                SCRIPTS / "_observer.py",
            )
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            recorder = module.Recorder()
            with recorder.phase(
                "obsidian.stats.scan",
                file_count=2,
                path="C:/private/note.md",
                query="secret",
                error_count=-1,
            ):
                pass
            self.assertEqual(
                recorder.intervals[0]["fields"],
                {"file_count": 2},
            )
            serialized = json.dumps(recorder.intervals)
            self.assertNotIn("private", serialized)
            self.assertNotIn("secret", serialized)
        finally:
            sys.path.remove(str(SCRIPTS))

    def test_phase_fields_can_be_completed_from_the_result(self) -> None:
        sys.path.insert(0, str(SCRIPTS))
        try:
            spec = importlib.util.spec_from_file_location(
                "observer_mutable_fields_probe",
                SCRIPTS / "_observer.py",
            )
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            recorder = module.Recorder()
            with recorder.phase("obsidian.retrieval.query") as fields:
                fields["result_count"] = 3
                fields["query"] = "private"
            self.assertEqual(recorder.intervals[0]["fields"], {"result_count": 3})
        finally:
            sys.path.remove(str(SCRIPTS))

    def test_failed_flush_remains_retryable_and_fail_open(self) -> None:
        sys.path.insert(0, str(SCRIPTS))
        try:
            spec = importlib.util.spec_from_file_location(
                "observer_retry_probe",
                SCRIPTS / "_observer.py",
            )
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            recorder = module.Recorder()
            with recorder.phase("obsidian.stats.scan"):
                pass
            started = time.perf_counter()
            with mock.patch.object(module, "_post_phase_payload", side_effect=[False, True]):
                self.assertFalse(recorder.flush())
                self.assertFalse(recorder.sent)
                self.assertTrue(recorder.flush())
                self.assertTrue(recorder.sent)
            self.assertLess(time.perf_counter() - started, 0.2)
        finally:
            sys.path.remove(str(SCRIPTS))

    def test_skill_uses_observation_v2_and_points_to_dictionary(self) -> None:
        text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertEqual(text.count("<!-- skill-observer:v2 -->"), 1)
        self.assertIn("phase set --skill obsidian-vault-notes", text)
        self.assertIn("references/observer-data-dictionary.md", text)


if __name__ == "__main__":
    unittest.main()
