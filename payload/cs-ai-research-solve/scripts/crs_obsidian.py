"""Local, derived Obsidian views. Never change the formal library."""

import crs_temp
from pathlib import Path
import hashlib
import re
import shutil
import tempfile
from urllib.parse import unquote

from crs_model import CRSError
from crs_store import ordinary


def write_map(store, target, body, snapshot, object_paths):
    from crs_unique import external_target
    target = external_target(store, target)
    if target.resolve().is_relative_to(store.root.resolve()):
        raise CRSError('map_output_inside_project', 'Place the map in the enclosing research folder, outside the machine data directory.')
    if target.suffix.lower() != '.md':
        raise CRSError('map_extension', 'An Obsidian map needs a .md filename.')
    if not target.parent.is_dir():
        raise CRSError('map_parent_missing', 'Create the named research folder before rendering its map.')
    view_name = target.stem + '-阅读对象'
    if any(c in view_name for c in '[]#|^'):
        raise CRSError('map_name_unsafe', 'Choose a map filename without Obsidian link delimiters.')
    view_dir = ordinary(target.parent / view_name)
    if target.exists() or view_dir.exists():
        raise CRSError('map_output_exists', 'Choose a new map name; existing maps and reading views are never overwritten.')
    anchors = set(re.findall(r'<a id="([a-z0-9-]+)"></a>', body))
    text = re.sub(r'<a id="([a-z0-9-]+)"></a>\n(?:\n)*([^\n]+)', lambda m: m[2] + ' ^' + m[1], body)
    paths = {unquote(url): sha for sha, url in object_paths.items() if url is not None}
    referenced = set()
    count = 0
    output = []
    fence = None
    for line in text.splitlines(keepends=True):
        marker = re.match(r'^(`{3,}|~{3,})', line)
        if marker:
            if fence is None:
                fence = marker[1]
            elif marker[1][0] == fence[0] and len(marker[1]) >= len(fence):
                fence = None
            output.append(line)
            continue
        if fence:
            output.append(line)
            continue
        def link(m):
            nonlocal count
            label, url = m.groups()
            url = unquote(url)
            if url.startswith('#'):
                if url[1:] not in anchors:
                    raise CRSError('map_anchor_missing', 'A generated map link has no matching anchor.')
                destination = '#^' + url[1:]
            elif url in paths:
                sha = paths[url]
                referenced.add(sha)
                destination = view_name + '/' + sha
            else:
                return m[0]
            count += 1
            # Markdown block links avoid alias separators in table cells.
            if line.lstrip().startswith('|'):
                from urllib.parse import quote
                return '[' + label + '](' + quote(destination + ('.md' if not destination.startswith('#') else ''), safe='/#^') + ')'
            label = label.replace('\\[', '&#91;').replace('\\]', '&#93;').replace('|', '&#124;')
            return '[[' + destination + '|' + label + ']]'
        line = re.sub(r'\[((?:\\.|[^\]\\\n])*)\]\(([^)\n]+)\)', link, line)
        # These are escaped literal brackets from render_map.label, not TeX.
        line = line.replace('\\[', '&#91;').replace('\\]', '&#93;')
        output.append(line)
    text = ''.join(output)
    from crs_formula import Presentation, validate
    text = Presentation().apply(text)
    validate(text)
    if '<a id=' in text:
        raise CRSError('map_anchor_unconverted', 'An unsupported generated anchor remains; no files were published.')
    scratch = Path(crs_temp.mkdtemp(prefix='.crs-view-', dir=target.parent))
    published_views = False
    created_map = False
    try:
        for sha in sorted(referenced):
            path = store.resolve(sha)
            size = path.stat().st_size
            header = f'原始对象的派生阅读视图；不构成新审核。\n\nSHA-256：`{sha}`\n\n快照：`{snapshot}`\n\n'
            if size <= 2 * 1024 * 1024:
                data = path.read_bytes()
                if hashlib.sha256(data).hexdigest() != sha:
                    raise CRSError('asset_hash_mismatch', 'An object changed while preparing its reading view.')
                try:
                    content = data.decode('utf-8')
                    if '\x00' in content:
                        raise UnicodeError('binary data')
                    ticks = '`' * max(3, 1 + max([len(x) for x in re.findall(r'`+', content)] + [0]))
                    header += ticks + 'text\n' + content + ('' if content.endswith('\n') else '\n') + ticks + '\n'
                except UnicodeError:
                    header += f'二进制对象，大小 {size} 字节。使用 asset 命令定位原件；此页未内嵌内容。\n'
            else:
                header += f'大型对象，大小 {size} 字节。使用 asset 命令定位原件；此页未内嵌内容。\n'
            (scratch / (sha + '.md')).write_text(header, encoding='utf-8', newline='\n')
        if store.head() != snapshot:
            raise CRSError('head_changed', 'The project changed while rendering; retry against its new snapshot.')
        # Never merge with or replace a directory belonging to another run.
        view_dir.mkdir()
        published_views = True
        for child in scratch.iterdir():
            child.rename(view_dir / child.name)
        with target.open('x', encoding='utf-8', newline='\n') as handle:
            created_map = True
            handle.write(text)
        formula_check = Presentation().readback(target, text)
        return {'formula_check': formula_check, 'snapshot': snapshot, 'map': str(target), 'format': 'obsidian', 'reading_views': str(view_dir), 'object_views': len(referenced), 'links': count}
    except BaseException:
        if created_map:
            target.unlink()
        if published_views:
            shutil.rmtree(view_dir)
        raise
    finally:
        shutil.rmtree(scratch)
