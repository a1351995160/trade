from unittest.mock import patch
from test_train_search_batch_v1 import _SyntheticClock
import sys
from pathlib import Path

import pandas as pd
import pytest
from chanlun_trader.research_factory.weekly_window_v1 import contract,NAME
from chanlun_trader.research_factory.fixed_account_rules import FixedAccountRules
from chanlun_trader.research_factory.train_search_batch_v1 import contract as original
from chanlun_trader.research_factory.degraded_train_v1 import feasibility_verdict

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))


def test_fixed_scope_original_thresholds():
    assert FixedAccountRules(original(NAME)).signal_window==(20220801,20240731)
    assert FixedAccountRules(contract()).signal_window==(20250801,20260731)
    value=contract();value['input_window'][0]=20241008
    with pytest.raises(PermissionError):FixedAccountRules(value)
    paths=[{'symbol':str(i%3),'entry_date':i//3,'reason':'COMPLETE_CLOSURE_PATH'} for i in range(30)]
    assert not feasibility_verdict(paths)['passed']
    assert contract()['thresholds']=={'closed_paths':30,'entry_dates':20,'symbols':2}


# Historical synthetic approval window only; production expiry is unchanged.
@patch('test_windowed_actions_and_train_grant.datetime',_SyntheticClock)
@patch('chanlun_trader.research_factory.degraded_governance_v1.datetime',_SyntheticClock)
@patch('chanlun_trader.research_factory.train_execution_governance_v1.datetime',_SyntheticClock)
@patch('chanlun_trader.research_factory.exploration_governance.datetime',_SyntheticClock)
def test_budget_parent_preserved_replay_and_revocation(tmp_path):
    from test_windowed_actions_and_train_grant import grant
    from chanlun_trader.research_factory.weekly_window_governance_v1 import WeeklyWindowGovernanceV1
    _,plan,source,evidence,parent=grant(tmp_path)
    service=WeeklyWindowGovernanceV1(tmp_path)
    plan.update(contracts={'fixed':contract()},result_type=contract()['result_type'])
    evidence.update(feasibility_passed=True,source_candidate_hash=contract()['source_candidate_contract_hash'])
    source.update(approval_record_sha256='SYNTHETIC',window_release_sha256='SYNTHETIC',approved_plan_sha256='SYNTHETIC')
    before=parent.summary()
    service.confirm(plan,source,preflight=lambda:evidence)
    r=service.reserve('fixed');service.start_exposure(r['execution_id']);service.settle(r['execution_id'],1,True)
    assert service.summary()['MAIN_BACKTEST_EXPOSURES_USED']==1
    assert service.summary()['REPAIR_BACKTEST_EXPOSURES_USED']==0
    assert service.reserve('fixed')['status']=='ALREADY_ATTEMPTED'
    assert parent.summary()==before
    service.revoke('SYNTHETIC_STOP')
    with pytest.raises(PermissionError):service.active()


@pytest.mark.parametrize('all_st',[False,True])
def test_stitch_to_feasibility_and_original_account(tmp_path,monkeypatch,all_st):
    import prepare_weekly_window_v1 as mod
    from chanlun_trader import synthetic_batch_resources
    from chanlun_trader.research_factory.exploration_governance import immutable
    from chanlun_trader.research_factory.degraded_execution_v2 import _run_account
    new=tmp_path/'new';old=tmp_path/'old';new.mkdir();old.mkdir()
    monkeypatch.setattr(mod,'ROOT',new);monkeypatch.setattr(mod,'OLD',old)
    monkeypatch.setattr(mod,'check',lambda:{'thread_id':'SYNTHETIC','identity':'SYNTHETIC'})
    handshakes=[]
    def handshake():
        handshakes.append(True)
        assert len(handshakes)==1, 'RESOURCE_STDIN_HANDSHAKE_IS_SINGLE_USE'
        return {'execution':{'stage':'prepare-window'}}
    monkeypatch.setattr(synthetic_batch_resources,'worker_resource_handshake',handshake)
    days=pd.bdate_range('2024-10-09','2026-07-31').strftime('%Y-%m-%d').tolist()
    existing=[d for d in days if d>='2025-07-03'];overlap=existing[:5]
    immutable(new/'CALENDAR_WINDOW.json',{'warmup_start':days[0],'sessions':days,'overlap_sessions':overlap,'fetch_end':overlap[-1]})
    immutable(old/'CALENDAR_WINDOW.json',{'sessions':existing})
    immutable(new/'PLAN_PROPOSAL_V2.json',{'source_candidate_contract_hash':contract()['source_candidate_contract_hash']})
    immutable(new/'ACQUISITION_COMPLETED.json',{'synthetic':True})
    codes=['sh.600000','sh.600001','sh.600002'];inputs={}
    def archived(path,rows):
        immutable(path,{'rows':rows,'error_code':'0'})
        immutable(path.with_suffix('.access.json'),{'sha256':mod.sha(path)})
        if path.is_relative_to(old):inputs[str(path)]=mod.sha(path)
    archived(old/'acquisition/basic.json',[{'code':c,'ipoDate':'1999-01-01','outDate':''} for c in codes])
    immutable(old/'WINDOW_UNIVERSE.json',{'codes':codes})
    for day in days:archived((old if day in existing else new)/'acquisition/pool'/f'{day}.json',[{'code':c,'tradeStatus':'1'} for c in codes])
    for code in codes:
        raw=[];hfq=[]
        for i,day in enumerate(days):
            p=10+i*.002
            raw.append(dict(code=code,date=day,adjustflag='3',open=str(p),high=str(p+.02),low=str(p-.02),
                close=str(p),preclose=str(p-.002),volume='1000000',amount=str(p*1000000),tradestatus='1',isST='1' if all_st else '0'))
            hfq.append(dict(code=code,date=day,adjustflag='1',close=str(p*2)))
        for flag,rows in [('3',raw),('1',hfq)]:
            path=old/'responses'/f'probe-{flag}.json' if code=='sh.600000' else old/'acquisition/prices'/code/f'{flag}.json'
            archived(path,[r for r in rows if r['date'] in existing])
            archived(new/'acquisition/prices'/code/f'{flag}.json',[r for r in rows if r['date']<=overlap[-1]])
        for root in [old,new]:archived(root/'acquisition/actions'/f'{code}.json',[])
    immutable(old/'INPUT_MANIFEST.json',{'inputs':inputs,'symbols':[c[3:]+'.SH' for c in codes]})
    mod.prepare();bundle=mod.load_bundle();ready=mod.read(new/'READY.json')
    assert ready['feasibility_passed'] is (not all_st)
    assert len(bundle.daily)==len(days)*3 and len(bundle.states)==len(days)*3
    assert bundle.daily.adjustflag.eq('3').all()
    assert bundle.ready_factors.timestamp.between(20250801,20260731).all()
    if all_st:assert bundle.ready_factors.empty
    else:
        result=_run_account(bundle,('SYNTHETIC',False),None,contract())
        assert result['status']=='COMPLETE'
        assert result['fills']
        assert all(int(f['fill_time'].strftime('%Y%m%d'))>=20250801 for f in result['fills'])
        # 复权价为两倍；成交应接近10元，不能接近20元。
        assert all(f['price']<15 for f in result['fills'])
        from review_monthly_robustness_v1 import describe
        with pytest.raises(ValueError,match='IDENTITY'):describe(result)
        result['evaluation_window']=[20250801,20260731]
        summary=describe(result,approved_weekly_window=True)
        assert summary['original_metrics']==result['metrics']
        assert [r['multiple'] for r in summary['static_cost_sensitivity']]==[1.5,2.0]
