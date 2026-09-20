"""Per-series coverage facts of macro_series: what the source answered for the requested window.

No network: the adapter's transport is a stub that answers per URL.
"""
from datetime import date, datetime, timezone
import json

import pytest

from ir_search import DataRegistry, DataRequest, RequestContext, get_data
from ir_search.adapters.global_macro import GlobalMacroAdapter
from ir_search.contracts import DataPage
from ir_search.infrastructure.public_web import _Reply
from ir_search.mcp_server import get_data_payload
from ir_search.registry import DataAdapterError

NOW = datetime(2026, 9, 18, tzinfo=timezone.utc)
KEYS = {"symbol", "provider", "source_series_id", "frequency", "date_field", "requested_start", "requested_end",
        "status", "first_date", "last_date", "n_obs", "n_missing", "n_returned"}
DGS10 = b"observation_date,DGS10\n2025-01-02,4.57\n2025-01-03,.\n2025-01-06,4.62\n2025-01-07,4.67\n2025-01-08,.\n"
DGS2 = b"observation_date,DGS2\n2025-01-02,4.25\n2025-01-03,4.28\n"


def world_bank(rows, *, pages=1, total=None, indicator="NY.GDP.MKTP.KD.ZG", country="USA"):
    items = [dict(indicator={"id": indicator}, countryiso3code=country, date=year, value=value) for year, value in rows]
    return json.dumps([dict(pages=pages, total=len(items) if total is None else total, lastupdated="2026-09-01"), items]).encode()


def registry(answers):
    """answers: fragment of the URL -> body bytes, or an error code to raise for that series."""
    def transport(url, **kwargs):
        for fragment, answer in answers.items():
            if fragment in url:
                if isinstance(answer, str):
                    raise DataAdapterError(answer)
                return _Reply(200, "text/csv", "", answer, NOW)
        pytest.fail(f"unexpected request {url}")
    made = DataRegistry()
    made.register(GlobalMacroAdapter(transport=transport))
    return made


def ask(answers, symbols, start="2025-01-01", end="2025-01-31", **changes):
    request = DataRequest(**dict(dict(dataset="macro_series", market="GLOBAL", symbols=symbols, start=start, end=end), **changes))
    return get_data(request, registry=registry(answers))


def test_one_row_per_symbol_in_request_order_with_a_closed_shape():
    result = ask({"id=DGS2": DGS2, "id=DGS10": DGS10}, ["FRED.DGS2", "FRED.DGS10"])
    assert [row["symbol"] for row in result.series_coverage] == ["FRED.DGS2", "FRED.DGS10"]
    assert all(set(row) == KEYS for row in result.series_coverage)
    first = result.series_coverage[0]
    assert (first["provider"], first["source_series_id"], first["frequency"], first["date_field"]) == (
        "global_macro", "DGS2", "daily", "observation_date")
    assert (first["requested_start"], first["requested_end"]) == (date(2025, 1, 1), date(2025, 1, 31))


def test_valueless_rows_are_counted_apart_and_never_mark_the_first_or_last_date():
    row = ask({"id=DGS10": DGS10}, ["FRED.DGS10"]).series_coverage[0]
    assert (row["status"], row["first_date"], row["last_date"]) == ("ok", date(2025, 1, 2), date(2025, 1, 7))
    assert (row["n_obs"], row["n_missing"], row["n_returned"]) == (3, 2, 5)


def test_a_series_that_stops_early_or_starts_late_is_reported_without_judgement():
    result = ask({"id=DGS10": DGS10}, ["FRED.DGS10"], start="2024-06-01", end="2026-09-01")
    row = result.series_coverage[0]
    assert row["status"] == "ok" and row["first_date"] == date(2025, 1, 2) and row["last_date"] == date(2025, 1, 7)
    assert (row["requested_start"], row["requested_end"]) == (date(2024, 6, 1), date(2026, 9, 1))
    assert result.status.value == "ok" and result.complete  # the facts change nothing else
    assert not [d for d in result.diagnostics if "stale" in d.code or "coverage" in d.code]


def test_an_empty_window_is_empty_not_ok_and_keeps_the_existing_diagnostic():
    result = ask({"id=DGS10": b"observation_date,DGS10\n2025-01-03,.\n"}, ["FRED.DGS10"])
    row = result.series_coverage[0]
    assert (row["status"], row["first_date"], row["last_date"], row["n_obs"], row["n_missing"]) == ("empty", None, None, 0, 1)
    nothing = ask({"id=DGS10": b"observation_date,DGS10\n"}, ["FRED.DGS10"])
    assert nothing.series_coverage[0]["status"] == "empty" and nothing.series_coverage[0]["n_missing"] == 0
    assert "macro_series_no_observations" in [d.code for d in nothing.diagnostics]


