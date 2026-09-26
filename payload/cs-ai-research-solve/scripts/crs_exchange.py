"""Reviewed research exchange; verification never executes research code."""
from __future__ import annotations

import crs_temp

from contextlib import contextmanager
from io import TextIOWrapper
from types import SimpleNamespace
import codecs
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import tempfile
import zipfile

from crs_model import canonical, digest, parse_json, CRSError, assess, validate_record, validate_review, objective_identity, objective_compatible, objective_complete, objective_conflicting_revisions, RECORD_SCHEMA

MAX_ENTRIES = 20000
MAX_ENTRY_BYTES = 512 * 1024 * 1024
MAX_EXPANDED_BYTES = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
MAX_JSON_BYTES = 256 * 1024 * 1024
# External declarations do not allocate or decode their stated bytes.
MAX_REFERENCED_BYTES = 64 * 1024**3
MAX_COMPRESSION_RATIO = 1000
SHA = re.compile(r"^[0-9a-f]{64}$")
LOCAL_PATH = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]+(?![\\/<>:*?|\x00-\x1f])|(?<!\\)\\\\(?!\\)[A-Za-z0-9_.-]+\\(?!\\)[^\\\s\"']+|(?<![A-Za-z0-9:])/(?:Users|home|root|tmp|private|Volumes|mnt|workspace)/")
MANIFEST_KEYS = {"schema", "source_snapshot", "record_hashes", "selected", "reviews", "assets", "statuses", "origins", "excluded_records", "namespace", "tools", "assurance"}
TOOL_NAMES = ("crs_temp.py", "crs_locations.py", "crs_unique.py", "crs_parts.py", "crs_metrics.py", "crs_prediction.py", "crs_output.py", "crs.py", "crs_model.py", "crs_store.py", "crs_exchange.py", "crs_adopt.py", "crs_replay.py", "crs_workflow.py", "crs_batch.py", "crs_finish.py", "crs_obsidian.py", "crs_formula.py", "crs_continuity.py", "crs_export_plan.py", "crs_recipient.py")
STRONG_RELATIONS = {"premise", "input", "term"}
EXCHANGE_SCHEMA = "crs-exchange/v1"
ASSURANCE = "byte_integrity_reviewed_projection_only_not_evidence_replay"


def _fail(code, message, details=None):
    raise CRSError(code, message, details or {})


def _sha(value):
    if not isinstance(value, str) or not SHA.fullmatch(value):
        _fail("exchange_invalid_hash", "Expected a lowercase SHA-256 digest.")
    return value


def _integer(value, label, maximum=MAX_EXPANDED_BYTES):
    if type(value) is not int or value < 0 or value > maximum:
        _fail("exchange_invalid_size", "Invalid bounded byte count.", {"field": label})
    return value


def _local_path_matches(value, *, formula_prose=True):
    """Recognize the specific TeX operator-colon ambiguity inside formula spans.

    Filesystem-shaped locators remain strict. This is a bounded lexical aid,
    not a general privacy sanitizer or a proof of the author's intended meaning.
    """
    matches = list(LOCAL_PATH.finditer(value))
    if not matches:
        return
    spans = []
    if formula_prose:
        for pattern in (r'(?s)(?<!\\)\$\$.*?(?<!\\)\$\$',
                        r'(?s)(?<![\\$])\$(?!\$).*?(?<![\\$])\$(?!\$)',
                        r'(?s)\\\[.*?\\\]', r'(?s)\\\(.*?\\\)'):
            spans.extend((m.start(), m.end()) for m in re.finditer(pattern, value))
    for match in matches:
        start = match.start()
        operator = re.match(r'[A-Za-z]:\\(?:sum|prod|int|iint|oint|frac|sqrt)(?=[_{^(\s])', value[start:])
        containing = next(((left, right) for left, right in spans if left <= start < right), None)
        # Permit only a TeX operator expression. Any later unknown
        # backslash word or file extension restores strict path treatment.
        tail = value[start + operator.end():containing[1]] if operator and containing else ''
        commands = re.findall(r'\\([A-Za-z]+)', tail)
        known = {'sum','prod','int','iint','oint','frac','sqrt','left','right','infty','limits','cdot','times','le','ge','in','mathbb','mathrm','mathbf','text','alpha','beta','gamma','delta','theta','pi','sigma','lambda','mu','varepsilon'}
        filesystem_suffix = bool(re.search(r'\.[A-Za-z0-9]{1,12}(?:[^A-Za-z0-9]|$)', tail)) or any(cmd not in known for cmd in commands)
        if operator and containing and not filesystem_suffix:
            continue
        yield match


