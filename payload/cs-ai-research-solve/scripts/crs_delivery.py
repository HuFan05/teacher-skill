"""Resume one complete derived-reading upgrade without mutating formal research.

The candidate, browser evidence, visual review, and guarded publication form
one operation. No pending browser state is a successful full-upgrade result.
"""
import hashlib
import html
import json
import os
import shutil
import subprocess
import uuid
import stat
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

VERSION='v1.0.0'
NAVIGATION='crs-project-navigation/v1'
SESSION='upgrade-session.json'


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def need(condition,message):
    if not condition:raise ValueError(message)


@contextmanager
def operation_lock(out):
    """One Windows process owns a candidate operation; crash releases ownership."""
    if os.name!='nt':
        yield;return
    import ctypes
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateMutexW.argtypes=[wintypes.LPVOID,wintypes.BOOL,wintypes.LPCWSTR];kernel.CreateMutexW.restype=wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD];kernel.WaitForSingleObject.restype=wintypes.DWORD
    kernel.ReleaseMutex.argtypes=[wintypes.HANDLE];kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    identity=hashlib.sha256(os.path.normcase(str(Path(out).resolve())).encode('utf-8')).hexdigest()
    handle=kernel.CreateMutexW(None,False,'Local\\CRSReadingUpgrade_'+identity)
    if not handle:raise ctypes.WinError(ctypes.get_last_error())
    owned=False
    try:
        result=kernel.WaitForSingleObject(handle,0)
        need(result in {0,0x80},'This exact upgrade operation is already running; retain it and resume after its owner exits.')
        owned=True;yield
    finally:
        if owned:kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


def write(path,value):
    data=json.dumps(value,ensure_ascii=False,indent=2)+'\n'
    tmp=path.with_suffix(path.suffix+'.pending')
    if tmp.exists():
        # The committed state remains authoritative. Preserve a torn previous
        # write for diagnosis without letting its fixed temporary name block.
        tmp.rename(tmp.with_name(tmp.name+'.interrupted-'+uuid.uuid4().hex))
    with tmp.open('x',encoding='utf-8',newline='') as stream:
        stream.write(data);stream.flush();os.fsync(stream.fileno())
    os.replace(tmp,path)


def preserve_interrupted(path,archive=None):
    """Move only this operation's private artifact, retaining failed bytes."""
    path=Path(path)
    if path.exists():
        parent=Path(archive) if archive else path.parent
        parent.mkdir(parents=True,exist_ok=True)
        path.rename(parent/(path.name+'.interrupted-'+uuid.uuid4().hex))


def prepare_entrance(path,text,state,session_path):
    """Only a flushed, complete preparation file acquires its final name."""
    expected=hashlib.sha256(text.encode('utf-8')).hexdigest()
    if path.exists():
        need(digest(path)==expected,'Prepared entrance changed during resume; preserve it for diagnosis.')
        return
    prepared=state.setdefault('prepared_entrances',{})
    need(path.name not in prepared or prepared[path.name]==expected,'Registered entrance preparation differs from the current source binding.')
    if path.name not in prepared:prepared[path.name]=expected;write(session_path,state)
    temp=path.with_name(path.name+'.prepare-pending')
    preserve_interrupted(temp,session_path.parent/'recovery-evidence')
    with temp.open('xb') as stream:
        stream.write(text.encode('utf-8'));stream.flush();os.fsync(stream.fileno())
    temp.rename(path)


def copy_complete(source,dest,history=None):
    """Never expose a partial final file; restart an owned partial copy."""
    source=Path(source);dest=Path(dest);expected=digest(source)
    if dest.exists():
        need(digest(dest)==expected,'Publication bytes differ; preserve both versions.')
        return
    temp=dest.with_name(dest.name+'.crs-copy-pending')
    if temp.exists():
        # A valid complete temporary copy can be promoted after a crash.
        if digest(temp)!=expected:preserve_interrupted(temp,history)
    if not temp.exists():
        with source.open('rb') as src,temp.open('xb') as dst:
            shutil.copyfileobj(src,dst);dst.flush();os.fsync(dst.fileno())
    need(digest(temp)==expected,'Source changed during publication copy.')
    # Windows rename refuses a concurrent destination. Never replace it.
    need(os.name=='nt','Guarded publication currently requires Windows.')
    temp.rename(dest)


