import pandas as pd
import pytest

from test_train_account_runner_v1 import fixture
from chanlun_trader.research_factory.degraded_execution_v2 import run_degraded_account, CONTRACT
from chanlun_trader.research_factory.degraded_governance_v1 import DegradedGovernanceV1
from test_windowed_actions_and_train_grant import grant


def degraded():
    b=fixture();b.hazards={}
    b.ready_factors=b.factors.copy()
    dates=b.calendar
    mapping={d:pd.Timestamp(str(dates[i+1])+' 09:30',tz='Asia/Shanghai') for i,d in enumerate(dates[:-1])}
    b.ready_factors=b.ready_factors[b.ready_factors.timestamp.isin(mapping)]
    b.ready_factors['effective_available_at']=b.ready_factors.timestamp.map(mapping)
    return b


def test_next_open_visibility_entry_plus_three_and_original_accounting():
    r=run_degraded_account(degraded(),('SYNTHETIC',False))
    buys=[f for f in r['fills'] if f['side']=='BUY'];sells=[f for f in r['fills'] if f['side']=='SELL']
    assert len(buys)>=1 and len(sells)>=1
    assert buys[0]['fill_time'].strftime('%Y%m%d')=='20220802'
    assert sells[0]['fill_time'].strftime('%Y%m%d')=='20220808', (r['exit_decisions'],r['orders'])
    assert r['signals'][0]['generated_at']==buys[0]['fill_time']
    assert r['metrics']['total_fees']>0 and r['metrics']['total_stamp_tax']>0
    assert r['status']=='COMPLETE'
    assert all(x['accepted_qty']<=x['H1_cap'] and x['accepted_qty']<=x['H2_cap'] for x in r['capacity_decisions'])


def test_hazard_entry_rejection_and_unexpected_extended_holding_partial():
    b=degraded();b.hazards={s:[20220803] for s in b.daily.symbol.unique()}
    r=run_degraded_account(b,('SYNTHETIC',False))
    assert not r['fills']
    assert any(x['reason']=='ENTRY_REJECT_CORPORATE_ACTION_UNSUPPORTED' for x in r['candidate_rejections'])
    b=degraded();b.hazards={s:[20220809] for s in b.daily.symbol.unique()}
    b.states.loc[b.states.trade_date=='20220808','suspension_status']='SUSPENDED'
    r=run_degraded_account(b,('SYNTHETIC',False))
    assert r['status']=='PARTIAL_UNSUPPORTED_EVENT' and r['metrics'] is None
    assert r['unsupported_lots']


def test_late_input_no_orders_and_real_without_receipt_rejected():
    b=degraded();b.ready_factors['effective_available_at']=pd.Timestamp('2024-07-31 16:00',tz='Asia/Shanghai')
    assert not run_degraded_account(b,('SYNTHETIC',False))['fills']
    with pytest.raises(PermissionError):run_degraded_account(b,('ACTUAL',False))


def test_degraded_grant_uses_original_registry_and_no_free_repeat(tmp_path):
    old,p,source,e,parent=grant(tmp_path)
    s=DegradedGovernanceV1(tmp_path)
    p['contracts']={'fixed':CONTRACT};p['result_type']=CONTRACT['result_type']
    source['approval_record_sha256']=source.pop('attachment_sha256')
    e['feasibility_passed']=True
    receipt=s.confirm(p,source,preflight=lambda:e)
    assert s.budget_path==parent.budget_path
    main=s.reserve('fixed');s.start_exposure(main['execution_id']);s.settle(main['execution_id'],1,True)
    assert s.summary()['MAIN_BACKTEST_EXPOSURES_USED']==1
    assert s.summary()['REPAIR_BACKTEST_EXPOSURES_USED']==0
    assert s.reserve('fixed')['status']=='ALREADY_ATTEMPTED'
    s.revoke('SYNTHETIC_STOP')
    with pytest.raises(PermissionError):s.active()


def test_failed_feasibility_does_not_register_increment(tmp_path):
    old,p,source,e,parent=grant(tmp_path)
    s=DegradedGovernanceV1(tmp_path)
    p['contracts']={'fixed':CONTRACT};p['result_type']=CONTRACT['result_type']
    source['approval_record_sha256']=source.pop('attachment_sha256');e['feasibility_passed']=False
    before=s.budget_path.read_bytes()
    with pytest.raises(PermissionError):s.confirm(p,source,preflight=lambda:e)
    assert s.budget_path.read_bytes()==before and not s.receipt_path.exists()


