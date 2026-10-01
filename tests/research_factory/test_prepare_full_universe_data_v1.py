"""维护者来源准备用合成DAY及缓存验证；不读取真实行情。"""
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from chanlun_trader.research.guard import FinalTestAccessViolation
from chanlun_trader.research_factory.board_execution_policy_v1 import board_policy_identity
from chanlun_trader.tdx_data import _DAY_STRUCT
from scripts.prepare_full_universe_data_v1 import prepare_full_universe_data_v1


DATES = [20230301, 20230302, 20230303, 20230306]
SYMBOLS = ['000001.SZ', '300001.SZ', '600001.SH']


def sources(tmp_path, *, sealed_cache=False, conflict=False):
    root, tdx = (tmp_path / 'source').resolve(), (tmp_path / 'tdx').resolve()
    data = root / 'data/research'
    data.mkdir(parents=True)
    records, rows = [], []
    for symbol, board in zip(SYMBOLS, ['SZ_MAIN', 'GEM', 'SH_MAIN']):
        records.append({'symbol': symbol, 'board': board, 'list_date': '2022-01-01', 'delist_date': None})
        path = tdx / 'vipdoc' / symbol[-2:].lower() / 'lday' / (symbol[-2:].lower() + symbol[:6] + '.day')
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = []
        for index, date in enumerate(DATES):
            rows.append({'symbol': symbol, 'date': 20250801 if sealed_cache and index == 3 else date,
                'open': 10., 'high': 11., 'low': 9., 'close': 10., 'volume': 1000,
                'amount': 10000., 'prev_close': float('nan') if index == 0 else 10.})
            encoded.append(_DAY_STRUCT.pack(date, 1000, 1100, 900, 1000, 10000.,
                1001 if conflict and symbol == SYMBOLS[0] and index == 0 else 1000, b'\0' * 4))
        # 不允许因日期探针预取这条封存行情。
        encoded.append(_DAY_STRUCT.pack(20250801, 999900, 999900, 999900, 999900, 9., 9999, b'\0' * 4))
        path.write_bytes(b''.join(encoded))
    records.append({'symbol': '000002.SZ', 'board': 'SZ_MAIN', 'list_date': '2022-01-01', 'delist_date': None})
    frame = pd.DataFrame(rows)
    # 缓存中未参与此历史目标范围的证券必须单列，不盲合并进目标。
    unknown = frame.loc[frame.symbol == SYMBOLS[0]].copy()
    unknown['symbol'] = '300002.SZ'
    pd.concat([frame, unknown], ignore_index=True).to_parquet(data / 'daily_all.parquet', index=False)
    master = {'version': 'STAGE3_UNIVERSE_MANIFEST_V1', 'scope': {'start': '2023-03-01', 'end': '2023-03-06'},
              'calendar_dates': DATES, 'symbols': records}
    (data / 'stage3_universe_manifest.json').write_text(json.dumps(master), encoding='utf-8')
    unit_path = tmp_path / 'unit.json'
    unit_path.write_text(json.dumps({'DERIVED_VOLUME_UNIT': 'SHARES',
        'VOLUME_EVIDENCE_LEVEL': 'DERIVED_AND_PUBLICLY_CORROBORATED', 'NOT_FOR_QUALIFICATION': True,
        'purpose': 'DEGRADED_TRAIN_ACCOUNT_BACKTEST_V1',
        'source_identity': {'directory': str(tdx / 'vipdoc')}}), encoding='utf-8')
    return {'source_root': root, 'tdx_root': tdx, 'unit_evidence_path': unit_path,
            'output_root': (tmp_path / 'prepared').resolve()}


