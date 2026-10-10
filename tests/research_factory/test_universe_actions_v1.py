"""年度原件、现金条款和部分股票覆盖认证；只使用合成供应商。"""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from scripts import collect_universe_gaps_v1 as collector
from chanlun_trader.research.guard import FinalTestAccessViolation
from scripts.prepare_universe_actions_v1 import (
    EVENT_SOURCE, SHARE_RATE_POLICY, _normalize_action, prepare_universe_actions_v1,
)


FIELDS = ['code', 'dividOperateDate', 'dividRegistDate', 'dividStocksPs',
          'dividReserveToStockPs', 'dividStockMarketDate', 'dividPayDate',
          'dividPlanDate', 'dividCashPsBeforeTax']


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def action(code='sz.000001', **overrides):
    result = {'code': code, 'dividOperateDate': '2023-01-05', 'dividRegistDate': '2023-01-04',
        'dividStocksPs': '0', 'dividReserveToStockPs': '0', 'dividStockMarketDate': '',
        'dividPayDate': '2023-01-06', 'dividPlanDate': '2023-01-03', 'dividCashPsBeforeTax': '0.2'}
    return {**result, **overrides}


class Response:
    error_code, error_msg = '0', 'success'

    def __init__(self, fields, rows):
        self.fields, self.rows, self.index = fields, rows, -1

    def next(self):
        self.index += 1
        return self.index < len(self.rows)

    def get_row_data(self):
        return self.rows[self.index]


class FakeClient:
    def __init__(self, by_code, *, missing_adjust=False):
        self.by_code, self.missing_adjust = by_code, missing_adjust

    def login(self):
        return SimpleNamespace(error_code='0', error_msg='success')

    def logout(self):
        pass

    def query_dividend_data(self, **query):
        rows = self.by_code.get(query['code'], [])
        return Response(FIELDS, [[row.get(field, '') for field in FIELDS] for row in rows])

    def query_adjust_factor(self, **query):
        dates = sorted({row['dividOperateDate'] for row in self.by_code.get(query['code'], [])})
        return Response(['code', 'dividOperateDate'], [[query['code'], day] for day in dates])


def fixture(tmp_path, *, by_code=None, symbols=('000001.SZ', '300001.SZ'),
            include_adjust=True, execute=True):
    base = tmp_path / 'data'
    base.mkdir()
    start, end = 20230103, 20230109
    dates = [20230103, 20230104, 20230105, 20230106, 20230109]
    states = base / 'states.parquet'
    pd.DataFrame({'symbol': symbols[0], 'trade_date': dates}).to_parquet(states, index=False)
    manifest = {'start': start, 'end': end,
        'master': {'records': [{'symbol': s, 'listing_date': '2000-01-03'} for s in symbols]},
        'files': {'states.parquet': {'kind': 'STATES', 'format': 'PARQUET', 'start': start,
                                   'end': end, 'sha256': sha(states), 'source_id': 'synthetic_states'}},
        'corporate_actions_complete': False}
    path = base / 'manifest_supplements_v1.json'
    write(path, manifest)
    requests = []
    for symbol in symbols:
        code = symbol[-2:].lower() + '.' + symbol[:6]
        requests.append({'file': f'DIVIDEND_{symbol}_2023.json', 'api': 'query_dividend_data',
                         'request': {'code': code, 'year': '2023', 'yearType': 'operate'}})
        if include_adjust:
            requests.append({'file': f'ADJUST_{symbol}.json', 'api': 'query_adjust_factor',
                'request': {'code': code, 'start_date': '2023-01-03', 'end_date': '2023-01-09'}})
    plan = {'batch_id': 'SUPPLEMENT_00001', 'purpose': 'EXPLORATORY_ENGINEERING_ACCEPTANCE',
            'authorization_scope': 'SYNTHETIC_SCOPE', 'max_requests': 9, 'requests': requests}
    queue = {'version': 'UNIVERSE_BAOSTOCK_SUPPLEMENTS_V1', 'request_count': len(requests),
        'authorization_source': {'origin': 'USER_EXPLICIT_CURRENT_TASK', 'statement': '合成测试授权',
                                 'scope': 'SYNTHETIC_SCOPE'}, 'batches': [plan]}
    queue_path = tmp_path / 'queue.json'
    write(queue_path, queue)
    acquisition = tmp_path / 'acquisition'
    if execute:
        collector.collect(queue_path, acquisition, client=FakeClient(by_code or {}))
    return path, acquisition


