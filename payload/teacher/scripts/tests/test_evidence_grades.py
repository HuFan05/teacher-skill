"""Evidence grades: a name fixes how strong a statement it may support.

The point of the table is that a result cannot be upgraded by describing it
more confidently. `numerical_evidence` cannot become a universal claim, and a
bounded claim must say what it does not imply.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from th import ModelError, validate_claim, validate_route_portfolio  # noqa: E402
from th import constants as C  # noqa: E402
from th.model import validate_grade_sufficient, validate_objective, validate_scope  # noqa: E402

from support import Harness, claim, route, scope  # noqa: E402


class GradeRuleTests(unittest.TestCase):
    def test_universal_claim_needs_formal_or_certificate(self) -> None:
        for grade in (C.GRADE_FORMAL, C.GRADE_CERTIFICATE):
            validate_grade_sufficient(grade, C.STRENGTH_UNIVERSAL)
        for grade in (C.GRADE_EXACT_REPRODUCTION, C.GRADE_BOUNDED_EMPIRICAL, C.GRADE_NUMERICAL):
            with self.assertRaises(ModelError) as caught:
                validate_grade_sufficient(grade, C.STRENGTH_UNIVERSAL)
            self.assertIn(C.ERR_EVIDENCE_INSUFFICIENT, str(caught.exception))

    def test_numerical_evidence_supports_an_observation_only(self) -> None:
        validate_grade_sufficient(C.GRADE_NUMERICAL, C.STRENGTH_OBSERVATION)
        for strength in (C.STRENGTH_UNIVERSAL, C.STRENGTH_CONDITIONAL, C.STRENGTH_BOUNDED):
            with self.assertRaises(ModelError):
                validate_grade_sufficient(C.GRADE_NUMERICAL, strength)

    def test_bounded_claim_cannot_rest_on_numerical_evidence(self) -> None:
        with self.assertRaises(ModelError):
            validate_claim(claim(grade=C.GRADE_NUMERICAL, strength=C.STRENGTH_BOUNDED))

    def test_a_claim_must_state_what_it_does_not_imply(self) -> None:
        with self.assertRaises(ModelError) as caught:
            validate_claim(claim(cannot_imply=[]))
        self.assertIn(C.ERR_CANNOT_IMPLY_MISSING, str(caught.exception))

    def test_a_claim_must_declare_its_scope(self) -> None:
        incomplete = scope()
        incomplete["datasets"] = []
        with self.assertRaises(ModelError) as caught:
            validate_claim(claim(scope=incomplete))
        self.assertIn(C.ERR_SCOPE_UNDECLARED, str(caught.exception))

    def test_unknown_scope_field_is_refused(self) -> None:
        with self.assertRaises(ModelError):
            validate_scope({**scope(), "vibes": ["good"]})

    def test_claim_must_declare_a_strength(self) -> None:
        with self.assertRaises(ModelError):
            validate_claim(claim(strength=None))

    def test_claim_must_have_evidence(self) -> None:
        with self.assertRaises(ModelError):
            validate_claim(claim(evidence_ids=[]))

    def test_unknown_field_is_refused_rather_than_ignored(self) -> None:
        with self.assertRaises(ModelError):
            validate_claim(claim(confidence=0.99))


class ObjectiveTests(unittest.TestCase):
    def test_objective_has_exactly_six_constitutive_fields(self) -> None:
        objective = {
            "statement": "s",
            "domain": "d",
            "claim_scope": "m",
            "assumptions": ["a"],
            "evidence_standard": C.GRADE_EXACT_REPRODUCTION,
            "completion_standard": "c",
        }
        validate_objective(objective)
        with self.assertRaises(ModelError):
            validate_objective({**objective, "created_at": "2026-01-01"})

    def test_changing_a_constitutive_field_changes_the_commitment(self) -> None:
        base = {
            "statement": "s",
            "domain": "d",
            "claim_scope": "m",
            "assumptions": ["a"],
            "evidence_standard": C.GRADE_EXACT_REPRODUCTION,
            "completion_standard": "c",
        }
        with Harness() as harness:
            first = harness.engine.open_objective(base)["commitment"]
            second = harness.engine.open_objective({**base, "claim_scope": "another scope"})["commitment"]
            self.assertNotEqual(first, second)


class RoutePortfolioTests(unittest.TestCase):
    def test_exactly_three_routes(self) -> None:
        validate_route_portfolio([route(0), route(1), route(2)])
        for count in (1, 2, 4):
            with self.assertRaises(ModelError) as caught:
                validate_route_portfolio([route(index) for index in range(count)])
            self.assertIn(C.ERR_ROUTE_PORTFOLIO, str(caught.exception))

    def test_duplicate_labels_are_refused(self) -> None:
        with self.assertRaises(ModelError):
            validate_route_portfolio(
                [route(0), route(1, label_code="route.candidate_0"), route(2)]
            )

    def test_route_needs_a_falsifier(self) -> None:
        with self.assertRaises(ModelError):
            validate_route_portfolio([route(0, falsifier=""), route(1), route(2)])

    def test_every_axis_must_be_declared(self) -> None:
        broken = route(0)
        broken["axes"] = {"learning_signal": "axis.x"}
        with self.assertRaises(ModelError):
            validate_route_portfolio([broken, route(1), route(2)])


if __name__ == "__main__":
    unittest.main()