@contextmanager
def exclusive_entrance(path,create=False):
    """Windows kernel share denial spans read, check, backup and write.

    Other editors and renames are denied for the entire critical interval.
    This is not a cooperative lock-file convention or a check/replace race.
    """
    need(os.name=='nt','Guarded entrance publication currently requires Windows.')
    import ctypes,msvcrt
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    kernel.CreateFileW.restype=wintypes.HANDLE
    # OPEN_REPARSE_POINT prevents following a raced symbolic link.
    handle=kernel.CreateFileW(str(path),0x80000000|0x40000000,0,None,1 if create else 3,0x80|0x00200000,None)
    if handle==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
    fd=msvcrt.open_osfhandle(handle,os.O_RDWR|os.O_BINARY)
    with os.fdopen(fd,'r+b',buffering=0) as stream:
        info=os.fstat(stream.fileno())
        need(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and not (getattr(info,'st_file_attributes',0)&0x400),'Linked or nonregular entrance is forbidden.')
        yield stream


def publish_entrance(root,name,text,backup,state,session_path):
    """Atomically transfer whole files; never write into an existing entrance.

    The old file is moved to a registered backup then held exclusively while
    the complete new file is renamed into the vacant name. A concurrent new
    entrance is never replaced. Recovery uses whole-file hashes, never prefixes.
    """
    target=safe_path(root,name);data=text.encode('utf-8');expected=hashlib.sha256(data).hexdigest()
    prior=state['entry_before'][name]
    if name in state['published_entries']:
        need(target.is_file() and digest(target)==expected,'A published entrance changed; preserve concurrent work.')
        return
    backup.mkdir(parents=True,exist_ok=True);saved=safe_path(backup,name);temp=safe_path(backup,'.new-'+name)
    intent={'name':name,'sha256':expected,'before':prior}
    existing_intent=state.get('entrance_transfer')
    need(existing_intent is None or existing_intent==intent,'Finish the registered entrance transfer before another entry.')
    if existing_intent is None:
        current=digest(target) if target.exists() else None
        need(current==expected or current==prior,'Entrance changed before transfer; preserve concurrent work.')
        if current==expected:
            if name not in state['published_entries']:state['published_entries'].append(name)
            write(session_path,state);return
        need(not saved.exists(),'Unregistered entrance backup collision.')
        state['entrance_transfer']=intent;write(session_path,state)
    # This source is the already prepared, exact entrance in the private output.
    copy_complete(session_path.parent/name,temp,backup/'recovery-evidence')
    if prior is not None:
        if not saved.exists():
            need(target.exists(),'Registered old entrance and backup are both missing.')
            target.rename(saved)  # Does not follow an alias or overwrite anything.
        try:
            with exclusive_entrance(saved) as original:
                need(hashlib.sha256(original.read()).hexdigest()==prior,'Original entrance changed; retain the moved bytes and concurrent work.')
                if target.exists():need(digest(target)==expected,'A concurrent new entrance occupies the target; it was preserved.')
                else:temp.rename(target)
        except (OSError,ValueError):
            # Restore visibility when possible, without replacing a new file.
            if not target.exists() and saved.exists():saved.rename(target)
            raise
    else:
        if target.exists():need(digest(target)==expected,'A concurrent new entrance occupies the target; it was preserved.')
        else:temp.rename(target)
    if temp.exists():
        # A crash after a successful rename can cause an identical prepared
        # temporary to be regenerated; preserve it away from the main entrance.
        preserve_interrupted(temp,backup/'recovery-evidence')
    if name not in state['published_entries']:state['published_entries'].append(name)
    state.pop('entrance_transfer',None);write(session_path,state)


def safe_path(root,value):
    from crs_project import relative
    return relative(root,value)


