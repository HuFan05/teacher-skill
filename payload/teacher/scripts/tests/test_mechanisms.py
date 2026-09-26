"""The mechanisms the operator drives.

Each has the refusal paths that make it worth having:

  layers       exploration stays complete; the reading layer adapts to budget
  coverage     countable obligations become a gate instead of a note
  maintenance  what can be computed propagates; what cannot requires a decision
  acceptance   a bounded retry loop with a declared ceiling and no easy fallback
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from th import constants as C  # noqa: E402
from th.acceptance import (  # noqa: E402
    GATE_ACTIVE,
    GATE_FALLBACK,
    GATE_PASSED,
    MAX_ROUNDS,
    AcceptanceError,
    AcceptanceGate,
    classify,
    deterministic_receipt,
    inventory,
    verifier_receipt,
)
from th.backend import make_draft  # noqa: E402
from th.coverage import (  # noqa: E402
    DISPOSITION_ACCEPTED,
    DISPOSITION_PENDING,
    CoverageError,
    check,
    decisions_template,
    make_plan,
    require_complete,
    summary_line,
    validate_plan,
)
from th.layers import (  # noqa: E402
    LAYER_COMPACT,
    LAYER_MINIMAL,
    LAYER_NORMAL,
    LAYERS,
    PROTECTED_FIELDS,
    LayerError,
    compression_gain,
    estimate_tokens,
    select_layer,
    validate_cognition,
)
from th.maintenance import (  # noqa: E402
    MaintenanceError,
    apply_decisions,
    coalesce_events,
    dependency_drift,
    mechanical_impact,
)
from th.store import digest

from support import ALLOW, Harness, claim, route  # noqa: E402


def cognition(**overrides) -> dict:
    base = {
        "objective": "判断该目标是否在声明范围内改善指标",
        "acceptance_standard": "依赖链闭合且结果可复现",
        "method_sources": ["论文 A 第 3 节", "官方实现"],
        "verified_backbone": ["数据划分已固定", "基线已复现"],
        "bottleneck_causality": "瓶颈在于梯度方差而非容量",
        "route_rationale": "选替代损失是因为它对标签噪声不敏感",
        "evidence_boundary": "不能推出在其他数据集或规模上同样成立",
        "uninstantiated_objects": ["尚未接入的第二数据集"],
        "retrieval_triggers": ["方差", "替代损失"],
        "reset_conditions": "若数据划分变化则重置该路线",
    }
    base.update(overrides)
    return base


# ==========================================================================
# Layers
# ==========================================================================
class LayerTests(unittest.TestCase):
    def test_layers_get_smaller(self) -> None:
        with_extra = cognition(
            repetition="重复说明" * 40,
            history_narrative="历史叙事" * 40,
            expandable_detail="可展开细节" * 40,
        )
        sizes = compression_gain(with_extra)["sizes"]
        self.assertGreater(sizes[LAYER_NORMAL], sizes[LAYER_COMPACT])
        self.assertGreater(sizes[LAYER_COMPACT], sizes[LAYER_MINIMAL])

    def test_every_protected_field_survives_every_layer(self) -> None:
        """This is the rule that keeps compression honest."""

        extra = {field: "这一条可以删" for field in ("repetition", "history_narrative")}
        for layer in LAYERS:
            result = select_layer({**cognition(**extra)}, preferred=layer)
            body = result["text"]
            for field in PROTECTED_FIELDS:
                value = cognition()[field]
                needle = value[0] if isinstance(value, list) else value
                self.assertIn(str(needle), body, f"{layer} dropped {field}")
            self.assertEqual(result["receipt"]["protected_fields_present"], list(PROTECTED_FIELDS))

    def test_compression_drops_only_droppable_fields(self) -> None:
        """Three layers, three behaviours: keep, truncate, drop.

        Nothing protected is ever touched, and the ordering is strict so the
        selection loop can rely on it.
        """

        extra = {
            "repetition": "重复说明" * 60,
            "history_narrative": "历史叙事" * 60,
            "expandable_detail": "可展开细节" * 60,
        }
        normal = select_layer(cognition(**extra), preferred=LAYER_NORMAL)
        compact = select_layer(cognition(**extra), preferred=LAYER_COMPACT)
        minimal = select_layer(cognition(**extra), preferred=LAYER_MINIMAL)

        self.assertIn("历史叙事" * 60, normal["text"])
        # compact drops narrative and expandable detail, truncates repetition
        self.assertNotIn("历史叙事" * 60, compact["text"])
        self.assertNotIn("可展开细节" * 60, compact["text"])
        self.assertIn("重复说明", compact["text"])
        self.assertIn("…", compact["text"])
        # minimal drops every droppable field
        self.assertNotIn("重复说明", minimal["text"])

        counts = [
            estimate_tokens(normal["text"]),
            estimate_tokens(compact["text"]),
            estimate_tokens(minimal["text"]),
        ]
        self.assertGreater(counts[0], counts[1])
        self.assertGreater(counts[1], counts[2])

    def test_every_protected_field_survives_every_layer_even_when_droppable_fields_exist(self) -> None:
        extra = {"repetition": "可删" * 100, "history_narrative": "可删" * 100}
        for layer in LAYERS:
            text = select_layer(cognition(**extra), preferred=layer)["text"]
            for field in PROTECTED_FIELDS:
                value = cognition()[field]
                needle = value[0] if isinstance(value, list) else value
                self.assertIn(str(needle), text, f"{layer} dropped {field}")

    def test_a_missing_protected_field_is_refused(self) -> None:
        broken = cognition()
        del broken["evidence_boundary"]
        with self.assertRaises(LayerError) as caught:
            validate_cognition(broken)
        self.assertIn("evidence_boundary", str(caught.exception))

    def test_an_empty_protected_field_is_refused(self) -> None:
        with self.assertRaises(LayerError):
            validate_cognition(cognition(reset_conditions=[]))

    def test_undeclared_field_is_refused(self) -> None:
        with self.assertRaises(LayerError):
            validate_cognition(cognition(vibes="good"))

    def test_overflow_does_not_stop_the_work(self) -> None:
        """The rule that keeps bookkeeping from costing more than the work."""

        huge = cognition(objective="目标" * 40000)
        result = select_layer(huge, target_tokens=100, ceiling_tokens=200)
        self.assertTrue(result["receipt"]["overflow"])
        self.assertEqual(result["layer"], LAYER_MINIMAL)
        self.assertTrue(result["receipt"]["continues"])
        self.assertFalse(result["receipt"]["may_pause_work"])
        self.assertEqual(result["receipt"]["token_count"] > 200, True)
        self.assertTrue(result["text"])

    def test_the_receipt_records_the_estimator(self) -> None:
        receipt = select_layer(cognition())["receipt"]
        self.assertEqual(receipt["schema"], "th-reading-receipt/v1")
        self.assertIn("estimator", receipt)
        self.assertEqual(receipt["text_sha256"], digest(select_layer(cognition())["text"]))

    def test_the_densest_fitting_layer_is_chosen(self) -> None:
        extra = {"repetition": "重复说明" * 60, "history_narrative": "历史叙事" * 60}
        sizes = compression_gain(cognition(**extra))["sizes"]
        # A ceiling just under `normal` must fall back to `compact`, not to the
        # smallest layer: the point is to use as much as fits.
        result = select_layer(cognition(**extra), ceiling_tokens=sizes[LAYER_NORMAL] - 1)
        self.assertEqual(result["layer"], LAYER_COMPACT)
        result = select_layer(cognition(**extra), ceiling_tokens=sizes[LAYER_COMPACT] - 1)
        self.assertEqual(result["layer"], LAYER_MINIMAL)
        result = select_layer(cognition(**extra), ceiling_tokens=sizes[LAYER_NORMAL] + 1)
        self.assertEqual(result["layer"], LAYER_NORMAL)

    def test_estimate_tokens_is_deterministic(self) -> None:
        self.assertGreater(estimate_tokens("abc"), 0)
        self.assertEqual(estimate_tokens("abc"), estimate_tokens("abc"))


# ==========================================================================
# Coverage
# ==========================================================================
class CoverageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = Harness()
        self.store = self.harness.store

    def tearDown(self) -> None:
        self.harness.close()

    def test_the_plan_is_deterministic(self) -> None:
        self.assertEqual(make_plan(self.store), make_plan(self.store))

    def test_a_trimmed_plan_is_refused(self) -> None:
        # Two unreviewed claims give the plan two members to trim.
        self.harness.engine.submit_claim(claim(statement="第一条结论"))
        self.harness.engine.submit_claim(claim(statement="第二条结论"))
        plan = make_plan(self.store)
        assert len(plan["members"]) >= 2
        plan["members"] = plan["members"][:1]
        with self.assertRaises(CoverageError) as caught:
            validate_plan(self.store, plan)
        self.assertEqual(caught.exception.code, "plan_changed")

    def test_decisions_must_bind_the_plan(self) -> None:
        plan = make_plan(self.store)
        decisions = decisions_template(plan)
        decisions["plan_sha256"] = "0" * 64
        with self.assertRaises(CoverageError) as caught:
            check(self.store, plan, decisions)
        self.assertEqual(caught.exception.code, "decision_binding")

    def test_every_member_starts_pending(self) -> None:
        self.harness.engine.submit_claim(claim(statement="第一条"))
        plan = make_plan(self.store)
        self.assertTrue(plan["members"])
        coverage = check(self.store, plan, decisions_template(plan))
        self.assertFalse(coverage["complete"])
        self.assertEqual(coverage["member_coverage"]["decided"], 0)
        self.assertEqual(set(coverage["member_coverage"]["pending"]), {m["id"] for m in plan["members"]})

    def test_a_decided_member_must_declare_an_exact_read(self) -> None:
        record_id = self.harness.engine.submit_claim(claim())["record_id"]
        plan = make_plan(self.store)
        decisions = decisions_template(plan)
        for item in decisions["members"]:
            if item["id"] == record_id:
                item["disposition"] = DISPOSITION_ACCEPTED
                item["read_revisions"] = []
        with self.assertRaises(CoverageError) as caught:
            check(self.store, plan, decisions)
        self.assertEqual(caught.exception.code, "exact_read_required")
        self.assertIn("cannot authenticate", str(caught.exception))

    def test_full_coverage_requires_every_member_and_pair(self) -> None:
        record_id = self.harness.engine.submit_claim(claim())["record_id"]
        revision = self.store.record_revision(record_id)
        plan = make_plan(self.store)
        decisions = decisions_template(plan)
        for item in decisions["members"]:
            item["disposition"] = DISPOSITION_ACCEPTED
            item["read_revisions"] = [revision]
        for item in decisions["pairs"]:
            item["disposition"] = DISPOSITION_ACCEPTED
        coverage = check(self.store, plan, decisions)
        self.assertTrue(coverage["complete"])
        require_complete(coverage)
        self.assertIn("members", summary_line(coverage))

    def test_the_omission_count_and_the_honesty_flags_are_always_present(self) -> None:
        plan = make_plan(self.store)
        coverage = check(self.store, plan, decisions_template(plan))
        self.assertIsInstance(coverage["omitted_candidates"], int)
        # The two statements that keep the numbers honest.
        self.assertTrue(coverage["software_cannot_authenticate_reading"])
        self.assertFalse(coverage["semantic_completeness_proven"])

    def test_incomplete_coverage_blocks_completion(self) -> None:
        window = self.harness.open_window()
        self.harness.engine.checkpoint(window["attempts"][0], body={}, verified=True)
        record_id = self.harness.accepted_claim()
        plan = make_plan(self.store)
        coverage = check(self.store, plan, decisions_template(plan))
        assessment = self.harness.engine.assess_completion(record_id=record_id, coverage=coverage)["assessment"]
        self.assertEqual(assessment["status"], C.COMPLETION_NOT_COMPLETE)
        self.assertIn(C.ISSUE_COVERAGE_INCOMPLETE, assessment["issue_codes"])
        self.assertIn("coverage", assessment)

    def test_an_unknown_disposition_is_refused(self) -> None:
        plan = make_plan(self.store)
        decisions = decisions_template(plan)
        if not decisions["members"]:
            self.skipTest("no members in this plan")
        decisions["members"][0]["disposition"] = "looks_fine"
        decisions["members"][0]["read_revisions"] = ["x"]
        with self.assertRaises(CoverageError) as caught:
            check(self.store, plan, decisions)
        self.assertEqual(caught.exception.code, "disposition_unknown")


# ==========================================================================
# Maintenance
# ==========================================================================
class MaintenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = Harness()
        self.store = self.harness.store

    def tearDown(self) -> None:
        self.harness.close()

    def test_events_coalesce_by_asset(self) -> None:
        record_id = self.harness.engine.submit_claim(claim())["record_id"]
        self.harness.engine.review_claim(record_id, decision=C.REVIEW_ACCEPTED)
        self.harness.engine.review_claim(record_id, decision=C.REVIEW_ACCEPTED)
        report = coalesce_events(self.store)
        self.assertGreaterEqual(report["events_seen"], 3)
        self.assertTrue(report["coalesced"])
        self.assertIn(record_id, report["affected"])
        self.assertGreaterEqual(report["affected"][record_id]["count"], 2)

    def _linked_claims(self) -> tuple[str, str]:
        premise = self.harness.engine.submit_claim(claim(statement="前提成立"))["record_id"]
        conclusion = self.harness.engine.submit_claim(claim(statement="由此推出结论"))["record_id"]
        self.store.transact(
            operation_id="link",
            head=C.HEAD_EXECUTION,
            expected=self.store.head(C.HEAD_EXECUTION),
            request={"link": 1},
            mutate=lambda connection: {
                "action": "linked",
                "notes": [
                    self.store.add_relation(
                        connection, src=conclusion, relation=C.REL_PREMISE, dst=premise, reason="conclusion rests on premise"
                    )
                ],
            },
        )
        return premise, conclusion

    def test_a_dependency_change_demotes_the_conclusion_without_anyone_marking_it(self) -> None:
        """The automatic half of maintenance. This is the load-bearing one."""

        premise, conclusion = self._linked_claims()
        self.harness.engine.review_claim(conclusion, decision=C.REVIEW_ACCEPTED)
        self.assertEqual(self.harness.engine.record_effect(conclusion), C.EFFECT_CURRENT)

        # The premise is revised: a new record revision for the same id.
        revised = dict(self.store.record(premise))
        revised["body"] = {**revised["body"], "amended": True}
        self.store.transact(
            operation_id="revise-premise",
            head=C.HEAD_EXECUTION,
            expected=self.store.head(C.HEAD_EXECUTION),
            request={"revise": premise},
            mutate=lambda connection: {
                "action": "premise_revised",
                "records": [self.store.put_record(connection, revised)],
            },
        )

        drift = dependency_drift(self.store, conclusion)
        self.assertTrue(drift["drifted"])
        self.assertIn(premise, drift["moved"])
        # Nobody marked it. The effect is computed.
        self.assertEqual(self.harness.engine.record_effect(conclusion), C.EFFECT_NEEDS_REVIEW)

    def test_mechanical_impact_reports_without_writing(self) -> None:
        premise, conclusion = self._linked_claims()
        self.harness.engine.review_claim(conclusion, decision=C.REVIEW_ACCEPTED)
        before = self.store.head(C.HEAD_EXECUTION)
        report = mechanical_impact(self.store)
        self.assertIn("counts", report)
        self.assertEqual(report["semantic_action"], "pending_owner_review")
        self.assertEqual(self.store.head(C.HEAD_EXECUTION), before, "detection writes nothing")

    def test_a_decision_must_state_a_reason(self) -> None:
        record_id = self.harness.engine.submit_claim(claim())["record_id"]
        with self.assertRaises(MaintenanceError) as caught:
            apply_decisions(
                self.store,
                [{"asset_id": record_id, "action": "retire", "reason": ""}],
                operation_id="m1",
            )
        self.assertEqual(caught.exception.code, "reason_required")

    def test_an_unknown_action_is_refused(self) -> None:
        record_id = self.harness.engine.submit_claim(claim())["record_id"]
        with self.assertRaises(MaintenanceError):
            apply_decisions(
                self.store,
                [{"asset_id": record_id, "action": "delete_everything", "reason": "why not"}],
                operation_id="m2",
            )

    def test_an_explicit_decision_changes_lifecycle_but_not_effect(self) -> None:
        """An operator can retire a record; an operator cannot promote one."""

        record_id = self.harness.engine.submit_claim(claim())["record_id"]
        self.assertEqual(self.harness.engine.record_effect(record_id), C.EFFECT_HISTORICAL)
        result = apply_decisions(
            self.store,
            [{"asset_id": record_id, "action": "mark_needs_review", "reason": "上游数据被替换"}],
            operation_id="m3",
        )
        self.assertEqual(result["authority"], "explicit_decision")
        self.assertEqual(self.store.record(record_id)["body"]["lifecycle"], "needs_review")
        # Effect is still derived from reviews, so it did not become current.
        self.assertEqual(self.harness.engine.record_effect(record_id), C.EFFECT_HISTORICAL)


# ==========================================================================
# Acceptance
# ==========================================================================
class ClassificationTests(unittest.TestCase):
    def test_exclusions_override_thresholds(self) -> None:
        heavy = {"targets": 10, "prose_targets": 9, "changed_non_whitespace_chars": 9000,
                 "non_whitespace_scope_chars": 10000, "mechanical_only": True}
        result = classify(heavy)
        self.assertFalse(result["applies"])
        self.assertIn("excluded", result["reason"])

    def test_thresholds_trigger(self) -> None:
        self.assertTrue(classify({"targets": 4, "prose_targets": 3})["applies"])
        self.assertTrue(
            classify({"non_whitespace_scope_chars": 7000, "changed_non_whitespace_chars": 3000})["applies"]
        )
        self.assertTrue(classify({"targets": 1}, operator_requested=True)["applies"])

    def test_routine_work_stays_on_the_ordinary_path(self) -> None:
        result = classify({"targets": 1, "prose_targets": 1, "changed_non_whitespace_chars": 50})
        self.assertFalse(result["applies"])
        self.assertEqual(result["path"], "ordinary")


class AcceptanceGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = Harness()
        self.gate = AcceptanceGate(self.harness.store)
        self._tmp = tempfile.TemporaryDirectory()
        self.candidate = Path(self._tmp.name) / "candidate"
        self.candidate.mkdir()
        for index in range(3):
            (self.candidate / f"n{index}.md").write_text(f"# n{index}\n第一版内容\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.harness.close()
        self._tmp.cleanup()

    def _receipts(self, gate_id: str, round_number: int, snapshot: dict, verdict: str, independent: bool = True):
        checks = {
            name: {"status": "PASS", "evidence_sha256": "a" * 64, "reason": ""}
            for name in (
                "utf8_and_path_containment", "schema_and_structure", "code_and_result_consistency",
                "references_and_links", "resource_references", "task_specific_lint",
                "diff_and_sentinels",
            )
        }
        deterministic = deterministic_receipt(
            round_number=round_number,
            inventory_sha256=snapshot["aggregate_sha256"],
            checks=checks,
        )
        verifier = verifier_receipt(
            round_number=round_number,
            inventory_sha256=snapshot["aggregate_sha256"],
            verdict=verdict,
            independent=independent,
            findings=(
                [{"relative_path": "n0.md", "location": "L1", "category": "meaning",
                  "severity": "blocking", "message": "含义变了"}]
                if verdict == "FAIL" else []
            ),
        )
        return deterministic, verifier

    def test_inventory_never_carries_bodies(self) -> None:
        snapshot = inventory(self.candidate)
        self.assertEqual(snapshot["target_count"], 3)
        for target in snapshot["targets"]:
            self.assertNotIn("content", target)
            self.assertNotIn("text", target)
        self.assertIn("aggregate_sha256", snapshot)

    def test_a_symlink_pointing_outside_the_candidate_is_refused(self) -> None:
        outside = Path(self._tmp.name) / "outside.md"
        outside.write_text("# outside\n不属于候选\n", encoding="utf-8")
        link = self.candidate / "linked.md"
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("symlinks are not available here")
        with self.assertRaises(AcceptanceError) as caught:
            inventory(self.candidate)
        self.assertEqual(caught.exception.code, "path_not_contained")

    def test_an_invalid_utf8_candidate_is_refused_before_review(self) -> None:
        (self.candidate / "bad.md").write_bytes(b"\xff\xfe\x00\x00")
        with self.assertRaises(AcceptanceError) as caught:
            inventory(self.candidate)
        self.assertEqual(caught.exception.code, "utf8_invalid")

    def test_a_pass_makes_the_candidate_write_eligible(self) -> None:
        begun = self.gate.begin(self.candidate)
        gate_id = begun["gate"]["gate_id"]
        snapshot = self.gate.current_inventory(gate_id)
        deterministic, verifier = self._receipts(gate_id, 1, snapshot, "PASS")
        result = self.gate.advance(gate_id, round_number=1, deterministic=deterministic, verifier=verifier)
        self.assertTrue(result["write_eligible"])
        self.assertTrue(result["approval"])
        self.assertEqual(result["gate"]["state"], GATE_PASSED)

    def test_a_mechanical_failure_does_not_consume_a_round(self) -> None:
        """The cheapest gate runs first, and its failure is not a review round."""

        begun = self.gate.begin(self.candidate)
        gate_id = begun["gate"]["gate_id"]
        snapshot = self.gate.current_inventory(gate_id)
        deterministic, verifier = self._receipts(gate_id, 1, snapshot, "PASS")
        deterministic["checks"]["utf8_and_path_containment"]["status"] = "FAIL"
        result = self.gate.advance(gate_id, round_number=1, deterministic=deterministic, verifier=verifier)
        self.assertFalse(result["advanced"])
        self.assertEqual(result["reason"], "mechanical_candidate")
        self.assertFalse(result["round_consumed"])
        self.assertEqual(self.gate.round_state(gate_id)["round"], 1)

    def test_unusable_verification_does_not_consume_a_round(self) -> None:
        """An unavailable verifier must not burn the ceiling.

        Otherwise an infrastructure problem would turn into a completion decision.
        """

        begun = self.gate.begin(self.candidate)
        gate_id = begun["gate"]["gate_id"]
        snapshot = self.gate.current_inventory(gate_id)
        deterministic, _ = self._receipts(gate_id, 1, snapshot, "PASS")

        result = self.gate.advance(gate_id, round_number=1, deterministic=deterministic, verifier=None)
        self.assertFalse(result["advanced"])
        self.assertFalse(result["round_consumed"])
        self.assertEqual(result["reason"], "verification_unusable")
        self.assertEqual(self.gate.round_state(gate_id)["round"], 1)
        self.assertFalse(self.gate.round_state(gate_id)["write_eligible"])

        non_independent = verifier_receipt(
            round_number=1, inventory_sha256=snapshot["aggregate_sha256"],
            verdict="PASS", independent=False,
        )
        result = self.gate.advance(
            gate_id, round_number=1, deterministic=deterministic, verifier=non_independent
        )
        self.assertFalse(result["advanced"])
        self.assertEqual(result["reason"], "verifier_not_independent")
        self.assertEqual(self.gate.round_state(gate_id)["round"], 1)

    def test_fail_then_revise_then_pass(self) -> None:
        begun = self.gate.begin(self.candidate)
        gate_id = begun["gate"]["gate_id"]
        snapshot = self.gate.current_inventory(gate_id)
        deterministic, verifier = self._receipts(gate_id, 1, snapshot, "FAIL")
        first = self.gate.advance(gate_id, round_number=1, deterministic=deterministic, verifier=verifier)
        self.assertEqual(first["next_round"], 2)
        self.assertFalse(first["write_eligible"])
        self.assertEqual(first["gate"]["state"], GATE_ACTIVE)

        # A new round requires changed bytes.
        with self.assertRaises(AcceptanceError) as caught:
            self.gate.assert_bytes_changed(gate_id, first["gate"]["inventory_sha256"])
        self.assertEqual(caught.exception.code, "new_round_requires_changed_bytes")

        (self.candidate / "n0.md").write_text("# n0\n修订后的内容\n", encoding="utf-8")
        self.gate.assert_bytes_changed(gate_id, first["gate"]["inventory_sha256"])

        snapshot2 = self.gate.current_inventory(gate_id)
        deterministic2, verifier2 = self._receipts(gate_id, 2, snapshot2, "PASS")
        second = self.gate.advance(gate_id, round_number=2, deterministic=deterministic2, verifier=verifier2)
        self.assertTrue(second["write_eligible"])
        self.assertTrue(second["approval"])

    def test_the_third_failure_is_a_fallback_and_is_labelled_as_one(self) -> None:
        begun = self.gate.begin(self.candidate)
        gate_id = begun["gate"]["gate_id"]
        for round_number in range(1, MAX_ROUNDS + 1):
            snapshot = self.gate.current_inventory(gate_id)
            deterministic, verifier = self._receipts(gate_id, round_number, snapshot, "FAIL")
            result = self.gate.advance(
                gate_id, round_number=round_number, deterministic=deterministic, verifier=verifier
            )
            if round_number < MAX_ROUNDS:
                (self.candidate / "n0.md").write_text(f"# n0\n第{round_number + 1}版\n", encoding="utf-8")
        self.assertEqual(result["gate"]["state"], GATE_FALLBACK)
        self.assertTrue(result["write_eligible"])
        # The distinction that matters.
        self.assertFalse(result["approval"])
        self.assertIn("fallback", result["must_report_as"])
        self.assertTrue(result["unresolved_findings"])

    def test_a_write_eligible_gate_cannot_be_reused(self) -> None:
        begun = self.gate.begin(self.candidate)
        gate_id = begun["gate"]["gate_id"]
        snapshot = self.gate.current_inventory(gate_id)
        deterministic, verifier = self._receipts(gate_id, 1, snapshot, "PASS")
        self.gate.advance(gate_id, round_number=1, deterministic=deterministic, verifier=verifier)
        with self.assertRaises(AcceptanceError) as caught:
            self.gate.advance(gate_id, round_number=1, deterministic=deterministic, verifier=verifier)
        self.assertEqual(caught.exception.code, "already_write_eligible")

    def test_a_receipt_bound_to_another_candidate_is_refused(self) -> None:
        begun = self.gate.begin(self.candidate)
        gate_id = begun["gate"]["gate_id"]
        snapshot = self.gate.current_inventory(gate_id)
        deterministic, verifier = self._receipts(gate_id, 1, snapshot, "PASS")
        (self.candidate / "n1.md").write_text("# n1\n换了内容\n", encoding="utf-8")
        with self.assertRaises(AcceptanceError) as caught:
            self.gate.advance(gate_id, round_number=1, deterministic=deterministic, verifier=verifier)
        self.assertEqual(caught.exception.code, "candidate_inventory_stale")

    def test_a_pass_carrying_a_blocking_finding_is_inconsistent(self) -> None:
        with self.assertRaises(AcceptanceError):
            verifier_receipt(
                round_number=1,
                inventory_sha256="a" * 64,
                verdict="PASS",
                independent=True,
                findings=[{"relative_path": "n0.md", "location": "L1", "category": "x",
                           "severity": "blocking", "message": "blocked"}],
            )

    def test_not_applicable_needs_a_reason(self) -> None:
        from th.acceptance import CHECK_NAMES

        checks = {
            name: {"status": "PASS", "evidence_sha256": "a" * 64, "reason": ""}
            for name in CHECK_NAMES
        }
        checks["code_and_result_consistency"] = {"status": "NOT_APPLICABLE", "evidence_sha256": "a" * 64, "reason": ""}
        with self.assertRaises(AcceptanceError):
            deterministic_receipt(round_number=1, inventory_sha256="a" * 64, checks=checks)


# ==========================================================================
# Escalation
# ==========================================================================
class EscalationTests(unittest.TestCase):
    def test_asking_for_input_records_how_much_investigation_preceded_it(self) -> None:
        with Harness(drafts=[make_draft(decision="needs_input", blocking_code="insufficient_context")], verdicts=[ALLOW]) as harness:
            result = harness.engine.turn("我没有给出足够的上下文", actor="worker")
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["escalation"]["blocking_code"], "insufficient_context")
            self.assertIn(result["escalation"]["assessment"], {"investigated", "uninvestigated"})
            receipts = [event for event in harness.store.events() if event["type"] == "escalation_recorded"]
            self.assertEqual(len(receipts), 1)

    def test_a_normal_turn_records_no_escalation(self) -> None:
        with Harness(drafts=[make_draft()], verdicts=[ALLOW]) as harness:
            result = harness.engine.turn("一个提交", actor="worker")
            self.assertIsNone(result["escalation"])


if __name__ == "__main__":
    unittest.main()
