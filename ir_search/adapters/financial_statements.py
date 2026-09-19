"""Standalone, version-preserving Wind/JYDB financial statement clients."""
from __future__ import annotations

import re
from functools import partial

from ir_search.contracts import AdapterMode, DataCapability
from ir_search.contracts.market import FINANCIAL_METRICS, MARKET_DATASETS
from ir_search.infrastructure.mysql import _select
from ir_search.registry import DataAdapterError
from ._market_common import _Task, _day, _identity, _in, _number, _page, _selected, _validate_request

_MAP = {
    'income': ('ashareincome', 'LC_IncomeStatementAll', 'LC_STIBIncomeState',
               ('TOT_OPER_REV', 'NET_PROFIT_INCL_MIN_INT_INC', 'NET_PROFIT_EXCL_MIN_INT_INC'),
               ('TotalOperatingRevenue', 'NetProfit', 'NPParentCompanyOwners')),
    'balance': ('asharebalancesheet', 'LC_BalanceSheetAll', 'LC_STIBBalanceSheet',
                ('TOT_ASSETS', 'TOT_LIAB', 'TOT_SHRHLDR_EQY_INCL_MIN_INT'),
                ('TotalAssets', 'TotalLiability', 'TotalShareholderEquity')),
    'cashflow': ('asharecashflow', 'LC_CashFlowStatementAll', 'LC_STIBCashFlowState',
                 ('NET_CASH_FLOWS_OPER_ACT', 'NET_CASH_FLOWS_INV_ACT', 'NET_CASH_FLOWS_FNC_ACT'),
                 ('NetOperateCashFlow', 'NetInvestCashFlow', 'NetFinanceCashFlow')),
}
_WIND_TYPES = {
    ('consolidated', 'cumulative', 'original'): '408001000',
    ('consolidated', 'cumulative', 'adjusted'): '408004000',
    ('parent', 'cumulative', 'original'): '408006000',
    ('parent', 'cumulative', 'adjusted'): '408009000',
    ('consolidated', 'single_quarter', 'original'): '408002000',
    ('consolidated', 'single_quarter', 'adjusted'): '408003000',
    ('parent', 'single_quarter', 'original'): '408007000',
    ('parent', 'single_quarter', 'adjusted'): '408008000',
}
_VENUES = {'SH': 83, 'SZ': 90, 'BJ': 18}


