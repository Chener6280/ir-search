"""Synthetic fixtures only; real platform content and credentials remain local."""
from dataclasses import replace
from datetime import datetime, timezone
import json
import socket
import sys
from types import SimpleNamespace

import pytest

from ir_search import MaterialRequest, MaterialSearchRequest, MaterialRegistry, RequestContext, search_materials, retrieve, build_material_registry
from ir_search.adapters.platform_materials import XueqiuMaterialAdapter, EastmoneyMaterialAdapter, VideoMaterialAdapter, platform_material_profile
from ir_search.infrastructure import community_documents as community, video_documents as video, platform_documents as platform
from ir_search.infrastructure.credentials import WebMaterialProfile, SourceConfigError, source_configuration_status
from ir_search.infrastructure.public_web import _Reply
from ir_search.infrastructure.web_search import WebSearchPage
from ir_search.registry import DataAdapterError
from ir_search.context import RequestStopped
from ir_search.mcp_server import search_materials_payload, retrieve_payload

NOW = datetime(2026, 9, 17, tzinfo=timezone.utc)
XQ = 'https://xueqiu.com/123/456'
EM = 'https://guba.eastmoney.com/news,600519,123456.html'
BV = 'https://www.bilibili.com/video/BV17x411w7KC'
YT = 'https://www.youtube.com/watch?v=jNQXAC9IVRw'
PROFILE = WebMaterialProfile(allow_anonymous=True)


def req(**changes):
    return MaterialSearchRequest(**{**dict(question='茅台动销', keywords=['动销'], published_start='2026-09-01',
        published_end='2026-09-17', providers=['eastmoney'], candidates_per_source=4, text_reads_per_source=2), **changes})


def reply(body, status=200, location=''):
    return _Reply(status, 'text/html', location, body if isinstance(body, bytes) else body.encode(), NOW)


def doc(url, *, text='茅台动销 2026 年 9 月 来源：原始发布者', details=None, **kw):
    return platform._document(url, platform._platform_url(url)[0], '茅台动销', text, NOW, details=details, **kw)


class Client:
    def __init__(self, urls, failure=None): self.urls, self.calls, self.failure = urls, [], failure
    def search(self, query, *, limit, context):
        self.calls.append((query, limit)); context.begin_operation()
        if self.failure: raise DataAdapterError(self.failure)
        return WebSearchPage([{'url': u, 'title': '茅台动销', 'snippet': '茅台动销搜索摘要'} for u in self.urls], NOW)


def registry(cls, urls, reader=None, client=None, profile=PROFILE):
    cn, overseas, reads = Client(urls), client or Client([]), []
    def read(url, **kw):
        reads.append(url)
        return reader(url, **kw) if reader else doc(url, published=NOW)
    reg = MaterialRegistry(); reg.register(cls(profile, bocha_client=cn, client=overseas, reader=read))
    return reg, cn, overseas, reads


@pytest.mark.parametrize('url,name,canonical', [
    (XQ + '?utm_source=x', 'xueqiu', XQ), (EM, 'eastmoney', EM),
    (BV + '/?p=2&spm_id_from=x', 'bilibili', BV + '?p=2'),
    ('https://m.bilibili.com/video/av123/?p=2', 'bilibili', 'https://www.bilibili.com/video/av123?p=2'),
    ('https://youtu.be/jNQXAC9IVRw?t=5', 'youtube', YT),
    ('https://www.youtube.com/shorts/jNQXAC9IVRw', 'youtube', YT)])
def test_platform_canonicalization(url, name, canonical):
    result = platform._platform_url(url)
    assert result[:2] == (name, canonical)


@pytest.mark.parametrize('url', [XQ + '?token=secret', 'https://xueqiu.com.attacker.test/123/456',
    'https://xueqiu.com/123', 'https://guba.eastmoney.com/list,600519.html',
    'http://127.0.0.1/watch?v=jNQXAC9IVRw', BV + '?p=0', BV + '?p=2&p=3',
    'https://www.youtube.com/watch?v=bad', 'https://www.youtube.com/playlist?list=PL123',
    'https://user:pass@xueqiu.com/123/456'])
def test_not_detail_or_unsafe_urls_rejected(url):
    with pytest.raises(DataAdapterError): platform._platform_url(url)


