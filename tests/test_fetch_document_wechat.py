from ir_search.documents import fetch_document
from ir_search.models import EvidenceType


def test_fetch_document_wechat_hint_marks_untrusted_wechat_content(monkeypatch):
    html = """<html><head><title>微信文章</title></head>
    <body><p>公众号观点认为产业链涨价需要公告和新闻交叉验证。</p></body></html>"""
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout: _response(html.encode("utf-8"), "text/html"))

    document = fetch_document("https://mp.weixin.qq.com/s/test", source_hint="wechat")

    assert document.content_type == "wechat"
    assert document.evidence_type == EvidenceType.OPINION
    assert document.extra["source_text_trust"] == "untrusted"
    assert "交叉验证" in document.text


def _response(raw, content_type):
    class FakeResponse:
        headers = {"content-type": content_type}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, _limit):
            return raw

    return FakeResponse()
