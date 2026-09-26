"""Bounded computation CLI transport; does not rerun jobs or confer evidence."""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from contextvars import ContextVar
from functools import wraps
import io
import json
import sys

DEFAULT_LIMIT = 65536
MAX_LIMIT = 1048576
ERROR_CODES = frozenset({'record_validation_failed', 'record_io_failed', 'capability_probe_failed', 'capability_probe_invalid_response'})
_limit = ContextVar('computation_output_limit', default=DEFAULT_LIMIT)
_operation = ContextVar('computation_requested_operation', default=None)
_outcome = ContextVar('computation_business_outcome', default=None)
_parser_text = ContextVar('computation_parser_text', default='')
_parser_exit = ContextVar('computation_parser_exit', default=False)
_response_reason = ContextVar('computation_response_reason', default=False)


class InputError(Exception):
    pass


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        raise InputError()

    def _print_message(self, message, file=None):
        if message:
            _parser_text.set(_parser_text.get() + message)

    def exit(self, status=0, message=None):
        if status != 0 or (_limit.get() > DEFAULT_LIMIT and not _response_reason.get()):
            raise InputError()
        self._print_message(message)
        _parser_exit.set(True)
        raise SystemExit(0)


class _ResponseLimit(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        if not 4096 <= values <= MAX_LIMIT:
            raise InputError()
        setattr(namespace, self.dest, values)
        _limit.set(values)


class _ResponseReason(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, values)
        _response_reason.set(bool(values.strip()))


def add_output_arguments(parser):
    parser.add_argument('--max-response-bytes', type=int, action=_ResponseLimit, default=DEFAULT_LIMIT,
                        help='Whole UTF-8 response limit including newline, 4096..1048576.')
    parser.add_argument('--response-reason', action=_ResponseReason, default='',
                        help='Specific evidence gap justifying a response above 65536 bytes; not returned.')


def configure_output(args):
    limit = args.max_response_bytes
    if not 4096 <= limit <= MAX_LIMIT or (limit > DEFAULT_LIMIT and not args.response_reason.strip()):
        raise InputError()
    _limit.set(limit)
    operation = getattr(args, 'operation', None)
    if isinstance(operation, str) and len(operation) <= 256:
        _operation.set(operation)


def error_response(error):
    candidate = getattr(error, 'code', None)
    # Some validators raise a plain ValueError whose text is a fixed code.
    if candidate is None and type(error) is ValueError:
        candidate = str(error)
    code = candidate if isinstance(candidate, str) and candidate in ERROR_CODES else 'input_or_environment_error'
    if isinstance(error, InputError):
        code = 'invalid_request'
    return {'ok': False, 'status': 'blocked', 'terminal_state': 'blocked', 'code': code,
            'message': 'Operation could not complete normally. Use its fixed code and original operation identity to inspect status or recovery; do not automatically rerun a computation.',
            'details': None, 'raw_diagnostics_returned': False,
            'business_outcome': 'unknown', 'do_not_retry_automatically': True,
            **({'operation': _operation.get()} if _operation.get() is not None else {})}


def _summary(value, reason):
    outcome = _outcome.get()
    known = isinstance(outcome, dict)
    ok = outcome.get('ok') is True if known else False
    refs = {}
    if known:
        data = outcome.get('data', outcome)
        if isinstance(data, dict):
            for key in ('operation', 'operation_id', 'head', 'snapshot', 'current_snapshot', 'project_id', 'transaction_id', 'run_id', 'candidate', 'job_id', 'result_directory'):
                item = data.get(key)
                if _safe_reference(item):
                    refs[key] = item
            for key in ('committed', 'reused', 'delivery_complete'):
                if type(data.get(key)) is bool:
                    refs[key] = data[key]
            states = {'success', 'blocked', 'recovered', 'pending_visual_review', 'ready_to_publish', 'published', 'incomplete'}
            for key in ('terminal_state', 'publication'):
                if isinstance(data.get(key), str) and data[key] in states:
                    refs[key] = data[key]
    if _safe_reference(_operation.get()):
        refs['operation'] = _operation.get()
    terminal = outcome.get('terminal_state') if known else None
    if terminal not in ('success', 'blocked', 'recovered', 'pending_visual_review', 'ready_to_publish', 'published', 'incomplete'):
        terminal = 'success' if ok else 'blocked'
    business = outcome.get('business_outcome') if known else None
    if not isinstance(business, str) or business not in ('success', 'unknown', 'failed', 'pending', 'incomplete'):
        business = 'success' if ok else 'unknown'
    result = {'ok': ok, 'status': 'success' if ok else 'blocked',
            'terminal_state': terminal,
            'response_complete': False, 'response_code': reason,
            'response_limit_bytes': _limit.get(), 'limit_scope': 'this_response_only',
            'business_outcome': business,
            'do_not_retry_automatically': True, 'data': {'details_omitted': True, **refs},
            'hint': 'The response is incomplete. Reopen the retained exact result for the required evidence or justify a larger response. Inspect the existing computation before recovery; output omission never authorizes rerunning it.'}
    # Drop optional opaque references whole, never truncate an identifier.
    for key in ('run_id', 'project_id', 'current_snapshot', 'head', 'transaction_id', 'operation_id', 'snapshot', 'candidate', 'operation', 'result_directory', 'job_id'):
        if len(_serialize(result).encode('utf-8')) <= _limit.get():
            break
        if key in result['data']:
            del result['data'][key]
            result['data'].setdefault('omitted_reference_keys', []).append(key)
    return result


def _safe_reference(item):
    if not isinstance(item, str) or len(item) > 256:
        return False
    try:
        item.encode('utf-8')
    except UnicodeError:
        return False
    return True


def _serialize(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n'


def emit_result(value):
    if isinstance(value, dict):
        value = dict(value)
        value.setdefault('response_complete', True)
    _outcome.set(value if isinstance(value, dict) else None)
    try:
        if not isinstance(value, dict):
            raise ValueError()
        wire = _serialize(value)
        size = len(wire.encode('utf-8'))
    except (ValueError, TypeError, RecursionError, UnicodeError):
        wire = _serialize(_summary(value, 'response_serialization_failed'))
    else:
        if size > _limit.get():
            wire = _serialize(_summary(value, 'response_limit_exceeded'))
    stream = sys.stdout
    if hasattr(stream, 'reconfigure'):
        stream.reconfigure(encoding='utf-8', errors='strict', newline='\n')
    stream.write(wire)


class _Capture(io.StringIO):
    def __init__(self):
        super().__init__()
        self.overflow = False

    def write(self, value):
        if self.tell() + len(value) > 2 * MAX_LIMIT:
            self.overflow = True
            return len(value)
        return super().write(value)


def public_main(function):
    """Prevent incidental stdout/errors from bypassing structured transport."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        tokens = (_limit.set(DEFAULT_LIMIT), _operation.set(None), _outcome.set(None),
                  _parser_text.set(''), _parser_exit.set(False), _response_reason.set(False))
        out, err = _Capture(), _Capture()
        code, help_requested = 0, False
        try:
            with redirect_stdout(out), redirect_stderr(err):
                try:
                    code = function(*args, **kwargs) or 0
                except SystemExit as error:
                    code = error.code if type(error.code) is int else (0 if error.code is None else 2)
                    help_requested = _parser_exit.get() and code == 0
                    if not help_requested and _outcome.get() is None:
                        code = 2
                        out.seek(0); out.truncate()
                        emit_result(error_response(InputError()))
                except Exception as error:
                    code = 2
                    out.seek(0); out.truncate()
                    emit_result(error_response(error))
            if help_requested and len(_parser_text.get().encode('utf-8')) <= _limit.get():
                if hasattr(sys.stdout, 'reconfigure'):
                    sys.stdout.reconfigure(encoding='utf-8', errors='strict', newline='\n')
                sys.stdout.write(_parser_text.get())
                return 0
            try:
                value = json.loads(out.getvalue())
                if out.overflow or not isinstance(value, dict):
                    raise ValueError()
            except (ValueError, UnicodeError, RecursionError):
                value = _summary(None, 'invalid_structured_output')
            if err.getvalue() or err.overflow:
                value['stderr_diagnostics_suppressed'] = True
            if value.get('ok') is False and code == 0:
                code = 1
            # Preserve the original outcome across the final serialization pass.
            outcome = _outcome.get()
            emit_result(value)
            _outcome.set(outcome)
            return code
        finally:
            _limit.reset(tokens[0]); _operation.reset(tokens[1]); _outcome.reset(tokens[2])
            _parser_text.reset(tokens[3]); _parser_exit.reset(tokens[4])
            _response_reason.reset(tokens[5])
    return wrapped
