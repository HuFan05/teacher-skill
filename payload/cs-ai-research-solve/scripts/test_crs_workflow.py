"""Synthetic end-to-end regressions for the research-data CLI; no archived research material."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

CLI = Path(__file__).resolve().with_name('crs.py')
FIELDS = {'statement', 'domain', 'claim_scope', 'assumptions', 'evidence_standard', 'completion_standard'}


def run(*args, ok=True):
    result = subprocess.run([sys.executable, '-B', str(CLI), *map(str, args)], capture_output=True, text=True)
    value = json.loads(result.stdout)
    if ok and not value.get('ok'):
        raise AssertionError(json.dumps(value)[:2000])
    return value


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return path


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name).resolve()
        self.work = self.base / 'work'
        self.work.mkdir()
        self.project = self.base / 'Project' / 'data'
        self.project.parent.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def objective(self):
        record = run('template', 'record', '--kind', 'objective')['data']
        record.update(title='Merge sort bound', statement=r'Merge sort uses at most $n \lceil \log_2 n \rceil$ comparisons',
                      scope='Comparison sorting of distinct keys', assumptions=['keys are distinct'])
        record['project_objective'] = {
            'statement': record['statement'], 'domain': record['scope'], 'assumptions': record['assumptions'],
            'claim_scope': r'all inputs of size $n \ge 1$; worst case; metric: comparison count',
            'evidence_standard': 'independently reviewed correctness argument',
            'completion_standard': 'accepted established result or refutation'}
        return record

    def test_objective_uses_claim_scope_identity(self):
        record = self.objective()
        self.assertEqual(set(record['project_objective']), FIELDS)
        self.assertEqual(record['epistemic'], 'hypothesis')
        result = run('init', self.project, '--title', 'Study', '--objective-file', write(self.work / 'o.json', record))
        self.assertEqual(result['data']['objective_identity']['completeness'], 'complete')
        broken = dict(record, project_objective={k: v for k, v in record['project_objective'].items() if k != 'claim_scope'})
        other = self.base / 'Other' / 'data'
        other.parent.mkdir()
        self.assertFalse(run('init', other, '--title', 'Bad', '--objective-file', write(self.work / 'bad.json', broken), ok=False)['ok'])

    def test_incomplete_description_needs_explicit_binding(self):
        created = run('init', self.project, '--title', 'Study', '--objective', 'Longer contexts help', '--scope', 'Length generalization')
        identity = created['data']['objective_identity']
        self.assertEqual(identity['completeness'], 'incomplete')
        self.assertEqual(identity['missing_fields'], ['claim_scope', 'completion_standard', 'evidence_standard'])
        status = run('status', self.project)['data']
        anchor = run('record', self.project, '--revision', status['objective'])['data']['record']
        completed = dict(anchor, previous=status['objective'])
        completed['project_objective'] = {'statement': anchor['statement'], 'domain': anchor['scope'], 'assumptions': anchor['assumptions'],
                                          'claim_scope': 'decoder-only models 125M-1.3B; copy task; mean±sd over 5 seeds; exact match',
                                          'evidence_standard': 'bounded_empirical', 'completion_standard': 'reviewed bounded observation'}
        run('bind-objective', self.project, '--objective-file', write(self.work / 'c.json', completed), '--origin', 'explicit', '--operation', 'bind-001')
        self.assertEqual(run('status', self.project)['data']['objective_identity']['completeness'], 'complete')

    def test_review_export_verify_import(self):
        run('init', self.project, '--title', 'Study', '--objective-file', write(self.work / 'o.json', self.objective()))
        head = run('status', self.project)['data']['snapshot']
        (self.work / 'bench.py').write_text('print(1)\n', encoding='utf-8')
        asset = run('asset', self.project, '--put', self.work / 'bench.py')['data']
        record = run('template', 'record', '--kind', 'hypothesis')['data']
        record.update(title='Bound holds for small inputs', statement=r'The bound holds for $n \le 64$', scope='adversarial inputs',
                      action='Instrumented runs', feedback='Held on all sizes',
                      evidence=[{'sha256': asset['sha256'], 'bytes': asset['bytes'], 'name': 'bench.py', 'role': 'code', 'summary': '', 'locator': ''}])
        intake = run('template', 'intake')['data']
        intake.update(origin='synthetic contributor', records=[record])
        run('ingest', self.project, '--submission', write(self.work / 'intake.json', intake), '--operation', 'capture-001', '--expected-head', head)
        match = run('query', self.project, '--kind', 'hypothesis', '--brief')['data']['matches'][0]
        self.assertFalse(match['record_complete'])
        review = run('template', 'review')['data']
        review.update(target=match['revision'], decision='accept', coverage=['record_fidelity', 'computation'], method='Reran the script',
                      scope='n<=64', findings='Held; bounded_empirical only', materials=[asset['sha256']],
                      reviewer={'identity': 'reviewer', 'independence': 'self', 'basis': ''}, created_at='2026-01-01T00:00:00Z')
        (self.work / 'report.md').write_text('Reran and compared counts.\n', encoding='utf-8')
        run('review', self.project, '--review', write(self.work / 'review.json', review), '--report', self.work / 'report.md', '--operation', 'review-001')
        state = run('record', self.project, '--revision', match['revision'])['data']['status']
        self.assertTrue(state['usable'])
        self.assertEqual(state['epistemic'], 'hypothesis')
        exported = self.base / 'handoff.zip'
        run('export', self.project, '--out', exported, '--asset-budget', '16777216')
        verified = run('verify-bundle', exported)['data']
        self.assertTrue(verified['canonical_data_verified'] and verified['human_map_verified'])
        self.assertFalse(verified['evidence_replay_performed'])
        plan = self.base / 'plan.json'
        run('delivery-plan', self.project, '--out', plan)
        planned = self.base / 'planned.zip'
        run('export', self.project, '--out', planned, '--plan', plan, '--expect-plan', hashlib.sha256(plan.read_bytes()).hexdigest())
        self.assertIn('delivery_plan_sha256', run('verify-bundle', planned)['data'])
        receiver = self.base / 'Received' / 'data'
        receiver.parent.mkdir()
        run('init', receiver, '--title', 'Received', '--receive')
        imported = run('import', receiver, exported, '--origin', 'sender', '--operation', 'receive-001')['data']
        self.assertFalse(imported['reviews_automatically_trusted'])

    def test_formula_gate_blocks_plain_text_notation(self):
        run('init', self.project, '--title', 'Study', '--objective-file', write(self.work / 'o.json', self.objective()))
        record = run('template', 'record', '--kind', 'failure')['data']
        record.update(title='Attempt', statement='Tried a bound with x_t>=0', scope='toy inputs', action='Checked', feedback='Failed')
        intake = run('template', 'intake')['data']
        intake.update(origin='synthetic', records=[record])
        run('ingest', self.project, '--submission', write(self.work / 'intake.json', intake), '--operation', 'capture-001')
        blocked = run('map', self.project, '--out', self.base / 'Project' / 'map.md', ok=False)
        self.assertEqual(blocked['code'], 'map_formula_invalid')
        replacements = write(self.work / 'repl.json', [{'source': 'x_t>=0', 'tex': r'x_t \ge 0'}])
        run('map', self.project, '--out', self.base / 'Project' / 'map2.md', '--formula-replacements', replacements)


if __name__ == '__main__':
    unittest.main()
