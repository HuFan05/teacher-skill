"""Requirement intake.

Intake is where a requester is least able to describe what they want, so it is
exactly where a model would most like to take over. These tests assert the
opposite: the field, the question and the completion decision stay with the
harness, and the model's contribution is a pointer into the requester's own
words plus closed enums.
"""

from __future__ import annotations

import unittest

from support import ALLOW, CANARY, Harness, deny

from th import constants as C
from th.engine import EngineError
from th.intake import (
    IntakeError,
    apply_draft,
    completeness,
    next_gap,
    new_state,
    packet,
    set_field,
    to_objective,
    validate_intake_draft,
)
from th.model import ModelError
from th.model import OBJECTIVE_FIELDS
from th.render import INTAKE_ASSUMPTION_PHRASE

BRIEF = "我这个训练老是抖，想知道换掉损失函数之后到底有没有用，最好能在我们自己的数据上看出来。"
GAP = "statement"


def draft(**overrides):
    base = {
        "decision": "continue",
        "field": GAP,
        "quote": None,
        "absent": False,
        "evidence_standard": None,
        "assumed_defaults": [],
        "blocking_code": None,
    }
    base.update(overrides)
    return base


class GapOrderTests(unittest.TestCase):
    def test_the_first_missing_field_in_declared_order_is_the_gap(self) -> None:
        state = new_state(BRIEF)
        self.assertEqual(next_gap(state), "statement")
        state = set_field(state, "statement", "换成 focal loss 后指标是否改善")
        self.assertEqual(next_gap(state), "domain")

    def test_the_gap_sequence_does_not_depend_on_the_model(self) -> None:
        state = new_state(BRIEF)
        order = []
        while True:
            field = next_gap(state)
            if field is None:
                break
            order.append(field)
            state = set_field(state, field, C.GRADE_BOUNDED_EMPIRICAL if field == "evidence_standard" else "x")
        self.assertEqual(order, list(C.INTAKE_ORDER))


class DraftValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = new_state(BRIEF)

    def test_a_model_cannot_answer_a_field_the_harness_did_not_choose(self) -> None:
        with self.assertRaises(ModelError):
            validate_intake_draft(draft(field="claim_scope"), brief=BRIEF, gap=GAP, state=self.state)

    def test_a_quote_that_is_not_in_the_brief_is_refused(self) -> None:
        with self.assertRaises(ModelError):
            validate_intake_draft(
                draft(quote="这句不在原文里"), brief=BRIEF, gap=GAP, state=self.state
            )

    def test_a_model_cannot_declare_readiness_before_the_shape_is_complete(self) -> None:
        with self.assertRaises(ModelError):
            validate_intake_draft(
                draft(decision="ready"), brief=BRIEF, gap=GAP, state=self.state
            )

    def test_an_undeclared_default_is_refused(self) -> None:
        with self.assertRaises(ModelError):
            validate_intake_draft(
                draft(assumed_defaults=["whatever_it_wants"]),
                brief=BRIEF,
                gap=GAP,
                state=self.state,
            )

    def test_undeclared_keys_are_refused(self) -> None:
        with self.assertRaises(ModelError):
            validate_intake_draft(
                {**draft(), "suggested_statement": "我来替你写一句"},
                brief=BRIEF,
                gap=GAP,
                state=self.state,
            )

    def test_a_verbatim_quote_is_accepted(self) -> None:
        validated = validate_intake_draft(
            draft(quote="想知道换掉损失函数之后到底有没有用"),
            brief=BRIEF,
            gap=GAP,
            state=self.state,
        )
        self.assertEqual(validated["quote"], "想知道换掉损失函数之后到底有没有用")


