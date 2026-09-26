"""Recursive content uniqueness for an entire project or one external delivery.

Read-only, bounded, no mtime hash cache. Archive members are streamed into owned
external scratch and never extracted to their supplied path names. Reports do
not prove absence of an unknown format with no identifiable signature: declared
archive extensions and recognized unsupported signatures fail closed.
"""
from __future__ import annotations

import crs_temp
import struct
import bz2, contextlib, gzip, hashlib, json, lzma, os, stat, tarfile, tempfile, zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from crs_model import CRSError, canonical
from crs_metrics import measured, count

CHUNK=1024*1024
ARCHIVE_SUFFIXES={'.zip','.zipx','.jar','.war','.apk','.docx','.xlsx','.pptx','.odt','.ods','.odp',
                  '.gz','.gzip','.tgz','.bz2','.tbz','.tbz2','.xz','.txz','.lzma','.tar','.7z','.rar',
                  '.zst','.zstd','.cab','.iso','.wim','.dmg','.br','.lz4','.arj','.lzh','.ace','.z'}
UNSUPPORTED=(b'7z\xbc\xaf\x27\x1c',b'Rar!\x1a\x07',b'\x28\xb5\x2f\xfd',b'MSCF',b'MSWIM',b'\x04\x22\x4d\x18')

def fail(code,message,details=None):raise CRSError(code,message,details)

def no_links(path):
    path=Path(path).absolute()
    for item in (path,*path.parents):
        try:s=item.lstat()
        except FileNotFoundError:continue
        if crs_temp.system_alias(item):continue
        if stat.S_ISLNK(s.st_mode) or getattr(s,'st_file_attributes',0)&0x400:
            fail('content_link_path','Linked/reparse paths cannot establish complete content coverage.')
    return path

def project_scope(store, explicit=None):
    formal=no_links(store.root).resolve()
    known=[]
    for parent in (formal,*formal.parents):
        marker=parent/'.crs-project.json'
        if marker.exists():
            no_links(marker)
            try:data=json.loads(marker.read_text(encoding='utf-8-sig'))
            except (ValueError,OSError):fail('content_scope_invalid','Project navigation is unreadable.')
            if not isinstance(data,dict) or 'formal_library' not in data:
                fail('content_scope_invalid','Project navigation does not bind its formal library.')
            known.append(parent)
    binding=formal/'scope.json'
    if binding.exists():
        no_links(binding)
        try:data=json.loads(binding.read_text(encoding='utf-8'))
        except (ValueError,OSError):fail('content_scope_invalid','Stored project scope is unreadable.')
        if (not isinstance(data,dict) or set(data)!={'schema','project_root'} or data['schema']!='crs-content-scope/v1'
                or not isinstance(data['project_root'],str)):
            fail('content_scope_invalid','Stored project scope has an invalid schema.')
        root=no_links(formal/data['project_root']).resolve()
        if not formal.is_relative_to(root):fail('content_scope_invalid','Stored scope does not contain the formal library.')
        known.append(root)
    if getattr(store,'project_root',None) is not None:known.append(no_links(store.project_root).resolve())
    required=min(known,key=lambda p:len(p.parts)) if known else formal
    chosen=no_links(explicit).resolve() if explicit is not None else required
    if not formal.is_relative_to(chosen) or not required.is_relative_to(chosen):
        fail('content_scope_narrowed','Use the entire enclosing project; a nested store is not an override.')
    return chosen

def external_target(store,path):
    target=no_links(path).resolve()
    root=project_scope(store)
    if target.is_relative_to(root) or root.is_relative_to(target):
        fail('delivery_inside_project','Delivery, scratch and recovery locations must be outside the entire project.')
    return target

