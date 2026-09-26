#!/usr/bin/env python3
"""CRS: capture, review, query and exchange durable research assets."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

sys.dont_write_bytecode = True
from crs_model import CRSError, assess, canonical, digest, parse_json, objective_identity, objective_complete, OBJECTIVE_FIELDS, RECORD_SCHEMA
from crs_store import Store, ordinary, replay_workspace

from crs_output import SafeParser, add_output_arguments, configure_output, error_response, emit_result, public_main, project_diagnostics

VERSION = 'v1.0.0'


def load(path):
    return parse_json(Path(path).read_bytes())


def record_template(kind='attempt'):
    epistemic = {'objective': 'hypothesis', 'result': 'established'}.get(kind, kind)
    result = {'schema': RECORD_SCHEMA, 'id': str(uuid.uuid4()), 'kind': kind,
            'title': '', 'statement': '', 'scope': '', 'action': '', 'feedback': '',
            'epistemic': epistemic, 'assumptions': [], 'limitations': [], 'reopen': [],
            'sources': [], 'evidence': [], 'dependencies': [], 'conditional_on': [],
            'previous': None, 'correction': None}
    if kind == 'objective':
        result['project_objective'] = {key: ([] if key == 'assumptions' else '') for key in sorted(OBJECTIVE_FIELDS)}
    return result


def review_template():
    return {'schema': 'crs-review/v1', 'id': str(uuid.uuid4()), 'target': '',
            'decision': 'inconclusive', 'coverage': ['record_fidelity'],
            'method': '', 'scope': '', 'findings': '', 'limitations': [],
            'materials': [], 'report': '', 'reviewer': {'identity': '', 'independence': 'unknown', 'basis': ''},
            'created_at': '', 'supersedes': []}


def parser():
    p = SafeParser(description=__doc__)
    p.add_argument('--project-root', help='Explicit entire enclosing project scope; never narrow an existing binding.')
    add_output_arguments(p)
    p.add_argument('--version', action='version', version=VERSION)
    p.add_argument('--workflow', metavar='SESSION_JSON', help='Track this command in an existing external workflow session.')
    sub = p.add_subparsers(dest='command', required=True)
    q = sub.add_parser('template', help='Print an incomplete record or review form; fill it before ingestion.')
    q.add_argument('type', choices=['record', 'review', 'review-batch', 'intake', 'adoption'])
    q.add_argument('--kind', default='attempt', choices=['objective', 'attempt', 'result', 'hypothesis', 'observation', 'failure', 'definition', 'correction'])
    q.add_argument('--inventory'); q.add_argument('--out')
    q = sub.add_parser('inventory', help='Inventory one bounded external research directory or ZIP without running its code.')
    q.add_argument('source'); q.add_argument('--out', required=True)
    q.add_argument('--max-files', type=int, default=10000, help='Maximum visited entries, including directories and skipped cache entries.'); q.add_argument('--max-bytes', type=int, default=1073741824)
    q.add_argument('--max-metadata-bytes', type=int, default=67108864, help='Maximum source metadata bytes before full archive parsing.')
    q = sub.add_parser('init', help='Create a new empty research project.')
    q.add_argument('project'); q.add_argument('--title', required=True)
    group = q.add_mutually_exclusive_group(required=True)
    group.add_argument('--receive', action='store_true', help='Initialize without inventing a question; the first imported package supplies its declared primary anchor.')
    group.add_argument('--objective-file', help='Complete objective record with six explicit constituents.')
    group.add_argument('--objective', help='Incomplete statement only; missing constituents remain visibly unknown.')
    q.add_argument('--scope')
    q = sub.add_parser('bind-objective', help='Explicitly complete the existing question description without inheriting review.')
    q.add_argument('project'); q.add_argument('--objective-file', required=True)
    q.add_argument('--origin', required=True); q.add_argument('--operation', required=True); q.add_argument('--expected-head')
    for name in ['status', 'audit']:
        q = sub.add_parser(name); q.add_argument('project')
        if name == 'status': q.add_argument('--full', action='store_true', help='Explicit broader selection view, still subject to the response limit.')
    q = sub.add_parser('asset', help='Archive exact bytes or locate a verified asset.')
    q.add_argument('project')
    group = q.add_mutually_exclusive_group(required=True)
    group.add_argument('--put'); group.add_argument('--sha256')
    q = sub.add_parser('ingest', help='Ingest a contribution; incoming reviews remain foreign evidence.')
    q.add_argument('project'); q.add_argument('--submission', required=True)
    q.add_argument('--operation', required=True); q.add_argument('--expected-head')
    q.add_argument('--metrics', action='store_true', help='Return aggregate local I/O timing and counts for this invocation.')
    q = sub.add_parser('content-audit', help='Recursively verify one entire directory or package content scope, including nested containers.')
    q.add_argument('scope'); q.add_argument('--max-bytes', type=int, default=64*1024**3); q.add_argument('--max-members',type=int,default=1000000); q.add_argument('--max-depth',type=int,default=16)
    q = sub.add_parser('predict', help='Predict intake and proposed local-review states without opening incoming assets or publishing.')
    q.add_argument('project'); q.add_argument('--submission', required=True)
    q.add_argument('--reviews', help='JSON array of proposed local reviews with already declared report identities; no admission.')
    q.add_argument('--expected-head')
    q = sub.add_parser('prepare-archive', help='Prepare declared archive metadata and reports in one bounded external checklist; never admit evidence or reviews.')
    q.add_argument('project'); q.add_argument('--submission', required=True)
    q.add_argument('--review-batch', required=True); q.add_argument('--revision', action='append', default=[])
    q.add_argument('--expected-head', required=True); q.add_argument('--out', required=True)
    q = sub.add_parser('adopt', help='Adopt explicitly mapped external research and evidence; retain original source bytes.')
    q.add_argument('project'); q.add_argument('--source', required=True); q.add_argument('--inventory', required=True)
    q.add_argument('--mapping', required=True); q.add_argument('--operation', required=True); q.add_argument('--expected-head')
    q = sub.add_parser('review', help='Record an actual explicit review of exact research materials.')
    q.add_argument('project'); q.add_argument('--review', required=True)
    q.add_argument('--report'); q.add_argument('--operation'); q.add_argument('--expected-head')
    q.add_argument('--check-only', action='store_true', help='Read-only portability check of this review and its actual report; never submit it or verify research claims.')
    q = sub.add_parser('review-batch', help='Check all review/report items, then explicitly admit one exact batch atomically.')
    q.add_argument('project'); q.add_argument('--submission', required=True)
    q.add_argument('--operation'); q.add_argument('--expected-head'); q.add_argument('--expected-batch')
    q.add_argument('--check-only', action='store_true', help='Prepare the whole batch without writing CAS, HEAD or operations; return its exact batch hash.')
    q = sub.add_parser('select', help='Explicitly select a known revision after inspecting a branch conflict.')
    q.add_argument('project'); q.add_argument('--id', required=True); q.add_argument('--revision', required=True)
    q.add_argument('--operation', required=True); q.add_argument('--expected-head')
    q = sub.add_parser('record', help='Read one exact record and its current evidence status.')
    q.add_argument('project'); q.add_argument('--revision', required=True)
    q = sub.add_parser('records', help='Read a bounded exact revision set from one fixed project view.')
    q.add_argument('project'); q.add_argument('--revision', action='append', required=True)
    q.add_argument('--expected-head', required=True)
    q = sub.add_parser('query', help='Retrieve project evidence; this command does not invent answers.')
    q.add_argument('project'); q.add_argument('--text', default='')
    q.add_argument('--kind'); q.add_argument('--state'); q.add_argument('--limit', type=int, default=20)
    q.add_argument('--offset', type=int, default=0, help='Navigation offset within the fixed query snapshot.')
    q.add_argument('--brief', action='store_true', help='Return explicit bounded summaries; use each read_command for its full record.')
    q = sub.add_parser('continuity-plan', help='Screen exact records for continuation and historical omissions without deciding semantics.')
    q.add_argument('project'); q.add_argument('--out', required=True)
    q.add_argument('--since'); q.add_argument('--scope', choices=['all', 'affected'], default='all')
    q.add_argument('--seed', action='append', default=[]); q.add_argument('--screening-limit', type=int, default=12)
    q = sub.add_parser('continuity-template', help='Write pending dispositions for an exact continuation plan.')
    q.add_argument('project'); q.add_argument('--plan', required=True); q.add_argument('--out', required=True)
    q = sub.add_parser('continuity-check', help='Verify continuation coverage and saved edges, not the truth of research claims.')
    q.add_argument('project'); q.add_argument('--plan', required=True); q.add_argument('--decisions', required=True); q.add_argument('--out', required=True)
    q = sub.add_parser('routes', help='Read explicit predecessors, successors and unresolved route ordering.')
    q.add_argument('project'); q.add_argument('--revision')
    q = sub.add_parser('map', help='Render the complete research map from one fixed snapshot.')
    q.add_argument('project'); q.add_argument('--out')
    q.add_argument('--format', choices=['obsidian', 'markdown'], default='obsidian')
    q.add_argument('--formula-replacements', help='Reviewed exact source/TeX JSON pairs for local presentation only.')
    q = sub.add_parser('prepare-report', help='Copy an inspected report as exact bytes into a new external file and return its hash; no review admission.')
    q.add_argument('--source', required=True); q.add_argument('--out', required=True)
    q = sub.add_parser('export-preflight', help='Read metadata once and save all portability findings and affected strong dependencies to a new external JSON report; no evidence replay or project writes.')
    q.add_argument('project'); q.add_argument('--out', required=True)
    q.add_argument('--asset-budget', type=int, default=460000000)
    q.add_argument('--priority-file')
    q.add_argument('--portable-history', help='Explicit source-snapshot-bound reviewed portable replacement plan.')
    q = sub.add_parser('delivery-plan', help='Prepare an exact external plan after metadata and selected-content checks; never execute research code.')
    q.add_argument('project'); q.add_argument('--out', required=True)
    q.add_argument('--asset-budget', type=int); q.add_argument('--max-zip-bytes', type=int)
    q.add_argument('--priority-file'); q.add_argument('--portable-history'); q.add_argument('--contract')
    q.add_argument('--reuse-plan', help='Revalidate a previous plan on this project without changing its declared delivery options.')
    q.add_argument('--expect-plan', help='Exact previous plan SHA-256 when using --reuse-plan.')
    q = sub.add_parser('export', help='Export a reviewed handoff with explicit evidence availability.')
    q.add_argument('project'); q.add_argument('--out', required=True)
    q.add_argument('--asset-budget', type=int, default=460000000)
    q.add_argument('--max-zip-bytes',type=int,default=512000000)
    q.add_argument('--priority-file',help='Optional asset SHA to importance, future_research and reason mapping.')
    q.add_argument('--portable-history', help='Explicit source-snapshot-bound reviewed portable replacement plan.')
    q.add_argument('--contract', help='Explicit delivery material contract; never confers review.')
    q.add_argument('--plan', help='Previously prepared exact delivery plan.')
    q.add_argument('--expect-plan', help='SHA-256 of the explicitly selected plan.')
    q = sub.add_parser('finish', help='Audit and prepare one snapshot-bound research handoff in a new output directory.')
    q.add_argument('project'); q.add_argument('--out', required=True)
    q.add_argument('--max-asset-bytes', type=int, default=460000000); q.add_argument('--expected-head')
    q.add_argument('--formula-replacements', help='Reviewed exact source/TeX JSON pairs for the local map.')
    q.add_argument('--contract'); q.add_argument('--plan'); q.add_argument('--expect-plan')
    q = sub.add_parser('restore-delivery', help='Restore one declared material group using only the final package; execute no code.')
    q.add_argument('bundle'); q.add_argument('--group', required=True); q.add_argument('--out', required=True)
    q = sub.add_parser('replay-delivery', help='Explicitly run inspected restored code under bounded Windows process supervision.')
    q.add_argument('restored'); q.add_argument('--out', required=True); q.add_argument('--expect-receipt', required=True)
    q.add_argument('--execute', action='store_true'); q.add_argument('--timeout', type=int, default=60)
    q = sub.add_parser('verify-bundle', help='Check package bytes and review bindings, not the truth of research claims.')
    q.add_argument('bundle')
    q = sub.add_parser('import', help='Import a returned handoff without trusting its review decisions.')
    q.add_argument('project'); q.add_argument('bundle'); q.add_argument('--origin', required=True)
    q.add_argument('--operation', required=True); q.add_argument('--expected-head')
    q = sub.add_parser('recover', help='Finish an exact prepared operation without overwriting a newer commit.')
    q.add_argument('project'); q.add_argument('--operation', required=True)
    q = sub.add_parser('cold-move', help='Move one verified object to a separately registered storage location.')
    q.add_argument('project'); q.add_argument('--sha256', required=True); q.add_argument('--cold-root', required=True)
    q = sub.add_parser('replay', help='Run inspected research code in a cleaned external reproduction directory.')
    q.add_argument('project'); q.add_argument('--spec', required=True)
    q.add_argument('--timeout', type=int, default=1200); q.add_argument('--temp-root')
    q.add_argument('--operation')
    for name in ['replay-status', 'replay-cancel']:
        q = sub.add_parser(name, help='Inspect or request cancellation of one exact registered reproduction job.')
        q.add_argument('project'); q.add_argument('--operation', required=True)
    q = sub.add_parser('workflow', help='Observe one full workflow across commands, review and waiting; never stop research.')
    q.add_argument('action', choices=['begin', 'checkpoint', 'end', 'watch'])
    q.add_argument('session'); q.add_argument('--phase'); q.add_argument('--note', default='')
    return p


def project_view(store):
    head = store.head()
    snapshot = store.snapshot(head)
    records = store.records(snapshot)
    reviews = store.reviews(snapshot)
    states = assess(records, reviews, snapshot['selected'], excluded_records=snapshot.get('excluded_records', []))
    return head, snapshot, records, reviews, states


def prepare_replay(spec, timeout):
    if not isinstance(spec, dict) or set(spec) != {'files', 'argv', 'capture', 'purpose'}:
        raise CRSError('replay_spec_invalid', 'Replay specification requires files, argv, capture and purpose.')
    if not isinstance(spec['argv'], list) or not spec['argv'] or not all(isinstance(x, str) for x in spec['argv']):
        raise CRSError('replay_argv_invalid', 'Supply an explicit argument list; shell command text is not supported.')
    if not 1 <= timeout <= 86400 or not isinstance(spec['purpose'], str) or not spec['purpose'].strip():
        raise CRSError('replay_limit_invalid', 'Declare a purpose and a timeout between one second and one day.')
    if not isinstance(spec['capture'], list) or not all(isinstance(name, str) and name for name in spec['capture']):
        raise CRSError('capture_spec_invalid', 'Capture must list explicit nonempty relative filenames.')
    capture_names = set()
    for name in spec['capture']:
        relative = Path(name)
        normalized = name.replace('\\', '/')
        key = normalized.casefold()
        if (relative.is_absolute() or ':' in name or '\x00' in name or
                any(part in {'', '.', '..'} for part in normalized.split('/')) or
                key in capture_names or key in {'stdout', 'stderr', '.crs-stdout', '.crs-stderr'}):
            raise CRSError('capture_spec_invalid', 'Capture filenames must be unique safe relative paths, distinct from reserved log names.', {'name': name})
        capture_names.add(key)

    argv = [sys.executable if x == '{python}' else x for x in spec['argv']]
    # Detect a directly named Python interpreter/launcher. Other compilers may
    # legitimately use -O; arguments after a script or -c/-m belong to that code.
    import re
    executable = argv[0].replace('\\', '/').rsplit('/', 1)[-1].casefold()
    direct_python = bool(re.fullmatch(r'(?:pythonw?(?:\d+(?:\.\d+)*)?|pypy(?:\d+(?:\.\d+)*)?|py)(?:\.exe)?', executable))
    if direct_python:
        index = 1
        while index < len(argv):
            token = argv[index]
            if token in {'--', '-'} or not token.startswith('-'):
                break
            if token.startswith('--'):
                if token == '--check-hash-based-pycs':
                    index += 1
                index += 1
                continue
            script_started = False
            for position, flag in enumerate(token[1:], 1):
                if flag == 'O':
                    raise CRSError('python_assertions_disabled', 'Python -O/-OO or combined optimization flags can skip certificate assertions; run without optimization.', {'argument': token})
                if flag in {'c', 'm'}:
                    script_started = True
                    break
                if flag in {'W', 'X'}:
                    if position == len(token) - 1:
                        index += 1
                    break
            if script_started:
                break
            index += 1
    environment = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}
    inherited_optimization = environment.pop('PYTHONOPTIMIZE', None) is not None
    assertion_policy = {'inherited_pythonoptimize_removed': inherited_optimization,
                        'direct_python_detected': direct_python,
                        'direct_python_optimization_flags_allowed': False,
                        'scope': 'Direct Python argv is checked; inspected code and nested processes remain the caller responsibility.'}
    return argv, environment, assertion_policy


def run_replay(store, spec, timeout, temp_root, job=None):
    argv, environment, assertion_policy = prepare_replay(spec, timeout)
    if job is not None:
        import crs_replay
        assertion_policy = job["assertion_policy"]
    started = time.monotonic()
    captured = {}
    capture_errors = []
    context = replay_workspace(store, spec['files'], temp_root) if job is None else crs_replay.workspace(store, job)
    with context as work:
        stdout = work / '.crs-stdout'
        stderr = work / '.crs-stderr'
        timed_out = cancelled = False
        process_tree_policy = 'direct synchronous process'
        with stdout.open('xb') as out, stderr.open('xb') as err:
            if job is not None:
                process_result = crs_replay.run_process(store, job, argv, work, environment, out, err)
                code, timed_out, cancelled = process_result['returncode'], process_result['timed_out'], process_result['cancelled']
                process_tree_policy = process_result['process_tree_policy']
            else:
                try:
                    result = subprocess.run(argv, cwd=work, env=environment, shell=False, stdout=out, stderr=err, timeout=timeout)
                    code = result.returncode
                except subprocess.TimeoutExpired:
                    code = None
                    timed_out = True
        captured['stdout'] = store.put_file(stdout)
        captured['stderr'] = store.put_file(stderr)
        for name in spec['capture']:
            try:
                target = ordinary(work / name).resolve()
                if not target.is_relative_to(work):
                    capture_errors.append({'name': name, 'code': 'capture_invalid', 'reason': 'Output leaves the owned reproduction directory.'})
                    continue
                if not target.exists():
                    capture_errors.append({'name': name, 'code': 'capture_missing', 'reason': 'Declared output was not produced.'})
                    continue
                if not target.is_file():
                    capture_errors.append({'name': name, 'code': 'capture_invalid', 'reason': 'Declared output is not an ordinary file.'})
                    continue
            except (CRSError, OSError) as error:
                capture_errors.append({'name': name, 'code': getattr(error, 'code', 'capture_invalid'), 'reason': str(error)})
                continue
            captured[name] = store.put_file(target)
        ok = code == 0 and not timed_out and not cancelled and not capture_errors
        report = {'schema': 'crs-replay/v1', 'purpose': spec['purpose'], 'inputs': spec['files'],
                  'argv': spec['argv'], 'outputs': captured, 'returncode': code, 'timed_out': timed_out,
                  'ok': ok, 'capture_errors': capture_errors, 'assertion_policy': assertion_policy,
                  'cancelled': cancelled, 'process_tree_policy': process_tree_policy,
                  'elapsed_seconds': time.monotonic() - started,
                  'claim_truth_inferred': False, 'execution_boundary': 'inspected trusted local code; not an operating-system sandbox'}
        report_sha = store.put_json(report)
        # Missing output must not skip later existing outputs or the failure
        # report. Reopen all preserved bytes before cleaning temporary copies.
        for sha in [report_sha, *captured.values()]:
            store.resolve(sha)
    return {'ok': ok, 'report': report_sha, 'returncode': code, 'timed_out': timed_out,
            'cancelled': cancelled, 'process_tree_policy': process_tree_policy,
            'capture_errors': capture_errors, 'assertion_policy': assertion_policy,
            'temporary_directory_removed': not work.exists(), 'outputs': captured}


def query_summary(record):
    """A bounded navigation projection, never a replacement for a full record."""
    truncated = []
    def clip(value, label, limit=400):
        if len(value) > limit:
            truncated.append(label)
            return value[:limit] + '…'
        return value
    result = {key: clip(record[key], key, 800 if key in {'statement', 'action', 'feedback'} else 400)
              for key in ['id', 'kind', 'title', 'epistemic', 'statement', 'scope', 'action', 'feedback']}
    for key in ['assumptions', 'limitations']:
        result[key + '_count'] = len(record[key])
        result[key] = [clip(value, key + '[' + str(index) + ']', 240) for index, value in enumerate(record[key][:3])]
        if len(record[key]) > 3:
            truncated.append(key)
    result['reopen_count'] = len(record['reopen'])
    result['reopen'] = [clip(value, 'reopen[' + str(index) + ']', 240) for index, value in enumerate(record['reopen'][:3])]
    if len(record['reopen']) > 3: truncated.append('reopen')
    result['evidence_count'] = len(record['evidence'])
    result['evidence_preview'] = [{key: clip(item[key], 'evidence[' + str(index) + '].' + key, 280)
                                   for key in ['name', 'role', 'summary', 'locator']} | {'sha256': item['sha256'], 'bytes': item['bytes']}
                                  for index, item in enumerate(record['evidence'][:3])]
    result['dependency_count'] = len(record['dependencies'])
    result['dependency_preview'] = [{key: item[key] for key in ['id', 'revision', 'relation']} for item in record['dependencies'][:4]]
    result['conditional_count'] = len(record['conditional_on'])
    result['conditional_on'] = record['conditional_on'][:4]
    for key, limit in [('evidence', 3), ('dependencies', 4), ('conditional_on', 4)]:
        if len(record[key]) > limit:
            truncated.append(key)
    if record['kind'] == 'objective':
        result['objective_identity'] = objective_identity(record)
    result['truncated_fields'] = sorted(set(truncated))
    return result


def execute(args):
    if args.command == 'continuity-check':
        store = Store(args.project)
        with store.read_io():
            return _execute(args, store)
    return _execute(args)


def _execute(args, store=None):
    def save_json(path, value):
        target = ordinary(Path(path).absolute())
        with target.open('xb') as out:
            out.write(canonical(value))
        return {'path': str(target), 'sha256': digest(canonical(value))}
    if args.command == 'workflow':
        import crs_workflow
        if args.action == 'begin':
            return crs_workflow.begin(args.session, args.phase or '开始', args.note)
        if args.action == 'checkpoint':
            if not args.phase:
                raise CRSError('workflow_phase_required', 'A checkpoint needs its actual phase.')
            return crs_workflow.checkpoint(args.session, args.phase, args.note)
        if args.action == 'end':
            return crs_workflow.end(args.session, args.phase or '完成', args.note)
        return crs_workflow.watch(args.session)
    if args.command == 'prepare-report':
        source = ordinary(Path(args.source).absolute())
        output = ordinary(Path(args.out).absolute())
        with source.open('rb') as handle:
            raw = handle.read(64 * 1024 * 1024 + 1)
        if len(raw) > 64 * 1024 * 1024:
            raise CRSError('batch_report_limit', 'Report preparation accepts at most 64 MiB.')
        raw.decode('utf-8')
        if not raw.strip():
            raise CRSError('empty_review_report', 'A report must contain substantive text.')
        with output.open('xb') as handle:
            handle.write(raw)
        stored = output.read_bytes()
        if stored != raw:
            raise CRSError('report_mismatch', 'Prepared report differs from the supplied bytes.')
        return {'output': str(output), 'sha256': digest(stored), 'bytes': len(stored),
                'review_admitted': False, 'source_rewritten': False}
    if args.command == 'template':
        if args.type == 'record':
            value = record_template(args.kind)
        elif args.type == 'review':
            value = review_template()
        elif args.type == 'review-batch':
            value = {'schema': 'crs-review-batch/v1', 'items': [{'review': review_template(), 'report': None}]}
        elif args.type == 'adoption':
            if not args.inventory:
                raise CRSError('inventory_required', 'Choose a frozen source inventory for the adoption template.')
            from crs_adopt import adoption_template
            value = adoption_template(load(args.inventory))
        else:
            value = {'schema': 'crs-intake/v1', 'origin': '', 'records': [], 'assets': {}, 'foreign_reviews': []}
        return save_json(args.out, value) if args.out else value
    if args.command == 'inventory':
        from crs_adopt import scan_source
        inventory = scan_source(Path(args.source), args.max_files, args.max_bytes, args.max_metadata_bytes)
        return {**save_json(args.out, inventory), 'files': len(inventory['files']), 'bytes': inventory['total_bytes'], 'skipped': inventory['skipped'], 'source_limits': inventory['source_limits'], 'source_usage': inventory['source_usage']}
    if args.command == 'restore-delivery':
        from crs_recipient import restore_delivery
        return restore_delivery(Path(args.bundle), args.group, Path(args.out))
    if args.command == 'replay-delivery':
        from crs_recipient import replay_delivery
        return replay_delivery(Path(args.restored), Path(args.out), args.expect_receipt, args.execute, args.timeout)
    if args.command == 'verify-bundle':
        from crs_exchange import verify_bundle
        return verify_bundle(Path(args.bundle))
    if args.command == 'content-audit':
        from crs_unique import audit_content
        report = audit_content(args.scope,max_bytes=args.max_bytes,max_members=args.max_members,max_depth=args.max_depth)
        return {**{k:v for k,v in report.items() if k not in {'duplicates','issues'}},'duplicates_preview':report['duplicates'][:10],'issues_preview':report['issues'][:10],'issue_count':len(report['issues'])}
    store = store or Store(args.project)
    if args.project_root:
        from crs_unique import project_scope
        store.project_root = project_scope(store, args.project_root)
    if args.command == 'init':
        if args.receive:
            if args.scope is not None:
                raise CRSError('objective_input_conflict', '--receive adopts the source question; do not supply --scope.')
            return store.initialize(args.title, None)
        if args.objective_file:
            if args.scope is not None:
                raise CRSError('objective_input_conflict', '--objective-file includes its own scope; do not override it.')
            objective = load(args.objective_file)
            if not objective_complete(objective):
                raise CRSError('objective_complete_required', '--objective-file requires a complete objective record with project_objective.')
        else:
            if not args.scope:
                raise CRSError('objective_scope_required', 'An incomplete --objective needs --scope; the remaining constituents stay explicitly unknown.')
            objective = record_template('objective')
            objective.pop('project_objective')
            objective.update(title=args.title, statement=args.objective, scope=args.scope)
        return store.initialize(args.title, objective)
    store.head()
    if args.command in {'continuity-plan', 'continuity-template', 'continuity-check'}:
        from crs_continuity import make_plan, validate_plan, decisions_template, check, write_external
        if args.command == 'continuity-plan':
            plan = make_plan(store, since=args.since, scope=args.scope, seeds=args.seed, screening_limit=args.screening_limit)
            if store.head() != plan['snapshot']:
                raise CRSError('continuity_head_changed', 'Project changed during screening; generate a fresh plan.')
            return {**write_external(store, args.out, plan), 'snapshot': plan['snapshot'], 'scope': plan['scope'],
                    'records': len(plan['record_revisions']), 'candidate_pairs': len(plan['pairs']), 'screening': plan['screening']}
        plan = load(args.plan)
        if args.command == 'continuity-template':
            plan = validate_plan(store, plan)
            return {**write_external(store, args.out, decisions_template(plan)), 'status': 'pending'}
        receipt = check(store, plan, load(args.decisions))
        return {**write_external(store, args.out, receipt), 'scope': receipt['scope'],
                'status': receipt['continuity_status'], 'scoped_records': receipt['scoped_records'],
                'confirmed_relationships': len(receipt['confirmed_relationships']),
                'pending_records': len(receipt['pending_records']), 'pending_pairs': len(receipt['pending_pairs']),
                'disputed_pairs': len(receipt['disputed_pairs']), 'research_review_performed': False}
    if args.command == 'bind-objective':
        return store.bind_objective(load(args.objective_file), args.origin, args.operation, args.expected_head)
    if args.command == 'adopt':
        from crs_adopt import adopt_source
        return adopt_source(store, Path(args.source), load(args.inventory), load(args.mapping), args.operation, args.expected_head)
    if args.command == 'asset':
        sha = store.put_file(args.put) if args.put else args.sha256
        path = store.resolve(sha)
        return {'sha256': sha, 'bytes': path.stat().st_size, 'path': str(path)}
    if args.command == 'prepare-archive':
        from crs_preparation import prepare_archive, load_input
        from crs_continuity import write_external
        value = prepare_archive(store, load_input(args.submission), load_input(args.review_batch),
                                Path(args.review_batch).absolute().parent, args.revision, args.expected_head)
        value['next_arguments'] = {
            'ingest': ['ingest', str(store.root), '--submission', str(Path(args.submission).absolute()), '--expected-head', value['snapshot'], '--operation', 'CHOOSE_NEW_OPERATION_ID'],
            'review_preflight_after_ingest': ['review-batch', str(store.root), '--submission', str(Path(args.review_batch).absolute()), '--check-only'],
            'final_records_after_commit': ['records', str(store.root), '--expected-head', 'USE_COMMITTED_HEAD'] + [part for row in value['prediction']['incoming'] for part in ['--revision', row['revision']]],
            'expanded_response': ['--max-response-bytes', '1048576', '--response-reason', 'Read the explicitly selected archive records.']}
        saved = write_external(store, args.out, value)
        return {**saved, 'terminal_state': 'success', 'snapshot': value['snapshot'], 'preparation_only': True, 'committed': False,
                'ready_for_declared_acceptance': value['ready_for_declared_acceptance'],
                'prediction_complete': value['prediction_complete'], 'not_accepted_count': len(value['not_accepted']),
                'report_finding_count': len(value['report_preparation']['findings']),
                'old_record_count': len(value['old_records']), 'full_checklist': 'Read the new external file; inspect gaps before ingestion.'}
    if args.command == 'predict':
        from crs_prediction import predict
        return predict(store, load(args.submission), load(args.reviews) if args.reviews else [], args.expected_head)
    if args.command == 'ingest':
        request = load(args.submission)
        if not isinstance(request, dict) or set(request) != {'schema', 'origin', 'records', 'assets', 'foreign_reviews'} or request['schema'] != 'crs-intake/v1':
            raise CRSError('intake_invalid', 'Use the crs-intake/v1 submission format.')
        base = Path(args.submission).absolute().parent
        assets = {sha: ordinary(base / name) for sha, name in request['assets'].items()}
        from crs_metrics import Metrics
        with Metrics(enabled=args.metrics) as measurements:
            result = store.ingest(request['records'], assets, request['origin'], args.operation, args.expected_head, request['foreign_reviews'])
        if args.metrics:
            result['metrics'] = measurements.report()
        return result
    if args.command == 'review':
        review = load(args.review)
        if args.check_only:
            from crs_exchange import check_review_portability
            return check_review_portability(store, review, args.report)
        if not args.operation:
            raise CRSError('operation_required', 'Review submission requires --operation; use --check-only for a read-only portability check.')
        if args.report:
            sha = store.put_file(args.report)
            if review.get('report') and review['report'] != sha:
                raise CRSError('report_mismatch', 'The review points to different report bytes.')
            review['report'] = sha
        return store.review(review, args.operation, args.expected_head)
    if args.command == 'review-batch':
        from crs_batch import review_batch
        submission = Path(args.submission).absolute()
        return review_batch(store, load(submission), submission.parent,
                            operation_id=args.operation, expected_head=args.expected_head,
                            expected_batch=args.expected_batch, check_only=args.check_only)
    if args.command == 'select':
        return store.select(args.id, args.revision, args.operation, args.expected_head)
    if args.command == 'recover':
        import crs_replay
        return crs_replay.recover(store, args.operation) if crs_replay.has_job(store, args.operation) else store.recover(args.operation)
    if args.command in {'replay-status', 'replay-cancel'}:
        import crs_replay
        return crs_replay.status(store, args.operation) if args.command == 'replay-status' else crs_replay.cancel(store, args.operation)
    if args.command == 'audit':
        return store.audit()
    if args.command == 'cold-move':
        return store.cold_move(args.sha256, args.cold_root)
    if args.command == 'replay':
        from crs_replay import start
        return start(store, load(args.spec), args.timeout, args.temp_root, args.operation)
    if args.command == 'export-preflight':
        from crs_exchange import export_preflight
        return export_preflight(store, Path(args.out), args.asset_budget, parse_json(Path(args.priority_file).read_bytes()) if args.priority_file else None, portable_history=parse_json(Path(args.portable_history).read_bytes()) if args.portable_history else None)
    if args.command == 'delivery-plan':
        from crs_export_plan import write_plan, refresh_plan
        if args.reuse_plan:
            if any(x is not None for x in [args.asset_budget, args.max_zip_bytes, args.priority_file, args.portable_history, args.contract]):
                raise CRSError('delivery_plan_options', 'A reused plan owns its declared options; create a fresh plan to change them.')
            return refresh_plan(store, Path(args.out), load(args.reuse_plan), args.expect_plan)
        if args.expect_plan is not None:
            raise CRSError('delivery_plan_options', '--expect-plan requires --reuse-plan for this command.')
        return write_plan(store, Path(args.out), max_asset_bytes=args.asset_budget if args.asset_budget is not None else 460000000, max_zip_bytes=args.max_zip_bytes if args.max_zip_bytes is not None else 512000000,
                          priorities=load(args.priority_file) if args.priority_file else None,
                          portable_history=load(args.portable_history) if args.portable_history else None,
                          contract=load(args.contract) if args.contract else None)
    if args.command == 'export':
        from crs_exchange import export_bundle
        return export_bundle(store, Path(args.out), args.asset_budget, max_zip_bytes=args.max_zip_bytes, priorities=parse_json(Path(args.priority_file).read_bytes()) if args.priority_file else None, portable_history=parse_json(Path(args.portable_history).read_bytes()) if args.portable_history else None, contract=load(args.contract) if args.contract else None, plan=load(args.plan) if args.plan else None, expected_plan=args.expect_plan)
    if args.command == 'finish':
        from crs_finish import finish
        return finish(store, Path(args.out), max_asset_bytes=args.max_asset_bytes,
                      expected_head=args.expected_head, formula_replacements=args.formula_replacements,
                      contract=load(args.contract) if args.contract else None,
                      plan=load(args.plan) if args.plan else None, expected_plan=args.expect_plan)
    if args.command == 'import':
        from crs_exchange import import_bundle
        return import_bundle(store, Path(args.bundle), args.origin, args.operation, args.expected_head)
    if args.command == 'records':
        if not 1 <= len(args.revision) <= 100 or len(set(args.revision)) != len(args.revision):
            raise CRSError('revision_batch_invalid', 'Request 1..100 distinct exact revisions.')
        if store.head() != args.expected_head:
            raise CRSError('stale_snapshot', 'The requested fixed snapshot is no longer current.')
    head, snapshot, records, reviews, states = project_view(store)
    if args.command == 'records' and head != args.expected_head:
        raise CRSError('stale_snapshot', 'The project changed before the batch was loaded.')
    origins = {}
    for item in snapshot['origins']:
        origins.setdefault(item['record'], []).append(item['source'])
    if args.command == 'status':
        return {'snapshot': head, 'title': snapshot['title'], 'objective': snapshot['objective'], 'objective_identity': snapshot['objective_identity'], 'records': len(records), 'selected': snapshot['selected'] if args.full else dict(sorted(snapshot['selected'].items())[:10]),
                'selected_count': len(snapshot['selected']), 'selected_complete': args.full or len(snapshot['selected']) <= 10, 'reviewed_exportable': sum(bool(x['exportable']) for x in states.values()), 'currently_usable': sum(bool(x['usable']) for x in states.values()), 'foreign_reviews': len(snapshot['foreign_reviews'])}
    if args.command == 'routes':
        from crs_continuity import graph
        route = graph(records)
        if args.revision:
            if args.revision not in records:
                raise CRSError('record_unknown', 'This revision is not in the fixed project snapshot.')
            route['edges'] = [edge for edge in route['edges'] if args.revision in (edge['predecessor'], edge['successor'])]
            keep = {args.revision} | {value for edge in route['edges'] for value in (edge['predecessor'], edge['successor'])}
            route['order'] = [sha for sha in route['order'] if sha in keep]
            route['order_unresolved'] = [sha for sha in route['order_unresolved'] if sha in keep]
        return {'snapshot': head, **route, 'records': [{'revision': sha, 'title': records[sha]['title'], 'status': states[sha],
                'read_command': [sys.executable, '-B', str(Path(__file__).resolve()), 'record', str(store.root), '--revision', sha]} for sha in route['order']]}
    if args.command == 'records':
        if any(sha not in records for sha in args.revision):
            raise CRSError('record_unknown', 'A requested revision is absent from this snapshot.')
        return {'snapshot': head, 'records': [
            {'revision': sha, 'record': records[sha], 'status': states[sha],
             'reported_sources': origins.get(sha, [])} for sha in args.revision],
            'project_objective_identity': snapshot['objective_identity'],
            'source_identity_authenticated': False}
    if args.command == 'record':
        if args.revision not in records:
            raise CRSError('record_unknown', 'This revision is not in the fixed project snapshot.')
        return {'snapshot': head, 'revision': args.revision, 'record': records[args.revision], 'objective_identity': objective_identity(records[args.revision]) if records[args.revision]['kind'] == 'objective' else None, 'project_objective_identity': snapshot['objective_identity'], 'status': states[args.revision], 'reported_sources': origins.get(args.revision, []), 'source_identity_authenticated': False}
    if args.command == 'query':
        if not 1 <= args.limit <= 100 or args.offset < 0:
            raise CRSError('query_limit_invalid', 'Query limit must be between one and one hundred.')
        words = args.text.casefold().split()
        matches = []
        for sha, record in records.items():
            state = states[sha]
            body = canonical([record, origins.get(sha, [])]).decode('utf-8').casefold()
            if args.kind and record['kind'] != args.kind:
                continue
            if args.state and args.state not in {state['review'], state['effect'], state['effective_epistemic']}:
                continue
            score = sum(word in body for word in words)
            if words and not score:
                continue
            matches.append({'revision': sha, 'record': record, 'status': state, 'reported_sources': origins.get(sha, []),
                            'matched_terms': score, 'current_selection': snapshot['selected'].get(record['id']) == sha})
        matches.sort(key=lambda x: (-x['matched_terms'], not x['current_selection'], x['record']['title'], x['revision']))
        returned = matches[args.offset:args.offset + args.limit]
        from crs_exchange import presentation_correction_context
        for match in returned:
            if match["record"]["kind"] != "objective":
                continue
            notes = presentation_correction_context(records, states, reviews, match["revision"])
            for note in notes:
                note["read_command"] = [sys.executable, '-B', str(Path(__file__).resolve()),
                                        'record', str(store.root), '--revision', note["revision"]]
                for reviewed in note["reviews"]:
                    reviewed["read_command"] = [sys.executable, '-B', str(Path(__file__).resolve()),
                                                'asset', str(store.root), '--sha256', reviewed["revision"]]
                    reviewed["report_command"] = [sys.executable, '-B', str(Path(__file__).resolve()),
                                                  'asset', str(store.root), '--sha256', reviewed["report"]]
            match["presentation_corrections"] = notes
        if args.brief:
            returned = [{key: row[key] for key in ['revision', 'status', 'matched_terms', 'current_selection']} |
                        {'summary': query_summary(row['record']), 'record_complete': False,
                         'reported_sources': row['reported_sources'][:5], 'reported_source_count': len(row['reported_sources']),
                         'reported_sources_complete': len(row['reported_sources']) <= 5,
                         **({'presentation_corrections': row['presentation_corrections']} if 'presentation_corrections' in row else {}),
                         'source_identity_authenticated': False,
                         'read_command': [sys.executable, '-B', str(Path(__file__).resolve()), 'record', str(store.root), '--revision', row['revision']]}
                        for row in returned]
        response = {'snapshot': head, 'matches': returned, 'total': len(matches), 'unknown': not matches,
                    'offset': args.offset, 'next_offset': args.offset + len(returned) if args.offset + len(returned) < len(matches) else None,
                    'answer_boundary': 'Only these retrieved project records support project-history answers.'}
        if args.brief:
            response.update(projection='summary', answer_boundary='These are bounded navigation summaries. Read exact records before answering claims, conditions or dependency questions; a match is not semantic truth.')
        return response
    if args.command == 'map':
        if args.out:
            from crs_unique import external_target
            external_target(store,args.out)
        from crs_exchange import render_map
        output_base = Path(args.out).absolute().parent if args.out else store.root
        resolved_links = {}
        def object_link(sha):
            if sha in resolved_links:
                return resolved_links[sha]
            try:
                path = store.resolve(sha)
            except CRSError as error:
                if error.code != 'asset_missing':
                    raise
                resolved_links[sha] = None
                return None
            try:
                resolved_links[sha] = Path(os.path.relpath(path, output_base)).as_posix()
            except ValueError:
                resolved_links[sha] = path.as_posix()
            return resolved_links[sha]
        from crs_formula import Presentation
        presentation = Presentation(args.formula_replacements) if args.out and args.format == 'obsidian' else None
        if args.formula_replacements and presentation is None:
            raise CRSError('map_formula_output_required', 'Reviewed presentation replacements require local Obsidian --out.')
        body = render_map(records, reviews, snapshot['selected'], snapshot['title'], presentation=presentation, object_path=object_link, origins=snapshot['origins'], excluded_records=snapshot.get('excluded_records', []), objective=snapshot['objective'], objective_binding=snapshot.get('objective_binding'))
        if args.out and args.format == 'obsidian':
            from crs_obsidian import write_map
            return dict(write_map(store, args.out, body, head, resolved_links), records=len(records))
        if args.out:
            target = ordinary(Path(args.out).absolute())
            # Views are derived outside the content store, never an authority file.
            if target.resolve().is_relative_to(store.root):
                raise CRSError('map_output_inside_project', 'Write the readable map beside the formal project, not into its immutable asset store.')
            with target.open('x', encoding='utf-8', newline='\n') as out:
                out.write(body)
            return {'snapshot': head, 'map': str(target), 'records': len(records)}
        return {'snapshot': head, 'markdown': body}
    raise CRSError('command_unknown', 'Unsupported command.')


@public_main
def main():
    # CLI pipes always carry UTF-8, independent of the inherited Windows code page.
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='strict')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='backslashreplace')
    args = parser().parse_args()
    configure_output(args)
    started = time.monotonic()
    watcher = None
    workflow_run = None
    workflow_finished = False
    observation_warnings = []
    def observation_warning(phase, error):
        warning = {'phase': phase, 'code': error_response(error)['code'],
                   'message': 'Workflow observation is unavailable; the business command is not retried.', 'error_type': 'ObservationError'}
        observation_warnings.append(warning)
        try:
            print('CRS observation warning (' + phase + '): ' + warning['message'] +
                  ' The business command is not retried; use its actual result. Complete workflow timing is unavailable.',
                  file=sys.stderr, flush=True)
        except (OSError, ValueError):
            pass
    def notify_workflow(value):
        if value.get('notification_emitted'):
            print(value['attention']['message'], file=sys.stderr, flush=True)
    def observe():
        try:
            if workflow_run:
                import crs_workflow
                notify_workflow(crs_workflow.check(args.workflow))
            else:
                print('CRS operation exceeded 20 minutes; inspect required computation, I/O, review and repair time without lowering evidence requirements.', file=sys.stderr, flush=True)
        except Exception as error:
            observation_warning('threshold', error)
    code = 0
    try:
        if args.workflow:
            if args.command == 'workflow':
                raise CRSError('workflow_nested_tracking', 'Use workflow actions directly, without --workflow.')
            try:
                import crs_workflow
                workflow_run = crs_workflow.command_started(args.workflow, args.command)
                notify_workflow(workflow_run)
            except Exception as error:
                workflow_run = None
                observation_warning('start', error)
        if args.command != 'workflow':
            try:
                delay = max(0, 1200 - workflow_run['elapsed_seconds']) if workflow_run else 1200
                watcher = threading.Timer(delay, observe)
                watcher.daemon = True
                watcher.start()
            except Exception as error:
                observation_warning('timer', error)
        data = execute(args)
        result = {'ok': True, 'status': 'success', 'data': data}
        if args.command in {'map', 'finish'}:
            result['browser_delivery'] = {'status': 'pending', 'complete': False,
                'reason': 'This command produces Markdown only. Build and check the current browser map, then verify the real project entrance and rendering before reporting complete delivery.'}

        if args.command == 'workflow':
            notify_workflow(data)
        if isinstance(data, dict) and (data.get('ok') is False or data.get('timed_out') or ('returncode' in data and data['returncode'] != 0)):
            result.update(ok=False, status='blocked')
            code = 1
    except Exception as error:
        result = error_response(error)
        code = 1
    finally:
        if watcher is not None:
            try:
                watcher.cancel()
            except Exception as error:
                observation_warning('timer', error)
    if workflow_run:
        try:
            result['workflow'] = crs_workflow.command_finished(args.workflow, args.command, workflow_run['run_id'], result['ok'])
            notify_workflow(result['workflow'])
            workflow_finished = True
        except Exception as error:
            observation_warning('finish', error)
    if observation_warnings:
        result['observation_warnings'] = list(observation_warnings)
        result['workflow_tracking_error'] = dict(observation_warnings[0])
    if args.workflow:
        result['workflow_command_observation_complete'] = bool(workflow_finished and not observation_warnings)
    result['terminal_state'] = 'blocked' if code else ('recovered' if args.command == 'recover' else 'success')
    result['elapsed_seconds'] = round(time.monotonic() - started, 6)
    result['elapsed_seconds_scope'] = 'command_invocation'
    result['over_twenty_minutes'] = result['elapsed_seconds'] > 1200
    if 'data' in result:
        result['data'] = project_diagnostics(args.command, result['data'])
    emit_result(result)
    return code


if __name__ == '__main__':
    sys.exit(main())

