"""Read a literal env file without shell execution, interpolation or global mutation."""
from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Mapping, Optional
import json


class SourceConfigError(ValueError):
    """A stable code plus, when known, the env key to fix. Never carries a value."""

    def __init__(self, code="invalid_source_config", key=None):
        self.code = code
        self.key = key if isinstance(key, str) and re.fullmatch(r"[A-Z][A-Z0-9_]*", key) else None
        super().__init__(code)


def _absolute_elsewhere(value):
    """True for a path that is absolute on another operating system but not on this one."""
    if os.name == "nt":
        return value.startswith("/") and not value.startswith("//")
    return bool(re.match(r"[A-Za-z]:", value)) or value.startswith(("\\\\", "//"))


def require_local_path(values, *keys, allow_relative=False):
    """Private paths are per computer; name the key when one was copied from another OS.

    `/Users/me/cache` is absolute on macOS but not on Windows. `~/...` works on both, and
    leaving a cache/state key empty selects the documented default.
    """
    for key in keys:
        value = values.get(key, "")
        if not isinstance(value, str) or not value:
            continue
        try:
            foreign = _absolute_elsewhere(value)
            if os.name == "nt" and (re.match(r"[A-Za-z]:(?![/\\])", value)
                                    or re.match(r"~[^/\\]", value)):
                # Windows expanduser invents sibling home paths without checking the user.
                # Only the current user's ~ is portable; drive-relative paths depend on cwd.
                foreign = True
            expanded = Path(value).expanduser()
            if foreign or not (allow_relative or expanded.is_absolute()):
                raise ValueError()
        except (OSError, ValueError, RuntimeError):
            raise SourceConfigError("path_not_absolute_on_this_platform", key) from None


def _error_row(provider, exc):
    row = dict(provider=provider, configured=False, access="unknown", code=exc.code)
    if getattr(exc, "key", None):
        row["key"] = exc.key
    return row


def credentials_path(path=None) -> Path:
    """Explicit path, process-selected file, then checkout/cwd credentials.env."""
    if path is not None:
        return Path(path).expanduser()
    if os.environ.get("IR_SEARCH_CREDENTIALS_FILE"):
        return Path(os.environ["IR_SEARCH_CREDENTIALS_FILE"]).expanduser()
    checkout = Path(__file__).resolve().parents[2]
    return (checkout if (checkout / "pyproject.toml").is_file() else Path.cwd()) / "credentials.env"


def credentials_file_state(path=None) -> dict:
    """Whether a credentials file was found, without reading it or revealing its location."""
    explicit = path is not None or bool(os.environ.get("IR_SEARCH_CREDENTIALS_FILE"))
    try:
        found = credentials_path(path).is_file()
    except (OSError, ValueError, RuntimeError):
        found = False
    return {"selection": "explicit_path" if explicit else "default_location", "found": found}


def setup_hint(path=None) -> str:
    """One fixed sentence for callers on a computer where nothing is enabled yet."""
    state = credentials_file_state(path)
    if not state["found"]:
        return ("credentials_file=not_found; copy credentials.env.example to a private location, set "
                "IR_SEARCH_CREDENTIALS_FILE to its absolute path, then enable sources with <SOURCE>_ENABLED=true")
    return ("credentials_file=found; no source is enabled for this operation: set <SOURCE>_ENABLED=true "
            "for the sources the user selects, then run ir-search-doctor to check keys and paths")


