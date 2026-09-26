"""Task-shaped, derived views; source files and transaction plans stay authoritative."""
from __future__ import annotations
import math


def scalar(value):
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and math.isfinite(value):
        return value
    raise ValueError('invalid_status_scalar')


def short_text(value, limit=1024):
    if value is None:
        return None
    if not isinstance(value, str) or len(value)>limit or any(ord(c)<32 for c in value):
        raise ValueError('invalid_status_text')
    return value


def configuration_view(value):
    if not isinstance(value, dict):
        raise ValueError('invalid_configuration_status')
    result={k:scalar(value[k]) for k in ('configured','healthy','schema_version') if k in value}
    if 'knowledge_root_id' in value:
        result['knowledge_root_id']=short_text(value['knowledge_root_id'],128)
    missing=value.get('missing',[])
    if not isinstance(missing,list):raise ValueError('invalid_missing_sources')
    result['missing']=[x for x in ('config','knowledge_root','vault','library') if x in missing]
    result['sources']={}
    sources=value.get('sources',{})
    if not isinstance(sources,dict):raise ValueError('invalid_source_status')
    for name in ('vault','library'):
        if name not in sources:continue
        row=sources[name]
        if not isinstance(row,dict):raise ValueError('invalid_source_status')
        result['sources'][name]={k:short_text(row[k]) for k in ('state','kind','relative_path') if k in row}
        if 'configured' in row:result['sources'][name]['configured']=row['configured'] is True
    result['rediscovery_required']=bool(result['missing']) and value.get('configured') is True
    return result


def library_status_view(value):
    if not isinstance(value,dict):raise ValueError('invalid_library_status')
    fields=('database_exists','root_exists','configured_root_exists','root_matches_config','root_path_matches_config','identity_matches_config','docs','pages','pages_with_text','searchable_docs','processed_docs','pending_docs','coverage_percent','search_format_current','search_rebuild_required','last_scan_success')
    result={k:scalar(value[k]) for k in fields if k in value}
    for k in ('knowledge_root_id','library_relative_path','configured_knowledge_root_id','configured_library_relative_path','last_scan_at','last_successful_scan_at'):
        if k in value:result[k]=short_text(value[k])
    for k in ('search_format_version','expected_search_format_version'):
        if k in value:
            result[k]=short_text(value[k]) if isinstance(value[k],str) else scalar(value[k])
    errors=value.get('last_scan_errors',[])
    if not isinstance(errors,list):raise ValueError('invalid_scan_errors')
    prior_count=value.get('last_scan_error_count',len(errors))
    if not isinstance(prior_count,int) or isinstance(prior_count,bool) or prior_count<0:
        raise ValueError('invalid_scan_error_count')
    result['last_scan_error_count']=len(errors) if 'last_scan_errors' in value else prior_count
    result['scan_error_details_included']=False
    counts=value.get('status_counts',{})
    if not isinstance(counts,dict):raise ValueError('invalid_status_counts')
    # State names are owned by the library schema; unknown future names are counted separately.
    states=('pending','indexed','no_text','error','failed','missing','processing','empty','ok')
    result['status_counts']={k:scalar(counts[k]) for k in states if k in counts}
    previous=value.get('unrecognized_status_count',0)
    if not isinstance(previous,int) or isinstance(previous,bool) or previous<0:raise ValueError('invalid_status_count')
    result['unrecognized_status_count']=previous+sum(v for k,v in counts.items() if k not in states and isinstance(v,int) and not isinstance(v,bool) and v>=0)
    return result


def question_result_groups(selected):
    """Index the selected complete records, avoiding duplicate or unselected bodies."""
    result={'vault_question_index':[],'question_collections':[]}
    for i,row in enumerate(selected):
        adapter=row.get('source_adapter')
        if adapter=='vault_question_index':group='vault_question_index'
        elif adapter=='question_collection':group='question_collections'
        else:raise ValueError('invalid_question_adapter')
        result[group].append({'result_index':i})
    return result


"""Append-only source for the task-shaped OCR diagnostic view."""

