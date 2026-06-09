from __future__ import annotations

import os
import urllib.error
import urllib.request
from typing import Mapping, Optional


def open_url(req: urllib.request.Request, timeout: int, proxy_url: Optional[str] = None, disable_proxy: bool = False):
    if proxy_url:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
        )
        return opener.open(req, timeout=timeout)
    if disable_proxy:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        return opener.open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout)


def bool_env(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def apply_auto_proxy_settings(proxies: Optional[Mapping[str, str]] = None) -> dict[str, Optional[str]]:
    if not bool_env("IR_SEARCH_AUTO_PROXY", True):
        return {"BOCHA_PROXY": os.environ.get("BOCHA_PROXY"), "EXA_PROXY": os.environ.get("EXA_PROXY")}

    detected = dict(proxies) if proxies is not None else detect_system_proxies()

    if not os.environ.get("BOCHA_PROXY"):
        bocha_proxy = _first_env(["IR_SEARCH_CN_PROXY", "CN_PROXY"])
        if not bocha_proxy and bool_env("BOCHA_AUTO_PROXY", False):
            bocha_proxy = choose_proxy(detected)
        if bocha_proxy:
            os.environ["BOCHA_PROXY"] = bocha_proxy

    if not os.environ.get("EXA_PROXY") and not bool_env("EXA_DISABLE_SYSTEM_PROXY", False):
        exa_proxy = _first_env(["IR_SEARCH_OVERSEAS_PROXY", "OVERSEAS_PROXY"])
        if not exa_proxy:
            exa_proxy = choose_proxy(detected)
        if exa_proxy:
            os.environ["EXA_PROXY"] = exa_proxy

    return {"BOCHA_PROXY": os.environ.get("BOCHA_PROXY"), "EXA_PROXY": os.environ.get("EXA_PROXY")}


def detect_system_proxies() -> dict[str, str]:
    return {key.lower(): value for key, value in urllib.request.getproxies().items() if value}


def choose_proxy(proxies: Mapping[str, str]) -> Optional[str]:
    for key in ["https", "http", "all"]:
        value = proxies.get(key)
        if value:
            return value
    return None


def _first_env(names: list[str]) -> Optional[str]:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def http_error_message(prefix: str, exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", errors="replace")[:1000]
    except Exception:
        body = ""
    if body:
        return f"{prefix}: HTTP {exc.code}: {body}"
    return f"{prefix}: HTTP {exc.code}"
