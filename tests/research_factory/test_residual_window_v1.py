from unittest.mock import patch
from test_train_search_batch_v1 import _SyntheticClock
import sys
from pathlib import Path

import pandas as pd
import pytest

from chanlun_trader.research_factory.residual_window_v1 import contract,NAME
from chanlun_trader.research_factory.train_search_batch_v1 import contract as original
from chanlun_trader.research_factory.fixed_account_rules import FixedAccountRules
from chanlun_trader.research_factory.common import stable_hash

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))


def test_window_changes_no_strategy_rule_and_old_isolation():
    base=original(NAME);value=contract()
    changed={k for k in base if value[k]!=base[k]}
    assert changed=={'version','adapter_version','purpose','result_type'}
    assert value['source_candidate_contract_hash']==stable_hash(base)
    assert FixedAccountRules(base).signal_window==(20220801,20240731)
    assert FixedAccountRules(value).signal_window==(20250801,20260731)
    for key,val in [('holding_sessions',19),('signal_window',[20250801,20260801]),('input_window',[20241008,20260731])]:
        bad=contract();bad[key]=val
        with pytest.raises((ValueError,PermissionError)):FixedAccountRules(bad)


# Historical synthetic approval window only; production expiry is unchanged.
@patch('test_windowed_actions_and_train_grant.datetime',_SyntheticClock)
@patch('chanlun_trader.research_factory.degraded_governance_v1.datetime',_SyntheticClock)
@patch('chanlun_trader.research_factory.train_execution_governance_v1.datetime',_SyntheticClock)
@patch('chanlun_trader.research_factory.exploration_governance.datetime',_SyntheticClock)
def test_canonical_increment_preserves_parent_and_rejects_replay(tmp_path,name=NAME):
    from test_windowed_actions_and_train_grant import grant
    from chanlun_trader.research_factory.residual_window_governance_v1 import ResidualWindowGovernanceV1
    _,plan,source,evidence,parent=grant(tmp_path)
    service=ResidualWindowGovernanceV1(tmp_path,name)
    plan.update(contracts={'fixed':contract(name)},result_type=contract(name)['result_type'])
    evidence.update(feasibility_passed=True,source_candidate_hash=contract(name)['source_candidate_contract_hash'])
    source.update(approval_record_sha256='SYNTHETIC',window_release_sha256='SYNTHETIC',approved_plan_sha256='SYNTHETIC')
    before=parent.summary()
    wrong={**evidence,'source_candidate_hash':'wrong'}
    with pytest.raises(PermissionError,match='LINEAGE'):service.confirm(plan,source,preflight=lambda:wrong)
    service.confirm(plan,source,preflight=lambda:evidence)
    reserved=service.reserve('fixed');service.start_exposure(reserved['execution_id']);service.settle(reserved['execution_id'],1,True)
    assert service.summary()['MAIN_BACKTEST_EXPOSURES_USED']==1
    assert service.summary()['REPAIR_BACKTEST_EXPOSURES_USED']==0
    assert parent.summary()==before
    assert service.reserve('fixed')['status']=='ALREADY_ATTEMPTED'
    service.revoke('SYNTHETIC_STOP')
    with pytest.raises(PermissionError):service.active()


@pytest.mark.parametrize('all_st',[False,True])
def test_reuse_source_to_new_feature_bundle_and_hash_rejection(tmp_path,monkeypatch,all_st,name=NAME):
    from test_weekly_window_adapter_v1 import test_stitch_to_feasibility_and_original_account
    test_stitch_to_feasibility_and_original_account(tmp_path,monkeypatch,all_st)
    import prepare_residual_window_v1 as mod
    monkeypatch.setattr(mod,'NAME',name)
    monkeypatch.setattr(mod,'contract',lambda:contract(name))
    source=tmp_path/'new';old=tmp_path/'old';out=tmp_path/'residual';out.mkdir()
    monkeypatch.setattr(mod,'ROOT',out);monkeypatch.setattr(mod,'INPUT',source);monkeypatch.setattr(mod,'OLD',old)
    monkeypatch.setattr(mod,'guard',lambda:{'thread_id':'SYNTHETIC'})
    mod.save(out/'WINDOW_RELEASE.json',{'synthetic':True})
    raw_hash=mod.sha(source/'DAILY.parquet')
    mod.prepare();bundle=mod.load_bundle()
    if all_st:
        assert bundle.ready_factors.empty
        assert not mod.read(out/'READY.json')['feasibility_passed']
    assert mod.sha(source/'DAILY.parquet')==raw_hash
    assert bundle.ready_factors.timestamp.between(20250801,20260731).all()
    assert bundle.ready_factors.signal_version.eq('TRAIN_SEARCH_BATCH_V1_'+name).all()
    assert bundle.daily.adjustflag.eq('3').all()
    assert not (out/'DAILY.parquet').exists()
    assert bundle.contract_identity==stable_hash(contract(name))
    with pytest.raises(PermissionError,match='OVERWRITE'):mod.prepare()
    (out/'FEATURES.parquet').write_bytes(b'corrupt')
    with pytest.raises(PermissionError,match='CHANGED'):mod.load_bundle()


def test_actual_account_external_window_keeps_raw_price_and_fixed_exit(name=NAME):
    from test_structured_exit_trial_v1 import bundle
    from chanlun_trader.research_factory.degraded_execution_v2 import _run_account
    b,old_days=bundle(name)
    days=[int(d.strftime('%Y%m%d')) for d in pd.bdate_range('2025-08-01',periods=len(old_days))]
    mapping=dict(zip(old_days,days))
    b.daily['date']=b.daily.date.map(mapping)
    b.states['trade_date']=b.states.trade_date.astype(int).map(mapping).astype(str)
    b.ready_factors['timestamp']=b.ready_factors.timestamp.map(mapping)
    next_open={d:pd.Timestamp(str(days[i+1])+' 09:30',tz='Asia/Shanghai') for i,d in enumerate(days[:-1])}
    b.ready_factors['effective_available_at']=b.ready_factors.timestamp.map(next_open)
    b.calendar=days;b.contract_identity=stable_hash(contract(name))
    result=_run_account(b,('SYNTHETIC',False),None,contract(name))
    sells=[f for f in result['fills'] if f['side']=='SELL']
    assert len(sells)==3
    assert {int(f['fill_time'].strftime('%Y%m%d')) for f in sells}=={days[22]}
    assert all(f['price']<15 for f in result['fills'])
    assert result['metrics']['ending_equity']==pytest.approx(10000-30-18-4.4955)
