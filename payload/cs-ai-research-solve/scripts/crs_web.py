"""Build a consistent, offline research reading view from prepared HTML content.

This module never changes research records or confers research assurance.
All inputs are local, reviewed presentation material, not executable manuscripts.
"""
from __future__ import annotations

from crs_output import SafeParser, add_output_arguments, configure_output, error_response, emit_result, public_main

import argparse
import hashlib
import html
import json
import re
import shutil
import sys
from collections import Counter
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
from string import Template
from urllib.parse import unquote, urlsplit

TEMPLATE_ID = 'crs-research-map-classic'
MODEL = 'crs-web-content/v1'
MODEL_COMPLETE = 'crs-web-content-complete/v1'
NAV = [('index', '研究首页'), ('route-organization', '动机与技术路线'),
       ('manuscripts', '论证、算法与实验'), ('goals', '研究目标'), ('overview', '概览'),
       ('attempts', '动作与反馈'), ('records', '完整研究记录'),
       ('assets', '资产与原始证据'), ('reviews', '审核记录'), ('sources', '来源索引')]
SHA = re.compile(r'[0-9a-f]{64}')
PAGE = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*')
SAFE_TAGS = set('a abbr article b blockquote br caption code col colgroup dd del details div dl dt em figcaption figure h2 h3 h4 h5 h6 hr i img kbd li mark ol p pre s section small span strong sub summary sup table tbody td th thead tr ul'.split())
SAFE_ATTRS = set('id class title href src alt width height colspan rowspan scope lang data-tex data-display data-page'.split())


