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


@pytest.mark.parametrize('name',['MOMENTUM_5','STABILITY_20','MOMENTUM_60','LIQUIDITY_20'])
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
