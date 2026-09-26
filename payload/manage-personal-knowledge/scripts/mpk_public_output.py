"""Bounded owned JSON CLI returns; interactive human terminals are a separate route."""
from __future__ import annotations
import argparse
from contextlib import redirect_stdout, redirect_stderr
from functools import wraps
import io
import json
import sys
from mpk_views import configuration_view, library_status_view, ocr_preflight_view, suite_status_view, transaction_result_view, configuration_result_view, paper_locate_preflight_view, paper_search_result_view, question_index_result_view, question_status_view, question_discovery_view, ocr_one_view

DEFAULT_LIMIT = 65536
SAFE_STATUS = frozenset({'error','setup_required','not_configured','not_initialized','missing_root','missing_source','invalid_config','attention_required','confirmation_required','ambiguous','blocked','cancelled','coverage_gap','missing_dependency','missing_skill_payload','not_ready','missing_dependencies'})


class RequestError(ValueError):
    pass


class SafeParser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        kwargs['prog'] = 'manage-personal-knowledge'
        super().__init__(*args, **kwargs)

    def error(self, message):
        raise RequestError('invalid_cli_arguments')


class Capture(io.StringIO):
    def __init__(self, ceiling):
        super().__init__()
        self.ceiling = ceiling
        self.bytes_seen = 0
        self.overflow = False

    def write(self, text):
        self.bytes_seen += len(text.encode('utf-8'))
        if self.bytes_seen <= self.ceiling:
            super().write(text)
        else:
            self.overflow = True
        return len(text)


def options(argv):
    result=[];limit=DEFAULT_LIMIT;reason=None;seen=set();i=0
    while i<len(argv):
        arg=argv[i]
        if arg=='--':
            result.extend(argv[i:]);break
        key,sep,value=arg.partition('=')
        if key not in ('--max-response-bytes','--response-reason'):
            result.append(arg);i+=1;continue
        if key in seen:raise RequestError('duplicate_output_option')
        seen.add(key)
        if not sep:
            i+=1
            if i>=len(argv):raise RequestError('missing_output_option')
            value=argv[i]
        if key=='--max-response-bytes':
            limit=int(value)
            if not 4096<=limit<=1048576:raise RequestError('invalid_output_limit')
        else:
            if not value.strip() or len(value)>1024:raise RequestError('invalid_expansion_reason')
            reason=value
        i+=1
    if limit>DEFAULT_LIMIT and reason is None:raise RequestError('expansion_reason_required')
    return result,limit


def encode(value):
    return (json.dumps(value,ensure_ascii=False,allow_nan=False)+'\n').encode('utf-8')


