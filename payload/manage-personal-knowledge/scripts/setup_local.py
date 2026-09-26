"""Interactive, receiver-safe setup for manage-personal-knowledge."""

from __future__ import annotations

from mpk_public_output import SafeParser, public_main
from mpk_views import configuration_view, transaction_result_view, configuration_result_view

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


SCRIPTS_ROOT = Path(__file__).resolve().parent
MANAGE = SCRIPTS_ROOT / "manage_kb.py"
sys.path.insert(0, str(SCRIPTS_ROOT))

from manage_personal_knowledge import config as mpk_config  # noqa: E402
from manage_personal_knowledge.discovery import discover_sources  # noqa: E402


class SetupError(RuntimeError):
    def __init__(self, message: str, code: int = 50, *, status: str = "error") -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def environment(args: argparse.Namespace) -> dict[str, str]:
    values = os.environ.copy()
    values["PYTHONDONTWRITEBYTECODE"] = "1"
    values.setdefault("PYTHONUTF8", "1")
    if args.config:
        values["MPK_CONFIG_PATH"] = str(Path(args.config).expanduser().resolve())
    if args.state_dir:
        values["MPK_STATE_DIR"] = str(Path(args.state_dir).expanduser().resolve())
    return values


def config_path(args: argparse.Namespace) -> Path:
    return mpk_config.get_config_path(environment(args))


def state_dir(args: argparse.Namespace) -> Path:
    return mpk_config.get_state_dir(environment(args))


def obsidian_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    """Return suite-owned receiver paths so setup and purge stay bounded."""

    return (
        config_path(args).parent / "obsidian-vault-notes.json",
        state_dir(args) / "obsidian-vault-notes",
    )


