"""Owned note CLI transport. Does not constrain libraries, the host or task totals."""
from contextlib import redirect_stdout, redirect_stderr
from contextvars import ContextVar
from functools import wraps
import io
import json
import sys
import hashlib
import tempfile
from pathlib import Path

DEFAULT_LIMIT = 65536
MAX_LIMIT = 1048576
CAPTURE_LIMIT = 8 * MAX_LIMIT
AUDIT_LIMIT = CAPTURE_LIMIT + DEFAULT_LIMIT
_saved_audit = ContextVar('saved_note_audit', default=None)


class OptionsError(ValueError):
    pass


def _options(argv):
    clean, values = [], {}
    flags = {'--max-response-bytes', '--response-reason', '--details-limit', '--details-offset', '--details-path'}
    i = 0
    while i < len(argv):
        token = argv[i]
        if token == '--':
            clean.extend(argv[i:]); break
        key, sep, value = token.partition('=')
        if key not in flags:
            clean.append(token); i += 1; continue
        if key in values:
            raise OptionsError()
        if not sep:
            i += 1
            if i >= len(argv):
                raise OptionsError()
            value = argv[i]
        values[key] = value
        i += 1
    try:
        limit = int(values.get('--max-response-bytes', DEFAULT_LIMIT))
        count = int(values.get('--details-limit', 20))
        offset = int(values.get('--details-offset', 0))
    except (TypeError, ValueError):
        raise OptionsError() from None
    reason = values.get('--response-reason', '')
    selected_path = values.get('--details-path', '')
    if not 4096 <= limit <= MAX_LIMIT or not 1 <= count <= 200 or not 0 <= offset <= 1000000:
        raise OptionsError()
    if selected_path and (not selected_path.startswith('/') or len(selected_path) > 512):
        raise OptionsError()
    if offset and not selected_path:
        raise OptionsError()
    if (limit > DEFAULT_LIMIT or count > 20 or offset or selected_path) and not reason.strip():
        raise OptionsError()
    if any(flag in clean for flag in ['--write', '--execute']) and (offset or selected_path):
        raise OptionsError()
    return clean, limit, count, offset, selected_path


def diagnostic(value):
    text = str(value).lower()
    rules = [
        ('reference_sync_pending', 'reference_sync_pending'),
        ('assertion', 'assertion_failed'),
        ('after temp preparation', 'concurrent_change_after_preparation'),
        ('external change', 'concurrent_change'),
        ('sha256', 'hash_guard_or_binding_failed'), ('hash', 'hash_guard_or_binding_failed'),
        ('mtime', 'concurrent_change'), ('changed', 'concurrent_change'),
        ('owner', 'owner_identity_mismatch'), ('permission', 'permission_denied'),
        ('outside', 'outside_authorized_root'), ('utf', 'invalid_encoding'),
        ('decode', 'invalid_encoding'), ('json', 'invalid_json'),
        ('ambiguous', 'ambiguous_target'), ('missing', 'required_input_missing'),
        ('not found', 'required_input_missing'), ('no such file', 'required_input_missing'),
        ('unclosed', 'unclosed_syntax'), ('empty', 'empty_input'),
        ('unsupported', 'unsupported_input'), ('delimiter', 'invalid_formula_delimiter'),
        ('backslash', 'formula_transport_check'), ('group', 'formula_group_check'),
        ('resource', 'resource_reference_check'), ('duplicate', 'duplicate_target'),
        ('argument', 'invalid_arguments'), ('required', 'required_input_missing'),
    ]
    return next((code for needle, code in rules if needle in text), 'operation_validation_failed')


class Capture(io.StringIO):
    def __init__(self):
        super().__init__(); self.overflow = False; self.byte_count = 0

    @property
    def encoding(self):
        return 'utf-8'

    def reconfigure(self, **kwargs):
        pass

    def write(self, value):
        size = len(value.encode('utf-8'))
        if self.byte_count + size > CAPTURE_LIMIT:
            self.overflow = True
            return len(value)
        self.byte_count += size
        return super().write(value)