def _portable_text(value, *, field="$", asset_sha256=None):
    # A locator reports structural position and content identity, never the
    # matched private path or an arbitrary private dictionary key.
    if asset_sha256 is None and isinstance(value, dict):
        if value.get("schema") in {RECORD_SCHEMA, "crs-review/v1"}:
            asset_sha256 = digest(canonical(value))
        elif isinstance(value.get("sha256"), str) and SHA.fullmatch(value["sha256"]):
            asset_sha256 = value["sha256"]
    if isinstance(value, str):
        match = next(_local_path_matches(value, formula_prose=not field.endswith(('.locator', '.path'))), None)
        if match:
            position = match.start()
            details = {"field": field, "line": value.count("\n", 0, position) + 1,
                       "column": position - value.rfind("\n", 0, position)}
            if asset_sha256 is not None:
                details["asset_sha256"] = asset_sha256
            _fail("exchange_local_path", "A reviewed portable derivative is required before exporting machine-local paths.", details)
    elif isinstance(value, dict):
        for index, (key, item) in enumerate(value.items()):
            _portable_text(key, field=field + ".<key:" + str(index) + ">", asset_sha256=asset_sha256)
            segment = "." + key if isinstance(key, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", key) else ".<value:" + str(index) + ">"
            _portable_text(item, field=field + segment, asset_sha256=asset_sha256)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _portable_text(item, field=field + "[" + str(index) + "]", asset_sha256=asset_sha256)


def _filename(name):
    if not isinstance(name, str) or not name or len(name) > 240 or "\\" in name or ":" in name or "\x00" in name:
        _fail("exchange_unsafe_path", "Unsafe exchange member name.")
    if any(c in name for c in '<>"|?*'):
        _fail("exchange_unsafe_path", "Windows-forbidden characters are not portable member names.")
    pieces = name.split("/")
    if name.startswith("/") or any(p in {"", ".", ".."} or p.endswith((" ", ".")) for p in pieces):
        _fail("exchange_unsafe_path", "Unsafe exchange member path.")
    reserved = {"CON", "PRN", "AUX", "NUL", "CLOCK$", *("COM" + str(n) for n in range(1, 10)), *("LPT" + str(n) for n in range(1, 10))}
    if any(p.split(".")[0].upper() in reserved or any(ord(c) < 32 for c in p) for p in pieces):
        _fail("exchange_unsafe_path", "Unsupported exchange member name.")
    return name


def _object_name(sha):
    return "objects/" + _sha(sha)


def _is_within(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _no_links(path):
    for component in (path, *path.parents):
        if not component.exists() or crs_temp.system_alias(component):
            continue
        info = component.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            _fail("exchange_link_path", "Exchange paths must not traverse links or reparse points.")


def _output_path(store, output):
    from crs_unique import external_target
    external_target(store, output)
    output = Path(output).absolute()
    if output.suffix.lower() != ".zip":
        _fail("exchange_output_type", "The exchange output must be a new .zip file.")
    if not output.parent.is_dir():
        _fail("exchange_output_parent", "The output parent directory must already exist.")
    _no_links(output)
    output = output.parent.resolve(strict=True) / output.name
    if _is_within(output, Path(store.root).resolve(strict=True)):
        _fail("exchange_output_overlap", "Exchange output must be outside the formal research store.")
    if output.exists() or output.is_symlink():
        _fail("exchange_output_exists", "Exchange output already exists; choose a fresh path.")
    return output


def _read_json(path):
    if path.stat().st_size > MAX_JSON_BYTES:
        _fail("exchange_json_limit", "Exchange JSON exceeds the metadata limit.")
    try:
        return parse_json(path.read_bytes())
    except CRSError:
        raise
    except (ValueError, UnicodeError, OSError) as exc:
        _fail("exchange_invalid_json", "Invalid exchange JSON.", {"error": type(exc).__name__})


def _file_digest(path):
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _portable_file(path):
    """Reject machine-local locators in ordinary UTF-8 text without rewriting it."""
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    tail = ""
    consumed_lines = 0
    line_base = 0
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                text = decoder.decode(block)
                line_base = consumed_lines - tail.count("\n")
                _portable_text(tail + text, field="$.utf8_content")
                consumed_lines += text.count("\n")
                tail = text[-512:]
        line_base = consumed_lines - tail.count("\n")
        _portable_text(tail + decoder.decode(b"", final=True), field="$.utf8_content")
    except UnicodeDecodeError:
        # Binary source bytes retain their exact identity; this is not a binary metadata sanitizer.
        return
    except CRSError as error:
        if error.code != "exchange_local_path":
            raise
        details = dict(error.details or {})
        details["asset_sha256"] = _file_digest(path)
        details["line"] = details.get("line", 1) + line_base
        raise CRSError(error.code, error.message, details) from None



def check_review_portability(store, review, report_path=None):
    """Read-only handoff preflight; not review admission or claim verification."""
    from crs_store import file_digest, ordinary
    snapshot = store.head()
    bound = dict(review) if isinstance(review, dict) else review
    findings, report = [], None
    path = None

    def finding(stage, error):
        findings.append({"stage": stage, "code": getattr(error, "code", "review_check_input_error"),
                         "message": str(error), "details": getattr(error, "details", None)})

    try:
        if report_path is not None:
            path = ordinary(Path(report_path).absolute())
        else:
            declared = bound.get("report") if isinstance(bound, dict) else None
            _sha(declared)
            path = store.resolve(declared)
        report_sha, report_bytes = file_digest(path)
        report = {"sha256": report_sha, "bytes": report_bytes,
                  "source": "provided_file" if report_path is not None else "stored_report"}
        if isinstance(bound, dict):
            if bound.get("report") and bound["report"] != report_sha:
                _fail("report_mismatch", "The review points to different report bytes.")
            bound["report"] = report_sha
    except (CRSError, OSError, ValueError, TypeError) as error:
        finding("report_binding", error)
    try:
        validate_review(bound)
    except CRSError as error:
        finding("review_schema", error)
    try:
        _portable_text(bound, field="$.review")
    except CRSError as error:
        finding("review_metadata", error)
    if report is not None:
        try:
            _portable_file(path)
        except (CRSError, OSError, ValueError) as error:
            finding("report_portability", error)
        try:
            if file_digest(path) != (report["sha256"], report["bytes"]):
                _fail("review_report_changed", "The report changed during this read-only check; check its final bytes again.")
        except (CRSError, OSError, ValueError) as error:
            finding("report_binding", error)
    return {"ok": not findings,
            "status": "review_portability_checked" if not findings else "review_portability_blocked",
            "check_only": True, "committed": False, "review_admitted": False,
            "claims_verified": False, "snapshot": snapshot,
            "review_sha256": digest(canonical(bound)),
            "target": bound.get("target") if isinstance(bound, dict) else None,
            "report": report, "findings": findings,
            "checks": ["review_schema", "review_metadata_portability", "exact_report_binding", "report_content_portability"],
            "scope": "Existing exchange portability checks only; no research review, material replay or admission guarantee."}


def _unique_hashes(values, label):
    if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
        _fail("exchange_invalid_manifest", "Invalid hash collection.", {"field": label})
    for value in values:
        _sha(value)
    if values != sorted(set(values)):
        _fail("exchange_invalid_manifest", "Hash collections must be sorted and unique.", {"field": label})
    return values


def _excluded_records(rows, records, *, check_portability=True):
    if not isinstance(rows, list):
        _fail("exchange_exclusions_invalid", "Expected excluded record locators.")
    excluded = {}
    for row in rows:
        from crs_model import validate_excluded_locator
        validate_excluded_locator(row)
        if row["reason"] == "portable_replacement":
            replacement = records.get(row["replacement"])
            if replacement is None or replacement["id"] != row["id"] or replacement["previous"] != row["sha256"]:
                _fail("exchange_portable_replacement_invalid", "Portable history needs its retained same-identity direct revision.")
        sha = _sha(row["sha256"])
        if sha in excluded or sha in records:
            _fail("exchange_exclusions_invalid", "An excluded locator cannot duplicate a retained record or another locator.")
        if check_portability:
            _portable_text(row)
        excluded[sha] = row
    if rows != sorted(rows, key=lambda row: row["sha256"]):
        _fail("exchange_exclusions_invalid", "Excluded locators must be deterministically ordered.")
    return excluded


def _dependencies(records, excluded):
    for sha, record in records.items():
        for dependency in record["dependencies"]:
            target = dependency["revision"]
            omitted_location = target in excluded and excluded[target]["id"] == dependency["id"] and dependency["relation"] not in STRONG_RELATIONS
            if not omitted_location and (target not in records or records[target]["id"] != dependency["id"]):
                _fail("exchange_dependency_unreviewed", "A retained research dependency has no reviewed record in this package.", {"record": sha, "dependency": target})
        correction = record["correction"]
        if correction and (correction["target"] not in records or correction["replacement"] and correction["replacement"] not in records and correction["replacement"] not in excluded):
            _fail("exchange_correction_context_missing", "A correction needs its actual target and an exact replacement record or excluded locator.")


def _location_refs(records, selected):
    refs = set(selected.values())
    for row in records.values():
        if row["previous"]:
            refs.add(row["previous"])
        refs.update(dep["revision"] for dep in row["dependencies"] if dep["relation"] not in STRONG_RELATIONS)
        if row["correction"] and row["correction"]["replacement"]:
            refs.add(row["correction"]["replacement"])
    return refs


def _origins(rows, records):
    if not isinstance(rows, list):
        _fail("exchange_origins_invalid", "Expected reported provenance rows.")
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"record", "source"} or row["record"] not in records or not isinstance(row["source"], str) or not row["source"].strip():
            _fail("exchange_origins_invalid", "Reported provenance must name a retained exact record and a source.")
        identity = (row["record"], row["source"])
        if identity in seen:
            _fail("exchange_origins_invalid", "Duplicate reported provenance rows are not accepted.")
        seen.add(identity)
        _portable_text(row)
    if rows != sorted(rows, key=lambda row: (row["record"], row["source"])):
        _fail("exchange_origins_invalid", "Reported provenance must be deterministically ordered.")
    return rows



def presentation_correction_context(records, statuses, reviews, target_sha):
    """Expose applicable reviewed presentation notes; never replace record bytes."""
    target = records.get(target_sha)
    targets = {target_sha}
    if target and target["kind"] == "objective":
        # Metadata revisions share one declared question. Keep each correction's
        # exact target visible; these are related notes, not propagated edits.
        targets.update(sha for sha, row in records.items()
                       if row["kind"] == "objective" and objective_compatible(target, row))
    corrections = set()
    for revision in targets:
        for reason in statuses.get(revision, {}).get("reasons", []):
            if reason.startswith("presentation_correction:"):
                corrections.add(reason.split(":", 1)[1])
    result = []
    for sha in sorted(corrections):
        row, state = records.get(sha), statuses.get(sha, {})
        if (not row or row["kind"] != "correction" or state.get("review") != "accepted"
                or not state.get("usable") or row["correction"]["effect"] != "presentation"
                or row["correction"]["target"] not in targets):
            continue
        review_refs = [{"revision": digest(canonical(review)), "id": review["id"],
                        "decision": review["decision"], "report": review["report"]}
                       for review in reviews if review["target"] == sha
                       and review["id"] in state["review_ids"]]
        result.append({"revision": sha, "id": row["id"], "title": row["title"],
                       "target": row["correction"]["target"], "statement": row["statement"],
                       "scope": row["scope"], "reason": row["correction"]["reason"],
                       "limitations": list(row["limitations"]), "status": dict(state),
                       "reviews": sorted(review_refs, key=lambda item: item["revision"])})
    return result


def render_map(records, reviews, selected, title, link_prefix="objects/", object_path=None, origins=None, excluded_records=None, objective=None, asset_paths=None, objective_binding=None, presentation=None):
    """Render a deterministic overview and complete navigable evidence view."""
    from collections import Counter, defaultdict
    from urllib.parse import quote

    statuses = assess(records, reviews, selected, excluded_records=excluded_records)
    excluded = _excluded_records(excluded_records or [], records)
    object_link = object_path or (lambda sha: link_prefix + sha)
    kinds = {"objective": "研究目标", "attempt": "尝试", "result": "结论记录", "hypothesis": "待检验假说", "observation": "实验、计算或事实观察", "failure": "失败经验", "definition": "定义", "correction": "更正"}
    epistemic = {"established": "记录声称已确立的结论", "refuted": "记录声称的反驳结论", "hypothesis": "待检验假说", "observation": "观察", "attempt": "尝试", "failure": "失败经验", "definition": "定义", "correction": "更正", "conditional": "有条件结论"}
    review_labels = {"accepted": "审核接受", "rejected": "审核拒绝", "inconclusive": "审核未定", "unreviewed": "未审核"}
    effects = {"current": "当前所选", "historical": "历史修订", "invalid": "已撤回或范围失效", "needs_review": "待审核或待复核（见记录依据）"}
    relations = {"premise": "前提", "input": "输入", "term": "术语或定义", "background": "背景", "extends": "扩展", "corrects": "更正", "supersedes": "替代"}
    ordered = sorted(records, key=lambda sha: (list(kinds).index(records[sha]["kind"]), records[sha]["id"], sha))
    review_objects = {digest(canonical(row)): row for row in reviews}

    def label(value):
        if presentation is not None:
            from crs_formula import safe_label
            return safe_label(presentation.apply(str(value)))
        return str(value).replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]").replace("|", "\\|").replace("\n", " ").replace("<", "&lt;").replace(">", "&gt;")

    def href(text, destination):
        return "[" + label(text) + "](" + quote(str(destination), safe="/:#?=&%\\") + ")"

    def anchor(name):
        return '<a id="' + name + '"></a>'

    def record_ref(sha):
        if sha in records:
            return href(records[sha]["title"] + " · " + sha[:10], "#record-" + sha)
        if sha in excluded:
            return href(excluded[sha]["id"] + " · 正文未提供", "#excluded-" + sha)
        return "`" + sha + "`（正文未提供）"

    def effect_label(sha):
        state = statuses[sha]
        if state["effect"] != "needs_review":
            return effects[state["effect"]]
        if any(reason.startswith(("dependency_", "corrected_", "correction_")) for reason in state["reasons"]):
            return "待复核 / 受影响"
        return "待审核" if state["review"] == "unreviewed" else "待复核"

    def state_text(sha):
        state = statuses[sha]
        grade = "研究目标（不据此判断是否完成）" if records[sha]["kind"] == "objective" else epistemic[state["effective_epistemic"]]
        return (grade + "；" + review_labels[state["review"]] + "；" + effect_label(sha) +
                "；当前可用=" + str(state["usable"]).lower() + "；可交付=" + str(state["exportable"]).lower())

    def excerpt(value, destination, limit=180):
        if presentation is not None:
            from crs_formula import excerpt as formula_excerpt, validate
            value = presentation.apply(value)
            validate(value)
            return label(formula_excerpt(value, limit)) + " " + href("全文", destination)
        value = " ".join(value.split())
        if not value:
            return "未记录"
        return label(value[:limit]) + ("…" if len(value) > limit else "") + " " + href("全文", destination)

    assets = defaultdict(list)
    asset_records = defaultdict(set)
    asset_reviews = defaultdict(set)
    by_target = defaultdict(list)
    source_records = defaultdict(set)
    source_kinds = defaultdict(set)
    record_sources = defaultdict(set)
    forward = defaultdict(list)
    backward = defaultdict(list)

    def edge(source, target, relation, reason):
        forward[source].append((target, relation, reason))
        backward[target].append((source, relation, reason))

    for sha in ordered:
        record = records[sha]
        for item in record["evidence"]:
            assets[item["sha256"]].append(item)
            asset_records[item["sha256"]].add(sha)
        for source in record["sources"]:
            source_records[source].add(sha)
            record_sources[sha].add(source)
            source_kinds[source].add("记录中的来源引用")
        for dep in record["dependencies"]:
            edge(sha, dep["revision"], relations[dep["relation"]] + " / " + dep["relation"], dep["reason"])
        if record["previous"]:
            edge(sha, record["previous"], "前一修订 / previous", "同一研究身份的显式修订链。")
        if record["correction"]:
            correction = record["correction"]
            edge(sha, correction["target"], "更正目标 / " + correction["effect"], correction["reason"])
            if correction["replacement"]:
                edge(sha, correction["replacement"], "更正指定的替代记录", "替代记录需凭自身审核取得适用资格。")
    for row in origins or []:
        if row["record"] in records:
            source = row["source"]
            source_records[source].add(row["record"])
            record_sources[row["record"]].add(source)
            source_kinds[source].add("报告的贡献来源（身份未认证）")
    for rsha, review in sorted(review_objects.items()):
        by_target[review["target"]].append(rsha)
        assets[review["report"]]
        asset_reviews[review["report"]].add(rsha)
        for material in review["materials"]:
            if material not in records and material not in excluded and material not in review_objects:
                assets[material]
                asset_reviews[material].add(rsha)
    source_ids = {source: digest(source.encode("utf-8")) for source in source_records}

    lines = ["# " + label(title), "", "研究地图：先看目标和状态，再沿记录、来源、前提、更正及原始资产查看完整证据。地图不重新检验任何研究结论。", "",
             href("目标", "#goals") + " · " + href("概览", "#overview") + " · " + href("发展路线", "#routes") + " · " + href("动作与反馈索引", "#attempts") + " · " + href("完整记录", "#records") + " · " + href("资产", "#assets") + " · " + href("审核", "#reviews") + " · " + href("来源", "#sources"), "",
             anchor("goals"), "## 研究目标", ""]
    if objective in records:
        record = records[objective]
        lines.extend(["### 当前项目主目标：" + record_ref(objective), "", record["statement"], "", "**适用范围：** " + record["scope"], "", "**状态：** " + state_text(objective), ""])
        expression_notes = presentation_correction_context(records, statuses, reviews, objective)
        if expression_notes:
            lines.extend(["#### 已审表达更正（并列保留，不覆盖原目标）", "",
                          "以下只说明各自指定修订的表达与作用范围；不自动选择一条代替其他更正，不改变主目标身份或后继结论状态。", ""])
            for note in expression_notes:
                lines.extend(["- **更正记录：** " + record_ref(note["revision"]),
                              "  **确切目标修订：** " + record_ref(note["target"]), "",
                              note["statement"], "", "**作用范围：** " + note["scope"],
                              "**更正原因：** " + note["reason"], ""])
                if note["limitations"]:
                    lines.extend(["**限制：** " + "；".join(note["limitations"]), ""])
                review_links = []
                for reviewed in note["reviews"]:
                    review_links.append(href(reviewed["id"] + " · " + reviewed["decision"],
                                             "#review-" + reviewed["revision"]))
                    destination = object_link(reviewed["report"])
                    if destination:
                        review_links.append(href("审核报告 · " + reviewed["report"][:10], destination))
                lines.extend(["**审核入口：** " + " · ".join(review_links), ""])
        description = records.get(objective_binding, record)
        identity = objective_identity(description)
        lines.extend(["**主目标身份：** " + identity["completeness"] + "；首次主目标锚点保持固定，选择其他问题不会替换它。", ""])
        if identity["missing_fields"]:
            lines.extend(["**未补齐构成：** " + "、".join(identity["missing_fields"]) + "。已有材料保持可读；不从文本猜测缺项。", ""])
        if objective_complete(description):
            labels = {"statement": "研究陈述", "domain": "研究领域与设定", "claim_scope": "结论范围（总体、汇总方式与指标）",
                      "assumptions": "假设条件", "evidence_standard": "证据标准", "completion_standard": "完成标准"}
            for field, field_label in labels.items():
                value = description["project_objective"][field]
                lines.extend(["- **" + field_label + "：** " + ("；".join(value) if isinstance(value, list) else value)])
            lines.append("")
        if objective_binding:
            lines.extend(["**显式描述补齐修订：** " + record_ref(objective_binding) + "；补齐描述不继承此前修订的审核。", ""])
        objective_conflicts = objective_conflicting_revisions(records, objective, objective_binding)
        if objective_conflicts:
            lines.extend(["**同 ID 的题意冲突修订：** " + "、".join(record_ref(sha) for sha in objective_conflicts),
                          "这些原始记录继续保留，但不是当前主目标；题意冲突不表示其中的研究陈述不成立，也不声称历史描述一致。新方向应以新 ID 关联保存。", ""])
    elif objective is not None:
        lines.extend(["当前项目主目标：" + record_ref(objective) + "。此视图未提供其正文，不从来源项目目标推断替代关系。", ""])
    else:
        lines.extend(["此视图未声明当前项目主目标；不要从其他记录推断目标或完成状态。", ""])
    goals = [sha for sha in ordered if records[sha]["kind"] == "objective" and sha != objective]
    if goals:
        lines.extend(["### 其他目标记录（含来源项目目标）", ""])
        for sha in goals:
            lines.append("- " + record_ref(sha) + "：" + state_text(sha))
        lines.append("")

    from crs_continuity import graph
    route_graph = graph(records)
    route_parents, route_children = defaultdict(list), defaultdict(list)
    for edge in route_graph['edges']:
        if edge['relation'] != 'background':
            route_parents[edge['successor']].append(edge)
            route_children[edge['predecessor']].append(edge)
    lines.extend([anchor("routes"), "## 研究发展路线", "",
                  "按已保存的修订、扩展和研究依赖查看前驱与后继。分支和汇合并列保留；背景相关不用于推断先后。关系存在不等于已经审核，也不表明历史上的实际承接。", "",
                  "| 记录 | 前驱及关系 | 本项反馈或进展（原文） | 后继 | 状态 |",
                  "| --- | --- | --- | --- | --- |"])
    for sha in route_graph['order']:
        predecessors = "；".join(record_ref(e['predecessor']) + "（" + relations.get(e['relation'], "修订") + "）" for e in route_parents[sha]) or "未保存前驱"
        successors = "；".join(record_ref(e['successor']) for e in route_children[sha]) or "未保存后继"
        row = records[sha]
        progress = excerpt(row['feedback'] or row['statement'], "#record-" + sha)
        lines.append("| " + record_ref(sha) + " | " + predecessors + " | " + progress + " | " + successors + " | " + state_text(sha) + " |")
    if route_graph['order_unresolved']:
        lines.extend(["", "**顺序待核对：** " + "；".join(record_ref(sha) for sha in route_graph['order_unresolved']) + "。存在环或受其影响，未强行排列逻辑先后。"])
    if route_graph['missing_references']:
        lines.extend(["", "**缺少引用正文：** " + "；".join(record_ref(sha) for sha in route_graph['missing_references'])])
    lines.append("")

    lines.extend([anchor("overview"), "## 概览", "", "共 " + str(len(records)) + " 个完整记录修订、" + str(len(review_objects)) + " 个审核记录、" + str(len(assets)) + " 个证据或报告资产。", "",
                  "结论类型、审核状态、依赖影响分别统计。记录声称已确立的结论须与审核及可用状态一起读；已审假说仍未确立，实验中成立的假说只是有界观察。历史记录可交付不等于可作当前前提。", "",
                  "| 分类 | 状态 | 记录数 |", "| --- | --- | ---: |"])
    for axis, names, heading in (("effective_epistemic", epistemic, "结论类型"), ("review", review_labels, "审核状态"), ("effect", effects, "依赖影响")):
        counts = Counter(state[axis] for sha, state in statuses.items()
                         if axis != "effective_epistemic" or records[sha]["kind"] != "objective")
        for value in names:
            if counts[value]:
                lines.append("| " + heading + " | " + names[value] + " / `" + value + "` | " + str(counts[value]) + " |")
    lines.extend(["", "当前可用：" + str(sum(state["usable"] for state in statuses.values())) + "；已审可交付：" + str(sum(state["exportable"] for state in statuses.values())) + "；仅有排除定位、没有正文：" + str(len(excluded)) + "。", "",
                  "### 研究进展", "", "以下为记录原文节选，不增加新的总结判断。数值、失败反馈须连同审核、条件和限制阅读；全文链接保留完整上下文。目标记录不计入结论类型统计。", "",
                  ""])
    for sha in ordered:
        row = records[sha]
        if row["kind"] == "objective":
            continue
        destination = "#record-" + sha
        state = statuses[sha]
        lines.extend(["#### " + href(row["title"], destination), "",
                      epistemic[state["effective_epistemic"]] + "；" + review_labels[state["review"]] + "；" + effect_label(sha) + "。", "",
                      excerpt(row["statement"], destination, 240), ""])
        if row["conditional_on"]:
            lines.extend(["**承接的前提：** " + "；".join(href(records[target]["title"], "#record-" + target) if target in records else record_ref(target) for target in row["conditional_on"]), ""])
        if row["assumptions"]:
            lines.extend(["**条件：** " + excerpt("；".join(row["assumptions"]), destination, 180), ""])
        if row["limitations"]:
            lines.extend(["**限制：** " + excerpt("；".join(row["limitations"]), destination, 240), ""])
    lines.extend(["", "| 记录类型 | 查看记录（完整修订） |", "| --- | --- |"])
    for kind, kind_label in kinds.items():
        group = [record_ref(sha) for sha in ordered if records[sha]["kind"] == kind]
        if group:
            lines.append("| " + kind_label + " | " + "；".join(group) + " |")

    lines.extend(["", anchor("attempts"), "## 动作与反馈索引", "", "索引覆盖所有记录过动作或反馈的研究条目，直接展示原文节选并链接完整内容。", "",
                  "| 记录 | 动作 | 反馈 | 审核与影响 |", "| --- | --- | --- | --- |"])
    for sha in ordered:
        row = records[sha]
        if row["action"] or row["feedback"]:
            lines.append("| " + record_ref(sha) + " | " + excerpt(row["action"], "#action-" + sha) + " | " +
                         excerpt(row["feedback"], "#feedback-" + sha) + " | " + review_labels[statuses[sha]["review"]] + "；" + effect_label(sha) + " |")

    lines.extend(["", anchor("records"), "## 完整研究记录", ""])
    for sha in ordered:
        row, state = records[sha], statuses[sha]
        lines.extend([anchor("record-" + sha), "### " + label(row["title"]), "", "**类型：** " + kinds[row["kind"]] + " / `" + row["kind"] + "`", "",
                      "**状态：** " + state_text(sha), "", "**记录原始结论类型：** `" + state["epistemic"] + "`；**考虑依赖后的结论类型：** `" + state["effective_epistemic"] + "`。", "",
                      "**研究身份：** `" + row["id"] + "`；**修订：** `" + sha + "`。", "",
                      href("查看完整 JSON 对象", object_link(sha)) + " · " + href("返回概览", "#overview"), "",
                      "**陈述：**", "", row["statement"], "", "**适用范围：**", "", row["scope"], ""])
        for key, heading in (("action", "动作"), ("feedback", "反馈")):
            if row[key]:
                lines.extend([anchor(key + "-" + sha), "**" + heading + "：**", "", row[key], ""])
        for key, heading in (("assumptions", "假设条件"), ("limitations", "限制"), ("reopen", "重新研究的条件")):
            if row[key]:
                lines.extend(["**" + heading + "：**", ""])
                lines.extend("- " + item for item in row[key])
                lines.append("")
        if state["reasons"]:
            lines.extend(["**状态依据（原始诊断标记）：**", ""])
            lines.extend("- `" + reason + "`" for reason in state["reasons"])
            lines.append("")
        if row["conditional_on"]:
            lines.extend(["**明确条件引用：** " + "；".join(record_ref(target) for target in row["conditional_on"]), ""])
        if row["correction"]:
            lines.extend(["**更正原始字段：** `" + json.dumps(row["correction"], ensure_ascii=False, sort_keys=True) + "`", ""])
        if forward[sha]:
            lines.extend(["**本记录依赖或指向：**", ""])
            for target, relation, reason in sorted(forward[sha]):
                lines.append("- " + relation + " → " + record_ref(target) + "：" + reason)
            lines.append("")
        if backward[sha]:
            lines.extend(["**引用本记录的后续、修订与更正：**", ""])
            for source, relation, reason in sorted(backward[sha]):
                lines.append("- " + record_ref(source) + " → " + relation + "：" + reason)
            lines.append("")
        if record_sources[sha]:
            lines.extend(["**来源：**", ""])
            for source in sorted(record_sources[sha]):
                lines.append("- " + href(source, "#source-" + source_ids[source]))
            lines.append("")
        if row["evidence"]:
            lines.extend(["**证据资产：**", ""])
            for item in row["evidence"]:
                lines.append("- " + href(item["name"], "#asset-" + item["sha256"]) + "：`" + item["role"] + "`，" + str(item["bytes"]) + " 字节；SHA-256 `" + item["sha256"] + "`。")
                if item["summary"]:
                    lines.append("  - 摘要：" + item["summary"])
                if item["locator"]:
                    lines.append("  - 原始定位：" + item["locator"])
            lines.append("")
        if by_target[sha]:
            lines.extend(["**相关审核：** " + "；".join(href(review_objects[rsha]["id"], "#review-" + rsha) for rsha in by_target[sha]), ""])

    lines.extend([anchor("assets"), "## 资产与原始证据", "", "对象链接定位确切字节。外部引用可能没有随包附带正文；读取摘要与适用条件，取得文件后核对哈希。", ""])
    for sha in sorted(assets):
        descriptors = assets[sha]
        names = sorted({item["name"] for item in descriptors})
        lines.extend([anchor("asset-" + sha), "### " + label(" / ".join(names) if names else "审核报告或材料") + " · " + sha[:10], "", "**SHA-256：** `" + sha + "`。", ""])
        locators = sorted({item["locator"] for item in descriptors if item["locator"]})
        # Availability is rendering context, not inferred from the presence of
        # a summary. Package paths come from the existing asset inventory; a
        # local resolver may return None when the evidence is unavailable.
        if asset_paths is not None and sha in asset_paths:
            destination = asset_paths[sha]
            availability = "已随包提供" if destination is not None else "未随包；按定位取回"
        elif object_path is not None:
            destination = object_link(sha)
            availability = "本地文件可用（已解析位置）" if destination is not None else "本地文件不可用；按定位取回"
        else:
            destination = None if locators else object_link(sha)
            availability = "按定位取回；此视图未声明文件可用性" if locators else "内容对象"
        lines.extend(["**文件可用性：** " + availability + "。", ""])
        if destination is not None:
            lines.extend([href("重开内容对象", destination), ""])
        for item in sorted(descriptors, key=lambda item: canonical(item)):
            lines.append("- `" + item["role"] + "`；" + str(item["bytes"]) + " 字节；" + item["summary"])
        for locator in locators:
            display = href("打开来源定位", locator) if locator.startswith(("https://", "http://")) else locator
            lines.append("- " + display + "；原始定位：" + locator)
        if asset_records[sha]:
            lines.extend(["", "**使用此资产的记录：** " + "；".join(record_ref(target) for target in sorted(asset_records[sha]))])
        if asset_reviews[sha]:
            lines.extend(["", "**使用此资产的审核：** " + "；".join(href(review_objects[rsha]["id"], "#review-" + rsha) for rsha in sorted(asset_reviews[sha]))])
        lines.append("")

    lines.extend([anchor("reviews"), "## 审核记录", "", "这里展示实际记录的审核方法和范围。不同名字不表明独立性，审核或字节校验也不自动确立研究结论。", ""])
    for rsha, review in sorted(review_objects.items(), key=lambda pair: (pair[1]["target"], pair[0])):
        lines.extend([anchor("review-" + rsha), "### " + label(review["id"]), "", "**目标：** " + record_ref(review["target"]), "",
                      "**决定：** `" + review["decision"] + "`；**覆盖：** " + ", ".join("`" + item + "`" for item in review["coverage"]), "",
                      href("审核 JSON 对象", object_link(rsha)) + " · " + href("重开审核报告", object_link(review["report"])), "",
                      "**方法：** " + review["method"], "", "**范围：** " + review["scope"], "", "**发现：** " + review["findings"], "",
                      "**审核者声明：** `" + json.dumps(review["reviewer"], ensure_ascii=False, sort_keys=True) + "`；**时间：** " + review["created_at"], ""])
        if review["limitations"]:
            lines.extend(["**限制：**", "", *["- " + item for item in review["limitations"]], ""])
        if review["materials"]:
            refs = []
            for material in review["materials"]:
                if material in records or material in excluded:
                    refs.append(record_ref(material))
                elif material in review_objects:
                    refs.append(href(review_objects[material]["id"], "#review-" + material))
                else:
                    refs.append(href(material[:10], "#asset-" + material))
            lines.extend(["**绑定材料：** " + "；".join(refs), ""])
        if review["supersedes"]:
            lines.extend(["**明确替代的审核：** " + "；".join(href(review_objects[old]["id"], "#review-" + old) for old in review["supersedes"]), ""])
        later = [other for other, value in review_objects.items() if rsha in value["supersedes"]]
        if later:
            lines.extend(["**替代本次审核的后续审核：** " + "；".join(href(review_objects[other]["id"], "#review-" + other) for other in sorted(later)), ""])

    lines.extend([anchor("sources"), "## 来源索引", "", "来源保留多方贡献与引用关系；报告的名字或组织不等于已认证身份。", ""])
    for source in sorted(source_records):
        lines.extend([anchor("source-" + source_ids[source]), "### 来源 · " + source_ids[source][:10], "", source, "", "**来源类型：** " + "；".join(sorted(source_kinds[source])), "",
                      "**相关记录：** " + "；".join(record_ref(sha) for sha in sorted(source_records[source])), ""])
    if excluded:
        lines.extend(["## 未提供正文的研究引用", "", "这些定位只保留身份、哈希与排除原因，不能代替正文、审核或可用前提。", ""])
        for sha, row in sorted(excluded.items()):
            lines.extend([anchor("excluded-" + sha), "### " + label(row["id"]), "", "`" + sha + "`；原因：`" + row["reason"] + "`。", ""])
            if row["reason"] == "portable_replacement":
                lines.extend(["历史原始正文未装入；已审可移植修订：" + record_ref(row["replacement"]) + "。原始字节仍按上述身份追溯；替代关系不是新的研究证据。", ""])
            for source, relation, reason in sorted(backward[sha]):
                lines.extend(["- 来自 " + record_ref(source) + " 的 " + relation + "：" + reason, ""])
    body = "\n".join(lines) + "\n"
    return presentation.apply(body) if presentation is not None else body


