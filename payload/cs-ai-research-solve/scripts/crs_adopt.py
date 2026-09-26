"""Bounded adoption of an external research directory or ZIP without executing its code.

The inventory freezes exact source bytes; an explicit mapping assigns research
records and a retain/omit disposition to every file. Adoption never grants review.
"""
from __future__ import annotations

import contextlib
import hashlib
import os
import stat
import struct
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from crs_model import CRSError, canonical, digest, parse_json, validate_record
from crs_store import ordinary, file_digest

CACHE_DIRS = {'__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache', '.git', '.venv'}
INVENTORY_KEYS = {'schema', 'source_type', 'source_name', 'files', 'skipped', 'total_bytes', 'source_limits', 'source_usage'}


def safe_name(name):
    if not isinstance(name, str) or not name or '\\' in name or ':' in name or '\x00' in name:
        raise CRSError('source_path_invalid', 'Use a portable, relative member path.')
    path = PurePosixPath(name)
    if path.is_absolute() or any(x in {'..', '.'} for x in name.split('/')) or '' in name.split('/'):
        raise CRSError('source_path_invalid', 'Source member path escapes or aliases its root.')
    if any(x.rstrip(' .') != x for x in path.parts):
        raise CRSError('source_path_invalid', 'Source member name is ambiguous on Windows.')
    return path


DEFAULT_METADATA_BYTES = 67108864
LIMIT_KEYS = {'max_entries', 'max_expanded_bytes', 'max_metadata_bytes'}
USAGE_KEYS = {'visited_entries', 'expanded_bytes', 'metadata_bytes'}


def _limits(max_files, max_bytes, max_metadata_bytes):
    if (type(max_files) is not int or not 1 <= max_files <= 1000000
            or type(max_bytes) is not int or max_bytes < 1
            or type(max_metadata_bytes) is not int or max_metadata_bytes < 1):
        raise CRSError('source_limit_invalid', 'Declare positive entry, expanded-byte and metadata-byte limits.')
    return {'max_entries': max_files, 'max_expanded_bytes': max_bytes, 'max_metadata_bytes': max_metadata_bytes}


class _Budget:
    def __init__(self, limits):
        self.limits = limits
        self.usage = dict(visited_entries=0, expanded_bytes=0, metadata_bytes=0)

    def add(self, entries=0, expanded=0, metadata=0):
        self.usage['visited_entries'] += entries
        self.usage['expanded_bytes'] += expanded
        self.usage['metadata_bytes'] += metadata
        for used, maximum in [('visited_entries', 'max_entries'), ('expanded_bytes', 'max_expanded_bytes'), ('metadata_bytes', 'max_metadata_bytes')]:
            if self.usage[used] > self.limits[maximum]:
                raise CRSError('source_inventory_limit', 'Source exceeds the declared inventory limits; narrow the scope or explicitly raise its limits.',
                               {'usage': self.usage.copy(), 'limits': self.limits.copy(), 'exceeded': maximum})


def _zip_invalid(message):
    raise CRSError('source_zip_invalid', message)


def _read_at(stream, offset, length, file_size):
    if offset < 0 or length < 0 or offset + length > file_size:
        _zip_invalid('ZIP metadata points outside its physical file.')
    stream.seek(offset)
    value = stream.read(length)
    if len(value) != length:
        _zip_invalid('ZIP metadata ended before its declared boundary.')
    return value


