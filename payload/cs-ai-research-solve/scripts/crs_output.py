"""Model-visible CLI transport; does not alter research objects or publication."""
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
# Explicit trusted source identifiers only; never derive this set from runtime input.
ERROR_CODES = frozenset([
    'adoption_disposition_incomplete',
    'adoption_disposition_invalid',
    'adoption_inventory_mismatch',
    'adoption_mapping_invalid',
    'adoption_required_asset_omitted',
    'adoption_research_missing',
    'adoption_source_changed',
    'adoption_unmapped_asset',
    'ambiguous_formal_library',
    'assessment_input',
    'asset_changed',
    'asset_corrupt',
    'asset_hash_mismatch',
    'asset_missing',
    'asset_size_mismatch',
    'batch_changed',
    'batch_preflight_required',
    'batch_report_limit',
    'batch_report_missing',
    'batch_report_path_invalid',
    'browser_index_missing',
    'browser_validation_failed',
    'bytes_required',
    'capture_invalid',
    'capture_missing',
    'capture_spec_invalid',
    'cold_root_overlap',
    'command_unknown',
    'commit_readback_failed',
    'complete_reading_requires_upgrade_command',
    'complete_upgrade_requires_browser_delivery',
    'conditional_dependency',
    'content_changed',
    'content_container_encrypted',
    'content_container_invalid',
    'content_container_unknown',
    'content_container_unsupported',
    'content_duplicate_write',
    'content_limit_invalid',
    'content_link_path',
    'content_member_collision',
    'content_member_link',
    'content_member_path',
    'content_nonordinary',
    'content_resource_limit',
    'content_scope_invalid',
    'content_scope_narrowed',
    'content_uniqueness_blocked',
    'continuity_baseline_unrelated',
    'continuity_basis_invalid',
    'continuity_contradictory_disposition',
    'continuity_decision_binding',
    'continuity_dependency_identity_mismatch',
    'continuity_disposition_invalid',
    'continuity_evidence_missing',
    'continuity_exact_read_required',
    'continuity_existing_relation_disputed',
    'continuity_head_changed',
    'continuity_list_invalid',
    'continuity_originals_missing',
    'continuity_output_inside_project',
    'continuity_pair_coverage',
    'continuity_pair_duplicate',
    'continuity_pair_evidence_required',
    'continuity_pair_outside_scope',
    'continuity_plan_changed',
    'continuity_read_missing',
    'continuity_reason_missing',
    'continuity_record_coverage',
    'continuity_relation_invalid',
    'continuity_relation_not_saved',
    'continuity_revision_cycle',
    'continuity_revision_identity_mismatch',
    'continuity_schema_invalid',
    'continuity_scope_invalid',
    'continuity_seed_missing',
    'continuity_seeds_required',
    'continuity_snapshot_unrelated',
    'continuity_stored_read_required',
    'continuity_stored_revision_invalid',
    'continuity_unconfirmed_edge',
    'correction_identity',
    'correction_kind',
    'delivery_contract_invalid',
    'delivery_dependencies_unknown',
    'delivery_entrypoint_missing',
    'delivery_execution_required',
    'delivery_group_unknown',
    'delivery_groups_required',
    'delivery_inside_project',
    'delivery_material_binding',
    'delivery_materials_incomplete',
    'delivery_plan_blocked',
    'delivery_plan_budget',
    'delivery_plan_foreign_project',
    'delivery_plan_hash',
    'delivery_plan_invalid',
    'delivery_plan_inventory',
    'delivery_plan_options',
    'delivery_plan_output',
    'delivery_plan_selection',
    'delivery_plan_stale',
    'delivery_process_unresolved',
    'delivery_recipient_hash',
    'delivery_recipient_output',
    'delivery_recipient_receipt',
    'delivery_record_scope',
    'delivery_recursive_material_conflict',
    'delivery_reference_invalid',
    'delivery_replay_timeout',
    'delivery_required_material_missing',
    'delivery_runtime_unsupported',
    'delivery_snapshot_changed',
    'delivery_unreviewed_asset',
    'duplicate_or_unknown_role',
    'dynamic_dependency_enumeration_unresolved',
    'dynamic_file_dependency_unresolved',
    'dynamic_import_unresolved',
    'empty_review_report',
    'epistemic_kind',
    'exact_read_required',
    'exchange_archive_limit',
    'exchange_asset_availability',
    'exchange_asset_inventory',
    'exchange_asset_missing',
    'exchange_asset_reference_invalid',
    'exchange_asset_reference_missing',
    'exchange_asset_size_conflict',
    'exchange_assurance_mismatch',
    'exchange_consumption_hash',
    'exchange_correction_context_missing',
    'exchange_dependency_unreviewed',
    'exchange_directory_limit',
    'exchange_duplicate_member',
    'exchange_entry_limit',
    'exchange_exclusions_invalid',
    'exchange_expansion_limit',
    'exchange_invalid_hash',
    'exchange_invalid_json',
    'exchange_invalid_manifest',
    'exchange_invalid_size',
    'exchange_invalid_zip',
    'exchange_json_limit',
    'exchange_link_path',
    'exchange_local_path',
    'exchange_member_size',
    'exchange_member_type',
    'exchange_namespace_hash',
    'exchange_namespace_invalid',
    'exchange_namespace_missing',
    'exchange_no_reviewed_records',
    'exchange_objective_invalid',
    'exchange_origins_invalid',
    'exchange_output_exists',
    'exchange_output_overlap',
    'exchange_output_parent',
    'exchange_output_type',
    'exchange_portable_closure_incomplete',
    'exchange_portable_content_changed',
    'exchange_portable_dependencies_changed',
    'exchange_portable_endpoint_outside_projection',
    'exchange_portable_evidence_changed',
    'exchange_portable_fidelity_review_missing',
    'exchange_portable_material_missing',
    'exchange_portable_objective_unsupported',
    'exchange_portable_plan_invalid',
    'exchange_portable_replacement_invalid',
    'exchange_portable_replacement_unreviewed',
    'exchange_publish_failed',
    'exchange_record_canonical',
    'exchange_record_hash',
    'exchange_record_missing',
    'exchange_review_binding',
    'exchange_review_history_missing',
    'exchange_review_missing',
    'exchange_review_report_budget',
    'exchange_review_report_missing',
    'exchange_snapshot_invalid',
    'exchange_status_mismatch',
    'exchange_status_not_preserved',
    'exchange_tool_inventory',
    'exchange_tools_missing',
    'exchange_unknown_file',
    'exchange_unreviewed_material',
    'exchange_unreviewed_summary',
    'exchange_unsafe_path',
    'excluded_identity_conflict',
    'excluded_record_invalid',
    'excluded_records_invalid',
    'export_indivisible_payload',
    'export_part_limit',
    'export_parts_changed',
    'export_parts_exists',
    'export_parts_invalid',
    'export_parts_limit',
    'export_parts_missing',
    'export_parts_unknown',
    'export_priority_invalid',
    'file_dependency_undeclared',
    'finish_atomic_publish_unavailable',
    'finish_link_path',
    'finish_output_exists',
    'finish_output_overlap',
    'finish_output_parent',
    'finish_publish_failed',
    'finish_snapshot_changed',
    'head_changed',
    'head_conflict',
    'head_invalid',
    'input_changed',
    'input_digest_mismatch',
    'input_or_environment_error',
    'intake_invalid',
    'invalid_browser_state',
    'invalid_digest',
    'invalid_formal_head',
    'invalid_markdown_binding',
    'invalid_navigation_schema',
    'invalid_relative_path',
    'invalid_request',
    'invalid_retained_entry',
    'invalid_retained_inventory',
    'invalid_title',
    'inventory_required',
    'json_duplicate_key',
    'json_invalid',
    'json_key',
    'json_nonfinite',
    'json_type',
    'linked_path_forbidden',
    'location_batch_readonly',
    'locations_invalid',
    'map_anchor_missing',
    'map_anchor_unconverted',
    'map_extension',
    'map_formula_delimiter',
    'map_formula_invalid',
    'map_formula_output_required',
    'map_formula_readback',
    'map_formula_replacements',
    'map_name_unsafe',
    'map_output_exists',
    'map_output_inside_project',
    'map_parent_missing',
    'markdown_target_required',
    'missing_directory_purpose',
    'missing_retained_target',
    'nonstandard_directory_roles',
    'objective_ambiguous',
    'objective_anchor_changed',
    'objective_binding_invalid',
    'objective_binding_required',
    'objective_complete_required',
    'objective_identity_changed',
    'objective_identity_mismatch',
    'objective_input_conflict',
    'objective_projection_mismatch',
    'objective_required',
    'objective_schema_kind',
    'objective_scope_required',
    'objective_selection_invalid',
    'operation_id_invalid',
    'operation_required',
    'operation_reused',
    'operation_unknown',
    'output_must_be_new_external_directory',
    'path_escape',
    'path_escape_or_noncanonical',
    'pending_browser_reason_required',
    'project_not_empty',
    'project_uninitialized',
    'python_assertions_disabled',
    'python_dependency_undeclared',
    'python_source_analysis_limit',
    'python_source_unparsed',
    'query_limit_invalid',
    'read_scope_invalid',
    'record_hash',
    'record_unknown',
    'recovery_conflict',
    'reparse_path',
    'replay_argv_invalid',
    'replay_cancel_failed',
    'replay_copy_corrupt',
    'replay_executor_interrupted',
    'replay_job_invalid',
    'replay_job_query_failed',
    'replay_job_unavailable',
    'replay_limit_invalid',
    'replay_operation_invalid',
    'replay_path_invalid',
    'replay_preparation_conflict',
    'replay_running',
    'replay_spec_invalid',
    'replay_token_mismatch',
    'replay_tree_still_active',
    'replay_unknown',
    'replay_workspace_invalid',
    'replay_workspace_unowned',
    'report_mismatch',
    'review_batch_blocked',
    'review_batch_duplicate',
    'review_batch_internal_supersession',
    'review_batch_invalid',
    'review_batch_item_invalid',
    'review_batch_reports_invalid',
    'review_report_changed',
    'review_report_is_target',
    'review_supersedes_cycle',
    'review_supersedes_missing',
    'review_supersedes_self',
    'review_supersedes_target',
    'review_supersession_invalid',
    'review_target_missing',
    'revision_batch_invalid',
    'revision_identity_mismatch',
    'schema_duplicate',
    'schema_enum',
    'schema_fields',
    'schema_hash',
    'schema_list',
    'schema_size',
    'schema_text',
    'schema_type',
    'schema_version',
    'selected_identity',
    'selected_identity_mismatch',
    'selection_invalid',
    'snapshot_invalid',
    'source_changed',
    'source_invalid',
    'source_inventory_invalid',
    'source_inventory_limit',
    'source_limit_invalid',
    'source_missing',
    'source_name_collision',
    'source_nonregular_file',
    'source_objective_invalid',
    'source_origins_invalid',
    'source_path_invalid',
    'source_required',
    'source_revision_missing',
    'source_selection_invalid',
    'source_unsafe_member',
    'source_zip_invalid',
    'stale_markdown_binding',
    'stale_navigation_snapshot',
    'stale_snapshot',
    'temporary_root_inside_project',
    'title_required',
    'workflow_busy',
    'workflow_ended',
    'workflow_exists',
    'workflow_inside_project',
    'workflow_invalid',
    'workflow_missing',
    'workflow_nested_tracking',
    'workflow_path_invalid',
    'workflow_phase_required',
    'workflow_text_invalid',
    'writer_busy',
])
_limit = ContextVar('crs_output_limit', default=DEFAULT_LIMIT)
_operation = ContextVar('crs_requested_operation', default=None)
_outcome = ContextVar('crs_business_outcome', default=None)
_parser_text = ContextVar('crs_parser_text', default='')
_parser_exit = ContextVar('crs_parser_exit', default=False)
_response_reason = ContextVar('crs_response_reason', default=False)


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


