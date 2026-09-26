"""Content-addressed research storage; process-crash recovery, not power-loss proof.

Only cooperating writers are serialized. Research reviews remain explicit
evidence-backed actions by the caller; byte checks cannot prove research claims.
"""
from __future__ import annotations

import crs_temp

import contextlib
from functools import wraps
from crs_metrics import measured, count
from crs_unique import project_scope, external_target, content_guard, check_write, record_write, check_candidate, writer_mutex
import copy
import hashlib
import os
import re
import shutil
import tempfile
import time
import uuid
from pathlib import Path

from crs_model import CRSError, canonical, digest, parse_json, validate_record, validate_review, objective_identity, objective_compatible, objective_complete, objective_conflicting_revisions, OBJECTIVE_FIELDS

SHA = re.compile(r'^[0-9a-f]{64}$')
OP = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$')
CHUNK = 1024 * 1024


def fail(code, message, details=None):
    raise CRSError(code, message, details)


@measured('hash')
def file_digest(path):
    h = hashlib.sha256()
    size = 0
    with Path(path).open('rb') as f:
        while data := f.read(CHUNK):
            h.update(data)
            count('bytes_read', len(data))
            count('bytes_hashed', len(data))
            size += len(data)
    return h.hexdigest(), size


@measured('path_check')
def ordinary(path):
    path = Path(path)
    for item in [path, *path.parents]:
        if item.exists() and not crs_temp.system_alias(item) and (item.is_symlink() or getattr(item.lstat(), 'st_file_attributes', 0) & 0x400):
            fail('reparse_path', 'Use ordinary directories and files, not links.', {'path': str(item)})
    return path


