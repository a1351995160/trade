from copy import deepcopy
import sys
from pathlib import Path

import pandas as pd
import pytest

from chanlun_trader.research_factory.structured_exit_trial_v1 import NAMES,EXIT_ONLY,WITH_MARKET,EXIT_POLICY
from chanlun_trader.research_factory.train_search_batch_v1 import contract
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.fixed_account_rules import FixedAccountRules
from chanlun_trader.research_factory.degraded_execution_v2 import _run_account
from chanlun_trader.research_factory.technical_train_signals_v1 import transform


def bundle(name,late=False,missing=False):
    from test_degraded_execution_v2 import degraded
    b=degraded()
    days=[int(d.strftime('%Y%m%d')) for d in pd.bdate_range('2022-08-01',periods=35)]
    daily=b.daily[b.daily.date==20220801];states=b.states[b.states.trade_date=='20220801']
    b.daily=pd.concat([daily.assign(date=d) for d in days],ignore_index=True)
    b.states=pd.concat([states.assign(trade_date=str(d)) for d in days],ignore_index=True)
    b.calendar=days
    original=b.ready_factors[b.ready_factors.timestamp==20220801]
    rows=[]
    for i,d in enumerate(days[:-1]):
        visible=pd.Timestamp(str(days[i+1])+' 09:30',tz='Asia/Shanghai')
        row=original.assign(timestamp=d,value=-.1 if i==0 else 1.,effective_available_at=visible,
            signal_version=contract(name)['signal_version'],exit_invalidated=1. if i>=3 else 0.,
            exit_available_at=pd.Timestamp('2025-01-01',tz='UTC') if late else visible)
        if not missing or i<3:rows.append(row)
    b.ready_factors=pd.concat(rows,ignore_index=True)
    b.contract_identity=stable_hash(contract(name))
    return b,days


@pytest.mark.parametrize('name',NAMES)
def test_actual_engine_invalidation_sells_next_open_and_keeps_fees(name):
    b,days=bundle(name)
    result=_run_account(b,('SYNTHETIC',False),None,contract(name))
    sells=[f for f in result['fills'] if f['side']=='SELL']
    assert len(sells)==3
    assert {int(f['fill_time'].strftime('%Y%m%d')) for f in sells}=={days[5]}
    assert {d['reason_code'] for d in result['exit_decisions']}=={'EXIT_DUE_STRUCTURE_INVALIDATION'}
    assert result['metrics']['ending_equity']==pytest.approx(10000-30-18-4.4955)


@pytest.mark.parametrize('kind',['late','missing'])
def test_unavailable_structure_does_not_cancel_fixed_exit(kind):
    b,days=bundle(EXIT_ONLY,**{kind:True})
    result=_run_account(b,('SYNTHETIC',False),None,contract(EXIT_ONLY))
    sells=[f for f in result['fills'] if f['side']=='SELL']
    assert len(sells)==3
    assert {int(f['fill_time'].strftime('%Y%m%d')) for f in sells}=={days[22]}


def test_hazard_full_holding_window_is_not_shrunk_for_early_exit():
    b,days=bundle(EXIT_ONLY)
    b.hazards={s:[days[15]] for s in b.daily.symbol.unique()}
    assert not _run_account(b,('SYNTHETIC',False),None,contract(EXIT_ONLY))['fills']


def test_suspended_sell_retries_even_after_invalidation_disappears():
    b,days=bundle(EXIT_ONLY)
    b.states.loc[b.states.trade_date==str(days[5]),'suspension_status']='SUSPENDED'
    b.ready_factors.loc[b.ready_factors.timestamp>days[3],'exit_invalidated']=0.
    result=_run_account(b,('SYNTHETIC',False),None,contract(EXIT_ONLY))
    sells=[f for f in result['fills'] if f['side']=='SELL']
    assert len(sells)==3
    assert {int(f['fill_time'].strftime('%Y%m%d')) for f in sells}=={days[6]}


def test_contract_policy_is_not_mutable_global_or_silently_relaxed():
    bad=contract(EXIT_ONLY);bad['exit_policy']['factor_conditions'][0]['value']=0
    assert EXIT_POLICY['factor_conditions'][0]['value']==1
    with pytest.raises(ValueError,match='CONFLICT'):FixedAccountRules(bad)


@pytest.mark.parametrize('name',NAMES)
def test_new_exposure_keeps_original_increment_and_revocation(tmp_path,name):
    from test_train_search_batch_v1 import test_governed_increment_repeat_and_revocation
    test_governed_increment_repeat_and_revocation(tmp_path,name)


def test_market_gate_never_hides_stock_exit_or_uses_late_breadth():
    import numpy as np
    days=[int(d.strftime('%Y%m%d')) for d in pd.bdate_range('2022-08-01',periods=200)]
    c=100+np.arange(200)*.2
    rows=pd.DataFrame({'symbol':'000001.SZ','timestamp':days,'signal_close':c,'signal_open':c,
        'signal_high':c+.1,'signal_low':c-.1,'effective_available_at':pd.Timestamp('2022-01-01',tz='UTC'),
        'market_median':-.01,'market_available_at':pd.Timestamp('2022-01-01',tz='UTC')})
    rows.loc[198,'signal_low']=137.5
    baseline=transform(rows,days,EXIT_ONLY)
    assert baseline.value.iloc[-1]<0
    gated=transform(rows,days,WITH_MARKET)
    assert gated.value.iloc[-1]==1
    pd.testing.assert_series_equal(baseline.exit_invalidated,gated.exit_invalidated)
    pd.testing.assert_series_equal(baseline.exit_available_at,gated.exit_available_at)
    rows['market_median']=.01;rows['market_available_at']=pd.Timestamp('2028-01-01',tz='UTC')
    gated=transform(rows,days,WITH_MARKET)
    assert gated.value.iloc[-1]<0 and gated.effective_available_at.iloc[-1].year==2028
    assert gated.exit_available_at.iloc[-1].year==2022
    rows['market_median']=np.nan;rows['market_available_at']=pd.NaT
    gated=transform(rows,days,WITH_MARKET)
    assert gated.computable.iloc[-1] and gated.value.iloc[-1]==1 and pd.notna(gated.exit_invalidated.iloc[-1])


def test_failure_summary_distinguishes_losses_from_insufficient_sample():
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
    from review_failed_composites_v1 import summarize
    assert summarize({'status':'COMPLETE','metrics':{'train_net_return':-.1,'closed_lots':48}})['failed_original_conditions']==['TRAIN_NET_RETURN_NOT_POSITIVE']
    assert summarize({'status':'COMPLETE','metrics':{'train_net_return':.1,'closed_lots':29}})['failed_original_conditions']==['CLOSED_LOTS_BELOW_30']
