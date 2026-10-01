"""状态接入按日期分区取证；可见时间、未知状态和缺股不能被填平。"""
from datetime import date
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from chanlun_trader.research.guard import FinalTestAccessViolation
from chanlun_trader.research_factory.board_execution_policy_v1 import board_policy_identity
from chanlun_trader.research_factory.universe_data_provider_v1 import UniverseDataProviderV1
from scripts.prepare_full_universe_states_v1 import (
    bind_prepared_board_policy_v1, prepare_full_universe_states_v1,
)


DATES = [20230301, 20230302, 20230303, 20230306]
SYMBOLS = ['000001.SZ', '000002.SZ', '300001.SZ', '600001.SH']


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sources(tmp_path, *, unknown=False):
    source, prepared = (tmp_path / 'source').resolve(), (tmp_path / 'prepared').resolve()
    normalized = source / 'data/research/security_state/normalized'
    _write(normalized / 'manifest.json', {'dataset_version': 'PIT_UNIVERSE_DATASET_V2',
        'coverage_start': '2023-03-01', 'coverage_end': '2025-08-01',
        'build_timestamp': '2026-08-23T14:04:00+08:00',
        'pit_policy': {'current_status_backfill_used': 'NO', 'missing_bar_auto_suspension_used': 'NO',
                       'available_at_rule': 'NEXT_SESSION_OPEN'}})
    masters = [{'symbol': s, 'board': 'GEM' if s.startswith('30') else (
        'SH_MAIN' if s.startswith('60') else 'SZ_MAIN'), 'list_date': '2022-01-01',
        'delist_date': '2023-03-03' if s.startswith('30') else None,
        'current_status_retained_only_as_raw': '1', 'source': 'baostock.query_stock_basic',
        'source_record_time': '2026-08-23T14:00:00+08:00',
        'evidence_quality': 'LIFECYCLE_DATES_ONLY_CURRENT_STATUS_NOT_BACKFILLED'} for s in SYMBOLS]
    _write(normalized / 'security_master_v2/records.json', masters)
    for i, day in enumerate(DATES):
        stamp = str(day)
        target = normalized / 'pit_universe_v2' / f'trade_date={stamp[:4]}-{stamp[4:6]}-{stamp[6:]}.jsonl'
        target.parent.mkdir(parents=True, exist_ok=True)
        next_day = DATES[i + 1] if i + 1 < len(DATES) else 20230307
        text = str(next_day)
        rows = []
        for master in masters:
            is_delisted = master['symbol'].startswith('30') and day > 20230303
            is_unknown = unknown and master['symbol'] == SYMBOLS[0] and day == DATES[1]
            rows.append({'symbol': master['symbol'], 'trade_date': str(date.fromisoformat(
                f'{stamp[:4]}-{stamp[4:6]}-{stamp[6:]}')), 'listed': True, 'delisted': is_delisted,
                'exists': not is_delisted, 'universe_member': not is_delisted, 'board': master['board'],
                'st_status': 'UNKNOWN' if is_unknown or is_delisted else 'NORMAL',
                'suspension_status': 'UNKNOWN' if is_unknown or is_delisted else 'TRADING',
                'eligibility_status': 'UNKNOWN' if is_unknown else ('INELIGIBLE' if is_delisted else 'ELIGIBLE'),
                'available_at': f'{text[:4]}-{text[4:6]}-{text[6:]}T09:30:00+08:00',
                'source_lineage': {'history': 'baostock.query_history_k_data_plus',
                    'security_master': 'baostock.query_stock_basic'},
                'reason_codes': ['ST_STATUS_UNKNOWN'] if is_unknown else []})
        target.write_text(''.join(json.dumps(row, sort_keys=True) + '\n' for row in rows), encoding='utf-8')
    # 毒丸分区只能盘点文件名，不能打开；源manifest的长范围不授予它内容访问。
    (normalized / 'pit_universe_v2/trade_date=2025-08-01.jsonl').write_bytes(b'sealed poison not JSON')
    calendar_sha = _write(prepared / 'calendar.json', DATES)
    gaps_sha = _write(prepared / 'PER_STOCK_GAPS.json', [{'symbol': s,
        'indicator_qualification': 'UNKNOWN' if s == SYMBOLS[1] else 'RAW_PRICE_AND_MODELED_UNIT_READY',
        'origin_status': 'UNKNOWN' if s == SYMBOLS[1] else 'EXACT_RAW_WINDOW_MATCH',
        'cache_present': s != SYMBOLS[1], 'reasons': ['SECURITY_STATE_NOT_IMPORTED',
            'CORPORATE_ACTION_COVERAGE_NOT_PROVEN'] + (['CACHE_MISSING'] if s == SYMBOLS[1] else [])}
        for s in SYMBOLS])
    rows = [{'symbol': s, 'date': d, 'open': 10., 'high': 11., 'low': 9., 'close': 10.,
             'volume': 1000, 'amount': 10000., 'prev_close': 10.}
            for s in SYMBOLS if s != SYMBOLS[1] for d in DATES]
    cache_path = prepared / 'daily_all.parquet'
    pd.DataFrame(rows).to_parquet(cache_path, index=False)
    cache_sha = hashlib.sha256(cache_path.read_bytes()).hexdigest()
    evidence = {'provider': 'TDX', 'source_id': 'tdx_cache', 'source_sha256': cache_sha,
        'price_mode': 'RAW', 'volume_unit': 'SHARES', 'amount_unit': 'CNY',
        'unit_evidence': {'source': 'fixture_unit', 'sha256': 'a' * 64,
                         'volume_multiplier': 1, 'amount_multiplier': 1},
        'transformation_version': 'SYNTHETIC_TEST', 'transformation_sha256': 'b' * 64,
        'original_source_hashes': {'synthetic_origin': 'c' * 64},
        'prev_close_semantics': 'DERIVED_PREVIOUS_VALID_CLOSE',
        'historical_available_at_verified': False}
    manifest = {'adapter': 'TDX_FULL_UNIVERSE_V1', 'universe_id': 'test-universe',
        'start': DATES[0], 'end': DATES[-1], 'universe_scope': {'start': DATES[0], 'end': DATES[-1]},
        'master': {'source': 'fixture_historical_master', 'records': [
            {'symbol': row['symbol'], 'board': row['board']} for row in masters]},
        'files': {
            'daily_all.parquet': {'kind': 'DAILY', 'format': 'PARQUET', 'start': DATES[0], 'end': DATES[-1],
                'source_id': 'tdx_cache', 'sha256': cache_sha, 'symbols': [s for s in SYMBOLS if s != SYMBOLS[1]],
                'evidence': evidence},
            'calendar.json': {'kind': 'CALENDAR', 'format': 'JSON', 'start': DATES[0], 'end': DATES[-1],
                'source_id': 'calendar', 'sha256': calendar_sha},
            'PER_STOCK_GAPS.json': {'kind': 'SOURCE_QUALIFICATION', 'format': 'JSON',
                'start': DATES[0], 'end': DATES[-1], 'source_id': 'source_qualification', 'sha256': gaps_sha}},
        'source_qualification_ref': 'PER_STOCK_GAPS.json', 'corporate_actions_complete': False,
        'account_preparation_blockers': ['SECURITY_STATE_NOT_IMPORTED', 'CORPORATE_ACTION_COVERAGE_NOT_PROVEN']}
    _write(prepared / 'manifest_v2.json', manifest)
    return {'source_root': source, 'prepared_root': prepared}, normalized