def ocr_preflight_view(value):
    import re
    if not isinstance(value,dict):raise ValueError('invalid_ocr_preflight')
    components=('venv_python','tesseract','tessdata','tessdata/configs/hocr','ocrmypdf','pypdfium2','tesseract_version','tesseract_languages')
    codes=('missing_dependency','dependency_probe_failed','unreadable_ocrmypdf_version','unexpected_ocrmypdf_version','missing_language')
    languages=('eng','chi_sim','chi_tra','osd')
    def version(raw):
        if raw is None:return None
        if not isinstance(raw,str) or len(raw)>256:return None
        match=re.fullmatch(r'(?:tesseract\s+)?v?(\d+(?:\.\d+){1,3}(?:[-+][A-Za-z0-9.]+)?)\s*',raw)
        return match.group(1) if match else None
    status=value.get('status')
    result={'ok':value.get('ok') is True,'status':status if status in ('ready','not_ready','missing_dependencies') else 'not_ready','diagnostic_view':'ocr_preflight','paths_included':False,'raw_probe_output_included':False}
    versions=value.get('versions',{})
    if not isinstance(versions,dict):raise ValueError('invalid_ocr_versions')
    result['versions']={k:version(versions.get(k)) for k in ('ocrmypdf','pypdfium2','tesseract')}
    result['unreadable_version_components']=[k for k in result['versions'] if (versions.get(k) is not None and result['versions'][k] is None) or k in value.get('unreadable_version_components',[])]
    reported=value.get('languages',{})
    if not isinstance(reported,dict):raise ValueError('invalid_ocr_languages')
    result['languages']={}
    for key in ('required','available','missing'):
        raw=reported.get(key,[])
        if not isinstance(raw,list):raise ValueError('invalid_ocr_languages')
        result['languages'][key]=[x for x in languages if x in raw]
    configs=value.get('tesseract_configs',{})
    if not isinstance(configs,dict):raise ValueError('invalid_tesseract_configs')
    result['tesseract_configs']={k:['configs/hocr'] if 'configs/hocr' in configs.get(k,[]) else [] for k in ('required','missing')}
    checks=value.get('checks',{})
    if not isinstance(checks,dict):raise ValueError('invalid_ocr_checks')
    result['checks']={}
    for key in ('ocrmypdf','pypdfium2','tesseract_version','tesseract_languages'):
        if key not in checks:continue
        row=checks[key]
        if not isinstance(row,dict):raise ValueError('invalid_ocr_check')
        result['checks'][key]={'ok':row.get('ok') is True}
        if type(row.get('returncode')) is int:result['checks'][key]['returncode']=row['returncode']
    rows=value.get('diagnostics',[])
    if not isinstance(rows,list):raise ValueError('invalid_ocr_diagnostics')
    result['diagnostics']=[]
    for row in rows[:20]:
        if not isinstance(row,dict):raise ValueError('invalid_ocr_diagnostic')
        item={'level':row.get('level') if row.get('level') in ('error','warning','info') else 'error','code':row.get('code') if row.get('code') in codes else 'additional_ocr_condition','component':row.get('component') if row.get('component') in components else 'other'}
        if row.get('language') in languages:item['language']=row['language']
        for key in ('expected','actual'):
            if key in row:item[key]=version(row[key])
        result['diagnostics'].append(item)
    count=value.get('diagnostic_count',len(rows))
    if type(count) is not int or count<len(rows):raise ValueError('invalid_ocr_diagnostic_count')
    result['diagnostic_count']=count
    result['diagnostics_complete']=count<=20 and value.get('diagnostics_complete',True) is True
    return result


