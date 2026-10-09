"""TDX单位/来源/价格与提供器边界测试；仅使用临时合成原件。"""
import hashlib
import json
import weakref
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from chanlun_trader.research.guard import FinalTestAccessViolation
from chanlun_trader.research_factory.tdx_research_adapter_v1 import (
    TdxResearchAdapterV1, compare_cache_to_raw_window, merge_daily_sources,
)
from chanlun_trader.research_factory.universe_data_provider_v1 import UniverseDataProviderV1


SYMBOLS = ['000001.SZ', '300001.SZ', '600001.SH']
DATES = [20230301, 20230302, 20230303, 20230306]


def evidence(digest='a' * 64):
    return {'provider': 'TDX', 'source_id': 'tdx_daily', 'source_sha256': digest,
        'price_mode': 'RAW', 'volume_unit': 'SHARES', 'amount_unit': 'CNY',
        'unit_evidence': {'source': 'UNIT_PROOF_SYNTHETIC', 'sha256': 'b' * 64,
                          'volume_multiplier': 1, 'amount_multiplier': 1},
        'transformation_version': 'SYNTHETIC_RAW_CACHE_V1', 'transformation_sha256': 'c' * 64,
        'original_source_hashes': {'tdx_original': 'd' * 64},
        'prev_close_semantics': 'DERIVED_PREVIOUS_VALID_CLOSE',
        'historical_available_at_verified': False}


def daily(symbols=SYMBOLS):
    return pd.DataFrame([dict(symbol=symbol, date=date, open=10., high=11., low=9.,
        close=10., volume=1000., amount=10000., prev_close=np.nan if i == 0 else 10.)
        for symbol in symbols for i, date in enumerate(DATES)])


def normalize(frame=None, proof=None, required=()):
    return TdxResearchAdapterV1().normalize_daily(daily() if frame is None else frame,
        evidence=evidence() if proof is None else proof, required_fields=required)


def test_tdx_source_is_preserved_without_baostock_impersonation_and_same_board_capabilities():
    result = normalize()
    assert result['source_evidence']['evidence']['provider'] == 'TDX'
    assert result['source_evidence']['symbols'] == SYMBOLS
    assert not result['qualification']['account_data_ready']
    assert not result['qualification']['independent_confirmation_eligible']
    assert result['qualification']['historical_availability'] == 'UNKNOWN'
    assert result['daily'].prev_close.isna().all()
    assert result['daily'].derived_previous_valid_close.isna().sum() == 3


def test_first_nan_and_ex_date_derived_close_never_silently_become_reference():
    frame = daily()
    frame.loc[frame.date == 20230302, 'close'] = 9.5
    result = normalize(frame)
    assert result['daily'].prev_close.isna().all()
    assert result['daily'].loc[result['daily'].date == 20230302, 'derived_previous_valid_close'].eq(10).all()
    with pytest.raises(ValueError, match='DATA_REQUIRED_FIELD_MISSING:prev_close'):
        normalize(frame, required=['prev_close'])


def test_units_require_evidence_and_convert_only_declared_multipliers():
    proof = evidence()
    proof['volume_unit'] = 'LOTS'
    proof['amount_unit'] = 'WAN_CNY'
    proof['unit_evidence']['volume_multiplier'] = 100
    proof['unit_evidence']['amount_multiplier'] = 10000
    result = normalize(proof=proof)
    assert result['daily'].volume.eq(100000).all()
    assert result['daily'].amount.eq(100000000).all()
    proof['unit_evidence']['volume_multiplier'] = 1
    with pytest.raises(ValueError, match='TDX_UNIT_CONVERSION_CONFLICT'):
        normalize(proof=proof)
    proof = evidence()
    proof['volume_unit'] = 'UNKNOWN'
    with pytest.raises(ValueError, match='TDX_UNIT_UNKNOWN'):
        normalize(proof=proof)
    proof = evidence()
    proof['unit_evidence'] = {'status': 'READY'}
    with pytest.raises(ValueError, match='TDX_UNIT_EVIDENCE_REQUIRED'):
        normalize(proof=proof)


def test_duplicate_keys_and_conflicting_sources_are_rejected():
    frame = daily()
    with pytest.raises(ValueError, match='TDX_DUPLICATE_SYMBOL_DATE'):
        normalize(pd.concat([frame, frame.iloc[:1]]))
    good = normalize()['daily']
    same = merge_daily_sources([good, good.copy()])
    assert len(same) == len(good)
    bad = good.copy()
    bad.loc[0, 'close'] = 10.1
    with pytest.raises(ValueError, match='TDX_MULTI_SOURCE_VALUE_CONFLICT'):
        merge_daily_sources([good, bad])


def test_turn_is_optional_and_is_not_filled_when_required():
    result = normalize()
    assert 'turn' not in result['turn']
    assert result['qualification']['field_status']['turn'] == 'UNKNOWN'
    with pytest.raises(ValueError, match='DATA_REQUIRED_FIELD_MISSING:turn'):
        normalize(required=['turn'])
    frame = daily()
    frame['turn'] = .01
    proof = evidence()
    proof.update(turn_unit='FRACTION', turn_evidence={'source': 'RECORDED_TURN', 'sha256': 'e' * 64})
    result = normalize(frame, proof, required=['turn'])
    assert result['turn'].turn.eq(1).all()