@pytest.mark.parametrize('changes', [{'video_platforms': []}, {'video_platforms': ['xiaoyuzhou']},
    {'video_languages': []}, {'video_languages': ['zh&token=x']}, {'video_languages': 'en'}, {'video_languages': [True]}])
def test_invalid_video_contracts(changes):
    with pytest.raises(ValueError): req(**changes)
    if 'video_languages' in changes:
        with pytest.raises(ValueError): MaterialRequest('q', [YT], **changes)


def test_profile_registry_status_and_disabled_without_network(tmp_path, monkeypatch):
    values = {'BOCHA_API_KEY': 'synthetic-test-key', 'XUEQIU_MATERIALS_ENABLED': 'true',
              'EASTMONEY_MATERIALS_ENABLED': 'true', 'VIDEO_MATERIALS_ENABLED': 'true'}
    assert platform_material_profile('xueqiu', values={}) is None
    assert platform_material_profile('xueqiu', values=values).bocha_api_key == 'synthetic-test-key'
    with pytest.raises(SourceConfigError): platform_material_profile('xueqiu', values={**values, 'XUEQIU_MATERIALS_ENABLED': 'yes'})
    with pytest.raises(ValueError): platform_material_profile('invalid', values={})
    with pytest.raises(ValueError): VideoMaterialAdapter(None)
    path = tmp_path / 'sources.env'; path.write_text('\n'.join(k+'='+v for k,v in values.items())); path.chmod(0o600)
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: pytest.fail('network'))
    reg = build_material_registry(env_file=path)
    assert {a.name for a in reg.entries()} == {'xueqiu', 'eastmoney', 'video'}
    status = source_configuration_status(env_file=path)
    assert all(not s.get('live_verified') for s in status['sources'])
    assert 'synthetic-test-key' not in json.dumps(status) and 'synthetic-test-key' not in repr(platform_material_profile('video', values=values))


@pytest.mark.parametrize('cls,url', [(XueqiuMaterialAdapter, XQ), (EastmoneyMaterialAdapter, EM)])
def test_community_search_dates_attribution_and_citations(cls, url):
    reg, cn, overseas, reads = registry(cls, [url, url])
    result = search_materials(req(providers=[cls.name]), registry=reg)
    assert len(result.items) == 1 and reads == [url] and not overseas.calls
    v = result.items[0]['versions'][0]
    assert v['material_type'] == 'social_post' and v['text_scope'] == 'extracted_text'
    assert v['provenance']['authority'] == 'ugc' and v['provenance']['evidence_type'] == 'opinion'
    assert '来源：' in v['text'] and v['published_on'] == '2026-09-17'
    for span in v['evidence_spans']:
        assert span['text'] == v[span['source_part']][span['start_char']:span['end_char']]


def test_detail_url_filter_read_budget_and_challenge_preserves_snippet():
    def read(*a, **kw): raise DataAdapterError('web_content_challenge')
    reg, cn, _, reads = registry(XueqiuMaterialAdapter, ['https://xueqiu.com/123', XQ, 'https://xueqiu.com/123/789'], read)
    result = search_materials(req(providers=['xueqiu'], text_reads_per_source=1), registry=reg)
    assert reads == [XQ] and len(result.items) == 2
    versions = [g['versions'][0] for g in result.items]
    assert all(v['text_scope'] == 'search_snippet' for v in versions)
    assert 'web_content_challenge' in {d.code for d in result.diagnostics}
    assert 'text_read_budget_exhausted' in {d.code for d in result.diagnostics}


def test_source_dates_override_search_and_outside_dates_rejected():
    reg, *_ = registry(EastmoneyMaterialAdapter, [EM], lambda url, **kw: doc(url, published=NOW.replace(year=2024)))
    result = search_materials(req(), registry=reg)
    assert not result.items and 'platform_outside_publication_window' in {d.code for d in result.diagnostics}


def test_platform_challenge_stops_further_body_reads_but_preserves_discovered_snippets():
    def blocked(*a, **kw): raise DataAdapterError('web_content_challenge')
    reg, _, _, reads = registry(XueqiuMaterialAdapter, [XQ, 'https://xueqiu.com/123/789'], blocked)
    result = search_materials(req(providers=['xueqiu'], text_reads_per_source=2), registry=reg)
    assert reads == [XQ] and len(result.items) == 2
    versions = [item['versions'][0] for item in result.items]
    assert all(v['text_scope'] == 'search_snippet' for v in versions)
    deferred = next(v for v in versions if v['source_ref'].endswith('/789'))
    assert deferred['read_details']['prior_read_failure'] == 'web_content_challenge'
    assert 'text_not_read_after_source_failure' in deferred['warnings']


