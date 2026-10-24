from dataclasses import replace
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import pytest

from _platform import symlinks_supported
from ir_search import MaterialRequest, RequestContext, retrieve, export_material
from ir_search import mcp_server
from ir_search.infrastructure import wechat as wc
from ir_search.services import material_archive as archive
from ir_search.infrastructure.public_web import _Reply
from ir_search.registry import DataAdapterError
from test_wechat_materials import URL, NOW, ACCOUNT, PROFILE, row, request
from test_wechat_savings import transport

IMAGE = 'https://mmbiz.qpic.cn/mmbiz_png/public/chart?wx_fmt=png'
HTML = '''<html><head><meta property="og:title" content="渠道研究"><meta name="author" content="作者不是公众号"></head>
<body><span id="js_name">研究公众号</span><span id="js_author_name">研究员</span><script>var ct="1789472485";</script>
<div id="js_content" class="rich_media_content" style="visibility: hidden; opacity: 0;">
<h2>需求与库存</h2><p>收入 <strong>12.5</strong> 亿元。<br/>同比增长 8%。</p>
<ul><li>需求改善</li><li>仍需验证</li></ul><blockquote>上述数据未经审计。</blockquote>
<table><tr><th>指标</th><th>单位</th></tr><tr><td>收入</td><td>亿元</td></tr></table>
<a href="https://company.example/research.pdf">原始报告</a>
<p hidden>隐藏噪音</p><p style="display:none">隐藏广告</p><script>secret()</script>
<img data-src="'''+IMAGE+'''" alt="渠道图表"><p>图后文字，原图未识别。</p>
</div><p>推荐文章与导航</p></body></html>'''


def document(html=HTML, max_chars=100000):
    return wc._document(html,URL,NOW,max_chars=max_chars)


def material(monkeypatch, html=HTML, max_chars=100000):
    monkeypatch.setattr(wc,'_request',transport(html))
    return retrieve(MaterialRequest('收入',(URL,),max_chars=max_chars,wechat_cache_mode='off')).materials[0]


def test_initial_visibility_recovers_only_main_container_with_exact_citations(monkeypatch):
    m = material(monkeypatch)
    assert m.text_provider == 'wechat_origin' and 'wechat_initial_visibility_recovered' in m.warnings
    assert '12.5' in m.text and '未经审计' in m.text and '图后文字' in m.text
    assert all(v not in m.text for v in ('隐藏噪音','隐藏广告','secret','推荐文章与导航'))
    assert m.read_details['vendor_body_calls'] == 0 and not m.read_details['browser_attempted']
    assert m.article['metadata']['authors'] == ['研究员']
    assert m.provenance.publisher == '研究公众号'
    assert m.published_at.isoformat() == '2026-09-15T19:41:25+08:00'
    assert m.article['text_hash'] == m.text_hash
    assert {b['kind'] for b in m.article['blocks']} >= {'heading','paragraph','list_item','quote','table_row','image'}
    for b in m.article['blocks']:
        if b['kind'] != 'image': assert b['text'] == m.text[b['start_char']:b['end_char']]
    for s in m.evidence_spans: assert s['text'] == m.text[s['start_char']:s['end_char']]
    assert m.article['images'][0]['url'] == IMAGE and m.article['images'][0]['ocr_status']=='not_requested'
    assert m.links[0]['url'] == 'https://company.example/research.pdf'


@pytest.mark.parametrize('change',[
    ('class="rich_media_content"','class="unrelated"'),
    ('visibility: hidden; opacity: 0;','display:none; visibility:hidden; opacity:0;'),
    ('id="js_content"','id="js_content" hidden'),
    ('id="js_content"','id="js_content" aria-hidden="true"')])
def test_other_hidden_containers_are_not_revealed(change):
    with pytest.raises(DataAdapterError): document(HTML.replace(*change))


def test_hidden_parent_and_visible_challenge_never_become_success():
    for html in (HTML.replace('<body>','<body hidden>'),HTML.replace('<body>','<body><p>环境异常，请完成验证</p>')):
        with pytest.raises(DataAdapterError): document(html)


