"""Restore exact declared materials and explicitly replay inspected local code.

Bundle verification never executes supplied tools or research. Replay is an
opt-in Windows process-tree supervised route, not a filesystem/network sandbox.
"""
from __future__ import annotations

import crs_temp
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from crs_model import CRSError, canonical, digest, parse_json
from crs_unique import no_links
from crs_exchange import _extracted, _verify_extracted, _read_json, _file_digest
from crs_export_plan import validate_contract, material_status, closed
from crs_finish import _publish

MARKER = '.crs-delivery.json'

def fail(code, message):
    raise CRSError(code, message)

def new_output(path):
    path = no_links(path).resolve()
    if path.exists() or not path.parent.is_dir():
        fail('delivery_recipient_output', 'Use a new output directory in an existing ordinary parent.')
    return path

def restore_delivery(bundle, group_id, output):
    output = new_output(output)
    bundle = no_links(bundle).resolve()
    if output.is_relative_to(bundle) or bundle.is_relative_to(output):
        fail('delivery_recipient_output', 'Restored materials must be separate from their delivery package.')
    with _extracted(bundle) as (root, names):
        verified, manifest, records, reviews = _verify_extracted(root, names)
        if 'delivery_plan' not in manifest:
            fail('delivery_groups_required', 'This package binds no delivery plan or material groups; use its ordinary reviewed-exchange route.')
        plan = _read_json(root / 'delivery-plan.json')
        contract = plan['contract']
        if contract is None:
            fail('delivery_groups_required', 'A material contract is required for named restoration.')
        group = next((g for g in contract['groups'] if g['id'] == group_id), None)
        if group is None:
            fail('delivery_group_unknown', 'Choose a group declared in this exact package.')
        coverage = next(g for g in verified['material_coverage']['groups'] if g['id'] == group_id)
        with crs_temp.TemporaryDirectory(prefix='.crs-restore-', dir=output.parent) as tmp:
            work = Path(tmp)
            copied = {}
            for name, member in group['files'].items():
                asset = manifest['assets'][member['sha256']]
                if asset['availability'] != 'included':
                    continue
                source = no_links(root / asset['path'])
                target = no_links(work / name)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                if _file_digest(target) != member['sha256']:
                    fail('delivery_recipient_hash', 'Restored material differs from verified package bytes.')
                copied[name] = member['sha256']
            receipt = {'schema':'crs-restored-delivery/v1',
                       'plan_sha256':manifest['delivery_plan']['sha256'],
                       'source_snapshot':manifest['source_snapshot']['sha256'],
                       'contract':dict(contract, groups=[group]), 'files':copied,
                       'material_coverage':coverage, 'replay_performed':False,
                       'research_authority_added':False}
            raw = canonical(receipt)
            (work / MARKER).write_bytes(raw)
            _publish(work, output)
    return {'status':'materials_restored', 'output':str(output),
            'receipt_sha256':digest(raw), 'group':group_id, 'restored_file_count':len(copied),
            'material_coverage':coverage, 'replay_performed':False,
            'offline_reproduction_verified':False, 'research_authority_added':False}

