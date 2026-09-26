"""Retrieval, assets, archive candidates and attached use."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from th import archive, retrieval  # noqa: E402
from th.attached import check_answer, frame_check  # noqa: E402
from th.broker import Broker, OperationRefused, validate_request  # noqa: E402
from th.cognition import current_cognition  # noqa: E402
from th.store import Store  # noqa: E402
from th import constants as C  # noqa: E402

CANARY = "MODEL_CANARY_c0ac4::no-surface::END"


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.store = Store(self.root / "s.sqlite3")
        self.store.init()

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()


class RetrievalTests(Base):
    def setUp(self) -> None:
        super().setUp()
        self.notes = self.root / "notes"
        self.notes.mkdir()
        (self.notes / "adam.md").write_text("# Adam\n\nAdam keeps running averages of gradients.\n\n# Bias\n\nBias correction divides by 1-beta^t.\n", encoding="utf-8")

    def index(self) -> dict:
        plan = retrieval.plan_index(self.notes)
        return retrieval.apply_index(self.store, plan, expect_plan_sha256=plan["plan_sha256"])

    def test_indexing_requires_the_exact_plan_hash(self) -> None:
        plan = retrieval.plan_index(self.notes)
        with self.assertRaises(retrieval.RetrievalError):
            retrieval.apply_index(self.store, plan, expect_plan_sha256="0" * 64)
        self.assertEqual(retrieval.index_status(self.store)["roots"], [])

    def test_search_returns_candidates_not_bodies(self) -> None:
        self.index()
        found = retrieval.search_sections(self.store, "running averages")
        self.assertEqual(found["total_matches"], 1)
        candidate = found["candidates"][0]
        self.assertEqual(set(candidate), {"section_id", "path", "heading", "snippet"})
        self.assertLessEqual(len(candidate["snippet"]), retrieval.SNIPPET_CHARS)
        self.assertIn("coverage_note", found)

    def test_reading_rereads_the_current_file(self) -> None:
        self.index()
        section = retrieval.search_sections(self.store, "Bias correction")["candidates"][0]["section_id"]
        (self.notes / "adam.md").write_text("# Adam\n\nchanged\n\n# Bias\n\nBias correction divides by (1-beta^t), updated.\n", encoding="utf-8")
        read = retrieval.read_section(self.store, section)
        self.assertEqual(read["state"], "changed_since_index")
        self.assertIn("updated", read["text"])
        (self.notes / "adam.md").write_text("# Other\n\nnothing\n", encoding="utf-8")
        self.assertEqual(retrieval.read_section(self.store, section)["state"], "stale")

    def test_network_operations_obey_policy(self) -> None:
        broker = Broker(read_roots=(), store=self.store, network={"enabled": False})
        with self.assertRaises(OperationRefused):
            broker.execute(validate_request({"operation": "fetch_url", "arguments": {"url": "https://arxiv.org/abs/1706.03762"}}))
        broker = Broker(read_roots=(), store=self.store, network={"enabled": True, "allow_domains": ["arxiv.org"]})
        with self.assertRaises(OperationRefused):
            broker.execute(validate_request({"operation": "fetch_url", "arguments": {"url": "https://evil.example/x"}}))

    def test_arxiv_results_become_excerpts(self) -> None:
        class Fake:
            @staticmethod
            def arxiv_search(query, policy):
                return {"entries": [{"id": "1706.03762", "url": "http://arxiv.org/abs/1706.03762v7", "title": "Attention Is All You Need",
                                     "published": "2017-06-12", "authors": ["A. Vaswani"], "summary": "The dominant sequence transduction models..."}],
                        "total_matches": 40, "omitted": 39}

        broker = Broker(read_roots=(), store=self.store, network={"enabled": True}, fetcher=Fake())
        result = broker.execute(validate_request({"operation": "arxiv_search", "arguments": {"query": "attention"}}))
        self.assertEqual(result["value"]["omitted"], 39)
        self.assertEqual(result["excerpts"][0]["kind"], "retrieved_source")
        self.assertIn("Attention Is All You Need", result["excerpts"][0]["text"])

    def test_declared_command_output_reaches_the_model_only_as_an_excerpt(self) -> None:
        broker = Broker(read_roots=(self.root,), declared_commands={"tests": ("true",)},
                        executor=lambda command, cwd, timeout: (0, "3 passed\n", ""))
        result = broker.execute(validate_request({"operation": "run_declared", "arguments": {"name": "tests", "arguments": []}}))
        self.assertNotIn("stdout", result["value"])
        self.assertEqual(result["excerpts"][0]["kind"], "executed_check")
        self.assertEqual(result["excerpts"][0]["returncode"], 0)


def _ask_with(points, unknowns=(), ask_id="ASK-1"):
    return {"ask_id": ask_id, "answer": {"points": points, "unknowns": list(unknowns)}}


def _point(text, basis="retrieved_source", grade="bounded_empirical", evidence=("E-1",), quote=None):
    return {"text": text, "strength": "bounded", "basis": basis, "grade": grade, "evidence": list(evidence),
            "quote": quote, "cannot_imply": "不能推出更大规模也成立"}


class ArchiveTests(Base):
    def seed(self) -> None:
        evidence = {"E-1": {"kind": "retrieved_source", "locator": "notes/a.md#A", "text": "x"},
                    "E-2": {"kind": "executed_check", "locator": "run:bench:returncode=1", "text": "fail", "returncode": 1}}
        candidates = archive.candidates_from_answer(
            _ask_with([_point("结论一"), _point("仅推理", basis="reasoning", grade=None, evidence=())], ["未知一"]),
            evidence, self.store)

        def mutate(connection):
            for candidate in candidates:
                self.store.put_candidate(connection, candidate)
            return {"action": "seed"}

        self.store.transact(operation_id="seed", head=C.HEAD_EXECUTION, expected=self.store.head(C.HEAD_EXECUTION),
                            request={}, mutate=mutate)

    def test_reasoning_never_becomes_a_claim_candidate(self) -> None:
        self.seed()
        kinds = sorted(item["kind"] for item in self.store.candidates())
        self.assertEqual(kinds, ["claim", "failure", "open_question", "source"])
        self.assertNotIn("仅推理", [item["text"] for item in self.store.candidates()])

    def test_duplicates_are_not_proposed_twice(self) -> None:
        self.seed()
        again = archive.candidates_from_answer(_ask_with([_point("结论 一")]), {"E-1": {"kind": "retrieved_source", "locator": "notes/a.md#A", "text": "x"}}, self.store)
        self.assertEqual(again, [])

    def test_one_decision_bound_to_the_shown_list(self) -> None:
        self.seed()
        proposal = archive.propose(self.store)
        before = self.store.head(C.HEAD_AUTHORITY)
        with self.assertRaises(archive.ArchiveError):
            archive.apply(self.store, proposal, choice="all", expect_plan_sha256="f" * 64)
        self.assertEqual(self.store.head(C.HEAD_AUTHORITY), before)
        receipt = archive.apply(self.store, proposal, choice="verified_only", expect_plan_sha256=proposal["plan_sha256"])
        self.assertEqual(receipt["archived"], 3)          # claim, source, failure
        self.assertEqual(receipt["kept_pending"], 1)      # the open question stays pending
        self.assertNotEqual(self.store.head(C.HEAD_AUTHORITY), before)
        # The archived material is now found before anything else is searched.
        self.assertEqual(retrieval.search_library(self.store, "结论一")["total_matches"], 1)

    def test_cognition_is_recomputed_from_the_library(self) -> None:
        self.seed()
        proposal = archive.propose(self.store)
        archive.apply(self.store, proposal, choice="verified_only", expect_plan_sha256=proposal["plan_sha256"])
        cognition = current_cognition(self.store)
        self.assertIn("结论一", cognition["text"])
        self.assertIn("不能推出更大规模也成立", cognition["text"])
        self.assertTrue(cognition["receipt"]["continues"])




class AttachedTests(Base):
    def test_frame_check_applies_the_same_rule(self) -> None:
        draft = {"kind": "concept_explanation", "stuck_point": "concept", "clarity": "ambiguous", "depth": "brief",
                 "interpretations": [{"text": "想知道动机", "quote": "为什么", "differs_by": "goal"},
                                     {"text": "想看推导", "quote": "除以", "differs_by": "output_form"}],
                 "material_ambiguity": True, "investigation_can_resolve": False, "defaults": []}
        result = frame_check(self.store, "为什么要除以根号d", draft)
        self.assertTrue(result["ask_requester"])
        self.assertIn("1) 想知道动机", result["visible"])

    def test_check_answer_rereads_local_files(self) -> None:
        notes = self.root / "notes"
        notes.mkdir()
        (notes / "a.md").write_text("Gradient clipping bounds the update norm.\n", encoding="utf-8")
        answer = {"conclusion": "裁剪限制更新范数。", "confidence": "medium", "explanation": [], "next_checks": [],
                  "followups": [], "unknowns": [],
                  "points": [{"text": "梯度裁剪限制每步更新的范数。", "strength": "observation", "basis": "retrieved_source",
                              "grade": None, "evidence": ["E1"], "quote": "Gradient clipping bounds the update norm.",
                              "cannot_imply": None}]}
        good = [{"id": "E1", "kind": "retrieved_source", "locator": "a.md", "text": "Gradient clipping bounds the update norm."}]
        result = check_answer(self.store, brief="梯度裁剪是干嘛的", kind="implementation_help", answer=answer,
                              evidence=good, roots=[str(notes)], network={"enabled": False})
        self.assertEqual(result["verification"]["E1"], "verified")
        self.assertEqual(result["metrics"]["excerpts_verified_by_skill"], 1)
        self.assertIn("片段由 Teacher 在原位置核对 1 条、只能采信 agent 0 条", result["visible"])
        forged = [{"id": "E1", "kind": "retrieved_source", "locator": "a.md", "text": "Gradient clipping bounds the update norm."
                   .replace("bounds", "removes")}]
        answer_forged = json.loads(json.dumps(answer).replace("bounds", "removes"))
        with self.assertRaises(Exception):
            # The excerpt is not in the file, so it is dropped, and the point citing it no longer resolves.
            check_answer(self.store, brief="梯度裁剪是干嘛的", kind="implementation_help", answer=answer_forged,
                         evidence=forged, roots=[str(notes)], network={"enabled": False})


