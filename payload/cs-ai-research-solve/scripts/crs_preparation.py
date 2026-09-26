"""Declared archive preparation: external reports, metadata expectations, no admission."""
from pathlib import Path

from crs_model import CRSError, canonical, digest, parse_json, _review_materials, _qualified_accept
from crs_batch import prepare_batch
from crs_prediction import _simulate, _assessment
from crs_store import ordinary, SHA

MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_RECORDS = 100
MAX_REPORT_ITEMS = 1000
MAX_ASSETS = 10000


def load_input(path):
    with ordinary(Path(path).absolute()).open('rb') as stream:
        raw = stream.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise CRSError('intake_invalid', 'Preparation input exceeds the 8 MiB boundary.')
    return parse_json(raw)


def prepare_archive(store, intake, batch, base_dir, revisions=(), expected_head=None):
    """Use one source metadata snapshot; read only explicitly named report files.

    Returned states are hypothetical, including when all supplied reviews qualify.
    No incoming evidence is opened; batch admission and locked byte checks remain
    necessary after ingestion. Existing report copies use prepare-report separately.
    """
    if (not isinstance(intake, dict) or not isinstance(intake.get('records'), list)
            or not 1 <= len(intake['records']) <= MAX_RECORDS
            or not isinstance(intake.get('assets'), dict) or len(intake['assets']) > MAX_ASSETS):
        raise CRSError('intake_invalid', 'Preparation accepts at most 100 records and 10000 declared assets.')
    if (not isinstance(batch, dict) or set(batch) != {'schema', 'items'}
            or batch['schema'] != 'crs-review-batch/v1' or not isinstance(batch['items'], list)
            or not 1 <= len(batch['items']) <= MAX_REPORT_ITEMS):
        raise CRSError('review_batch_invalid', 'Supply an existing review batch with 1..1000 items.')
    # Stored-report fallback is deliberately outside this declared-file operation.
    # Missing files produce ordinary batch findings, never an invented report.
    for item in batch['items']:
        if not isinstance(item, dict) or set(item) != {'review', 'report'} or not isinstance(item['report'], str) or not item['report']:
            raise CRSError('review_batch_item_invalid', 'Preparation requires each review and an explicit relative report file.')
    if not isinstance(revisions, (list, tuple)) or len(revisions) > MAX_RECORDS or any(not isinstance(sha, str) or not SHA.fullmatch(sha) for sha in revisions) or len(set(revisions)) != len(revisions):
        raise CRSError('exact_read_required', 'Choose at most 100 distinct exact old revisions.')
    with store.read_io():
        memory, result, head, old_records, old_reviews = _simulate(store, intake, expected_head)
        absent = [sha for sha in revisions if sha not in old_records]
        if absent:
            raise CRSError('exact_read_required', 'Requested old revisions must belong to this snapshot.', {'missing_revisions': absent})
        prepared, batch_report = prepare_batch(memory, batch, base_dir, verify_materials=False)
        # These two report-content conditions are also required by prepare_review.
        # Check frozen bytes now, without executing that method's material I/O.
        if prepared is not None:
            for index, review in enumerate(prepared['reviews']):
                raw = prepared['reports'][review['report']]
                code = ('empty_review_report' if not raw.strip() else
                        'review_report_is_target' if review['report'] == review['target'] else None)
                if code:
                    finding = {'index': index, 'stage': 'report_content', 'code': code}
                    batch_report['findings'].append(finding)
                    batch_report['items'][index]['findings'].append(finding)
            if batch_report['findings']:
                batch_report['ok'] = False
                batch_report['status'] = 'review_batch_blocked'
        reviews = prepared['reviews'] if prepared is not None else []
        prediction = _assessment(store, intake, reviews, memory, result, head, old_reviews)
        records = memory.records(memory.state)
        checklist = []
        for index, review in enumerate(reviews):
            target = records.get(review['target'])
            checklist.append({'index': index, 'target': review['target'],
                'target_present': target is not None,
                'missing_materials': sorted(_review_materials(target) - set(review['materials'])) if target else [],
                'qualifies_for_acceptance': bool(target is not None and _qualified_accept(target, review))})
        # Exact old records are selected from the same loaded view, not re-queried.
        old = [{'revision': sha, 'record': old_records[sha]} for sha in revisions]
        incoming = {row['revision']: row['status'] for row in prediction['incoming']}
        gaps = [sha for sha, state in incoming.items() if state['review'] != 'accepted']
        if store.head() != head:
            raise CRSError('stale_snapshot', 'Project changed during preparation; reopen its current snapshot.')
        return {'snapshot': head, 'preparation_only': True, 'committed': False,
                'review_admitted': False, 'materials_verified': False, 'claims_verified': False,
                'report_bindings_ok': batch_report['ok'],
                'prediction_complete': prepared is not None,
                'ready_for_declared_acceptance': bool(batch_report['ok'] and not gaps and all(
                    row['target_present'] and not row['missing_materials'] for row in checklist)),
                'prediction': prediction, 'not_accepted': gaps,
                'review_checklist': checklist, 'report_preparation': batch_report,
                'old_records': old,
                'limits': {'records': MAX_RECORDS, 'old_revisions': MAX_RECORDS,
                           'review_items': MAX_REPORT_ITEMS, 'assets': MAX_ASSETS},
                'next_action': 'Inspect gaps and actual review content; ingest with this HEAD, then run native review-batch --check-only and commit its exact binding. Preparation is not a preflight receipt.'}
