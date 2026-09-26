from __future__ import annotations

import json
import io
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from pdf_paper_search.features import build_page_evidence
from pdf_paper_search.locator import (
    MAX_WIRE_BYTES,
    bound_payload,
    bounded_json,
    locate_paper,
)
from pdf_paper_search.normalization import (
    PAPER_CANONICALIZER_VERSION,
    normalize_paper_query,
)
from pdf_paper_search.query_spec import build_query_spec
from paper_search import query_relevant_statement_window, rerank_bonus


SCRIPT_DIR = Path(__file__).resolve().parent
CONTRACT_PATH = SCRIPT_DIR.parent / "references" / "paper-anchor-contract-v1.json"
RAW_FIXTURE_PATH = SCRIPT_DIR.parent / "references" / "paper-locate-v1-fixture.json"

LOWER_BOUND_QUERY = "Theorem: lower bound Ω(n log n) for comparison sorting in the worst case"
TARGET_STATEMENT = (
    "Theorem 10. Every comparison sort needs Omega(n log n) comparisons "
    "in the worst case; this lower bound is tight. target statement"
)


def make_index(path: Path, *, extra_incomplete_doc: bool = False) -> None:
    con = sqlite3.connect(path)
    try:
        con.executescript(
            """
            CREATE TABLE pdf_docs (
                id INTEGER PRIMARY KEY,
                path TEXT UNIQUE,
                title TEXT,
                status TEXT,
                extraction_method TEXT,
                extraction_warning TEXT
            );
            CREATE TABLE pdf_pages (
                id INTEGER PRIMARY KEY,
                doc_id INTEGER,
                page_number INTEGER,
                content TEXT,
                snippet TEXT,
                char_count INTEGER
            );
            CREATE TABLE pdf_page_fts (page_id TEXT);
            INSERT INTO pdf_docs VALUES
                (1, 'sorting.pdf', 'Sorting Lower Bounds', 'indexed', 'fixture', NULL);
            INSERT INTO pdf_pages VALUES
                (1, 1, 5, 'nearby discussion', 'nearby discussion', 17),
                (2, 1, 6, 'Theorem 3. Every comparison sort needs Omega(n log n) comparisons in the worst case; lower bound TARGET', 'Theorem 3', 104);
            """
        )
        if extra_incomplete_doc:
            con.execute(
                "INSERT INTO pdf_docs VALUES (2, 'scan.pdf', 'Scan', 'no_text', NULL, 'no text')"
            )
        con.commit()
    finally:
        con.close()


def make_ranked_index(path: Path, *, target_text: str, count: int = 10) -> None:
    con = sqlite3.connect(path)
    try:
        con.executescript(
            """
            CREATE TABLE pdf_docs (
                id INTEGER PRIMARY KEY,
                path TEXT UNIQUE,
                title TEXT,
                status TEXT,
                extraction_method TEXT,
                extraction_warning TEXT
            );
            CREATE TABLE pdf_pages (
                id INTEGER PRIMARY KEY,
                doc_id INTEGER,
                page_number INTEGER,
                content TEXT,
                snippet TEXT,
                char_count INTEGER
            );
            CREATE TABLE pdf_page_fts (page_id TEXT);
            """
        )
        for index in range(1, count + 1):
            is_target = index == count
            title = "Original Paper" if is_target else f"Book {index}"
            doc_path = "arxiv-2101.00001.pdf" if is_target else f"book-{index}.pdf"
            content = target_text if is_target else f"Theorem {index}. nearby discussion"
            con.execute(
                "INSERT INTO pdf_docs VALUES (?, ?, ?, 'indexed', 'fixture', NULL)",
                (index, doc_path, title),
            )
            con.execute(
                "INSERT INTO pdf_pages VALUES (?, ?, 1, ?, ?, ?)",
                (index, index, content, content, len(content)),
            )
        con.commit()
    finally:
        con.close()