def suite_status_view(value):
    """Finite diagnostic overview; detailed records require their own operation."""
    if not isinstance(value, dict):
        raise ValueError('invalid_suite_status')

    def selected(row, fields):
        if not isinstance(row, dict):
            raise ValueError('invalid_suite_component')
        return {k: scalar(row[k]) for k in fields if k in row}

    result = {
        'ok': value.get('ok') is True,
        'status': 'healthy' if value.get('ok') is True else 'attention_required',
        'diagnostic_view': 'suite_status',
        'diagnostics_included': True,
        'raw_diagnostics_returned': False,
        'configuration': configuration_view(value.get('configuration', {})),
        'index_coverage': library_status_view(value.get('index_coverage', {})),
        'ocr': ocr_preflight_view(value.get('ocr', {})),
    }
    deps = value.get('dependencies', {})
    result['dependencies'] = {'pdftotext': selected(deps.get('pdftotext', {}), ('ok',))}
    integration = value.get('integrations', {})
    names = ('skills_root', 'obsidian_setup', 'obsidian_local_kb', 'pdf_paper_search', 'pdf_paper_locate', 'pdf_observer_run')
    checks = integration.get('checks', {})
    result['integrations'] = {
        'ok': integration.get('ok') is True,
        'checks': {k: scalar(checks[k]) for k in names if k in checks},
        'required': [k for k in names if k in integration.get('required', [])],
    }
    obsidian = value.get('obsidian')
    result['obsidian'] = None
    if obsidian is not None:
        row = selected(obsidian, ('ok', 'returncode'))
        error = obsidian.get('error') or {}
        code = error.get('code')
        allowed = ('invalid_vault', 'missing_config', 'invalid_config', 'configured_vault_mismatch', 'missing_dependencies', 'subprocess_failed', 'invalid_json_output', 'subprocess_launch_failed', 'child_reported_failure')
        if error:
            row['error'] = {'code': code if code in allowed else 'integration_error'}
        result['obsidian'] = row
    question = value.get('question_index', {})
    row = selected(question, ('ok', 'root_matches_config', 'docs', 'questions', 'coverage_complete'))
    status = question.get('status')
    row['status'] = status if status in ('ready', 'coverage_gap', 'not_initialized') else 'unknown'
    # The raw list never leaves this overview, but a gap remains visible.
    if 'discovered_unimported' in question:
        items = question['discovered_unimported']
        if not isinstance(items, list):
            raise ValueError('invalid_unimported_collection_list')
        row['discovered_unimported_count'] = len(items)
    elif 'discovered_unimported_count' in question:
        row['discovered_unimported_count'] = scalar(question['discovered_unimported_count'])
    result['question_index'] = row
    resource = value.get('resource_registry', {})
    row = selected(resource, ('ok', 'initialized', 'inventory_checked', 'manifest_checked', 'incomplete_operations', 'total', 'scan_resume_available', 'hash_resume_available', 'eligible_file_count', 'excluded_count', 'unregistered_count', 'reference_sync_pending_count', 'conflict_count'))
    if 'error' in resource or resource.get('diagnostic_failed') is True:
        row['diagnostic_failed'] = True
    for field, names in (('status_counts', ('active', 'missing', 'retired')), ('reference_cache', ('notes', 'references', 'diagnostic_notes', 'sync_pending_notes'))):
        if field in resource:
            row[field] = selected(resource[field], names)
    result['resource_registry'] = row
    result['next_checks'] = ['question-status for coverage detail', 'registry-status for registry detail', 'ocr-preflight for OCR conditions']
    return result


def registry_status_view(value):
    fields = ('total', 'reference_sync_pending_count', 'incomplete_operations', 'scan_resume_available', 'hash_resume_available')
    result = {k: scalar(value[k]) for k in fields if k in value}
    for key, names in (('status_counts', ('active','missing','retired')), ('hash_counts', ('unverified','verified','stale','error')), ('reference_cache', ('notes','references','diagnostic_notes','sync_pending_notes'))):
        row = value.get(key, {})
        result[key] = {k: scalar(row[k]) for k in names if k in row}
    checked = value.get('inventory_checked') is not False
    result.update(inventory_checked=checked, manifest_checked=checked,
                  status_scope='inventory_and_registry' if checked else 'registered_database_counts')
    if checked:
        preview = value.get('inventory_preview', {})
        result['inventory_complete'] = preview.get('truncated') is False and preview.get('error_count') == 0
        result['inventory'] = {k: scalar(preview[k]) for k in ('examined_entries','max_entries','error_count','reparse_point_count') if k in preview}
        for k in ('eligible_file_count','excluded_count','unregistered_count','conflict_count'):
            if k in value: result[k] = scalar(value[k])
        result['inventory_counts_complete'] = result['inventory_complete']
        manifest = value.get('manifest', {})
        result['manifest_state'] = short_text(manifest.get('state'))
    result['next_check'] = 'registry-status --inventory --inventory-purpose <specific question> for bounded filesystem and manifest checks'
    return result


