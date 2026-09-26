"""Transactional installer for the Teacher portable Skill suite (package-manifest schema v3).

Installs every declared Skill payload into one agent Skill root as a single
transaction, optionally configures the local knowledge root and the teacher
Skill's read scope, and checks, repairs, replaces or uninstalls the result. It
never downloads anything, never requests elevated privileges and never runs a
package manager.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = PACKAGE_ROOT / "package-manifest.json"
CHECKSUM_PATH = PACKAGE_ROOT / "checksums.sha256"
AGGREGATE_PATH = PACKAGE_ROOT / "checksums.aggregate.json"
MIN_PYTHON = (3, 10)

# Same exclusions as tools/checksums.py, so the installer verifies exactly the
# inventory that `python tools/checksums.py --write` records.
EXCLUDED_DIRS = {".git", "__pycache__", ".pytest_cache", "work", "node_modules", ".venv", "venv", ".workbuddy"}
EXCLUDED_NAMES = {"checksums.sha256", "checksums.aggregate.json", ".DS_Store"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".sqlite3", ".sqlite3-wal", ".sqlite3-shm"}
LOCAL_NOISE = {".DS_Store", "Thumbs.db"}

# Native agent Skill roots: (override variable, default folder under the home directory).
AGENT_HOMES = {
    "codex": ("CODEX_HOME", ".codex"),
    "claude": ("CLAUDE_CONFIG_DIR", ".claude"),
}
# Override variables honoured by the owning Skills themselves.
KNOWLEDGE_CONFIG_ENV = "MPK_CONFIG_PATH"
KNOWLEDGE_STATE_ENV = "MPK_STATE_DIR"
TEACHER_CONFIG_ENV = "TEACHER_CONFIG_DIR"
OBSIDIAN_CONFIG_NAME = "obsidian-vault-notes.json"
TEACHER_CONFIG_NAME = "config.json"
STATE_MARKER = "configuration-state.json"
REPORT_NAME = "INSTALL_REPORT.json"
SECRET_WORDS = ("key", "token", "secret", "password")


class InstallError(RuntimeError):
    def __init__(self, message: str, code: int = 50) -> None:
        super().__init__(message)
        self.code = code


def read_manifest() -> dict:
    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InstallError(f"Manifest is unreadable: {type(exc).__name__}") from exc
    version = manifest.get("schema_version") if isinstance(manifest, dict) else None
    if not isinstance(version, int) or version < 3:
        raise InstallError("This installer requires package-manifest schema v3.")
    return manifest


def safe_relative(root: Path, value: str) -> Path:
    candidate = Path(value.replace("/", os.sep))
    if candidate.is_absolute() or ".." in candidate.parts:
        raise InstallError("Manifest contains an unsafe relative path.")
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise InstallError("Manifest path escapes the package.") from exc
    return resolved


def checksum_excluded(relative: Path) -> bool:
    return (
        any(part in EXCLUDED_DIRS for part in relative.parts)
        or relative.name in EXCLUDED_NAMES
        or relative.suffix.casefold() in EXCLUDED_SUFFIXES
    )


def verify_checksums() -> int:
    try:
        lines = CHECKSUM_PATH.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError) as exc:
        raise InstallError("checksums.sha256 is unreadable.") from exc
    recorded: dict[str, str] = {}
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        parts = line.split("  ", 1)
        if len(parts) != 2 or not re.fullmatch(r"[0-9A-Fa-f]{64}", parts[0]):
            raise InstallError(f"Invalid checksum line {line_number}.")
        expected, relative = parts
        name = Path(relative).as_posix()
        if name in recorded:
            raise InstallError(f"Duplicate checksum entry at line {line_number}.")
        path = safe_relative(PACKAGE_ROOT, relative)
        if not path.is_file():
            raise InstallError(f"Checksum target is missing at line {line_number}.")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected.casefold():
            raise InstallError(f"Checksum mismatch at line {line_number}.")
        recorded[name] = actual
    present: set[str] = set()
    for path in PACKAGE_ROOT.rglob("*"):
        relative = path.relative_to(PACKAGE_ROOT)
        if path.is_file() and not checksum_excluded(relative):
            present.add(relative.as_posix())
    if present != set(recorded):
        unlisted = sorted(present - set(recorded))
        missing = sorted(set(recorded) - present)
        raise InstallError(
            "Checksum inventory does not match the extracted package "
            f"({len(unlisted)} unlisted, {len(missing)} missing: {', '.join((unlisted + missing)[:3])})."
        )
    verify_aggregate(recorded)
    return len(recorded)


def verify_aggregate(recorded: dict[str, str]) -> None:
    """Check checksums.aggregate.json when present (sorted `path NUL sha256 LF` records)."""

    if not AGGREGATE_PATH.exists():
        return
    try:
        summary = json.loads(AGGREGATE_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InstallError("checksums.aggregate.json is unreadable.") from exc
    digest = hashlib.sha256()
    for name in sorted(recorded):
        digest.update(name.encode("utf-8") + b"\x00" + recorded[name].encode("ascii") + b"\n")
    if (
        not isinstance(summary, dict)
        or summary.get("file_count") != len(recorded)
        or str(summary.get("aggregate_source_sha256", "")).casefold() != digest.hexdigest()
    ):
        raise InstallError("checksums.aggregate.json does not match checksums.sha256.")


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


def env_path(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    return Path(os.path.expandvars(value)).expanduser() if value else None


def safe_name(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value) or value.endswith('.'):
        raise InstallError("Skill names must be safe single path segments.")
    if value.split('.')[0].casefold() in {'con', 'prn', 'aux', 'nul', *(f'com{i}' for i in range(1, 10)), *(f'lpt{i}' for i in range(1, 10))}:
        raise InstallError("Reserved device name is not an installation target.")
    return value


def no_links(path: Path) -> None:
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()) or (part.exists() and getattr(part.lstat(), 'st_file_attributes', 0) & 0x400):
            raise InstallError("Installation paths must not traverse links or reparse points.")


def knowledge_spec(manifest: dict) -> dict | None:
    spec = manifest.get("configuration", {}).get("knowledge_root")
    return spec if isinstance(spec, dict) and spec.get("owner") and spec.get("setup_entrypoint") else None


def teacher_spec(manifest: dict) -> dict | None:
    spec = manifest.get("configuration", {}).get("teacher_skill")
    return spec if isinstance(spec, dict) and spec.get("owner") and spec.get("setup_entrypoint") else None


def apply_defaults(manifest: dict, args: argparse.Namespace) -> None:
    """Fill every unset location with the default its owning component reads at run time."""

    name = safe_name(manifest["artifact"]["name"])
    knowledge = knowledge_spec(manifest)
    teacher = teacher_spec(manifest)
    if knowledge:
        owner = safe_name(str(knowledge["owner"]))
        if not args.config:
            args.config = str((env_path(KNOWLEDGE_CONFIG_ENV) or platform_config_root() / owner / "config.json").resolve())
        if not args.knowledge_state_dir:
            args.knowledge_state_dir = str((env_path(KNOWLEDGE_STATE_ENV) or platform_state_root() / owner).resolve())
    if teacher and not args.teacher_config_dir:
        owner = safe_name(str(teacher["owner"]))
        args.teacher_config_dir = str((env_path(TEACHER_CONFIG_ENV) or platform_config_root() / owner).resolve())
    if not args.state_dir:
        args.state_dir = str((platform_state_root() / name).resolve())


def teacher_config_path(args: argparse.Namespace) -> Path:
    return Path(args.teacher_config_dir).expanduser().resolve() / TEACHER_CONFIG_NAME


def managed_paths(manifest: dict, args: argparse.Namespace) -> list[Path]:
    paths: list[Path] = []
    if knowledge_spec(manifest):
        if not args.config:
            raise InstallError("Setup requires an explicit --config outside the Skill root for managed rollback.", 2)
        config = Path(args.config).expanduser().absolute()
        paths.extend([config, config.parent / OBSIDIAN_CONFIG_NAME])
    if teacher_spec(manifest) and args.teacher_config_dir:
        directory = Path(args.teacher_config_dir).expanduser().absolute()
        paths.extend([directory / TEACHER_CONFIG_NAME, directory / "config.tmp"])
    if args.state_dir:
        paths.append(Path(args.state_dir).expanduser().absolute() / STATE_MARKER)
    return paths


def managed_config(manifest: dict, args: argparse.Namespace, root: Path) -> list[tuple[Path | None, bytes | None]]:
    """Validate and snapshot every configuration file an install or setup may write.

    The teacher key file is deliberately not part of the snapshot: the installer
    never reads or writes a secret.
    """

    saved = []
    package = PACKAGE_ROOT.resolve()
    for item in managed_paths(manifest, args):
        no_links(item)
        item = item.resolve()
        if item == root or root in item.parents or item in root.parents:
            raise InstallError("Managed configuration must be outside the Skill root.")
        if item == package or package in item.parents:
            raise InstallError('Managed configuration must be outside the package.')
        if item.exists() and not item.is_file():
            raise InstallError('Managed configuration must be a regular file.')
        saved.append((item, item.read_bytes() if item.exists() else None))
    return saved


def restore_config(saved: list[tuple[Path | None, bytes | None]]) -> None:
    failures = []
    for item in saved:
        try:
            restore_one_config(item)
        except Exception as exc:
            failures.append(type(exc).__name__)
    if failures:
        raise InstallError('Configuration recovery_required: ' + ', '.join(failures), 52)


def restore_one_config(saved: tuple[Path | None, bytes | None]) -> None:
    path, data = saved
    if path is None:
        return
    no_links(path)
    if data is None:
        path.unlink(missing_ok=True)
        return
    if path.is_file() and path.read_bytes() == data:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_name(path.name + '.restore-' + uuid.uuid4().hex)
    scratch.write_bytes(data)
    os.replace(scratch, path)


def checked_state(manifest: dict, args: argparse.Namespace, root: Path) -> Path:
    state = Path(args.state_dir).expanduser().absolute() if args.state_dir else platform_state_root() / safe_name(manifest['artifact']['name'])
    no_links(state)
    state = state.resolve()
    if state == root or root in state.parents or state in root.parents:
        raise InstallError("State and backups must not overlap the Skill scan root.")
    if state == PACKAGE_ROOT.resolve() or PACKAGE_ROOT.resolve() in state.parents or state in PACKAGE_ROOT.resolve().parents:
        raise InstallError('State must not overlap the source package.')
    return state


def checked_knowledge_state(manifest: dict, args: argparse.Namespace, root: Path, state: Path) -> Path | None:
    if not knowledge_spec(manifest):
        return None
    path = Path(args.knowledge_state_dir).expanduser().absolute()
    no_links(path)
    path = path.resolve()
    package = PACKAGE_ROOT.resolve()
    if path == root or root in path.parents or path in root.parents:
        raise InstallError("Knowledge state must not overlap the Skill scan root.")
    if path == package or package in path.parents or path in package.parents:
        raise InstallError("Knowledge state must not overlap the source package.")
    if path == state or path in state.parents:
        raise InstallError("Knowledge state must not contain the installer state; purging it would remove backups and the teacher store.")
    if path == Path.home().resolve() or path == Path(path.anchor):
        raise InstallError("Knowledge state must be a dedicated directory.")
    protected = [recorded_knowledge_root(args)]
    if args.knowledge_root:
        protected.append(Path(args.knowledge_root).expanduser().resolve())
    if args.teacher_config_dir:
        protected.append(Path(args.teacher_config_dir).expanduser().resolve())
    if any(item is not None and (item == path or path in item.parents) for item in protected):
        raise InstallError("Knowledge state must not contain the knowledge root or the teacher configuration; purging it would delete them.")
    return path


def recorded_knowledge_root(args: argparse.Namespace) -> Path | None:
    try:
        data = json.loads(Path(args.config).expanduser().resolve().read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
        return None
    value = data.get("knowledge_root") if isinstance(data, dict) else None
    return Path(value).expanduser().resolve() if isinstance(value, str) and value else None


def generated_path(relative: Path) -> bool:
    """Caches and local stores that are never installed, verified or treated as drift."""

    return any(part in EXCLUDED_DIRS for part in relative.parts) or relative.suffix.casefold() in EXCLUDED_SUFFIXES


def payload_inventory(root: Path) -> dict[str, str]:
    result = {}
    for path in root.rglob('*'):
        no_links(path)
        if path.is_file():
            relative = path.relative_to(root)
            if generated_path(relative) or path.name in LOCAL_NOISE:
                continue
            result[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def check_installed(specs: list[dict[str, str]], targets: dict[str, Path]) -> None:
    for item in specs:
        source = safe_relative(PACKAGE_ROOT, item['path'])
        if payload_inventory(source) != payload_inventory(targets[item['name']]):
            raise InstallError("Installed payload differs from the package; review missing, changed or extra files before repair.", 51)


def payload_specs(manifest: dict) -> list[dict[str, str]]:
    safe_name(manifest["artifact"]["name"])
    raw = manifest.get("payloads")
    if raw is None:
        name = safe_name(manifest["artifact"]["name"])
        return [{"name": name, "path": str(manifest["payload_root"]), "role": "primary"}]
    if not isinstance(raw, list) or not raw:
        raise InstallError("Manifest payloads must be a nonempty array.")
    specs: list[dict[str, str]] = []
    seen: set[str] = set()
    primary = 0
    for item in raw:
        if not isinstance(item, dict):
            raise InstallError("Every payload entry must be an object.")
        name, path, role = (str(item.get(key, "")).strip() for key in ("name", "path", "role"))
        if not name or not path or role not in {"primary", "dependency"} or name in seen:
            raise InstallError("Payload entries require unique names, relative paths, and primary/dependency roles.")
        safe_name(name)
        if name.casefold() in {value.casefold() for value in seen}:
            raise InstallError("Payload names collide on a case-insensitive filesystem.")
        safe_relative(PACKAGE_ROOT, path)
        seen.add(name)
        primary += role == "primary"
        specs.append({"name": name, "path": path, "role": role})
    if primary != 1:
        raise InstallError("A portable Skill suite must declare exactly one primary payload.")
    graph = manifest.get("dependency_graph", {})
    if not isinstance(graph, dict) or any(
        key not in seen or not isinstance(value, list) or any(dep not in seen for dep in value)
        for key, value in graph.items()
    ):
        raise InstallError("The dependency graph may only name declared payloads.")
    return specs


def check_payload_sources(specs: list[dict[str, str]]) -> None:
    for item in specs:
        source = safe_relative(PACKAGE_ROOT, item["path"])
        if not source.is_dir() or not (source / "SKILL.md").is_file():
            raise InstallError(f"Payload {item['name']} is incomplete: SKILL.md is missing.", 50)


def target_root(manifest: dict, args: argparse.Namespace, specs: list[dict[str, str]]) -> Path:
    if args.target_root:
        return Path(args.target_root).expanduser().resolve()
    if len(specs) == 1 and args.target_dir:
        return Path(args.target_dir).expanduser().resolve().parent
    if args.agent not in AGENT_HOMES:
        raise InstallError("A generic agent installation requires --target-root.", 2)
    variable, folder = AGENT_HOMES[args.agent]
    home = env_path(variable) or Path.home() / folder
    return (home / "skills").resolve()


def target_map(manifest: dict, args: argparse.Namespace) -> tuple[list[dict[str, str]], Path, dict[str, Path]]:
    specs = payload_specs(manifest)
    if args.target_dir:
        no_links(Path(args.target_dir).expanduser().absolute())
    if args.target_root:
        no_links(Path(args.target_root).expanduser().absolute())
    root = target_root(manifest, args, specs)
    if len(specs) > 1 and args.target_dir:
        raise InstallError("Multi-Skill bundles use --target-root, not --target-dir.", 2)
    targets = {item["name"]: root / item["name"] for item in specs}
    if len(specs) == 1 and args.target_dir:
        targets[specs[0]["name"]] = Path(args.target_dir).expanduser().resolve()
    no_links(root)
    for target in targets.values():
        no_links(target)
        if target.resolve().parent != root or target.resolve() == root:
            raise InstallError("Installation target must be an immediate child of the selected root.")
        source_root = PACKAGE_ROOT.resolve()
        if target.resolve() == source_root or target.resolve() in source_root.parents or source_root in target.resolve().parents:
            raise InstallError("Installation target must not overlap the source package.")
    return specs, root, targets


def copy_payload(source: Path, stage: Path) -> None:
    def ignore(directory: str, names: list[str]) -> set[str]:
        base = Path(directory).relative_to(source)
        return {name for name in names if generated_path(base / name) or name in LOCAL_NOISE}
    shutil.copytree(source, stage, ignore=ignore)


def confirm(prompt: str, yes: bool, default: bool = False) -> bool:
    if yes:
        return True
    suffix = "Y/n" if default else "y/N"
    try:
        answer = input(f"{prompt} [{suffix}] ").strip().casefold()
    except EOFError:
        return False
    return default if not answer else answer in {"y", "yes"}


def python_environment(skills_root: Path | None = None, extra: dict[str, str] | None = None) -> dict[str, str]:
    values = os.environ.copy()
    values["PYTHONDONTWRITEBYTECODE"] = "1"
    values.setdefault("PYTHONUTF8", "1")
    if skills_root is not None:
        values["MPK_SKILLS_ROOT"] = str(skills_root)
    values.update(extra or {})
    return values


def run_command(command: list[str], cwd: Path, *, skills_root: Path | None = None, extra_env: dict[str, str] | None = None) -> None:
    completed = subprocess.run(command, cwd=str(cwd), env=python_environment(skills_root, extra_env), check=False)
    if completed.returncode != 0:
        code = 2 if completed.returncode == 2 else 50
        raise InstallError(f"Setup or validation command failed with exit code {completed.returncode}.", code)


def primary_spec(specs: list[dict[str, str]]) -> dict[str, str]:
    return next(item for item in specs if item["role"] == "primary")


def owner_root(owner: str, targets: dict[str, Path]) -> Path:
    """The installed owner Skill, or the package copy when it is no longer installed."""

    target = targets.get(owner)
    if target is not None and target.is_dir():
        return target
    return PACKAGE_ROOT / "payload" / safe_name(owner)


def setup_command(
    manifest: dict, targets: dict[str, Path], args: argparse.Namespace, mode: str
) -> tuple[list[str], Path] | None:
    """Knowledge-root setup/check/remove through the owning Skill's setup entrypoint."""

    spec = knowledge_spec(manifest)
    if not spec:
        return None
    owner = owner_root(str(spec["owner"]), targets)
    setup = safe_relative(owner, str(spec["setup_entrypoint"]))
    command = [sys.executable, "-B", str(setup), mode]
    if args.knowledge_root:
        command.extend(["--knowledge-root", str(Path(args.knowledge_root).expanduser().resolve())])
    if args.vault:
        command.extend(["--vault", args.vault])
    if args.library:
        command.extend(["--library", args.library])
    if args.build_vault_index:
        command.append("--build-vault-index")
    if args.build_pdf_index:
        command.append("--build-pdf-index")
    command.extend(["--config", str(Path(args.config).expanduser().resolve())])
    command.extend(["--state-dir", str(Path(args.knowledge_state_dir).expanduser().resolve())])
    if args.purge_local_state:
        command.append("--purge")
    if args.yes:
        command.append("--yes")
    return command, owner