README = """# Reviewed research exchange

Start with [the research map](research-map.md), then follow exact JSON object and review-report links. The manifest names the source snapshot and the states observed at issuance. An offline copy cannot learn about later corrections automatically.

`manifest.json` lists assets as included or referenced and preserves each record's multiple reported origins. Reported provenance is not authenticated author identity or review authority. Referenced assets are NOT present: use the reviewed summary and locator, and check the expected SHA-256 and byte count after retrieval. Do not infer the validity of a research claim from byte integrity or a recorded PASS. Records marked invalid, historical, or needs_review are not current usable premises.

The packaged Python tools use only the standard library for project storage and exchange. Package verification never executes research programs. Review reports are supplied as evidence of the sender's review; importing them does not make them locally trusted. Excluded-record locators carry only hashes, IDs and an exclusion reason: they preserve correction relationships without delivering unreviewed bodies or making an excluded record a valid premise.

Verification separates canonical research data from human presentation. `canonical_data_verified` covers declared bytes, exact records, review bindings, dependencies and asset availability, not the truth of research claims or authenticated reviewers. `human_map_verified` and `readme_verified` report agreement with the trusted local projection/template. A changed or inconsistent view is marked `needs_rebuild`; do not rely on that provided view. Import with trusted local tools and run `map` to rebuild from canonical records and locally admitted reviews. Provided maps, README text and code are not imported as research evidence, and the original ZIP is unchanged. A different bundled tool inventory does not invalidate research data; declared tool paths and bytes are checked, but code is neither executed nor authenticated.

To open a received ZIP in a separate project (use `python3` where `python` does not name Python 3.10+):

```text
python tools/crs.py init continued-project --title "Continued research" --receive
python tools/crs.py import continued-project received.zip --origin sender --operation receive-001
python tools/crs.py query continued-project --text "record title" --brief
python tools/crs.py map continued-project --out rebuilt-research-map.md
```

To save a new contribution, generate editable forms:

```text
python tools/crs.py template record --kind observation --out new-record.json
python tools/crs.py template intake --out submission.json
```

Fill every required record field, retaining exact scope, limitations, evidence hashes, source snapshot and either a new ID or an explicit previous revision. Put the complete record OBJECT in `submission.json`'s `records` array; set `origin` and map each `assets` hash to its source file path relative to the submission. Keep `foreign_reviews` empty unless attaching third-party reviews. Then use:

```text
python tools/crs.py ingest continued-project --submission submission.json --operation contribute-001
python tools/crs.py template review --out review.json
```

Perform an actual local review, save its findings in `review-report.md`, and fill the review form's exact target, material hashes, coverage and reviewer basis. Use a portable logical reviewer name. Inspect the read-only portability check before submitting; a finding concerns the representation and is not an error in the research content:

```text
python tools/crs.py review continued-project --review review.json --report review-report.md --check-only
python tools/crs.py review continued-project --review review.json --report review-report.md --operation review-001
python tools/crs.py export continued-project --out response.zip
```

`ingest` accepts the contribution submission; `import` accepts an exchange ZIP, not a record JSON file. Never overwrite old objects or use sender reviews to auto-approve new work. Unavailable excluded references are reported as not replayed; inspect the retained target and review report before accepting a correction, without assuming its replacement is established.

No machine-local project paths are included. Any portable source derivative must be reviewed before export; original byte identities are never silently rewritten.
"""