def test_void_elements_do_not_leak_footer_into_article_and_multiple_roots_rejected():
    h='<title>文章</title><div id="js_content"><p>第一行<br/>第二行<img src="'+IMAGE+'"/></p></div><p>不能进入正文</p>'
    d=document(h)
    assert '第二行' in d.text and '不能进入正文' not in d.text
    assert len(d.extra['article']['images']) == 1
    with pytest.raises(DataAdapterError): document(h+'<div id="js_content">另一个版本</div>')


def test_structure_truncation_never_exports_omitted_text():
    d = document(max_chars=4)
    a=d.extra['article']
    assert a['structure_truncated'] and '原图未识别' not in a['markdown']
    assert a['images']==[]
    assert all(b.get('text','') in d.text for b in a['blocks'])
    h='<title>表</title><div id="js_content"><table><tr><td>'+('数据'*60000)+'</td></tr></table></div>'
    d=document(h,max_chars=10)
    assert len(d.extra['article']['markdown']) < 300


def test_table_rows_spans_images_and_preformatted_text_remain_structured():
    h='<title>结构</title><div id="js_content"><table><tr><th colspan="2">指标表</th></tr><tr><td>收入</td><td>亿元<img src="'+IMAGE+'"></td></tr></table>'
    h+='<pre>first\n  indented</pre></div>'
    d=document(h)
    rows=[b for b in d.extra['article']['blocks'] if b['kind']=='table_row']
    assert rows[0]['cell_spans'][0]['colspan']==2 and rows[0]['table_id']==rows[1]['table_id']
    assert d.extra['article']['markdown'].count('<table>')==1
    assert d.extra['article']['images'][0]['position_basis']=='after_containing_table_row'
    assert 'first\n  indented' in d.text


def test_unsafe_links_images_and_html_injection_are_not_exported():
    h='<title>x</title><div id="js_content"><p>&lt;script&gt;bad()&lt;/script&gt; [x](javascript:alert(1))</p>'
    h+='<img src="http://127.0.0.1/private"><img src="https://public.example/img?token=private"><a href="javascript:alert(1)">链接</a></div>'
    a=document(h).extra['article']
    assert a['images']==[] and a['links']==[]
    assert '<script>' not in a['markdown'] and '\\[x\\]' in a['markdown']


def test_export_is_offline_by_default_and_versioned_not_title_based(tmp_path, monkeypatch):
    m=material(monkeypatch)
    monkeypatch.setattr(archive,'_request',lambda *a,**k:pytest.fail('default export must be offline'))
    root=tmp_path/'archive'
    result=export_material(m,root)
    assert result['status']=='ok' and result['image_requests']==0
    folder=Path(result['directory'])
    saved=json.loads((folder/'material.json').read_text(encoding='utf-8'))
    assert saved['text']==m.text and saved['evidence_spans']==m.evidence_spans
    markdown=(folder/'article.md').read_text(encoding='utf-8')
    assert '12.5' in markdown and '未经审计' in markdown
    assert not (folder/'images').exists()
    assert export_material(m,root)['status']=='reused'
    later=replace(m,provenance=replace(m.provenance,fetched_at=NOW+timedelta(hours=1)))
    assert export_material(later,root)['status']=='reused'
    other=replace(m,url=URL.replace('mid=123','mid=124'),original_url=URL.replace('mid=123','mid=124'))
    assert export_material(other,root)['directory']!=result['directory']
    edited=material(monkeypatch,HTML.replace('12.5','15.5'))
    assert export_material(edited,root)['directory']!=result['directory']
    (folder/'article.md').write_text('用户编辑的内容',encoding='utf-8')
    assert export_material(m,root)['status']=='error'
    assert (folder/'article.md').read_text(encoding='utf-8')=='用户编辑的内容'