class Scanner:
    def __init__(self,max_bytes=64*1024**3,max_members=1000000,max_depth=16):
        if any(type(x) is not int or x<1 for x in (max_bytes,max_members,max_depth)):
            fail('content_limit_invalid','Content limits must be positive integers.')
        self.max_bytes=max_bytes;self.max_members=max_members;self.max_depth=max_depth
        self.bytes=0;self.members=0;self.containers=0;self.hashes={};self.duplicates=[];self.issues=[]
        self.binding=hashlib.sha256();self.facts=[];self.member_names=set();self._seen_hashes=set()
    def issue(self,code,where):
        self.issues.append({'code':code,'locator':where})
    def safe_member(self,name):
        p=PurePosixPath(name.replace('\\','/'));w=PureWindowsPath(name)
        return bool(name) and not (p.is_absolute() or w.drive or w.root or '\x00' in name or '..' in p.parts)
    def consume(self,stream,destination=None):
        h=hashlib.sha256();size=0
        while True:
            block=stream.read(min(CHUNK,max(1,self.max_bytes-self.bytes+1)))
            if not block:break
            self.bytes+=len(block);size+=len(block);count('content_bytes_read_and_expanded',len(block))
            if self.bytes>self.max_bytes:fail('content_resource_limit','Byte expansion ceiling reached.')
            h.update(block)
            if destination is not None:destination.write(block)
        return h.hexdigest(),size
    def register(self,sha,size,where):
        self.members+=1
        if self.members>self.max_members:fail('content_resource_limit','Member ceiling reached.')
        first=self.hashes.setdefault(sha,where)
        if sha in self._seen_hashes:self.duplicates.append({'sha256':sha,'bytes':size,'first':first,'duplicate':where})
        self._seen_hashes.add(sha)
        self.binding.update(canonical([where,sha,size]))
    def member(self,stream,name,where,depth,scratch):
        if not self.safe_member(name):fail('content_member_path','Archive member path escapes its container.')
        locator=where+'!/'+name.replace('%','%25').replace('!','%21')
        if locator in self.member_names:fail('content_member_collision','Archive member name occurs more than once.')
        self.member_names.add(locator)
        with tempfile.TemporaryFile(dir=scratch) as temporary:
            sha,size=self.consume(stream,temporary);self.register(sha,size,locator)
            temporary.seek(0);self.container(temporary,name,locator,depth,scratch)
    def container(self,stream,name,where,depth,scratch):
        stream.seek(0);prefix=stream.read(1024);stream.seek(0)
        suffix=Path(name).suffix.lower()
        if any(prefix.startswith(x) for x in UNSUPPORTED):fail('content_container_unsupported','Unsupported archive signature.')
        kind=None
        if prefix.startswith((b'PK\x03\x04',b'PK\x05\x06',b'PK\x07\x08')):kind='zip'
        elif prefix.startswith(b'\x1f\x8b'):kind='gzip'
        elif prefix.startswith(b'BZh'):kind='bz2'
        elif prefix.startswith(b'\xfd7zXZ\x00'):kind='xz'
        elif len(prefix)>=512 and (prefix[257:262]==b'ustar' or suffix=='.tar' or tar_header(prefix[:512])):kind='tar'
        elif zipfile.is_zipfile(stream):kind='zip'  # Also self-extracting/prefixed ZIP.
        stream.seek(0)
        if kind is None:
            if suffix in ARCHIVE_SUFFIXES:fail('content_container_unknown','Declared archive cannot be completely decoded.')
            return
        if depth>=self.max_depth:fail('content_resource_limit','Container nesting ceiling reached.')
        self.containers+=1
        if kind=='zip':
            zip_directory_bound(stream,self.max_members-self.members,min(self.max_bytes-self.bytes,64*1024**2))
            with zipfile.ZipFile(stream) as z:
                for info in z.infolist():
                    if not self.safe_member(info.filename):fail('content_member_path','Archive member path escapes its container.')
                    if info.flag_bits&1:fail('content_container_encrypted','Encrypted members are not verified.')
                    mode=info.external_attr>>16
                    if stat.S_ISLNK(mode):fail('content_member_link','Archive links are not verified.')
                    if info.is_dir():continue
                    if info.file_size>self.max_bytes-self.bytes:fail('content_resource_limit','Declared member exceeds remaining byte ceiling.')
                    with z.open(info) as f:self.member(f,info.filename,where,depth+1,scratch)
        elif kind=='tar':
            with tarfile.open(fileobj=stream,mode='r:') as t:
                for info in t:
                    if not self.safe_member(info.name):fail('content_member_path','Archive member path escapes its container.')
                    if info.isdir():continue
                    if not info.isfile():fail('content_member_link','Nonordinary TAR members are not verified.')
                    if info.size>self.max_bytes-self.bytes:fail('content_resource_limit','Declared member exceeds remaining byte ceiling.')
                    with t.extractfile(info) as f:self.member(f,info.name,where,depth+1,scratch)
        else:
            factory={'gzip':gzip.GzipFile,'bz2':bz2.BZ2File,'xz':lzma.LZMAFile}[kind]
            with (factory(fileobj=stream,mode='rb') if kind=='gzip' else factory(stream,mode='rb')) as f:
                child=Path(name).stem
                if suffix in {'.tgz','.tbz','.tbz2','.txz'}:child+='.tar'
                self.member(f,child,where,depth+1,scratch)
    def file(self,path,where,scratch):
        no_links(path);before=path.stat()
        if not stat.S_ISREG(before.st_mode):fail('content_nonordinary','Only ordinary files can establish coverage.')
        with path.open('rb') as f:
            where=where.replace('%','%25').replace('!','%21')
            sha,size=self.consume(f);self.register(sha,size,where)
            self.container(f,path.name,where,0,scratch)
        after=path.stat()
        fact=(before.st_size,before.st_mtime_ns,before.st_ino)
        if fact!=(after.st_size,after.st_mtime_ns,after.st_ino):fail('content_changed','A source changed while it was scanned.')
        self.facts.append((path,fact))