def test_vendor_reference_needs_separate_evidence_and_finite_coverage():
    proof = evidence()
    proof['prev_close_semantics'] = 'VENDOR_REFERENCE'
    with pytest.raises(ValueError, match='TDX_REFERENCE_EVIDENCE_REQUIRED'):
        normalize(proof=proof)
    proof['reference_evidence'] = {'source': 'VENDOR_PRECLOSE', 'sha256': 'f' * 64}
    frame = daily().fillna({'prev_close': 10.})
    result = normalize(frame, proof, required=['prev_close'])
    assert result['daily'].prev_close.eq(10).all()
    assert 'derived_previous_valid_close' not in result['daily']


def test_matching_another_vendor_does_not_upgrade_historical_visibility():
    proof = evidence()
    proof['cross_vendor_match'] = True
    result = normalize(proof=proof)
    assert result['qualification']['historical_availability'] == 'UNKNOWN'
    proof['historical_available_at_verified'] = True
    with pytest.raises(ValueError, match='TDX_HISTORICAL_AVAILABILITY_NOT_CERTIFIED'):
        normalize(proof=proof)


def test_source_and_transformation_change_identity_without_mutating_input():
    frame = daily()
    original = frame.copy(deep=True)
    result = normalize(frame)
    other = evidence()
    other['transformation_version'] = 'SYNTHETIC_RAW_CACHE_V2'
    assert result['source_identity'] != normalize(frame, other)['source_identity']
    pd.testing.assert_frame_equal(frame, original)


def test_cache_origin_comparison_requires_all_dates_and_fields():
    cache = daily()
    raw = cache.rename(columns={'volume': 'volume_encoded', 'amount': 'amount_encoded'})
    good = compare_cache_to_raw_window(cache, raw)
    assert good['passed']
    assert good['comparison_basis'] == 'EXACT_RAW_ENCODING_VALUES_NOT_UNIT_OR_PIT_PROOF'
    raw.loc[0, 'volume_encoded'] += 1
    bad = compare_cache_to_raw_window(cache, raw)
    assert not bad['passed']
    assert not bad['field_checks']['volume']
    missing = compare_cache_to_raw_window(cache, raw.iloc[1:])
    assert missing['missing_raw_rows'] == 1
    assert not missing['passed']


def test_zero_activity_is_not_a_price_failure_and_outcome_columns_are_not_carried():
    frame = daily()
    frame.loc[0, ['volume', 'amount']] = 0
    frame['future_return'] = .9
    result = normalize(frame)
    assert result['daily'].volume.iloc[0] == 0
    assert 'future_return' not in result['daily']
    frame.loc[0, 'close'] = 0
    with pytest.raises(ValueError, match='TDX_PRICE_OR_ACTIVITY_INVALID'):
        normalize(frame)


