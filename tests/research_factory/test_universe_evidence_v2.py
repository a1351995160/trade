"""新版真实执行路径的合成核账、独立性与收盘续核验反例。"""
from copy import deepcopy
import json

import pandas as pd
import pytest

from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.formal_account_backend_v1 import BASE_COSTS
from chanlun_trader.research_factory.research_rule_strategy_v3 import ResearchRuleStrategyV3
from chanlun_trader.research_factory.research_rule_strategy_v4 import ResearchRuleStrategyV4
from chanlun_trader.research_factory.universe_account_backend_v2 import UniverseAccountBackendV2, SegmentBoundary
from chanlun_trader.research_factory.universe_account_inputs_v1 import universe_input_identity_v1
from chanlun_trader.research_factory.universe_evidence_v1 import reconstruct_universe_account
from chanlun_trader.research_factory.universe_execution_artifacts_v1 import ArtifactSequence, DayArtifacts, hydrated_result
from chanlun_trader.research_factory.universe_execution_profile_v1 import execution_profile, SEGMENTED_PROFILE
from universe_test_fixture_v1 import fixture, proposal


def case(root, *, scored=False, symbols=None, days_count=67):
    window, bundle = fixture(symbols=symbols, days_count=days_count)
    value = proposal()
    value['max_hold_sessions'] = 2
    if scored:
        value['version'] = 'RESEARCH_RULE_STRATEGY_V4'
        value['selection'] = {'score': {'op': 'field', 'args': ['close'], 'params': {}},
                              'direction': 'DESCENDING', 'tie_breaker': 'SYMBOL_ASCENDING'}
    strategy = (ResearchRuleStrategyV4 if scored else ResearchRuleStrategyV3)(value, strategy_id='auditv2')
    identity = universe_input_identity_v1(bundle, window)
    profile = execution_profile(SEGMENTED_PROFILE, days_count - 60)
    root.mkdir(parents=True, exist_ok=True)
    backend = UniverseAccountBackendV2(window, execution_profile=profile, initial_cash=50000,
        max_positions=min(2, len(window['symbols'])), max_symbol_exposure_bps=5000,
        checkpoint_path=root / 'EXECUTION.json')
    result = backend.run(strategy, bundle, bundle['events'], lambda: {'input_identity': identity})
    return bundle, window, value, result


def audit(data, **options):
    bundle, window, rule, result = data
    return reconstruct_universe_account(bundle, window, hydrated_result(result), initial_cash=50000,
        costs=BASE_COSTS, strategy_id='auditv2', rule=rule, **options)


@pytest.mark.parametrize('scored', [False, True], ids=['V3', 'V4'])
def test_sharded_public_backend_has_independent_daily_evidence(tmp_path, scored):
    data = case(tmp_path / 'job', scored=scored)
    result = data[-1]
    rebuilt = audit(data)
    assert result['reconciliation']['passed'] and rebuilt['version'] == 'UNIVERSE_EVIDENCE_V2'
    assert rebuilt['daily_accounts'] == list(hydrated_result(result)['daily_accounts'])
    assert rebuilt['method_scopes']['allocations'].startswith('INDEPENDENT_')
    assert rebuilt['strategy_qualified'] is False


def test_new_auditor_does_not_read_scanner_or_engine_answers(tmp_path, monkeypatch):
    data = case(tmp_path / 'job', scored=True)
    from chanlun_trader.engine.ledger import PortfolioLedger
    from chanlun_trader.research_factory.universe_signal_scan_v1 import UniverseSignalScanV1
    from chanlun_trader.research_factory.universe_signal_scan_v2 import UniverseSignalScanV2
    def forbidden(*args, **kwargs):
        raise AssertionError('EXECUTION_USED_AS_AUDIT_ORACLE')
    monkeypatch.setattr(PortfolioLedger, 'apply_fill', forbidden)
    monkeypatch.setattr(UniverseSignalScanV1, '__init__', forbidden)
    monkeypatch.setattr(UniverseSignalScanV2, 'at', forbidden)
    assert audit(data)['metrics'] == data[-1]['metrics']


