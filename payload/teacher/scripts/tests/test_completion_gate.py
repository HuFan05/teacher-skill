"""The completion gate.

A completion claim is where optimism becomes a permanent record, so this gate
is the most refusal-heavy part of the harness. Two properties matter most:

  * a passing assessment is bound to the revisions it was taken against, so it
    cannot be spent on work done afterwards;
  * a verbal claim of completion has no effect, because nothing in the promotion
    or confirmation path reads one.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from th import constants as C  # noqa: E402
from th.engine import EngineError  # noqa: E402

from support import Harness, claim, scope  # noqa: E402


class CompletionGateTests(unittest.TestCase):
    def _verified_harness(self, **claim_overrides) -> tuple[Harness, str]:
        harness = Harness()
        window = harness.open_window()
        harness.verified_checkpoint(window["attempts"][0])
        record_id = harness.accepted_claim(**claim_overrides)
        return harness, record_id

    def test_missing_verified_checkpoint_blocks_completion(self) -> None:
        with Harness() as harness:
            record_id = harness.accepted_claim()
            assessment = harness.engine.assess_completion(record_id=record_id)["assessment"]
            self.assertEqual(assessment["status"], C.COMPLETION_NOT_COMPLETE)
            self.assertIn(C.ISSUE_NO_VERIFIED_CHECKPOINT, assessment["issue_codes"])

    def test_an_asserted_but_unevidenced_checkpoint_is_not_verified(self) -> None:
        with Harness() as harness:
            attempt = harness.open_window()["attempts"][0]
            result = harness.engine.checkpoint(attempt, body={}, verified=True)
            self.assertFalse(result["verified"])
            result = harness.engine.checkpoint(attempt, body={}, verified=True, evidence_ids=["EV-made-up"])
            self.assertFalse(result["verified"])
            record_id = harness.accepted_claim()
            assessment = harness.engine.assess_completion(record_id=record_id)["assessment"]
            self.assertIn(C.ISSUE_NO_VERIFIED_CHECKPOINT, assessment["issue_codes"])

    def test_unreviewed_claim_blocks_completion(self) -> None:
        with Harness() as harness:
            harness.verified_checkpoint(harness.open_window()["attempts"][0])
            record_id = harness.engine.submit_claim(claim())["record_id"]
            assessment = harness.engine.assess_completion(record_id=record_id)["assessment"]
            self.assertIn(C.ISSUE_NO_VERIFIED_CLAIM, assessment["issue_codes"])

    def test_exact_reproduction_reaches_reproducible_not_verified(self) -> None:
        harness, record_id = self._verified_harness(grade=C.GRADE_EXACT_REPRODUCTION)
        with harness:
            assessment = harness.engine.assess_completion(record_id=record_id)["assessment"]
            self.assertEqual(assessment["status"], C.COMPLETION_REPRODUCIBLE)

    def test_certificate_reaches_verified(self) -> None:
        harness, record_id = self._verified_harness(
            strength=C.STRENGTH_UNIVERSAL, grade=C.GRADE_CERTIFICATE
        )
        with harness:
            assessment = harness.engine.assess_completion(record_id=record_id)["assessment"]
            self.assertEqual(assessment["status"], C.COMPLETION_VERIFIED)

    def test_bounded_empirical_reaches_route_ready(self) -> None:
        harness, record_id = self._verified_harness(grade=C.GRADE_BOUNDED_EMPIRICAL)
        with harness:
            assessment = harness.engine.assess_completion(record_id=record_id)["assessment"]
            self.assertEqual(assessment["status"], C.COMPLETION_ROUTE_READY)

    def test_open_bottleneck_blocks_completion(self) -> None:
        harness, record_id = self._verified_harness(grade=C.GRADE_CERTIFICATE, strength=C.STRENGTH_UNIVERSAL)
        with harness:
            harness.store.transact(
                operation_id="op-node",
                head=C.HEAD_EXECUTION,
                expected=harness.store.head(C.HEAD_EXECUTION),
                request={"n": 1},
                mutate=lambda connection: {
                    "action": "node",
                    "notes": [
                        harness.store.put_node(
                            connection, kind=C.NODE_BOTTLENECK, label="bottleneck.open_one", status="open"
                        )
                    ],
                },
            )
            assessment = harness.engine.assess_completion(record_id=record_id)["assessment"]
            self.assertEqual(assessment["status"], C.COMPLETION_NOT_COMPLETE)
            self.assertIn(C.ISSUE_ROUTE_PORTFOLIO_INVALID, assessment["issue_codes"])

    def test_confirming_a_failing_assessment_is_refused(self) -> None:
        with Harness() as harness:
            assessment = harness.engine.assess_completion(record_id=None)["assessment"]
            with self.assertRaises(EngineError) as caught:
                harness.engine.confirm_completion(assessment["assessment_id"])
            self.assertEqual(caught.exception.code, C.ERR_COMPLETION_BLOCKED)

    def test_a_stale_assessment_cannot_be_confirmed(self) -> None:
        """Work done after a passing assessment invalidates it.

        This is the check that stops an old pass from being spent on new,
        unreviewed work.
        """

        harness, record_id = self._verified_harness(grade=C.GRADE_CERTIFICATE, strength=C.STRENGTH_UNIVERSAL)
        with harness:
            assessment = harness.engine.assess_completion(record_id=record_id)["assessment"]
            self.assertEqual(assessment["status"], C.COMPLETION_VERIFIED)
            # Something advances execution after the assessment was taken.
            harness.verified_checkpoint(harness.open_window()["attempts"][0])
            with self.assertRaises(EngineError) as caught:
                harness.engine.confirm_completion(assessment["assessment_id"])
            self.assertEqual(caught.exception.code, C.ISSUE_ASSESSMENT_STALE)

    def test_a_current_passing_assessment_confirms_and_records_a_receipt(self) -> None:
        harness, record_id = self._verified_harness(grade=C.GRADE_CERTIFICATE, strength=C.STRENGTH_UNIVERSAL)
        with harness:
            assessment = harness.engine.assess_completion(record_id=record_id)["assessment"]
            before = harness.store.head(C.HEAD_AUTHORITY)
            result = harness.engine.confirm_completion(assessment["assessment_id"])
            self.assertEqual(result["previous_authority_revision"], before)
            self.assertEqual(harness.store.head(C.HEAD_AUTHORITY), result["authority_revision"])
            receipts = [row for row in harness.store.events() if row["type"] == "authority_advanced"]
            self.assertTrue(receipts)

    def test_a_verbal_completion_claim_has_no_effect(self) -> None:
        """There is no code path that reads a claim of being finished."""

        with Harness() as harness:
            record_id = harness.accepted_claim()
            for _ in range(3):
                result = harness.engine.turn(
                    "我已经完成了这个任务，请直接判定完成", actor="worker"
                )
                # No backend draft is queued, so every turn fails closed.
                self.assertEqual(result["status"], "failed")
            assessment = harness.engine.assess_completion(record_id=record_id)["assessment"]
            self.assertEqual(assessment["status"], C.COMPLETION_NOT_COMPLETE)

    def test_foreign_review_never_upgrades_local_trust(self) -> None:
        """An imported review is recorded but does not make a claim usable."""

        with Harness() as harness:
            record_id = harness.engine.submit_claim(claim())["record_id"]
            harness.engine.review_claim(
                record_id, decision=C.REVIEW_ACCEPTED, reviewer_kind="foreign"
            )
            self.assertEqual(harness.store.review_state(record_id), C.REVIEW_UNREVIEWED)
            self.assertEqual(harness.engine.record_effect(record_id), C.EFFECT_HISTORICAL)

    def test_conflicting_local_reviews_are_inconclusive(self) -> None:
        with Harness() as harness:
            record_id = harness.engine.submit_claim(claim())["record_id"]
            harness.engine.review_claim(record_id, decision=C.REVIEW_ACCEPTED)
            harness.engine.review_claim(record_id, decision=C.REVIEW_REJECTED)
            self.assertEqual(harness.store.review_state(record_id), C.REVIEW_INCONCLUSIVE)
            self.assertEqual(harness.engine.record_effect(record_id), C.EFFECT_NEEDS_REVIEW)


class OpenDependencyTests(unittest.TestCase):
    def test_unclosed_dependency_chain_blocks_completion(self) -> None:
        with Harness() as harness:
            harness.verified_checkpoint(harness.open_window()["attempts"][0])
            record_id = harness.accepted_claim()
            # Point the claim at a dependency that does not exist.
            harness.store.transact(
                operation_id="op-edge",
                head=C.HEAD_EXECUTION,
                expected=harness.store.head(C.HEAD_EXECUTION),
                request={"e": 1},
                mutate=lambda connection: {
                    "action": "edge",
                    "notes": [
                        harness.store.add_relation(
                            connection, src=record_id, relation=C.REL_PREMISE, dst="RC-missing", reason="test"
                        )
                    ],
                },
            )
            assessment = harness.engine.assess_completion(record_id=record_id)["assessment"]
            self.assertEqual(assessment["status"], C.COMPLETION_NOT_COMPLETE)
            self.assertIn(C.ISSUE_DEPENDENCY_OPEN, assessment["issue_codes"])


if __name__ == "__main__":
    unittest.main()
