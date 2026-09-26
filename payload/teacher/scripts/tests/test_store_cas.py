"""Compare-and-swap, operation replay and the revision hash chain."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from th import Store, digest  # noqa: E402
from th import constants as C  # noqa: E402
from th.store import StoreError  # noqa: E402


def noop(connection):  # noqa: ANN001
    return {"action": "noop"}


class CompareAndSwapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = Store(":memory:")
        self.store.init()

    def tearDown(self) -> None:
        self.store.close()

    def _commit(self, *, operation_id: str, expected: str, request=None) -> dict:
        return self.store.transact(
            operation_id=operation_id,
            head=C.HEAD_EXECUTION,
            expected=expected,
            request=request or {"i": operation_id},
            mutate=noop,
        )

    def test_first_commit_advances_the_head(self) -> None:
        before = self.store.head(C.HEAD_EXECUTION)
        result = self._commit(operation_id="op-1", expected=before)
        self.assertEqual(result["status"], "committed")
        self.assertEqual(self.store.head(C.HEAD_EXECUTION), result["revision"])
        self.assertNotEqual(result["revision"], before)

    def test_stale_expected_revision_is_refused_and_writes_nothing(self) -> None:
        self._commit(operation_id="op-1", expected=self.store.head(C.HEAD_EXECUTION))
        current = self.store.head(C.HEAD_EXECUTION)
        with self.assertRaises(StoreError) as caught:
            self._commit(operation_id="op-2", expected="execution-genesis")
        self.assertEqual(caught.exception.code, C.ERR_STALE_SNAPSHOT)
        self.assertEqual(self.store.head(C.HEAD_EXECUTION), current)

    def test_replaying_an_operation_with_a_different_request_is_refused(self) -> None:
        before = self.store.head(C.HEAD_EXECUTION)
        self._commit(operation_id="op-1", expected=before, request={"a": 1})
        current = self.store.head(C.HEAD_EXECUTION)
        with self.assertRaises(StoreError) as caught:
            self._commit(operation_id="op-1", expected=current, request={"a": 2})
        self.assertEqual(caught.exception.code, C.ERR_OPERATION_REUSED)
        self.assertEqual(self.store.head(C.HEAD_EXECUTION), current)

    def test_replaying_an_operation_with_the_same_request_is_idempotent(self) -> None:
        before = self.store.head(C.HEAD_EXECUTION)
        first = self._commit(operation_id="op-1", expected=before, request={"a": 1})
        second = self._commit(operation_id="op-1", expected="whatever", request={"a": 1})
        self.assertEqual(second["status"], "replayed")
        self.assertEqual(second["revision"], first["revision"])
        self.assertEqual(self.store.head(C.HEAD_EXECUTION), first["revision"])

    def test_mutation_failure_rolls_back_entirely(self) -> None:
        before = self.store.head(C.HEAD_EXECUTION)
        events_before = len(self.store.events())

        def explode(connection):  # noqa: ANN001
            raise RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            self.store.transact(
                operation_id="op-boom",
                head=C.HEAD_EXECUTION,
                expected=before,
                request={"x": 1},
                mutate=explode,
            )
        self.assertEqual(self.store.head(C.HEAD_EXECUTION), before)
        self.assertEqual(len(self.store.events()), events_before)

    def test_heads_advance_independently(self) -> None:
        authority_before = self.store.head(C.HEAD_AUTHORITY)
        self._commit(operation_id="op-exec", expected=self.store.head(C.HEAD_EXECUTION))
        self.assertEqual(self.store.head(C.HEAD_AUTHORITY), authority_before)

    def test_unknown_head_is_refused(self) -> None:
        with self.assertRaises(StoreError) as caught:
            self.store.head("nonsense")
        self.assertEqual(caught.exception.code, C.ERR_UNKNOWN_HEAD)


class ChainTests(unittest.TestCase):
    def test_chain_links_every_revision_to_its_parent(self) -> None:
        store = Store(":memory:")
        store.init()
        try:
            revisions = []
            for index in range(5):
                result = store.transact(
                    operation_id=f"op-{index}",
                    head=C.HEAD_EXECUTION,
                    expected=store.head(C.HEAD_EXECUTION),
                    request={"i": index},
                    mutate=noop,
                )
                revisions.append(result["revision"])
            chain = store.verify_chain(C.HEAD_EXECUTION)
            self.assertEqual(chain["length"], 5)
            self.assertEqual(chain["problems"], [])
            self.assertEqual(chain["revisions"], list(reversed(revisions)))
            for index, revision in enumerate(reversed(revisions)):
                snapshot = store.snapshot(revision)
                expected_parent = (
                    "execution-genesis" if index == len(revisions) - 1 else revisions[len(revisions) - 2 - index]
                )
                self.assertEqual(snapshot["parent"], expected_parent)
        finally:
            store.close()

    def test_a_revision_commits_to_its_operation_and_request(self) -> None:
        store = Store(":memory:")
        store.init()
        try:
            request = {"payload": "value"}
            result = store.transact(
                operation_id="op-1",
                head=C.HEAD_EXECUTION,
                expected=store.head(C.HEAD_EXECUTION),
                request=request,
                mutate=noop,
            )
            snapshot = store.snapshot(result["revision"])
            self.assertEqual(snapshot["request_sha256"], digest(request))
            self.assertEqual(snapshot["operation_id"], "op-1")
            # The raw request is never persisted, only its digest.
            self.assertNotIn("value", str(snapshot))
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
