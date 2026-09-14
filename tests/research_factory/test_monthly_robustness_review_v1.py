import sys
from pathlib import Path

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
from review_monthly_robustness_v1 import describe


def fixture():
    snapshots=[{'timestamp':'2022-12-30T15:30:00+08:00','equity':10100,'unrealized_pnl':30,'positions':1},
               {'timestamp':'2023-01-03T15:30:00+08:00','equity':10200,'unrealized_pnl':130,'positions':1}]
    fills=[{'side':'SELL','total_fee':10,'slippage_cost':2,
            'lot_allocations':[{'lot_id':'a','realized_pnl':100},{'lot_id':'b','realized_pnl':-30}]}]
    return {'status':'COMPLETE','metrics':{'ending_equity':10200,'total_fees':10,
            'total_slippage_cost':2,'closed_lots':2},'daily_account':snapshots,'fills':fills,
            'final_account_checkpoint':{'state':{'initial_cash':10000,
                'lots':{'a':{'remaining_quantity':0},'b':{'remaining_quantity':0}}}}}


def test_hand_cost_period_boundaries_and_concentration():
    r=describe(fixture())
    assert r['static_cost_sensitivity'][1]['static_net_change_cny']==188
    assert r['periods']['years'][1]['start_equity']==10100
    assert r['periods']['years'][1]['return']==pytest.approx(10200/10100-1)
    assert r['closed_lot_net_pnl']==70
    assert r['closed_lot_concentration'][0]['closed_net_excluding_top_winners']==-30
    assert r['flags']==['CLOSED_NET_DEPENDS_ON_TOP_WINNER']


def test_missing_identity_and_zero_gross_profit_not_filled():
    r=fixture();r['fills'][0]['lot_allocations'][0]['realized_pnl']=0
    assert describe(r)['closed_lot_concentration'][0]['share_of_gross_winning_pnl'] is None
    r['metrics']['total_fees']=11
    with pytest.raises(ValueError,match='COST_COMPONENT'):describe(r)


def test_no_compression_of_duplicates_or_result_conflicts():
    r=fixture();r['daily_account'].append({**r['daily_account'][-1],'equity':10199})
    with pytest.raises(ValueError,match='DAILY_IDENTITY'):describe(r)
    r=fixture();r['metrics']['ending_equity']=20000
    with pytest.raises(ValueError,match='ENDING_EQUITY'):describe(r)


def test_identical_terminal_snapshot_from_original_finalize():
    r=fixture();r['daily_account'].append(dict(r['daily_account'][-1]))
    result=describe(r)
    assert result['snapshot_records']==3
    assert result['logical_sessions']==2
    assert sum(p['observed_sessions'] for p in result['periods']['months'])==2
