"""Timestamp semantics of macro_series: period end, period average, declared release lag.

No network: the adapter's transport is a stub. The reviewed declarations of the packaged catalog are
pinned here on purpose, so that changing one is a deliberate act that also changes this file.
"""
from datetime import date, datetime, timezone
import json

import pytest

from ir_search import DataRegistry, DataRequest, get_data, list_capabilities
from ir_search.adapters import global_macro as macro
from ir_search.adapters.global_macro import GlobalMacroAdapter, _catalog, _declaration, _period_end
from ir_search.contracts.expanded_data import MACRO_DATASETS
from ir_search.infrastructure.public_web import _Reply
from ir_search.mcp_server import get_data_payload

NOW = datetime(2026, 9, 20, tzinfo=timezone.utc)
ECB = "ECB.EXR.D.USD.EUR.SP00.A"
BODIES = {
    "id=FEDFUNDS": b"observation_date,FEDFUNDS\n2024-02-01,5.33\n2025-02-01,4.33\n",
    "id=CPIAUCSL": b"observation_date,CPIAUCSL\n2024-02-01,311.0\n",
    "id=UNRATE": b"observation_date,UNRATE\n2024-02-01,3.9\n",
    "id=DGS10": b"observation_date,DGS10\n2024-02-29,4.25\n",
    "id=GDPC1": b"observation_date,GDPC1\n2024-01-01,22758\n2024-10-01,23542\n",
    "worldbank": json.dumps([dict(pages=1, total=1, lastupdated="2026-09-01"), [dict(
        indicator={"id": "NY.GDP.MKTP.KD.ZG"}, countryiso3code="USA", date="2024", value=2.8)]]).encode(),
    "EXR": b"KEY,TIME_PERIOD,OBS_VALUE,UNIT,UNIT_MULT,OBS_STATUS\nEXR.D.USD.EUR.SP00.A,2024-02-29,1.08,USD,0,A\n",
}


def registry():
    def transport(url, **kwargs):
        return _Reply(200, "text/csv", "", next(body for fragment, body in BODIES.items() if fragment in url), NOW)
    made = DataRegistry()
    made.register(GlobalMacroAdapter(transport=transport))
    return made


def rows(*symbols, **changes):
    request = DataRequest(**dict(dict(dataset="macro_series", market="GLOBAL", symbols=list(symbols),
                                      start="2024-01-01", end="2025-12-31"), **changes))
    result = get_data(request, registry=registry())
    assert result.status.value == "ok", [d.code for d in result.diagnostics]
    return result


# --- period_end: calendar arithmetic on the declared frequency, only for a verified calendar ---

CALENDAR = dict(period_calendar="calendar")


@pytest.mark.parametrize("frequency,start,end", [
    ("daily", date(2024, 2, 29), date(2024, 2, 29)),
    ("monthly", date(2024, 2, 1), date(2024, 2, 29)),   # leap year
    ("monthly", date(2025, 2, 1), date(2025, 2, 28)),
    ("monthly", date(2024, 4, 1), date(2024, 4, 30)),
    ("monthly", date(2024, 12, 1), date(2024, 12, 31)),
    ("quarterly", date(2024, 1, 1), date(2024, 3, 31)),
    ("quarterly", date(2024, 4, 1), date(2024, 6, 30)),
    ("quarterly", date(2024, 10, 1), date(2024, 12, 31)),
    ("annual", date(2024, 1, 1), date(2024, 12, 31)),
])
def test_period_end_is_the_last_day_of_the_calendar_period(frequency, start, end):
    assert _period_end(start, dict(CALENDAR, frequency=frequency)) == end


@pytest.mark.parametrize("frequency,start", [("monthly", date(2024, 2, 15)), ("quarterly", date(2024, 2, 1)),
                                             ("quarterly", date(2024, 1, 2)), ("annual", date(2024, 7, 1)),
                                             ("weekly", date(2024, 1, 1)), ("unknown", date(2024, 1, 1))])
def test_no_period_end_is_guessed_for_a_date_that_is_not_a_canonical_period_start(frequency, start):
    assert _period_end(start, dict(CALENDAR, frequency=frequency)) is None


@pytest.mark.parametrize("frequency", ["daily", "monthly", "quarterly", "annual"])
def test_an_unverified_calendar_never_yields_a_period_end(frequency):
    assert _period_end(date(2024, 1, 1), dict(period_calendar="unverified", frequency=frequency)) is None