def _evidence_spec(records):
    specs = {}
    for record in records.values():
        for item in record["evidence"]:
            sha = item["sha256"]
            prior = specs.get(sha)
            if prior is not None and prior["bytes"] != item["bytes"]:
                _fail("exchange_asset_size_conflict", "One asset hash has conflicting byte counts.", {"asset": sha})
            if prior is None or (not prior["summary"] or not prior["locator"]) and item["summary"] and item["locator"]:
                specs[sha] = dict(item)
    return specs


def _portable_history_plan(plan, snapshot_sha, snapshot, records, states):
    """Validate explicitly reviewed derivatives, without rewriting source bytes."""
    if plan is None:
        return {}
    if (not isinstance(plan, dict) or set(plan) != {"schema", "snapshot", "replacements"}
            or plan["schema"] != "crs-portable-history/v1" or plan["snapshot"] != snapshot_sha
            or not isinstance(plan["replacements"], list) or not plan["replacements"]):
        _fail("exchange_portable_plan_invalid", "Use a nonempty portable-history plan bound to the current source snapshot.")
    mapping = {}
    for item in plan["replacements"]:
        if not isinstance(item, dict) or set(item) != {"original", "replacement"}:
            _fail("exchange_portable_plan_invalid", "Each replacement binds two exact revisions.")
        old, new = _sha(item["original"]), _sha(item["replacement"])
        if old in mapping or old == new or old not in records or new not in records:
            _fail("exchange_portable_plan_invalid", "Missing, repeated or reflexive portable replacement.")
        mapping[old] = new
    if set(mapping) & set(mapping.values()) or len(set(mapping.values())) != len(mapping):
        _fail("exchange_portable_plan_invalid", "Replacement chains, cycles and merged histories are not supported.")
    if set(mapping) & {snapshot.get("objective"), snapshot.get("objective_binding")}:
        _fail("exchange_portable_objective_unsupported", "The founding objective and bound objective must remain available as exact bodies.")
    for old, new in mapping.items():
        before, after = records[old], records[new]
        if (before["id"] != after["id"] or after["previous"] != old
                or not states[old]["exportable"] or not states[new]["exportable"]
                or not states[new]["usable"] or states[new]["review"] != "accepted"):
            _fail("exchange_portable_replacement_unreviewed", "A portable derivative needs its own applicable review and the exact original identity.")
        _portable_text(after)
        # Only representation/source navigation may differ. The derivative's
        # review remains responsible for action/source semantics and fidelity.
        editable = {"previous", "action", "sources", "evidence", "dependencies", "conditional_on", "correction"}
        if any(before[k] != after[k] for k in set(before) - editable) or set(before) != set(after):
            _fail("exchange_portable_content_changed", "Portable history cannot replace changed research or historical content.")
        if len(before["evidence"]) != len(after["evidence"]):
            _fail("exchange_portable_evidence_changed", "Portable history must retain every exact evidence declaration.")
        for a, b in zip(before["evidence"], after["evidence"]):
            if any(a[k] != b[k] for k in a if k != "locator"):
                _fail("exchange_portable_evidence_changed", "Only the evidence locator may change.")
        dependencies = [dict(d, revision=mapping.get(d["revision"], d["revision"])) for d in before["dependencies"]]
        conditional = [mapping.get(h, h) for h in before["conditional_on"]]
        correction = before["correction"]
        if correction:
            correction = dict(correction, target=mapping.get(correction["target"], correction["target"]),
                              replacement=mapping.get(correction["replacement"], correction["replacement"]))
        if dependencies != after["dependencies"] or conditional != after["conditional_on"] or correction != after["correction"]:
            _fail("exchange_portable_dependencies_changed", "Dependencies may change only through the complete explicit replacement mapping.")
    return {old: {"sha256": old, "id": records[old]["id"], "reason": "portable_replacement", "replacement": new}
            for old, new in mapping.items()}


