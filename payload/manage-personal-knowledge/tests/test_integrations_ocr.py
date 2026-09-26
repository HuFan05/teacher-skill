from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from os.path import realpath as _realpath

# The platform temporary root can be a symlink (for example on macOS); resolve
# it so synthetic knowledge roots match the resolved paths the code compares.
tempfile.tempdir = _realpath(tempfile.gettempdir())
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS_ROOT = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from manage_personal_knowledge import integrations, ocr  # noqa: E402


def completed(command: list[str], stdout: str = "{}", stderr: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr=stderr)


class IntegrationTests(unittest.TestCase):
    def test_runtime_skills_root_honors_portable_override(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "portable skills"
            with patch.dict(os.environ, {"MPK_SKILLS_ROOT": str(root)}, clear=False):
                self.assertEqual(integrations.runtime_skills_root(), root.resolve())

    def build_runtime(self, root: Path) -> Path:
        skills_home = root / "skills-home"
        obsidian = skills_home / "skills" / "obsidian-vault-notes"
        pdf_paper = skills_home / "skills" / "pdf-paper-search"
        (obsidian / "scripts" / "obsidian_local_kb" / "obsidian_local_kb").mkdir(parents=True)
        (pdf_paper / "scripts").mkdir(parents=True)
        (obsidian / "scripts" / "setup_local.py").write_text("# test\n", encoding="utf-8")
        (obsidian / "scripts" / "obsidian_local_kb" / "obsidian_local_kb" / "__main__.py").write_text(
            "# test\n", encoding="utf-8"
        )
        (pdf_paper / "scripts" / "paper_search.py").write_text("# test\n", encoding="utf-8")
        (pdf_paper / "scripts" / "paper_locate.py").write_text("# test\n", encoding="utf-8")
        (pdf_paper / "scripts" / "observer_run.py").write_text("# test\n", encoding="utf-8")
        return skills_home

    @staticmethod
    def paper_locate_payload(status: str = "verified_hit") -> dict[str, object]:
        results: list[dict[str, object]] = []
        if status in {"verified_hit", "ambiguous"}:
            results.append(
                {
                    "rank": 1,
                    "title": "Sorting lower bounds",
                    "path": "algorithms/sorting-notes.pdf",
                    "pdf_page": 17,
                    "printed_page": None,
                    "match_type": "exact hit",
                    "confidence": "high",
                    "verified": True,
                    "origin": "adjacent",
                    "seed_page": 16,
                    "adjacent_offset": 1,
                    "statement_window": "Theorem 3. Comparison sorting needs Omega(n log n) comparisons in the worst case",
                    "why": ["all hard concepts occur in one statement window"],
                    "extraction": {
                        "status": "indexed",
                        "method": "pdftotext",
                        "warning": None,
                    },
                    "features": {
                        "page_role": "theorem",
                        "local_statement": True,
                        "direct_statement": True,
                        "hard_concepts_required": ["lower_bound", "worst_case"],
                        "hard_concepts_missing": [],
                    },
                }
            )
        return {
            "schema_version": "paper-locate/v1",
            "canonicalizer_version": "paper-canonical/v1",
            "status": status,
            "route": "explicit-index",
            "query": {
                "query_type": "theorem_lookup",
                "hard_concepts": ["lower_bound", "worst_case"],
                "signature_terms": ["comparison"],
            },
            "search": {
                "alias_mode": "core",
                "expanded": False,
                "candidate_count": len(results),
                "candidate_pdf_count": len(results),
                "verified_page_count": len(results),
                "adjacent_page_count": 1,
                "stop_reason": "verified-hard-anchor-window",
            },
            "results": results,
            "coverage": {
                "document_status_counts": {
                    "indexed": 1,
                    "pending": 0,
                    "no_text": 0,
                    "error": 0,
                },
                "incomplete": False,
                "diagnostics_summary": {},
                "warnings": [],
            },
            "timing_ms": {
                "core": 10.125,
                "expanded": 0,
                "verify": 5.25,
                "total": 15.375,
            },
        }

    def test_validate_integrations_reports_missing_dependencies(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = integrations.validate_integrations(Path(temporary) / "missing")
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "missing_dependencies")
        self.assertIn("obsidian_setup", [item["component"] for item in result["diagnostics"]])

    def test_status_validation_requires_paper_locator_and_observer_entries(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skills_home = self.build_runtime(root)
            paths = integrations.locate_integrations(skills_home)
            paths.pdf_paper_locate.unlink()
            paths.pdf_observer_run.unlink()
            result = integrations.validate_integrations(skills_home)
        self.assertFalse(result["ok"])
        self.assertFalse(result["checks"]["pdf_paper_locate"])
        self.assertFalse(result["checks"]["pdf_observer_run"])
        components = {item["component"] for item in result["diagnostics"]}
        self.assertTrue({"pdf_paper_locate", "pdf_observer_run"}.issubset(components))

    def test_operation_validation_ignores_unrelated_missing_paper_entries(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skills_home = self.build_runtime(root)
            paths = integrations.locate_integrations(skills_home)
            paths.pdf_paper_locate.unlink()
            paths.pdf_observer_run.unlink()
            result = integrations.validate_integrations(
                skills_home,
                required={"obsidian_setup", "obsidian_local_kb"},
            )
        self.assertTrue(result["ok"])
        self.assertFalse(result["checks"]["pdf_paper_locate"])
        self.assertFalse(result["checks"]["pdf_observer_run"])
        self.assertEqual(result["diagnostics"], [])

    def test_obsidian_configure_builds_explicit_command(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skills_home = self.build_runtime(root)
            vault = root / "vault"
            (vault / ".obsidian").mkdir(parents=True)
            config = root / "config" / "obsidian.json"
            state = root / "state" / "obsidian"
            response = json.dumps({"status": "configured", "vault_root": str(vault.resolve())})
            with patch.object(integrations.subprocess, "run", return_value=completed([], response)) as run:
                result = integrations.configure_obsidian(
                    vault,
                    config,
                    state,
                    build_index=True,
                    confirmed_replace=True,
                    skills_home=skills_home,
                    python_executable="python-test",
                )
            self.assertTrue(result["ok"])
            command = run.call_args.args[0]
            self.assertEqual(command[:2], ["python-test", "-B"])
            self.assertIn("--vault-root", command)
            self.assertIn(str(vault.resolve()), command)
            self.assertIn("--config", command)
            self.assertIn(str(config.resolve()), command)
            self.assertIn("--build-index", command)
            self.assertIn("--yes", command)
            self.assertFalse(run.call_args.kwargs.get("shell", False))

    def test_obsidian_check_rejects_mismatched_config_before_launch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skills_home = self.build_runtime(root)
            vault = root / "vault"
            other = root / "other"
            (vault / ".obsidian").mkdir(parents=True)
            other.mkdir()
            config = root / "obsidian.json"
            config.write_text(json.dumps({"vault_root": str(other)}), encoding="utf-8")
            with patch.object(integrations.subprocess, "run") as run:
                result = integrations.check_obsidian(vault, config, skills_home=skills_home)
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "configured_vault_mismatch")
            run.assert_not_called()

    def test_obsidian_refresh_uses_receiver_paths_and_incremental_command(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            kb_root = root / "obsidian_local_kb"
            (kb_root / "obsidian_local_kb").mkdir(parents=True)
            (kb_root / "obsidian_local_kb" / "__main__.py").write_text("# test\n", encoding="utf-8")
            vault = root / "vault"
            (vault / ".obsidian").mkdir(parents=True)
            database = root / "state" / "vault.sqlite3"
            config = root / "obsidian.json"
            config.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "vault_root": str(vault.resolve()),
                        "kb_root": str(kb_root.resolve()),
                        "db_path": str(database.resolve()),
                    }
                ),
                encoding="utf-8",
            )
            response = json.dumps({"status": "refreshed", "changed": 1})
            with patch.object(integrations.subprocess, "run", return_value=completed([], response)) as run:
                result = integrations.refresh_obsidian_index(
                    vault,
                    config,
                    python_executable="python-test",
                )
            self.assertTrue(result["ok"])
            command = run.call_args.args[0]
            self.assertEqual(command[:4], ["python-test", "-B", "-m", "obsidian_local_kb"])
            self.assertIn("refresh", command)
            self.assertEqual(command[command.index("--db") + 1], str(database.resolve()))
            self.assertEqual(run.call_args.kwargs["cwd"], str(kb_root.resolve()))
            self.assertFalse(run.call_args.kwargs.get("shell", False))

    def test_paper_search_sets_explicit_db_command_and_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skills_home = self.build_runtime(root)
            database = root / "library.sqlite3"
            database.write_bytes(b"sqlite")
            response = json.dumps({"query": "Bellman", "results": []})
            with patch.object(integrations.subprocess, "run", return_value=completed([], response)) as run:
                result = integrations.run_paper_search(
                    "Bellman optimality",
                    database,
                    aliases=["Bellman 最优性", "Bellman 最优性"],
                    skills_home=skills_home,
                    python_executable="python-test",
                )
            self.assertTrue(result["ok"])
            command = run.call_args.args[0]
            self.assertIn("--db", command)
            self.assertEqual(command[command.index("--db") + 1], str(database.resolve()))
            self.assertIn("--compact", command)
            self.assertEqual(command.count("--alias"), 1)
            environment = run.call_args.kwargs["env"]
            expected_root = skills_home / "skills" / "obsidian-vault-notes" / "scripts" / "obsidian_local_kb"
            self.assertEqual(environment["OBSIDIAN_LOCAL_KB_ROOT"], str(expected_root))
            self.assertFalse(run.call_args.kwargs.get("shell", False))

    def test_paper_search_does_not_hide_downstream_error_diagnostics(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skills_home = self.build_runtime(root)
            database = root / "library.sqlite3"
            database.write_bytes(b"sqlite")
            response = json.dumps(
                {
                    "query": "Bellman",
                    "results": [],
                    "diagnostics": [{"level": "error", "code": "schema_error"}],
                }
            )
            with patch.object(
                integrations.subprocess,
                "run",
                return_value=completed([], response),
            ):
                result = integrations.run_paper_search(
                    "Bellman",
                    database,
                    skills_home=skills_home,
                    python_executable="python-test",
                )
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "downstream_diagnostics")

    def test_paper_locate_uses_one_explicit_db_and_returns_bounded_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skills_home = self.build_runtime(root)
            database = root / "library.sqlite3"
            database.write_bytes(b"sqlite")
            response = json.dumps(self.paper_locate_payload(), ensure_ascii=False)
            with patch.object(
                integrations.subprocess,
                "run",
                return_value=completed([], response),
            ) as run:
                result = integrations.run_paper_locate(
                    "Ω(n log n) comparison sorting lower bound",
                    database,
                    aliases=["sorting lower bound", "sorting lower bound"],
                    skills_home=skills_home,
                    python_executable="python-test",
                )
            self.assertTrue(result["ok"])
            self.assertEqual(result["schema_version"], "paper-locate/v1")
            self.assertEqual(result["results"][0]["origin"], "adjacent")
            self.assertIn("Omega(n log n)", result["results"][0]["statement_window"])
            self.assertEqual(result["results"][0]["extraction"]["status"], "indexed")
            self.assertEqual(
                result["results"][0]["features"]["hard_concepts_required"],
                ["lower_bound", "worst_case"],
            )
            self.assertEqual(result["timing_ms"]["core"], 10.125)
            self.assertNotIn("command", result)
            command = run.call_args.args[0]
            self.assertTrue(str(command[2]).endswith("observer_run.py"))
            self.assertIn("pdf-paper-search.script.paper_locate", command)
            self.assertIn(
                str(
                    skills_home
                    / "skills"
                    / "pdf-paper-search"
                    / "scripts"
                    / "paper_locate.py"
                ),
                command,
            )
            self.assertEqual(command.count("--db"), 1)
            self.assertEqual(command[command.index("--db") + 1], str(database.resolve()))
            self.assertEqual(command.count("--alias"), 1)
            self.assertEqual(command[-1], "--json")
            self.assertLessEqual(
                len(json.dumps(result, ensure_ascii=False).encode("utf-8")),
                10 * 1024,
            )

    def test_shared_raw_fixture_matches_adapter_contract(self):
        fixture = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "references"
                / "paper-locate-v1-fixture.json"
            ).read_text(encoding="utf-8")
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skills_home = self.build_runtime(root)
            database = root / "library.sqlite3"
            database.write_bytes(b"sqlite")
            with patch.object(
                integrations.subprocess,
                "run",
                return_value=completed(
                    [], json.dumps(fixture, ensure_ascii=False)
                ),
            ):
                result = integrations.run_paper_locate(
                    "comparison sorting worst case",
                    database,
                    skills_home=skills_home,
                )
        self.assertTrue(result["ok"])
        self.assertTrue(result["results"][0]["verified"])
        self.assertEqual(result["results"][0]["origin"], "adjacent")
        self.assertTrue(result["results"][0]["statement_window"])
        self.assertEqual(
            result["coverage"]["document_status_counts"]["no_text"], 2
        )
        self.assertEqual(result["timing_ms"]["core"], 124.375)

    def test_paper_locate_accepts_structured_coverage_gap_exit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skills_home = self.build_runtime(root)
            database = root / "library.sqlite3"
            database.write_bytes(b"sqlite")
            response = json.dumps(self.paper_locate_payload("coverage_gap"))
            with patch.object(
                integrations.subprocess,
                "run",
                return_value=completed([], response, returncode=2),
            ):
                result = integrations.run_paper_locate(
                    "not present",
                    database,
                    skills_home=skills_home,
                )
            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "coverage_gap")

    def test_paper_locate_blocks_unverified_printed_page(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skills_home = self.build_runtime(root)
            database = root / "library.sqlite3"
            database.write_bytes(b"sqlite")
            payload = self.paper_locate_payload()
            payload["results"][0]["printed_page"] = 9
            with patch.object(
                integrations.subprocess,
                "run",
                return_value=completed([], json.dumps(payload)),
            ):
                result = integrations.run_paper_locate(
                    "sorting lower bound",
                    database,
                    skills_home=skills_home,
                )
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "unverified_printed_page")

    def test_paper_locate_does_not_require_unrelated_obsidian_setup_entry(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skills_home = root / "skills-home"
            pdf_script = (
                skills_home
                / "skills"
                / "pdf-paper-search"
                / "scripts"
                / "paper_locate.py"
            )
            pdf_script.parent.mkdir(parents=True)
            pdf_script.write_text("# test\n", encoding="utf-8")
            (pdf_script.parent / "observer_run.py").write_text(
                "# test\n", encoding="utf-8"
            )
            database = root / "library.sqlite3"
            database.write_bytes(b"sqlite")
            response = json.dumps(self.paper_locate_payload(), ensure_ascii=False)
            with patch.object(
                integrations.subprocess,
                "run",
                return_value=completed([], response),
            ):
                result = integrations.run_paper_locate(
                    "sorting worst case",
                    database,
                    skills_home=skills_home,
                )
            self.assertTrue(result["ok"])

    def test_paper_locate_rejects_absolute_or_escaping_pdf_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skills_home = self.build_runtime(root)
            database = root / "library.sqlite3"
            database.write_bytes(b"sqlite")
            for unsafe in (
                r"C:\private\book.pdf",
                "/private/book.pdf",
                "../outside.pdf",
                "nested/../../outside.pdf",
            ):
                with self.subTest(path=unsafe):
                    payload = self.paper_locate_payload()
                    payload["results"][0]["path"] = unsafe
                    with patch.object(
                        integrations.subprocess,
                        "run",
                        return_value=completed([], json.dumps(payload)),
                    ):
                        result = integrations.run_paper_locate(
                            "sorting lower bound",
                            database,
                            skills_home=skills_home,
                        )
                    self.assertFalse(result["ok"])
                    self.assertEqual(
                        result["error"]["code"], "invalid_paper_locate_result"
                    )

    def test_paper_locate_rejects_duplicate_pdf_results(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skills_home = self.build_runtime(root)
            database = root / "library.sqlite3"
            database.write_bytes(b"sqlite")
            payload = self.paper_locate_payload("ambiguous")
            duplicate = dict(payload["results"][0])
            duplicate["rank"] = 2
            duplicate["pdf_page"] = 18
            payload["results"].append(duplicate)
            with patch.object(
                integrations.subprocess,
                "run",
                return_value=completed([], json.dumps(payload)),
            ):
                result = integrations.run_paper_locate(
                    "sorting lower bound",
                    database,
                    skills_home=skills_home,
                )
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "duplicate_paper_locate_pdf")

    def test_paper_locate_rejects_false_verified_hit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skills_home = self.build_runtime(root)
            database = root / "library.sqlite3"
            database.write_bytes(b"sqlite")
            payload = self.paper_locate_payload()
            payload["results"][0]["features"]["hard_concepts_missing"] = ["worst_case"]
            with patch.object(
                integrations.subprocess,
                "run",
                return_value=completed([], json.dumps(payload)),
            ):
                result = integrations.run_paper_locate(
                    "sorting lower bound",
                    database,
                    skills_home=skills_home,
                )
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "invalid_verified_hit")

    def test_oversize_verified_payload_keeps_verification_invariants(self):
        payload = self.paper_locate_payload()
        payload["query"]["hard_concepts"] = ["概念" * 100] * 30
        payload["query"]["signature_terms"] = ["特征" * 100] * 30
        payload["results"][0]["statement_window"] = "正文" * 5000
        payload["results"][0]["why"] = ["理由" * 500] * 10
        payload["results"][0]["features"]["hard_concepts_required"] = [
            "要求" * 100
        ] * 20
        payload["results"][0]["features"]["hard_concepts_found"] = [
            "命中" * 100
        ] * 20
        payload["coverage"]["warnings"] = [
            {"level": "warning", "code": "large", "message": "警告" * 500}
        ] * 10
        result = integrations._compact_paper_locate_payload(payload)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "verified_hit")
        features = result["results"][0]["features"]
        self.assertTrue(features["local_statement"])
        self.assertTrue(features["direct_statement"])
        self.assertEqual(features["hard_concepts_missing"], [])
        self.assertLessEqual(
            len(
                json.dumps(
                    result, ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")
            ),
            10 * 1024,
        )


class OcrTests(unittest.TestCase):
    def build_tools(self, state: Path) -> dict[str, str]:
        override_tesseract = state / "external" / "tesseract.exe"
        override_tesseract.parent.mkdir(parents=True)
        override_tesseract.write_bytes(b"exe")
        paths = ocr.discover_ocr_tools(state, tesseract_path=override_tesseract)
        Path(paths["venv_python"]).parent.mkdir(parents=True)
        Path(paths["venv_python"]).write_bytes(b"python")
        Path(paths["tessdata"]).mkdir(parents=True)
        hocr_config = Path(paths["tessdata"]) / "configs" / "hocr"
        hocr_config.parent.mkdir(parents=True)
        hocr_config.write_text("tessedit_create_hocr 1", encoding="utf-8")
        return paths

    @staticmethod
    def successful_probe(command, **kwargs):
        if "--version" in command and "ocrmypdf" in command:
            return completed(command, "", "17.8.0\n")
        if "--list-langs" in command:
            return completed(command, "List of available languages (4):\neng\nchi_sim\nchi_tra\nosd\n")
        if "--version" in command:
            return completed(command, "tesseract 5.5.0\n")
        if "-c" in command:
            return completed(command, "4.30.0\n")
        return completed(command, "")

    def test_preflight_reports_missing_dependencies_without_subprocess(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(ocr.subprocess, "run") as run:
                result = ocr.preflight_ocr(Path(temporary))
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "missing_dependencies")
        self.assertGreaterEqual(len(result["diagnostics"]), 2)
        run.assert_not_called()

    def test_preflight_checks_required_languages(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            paths = self.build_tools(state)

            def incomplete_languages(command, **kwargs):
                result = self.successful_probe(command, **kwargs)
                if "--list-langs" in command:
                    return completed(command, "List of available languages (2):\neng\nosd\n")
                return result

            with patch.object(ocr.subprocess, "run", side_effect=incomplete_languages):
                result = ocr.preflight_ocr(state, tesseract_path=paths["tesseract"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["languages"]["missing"], ["chi_sim", "chi_tra"])
        self.assertEqual(
            [item["language"] for item in result["diagnostics"] if item["code"] == "missing_language"],
            ["chi_sim", "chi_tra"],
        )

    def test_run_ocr_refuses_input_as_output_and_library_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            library = root / "library"
            outside = root / "outside"
            library.mkdir()
            outside.mkdir()
            source = library / "book.pdf"
            source.write_bytes(b"pdf")
            same = ocr.run_ocr_one(source, source, outside / "book.txt", root / "state")
            in_library = ocr.run_ocr_one(
                source,
                library / "ocr.pdf",
                outside / "ocr.txt",
                root / "state",
                library_root=library,
            )
        self.assertEqual(same["error"]["code"], "input_equals_output")
        self.assertEqual(in_library["error"]["code"], "library_write_refused")

    def test_run_ocr_command_and_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "state"
            paths = self.build_tools(state)
            source = root / "input.pdf"
            output = root / "result.pdf"
            sidecar = root / "result.txt"
            source.write_bytes(b"pdf")

            def execute(command, **kwargs):
                result = self.successful_probe(command, **kwargs)
                if "--sidecar" in command:
                    output.write_bytes(b"ocr-pdf")
                    sidecar.write_text("text", encoding="utf-8")
                return result

            with patch.object(ocr.subprocess, "run", side_effect=execute) as run:
                result = ocr.run_ocr_one(
                    source,
                    output,
                    sidecar,
                    state,
                    tesseract_path=paths["tesseract"],
                )
            self.assertTrue(result["ok"])
            command = run.call_args_list[-1].args[0]
            self.assertIn("--skip-text", command)
            self.assertIn("--rotate-pages", command)
            self.assertEqual(command[command.index("--output-type") + 1], "pdf")
            self.assertEqual(command[command.index("--language") + 1], "eng+chi_sim")
            self.assertEqual(command[command.index("--sidecar") + 1], str(sidecar.resolve()))
            environment = run.call_args_list[-1].kwargs["env"]
            self.assertEqual(environment["TESSDATA_PREFIX"], paths["tessdata"])
            self.assertTrue(environment["PATH"].startswith(paths["venv_bin"] + os.pathsep))
            self.assertFalse(run.call_args_list[-1].kwargs.get("shell", False))


if __name__ == "__main__":
    unittest.main()
