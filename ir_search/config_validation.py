from __future__ import annotations

import argparse
from typing import Mapping, Optional

from .config import load_yaml
from .kernel import build_registry
from .models import CoverageStatus, Intent, ResultKind, SourceAuthority, SourceTier


class ConfigError(Exception):
    pass


ALLOWED_WEIGHT_KEYS = {
    "authority",
    "relevance",
    "evidence_type",
    "freshness",
    "agreement",
    "entity_match",
    "noise_penalty",
}


def validate_configs(registry: Optional[Mapping[str, object]] = None, configs: Optional[dict] = None) -> list[str]:
    registry = registry or build_registry(live=False)
    known_sources = set(registry) | {"anysearch", "web_search", "tavily"}
    cfg = configs or {
        "source_routes": load_yaml("source_routes.yaml"),
        "source_tiers": load_yaml("source_tiers.yaml"),
        "rerank_weights": load_yaml("rerank_weights.yaml"),
        "source_focus_sites": load_yaml("source_focus_sites.yaml"),
        "fallback_routes": load_yaml("fallback_routes.yaml"),
        "source_capabilities": load_yaml("source_capabilities.yaml"),
        "sentiment_sites": load_yaml("sentiment_sites.yaml"),
        "intent_rules": load_yaml("intent_rules.yaml"),
    }
    errors: list[str] = []
    _validate_source_routes(cfg.get("source_routes", {}), known_sources, errors)
    _validate_source_tiers(cfg.get("source_tiers", {}), errors)
    _validate_rerank_weights(cfg.get("rerank_weights", {}), errors)
    _validate_source_focus_sites(cfg.get("source_focus_sites", {}), known_sources, errors)
    _validate_fallback_routes(cfg.get("fallback_routes", {}), known_sources, errors)
    _validate_source_capabilities(cfg.get("source_capabilities", {}), known_sources, errors)
    _validate_sentiment_sites(cfg.get("sentiment_sites", {}), errors)
    _validate_intent_rules(cfg.get("intent_rules", {}), errors)
    return errors


def assert_valid_configs(registry: Optional[Mapping[str, object]] = None) -> None:
    errors = validate_configs(registry)
    if errors:
        raise ConfigError("\n".join(errors))


def _validate_source_routes(config: dict, known_sources: set[str], errors: list[str]) -> None:
    routes = config.get("routes", {})
    for intent_name, route in routes.items():
        _check_intent(intent_name, "source_routes", errors)
        for lang_or_default, sources in route.items():
            if not isinstance(sources, list) or not all(isinstance(source, str) for source in sources):
                errors.append(f"source_routes.{intent_name}.{lang_or_default} must be list[str]")
                continue
            _check_sources(sources, known_sources, f"source_routes.{intent_name}.{lang_or_default}", errors)


def _validate_source_tiers(config: dict, errors: list[str]) -> None:
    for domain, tier_name in config.get("source_tiers", {}).items():
        if not isinstance(domain, str) or not domain:
            errors.append("source_tiers domain must be non-empty string")
        if tier_name not in SourceTier.__members__:
            errors.append(f"source_tiers.{domain} unknown tier {tier_name}")


def _validate_rerank_weights(config: dict, errors: list[str]) -> None:
    for group_name, weights in config.items():
        if group_name != "default":
            _check_intent(group_name, "rerank_weights", errors)
        if not isinstance(weights, dict):
            errors.append(f"rerank_weights.{group_name} must be a mapping")
            continue
        for key, value in weights.items():
            if key not in ALLOWED_WEIGHT_KEYS:
                errors.append(f"rerank_weights.{group_name}.{key} unknown weight key")
            if not isinstance(value, (int, float)):
                errors.append(f"rerank_weights.{group_name}.{key} must be numeric")
            elif value < 0:
                errors.append(f"rerank_weights.{group_name}.{key} must be non-negative")


def _validate_source_focus_sites(config: dict, known_sources: set[str], errors: list[str]) -> None:
    for adapter, intents in config.items():
        if adapter not in known_sources:
            errors.append(f"source_focus_sites unknown adapter {adapter}")
        if not isinstance(intents, dict):
            errors.append(f"source_focus_sites.{adapter} must be a mapping")
            continue
        for intent_name, domains in intents.items():
            _check_intent(intent_name, f"source_focus_sites.{adapter}", errors)
            if not isinstance(domains, list) or not all(isinstance(domain, str) for domain in domains):
                errors.append(f"source_focus_sites.{adapter}.{intent_name} must be list[str]")
                continue
            for domain in domains:
                if not domain or "://" in domain:
                    errors.append(f"source_focus_sites.{adapter}.{intent_name} invalid domain {domain}")


