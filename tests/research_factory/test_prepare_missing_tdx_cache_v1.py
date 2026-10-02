import hashlib
import json
import struct

import pytest

from scripts.prepare_missing_tdx_cache_v1 import prepare_missing_tdx_cache_v1
from test_tdx_research_adapter_v1 import evidence


def setup_case(tmp_path, dates=(20230301, 20230302)):
    data, tdx = tmp_path / 'data', tmp_path / 'tdx'
    data.mkdir()
    raw = tdx / 'vipdoc/sz/lday/sz000001.day'
    raw.parent.mkdir(parents=True)
    # 封存报价存在于同一源，限定读取只能导出所需交易日。
    raw.write_bytes(b''.join(struct.pack('<IIIIIfII', d, 1000, 1100, 900, 1000,
                                        10000., 1000, 0) for d in (*dates, 20250801)))
    (data / 'calendar.json').write_text(json.dumps(list(dates)))
    qualification = [{'symbol': '000001.SZ', 'reasons': ['CACHE_MISSING', 'CORPORATE_ACTION_COVERAGE_NOT_PROVEN'],
                      'origin_status': 'UNKNOWN', 'indicator_qualification': 'UNKNOWN'}]
    (data / 'qualification.json').write_text(json.dumps(qualification))
    def sha(name):
        return hashlib.sha256((data / name).read_bytes()).hexdigest()
    manifest = {'adapter': 'TDX_FULL_UNIVERSE_V1', 'files': {
        'calendar.json': {'kind': 'CALENDAR', 'start': dates[0], 'end': dates[-1], 'sha256': sha('calendar.json')},
        'qualification.json': {'kind': 'SOURCE_QUALIFICATION', 'format': 'JSON', 'start': dates[0],
                               'end': dates[-1], 'sha256': sha('qualification.json'), 'source_id': 'qualification'},
        'existing.parquet': {'kind': 'DAILY', 'evidence': evidence()}}}
    path = data / 'manifest.json'
    path.write_text(json.dumps(manifest))
    return path, tdx, raw


def test_missing_cache_materializes_only_real_window_and_keeps_other_gaps(tmp_path):
    import pandas as pd
    path, tdx, raw = setup_case(tmp_path)
    before = raw.read_bytes()
    receipt = prepare_missing_tdx_cache_v1(manifest_path=path, tdx_root=tdx)
    assert receipt['created_count'] == receipt['requested_count'] == 1
    assert not receipt['account_data_ready']
    assert raw.read_bytes() == before
    rows = pd.read_parquet(path.parent / 'missing_tdx_v1/daily.parquet')
    assert rows.date.tolist() == [20230301, 20230302]
    assert 'prev_close' not in rows
    assert len(rows) == 2
    qualification = json.loads((path.parent / 'missing_tdx_v1/source_qualification.json').read_text())
    assert qualification[0]['reasons'] == ['CORPORATE_ACTION_COVERAGE_NOT_PROVEN']


def test_absent_raw_source_keeps_target_and_explicit_failure(tmp_path):
    path, tdx, raw = setup_case(tmp_path)
    raw.unlink()
    receipt = prepare_missing_tdx_cache_v1(manifest_path=path, tdx_root=tdx)
    assert receipt['requested_count'] == 1 and receipt['created_count'] == 0
    assert receipt['stocks'][0]['status'] == 'UNKNOWN'
    qualification = json.loads((path.parent / 'missing_tdx_v1/source_qualification.json').read_text())
    assert 'CACHE_MISSING' in qualification[0]['reasons']


def test_sealed_calendar_rejected_before_day_access_or_output(tmp_path):
    path, tdx, _ = setup_case(tmp_path, dates=(20250801, 20250804))
    with pytest.raises(Exception, match='(?i)final|sealed|holdout|2025-08|20250801'):
        prepare_missing_tdx_cache_v1(manifest_path=path, tdx_root=tdx)
    assert not (path.parent / 'missing_tdx_v1').exists()
