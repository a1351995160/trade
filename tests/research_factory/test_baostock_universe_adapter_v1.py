"""旧 BaoStock 原件到新输入的合成核验；不读取真实行情或授予研究资格。"""
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pandas as pd
import pytest

from chanlun_trader.research.guard import FinalTestAccessViolation
from chanlun_trader.research_factory.baostock_universe_adapter_v1 import (
    ADAPTER_VERSION, guard_baostock_response_header_v1, prepare_baostock_universe_v1,
)
from chanlun_trader.research_factory.board_execution_policy_v1 import board_policy_identity
from chanlun_trader.research_factory.universe_account_inputs_v1 import UniverseAccountInputsV1
from chanlun_trader.research_factory.universe_data_provider_v1 import UniverseDataProviderV1
from test_research_data_provider_v1 import dataset as legacy_dataset, provider, rewrite_response
from scripts import fetch_research_baostock_v1 as acquisition


def dataset(root, symbols=('000003.SZ', '600004.SH')):
    args = legacy_dataset(root, symbols=symbols)
    # report 请求的保守整文件范围含次年；合成数据授权明确覆盖该范围。
    args['authorization'].update(start='2023-01-01', end='2024-12-31')
    return args


def bridge_manifest(root):
    manifest = json.loads((root / 'manifest.json').read_text())
    manifest['adapter'] = ADAPTER_VERSION
    manifest['universe_id'] = 'SYNTHETIC_BAOSTOCK_BRIDGE'
    manifest['board_policy_identity'] = board_policy_identity()
    manifest['master'] = {'source': 'SYNTHETIC_LISTING_REGISTRATION', 'records': [
        {'symbol': symbol, 'board': 'SH_MAIN' if symbol.startswith('60') else
         'CHINEXT' if symbol.startswith('30') else 'SZ_MAIN', 'listing_date': 20000103}
        for symbol in manifest['symbols']]}
    manifest['listing_dates'] = {symbol: 20000103 for symbol in manifest['symbols']}
    manifest['listing_date_sources'] = {symbol: ['registered_manifest']
                                       for symbol in manifest['symbols']}
    for name, metadata in manifest['files'].items():
        metadata.update(format='BAOSTOCK_RESPONSE_JSON', source_id=name,
            kind='CALENDAR' if name.startswith('TRADE_DATES') else
            'DAILY' if name.startswith('DAILY') else
            'EVENTS' if name.startswith('DIVIDEND') else 'ADJUST')
        if name.startswith('DAILY'):
            metadata['symbol'] = name.removeprefix('DAILY_').removesuffix('.json')
        elif name.startswith('DIVIDEND'):
            year = int(name.rsplit('_', 1)[1].removesuffix('.json'))
            metadata.update(start=f'{year}-01-01', end=f'{year + 1}-12-31')
    return manifest


def bridge(root, args, *, manifest=None, events=None):
    return prepare_baostock_universe_v1(root, manifest or bridge_manifest(root),
        dataset_id='sample', required_fields=('turn',),
        access_recorder=(events if events is not None else []).append, **args)


def inputs(result, *, listing=True):
    bundle = deepcopy(result['bundle'])
    bundle.update(universe_identity='a' * 64, source_identity='b' * 64,
                  board_policy_identity=board_policy_identity())
    if listing:
        bundle['listing_dates'] = {symbol: 20000103 for symbol in result['window']['symbols']}
        bundle['listing_date_sources'] = {symbol: f'DAILY_{symbol}.json'
                                        for symbol in result['window']['symbols']}
    return UniverseAccountInputsV1(bundle, result['window'], stage='SCAN')


def cash_action(root, symbol='000003.SZ'):
    code = 'sz.' + symbol[:6]
    def action(value):
        value['fields'] = ['code', 'dividOperateDate', 'dividRegistDate', 'dividStocksPs',
            'dividReserveToStockPs', 'dividStockMarketDate', 'dividPayDate',
            'dividPlanDate', 'dividCashPsBeforeTax']
        value['raw_rows'] = [[code, '2023-03-29', '2023-03-28', '0', '0', '',
                              '2023-03-29', '2023-03-20', '0.2']]
    def adjust(value):
        value['raw_rows'] = [[code, '2023-03-29']]
    def daily(value):
        fields = value['fields']
        for row in value['raw_rows'][62:]:
            for name, number in {'open': '9.8', 'high': '10.8', 'low': '8.8',
                                 'close': '9.8', 'preclose': '9.8'}.items():
                row[fields.index(name)] = number
    rewrite_response(root, f'DIVIDEND_{symbol}_2023.json', action)
    rewrite_response(root, f'ADJUST_{symbol}.json', adjust)
    rewrite_response(root, f'DAILY_{symbol}.json', daily)