def integration_result_view(value):
    if not isinstance(value, dict):
        raise ValueError('invalid_integration_result')
    result = {'ok': value.get('ok') is True}
    if 'returncode' in value:
        result['returncode'] = scalar(value['returncode'])
    error = value.get('error')
    if error:
        code = error.get('code') if isinstance(error, dict) else None
        known = ('subprocess_failed','invalid_json_output','subprocess_launch_failed','child_reported_failure','invalid_vault','missing_config','invalid_config','configured_vault_mismatch','missing_dependencies','integration_error')
        result['error'] = {'code': code if code in known else 'integration_error'}
    return result


def transaction_result_view(value):
    """Preserve exact review material and recovery locators; omit raw child logs."""
    if not isinstance(value, dict):
        raise ValueError('invalid_transaction_result')
    result = dict(value)
    for key in ('error', 'rollback_errors'):
        result.pop(key, None)
    if 'error' in value:
        result['error_code'] = 'transaction_failed'
    if 'rollback_errors' in value:
        if not isinstance(value['rollback_errors'], list):
            raise ValueError('invalid_rollback_errors')
        result['rollback_error_count'] = len(value['rollback_errors'])
    for name in ('post_move_check', 'post_relink_check'):
        child = value.get(name)
        if isinstance(child, dict):
            projected = {'ok': child.get('ok') is True}
            if isinstance(child.get('vault_index_refresh'), dict):
                projected['vault_index_refresh'] = integration_result_view(child['vault_index_refresh'])
            if isinstance(child.get('pdf_index_verification'), dict):
                projected['pdf_index_verification'] = library_status_view(child['pdf_index_verification'])
            result[name] = projected
    if isinstance(value.get('vault_index_refresh'), dict):
        result['vault_index_refresh'] = integration_result_view(value['vault_index_refresh'])
    if isinstance(value.get('pdf_index_verification'), dict):
        result['pdf_index_verification'] = library_status_view(value['pdf_index_verification'])
    state = value.get('state')
    if state in ('rolled_back', 'partial_write'):
        # The original actions/context and hash are recovery evidence, not a new plan.
        result.update(ok=False, status=state, diagnostic_view='transaction_result',
                      business_outcome=state, do_not_retry_automatically=True)
    return result


def configuration_result_view(value):
    """A committed local configuration plus a possibly failed receiver setup."""
    config = value.get('config', {})
    if not isinstance(config, dict):
        raise ValueError('invalid_written_configuration')
    saved = {k: scalar(config[k]) for k in ('schema_version',) if k in config}
    for k in ('knowledge_root','knowledge_root_id'):
        if k in config: saved[k] = short_text(config[k])
    saved['sources'] = {}
    for name in ('vault','library'):
        row = config.get('sources', {}).get(name)
        if row is not None:
            saved['sources'][name] = {k: short_text(row[k]) for k in ('kind','relative_path') if k in row}
    result = {'ok': value.get('ok') is not False, 'status': 'configured',
              'diagnostic_view': 'configuration_result', 'configuration_persisted': True,
              'config': saved}
    if 'config_path' in value: result['config_path'] = short_text(value['config_path'])
    if isinstance(value.get('obsidian'), dict):
        result['obsidian'] = integration_result_view(value['obsidian'])
        if result['obsidian']['ok'] is not True:
            result.update(ok=False, status='configured_with_obsidian_error',
                          business_outcome='configuration_written_child_failed',
                          do_not_retry_automatically=True,
                          recovery='The local configuration was saved. Inspect the receiver setup failure and repair that step; do not repeat configure as though nothing was written.')
    return result


