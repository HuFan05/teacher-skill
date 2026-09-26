"""Validate the glossary, registry, code fields, and documented commands."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
TERM_KEYS = {"canonical_id", "display_name_zh", "semantic_layer", "authority_class", "constitutive_fields", "aliases", "deprecated_aliases", "reserved_conflicts", "change_policy", "definition_ref", "introduced_in", "visibility", "relations", "history"}
DEFINITION_LABELS = ('简要定义', '规范定义', '构成字段', '权威等级', '生命周期规则', '允许的变化', '禁止的变化', '不得混淆', '完成关系', '机器绑定')


def validate(root):
    root = Path(root).resolve()
    sys.path.insert(0, str(root / "scripts"))
    from crs_model import parse_json, _RECORD_FIELDS, _REVIEW_FIELDS, OBJECTIVE_FIELDS
    import crs
    paths = [root / "SKILL.md", root / "references/terminology.md", root / "references/terminology-registry.json", root / "references/data-contract.md", root / "references/workflows.md", root / "agents/openai.yaml"]
    for path in paths:
        if not path.is_file():
            raise ValueError("Missing required document: " + str(path.relative_to(root)))
    registry = parse_json(paths[2].read_bytes())
    if set(registry) != {"schema_version", "skill_id", "terms"} or registry["schema_version"] != "skill-terminology-registry/v1" or registry["skill_id"] != "teacher:cs-ai-research-solve":
        raise ValueError("Use the closed skill-terminology-registry/v1 root")
    if not isinstance(registry["terms"], list) or not registry["terms"]:
        raise ValueError("Define the current research-data concepts")
    definitions = paths[1].read_text(encoding="utf-8")
    ids, aliases = set(), set()
    for term in registry["terms"]:
        if not isinstance(term, dict) or set(term) != TERM_KEYS:
            raise ValueError("Term fields do not match the closed registry contract")
        term_id = term["canonical_id"]
        if not isinstance(term_id, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", term_id) or term_id in ids:
            raise ValueError("Canonical IDs must be unique lowercase snake_case")
        ids.add(term_id)
        for key in ("display_name_zh", "semantic_layer", "authority_class", "change_policy", "introduced_in"):
            if not isinstance(term[key], str) or not term[key].strip():
                raise ValueError("Missing text: " + term_id + "." + key)
        for key in ("constitutive_fields", "aliases", "deprecated_aliases", "reserved_conflicts", "history"):
            values = term[key]
            if not isinstance(values, list) or not all(isinstance(v, str) and v.strip() for v in values) or len(values) != len(set(values)):
                raise ValueError("Invalid string list: " + term_id + "." + key)
        if not term["constitutive_fields"] or not term["history"]:
            raise ValueError("Each term needs constitutive fields and explicit history")
        if term["visibility"] not in {"local", "exported"} or not isinstance(term["relations"], list):
            raise ValueError("Invalid visibility or relations for " + term_id)
        for label in [term["display_name_zh"], *term["aliases"]]:
            key = label.casefold()
            if key in aliases:
                raise ValueError("Colliding active term labels: " + label)
            aliases.add(key)
        expected_ref = "references/terminology.md#" + term_id
        anchor = '<a id="' + term_id + '"></a>'
        if term["definition_ref"] != expected_ref or definitions.count(anchor) != 1:
            raise ValueError("Missing unique definition anchor for " + term_id)
        section = definitions.split(anchor, 1)[1].split('<a id="', 1)[0]
        if any(not re.search(r"(?m)^###\s+" + re.escape(label) + r"\s*$", section) for label in DEFINITION_LABELS):
            raise ValueError("Incomplete normative definition for " + term_id)
        if any("`" + field + "`" not in section for field in term["constitutive_fields"]):
            raise ValueError("Registry fields disagree with definition for " + term_id)
    documented_ids = set(re.findall(r'<a id="([a-z][a-z0-9_]*)"></a>', definitions))
    if documented_ids != ids:
        raise ValueError("Glossary and registry concept sets disagree")
    by_id = {t["canonical_id"]: t for t in registry["terms"]}
    for term_id, allowed in (("research_record", _RECORD_FIELDS), ("review_record", _REVIEW_FIELDS)):
        if term_id not in by_id or not set(by_id[term_id]["constitutive_fields"]) <= allowed:
            raise ValueError("Term fields do not bind to the live model: " + term_id)
    objective = by_id.get("project_objective", {})
    if (set(objective.get("constitutive_fields", [])) != OBJECTIVE_FIELDS
            or objective.get("authority_class") != "immutable_research_identity"
            or objective.get("change_policy") != "semantic_change_requires_new_project_or_explicit_fork"):
        raise ValueError("Project objective terminology must bind to the immutable live six-field identity")
    contract = paths[3].read_text(encoding="utf-8")
    missing = [field for field in _RECORD_FIELDS | _REVIEW_FIELDS if "`" + field + "`" not in contract]
    if missing:
        raise ValueError("Data contract omits live required fields: " + ", ".join(sorted(missing)))
    entry = paths[0].read_text(encoding="utf-8")
    if "version: " + crs.VERSION not in entry:
        raise ValueError("SKILL version differs from the live CLI")
    if len(entry.splitlines()) > 180:
        raise ValueError("Keep the main skill entry within 180 lines")
    cli_parser = crs.parser()
    commands = next(action.choices for action in cli_parser._actions if isinstance(action, argparse._SubParsersAction))
    workflow = paths[4].read_text(encoding="utf-8")
    found = re.findall(r"python -B \$crsCli (?:--workflow \S+ )?([a-z][a-z-]*)([^\n]*)", workflow)
    for command, tail in found:
        if command not in commands:
            raise ValueError("Documented command is not implemented: " + command)
        flags = set(re.findall(r"(?<!\w)--[a-z][a-z0-9-]*", tail))
        allowed = {flag for action in commands[command]._actions for flag in action.option_strings}
        if not flags <= allowed:
            raise ValueError("Unsupported documented flags for " + command + ": " + str(sorted(flags - allowed)))
    if not {"init", "ingest", "review", "query", "map", "export", "import", "recover", "replay"} <= {command for command, _ in found}:
        raise ValueError("Workflows omit a primary implemented operation")
    ui = paths[5].read_text(encoding="utf-8")
    if "$cs-ai-research-solve" not in ui:
        raise ValueError("UI prompt must invoke the research-data Skill")
    for path in paths:
        if path.suffix == ".md":
            text = path.read_text(encoding="utf-8")
            for target in re.findall(r"\]\(([^)]+)\)", text):
                file_part = target.split("#", 1)[0]
                if file_part and "://" not in file_part and not (path.parent / file_part).is_file():
                    raise ValueError("Broken local reference: " + target)
    return {"ok": True, "code": "terminology_valid", "status": "terminology_valid", "version": crs.VERSION, "term_count": len(ids), "documented_command_count": len({command for command, _ in found}), "registry_sha256": hashlib.sha256(paths[2].read_bytes()).hexdigest(), "semantic_review_performed": False, "research_verification_performed": False}


# Resolve sibling support for both direct CLI and file-path module loading.
# The support code belongs to this validator, not the root being checked.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from crs_output import SafeParser, add_output_arguments, configure_output, emit_result, public_main


@public_main
def main():
    parser = SafeParser(description=__doc__)
    add_output_arguments(parser)
    parser.add_argument("--skill-root", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    configure_output(args)
    try:
        result = validate(args.skill_root)
    except (ValueError, OSError, KeyError, TypeError, StopIteration) as exc:
        result = {"ok": False, "status": "blocked", "code": "terminology_contract_invalid", "message": "Skill terminology or live-interface contract is invalid; inspect the declared contract and rerun validation.", "recovery": "Repair the actual registry, definitions, or documented live interface; then rerun this check."}
    emit_result(result)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
