import urllib.error

from ir_search.documents import fetch_document


def test_redirect_to_public_allowed(monkeypatch):
    responses = [
        _redirect("https://example.org/final", 302),
        _response(b"<html><title>Final</title><p>public body</p></html>", "text/html"),
    ]
    monkeypatch.setattr("ir_search.documents.fetcher._open_once", _sequence(responses))

    document = fetch_document("https://example.com/start")

    assert document.errors == []
    assert document.url == "https://example.org/final"
    assert document.extra["redirect_chain"][0]["to"] == "https://example.org/final"


def test_redirect_to_localhost_blocked(monkeypatch):
    monkeypatch.setattr("ir_search.documents.fetcher._open_once", _sequence([_redirect("http://localhost:8000/private", 302)]))

    document = fetch_document("https://example.com/start")

    assert "blocked_by_policy" in document.errors[0]
    assert document.extra["redirect_chain"][0]["to"] == "http://localhost:8000/private"


def test_redirect_to_private_ip_blocked(monkeypatch):
    monkeypatch.setattr("ir_search.documents.fetcher._open_once", _sequence([_redirect("http://192.168.1.1/private", 302)]))

    document = fetch_document("https://example.com/start")

    assert "blocked_by_policy" in document.errors[0]
    assert "192.168.1.1" in document.extra["redirect_chain"][0]["to"]


def test_redirect_chain_limit(monkeypatch):
    monkeypatch.setattr(
        "ir_search.documents.fetcher._open_once",
        _sequence([_redirect(f"https://example.com/{idx}", 302) for idx in range(6)]),
    )

    document = fetch_document("https://example.com/start")

    assert "redirect limit exceeded" in document.errors[0]
    assert len(document.extra["redirect_chain"]) == 6


def test_relative_redirect_resolved_and_checked(monkeypatch):
    responses = [
        _redirect("/final", 302),
        _response(b"<html><title>Final</title><p>relative body</p></html>", "text/html"),
    ]
    monkeypatch.setattr("ir_search.documents.fetcher._open_once", _sequence(responses))

    document = fetch_document("https://example.com/start")

    assert document.extra["redirect_chain"][0]["to"] == "https://example.com/final"
    assert document.errors == []


def test_redirect_chain_recorded_in_document_extra(monkeypatch):
    responses = [
        _redirect("https://example.org/final", 301),
        _response(b"<html><title>Final</title><p>body</p></html>", "text/html"),
    ]
    monkeypatch.setattr("ir_search.documents.fetcher._open_once", _sequence(responses))

    document = fetch_document("https://example.com/start")

    assert document.extra["requested_url"] == "https://example.com/start"
    assert document.extra["redirect_chain"] == [
        {"from": "https://example.com/start", "to": "https://example.org/final", "status": "301"}
    ]


def _sequence(responses):
    remaining = list(responses)

    def fake_open(opener, req, timeout):
        del opener, timeout
        response = remaining.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    return fake_open


def _redirect(location, code):
    headers = {"Location": location}
    return urllib.error.HTTPError("https://example.com/start", code, "redirect", headers, None)


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
