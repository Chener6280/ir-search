"""Fund, macro and provider-native derivatives schemas; no inferred investment signals."""
from . import DatasetDefinition as D, FieldDefinition as F


def _f(name, dtype='number', description=None, unit=None, nullable=True):
    return F(name, dtype, description or name.replace('_', ' '), unit, nullable)


_SYMBOL = _f('symbol', 'string', 'Exact requested series or share-class code', nullable=False)
_ID = _f('source_record_id', 'string', 'Vendor record identity; corrections are not collapsed', nullable=False)
_ANN = _f('announcement_date', 'date', 'Source disclosure date, not valuation/report date')
_CCY = _f('currency', 'string', 'Source currency; null when table does not declare it')
FUND_DATASETS = {
 'fund_profile': D('fund_profile', 'Current fund share-class directory selected by inception date; not historical identity or PIT.',
  (_SYMBOL, _ID, _f('name','string',nullable=False), _f('inception_date','date',nullable=False),
   _f('maturity_date','date'), _f('listing_date','date'), _f('exchange','string'), _f('fund_type','string'),
   _f('listed_flag','string'), _f('benchmark','string'), _CCY), ('symbol','source_record_id'), 'inception_date'),
 'fund_nav': D('fund_nav', 'Share-class NAV; bounds select valuation dates, cumulative NAV is not a total-return index.',
  (_SYMBOL, _ID, _f('nav_date','date',nullable=False), _ANN,
   _f('unit_nav',unit='currency_per_fund_unit'), _f('accumulated_nav',unit='source_cumulative_nav'),
   _f('adjusted_nav',unit='source_adjusted_nav'), _f('net_assets',unit='currency'),
   _f('merged_share_class_flag','string'), _CCY), ('symbol','nav_date','source_record_id'), 'nav_date'),
 'fund_shares': D('fund_shares', 'Effective-date share observations; share changes are NOT inferred money flows.',
  (_SYMBOL, _ID, _f('effective_date','date',nullable=False), _ANN,
   _f('total_shares',description='Vendor F_UNIT_TOTAL; may aggregate split fund classes',unit='fund_units'),
   _f('share_class_shares',description='Vendor FUNDSHARE for this fund record',unit='fund_units'),
   _f('combined_shares',description='Vendor FUNDSHARE_TOTAL; combined split fund shares',unit='fund_units'),
   _f('tradable_shares',unit='fund_units'),
   _f('merged_share_class_flag','string'), _f('change_reason','string')), ('symbol','effective_date','source_record_id'), 'effective_date'),
 'fund_holdings': D('fund_holdings', 'Reported equity holdings only; quarterly lists may be top-ten, not a complete portfolio.',
  (_SYMBOL, _ID, _f('report_date','date',nullable=False), _ANN,
   _f('holding_symbol','string',nullable=False), _f('market_value',unit='currency'),
   _f('quantity',unit='shares'), _f('nav_weight',unit='ratio'), _f('source_report_type','string'), _CCY),
  ('symbol','report_date','source_record_id'), 'report_date'),
 'fund_exchange_daily': D('fund_exchange_daily', 'Mainland listed fund raw EOD (ETF/LOF/closed funds); does not classify every listing as ETF.',
  (_SYMBOL,_ID,_f('trade_date','date',nullable=False), *(_f(k,unit='currency_per_fund_unit') for k in ('open','high','low','close')),
   _f('volume',unit='fund_units'), _f('amount',unit='currency'), _f('source_volume',unit='hundred_fund_units'),
   _f('source_amount',unit='thousand_currency'), _f('source_discount_percent',unit='percent'), _CCY),
  ('symbol','trade_date','source_record_id'), 'trade_date'),
}
MACRO_DATASETS = {
 'macro_series': D('macro_series', 'Official observations in native frequency; latest revised snapshot, not release-time/PIT data.',
  (_SYMBOL,_f('observation_date','date','First day of native observation period, not publication date',nullable=False),
   _f('period','string',nullable=False),_f('value'),_f('unit','string',nullable=False),
   _f('frequency','string',nullable=False),_f('seasonal_adjustment','string',nullable=False),
   _f('source_series_id','string',nullable=False),_f('source_url','string',nullable=False),
   _f('observation_status','string'),_f('source_updated_on','date')),
  ('symbol','observation_date'), 'observation_date')
}
FIONA_DATASETS = {
 'derivatives_bars': D('derivatives_bars', 'Fiona recent contract bars; bounds select exchange trading days including preceding night sessions.',
  (_SYMBOL,_f('bar_time','datetime',nullable=False),_f('trade_date','date',nullable=False),
   *(_f(k,unit='native_contract_quote') for k in ('open','high','low','close')),
   *(_f('source_'+k,unit='provider_native_unverified') for k in ('volume','turnover','open_interest')),
   _f('source_contract','string',nullable=False)), ('symbol','bar_time'), 'trade_date'),
 'option_risk': D('option_risk', 'Provider-calculated risk metrics with unverified model/scale; never model-independent raw facts.',
  (_SYMBOL,_f('trade_date','date',nullable=False),*(_f('source_'+k,unit='provider_native_unverified') for k in ('delta','gamma','vega','theta','rho','iv')),
   _f('model','string',nullable=False),_f('price_type','string',nullable=False),_f('source_contract','string',nullable=False)),
  ('symbol','trade_date'), 'trade_date'),
}
EXPANDED_DATASETS = {**FUND_DATASETS, **MACRO_DATASETS, **FIONA_DATASETS}