def run_manage(args: argparse.Namespace, *parts: str) -> dict[str, Any]:
    command = [sys.executable, "-B", str(MANAGE), *parts]
    if args.config and "--config" not in command:
        command.extend(["--config", str(Path(args.config).expanduser().resolve())])
    completed = subprocess.run(
        command,
        cwd=str(SCRIPTS_ROOT),
        env=environment(args),
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise SetupError(
            f"manage_kb returned unreadable output (exit {completed.returncode})."
        ) from exc
    if not isinstance(payload, dict):
        raise SetupError("manage_kb returned an invalid result.")
    if completed.returncode != 0 or payload.get("ok") is False:
        error = SetupError("manage_kb reported an unsuccessful operation.")
        if payload.get("diagnostic_view") == "transaction_result":
            error.transaction_result = transaction_result_view(payload)
        elif payload.get("diagnostic_view") == "configuration_result":
            error.transaction_result = configuration_result_view(payload)
        raise error
    return payload


def ask_path(prompt: str, value: str | None, *, yes: bool) -> Path:
    if value:
        candidate = Path(value).expanduser().resolve()
    elif yes or not sys.stdin.isatty():
        raise SetupError(
            "Knowledge-root setup requires --knowledge-root in non-interactive mode.",
            2,
            status="setup_required",
        )
    else:
        entered = input(prompt).strip().strip('"')
        if not entered:
            raise SetupError("Knowledge-root setup was cancelled.", 2, status="setup_required")
        candidate = Path(entered).expanduser().resolve()
    if not candidate.is_dir():
        raise SetupError("The specified knowledge root is not an existing directory.", 2, status="setup_required")
    return candidate


def choose_source(
    label: str,
    candidates: list[dict[str, Any]],
    explicit: str | None,
    *,
    yes: bool,
) -> str:
    if explicit:
        values = {str(item.get("relative_path")) for item in candidates}
        normalized = Path(explicit).as_posix().strip("/") or "."
        if normalized not in values:
            raise SetupError(f"The selected {label} is not one of the discovered candidates.", 2, status="setup_required")
        return normalized
    if yes or not sys.stdin.isatty():
        raise SetupError(
            f"Non-interactive setup requires an explicit --{label} candidate.",
            2,
            status="setup_required",
        )
    if not candidates:
        raise SetupError(f"No {label} candidate was found under the knowledge root.", 2, status="setup_required")
    print(f"Discovered {label} candidates:", file=sys.stderr)
    for index, item in enumerate(candidates, start=1):
        evidence = item.get("pdf_count")
        suffix = f" ({evidence} PDFs)" if evidence is not None else ""
        print(f"  {index}. {item['relative_path']}{suffix}", file=sys.stderr)
    entered = input(f"Choose {label} [1-{len(candidates)}]: ").strip()
    try:
        selected = candidates[int(entered) - 1]
    except (ValueError, IndexError) as exc:
        raise SetupError(f"Invalid {label} selection.", 2, status="setup_required") from exc
    return str(selected["relative_path"])


def ask_yes(prompt: str, *, explicit: bool, yes: bool) -> bool:
    if explicit:
        return True
    if yes or not sys.stdin.isatty():
        return False
    return input(f"{prompt} [y/N] ").strip().casefold() in {"y", "yes"}


def selected_sources(args: argparse.Namespace, root: Path) -> tuple[str, str, dict[str, Any]]:
    discovery = discover_sources(root)
    vault = choose_source(
        "vault", list(discovery["vault_candidates"]), args.vault, yes=args.yes
    )
    library = choose_source(
        "library", list(discovery["library_candidates"]), args.library, yes=args.yes
    )
    return vault, library, discovery


def configure_new(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    vault, library, discovery = selected_sources(args, root)
    command = [
        "configure", "--root", str(root), "--vault", vault, "--library", library, "--yes",
    ]
    skills_root = Path(os.environ.get("MPK_SKILLS_ROOT", Path(__file__).resolve().parents[2]))
    if (skills_root / "obsidian-vault-notes" / "scripts" / "setup_local.py").is_file():
        receiver_config, receiver_state = obsidian_paths(args)
        command.extend(
            [
                "--configure-obsidian",
                "--obsidian-config",
                str(receiver_config),
                "--obsidian-state",
                str(receiver_state),
            ]
        )
        if ask_yes("Build the Vault note index now?", explicit=args.build_vault_index, yes=args.yes):
            command.append("--build-vault-index")
    configured = run_manage(args, *command)
    pdf_index_built = False
    if ask_yes("Build the PDF text index now?", explicit=args.build_pdf_index, yes=args.yes):
        run_manage(args, "index", "--resume")
        pdf_index_built = True
    return {
        "ok": True,
        "status": "configured",
        "knowledge_root": str(root),
        "vault": vault,
        "library": library,
        "discovery_counts": discovery["counts"],
        "vault_index_built": "--build-vault-index" in command,
        "pdf_index_built": pdf_index_built,
        "registry_initialized": False,
        "next_action": "python scripts/manage_kb.py registry-init --json",
        "configuration": configured,
    }


def repair_existing(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    saved = mpk_config.load_config(config_path(args), required=True)
    assert saved is not None
    if int(saved["schema_version"]) == 2:
        receiver_config, _ = obsidian_paths(args)
        preview = run_manage(
            args, "root-relink", "--root", str(root), "--obsidian-config", str(receiver_config)
        )
        plan = str(preview.get("plan_sha256") or "")
        if not plan:
            raise SetupError("root-relink did not return a plan hash.")
        if not args.yes:
            if not sys.stdin.isatty() or input("Apply the verified root-relink plan? [y/N] ").strip().casefold() not in {"y", "yes"}:
                raise SetupError("Root relink requires confirmation.", 2, status="setup_required")
        written = run_manage(
            args,
            "root-relink",
            "--root",
            str(root),
            "--obsidian-config",
            str(receiver_config),
            "--write",
            "--expect-plan-sha256",
            plan,
        )
        return {"ok": True, "status": "relinked", "schema_version": 2, "result": written}
    vault, library, discovery = selected_sources(args, root)
    written = run_manage(
        args, "relink", "--root", str(root), "--vault", vault, "--library", library, "--yes"
    )
    receiver_config, receiver_state = obsidian_paths(args)
    skills_root = Path(os.environ.get("MPK_SKILLS_ROOT", Path(__file__).resolve().parents[2]))
    if (skills_root / "obsidian-vault-notes" / "scripts" / "setup_local.py").is_file():
        run_manage(
            args,
            "configure",
            "--root",
            str(root),
            "--vault",
            vault,
            "--library",
            library,
            "--yes",
            "--configure-obsidian",
            "--obsidian-config",
            str(receiver_config),
            "--obsidian-state",
            str(receiver_state),
        )
    return {
        "ok": True,
        "status": "relinked",
        "schema_version": 1,
        "discovery_counts": discovery["counts"],
        "result": written,
    }


def check(args: argparse.Namespace) -> dict[str, Any]:
    report = mpk_config.status(config_path(args))
    if not report.get("configured") or not report.get("healthy"):
        return {
            "ok": False,
            "status": "setup_required",
            "configuration": configuration_view(report),
            "next_action": "python scripts/setup_local.py setup --knowledge-root <PATH>",
        }
    return {"ok": True, "status": "healthy", "configuration": configuration_view(report)}


def setup(args: argparse.Namespace) -> dict[str, Any]:
    report = mpk_config.status(config_path(args))
    if report.get("configured") and report.get("healthy"):
        return {"ok": True, "status": "already_configured", "configuration": configuration_view(report)}
    root = ask_path("Local knowledge-root path: ", args.knowledge_root, yes=args.yes)
    if report.get("configured"):
        return repair_existing(args, root)
    return configure_new(args, root)


def remove(args: argparse.Namespace) -> dict[str, Any]:
    if not args.purge:
        return {"ok": True, "status": "local_state_preserved"}
    if not args.yes:
        raise SetupError("Purging local configuration and indexes requires --yes.", 2)
    cfg = config_path(args)
    state = state_dir(args)
    receiver_config, receiver_state = obsidian_paths(args)
    cfg.unlink(missing_ok=True)
    receiver_config.unlink(missing_ok=True)
    if receiver_state.exists():
        resolved_receiver = receiver_state.resolve()
        if resolved_receiver in {resolved_receiver.parent, Path.home().resolve()}:
            raise SetupError("Refusing to purge an unsafe Obsidian state directory.")
        shutil.rmtree(resolved_receiver)
    if state.exists():
        resolved = state.resolve()
        if resolved in {resolved.parent, Path.home().resolve()}:
            raise SetupError("Refusing to purge an unsafe state directory.")
        shutil.rmtree(resolved)
    return {"ok": True, "status": "local_state_purged"}


def parse_args() -> argparse.Namespace:
    parser = SafeParser(description=__doc__)
    parser.add_argument("mode", choices=("setup", "check", "repair", "remove"))
    parser.add_argument("--knowledge-root")
    parser.add_argument("--vault")
    parser.add_argument("--library")
    parser.add_argument("--config")
    parser.add_argument("--state-dir")
    parser.add_argument("--build-vault-index", action="store_true")
    parser.add_argument("--build-pdf-index", action="store_true")
    parser.add_argument("--purge", action="store_true")
    parser.add_argument("--yes", action="store_true")
    return parser.parse_args()


@public_main(interactive=True)
def main() -> int:
    args = parse_args()
    try:
        if args.mode == "check":
            result = check(args)
            code = 0 if result["ok"] else 2
        elif args.mode == "remove":
            result = remove(args)
            code = 0
        elif args.mode == "repair":
            root = ask_path("New knowledge-root path: ", args.knowledge_root, yes=args.yes)
            result = repair_existing(args, root)
            code = 0
        else:
            result = setup(args)
            code = 0
    except SetupError as exc:
        result = getattr(exc, "transaction_result", None) or {"ok": False, "status": exc.status, "error": str(exc)}
        code = exc.code
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        result = {"ok": False, "status": "error", "error": str(exc)}
        code = 50
    print(json.dumps(result, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
