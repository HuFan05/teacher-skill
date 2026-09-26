"""Detect omitted formula markup and apply reviewed, exact HTML edits.

Detection produces candidates, never guessed TeX. Code, pseudocode and
identifiers belong in code/pre elements; explicit exemptions identify other
literal text by exact digest and a review reason.
"""
import hashlib
import html
import re
from html.parser import HTMLParser

SIGNAL = re.compile(r'\\(?:frac|sum|int|mu|pi|alpha|beta|gamma|epsilon|sqrt|begin|end|lVert|mathbb)\b|\$[^$\n]+\$|(?:<=|>=)|[∈∉∀∃≤≥≠∑∫√]|(?<![\w/])[A-Za-z][A-Za-z0-9]*_(?:\{|\*|[A-Za-z0-9])|(?<![\w/])[A-Za-z][A-Za-z0-9]*\^|(?<![\w/])(?:sqrt|sin|cos|tan|log|exp|softmax|argmax|argmin)\s*\(')
VOID = {'br', 'hr', 'img', 'input', 'meta', 'link', 'col', 'wbr', 'source'}


def text_hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


class ProseScan(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.blocks = []
        self.formulas = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        protected = bool(self.stack and self.stack[-1][1]) or 'data-tex' in a or tag in {'script', 'style'}
        literal = bool(self.stack and self.stack[-1][2]) or tag in {'code', 'pre', 'kbd'}
        if 'data-tex' in a: self.formulas.append(a['data-tex'])
        if tag not in VOID: self.stack.append((tag, protected, literal))

    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, text):
        # Formula spans are protected; code, pseudocode and keyboard text inside
        # code/pre/kbd are literal presentation, not unmarked formula prose.
        if (self.stack and (self.stack[-1][1] or self.stack[-1][2])) or not text.strip(): return
        if SIGNAL.search(text):
            self.blocks.append({'sha256':text_hash(text),'text':text,'context':self.stack[-1][0] if self.stack else 'text'})


def inspect(body):
    p=ProseScan();p.feed(body);p.close()
    return {'formulas':p.formulas,'suspects':p.blocks}


def check_notation(pages, review):
    if not isinstance(review,dict) or set(review) != {'exemptions','page_reviews'}:
        raise ValueError('Notation review requires exact exemptions and page_reviews.')
    exemptions={}
    for row in review['exemptions']:
        if not isinstance(row,dict) or set(row)!={'sha256','reason'} or not isinstance(row['reason'],str) or len(row['reason'].strip())<12:
            raise ValueError('Literal-notation exemption requires an exact text digest and a specific reason.')
        if row['sha256'] in exemptions: raise ValueError('Duplicate notation exemption.')
        exemptions[row['sha256']]=row['reason']
    reviewed={}
    for row in review['page_reviews']:
        if set(row)!={'page','language','body_sha256','formula_prose','reason'} or row['language'] not in {'en','zh'} or not isinstance(row['formula_prose'],bool) or len(row['reason'].strip())<12:
            raise ValueError('Invalid source-aligned page review.')
        identity=(row['page'],row['language'])
        if identity in reviewed:raise ValueError('Duplicate page-language review.')
        reviewed[identity]=row
    failures=[];used=set();expected=set()
    for page in pages:
        for lang,key in [('en','body'),('zh','body_zh')]:
            body=page[key]
            if body is None:continue
            identity=(page['id'],lang);expected.add(identity)
            row=reviewed.get(identity)
            if not row or row['body_sha256']!=text_hash(body):
                failures.append({'page':page['id'],'language':lang,'issue':'missing_or_stale_page_review'});continue
            scanned=inspect(body)
            if row['formula_prose'] and not scanned['formulas']:
                failures.append({'page':page['id'],'language':lang,'issue':'formula_prose_has_zero_formulas'})
            for block in scanned['suspects']:
                if block['sha256'] in exemptions:used.add(block['sha256'])
                else:failures.append({'page':page['id'],'language':lang,'issue':'unmarked_formula_notation','sha256':block['sha256'],'excerpt':block['text'][:160]})
    if set(reviewed)!=expected:raise ValueError('Page review inventory differs from all active language bodies.')
    if set(exemptions)-used:raise ValueError('Unused notation exemptions; remove stale blanket waivers.')
    if failures:raise ValueError('Unresolved formula presentation: '+str(failures[:8])+'; total='+str(len(failures)))
    return {'page_languages':len(expected),'literal_exemptions':len(used),'residual_check':'passed','semantic_boundary':'source-aligned author review; not an audit of any proof, argument or experiment'}


def apply_exact_edits(body, edits):
    """An edit binds exact old HTML, expected occurrences, and prepared new HTML.

    This supports source-verified repairs spanning erroneous emphasis tags. It
    does not normalize or infer the meaning of either string.
    """
    for edit in edits:
        if set(edit)!={'before','after','count','reason'} or not edit['before'] or not edit['after'] or not isinstance(edit['count'],int) or edit['count']<1 or not edit['reason'].strip():
            raise ValueError('Malformed exact presentation edit.')
        if body.count(edit['before'])!=edit['count']:
            raise ValueError('Source presentation drift; exact edit occurrence count changed.')
        body=body.replace(edit['before'],edit['after'])
    return body
