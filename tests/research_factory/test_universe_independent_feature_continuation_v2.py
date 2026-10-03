"""独立公式在证券边界提交，不能把执行器特征缓存当作答案。"""
import json

import numpy as np
import pytest

from chanlun_trader.research_factory.research_rule_strategy_v4 import ResearchRuleStrategyV4
from chanlun_trader.research_factory.universe_account_backend_v2 import SegmentBoundary
from chanlun_trader.research_factory.universe_account_inputs_v1 import UniverseAccountInputsV1
from chanlun_trader.research_factory import universe_evidence_v2 as evidence
from test_research_rule_strategy_v4 import node, payload
from universe_test_fixture_v1 import fixture


def features_case():
    window, bundle = fixture(days_count=140, prices=[12., 13., 12., 14., 13.])
    rule = payload()
    rule['indicator_instances'].append({'instance_id': 'macd', 'id': 'MACD', 'version': 'MACD_V1',
                                       'params': {'fast': 12, 'slow': 26, 'signal': 9}})
    rule['buy'] = node('gt', node('indicator', 'macd', output='dif', version='MACD_V1'),
                       node('indicator', 'macd', output='dea', version='MACD_V1'))
    strategy = ResearchRuleStrategyV4(rule, strategy_id='OWN_FEATURE_CONTINUATION')
    return strategy, UniverseAccountInputsV1(bundle, window, stage='SCAN')


def test_slow_independent_features_commit_one_stock_then_resume_without_recompute(tmp_path, monkeypatch):
    strategy, inputs = features_case()
    whole = evidence._ConditionTable(strategy, inputs, tmp_path / 'whole', 'own-source')
    expected_values = whole.values.copy()
    expected_truth = {(symbol, day): whole.truth(symbol, day) for symbol in inputs.symbols for day in inputs.calendar}
    whole.values._mmap.close()
    original = strategy.build_feature_matrix
    calls, clock = [], [0.]

    def slow_formula(*args, **kwargs):
        calls.append(len(args[0]))
        result = original(*args, **kwargs)
        clock[0] += 2.
        return result

    monkeypatch.setattr(strategy, 'build_feature_matrix', slow_formula)
    monkeypatch.setattr(evidence.time, 'monotonic', lambda: clock[0])
    root = tmp_path / 'segmented'
    with pytest.raises(SegmentBoundary) as boundary:
        evidence._ConditionTable(strategy, inputs, root, 'own-source', deadline=1.)
    assert boundary.value.phase == 'AUDIT_FEATURES'
    assert len(calls) == 1
    assert not (root / 'OWN_FEATURES.json').exists()
    first = (root / 'OWN_FEATURE_000000.json').read_bytes()
    resumed = evidence._ConditionTable(strategy, inputs, root, 'own-source')
    try:
        assert len(calls) == len(inputs.symbols)
        assert (root / 'OWN_FEATURE_000000.json').read_bytes() == first
        assert resumed.preparation == whole.preparation
        np.testing.assert_array_equal(resumed.values, expected_values)
        for symbol in inputs.symbols:
            for day in inputs.calendar:
                assert resumed.truth(symbol, day) == expected_truth[symbol, day]
    finally:
        resumed.values._mmap.close()


def partial_features(root, monkeypatch, *, budget=1.):
    strategy, inputs = features_case()
    original = strategy.build_feature_matrix
    clock = [0.]
    with monkeypatch.context() as patch:
        def slow_formula(*args, **kwargs):
            result = original(*args, **kwargs)
            clock[0] += 2.
            return result
        patch.setattr(strategy, 'build_feature_matrix', slow_formula)
        patch.setattr(evidence.time, 'monotonic', lambda: clock[0])
        with pytest.raises(SegmentBoundary, match='OWN_FEATURE_NEXT_SEGMENT'):
            evidence._ConditionTable(strategy, inputs, root, 'own-source', deadline=budget)
    return strategy, inputs


