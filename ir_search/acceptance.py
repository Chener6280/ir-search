"""Opt-in, bounded data acceptance. Reports metadata, never credentials or raw rows."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .contracts import AdapterMode, DataRequest, DataResult, JsonModel, Status
from .context import RequestContext
from .infrastructure.credentials import source_configuration_status
from .registry import _DATASETS, build_data_registry
from .services.data import get_data
from .source_policy import source_policy
from .models import FailureKind


@dataclass(frozen=True)
class AcceptanceProbe(JsonModel):
    name: str
    request: DataRequest

    def __post_init__(self):
        import re
        if not isinstance(self.name, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", self.name):
            raise ValueError("Invalid probe name")
        if not isinstance(self.request, DataRequest):
            raise ValueError("A probe requires DataRequest")


def default_acceptance_probes(*, day=None) -> tuple[AcceptanceProbe, ...]:
    """Representative closed-date requests; weekday selection is NOT a holiday calendar."""
    today = day if day is not None else datetime.now(ZoneInfo("Asia/Shanghai")).date()
    if type(today) is not date:
        raise ValueError("day must be a date")
    end = today - timedelta(days=1)
    while end.weekday() > 4:
        end -= timedelta(days=1)
    probes = []
    def add(name, dataset, symbols, market="A_SHARE", fields=(), provider=None, frequency=None):
        probes.append(AcceptanceProbe(name, DataRequest(dataset, symbols=symbols, fields=fields, market=market,
                                                       start=end, end=end, provider=provider, frequency=frequency, limit=1000)))
    for provider in ("wind_mysql", "jydb"):
        add(provider + "_daily_prices", "prices_daily", ("600519.SH", "000001.SZ", "688981.SH"),
            fields=("open", "high", "low", "close"), provider=provider)
        add(provider + "_daily_activity", "prices_daily", ("600519.SH",), fields=("volume", "amount"), provider=provider)
    for symbol, label in (("600519.SH", "sh"), ("000001.SZ", "sz")):
        add("akshare_" + label + "_intraday", "prices_intraday", (symbol,), fields=("open", "high", "low", "close"), provider="akshare")
    # YYMM references select this month's concrete contracts; existence is a live check.
    month = end.strftime("%y%m")
    for root, label in (("IF", "financial"), ("RB", "commodity")):
        add("akshare_" + label + "_futures_intraday", "futures_intraday", (root + month,), "CN_FUTURES", provider="akshare", frequency="5m")
    for provider in ('wind_mysql', 'jydb'):
        period = date(today.year - 1, 12, 31)
        probes.append(AcceptanceProbe(provider + '_annual_financial', DataRequest('financial_statements',
            symbols=('600519.SH', '688981.SH'), start=period, end=period, provider=provider)))
        add(provider + '_futures_daily', 'futures_daily', ('IF' + month + '.CFE',), 'CN_FUTURES', provider=provider)
    add('futures_contract_metadata', 'futures_contracts', ('IF' + month + '.CFE',), 'CN_FUTURES')
    add('futures_open_dates', 'trading_calendar', ('CFFEX', 'SHFE', 'DCE', 'CZCE', 'INE', 'GFEX'), 'CN_FUTURES')
    for dataset, market in (("options_daily", "CN_OPTIONS"), ("derivatives_daily", "CN_DERIVATIVES"),
                            ("options_intraday", "CN_OPTIONS")):
        # Option IDs must come from a user's eligible contract sample, never a made-up strike/expiry.
        add(dataset + "_coverage", dataset, (), market)
    return tuple(probes)


def data_coverage(*, registry=None) -> dict:
    """Separate configured capabilities from requested routes; performs no network I/O."""
    registry = registry if registry is not None else build_data_registry()
    rows = []
    for route in source_policy()["routes"]:
        caps = [cap for cap, _ in registry.entries() if cap.dataset == route["dataset"] and route["market"] in cap.markets]
        rows.append(dict(route, dataset_defined=route["dataset"] in _DATASETS,
                         configured_capabilities=[cap.to_dict() for cap in caps], live_verified=False))
    return {"verification_basis": "local_code_and_configuration_only", "coverage": rows,
            "historical_intraday_provider": None, "diagnostics": [d.to_dict() for d in registry.diagnostics]}


def assess_data_quality(result: DataResult) -> dict:
    """Describe observed coverage/nulls without inferring sessions, freshness or PIT."""
    if not isinstance(result, DataResult):
        raise ValueError("Expected DataResult")
    definition = result.definition
    date_field = definition.date_field if definition else None
    dates = sorted({str(row[date_field]) for row in result.records if date_field and row.get(date_field) is not None})
    observed = {row["symbol"] for row in result.records}
    selected = set(result.request.fields) if result.request.fields else ({f.name for f in definition.fields} if definition else set())
    nulls = {name: sum(row.get(name) is None for row in result.records) for name in sorted(selected)}
    return {"row_count": len(result.records), "observed_symbol_count": len(observed),
            "unseen_symbols": sorted(set(result.request.symbols) - observed),
            "observed_start": dates[0] if dates else None, "observed_end": dates[-1] if dates else None,
            "observed_date_count": len(dates), "null_counts": nulls,
            "all_null_requested_fields": [name for name in result.request.fields if result.records and nulls[name] == len(result.records)],
            "page_complete": result.complete, "has_next_page": bool(result.next_cursor),
            "exchange_calendar_coverage": "unverified", "publication_delay": "unverified",
            "point_in_time": "unverified", "date_basis": date_field,
            "units": {f.name: f.unit for f in definition.fields if f.name in selected and f.unit} if definition else {}}


def run_data_acceptance(probes=None, *, live=False, env_file=None, repeats=2,
                        timeout_seconds=20.0, total_timeout_seconds=180.0) -> dict:
    """Serial live probes with bounded repeats and fatal-error circuit breaking.

    A successful sample never certifies the entire dataset or future availability.
    No credentials, endpoints, SQL, raw errors, rows or cursors enter the report.
    """
    if type(live) is not bool or type(repeats) is not int or not 1 <= repeats <= 3:
        raise ValueError("Invalid acceptance settings")
    RequestContext(timeout_seconds=timeout_seconds)
    RequestContext(timeout_seconds=total_timeout_seconds)
    probes = tuple(default_acceptance_probes() if probes is None else probes)
    if not 1 <= len(probes) <= 30 or any(not isinstance(p, AcceptanceProbe) for p in probes) or len({p.name for p in probes}) != len(probes):
        raise ValueError("Expected 1 to 30 uniquely named probes")
    registry = build_data_registry(env_file=env_file)
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "live_requested": live,
              "configuration": source_configuration_status(env_file=env_file), "coverage": data_coverage(registry=registry),
              "date_selection": "previous_weekday_for_prices_previous_year_end_for_financials_not_verified_availability", "probes": [],
              "full_acceptance_passed": False, "limitations": ["samples_only", "no_full_session_or_PIT_certification",
                "short_run_repeatability_not_long_term_uptime", "no_live_cross_vendor_reconciliation_without_two_usable_sources"]}
    deadline = time.monotonic() + total_timeout_seconds
    blocked = {}
    fatal = {"tls_error", "tls_not_supported", "entitlement_denied", "no_credential", "source_credentials_missing",
             "dependency_missing", "source_config_error", "quota", "rate_limit"}
    for probe in probes:
        request = probe.request
        item = {"name": probe.name, "request": request.to_dict(), "attempts": []}
        # Request cursors and as_of labels are unnecessary in a shareable acceptance artifact.
        item["request"].pop("cursor", None)
        report["probes"].append(item)
        if request.dataset not in _DATASETS:
            item.update(outcome="mapping_not_implemented", diagnostics=["dataset_mapping_not_implemented"])
            continue
        if not live:
            item.update(outcome="not_run", diagnostics=["live_probe_not_requested"])
            continue
        if not request.symbols:
            item.update(outcome="sample_parameters_required", diagnostics=["explicit_eligible_sample_required"])
            continue
        fingerprints = []
        outcomes = []
        for _ in range(repeats):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                outcomes.append("not_run")
                item["attempts"].append({"diagnostics": ["acceptance_budget_exhausted"]})
                break
            if request.provider in blocked:
                outcomes.append("blocked")
                item["attempts"].append({"diagnostics": ["provider_circuit_open", blocked[request.provider]]})
                break
            started = time.monotonic()
            result = get_data(request, registry=registry, context=RequestContext(timeout_seconds=min(timeout_seconds, remaining)))
            codes = [d.code for d in result.diagnostics]
            quality = assess_data_quality(result)
            real = bool(result.records and result.provenance and result.provenance.adapter_mode == AdapterMode.LIVE
                        and not result.provenance.generated and result.status in {Status.OK, Status.PARTIAL})
            outcome = "sample_received" if real else "no_usable_sample"
            if real and (quality["all_null_requested_fields"] or (quality["unseen_symbols"] and result.complete)
                         or any(d.failure_kind == FailureKind.UPSTREAM_SCHEMA for d in result.diagnostics)):
                outcome = "sample_quality_failed"
            outcomes.append(outcome)
            attempt = {"status": result.status.value, "outcome": outcome, "quality": quality, "diagnostics": codes,
                       "elapsed_seconds": round(time.monotonic() - started, 3),
                       "provider": result.provenance.provider if result.provenance else request.provider,
                       "publisher": result.provenance.publisher if result.provenance else None,
                       "evidence_type": result.provenance.evidence_type.value if result.provenance else None,
                       "source_tier": int(result.provenance.source_tier) if result.provenance and result.provenance.source_tier is not None else None,
                       "authority": result.provenance.authority.value if result.provenance else None}
            item["attempts"].append(attempt)
            if real:
                payload = json.dumps(result.to_dict()["records"], sort_keys=True, ensure_ascii=False, separators=(",", ":"))
                fingerprint = hashlib.sha256(payload.encode()).hexdigest()
                attempt["sample_sha256"] = fingerprint
                fingerprints.append(fingerprint)
            terminal = next((code for code in codes if code in fatal), None)
            if terminal and request.provider:
                blocked[request.provider] = terminal
            if not real:
                # Do not hammer a missing mapping/credential or deterministic failed query.
                break
        item["outcome"] = outcomes[-1] if outcomes else "not_run"
        if len(fingerprints) >= 2:
            item["repeatability"] = "identical" if len(set(fingerprints)) == 1 else "changed_requires_review"
        else:
            item["repeatability"] = "not_verified"
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    report["sample_received_count"] = sum(p["outcome"] == "sample_received" for p in report["probes"])
    report["sample_checks_passed"] = bool(live and all(
        p["outcome"] == "sample_received" and p.get("repeatability") == "identical" for p in report["probes"]))
    # Full acceptance is intentionally not inferred from a handful of observations.
    return report


def main(argv=None) -> int:
    """CLI defaults to an offline inventory. --live explicitly enables reads."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--env-file")
    parser.add_argument("--output")
    parser.add_argument("--repeats", type=int, default=2)
    args = parser.parse_args(argv)
    try:
        report = run_data_acceptance(live=args.live, env_file=args.env_file, repeats=args.repeats)
        payload = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.output:
            target = Path(args.output)
            target.parent.mkdir(parents=True, exist_ok=True)
            # Avoid accidental overwrite of credentials or an existing artifact.
            with target.open("x", encoding="utf-8") as stream:
                stream.write(payload)
        else:
            print(payload, end="")
        return 0 if not args.live or report["sample_checks_passed"] else 2
    except Exception:
        print('{"error":"acceptance_failed"}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