class FakeBackend:
    def __init__(
        self,
        db_path: Path,
        *,
        return_hits: bool = True,
        neighbor_exact: bool = True,
        expanded_same_pdf: bool = False,
    ) -> None:
        self.db_path = db_path
        self.return_hits = return_hits
        self.neighbor_exact = neighbor_exact
        self.expanded_same_pdf = expanded_same_pdf

    def reset_diagnostics(self) -> None:
        return None

    def get_diagnostics(self) -> list[object]:
        return []

    def build_aliases(self, query: str, aliases: list[str], mode: str) -> list[str]:
        core = ["lower bound worst case comparison"]
        return core if mode == "core" else [*core, "comparison sort lower bound"]

    def aggregate_hits(
        self, db_paths: list[Path], aliases: list[str], limit: int, query: str
    ) -> list[SimpleNamespace]:
        if not self.return_hits:
            return []
        expanded = "comparison sort lower bound" in aliases
        page_number = 6 if expanded and self.expanded_same_pdf else 5
        return [
            SimpleNamespace(
                db=str(self.db_path),
                path="sorting.pdf",
                title="Sorting Lower Bounds",
                page_number=page_number,
                classification="near-exact",
                reasons=["seed is nearby"],
                snippet="nearby discussion",
                statement_window="nearby discussion",
                final_score=20.0 if expanded else 10.0,
                features={
                    "page_role": "theorem",
                    "local_statement": True,
                    "direct_statement": False,
                    "hard_concepts_missing": ["lower_bound"],
                },
            )
        ]

    def rerank_bonus(
        self, *, search_spec: str, title: str, path: str, snippet: str, content: str
    ) -> tuple[float, list[str], str, object]:
        exact = self.neighbor_exact and "TARGET" in content
        evidence = build_page_evidence(
            page_role="theorem",
            local_statement=exact,
            direct_statement=exact,
            hard_concepts_required={"lower_bound", "worst_case"},
            evidence_text=content,
            statement_window=content,
        )
        return (
            50.0 if exact else 0.0,
            ["adjacent exact statement"] if exact else ["adjacent nearby"],
            "exact hit" if exact else "nearby material",
            evidence,
        )


