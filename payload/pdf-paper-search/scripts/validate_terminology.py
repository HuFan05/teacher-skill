from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


TERM_FIELDS = {
    "canonical_id", "display_name_zh", "definition_ref", "semantic_layer",
    "authority_class", "constitutive_fields", "aliases", "deprecated_aliases",
    "reserved_conflicts", "introduced_in", "change_policy", "visibility",
    "relations", "history",
}
SECTIONS = (
    "简要定义", "规范定义", "构成字段", "权威等级", "生命周期规则",
    "允许的变化", "禁止的变化", "不得混淆", "完成关系", "机器绑定",
)
ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
GLOBAL_RE = re.compile(r"^personal:[a-z0-9]+(?:-[a-z0-9]+)*#[a-z][a-z0-9_]*$")


def fail(code: str, message: str, recovery: str, details: object = None) -> int:
    print(json.dumps({
        "ok": False,
        "code": code,
        "message": message,
        "recovery": recovery,
        "details": details,
    }, ensure_ascii=False))
    return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skill-root", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    root = Path(args.skill_root).resolve()
    skill = root / "SKILL.md"
    glossary_path = root / "references" / "terminology.md"
    registry_path = root / "references" / "terminology-registry.json"
    for path in (skill, glossary_path, registry_path):
        if not path.is_file():
            return fail("terminology_asset_missing", f"Missing {path.relative_to(root)}", "Restore all four terminology layers.")
    skill_text = skill.read_text(encoding="utf-8-sig")
    if "references/terminology.md" not in skill_text:
        return fail("terminology_skill_reference_missing", "SKILL.md does not link canonical terminology.", "Add the mandatory read timing and link.")
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        return fail("terminology_registry_invalid", str(error), "Repair the UTF-8 JSON registry.")
    if set(registry) != {"schema_version", "skill_id", "terms"} or registry.get("schema_version") != "skill-terminology-registry/v1":
        return fail("terminology_registry_schema_invalid", "Registry root is not the closed v1 schema.", "Use schema_version, skill_id, and terms only.")
    name_match = re.search(r"(?m)^name:\s*([^\s]+)\s*$", skill_text)
    name = name_match.group(1) if name_match else root.name
    if registry.get("skill_id") != f"personal:{name}":
        return fail("terminology_skill_identity_mismatch", "skill_id does not match SKILL.md.", f"Use personal:{name}.")
    terms = registry.get("terms")
    if not isinstance(terms, list) or not terms:
        return fail("terminology_terms_missing", "No canonical terms are registered.", "Register terms or remove all assets and use a reviewed non-asset decision.")
    glossary = glossary_path.read_text(encoding="utf-8-sig")
    ids: set[str] = set()
    active_labels: dict[str, str] = {}
    deprecated_labels: dict[str, str] = {}
    for term in terms:
        if not isinstance(term, dict) or set(term) != TERM_FIELDS:
            return fail("terminology_term_schema_invalid", "A term does not use the closed schema.", "Add missing fields and remove extra fields.")
        term_id = term.get("canonical_id")
        if not isinstance(term_id, str) or not ID_RE.fullmatch(term_id) or term_id in ids:
            return fail("terminology_canonical_id_invalid", f"Invalid or duplicate ID: {term_id}", "Use unique lowercase snake_case IDs.")
        ids.add(term_id)
        if term.get("definition_ref") != f"references/terminology.md#{term_id}":
            return fail("terminology_definition_ref_invalid", f"Bad definition_ref for {term_id}", "Point to the exact canonical heading anchor.")
        heading = re.search(rf"(?m)^##\s+`?{re.escape(term_id)}`?\s*$", glossary)
        if not heading:
            return fail("terminology_definition_anchor_missing", f"Missing heading for {term_id}", "Add the exact level-two canonical heading.")
        following = re.search(r"(?m)^##\s+", glossary[heading.end():])
        end = heading.end() + following.start() if following else len(glossary)
        entry = glossary[heading.end():end]
        missing = [section for section in SECTIONS if not re.search(rf"(?m)^###\s+{re.escape(section)}\s*$", entry)]
        if missing:
            return fail("terminology_definition_incomplete", f"Incomplete definition for {term_id}", "Add every normative section.", missing)
        if term.get("visibility") not in {"local", "exported"}:
            return fail("terminology_visibility_invalid", f"Invalid visibility for {term_id}", "Use local or exported.")
        if not isinstance(term.get("history"), list) or not term["history"]:
            return fail("terminology_history_missing", f"Missing history for {term_id}", "Record introduction and semantic changes.")
        for relation in term.get("relations", []):
            if not isinstance(relation, dict) or set(relation) != {"type", "target"} or relation.get("type") not in {"equivalent_to", "refines", "distinct_from"} or not GLOBAL_RE.fullmatch(str(relation.get("target"))):
                return fail("terminology_relation_invalid", f"Invalid relation for {term_id}", "Use a valid type and global target identity.")
        for label in [term_id, term.get("display_name_zh"), *term.get("aliases", [])]:
            key = str(label).casefold()
            if key in active_labels and active_labels[key] != term_id:
                return fail("terminology_alias_polysemy", f"Active label {label} is ambiguous.", "Rename or relate the concepts explicitly.")
            active_labels[key] = term_id
        for label in term.get("deprecated_aliases", []):
            key = str(label).casefold()
            if key in deprecated_labels and deprecated_labels[key] != term_id:
                return fail("terminology_deprecated_alias_polysemy", f"Deprecated label {label} is ambiguous.", "Keep one historical owner.")
            deprecated_labels[key] = term_id
    reused = sorted(set(active_labels) & set(deprecated_labels))
    if reused:
        return fail("terminology_deprecated_name_reused", "A deprecated name is active again.", "Choose a new canonical label and preserve migration history.", reused)
    project = next((term for term in terms if term["canonical_id"] == "project_objective"), None)
    if project is not None:
        fields = {"statement", "domain", "claim_scope", "assumptions", "evidence_standard", "completion_standard"}
        if set(project["constitutive_fields"]) != fields:
            return fail("project_objective_constitutive_fields_invalid", "project_objective does not retain exactly six fields.", "Restore the immutable six-field identity.")
    print(json.dumps({"ok": True, "code": "terminology_valid", "term_count": len(terms)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