def test_no_outcome_closure_checks_capacity_both_sides_and_hazards(monkeypatch):
    from chanlun_trader.research_factory import degraded_input_v2 as mod
    b=degraded();b.input_identity='SYNTHETIC';b.coverage=[];b.access=[]
    row={'symbol':'600000.SH','signal_session':20220801,'rank':1,'entry_date':20220802,'exit_date':20220808}
    monkeypatch.setattr(mod,'read',lambda path:[row])
    assert mod.check_feasibility(b)['paths'][0]['reason']=='COMPLETE_CLOSURE_PATH'
    b.hazards={'600000.SH':[20220808]}
    assert mod.check_feasibility(b)['paths'][0]['reason']=='ENTRY_REJECT_CORPORATE_ACTION_UNSUPPORTED'
    b.hazards={}
    b.daily.loc[(b.daily.symbol=='600000.SH') & (b.daily.date==20220805),'volume']=10
    assert mod.check_feasibility(b)['paths'][0]['reason']=='EXIT_CAPACITY_NO_COMPLETE_CLOSE'


def test_degraded_expiry_and_repair_proof_remain_required(tmp_path):
    from datetime import datetime,timezone,timedelta
    old,p,source,e,parent=grant(tmp_path)
    s=DegradedGovernanceV1(tmp_path)
    p['contracts']={'fixed':CONTRACT};p['result_type']=CONTRACT['result_type']
    source['approval_record_sha256']=source.pop('attachment_sha256');e['feasibility_passed']=True
    p['expires_at']=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
    with pytest.raises(PermissionError,match='EXPIRED'):s.confirm(p,source,preflight=lambda:e)


def test_friday_entry_sellability_projects_to_next_real_session():
    b=degraded()
    for day in [20220811,20220812]:
        d=b.daily[b.daily.date==20220810].copy();d['date']=day
        b.daily=pd.concat([b.daily,d],ignore_index=True)
        s=b.states[b.states.trade_date=='20220810'].copy();s['trade_date']=str(day)
        b.states=pd.concat([b.states,s],ignore_index=True)
        b.calendar.append(day)
    b.ready_factors=b.ready_factors[b.ready_factors.timestamp==20220804]
    r=run_degraded_account(b,('SYNTHETIC',False))
    assert r['status']=='COMPLETE'
    buys=[f for f in r['fills'] if f['side']=='BUY'];sells=[f for f in r['fills'] if f['side']=='SELL']
    assert buys[0]['fill_time'].strftime('%Y%m%d')=='20220805'
    assert sells[0]['fill_time'].strftime('%Y%m%d')=='20220811'
    lots=r['final_account_checkpoint']['state']['lots']
    assert all(pd.Timestamp(lot['sellable_from']).strftime('%Y%m%d')=='20220808' for lot in lots.values())


def test_only_one_proven_repair_is_charged_in_original_registry(tmp_path):
    import json
    old,p,source,e,parent=grant(tmp_path)
    s=DegradedGovernanceV1(tmp_path)
    p['contracts']={'fixed':CONTRACT};p['result_type']=CONTRACT['result_type']
    source['approval_record_sha256']=source.pop('attachment_sha256');e['feasibility_passed']=True
    s.confirm(p,source,preflight=lambda:e)
    red=tmp_path/'red.json';green=tmp_path/'green.json'
    red.write_text(json.dumps({'status':'FAIL','case_id':'weekend','affected_contract':'fixed'}))
    green.write_text(json.dumps({'status':'PASS','case_id':'weekend','fix_commit':'f'*40}))
    proof={'red_evidence':str(red),'green_evidence':str(green),'fix_commit':'f'*40,'affected_contract':'fixed'}
    with pytest.raises(PermissionError,match='NO_PRIOR_EXPOSURE'):s.reserve('fixed',proof)
    main=s.reserve('fixed');s.start_exposure(main['execution_id']);s.settle(main['execution_id'],1,False)
    repair=s.reserve('fixed',proof);s.start_exposure(repair['execution_id']);s.settle(repair['execution_id'],1,True)
    assert s.summary()['MAIN_BACKTEST_EXPOSURES_USED']==1
    assert s.summary()['REPAIR_BACKTEST_EXPOSURES_USED']==1
    assert s.reserve('fixed',proof)['status']=='ALREADY_ATTEMPTED'
