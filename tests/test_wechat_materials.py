"""Synthetic WeChat contracts, account isolation, failures and evidence fidelity."""
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import pytest

from _platform import POSIX_PERMISSIONS, symlinks_supported
from ir_search import MaterialRegistry, MaterialSearchRequest, RequestContext, search_materials, build_material_registry
from ir_search.adapters.wechat_materials import WechatMaterialAdapter
from ir_search.infrastructure.credentials import (WechatAccount,WechatProfile,wechat_profile,SourceConfigError,source_configuration_status)
from ir_search.infrastructure.wechat import (DajialaMaterialClient,WechatHistoryPage,normalize_wechat_url,fetch_wechat_document,_document,_published)
from ir_search.infrastructure.public_web import _Reply
from ir_search.registry import DataAdapterError
from ir_search import mcp_server

NOW=datetime(2026,9,16,tzinfo=timezone.utc)
ACCOUNT=WechatAccount('研究公众号','gh_testaccount')
PROFILE=WechatProfile('test_key_not_private',(ACCOUNT,))
URL='https://mp.weixin.qq.com/s?__biz=MzTestAA%3D%3D&mid=123&idx=1&sn='+'a'*32
HTML='<html><head><title>消费研究</title></head><body><p>页面导航</p><div id="js_content"><p>茅台动销与渠道库存调研。</p><p>本文描述渠道观察，不能当成确定事实。</p><script>bad()</script><p hidden>秘密导航</p></div><p>登录</p></body></html>'


def row(n=123, **changes):
    return dict({'url':URL.replace('mid=123','mid='+str(n)), 'title':'茅台动销研究','post_time_str':'2026-09-15 19:41:25','digest':'茅台渠道调研摘要'},**changes)


def request(**kwargs):
    return MaterialSearchRequest(**dict(dict(question='茅台动销', keywords=['动销'],published_start='2026-09-01',published_end='2026-09-16',providers=['wechat'],wechat_accounts=[ACCOUNT.name],candidates_per_source=5,text_reads_per_source=1),**kwargs))


def doc(url=URL,*,vendor=False,max_chars=20000):
    return _document(HTML,url,NOW,max_chars=max_chars,vendor=vendor,metadata={'title':'茅台动销研究','post_time_str':'2026-09-15 19:41:25','nickname':ACCOUNT.name,'author':'研究作者'})


class Client:
    def __init__(self, rows=None, pages=None):self.rows=rows if rows is not None else [row()];self.calls=[];self.pages=pages
    def history(self,account,*,cursor='',context):
        context.begin_operation();self.calls.append((account,cursor))
        if self.pages:
            result=self.pages[len(self.calls)-1]
            if isinstance(result,Exception):raise result
            return result
        return WechatHistoryPage(tuple(self.rows),account.name,account.ghid,None,False,NOW)
    def article(self,url,*,context,max_chars):context.begin_operation();return doc(url,vendor=True,max_chars=max_chars)


def setup(rows=None,*,profile=PROFILE,reader=None,pages=None):
    client=Client(rows,pages); reads=[]
    def read(url,**kw):
        reads.append(url);kw['context'].begin_operation()
        return reader(url,**kw) if reader else doc(url,max_chars=kw['max_chars'])
    registry=MaterialRegistry();registry.register(WechatMaterialAdapter(profile,client=client,reader=read))
    return registry,client,reads


def test_scoped_search_dates_source_and_character_citations():
    reg,c,reads=setup()
    result=search_materials(request(),registry=reg).to_dict()
    assert result['status']=='partial' and len(reads)==1 and not result['complete']
    v=result['items'][0]['versions'][0]
    assert v['channel']=='wechat' and v['text_scope']=='extracted_text' and v['text_provider']=='wechat_origin'
    assert v['discovery_provider']=='dajiala' and v['collection_id']==ACCOUNT.ghid
    assert v['provenance']['publisher']==ACCOUNT.name and v['provenance']['authority']=='unknown'
    assert v['authors']==['研究作者'] and v['published_at'].endswith('+08:00')
    for s in v['evidence_spans']:assert s['text']==v[s['source_part']][s['start_char']:s['end_char']]
    scan=result['coverage'][0]['scans'][0]
    assert scan['received_count']==scan['inspected_count']==1 and scan['date_filter_basis']=='local_publication_metadata'
    assert 'publication_filter_applied_locally' in {g['code'] for g in result['gaps']}


