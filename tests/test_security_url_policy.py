import pytest

from ir_search.documents.safety import UrlBlockedError, ensure_url_allowed, is_url_allowed


def test_public_http_and_https_are_allowed():
    assert is_url_allowed("https://example.com").allowed is True
    assert is_url_allowed("http://example.com").allowed is True


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/file",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://10.0.0.1",
        "http://172.16.0.10",
        "http://192.168.1.1",
        "http://169.254.0.1",
        "http://[::1]:8000",
        "http://[fc00::1]",
    ],
)
def test_private_or_unsafe_urls_are_blocked(url):
    assert is_url_allowed(url).allowed is False
    with pytest.raises(UrlBlockedError):
        ensure_url_allowed(url)


def test_private_network_override_is_explicit():
    result = ensure_url_allowed("http://127.0.0.1:8000", allow_private_network=True)

    assert result.allowed is True
    assert "override" in result.reason
