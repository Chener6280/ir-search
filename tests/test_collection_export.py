"""Offline contracts for the public archival export API; no network or real IDs."""
from datetime import datetime, timezone
from hashlib import sha256
import json

import pytest

from ir_search import (MaterialRegistry, RequestContext, export_collection_timeline,
                       list_material_collections, list_topic_comments, retrieve_asset)
from ir_search.adapters.ima_materials import IMAMaterialAdapter
from ir_search.adapters.wisburg_materials import WisburgMaterialAdapter
from ir_search.adapters.zsxq_materials import ZsxqMaterialAdapter
from ir_search.infrastructure.credentials import IMAProfile, WisburgProfile, ZsxqProfile
from ir_search.infrastructure.ima import IMAReply
from ir_search.infrastructure.public_web import _Reply
from ir_search.infrastructure.wisburg import WisburgResponse
from ir_search.infrastructure.zsxq import ZsxqResponse
from ir_search.registry import DataAdapterError

NOW = datetime(2026, 9, 15, tzinfo=timezone.utc)
ZSXQ_PROFILE = ZsxqProfile('synthetic_zsxq_key', ('100',))
IMA_PROFILE = IMAProfile('synthetic_ima_api_key', 'synthetic_ima_client_id', ('kb1',))
WISBURG_PROFILE = WisburgProfile('synthetic_wisburg_key')
PDF_BYTES = b'%PDF-1.4\narchival fixture\n%%EOF'
SECONDS = {'token': 'synthetic_zsxq_key', 'apikey': 'synthetic_ima_api_key',
           'client': 'synthetic_ima_client_id', 'wisburg': 'synthetic_wisburg_key'}


def topic(tid='101', gid='100', **kw):
    return dict({'topic_id': tid, 'group': {'group_id': gid, 'name': '测试投研星球'}, 'type': 'talk',
        'owner': {'name': '测试作者'}, 'title': '公司渠道跟踪', 'content': '贵州茅台动销与渠道库存的原始讨论。',
        'create_time': '2026-09-14T10:00:00.000+0800', 'files': []}, **kw)


class ZsxqClient:
    """State-free fake keyed by request arguments, like a real MCP session."""

    def __init__(self, pages=None, details=None, groups=None, comment_pages=None, group_rows_raw=None):
        self.pages = pages or {'2026-09-15T23:59:59.999+08:00':
            {'topics_brief': [topic()], 'has_more': False, 'next_end_time': ''}}
        self.details = details or {}
        self.group_rows = groups if groups is not None else ['100']
        self.group_rows_raw = group_rows_raw
        self.comment_pages = comment_pages if comment_pages is not None else {None: {'comments': [], 'has_more': False}}
        self.calls = []

    def groups(self, *, context):
        context.begin_operation()
        self.calls.append(('groups', {}))
        if self.group_rows_raw is not None:
            rows = self.group_rows_raw
        else:
            rows = [{'group_id': str(g), 'name': f'星球{g}', 'type': 'pay',
                     'owner': {'user_id': '5118', 'name': '星主'}, 'statistics': {'members': {'count': 10}},
                     'background_url': 'https://files.zsxq.com/bg'} for g in self.group_rows]
        return ZsxqResponse({'count': len(rows), 'groups': rows, 'success': True}, NOW)

    def read(self, tool, arguments, *, context):
        context.begin_operation()
        self.calls.append((tool, arguments))
        if tool == 'get_group_topics':
            response = self.pages[arguments['end_time']]
        elif tool == 'get_topic_info':
            response = self.details.get(arguments['topic_id'], {'topic': topic(arguments['topic_id'])})
        elif tool == 'get_topic_comments':
            response = self.comment_pages.get(arguments.get("index"), {'comments': [], 'has_more': False})
        elif tool == 'call_zsxq_api':
            response = self.details['download_url']
        else:
            raise AssertionError(tool)
        if isinstance(response, Exception):
            raise response
        return ZsxqResponse(response, NOW)


def zsxq_registry(client=None, profile=ZSXQ_PROFILE):
    registry = MaterialRegistry()
    registry.register(ZsxqMaterialAdapter(profile, client_factory=lambda _: client or ZsxqClient()))
    return registry


class IMAClient:
    def __init__(self, changes=None):
        self.calls = []
        self.changes = changes or {}

    def read(self, operation, p, *, context):
        context.begin_operation()
        self.calls.append((operation, p))
        data = {
            'search_knowledge_base': {'info_list': [{'kb_id': 'kb1', 'kb_name': '研究库'}], 'is_end': True},
            'search_knowledge': {'info_list': [{'media_id': 'm1', 'title': '茅台动销文档',
                'highlight_content': '<em>动销</em>摘要', 'media_type': 1}]},
            'search_note': {'search_note_infos': [{'note_book_info': {'note_id': 'n1', 'title': '茅台动销笔记',
                'summary': '茅台动销观察', 'create_time': '1789430400000', 'modify_time': 1789516800000}}],
                'is_end': True},
            'get_media_info': {'media_type': 1, 'url_info': {'url': 'https://res-pkb.ima.qq.com/a?signature=SIG',
                'headers': {'X-IMA-Sign': 'sign'}}},
        }[operation]
        selected = self.changes.get(operation, data)
        if isinstance(selected, Exception):
            raise selected
        if callable(selected):
            selected = selected(p)
        return IMAReply(selected, NOW)


