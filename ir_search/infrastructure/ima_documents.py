"""Read authorized IMA originals; temporary download credentials never leave memory."""
from __future__ import annotations

from dataclasses import replace
from io import BytesIO
import re
import zipfile
from xml.etree import ElementTree
from urllib.parse import urlsplit

from ir_search.documents.html import extract_html_document
from ir_search.documents.models import Document, hash_text, make_doc_id
from ir_search.documents.pdf import extract_pdf_document
from ir_search.infrastructure.credentials import ima_profile, SourceConfigError
from ir_search.infrastructure.ima import IMAClient, _parse_ref, _id
from ir_search.infrastructure.public_web import _request, _url, _allowed_domain, fetch_public_document
from ir_search.models import EvidenceType, SourceTier
from ir_search.registry import DataAdapterError


def _download(url, headers, *, context):
    _, host, _ = _url(url, signed_download=True)
    if not _allowed_domain(host, ('ima.qq.com','myqcloud.com')): raise DataAdapterError('blocked_url')
    reply = _request(url, context=context, signed_download=True, scoped_download_headers=headers)
    if reply.status != 200: raise DataAdapterError('blocked_url')
    return reply


def _office_text(raw, media_type, *, context, max_chars):
    """Bounded OOXML text only; no embedded files, macros, images or active content."""
    try:
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            members=archive.infolist()
            if len(members)>5000 or sum(i.file_size for i in members)>32*1024*1024:
                raise DataAdapterError('response_too_large')
            if len({i.filename for i in members})!=len(members): raise DataAdapterError('upstream_schema')
            names=([i.filename for i in members if i.filename=='word/document.xml'] if media_type==3 else
                sorted((i.filename for i in members if re.fullmatch(r'ppt/slides/slide[1-9][0-9]*\.xml',i.filename)),
                       key=lambda n:int(re.search(r'slide(\d+)',n).group(1))))
            if not names: raise DataAdapterError('web_content_unsupported')
            namespace='http://schemas.openxmlformats.org/'+('wordprocessingml/2006/main' if media_type==3 else 'drawingml/2006/main')
            paragraphs=[];size=0;truncated=False
            for name in names:
                context.check_active()
                if archive.getinfo(name).file_size>4*1024*1024: raise DataAdapterError('response_too_large')
                xml=archive.read(name)
                # OOXML normally uses UTF-8. Reject NUL/UTF-16 input rather than
                # letting alternate encodings conceal DTD/entity declarations.
                if b'\x00' in xml or b'<!DOCTYPE' in xml.upper() or b'<!ENTITY' in xml.upper():
                    raise DataAdapterError('web_content_unsupported')
                root=ElementTree.fromstring(xml)
                for p in root.iter('{'+namespace+'}p'):
                    text=''.join(node.text or '' for node in p.iter('{'+namespace+'}t'))
                    if text.strip(): paragraphs.append(text);size+=len(text)+2
                    if size>max_chars: truncated=True;break
                if truncated:break
            return '\n\n'.join(paragraphs)[:max_chars],truncated
    except DataAdapterError: raise
    except (zipfile.BadZipFile,ElementTree.ParseError,ValueError,KeyError,RuntimeError):
        raise DataAdapterError('web_content_unsupported') from None


def _note(client, identifier, reference, *, context, max_chars):
    reply=client.read('get_doc_content',{'note_id':_id(identifier),'target_content_format':0},context=context)
    text=reply.data.get('content')
    if not isinstance(text,str): raise DataAdapterError('upstream_schema')
    if not text.strip(): raise DataAdapterError('no_extracted_text')
    warnings=['published_date_unknown','publisher_unknown','note_authorship_not_original_source_verification',
              'title_unknown','source_uri_not_web_url']
    if len(text)>max_chars: warnings.append('text_truncated')
    text=text[:max_chars]
    return Document(make_doc_id(reference,hash_text(text)), reference, None, 'IMA 笔记', 'ima',
        SourceTier.UGC, EvidenceType.UNKNOWN, 'text/plain', None, reply.fetched_at, 'ima_note_plaintext',text,
        text_hash=hash_text(text),warnings=warnings,
        extra={'adapter_mode':'live','publisher':'unknown','text_provider':'ima','source_text_trust':'untrusted'})


