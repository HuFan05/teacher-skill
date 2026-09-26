from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

if os.name == "nt":
    from ctypes import wintypes

    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.GetCurrentProcess.argtypes = []
    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p
    _advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    _advapi32.OpenProcessToken.restype = wintypes.BOOL
    _advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _advapi32.GetTokenInformation.restype = wintypes.BOOL
    _advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    _advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    _advapi32.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.LPWSTR),
    ]
    _advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL

    class _SidAndAttributes(ctypes.Structure):
        _fields_ = [("sid", ctypes.c_void_p), ("attributes", wintypes.DWORD)]

    class _TokenUser(ctypes.Structure):
        _fields_ = [("user", _SidAndAttributes)]


class BundleError(ValueError):
    pass


def _windows_sid_to_string(sid: int) -> str:
    string_sid = wintypes.LPWSTR()
    if not _advapi32.ConvertSidToStringSidW(
        ctypes.c_void_p(sid), ctypes.byref(string_sid)
    ):
        raise ctypes.WinError()
    try:
        return string_sid.value
    finally:
        _kernel32.LocalFree(ctypes.cast(string_sid, ctypes.c_void_p))


def windows_path_owner_sid(path: Path) -> str:
    if os.name != "nt":
        raise RuntimeError("Windows owner lookup is available only on Windows")
    owner_sid = ctypes.c_void_p()
    security_descriptor = ctypes.c_void_p()
    result = _advapi32.GetNamedSecurityInfoW(
        str(path),
        1,  # SE_FILE_OBJECT
        1,  # OWNER_SECURITY_INFORMATION
        ctypes.byref(owner_sid),
        None,
        None,
        None,
        ctypes.byref(security_descriptor),
    )
    if result != 0:
        raise OSError(result, f"Could not read Windows owner for {path}")
    try:
        return _windows_sid_to_string(owner_sid.value)
    finally:
        _kernel32.LocalFree(security_descriptor)


def windows_current_user_sid() -> str:
    if os.name != "nt":
        raise RuntimeError("Windows token lookup is available only on Windows")

    token = wintypes.HANDLE()
    if not _advapi32.OpenProcessToken(
        _kernel32.GetCurrentProcess(),
        0x0008,  # TOKEN_QUERY
        ctypes.byref(token),
    ):
        raise ctypes.WinError()
    try:
        required = wintypes.DWORD()
        _advapi32.GetTokenInformation(
            token, 1, None, 0, ctypes.byref(required)  # TokenUser
        )
        buffer = ctypes.create_string_buffer(required.value)
        if not _advapi32.GetTokenInformation(
            token,
            1,
            buffer,
            required,
            ctypes.byref(required),
        ):
            raise ctypes.WinError()
        token_user = ctypes.cast(buffer, ctypes.POINTER(_TokenUser)).contents
        return _windows_sid_to_string(token_user.user.sid)
    finally:
        _kernel32.CloseHandle(token)


def require_windows_publication_owner(
    parent: Path,
    *,
    platform_name: str | None = None,
    path_owner_sid=windows_path_owner_sid,
    current_user_sid=windows_current_user_sid,
) -> None:
    platform_name = os.name if platform_name is None else platform_name
    if platform_name != "nt":
        return
    if path_owner_sid(parent) != current_user_sid():
        raise BundleError(
            "Windows Vault parent owner does not match the current process identity; "
            "rerun this exact --execute command through the sandbox approval mechanism"
        )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative_inside(root: Path, path: Path, label: str) -> Path:
    try:
        return path.relative_to(root)
    except ValueError as exc:
        raise BundleError(f"{label} escapes the Vault root") from exc