def test_request_validation_mcp_and_opt_in_registry(tmp_path,monkeypatch):
    for value in ('name',[1],['x'*101],['x']*21):
        with pytest.raises(ValueError):request(wechat_accounts=value)
    account_file=tmp_path/'private_accounts.json'
    account_file.write_text(json.dumps([{'name':ACCOUNT.name,'ghid':ACCOUNT.ghid}]),encoding='utf8');account_file.chmod(0o600)
    env=tmp_path/'wechat.env';env.write_text('WECHAT_MATERIALS_ENABLED=true\nDAJIALA_KEY=test_key_not_private\nWECHAT_ACCOUNTS_FILE=private_accounts.json\n');env.chmod(0o600)
    p=wechat_profile(env_file=env)
    assert p.accounts==(ACCOUNT,) and PROFILE.api_key not in repr(p) and ACCOUNT.name not in repr(p)
    assert [a.name for a in build_material_registry(env_file=env).entries()]==['wechat']
    status=source_configuration_status(env_file=env)
    item=next(v for v in status['sources'] if v['provider']=='wechat')
    assert item['configured'] and not item['live_verified'] and ACCOUNT.name not in json.dumps(status)
    assert wechat_profile(values={}) is None
    reg,_,_=setup()
    result=mcp_server.search_materials_payload(request().to_dict(),registry=reg)
    assert result['items'][0]['versions'][0]['text_provider']=='wechat_origin'


@pytest.mark.parametrize('data', [None,{},[],[{'name':'same'},{'name':'same'}],[{'name':'x','secret':'no'}],[{'name':'x','ghid':'invalid'}]])
def test_invalid_private_inventory(tmp_path,data):
    p=tmp_path/'a.json';p.write_text(json.dumps(data));p.chmod(0o600)
    with pytest.raises(SourceConfigError):wechat_profile(values={'WECHAT_MATERIALS_ENABLED':'true','DAJIALA_KEY':PROFILE.api_key,'WECHAT_ACCOUNTS_FILE':str(p)})


def test_private_inventory_permissions_symlink_and_configuration_failures(tmp_path):
    p=tmp_path/'a.json';p.write_text('[{"name":"test"}]',encoding='utf-8')
    values={'WECHAT_MATERIALS_ENABLED':'true','DAJIALA_KEY':PROFILE.api_key,'WECHAT_ACCOUNTS_FILE':str(p)}
    if POSIX_PERMISSIONS:
        p.chmod(0o644)
        with pytest.raises(SourceConfigError,match='permissions'):wechat_profile(values=values)
    p.chmod(0o600)
    if symlinks_supported():
        link=tmp_path/'link';link.symlink_to(p)
        with pytest.raises(SourceConfigError):wechat_profile(values=dict(values,WECHAT_ACCOUNTS_FILE=str(link)))
    for more in({'WECHAT_MAX_ACCOUNTS_PER_QUERY':'6'},{'WECHAT_MAX_PAGES_PER_ACCOUNT':'4'},{'DAJIALA_KEY':''},{'WECHAT_MATERIALS_ENABLED':'yes'}):
        with pytest.raises(SourceConfigError):wechat_profile(values=dict(values,**more))