class ApplyTests(unittest.TestCase):
    def test_a_verbatim_quote_fills_the_field_and_only_that_field(self) -> None:
        state = new_state(BRIEF)
        updated = apply_draft(state, draft(quote="想知道换掉损失函数之后到底有没有用"))
        self.assertEqual(updated["fields"]["statement"], "想知道换掉损失函数之后到底有没有用")
        self.assertEqual(next_gap(updated), "domain")

    def test_declaring_a_field_absent_does_not_fill_it(self) -> None:
        state = new_state(BRIEF)
        updated = apply_draft(state, draft(absent=True))
        self.assertNotIn("statement", updated["fields"])
        self.assertIn("statement", updated["absent"])
        self.assertEqual(updated["round"], 1)

    def test_rounds_are_counted(self) -> None:
        state = new_state(BRIEF)
        self.assertEqual(apply_draft(state, draft(absent=True))["round"], 1)

    def test_completion_is_computed_not_declared(self) -> None:
        state = new_state(BRIEF)
        for field, value in (
            ("statement", "s"),
            ("domain", "d"),
            ("claim_scope", "m"),
            ("assumptions", ["a"]),
            ("completion_standard", "cs"),
        ):
            state = set_field(state, field, value)
        state = apply_draft(state, draft(field=None, evidence_standard=C.GRADE_BOUNDED_EMPIRICAL))
        self.assertTrue(completeness(state)["complete"])
        self.assertEqual(state["status"], "ready")


class CommitTests(unittest.TestCase):
    def _complete(self):
        state = new_state(BRIEF)
        for field, value in (
            ("statement", "换掉损失函数后指标是否改善"),
            ("domain", "监督分类"),
            ("claim_scope", "held-out accuracy"),
            ("assumptions", ["固定数据划分"]),
            ("evidence_standard", C.GRADE_EXACT_REPRODUCTION),
            ("completion_standard", "固定种子下逐字节可复现"),
        ):
            state = set_field(state, field, value)
        return state

    def test_an_incomplete_intake_cannot_produce_an_objective(self) -> None:
        with self.assertRaises(IntakeError):
            to_objective(new_state(BRIEF), assumption_phrases=INTAKE_ASSUMPTION_PHRASE)

    def test_closed_defaults_become_stated_assumptions(self) -> None:
        state = self._complete()
        state = apply_draft(state, draft(field=None, assumed_defaults=["fixed_seeds"]))
        objective = to_objective(state, assumption_phrases=INTAKE_ASSUMPTION_PHRASE)
        self.assertIn("随机种子固定", objective["assumptions"])

    def test_the_objective_carries_exactly_the_constitutive_fields(self) -> None:
        objective = to_objective(self._complete(), assumption_phrases=INTAKE_ASSUMPTION_PHRASE)
        self.assertEqual(sorted(objective), sorted(OBJECTIVE_FIELDS))


