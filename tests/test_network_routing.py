from ir_search.adapters.bocha import bocha_network_options
from ir_search.adapters.exa import exa_network_options
from ir_search.network import apply_auto_proxy_settings, choose_proxy


def test_bocha_disables_system_proxy_by_default(monkeypatch):
    monkeypatch.delenv("BOCHA_PROXY", raising=False)
    monkeypatch.delenv("BOCHA_DISABLE_SYSTEM_PROXY", raising=False)

    assert bocha_network_options() == {"proxy_url": None, "disable_proxy": True}


def test_bocha_can_use_china_proxy(monkeypatch):
    monkeypatch.setenv("BOCHA_PROXY", "http://cn-proxy.example:8080")

    assert bocha_network_options() == {
        "proxy_url": "http://cn-proxy.example:8080",
        "disable_proxy": False,
    }


def test_exa_uses_explicit_proxy_without_disabling_system_proxy(monkeypatch):
    monkeypatch.setenv("EXA_PROXY", "http://127.0.0.1:7890")
    monkeypatch.delenv("EXA_DISABLE_SYSTEM_PROXY", raising=False)

    assert exa_network_options() == {
        "proxy_url": "http://127.0.0.1:7890",
        "disable_proxy": False,
    }


def test_exa_can_disable_system_proxy_if_requested(monkeypatch):
    monkeypatch.delenv("EXA_PROXY", raising=False)
    monkeypatch.setenv("EXA_DISABLE_SYSTEM_PROXY", "1")

    assert exa_network_options() == {"proxy_url": None, "disable_proxy": True}


def test_choose_proxy_prefers_https():
    assert choose_proxy({"http": "http://h:8080", "https": "http://s:8080"}) == "http://s:8080"


def test_auto_proxy_fills_exa_from_system_proxy(monkeypatch):
    for name in [
        "IR_SEARCH_AUTO_PROXY",
        "BOCHA_PROXY",
        "EXA_PROXY",
        "EXA_DISABLE_SYSTEM_PROXY",
        "IR_SEARCH_OVERSEAS_PROXY",
        "OVERSEAS_PROXY",
    ]:
        monkeypatch.delenv(name, raising=False)

    result = apply_auto_proxy_settings({"https": "http://127.0.0.1:7890"})

    assert result["EXA_PROXY"] == "http://127.0.0.1:7890"
    assert result["BOCHA_PROXY"] is None


def test_auto_proxy_fills_bocha_from_cn_alias(monkeypatch):
    for name in ["IR_SEARCH_AUTO_PROXY", "BOCHA_PROXY", "EXA_PROXY", "IR_SEARCH_CN_PROXY", "CN_PROXY"]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("IR_SEARCH_CN_PROXY", "http://cn-proxy.example:8080")

    result = apply_auto_proxy_settings({})

    assert result["BOCHA_PROXY"] == "http://cn-proxy.example:8080"


def test_auto_proxy_can_fill_bocha_from_system_when_enabled(monkeypatch):
    for name in ["IR_SEARCH_AUTO_PROXY", "BOCHA_PROXY", "EXA_PROXY", "BOCHA_AUTO_PROXY"]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BOCHA_AUTO_PROXY", "1")

    result = apply_auto_proxy_settings({"https": "http://rule-proxy.local:7890"})

    assert result["BOCHA_PROXY"] == "http://rule-proxy.local:7890"


def test_auto_proxy_does_not_override_manual_adapter_proxy(monkeypatch):
    monkeypatch.setenv("BOCHA_PROXY", "http://manual-cn:8080")
    monkeypatch.setenv("EXA_PROXY", "http://manual-overseas:8080")

    result = apply_auto_proxy_settings({"https": "http://system:8080"})

    assert result["BOCHA_PROXY"] == "http://manual-cn:8080"
    assert result["EXA_PROXY"] == "http://manual-overseas:8080"