def test_a_failed_series_has_a_code_and_no_facts_while_the_other_series_is_intact():
    result = ask({"id=DGS10": DGS10, "worldbank": "network"}, ["WB.USA.NY.GDP.MKTP.KD.ZG", "FRED.DGS10"])
    failed, fine = result.series_coverage
    assert set(failed) == KEYS | {"code"} and (failed["status"], failed["code"]) == ("failed", "network")
    assert all(failed[name] is None for name in ("first_date", "last_date", "n_obs", "n_missing")) and failed["n_returned"] == 0
    assert (fine["status"], fine["n_obs"], fine["n_returned"]) == ("ok", 3, 5)
    assert result.status.value == "partial" and len(result.records) == 5


def test_every_series_failing_is_still_an_error_without_coverage_rows():
    result = ask({"id=DGS10": "network"}, ["FRED.DGS10"])
    assert result.status.value == "error" and result.series_coverage == []


def test_truncated_means_the_source_itself_reported_more_rows_and_wins_over_empty():
    body = world_bank([("2024", 2.8)], pages=2)
    row = ask({"worldbank": body}, ["WB.USA.NY.GDP.MKTP.KD.ZG"], start="2020-01-01", end="2025-12-31").series_coverage[0]
    assert (row["status"], row["n_obs"], row["first_date"], row["frequency"]) == ("truncated", 1, date(2024, 1, 1), "annual")
    assert row["source_series_id"] == "NY.GDP.MKTP.KD.ZG"
    unread = ask({"worldbank": world_bank([], pages=2, total=9)}, ["WB.USA.NY.GDP.MKTP.KD.ZG"], start="2020-01-01", end="2025-12-31")
    assert unread.series_coverage[0]["status"] == "truncated" and unread.series_coverage[0]["n_obs"] == 0


def test_facts_are_taken_before_the_local_limit_cut():
    result = ask({"id=DGS2": DGS2, "id=DGS10": DGS10}, ["FRED.DGS2", "FRED.DGS10"], limit=3)
    assert len(result.records) == 3 and "macro_result_limit_narrow_dates" in [d.code for d in result.diagnostics]
    dgs2, dgs10 = result.series_coverage
    assert (dgs10["n_obs"], dgs10["n_missing"], dgs10["n_returned"], dgs10["last_date"]) == (3, 2, 3, date(2025, 1, 7))
    assert (dgs2["n_obs"], dgs2["n_missing"], dgs2["n_returned"]) == (2, 0, 0)  # sorted by symbol: DGS2 was cut away


def test_field_selection_does_not_change_the_facts():
    everything = ask({"id=DGS10": DGS10}, ["FRED.DGS10"]).series_coverage
    narrow = ask({"id=DGS10": DGS10}, ["FRED.DGS10"], fields=["value"])
    assert narrow.series_coverage == everything and "unit" not in narrow.records[0]


def test_withheld_records_still_leave_coverage_rows_that_carry_no_observation():
    """allow_partial=False empties the records; the facts about each series stay and leak none of them."""
    sentinel = b"observation_date,DGS10\n2025-01-02,98765.4321\n2025-01-03,12345.6789\n"
    result = ask({"id=DGS10": sentinel, "worldbank": "network"}, ["FRED.DGS10", "WB.USA.NY.GDP.MKTP.KD.ZG"], allow_partial=False)
    assert result.records == [] and result.status.value == "unavailable"
    assert "partial_data_not_allowed" in [d.code for d in result.diagnostics]
    fine, failed = result.series_coverage
    assert (fine["status"], fine["n_obs"], fine["n_returned"]) == ("ok", 2, 0) and failed["status"] == "failed"
    text = json.dumps(result.to_dict()["series_coverage"])
    assert "98765" not in text and "12345" not in text and "4321" not in text
    assert all(set(row) <= KEYS | {"code"} for row in result.series_coverage)


def malformed(change):
    rows = [dict(symbol="FRED.DGS10", provider="global_macro", source_series_id="DGS10", frequency="daily",
                 date_field="observation_date", requested_start=date(2025, 1, 1), requested_end=date(2025, 1, 31),
                 status="ok", first_date=date(2025, 1, 2), last_date=date(2025, 1, 7), n_obs=3, n_missing=2)]
    change(rows)
    return rows