def test_audit_checkpoint_is_own_state_and_resume_equals_continuous(tmp_path, monkeypatch):
    data = case(tmp_path / 'job', scored=True)
    from chanlun_trader.research_factory import universe_evidence_v2 as module
    original = module._ReconstructionV2.decisions
    def interrupt_clock(self, day, frames):
        result = original(self, day, frames)
        monkeypatch.setattr(module.time, 'monotonic', lambda: 100.)
        return result
    path = tmp_path / 'own' / 'AUDIT.json'
    monkeypatch.setattr(module.time, 'monotonic', lambda: 0.)
    monkeypatch.setattr(module._ReconstructionV2, 'decisions', interrupt_clock)
    with pytest.raises(SegmentBoundary) as interrupted:
        audit(data, audit_checkpoint_path=path, segment_seconds=1.)
    assert interrupted.value.phase == 'AUDIT'
    receipt = json.loads(path.read_text(encoding='utf-8'))
    assert receipt['origin'] == 'INDEPENDENT_RECONSTRUCTION_ONLY' and receipt['last_index'] == 0
    assert receipt['state']['cash'][0] == 'VALUE'
    monkeypatch.setattr(module._ReconstructionV2, 'decisions', original)
    resumed = audit(data, audit_checkpoint_path=path)
    assert resumed == audit(data)
    assert audit(data, audit_checkpoint_path=path) == resumed


def mutate_artifact(data, root, field, change):
    result = deepcopy(data[-1])
    store = DayArtifacts(root, identity=result['artifacts']['identity'])
    fields = ['account', 'scan', 'decision', 'allocation_records', 'skipped_intents']
    changed = False
    for index, row in enumerate(result['artifacts']['days']):
        packet = {name: ArtifactSequence(result['artifacts'], name)[index] for name in fields}
        if not changed and change(packet[field]):
            changed = True
        store.commit(row['date'], packet)
    assert changed
    result['artifacts'] = store.manifest()
    return (*data[:-1], result)


def test_forged_slot_reason_is_rejected_by_own_limits_not_self_report(tmp_path):
    data = case(tmp_path / 'job')
    def change(rows):
        if rows:
            rows[0]['primary_reason'] = 'POSITION_LIMIT'
            return True
        return False
    forged = mutate_artifact(data, tmp_path / 'forged', 'allocation_records', change)
    with pytest.raises(ValueError, match='ALLOCATION_BASIS_OR_REASON_CONFLICT'):
        audit(forged)


def test_original_quantity_and_score_metadata_are_independently_bound(tmp_path):
    data = case(tmp_path / 'job', scored=True)
    forged = deepcopy(data[-1])
    order = next(iter(forged['final_account_checkpoint']['economic']['orders'].values()))
    order['metadata']['allocated_quantity'] += 100
    with pytest.raises(ValueError, match='ORIGINAL_QUANTITY_OR_SELECTION_CONFLICT'):
        audit((*data[:-1], forged))


def rerun_v2(root, window, bundle, rule, *, stop=None):
    strategy = (ResearchRuleStrategyV4 if rule['version'] == 'RESEARCH_RULE_STRATEGY_V4'
                else ResearchRuleStrategyV3)(rule, strategy_id='auditv2')
    first = window['calendar'].index(window['account_start'])
    profile = execution_profile(SEGMENTED_PROFILE, len(window['calendar']) - first)
    root.mkdir(parents=True, exist_ok=True)
    backend = UniverseAccountBackendV2(window, execution_profile=profile,
        checkpoint_path=root / 'EXECUTION.json', max_positions=min(2, len(window['symbols'])))
    return backend.run(strategy, bundle, bundle['events'],
        lambda: {'input_identity': universe_input_identity_v1(bundle, window)}, stop_after_date=stop)


@pytest.mark.parametrize('taxable', [False, True], ids=['capitalization', 'bonus-tax'])
def test_share_cash_rights_and_locked_lots_match_after_execution_and_audit_resume(tmp_path, monkeypatch, taxable):
    from test_universe_evidence_v1 import share_case
    bundle, window, rule, _ = share_case(taxable=taxable, cash=True, max_hold_sessions=2,
        exits={'trailing_activate_pct': .01, 'trailing_pct': .03, 'stop_loss_pct': .03})
    continuous = rerun_v2(tmp_path / 'continuous', window, bundle, rule)
    with pytest.raises(SegmentBoundary):
        rerun_v2(tmp_path / 'segmented', window, bundle, rule, stop=window['calendar'][63])
    resumed = rerun_v2(tmp_path / 'segmented', window, bundle, rule)
    for field in ('economic', 'equity', 'rule_states', 'rule_exit_states'):
        assert resumed['final_account_checkpoint'][field] == continuous['final_account_checkpoint'][field]
    data = bundle, window, rule, resumed
    from chanlun_trader.research_factory import universe_evidence_v2 as module
    original = module._ReconstructionV2.decisions
    def interrupt_after_uncredited_share(self, day, frames):
        result = original(self, day, frames)
        if day == window['calendar'][63]:
            monkeypatch.setattr(module.time, 'monotonic', lambda: 100.)
        return result
    monkeypatch.setattr(module.time, 'monotonic', lambda: 0.)
    monkeypatch.setattr(module._ReconstructionV2, 'decisions', interrupt_after_uncredited_share)
    path = tmp_path / 'own' / 'AUDIT.json'
    with pytest.raises(SegmentBoundary):
        audit(data, audit_checkpoint_path=path, segment_seconds=1.)
    receipt = json.loads(path.read_text(encoding='utf-8'))
    assert receipt['last_index'] == 3
    monkeypatch.setattr(module._ReconstructionV2, 'decisions', original)
    assert audit(data, audit_checkpoint_path=path) == audit(data)