def _portable_history_materials(records, reviews, excluded, states):
    history = {h for h, row in excluded.items() if row["reason"] == "portable_replacement"}
    if not history:
        return
    from crs_model import _review_materials
    superseded = {h for r in reviews
                  if _review_materials(records[r["target"]]) <= set(r["materials"])
                  for h in r["supersedes"]}
    for h in history:
        target = excluded[h]["replacement"]
        if not states.get(target, {}).get("usable") or not states[target]["exportable"]:
            _fail("exchange_portable_replacement_unreviewed", "Portable replacement is not currently supported by included reviews.")
        from crs_model import _qualified_accept, _review_materials
        faithful = [r for r in reviews if r["target"] == target and h in r["materials"]
                    and {"record_fidelity", "source"} <= set(r["coverage"])
                    and digest(canonical(r)) not in superseded
                    and _review_materials(records[target]) <= set(r["materials"])
                    and _qualified_accept(records[target], r)]
        if not faithful:
            _fail("exchange_portable_fidelity_review_missing", "The retained derivative needs an applicable fidelity/source review bound to its exact original.")
    evidence = {e["sha256"] for r in records.values() for e in r["evidence"]}
    reports = {r["report"] for r in reviews}
    # Background dependency revisions in review.materials are structural links.
    # All other materials must remain real evidence, never omitted aliases.
    nonstructural = set()
    for review in reviews:
        record = records[review["target"]]
        weak = {d["revision"] for d in record["dependencies"] if d["relation"] not in STRONG_RELATIONS}
        previous = record["previous"]
        if (previous in history and excluded[previous]["replacement"] == review["target"]
                and {"record_fidelity", "source"} <= set(review["coverage"])):
            weak.add(previous)
        nonstructural.update(set(review["materials"]) - weak)
    if history & (evidence | reports | nonstructural):
        _fail("exchange_portable_material_missing", "An omitted historical body cannot stand in for required evidence or a report.")


def _export_projection(store, portable_history=None):
    snapshot_sha = store.head()
    snapshot = store.snapshot(snapshot_sha)
    all_records = store.records(snapshot)
    all_reviews = store.reviews(snapshot)
    inherited_exclusions = snapshot.get("excluded_records", [])
    states = assess(all_records, all_reviews, snapshot["selected"], inherited_exclusions)
    # First preserve the ordinary source projection, including its pre-existing
    # closure exclusions. Replacement must not turn those old gaps into a new
    # blocker, or make any additional retained record silently disappear.
    records = {sha: row for sha, row in all_records.items() if states[sha]["exportable"]}
    while True:
        blocked = {sha for sha, row in records.items() if any(dep["relation"] in STRONG_RELATIONS and dep["revision"] not in records for dep in row["dependencies"]) or row["correction"] and row["correction"]["target"] not in records}
        if not blocked:
            break
        records = {sha: row for sha, row in records.items() if sha not in blocked}
    portable_exclusions = _portable_history_plan(portable_history, snapshot_sha, snapshot, all_records, states)
    if any(old not in records or item["replacement"] not in records for old, item in portable_exclusions.items()):
        _fail("exchange_portable_endpoint_outside_projection", "Both portable replacement endpoints must belong to the ordinary source projection.")
    records = {sha: row for sha, row in records.items() if sha not in portable_exclusions}
    # Do not manufacture a usable premise from a reference-only node. Such a
    # dependent can be omitted without preventing unrelated reviewed delivery.
    while True:
        blocked = {sha for sha, row in records.items() if any(dep["relation"] in STRONG_RELATIONS and dep["revision"] not in records for dep in row["dependencies"]) or row["correction"] and row["correction"]["target"] not in records}
        if not blocked:
            break
        if portable_exclusions:
            _fail("exchange_portable_closure_incomplete", "The replacement plan leaves retained strong dependencies or correction targets without bodies.")
        records = {sha: row for sha, row in records.items() if sha not in blocked}
    if not records:
        _fail("exchange_no_reviewed_records", "There are no exportable reviewed research records.")
    retained_ids = {row["id"] for row in records.values()}
    selected = {identity: revision for identity, revision in snapshot["selected"].items() if identity in retained_ids}
    known_excluded = {row["sha256"]: row for row in inherited_exclusions}
    known_excluded.update({sha: {"sha256": sha, "id": row["id"], "reason": "not_exportable"} for sha, row in all_records.items() if sha not in records})
    known_excluded.update(portable_exclusions)
    allowed_locations = _location_refs(records, selected)
    excluded_rows = sorted((known_excluded[sha] for sha in allowed_locations if sha in known_excluded), key=lambda row: row["sha256"])
    excluded = _excluded_records(excluded_rows, records, check_portability=False)
    _dependencies(records, excluded)
    reviews = [row for row in all_reviews if row["target"] in records]
    review_hashes = {digest(canonical(row)) for row in reviews}
    if any(set(row["supersedes"]) - review_hashes for row in reviews):
        _fail("exchange_review_history_missing", "The retained review history is not closed.")
    mentioned = {item["sha256"] for row in records.values() for item in row["evidence"]}
    mentioned.update(sha for row in reviews for sha in row["materials"])
    mentioned.update(row["report"] for row in reviews)
    hidden_material = (mentioned & set(known_excluded)) - set(excluded)
    hidden_material.update(row["report"] for row in reviews if row["report"] in excluded)
    if hidden_material:
        _fail("exchange_unreviewed_material", "An unreviewed research record cannot be exported through an evidence or review-material alias.", {"record_hashes": sorted(hidden_material)})
    statuses = assess(records, reviews, selected, excluded_rows)
    _portable_history_materials(records, reviews, excluded, statuses)
    if any(not statuses[sha]["exportable"] or canonical(statuses[sha]) != canonical(states[sha]) for sha in records):
        _fail("exchange_status_not_preserved", "The reviewed projection does not preserve source evidence and correction states.")
    return snapshot_sha, snapshot, records, reviews, statuses, selected, excluded_rows, excluded, review_hashes


def _path_findings(value, owner, field='$'):
    # Structural field locators only: never return matched private values/keys.
    if isinstance(value, str):
        for match in _local_path_matches(value, formula_prose=not field.endswith(('.locator', '.path'))):
            yield {'code': 'exchange_local_path', 'sha256': owner, 'field': field,
                   'line': value.count('\n', 0, match.start()) + 1,
                   'context': ('locator' if field.endswith(('.locator', '.path')) else
                               'code_or_formula_ambiguous' if any(c in value for c in ('$', '`', r'\(' , r'\[')) else 'path_shaped_text'),
                   'blocking': True, 'automatic_exemption': False}
    elif isinstance(value, dict):
        for index, (key, item) in enumerate(value.items()):
            yield from _path_findings(key, owner, field + '.<key:' + str(index) + '>')
            segment = '.' + key if isinstance(key, str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}', key) else '.<value:' + str(index) + '>'
            yield from _path_findings(item, owner, field + segment)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _path_findings(item, owner, field + '[' + str(index) + ']')


def _export_preflight(snapshot_sha, snapshot, records, reviews, states, selected, excluded_rows, excluded, budget, priorities=None):
    findings = []
    affected = set()
    record_bytes = 0
    for sha, row in records.items():
        issues = list(_path_findings(row, sha))
        findings.extend(issues)
        if issues:
            affected.add(sha)
        size = len(canonical(row)); record_bytes += size
        if size > MAX_JSON_BYTES:
            findings.append({'code': 'exchange_json_limit', 'sha256': sha, 'bytes': size})
    for row in reviews:
        sha = digest(canonical(row))
        findings.extend(_path_findings(row, sha))
        if len(canonical(row)) > MAX_JSON_BYTES:
            findings.append({'code': 'exchange_json_limit', 'sha256': sha})
    retained_origins = [row for row in snapshot['origins'] if row['record'] in records]
    findings.extend(_path_findings({'title': snapshot['title'], 'project_id': snapshot['project_id'],
                                    'selected': selected, 'origins': retained_origins,
                                    'excluded_records': excluded_rows, 'objective_binding': snapshot.get('objective_binding')}, snapshot_sha))
    reverse = {}
    for sha, row in records.items():
        targets = {d['revision'] for d in row['dependencies'] if d['relation'] in STRONG_RELATIONS}
        if row['correction']:
            targets.add(row['correction']['target'])
        for target in targets:
            reverse.setdefault(target, set()).add(sha)
    frontier = list(affected); dependents = set(affected)
    while frontier:
        for sha in reverse.get(frontier.pop(), ()):
            if sha not in dependents:
                dependents.add(sha); frontier.append(sha)
    specs = _evidence_spec(records)
    reports = {r['report'] for r in reviews}
    required = (set(specs) | reports | {s for r in reviews for s in r['materials']}) - set(excluded)
    from crs_parts import rank_assets
    ranking = rank_assets(records, reviews, required, specs, priorities)
    findings.extend(_path_findings(ranking, snapshot_sha))
    for sha, item in specs.items():
        if item['bytes'] > MAX_REFERENCED_BYTES:
            findings.append({'code': 'exchange_invalid_size', 'sha256': sha, 'bytes': item['bytes']})
    # Conservative metadata upper estimate; actual generated payloads are checked
    # before compression. Availability/content/hash checks remain export work.
    asset_estimate = sum(len(canonical(dict(item, availability='referenced', path=_object_name(sha)))) + 70 for sha, item in specs.items())
    manifest_estimate = asset_estimate + len(canonical(states)) + len(canonical(selected)) + len(canonical(excluded_rows)) + len(canonical(snapshot.get('origins', []))) + 160 * (len(records) + len(reviews)) + 8192
    minimum_entries = len(set(records) | {digest(canonical(r)) for r in reviews} | reports) + len(TOOL_NAMES) + 4
    if minimum_entries > MAX_ENTRIES:
        findings.append({'code': 'exchange_entry_limit', 'minimum_entries': minimum_entries})
    mandatory_json_bytes = record_bytes + sum(len(canonical(r)) for r in reviews)
    if mandatory_json_bytes > MAX_EXPANDED_BYTES:
        findings.append({'code': 'exchange_expansion_limit', 'minimum_bytes': mandatory_json_bytes})
    known_reports = sum(specs[s]['bytes'] for s in reports if s in specs)
    if known_reports > budget:
        findings.append({'code': 'exchange_review_report_budget', 'bytes': known_reports})
    return {'ok': not findings, 'preflight_status': 'metadata_blocked' if findings else 'metadata_checked', 'snapshot': snapshot_sha,
            'record_count': len(records), 'review_count': len(reviews), 'asset_count': len(required),
            'record_json_bytes': record_bytes, 'manifest_estimate_bytes': manifest_estimate,
            'manifest_estimate_over_limit': manifest_estimate > MAX_JSON_BYTES,
            'limits': {'json_bytes': MAX_JSON_BYTES, 'referenced_bytes': MAX_REFERENCED_BYTES,
                       'entry_bytes': MAX_ENTRY_BYTES, 'expanded_bytes': MAX_EXPANDED_BYTES, 'entries': MAX_ENTRIES},
            'finding_count': len(findings), 'findings': findings,
            'portable_revision_candidates': sorted(affected),
            'strong_dependency_revision_candidates': sorted(dependents - affected),
            'assurance': 'metadata_only_not_material_verification_or_research_review',
            'remaining_checks': ['actual evidence availability and hashes', 'required report content and budget',
                                 'exact generated map/manifest/namespace size', 'compressed ZIP and final-byte verification'],
            'repair_rule': 'Review each portable revision and its transitive strong dependencies before intake; never rewrite immutable source records or automatically equate changed premises.'}


