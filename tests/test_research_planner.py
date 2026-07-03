from ir_search.research.planner import plan_research_queries


def test_plan_earnings_queries_include_official_and_financial_report():
    plan = plan_research_queries("中际旭创 最新季报", intent="earnings")

    assert plan.intent == "earnings"
    assert "cninfo" in plan.required_sources
    assert any("官方公告" in query or "财报" in query for query in plan.queries)


def test_plan_policy_queries_include_regulator():
    plan = plan_research_queries("最近监管政策有什么变化", intent="policy")

    assert plan.intent == "policy"
    assert plan.required_sources == ["regulator_sites"]
    assert any("监管" in query for query in plan.queries)


def test_plan_wechat_crosscheck_queries_include_confirmation_sources():
    plan = plan_research_queries("某公众号称行业涨价，是否验证", intent="wechat_crosscheck")

    assert "manual_wechat" in plan.required_sources
    assert "cninfo" in plan.required_sources
    assert any("交叉验证" in query for query in plan.queries)


def test_plan_clamps_max_searches_and_adds_warning():
    plan = plan_research_queries("中际旭创 最新季报", intent="earnings", max_searches=20)

    assert len(plan.queries) <= 8
    assert "max_searches clamped to 8" in plan.warnings


def test_source_health_placeholder_added_to_plan_warnings():
    health = {"sources": {"sse": {"adapter_mode": "placeholder"}}}
    plan = plan_research_queries("中际旭创 最新季报", intent="earnings", source_health=health)

    assert any("sse is placeholder" in warning for warning in plan.warnings)