def ima_registry(client=None, profile=IMA_PROFILE):
    registry = MaterialRegistry()
    registry.register(IMAMaterialAdapter(profile, client=client or IMAClient()))
    return registry


DATE_1 = '2026-09-14T08:30:00+08:00'
DATE_2 = '2026-09-13T08:30:00+08:00'
FOOTER = '\n\n--- Page Info ---\nNext cursor: {cursor}\nUse "after" parameter with this value to get the next page.'


def listing(rows, category='ib', next_cursor=None):
    label = {'article': 'articles', 'mikko': 'Mikko logs', 'feed': 'feed items'}.get(category, 'reports')
    parts = []
    for identifier, title, date_text, body in rows:
        parts.append(f'[{identifier}] {title}\n  date: {date_text}\n{body}')
    text = f'Found {len(rows)} {label}:\n\n' + '\n\n'.join(parts)
    if next_cursor:
        text += FOOTER.format(cursor=next_cursor)
    return text


class WisburgClient:
    def __init__(self, hook=None):
        self.calls = []
        self.hook = hook

    def read(self, tool, args, *, context):
        context.begin_operation()
        self.calls.append((tool, args))
        if self.hook:
            result = self.hook(tool, args)
            if isinstance(result, Exception):
                raise result
            if result is not None:
                return WisburgResponse(result, NOW)
        rows = [(101, '美联储观点', DATE_1, '美联储政策研究，仅为供应商整理内容。')]
        return WisburgResponse(listing(rows, next_cursor=None), NOW)


def wisburg_registry(client=None, profile=WISBURG_PROFILE):
    registry = MaterialRegistry()
    registry.register(WisburgMaterialAdapter(profile, client=client or WisburgClient()))
    return registry


def assert_secret_free(result):
    encoded = json.dumps({k: v for k, v in result.items() if k != 'content'}, ensure_ascii=False)
    for secret in SECONDS.values():
        assert secret not in encoded
    assert 'signature' not in encoded
    return encoded


# ---------------------------------------------------------------- collections


def test_list_collections_zsxq_directory_and_page_limit():
    client = ZsxqClient(groups=['100', '200'])
    result = list_material_collections('zsxq', registry=zsxq_registry(client))
    assert result['provider'] == 'zsxq' and result['exhaustive'] is True
    assert result['provider_full_export_supported'] is True
    assert [(c['collection_id'], c['name']) for c in result['collections']] == [('100', '星球100'), ('200', '星球200')]
    assert client.calls[0][0] == 'groups'
    assert_secret_free(result)
    many = ZsxqClient(groups=[str(i) for i in range(1, 201)])
    limited = list_material_collections('zsxq', registry=zsxq_registry(many))
    assert limited['collection_count'] == 200 and limited['exhaustive'] is False
    assert 'collection_directory_not_exhaustive' in {d['code'] for d in limited['diagnostics']}


def test_list_collections_zsxq_accepts_observed_field_name_variants():
    rows = [
        {'group_id': '100', 'name': '标准星球'},                                  # observed official shape
        {'group_id': 200, 'name': '整型ID星球'},                                  # numeric identifier value
        {'group': {'group_id': '300', 'name': '嵌套星球'}},                       # nested group envelope
        {'id': '400', 'name': '别名ID星球'},                                      # id alias fallback
        {'group': {'id': '500', 'name': '嵌套别名星球'}},                         # nested id alias
        {'name': '缺失ID星球'},                                                    # unrecoverable: skipped
        {'group_id': 'abc', 'name': '非法ID星球'},                                # invalid id: skipped
        'not-a-dict',                                                             # malformed row: skipped
    ]
    client = ZsxqClient(group_rows_raw=rows)
    result = list_material_collections('zsxq', registry=zsxq_registry(client))
    assert [(c['collection_id'], c['name']) for c in result['collections']] == [
        ('100', '标准星球'), ('200', '整型ID星球'), ('300', '嵌套星球'), ('400', '别名ID星球'),
        ('500', '嵌套别名星球')]
    assert result['collection_count'] == 5 and result['exhaustive'] is False
    assert 'invalid_collection_record' in {d['code'] for d in result['diagnostics']}
    assert_secret_free(result)
    empty = list_material_collections('zsxq', registry=zsxq_registry(ZsxqClient(group_rows_raw=[])))
    assert empty['collections'] == [] and empty['exhaustive'] is True