def test_zero_remaining_time_commits_no_stock_and_never_advertises_complete(tmp_path, monkeypatch):
    strategy, inputs = features_case()
    monkeypatch.setattr(strategy, 'build_feature_matrix', lambda *args: pytest.fail('FORMULA_STARTED_AFTER_CUTOFF'))
    monkeypatch.setattr(evidence.time, 'monotonic', lambda: 10.)
    with pytest.raises(SegmentBoundary) as boundary:
        evidence._ConditionTable(strategy, inputs, tmp_path, 'own-source', deadline=10.)
    assert boundary.value.phase == 'AUDIT_FEATURES'
    assert not (tmp_path / 'OWN_FEATURES.json').exists()
    assert not (tmp_path / 'OWN_FEATURE_000000.json').exists()
    assert (tmp_path / 'OWN_FEATURE_BINDING.json').is_file()
    values = np.load(tmp_path / 'OWN_FEATURES.npy', mmap_mode='r')
    try:
        assert np.isnan(values).all()
    finally:
        values._mmap.close()


@pytest.mark.parametrize('change', ['slice', 'receipt-hash', 'receipt-symbol', 'missing-binding', 'shape', 'dtype', 'columns'])
def test_partial_own_cache_rejects_changed_committed_evidence(tmp_path, monkeypatch, change):
    strategy, inputs = partial_features(tmp_path, monkeypatch)
    array_path = tmp_path / 'OWN_FEATURES.npy'
    receipt_path = tmp_path / 'OWN_FEATURE_000000.json'
    binding_path = tmp_path / 'OWN_FEATURE_BINDING.json'
    if change == 'slice':
        values = np.load(array_path, mmap_mode='r+')
        values[0, 0, 0] = 999.
        values.flush()
        values._mmap.close()
        error = 'OWN_FEATURE_SLICE_CHANGED'
    elif change.startswith('receipt'):
        receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
        receipt['symbol'] = inputs.symbols[1]
        if change == 'receipt-hash':
            receipt_path.write_text(json.dumps(receipt), encoding='utf-8')
            error = 'OWN_STATE_IDENTITY_CONFLICT'
        else:
            evidence._write(receipt_path, {key: value for key, value in receipt.items() if key != 'receipt_hash'})
            error = 'OWN_FEATURE_RECEIPT_SCOPE_CONFLICT'
    elif change == 'missing-binding':
        binding_path.unlink()
        error = 'OWN_FEATURE_INCOMPLETE_BINDING_CONFLICT'
    elif change in ('shape', 'dtype'):
        np.save(array_path, np.zeros((len(inputs.symbols), len(inputs.calendar), 6 if change == 'shape' else 7),
                                    dtype='float32' if change == 'dtype' else 'float64'))
        error = 'OWN_FEATURE_ARRAY_SCOPE_CONFLICT'
    else:
        binding = evidence._read(binding_path, 'own-source')
        binding['columns'].reverse()
        evidence._write(binding_path, binding)
        error = 'OWN_FEATURE_BINDING_CONFLICT'
    with pytest.raises(ValueError, match=error):
        evidence._ConditionTable(strategy, inputs, tmp_path, 'own-source')


@pytest.mark.parametrize('change', ['source', 'symbols', 'calendar'])
def test_partial_own_cache_cannot_rebind_source_or_ordered_scope(tmp_path, monkeypatch, change):
    strategy, inputs = partial_features(tmp_path, monkeypatch)
    identity = 'other-source' if change == 'source' else 'own-source'
    if change == 'symbols':
        inputs.symbols = tuple(reversed(inputs.symbols))
    elif change == 'calendar':
        inputs.calendar = tuple(reversed(inputs.calendar))
    with pytest.raises(ValueError, match='OWN_STATE_IDENTITY_CONFLICT' if change == 'source' else 'OWN_FEATURE_BINDING_CONFLICT'):
        evidence._ConditionTable(strategy, inputs, tmp_path, identity)


def test_missing_prefix_receipt_cannot_turn_later_stock_into_a_committed_prefix(tmp_path, monkeypatch):
    strategy, inputs = partial_features(tmp_path, monkeypatch, budget=3.)
    (tmp_path / 'OWN_FEATURE_000000.json').unlink()
    assert (tmp_path / 'OWN_FEATURE_000001.json').exists()
    with pytest.raises(ValueError, match='OWN_FEATURE_RECEIPT_SCOPE_CONFLICT'):
        evidence._ConditionTable(strategy, inputs, tmp_path, 'own-source')