def test_same_validated_raw_data_and_account_states_are_preserved_with_warmup(tmp_path):
    args = dataset(tmp_path)
    old = provider(tmp_path, []).prepare('sample', **args)
    accesses = []
    result = bridge(tmp_path, args, events=accesses)
    assert result['window'] == old['window']
    pd.testing.assert_frame_equal(result['bundle']['daily'], old['bundle']['daily'])
    pd.testing.assert_frame_equal(result['bundle']['turn'], old['bundle']['turn'])
    actual = result['bundle']['states']
    account_states = actual.loc[actual.trade_date >= result['window']['account_start']]
    pd.testing.assert_frame_equal(account_states[old['bundle']['states'].columns].reset_index(drop=True),
                                  old['bundle']['states'])
    assert len(actual) == 128 and len(old['bundle']['states']) == 8
    assert actual.availability_status.eq('MODELED').all()
    assert 'execution_profile' not in result['bundle']['source_hashes']
    assert result['checks'] == old['qualification']['checks']
    assert len(accesses) == 7 and all(row['dataset_id'] == 'sample' for row in accesses)
    prepared = inputs(result)
    assert prepared.coverage['account_data_ready'] is True
    assert prepared.coverage['historical_availability'] == 'MODELED'
    assert prepared.coverage['independent_confirmation_eligible'] is False


def test_verified_cash_action_and_reference_transition_share_existing_evidence(tmp_path):
    args = dataset(tmp_path)
    cash_action(tmp_path)
    old = provider(tmp_path, []).prepare('sample', **args)
    result = bridge(tmp_path, args)
    assert result['bundle']['events'] == old['bundle']['events']
    assert len(result['checks']['cash_action_reference_checks']) == 1
    own = next(row for row in result['bundle']['corporate_action_coverage']
               if row['symbols'] == ['000003.SZ'])
    assert own['complete'] is True
    assert set(own['evidence_sources']) == {
        'DAILY_000003.SZ.json', 'ADJUST_000003.SZ.json', 'DIVIDEND_000003.SZ_2023.json'}
    assert inputs(result).coverage['account_data_ready'] is True


def test_missing_dividend_source_cannot_become_no_actions(tmp_path):
    args = dataset(tmp_path)
    manifest = bridge_manifest(tmp_path)
    manifest['files'].pop('DIVIDEND_000003.SZ_2023.json')
    with pytest.raises(ValueError, match='DATA_SOURCE_NOT_REGISTERED:DIVIDEND'):
        bridge(tmp_path, args, manifest=manifest)


def test_changed_raw_bytes_are_rejected_with_access_receipt(tmp_path):
    args = dataset(tmp_path)
    manifest = bridge_manifest(tmp_path)
    with (tmp_path / 'DAILY_000003.SZ.json').open('ab') as stream:
        stream.write(b' ')
    accesses = []
    with pytest.raises(ValueError, match='DATA_SOURCE_CONTENT_CHANGED:DAILY'):
        bridge(tmp_path, args, manifest=manifest, events=accesses)
    assert accesses[-1]['source'] == 'DAILY_000003.SZ.json'


def test_real_declared_availability_is_kept_and_not_advanced(tmp_path):
    args = dataset(tmp_path)
    delayed = '2023-03-28T09:30:00+08:00'
    def timed(value):
        value['fields'].append('available_at')
        for index, row in enumerate(value['raw_rows']):
            row.append(delayed if index == 60 else '2023-01-01T09:30:00+08:00')
    rewrite_response(tmp_path, 'DAILY_000003.SZ.json', timed)
    result = bridge(tmp_path, args)
    state = result['bundle']['states'].loc[lambda value:
        value.symbol.eq('000003.SZ') & value.trade_date.eq(20230327)].iloc[0]
    assert state.available_at == delayed and state.availability_status == 'MODELED'
    checked = inputs(result)
    assert checked.state('000003.SZ', 20230327)['reason'] == 'UNIVERSE_STATE_NOT_YET_AVAILABLE'
    assert not checked.coverage['account_data_ready']


def test_listing_date_is_not_invented_for_real_adapter(tmp_path):
    args = dataset(tmp_path)
    result = bridge(tmp_path, args)
    assert 'listing_dates' not in result['bundle']
    checked = inputs(result, listing=False)
    assert {row['reason'] for row in checked.coverage['gaps']} == {'UNIVERSE_LISTING_DATE_UNVERIFIED'}


