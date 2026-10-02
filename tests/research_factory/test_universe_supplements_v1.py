"""已有 account 原件适配与补采边界；只用合成数据。"""
import hashlib
import json

import pandas as pd
import pytest

from chanlun_trader.research.guard import FinalTestAccessViolation
from scripts.prepare_universe_supplements_v1 import (
    STATE_POLICY, STATE_SOURCE, prepare_universe_supplements_v1,
    read_account_raw_response_v1,
)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture(root, symbols=('000001.SZ', '300001.SZ')):
    source = root / 'vendor'
    data = root / 'data'
    data.mkdir()
    days = ['2023-01-03', '2023-01-04', '2023-01-05']
    plan = {'symbols': list(symbols), 'start': days[0], 'end': days[-1]}
    write(source / 'READ_PLAN.json', plan)
    tdx, masters = [], []
    for symbol in symbols:
        market, code = symbol[-2:].lower(), symbol[:6]
        fields = ['date', 'code', 'open', 'high', 'low', 'close', 'preclose',
                  'volume', 'amount', 'adjustflag', 'tradestatus', 'isST']
        query = {'code': market + '.' + code, 'start_date': days[0], 'end_date': days[-1],
                 'frequency': 'd', 'adjustflag': '3', 'fields': ','.join(fields)}
        rows = [{'date': day, 'code': query['code'], 'open': '10', 'high': '11', 'low': '9',
                 'close': '10', 'preclose': '10', 'volume': '100', 'amount': '1000',
                 'adjustflag': '3', 'tradestatus': '1', 'isST': '0'} for day in days]
        path = source / 'responses' / symbol / '3.json'
        write(path, {'query': query, 'fields': fields, 'rows': rows, 'error_code': '0',
                     'error_msg': '', 'completed_at': '2026-10-02T01:00:00+00:00'})
        write(path.with_name('3.started.json'), {'query': query})
        write(path.with_name('3.access.json'), {'path': str(path.absolute()), 'sha256': sha(path),
                                              'row_count': len(rows), 'error_code': '0'})
        masters.append({'symbol': symbol, 'listing_date': '2000-01-03', 'delisting_date': None,
                        'board': 'CHINEXT' if symbol.startswith('30') else 'SZ_MAIN'})
        tdx.extend({'symbol': symbol, 'date': int(day.replace('-', '')), 'open': 10., 'high': 11.,
                    'low': 9., 'close': 10.} for day in days)
    price = data / 'daily.parquet'
    pd.DataFrame(tdx).to_parquet(price, index=False)
    manifest = {'start': 20230103, 'end': 20230105, 'master': {'records': masters},
                'files': {'daily.parquet': {'kind': 'DAILY', 'format': 'PARQUET',
                    'start': 20230103, 'end': 20230105, 'sha256': sha(price), 'source_id': 'tdx'}}}
    manifest_path = data / 'manifest.json'
    write(manifest_path, manifest)
    return manifest_path, source, plan


def prepare(root, **overrides):
    manifest, source, _ = fixture(root)
    return prepare_universe_supplements_v1(manifest=manifest, response_root=source,
        output_dir=manifest.parent / 'supplement', feature_start=20230103,
        account_end=20230105, **overrides), manifest


def test_all_targets_normalize_same_day_status_without_claiming_visibility(tmp_path):
    summary, manifest = prepare(tmp_path)
    output = manifest.parent / 'supplement'
    states = pd.read_parquet(output / 'states.parquet')
    reference = pd.read_parquet(output / 'reference_prices.parquet')
    assert summary['target_count'] == 2
    assert summary['raw_responses_verified'] == 2
    assert len(states) == 6 and len(reference) == 6
    assert set(states.symbol) == {'000001.SZ', '300001.SZ'}
    assert set(states.source_availability_policy) == {STATE_POLICY}
    assert set(states.available_at) == {'MODELED'}
    assert set(states.availability_status) == {'MODELED'}
    assert not states.historical_available_at_verified.any()
    assert not summary['account_data_ready'] and not summary['independent_confirmation_eligible']
    assert set(reference.prev_close) == {10.}
    new = json.loads((manifest.parent / 'manifest_supplements_v1.json').read_text())
    assert new['listing_date_sources']['300001.SZ'] == [STATE_SOURCE]
    assert not new['supplement_preparation']['corporate_sources_validated']
    original = json.loads(manifest.read_text())
    assert 'supplement_preparation' not in original