# Diagnostic projections apply only to declared operation-result fields. Never
# traverse research records by key name or rewrite persisted diagnostic evidence.
_DIAGNOSTIC_CODES = ERROR_CODES | frozenset({
    'capture_missing', 'review_check_input_error', 'review_batch_input_error',
    'review_batch_io_error', 'terminology_contract_invalid',
})
_DIAGNOSTIC_STAGES = frozenset({
    'item_schema', 'report_binding', 'review_schema', 'review_metadata',
    'report_portability', 'review_admission', 'batch_relations',
    'prepared', 'preparing', 'executing', 'evidence_saved', 'complete',
    'cancel_requested',
})


def _diagnostic_view(value, index=None):
    source = value if isinstance(value, dict) else {}
    code = source.get('code')
    result = {
        'code': code if isinstance(code, str) and code in _DIAGNOSTIC_CODES else 'input_or_environment_error',
        'message': 'Raw diagnostic text is omitted; use the fixed code and operation context.',
        'details': None,
        'raw_diagnostics_returned': False,
        'diagnostic_complete': False,
    }
    position = source.get('index', index)
    if type(position) is int and position >= 0:
        result['index'] = position
    for key in ('stage', 'phase'):
        selected = source.get(key)
        if isinstance(selected, str) and selected in _DIAGNOSTIC_STAGES:
            result[key] = selected
    details = source.get('details')
    if isinstance(details, dict):
        safe = {}
        for key in ('sha256', 'asset_sha256', 'actual_sha256'):
            selected = details.get(key)
            if isinstance(selected, str) and len(selected) == 64 and all(c in '0123456789abcdef' for c in selected):
                safe[key] = selected
        for key in ('line', 'index'):
            selected = details.get(key)
            if type(selected) is int and selected >= 0:
                safe[key] = selected
        if safe:
            result['details'] = safe
    return result


