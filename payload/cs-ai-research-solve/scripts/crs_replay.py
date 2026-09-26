"""Durable bounded reproduction jobs. Byte capture never admits research review.

Windows uses a job object for the launched process tree. POSIX uses a separate
process group that is signalled as a whole on timeout or cancellation.
An uncertain supervisor crash preserves its workspace for explicit diagnosis.
"""
from __future__ import annotations
import argparse
import contextlib
import ctypes
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid

from crs_model import CRSError, canonical, digest, parse_json
from crs_store import Store, OP, ordinary, file_digest


def fail(code, message, details=None):
    raise CRSError(code, message, details)


def job_path(store, operation):
    if not isinstance(operation, str) or not OP.fullmatch(operation):
        fail('replay_operation_invalid', 'Use an explicit short operation identifier.')
    return store._path(Path('replays') / operation / 'job.json')


def has_job(store, operation):
    return job_path(store, operation).is_file()


def read_job(store, operation):
    path = job_path(store, operation)
    if not path.is_file():
        fail('replay_unknown', 'This reproduction job is not registered.', {'operation_id': operation})
    job = parse_json(path.read_bytes())
    if (not isinstance(job, dict) or job.get('schema') != 'crs-replay-job/v1'
            or job.get('operation_id') != operation or job.get('project') != str(store.root)):
        fail('replay_job_invalid', 'The reproduction job binding is invalid.')
    return job


def update_job(store, operation, **changes):
    with store._lock():
        job = read_job(store, operation)
        job.update(changes)
        for attempt in range(5):
            try:
                store._atomic(job_path(store, operation), canonical(job))
                break
            except PermissionError as error:
                # A brief Windows read handle can deny atomic replacement. Bound
                # this retry; real access failures still surface with their cause.
                if os.name != 'nt' or getattr(error, 'winerror', None) not in {5, 32, 33} or attempt == 4:
                    raise
                time.sleep(0.05)
    return job


@contextlib.contextmanager
def worker_lock(store, operation):
    if os.name == 'nt':
        from crs_unique import writer_mutex
        with writer_mutex(store.root / 'replays' / operation / 'worker'):
            yield
        return
    path = job_path(store, operation).with_name('worker.lock')
    try:
        with path.open('xb') as handle:
            handle.write(uuid.uuid4().hex.encode('ascii'))
    except FileExistsError:
        pass
    with path.open('r+b') as handle:
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fail('replay_running', 'The registered supervisor is still active; its workspace is retained.')
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def owned_workspace(store, job, create=False):
    from crs_unique import external_target
    external_target(store, job['temp_root'])
    external_target(store, job['workspace'])
    base = ordinary(Path(job['temp_root'])).resolve()
    work = ordinary(Path(job['workspace'])).resolve()
    if (work.parent != base or not work.name.startswith('crs-replay-')
            or work.is_relative_to(store.root) or base.is_relative_to(store.root)):
        fail('replay_workspace_invalid', 'The registered workspace is not an external owned directory.')
    marker = work / '.crs-replay-owner.json'
    expected = {'schema': 'crs-replay-owner/v1', 'operation_id': job['operation_id'],
                'token': job['token'], 'project': str(store.root)}
    if create and not work.exists():
        base.mkdir(parents=True, exist_ok=True)
        work.mkdir()
        with marker.open('xb') as stream:
            stream.write(canonical(expected))
    if work.exists() and (not marker.is_file() or parse_json(marker.read_bytes()) != expected):
        fail('replay_workspace_unowned', 'The workspace ownership marker does not match this exact job.')
    return work


