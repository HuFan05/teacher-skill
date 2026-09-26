"""Read-only capability report for the portable Teacher Skill suite."""

from __future__ import annotations

from mpk_public_output import SafeParser, public_main

import json
import os
import shutil
import sys
from pathlib import Path


def command_available(*names: str) -> bool:
    return any(shutil.which(name) for name in names)


@public_main
def main() -> int:
    skill_root = Path(__file__).resolve().parents[1]
    skills_root = Path(os.environ.get("MPK_SKILLS_ROOT", skill_root.parent)).expanduser().resolve()
    suite = (
        "teacher",
        "cs-ai-research-solve",
        "cs-ai-computation",
        "pdf-paper-search",
        "manage-personal-knowledge",
        "obsidian-vault-notes",
    )
    required = {name: skills_root / name / "SKILL.md" for name in suite}
    installed = {name: path.is_file() for name, path in required.items()}
    optional = {
        "pdf_text_extraction": {
            "status": "available" if command_available("pdftotext") else "missing_optional_dependency",
            "dependency": "pdftotext",
        },
        "gpu_accelerator": {
            "status": "available_command" if command_available("nvidia-smi") else "not_detected",
            "note": "A live device query is still required before claiming accelerator availability.",
        },
        "smt_solver": {
            "status": "available_command" if command_available("z3", "cvc5") else "not_detected",
        },
        "proof_assistant": {
            "status": "available_command" if command_available("lean", "coqc", "rocq", "isabelle") else "not_detected",
        },
    }
    result = {
        "status": "installed" if all(installed.values()) else "missing_skill_payload",
        "python": {"version": sys.version.split()[0], "supported": sys.version_info >= (3, 10)},
        "skills": installed,
        "optional_capabilities": optional,
        "notes": [
            "Bundled computation routing is not evidence that a GPU, a machine-learning framework, an SMT solver, or a proof assistant is installed or usable; cs-ai-computation owns live backend checks.",
            "PDF visual-layout reading is not bundled; the receiving agent may supply an equivalent optional capability.",
        ],
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "installed" and result["python"]["supported"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
