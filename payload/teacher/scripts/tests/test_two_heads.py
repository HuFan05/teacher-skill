"""The central invariant: only promotion and terminal completion move authority.

If this file passes, then "the model kept working and thereby changed the
conclusion" is not a rule anyone has to remember — it is not expressible.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from th import make_draft  # noqa: E402
from th import constants as C  # noqa: E402
from th.backend import empty_patch  # noqa: E402
from th.guard import GuardDenied  # noqa: E402
from th.store import StoreError  # noqa: E402

from support import ALLOW, Harness, claim, deny, route, scope  # noqa: E402


class AuthorityVersusExecutionTests(unittest.TestCase):
    def test_every_execution_operation_leaves_authority_untouched(self) -> None:
        with Harness() as harness:
            authority_before = harness.store.head(C.HEAD_AUTHORITY)
            window = harness.open_window()
            attempt_id = window["attempts"][0]
            self.assertEqual(harness.store.head(C.HEAD_AUTHORITY), authority_before)

            harness.queue_turn()
            self.assertEqual(harness.engine.turn("一个提交", actor="worker")["status"], "ok")
            self.assertEqual(harness.store.head(C.HEAD_AUTHORITY), authority_before)

            harness.engine.checkpoint(attempt_id, body={}, verified=True)
            self.assertEqual(harness.store.head(C.HEAD_AUTHORITY), authority_before)

            record_id = harness.engine.submit_claim(claim())["record_id"]
            self.assertEqual(harness.store.head(C.HEAD_AUTHORITY), authority_before)

            harness.engine.review_claim(record_id, decision=C.REVIEW_ACCEPTED)
            self.assertEqual(harness.store.head(C.HEAD_AUTHORITY), authority_before)

            harness.engine.close_attempt(attempt_id, outcome=C.OUTCOME_CANDIDATE_FOUND)
            self.assertEqual(harness.store.head(C.HEAD_AUTHORITY), authority_before)

            # Execution moved many times; authority moved zero times.
            self.assertNotEqual(harness.store.head(C.HEAD_EXECUTION), "execution-genesis")

    def test_promotion_is_the_only_authority_transition(self) -> None:
        with Harness() as harness:
            record_id = harness.accepted_claim()
            harness.engine.checkpoint(
                harness.open_window()["attempts"][0], body={}, verified=True
            )
            before = harness.store.head(C.HEAD_AUTHORITY)
            result = harness.engine.promote(record_id)
            self.assertEqual(result["previous_authority_revision"], before)
            self.assertNotEqual(result["authority_revision"], before)
            self.assertEqual(harness.store.head(C.HEAD_AUTHORITY), result["authority_revision"])

    def test_denied_promotion_preserves_the_previous_authority_revision(self) -> None:
        with Harness() as harness:
            # A claim that is submitted but never reviewed cannot be promoted.
            record_id = harness.engine.submit_claim(claim())["record_id"]
            before = harness.store.head(C.HEAD_AUTHORITY)
            with self.assertRaises(GuardDenied) as caught:
                harness.engine.promote(record_id)
            self.assertIn(C.RISK_ASSESSMENT_INCONSISTENT, caught.exception.risk_codes)
            self.assertEqual(harness.store.head(C.HEAD_AUTHORITY), before)
            # Nothing was written for the failed attempt.
            self.assertEqual(harness.store.verify_chain(C.HEAD_AUTHORITY)["length"], 0)

    def test_failed_turn_preserves_authority_and_records_only_a_receipt(self) -> None:
        with Harness(drafts=[make_draft(focus_quote="这不是原文")], verdicts=[ALLOW]) as harness:
            authority_before = harness.store.head(C.HEAD_AUTHORITY)
            execution_before = harness.store.head(C.HEAD_EXECUTION)
            result = harness.engine.turn("原文", actor="worker")
            self.assertEqual(result["status"], "failed")
            self.assertEqual(harness.store.head(C.HEAD_AUTHORITY), authority_before)
            # A failure changed no state, so neither head moved.
            self.assertEqual(harness.store.head(C.HEAD_EXECUTION), execution_before)
            failures = [row for row in harness.store.events() if row["type"] == "turn_failed"]
            self.assertTrue(failures, "a failure must still be observable")

    def test_checkpoint_cannot_promote_a_claim(self) -> None:
        """A checkpoint is local progress. The claim's effect is derived from
        its reviews, which a checkpoint cannot reach."""

        with Harness() as harness:
            window = harness.open_window()
            record_id = harness.engine.submit_claim(claim())["record_id"]
            self.assertEqual(harness.store.review_state(record_id), C.REVIEW_UNREVIEWED)
            self.assertEqual(harness.engine.record_effect(record_id), C.EFFECT_HISTORICAL)
            for _ in range(5):
                harness.engine.checkpoint(window["attempts"][0], body={}, verified=True)
            self.assertEqual(harness.store.review_state(record_id), C.REVIEW_UNREVIEWED)
            self.assertEqual(harness.engine.record_effect(record_id), C.EFFECT_HISTORICAL)

    def test_promoted_claim_is_refused_when_evidence_grade_is_insufficient(self) -> None:
        with Harness() as harness:
            bad = claim(strength=C.STRENGTH_UNIVERSAL, grade=C.GRADE_NUMERICAL)
            with self.assertRaises(Exception) as caught:
                harness.engine.submit_claim(bad)
            self.assertIn(C.ERR_EVIDENCE_INSUFFICIENT, str(caught.exception))

    def test_open_window_is_atomic_on_invalid_portfolio(self) -> None:
        with Harness() as harness:
            before = harness.store.head(C.HEAD_EXECUTION)
            duplicate = [route(0), route(0, route_id="RT-x", label_code="route.other"), route(2)]
            with self.assertRaises(Exception) as caught:
                harness.engine.open_window(duplicate)
            self.assertIn(C.ERR_ROUTE_PORTFOLIO, str(caught.exception))
            self.assertEqual(harness.store.head(C.HEAD_EXECUTION), before)
            self.assertEqual(harness.store.attempts(), [])

    def test_two_routes_are_not_enough(self) -> None:
        with Harness() as harness:
            with self.assertRaises(Exception) as caught:
                harness.engine.open_window([route(0), route(1)])
            self.assertIn(C.ERR_ROUTE_PORTFOLIO, str(caught.exception))

    def test_routes_must_differ_on_every_axis(self) -> None:
        """Renaming a route is not a new route."""

        with Harness() as harness:
            same_axes = [
                route(0),
                route(0, route_id="RT-9", label_code="route.twin"),
                route(2),
            ]
            with self.assertRaises(Exception) as caught:
                harness.engine.open_window(same_axes)
            self.assertIn(C.ERR_ROUTE_PORTFOLIO, str(caught.exception))


class RollbackTests(unittest.TestCase):
    def test_invalid_outcome_rolls_back_and_preserves_the_head(self) -> None:
        with Harness() as harness:
            attempt_id = harness.open_window()["attempts"][0]
            before = harness.store.head(C.HEAD_EXECUTION)
            with self.assertRaises(StoreError) as caught:
                harness.engine.close_attempt(attempt_id, outcome="not_an_outcome")
            self.assertEqual(caught.exception.code, C.ERR_UNSUPPORTED_TRANSITION)
            self.assertEqual(harness.store.head(C.HEAD_EXECUTION), before)
            self.assertEqual(harness.store.attempt(attempt_id)["status"], C.ATTEMPT_OPEN)


if __name__ == "__main__":
    unittest.main()
