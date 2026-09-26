#!/usr/bin/env python3
"""Build and check a shared human/AI project entrance without changing research."""
from crs_output import SafeParser, add_output_arguments, configure_output, error_response, emit_result, public_main

import argparse
import hashlib
import html
import json
import re
import sys
from pathlib import Path, PurePosixPath
from urllib.parse import quote

SCHEMA = 'crs-project-navigation/v1'
MANIFEST = '.crs-project.json'
HOME = '00-项目入口.md'
PORTAL = '00-打开项目.html'
ROLES = {'formal_library': ('研究数据', '正式研究库：只通过 CRS 命令归档与修订'),
         'reading': ('阅读视图', '外部阅读交付的引用；不存完整导出副本'),
         'sources': ('原始材料', '待整理的来源材料；归档后保留来源去向'),
         'work': ('工作区', '尚未归档的尝试、计算与待决材料'),
         'history': ('历史记录', '历史与来源引用、导入整理及核验记录；完整恢复副本外置'),
         'deliveries': ('交付包', '外部交付包的身份与位置引用；压缩包放在项目外')}
GENERATED = {MANIFEST, HOME, PORTAL}

def fail(message):
    raise ValueError(message)

def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def relative(root, value):
    if not isinstance(value, str) or not value or '\\' in value or ':' in value or '%' in value or '#' in value or '?' in value:
        fail('invalid_relative_path')
    p = PurePosixPath(value)
    if p.is_absolute() or any(x in {'.', '..'} for x in value.split('/')):
        fail('path_escape_or_noncanonical')
    target = root.joinpath(*p.parts)
    for item in [target, *target.parents]:
        if item == root: break
        if item.is_symlink() or (hasattr(item, 'is_junction') and item.is_junction()): fail('linked_path_forbidden')
    if not target.resolve().is_relative_to(root.resolve()): fail('path_escape')
    return target

def snapshot(root, library):
    head = read(relative(root, library) / 'HEAD')
    if head.get('schema') != 'crs-head/v1' or not re.fullmatch('[0-9a-f]{64}', head.get('snapshot', '')):
        fail('invalid_formal_head')
    return head['snapshot']

def validate(root, d, published=False):
    fields = {'schema', 'title', 'formal_library', 'snapshot', 'markdown', 'browser', 'roles', 'retained'}
    from crs_unique import require_unique, external_reference
    require_unique(root)
    # A complete reading delivery additionally binds its external delivery evidence.
    complete = isinstance(d, dict) and 'delivery' in d
    if complete: fields.add('delivery')
    if set(d) != fields or d['schema'] != SCHEMA: fail('invalid_navigation_schema')
    if not isinstance(d['title'], str) or not d['title'].strip() or any(c in d['title'] for c in '\n\r<>[]|'): fail('invalid_title')
    expected_roles = {k:(d['formal_library'] if complete and k=='formal_library' else v[0]) for k,v in ROLES.items()}
    if d['roles'] != expected_roles: fail('nonstandard_directory_roles')
    if d['formal_library'] != d['roles']['formal_library']: fail('ambiguous_formal_library')
    if snapshot(root, d['formal_library']) != d['snapshot']: fail('stale_navigation_snapshot')
    md=d['markdown']
    if not isinstance(md, dict) or set(md) != {'path','sha256','snapshot'}: fail('invalid_markdown_binding')
    if md['snapshot'] != d['snapshot'] or sha(external_reference(root,md['path'])) != md['sha256']: fail('stale_markdown_binding')
    if not md['path'].endswith('.md'): fail('markdown_target_required')
    browser=d['browser']
    if set(browser) != {'status','path','reason'} or browser['status'] not in {'pending','ready'}: fail('invalid_browser_state')
    if complete and browser['status']!='ready': fail('complete_upgrade_requires_browser_delivery')
    if browser['status']=='pending':
        if browser['path'] is not None or not isinstance(browser['reason'],str) or not browser['reason'].strip(): fail('pending_browser_reason_required')
    else:
        entry=external_reference(root,browser['path'])
        if entry.name!='index.html' or not entry.is_file(): fail('browser_index_missing')
        from crs_web import validate_site
        result=validate_site(entry.parent, d['snapshot'], relative(root,d['formal_library']))
        if not result.get('ok'): fail('browser_validation_failed')
    if not isinstance(d['retained'],list): fail('invalid_retained_inventory')
    generated = {MANIFEST,HOME,'00-打开研究地图.html'} if complete else GENERATED
    seen=set(generated)|set(d['roles'].values())
    for item in d['retained']:
        if not isinstance(item,dict) or set(item)!={'path','role','purpose'}: fail('invalid_retained_entry')
        path=item['path']
        if '/' in path or path in seen or item['role'] not in ROLES: fail('duplicate_or_unknown_role')
        if not isinstance(item['purpose'],str) or not item['purpose'].strip(): fail('missing_directory_purpose')
        if not relative(root,path).exists(): fail('missing_retained_target')
        seen.add(path)
    unknown={p.name for p in root.iterdir()}-seen
    if unknown: fail('unclassified_root_entries: '+', '.join(sorted(unknown)))
    if complete:
        from crs_delivery import validate_delivery, render_entrances
        validate_delivery(root,d)
    if published:
        if complete:
            home=read(external_reference(root,browser['path']).parent/'reading-contract.json')['home']
            expected=render_entrances(d,home)
        else: expected=render(d)
        for name,text in expected.items():
            if (root/name).read_text(encoding='utf-8') != text: fail('entrance_bytes_stale: '+name)
    return {'ok':True,'snapshot':d['snapshot'],'formal_library':d['formal_library'],
            'browser_delivery':browser['status'],'retained_entries':len(d['retained']),
            'delivery_complete':complete,'check_scope':'complete_derived_delivery' if complete else 'navigation_only',
            'research_review':'not_performed','terminal_state':'success'}