def _discover_direct_revisions(store, head):
    snapshot = store.snapshot(head)
    records = store.records(snapshot)
    reviews = [store.get_json(h) for h in snapshot['reviews']]
    states = assess(records, reviews, snapshot['selected'], excluded_records=snapshot.get('excluded_records', []))
    pairs = []
    rejected = []
    for new, row in records.items():
        old = row['previous']
        if old not in records or row['id'] != records[old]['id']:
            continue
        if not states[new]['usable'] or not states[new]['exportable'] or states[new]['review'] != 'accepted':
            rejected.append({'original': old, 'replacement': new, 'code': 'exchange_portable_replacement_unreviewed'})
            continue
        if list(_path_findings(records[old], old)) and not list(_path_findings(row, new)):
            pairs.append({'original': old, 'replacement': new})
    plan = {'schema':'crs-portable-history/v1','snapshot':head,'replacements':pairs}
    blocker = None
    if pairs:
        try:
            _export_projection(store, plan)
        except CRSError as error:
            blocker = error.code
    if store.head() != head:
        _fail('delivery_snapshot_changed', 'Source changed while discovering direct revisions.')
    return {'candidate_count':len(pairs), 'candidates':pairs,
            'rejected_count':len(rejected), 'rejected':rejected,
            'validated_portable_history':plan if pairs and blocker is None else None,
            'blocker':blocker, 'research_authority_added':False}


def export_preflight(store, output, max_asset_bytes=460000000, priorities=None, *, portable_history=None):
    from crs_unique import external_target
    _integer(max_asset_bytes, 'max_asset_bytes')
    output = Path(output).absolute()
    external_target(store, output); _no_links(output)
    if output.suffix.lower() != '.json' or not output.parent.is_dir():
        _fail('exchange_output_type', 'Preflight requires a new external JSON report in an existing directory.')
    view = _export_projection(store, portable_history)
    snapshot_sha, snapshot, records, reviews, states, selected, excluded_rows, excluded, _ = view
    result = _export_preflight(snapshot_sha, snapshot, records, reviews, states, selected, excluded_rows, excluded, max_asset_bytes, priorities)
    result['direct_revision_discovery'] = _discover_direct_revisions(store, snapshot_sha)
    # Complete report stays local, create-only; an omitted response cannot require
    # another library scan merely to recover already captured findings.
    with output.open('xb') as handle:
        handle.write(canonical(result))
    return {key: value for key, value in result.items() if key not in {'findings', 'portable_revision_candidates', 'strong_dependency_revision_candidates', 'direct_revision_discovery'}} | {
        'portable_revision_count': len(result['portable_revision_candidates']),
        'strong_dependency_revision_count': len(result['strong_dependency_revision_candidates']),
        'findings_preview': result['findings'][:20], 'findings_complete': len(result['findings']) <= 20,
        'report_written': True, 'report_sha256': _file_digest(output), 'output':str(output),
        'direct_revision_candidate_count':result['direct_revision_discovery']['candidate_count'],
        'direct_revision_reuse_validated':result['direct_revision_discovery']['validated_portable_history'] is not None}


def _payload_limits(payloads):
    if len(payloads) > MAX_ENTRIES:
        _fail('exchange_entry_limit', 'Exchange contains too many members before compression.')
    expanded = 0
    for name, value in payloads.items():
        size = value.stat().st_size if isinstance(value, Path) else len(value)
        _integer(size, 'entry bytes', MAX_ENTRY_BYTES); expanded += size
        if name in {'manifest.json', 'namespace.json'} and size > MAX_JSON_BYTES:
            _fail('exchange_json_limit', 'Generated exchange metadata exceeds its bound before compression.')
    if expanded > MAX_EXPANDED_BYTES:
        _fail('exchange_expansion_limit', 'Exchange expanded bytes exceed the bound before compression.')


def _delivery_receipt(output, result=None, expected_plan=None, terminal='unresolved'):
    result = result or {}
    return {'terminal_state': terminal, 'output': str(output),
            'sha256': result.get('sha256'), 'index_sha256':result.get('index_sha256'),
            'delivery_plan_sha256': result.get('delivery_plan_sha256', expected_plan),
            'source_snapshot': result.get('source_snapshot'),
            'record_count': result.get('record_count'), 'review_count': result.get('review_count'),
            'included_asset_count': result.get('included_asset_count'),
            'referenced_asset_count': result.get('referenced_asset_count'),
            'part_count': result.get('part_count', 1 if terminal == 'success' else None),
            'read_command': ['verify-bundle', str(output)],
            'do_not_retry_automatically': True}


def export_bundle(store, output: Path, max_asset_bytes: int = 460000000, *, max_zip_bytes=512000000, priorities=None, portable_history=None, contract=None, plan=None, expected_plan=None):
    # Acquire the same resource used by operation_io BEFORE material work or
    # compression. Contention is cheap; publication stays under this lease.
    recovery_output = [Path(output).absolute()]
    try:
        with store._lock():
            result = _export_bundle_locked(store, output, max_asset_bytes,
                max_zip_bytes=max_zip_bytes, priorities=priorities,
                portable_history=portable_history, contract=contract,
                plan=plan, expected_plan=expected_plan, _recovery_output=recovery_output)
            result['delivery_receipt'] = _delivery_receipt(result['output'], result, terminal='success')
            return result
    except Exception as error:
        # Null identities mean unavailable, never a guessed hash or rollback.
        error.delivery_receipt = _delivery_receipt(recovery_output[0], expected_plan=expected_plan)
        raise