def teacher_requested(args: argparse.Namespace) -> bool:
    return args.network is not None or bool(args.read_root)


def check_teacher_inputs(args: argparse.Namespace) -> None:
    for item in args.read_root or []:
        if not Path(item).expanduser().is_dir():
            raise InstallError("Every --read-root must be an existing directory.", 2)


def teacher_setup_command(manifest: dict, targets: dict[str, Path], args: argparse.Namespace) -> tuple[list[str], Path] | None:
    """`teacher.py setup` with only the flags the user supplied."""

    spec = teacher_spec(manifest)
    if not spec or not teacher_requested(args):
        return None
    owner = targets[str(spec["owner"])]
    entry = safe_relative(owner, str(spec["setup_entrypoint"]))
    command = [sys.executable, "-B", str(entry), *[str(item) for item in spec.get("setup_arguments", ["setup"])]]
    if args.network is not None:
        command.append(f"--network={args.network}")
    for item in args.read_root or []:
        command.append(f"--read-root={Path(item).expanduser().resolve()}")
    return command, owner


def check_teacher_config(args: argparse.Namespace) -> None:
    path = teacher_config_path(args)
    no_links(path)
    if not path.is_file():
        raise InstallError("Teacher Skill configuration is missing; rerun with --setup and the same --read-root/--network options.", 2)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InstallError("Teacher Skill configuration is unreadable.", 50) from exc
    if not isinstance(data, dict) or not str(data.get("schema", "")).startswith("teacher-config/"):
        raise InstallError("Teacher Skill configuration has an unexpected schema.", 50)
    roots = data.get("read_roots") if isinstance(data.get("read_roots"), list) else []
    if any(str(Path(item).expanduser().resolve()) not in roots for item in args.read_root or []):
        raise InstallError("Teacher Skill configuration was not written as requested.", 50)
    if args.network is not None:
        network = data.get("network") if isinstance(data.get("network"), dict) else {}
        if bool(network.get("enabled")) != (args.network == "on"):
            raise InstallError("Teacher Skill configuration was not written as requested.", 50)


