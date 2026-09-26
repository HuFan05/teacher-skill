"""Run one selected computation; retain complete large outputs without replay."""
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

INLINE_BYTES=16384

def _describe(destination):
    digest=hashlib.sha256()
    count=0
    with destination.open('rb') as source:
        while True:
            block=source.read(1024*1024)
            if not block:break
            digest.update(block)
            count+=len(block)
    return {'path':str(destination),'bytes':count,'sha256':digest.hexdigest(),'complete':True}

def run_computation(command, environment, result_parent=None, response_limit=65536):
    # File-backed capture keeps large results out of process memory and
    # diagnostics off the inherited terminal. These temporary files are local.
    parent=Path(result_parent).expanduser().resolve() if result_parent else None
    if parent is not None:parent.mkdir(parents=True,exist_ok=True)
    root=Path(tempfile.mkdtemp(prefix='computation-result-',dir=parent))
    out_path=root/'stdout.bin'; err_path=root/'stderr.bin'
    with out_path.open('x+b') as output, err_path.open('x+b') as diagnostic:
        try:
            process=subprocess.run(command,check=False,env=environment,stdout=output,stderr=diagnostic)
        except (OSError,subprocess.SubprocessError):
            return {'ok':False,'code':'computation_execution_interrupted','business_outcome':'unknown','do_not_retry_automatically':True,'job_id':root.name,'result_directory':str(root),'result_complete':False,'raw_diagnostics_returned':False}
        size=output.tell(); diagnostic_size=diagnostic.tell()
        result={'ok':process.returncode==0,'job_id':root.name,'child_exit_code':process.returncode,'business_outcome':'success' if process.returncode==0 else 'failed','do_not_retry_automatically':True,'stdout_bytes':size,'stderr_bytes':diagnostic_size,'raw_diagnostics_returned':False}
        if process.returncode==0 and size<=INLINE_BYTES:
            output.seek(0)
            try:value=output.read().decode('utf-8')
            except UnicodeError:value=None
            if value is not None:
                try:
                    # Decimal/exponent tokens must not pass through binary floats.
                    # Returning the exact UTF-8 text preserves every numeric lexeme,
                    # including nested values and exponent underflow.
                    parsed=json.loads(value,parse_float=lambda _:(_ for _ in ()).throw(ValueError()),parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
                    # Reject lone surrogates or other non-wire-safe JSON strings.
                    json.dumps(parsed,ensure_ascii=False,allow_nan=False).encode('utf-8')
                except (ValueError,TypeError,UnicodeError,RecursionError):
                    result.update(result=value,result_format='text',result_complete=True)
                else:
                    result.update(result=parsed,result_format='json',result_complete=True)
                if not diagnostic_size and len(json.dumps({**result,'response_complete':True},ensure_ascii=False,allow_nan=False).encode('utf-8'))+1<=response_limit:
                    output.close(); diagnostic.close()
                    out_path.unlink(); err_path.unlink(); root.rmdir()
                    return result
                if len(json.dumps(result,ensure_ascii=False,allow_nan=False).encode('utf-8'))+1>response_limit:
                    result.pop('result',None)
                    result.pop('result_format',None)
        # Only create retained artifacts when inline delivery is insufficient
        # or diagnostics/failure need a recoverable local record.
        output.flush(); diagnostic.flush()
        result['artifacts']={'stdout':_describe(out_path),'stderr':_describe(err_path)}
        result['result_directory']=str(root)
        result['result_complete']=True
        result['result_inlined']='result' in result
        result['hint']='Complete local output retained. Select the needed result evidence; do not rerun the computation because output was not inlined. Diagnostic artifacts require explicit scoped inspection.'
        return result