class FinancialStatementsAdapter:
    def __init__(self, profile, *, select=None):
        if profile.provider not in {'wind_mysql', 'jydb'}:
            raise ValueError('Unsupported financial provider')
        self.name = profile.provider
        self._profile, self._select = profile, select or _select
        self.capabilities = (DataCapability(self.name, 'financial_statements',
            tuple(f.name for f in MARKET_DATASETS['financial_statements'].fields), ('A_SHARE',),
            frequencies=('report',), adjustments=('none',), supports_pagination=True, adapter_mode=AdapterMode.LIVE,
            coverage_notes=('Core three-statement amounts; request bounds select report-period end dates',
                'Preserves source row/version/disclosure; not point-in-time data',
                'Wind cumulative and source single-quarter; JYDB cumulative only, no invented quarter subtraction',
                'revision=all means mapped original and adjusted types, not every vendor report category',
                'JYDB tables omit currency; explicit local financial currency config or null with diagnostic')) ,)

    def query_data(self, request, *, context):
        """Query source financial rows without collapsing disclosures or parent reports."""
        _validate_request(request, context, {'financial_statements'}, 'A_SHARE')
        if request.frequency != 'report' or request.adjustment != 'none' or any(not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', s) for s in request.symbols):
            raise DataAdapterError('unsupported')
        if self.name == 'jydb' and request.period_basis == 'single_quarter':
            raise DataAdapterError('unsupported')
        issues = {'database_snapshot_not_pit', 'financial_core_fields_only', 'report_period_not_disclosure_date'}
        if request.revision == 'all':
            issues.add('mapped_financial_versions_only')
        selected = _selected(request)
        statements = [s for s in _MAP if request.statement in ('all', s)]
        if request.fields:
            metrics = set().union(*map(set, FINANCIAL_METRICS.values()))
            if selected & metrics:
                statements = [s for s in statements if selected & set(FINANCIAL_METRICS[s])]
        if not statements:
            raise DataAdapterError('unsupported')
        tasks = self._wind_tasks(request, statements) if self.name == 'wind_mysql' else self._jydb_tasks(request, statements, context, issues)
        return _page(self, request, tasks, context, issues)

    def _wind_tasks(self, request, statements):
        tasks = []
        for statement in statements:
            basis = 'cumulative' if statement == 'balance' else request.period_basis
            revisions = ('original', 'adjusted') if request.revision == 'all' else (request.revision,)
            types = {_WIND_TYPES[(request.statement_scope, basis, rev)]:rev for rev in revisions}
            columns = ('OBJECT_ID', 'S_INFO_WINDCODE', 'REPORT_PERIOD', 'STATEMENT_TYPE', 'CRNCY_CODE', 'ANN_DT', 'ACTUAL_ANN_DT')
            if statement != 'balance':
                columns += ('IS_CALCULATION',)
            columns += _MAP[statement][3]
            tasks.append(_Task(_MAP[statement][0], columns,
                ('S_INFO_WINDCODE IN ' + _in(request.symbols), 'REPORT_PERIOD BETWEEN %s AND %s', 'STATEMENT_TYPE IN ' + _in(types)),
                tuple(request.symbols) + (request.start.strftime('%Y%m%d'), request.end.strftime('%Y%m%d')) + tuple(types),
                ('OBJECT_ID',), partial(self._wind_row, request=request, statement=statement, types=types)))
        return tasks

    def _base(self, request, statement, source_id, period, ann, revision, version, currency):
        row = {name:None for names in FINANCIAL_METRICS.values() for name in names}
        row.update(report_period=_day(period), statement=statement, statement_scope=request.statement_scope,
            period_basis='point_in_time' if statement == 'balance' else request.period_basis,
            revision=revision, source_version=version, source_record_id=_identity(source_id),
            announcement_date=_day(ann, nullable=True), source_calculation='unverified', currency=currency)
        if row['announcement_date'] and row['announcement_date'] < row['report_period']:
            raise DataAdapterError('upstream_schema')
        return row

    def _wind_row(self, raw, issues, *, request, statement, types):
        version = str(raw['STATEMENT_TYPE'])
        if version not in types or raw['S_INFO_WINDCODE'] not in request.symbols:
            raise DataAdapterError('upstream_schema')
        currency = raw['CRNCY_CODE']
        if not isinstance(currency, str) or not re.fullmatch('[A-Z]{3}', currency):
            raise DataAdapterError('upstream_schema')
        row = self._base(request, statement, raw['OBJECT_ID'], raw['REPORT_PERIOD'],
            raw['ACTUAL_ANN_DT'] or raw['ANN_DT'], types[version], version, currency)
        row['symbol'] = raw['S_INFO_WINDCODE']
        # Vendor calculation flags are retained conservatively until their enum is verified.
        if statement != 'balance':
            row['source_calculation'] = 'vendor_flag_' + str(raw['IS_CALCULATION']) if raw['IS_CALCULATION'] is not None else 'unverified'
            issues.add('vendor_calculation_flag_not_normalized')
        for name, column in zip(FINANCIAL_METRICS[statement], _MAP[statement][3]):
            row[name] = _number(raw[column])
        return row

    def _jydb_tasks(self, request, statements, context, issues):
        clauses, params = [], []
        for symbol in request.symbols:
            code, venue = symbol.split('.')
            clauses.append('(SecuCode = %s AND SecuMarket = %s)')
            params.extend((code, _VENUES[venue]))
        rows = self._select(self._profile,
            'SELECT CompanyCode, SecuCode, SecuMarket, ListedSector FROM SecuMain WHERE SecuCategory = 1 AND (' + ' OR '.join(clauses) + ') ORDER BY CompanyCode LIMIT %s',
            tuple(params + [41]), max_rows=41, context=context)
        companies, groups = {}, {False:[], True:[]}
        for row in rows:
            symbol = row['SecuCode'] + '.' + next((k for k,v in _VENUES.items() if v == row['SecuMarket']), '?')
            code = row['CompanyCode']
            if symbol not in request.symbols or type(code) is not int or code in companies or row['ListedSector'] not in {1,2,6,7,8}:
                raise DataAdapterError('upstream_schema')
            companies[code] = symbol
            groups[row['ListedSector'] == 7].append(code)
        if not companies:
            raise DataAdapterError('not_found')
        if set(companies.values()) != set(request.symbols):
            issues.add('security_mapping_incomplete')
        tasks = []
        revisions = (1, 2) if request.revision == 'all' else (2 if request.revision == 'original' else 1,)
        for statement in statements:
            for star, codes in groups.items():
                if not codes:
                    continue
                tasks.append(_Task(_MAP[statement][2 if star else 1],
                    ('ID', 'CompanyCode', 'EndDate', 'InfoPublDate', 'IfMerged', 'IfAdjusted') + _MAP[statement][4],
                    ('CompanyCode IN ' + _in(codes), 'EndDate BETWEEN %s AND %s', 'IfMerged = %s', 'IfAdjusted IN ' + _in(revisions)),
                    tuple(codes) + (request.start.isoformat(), request.end.isoformat(), 1 if request.statement_scope == 'consolidated' else 2) + revisions,
                    ('ID',), partial(self._jydb_row, request=request, statement=statement, companies=companies, revisions=revisions)))
        return tasks

    def _jydb_row(self, raw, issues, *, request, statement, companies, revisions):
        if raw['IfAdjusted'] not in revisions or raw['IfMerged'] != (1 if request.statement_scope == 'consolidated' else 2):
            raise DataAdapterError('upstream_schema')
        currency = self._profile.financial_currency
        if currency is None:
            issues.add('financial_currency_not_provided_by_table')
        else:
            issues.add('financial_currency_from_explicit_configuration')
        row = self._base(request, statement, raw['ID'], raw['EndDate'], raw['InfoPublDate'],
            'original' if raw['IfAdjusted'] == 2 else 'adjusted', str(raw['IfAdjusted']), currency)
        row['symbol'] = companies[raw['CompanyCode']]
        for name, column in zip(FINANCIAL_METRICS[statement], _MAP[statement][4]):
            row[name] = _number(raw[column])
        return row