def build_plan(
    vault_root_arg: str,
    parent_arg: str,
    bundle_name: str,
    source_args: list[str],
) -> dict[str, object]:
    vault_root = Path(vault_root_arg).expanduser().resolve()
    if not vault_root.is_dir():
        raise BundleError("Vault root does not exist or is not a directory")

    parent_input = Path(parent_arg)
    if parent_input.is_absolute():
        raise BundleError("--parent must be relative to the Vault root")
    parent = (vault_root / parent_input).resolve()
    relative_parent = relative_inside(vault_root, parent, "Parent")
    if not parent.is_dir():
        raise BundleError("Parent does not exist or is not a directory")

    if not bundle_name or bundle_name in {".", ".."}:
        raise BundleError("Bundle name must be one non-empty child-folder name")
    if Path(bundle_name).name != bundle_name or "/" in bundle_name or "\\" in bundle_name:
        raise BundleError("Bundle name must not contain path separators")

    if len(source_args) < 2:
        raise BundleError("Grouped delivery requires at least two source files")
    sources = [Path(value).expanduser().resolve() for value in source_args]
    for source in sources:
        if not source.is_file():
            raise BundleError(f"Source does not exist or is not a file: {source}")
    if all(source.suffix.lower() == ".md" for source in sources):
        raise BundleError("This workflow applies only when the set includes a non-Markdown file")

    names = [source.name for source in sources]
    if len(set(names)) != len(names):
        raise BundleError("Source basenames must be unique")

    target = (parent / bundle_name).resolve()
    relative_inside(parent, target, "Target")
    if target.parent != parent:
        raise BundleError("Target must be a direct child of the named parent")
    if target.exists():
        raise BundleError("Destination child folder already exists")

    files = [
        {
            "source": str(source),
            "destination_name": source.name,
            "bytes": source.stat().st_size,
            "sha256": sha256_file(source),
        }
        for source in sources
    ]
    return {
        "vault_root": str(vault_root),
        "parent": relative_parent.as_posix(),
        "bundle_name": bundle_name,
        "destination": target.relative_to(vault_root).as_posix(),
        "files": files,
        "_parent_path": parent,
        "_target_path": target,
        "_source_paths": sources,
    }


def execute_plan(plan: dict[str, object]) -> None:
    parent = plan["_parent_path"]
    target = plan["_target_path"]
    sources = plan["_source_paths"]
    assert isinstance(parent, Path)
    assert isinstance(target, Path)
    assert isinstance(sources, list)

    require_windows_publication_owner(parent)
    stage = Path(tempfile.mkdtemp(prefix=f".{target.name}.bundle-stage-", dir=parent))
    try:
        for source in sources:
            assert isinstance(source, Path)
            destination = stage / source.name
            shutil.copy2(source, destination)
            if sha256_file(source) != sha256_file(destination):
                raise BundleError(f"Hash verification failed: {source.name}")
        if target.exists():
            raise BundleError("Destination child folder appeared during copying")
        os.replace(stage, target)
    except Exception:
        if stage.exists() and stage.parent == parent and ".bundle-stage-" in stage.name:
            shutil.rmtree(stage)
        raise


def public_plan(plan: dict[str, object], executed: bool) -> dict[str, object]:
    return {
        "ok": True,
        "executed": executed,
        "destination": plan["destination"],
        "file_count": len(plan["files"]),
        "files": plan["files"],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preflight and copy a grouped multi-file deliverable into one Vault child folder."
    )
    parser.add_argument("--vault-root", required=True)
    parser.add_argument("--parent", required=True)
    parser.add_argument("--bundle-name", required=True)
    parser.add_argument("--source", action="append", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--campaign-terminal", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


# Shared output support is resolved from this script, including importlib callers.
import sys as _output_sys
from pathlib import Path as _OutputPath
_output_dir = str(_OutputPath(__file__).resolve().parent)
if _output_dir not in _output_sys.path:
    _output_sys.path.insert(0, _output_dir)
from note_public_output import public_entry

@public_entry('vault_file_bundle')
def main() -> int:
    args = parse_args()
    try:
        plan = build_plan(args.vault_root, args.parent, args.bundle_name, args.source)
        if args.execute:
            execute_plan(plan)
        payload = public_plan(plan, args.execute)
        if args.campaign_terminal:
            payload["terminal_state"] = "success"
        print(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=None if args.campaign_terminal else 2,
            )
        )
        return 0
    except (BundleError, OSError) as exc:
        payload = {"ok": False, "error": str(exc)}
        if args.campaign_terminal:
            payload["terminal_state"] = "blocked"
        print(json.dumps(payload, ensure_ascii=False), file=sys.stderr if not args.campaign_terminal else sys.stdout)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