class RankedBackend:
    def __init__(
        self,
        db_path: Path,
        *,
        core_exact_rank: int | None = None,
        anchor_hit: bool = False,
        signature_hit: bool = False,
    ) -> None:
        self.db_path = db_path
        self.core_exact_rank = core_exact_rank
        self.anchor_hit = anchor_hit
        self.signature_hit = signature_hit

    def reset_diagnostics(self) -> None:
        return None

    def get_diagnostics(self) -> list[object]:
        return []

    def build_aliases(self, query: str, aliases: list[str], mode: str) -> list[str]:
        return ["stable anchor"]

    def original_source_score(
        self, title: str, path: str
    ) -> tuple[float, list[str]]:
        if "arxiv" in f"{title} {path}".casefold():
            return 24.0, ["original paper source (arXiv/proceedings)"]
        return 0.0, []

    def aggregate_hits(
        self, db_paths: list[Path], aliases: list[str], limit: int, query: str
    ) -> list[SimpleNamespace]:
        hits: list[SimpleNamespace] = []
        for rank in range(1, 11):
            is_target = rank == self.core_exact_rank
            doc_path = "arxiv-2101.00001.pdf" if rank == 10 else f"book-{rank}.pdf"
            title = "Original Paper" if rank == 10 else f"Book {rank}"
            statement = TARGET_STATEMENT if is_target else f"Theorem {rank}. nearby discussion"
            hits.append(
                SimpleNamespace(
                    db=str(self.db_path),
                    path=doc_path,
                    title=title,
                    page_number=1,
                    classification="exact hit" if is_target else "nearby material",
                    reasons=["ranked fixture"],
                    snippet=statement,
                    statement_window=statement,
                    final_score=float(100 - rank),
                    features={
                        "page_role": "theorem",
                        "local_statement": True,
                        "direct_statement": is_target,
                        "hard_concepts_missing": [] if is_target else ["lower_bound"],
                    },
                )
            )
        return hits

    def anchor_rescue_hits(
        self, *, db_path: Path, query: str, spec: object, limit: int
    ) -> list[SimpleNamespace]:
        if not self.anchor_hit:
            return []
        con = sqlite3.connect(self.db_path)
        try:
            content = str(
                con.execute(
                    """
                    SELECT p.content
                    FROM pdf_docs d
                    JOIN pdf_pages p ON p.doc_id = d.id
                    WHERE d.path = 'arxiv-2101.00001.pdf'
                    """
                ).fetchone()[0]
            )
        finally:
            con.close()
        return [
            SimpleNamespace(
                db=str(self.db_path),
                path="arxiv-2101.00001.pdf",
                title="Original Paper",
                page_number=1,
                classification="exact hit",
                reasons=["anchor rescue fixture"],
                snippet=content,
                statement_window=content,
                final_score=200.0,
                features={
                    "page_role": "theorem",
                    "local_statement": True,
                    "direct_statement": True,
                    "hard_concepts_missing": [],
                },
            )
        ]

    def signature_rescue_hits(
        self, *, db_path: Path, query: str, spec: object, limit: int
    ) -> list[SimpleNamespace]:
        if not self.signature_hit:
            return []
        statement = "Theorem 10. Any policy gradient method suffers a regret lower bound target statement"
        return [
            SimpleNamespace(
                db=str(self.db_path),
                path="arxiv-2101.00001.pdf",
                title="Original Paper",
                page_number=1,
                classification="exact hit",
                reasons=["signature rescue fixture"],
                snippet=statement,
                statement_window=statement,
                final_score=180.0,
                features={
                    "page_role": "theorem",
                    "local_statement": True,
                    "direct_statement": True,
                    "hard_concepts_missing": [],
                },
            )
        ]

    def rerank_bonus(
        self, *, search_spec: str, title: str, path: str, snippet: str, content: str
    ) -> tuple[float, list[str], str, object]:
        evidence = build_page_evidence(
            page_role="theorem",
            local_statement=True,
            direct_statement=False,
            hard_concepts_required=set(),
            evidence_text=content,
            statement_window=content,
        )
        return 0.0, ["nearby fixture"], "nearby material", evidence