def paper_locate_preflight_view(value):
    """Rebuild the owned empty preflight failure contract, never raw exceptions."""
    messages = {
        'missing_configuration': 'The personal knowledge root is not configured.',
        'search_format_rebuild_required': 'The PDF search format is outdated; run `index --resume` to rebuild FTS from existing extracted page text.',
        'missing_index': 'The configured PDF index is missing; run `index --resume`.',
        'library_source_unavailable': 'The configured PDF library is unavailable or does not match its index.',
        'preflight_failed': 'The configured PDF library could not pass the bounded locator preflight.',
    }
    code = value.get('error', {}).get('code')
    if code not in messages:
        raise ValueError('invalid_locator_preflight_code')
    return {
        'schema_version':'paper-locate/v1', 'canonicalizer_version':'paper-canonical/v1',
        'ok':False, 'status':'failed', 'route':'explicit-index',
        'diagnostic_view':'paper_locate_preflight',
        'query':{'query_type':None,'hard_concepts':[],'signature_terms':[]},
        'search':{'alias_mode':None,'expanded':False,'candidate_count':0,'candidate_pdf_count':0,
                  'verified_page_count':0,'adjacent_page_count':0,'stop_reason':code},
        'results':[],
        'coverage':{'document_status_counts':{},'incomplete':True,'diagnostics_summary':{},'warnings':[]},
        'timing_ms':{'core':0,'expanded':0,'verify':0,'total':0},
        'managed_by':'manage-personal-knowledge',
        'error':{'code':code,'message':messages[code]},
    }


def library_search_view(value):
    diagnostics = value.get('diagnostics', {})
    fields = ('title','path','page_number','snippet','score','reasons','matched_aliases','status','method','warning','resource_id')
    rows = value.get('results', [])
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError('invalid_library_results')
    return {'query':value.get('query'), 'aliases':value.get('aliases', []),
            'results':[{k: row[k] for k in fields if k in row} for row in rows],
            'diagnostics':{'coverage':library_status_view(diagnostics.get('coverage', {})),
                           'matched_aliases':diagnostics.get('matched_aliases', []),
                           'unmatched_aliases':diagnostics.get('unmatched_aliases', []),
                           'incomplete':diagnostics.get('incomplete') is not False,
                           'warning':'Search coverage is incomplete; do not infer a library-wide absence.' if diagnostics.get('incomplete') is not False else None}}


def paper_search_result_view(value):
    """Keep the delegated public evidence package, not its process wrapper."""
    if not isinstance(value, dict):
        raise ValueError('invalid_paper_search_result')
    ok = value.get('ok') is True
    data = value.get('data')
    if data is not None and not isinstance(data, dict):
        raise ValueError('invalid_paper_search_package')
    if ok and data is None:
        raise ValueError('missing_paper_search_package')
    result = {'ok':ok,'status':'completed' if ok else 'failed','operation':'paper_search',
              'diagnostic_view':'paper_search_result','data':data}
    if 'returncode' in value: result['returncode'] = scalar(value['returncode'])
    error = value.get('error')
    if error:
        code = error.get('code') if isinstance(error, dict) else None
        known = ('empty_query','missing_db','invalid_output_mode','invalid_limit','missing_dependencies','subprocess_failed','invalid_json_output','subprocess_launch_failed','child_reported_failure','downstream_diagnostics','integration_error')
        result['error'] = {'code':code if code in known else 'integration_error'}
    if not ok:
        result['recovery'] = 'Inspect the retained search status and coverage. Repair the named dependency or index condition before retrying; a failed search does not prove absence.'
    return result


def library_index_view(value):
    if not isinstance(value, dict):
        raise ValueError('invalid_index_result')
    result = {k:scalar(value[k]) for k in ('selected','processed','indexed','no_text','errors','progress_events','remaining') if k in value}
    inventory = value.get('inventory', {})
    fields = ('found','new','changed','unchanged','pruned','scan_success','root_changed','identity_matches','index_reused_after_root_move')
    result['inventory'] = {k:scalar(inventory[k]) for k in fields if k in inventory}
    errors = inventory.get('scan_errors', [])
    if not isinstance(errors, list):
        raise ValueError('invalid_inventory_errors')
    count = len(errors) if 'scan_errors' in inventory else inventory.get('scan_error_count', 0)
    if type(count) is not int or count < 0:
        raise ValueError('invalid_inventory_error_count')
    result['inventory']['scan_error_count'] = count
    result['status'] = library_status_view(value.get('status', {}))
    upgrade = value.get('search_format_upgrade', {})
    result['search_format_upgrade'] = {k:scalar(upgrade[k]) for k in ('changed','rebuilt_pages','preserves_extracted_text') if k in upgrade}
    for k in ('from_version','to_version'):
        if k in upgrade: result['search_format_upgrade'][k] = short_text(upgrade[k])
    if 'journal_mode' in value:
        result['journal_mode'] = value['journal_mode'] if value['journal_mode'] in ('delete','wal','truncate','persist','memory','off',None) else 'unknown'
    return result


