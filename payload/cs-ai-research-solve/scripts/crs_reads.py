"""Bounded JSON reuse during one synchronous operation, with live file leases.

No persistent cache and no research authority. Unsupported platforms,
unavailable Windows leases and capacity misses retain native byte verification.
"""
from __future__ import annotations
import contextlib
import copy
import os
import sys
import threading
from crs_model import CRSError, parse_json, digest
from crs_metrics import count

MAX_OBJECTS = 4096
MAX_DECODED_BYTES = 64 * 1024 * 1024
MAX_SOURCE_BYTES = 2 * 1024 * 1024


def decoded_size(value):
    """Conservative sum of retained JSON containers/scalars; not process RSS."""
    pending, seen, total = [value], set(), 0
    while pending:
        item = pending.pop()
        if id(item) in seen:
            continue
        seen.add(id(item)); total += sys.getsizeof(item)
        if total > MAX_DECODED_BYTES:
            return total
        if isinstance(item, dict):
            pending.extend(item.keys()); pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return total


class _JSONReads:
    def __init__(self, store):
        self.store = store
        self.owner = threading.get_ident()
        self.active = True
        self.entries = {}
        self.decoded_bytes = 0

    def current(self):
        if not self.active or self.owner != threading.get_ident():
            raise CRSError('read_scope_invalid', 'JSON reads belong to their active synchronous operation.')

    def same_file(self, sha, stream):
        # locate reopens current topology/location metadata, not content trust.
        path = self.store.locate(sha)
        actual, held = path.stat(), os.fstat(stream.fileno())
        if (actual.st_dev, actual.st_ino) != (held.st_dev, held.st_ino):
            raise CRSError('asset_changed', 'The requested object path changed during its read scope.')

    def read(self, sha):
        self.current()
        cached = self.entries.get(sha)
        if cached is not None:
            stream, value = cached
            self.same_file(sha, stream)
            count('json_read_cache_hits')
            return copy.deepcopy(value)
        if len(self.entries) >= MAX_OBJECTS or self.decoded_bytes >= MAX_DECODED_BYTES:
            count('json_read_cache_capacity_fallbacks')
            return parse_json(self.store.get_blob(sha))
        path = self.store.locate(sha)
        if path.stat().st_size > MAX_SOURCE_BYTES:
            return parse_json(self.store.get_blob(sha))
        from crs_locations import _read_lease
        try:
            stream = _read_lease(path)
        except OSError:
            count('json_read_cache_lease_fallbacks')
            return parse_json(self.store.get_blob(sha))
        retained = False
        try:
            # Hash bytes read from the held handle itself, never another open
            # through a path that could now identify a different file.
            raw = stream.read(MAX_SOURCE_BYTES + 1)
            count('bytes_read', len(raw))
            if len(raw) > MAX_SOURCE_BYTES:
                return parse_json(self.store.get_blob(sha))
            count('hash_calls')
            count('bytes_hashed', len(raw))
            if digest(raw) != sha:
                raise CRSError('asset_corrupt', 'Content bytes do not match their identity.', {'sha256': sha})
            self.same_file(sha, stream)
            value = parse_json(raw)
            size = decoded_size(value)
            if len(raw) <= MAX_SOURCE_BYTES and self.decoded_bytes + size <= MAX_DECODED_BYTES:
                self.entries[sha] = (stream, value)
                self.decoded_bytes += size
                retained = True
                count('json_read_cache_stores')
            return copy.deepcopy(value) if retained else value
        finally:
            if not retained:
                stream.close()

    def close(self):
        self.active = False
        try:
            for stream, _ in self.entries.values():
                stream.close()
        finally:
            self.entries.clear()
            self.decoded_bytes = 0


@contextlib.contextmanager
def read_scope(store):
    current = store._json_reads
    if current is not None:
        current.current()
        yield
        return
    if os.name != 'nt':
        yield
        return
    scope = _JSONReads(store)
    store._json_reads = scope
    try:
        yield
    finally:
        store._json_reads = None
        scope.close()
