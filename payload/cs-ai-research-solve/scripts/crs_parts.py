"""Size-bounded ZIP volumes of one exact logical exchange; no repeated payloads.

512 MB means 512,000,000 final bytes. A part is not a standalone reviewed bundle.
All parts and the index are required for verification/import. No record, report,
dependency or provenance is dropped merely to fit a volume.
"""

import crs_temp
from contextlib import contextmanager, nullcontext
import hashlib,json,os,shutil,tempfile,zipfile
from pathlib import Path
from crs_model import CRSError,canonical
from crs_unique import no_links,require_unique

LIMIT=512_000_000
def fail(code,message):raise CRSError(code,message)
def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()

def rank_assets(records,reviews,required,specs,overrides=None):
    overrides=overrides or {}
    if not isinstance(overrides,dict) or set(overrides)-set(required):fail('export_priority_invalid','Priority overrides must name current required asset SHA identities.')
    reports={r['report'] for r in reviews};ranks={}
    reverse={}
    for record in records.values():
        for asset_sha in {a['sha256'] for a in record['evidence']}:
            reverse.setdefault(asset_sha,[]).append(record)
    for digest in required:
        users=reverse.get(digest,[])
        future=sum(bool(r.get('reopen')) or r['kind'] in {'objective','hypothesis'} for r in users)
        role=specs.get(digest,{}).get('role','')
        important=100 if digest in reports else 80 if role in {'proof','argument','verifier','code','configuration','environment','certificate','evaluation'} else 50
        value=overrides.get(digest)
        if value is not None:
            if not isinstance(value,dict) or set(value)!={'importance','future_research','reason'} or any(type(value[x]) is not int or not 0<=value[x]<=100 for x in ['importance','future_research']) or not isinstance(value['reason'],str) or not value['reason'].strip():fail('export_priority_invalid','Each override needs importance/future_research integers 0..100 and an explicit reason.')
            important=value['importance'];future=value['future_research'];reason=value['reason']
        else:
            future=min(100,40*future+10*len(users));reason='Metadata heuristic: required review reports, evidence role, retained future-work hooks and reuse count; not a research quality verdict.'
        ranks[digest]={'sha256':digest,'required_report':digest in reports,'importance':important,'future_research':future,'reason':reason}
    return sorted(ranks.values(),key=lambda r:(not r['required_report'],-r['importance'],-r['future_research'],r['sha256']))

