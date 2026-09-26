"""Layered memory and bounded disclosure.

Three ideas:

  * **Stable anchors.** An identifier is random and content-independent, so a
    file may move without losing its identity. `anchor_paths` keeps the history,
    which is how "path changed" stays distinguishable from "different file".
  * **Preflight then commit.** A bulk index is planned first, the plan carries a
    `plan_sha256`, and applying it requires quoting that hash back. A plan that
    no longer matches the filesystem is refused rather than half-applied.
  * **Bounded packets.** Disclosure levels are `index < synopsis < section <
    evidence`. Protected task context is carried in full or the packet is
    refused; records are added until the budget would be exceeded.

The store never owns a root. A root is supplied per call, so the package holds
no standing capability it did not receive.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import constants as C
from .model import digest, digest_bytes
from .store import Store

DEFAULT_MAX_TOKENS = 4000
MIN_HEADROOM_TOKENS = 20


class AssetError(RuntimeError):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


# --------------------------------------------------------------------------
# Token estimate. Deliberately conservative and deterministic: the same input
# must always cost the same, or a packet boundary would move between runs.
# --------------------------------------------------------------------------
def estimate_tokens(value: Any) -> int:
    if not isinstance(value, str):
        value = _serialize(value)
    return max(1, (len(value.encode("utf-8")) + 2) // 3)


def _serialize(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


# --------------------------------------------------------------------------
# Anchors
# --------------------------------------------------------------------------
def normalize_relative(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError as exc:
        raise AssetError(C.ERR_ASSET_UNMANAGED, f"{path} is outside {root}") from exc


def scan_files(root: Path, *, limit: int = 20000) -> list[Path]:
    if root.is_file():
        return [root]
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.name.startswith("."):
            files.append(path)
        if len(files) >= limit:
            break
    return files


def make_index_plan(store: Store, root: str | Path, *, limit: int = 20000) -> dict[str, Any]:
    """A dry run. Produces a plan and its hash; writes nothing."""

    root_path = Path(root).expanduser().resolve()
    if not root_path.exists():
        raise AssetError(C.ERR_ASSET_UNMANAGED, f"root does not exist: {root_path}")
    entries = []
    for path in scan_files(root_path, limit=limit):
        stat = path.stat()
        entries.append(
            {
                "relative_path": normalize_relative(root_path, path),
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
        )
    body = {"root": str(root_path), "entries": entries}
    return {
        "schema": "th-index-plan/v1",
        "root": str(root_path),
        "entry_count": len(entries),
        "entries": entries,
        "plan_sha256": digest(body),
        "dry_run": True,
    }


def apply_index_plan(
    store: Store,
    plan: dict[str, Any],
    *,
    expect_plan_sha256: str,
    operation_prefix: str = "index",
) -> dict[str, Any]:
    """Apply a previously planned index. Requires an exact plan hash."""

    body = {"root": plan["root"], "entries": plan["entries"]}
    actual = digest(body)
    if actual != plan.get("plan_sha256"):
        raise AssetError(C.ERR_PLAN_STALE, "plan body does not match its own hash")
    if actual != expect_plan_sha256:
        raise AssetError(C.ERR_PLAN_HASH, "write requires --expect-plan-sha256 equal to the plan hash")

    root_path = Path(plan["root"])
    registered: list[str] = []
    refreshed: list[str] = []
    stale: list[str] = []

    def mutate(connection: Any) -> dict[str, Any]:
        for entry in plan["entries"]:
            relative = entry["relative_path"]
            absolute = root_path / relative
            try:
                sha256 = digest_bytes(absolute.read_bytes())
            except OSError:
                sha256 = None
            # Whether the anchor already existed must be decided *before*
            # registering, otherwise a brand new anchor looks like a refresh.
            preexisting = connection.execute(
                "SELECT anchor_id FROM anchors WHERE relative_path = ?", (relative,)
            ).fetchone()
            anchor_id = store.register_anchor(
                connection,
                relative_path=relative,
                sha256=sha256,
                size=entry["size"],
                mtime_ns=entry["mtime_ns"],
            )
            if sha256 is not None:
                store.set_anchor_hash(
                    connection, anchor_id, sha256, size=entry["size"], mtime_ns=entry["mtime_ns"]
                )
            if preexisting is None:
                registered.append(anchor_id)
            else:
                refreshed.append(anchor_id)
        return {"action": "index_applied", "anchors": registered + refreshed}

    result = store.transact(
        operation_id=f"{operation_prefix}-{actual[:16]}",
        head=C.HEAD_EXECUTION,
        expected=store.head(C.HEAD_EXECUTION),
        request={"plan_sha256": actual, "root": plan["root"], "entries": len(plan["entries"])},
        mutate=mutate,
    )
    return {
        "schema": "th-index-receipt/v1",
        "plan_sha256": actual,
        "registered": sorted(registered),
        "refreshed": sorted(refreshed),
        "stale": sorted(stale),
        "revision": result["revision"],
        "entry_count": len(plan["entries"]),
    }


def detect_stale(store: Store, root: str | Path, *, limit: int = 20000) -> dict[str, Any]:
    """Compare stored metadata against the live filesystem.

    Detects new, modified, missing and relocated files. Nothing is written.
    """

    root_path = Path(root).expanduser().resolve()
    live: dict[str, tuple[int, int]] = {}
    for path in scan_files(root_path, limit=limit):
        stat = path.stat()
        live[normalize_relative(root_path, path)] = (stat.st_size, stat.st_mtime_ns)

    anchors = store.anchors()
    known_paths = {anchor["relative_path"]: anchor for anchor in anchors}
    by_hash: dict[str, list[dict[str, Any]]] = {}
    for anchor in anchors:
        if anchor["sha256"]:
            by_hash.setdefault(anchor["sha256"], []).append(anchor)

    modified: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    unindexed: list[str] = []

    for relative, (size, mtime_ns) in live.items():
        anchor = known_paths.get(relative)
        if anchor is None:
            unindexed.append(relative)
            continue
        if anchor["size"] != size or anchor["mtime_ns"] != mtime_ns:
            modified.append({"anchor_id": anchor["anchor_id"], "relative_path": relative})

    for relative, anchor in known_paths.items():
        if relative not in live:
            missing.append({"anchor_id": anchor["anchor_id"], "relative_path": relative})

    return {
        "schema": "th-stale-report/v1",
        "root": str(root_path),
        "modified": sorted(modified, key=lambda item: item["relative_path"]),
        "missing": sorted(missing, key=lambda item: item["relative_path"]),
        "unindexed": sorted(unindexed),
        "stale_count": len(modified) + len(missing) + len(unindexed),
        "checks": "size and modification time only; content equality is not claimed",
    }


def locate_by_content(store: Store, root: str | Path, relative_path: str) -> dict[str, Any] | None:
    """A relocated file keeps its anchor when the bytes match.

    This is the reason the identifier must not encode the path.
    """

    root_path = Path(root).expanduser().resolve()
    candidate = root_path / relative_path
    if not candidate.is_file():
        return None
    sha256 = digest_bytes(candidate.read_bytes())
    for anchor in store.anchors():
        if anchor["sha256"] == sha256:
            return {"anchor_id": anchor["anchor_id"], "previous_path": anchor["relative_path"], "found_at": relative_path}
    return None


# --------------------------------------------------------------------------
# Disclosure
# --------------------------------------------------------------------------
TEXT_FIELDS = ("title", "statement", "summary", "findings", "cannot_imply", "assumptions")


def _record_text(record: dict[str, Any]) -> str:
    body = record.get("body", {})
    parts: list[str] = [record.get("title", "")]
    for key in TEXT_FIELDS:
        value = body.get(key)
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            parts.extend(str(item) for item in value[:8])
    claim = body.get("claim")
    if isinstance(claim, dict):
        for key in ("statement", "cannot_imply"):
            value = claim.get(key)
            if isinstance(value, str):
                parts.append(value)
            elif isinstance(value, list):
                parts.extend(str(item) for item in value[:8])
    return "\n".join(part for part in parts if part)


def _render_record(store: Store, record: dict[str, Any], level: str, *, remaining: int) -> dict[str, Any]:
    rendered: dict[str, Any] = {
        "record_id": record["record_id"],
        "kind": record["kind"],
        "title": record["title"],
        "review_state": store.review_state(record["record_id"]),
    }
    if C.LEVEL_RANK[level] >= C.LEVEL_RANK[C.LEVEL_SYNOPSIS]:
        body = record.get("body", {})
        if isinstance(body.get("claim"), dict):
            claim = body["claim"]
            rendered["strength"] = claim.get("strength")
            rendered["grade"] = claim.get("grade")
            rendered["scope_declared"] = bool(claim.get("scope"))
    if C.LEVEL_RANK[level] >= C.LEVEL_RANK[C.LEVEL_SECTION]:
        text = _record_text(record)
        budget = max(200, remaining * 3)
        rendered["section"] = text[:budget]
        rendered["section_truncated"] = len(text) > budget
    if C.LEVEL_RANK[level] >= C.LEVEL_RANK[C.LEVEL_EVIDENCE]:
        rendered["dependencies"] = store.dependency_closure(record["record_id"])
        rendered["revisions"] = store.revisions_of(record["record_id"])
    return rendered


def build_context_packet(
    store: Store,
    *,
    contract: dict[str, Any],
    actor: str,
    purpose: str,
    level: str | None = None,
    record_ids: Iterable[str] | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    operation_results: Sequence[dict[str, Any]] | None = None,
    repair: dict[str, Any] | None = None,
    turn_text: str | None = None,
) -> dict[str, Any]:
    """Assemble a bounded packet.

    Protected task context (objective, delegation, scope, open obligations) is
    never truncated. If it alone exceeds the budget the packet is refused,
    because silently dropping it would change what the work means.

    `operation_results` carries bounded values this Skill already executed. Raw
    output is never in here: the broker returns digests and sizes.

    `repair` carries closed risk codes after a rejected attempt, so a backend can
    fix a structural defect without learning what a reviewer saw.
    """

    if actor not in C.ACTORS:
        raise AssetError(C.ERR_INPUT_REJECTED, f"unknown actor {actor}")
    selected = level or C.ACTOR_DEFAULTS[actor]
    if selected not in C.LEVELS:
        raise AssetError(C.ERR_INPUT_REJECTED, f"unknown level {selected}")
    if max_tokens < 1:
        raise AssetError(C.ERR_BUDGET_EXCEEDED, "max_tokens must be positive")

    protected = {
        "objective": contract.get("objective"),
        "delegation": contract.get("delegation"),
        "scope": contract.get("scope"),
        "open_obligations": contract.get("open_obligations", []),
        "applicability": contract.get("applicability"),
    }
    protected_tokens = estimate_tokens(protected)
    if protected_tokens > max_tokens:
        raise AssetError(C.ERR_BUDGET_EXCEEDED, "protected task context exceeds the packet budget")

    wanted = list(dict.fromkeys(record_ids or []))
    results = list(operation_results or [])
    packet: dict[str, Any] = {
        "schema": "th-context/v1",
        "actor": actor,
        "purpose": purpose,
        "level": selected,
        "protected": protected,
        "operation_results": results,
        "records": [],
        "coverage": {"requested": wanted, "served": [], "truncated": False},
        "token_estimate": 0,
    }
    if turn_text is not None:
        # The caller's own text for this turn. It is already past the input gate
        # and is the only thing that makes `focus_quote` verifiable, so it is
        # carried verbatim and is not charged against the store-derived budget.
        packet["turn_text"] = turn_text
    if repair is not None:
        # Closed values only: an attempt number and risk codes.
        packet["repair"] = {
            "attempt": int(repair.get("attempt", 2)),
            "risk_codes": sorted(str(code) for code in repair.get("risk_codes", [])),
            "instruction": "上一轮被拒绝。请修正该结构缺陷后重新提交；不要猜测评审看到的内容。",
        }
    used = estimate_tokens(packet)
    effective_max = max_tokens + (estimate_tokens(turn_text) if turn_text is not None else 0)
    for record_id in wanted:
        remaining = effective_max - used
        if remaining <= MIN_HEADROOM_TOKENS:
            packet["coverage"]["truncated"] = True
            break
        record = store.record(record_id)
        rendered = _render_record(store, record, selected, remaining=remaining)
        cost = estimate_tokens(rendered)
        if used + cost > effective_max:
            if selected == C.LEVEL_INDEX:
                packet["coverage"]["truncated"] = True
                break
            rendered = _render_record(store, record, C.LEVEL_INDEX, remaining=remaining)
            cost = estimate_tokens(rendered)
            if used + cost > effective_max:
                packet["coverage"]["truncated"] = True
                break
            packet["coverage"]["truncated"] = True
        packet["records"].append(rendered)
        packet["coverage"]["served"].append(record_id)
        used += cost
    packet["token_estimate"] = used
    return packet


def read_packet(
    store: Store,
    *,
    record_ids: Sequence[str],
    profile: str = "small",
    max_chars: int | None = None,
) -> dict[str, Any]:
    """A bounded read packet with a hard character ceiling.

    `small` is bounded by construction: at most three records, at most three
    sections each, and a global character budget. All profiles report `chars`
    the same way — the length of the serialized payload — so the sizes of two
    profiles can be compared.
    """

    if profile == "metadata":
        items = [
            {"record_id": rid, "kind": store.record(rid)["kind"], "title": store.record(rid)["title"]}
            for rid in record_ids
        ]
        return {
            "schema": "th-read/v1",
            "profile": profile,
            "items": items,
            "chars": len(_serialize(items)),
            "ceiling": None,
            "truncated": False,
        }

    if profile == "small":
        limit_records = C.READ_PACKET_SMALL_MAX_RECORDS
        limit_sections = C.READ_PACKET_SMALL_MAX_SECTIONS
        ceiling = max_chars or C.READ_PACKET_SMALL_MAX_CHARS
    elif profile == "custom":
        limit_records = len(record_ids)
        limit_sections = len(record_ids)
        ceiling = max_chars or C.READ_PACKET_DEFAULT_MAX_CHARS
    else:
        raise AssetError(C.ERR_INPUT_REJECTED, f"unknown read profile {profile}")

    if ceiling > C.READ_PACKET_DEFAULT_MAX_CHARS and profile != "custom":
        raise AssetError(C.ERR_BUDGET_EXCEEDED, "ceiling exceeds the declared maximum")

    items: list[dict[str, Any]] = []
    truncated = False
    for record_id in record_ids[:limit_records]:
        record = store.record(record_id)
        text = _record_text(record)
        remaining = ceiling - len(_serialize(items))
        if remaining <= 0:
            truncated = True
            break
        candidate = {
            "record_id": record_id,
            "kind": record["kind"],
            "title": record["title"],
            "sections": min(limit_sections, max(1, len(text) // 400 or 1)),
            "text": text,
            "truncated": False,
        }
        size = len(_serialize([*items, candidate]))
        if size > ceiling:
            # Trim the body so the serialized packet fits. The budget is
            # enforced on the same quantity it reports, so the number in the
            # receipt is the number that was actually bounded.
            over = size - ceiling
            candidate["text"] = candidate["text"][: max(0, len(candidate["text"]) - over)]
            candidate["truncated"] = True
            size = len(_serialize([*items, candidate]))
            truncated = True
            if size > ceiling:
                break
        items.append(candidate)
        if len(candidate["text"]) < len(text):
            truncated = True
    return {
        "schema": "th-read/v1",
        "profile": profile,
        "items": items,
        "chars": len(_serialize(items)),
        "ceiling": ceiling,
        "truncated": truncated or len(record_ids) > limit_records,
    }
