from ir_search.models import Intent, Query
from ir_search.config_validation import validate_configs
from ir_search.pipeline import intent_rules, prepare_query


def test_single_intent_query():
    q = prepare_query(Query(text="中际旭创 最新公告"))

    assert q.intent == Intent.FILING


def test_mixed_intent_query_has_secondary_intents():
    q = prepare_query(Query(text="中际旭创公告 解读 观点"))

    assert q.intent == Intent.FILING
    assert Intent.BROKER_RESEARCH in q.secondary_intents


def test_intent_scores_are_recorded():
    q = prepare_query(Query(text="中际旭创公告 解读 观点"))

    assert q.intent_scores["FILING"] > 0
    assert q.intent_scores["BROKER_RESEARCH"] > 0


def test_primary_intent_is_highest_score():
    q = prepare_query(Query(text="商务部 出口管制 半导体 政策"))

    assert q.intent_scores[q.intent.name] == max(q.intent_scores.values())


def test_intent_rules_loaded_from_config():
    rules = intent_rules()

    assert Intent.FILING in rules
    assert "公告" in rules[Intent.FILING]


def test_config_validation_checks_intent_rules():
    errors = validate_configs(configs={"intent_rules": {"rules": {"NOT_AN_INTENT": ["x"]}}})

    assert any("intent_rules unknown intent NOT_AN_INTENT" in error for error in errors)