def registered_dataset(root, *, physical_dates=DATES, declared_end=20230306):
    root.mkdir(parents=True, exist_ok=True)
    frame = daily()
    if physical_dates != DATES:
        frame['date'] = [date for _ in SYMBOLS for date in physical_dates]
    frame.to_parquet(root / 'daily.parquet', index=False)
    (root / 'calendar.json').write_text(json.dumps(DATES), encoding='utf-8')
    def digest(name):
        return hashlib.sha256((root / name).read_bytes()).hexdigest()
    files = {'daily.parquet': {'kind': 'DAILY', 'format': 'PARQUET', 'start': 20230301,
        'end': declared_end, 'sha256': digest('daily.parquet'), 'source_id': 'tdx_daily',
        'symbols': SYMBOLS, 'evidence': evidence(digest('daily.parquet'))},
        'calendar.json': {'kind': 'CALENDAR', 'format': 'JSON', 'start': 20230301,
        'end': 20230306, 'sha256': digest('calendar.json'), 'source_id': 'calendar'}}
    manifest = {'adapter': 'TDX_FULL_UNIVERSE_V1', 'universe_id': 'ALL_SUPPORTED',
        'start': 20230301, 'end': 20230306, 'files': files,
        'master': {'source': 'SYNTHETIC_HISTORICAL_MASTER', 'records': [
            {'symbol': symbol, 'board': board, 'listing_date': 20220101}
            for symbol, board in zip(SYMBOLS, ['SZ_MAIN', 'CHINEXT', 'SH_MAIN'])]}}
    (root / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    args = {'feature_start': 20230301, 'account_start': 20230303, 'account_end': 20230306,
        'stage': 'SCAN', 'authorization': {'authorization_id': 'SYNTHETIC_AUTHORIZED',
        'purpose': 'EXPLORATORY', 'dataset_ids': ['sample'], 'start': 20230301, 'end': 20230306}}
    return manifest, args


def provider(root, events):
    value = UniverseDataProviderV1({'root': root}, events.append)
    value.register('sample', 'root', 'manifest.json')
    return value


def test_registered_metadata_path_is_internal_and_cannot_be_chosen_by_strategy(tmp_path):
    _, args = registered_dataset(tmp_path)
    events = []
    service = provider(tmp_path, events)
    assert service._metadata_paths == {'sample': (tmp_path / 'manifest.json').absolute()}
    catalog = service.catalog()
    assert str(tmp_path / 'manifest.json') not in json.dumps(catalog)
    assert 'metadata_path' not in catalog['datasets'][0]
    with pytest.raises(TypeError, match='metadata_path'):
        service.prepare('sample', metadata_path='another_manifest.json', **args)
    assert events == []


def test_provider_catalog_reads_metadata_only_and_keeps_entire_target(tmp_path):
    registered_dataset(tmp_path)
    events = []
    catalog = provider(tmp_path, events).catalog()
    assert catalog['content_read'] is False
    row = catalog['datasets'][0]
    assert row['symbols'] == SYMBOLS
    assert row['target_count'] == 3
    assert row['by_board']['CHINEXT']['target_count'] == 1
    assert events == []


def test_provider_cannot_be_requested_as_small_handpicked_pool(tmp_path):
    _, args = registered_dataset(tmp_path)
    events = []
    with pytest.raises(ValueError, match='DATA_FULL_UNIVERSE_REQUIRED'):
        provider(tmp_path, events).prepare('sample', symbols=[SYMBOLS[0]], **args)
    assert events == []


def test_provider_scan_stage_uses_dynamic_inputs_without_inventing_account_qualification(tmp_path):
    _, args = registered_dataset(tmp_path)
    events = []
    result = provider(tmp_path, events).prepare('sample', **args)
    assert result['window']['symbols'] == SYMBOLS
    assert result['window']['calendar'] == DATES
    assert result['bundle']['daily'].prev_close.isna().all()
    assert result['bundle']['calendar_source'] == 'calendar'
    assert result['bundle']['price_basis']['execution'] == 'RAW'
    assert result['bundle']['source_hashes']['tdx_daily']
    assert len(result['input_identity']) == 64
    assert not result['qualification']['account_data_ready']
    assert not result['qualification']['independent_confirmation_eligible']
    assert events[-1]['event'] == 'DATA_BUNDLE_PREPARED'


def test_private_preparation_returns_one_inputs_object_without_changing_public_prepared_contract(tmp_path):
    _, args = registered_dataset(tmp_path)
    service = provider(tmp_path, [])
    prepared, inputs = service._prepare_with_inputs('sample', required_fields=('close',),
        normalization_fields=(), warmup_bars=3, **args)
    public = service.prepare('sample', required_fields=('close',), **args)
    assert set(prepared) == set(public) == {'window', 'bundle', 'qualification', 'input_identity'}
    assert inputs.bundle is prepared['bundle']
    assert inputs.coverage is prepared['qualification']['coverage']
    assert inputs.input_identity == prepared['input_identity'] == public['input_identity']
    assert inputs.required_fields == frozenset({'close'}) and inputs.warmup_bars == 3
    assert prepared['window']['symbols'] == SYMBOLS
    # 可冻结元数据中没有私有对象，公开调用也不能选择内部复用开关。
    json.dumps({k: v for k, v in prepared.items() if k != 'bundle'})
    with pytest.raises(TypeError, match='normalization_fields'):
        service.prepare('sample', normalization_fields=(), **args)


def test_provider_owns_newly_read_frames_but_public_inputs_still_isolate_callers(tmp_path, monkeypatch):
    from chanlun_trader.research_factory.universe_account_inputs_v1 import (
        UniverseAccountInputsV1, prepare_universe_account_inputs_v1,
    )
    _, args = registered_dataset(tmp_path)
    original_sha = hashlib.sha256((tmp_path / 'daily.parquet').read_bytes()).hexdigest()
    captured = []
    original = UniverseAccountInputsV1._frame
    def tracked(self, value, date_col, name, **kwargs):
        frame = original(self, value, date_col, name, **kwargs)
        if name == 'daily':
            captured.append((value, frame))
        return frame
    monkeypatch.setattr(UniverseAccountInputsV1, '_frame', tracked)
    prepared, inputs = provider(tmp_path, [])._prepare_with_inputs('sample', **args)
    source, normalized = captured[0]
    assert np.shares_memory(source.close.to_numpy(copy=False), normalized.close.to_numpy(copy=False))
    assert inputs._copy_frames is False
    public = prepare_universe_account_inputs_v1(prepared['bundle'], prepared['window'], stage='SCAN')
    assert public._copy_frames is True and public.input_identity == inputs.input_identity
    original_close = public.daily.close.iloc[0]
    prepared['bundle']['daily'].loc[0, 'close'] = original_close + 1
    assert public.daily.close.iloc[0] == original_close
    public.assert_unchanged()
    assert hashlib.sha256((tmp_path / 'daily.parquet').read_bytes()).hexdigest() == original_sha


def test_private_scan_keeps_optional_field_gaps_while_public_required_source_field_stays_strict(tmp_path):
    _, args = registered_dataset(tmp_path)
    service = provider(tmp_path, [])
    with pytest.raises(ValueError, match='DATA_REQUIRED_FIELD_MISSING:turn'):
        service.prepare('sample', required_fields=('turn',), **args)
    prepared, inputs = service._prepare_with_inputs('sample', required_fields=('turn',),
        normalization_fields=(), **args)
    assert inputs.required_fields == frozenset({'turn'})
    assert prepared['qualification']['coverage']['target_symbol_count'] == len(SYMBOLS)
    assert not prepared['qualification']['account_data_ready']
    assert 'turn' not in prepared['bundle']['turn']


@pytest.mark.parametrize('change,reason', [({'required_fields': ('future_return',)}, 'DATA_FIELD_UNSUPPORTED'),
    ({'normalization_fields': ('future_return',)}, 'DATA_FIELD_UNSUPPORTED'),
    ({'warmup_bars': -1}, 'UNIVERSE_REQUIRED_FIELDS_INVALID'),
    ({'warmup_bars': True}, 'UNIVERSE_REQUIRED_FIELDS_INVALID')])
def test_private_rule_requirements_are_validated_before_registered_content(tmp_path, change, reason):
    _, args = registered_dataset(tmp_path)
    events = []
    with pytest.raises(ValueError, match=reason):
        provider(tmp_path, events)._prepare_with_inputs('sample', **change, **args)
    assert events == []


def test_provider_unknown_required_field_is_refused_before_content(tmp_path):
    _, args = registered_dataset(tmp_path)
    events = []
    with pytest.raises(ValueError, match='DATA_FIELD_UNSUPPORTED'):
        provider(tmp_path, events).prepare('sample', required_fields=['future_return'], **args)
    assert not events


def test_source_qualification_mapping_is_registered_and_preserves_unknown_target(tmp_path):
    manifest, args = registered_dataset(tmp_path)
    rows = [{'symbol': symbol, 'indicator_qualification':
        'UNKNOWN' if i == 0 else 'RAW_PRICE_AND_MODELED_UNIT_READY',
        'origin_status': 'UNKNOWN' if i == 0 else 'EXACT_RAW_WINDOW_MATCH',
        'reasons': ['RAW_ORIGIN_NOT_CHECKED'] if i == 0 else []}
        for i, symbol in enumerate(SYMBOLS)]
    path = tmp_path / 'sources.json'
    path.write_text(json.dumps(rows), encoding='utf-8')
    manifest['files']['sources.json'] = {'kind': 'SOURCE_QUALIFICATION', 'format': 'JSON',
        'start': 20230301, 'end': 20230306, 'source_id': 'source_qualification',
        'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    result = provider(tmp_path, []).prepare('sample', **args)
    assert result['window']['symbols'] == SYMBOLS
    assert result['bundle']['source_qualification'][SYMBOLS[0]]['indicator_qualification'] == 'UNKNOWN'
    assert result['bundle']['source_hashes']['source_qualification']
    assert not result['qualification']['account_data_ready']


def test_source_qualification_cannot_drop_a_target_from_mapping(tmp_path):
    manifest, args = registered_dataset(tmp_path)
    path = tmp_path / 'sources.json'
    path.write_text(json.dumps([{'symbol': SYMBOLS[0], 'indicator_qualification': 'UNKNOWN'}]), encoding='utf-8')
    manifest['files']['sources.json'] = {'kind': 'SOURCE_QUALIFICATION', 'format': 'JSON',
        'start': 20230301, 'end': 20230306, 'source_id': 'source_qualification',
        'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError, match='DATA_SOURCE_QUALIFICATION_TARGET_MISMATCH'):
        provider(tmp_path, []).prepare('sample', **args)


def test_historical_universe_scope_cannot_be_extended_to_longer_cache_scope(tmp_path):
    manifest, args = registered_dataset(tmp_path)
    manifest['universe_scope'] = {'start': 20230301, 'end': 20230303}
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    events = []
    with pytest.raises(ValueError, match='DATA_HISTORICAL_UNIVERSE_SCOPE_NOT_COVERED'):
        provider(tmp_path, events).prepare('sample', **args)
    assert events == []


def test_physical_source_scope_is_checked_before_any_content(tmp_path):
    _, args = registered_dataset(tmp_path)
    args['feature_start'] = 20230302
    args['authorization']['start'] = 20230302
    events = []
    with pytest.raises(ValueError, match='DATA_WHOLE_SOURCE_NOT_AUTHORIZED'):
        provider(tmp_path, events).prepare('sample', **args)
    assert events == []


def test_sealed_parquet_cannot_hide_under_incorrect_manifest_bounds(tmp_path):
    _, args = registered_dataset(tmp_path, physical_dates=[20230301, 20230302, 20230303, 20250801])
    events = []
    with pytest.raises(FinalTestAccessViolation):
        provider(tmp_path, events).prepare('sample', **args)
    assert events == []


def test_source_change_is_refused_before_dataframe_materialization(tmp_path, monkeypatch):
    _, args = registered_dataset(tmp_path)
    events = []
    service = provider(tmp_path, events)
    frame = daily()
    frame.loc[0, 'amount'] += 1
    frame.to_parquet(tmp_path / 'daily.parquet', index=False)
    monkeypatch.setattr(service, '_read_parquet', lambda *a: pytest.fail('原件 SHA 校验必须先于分批加载'))
    with pytest.raises(ValueError, match='DATA_SOURCE_CONTENT_CHANGED'):
        service.prepare('sample', **args)
    assert all(event['event'] != 'DATA_BUNDLE_PREPARED' for event in events)


def test_unqualified_formal_purpose_is_refused_without_source_access(tmp_path):
    _, args = registered_dataset(tmp_path)
    events = []
    with pytest.raises(ValueError, match='DATA_PURPOSE_NOT_QUALIFIED'):
        provider(tmp_path, events).prepare('sample', purpose='INDEPENDENT', **args)
    assert not events


def test_outcome_file_cannot_be_registered_as_daily(tmp_path):
    manifest, _ = registered_dataset(tmp_path)
    (tmp_path / 'labels.parquet').write_bytes((tmp_path / 'daily.parquet').read_bytes())
    manifest['files']['labels.parquet'] = manifest['files'].pop('daily.parquet')
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError, match='DATA_OUTCOME_SOURCE_FORBIDDEN'):
        provider(tmp_path, [])


def test_source_path_escape_is_rejected_at_registration(tmp_path):
    manifest, _ = registered_dataset(tmp_path)
    manifest['files']['../daily.parquet'] = manifest['files'].pop('daily.parquet')
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError, match='DATA_PATH_OUTSIDE_ROOT'):
        provider(tmp_path, [])


@pytest.mark.parametrize('relative', [r'\outside.json', '/outside.json', r'E:outside.json',
    r'E:\outside.json', r'\\server\share\outside.json', r'\\?\E:\outside.json',
    '../outside.json', r'nested\..\outside.json', 'nested/../outside.json'])
def test_source_path_escape_is_rejected_before_any_file_probe(tmp_path, monkeypatch, relative):
    probes = []
    def unexpected_probe(*args, **kwargs):
        probes.append(args)
        raise AssertionError('非法路径不得进行文件探测')
    with monkeypatch.context() as patch:
        for method in ('resolve', 'is_file', 'stat'):
            patch.setattr(Path, method, unexpected_probe)
        with pytest.raises(ValueError, match='DATA_PATH_OUTSIDE_ROOT'):
            UniverseDataProviderV1._path(tmp_path, relative)
    assert probes == []


def test_source_path_allows_registered_subfile_and_still_refuses_missing_file(tmp_path):
    nested = tmp_path / 'nested'
    nested.mkdir()
    target = nested / 'daily.json'
    target.write_text('[]', encoding='utf-8')
    assert UniverseDataProviderV1._path(tmp_path, 'nested/daily.json') == target
    with pytest.raises(ValueError, match='DATA_PATH_INVALID_OR_REDIRECTED'):
        UniverseDataProviderV1._path(tmp_path, 'nested/missing.json')


def test_source_path_still_refuses_symlink_to_existing_file(tmp_path):
    target = tmp_path / 'actual.json'
    target.write_text('[]', encoding='utf-8')
    link = tmp_path / 'redirect.json'
    try:
        link.symlink_to(target)
    except OSError as exc:
        if getattr(exc, 'winerror', None) in {1, 50, 1314}:
            pytest.skip('Windows 当前文件系统或账户不支持创建符号链接')
        raise
    assert link.is_file()
    with pytest.raises(ValueError, match='DATA_PATH_INVALID_OR_REDIRECTED'):
        UniverseDataProviderV1._path(tmp_path, link.name)


def test_source_path_refuses_resolved_redirection_before_file_probe(tmp_path, monkeypatch):
    target = tmp_path / 'redirect.json'
    redirected = tmp_path.parent / 'outside.json'
    resolutions = []
    def resolve(path, *args, **kwargs):
        resolutions.append(path)
        return redirected
    def unexpected_probe(*args, **kwargs):
        raise AssertionError('解析后重定向的路径不得进行文件探测')
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'resolve', resolve)
        patch.setattr(Path, 'is_file', unexpected_probe)
        with pytest.raises(ValueError, match='DATA_PATH_INVALID_OR_REDIRECTED'):
            UniverseDataProviderV1._path(tmp_path, target.name)
    assert resolutions == [target]