def test_list_collections_ima_paginates_and_stops_on_operation_budget():
    def directory(p):
        if not p['cursor']:
            return {'info_list': [{'kb_id': 'kb1', 'kb_name': '库1'}, {'id': 'kb2', 'name': '库2'}],
                    'is_end': False, 'next_cursor': 'c2'}
        return {'info_list': [{'kb_id': 'kb3', 'kb_name': '库3', 'status': 1}], 'is_end': True}
    client = IMAClient({'search_knowledge_base': directory})
    result = list_material_collections('ima', registry=ima_registry(client))
    assert result['exhaustive'] is True
    assert [c['collection_id'] for c in result['collections']] == ['kb1', 'kb2', 'kb3']
    assert result['collections'][2]['status'] == 1
    assert all(p['query'] == '' and p['limit'] == 20 for _, p in client.calls)
    assert 'ima_full_file_enumeration_unverified' in {d['code'] for d in result['diagnostics']}
    assert_secret_free(result)
    client = IMAClient({'search_knowledge_base': directory})
    result = list_material_collections('ima', registry=ima_registry(client),
                                       context=RequestContext(max_operations=1))
    assert result['exhaustive'] is False and result['collection_count'] == 2
    assert 'operation_budget_exhausted' in {d['code'] for d in result['diagnostics']}


def test_list_collections_wisburg_static_and_registration_errors():
    result = list_material_collections('wisburg', registry=MaterialRegistry())
    assert result['exhaustive'] is True and result['provider_full_export_supported'] is True
    assert len(result['collections']) == 9
    assert {c['category'] for c in result['collections']} == {'ib', 'company', 'am', 'archive', 'ec',
                                                              'feed', 'market_daily', 'article', 'mikko'}
    assert 'static_catalog_account_check_via_timeline_export' in {d['code'] for d in result['diagnostics']}
    with pytest.raises(ValueError):
        list_material_collections('web')
    with pytest.raises(ValueError):
        list_material_collections('zsxq', registry=MaterialRegistry())


# ------------------------------------------------------------------- timeline


def test_export_zsxq_timeline_metadata_pagination_and_text_opt_in():
    page1 = {'topics_brief': [topic('101', labels=['渠道'], like_count=7, comments_count=3,
            images=[{'image_id': '9001', 'url': 'https://files.zsxq.com/pic?signature=SECRET'}]),
            topic('102', create_time='2026-09-13T09:00:00.000+0800')],
            'has_more': True, 'next_end_time': '2026-09-13T10:00:00.000+0800'}
    page2 = {'topics_brief': [topic('103', create_time='2026-09-10T09:00:00.000+0800',
            files=[{'file_id': '501', 'name': '渠道研究.pdf', 'size': 4096}])], 'has_more': False,
            'next_end_time': ''}
    client = ZsxqClient(pages={'2026-09-15T23:59:59.999+08:00': page1,
                               '2026-09-13T10:00:00.000+0800': page2})
    result = export_collection_timeline('zsxq', '100', published_start='2026-09-01',
                                        published_end='2026-09-15', registry=zsxq_registry(client))
    assert result['status'] == 'ok' and result['coverage']['state'] == 'complete'
    assert [i['ref'] for i in result['items']] == ['zsxq://topic/100/101', 'zsxq://topic/100/102',
                                                   'zsxq://topic/100/103']
    first = result['items'][0]
    assert first['author'] == '测试作者' and first['type'] == 'talk'
    assert first['published_at'].endswith('+08:00')
    assert first['labels'] == ['渠道'] and first['like_count'] == 7 and first['comments_count'] == 3
    assert first['text'] is None and first['text_scope'] is None
    assert first['images'] == [{'index': 0, 'image_id': '9001'}]
    third = result['items'][2]
    assert third['attachments'] == [{'source_ref': 'zsxq://file/100/103/501', 'name': '渠道研究.pdf',
                                     'media_type': 'pdf', 'size_bytes': 4096, 'status': 'metadata_only'}]
    assert [c[1]['limit'] for c in client.calls if c[0] == 'get_group_topics'] == [30, 30]
    encoded = assert_secret_free(result)
    assert 'https://files.zsxq.com' not in encoded
    with_text = export_collection_timeline('zsxq', '100', published_start='2026-09-01',
        published_end='2026-09-15', include_text=True, registry=zsxq_registry(ZsxqClient(
            pages={'2026-09-15T23:59:59.999+08:00': page1, '2026-09-13T10:00:00.000+0800': page2})))
    assert with_text['items'][0]['text'].startswith('贵州茅台动销')
    assert with_text['items'][0]['text_scope'] == 'source_excerpt'