@pytest.mark.parametrize("change", [
    lambda rows: rows[0].update(last_value=4.67),                      # anything beyond the closed shape
    lambda rows: rows[0].pop("n_missing"),
    lambda rows: rows[0].update(symbol="FRED.DGS2"),                   # not the requested symbol
    lambda rows: rows.append(dict(rows[0])),                           # more rows than symbols
    lambda rows: rows[0].update(provider="someone_else"),
    lambda rows: rows[0].update(date_field="period"),
    lambda rows: rows[0].update(requested_end=date(2025, 2, 28)),
    lambda rows: rows[0].update(status="partial"),                     # a judgement, not a fact
    lambda rows: rows[0].update(status="stale"),
    lambda rows: rows[0].update(n_obs=-1),
    lambda rows: rows[0].update(n_obs=3.0),
    lambda rows: rows[0].update(first_date="2025-01-02"),
    lambda rows: rows[0].update(first_date=datetime(2025, 1, 2)),
    lambda rows: rows[0].update(last_date=date(2025, 3, 1)),           # outside the requested window
    lambda rows: rows[0].update(first_date=date(2025, 1, 9)),          # after the last date
    lambda rows: rows[0].update(status="empty"),                       # yet it has observations
    lambda rows: rows[0].update(n_obs=0),                              # yet it has dates
    lambda rows: rows[0].update(status="failed"),                      # failed without a code, with facts
    lambda rows: rows[0].update(status="failed", code="not_a_code", first_date=None, last_date=None, n_obs=None, n_missing=None),
    lambda rows: rows[0].update(code="network"),                       # a code on a series that did not fail
    lambda rows: rows.__setitem__(0, "FRED.DGS10"),
])
def test_a_provider_that_reports_malformed_coverage_is_an_invalid_response(monkeypatch, change):
    original = GlobalMacroAdapter.query_data
    def query_data(self, request, *, context):
        page = original(self, request, context=context)
        return DataPage(page.records, page.provenance, page.complete, page.next_cursor, page.diagnostics,
                        series_coverage=malformed(change))
    monkeypatch.setattr(GlobalMacroAdapter, "query_data", query_data)
    result = ask({"id=DGS10": DGS10}, ["FRED.DGS10"])
    assert result.status.value == "error" and not result.records and result.series_coverage == []
    assert "invalid_provider_response" in [d.code for d in result.diagnostics]


def test_well_formed_coverage_from_the_same_wrapper_is_accepted(monkeypatch):
    original = GlobalMacroAdapter.query_data
    def query_data(self, request, *, context):
        page = original(self, request, context=context)
        return DataPage(page.records, page.provenance, page.complete, page.next_cursor, page.diagnostics,
                        series_coverage=malformed(lambda rows: None))
    monkeypatch.setattr(GlobalMacroAdapter, "query_data", query_data)
    assert ask({"id=DGS10": DGS10}, ["FRED.DGS10"]).series_coverage[0]["n_returned"] == 5


def test_an_adapter_that_reports_nothing_leaves_an_empty_list(monkeypatch):
    original = GlobalMacroAdapter.query_data
    def query_data(self, request, *, context):
        page = original(self, request, context=context)
        return DataPage(page.records, page.provenance, page.complete, page.next_cursor, page.diagnostics)
    monkeypatch.setattr(GlobalMacroAdapter, "query_data", query_data)
    result = ask({"id=DGS10": DGS10}, ["FRED.DGS10"])
    assert result.status.value == "ok" and result.series_coverage == [] and result.to_dict()["series_coverage"] == []


def test_sdk_and_mcp_return_the_same_coverage():
    made = registry({"id=DGS10": DGS10, "worldbank": "network"})
    arguments = dict(dataset="macro_series", market="GLOBAL", symbols=["FRED.DGS10", "WB.USA.NY.GDP.MKTP.KD.ZG"],
                     start="2025-01-01", end="2025-01-31")
    sdk = get_data(DataRequest(**arguments), registry=made, context=RequestContext()).to_dict()["series_coverage"]
    mcp = get_data_payload(arguments, registry=made)["series_coverage"]
    assert mcp == sdk and json.loads(json.dumps(mcp)) == mcp
    assert mcp[0]["first_date"] == "2025-01-02" and mcp[0]["requested_end"] == "2025-01-31" and mcp[1]["code"] == "network"