def title_fragment(text):
    """Render only explicitly delimited title formulas, with escaped prose."""
    pattern = re.compile(r'\\\((.+?)\\\)|(?<!\\)\$([^$\n]+?)(?<!\\)\$')
    parts=[];last=0
    for match in pattern.finditer(text):
        tex=match[1] if match[1] is not None else match[2]
        parts.append(html.escape(text[last:match.start()]))
        parts.append('<span class="formula" data-tex="'+html.escape(tex,quote=True)+'">'+html.escape(match[0])+'</span>')
        last=match.end()
    parts.append(html.escape(text[last:]))
    return ''.join(parts)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def need(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def formal_snapshot(project):
    head = read_json(Path(project) / 'HEAD')
    need(isinstance(head, dict) and set(head) == {'schema', 'snapshot'} and head['schema'] == 'crs-head/v1'
         and isinstance(head['snapshot'], str) and SHA.fullmatch(head['snapshot']), 'Invalid formal HEAD contract.')
    return head['snapshot']


@lru_cache(maxsize=65536)
def resolved_path(path):
    return path.resolve()


def local_target(root, base, url, current=None):
    parsed = urlsplit(url)
    need(not parsed.scheme and not parsed.netloc and not parsed.path.startswith(('/', '\\')),
         'Use relative local destinations; external URLs belong in the source text.')
    need('\\' not in parsed.path and ':' not in unquote(parsed.path), 'Unsafe local destination.')
    path = resolved_path(base / unquote(parsed.path)) if parsed.path else resolved_path(current or base)
    need(path.is_relative_to(resolved_path(root)), 'Link escapes the reading folder; preserve source copies inside the delivery.')
    return path, unquote(parsed.fragment)


class Scan(HTMLParser):
    def __init__(self, fragment=False):
        super().__init__(convert_charrefs=True)
        self.fragment = fragment
        self.ids, self.links, self.text, self.meta = set(), [], [], {}
        self.formulas = 0
        self.formula_tokens = []
        self.payload = ''
        self.in_payload = False
        self.navigation = []
        self.in_nav = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        need(len(attributes) == len(attrs), 'Duplicate HTML attributes.')
        if tag == 'script' and attributes.get('id') == 'language-content':
            self.in_payload = True
        if tag == 'nav': self.in_nav = True
        if tag == 'a' and self.in_nav: self.navigation.append(attributes.get('href'))
        if self.fragment:
            need(tag in SAFE_TAGS, 'Unsupported content tag: ' + tag)
            need(all(k in SAFE_ATTRS for k in attributes), 'Unsupported content attribute; remove scripts, inline styles and event handlers.')
        identity = attributes.get('id')
        if identity:
            need(identity not in self.ids, 'Duplicate HTML id: ' + identity)
            self.ids.add(identity)
        for key in ('href', 'src'):
            if attributes.get(key):
                self.links.append(attributes[key])
        if 'data-tex' in attributes:
            need('formula' in attributes.get('class', '').split(), 'TeX requires the formula class.')
            self.formulas += 1
            self.formula_tokens.append(attributes['data-tex'])
        if tag == 'meta' and attributes.get('name'):
            self.meta[attributes['name']] = attributes.get('content', '')

    def handle_endtag(self, tag):
        if tag == 'script': self.in_payload = False
        if tag == 'nav': self.in_nav = False

    def handle_data(self, data):
        if self.in_payload: self.payload += data
        else: self.text.append(data)


def scan(text, fragment=False):
    parser = Scan(fragment)
    parser.feed(text)
    parser.close()
    return parser



class ReadingDirectory(HTMLParser):
    """Check reader choices, not the truth of the linked manuscripts."""
    def __init__(self):
        super().__init__(); self.entries=[]; self.current=None; self.depth=0
        self.field=None; self.title=False; self.archive_links=[]
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs); classes=attrs.get('class','').split()
        if tag=='section':
            if 'reading-entry' in classes:
                need(self.current is None,'Nested manuscript directory entries are invalid.')
                self.current={'page':attrs.get('data-page'),'title':'','scope':'','status':'','links':[]};self.depth=0
            if self.current is not None:self.depth+=1
        if self.current is not None:
            if tag=='h3':self.title=True
            if tag=='p':
                self.field='scope' if 'reading-scope' in classes else 'status' if 'reading-status' in classes else None
            if tag=='a':self.current['links'].append(attrs.get('href',''))
            if self.field and 'data-tex' in attrs:
                self.current[self.field]+=repr(('formula',attrs['data-tex'],attrs.get('data-display','false')))
        if tag=='a' and urlsplit(attrs.get('href','')).path.startswith('record-'):
            self.archive_links.append(attrs['href'])
    def handle_endtag(self,tag):
        if tag=='h3':self.title=False
        if tag=='p':self.field=None
        if tag=='section' and self.current is not None:
            self.depth-=1
            if self.depth==0:self.entries.append(self.current);self.current=None
    def handle_data(self,data):
        if self.current is not None:
            if self.title:self.current['title']+=data
            if self.field:self.current[self.field]+=data


class ReadingSource(HTMLParser):
    def __init__(self):
        super().__init__();self.text=[];self.links=[]
    def handle_data(self,data):self.text.append(data)
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='a':self.links.append(attrs.get('href',''))
        if 'data-tex' in attrs:self.text.append(repr(('formula',attrs['data-tex'],attrs.get('data-display','false'))))


def validate_reading_directory(body,manuscript_pages,manuscripts=None):
    parser=ReadingDirectory();parser.feed(body);parser.close()
    need(parser.current is None,'Incomplete manuscript directory entry.')
    identities=[entry['page'] for entry in parser.entries]
    need(len(identities)==len(set(identities)) and set(identities)==set(manuscript_pages),
         'The manuscript directory must identify every complete manuscript exactly once.')
    need(not parser.archive_links,'Keep raw result and intake records in the research archive, not the manuscript directory.')
    titles=[]
    for entry in parser.entries:
        title=' '.join(entry['title'].split());titles.append(title)
        need(len(title)>8 and title not in {'证明与独立核验','论证与独立核验','实验与独立核验','Proof and independent verification','Argument and independent verification','Experiment and independent verification'},'Use a concrete manuscript subject, not a generic verification label.')
        need(len(entry['scope'].strip())>=25 and len(entry['status'].strip())>=25,
             'Each manuscript choice needs a source-bound reading scope and explicit evidence status.')
        need(entry['page']+'.html' in entry['links'] and any(link.startswith('sources/') for link in entry['links']),
             'Each manuscript choice must open the complete page and its original source.')
        if manuscripts is not None:
            source=ReadingSource();source.feed(manuscripts[entry['page']]);source.close()
            links=[link for link in entry['links'] if link.startswith('sources/')]
            need(all(link in source.links for link in links),'Manuscript source link belongs to another reading page.')
            need(' '.join(entry['scope'].split()) in ' '.join(''.join(source.text).split()),
                 'Reading scope must be an exact excerpt of the corresponding manuscript.')
    need(len(titles)==len(set(titles)),'Repeated manuscript titles do not identify distinct reading choices.')