def test_state_import_retains_next_session_times_and_missing_stock(tmp_path, monkeypatch):
    args, normalized = sources(tmp_path)
    sealed = normalized / 'pit_universe_v2/trade_date=2025-08-01.jsonl'
    original_open = Path.open
    def no_sealed(path, *a, **kw):
        if path == sealed:
            raise AssertionError('sealed partition must not be opened')
        return original_open(path, *a, **kw)
    monkeypatch.setattr(Path, 'open', no_sealed)
    original = (args['prepared_root'] / 'manifest_v2.json').read_bytes()
    result = prepare_full_universe_states_v1(**args)
    assert result['rows'] == result['expected_rows'] == 16
    assert result['missing_rows'] == 0
    assert result['availability']['NOT_AVAILABLE_BY_CLOSE'] == 16
    assert not result['account_ready'] and not result['real_historical_release_evidence']
    assert (args['prepared_root'] / 'manifest_v2.json').read_bytes() == original
    frame = pd.read_parquet(args['prepared_root'] / 'states_v1/states.parquet')
    assert set(frame.availability_status) == {'MODELED'}
    first = frame.loc[(frame.symbol == SYMBOLS[0]) & (frame.trade_date == DATES[0])].iloc[0]
    assert first.available_at == '2023-03-02T09:30:00+08:00'
    assert first.master_observed_at == '2026-08-23T14:00:00+08:00'
    assert len(first.state_partition_sha256) == 64
    gaps = json.loads((args['prepared_root'] / 'states_v1/PER_STOCK_GAPS.json').read_text(encoding='utf-8'))
    missing_cache = next(row for row in gaps if row['symbol'] == SYMBOLS[1])
    assert missing_cache['indicator_qualification'] == 'UNKNOWN'
    assert 'CACHE_MISSING' in missing_cache['reasons']
    assert 'SECURITY_STATE_NOT_IMPORTED' not in missing_cache['reasons']
    assert 'STATE_NOT_AVAILABLE_BY_DECISION_TIME' in missing_cache['reasons']
    manifest = json.loads((args['prepared_root'] / 'manifest_v3.json').read_text(encoding='utf-8'))
    assert not manifest['corporate_actions_complete']
    assert manifest['board_policy_identity'] == result['board_policy_identity'] == board_policy_identity()
    assert manifest['board_policy_binding']['evidence_type'] == 'EXECUTION_POLICY_CODE_VERSION_ONLY'
    assert 'BOARD_POLICY_NOT_BOUND' not in manifest['account_preparation_blockers']
    assert not any(item['kind'] in {'EVENTS', 'CORPORATE_ACTION_COVERAGE'} for item in manifest['files'].values())