def _validate_fallback_routes(config: dict, known_sources: set[str], errors: list[str]) -> None:
    fallbacks = config.get("fallbacks", {})
    for source, fallback_sources in fallbacks.items():
        if source not in known_sources:
            errors.append(f"fallback_routes unknown source {source}")
        if not isinstance(fallback_sources, list) or not all(isinstance(item, str) for item in fallback_sources):
            errors.append(f"fallback_routes.{source} must be list[str]")
            continue
        _check_sources(fallback_sources, known_sources, f"fallback_routes.{source}", errors)
        if source in fallback_sources:
            errors.append(f"fallback_routes.{source} cannot fallback to itself")
    for source in fallbacks:
        _check_cycle(source, fallbacks, [], errors)
    error_classes = config.get("error_classes", {})
    if not isinstance(error_classes, dict):
        errors.append("fallback_routes.error_classes must be a mapping")
        return
    for class_name, needles in error_classes.items():
        if class_name not in {"quota", "network"}:
            errors.append(f"fallback_routes.error_classes unknown class {class_name}")
        if not isinstance(needles, list) or not all(isinstance(item, str) and item for item in needles):
            errors.append(f"fallback_routes.error_classes.{class_name} must be non-empty list[str]")


def _validate_source_capabilities(config: dict, known_sources: set[str], errors: list[str]) -> None:
    rows = config.get("sources", {})
    if not isinstance(rows, dict):
        errors.append("source_capabilities.sources must be a mapping")
        return
    for source, row in rows.items():
        if source not in known_sources:
            errors.append(f"source_capabilities unknown source {source}")
        if not isinstance(row, dict):
            errors.append(f"source_capabilities.{source} must be a mapping")
            continue
        authority = row.get("authority")
        if authority not in {item.value for item in SourceAuthority}:
            errors.append(f"source_capabilities.{source}.authority unknown authority {authority}")
        result_kinds = row.get("result_kinds", [])
        if not isinstance(result_kinds, list) or not result_kinds:
            errors.append(f"source_capabilities.{source}.result_kinds must be non-empty list[str]")
        else:
            for kind in result_kinds:
                if kind not in {item.value for item in ResultKind}:
                    errors.append(f"source_capabilities.{source}.result_kinds unknown kind {kind}")
        authorities = row.get("can_fallback_to_authorities", [])
        if not isinstance(authorities, list):
            errors.append(f"source_capabilities.{source}.can_fallback_to_authorities must be list[str]")
        else:
            for target in authorities:
                if target not in {item.value for item in SourceAuthority}:
                    errors.append(f"source_capabilities.{source}.can_fallback_to_authorities unknown authority {target}")
        if "max_evidence_status" in row and row.get("max_evidence_status") not in {item.value for item in CoverageStatus}:
            errors.append(
                f"source_capabilities.{source}.max_evidence_status unknown coverage status {row.get('max_evidence_status')}"
            )


def _validate_sentiment_sites(config: dict, errors: list[str]) -> None:
    for region in ["cn", "us"]:
        rows = config.get(region, [])
        if not isinstance(rows, list):
            errors.append(f"sentiment_sites.{region} must be a list")
            continue
        for idx, row in enumerate(rows):
            domain = row.get("domain") if isinstance(row, dict) else None
            if not isinstance(domain, str) or not domain or "://" in domain:
                errors.append(f"sentiment_sites.{region}[{idx}] invalid domain")


def _validate_intent_rules(config: dict, errors: list[str]) -> None:
    rules = config.get("rules", {})
    if not isinstance(rules, dict):
        errors.append("intent_rules.rules must be a mapping")
        return
    for intent_name, needles in rules.items():
        _check_intent(intent_name, "intent_rules", errors)
        if not isinstance(needles, list) or not all(isinstance(needle, str) and needle for needle in needles):
            errors.append(f"intent_rules.{intent_name} must be non-empty list[str]")


def _check_sources(sources: list[str], known_sources: set[str], path: str, errors: list[str]) -> None:
    for source in sources:
        if source not in known_sources:
            errors.append(f"{path} unknown source {source}")


def _check_intent(intent_name: str, path: str, errors: list[str]) -> None:
    if intent_name not in Intent.__members__:
        errors.append(f"{path} unknown intent {intent_name}")


def _check_cycle(source: str, fallbacks: dict, path: list[str], errors: list[str]) -> None:
    if source in path:
        errors.append(f"fallback_routes cycle detected: {' -> '.join(path + [source])}")
        return
    for child in fallbacks.get(source, []):
        if child in fallbacks:
            _check_cycle(child, fallbacks, path + [source], errors)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    errors = validate_configs()
    if errors:
        for error in errors:
            print(error)
        if args.strict:
            raise SystemExit(1)
    else:
        print("config validation passed")


if __name__ == "__main__":
    main()