def preflight_layout(root,config,pending=None):
    """Reject conflicting locations before creating or publishing any output."""
    from crs_project import ROLES
    for name in ['.crs-project.json','00-项目入口.md','00-打开研究地图.html']:
        entry=safe_path(root,name)
        if entry.exists():
            info=entry.stat();need(stat.S_ISREG(info.st_mode) and info.st_nlink==1,'Linked or nonregular entrance is forbidden.')
    need(len(set(config['roles'].values()))==len(ROLES),'Formal and derived directory responsibilities overlap.')
    role_paths=[safe_path(root,p) for p in config['roles'].values()]
    for i,a in enumerate(role_paths):
        for b in role_paths[i+1:]:need(not a.is_relative_to(b) and not b.is_relative_to(a),'Directory responsibility subtrees overlap.')
    seen={'.crs-project.json','00-项目入口.md','00-打开研究地图.html'}|set(config['roles'].values())
    for name,expected in (pending or {}).items():
        target=safe_path(root,name)
        if target.exists():
            need(digest(target)==expected,'Interrupted temporary entrance differs; preserve it for diagnosis.')
            seen.add(name)
    for item in config['retained']:
        need(isinstance(item,dict) and set(item)=={'path','role','purpose'},'Invalid retained existing path.')
        value=item['path'];need(isinstance(value,str) and '/' not in value and value not in seen and item['role'] in ROLES and isinstance(item['purpose'],str) and item['purpose'].strip(),'Duplicate or unclassified retained responsibility.')
        need(safe_path(root,value).exists(),'A planned retained existing path is missing.');seen.add(value)
    need({p.name for p in root.iterdir()}.issubset(seen),'Unclassified project root entries; prepare the complete reference and role inventory first.')
    existing=root/'.crs-project.json'
    if existing.exists():need(read(existing).get('formal_library')==config['formal_library'],'Existing formal-library locator conflicts; inspect and reconcile it explicitly before upgrade.')


def validate_browser(site,report_path):
    report=read(report_path)
    need(report.get('schema')=='crs-browser-check/v1' and report.get('complete') is True,'Actual browser verification is missing or incomplete.')
    manifest=read(site/'build-manifest.json')
    need(report.get('snapshot')==manifest['snapshot'] and report.get('manifest_sha256')==digest(site/'build-manifest.json'),'Browser evidence is stale for the candidate site.')
    required={'lazy_search','search','language_reload','language_navigation','contents','narrow_home','narrow_manuscript','print_readable','no_network','inline_formula_reflow','formula_zoom'}
    checks=report.get('checks',{})
    need(required.issubset(checks) and all(checks[k] is True for k in required),'Required browser interactions have not all passed.')
    from crs_web import scan
    from crs_web_formula import text_hash
    contract=read(site/'reading-contract.json');expected={}
    for filename in manifest['files']:
        if not filename.endswith('.html') or '/' in filename or filename=='00-打开研究地图.html':continue
        page_text=(site/filename).read_text(encoding='utf-8')
        bodies=json.loads(scan(page_text).payload)
        hero=__import__('re').search(r'<h1 class="hero">(.*?)</h1>',page_text,__import__('re').S)
        title_count=scan(hero[1],True).formulas if hero else 0
        for lang,body in bodies.items():
            count=scan(body,True).formulas
            if count or title_count or filename[:-5] in contract['manuscript_pages'] or filename=='index.html':
                expected[(filename,lang)]=(count,text_hash(body),title_count)
    rows=report.get('page_languages',[])
    seen=set()
    for row in rows:
        key=(row.get('filename'),row.get('lang'))
        need(key in expected and key not in seen,'Browser page/language coverage is missing, extra or duplicated.')
        count,sha,title_count=expected[key];seen.add(key)
        need(row.get('title_count',0)==title_count and row.get('title_marked',0)==title_count and row.get('title_rendered',0)==title_count and (not title_count or row.get('title_identity_equal') is True),'Title formulas were not rendered and identity-checked.')
        need(row.get('body_sha256')==sha and row.get('count')==count and row.get('marked')==count and row.get('rendered')==count,'Browser formula count or exact body differs.')
        need(row.get('dom_equal') is True and row.get('formula_identity_equal') is True and row.get('activeLanguage')==('zh-CN' if key[1]=='zh' else 'en'),'Rendered body or active language differs from the exact language source.')
        need(row.get('layout_errors')==[] and row.get('narrow_layout_errors')==[],'Short-inline or wide-formula layout evidence is missing or failed.')
        need(row.get('errors')==0 and row.get('merrors')==0 and row.get('script_errors')==[] and row.get('overflow') is False,'Actual formula page rendering failed.')
    need(seen==set(expected) and report.get('expected_page_languages')==len(expected),'Not every required formula, manuscript or home page and language was rendered.')
    need(report.get('network_requests')==[],'Offline browser verification made network requests.')
    shots=report.get('screenshots',[])
    need(len(shots)>=6,'Representative visual evidence is missing.')
    for shot in shots:
        target=safe_path(report_path.parent,shot['file'])
        need(digest(target)==shot['sha256'],'Visual evidence bytes changed.')
    return report