def test_allowlist_budget_date_unknown_dates_and_url_dedup():
    accounts=(ACCOUNT,WechatAccount('second','gh_second'),WechatAccount('third','gh_third'))
    profile=replace(PROFILE,accounts=accounts,max_accounts_per_query=2)
    reg,c,reads=setup([row(),row(url=URL+'&scene=123#rd'),row(124,post_time_str='2025-01-01 00:00:00'),row(125,post_time_str='bad')],profile=profile)
    result=search_materials(request(wechat_accounts=[],candidates_per_source=8,text_reads_per_source=0),registry=reg)
    assert len(c.calls)==2 and not reads
    versions=[v for i in result.items for v in i['versions']]
    assert len(versions)==2 and {v['text_scope'] for v in versions}=={'abstract'}
    assert any('published_date_unknown' in v['warnings'] for v in versions)
    assert 'wechat_account_budget_exhausted' in {d.code for d in result.diagnostics}
    result=search_materials(request(wechat_accounts=['unconfigured']),registry=reg)
    assert 'wechat_account_unresolved' in {d.code for d in result.diagnostics} and len(c.calls)==2


def test_pagination_cursor_and_failed_account_are_observable():
    pages=[WechatHistoryPage((row(),),ACCOUNT.name,ACCOUNT.ghid,'cursor_2',True,NOW),DataAdapterError('quota')]
    reg,c,_=setup(pages=pages)
    result=search_materials(request(text_reads_per_source=0),registry=reg)
    assert c.calls[1][1]=='cursor_2' and result.items
    assert [s['state'] for s in result.coverage[0]['scans']]==['queried','failed']
    assert 'quota' in {d.code for d in result.diagnostics}
    reg,_,_=setup(pages=[DataAdapterError('authentication_failed')])
    result=search_materials(request(),registry=reg)
    assert result.status.value=='unavailable' and result.coverage[0]['state']=='source_queries_failed'
    assert not any(g['code']=='no_match_in_scanned_records' for g in result.gaps)


def test_cursor_stalls_and_candidate_budget():
    pages=[WechatHistoryPage((row(),),ACCOUNT.name,ACCOUNT.ghid,'cursor_2',True,NOW),WechatHistoryPage((row(124),),ACCOUNT.name,ACCOUNT.ghid,'cursor_2',True,NOW)]
    reg,_,_=setup(pages=pages)
    result=search_materials(request(text_reads_per_source=0),registry=reg)
    assert 'wechat_pagination_stalled' in {d.code for d in result.diagnostics}
    reg,_,_=setup([row(i) for i in range(10)])
    result=search_materials(request(candidates_per_source=2,text_reads_per_source=0),registry=reg)
    assert result.coverage[0]['scanned_count']==2 and result.coverage[0]['scans'][0]['received_count']==10


def test_failed_body_keeps_abstract_scope_no_fake_full_text():
    def failed(*args,**kwargs):raise DataAdapterError('not_found')
    reg,_,reads=setup([row(),row(124)],reader=failed)
    result=search_materials(request(),registry=reg)
    assert len(reads)==1
    assert all(v['text_scope']=='abstract' for i in result.items for v in i['versions'])
    assert 'original_text_fetch_failed' in result.items[0]['versions'][0]['warnings']


@pytest.mark.parametrize('url',[None,'https://evil.test/s?x=mp.weixin.qq.com',URL.replace('mp.weixin.qq.com','mp.weixin.qq.com.evil.test'),URL.replace('mp.weixin.qq.com','user@mp.weixin.qq.com'),URL+'&key=secret',URL+'&mid=999',URL.replace('/s?','/other?'),'file:///etc/passwd',URL+'&pass_ticket=secret'])
def test_wechat_url_is_strict_and_credential_free(url):
    with pytest.raises(DataAdapterError,match='blocked_url'):normalize_wechat_url(url)


def test_url_cleanup_html_entities_short_links_and_timezone():
    assert normalize_wechat_url(URL.replace('&','&amp;')+'&scene=1#rd')==URL
    assert normalize_wechat_url('http://mp.weixin.qq.com/s/abcdefghijk?scene=1')=='https://mp.weixin.qq.com/s/abcdefghijk'
    assert _published({'post_time':1789472485}).isoformat()=='2026-09-15T19:41:25+08:00'
    assert _published({'post_time_str':'2026-09-15 19:41:25'}).isoformat()=='2026-09-15T19:41:25+08:00'
    assert _published({'post_time':'bad'}) is None


