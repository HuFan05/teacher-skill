"""Closed research records and conservative, evidence-bound semantic assessment.

An accepted review records the result of a human/agent method, not automatic
truth of a research claim or proof of the reviewer's identity. ``reviews`` contains
ONLY reviews explicitly admitted by the local Store; contribution packages do
not confer trust. Store must reopen report/material bytes or explicitly check a
source-backed summary reference before admission. This module performs no I/O.

``usable`` means usable as a current premise. ``exportable`` also permits
previously accepted research subsequently withdrawn or questioned, provided its
structure is closed and its correction/impact context accompanies delivery.
Review rejection never turns a claim into a refutation: a rejected proof,
correctness argument or experiment does not refute the claim it addressed.
Missing evidence bytes do not imply missing review: summary/source assurance is
separate from replay. Missing record dependencies and circular justification,
however, cannot form a faithfully closed export. Offline packages do not learn
later corrections automatically; the exporter must identify its snapshot.
"""

import hashlib
import json
import math
import re
from collections import defaultdict, deque


class CRSError(ValueError):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


def _fail(code, message, details=None):
    raise CRSError(code, message, details)


def _json_value(value):
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            _fail("json_nonfinite", "JSON numbers must be finite")
        return
    if isinstance(value, list):
        for item in value:
            _json_value(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                _fail("json_key", "JSON object keys must be strings")
            _json_value(item)
        return
    _fail("json_type", "Unsupported JSON value", type(value).__name__)


def canonical(obj):
    try:
        _json_value(obj)
        return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except CRSError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        _fail("json_invalid", "Value cannot be represented as canonical JSON", str(exc))


def digest(data):
    if not isinstance(data, (bytes, bytearray, memoryview)):
        _fail("bytes_required", "Digest input must be bytes")
    return hashlib.sha256(data).hexdigest()


def parse_json(data):
    if not isinstance(data, bytes):
        _fail("bytes_required", "JSON input must be UTF-8 bytes")

    def pairs(items):
        obj = {}
        for key, value in items:
            if key in obj:
                _fail("json_duplicate_key", "Duplicate JSON object key", key)
            obj[key] = value
        return obj

    def constant(value):
        _fail("json_nonfinite", "Non-finite JSON number", value)

    try:
        obj = json.loads(data.decode("utf-8"), object_pairs_hook=pairs,
                         parse_constant=constant)
        canonical(obj)  # Also rejects exponent overflow and lone surrogates.
        return obj
    except CRSError:
        raise
    except (ValueError, UnicodeError, RecursionError) as exc:
        _fail("json_invalid", "Invalid UTF-8 JSON", str(exc))


_SHA = re.compile(r"[0-9a-f]{64}\Z")
_KINDS = {"objective", "attempt", "result", "hypothesis", "observation",
          "failure", "definition", "correction"}
_EPISTEMIC = {"established", "refuted", "hypothesis", "observation", "attempt",
              "failure", "definition", "correction"}
_RELATIONS = {"premise", "input", "term", "background", "extends", "corrects", "supersedes"}
_STRONG = {"premise", "input", "term"}
# ``argument``: a proof or correctness argument was checked (CS theory).
# ``reproduction``: the claimed result was reproduced within its declared scope.
# ``computation``: code, computation or an experiment run was checked.
_COVERAGE = {"record_fidelity", "argument", "reproduction", "computation", "source", "terminology"}
_RECORD_FIELDS = {"schema", "id", "kind", "title", "statement", "scope", "action",
                  "feedback", "epistemic", "assumptions", "limitations", "reopen",
                  "sources", "evidence", "dependencies", "conditional_on", "previous",
                  "correction"}
OBJECTIVE_FIELDS = frozenset({"statement", "domain", "claim_scope", "assumptions",
                              "evidence_standard", "completion_standard"})
RECORD_SCHEMA = "crs-record/v1"
_OBJECTIVE_KNOWN_FIELDS = frozenset({"statement", "domain", "assumptions"})
_REVIEW_FIELDS = {"schema", "id", "target", "decision", "coverage", "method", "scope",
                  "findings", "limitations", "materials", "report", "reviewer",
                  "created_at", "supersedes"}


def _exact(value, fields, where):
    if not isinstance(value, dict):
        _fail("schema_type", where + " must be an object")
    if set(value) != fields:
        _fail("schema_fields", where + " fields do not match the contract",
              {"missing": sorted(fields - set(value)), "extra": sorted(set(value) - fields, key=str)})


def _text(value, where, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()):
        _fail("schema_text", where + " must be " + ("text" if empty else "nonempty text"))


def _sha(value, where, nullable=False):
    if nullable and value is None:
        return
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        _fail("schema_hash", where + " must be a lowercase SHA-256")


def _enum(value, choices, where):
    if not isinstance(value, str) or value not in choices:
        _fail("schema_enum", where + " has an unsupported value")


def _list(value, where):
    if not isinstance(value, list):
        _fail("schema_list", where + " must be an array")


def _strings(value, where, hashes=False, unique=False):
    _list(value, where)
    for item in value:
        (_sha if hashes else _text)(item, where + "[]")
    if unique and len(set(value)) != len(value):
        _fail("schema_duplicate", where + " must not repeat entries")


def objective_complete(record):
    """A complete objective record carries the six-field ``project_objective``."""
    return isinstance(record, dict) and "project_objective" in record


def validate_record(record):
    extended = objective_complete(record)
    _exact(record, _RECORD_FIELDS | ({"project_objective"} if extended else set()), "record")
    if record["schema"] != RECORD_SCHEMA:
        _fail("schema_version", "Unsupported record schema")
    if extended:
        if record["kind"] != "objective":
            _fail("objective_schema_kind", "Only objective records carry project_objective.")
        contract = record["project_objective"]
        _exact(contract, OBJECTIVE_FIELDS, "project_objective")
        for key in OBJECTIVE_FIELDS - {"assumptions"}:
            _text(contract[key], "project_objective." + key)
        _strings(contract["assumptions"], "project_objective.assumptions")
        if (contract["statement"] != record["statement"] or contract["domain"] != record["scope"]
                or contract["assumptions"] != record["assumptions"]):
            _fail("objective_projection_mismatch", "Objective statement, domain and assumptions must match the record's statement, scope and assumptions.")
    _enum(record["kind"], _KINDS, "record.kind")
    _enum(record["epistemic"], _EPISTEMIC, "record.epistemic")
    expected = {"objective": {"hypothesis"}, "result": {"established", "refuted"}}
    allowed = expected.get(record["kind"], {record["kind"]})
    if record["epistemic"] not in allowed:
        _fail("epistemic_kind", "Record kind and epistemic label disagree")
    for key in ("id", "title", "statement", "scope"):
        _text(record[key], "record." + key)
    for key in ("action", "feedback"):
        _text(record[key], "record." + key, record["kind"] in {"objective", "definition"})
    for key in ("assumptions", "limitations", "reopen", "sources"):
        _strings(record[key], "record." + key)
    _strings(record["conditional_on"], "record.conditional_on", hashes=True, unique=True)
    _sha(record["previous"], "record.previous", nullable=True)
    _list(record["evidence"], "record.evidence")
    for item in record["evidence"]:
        _exact(item, {"sha256", "bytes", "name", "role", "summary", "locator"}, "evidence")
        _sha(item["sha256"], "evidence.sha256")
        if type(item["bytes"]) is not int or item["bytes"] < 0:
            _fail("schema_size", "Evidence bytes must be a nonnegative integer")
        for key in ("name", "role"):
            _text(item[key], "evidence." + key)
        for key in ("summary", "locator"):
            _text(item[key], "evidence." + key, empty=True)
    _list(record["dependencies"], "record.dependencies")
    seen = set()
    for item in record["dependencies"]:
        _exact(item, {"id", "revision", "relation", "reason"}, "dependency")
        _text(item["id"], "dependency.id")
        _sha(item["revision"], "dependency.revision")
        _enum(item["relation"], _RELATIONS, "dependency.relation")
        _text(item["reason"], "dependency.reason")
        key = (item["id"], item["revision"], item["relation"])
        if key in seen:
            _fail("schema_duplicate", "Duplicate dependency relation")
        seen.add(key)
    declared = {d["revision"] for d in record["dependencies"] if d["relation"] in _STRONG}
    if not set(record["conditional_on"]) <= declared:
        _fail("conditional_dependency", "Conditional references must be explicit premise/input/term dependencies")
    correction = record["correction"]
    if record["kind"] == "correction":
        _exact(correction, {"target", "effect", "reason", "replacement"}, "correction")
        _sha(correction["target"], "correction.target")
        _sha(correction["replacement"], "correction.replacement", nullable=True)
        _enum(correction["effect"], {"presentation", "invalid", "narrow"}, "correction.effect")
        _text(correction["reason"], "correction.reason")
        if correction["target"] == correction["replacement"]:
            _fail("correction_identity", "A replacement must differ from the corrected revision")
    elif correction is not None:
        _fail("correction_kind", "Only a correction record can carry a correction object")
    canonical(record)



def objective_identity(record):
    """Declared question identity only; completeness is not a research review."""
    validate_record(record)
    if record["kind"] != "objective":
        _fail("objective_required", "Question identity requires an objective record.")
    fields = (record["project_objective"] if objective_complete(record) else
              {"statement": record["statement"], "domain": record["scope"],
               "assumptions": record["assumptions"]})
    return {"completeness": "complete" if set(fields) == OBJECTIVE_FIELDS else "incomplete",
            "identity_sha256": digest(canonical(fields)),
            "known_fields": sorted(fields),
            "missing_fields": sorted(OBJECTIVE_FIELDS - set(fields))}


def objective_compatible(anchor, candidate, binding=None):
    """Compare exact declared constituents; never infer semantic equivalence."""
    for record in (anchor, candidate):
        validate_record(record)
    if anchor["kind"] != "objective" or candidate["kind"] != "objective" or anchor["id"] != candidate["id"]:
        return False
    known = lambda record: (record["statement"], record["scope"], record["assumptions"])
    if known(anchor) != known(candidate):
        return False
    fixed = binding or anchor
    if objective_complete(fixed):
        return (objective_complete(candidate)
                and fixed["project_objective"] == candidate["project_objective"])
    return True


def objective_conflicting_revisions(records, anchor_sha, binding_sha=None):
    """Same reported ID, different declared question; not a refutation of either."""
    anchor = records.get(anchor_sha)
    if anchor is None:
        return []
    fixed = records.get(binding_sha) or anchor
    conflicts = []
    for sha, row in records.items():
        if row["id"] != anchor["id"]:
            continue
        if (row["kind"] != "objective" or
                any(row[key] != anchor[key] for key in ("statement", "scope", "assumptions")) or
                (objective_complete(fixed) and objective_complete(row)
                 and fixed["project_objective"] != row["project_objective"])):
            conflicts.append(sha)
    return sorted(conflicts)


def validate_review(review):
    _exact(review, _REVIEW_FIELDS, "review")
    if review["schema"] != "crs-review/v1":
        _fail("schema_version", "Unsupported review schema")
    for key in ("id", "method", "scope", "findings", "created_at"):
        _text(review[key], "review." + key)
    for key in ("target", "report"):
        _sha(review[key], "review." + key)
    _enum(review["decision"], {"accept", "reject", "inconclusive"}, "review.decision")
    _strings(review["coverage"], "review.coverage", unique=True)
    for item in review["coverage"]:
        _enum(item, _COVERAGE, "review.coverage[]")
    _strings(review["limitations"], "review.limitations")
    _strings(review["materials"], "review.materials", hashes=True, unique=True)
    _strings(review["supersedes"], "review.supersedes", hashes=True, unique=True)
    _exact(review["reviewer"], {"identity", "independence", "basis"}, "reviewer")
    _text(review["reviewer"]["identity"], "reviewer.identity")
    _enum(review["reviewer"]["independence"], {"independent", "self", "unknown"}, "reviewer.independence")
    _text(review["reviewer"]["basis"], "reviewer.basis", empty=True)
    canonical(review)


def _cycle_nodes(graph):
    """Find precisely the cyclic nodes, without recursion depth limits."""
    order, seen = [], set()
    for start in graph:
        if start in seen:
            continue
        seen.add(start)
        stack = [(start, iter(graph[start]))]
        while stack:
            node, children = stack[-1]
            child = next(children, None)
            if child is None:
                order.append(node)
                stack.pop()
            elif child not in seen:
                seen.add(child)
                stack.append((child, iter(graph.get(child, ()))))
    reverse = {node: [] for node in graph}
    for node, children in graph.items():
        for child in children:
            reverse.setdefault(child, []).append(node)
    seen, cycles = set(), set()
    for start in reversed(order):
        if start in seen:
            continue
        component, stack = [], [start]
        seen.add(start)
        while stack:
            node = stack.pop()
            component.append(node)
            for child in reverse[node]:
                if child not in seen:
                    seen.add(child)
                    stack.append(child)
        if len(component) > 1 or start in graph.get(start, ()):
            cycles.update(component)
    return cycles


def _review_materials(record):
    needed = {item["sha256"] for item in record["evidence"]}
    needed.update(item["revision"] for item in record["dependencies"])
    correction = record["correction"]
    if correction:
        needed.add(correction["target"])
        if correction["replacement"]:
            needed.add(correction["replacement"])
    return needed


def _qualified_accept(record, review):
    if review["decision"] != "accept":
        return False
    coverage = set(review["coverage"])
    if "record_fidelity" not in coverage:
        return False
    epistemic = record["epistemic"]
    if epistemic in {"established", "refuted"}:
        reviewer = review["reviewer"]
        return bool(coverage & {"argument", "reproduction", "source"}) and reviewer["independence"] == "independent" and bool(reviewer["basis"].strip())
    if epistemic == "observation":
        return bool(coverage & {"computation", "reproduction", "source"})
    if epistemic == "definition":
        return "terminology" in coverage
    return True


def validate_excluded_locator(row):
    """Validate a location-only record reference; never grant review authority."""
    replacement = isinstance(row, dict) and row.get("reason") == "portable_replacement"
    _exact(row, {"sha256", "id", "reason", "replacement"} if replacement else {"sha256", "id", "reason"}, "excluded record")
    _sha(row["sha256"], "excluded revision")
    _text(row["id"], "excluded id")
    if replacement:
        _sha(row["replacement"], "portable replacement")
        if row["replacement"] == row["sha256"]:
            _fail("excluded_record_invalid", "A portable replacement cannot refer to itself")
    elif row["reason"] != "not_exportable":
        _fail("excluded_record_invalid", "Unknown excluded-record reason")
    return row


def assess(records, reviews, selected, excluded_records=None):
    """Assess one immutable snapshot; no filesystem access or trust admission.

    ``selected`` may retain hashes of omitted, unreviewed current revisions so a
    package does not mislabel an older accepted revision as current. Review IDs
    list all applicable historical and current reviews; explicit supersedes
    determines current decisions, never timestamps or differing reviewer names.
    """
    if not isinstance(records, dict) or not isinstance(reviews, list) or not isinstance(selected, dict):
        _fail("assessment_input", "Expected records object, reviews array, and selected object")
    if excluded_records is None:
        excluded_records = []
    _list(excluded_records, "excluded_records")
    excluded = {}
    for row in excluded_records:
        validate_excluded_locator(row)
        if row["sha256"] in excluded or row["sha256"] in records:
            _fail("excluded_record_invalid", "Excluded references are unique hash/id locators, never duplicate records or review authority")
        excluded[row["sha256"]] = row
    for sha, record in records.items():
        _sha(sha, "records key")
        validate_record(record)
        if digest(canonical(record)) != sha:
            _fail("record_hash", "Record bytes do not match their revision", sha)
    for identity, sha in selected.items():
        _text(identity, "selected key")
        _sha(sha, "selected revision")
        if sha in records and records[sha]["id"] != identity:
            _fail("selected_identity", "Selected revision belongs to another identity", identity)
        if sha in excluded and excluded[sha]["id"] != identity:
            _fail("selected_identity", "Selected excluded revision belongs to another identity", identity)
    by_hash = {}
    for review in reviews:
        validate_review(review)
        by_hash[digest(canonical(review))] = review
    review_graph = {}
    for sha, review in by_hash.items():
        if review["target"] not in records:
            _fail("review_target_missing", "Trusted review target is absent from assessment", review["id"])
        review_graph[sha] = review["supersedes"]
        for previous in review["supersedes"]:
            if previous == sha:
                _fail("review_supersedes_self", "A review cannot supersede itself")
            if previous not in by_hash:
                _fail("review_supersedes_missing", "Superseded review is absent from the trusted set", previous)
            if by_hash[previous]["target"] != review["target"]:
                _fail("review_supersedes_target", "Superseded review belongs to another target", previous)
    if _cycle_nodes(review_graph):
        _fail("review_supersedes_cycle", "Review supersession must be acyclic")

    applicable = {}
    target_reviews = defaultdict(list)
    for sha, review in by_hash.items():
        target_reviews[review["target"]].append(sha)
        applicable[sha] = _review_materials(records[review["target"]]) <= set(review["materials"])
    superseded = {old for sha, review in by_hash.items() if applicable[sha] for old in review["supersedes"]}
    statuses = {}
    structural = set()
    strong = {sha: set() for sha in records}
    reverse = defaultdict(set)
    reasons = defaultdict(set)
    for sha, record in records.items():
        hashes = target_reviews[sha]
        bound = [h for h in hashes if applicable[h]]
        active = [by_hash[h] for h in bound if h not in superseded]
        qualified = [r for r in active if _qualified_accept(record, r)]
        history = any(_qualified_accept(record, by_hash[h]) for h in bound)
        decisions = {r["decision"] for r in active}
        if "reject" in decisions:
            review_status = "rejected"
        elif "inconclusive" in decisions or ("accept" in decisions and not qualified):
            review_status = "inconclusive"
        elif qualified:
            review_status = "accepted"
        else:
            review_status = "unreviewed"
        usable = bool(qualified) and not (decisions & {"reject", "inconclusive"})
        if len(decisions) > 1:
            reasons[sha].add("review_conflict")
        if any(not applicable[h] for h in hashes):
            reasons[sha].add("review_materials_incomplete")
        if any(r["decision"] == "accept" and not _qualified_accept(record, r) for r in active):
            reasons[sha].add("review_coverage_or_independence_insufficient")
            usable = False
        if not usable:
            reasons[sha].add("review_" + review_status)
        statuses[sha] = {"review": review_status, "epistemic": record["epistemic"],
                         "effective_epistemic": record["epistemic"],
                         "effect": "current" if selected.get(record["id"]) == sha else "historical",
                         "exportable": history, "usable": usable,
                         "reasons": [], "review_ids": sorted({by_hash[h]["id"] for h in bound})}
        if not usable:
            statuses[sha]["effect"] = "needs_review"
        for dep in record["dependencies"]:
            revision = dep["revision"]
            if revision not in records:
                if revision in excluded and excluded[revision]["id"] == dep["id"] and dep["relation"] not in _STRONG:
                    pass  # A location-only relation makes no assertion about this omitted record.
                else:
                    reasons[sha].add("dependency_missing:" + revision)
                    structural.add(sha)
            elif records[revision]["id"] != dep["id"]:
                reasons[sha].add("dependency_identity_mismatch:" + revision)
                structural.add(sha)
            elif dep["relation"] in _STRONG:
                strong[sha].add(revision)
                reverse[revision].add(sha)
        correction = record["correction"]
        if correction:
            for reference in (correction["target"], correction["replacement"]):
                replacement_locator = reference == correction["replacement"] and reference in excluded
                if reference and reference not in records and not replacement_locator:
                    reasons[sha].add("correction_reference_missing:" + reference)
                    structural.add(sha)

    for sha in _cycle_nodes(strong):
        reasons[sha].add("dependency_cycle")
        structural.add(sha)
    # Structural closure follows every relation: an exported dependency graph
    # must not conceal missing nodes behind a background link.
    all_reverse = defaultdict(set)
    for sha, record in records.items():
        for dep in record["dependencies"]:
            if dep["revision"] in records:
                all_reverse[dep["revision"]].add(sha)
    pending = deque(structural)
    while pending:
        bad = pending.popleft()
        for child in all_reverse[bad]:
            if child not in structural:
                structural.add(child)
                reasons[child].add("dependency_structure_invalid:" + bad)
                pending.append(child)
    for sha in structural:
        statuses[sha].update(usable=False, exportable=False, effect="needs_review")

    # Evaluate influences before their targets. Applying a correction before
    # examining its own premises can produce a false withdrawal. This graph
    # also lets an accepted correction of a mistaken correction restore the
    # original claim, without depending on dictionary iteration order.
    influence = {sha: set(dependencies) for sha, dependencies in strong.items()}
    correcting = defaultdict(list)
    for sha, record in records.items():
        correction = record["correction"]
        if correction and statuses[sha]["usable"]:
            target = correction["target"]
            correcting[target].append(sha)
            influence[target].add(sha)
    influence_cycles = _cycle_nodes(influence)
    for sha in influence_cycles:
        statuses[sha].update(usable=False, effect="needs_review")
        reasons[sha].add("correction_dependency_cycle")
    # Cyclic nodes are already unusable; resolve their external dependants
    # against that result, instead of either guessing an order or looping.
    remaining = {sha: len(deps - influence_cycles) for sha, deps in influence.items() if sha not in influence_cycles}
    influence_reverse = defaultdict(set)
    for sha, dependencies in influence.items():
        for dependency in dependencies:
            influence_reverse[dependency].add(sha)
    ready = deque(sorted(sha for sha, count in remaining.items() if count == 0))
    while ready:
        sha = ready.popleft()
        record, status = records[sha], statuses[sha]
        for correction_sha in sorted(correcting[sha]):
            if not statuses[correction_sha]["usable"]:
                reasons[sha].add("correction_not_applicable:" + correction_sha)
                continue
            correction = records[correction_sha]["correction"]
            if correction["effect"] == "presentation":
                reasons[sha].add("presentation_correction:" + correction_sha)
                if correction["replacement"] and status["effect"] == "current":
                    status["effect"] = "historical"
            else:
                status.update(usable=False, effect="invalid")
                reasons[sha].add("corrected_" + correction["effect"] + ":" + correction_sha)
        for dependency in sorted(strong[sha]):
            dep_status = statuses[dependency]
            if not dep_status["usable"]:
                status["usable"] = False
                if status["effect"] != "invalid":
                    status["effect"] = "needs_review"
                reasons[sha].add("dependency_not_usable:" + dependency)
            if record["epistemic"] in {"established", "refuted"} and dep_status["effective_epistemic"] in {"hypothesis", "conditional"}:
                status["effective_epistemic"] = "conditional"
                if dependency not in record["conditional_on"]:
                    status.update(usable=False, exportable=False)
                    if status["effect"] != "invalid":
                        status["effect"] = "needs_review"
                    reasons[sha].add("conditional_dependency_undeclared:" + dependency)
                else:
                    reasons[sha].add("conditional_dependency:" + dependency)
        if record["conditional_on"] and record["epistemic"] in {"established", "refuted"}:
            status["effective_epistemic"] = "conditional"
            reasons[sha].add("explicit_conditions")
        for child in influence_reverse[sha]:
            if child in remaining:
                remaining[child] -= 1
                if remaining[child] == 0:
                    ready.append(child)
    for sha, status in statuses.items():
        status["reasons"] = sorted(reasons[sha])
    return statuses