def test_unknown_status_and_lifecycle_originals_are_not_backfilled(tmp_path):
    args, _ = sources(tmp_path, unknown=True)
    prepare_full_universe_states_v1(**args)
    frame = pd.read_parquet(args['prepared_root'] / 'states_v1/states.parquet')
    unknown = frame.loc[(frame.symbol == SYMBOLS[0]) & (frame.trade_date == DATES[1])].iloc[0]
    assert unknown.st_status == unknown.suspension_status == 'UNKNOWN'
    delisted = frame.loc[(frame.symbol == SYMBOLS[2]) & (frame.trade_date == DATES[-1])].iloc[0]
    assert delisted.source_listed and delisted.source_delisted
    assert not delisted.listed and delisted.delisted
    assert delisted.st_status == delisted.suspension_status == 'UNKNOWN'


def test_missing_partition_is_a_per_stock_gap_without_shrinking_target(tmp_path):
    args, normalized = sources(tmp_path)
    (normalized / 'pit_universe_v2/trade_date=2023-03-02.jsonl').unlink()
    result = prepare_full_universe_states_v1(**args)
    assert result['historical_target_count'] == 4
    assert result['rows'] == 12 and result['missing_rows'] == 4
    gaps = json.loads((args['prepared_root'] / 'states_v1/PER_STOCK_GAPS.json').read_text(encoding='utf-8'))
    assert len(gaps) == 4 and all('STATE_ROWS_MISSING' in row['reasons'] for row in gaps)


def test_state_source_partition_mismatch_prevents_registration(tmp_path):
    args, normalized = sources(tmp_path)
    path = normalized / 'pit_universe_v2/trade_date=2023-03-01.jsonl'
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[0]['trade_date'] = '2023-03-02'
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
    with pytest.raises(ValueError, match='STATE_PARTITION_DATE_CONFLICT'):
        prepare_full_universe_states_v1(**args)
    assert not (args['prepared_root'] / 'manifest_v3.json').exists()


def test_changed_source_qualification_is_rejected_before_state_writes(tmp_path):
    args, _ = sources(tmp_path)
    with (args['prepared_root'] / 'PER_STOCK_GAPS.json').open('a', encoding='utf-8') as stream:
        stream.write('\n')
    with pytest.raises(ValueError, match='STATE_SOURCE_QUALIFICATION_CONTENT_CHANGED'):
        prepare_full_universe_states_v1(**args)
    assert not (args['prepared_root'] / 'states_v1').exists()


def test_sealed_requested_scope_is_refused_before_state_reads(tmp_path):
    args, _ = sources(tmp_path)
    path = args['prepared_root'] / 'manifest_v2.json'
    value = json.loads(path.read_text())
    value['universe_scope']['end'] = 20250801
    _write(path, value)
    with pytest.raises(FinalTestAccessViolation):
        prepare_full_universe_states_v1(**args)
    assert not (args['prepared_root'] / 'states_v1').exists()


def test_current_status_backfill_source_policy_cannot_be_imported(tmp_path):
    args, normalized = sources(tmp_path)
    path = normalized / 'manifest.json'
    value = json.loads(path.read_text())
    value['pit_policy']['current_status_backfill_used'] = 'YES'
    _write(path, value)
    with pytest.raises(ValueError, match='STATE_PREPARATION_SOURCE_POLICY_UNKNOWN'):
        prepare_full_universe_states_v1(**args)
    assert not (args['prepared_root'] / 'states_v1').exists()


