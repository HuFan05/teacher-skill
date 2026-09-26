"""Lossless formula, code and table transport with fail-closed map presentation.

Formulas travel as complete TeX ($...$ or $$...$$); code, pseudocode and
identifiers stay literal in backtick spans or fences; table cells keep their
formulas intact. No semantic equivalence is inferred. Plain-text formula
notation needs explicitly reviewed, exact source-span replacements; original
research objects stay intact.
"""
import hashlib
import html
import json
import re
from pathlib import Path

from crs_model import CRSError

TICK = chr(96)
# High-signal notation only: this is not a formula-language recognizer.
MACHINE = re.compile(r'(?<![\w/])(?:int_[A-Za-z0-9{]|integral_[A-Za-z{]|sum_?\{|lim_\(|(?:sin|cos|tan|log|exp|softmax|argmax|argmin)\s*\(|[A-Za-z][A-Za-z0-9]*_\(|[A-Za-z][A-Za-z0-9]*\[[^\]\n]+\]\s*=)|\^\s*\(|(?<![\w/])[A-Za-z]_[A-Za-z0-9]{1,3}\b|(?:<=|>=)')
BARE_TEX = re.compile(r'\\(?:frac|sum|int|mu|pi|alpha|beta|gamma|epsilon|lim|sqrt|begin|end)\b')

def _spans(text):
    """Yield prose, literal code/link, or whole formula tokens, without escaping."""
    i = 0
    while i < len(text):
        if text[i] == TICK:
            run = len(text[i:]) - len(text[i:].lstrip(TICK))
            marker = TICK * run
            end = text.find(marker, i + run)
            if end >= 0:
                yield 'literal', text[i:end+run]; i=end+run; continue
        if text.startswith('~~~', i) and (i == 0 or text[i-1] == '\n'):
            end=text.find('\n~~~',i+3)
            if end >= 0:
                end=text.find('\n',end+1)
                if end < 0:end=len(text)
                yield 'literal',text[i:end];i=end;continue
        if text.startswith('[[',i):
            end=text.find(']]',i+2)
            if end >= 0:
                yield 'literal',text[i:end+2];i=end+2;continue
        if text[i] == '[':
            match=re.match(r'\[((?:\\.|[^\]\\\n])*)\]\([^\n)]*\)',text[i:])
            if match:
                yield 'literal',match[0];i+=len(match[0]);continue
        if text[i] == '$' and (i == 0 or text[i-1] != '\\'):
            marker='$$' if text.startswith('$$',i) else '$'
            end=i+len(marker)
            while True:
                end=text.find(marker,end)
                if end < 0:
                    raise CRSError('map_formula_delimiter','Unclosed formula delimiter; repair the source or reviewed presentation replacements.')
                if text[end-1] != '\\':break
                end+=len(marker)
            body=text[i+len(marker):end]
            if not body.strip() or (marker=='$' and '\n' in body):
                raise CRSError('map_formula_delimiter','Empty or multiline inline formula; use complete $...$ or $$...$$.')
            yield 'formula',text[i:end+len(marker)];i=end+len(marker);continue
        start=i;i+=1
        while i<len(text) and text[i] not in TICK+'[$' and not text.startswith('~~~',i):i+=1
        yield 'prose',text[start:i]

def spans(text):
    pending=''
    for kind,part in _spans(text):
        if kind=='prose':pending+=part;continue
        if pending:yield 'prose',pending;pending=''
        yield kind,part
    if pending:yield 'prose',pending

