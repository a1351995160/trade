import hashlib
import json

import pandas as pd
import pytest

from scripts.run_historical_process_research_v1 import build_bundle, response, summarize


def write_response(path, api, request, fields, rows):
    value = {'provider': 'BaoStock', 'api': api, 'request': request,
             'mode': 'HISTORICAL_MODELED', 'historical_available_at_verified': False,
             'error_code': '0', 'fields': fields, 'raw_rows': rows,
             'raw_rows_sha256': hashlib.sha256(json.dumps(rows, ensure_ascii=False,
                                          separators=(',', ':')).encode()).hexdigest()}
    path.write_text(json.dumps(value), encoding='utf-8')


@pytest.fixture
def source(tmp_path):
    request = {'start_date': '2022-01-01', 'end_date': '2024-07-31'}
    days = pd.date_range(request['start_date'], request['end_date'])
    calendar = [[str(d.date()), '1' if d.weekday() < 5 else '0'] for d in days]
    write_response(tmp_path / 'TRADE_DATES.json', 'query_trade_dates', request,
                   ['calendar_date', 'is_trading_day'], calendar)
    fields = ['date', 'code', 'open', 'high', 'low', 'close', 'preclose', 'volume', 'amount',
              'adjustflag', 'turn', 'tradestatus', 'isST']
    for symbol, code in [('000001.SZ', 'sz.000001'), ('600000.SH', 'sh.600000')]:
        rows = [[d, code, '10', '10', '10', '10', '10', '10000', '100000', '3', '1', '1', '0']
                for d, trading in calendar if trading == '1']
        write_response(tmp_path / f'DAILY_{symbol}.json', 'query_history_k_data_plus',
                       {**request, 'code': code, 'frequency': 'd', 'adjustflag': '3'}, fields, rows)
        for year in (2022, 2023, 2024):
            write_response(tmp_path / f'DIVIDEND_{symbol}_{year}.json', 'query_dividend_data',
                           dict(code=code, year=str(year), yearType='report'), ['code'], [])
        write_response(tmp_path / f'ADJUST_{symbol}.json', 'query_adjust_factor',
                       {**request, 'code': code}, ['code', 'dividOperateDate'], [])
    return tmp_path


def mutate(path, change):
    value = json.loads(path.read_text(encoding='utf-8'))
    change(value)
    write_response(path, value['api'], value['request'], value['fields'], value['raw_rows'])


def test_fixed_calendar_window_and_explicit_historical_model(source):
    window, bundle, coverage = build_bundle(source)
    assert len(window['calendar']) == 564
    assert window['account_start'] == window['calendar'][60] and window['account_end'] == 20240731
    assert len(bundle['states']) == 1008 and len(bundle['daily']) == len(bundle['turn']) == 1128
    assert bundle['profile'] == bundle['source_hashes']['execution_profile'] == 'HISTORICAL_MODELED'
    assert bundle['open_snapshots'] == bundle['close_snapshots'] == []
    assert coverage['historical_available_at_verified'] is False


@pytest.mark.parametrize('kind', ['missing_day', 'suspension', 'unknown_action', 'missing_calendar', 'unexplained_reference'])
def test_incomplete_or_incompatible_sources_fail_before_account(source, kind):
    daily = source / 'DAILY_000001.SZ.json'
    if kind == 'missing_day':
        mutate(daily, lambda x: x['raw_rows'].pop(-3))
    elif kind == 'suspension':
        mutate(daily, lambda x: x['raw_rows'][-3].__setitem__(11, '0'))
    elif kind == 'unexplained_reference':
        mutate(daily, lambda x: x['raw_rows'][-3].__setitem__(6, '9'))
    elif kind == 'unknown_action':
        mutate(source / 'ADJUST_000001.SZ.json', lambda x: x['raw_rows'].append(['sz.000001', '2024-07-01']))
    else:
        mutate(source / 'TRADE_DATES.json', lambda x: x['raw_rows'].pop(0))
    with pytest.raises(ValueError, match='HISTORICAL_'):
        build_bundle(source)


def test_raw_payload_tamper_is_not_accepted(source):
    path = source / 'DAILY_000001.SZ.json'
    value = json.loads(path.read_text(encoding='utf-8'))
    value['raw_rows'][-1][5] = '12'
    path.write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises(ValueError, match='HASH_INVALID'):
        response(path, 'query_history_k_data_plus')


def test_missing_family_cannot_be_summarized_as_complete():
    with pytest.raises(ValueError, match='ELEVEN_ACCOUNT_FAMILY_REQUIRED'):
        summarize({}, {})