def question_coverage_view(value):
    if not isinstance(value, dict):
        raise ValueError('invalid_question_coverage')
    result = {k:scalar(value[k]) for k in ('ok','root_matches_config','docs','questions') if k in value}
    result['coverage_complete'] = value.get('coverage_complete') is True
    result['status'] = 'ready' if result['coverage_complete'] else 'coverage_gap'
    result['schema_version'] = 'mpk-question-federation/v1'
    scope = value.get('coverage_scope', {})
    result['coverage_scope'] = {k:scalar(scope[k]) for k in ('vault_registered_sources','discovered_external_collections') if k in scope}
    if 'knowledge_root_discovery_relative' in scope:
        result['coverage_scope']['knowledge_root_discovery_relative'] = short_text(scope['knowledge_root_discovery_relative'])
    legacy = value.get('document_index', {})
    collections = value.get('question_collections', {})
    for name, row in (('document_index',legacy),('question_collections',collections)):
        result[name] = {k:scalar(row[k]) for k in ('ok','root_matches_config','coverage_complete','docs','documents','questions','collection_count') if k in row}
    unimported = value.get('discovered_unimported', [])
    if not isinstance(unimported, list):
        raise ValueError('invalid_unimported_collections')
    count = len(unimported) if 'discovered_unimported' in value else value.get('discovered_unimported_count',0)
    if type(count) is not int or count < 0:
        raise ValueError('invalid_unimported_count')
    result['discovered_unimported_count'] = count
    discovery = collections.get('discovery', value.get('discovery', {}))
    result['discovery'] = {k:scalar(discovery[k]) for k in ('ok','truncated','discovery_complete') if k in discovery}
    errors = discovery.get('errors', [])
    if not isinstance(errors, list):
        raise ValueError('invalid_question_discovery_errors')
    result['discovery']['error_count'] = len(errors) if 'errors' in discovery else scalar(discovery.get('error_count',0))
    result['details_command'] = 'question-status or question-discover for source inventory and coverage details'
    return result


def question_index_result_view(value):
    """Called only after the document indexer has returned from its transaction."""
    if not isinstance(value, dict):
        raise ValueError('invalid_question_index_result')
    ok = value.get('ok') is True
    errors = value.get('scan_errors', [])
    if not isinstance(errors, list):
        raise ValueError('invalid_question_index_errors')
    count = len(errors) if 'scan_errors' in value else value.get('scan_error_count', 0)
    if type(count) is not int or count < 0:
        raise ValueError('invalid_question_index_error_count')
    result = {'ok':ok,'status':'indexed' if ok else 'coverage_gap',
              'diagnostic_view':'question_index_result','index_committed':True,
              'source_scan_complete':ok,'source_relative':short_text(value.get('source_relative')),
              'scan_error_count':count,
              'business_outcome':'index_committed' if ok else 'index_committed_with_coverage_gap'}
    for key in ('indexed_docs','unchanged_docs','questions_accounted','source_files_discovered'):
        if key in value: result[key] = scalar(value[key])
    if not ok:
        result['do_not_retry_automatically'] = True
        result['recovery'] = 'The derived index was updated, but this source scan is incomplete. Inspect the named source and repair scan conditions before continuing; do not infer complete federation coverage.'
    return result


def question_inventory_options(args):
    for name, default, maximum in (('max_entries',50000,1000000),('max_directories',5000,100000)):
        value = getattr(args, name, default)
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError('invalid_question_discovery_budget')
    limit = getattr(args, 'inventory_limit', 20)
    offset = getattr(args, 'inventory_offset', 0)
    if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
        raise ValueError('invalid_question_inventory_page')
    include = getattr(args, 'inventory', False)
    if include:
        purpose = getattr(args, 'inventory_purpose', None)
        if not isinstance(purpose, str) or not purpose.strip() or len(purpose) > 512:
            raise ValueError('question_inventory_purpose_required')
    return include, limit, offset