def _diagnostic_list(value):
    if not isinstance(value, list):
        return [_diagnostic_view(None)]
    return [_diagnostic_view(item, index) for index, item in enumerate(value)]


def project_diagnostics(command, value):
    """Return a projection without altering the operation's stored evidence."""
    if not isinstance(value, dict):
        return value
    if command not in {'replay', 'replay-status', 'recover', 'review', 'review-batch'}:
        return value
    result = dict(value)
    if command in {'replay', 'replay-status', 'recover'}:
        for key in ('executor_error',):
            if result.get(key) is not None:
                result[key] = _diagnostic_view(result[key])
        if 'capture_errors' in result:
            result['capture_errors'] = _diagnostic_list(result['capture_errors'])
        # A saved replay job contains one result object, not arbitrary research.
        if 'result' in result and result['result'] is not None:
            nested = result['result']
            if isinstance(nested, dict):
                nested = dict(nested)
                if nested.get('executor_error') is not None:
                    nested['executor_error'] = _diagnostic_view(nested['executor_error'])
                if 'capture_errors' in nested:
                    nested['capture_errors'] = _diagnostic_list(nested['capture_errors'])
                result['result'] = nested
            else:
                result['result'] = {'diagnostic_complete': False, 'code': 'input_or_environment_error'}
    if command in {'review', 'review-batch'}:
        if 'findings' in result:
            result['findings'] = _diagnostic_list(result['findings'])
        if command == 'review-batch' and isinstance(result.get('items'), list):
            rows = []
            for row in result['items']:
                if isinstance(row, dict):
                    row = dict(row)
                    if 'findings' in row:
                        row['findings'] = _diagnostic_list(row['findings'])
                rows.append(row)
            result['items'] = rows
    return result





