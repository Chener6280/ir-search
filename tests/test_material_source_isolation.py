"""One slow source or one unusable record must not cost the rest of a material search."""
from datetime import datetime, timezone

from ir_search import (
    AdapterMode, Diagnostic, MaterialCandidate, MaterialCapability, MaterialKind, MaterialRegistry,
    MaterialSearchPage, MaterialSearchRequest, Provenance, RequestContext, search_materials,
)
from ir_search.context import RequestStopped, SourceSlice
from ir_search.models import EvidenceType, FailureKind, SourceAuthority, SourceTier

NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def candidate(provider, index=1, text="贵州茅台9月动销出现分化。", **changes):
    args = dict(source_ref=f"fixture://{provider}/{index}", title="渠道反馈", material_type="channel_check",
                channel="community", published_on="2026-10-01", symbols=["600519.SH"], text=text,
                text_scope="extracted_text",
                provenance=Provenance(provider, "fixture publisher", NOW, authority=SourceAuthority.DATA_VENDOR,
                                      source_tier=SourceTier.UGC, evidence_type=EvidenceType.OPINION,
                                      adapter_mode=AdapterMode.LIVE))
    args.update(changes)
    return MaterialCandidate(**args)


class Source:
    def __init__(self, name, rows=None, hook=None, diagnostics=()):
        self.name, self.hook, self.diagnostics = name, hook, list(diagnostics)
        self.capability = MaterialCapability(name, "community", tuple(MaterialKind))
        self.rows = rows if rows is not None else [candidate(name)]
        self.contexts = []

    def search_materials(self, request, *, context):
        self.contexts.append(context)
        if self.hook:
            self.hook(context)
        return MaterialSearchPage(self.rows, len(self.rows), True, diagnostics=self.diagnostics)


def search(sources, context=None, **changes):
    registry = MaterialRegistry()
    for source in sources:
        registry.register(source)
    args = dict(question="贵州茅台9月动销", published_start="2026-09-01", published_end="2026-10-10",
                period_start="2026-09-01", period_end="2026-09-30", providers=[s.name for s in sources])
    args.update(changes)
    return search_materials(MaterialSearchRequest(**args), registry=registry, context=context)


def states(result):
    return {row["provider"]: row for row in result.coverage}


def test_slow_source_only_loses_its_own_time_share_and_later_sources_still_run():
    clock = Clock()

    def slow(context):
        assert isinstance(context, SourceSlice) and context.remaining_seconds() == 10  # 30s over three sources
        clock.now += 11
        context.check_active()

    sources = [Source("source_a", hook=slow), Source("source_b"), Source("source_c")]
    result = search(sources, context=RequestContext(timeout_seconds=30, _clock=clock))
    coverage = states(result)
    assert coverage["source_a"]["state"] == "source_time_share_exceeded"
    assert result.timing == {"elapsed_ms": 11000, "sources": [
        {"provider": "source_a", "elapsed_ms": 11000}, {"provider": "source_b", "elapsed_ms": 0},
        {"provider": "source_c", "elapsed_ms": 0}]}
    assert all("elapsed_ms" not in row for row in result.coverage)  # coverage stays comparable across runs
    assert coverage["source_b"]["state"] == coverage["source_c"]["state"] == "queried"
    assert {v["provenance"]["provider"] for g in result.items for v in g["versions"]} == {"source_b", "source_c"}
    assert {"code": "source_time_share_exceeded", "provider": "source_a"} in result.gaps
    assert not any(gap["code"] == "not_queried_request_stopped" for gap in result.gaps)
    # Unused time rolls over: 19s left for two sources.
    assert sources[1].contexts[0]._deadline == 1020.5
    timeout = next(d for d in result.diagnostics if d.code == "source_time_share_exceeded")
    assert timeout.provider == "source_a" and timeout.failure_kind == FailureKind.TIMEOUT


def test_request_deadline_and_cancellation_still_stop_every_later_source():
    clock = Clock()

    def exhaust(context):
        clock.now += 31
        context.check_active()

    late = Source("source_b")
    result = search([Source("source_a", hook=exhaust), late], context=RequestContext(timeout_seconds=30, _clock=clock))
    assert states(result)["source_a"]["state"] == "deadline_exceeded" and not late.contexts
    assert states(result)["source_b"]["state"] == "not_queried_request_stopped"

    context = RequestContext(timeout_seconds=30)

    def cancel(share):
        context.cancel()
        share.check_active()

    late = Source("source_b")
    result = search([Source("source_a", hook=cancel), late], context=context)
    assert states(result)["source_a"]["state"] == "cancelled" and not late.contexts


def test_slice_shares_operations_cancellation_and_request_local_state_with_the_request():
    clock = Clock()
    parent = RequestContext(timeout_seconds=30, max_operations=2, _clock=clock)
    share = SourceSlice(parent, 5)
    share.begin_operation()
    assert parent.operations == share.operations == 1 and share.request_id == parent.request_id
    share._request_cache = {"k": 1}
    assert parent._request_cache == {"k": 1}
    clock.now += 5
    assert share.expired() and share.remaining_seconds() == 0 and parent.remaining_seconds() == 25
    try:
        share.begin_operation()
    except RequestStopped as exc:
        assert exc.code == "deadline_exceeded" and parent.operations == 1
    else:
        raise AssertionError("an expired share must not start operations")


def test_one_unusable_record_is_counted_and_the_other_records_are_kept():
    # Valid on arrival, but truncation to max_chars leaves only whitespace.
    blank_after_truncation = candidate("source_a", 2, text=" " * 150 + "贵州茅台9月动销。")
    rows = [candidate("source_a", 1), blank_after_truncation, candidate("source_a", 3, text="贵州茅台动销回暖。")]
    result = search([Source("source_a", rows=rows)], max_chars=120)
    assert sorted(v["source_ref"] for g in result.items for v in g["versions"]) == ["fixture://source_a/1", "fixture://source_a/3"]
    coverage = states(result)["source_a"]
    assert coverage["state"] == "queried" and coverage["matched_count"] == 2 and coverage["rejected_count"] == 1
    assert {"code": "candidate_rejected", "provider": "source_a", "count": 1} in result.gaps
    rejected = next(d for d in result.diagnostics if d.code == "candidate_rejected")
    assert rejected.message == "rejected_count=1" and rejected.failure_kind == FailureKind.UPSTREAM_SCHEMA


def test_adapter_route_labels_survive_the_service_boundary():
    note = Diagnostic("web_discovery_route_failed", "search_materials", "source_a",
                      failure_kind=FailureKind.NETWORK, message="discovery_provider=bocha;web_region=domestic")
    result = search([Source("source_a", diagnostics=[note])])
    passed = next(d for d in result.diagnostics if d.code == "web_discovery_route_failed")
    assert passed.message == "discovery_provider=bocha;web_region=domestic" and passed.provider == "source_a"
    assert result.plan["budget"]["source_time_share"] == "remaining_seconds_divided_by_pending_sources_unused_time_rolls_over"