def test_rows_carry_the_period_end_and_leave_the_observation_date_alone():
    result = rows("FRED.FEDFUNDS", "FRED.GDPC1", "FRED.DGS10", ECB, "WB.USA.NY.GDP.MKTP.KD.ZG")
    got = {(r["symbol"], r["observation_date"]): r["period_end"] for r in result.records}
    assert got == {("FRED.FEDFUNDS", date(2024, 2, 1)): date(2024, 2, 29), ("FRED.FEDFUNDS", date(2025, 2, 1)): date(2025, 2, 28),
                   ("FRED.GDPC1", date(2024, 1, 1)): date(2024, 3, 31), ("FRED.GDPC1", date(2024, 10, 1)): date(2024, 12, 31),
                   ("FRED.DGS10", date(2024, 2, 29)): date(2024, 2, 29), (ECB, date(2024, 2, 29)): date(2024, 2, 29),
                   ("WB.USA.NY.GDP.MKTP.KD.ZG", date(2024, 1, 1)): None}  # fiscal-year labelling not verified
    definition = MACRO_DATASETS["macro_series"]
    assert definition.primary_key == ("symbol", "observation_date") and definition.date_field == "observation_date"
    # Where the per-series coverage facts exist (their own commit), they still speak of observation dates.
    coverage = {row["symbol"]: row for row in getattr(result, "series_coverage", [])}
    if coverage:
        assert coverage["FRED.GDPC1"]["date_field"] == "observation_date"
        assert (coverage["FRED.GDPC1"]["first_date"], coverage["FRED.GDPC1"]["last_date"]) == (date(2024, 1, 1), date(2024, 10, 1))


# --- is_period_average: three states, and not knowing is not "no" ---

def test_the_three_states_pass_through_and_unknown_is_not_false():
    result = rows("FRED.FEDFUNDS", "FRED.CPIAUCSL", "FRED.UNRATE")
    got = {r["symbol"]: r["is_period_average"] for r in result.records}
    assert got["FRED.FEDFUNDS"] is True and got["FRED.CPIAUCSL"] is False
    assert got["FRED.UNRATE"] is None and got["FRED.UNRATE"] is not False
    sent = {r["symbol"]: r for r in result.to_dict()["records"]}
    assert "is_period_average" in sent["FRED.UNRATE"] and sent["FRED.UNRATE"]["is_period_average"] is None
    assert json.dumps(sent["FRED.UNRATE"]).count('"is_period_average": null') == 1


# --- release_lag_days: a declared convention that never becomes a date ---

def test_the_declared_lag_is_passed_on_and_everything_else_is_empty():
    result = rows("FRED.GDPC1", "FRED.CPIAUCSL", "FRED.FEDFUNDS", "WB.USA.NY.GDP.MKTP.KD.ZG")
    got = {r["symbol"]: r["release_lag_days"] for r in result.records}
    assert got == {"FRED.GDPC1": 75, "FRED.CPIAUCSL": None, "FRED.FEDFUNDS": None, "WB.USA.NY.GDP.MKTP.KD.ZG": None}


def test_the_lag_never_moves_a_date_and_never_serves_as_of(monkeypatch):
    before = rows("FRED.GDPC1").to_dict()["records"]
    original = macro._catalog
    monkeypatch.setattr(macro, "_catalog", lambda: {key: dict(row, release_lag_days=0 if row["release_lag_days"] else None)
                                                    for key, row in original().items()})
    after = rows("FRED.GDPC1").to_dict()["records"]
    assert [r["release_lag_days"] for r in before] == [75, 75] and [r["release_lag_days"] for r in after] == [0, 0]
    strip = lambda records: [{k: v for k, v in r.items() if k != "release_lag_days"} for r in records]
    assert strip(before) == strip(after)  # no date, and nothing else, depends on the lag
    definition = MACRO_DATASETS["macro_series"]
    assert {f.name for f in definition.fields if f.dtype in {"date", "datetime"}} == {"observation_date", "source_updated_on", "period_end"}
    assert "available_at" not in {f.name for f in definition.fields}
    capability = GlobalMacroAdapter().capabilities[0]
    assert capability.supports_as_of is False
    refused = get_data(DataRequest("macro_series", market="GLOBAL", symbols=["FRED.GDPC1"], start="2024-01-01", end="2024-12-31",
                                   as_of="2024-06-01T00:00:00Z"), registry=registry())
    assert not refused.records and "as_of_not_supported" in [d.code for d in refused.diagnostics]


