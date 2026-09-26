"""Receiver-local configuration.

Configuration and state live outside the package, in the platform's user
directories, so the package itself never holds a path, a corpus or a secret:

    macOS    ~/Library/Application Support/teacher/
    Linux    ${XDG_CONFIG_HOME:-~/.config}/teacher/ and ${XDG_STATE_HOME:-~/.local/state}/teacher/
    Windows  %APPDATA%\\teacher\\ and %LOCALAPPDATA%\\teacher\\
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from .web import DEFAULT_ALLOW_DOMAINS

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = SCRIPTS_ROOT.parent


def config_dir() -> Path:
    override = os.environ.get("TEACHER_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "teacher"
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", Path.home())) / "teacher"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "teacher"


def state_dir() -> Path:
    override = os.environ.get("TEACHER_STATE_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "teacher"
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "teacher"
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "teacher"


def config_path() -> Path:
    return config_dir() / "config.json"


def defaults() -> dict[str, Any]:
    return {
        "schema": "teacher-config/v1",
        "store": str(state_dir() / "teacher.sqlite3"),
        "read_roots": [],
        "network": {"enabled": True, "allow_domains": list(DEFAULT_ALLOW_DOMAINS)},
        "declared_commands": {},
        "checkers": {},
    }


def load() -> dict[str, Any]:
    config = defaults()
    path = config_path()
    if path.is_file():
        stored = json.loads(path.read_text(encoding="utf-8"))
        for key, value in stored.items():
            if isinstance(value, dict) and isinstance(config.get(key), dict):
                config[key] = {**config[key], **value}
            else:
                config[key] = value
    return config


def save(config: dict[str, Any]) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def contract(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "objective": None,
        "delegation": {
            "tools": [],
            "capabilities": ["read"],
            "commands": dict(config.get("declared_commands") or {}),
            "checkers": dict(config.get("checkers") or {}),
        },
        "scope": {"read_roots": list(config.get("read_roots") or [])},
        "network": dict(config.get("network") or {"enabled": False}),
        "open_obligations": [],
    }
