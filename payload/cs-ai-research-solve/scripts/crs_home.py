"""One prepared research-home model for browser, Markdown and project location.

Presence and links are observable. The author still checks research meaning
against the cited records; this module never creates a research conclusion.
"""
import html
import re
from html.parser import HTMLParser

SECTIONS = [
    ('question', '研究问题', 'Research question'),
    ('completion', '完成标准', 'Completion standard'),
    ('conclusions', '当前结论', 'Current conclusions'),
    ('progress', '已有进展', 'Progress'),
    ('obstacles', '当前障碍', 'Current obstacles'),
    ('next_steps', '后续可行方向', 'Existing next directions'),
    ('materials', '进入完整材料', 'Complete material'),
]


HOME_TAGS = {'p', 'section', 'div', 'ul', 'li', 'br', 'strong', 'b',
             'em', 'i', 'code', 'a', 'span', 'h2', 'h3', 'h4'}


class HomeMarkup(HTMLParser):
    """Reject structures the shared Markdown entrance cannot preserve.

    Tables, images, superscripts/subscripts and ordered lists belong in linked
    reading pages. Home formulas use explicit data-tex spans or divs;
    code stays in code elements.
    """
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.stack = []
        self.formula_tag = None

    def handle_starttag(self, tag, attrs):
        if tag not in HOME_TAGS:
            raise ValueError('Unsupported shared research-home markup: ' + tag)
        if self.formula_tag is not None:
            raise ValueError('Research-home formula fallback must contain text only.')
        if tag == 'ul' and 'ul' in self.stack:
            raise ValueError('Nested research-home lists require a linked reading page.')
        if tag == 'li' and (not self.stack or self.stack[-1] != 'ul'):
            raise ValueError('Research-home list items require an explicit list parent.')
        if 'data-tex' in dict(attrs):
            if tag not in {'span', 'div'}:
                raise ValueError('Research-home formulas require a span or div.')
            self.formula_tag = tag
        if tag != 'br':
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        if tag != 'br':
            raise ValueError('Self-closing research-home markup is only valid for br.')
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag not in HOME_TAGS:
            raise ValueError('Unsupported shared research-home markup: ' + tag)
        if tag == 'br' or not self.stack or self.stack[-1] != tag:
            raise ValueError('Research-home markup requires balanced explicit nesting.')
        self.stack.pop()
        if self.formula_tag == tag:
            self.formula_tag = None

    def close(self):
        super().close()
        if self.stack:
            raise ValueError('Research-home markup has an unclosed element.')


def validate_home(home):
    if not isinstance(home, dict) or set(home) != {x[0] for x in SECTIONS}:
        raise ValueError('Research home requires question, completion, conclusions, progress, obstacles, next_steps and materials.')
    for key, _, _ in SECTIONS:
        section = home[key]
        if not isinstance(section, dict) or set(section) != {'en', 'zh'}:
            raise ValueError('Each research-home section requires complete en and zh content: ' + key)
        for lang, body in section.items():
            if not isinstance(body, str) or not body.strip() or '<a ' not in body:
                raise ValueError('Each research-home section needs substantive text and a specific evidence link: ' + key + '/' + lang)
            markup = HomeMarkup(convert_charrefs=True)
            markup.feed(body)
            markup.close()
            if re.sub('<[^>]*>', '', body).strip() in {'TODO', 'TBD'}:
                raise ValueError('Unprepared research-home content: ' + key)
    return home


def render_home(home, lang):
    validate_home(home)
    toc = '<div class="contents"><p><strong>' + ('本页目录' if lang == 'zh' else 'On this page') + '</strong></p><ul>'
    toc += ''.join('<li><a href="#home-' + key + '">' + (zh if lang == 'zh' else en) + '</a></li>' for key, zh, en in SECTIONS)
    toc += '</ul></div>'
    return toc + ''.join('<section class="home-section" id="home-' + key + '"><h2>' + (zh if lang == 'zh' else en) + '</h2>' + home[key][lang] + '</section>' for key, zh, en in SECTIONS)


class MarkdownHome(HTMLParser):
    """Restricted semantic HTML to Obsidian Markdown, preserving exact TeX."""
    def __init__(self, prefix):
        super().__init__(convert_charrefs=True)
        self.prefix = prefix.rstrip('/') + '/'
        self.parts = []
        self.links = []
        self.formula_depth = 0
        self.after_formula = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if self.formula_depth:
            if tag not in {'br', 'hr', 'img'}: self.formula_depth += 1
            return
        self.after_formula = False
        if 'data-tex' in a:
            if self.parts:
                self.parts[-1] = re.sub(r'([\u3400-\u9fff\u3000-\u303f\uff00-\uffef])[ \t]+$', r'\1', self.parts[-1])
            marker = '$$' if a.get('data-display') == 'true' else '$'
            self.parts.append(marker + a['data-tex'] + marker)
            self.formula_depth = 1
        elif tag in {'p', 'section', 'div', 'ul', 'ol'}: self.parts.append('\n\n')
        elif tag in {'h2', 'h3', 'h4'}: self.parts.append('\n\n' + '#' * int(tag[1]) + ' ')
        elif tag == 'li': self.parts.append('\n- ')
        elif tag == 'br': self.parts.append('\n')
        elif tag in {'strong', 'b'}: self.parts.append('**')
        elif tag in {'em', 'i'}: self.parts.append('*')
        elif tag == 'code': self.parts.append('`')
        elif tag == 'a':
            href = a.get('href', '')
            self.links.append(href if href.startswith('#') else self.prefix + href)
            self.parts.append('[')

    def handle_endtag(self, tag):
        if self.formula_depth:
            self.formula_depth -= 1
            if self.formula_depth == 0: self.after_formula = True
            return
        if tag in {'p', 'h2', 'h3', 'h4', 'ul', 'ol', 'section', 'div'}: self.parts.append('\n\n')
        elif tag in {'strong', 'b'}: self.parts.append('**')
        elif tag in {'em', 'i'}: self.parts.append('*')
        elif tag == 'code': self.parts.append('`')
        elif tag == 'a': self.parts.append('](' + self.links.pop() + ')')

    def handle_data(self, text):
        if not self.formula_depth:
            if self.after_formula:
                text = re.sub(r'^[ \t]+(?=[\u3400-\u9fff\u3000-\u303f\uff00-\uffef])', '', text)
            self.after_formula = False
            self.parts.append(text)


def home_markdown(home, prefix):
    validate_home(home)
    chunks = []
    for key, zh, _ in SECTIONS:
        parser = MarkdownHome(prefix)
        parser.feed(home[key]['zh'])
        parser.close()
        chunks.append('## ' + zh + '\n\n' + re.sub(r'\n{3,}', '\n\n', ''.join(parser.parts)).strip())
    return '\n\n'.join(chunks) + '\n'