# --- the catalog: a closed vocabulary, and every declaration has its source and the day it was checked ---

def declared(**changes):
    row = dict(frequency="monthly", is_period_average="true", average_of="daily", period_calendar="calendar",
               semantics_source="https://example.org/series", semantics_checked_on="2026-09-20", release_lag_days="",
               release_lag_source="", release_lag_checked_on="", semantics_note="")
    row.update(changes)
    return row


def test_a_well_formed_declaration_is_typed():
    assert _declaration(declared())["is_period_average"] is True
    assert _declaration(declared(is_period_average="false", average_of=""))["is_period_average"] is False
    nothing = _declaration(declared(is_period_average="", average_of="", period_calendar="unverified", semantics_source="", semantics_checked_on=""))
    assert nothing["is_period_average"] is None and nothing["release_lag_days"] is None
    lag = _declaration(declared(release_lag_days="75", release_lag_source="a review note", release_lag_checked_on="2026-09-20"))
    assert lag["release_lag_days"] == 75 and type(lag["release_lag_days"]) is int


@pytest.mark.parametrize("changes", [
    dict(is_period_average="TRUE"), dict(is_period_average="1"), dict(is_period_average="yes"), dict(is_period_average="unknown"),
    dict(period_calendar="fiscal"), dict(period_calendar=""),
    dict(release_lag_days="-1", release_lag_source="x", release_lag_checked_on="2026-09-20"),
    dict(release_lag_days="7.5", release_lag_source="x", release_lag_checked_on="2026-09-20"),
    dict(release_lag_days="about 75", release_lag_source="x", release_lag_checked_on="2026-09-20"),
    dict(semantics_source=""), dict(semantics_source="   "), dict(semantics_checked_on=""), dict(semantics_checked_on="20 Sep 2026"),
    dict(release_lag_days="75"),                                             # a lag without its source
    dict(release_lag_days="75", release_lag_source="x"),                     # and without the day it was checked
    dict(release_lag_source="x", release_lag_checked_on="2026-09-20"),       # a source without a lag
    dict(is_period_average="", average_of="", period_calendar="unverified"),  # a source without any declaration
    dict(average_of=""),                                                     # an average that does not say of what
    dict(average_of="monthly"), dict(average_of="quarterly"), dict(average_of="hourly"),  # not a higher frequency
    dict(is_period_average="false"),                                         # not an average, yet names one
    dict(is_period_average="", period_calendar="calendar"),                  # unknown, yet names one
], ids=lambda changes: ",".join(f"{k}={v}" for k, v in changes.items())[:60])
def test_the_catalog_refuses_anything_outside_its_closed_vocabulary(changes):
    with pytest.raises(ValueError):
        _declaration(declared(**changes))


def test_a_catalog_column_that_is_missing_is_refused():
    row = declared()
    del row["semantics_note"]
    with pytest.raises(ValueError):
        _declaration(row)


# --- the reviewed declarations, pinned ---

REVIEWED = {  # (is_period_average, average_of, period_calendar, release_lag_days)
    ("FRED", "CPIAUCSL"): (False, "", "calendar", None),
    ("FRED", "UNRATE"): (None, "", "calendar", None),
    ("FRED", "FEDFUNDS"): (True, "daily", "calendar", None),
    ("FRED", "DGS10"): (False, "", "calendar", None),
    ("FRED", "DGS2"): (False, "", "calendar", None),
    ("FRED", "GDPC1"): (False, "", "calendar", 75),
    ("WB", "NY.GDP.MKTP.KD.ZG"): (None, "", "unverified", None),
    ("WB", "NY.GDP.MKTP.CD"): (None, "", "unverified", None),
    ("WB", "FP.CPI.TOTL.ZG"): (None, "", "unverified", None),
    ("WB", "SL.UEM.TOTL.ZS"): (None, "", "unverified", None),
    ("WB", "NE.EXP.GNFS.ZS"): (None, "", "unverified", None),
}


