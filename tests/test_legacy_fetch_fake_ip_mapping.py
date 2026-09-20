"""The legacy fetcher reports a fake-ip refusal as a policy block, like any other refused address.

No real network: the transport is replaced, or DNS is synthetic and connecting is forbidden.
"""
import socket

import pytest

from ir_search.documents import fetcher
from ir_search.infrastructure import public_web
from ir_search.models import FailureKind
from ir_search.pipeline import failure_kind_for_error
from ir_search.registry import DataAdapterError

URL = "https://example.org/a"


def refuse(monkeypatch, code):
    def request(url, **kwargs):
        raise DataAdapterError(code)
    monkeypatch.setattr(public_web, "_request", request)


@pytest.mark.parametrize("code", ["blocked_url", "fake_ip_blocked"])
def test_both_address_refusals_are_policy_blocks(monkeypatch, code):
    refuse(monkeypatch, code)
    document = fetcher.fetch_document(URL)
    assert len(document.errors) == 1 and document.errors[0].startswith("blocked_by_policy: ")
    assert "fetch failed" not in document.errors[0] and not document.text
    assert failure_kind_for_error(document.errors[0]) is FailureKind.BLOCKED_BY_POLICY


def test_the_fake_ip_refusal_keeps_its_cause_and_the_generic_one_its_wording(monkeypatch):
    refuse(monkeypatch, "fake_ip_blocked")
    assert "fake_ip_blocked" in fetcher.fetch_document(URL).errors[0]
    refuse(monkeypatch, "blocked_url")
    assert fetcher.fetch_document(URL).errors == ["blocked_by_policy: blocked public URL or address"]


@pytest.mark.parametrize("code", ["network", "timeout", "tls_error", "source_config_error"])
def test_every_other_transport_failure_is_still_a_fetch_failure(monkeypatch, code):
    refuse(monkeypatch, code)
    assert fetcher.fetch_document(URL).errors == [f"fetch failed: {code}"]


def test_placeholder_answer_end_to_end_with_the_switch_off(monkeypatch):
    """The real transport and resolver run; only the DNS answer is synthetic."""
    placeholder = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.1.4", 443))
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [placeholder])
    monkeypatch.setattr(public_web, "_Connection", lambda *a, **kw: pytest.fail("a refused address is never connected to"))
    (error,) = fetcher.fetch_document(URL).errors
    assert error.startswith("blocked_by_policy: ") and "fake_ip_blocked" in error
    assert failure_kind_for_error(error) is FailureKind.BLOCKED_BY_POLICY
