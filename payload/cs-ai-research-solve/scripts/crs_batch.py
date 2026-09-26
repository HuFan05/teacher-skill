"""Bounded, read-only batch preparation and explicit atomic local review admission."""
from __future__ import annotations

import io
from pathlib import Path, PureWindowsPath

from crs_model import CRSError, canonical, digest, parse_json, validate_review
from crs_store import ordinary
from crs_exchange import _portable_text, _portable_file

MAX_BATCH_ITEMS = 1000
MAX_REPORT_BYTES = 16 * 1024 * 1024
MAX_BATCH_REPORT_BYTES = 64 * 1024 * 1024


def _fail(code, message, details=None):
    raise CRSError(code, message, details)


class _FrozenReport:
    """Existing file scanner reads one frozen byte value, never reopens a source."""
    def __init__(self, data):
        self.data = data

    def open(self, mode):
        if mode != 'rb':
            raise ValueError('Frozen reports permit binary reads only.')
        return io.BytesIO(self.data)


def prepare_batch(store, submission, base_dir, *, verify_materials=True):
    """Return (in-memory prepared data, JSON report); write no project or temp file."""
    if (not isinstance(submission, dict) or set(submission) != {'schema', 'items'}
            or submission['schema'] != 'crs-review-batch/v1'
            or not isinstance(submission['items'], list) or not submission['items']
            or len(submission['items']) > MAX_BATCH_ITEMS):
        _fail('review_batch_invalid', 'Use crs-review-batch/v1 with1..1000 review/report items.')
    base = ordinary(Path(base_dir).absolute()).resolve()
    head = store.head()
    snapshot = store.snapshot(head)
    context = store.review_context(snapshot)
    frozen_paths, reports, reviews, rows, findings = {}, {}, [], [], []
    total_bytes = 0
    bindings_complete = True

    def add(row, stage, error):
        item = {'index': row['index'], 'stage': stage,
                'code': getattr(error, 'code', 'review_batch_input_error'),
                'message': str(error), 'details': getattr(error, 'details', None)}
        row['findings'].append(item)
        findings.append(item)

    def read_report(bound, name):
        nonlocal total_bytes
        if name is None:
            sha = bound.get('report') if isinstance(bound, dict) else None
            path = store._object(sha)
            if not path.exists():
                location = store._locations().get(sha)
                if location is None:
                    _fail('asset_missing', 'The bound report is not available locally.', {'sha256': sha})
                path = ordinary(Path(location))
        else:
            if (not isinstance(name, str) or not name or Path(name).is_absolute()
                    or PureWindowsPath(name).drive or PureWindowsPath(name).root):
                _fail('batch_report_path_invalid', 'Report paths must be relative to the batch file directory.')
            path = ordinary(base / name)
            if not path.resolve().is_relative_to(base):
                _fail('batch_report_path_invalid', 'Report paths must remain within the batch file directory.')
        path = ordinary(path)
        if not path.is_file():
            _fail('batch_report_missing', 'A report must be an ordinary readable file.')
        key = str(path.resolve())
        if key not in frozen_paths:
            if path.stat().st_size > MAX_REPORT_BYTES:
                _fail('batch_report_limit', 'One review report exceeds the16MiB preparation limit.')
            with path.open('rb') as stream:
                data = stream.read(MAX_REPORT_BYTES + 1)
            if len(data) > MAX_REPORT_BYTES:
                _fail('batch_report_limit', 'One review report exceeds the16MiB preparation limit.')
            if total_bytes + len(data) > MAX_BATCH_REPORT_BYTES:
                _fail('batch_report_limit', 'Frozen batch reports exceed the64MiB preparation limit.')
            total_bytes += len(data)
            frozen_paths[key] = data
        data = frozen_paths[key]
        actual = digest(data)
        declared = bound.get('report') if isinstance(bound, dict) else None
        if declared and declared != actual:
            _fail('report_mismatch', 'The review points to different report bytes.', {'actual_sha256': actual})
        return actual, data

    for index, item in enumerate(submission['items']):
        row = {'index': index, 'review_id': None, 'target': None, 'decision': None, 'coverage': [],
               'epistemic': None, 'review_sha256': None, 'report': None, 'findings': []}
        rows.append(row)
        if not isinstance(item, dict) or set(item) != {'review', 'report'}:
            add(row, 'item_schema', CRSError('review_batch_item_invalid', 'Each item has exactly review and report.'))
            bindings_complete = False
            continue
        try:
            bound = parse_json(canonical(item['review']))
        except (CRSError, TypeError, ValueError) as error:
            add(row, 'review_schema', error)
            bindings_complete = False
            continue
        if isinstance(bound, dict):
            row.update(review_id=bound.get('id'), target=bound.get('target'), decision=bound.get('decision'), coverage=bound.get('coverage'))
        data = None
        try:
            sha, data = read_report(bound, item['report'])
            if isinstance(bound, dict):
                bound['report'] = sha
            row['report'] = {'sha256': sha, 'bytes': len(data)}
            reports[sha] = data
        except (CRSError, OSError, TypeError, ValueError) as error:
            add(row, 'report_binding', error)
            bindings_complete = False
        valid = True
        try:
            validate_review(bound)
            row['review_sha256'] = digest(canonical(bound))
        except CRSError as error:
            add(row, 'review_schema', error)
            valid = False
            bindings_complete = False
        try:
            _portable_text(bound, field='$.review')
        except CRSError as error:
            add(row, 'review_metadata', error)
        if data is not None:
            try:
                _portable_file(_FrozenReport(data))
            except (CRSError, OSError, ValueError) as error:
                add(row, 'report_portability', error)
        if valid and data is not None:
            reviews.append(bound)
            target = context['records'].get(bound['target'])
            row['epistemic'] = target.get('epistemic') if target else None
            if verify_materials:
                try:
                    row['admission_checks'] = store.prepare_review(bound, report_bytes=data, context=context)
                except (CRSError, OSError) as error:
                    add(row, 'review_admission', error)
            else:
                row['material_checks'] = 'deferred_to_locked_transaction'

    batch_sha = None
    prepared = None
    if bindings_complete:
        hashes = [digest(canonical(review)) for review in reviews]
        for row, review in zip(rows, reviews):
            if hashes.count(row['review_sha256']) > 1:
                add(row, 'batch_relations', CRSError('review_batch_duplicate', 'List each exact review only once per batch.'))
            if set(review['supersedes']) & set(hashes):
                add(row, 'batch_relations', CRSError('review_batch_internal_supersession', 'A batch cannot replace another review in the same batch.'))
        batch_sha = digest(canonical({'schema': 'crs-review-batch/v1', 'reviews': reviews, 'reports': sorted(reports)}))
        prepared = {'snapshot': head, 'reviews': reviews, 'reports': reports, 'batch_sha256': batch_sha}
    report = {'ok': not findings, 'status': 'review_batch_ready' if not findings else 'review_batch_blocked',
              'check_only': True, 'committed': False, 'review_admitted': False,
              'claims_verified': False, 'snapshot': head, 'batch_sha256': batch_sha,
              'item_count': len(rows), 'items': rows, 'findings': findings,
              'report_bytes_read': total_bytes, 'unique_report_paths_read': len(frozen_paths),
              'scope': 'Exact batch metadata, frozen reports, portability and existing local admission conditions only; no evidence replay or independent review is performed.'}
    return prepared, report