def replay_delivery(restored, output, expected_receipt, execute=False, timeout=60):
    # Preflight cannot be mistaken for permission to run newly received code.
    if not execute:
        fail('delivery_execution_required', 'Inspect the restored code and use --execute with its exact receipt hash to authorize execution.')
    if os.name != 'nt':
        fail('delivery_runtime_unsupported', 'This supervised recipient route currently supports Windows; use an independently supervised runtime elsewhere.')
    if type(timeout) is not int or not 1 <= timeout <= 3600:
        fail('delivery_replay_timeout', 'Replay timeout must be 1..3600 seconds.')
    restored = no_links(restored).resolve()
    marker = no_links(restored / MARKER)
    if marker.stat().st_size > 32 * 1024 * 1024:
        fail('delivery_recipient_receipt', 'Restoration receipt exceeds the metadata bound.')
    raw = marker.read_bytes()
    if digest(raw) != expected_receipt:
        fail('delivery_recipient_hash', 'Restoration receipt does not match the inspected identity.')
    receipt = parse_json(raw)
    closed(receipt, {'schema','plan_sha256','source_snapshot','contract','files','material_coverage','replay_performed','research_authority_added'}, 'restoration')
    if receipt['schema'] != 'crs-restored-delivery/v1' or receipt['replay_performed'] is not False or receipt['research_authority_added'] is not False:
        fail('delivery_recipient_receipt', 'Invalid restoration authority or status.')
    contract = validate_contract(receipt['contract'])
    if len(contract['groups']) != 1 or not isinstance(receipt['files'], dict):
        fail('delivery_recipient_receipt', 'Restore exactly one material group before replay.')
    group = contract['groups'][0]
    declared = {name:row['sha256'] for name,row in group['files'].items()}
    if any(name not in declared or sha != declared[name] for name,sha in receipt['files'].items()):
        fail('delivery_recipient_receipt', 'Restored file inventory differs from its declaration.')
    actual_names = {p.relative_to(restored).as_posix() for p in restored.rglob('*') if p.is_file()}
    if actual_names != set(receipt['files']) | {MARKER}:
        fail('delivery_recipient_receipt', 'Unexpected files could alter replay imports; restore a fresh exact workspace.')
    paths = {}
    for name, sha in receipt['files'].items():
        path = no_links(restored / name)
        if _file_digest(path) != sha:
            fail('delivery_recipient_hash', 'Inspected material changed after restoration.')
        paths[sha] = path
    coverage = material_status(contract, {sha:{'availability':'included'} for sha in paths}, paths=paths)
    if not coverage['material_groups_complete']:
        fail('delivery_materials_incomplete', 'Missing or unknown declared material dependencies prevent replay.')
    env = group['environment']
    version = '.'.join(map(str,sys.version_info[:3]))
    if (env['runtime'].casefold() != 'python' or env['packages']
            or env['platform'].casefold() not in {'any','windows','win32'}
            or not (version == env['version'] or version.startswith(env['version'] + '.'))):
        fail('delivery_runtime_unsupported', 'The bounded built-in route requires this Python version on Windows with standard-library dependencies only.')
    if group['argv'] != ['{python}', group['entrypoint']]:
        fail('delivery_runtime_unsupported', 'This route accepts exactly {python} and the declared script; other invocations need independent inspection and execution.')
    output = new_output(output)
    if output.is_relative_to(restored) or restored.is_relative_to(output):
        fail('delivery_recipient_output', 'Replay output must be separate from restored input.')
    # Retain the owned workspace, logs and started receipt on every outcome.
    # An interrupted started receipt is unresolved and must never auto-resume.
    output.mkdir()
    (output / '.crs-replay-started.json').write_bytes(canonical({
        'schema':'crs-recipient-replay-start/v1','restoration_sha256':expected_receipt,
        'plan_sha256':receipt['plan_sha256'],'timeout_seconds':timeout,
        'automatic_resume_allowed':False,'isolation':'Owned working directory and process tree; no filesystem or network sandbox.'}))
    work = output / 'work'; work.mkdir()
    for name, sha in receipt['files'].items():
        target = no_links(work / name); target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(paths[sha], target)
        if _file_digest(target) != sha:
            fail('delivery_recipient_hash', 'Replay input changed during copying; execution did not start.')
    environment = {k:v for k,v in os.environ.items() if k.upper() in {'SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','TEMP','TMP'}}
    environment.update(PYTHONNOUSERSITE='1',PYTHONDONTWRITEBYTECODE='1')
    from crs_replay import _WindowsTree
    tree = _WindowsTree(); started = time.monotonic(); timed_out = False; descendants = False
    try:
        with (output/'stdout.log').open('xb') as stdout, (output/'stderr.log').open('xb') as stderr:
            process = tree.launch([sys.executable,'-E','-s','-B',group['entrypoint']],cwd=work,env=environment,
                                  stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr)
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True; tree.terminate(); process.wait(timeout=10)
            # Root wait completion can precede Windows Job accounting by a
            # short interval. Wait for accounting convergence before treating
            # a nonempty job as lingering descendants, still within the bound.
            settle_deadline = time.monotonic() + min(2.0, max(0.0, timeout - (time.monotonic() - started)))
            while tree.active() and time.monotonic() < settle_deadline:
                time.sleep(.02)
            descendants = tree.active() != 0
            if descendants:
                tree.terminate()
            deadline = time.monotonic() + 10
            while tree.active() and time.monotonic() < deadline:
                time.sleep(.02)
            if tree.active():
                fail('delivery_process_unresolved', 'Owned process tree did not reach a verified stopped state; retain the workspace for diagnosis.')
    finally:
        tree.close()
    checks=[]
    for name, sha in group['expected_outputs'].items():
        target=no_links(work/name)
        actual=_file_digest(target) if target.is_file() else None
        checks.append({'name':name,'expected_sha256':sha,'actual_sha256':actual,'passed':actual==sha})
    inputs_unchanged = all(no_links(work/name).is_file() and _file_digest(work/name)==sha for name,sha in receipt['files'].items())
    passed = process.returncode == 0 and not timed_out and not descendants and inputs_unchanged and bool(checks) and all(row['passed'] for row in checks)
    result={'schema':'crs-recipient-replay/v1','status':'replay_verified' if passed else 'replay_failed',
            'ok':passed,'restoration_sha256':expected_receipt,'plan_sha256':receipt['plan_sha256'],
            'returncode':process.returncode,'timed_out':timed_out,'lingering_descendants':descendants,
            'process_tree_stopped':True,'input_bytes_unchanged':inputs_unchanged,'output_checks':checks,
            'runtime':{'python':version,'platform':sys.platform},'elapsed_seconds':time.monotonic()-started,
            'replay_performed':True,'offline_reproduction_verified':passed,
            'verification_scope':'This material group, runtime and exact declared output hashes only.',
            'research_authority_added':False,'bundled_tools_executed':False,
            'filesystem_network_sandbox':False}
    (output/'result.json').write_bytes(canonical(result))
    return dict(result, output=str(output))
