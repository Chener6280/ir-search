"""Explicit or disclosed deterministic region selection, independent of query language."""
from __future__ import annotations

from dataclasses import dataclass
import re

from ir_search.contracts.materials import MaterialSearchRequest
from ir_search.infrastructure.credentials import WebMaterialProfile
from ir_search.institutions import _match_institutions, _selected_institutions


@dataclass(frozen=True)
class WebRoute:
    provider: str
    region: str
    basis: str


def route_web_search(request: MaterialSearchRequest, profile: WebMaterialProfile) -> tuple[WebRoute, ...]:
    """Prefer explicit scope; expose market/entity rules and weak language defaults."""
    if not isinstance(request, MaterialSearchRequest) or not isinstance(profile, WebMaterialProfile):
        raise ValueError("Typed material request and web profile required")
    if profile.search_provider != "regional":
        region = request.web_region if request.web_region in {"cn", "overseas"} else "cn" if profile.search_provider == "bocha" else "overseas"
        return (WebRoute(profile.search_provider, region, "configured_provider"),)
    if request.web_region != "auto":
        regions = ("cn", "overseas") if request.web_region == "both" else (request.web_region,)
        basis = "explicit_request"
    else:
        text = " ".join((request.question, *request.keywords, *request.entities)).lower()
        cn = any(re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", s.upper()) for s in request.symbols)
        overseas = any(re.fullmatch(r"(?:\d{4,5}\.HK|[A-Z][A-Z0-9-]{0,7}(?:\.(?:US|HK|L|T))?)", s.upper()) for s in request.symbols)
        cn |= bool(re.search(r"中国|国内|大陆|沪深|a股|\b(?:china|chinese|mainland|a-shares?)\b", text))
        overseas |= bool(re.search(r"海外|国外|全球|国际|美国|美股|欧洲|欧盟|英国|日本|韩国|香港|港股|台湾|台股|美联储|英伟达|微软|特斯拉|亚马逊|谷歌|苹果公司|英特尔|\b(?:overseas|global|international|usa?|u\.s\.|europe|japan|hong kong|nvidia|tesla|microsoft|amazon|alphabet|apple|intel)\b", text))
        cn |= any(d.endswith(".cn") or d == "cn" for d in profile.allowed_domains)
        overseas |= any(d.endswith(".gov") for d in profile.allowed_domains)
        institutions = (_selected_institutions(request.web_institutions) if request.web_institutions
                        else _match_institutions(request.question, request.entities))
        cn |= any(row.region == 'cn' for row in institutions)
        overseas |= any(row.region == 'overseas' for row in institutions)
        regions = tuple(r for r, found in (("cn", cn), ("overseas", overseas)) if found)
        basis = "market_entity_rules"
        if not regions:
            regions = ("cn",) if re.search(r"[\u3400-\u9fff]", text) else ("overseas",)
            basis = "language_default_unverified"
    return tuple(WebRoute("bocha" if r == "cn" else profile.overseas_provider, r, basis) for r in regions)
