from ir_search.documents import fetch_document


def test_fetch_document_pdf_records_extraction_errors_or_warnings(monkeypatch):
    raw = b"%PDF-1.4\n1 0 obj <<>>\nendobj\ntrailer <<>>\n%%EOF"
    monkeypatch.setattr("ir_search.documents.fetcher._open_once", lambda opener, req, timeout: _response(raw, "application/pdf"))

    document = fetch_document("https://example.com/report.pdf")

    assert document.content_type == "pdf"
    assert document.errors or document.warnings
    assert document.extra["source_text_trust"] == "untrusted"


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
