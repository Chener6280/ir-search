"""Domestic data schemas; native quote units never masquerade as equity shares."""
from . import DatasetDefinition as Dataset, FieldDefinition as Field

_SYMBOL = Field('symbol', 'string', 'Requested code including exchange suffix')
_DATE = Field('trade_date', 'date', 'Exchange trading date')
_CURRENCY = Field('currency', 'string', 'Currency of monetary amounts', nullable=True)
_PRICE = tuple(Field(n, 'number', d, 'contract_native_quote', True) for n, d in (
    ('open', 'First traded price; absent if no trade'), ('high', 'Highest traded price'),
    ('low', 'Lowest traded price'), ('close', 'Vendor closing field; may be a reference price on no-trade days'),
    ('settlement', 'Settlement/reference calculation, distinct from a traded price'),
    ('previous_settlement', 'Previous settlement')))
_ACTIVITY = (
    Field('source_close', 'number', 'Unmodified vendor closing field, including reference/zero values', 'contract_native_quote', True),
    Field('volume', 'number', 'Reported traded contract count; see counting convention', 'contracts', True),
    Field('amount', 'number', 'Turnover in currency; source precision can differ', 'quote_currency', True),
    Field('open_interest', 'number', 'Reported outstanding contract count; see diagnostics', 'contracts', True),
    Field('counting_convention', 'string', 'Explicit current/historical counting convention'),
    Field('price_status', 'string', 'traded, no_trade_reference, or unknown'),
)
FINANCIAL_METRICS = {
    'income': ('total_revenue', 'net_profit', 'parent_net_profit'),
    'balance': ('total_assets', 'total_liabilities', 'total_equity'),
    'cashflow': ('operating_cashflow', 'investing_cashflow', 'financing_cashflow'),
}
_FINANCE = (
    _SYMBOL, Field('report_period', 'date', 'End date of the reported period'),
    Field('statement', 'string', 'income, balance or cashflow'),
    Field('statement_scope', 'string', 'consolidated or parent'),
    Field('period_basis', 'string', 'cumulative, single_quarter, or point_in_time for balance sheet'),
    Field('revision', 'string', 'original or adjusted; not an as-of guarantee'),
    Field('source_version', 'string', 'Vendor statement type or adjustment code'),
    Field('source_record_id', 'string', 'Vendor row identity, retained to distinguish revisions'),
    Field('announcement_date', 'date', 'Source disclosure date; not capture time', nullable=True),
    Field('source_calculation', 'string', 'Uninterpreted vendor_flag_<value>, or unverified; not a reported/calculated classification'), _CURRENCY,
) + tuple(Field(n, 'number', 'Source financial amount; null if not applicable to this statement', 'quote_currency', True)
          for names in FINANCIAL_METRICS.values() for n in names)

_CONTRACT_BASE = (
    _SYMBOL, Field('source_contract_id', 'string', 'Vendor identity including the contract lifecycle'),
    Field('native_code', 'string', 'Vendor trading code; can repeat across decades'),
    Field('exchange', 'string', 'Normalized exchange abbreviation'),
    Field('listing_date', 'date', 'Contract first listing date'),
    Field('last_trading_date', 'date', 'Contract last trading date'), _CURRENCY,
)
_FUTURE_CONTRACT = _CONTRACT_BASE + (
    Field('contract_multiplier', 'number', 'Native quote to amount per contract multiplier', nullable=True),
    Field('quote_unit', 'string', 'Vendor description of quote unit', nullable=True),
    Field('contract_unit', 'string', 'Vendor trading unit description', nullable=True),
)
_OPTION_CONTRACT = _CONTRACT_BASE + (
    Field('option_type', 'string', 'call or put'),
    Field('strike', 'number', 'Strike in native underlying quote units', 'contract_native_quote'),
    Field('contract_size', 'number', 'Vendor contract size; not necessarily cash notional'),
    Field('underlying_source_id', 'string', 'Provider-native underlying/group identifier; not a universal ticker'),
    Field('adjustment_status', 'string', 'adjusted, unadjusted, or unverified'),
)

_INDEX_LEVELS = tuple(Field(n, 'number', d, 'index_points', True) for n, d in (
    ('open', 'Opening index level'), ('high', 'Highest index level'),
    ('low', 'Lowest index level'), ('close', 'Closing or latest bar index level')))
_CHAIN_CONTRACT = (
    Field('symbol', 'string', 'Requested exchange: SSE, SZSE, CFFEX, SHFE, DCE, CZCE, INE or GFEX'), _DATE,
    Field('contract', 'string', 'Option contract code including vendor exchange suffix'),
    Field('series_id', 'string', 'Provider-native option series/underlying group identifier; not a universal ticker'),
    Field('option_type', 'string', 'call or put'),
    Field('strike', 'number', 'Current listed strike in native underlying quote units', 'contract_native_quote'),
    Field('contract_size', 'number', 'Vendor contract unit; changes after ETF dividend adjustments'),
    Field('contract_month', 'string', 'Vendor delivery/expiry month YYYYMM', nullable=True),
    Field('trading_code', 'string', 'Exchange trading code; ETF adjusted contracts carry a changed flag', nullable=True),
    Field('listing_date', 'date', 'Contract first trading date'),
    Field('last_trading_date', 'date', 'Contract last trading date'),
)