def test_entire_cache_copied_with_original_preserved_and_distinct_denominators(tmp_path):
    args = sources(tmp_path)
    cache = args['source_root'] / 'data/research/daily_all.parquet'
    before = cache.read_bytes()
    result = prepare_full_universe_data_v1(**args)
    out = args['output_root']
    assert cache.read_bytes() == before
    assert (out / 'daily_all.parquet').read_bytes() == before
    assert result['source_cache_sha256'] == hashlib.sha256(before).hexdigest()
    assert result['historical_target_count'] == 4
    assert result['cached_symbol_count'] == 4
    assert result['target_cached_count'] == 3
    assert result['target_missing_cache_count'] == 1
    assert result['cached_symbols_outside_historical_master_count'] == 1
    assert result['origin_checks']['RAW_ORIGIN_PASS'] == 3
    assert not result['account_ready']
    assert not result['independent_confirmation_eligible']
    assert result['units_NOT_FOR_QUALIFICATION']
    manifest = json.loads((out / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest['corporate_actions_complete'] is False
    assert manifest['board_policy_identity'] == board_policy_identity()
    assert manifest['board_policy_binding']['identity'] == result['board_policy_identity']
    policy_path = Path(__file__).resolve().parents[2] / manifest['board_policy_binding']['implementation_source']
    assert manifest['board_policy_binding']['implementation_sha256'] == hashlib.sha256(policy_path.read_bytes()).hexdigest()
    assert manifest['board_policy_binding']['historical_data_qualification_changed'] is False
    assert 'BOARD_POLICY_NOT_BOUND' not in manifest['account_preparation_blockers']
    assert manifest['universe_scope'] == {'start': 20230301, 'end': 20230306}
    assert manifest['files']['daily_all.parquet']['evidence']['unit_evidence']['NOT_FOR_QUALIFICATION']
    assert len(manifest['files']['daily_all.parquet']['evidence']['original_source_hashes']) == 3
    gaps = json.loads((out / 'PER_STOCK_GAPS.json').read_text(encoding='utf-8'))
    missing = next(row for row in gaps if row['symbol'] == '000002.SZ')
    assert 'CACHE_MISSING' in missing['reasons']
    assert all(row['account_qualification'] == 'BLOCKED' for row in gaps)


def test_sealed_cache_is_refused_before_output_directory_or_copy(tmp_path):
    args = sources(tmp_path, sealed_cache=True)
    with pytest.raises(FinalTestAccessViolation):
        prepare_full_universe_data_v1(**args)
    assert not args['output_root'].exists()


def test_raw_mismatch_preserves_stock_and_does_not_invent_lineage_hash(tmp_path):
    args = sources(tmp_path, conflict=True)
    result = prepare_full_universe_data_v1(**args)
    assert result['historical_target_count'] == 4
    assert result['origin_checks']['RAW_ORIGIN_CONFLICT'] == 1
    gaps = json.loads((args['output_root'] / 'PER_STOCK_GAPS.json').read_text(encoding='utf-8'))
    row = next(row for row in gaps if row['symbol'] == '000001.SZ')
    assert row['origin_status'] == 'RAW_CACHE_CONFLICT'
    assert not row['comparison']['field_checks']['volume']
    manifest = json.loads((args['output_root'] / 'manifest.json').read_text(encoding='utf-8'))
    assert len(manifest['files']['daily_all.parquet']['evidence']['original_source_hashes']) == 2


def test_metadata_only_never_claims_source_verified(tmp_path):
    args = sources(tmp_path)
    result = prepare_full_universe_data_v1(**args, verify_raw=False)
    assert result['origin_checks'].get('RAW_ORIGIN_PASS', 0) == 0
    manifest = json.loads((args['output_root'] / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest['files']['daily_all.parquet']['evidence']['original_source_hashes'] == {}
    assert not result['account_ready']
    catalog = json.loads((args['output_root'] / 'SOURCE_CATALOG.json').read_text(encoding='utf-8'))
    assert all(row['content_read'] is False for row in catalog)


def test_preparation_does_not_overwrite_existing_evidence_directory(tmp_path):
    args = sources(tmp_path)
    args['output_root'].mkdir()
    (args['output_root'] / 'existing.json').write_text('preserve', encoding='utf-8')
    with pytest.raises(ValueError, match='PREPARATION_NEW_OUTPUT_DIRECTORY_REQUIRED'):
        prepare_full_universe_data_v1(**args)
    assert (args['output_root'] / 'existing.json').read_text(encoding='utf-8') == 'preserve'


def test_day_window_does_not_physically_read_sealed_value_record(tmp_path, monkeypatch):
    from chanlun_trader.research_factory.tdx_research_adapter_v1 import read_tdx_day_window
    args = sources(tmp_path)
    original = Path.open
    target = args['tdx_root'] / 'vipdoc/sz/lday/sz000001.day'
    observations = []
    class Probe:
        def __init__(self, stream):
            self.stream = stream
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.stream.close()
        def seek(self, offset):
            return self.stream.seek(offset)
        def read(self, size):
            offset = self.stream.tell()
            observations.append((offset, size))
            if offset == 4 * 32 and size > 4:
                raise AssertionError('sealed OHLC must not be physically read')
            return self.stream.read(size)
    def opened(path, *args, **kwargs):
        stream = original(path, *args, **kwargs)
        if path == target:
            assert kwargs['buffering'] == 0
            return Probe(stream)
        return stream
    monkeypatch.setattr(Path, 'open', opened)
    rows, evidence = read_tdx_day_window(target, DATES)
    assert len(rows) == 4
    assert (4 * 32, 4) in observations
    assert evidence['physical_value_dates'] == DATES