def _references(value, selected_path='', prefix='/resource_references'):
    if not isinstance(value, dict):
        return {'complete': False, 'code': 'invalid_reference_audit'}
    result = {'external_change': value.get('external_change'), 'preflight': value.get('preflight'), 'available_detail_paths': []}
    for key in ['added', 'removed', 'retained', 'unknown', 'missing', 'retired', 'path_mismatch', 'uri_mismatch', 'unmanaged_added', 'unmanaged_removed', 'unmanaged_retained']:
        rows = value.get(key, [])
        result[key + '_count'] = len(rows) if isinstance(rows, list) else None
        if isinstance(rows, list) and rows:
            result['available_detail_paths'].append(prefix + '/' + key)
        if key in {'unknown', 'missing', 'retired', 'path_mismatch', 'uri_mismatch', 'unmanaged_added'} or selected_path == prefix + '/' + key:
            result[key] = rows if isinstance(rows, list) else []
    for key in ['before', 'after']:
        part = value.get(key, {})
        result[key] = {name: part.get(name) for name in ['managed_count', 'unmanaged_count']} if isinstance(part, dict) else {}
        if isinstance(part, dict):
            for collection in ['managed', 'unmanaged']:
                if isinstance(part.get(collection), list) and part[collection]:
                    result['available_detail_paths'].append(prefix + '/' + key + '/' + collection)
                if selected_path == prefix + '/' + key + '/' + collection:
                    result[key][collection] = part.get(collection, [])
    for name in ['sync', 'rollback_sync']:
        if name == 'rollback_sync' and name not in value:
            continue
        sync = value.get(name, {})
        result[name] = {k: sync[k] for k in ['status', 'ok', 'post_sha256', 'error'] if k in sync} if isinstance(sync, dict) else {'status': 'unknown'}
    result['reference_details_omitted'] = True
    return result


def project(operation, value, count=20, offset=0, selected_path=''):
    """Project diagnostic fields and paged auxiliary collections; never rewrite artifacts."""
    coverage = {}
    fields_reduced = False

    def page(rows, path):
        start = offset if path == selected_path else 0
        size = count if not selected_path or path == selected_path else 20
        if selected_path.startswith(path + '/'):
            descendant = selected_path[len(path) + 1:].split('/', 1)[0]
            if descendant.isdecimal():
                start, size = int(descendant), 1
        selected = rows[start:start + size]
        coverage[path] = {'total': len(rows), 'offset': start, 'returned': len(selected), 'complete': start == 0 and len(selected) == len(rows)}
        return selected, start

    def visit(obj, path='', diagnostic_row=False):
        nonlocal fields_reduced
        if isinstance(obj, dict):
            result = {}
            for key, item in obj.items():
                at = path + '/' + key.replace('~', '~0').replace('/', '~1')
                if key in {'harness_diagnostics', 'raw_output', 'stdout', 'stderr', 'traceback'}:
                    fields_reduced = True
                    result[key + '_omitted'] = True
                elif key == 'resource_references':
                    fields_reduced = True
                    result[key] = visit(_references(item, selected_path, at), at)
                elif key in {'error', 'write_error', 'status_error', 'detail'} and item:
                    result[key] = diagnostic(item)
                    fields_reduced = fields_reduced or result[key] != item
                elif key == 'message' and diagnostic_row:
                    result[key] = diagnostic(item)
                    fields_reduced = fields_reduced or result[key] != item
                elif key in {'tex', 'snippet', 'excerpt'} and diagnostic_row:
                    fields_reduced = True
                    result[key + '_omitted'] = True
                elif key == 'text' and operation == 'vault_batch_edit':
                    fields_reduced = True
                    result['text_omitted'] = True
                elif key in {'warnings', 'errors'} and isinstance(item, list):
                    selected, start = page(item, at)
                    result[key] = [visit(row, at + '/' + str(i + start), True) if isinstance(row, dict) else diagnostic(row) for i, row in enumerate(selected)]
                    fields_reduced = fields_reduced or any(not isinstance(row, dict) and diagnostic(row) != row for row in selected)
                else:
                    result[key] = visit(item, at, diagnostic_row)
            return result
        if isinstance(obj, list):
            selected, start = page(obj, path)
            return [visit(row, path + '/' + str(i + start), diagnostic_row) for i, row in enumerate(selected)]
        return obj

    result = visit(value)
    if not isinstance(result, dict):
        raise ValueError()
    if selected_path and selected_path not in coverage:
        raise OptionsError()
    result['detail_coverage'] = coverage
    result['fields_reduced'] = fields_reduced
    source_complete = all(row.get('complete') is True for row in value.get('source_coverage', {}).values())
    result['details_complete'] = value.get('details_complete', True) is True and source_complete and not fields_reduced and all(row['complete'] for row in coverage.values())
    result['response_complete'] = value.get('response_complete', True) is True
    result['raw_diagnostics_returned'] = False
    return result


def _fallback(code, payload=None, exit_code=2):
    result = {'ok': False, 'code': code, 'response_complete': False, 'business_outcome': 'unknown', 'automatic_retry_allowed': False,
              'recovery': 'Inspect the selected operation or artifact before any retry. Narrow the requested view, or justify a larger response for missing evidence.'}
    if isinstance(payload, dict):
        for key in ['ok', 'applied', 'changed', 'write', 'status', 'transaction_status', 'race_check', 'pre_sha256', 'post_sha256', 'terminal_state']:
            value = payload.get(key)
            if value is None or isinstance(value, bool) or isinstance(value, str) and len(value) <= 128:
                if key in payload:
                    result[key] = value
        result['business_outcome'] = 'see_preserved_fields'
    result['operation_exit_code'] = exit_code
    return result