def test_chinext_is_supported_in_new_adapter_and_old_default_is_unchanged(tmp_path):
    args = dataset(tmp_path, symbols=('300003.SZ', '600004.SH'))
    with pytest.raises(ValueError, match='DATA_SYMBOLS_INVALID'):
        provider(tmp_path, []).prepare('sample', **args)
    result = bridge(tmp_path, args)
    assert result['bundle']['states'].loc[lambda value:
        value.symbol.eq('300003.SZ'), 'board'].eq('CHINEXT').all()
    assert inputs(result).coverage['by_board']['CHINEXT']['account_qualified_count'] == 1


def test_sealed_declared_source_is_blocked_before_content_read(tmp_path):
    args = dataset(tmp_path)
    manifest = bridge_manifest(tmp_path)
    manifest['files']['TRADE_DATES.json']['end'] = '2025-08-01'
    accesses = []
    with pytest.raises(FinalTestAccessViolation):
        bridge(tmp_path, args, manifest=manifest, events=accesses)
    assert accesses == []


def test_whole_source_scope_and_recorder_failure_block_before_read(tmp_path):
    args = dataset(tmp_path)
    args['authorization']['start'] = '2023-01-03'
    args['feature_start'] = '2023-01-03'
    accesses = []
    with pytest.raises(ValueError, match='DATA_WHOLE_SOURCE_NOT_AUTHORIZED'):
        bridge(tmp_path, args, events=accesses)
    assert accesses == []
    args = dataset(tmp_path)
    def fail(event):
        raise RuntimeError('audit unavailable')
    with pytest.raises(RuntimeError, match='audit unavailable'):
        prepare_baostock_universe_v1(tmp_path, bridge_manifest(tmp_path),
            dataset_id='sample', required_fields=(), access_recorder=fail, **args)


def test_public_provider_accepts_registered_baostock_files_on_all_three_boards(tmp_path):
    args = dataset(tmp_path, symbols=('000003.SZ', '600004.SH', '300003.SZ'))
    manifest = bridge_manifest(tmp_path)
    path = tmp_path / 'universe-manifest.json'
    path.write_text(json.dumps(manifest), encoding='utf-8')
    accesses = []
    service = UniverseDataProviderV1({'raw': tmp_path}, accesses.append)
    service.register('sample', 'raw', path.name)
    result = service.prepare('sample', required_fields=('turn',), **args)
    assert result['qualification']['account_data_ready'] is True
    assert result['qualification']['historical_availability'] == 'MODELED'
    assert result['qualification']['independent_confirmation_eligible'] is False
    assert len(result['bundle']['daily']) == len(result['bundle']['states']) == 192
    assert result['window']['symbols'] == ['000003.SZ', '300003.SZ', '600004.SH']
    assert len(accesses) >= 10


def test_public_scan_preserves_full_denominator_and_per_symbol_missing_turn(tmp_path):
    args = dataset(tmp_path)
    def remove_turn(value):
        index = value['fields'].index('turn')
        value['fields'].pop(index)
        for row in value['raw_rows']:
            row.pop(index)
    rewrite_response(tmp_path, 'DAILY_000003.SZ.json', remove_turn)
    path = tmp_path / 'universe-manifest.json'
    path.write_text(json.dumps(bridge_manifest(tmp_path)), encoding='utf-8')
    service = UniverseDataProviderV1({'raw': tmp_path}, list().append)
    service.register('sample', 'raw', path.name)
    result = service.prepare('sample', required_fields=('turn',), stage='SCAN', **args)
    assert result['window']['symbols'] == ['000003.SZ', '600004.SH']
    coverage = result['qualification']['coverage']
    assert coverage['target_symbol_count'] == 2 and not coverage['account_data_ready']
    missing = {row['symbol'] for row in coverage['gaps']
               if row['reason'] == 'UNIVERSE_REQUIRED_TURN_MISSING'}
    assert missing == {'000003.SZ'}
    checked = UniverseAccountInputsV1(result['bundle'], result['window'],
                                     stage='SCAN', required_fields=('turn',))
    day = result['window']['account_start']
    assert checked.scan_status('000003.SZ', day)['signal_ready'] is False
    assert checked.scan_status('600004.SH', day)['signal_ready'] is True
    with pytest.raises(ValueError, match='DATA_REQUIRED_FIELD_MISSING:turn'):
        service.prepare('sample', required_fields=('turn',), stage='ACCOUNT', **args)