def _question_inventory_page(rows, limit, offset):
    if not isinstance(rows, list):
        raise ValueError('invalid_question_inventory')
    selected = []
    for row in rows[offset:offset + limit]:
        if not isinstance(row, dict):
            raise ValueError('invalid_question_inventory_row')
        item = {k:short_text(row[k]) for k in ('collection_id','source_relative','relative_path','display_name','source_format','verification_status') if k in row}
        item.update({k:scalar(row[k]) for k in ('registered','scan_complete','document_count','question_count') if k in row})
        errors = row.get('scan_errors', [])
        if not isinstance(errors, list): raise ValueError('invalid_question_scan_errors')
        item['scan_error_count'] = len(errors) if 'scan_errors' in row else row.get('scan_error_count', 0)
        selected.append(item)
    return {'items':selected, 'total':len(rows), 'offset':offset, 'limit':limit, 'has_more':offset + len(selected) < len(rows)}


def question_discovery_view(value, *, limit=20, offset=0):
    if not isinstance(value, dict): raise ValueError('invalid_question_discovery')
    result = {'ok':value.get('ok') is True, 'diagnostic_view':'question_discovery',
              'status':value.get('status') if value.get('status') in ('complete','coverage_gap','discovery_root_missing') else 'coverage_gap',
              'discovery_relative':short_text(value.get('discovery_relative')),
              'discovery_complete':value.get('discovery_complete') is True,
              'truncated':value.get('truncated') is True}
    errors = value.get('errors', [])
    if not isinstance(errors, list): raise ValueError('invalid_question_discovery_errors')
    result['error_count'] = len(errors) if 'errors' in value else value.get('error_count', 0)
    if 'candidate_page' in value:
        page = value['candidate_page']
        # Revalidate selected fields without treating a partial page as the inventory.
        projected = _question_inventory_page(page['items'], 100, 0)
        result['candidate_page'] = {**projected, **{k:page[k] for k in ('total','offset','limit','has_more')}}
    else:
        result['candidate_page'] = _question_inventory_page(value.get('candidates', []), limit, offset)
    result['coverage_note'] = 'Counts describe the scanned scope only; an incomplete scan cannot establish absence.'
    return result


def question_status_view(value, *, include_inventory=False, limit=20, offset=0):
    result = question_coverage_view(value)
    result['diagnostic_view'] = 'question_status'
    legacy = value.get('document_index', {})
    for key in ('database_exists','schema_initialized'):
        if key in legacy: result['document_index'][key] = legacy[key] is True
    discovery = value.get('question_collections', {}).get('discovery', value.get('discovery', {}))
    status = discovery.get('status')
    if status in ('complete','coverage_gap','discovery_root_missing'):
        result['discovery']['status'] = status
    if 'inventory' in value:
        result['inventory'] = {}
        for name in ('vault_sources','registered_collections','unimported_collections'):
            page = value['inventory'][name]
            projected = _question_inventory_page(page['items'], 100, 0)
            result['inventory'][name] = {**projected, **{k:page[k] for k in ('total','offset','limit','has_more')}}
    elif include_inventory:
        result['inventory'] = {
            'vault_sources':_question_inventory_page(value.get('sources', []), limit, offset),
            'registered_collections':_question_inventory_page(value.get('question_collections', {}).get('collections', []), limit, offset),
            'unimported_collections':_question_inventory_page(value.get('discovered_unimported', []), limit, offset)}
    result['details_command'] = 'question-status --inventory --inventory-purpose PURPOSE --inventory-limit 20 --inventory-offset 0; or question-discover with inventory pagination'
    return result