def public_main(function=None, *, interactive=False):
    def decorate(func):
        @wraps(func)
        def wrapped(*args, **kwargs):
            original=sys.argv;code=2;limit=DEFAULT_LIMIT
            try:
                clean,limit=options(original[1:]);sys.argv=[original[0],*clean]
                help_requested='--help' in clean or '-h' in clean
                if interactive and sys.stdin.isatty():
                    # Local human prompts must stay visible. This route is not an AI JSON boundary.
                    return func(*args,**kwargs)
                if interactive and clean and clean[0] in ('setup','repair') and '--yes' not in clean and not help_requested:
                    raise RequestError('noninteractive_confirmation_required')
                out=Capture(limit);err=Capture(8192)
                with redirect_stdout(out),redirect_stderr(err):
                    try:
                        returned=func(*args,**kwargs);code=0 if returned is None else returned
                    except SystemExit as exc:
                        code=exc.code if type(exc.code) is int else 2
                        if code!=0 or not help_requested:raise RequestError('invalid_cli_exit')
                if out.overflow:
                    payload=encode({'ok':False,'code':'response_limit_exceeded','response_complete':False,'business_outcome':'success' if code==0 else 'failed','do_not_retry_automatically':True,'recovery':'Use a narrower view or justified output expansion; inspect current state before repeating a mutation.'})
                    code=2
                elif help_requested and code==0:
                    payload=out.getvalue().encode('utf-8')
                else:
                    value=json.loads(out.getvalue())
                    if not isinstance(value,dict):raise RequestError('invalid_owned_response')
                    if code!=0 or value.get('ok') is False:
                        status=value.get('status')
                        context={}
                        if value.get('diagnostic_view')=='ocr_one':
                            context=ocr_one_view(value)
                        if value.get('diagnostic_view')=='question_status':
                            context=question_status_view(value)
                        if value.get('diagnostic_view')=='question_discovery':
                            context=question_discovery_view(value)
                        if value.get('diagnostic_view')=='question_index_result':
                            context=question_index_result_view(value)
                        if value.get('diagnostic_view')=='paper_search_result':
                            context=paper_search_result_view(value)
                        if value.get('diagnostic_view')=='managed_paper_locate':
                            if value.get('schema_version') != 'paper-locate/v1':
                                raise RequestError('invalid_managed_locator_schema')
                            # The owned handler receives the validated adapter contract and
                            # enforces its managed package bound before this transport layer.
                            context={key:value[key] for key in ('schema_version','canonicalizer_version','ok','status','route','query','search','results','coverage','timing_ms','managed_by','error','diagnostic_view') if key in value}
                        if value.get('diagnostic_view')=='paper_locate_preflight':
                            context=paper_locate_preflight_view(value)
                        if value.get('diagnostic_view')=='configuration_result':
                            context=configuration_result_view(value)
                        if value.get('diagnostic_view')=='transaction_result':
                            context=transaction_result_view(value)
                        if value.get('diagnostic_view')=='reference_scan_limit':
                            scan_limit=value.get('max_scan_entries')
                            if type(scan_limit) is not int or not 1<=scan_limit<=1000000:
                                raise RequestError('invalid_scan_limit_status')
                            context={'diagnostic_view':'reference_scan_limit','scan_complete':False,'applied':False,
                                     'code':'reference_metadata_scan_limit','max_scan_entries':scan_limit,
                                     'recovery':'Increase --max-scan-entries within the approved scope or refresh one named note; no reference-cache write was applied.'}
                        if value.get('diagnostic_view')=='reference_body_limit':
                            examined_limit=value.get('max_examined_notes')
                            byte_limit=value.get('max_scan_bytes')
                            boundary=value.get('boundary')
                            if type(examined_limit) is not int or not 1<=examined_limit<=100000 or type(byte_limit) is not int or not 1<=byte_limit<=1073741824 or boundary not in ('body_bytes','examined_notes'):
                                raise RequestError('invalid_body_limit_status')
                            context={'diagnostic_view':'reference_body_limit','scan_complete':False,'applied':False,
                                     'code':'reference_body_scan_limit','boundary':boundary,
                                     'max_examined_notes':examined_limit,'max_scan_bytes':byte_limit,
                                     'recovery':'Increase the indicated explicit scan limit within the approved scope or refresh one named note; no reference-cache write was applied.'}
                        if value.get('diagnostic_view')=='suite_status':
                            context=suite_status_view(value)
                        if value.get('diagnostic_view')=='ocr_preflight':
                            context=ocr_preflight_view(value)
                        if status=='setup_required' or value.get('status_scope')=='configured_sources_and_library_index':
                            if isinstance(value.get('configuration'),dict):
                                context['configuration']=configuration_view(value['configuration'])
                            if isinstance(value.get('index_coverage'),dict):
                                context['index_coverage']=library_status_view(value['index_coverage'])
                        value={'ok':False,'status':status if isinstance(status,str) and status in SAFE_STATUS else 'error','code':'operation_failed','business_outcome':'failed','raw_diagnostics_returned':False,'do_not_retry_automatically':True,'recovery':'Check the selected source and operation prerequisites; do not automatically repeat writes.'}
                        value.update(context)
                        if code==0:code=2
                    elif err.bytes_seen:
                        value['local_diagnostics_omitted']=True
                    payload=encode(value)
                    if len(payload)>limit:
                        payload=encode({'ok':False,'code':'response_limit_exceeded','response_complete':False,'business_outcome':'success' if code==0 else 'failed','do_not_retry_automatically':True})
                        code=2
            except Exception:
                payload=encode({'ok':False,'code':'request_or_execution_failed','raw_diagnostics_returned':False,'business_outcome':'unknown','do_not_retry_automatically':True})
                code=2
            finally:
                sys.argv=original
            sys.stdout.write(payload.decode('utf-8'));sys.stdout.flush()
            return code
        return wrapped
    return decorate(function) if function is not None else decorate