def _wire(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n'


def save_audit(operation, payload):
    raw = _wire({'schema': 'note-operation-audit/v1', 'operation': operation, 'result': payload}).encode('utf-8')
    if len(raw) > AUDIT_LIMIT:
        raise ValueError('Unsupported audit size')
    with tempfile.NamedTemporaryFile(prefix='note-operation-', suffix='.json', delete=False) as stream:
        stream.write(raw)
        path = stream.name
    return {'path': path, 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw),
            'read_arguments': [str(Path(__file__).resolve()), '--read-audit', path, '--expected-sha256', hashlib.sha256(raw).hexdigest()],
            'purpose': 'Read this saved result with a justified response/detail window; do not repeat the original mutation.'}


def public_entry(operation):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            supplied = args[0] if args else kwargs.get('argv')
            argv = list(sys.argv[1:] if supplied is None else supplied)
            saved = sys.argv[:]
            output, errors = Capture(), Capture()
            payload = None; exit_code = 0; channel = sys.stdout
            limit = DEFAULT_LIMIT
            saved_token = _saved_audit.set(None)
            try:
                clean, limit, count, offset, selected_path = _options(argv)
                sys.argv = [saved[0], *clean]
                if args:
                    args = (clean, *args[1:])
                elif 'argv' in kwargs:
                    kwargs = {**kwargs, 'argv': clean}
                with redirect_stdout(output), redirect_stderr(errors):
                    try:
                        exit_code = function(*args, **kwargs) or 0
                    except SystemExit as exc:
                        exit_code = exc.code if type(exc.code) is int else 2
                if output.overflow or errors.overflow:
                    raise ValueError()
                raw = output.getvalue()
                if not raw.strip() and errors.getvalue().strip():
                    raw = errors.getvalue(); channel = sys.stderr
                try:
                    payload = json.loads(raw)
                except ValueError:
                    if exit_code == 0 and (operation == 'vault_stats' and '--markdown' in clean or '--help' in clean or '-h' in clean):
                        if len(raw.encode('utf-8')) <= limit:
                            channel.write(raw)
                            return 0
                    exit_code = exit_code or 2
                    payload = _fallback('invalid_or_unstructured_result', exit_code=exit_code)
                saved_audit = _saved_audit.get()
                actual_operation = saved_audit['operation'] if saved_audit else operation
                result = project(actual_operation, payload, count, offset, selected_path)
                audit = saved_audit['reference'] if saved_audit else None
                if isinstance(payload, dict) and any(result.get(key) != item for key, item in payload.items()):
                    audit = audit or save_audit(actual_operation, payload)
                    result['local_audit'] = audit
                wire = _wire(result)
                if len(wire.encode('utf-8')) > limit:
                    audit = audit or save_audit(operation, payload)
                    reduced = _fallback('response_limit_exceeded', payload, exit_code)
                    reduced['local_audit'] = audit
                    wire = _wire(reduced)
            except OptionsError:
                exit_code = 2; wire = _wire(_fallback('invalid_response_options', payload, exit_code))
            except Exception as exc:
                exit_code = 2; wire = _wire(_fallback(diagnostic(exc), payload, exit_code))
            finally:
                sys.argv = saved
                _saved_audit.reset(saved_token)
            if hasattr(channel, 'reconfigure'):
                channel.reconfigure(encoding='utf-8', errors='strict', newline='\n')
            channel.write(wire)
            return exit_code
        return wrapped
    return decorate


@public_entry('saved_audit')
def main():
    import argparse
    parser = argparse.ArgumentParser(description='Read a hash-bound saved operation result without repeating the operation.')
    parser.add_argument('--read-audit', type=Path, required=True)
    parser.add_argument('--expected-sha256', required=True)
    args = parser.parse_args()
    if args.read_audit.stat().st_size > AUDIT_LIMIT:
        raise ValueError('Unsupported audit size')
    with args.read_audit.open('rb') as source:
        raw = source.read(AUDIT_LIMIT + 1)
    if len(raw) > AUDIT_LIMIT:
        raise ValueError('Unsupported audit size')
    if hashlib.sha256(raw).hexdigest() != args.expected_sha256:
        raise ValueError('Audit hash mismatch')
    record = json.loads(raw)
    if not isinstance(record, dict) or set(record) != {'schema', 'operation', 'result'} or record['schema'] != 'note-operation-audit/v1' or not isinstance(record['result'], dict):
        raise ValueError('Unsupported audit schema')
    if record['operation'] not in {'vault_edit', 'vault_stats', 'vault_impact', 'vault_batch_edit', 'vault_file_bundle', 'validate_obsidian_formulas', 'validate_obsidian_links', 'extract_recall_markdown'}:
        raise ValueError('Unsupported audit operation')
    _saved_audit.set({'operation': record['operation'], 'reference': {'path': str(args.read_audit), 'sha256': args.expected_sha256, 'bytes': len(raw), 'read_only': True}})
    print(_wire(record['result']), end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
