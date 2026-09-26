"""Configure, check, or remove local state for obsidian-vault-notes."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


SKILL_NAME = "obsidian-vault-notes"
SCRIPTS_ROOT = Path(__file__).resolve().parent
KB_ROOT = SCRIPTS_ROOT / "obsidian_local_kb"


def platform_config_root() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))


def platform_state_root() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))


def default_config_path() -> Path:
    override = os.environ.get("OBSIDIAN_VAULT_NOTES_CONFIG")
    if override:
        return Path(override).expanduser().resolve()
    return (platform_config_root() / SKILL_NAME / "config.json").resolve()


def default_state_dir() -> Path:
    return (platform_state_root() / SKILL_NAME).resolve()


def atomic_json_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_config(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Local config is unreadable: {path}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise RuntimeError(f"Local config has an unsupported schema: {path}")
    return payload


def python_environment(config: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment.setdefault("PYTHONUTF8", "1")
    environment["OBSIDIAN_VAULT_NOTES_CONFIG"] = str(config)
    return environment


def run_kb(command: list[str], config: Path) -> None:
    completed = subprocess.run(
        [sys.executable, "-B", "-m", "obsidian_local_kb", *command],
        cwd=str(KB_ROOT),
        env=python_environment(config),
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"Local index command failed with exit code {completed.returncode}.")


def configure(args: argparse.Namespace) -> dict:
    if not args.vault_root:
        raise RuntimeError("--vault-root is required for configuration.")
    vault = Path(args.vault_root).expanduser().resolve()
    if not vault.is_dir():
        raise RuntimeError("The authorized Vault directory does not exist.")
    if not (KB_ROOT / "obsidian_local_kb" / "__main__.py").is_file():
        raise RuntimeError("The bundled obsidian_local_kb module is missing.")
    config_path = Path(args.config).expanduser().resolve() if args.config else default_config_path()
    state_dir = Path(args.state_dir).expanduser().resolve() if args.state_dir else default_state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)
    db_path = state_dir / "vault.sqlite3"
    payload = {
        "schema_version": 1,
        "vault_root": str(vault),
        "kb_root": str(KB_ROOT),
        "db_path": str(db_path),
    }
    if config_path.exists() and load_config(config_path) != payload and not args.yes:
        raise RuntimeError("Local config already exists with different values; rerun after review with --yes.")
    atomic_json_write(config_path, payload)
    if args.build_index:
        run_kb(["index", "--vault", str(vault), "--db", str(db_path)], config_path)
    report = {
        "status": "configured",
        "config": str(config_path),
        "state_dir": str(state_dir),
        "vault_root": str(vault),
        "kb_root": str(KB_ROOT),
        "db_path": str(db_path),
        "index_built": bool(args.build_index),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }
    atomic_json_write(Path(args.report).expanduser().resolve() if args.report else state_dir / "SETUP_REPORT.json", report)
    return report


def check(args: argparse.Namespace) -> dict:
    config_path = Path(args.config).expanduser().resolve() if args.config else default_config_path()
    if not config_path.is_file():
        raise RuntimeError("Local config is missing; run configure first.")
    payload = load_config(config_path)
    vault = Path(payload["vault_root"]).expanduser().resolve()
    kb_root = Path(payload["kb_root"]).expanduser().resolve()
    db_path = Path(payload["db_path"]).expanduser().resolve()
    if not vault.is_dir():
        raise RuntimeError("Configured Vault directory is missing.")
    if kb_root != KB_ROOT.resolve() or not (kb_root / "obsidian_local_kb" / "__main__.py").is_file():
        raise RuntimeError("Configured bundled index module is missing or does not match this install.")
    if not db_path.is_file():
        raise RuntimeError("Configured note index is missing; rebuild it with --build-index.")
    run_kb(["status", "--vault", str(vault), "--db", str(db_path), "--json"], config_path)
    return {"status": "healthy", "config": str(config_path), "db_path": str(db_path)}


def remove(args: argparse.Namespace) -> dict:
    if not args.purge:
        return {"status": "local-state-preserved"}
    if not args.yes:
        raise RuntimeError("Purging local configuration and index requires --yes.")
    config_path = Path(args.config).expanduser().resolve() if args.config else default_config_path()
    state_dir = Path(args.state_dir).expanduser().resolve() if args.state_dir else default_state_dir()
    config_path.unlink(missing_ok=True)
    if state_dir.exists():
        if state_dir == state_dir.parent or state_dir == Path.home().resolve():
            raise RuntimeError("Refusing to purge an unsafe state directory.")
        shutil.rmtree(state_dir)
    return {"status": "local-state-purged", "config": str(config_path), "state_dir": str(state_dir)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("configure", "check", "remove"))
    parser.add_argument("--vault-root")
    parser.add_argument("--config")
    parser.add_argument("--state-dir")
    parser.add_argument("--report")
    parser.add_argument("--build-index", action="store_true")
    parser.add_argument("--purge", action="store_true")
    parser.add_argument("--yes", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.mode == "configure":
            result = configure(args)
        elif args.mode == "check":
            result = check(args)
        else:
            result = remove(args)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (KeyError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 50


if __name__ == "__main__":
    raise SystemExit(main())