def load_model(path):
    data = read_json(path)
    complete = data.get('schema') == MODEL_COMPLETE
    required = {'schema', 'title', 'subtitle', 'snapshot', 'status', 'pages'}
    if complete: required |= {'home', 'manuscript_pages', 'notation_review'}
    need(set(data) == required, 'Content model fields differ from the documented contract.')
    need(data['schema'] in {MODEL, MODEL_COMPLETE} and isinstance(data['snapshot'], str) and SHA.fullmatch(data['snapshot']), 'Provide the exact source snapshot hash.')
    for field in ('title', 'subtitle', 'status'):
        need(isinstance(data[field], str) and 0 < len(data[field]) <= 2000, 'Missing or oversized ' + field)
    need(isinstance(data['pages'], list) and data['pages'], 'Content pages are required.')
    if complete:
        from crs_home import validate_home, render_home
        validate_home(data['home'])
        home_pages = [p for p in data['pages'] if p.get('id') == 'index']
        need(len(home_pages) == 1, 'One research homepage is required.')
        for lang, field in [('en','body'),('zh','body_zh')]:
            need(home_pages[0][field] == render_home(data['home'], lang), 'Homepage must be generated from the shared home model.')
    seen = set()
    for page in data['pages']:
        need(set(page) == {'id', 'title', 'group', 'body', 'body_zh'}, 'Each page needs id, title, group, body and body_zh.')
        need(isinstance(page['id'], str) and PAGE.fullmatch(page['id']) and page['id'] not in seen, 'Invalid or duplicate page id.')
        seen.add(page['id'])
        for field in ('title', 'group', 'body'):
            need(isinstance(page[field], str) and page[field].strip(), 'Page field must be nonempty: ' + field)
        need(page['body_zh'] is None or isinstance(page['body_zh'], str) and page['body_zh'].strip(), 'body_zh must be null or complete translated HTML.')
        primary = scan(page['body'], True)
        if page['body_zh'] is not None:
            translated = scan(page['body_zh'], True)
            if not complete:
                need(Counter(primary.formula_tokens) == Counter(translated.formula_tokens), 'Translated TeX differs from the primary text; preserve formulas exactly.')
    need({key for key, _ in NAV}.issubset(seen), 'Keep every fixed navigation page; explain missing material on an empty-category page.')
    if complete:
        need(isinstance(data['manuscript_pages'], list) and len(data['manuscript_pages']) == len(set(data['manuscript_pages'])), 'Invalid manuscript inventory.')
        expected = {p['id'] for p in data['pages'] if p['id'].startswith('manuscript-') or p['id'].endswith('-manuscript')}
        need(expected.issubset(set(data['manuscript_pages'])) and set(data['manuscript_pages']).issubset(seen), 'Full manuscript inventory is incomplete.')
        for page in data['pages']:
            if page['id'] in data['manuscript_pages']:
                need(page['body_zh'] is not None, 'Full Chinese manuscript reading is missing: ' + page['id'])
                need('sources/' in page['body'] and 'sources/' in page['body_zh'], 'Manuscript source identity must remain retrievable.')
        directory=next(p for p in data['pages'] if p['id']=='manuscripts')
        for field in ['body','body_zh']:
            need(directory[field] is not None,'Provide both reading languages for the manuscript directory.')
            validate_reading_directory(directory[field],data['manuscript_pages'],{p['id']:p[field] for p in data['pages'] if p['id'] in data['manuscript_pages']})
        from crs_web_formula import check_notation
        check_notation(data['pages'], data['notation_review'])
    return data