def fetch_ima_document(reference: str, *, context, max_chars=20000, profile=None, client=None, downloader=None):
    """Resolve ima://media/ID or ima://note/ID under the current official account.

    Knowledge-base selections constrain discovery, not authorization. The provider
    enforces access to explicit IDs. No membership, authorship or publication is inferred.
    """
    kind, identifier=_parse_ref(reference)
    if type(max_chars) is not int or not 1<=max_chars<=100000: raise ValueError('Invalid text limit')
    context.check_active()
    try: profile=profile if profile is not None else ima_profile()
    except SourceConfigError: raise DataAdapterError('source_config_error') from None
    if profile is None: raise DataAdapterError('no_credential')
    if context.account_scope!='default': raise DataAdapterError('entitlement_denied')
    client=client if client is not None else IMAClient(profile)
    if kind=='note': return _note(client,identifier,reference,context=context,max_chars=max_chars)
    reply=client.read('get_media_info',{'media_id':identifier},context=context)
    data=reply.data; media_type=data.get('media_type')
    if type(media_type) is not int: raise DataAdapterError('upstream_schema')
    if media_type==11:
        ext=data.get('notebook_ext_info')
        if not isinstance(ext,dict): raise DataAdapterError('ima_original_unavailable')
        return _note(client,ext.get('notebook_id'),reference,context=context,max_chars=max_chars)
    if media_type not in {1,2,3,4,6,7,13,20}: raise DataAdapterError('web_content_unsupported')
    info=data.get('url_info')
    if not isinstance(info,dict) or not info.get('url'): raise DataAdapterError('ima_original_unavailable')
    url=info['url']; headers=info.get('headers') or {}
    if not isinstance(headers,dict): raise DataAdapterError('upstream_schema')
    original=None
    if media_type in {2,6}:
        # Public originals are fetched without forwarding IMA or temporary download auth.
        if headers: raise DataAdapterError('blocked_url')
        parsed, _, _=_url(url)
        if parsed.scheme!='https': raise DataAdapterError('blocked_url')
        if media_type==6:
            if parsed.hostname!='mp.weixin.qq.com': raise DataAdapterError('upstream_schema')
            from ir_search.infrastructure.wechat import fetch_wechat_document
            doc=fetch_wechat_document(url, context=context, max_chars=min(max_chars,50000))
        else: doc=fetch_public_document(url,context=context,max_chars=min(max_chars,50000))
        # Use the actual fetched location; HTML canonical tags are untrusted.
        original=doc.url
        _url(original)
    else:
        _,host,_=_url(url,signed_download=True)
        if not _allowed_domain(host,('ima.qq.com','myqcloud.com')): raise DataAdapterError('blocked_url')
        download=(downloader or _download)(url,headers,context=context)
        context.check_active()
        raw=download.body
        if not isinstance(raw,bytes) or len(raw)>8*1024*1024: raise DataAdapterError('response_too_large')
        if media_type==1:
            if not raw.startswith(b'%PDF-'): raise DataAdapterError('web_content_unsupported')
            doc=extract_pdf_document(raw,reference,max_chars=max_chars)
            if doc.errors:
                raise DataAdapterError('dependency_missing' if any('not installed' in e for e in doc.errors) else 'upstream_schema')
        elif media_type in {3,4}:
            text,truncated=_office_text(raw,media_type,context=context,max_chars=max_chars)
            doc=Document(make_doc_id(reference,hash_text(text)),reference,None,'IMA 文档','ima',
                SourceTier.UGC,EvidenceType.UNKNOWN,'application/zip',None,download.fetched_at,'ima_ooxml_text',text,
                warnings=['office_text_only_images_charts_notes_not_extracted']+(['text_truncated'] if truncated else []))
        elif media_type==20:
            doc=extract_html_document(raw,reference,max_chars=max_chars)
        else:
            try: text=raw.decode('utf-8-sig')
            except UnicodeError: raise DataAdapterError('web_content_unsupported') from None
            if '\x00' in text: raise DataAdapterError('web_content_unsupported')
            doc=Document(make_doc_id(reference,hash_text(text)),reference,None,'IMA 文档','ima',
                SourceTier.UGC,EvidenceType.UNKNOWN,'text/plain',None,download.fetched_at,'ima_utf8_text',text[:max_chars],
                warnings=['text_truncated'] if len(text)>max_chars else [])
    context.check_active()
    if doc.errors or not doc.text.strip(): raise DataAdapterError('no_extracted_text')
    warnings=[w for w in doc.warnings if isinstance(w,str) and w.replace('_','').isalnum()]
    if any('truncat' in str(w).lower() for w in doc.warnings): warnings.append('text_truncated')
    warnings+=['publisher_unknown','ima_original_publisher_unverified','source_uri_not_web_url']
    title=doc.title if original else 'IMA 文档'
    if not original: warnings+=['title_unknown','published_date_unknown']
    return replace(doc,doc_id=make_doc_id(reference,hash_text(doc.text)),url=reference,canonical_url=original,
        title=title,source='ima',source_tier=SourceTier.UGC,evidence_type=EvidenceType.UNKNOWN,
        published_at=doc.published_at if original else None,fetched_at=reply.fetched_at,
        text_hash=hash_text(doc.text),warnings=list(dict.fromkeys(warnings)),links=[],tables=[],
        extra={'adapter_mode':'live','publisher':'unknown','source_text_trust':'untrusted',
               'text_provider':doc.extra.get('text_provider','ima') if original else 'ima'})
