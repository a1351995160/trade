import json

import pytest

from chanlun_trader.research_factory.universe_account_backend_v2 import UniverseAccountBackendV2, SegmentBoundary
from chanlun_trader.research_factory.universe_account_inputs_v1 import universe_input_identity_v1
from chanlun_trader.research_factory.universe_execution_profile_v1 import execution_profile, SEGMENTED_PROFILE
from chanlun_trader.research_factory.research_rule_strategy_v3 import ResearchRuleStrategyV3
from universe_test_fixture_v1 import fixture, proposal


def run_to(tmp_path, *, stop, bundle, window, rule):
    profile = execution_profile(SEGMENTED_PROFILE, len(window['calendar']) - 60)
    backend = UniverseAccountBackendV2(window, execution_profile=profile, checkpoint_path=str(tmp_path / 'CLOSE.json'),
        max_positions=2, max_symbol_exposure_bps=5000)
    guard = lambda: {'input_identity': universe_input_identity_v1(bundle, window)}
    with pytest.raises(SegmentBoundary):
        backend.run(rule, bundle, bundle['events'], guard, stop_after_date=stop)
    return json.loads((tmp_path / 'CLOSE.json').read_text(encoding='utf-8'))


def test_close_restore_is_exact_without_replaying_committed_days(tmp_path, monkeypatch):
    window, bundle = fixture(days_count=70, prices=[12., 12.5, 13., 13.5, 13.4, 13.2, 13.1, 12.8])
    strategy = ResearchRuleStrategyV3(proposal({'trailing_activate_pct': .04, 'trailing_pct': .03}), strategy_id='state')
    continuous = run_to(tmp_path / 'continuous', stop=window['calendar'][68], bundle=bundle, window=window, rule=strategy)
    run_to(tmp_path / 'segmented', stop=window['calendar'][63], bundle=bundle, window=window, rule=strategy)
    from chanlun_trader.research_factory.universe_execution_artifacts_v1 import ArtifactSequence
    with monkeypatch.context() as patch:
        patch.setattr(ArtifactSequence, 'read_day', lambda *args: pytest.fail('恢复前缀不应解压已提交日'))
        resumed = run_to(tmp_path / 'segmented', stop=window['calendar'][68], bundle=bundle, window=window, rule=strategy)
    for field in ('ledger', 'engine', 'orders', 'order_counters', 'broker_fill_counter', 'events', 'event_counter',
                  'rule_states', 'rule_exits', 'bars', 'states', 'skips', 'allocations', 'decisions', 'peak', 'drawdown'):
        assert continuous[field] == resumed[field], field
    assert len(resumed['artifacts']['days']) == 9
    assert resumed['broker_fill_counter'] >= 4  # 恢复前买入、恢复后卖出均实际成交。


def test_corrupt_close_state_refuses_resume(tmp_path):
    window, bundle = fixture(days_count=70)
    strategy = ResearchRuleStrategyV3(proposal(), strategy_id='state')
    value = run_to(tmp_path, stop=window['calendar'][62], bundle=bundle, window=window, rule=strategy)
    value['ledger']['state']['cash'] += 1
    (tmp_path / 'CLOSE.json').write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises(ValueError, match='STATE_OR_EXECUTION_CONFLICT'):
        run_to(tmp_path, stop=window['calendar'][64], bundle=bundle, window=window, rule=strategy)


@pytest.mark.parametrize('change', ['append', 'missing'])
def test_resume_still_checks_all_real_committed_bytes(tmp_path, change):
    from pathlib import Path
    window, bundle = fixture(days_count=70)
    strategy = ResearchRuleStrategyV3(proposal(), strategy_id='state')
    value = run_to(tmp_path, stop=window['calendar'][62], bundle=bundle, window=window, rule=strategy)
    artifact = Path(value['artifacts']['root']) / value['artifacts']['days'][0]['file']
    if change == 'append':
        with artifact.open('ab') as stream:
            stream.write(b'changed')
        expected = ValueError
    else:
        artifact.unlink()
        expected = FileNotFoundError
    with pytest.raises(expected):
        run_to(tmp_path, stop=window['calendar'][64], bundle=bundle, window=window, rule=strategy)
    assert json.loads((tmp_path / 'CLOSE.json').read_text(encoding='utf-8')) == value