def render(d):
    link=lambda p:quote(p,safe='/')
    current=d['markdown']['path']; e=html.escape
    browser=(f'[打开网页版研究地图]({link(d["browser"]["path"])})' if d['browser']['status']=='ready' else '网页版研究地图：待交付。'+d['browser']['reason'])
    lines=[f'项目：{d["title"]}', '', '这里是唯一的当前项目入口。其他既有入口只用于兼容与查阅。', '',
           '## 从这里开始', '', f'- [当前 Markdown 研究地图]({link(current)})',f'- {browser}',
           f'- [浏览器项目导航]({link(PORTAL)})', '',
           '## 文件放在哪里', '', '| 位置 | 用途 |','|---|---|']
    for role,(path,purpose) in ROLES.items(): lines.append(f'| `{path}/` | {purpose} |')
    lines += ['', '以上是固定归档位置，按需建立；尚未使用的目录不必制造空文件。正式研究库只有 `研究数据/`。', '',
              '## 人和 AI 共用的定位', '', f'- 导航清单：`{MANIFEST}`。先读清单，再定位正式库，不从日期或旧目录名猜测。',
              f'- 当前快照：`{d["snapshot"]}`。',
              '- 研究记录与结论以正式库为准；阅读页、导入成功或来源忠实性审核均不等于研究结论得到确立。',
              '- 归档使用 CRS 命令；完成后同步清单和当前地图，再检查入口。新增顶层目录必须先明确职责。','',
              '## 保留的既有位置', '', '以下既有位置按原路径保留以兼容已有链接，不作为新材料的默认归档位置。','', '| 既有位置 | 职责 | 说明 |','|---|---|']
    for i in d['retained']:
        label=i['path'].replace('|','\\|');purpose=i['purpose'].replace('|','\\|')
        lines.append(f'| [{label}]({link(i["path"])}) | {ROLES[i["role"]][0]} | {purpose} |')
    lines+=['','后续日期快照放入对应职责目录；主入口直接更新当前指向，不追加多份“最新入口”。','']
    md='\n'.join(lines)
    cards=''.join(f'<section><h2>{e(p)}/</h2><p>{e(t)}</p></section>' for p,t in ROLES.values())
    histories=''.join(f'<li><a href="{link(i["path"])}">{e(i["path"])}</a>：{e(i["purpose"])}</li>' for i in d['retained'])
    web=(f'<a href="{link(d["browser"]["path"])}">打开网页版研究地图</a>' if d['browser']['status']=='ready' else '<p>网页版研究地图：待交付。'+e(d['browser']['reason'])+'</p>')
    portal=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(d['title'])} · 项目入口</title>