def test_export_zsxq_budget_truncation_resume_and_cursor_binding():
    page1 = {'topics_brief': [topic('101'), topic('102'), topic('103')], 'has_more': True,
             'next_end_time': '2026-09-13T10:00:00.000+0800'}
    page2 = {'topics_brief': [topic('104', create_time='2026-09-12T09:00:00.000+0800')],
             'has_more': False, 'next_end_time': ''}
    pages = {'2026-09-15T23:59:59.999+08:00': page1, '2026-09-13T10:00:00.000+0800': page2}
    client = ZsxqClient(pages=pages)
    first = export_collection_timeline('zsxq', '100', published_start='2026-09-01',
                                       published_end='2026-09-15', max_items=2, registry=zsxq_registry(client))
    assert first['coverage']['state'] == 'budget_exhausted'
    assert [i['ref'] for i in first['items']] == ['zsxq://topic/100/101', 'zsxq://topic/100/102']
    assert first['continuation_cursor']
    token = first['continuation_cursor']
    second = export_collection_timeline('zsxq', '100', published_start='2026-09-01',
                                        published_end='2026-09-15', max_items=5, cursor=token,
                                        registry=zsxq_registry(ZsxqClient(pages=pages)))
    assert second['coverage']['state'] == 'complete'
    assert [i['ref'] for i in second['items']] == ['zsxq://topic/100/103', 'zsxq://topic/100/104']
    assert second['continuation_cursor'] is None
    with pytest.raises(DataAdapterError, match='invalid_cursor'):
        export_collection_timeline('zsxq', '100', published_start='2026-09-02',
                                   published_end='2026-09-15', cursor=token,
                                   registry=zsxq_registry(ZsxqClient(pages=pages)))
    stale = dict(pages)
    stale['2026-09-15T23:59:59.999+08:00'] = {'topics_brief': [topic('199')] + page1['topics_brief'],
                                              'has_more': True, 'next_end_time': '2026-09-13T10:00:00.000+0800'}
    with pytest.raises(DataAdapterError, match='material_cursor_stale'):
        export_collection_timeline('zsxq', '100', published_start='2026-09-01',
                                   published_end='2026-09-15', cursor=token,
                                   registry=zsxq_registry(ZsxqClient(pages=stale)))
    client = ZsxqClient(pages=pages)
    result = export_collection_timeline('zsxq', '100', published_start='2026-09-01',
                                        published_end='2026-09-15', registry=zsxq_registry(client),
                                        context=RequestContext(max_operations=1))
    assert result['coverage']['state'] == 'budget_exhausted'
    assert len(result['items']) == 3 and result['continuation_cursor']


def test_export_zsxq_empty_result_and_window_validation():
    client = ZsxqClient(pages={'2026-09-15T23:59:59.999+08:00':
                               {'topics_brief': [], 'has_more': False, 'next_end_time': ''}})
    result = export_collection_timeline('zsxq', '100', published_start='2026-09-01',
                                        published_end='2026-09-15', registry=zsxq_registry(client))
    assert result['items'] == [] and result['coverage']['state'] == 'complete'
    assert result['window'] == {'start': '2026-09-01', 'end': '2026-09-15'}
    assert_secret_free(result)
    with pytest.raises(ValueError):
        export_collection_timeline('zsxq', '100', published_start='2026-01-01', published_end='2027-01-05',
                                   registry=zsxq_registry())
    with pytest.raises(ValueError):
        export_collection_timeline('web', '100', published_start='2026-09-01', published_end='2026-09-15')
    with pytest.raises(ValueError):
        export_collection_timeline('zsxq', 'not-a-group', published_start='2026-09-01',
                                   published_end='2026-09-15', registry=zsxq_registry())
    with pytest.raises(ValueError):
        export_collection_timeline('zsxq', '100', published_start='2026-09-01', published_end='2026-09-15',
                                   include_text=1, registry=zsxq_registry())


def test_export_ima_media_and_notes_metadata():
    def knowledge(p):
        if not p['cursor']:
            return {'info_list': [{'media_id': 'm1', 'title': '茅台动销文档',
                    'highlight_content': '<em>动销</em>摘要', 'media_type': 1},
                    {'media_id': 'm2', 'title': '渠道笔记', 'media_type': 11}], 'is_end': False,
                    'next_cursor': 'k2'}
        return {'info_list': [{'media_id': 'm3', 'title': '批价跟踪', 'media_type': 3}], 'is_end': True}
    client = IMAClient({'search_knowledge': knowledge})
    result = export_collection_timeline('ima', 'kb1', published_start='2026-09-01',
                                        published_end='2026-09-15', registry=ima_registry(client))
    assert result['coverage']['state'] == 'complete' and result['coverage']['notes'] == 1
    assert [i['ref'] for i in result['items']] == ['ima://media/m1', 'ima://media/m2', 'ima://media/m3',
                                                   'ima://note/n1']
    media, note = result['items'][0], result['items'][3]
    assert media['type'] == 'pdf' and media['collection_id'] == 'kb1'
    assert media['published_at'] is None and media['created_at'] is None
    assert note['type'] == 'note' and note['collection_id'] == 'notes'
    assert note['created_at'] == '2026-09-15T00:00:00+00:00'
    assert note['modified_at'] == '2026-09-16T00:00:00+00:00'
    assert note['published_at'] is None
    assert 'note_created_modified_not_publication_dates' in note['warnings']
    assert all(p['query'] == '' and p['knowledge_base_id'] == 'kb1' for _, p in client.calls
               if _ == 'search_knowledge')
    codes = {d['code'] for d in result['diagnostics']}
    assert 'ima_publication_dates_not_provided' in codes
    assert_secret_free(result)
    without_notes = export_collection_timeline('ima', 'kb1', published_start='2026-09-01',
        published_end='2026-09-15', include_notes=False, registry=ima_registry(IMAClient({'search_knowledge': knowledge})))
    assert without_notes['coverage']['notes'] == 0
    assert all(i['ref'].startswith('ima://media/') for i in without_notes['items'])