@measured('content_scan')
def audit_content(root,*,max_bytes=64*1024**3,max_members=1000000,max_depth=16,_with_index=False):
    root=no_links(root).resolve(strict=True);scanner=Scanner(max_bytes,max_members,max_depth)
    # Scratch outside the complete scope, never under the project or delivery.
    with crs_temp.TemporaryDirectory(prefix='.crs-scan-',dir=root.parent) as scratch:
        def visit(directory):
            for p in sorted(directory.iterdir(),key=lambda p:p.name):
                try:
                    no_links(p)
                    if p.is_dir():yield from visit(p)
                    else:yield p
                except (CRSError,OSError) as error:scanner.issue(getattr(error,'code','content_io_error'),p.relative_to(root).as_posix())
        files=[]
        for p in (visit(root) if root.is_dir() else [root]):
            files.append(p)
            if len(files)>max_members:
                scanner.issue('content_resource_limit','.');break
        for p in files:
            where=p.relative_to(root).as_posix() if root.is_dir() else p.name
            try:scanner.file(p,where,scratch)
            except (CRSError,OSError,ValueError,EOFError,RuntimeError,zipfile.BadZipFile,tarfile.TarError,lzma.LZMAError) as error:
                scanner.issue(getattr(error,'code','content_container_invalid'),where)
                if getattr(error,'code','')=='content_resource_limit':break
        for p,fact in scanner.facts:
            try:
                s=p.stat()
                if fact!=(s.st_size,s.st_mtime_ns,s.st_ino):scanner.issue('content_changed',str(p.relative_to(root)) if root.is_dir() else p.name)
            except OSError:scanner.issue('content_changed',p.name)
        if root.is_dir() and not scanner.issues:
            current=set()
            for p in visit(root):
                current.add(p)
                if len(current)>max_members:scanner.issue('content_resource_limit','.');break
            if current!=set(files):scanner.issue('content_changed','.')
    result={'schema':'crs-content-audit/v1','scope':str(root),'scope_kind':'directory' if root.is_dir() else 'file',
            'ok':not scanner.issues and not scanner.duplicates,'coverage_complete':not scanner.issues,
            'scope_sha256':scanner.binding.hexdigest(),'files_and_members':scanner.members,'containers':scanner.containers,
            'bytes_read_and_expanded':scanner.bytes,'duplicate_count':len(scanner.duplicates),'duplicates':scanner.duplicates,
            'issues':scanner.issues,'limits':{'bytes':max_bytes,'members':max_members,'depth':max_depth},
            'assurance':'Current streamed bytes under cooperating-writer quiescence; no hash cache. Recognized formats and declared archive extensions only; not an OS sandbox or arbitrary format oracle.'}
    if _with_index:result['_index']=dict(scanner.hashes)
    return result

def require_unique(root,**limits):
    report=audit_content(root,**limits)
    if not report['ok']:
        fail('content_uniqueness_blocked','Content is duplicated or recursive coverage is incomplete; retain sources and consolidate them through an explicit authorized cleanup.',
             {**{k:report[k] for k in ['scope','coverage_complete','scope_sha256','files_and_members','duplicate_count']},
              'issues_preview':report['issues'][:10],'issues_count':len(report['issues'])})
    return {k:v for k,v in report.items() if k not in {'duplicates','issues'}}

@contextlib.contextmanager
def content_guard(store):
    """Guard this publication, without making old history cleanup a prerequisite.

    Existing content-addressed objects are resolved and hashed on demand. New
    candidates are still recursively decoded. Complete enclosing-scope conformity
    belongs to audit_content/require_unique and is never implied by this guard.
    """
    if getattr(store,'_content_index',None) is not None:
        yield;return
    store._content_scope=project_scope(store)
    store._content_index={}
    try:yield
    finally:store._content_index=None;store._content_scope=None


def prior_content(store,sha):
    prior=store._content_index.get(sha)
    if prior is not None:return prior
    # A CAS filename is a lookup key, not proof that its bytes are intact.
    if store._object(sha).exists() or sha in store._locations():
        path=store.resolve(sha)
        if path.is_relative_to(store._content_scope):
            return path.relative_to(store._content_scope).as_posix().replace('%','%25').replace('!','%21')
        return 'cold:'+sha
    return None


