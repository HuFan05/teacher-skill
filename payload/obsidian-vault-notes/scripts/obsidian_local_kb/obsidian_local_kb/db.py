from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .markdown import NoteData, parse_markdown_note
from .util import (
    build_match_query,
    make_search_blob,
    normalize_note_key,
    normalize_text,
    note_key_from_relative_path,
    tokenize_search_text,
)


CURRENT_SCHEMA_VERSION = 3
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    path_key TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    title_norm TEXT NOT NULL,
    source_mtime_ns INTEGER NOT NULL DEFAULT 0,
    source_size INTEGER NOT NULL DEFAULT 0,
    indexed_at_utc TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS index_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS note_aliases (
    note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    alias_norm TEXT NOT NULL,
    UNIQUE(note_id, alias_norm)
);

CREATE TABLE IF NOT EXISTS note_tags (
    note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    tag_norm TEXT NOT NULL,
    UNIQUE(note_id, tag_norm)
);

CREATE TABLE IF NOT EXISTS sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    section_key INTEGER NOT NULL,
    heading TEXT,
    heading_path TEXT NOT NULL,
    heading_norm TEXT,
    level INTEGER NOT NULL,
    content TEXT NOT NULL,
    snippet TEXT NOT NULL,
    UNIQUE(note_id, section_key)
);

CREATE TABLE IF NOT EXISTS links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    source_section_key INTEGER NOT NULL,
    target_raw TEXT NOT NULL,
    target_note_raw TEXT,
    target_note_norm TEXT,
    target_heading_raw TEXT,
    target_heading_norm TEXT,
    resolved_note_id INTEGER REFERENCES notes(id) ON DELETE SET NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS section_fts USING fts5(
    search_text,
    title_text,
    heading_text,
    path_text,
    alias_text,
    tag_text,
    section_id UNINDEXED,
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE INDEX IF NOT EXISTS idx_sections_note_id ON sections(note_id);
CREATE INDEX IF NOT EXISTS idx_note_aliases_norm ON note_aliases(alias_norm);
CREATE INDEX IF NOT EXISTS idx_note_tags_norm ON note_tags(tag_norm);
CREATE INDEX IF NOT EXISTS idx_links_source_note ON links(source_note_id);
CREATE INDEX IF NOT EXISTS idx_links_resolved_note ON links(resolved_note_id);
"""


@dataclass(slots=True)
class QueryResult:
    score: float
    note_id: int
    title: str
    path: str
    heading_path: str
    snippet: str
    reasons: list[str]


def connect(db_path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        uri = db_path.resolve().as_uri() + "?mode=ro"
        con = sqlite3.connect(uri, uri=True)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        return con

    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA_SQL)
    return con


def _indexed_at_utc() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _set_index_meta(con: sqlite3.Connection, *, indexed_at_utc: str, note_count: int) -> None:
    con.executemany(
        """
        INSERT INTO index_meta(key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        [
            ("schema_version", str(CURRENT_SCHEMA_VERSION)),
            ("indexed_at_utc", indexed_at_utc),
            ("note_count", str(note_count)),
        ],
    )


def _insert_note_record(con: sqlite3.Connection, note: NoteData, indexed_at_utc: str) -> int:
    path_key = note_key_from_relative_path(note.path)
    cur = con.execute(
        """
        INSERT INTO notes(path, path_key, title, title_norm, source_mtime_ns, source_size, indexed_at_utc)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            note.path,
            path_key,
            note.title,
            note.title_norm,
            note.source_mtime_ns,
            note.source_size,
            indexed_at_utc,
        ),
    )
    note_id = int(cur.lastrowid)
    _insert_note_children(con, note_id=note_id, note=note)
    return note_id


def _insert_note_children(con: sqlite3.Connection, *, note_id: int, note: NoteData) -> None:
    alias_rows = [(note_id, alias, normalize_note_key(alias)) for alias in note.aliases]
    tag_rows = [(note_id, tag, normalize_text(tag.lstrip("#"))) for tag in note.tags]
    if alias_rows:
        con.executemany(
            "INSERT OR IGNORE INTO note_aliases(note_id, alias, alias_norm) VALUES (?, ?, ?)",
            alias_rows,
        )
    if tag_rows:
        con.executemany(
            "INSERT OR IGNORE INTO note_tags(note_id, tag, tag_norm) VALUES (?, ?, ?)",
            tag_rows,
        )

    alias_blob = make_search_blob(*note.aliases)
    tag_blob = make_search_blob(*note.tags)
    for section in note.sections:
        cur = con.execute(
            """
            INSERT INTO sections(note_id, section_key, heading, heading_path, heading_norm, level, content, snippet)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                note_id,
                section.section_key,
                section.heading,
                section.heading_path,
                section.heading_norm,
                section.level,
                section.content,
                section.snippet,
            ),
        )
        section_id = int(cur.lastrowid)
        con.execute(
            """
            INSERT INTO section_fts(
                search_text, title_text, heading_text, path_text, alias_text, tag_text, section_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                make_search_blob(section.content),
                make_search_blob(note.title),
                make_search_blob(section.heading_path),
                make_search_blob(note.path),
                alias_blob,
                tag_blob,
                section_id,
            ),
        )

    for link in note.links:
        con.execute(
            """
            INSERT INTO links(
                source_note_id, source_section_key, target_raw, target_note_raw, target_note_norm,
                target_heading_raw, target_heading_norm, resolved_note_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                note_id,
                link.source_section_key,
                link.target_raw,
                link.target_note_raw,
                link.target_note_norm,
                link.target_heading_raw,
                link.target_heading_norm,
            ),
        )