def validate_site(root, expected_snapshot, project=None):
    resolved_path.cache_clear()  # Never reuse path identity across separate validations.
    root = Path(root).resolve()
    manifest = read_json(root / 'build-manifest.json')
    need(manifest['template_id'] == TEMPLATE_ID, 'Unknown template identity.')
    need(manifest['snapshot'] == expected_snapshot, 'Stale website: rebuild against the current source snapshot.')
    if project is not None:
        need(formal_snapshot(project) == expected_snapshot,
             'Formal library HEAD changed; inspect the new snapshot before rebuilding.')
        from crs_unique import external_target
        from crs_store import Store
        external_target(Store(project),root)
    files = manifest['files']
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and p.name != 'build-manifest.json'}
    need(actual == set(files), 'Website file inventory changed; rebuild and validate final bytes.')
    for relative, sha in files.items():
        path, _ = local_target(root, root, relative)
        need(digest(path) == sha, 'Website bytes changed: ' + relative)
    pages = {p: scan((root / p).read_text(encoding='utf-8')) for p in files if p.endswith('.html')}
    for key, _ in NAV:
        need(key + '.html' in pages, 'Missing fixed navigation page: ' + key)
    need('00-打开研究地图.html' in pages, 'Missing browser entrance.')
    complete = manifest.get('model_schema') == MODEL_COMPLETE
    language_scopes = {}
    for relative, parsed in pages.items():
        if relative == '00-打开研究地图.html': continue
        need(parsed.navigation == [key + '.html' for key, _ in NAV], 'Fixed navigation order changed: ' + relative)
        bodies = json.loads(parsed.payload)
        need(set(bodies) in ({'en'}, {'en', 'zh'}), 'Invalid language payload.')
        language_scopes[relative] = {lang: scan(body, True) for lang, body in bodies.items()}
        scopes = language_scopes[relative]
        if 'zh' in scopes:
            if not complete:
                need(Counter(scopes['en'].formula_tokens) == Counter(scopes['zh'].formula_tokens), 'Translated TeX changed: ' + relative)
            need(scopes['en'].ids == scopes['zh'].ids, 'Language anchors must match: ' + relative)
        for scope in scopes.values():
            need(not ({'content', 'search', 'results', 'print', 'language-content', 'search-status'} & scope.ids), 'Content id conflicts with template controls.')
    local_checks = {}
    existence = {}
    def checked_target(relative, link):
        key = (relative, link)
        if key not in local_checks:
            local_checks[key] = local_target(root, (root / relative).parent, link, root / relative)
        target, fragment = local_checks[key]
        if target not in existence:
            existence[target] = target.is_file()
        need(existence[target], 'Missing local link target.')
        return target, fragment
    for relative, scopes in language_scopes.items():
        for lang, scope in scopes.items():
            for link in scope.links:
                target, fragment = checked_target(relative, link)
                target_name = target.relative_to(root).as_posix()
                if fragment:
                    targets = language_scopes.get(target_name, {})
                    requested = dict(__import__('urllib.parse', fromlist=['parse_qsl']).parse_qsl(urlsplit(link).query)).get('lang', lang)
                    active = targets.get(requested, targets.get('en'))
                    need(active is not None and fragment in active.ids, 'Broken active-language fragment: ' + relative + ': ' + link)
    checked = 0
    for relative, parsed in pages.items():
        if relative != '00-打开研究地图.html':
            need(parsed.meta.get('crs-snapshot') == expected_snapshot, 'Page snapshot missing or stale: ' + relative)
        for link in parsed.links:
            target, fragment = checked_target(relative, link)
            target_name = target.relative_to(root).as_posix()
            if fragment:
                need(target_name in pages and fragment in pages[target_name].ids, 'Broken fragment in ' + relative + ': ' + link)
            checked += 1
    if complete:
        contract = read_json(root / 'reading-contract.json')
        need(set(contract) == {'schema','home','manuscript_pages','notation_review'} and contract['schema'] == 'crs-reading-contract/v1', 'Invalid complete reading contract.')
        from crs_home import render_home
        bodies = json.loads(pages['index.html'].payload)
        need(all(bodies.get(lang) == render_home(contract['home'],lang) for lang in ['en','zh']), 'Research homepage differs from the shared content model.')
        expected_manuscripts = {p[:-5] for p in language_scopes if p.startswith('manuscript-') or p.endswith('-manuscript.html')}
        need(expected_manuscripts.issubset(set(contract['manuscript_pages'])), 'Manuscript inventory omitted a page.')
        for identity in contract['manuscript_pages']:
            need(identity+'.html' in language_scopes and set(language_scopes[identity+'.html']) == {'en','zh'}, 'Complete manuscript translation missing: '+identity)
        directory=json.loads(pages['manuscripts.html'].payload)
        need(set(directory)=={'en','zh'},'Both manuscript-directory languages are required.')
        for lang,body in directory.items():
            validate_reading_directory(body,contract['manuscript_pages'],{identity:json.loads(pages[identity+'.html'].payload)[lang] for identity in contract['manuscript_pages']})
        from crs_web_formula import check_notation
        reconstructed=[]
        for filename in language_scopes:
            payload=json.loads(pages[filename].payload)
            reconstructed.append({'id':filename[:-5], 'body':payload['en'], 'body_zh':payload.get('zh')})
        check_notation(reconstructed,contract['notation_review'])
    return {'ok': True, 'template_id': TEMPLATE_ID, 'snapshot': expected_snapshot,
            'pages': len(pages), 'links': checked, 'formulas': sum(p.formulas for p in pages.values()),
            'rendering': 'not_checked', 'delivery_complete': False, 'check_scope': 'structure_and_source_binding',
            'research_review': 'not_performed', 'terminal_state': 'success'}


