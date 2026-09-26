"""Owned scratch directories compatible with Windows write-restricted tokens.

Windows scratch inherits the caller-selected parent's ACL. No ACL is edited and
no new authority is granted. POSIX keeps tempfile's private 0700 directory mode.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import sys
import tempfile
import uuid


def system_alias(path):
    """Recognize the OS-owned macOS root aliases tmp, var and etc.

    On macOS these root-level names point into the system "private" directory.
    They are platform directory aliases, not links created inside or around a
    research project; every other symbolic link or reparse point stays rejected.
    """
    path = Path(path)
    if sys.platform != 'darwin' or path.parent != Path(path.anchor or os.sep) or path.name not in {'tmp', 'var', 'etc'}:
        return False
    try:
        target = Path(os.readlink(path))
    except OSError:
        return False
    parts = target.parts[1:] if target.is_absolute() else target.parts
    return parts == ('private', path.name)


def mkdtemp(suffix='', prefix='tmp', dir=None):
    if os.name != 'nt':
        return tempfile.mkdtemp(suffix=suffix, prefix=prefix, dir=dir)
    # Python's Windows 0700 mkdir replaces inherited ACEs. That removes the
    # workspace/temp capability ACE required by a WRITE_RESTRICTED child.
    for part in (prefix, suffix):
        if not isinstance(part, str) or any(c in part for c in ('/', '\\', ':', '\x00')):
            raise ValueError('Temporary name components must be simple strings.')
    base = Path(dir if dir is not None else tempfile.gettempdir()).absolute()
    for ancestor in (base, *base.parents):
        info = ancestor.lstat()
        if ancestor.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Temporary parents must be ordinary directories.')
    for _ in range(100):
        candidate = base / (prefix + uuid.uuid4().hex + suffix)
        try:
            os.mkdir(candidate, 0o777)
        except FileExistsError:
            continue
        return str(candidate)
    raise FileExistsError('Unable to allocate an exclusive temporary directory.')


def _cleanup(name, identity):
    path = Path(name)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if path.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise ValueError('Refuse cleanup of a replaced temporary link.')
    # Only the exact exclusively created directory is owned. Do not chmod,
    # rewrite ACLs, follow junctions or clean siblings to force success.
    for ancestor in path.parents:
        parent_info = ancestor.lstat()
        if ancestor.is_symlink() or getattr(parent_info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Refuse cleanup through a replaced parent.')
    if (info.st_dev, info.st_ino) != identity:
        raise ValueError('Refuse cleanup of a replaced temporary directory.')
    shutil.rmtree(path)


@contextmanager
def TemporaryDirectory(suffix='', prefix='tmp', dir=None):
    if os.name != 'nt':
        with tempfile.TemporaryDirectory(suffix=suffix, prefix=prefix, dir=dir) as name:
            yield name
        return
    name = mkdtemp(suffix=suffix, prefix=prefix, dir=dir)
    info = Path(name).lstat()
    identity = (info.st_dev, info.st_ino)
    try:
        yield name
    finally:
        _cleanup(name, identity)
