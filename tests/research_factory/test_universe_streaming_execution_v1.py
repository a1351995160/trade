import pytest

from chanlun_trader.research_factory.universe_execution_artifacts_v1 import DayArtifacts, ArtifactSequence


def test_daily_commit_is_immutable_and_chain_bound(tmp_path):
    store = DayArtifacts(tmp_path, identity='frozen')
    store.commit(20240102, {'account': {'date': 20240102, 'cash': 50000}, 'scan': {'rows': []}})
    store.commit(20240103, {'account': {'date': 20240103, 'cash': 50001}, 'scan': {'rows': []}})
    manifest = store.manifest()
    assert [r['date'] for r in ArtifactSequence(manifest, 'account')] == [20240102, 20240103]
    with pytest.raises(ValueError, match='ALREADY_COMMITTED'):
        store.commit(20240103, {})
    path = tmp_path / manifest['days'][0]['file']
    path.write_bytes(b'tampered')
    with pytest.raises(ValueError, match='CHANGED'):
        ArtifactSequence(manifest, 'account')[0]


def test_uncommitted_tail_is_not_part_of_manifest(tmp_path):
    store = DayArtifacts(tmp_path, identity='frozen')
    (tmp_path / 'partial.tmp').write_text('orphan', encoding='utf-8')
    assert len(ArtifactSequence(store.manifest(), 'account')) == 0


def test_stock_feature_continuation_preserves_recursive_history_and_score_warmup(tmp_path, monkeypatch):
    from chanlun_trader.research_factory.universe_account_backend_v2 import SegmentBoundary
    from chanlun_trader.research_factory.universe_account_inputs_v1 import UniverseAccountInputsV1
    from chanlun_trader.research_factory.universe_signal_scan_v1 import UniverseSignalScanV1
    from chanlun_trader.research_factory.universe_signal_scan_v2 import UniverseSignalScanV2
    from chanlun_trader.research_factory.research_rule_strategy_v4 import ResearchRuleStrategyV4
    from test_research_rule_strategy_v4 import payload
    from universe_test_fixture_v1 import fixture
    window, bundle = fixture(days_count=140, prices=[12., 13., 12., 14., 13.])
    strategy = ResearchRuleStrategyV4(payload(), strategy_id='STOCK_STREAM')
    inputs = UniverseAccountInputsV1(bundle, window, stage='SCAN')
    whole = UniverseSignalScanV1(strategy, inputs)
    monkeypatch.setattr('chanlun_trader.research_factory.universe_signal_scan_v2.time.monotonic', lambda: 1.)
    with pytest.raises(SegmentBoundary) as interrupted:
        UniverseSignalScanV2(strategy, inputs, root=tmp_path, deadline=0.)
    assert interrupted.value.phase == 'FEATURES'
    receipt = tmp_path / (window['symbols'][0] + '.json')
    original = receipt.read_bytes()
    resumed = UniverseSignalScanV2(strategy, inputs, root=tmp_path)
    assert receipt.read_bytes() == original
    assert resumed.preparation == whole.preparation
    for symbol in window['symbols']:
        for day in window['calendar']:
            assert resumed.at(symbol, day) == whole.at(symbol, day)
    assert resumed.at(window['symbols'][0], window['calendar'][60])['condition_ready']
    assert not resumed.at(window['symbols'][0], window['calendar'][60])['score_ready']


def test_completed_feature_cache_refuses_changed_matrix_bytes(tmp_path):
    from chanlun_trader.research_factory.universe_account_inputs_v1 import UniverseAccountInputsV1
    from chanlun_trader.research_factory.universe_signal_scan_v2 import UniverseSignalScanV2
    from chanlun_trader.research_factory.research_rule_strategy_v3 import ResearchRuleStrategyV3
    from universe_test_fixture_v1 import fixture, proposal
    window, bundle = fixture(days_count=70)
    strategy = ResearchRuleStrategyV3(proposal(), strategy_id='STOCK_CACHE')
    inputs = UniverseAccountInputsV1(bundle, window, stage='SCAN')
    scanner = UniverseSignalScanV2(strategy, inputs, root=tmp_path)
    scanner.values._mmap.close()
    path = tmp_path / 'CONDITIONS.npy'
    with path.open('r+b') as stream:
        stream.seek(-1, 2)
        stream.write(b'X')
    with pytest.raises(ValueError, match='CACHE_CHANGED'):
        UniverseSignalScanV2(strategy, inputs, root=tmp_path)
