"""Regression cases for real transport/session failures, using synthetic text only."""
import json
from datetime import datetime, timezone

import pytest

from ir_search import MaterialRequest, RequestContext, retrieve
from ir_search.infrastructure import community_documents as community, video_documents as video
from ir_search.infrastructure.public_web import _Reply
from ir_search.infrastructure.recovery import _recovery
from ir_search.registry import DataAdapterError

URL = 'https://www.bilibili.com/video/BV17x411w7KC'
TRACK = {'lan': 'ai-zh', 'subtitle_url': 'https://aisubtitle.hdslb.com/a?auth_key=synthetic-signature'}


def response(text=''):
    return _Reply(200, 'text/html', '', text.encode(), datetime.now(timezone.utc))


def fixture(monkeypatch, *, player=None, rows=None):
    calls = []
    def read(url, **kw):
        calls.append((url, kw))
        if '/view?' in url:
            value = {'code': 0, 'data': {'bvid': 'BV17x411w7KC', 'aid': 123, 'title': '研究视频',
                'cid': 456, 'pages': [{'page': 1, 'cid': 456, 'duration': 300}]}}
        elif '/player/' in url:
            value = {'code': 0, 'data': player if player is not None else
                     {'bvid': 'BV17x411w7KC', 'aid': 123, 'cid': 456, 'subtitle': {'subtitles': [TRACK]}}}
        else:
            value = {'body': rows if rows is not None else [{'from': 1, 'to': 3, 'content': '字幕正文'}]}
        return value, response()
    monkeypatch.setattr(video, '_json_read', read)
    monkeypatch.setattr(video, '_cookie', lambda _: 'synthetic-private-cookie')
    return calls


def test_empty_preferred_track_uses_available_requested_language_and_no_cookie_on_cdn(monkeypatch):
    calls = fixture(monkeypatch, player={'subtitle': {'subtitles': [
        {'lan': 'zh-Hans', 'subtitle_url': ''}, TRACK]}})
    doc = video.read_video_document(URL, context=RequestContext())
    assert doc.text == '字幕正文'
    assert 'video_caption_track_url_missing' in doc.warnings
    assert 'video_caption_ends_before_video' in doc.warnings
    details = doc.extra['web_read']
    assert details['caption_completeness'] == 'not_verified'
    assert details['caption_rows_returned'] == 1 and details['caption_returned_end_seconds'] == 3
    assert details['caption_language'] == 'ai-zh'
    assert 'cookie' not in calls[-1][1] and calls[-1][1]['signed']
    assert 'synthetic-private-cookie' not in json.dumps(doc.extra)
    assert 'synthetic-signature' not in json.dumps(doc.extra)


@pytest.mark.parametrize('player,code', [
    ({'need_login_subtitle': True, 'subtitle': {'subtitles': []}}, 'video_captions_login_required'),
    ({'subtitle': {'subtitles': [{'lan': 'zh-Hans', 'subtitle_url': ''}]}}, 'video_caption_url_unavailable'),
    ({'subtitle': {'subtitles': []}}, 'video_captions_unavailable'),
    ({'subtitle': {'subtitles': [{'lan': 'fr', 'subtitle_url': ''}]}}, 'video_caption_language_unavailable'),
    ({'subtitle': ['not-a-dict']}, 'upstream_schema'),
    ({'subtitle': {'subtitles': [{'lan': 1}]}}, 'upstream_schema'),
    ({'bvid': 'BV11x411w7KC', 'subtitle': {'subtitles': [TRACK]}}, 'upstream_schema'),
    ({'cid': 999, 'subtitle': {'subtitles': [TRACK]}}, 'upstream_schema'),
    ({'aid': 999, 'subtitle': {'subtitles': [TRACK]}}, 'upstream_schema'),
])
def test_missing_or_mismatched_captions_keep_uncitable_metadata(monkeypatch, player, code):
    calls = fixture(monkeypatch, player=player)
    result = retrieve(MaterialRequest('字幕正文', [URL]), context=RequestContext()).to_dict()
    assert len(calls) == 2
    assert result['materials'][0]['text_origin'] == 'metadata_only'
    assert result['materials'][0]['text'] == '' and not result['materials'][0]['evidence_spans']
    assert code in {d['code'] for d in result['diagnostics']}