def test_missing_annual_and_adjustment_are_queued_but_local_files_need_validation(tmp_path):
    manifest, source, _ = fixture(tmp_path)
    old = tmp_path / 'legacy'
    local = old / 'DIVIDEND_000001.SZ_2023.json'
    write(local, {'do_not_read_this_as_ready': True})
    old_manifest = old / 'manifest.json'
    write(old_manifest, {'files': {local.name: {'sha256': sha(local),
        'start': '2023-01-01', 'end': '2023-12-31'}}})
    catalog = tmp_path / 'catalog.json'
    write(catalog, {'roots': {'A': str(old)}, 'datasets': [
        {'root_id': 'A', 'manifest_path': str(old_manifest)}]})
    result = prepare_universe_supplements_v1(manifest=manifest, response_root=source,
        output_dir=manifest.parent / 'supplement', legacy_catalog=catalog,
        feature_start=20230103, account_end=20230105)
    gaps = json.loads((manifest.parent / 'supplement/PER_STOCK_YEAR_GAPS.json').read_text())
    existing = next(row for row in gaps if row['symbol'] == '000001.SZ' and row['kind'] == 'DIVIDEND')
    assert existing['status'] == 'LOCAL_REQUIRES_VALIDATION'
    assert existing['sources'][0]['content_read'] is False
    queues = json.loads((manifest.parent / 'supplement/ACQUISITION_BATCHES.json').read_text())
    assert result['acquisition_request_count'] == 3
    requests = queues['batches'][0]['requests']
    assert {r['api'] for r in requests} == {'query_dividend_data', 'query_adjust_factor'}
    assert not any(r['api'] == 'query_history_k_data_plus' for r in requests)
    assert next(r for r in requests if r['api'] == 'query_dividend_data')['request']['yearType'] == 'operate'


def test_raw_price_conflict_is_reported_and_reference_is_not_overwritten(tmp_path):
    manifest, source, _ = fixture(tmp_path)
    path = manifest.parent / 'daily.parquet'
    prices = pd.read_parquet(path)
    prices.loc[0, 'close'] = 10.01
    prices.to_parquet(path, index=False)
    meta = json.loads(manifest.read_text())
    meta['files']['daily.parquet']['sha256'] = sha(path)
    write(manifest, meta)
    summary = prepare_universe_supplements_v1(manifest=manifest, response_root=source,
        output_dir=manifest.parent / 'supplement', feature_start=20230103, account_end=20230105)
    assert summary['symbols_with_price_conflict'] == 1
    assert summary['states_rows'] == 6 and summary['reference_rows'] == 5
    gaps = json.loads((manifest.parent / 'supplement/PER_STOCK_YEAR_GAPS.json').read_text())
    conflict = next(row for row in gaps if row['kind'] == 'STATE_AND_REFERENCE'
                    and row['symbol'] == '000001.SZ')
    assert conflict['price_conflict_dates'] == [20230103]
    assert 'TDX_BAOSTOCK_RAW_OHLC_CONFLICT' in conflict['reasons']
    assert pd.read_parquet(path).loc[0, 'close'] == 10.01


def test_own_raw_query_header_is_guarded_before_hash_or_rows(tmp_path, monkeypatch):
    _, source, plan = fixture(tmp_path)
    path = source / 'responses/000001.SZ/3.json'
    value = json.loads(path.read_text())
    value['query']['end_date'] = '2025-08-01'
    write(path, value)
    import scripts.prepare_universe_supplements_v1 as module
    monkeypatch.setattr(module, '_sha', lambda p: pytest.fail('sealed body/hash was accessed'))
    log = tmp_path / 'audit.jsonl'
    with pytest.raises(FinalTestAccessViolation):
        read_account_raw_response_v1(source, '000001.SZ', plan, log)
    assert not log.exists()