def build(content, destination, renderer_dir=None, project=None, source_dir=None):
    resolved_path.cache_clear()
    data = load_model(content)
    from crs_unique import no_links
    out = no_links(destination).resolve()
    need(not out.exists(), 'Destination exists; build in a new candidate folder, then publish with a guarded backup.')
    if project is not None:
        formal = Path(project).resolve()
        from crs_unique import external_target, require_unique, project_scope
        from crs_store import Store
        source_store=Store(formal)
        external_target(source_store,destination)
        require_unique(project_scope(source_store))
        need(formal_snapshot(formal) == data['snapshot'], 'Source snapshot does not match formal HEAD.')
    templates = Path(__file__).resolve().parent.parent / 'assets' / 'research-map'
    shell = Template((templates / 'page.html').read_text(encoding='utf-8'))
    has_formulas = any('data-tex' in title_fragment(p['title']) or 'data-tex' in p['body'] or 'data-tex' in (p['body_zh'] or '') for p in data['pages'])
    if has_formulas:
        need(renderer_dir is not None and (Path(renderer_dir) / 'tex-svg.js').is_file(), 'Formula pages need a local MathJax renderer directory containing tex-svg.js; do not use a CDN.')
    source_files = []
    if source_dir is not None:
        source_root = Path(source_dir).resolve()
        need(source_root.is_dir(), 'Source directory is missing.')
        for source in source_root.rglob('*'):
            need(not source.is_symlink(), 'Source symlinks are forbidden.')
            if source.is_file():
                need(source.suffix.lower() in {'.md', '.txt', '.json', '.csv', '.pdf', '.png', '.jpg', '.zip'}, 'Unsupported source attachment type.')
                source_files.append((source, source.relative_to(source_root)))
    out.mkdir(parents=True)
    try:
        for filename in ('style.css', 'app.js'):
            shutil.copyfile(templates / filename, out / filename)
        if has_formulas:
            shutil.copytree(renderer_dir, out / 'mathjax')
        for source, relative in source_files:
            target = out / 'sources' / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        if data['schema'] == MODEL_COMPLETE:
            contract = {'schema':'crs-reading-contract/v1', **{k:data[k] for k in ['home','manuscript_pages','notation_review']}}
            (out / 'reading-contract.json').write_text(json.dumps(contract,ensure_ascii=False),encoding='utf-8')
        search = []
        for page in data['pages']:
            nav = ''.join('<a href="' + key + '.html"' + (' aria-current="page"' if key == page['id'] else '') + '>' + title + '</a>' for key, title in NAV)
            bilingual = page['body_zh'] is not None
            controls = '<div class="language" role="group" aria-label="Reading language"><button type="button" data-language="en">English</button><button type="button" data-language="zh">中文</button></div>' if bilingual else ''
            # Each language is a separate article scope. The inactive text is not injected into the DOM.
            bodies = {'en': page['body']}
            if bilingual:
                bodies['zh'] = page['body_zh']
            payload = json.dumps(bodies, ensure_ascii=False).replace('<', '\\u003c')
            formula_script = '<script defer src="mathjax/tex-svg.js"></script>' if has_formulas else ''
            rendered = shell.substitute(title=html.escape(page['title']), title_html=title_fragment(page['title']), project_title=html.escape(data['title']),
                subtitle=html.escape(data['subtitle']), snapshot=data['snapshot'], status=html.escape(data['status']),
                navigation=nav, language_controls=controls, body=page['body'], bodies=payload,
                page_id=page['id'], formula_script=formula_script, template_id=TEMPLATE_ID,
                default_language='zh' if data['schema']==MODEL_COMPLETE and page['id'] not in data['manuscript_pages'] else 'en')
            (out / (page['id'] + '.html')).write_text(rendered, encoding='utf-8')
            search.append({'title': page['title'], 'group': page['group'], 'url': page['id'] + '.html',
                           'text': ' '.join(scan(page['body']).text + scan(page['body_zh'] or '').text)})
        search_text = 'window.CRS_SEARCH=' + json.dumps(search, ensure_ascii=False).replace('<', '\\u003c') + ';window.dispatchEvent(new Event("crs-search-ready"));'
        (out / 'search-index.js').write_text(search_text, encoding='utf-8')
        entrance = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="0;url=index.html"><title>打开研究地图</title><p><a href="index.html">打开研究地图</a></p></html>'
        (out / '00-打开研究地图.html').write_text(entrance, encoding='utf-8')
        manifest = {'template_id': TEMPLATE_ID, 'snapshot': data['snapshot'], 'content_sha256': digest(content),
                    'model_schema': data['schema'],
                    'files': {p.relative_to(out).as_posix(): digest(p) for p in out.rglob('*') if p.is_file()}}
        (out / 'build-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        result = validate_site(out, data['snapshot'], project)
        # Also validate translated fragment links without treating duplicate IDs in distinct language scopes as duplicates.
        for page in data['pages']:
            for lang, body in [('en', page['body']), ('zh', page['body_zh'])]:
                if body is None:
                    continue
                parsed = scan(body, True)
                for link in parsed.links:
                    target, fragment = local_target(out, out, link, out / (page['id'] + '.html'))
                    need(target.is_file(), 'Missing language link target: ' + link)
                    if fragment and target == out / (page['id'] + '.html'):
                        need(fragment in parsed.ids, 'Missing active-language contents anchor: ' + fragment)
        return result
    except Exception:
        # Leave the owned partial candidate inspectable; never remove or overwrite a user's destination.
        raise


@public_main
def main():
    parser = SafeParser(description=__doc__)
    add_output_arguments(parser)
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('build')
    create.add_argument('--content', required=True)
    create.add_argument('--out', required=True)
    create.add_argument('--renderer-dir')
    create.add_argument('--project')
    create.add_argument('--source-dir', help='Optional local read-only source attachments copied under sources/.')
    check = commands.add_parser('check')
    check.add_argument('--site', required=True)
    check.add_argument('--expected-snapshot', required=True)
    check.add_argument('--project')
    args = parser.parse_args()
    configure_output(args)
    try:
        result = build(args.content, args.out, args.renderer_dir, args.project, args.source_dir) if args.command == 'build' else validate_site(args.site, args.expected_snapshot, args.project)
        emit_result(result)
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        emit_result(error_response(exc))
        return 2


if __name__ == '__main__':
    sys.exit(main())