def _zip_preflight(stream, budget):
    """Bound and stream-check the central directory before ZipFile allocates it."""
    file_size = os.fstat(stream.fileno()).st_size
    if file_size < 22:
        _zip_invalid('ZIP end record is missing.')
    tail_start = max(0, file_size - 65557)
    tail = _read_at(stream, tail_start, file_size - tail_start, file_size)
    position = tail.rfind(b'PK\x05\x06')
    while position >= 0:
        if position + 22 <= len(tail):
            eocd = struct.unpack_from('<4s4H2LH', tail, position)
            if position + 22 + eocd[-1] == len(tail):
                break
        position = tail.rfind(b'PK\x05\x06', 0, position)
    if position < 0:
        _zip_invalid('ZIP end record or comment boundary is invalid.')
    _, disk, cd_disk, disk_count, count, cd_size, cd_offset, comment = eocd
    if disk != 0 or cd_disk != 0 or disk_count != count:
        _zip_invalid('Multi-volume ZIP sources are unsupported.')
    eocd_offset = tail_start + position
    metadata_end = eocd_offset
    metadata_overhead = 22 + comment
    locator = _read_at(stream, eocd_offset - 20, 20, file_size) if eocd_offset >= 20 else b''
    zip64 = locator.startswith(b'PK\x06\x07')
    if zip64:
        _, locator_disk, zip64_offset, volumes = struct.unpack('<4sLQL', locator)
        if locator_disk != 0 or volumes != 1:
            _zip_invalid('ZIP64 locator must bind one volume.')
        header = _read_at(stream, zip64_offset, 56, file_size)
        signature, record_size, made, needed, disk64, cd_disk64, disk_count64, count64, size64, offset64 = struct.unpack('<4sQ2H2L4Q', header)
        if (signature != b'PK\x06\x06' or record_size < 44 or needed > 63
                or disk64 != 0 or cd_disk64 != 0 or disk_count64 != count64
                or zip64_offset + 12 + record_size != eocd_offset - 20):
            _zip_invalid('ZIP64 end record has inconsistent bounds or volume counts.')
        if ((count != 65535 and count != count64) or (cd_size != 4294967295 and cd_size != size64)
                or (cd_offset != 4294967295 and cd_offset != offset64)):
            _zip_invalid('ZIP and ZIP64 end records disagree.')
        count, cd_size, cd_offset = count64, size64, offset64
        metadata_end = zip64_offset
        metadata_overhead += 20 + 12 + record_size
    elif count == 65535 or cd_size == 4294967295 or cd_offset == 4294967295:
        _zip_invalid('ZIP64 end metadata is required for sentinel values.')
    if cd_offset + cd_size != metadata_end or cd_offset < 0:
        _zip_invalid('Central-directory bounds do not meet the end records.')
    if count > budget.limits['max_entries']:
        raise CRSError('source_inventory_limit', 'ZIP declares more entries than the explicit limit.')
    budget.add(metadata=cd_size + metadata_overhead)
    cursor = cd_offset
    end = cd_offset + cd_size
    actual_count = 0
    while cursor < end:
        fixed = _read_at(stream, cursor, 46, file_size)
        fields = struct.unpack('<4s6H3L5H2L', fixed)
        if fields[0] != b'PK\x01\x02':
            _zip_invalid('Central-directory member header is invalid.')
        name_size, extra_size, comment_size = fields[10:13]
        record_end = cursor + 46 + name_size + extra_size + comment_size
        if not name_size or record_end > end:
            _zip_invalid('Central-directory variable fields leave its bounded region.')
        expanded, compressed, local_offset, start_disk = fields[9], fields[8], fields[16], fields[13]
        needs = [expanded == 4294967295, compressed == 4294967295, local_offset == 4294967295, start_disk == 65535]
        if any(needs):
            extra = _read_at(stream, cursor + 46 + name_size, extra_size, file_size)
            offset = 0
            values = None
            while offset + 4 <= len(extra):
                tag, size = struct.unpack_from('<HH', extra, offset)
                offset += 4
                if offset + size > len(extra):
                    _zip_invalid('ZIP extra field is truncated.')
                if tag == 1:
                    if values is not None:
                        _zip_invalid('ZIP64 member repeats its size field.')
                    values = extra[offset:offset + size]
                offset += size
            if offset != len(extra) or values is None:
                _zip_invalid('ZIP64 member sizes are missing or malformed.')
            parsed = [expanded, compressed, local_offset, start_disk]
            offset = 0
            for index, required in enumerate(needs):
                if required:
                    width = 4 if index == 3 else 8
                    if offset + width > len(values):
                        _zip_invalid('ZIP64 member sizes are incomplete.')
                    parsed[index] = int.from_bytes(values[offset:offset + width], 'little')
                    offset += width
            expanded, compressed, local_offset, start_disk = parsed
        if start_disk != 0 or local_offset >= cd_offset:
            _zip_invalid('Member data points outside this ZIP volume.')
        budget.add(entries=1, expanded=expanded)
        actual_count += 1
        cursor = record_end
    if cursor != end or actual_count != count:
        _zip_invalid('Actual central-directory count disagrees with the end record.')
    if os.fstat(stream.fileno()).st_size != file_size:
        raise CRSError('source_changed', 'ZIP size changed during metadata preflight.')
    return actual_count


