"""本机合成导出与权威预算增量，旧账不迁移。"""
import hashlib
import json
from datetime import datetime,timedelta,timezone

import pytest
from chanlun_trader.data.tdx.windowed_actions_v1 import WindowedCorporateActionDatasetV1
from chanlun_trader.research_factory.budget import SearchBudgetRegistryV1,BudgetExhaustedError
from chanlun_trader.research_factory.exploration_governance import ExplorationGovernanceServiceV1
from chanlun_trader.research_factory.train_execution_governance_v1 import TrainExecutionGovernanceV1
from chanlun_trader.research_factory.train_account_runner_v1 import FIXED_CONTRACT


def bundle(tmp_path,events,**changes):
    data=b'\n'.join(json.dumps(e).encode() for e in events)
    (tmp_path/'events.jsonl').write_bytes(data)
    m=dict(dataset_id='SYNTHETIC',version='WindowedCorporateActionDatasetV1',start=20220722,end=20240731,
        source_identity='OWNER_SYNTHETIC',physical_window_attestation=dict(owner='SYNTHETIC_OWNER',
        window_enforced_before_export=True,not_derived_from_current_incident=True,start=20220722,end=20240731),coverage={'complete':True,'symbols':['600000.SH']},
        events_file='events.jsonl',events_sha256=hashlib.sha256(data).hexdigest())
    m.update(changes); raw=json.dumps(m).encode();p=tmp_path/'manifest.json';p.write_bytes(raw)
    return p,hashlib.sha256(raw).hexdigest()


def event():
    return dict(event_id='1',symbol='600000.SH',effective_date=20220803,event_type='SPLIT',
        terms={'ratio_numerator':2,'ratio_denominator':1},units='NEW_SHARES_PER_OLD_SHARE',source='SYNTHETIC',source_published_at=None)


def test_windowed_import_keeps_unknown_and_attested_empty(tmp_path):
    p,h=bundle(tmp_path,[event()]);d=WindowedCorporateActionDatasetV1.load(p,h,{'600000.SH'})
    assert d.events[0]['source_published_at'] is None
    p,h=bundle(tmp_path,[]);assert WindowedCorporateActionDatasetV1.load(p,h,{'600000.SH'}).events==()


@pytest.mark.parametrize('mode',['outside','duplicate','conflict','unknown','coverage','attestation','hash'])
def test_windowed_import_rejects_conflicts(tmp_path,mode):
    events=[event()];change={}
    if mode=='outside':events[0]['effective_date']=20250801
    if mode=='duplicate':events.append(event())
    if mode=='conflict':events.append({**event(),'event_id':'2'})
    if mode=='unknown':events[0]['event_type']='UNKNOWN'
    if mode=='coverage':change={'coverage':{'complete':False,'symbols':['600000.SH']}}
    if mode=='attestation':change={'physical_window_attestation':{}}
    p,h=bundle(tmp_path,events,**change)
    with pytest.raises((ValueError,PermissionError)):
        WindowedCorporateActionDatasetV1.load(p,'0'*64 if mode=='hash' else h,{'600000.SH'})


def grant(tmp_path):
    source={'origin':'USER_EXPLICIT_PLAN_APPROVAL_VIA_CODEX','thread_id':'SYNTHETIC','attachment_sha256':'a'*64,'approval_statement':'SYNTHETIC_USER_APPROVAL'}
    expiry=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
    parent=ExplorationGovernanceServiceV1(tmp_path)
    parent.confirm(dict(contracts={str(i):{} for i in range(4)},limit=6,wall_limit=5400,
        result_type='EXPLORATORY_RAW_PRICE_RELATION',expires_at=expiry,objective_id='SYNTHETIC'),source)
    service=TrainExecutionGovernanceV1(tmp_path)
    plan=dict(contracts={'fixed':FIXED_CONTRACT},limit=2,wall_limit=1800,result_type='TRAIN_EXECUTION_BACKTEST_EXPLORATORY',
        expires_at=expiry,objective_id='SYNTHETIC',input_identity='input')
    evidence={'status':'READY','input_identity':'input','novelty_status':'AUTHORIZED_FIXED_REFERENCE_REPRODUCTION'}
    return service,plan,source,evidence,parent


def test_increment_main_repair_and_old_buckets_unchanged(tmp_path):
    s,p,source,e,parent=grant(tmp_path);old=parent.summary()
    receipt=s.confirm(p,source,preflight=lambda:e)
    run=s.reserve('fixed');s.start_exposure(run['execution_id']);s.settle(run['execution_id'],1,True)
    assert s.reserve('fixed')['status']=='ALREADY_ATTEMPTED'
    assert s.summary()['MAIN_BACKTEST_EXPOSURES_USED']==1
    assert parent.summary()==old
    budget=SearchBudgetRegistryV1('SYNTHETIC',s.budget_path)
    repair=budget.reserve_train_execution(receipt['plan_id'],'fixed',repair_id='proven-fix');budget.consume(repair)
    with pytest.raises(BudgetExhaustedError):budget.reserve_train_execution(receipt['plan_id'],'fixed',repair_id='second-fix')
    assert parent.summary()==old


def test_not_ready_has_no_receipt_and_revocation_is_separate(tmp_path):
    s,p,source,e,parent=grant(tmp_path)
    with pytest.raises(PermissionError):s.confirm(p,source,preflight=lambda:{'status':'MISSING'})
    assert not s.receipt_path.exists()
    s.confirm(p,source,preflight=lambda:e);s.revoke('SYNTHETIC_STOP')
    with pytest.raises(PermissionError):s.reserve('fixed')
    assert parent.active()


def test_consumed_before_start_journal_crash_never_becomes_free_replay(tmp_path):
    s,p,source,e,_=grant(tmp_path);s.confirm(p,source,preflight=lambda:e)
    run=s.reserve('fixed')
    budget=SearchBudgetRegistryV1('SYNTHETIC',s.budget_path);budget.consume(run['reservation_id'])
    recovered=TrainExecutionGovernanceV1(tmp_path)
    recovered.settle(run['execution_id'],900,False)
    assert recovered.summary()['MAIN_BACKTEST_EXPOSURES_USED']==1
    assert recovered.events()[-1]['exposure_status']=='POSSIBLE_CHARGED'
    assert recovered.reserve('fixed')['status']=='ALREADY_ATTEMPTED'


def test_expired_or_changed_contract_cannot_issue_grant(tmp_path):
    s,p,source,e,_=grant(tmp_path)
    p['expires_at']=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
    with pytest.raises(PermissionError,match='EXPIRED'):s.confirm(p,source,preflight=lambda:e)
    assert not s.receipt_path.exists()
    p['contracts']={'fixed':{**FIXED_CONTRACT,'top_n':4}}
    with pytest.raises(ValueError,match='PARAMETERS_CHANGED'):s.confirm(p,source,preflight=lambda:e)


def test_unknown_source_cannot_certify_empty_actions(tmp_path):
    p,h=bundle(tmp_path,[],source_identity='UNKNOWN')
    with pytest.raises(ValueError,match='SOURCE_IDENTITY'):WindowedCorporateActionDatasetV1.load(p,h,{'600000.SH'})
