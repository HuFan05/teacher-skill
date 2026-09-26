"""Bound Python public-main returns before exposing content to a caller."""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from contextvars import ContextVar
from functools import wraps
import io
import json
import sys

DEFAULT_BYTES = 12 * 1024
MAX_BYTES = 256 * 1024
_limit = ContextVar('pdf_response_limit', default=DEFAULT_BYTES)
_json_expected = ContextVar('pdf_response_json', default=False)
_CODES = frozenset('alias_query_failed anchor_query_failed cache_not_writable db_connect_failed dependency_import_failed empty_db inventory_load_failed missing_cases_file missing_data_root missing_db missing_pdf missing_root module_import_failed no_cases no_pdf_databases pdf_extraction_failed pdftotext_missing query_exception query_spec_failed schema_error signature_query_failed unsupported_search_mode unexpected_error output_too_large'.split())
_STAGES = frozenset('anchor_rescue cache case_load data_root db_connect db_discovery db_validate dependency_import inventory_load module_import pdf_discovery pdf_extract pdftotext query_spec regression_db signature_rescue sqlite_fts_query survey_context paper_locate query'.split())


class PublicInputError(Exception):
    pass


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise PublicInputError()


def parse_public_args(parser, *, route):
    parser.add_argument('--max-response-bytes', type=int, default=DEFAULT_BYTES,
                        help='Full UTF-8 response limit including newline: 1024..262144; default 12288.')
    parser.add_argument('--response-reason', default='',
                        help='Specific evidence gap requiring a larger response; never echoed.')
    args = parser.parse_args()
    _json_expected.set(route == 'locate' or any(getattr(args, k, False) for k in ('json', 'compact', 'score_files_only')))
    if not 1024 <= args.max_response_bytes <= MAX_BYTES:
        raise PublicInputError()
    if args.max_response_bytes > DEFAULT_BYTES and not args.response_reason.strip():
        raise PublicInputError()
    _limit.set(args.max_response_bytes)
    if route == 'indexed' and not args.db and not any(
            x == '--data-root' or x.startswith('--data-root=') for x in sys.argv[1:]):
        raise PublicInputError()
    if route == 'direct' and not (args.root or args.pdf_file):
        raise PublicInputError()
    return args


def public_diagnostic(value):
    if not isinstance(value, dict):
        return {'level': 'warning', 'code': 'unclassified_diagnostic'}
    level = value.get('level')
    code = value.get('code')
    stage = value.get('stage')
    result = {'level': level if level in ('info', 'warning', 'error') else 'warning',
            'code': code if isinstance(code, str) and code in _CODES else 'unclassified_diagnostic',
            'stage': stage if isinstance(stage, str) and stage in _STAGES else 'search'}
    exception = value.get('exception_type')
    if exception in ('RuntimeError', 'ValueError', 'TypeError', 'OSError', 'FileNotFoundError', 'PermissionError', 'UnicodeDecodeError', 'OperationalError', 'DatabaseError', 'ImportError', 'ModuleNotFoundError'):
        result['exception_type'] = exception
    return result


def _project(value):
    if isinstance(value, list):
        return [_project(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key == 'diagnostics_summary':
            summary = item if isinstance(item, dict) else {}
            result[key] = {name: summary.get(name) if type(summary.get(name)) is int and summary[name] >= 0 else None
                           for name in ('errors', 'warnings', 'info')}
        elif key == 'extraction' and isinstance(item, dict):
            status = item.get('status')
            method = item.get('method')
            result[key] = {
                'status': status if status in ('indexed', 'partial', 'failed', 'empty', 'unknown', 'ok', 'no_text', 'unindexed') else 'unknown',
                'method': method if method in (None, 'pdftotext', 'pymupdf', 'PyMuPDF', 'ocr', 'fixture') else 'other',
                'warning': 'extraction_warning_present' if item.get('warning') else None,
            }
        elif key in ('diagnostics', 'warnings'):
            items = item if isinstance(item, list) else [item]
            result[key] = [public_diagnostic(d) for d in items[:8]]
            if len(items) > 8:
                result[key + '_omitted_count'] = len(items) - 8
        else:
            result[key] = _project(item)
    return result


class _Capture(io.StringIO):
    """Cap buffered characters locally; overflow never falls back to raw text."""
    def __init__(self):
        super().__init__()
        self.overflow = False

    def write(self, text):
        if self.tell() + len(text) > MAX_BYTES:
            self.overflow = True
            return len(text)
        return super().write(text)


def _failure(code):
    return {'status': 'failed', 'error': code, 'results': [],
            'coverage': {'incomplete': True}, 'content_returned': False,
            'response_limit_bytes': _limit.get(), 'limit_scope': 'this_response_only',
            'hint': ('Select fewer candidates or one verification rank. If the complete statement still needs more space, use --max-response-bytes with --response-reason; do not treat omitted evidence as verified.'
                     if code == 'output_too_large' else
                     'Check explicit source selection and arguments with --help; no raw diagnostic was returned.')}


def guarded_main(function):
    @wraps(function)
    def run():
        token = _limit.set(DEFAULT_BYTES)
        format_token = _json_expected.set(False)
        out, err = _Capture(), _Capture()
        result = None
        code = 1
        try:
            with redirect_stdout(out), redirect_stderr(err):
                try:
                    code = function()
                except PublicInputError:
                    result = _failure('invalid_request')
                    code = 2
                except SystemExit as exc:
                    if exc.code in (None, 0):
                        code = 0
                    else:
                        result = _failure('invalid_request')
                        code = 2
                except Exception:
                    result = _failure('search_failed')
            if result is None and (out.overflow or err.overflow):
                result, code = _failure('output_too_large'), 1
            if result is None and err.getvalue():
                result, code = _failure('unexpected_diagnostic_output'), 1
            if result is None:
                raw = out.getvalue()
                try:
                    result = _project(json.loads(raw))
                except json.JSONDecodeError:
                    if _json_expected.get():
                        code = 1
                        wire = json.dumps(_failure('invalid_result'), separators=(',', ':')) + '\n'
                    else:
                        # Explicit plain-text contract and static --help only.
                        wire = raw.rstrip('\r\n') + '\n'
                except (RecursionError, TypeError, ValueError):
                    code = 1
                    wire = json.dumps(_failure('invalid_result'), separators=(',', ':')) + '\n'
                else:
                    wire = json.dumps(result, ensure_ascii=False, separators=(',', ':')) + '\n'
            else:
                wire = json.dumps(result, ensure_ascii=False, separators=(',', ':')) + '\n'
            try:
                wire_bytes = len(wire.encode('utf-8'))
            except UnicodeError:
                code = 1
                wire = json.dumps(_failure('invalid_result'), separators=(',', ':')) + '\n'
                wire_bytes = len(wire.encode('utf-8'))
            if wire_bytes > _limit.get():
                code = 1
                wire = json.dumps(_failure('output_too_large'), separators=(',', ':')) + '\n'
            stream = sys.stdout
            if hasattr(stream, 'reconfigure'):
                stream.reconfigure(encoding='utf-8', errors='strict', newline='\n')
            stream.write(wire)
            return code
        finally:
            _limit.reset(token)
            _json_expected.reset(format_token)
    return run