def test_response_sha_change_and_mutating_original_manifest_are_rejected(tmp_path):
    manifest, source, plan = fixture(tmp_path)
    path = source / 'responses/000001.SZ/3.json'
    value = json.loads(path.read_text())
    value['rows'][0]['preclose'] = '9.9'
    write(path, value)
    with pytest.raises(ValueError, match='SUPPLEMENT_RAW_SHA_CHANGED'):
        read_account_raw_response_v1(source, '000001.SZ', plan, tmp_path / 'audit.jsonl')
    with pytest.raises(ValueError, match='SUPPLEMENT_NEW_CHILD_OUTPUT_REQUIRED'):
        prepare_universe_supplements_v1(manifest=manifest, response_root=source,
            output_dir=manifest.parent, feature_start=20230103, account_end=20230105)


def test_many_missing_requests_form_independently_bounded_batches(tmp_path):
    symbols = tuple(f'00000{i}.SZ' for i in range(1, 6))
    manifest, source, _ = fixture(tmp_path, symbols)
    summary = prepare_universe_supplements_v1(manifest=manifest, response_root=source,
        output_dir=manifest.parent / 'supplement', feature_start=20230103, account_end=20230105)
    queues = json.loads((manifest.parent / 'supplement/ACQUISITION_BATCHES.json').read_text())
    assert summary['acquisition_request_count'] == 10
    assert [len(b['requests']) for b in queues['batches']] == [9, 1]
    assert all(b['max_requests'] == 9 for b in queues['batches'])
    assert not queues['automatic_execution'] and not queues['automatic_retry']


def test_out_of_scope_master_and_unknown_vendor_state_are_not_made_eligible(tmp_path):
    manifest, source, _ = fixture(tmp_path, ('000001.SZ', '688001.SH'))
    path = source / 'responses/000001.SZ/3.json'
    value = json.loads(path.read_text())
    value['rows'][1]['isST'] = ''
    write(path, value)
    access_path = path.with_name('3.access.json')
    access = json.loads(access_path.read_text())
    access['sha256'] = sha(path)
    write(access_path, access)
    summary = prepare_universe_supplements_v1(manifest=manifest, response_root=source,
        output_dir=manifest.parent / 'supplement', feature_start=20230103, account_end=20230105)
    states = pd.read_parquet(manifest.parent / 'supplement/states.parquet')
    assert summary['target_count'] == 1 and len(states) == 3
    assert states.loc[states.trade_date == 20230104, 'eligibility_status'].iloc[0] == 'UNKNOWN'
    assert not summary['account_data_ready']


@pytest.mark.parametrize('field', ['available_at', 'effective_available_at', 'researcher_available_at'])
@pytest.mark.parametrize('declared', [True, False])
def test_explicit_future_availability_is_rejected_instead_of_overwritten_by_modeled(tmp_path, field, declared):
    manifest, source, _ = fixture(tmp_path)
    path = source / 'responses/000001.SZ/3.json'
    value = json.loads(path.read_text())
    value['rows'][0][field] = '2023-01-06T09:30:00+08:00'
    if declared:
        value['fields'].append(field)
    write(path, value)
    access_path = path.with_name('3.access.json')
    access = json.loads(access_path.read_text())
    access['sha256'] = sha(path)
    write(access_path, access)
    summary = prepare_universe_supplements_v1(manifest=manifest, response_root=source,
        output_dir=manifest.parent / 'supplement', feature_start=20230103, account_end=20230105)
    states = pd.read_parquet(manifest.parent / 'supplement/states.parquet')
    gaps = json.loads((manifest.parent / 'supplement/PER_STOCK_YEAR_GAPS.json').read_text())
    assert summary['raw_responses_verified'] == 1
    assert set(states.symbol) == {'300001.SZ'}
    rejected = next(g for g in gaps if g['symbol'] == '000001.SZ' and g['kind'] == 'STATE_AND_REFERENCE')
    assert any('SUPPLEMENT_RAW_EXPLICIT_AVAILABILITY_UNSUPPORTED' in reason for reason in rejected['reasons'])