def test_uncommitted_tail_is_recomputed_from_raw_history(tmp_path, monkeypatch):
    strategy, inputs = partial_features(tmp_path / 'partial', monkeypatch)
    path = tmp_path / 'partial' / 'OWN_FEATURES.npy'
    values = np.load(path, mmap_mode='r+')
    values[1:, :, :] = 999.
    values.flush()
    values._mmap.close()
    continued = evidence._ConditionTable(strategy, inputs, tmp_path / 'partial', 'own-source')
    whole = evidence._ConditionTable(strategy, inputs, tmp_path / 'whole', 'own-source')
    try:
        assert continued.preparation == whole.preparation
        np.testing.assert_array_equal(continued.values, whole.values)
    finally:
        continued.values._mmap.close()
        whole.values._mmap.close()


@pytest.mark.parametrize('change', ['array', 'receipt', 'missing-receipt', 'cache-version', 'downgrade'])
def test_completed_own_cache_still_checks_full_bytes_and_each_committed_receipt(tmp_path, change):
    strategy, inputs = features_case()
    whole = evidence._ConditionTable(strategy, inputs, tmp_path, 'own-source')
    whole.values._mmap.close()
    if change == 'array':
        values = np.load(tmp_path / 'OWN_FEATURES.npy', mmap_mode='r+')
        values[-1, -1, 0] = 999.
        values.flush()
        values._mmap.close()
        error = 'OWN_FEATURE_CACHE_CHANGED'
    elif change == 'receipt':
        path = tmp_path / 'OWN_FEATURE_000000.json'
        receipt = evidence._read(path, 'own-source')
        receipt['symbol'] = inputs.symbols[1]
        evidence._write(path, receipt)
        error = 'OWN_FEATURE_RECEIPT_SCOPE_CONFLICT'
    elif change == 'missing-receipt':
        (tmp_path / 'OWN_FEATURE_000002.json').unlink()
        error = 'OWN_FEATURE_PREPARATION_SCOPE_CONFLICT'
    elif change == 'cache-version':
        path = tmp_path / 'OWN_FEATURES.json'
        manifest = evidence._read(path, 'own-source')
        manifest['cache_format'] = 'UNKNOWN'
        evidence._write(path, manifest)
        error = 'OWN_FEATURE_CACHE_VERSION_CONFLICT'
    else:
        path = tmp_path / 'OWN_FEATURES.json'
        manifest = evidence._read(path, 'own-source')
        manifest.pop('cache_format')
        evidence._write(path, manifest)
        error = 'OWN_FEATURE_CACHE_VERSION_CONFLICT'
    with pytest.raises(ValueError, match=error):
        evidence._ConditionTable(strategy, inputs, tmp_path, 'own-source')


def test_legacy_complete_own_cache_remains_byte_checked_and_compatible(tmp_path, monkeypatch):
    strategy, inputs = features_case()
    whole = evidence._ConditionTable(strategy, inputs, tmp_path, 'own-source')
    expected = whole.preparation
    whole.values._mmap.close()
    manifest_path = tmp_path / 'OWN_FEATURES.json'
    manifest = evidence._read(manifest_path, 'own-source')
    manifest.pop('cache_format')
    evidence._write(manifest_path, manifest)
    # 原旧版完成缓存没有分区协议原件，不能仅删除版本字段冒充旧缓存。
    for path in tmp_path.glob('OWN_FEATURE_*.json'):
        path.unlink()
    monkeypatch.setattr(strategy, 'build_feature_matrix', lambda *args: pytest.fail('COMPLETED_OWN_CACHE_RECOMPUTED'))
    continued = evidence._ConditionTable(strategy, inputs, tmp_path, 'own-source', deadline=0.)
    try:
        assert continued.preparation == expected
    finally:
        continued.values._mmap.close()


