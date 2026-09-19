"""Internal bounded SQL paging and normalization shared by domestic adapters."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
from typing import Callable

from ir_search.contracts import AdapterMode, DataPage, Diagnostic, Provenance
from ir_search.infrastructure.pagination import _decode_cursor, _encode_cursor
from ir_search.models import SourceAuthority, FailureKind
from ir_search.registry import DataAdapterError, _DATASETS


@dataclass(frozen=True)
class _Task:
    table: str
    columns: tuple[str, ...]
    conditions: tuple[str, ...]
    params: tuple
    keys: tuple[str, ...]
    normalize: Callable


def _day(value, *, nullable=False):
    if value in (None, '') and nullable:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None or value.time() != time():
            raise DataAdapterError('upstream_schema')
        return value.date()
    if type(value) is date:
        return value
    try:
        if not isinstance(value, str):
            raise ValueError()
        return datetime.strptime(value, '%Y%m%d').date() if len(value) == 8 else date.fromisoformat(value)
    except (TypeError, ValueError):
        raise DataAdapterError('upstream_schema') from None


def _number(value, *, nullable=True):
    if value is None and nullable:
        return None
    try:
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal, str)):
            raise ValueError()
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError()
        return result
    except (ValueError, InvalidOperation):
        raise DataAdapterError('upstream_schema') from None


def _identity(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not str(value).strip():
        raise DataAdapterError('upstream_schema')
    return str(value)


def _selected(request):
    definition = _DATASETS[request.dataset]
    fields = (set(request.fields) if request.fields else {f.name for f in definition.fields}) | set(definition.primary_key) | ({definition.date_field} if definition.date_field else set()) | ({'currency'} if any(f.name == 'currency' for f in definition.fields) else set())
    if request.dataset == 'financial_statements':
        fields |= {f.name for f in definition.fields if f.dtype != 'number'}
    if request.dataset in {'futures_daily', 'options_daily'}:
        fields |= {'price_status', 'source_close', 'counting_convention'}
    return fields


def _page(adapter, request, tasks, context, issues):
    """Stable per-table keysets; cursor stays bound to all query selections."""
    tasks = tuple(tasks)
    last = _decode_cursor(request, adapter._profile)
    index, position = 0, None
    if last is not None:
        if (not isinstance(last, list) or len(last) != 2 or type(last[0]) is not int
                or not 0 <= last[0] < len(tasks)):
            raise DataAdapterError('invalid_cursor')
        index, position = last
        if position is not None and (not isinstance(position, list) or len(position) != len(tasks[index].keys)
                or any(type(v) not in (int, str) for v in position)):
            raise DataAdapterError('invalid_cursor')
    records, cursor = [], None
    for task_index in range(index, len(tasks)):
        context.check_active()
        task = tasks[task_index]
        conditions, params = list(task.conditions), list(task.params)
        if task_index == index and position is not None:
            conditions.append('(' + ', '.join(task.keys) + ') > (' + ', '.join(['%s'] * len(task.keys)) + ')')
            params.extend(position)
        remaining = request.limit - len(records)
        sql = ('SELECT ' + ', '.join(task.columns) + ' FROM ' + task.table + ' WHERE '
               + ' AND '.join(conditions) + ' ORDER BY ' + ', '.join(task.keys) + ' LIMIT %s')
        rows = adapter._select(adapter._profile, sql, tuple(params + [remaining + 1]), max_rows=remaining + 1, context=context)
        previous = position if task_index == index else None
        for raw in rows[:remaining]:
            key = [v.isoformat() if isinstance(v, (date, datetime)) else v for v in (raw[k] for k in task.keys)]
            if previous is not None and tuple(key) <= tuple(previous):
                raise DataAdapterError('upstream_schema')
            previous = key
            normalized = task.normalize(raw, issues)
            records.append({k:v for k,v in normalized.items() if k in _selected(request)})
        if len(rows) > remaining:
            cursor = _encode_cursor(request, adapter._profile, [task_index, previous])
            break
        if len(records) == request.limit and task_index + 1 < len(tasks):
            cursor = _encode_cursor(request, adapter._profile, [task_index + 1, None])
            break
    if adapter._profile.tls_mode == 'disabled':
        issues.add('non_tls_explicitly_configured')
    if records and cursor is None and request.cursor is None and set(request.symbols) - {r['symbol'] for r in records}:
        issues.add('requested_symbols_without_rows')
    return DataPage(records, Provenance(adapter.name, 'Wind' if adapter.name == 'wind_mysql' else 'JYDB',
        datetime.now(timezone.utc), authority=SourceAuthority.DATA_VENDOR, adapter_mode=AdapterMode.LIVE),
        complete=cursor is None, next_cursor=cursor,
        diagnostics=[Diagnostic(code, 'query_data', provider=adapter.name, adapter_mode=AdapterMode.LIVE,
            failure_kind=FailureKind.UPSTREAM_SCHEMA if code.endswith('_mapping_incomplete') or code in {
                'known_source_conflict', 'financial_currency_not_provided_by_table',
                'requested_symbols_without_rows', 'contract_currency_unmapped',
            } else FailureKind.NONE) for code in sorted(issues)])


def _in(values):
    return '(' + ','.join(['%s'] * len(values)) + ')'


def _validate_request(request, context, datasets, market):
    if (request.dataset not in datasets or request.market != market or request.as_of
            or request.value_kind.value != 'actual' or context.account_scope != 'default'
            or not request.start or not 1 <= len(request.symbols) <= 20):
        raise DataAdapterError('unsupported')