def test_video_region_routing_budget_dry_run_and_failed_platform_independent(monkeypatch):
    overseas = Client([YT])
    reg, cn, _, reads = registry(VideoMaterialAdapter, [BV], client=overseas)
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: pytest.fail('network'))
    context = RequestContext()
    preview = search_materials(req(providers=['video'], dry_run=True, candidates_per_source=5), registry=reg, context=context)
    assert context.operations == 0 and not cn.calls and not overseas.calls and not reads
    plans = preview.plan['source_plans'][0]['queries']
    assert [(p['discovery_provider'], p['max_candidates']) for p in plans] == [('bocha', 3), ('anysearch', 2)]
    cn.failure = 'quota'
    result = search_materials(req(providers=['video'], candidates_per_source=5, web_region='cn'), registry=reg)
    assert len(result.items) == 1 and reads == [YT]
    assert 'quota' in {d.code for d in result.diagnostics}
    assert cn.calls[0] == (plans[0]['query'], 3) and overseas.calls[0] == (plans[1]['query'], 2)


def test_no_scope_broadening_or_wrong_material_kind():
    reg, cn, _, reads = registry(EastmoneyMaterialAdapter, [EM], profile=replace(PROFILE, allowed_domains=('sec.gov',)))
    assert not search_materials(req(), registry=reg).items and not cn.calls
    reg, cn, _, reads = registry(VideoMaterialAdapter, [BV])
    assert not search_materials(req(providers=['video'], material_types=['policy']), registry=reg).items and not cn.calls


def test_cancel_and_operation_budgets():
    reg, cn, *_ = registry(VideoMaterialAdapter, [BV])
    context = RequestContext(); context.cancel()
    result = search_materials(req(providers=['video']), registry=reg, context=context)
    assert not cn.calls and 'cancelled' in {d.code for d in result.diagnostics}


def test_guba_primary_json_keeps_sources_and_excludes_comments(monkeypatch):
    data = {'post_id': 123456, 'post_title': '茅台动销', 'post_content': '<p>来源：公司原文</p><p>正文<script>bad</script>信息</p>',
            'post_user': {'user_nickname': '社区作者'}, 'post_publish_time': '2026-09-17 08:30:00'}
    html = '<div>其他评论</div><script>var post_article=' + json.dumps(data, ensure_ascii=False) + ';</script>'
    monkeypatch.setattr(community, '_read', lambda *a, **kw: reply(html))
    doc = community.read_community_document(EM, context=RequestContext())
    assert doc.text == '来源：公司原文\n正文信息'
    assert doc.published_at.isoformat() == '2026-09-17T08:30:00+08:00'
    assert doc.extra['publisher'] == '社区作者'
    result = retrieve(MaterialRequest('正文', [EM]))
    assert result.materials[0].provenance.authority.value == 'ugc'
    assert result.materials[0].text == doc.text


def test_xueqiu_selector_and_status_extraction(monkeypatch):
    html = '<p>导航</p><h1 class="article__bd__title">茅台</h1><div class="article__bd__detail"><p>动销</p><p>来源：作者</p></div><p>评论</p>'
    monkeypatch.setattr(community, '_read', lambda *a, **kw: reply(html))
    doc = community.read_community_document(XQ, context=RequestContext())
    assert doc.text == '动销\n来源：作者' and doc.title == '茅台' and doc.published_at is None
    data = {'id': 456, 'title': '茅台', 'text': '<p>动销正文</p>', 'created_at': 1789603200000, 'user': {'screen_name': '作者'}}
    monkeypatch.setattr(community, '_read', lambda *a, **kw: reply('<script>SNB.data.status=' + json.dumps(data) + ';</script>'))
    assert community.read_community_document(XQ, context=RequestContext()).text == '动销正文'


@pytest.mark.parametrize('html,code', [('<textarea>_waf_abc</textarea><p>伪正文</p>', 'web_content_challenge'),
    ('<p>navigation only</p>', 'no_extracted_text')])
