"""Read-only metadata simulation of the existing intake and assessment rules.

Incoming paths are opaque labels. No incoming file, report, CAS object or journal
is opened or written. This is an expectation, never an admission receipt.
"""
import contextlib
import copy
from types import SimpleNamespace

from crs_model import CRSError, assess, canonical, digest, parse_json, validate_review
from crs_store import Store

class _Declared:
    def __init__(self, size):
        self.size = size
    def stat(self):
        return SimpleNamespace(st_size=self.size)

class _PredictionStore(Store):
    def __init__(self, source, head, snapshot, records, reviews, intake):
        # Deliberately no Store filesystem initialization or mutable publication.
        self.root = source.root
        self.current = head
        self.state = copy.deepcopy(snapshot)
        self.blobs = {sha: canonical(row) for sha, row in records.items()}
        self.blobs.update({digest(canonical(row)): canonical(row) for row in reviews})
        self.blobs[head] = canonical(snapshot)
        self.assets = intake['assets']
    def operation_io(self):
        # Metadata-only adapter: ingest's operation decorator must never enter
        # a filesystem location lease or create a source-project mutex/file.
        return contextlib.nullcontext()

    def _object(self, sha):
        from crs_store import SHA
        if not isinstance(sha, str) or not SHA.fullmatch(sha):
            raise CRSError('invalid_digest', 'Expected an exact SHA-256 identity.')
        return sha
    def head(self):
        return self.current
    def snapshot(self, head=None):
        return copy.deepcopy(self.state)
    def get_blob(self, sha):
        if sha not in self.blobs:
            raise CRSError('asset_missing', 'Metadata is absent from this prediction.')
        return self.blobs[sha]
    def get_json(self, sha):
        # Own the pure-memory contract independently of Store read-cache fields.
        return parse_json(self.get_blob(sha))

    def resolve(self, sha):
        if sha in self.blobs:
            return _Declared(len(self.blobs[sha]))
        raise CRSError('asset_missing', 'No size declaration is available.')
    def put_blob(self, data):
        sha = digest(data)
        self.blobs[sha] = data
        return sha
    def put_file(self, source, expected_sha=None):
        self._object(expected_sha)
        if expected_sha not in self.assets or self.assets[expected_sha] != source:
            raise CRSError('input_digest_mismatch', 'Prediction asset binding changed.')
        return expected_sha
    def _verify_evidence(self, record):
        # Byte availability and size are explicitly outside metadata prediction.
        # In particular, conflicting missing-source declarations remain history.
        pass

    def _input_assets(self, assets):
        for sha in assets:
            self._object(sha)
        # Unreferenced assets have no declared size. Zero is only a placeholder
        # in the discarded simulation request hash, never an admission binding.
        return {sha: 0 for sha in assets}
    @contextlib.contextmanager
    def batch_work(self):
        yield
    def _atomic(self, *args):
        raise AssertionError('Prediction cannot publish files')
    def transact(self, operation_id, request_sha256, transform, expected_head=None):
        if expected_head is not None and expected_head != self.current:
            raise CRSError('stale_snapshot', 'Prediction context changed.')
        before = copy.deepcopy(self.state)
        candidate = transform(copy.deepcopy(before))
        self._guard_objective_transition(before, candidate)
        self._refresh_objective(candidate)
        self.state = candidate
        return {'committed': False}

def _simulate(store, intake, expected_head=None):
    if (not isinstance(intake, dict) or set(intake) != {'schema', 'origin', 'records', 'assets', 'foreign_reviews'}
            or intake['schema'] != 'crs-intake/v1' or not isinstance(intake['records'], list)
            or not isinstance(intake['assets'], dict)):
        raise CRSError('intake_invalid', 'Use an intake and an optional array of proposed local reviews.')
    # Validate record shape before the simulation indexes evidence declarations.
    from crs_model import validate_record
    for row in intake['records']:
        validate_record(row)
    head = store.head()
    if expected_head is not None and expected_head != head:
        raise CRSError('stale_snapshot', 'Reopen the project before predicting.')
    snapshot = store.snapshot(head)
    records = store.records(snapshot)
    reviews = [store.get_json(sha) for sha in snapshot['reviews']]
    for review in reviews:
        validate_review(review)
    memory = _PredictionStore(store, head, snapshot, records, reviews, intake)
    result = memory.ingest(intake['records'], intake['assets'], intake['origin'], 'prediction', head, intake['foreign_reviews'])
    return memory, result, head, records, reviews

def _assessment(store, intake, proposed_reviews, memory, result, head, reviews):
    if not isinstance(proposed_reviews, list):
        raise CRSError('intake_invalid', 'Proposed reviews must be an array.')
    predicted_records = memory.records(memory.state)
    for review in proposed_reviews:
        validate_review(review)
    states = assess(predicted_records, reviews + proposed_reviews, memory.state['selected'],
                    excluded_records=memory.state.get('excluded_records', []))
    if store.head() != head:
        raise CRSError('stale_snapshot', 'Project changed during prediction; reopen its current snapshot.')
    incoming = list(dict.fromkeys(digest(canonical(row)) for row in intake['records']))
    return {'snapshot': head, 'prediction_only': True, 'committed': False,
            'review_admitted': False, 'materials_verified': False, 'claims_verified': False,
            'classification': result['classification'],
            'incoming': [{'revision': sha, 'status': states[sha]} for sha in incoming],
            'counts': {'incoming': len(incoming), 'accepted': sum(states[sha]['review'] == 'accepted' for sha in incoming),
                       'usable': sum(states[sha]['usable'] for sha in incoming),
                       'exportable': sum(states[sha]['exportable'] for sha in incoming)},
            'assumptions': ['Incoming material sizes are declarations; no incoming file is read.',
                            'Proposed local reviews are hypothetical; reports and admission remain unchecked.',
                            'Foreign reviews remain untrusted and are excluded from predicted authority.',
                            'Commit with this expected_head and perform normal integrity/admission checks.',
                            'A saved archive may legitimately contain unreviewed or unusable records.']}


def predict(store, intake, proposed_reviews, expected_head=None):
    memory, result, head, _, reviews = _simulate(store, intake, expected_head)
    return _assessment(store, intake, proposed_reviews, memory, result, head, reviews)