def test_export_ima_max_items_resume_and_notes_without_keyword():
    page1 = {'info_list': [{'media_id': 'm1', 'title': '文档一', 'media_type': 1},
                           {'media_id': 'm2', 'title': '文档二', 'media_type': 1},
                           {'media_id': 'm3', 'title': '文档三', 'media_type': 1}],
             'is_end': False, 'next_cursor': 'k2'}
    page2 = {'info_list': [{'media_id': 'm4', 'title': '文档四', 'media_type': 1}], 'is_end': True}
    knowledge = lambda p: page1 if not p['cursor'] else page2
    first = export_collection_timeline('ima', 'kb1', published_start='2026-09-01',
                                       published_end='2026-09-15', max_items=2, registry=ima_registry(IMAClient({'search_knowledge': knowledge})))
    assert first['coverage']['state'] == 'budget_exhausted'
    assert [i['ref'] for i in first['items']] == ['ima://media/m1', 'ima://media/m2']
    second = export_collection_timeline('ima', 'kb1', published_start='2026-09-01',
                                        published_end='2026-09-15', max_items=5, include_notes=False,
                                        cursor=first['continuation_cursor'],
                                        registry=ima_registry(IMAClient({'search_knowledge': knowledge})))
    assert second['coverage']['state'] == 'complete'
    assert [i['ref'] for i in second['items']] == ['ima://media/m3', 'ima://media/m4']
    blocked = IMAClient({'search_knowledge': {'info_list': [{'media_id': 'm1', 'title': '文档一',
                                                             'media_type': 1}], 'is_end': True},
                         'search_note': DataAdapterError('unsupported')})
    result = export_collection_timeline('ima', 'kb1', published_start='2026-09-01',
                                        published_end='2026-09-15', registry=ima_registry(blocked))
    assert result['coverage']['state'] == 'complete'
    assert [i['ref'] for i in result['items']] == ['ima://media/m1']
    assert 'ima_notes_keyword_required' in {d['code'] for d in result['diagnostics']}


def test_export_wisburg_no_keyword_window_filter_and_summary():
    def hook(tool, args):
        assert 'query' not in args
        assert args['startTime'] == '2026-09-01T00:00:00+08:00'
        assert args['endTime'] == '2026-09-16T00:00:00+08:00'
        return listing([(101, '美联储观点', DATE_1, '美联储政策研究，仅为供应商整理内容。'),
                        (102, '旧文', '2026-08-31T08:30:00+08:00', '窗口外')], category='ib')
    client = WisburgClient(hook)
    result = export_collection_timeline('wisburg', 'ib', published_start='2026-09-01',
                                        published_end='2026-09-15', registry=wisburg_registry(client))
    assert result['coverage']['state'] == 'complete' and len(result['items']) == 1
    item = result['items'][0]
    assert item['ref'] == 'wisburg://report/101' and item['type'] == 'report' and item['category'] == 'ib'
    assert item['published_at'] == DATE_1
    assert item['has_summary'] is True and item['summary'] is None
    assert item['original_document_available'] is False
    assert 'wisburg_outside_publication_window' in {d['code'] for d in result['diagnostics']}
    assert_secret_free(result)
    with_text = export_collection_timeline('wisburg', 'ib', published_start='2026-09-01',
        published_end='2026-09-15', include_text=True, registry=wisburg_registry(WisburgClient(hook)))
    assert with_text['items'][0]['summary'] == '美联储政策研究，仅为供应商整理内容。'
    article = export_collection_timeline('wisburg', 'article', published_start='2026-09-01',
        published_end='2026-09-15', registry=wisburg_registry(WisburgClient(
            lambda t, a: listing([(101, '智堡专栏', DATE_2, '专栏正文')], category='article'))))
    assert article['items'][0]['type'] == 'article'
    assert article['items'][0]['original_document_available'] is None
    assert article['items'][0]['text'] is None
    with pytest.raises(ValueError):
        export_collection_timeline('wisburg', 'chat', published_start='2026-09-01', published_end='2026-09-15',
                                   registry=wisburg_registry())