def split_bundle(source,destination,ranking,limit=LIMIT,*,publication_guard=None):
    """Actual compression trial, split by unique logical files, then verify bytes."""
    if type(limit) is not int or not 1024<=limit<=LIMIT:fail('export_part_limit','ZIP limit must be 1,024..512,000,000 bytes.')
    destination=no_links(destination).resolve()
    if destination.exists():fail('export_parts_exists','Part-set destination already exists.')
    with crs_temp.TemporaryDirectory(prefix='.crs-volumes-',dir=destination.parent) as tmp:
        root=Path(tmp);parts=[];files=[]
        rank={r['sha256']:i for i,r in enumerate(ranking)}
        with zipfile.ZipFile(source) as original:
            names=original.namelist()
            names.sort(key=lambda n:(0 if not n.startswith('objects/') else 1,rank.get(n.rsplit('/',1)[-1],-1),n))
            def write(group,path):
                with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6,allowZip64=True) as z:
                    for name in group:
                        info=zipfile.ZipInfo(name,(1980,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100644<<16
                        with original.open(name) as src,z.open(info,'w',force_zip64=True) as dst:shutil.copyfileobj(src,dst,1024*1024)
            def pack(group):
                candidate=root/('part-%04d.zip'%(len(parts)+1));write(group,candidate)
                if candidate.stat().st_size>limit:
                    candidate.unlink()
                    if len(group)==1:fail('export_indivisible_payload','One mandatory exact payload exceeds the ZIP cap. Retain it externally with a reviewed resolvable reference before export; no oversized package was published.')
                    cut=len(group)//2;pack(group[:cut]);pack(group[cut:]);return
                for name in group:
                    h=hashlib.sha256();size=0
                    with original.open(name) as f:
                        for block in iter(lambda:f.read(1024*1024),b''):h.update(block);size+=len(block)
                    files.append({'path':name,'part':candidate.name,'sha256':h.hexdigest(),'bytes':size})
                parts.append({'path':candidate.name,'sha256':sha(candidate),'bytes':candidate.stat().st_size})
            pack(names)
        index={'schema':'crs-exchange-parts/v1','limit_bytes':limit,'parts':parts,'files':files,'ranking':ranking,
               'selection':'All canonical records, reports and dependency relations retained. Optional evidence follows ranked asset budget; referenced evidence remains exact and explicit.',
               'use':'Keep every part and this index together. verify-bundle/import accept this directory using trusted local CRS tools; a lone volume is incomplete.'}
        from crs_exchange import _portable_text
        _portable_text(index)
        (root/'parts.json').write_bytes(canonical(index));require_unique(root)
        # Caller verifies the reconstructed canonical exchange before this atomic publication.
        with materialized(root) as restored:
            from crs_exchange import _verify_bundle_data
            result=_verify_bundle_data(restored)
        from crs_finish import _publish
        with publication_guard() if publication_guard is not None else nullcontext():
            _publish(root,destination)
    result.pop('delivery_receipt', None)
    result.pop('sha256', None)
    return {**result,'sha256':None,'index_sha256':sha(destination/'parts.json'),'output':str(destination),'parts':parts,'part_count':len(parts),'part_limit_bytes':limit,'ranking_count':len(ranking),'all_parts_required':True}

@contextmanager
def materialized(path):
    path=no_links(path).resolve()
    if not path.is_dir():yield path;return
    require_unique(path)
    index_path=no_links(path/'parts.json')
    if index_path.stat().st_size>32*1024**2:fail('export_parts_limit','Part index exceeds bounded metadata size.')
    index_bytes=index_path.read_bytes();index_sha=hashlib.sha256(index_bytes).hexdigest()
    index=json.loads(index_bytes)
    if not isinstance(index,dict) or set(index)!={'schema','limit_bytes','parts','files','ranking','selection','use'} or index.get('schema')!='crs-exchange-parts/v1':fail('export_parts_invalid','Unknown part-set schema.')
    from crs_exchange import _portable_text
    _portable_text(index)
    parts=index.get('parts');rows=index.get('files');limit=index.get('limit_bytes')
    if type(limit) is not int or not 1024<=limit<=LIMIT or not isinstance(parts,list) or not parts or not isinstance(rows,list):fail('export_parts_invalid','Invalid part-set bounds.')
    if len(parts)>20000 or len(rows)>20000:fail('export_parts_limit','Part set exceeds the logical exchange member ceiling.')
    expected={};declared={}
    for part in parts:
        if not isinstance(part,dict) or set(part)!={'path','sha256','bytes'}:fail('export_parts_invalid','Invalid volume binding.')
        name=part['path']
        if not isinstance(name,str) or Path(name).name!=name or not name.endswith('.zip') or name in declared:fail('export_parts_invalid','Invalid volume name.')
        file=no_links(path/name)
        if file.stat().st_size!=part['bytes'] or part['bytes']>limit or sha(file)!=part['sha256']:fail('export_parts_changed','Missing, oversized or changed volume.')
        declared[name]=part
    if {p.name for p in path.iterdir()}!=set(declared)|{'parts.json'}:fail('export_parts_unknown','Missing or undeclared part-set files.')
    from crs_exchange import _filename
    for row in rows:
        if not isinstance(row,dict) or set(row)!={'path','part','sha256','bytes'}:fail('export_parts_invalid','Invalid logical file binding.')
        _filename(row['path'])
        if row['path'] in expected or row['part'] not in declared:fail('export_parts_invalid','Repeated or unknown logical payload.')
        expected[row['path']]=row
    with crs_temp.TemporaryDirectory(prefix='.crs-restore-',dir=path.parent) as temp:
        target=Path(temp)/'logical.zip';seen=set()
        with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_STORED,allowZip64=True) as out:
            for name in declared:
                with zipfile.ZipFile(path/name) as part:
                    for info in part.infolist():
                        row=expected.get(info.filename)
                        if row is None or row['part']!=name or info.filename in seen or info.file_size!=row['bytes']:fail('export_parts_invalid','Volume member differs from its logical inventory.')
                        seen.add(info.filename);h=hashlib.sha256()
                        with part.open(info) as src,out.open(info.filename,'w',force_zip64=True) as dst:
                            for block in iter(lambda:src.read(1024*1024),b''):h.update(block);dst.write(block)
                        if h.hexdigest()!=row['sha256']:fail('export_parts_changed','Reconstructed content identity differs.')
        if seen!=set(expected):fail('export_parts_missing','The logical exchange is incomplete.')
        require_unique(target)
        for name,part in declared.items():
            volume=no_links(path/name)
            if volume.stat().st_size!=part['bytes'] or sha(volume)!=part['sha256']:fail('export_parts_changed','Volume changed during reconstruction.')
        if sha(index_path)!=index_sha or {p.name for p in path.iterdir()}!=set(declared)|{'parts.json'}:fail('export_parts_changed','Index or volume inventory changed during reconstruction.')
        yield target
