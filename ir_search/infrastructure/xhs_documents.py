"""XHS note/comment normalization with exact section attribution, no synthesis."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re

from .xhs import XhsClient, xhs_profile, _reference, _parse_reference
from .credentials import SourceConfigError
from ir_search.documents.models import Document, make_doc_id, hash_text
from ir_search.models import SourceTier, EvidenceType
from ir_search.registry import DataAdapterError

CST=timezone(timedelta(hours=8))


def _text(value, limit=None):
    if value is None: return ''
    if not isinstance(value,str): raise DataAdapterError('upstream_schema')
    return value.replace('\r\n','\n').replace('\x00','').strip()[:limit]


def _time(value):
    if value in (None,'',0): return None
    # The verified backend schema specifies milliseconds; do not guess seconds.
    if type(value) is not int or not 946684800000 <= value <= 4102444800000: return None
    return datetime.fromtimestamp(value/1000,CST)


def _author(user):
    if not isinstance(user,dict): return 'unknown'
    return _text(user.get('nickname') or user.get('nickName'),200) or 'unknown'


def _counts(row):
    info=row.get('interactInfo') or {}
    if not isinstance(info,dict): raise DataAdapterError('upstream_schema')
    result={}
    for source,target in [('likedCount','likes'),('collectedCount','collects'),('commentCount','comments'),('sharedCount','shares')]:
        raw=info.get(source)
        if raw is not None and not isinstance(raw,(str,int)): raise DataAdapterError('upstream_schema')
        raw=str(raw)[:64] if raw is not None else ''
        result[target]={'raw':raw or None,'exact':int(raw) if re.fullmatch(r'[0-9]{1,15}',raw) else None}
    return result


def _document(identifier, reply, *, max_chars, comment_limit):
    row=reply.data.get('note')
    if not isinstance(row,dict) or row.get('noteId')!=identifier: raise DataAdapterError('upstream_schema')
    reference=_reference(identifier); canonical='https://www.xiaohongshu.com/explore/'+identifier
    title=_text(row.get('title'),2000) or '小红书笔记 '+identifier
    author=_author(row.get('user')); published=_time(row.get('time'))
    desc=_text(row.get('desc')); text=''; sections=[]; comments=[]
    warnings=['xhs_ugc_not_independently_verified','business_period_unknown','xhs_images_and_video_not_transcribed',
              'xhs_comments_not_exhaustive','xhs_token_free_link_may_require_session']
    if published is None: warnings.append('published_date_unknown')
    if row.get('time') not in (None,'',0) and published is None: warnings.append('publication_date_invalid')
    if not row.get('title'): warnings.append('xhs_title_is_display_label')
    clipped=False
    def append(content, role, name, ref, instant):
        nonlocal text, clipped
        if not content: return
        separator='\n\n' if text else ''
        remaining=max_chars-len(text)-len(separator)
        if remaining<=0: clipped=True; return
        start=len(text)+len(separator); value=content[:remaining]
        clipped=clipped or len(content)>remaining
        text+=separator+value
        sections.append({'role':role,'author':name,'source_ref':ref,'published_at':instant.isoformat() if instant else None,
                         'start_char':start,'end_char':start+len(value)})
    append(desc,'post',author,reference,published)
    container=reply.data.get('comments') or {}
    if not isinstance(container,dict): raise DataAdapterError('upstream_schema')
    rows=container.get('list') or []
    if not isinstance(rows,list) or len(rows)>1000: raise DataAdapterError('upstream_schema')
    seen=set(); queue=[(r,None) for r in rows]; inspected=0
    while queue and len(comments)<comment_limit:
        row,parent=queue.pop(0); inspected+=1
        if inspected>1000: warnings.append('xhs_comment_scan_limit'); break
        if not isinstance(row,dict): raise DataAdapterError('upstream_schema')
        cid=row.get('id')
        if not isinstance(cid,str) or not re.fullmatch('[A-Za-z0-9_-]{1,128}',cid): raise DataAdapterError('upstream_schema')
        if row.get('noteId') not in (None,'',identifier): raise DataAdapterError('upstream_schema')
        if cid in seen: continue
        seen.add(cid)
        content=_text(row.get('content')); ref=reference+'#comment='+cid
        instant=_time(row.get('createTime')); name=_author(row.get('userInfo'))
        before=len(sections); append(content,'comment',name,ref,instant)
        if len(sections)>before:
            comments.append({'comment_id':cid,'parent_comment_id':parent,'source_ref':ref,
                'published_at':instant.isoformat() if instant else None,'author':name})
        children=row.get('subComments') or []
        if not isinstance(children,list) or len(children)>1000: raise DataAdapterError('upstream_schema')
        queue.extend((child,cid) for child in children)
        if len(text)>=max_chars: break
    if clipped: warnings.append('text_truncated')
    if not desc: warnings.append('xhs_note_body_empty')
    if not text: raise DataAdapterError('no_extracted_text')
    images=reply.data['note'].get('imageList') or []
    if not isinstance(images,list): raise DataAdapterError('upstream_schema')
    details={'content_state':'article_text','content_origin':'platform_note_and_comments','complete':False,
        'source_text_trust':'untrusted','backend':'xiaohongshu_mcp_http','cache_state':reply.cache_state,
        'snapshot_fetched_at':reply.fetched_at.isoformat(),'freshness':'cached_snapshot_not_revalidated' if reply.cache_state=='hit' else 'fetched_now',
        'note_id':identifier,'note_type':_text(reply.data['note'].get('type'),30),'comment_limit':comment_limit,
        'comments_returned':len(comments),'comments':comments,'comments_complete':False,
        'upstream_comments_has_more':container.get('hasMore') if type(container.get('hasMore')) is bool else None,
        'comment_pagination_available':False,'image_count':len(images),'images_read':False,
        'video_transcribed':False,'interaction_counts':_counts(reply.data['note']),
        'interaction_counts_basis':'snapshot_not_growth_or_sales','locally_truncated':clipped,
        'publication_basis':'platform_note_time_milliseconds','business_period_verified':False}
    digest=hash_text(text)
    return Document(doc_id=make_doc_id(reference,digest),url=reference,canonical_url=canonical,title=title,
        source='xhs',source_tier=SourceTier.UGC,evidence_type=EvidenceType.OPINION,content_type='text/plain',
        published_at=published,fetched_at=reply.fetched_at,extraction_method='xhs_note_text',text=text,
        text_hash=digest,warnings=warnings,
        extra={'adapter_mode':'live','generated':False,'publisher':author,'text_provider':'xiaohongshu_mcp',
               'material_read':details,'sections':sections})


def fetch_xhs_document(reference, *, context, max_chars=20000, comment_limit=0, cache_mode='use', profile=None, client=None):
    """Retrieve a prior search reference without exposing or requesting access tokens."""
    if type(max_chars) is not int or not 1<=max_chars<=100000 or cache_mode not in {'use','refresh'}: raise ValueError('Invalid XHS read options')
    if type(comment_limit) is not int or not 0<=comment_limit<=19: raise ValueError('Invalid XHS comment budget')
    identifier=_parse_reference(reference)
    if context.account_scope!='default': raise DataAdapterError('entitlement_denied')
    if client is None:
        try: profile=profile or xhs_profile()
        except SourceConfigError: raise DataAdapterError('source_config_error') from None
        if profile is None: raise DataAdapterError('source_disabled')
        client=XhsClient(profile)
    reply=client.detail(identifier,context=context,comment_limit=comment_limit,refresh=cache_mode=='refresh')
    context.check_active()
    return _document(identifier,reply,max_chars=max_chars,comment_limit=comment_limit)