@contextlib.contextmanager
def source_entries(source, max_files=10000, max_bytes=1073741824, max_metadata_bytes=DEFAULT_METADATA_BYTES, *, _usage=None):
    source = ordinary(Path(source).absolute())
    budget = _Budget(_limits(max_files, max_bytes, max_metadata_bytes))
    rows, skipped, seen = [], [], set()

    def check_name(name):
        safe_name(name)
        if name.casefold() in seen:
            raise CRSError('source_name_collision', 'Source contains duplicate or case-colliding member paths.')
        seen.add(name.casefold())

    def ready():
        if _usage is not None:
            _usage.update(budget.usage)

    if source.is_dir():
        pending = [source]
        while pending:
            root = ordinary(pending.pop())
            with os.scandir(root) as entries:
                for entry in entries:
                    path = root / entry.name
                    relative = path.relative_to(source).as_posix()
                    budget.add(entries=1, metadata=len(relative.encode('utf-8')))
                    path = ordinary(path)
                    check_name(relative)
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name in CACHE_DIRS:
                            skipped.append({'path': relative + '/', 'reason': 'generated cache or repository control directory'})
                        else:
                            pending.append(path)
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        raise CRSError('source_nonregular_file', 'Source contains a nonregular file.')
                    size = entry.stat(follow_symlinks=False).st_size
                    budget.add(expanded=size)
                    if path.suffix in {'.pyc', '.pyo'}:
                        skipped.append({'path': relative, 'reason': 'generated Python bytecode'})
                    else:
                        rows.append((relative, size, path))
        ready()
        yield 'directory', sorted(rows), sorted(skipped, key=lambda x: x['path']), lambda token: token.open('rb')
    elif source.is_file():
        try:
            with source.open('rb') as stream:
                count = _zip_preflight(stream, budget)
                stream.seek(0)
                with zipfile.ZipFile(stream) as archive:
                    if len(archive.filelist) != count:
                        _zip_invalid('ZIP member namespace changed after metadata preflight.')
                    for item in archive.filelist:
                        name = item.filename.rstrip('/') if item.is_dir() else item.filename
                        check_name(name)
                        if stat.S_ISLNK(item.external_attr >> 16) or item.flag_bits & 1:
                            raise CRSError('source_unsafe_member', 'Encrypted or linked source members are unsupported.')
                        if any(part in CACHE_DIRS for part in PurePosixPath(name).parts) or Path(name).suffix in {'.pyc', '.pyo'}:
                            skipped.append({'path': item.filename, 'reason': 'generated cache or repository control content'})
                        elif not item.is_dir():
                            rows.append((item.filename, item.file_size, item))
                    ready()
                    yield 'zip', sorted(rows, key=lambda x: x[0]), sorted(skipped, key=lambda x: x['path']), lambda token: archive.open(token, 'r')
        except zipfile.BadZipFile as error:
            raise CRSError('source_zip_invalid', str(error)) from error
    else:
        raise CRSError('source_invalid', 'Choose an existing ordinary directory or ZIP archive.')