def test_new_tdx_chinext_support_does_not_broaden_old_baostock_contract(tmp_path):
    from chanlun_trader.research_factory.research_data_provider_v1 import ResearchDataProviderV1
    old = ResearchDataProviderV1({'root': tmp_path}, lambda event: None)
    with pytest.raises(ValueError, match='DATA_SYMBOLS_INVALID'):
        old.prepare('not_registered', symbols=['300001.SZ'], feature_start=20230301,
            account_start=20230303, account_end=20230306)


def long_registered_dataset(root):
    manifest, args = registered_dataset(root)
    frame = daily()
    outside = frame.loc[frame.date == DATES[0]].copy()
    outside['date'] = 20230227
    future = outside.copy()
    future['date'] = 20230403
    pd.concat([outside, frame, future], ignore_index=True).to_parquet(root / 'daily.parquet', index=False)
    digest = hashlib.sha256((root / 'daily.parquet').read_bytes()).hexdigest()
    manifest['start'], manifest['end'] = 20230227, 20230403
    metadata = manifest['files']['daily.parquet']
    metadata.update(start=20230227, end=20230403, sha256=digest, evidence=evidence(digest))
    manifest['master']['records'].append({'symbol': '000002.SZ', 'board': 'SZ_MAIN', 'listing_date': 20220101})
    (root / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    args['feature_start'] = 20230302
    args['authorization'].update(start=20230227, end=20230403)
    return manifest, args


def test_long_source_is_windowed_before_normalization_without_losing_targets_or_identity(tmp_path, monkeypatch):
    manifest, args = long_registered_dataset(tmp_path)
    events, raw_references, unused_turns, seen = [], [], [], []
    service = provider(tmp_path, events)
    original_read, original_normalize = service._read, TdxResearchAdapterV1.normalize_daily
    def recorded_read(root, name, metadata, dataset_id, authorization):
        result = original_read(root, name, metadata, dataset_id, authorization)
        if metadata['kind'] == 'DAILY':
            raw_references.append(weakref.ref(result))
        return result
    def windowed(instance, frame, **kwargs):
        assert raw_references[-1]() is None, '整缓存应在昂贵规范化前释放'
        assert set(frame.date) == set(DATES[1:])
        assert set(frame.symbol) == set(SYMBOLS)
        seen.append(len(frame))
        result = original_normalize(instance, frame, **kwargs)
        unused_turns.append(weakref.ref(result['turn']))
        return result
    monkeypatch.setattr(service, '_read', recorded_read)
    monkeypatch.setattr(TdxResearchAdapterV1, 'normalize_daily', windowed)
    first, second = service.prepare('sample', **args), service.prepare('sample', **args)
    assert seen == [9, 9]
    assert first['input_identity'] == second['input_identity']
    assert first['window']['symbols'] == sorted([*SYMBOLS, '000002.SZ'])
    assert first['qualification']['coverage']['target_symbol_count'] == 4
    assert all(reference() is None for reference in unused_turns)
    assert first['bundle']['source_hashes']['tdx_daily'] == manifest['files']['daily.parquet']['sha256']
    assert not first['qualification']['account_data_ready']
    columns = ['symbol', 'date', 'open', 'high', 'low', 'close', 'volume', 'amount']
    expected = daily().loc[lambda f: f.date.isin(DATES[1:]), columns].reset_index(drop=True)
    pd.testing.assert_frame_equal(first['bundle']['daily'][columns], expected)
    assert first['bundle']['daily'].prev_close.isna().all()


def test_invalid_date_outside_requested_window_cannot_be_filtered_away(tmp_path):
    manifest, args = registered_dataset(tmp_path)
    rows = daily().to_dict('records')
    rows.append({**rows[0], 'date': 20230227.5})
    path = tmp_path / 'daily.json'
    path.write_text(json.dumps(rows), encoding='utf-8')
    metadata = manifest['files'].pop('daily.parquet')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    metadata.update(format='JSON', start=20230227, sha256=digest, evidence=evidence(digest))
    manifest['files']['daily.json'] = metadata
    manifest['start'] = 20230227
    args['authorization']['start'] = 20230227
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError, match='TDX_DATE_INVALID'):
        provider(tmp_path, []).prepare('sample', **args)


