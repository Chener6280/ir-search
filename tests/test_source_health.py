from ir_search.source_health import source_health


def test_source_health_exposes_mock_without_secret_values(monkeypatch):
    monkeypatch.setenv("IR_SEARCH_LIVE", "0")
    monkeypatch.setenv("EXA_API_KEY", "secret-value")

    payload = source_health()

    assert payload["sources"]["cninfo"]["adapter_mode"] == "mock"
    assert payload["sources"]["cninfo"]["ok"] is False
    assert payload["sources"]["cninfo"]["availability_reason"] == "live_disabled"
    assert payload["env"]["has_EXA_API_KEY"] is True
    assert "secret-value" not in str(payload)


def test_source_health_distinguishes_key_missing_from_mock(monkeypatch):
    monkeypatch.setenv("IR_SEARCH_LIVE", "1")
    monkeypatch.delenv("BOCHA_API_KEY", raising=False)
    monkeypatch.delenv("WECHAT_OPENCLI_COMMAND", raising=False)

    payload = source_health()

    assert payload["sources"]["bocha"]["availability_reason"] == "key_missing"
    assert "key_missing" in payload["sources"]["bocha"]["diagnostics"]["reasons"]
    assert payload["sources"]["wechat_opencli"]["availability_reason"] == "command_missing"
    assert payload["sources"]["company_ir"]["availability_reason"] == "adapter_mock"


def test_source_health_reports_live_enabled_key_without_leaking(monkeypatch):
    monkeypatch.setenv("IR_SEARCH_LIVE", "1")
    monkeypatch.setenv("TAVILY_API_KEY", "tavily_test_value")

    payload = source_health()

    assert payload["env"]["has_TAVILY_API_KEY"] is True
    assert payload["sources"]["tavily"]["availability_reason"] == "available"
    assert "tavily_test_value" not in str(payload)
