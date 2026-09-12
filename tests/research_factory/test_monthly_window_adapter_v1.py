"""外窗最小适配的真实组件合成验证。"""
import pandas as pd
import pytest
from chanlun_trader.research_factory.monthly_window_v1 import contract, feasibility
from chanlun_trader.research_factory.fixed_account_rules import FixedAccountRules
from chanlun_trader.research_factory.train_search_batch_v1 import contract as old_contract
from chanlun_trader.research_factory.baostock_price_views_v1 import prepare_symbol


def test_old_contract_stays_train_and_new_contract_cannot_expand():
    assert FixedAccountRules(old_contract('MONTHLY_REVERSAL_HOLD_20')).signal_window==(20220801,20240731)
    assert FixedAccountRules(contract()).signal_window==(20250801,20260731)
    changed=contract();changed['signal_window'][1]=20260801
    with pytest.raises(PermissionError):FixedAccountRules(changed)
    changed=old_contract('MONTHLY_REVERSAL_HOLD_20');changed['signal_window']=[20250801,20260731]
    with pytest.raises(PermissionError):FixedAccountRules(changed)


def test_new_gate_ten_dates_keeps_old_verdict_and_all_other_thresholds():
    paths=[{'symbol':str(i%3),'entry_date':i//3,'reason':'COMPLETE_CLOSURE_PATH'} for i in range(30)]
    result=feasibility(paths,contract())
    assert result['passed'] and not result['original_train_verdict']['passed']
    assert not feasibility(paths[:-1],contract())['passed']
    assert not feasibility([{**p,'symbol':'ONE'} for p in paths],contract())['passed']


def test_window_raw_and_adjusted_remain_separate():
    days=[int(d.strftime('%Y%m%d')) for d in pd.bdate_range('2025-07-03',periods=8)]
    raw=[dict(code='sh.600000',date=str(pd.Timestamp(str(d)).date()),adjustflag='3',
              open='5',high='5',low='5',close='5',preclose='5',volume='100000',amount='500000',
              tradestatus='1',isST='0') for d in days]
    hfq=[dict(code=r['code'],date=r['date'],close='70',adjustflag='1') for r in raw]
    views=prepare_symbol('600000.SH',raw,hfq,days,window_contract=contract())
    assert views.execution_store().get_daily_bar('600000.SH',days[0])['open']==5
    assert not views.execution_store().daily_hfq
    with pytest.raises(ValueError,match='TRAIN'):prepare_symbol('600000.SH',raw,hfq,days)


def test_window_original_account_engine_enters_and_exits_at_fixed_sessions():
    from types import SimpleNamespace
    from chanlun_trader.data.tdx.windowed_actions_v1 import WindowedCorporateActionDatasetV1
    from chanlun_trader.research_factory.degraded_execution_v2 import _run_account
    from chanlun_trader.research_factory.common import stable_hash
    days=[int(d.strftime('%Y%m%d')) for d in pd.bdate_range('2025-07-03','2025-10-15')]
    daily=[];states=[];features=[]
    for i,day in enumerate(days):
        for symbol in ['600000.SH','600001.SH','600002.SH']:
            daily.append(dict(symbol=symbol,date=day,open=10,high=10.1,low=9.9,close=10,prev_close=10,
                              volume=1000000,amount=10000000,adjustflag='3'))
            states.append(dict(symbol=symbol,trade_date=day,listed=True,delisted=False,universe_member=True,
                eligibility_status='ELIGIBLE',st_status='NORMAL',suspension_status='TRADING',board='MAIN'))
            if i+1<len(days):
                features.append(dict(symbol=symbol,timestamp=day,value=-.1 if day==20250829 else 1,
                    effective_available_at=pd.Timestamp(str(days[i+1])+' 09:30',tz='Asia/Shanghai')))
    actions=WindowedCorporateActionDatasetV1('SYNTHETIC','WindowedCorporateActionDatasetV1',days[0],days[-1],(),
        'a'*64,('600000.SH','600001.SH','600002.SH'),'SYNTHETIC')
    bundle=SimpleNamespace(daily=pd.DataFrame(daily),states=pd.DataFrame(states),ready_factors=pd.DataFrame(features),
        calendar=days,actions=actions,hazards={},input_identity='a'*64,pool_identity='b'*64,factor_identity='c'*64,
        calendar_identity='d'*64,contract_identity=stable_hash(contract()))
    result=_run_account(bundle,('SYNTHETIC_WINDOW',False),None,contract())
    buys=[f for f in result['fills'] if f['side']=='BUY']
    sells=[f for f in result['fills'] if f['side']=='SELL']
    assert len(buys)==len(sells)==3
    assert buys[0]['fill_time'].strftime('%Y%m%d')=='20250901'
    assert int(sells[0]['fill_time'].strftime('%Y%m%d'))==days[days.index(20250829)+22]
    assert all(int(s['timestamp'].strftime('%Y%m%d'))>=20250801 for s in result['daily_account'])
    assert result['metrics']['total_fees']>0


def test_window_budget_uses_original_and_does_not_allow_repeat(tmp_path):
    from test_windowed_actions_and_train_grant import grant
    from chanlun_trader.research_factory.monthly_window_governance_v1 import MonthlyWindowGovernanceV1
    _,plan,source,evidence,parent=grant(tmp_path)
    service=MonthlyWindowGovernanceV1(tmp_path)
    plan.update(contracts={'fixed':contract()},result_type=contract()['result_type'])
    evidence.update(feasibility_passed=True,source_candidate_hash=contract()['source_candidate_contract_hash'])
    source.update(approval_record_sha256='SYNTHETIC',window_release_sha256='SYNTHETIC',window_gate_approval_sha256='SYNTHETIC')
    before=parent.summary()
    service.confirm(plan,source,preflight=lambda:evidence)
    reservation=service.reserve('fixed')
    service.start_exposure(reservation['execution_id'])
    service.settle(reservation['execution_id'],1,True)
    assert service.summary()['MAIN_BACKTEST_EXPOSURES_USED']==1
    assert service.summary()['REPAIR_BACKTEST_EXPOSURES_USED']==0
    assert service.reserve('fixed')['status']=='ALREADY_ATTEMPTED'
    assert parent.summary()==before
    service.revoke('SYNTHETIC_STOP')
    with pytest.raises(PermissionError):service.active()


def test_provider_unknown_states_are_not_fabricated():
    import sys
    from pathlib import Path
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
    from prepare_monthly_window_v1 import state_rows
    rows=state_rows('sh.600000',{'ipoDate':'2025-08-04','outDate':''},[],
                    {20250801:{},20250804:{'sh.600000':'1'}},[20250801,20250804])
    assert rows[0]['listed'] is False and rows[0]['st_status']=='UNKNOWN'
    assert rows[1]['eligibility_status']=='CONFLICT' and rows[1]['suspension_status']=='UNKNOWN'


def test_synthetic_source_to_materialized_window_and_feasibility(tmp_path,monkeypatch):
    import sys
    from pathlib import Path
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
    import prepare_monthly_window_v1 as mod
    from chanlun_trader import synthetic_batch_resources
    from chanlun_trader.research_factory.exploration_governance import immutable
    monkeypatch.setattr(mod,'ROOT',tmp_path)
    monkeypatch.setattr(mod,'check',lambda:{'thread_id':'SYNTHETIC','identity':'SYNTHETIC'})
    monkeypatch.setattr(synthetic_batch_resources,'worker_resource_handshake',lambda:{'execution':{'stage':'prepare-window'}})
    days=pd.bdate_range('2025-07-03','2026-07-31').strftime('%Y-%m-%d').tolist()
    codes=['sh.600000','sh.600001','sh.600002']
    immutable(tmp_path/'CALENDAR_WINDOW.json',{'sessions':days})
    def archived(path,rows):
        immutable(path,{'rows':rows,'error_code':'0'})
        immutable(path.with_suffix('.access.json'),{'sha256':mod.sha(path)})
    basic=tmp_path/'acquisition/basic.json'
    archived(basic,[{'code':code,'ipoDate':'1999-01-01','outDate':''} for code in codes])
    pools={}
    for day in days:
        p=tmp_path/'acquisition/pool'/f'{day}.json'
        archived(p,[{'code':code,'tradeStatus':'1'} for code in codes])
        pools[day]=mod.sha(p)
    immutable(tmp_path/'WINDOW_UNIVERSE.json',{'codes':codes,'count':3,'basic_sha256':mod.sha(basic),'pool_hashes':pools})
    for code in codes:
        raw=[];hfq=[]
        for i,day in enumerate(days):
            price=10-i*.001
            raw.append(dict(code=code,date=day,adjustflag='3',open=str(price),high=str(price+.02),low=str(price-.02),
                close=str(price),preclose=str(price+.001),volume='1000000',amount=str(price*1000000),tradestatus='1',isST='0'))
            hfq.append(dict(code=code,date=day,adjustflag='1',close=str(price*2)))
        for flag,rows in [('3',raw),('1',hfq)]:
            path=tmp_path/'responses'/f'probe-{flag}.json' if code=='sh.600000' else tmp_path/'acquisition/prices'/code/f'{flag}.json'
            archived(path,rows)
        archived(tmp_path/'acquisition/actions'/f'{code}.json',[])
    mod.prepare()
    ready=mod.read(tmp_path/'READY.json')
    assert ready['feasibility_passed']
    bundle=mod.load_bundle()
    assert bundle.daily.adjustflag.eq('3').all()
    assert bundle.ready_factors.timestamp.between(20250801,20260731).all()
    assert bundle.states.shape[0]==len(days)*3
    assert mod.read(tmp_path/'FEASIBILITY.json')['thresholds']['entry_dates']==10
    assert not mod.read(tmp_path/'FEASIBILITY.json')['original_train_verdict']['passed']