def validate(text):
    count=0; issues=set()
    for kind,value in spans(text):
        if kind=='literal':continue
        if kind=='prose':
            if MACHINE.search(value) or BARE_TEX.search(value):issues.add('unconverted_notation')
            if any(x in value for x in (r'\(',r'\)',r'\[',r'\]')):issues.add('unsupported_delimiter')
            if re.search(r'&(?:amp;)?#(?:91|93);',value):issues.add('escaped_brackets')
            continue
        count+=1
        marker=2 if value.startswith('$$') else 1
        tex=value[marker:-marker]
        if any(ord(c)<32 and c not in '\n\r\t' for c in tex):issues.add('control_character')
        if '&#' in tex or '&lt;' in tex or '&gt;' in tex:issues.add('formula_entity')
        if '…' in tex:issues.add('truncated_formula')
        if re.search(r'\\\\(?:frac|sum|int|mu|pi|sqrt|lim)\b',tex):issues.add('double_escaped_tex')
        depth=0
        for m in re.finditer(r'(?<!\\)[{}]',tex):
            depth+=1 if m[0]=='{' else -1
            if depth<0:issues.add('tex_group')
        if depth:issues.add('tex_group')
        env=[]
        for m in re.finditer(r'\\(begin|end)\{([^}]+)\}',tex):
            if m[1]=='begin':env.append(m[2])
            elif not env or env.pop()!=m[2]:issues.add('tex_environment')
        if env:issues.add('tex_environment')
    if issues:
        raise CRSError('map_formula_invalid','Formula presentation is not ready. Supply reviewed --formula-replacements before generating the map; mark code and pseudocode as code, and do not edit immutable objects or hide formulas in code.',{'issues':sorted(issues),'formulas':count,'zero_formula_false_pass_blocked':count==0})
    return {'formulas':count,'structure':'passed','rendering':'not_checked','live_obsidian_checked':False}

def safe_label(value):
    out=[]
    for kind,part in spans(str(value)):
        if kind=='formula':
            # A table separator in TeX must not split a Markdown table.
            out.append(part.replace('|',r'\vert '))
        elif kind=='literal':out.append(part)
        else:out.append(part.replace('\\','\\\\').replace('[','&#91;').replace(']','&#93;').replace('|','&#124;').replace('\n',' ').replace('<','&lt;').replace('>','&gt;'))
    return ''.join(out)

def excerpt(value,limit):
    out=[];size=0
    for kind,part in spans(value):
        if size+len(part)<=limit:
            out.append(part);size+=len(part);continue
        if kind=='prose':
            room=max(0,limit-size)
            chunk=part[:room]
            # Never cut a word / raw machine expression into an innocent prefix.
            if room<len(part) and room and not part[room].isspace():
                chunk=chunk.rsplit(' ',1)[0] if ' ' in chunk else ''
            out.append(chunk.rstrip())
        elif not out:out.append(part)
        break
    result=''.join(out)
    return result+('…' if result!=value else '')

class Presentation:
    def __init__(self,path=None):
        self.rows=[]
        if path:
            obj=json.loads(Path(path).read_text(encoding='utf-8'))
            if not isinstance(obj,list):
                raise CRSError('map_formula_replacements','Expected a JSON list of exact {source, tex} pairs.')
            seen=set()
            for row in obj:
                if not isinstance(row,dict) or set(row)!={'source','tex'} or not all(isinstance(row[k],str) and row[k] for k in row):
                    raise CRSError('map_formula_replacements','Every replacement must contain only nonempty source and tex strings.')
                source=row['source'];tex=row['tex']
                if source in seen or any(x in source for x in ('$', '\n', TICK)):
                    raise CRSError('map_formula_replacements','Sources must be unique plain single-line formula spans.')
                if any(k in tex for k in ('$',TICK,'\n')) or not (MACHINE.search(source) or BARE_TEX.search(source)):
                    raise CRSError('map_formula_replacements','Only recognizable formula source spans and delimiter-free TeX are allowed.')
                validate('$'+tex+'$');seen.add(source);self.rows.append((source,'$'+tex+'$'))
            self.rows.sort(key=lambda x:len(x[0]),reverse=True)

    def apply(self,text):
        result=[]
        for kind,part in spans(text):
            if kind!='prose':result.append(part);continue
            part=html.unescape(part)
            # One substitution pass: generated TeX can never be rewritten.
            if self.rows:
                lookup=dict(self.rows)
                part=re.sub('|'.join(re.escape(x) for x in lookup),lambda m:lookup[m[0]],part)
            result.append(part)
        return ''.join(result)

    def readback(self,path,expected):
        data=Path(path).read_bytes()
        if data!=expected.encode('utf-8'):
            raise CRSError('map_formula_readback','Final map bytes differ from checked presentation.')
        return dict(validate(data.decode('utf-8')),sha256=hashlib.sha256(data).hexdigest())