def prepare(manifest, acquisition, **kwargs):
    return prepare_universe_actions_v1(manifest=manifest, acquisition_root=acquisition,
                                       output_dir=manifest.parent / 'actions', **kwargs)


def reports(manifest):
    output = manifest.parent / 'actions'
    return tuple(json.loads((output / name).read_text(encoding='utf-8')) for name in
                 ['EVENTS.json', 'CORPORATE_ACTION_COVERAGE.json', 'ACTION_GAPS.json', 'SOURCE_CATALOG.json'])


@pytest.fixture(autouse=True)
def isolated_timeout(monkeypatch):
    monkeypatch.setattr(collector.socket, 'setdefaulttimeout', lambda value: None)


def test_verified_cash_with_later_actual_payment_and_empty_annual_records(tmp_path):
    manifest, acquisition = fixture(tmp_path, by_code={'sz.000001': [action()]})
    source_before = (acquisition / 'COLLECTION_JOURNAL.jsonl').read_bytes()
    result = prepare(manifest, acquisition)
    events, coverage, gaps, catalog = reports(manifest)
    assert result['corporate_covered_symbol_count'] == 2
    assert result['corporate_actions_complete'] is True and not result['account_data_ready']
    assert len(events) == 1 and events[0]['payment_date'] == 20230106
    assert events[0]['effective_date'] == 20230105 and events[0]['source'] == EVENT_SOURCE
    assert events[0]['terms']['cash_per_share'] == .2
    assert events[0]['terms']['tax_rule']['source']
    assert all(row['complete'] for row in coverage) and gaps == []
    assert len(catalog['sources']) == 4
    assert not catalog['independent_confirmation_eligible']
    assert source_before == (acquisition / 'COLLECTION_JOURNAL.jsonl').read_bytes()


def test_record_date_before_feature_window_is_preserved(tmp_path):
    event = action(dividOperateDate='2023-01-03', dividRegistDate='2023-01-02',
                   dividPlanDate='2023-01-01', dividPayDate='2023-01-04')
    manifest, acquisition = fixture(tmp_path, by_code={'sz.000001': [event]})
    result = prepare(manifest, acquisition)
    events, _, gaps, _ = reports(manifest)
    assert result['corporate_actions_complete']
    assert events[0]['record_date'] == 20230102 and events[0]['payment_date'] == 20230104
    assert gaps == []


def test_noncash_and_missing_payment_are_kept_as_per_stock_gaps(tmp_path):
    manifest, acquisition = fixture(tmp_path, by_code={
        'sz.000001': [action(dividStocksPs='0.1', dividStockMarketDate='2023-01-09')],
        'sz.300001': [action('sz.300001', dividPayDate='')]})
    result = prepare(manifest, acquisition)
    events, coverage, gaps, _ = reports(manifest)
    assert not result['corporate_actions_complete'] and events == []
    assert not any(row['complete'] for row in coverage)
    assert {row['status'] for row in gaps} == {'UNKNOWN', 'UNSUPPORTED'}
    assert any(row['reason'] == 'UNSUPPORTED_NONCASH_CORPORATE_ACTION' for row in gaps)