def validate_visual(path,report_path):
    review=read(path)
    need(set(review)=={'schema','browser_report_sha256','screenshots','checks','reviewer','limitations'},'Invalid visual review fields.')
    need(review['schema']=='crs-visual-review/v1' and review['browser_report_sha256']==digest(report_path),'Visual review is missing or stale.')
    report=read(report_path)
    need(review['screenshots']=={x['file']:x['sha256'] for x in report['screenshots']},'Visual review does not bind every representative screenshot.')
    required={'home_question_progress_obstacles','inline_and_display_formulas','long_formula_and_table_layout','both_manuscript_languages','desktop_narrow_and_print'}
    need(set(review['checks'])==required and all(v=='passed' for v in review['checks'].values()),'Human or agent visual review is incomplete.')
    need(isinstance(review['reviewer'],str) and review['reviewer'].strip(),'Visual reviewer must be identified without claiming review independence.')
    need(isinstance(review['limitations'],list),'Visual review must retain its limitations.')
    return review


def render_entrances(d,home):
    from crs_home import home_markdown
    from crs_project import ROLES
    index=d['browser']['path'];prefix=Path(index).parent.as_uri();url=Path(index).as_uri()
    markdown_url=Path(d['markdown']['path']).as_uri()
    lines=['项目：'+d['title'],'','[打开网页版研究地图]('+url+') · [完整 Markdown 地图]('+markdown_url+')','',home_markdown(home,prefix).strip(),'','## 文件放在哪里','','| 位置 | 职责 |','|---|---|']
    for role,(_,description) in ROLES.items():lines.append('| `'+d['roles'][role]+'/` | '+description+' |')
    lines+=['','未使用的目录按需建立。保留的既有位置见导航清单中的 retained 项；新增材料使用上述职责目录。','','## 来源与版本','',f'正式库：`{d["formal_library"]}/`。当前快照：`{d["snapshot"]}`。',f'人和 AI 共用 `.crs-project.json` 定位当前阅读视图。工具版本：{VERSION}。','阅读视图不改变正式研究结论和审核状态。','']
    entrance='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="0;url='+url+'"><title>'+html.escape(d['title'])+'</title><p><a href="'+url+'">打开研究地图</a></p></html>'
    return {'00-项目入口.md':'\n'.join(lines),'00-打开研究地图.html':entrance}


def validate_delivery(root,d):
    from crs_unique import external_reference, require_unique
    need(d['schema']==NAVIGATION and 'delivery' in d,'A complete reading delivery requires its external delivery binding.')
    delivery_root=external_reference(root,d['delivery']['external_root'])
    require_unique(delivery_root)
    need(set(d['delivery'])=={'external_root','content_sha256','browser_report','visual_review','tool_version'},'Invalid complete delivery binding.')
    site=external_reference(root,d['browser']['path']).parent
    need(site.is_relative_to(delivery_root),'Browser target leaves its declared delivery scope.')
    for key in ['browser_report','visual_review']:
        binding=d['delivery'][key]
        need(set(binding)=={'path','sha256'},'Invalid delivery-evidence binding.')
        need(external_reference(root,binding['path']).is_relative_to(delivery_root),'Report leaves the declared external delivery scope.')
        need(digest(external_reference(root,binding['path']))==binding['sha256'],'Published delivery evidence changed.')
    report=external_reference(root,d['delivery']['browser_report']['path'])
    validate_browser(site,report);validate_visual(external_reference(root,d['delivery']['visual_review']['path']),report)
    need(read(site/'build-manifest.json')['content_sha256']==d['delivery']['content_sha256'],'Published content model identity changed.')
    return {'complete':True,'display':'verified','research_review':'not_performed'}


def upgrade(args):
    with operation_lock(args.out):return _upgrade(args)


