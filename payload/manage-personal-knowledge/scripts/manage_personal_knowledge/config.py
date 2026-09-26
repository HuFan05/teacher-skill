"""Persistent configuration for manage-personal-knowledge.

Only explicit, confirmed configuration operations write to disk.  Status and
discovery are read-only, including when configured paths have disappeared.
"""

from __future__ import annotations

import copy
import json
import os
import re
import sys
import tempfile
from pathlib import Path, PureWindowsPath
from typing import Mapping


LEGACY_SCHEMA_VERSION = 1
SCHEMA_VERSION = 2
SUPPORTED_SCHEMA_VERSIONS = frozenset({LEGACY_SCHEMA_VERSION, SCHEMA_VERSION})
DEFAULT_MANIFEST_RELATIVE_PATH = ".mpk/resources.jsonl"
_ROOT_ID_PATTERN = re.compile(r"^KBROOT-[A-Z2-7]{26}$")
SOURCE_KINDS = {"vault": "obsidian-vault", "library": "pdf-library"}


class ConfigError(ValueError):
    """Raised for unsafe, invalid, or unconfirmed configuration changes."""


def _environment(env: Mapping[str, str] | None = None) -> Mapping[str, str]:
    return os.environ if env is None else env


def _expanded_path(raw: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(raw))).resolve(strict=False)


def get_config_path(env: Mapping[str, str] | None = None) -> Path:
    values = _environment(env)
    override = values.get("MPK_CONFIG_PATH")
    if override:
        return _expanded_path(override)
    if sys.platform.startswith("win"):
        appdata = values.get("APPDATA")
        base = _expanded_path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        xdg = values.get("XDG_CONFIG_HOME")
        base = _expanded_path(xdg) if xdg else Path.home() / ".config"
    return (base / "manage-personal-knowledge" / "config.json").resolve(strict=False)


def get_state_dir(env: Mapping[str, str] | None = None) -> Path:
    values = _environment(env)
    override = values.get("MPK_STATE_DIR")
    if override:
        return _expanded_path(override)
    if sys.platform.startswith("win"):
        local_appdata = values.get("LOCALAPPDATA")
        base = _expanded_path(local_appdata) if local_appdata else Path.home() / "AppData" / "Local"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        xdg = values.get("XDG_STATE_HOME")
        base = _expanded_path(xdg) if xdg else Path.home() / ".local" / "state"
    return (base / "manage-personal-knowledge").resolve(strict=False)


def _config_path(config_path: os.PathLike[str] | str | None) -> Path:
    return get_config_path() if config_path is None else _expanded_path(os.fspath(config_path))


def _confirm(confirmed: bool, operation: str) -> None:
    if confirmed is not True:
        raise ConfigError(f"{operation} requires explicit confirmation (confirmed=True)")


def _absolute_directory(path: os.PathLike[str] | str, label: str) -> Path:
    candidate = Path(path).expanduser()
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ConfigError(f"{label} does not exist or cannot be resolved: {candidate}") from exc
    if not resolved.is_dir():
        raise ConfigError(f"{label} is not a directory: {resolved}")
    return resolved


def _source_directory(
    root: Path,
    path: os.PathLike[str] | str,
    label: str,
) -> Path:
    """Resolve a source path, treating relative input as root-relative."""

    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    return _absolute_directory(candidate, label)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _relative_text(root: Path, source: Path, label: str) -> str:
    if not _is_within(source, root):
        raise ConfigError(f"{label} must be inside knowledge_root: {source}")
    relative = source.relative_to(root).as_posix()
    return relative or "."


