from ir_search.source_health import source_health


def test_source_health_exposes_mock_without_secret_values(monkeypatch):
    monkeypatch.setenv("IR_SEARCH_LIVE", "0")
    monkeypatch.setenv("EXA_API_KEY", "secret-value")

    payload = source_health()

    assert payload["sources"]["cninfo"]["adapter_mode"] == "mock"
    assert payload["sources"]["cninfo"]["ok"] is False
    assert payload["sources"]["cninfo"]["availability_reason"] == "live_disabled"
    assert "adapter_mock" in payload["sources"]["cninfo"]["diagnostics"]["reasons"]
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


def test_source_health_reports_keyed_experimental_interface_as_available(monkeypatch):
    monkeypatch.setenv("IR_SEARCH_LIVE", "1")
    monkeypatch.setenv("ANYSEARCH_API_KEY", "anysearch_test_value")

    payload = source_health()

    assert payload["env"]["has_ANYSEARCH_API_KEY"] is True
    assert payload["sources"]["anysearch"]["ok"] is True
    assert payload["sources"]["anysearch"]["availability_reason"] == "available_experimental"
    assert "experimental_adapter" in payload["sources"]["anysearch"]["diagnostics"]["reasons"]
    assert "anysearch_test_value" not in str(payload)


def test_source_health_reports_dajiala_accounts_file_gap(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("IR_SEARCH_LIVE", "1")
    monkeypatch.setenv("DAJIALA_KEY", "dajiala_test_value")
    monkeypatch.delenv("DAJIALA_ACCOUNTS_PATH", raising=False)
    monkeypatch.delenv("WECHAT_ACCOUNTS_PATH", raising=False)
    monkeypatch.delenv("IR_SEARCH_PATH", raising=False)

    missing_payload = source_health()

    assert missing_payload["sources"]["dajiala"]["ok"] is False
    assert missing_payload["sources"]["dajiala"]["availability_reason"] == "path_missing"
    assert missing_payload["env"]["has_DAJIALA_ACCOUNTS_FILE"] is False
    assert "dajiala_test_value" not in str(missing_payload)

    accounts = tmp_path / "accounts.json"
    accounts.write_text("{}", encoding="utf-8")

    available_payload = source_health()

    assert available_payload["sources"]["dajiala"]["ok"] is True
    assert available_payload["sources"]["dajiala"]["availability_reason"] == "available"
    assert available_payload["env"]["has_DAJIALA_ACCOUNTS_FILE"] is True


def test_source_health_extended_env_booleans_do_not_leak(monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "tushare_test_value")
    monkeypatch.setenv("LONGBRIDGE_CLI_COMMAND", "longbridge_test_command")
    monkeypatch.setenv("ZSXQ_CLI_COMMAND", "zsxq_test_command")
    monkeypatch.setenv("WEWE_RSS_BASE", "http://example.invalid")

    payload = source_health()

    assert payload["env"]["has_TUSHARE_TOKEN"] is True
    assert payload["env"]["has_LONGBRIDGE_CLI_COMMAND"] is True
    assert payload["env"]["has_ZSXQ_CLI_COMMAND"] is True
    assert payload["env"]["has_WEWE_RSS_BASE"] is True
    assert "tushare_test_value" not in str(payload)
    assert "longbridge_test_command" not in str(payload)
    assert "zsxq_test_command" not in str(payload)
