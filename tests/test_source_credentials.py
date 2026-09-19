import json
import os
from dataclasses import replace
from decimal import Decimal

import pytest

from ir_search import build_data_registry, get_data, DataRequest
from ir_search.infrastructure.credentials import (
    SourceConfigError, credentials_path, read_credentials, mysql_profile, source_configuration_status,
)


def env_file(tmp_path, text):
    path = tmp_path / "credentials.env"
    path.write_text(text)
    path.chmod(0o600)
    return path


def values(provider="WIND"):
    return {f"{provider}_MYSQL_{key}": value for key, value in {
        "ENABLED": "true", "HOST": "private-host", "DATABASE": "private-db", "USER": "private-user",
        "PASSWORD": "must_not_escape", "VOLUME_MULTIPLIER": "100", "AMOUNT_MULTIPLIER": "1000",
    }.items()}


def test_literal_env_preserves_secret_characters_without_execution_or_interpolation(tmp_path, monkeypatch):
    path = env_file(tmp_path, "# private\nTOKEN='abc#$HOME`no_command`$(no_command)=x'\nexport EMPTY=\n")
    monkeypatch.setenv("IR_SEARCH_CREDENTIALS_FILE", str(path))
    before = dict(os.environ)
    assert credentials_path() == path
    assert read_credentials()["TOKEN"] == "abc#$HOME`no_command`$(no_command)=x"
    assert read_credentials(path)["EMPTY"] == ""
    assert dict(os.environ) == before
    assert credentials_path(tmp_path / "override") == tmp_path / "override"


@pytest.mark.parametrize("text", ["PASSWORD=must_not_escape\nPASSWORD=other", "PASSWORD='must_not_escape", "invalid_line_must_not_escape", "lower=must_not_escape", "KEY=bad\x00"])
def test_invalid_env_never_reflects_line_or_value(tmp_path, text):
    with pytest.raises(SourceConfigError) as caught:
        read_credentials(env_file(tmp_path, text))
    assert "must_not_escape" not in str(caught.value)


def test_missing_permissions_symlinks_and_config_status_are_safe(tmp_path, monkeypatch):
    with pytest.raises(SourceConfigError, match="credentials_file_missing"):
        read_credentials(tmp_path / "missing")
    path = env_file(tmp_path, "PASSWORD=must_not_escape")
    path.chmod(0o644)
    with pytest.raises(SourceConfigError, match="credentials_permissions_unsafe"):
        read_credentials(path)
    assert "must_not_escape" not in json.dumps(source_configuration_status(env_file=path))
    path.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(SourceConfigError):
        read_credentials(link)
    monkeypatch.setenv("IR_SEARCH_CREDENTIALS_FILE", str(tmp_path / "missing"))
    result = get_data(DataRequest("securities"))
    assert result.diagnostics[0].code == "credentials_file_missing"


def test_profiles_are_explicit_separate_and_repr_does_not_contain_credentials():
    assert mysql_profile("wind_mysql", values={}) is None
    profile = mysql_profile("wind_mysql", values=values())
    assert profile.volume_multiplier == Decimal(100)
    assert all(v not in repr(profile) for v in ("private-host", "private-user", "private-db", "must_not_escape"))
    assert mysql_profile("jydb", values=values()) is None
    bad = values()
    bad.pop("WIND_MYSQL_PASSWORD")
    with pytest.raises(SourceConfigError, match="source_credentials_missing"):
        mysql_profile("wind_mysql", values=bad)
    with pytest.raises(SourceConfigError):
        mysql_profile("other", values={})


@pytest.mark.parametrize("key, value", [("PORT", "0"), ("PORT", "must_not_escape"), ("ENABLED", "maybe"),
                                       ("VOLUME_MULTIPLIER", "NaN"), ("AMOUNT_MULTIPLIER", "-1"), ("TLS_MODE", "automatic"),
                                       ("TLS_MODE", "pinned_ca")])
def test_invalid_profile_configuration_is_rejected(key, value):
    config = values()
    config["WIND_MYSQL_" + key] = value
    with pytest.raises(SourceConfigError) as caught:
        mysql_profile("wind_mysql", values=config)
    assert "must_not_escape" not in str(caught.value)


def test_registry_and_status_load_enabled_file_without_connecting(tmp_path, monkeypatch):
    path = env_file(tmp_path, "\n".join(f"{k}={v}" for k,v in values().items()))
    monkeypatch.setattr("ir_search.infrastructure.mysql._connect", lambda *a: pytest.fail("Unexpected network"))
    registry = build_data_registry(env_file=path)
    assert {cap.dataset for cap,_ in registry.entries()} == {'securities','prices_daily','financial_statements',
        'futures_contracts','options_contracts','futures_daily','options_daily','trading_calendar','fund_profile','fund_nav','fund_shares','fund_holdings','fund_exchange_daily'}
    status = source_configuration_status(env_file=path)
    assert status["sources"][0]["configured"] and not status["sources"][0]["live_verified"]
    assert not status["sources"][1]["enabled"]
    assert "must_not_escape" not in json.dumps(status)
    path.write_text("WIND_MYSQL_ENABLED=true\n")
    assert build_data_registry(env_file=path).diagnostics[0].code == "source_credentials_missing"
    assert source_configuration_status(env_file=path)["sources"][0]["code"] == "source_credentials_missing"


def test_jydb_pinned_ca_requires_an_explicit_fingerprint():
    config = values("JYDB")
    config.update(JYDB_MYSQL_TLS_MODE="pinned_ca", JYDB_MYSQL_SSL_CA="/local/ca.pem")
    with pytest.raises(SourceConfigError, match="invalid_tls_config"):
        mysql_profile("jydb", values=config)
    config["JYDB_MYSQL_SSL_CA_SHA256"] = "a" * 64
    assert mysql_profile("jydb", values=config).tls_mode == "pinned_ca"


def test_non_tls_is_explicit_wind_only_and_visible_without_credentials(tmp_path):
    config = values()
    config['WIND_MYSQL_TLS_MODE'] = 'disabled'
    assert mysql_profile('wind_mysql', values=config).tls_mode == 'disabled'
    path = env_file(tmp_path, '\n'.join(f'{k}={v}' for k,v in config.items()))
    status = source_configuration_status(env_file=path)
    assert status['sources'][0]['transport_encrypted'] is False
    assert 'must_not_escape' not in json.dumps(status)
    config['WIND_MYSQL_SSL_CA'] = 'ca.pem'
    with pytest.raises(SourceConfigError):
        mysql_profile('wind_mysql', values=config)
    config = values('JYDB')
    config['JYDB_MYSQL_TLS_MODE'] = 'disabled'
    with pytest.raises(SourceConfigError):
        mysql_profile('jydb', values=config)


def test_symlink_rejected_even_when_platform_has_no_nofollow_flag(tmp_path, monkeypatch):
    path = env_file(tmp_path, "PRIVATE_KEY=must_not_escape\n")
    link = tmp_path / "link"
    link.symlink_to(path)
    monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)
    with pytest.raises(SourceConfigError, match="credentials_file_unreadable"):
        read_credentials(link)