def scan_source(source, max_files=10000, max_bytes=1073741824, max_metadata_bytes=DEFAULT_METADATA_BYTES):
    files, usage = [], {}
    limits = _limits(max_files, max_bytes, max_metadata_bytes)
    with source_entries(source, max_files, max_bytes, max_metadata_bytes, _usage=usage) as (kind, rows, skipped, open_member):
        for name, size, token in rows:
            h = hashlib.sha256()
            actual = 0
            with open_member(token) as stream:
                while chunk := stream.read(1048576):
                    h.update(chunk)
                    actual += len(chunk)
                    if actual > size:
                        raise CRSError('source_changed', 'Source size changed while inventorying it.')
            if actual != size:
                raise CRSError('source_changed', 'Source size changed while inventorying it.')
            files.append({'path': name, 'sha256': h.hexdigest(), 'bytes': size})
        return {'schema': 'crs-source-inventory/v1', 'source_type': kind, 'source_name': Path(source).name,
                'files': files, 'skipped': skipped, 'total_bytes': sum(x['bytes'] for x in files),
                'source_limits': limits, 'source_usage': usage}


def adoption_template(inventory):
    validate_inventory(inventory)
    return {'schema': 'crs-adoption/v1', 'inventory_sha256': digest(canonical(inventory)),
            'origin': '', 'records': [],
            'dispositions': [{'path': row['path'], 'action': 'retain', 'reason': ''} for row in inventory['files']]}


def validate_inventory(inventory):
    if (not isinstance(inventory, dict) or set(inventory) != INVENTORY_KEYS
            or inventory['schema'] != 'crs-source-inventory/v1'):
        raise CRSError('source_inventory_invalid', 'Unsupported source inventory format.')
    limits, usage = inventory['source_limits'], inventory['source_usage']
    if not isinstance(limits, dict) or set(limits) != LIMIT_KEYS or not isinstance(usage, dict) or set(usage) != USAGE_KEYS:
        raise CRSError('source_inventory_invalid', 'Inventory resource budgets and measured usage require their exact fields.')
    _limits(limits['max_entries'], limits['max_expanded_bytes'], limits['max_metadata_bytes'])
    for used, maximum in [('visited_entries', 'max_entries'), ('expanded_bytes', 'max_expanded_bytes'), ('metadata_bytes', 'max_metadata_bytes')]:
        if type(usage[used]) is not int or not 0 <= usage[used] <= limits[maximum]:
            raise CRSError('source_inventory_invalid', 'Inventory resource usage exceeds its frozen limits.')
    if not isinstance(inventory['files'], list) or not isinstance(inventory['skipped'], list):
        raise CRSError('source_inventory_invalid', 'Inventory files and skipped entries must be lists.')
    seen = set()
    for row in inventory['files']:
        if not isinstance(row, dict) or set(row) != {'path', 'sha256', 'bytes'}:
            raise CRSError('source_inventory_invalid', 'Inventory entries require path, hash and byte count.')
        safe_name(row['path'])
        if row['path'].casefold() in seen or type(row['bytes']) is not int or row['bytes'] < 0:
            raise CRSError('source_inventory_invalid', 'Duplicate inventory path or invalid byte count.')
        seen.add(row['path'].casefold())
        if not isinstance(row['sha256'], str) or len(row['sha256']) != 64 or any(c not in '0123456789abcdef' for c in row['sha256']):
            raise CRSError('source_inventory_invalid', 'Invalid content identity.')
    if inventory['total_bytes'] != sum(x['bytes'] for x in inventory['files']):
        raise CRSError('source_inventory_invalid', 'Inventory total does not match its members.')