def _upgrade(args):
    from crs_project import relative, snapshot, ROLES, validate
    from crs_web import build, load_model, validate_site
    from crs_store import ordinary
    from crs_unique import external_reference, require_unique, no_links
    root=no_links(Path(args.project).absolute()).resolve();out=external_reference(root,str(Path(args.out).absolute()))
    require_unique(root)
    need(out.drive.lower()==root.drive.lower(),'Guarded entrance transfer requires external scratch on the same volume.')
    need(not out.is_relative_to(root) and not root.is_relative_to(out),'Use a private candidate outside the project.')
    session_path=out/SESSION
    if args.resume:
        if not session_path.exists():
            pending=session_path.with_suffix(session_path.suffix+'.pending')
            recovered=read(pending)
            need(recovered.get('schema')=='crs-upgrade-session/v1' and recovered.get('project')==str(root),'Incomplete initial state; preserve the candidate and inspect the pending session.')
            pending.rename(session_path)
        state=read(session_path)
        need(state['schema']=='crs-upgrade-session/v1' and state['project']==str(root),'Resume the same project operation.')
        config=state['config']
        need(snapshot(root,config['formal_library'])==state['snapshot'],'Formal HEAD changed; inspect the new state before resuming.')
        need(digest(Path(state['content_path']))==state['content_sha256'],'Prepared content changed; retain this operation and build a reviewed candidate explicitly.')
    else:
        need(not out.exists(),'Candidate directory already exists; use --resume for the registered operation.')
        need(args.config and args.content,'New upgrade requires --config and --content.')
        config=read(args.config);model=load_model(args.content)
        need(model['schema']=='crs-web-content-complete/v1','Complete project upgrade requires the full research-home and notation contract.')
        need(set(config)=={'title','formal_library','snapshot','markdown','roles','retained'},'Upgrade configuration requires title, formal_library, snapshot, markdown, roles and retained.')
        need(config['snapshot']==snapshot(root,config['formal_library'])==model['snapshot'],'Project and prepared content snapshots differ.')
        need(config['title']==model['title'],'Project and shared content titles differ.')
        need(config['roles']=={k:(config['formal_library'] if k=='formal_library' else v[0]) for k,v in ROLES.items()},'Use the standard directory responsibilities and existing sole formal library.')
        md=config['markdown'];need(set(md)=={'path','sha256','snapshot'} and md['snapshot']==config['snapshot'] and digest(external_reference(root,md['path']))==md['sha256'],'Current Markdown snapshot binding is stale.')
        preflight_layout(root,config)
        out.mkdir(parents=True)
        state={'schema':'crs-upgrade-session/v1','project':str(root),'snapshot':config['snapshot'],'content_path':str(Path(args.content).resolve()),'content_sha256':digest(args.content),'config':config,'stage':'prepared','renderer_dir':str(Path(args.renderer_dir).resolve()) if args.renderer_dir else None,'source_dir':str(Path(args.source_dir).resolve()) if args.source_dir else None,'entry_before':{name:digest(root/name) if (root/name).is_file() else None for name in ['.crs-project.json','00-项目入口.md','00-打开研究地图.html']},'published_entries':[]}
        write(session_path,state)
    external_reference(root,config['markdown']['path'])
    if state['stage']=='prepared':
        completed=False
        if (out/'site'/'build-manifest.json').is_file():
            try:validate_site(out/'site',state['snapshot'],relative(root,config['formal_library']));completed=True
            except (ValueError,KeyError,TypeError,OSError):pass
        if not completed:
            preserve_interrupted(out/'site',out/'recovery-evidence')
            build(state['content_path'],out/'site',state['renderer_dir'],relative(root,config['formal_library']),state['source_dir'])
        state['stage']='built';write(session_path,state)
    need(state['stage'] in {'built','browser_checked','visual_checked','publishing','published'},'Interrupted build preserved; inspect the partial candidate before continuing.')
    validate_site(out/'site',state['snapshot'],relative(root,config['formal_library']))
    report_path=out/'checks'/'browser-report.json'
    if state['stage']=='built':
        complete=False
        if report_path.exists():
            try:validate_browser(out/'site',report_path);complete=True
            except (ValueError,KeyError,TypeError,OSError):pass
        if not complete:
            need(args.node,'Supply the local Node runtime for actual offline browser verification.')
            preserve_interrupted(out/'checks',out/'recovery-evidence')
            command=[args.node,str(Path(__file__).with_name('crs_browser.cjs')),'--site',str(out/'site'),'--out',str(out/'checks')]
            if args.playwright_module:command+=['--playwright-module',args.playwright_module]
            if getattr(args,'browser_channel',None):command+=['--channel',args.browser_channel]
            # The report file carries the outcome; keep the CLI response a single JSON envelope.
            result=subprocess.run(command,timeout=args.browser_timeout,check=False,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            need(result.returncode==0,'Browser verification failed; retain the same operation and its evidence.')
        validate_browser(out/'site',report_path);state['stage']='browser_checked';write(session_path,state)
    if state['stage']=='browser_checked':
        if not args.visual_review:return {'ok':False,'terminal_state':'pending_visual_review','delivery_complete':False,'candidate':str(out),'message':'Inspect the actual screenshots, retain limitations, then resume this operation with --visual-review.'}
        validate_visual(Path(args.visual_review),report_path)
        shutil.copyfile(args.visual_review,out/'checks'/'visual-review.json');state['stage']='visual_checked';write(session_path,state)
    validate_browser(out/'site',report_path);validate_visual(out/'checks'/'visual-review.json',report_path)
    destination=str(out/'delivery')
    d={**config,'schema':NAVIGATION,'browser':{'status':'ready','path':destination+'/site/index.html','reason':''},'delivery':{'external_root':destination,'content_sha256':state['content_sha256'],'browser_report':{'path':destination+'/checks/browser-report.json','sha256':digest(report_path)},'visual_review':{'path':destination+'/checks/visual-review.json','sha256':digest(out/'checks'/'visual-review.json')},'tool_version':VERSION}}
    home=read(out/'site'/'reading-contract.json')['home'];entrances=render_entrances(d,home)
    entrances['.crs-project.json']=json.dumps(d,ensure_ascii=False,indent=2)+'\n'
    for name,text in entrances.items():
        prepare_entrance(out/name,text,state,session_path)
    if not args.publish:return {'ok':False,'terminal_state':'ready_to_publish','delivery_complete':False,'candidate':str(out)}
    need(os.name=='nt','Guarded publication currently requires Windows; candidate and evidence remain available.')
    if state['stage']!='published':
        pending={name+'.crs-pending':hashlib.sha256(text.encode('utf-8')).hexdigest() for name,text in entrances.items()} if state['stage']=='publishing' else None
        preflight_layout(root,config,pending)
        for name,text in entrances.items():
            target=root/name;current=digest(target) if target.is_file() else None
            expected=hashlib.sha256(text.encode('utf-8')).hexdigest()
            intent=state.get('entrance_transfer',{})
            saved=safe_path(out/'entrance-backup',name)
            registered_transfer=(intent.get('name')==name and intent.get('sha256')==expected and current is None and saved.is_file() and digest(saved)==state['entry_before'][name])
            need(current==expected or current==state['entry_before'][name] or registered_transfer,'An entrance changed after preparation; no additional publication work is allowed.')
        target=external_reference(root,destination)
        need(not target.exists() or state['stage']=='publishing','Publication destination exists; never overwrite an unrelated delivery.')
        need(not target.is_relative_to(relative(root,config['formal_library'])),'Derived delivery would enter the formal library.')
        if state['stage']!='publishing':state['stage']='publishing';write(session_path,state)
        # Only complete files acquire their final names; failed bytes stay in
        # the operation's recovery evidence, outside the published tree.
        target.mkdir(parents=True,exist_ok=True)
        for folder in ['site','checks']:
            for source in (out/folder).rglob('*'):
                if not source.is_file():continue
                dest=safe_path(target,folder+'/'+source.relative_to(out/folder).as_posix());dest.parent.mkdir(parents=True,exist_ok=True)
                copy_complete(source,dest,out/'recovery-evidence')
        need(snapshot(root,config['formal_library'])==state['snapshot'],'HEAD changed during publication; root entrances remain guarded.')
        backup=out/'entrance-backup'
        formal=relative(root,config['formal_library'])
        need(not backup.is_relative_to(formal) and not formal.is_relative_to(backup),'Entrance backup overlaps formal research.')
        require_unique(target)
        require_unique(root)
        for name,text in entrances.items():
            publish_entrance(root,name,text,backup,state,session_path)
        state['stage']='published';write(session_path,state)
    preflight_layout(root,config)
    for name,text in entrances.items():
        need(digest(safe_path(root,name))==hashlib.sha256(text.encode('utf-8')).hexdigest(),'Actual published entrance or navigation manifest differs from the prepared binding.')
    require_unique(root)
    require_unique(Path(destination))
    result=validate(root,d,published=True)
    result.update(delivery_complete=True,publication='published',content_completeness='prepared_and_source_bound',display='browser_and_visual_checked',research_review='not_performed')
    return result