def _relative_parts(relative_path: str) -> tuple[str, ...]:
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise ConfigError("source.relative_path must be a non-empty string")
    windows_path = PureWindowsPath(relative_path)
    if windows_path.is_absolute() or windows_path.drive:
        raise ConfigError(f"source.relative_path must be relative: {relative_path}")
    normalised = relative_path.replace("\\", "/")
    if normalised.startswith("/"):
        raise ConfigError(f"source.relative_path must be relative: {relative_path}")
    parts = tuple(part for part in normalised.split("/") if part not in ("", "."))
    if any(part == ".." for part in parts):
        raise ConfigError(f"source.relative_path cannot escape knowledge_root: {relative_path}")
    return parts


def _validate_root_name(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("knowledge_root_name must be a non-empty string")
    name = value.strip()
    if name in {".", ".."} or any(character in name for character in ("/", "\\", "\x00")):
        raise ConfigError("knowledge_root_name must be a display name, not a path")
    return name


def _validate_root_id(value: object) -> str:
    if not isinstance(value, str) or _ROOT_ID_PATTERN.fullmatch(value) is None:
        raise ConfigError(
            "knowledge_root_id must be 'KBROOT-' followed by 26 uppercase Base32 characters"
        )
    return value


def _normalised_relative_text(relative_path: object, label: str) -> str:
    if not isinstance(relative_path, str):
        raise ConfigError(f"{label} must be a non-empty root-relative path")
    parts = _relative_parts(relative_path)
    if not parts:
        raise ConfigError(f"{label} must identify an item below knowledge_root")
    return "/".join(parts)


def _validate_registry(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ConfigError("registry must be a JSON object")
    manifest = _normalised_relative_text(
        value.get("manifest_relative_path"), "registry.manifest_relative_path"
    )
    excluded = value.get("excluded_relative_paths")
    if not isinstance(excluded, list):
        raise ConfigError("registry.excluded_relative_paths must be a JSON array")
    normalised_excluded: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(excluded):
        path = _normalised_relative_text(
            item, f"registry.excluded_relative_paths[{index}]"
        )
        key = path.casefold()
        if key in seen:
            raise ConfigError(f"Duplicate registry exclusion: {path}")
        seen.add(key)
        normalised_excluded.append(path)
    validated = copy.deepcopy(value)
    validated["manifest_relative_path"] = manifest
    validated["excluded_relative_paths"] = normalised_excluded
    return validated


def _validate_config(config: object) -> dict[str, object]:
    if not isinstance(config, dict):
        raise ConfigError("Config must be a JSON object")
    schema_version = config.get("schema_version")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version not in SUPPORTED_SCHEMA_VERSIONS
    ):
        raise ConfigError(f"Unsupported schema_version: {config.get('schema_version')!r}")
    root_value = config.get("knowledge_root")
    if not isinstance(root_value, str) or not root_value.strip():
        raise ConfigError("knowledge_root must be a non-empty absolute path")
    root = Path(root_value).expanduser()
    if not root.is_absolute():
        raise ConfigError("knowledge_root must be an absolute path")

    sources = config.get("sources")
    if not isinstance(sources, dict):
        raise ConfigError("sources must be a JSON object")
    for name, source in sources.items():
        if not isinstance(name, str) or not isinstance(source, dict):
            raise ConfigError("Every source must be a named JSON object")
        kind = source.get("kind")
        relative_path = source.get("relative_path")
        if not isinstance(kind, str) or not kind.strip():
            raise ConfigError(f"sources.{name}.kind must be a non-empty string")
        expected_kind = SOURCE_KINDS.get(name)
        if expected_kind and kind != expected_kind:
            raise ConfigError(f"sources.{name}.kind must be {expected_kind!r}")
        _relative_parts(relative_path)

    validated = copy.deepcopy(config)
    if schema_version == SCHEMA_VERSION:
        validated["knowledge_root_name"] = _validate_root_name(
            config.get("knowledge_root_name")
        )
        validated["knowledge_root_id"] = _validate_root_id(config.get("knowledge_root_id"))
        validated["registry"] = _validate_registry(config.get("registry"))

    # Resolve every source now to enforce containment even if the path is
    # currently missing.  strict=False is intentional for status reporting.
    for name in sources:
        resolve_source(validated, name, strict=False)
    return validated


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        try:
            temporary_path.unlink(missing_ok=True)
        finally:
            raise


def save_config(config: dict[str, object], config_path: os.PathLike[str] | str | None = None) -> dict[str, object]:
    """Validate and atomically persist a complete config object."""

    validated = _validate_config(config)
    _atomic_write_json(_config_path(config_path), validated)
    return validated


def load_config(
    config_path: os.PathLike[str] | str | None = None, *, required: bool = False
) -> dict[str, object] | None:
    path = _config_path(config_path)
    if not path.exists():
        if required:
            raise ConfigError(f"Configuration file does not exist: {path}")
        return None
    try:
        with path.open("r", encoding="utf-8") as stream:
            raw = json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Cannot read configuration: {path}: {exc}") from exc
    return _validate_config(raw)


def resolve_source(config: dict[str, object], source_name: str, *, strict: bool = False) -> Path:
    root_value = config.get("knowledge_root")
    sources = config.get("sources")
    if not isinstance(root_value, str) or not isinstance(sources, dict):
        raise ConfigError("Config is missing knowledge_root or sources")
    source = sources.get(source_name)
    if not isinstance(source, dict):
        raise ConfigError(f"Source is not configured: {source_name}")
    parts = _relative_parts(source.get("relative_path"))
    root = Path(root_value).expanduser().resolve(strict=False)
    candidate = root.joinpath(*parts).resolve(strict=strict)
    if not _is_within(candidate, root):
        raise ConfigError(f"Source escapes knowledge_root: {source_name}")
    return candidate


def _source_entry(name: str, root: Path, path: Path) -> dict[str, str]:
    return {"kind": SOURCE_KINDS[name], "relative_path": _relative_text(root, path, name)}


def _validate_source_shape(name: str, path: Path) -> None:
    if name == "vault" and not (path / ".obsidian").is_dir():
        raise ConfigError(f"Vault path does not contain a .obsidian directory: {path}")


def configure(
    knowledge_root: os.PathLike[str] | str,
    vault_path: os.PathLike[str] | str,
    library_path: os.PathLike[str] | str,
    *,
    confirmed: bool = False,
    config_path: os.PathLike[str] | str | None = None,
) -> dict[str, object]:
    """Persist explicitly selected Vault and library paths after confirmation."""

    _confirm(confirmed, "configure")
    current = load_config(config_path)
    if current is not None and current["schema_version"] == SCHEMA_VERSION:
        raise ConfigError(
            "configure cannot replace an initialized schema-v2 root; use root-relink "
            "for a whole-root move and a registry-aware migration for source changes"
        )
    root = _absolute_directory(knowledge_root, "knowledge_root")
    vault = _source_directory(root, vault_path, "vault_path")
    library = _source_directory(root, library_path, "library_path")
    _relative_text(root, vault, "vault_path")
    _relative_text(root, library, "library_path")
    _validate_source_shape("vault", vault)
    if vault == library or _is_within(library, vault) or _is_within(vault, library):
        raise ConfigError("Vault and library paths must be separate, non-nested directories")

    config: dict[str, object] = {
        "schema_version": LEGACY_SCHEMA_VERSION,
        "knowledge_root": str(root),
        "sources": {
            "vault": _source_entry("vault", root, vault),
            "library": _source_entry("library", root, library),
        },
    }
    return save_config(config, config_path)


def migrate_to_schema_v2(
    *,
    knowledge_root_id: str,
    knowledge_root_name: str | None = None,
    manifest_relative_path: str = DEFAULT_MANIFEST_RELATIVE_PATH,
    excluded_relative_paths: list[str] | tuple[str, ...] | None = None,
    confirmed: bool = False,
    config_path: os.PathLike[str] | str | None = None,
) -> dict[str, object]:
    """Explicitly upgrade a saved v1 config while preserving source paths.

    Identity generation and creation of ``.mpk`` files belong to the caller
    performing ``registry-init``.  This function only validates and atomically
    persists the corresponding schema-v2 configuration.
    """

    _confirm(confirmed, "migrate_to_schema_v2")
    current = load_config(config_path, required=True)
    assert current is not None
    root = Path(str(current["knowledge_root"])).expanduser().resolve(strict=False)
    selected_name = knowledge_root_name if knowledge_root_name is not None else root.name
    selected_exclusions = list(excluded_relative_paths or [])

    if current["schema_version"] == SCHEMA_VERSION:
        requested_registry = {
            "manifest_relative_path": manifest_relative_path,
            "excluded_relative_paths": selected_exclusions,
        }
        if (
            current.get("knowledge_root_id") != knowledge_root_id
            or current.get("knowledge_root_name") != selected_name
            or current.get("registry") != _validate_registry(requested_registry)
        ):
            raise ConfigError(
                "Configuration is already schema v2 with a different root identity or registry"
            )
        return current

    migrated = copy.deepcopy(current)
    migrated["schema_version"] = SCHEMA_VERSION
    migrated["knowledge_root_name"] = selected_name
    migrated["knowledge_root_id"] = knowledge_root_id
    migrated["registry"] = {
        "manifest_relative_path": manifest_relative_path,
        "excluded_relative_paths": selected_exclusions,
    }
    return save_config(migrated, config_path)


def status(config_path: os.PathLike[str] | str | None = None, *, rediscover: bool = False) -> dict[str, object]:
    """Report path health; source rediscovery is an explicit separate request."""

    path = _config_path(config_path)
    config = load_config(path)
    if config is None:
        return {
            "configured": False,
            "config_path": str(path),
            "healthy": False,
            "missing": ["config"],
            "sources": {},
            "rediscovery": None,
        }

    root = Path(str(config["knowledge_root"])).expanduser().resolve(strict=False)
    root_healthy = root.is_dir()
    missing: list[str] = []
    source_status: dict[str, object] = {}
    sources = config["sources"]
    assert isinstance(sources, dict)  # enforced by _validate_config

    if not root_healthy:
        missing.append("knowledge_root")
    for name in SOURCE_KINDS:
        if name not in sources:
            missing.append(name)
            source_status[name] = {"configured": False, "state": "unconfigured", "path": None}
            continue
        resolved = resolve_source(config, name, strict=False)
        exists = resolved.is_dir()
        valid_shape = exists and (name != "vault" or (resolved / ".obsidian").is_dir())
        state = "healthy" if valid_shape else "missing"
        if not valid_shape:
            missing.append(name)
        source_status[name] = {
            "configured": True,
            "kind": sources[name]["kind"],
            "relative_path": sources[name]["relative_path"],
            "path": str(resolved),
            "state": state,
        }

    rediscovery = None
    if rediscover and missing and root_healthy:
        from .discovery import discover_sources

        rediscovery = discover_sources(root)

    result: dict[str, object] = {
        "configured": True,
        "config_path": str(path),
        "schema_version": config["schema_version"],
        "knowledge_root": str(root),
        "healthy": not missing,
        "missing": missing,
        "sources": source_status,
        "rediscovery": rediscovery,
    }
    if config["schema_version"] == SCHEMA_VERSION:
        result.update(
            {
                "knowledge_root_name": config["knowledge_root_name"],
                "knowledge_root_id": config["knowledge_root_id"],
                "registry": copy.deepcopy(config["registry"]),
            }
        )
    return result


def relink_source(
    source_name: str,
    new_path: os.PathLike[str] | str,
    *,
    confirmed: bool = False,
    config_path: os.PathLike[str] | str | None = None,
) -> dict[str, object]:
    """Explicitly update one source while keeping the knowledge root fixed."""

    _confirm(confirmed, "relink_source")
    if source_name not in SOURCE_KINDS:
        raise ConfigError(f"Unknown source: {source_name}")
    config = load_config(config_path, required=True)
    assert config is not None
    if config["schema_version"] == SCHEMA_VERSION:
        raise ConfigError(
            "relink_source is disabled after registry initialization because it would "
            "desynchronize exclusions and source identity"
        )
    root = _absolute_directory(str(config["knowledge_root"]), "knowledge_root")
    source_path = _source_directory(root, new_path, f"{source_name}_path")
    _relative_text(root, source_path, f"{source_name}_path")
    _validate_source_shape(source_name, source_path)

    if source_name == "library" and "vault" in config["sources"]:
        vault = resolve_source(config, "vault", strict=False)
        if source_path == vault or _is_within(source_path, vault) or _is_within(vault, source_path):
            raise ConfigError("Vault and library paths must be separate, non-nested directories")
    if source_name == "vault" and "library" in config["sources"]:
        library = resolve_source(config, "library", strict=False)
        if source_path == library or _is_within(library, source_path) or _is_within(source_path, library):
            raise ConfigError("Vault and library paths must be separate, non-nested directories")

    updated = copy.deepcopy(config)
    updated_sources = updated["sources"]
    assert isinstance(updated_sources, dict)
    updated_sources[source_name] = _source_entry(source_name, root, source_path)
    return save_config(updated, config_path)


def relink(
    *,
    knowledge_root: os.PathLike[str] | str,
    vault_path: os.PathLike[str] | str,
    library_path: os.PathLike[str] | str,
    confirmed: bool = False,
    config_path: os.PathLike[str] | str | None = None,
) -> dict[str, object]:
    """Explicitly relink a moved root and both sources in one operation."""

    _confirm(confirmed, "relink")
    current = load_config(config_path)
    # Legacy configs retain the original v1 behaviour.  A registry-initialised
    # config must not be silently downgraded by the old relink entry point.
    if current is None or current["schema_version"] == LEGACY_SCHEMA_VERSION:
        return configure(
            knowledge_root,
            vault_path,
            library_path,
            confirmed=True,
            config_path=config_path,
        )

    raise ConfigError(
        "relink cannot move an initialized schema-v2 root; use root-relink with an "
        "accepted plan_sha256"
    )


def forget_source(
    source_name: str,
    *,
    confirmed: bool = False,
    config_path: os.PathLike[str] | str | None = None,
) -> dict[str, object]:
    """Explicitly remove one source association from the saved config."""

    _confirm(confirmed, "forget_source")
    if source_name not in SOURCE_KINDS:
        raise ConfigError(f"Unknown source: {source_name}")
    config = load_config(config_path, required=True)
    assert config is not None
    if config["schema_version"] == SCHEMA_VERSION:
        raise ConfigError(
            "forget_source is disabled after registry initialization; preserve the "
            "source mapping until a registry-aware reconfiguration is performed"
        )
    updated = copy.deepcopy(config)
    sources = updated["sources"]
    assert isinstance(sources, dict)
    if source_name not in sources:
        raise ConfigError(f"Source is not configured: {source_name}")
    del sources[source_name]
    return save_config(updated, config_path)


def forget_config(
    *, confirmed: bool = False, config_path: os.PathLike[str] | str | None = None
) -> bool:
    """Explicitly remove the entire configuration file."""

    _confirm(confirmed, "forget_config")
    path = _config_path(config_path)
    if not path.exists():
        return False
    path.unlink()
    return True


__all__ = [
    "ConfigError",
    "DEFAULT_MANIFEST_RELATIVE_PATH",
    "LEGACY_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "SOURCE_KINDS",
    "configure",
    "forget_config",
    "forget_source",
    "get_config_path",
    "get_state_dir",
    "load_config",
    "migrate_to_schema_v2",
    "relink",
    "relink_source",
    "resolve_source",
    "save_config",
    "status",
]