def test_challenge_or_missing_body_not_evidence(monkeypatch, html, code):
    monkeypatch.setattr(community, '_read', lambda *a, **kw: reply(html))
    with pytest.raises(DataAdapterError, match=code): community.read_community_document(XQ, context=RequestContext())
    result = retrieve(MaterialRequest('q', [XQ]))
    assert not result.materials and code in {d.code for d in result.diagnostics}


def test_guba_id_mismatch_rejected(monkeypatch):
    monkeypatch.setattr(community, '_read', lambda *a, **kw: reply('var post_article={"post_id":999,"post_content":"wrong"};'))
    with pytest.raises(DataAdapterError, match='upstream_schema'): community.read_community_document(EM, context=RequestContext())


def test_platform_cookie_scoped_redirect_and_no_secret_diagnostics(monkeypatch):
    calls = []
    def fetch(url, **kw): calls.append((url, kw)); return reply('', 302, 'https://evil.test/a')
    monkeypatch.setattr(platform, '_request', fetch)
    with pytest.raises(DataAdapterError, match='blocked_url'):
        platform._read(XQ, context=RequestContext(), hosts={'xueqiu.com'}, cookie='private-session')
    assert len(calls) == 1 and calls[0][1]['headers'] == {'Cookie': 'private-session'}
    monkeypatch.setattr(platform, '_request', lambda *a, **kw: reply('<p>private-session</p>'))
    with pytest.raises(DataAdapterError, match='upstream_schema'):
        platform._read(XQ, context=RequestContext(), hosts={'xueqiu.com'}, cookie='private-session')


def test_china_epoch_publication_day_is_not_utc_day():
    instant = datetime(2026, 9, 16, 18, tzinfo=timezone.utc)
    assert platform._epoch(instant.timestamp()).date().isoformat() == '2026-09-17'


def test_bilibili_captions_and_timestamp_citations(monkeypatch):
    calls = []
    def read(url, **kw):
        calls.append((url, kw))
        if '/view?' in url: data = {'code': 0, 'data': {'bvid': 'BV17x411w7KC', 'title': '茅台动销', 'desc': '这只是简介',
            'owner': {'name': '作者'}, 'pubdate': 1789603200, 'cid': 1, 'pages': [{'page': 1, 'cid': 1, 'duration': 90}]}}
        elif '/player/' in url: data = {'code': 0, 'data': {'subtitle': {'subtitles': [
            {'lan': 'ai-zh', 'subtitle_url': 'https://aisubtitle.hdslb.com/a?auth_key=public-signature', 'ai_type': 1}]}}}
        else: data = {'body': [{'from': 12, 'to': 17, 'content': '茅台动销渠道库存。'}, {'from': 20, 'to': 24, 'content': '本期茅台终端动销信息。'}]}
        return data, reply('')
    monkeypatch.setattr(video, '_json_read', read)
    doc = video.read_video_document(BV, context=RequestContext(), languages=['zh'])
    details = doc.extra['web_read']
    assert details['caption_kind'] == 'automatic' and details['description'] == '这只是简介'
    assert '简介' not in doc.text and details['transcript_segments'][0]['start_ms'] == 12000
    assert calls[-1][1]['signed'] and 'cookie' not in calls[-1][1]
    result = retrieve(MaterialRequest('茅台动销', [BV], video_languages=['zh']))
    material = result.materials[0]
    assert material.text_origin == 'extracted_text' and material.provenance.authority.value == 'ugc'
    assert any(s['extra'].get('timestamp_url', '').endswith('t=12') for s in material.evidence_spans)
    reg, *_ = registry(VideoMaterialAdapter, [BV], lambda *a, **kw: doc)
    found = search_materials(req(providers=['video'], video_platforms=['bilibili']), registry=reg)
    spans = found.items[0]['versions'][0]['evidence_spans']
    assert any(s.get('start_ms') == 12000 for s in spans if s['source_part'] == 'text')
    assert all('start_ms' not in s for s in spans if s['source_part'] == 'title')


def test_video_metadata_only_is_never_transcript(monkeypatch):
    monkeypatch.setattr(video, '_json_read', lambda *a, **kw: ({'title': '茅台动销', 'author_name': '作者'}, reply('')))
    def missing(*a, **kw): raise DataAdapterError('video_captions_unavailable')
    monkeypatch.setattr(video, '_youtube_captions', missing)
    result = retrieve(MaterialRequest('茅台动销', [YT]))
    material = result.materials[0]
    assert material.text == '' and not material.evidence_spans and material.text_origin == 'metadata_only'
    assert 'video_captions_unavailable' in {d.code for d in result.diagnostics}
    reg, *_ = registry(VideoMaterialAdapter, [], video.read_video_document, client=Client([YT]))
    found = search_materials(req(providers=['video'], video_platforms=['youtube']), registry=reg)
    v = found.items[0]['versions'][0]
    assert v['text_scope'] == 'search_snippet'
    assert all('start_ms' not in s for s in v['evidence_spans'])