def read_credentials(path=None) -> dict[str, str]:
    """Return private values. Never log the result; missing default file is allowed."""
    try:
        target = credentials_path(path)
    except (ValueError, OSError, RuntimeError):
        raise SourceConfigError("invalid_credentials_path", "IR_SEARCH_CREDENTIALS_FILE") from None
    try:
        if target.is_symlink():
            raise SourceConfigError("credentials_file_unreadable")
        fd = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        if path is not None or os.environ.get("IR_SEARCH_CREDENTIALS_FILE"):
            raise SourceConfigError("credentials_file_missing") from None
        return {}
    except OSError:
        raise SourceConfigError("credentials_file_unreadable") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
            raise SourceConfigError("invalid_credentials_file")
        if os.name == "posix" and (info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise SourceConfigError("credentials_permissions_unsafe")
        with os.fdopen(fd, "r", encoding="utf-8-sig") as stream:
            fd = None
            text = stream.read(65537)
    except (UnicodeError, OSError):
        raise SourceConfigError("credentials_file_unreadable") from None
    finally:
        if fd is not None:
            os.close(fd)
    result = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or key in result:
            raise SourceConfigError("invalid_credentials_syntax")
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise SourceConfigError("invalid_credentials_syntax")
            value = value[1:-1]
        if "\x00" in value:
            raise SourceConfigError("invalid_credentials_syntax")
        result[key] = value
    return result


@dataclass(frozen=True)
class MySQLProfile:
    provider: str
    host: str = field(repr=False)
    database: str = field(repr=False)
    user: str = field(repr=False)
    password: str = field(repr=False)
    port: int = 3306
    ssl_ca: Optional[str] = field(default=None, repr=False)
    tls_mode: str = "verify_identity"
    ca_sha256: Optional[str] = field(default=None, repr=False)
    volume_multiplier: Optional[Decimal] = None
    amount_multiplier: Optional[Decimal] = None
    derivatives_amount_multiplier: Optional[Decimal] = None
    financial_currency: Optional[str] = None


def mysql_profile(provider: str, *, values: Optional[Mapping[str, str]] = None, env_file=None, datasets=None) -> Optional[MySQLProfile]:
    """Build an explicitly enabled profile; no cross-provider credential fallback."""
    if provider not in {"wind_mysql", "jydb"}:
        raise SourceConfigError()
    values = read_credentials(env_file) if values is None else values
    prefix = "WIND_MYSQL_" if provider == "wind_mysql" else "JYDB_MYSQL_"
    enabled = values.get(prefix + "ENABLED", "false").lower()
    if enabled not in {"true", "false"}:
        raise SourceConfigError()
    if enabled == "false":
        return None
    required = [values.get(prefix + key, "") for key in ("HOST", "DATABASE", "USER", "PASSWORD")]
    for key, value in zip(("HOST", "DATABASE", "USER", "PASSWORD"), required):
        if not value:
            raise SourceConfigError("source_credentials_missing", prefix + key)
    if any(not isinstance(v, str) or "\x00" in v or "\n" in v for v in required):
        raise SourceConfigError()
    if any(v in {"未配置", "请填写", "YOUR_USERNAME", "YOUR_PASSWORD"}
           or v.startswith(("${", "$WIND_", "$JYDB_", "$ASHARE_")) for v in required):
        raise SourceConfigError("source_credentials_missing")
    config_key = "PORT"
    try:
        port = int(values.get(prefix + "PORT", "3306"))
        if not 1 <= port <= 65535:
            raise ValueError()
        multipliers = []
        for key in ("VOLUME_MULTIPLIER", "AMOUNT_MULTIPLIER", "DERIVATIVES_AMOUNT_MULTIPLIER"):
            config_key = key
            scope = {"VOLUME_MULTIPLIER": {"prices_daily"}, "AMOUNT_MULTIPLIER": {"prices_daily"},
                     "DERIVATIVES_AMOUNT_MULTIPLIER": {"futures_daily", "options_daily"}}[key]
            raw = values.get(prefix + key, "") if datasets is None or scope.intersection(datasets) else ""
            value = Decimal(raw) if raw else None
            if value is not None and (not value.is_finite() or value <= 0 or value > 1000000000):
                raise ValueError()
            multipliers.append(value)
    except (ValueError, InvalidOperation):
        raise SourceConfigError("invalid_source_config", prefix + config_key) from None
    tls_mode = values.get(prefix + "TLS_MODE", "verify_identity")
    ca = values.get(prefix + "SSL_CA") or None
    fingerprint = values.get(prefix + "SSL_CA_SHA256") or None
    if tls_mode not in {"verify_identity", "pinned_ca", "disabled"}:
        raise SourceConfigError("invalid_tls_config", prefix + "TLS_MODE")
    if tls_mode == "disabled" and (provider != "wind_mysql" or ca or fingerprint):
        raise SourceConfigError("invalid_tls_config")
    if tls_mode == "pinned_ca" and (provider != "jydb" or not ca or not fingerprint or not re.fullmatch(r"[a-fA-F0-9]{64}", fingerprint)):
        raise SourceConfigError("invalid_tls_config")
    currency = (values.get(prefix + 'FINANCIAL_CURRENCY') or None) if datasets is None or 'financial_statements' in datasets else None
    if currency is not None and not re.fullmatch('[A-Z]{3}', currency):
        raise SourceConfigError('invalid_source_config', prefix + 'FINANCIAL_CURRENCY')
    return MySQLProfile(provider, *required, port=port, ssl_ca=ca, tls_mode=tls_mode,
                        ca_sha256=fingerprint, volume_multiplier=multipliers[0], amount_multiplier=multipliers[1],
                        derivatives_amount_multiplier=multipliers[2], financial_currency=currency)


@dataclass(frozen=True)
class FMPProfile:
    """Private FMP credentials and bounded, account-local request settings."""
    api_key: str = field(repr=False)
    max_requests_per_query: int = 5
    annual_record_limit: int = 5
    cache_ttl_seconds: int = 60

    def __post_init__(self):
        if (not isinstance(self.api_key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,256}", self.api_key)
                or self.api_key.lower() in {"your_api_key", "your_fmp_api_key", "changeme", "placeholder"}):
            raise SourceConfigError("source_credentials_missing")
        for value, lower, upper in ((self.max_requests_per_query, 1, 25),
                                    (self.annual_record_limit, 1, 100), (self.cache_ttl_seconds, 0, 3600)):
            if type(value) is not int or not lower <= value <= upper:
                raise SourceConfigError()


def fmp_profile(*, values: Optional[Mapping[str, str]] = None, env_file=None) -> Optional[FMPProfile]:
    """Build only an explicitly enabled FMP profile; a plan name grants no access."""
    values = read_credentials(env_file) if values is None else values
    enabled = values.get("FMP_ENABLED", "false").lower()
    if enabled not in {"true", "false"}:
        raise SourceConfigError()
    if enabled == "false":
        return None
    try:
        return FMPProfile(values.get("FMP_API_KEY", ""),
                          int(values.get("FMP_MAX_REQUESTS_PER_QUERY", "5")),
                          int(values.get("FMP_ANNUAL_RECORD_LIMIT", "5")),
                          int(values.get("FMP_CACHE_TTL_SECONDS", "60")))
    except (ValueError, TypeError) as exc:
        raise SourceConfigError(exc.code if isinstance(exc, SourceConfigError) else "invalid_source_config") from None


@dataclass(frozen=True)
class TushareCorpusProfile:
    """Credentials for the official corpus MCP only; never the legacy HTTP API."""
    token: str = field(repr=False)
    max_calls_per_query: int = 3
    news_source: str = "新浪财经"

    def __post_init__(self):
        if (not isinstance(self.token, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{8,512}", self.token)
                or self.token.lower() in {"your_token", "changeme", "placeholder"}):
            raise SourceConfigError("source_credentials_missing")
        if type(self.max_calls_per_query) is not int or not 1 <= self.max_calls_per_query <= 3:
            raise SourceConfigError()
        if self.news_source not in {"新华网", "凤凰财经", "同花顺", "新浪财经", "华尔街见闻", "中证网", "财新网", "第一财经", "财联社"}:
            raise SourceConfigError()


def tushare_corpus_profile(*, values=None, env_file=None) -> Optional[TushareCorpusProfile]:
    """Load only explicitly enabled corpus settings; no token aliases or fallback."""
    values = read_credentials(env_file) if values is None else values
    enabled = values.get("TUSHARE_CORPUS_ENABLED", "false").lower()
    if enabled not in {"true", "false"}:
        raise SourceConfigError()
    if enabled == "false":
        return None
    try:
        return TushareCorpusProfile(values.get("TUSHARE_MCP_TOKEN", ""),
            int(values.get("TUSHARE_CORPUS_MAX_CALLS_PER_QUERY", "3")),
            values.get("TUSHARE_CORPUS_NEWS_SOURCE", "新浪财经"))
    except (ValueError, TypeError) as exc:
        raise SourceConfigError(exc.code if isinstance(exc, SourceConfigError) else "invalid_source_config") from None


@dataclass(frozen=True)
class WebMaterialProfile:
    """Opt-in public web discovery; credentials only go to the fixed search API."""
    api_key: str = field(default="", repr=False)
    allow_anonymous: bool = False
    allowed_domains: tuple[str, ...] = ()
    search_provider: str = "anysearch"
    bocha_api_key: str = field(default="", repr=False)
    exa_api_key: str = field(default="", repr=False)
    overseas_provider: str = 'anysearch'

    def __post_init__(self):
        for key in (self.api_key, self.bocha_api_key, self.exa_api_key):
            if not isinstance(key, str) or (key and not re.fullmatch(r"[A-Za-z0-9_.-]{8,512}", key)):
                raise SourceConfigError("invalid_source_config")
        if self.search_provider not in {"anysearch", "bocha", "exa", "regional"} or self.overseas_provider not in {'anysearch', 'exa'}:
            raise SourceConfigError()
        if type(self.allow_anonymous) is not bool:
            raise SourceConfigError()
        ready = {"anysearch": bool(self.api_key or self.allow_anonymous), "bocha": bool(self.bocha_api_key), 'exa': bool(self.exa_api_key)}
        configured = (ready['bocha'] or ready[self.overseas_provider]) if self.search_provider == "regional" else ready[self.search_provider]
        if not configured:
            raise SourceConfigError("source_credentials_missing")
        if not isinstance(self.allowed_domains, tuple) or len(self.allowed_domains) > 10:
            raise SourceConfigError()
        if any(not isinstance(d, str) or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}", d)
               or len(d) > 253 or ".." in d for d in self.allowed_domains):
            raise SourceConfigError()


def web_material_profile(*, values=None, env_file=None) -> Optional[WebMaterialProfile]:
    """Configure the independent web material adapter without changing process env."""
    values = read_credentials(env_file) if values is None else values
    enabled = values.get("WEB_MATERIALS_ENABLED", "false").lower()
    anonymous = values.get("WEB_ALLOW_ANONYMOUS", "false").lower()
    if enabled not in {"true", "false"}:
        raise SourceConfigError()
    if enabled == "false":
        return None
    if anonymous not in {"true", "false"}:
        raise SourceConfigError()
    domains = tuple(dict.fromkeys(d.strip().lower() for d in values.get("WEB_ALLOWED_DOMAINS", "").split(",") if d.strip()))
    return WebMaterialProfile(values.get("ANYSEARCH_API_KEY", ""), anonymous == "true", domains,
                              values.get("WEB_SEARCH_PROVIDER", "anysearch"), values.get("BOCHA_API_KEY", ""),
                              values.get('EXA_API_KEY', ''), values.get('WEB_OVERSEAS_PROVIDER', 'anysearch'))


@dataclass(frozen=True)
class ZsxqProfile:
    """Official MCP bearer credential, private collections and finite read budgets."""
    token: str = field(repr=False)
    group_ids: tuple[str, ...] = ()
    max_groups: int = 3
    max_pages_per_group: int = 2
    comments_per_topic: int = 0

    def __post_init__(self):
        if not isinstance(self.token, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{8,2048}", self.token):
            raise SourceConfigError("source_credentials_missing" if not self.token else "invalid_source_config")
        if (not isinstance(self.group_ids, tuple) or len(self.group_ids) > 50
                or any(not isinstance(v, str) or not re.fullmatch(r"[1-9][0-9]{0,29}", v) for v in self.group_ids)):
            raise SourceConfigError()
        for value, minimum, maximum in ((self.max_groups, 1, 5), (self.max_pages_per_group, 1, 3), (self.comments_per_topic, 0, 10)):
            if type(value) is not int or not minimum <= value <= maximum:
                raise SourceConfigError()


def zsxq_profile(*, values=None, env_file=None) -> Optional[ZsxqProfile]:
    """Load opt-in official access; never fall back to browser cookies or a CLI."""
    values = read_credentials(env_file) if values is None else values
    enabled = values.get("ZSXQ_MATERIALS_ENABLED", "false").lower()
    if enabled not in {"true", "false"}:
        raise SourceConfigError()
    if enabled == "false":
        return None
    try:
        return ZsxqProfile(values.get("ZSXQ_KEY", ""),
            tuple(dict.fromkeys(v.strip() for v in values.get("ZSXQ_GROUP_IDS", "").split(",") if v.strip())),
            int(values.get("ZSXQ_MAX_GROUPS_PER_QUERY", "3")), int(values.get("ZSXQ_MAX_PAGES_PER_GROUP", "2")),
            int(values.get("ZSXQ_COMMENTS_PER_TOPIC", "0")))
    except (ValueError, TypeError) as exc:
        raise SourceConfigError(exc.code if isinstance(exc, SourceConfigError) else "invalid_source_config") from None


@dataclass(frozen=True)
class WechatAccount:
    name: str
    ghid: str = ""

    def __post_init__(self):
        if not isinstance(self.name, str) or not 1 <= len(self.name.strip()) <= 100 or any(ord(c) < 32 for c in self.name):
            raise SourceConfigError("invalid_wechat_accounts")
        if not isinstance(self.ghid, str) or (self.ghid and not re.fullmatch(r"gh_[A-Za-z0-9_-]{1,80}", self.ghid)):
            raise SourceConfigError("invalid_wechat_accounts")


@dataclass(frozen=True)
class WechatProfile:
    api_key: str = field(repr=False)
    accounts: tuple[WechatAccount, ...] = field(repr=False)
    max_accounts_per_query: int = 3
    max_pages_per_account: int = 2
    cache_dir: Optional[str] = field(default=None, repr=False)

    def __post_init__(self):
        if self.cache_dir is not None and (not isinstance(self.cache_dir, str) or not self.cache_dir.strip() or '\x00' in self.cache_dir):
            raise SourceConfigError('invalid_source_config')
        if (not isinstance(self.api_key, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{8,512}", self.api_key)
                or self.api_key.lower() in {"your_api_key", "changeme", "placeholder"}):
            raise SourceConfigError("source_credentials_missing")
        if not isinstance(self.accounts, tuple) or not 1 <= len(self.accounts) <= 500 or any(not isinstance(a, WechatAccount) for a in self.accounts):
            raise SourceConfigError("invalid_wechat_accounts")
        if len({a.name for a in self.accounts}) != len(self.accounts) or len({a.ghid for a in self.accounts if a.ghid}) != sum(bool(a.ghid) for a in self.accounts):
            raise SourceConfigError("invalid_wechat_accounts")
        for value, upper in ((self.max_accounts_per_query, 5), (self.max_pages_per_account, 3)):
            if type(value) is not int or not 1 <= value <= upper: raise SourceConfigError()


def wechat_profile(*, values=None, env_file=None) -> Optional[WechatProfile]:
    """Load opt-in WeChat settings and a private, env-file-relative account inventory."""
    values = read_credentials(env_file) if values is None else values
    enabled = values.get("WECHAT_MATERIALS_ENABLED", "false").lower()
    if enabled not in {"true", "false"}: raise SourceConfigError()
    if enabled == "false": return None
    raw_path = values.get("WECHAT_ACCOUNTS_FILE", "")
    if not raw_path: raise SourceConfigError("wechat_accounts_missing", "WECHAT_ACCOUNTS_FILE")
    require_local_path(values, "WECHAT_ACCOUNTS_FILE", "WECHAT_CACHE_DIR", allow_relative=True)
    path = Path(raw_path).expanduser()
    if not path.is_absolute(): path = credentials_path(env_file).absolute().parent / path
    fd = None
    try:
        if path.is_symlink(): raise SourceConfigError("invalid_wechat_accounts")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 262144: raise SourceConfigError("invalid_wechat_accounts")
        if os.name == "posix" and (info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise SourceConfigError("wechat_accounts_permissions_unsafe")
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            fd = None
            rows = json.load(stream)
        if not isinstance(rows, list) or any(not isinstance(row, dict) or set(row) - {"name", "ghid"} for row in rows):
            raise SourceConfigError("invalid_wechat_accounts")
        accounts = tuple(WechatAccount(**row) for row in rows)
        if not accounts or len(accounts) > 500 or len({a.name for a in accounts}) != len(accounts) or len({a.ghid for a in accounts if a.ghid}) != sum(bool(a.ghid) for a in accounts):
            raise SourceConfigError("invalid_wechat_accounts")
    except SourceConfigError as exc:
        # Every failure here concerns the private inventory file; point at its key.
        raise SourceConfigError(exc.code, exc.key or "WECHAT_ACCOUNTS_FILE") from None
    except FileNotFoundError:
        raise SourceConfigError("wechat_accounts_file_not_found", "WECHAT_ACCOUNTS_FILE") from None
    except (OSError, UnicodeError, ValueError, TypeError):
        raise SourceConfigError("invalid_wechat_accounts", "WECHAT_ACCOUNTS_FILE") from None
    finally:
        if fd is not None: os.close(fd)

    budgets = []
    for key, default, upper in (("WECHAT_MAX_ACCOUNTS_PER_QUERY", "3", 5), ("WECHAT_MAX_PAGES_PER_ACCOUNT", "2", 3)):
        try:
            value = int(values.get(key, default))
            if not 1 <= value <= upper: raise ValueError()
        except (ValueError, TypeError):
            raise SourceConfigError("invalid_source_config", key) from None
        budgets.append(value)
    cache_dir = Path(values.get('WECHAT_CACHE_DIR') or '.local/wechat-cache').expanduser()
    if not cache_dir.is_absolute(): cache_dir = credentials_path(env_file).absolute().parent / cache_dir
    try:
        return WechatProfile(values.get("DAJIALA_KEY", ""), accounts, *budgets, str(cache_dir))
    except SourceConfigError as exc:
        raise SourceConfigError(exc.code, "DAJIALA_KEY") from None


@dataclass(frozen=True)
class IMAProfile:
    """Private official OpenAPI credentials; knowledge-base IDs scope discovery only."""
    api_key: str = field(repr=False)
    client_id: str = field(repr=False)
    knowledge_base_ids: tuple[str, ...] = field(default=(), repr=False)
    include_notes: bool = True
    max_knowledge_bases: int = 3
    max_pages: int = 2

    def __post_init__(self):
        for value in (self.api_key, self.client_id):
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_./+=-]{8,1024}", value):
                raise SourceConfigError("source_credentials_missing")
        if (not isinstance(self.knowledge_base_ids, tuple) or len(self.knowledge_base_ids) > 20
                or any(not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9_+=.-]{1,512}", v) for v in self.knowledge_base_ids)
                or len(set(self.knowledge_base_ids)) != len(self.knowledge_base_ids)):
            raise SourceConfigError()
        if type(self.include_notes) is not bool: raise SourceConfigError()
        for value, maximum in ((self.max_knowledge_bases, 3), (self.max_pages, 2)):
            if type(value) is not int or not 1 <= value <= maximum: raise SourceConfigError()


def ima_profile(*, values=None, env_file=None) -> Optional[IMAProfile]:
    """Build an explicitly enabled official IMA profile, without ambient credentials."""
    values = read_credentials(env_file) if values is None else values
    enabled = values.get("IMA_MATERIALS_ENABLED", "false").lower()
    if enabled not in {"true", "false"}: raise SourceConfigError()
    if enabled == "false": return None
    notes = values.get("IMA_INCLUDE_NOTES", "true").lower()
    if notes not in {"true", "false"}: raise SourceConfigError()
    try:
        return IMAProfile(values.get("IMA_API_KEY", ""), values.get("IMA_CLIENT_ID", ""),
            tuple(v.strip() for v in values.get("IMA_KNOWLEDGE_BASE_IDS", "").split(",") if v.strip()),
            notes == "true", int(values.get("IMA_MAX_KNOWLEDGE_BASES", "3")), int(values.get("IMA_MAX_PAGES", "2")))
    except (ValueError, TypeError) as exc:
        raise SourceConfigError(exc.code if isinstance(exc, SourceConfigError) else "invalid_source_config") from None


@dataclass(frozen=True)
class WisburgProfile:
    """Private official MCP key and bounded stored-material reads."""
    api_key: str = field(repr=False)
    max_calls_per_query: int = 20
    max_pages_per_category: int = 1

    def __post_init__(self):
        if (not isinstance(self.api_key, str) or not re.fullmatch(r'[A-Za-z0-9_.~+/=-]{8,2048}', self.api_key)
                or self.api_key.lower() in {'your_api_key', 'changeme', 'placeholder'}):
            raise SourceConfigError('source_credentials_missing')
        if (type(self.max_calls_per_query) is not int or not 1 <= self.max_calls_per_query <= 40
                or type(self.max_pages_per_category) is not int or not 1 <= self.max_pages_per_category <= 2):
            raise SourceConfigError()


def wisburg_profile(*, values=None, env_file=None) -> Optional[WisburgProfile]:
    """Use explicit enablement; accept the existing local WISBERG_KEY alias."""
    values = read_credentials(env_file) if values is None else values
    enabled = values.get('WISBURG_MATERIALS_ENABLED', 'false').lower()
    if enabled not in {'true', 'false'}: raise SourceConfigError()
    if enabled == 'false': return None
    keys = {v for v in (values.get('WISBURG_API_KEY'), values.get('WISBERG_KEY')) if v}
    if len(keys) > 1: raise SourceConfigError('conflicting_wisburg_credentials')
    try:
        return WisburgProfile(next(iter(keys), ''), int(values.get('WISBURG_MAX_CALLS_PER_QUERY', '20')),
            int(values.get('WISBURG_MAX_PAGES_PER_CATEGORY', '1')))
    except (ValueError, TypeError) as exc:
        if isinstance(exc, SourceConfigError): raise
        raise SourceConfigError() from None


def source_configuration_status(*, env_file=None) -> dict:
    """Report only safe booleans and stable codes; this does not contact databases."""
    try:
        values = read_credentials(env_file)
    except SourceConfigError as exc:
        return {"verification_basis": "local_configuration_only", "sources": [], "diagnostics": [{"code": exc.code}]}
    sources = []
    for provider in ("wind_mysql", "jydb"):
        try:
            profile = mysql_profile(provider, values=values)
            item = {"provider": provider, "enabled": profile is not None, "configured": profile is not None,
                    "access": "unknown", "live_verified": False}
            if profile and provider == "wind_mysql":
                item["volume_unit_configured"] = profile.volume_multiplier is not None
                item["amount_unit_configured"] = profile.amount_multiplier is not None
                item["derivatives_amount_unit_configured"] = profile.derivatives_amount_multiplier is not None
            if profile:
                item["transport_mode"] = profile.tls_mode
                item["transport_encrypted"] = profile.tls_mode != "disabled"
                if profile.ssl_ca and not Path(profile.ssl_ca).expanduser().is_file():
                    # Common after copying credentials.env to another computer.
                    prefix = "WIND_MYSQL_" if provider == "wind_mysql" else "JYDB_MYSQL_"
                    item.update(configured=False, code="ssl_ca_file_missing", key=prefix + "SSL_CA")
            sources.append(item)
        except SourceConfigError as exc:
            sources.append(_error_row(provider, exc))
    try:
        profile = fmp_profile(values=values)
        item = {"provider": "fmp", "enabled": profile is not None, "configured": profile is not None,
                "access": "unknown", "live_verified": False, "transport_encrypted": True}
        if profile:
            item.update(max_requests_per_query=profile.max_requests_per_query,
                        annual_record_limit=profile.annual_record_limit, cache_ttl_seconds=profile.cache_ttl_seconds)
        sources.append(item)
    except SourceConfigError as exc:
        sources.append(_error_row("fmp", exc))
    enabled = values.get("AKSHARE_ENABLED", "false").lower()
    item = {"provider": "akshare", "enabled": enabled == "true", "configured": enabled == "true",
            "requires_key": False, "access": "unknown", "live_verified": False}
    backend = values.get("AKSHARE_STOCK_BACKEND", "eastmoney")
    if backend in {"eastmoney", "sina"}:
        item["stock_backend"] = backend
    else:
        item["code"] = "source_config_error"
        item["configured"] = False
    if enabled not in {"true", "false"}:
        item["code"] = "source_config_error"
    sources.append(item)
    try:
        profile = tushare_corpus_profile(values=values)
        sources.append({"provider": "tushare_corpus", "enabled": profile is not None,
                        "configured": profile is not None, "access": "unknown", "live_verified": False,
                        "transport_encrypted": True})
    except SourceConfigError as exc:
        sources.append(_error_row("tushare_corpus", exc))
    try:
        profile = web_material_profile(values=values)
        item = {"provider": "web", "enabled": profile is not None, "configured": profile is not None,
                "access": "unknown", "live_verified": False}
        if profile:
            item.update(discovery_provider=profile.search_provider,
                        authentication_mode="api_key" if profile.api_key or profile.bocha_api_key or profile.exa_api_key else "anonymous",
                        anysearch_configured=bool(profile.api_key or profile.allow_anonymous),
                        bocha_configured=bool(profile.bocha_api_key), exa_configured=bool(profile.exa_api_key),
                        overseas_provider=profile.overseas_provider,
                        quota_fallback='caller_native_web_search')
        sources.append(item)
    except SourceConfigError as exc:
        sources.append(_error_row("web", exc))
    try:
        profile = zsxq_profile(values=values)
        sources.append({"provider": "zsxq", "enabled": profile is not None, "configured": profile is not None,
                        "access": "unknown", "live_verified": False, "transport_encrypted": True,
                        "authentication_mode": "official_mcp_bearer"})
    except SourceConfigError as exc:
        sources.append(_error_row("zsxq", exc))
    try:
        profile = wechat_profile(values=values, env_file=env_file)
        sources.append({"provider": "wechat", "enabled": profile is not None, "configured": profile is not None,
                        "access": "unknown", "live_verified": False, "transport_encrypted": True,
                        "discovery_provider": "dajiala", "account_count": len(profile.accounts) if profile else 0,
                        "cache_modes": ["use", "refresh", "off"], "body_cache_ttl_seconds": 86400,
                        "history_head_ttl_seconds": 300})
    except SourceConfigError as exc:
        sources.append(_error_row("wechat", exc))
    try:
        profile = ima_profile(values=values)
        sources.append({"provider": "ima", "enabled": profile is not None, "configured": profile is not None,
                        "access": "unknown", "live_verified": False, "transport_encrypted": True,
                        "authentication_mode": "official_openapi", "include_notes": profile.include_notes if profile else False,
                        "configured_knowledge_base_count": len(profile.knowledge_base_ids) if profile else 0})
    except SourceConfigError as exc:
        sources.append(_error_row("ima", exc))
    try:
        profile = wisburg_profile(values=values)
        sources.append({'provider':'wisburg', 'enabled':profile is not None, 'configured':profile is not None,
            'access':'unknown', 'live_verified':False, 'transport_encrypted':True,
            'authentication_mode':'official_mcp_bearer'})
    except SourceConfigError as exc:
        sources.append(_error_row('wisburg', exc))
    from ir_search.adapters.platform_materials import platform_material_profile
    from importlib.util import find_spec
    for provider in ('xueqiu', 'eastmoney', 'video', 'xiaoyuzhou'):
        try:
            profile = platform_material_profile(provider, values=values)
            item = {'provider': provider, 'enabled': profile is not None, 'configured': profile is not None,
                    'access': 'unknown', 'live_verified': False, 'discovery': 'bounded_search_index'}
            if provider == 'xiaoyuzhou':
                from .audio_asr import audio_configuration_status
                item['audio_recognition'] = audio_configuration_status(values=values, env_file=env_file)
            if provider == 'video':
                item['youtube_caption_dependency_installed'] = find_spec('youtube_transcript_api') is not None
                dns_mode = values.get('YOUTUBE_DNS_MODE', 'system')
                if dns_mode not in {'system','google_doh'}: raise SourceConfigError()
                item['youtube_dns_mode'] = dns_mode
                item['bilibili_cookie_configured'] = bool(values.get('BILIBILI_COOKIE'))
            if provider == 'xueqiu':
                from .xueqiu_browser import xueqiu_read_profile
                reader = xueqiu_read_profile(values=values)
                item['cookie_configured'] = bool(values.get('XUEQIU_COOKIE'))
                item['read_mode'] = reader.mode
                item['browser_cookie_mode'] = 'explicit_env' if reader.browser_use_cookie else 'anonymous'
                item['browser_channel'] = reader.browser_channel
                item['browser_headless'] = reader.browser_headless
                item['browser_experimental'] = True
                item['browser_dependency_installed'] = find_spec('playwright') is not None
                item['browser_runtime_verified'] = False
            if profile:
                item.update(bocha_configured=bool(profile.bocha_api_key), anysearch_configured=bool(profile.api_key or profile.allow_anonymous))
            sources.append(item)
        except SourceConfigError as exc:
            sources.append(_error_row(provider, exc))
    try:
        from .sec import sec_profile
        profile = sec_profile(values=values)
        sources.append({'provider': 'sec', 'enabled': profile is not None, 'configured': profile is not None,
            'access': 'unknown', 'live_verified': False, 'requires_key': False, 'transport_encrypted': True,
            'contact_configured': profile is not None})
    except SourceConfigError as exc:
        sources.append(_error_row('sec', exc))
    try:
        from .fiona import fiona_profile
        profile = fiona_profile(values=values)
        sources.append({'provider': 'fiona', 'enabled': profile is not None, 'configured': profile is not None,
            'access': 'unknown', 'live_verified': False, 'authentication_mode': 'fiona_mcp_bearer',
            'transport_encrypted': True, 'integration_stage': 'get_data_adapter'})
    except SourceConfigError as exc:
        sources.append(_error_row('fiona', exc))
    try:
        from .alphapai import alphapai_profile
        profile = alphapai_profile(values=values)
        sources.append({'provider':'alphapai', 'enabled':profile is not None, 'configured':profile is not None,
            'access':'unknown', 'live_verified':False, 'transport_encrypted':True,
            'authentication_mode':'account_browser', 'requires_api_key':False,
            'browser_dependency_installed':find_spec('playwright') is not None,
            'browser_runtime_verified':False, 'collection':'shared_meetings',
            'personal_recordings_enabled':False, 'cache_ttl_seconds':profile.cache_ttl_seconds if profile else None})
    except SourceConfigError as exc:
        sources.append(_error_row('alphapai', exc))
    try:
        from .gangtise import gangtise_profile
        profile = gangtise_profile(values=values)
        sources.append({'provider':'gangtise', 'enabled':profile is not None, 'configured':profile is not None,
            'access':'unknown', 'live_verified':False, 'transport_encrypted':True,
            'authentication_mode':'account_session_with_optional_browser_verification', 'requires_api_key':False,
            'browser_dependency_installed':find_spec('playwright') is not None,
            'browser_runtime_verified':False, 'session_verified':False,
            'collections':['summary','report','opinion'], 'official_ak_sk_mcp_enabled':False})
    except SourceConfigError as exc:
        sources.append(_error_row('gangtise', exc))
    try:
        from .xhs import xhs_profile
        profile = xhs_profile(values=values, env_file=env_file)
        sources.append({'provider':'xhs','enabled':profile is not None,'configured':profile is not None,
            'access':'unknown','live_verified':False,'backend':'optional_local_xiaohongshu_mcp_http',
            'authentication_mode':'private_local_bearer_and_user_browser_session',
            'backend_live_verified':False,'login_live_verified':False,'read_only':True})
    except SourceConfigError as exc:
        sources.append(_error_row('xhs', exc))
    try:
        from .rss import rss_profile
        profile = rss_profile(values=values)
        sources.append({'provider': 'rss', 'enabled': profile is not None, 'configured': profile is not None,
            'access': 'unknown', 'live_verified': False, 'requires_key': False,
            'feed_count': len(profile.feed_urls) if profile else 0})
    except SourceConfigError as exc:
        sources.append(_error_row('rss', exc))
    for provider, key in (('global_macro','GLOBAL_MACRO_ENABLED'), ('hkex','HKEX_ENABLED'), ('company_ir','COMPANY_IR_ENABLED')):
        enabled = values.get(key, 'false').lower()
        sources.append({'provider':provider, 'enabled':enabled=='true', 'configured':enabled=='true',
            'access':'unknown', 'live_verified':False, 'authentication_mode':'official_public_no_key',
            **({'code':'source_config_error'} if enabled not in {'true','false'} else {})})
    return {"verification_basis": "local_configuration_only", "sources": sources, "diagnostics": []}
