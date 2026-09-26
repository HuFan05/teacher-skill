"""External workflow wall-clock observations; never stop or approve research.

One session spans commands, review and waiting. Its clock starts once. The
foreground watch ends after one attention event or session end, not a service.
"""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile
import time
import uuid

from crs_model import CRSError, canonical, parse_json
from crs_store import ordinary

THRESHOLD_SECONDS = 1200
NOTICE = 'CRS workflow exceeded 20 minutes; inspect recorded phases, computation, I/O, review and repair time. Research continues and evidence requirements remain unchanged.'


def _stamp(now):
    return datetime.fromtimestamp(now, timezone.utc).isoformat()


def _seconds(stamp):
    return datetime.fromisoformat(stamp).timestamp()


def _text(value, label, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise CRSError('workflow_text_invalid', 'Expected workflow text.', {'field': label})
    return value


def _path(path):
    path = ordinary(Path(path).absolute())
    if path.suffix.lower() != '.json' or not path.parent.is_dir():
        raise CRSError('workflow_path_invalid', 'Choose an external JSON session in an existing directory.')
    for parent in path.parents:
        if (parent / 'HEAD').is_file() and (parent / 'objects').is_dir():
            raise CRSError('workflow_inside_project', 'Workflow observations belong outside the formal research store.')
    return path


@contextmanager
def _locked(path):
    lock = ordinary(path.with_name(path.name + '.lock'))
    with lock.open('a+b') as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        deadline = time.monotonic() + 5
        while True:
            try:
                handle.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise CRSError('workflow_busy', 'Another writer is updating this workflow session.')
                time.sleep(.02)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _load(path):
    if not path.is_file():
        raise CRSError('workflow_missing', 'Begin the external workflow session first.')
    value = parse_json(path.read_bytes())
    keys = {'schema', 'id', 'started_at', 'finished_at', 'phase', 'events', 'attention'}
    if not isinstance(value, dict) or set(value) != keys or value['schema'] != 'crs-workflow/v1':
        raise CRSError('workflow_invalid', 'Invalid workflow session.')
    _seconds(value['started_at'])
    if value['finished_at'] is not None:
        _seconds(value['finished_at'])
    if not isinstance(value['events'], list) or not value['events']:
        raise CRSError('workflow_invalid', 'Missing workflow history.')
    return value


def _save(path, value):
    descriptor, name = tempfile.mkstemp(prefix='.' + path.name + '-', suffix='.tmp', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(canonical(value))
            handle.flush()
            os.fsync(handle.fileno())
        ordinary(path)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _diagnosis(session, now):
    phases, active = {}, {}
    phase, previous = session['events'][0]['phase'], _seconds(session['started_at'])
    durations, count = 0.0, 0
    for event in session['events']:
        at = _seconds(event['at'])
        if event['kind'] in {'checkpoint', 'end'}:
            phases[phase] = phases.get(phase, 0) + max(0, at - previous)
            phase, previous = event['phase'], at
        if event['kind'] == 'command_started':
            active[event['run_id']] = event
        elif event['kind'] == 'command_finished':
            start = active.pop(event['run_id'], None)
            if start:
                durations += max(0, at - _seconds(start['at']))
                count += 1
    phases[phase] = phases.get(phase, 0) + max(0, now - previous)
    return {'phase_wall_seconds': {key: round(value, 6) for key, value in phases.items()},
            'completed_command_count': count, 'summed_command_wall_seconds': round(durations, 6),
            'unfinished_commands': [{'run_id': key, 'command': event['command'], 'started_at': event['at']}
                                    for key, event in active.items()],
            'interpretation': 'Phase time includes review and waiting. Overlapping command durations may overlap; unfinished entries do not prove a process is alive.'}


def _observe(session, now):
    elapsed = max(0, now - _seconds(session['started_at']))
    if session['finished_at'] is None and session['attention'] is None and elapsed >= THRESHOLD_SECONDS:
        session['attention'] = {'at': _stamp(now), 'elapsed_seconds': elapsed,
                                'diagnosis': _diagnosis(session, now), 'message': NOTICE,
                                'research_stopped': False, 'quality_requirements_changed': False}
        session['events'].append({'kind': 'attention', 'at': _stamp(now), 'phase': session['phase']})
        return True
    return False


def _result(path, session, now, notified=False):
    finish = _seconds(session['finished_at']) if session['finished_at'] else now
    elapsed = max(0, finish - _seconds(session['started_at']))
    return {'session': str(path), 'id': session['id'], 'started_at': session['started_at'],
            'finished_at': session['finished_at'], 'phase': session['phase'],
            'elapsed_seconds': round(elapsed, 6), 'over_twenty_minutes': elapsed >= THRESHOLD_SECONDS,
            'status': 'workflow_ended' if session['finished_at'] else ('workflow_attention_required' if session['attention'] else 'workflow_active'),
            'notification_emitted': notified, 'attention': session['attention'],
            'research_stopped': False, 'quality_requirements_changed': False}


def begin(path, phase='开始', note=''):
    path = _path(path)
    _text(phase, 'phase'); _text(note, 'note', True)
    with _locked(path):
        if path.exists():
            raise CRSError('workflow_exists', 'This session already exists; checkpoint it without resetting its start.')
        now = time.time()
        value = {'schema': 'crs-workflow/v1', 'id': uuid.uuid4().hex,
                 'started_at': _stamp(now), 'finished_at': None, 'phase': phase,
                 'events': [{'kind': 'begin', 'at': _stamp(now), 'phase': phase, 'note': note}], 'attention': None}
        _save(path, value)
        return _result(path, value, now)


def _update(path, kind=None, phase=None, note='', command=None, run_id=None, ok=None):
    path = _path(path)
    if phase is not None:
        _text(phase, 'phase')
    _text(note, 'note', True)
    with _locked(path):
        value = _load(path)
        now = time.time()
        if value['finished_at'] is not None:
            if kind not in {None, 'end'}:
                raise CRSError('workflow_ended', 'This workflow has ended; begin a new session for a new task.')
            return _result(path, value, now)
        notified = _observe(value, now)
        if kind:
            if phase is not None:
                value['phase'] = phase
            event = {'kind': kind, 'at': _stamp(now), 'phase': value['phase'], 'note': note}
            if command is not None:
                event.update(command=command, run_id=run_id)
            if ok is not None:
                event['ok'] = ok
            value['events'].append(event)
            if kind == 'end':
                value['finished_at'] = _stamp(now)
        if notified or kind:
            _save(path, value)
        return _result(path, value, now, notified)


def checkpoint(path, phase, note=''):
    return _update(path, 'checkpoint', phase, note)


def end(path, phase='完成', note=''):
    return _update(path, 'end', phase, note)


def check(path):
    return _update(path)


def command_started(path, command):
    _text(command, 'command')
    run_id = uuid.uuid4().hex
    result = _update(path, 'command_started', command=command, run_id=run_id)
    return {**result, 'run_id': run_id}


def command_finished(path, command, run_id, ok):
    return _update(path, 'command_finished', command=command, run_id=run_id, ok=bool(ok))


def watch(path):
    """Bounded foreground wait; no worker service, research execution or stop."""
    while True:
        result = check(path)
        if result['status'] != 'workflow_active':
            return result
        remaining = THRESHOLD_SECONDS - result['elapsed_seconds']
        time.sleep(max(.01, min(1, remaining)))