def test_youtube_dependency_protocol_and_safe_exception_mapping(monkeypatch):
    class Api:
        def __init__(self, http_client): assert isinstance(http_client, video._YouTubeSession)
        def fetch(self, identifier, languages):
            return SimpleNamespace(to_raw_data=lambda: [{'start': 1, 'duration': 2, 'text': '动销'}], language_code='zh', is_generated=True)
    monkeypatch.setitem(sys.modules, 'youtube_transcript_api', SimpleNamespace(YouTubeTranscriptApi=Api))
    rows, language, kind = video._youtube_captions('jNQXAC9IVRw', ('zh',), RequestContext())
    assert rows[0]['text'] == '动销' and language == 'zh' and kind == 'automatic'
    def fail(*a, **kw): raise type('RequestBlocked', (Exception,), {})('secret upstream details')
    Api.fetch = fail
    with pytest.raises(DataAdapterError, match='web_content_challenge') as error:
        video._youtube_captions('jNQXAC9IVRw', ('en',), RequestContext())
    assert 'secret' not in str(error.value)
    monkeypatch.setitem(sys.modules, 'youtube_transcript_api', None)
    with pytest.raises(DataAdapterError, match='video_dependency_missing'):
        video._youtube_captions('jNQXAC9IVRw', ('en',), RequestContext())


@pytest.mark.parametrize('url', ['https://evil.test/api/timedtext?v=jNQXAC9IVRw',
    'https://www.youtube.com/api/timedtext?v=OtherVid123', 'http://www.youtube.com/watch?v=jNQXAC9IVRw',
    'https://www.youtube.com/delete?v=jNQXAC9IVRw'])
def test_youtube_transport_rejects_unrelated_endpoints_before_network(monkeypatch, url):
    monkeypatch.setattr(video, '_request', lambda *a, **kw: pytest.fail('network'))
    with pytest.raises(DataAdapterError, match='blocked_url'): video._YouTubeSession(RequestContext(), 'jNQXAC9IVRw').get(url)


def test_youtube_player_read_transport_strips_public_key_from_url(monkeypatch):
    session, calls = video._YouTubeSession(RequestContext(), 'jNQXAC9IVRw'), []
    monkeypatch.setattr(session, '_response', lambda r: r)
    monkeypatch.setattr(video, '_request', lambda url, **kw: calls.append((url, kw)) or reply('{}'))
    session.post('https://www.youtube.com/youtubei/v1/player?key=public-test-key', json={'context': {}, 'videoId': 'jNQXAC9IVRw'})
    assert calls[0][0] == 'https://www.youtube.com/youtubei/v1/player'
    assert calls[0][1]['headers']['X-Goog-Api-Key'] == 'public-test-key'
    with pytest.raises(DataAdapterError): session.post('https://evil.test/youtubei/v1/player?key=public-test-key', json={'context': {}, 'videoId': 'jNQXAC9IVRw'})
    with pytest.raises(DataAdapterError, match='web_content_challenge'): session.cookies.set('CONSENT', 'YES')


def test_caption_offsets_truncation_invalid_timing_and_timestamp_scope():
    text, segments, truncated = video._transcript([{'start': 10, 'duration': 3, 'text': '甲乙丙丁'}, {'start': 20, 'duration': 2, 'text': '后文'}], 3)
    assert text == '甲乙丙' and segments[0]['end_char'] == 3 and truncated
    details = {'content_origin': 'platform_captions', 'transcript_segments': segments}
    assert video._time_citation(details, BV + '?p=2', text, 0, 3)['timestamp_url'].endswith('p=2&t=10')
    assert not video._time_citation({**details, 'content_origin': 'video_metadata'}, BV, text, 0, 3)
    assert not video._time_citation({**details, 'transcript_segments': [{'start_char': 0}]}, BV, text, 0, 3)
    with pytest.raises(DataAdapterError): video._transcript([{'start': float('nan'), 'duration': 1, 'text': 'a'}], 10)