def test_existing_state_evidence_cannot_be_overwritten(tmp_path):
    args, _ = sources(tmp_path)
    result = prepare_full_universe_states_v1(**args)
    before = (args['prepared_root'] / 'manifest_v3.json').read_bytes()
    with pytest.raises(ValueError, match='STATE_PREPARATION_NEW_OUTPUT_REQUIRED'):
        prepare_full_universe_states_v1(**args)
    assert (args['prepared_root'] / 'manifest_v3.json').read_bytes() == before
    assert result['manifest_sha256'] == hashlib.sha256(before).hexdigest()


def test_registered_states_are_windowed_and_remain_unavailable_on_public_scan(tmp_path):
    args, _ = sources(tmp_path)
    prepare_full_universe_states_v1(**args)
    provider = UniverseDataProviderV1({'data': args['prepared_root']})
    provider.register('full', 'data', 'manifest_v3.json')
    result = provider.prepare('full', feature_start=DATES[1], account_start=DATES[2], account_end=DATES[-1],
        stage='SCAN', authorization={'authorization_id': 'test-state-import', 'dataset_ids': ['full'],
                                    'purpose': 'EXPLORATORY', 'start': DATES[0], 'end': DATES[-1]})
    assert set(result['window']['symbols']) == set(SYMBOLS)
    assert set(result['bundle']['states'].trade_date) == set(DATES[1:])
    assert len(result['bundle']['states']) == 12
    assert not result['qualification']['account_data_ready']
    assert result['bundle']['source_qualification'][SYMBOLS[1]]['indicator_qualification'] == 'UNKNOWN'
    assert not result['bundle']['corporate_actions_complete']


def test_policy_binding_adds_current_identity_without_reopening_or_requalifying_data(tmp_path, monkeypatch):
    args, _ = sources(tmp_path)
    prepare_full_universe_states_v1(**args)
    prepared = args['prepared_root']
    old_path = prepared / 'manifest_v3.json'
    previous = json.loads(old_path.read_text(encoding='utf-8'))
    previous.pop('board_policy_identity')
    previous.pop('board_policy_binding')
    previous['account_preparation_blockers'].append('BOARD_POLICY_NOT_BOUND')
    _write(old_path, previous)
    original = old_path.read_bytes()
    protected = {prepared / name for name in previous['files']}
    original_open = Path.open
    def no_data_reads(path, *a, **kw):
        if path in protected:
            raise AssertionError('policy identity binding must not reopen registered data')
        return original_open(path, *a, **kw)
    monkeypatch.setattr(Path, 'open', no_data_reads)
    result = bind_prepared_board_policy_v1(prepared_root=prepared)
    assert old_path.read_bytes() == original
    assert result['previous_manifest_sha256'] == hashlib.sha256(original).hexdigest()
    assert result['board_policy_identity'] == board_policy_identity()
    assert not result['data_qualification_changed']
    current = json.loads((prepared / 'manifest_v4.json').read_text(encoding='utf-8'))
    assert current['files'] == previous['files']
    assert current['master'] == previous['master']
    assert current['state_preparation_evidence'] == previous['state_preparation_evidence']
    assert not current['corporate_actions_complete']
    assert current['source_qualification_ref'] == previous['source_qualification_ref']
    assert 'STATE_NOT_AVAILABLE_BY_DECISION_TIME' in current['account_preparation_blockers']
    assert 'BOARD_POLICY_NOT_BOUND' not in current['account_preparation_blockers']
    evidence = json.loads((prepared / 'IDENTITY_BINDING.json').read_text(encoding='utf-8'))
    assert not evidence['data_content_read'] and not evidence['historical_availability_upgraded']
    assert not evidence['data_qualification_reassessment_performed']
    assert evidence['registered_data_source_hashes'] == {n: item['sha256'] for n, item in previous['files'].items()}
    assert result['identity_binding_sha256'] == hashlib.sha256((prepared / 'IDENTITY_BINDING.json').read_bytes()).hexdigest()
    frozen = (prepared / 'manifest_v4.json').read_bytes()
    with pytest.raises(ValueError, match='POLICY_BINDING_NEW_OUTPUT_REQUIRED'):
        bind_prepared_board_policy_v1(prepared_root=prepared)
    assert (prepared / 'manifest_v4.json').read_bytes() == frozen


def test_policy_binding_cannot_register_a_sealed_scope(tmp_path):
    args, _ = sources(tmp_path)
    prepare_full_universe_states_v1(**args)
    prepared = args['prepared_root']
    path = prepared / 'manifest_v3.json'
    value = json.loads(path.read_text(encoding='utf-8'))
    value['end'] = 20250801
    _write(path, value)
    with pytest.raises(FinalTestAccessViolation):
        bind_prepared_board_policy_v1(prepared_root=prepared)
    assert not (prepared / 'manifest_v4.json').exists()
    assert not (prepared / 'IDENTITY_BINDING.json').exists()
