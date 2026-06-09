from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path

from .models import Entity, EntityType, Query


ENTITY_DIR = Path(__file__).resolve().parent / "entities"


def _split(value: str) -> list[str]:
    return [part.strip() for part in value.split("|") if part.strip()]


@lru_cache(maxsize=1)
def load_entities() -> list[Entity]:
    entities: list[Entity] = []
    for filename, entity_type in [
        ("a_share_companies.csv", EntityType.COMPANY),
        ("industry_terms.csv", EntityType.INDUSTRY),
    ]:
        with (ENTITY_DIR / filename).open("r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                entities.append(
                    Entity(
                        canonical_id=row["canonical_id"],
                        entity_type=entity_type,
                        names=_split(row.get("names", "")),
                        aliases=_split(row.get("aliases", "")),
                        codes=_split(row.get("codes", "")),
                        market=row.get("market") or None,
                        related_terms=_split(row.get("related_terms", "")),
                    )
                )
    return entities


def normalize_entities(q: Query) -> Query:
    haystack = q.text.lower()
    found: list[Entity] = []
    terms: list[str] = []

    for entity in load_entities():
        candidates = entity.names + entity.aliases + entity.codes
        if any(candidate and candidate.lower() in haystack for candidate in candidates):
            found.append(entity)
            terms.extend(candidates)
            terms.extend(entity.related_terms)

    q.entities = _dedupe_entities(found)
    q.expanded_terms = _dedupe_terms([q.text] + terms)
    return q


def match_entities(hit_text: str, entities: list[Entity]) -> list[str]:
    haystack = hit_text.lower()
    matched: list[str] = []
    for entity in entities:
        candidates = entity.names + entity.aliases + entity.codes + entity.related_terms
        if any(candidate and candidate.lower() in haystack for candidate in candidates):
            matched.append(entity.canonical_id)
    return sorted(set(matched))


def _dedupe_entities(entities: list[Entity]) -> list[Entity]:
    seen: set[str] = set()
    out: list[Entity] = []
    for entity in entities:
        if entity.canonical_id not in seen:
            seen.add(entity.canonical_id)
            out.append(entity)
    return out


def _dedupe_terms(terms: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for term in terms:
        key = term.lower()
        if term and key not in seen:
            seen.add(key)
            out.append(term)
    return out