def read_marker(args: argparse.Namespace) -> dict:
    try:
        data = json.loads((Path(args.state_dir).expanduser().resolve() / STATE_MARKER).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def record_configuration(manifest: dict, args: argparse.Namespace, done: dict) -> None:
    """Remember which optional parts were configured, so doctor can detect a missing configuration."""

    if not done:
        return
    data = read_marker(args)
    data["schema"] = "teacher-install-state/v1"
    if "knowledge_root" in done:
        config = Path(args.config).expanduser().resolve()
        data["knowledge_root"] = {"configured": True, "config": str(config),
                                  "obsidian_config": str(config.parent / OBSIDIAN_CONFIG_NAME),
                                  "state": str(Path(args.knowledge_state_dir).expanduser().resolve())}
    if "teacher_skill" in done:
        data["teacher_skill"] = {"configured": True, "config": str(teacher_config_path(args))}
    path = Path(args.state_dir).expanduser().resolve() / STATE_MARKER
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_name(path.name + ".write-" + uuid.uuid4().hex)
    scratch.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(scratch, path)


def knowledge_expected(manifest: dict, args: argparse.Namespace, marker: dict) -> bool:
    if not knowledge_spec(manifest):
        return False
    recorded = marker.get("knowledge_root") if isinstance(marker.get("knowledge_root"), dict) else {}
    return bool(args.knowledge_root) or recorded.get("configured") is True or Path(args.config).expanduser().resolve().exists()


def teacher_expected(manifest: dict, args: argparse.Namespace, marker: dict) -> bool:
    if not teacher_spec(manifest):
        return False
    recorded = marker.get("teacher_skill") if isinstance(marker.get("teacher_skill"), dict) else {}
    return teacher_requested(args) or recorded.get("configured") is True or teacher_config_path(args).exists()


def validation_commands(manifest: dict, targets: dict[str, Path], root: Path) -> list[list[str]]:
    commands: list[list[str]] = []
    primary = targets[primary_spec(payload_specs(manifest))["name"]]
    for text in manifest.get("validation", []):
        replaced = str(text).replace("<INSTALLED_SKILL>", str(primary)).replace("<INSTALLED_ROOT>", str(root))
        for name, target in targets.items():
            replaced = replaced.replace(f"<SKILL:{name}>", str(target))
        parts = shlex.split(replaced, posix=True)
        if parts and parts[0].casefold() in {"python", "python3", "py"}:
            parts[0] = sys.executable
        if parts:
            commands.append(parts)
    return commands


def write_report(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def backup_target(target: Path, state: Path, label: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = state / "backups" / f"{target.name}-{label}-{stamp}-{uuid.uuid4().hex[:8]}"
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(target), str(backup))
    return backup


def require_setup_input(manifest: dict, args: argparse.Namespace) -> None:
    """The knowledge root is optional: the teacher Skill itself never needs a Vault."""

    knowledge = knowledge_spec(manifest)
    if not knowledge:
        return
    if (not args.knowledge_root and not args.yes and knowledge.get("prompt_on_first_run") is True
            and not Path(args.config).expanduser().resolve().exists()):
        try:
            args.knowledge_root = input("Local knowledge-root path (press Enter to skip; the teacher Skill does not need one): ").strip().strip('"')
        except EOFError:
            args.knowledge_root = ""
    if args.knowledge_root:
        if not Path(args.knowledge_root).expanduser().is_dir():
            raise InstallError("--knowledge-root must be an existing directory.", 2)
        if args.yes and (not args.vault or not args.library):
            raise InstallError("Non-interactive knowledge-root setup requires --vault and --library.", 2)
    elif args.vault or args.library or args.build_vault_index or args.build_pdf_index:
        raise InstallError("--vault, --library and the index options require --knowledge-root.", 2)


def run_setup_and_validation(
    manifest: dict, args: argparse.Namespace, specs: list[dict[str, str]], root: Path, targets: dict[str, Path], mode: str
) -> dict[str, str]:
    status: dict[str, str] = {}
    if mode == "setup":
        if args.knowledge_root:
            knowledge = setup_command(manifest, targets, args, "setup")
            if knowledge:
                run_command(knowledge[0], knowledge[1], skills_root=root)
                status["knowledge_root"] = "configured"
        teacher = teacher_setup_command(manifest, targets, args)
        if teacher:
            run_command(teacher[0], teacher[1], skills_root=root,
                        extra_env={TEACHER_CONFIG_ENV: str(Path(args.teacher_config_dir).expanduser().resolve())})
            status["teacher_skill"] = "configured"
        return status
    marker = read_marker(args)
    if knowledge_expected(manifest, args, marker):
        knowledge = setup_command(manifest, targets, args, "check")
        if knowledge:
            run_command(knowledge[0], knowledge[1], skills_root=root)
        status["knowledge_root"] = "healthy"
    elif knowledge_spec(manifest):
        status["knowledge_root"] = "not_configured"
    if teacher_expected(manifest, args, marker):
        check_teacher_config(args)
        status["teacher_skill"] = "configured"
    elif teacher_spec(manifest):
        status["teacher_skill"] = "not_configured"
    for command in validation_commands(manifest, targets, root):
        run_command(command, root, skills_root=root)
    return status


def configuration_summary(manifest: dict, args: argparse.Namespace, status: dict[str, str]) -> dict:
    summary: dict = dict(status)
    runtime: dict[str, str] = {}
    spec = knowledge_spec(manifest)
    if spec:
        config = Path(args.config).expanduser().resolve()
        state = Path(args.knowledge_state_dir).expanduser().resolve()
        owner = safe_name(str(spec["owner"]))
        summary.update(knowledge_config=str(config), obsidian_config=str(config.parent / OBSIDIAN_CONFIG_NAME), knowledge_state=str(state))
        if config != (platform_config_root() / owner / "config.json").resolve():
            runtime[KNOWLEDGE_CONFIG_ENV] = str(config)
        if state != (platform_state_root() / owner).resolve():
            runtime[KNOWLEDGE_STATE_ENV] = str(state)
    spec = teacher_spec(manifest)
    if spec:
        directory = Path(args.teacher_config_dir).expanduser().resolve()
        summary["teacher_config"] = str(directory / TEACHER_CONFIG_NAME)
        if directory != (platform_config_root() / safe_name(str(spec["owner"]))).resolve():
            runtime[TEACHER_CONFIG_ENV] = str(directory)
    if runtime:
        summary["runtime_environment"] = runtime
    return summary


def configuration_plan(manifest: dict, args: argparse.Namespace) -> dict:
    plan = {}
    if knowledge_spec(manifest):
        plan["knowledge_root"] = "configure" if args.knowledge_root else "skip (optional)"
    if teacher_spec(manifest):
        plan["teacher_skill"] = "configure" if teacher_requested(args) else "skip (optional)"
    return configuration_summary(manifest, args, plan)


def install(manifest: dict, args: argparse.Namespace) -> dict:
    apply_defaults(manifest, args)
    checked = verify_checksums()
    specs, root, targets = target_map(manifest, args)
    facts = preflight(manifest, args, root, specs)
    if args.dry_run:
        return {"status": "dry-run", "targets": [str(targets[item["name"]]) for item in specs], "checksums_verified": checked,
                'preflight': facts, "configuration": configuration_plan(manifest, args)}
    existing = [target for target in targets.values() if target.exists()]
    if existing and not args.replace:
        raise InstallError("One or more targets already exist. Review them, then rerun with --replace.", 21)
    require_setup_input(manifest, args)
    if not confirm("Install the Teacher Skill suite and run the requested local setup?", args.yes, default=True):
        raise InstallError("Installation cancelled.", 2)
    state = checked_state(manifest, args, root)
    checked_knowledge_state(manifest, args, root, state)
    saved_config = managed_config(manifest, args, root)
    root.mkdir(parents=True, exist_ok=True)
    stage_root = root / f".{manifest['artifact']['name']}.install-{uuid.uuid4().hex}"
    backups: dict[str, Path] = {}
    installed: list[Path] = []
    stage_root.mkdir()
    try:
        for item in specs:
            copy_payload(safe_relative(PACKAGE_ROOT, item["path"]), stage_root / item["name"])
        for item in specs:
            target = targets[item["name"]]
            if target.exists():
                backups[item["name"]] = backup_target(target, state, "pre-update")
            shutil.move(str(stage_root / item["name"]), str(target))
            installed.append(target)
        done = run_setup_and_validation(manifest, args, specs, root, targets, "setup")
        health = run_setup_and_validation(manifest, args, specs, root, targets, "check")
        check_installed(specs, targets)
        record_configuration(manifest, args, done)
    except Exception as original:
        recovery_errors = []
        for target in reversed(installed):
            try:
                no_links(target)
                if target.exists():
                    shutil.rmtree(target)
            except Exception as error:
                recovery_errors.append("remove-new:" + target.name + ":" + type(error).__name__)
        for name, backup in backups.items():
            try:
                no_links(targets[name])
                no_links(backup)
                if backup.exists():
                    if targets[name].exists():
                        raise InstallError("Cannot restore over an unresolved target.")
                    shutil.move(str(backup), str(targets[name]))
            except Exception as error:
                recovery_errors.append("restore-old:" + name + ":" + type(error).__name__)
        try:
            restore_config(saved_config)
        except Exception as error:
            recovery_errors.append("restore-config:" + type(error).__name__)
        try:
            if stage_root.exists():
                shutil.rmtree(stage_root)
        except Exception as error:
            recovery_errors.append("clean-stage:" + type(error).__name__)
        if recovery_errors:
            detail = {"status": "recovery_required", "original_error": type(original).__name__,
                      "original_message": str(original), "recovery_errors": recovery_errors,
                      "retained_backups": {name: str(path) for name, path in backups.items() if path.exists()}}
            raise InstallError(json.dumps(detail, ensure_ascii=False), 52) from original
        raise
    else:
        if stage_root.exists():
            shutil.rmtree(stage_root)
    return {
        "status": "installed", "agent": args.agent, "target_root": str(root),
        "targets": {name: str(path) for name, path in targets.items()},
        "backups": {name: str(path) for name, path in backups.items()},
        "checksums_verified": checked,
        "configuration": {**configuration_summary(manifest, args, health), "configured_now": sorted(done)},
        "next_step": "Start a new agent session so the agent discovers the installed Skills.",
    }


def setup_only(manifest: dict, args: argparse.Namespace) -> dict:
    apply_defaults(manifest, args)
    verify_checksums()
    specs, root, targets = target_map(manifest, args)
    if any(not path.is_dir() for path in targets.values()):
        raise InstallError("One or more installed Skill targets are missing.")
    check_teacher_inputs(args)
    require_setup_input(manifest, args)
    if not args.knowledge_root and not teacher_requested(args):
        raise InstallError("Nothing to configure: supply --knowledge-root with --vault and --library, and/or --read-root or --network.", 2)
    state = checked_state(manifest, args, root)
    checked_knowledge_state(manifest, args, root, state)
    saved_config = managed_config(manifest, args, root)
    try:
        done = run_setup_and_validation(manifest, args, specs, root, targets, "setup")
        health = run_setup_and_validation(manifest, args, specs, root, targets, "check")
        check_installed(specs, targets)
        record_configuration(manifest, args, done)
    except Exception:
        restore_config(saved_config)
        raise
    return {"status": "configured", "target_root": str(root),
            "configuration": {**configuration_summary(manifest, args, health), "configured_now": sorted(done)}}


def doctor(manifest: dict, args: argparse.Namespace) -> dict:
    apply_defaults(manifest, args)
    checked = verify_checksums()
    specs, root, targets = target_map(manifest, args)
    missing = [name for name, path in targets.items() if not path.is_dir()]
    if missing:
        raise InstallError("Installed targets are missing: " + ", ".join(missing))
    check_installed(specs, targets)
    health = run_setup_and_validation(manifest, args, specs, root, targets, "check")
    check_installed(specs, targets)
    return {"status": "healthy", "installed_integrity": "verified", "validation_scope": "manifest-declared commands",
            "target_root": str(root), "checksums_verified": checked, "configuration": configuration_summary(manifest, args, health)}


def purge_local_files(args: argparse.Namespace, state: Path) -> tuple[list[str], list[str]]:
    """Remove installer-managed configuration, reports and backups; never a research store."""

    removed: list[str] = []
    directories: list[Path] = []
    if args.teacher_config_dir:
        teacher_dir = Path(args.teacher_config_dir).expanduser().resolve()
        directories.append(teacher_dir)
        for name in (TEACHER_CONFIG_NAME, "config.tmp"):
            path = teacher_dir / name
            no_links(path)
            if path.is_file():
                path.unlink()
                removed.append(str(path))
    backups = state / "backups"
    no_links(backups)
    if backups.is_dir():
        shutil.rmtree(backups)
        removed.append(str(backups))
    for name in (REPORT_NAME, STATE_MARKER):
        path = state / name
        if path.is_file():
            path.unlink()
            removed.append(str(path))
    directories.append(state)
    preserved: list[str] = []
    for directory in dict.fromkeys(directories):
        if directory.is_dir():
            leftovers = sorted(directory.iterdir())
            if leftovers:
                preserved.extend(str(path) for path in leftovers if str(path) not in preserved)
            else:
                directory.rmdir()
    return removed, preserved


def uninstall(manifest: dict, args: argparse.Namespace) -> dict:
    apply_defaults(manifest, args)
    specs, root, targets = target_map(manifest, args)
    existing = [path for path in targets.values() if path.is_dir()]
    if not existing:
        raise InstallError("Installed targets are missing.")
    if args.purge_local_state and not args.yes:
        raise InstallError("Purging local state requires --purge-local-state together with --yes.", 2)
    if not confirm(f"Remove {len(existing)} installed Skill target(s) under {root}?", args.yes):
        raise InstallError("Uninstall cancelled.", 2)
    state = checked_state(manifest, args, root)
    if args.purge_local_state:
        knowledge_state = checked_knowledge_state(manifest, args, root, state)
        saved = managed_config(manifest, args, root)
        # managed_paths lists the knowledge config and its Obsidian companion first.
        knowledge_items = [path for path, _ in saved[:2]] if knowledge_spec(manifest) else []
        knowledge_items += [knowledge_state] if knowledge_state else []
        present = [path for path in knowledge_items if path.exists()]
        command = setup_command(manifest, targets, args, "remove")
        if command:
            run_command(command[0], command[1], skills_root=root)
        for target in existing:
            shutil.rmtree(target)
        removed, preserved = purge_local_files(args, state)
        removed = [str(path) for path in present if not path.exists()] + removed
        return {"status": "uninstalled-and-purged", "target_root": str(root), "removed": removed, "preserved": preserved,
                "note": "Knowledge roots, research projects and the teacher store are never purged."}
    backups = {target.name: str(backup_target(target, state, "uninstalled")) for target in existing}
    return {"status": "uninstalled", "target_root": str(root), "backups": backups,
            "configuration": "preserved; rerun the same install command to restore discovery"}


def secret_argument(argv: list[str]) -> bool:
    for item in argv:
        if item == "--":
            break
        if item.startswith("--") and any(word in item[2:].split("=", 1)[0].casefold() for word in SECRET_WORDS):
            return True
    return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--agent", choices=("codex", "claude", "generic"), default="codex",
                        help="codex: ${CODEX_HOME:-~/.codex}/skills; claude: ${CLAUDE_CONFIG_DIR:-~/.claude}/skills; generic requires --target-root.")
    parser.add_argument("--target-root", help="Directory that will contain every declared Skill payload.")
    parser.add_argument("--target-dir", help="Exact destination for a single Skill (advanced).")
    knowledge = parser.add_argument_group("optional knowledge root (manage-personal-knowledge, obsidian-vault-notes)")
    knowledge.add_argument("--knowledge-root")
    knowledge.add_argument("--vault", help="Knowledge-root-relative Obsidian Vault candidate.")
    knowledge.add_argument("--library", help="Knowledge-root-relative PDF library candidate.")
    knowledge.add_argument("--build-vault-index", action="store_true")
    knowledge.add_argument("--build-pdf-index", action="store_true")
    knowledge.add_argument("--config", help="Knowledge-root configuration file (default: the manage-personal-knowledge location).")
    knowledge.add_argument("--knowledge-state-dir", help="Knowledge indexes and tool state (default: the manage-personal-knowledge location).")
    teacher = parser.add_argument_group("optional teacher Skill read scope (runs teacher.py setup only when one of these is given)")
    teacher.add_argument("--read-root", action="append", help="A directory the Skill may re-read a cited excerpt from (repeatable).")
    teacher.add_argument("--network", choices=("on", "off"), help="Whether the Skill may re-fetch a cited URL.")
    teacher.add_argument("--teacher-config-dir", help="Teacher Skill configuration directory (default: the Skill location).")
    parser.add_argument("--state-dir", help="Installer state: backups and INSTALL_REPORT.json.")
    parser.add_argument("--report")
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--setup", action="store_true")
    parser.add_argument("--doctor", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--purge-local-state", action="store_true")
    return parser.parse_args()


def preflight(manifest: dict, args: argparse.Namespace, root: Path, specs: list[dict[str, str]]) -> dict:
    if sys.platform not in {'win32', 'linux', 'darwin'}:
        raise InstallError('Supported systems: Windows, Linux and macOS.', 31)
    check_payload_sources(specs)
    state = checked_state(manifest, args, root)
    checked_knowledge_state(manifest, args, root, state)
    managed_config(manifest, args, root)
    check_teacher_inputs(args)
    probe = root
    while not probe.exists():
        probe = probe.parent
    if not probe.is_dir() or not os.access(probe, os.W_OK):
        raise InstallError('Installation parent is not writable; choose a user-owned --target-root.', 40)
    required = sum(p.stat().st_size for p in (PACKAGE_ROOT / 'payload').rglob('*') if p.is_file()) * 3 + 10 * 1024 * 1024
    free = shutil.disk_usage(probe).free
    if free < required:
        raise InstallError('Insufficient free space for staging and rollback.', 40)
    return {'os': platform.system(), 'architecture': platform.machine(), 'python': platform.python_version(), 'agent': args.agent,
            'target_root': str(root), 'free_bytes': free, 'required_bytes': required, 'network': 'not_required',
            'package_manager': 'not_required', 'state': str(state), 'config': args.config}


def main() -> int:
    if sys.version_info < MIN_PYTHON:
        print("Python 3.10 or newer is required. No automatic download was attempted.", file=sys.stderr)
        return 11
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    if secret_argument(sys.argv[1:]):
        print("Secrets are never accepted on the command line.", file=sys.stderr)
        return 2
    args = parse_args()
    try:
        manifest = read_manifest()
        if args.dry_run and (args.uninstall or args.setup):
            raise InstallError("--dry-run previews an installation; use --doctor for a read-only check.", 2)
        if args.uninstall:
            result = uninstall(manifest, args)
        elif args.doctor:
            result = doctor(manifest, args)
        elif args.setup:
            result = setup_only(manifest, args)
        else:
            result = install(manifest, args)
        state = Path(args.state_dir).expanduser().resolve() if args.state_dir else platform_state_root() / manifest["artifact"]["name"]
        purged = result.get("status") == "uninstalled-and-purged" or args.doctor or args.dry_run
        report = Path(args.report).expanduser().resolve() if args.report else None if purged else state / REPORT_NAME
        if report is not None:
            write_report(report, {**result, "timestamp_utc": datetime.now(timezone.utc).isoformat()})
        print(json.dumps({**result, "report": str(report) if report else None}, ensure_ascii=False))
        return 0
    except InstallError as exc:
        print(str(exc), file=sys.stderr)
        return exc.code
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"Installation failed: {type(exc).__name__}", file=sys.stderr)
        return 40


if __name__ == "__main__":
    raise SystemExit(main())