def _export_bundle_locked(store, output: Path, max_asset_bytes: int = 460000000, *, max_zip_bytes=512000000, priorities=None, portable_history=None, contract=None, plan=None, expected_plan=None, _recovery_output=None):
    output = _output_path(store, output)
    from crs_unique import require_unique, project_scope
    # Verify the selected closure and final delivery; historical source duplication
    # is reported by an explicit audit, not a prerequisite for faithful export.
    _integer(max_asset_bytes, "max_asset_bytes")
    from crs_export_plan import create_plan, prepare_plan, validate_plan, toolset_sha256, material_status
    explicit_plan = plan is not None
    if explicit_plan:
        validate_plan(plan)
        if expected_plan is None or digest(canonical(plan)) != expected_plan:
            _fail('delivery_plan_hash', 'Execution requires the exact expected plan SHA-256.')
        if contract is not None or portable_history is not None or priorities is not None:
            _fail('delivery_plan_options', 'A frozen plan owns its contract, history and ranking options.')
        if plan['toolset_sha256'] != toolset_sha256() or plan['source_snapshot'] != store.head():
            _fail('delivery_plan_stale', 'The source snapshot or exporter changed after planning.')
        max_asset_bytes, max_zip_bytes = plan['asset_budget'], plan['max_zip_bytes']
        portable_history, contract = plan['portable_history'], plan['contract']
        view = _export_projection(store, portable_history)
        if sorted(view[2]) != plan['record_hashes'] or sorted(view[8]) != plan['review_hashes']:
            _fail('delivery_plan_selection', 'Planned research and review coverage differs from the source projection.')
    else:
        plan, view = create_plan(store, max_asset_bytes, max_zip_bytes, priorities, portable_history, contract)
        plan = prepare_plan(store, plan, view)
    if plan['readiness'] != 'ready_for_packaging' or plan['findings']:
        _fail('delivery_plan_blocked', 'Complete the reported material and scope checks before packaging.',
              {'finding_count': len(plan['findings']), 'findings_preview': plan['findings'][:20]})
    snapshot_sha, snapshot, records, reviews, states, selected, excluded_rows, excluded, review_hashes = view
    def unchanged():
        if store.head() != snapshot_sha:
            _fail('delivery_snapshot_changed', 'The source changed during export; no final delivery was published.')
    @contextmanager
    def publication_guard():
        # The public export entry holds the store lock for this entire call.
        unchanged()
        yield
    unchanged()
    payloads = {}
    for sha, row in records.items():
        validate_record(row)
        raw = canonical(row)
        if digest(raw) != sha:
            _fail("exchange_record_hash", "Source research record does not match its hash.")
        payloads[_object_name(sha)] = raw
    for row in reviews:
        validate_review(row)
        raw = canonical(row)
        payloads[_object_name(digest(raw))] = raw
    specs = _evidence_spec(records)
    reports = {row["report"] for row in reviews}
    materials = {sha for row in reviews for sha in row["materials"]}
    required = (set(specs) | reports | materials) - set(excluded)
    assets = {}
    referenced_assets = []
    spent = 0
    from crs_parts import rank_assets, LIMIT
    if type(max_zip_bytes) is not int or not 1024<=max_zip_bytes<=LIMIT:
        _fail('export_part_limit','ZIP limit must be 1,024..512,000,000 bytes.')
    ranking = plan['ranking']
    structural = set(records) | set(review_hashes)
    if {a['sha256'] for a in plan['assets']} != required - structural:
        _fail('delivery_plan_inventory', 'The planned inventory must match every required source evidence identity.')
    forced = reports
    from crs_export_plan import mandatory_assets
    forced = forced | mandatory_assets(contract)
    paths = {}
    for planned in plan['assets']:
        sha = planned['sha256']
        item = specs.get(sha)
        include = planned['include']
        size = planned['bytes']
        if item is not None and size != item['bytes']:
            _fail('delivery_plan_inventory', 'Planned size differs from reviewed source metadata.', {'sha256': sha})
        if sha in forced and not include:
            _fail('delivery_required_material_missing', 'Required materials cannot be omitted by an execution plan.', {'sha256': sha})
        if not include and (item is None or not item['summary'] or not item['locator']):
            _fail('exchange_asset_reference_missing', 'An omitted asset needs an exact reviewed summary and locator.', {'asset': sha})
        source = None
        if include:
            source = Path(store.resolve(sha))
            if source.stat().st_size != size:
                _fail('exchange_asset_size_conflict', 'Selected source material changed size.', {'asset': sha})
            _portable_file(source)
            paths[sha] = source
            spent += size
            if spent > max_asset_bytes:
                _fail('delivery_plan_budget', 'The frozen selection exceeds its declared asset budget.')
            payloads[_object_name(sha)] = source
        asset = {'sha256': sha, 'bytes': size, 'availability': 'included' if include else 'referenced',
                 'path': _object_name(sha) if include else None,
                 'name': item['name'] if item else 'review-report' if sha in reports else 'review-material',
                 'role': item['role'] if item else 'report' if sha in reports else 'review-material',
                 'summary': item['summary'] if item else 'Required review material.',
                 'locator': item['locator'] if item else ''}
        _portable_text(asset)
        assets[sha] = asset
        if not include:
            referenced_assets.append({'sha256': sha, 'reason': planned['reason'], 'bytes_verified_this_export': False})
    coverage = material_status(contract, assets, records, paths)
    if contract is not None and contract['scope'] == 'offline_reproduction' and not coverage['material_groups_complete']:
        _fail('delivery_materials_incomplete', 'An offline material declaration remains incomplete.')
    tools = []
    for name in TOOL_NAMES:
        source = Path(__file__).resolve().parent / name
        if not source.is_file():
            _fail("exchange_tools_missing", "The portable tool bundle is incomplete.", {"tool": name})
        _no_links(source)
        payloads["tools/" + name] = source
        tools.append("tools/" + name)
    payloads["README.md"] = README.encode("utf-8")
    origins = _origins(sorted((row for row in snapshot["origins"] if row["record"] in records), key=lambda row: (row["record"], row["source"])), records)
    payloads["research-map.md"] = render_map(records, reviews, selected, snapshot["title"], origins=origins, excluded_records=excluded_rows, objective=snapshot["objective"], objective_binding=snapshot.get("objective_binding"), asset_paths={sha: item["path"] for sha, item in assets.items()}).encode("utf-8")
    manifest = {"schema": EXCHANGE_SCHEMA, "source_snapshot": {"sha256": snapshot_sha, "project_id": snapshot["project_id"], "title": snapshot["title"], "objective": snapshot["objective"], "objective_binding": snapshot.get("objective_binding")}, "record_hashes": sorted(records), "selected": selected, "reviews": sorted(review_hashes), "assets": assets, "statuses": states, "origins": origins, "excluded_records": excluded_rows, "namespace": "namespace.json", "tools": tools, "assurance": ASSURANCE}
    if explicit_plan or contract is not None:
        # A planned delivery binds its exact plan; the schema is unchanged.
        manifest['delivery_plan'] = {'path': 'delivery-plan.json', 'sha256': digest(canonical(plan))}
        payloads['delivery-plan.json'] = canonical(plan)
    _portable_text(manifest)
    payloads["manifest.json"] = canonical(manifest)
    namespace = []
    for name, value in sorted(payloads.items()):
        _filename(name)
        if isinstance(value, Path):
            _portable_file(value)
            size, sha = value.stat().st_size, _file_digest(value)
        else:
            size, sha = len(value), digest(value)
        if name.startswith("objects/") and sha != name.split("/")[1]:
            _fail("exchange_consumption_hash", "Asset changed before export consumption.")
        namespace.append({"path": name, "sha256": sha, "bytes": size})
    actual_toolset = {Path(row["path"]).name: row["sha256"] for row in namespace if row["path"] in tools}
    if digest(canonical(actual_toolset)) != plan["toolset_sha256"]:
        _fail("delivery_plan_stale", "Bundled tools changed after delivery planning.")
    payloads["namespace.json"] = canonical({"schema": "crs-namespace/v1", "files": namespace})
    _payload_limits(payloads)
    handle, temp_name = tempfile.mkstemp(prefix=".crs-exchange-", suffix=".zip", dir=output.parent)
    os.close(handle)
    temporary = Path(temp_name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
            for name, value in sorted(payloads.items()):
                info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                with archive.open(info, "w", force_zip64=True) as target:
                    if isinstance(value, Path):
                        with value.open("rb") as source:
                            for block in iter(lambda: source.read(1024 * 1024), b""):
                                target.write(block)
                    else:
                        target.write(value)
        result = verify_bundle(temporary)
        unchanged()
        if temporary.stat().st_size > max_zip_bytes:
            from crs_parts import split_bundle
            destination=output.with_suffix('.parts')
            if _recovery_output is not None:
                _recovery_output[0] = destination
            result=split_bundle(temporary,destination,ranking,max_zip_bytes,publication_guard=publication_guard)
            return {**result,'status':'exchange_ready','asset_bytes_included':spent,'referenced_assets':referenced_assets,'delivery_plan_sha256':digest(canonical(plan)),'material_coverage':coverage,'referenced_bytes_verified':False}
        require_unique(temporary)
        _no_links(output.parent)
        try:
            with publication_guard():
                os.link(temporary, output)
        except FileExistsError:
            _fail("exchange_output_exists", "Exchange output was created concurrently.")
        except OSError as exc:
            _fail("exchange_publish_failed", "Unable to publish the complete archive without overwriting another file.", {"error": type(exc).__name__})
        return {"delivery_plan_sha256":digest(canonical(plan)), "material_coverage":coverage, "referenced_bytes_verified":False, **result, "status": "exchange_ready", "output": str(output), "sha256": _file_digest(output), "asset_bytes_included": spent, "referenced_assets": referenced_assets, "part_limit_bytes":max_zip_bytes,"ranking_preview":ranking[:10],"ranking_count":len(ranking),"ranking_basis":"Required reports first, then importance and future research relevance; explicit overrides take precedence."}
    finally:
        temporary.unlink(missing_ok=True)


class _ArchiveMember:
    """Read only the exact member already admitted by this archive session."""

    def __init__(self, archive, entries, name):
        self._archive = archive
        self._entries = entries
        self._name = name

    def _entry(self):
        try:
            return self._entries[self._name]
        except KeyError:
            raise FileNotFoundError(self._name) from None

    def stat(self):
        return SimpleNamespace(st_size=self._entry().file_size)

    def open(self, mode="rb"):
        if mode != "rb":
            raise ValueError("Archive members support binary reads only.")
        return self._archive.open(self._entry(), "r")

    def read_bytes(self):
        with self.open() as stream:
            return stream.read()

    def read_text(self, encoding="utf-8"):
        with TextIOWrapper(self.open(), encoding=encoding) as stream:
            return stream.read()


class _ArchiveView:
    """Invocation-local access to the admitted ZipInfo objects, not a Path."""

    def __init__(self, archive, entries):
        self._archive = archive
        self._entries = {entry.filename: entry for entry in entries}

    def __truediv__(self, name):
        return _ArchiveMember(self._archive, self._entries, name)


@contextmanager
def _extracted(path, *, member_stream=False):
    if Path(path).is_dir():
        from crs_parts import materialized
        with materialized(path) as restored:
            with _extracted(restored, member_stream=member_stream) as result:
                yield result
        return
    from crs_unique import require_unique
    require_unique(Path(path))
    path = Path(path)
    _no_links(path)
    if not path.is_file() or path.stat().st_size > MAX_ARCHIVE_BYTES:
        _fail("exchange_archive_limit", "Expected a bounded exchange ZIP file.")
    # Bound the central directory before ZipFile allocates its member objects.
    with path.open("rb") as stream:
        if stream.read(4) != b"PK\x03\x04":
            _fail("exchange_invalid_zip", "Expected a plain ZIP without an executable prefix.")
        stream.seek(max(0, path.stat().st_size - 65557))
        tail = stream.read()
    at = tail.rfind(b"PK\x05\x06")
    if at < 0 or len(tail) - at < 22:
        _fail("exchange_invalid_zip", "ZIP end-of-directory is missing.")
    _, disk, directory_disk, disk_entries, entries_count, directory_bytes, directory_offset, comment_bytes = struct.unpack("<4s4H2LH", tail[at:at + 22])
    if disk or directory_disk or disk_entries != entries_count or entries_count > MAX_ENTRIES or directory_bytes > MAX_ENTRIES * 4096 or len(tail) != at + 22 + comment_bytes or directory_offset + directory_bytes > path.stat().st_size:
        _fail("exchange_directory_limit", "ZIP directory exceeds bounds or uses unsupported multi-volume/ZIP64 metadata.")
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ENTRIES:
                _fail("exchange_entry_limit", "Exchange contains too many members.")
            names, folded, expanded = set(), set(), 0
            for entry in entries:
                name = _filename(entry.filename)
                mode = (entry.external_attr >> 16) & 0xffff
                if entry.is_dir() or stat.S_ISLNK(mode) or stat.S_IFMT(mode) not in {0, stat.S_IFREG} or entry.flag_bits & 1:
                    _fail("exchange_member_type", "Directories, links, special files and encrypted entries are not accepted.")
                if name in names or name.casefold() in folded:
                    _fail("exchange_duplicate_member", "Duplicate or case-colliding ZIP members are not accepted.")
                names.add(name)
                folded.add(name.casefold())
                _integer(entry.file_size, "entry bytes", MAX_ENTRY_BYTES)
                expanded += entry.file_size
                if expanded > MAX_EXPANDED_BYTES or entry.file_size > max(1, entry.compress_size) * MAX_COMPRESSION_RATIO:
                    _fail("exchange_expansion_limit", "Exchange expansion exceeds its bounded limits.")
            with crs_temp.TemporaryDirectory(prefix="crs-exchange-verify-") as temporary:
                root = Path(temporary)
                for entry in entries:
                    destination = root.joinpath(*PurePosixPath(entry.filename).parts)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    count = 0
                    with archive.open(entry) as source, destination.open("xb") as target:
                        for block in iter(lambda: source.read(1024 * 1024), b""):
                            count += len(block)
                            if count > entry.file_size:
                                _fail("exchange_expansion_limit", "Expanded member exceeds its declared size.")
                            target.write(block)
                    if count != entry.file_size:
                        _fail("exchange_member_size", "Expanded member differs from its declared size.")
                yield (_ArchiveView(archive, entries) if member_stream else root), names
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError) as exc:
        _fail("exchange_invalid_zip", "Exchange ZIP is invalid or unsupported.", {"error": type(exc).__name__})