def test_no_metadata_and_no_captions_is_failure_not_empty_success(monkeypatch):
    def fail(*a, **kw): raise DataAdapterError('blocked_url')
    monkeypatch.setattr(video, '_json_read', fail)
    monkeypatch.setattr(video, '_youtube_captions', fail)
    result = retrieve(MaterialRequest('q', [YT]))
    assert not result.materials and 'blocked_url' in {d.code for d in result.diagnostics}


def test_platform_http_precondition_challenge_has_specific_diagnostic(monkeypatch):
    def fail(*a, **kw):
        exc = DataAdapterError('network'); exc.http_status = 412; raise exc
    monkeypatch.setattr(platform, '_request', fail)
    with pytest.raises(DataAdapterError, match='web_content_challenge'):
        platform._read('https://api.bilibili.com/x/web-interface/view?bvid=BV17x411w7KC', context=RequestContext(), hosts={'api.bilibili.com'})


def test_retrieve_known_platform_home_does_not_use_generic_reader(monkeypatch):
    import ir_search.services.retrieval as service
    monkeypatch.setattr(service, 'read_web_document', lambda *a, **kw: pytest.fail('generic fallback'))
    result = retrieve(MaterialRequest('q', ['https://xueqiu.com/123']))
    assert not result.materials and 'unsupported' in {d.code for d in result.diagnostics}


def test_mcp_payload_options_serializable(monkeypatch):
    reg, cn, *_ = registry(VideoMaterialAdapter, [BV])
    result = search_materials_payload(req(providers=['video'], dry_run=True, video_platforms=['bilibili']).to_dict(), registry=reg)
    assert result['plan']['source_plans'][0]['queries'][0]['platform'] == 'bilibili'
    assert not cn.calls
    monkeypatch.setattr(video, 'read_video_document', lambda *a, **kw: doc(YT, text='', details={'caption_status': 'video_captions_unavailable'}))
    result = retrieve_payload('q', [YT], video_languages=['en'])
    assert result['materials'][0]['text_origin'] == 'metadata_only'
    json.dumps(result)


def test_installed_youtube_library_uses_only_bounded_transport(monkeypatch):
    pytest.importorskip('youtube_transcript_api')
    calls = []
    def transport(url, **kw):
        calls.append((url, kw))
        if '/watch?' in url: return reply('"INNERTUBE_API_KEY":"public-test-key"')
        if '/youtubei/' in url:
            return reply(json.dumps({'playabilityStatus': {'status': 'OK'}, 'captions': {'playerCaptionsTracklistRenderer': {
                'captionTracks': [{'baseUrl': 'https://www.youtube.com/api/timedtext?v=jNQXAC9IVRw&signature=public-signature',
                                  'name': {'runs': [{'text': 'English'}]}, 'languageCode': 'en', 'kind': 'asr'}]}}}))
        return reply('<transcript><text start="10" dur="3">NVIDIA earnings.</text></transcript>')
    monkeypatch.setattr(video, '_request', transport)
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **kw: pytest.fail('unbounded requests network'))
    rows, language, kind = video._youtube_captions('jNQXAC9IVRw', ('en',), RequestContext())
    assert rows == [{'text': 'NVIDIA earnings.', 'start': 10.0, 'duration': 3.0}]
    assert language == 'en' and kind == 'automatic' and len(calls) == 3
    assert calls[-1][1]['signed_download'] and 'key=' not in calls[1][0]


def test_mcp_runtime_video_schema_and_option_forwarding(monkeypatch):
    import asyncio
    from ir_search import mcp_server
    FastMCP = pytest.importorskip('mcp.server.fastmcp').FastMCP
    server = FastMCP('video-test')
    monkeypatch.setattr(mcp_server, 'make_fastmcp', lambda _: server)
    monkeypatch.setattr(server, 'run', lambda: None)
    mcp_server.run()
    async def check():
        definitions = {t.name: t for t in await server.list_tools()}
        props = definitions['search_materials'].inputSchema['properties']
        assert 'video_platforms' in props and 'video_languages' in props
        assert 'video_languages' in definitions['retrieve'].inputSchema['properties']
        result = await server.call_tool('search_materials', {'question':'q', 'video_platforms': [], 'dry_run':True})
        assert 'invalid_request' in json.dumps(result, default=str)
    asyncio.run(check())