class PaperCanonicalContractTests(unittest.TestCase):
    def test_anchor_contract_vectors(self) -> None:
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        self.assertEqual(contract["contract_version"], "ascii-paper-anchor/v1")
        self.assertEqual(contract["query_canonicalizer_version"], PAPER_CANONICALIZER_VERSION)
        for vector in contract["vectors"]:
            tokens = set(re.findall(r"[a-z0-9]+", normalize_paper_query(vector["input"])))
            self.assertTrue(
                set(vector["required_tokens"]).issubset(tokens),
                msg=f"{vector['id']}: {sorted(tokens)}",
            )

    def test_query_spec_recognizes_complexity_hard_concepts(self) -> None:
        spec = build_query_spec(LOWER_BOUND_QUERY)
        self.assertTrue({"lower_bound", "worst_case"}.issubset(spec.hard_concepts))
        self.assertIn("complexity:omega:nlogn", spec.exact_anchors)
        self.assertEqual(spec.query_type, "theorem_lookup")

    def test_ocr_bridges_remain_compatible(self) -> None:
        normalized = normalize_paper_query("x 1 and n^{2} and QKᵀ")
        self.assertIn("x1", normalized.split())
        self.assertIn("n2", normalized.split())

    def test_lowercase_parameter_is_not_a_complexity_anchor(self) -> None:
        self.assertEqual(build_query_spec("policy π_θ(a|s) with f_θ(t)").exact_anchors, ())

    def test_later_numbered_statement_is_selected_by_query_content(self) -> None:
        page = """
        Theorem 1. Every convex function on a compact set attains its minimum.
        Theorem 2. Merge sort uses O(n log n) comparisons.
        Theorem 3. Every comparison sort needs Omega(n log n) comparisons
        in the worst case, a lower bound.
        """
        window = query_relevant_statement_window(LOWER_BOUND_QUERY, page, "theorem")
        self.assertIn("theorem 3", window)
        self.assertIn("lower bound", window)
        self.assertNotIn("theorem 1", window)

    def test_exact_gate_requires_the_lower_bound_statement(self) -> None:
        query = (
            "Theorem. Any comparison-based sorting algorithm requires Ω(n log n) "
            "comparisons in the worst case; this is a lower bound."
        )
        exact_page = """
        Theorem 1. Insertion sort runs in quadratic time on reversed input.
        Theorem 4. Any comparison-based sorting algorithm requires Omega(n log n)
        comparisons in the worst case; this lower bound is tight.
        """
        decoy_page = """
        Theorem 4. Merge sort is a comparison-based sorting algorithm that uses
        O(n log n) comparisons in the worst case, an upper bound.
        """
        _score, _reasons, exact_class, exact_evidence = rerank_bonus(
            query, "Sorting Notes", "sorting.pdf", "", exact_page
        )
        _score, _reasons, decoy_class, decoy_evidence = rerank_bonus(
            query, "Sorting Notes", "decoy.pdf", "", decoy_page
        )
        self.assertEqual(exact_class, "exact hit")
        self.assertTrue(exact_evidence.direct_statement)
        self.assertNotEqual(decoy_class, "exact hit")
        self.assertIn("lower_bound", decoy_evidence.hard_concepts_missing)

    def test_related_work_citation_is_not_the_original_statement(self) -> None:
        page = """
        2 Related Work
        Knuth et al. [3] proved that every comparison sort requires Omega(n log n)
        comparisons in the worst case, the classic lower bound for sorting.
        """
        _score, reasons, classification, evidence = rerank_bonus(
            LOWER_BOUND_QUERY, "Survey of Sorting", "sorting-survey.pdf", "", page
        )
        self.assertNotEqual(classification, "exact hit")
        self.assertEqual(evidence.page_role, "related_work")
        self.assertTrue(evidence.overview_reference)
        self.assertTrue(any("not the original" in reason for reason in reasons))

    def test_model_variant_anchor_blocks_a_neighbor_variant(self) -> None:
        query = "Table 3 ResNet-50 top-1 accuracy on ImageNet"
        exact_page = """
        Table 3: Single-model top-1 accuracy on ImageNet validation.
        ResNet-50 top-1 76.1
        ResNet-101 top-1 77.4
        """
        variant_page = """
        Table 3: Single-model top-1 accuracy on ImageNet validation.
        ResNet-101 top-1 77.4
        ResNet-152 top-1 78.3
        """
        _score, _reasons, exact_class, exact_evidence = rerank_bonus(
            query, "Deep Residual Learning", "arxiv-1512.03385.pdf", "", exact_page
        )
        _score, _reasons, variant_class, _variant_evidence = rerank_bonus(
            query, "Deep Residual Learning", "variant.pdf", "", variant_page
        )
        self.assertEqual(exact_evidence.page_role, "results_table")
        self.assertEqual(exact_class, "exact hit")
        self.assertNotEqual(variant_class, "exact hit")

    def test_generic_signature_rescue_preserves_short_formula_identifiers(self) -> None:
        spec = build_query_spec(
            "证明策略梯度 ∇θ J(θ) = E[∇θ log πθ(a|s) Q(s,a)]"
        )
        self.assertIn("pi", spec.signature_terms)
        self.assertTrue(
            any(
                {"policy", "gradient", "log", "pi"}.issubset(set(alias.split()))
                for alias in spec.signature_rescue_queries
            )
        )
        self.assertIn("policy gradient", spec.normalized)

    def test_rescue_accepts_noncontiguous_complexity_phrase(self) -> None:
        spec = build_query_spec(
            "Show that the time and space complexity of self-attention are both O(n^2)"
        )
        self.assertIn("time_complexity", spec.hard_concepts)
        self.assertIn("space_complexity", spec.hard_concepts)
        self.assertIn("complexity:o:n2", spec.exact_anchors)
        self.assertTrue(
            any(
                {"time", "space", "complexity"}.issubset(set(alias.split()))
                for alias in spec.signature_rescue_queries
            )
        )


