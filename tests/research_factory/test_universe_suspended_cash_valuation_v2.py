"""停牌现金除息的账户权益，不能由执行器与审计共同高估来证明正确。"""
import json

import pandas as pd
import pytest

from chanlun_trader.research_factory.universe_account_backend_v2 import SegmentBoundary
from chanlun_trader.research_factory.universe_execution_artifacts_v1 import hydrated_result
from test_universe_evidence_v2 import audit, rerun_v2
from test_universe_sparse_causal_price_v3 import sparse_case


def test_suspended_cash_ex_date_does_not_create_extra_account_equity(tmp_path):
    window, bundle, rule = sparse_case()
    result = rerun_v2(tmp_path/'job', window, bundle, rule)
    days = {row['date']: row for row in hydrated_result(result)['daily_accounts']}
    before, effective = window['calendar'][64:66]
    quantity = days[before]['positions'][0]['quantity']
    assert quantity > 0
    assert days[effective]['cash'] == days[before]['cash']
    assert days[effective]['equity'] == pytest.approx(days[before]['equity'], abs=1e-6)
    rebuilt = audit((bundle, window, rule, result))
    assert rebuilt['daily_accounts'] == list(hydrated_result(result)['daily_accounts'])


def test_suspended_cash_mark_and_position_pnl_survive_resume_without_double_adjustment(tmp_path):
    window, bundle, rule = sparse_case()
    root = tmp_path/'job'
    with pytest.raises(SegmentBoundary):
        rerun_v2(root, window, bundle, rule, stop=window['calendar'][65])
    saved = json.loads((root/'EXECUTION.json').read_bytes())['ledger']['state']
    symbol = window['symbols'][0]
    assert saved['last_price'][symbol] == pytest.approx(11.99)
    for position in saved['positions'].values():
        if position['quantity']:
            assert position['unrealized_pnl'] == pytest.approx(
                (11.99-position['average_cost'])*position['quantity'])
    resumed = rerun_v2(root, window, bundle, rule)
    continuous = rerun_v2(tmp_path/'reference', window, bundle, rule)
    assert resumed['final_account_checkpoint'] == continuous['final_account_checkpoint']
    assert list(hydrated_result(resumed)['daily_accounts']) == list(hydrated_result(continuous)['daily_accounts'])


@pytest.mark.parametrize('taxable', [False, True])
def test_same_day_suspended_cash_and_shares_keep_neutral_equity(taxable, tmp_path):
    from test_universe_evidence_v1 import share_case
    bundle, window, rule, _ = share_case(taxable=taxable, cash=True, max_hold_sessions=99)
    days = window['calendar']
    original = bundle['states'].iloc[0].to_dict()
    bundle['states'] = pd.DataFrame([{**original, 'valid_to': days[62]},
        {**original, 'effective_date': days[63], 'valid_to': days[66], 'suspension_status': 'SUSPENDED'},
        {**original, 'effective_date': days[67]}])
    bundle['daily'] = bundle['daily'].loc[~bundle['daily'].date.isin(days[63:67])].copy()
    result = rerun_v2(tmp_path/'job', window, bundle, rule)
    accounts = {row['date']: row for row in hydrated_result(result)['daily_accounts']}
    for day in days[63:67]:
        assert accounts[day]['equity'] == pytest.approx(accounts[days[62]]['equity'], abs=1e-6)
    assert accounts[days[63]]['positions'][0]['quantity'] == accounts[days[62]]['positions'][0]['quantity']*1.3
    assert audit((bundle, window, rule, result))['metrics'] == result['metrics']


def test_suspended_cash_trace_remains_compatible_with_both_reports(tmp_path):
    from chanlun_trader.research_factory.universe_account_inputs_v1 import UniverseAccountInputsV1
    from chanlun_trader.research_factory.universe_research_report_v2 import build_research_reports, default_observation_plan
    window, bundle, rule = sparse_case()
    result = rerun_v2(tmp_path/'job', window, bundle, rule)
    inputs = UniverseAccountInputsV1(bundle, window, stage='ACCOUNT')
    reports = build_research_reports(hydrated_result(result), inputs, default_observation_plan(window))
    assert reports['account']['net_return'] == result['metrics']['net_return']
    assert reports['strategy_qualified'] is False
    assert [row for row in result['final_account_checkpoint']['economic']['action_audit']
        if row['phase'] == 'SUSPENDED_CASH_MARK']
