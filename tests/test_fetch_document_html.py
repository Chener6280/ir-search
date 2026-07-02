from ir_search.documents import fetch_document


def test_fetch_document_extracts_html_fixture(monkeypatch):
    html = """<!doctype html>
    <html>
      <head>
        <title>样例文章</title>
        <link rel="canonical" href="https://example.com/canonical-article">
        <meta property="article:published_time" content="2026-07-01T08:00:00+08:00">
      </head>
      <body>
        <article>
          <h1>样例文章</h1>
          <p>中际旭创 一季报 显示海外 AI 光模块需求保持强劲。</p>
          <p>管理层同时提示产能和交付节奏存在不确定性。</p>
        </article>
      </body>
    </html>"""
    monkeypatch.setattr("ir_search.documents.fetcher._open_once", lambda opener, req, timeout: _response(html.encode("utf-8"), "text/html"))

    document = fetch_document("https://example.com/article.html")

    assert document.content_type == "html"
    assert document.title == "样例文章"
    assert document.canonical_url == "https://example.com/canonical-article"
    assert "海外 AI 光模块需求保持强劲" in document.text
    assert document.published_at is not None


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