def check_write(store,path,sha):
    relative=Path(path).resolve().relative_to(store._content_scope).as_posix().replace('%','%25').replace('!','%21')
    prior=prior_content(store,sha)
    if prior is not None and prior!=relative:
        fail('content_duplicate_write','This write would duplicate existing project content; preserve one physical object and retain provenance as references.')

def record_write(store,path,sha,additions=None):
    relative=Path(path).resolve().relative_to(store._content_scope).as_posix().replace('%','%25').replace('!','%21')
    for old,where in list(store._content_index.items()):
        if where==relative or where.startswith(relative+'!/'):del store._content_index[old]
    store._content_index[sha]=relative
    store._content_index.update(additions or {})

def external_reference(root,value):
    if not isinstance(value,str) or not Path(value).is_absolute():
        fail('delivery_reference_invalid','Current external delivery references must be absolute ordinary paths.')
    path=no_links(value).resolve();root=no_links(root).resolve()
    if path.is_relative_to(root) or root.is_relative_to(path):
        fail('delivery_inside_project','The delivered artifact must be outside the entire project.')
    return path


@contextlib.contextmanager
def writer_mutex(root):
    """Windows cooperative writer mutex; does not deny scanner file reads."""
    import ctypes
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateMutexW.argtypes=[wintypes.LPVOID,wintypes.BOOL,wintypes.LPCWSTR];kernel.CreateMutexW.restype=wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD];kernel.WaitForSingleObject.restype=wintypes.DWORD
    kernel.ReleaseMutex.argtypes=[wintypes.HANDLE];kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    identity=hashlib.sha256(os.path.normcase(str(Path(root).resolve())).encode()).hexdigest()
    handle=kernel.CreateMutexW(None,False,'Local\\CRSStore_'+identity)
    if not handle:raise ctypes.WinError(ctypes.get_last_error())
    owned=False
    try:
        result=kernel.WaitForSingleObject(handle,0)
        if result not in {0,0x80}:fail('writer_busy','Another cooperating writer owns this project.')
        owned=True;yield
    finally:
        if owned:kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


def check_candidate(store,target,source):
    """Expand actual staged bytes before publication; retain each member identity."""
    scanner=Scanner()
    relative=Path(target).resolve().relative_to(store._content_scope).as_posix()
    with crs_temp.TemporaryDirectory(prefix='.crs-candidate-',dir=store._content_scope.parent) as scratch:
        scanner.file(Path(source),relative,scratch)
    if scanner.duplicates:fail('content_duplicate_write','Candidate contains recursively repeated content.')
    for sha,where in scanner.hashes.items():
        prior=prior_content(store,sha)
        if prior is not None and prior!=where:
            fail('content_duplicate_write','Candidate repeats content already stored in this project.')
    return scanner.hashes


def tar_header(block):
    if block == bytes(512):return True
    try:
        tarfile.TarInfo.frombuf(block,'utf-8','surrogateescape')
        return True
    except (tarfile.TarError,ValueError):return False


def zip_directory_bound(stream,members,bytes_limit):
    """Bound metadata allocation before ZipFile parses the central directory."""
    stream.seek(0,2);size=stream.tell();stream.seek(max(0,size-65557));tail=stream.read(65557)
    offset=tail.rfind(b'PK\x05\x06')
    if offset<0 or len(tail)-offset<22:fail('content_container_invalid','ZIP footer is missing.')
    fields=struct.unpack('<4s4H2LH',tail[offset:offset+22]);entries=fields[4];central_bytes=fields[5]
    if fields[1] or fields[2]:fail('content_container_unsupported','Multi-disk raw ZIP is not a verified content scope.')
    if entries==65535 or central_bytes==0xffffffff:
        footer=size-len(tail)+offset
        if footer<20:fail('content_container_invalid','ZIP64 locator missing.')
        stream.seek(footer-20);loc=stream.read(20)
        if loc[:4]!=b'PK\x06\x07':fail('content_container_invalid','ZIP64 locator missing.')
        _,disk,position,disks=struct.unpack('<4sLQL',loc)
        if disk or disks!=1 or position>footer-56:fail('content_container_invalid','ZIP64 locator escapes archive.')
        stream.seek(position);header=stream.read(56)
        if len(header)!=56 or header[:4]!=b'PK\x06\x06':fail('content_container_invalid','ZIP64 footer missing.')
        entries=struct.unpack_from('<Q',header,32)[0];central_bytes=struct.unpack_from('<Q',header,40)[0]
    if entries>members or central_bytes>bytes_limit:fail('content_resource_limit','ZIP central-directory metadata exceeds the scan ceiling.')
    stream.seek(0)
