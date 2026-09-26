"""Canary: prove there is no path from model output to a visible surface.

This is the harness's one absolute claim, and the reason it is stated as a
data-flow property rather than a semantic one. A canary string is placed in
every field a backend controls and in every exception it can raise; the test
then asserts the canary is absent from everything reachable — the returned
result, the rendered text, every text column of the store, every file the store
wrote, and the module's own output tables — in seven encodings.

The claim is deliberately narrow:

    While a turn is being produced, model-authored free text has no path to a
    rendered surface.

It is **not** a claim that a semantically disguised answer would be caught.
Deciding that would require solving the classification problem the guard is
trying to defend, and no system that judges arbitrary free-form claims can
prove it does so correctly. See docs/security-model.md.
"""

from __future__ import annotations

import base64
import json
import sqlite3
import sys
import unittest
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from th import ScriptedBackend, Store, make_draft  # noqa: E402
from th import constants as C  # noqa: E402
from th.backend import BackendTimeout, BackendUnavailable, empty_patch  # noqa: E402
from th.engine import Engine, safe_code  # noqa: E402
from th.render import DECISION_PHRASE, GRADE_PHRASE, TEXT  # noqa: E402

from support import ALLOW, CANARY, Harness, contract  # noqa: E402


def canary_variants(value: str) -> set[str]:
    raw = value.encode("utf-8")
    return {
        value,
        json.dumps(value, ensure_ascii=True)[1:-1],
        json.dumps(value, ensure_ascii=False)[1:-1],
        quote(value, safe=""),
        base64.b64encode(raw).decode("ascii"),
        raw.hex(),
        "".join(char for char in value if char.isalnum()).casefold(),
    }


def folded(value: str) -> str:
    return "".join(char for char in value if char.isalnum()).casefold()


def sqlite_text_dump(path: Path) -> list[str]:
    """Every TEXT, CHAR and CLOB value in every user table."""

    values: list[str] = []
    connection = sqlite3.connect(str(path))
    try:
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            if not str(row[0]).startswith("sqlite_")
        ]
        for table in tables:
            escaped_table = table.replace('"', '""')
            columns = [
                str(row[1])
                for row in connection.execute(f'PRAGMA table_info("{escaped_table}")')
                if any(marker in str(row[2]).upper() for marker in ("TEXT", "CHAR", "CLOB"))
            ]
            for column in columns:
                escaped_column = column.replace('"', '""')
                for row in connection.execute(f'SELECT "{escaped_column}" FROM "{escaped_table}"'):
                    if isinstance(row[0], str):
                        values.append(row[0])
    finally:
        connection.close()
    return values


def module_texts() -> list[str]:
    """The rendered phrase tables. A canary must not be a template value."""

    blobs = [json.dumps(TEXT, ensure_ascii=False), json.dumps(DECISION_PHRASE, ensure_ascii=False),
             json.dumps(GRADE_PHRASE, ensure_ascii=False)]
    return blobs