def test_the_packaged_declarations_are_the_reviewed_ones():
    catalog = _catalog()
    assert {key: (row["is_period_average"], row["average_of"], row["period_calendar"], row["release_lag_days"])
            for key, row in catalog.items()} == REVIEWED
    for key, row in catalog.items():
        assert row["semantics_note"].strip(), key  # every row says what was verified and what was not
        if row["is_period_average"] is not None:
            assert row["semantics_source"].startswith("https://") and row["semantics_checked_on"]
    lagged = catalog["FRED", "GDPC1"]
    assert lagged["release_lag_source"] == "评审文档 2.2（旧系统反推的约定）" and lagged["release_lag_checked_on"] == "2026-09-20"
    ecb = macro._ECB_EXR_DAILY
    assert (ecb["is_period_average"], ecb["period_calendar"], ecb["release_lag_days"]) == (False, "calendar", None)


def test_a_monthly_index_measured_once_a_month_is_not_a_period_average_and_the_catalog_says_why():
    """The criterion: a time average of higher-frequency observations of the same quantity. There is no daily CPI."""
    cpi = _catalog()["FRED", "CPIAUCSL"]
    assert cpi["is_period_average"] is False and cpi["average_of"] == ""
    assert "构造歧义" in cpi["semantics_note"] and "跨品类" in cpi["semantics_note"]
    rule = macro.SEMANTICS_RULES["is_period_average"]
    assert "更高频" in rule and "时间平均" in rule and "average_of" in rule and "不等于 false" in rule


# --- the contract and the listing ---

def wrap(monkeypatch, change):
    original = GlobalMacroAdapter.query_data
    def query_data(self, request, *, context):
        page = original(self, request, context=context)
        for row in page.records:
            change(row)
        return page
    monkeypatch.setattr(GlobalMacroAdapter, "query_data", query_data)


@pytest.mark.parametrize("value", [0, 1, "true", "false", "", 1.0])
def test_the_service_accepts_only_a_real_boolean_or_null(monkeypatch, value):
    wrap(monkeypatch, lambda row: row.update(is_period_average=value))
    result = get_data(DataRequest("macro_series", market="GLOBAL", symbols=["FRED.FEDFUNDS"], start="2024-01-01", end="2025-12-31"),
                      registry=registry())
    assert result.status.value == "error" and not result.records
    assert "invalid_provider_response" in [d.code for d in result.diagnostics]


def test_sdk_and_mcp_return_the_same_three_fields():
    arguments = dict(dataset="macro_series", market="GLOBAL", symbols=["FRED.GDPC1", "FRED.UNRATE"], start="2024-01-01", end="2025-12-31")
    sdk = get_data(DataRequest(**arguments), registry=registry()).to_dict()["records"]
    mcp = get_data_payload(arguments, registry=registry())["records"]
    assert mcp == sdk
    assert [(r["period_end"], r["is_period_average"], r["release_lag_days"]) for r in mcp] == [
        ("2024-03-31", False, 75), ("2024-12-31", False, 75), ("2024-02-29", None, None)]


def test_field_selection_works_for_the_new_fields():
    result = rows("FRED.GDPC1", fields=["value", "period_end", "is_period_average"])
    assert set(result.records[0]) == {"symbol", "observation_date", "value", "period_end", "is_period_average"}


def test_the_catalog_listing_carries_the_rules_the_sources_and_the_day_they_were_checked():
    listing = list_capabilities()["macro_series_catalog"]
    assert listing["semantics_rules"] == macro.SEMANTICS_RULES and len(listing["series"]) == 11
    fedfunds = next(row for row in listing["series"] if row["series_id"] == "FEDFUNDS")
    assert fedfunds["is_period_average"] is True and fedfunds["average_of"] == "daily"
    assert fedfunds["semantics_source"] == "https://fred.stlouisfed.org/series/FEDFUNDS" and fedfunds["semantics_checked_on"] == "2026-09-20"
    (pattern,) = listing["pattern_series"]
    assert pattern["symbol_pattern"] == "ECB.EXR.D.<CCY>.EUR.SP00.A" and pattern["is_period_average"] is False
    assert pattern["semantics_source"].startswith("https://www.ecb.europa.eu/") and pattern["semantics_checked_on"] == "2026-09-20"
    json.dumps(listing, ensure_ascii=False)
