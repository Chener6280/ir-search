from ir_search.config_validation import validate_configs
from ir_search.kernel import build_registry
from ir_search.config import load_yaml


def _configs():
    return {
        "source_routes": load_yaml("source_routes.yaml"),
        "source_tiers": load_yaml("source_tiers.yaml"),
        "rerank_weights": load_yaml("rerank_weights.yaml"),
        "source_focus_sites": load_yaml("source_focus_sites.yaml"),
        "fallback_routes": load_yaml("fallback_routes.yaml"),
        "sentiment_sites": load_yaml("sentiment_sites.yaml"),
    }


def test_valid_configs_load():
    assert validate_configs(build_registry(live=False)) == []


def test_unknown_source_in_routes_fails():
    cfg = _configs()
    cfg["source_routes"] = {"routes": {"GENERAL": {"zh": ["cninf0"]}}}

    assert any("unknown source cninf0" in error for error in validate_configs(configs=cfg))


def test_unknown_intent_in_routes_fails():
    cfg = _configs()
    cfg["source_routes"] = {"routes": {"NOT_INTENT": {"zh": ["bocha"]}}}

    assert any("unknown intent NOT_INTENT" in error for error in validate_configs(configs=cfg))


def test_invalid_rerank_weight_type_fails():
    cfg = _configs()
    cfg["rerank_weights"] = {"default": {"authority": "bad"}}

    assert any("must be numeric" in error for error in validate_configs(configs=cfg))


def test_negative_rerank_weight_fails():
    cfg = _configs()
    cfg["rerank_weights"] = {"default": {"authority": -1}}

    assert any("non-negative" in error for error in validate_configs(configs=cfg))


def test_unknown_tier_fails():
    cfg = _configs()
    cfg["source_tiers"] = {"source_tiers": {"example.com": "SUPER"}}

    assert any("unknown tier" in error for error in validate_configs(configs=cfg))


def test_focus_site_with_protocol_fails():
    cfg = _configs()
    cfg["source_focus_sites"] = {"bocha": {"GENERAL": ["https://example.com"]}}

    assert any("invalid domain" in error for error in validate_configs(configs=cfg))


def test_fallback_self_loop_fails():
    cfg = _configs()
    cfg["fallback_routes"] = {"fallbacks": {"bocha": ["bocha"]}}

    assert any("cannot fallback to itself" in error for error in validate_configs(configs=cfg))


def test_fallback_unknown_source_fails():
    cfg = _configs()
    cfg["fallback_routes"] = {"fallbacks": {"bocha": ["missing"]}}

    assert any("unknown source missing" in error for error in validate_configs(configs=cfg))