def test_partial_sell_and_t_plus_one_keep_original_order_realization(tmp_path):
    window, bundle = fixture(symbols=['000001.SZ'], days_count=67)
    bundle['daily'].loc[bundle['daily'].date.eq(window['calendar'][63]), 'volume'] = 1680.
    rule = proposal()
    rule['max_hold_sessions'] = 2
    result = rerun_v2(tmp_path / 'job', window, bundle, rule)
    assert [trade['side'] for trade in result['fills']] == ['BUY', 'SELL', 'SELL']
    sold = [trade for trade in result['fills'] if trade['side'] == 'SELL']
    assert sold[0]['quantity'] == 168
    assert sold[0]['quantity'] + sold[1]['quantity'] == result['fills'][0]['quantity']
    assert audit((bundle, window, rule, result))['metrics'] == result['metrics']


def test_limit_down_broker_reason_cannot_be_relabelled_as_cash(tmp_path):
    window, bundle = fixture(symbols=['000001.SZ'], days_count=65, prices=[12., 12., 11.2, 10.08, 10.4])
    rule = proposal({'stop_loss_pct': .05})
    result = rerun_v2(tmp_path / 'job', window, bundle, rule)
    assert audit((bundle, window, rule, result))['metrics'] == result['metrics']
    rejected = next(order for order in result['final_account_checkpoint']['economic']['orders'].values()
                    if order['status'] == 'REJECTED')
    assert rejected['metadata']['broker_rejection_reason'] == 'LIMIT_DOWN_OPEN_DAILY_CONSERVATIVE'
    forged = deepcopy(result)
    order = forged['final_account_checkpoint']['economic']['orders'][rejected['order_id']]
    order['metadata']['broker_rejection_reason'] = 'INSUFFICIENT_CASH'
    order['reason_code'] = 'INSUFFICIENT_CASH'
    with pytest.raises(ValueError, match='BROKER_REJECTION_REASON_CONFLICT'):
        audit((bundle, window, rule, forged))


def test_known_suspension_and_resume_liquidity_are_independently_retained(tmp_path):
    window, bundle = fixture(symbols=['000001.SZ'], days_count=66)
    days = window['calendar']
    original = bundle['states'].iloc[0].to_dict()
    bundle['states'] = pd.DataFrame([{**original, 'valid_to': days[61]},
        {**original, 'effective_date': days[62], 'valid_to': days[62], 'suspension_status': 'SUSPENDED'},
        {**original, 'effective_date': days[63]}])
    bundle['daily'] = bundle['daily'].loc[~bundle['daily'].date.eq(days[62])].reset_index(drop=True)
    bundle['turn'] = bundle['turn'].loc[~bundle['turn'].date.eq(days[62])].reset_index(drop=True)
    rule = proposal({'trailing_activate_pct': .05, 'trailing_pct': .03})
    rule['max_hold_sessions'] = 1
    result = rerun_v2(tmp_path / 'job', window, bundle, rule)
    rebuilt = audit((bundle, window, rule, result))
    halt = next(row for row in rebuilt['daily_accounts'] if row['date'] == days[62])
    assert halt['stale_valuations'] == [{'symbol': '000001.SZ', 'status': 'STALE_VERIFIED_SUSPENSION'}]
    assert result['reconciliation']['passed']


def test_score_orders_higher_symbol_first_and_tampered_rank_is_rejected(tmp_path):
    window, bundle = fixture(days_count=64)
    for index, symbol in enumerate(window['symbols']):
        mask = bundle['daily'].symbol.eq(symbol)
        bundle['daily'].loc[mask, ['open', 'high', 'low', 'close', 'prev_close']] *= 1 + index * .01
        bundle['daily'].loc[mask, 'amount'] *= 1 + index * .01
    rule = proposal()
    rule.update(version='RESEARCH_RULE_STRATEGY_V4', selection={
        'score': {'op': 'field', 'args': ['close'], 'params': {}},
        'direction': 'DESCENDING', 'tie_breaker': 'SYMBOL_ASCENDING'})
    result = rerun_v2(tmp_path / 'job', window, bundle, rule)
    first_plan = list(hydrated_result(result)['decisions'])[1]['plan']
    assert [intent['symbol'] for intent in first_plan['intents']] == list(reversed(window['symbols']))
    assert [intent['metadata']['selection']['rank'] for intent in first_plan['intents']] == [1, 2, 3]
    data = bundle, window, rule, result
    def change(entry):
        if entry['decisions']:
            target = next(item for item in entry['decisions'] if item.get('metadata'))
            target['metadata']['selection']['rank'] += 1
            return True
        return False
    forged = mutate_artifact(data, tmp_path / 'forged_rank', 'decision', change)
    with pytest.raises(ValueError, match='CONSUMED_DECISIONS_CONFLICT'):
        audit(forged)