def test_window_without_bars_preserves_entire_target_as_data_gaps(tmp_path):
    manifest, args = long_registered_dataset(tmp_path)
    path = tmp_path / 'daily.parquet'
    frame = pd.read_parquet(path)
    frame.loc[~frame.date.isin(DATES)].to_parquet(path, index=False)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest['files'][path.name].update(sha256=digest, evidence=evidence(digest))
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    service = provider(tmp_path, [])
    first, second = service.prepare('sample', **args), service.prepare('sample', **args)
    assert first['bundle']['daily'].empty
    assert first['window']['symbols'] == sorted([*SYMBOLS, '000002.SZ'])
    assert first['qualification']['coverage']['target_symbol_count'] == 4
    assert len(first['qualification']['coverage']['per_symbol']) == 4
    assert not first['qualification']['account_data_ready']
    assert first['input_identity'] == second['input_identity']


def test_short_window_still_rejects_sealed_whole_file_before_materializing_prices(tmp_path, monkeypatch):
    _, args = registered_dataset(tmp_path, physical_dates=[20230301, 20230302, 20230303, 20250801])
    args['feature_start'] = 20230302
    events = []
    service = provider(tmp_path, events)
    monkeypatch.setattr(service, '_read_parquet', lambda *a: pytest.fail('整文件封存检查必须先于分批行情加载'))
    with pytest.raises(FinalTestAccessViolation):
        service.prepare('sample', **args)
    assert events == []


