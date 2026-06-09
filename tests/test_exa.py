from ir_search.adapters.exa import build_exa_payload, focus_site_domains, sentiment_site_domains
from ir_search.evidence import classify_hit
from ir_search.models import EvidenceType, Hit, Intent, Query, SourceTier


def test_exa_focus_site_domains_include_requested_media():
    domains = focus_site_domains()

    assert "wsj.com" in domains
    assert "bloomberg.com" in domains
    assert "ft.com" in domains
    assert "reuters.com" in domains
    assert "cnbc.com" in domains
    assert "economist.com" in domains
    assert "forbes.com" in domains
    assert "fortune.com" in domains
    assert "barrons.com" in domains
    assert "marketwatch.com" in domains
    assert "businessinsider.com" in domains
    assert "finance.yahoo.com" in domains
    assert "morningstar.com" in domains
    assert "seekingalpha.com" in domains


def test_exa_focus_site_domains_can_filter_by_category():
    assert focus_site_domains("authoritative_financial_press") == [
        "wsj.com",
        "bloomberg.com",
        "ft.com",
        "reuters.com",
    ]
    assert focus_site_domains("investor_research_opinion") == [
        "barrons.com",
        "morningstar.com",
        "seekingalpha.com",
    ]


def test_exa_payload_uses_include_domains():
    payload = build_exa_payload(Query(text="NVIDIA capex optical modules", intent=Intent.COMPANY_NEWS, count=5))

    assert payload["query"] == "NVIDIA capex optical modules"
    assert payload["numResults"] == 5
    assert "includeDomains" in payload
    assert "bloomberg.com" in payload["includeDomains"]
    assert "reuters.com" in payload["includeDomains"]
    assert "seekingalpha.com" not in payload["includeDomains"]


def test_exa_sentiment_payload_uses_us_sentiment_domains():
    payload = build_exa_payload(Query(text="NVIDIA StockTwits sentiment", intent=Intent.SENTIMENT, count=5))

    assert sentiment_site_domains("us") == ["stocktwits.com", "aaii.com", "naaim.org", "cboe.com"]
    assert payload["includeDomains"] == ["stocktwits.com", "aaii.com", "naaim.org", "cboe.com"]
    assert "bloomberg.com" not in payload["includeDomains"]


def test_exa_domain_tiers_and_opinion_classification():
    morningstar = classify_hit(
        Hit(
            title="Analyst view on AI infrastructure stocks",
            url="https://www.morningstar.com/stocks/mock",
            snippet="Analyst research and valuation view.",
            source="exa",
        )
    )
    seeking_alpha = classify_hit(
        Hit(
            title="NVIDIA capex: investor opinion",
            url="https://seekingalpha.com/article/mock",
            snippet="Contributor view.",
            source="exa",
        )
    )

    assert morningstar.tier == SourceTier.BROKER
    assert morningstar.evidence_type == EvidenceType.OPINION
    assert seeking_alpha.tier == SourceTier.UGC
    assert seeking_alpha.evidence_type == EvidenceType.SOCIAL_POST


def test_sentiment_domain_tiers_and_data_table_classification():
    stocktwits = classify_hit(
        Hit(
            title="NVDA message volume and bullish sentiment",
            url="https://stocktwits.com/symbol/NVDA",
            snippet="Trending messages.",
            source="exa",
        )
    )
    cboe = classify_hit(
        Hit(
            title="CBOE Put/Call Ratio",
            url="https://www.cboe.com/us/options/market_statistics/daily/",
            snippet="Total put/call, equity put/call, index put/call.",
            source="exa",
        )
    )

    assert stocktwits.tier == SourceTier.UGC
    assert stocktwits.evidence_type == EvidenceType.SOCIAL_POST
    assert cboe.tier == SourceTier.COMPANY
    assert cboe.evidence_type == EvidenceType.DATA_TABLE