def test_export_wisburg_pagination_budget_and_resume():
    page1 = listing([(101, '报告一', DATE_1, ''), (102, '报告二', DATE_2, '')], next_cursor='2')
    page2 = listing([(103, '报告三', DATE_1, '')])
    def hook(tool, args):
        return page1 if not args.get('after') else page2
    client = WisburgClient(hook)
    first = export_collection_timeline('wisburg', 'ib', published_start='2026-09-01',
                                       published_end='2026-09-15', max_items=2, registry=wisburg_registry(client))
    assert first['coverage']['state'] == 'budget_exhausted'
    assert [i['ref'] for i in first['items']] == ['wisburg://report/101', 'wisburg://report/102']
    assert first['continuation_cursor']
    second = export_collection_timeline('wisburg', 'ib', published_start='2026-09-01',
                                        published_end='2026-09-15', max_items=5,
                                        cursor=first['continuation_cursor'],
                                        registry=wisburg_registry(WisburgClient(hook)))
    assert second['coverage']['state'] == 'complete'
    assert [i['ref'] for i in second['items']] == ['wisburg://report/103']
    client = WisburgClient(hook)
    result = export_collection_timeline('wisburg', 'ib', published_start='2026-09-01',
                                        published_end='2026-09-15', registry=wisburg_registry(client),
                                        context=RequestContext(max_operations=1))
    assert result['coverage']['state'] == 'budget_exhausted'
    assert result['continuation_cursor'] and len(result['items']) == 2


# -------------------------------------------------------------- retrieve_asset


def test_non_audio_policy_rechecks_real_asset_metadata_before_getting_bytes(monkeypatch):
    monkeypatch.setattr('ir_search.infrastructure.zsxq_documents._request', lambda *a, **k: pytest.fail('audio bytes requested'))
    file_topic = topic('103', files=[{'file_id': '501', 'name': 'recording.mp3', 'size': 10}])
    client = ZsxqClient(details={'103': {'topic': file_topic}})
    result = retrieve_asset('zsxq://file/100/103/501', media_policy='text_non_audio', registry=zsxq_registry(client))
    assert result['reason'] == 'media_excluded_by_policy' and result['content'] is None
    assert [call[0] for call in client.calls] == ['get_topic_info']
    client = IMAClient({'get_media_info': {'media_type': 12, 'file_name': 'recording.mp3'}})
    result = retrieve_asset('ima://media/m1', media_policy='text_non_audio', registry=ima_registry(client))
    assert result['reason'] == 'media_excluded_by_policy' and result['content'] is None
    with pytest.raises(ValueError): retrieve_asset('ima://media/m1', media_policy='invalid')


def test_retrieve_asset_zsxq_file_bytes_without_leaks(monkeypatch):
    file_topic = topic('103', files=[{'file_id': '501', 'name': '渠道研究.pdf', 'size': len(PDF_BYTES)}])
    client = ZsxqClient(details={'103': {'topic': file_topic},
                                 'download_url': {'status_code': 200, 'body': {'succeeded': True,
                                     'resp_data': {'download_url': 'https://files.zsxq.com/f/501?signature=SECRET'}}}})
    requested = []

    def fake_request(url, **kwargs):
        requested.append((url, kwargs))
        kwargs['context'].begin_operation()
        return _Reply(200, 'application/pdf', '', PDF_BYTES, NOW)

    monkeypatch.setattr('ir_search.infrastructure.zsxq_documents._request', fake_request)
    large_budget = 768 * 1024 * 1024
    result = retrieve_asset('zsxq://file/100/103/501', max_bytes=large_budget,
                            registry=zsxq_registry(client))
    assert result['status'] == 'ok'
    assert result['content'] == PDF_BYTES
    assert result['content_sha256'] == sha256(PDF_BYTES).hexdigest()
    assert result['filename'] == '渠道研究.pdf' and result['media_type'] == 'application/pdf'
    assert result['size_bytes'] == len(PDF_BYTES)
    assert result['warnings'] == ['pdf_magic_verified']
    assert [url for url, _ in requested] == ['https://files.zsxq.com/f/501?signature=SECRET']
    assert requested[0][1]['max_bytes'] == large_budget > 512 * 1024 * 1024
    assert_secret_free(result)


