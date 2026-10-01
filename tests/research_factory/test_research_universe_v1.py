"""全范围清单测试不读取真实行情、不访问收益。"""
from pathlib import Path

import pytest

from chanlun_trader.research.guard import FinalTestAccessViolation
from chanlun_trader.research_factory.research_universe_v1 import (
    ResearchUniverseV1, canonical_symbol, inventory_research_paths, scope_board,
)


def master():
    return [
        {'symbol': '000001.SZ', 'board': 'SZ_MAIN', 'listing_date': 20220101},
        {'symbol': '600001.SH', 'board': 'SH_MAIN', 'listing_date': 20230303},
        {'symbol': '300001.SZ', 'board': 'GEM', 'listing_date': 20220101, 'delisting_date': 20230306},
        {'symbol': '688001.SH', 'board': 'STAR', 'listing_date': 20220101},
        {'symbol': '800001.BJ', 'board': 'BSE', 'listing_date': 20220101},
    ]


def universe(cache=(), records=None):
    return ResearchUniverseV1(records or master(), cache, master_source='REGISTERED_HISTORICAL_MASTER')


def test_three_boards_are_targets_and_missing_cache_keeps_denominator():
    result = universe([{'symbol': '000001.SZ', 'source_id': 'daily'}]).snapshot()
    assert result['target_count'] == 3
    assert result['cached_target_count'] == 1
    assert result['by_board']['CHINEXT']['target_count'] == 1
    assert result['by_board']['CHINEXT']['cached_count'] == 0
    rows = {r['symbol']: r for r in result['securities']}
    assert rows['300001.SZ']['status'] == 'CACHE_MISSING'
    assert rows['688001.SH']['status'] == 'OUT_OF_SCOPE'
    assert rows['800001.BJ']['status'] == 'OUT_OF_SCOPE'
    assert rows['300001.SZ']['st_status'] == 'UNKNOWN'
    assert rows['300001.SZ']['suspension_status'] == 'UNKNOWN'
    assert result['completeness'] == 'UNIVERSE_COMPLETENESS_UNKNOWN'


def test_current_cache_is_not_historical_master_or_independence_proof():
    cache = [{'symbol': row['symbol'], 'source_id': 'cache'} for row in master()]
    result = universe(cache).snapshot()
    assert result['cached_target_count'] == result['target_count'] == 3
    assert result['completeness'] == 'UNIVERSE_COMPLETENESS_UNKNOWN'
    assert result['historical_independence'] == 'UNKNOWN'
    assert not result['independent_confirmation_eligible']


def test_lifecycle_changes_on_effective_day_without_today_backfill():
    value = universe()
    early = {r['symbol']: r for r in value.qualification_at(20230302)}
    listed = {r['symbol']: r for r in value.qualification_at(20230303)}
    delisted = {r['symbol']: r for r in value.qualification_at(20230306)}
    assert early['600001.SH']['lifecycle_status'] == 'NOT_LISTED'
    assert listed['600001.SH']['lifecycle_status'] == 'LISTED_STATUS_REQUIRES_DAILY_EVIDENCE'
    assert delisted['300001.SZ']['lifecycle_status'] == 'DELISTED'
    assert not listed['600001.SH']['scan_eligible']
    assert not listed['600001.SH']['execution_eligible']


def test_unknown_board_and_listing_remain_targets_with_gaps():
    records = [{'symbol': '300001.SZ', 'board': 'UNKNOWN'},
               {'symbol': '000002.SZ', 'board': 'SH_MAIN', 'listing_date': 20220101},
               {'symbol': '600002.SH', 'board': 'SH_MAIN'}]
    value = universe(records=records)
    assert value.snapshot()['target_count'] == 3
    rows = {r['symbol']: r for r in value.qualification_at(20230303)}
    assert rows['300001.SZ']['lifecycle_status'] == 'BOARD_UNKNOWN'
    assert rows['000002.SZ']['lifecycle_status'] == 'BOARD_CONFLICT'
    assert rows['600002.SH']['lifecycle_status'] == 'LIFECYCLE_UNKNOWN'


def test_aliases_and_original_board_are_preserved():
    value = universe()
    assert canonical_symbol('sz.300001') == '300001.SZ'
    assert value.records['300001.SZ']['board'] == 'CHINEXT'
    assert value.records['300001.SZ']['original_board'] == 'GEM'
    assert scope_board('600001.SH') == 'SH_MAIN'
    assert scope_board('000001.SZ') == 'SZ_MAIN'
    assert scope_board('300001.SZ') == 'CHINEXT'


def test_duplicate_source_roots_do_not_duplicate_symbols_or_coverage(tmp_path, monkeypatch):
    root = tmp_path / 'data'
    root.mkdir()
    child = root / 'research'
    child.mkdir()
    (child / 'daily_all.parquet').write_bytes(b'not opened')
    (child / 'labels_m6.parquet').write_bytes(b'future labels not opened')
    (child / 'final_test_daily.parquet').write_bytes(b'sealed not opened')
    def deny_content(*args, **kwargs):
        raise AssertionError('inventory must not open contents')
    monkeypatch.setattr(Path, 'open', deny_content)
    result = inventory_research_paths([root, child, root])
    assert len(result['roots']) == 1
    assert len(result['files']) == 3
    assert not result['content_read']
    labels = next(r for r in result['files'] if 'labels' in r['path'])
    assert labels['reason'] == 'OUTCOME_SOURCE_METADATA_ONLY'
    assert not labels['signal_input_allowed']


def test_metadata_can_exist_for_sealed_files_but_qualification_is_refused():
    value = universe()
    with pytest.raises(FinalTestAccessViolation):
        value.qualification_at(20250801)


def test_inventory_retains_metadata_gap_if_file_disappears_during_walk(tmp_path, monkeypatch):
    first = tmp_path / 'daily_missing.parquet'
    second = tmp_path / 'daily_present.parquet'
    first.write_bytes(b'not opened')
    second.write_bytes(b'not opened')
    original = Path.lstat
    def changing_lstat(path):
        if path == first:
            raise FileNotFoundError(str(path))
        return original(path)
    monkeypatch.setattr(Path, 'lstat', changing_lstat)
    result = inventory_research_paths([tmp_path])
    assert [row['path'] for row in result['files']] == [str(second)]
    assert {'root': str(first), 'reason': 'METADATA_ACCESS_FAILED'} in result['skipped']
    assert not result['content_read']


def test_coverage_queue_resume_is_idempotent_and_does_not_lose_missing_stock():
    value = universe()
    assert value.coverage_queue(batch_size=2) == [['000001.SZ', '300001.SZ'], ['600001.SH']]
    assert value.coverage_queue(['000001.SZ', '000001.SZ'], batch_size=1) == [['300001.SZ'], ['600001.SH']]
    assert value.coverage_queue(value.target_symbols) == []
    with pytest.raises(ValueError, match='CHECKPOINT_OUTSIDE_TARGET'):
        value.coverage_queue(['000099.SZ'])


def test_conflicting_master_never_chooses_last_row():
    records = master() + [{'symbol': '300001.SZ', 'board': 'SZ_MAIN', 'listing_date': 20220101}]
    with pytest.raises(ValueError, match='UNIVERSE_MASTER_CONFLICT'):
        universe(records=records)


def test_inventory_and_target_have_separate_stable_identities():
    value = universe([{'symbol': '000001.SZ', 'source_id': 'a'}])
    other = universe([{'symbol': '000001.SZ', 'source_id': 'b'}])
    assert value.universe_identity == other.universe_identity
    assert value.coverage_identity != other.coverage_identity