def test_original_parser_excludes_chrome_scripts_and_hidden_nodes():
    d=doc();assert '茅台' in d.text
    assert all(x not in d.text for x in ('导航','bad()','登录'))
    assert d.extra['text_provider']=='wechat_origin'
    assert 'text_truncated' in doc(max_chars=5).warnings
    with pytest.raises(DataAdapterError):_document('<title>验证</title><body>环境异常，请完成验证</body>',URL,NOW,max_chars=500)


def test_vendor_fallback_is_explicit_and_public_request_has_no_credentials():
    calls=[]
    def transport(url,**kw):
        calls.append((url,kw));kw['context'].begin_operation()
        return _Reply(200,'text/html','',b'<title>challenge</title><p>captcha</p>',NOW)
    d=fetch_wechat_document(URL,context=RequestContext(),client=Client(),transport=transport)
    assert d.extra['text_provider']=='dajiala' and 'wechat_vendor_fallback_used' in d.warnings
    assert 'vendor_text_not_original_file' in d.warnings
    assert all('body' not in kw and 'headers' not in kw for _,kw in calls)
    with pytest.raises(DataAdapterError):fetch_wechat_document(URL,context=RequestContext(),transport=transport,cache_mode='off')


def api_client(responses):
    calls=[]
    def transport(url,**kwargs):
        kwargs['context'].begin_operation();calls.append((url,kwargs))
        item=responses.pop(0)
        return _Reply(200,'application/json','',json.dumps(item).encode(),NOW)
    return DajialaMaterialClient(PROFILE,transport=transport),calls


def test_vendor_transport_exact_account_match_cursor_and_identity():
    c,calls=api_client([{'code':0,'data':[{'name':'unrelated','ghid':'gh_wrong'},{'name':ACCOUNT.name,'ghid':ACCOUNT.ghid}]},
        {'code':0,'data':[row()],'nickname':ACCOUNT.name,'ghid':ACCOUNT.ghid,'offset':'nextopaque==','is_end':0}])
    page=c.history(WechatAccount(ACCOUNT.name),context=RequestContext())
    assert page.next_cursor=='nextopaque==' and c.resolve_account(WechatAccount(ACCOUNT.name),context=RequestContext())==ACCOUNT.ghid
    assert len(calls)==2
    for url,kw in calls:
        assert url.startswith('https://www.dajiala.com/') and '?' not in url and PROFILE.api_key not in url
        assert json.loads(kw['body'])['key']==PROFILE.api_key and kw['method']=='POST'
    assert json.loads(calls[1][1]['body'])['offset']==''


@pytest.mark.parametrize('code,expected',[(10002,'authentication_failed'),(20001,'quota'),(-1,'rate_limit'),(101,'not_found'),(999,'upstream_schema')])
def test_vendor_errors_do_not_echo_messages_or_keys(code,expected):
    c,_=api_client([{'code':code,'msg':'PRIVATE_KEY_MUST_NOT_ESCAPE','remain_money':1234}])
    with pytest.raises(DataAdapterError,match=expected) as e:c.history(ACCOUNT,context=RequestContext())
    assert 'PRIVATE' not in str(e.value)


def test_no_fuzzy_match_no_wrong_article_no_foreign_redirects():
    c,_=api_client([{'code':0,'data':[{'name':'similar','ghid':'gh_bad'}]}])
    with pytest.raises(DataAdapterError,match='unresolved'):c.resolve_account(WechatAccount(ACCOUNT.name),context=RequestContext())
    c,_=api_client([{'code':0,'data':{'article_url':URL.replace('mid=123','mid=999'),'html':HTML}}])
    with pytest.raises(DataAdapterError,match='mismatch'):c.article(URL,context=RequestContext(),max_chars=1000)
    c,_=api_client([{'code':0,'data':{'article_url':URL,'html':HTML,'title':'消费','nickname':ACCOUNT.name}}])
    assert c.article(URL,context=RequestContext(),max_chars=1000).extra['text_provider']=='dajiala'