def _review_batch(store, submission, base_dir, operation_id=None, expected_head=None,
                 expected_batch=None, check_only=False):
    """Check the whole batch or commit the exact prepared content in one operation."""
    if not check_only:
        if expected_batch is None:
            _fail('batch_preflight_required', 'Run review-batch --check-only and submit its exact batch_sha256.')
        if not operation_id:
            _fail('operation_required', 'Batch publication requires an explicit operation ID.')
    # Submission still freezes reports and validates the exact batch. Material
    # admission is performed once inside review_many's project lock, never via
    # a persisted hash/mtime cache or a trusted earlier preflight.
    prepared, report = prepare_batch(store, submission, base_dir, verify_materials=check_only)
    if check_only:
        return report
    if prepared is None:
        _fail('review_batch_blocked', 'No reviews were published; correct the reported batch items.', report)
    if prepared['batch_sha256'] != expected_batch:
        _fail('batch_changed', 'Batch content differs from the preflight binding; check the complete batch again.',
              {'expected_batch': expected_batch, 'actual_batch': prepared['batch_sha256']})
    # A committed/prepared exact operation may be resumed despite later material
    # availability changes; transact validates its binding before any transform.
    prior = store._operation_path(operation_id)
    existing = parse_json(prior.read_bytes()) if prior.is_file() else None
    if existing is not None and existing.get('request_sha256') != prepared['batch_sha256']:
        _fail('operation_reused', 'This operation ID is already bound to different input.')
    if not report['ok'] and existing is None:
        _fail('review_batch_blocked', 'No reviews were published; correct the reported batch items.', report)
    result = store.review_many(prepared['reviews'], prepared['reports'], operation_id,
                              expected_head if expected_head is not None else prepared['snapshot'])
    return {**report, **result, 'ok': True, 'status': 'review_batch_committed',
            'check_only': False, 'review_admitted': True,
            'batch_sha256': prepared['batch_sha256'], 'preflight_snapshot': prepared['snapshot'],
            'items': report['items'], 'claims_verified': False,
            'scope': 'Explicit local review admission with one prepared/committed operation; reviewer findings and claim grades remain exactly as supplied.'}


def review_batch(store, *args, **kwargs):
    """Share one lazy location scope across preparation and publication."""
    with store.operation_io():
        return _review_batch(store, *args, **kwargs)
