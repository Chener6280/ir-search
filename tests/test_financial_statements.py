from dataclasses import replace
from datetime import date
from decimal import Decimal as D
import re

import pytest

from ir_search import DataRequest, DataRegistry, get_data
from ir_search.adapters.financial_statements import FinancialStatementsAdapter, _MAP
from ir_search.infrastructure.credentials import MySQLProfile


def request(**kwargs):
    return DataRequest('financial_statements', symbols=['600519.SH'], start='2025-12-31', end='2025-12-31', **kwargs)


class Database:
    def __init__(self, provider='wind_mysql'):
        self.calls=[]
        self.rows={}
        for n,(statement,mapping) in enumerate(_MAP.items()):
            if provider=='wind_mysql':
                row={'OBJECT_ID':str(n), 'S_INFO_WINDCODE':'600519.SH','REPORT_PERIOD':'20251231',
                     'STATEMENT_TYPE':'408001000','CRNCY_CODE':'CNY','ANN_DT':'20260417','ACTUAL_ANN_DT':'20260417','IS_CALCULATION':0}
                row.update({f:D(100+n) for f in mapping[3]});self.rows[mapping[0]]=[row]
            else:
                row={'ID':n+1,'CompanyCode':1460,'EndDate':date(2025,12,31),'InfoPublDate':date(2026,4,17),'IfMerged':1,'IfAdjusted':2}
                row.update({f:D(100+n) for f in mapping[4]});self.rows[mapping[1]]=[row]
        self.rows['SecuMain']=[{'CompanyCode':1460,'SecuCode':'600519','SecuMarket':83,'ListedSector':1}]
        self.profile=MySQLProfile(provider,'host','db','user','password',tls_mode='disabled' if provider=='wind_mysql' else 'verify_identity')

    def select(self, profile, sql, params, **kwargs):
        self.calls.append((sql,params))
        table=re.search(r'FROM (\w+)',sql)[1]
        return [dict(r) for r in self.rows.get(table,[])][:params[-1]]

    def run(self, req=None):
        registry=DataRegistry()
        registry.register(FinancialStatementsAdapter(self.profile,select=self.select))
        return get_data(req or request(),registry=registry)


@pytest.mark.parametrize('provider',['wind_mysql','jydb'])
def test_three_statement_rows_have_period_version_source_currency_and_no_desktop_dependency(provider):
    db=Database(provider);result=db.run()
    assert result.status.value==('ok' if provider=='wind_mysql' else 'partial') and len(result.records)==3
    assert {r['statement'] for r in result.records}=={'income','balance','cashflow'}
    assert all(r['announcement_date']==date(2026,4,17) for r in result.records)
    assert result.records[1]['period_basis']=='point_in_time'
    assert all(' JOIN ' not in sql and sql.endswith('LIMIT %s') for sql,_ in db.calls)
    assert result.provenance.provider==provider and result.provenance.adapter_mode.value=='live'
    if provider=='wind_mysql':
        assert 'non_tls_explicitly_configured' in [d.code for d in result.diagnostics]
    else:
        assert result.records[0]['currency'] is None
        assert 'financial_currency_not_provided_by_table' in [d.code for d in result.diagnostics]
        assert not db.run(request(allow_partial=False)).records


def test_selected_metrics_skip_unrelated_tables_and_preserve_only_requested_fields():
    db=Database();result=db.run(request(fields=['parent_net_profit']))
    assert len(db.calls)==1 and len(result.records)==1
    assert 'parent_net_profit' in result.records[0] and 'total_revenue' not in result.records[0]
    assert {'report_period','statement_scope','revision','announcement_date'} <= set(result.records[0])


def test_cross_statement_pagination_is_bounded_and_cursor_binds_financial_selection():
    db=Database();first=db.run(request(limit=1,provider='wind_mysql'))
    second=db.run(replace(first.request,cursor=first.next_cursor))
    third=db.run(replace(first.request,cursor=second.next_cursor))
    assert [r.records[0]['statement'] for r in (first,second,third)]==['income','balance','cashflow']
    assert third.complete and third.next_cursor is None
    bad=db.run(replace(first.request,cursor=first.next_cursor,statement_scope='parent'))
    assert not bad.records and 'invalid_cursor' in [d.code for d in bad.diagnostics]


def test_parent_adjusted_and_single_quarter_are_explicit_and_never_inferred():
    db=Database();db.rows['ashareincome'][0]['STATEMENT_TYPE']='408008000'
    result=db.run(request(statement='income',statement_scope='parent',period_basis='single_quarter',revision='adjusted'))
    assert result.records[0]['period_basis']=='single_quarter'
    assert result.records[0]['revision']=='adjusted' and result.records[0]['statement_scope']=='parent'
    assert '408008000' in db.calls[0][1]
    jydb=Database('jydb');result=jydb.run(request(period_basis='single_quarter'))
    assert not result.records and not jydb.calls


def test_multiple_disclosure_versions_are_retained_and_wrong_period_is_rejected():
    db=Database();db.rows['ashareincome'].append(dict(db.rows['ashareincome'][0],OBJECT_ID='new',STATEMENT_TYPE='408004000',ACTUAL_ANN_DT='20260501'))
    result=db.run(request(statement='income',revision='all'))
    assert len(result.records)==2 and {r['revision'] for r in result.records}=={'original','adjusted'}
    db.rows['ashareincome'][0]['REPORT_PERIOD']='20241231'
    assert db.run(request(statement='income')).status.value=='error'


def test_jydb_explicit_currency_and_mapping_incompleteness_are_visible():
    db=Database('jydb');db.profile=replace(db.profile,financial_currency='CNY')
    result=db.run(replace(request(),symbols=('600519.SH','688981.SH')))
    assert result.records[0]['currency']=='CNY' and result.status.value=='partial'
    assert 'security_mapping_incomplete' in [d.code for d in result.diagnostics]
    verified=db.run()
    assert verified.status.value=='ok' and verified.records[0]['currency']=='CNY'


def test_wind_missing_symbol_on_unpaged_request_is_partial():
    db=Database()
    result=db.run(replace(request(),symbols=('600519.SH','688981.SH')))
    assert result.status.value=='partial' and len(result.records)==3
    assert 'requested_symbols_without_rows' in {d.code for d in result.diagnostics}


@pytest.mark.parametrize('kwargs',[{'statement':'unsafe'},{'revision':'latest'},{'statement_scope':'guess'},{'period_basis':'monthly'}])
def test_financial_selection_validation(kwargs):
    with pytest.raises(ValueError):request(**kwargs)
