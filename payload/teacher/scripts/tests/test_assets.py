"""Layered memory: stable anchors, plan-hash preflight, stale detection, budgets."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from th import Store, detect_stale, locate_by_content, make_index_plan, read_packet  # noqa: E402
from th import constants as C  # noqa: E402
from th.assets import AssetError, apply_index_plan, estimate_tokens  # noqa: E402

from support import Harness  # noqa: E402


class Corpus:
    """A tiny workspace on disk."""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.write("notes/a.md", "# A\n\n第一个笔记的内容。\n")
        self.write("notes/b.md", "# B\n\n第二个笔记。\n")
        self.write("data/table.csv", "x,y\n1,2\n")

    def write(self, relative: str, text: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def close(self) -> None:
        self._tmp.cleanup()


class AnchorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.corpus = Corpus()
        self.harness = Harness()
        self.store = self.harness.store

    def tearDown(self) -> None:
        self.harness.close()
        self.corpus.close()

    def _index(self) -> dict:
        plan = make_index_plan(self.store, self.corpus.root)
        return apply_index_plan(self.store, plan, expect_plan_sha256=plan["plan_sha256"])

    def test_plan_is_a_dry_run(self) -> None:
        plan = make_index_plan(self.store, self.corpus.root)
        self.assertTrue(plan["dry_run"])
        self.assertEqual(plan["entry_count"], 3)
        self.assertEqual(self.store.anchors(), [], "a plan must write nothing")
        self.assertEqual(self.store.head(C.HEAD_EXECUTION), "execution-genesis")

    def test_applying_a_plan_requires_its_exact_hash(self) -> None:
        plan = make_index_plan(self.store, self.corpus.root)
        with self.assertRaises(AssetError) as caught:
            apply_index_plan(self.store, plan, expect_plan_sha256="0" * 64)
        self.assertEqual(caught.exception.code, C.ERR_PLAN_HASH)
        self.assertEqual(self.store.anchors(), [])

    def test_a_tampered_plan_is_refused_before_its_hash_is_compared(self) -> None:
        plan = make_index_plan(self.store, self.corpus.root)
        plan["entries"] = plan["entries"][:1]
        with self.assertRaises(AssetError) as caught:
            apply_index_plan(self.store, plan, expect_plan_sha256=plan["plan_sha256"])
        self.assertEqual(caught.exception.code, C.ERR_PLAN_STALE)

    def test_anchor_identity_survives_a_move(self) -> None:
        """This is why an identifier must not encode a path."""

        self._index()
        anchors = {anchor["relative_path"]: anchor["anchor_id"] for anchor in self.store.anchors()}
        original = anchors["data/table.csv"]

        moved = self.corpus.root / "data" / "renamed.csv"
        (self.corpus.root / "data" / "table.csv").rename(moved)

        located = locate_by_content(self.store, self.corpus.root, "data/renamed.csv")
        self.assertIsNotNone(located)
        self.assertEqual(located["anchor_id"], original)
        self.assertEqual(located["previous_path"], "data/table.csv")

        self.store.observe(
            event_type="anchor_moved",
            payload={"anchor_id": original},
            mutate=lambda connection: self.store.move_anchor(
                connection, anchor_id=original, relative_path="data/renamed.csv"
            ),
        )
        history = self.store.anchor_history(original)
        self.assertEqual([entry["relative_path"] for entry in history], ["data/table.csv", "data/renamed.csv"])
        self.assertEqual(self.store.anchor(original)["anchor_id"], original)

    def test_stale_detection_reports_modified_missing_and_unindexed(self) -> None:
        self._index()
        self.corpus.write("notes/a.md", "# A\n\n内容变了。\n")
        (self.corpus.root / "notes" / "b.md").unlink()
        self.corpus.write("notes/c.md", "# C\n")

        report = detect_stale(self.store, self.corpus.root)
        modified = {item["relative_path"] for item in report["modified"]}
        missing = {item["relative_path"] for item in report["missing"]}
        self.assertIn("notes/a.md", modified)
        self.assertIn("notes/b.md", missing)
        self.assertIn("notes/c.md", report["unindexed"])
        self.assertGreaterEqual(report["stale_count"], 3)
        # It reports observations, not claims about content equality.
        self.assertIn("not claimed", report["checks"])

    def test_stale_anchor_is_flagged_by_the_claim_guard(self) -> None:
        self._index()
        anchor_id = self.store.anchors()[0]["anchor_id"]
        self.store.observe(
            event_type="anchor_stale",
            payload={"anchor_id": anchor_id},
            mutate=lambda connection: self.store.mark_anchor_stale(connection, [anchor_id]),
        )
        harness = self.harness
        from support import claim

        bad = claim(anchor_ids=[anchor_id])
        risks = harness.engine.guard.deterministic.check_claim(bad)
        self.assertIn(C.RISK_FOREIGN_ANCHOR, risks)

    def test_unknown_anchor_is_a_distinct_risk_from_a_stale_one(self) -> None:
        from support import claim

        risks = self.harness.engine.guard.deterministic.check_claim(claim(anchor_ids=["AN-doesnotexist"]))
        self.assertIn(C.RISK_UNKNOWN_ANCHOR, risks)
        self.assertNotIn(C.RISK_FOREIGN_ANCHOR, risks)


class DisclosureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = Harness()

    def tearDown(self) -> None:
        self.harness.close()

    def test_read_levels_grow_monotonically(self) -> None:
        harness, _ = self.harness, None
        record_id = harness.accepted_claim()
        sizes = []
        for profile in ("metadata", "small", "custom"):
            packet = read_packet(harness.store, record_ids=[record_id], profile=profile)
            sizes.append(packet["chars"])
        self.assertLessEqual(sizes[0], sizes[1])
        self.assertLessEqual(sizes[1], sizes[2])

    def test_small_packet_is_bounded(self) -> None:
        harness = self.harness
        ids = [harness.accepted_claim() for _ in range(5)]
        packet = read_packet(harness.store, record_ids=ids, profile="small")
        self.assertLessEqual(len(packet["items"]), C.READ_PACKET_SMALL_MAX_RECORDS)
        self.assertLessEqual(packet["chars"], C.READ_PACKET_SMALL_MAX_CHARS)
        self.assertTrue(packet["truncated"])

    def test_a_custom_packet_may_not_exceed_the_declared_ceiling(self) -> None:
        harness = self.harness
        record_id = harness.accepted_claim()
        with self.assertRaises(AssetError) as caught:
            read_packet(
                harness.store,
                record_ids=[record_id],
                profile="small",
                max_chars=C.READ_PACKET_DEFAULT_MAX_CHARS + 1,
            )
        self.assertEqual(caught.exception.code, C.ERR_BUDGET_EXCEEDED)

    def test_context_packet_refuses_when_protected_context_exceeds_budget(self) -> None:
        from th.assets import build_context_packet

        harness = self.harness
        with self.assertRaises(AssetError) as caught:
            build_context_packet(
                harness.store,
                contract={"objective": {"statement": "x" * 5000}},
                actor="worker",
                purpose="research",
                max_tokens=10,
            )
        self.assertEqual(caught.exception.code, C.ERR_BUDGET_EXCEEDED)

    def test_context_packet_never_truncates_protected_context(self) -> None:
        from th.assets import build_context_packet

        harness = self.harness
        objective = {"statement": "一个目标", "evidence_standard": C.GRADE_EXACT_REPRODUCTION}
        packet = build_context_packet(
            harness.store,
            contract={"objective": objective, "open_obligations": ["o1"]},
            actor="worker",
            purpose="research",
            record_ids=[],
        )
        self.assertEqual(packet["protected"]["objective"], objective)
        self.assertEqual(packet["protected"]["open_obligations"], ["o1"])

    def test_unknown_actor_is_refused(self) -> None:
        from th.assets import build_context_packet

        with self.assertRaises(AssetError):
            build_context_packet(self.harness.store, contract={}, actor="nobody", purpose="x")

    def test_token_estimate_is_deterministic(self) -> None:
        value = {"a": "中文内容", "b": [1, 2, 3]}
        self.assertEqual(estimate_tokens(value), estimate_tokens(json.loads(json.dumps(value))))


if __name__ == "__main__":
    unittest.main()