def test_audit_feature_deadline_includes_its_strict_input_loading(tmp_path, monkeypatch):
    from test_universe_evidence_v2 import audit, case
    from chanlun_trader.research_factory import universe_account_inputs_v1 as inputs_module
    data = case(tmp_path / 'job', scored=True)
    original = inputs_module._prepare_owned_universe_account_inputs_v1
    clock = [0.]

    def slow_loader(*args, **kwargs):
        result = original(*args, **kwargs)
        clock[0] = 2.
        return result

    monkeypatch.setattr(inputs_module, '_prepare_owned_universe_account_inputs_v1', slow_loader)
    monkeypatch.setattr(evidence.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(ResearchRuleStrategyV4, 'build_feature_matrix',
                        lambda *args: pytest.fail('FEATURE_STARTED_AFTER_LOADER_EXHAUSTED_CUTOFF'))
    checkpoint = tmp_path / 'own' / 'AUDIT.json'
    with pytest.raises(SegmentBoundary) as boundary:
        audit(data, audit_checkpoint_path=checkpoint, segment_seconds=1.)
    assert boundary.value.phase == 'AUDIT_FEATURES'
    assert not checkpoint.exists()


def test_repeated_independent_feature_segments_keep_formula_and_account_truth(tmp_path, monkeypatch):
    from test_universe_evidence_v2 import audit, case
    data = case(tmp_path / 'job', scored=True)
    expected = audit(data)
    original = ResearchRuleStrategyV4.build_feature_matrix
    calls, clock = [], [0.]

    def slow_formula(self, *args, **kwargs):
        calls.append(len(args[0]))
        result = original(self, *args, **kwargs)
        clock[0] += 2.
        return result

    monkeypatch.setattr(ResearchRuleStrategyV4, 'build_feature_matrix', slow_formula)
    monkeypatch.setattr(evidence.time, 'monotonic', lambda: clock[0])
    checkpoint = tmp_path / 'own' / 'AUDIT.json'
    for count in (1, 2):
        clock[0] = 0.
        with pytest.raises(SegmentBoundary) as boundary:
            audit(data, audit_checkpoint_path=checkpoint, segment_seconds=1.)
        assert boundary.value.phase == 'AUDIT_FEATURES'
        assert len(calls) == count
        assert not checkpoint.exists()
    clock[0] = 0.
    with pytest.raises(SegmentBoundary) as boundary:
        audit(data, audit_checkpoint_path=checkpoint, segment_seconds=1.)
    assert boundary.value.phase == 'AUDIT'
    assert len(calls) == len(data[1]['symbols'])
    assert json.loads(checkpoint.read_text(encoding='utf-8'))['last_index'] == -1
    assert audit(data, audit_checkpoint_path=checkpoint) == expected
    assert len(calls) == len(data[1]['symbols'])


def test_formula_failure_is_not_swallowed_and_closes_its_writable_mapping(tmp_path, monkeypatch):
    strategy, inputs = features_case()
    original = np.load
    mappings = []

    def mapped_array(*args, **kwargs):
        result = original(*args, **kwargs)
        mappings.append(result)
        return result

    def rejected_formula(*args, **kwargs):
        raise ValueError('FORMULA_SOURCE_INVALID')

    monkeypatch.setattr(np, 'load', mapped_array)
    monkeypatch.setattr(strategy, 'build_feature_matrix', rejected_formula)
    with pytest.raises(ValueError, match='FORMULA_SOURCE_INVALID'):
        evidence._ConditionTable(strategy, inputs, tmp_path, 'own-source')
    assert mappings and all(array._mmap.closed for array in mappings)
    assert not (tmp_path / 'OWN_FEATURES.json').exists()
    assert not (tmp_path / 'OWN_FEATURE_000000.json').exists()


def test_independent_features_do_not_call_execution_scanner_during_initial_or_resume(tmp_path, monkeypatch):
    strategy, inputs = partial_features(tmp_path, monkeypatch)
    from chanlun_trader.research_factory.universe_signal_scan_v1 import UniverseSignalScanV1
    from chanlun_trader.research_factory.universe_signal_scan_v2 import UniverseSignalScanV2

    def forbidden(*args, **kwargs):
        pytest.fail('EXECUTION_FEATURES_USED_AS_INDEPENDENT_TRUTH')

    monkeypatch.setattr(UniverseSignalScanV1, '__init__', forbidden)
    monkeypatch.setattr(UniverseSignalScanV2, '__init__', forbidden)
    monkeypatch.setattr(UniverseSignalScanV2, 'at', forbidden)
    continued = evidence._ConditionTable(strategy, inputs, tmp_path, 'own-source')
    try:
        assert len(continued.preparation) == len(inputs.symbols)
    finally:
        continued.values._mmap.close()