def _safe_delivery_receipt(value):
    if not isinstance(value, dict):
        return None
    keys = {'terminal_state', 'output', 'sha256', 'index_sha256', 'delivery_plan_sha256', 'source_snapshot',
            'record_count', 'review_count', 'included_asset_count', 'referenced_asset_count',
            'part_count', 'read_command', 'do_not_retry_automatically'}
    if set(value) != keys or value['terminal_state'] not in {'success', 'unresolved'}:
        return None
    if not isinstance(value['output'], str) or len(value['output'].encode('utf-8')) > 1000:
        return None
    if value['read_command'] != ['verify-bundle', value['output']] or value['do_not_retry_automatically'] is not True:
        return None
    for key in ('sha256', 'index_sha256', 'delivery_plan_sha256', 'source_snapshot'):
        item = value[key]
        if item is not None and (not isinstance(item, str) or len(item) != 64 or any(c not in '0123456789abcdef' for c in item)):
            return None
    for key in ('record_count','review_count','included_asset_count','referenced_asset_count','part_count'):
        if value[key] is not None and (type(value[key]) is not int or not 0 <= value[key] <= 10**12):
            return None
    return dict(value)


def error_response(error):
    candidate = getattr(error, 'code', None)
    # Some project validators use literal ValueError codes instead of CRSError.
    if candidate is None and type(error) is ValueError:
        candidate = str(error)
    code = candidate if isinstance(candidate, str) and candidate in ERROR_CODES else 'input_or_environment_error'
    if isinstance(error, InputError):
        code = 'invalid_request'
    return {'ok': False, 'status': 'blocked', 'terminal_state': 'blocked', 'code': code,
            'message': 'Operation could not complete normally. Use its fixed code and original operation identity to inspect status or recovery; do not automatically retry publication.',
            'details': None, 'raw_diagnostics_returned': False,
            'business_outcome': 'unknown', 'do_not_retry_automatically': True,
            **({'operation': _operation.get()} if _operation.get() is not None else {}),
            **({'delivery_receipt': _safe_delivery_receipt(error.delivery_receipt)} if hasattr(error, 'delivery_receipt') else {})}