<style>body{{margin:0;background:#fafbf7;color:#273b37;font:17px/1.8 system-ui,sans-serif}}header{{background:#edf2eb;padding:2rem max(5vw,1rem)}}main{{max-width:1100px;margin:auto;padding:2rem}}a{{color:#176f70}}h1{{font-size:2.2rem;line-height:1.3}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:1rem}}section{{padding:1rem;border:1px solid #d7e0d8;border-radius:6px}}code{{overflow-wrap:anywhere}}@media print{{body{{background:white}}header,main{{padding:1rem}}details{{display:block}}}}</style>
<header><p>统一项目入口 · 本地阅读</p><h1>{e(d['title'])}</h1><a href="{link(current)}">当前 Markdown 研究地图</a> · <a href="{link(HOME)}">Obsidian 项目入口</a>{web}</header>
<main><h2>文件放在哪里</h2><div class="cards">{cards}</div><p>固定位置按需建立。研究数据是唯一正式库；本页是项目导航，不是完整网页版研究地图。</p><h2>人和 AI 共用的定位</h2><p>先读取 <code>{MANIFEST}</code>。归档后同步当前地图和入口；软件检查不授予研究结论可信度。</p><p>当前快照：<code>{d['snapshot']}</code></p><details><summary>保留的既有位置</summary><ul>{histories}</ul></details></main></html>'''
    return {HOME:md,PORTAL:portal}

@public_main
def main():
    p=SafeParser(description=__doc__);add_output_arguments(p);sub=p.add_subparsers(dest='command',required=True)
    for cmd in ['check','resolve','inspect']:
        q=sub.add_parser(cmd);q.add_argument('project')
    q=sub.add_parser('build');q.add_argument('project');q.add_argument('--config',required=True);q.add_argument('--out',required=True)
    q=sub.add_parser('upgrade',help='Prepare, verify and publish one complete research reading view; resume the same operation after visual review.')
    q.add_argument('project');q.add_argument('--config');q.add_argument('--content');q.add_argument('--out',required=True)
    q.add_argument('--renderer-dir');q.add_argument('--source-dir');q.add_argument('--node');q.add_argument('--playwright-module');q.add_argument('--browser-channel',help='Playwright browser channel for the offline check, for example chromium; default msedge.')
    q.add_argument('--browser-timeout',type=int,default=1800);q.add_argument('--visual-review');q.add_argument('--resume',action='store_true');q.add_argument('--publish',action='store_true')
    a=p.parse_args();configure_output(a);root=Path(a.project).resolve()
    if a.command=='upgrade':
        from crs_delivery import upgrade
        result=upgrade(a);emit_result(result)
        if not result.get('delivery_complete'):sys.exit(2)
        return
    if a.command=='inspect':
        result={'ok':True,'entries':[{'path':x.name,'kind':'directory' if x.is_dir() else 'file'} for x in sorted(root.iterdir())],'terminal_state':'success'}
    else:
        d=read(Path(a.config)) if a.command=='build' else read(root/MANIFEST)
        if a.command=='build' and (d.get('schema')!=SCHEMA or 'delivery' in d): fail('complete_reading_requires_upgrade_command')
        result=validate(root,d,published=a.command=='check')
        if a.command=='build':
            from crs_unique import external_reference
            out=external_reference(root,str(Path(a.out).absolute()))
            if out.exists() or out.is_relative_to(root): fail('output_must_be_new_external_directory')
            out.mkdir(parents=True)
            for name,text in render(d).items(): (out/name).write_text(text,encoding='utf-8',newline='')
            (out/MANIFEST).write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='')
        if a.command=='resolve':result['path']=str(relative(root,d['formal_library']))
    emit_result(result)

if __name__=='__main__':
    if hasattr(sys.stdout, 'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
    sys.exit(main())
