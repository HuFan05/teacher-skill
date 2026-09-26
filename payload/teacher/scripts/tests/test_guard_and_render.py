"""Input gate, guard ordering, renderer determinism and refusal shape."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from th import Renderer, make_draft  # noqa: E402
from th import constants as C  # noqa: E402
from th.backend import (  # noqa: E402
    BackendUnavailable,
    empty_patch,
    validate_draft,
    validate_guard_verdict,
    validate_patch,
)
from th.engine import Engine, gate_input  # noqa: E402
from th.guard import GuardDenied  # noqa: E402
from th.model import ModelError  # noqa: E402
from th.render import RenderError, render_bytes  # noqa: E402

from support import ALLOW, Harness, claim, deny  # noqa: E402


class InputGateTests(unittest.TestCase):
    def test_rejects_shape_not_meaning(self) -> None:
        self.assertFalse(gate_input("")["ok"])
        self.assertFalse(gate_input("   ")["ok"])
        self.assertFalse(gate_input(None)["ok"])
        self.assertFalse(gate_input("x" * 50000)["ok"])
        self.assertFalse(gate_input("bad\u0000null")["ok"])
        self.assertTrue(gate_input("一个正常的问题")["ok"])

    def test_a_rejected_input_never_reaches_the_backend(self) -> None:
        with Harness(drafts=[make_draft()], verdicts=[ALLOW]) as harness:
            result = harness.engine.turn("", actor="worker")
            self.assertEqual(result["status"], "refused")
            self.assertEqual(harness.backend.calls, [], "the backend must not be consulted")
            self.assertEqual(harness.store.head(C.HEAD_EXECUTION), "execution-genesis")


class DraftValidationTests(unittest.TestCase):
    def test_unknown_key_is_refused(self) -> None:
        draft = make_draft()
        draft["reasoning"] = "hidden chain"
        with self.assertRaises(ModelError):
            validate_draft(draft)

    def test_missing_key_is_refused(self) -> None:
        draft = make_draft()
        del draft["certainty"]
        with self.assertRaises(ModelError):
            validate_draft(draft)

    def test_quote_must_be_verbatim(self) -> None:
        with self.assertRaises(ModelError):
            validate_draft(make_draft(focus_quote="不是原文里的话"), supplied_text="原文里的话")
        validate_draft(make_draft(focus_quote="原文里的"), supplied_text="原文里的话")

    def test_quote_length_is_bounded(self) -> None:
        text = "x" * 5000
        with self.assertRaises(ModelError):
            validate_draft(make_draft(focus_quote=text), supplied_text=text)

    def test_unknown_anchor_is_refused_at_the_boundary(self) -> None:
        with self.assertRaises(ModelError):
            validate_draft(make_draft(focus_anchor_id="AN-nope"), known_anchor_ids={"AN-yes"})

    def test_enum_outside_the_vocabulary_is_refused(self) -> None:
        for key, value in (("decision", "maybe"), ("scope", "vibes"), ("certainty", "absolute")):
            with self.assertRaises(ModelError):
                validate_draft(make_draft(**{key: value}))

    def test_label_code_must_be_a_dotted_token_with_a_known_prefix(self) -> None:
        for bad in ("a sentence of prose", "unknown.prefix", "", "x" * 200, "中文标签"):
            with self.assertRaises(ModelError):
                validate_patch({**empty_patch(), "nodes": [{"kind": "step", "label_code": bad, "status": "open"}]})

    def test_patch_size_is_bounded(self) -> None:
        nodes = [{"kind": "step", "label_code": f"step.s{index}", "status": "open"} for index in range(50)]
        with self.assertRaises(ModelError):
            validate_patch({**empty_patch(), "nodes": nodes})

    def test_guard_verdict_shape(self) -> None:
        validate_guard_verdict({"allow": True, "risk_codes": []})
        validate_guard_verdict({"allow": False, "risk_codes": [C.RISK_SCHEMA]})
        with self.assertRaises(ModelError):
            validate_guard_verdict({"allow": True, "risk_codes": [C.RISK_SCHEMA]})
        with self.assertRaises(ModelError):
            validate_guard_verdict({"allow": False, "risk_codes": []})
        with self.assertRaises(ModelError):
            validate_guard_verdict({"allow": "yes", "risk_codes": []})


class GuardOrderingTests(unittest.TestCase):
    def test_a_model_guard_cannot_reopen_a_deterministic_denial(self) -> None:
        """The deterministic layer runs first and its denial is final.

        A repair attempt is made, and it fails the same way: the closed defect is
        not something a second opinion can reopen.
        """

        patch = empty_patch()
        patch["edges"] = [{"src": "step.ghost", "relation": "supports", "dst": "step.ghost"}]
        with Harness(
            drafts=[make_draft(graph_patch=patch), make_draft(graph_patch=patch)],
            verdicts=[ALLOW, ALLOW],
        ) as harness:
            result = harness.engine.turn("一个提交", actor="worker")
            self.assertEqual(result["status"], "failed")
            self.assertIn(C.RISK_UNKNOWN_ANCHOR, result["risk_codes"])
            self.assertEqual(result["repairs_used"], 1)
            # The model guard was never consulted on either attempt.
            self.assertNotIn("guard_turn", harness.backend.calls)

    def test_a_model_guard_can_veto_what_the_deterministic_layer_allows(self) -> None:
        with Harness(
            drafts=[make_draft(), make_draft()],
            verdicts=[deny(C.RISK_ASSESSMENT_INCONSISTENT), deny(C.RISK_ASSESSMENT_INCONSISTENT)],
        ) as harness:
            result = harness.engine.turn("一个提交", actor="worker")
            self.assertEqual(result["status"], "failed")
            self.assertIn(C.RISK_ASSESSMENT_INCONSISTENT, result["risk_codes"])
            self.assertEqual(result["repairs_used"], 1)

    def test_a_resolved_result_node_needs_an_accepted_claim(self) -> None:
        patch = empty_patch()
        patch["nodes"] = [{"kind": "result", "label_code": "result.one", "status": "resolved"}]
        with Harness(
            drafts=[make_draft(graph_patch=patch), make_draft(graph_patch=patch)],
            verdicts=[ALLOW, ALLOW],
        ) as harness:
            result = harness.engine.turn("一个提交", actor="worker")
            self.assertEqual(result["status"], "failed")
            self.assertIn(C.RISK_EVIDENCE_INSUFFICIENT, result["risk_codes"])

    def test_one_bounded_repair_is_allowed_and_it_can_succeed(self) -> None:
        """A fixable structural defect gets exactly one retry.

        The retry is told the closed risk code and nothing else, so it can fix
        shape but cannot learn what a reviewer concluded.
        """

        bad = make_draft(certainty="absolute")  # outside the closed vocabulary
        with Harness(drafts=[bad, make_draft()], verdicts=[ALLOW]) as harness:
            result = harness.engine.turn("一个提交", actor="worker")
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["repairs_used"], 1)
            repairs = [event for event in harness.store.events() if event["type"] == "turn_repair"]
            self.assertEqual(len(repairs), 1)
            # The repair receipt carries closed codes only, not the rejection text.
            self.assertEqual(repairs[0]["payload"]["attempt"], 2)
            self.assertTrue(repairs[0]["payload"]["risk_codes"])

    def test_the_repair_budget_is_exactly_one(self) -> None:
        bad = make_draft(certainty="absolute")
        with Harness(drafts=[bad, bad, make_draft()], verdicts=[ALLOW, ALLOW]) as harness:
            result = harness.engine.turn("一个提交", actor="worker")
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["repairs_used"], 1)
            # The third draft was never requested: the budget was spent.
            self.assertEqual(len(harness.backend._drafts), 1)

    def test_a_claim_guard_reports_each_defect_separately(self) -> None:
        with Harness() as harness:
            risks = harness.engine.guard.deterministic.check_claim(
                claim(scope={"datasets": []}, cannot_imply=[], strength=None)
            )
            self.assertIn(C.RISK_SCOPE_UNDECLARED, risks)
            self.assertIn(C.RISK_CANNOT_IMPLY_MISSING, risks)
            self.assertIn(C.RISK_UNTYPED_CLAIM, risks)

    def test_audit_trail_records_every_verdict_without_content(self) -> None:
        with Harness(drafts=[make_draft()], verdicts=[ALLOW]) as harness:
            harness.engine.turn("一个提交", actor="worker")
            trail = harness.engine.guard.audit_trail()
            self.assertTrue(trail)
            for entry in trail:
                self.assertIn("payload_sha256", entry)
                self.assertNotIn("text", entry)


class RenderDeterminismTests(unittest.TestCase):
    def test_same_input_yields_identical_bytes(self) -> None:
        renderer = Renderer()
        draft = make_draft(decision="needs_input", certainty="low", blocking_code="insufficient_context")
        first = renderer.render_turn(draft, supplied_text="上下文")
        second = renderer.render_turn(dict(reversed(list(draft.items()))), supplied_text="上下文")
        self.assertEqual(render_bytes(first), render_bytes(second))

    def test_renderer_refuses_a_quote_without_verification(self) -> None:
        renderer = Renderer()
        with self.assertRaises(RenderError):
            renderer.render_turn(make_draft(focus_quote="引文"), supplied_text="引文")

    def test_renderer_prints_codes_not_free_strings(self) -> None:
        renderer = Renderer()
        text = renderer.render_turn(
            make_draft(certainty="low", decision="blocked", blocking_code="out_of_scope"),
            supplied_text="",
        )
        self.assertIn("超出当前授权范围", text)

    def test_renderer_rejects_a_bad_count(self) -> None:
        renderer = Renderer()
        patch = empty_patch()
        patch["nodes"] = [{"kind": "step", "label_code": "step.a", "status": "open"}]
        patch["nodes"] = patch["nodes"] * 3
        text = renderer.render_turn(make_draft(graph_patch=patch), supplied_text="")
        self.assertIn("记录新增节点 3 个", text)

    def test_refusal_must_name_a_risk(self) -> None:
        with self.assertRaises(RenderError):
            Renderer().render_refusal([])

    def test_failure_text_is_a_fixed_string(self) -> None:
        renderer = Renderer()
        self.assertEqual(renderer.render_failure(), C.SAFE_FAILURE_RESPONSE)
        self.assertEqual(renderer.render_post_review_failure(), C.SAFE_POST_REVIEW_FAILURE)

    def test_completion_render_lists_obligations_from_codes(self) -> None:
        renderer = Renderer()
        text = renderer.render_completion(
            {
                "status": C.COMPLETION_NOT_COMPLETE,
                "issue_codes": [C.ISSUE_NO_VERIFIED_CHECKPOINT, C.ISSUE_DEPENDENCY_OPEN],
            }
        )
        self.assertIn("缺少已核验的检查点", text)
        self.assertIn("依赖链未闭合", text)

    def test_route_rendering_uses_axis_tokens(self) -> None:
        from support import routes

        text = Renderer().render_routes(routes())
        self.assertIn("route.candidate_0", text)
        self.assertIn("axis.mlp", text)


class UnavailableBackendTests(unittest.TestCase):
    def test_no_configured_model_fails_closed(self) -> None:
        from th import Store

        store = Store(":memory:")
        store.init()
        try:
            engine = Engine(store, backend=None)
            result = engine.turn("一个提交", actor="worker")
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["safe_visible"], C.SAFE_FAILURE_RESPONSE)
            self.assertEqual(store.head(C.HEAD_EXECUTION), "execution-genesis")
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