def test_retrieve_asset_zsxq_failure_paths(monkeypatch):
    monkeypatch.setattr('ir_search.infrastructure.zsxq_documents._request',
                        lambda *a, **kw: pytest.fail('network reached'))
    missing = retrieve_asset('zsxq://file/100/101/502', registry=zsxq_registry())
    assert missing['status'] == 'failed' and missing['reason'] == 'attachment_not_found_in_topic'
    topic_ref = retrieve_asset('zsxq://topic/100/101', registry=zsxq_registry())
    assert topic_ref['status'] == 'failed' and topic_ref['reason'] == 'not_a_file_ref'
    big = topic('103', files=[{'file_id': '501', 'name': 'big.pdf', 'size': 100}])
    client = ZsxqClient(details={'103': {'topic': big},
                                 'download_url': {'status_code': 200, 'body': {'succeeded': True,
                                     'resp_data': {'download_url': 'https://files.zsxq.com/f/501?x=1'}}}})
    result = retrieve_asset('zsxq://file/100/103/501', max_bytes=10, registry=zsxq_registry(client))
    assert result['status'] == 'failed' and result['reason'] == 'asset_exceeds_max_bytes'
    assert result['filename'] == 'big.pdf' and result['size_bytes'] == 100
    bad = ZsxqClient(details={'103': {'topic': big},
                              'download_url': {'status_code': 200, 'body': {'succeeded': True,
                                  'resp_data': {'download_url': 'https://files.zsxq.com/f/501?x=1'}}}})
    def bad_request(url, **kwargs):
        kwargs['context'].begin_operation()
        return _Reply(200, 'text/plain', '', b'not a pdf at all', NOW)
    monkeypatch.setattr('ir_search.infrastructure.zsxq_documents._request', bad_request)
    result = retrieve_asset('zsxq://file/100/103/501', registry=zsxq_registry(bad))
    assert result['status'] == 'ok' and result['warnings'] == ['pdf_magic_mismatch',
                                                               'declared_size_mismatch']
    denied = ZsxqClient(details={'103': DataAdapterError('entitlement_denied')})
    result = retrieve_asset('zsxq://file/100/103/501', registry=zsxq_registry(denied))
    assert result['status'] == 'failed' and result['reason'] == 'entitlement_denied'


def test_retrieve_asset_ima_media_bytes_and_limits(monkeypatch):
    calls = []

    def fake_request(url, **kwargs):
        calls.append((url, kwargs))
        kwargs['context'].begin_operation()
        return _Reply(200, 'application/pdf', '', PDF_BYTES, NOW)

    monkeypatch.setattr('ir_search.infrastructure.ima_documents._request', fake_request)
    large_budget = 768 * 1024 * 1024
    result = retrieve_asset('ima://media/m1', max_bytes=large_budget, registry=ima_registry())
    assert result['status'] == 'ok' and result['content'] == PDF_BYTES
    assert result['media_type'] == 'application/pdf'
    assert calls[0][0] == 'https://res-pkb.ima.qq.com/a?signature=SIG'
    assert calls[0][1]['scoped_download_headers'] == {'X-IMA-Sign': 'sign'}
    assert calls[0][1]['max_bytes'] == large_budget
    assert_secret_free(result)
    small = retrieve_asset('ima://media/m1', max_bytes=4, registry=ima_registry())
    assert small['status'] == 'failed' and small['reason'] == 'asset_exceeds_max_bytes'

    def bad_request(url, **kwargs):
        kwargs['context'].begin_operation()
        return _Reply(200, 'text/plain', '', b'plain text', NOW)
    monkeypatch.setattr('ir_search.infrastructure.ima_documents._request', bad_request)
    result = retrieve_asset('ima://media/m1', registry=ima_registry())
    assert result['status'] == 'failed' and result['reason'] == 'web_content_unsupported'
    note = retrieve_asset('ima://note/n1', registry=ima_registry())
    assert note['status'] == 'failed' and note['reason'] == 'notes_do_not_provide_file_bytes'
    media_note = IMAClient({'get_media_info': {'media_type': 11, 'notebook_ext_info': {'notebook_id': 'n1'}}})
    result = retrieve_asset('ima://media/m2', registry=ima_registry(media_note))
    assert result['status'] == 'failed' and result['reason'] == 'notes_do_not_provide_file_bytes'
    webpage = IMAClient({'get_media_info': {'media_type': 2, 'url_info': {'url': 'https://example.org/a'}}})
    result = retrieve_asset('ima://media/m3', registry=ima_registry(webpage))
    assert result['status'] == 'failed' and result['reason'] == 'public_web_original_not_exported_as_bytes'


def test_retrieve_asset_unsupported_schemes_and_validation():
    result = retrieve_asset('wisburg://report/101', registry=wisburg_registry())
    assert result == {'status': 'unsupported', 'reason': 'upstream_does_not_provide_original_files',
                      'ref': 'wisburg://report/101', 'filename': None, 'media_type': None,
                      'size_bytes': None, 'content_sha256': None, 'content': None, 'warnings': []}
    failed = retrieve_asset('https://example.org/a.pdf', registry=MaterialRegistry())
    assert failed['status'] == 'failed' and failed['reason'] == 'unsupported_asset_ref'
    with pytest.raises(ValueError):
        retrieve_asset('ima://media/m1', max_bytes=0, registry=ima_registry())
    with pytest.raises(ValueError):
        retrieve_asset('')


