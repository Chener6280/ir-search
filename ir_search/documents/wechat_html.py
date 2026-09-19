"""WeChat-specific source extraction, independent of skills and summarization.

The initial visibility/opacity pair on the one known article container is a
rendering delay, not a reason to discard source text already present in HTML.
All other hidden elements remain excluded. Structure is separate from citations.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from html import escape
from html.parser import HTMLParser
import re
from urllib.parse import urljoin, urlunsplit, quote, parse_qsl


@dataclass
class _Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)
    hidden: bool = False


_VOID = {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}
_BLOCK = {'p','div','section','article','li','blockquote','pre','h1','h2','h3','h4','h5','h6','tr','ul','ol','table'}


def _public_reference(value, base):
    # Imported lazily because the public transport also imports document parsers.
    from ir_search.infrastructure.public_web import _url
    from ir_search.registry import DataAdapterError
    try:
        if not isinstance(value, str) or not value.strip(): return None
        parsed, _, _ = _url(urljoin(base, value.strip()))
        if any(k.lower() in {'pass_ticket','wxtoken','uin','verifycode'} for k, _ in parse_qsl(parsed.query)):
            return None
        return quote(urlunsplit(parsed._replace(fragment='')), safe="/:?[]@!$&'+,;=%#")
    except (DataAdapterError, ValueError):
        return None


class WechatHTMLParser(HTMLParser):
    def __init__(self, base_url='https://mp.weixin.qq.com/'):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.root = _Node('root')
        self.stack = [self.root]
        self.articles = []
        self.meta = {}
        self.title, self.publisher, self.author, self.publication = [], [], [], []
        self.initial_visibility = False
        self.nodes = 0

    @property
    def article_found(self):
        return bool(self.articles)

    def handle_starttag(self, tag, attrs):
        self.nodes += 1
        if self.nodes > 100000 or len(self.stack) > 256: raise ValueError('HTML structure limit')
        attrs = {k: v or '' for k, v in attrs}
        style = {k.strip().lower(): v.strip().lower().replace('!important','').strip()
                 for k, sep, v in (part.partition(':') for part in attrs.get('style','').split(';')) if sep}
        root = tag == 'div' and attrs.get('id') == 'js_content'
        initial = (root and 'rich_media_content' in attrs.get('class','').split()
                   and style.get('visibility') == 'hidden' and style.get('opacity') in {'0','0.0'}
                   and style.get('display') != 'none' and 'hidden' not in attrs
                   and attrs.get('aria-hidden','').lower() != 'true' and not self.stack[-1].hidden)
        hidden = (self.stack[-1].hidden or tag in {'head','script','style','noscript','svg','template','iframe'}
                  or 'hidden' in attrs or attrs.get('aria-hidden','').lower() == 'true'
                  or style.get('display') == 'none'
                  or not initial and (style.get('visibility') in {'hidden','collapse'} or style.get('opacity') in {'0','0.0'}))
        node = _Node(tag, attrs, hidden=hidden)
        self.stack[-1].children.append(node)
        if root:
            self.articles.append(node)
            self.initial_visibility |= initial
        if tag == 'meta':
            name = attrs.get('property') or attrs.get('name')
            if name and attrs.get('content'): self.meta[name.lower()] = attrs['content']
        if tag not in _VOID: self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID: self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in _VOID: return
        for i in range(len(self.stack)-1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.nodes += 1
        if self.nodes > 100000: raise ValueError('HTML structure limit')
        # Metadata capture is independent of <head>'s exclusion from article text.
        for node in reversed(self.stack):
            ident = node.attrs.get('id')
            target = (self.title if node.tag == 'title' or ident == 'activity-name' else
                      self.publisher if ident == 'js_name' else self.author if ident == 'js_author_name' else
                      self.publication if ident == 'publish_time' else None)
            if target is not None:
                target.append(data)
                break
        if not self.stack[-1].hidden: self.stack[-1].children.append(data)

    def _source(self, vendor):
        if len(self.articles) > 1: raise ValueError('Ambiguous article containers')
        return self.articles[0] if self.articles else self.root if vendor else _Node('empty')

    def text(self, *, vendor=False):
        return self.content(vendor=vendor)[0]

    def content(self, *, vendor=False, max_chars=100000):
        builder = _Structure(self.base_url, max_chars)
        builder.walk(self._source(vendor))
        builder.flush()
        return builder.result(self.initial_visibility, 'article_container' if self.articles else 'vendor_fragment')

    def challenge_visible(self):
        def outside(node):
            if isinstance(node, str): return node
            if node.hidden or any(node is n for n in self.articles): return ''
            return ' '.join(outside(n) for n in node.children)
        visible = outside(self.root)
        return any(phrase in visible for phrase in ('环境异常', '请完成验证', '访问过于频繁', '该内容已被发布者删除', '此内容因违规无法查看'))


class _Structure:
    def __init__(self, base, limit):
        self.base, self.limit = base, limit
        self.text = ''
        self.pending = []
        self.kind, self.attributes = 'paragraph', {}
        self.blocks, self.images, self.links = [], [], []
        self.truncated = False
        self.links_truncated = False
        self.table_id, self.table_count = None, 0

    def flush(self):
        value = ''.join(self.pending).strip() if self.kind == 'preformatted' else ' '.join(''.join(self.pending).split())
        self.pending = []
        if not value: return
        if len(self.blocks) >= 2000 or len(self.text) >= self.limit:
            self.truncated = True
            return
        start = len(self.text) + bool(self.text)
        attributes = dict(self.attributes)
        if start + len(value) > self.limit:
            self.truncated = True
            attributes.pop('cells', None); attributes.pop('header_cells', None)
            attributes.pop('cell_spans', None)
        value = value[:max(0, self.limit-start)]
        if not value:
            self.truncated = True
            return
        self.text += ('\n' if self.text else '') + value
        self.blocks.append({'kind':self.kind, 'start_char':start, 'end_char':len(self.text), **attributes})

    def walk(self, node, ancestors=()):
        if isinstance(node, str):
            self.pending.append(node)
            return
        if node.hidden: return
        tag = node.tag
        previous_table = self.table_id
        if tag == 'table':
            self.table_count += 1
            self.table_id = self.table_count
        if tag in _BLOCK or tag in {'br','img'}: self.flush()
        old = self.kind, self.attributes
        if re.fullmatch('h[1-6]', tag): self.kind, self.attributes = 'heading', {'level':int(tag[1])}
        elif tag == 'li': self.kind, self.attributes = 'list_item', {'ordered': 'ol' in ancestors}
        elif tag == 'blockquote': self.kind, self.attributes = 'quote', {}
        elif tag == 'pre': self.kind, self.attributes = 'preformatted', {}
        elif tag == 'tr': self.kind, self.attributes = 'table_row', {}
        elif tag in {'p','div','section','article'} and self.kind not in {'list_item','quote','preformatted','table_row'}:
            self.kind, self.attributes = 'paragraph', {}
        if tag == 'img':
            src = next((node.attrs[k] for k in ('data-src','data-original','src') if node.attrs.get(k)), None)
            url = _public_reference(src, self.base)
            if url and len(self.images) < 100 and len(self.blocks) < 2000 and len(self.text) < self.limit:
                index = len(self.images)
                self.images.append({'url':url, 'alt':node.attrs.get('alt','')[:1000], 'position_char':len(self.text),
                                    'status':'not_downloaded', 'ocr_status':'not_requested'})
                self.blocks.append({'kind':'image','image_index':index, 'start_char':len(self.text),'end_char':len(self.text)})
            elif src: self.truncated = True
        if tag == 'a':
            url = _public_reference(node.attrs.get('href'), self.base)
            if url and len(self.links) < 100:
                label = _node_text(node)
                self.links.append({'url':url, 'text':label[:1000], 'kind':'pdf' if url.lower().split('?')[0].endswith('.pdf') else 'web_page',
                                   'status':'discovered_not_retrieved'})
            elif url: self.links_truncated = True
        if tag == 'tr':
            cells = [n for n in node.children if isinstance(n, _Node) and n.tag in {'td','th'} and not n.hidden]
            if cells:
                values = [' '.join(_node_text(c).split()) for c in cells]
                span = lambda c,k: int(c.attrs[k]) if re.fullmatch(r'[1-9][0-9]{0,2}',c.attrs.get(k,'')) else 1
                self.attributes = {'cells':values, 'header_cells':[c.tag == 'th' for c in cells],
                                   'table_id':self.table_id,
                                   'cell_spans':[{'colspan':span(c,'colspan'),'rowspan':span(c,'rowspan')} for c in cells]}
                self.pending.append(' | '.join(values))
                self.flush()
                for cell in cells: self._table_assets(cell)
            else:
                for child in node.children: self.walk(child, ancestors+(tag,))
        else:
            for child in node.children: self.walk(child, ancestors+(tag,))
        if tag in _BLOCK: self.flush()
        self.kind, self.attributes = old
        self.table_id = previous_table

    def _table_assets(self, node):
        if isinstance(node, str) or node.hidden: return
        if node.tag == 'img':
            before = len(self.images)
            self.walk(node)
            if len(self.images) > before: self.images[-1]['position_basis'] = 'after_containing_table_row'
        elif node.tag == 'a':
            url = _public_reference(node.attrs.get('href'), self.base)
            if url and len(self.links) < 100:
                self.links.append({'url':url,'text':_node_text(node)[:1000],'kind':'web_page','status':'discovered_not_retrieved'})
            elif url: self.links_truncated = True
        for child in node.children: self._table_assets(child)

    def result(self, initial, scope):
        for block in self.blocks:
            if block['kind'] != 'image': block['text'] = self.text[block['start_char']:block['end_char']]
        article = {'schema_version':'1.0', 'text_hash':hashlib.sha256(self.text.encode()).hexdigest(),
                   'blocks':self.blocks, 'images':self.images, 'links':self.links,
                   'links_truncated':self.links_truncated,
                   'structure_truncated':self.truncated, 'scope':scope, 'initial_visibility_recovered':initial}
        article['markdown'] = _markdown(article)
        return self.text, article


def _node_text(node):
    if isinstance(node, str): return node
    if node.hidden: return ''
    return ''.join(_node_text(n) for n in node.children)


def _markdown(article, image_paths=None):
    """A reading view only. Never used as the source for text-offset citations."""
    result = []
    rows, table_id = [], None
    image_paths = image_paths or {}
    for block in article['blocks']:
        if block.get('kind') not in {'paragraph','heading','list_item','quote','preformatted','table_row','image'}:
            raise ValueError('article_block_invalid')
        if block['kind'] == 'heading' and (type(block.get('level')) is not int or not 1 <= block['level'] <= 6):
            raise ValueError('article_heading_invalid')
        kind = block['kind']
        if rows and (kind != 'table_row' or block.get('table_id') != table_id):
            result.append('<table>'+''.join(rows)+'</table>'); rows = []
        value = escape(block.get('text',''), quote=False)
        value = re.sub(r'([\\`*_\[\]])', r'\\\1', value)
        if kind == 'image':
            item = article['images'][block['image_index']]
            target = image_paths.get(item['url'])
            label = escape(item.get('alt') or '原文图片', quote=False).replace('[','\\[').replace(']','\\]')
            # Remote images are references, not automatic rendering/tracking fetches.
            result.append(('!' if target else '') + f'[{label}](<{target or item["url"]}>)')
        elif kind == 'heading': result.append('#' * block['level'] + ' ' + value)
        elif kind == 'list_item': result.append(('1. ' if block['ordered'] else '- ') + value)
        elif kind == 'quote': result.append('> ' + value)
        elif kind == 'preformatted': result.append('<pre>' + escape(block['text']) + '</pre>')
        elif kind == 'table_row':
            cells = block.get('cells', [block['text']])
            heads = block.get('header_cells', [False]*len(cells))
            spans = block.get('cell_spans', [{'colspan':1,'rowspan':1} for _ in cells])
            table_id = block.get('table_id')
            rows.append('<tr>' + ''.join(f'<{"th" if h else "td"} colspan="{int(s["colspan"])}" rowspan="{int(s["rowspan"])}">{escape(c)}</{"th" if h else "td"}>'
                for c,h,s in zip(cells,heads,spans)) + '</tr>')
        else: result.append(value)
    if rows: result.append('<table>'+''.join(rows)+'</table>')
    if article.get('links'):
        result.append('## 原文链接')
        result.extend(f'- [{escape(item["text"] or "链接", quote=False).replace("[", "&#91;").replace("]", "&#93;")}](<{item["url"]}>)'
                      for item in article['links'])
    return '\n\n'.join(result)


def _clip_article(article, text):
    """Restrict structure to the exact text prefix returned to the caller."""
    if not article: return {}
    size = len(text)
    is_full = article.get('text_hash') == hashlib.sha256(text.encode()).hexdigest()
    blocks, images = [], []
    for original in article.get('blocks', []):
        start, end = original['start_char'], original['end_char']
        if start >= size and not (is_full and original['kind'] == 'image' and start == size): continue
        block = dict(original)
        if block['kind'] == 'image':
            item = dict(article['images'][block['image_index']])
            block['image_index'] = len(images); images.append(item)
        else:
            block['end_char'] = min(end, size)
            block['text'] = text[start:block['end_char']]
            if end > size:
                block.pop('cells', None); block.pop('header_cells', None)
                block.pop('cell_spans', None)
        blocks.append(block)
    result = {**article, 'blocks':blocks, 'images':images,
              'text_hash':hashlib.sha256(text.encode()).hexdigest(),
              'structure_truncated':article.get('structure_truncated',False) or article.get('text_hash') != hashlib.sha256(text.encode()).hexdigest()}
    # Links are metadata of the full fetched source, not evidence from omitted text.
    result['markdown'] = _markdown(result)
    return result


def _validate_article(article, text):
    if not isinstance(article, dict) or article.get('schema_version') != '1.0' or article.get('text_hash') != hashlib.sha256(text.encode()).hexdigest():
        raise ValueError('article_text_mismatch')
    if any(not isinstance(article.get(k), list) for k in ('blocks','images','links')):
        raise ValueError('article_structure_invalid')
    if len(article['blocks']) > 2000 or len(article['images']) > 100 or len(article['links']) > 100:
        raise ValueError('article_structure_limit')
    for item in article['images']+article['links']:
        if not isinstance(item,dict) or not _public_reference(item.get('url'), 'https://mp.weixin.qq.com/'):
            raise ValueError('article_url_invalid')
    previous = 0
    for block in article['blocks']:
        start, end = block['start_char'], block['end_char']
        if type(start) is not int or type(end) is not int or not 0 <= previous <= start <= end <= len(text):
            raise ValueError('article_offsets_invalid')
        previous = end
        if block['kind'] == 'image':
            if type(block.get('image_index')) is not int or not 0 <= block['image_index'] < len(article['images']): raise ValueError('article_image_invalid')
        elif block.get('text') != text[start:end]: raise ValueError('article_text_mismatch')