def _verify_extracted(root, names):
    if not {"namespace.json", "manifest.json", "README.md", "research-map.md"} <= names:
        _fail("exchange_namespace_missing", "Required exchange files are missing.")
    namespace = _read_json(root / "namespace.json")
    if not isinstance(namespace, dict) or set(namespace) != {"schema", "files"} or namespace["schema"] != "crs-namespace/v1" or not isinstance(namespace["files"], list):
        _fail("exchange_namespace_invalid", "Invalid namespace manifest.")
    declared = set()
    for row in namespace["files"]:
        if not isinstance(row, dict) or set(row) != {"path", "sha256", "bytes"}:
            _fail("exchange_namespace_invalid", "Invalid namespace row.")
        name = _filename(row["path"])
        if name in declared or name == "namespace.json":
            _fail("exchange_namespace_invalid", "Duplicate or self-referential namespace row.")
        declared.add(name)
        _sha(row["sha256"])
        _integer(row["bytes"], "namespace bytes", MAX_ENTRY_BYTES)
        target = root / name
        if name not in names or target.stat().st_size != row["bytes"] or _file_digest(target) != row["sha256"]:
            _fail("exchange_namespace_hash", "Exchange member does not match its declared bytes.", {"member": name})
        _portable_file(target)
    if names != declared | {"namespace.json"}:
        _fail("exchange_unknown_file", "Exchange contains undeclared files.")
    manifest = _read_json(root / "manifest.json")
    if not isinstance(manifest, dict) or set(manifest) not in (MANIFEST_KEYS, MANIFEST_KEYS | {"delivery_plan"}) or manifest.get("schema") != EXCHANGE_SCHEMA or manifest["namespace"] != "namespace.json":
        _fail("exchange_invalid_manifest", "Invalid exchange manifest.")
    _portable_text(manifest)
    source = manifest["source_snapshot"]
    if not isinstance(source, dict) or set(source) not in ({"sha256", "project_id", "title", "objective"}, {"sha256", "project_id", "title", "objective", "objective_binding"}) or not all(isinstance(source[k], str) and source[k] for k in ("project_id", "title")):
        _fail("exchange_snapshot_invalid", "Invalid source snapshot locator.")
    _sha(source["sha256"])
    if source["objective"] is not None:
        _sha(source["objective"])
    if source.get("objective_binding") is not None:
        _sha(source["objective_binding"])
    records, reviews = {}, []
    expected = {"README.md", "research-map.md", "manifest.json", "namespace.json"}
    for sha in _unique_hashes(manifest["record_hashes"], "record_hashes"):
        name = _object_name(sha)
        if name not in names or _file_digest(root / name) != sha:
            _fail("exchange_record_missing", "A declared research record is missing or corrupt.")
        row = _read_json(root / name)
        validate_record(row)
        if digest(canonical(row)) != sha:
            _fail("exchange_record_canonical", "Research JSON must preserve its canonical identity.")
        _portable_text(row)
        records[sha] = row
        expected.add(name)
    if not records:
        _fail("exchange_no_reviewed_records", "An exchange must contain reviewed research records.")
    primary = records.get(source["objective"])
    described = records.get(source.get("objective_binding"))
    if primary is not None and primary["kind"] != "objective":
        _fail("exchange_objective_invalid", "The declared founding research question is not an objective.")
    if described is not None and (not objective_complete(described) or
                                  (primary is not None and not objective_compatible(primary, described))):
        _fail("exchange_objective_invalid", "The reported completed description changes the founding question.")
    _origins(manifest["origins"], records)
    excluded = _excluded_records(manifest["excluded_records"], records)
    if any(r["reason"] == "portable_replacement" and h in {source.get("objective"), source.get("objective_binding")}
           for h, r in excluded.items()):
        _fail("exchange_portable_objective_unsupported", "Founding and bound objectives cannot be omitted as portable history.")
    if set(excluded) - _location_refs(records, manifest["selected"]) or any(_object_name(sha) in names for sha in excluded):
        _fail("exchange_exclusions_invalid", "Excluded research references cannot introduce unrelated nodes or deliver unreviewed record bodies.")
    for sha in _unique_hashes(manifest["reviews"], "reviews"):
        name = _object_name(sha)
        if name not in names or _file_digest(root / name) != sha:
            _fail("exchange_review_missing", "A declared review is missing or corrupt.")
        row = _read_json(root / name)
        validate_review(row)
        if digest(canonical(row)) != sha or row["target"] not in records:
            _fail("exchange_review_binding", "Review is not bound to retained research.")
        _portable_text(row)
        if set(row["supersedes"]) - set(manifest["reviews"]):
            _fail("exchange_review_history_missing", "Review supersession is not closed.")
        reviews.append(row)
        expected.add(name)
    _dependencies(records, excluded)
    states = assess(records, reviews, manifest["selected"], manifest["excluded_records"])
    _portable_history_materials(records, reviews, excluded, states)
    if canonical(states) != canonical(manifest["statuses"]) or any(not state["exportable"] for state in states.values()):
        _fail("exchange_status_mismatch", "Exchange states do not follow its exact records and reviews.")
    specs = _evidence_spec(records)
    reports = {row["report"] for row in reviews}
    materials = {sha for row in reviews for sha in row["materials"]}
    if reports & set(excluded):
        _fail("exchange_review_report_missing", "Review reports cannot be replaced by excluded research locators.")
    structural = set(records) | set(manifest["reviews"]) | set(excluded)
    required = (set(specs) | reports | materials) - structural
    assets = manifest["assets"]
    if not isinstance(assets, dict) or set(assets) != required:
        _fail("exchange_asset_inventory", "Asset inventory does not match reviewed evidence and review materials.")
    for sha, item in assets.items():
        keys = {"sha256", "bytes", "availability", "path", "name", "role", "summary", "locator"}
        if not isinstance(item, dict) or set(item) != keys or item["sha256"] != sha:
            _fail("exchange_asset_inventory", "Invalid asset availability record.")
        _sha(sha)
        _integer(item["bytes"], "asset bytes", maximum=MAX_REFERENCED_BYTES if item["availability"] == "referenced" else MAX_EXPANDED_BYTES)
        if not all(isinstance(item[key], str) for key in ("name", "role", "summary", "locator")):
            _fail("exchange_asset_inventory", "Invalid asset metadata.")
        original = specs.get(sha)
        if original is not None and any(item[key] != original[key] for key in ("bytes", "name", "role", "summary", "locator")):
            _fail("exchange_unreviewed_summary", "Asset summaries and locators must match reviewed records exactly.")
        if item["availability"] == "included":
            name = _object_name(sha)
            if item["path"] != name or name not in names or (root / name).stat().st_size != item["bytes"] or _file_digest(root / name) != sha:
                _fail("exchange_asset_missing", "Included asset is missing or corrupt.", {"asset": sha})
            expected.add(name)
        elif item["availability"] == "referenced":
            if item["path"] is not None or sha in reports or original is None or not item["summary"] or not item["locator"] or _object_name(sha) in names:
                _fail("exchange_asset_reference_invalid", "Referenced asset lacks a reviewed portable reference or incorrectly replaces a required report.")
        else:
            _fail("exchange_asset_availability", "Unknown asset availability state.")
    # Bundled software is hashed inert payload, not the verifier's own runtime.
    # A local module addition must not invalidate a package built with another tool set.
    tool_paths = manifest["tools"]
    if not isinstance(tool_paths, list) or not tool_paths or not all(isinstance(name, str) for name in tool_paths):
        _fail("exchange_tool_inventory", "Expected a declared portable tool list.")
    if len(set(tool_paths)) != len(tool_paths):
        _fail("exchange_tool_inventory", "Duplicate portable tool declaration.")
    for name in tool_paths:
        _filename(name)
        path = PurePosixPath(name)
        if path.parent != PurePosixPath("tools") or path.suffix != ".py":
            _fail("exchange_tool_inventory", "Declared Python tools must be ordinary files in the tools directory.")
    expected.update(tool_paths)
    delivery_coverage = None
    if 'delivery_plan' in manifest:
        from crs_export_plan import validate_plan, validate_contract, material_status
        declaration = manifest['delivery_plan']
        if not isinstance(declaration, dict) or set(declaration) != {'path', 'sha256'} or declaration['path'] != 'delivery-plan.json':
            _fail('delivery_plan_invalid', 'Invalid package delivery-plan binding.')
        _sha(declaration['sha256'])
        plan = _read_json(root / 'delivery-plan.json')
        validate_plan(plan)
        if digest(canonical(plan)) != declaration['sha256'] or plan['source_snapshot'] != source['sha256']:
            _fail('delivery_plan_hash', 'Package plan does not match its declared identity and source.')
        if plan['record_hashes'] != sorted(records) or plan['review_hashes'] != sorted(manifest['reviews']):
            _fail('delivery_plan_selection', 'Package records differ from the declared plan.')
        if plan['readiness'] != 'ready_for_packaging' or plan['findings']:
            _fail('delivery_plan_blocked', 'A package cannot claim execution of a blocked plan.')
        planned = {row['sha256']: row for row in plan['assets']}
        if set(planned) != set(assets) or any(planned[sha]['include'] != (a['availability'] == 'included') or planned[sha]['bytes'] != a['bytes'] for sha, a in assets.items()):
            _fail('delivery_plan_inventory', 'Actual package materials differ from the plan.')
        if plan['contract'] is not None:
            validate_contract(plan['contract'], records, specs)
        bundled_toolset = {PurePosixPath(name).name: _file_digest(root / name) for name in tool_paths}
        if digest(canonical(bundled_toolset)) != plan['toolset_sha256']:
            _fail('delivery_plan_stale', 'Actual bundled tools differ from the saved plan.')
        material_paths = {sha: root / item['path'] for sha, item in assets.items() if item['availability'] == 'included'}
        delivery_coverage = material_status(plan['contract'], assets, records, material_paths)
        from crs_export_plan import mandatory_assets
        if mandatory_assets(plan['contract']) - set(material_paths):
            _fail('delivery_materials_incomplete', 'Declared nonoptional material is absent from package bytes.')
        if plan['scope'] == 'offline_reproduction' and not delivery_coverage['material_groups_complete']:
            _fail('delivery_materials_incomplete', 'Required offline materials are absent or unknown.')
        expected.add('delivery-plan.json')
    if names != expected:
        _fail("exchange_unknown_file", "Exchange contains unrecognized or missing files.")
    expected_map = render_map(records, reviews, manifest["selected"], source["title"], origins=manifest["origins"], excluded_records=manifest["excluded_records"], objective=source["objective"], objective_binding=source.get("objective_binding"), asset_paths={sha: item["path"] for sha, item in assets.items()}).encode("utf-8")
    # Human views are disposable projections. Their namespace hashes were
    # checked above; template equality is a separate, explicitly scoped claim.
    # Never import a provided view as research evidence or execute package code.
    provided_map = (root / "research-map.md").read_bytes()
    provided_readme = (root / "README.md").read_bytes()
    expected_readme = README.encode("utf-8")
    map_matches = provided_map == expected_map
    readme_matches = provided_readme == expected_readme
    if manifest["assurance"] != ASSURANCE:
        _fail("exchange_assurance_mismatch", "Exchange verification cannot claim evidence replay.")
    result = {"status": "exchange_verified", "source_snapshot": source["sha256"], "record_count": len(records), "review_count": len(reviews), "origin_count": len(manifest["origins"]), "excluded_record_count": len(excluded), "included_asset_count": sum(a["availability"] == "included" for a in assets.values()), "referenced_asset_count": sum(a["availability"] == "referenced" for a in assets.values()), "usable_record_count": sum(s["usable"] for s in states.values()), "evidence_replay_performed": False, "review_identity_authenticated": False, "provenance_identity_authenticated": False}
    result.update({
        "canonical_data_verified": True,
        "verification_scope": "namespace_bytes_canonical_records_review_bindings_dependency_states_and_asset_availability",
        "human_map_verified": map_matches,
        "human_map": {"status": "matches_current_projection" if map_matches else "needs_rebuild",
                      "provided_sha256": digest(provided_map), "rebuilt_sha256": digest(expected_map),
                      "verification_scope": "matches_local_projection_only_not_claim_truth"},
        "readme_verified": readme_matches,
        "readme": {"status": "matches_current_template" if readme_matches else "needs_rebuild",
                   "provided_sha256": digest(provided_readme), "rebuilt_sha256": digest(expected_readme),
                   "verification_scope": "matches_local_usage_template_only"},
        "provided_human_views_are_authority": False,
        "bundled_tool_count": len(tool_paths),
        "bundled_code_executed": False,
        "bundled_code_authenticated": False,
    })
    if delivery_coverage is not None:
        result["material_coverage"] = delivery_coverage
    return result, manifest, records, reviews


def verify_bundle(path: Path):
    """Verify canonical research; report view equality separately; execute no code."""
    identity = Path(path) / 'parts.json' if Path(path).is_dir() else Path(path)
    _no_links(identity)
    identity_limit = 32 * 1024**2 if Path(path).is_dir() else MAX_ARCHIVE_BYTES
    if not identity.is_file() or identity.stat().st_size > identity_limit:
        _fail('export_parts_limit' if Path(path).is_dir() else 'exchange_archive_limit', 'Delivery identity exceeds its file or byte limit.')
    identity_sha = _file_digest(identity)
    with _extracted(path, member_stream=True) as (root, names):
        result, manifest, _, _ = _verify_extracted(root, names)
        result['output'] = str(Path(path).absolute())
        if _file_digest(identity) != identity_sha:
            _fail('exchange_consumption_hash', 'Delivery changed while it was being verified.')
        if Path(path).is_dir():
            index = parse_json(identity.read_bytes())
            result.update(sha256=None, index_sha256=identity_sha, part_count=len(index['parts']))
        else:
            result['sha256'] = identity_sha
        if 'delivery_plan' in manifest:
            result['delivery_plan_sha256'] = digest(canonical(parse_json((root / 'delivery-plan.json').read_bytes())))
        result['delivery_receipt'] = _delivery_receipt(path, result, terminal='success')
        return result


def import_bundle(store, path, origin, operation_id, expected_head=None):
    """Import canonical records and foreign reviews, never the provided views.

    Rebuild presentation using the local map command and locally admitted reviews.
    A source view mismatch neither authenticates its text nor rejects intact data.
    """
    with _extracted(path) as (root, names):
        result, manifest, records, reviews = _verify_extracted(root, names)
        assets = {sha: root / item["path"] for sha, item in manifest["assets"].items() if item["availability"] == "included"}
        committed = store.ingest(records=list(records.values()), assets=assets, origin=origin, operation_id=operation_id, expected_head=expected_head, foreign_reviews=reviews, source_origins=manifest["origins"], excluded_records=manifest["excluded_records"], source_selected=manifest["selected"], source_objective=manifest["source_snapshot"]["objective"])
        return {**committed, "exchange_verification": result, "source_snapshot": manifest["source_snapshot"]["sha256"], "foreign_review_count": len(reviews), "reviews_automatically_trusted": False, "evidence_replay_performed": False,
                "provided_human_views_imported": False,
                "local_display": {"status": "rebuild_from_imported_canonical_records",
                                  "command": "map PROJECT --out FRESH_MAP_FILE",
                                  "review_basis": "locally_admitted_reviews"}}


_verify_bundle_data = verify_bundle