class PaperLocatorTests(unittest.TestCase):
    def test_neighbor_page_can_close_the_loop(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "fixture.sqlite3"
            make_index(db_path)
            payload = locate_paper(
                query=LOWER_BOUND_QUERY,
                db_path=db_path,
                backend=FakeBackend(db_path),
            )
        self.assertEqual(payload["status"], "verified_hit")
        self.assertEqual(payload["search"]["stop_reason"], "verified_core_hit")
        self.assertEqual(payload["search"]["adjacent_page_count"], 1)
        self.assertFalse(payload["search"]["expanded"])
        self.assertEqual(payload["results"][0]["pdf_page"], 6)
        self.assertEqual(payload["results"][0]["adjacent_offset"], 1)
        self.assertEqual(payload["results"][0]["origin"], "adjacent")
        self.assertTrue(payload["results"][0]["verified"])
        self.assertEqual(payload["results"][0]["confidence"], "high")
        self.assertEqual(payload["results"][0]["extraction"]["status"], "indexed")
        self.assertIn("statement_window", payload["results"][0])
        raw_fixture = json.loads(RAW_FIXTURE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], raw_fixture["schema_version"])
        self.assertEqual(payload["canonicalizer_version"], raw_fixture["canonicalizer_version"])
        self.assertEqual(set(payload["results"][0]), set(raw_fixture["results"][0]))
        self.assertLessEqual(len(bounded_json(payload).encode("utf-8")), MAX_WIRE_BYTES)

    def test_missing_explicit_db_is_a_coverage_gap(self) -> None:
        payload = locate_paper(
            query=LOWER_BOUND_QUERY,
            db_path=Path("definitely-missing.sqlite3"),
            backend=FakeBackend(Path("definitely-missing.sqlite3")),
        )
        self.assertEqual(payload["status"], "coverage_gap")
        self.assertEqual(payload["search"]["stop_reason"], "missing_db")

    def test_global_no_text_warning_does_not_hide_scoped_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "fixture.sqlite3"
            make_index(db_path, extra_incomplete_doc=True)
            payload = locate_paper(
                query=LOWER_BOUND_QUERY,
                db_path=db_path,
                backend=FakeBackend(db_path, return_hits=False),
            )
        self.assertEqual(payload["status"], "not_found_in_indexed_text")
        self.assertTrue(payload["coverage"]["incomplete"])

    def test_core_and_expanded_alternatives_remain_distinct_by_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "fixture.sqlite3"
            make_index(db_path)
            payload = locate_paper(
                query=LOWER_BOUND_QUERY,
                db_path=db_path,
                backend=FakeBackend(
                    db_path,
                    neighbor_exact=False,
                    expanded_same_pdf=True,
                ),
            )
        self.assertEqual(payload["status"], "ambiguous")
        self.assertTrue(payload["search"]["expanded"])
        self.assertEqual(len(payload["results"]), 1)
        self.assertFalse(payload["results"][0]["verified"])
        self.assertNotEqual(payload["results"][0]["confidence"], "high")

    def test_sixth_distinct_pdf_can_be_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "ranked.sqlite3"
            make_ranked_index(db_path, target_text=TARGET_STATEMENT)
            payload = locate_paper(
                query=LOWER_BOUND_QUERY,
                db_path=db_path,
                backend=RankedBackend(db_path, core_exact_rank=6),
            )
        self.assertEqual(payload["status"], "verified_hit")
        self.assertEqual(payload["results"][0]["path"], "book-6.pdf")
        self.assertGreaterEqual(payload["search"]["verified_page_count"], 6)

    def test_verification_window_remains_capped_at_eight_pdfs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "ranked.sqlite3"
            make_ranked_index(db_path, target_text=TARGET_STATEMENT)
            payload = locate_paper(
                query=LOWER_BOUND_QUERY,
                db_path=db_path,
                backend=RankedBackend(db_path, core_exact_rank=9),
            )
        self.assertEqual(payload["status"], "ambiguous")
        self.assertEqual(payload["search"]["verified_page_count"], 8)

    def test_exact_anchor_rescue_can_recover_beyond_normal_rank(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "ranked.sqlite3"
            make_ranked_index(
                db_path,
                target_text=(
                    "Theorem 12. (a) Any comparison-based sort requires "
                    "Omega(n log n) comparisons in the worst case (lower bound)."
                ),
            )
            payload = locate_paper(
                query="证明比较排序在最坏情况下的下界 Ω(n log n)",
                db_path=db_path,
                backend=RankedBackend(db_path, anchor_hit=True),
            )
        self.assertEqual(payload["status"], "verified_hit")
        self.assertTrue(payload["search"]["anchor_rescue_used"])
        self.assertEqual(
            payload["search"]["stop_reason"], "verified_anchor_rescue_hit"
        )
        self.assertEqual(payload["results"][0]["path"], "arxiv-2101.00001.pdf")

    def test_wrong_formula_cannot_pass_anchor_rescue(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "ranked.sqlite3"
            make_ranked_index(
                db_path,
                target_text=(
                    "Theorem 12. Any comparison-based sort requires "
                    "Omega(n^2) comparisons in the worst case (lower bound)."
                ),
            )
            payload = locate_paper(
                query="证明比较排序在最坏情况下的下界 Ω(n log n)",
                db_path=db_path,
                backend=RankedBackend(db_path, anchor_hit=True),
            )
        self.assertNotEqual(payload["status"], "verified_hit")
        self.assertEqual(payload["search"]["stop_reason"], "signature_rescue_no_exact")

    def test_signature_rescue_can_recover_beyond_normal_rank(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "ranked.sqlite3"
            make_ranked_index(
                db_path,
                target_text="Theorem 10. Any policy gradient method suffers a regret lower bound target statement",
            )
            payload = locate_paper(
                query="Theorem: regret lower bound for policy gradient methods",
                db_path=db_path,
                backend=RankedBackend(db_path, signature_hit=True),
            )
        self.assertEqual(payload["status"], "verified_hit")
        self.assertTrue(payload["search"]["signature_rescue_used"])
        self.assertEqual(
            payload["search"]["stop_reason"],
            "verified_signature_rescue_hit",
        )

    def test_pathological_output_becomes_small_structured_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "fixture.sqlite3"
            make_index(db_path)
            payload = locate_paper(
                query=LOWER_BOUND_QUERY,
                db_path=db_path,
                backend=FakeBackend(db_path),
            )
        pathological = deepcopy(payload)
        pathological["results"][0]["path"] = "x" * 100_000
        pathological["query"]["signature_terms"] = ["y" * 10_000] * 20
        wire_payload = bound_payload(pathological)
        encoded = bounded_json(wire_payload)
        self.assertEqual(wire_payload["status"], "failed")
        self.assertEqual(
            wire_payload["query"],
            {"query_type": None, "hard_concepts": [], "signature_terms": []},
        )
        self.assertEqual(wire_payload["search"]["stop_reason"], "output_too_large")
        self.assertEqual(wire_payload["results"], [])
        self.assertTrue(wire_payload["coverage"]["incomplete"])
        self.assertLessEqual(len(encoded.encode("utf-8")), MAX_WIRE_BYTES)

    def test_cli_unexpected_error_is_structured(self) -> None:
        import paper_locate as cli

        output = io.StringIO()
        argv = [
            "paper_locate.py",
            "--query",
            "lower bound worst case",
            "--db",
            "fixture.sqlite3",
            "--json",
        ]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(cli, "locate_paper", side_effect=RuntimeError("private detail")),
            redirect_stdout(output),
        ):
            exit_code = cli.main()
        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(
            payload["query"],
            {"query_type": None, "hard_concepts": [], "signature_terms": []},
        )
        self.assertEqual(payload["search"]["stop_reason"], "unexpected_error")
        self.assertEqual(
            payload["coverage"]["warnings"][0]["exception_type"], "RuntimeError"
        )

    def test_cli_requires_db(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT_DIR / "paper_locate.py"), "--query", "lower bound worst case"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(json.loads(completed.stdout)["error"], "invalid_request")


if __name__ == "__main__":
    unittest.main()