def test_funnel_public_day_packets_and_economic_denominators_close(tmp_path):
    from chanlun_trader.research_factory.universe_signal_funnel_v1 import (
        funnel_day_packets_v1, build_signal_funnel_stream_v1,
    )
    data = case(tmp_path / 'job', scored=True)
    bundle, window, rule, result = data
    identity = ResearchRuleStrategyV4(rule, strategy_id='auditv2').rule_identity
    written = []
    summary = build_signal_funnel_stream_v1(rule_identity=identity, strategy_id='auditv2',
        calendar=window['calendar'], day_packets=funnel_day_packets_v1(result, window['calendar']),
        event_sink=written.append, expected_decision_sessions=window['calendar'][60:])
    assert summary['counts']['scan_rows'] == 7 * 3
    assert summary['counts']['orders'] == len(result['final_account_checkpoint']['economic']['orders'])
    assert summary['counts']['fills'] == len(result['fills'])
    assert summary['counts']['realized_signals'] == len([trade for trade in result['fills'] if trade['side'] == 'BUY'])
    assert summary['counts']['tail_signals'] == 3
    assert len(written) == 8 and 'signals' not in summary


def test_complete_own_receipt_still_checks_artifact_bytes(tmp_path):
    data = case(tmp_path / 'job')
    path = tmp_path / 'audit' / 'AUDIT.json'
    audit(data, audit_checkpoint_path=path)
    artifact = data[-1]['artifacts']['days'][0]
    target = __import__('pathlib').Path(data[-1]['artifacts']['root']) / artifact['file']
    target.write_bytes(target.read_bytes() + b'changed')
    with pytest.raises(ValueError, match='ARTIFACT_CHANGED'):
        audit(data, audit_checkpoint_path=path)


def test_score_only_long_warmup_keeps_opportunities_and_names_missing_score(tmp_path):
    from test_research_rule_strategy_v4 import node
    from chanlun_trader.research_factory.universe_signal_funnel_v1 import (
        funnel_day_packets_v1, build_signal_funnel_stream_v1,
    )
    window, bundle = fixture(days_count=67)
    rule = proposal()
    rule.update(version='RESEARCH_RULE_STRATEGY_V4', indicator_instances=[
        {'instance_id': 'ma10', 'id': 'MA', 'version': 'MA_ARITHMETIC_V1', 'params': {'window': 10}},
        {'instance_id': 'ma120', 'id': 'MA', 'version': 'MA_ARITHMETIC_V1', 'params': {'window': 120}}],
        buy=node('ge', node('field', 'close'), node('indicator', 'ma10', output='ma', version='MA_ARITHMETIC_V1')),
        selection={'score': node('indicator', 'ma120', output='ma', version='MA_ARITHMETIC_V1'),
            'direction': 'DESCENDING', 'tie_breaker': 'SYMBOL_ASCENDING'})
    strategy = ResearchRuleStrategyV4(rule, strategy_id='auditv2')
    backend = UniverseAccountBackendV2(window, execution_profile=execution_profile(SEGMENTED_PROFILE, 7),
        initial_cash=50000, max_positions=2, max_symbol_exposure_bps=5000, checkpoint_path=tmp_path / 'EXECUTION.json')
    result = backend.run(strategy, bundle, [], lambda: {'input_identity': universe_input_identity_v1(bundle, window)})
    assert audit((bundle, window, rule, result))['metrics'] == result['metrics']
    scans = list(hydrated_result(result)['scan_days'])
    assert all(row['reason'] == 'SCORE_UNKNOWN' and row['conditions']['condition_ready']
               for scan in scans for row in scan['rows'])
    funnel = build_signal_funnel_stream_v1(rule_identity=strategy.rule_identity, strategy_id='auditv2',
        calendar=window['calendar'], day_packets=funnel_day_packets_v1(result, window['calendar']),
        expected_decision_sessions=window['calendar'][60:], event_sink=lambda row: None)
    assert funnel['counts']['opportunity_signals'] == 21
    assert funnel['opportunity_dispositions'] == {'END_OF_OBSERVATION_NO_NEXT_SESSION': 3, 'SCORE_UNKNOWN': 18}