def test_unsafe_nonempty_caption_url_never_relaxes_policy(monkeypatch):
    calls = fixture(monkeypatch, player={'subtitle': {'subtitles': [
        {'lan': 'zh-Hans', 'subtitle_url': 'http://127.0.0.1/secret'}, TRACK]}})
    doc = video.read_video_document(URL, context=RequestContext())
    assert not doc.text and 'blocked_url' in doc.warnings and len(calls) == 2


def test_nonexistent_part_does_not_return_first_part_metadata(monkeypatch):
    calls = fixture(monkeypatch)
    with pytest.raises(DataAdapterError, match='not_found'):
        video.read_video_document(URL+'?p=2', context=RequestContext())
    assert len(calls) == 1


@pytest.mark.parametrize('code,expected', [(-101, 'authentication_failed'), (-352, 'web_content_challenge'),
    (-412, 'web_content_challenge'), (412, 'web_content_challenge'), (-509, 'rate_limit'),
    (-404, 'not_found'), (62002, 'not_found'), (-403, 'entitlement_denied'),
    (-999, 'upstream_schema'), (False, 'upstream_schema'), ('0', 'upstream_schema'), ([], 'upstream_schema')])
def test_api_error_classification_is_safe(monkeypatch, code, expected):
    monkeypatch.setattr(video, '_json_read', lambda *a, **k: ({'code': code, 'message': 'private-error'}, response()))
    with pytest.raises(DataAdapterError, match=expected) as caught:
        video._bili_data('https://api.bilibili.com/x/web-interface/view?bvid=BV17x411w7KC', RequestContext())
    assert 'private-error' not in str(caught.value)


def test_timing_validation_still_covers_rows_beyond_character_limit(monkeypatch):
    fixture(monkeypatch, rows=[{'from': 1, 'to': 2, 'content': '正常正文'},
        {'from': 3, 'to': float('nan'), 'content': '异常后续行'}])
    doc = video.read_video_document(URL, context=RequestContext(), max_chars=1)
    assert not doc.text and 'upstream_schema' in doc.warnings


def test_caption_end_beyond_part_duration_is_not_citable(monkeypatch):
    fixture(monkeypatch, rows=[{'from': 1, 'to': 2, 'content': '开头'},
        {'from': 410, 'to': 418, 'content': '超出视频时长的字幕'}])
    result = retrieve(MaterialRequest('字幕', [URL]), context=RequestContext()).to_dict()
    mat = result['materials'][0]
    assert not mat['text'] and not mat['evidence_spans']
    assert mat['text_origin'] == 'metadata_only'
    assert 'video_caption_timing_mismatch' in {d['code'] for d in result['diagnostics']}


def test_xueqiu_captcha_words_in_real_post_or_login_widget_do_not_hide_body(monkeypatch):
    data = {'id': 456, 'title': '软件调研', 'text': '<p>请输入验证码，是用户注册界面文字。</p>',
            'user': {'screen_name': '测试作者'}}
    html = '<script>SNB.data.status=' + json.dumps(data) + ';</script><aside>人机验证</aside>'
    monkeypatch.setattr(community, '_read', lambda *a, **k: response(html))
    doc = community.read_community_document('https://xueqiu.com/123/456', context=RequestContext())
    assert doc.text == '请输入验证码，是用户注册界面文字。'


@pytest.mark.parametrize('url', ['https://xueqiu.com/123/456', 'https://guba.eastmoney.com/news,600519,123456.html'])
def test_challenge_wrapper_is_never_post_text(monkeypatch, url):
    html = '<textarea id="renderData">{"_waf_synthetic":"opaque"}</textarea><p>请验证</p>'
    monkeypatch.setattr(community, '_read', lambda *a, **k: response(html))
    with pytest.raises(DataAdapterError, match='web_content_challenge'):
        community.read_community_document(url, context=RequestContext())


def test_login_and_empty_url_have_specific_recovery_actions():
    assert _recovery('video_captions_login_required')['category'] == 'authentication'
    assert _recovery('video_caption_url_unavailable')['category'] == 'content_scope'
