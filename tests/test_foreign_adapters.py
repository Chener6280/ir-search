from ir_search.adapters.tavily import build_tavily_payload
from ir_search.models import Query


def test_tavily_payload_defaults_to_finance_basic():
    payload = build_tavily_payload(Query(text="NVIDIA capex", count=7))

    assert payload["query"] == "NVIDIA capex"
    assert payload["max_results"] == 7
    assert payload["search_depth"] == "basic"
    assert payload["topic"] == "finance"
    assert payload["include_answer"] is False

