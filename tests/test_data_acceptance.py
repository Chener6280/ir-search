from dataclasses import replace
from datetime import date
import json

import pytest

from ir_search import DataRegistry, DataRequest, DataAdapterError, get_data
from ir_search.acceptance import (AcceptanceProbe, default_acceptance_probes, data_coverage,
                                  assess_data_quality, run_data_acceptance, main)
from test_framework_data import Upstream


def probe():
    return AcceptanceProbe("closed_sample", DataRequest("prices_daily", symbols=("000001.SZ",),
            start="2026-09-01", end="2026-09-02", fields=("close",), provider="wind_mysql"))


def setup(monkeypatch, source=None):
    source = source or Upstream("wind_mysql")
    registry = DataRegistry(use_source_policy=True)
    registry.register(source)
    monkeypatch.setattr("ir_search.acceptance.build_data_registry", lambda **kw: registry)
    return source, registry


def test_probe_validation_and_weekend_reference_is_not_a_calendar_claim():
    values = default_acceptance_probes(day=date(2026, 9, 14))
    assert all(p.request.end == date(2026, 9, 11) for p in values if p.request.dataset != 'financial_statements')
    assert all(p.request.end == date(2025, 12, 31) and p.request.symbols for p in values if p.request.dataset == 'financial_statements')
    assert len({p.name for p in values}) == len(values)
    assert any(p.request.symbols == ("IF2609",) for p in values)
    assert any(p.request.dataset == "options_daily" for p in values)
    json.dumps(values[0].to_dict())
    with pytest.raises(ValueError): AcceptanceProbe("not safe", probe().request)
    with pytest.raises(ValueError): AcceptanceProbe("sample", "bad")
    with pytest.raises(ValueError): default_acceptance_probes(day="2026-09-14")


def test_known_dataset_without_eligible_contract_is_inventoried_not_queried(monkeypatch):
    source, _ = setup(monkeypatch)
    report = run_data_acceptance([AcceptanceProbe('option_coverage', DataRequest('options_daily', market='CN_OPTIONS'))], live=True)
    assert report['probes'][0]['outcome'] == 'sample_parameters_required'
    assert not source.calls and not report['sample_checks_passed']


def test_offline_coverage_and_default_cli_do_not_contact_providers(monkeypatch, capsys):
    source, registry = setup(monkeypatch)
    coverage = data_coverage(registry=registry)
    assert all(not r["live_verified"] for r in coverage["coverage"])
    assert next(r for r in coverage["coverage"] if r["dataset"] == "financial_statements")["dataset_defined"] is True
    assert next(r for r in coverage["coverage"] if r["dataset"] == "derivatives_daily")["dataset_defined"] is False
    assert run_data_acceptance([probe()])["probes"][0]["outcome"] == "not_run"
    assert main([]) == 0 and not source.calls
    assert json.loads(capsys.readouterr().out)["live_requested"] is False


def test_live_repeatability_reports_metadata_without_raw_rows_or_secret_values(monkeypatch):
    source, _ = setup(monkeypatch)
    report = run_data_acceptance([probe()], live=True)
    item = report["probes"][0]
    assert item["repeatability"] == "identical" and len(source.calls) == 2
    assert report["sample_checks_passed"] and not report["full_acceptance_passed"]
    attempt = item["attempts"][0]
    assert attempt["quality"]["row_count"] == 2 and attempt["quality"]["publication_delay"] == "unverified"
    payload = json.dumps(report)
    assert '"records"' not in payload and "11.001" not in payload and "must_not_escape" not in payload


def test_changed_snapshot_and_all_null_fields_do_not_pass(monkeypatch):
    source, _ = setup(monkeypatch)
    source.on_call = lambda: source.page.records[0].update(close=10 + len(source.calls) / 10)
    report = run_data_acceptance([probe()], live=True)
    assert report["probes"][0]["repeatability"] == "changed_requires_review"
    assert not report["sample_checks_passed"]
    source.on_call = lambda: [r.update(close=None) for r in source.page.records]
    report = run_data_acceptance([probe()], live=True)
    assert report["probes"][0]["outcome"] == "sample_quality_failed" and not report["sample_checks_passed"]