MARKET_DATASETS = {
    'index_daily': Dataset('index_daily', 'Index EOD levels in index points; an index level is not a tradable price.',
        (_SYMBOL, _DATE, Field('previous_close', 'number', 'Vendor previous close level', 'index_points', True))
        + _INDEX_LEVELS + (_CURRENCY,), ('symbol', 'trade_date'), 'trade_date'),
    'index_valuation': Dataset('index_valuation', 'Index valuation snapshot; figures are vendor-computed, not recomputed from constituents.',
        (_SYMBOL, _DATE, Field('dividend_yield', 'number', 'Vendor index dividend yield (trailing) in percent', 'percent', True)),
        ('symbol', 'trade_date'), 'trade_date'),
    'benchmark_rate': Dataset('benchmark_rate', 'Benchmark interest-rate fixings; moving-average codes are vendor series, not recomputed.',
        (_SYMBOL, _DATE, Field('rate', 'number', 'Vendor benchmark fixing in percent per annum', 'percent', True)),
        ('symbol', 'trade_date'), 'trade_date'),
    'index_intraday': Dataset('index_intraday', 'Recent delayed web index bars in index points; NOT exchange ticks or official closes.',
        (_SYMBOL, Field('bar_time', 'datetime', 'Source bar timestamp in Asia/Shanghai'), _DATE) + _INDEX_LEVELS + (_CURRENCY,),
        ('symbol', 'bar_time'), 'trade_date'),
    'options_chain': Dataset('options_chain', 'Contracts listed on each requested exchange and date with that day EOD fields; request symbols are exchanges.',
        _CHAIN_CONTRACT + _PRICE + tuple(f for f in _ACTIVITY if f.name != 'amount') + (_CURRENCY,),
        ('symbol', 'contract', 'trade_date'), 'trade_date'),
    'futures_snapshot': Dataset('futures_snapshot', 'Latest provider snapshot per concrete futures contract; NOT an exchange tick feed; delay unverified.',
        (_SYMBOL, _DATE, Field('quote_time', 'datetime', 'Source snapshot time in Asia/Shanghai'),
         Field('last', 'number', 'Latest price at quote_time; after the session the source closing price', 'contract_native_quote', True))
        + tuple(f for f in _PRICE if f.name != 'close')
        + tuple(f for f in _ACTIVITY if f.name in ('volume', 'open_interest', 'counting_convention'))
        + (Field('last_trading_date', 'date', 'Contract last trading date from the provider contract table'), _CURRENCY),
        ('symbol', 'quote_time'), 'trade_date'),
    'financial_statements': Dataset('financial_statements', 'Version-preserving financial statements; bounds select report period, not disclosure date.',
        _FINANCE, ('symbol', 'statement', 'source_record_id'), 'report_period'),
    **{name: Dataset(name, 'Concrete domestic contracts only; prices, settlement, count and amount have separate semantics.',
        (_SYMBOL, _DATE) + _PRICE + _ACTIVITY + (_CURRENCY,), ('symbol', 'trade_date'), 'trade_date')
       for name in ('futures_daily', 'options_daily')},
    'futures_contracts': Dataset('futures_contracts', 'Contracts overlapping requested dates; preserves repeated codes across lifecycles.',
        _FUTURE_CONTRACT, ('symbol', 'source_contract_id')),
    'options_contracts': Dataset('options_contracts', 'Option metadata; adjusted/history changes require source-specific versions.',
        _OPTION_CONTRACT, ('symbol', 'source_contract_id')),
    'trading_calendar': Dataset('trading_calendar', 'Published open dates; symbols are exchange identifiers, not securities.',
        (Field('symbol', 'string', 'Exchange: CFFEX, SHFE, DCE, CZCE, INE or GFEX'), _DATE),
        ('symbol', 'trade_date'), 'trade_date'),
    'options_intraday': Dataset('options_intraday', 'Current-day ETF option minute point prices; NOT OHLC bars or tick history.',
        (_SYMBOL, Field('bar_time', 'datetime', 'Source observation time in Asia/Shanghai'), _DATE,
         Field('price', 'number', 'Observed point price; zero can be missing', 'CNY_per_underlying_unit', True),
         Field('average_price', 'number', 'Vendor reported average, not independently recomputed', 'CNY_per_underlying_unit', True),
         Field('source_volume', 'number', 'Unverified source activity count', 'source_count', True),
         Field('source_open_interest', 'number', 'Unverified source open interest', 'source_count', True), _CURRENCY),
        ('symbol', 'bar_time'), 'trade_date'),
}
