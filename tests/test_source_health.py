from ir_search.source_health import source_health


def test_source_health_exposes_mock_without_secret_values(monkeypatch):
    monkeypatch.setenv("IR_SEARCH_LIVE", "0")
    monkeypatch.setenv("EXA_API_KEY", "secret-value")

    payload = source_health()

    assert payload["sources"]["cninfo"]["adapter_mode"] == "mock"
    assert payload["sources"]["cninfo"]["ok"] is False
    assert payload["env"]["has_EXA_API_KEY"] is True
    assert "secret-value" not in str(payload)