def test_windowing_does_not_relax_same_window_multi_source_value_conflicts(tmp_path):
    manifest, args = long_registered_dataset(tmp_path)
    other = pd.read_parquet(tmp_path / 'daily.parquet')
    other.loc[(other.symbol == SYMBOLS[0]) & (other.date == DATES[1]), 'amount'] += 1
    path = tmp_path / 'other_daily.parquet'
    other.to_parquet(path, index=False)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    proof = evidence(digest)
    proof['source_id'] = 'other_tdx_daily'
    manifest['files'][path.name] = {**manifest['files']['daily.parquet'],
        'source_id': 'other_tdx_daily', 'sha256': digest, 'evidence': proof}
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError, match='TDX_MULTI_SOURCE_VALUE_CONFLICT'):
        provider(tmp_path, []).prepare('sample', **args)


def test_long_state_source_preserves_original_fields_and_times_in_short_window(tmp_path):
    manifest, args = long_registered_dataset(tmp_path)
    state_dates = [20230227, *DATES, 20230403]
    rows = [{'symbol': symbol, 'trade_date': day, 'listed': True, 'delisted': False,
        'universe_member': True, 'eligibility_status': 'ELIGIBLE', 'st_status': 'NORMAL',
        'suspension_status': 'TRADING', 'board': 'CHINEXT' if symbol.startswith('30') else (
            'SH_MAIN' if symbol.startswith('60') else 'SZ_MAIN'), 'source': 'states',
        'available_at': '2026-08-23T14:04:00+08:00', 'availability_status': 'MODELED',
        'source_record_time': '2026-08-23T14:04:00+08:00', 'original_lineage': 'synthetic_original_unchanged'}
        for symbol in SYMBOLS for day in state_dates]
    path = tmp_path / 'states.parquet'
    pd.DataFrame(rows).to_parquet(path, index=False)
    manifest['files'][path.name] = {'kind': 'STATES', 'format': 'PARQUET',
        'source_id': 'states', 'date_column': 'trade_date', 'start': 20230227, 'end': 20230403,
        'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    result = provider(tmp_path, []).prepare('sample', **args)
    frame = result['bundle']['states']
    assert len(frame) == 9 and set(frame.trade_date) == set(DATES[1:])
    assert set(frame.original_lineage) == {'synthetic_original_unchanged'}
    assert set(frame.available_at) == {'2026-08-23T14:04:00+08:00'}
    assert set(frame.availability_status) == {'MODELED'}
    assert result['qualification']['coverage']['target_symbol_count'] == 4
    assert not result['qualification']['account_data_ready']


def test_parquet_batches_preserve_all_values_dtypes_nulls_and_share_long_strings(tmp_path, monkeypatch):
    _, args = registered_dataset(tmp_path)
    count = 8192 * 2 + 5
    long_source = 'synthetic_original_lineage_' + '原始来源证据' * 200
    frame = pd.DataFrame({
        'symbol': [SYMBOLS[i % len(SYMBOLS)] for i in range(count)],
        'trade_date': [DATES[i % len(DATES)] for i in range(count)],
        'source': pd.Series([long_source] * count, dtype='object'),
        'available_at': ['2026-08-23T14:04:00+08:00'] * count,
        'availability_status': ['MODELED'] * count,
        'source_lineage_json': pd.Series([long_source] * count, dtype='object'),
        'original_integer': pd.Series(np.arange(count), dtype='int32'),
        'nullable_integer': pd.Series(np.arange(count), dtype='Int64'),
        'raw_float': pd.Series(np.arange(count), dtype='float32'),
        'original_bool': [True] * count,
        'nullable_bool': pd.Series([True] * count, dtype='boolean'),
        'original_bytes': [b'unchanged'] * count,
    })
    frame.loc[[1, 8192, count - 1], 'source'] = None
    frame.loc[8193, ['nullable_integer', 'nullable_bool', 'raw_float']] = pd.NA
    path = tmp_path / 'states.parquet'
    frame.to_parquet(path, index=False)
    metadata = {'kind': 'STATES', 'format': 'PARQUET', 'source_id': 'states',
        'start': DATES[0], 'end': DATES[-1], 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    sizes = []
    original = pq.ParquetFile.iter_batches
    def batches(parquet, *a, **kw):
        assert kw['use_threads'] is False
        for batch in original(parquet, *a, **kw):
            sizes.append(batch.num_rows)
            yield batch
    monkeypatch.setattr(pq.ParquetFile, 'iter_batches', batches)
    result = provider(tmp_path, [])._read(tmp_path, path.name, metadata, 'sample', args['authorization'])
    pd.testing.assert_frame_equal(result, frame, check_exact=True)
    assert len(sizes) == 3 and sum(sizes) == count and max(sizes) <= 8192
    assert result.source.iloc[1] is None and result.source.iloc[8192] is None
    assert result.source.iloc[0] is result.source.iloc[8193]
    assert result.source.iloc[0] is result.source_lineage_json.iloc[-1]
    assert set(result.available_at) == {'2026-08-23T14:04:00+08:00'}
    assert set(result.availability_status) == {'MODELED'}


def test_empty_parquet_preserves_original_schema_without_whole_table_load(tmp_path):
    _, args = registered_dataset(tmp_path)
    frame = pd.DataFrame({'symbol': pd.Series(dtype='object'),
        'trade_date': pd.Series(dtype='int64'), 'available_at': pd.Series(dtype='object'),
        'nullable_integer': pd.Series(dtype='Int64'), 'raw_float': pd.Series(dtype='float32'),
        'listed': pd.Series(dtype='bool')})
    path = tmp_path / 'empty_states.parquet'
    frame.to_parquet(path, index=False)
    metadata = {'kind': 'STATES', 'format': 'PARQUET', 'source_id': 'states',
        'start': DATES[0], 'end': DATES[-1], 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    actual = provider(tmp_path, [])._read(tmp_path, path.name, metadata, 'sample', args['authorization'])
    pd.testing.assert_frame_equal(actual, frame, check_exact=True)


@pytest.mark.parametrize('empty', [False, True])
def test_batch_reader_preserves_standard_string_dtypes_and_frozen_object_values(tmp_path, empty):
    count = 0 if empty else 8195
    long_source = 'synthetic_lineage_' + 'x' * 1536
    frame = pd.DataFrame({
        'implicit_string': ['implicit'] * count,
        'nullable_string': pd.Series(['nullable'] * count, dtype='string'),
        'original_source': pd.Series([long_source] * count, dtype='object'),
        'late_source': pd.Series([None] * min(count, 8192) + ['late'] * max(0, count - 8192), dtype='object'),
        'original_list': pd.Series([[1, 2]] * count, dtype='object'),
        'original_dict': pd.Series([{'known': 1, 'missing': None}] * count, dtype='object'),
        'original_decimal': pd.Series([Decimal('1.25')] * count, dtype='object'),
        'optional_integer': pd.Series([None] * count, dtype='Int64'),
    })
    if not empty:
        frame.loc[1, ['original_source', 'original_list', 'original_dict', 'original_decimal']] = None
        frame.loc[8193, 'nullable_string'] = pd.NA
    path = tmp_path / 'typed_strings.parquet'
    frame.to_parquet(path, index=False)
    expected = pd.read_parquet(path)
    standard = UniverseDataProviderV1._read_parquet(path)
    pd.testing.assert_frame_equal(standard, expected, check_exact=True)
    frozen = UniverseDataProviderV1._read_parquet(path, preserve_pandas_objects=True)
    object_columns = [name for name in frame if pd.api.types.is_object_dtype(frame[name].dtype)]
    for name in object_columns:
        pd.testing.assert_series_equal(frozen[name], frame[name], check_exact=True)
    for name in set(frame) - set(object_columns):
        pd.testing.assert_series_equal(frozen[name], expected[name], check_exact=True)
    if not empty:
        assert frozen.original_source.iloc[1] is None
        assert frozen.late_source.iloc[0] is None and frozen.late_source.iloc[-1] == 'late'
        assert frozen.original_source.iloc[0] is frozen.original_source.iloc[-1]
        assert isinstance(frozen.original_list.iloc[-1], list)
        assert frozen.original_dict.iloc[-1] == {'known': 1, 'missing': None}
        assert frozen.original_decimal.iloc[-1] == Decimal('1.25')


def test_python_string_extension_shares_long_values_without_becoming_object(tmp_path):
    count = 8195
    long_source = 'synthetic_python_string_' + 'x' * 1536
    frame = pd.DataFrame({'source': pd.Series([long_source] * count, dtype=pd.StringDtype(storage='python'))})
    frame.loc[8192, 'source'] = pd.NA
    path = tmp_path / 'python_strings.parquet'
    frame.to_parquet(path, index=False)
    # 只在本测试选择已有字符串存储模式，生产读取不修改 Pandas 全局选项。
    with pd.option_context('mode.string_storage', 'python'):
        expected = pd.read_parquet(path)
        actual = UniverseDataProviderV1._read_parquet(path)
    pd.testing.assert_frame_equal(actual, expected, check_exact=True)
    assert isinstance(actual.source.dtype, pd.StringDtype) and actual.source.dtype.storage == 'python'
    assert actual.source.iloc[0] is actual.source.iloc[-1]
    assert actual.source.iloc[8192] is pd.NA


@pytest.mark.parametrize('empty', [False, True])
def test_native_arrow_state_strings_share_across_batches_without_inventing_pandas_metadata(tmp_path, empty):
    _, args = registered_dataset(tmp_path)
    count = 0 if empty else 8195
    long_source = 'synthetic_native_lineage_' + '原始来源证据' * 120
    source_values = [long_source] * count
    if not empty:
        source_values[1] = source_values[8192] = None
    string_names = ['source', 'source_lineage_json', *[f'original_string_{i}' for i in range(17)]]
    arrays = [pa.array(source_values, type=pa.string()), pa.array(source_values, type=pa.large_string())]
    arrays.extend(pa.array(['MODELED'] * count, type=pa.string()) for _ in range(17))
    names = [*string_names, *[f'original_integer_{i}' for i in range(3)],
             *[f'original_bool_{i}' for i in range(6)]]
    arrays.extend(pa.array(np.arange(count), type=pa.int64()) for _ in range(3))
    arrays.extend(pa.array([True] * count, type=pa.bool_()) for _ in range(6))
    table = pa.Table.from_arrays(arrays, names=names)
    assert len(table.schema) == 28 and table.schema.pandas_metadata is None
    path = tmp_path / 'native_states.parquet'
    pq.write_table(table, path)
    assert pq.ParquetFile(path).schema_arrow.pandas_metadata is None
    expected_standard = pd.read_parquet(path)
    standard = UniverseDataProviderV1._read_parquet(path)
    pd.testing.assert_frame_equal(standard, expected_standard, check_exact=True)
    expected = expected_standard.copy()
    for name in string_names:
        expected[name] = pd.Series(table.column(name).to_pylist(), dtype='object')
    metadata = {'kind': 'STATES', 'format': 'PARQUET', 'source_id': 'states',
        'start': DATES[0], 'end': DATES[-1], 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    actual = provider(tmp_path, [])._read(tmp_path, path.name, metadata, 'sample', args['authorization'])
    pd.testing.assert_frame_equal(actual, expected, check_exact=True)
    assert all(pd.api.types.is_object_dtype(actual[name].dtype) for name in string_names)
    if not empty:
        assert actual.source.iloc[1] is None and actual.source.iloc[8192] is None
        assert actual.source.iloc[0] is actual.source.iloc[-1]
        assert actual.source.iloc[0] is actual.source_lineage_json.iloc[-1]
        assert actual.original_string_0.iloc[0] is actual.original_string_16.iloc[-1]


def test_single_state_source_all_target_and_calendar_rows_reach_inputs_without_recopy(tmp_path, monkeypatch):
    from chanlun_trader.research_factory import universe_account_inputs_v1 as inputs_module
    manifest, args = registered_dataset(tmp_path)
    rows = [{'symbol': symbol, 'trade_date': date, 'available_at': '2026-08-23T14:04:00+08:00',
             'source': 'states', 'availability_status': 'MODELED', 'original_note': None}
            for symbol in SYMBOLS for date in DATES]
    path = tmp_path / 'states.parquet'
    pd.DataFrame(rows).to_parquet(path, index=False)
    manifest['files'][path.name] = {'kind': 'STATES', 'format': 'PARQUET', 'source_id': 'states',
        'date_column': 'trade_date', 'start': DATES[0], 'end': DATES[-1],
        'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    service = provider(tmp_path, [])
    raw_states = []
    original_read, original_inputs = service._read, inputs_module.prepare_universe_account_inputs_v1
    def read(root, name, metadata, dataset_id, authorization):
        result = original_read(root, name, metadata, dataset_id, authorization)
        if metadata['kind'] == 'STATES':
            raw_states.append(weakref.ref(result))
        return result
    def prepare_inputs(bundle, window, **kwargs):
        assert bundle['states'] is raw_states[0](), '单来源且全部命中时不应复制整张状态表'
        assert bundle['states'].original_note.isna().all()
        assert set(bundle['states'].available_at) == {'2026-08-23T14:04:00+08:00'}
        return original_inputs(bundle, window, **kwargs)
    monkeypatch.setattr(service, '_read', read)
    monkeypatch.setattr(inputs_module, 'prepare_universe_account_inputs_v1', prepare_inputs)
    result = service.prepare('sample', **args)
    assert result['qualification']['coverage']['target_symbol_count'] == len(SYMBOLS)
    assert not result['qualification']['account_data_ready']