def _delete_note_children(con: sqlite3.Connection, note_id: int) -> None:
    con.execute(
        "DELETE FROM section_fts WHERE section_id IN (SELECT id FROM sections WHERE note_id = ?)",
        (note_id,),
    )
    con.execute("DELETE FROM note_aliases WHERE note_id = ?", (note_id,))
    con.execute("DELETE FROM note_tags WHERE note_id = ?", (note_id,))
    con.execute("DELETE FROM links WHERE source_note_id = ?", (note_id,))
    con.execute("DELETE FROM sections WHERE note_id = ?", (note_id,))


def _resolution_maps(con: sqlite3.Connection) -> tuple[dict[str, int], dict[str, int]]:
    note_id_by_path_key: dict[str, int] = {}
    title_candidates: defaultdict[str, list[int]] = defaultdict(list)
    for row in con.execute("SELECT id, path_key, title_norm FROM notes"):
        note_id = int(row["id"])
        note_id_by_path_key[str(row["path_key"])] = note_id
        title_candidates[str(row["title_norm"])].append(note_id)
    unique_title_map = {key: ids[0] for key, ids in title_candidates.items() if len(ids) == 1}
    return note_id_by_path_key, unique_title_map


def _resolve_all_links(con: sqlite3.Connection) -> None:
    note_id_by_path_key, unique_title_map = _resolution_maps(con)
    con.execute("UPDATE links SET resolved_note_id = NULL")
    rows = con.execute("SELECT id, target_note_norm FROM links WHERE target_note_norm IS NOT NULL").fetchall()
    for row in rows:
        resolved_note_id = resolve_note_target(
            target_note_norm=str(row["target_note_norm"]),
            note_id_by_path_key=note_id_by_path_key,
            unique_title_map=unique_title_map,
        )
        if resolved_note_id is not None:
            con.execute("UPDATE links SET resolved_note_id = ? WHERE id = ?", (resolved_note_id, row["id"]))


def _remove_sqlite_sidecars(db_path: Path) -> None:
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(db_path) + suffix)
        if sidecar.exists():
            sidecar.unlink()


def _prepare_existing_db_for_replacement(db_path: Path) -> None:
    if not db_path.exists():
        _remove_sqlite_sidecars(db_path)
        return
    sidecars_present = any(Path(str(db_path) + suffix).exists() for suffix in ("-wal", "-shm"))
    if sidecars_present:
        con = sqlite3.connect(db_path)
        try:
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
            con.execute("PRAGMA journal_mode=DELETE").fetchall()
        finally:
            con.close()
    _remove_sqlite_sidecars(db_path)


