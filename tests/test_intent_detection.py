from ir_search.models import Intent, Query
from ir_search.pipeline import prepare_query


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
