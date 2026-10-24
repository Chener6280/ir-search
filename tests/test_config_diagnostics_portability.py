"""Configuration problems must say which key to fix, especially after moving computers."""
import json
import os

import pytest

from ir_search.infrastructure.alphapai import alphapai_profile
from ir_search.infrastructure.credentials import (
    SourceConfigError, _absolute_elsewhere, mysql_profile, require_local_path, source_configuration_status,
)
from ir_search.infrastructure.gangtise import gangtise_profile
from ir_search.services.source_diagnostics import diagnose_sources

FOREIGN = "/Users/someone/private" if os.name == "nt" else "C:/Users/someone/private"


def env_file(tmp_path, text):
    path = tmp_path / "credentials.env"
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)
    return path


def test_error_keys_are_env_names_and_never_values():
    assert SourceConfigError("x", "WIND_MYSQL_HOST").key == "WIND_MYSQL_HOST"
    assert SourceConfigError("x", "secret-value/with=chars").key is None and SourceConfigError("x").key is None


def test_path_copied_from_another_operating_system_names_its_key():
    assert _absolute_elsewhere(FOREIGN) and not _absolute_elsewhere("~/private") and not _absolute_elsewhere(".local/cache")
    with pytest.raises(SourceConfigError) as error:
        require_local_path({"A_DIR": "~/ok", "B_DIR": FOREIGN}, "A_DIR", "B_DIR")
    assert (error.value.code, error.value.key) == ("path_not_absolute_on_this_platform", "B_DIR")
    require_local_path({"A_DIR": ".local/cache"}, "A_DIR", allow_relative=True)
    with pytest.raises(SourceConfigError):
        require_local_path({"A_DIR": FOREIGN}, "A_DIR", allow_relative=True)
    with pytest.raises(SourceConfigError):
        require_local_path({"A_DIR": ".local/cache"}, "A_DIR")


@pytest.mark.parametrize("factory,values,key", [
    (alphapai_profile, {"ALPHAPAI_MATERIALS_ENABLED": "true", "ALPHA_PIE_PHONE": "13800000000",
                        "ALPHA_PIE_PWD": "must_not_escape", "ALPHAPAI_CACHE_DIR": FOREIGN}, "ALPHAPAI_CACHE_DIR"),
    (gangtise_profile, {"GANGTISE_MATERIALS_ENABLED": "true", "GANGTISE_PHONE": "13800000000",
                        "GANGTISE_PASSWORD": "must_not_escape", "GANGTISE_STATE_DIR": FOREIGN}, "GANGTISE_STATE_DIR"),
])
def test_account_sources_report_the_unusable_path_key(factory, values, key):
    with pytest.raises(SourceConfigError) as error:
        factory(values=values)
    assert (error.value.code, error.value.key) == ("path_not_absolute_on_this_platform", key)


def test_mysql_profile_names_the_missing_key():
    with pytest.raises(SourceConfigError) as error:
        mysql_profile("wind_mysql", values={"WIND_MYSQL_ENABLED": "true", "WIND_MYSQL_HOST": "db.example.test",
                                             "WIND_MYSQL_DATABASE": "wind", "WIND_MYSQL_USER": "reader"})
    assert (error.value.code, error.value.key) == ("source_credentials_missing", "WIND_MYSQL_PASSWORD")


def test_doctor_keeps_the_precise_reason_and_key_and_checks_the_ca_file(tmp_path):
    ca = tmp_path / "not-copied-to-this-computer.pem"
    path = env_file(tmp_path, "\n".join([
        "JYDB_MYSQL_ENABLED=true", "JYDB_MYSQL_HOST=db.example.test", "JYDB_MYSQL_DATABASE=jydb",
        "JYDB_MYSQL_USER=reader", "JYDB_MYSQL_PASSWORD=must_not_escape", "JYDB_MYSQL_TLS_MODE=pinned_ca",
        f"JYDB_MYSQL_SSL_CA={ca.as_posix()}", "JYDB_MYSQL_SSL_CA_SHA256=" + "a" * 64,
        "ALPHAPAI_MATERIALS_ENABLED=true", "ALPHA_PIE_PHONE=13800000000", "ALPHA_PIE_PWD=must_not_escape",
        f"ALPHAPAI_CACHE_DIR={FOREIGN}"]) + "\n")
    rows = {row["provider"]: row for row in source_configuration_status(env_file=path)["sources"]}
    assert (rows["jydb"]["code"], rows["jydb"]["key"], rows["jydb"]["configured"]) == ("ssl_ca_file_missing", "JYDB_MYSQL_SSL_CA", False)
    assert (rows["alphapai"]["code"], rows["alphapai"]["key"]) == ("path_not_absolute_on_this_platform", "ALPHAPAI_CACHE_DIR")

    report = diagnose_sources(["jydb", "alphapai"], env_file=path)
    found = {item["provider"]: item for item in report["sources"]}
    for provider, detail, key in (("jydb", "ssl_ca_file_missing", "JYDB_MYSQL_SSL_CA"),
                                  ("alphapai", "path_not_absolute_on_this_platform", "ALPHAPAI_CACHE_DIR")):
        assert found[provider]["state"] == "configuration_error"
        (diagnostic,) = found[provider]["diagnostics"]
        assert diagnostic["code"] == "source_config_error"  # stable for existing callers
        assert (diagnostic["detail_code"], diagnostic["key"]) == (detail, key)
    assert "must_not_escape" not in json.dumps(report, ensure_ascii=False) and FOREIGN not in json.dumps(report)

    ca.write_text("placeholder", encoding="ascii")
    rows = {row["provider"]: row for row in source_configuration_status(env_file=path)["sources"]}
    assert rows["jydb"]["configured"] and "code" not in rows["jydb"]
