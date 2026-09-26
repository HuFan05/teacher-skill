"""Operation-specific backend views; no I/O and no mutation of cached facts."""
import re

STATES=frozenset(('available','unavailable','probe_failed','not_requested','unsupported_platform','wsl_unavailable','requires_agent_probe','session_probe_required'))
LIBRARIES=('numpy','scipy','sympy','pandas','sklearn','statsmodels','hypothesis','torch','jax','jaxlib','tensorflow','z3','cvc5','mpmath')
TOOL_GROUPS={
    'smt_solvers':('z3','cvc5'),
    'proof_assistants':('lean','coq','isabelle'),
    'model_checkers':('spin','cbmc'),
    'profilers':('hyperfine','py_spy','perf','nsys','ncu'),
}
BACKENDS=('python','accelerators',*TOOL_GROUPS)
CAPABILITIES=('numpy_linalg','scipy_stats','sympy_diff','mpmath_iv','z3_smt','hypothesis_pbt','torch_cpu','torch_cuda','torch_mps','jax_default','tensorflow_default')
MAX_GPUS=16

def text(value, limit=256):
    if not isinstance(value,str) or len(value)>limit:
        return None
    try:value.encode('utf-8')
    except UnicodeError:return None
    return value

def fields(value, names, limit=256):
    if not isinstance(value,dict):return {}
    return {k:text(value[k],limit) for k in names if k in value and text(value[k],limit) is not None}

def integer(value):
    return value if type(value) is int and -(2**31)<=value<=2**63-1 else None

def diagnostic(value):
    return '' if value in (None,'') else 'diagnostic_suppressed'

def status(value):
    return value if isinstance(value,str) and value in STATES else 'unavailable'

def capability(value):
    value=value if isinstance(value,dict) else {}
    out=fields(value,('capability','version','source','device'))
    out['status']=status(value.get('status'))
    out['smoke_test']=value.get('smoke_test') is True
    if out['status']!='available':out['smoke_test']=False
    out['error']=diagnostic(value.get('error'))
    if 'vendor_root' in value:out['vendor_root']=text(value['vendor_root'],4096)
    return out

def backend(value):
    value=value if isinstance(value,dict) else {}
    out=fields(value,('requested_command','path'),4096)
    if 'path' in value and value['path'] is None:out['path']=None
    out.update(fields(value,('discovery_source','distro','version','manager','toolchain')))
    out['status']=status(value.get('status'))
    code=value.get('exit_code')
    out['exit_code']=code if type(code) is int and -(2**31)<=code<=2**32-1 else None
    out['error']=diagnostic(value.get('error'))
    # A version banner can contain unrelated startup output. Return only a
    # version-shaped token, never the banner or a raw diagnostic fallback.
    if 'version_output' in value:
        banner=text(value['version_output'],8192)
        match=re.search(r'(?<![\w.])\d{1,4}(?:\.\d{1,4}){1,4}(?![\w.])',banner or '')
        out['version_output']=match.group() if match else ''
        out['version_banner_returned']=False
    return out

def _strings(value, limit=16, size=256):
    if not isinstance(value,list):return []
    return [item for item in (text(x,size) for x in value[:limit]) if item is not None]

def framework(value):
    value=value if isinstance(value,dict) else {}
    out=fields(value,('version','cuda_built','hip_built','cudnn_version','default_backend'))
    out['status']=status(value.get('status'))
    for key in ('cuda_available','mps_built','mps_available'):
        if key in value:out[key]=value.get(key) is True
    if 'device_count' in value:out['device_count']=integer(value.get('device_count'))
    for key in ('platforms','device_types'):
        if key in value:out[key]=_strings(value.get(key))
    out['error']=diagnostic(value.get('error'))
    return out

def accelerators(value):
    value=value if isinstance(value,dict) else {}
    smi=value.get('nvidia_smi')
    smi=smi if isinstance(smi,dict) else {}
    out={'nvidia_smi':backend(smi)}
    gpus=smi.get('gpus')
    gpus=gpus if isinstance(gpus,list) else []
    out['nvidia_smi']['gpus']=[{**fields(g,('name','driver_version')),'memory_total_mib':integer(g.get('memory_total_mib'))} for g in gpus[:MAX_GPUS] if isinstance(g,dict)]
    out['nvidia_smi']['gpu_count']=len(gpus)
    out['nvidia_smi']['gpus_complete']=len(gpus)<=MAX_GPUS
    frameworks=value.get('frameworks')
    frameworks=frameworks if isinstance(frameworks,dict) else {}
    out['frameworks']={name:framework(frameworks.get(name)) for name in ('torch','jax','tensorflow')}
    out['apple_silicon']=value.get('apple_silicon') is True
    return out

def tool_group(value, names):
    value=value if isinstance(value,dict) else {}
    return {name:backend(value.get(name)) for name in names}

def local_inventory(value, guidance, include_mcp=False):
    value=value if isinstance(value,dict) else {}
    out=fields(value,('schema_version','probed_at_utc'))
    out['host']=fields(value.get('host'),('system','architecture','python_implementation','powershell_edition'))
    hardware=value.get('hardware')
    hardware=hardware if isinstance(hardware,dict) else {}
    out['hardware']={'logical_cpus':integer(hardware.get('logical_cpus')),'memory_bytes':integer(hardware.get('memory_bytes')),**fields(hardware,('cpu_model',))}
    if 'python' in value or guidance:
        py=value.get('python')
        py=py if isinstance(py,dict) else {}
        out['python']=backend(py)
        libs=py.get('libraries')
        libs=libs if isinstance(libs,dict) else {}
        out['python']['libraries']={}
        for name in LIBRARIES:
            if name not in libs and name not in guidance:continue
            item=libs.get(name)
            item=item if isinstance(item,dict) else {}
            out['python']['libraries'][name]={'available':item.get('available') is True,'version':text(item.get('version')),**guidance.get(name,{})}
        if 'wsl' in py:out['python']['wsl']=backend(py.get('wsl'))
    if 'accelerators' in value:
        out['accelerators']=accelerators(value.get('accelerators'))
    for group,names in TOOL_GROUPS.items():
        if group in value:
            out[group]=tool_group(value.get(group),names)
    if include_mcp:
        out['mcp']={'status':'requires_agent_probe','evidence':'Current session discovery and a live check of the selected MCP tool are required.'}
    return out