@contextlib.contextmanager
def workspace(store, job):
    work = owned_workspace(store, job, create=True)
    used = set()
    for name, sha in job['spec']['files'].items():
        normalized = name.replace('\\', '/')
        relative = Path(normalized)
        if (relative.is_absolute() or ':' in name or '\x00' in name
                or any(part in {'', '.', '..'} for part in normalized.split('/'))
                or normalized.casefold() in used or normalized.startswith('.crs-')):
            fail('replay_path_invalid', 'Input filenames must be unique safe relative paths.')
        used.add(normalized.casefold())
        target = ordinary(work / relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if not target.is_file() or file_digest(target)[0] != sha:
                fail('replay_preparation_conflict', 'A prepared input differs from its registered digest.')
        else:
            shutil.copyfile(store.resolve(sha), target)
            if file_digest(target)[0] != sha:
                fail('replay_copy_corrupt', 'A copied input differs from its registered digest.')
    yield work


def _record(store, job, result=None):
    inputs = {job['spec_sha256']: 'specification.json'}
    inputs.update({sha: name for name, sha in job['spec']['files'].items()})
    evidence = []
    if result:
        inputs.update({sha: name for name, sha in result['outputs'].items()})
        inputs[result['report']] = 'reproduction-report.json'
    for sha, name in sorted(inputs.items()):
        path = store.resolve(sha)
        evidence.append({'sha256': sha, 'bytes': path.stat().st_size, 'name': name, 'role': 'input' if result is None else 'evidence',
                         'summary': 'Exact reproduction material; its storage does not certify the truth of any research claim.', 'locator': 'crs-object:' + sha})
    feedback = 'Prepared; execution has not supplied a result.' if result is None else (
        'Execution completed; returncode=' + str(result['returncode']) + '; timed_out=' + str(result['timed_out'])
        + '; cancelled=' + str(result.get('cancelled', False)) + '; capture_errors=' + str(len(result.get('capture_errors', []))))
    if result and result.get('execution_started') is False:
        error = result['executor_error']
        feedback = 'Execution did not start; ' + error['code'] + ': ' + error['message']
    row = {'schema': 'crs-record/v1', 'id': 'replay:' + job['operation_id'], 'kind': 'attempt',
           'title': 'Reproduction: ' + job['spec']['purpose'], 'statement': 'Bounded reproduction job ' + job['operation_id'] + '.',
           'scope': 'Execution and evidence capture only; no research review is inferred.',
           'action': 'Run the exact registered specification with a declared timeout of ' + str(job['timeout']) + ' seconds.',
           'feedback': feedback, 'epistemic': 'attempt', 'assumptions': [],
           'limitations': ['Code is inspected trusted local code, not a filesystem or network sandbox.'],
           'reopen': [], 'sources': [], 'evidence': evidence, 'dependencies': [], 'conditional_on': [],
           'previous': job.get('initial_record') if result else None, 'correction': None}
    operation = 'replay-' + ('finish-' if result else 'begin-') + digest(job['operation_id'].encode())[:32]
    store.ingest([row], {}, 'local-reproduction', operation)
    return digest(canonical(row))


def persist_result(store, job, result):
    for sha in [result['report'], *result['outputs'].values()]:
        store.resolve(sha)
    update_job(store, job['operation_id'], result=result, status='evidence_saved')


def _save_preexecution_failure(store, job, error, elapsed):
    """Only the owning worker can establish this no-process boundary."""
    work = owned_workspace(store, job)
    captured = {}
    capture_errors = []
    names = [('stdout', '.crs-stdout'), ('stderr', '.crs-stderr')]
    names.extend((name, name) for name in job['spec']['capture'])
    for label, name in names:
        try:
            target = ordinary(work / name).resolve()
            if not target.is_relative_to(work):
                fail('capture_invalid', 'Output leaves the owned reproduction directory.')
            if target.is_file():
                captured[label] = store.put_file(target)
            elif target.exists():
                fail('capture_invalid', 'Declared output is not an ordinary file.')
        except (CRSError, OSError) as problem:
            capture_errors.append({'name': label, 'code': getattr(problem, 'code', 'capture_invalid'), 'reason': str(problem)})
    report = {'schema': 'crs-replay/v1', 'purpose': job['spec']['purpose'], 'inputs': job['spec']['files'],
              'argv': job['spec']['argv'], 'outputs': captured, 'returncode': None, 'timed_out': False,
              'ok': False, 'capture_errors': capture_errors, 'assertion_policy': job['assertion_policy'],
              'cancelled': False, 'process_tree_policy': 'no_research_process_started',
              'execution_started': False, 'executor_error': error, 'elapsed_seconds': elapsed,
              'claim_truth_inferred': False,
              'execution_boundary': 'Preparation or confirmed launch failure; any captured bytes preceded research execution.'}
    result = {key: report[key] for key in ['ok', 'returncode', 'timed_out', 'cancelled', 'process_tree_policy',
              'capture_errors', 'assertion_policy', 'outputs', 'execution_started', 'executor_error']}
    result.update(report=store.put_json(report), temporary_directory_removed=False)
    persist_result(store, job, result)
    return _finish(store, read_job(store, job['operation_id']))


def _finish(store, job):
    result = dict(job['result'])
    _record(store, job, result)
    work = owned_workspace(store, job)
    if work.exists():
        # The resolved exact directory and its ownership marker were checked above.
        shutil.rmtree(work)
    result.update(temporary_directory_removed=not work.exists(), operation_id=job['operation_id'],
                  job_status='complete', background_lifetime='The supervisor ends after this result and cleanup.')
    update_job(store, job['operation_id'], status='complete', result=result)
    return result


def _spawn(store, job):
    environment = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}
    environment.pop('PYTHONOPTIMIZE', None)
    options = {'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
    # Independent, explicitly bounded supervisor. It exits after result/cleanup.
    subprocess.Popen([sys.executable, '-B', str(Path(__file__).resolve()), '--worker', str(store.root),
                      '--operation', job['operation_id'], '--token', job['token']],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     env=environment, close_fds=True, **options)


def start(store, spec, timeout, temp_root=None, operation=None):
    import crs
    _, _, policy = crs.prepare_replay(spec, timeout)
    if not isinstance(spec['files'], dict) or not all(isinstance(k, str) for k in spec['files']):
        fail('replay_spec_invalid', 'Files must map explicit relative names to content digests.')
    for sha in spec['files'].values():
        store.resolve(sha)
    operation = operation or (spec['purpose'] if OP.fullmatch(spec['purpose']) else 'replay-' + uuid.uuid4().hex)
    path = job_path(store, operation)
    from crs_unique import external_target
    base = external_target(store, Path(temp_root).absolute() if temp_root else Path(tempfile.gettempdir()))
    if base.is_relative_to(store.root):
        fail('temporary_root_inside_project', 'Reproduction copies must be outside the formal project.')
    request = digest(canonical({'spec': spec, 'timeout': timeout, 'temp_root': str(base)}))
    with store._lock():
        if path.exists():
            job = read_job(store, operation)
            if job['request_sha256'] != request:
                fail('operation_reused', 'This reproduction operation is bound to different inputs.')
        else:
            token = uuid.uuid4().hex
            job = {'schema': 'crs-replay-job/v1', 'project': str(store.root), 'operation_id': operation,
                   'token': token, 'status': 'prepared', 'spec': spec, 'spec_sha256': store.put_json(spec),
                   'request_sha256': request, 'timeout': timeout, 'temp_root': str(base),
                   'workspace': str(base / ('crs-replay-' + token)), 'assertion_policy': policy,
                   'cancel_requested': False, 'result': None}
            store._atomic(path, canonical(job))
    if not job.get('initial_record'):
        initial = _record(store, job)
        job = update_job(store, operation, initial_record=initial)
    return recover(store, operation, wait_seconds=timeout + 30)


def recover(store, operation, wait_seconds=10):
    deadline = time.monotonic() + wait_seconds
    launched = False
    while True:
        job = read_job(store, operation)
        try:
            with worker_lock(store, operation):
                job = read_job(store, operation)
                if job['result'] is not None:
                    return _finish(store, job)
                if job['status'] == 'prepared':
                    if not job.get('initial_record'):
                        initial = _record(store, job)
                        job = update_job(store, operation, initial_record=initial)
                    if not launched:
                        _spawn(store, job)
                        launched = True
                else:
                    fail('replay_executor_interrupted', 'The supervisor stopped without a terminal result. Its registered workspace is preserved; no PID is guessed and no cleanup is attempted.',
                         {'operation_id': operation, 'workspace': job['workspace'], 'status': job['status'],
                          'executor_error': job.get('executor_error')})
        except CRSError as error:
            if error.code != 'replay_running':
                raise
        if time.monotonic() >= deadline:
            fail('replay_running', 'The bounded job is still active. Use recover or replay-cancel with its operation identifier.', {'operation_id': operation})
        time.sleep(0.05)


def cancel(store, operation):
    job = read_job(store, operation)
    if job['status'] == 'complete':
        return {'operation_id': operation, 'cancel_requested': False, 'status': 'complete'}
    update_job(store, operation, cancel_requested=True)
    return {'operation_id': operation, 'cancel_requested': True, 'status': 'cancel_requested',
            'boundary': 'The registered supervisor cancels only its own launched process tree; recover returns the saved outcome.'}


def status(store, operation):
    job = read_job(store, operation)
    return {'operation_id': operation, 'status': job['status'], 'purpose': job['spec']['purpose'],
            'timeout_seconds': job['timeout'], 'workspace': job['workspace'],
            'cancel_requested': job['cancel_requested'], 'result': job['result'], 'executor_error': job.get('executor_error'),
            'claim_truth_inferred': False}


class _WindowsTree:
    def __init__(self):
        from ctypes import wintypes as w
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        class Basic(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong), ('flags', w.DWORD),
                        ('min_work', ctypes.c_size_t), ('max_work', ctypes.c_size_t), ('active_limit', w.DWORD),
                        ('affinity', ctypes.c_size_t), ('priority', w.DWORD), ('scheduling', w.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in ['read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes']]
        class Extended(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', IO), ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                        ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]
        class Accounting(ctypes.Structure):
            _fields_ = [(name, ctypes.c_longlong) for name in ['user', 'kernel', 'period_user', 'period_kernel']] + [(name, w.DWORD) for name in ['faults', 'total', 'active', 'terminated']]
        self.Accounting = Accounting
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        self.api.CreateJobObjectW.restype = w.HANDLE
        self.api.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        self.api.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        self.api.QueryInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.c_void_p]
        self.api.TerminateJobObject.argtypes = [w.HANDLE, w.UINT]
        self.api.CloseHandle.argtypes = [w.HANDLE]
        self.handle = self.api.CreateJobObjectW(None, None)
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE.
        if not self.handle or not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            fail('replay_job_unavailable', 'Windows could not create a bounded process-tree job.')

    def launch(self, argv, **kwargs):
        process = subprocess.Popen(argv, creationflags=subprocess.CREATE_NO_WINDOW | 0x4, **kwargs)
        try:
            if not self.api.AssignProcessToJobObject(self.handle, int(process._handle)):
                fail('replay_job_unavailable', 'The suspended child could not be bound to its process-tree job.')
            resume = ctypes.WinDLL('ntdll').NtResumeProcess
            resume.argtypes = [ctypes.c_void_p]
            resume.restype = ctypes.c_long
            if resume(int(process._handle)) < 0:
                fail('replay_job_unavailable', 'The bound child could not be resumed.')
            return process
        except BaseException:
            process.kill()
            process.wait()
            raise

    def active(self):
        value = self.Accounting()
        if not self.api.QueryInformationJobObject(self.handle, 1, ctypes.byref(value), ctypes.sizeof(value), None):
            fail('replay_job_query_failed', 'Cannot establish whether the owned process tree has ended.')
        return value.active

    def terminate(self):
        if not self.api.TerminateJobObject(self.handle, 130):
            fail('replay_cancel_failed', 'Unable to stop the exact owned process tree.')

    def close(self):
        if getattr(self, 'handle', None):
            self.api.CloseHandle(self.handle)
            self.handle = None


def run_process(store, job, argv, work, environment, stdout, stderr):
    tree = _WindowsTree() if os.name == 'nt' else None
    process = None
    started = time.monotonic()
    timed_out = cancelled = False
    try:
        options = dict(cwd=work, env=environment, shell=False, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr)
        job['_execution_boundary'] = 'launch_uncertain'
        try:
            process = tree.launch(argv, **options) if tree else subprocess.Popen(argv, start_new_session=True, **options)
        except FileNotFoundError:
            # Popen failed to launch the executable. It returns no live process;
            # do not extend this fact to other launch or process-control errors.
            job['_execution_boundary'] = 'not_started'
            raise
        job['_execution_boundary'] = 'started'
        update_job(store, job['operation_id'], status='executing', child_pid=process.pid,
                   process_tree_policy='windows_job_object' if tree else 'posix_process_group')
        while True:
            code = process.poll()
            if tree:
                active = tree.active() > 0
            else:
                try:
                    os.killpg(process.pid, 0)
                    active = True
                except ProcessLookupError:
                    active = False
            if code is not None and not active:
                break
            cancelled = bool(read_job(store, job['operation_id'])['cancel_requested'])
            timed_out = time.monotonic() - started >= job['timeout']
            if cancelled or timed_out:
                if tree:
                    tree.terminate()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
                if tree:
                    deadline = time.monotonic() + 10
                    while tree.active() and time.monotonic() < deadline:
                        time.sleep(0.02)
                    if tree.active():
                        fail('replay_tree_still_active', 'The exact process tree has not stopped; retain its workspace.')
                else:
                    deadline = time.monotonic() + 10
                    while True:
                        try:
                            os.killpg(process.pid, 0)
                        except ProcessLookupError:
                            break
                        if time.monotonic() >= deadline:
                            fail('replay_tree_still_active', 'The POSIX process group has not disappeared; retain its workspace.')
                        time.sleep(0.02)
                break
            time.sleep(0.05)
        return {'returncode': None if timed_out else process.returncode, 'timed_out': timed_out, 'cancelled': cancelled,
                'process_tree_policy': 'windows_job_object' if tree else 'posix_process_group'}
    finally:
        if tree:
            tree.close()  # Also stops owned descendants if the supervisor fails.
        elif process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)