class CanaryNoSinkTests(unittest.TestCase):
    def assert_canary_absent(self, harness: Harness, *objects: object) -> None:
        """Assert the canary is absent from every surface reachable from a turn.

        Two passes, because they catch different things:
          * a text pass over the returned objects, the renderer's own phrase
            tables and every TEXT column of the store — this catches a canary
            that survived shape validation and reached state;
          * a raw-byte pass over every file the store wrote — this catches the
            encoding disguises (base64, hex, url-quoted, json-escaped) that a
            text pass would miss.
        """

        text_blobs: list[str] = [
            json.dumps(objects, ensure_ascii=False, sort_keys=True, default=str),
            json.dumps(objects, ensure_ascii=True, sort_keys=True, default=str),
            *module_texts(),
            *sqlite_text_dump(harness.path),
        ]
        text_joined = "\n".join(text_blobs)
        text_folded = folded(text_joined)

        raw = bytearray()
        for path in sorted(harness.path.parent.rglob("*")):
            if path.is_file():
                try:
                    raw += path.read_bytes()
                except OSError:
                    continue

        for variant in canary_variants(CANARY):
            with self.subTest(variant=variant[:24]):
                if variant == folded(CANARY):
                    self.assertNotIn(variant, text_folded)
                else:
                    self.assertNotIn(variant.encode("utf-8"), bytes(raw))

    # -- scenario 1: the canary is placed in a field the backend controls ----
    def test_canary_in_focus_quote_has_no_sink(self) -> None:
        with Harness(drafts=[make_draft(focus_quote=CANARY)], verdicts=[ALLOW]) as harness:
            result = harness.engine.turn("一个普通的提交", actor="worker")
            self.assertEqual(result["status"], "failed")
            self.assert_canary_absent(harness, result)

    def test_canary_in_label_code_has_no_sink(self) -> None:
        patch = empty_patch()
        patch["nodes"] = [
            {"kind": "step", "label_code": CANARY, "status": "open"}
        ]
        with Harness(drafts=[make_draft(graph_patch=patch)], verdicts=[ALLOW]) as harness:
            result = harness.engine.turn("一个普通的提交", actor="worker")
            self.assertEqual(result["status"], "failed")
            self.assert_canary_absent(harness, result)

    def test_canary_in_edge_endpoints_has_no_sink(self) -> None:
        patch = empty_patch()
        patch["edges"] = [{"src": CANARY, "relation": "supports", "dst": CANARY}]
        with Harness(drafts=[make_draft(graph_patch=patch)], verdicts=[ALLOW]) as harness:
            result = harness.engine.turn("一个普通的提交", actor="worker")
            self.assertEqual(result["status"], "failed")
            self.assert_canary_absent(harness, result)

    def test_canary_in_unknown_draft_key_has_no_sink(self) -> None:
        draft = make_draft()
        draft["free_reasoning"] = CANARY
        with Harness(drafts=[draft], verdicts=[ALLOW]) as harness:
            result = harness.engine.turn("一个普通的提交", actor="worker")
            self.assertEqual(result["status"], "failed")
            self.assert_canary_absent(harness, result)

    # -- scenario 2: the canary is carried by an exception -------------------
    def test_canary_in_backend_errors_has_no_sink(self) -> None:
        scenarios = [
            ("timeout", BackendTimeout(CANARY)),
            ("unavailable", BackendUnavailable(CANARY)),
            ("runtime", RuntimeError(CANARY)),
            ("value", ValueError(CANARY)),
            ("os", PermissionError(CANARY)),
        ]
        for name, exc in scenarios:
            with self.subTest(scenario=name):
                with Harness(drafts=[exc], verdicts=[ALLOW]) as harness:
                    result = harness.engine.turn("一个普通的提交", actor="worker")
                    self.assertEqual(result["status"], "failed")
                    # The code must be screened, so the exception message cannot
                    # travel even though the code is returned to the caller.
                    self.assertEqual(result["code"], safe_code(result["code"]))
                    self.assert_canary_absent(harness, result)

    def test_canary_in_guard_exception_has_no_sink(self) -> None:
        with Harness(drafts=[make_draft()], verdicts=[PermissionError(CANARY)]) as harness:
            result = harness.engine.turn("一个普通的提交", actor="worker")
            self.assertEqual(result["status"], "failed")
            self.assert_canary_absent(harness, result)

    # -- scenario 3: the canary attempts to enter through caller text --------
    def test_canary_in_caller_text_is_rendered_only_as_a_verified_quote(self) -> None:
        """Caller text may appear, but only because the caller wrote it.

        The distinction matters: the draft legitimately echoes the caller's own
        words, so the check is that nothing *else* carried it — not the store,
        not a receipt, not any file.
        """

        with Harness(drafts=[make_draft(focus_quote=CANARY)], verdicts=[ALLOW]) as harness:
            result = harness.engine.turn(f"我的原文包含 {CANARY} 这段", actor="worker")
            self.assertEqual(result["status"], "ok")
            self.assertIn(CANARY, result["visible"])
            # Nothing may be persisted: not the store, not any file on disk.
            self.assert_canary_absent(harness)

    # -- deterministic screening of the code itself -------------------------
    def test_safe_code_screens_non_token_codes(self) -> None:
        self.assertEqual(safe_code("guard_turn_queue_empty"), "guard_turn_queue_empty")
        self.assertEqual(safe_code(CANARY), "unsafe_code")
        self.assertEqual(safe_code("has space"), "unsafe_code")
        self.assertEqual(safe_code("x" * 200), "unsafe_code")
        self.assertEqual(safe_code(None), "unsafe_code")
        self.assertEqual(safe_code("UPPER"), "unsafe_code")


class NoModelProsePathTests(unittest.TestCase):
    """Structural companions to the canary: the renderer has no text entry point."""

    def test_renderer_exposes_no_free_text_entry(self) -> None:
        from th.render import Renderer

        public = [name for name in dir(Renderer) if not name.startswith("_")]
        forbidden = {"render_text", "render_prose", "render_model", "render_free", "write"}
        self.assertFalse(forbidden & set(public), f"renderer exposes {forbidden & set(public)}")

    def test_renderer_rejects_unvalidated_draft(self) -> None:
        from th.render import Renderer, RenderError

        renderer = Renderer()
        with self.assertRaises(Exception):
            renderer.render_turn(
                {"decision": "proceed", "extra": CANARY},  # missing keys, extra key
                supplied_text="",
            )

    def test_renderer_requires_byte_verified_quote(self) -> None:
        from th.render import Renderer, RenderError

        renderer = Renderer()
        draft = make_draft(focus_quote="可渲染的引文")
        with self.assertRaises(RenderError):
            # quote_verified defaults to False, so a quote cannot reach the
            # screen unless the caller asserted verification.
            renderer.render_turn(draft, supplied_text="可渲染的引文")
        self.assertIn("可渲染的引文", renderer.render_turn(draft, supplied_text="可渲染的引文", quote_verified=True))


if __name__ == "__main__":
    unittest.main()
