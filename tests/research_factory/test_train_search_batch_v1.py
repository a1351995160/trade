import numpy as np
import pandas as pd
import pytest

from chanlun_trader.research_factory.train_search_batch_v1 import contract, design, transform, TrainSearchGovernanceV1
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.novelty import CandidateNoveltyGateV2
from test_windowed_actions_and_train_grant import grant


def data():
    dates = pd.bdate_range('2022-08-01', periods=25)
    sessions = [int(d.strftime('%Y%m%d')) for d in dates]
    return sessions, pd.DataFrame({'symbol':'000001.SZ','timestamp':sessions,
        'value':np.arange(25)/100,
        'effective_available_at': dates.tz_localize('Asia/Shanghai')+pd.Timedelta(days=1,hours=9,minutes=30)})


def test_formula_by_hand_and_no_future_feedback():
    sessions, rows = data()
    momentum = transform(rows,sessions,'MOMENTUM_5')
    assert momentum.value.iloc[5] == pytest.approx(-.05)
    stable = transform(rows,sessions,'STABILITY_20')
    assert stable.value.iloc[:19].isna().all()
    expected = -1/(1+np.sqrt(sum((x/100-.095)**2 for x in range(20))/20))
    assert stable.value.iloc[19] == pytest.approx(expected)
    rows.loc[24,'value'] = 10000
    changed = transform(rows,sessions,'STABILITY_20')
    pd.testing.assert_frame_equal(stable.iloc[:24],changed.iloc[:24])


def test_missing_date_is_not_compressed_and_late_evidence_wins():
    sessions, rows = data()
    missing = transform(rows.drop(index=10),sessions,'STABILITY_20')
    assert not missing.computable.any()
    late = pd.Timestamp('2023-01-01',tz='UTC')
    rows.loc[5,'effective_available_at'] = late
    result = transform(rows,sessions,'STABILITY_20')
    assert result.effective_available_at.iloc[19] == late


def test_undefined_not_zero_and_unfrozen_rejected():
    sessions, rows = data()
    rows.loc[4,'value'] = np.inf
    assert not transform(rows,sessions,'MOMENTUM_5').computable.iloc[4]
    with pytest.raises(ValueError,match='UNFROZEN'):
        contract('MOMENTUM_6')
    with pytest.raises(ValueError,match='IDENTITY'):
        transform(pd.concat([rows,rows.iloc[:1]]),sessions,'MOMENTUM_5')


def test_sixty_session_compounding_and_amount_units():
    dates=pd.bdate_range('2022-08-01',periods=70)
    sessions=[int(d.strftime('%Y%m%d')) for d in dates]
    rows=pd.DataFrame({'symbol':'000001.SZ','timestamp':sessions,
        'value':[np.nan]*5+[1.01**5-1]*65,
        'effective_available_at':dates.tz_localize('Asia/Shanghai')+pd.Timedelta(days=1),
        'amount':np.arange(1,71)*10000.})
    result=transform(rows,sessions,'MOMENTUM_60')
    assert result.value.iloc[:60].isna().all()
    assert result.value.iloc[60]==pytest.approx(1-1.01**60)
    turnover=transform(rows,sessions,'LIQUIDITY_20')
    assert turnover.value.iloc[19]==pytest.approx(-105000.)
    rows.loc[30,'amount']=0
    turnover=transform(rows,sessions,'LIQUIDITY_20')
    assert turnover.value.iloc[30:50].isna().all()