# ------------------------------------------------------------------- comments


def test_list_topic_comments_pages_nested_and_metadata():
    pages = {None: {'comments': [{'comment_id': '1001', 'text': '动销口径待核实。', 'like_count': 2,
                            'owner': {'name': '评论人', 'role': 'guest'},
                            'create_time': '2026-09-15T10:00:00+0800',
                            'replies': [{'comment_id': '1002', 'text': '同意，补充样本。',
                                         'owner': {'name': '回复人'}, 'create_time': '2026-09-15T11:00:00+0800'}]}],
              'has_more': True, 'index': 'ix2'},
             'ix2': {'comments': [{'comment_id': '1003', 'text': '渠道库存呢？', 'owner': {'name': '提问人'},
                            'create_time': '2026-09-15T12:00:00+0800', 'reply_to': {'comment_id': '1001',
                            'owner': {'name': '评论人'}}}], 'has_more': False}}
    client = ZsxqClient(comment_pages=pages)
    result = list_topic_comments('zsxq', 'zsxq://topic/100/101', registry=zsxq_registry(client))
    assert result['status'] == 'ok' and result['coverage'] == {'state': 'complete', 'scanned': 2, 'returned': 2}
    assert len(result['comments']) == 2
    first, third = result['comments'][0], result['comments'][1]
    assert first['ref'] == 'zsxq://comment/100/101/1001' and first['author'] == '评论人'
    assert first['role'] == 'guest' and first['like_count'] == 2
    assert first['created_at'] == '2026-09-15T10:00:00+08:00'
    assert first['replies'][0]['ref'] == 'zsxq://comment/100/101/1002'
    assert first['replies'][0]['text'] == '同意，补充样本。'
    assert third['reply_to'] == {'comment_id': '1001', 'author': '评论人'}
    codes = {d['code'] for d in result['diagnostics']}
    assert 'nested_replies_not_provided' not in codes
    assert client.calls[1][1].get('index') == 'ix2'
    assert all(c[1]['limit'] == 30 for c in client.calls if c[0] == 'get_topic_comments')
    assert_secret_free(result)


def test_list_topic_comments_budget_resume_empty_and_unsupported():
    pages = {None: {'comments': [{'comment_id': '1001', 'text': '第一条', 'owner': {'name': '甲'},
                            'create_time': '2026-09-15T10:00:00+0800'}], 'has_more': True, 'index': 'ix2'},
             'ix2': {'comments': [{'comment_id': '1002', 'text': '第二条', 'owner': {'name': '乙'},
                            'create_time': '2026-09-15T11:00:00+0800'}], 'has_more': False}}
    client = ZsxqClient(comment_pages=pages)
    first = list_topic_comments('zsxq', 'zsxq://topic/100/101', max_pages=1, registry=zsxq_registry(client))
    assert first['coverage']['state'] == 'budget_exhausted'
    assert [c['comment_id'] for c in first['comments']] == ['1001']
    assert first['continuation_cursor']
    second = list_topic_comments('zsxq', 'zsxq://topic/100/101', cursor=first['continuation_cursor'],
                                 registry=zsxq_registry(ZsxqClient(comment_pages=pages)))
    assert second['coverage']['state'] == 'complete'
    assert [c['comment_id'] for c in second['comments']] == ['1002']
    assert second['continuation_cursor'] is None
    empty = list_topic_comments('zsxq', 'zsxq://topic/100/101',
                                registry=zsxq_registry(ZsxqClient(comment_pages={None: {'comments': [],
                                                                                       'has_more': False}})))
    assert empty['comments'] == [] and empty['coverage']['state'] == 'complete'
    assert 'nested_replies_not_provided' in {d['code'] for d in empty['diagnostics']}
    for provider in ('ima', 'wisburg'):
        result = list_topic_comments(provider, 'ima://media/m1', registry=MaterialRegistry())
        assert result['status'] == 'not_supported'
        assert result['coverage']['state'] == 'not_supported'
        assert result['comments'] == []
    with pytest.raises(ValueError):
        list_topic_comments('web', 'zsxq://topic/100/101')
    with pytest.raises(ValueError):
        list_topic_comments('zsxq', 'ima://media/m1', registry=zsxq_registry())
    with pytest.raises(ValueError):
        list_topic_comments('zsxq', 'zsxq://topic/100/101', per_page=31, registry=zsxq_registry())
    client = ZsxqClient(comment_pages=pages)
    result = list_topic_comments('zsxq', 'zsxq://topic/100/101', registry=zsxq_registry(client),
                                 context=RequestContext(max_operations=1))
    assert result['coverage']['state'] == 'budget_exhausted'
    assert result['continuation_cursor']
