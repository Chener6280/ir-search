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


@pytest.mark.parametrize("url", [
    "http://2130706433/",            # decimal 127.0.0.1
    "http://0x7f000001/",            # hexadecimal
    "http://0177.0.0.1/",            # octal-looking dotted form
    "http://127.1/",                 # short dotted form
    "http://intranet/",              # single-label names only exist on private networks
    "http://wiki.corp/",
    "http://printer.local/",
    "http://metadata.internal/",
    "https://example.com:8443/",     # services on odd ports are not public web documents
    "http://example.com:18060/",
    "http://example.com:99999/",
])
def test_disguised_local_targets_and_odd_ports_are_blocked(url):
    assert is_url_allowed(url).allowed is False


def test_default_ports_and_explicit_overrides_still_work():
    assert is_url_allowed("https://example.com:443/a").allowed and is_url_allowed("http://example.com:80/a").allowed
    assert is_url_allowed("http://intranet:8080/", allow_private_network=True).allowed
    assert is_url_allowed("http://wiki.corp/", allowlist=["wiki.corp"]).allowed


def test_public_looking_name_that_resolves_locally_is_refused_before_any_request():
    from ir_search.documents.safety import ensure_host_resolves_public

    def answers(*addresses):
        return lambda host, port, **kwargs: [(2, 1, 6, "", (address, port)) for address in addresses]

    ensure_host_resolves_public("https://example.com/a", resolver=answers("93.184.216.34"))
    for address in ("127.0.0.1", "10.1.2.3", "169.254.169.254", "100.64.0.1", "::1", "fe80::1%eth0"):
        with pytest.raises(UrlBlockedError):
            ensure_host_resolves_public("https://rebind.example.com/a", resolver=answers("93.184.216.34", address))

    def offline(host, port, **kwargs):
        raise OSError("no resolver")
    ensure_host_resolves_public("https://example.com/a", resolver=offline)  # the request itself will fail


def test_fetcher_checks_resolution_at_the_network_seam(monkeypatch):
    import urllib.error
    import urllib.request
    from ir_search.documents import fetcher

    from ir_search.infrastructure import public_web
    from ir_search.registry import DataAdapterError
    monkeypatch.setattr(public_web, "_request",
                        lambda url, **kw: (_ for _ in ()).throw(DataAdapterError("blocked_url")))

    class Opener:
        def open(self, request, timeout):
            raise AssertionError("no request may be sent")

    request = urllib.request.Request("https://rebind.example.com/a")
    with pytest.raises(UrlBlockedError):
        fetcher._open_once(Opener(), request, 5)
    request.allow_private_network = True
    with pytest.raises(AssertionError):
        fetcher._open_once(Opener(), request, 5)
