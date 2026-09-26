from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


SKILL_NAME = "obsidian-vault-notes"
SCRIPT_PATH = Path(__file__).resolve()
SCRIPTS_ROOT = SCRIPT_PATH.parent
BUNDLED_KB_ROOT = SCRIPTS_ROOT / "obsidian_local_kb"


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


def load_local_config(path: Path | None = None) -> dict[str, Any]:
    target = (path or default_config_path()).expanduser().resolve()
    if not target.exists():
        return {}
    try:
        payload = json.loads(target.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Local config is unreadable: {target}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise RuntimeError(f"Local config has an unsupported schema: {target}")
    return payload


def configured_path(config: dict[str, Any], key: str) -> Path | None:
    value = config.get(key)
    if isinstance(value, str) and value.strip():
        return Path(value).expanduser().resolve()
    return None


def resolve_existing_path(candidates: list[Path | None]) -> Path:
    normalized = [Path(path).expanduser().resolve() for path in candidates if path is not None]
    if not normalized:
        raise RuntimeError("No path candidates were configured.")
    for path in normalized:
        if path.exists():
            return path
    return normalized[0]


LOCAL_CONFIG = load_local_config()
GENERIC_VAULT_ROOT = Path.home() / "Documents" / "Obsidian Vault"
DEFAULT_VAULT_ROOT = resolve_existing_path(
    [
        Path(os.environ["OBSIDIAN_VAULT_ROOT"])
        if os.environ.get("OBSIDIAN_VAULT_ROOT")
        else None,
        configured_path(LOCAL_CONFIG, "vault_root"),
        GENERIC_VAULT_ROOT,
    ]
)


def resolve_kb_root() -> Path:
    env_root = os.environ.get("OBSIDIAN_LOCAL_KB_ROOT")
    return resolve_existing_path(
        [
            Path(env_root) if env_root else None,
            configured_path(LOCAL_CONFIG, "kb_root"),
            BUNDLED_KB_ROOT,
        ]
    )


def resolve_note_db_path(kb_root: Path) -> Path:
    env_db = os.environ.get("OBSIDIAN_LOCAL_KB_DB")
    state_default = platform_state_root() / SKILL_NAME / "vault.sqlite3"
    return resolve_existing_path(
        [
            Path(env_db) if env_db else None,
            configured_path(LOCAL_CONFIG, "db_path"),
            kb_root / "data" / "vault.sqlite3",
            state_default,
        ]
    )


DEFAULT_KB_ROOT = resolve_kb_root()
DEFAULT_DB_PATH = resolve_note_db_path(DEFAULT_KB_ROOT)


def configure_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
