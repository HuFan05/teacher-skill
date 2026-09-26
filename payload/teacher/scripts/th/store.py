"""Durable store: two heads, compare-and-swap commits, append-only events.

The property this module exists to provide:

    A rejected or failed operation leaves both heads exactly as they were.

Every structural change goes through `transact()`, which
  1. rejects a reused operation id carrying a different request,
  2. requires the caller's `expected` revision to equal the current head
     (compare-and-swap), and
  3. verifies the new revision by reading it back before returning.

Revisions form a hash chain: each revision commits to its parent, its
operation, the canonical request and a summary of what the mutation did. So a
revision can be recomputed and a missing link is detectable after the fact.
"""

from __future__ import annotations

import functools
import json
import sqlite3
import threading
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

from . import constants as C
from .model import digest, digest_bytes, utc_now

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS heads (
  name       TEXT PRIMARY KEY,
  revision   TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS snapshots (
  revision       TEXT PRIMARY KEY,
  parent         TEXT,
  head_name      TEXT NOT NULL,
  operation_id   TEXT NOT NULL,
  request_sha256 TEXT NOT NULL,
  summary        TEXT NOT NULL,
  created_at     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS operations (
  operation_id   TEXT PRIMARY KEY,
  head_name      TEXT NOT NULL,
  request_sha256 TEXT NOT NULL,
  expected       TEXT NOT NULL,
  candidate      TEXT,
  status         TEXT NOT NULL,
  detail         TEXT NOT NULL,
  created_at     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS records (
  record_id   TEXT PRIMARY KEY,
  kind        TEXT NOT NULL,
  revision    TEXT NOT NULL,
  body        TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS record_revisions (
  record_id  TEXT NOT NULL,
  revision   TEXT NOT NULL,
  body       TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (record_id, revision)
);
CREATE TABLE IF NOT EXISTS events (
  sequence   INTEGER PRIMARY KEY AUTOINCREMENT,
  type       TEXT NOT NULL,
  payload    TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS anchors (
  anchor_id     TEXT PRIMARY KEY,
  relative_path TEXT NOT NULL,
  sha256        TEXT,
  bytes         INTEGER,
  mtime_ns      INTEGER,
  size          INTEGER,
  hash_state    TEXT NOT NULL,
  indexed_at    TEXT,
  created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS anchor_paths (
  sequence      INTEGER PRIMARY KEY AUTOINCREMENT,
  anchor_id     TEXT NOT NULL,
  relative_path TEXT NOT NULL,
  valid_from    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS route_nodes (
  node_id  TEXT PRIMARY KEY,
  kind     TEXT NOT NULL,
  status   TEXT NOT NULL,
  label    TEXT NOT NULL,
  body     TEXT NOT NULL,
  revision TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS route_edges (
  edge_id  TEXT PRIMARY KEY,
  src      TEXT NOT NULL,
  relation TEXT NOT NULL,
  dst      TEXT NOT NULL,
  body     TEXT NOT NULL,
  revision TEXT NOT NULL,
  UNIQUE (src, relation, dst)
);
CREATE TABLE IF NOT EXISTS windows (
  window_id    TEXT PRIMARY KEY,
  status       TEXT NOT NULL,
  binding      TEXT NOT NULL,
  body         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attempts (
  attempt_id       TEXT PRIMARY KEY,
  window_id        TEXT NOT NULL,
  route_id         TEXT NOT NULL,
  cognition_sha256 TEXT NOT NULL,
  status           TEXT NOT NULL,
  outcome          TEXT,
  body             TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS checkpoints (
  checkpoint_id TEXT PRIMARY KEY,
  attempt_id    TEXT NOT NULL,
  revision      TEXT NOT NULL,
  body          TEXT NOT NULL,
  created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS assessments (
  assessment_id      TEXT PRIMARY KEY,
  scope              TEXT NOT NULL,
  status             TEXT NOT NULL,
  issue_codes        TEXT NOT NULL,
  execution_revision TEXT NOT NULL,
  authority_revision TEXT NOT NULL,
  created_at         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS receipts (
  receipt_id TEXT PRIMARY KEY,
  kind       TEXT NOT NULL,
  target     TEXT NOT NULL,
  sha256     TEXT NOT NULL,
  body       TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reviews (
  review_id     TEXT PRIMARY KEY,
  target_id     TEXT NOT NULL,
  decision      TEXT NOT NULL,
  reviewer_kind TEXT NOT NULL,
  body          TEXT NOT NULL,
  created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS intakes (
  intake_id  TEXT PRIMARY KEY,
  status     TEXT NOT NULL,
  brief      TEXT NOT NULL,
  body       TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS anchor_plan_hash (
  anchor_id TEXT PRIMARY KEY,
  plan_sha256 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS frames (
  frame_id   TEXT PRIMARY KEY,
  status     TEXT NOT NULL,
  body       TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS asks (
  ask_id     TEXT PRIMARY KEY,
  frame_id   TEXT NOT NULL,
  status     TEXT NOT NULL,
  body       TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence (
  evidence_id TEXT PRIMARY KEY,
  ask_id      TEXT NOT NULL,
  kind        TEXT NOT NULL,
  locator     TEXT NOT NULL,
  text        TEXT NOT NULL,
  sha256      TEXT NOT NULL,
  verified    INTEGER NOT NULL,
  created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS candidates (
  candidate_id TEXT PRIMARY KEY,
  ask_id       TEXT NOT NULL,
  kind         TEXT NOT NULL,
  state        TEXT NOT NULL,
  score        REAL NOT NULL,
  content_sha  TEXT NOT NULL,
  body         TEXT NOT NULL,
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sections (
  section_id    TEXT PRIMARY KEY,
  root          TEXT NOT NULL,
  relative_path TEXT NOT NULL,
  anchor_id     TEXT,
  heading       TEXT NOT NULL,
  ordinal       INTEGER NOT NULL,
  text          TEXT NOT NULL,
  sha256        TEXT NOT NULL,
  file_sha256   TEXT NOT NULL,
  indexed_at    TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS sections_fts USING fts5(section_id UNINDEXED, heading, text, tokenize='trigram');
CREATE VIRTUAL TABLE IF NOT EXISTS library_fts USING fts5(item_id UNINDEXED, kind UNINDEXED, text, tokenize='trigram');
CREATE TABLE IF NOT EXISTS gates (
  gate_id          TEXT PRIMARY KEY,
  candidate_root   TEXT NOT NULL,
  round            INTEGER NOT NULL,
  state            TEXT NOT NULL,
  write_eligible   INTEGER NOT NULL,
  inventory_sha256 TEXT,
  bytes_sha256     TEXT,
  verdicts         TEXT NOT NULL,
  unresolved       TEXT NOT NULL,
  deterministic    TEXT NOT NULL,
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL
);
"""



def synchronized(method):
    """Serialise access to the connection.

    A `sqlite3.Connection` is not safe to share across threads, and the surface
    serves requests on a thread per connection while the engine may be used from
    another. A reentrant lock is enough because every mutation of the store is
    already a single short transaction.
    """

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


class StoreError(RuntimeError):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


# --------------------------------------------------------------------------
# Operation result of a mutation callback
# --------------------------------------------------------------------------
MUTATION_SUMMARY_FIELDS = ("action", "records", "anchors", "nodes", "edges", "notes")


def _normalize_summary(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise StoreError(C.ERR_UNSUPPORTED_TRANSITION, "mutation must return a summary object")
    summary: dict[str, Any] = {}
    for field in MUTATION_SUMMARY_FIELDS:
        value = raw.get(field)
        if value is None:
            summary[field] = [] if field != "action" else "unspecified"
        elif field == "action":
            summary[field] = str(value)
        elif field == "notes":
            summary[field] = [str(item) for item in value]
        else:
            summary[field] = sorted(str(item) for item in value)
    return summary


class Store:
    """Loopback-local SQLite store. It is the only writer of its own tables."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), isolation_level=None, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")

    # -- lifecycle --------------------------------------------------------
    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @synchronized
    def init(self) -> dict[str, Any]:
        self._conn.executescript(SCHEMA)
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)", (C.SCHEMA_META,)
            )
            self._conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES('created_at', ?)", (utc_now(),)
            )
            self._conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES('version', ?)", (C.VERSION,)
            )
            for name in C.HEADS:
                self._conn.execute(
                    "INSERT OR IGNORE INTO heads(name, revision, updated_at) VALUES(?, ?, ?)",
                    (name, f"{name}-genesis", utc_now()),
                )
            self._conn.execute(
                "INSERT OR IGNORE INTO events(type, payload, created_at) VALUES(?, ?, ?)",
                ("store_initialized", "{}", utc_now()),
            )
        return self.status()

    @synchronized
    def status(self) -> dict[str, Any]:
        return {
            "schema": C.SCHEMA_META,
            "version": self.meta("version"),
            "created_at": self.meta("created_at"),
            "heads": {name: self.head(name) for name in C.HEADS},
            "counts": {
                "records": self._count("records"),
                "anchors": self._count("anchors"),
                "route_nodes": self._count("route_nodes"),
                "route_edges": self._count("route_edges"),
                "attempts": self._count("attempts"),
                "checkpoints": self._count("checkpoints"),
                "assessments": self._count("assessments"),
                "receipts": self._count("receipts"),
                "events": self._count("events"),
            },
        }

    @synchronized
    def _count(self, table: str) -> int:
        return int(self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    @synchronized
    def meta(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row["value"])

    # -- heads ------------------------------------------------------------
    @synchronized
    def head(self, name: str) -> str:
        if name not in C.HEADS:
            raise StoreError(C.ERR_UNKNOWN_HEAD, name)
        row = self._conn.execute("SELECT revision FROM heads WHERE name = ?", (name,)).fetchone()
        if row is None:
            raise StoreError(C.ERR_UNKNOWN_HEAD, name)
        return str(row["revision"])

    @synchronized
    def heads(self) -> dict[str, str]:
        return {name: self.head(name) for name in C.HEADS}

    @synchronized
    def snapshot(self, revision: str) -> dict[str, Any]:
        row = self._conn.execute("SELECT * FROM snapshots WHERE revision = ?", (revision,)).fetchone()
        if row is None:
            raise StoreError(C.ERR_UNKNOWN_HEAD, f"unknown revision {revision}")
        return {
            "schema": C.SCHEMA_SNAPSHOT,
            "revision": row["revision"],
            "parent": row["parent"],
            "head_name": row["head_name"],
            "operation_id": row["operation_id"],
            "request_sha256": row["request_sha256"],
            "summary": json.loads(row["summary"]),
            "created_at": row["created_at"],
        }

    @synchronized
    def verify_chain(self, name: str, *, limit: int = 1000) -> dict[str, Any]:
        """Walk the parent links. A missing or mismatched link is reported."""

        revision = self.head(name)
        walked: list[str] = []
        problems: list[str] = []
        while revision and revision != f"{name}-genesis" and len(walked) < limit:
            try:
                snap = self.snapshot(revision)
            except StoreError:
                problems.append(f"missing snapshot {revision}")
                break
            walked.append(revision)
            parent = snap["parent"]
            if parent and parent != f"{name}-genesis":
                row = self._conn.execute("SELECT revision FROM snapshots WHERE revision = ?", (parent,)).fetchone()
                if row is None:
                    problems.append(f"dangling parent {parent}")
                    break
            revision = parent
        return {"head": name, "length": len(walked), "problems": problems, "revisions": walked}

    # -- compare-and-swap transaction -------------------------------------
    def transact(
        self,
        *,
        operation_id: str,
        head: str,
        expected: str,
        request: dict[str, Any],
        mutate: Callable[[sqlite3.Connection], Any],
        require_expected: bool = True,
    ) -> dict[str, Any]:
        """Commit one mutation against one head under compare-and-swap.

        Raises StoreError(ERR_OPERATION_REUSED) when the same operation id is
        replayed with a different request, and StoreError(ERR_STALE_SNAPSHOT)
        when `expected` is no longer the live revision. In both cases nothing
        has been written.
        """

        if head not in C.HEADS:
            raise StoreError(C.ERR_UNKNOWN_HEAD, head)
        request_sha256 = digest(request)

        existing = self._conn.execute(
            "SELECT request_sha256, status, candidate FROM operations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        if existing is not None:
            if existing["request_sha256"] != request_sha256:
                raise StoreError(
                    C.ERR_OPERATION_REUSED,
                    f"operation {operation_id} was submitted with a different request",
                )
            if existing["status"] == "committed":
                return {
                    "operation_id": operation_id,
                    "status": "replayed",
                    "head": head,
                    "revision": existing["candidate"],
                }

        current = self.head(head)
        if require_expected and expected != current:
            raise StoreError(
                C.ERR_STALE_SNAPSHOT,
                f"{head} is at {current}, caller expected {expected}",
            )

        connection = self._conn
        connection.execute("BEGIN IMMEDIATE")
        try:
            produced = mutate(connection)
            summary = _normalize_summary(produced)
            candidate = digest(
                {
                    "parent": current,
                    "head": head,
                    "operation_id": operation_id,
                    "request_sha256": request_sha256,
                    "summary": summary,
                }
            )
            updated = connection.execute(
                "UPDATE heads SET revision = ?, updated_at = ? WHERE name = ? AND revision = ?",
                (candidate, utc_now(), head, current),
            ).rowcount
            if updated != 1:
                raise StoreError(C.ERR_RECOVERY_CONFLICT, f"{head} moved during the transaction")
            connection.execute(
                "INSERT INTO snapshots(revision, parent, head_name, operation_id, request_sha256, summary, created_at)"
                " VALUES(?, ?, ?, ?, ?, ?, ?)",
                (candidate, current, head, operation_id, request_sha256,
                 json.dumps(summary, ensure_ascii=False, sort_keys=True), utc_now()),
            )
            connection.execute(
                "INSERT INTO operations(operation_id, head_name, request_sha256, expected, candidate, status, detail, created_at)"
                " VALUES(?, ?, ?, ?, ?, 'committed', ?, ?)",
                (operation_id, head, request_sha256, current, candidate,
                 json.dumps(summary, ensure_ascii=False, sort_keys=True), utc_now()),
            )
            self._append_event(connection, f"{head}_advanced", {
                "operation_id": operation_id,
                "parent": current,
                "revision": candidate,
                "action": summary["action"],
                # Naming the touched assets is what makes event coalescing
                # possible: without it a maintenance pass cannot group repeat
                # events by the thing they changed.
                "records": summary["records"],
                "anchors": summary["anchors"],
                "nodes": summary["nodes"],
            })
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise

        readback = self.head(head)
        if readback != candidate:
            raise StoreError(
                C.ERR_COMMIT_READBACK_FAILED,
                f"{head} reads back as {readback} after committing {candidate}",
            )
        return {"operation_id": operation_id, "status": "committed", "head": head, "revision": candidate, "summary": summary}

    @synchronized
    def _append_event(self, connection: sqlite3.Connection, event_type: str, payload: dict[str, Any]) -> None:
        connection.execute(
            "INSERT INTO events(type, payload, created_at) VALUES(?, ?, ?)",
            (event_type, json.dumps(payload, ensure_ascii=False, sort_keys=True), utc_now()),
        )

    def observe(
        self,
        *,
        event_type: str,
        payload: dict[str, Any],
        mutate: Callable[[sqlite3.Connection], Any],
    ) -> Any:
        """Write rows and append an event **without advancing a head**.

        This exists for observations: an assessment or a failure receipt
        describes the state rather than changing it. Advancing a head here would
        be self-defeating, because the observation would immediately be stale
        with respect to the very revision it recorded.
        """

        connection = self._conn
        connection.execute("BEGIN IMMEDIATE")
        try:
            produced = mutate(connection)
            self._append_event(connection, event_type, payload)
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        return produced

    @synchronized
    def events(self, *, after_sequence: int | None = None, limit: int = 500) -> list[dict[str, Any]]:
        sql = "SELECT * FROM events"
        params: list[Any] = []
        if after_sequence is not None:
            sql += " WHERE sequence > ?"
            params.append(after_sequence)
        sql += " ORDER BY sequence LIMIT ?"
        params.append(limit)
        return [
            {
                "sequence": int(row["sequence"]),
                "type": row["type"],
                "payload": json.loads(row["payload"]),
                "created_at": row["created_at"],
            }
            for row in self._conn.execute(sql, params)
        ]

    @synchronized
    def last_event_sequence(self) -> int:
        row = self._conn.execute("SELECT COALESCE(MAX(sequence), 0) AS s FROM events").fetchone()
        return int(row["s"])

    # -- records ----------------------------------------------------------
    @synchronized
    def put_record(self, connection: sqlite3.Connection, record: dict[str, Any]) -> str:
        body = json.dumps(record, ensure_ascii=False, sort_keys=True)
        revision = digest(record)
        exists = connection.execute("SELECT 1 FROM records WHERE record_id = ?", (record["record_id"],)).fetchone()
        now = utc_now()
        if exists:
            connection.execute(
                "UPDATE records SET kind = ?, revision = ?, body = ?, updated_at = ? WHERE record_id = ?",
                (record["kind"], revision, body, now, record["record_id"]),
            )
        else:
            connection.execute(
                "INSERT INTO records(record_id, kind, revision, body, created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?)",
                (record["record_id"], record["kind"], revision, body, now, now),
            )
        connection.execute(
            "INSERT OR REPLACE INTO record_revisions(record_id, revision, body, created_at) VALUES(?, ?, ?, ?)",
            (record["record_id"], revision, body, now),
        )
        return revision

    @synchronized
    def record(self, record_id: str) -> dict[str, Any]:
        row = self._conn.execute("SELECT * FROM records WHERE record_id = ?", (record_id,)).fetchone()
        if row is None:
            raise StoreError(C.ERR_UNKNOWN_RECORD, record_id)
        record = json.loads(row["body"])
        record["_revision"] = row["revision"]
        return record

    @synchronized
    def record_revision(self, record_id: str) -> str:
        row = self._conn.execute("SELECT revision FROM records WHERE record_id = ?", (record_id,)).fetchone()
        if row is None:
            raise StoreError(C.ERR_UNKNOWN_RECORD, record_id)
        return str(row["revision"])

    @synchronized
    def records(self, kind: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT record_id FROM records"
        params: tuple[Any, ...] = ()
        if kind is not None:
            sql += " WHERE kind = ?"
            params = (kind,)
        sql += " ORDER BY created_at, record_id"
        out = []
        for row in self._conn.execute(sql, params):
            out.append(self.record(str(row["record_id"])))
        return out

    @synchronized
    def record_ids(self, kind: str | None = None) -> list[str]:
        return [record["record_id"] for record in self.records(kind)]

    @synchronized
    def revisions_of(self, record_id: str) -> list[str]:
        return [
            str(row["revision"])
            for row in self._conn.execute(
                "SELECT revision FROM record_revisions WHERE record_id = ? ORDER BY created_at", (record_id,)
            )
        ]

    # -- relations / dependency closure -----------------------------------
    @synchronized
    def add_relation(self, connection: sqlite3.Connection, *, src: str, relation: str, dst: str, reason: str) -> str:
        if relation not in C.RELATIONS:
            raise StoreError(C.ERR_UNSUPPORTED_TRANSITION, f"unknown relation {relation}")
        edge_id = digest({"src": src, "relation": relation, "dst": dst})
        body = json.dumps({"reason": reason}, ensure_ascii=False, sort_keys=True)
        connection.execute(
            "INSERT OR REPLACE INTO route_edges(edge_id, src, relation, dst, body, revision)"
            " VALUES(?, ?, ?, ?, ?, ?)",
            (edge_id, src, relation, dst, body, digest({"src": src, "relation": relation, "dst": dst, "reason": reason})),
        )
        return edge_id

    @synchronized
    def edges_from(self, src: str) -> list[dict[str, Any]]:
        return [
            {"edge_id": row["edge_id"], "src": row["src"], "relation": row["relation"], "dst": row["dst"]}
            for row in self._conn.execute(
                "SELECT * FROM route_edges WHERE src = ? ORDER BY relation, dst", (src,)
            )
        ]

    @synchronized
    def all_edges(self) -> list[dict[str, Any]]:
        return [
            {"edge_id": row["edge_id"], "src": row["src"], "relation": row["relation"], "dst": row["dst"]}
            for row in self._conn.execute("SELECT * FROM route_edges ORDER BY src, relation, dst")
        ]

    @synchronized
    def dependency_closure(self, record_id: str) -> dict[str, Any]:
        """Strong-relation closure plus cycle detection. Never guesses meaning."""

        seen: set[str] = set()
        stack = [record_id]
        missing: list[str] = []
        edges: list[dict[str, str]] = []
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            for edge in self.edges_from(current):
                if edge["relation"] not in C.STRONG_RELATIONS:
                    continue
                edges.append({"src": edge["src"], "relation": edge["relation"], "dst": edge["dst"]})
                target = edge["dst"]
                if not self._record_exists(target):
                    missing.append(target)
                    continue
                stack.append(target)
        cycle = self._find_cycle(seen, edges)
        return {
            "root": record_id,
            "nodes": sorted(seen),
            "edges": edges,
            "missing": sorted(set(missing)),
            "cycle": cycle,
            "closed": not missing and not cycle,
        }

    @synchronized
    def _record_exists(self, record_id: str) -> bool:
        return self._conn.execute("SELECT 1 FROM records WHERE record_id = ?", (record_id,)).fetchone() is not None

    @synchronized
    def _find_cycle(self, nodes: set[str], edges: Sequence[dict[str, str]]) -> list[str] | None:
        adjacency: dict[str, list[str]] = {}
        for edge in edges:
            adjacency.setdefault(edge["src"], []).append(edge["dst"])
        colour: dict[str, int] = {}
        path: list[str] = []

        def visit(node: str) -> list[str] | None:
            colour[node] = 1
            path.append(node)
            for nxt in adjacency.get(node, []):
                if colour.get(nxt) == 1:
                    return path[path.index(nxt):] + [nxt]
                if colour.get(nxt, 0) == 0:
                    found = visit(nxt)
                    if found:
                        return found
            colour[node] = 2
            path.pop()
            return None

        for node in sorted(nodes):
            if colour.get(node, 0) == 0:
                found = visit(node)
                if found:
                    return found
        return None

    # -- anchors (layered memory) -----------------------------------------
    def register_anchor(
        self,
        connection: sqlite3.Connection,
        *,
        relative_path: str,
        sha256: str | None = None,
        size: int | None = None,
        mtime_ns: int | None = None,
    ) -> str:
        """Create a new anchor. The identifier never encodes the path."""

        existing = connection.execute(
            "SELECT anchor_id FROM anchors WHERE relative_path = ?", (relative_path,)
        ).fetchone()
        if existing is not None:
            return str(existing["anchor_id"])
        from .model import new_anchor_id

        anchor_id = new_anchor_id()
        now = utc_now()
        connection.execute(
            "INSERT INTO anchors(anchor_id, relative_path, sha256, bytes, mtime_ns, size, hash_state, indexed_at, created_at)"
            " VALUES(?, ?, ?, ?, ?, ?, 'unverified', NULL, ?)",
            (anchor_id, relative_path, sha256, size, mtime_ns, size, now),
        )
        connection.execute(
            "INSERT INTO anchor_paths(anchor_id, relative_path, valid_from) VALUES(?, ?, ?)",
            (anchor_id, relative_path, now),
        )
        return anchor_id

    @synchronized
    def move_anchor(self, connection: sqlite3.Connection, *, anchor_id: str, relative_path: str) -> None:
        """Retire the old path and record a new validity interval.

        The identifier does not change, which is the whole point of not
        encoding meaning into it.
        """

        connection.execute(
            "UPDATE anchors SET relative_path = ? WHERE anchor_id = ?", (relative_path, anchor_id)
        )
        connection.execute(
            "INSERT INTO anchor_paths(anchor_id, relative_path, valid_from) VALUES(?, ?, ?)",
            (anchor_id, relative_path, utc_now()),
        )

    @synchronized
    def anchor(self, anchor_id: str) -> dict[str, Any]:
        row = self._conn.execute("SELECT * FROM anchors WHERE anchor_id = ?", (anchor_id,)).fetchone()
        if row is None:
            raise StoreError(C.ERR_UNKNOWN_ANCHOR, anchor_id)
        return self._anchor_row(row)

    @staticmethod
    def _anchor_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "schema": C.SCHEMA_ASSET,
            "anchor_id": row["anchor_id"],
            "relative_path": row["relative_path"],
            "sha256": row["sha256"],
            "bytes": row["bytes"],
            "mtime_ns": row["mtime_ns"],
            "size": row["size"],
            "hash_state": row["hash_state"],
            "indexed_at": row["indexed_at"],
            "created_at": row["created_at"],
        }

    @synchronized
    def anchors(self) -> list[dict[str, Any]]:
        return [
            self._anchor_row(row)
            for row in self._conn.execute("SELECT * FROM anchors ORDER BY relative_path")
        ]

    @synchronized
    def anchor_history(self, anchor_id: str) -> list[dict[str, str]]:
        """Path history in a total order.

        Ordered by an insertion sequence rather than by `valid_from`: a
        timestamp at second resolution cannot order two changes made in the same
        second, and a path history that reorders itself is not a history.
        """

        return [
            {"relative_path": row["relative_path"], "valid_from": row["valid_from"]}
            for row in self._conn.execute(
                "SELECT relative_path, valid_from FROM anchor_paths WHERE anchor_id = ? ORDER BY sequence",
                (anchor_id,),
            )
        ]

    @synchronized
    def resolve_anchor(self, anchor_id: str) -> dict[str, Any]:
        """Resolve under a root that the caller supplies.

        The store deliberately does not own a root: a compiled path would be a
        capability the store does not have.
        """

        anchor = self.anchor(anchor_id)
        return {"anchor_id": anchor_id, "relative_path": anchor["relative_path"], "hash_state": anchor["hash_state"]}

    @synchronized
    def set_anchor_hash(self, connection: sqlite3.Connection, anchor_id: str, sha256: str, *, size: int, mtime_ns: int) -> None:
        connection.execute(
            "UPDATE anchors SET sha256 = ?, bytes = ?, size = ?, mtime_ns = ?, hash_state = 'verified', indexed_at = ?"
            " WHERE anchor_id = ?",
            (sha256, size, size, mtime_ns, utc_now(), anchor_id),
        )

    @synchronized
    def mark_anchor_stale(self, connection: sqlite3.Connection, anchor_ids: Iterable[str]) -> list[str]:
        marked = []
        for anchor_id in anchor_ids:
            connection.execute("UPDATE anchors SET hash_state = 'stale' WHERE anchor_id = ?", (anchor_id,))
            marked.append(anchor_id)
        return marked

    # -- route graph ------------------------------------------------------
    def put_node(
        self,
        connection: sqlite3.Connection,
        *,
        kind: str,
        label: str,
        status: str = "open",
        body: dict[str, Any] | None = None,
        node_id: str | None = None,
    ) -> str:
        if kind not in C.NODE_KINDS:
            raise StoreError(C.ERR_UNSUPPORTED_TRANSITION, f"unknown node kind {kind}")
        if status not in C.NODE_STATUSES:
            raise StoreError(C.ERR_UNSUPPORTED_TRANSITION, f"unknown node status {status}")
        from .model import new_id

        node_id = node_id or new_id("ND")
        payload = json.dumps(body or {}, ensure_ascii=False, sort_keys=True)
        revision = digest({"node_id": node_id, "kind": kind, "label": label, "status": status, "body": body or {}})
        connection.execute(
            "INSERT OR REPLACE INTO route_nodes(node_id, kind, status, label, body, revision) VALUES(?, ?, ?, ?, ?, ?)",
            (node_id, kind, status, label, payload, revision),
        )
        return node_id

    @synchronized
    def node(self, node_id: str) -> dict[str, Any]:
        row = self._conn.execute("SELECT * FROM route_nodes WHERE node_id = ?", (node_id,)).fetchone()
        if row is None:
            raise StoreError(C.ERR_UNKNOWN_RECORD, node_id)
        return {
            "node_id": row["node_id"],
            "kind": row["kind"],
            "status": row["status"],
            "label": row["label"],
            "body": json.loads(row["body"]),
            "revision": row["revision"],
        }

    @synchronized
    def nodes(self, *, kind: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT node_id FROM route_nodes"
        params: tuple[Any, ...] = ()
        if kind is not None:
            sql += " WHERE kind = ?"
            params = (kind,)
        sql += " ORDER BY node_id"
        return [self.node(str(row["node_id"])) for row in self._conn.execute(sql, params)]

    @synchronized
    def node_ids(self, kind: str | None = None) -> set[str]:
        return {node["node_id"] for node in self.nodes(kind=kind)}

    @synchronized
    def node_ids_by_label(self) -> dict[str, str]:
        """Map label code to node id.

        Labels are the only vocabulary a backend uses, so this lookup is what
        turns a validated token into a real reference.
        """

        mapping: dict[str, str] = {}
        for row in self._conn.execute("SELECT node_id, label FROM route_nodes"):
            mapping.setdefault(str(row["label"]), str(row["node_id"]))
        return mapping

    @synchronized
    def graph_snapshot_hash(self) -> str:
        nodes = [
            {"node_id": n["node_id"], "kind": n["kind"], "status": n["status"], "label": n["label"], "revision": n["revision"]}
            for n in self.nodes()
        ]
        edges = self.all_edges()
        return digest({"nodes": nodes, "edges": edges})

    # -- attempts / checkpoints / windows ---------------------------------
    @synchronized
    def put_window(self, connection: sqlite3.Connection, *, window_id: str, binding: str, body: dict[str, Any]) -> None:
        connection.execute(
            "INSERT OR REPLACE INTO windows(window_id, status, binding, body) VALUES(?, 'open', ?, ?)",
            (window_id, binding, json.dumps(body, ensure_ascii=False, sort_keys=True)),
        )

    @synchronized
    def window(self, window_id: str) -> dict[str, Any]:
        row = self._conn.execute("SELECT * FROM windows WHERE window_id = ?", (window_id,)).fetchone()
        if row is None:
            raise StoreError(C.ERR_UNKNOWN_RECORD, window_id)
        return {"window_id": row["window_id"], "status": row["status"], "binding": row["binding"], "body": json.loads(row["body"])}

    def put_attempt(
        self,
        connection: sqlite3.Connection,
        *,
        attempt_id: str,
        window_id: str,
        route_id: str,
        cognition_sha256: str,
        body: dict[str, Any],
        status: str = C.ATTEMPT_OPEN,
    ) -> None:
        connection.execute(
            "INSERT OR REPLACE INTO attempts(attempt_id, window_id, route_id, cognition_sha256, status, outcome, body)"
            " VALUES(?, ?, ?, ?, ?, NULL, ?)",
            (attempt_id, window_id, route_id, cognition_sha256, status,
             json.dumps(body, ensure_ascii=False, sort_keys=True)),
        )

    @synchronized
    def attempt(self, attempt_id: str) -> dict[str, Any]:
        row = self._conn.execute("SELECT * FROM attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()
        if row is None:
            raise StoreError(C.ERR_UNKNOWN_RECORD, attempt_id)
        return {
            "attempt_id": row["attempt_id"],
            "window_id": row["window_id"],
            "route_id": row["route_id"],
            "cognition_sha256": row["cognition_sha256"],
            "status": row["status"],
            "outcome": row["outcome"],
            "body": json.loads(row["body"]),
        }

    @synchronized
    def attempts(self, *, window_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT attempt_id FROM attempts"
        params: tuple[Any, ...] = ()
        if window_id is not None:
            sql += " WHERE window_id = ?"
            params = (window_id,)
        sql += " ORDER BY attempt_id"
        return [self.attempt(str(row["attempt_id"])) for row in self._conn.execute(sql, params)]

    @synchronized
    def close_attempt(self, connection: sqlite3.Connection, *, attempt_id: str, outcome: str) -> None:
        if outcome not in C.OUTCOMES:
            raise StoreError(C.ERR_UNSUPPORTED_TRANSITION, f"unknown outcome {outcome}")
        connection.execute(
            "UPDATE attempts SET status = 'closed', outcome = ? WHERE attempt_id = ?", (outcome, attempt_id)
        )

    def put_checkpoint(
        self,
        connection: sqlite3.Connection,
        *,
        checkpoint_id: str,
        attempt_id: str,
        body: dict[str, Any],
        revision: str,
    ) -> None:
        connection.execute(
            "INSERT OR REPLACE INTO checkpoints(checkpoint_id, attempt_id, revision, body, created_at) VALUES(?, ?, ?, ?, ?)",
            (checkpoint_id, attempt_id, revision, json.dumps(body, ensure_ascii=False, sort_keys=True), utc_now()),
        )

    @synchronized
    def checkpoints(self, *, attempt_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM checkpoints"
        params: tuple[Any, ...] = ()
        if attempt_id is not None:
            sql += " WHERE attempt_id = ?"
            params = (attempt_id,)
        sql += " ORDER BY created_at, checkpoint_id"
        return [
            {
                "checkpoint_id": row["checkpoint_id"],
                "attempt_id": row["attempt_id"],
                "revision": row["revision"],
                "body": json.loads(row["body"]),
                "created_at": row["created_at"],
            }
            for row in self._conn.execute(sql, params)
        ]

    # -- reviews / assessments / receipts ---------------------------------
    def put_review(
        self,
        connection: sqlite3.Connection,
        *,
        review_id: str,
        target_id: str,
        decision: str,
        reviewer_kind: str,
        body: dict[str, Any],
    ) -> None:
        if decision not in C.REVIEW_DECISIONS:
            raise StoreError(C.ERR_UNSUPPORTED_TRANSITION, f"unknown review decision {decision}")
        if reviewer_kind not in C.REVIEWER_KINDS:
            raise StoreError(C.ERR_UNSUPPORTED_TRANSITION, f"unknown reviewer kind {reviewer_kind}")
        connection.execute(
            "INSERT OR REPLACE INTO reviews(review_id, target_id, decision, reviewer_kind, body, created_at)"
            " VALUES(?, ?, ?, ?, ?, ?)",
            (review_id, target_id, decision, reviewer_kind,
             json.dumps(body, ensure_ascii=False, sort_keys=True), utc_now()),
        )

    @synchronized
    def reviews_for(self, target_id: str) -> list[dict[str, Any]]:
        return [
            {
                "review_id": row["review_id"],
                "target_id": row["target_id"],
                "decision": row["decision"],
                "reviewer_kind": row["reviewer_kind"],
                "body": json.loads(row["body"]),
                "created_at": row["created_at"],
            }
            for row in self._conn.execute(
                "SELECT * FROM reviews WHERE target_id = ? ORDER BY created_at", (target_id,)
            )
        ]

    @synchronized
    def review_state(self, target_id: str) -> str:
        """Counted reviews only.

        A foreign review never upgrades local trust, and a self-review by the
        party that produced the claim is recorded but never counted. The kinds
        that count are the local operator (`local`/`human`), an isolated
        reviewer this Skill itself called (`independent_model`), and an
        independence the host agent declared (`declared_independent`) — the last
        is counted but stays distinguishable, because software cannot
        authenticate it.
        """

        local = [review for review in self.reviews_for(target_id) if review["reviewer_kind"] in C.COUNTED_REVIEWERS]
        if not local:
            return C.REVIEW_UNREVIEWED
        decisions = {review["decision"] for review in local}
        if len(decisions) > 1:
            return C.REVIEW_INCONCLUSIVE
        return next(iter(decisions))

    @synchronized
    def put_assessment(self, connection: sqlite3.Connection, assessment: dict[str, Any]) -> None:
        connection.execute(
            "INSERT OR REPLACE INTO assessments(assessment_id, scope, status, issue_codes, execution_revision, authority_revision, created_at)"
            " VALUES(?, ?, ?, ?, ?, ?, ?)",
            (assessment["assessment_id"], assessment["scope"], assessment["status"],
             json.dumps(assessment["issue_codes"]), assessment["execution_revision"],
             assessment["authority_revision"], assessment["created_at"]),
        )

    @synchronized
    def assessment(self, assessment_id: str) -> dict[str, Any]:
        row = self._conn.execute("SELECT * FROM assessments WHERE assessment_id = ?", (assessment_id,)).fetchone()
        if row is None:
            raise StoreError(C.ERR_UNKNOWN_RECORD, assessment_id)
        return {
            "schema": C.SCHEMA_ASSESSMENT,
            "assessment_id": row["assessment_id"],
            "scope": row["scope"],
            "status": row["status"],
            "issue_codes": json.loads(row["issue_codes"]),
            "execution_revision": row["execution_revision"],
            "authority_revision": row["authority_revision"],
            "created_at": row["created_at"],
        }

    @synchronized
    def put_receipt(self, connection: sqlite3.Connection, *, receipt_id: str, kind: str, target: str, sha256: str, body: dict[str, Any]) -> None:
        connection.execute(
            "INSERT OR REPLACE INTO receipts(receipt_id, kind, target, sha256, body, created_at) VALUES(?, ?, ?, ?, ?, ?)",
            (receipt_id, kind, target, sha256, json.dumps(body, ensure_ascii=False, sort_keys=True), utc_now()),
        )

    @synchronized
    def provenance_of(self, record_id: str) -> str:
        """Where a record came from. Content is never a control channel."""

        kind = self.record(record_id)["kind"]
        if kind == C.KIND_ARTIFACT:
            return "artifact"
        if kind == C.KIND_OBSERVATION:
            return "observation"
        return "local"

    # -- acceptance gates -------------------------------------------------
    @synchronized
    def put_gate(self, connection: sqlite3.Connection, gate: dict[str, Any]) -> None:
        connection.execute(
            "INSERT OR REPLACE INTO gates(gate_id, candidate_root, round, state, write_eligible,"
            " inventory_sha256, bytes_sha256, verdicts, unresolved, deterministic, created_at, updated_at)"
            " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                gate["gate_id"],
                gate["candidate_root"],
                int(gate["round"]),
                gate["state"],
                1 if gate["write_eligible"] else 0,
                gate.get("inventory_sha256"),
                gate.get("bytes_sha256"),
                json.dumps(gate.get("verdicts", []), ensure_ascii=False, sort_keys=True),
                json.dumps(gate.get("unresolved", []), ensure_ascii=False, sort_keys=True),
                json.dumps(gate.get("deterministic", []), ensure_ascii=False, sort_keys=True),
                gate["created_at"],
                utc_now(),
            ),
        )

    @synchronized
    def gate(self, gate_id: str) -> dict[str, Any]:
        row = self._conn.execute("SELECT * FROM gates WHERE gate_id = ?", (gate_id,)).fetchone()
        if row is None:
            raise StoreError(C.ERR_UNKNOWN_RECORD, gate_id)
        return {
            "schema": "th-gate/v1",
            "gate_id": row["gate_id"],
            "candidate_root": row["candidate_root"],
            "round": int(row["round"]),
            "state": row["state"],
            "write_eligible": bool(row["write_eligible"]),
            "inventory_sha256": row["inventory_sha256"],
            "bytes_sha256": row["bytes_sha256"],
            "verdicts": json.loads(row["verdicts"]),
            "unresolved": json.loads(row["unresolved"]),
            "deterministic": json.loads(row["deterministic"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @synchronized
    def gates(self) -> list[dict[str, Any]]:
        return [
            self.gate(str(row["gate_id"]))
            for row in self._conn.execute("SELECT gate_id FROM gates ORDER BY created_at")
        ]

    # -- intake ----------------------------------------------------------
    def put_intake(self, connection: sqlite3.Connection, intake: dict[str, Any]) -> None:
        connection.execute(
            "INSERT OR REPLACE INTO intakes(intake_id, status, brief, body, created_at, updated_at)"
            " VALUES(?, ?, ?, ?, ?, ?)",
            (
                intake["intake_id"],
                intake["status"],
                intake["brief"],
                json.dumps(intake, ensure_ascii=False, sort_keys=True),
                intake["created_at"],
                utc_now(),
            ),
        )

    @synchronized
    def intake(self, intake_id: str) -> dict[str, Any]:
        row = self._conn.execute("SELECT body FROM intakes WHERE intake_id = ?", (intake_id,)).fetchone()
        if row is None:
            raise StoreError(C.ERR_UNKNOWN_RECORD, intake_id)
        return json.loads(row["body"])

    @synchronized
    def latest_intake(self) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT body FROM intakes ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        return json.loads(row["body"]) if row is not None else None



    # -- generic bounded access ------------------------------------------
    @synchronized
    def rows(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        """Read-only query helper for the modules that own the newer tables."""

        return [dict(row) for row in self._conn.execute(sql, tuple(params))]

    # -- need frames ------------------------------------------------------
    @synchronized
    def put_frame(self, connection: sqlite3.Connection, frame: dict[str, Any]) -> None:
        now = utc_now()
        connection.execute(
            "INSERT INTO frames(frame_id, status, body, created_at, updated_at) VALUES(?, ?, ?, ?, ?)"
            " ON CONFLICT(frame_id) DO UPDATE SET status = excluded.status, body = excluded.body,"
            " updated_at = excluded.updated_at",
            (frame["frame_id"], frame["status"], json.dumps(frame, ensure_ascii=False, sort_keys=True), now, now),
        )

    @synchronized
    def frame(self, frame_id: str) -> dict[str, Any]:
        row = self._conn.execute("SELECT body FROM frames WHERE frame_id = ?", (frame_id,)).fetchone()
        if row is None:
            raise StoreError(C.ERR_UNKNOWN_RECORD, frame_id)
        return json.loads(row["body"])

    @synchronized
    def latest_frame(self, *, status: str | None = None) -> dict[str, Any] | None:
        sql = "SELECT body FROM frames"
        params: list[Any] = []
        if status is not None:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY rowid DESC LIMIT 1"
        row = self._conn.execute(sql, params).fetchone()
        return json.loads(row["body"]) if row else None

    # -- asks and evidence ------------------------------------------------
    @synchronized
    def put_ask(self, connection: sqlite3.Connection, ask: dict[str, Any]) -> None:
        connection.execute(
            "INSERT OR REPLACE INTO asks(ask_id, frame_id, status, body, created_at) VALUES(?, ?, ?, ?, ?)",
            (ask["ask_id"], ask["frame_id"], ask["status"], json.dumps(ask, ensure_ascii=False, sort_keys=True), utc_now()),
        )

    @synchronized
    def ask(self, ask_id: str) -> dict[str, Any]:
        row = self._conn.execute("SELECT body FROM asks WHERE ask_id = ?", (ask_id,)).fetchone()
        if row is None:
            raise StoreError(C.ERR_UNKNOWN_RECORD, ask_id)
        return json.loads(row["body"])

    @synchronized
    def asks(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return [
            json.loads(row["body"])
            for row in self._conn.execute("SELECT body FROM asks ORDER BY rowid DESC LIMIT ?", (limit,))
        ]

    @synchronized
    def put_evidence(self, connection: sqlite3.Connection, item: dict[str, Any]) -> None:
        connection.execute(
            "INSERT OR REPLACE INTO evidence(evidence_id, ask_id, kind, locator, text, sha256, verified, created_at)"
            " VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (item["evidence_id"], item["ask_id"], item["kind"], item["locator"], item["text"],
             item["sha256"], 1 if item.get("verified") else 0, utc_now()),
        )

    @synchronized
    def evidence_item(self, evidence_id: str) -> dict[str, Any]:
        row = self._conn.execute("SELECT * FROM evidence WHERE evidence_id = ?", (evidence_id,)).fetchone()
        if row is None:
            raise StoreError(C.ERR_UNKNOWN_RECORD, evidence_id)
        item = dict(row)
        item["verified"] = bool(item["verified"])
        return item

    @synchronized
    def evidence_for(self, ask_id: str) -> list[dict[str, Any]]:
        items = []
        for row in self._conn.execute("SELECT * FROM evidence WHERE ask_id = ? ORDER BY rowid", (ask_id,)):
            item = dict(row)
            item["verified"] = bool(item["verified"])
            items.append(item)
        return items

    # -- archive candidates -----------------------------------------------
    @synchronized
    def put_candidate(self, connection: sqlite3.Connection, candidate: dict[str, Any]) -> None:
        now = utc_now()
        connection.execute(
            "INSERT INTO candidates(candidate_id, ask_id, kind, state, score, content_sha, body, created_at, updated_at)"
            " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(candidate_id) DO UPDATE SET state = excluded.state, score = excluded.score,"
            " body = excluded.body, updated_at = excluded.updated_at",
            (candidate["candidate_id"], candidate["ask_id"], candidate["kind"], candidate["state"],
             float(candidate["score"]), candidate["content_sha"],
             json.dumps(candidate, ensure_ascii=False, sort_keys=True), now, now),
        )

    @synchronized
    def candidates(self, *, state: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT body FROM candidates"
        params: list[Any] = []
        if state is not None:
            sql += " WHERE state = ?"
            params.append(state)
        sql += " ORDER BY score DESC, rowid"
        return [json.loads(row["body"]) for row in self._conn.execute(sql, params)]

    @synchronized
    def candidate_by_content(self, content_sha: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT body FROM candidates WHERE content_sha = ? ORDER BY rowid LIMIT 1", (content_sha,)
        ).fetchone()
        return json.loads(row["body"]) if row else None

    # -- library full-text ------------------------------------------------
    @synchronized
    def put_library_text(self, connection: sqlite3.Connection, *, item_id: str, kind: str, text: str) -> None:
        connection.execute("DELETE FROM library_fts WHERE item_id = ?", (item_id,))
        connection.execute("INSERT INTO library_fts(item_id, kind, text) VALUES(?, ?, ?)", (item_id, kind, text))



def digest_file(path: Path) -> str:
    return digest_bytes(path.read_bytes())