def test_operation_year_registration_keeps_raw_request_identity(tmp_path):
    args = dataset(tmp_path, symbols=('000003.SZ',))
    cash_action(tmp_path)
    rewrite_response(tmp_path, 'DIVIDEND_000003.SZ_2023.json',
                     lambda value: value['request'].update(yearType='operate'))
    original = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    manifest = bridge_manifest(tmp_path)
    manifest['dividend_year_type'] = 'operate'
    result = bridge(tmp_path, args, manifest=manifest)
    assert ':2023:operate:sha256:' in result['bundle']['events'][0]['source']
    assert inputs(result).coverage['account_data_ready'] is True
    assert original == {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    with pytest.raises(ValueError, match='DATA_DIVIDEND_YEAR_TYPE_INVALID'):
        bridge(tmp_path, args, manifest=bridge_manifest(tmp_path))
    with pytest.raises(ValueError, match='HISTORICAL_ACTION_REQUEST_CHANGED'):
        provider(tmp_path, []).prepare('sample', **args)


def test_operation_year_response_cannot_include_another_year(tmp_path):
    args = dataset(tmp_path, symbols=('000003.SZ',))
    cash_action(tmp_path)
    def wrong(value):
        value['request']['yearType'] = 'operate'
        value['raw_rows'][0][value['fields'].index('dividOperateDate')] = '2022-03-29'
    rewrite_response(tmp_path, 'DIVIDEND_000003.SZ_2023.json', wrong)
    manifest = bridge_manifest(tmp_path)
    manifest['dividend_year_type'] = 'operate'
    with pytest.raises(ValueError, match='DATA_RESPONSE_DATE_OUTSIDE_SOURCE_SCOPE'):
        bridge(tmp_path, args, manifest=manifest)


def acquisition_plan(tmp_path, *, year_type='operate'):
    path = tmp_path / 'acquisition.json'
    plan = {'purpose': 'EXPLORATORY_ENGINEERING_ACCEPTANCE',
        'authorization_scope': 'SYNTHETIC_ACQUISITION_TEST', 'max_requests': 1,
        'requests': [{'api': 'query_dividend_data', 'file': 'DIVIDEND_300003.SZ_2024.json',
                      'request': {'code': 'sz.300003', 'year': '2024', 'yearType': year_type}}]}
    path.write_text(json.dumps(plan), encoding='utf-8')
    return path


def fake_baostock(monkeypatch, *, fields=None, rows=None):
    calls = []
    class Response:
        error_code, error_msg = '0', 'success'
        def __init__(self):
            self.fields = fields or ['code', 'dividOperateDate', 'dividPayDate', 'dividPlanDate']
            self.rows = iter(rows or [])
        def next(self):
            self.current = next(self.rows, None)
            return self.current is not None
        def get_row_data(self):
            return self.current
    def query(**kwargs):
        calls.append(kwargs)
        return Response()
    fake = SimpleNamespace(login=lambda: calls.append('LOGIN') or Response(),
        logout=lambda: calls.append('LOGOUT'), query_dividend_data=query)
    monkeypatch.setitem(sys.modules, 'baostock', fake)
    # 实际采集 helper 的进程级 timeout 不影响其他测试。
    monkeypatch.setattr(acquisition.socket, 'setdefaulttimeout', lambda value: None)
    return calls


def test_collection_2024_operate_and_chinext_are_allowed_without_network(tmp_path, monkeypatch):
    path = acquisition_plan(tmp_path)
    calls = fake_baostock(monkeypatch, rows=[['sz.300003', '2024-05-20', '2024-05-20', '2024-03-01']])
    output = tmp_path / 'collected'
    acquisition.execute(path, output)
    raw = json.loads((output / 'DIVIDEND_300003.SZ_2024.json').read_text(encoding='utf-8'))
    assert raw['request'] == {'code': 'sz.300003', 'year': '2024', 'yearType': 'operate'}
    assert raw['raw_rows'] == [['sz.300003', '2024-05-20', '2024-05-20', '2024-03-01']]
    assert raw['historical_available_at_verified'] is False
    assert calls == ['LOGIN', raw['request'], 'LOGOUT']


def test_collection_2024_report_is_blocked_before_login_or_output(tmp_path, monkeypatch):
    path = acquisition_plan(tmp_path, year_type='report')
    calls = fake_baostock(monkeypatch)
    output = tmp_path / 'collected'
    with pytest.raises(FinalTestAccessViolation):
        acquisition.execute(path, output)
    assert calls == [] and not output.exists()


@pytest.mark.parametrize('rows,error', [
    ([['sz.300003', '2023-05-20', '2023-05-20', '2023-03-01']], ValueError),
    ([['sz.300003', '2024-05-20', '2025-08-01', '2024-03-01']], FinalTestAccessViolation),
])
def test_collection_rejects_wrong_year_or_sealed_payment_before_saving_raw(tmp_path, monkeypatch, rows, error):
    path = acquisition_plan(tmp_path)
    calls = fake_baostock(monkeypatch, rows=rows)
    output = tmp_path / 'collected'
    with pytest.raises(error):
        acquisition.execute(path, output)
    assert not (output / 'DIVIDEND_300003.SZ_2024.json').exists()
    assert calls[-1] == 'LOGOUT'
    assert json.loads((output / 'ACQUISITION_RESULT.json').read_text(encoding='utf-8'))['completed'] is False


def test_false_safe_metadata_cannot_hide_sealed_actual_query_before_whole_read(tmp_path, monkeypatch):
    dataset(tmp_path)
    source = tmp_path / 'DAILY_000003.SZ.json'
    value = json.loads(source.read_text())
    value['request']['end_date'] = '2025-08-01'
    source.write_text(json.dumps(value), encoding='utf-8')
    raw = source.read_bytes()
    metadata = bridge_manifest(tmp_path)['files'][source.name]
    opened, original_open = [], Path.open
    class HeaderReader:
        def __init__(self, stream):
            self.stream, self.bytes_read = stream, 0
        def read(self, count):
            assert count == 1
            result = self.stream.read(count)
            self.bytes_read += len(result)
            return result
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.stream.close()
    def only_header(path, *args, **kwargs):
        assert path == source and args == ('rb',) and kwargs == {'buffering': 0}
        result = HeaderReader(original_open(path, *args, **kwargs))
        opened.append(result)
        return result
    monkeypatch.setattr(Path, 'open', only_header)
    monkeypatch.setattr(Path, 'read_bytes', lambda path: pytest.fail('封存源不得整读或哈希'))
    with pytest.raises(FinalTestAccessViolation):
        guard_baostock_response_header_v1(source, metadata)
    assert opened[0].bytes_read <= raw.index(b'"raw_rows"') + len(b'"raw_rows"')


def test_scope_after_raw_rows_is_not_discovered_by_reading_quote_body(tmp_path, monkeypatch):
    dataset(tmp_path)
    source = tmp_path / 'DAILY_000003.SZ.json'
    value = json.loads(source.read_text())
    request = value.pop('request')
    value['request'] = request
    source.write_text(json.dumps(value), encoding='utf-8')
    metadata = bridge_manifest(tmp_path)['files'][source.name]
    monkeypatch.setattr(Path, 'read_bytes', lambda path: pytest.fail('未证明头范围不得整读'))
    with pytest.raises(ValueError, match='DATA_VENDOR_RESPONSE_HEADER_INVALID'):
        guard_baostock_response_header_v1(source, metadata)


def test_unsealed_query_outside_declared_scope_is_rejected_before_whole_read(tmp_path, monkeypatch):
    args = dataset(tmp_path)
    source = tmp_path / 'DAILY_000003.SZ.json'
    value = json.loads(source.read_text())
    value['request']['end_date'] = '2024-07-31'
    source.write_text(json.dumps(value), encoding='utf-8')
    metadata = bridge_manifest(tmp_path)['files'][source.name]
    monkeypatch.setattr(Path, 'read_bytes', lambda path: pytest.fail('越界源不得整读'))
    with pytest.raises(ValueError, match='DATA_QUERY_SCOPE_NOT_COVERED_BY_MANIFEST'):
        guard_baostock_response_header_v1(source, metadata, authorization=args['authorization'])


def test_late_sealed_payload_date_is_checked_before_window_filtering(tmp_path):
    args = dataset(tmp_path)
    def mutate(value):
        row = value['raw_rows'][-1].copy()
        row[value['fields'].index('date')] = '2025-08-01'
        value['raw_rows'].append(row)
    rewrite_response(tmp_path, 'DAILY_000003.SZ.json', mutate)
    with pytest.raises(FinalTestAccessViolation):
        bridge(tmp_path, args)


def test_late_unsealed_payload_date_outside_query_is_not_silently_filtered(tmp_path):
    args = dataset(tmp_path)
    def mutate(value):
        row = value['raw_rows'][-1].copy()
        row[value['fields'].index('date')] = '2023-03-31'
        value['raw_rows'].append(row)
    rewrite_response(tmp_path, 'DAILY_000003.SZ.json', mutate)
    manifest = bridge_manifest(tmp_path)
    manifest['files']['DAILY_000003.SZ.json']['end'] = '2023-12-31'
    with pytest.raises(ValueError, match='DATA_RESPONSE_DATE_OUTSIDE_QUERY_SCOPE'):
        bridge(tmp_path, args, manifest=manifest)