def test_fatal_error_is_sanitized_and_circuit_stops_further_connections(monkeypatch):
    source, _ = setup(monkeypatch)
    source.error = DataAdapterError("tls_not_supported")
    report = run_data_acceptance([probe(), replace(probe(), name="second_query")], live=True)
    assert len(source.calls) == 1
    assert report["probes"][1]["attempts"][0]["diagnostics"] == ["provider_circuit_open", "tls_not_supported"]
    source.error = RuntimeError("must_not_escape")
    assert "must_not_escape" not in json.dumps(run_data_acceptance([probe()], live=True))


def test_quality_reports_unseen_symbols_nulls_dates_and_only_observed_coverage():
    source = Upstream()
    registry = DataRegistry()
    registry.register(source)
    request = replace(probe().request, provider=None, symbols=("000001.SZ", "600519.SH"))
    quality = assess_data_quality(get_data(request, registry=registry))
    assert quality["unseen_symbols"] == ["600519.SH"]
    assert quality["observed_start"] == "2026-09-01" and quality["observed_end"] == "2026-09-02"
    assert quality["null_counts"]["close"] == 0
    with pytest.raises(ValueError): assess_data_quality({})


@pytest.mark.parametrize("kw", [dict(live=1), dict(repeats=0), dict(repeats=4), dict(timeout_seconds=0),
                               dict(total_timeout_seconds=float("inf")), dict(probes=[]), dict(probes=[probe(), probe()])])
def test_bad_acceptance_settings_never_run(kw):
    with pytest.raises(ValueError): run_data_acceptance(**kw)


def test_report_cli_refuses_to_overwrite_private_file(tmp_path, capsys):
    path = tmp_path / "credentials.env"
    path.write_text("secret sentinel")
    assert main(["--output", str(path)]) == 1
    assert path.read_text() == "secret sentinel"
    assert "sentinel" not in capsys.readouterr().out
    output = tmp_path / "report.json"
    assert main(["--output", str(output)]) == 0
    assert json.loads(output.read_text())["full_acceptance_passed"] is False


@pytest.mark.parametrize("change", [dict(high=8), dict(low=13), dict(open=15), dict(close=8), dict(volume=-1), dict(amount=-1)])
def test_data_service_rejects_inconsistent_ohlc_or_negative_activity(change):
    source = Upstream()
    source.page.records[0].update(change)
    registry = DataRegistry()
    registry.register(source)
    result = get_data(replace(probe().request, provider=None, fields=()), registry=registry)
    assert result.status.value == "error" and not result.records


def test_missing_config_placeholder_rejected_without_echoing_value():
    from ir_search.infrastructure.credentials import mysql_profile, SourceConfigError
    from test_source_credentials import values
    for value in ("未配置", "${WIND_DB_PASSWORD}", "$JYDB_PASSWORD"):
        config = values()
        config["WIND_MYSQL_PASSWORD"] = value
        with pytest.raises(SourceConfigError, match="source_credentials_missing"):
            mysql_profile("wind_mysql", values=config)


def test_halted_database_quotes_keep_zero_activity_and_expose_missing_prices():
    from test_wind_mysql_adapter import run as run_wind, price as wind_price
    from test_jydb_market_adapter import Database
    halted = dict(wind_price(), open=0, high=0, low=0, volume=0, amount=0)
    wind, _ = run_wind([halted])
    db = Database()
    db.rows["QT_DailyQuote"][0].update(open=0, high=0, low=0, volume=0, amount=0)
    jydb = db.run()
    for result in (wind, jydb):
        assert result.status.value == "ok" and result.records[0]["high"] is None
        assert result.records[0]["volume"] == 0 and result.records[0]["close"] > 0
        assert "zero_source_price_replaced_with_null" in [d.code for d in result.diagnostics]
