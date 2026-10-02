import hashlib
import json

import pytest

from scripts.prepare_baostock_universe_data_v1 import prepare_baostock_universe_data_v1
from test_research_data_provider_v1 import dataset
from test_research_data_provider_v1 import rewrite_response


def case(tmp_path):
    source = tmp_path / 'originals'
    args = dataset(source)
    legacy = source / 'manifest.json'
    master = tmp_path / 'master.json'
    master.write_text(json.dumps({'master': {'source': 'SYNTHETIC_MASTER', 'records': [
        {'symbol': s, 'listing_date': 20000103,
         'board': 'SZ_MAIN' if s.endswith('SZ') else 'SH_MAIN'} for s in args['symbols']]}}), encoding='utf-8')
    return dict(source_root=source, source_manifest=legacy, master_manifest=master,
                output_root=tmp_path / 'prepared', universe_id='TEST_LEGACY',
                feature_start=args['feature_start'], account_end=args['account_end'])


def test_copies_original_bytes_and_keeps_modeled_provenance(tmp_path):
    params = case(tmp_path)
    original = params['source_manifest'].read_bytes()
    manifest = prepare_baostock_universe_data_v1(**params)
    assert params['source_manifest'].read_bytes() == original
    assert manifest['adapter'] == 'BAOSTOCK_FULL_UNIVERSE_V1'
    assert manifest['historical_availability'] == 'MODELED'
    for name, metadata in manifest['files'].items():
        copied = (params['output_root'] / name).read_bytes()
        assert copied == (params['source_root'] / name).read_bytes()
        assert hashlib.sha256(copied).hexdigest() == metadata['sha256']
    assert manifest['listing_date_sources'] == {s: 'registered_manifest' for s in manifest['listing_dates']}


def test_missing_year_file_is_not_filled_with_empty_events(tmp_path):
    params = case(tmp_path)
    manifest = json.loads(params['source_manifest'].read_text())
    del manifest['files'][next(n for n in manifest['files'] if n.startswith('DIVIDEND_'))]
    params['source_manifest'].write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError, match='DATA_SOURCE_NOT_REGISTERED'):
        prepare_baostock_universe_data_v1(**params)
    assert not params['output_root'].exists()


def test_hash_conflict_is_not_registered_as_valid_source(tmp_path):
    params = case(tmp_path)
    path = params['source_root'] / 'TRADE_DATES.json'
    path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(ValueError, match='DATA_SOURCE_CONTENT_CHANGED'):
        prepare_baostock_universe_data_v1(**params)
    assert not (params['output_root'] / 'manifest.json').exists()


def test_sealed_whole_file_rejected_before_any_copy(tmp_path):
    params = case(tmp_path)
    manifest = json.loads(params['source_manifest'].read_text())
    manifest['files']['TRADE_DATES.json']['end'] = '2025-08-01'
    params['source_manifest'].write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(Exception, match='(?i)final|sealed|holdout|2025-08|20250801'):
        prepare_baostock_universe_data_v1(**params)
    assert not params['output_root'].exists()


def test_safe_manifest_cannot_hide_sealed_actual_query_before_copy(tmp_path):
    params = case(tmp_path)
    rewrite_response(params['source_root'], 'TRADE_DATES.json',
        lambda value: value['request'].update(end_date='2025-08-01'))
    with pytest.raises(Exception, match='(?i)final|sealed|holdout|2025-08|20250801'):
        prepare_baostock_universe_data_v1(**params)
    assert not params['output_root'].exists()


def test_report_year_registration_records_actual_query_scope_without_rewriting_original(tmp_path):
    params = case(tmp_path)
    original_manifest = params['source_manifest'].read_bytes()
    manifest = prepare_baostock_universe_data_v1(**params)
    annual = next(v for k, v in manifest['files'].items() if k.startswith('DIVIDEND_'))
    assert annual['end'] == 20241231
    assert manifest['dividend_year_type'] == 'report'
    assert params['source_manifest'].read_bytes() == original_manifest
