"""Explicit, versioned local exports; source text and citations remain immutable.

Inspired by podcast-summary/wechat-to-md's article/metadata artifact contract.
This implementation uses ir-search's bounded transport and provenance contracts.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from html import escape
import json
import os
import errno
import re
from pathlib import Path
from urllib.parse import urljoin

from ir_search.context import RequestContext, RequestStopped
from ir_search.contracts import Material
from ir_search.documents.wechat_html import _markdown, _validate_article
from ir_search.infrastructure.private_files import _directory_lock, _private_dir, _private_read, _private_write
from ir_search.infrastructure.public_web import _request, _url
from ir_search.registry import DataAdapterError


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8')


def _sha(data):
    return hashlib.sha256(data).hexdigest()


@dataclass
class _ImageBudget:
    remaining: int = 20*1024*1024
    calls: int = 0


def _image(url, context, budget):
    seen = set()
    for _ in range(4):
        _url(url)
        if url in seen: raise DataAdapterError('web_redirect_limit')
        seen.add(url)
        if budget.remaining <= 0: raise DataAdapterError('response_too_large')
        limit = min(8*1024*1024, budget.remaining)
        budget.calls += 1
        try:
            reply = _request(url, context=context, max_bytes=limit)
        except (DataAdapterError, RequestStopped):
            # A failed transport may have consumed an unknown partial response.
            budget.remaining -= limit
            raise
        budget.remaining -= len(reply.body)
        if len(reply.body) > limit: raise DataAdapterError('response_too_large')
        if reply.status in {301,302,303,307,308}:
            url = urljoin(url, reply.location)
            continue
        if reply.status != 200: raise DataAdapterError('network')
        raw = reply.body
        suffix = ('.png' if raw.startswith(b'\x89PNG\r\n\x1a\n') else '.jpg' if raw.startswith(b'\xff\xd8\xff') else
                  '.gif' if raw.startswith((b'GIF87a',b'GIF89a')) else '.webp' if raw[:4] == b'RIFF' and raw[8:12] == b'WEBP' else None)
        if not suffix or not reply.content_type.lower().startswith('image/'):
            raise DataAdapterError('web_content_unsupported')
        return raw, suffix
    raise DataAdapterError('web_redirect_limit')


def _checked_article(material):
    article = material.article
    if not article: return {}
    _validate_article(article, material.text)
    for item in article.get('images',[]) + article.get('links',[]): _url(item['url'])
    return article


def export_material(material: Material, output_dir, *, download_images=False, max_images=10, context=None):
    """Export one returned material; default is offline text/metadata only.

    Reuses identical, checksum-verified artifacts. Partial image exports resume;
    text changes create a new version and never overwrite another article/title.
    File or network failures return bounded diagnostic codes without losing text.
    """
    if not isinstance(material, Material): raise ValueError('Expected Material')
    if not isinstance(material.text, str) or len(material.text)>100000: raise ValueError('Invalid material text')
    if (not isinstance(output_dir, (str, Path)) or not str(output_dir).strip()
            or any(ord(c)<32 for c in str(output_dir))): raise ValueError('Expected archive directory')
    if type(download_images) is not bool or type(max_images) is not int or not 0 <= max_images <= 20:
        raise ValueError('Invalid image budget')
    context = context or RequestContext(timeout_seconds=60, max_operations=100)
    root = Path(output_dir).expanduser().absolute()
    result = {'status':'error', 'diagnostics':[], 'image_requests':0, 'downloaded_images':0}
    try:
        context.check_active()
        article = _checked_article(material)
        stored_summary = (material.text_origin == 'provider_summary'
                          and material.read_details.get('content_origin') == 'provider_stored_summary'
                          and material.read_details.get('text_scope') == 'abstract'
                          and material.provenance.evidence_type.value == 'opinion')
        if not material.text.strip() or (material.provenance.generated and not stored_summary):
            raise ValueError('not_source_text')
        snapshot = material.to_dict()
        snapshot['archive'] = {}
        source = material.original_url or material.url
        # Fingerprint includes citation selection and rich structure, but omits
        # volatile read diagnostics. Reuse reports the original archive's time.
        identity = {k:snapshot[k] for k in ('url','original_url','title','text','published_at','article','evidence_spans','text_provider','extraction_method')}
        identity['provenance'] = {k:v for k,v in snapshot['provenance'].items() if k != 'fetched_at'}
        identity['text_origin'] = material.text_origin
        identity['export_options'] = {'download_images':download_images,'max_images':max_images}
        version = _sha(_json(identity))
        with _directory_lock(root, context):
            parent = _private_dir(root/_sha(source.encode())[:20])
            directory = _private_dir(parent/version)
            result.update(directory=str(directory), version=version, text_hash=_sha(material.text.encode()))
            manifest_path = directory/'manifest.json'
            previous = None
            try: previous = json.loads(_private_read(manifest_path))
            except FileNotFoundError: pass
            if previous:
                if previous.get('version') != version: raise ValueError('archive_identity_mismatch')
                for name, digest in previous.get('files',{}).items():
                    # Manifest is local untrusted input; no paths outside this artifact.
                    p = Path(name)
                    if p.is_absolute() or '..' in p.parts or len(p.parts)>2: raise ValueError('archive_path_invalid')
                    if len(p.parts) == 2: _private_dir(directory/p.parent)
                    if _sha(_private_read(directory/p)) != digest: raise ValueError('archive_checksum_mismatch')
                for record in previous.get('images',[]):
                    if record.get('status') == 'downloaded' and (not re.fullmatch(r'images/[a-f0-9]{32}(?:[a-f0-9]{32})?\.(png|jpg|gif|webp)',record.get('path',''))
                            or previous.get('files',{}).get(record['path']) != record.get('sha256')):
                        raise ValueError('archive_path_invalid')
                if previous.get('complete'):
                    result.update(status='reused', downloaded_images=previous.get('downloaded_images',0),
                                  archived_fetched_at=previous.get('fetched_at'), manifest=str(manifest_path))
                    return result
            image_paths, images, diagnostics = {}, [], []
            old_images = {i['url']:i for i in (previous or {}).get('images',[])}
            budget = _ImageBudget()
            unique = list(dict.fromkeys(i['url'] for i in article.get('images',[])))
            for i, url in enumerate(unique):
                record = {'url':url, 'status':'not_requested', 'ocr_status':'not_requested'}
                if download_images:
                    if i >= max_images:
                        record['status'] = 'budget_exhausted'; diagnostics.append('archive_image_budget_exhausted')
                    elif old_images.get(url,{}).get('status') == 'downloaded':
                        record = old_images[url]
                        image_paths[url] = record['path']
                    else:
                        try:
                            raw, suffix = _image(url, context, budget)
                            image_dir = _private_dir(directory/'images')
                            name = _sha(raw)[:32] + suffix  # full sha256 stays in the manifest
                            _private_write(image_dir/name, raw)
                            image_paths[url] = 'images/'+name
                            record.update(status='downloaded', path='images/'+name, sha256=_sha(raw), bytes=len(raw))
                        except (DataAdapterError, RequestStopped) as exc:
                            record.update(status='failed', code=exc.code); diagnostics.append('archive_image_'+exc.code)
                            # Do not start further network work after cancellation/deadline.
                            if isinstance(exc, RequestStopped):
                                images.append(record)
                                diagnostics.append('archive_image_processing_stopped')
                                break
                images.append(record)
            result['image_requests'] = budget.calls
            result['image_bytes_charged_to_budget'] = 20*1024*1024 - budget.remaining
            downloaded = sum(i['status']=='downloaded' for i in images)
            # A safe reading view plus the unchanged evidence-bearing representation.
            body = _markdown(article, image_paths) if article else '<pre>'+escape(material.text)+'</pre>'
            line = lambda v: re.sub(r'([\\`*_\[\]()])', r'\\\1', escape(str(v or 'unknown'), quote=False).replace('\n',' '))
            notice = ('供应商已存摘要，可能经过 AI 辅助整理；不是原始文件或逐字记录，未经独立核验。'
                      if stored_summary else '来源文本，未经独立核验。')
            markdown = '# '+line(material.title)+'\n\n> '+notice+'\n\n'+body+'\n'
            readme = '\n'.join(['# '+line(material.title), '', '- 来源引用：'+line(source),
                '- 文本口径：'+notice,
                '- 发布时间：'+line(snapshot['published_at']), '- 抓取时间：'+line(snapshot['provenance']['fetched_at']),
                '- 文字提供方：'+line(material.text_provider or material.provenance.provider),
                '- 引用基准：material.json 中的 text，非 article.md。',
                '- 文本 SHA-256：'+_sha(material.text.encode()),
                '- 图片：'+str(downloaded)+'/'+str(len(unique))+' 已下载；未执行 OCR。',
                '- 警告：'+', '.join(material.warnings), ''])
            files = {'article.md':markdown.encode(), 'README.md':readme.encode(), 'material.json':_json(snapshot)}
            for name, raw in files.items(): _private_write(directory/name, raw)
            hashes = {name:_sha(raw) for name,raw in files.items()}
            for record in images:
                if record['status'] == 'downloaded': hashes[record['path']] = record['sha256']
            manifest = {'schema_version':'1.0', 'version':version, 'text_hash':result['text_hash'],
                        'fetched_at':snapshot['provenance']['fetched_at'], 'files':hashes, 'images':images,
                        'downloaded_images':downloaded, 'complete':not diagnostics, 'diagnostics':sorted(set(diagnostics)),
                        'source_text_trust':'untrusted'}
            _private_write(manifest_path, _json(manifest))
            result.update(status='partial' if diagnostics else 'ok', diagnostics=manifest['diagnostics'],
                          downloaded_images=downloaded, archived_fetched_at=manifest['fetched_at'], manifest=str(manifest_path))
    except RequestStopped as exc:
        result['diagnostics'].append(exc.code)
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError, DataAdapterError) as exc:
        # Classify the actual failure; a hypothetical future image path cannot explain ENOSPC.
        too_long = isinstance(exc, OSError) and (getattr(exc, 'winerror', None) == 206 or exc.errno == errno.ENAMETOOLONG)
        result['diagnostics'].append('archive_path_too_long' if too_long else 'archive_validation_or_io_failed')
    return result