def test_retrieve_wechat_dispatch_and_evidence(monkeypatch):
    from ir_search import MaterialRequest,retrieve
    import ir_search.infrastructure.wechat as wc
    monkeypatch.setattr('ir_search.services.retrieval.is_url_allowed',lambda url:type('Policy',(),{'allowed':True})())
    monkeypatch.setattr(wc,'fetch_wechat_document',lambda url,**kw:doc(url,vendor=True,max_chars=kw['max_chars']))
    bundle=retrieve(MaterialRequest('茅台动销',(URL,)))
    m=bundle.materials[0]
    assert m.text_provider=='dajiala' and m.original_url==URL and m.text_origin=='extracted_text'
    assert 'vendor_text_not_original_file' in m.warnings
    for s in m.evidence_spans:assert s['text']==m.text[s['start_char']:s['end_char']]


def test_deadline_cancel_and_operation_budget_prevent_hidden_extra_calls():
    from ir_search.context import RequestStopped
    context=RequestContext();context.cancel()
    c,calls=api_client([])
    with pytest.raises(RequestStopped,match='cancelled'):c.history(ACCOUNT,context=context)
    assert not calls
    def expired(url,**kwargs):
        kwargs['context'].begin_operation()
        raise RequestStopped('deadline_exceeded')
    d=fetch_wechat_document(URL,context=RequestContext(),transport=expired,client=Client())
    assert 'origin_failure_timeout' in d.warnings
    ctx=RequestContext(max_operations=1);ctx.begin_operation()
    with pytest.raises(RequestStopped,match='operation_budget_exhausted'):
        fetch_wechat_document(URL,context=ctx,transport=expired,client=Client(),cache_mode='off')


def test_transport_redirect_and_schema_drift_fail_closed():
    def redirect(url,**kwargs):return _Reply(302,'text/html','https://evil.test',b'',NOW)
    c=DajialaMaterialClient(PROFILE,transport=redirect)
    with pytest.raises(DataAdapterError):c.history(ACCOUNT,context=RequestContext())
    for bad in ([{'code':'0'}], [{'code':True}], [{'code':0,'data':None}], [{'code':0,'mode':2023}],
                [{'code':0,'data':[],'nickname':ACCOUNT.name,'ghid':'gh_wrong','is_end':1}],
                [{'code':0,'data':[],'nickname':ACCOUNT.name,'ghid':ACCOUNT.ghid,'is_end':0,'offset':''}]):
        c,_=api_client(bad)
        with pytest.raises(DataAdapterError):c.history(ACCOUNT,context=RequestContext())


def test_unresolved_accounts_not_registered_and_direct_body_date_conflict(tmp_path):
    p=tmp_path/'bad.env';p.write_text('WECHAT_MATERIALS_ENABLED=true\n');p.chmod(0o600)
    reg=build_material_registry(env_file=p)
    assert not reg.entries() and reg.diagnostics[0].code=='wechat_accounts_missing'
    def wrong_date(url,**kw):return replace(doc(url),published_at=datetime(2025,1,1,tzinfo=timezone.utc))
    reg,_,_=setup(reader=wrong_date)
    result=search_materials(request(),registry=reg)
    assert not result.items and 'wechat_outside_publication_window' in {d.code for d in result.diagnostics}


def test_body_keywords_can_match_when_title_and_digest_do_not():
    reg,_,_=setup([row(title='渠道观察',digest='')])
    result=search_materials(request(),registry=reg)
    assert result.items and result.items[0]['versions'][0]['evidence_spans'][0]['source_part']=='text'


def test_short_article_links_can_resolve_without_title_based_guessing():
    short='https://mp.weixin.qq.com/s/abcdefghijk'
    reg,_,_=setup([row(url=short,original=2)],reader=lambda url,**kw:doc(URL,vendor=True))
    result=search_materials(request(),registry=reg)
    v=result.items[0]['versions'][0]
    assert v['source_ref']==short and v['original_url']==URL and v['source_document_id']==URL
    assert 'short_url_resolved_by_text_provider' in v['warnings'] and 'vendor_reports_repost' in v['warnings']