class EngineIntakeTests(unittest.TestCase):
    def test_intake_advances_execution_and_never_authority(self) -> None:
        with Harness() as fixture:
            before = fixture.store.head(C.HEAD_AUTHORITY)
            fixture.engine.intake_begin(BRIEF)
            fixture.queue_intake(draft(absent=True))
            result = fixture.engine.intake_turn()
            self.assertEqual(result["revision_kind"], "execution")
            self.assertEqual(fixture.store.head(C.HEAD_AUTHORITY), before)

    def test_the_question_text_is_renderer_authored(self) -> None:
        with Harness() as fixture:
            fixture.engine.intake_begin(BRIEF)
            fixture.queue_intake(draft(quote="想知道换掉损失函数之后到底有没有用"))
            result = fixture.engine.intake_turn()
            self.assertIn("问题（由 Skill 生成）", result["visible"])
            self.assertIn("想知道换掉损失函数之后到底有没有用", result["visible"])
            self.assertNotIn(CANARY, result["visible"])

    def test_rounds_are_bounded(self) -> None:
        with Harness() as fixture:
            fixture.engine.intake_begin(BRIEF)
            for _ in range(C.INTAKE_MAX_ROUNDS):
                fixture.queue_intake(draft(absent=True))
                fixture.engine.intake_turn()
            fixture.queue_intake(draft(absent=True))
            with self.assertRaises(EngineError) as caught:
                fixture.engine.intake_turn()
            self.assertEqual(caught.exception.code, C.ERR_INTAKE_ROUNDS_EXHAUSTED)

    def test_a_guard_denial_leaves_the_intake_where_it_was(self) -> None:
        with Harness() as fixture:
            before = fixture.store.head(C.HEAD_EXECUTION)
            fixture.engine.intake_begin(BRIEF)
            fixture.queue_intake(draft(absent=True), verdict=deny(C.RISK_SCOPE_UNDECLARED))
            fixture.queue_intake(draft(absent=True))
            result = fixture.engine.intake_turn()
            self.assertEqual(result["status"], "ok")
            self.assertNotEqual(fixture.store.head(C.HEAD_EXECUTION), before)

    def test_committing_requires_a_complete_shape(self) -> None:
        with Harness() as fixture:
            fixture.engine.intake_begin(BRIEF)
            with self.assertRaises(IntakeError):
                fixture.engine.intake_commit()

    def test_commit_emits_an_objective_without_binding_it(self) -> None:
        with Harness() as fixture:
            before = fixture.store.head(C.HEAD_AUTHORITY)
            fixture.engine.intake_begin(BRIEF)
            for field, value in (
                ("statement", "换掉损失函数后指标是否改善"),
                ("domain", "监督分类"),
                ("claim_scope", "held-out accuracy"),
                ("assumptions", ["固定数据划分"]),
                ("completion_standard", "固定种子下逐字节可复现"),
            ):
                fixture.engine.intake_set(field, value)
            fixture.engine.intake_standard(C.GRADE_EXACT_REPRODUCTION)
            result = fixture.engine.intake_commit()
            self.assertEqual(result["next"], "open-objective")
            self.assertEqual(fixture.store.head(C.HEAD_AUTHORITY), before)

    def test_the_packet_shows_only_closed_values_and_the_brief(self) -> None:
        state = new_state(BRIEF)
        built = packet(state)
        self.assertEqual(built["gap"], "statement")
        self.assertEqual(built["brief"], BRIEF)
        self.assertEqual(sorted(built["evidence_standards"]), sorted(C.GRADES))


class CompletionConsultTests(unittest.TestCase):
    """A consulted reviewer may veto. It may never approve."""

    def test_a_claim_of_sufficiency_cannot_unblock_a_blocked_assessment(self) -> None:
        with Harness() as fixture:
            fixture.queue_completion(
                {"sufficient": True, "risk_codes": []},
                verdict=ALLOW,
            )
            result = fixture.engine.assess_completion(record_id=None, consult=True)
            self.assertEqual(result["assessment"]["status"], C.COMPLETION_NOT_COMPLETE)
            self.assertTrue(result["assessment"]["completion_review"]["advisory_sufficient"])

    def test_a_denial_blocks_completion(self) -> None:
        with Harness() as fixture:
            fixture.queue_completion(
                {"sufficient": True, "risk_codes": []},
                verdict=deny(C.RISK_EVIDENCE_INSUFFICIENT),
            )
            result = fixture.engine.assess_completion(record_id=None, consult=True)
            self.assertIn(C.ISSUE_REVIEW_DENIED, result["assessment"]["issue_codes"])

    def test_an_unavailable_reviewer_is_not_a_decision(self) -> None:
        with Harness() as fixture:
            result = fixture.engine.assess_completion(record_id=None, consult=True)
            self.assertEqual(result["assessment"]["completion_review"]["state"], "unavailable")
            self.assertNotIn(C.ISSUE_REVIEW_DENIED, result["assessment"]["issue_codes"])

    def test_without_consultation_no_backend_is_called(self) -> None:
        with Harness() as fixture:
            fixture.engine.assess_completion(record_id=None)
            self.assertEqual(fixture.backend.calls, [])


if __name__ == "__main__":
    unittest.main()
