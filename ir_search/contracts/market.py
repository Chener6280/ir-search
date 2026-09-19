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

MARKET_DATASETS = {
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