def test_long_hold_uses_original_exit_engine_and_preserves_legacy_contract():
    from test_degraded_execution_v2 import degraded
    from chanlun_trader.research_factory.degraded_execution_v2 import _run_account
    from chanlun_trader.research_factory.fixed_account_rules import FixedAccountRules
    b=degraded()
    days=[int(d.strftime('%Y%m%d')) for d in pd.bdate_range('2022-08-01',periods=35)]
    daily=b.daily[b.daily.date==20220801]
    states=b.states[b.states.trade_date=='20220801']
    b.daily=pd.concat([daily.assign(date=d) for d in days],ignore_index=True)
    b.states=pd.concat([states.assign(trade_date=str(d)) for d in days],ignore_index=True)
    b.calendar=days
    b.ready_factors=b.ready_factors[b.ready_factors.timestamp==20220801]
    frozen=contract('STABILITY_20_HOLD_20')
    b.contract_identity=stable_hash(frozen)
    result=_run_account(b,('SYNTHETIC',False),None,frozen)
    buys=[f for f in result['fills'] if f['side']=='BUY']
    sells=[f for f in result['fills'] if f['side']=='SELL']
    assert len(buys)==len(sells)==3
    assert {int(f['fill_time'].strftime('%Y%m%d')) for f in sells}=={days[22]}
    assert result['metrics']['total_fees']>0
    # 恒定10元，3组各300股：佣金30、双边滑点18、卖印花税4.4955。
    assert result['metrics']['ending_equity']==pytest.approx(10000-30-18-4.4955)
    b.hazards={s:[days[15]] for s in b.daily.symbol.unique()}
    assert not _run_account(b,('SYNTHETIC',False),None,frozen)['fills']
    with pytest.raises(ValueError,match='UNSUPPORTED'):
        FixedAccountRules({**contract('STABILITY_20'),'holding_sessions':20})


def test_market_gate_uses_cross_section_time_without_filling_missing_signal():
    sessions, rows=data()
    rows['market_median']=-.01
    rows['market_available_at']=rows.effective_available_at
    negative=transform(rows,sessions,'STABILITY_20_HOLD_20_MARKET_5')
    assert negative.value.iloc[:19].isna().all()
    assert negative.value.iloc[19:].eq(1).all()
    rows['market_median']=.01
    late=pd.Timestamp('2023-01-01',tz='UTC')
    rows.loc[19,'market_available_at']=late
    positive=transform(rows,sessions,'STABILITY_20_HOLD_20_MARKET_5')
    assert positive.value.iloc[19]<0
    assert positive.effective_available_at.iloc[19]==late


@pytest.mark.parametrize('name',['MOMENTUM_5','STABILITY_20','MOMENTUM_60','LIQUIDITY_20',
                                'STABILITY_20_HOLD_20','LIQUIDITY_20_HOLD_20',
                                'MOMENTUM_60_HOLD_20_MARKET_5','STABILITY_20_HOLD_20_MARKET_5',
                                'MACD_CROSS_HOLD_20','KDJ_OVERSOLD_CROSS_HOLD_20',
                                'CHAN_BOTTOM_MACD_HOLD_20','MONTHLY_REVERSAL_HOLD_20',
                                'HIGH_252_HOLD_20','LOW_MAX_20_HOLD_20'])
def test_governed_increment_repeat_and_revocation(tmp_path,name):
    _,plan,source,evidence,parent=grant(tmp_path)
    service=TrainSearchGovernanceV1(tmp_path,name)
    frozen=contract(name)
    cid=stable_hash(frozen)
    plan.update(contracts={cid:frozen},result_type=frozen['result_type'])
    source['approval_record_sha256']=source.pop('attachment_sha256')
    evidence.update(raw_hfq_pairing_verified=True,historical_universe_complete=True,
        source_identity_verified=True,feasibility_passed=True,novelty_status='PASSED',
        novelty_decision=CandidateNoveltyGateV2().evaluate(design(name)).to_dict())
    from chanlun_trader.research_factory.budget import SearchBudgetRegistryV1
    old=SearchBudgetRegistryV1(plan['objective_id'],service.budget_path).snapshot()['buckets']
    receipt=service.confirm(plan,source,preflight=lambda:evidence)
    assert service.confirm(plan,source,preflight=lambda:evidence)==receipt
    after=SearchBudgetRegistryV1(plan['objective_id'],service.budget_path).snapshot()['buckets']
    assert all(bucket in after for bucket in old)
    assert receipt['authorization_origin']=='USER_RESEARCH_DELEGATION_NOT_PER_CANDIDATE_APPROVAL'
    attempt=service.reserve(cid)
    service.start_exposure(attempt['execution_id'])
    service.settle(attempt['execution_id'],1,True)
    assert service.summary()['MAIN_BACKTEST_EXPOSURES_USED']==1
    assert service.reserve(cid)['status']=='ALREADY_ATTEMPTED'
    with pytest.raises(PermissionError,match='REPAIR'):
        service.reserve(cid,{'dummy':True})
    service.revoke('SYNTHETIC_STOP')
    with pytest.raises(PermissionError):
        service.active()