def rebuild_index(db_path: Path, notes: list[NoteData]) -> dict[str, int]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{db_path.name}.", suffix=".tmp", dir=db_path.parent)
    os.close(descriptor)
    temp_path = Path(temp_name)
    temp_path.unlink()
    indexed_at_utc = _indexed_at_utc()
    note_id_by_path_key: dict[str, int] = {}
    try:
        con = connect(temp_path)
        try:
            with con:
                _set_index_meta(con, indexed_at_utc=indexed_at_utc, note_count=len(notes))
                for note in notes:
                    note_id = _insert_note_record(con, note=note, indexed_at_utc=indexed_at_utc)
                    note_id_by_path_key[note_key_from_relative_path(note.path)] = note_id
                _resolve_all_links(con)
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
            con.execute("PRAGMA journal_mode=DELETE").fetchall()
        finally:
            con.close()
        _prepare_existing_db_for_replacement(db_path)
        os.replace(temp_path, db_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()
        _remove_sqlite_sidecars(temp_path)
    return note_id_by_path_key


def schema_version(db_path: Path) -> int | None:
    if not db_path.exists():
        return None
    try:
        con = connect(db_path, read_only=True)
        try:
            row = con.execute(
                "SELECT value FROM index_meta WHERE key = 'schema_version'"
            ).fetchone()
            return int(row["value"]) if row is not None else None
        finally:
            con.close()
    except (sqlite3.DatabaseError, ValueError):
        return None


def refresh_index(db_path: Path, vault_root: Path, markdown_files: list[Path]) -> dict[str, object]:
    file_by_path = {path.relative_to(vault_root).as_posix(): path for path in markdown_files}
    existing_version = schema_version(db_path)
    if existing_version != CURRENT_SCHEMA_VERSION:
        notes = [parse_markdown_note(vault_root, path) for path in markdown_files]
        rebuild_index(db_path, notes)
        con = connect(db_path, read_only=True)
        try:
            meta_row = con.execute(
                "SELECT value FROM index_meta WHERE key = 'indexed_at_utc'"
            ).fetchone()
            indexed_at_utc = str(meta_row["value"]) if meta_row is not None else ""
        finally:
            con.close()
        return {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "added": len(notes),
            "modified": 0,
            "deleted": 0,
            "unchanged": 0,
            "refreshed": True,
            "rebuilt": True,
            "indexed_at_utc": indexed_at_utc,
            "warnings": _note_warnings(notes),
        }

    con = connect(db_path, read_only=True)
    try:
        existing_rows = con.execute(
            "SELECT path, source_mtime_ns, source_size FROM notes"
        ).fetchall()
        meta_row = con.execute(
            "SELECT value FROM index_meta WHERE key = 'indexed_at_utc'"
        ).fetchone()
    finally:
        con.close()
    existing_by_path = {str(row["path"]): row for row in existing_rows}
    added_paths = sorted(set(file_by_path) - set(existing_by_path))
    deleted_paths = sorted(set(existing_by_path) - set(file_by_path))
    modified_paths: list[str] = []
    unchanged_paths: list[str] = []
    for path in sorted(set(file_by_path) & set(existing_by_path)):
        stat = file_by_path[path].stat()
        row = existing_by_path[path]
        if stat.st_mtime_ns != int(row["source_mtime_ns"]) or stat.st_size != int(row["source_size"]):
            modified_paths.append(path)
        else:
            unchanged_paths.append(path)

    changed_paths = added_paths + modified_paths
    changed_notes = [parse_markdown_note(vault_root, file_by_path[path]) for path in changed_paths]
    changed_by_path = {note.path: note for note in changed_notes}
    if not changed_paths and not deleted_paths:
        return {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "added": 0,
            "modified": 0,
            "deleted": 0,
            "unchanged": len(unchanged_paths),
            "refreshed": False,
            "rebuilt": False,
            "indexed_at_utc": str(meta_row["value"]) if meta_row is not None else "",
            "warnings": [],
        }

    indexed_at_utc = _indexed_at_utc()
    con = connect(db_path)
    try:
        with con:
            for path in deleted_paths:
                con.execute("DELETE FROM notes WHERE path = ?", (path,))
            for path in modified_paths:
                row = con.execute("SELECT id FROM notes WHERE path = ?", (path,)).fetchone()
                if row is None:
                    raise RuntimeError(f"indexed note disappeared during refresh: {path}")
                note_id = int(row["id"])
                note = changed_by_path[path]
                _delete_note_children(con, note_id)
                con.execute(
                    """
                    UPDATE notes
                    SET path_key = ?, title = ?, title_norm = ?, source_mtime_ns = ?,
                        source_size = ?, indexed_at_utc = ?
                    WHERE id = ?
                    """,
                    (
                        note_key_from_relative_path(note.path),
                        note.title,
                        note.title_norm,
                        note.source_mtime_ns,
                        note.source_size,
                        indexed_at_utc,
                        note_id,
                    ),
                )
                _insert_note_children(con, note_id=note_id, note=note)
            for path in added_paths:
                _insert_note_record(con, note=changed_by_path[path], indexed_at_utc=indexed_at_utc)
            _resolve_all_links(con)
            note_count = int(con.execute("SELECT COUNT(*) AS count FROM notes").fetchone()["count"])
            _set_index_meta(con, indexed_at_utc=indexed_at_utc, note_count=note_count)
    finally:
        con.close()
    return {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "added": len(added_paths),
        "modified": len(modified_paths),
        "deleted": len(deleted_paths),
        "unchanged": len(unchanged_paths),
        "refreshed": True,
        "rebuilt": False,
        "indexed_at_utc": indexed_at_utc,
        "warnings": _note_warnings(changed_notes),
    }


def _note_warnings(notes: list[NoteData]) -> list[str]:
    return [f"{note.path}: {warning}" for note in notes for warning in note.warnings]


def resolve_note_target(
    target_note_norm: str | None,
    note_id_by_path_key: dict[str, int],
    unique_title_map: dict[str, int],
) -> int | None:
    if not target_note_norm:
        return None
    if target_note_norm in note_id_by_path_key:
        return note_id_by_path_key[target_note_norm]
    return unique_title_map.get(target_note_norm)


def resolve_note_spec(con: sqlite3.Connection, spec: str) -> sqlite3.Row:
    norm = normalize_note_key(spec)
    rows_with_priority: list[tuple[int, sqlite3.Row]] = []
    rows = con.execute(
        """
        SELECT *
        FROM notes
        WHERE path = ?
           OR path_key = ?
           OR title_norm = ?
        ORDER BY CASE
            WHEN path = ? THEN 0
            WHEN path_key = ? THEN 1
            ELSE 2
        END
        """,
        (spec, norm, norm, spec, norm),
    ).fetchall()
    for row in rows:
        if row["path"] == spec:
            priority = 0
        elif row["path_key"] == norm:
            priority = 1
        else:
            priority = 2
        rows_with_priority.append((priority, row))
    alias_rows = con.execute(
        """
        SELECT n.*
        FROM note_aliases a
        JOIN notes n ON n.id = a.note_id
        WHERE a.alias_norm = ?
        ORDER BY n.path COLLATE NOCASE
        """,
        (norm,),
    ).fetchall()
    rows_with_priority.extend((3, row) for row in alias_rows)
    if not rows_with_priority:
        raise ValueError(f"Could not resolve note spec: {spec}")
    best_priority = min(item[0] for item in rows_with_priority)
    best_by_id = {int(row["id"]): row for priority, row in rows_with_priority if priority == best_priority}
    best_rows = list(best_by_id.values())
    if len(best_rows) > 1:
        titles = ", ".join(row["path"] for row in best_rows[:5])
        raise ValueError(f"Ambiguous note spec: {spec} -> {titles}")
    return best_rows[0]


def collect_graph_neighborhood(con: sqlite3.Connection, note_id: int, depth: int = 2) -> dict[int, tuple[int, str]]:
    direct: dict[int, tuple[int, str]] = {}

    outgoing = con.execute(
        """
        SELECT DISTINCT resolved_note_id AS note_id
        FROM links
        WHERE source_note_id = ? AND resolved_note_id IS NOT NULL
        """,
        (note_id,),
    ).fetchall()
    incoming = con.execute(
        """
        SELECT DISTINCT source_note_id AS note_id
        FROM links
        WHERE resolved_note_id = ?
        """,
        (note_id,),
    ).fetchall()

    for row in outgoing:
        target_id = row["note_id"]
        if target_id != note_id:
            direct[target_id] = (1, "direct outlink")
    for row in incoming:
        target_id = row["note_id"]
        if target_id != note_id and target_id not in direct:
            direct[target_id] = (1, "direct backlink")

    if depth <= 1:
        return direct

    neighborhood = dict(direct)
    seed_ids = list(direct.keys())
    for seed_id in seed_ids:
        second_hop_rows = con.execute(
            """
            SELECT DISTINCT candidate_id FROM (
                SELECT resolved_note_id AS candidate_id
                FROM links
                WHERE source_note_id = ? AND resolved_note_id IS NOT NULL
                UNION
                SELECT source_note_id AS candidate_id
                FROM links
                WHERE resolved_note_id = ?
            )
            WHERE candidate_id IS NOT NULL
            """,
            (seed_id, seed_id),
        ).fetchall()
        for row in second_hop_rows:
            candidate_id = row["candidate_id"]
            if candidate_id in (note_id, seed_id):
                continue
            neighborhood.setdefault(candidate_id, (2, "two-hop link"))
    return neighborhood


def query_sections(
    con: sqlite3.Connection,
    query: str,
    limit: int = 8,
    anchor_note_id: int | None = None,
) -> list[QueryResult]:
    if limit < 0:
        raise ValueError("limit must be non-negative")
    if limit == 0:
        return []
    match_query = build_match_query(query)
    lexical_rows = con.execute(
        """
        SELECT
            s.id AS section_id,
            s.note_id,
            n.title,
            n.path,
            s.heading_path,
            s.snippet,
            s.heading,
            n.path_key,
            n.title_norm,
            s.section_key,
            bm25(section_fts, 1.0, 3.0, 2.0, 0.5, 2.5, 1.5, 0.0) AS bm25_score
        FROM section_fts
        JOIN sections s ON s.id = CAST(section_fts.section_id AS INTEGER)
        JOIN notes n ON n.id = s.note_id
        WHERE section_fts MATCH ?
        ORDER BY bm25_score ASC, n.path COLLATE NOCASE ASC, s.section_key ASC
        LIMIT 80
        """,
        (match_query,),
    ).fetchall()

    anchor_graph: dict[int, tuple[int, str]] = {}
    if anchor_note_id is not None:
        anchor_graph = collect_graph_neighborhood(con, anchor_note_id, depth=2)

    candidate_note_ids = sorted({int(row["note_id"]) for row in lexical_rows})
    aliases_by_note: defaultdict[int, list[tuple[str, str]]] = defaultdict(list)
    tags_by_note: defaultdict[int, list[tuple[str, str]]] = defaultdict(list)
    if candidate_note_ids:
        placeholders = ",".join("?" for _ in candidate_note_ids)
        for row in con.execute(
            f"SELECT note_id, alias, alias_norm FROM note_aliases WHERE note_id IN ({placeholders})",
            candidate_note_ids,
        ):
            aliases_by_note[int(row["note_id"])].append((str(row["alias"]), str(row["alias_norm"])))
        for row in con.execute(
            f"SELECT note_id, tag, tag_norm FROM note_tags WHERE note_id IN ({placeholders})",
            candidate_note_ids,
        ):
            tags_by_note[int(row["note_id"])].append((str(row["tag"]), str(row["tag_norm"])))

    query_norm = normalize_text(query)
    query_key = normalize_note_key(query)
    query_tokens = set(tokenize_search_text(query))
    results: list[QueryResult] = []
    seen_sections: set[int] = set()

    for lexical_rank, row in enumerate(lexical_rows, start=1):
        section_id = row["section_id"]
        if section_id in seen_sections:
            continue
        seen_sections.add(section_id)
        bm25_score = float(row["bm25_score"])
        lexical_points = 81 - lexical_rank
        score = float(lexical_points)
        reasons = [f"lexical rank +{lexical_points} (bm25={bm25_score:.6f})"]

        note_id = int(row["note_id"])
        title_norm = str(row["title_norm"])
        path_key = str(row["path_key"])
        heading_norm = normalize_text(row["heading_path"])
        aliases = aliases_by_note[note_id]
        tags = tags_by_note[note_id]
        alias_norms = {item[1] for item in aliases}
        tag_norms = {item[1] for item in tags}
        title_tokens = set(tokenize_search_text(str(row["title"])))
        alias_tokens = set(tokenize_search_text(" ".join(item[0] for item in aliases)))
        tag_tokens = set(tokenize_search_text(" ".join(item[0] for item in tags)))

        if query_key and query_key in {title_norm, path_key}:
            score += 100.0
            reasons.append("title/path exact +100")
        elif query_key and query_key in title_norm:
            score += 35.0
            reasons.append("title contains +35")
        elif query_tokens & title_tokens:
            reasons.append("title lexical match (included in lexical rank)")

        if query_key and query_key in alias_norms:
            score += 80.0
            reasons.append("alias exact +80")
        elif query_tokens & alias_tokens:
            reasons.append("alias lexical match (included in lexical rank)")

        if query_key and query_key.lstrip("#") in tag_norms:
            reasons.append("tag exact match (included in lexical rank)")
        elif query_tokens & tag_tokens:
            reasons.append("tag lexical match (included in lexical rank)")

        if query_norm and heading_norm and query_norm in heading_norm:
            score += 20.0
            reasons.append("heading contains +20")

        if anchor_note_id is not None and note_id == anchor_note_id:
            score += 30.0
            reasons.append("graph anchor +30")
        elif note_id in anchor_graph:
            depth, label = anchor_graph[note_id]
            if depth == 1:
                score += 18.0
                reasons.append(f"graph {label} +18")
            else:
                score += 8.0
                reasons.append(f"graph {label} +8")

        results.append(
            QueryResult(
                score=score,
                note_id=note_id,
                title=row["title"],
                path=row["path"],
                heading_path=row["heading_path"],
                snippet=row["snippet"],
                reasons=reasons,
            )
        )

    results.sort(
        key=lambda item: (
            -item.score,
            item.path.casefold(),
            item.heading_path.casefold(),
            item.note_id,
        )
    )
    return results[:limit]


def note_sections(con: sqlite3.Connection, note_id: int) -> list[sqlite3.Row]:
    return con.execute(
        """
        SELECT section_key, heading, heading_path, level, snippet, content
        FROM sections
        WHERE note_id = ?
        ORDER BY section_key
        """,
        (note_id,),
    ).fetchall()


def note_links(con: sqlite3.Connection, note_id: int, depth: int = 1) -> list[dict[str, object]]:
    neighborhood = collect_graph_neighborhood(con, note_id, depth=depth)
    if not neighborhood:
        return []

    rows = con.execute(
        f"""
        SELECT id, title, path
        FROM notes
        WHERE id IN ({",".join("?" for _ in neighborhood)})
        """,
        tuple(neighborhood.keys()),
    ).fetchall()
    by_id = {row["id"]: row for row in rows}
    results: list[dict[str, object]] = []
    ordered_neighbors = sorted(
        neighborhood.items(),
        key=lambda item: (item[1][0], by_id.get(item[0], {"title": ""})["title"]),
    )
    for note_id_key, (depth_value, reason) in ordered_neighbors:
        note = by_id.get(note_id_key)
        if not note:
            continue
        results.append(
            {
                "note_id": note_id_key,
                "title": note["title"],
                "path": note["path"],
                "depth": depth_value,
                "reason": reason,
            }
        )
    return results


def query_results_to_json(results: list[QueryResult]) -> str:
    payload = [
        {
            "score": round(item.score, 3),
            "title": item.title,
            "path": item.path,
            "heading_path": item.heading_path,
            "snippet": item.snippet,
            "reasons": item.reasons,
        }
        for item in results
    ]
    return json.dumps(payload, ensure_ascii=False, indent=2)
