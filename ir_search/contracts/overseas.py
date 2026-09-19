"""Vendor-standardized overseas data; never imply domestic revision semantics."""
from . import DatasetDefinition as Dataset, FieldDefinition as Field

_SYMBOL = Field("symbol", "string", "Provider-native ticker; venue is verified through the current profile")
_CURRENCY = Field("currency", "string", "ISO currency; trading currency for prices, reported currency for statements")

FINANCIAL_METRICS = {
    "income": {"revenue": "revenue", "operating_income": "operatingIncome", "net_income": "netIncome"},
    "balance": {"total_assets": "totalAssets", "total_liabilities": "totalLiabilities",
                "stockholders_equity": "totalStockholdersEquity", "total_equity": "totalEquity"},
    "cashflow": {"operating_cashflow": "operatingCashFlow", "investing_cashflow": "netCashProvidedByInvestingActivities",
                 "financing_cashflow": "netCashProvidedByFinancingActivities", "capital_expenditure": "capitalExpenditure",
                 "free_cashflow": "freeCashFlow"},
}
FINANCIAL_METADATA = (
    _SYMBOL, Field("report_period", "date", "Fiscal period end; not necessarily December 31"),
    Field("statement", "string", "income, balance or cashflow"),
    Field("fiscal_year", "string", "Vendor fiscal year label"), Field("fiscal_period", "string", "FY"), _CURRENCY,
    Field("filing_date", "date", "Vendor filing date, nullable when absent", nullable=True),
    Field("accepted_at_source", "string", "Unmodified vendor acceptance timestamp; timezone unverified", nullable=True),
    Field("version_id", "string", "Derived sha256 of normalized metadata and all mapped statement metrics; not a vendor ID"),
    Field("version_basis", "string", "provider_current_snapshot; no original/restated or point-in-time guarantee"),
    Field("statement_basis", "string", "provider_standardized_scope_unverified"),
)

OVERSEAS_DATASETS = {
    "prices_daily_basic": Dataset("prices_daily_basic", "Vendor daily price and activity only; OHLC and adjustment basis are unverified.",
        (_SYMBOL, Field("trade_date", "date", "Vendor trading date"),
         Field("price", "number", "Vendor price; not asserted to be unadjusted close", "quote_currency"),
         Field("source_volume", "number", "Source volume; unit and adjustment convention unverified", "source_native_count_unverified", True),
         _CURRENCY), ("symbol", "trade_date"), "trade_date"),
    "financial_statements_standardized": Dataset("financial_statements_standardized",
        "Bounded annual vendor-standardized current snapshots; dates select fiscal period ends, not disclosure dates.",
        FINANCIAL_METADATA + tuple(Field(name, "number", "Vendor " + source + "; null outside its statement or if missing",
                                        "reported_currency", True)
                                   for mapping in FINANCIAL_METRICS.values() for name, source in mapping.items()),
        ("symbol", "statement", "report_period", "version_id"), "report_period"),
}
