"""Need discovery: find the real question before answering it.

A requester rarely knows how to state what they are stuck on. Asking them to
"state the problem more precisely" hands the hardest part of the work back to
the person least equipped to do it. This module runs on every new request,
before any answer is attempted, and it keeps the decisions in code:

  * the model classifies the request with closed values (kind, stuck point,
    clarity, depth) and proposes **at most three** short readings, each
    anchored by a verbatim fragment of the requester's own words;
  * this Skill decides whether the ambiguity is *material* (the readings would
    send the work in different directions) and whether investigation of the
    requester's own material could settle it;
  * only when the ambiguity is material **and** investigation cannot settle it
    does this Skill ask — once, as a closed choice with a preselected
    default, answerable with one keystroke;
  * otherwise it proceeds on the leading reading and says so, so the requester
    can redirect without having been interrupted.

Factual uncertainty is settled by investigation or an honest "unknown", never
by putting it to a vote. Value and direction choices stay with the requester.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Mapping

from . import constants as C
from .model import ModelError, digest, new_id, utc_now

SCHEMA_FRAME = "th-need-frame/v1"
SCHEMA_FRAME_PACKET = "th-frame-packet/v1"

STATUS_OPEN = "open"
STATUS_AWAITING_CHOICE = "awaiting_choice"
STATUS_RESOLVED = "resolved"
STATUS_ANSWERED = "answered"

DECISION_PROCEED = "proceed"
DECISION_INVESTIGATE_FIRST = "investigate_then_reframe"
DECISION_ASK = "ask_one_choice"

URL_RE = re.compile(r"https?://", re.I)


def _norm(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r"[\s\W_]+", "", folded)


def new_frame(brief: str, *, context_quote: str | None = None) -> dict[str, Any]:
    if not isinstance(brief, str) or not brief.strip():
        raise ModelError(f"{C.ERR_INPUT_REJECTED}: a request is required")
    return {
        "schema": SCHEMA_FRAME,
        "frame_id": new_id("NF"),
        "brief": brief,
        "status": STATUS_OPEN,
        "kind": None,
        "stuck_point": None,
        "clarity": None,
        "depth": "standard",
        "interpretations": [],
        "chosen": None,
        "defaults": [],
        "material_ambiguity": False,
        "investigation_can_resolve": False,
        "clarifications_asked": 0,
        "reframes": 0,
        "decision": None,
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }


def packet(frame: Mapping[str, Any], *, recent: list[dict[str, Any]] | None = None,
           library_hits: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """What the model may see when framing: the request, closed vocabularies,
    and bounded pointers to what the requester asked recently."""

    return {
        "schema": SCHEMA_FRAME_PACKET,
        "brief": frame["brief"],
        "request_kinds": list(C.REQUEST_KINDS),
        "stuck_points": list(C.STUCK_POINTS),
        "clarity_levels": list(C.CLARITY_LEVELS),
        "differs_by": list(C.DIFFERS_BY),
        "depths": list(C.DEPTHS),
        "defaults": list(C.NEED_DEFAULTS),
        "recent_requests": recent or [],
        "library_hits": library_hits or [],
        "limits": {
            "interpretations": C.MAX_INTERPRETATIONS,
            "interpretation_chars": C.MAX_INTERPRETATION_CHARS,
        },
        "instruction": (
            "Classify with the closed values. Propose at most three readings of what the requester "
            "actually needs, each at most 80 characters, each anchored by a verbatim fragment of the "
            "brief in `quote`. Set material_ambiguity only if the readings would lead to different work."
        ),
    }


def validate_frame_draft(draft: Any, *, brief: str) -> dict[str, Any]:
    """Everything here is decidable without a model."""

    if not isinstance(draft, dict):
        raise ModelError(f"{C.RISK_SCHEMA}: frame draft must be an object")
    unknown = set(draft) - set(C.FRAME_KEYS)
    missing = [key for key in C.FRAME_KEYS if key not in draft]
    if unknown or missing:
        raise ModelError(f"{C.RISK_SCHEMA}: frame keys mismatch (unknown={sorted(unknown)}, missing={missing})")
    for key, allowed in (
        ("kind", C.REQUEST_KINDS),
        ("stuck_point", C.STUCK_POINTS),
        ("clarity", C.CLARITY_LEVELS),
        ("depth", C.DEPTHS),
    ):
        if draft[key] not in allowed:
            raise ModelError(f"{C.RISK_SCHEMA}: {key} {draft[key]!r} is not declared")
    for key in ("material_ambiguity", "investigation_can_resolve"):
        if not isinstance(draft[key], bool):
            raise ModelError(f"{C.RISK_SCHEMA}: {key} must be a boolean")
    defaults = draft["defaults"]
    if not isinstance(defaults, list) or len(defaults) > 4 or any(code not in C.NEED_DEFAULTS for code in defaults):
        raise ModelError(f"{C.RISK_SCHEMA}: defaults must be at most four declared codes")

    readings = draft["interpretations"]
    if not isinstance(readings, list) or not 1 <= len(readings) <= C.MAX_INTERPRETATIONS:
        raise ModelError(f"interpretation_invalid: between 1 and {C.MAX_INTERPRETATIONS} readings are required")
    seen: set[str] = set()
    for reading in readings:
        if not isinstance(reading, dict) or set(reading) != set(C.INTERPRETATION_KEYS):
            raise ModelError(f"interpretation_invalid: reading keys must be {list(C.INTERPRETATION_KEYS)}")
        text = reading["text"]
        if not isinstance(text, str) or not text.strip() or len(text) > C.MAX_INTERPRETATION_CHARS:
            raise ModelError(f"interpretation_invalid: reading text must be 1..{C.MAX_INTERPRETATION_CHARS} characters")
        if URL_RE.search(text) or "\n" in text:
            raise ModelError("interpretation_invalid: a reading is one line without links")
        quote = reading["quote"]
        if not isinstance(quote, str) or not quote.strip() or quote not in brief:
            # A reading must be anchored in what the requester actually said.
            raise ModelError("quote_unverified: a reading must quote the request verbatim")
        if reading["differs_by"] not in C.DIFFERS_BY:
            raise ModelError(f"interpretation_invalid: differs_by {reading['differs_by']!r} is not declared")
        key = _norm(text)
        if key in seen:
            raise ModelError("duplicate_points: two readings say the same thing")
        seen.add(key)
    if len(readings) > 1 and draft["clarity"] == "clear":
        # "Clear" with several different readings is incoherent; this Skill
        # does not guess which half of the draft to believe.
        raise ModelError("interpretation_invalid: a clear request has exactly one reading")
    return draft


def decide(frame: Mapping[str, Any], draft: Mapping[str, Any]) -> str:
    """The rule that keeps human decisions to a minimum.

    Ask only when all hold: more than one reading, the ambiguity is material,
    investigation cannot settle it (or was already tried), and the frame has not
    already used its one clarification.
    """

    readings = draft["interpretations"]
    if len(readings) <= 1 or not draft["material_ambiguity"]:
        return DECISION_PROCEED
    if draft["investigation_can_resolve"] and int(frame.get("reframes", 0)) < C.MAX_REFRAMES:
        return DECISION_INVESTIGATE_FIRST
    if int(frame.get("clarifications_asked", 0)) >= C.MAX_CLARIFICATIONS_PER_FRAME:
        return DECISION_PROCEED
    return DECISION_ASK


def apply_draft(frame: Mapping[str, Any], draft: Mapping[str, Any], decision: str) -> dict[str, Any]:
    updated = dict(frame)
    updated.update({
        "kind": draft["kind"],
        "stuck_point": draft["stuck_point"],
        "clarity": draft["clarity"],
        "depth": draft["depth"],
        "interpretations": [
            {"id": index + 1, "text": reading["text"], "quote": reading["quote"], "differs_by": reading["differs_by"]}
            for index, reading in enumerate(draft["interpretations"])
        ],
        "defaults": sorted(set(draft["defaults"])),
        "material_ambiguity": draft["material_ambiguity"],
        "investigation_can_resolve": draft["investigation_can_resolve"],
        "decision": decision,
        "updated_at": utc_now(),
    })
    if decision == DECISION_ASK:
        updated["status"] = STATUS_AWAITING_CHOICE
        updated["clarifications_asked"] = int(frame.get("clarifications_asked", 0)) + 1
        updated["chosen"] = None
    elif decision == DECISION_INVESTIGATE_FIRST:
        updated["status"] = STATUS_OPEN
        updated["reframes"] = int(frame.get("reframes", 0)) + 1
    else:
        updated["status"] = STATUS_RESOLVED
        updated["chosen"] = 1
    return updated


CHOICE_RE = re.compile(r"^\s*([1-9])\s*$")


def parse_choice(frame: Mapping[str, Any], text: str) -> int | None:
    """A reply to the one clarification. A digit picks a reading; an empty
    reply takes the preselected default; anything else is new information."""

    if text.strip() == "":
        return 1
    match = CHOICE_RE.match(text)
    if match:
        number = int(match.group(1))
        if 1 <= number <= len(frame.get("interpretations", [])):
            return number
    return None


def choose(frame: Mapping[str, Any], number: int) -> dict[str, Any]:
    updated = dict(frame)
    updated["chosen"] = number
    updated["status"] = STATUS_RESOLVED
    updated["updated_at"] = utc_now()
    return updated


def extend(frame: Mapping[str, Any], text: str) -> dict[str, Any]:
    """New information from the requester re-opens framing with a longer brief."""

    updated = dict(frame)
    updated["brief"] = (frame["brief"].rstrip() + "\n" + text.strip()).strip()
    updated["status"] = STATUS_OPEN
    updated["updated_at"] = utc_now()
    return updated


def chosen_reading(frame: Mapping[str, Any]) -> dict[str, Any] | None:
    number = frame.get("chosen")
    for reading in frame.get("interpretations", []):
        if reading["id"] == number:
            return reading
    return None


def summary(frame: Mapping[str, Any]) -> dict[str, Any]:
    """The protected part of every later packet: what is being answered."""

    reading = chosen_reading(frame)
    return {
        "frame_id": frame["frame_id"],
        "brief_sha256": digest(frame["brief"]),
        "kind": frame.get("kind"),
        "stuck_point": frame.get("stuck_point"),
        "depth": frame.get("depth"),
        "reading": reading["text"] if reading else None,
        "reading_quote": reading["quote"] if reading else None,
        "defaults": list(frame.get("defaults", [])),
    }