def legacy_catalog(tmp_path, *, event, empty=False):
    root = tmp_path / 'legacy'
    name = 'DIVIDEND_000001.SZ_2023.json'
    rows = [] if empty else [[event[field] for field in FIELDS]]
    query = {'code': 'sz.000001', 'year': '2023', 'yearType': 'report'}
    response = {'provider': 'BaoStock', 'api': 'query_dividend_data', 'request': query,
        'fields': FIELDS, 'error_code': '0', 'historical_available_at_verified': False,
        'raw_rows': rows, 'raw_rows_sha256': hashlib.sha256(json.dumps(rows,
            ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()}
    path = root / name
    write(path, response)
    manifest = root / 'manifest.json'
    write(manifest, {'files': {name: {'sha256': sha(path), 'start': '2023-01-01', 'end': '2023-12-31'}}})
    catalog = tmp_path / 'catalog.json'
    write(catalog, {'roots': {'A': str(root)}, 'datasets': [{'root_id': 'A', 'manifest_path': str(manifest)}]})
    return catalog


def test_same_report_and_operate_event_retains_both_original_sources(tmp_path):
    manifest, acquisition = fixture(tmp_path, by_code={'sz.000001': [action()]})
    catalog = legacy_catalog(tmp_path, event=action())
    result = prepare(manifest, acquisition, legacy_catalog=catalog)
    events, _, gaps, sources = reports(manifest)
    assert result['cash_event_count'] == 1 and result['corporate_actions_complete']
    assert {s['year_type'] for s in events[0]['original_sources']} == {'report', 'operate'}
    assert {s['source_year_type'] for s in sources['sources'] if s['symbol'] == '000001.SZ'
            and s['kind'] == 'DIVIDEND'} == {'report', 'operate'}
    assert gaps == []


def test_conflicting_duplicate_event_does_not_emit_a_preferred_cash_value(tmp_path):
    manifest, acquisition = fixture(tmp_path, by_code={'sz.000001': [action()]})
    catalog = legacy_catalog(tmp_path, event=action(dividCashPsBeforeTax='0.3'))
    result = prepare(manifest, acquisition, legacy_catalog=catalog)
    events, coverage, gaps, _ = reports(manifest)
    assert result['corporate_covered_symbol_count'] == 1 and events == []
    assert any(g['reason'] == 'DUPLICATE_ACTION_TERMS_CONFLICT' for g in gaps)
    assert next(row for row in coverage if row['symbol'] == '000001.SZ')['complete'] is False


def test_missing_adjustment_or_missing_collection_keeps_unknown_coverage(tmp_path):
    manifest, acquisition = fixture(tmp_path, include_adjust=False)
    result = prepare(manifest, acquisition)
    _, coverage, gaps, _ = reports(manifest)
    assert not result['corporate_actions_complete'] and not any(row['complete'] for row in coverage)
    assert any(g['reason'] == 'ADJUST_WHOLE_WINDOW_SOURCE_MISSING' for g in gaps)


def test_unexecuted_collection_is_diagnostic_and_does_not_create_fake_empty_events(tmp_path):
    manifest, acquisition = fixture(tmp_path, execute=False)
    result = prepare(manifest, acquisition)
    events, coverage, gaps, _ = reports(manifest)
    assert result['source_count'] == 0 and events == []
    assert not any(row['complete'] for row in coverage)
    assert {row['reason'] for row in gaps} == {'DIVIDEND_OPERATE_YEAR_SOURCE_MISSING', 'ADJUST_WHOLE_WINDOW_SOURCE_MISSING'}
    assert not acquisition.exists()


def test_response_sha_corruption_marks_only_affected_symbol_unknown(tmp_path):
    manifest, acquisition = fixture(tmp_path)
    path = acquisition / 'batches/SUPPLEMENT_00001/DIVIDEND_000001.SZ_2023.json'
    with path.open('ab') as stream:
        stream.write(b' ')
    result = prepare(manifest, acquisition)
    _, coverage, gaps, _ = reports(manifest)
    assert result['corporate_covered_symbol_count'] == 1
    assert next(row for row in coverage if row['symbol'] == '000001.SZ')['complete'] is False
    assert any('ACTION_RAW_SOURCE_SHA_CHANGED' in row['reason'] for row in gaps)


def test_completed_collection_result_identity_change_is_rejected(tmp_path):
    manifest, acquisition = fixture(tmp_path)
    result_path = acquisition / 'batches/SUPPLEMENT_00001/ACQUISITION_RESULT.json'
    value = json.loads(result_path.read_text())
    value['request_count'] += 1
    write(result_path, value)
    with pytest.raises(ValueError, match='ACTION_COMPLETED_BATCH_RESULT_CHANGED'):
        prepare(manifest, acquisition)
    assert not (manifest.parent / 'actions').exists()


def test_original_action_manifest_cannot_be_overwritten(tmp_path):
    manifest, acquisition = fixture(tmp_path)
    before = manifest.read_bytes()
    prepare(manifest, acquisition)
    with pytest.raises(ValueError, match='ACTION_NEW_CHILD_OUTPUT_REQUIRED'):
        prepare(manifest, acquisition)
    assert manifest.read_bytes() == before


def test_adjustment_event_without_matching_annual_action_is_not_no_action(tmp_path):
    manifest, acquisition = fixture(tmp_path, by_code={'sz.000001': [action()]})
    # 只移除 dividend 成功资料的正文并连同集合回执重建一个独立合成集合。
    # 使用新集合避免原冻结日志内容被改写。
    source = tmp_path / 'new-source'
    source.mkdir()
    requested = json.loads((acquisition / 'ACQUISITION_BATCHES.json').read_text())
    client = FakeClient({'sz.000001': [action()]})
    client.query_dividend_data = lambda **query: Response(FIELDS, [])
    queue = source / 'queue.json'
    write(queue, requested)
    root = source / 'collection'
    collector.collect(queue, root, client=client)
    result = prepare(manifest, root)
    events, coverage, gaps, _ = reports(manifest)
    assert events == [] and result['corporate_covered_symbol_count'] == 1
    assert any(g['reason'] == 'ADJUST_AND_ACTION_DATES_CONFLICT'
               and g['effective_date'] == 20230105 for g in gaps)
    assert not next(row for row in coverage if row['symbol'] == '000001.SZ')['complete']


def test_requested_sealed_window_is_rejected_before_original_market_read(tmp_path, monkeypatch):
    manifest, acquisition = fixture(tmp_path)
    import scripts.prepare_universe_actions_v1 as module
    monkeypatch.setattr(module, '_read_response', lambda *args: pytest.fail('original action body read'))
    with pytest.raises(FinalTestAccessViolation):
        prepare(manifest, acquisition, feature_start=20230103, account_end=20250801)
    assert not (manifest.parent / 'actions').exists()


def test_report_year_is_not_relabelled_as_verified_operate_year(tmp_path):
    manifest, original = fixture(tmp_path, by_code={'sz.000001': [action()]})
    queue = json.loads((original / 'ACQUISITION_BATCHES.json').read_text())
    queue['batches'][0]['requests'] = [item for item in queue['batches'][0]['requests']
                                     if item['api'] == 'query_adjust_factor']
    queue['request_count'] = len(queue['batches'][0]['requests'])
    queue_path = tmp_path / 'adjust-only-queue.json'
    write(queue_path, queue)
    acquisition = tmp_path / 'adjust-only-acquisition'
    collector.collect(queue_path, acquisition, client=FakeClient({'sz.000001': [action()]}))
    result = prepare(manifest, acquisition, legacy_catalog=legacy_catalog(tmp_path, event=action()))
    events, coverage, gaps, _ = reports(manifest)
    assert events[0]['original_query_year_type'] == 'report'
    assert result['corporate_covered_symbol_count'] == 0
    assert not any(row['complete'] for row in coverage)
    assert any(g['symbol'] == '000001.SZ' and g['year'] == 2023
               and g['reason'] == 'DIVIDEND_OPERATE_YEAR_SOURCE_MISSING' for g in gaps)
    queue_path = manifest.parent / 'actions/ADDITIONAL_ACQUISITION_BATCHES.json'
    queue = json.loads(queue_path.read_text())
    assert queue['source_version'] == 'UNIVERSE_BAOSTOCK_SUPPLEMENTS_V1'
    assert queue['request_count'] == 2
    assert all(item['api'] == 'query_dividend_data' and item['request']['yearType'] == 'operate'
               for batch in queue['batches'] for item in batch['requests'])
    extra_root = tmp_path / 'additional-acquisition'
    collector.collect(queue_path, extra_root, client=FakeClient({'sz.000001': [action()]}))
    completed = prepare_universe_actions_v1(manifest=manifest, acquisition_root=acquisition,
        additional_acquisition_root=[extra_root], legacy_catalog=legacy_catalog(tmp_path, event=action()),
        output_dir=manifest.parent / 'actions-complete', manifest_name='manifest_actions_complete_v1.json')
    assert completed['corporate_covered_symbol_count'] == 2
    assert completed['additional_acquisition_request_count'] == 0
    assert (manifest.parent / 'manifest_actions_v1.json').is_file()
    assert (manifest.parent / 'manifest_actions_complete_v1.json').is_file()


def test_report_2024_sealed_query_is_skipped_before_body_or_hash(tmp_path, monkeypatch):
    manifest, acquisition = fixture(tmp_path)
    catalog_path = legacy_catalog(tmp_path, event=action(), empty=True)
    catalog = json.loads(catalog_path.read_text())
    legacy_manifest = Path(catalog['datasets'][0]['manifest_path'])
    old = legacy_manifest.parent / 'DIVIDEND_000001.SZ_2023.json'
    target = legacy_manifest.parent / 'DIVIDEND_000001.SZ_2024.json'
    raw = json.loads(old.read_text())
    raw['request']['year'] = '2024'
    write(target, raw)
    # 窄物理 metadata 不得让 report2024 的实际查询潜在 2025 内容越过封存。
    write(legacy_manifest, {'files': {target.name: {'sha256': sha(target),
        'start': '2024-01-01', 'end': '2024-12-31'}}})
    import scripts.prepare_universe_actions_v1 as module
    original_sha, original_read = module._sha, module._read_response
    def guarded_sha(path):
        if Path(path) == target:
            pytest.fail('sealed legacy body hashed')
        return original_sha(path)
    def guarded_read(item, audit):
        if item['path'] == target:
            pytest.fail('sealed legacy body read')
        return original_read(item, audit)
    monkeypatch.setattr(module, '_sha', guarded_sha)
    monkeypatch.setattr(module, '_read_response', guarded_read)
    result = prepare(manifest, acquisition, legacy_catalog=catalog_path)
    _, _, gaps, sources = reports(manifest)
    assert result['corporate_actions_complete'] and gaps == []
    assert sources['skipped_sources_without_body_read'][0]['reason'] == 'LEGACY_QUERY_SCOPE_SEALED'


def test_batch_without_completion_witness_is_not_read_while_being_written(tmp_path):
    manifest, acquisition = fixture(tmp_path)
    journal = acquisition / 'COLLECTION_JOURNAL.jsonl'
    lines = journal.read_text(encoding='utf-8').splitlines()
    first_completion = next(i for i, line in enumerate(lines)
                            if json.loads(line)['event'] == 'BATCH_COMPLETED')
    journal.write_text('\n'.join(lines[:first_completion]) + '\n', encoding='utf-8')
    # 模拟正在写入当前 batch 的中间状态；应只读冻结队列和完整日志前缀。
    (acquisition / 'batches/SUPPLEMENT_00001/ACQUISITION_RESULT.json').write_text('{', encoding='utf-8')
    # 既有 pending 宽窗 ADJUST 请求已覆盖窄准备窗，不得另建冗余查询。
    result = prepare(manifest, acquisition, feature_start=20230104, account_end=20230106)
    assert result['source_count'] == 0 and result['corporate_covered_symbol_count'] == 0
    assert result['additional_acquisition_request_count'] == 0


def test_additional_source_conflict_does_not_select_a_preferred_response(tmp_path):
    manifest, acquisition = fixture(tmp_path, by_code={'sz.000001': [action()]})
    queue = tmp_path / 'duplicate-queue.json'
    write(queue, json.loads((acquisition / 'ACQUISITION_BATCHES.json').read_text()))
    additional = tmp_path / 'duplicate-acquisition'
    collector.collect(queue, additional,
        client=FakeClient({'sz.000001': [action(dividCashPsBeforeTax='0.3')]}))
    result = prepare(manifest, acquisition, additional_acquisition_root=[additional])
    events, coverage, gaps, _ = reports(manifest)
    assert result['corporate_covered_symbol_count'] == 1 and events == []
    assert any(g['reason'] == 'ACTION_QUERY_RESPONSE_CONTENT_CONFLICT' for g in gaps)
    assert not next(row for row in coverage if row['symbol'] == '000001.SZ')['complete']
    assert result['additional_acquisition_request_count'] == 0


@pytest.mark.parametrize('name', ['../manifest_actions_v2.json', 'sub/manifest_actions_v2.json',
                                 'manifest.json', 'manifest_actions.txt'])
def test_manifest_name_is_restricted_to_new_json_basename(tmp_path, name):
    manifest, acquisition = fixture(tmp_path)
    with pytest.raises(ValueError, match='ACTION_MANIFEST_JSON_BASENAME_REQUIRED'):
        prepare(manifest, acquisition, manifest_name=name)
    assert not (manifest.parent / 'actions').exists()


def test_empty_annual_result_without_required_schema_is_unknown(tmp_path):
    manifest, original = fixture(tmp_path)
    queue = tmp_path / 'missing-schema-queue.json'
    write(queue, json.loads((original / 'ACQUISITION_BATCHES.json').read_text()))
    client = FakeClient({})
    client.query_dividend_data = lambda **query: Response(['code', 'dividOperateDate'], [])
    acquisition = tmp_path / 'missing-schema-acquisition'
    collector.collect(queue, acquisition, client=client)
    result = prepare(manifest, acquisition)
    _, coverage, gaps, _ = reports(manifest)
    assert result['corporate_covered_symbol_count'] == 0 and not any(row['complete'] for row in coverage)
    assert any('ACTION_RAW_REQUIRED_FIELDS_MISSING' in g['reason'] for g in gaps)


@pytest.mark.parametrize('overrides', [{'dividStocksPs': '0.000000', 'dividReserveToStockPs': ''},
                                     {'dividStocksPs': '', 'dividReserveToStockPs': ''}])
def test_real_vendor_empty_optional_share_rates_follow_legacy_zero_semantics(tmp_path, overrides):
    manifest, acquisition = fixture(tmp_path, by_code={'sz.000001': [action(
        dividCashPsBeforeTax='0.100000', **overrides)]})
    result = prepare(manifest, acquisition)
    events, _, gaps, sources = reports(manifest)
    assert result['corporate_actions_complete'] and gaps == []
    assert events[0]['terms']['cash_per_share'] == .1
    assert events[0]['optional_share_rate_interpretation_policy'] == SHARE_RATE_POLICY
    assert sources['optional_share_rate_interpretation']['missing_fields_are_not_zero']
    assert sources['optional_share_rate_interpretation']['reference_source_sha256']


@pytest.mark.parametrize('overrides', [{'dividStocksPs': 'unknown'}, {'dividReserveToStockPs': '-.1'},
                                     {'dividCashPsBeforeTax': ''}, {'dividReserveToStockPs': None}])
def test_optional_empty_rate_compatibility_does_not_hide_unknown_invalid_or_empty_cash(tmp_path, overrides):
    manifest, acquisition = fixture(tmp_path, by_code={'sz.000001': [action(**overrides)]})
    result = prepare(manifest, acquisition)
    events, coverage, gaps, _ = reports(manifest)
    assert result['corporate_covered_symbol_count'] == 1 and events == []
    assert not next(row for row in coverage if row['symbol'] == '000001.SZ')['complete']
    assert gaps


def test_empty_optional_rate_with_real_stock_market_date_still_is_unsupported(tmp_path):
    manifest, acquisition = fixture(tmp_path, by_code={'sz.000001': [action(
        dividStocksPs='', dividReserveToStockPs='', dividStockMarketDate='2023-01-09')]})
    result = prepare(manifest, acquisition)
    events, _, gaps, _ = reports(manifest)
    assert result['corporate_covered_symbol_count'] == 1 and events == []
    assert any(g['reason'] == 'UNSUPPORTED_NONCASH_CORPORATE_ACTION' for g in gaps)


@pytest.mark.parametrize('overrides', [{'dividStocksPs': '.1', 'dividCashPsBeforeTax': ''},
    {'dividReserveToStockPs': '.2', 'dividCashPsBeforeTax': ''},
    {'dividStockMarketDate': '2023-01-09', 'dividCashPsBeforeTax': ''}])
def test_proven_noncash_with_blank_cash_is_unsupported_instead_of_unknown(tmp_path, overrides):
    manifest, acquisition = fixture(tmp_path, by_code={'sz.000001': [action(**overrides)]})
    result = prepare(manifest, acquisition)
    events, _, gaps, _ = reports(manifest)
    assert result['corporate_covered_symbol_count'] == 1 and events == []
    assert any(g['reason'] == 'UNSUPPORTED_NONCASH_CORPORATE_ACTION' for g in gaps)
    assert not any(g['reason'] == 'ACTION_NUMERIC_TERM_UNKNOWN' for g in gaps)


def test_cash_terms_match_actual_legacy_bundle_for_same_raw_optional_blank_rates(tmp_path):
    from chanlun_trader.research_factory.research_data_provider_v1 import _baostock_bundle
    days = pd.bdate_range('2023-01-03', periods=64)
    vendor_dates = days.strftime('%Y-%m-%d').tolist()
    start, end = vendor_dates[0], vendor_dates[-1]
    raw_action = action(dividOperateDate=vendor_dates[62], dividRegistDate=vendor_dates[61],
        dividPayDate=vendor_dates[62], dividPlanDate=vendor_dates[60],
        dividStocksPs='', dividReserveToStockPs='', dividCashPsBeforeTax='0.100000')
    calendar_days = pd.date_range(start, end).strftime('%Y-%m-%d').tolist()
    request_range = {'start_date': start, 'end_date': end}
    daily = pd.DataFrame([{'date': date, 'code': 'sz.000001', 'open': '10', 'high': '11',
        'low': '9', 'close': '10', 'preclose': '9.9' if i == 62 else '10',
        'volume': '100', 'amount': '1000', 'adjustflag': '3', 'tradestatus': '1', 'isST': '0'}
        for i, date in enumerate(vendor_dates)])
    responses = {
        'TRADE_DATES.json': ({'request': request_range}, pd.DataFrame({
            'calendar_date': calendar_days, 'is_trading_day': ['1' if d in vendor_dates else '0'
                                                              for d in calendar_days]})),
        'DAILY_000001.SZ.json': ({'request': {**request_range, 'code': 'sz.000001',
            'frequency': 'd', 'adjustflag': '3'}}, daily),
        'DIVIDEND_000001.SZ_2023.json': ({'request': {'code': 'sz.000001', 'year': '2023',
            'yearType': 'report'}}, pd.DataFrame([raw_action])),
        'ADJUST_000001.SZ.json': ({'request': {**request_range, 'code': 'sz.000001'}},
            pd.DataFrame([{'code': 'sz.000001', 'dividOperateDate': vendor_dates[62]}]))}
    _, legacy, _ = _baostock_bundle(tmp_path, symbols=['000001.SZ'],
        feature_start=int(start.replace('-', '')), account_start=int(vendor_dates[60].replace('-', '')),
        account_end=int(end.replace('-', '')), required_fields=(),
        response=lambda path, api: responses[path.name], file_hash=lambda path: 'a' * 64)
    new, _, unsupported = _normalize_action('000001.SZ', raw_action,
        {'path': 'synthetic:vendor', 'sha256': 'a' * 64, 'source_year_type': 'report'})
    assert unsupported is None
    for key in ('event_id', 'symbol', 'event_type', 'record_date', 'effective_date',
                'payment_date', 'source_published_at', 'units', 'terms'):
        assert new[key] == legacy['events'][0][key]


def blocked_collection(tmp_path):
    manifest, acquisition = fixture(tmp_path, execute=False)
    client = FakeClient({'sz.000001': [action()]})
    normal_adjust = client.query_adjust_factor
    def query_adjust_factor(**query):
        if query['code'] == 'sz.300001':
            response = Response(['code', 'dividOperateDate'], [])
            response.error_code, response.error_msg = '10002007', 'synthetic receive error'
            return response
        return normal_adjust(**query)
    client.query_adjust_factor = query_adjust_factor
    with pytest.raises(RuntimeError):
        collector.collect(tmp_path / 'queue.json', acquisition, client=client)
    return manifest, acquisition


def test_blocked_batch_reuses_only_each_strictly_verified_successful_request(tmp_path, monkeypatch):
    manifest, acquisition = blocked_collection(tmp_path)
    before_journal = (acquisition / 'COLLECTION_JOURNAL.jsonl').read_bytes()
    result_path = acquisition / 'batches/SUPPLEMENT_00001/ACQUISITION_RESULT.json'
    before_result = result_path.read_bytes()
    verifier, verified = collector.verify_collected_response_v1, []
    def verify(directory, item, resultrow):
        verified.append(item['file'])
        return verifier(directory, item, resultrow)
    monkeypatch.setattr(collector, 'verify_collected_response_v1', verify)
    result = prepare(manifest, acquisition)
    events, coverage, gaps, sources = reports(manifest)
    assert result['source_count'] == 3 and result['corporate_covered_symbol_count'] == 1
    assert len(verified) == 3 and 'ADJUST_300001.SZ.json' not in verified
    assert events[0]['terms']['cash_per_share'] == .2
    assert next(c for c in coverage if c['symbol'] == '000001.SZ')['complete']
    assert any(g['symbol'] == '300001.SZ' and g['kind'] == 'ADJUST' for g in gaps)
    assert all(s['batch_completed'] is False and s['request_verified'] is True
               and s['verification_status'] == 'BATCH_PARTIAL_REQUEST_VERIFIED'
               and s['parent_batch_status'] == 'BLOCKED' for s in sources['sources'])
    assert all(s['request_verification_witness']['request_verified'] for s in sources['sources'])
    assert sources['collections'][0]['completed_batch_count'] == 0
    assert sources['collections'][0]['partial_verified_request_count'] == 3
    assert before_result == result_path.read_bytes()
    assert before_journal == (acquisition / 'COLLECTION_JOURNAL.jsonl').read_bytes()
    assert not json.loads(before_result)['completed']


def test_tampered_success_in_blocked_batch_is_not_requalified(tmp_path):
    manifest, acquisition = blocked_collection(tmp_path)
    path = acquisition / 'batches/SUPPLEMENT_00001/DIVIDEND_000001.SZ_2023.json'
    with path.open('ab') as stream:
        stream.write(b' ')
    with pytest.raises(ValueError, match='COLLECTOR_RESPONSE_CONTENT_CHANGED'):
        prepare(manifest, acquisition)
    assert not (manifest.parent / 'actions').exists()


def test_action_preparation_inherits_two_generation_successes_and_preserves_blocked_status(tmp_path):
    from scripts import prepare_universe_collection_continuation_v1 as continuation
    manifest, original = blocked_collection(tmp_path)
    roots, source = [original, tmp_path / 'continued_1', tmp_path / 'continued_2'], original
    for index in range(2):
        plan = tmp_path / f'continuation_{index + 1}'
        continuation.prepare_continuation(source, plan,
            collection_output_root=roots[index + 1], reason='合成公司行动续采回归')
        client = FakeClient({})
        if index == 0:
            def failed_adjust(**query):
                response = Response([], [])
                response.error_code, response.error_msg = '10002007', 'synthetic receive failure'
                return response
            client.query_adjust_factor = failed_adjust
            with pytest.raises(RuntimeError, match='BAOSTOCK_RESPONSE_FAILED'):
                collector.collect(plan / 'ACQUISITION_BATCHES.json', roots[index + 1], client=client)
        else:
            collector.collect(plan / 'ACQUISITION_BATCHES.json', roots[index + 1], client=client)
        source = roots[index + 1]
    originals = {path: path.read_bytes() for root in roots for path in root.rglob('*') if path.is_file()}
    result = prepare(manifest, roots[-1])
    events, coverage, gaps, catalog = reports(manifest)
    assert result['source_count'] == 4 and result['corporate_actions_complete']
    assert len(events) == 1 and all(row['complete'] for row in coverage) and not gaps
    proof = catalog['collections'][0]
    assert proof['request_count'] == 6 and proof['success_count'] == 4 and proof['generation'] == 2
    assert len(proof['collections']) == 3 and proof['partial_verified_request_count'] == 3
    parent_sources = [s for s in catalog['sources'] if s['collection_root'] == str(original)]
    assert len(parent_sources) == 3
    assert all(s['parent_batch_status'] == 'BLOCKED' and s['batch_completed'] is False for s in parent_sources)
    for row in catalog['sources']:
        assert sha(Path(row['result_path'])) == row['parent_batch_result_sha256']
        assert sha(Path(row['path'] + '.START.json')) == row['start_sha256']
        assert row['request_verification_witness']['source_sha256'] == row['sha256']
    assert all(path.read_bytes() == content for path, content in originals.items())


def test_action_preparation_rejects_same_ancestor_supplied_again_as_additional_root(tmp_path):
    from scripts import prepare_universe_collection_continuation_v1 as continuation
    manifest, original = blocked_collection(tmp_path)
    plan, target = tmp_path / 'continuation', tmp_path / 'continued'
    continuation.prepare_continuation(original, plan, collection_output_root=target,
        reason='合成重复谱系回归')
    collector.collect(plan / 'ACQUISITION_BATCHES.json', target, client=FakeClient({}))
    with pytest.raises(ValueError, match='ACTION_DUPLICATE_COLLECTION_LINEAGE'):
        prepare(manifest, target, additional_acquisition_root=[original])
    assert not (manifest.parent / 'actions').exists()