def adopt_source(store, source, inventory, mapping, operation_id, expected_head=None):
    validate_inventory(inventory)
    fields = {'schema', 'inventory_sha256', 'origin', 'records', 'dispositions'}
    if not isinstance(mapping, dict) or set(mapping) != fields or mapping['schema'] != 'crs-adoption/v1':
        raise CRSError('adoption_mapping_invalid', 'Use an explicit crs-adoption/v1 asset and research mapping.')
    if mapping['inventory_sha256'] != digest(canonical(inventory)):
        raise CRSError('adoption_inventory_mismatch', 'The adoption mapping belongs to different source inventory bytes.')
    if not isinstance(mapping['records'], list) or not mapping['records']:
        raise CRSError('adoption_research_missing', 'Map the actual research attempts and feedback; a file-only move is incomplete.')
    for record in mapping['records']:
        validate_record(record)
    if not isinstance(mapping['dispositions'], list):
        raise CRSError('adoption_disposition_invalid', 'Explicit dispositions are required for every source file.')
    decisions = {}
    for row in mapping['dispositions']:
        if not isinstance(row, dict) or set(row) != {'path', 'action', 'reason'} or row['action'] not in {'retain', 'omit'} or not isinstance(row['reason'], str) or not row['reason'].strip():
            raise CRSError('adoption_disposition_invalid', 'Every retention or omission needs an explicit reason.')
        if row['path'] in decisions:
            raise CRSError('adoption_disposition_invalid', 'Each source file must receive exactly one disposition.')
        decisions[row['path']] = row
    inventory_rows = {row['path']: row for row in inventory['files']}
    if set(decisions) != set(inventory_rows):
        raise CRSError('adoption_disposition_incomplete', 'The mapping must account for every inventoried file exactly once.')
    required = {e['sha256'] for r in mapping['records'] for e in r['evidence']}
    retained = {row['sha256'] for name, row in inventory_rows.items() if decisions[name]['action'] == 'retain'}
    if not retained.issubset(required):
        raise CRSError('adoption_unmapped_asset', 'Every retained asset must have an explicit role in at least one research record.')
    local_only = set(inventory_rows[row['path']]['sha256'] for row in decisions.values() if row['action'] == 'omit') - retained
    if local_only & required:
        raise CRSError('adoption_required_asset_omitted', 'A mapped evidence asset is omitted without another retained copy.')
    # The supplied frozen inventory defines this operation's resource ceiling.
    with store._work() as work:
        assets = {}
        limits, usage = inventory['source_limits'], {}
        with source_entries(source, limits['max_entries'], limits['max_expanded_bytes'], limits['max_metadata_bytes'], _usage=usage) as (kind, rows, skipped, open_member):
            if usage != inventory['source_usage']:
                raise CRSError('adoption_source_changed', 'Source resource accounting changed after inventory; inspect and explicitly re-inventory it.')
            if kind != inventory['source_type'] or {name for name, _, _ in rows} != set(inventory_rows) or skipped != inventory['skipped']:
                raise CRSError('adoption_source_changed', 'Source namespace changed after inventory; inspect the new contribution.')
            for name, size, token in rows:
                expected = inventory_rows[name]
                if size != expected['bytes']:
                    raise CRSError('adoption_source_changed', 'An inventoried source changed size.')
                h = hashlib.sha256()
                actual = 0
                keep = decisions[name]['action'] == 'retain'
                staged = work / ('asset-' + str(len(assets)))
                with open_member(token) as src, (staged.open('wb') if keep else contextlib.nullcontext()) as dst:
                    while chunk := src.read(1048576):
                        actual += len(chunk)
                        if actual > size:
                            raise CRSError('adoption_source_changed', 'An inventoried source grew during adoption.')
                        h.update(chunk)
                        if keep:
                            dst.write(chunk)
                if actual != size or h.hexdigest() != expected['sha256']:
                    raise CRSError('adoption_source_changed', 'An inventoried source changed bytes; the frozen mapping cannot cover it.')
                if keep:
                    if expected['sha256'] in assets:
                        staged.unlink()
                    else:
                        assets[expected['sha256']] = staged
        result = store.ingest(mapping['records'], assets, mapping['origin'], operation_id, expected_head)
        receipt = {'schema': 'crs-adoption-receipt/v1', 'source_inventory': inventory, 'mapping': mapping,
                   'snapshot': result['snapshot'], 'retained_unique_assets': len(assets),
                   'omitted_files': sum(x['action'] == 'omit' for x in decisions.values()),
                   'research_review_automatically_granted': False, 'source_code_executed': False}
        receipt_sha = store.put_json(receipt)
        store.resolve(receipt_sha)
        for sha in assets:
            store.resolve(sha)
    return {**result, 'adoption_receipt': receipt_sha, 'retained_unique_assets': len(assets),
            'temporary_directory_removed': not work.exists(), 'research_review_automatically_granted': False}