def worker(store, operation, token):
    import crs
    with worker_lock(store, operation):
        job = read_job(store, operation)
        if job['token'] != token:
            fail('replay_token_mismatch', 'The supervisor does not own this registered job.')
        if job['result'] is not None:
            return _finish(store, job)
        if job['status'] != 'prepared':
            fail('replay_executor_interrupted', 'An unfinished prior supervisor requires diagnosis; it will not be rerun automatically.')
        job = update_job(store, operation, status='preparing')
        job['_execution_boundary'] = 'not_started'  # In-memory fact of this exact worker, never inferred from saved status.
        started = time.monotonic()
        # Unexpected supervisor errors retain the registered workspace. The user
        # can inspect it; recover never guesses that an unknown process has exited.
        try:
            result = crs.run_replay(store, job['spec'], job['timeout'], job['temp_root'], job=job)
            persist_result(store, job, result)
            return _finish(store, read_job(store, operation))
        except Exception as error:
            # Preserve the cause while holding the supervisor lock. Do not infer
            # that an unknown process has ended or delete an uncertain workspace.
            diagnostic = None
            for attempt in range(3):
                try:
                    current = read_job(store, operation)
                    diagnostic = {'code': getattr(error, 'code', type(error).__name__),
                                  'message': str(error)[:2000], 'phase': current['status'], 'exception_type': type(error).__name__}
                    update_job(store, operation, executor_error=diagnostic)
                    break
                except (CRSError, OSError):
                    if attempt < 2:
                        time.sleep(0.05)
            if diagnostic is not None and job['_execution_boundary'] == 'not_started':
                return _save_preexecution_failure(store, job, diagnostic, time.monotonic() - started)
            raise


if __name__ == '__main__':
    sys.dont_write_bytecode = True
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', required=True)
    parser.add_argument('--operation', required=True)
    parser.add_argument('--token', required=True)
    arguments = parser.parse_args()
    try:
        worker(Store(arguments.worker), arguments.operation, arguments.token)
    except BaseException:
        # The durable nonterminal state is intentionally retained for diagnosis.
        raise