def incremental_operation(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self.operation_io():
            return method(self, *args, **kwargs)
    return call


class Store:
    def __init__(self, root):
        self.root = ordinary(Path(root).absolute()).resolve()
        self._batch_work = None
        self._content_index = None
        self._content_scope = None
        self._location_scope = None
        self._last_location_counts = None
        self._json_reads = None

    def operation_io(self):
        """Scope synchronous archive work; native full audit stays outside."""
        from crs_locations import operation_scope
        return operation_scope(self)

    def read_io(self):
        from crs_reads import read_scope
        return read_scope(self)

    def _path(self, relative):
        path = ordinary(self.root / relative)
        if not path.resolve().is_relative_to(self.root):
            fail('path_escape', 'The requested path leaves the research project.')
        return path

    @contextlib.contextmanager
    def _work(self):
        # Batch files are private siblings; publication still uses atomic rename.
        if self._batch_work is not None:
            yield self._batch_work
            return
        count('temporary_directories')
        with crs_temp.TemporaryDirectory(prefix='.crs-work-', dir=project_scope(self).parent) as work:
            yield Path(work)

    @contextlib.contextmanager
    def batch_work(self):
        if self._batch_work is not None:
            yield
            return
        count('temporary_directories')
        with crs_temp.TemporaryDirectory(prefix='.crs-work-', dir=project_scope(self).parent) as work:
            self._batch_work = Path(work)
            try:
                yield
            finally:
                self._batch_work = None

    @measured('fsync')
    def _sync(self, stream):
        stream.flush()
        os.fsync(stream.fileno())

    @contextlib.contextmanager
    def _lock(self):
        if os.name == 'nt':
            with writer_mutex(self.root):
                yield
            return
        lock = self._path('.crs-lock')
        try:
            with lock.open('xb') as f:
                f.write(uuid.uuid4().hex.encode('ascii'))
        except FileExistsError:
            pass
        f = lock.open('r+b')
        try:
            f.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                fail('writer_busy', 'Another writer holds this project. Retry after it finishes.')
            try:
                yield
            finally:
                if os.name == 'nt':
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        finally:
            f.close()

    def _atomic(self, path, data):
        if self._location_scope is not None and Path(path) == self.root / 'locations.json':
            fail('location_batch_readonly', 'Location metadata is read-only during an archive operation.')
        if self._content_index is None:
            with content_guard(self):
                return self._atomic(path, data)
        path = ordinary(path)
        if not path.resolve().is_relative_to(self.root):
            fail('path_escape', 'Atomic publication target leaves the project.')
        check_write(self, path, digest(data))
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._work() as work:
            staged = work / ('publish-' + uuid.uuid4().hex)
            with staged.open('xb') as f:
                f.write(data)
                count('bytes_written', len(data))
                self._sync(f)
            additions = check_candidate(self, path, staged)
            os.replace(staged, path)
        record_write(self, path, digest(data), additions)

    def _object(self, sha):
        if not isinstance(sha, str) or not SHA.fullmatch(sha):
            fail('invalid_digest', 'Expected a lowercase SHA-256 content identity.')
        return self._path(Path('objects') / sha[:2] / sha[2:])

    def _locations(self, with_pending=False):
        if self._location_scope is not None:
            return self._location_scope.locations(with_pending)
        return self._locations_full(with_pending)

    def _locations_full(self, with_pending=False):
        path = self._path('locations.json')
        if not path.exists():
            data = {'schema': 'crs-locations/v1', 'objects': {}}
        else:
            data = parse_json(path.read_bytes())
        if (not isinstance(data, dict) or set(data) - {'schema', 'objects', 'pending_moves'}
                or data.get('schema') != 'crs-locations/v1' or not isinstance(data.get('objects'), dict)
                or not isinstance(data.get('pending_moves', {}), dict)):
            fail('locations_invalid', 'Cold asset location table is invalid.')

        def checked_path(value, sha, allow_hot=False):
            if not isinstance(value, str) or not value or not Path(value).is_absolute():
                fail('locations_invalid', 'Registered locations must be absolute ordinary file paths.')
            candidate = ordinary(Path(value)).absolute()
            hot = self._object(sha)
            if allow_hot and candidate == hot:
                return candidate
            if (candidate.name != sha or candidate.resolve().is_relative_to(self.root)
                    or self.root.is_relative_to(candidate.parent.resolve())):
                fail('locations_invalid', 'Cold locations must name their digest outside the project and its ancestors.')
            return candidate

        for sha, location in data['objects'].items():
            self._object(sha)
            checked_path(location, sha)
        for sha, move in data.get('pending_moves', {}).items():
            hot = self._object(sha)
            if not isinstance(move, dict) or set(move) != {'source', 'destination'}:
                fail('locations_invalid', 'A pending cold move must bind its source and destination.')
            source = checked_path(move['source'], sha, allow_hot=True)
            destination = checked_path(move['destination'], sha)
            current = data['objects'].get(sha)
            if (source == destination or (current is None and source != hot)
                    or (current is not None and current not in {str(source), str(destination)})):
                fail('locations_invalid', 'A pending cold move does not match the registered object location.')
        return data if with_pending else data['objects']

    def locate(self, sha, *, locations=None):
        """Locate ordinary content without claiming its byte identity is verified.

        Used by delivery metadata planning only. Existing resolve/get_blob
        consumers keep their full integrity checks. A caller-supplied location
        snapshot must come from this Store's validated _locations() operation.
        """
        path = self._object(sha)
        if not path.exists():
            location = (self._locations() if locations is None else locations).get(sha)
            if not location:
                fail('asset_missing', 'Content is not available locally.', {'sha256': sha})
            path = ordinary(Path(location))
        if not path.is_file():
            fail('asset_missing', 'Registered content is not an ordinary file.', {'sha256': sha})
        return path

    @measured('resolve')
    def resolve(self, sha):
        path = self._object(sha)
        if not path.exists():
            location = self._locations().get(sha)
            if not location:
                fail('asset_missing', 'Content is not available locally.', {'sha256': sha})
            path = ordinary(Path(location))
        if not path.is_file():
            fail('asset_missing', 'Registered content is not an ordinary file.', {'sha256': sha})
        actual, _ = file_digest(path)
        if actual != sha:
            fail('asset_corrupt', 'Content bytes do not match their identity.', {'sha256': sha})
        return path

    def get_blob(self, sha):
        data = self.resolve(sha).read_bytes()
        if digest(data) != sha:
            fail('asset_changed', 'Content changed while being read.', {'sha256': sha})
        return data

    def get_json(self, sha):
        if self._json_reads is not None:
            return self._json_reads.read(sha)
        return parse_json(self.get_blob(sha))

    def _put_staged(self, path, sha):
        if self._content_index is None:
            with content_guard(self):
                return self._put_staged(path, sha)
        destination = self._object(sha)
        if destination.exists() or sha in self._locations():
            self.resolve(sha)
            count('objects_reused_after_copy')
            count('avoidable_copy_bytes', path.stat().st_size)
            path.unlink()
            return sha
        additions = check_candidate(self, destination, path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Rename creates no second official copy. Writers use the same project lock.
        # Object content is immutable; a racing identical writer may reuse it.
        if os.name == 'nt':
            try:
                os.rename(path, destination)
            except FileExistsError:
                self.resolve(sha)
        else:
            try:
                os.link(path, destination)
            except FileExistsError:
                self.resolve(sha)
            else:
                path.unlink()
        record_write(self, destination, sha, additions)
        count('objects_published')
        return sha

    def put_blob(self, data):
        if not isinstance(data, bytes):
            fail('bytes_required', 'put_blob expects exact bytes.')
        sha = digest(data)
        with self._work() as work:
            path = work / ('object-' + uuid.uuid4().hex)
            with path.open('xb') as f:
                f.write(data)
                count('bytes_written', len(data))
                self._sync(f)
            return self._put_staged(path, sha)

    @measured('copy')
    def put_file(self, source, expected_sha=None):
        source = ordinary(Path(source).absolute())
        if not source.is_file():
            fail('source_missing', 'Source is not an ordinary file.')
        if expected_sha is not None:
            self._object(expected_sha)
            try:
                self.resolve(expected_sha)
            except CRSError as error:
                if error.code != 'asset_missing':
                    raise
            else:
                actual, _ = file_digest(source)
                if actual != expected_sha:
                    fail('input_changed', 'Input changed while being archived.')
                count('objects_reused_before_copy')
                return expected_sha
        with self._work() as work:
            path = work / ('object-' + uuid.uuid4().hex)
            h = hashlib.sha256()
            count('hash_calls')
            with source.open('rb') as src, path.open('xb') as dst:
                while data := src.read(CHUNK):
                    h.update(data)
                    count('bytes_read', len(data))
                    count('bytes_hashed', len(data))
                    dst.write(data)
                    count('bytes_written', len(data))
                self._sync(dst)
            actual = h.hexdigest()
            if expected_sha is not None and actual != expected_sha:
                fail('input_changed', 'Input changed while being archived.')
            return self._put_staged(path, actual)

    def put_json(self, obj):
        return self.put_blob(canonical(obj))

    def head(self):
        try:
            data = parse_json(self._path('HEAD').read_bytes())
        except FileNotFoundError:
            fail('project_uninitialized', 'Initialize a new empty project before using it.')
        if not isinstance(data, dict) or set(data) != {'schema', 'snapshot'} or data['schema'] != 'crs-head/v1':
            fail('head_invalid', 'Project HEAD is malformed; inspect recovery records.')
        self._object(data['snapshot'])
        return data['snapshot']

    def snapshot(self, head=None):
        value = self.get_json(head or self.head())
        keys = {'schema', 'project_id', 'title', 'objective', 'record_hashes', 'selected', 'reviews', 'foreign_reviews', 'origins', 'parent', 'operation_id', 'request_sha256'}
        if not isinstance(value, dict) or not keys.issubset(value) or set(value) - keys - {'excluded_records', 'objective_binding', 'objective_identity'} or value.get('schema') != 'crs-snapshot/v1':
            fail('snapshot_invalid', 'The snapshot does not use the supported research data contract.')
        value.setdefault('excluded_records', [])
        excluded = self._check_excluded(value['excluded_records'])
        for key in ['record_hashes', 'reviews', 'foreign_reviews']:
            if not isinstance(value[key], list) or len(value[key]) != len(set(value[key])):
                fail('snapshot_invalid', 'Snapshot references must be unique lists.')
            for sha in value[key]:
                self._object(sha)
        if set(excluded) & set(value['record_hashes']):
            fail('snapshot_invalid', 'Available records cannot also be excluded references.')
        if not isinstance(value['selected'], dict) or not set(value['selected'].values()).issubset(set(value['record_hashes']) | set(excluded)):
            fail('snapshot_invalid', 'Selected revisions must belong to the snapshot or its explicitly excluded references.')
        stored_identity = value.get('objective_identity')
        self._refresh_objective(value)
        if stored_identity is not None:
            if not isinstance(stored_identity, dict):
                fail('objective_identity_mismatch', 'Snapshot question identity must be a structured value.')
            if stored_identity != value['objective_identity']:
                fail('objective_identity_mismatch', 'Snapshot question identity does not match its immutable objective records.')
        return value

    def _refresh_objective(self, snapshot, records=None):
        anchor_sha = snapshot['objective']
        binding_sha = snapshot.setdefault('objective_binding', None)
        def locate(sha):
            if sha is None or sha not in snapshot['record_hashes']:
                return None
            return records.get(sha) if records is not None else self.get_json(sha)
        if anchor_sha is not None:
            self._object(anchor_sha)
        if binding_sha is not None:
            self._object(binding_sha)
        anchor, binding = locate(anchor_sha), locate(binding_sha)
        if anchor is None:
            if binding_sha is not None:
                fail('objective_binding_invalid', 'A described objective must have its founding record available.')
            identity = {'completeness': 'unresolved' if anchor_sha else 'unset',
                        'identity_sha256': None, 'known_fields': [],
                        'missing_fields': sorted(OBJECTIVE_FIELDS)}
        else:
            identity = objective_identity(anchor)
            if binding_sha:
                if (binding is None or not objective_complete(binding)
                        or not objective_compatible(anchor, binding)):
                    fail('objective_binding_invalid', 'The explicit description must preserve the founding question.')
                identity = objective_identity(binding)
        known = (records if records is not None else
                 {sha: self.get_json(sha) for sha in snapshot['record_hashes']}) if anchor else {}
        conflicts = objective_conflicting_revisions(known, anchor_sha, binding_sha)
        snapshot['objective_identity'] = {**identity, 'anchor': anchor_sha, 'bound_revision': binding_sha,
                                          'conflicting_revisions': conflicts}
        return snapshot['objective_identity']

    def _check_primary_revision(self, snapshot, records, record, completing=False):
        anchor = records.get(snapshot['objective'])
        if anchor is None or record['id'] != anchor['id']:
            return True
        binding = records.get(snapshot.get('objective_binding'))
        # Incomplete revisions remain readable after an explicit completed description is added.
        if not objective_compatible(anchor, record):
            fail('objective_identity_changed', 'This changes the primary question. Create a linked new question or a new project; do not revise the primary identity.')
        if binding and objective_complete(record) and not objective_compatible(anchor, record, binding):
            fail('objective_identity_changed', 'The six fixed objective constituents cannot change inside this project.')
        if (binding or objective_complete(anchor)) and not objective_complete(record):
            return False
        if not objective_complete(anchor) and not binding and objective_complete(record):
            return completing
        return True

    def _guard_objective_transition(self, before, candidate):
        """Protect new commits, while leaving old immutable history readable."""
        anchor_sha = before['objective']
        if anchor_sha is not None and candidate['objective'] != anchor_sha:
            fail('objective_anchor_changed', 'An established primary anchor cannot be replaced or removed; use a linked question or a new project.')
        if anchor_sha in before['record_hashes'] and anchor_sha not in candidate['record_hashes']:
            fail('objective_anchor_changed', 'The available founding question must remain part of the project.')
        old_binding = before.get('objective_binding')
        new_binding = candidate.get('objective_binding')
        if old_binding:
            if not new_binding or new_binding not in candidate['record_hashes']:
                fail('objective_identity_changed', 'A bound six-part objective description cannot be removed.')
            fixed, proposed = self.get_json(old_binding), self.get_json(new_binding)
            if not objective_compatible(self.get_json(anchor_sha), proposed, fixed):
                fail('objective_identity_changed', 'The bound six objective constituents cannot change at commit.')
        current_anchor = candidate['objective']
        if current_anchor in candidate['record_hashes']:
            known = {sha: self.get_json(sha) for sha in candidate['record_hashes']}
            anchor = known[current_anchor]
            selected = candidate['selected'].get(anchor['id'])
            if selected not in known or not self._check_primary_revision(candidate, known, known[selected]):
                fail('objective_selection_invalid', 'The current primary selection must preserve its fixed question identity; explicitly select an available matching revision.')

    def _check_excluded(self, items):
        if not isinstance(items, list):
            fail('excluded_records_invalid', 'Excluded references must be a list.')
        result = {}
        for item in items:
            from crs_model import validate_excluded_locator
            validate_excluded_locator(item)
            self._object(item['sha256'])
            if item['sha256'] in result:
                fail('excluded_records_invalid', 'Excluded references must be unique.')
            result[item['sha256']] = item
        return result

    def records(self, snapshot=None):
        snapshot = snapshot or self.snapshot()
        records = {}
        for sha in snapshot['record_hashes']:
            obj = self.get_json(sha)
            validate_record(obj)
            records[sha] = obj
        excluded = self._check_excluded(snapshot.get('excluded_records', [])) if snapshot['selected'] else {}
        for identifier, sha in snapshot['selected'].items():
            referenced = records.get(sha) or excluded.get(sha)
            if not referenced or referenced['id'] != identifier:
                fail('selected_identity_mismatch', 'Selected revision belongs to a different research record.')
        return records

    def reviews(self, snapshot=None):
        snapshot = snapshot or self.snapshot()
        result = []
        for sha in snapshot['reviews']:
            obj = self.get_json(sha)
            validate_review(obj)
            self.resolve(obj['report'])
            result.append(obj)
        return result

    def initialize(self, title, objective=None, project_id=None):
        if self.root.exists() and any(self.root.iterdir()):
            fail('project_not_empty', 'Initialization requires an empty directory.')
        if not isinstance(title, str) or not title.strip():
            fail('title_required', 'A project title is required.')
        self.root.mkdir(parents=True, exist_ok=True)
        with self._lock(), content_guard(self):
            project_id = project_id or str(uuid.uuid4())
            value = {'schema': 'crs-snapshot/v1', 'project_id': project_id, 'title': title, 'objective': None, 'record_hashes': [], 'selected': {}, 'reviews': [], 'foreign_reviews': [], 'origins': [], 'excluded_records': [], 'parent': None, 'operation_id': 'initialize', 'request_sha256': digest(canonical({'title': title, 'project_id': project_id}))}
            if objective is not None:
                validate_record(objective)
                if objective['kind'] != 'objective':
                    fail('objective_required', 'The initial research question must use kind=objective.')
                sha = self.put_json(objective)
                value['objective'] = sha
                value['record_hashes'] = [sha]
                value['selected'][objective['id']] = sha
            self._refresh_objective(value)
            sha = self.put_json(value)
            self._atomic(self._path('HEAD'), canonical({'schema': 'crs-head/v1', 'snapshot': sha}))
            readme = ('# ' + title + '\n\n研究项目 ID：' + project_id + '\n\n'
                      'HEAD 指向当前不可变研究快照，objects 按内容散列只保存一份资产。\n'
                      '使用 CRS 的 map、query、record 和 asset 命令阅读；audit 检查完整性和重复内容。\n'
                      '审核状态与研究结论是不同状态。新资料先入库，显式审核后才能成为对外交付内容。\n'
                      '软件发布版本不决定研究证据是否有效。不要手工修改 objects 或 HEAD。\n')
            self._atomic(self._path('README.md'), readme.encode('utf-8'))
            self._atomic(self._path('scope.json'), canonical({'schema':'crs-content-scope/v1','project_root':os.path.relpath(project_scope(self),self.root)}))
        return {'snapshot': sha, 'project_id': project_id, 'committed': True, 'objective_identity': value['objective_identity']}

    def _operation_path(self, operation_id):
        if not isinstance(operation_id, str) or not OP.fullmatch(operation_id):
            fail('operation_id_invalid', 'Use a short alphanumeric operation ID, with dots, underscores or hyphens.')
        return self._path(Path('operations') / (operation_id + '.json'))

    def _finish(self, operation):
        current = self.head()
        if operation['status'] == 'committed':
            self.snapshot(operation['candidate'])
            return {'snapshot': operation['candidate'], 'current_snapshot': current, 'committed': True, 'reused': True}
        if current == operation['candidate']:
            pass
        elif current == operation['expected']:
            candidate = self.snapshot(operation['candidate'])
            self._guard_objective_transition(self.snapshot(operation['expected']), candidate)
            self.records(candidate)
            self.reviews(candidate)
            self._atomic(self._path('HEAD'), canonical({'schema': 'crs-head/v1', 'snapshot': operation['candidate']}))
        else:
            fail('recovery_conflict', 'Another commit advanced the project. This prepared operation cannot overwrite it.', {'operation_id': operation['operation_id'], 'current_snapshot': current})
        if self.head() != operation['candidate']:
            fail('commit_readback_failed', 'Publication may have occurred; recover this operation before retrying.')
        operation = {**operation, 'status': 'committed'}
        self._atomic(self._operation_path(operation['operation_id']), canonical(operation))
        return {'snapshot': operation['candidate'], 'current_snapshot': operation['candidate'], 'committed': True, 'reused': False}

    @incremental_operation
    def transact(self, operation_id, request_sha256, transform, expected_head=None):
        self._object(request_sha256)
        with self._lock(), content_guard(self):
            path = self._operation_path(operation_id)
            if path.exists():
                operation = parse_json(path.read_bytes())
                if operation.get('request_sha256') != request_sha256:
                    fail('operation_reused', 'This operation ID is already bound to different input.')
                return self._finish(operation)
            current = self.head()
            if expected_head is not None and current != expected_head:
                fail('stale_snapshot', 'Project changed after preparation. Reopen it before submitting.', {'current_snapshot': current})
            before = self.snapshot(current)
            candidate = transform(copy.deepcopy(before))
            self._guard_objective_transition(before, candidate)
            self._refresh_objective(candidate)
            if candidate == before:
                candidate_sha = current
            else:
                candidate.update(parent=current, operation_id=operation_id, request_sha256=request_sha256)
                candidate_sha = self.put_json(candidate)
                self.records(candidate)
                self.reviews(candidate)
            operation = {'schema': 'crs-operation/v1', 'operation_id': operation_id, 'request_sha256': request_sha256, 'expected': current, 'candidate': candidate_sha, 'status': 'prepared'}
            self._atomic(path, canonical(operation))
            return self._finish(operation)

    @incremental_operation
    def recover(self, operation_id):
        with self._lock(), content_guard(self):
            path = self._operation_path(operation_id)
            if not path.is_file():
                fail('operation_unknown', 'No prepared operation with this ID exists.')
            return self._finish(parse_json(path.read_bytes()))

    def _verify_evidence(self, record):
        for evidence in record['evidence']:
            try:
                available = self.resolve(evidence['sha256'])
            except CRSError as error:
                if getattr(error, 'code', '') != 'asset_missing' or not (evidence['summary'] and evidence['locator']):
                    raise
            else:
                if available.stat().st_size != evidence['bytes']:
                    fail('asset_size_mismatch', 'Evidence size differs from archived content.')

    def _input_assets(self, assets):
        result = {}
        for sha, path in assets.items():
            actual, size = file_digest(ordinary(path))
            if actual != sha:
                fail('input_digest_mismatch', 'Input asset differs from its declared identity.', {'sha256': sha})
            result[sha] = size
        return result

    @incremental_operation
    def ingest(self, records, assets, origin, operation_id, expected_head=None, foreign_reviews=None, source_origins=None, excluded_records=None, source_selected=None, source_objective=None, *, _bind_objective=False):
        foreign_reviews = foreign_reviews or []
        source_origins = source_origins or []
        excluded_records = excluded_records or []
        incoming_excluded = self._check_excluded(excluded_records)
        if source_objective is not None:
            self._object(source_objective)
        if source_selected is not None and (not isinstance(source_selected, dict) or not all(isinstance(k, str) and k.strip() for k in source_selected)):
            fail('source_selection_invalid', 'Reported source selection must map record identities to exact revisions.')
        if not isinstance(origin, str) or not origin.strip():
            fail('source_required', 'Record an explicit contribution source.')
        if not isinstance(source_origins, list):
            fail('source_origins_invalid', 'Reported source relationships must be a list.')
        for item in source_origins:
            if not isinstance(item, dict) or set(item) != {'record', 'source'} or not isinstance(item['source'], str) or not item['source'].strip():
                fail('source_origins_invalid', 'Each reported source must bind one exact research revision.')
            self._object(item['record'])
        for record in records:
            validate_record(record)
        for review in foreign_reviews:
            validate_review(review)
        asset_inputs = self._input_assets(assets)
        request = {'records': records, 'assets': asset_inputs, 'origin': origin, 'foreign_reviews': foreign_reviews, 'source_origins': source_origins, 'excluded_records': excluded_records, 'source_selected': source_selected, 'source_objective': source_objective, 'bind_objective': _bind_objective}
        request_sha = digest(canonical(request))
        result_details = {'new': [], 'existing': [], 'extensions': [], 'conflicts': [], 'objective_conflicts': [], 'objective_anchor_resolved': False}

        def change(snapshot):
            old_records = self.records(snapshot)
            previously_unresolved = snapshot['objective'] is not None and snapshot['objective'] not in old_records
            incoming = {digest(canonical(record)): record for record in records}
            known_records = {**old_records, **incoming}
            prior_ids = set(snapshot['selected'])
            from crs_continuity import revision_order
            ordered_incoming, branch_points, ambiguous_new_ids = revision_order(records, old_records)
            if snapshot['objective'] is None:
                if source_objective is not None:
                    declared = known_records.get(source_objective)
                    if declared and declared['kind'] != 'objective':
                        fail('source_objective_invalid', 'The declared primary revision is not an objective.')
                    snapshot['objective'] = source_objective
                else:
                    questions = {sha: row for sha, row in incoming.items() if row['kind'] == 'objective'}
                    if len(questions) > 1:
                        fail('objective_ambiguous', 'Several initial objective revisions need one explicit source_objective; do not choose by import order.')
                    if questions:
                        snapshot['objective'] = next(iter(questions))
            anchor = known_records.get(snapshot['objective'])
            if anchor and anchor['kind'] != 'objective':
                fail('objective_required', 'The founding question must be an objective record.')
            if _bind_objective:
                if len(records) != 1 or anchor is None or not objective_complete(records[0]):
                    fail('objective_binding_invalid', 'Bind one complete objective description to an available founding question.')
                target = records[0]
                current = snapshot['selected'].get(anchor['id'])
                if target['id'] != anchor['id'] or (target['previous'] != current and digest(canonical(target)) != current):
                    fail('objective_binding_invalid', 'The description must explicitly revise the currently selected primary record.')
                if not self._check_primary_revision(snapshot, known_records, target, completing=True):
                    fail('objective_binding_invalid', 'The complete description cannot restore an incomplete identity.')
                snapshot['objective_binding'] = digest(canonical(target))
            reported_primary = (source_selected or {}).get(anchor['id']) if anchor else None
            if anchor and source_objective == snapshot['objective'] and reported_primary in known_records:
                # Even an existing local selection cannot conceal a source that
                # explicitly reports a different research question as primary.
                self._check_primary_revision(snapshot, known_records, known_records[reported_primary])
            selectable = {}
            for sha, row in incoming.items():
                try:
                    selectable[sha] = self._check_primary_revision(snapshot, known_records, row, _bind_objective)
                except CRSError as error:
                    historical_source_conflict = (
                        error.code == 'objective_identity_changed' and anchor is not None
                        and source_objective == snapshot['objective'] and source_selected is not None
                        and row['id'] == anchor['id'] and reported_primary is not None and sha != reported_primary)
                    if not historical_source_conflict:
                        raise
                    selectable[sha] = False
                    result_details['objective_conflicts'].append(sha)
            for sha, path in assets.items():
                if self.put_file(path, expected_sha=sha) != sha:
                    fail('input_changed', 'Input changed while being archived.')
            for record in ordered_incoming:
                sha = self.put_json(record)
                if sha in snapshot['record_hashes']:
                    result_details['existing'].append(sha)
                else:
                    previous = record['previous']
                    known = snapshot['selected'].get(record['id'])
                    if previous and previous in old_records and old_records[previous]['id'] != record['id']:
                        fail('revision_identity_mismatch', 'A prior revision belongs to another record.')
                    if not selectable[sha] or previous in branch_points or (record['id'] not in prior_ids and record['id'] in ambiguous_new_ids):
                        result_details['conflicts'].append(sha)
                    elif known is None:
                        snapshot['selected'][record['id']] = sha
                        result_details['new'].append(sha)
                    elif previous == known:
                        snapshot['selected'][record['id']] = sha
                        result_details['extensions'].append(sha)
                    else:
                        result_details['conflicts'].append(sha)
                    snapshot['record_hashes'].append(sha)
                    old_records[sha] = record
                provenance = {'record': sha, 'source': origin}
                if provenance not in snapshot['origins']:
                    snapshot['origins'].append(provenance)
                self._verify_evidence(record)
            snapshot['record_hashes'].sort()
            excluded = self._check_excluded(snapshot.get('excluded_records', []))
            for sha, item in incoming_excluded.items():
                if sha in excluded and excluded[sha] != item:
                    fail('excluded_identity_conflict', 'Sources disagree on an excluded record identity.')
                if sha in old_records and old_records[sha]['id'] != item['id']:
                    fail('excluded_identity_conflict', 'Excluded reference disagrees with available record bytes.')
                excluded[sha] = item
            for sha in snapshot['record_hashes']:
                excluded.pop(sha, None)
            snapshot['excluded_records'] = [excluded[sha] for sha in sorted(excluded)]
            if source_selected is not None:
                for identifier, sha in source_selected.items():
                    self._object(sha)
                    referenced = old_records.get(sha) or excluded.get(sha)
                    if not referenced or referenced['id'] != identifier:
                        fail('source_selection_invalid', 'Reported selection must resolve to the same known or excluded record identity.')
                    if identifier not in prior_ids:
                        primary = known_records.get(snapshot['objective'])
                        if primary and identifier == primary['id']:
                            if sha not in known_records or not self._check_primary_revision(snapshot, known_records, known_records[sha]):
                                continue
                        snapshot['selected'][identifier] = sha
            for provenance in source_origins:
                if provenance['record'] not in snapshot['record_hashes']:
                    fail('source_revision_missing', 'A reported source refers to a revision absent from this project.')
                if provenance not in snapshot['origins']:
                    snapshot['origins'].append(dict(provenance))
            if previously_unresolved and anchor is not None:
                # The exact pre-registered body restores the declared question.
                # Earlier same-ID questions remain intact and visibly conflicted.
                snapshot['selected'][anchor['id']] = snapshot['objective']
                result_details['objective_anchor_resolved'] = True
                result_details['objective_conflicts'] = objective_conflicting_revisions(
                    old_records, snapshot['objective'], snapshot.get('objective_binding'))
            if _bind_objective:
                bound = snapshot['objective_binding']
                snapshot['selected'][records[0]['id']] = bound
            for review in foreign_reviews:
                sha = self.put_json(review)
                if sha not in snapshot['foreign_reviews']:
                    snapshot['foreign_reviews'].append(sha)
            return snapshot

        before = self.head()
        with self.batch_work():
            result = self.transact(operation_id, request_sha, change, expected_head)
        from crs_continuity import intake_notice
        return intake_notice({**result, 'classification': result_details, 'foreign_reviews_trusted': False}, before, self.root)

    def bind_objective(self, record, origin, operation_id, expected_head=None):
        """Explicitly complete an incomplete description; never inherit review."""
        return self.ingest([record], {}, origin, operation_id, expected_head, _bind_objective=True)

    def review_context(self, snapshot=None):
        """Read the fixed review authority context without writing project state."""
        snapshot = snapshot if snapshot is not None else self.snapshot()
        return {'snapshot': snapshot, 'records': self.records(snapshot),
                'local_reviews': {sha: self.get_json(sha) for sha in snapshot['reviews']}}

    @incremental_operation
    def prepare_review(self, review, snapshot=None, report_bytes=None, context=None):
        """Pure admission checks shared by single and batch review publication."""
        validate_review(review)
        context = context if context is not None else self.review_context(snapshot)
        snapshot = context['snapshot']
        target = context['records'].get(review['target'])
        if target is None:
            fail('review_target_missing', 'The reviewed revision is not in this project.')
        report = self.get_blob(review['report']) if report_bytes is None else report_bytes
        if not isinstance(report, bytes) or digest(report) != review['report']:
            fail('report_mismatch', 'The review points to different report bytes.')
        if not report.strip():
            fail('empty_review_report', 'Store the actual review findings before recording a decision.')
        if review['report'] == review['target']:
            fail('review_report_is_target', 'The reviewed document itself is not an independent review report.')
        checked, cited, excluded_cited = [], [], []
        evidence = {item['sha256']: item for item in target['evidence']}
        excluded = self._check_excluded(snapshot.get('excluded_records', []))
        weak_references = {d['revision'] for d in target['dependencies'] if d['relation'] in {'background', 'extends'}}
        strong_references = {d['revision'] for d in target['dependencies'] if d['relation'] in {'premise', 'input', 'term'}}
        if target['correction'] and target['correction']['replacement']:
            weak_references.add(target['correction']['replacement'])
        previous = target['previous']
        if (previous in excluded and excluded[previous]['reason'] == 'portable_replacement'
                and excluded[previous]['replacement'] == review['target']
                and {'record_fidelity', 'source'} <= set(review['coverage'])):
            weak_references.add(previous)
        for sha in review['materials']:
            try:
                if sha != review['report']:
                    self.resolve(sha)
                checked.append(sha)
            except CRSError as error:
                if error.code == 'asset_missing' and sha in excluded and sha in weak_references and sha not in strong_references:
                    excluded_cited.append(sha)
                    continue
                item = evidence.get(sha)
                if error.code != 'asset_missing' or 'source' not in review['coverage'] or not item or not (item['summary'] and item['locator']):
                    raise
                cited.append(sha)
        for sha in review['supersedes']:
            prior = context['local_reviews'].get(sha)
            if prior is None or prior['target'] != review['target']:
                fail('review_supersession_invalid', 'A review can replace only an existing local review of the same revision.')
        return {'target': review['target'], 'decision': review['decision'],
                'locally_reopened_materials': sorted(set(checked)),
                'source_citations_not_replayed': sorted(set(cited)),
                'excluded_references_not_replayed': sorted(set(excluded_cited)),
                'claim_verdict_proven_by_software': False}

    def review(self, review, operation_id, expected_head=None):
        validate_review(review)
        request_sha = digest(canonical(review))
        details = {'locally_reopened_materials': [], 'source_citations_not_replayed': [],
                   'excluded_references_not_replayed': [], 'claim_verdict_proven_by_software': False}
        def change(snapshot):
            checked = self.prepare_review(review, snapshot)
            details.update({key: checked[key] for key in details})
            sha = self.put_json(review)
            if sha not in snapshot['reviews']:
                snapshot['reviews'].append(sha)
            return snapshot
        return {**self.transact(operation_id, request_sha, change, expected_head), **details}

    @incremental_operation
    def review_many(self, reviews, reports, operation_id, expected_head=None):
        """One prepared/committed operation; failed I/O may leave unreferenced CAS bytes."""
        reviews = copy.deepcopy(reviews)
        if not isinstance(reviews, list) or not reviews:
            fail('review_batch_invalid', 'Provide a nonempty review list.')
        for review in reviews:
            validate_review(review)
        required = {review['report'] for review in reviews}
        if not isinstance(reports, dict) or set(reports) != required:
            fail('review_batch_reports_invalid', 'Frozen reports must exactly match the reviews.')
        reports = dict(reports)
        for sha, data in reports.items():
            if not isinstance(data, bytes) or digest(data) != sha:
                fail('report_mismatch', 'Frozen report bytes do not match the batch.')
        review_hashes = [digest(canonical(review)) for review in reviews]
        if len(set(review_hashes)) != len(review_hashes):
            fail('review_batch_duplicate', 'List each exact review only once per batch.')
        request = {'schema': 'crs-review-batch/v1', 'reviews': reviews, 'reports': sorted(reports)}
        details = []
        def change(snapshot):
            context = self.review_context(snapshot)
            errors = []
            for index, review in enumerate(reviews):
                try:
                    if set(review['supersedes']) & set(review_hashes):
                        fail('review_batch_internal_supersession', 'A batch cannot replace another review in the same batch.')
                    details.append(self.prepare_review(review, report_bytes=reports[review['report']], context=context))
                except (CRSError, OSError) as error:
                    errors.append({'index': index, 'code': getattr(error, 'code', 'review_batch_io_error'),
                                   'message': str(error), 'details': getattr(error, 'details', None)})
            if errors:
                fail('review_batch_blocked', 'No reviews were published; correct all reported batch items.', {'findings': errors})
            # All admission checks finish against the pre-batch snapshot before CAS writes.
            for sha, data in reports.items():
                if self.put_blob(data) != sha:
                    fail('report_mismatch', 'Report publication changed its content identity.')
            for review in reviews:
                sha = self.put_json(review)
                if sha not in snapshot['reviews']:
                    snapshot['reviews'].append(sha)
            return snapshot
        result = self.transact(operation_id, digest(canonical(request)), change, expected_head)
        return {**result, 'items': details, 'review_count': len(reviews),
                'claim_verdict_proven_by_software': False}

    def select(self, identifier, revision, operation_id, expected_head=None):
        request = digest(canonical({'id': identifier, 'revision': revision}))
        def change(snapshot):
            record = self.records(snapshot).get(revision)
            if not record or record['id'] != identifier:
                fail('selection_invalid', 'Select an existing revision of the same record.')
            if not self._check_primary_revision(snapshot, self.records(snapshot), record):
                fail('objective_binding_required', 'Use bind-objective to explicitly complete an incomplete description; selection cannot replace that action.')
            snapshot['selected'][identifier] = revision
            return snapshot
        return self.transact(operation_id, request, change, expected_head)

    def cold_move(self, sha, cold_root):
        if self._location_scope is not None or self._json_reads is not None:
            fail('location_batch_readonly', 'Cold relocation requires a separate operation.')
        external_target(self, cold_root)
        hot = self._object(sha)
        cold_root = ordinary(Path(cold_root).absolute()).resolve()
        if cold_root.is_relative_to(self.root) or self.root.is_relative_to(cold_root):
            fail('cold_root_overlap', 'Cold storage must be outside the formal project and its ancestor directories.')
        cold_root.mkdir(parents=True, exist_ok=True)
        with self._lock():
            table = self._locations(with_pending=True)
            pending = table.setdefault('pending_moves', {})
            retained_external = set()

            def receipt(moved, destination):
                return {'sha256': sha, 'moved': moved, 'location': str(destination),
                        'retained_external_sources': sorted(retained_external),
                        'external_cleanup': 'not_attempted_without_exclusive_ownership' if retained_external else 'not_needed_for_this_operation'}

            def save():
                self._atomic(self._path('locations.json'), canonical(table))

            def verify(path, code):
                ordinary(path)
                if not path.is_file() or file_digest(path)[0] != sha:
                    fail(code, 'Cold movement requires the exact registered evidence bytes.', {'sha256': sha})

            def finish(move):
                source = ordinary(Path(move['source']))
                destination = ordinary(Path(move['destination']))
                if destination.exists():
                    verify(destination, 'cold_destination_conflict')
                else:
                    verify(source, 'asset_corrupt' if source.exists() else 'asset_missing')
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with crs_temp.TemporaryDirectory(prefix='.crs-cold-', dir=destination.parent) as work:
                        staged = Path(work) / 'object'
                        shutil.copyfile(source, staged)
                        verify(staged, 'cold_copy_corrupt')
                        with staged.open('r+b') as f:
                            f.flush()
                            os.fsync(f.fileno())
                        try:
                            # Exclusive publication never replaces an unrelated destination.
                            os.link(staged, destination)
                        except FileExistsError:
                            verify(destination, 'cold_destination_conflict')
                if source.exists():
                    verify(source, 'cold_move_changed')
                verify(destination, 'cold_move_changed')
                table['objects'][sha] = str(destination)
                save()  # Keep the old source in the same durable location table.
                if source.exists():
                    verify(source, 'cold_move_changed')
                    verify(destination, 'cold_move_changed')
                    if source == hot:
                        source.unlink()  # Only this project's owned CAS object.
                    else:
                        # A local locator does not establish exclusive ownership:
                        # another project may still use this exact cold file.
                        retained_external.add(str(source))
                pending.pop(sha)
                save()

            recovered = sha in pending
            if recovered:
                # Reads never delete. Explicit cold movement completes any prior intent
                # before a caller can ask to move the same object somewhere else.
                finish(pending[sha])
            source = self.resolve(sha)
            destination = ordinary(cold_root / sha)
            if source == destination:
                return receipt(recovered, destination)
            if destination.exists():
                verify(destination, 'cold_destination_conflict')
            pending[sha] = {'source': str(source), 'destination': str(destination)}
            save()  # Record the exact source before creating any second official copy.
            finish(pending[sha])
            return receipt(True, destination)

    def audit(self):
        from crs_unique import require_unique
        global_content = require_unique(project_scope(self))
        snapshot = self.snapshot()
        records = self.records(snapshot)
        self.reviews(snapshot)
        seen = {}
        duplicates = []
        count = total = 0
        for path in sorted(self.root.rglob('*')):
            ordinary(path)
            if not path.is_file():
                continue
            sha, size = file_digest(path)
            count += 1
            total += size
            relative = path.relative_to(self.root).as_posix()
            if sha in seen:
                duplicates.append([seen[sha], relative])
            else:
                seen[sha] = relative
            if relative.startswith('objects/') and ''.join(path.relative_to(self.root / 'objects').parts) != sha:
                fail('asset_corrupt', 'A stored object differs from its path identity.', {'path': relative})
        external = []
        missing = []
        for sha, location in self._locations().items():
            path = ordinary(Path(location))
            if path.exists():
                if file_digest(path)[0] != sha:
                    fail('asset_corrupt', 'Cold asset differs from its identity.', {'sha256': sha})
                if sha in seen:
                    duplicates.append([seen[sha], 'cold:' + sha])
                seen[sha] = 'cold:' + sha
                external.append(sha)
            else:
                missing.append(sha)
        for record in records.values():
            for evidence in record['evidence']:
                sha = evidence['sha256']
                if sha not in seen and sha not in missing:
                    missing.append(sha)
        return {'content_uniqueness': global_content, 'ok': not duplicates, 'snapshot': self.head(), 'files': count, 'bytes': total, 'duplicate_content': duplicates, 'cold_available': external, 'not_locally_available': sorted(set(missing)), 'evidence_replayed': False}


@contextlib.contextmanager
def replay_workspace(store, mapping, temp_root=None):
    external_target(store, temp_root or tempfile.gettempdir())
    """Reopen exact assets into an owned external directory and clean on exit.

    Capture any new evidence inside the context with store.put_file before exit.
    This utility never executes commands or guesses which outputs are evidence.
    """
    base = Path(temp_root).absolute() if temp_root else Path(tempfile.gettempdir()).resolve()
    ordinary(base)
    if base.resolve().is_relative_to(store.root):
        fail('temporary_root_inside_project', 'Reproduction copies must be outside the formal project.')
    base.mkdir(parents=True, exist_ok=True)
    with crs_temp.TemporaryDirectory(prefix='crs-replay-', dir=base) as directory:
        work = Path(directory).resolve()
        used = set()
        for name, sha in mapping.items():
            relative = Path(name)
            key = name.replace('\\', '/').casefold()
            if relative.is_absolute() or ':' in name or '..' in relative.parts or key in used:
                fail('replay_path_invalid', 'Reproduction paths must be unique safe relative filenames.')
            used.add(key)
            target = (work / relative).resolve()
            if not target.is_relative_to(work):
                fail('replay_path_invalid', 'Reproduction path leaves its owned temporary directory.')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(store.resolve(sha), target)
            if file_digest(target)[0] != sha:
                fail('replay_copy_corrupt', 'Temporary reproduction copy does not match its source.')
        yield work
