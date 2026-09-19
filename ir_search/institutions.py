"""Small, reviewed institution catalog; lexical hints, never identity verification.

This catalog belongs to the core material service and is not loaded by legacy
entity normalization. Official domains constrain discovery only when requested.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
import re
from urllib.parse import urlsplit

from .contracts import JsonModel


@dataclass(frozen=True)
class Institution(JsonModel):
    institution_id: str
    name: str
    aliases: tuple[str, ...]
    category: str
    region: str
    domains: tuple[str, ...]
    source_url: str
    reviewed_on: date
    directory_urls: tuple[str, ...] = ()


@lru_cache(maxsize=1)
def _catalog() -> tuple[Institution, ...]:
    with (Path(__file__).parent / 'entities' / 'institutions.csv').open(encoding='utf-8', newline='') as stream:
        rows = tuple(Institution(row['institution_id'], row['name'], tuple(filter(None, row['aliases'].split('|'))),
            row['category'], row['region'], tuple(row['domains'].split('|')), row['source_url'],
            date.fromisoformat(row['reviewed_on']), tuple(filter(None, row['directory_urls'].split('|'))))
            for row in csv.DictReader(stream))
    if len({row.institution_id for row in rows}) != len(rows):
        raise ValueError('Duplicate packaged institution')
    for row in rows:
        if (not re.fullmatch(r'[a-z][a-z0-9_]*', row.institution_id) or not row.name
                or row.region not in {'cn', 'overseas'}
                or row.category not in {'regulator', 'fund_manager', 'bank', 'securities'}
                or any(not re.fullmatch(r'[a-z0-9]+(?:[.-][a-z0-9]+)*\.[a-z]{2,}', d) for d in row.domains)
                or any(urlsplit(url).scheme != 'https' or not _host_matches(urlsplit(url).hostname or '', row.domains)
                       for url in (row.source_url, *row.directory_urls))):
            raise ValueError('Invalid packaged institution')
    return rows


def list_institutions() -> dict:
    """Return offline catalog metadata, including review dates and official references."""
    return {'schema_version': '1.0', 'coverage': 'curated_seed_not_exhaustive',
            'verification_basis': 'reviewed_official_website_metadata_not_live_probe',
            'alias_policy': 'curated_search_aliases_not_legal_entity_equivalence',
            'institutions': [row.to_dict() for row in _catalog()]}


def _alias_spans(text, alias):
    if alias.isascii():
        # Short acronyms are case-sensitive: ordinary English "sec" is not SEC.
        flags = 0 if alias.isupper() and len(alias) <= 4 else re.I
        return tuple(m.span() for m in re.finditer(r'(?<![A-Za-z0-9_])' + re.escape(alias) + r'(?![A-Za-z0-9_])', text, flags))
    return tuple(m.span() for m in re.finditer(re.escape(alias), text, re.I))


def _match_institutions(question, entities=()):
    found = set()
    for text in (question, *entities):
        matches = [(row.institution_id, start, end) for row in _catalog() for name in (row.name, *row.aliases)
                   for start, end in _alias_spans(text, name)]
        furthest = {}
        for identifier, start, end in sorted(matches, key=lambda match: (match[1], -match[2], match[0])):
            # "美国证监会" must not also resolve the shorter Chinese "证监会".
            # Sweep containment by institution, avoiding quadratic work on long
            # regulator pages that repeat the same names hundreds of times.
            if not any(other != identifier and right >= end and right-left > end-start
                       for other, (left, right) in furthest.items()):
                found.add(identifier)
            if identifier not in furthest or end > furthest[identifier][1]:
                furthest[identifier] = (start, end)
    return tuple(row for row in _catalog() if row.institution_id in found)


def _host_matches(host, domains):
    host = host.lower().rstrip('.')
    return any(host == domain or host.endswith('.' + domain) for domain in domains)


def _institution_for_url(url):
    parsed = urlsplit(url)
    if parsed.scheme not in {'https', 'http'} or parsed.username or parsed.password:
        return None
    return next((row for row in _catalog() if _host_matches(parsed.hostname or '', row.domains)), None)


def _selected_institutions(ids):
    entries = {row.institution_id: row for row in _catalog()}
    if any(value not in entries for value in ids):
        raise ValueError('Unknown institution ID; inspect list_institutions')
    return tuple(entries[value] for value in ids)