def test_images_opt_in_deduplicate_validate_and_resume(tmp_path, monkeypatch):
    h=HTML.replace('</div><p>推荐','<img src="'+IMAGE+'"></div><p>推荐')
    m=material(monkeypatch,h); calls=[]
    def image(url, **kw):
        calls.append(url);kw['context'].begin_operation()
        return _Reply(200,'image/png','',b'\x89PNG\r\n\x1a\nfixture',NOW)
    monkeypatch.setattr(archive,'_request',image)
    root=tmp_path/'archive'
    result=export_material(m,root,download_images=True)
    assert result['status']=='ok' and result['downloaded_images']==1 and len(calls)==1
    assert '![' in (Path(result['directory'])/'article.md').read_text(encoding='utf-8')
    assert export_material(m,root,download_images=True)['status']=='reused' and len(calls)==1
    failed_root=tmp_path/'retry'
    monkeypatch.setattr(archive,'_request',lambda *a,**k:_Reply(503,'text/html','',b'bad',NOW))
    result=export_material(m,failed_root,download_images=True)
    assert result['status']=='partial'
    monkeypatch.setattr(archive,'_request',image)
    assert export_material(m,failed_root,download_images=True)['status']=='ok'


def test_image_redirect_and_file_type_are_checked(tmp_path,monkeypatch):
    m=material(monkeypatch)
    calls=[]
    def redirect(url,**kw):
        calls.append(url)
        return _Reply(302,'','http://127.0.0.1/private',b'',NOW)
    monkeypatch.setattr(archive,'_request',redirect)
    r=export_material(m,tmp_path/'redirect',download_images=True)
    assert r['status']=='partial' and len(calls)==1
    monkeypatch.setattr(archive,'_request',lambda *a,**k:_Reply(200,'image/svg+xml','',b'<svg><script>bad()</script></svg>',NOW))
    r=export_material(m,tmp_path/'svg',download_images=True)
    assert r['status']=='partial' and r['downloaded_images']==0
    r=export_material(m,tmp_path/'zero',download_images=True,max_images=0)
    assert r['image_requests']==0 and 'archive_image_budget_exhausted' in r['diagnostics']


def test_export_cancel_symlink_and_structure_mismatch(tmp_path,monkeypatch):
    m=material(monkeypatch)
    if symlinks_supported():
        target=tmp_path/'other';target.mkdir();link=tmp_path/'linked';link.symlink_to(target,target_is_directory=True)
        assert export_material(m,link)['status']=='error'
    ctx=RequestContext();ctx.cancel()
    assert export_material(m,tmp_path/'cancelled',context=ctx)['diagnostics']==['cancelled']
    bad=replace(m,article={**m.article,'text_hash':'bad'})
    assert export_material(bad,tmp_path/'bad')['status']=='error'


def test_sdk_mcp_archive_options_and_search_structure(tmp_path,monkeypatch):
    m=material(monkeypatch)
    monkeypatch.setenv('IR_SEARCH_OUTPUT_ROOT', str(tmp_path))  # MCP writes are confined to one root
    result=mcp_server.retrieve_payload('收入',[URL],wechat_cache_mode='off',archive_dir=str(tmp_path/'mcp'))
    assert result['materials'][0]['archive']['status']=='ok'
    assert result['materials'][0]['article']['blocks']
    for args in ({'archive_images':True},{'archive_dir':''},{'max_archive_images':21}):
        with pytest.raises(ValueError): MaterialRequest('收入',(URL,),**args)
    from ir_search import MaterialRegistry,search_materials
    from ir_search.adapters.wechat_materials import WechatMaterialAdapter
    from test_wechat_materials import Client
    registry=MaterialRegistry()
    registry.register(WechatMaterialAdapter(PROFILE,client=Client([row()]),reader=lambda *a,**k:document()))
    result=search_materials(request(question='需求',keywords=['需求']),registry=registry).to_dict()
    v=result['items'][0]['versions'][0]
    assert v['article']['metadata']['authors']==['研究员'] and v['links']
    assert all(s['text']==v[s['source_part']][s['start_char']:s['end_char']] for s in v['evidence_spans'])


def test_plain_web_material_can_use_same_exporter(tmp_path,monkeypatch):
    m=replace(material(monkeypatch),article={},text_provider=None)
    assert export_material(m,tmp_path/'web')['status']=='ok'