def _summary(value, reason):
    outcome = _outcome.get()
    known = isinstance(outcome, dict)
    ok = outcome.get('ok') is True if known else False
    refs = {}
    if known:
        data = outcome.get('data', outcome)
        if isinstance(data, dict):
            receipt = _safe_delivery_receipt(data.get('delivery_receipt'))
            if receipt is not None:
                refs['delivery_receipt'] = receipt
            for key in ('operation', 'operation_id', 'head', 'snapshot', 'current_snapshot', 'project_id', 'transaction_id', 'run_id', 'candidate', 'batch_sha256'):
                item = data.get(key)
                if _safe_reference(item) and (key != 'batch_sha256' or len(item) == 64 and all(c in '0123456789abcdef' for c in item)):
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
            'hint': 'The response is incomplete, not an empty search or a rollback. Narrow a read, reopen exact records, or justify a larger response. Complete maps remain available through map --out. Inspect the existing operation before any publication recovery.'}
    # Drop optional opaque references whole, never truncate an identifier.
    for key in ('run_id', 'project_id', 'current_snapshot', 'head', 'transaction_id', 'operation_id', 'snapshot', 'candidate', 'operation', 'batch_sha256'):
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
    def wrapped():
        tokens = (_limit.set(DEFAULT_LIMIT), _operation.set(None), _outcome.set(None),
                  _parser_text.set(''), _parser_exit.set(False), _response_reason.set(False))
        out, err = _Capture(), _Capture()
        code, help_requested = 0, False
        try:
            with redirect_stdout(out), redirect_stderr(err):
                try:
                    code = function() or 0
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