def ocr_one_view(value):
    if not isinstance(value, dict): raise ValueError('invalid_ocr_result')
    ok = value.get('ok') is True
    status = value.get('status')
    result = {'ok':ok, 'diagnostic_view':'ocr_one',
              'status':status if status in ('completed','failed','preflight_failed','invalid_request') else 'failed'}
    for key in ('input','output','sidecar'):
        if key in value: result[key] = short_text(value[key])
    if 'returncode' in value: result['returncode'] = scalar(value['returncode'])
    if 'languages' in value:
        languages = value['languages']
        if not isinstance(languages, list) or len(languages)>32: raise ValueError('invalid_ocr_languages')
        result['languages'] = [short_text(item) for item in languages]
    error = value.get('error')
    if error:
        known = ('invalid_input','invalid_output','input_equals_output','unsafe_sidecar','output_exists','missing_output_directory','library_write_refused','invalid_languages','requested_language_missing','ocr_not_ready','missing_ocr_outputs','ocr_process_failed','subprocess_failed','subprocess_launch_failed')
        code = error.get('code') if isinstance(error, dict) else None
        result['error'] = {'code':code if code in known else 'ocr_process_failed'}
    if isinstance(value.get('preflight'), dict): result['preflight'] = ocr_preflight_view(value['preflight'])
    if not ok:
        result['do_not_retry_automatically'] = True
        result['recovery'] = 'Check the retained request or preflight condition. If an OCR process ran, inspect the explicit output and sidecar paths before retrying; failure does not establish that no files were created.'
    return result


AUDIT_CATEGORIES = ('unregistered','missing','metadata_drift','duplicate_candidates','manual_move_candidates','weak_manual_move_candidates')

def resource_audit_view(value, *, category=None, limit=20, offset=0, member_offset=0):
    if category is not None and category not in AUDIT_CATEGORIES: raise ValueError('invalid_audit_category')
    if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0 or type(member_offset) is not int or member_offset < 0:
        raise ValueError('invalid_audit_page')
    counts = {}
    for name in AUDIT_CATEGORIES:
        rows = value.get(name, [])
        if not isinstance(rows,list): raise ValueError('invalid_audit_rows')
        counts[name] = len(rows)
    result = {'ok':True,'scanned_files':scalar(value.get('scanned_files')),
              'truncated':value.get('truncated') is True, 'category_counts':counts,
              'manifest_state':short_text(value.get('manifest',{}).get('state')),
              'note_references':value.get('note_references',{}),
              'count_scope':'Observed inventory and registered metadata; truncation does not prove absence. Weak move candidates retain the producer cap of 200.',
              'details_command':'resource-audit --category CATEGORY --diagnostic-limit 20 --diagnostic-offset 0; duplicate groups also accept --member-offset'}
    if category is not None:
        rows = value.get(category,[])
        selected=[]
        for row in rows[offset:offset+limit]:
            if category in ('unregistered','missing'):
                selected.append(short_text(row));continue
            item={k:short_text(row[k]) for k in ('resource_id','relative_path','old_relative_path','candidate_relative_path','sha256','identity_strength') if k in row}
            if 'requires_confirmation' in row: item['requires_confirmation']=row['requires_confirmation'] is True
            if 'signals' in row: item['signals']=[x for x in row['signals'] if x in ('size','file_name','mtime_ns')]
            if category=='duplicate_candidates':
                ids=row.get('resource_ids',[])
                if not isinstance(ids,list):raise ValueError('invalid_duplicate_members')
                item['resource_ids']=[short_text(x) for x in ids[member_offset:member_offset+limit]]
                item['member_page']={'total':len(ids),'offset':member_offset,'limit':limit,'has_more':member_offset+limit<len(ids)}
            selected.append(item)
        result['category']=category
        result['items']=selected
        result['page']={'total':len(rows),'offset':offset,'limit':limit,'has_more':offset+limit<len(rows)}
    return result


def registry_configuration_view(config):
    result = configuration_result_view({'config':config})['config']
    if 'knowledge_root_name' in config: result['knowledge_root_name'] = short_text(config['knowledge_root_name'])
    registry = config.get('registry',{})
    if not isinstance(registry,dict): raise ValueError('invalid_registry_configuration')
    result['registry'] = {}
    if 'manifest_relative_path' in registry:
        result['registry']['manifest_relative_path'] = short_text(registry['manifest_relative_path'])
    if 'excluded_relative_paths' in registry:
        paths = registry['excluded_relative_paths']
        if not isinstance(paths,list): raise ValueError('invalid_registry_exclusions')
        # These exact exclusions were accepted as part of the migration plan.
        result['registry']['excluded_relative_paths'] = [short_text(path) for path in paths]
    return result
