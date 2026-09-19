"""Official SEC EDGAR access. No API key, browser impersonation or vendor fallback."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import date
from threading import Lock
from urllib.parse import urljoin, urlsplit

from ir_search.documents.html import ArticleHTMLParser
from ir_search.documents.models import Document, hash_bytes, hash_text, make_doc_id
from ir_search.infrastructure.credentials import SourceConfigError, read_credentials
from ir_search.infrastructure.public_web import _request
from ir_search.models import EvidenceType, SourceTier
from ir_search.registry import DataAdapterError

_LOCK = Lock()
_NEXT = 0.0
_ACCESSION = r'[0-9]{10}-[0-9]{2}-[0-9]{6}'
_FILE = r'[A-Za-z0-9][A-Za-z0-9_.-]{0,199}'
_DEFAULT_FORMS = ('10-K', '10-K/A', '10-Q', '10-Q/A', '8-K', '8-K/A',
                  '20-F', '20-F/A', '40-F', '40-F/A', '6-K', '6-K/A')


@dataclass(frozen=True)
class SECProfile:
    user_agent: str = field(repr=False)
    max_history_files: int = 2

    def __post_init__(self):
        if (not isinstance(self.user_agent, str) or not 5 <= len(self.user_agent) <= 256
                or any(ord(c) < 32 or ord(c) > 126 for c in self.user_agent)
                or not re.search(r'[^\s@]+@[^\s@]+\.[A-Za-z]{2,}', self.user_agent)):
            raise SourceConfigError('sec_contact_required')
        if type(self.max_history_files) is not int or not 0 <= self.max_history_files <= 2:
            raise SourceConfigError()


def sec_profile(*, values=None, env_file=None):
    """Opt-in contact header; never include its value in diagnostics."""
    values = read_credentials(env_file) if values is None else values
    enabled = values.get('SEC_MATERIALS_ENABLED', 'false').lower()
    if enabled not in {'true', 'false'}: raise SourceConfigError()
    if enabled == 'false': return None
    try:
        return SECProfile(values.get('SEC_USER_AGENT', ''), int(values.get('SEC_MAX_HISTORY_FILES', '2')))
    except (TypeError, ValueError) as exc:
        if isinstance(exc, SourceConfigError): raise
        raise SourceConfigError() from None


def _cik(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,10}', value) or int(value) == 0:
        raise ValueError('Invalid SEC CIK')
    return value.zfill(10)


def _archive_parts(url):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.netloc != 'www.sec.gov' or parsed.query or parsed.fragment
            or not re.fullmatch(r'/Archives/edgar/data/[1-9][0-9]{0,9}/[0-9]{18}/' + _FILE, parsed.path)):
        raise DataAdapterError('blocked_url')
    cik, accession, filename = parsed.path.split('/')[-3:]
    if (filename in {'index.html', 'index.json', 'index.xml'} or filename.endswith('-index.html')
            or not filename.lower().endswith(('.htm', '.html', '.pdf'))):
        raise DataAdapterError('web_content_unsupported')
    return _cik(cik), accession[:10] + '-' + accession[10:12] + '-' + accession[12:], filename


def _throttle(context):
    # One shared 4 requests/sec ceiling across SEC hosts in this process.
    # Multiple processes/computers still need a caller-owned shared rate budget.
    global _NEXT
    while True:
        context.check_active()
        with _LOCK:
            delay = _NEXT - time.monotonic()
            if delay <= 0:
                _NEXT = time.monotonic() + 0.25
                return
        context._cancelled.wait(min(delay, context.remaining_seconds(), 0.05))


def _fetch(url, profile, context):
    parsed = urlsplit(url)
    allowed = (url == 'https://www.sec.gov/files/company_tickers.json'
               or (parsed.netloc == 'data.sec.gov' and re.fullmatch(
                   r'/submissions/CIK[0-9]{10}(?:-submissions-[0-9]{3})?\.json', parsed.path)))
    if not allowed: _archive_parts(url)
    if parsed.scheme != 'https' or parsed.query or parsed.fragment: raise DataAdapterError('blocked_url')
    _throttle(context)
    try:
        reply = _request(url, context=context, headers={'User-Agent': profile.user_agent,
                         'Accept': 'application/json,text/html,application/pdf'}, max_bytes=24 * 1024 * 1024)
    except DataAdapterError as exc:
        if getattr(exc, 'http_status', None) == 403:
            raise DataAdapterError('sec_access_blocked') from None
        raise
    if reply.status != 200:
        # Do not forward contact headers to a redirect target.
        raise DataAdapterError('web_redirect_limit')
    sample = reply.body[:12000].lower()
    if any(x in sample for x in (b'undeclared automated tool', b'request rate threshold exceeded',
                                 b'your request originates from an undeclared')):
        raise DataAdapterError('sec_access_blocked')
    return reply


def _json(url, profile, context):
    cache = getattr(context, '_sec_json_cache', None)
    if cache is None:
        cache = context._sec_json_cache = {}
    if url in cache: return cache[url]
    reply = _fetch(url, profile, context)
    try:
        value = json.loads(reply.body)
        if not isinstance(value, dict): raise ValueError()
    except (ValueError, UnicodeError): raise DataAdapterError('upstream_schema') from None
    cache[url] = (value, reply.fetched_at)
    return cache[url]


@dataclass(frozen=True)
class SECFiling:
    cik: str
    company: str
    symbols: tuple[str, ...]
    accession: str
    form: str
    filing_date: date
    report_date: str
    accepted_at: str
    primary_document: str
    description: str

    @property
    def url(self):
        return f'https://www.sec.gov/Archives/edgar/data/{int(self.cik)}/{self.accession.replace("-", "")}/{self.primary_document}'

    def _details(self):
        return {'cik': self.cik, 'company': self.company, 'accession_number': self.accession,
                'form': self.form, 'is_amendment': self.form.endswith('/A'),
                'filing_date': self.filing_date.isoformat(), 'report_date': self.report_date or None,
                'acceptance_datetime_raw': self.accepted_at or None,
                'primary_document': self.primary_document, 'primary_document_description': self.description,
                'metadata_basis': 'sec_submissions', 'report_date_is_not_full_business_period': True,
                'filing_index_url': self.url.rsplit('/', 1)[0] + '/' + self.accession + '-index.html'}


def _rows(data, cik, company, symbols):
    required = ('accessionNumber', 'filingDate', 'form', 'primaryDocument')
    if not isinstance(data, dict) or any(not isinstance(data.get(k), list) for k in required):
        raise DataAdapterError('upstream_schema')
    size = len(data['accessionNumber'])
    if size > 20000 or any(len(data[k]) != size for k in required): raise DataAdapterError('upstream_schema')
    optional = ('reportDate', 'acceptanceDateTime', 'primaryDocDescription')
    if any(k in data and (not isinstance(data[k], list) or len(data[k]) != size) for k in optional):
        raise DataAdapterError('upstream_schema')
    result = []
    try:
        for i in range(size):
            row = {k: data[k][i] if k in data else '' for k in (*required, *optional)}
            if any(not isinstance(v, str) or len(v) > 1000 for v in row.values()): raise ValueError()
            if not re.fullmatch(_ACCESSION, row['accessionNumber']): raise ValueError()
            filed = date.fromisoformat(row['filingDate'])
            if row['reportDate']: date.fromisoformat(row['reportDate'])
            if row['primaryDocument'] and not re.fullmatch(r'(?:[A-Za-z0-9_-]+/){0,2}' + _FILE, row['primaryDocument']): raise ValueError()
            result.append(SECFiling(cik, company, symbols, row['accessionNumber'], row['form'], filed,
                row['reportDate'], row['acceptanceDateTime'], row['primaryDocument'], row['primaryDocDescription']))
    except (ValueError, TypeError): raise DataAdapterError('upstream_schema') from None
    return result


def _resolve_symbols(symbols, profile, context):
    if not symbols: return {}
    data, _ = _json('https://www.sec.gov/files/company_tickers.json', profile, context)
    result = {}
    for symbol in symbols:
        matches = set()
        for row in data.values():
            if isinstance(row, dict) and row.get('ticker') == symbol.upper():
                try: matches.add(_cik(str(row['cik_str'])))
                except (KeyError, ValueError): raise DataAdapterError('upstream_schema') from None
        if len(matches) != 1: raise DataAdapterError('sec_symbol_unresolved')
        result[symbol] = matches.pop()
    return result


def _filings(cik, profile, context, start, end):
    data, fetched_at = _json(f'https://data.sec.gov/submissions/CIK{cik}.json', profile, context)
    try:
        if _cik(str(data['cik'])) != cik: raise ValueError()
        if not isinstance(data['tickers'], list): raise ValueError()
        company, symbols = data['name'], tuple(data['tickers'])
        if not isinstance(company, str) or not company or len(company) > 1000: raise ValueError()
        if any(not isinstance(s, str) or len(s) > 100 for s in symbols): raise ValueError()
        rows = _rows(data['filings']['recent'], cik, company, symbols)
        files = data['filings'].get('files', [])
        if not isinstance(files, list) or len(files) > 1000: raise ValueError()
        pending = []
        for item in files:
            if (not re.fullmatch('CIK' + cik + r'-submissions-[0-9]{3}\.json', item['name'])
                    or date.fromisoformat(item['filingFrom']) > date.fromisoformat(item['filingTo'])): raise ValueError()
            if date.fromisoformat(item['filingFrom']) <= end and date.fromisoformat(item['filingTo']) >= start:
                pending.append(item)
    except (KeyError, TypeError, ValueError): raise DataAdapterError('upstream_schema') from None
    pending.sort(key=lambda x: x['filingTo'], reverse=True)
    for item in pending[:profile.max_history_files]:
        historical, _ = _json('https://data.sec.gov/submissions/' + item['name'], profile, context)
        rows.extend(_rows(historical, cik, company, symbols))
    unique = {}
    for row in rows:
        if row.accession in unique and unique[row.accession] != row: raise DataAdapterError('upstream_schema')
        unique[row.accession] = row
    return list(unique.values()), fetched_at, len(pending) > profile.max_history_files


class _FilingHTMLParser(ArticleHTMLParser):
    """Remove Inline XBRL hidden facts and hidden HTML without flattening their numbers into prose."""
    def __init__(self, url):
        super().__init__(url)
        self._hidden = []
        self._fact = None
        self.facts = {}

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        hidden = (bool(self._hidden and self._hidden[-1][1]) or tag in {'ix:hidden', 'ix:header', 'ix:references', 'ix:resources'}
                  or 'hidden' in attr or bool(re.search(r'(?:display\s*:\s*none|visibility\s*:\s*hidden)', attr.get('style') or '', re.I)))
        if tag not in {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}:
            self._hidden.append((tag, hidden))
        if tag == 'ix:nonnumeric' and attr.get('name') in {'dei:DocumentType', 'dei:DocumentPeriodEndDate', 'dei:EntityRegistrantName'}:
            self._fact = [attr['name'], '']
        if not hidden: super().handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        hidden = bool(self._hidden and self._hidden[-1][1])
        if tag == 'ix:nonnumeric' and self._fact:
            self.facts[self._fact[0]] = self._fact[1].strip()[:1000]
            self._fact = None
        if not hidden: super().handle_endtag(tag)
        for i in range(len(self._hidden) - 1, -1, -1):
            if self._hidden[i][0] == tag:
                del self._hidden[i:]
                break

    def handle_data(self, data):
        if self._fact: self._fact[1] += data[:1000]
        if not (self._hidden and self._hidden[-1][1]): super().handle_data(data)


def read_sec_document(url, *, context, max_chars=20000, profile=None, filing=None):
    """Read an exact SEC archive primary document/exhibit; preserve raw and text hashes."""
    if type(max_chars) is not int or not 1 <= max_chars <= 100000: raise ValueError('Invalid text limit')
    cik, accession, filename = _archive_parts(url)
    try: profile = profile if profile is not None else sec_profile()
    except SourceConfigError as exc: raise DataAdapterError('sec_contact_required' if exc.code == 'sec_contact_required' else 'source_config_error') from None
    if profile is None: raise DataAdapterError('source_disabled')
    if filing and filing.url != url: raise DataAdapterError('upstream_schema')
    reply = _fetch(url, profile, context)
    facts = {}
    links = []
    if reply.body.startswith(b'%PDF-'):
        from ir_search.documents.pdf import extract_pdf_document
        document = extract_pdf_document(reply.body, url, max_chars=max_chars)
        if document.errors: raise DataAdapterError('dependency_missing' if any('not installed' in e for e in document.errors) else 'upstream_schema')
        if not document.text.strip(): raise DataAdapterError('no_extracted_text')
        document.warnings = list(dict.fromkeys('text_truncated' if 'truncat' in w.lower()
            else 'sec_extraction_warning' for w in document.warnings))
    else:
        if reply.content_type.split(';')[0].lower() not in {'text/html', 'application/xhtml+xml'}:
            raise DataAdapterError('web_content_unsupported')
        parser = _FilingHTMLParser(url)
        try: parser.feed(reply.body.decode('utf-8'))
        except (ValueError, UnicodeError): raise DataAdapterError('upstream_schema') from None
        extracted = parser.extracted_text()
        facts = parser.facts
        text = extracted[:max_chars].rstrip()
        if not text: raise DataAdapterError('no_extracted_text')
        document = Document(make_doc_id(url, hash_text(text)), url, url, parser.title or filename,
            'sec', SourceTier.EXCHANGE_FILING, EvidenceType.ANNOUNCEMENT, 'html', None,
            reply.fetched_at, 'sec_visible_html', text, raw_hash=hash_bytes(reply.body), text_hash=hash_text(text),
            warnings=['text_truncated'] if len(extracted) > max_chars else [])
        for link in parser.links:
            target = urljoin(url, link.get('href', '')).split('#')[0]
            try: parts = _archive_parts(target)
            except DataAdapterError: continue
            if parts[:2] == (cik, accession) and target != url and target not in {x['url'] for x in links}:
                links.append({'url': target, 'text': link.get('text', '')[:1000]})
    details = filing._details() if filing else {'cik': cik, 'accession_number': accession,
        'primary_document': filename, 'metadata_basis': 'archive_url_and_inline_xbrl',
        'filing_date': None, 'report_date': facts.get('dei:DocumentPeriodEndDate'),
        'form': facts.get('dei:DocumentType'), 'company': facts.get('dei:EntityRegistrantName')}
    document.source = 'sec'
    document.source_tier = SourceTier.EXCHANGE_FILING
    form = details.get('form') or ''
    document.evidence_type = EvidenceType.FINANCIAL_REPORT if form.removesuffix('/A') in {'10-K', '10-Q', '20-F', '40-F'} else EvidenceType.ANNOUNCEMENT
    document.published_at = None  # Filing day is not an invented publication timestamp.
    document.fetched_at = reply.fetched_at
    document.extra = {'adapter_mode': 'live', 'publisher': details.get('company') or 'SEC filer (identity unverified)',
        'source_text_trust': 'untrusted', 'public_links': links[:100],
        'material_read': {'content_state': 'document_text', 'provider': 'sec', 'filing': details,
            'original_url': url, 'raw_hash': document.raw_hash, 'raw_hash_algorithm': 'sha1',
            'exhibits_fetched': False, 'links_truncated': len(links) > 100,
            'content_origin': 'sec_archive_original', 'sec_hosting_is_not_regulator_endorsement': True}}
    document.warnings.extend(['sec_exhibits_not_automatically_read', 'business_period_not_verified'])
    return document
