"""正式评审服务的合成状态测试；mock不代表真实行情或统计方法验收。"""
from copy import deepcopy
import json

import pandas as pd
import pytest

from chanlun_trader.research_factory import formal_assessment_v1 as formal
from chanlun_trader.research_factory.common import stable_hash


@pytest.fixture
def harness(tmp_path, monkeypatch):
    service = formal.FormalAssessmentServiceV1(tmp_path/'archives')
    records, calls = {}, []
    monkeypatch.setattr(formal, '_now', lambda:'2026-09-26T08:00:00+00:00')
    monkeypatch.setattr(formal, 'source_identity', lambda:'SYNTHETIC_SOURCE_IDENTITY')
    monkeypatch.setattr(formal, 'load_calibration', lambda path:{'method_approved':False,'profile':'SYNTHETIC_TEST'})
    monkeypatch.setattr(formal, 'CALIBRATION_HASH', stable_hash(formal.load_calibration(None)))
    monkeypatch.setattr(service.archive, 'load', lambda key:deepcopy(records[key]))
    monkeypatch.setattr(service.archive, 'admission', lambda key, purpose:{'allowed':True,'strategy_qualified':False})
    dates = [int(d.strftime('%Y%m%d')) for d in pd.bdate_range('2026-09-28',periods=564)]
    window = {'symbols':['000001.SZ','600000.SH'],'feature_start':dates[0],
              'account_start':dates[60],'account_end':dates[-1],'calendar':dates}
    prepared = {'bundle':{'synthetic_fixture':True},'window':window,
                'evidence':{'profile':'SYNTHETIC','qualification_evidence_eligible':False}}
    monkeypatch.setattr(formal,'build_confirmation_bundle',lambda **kwargs:deepcopy(prepared))
    monkeypatch.setattr(formal,'window_input_identity',lambda bundle, window:stable_hash([bundle,window]))
    def account_plan(proposal, *, strategy_id, window, costs):
        return {'strategy_id':strategy_id,'proposal':proposal,'window_hash':stable_hash(window),'costs':costs}
    monkeypatch.setattr(formal,'prepare_formal_account',account_plan)
    def account(proposal, *, strategy_id, bundle, window, costs, input_identity, active_check):
        receipt = active_check()
        calls.append(strategy_id)
        assert receipt['execution_consumed'] is True
        return {'strategy_plan':account_plan(proposal,strategy_id=strategy_id,window=window,costs=costs),
            'input_identity':input_identity,'status':'RECONCILED_DIAGNOSTIC',
            'daily_returns':[{'date':day,'net_return':.001 if proposal else 0.} for day in dates[60:]],
            'metrics':{'net_return':.5,'max_drawdown':-.1}}
    monkeypatch.setattr(formal,'run_formal_account',account)
    if hasattr(formal, 'validate_formal_result'):
        monkeypatch.setattr(formal,'validate_formal_result',lambda *args, **kwargs:None)
    monkeypatch.setattr(formal,'family_test',lambda excess, alpha:{
        'supported':{key:False for key in excess},'adjusted_p':{key:1. for key in excess}})
    def family(index=1, members=1):
        origin = tmp_path/f'origin-{index}'
        ids = []
        for i in range(1,members+1):
            candidate = f'CANDIDATE_{i:03d}'
            root = origin/candidate
            root.mkdir(parents=True)
            (root/'RESULT.json').write_text('{}',encoding='utf-8')
            (root/'DECISION.json').write_text('{}',encoding='utf-8')
            sid = f'SYNTHETIC_{index}_{i}'
            records[sid] = {'strategy_id':sid,'archive_hash':stable_hash(sid),'rule_identity':stable_hash(['rule',sid]),
                'proposal':{'synthetic':sid},'source_profile':'SYNTHETIC',
                'origin':{'scope_id':f'scope-{index}','source_root':str(origin),'candidate_id':candidate},
                'evidence':{'session':{'scope_id':f'scope-{index}','max_attempts':members}}}
            ids.append(sid)
        return ids
    def register(ids, **updates):
        return service.register(**{'strategy_ids':ids,'symbols':window['symbols'],'not_before':20260928,
            'calibration_path':tmp_path/'mock-calibration.json','profile':'SYNTHETIC',**updates})
    def run(batch):
        return service.run(batch,snapshot_root=service.path(batch,'snapshots'),snapshot_ids=['SYNTHETIC_CLOSE'],
            open_snapshot_ids=['SYNTHETIC_OPEN'],calendar_root=service.path(batch,'calendar'),calendar_id='SYNTHETIC_CALENDAR')
    return service, family, register, run, calls, prepared


def test_synthetic_complete_report_and_repeat_do_not_grant_admission_or_rerun(harness):
    service,family,register,run,calls,_ = harness
    ids = family()
    status = register(ids)
    report = run(status['batch_id'])
    assert report['status']=='ASSESSED' and not report['strategy_qualified']
    assert report['account_budget_consumed']==3
    assert run(status['batch_id'])==report==service.report(status['batch_id'])
    assert len(calls)==3
    decision = service.assessment_for(ids[0])
    assert not decision['strategy_qualified']
    assert 'SYNTHETIC_SOURCE_NOT_QUALIFIED' in decision['reason_codes']
    assert 'FORMAL_STATISTICAL_METHOD_NOT_APPROVED' in decision['reason_codes']
    feedback = service.feedback(status['batch_id'])
    assert feedback == service.feedback(status['batch_id'])
    def assert_qualitative(value):
        assert not isinstance(value,float)
        if isinstance(value,dict):
            assert not {'metrics','adjusted_p','pvalues','net_return'} & value.keys()
            for child in value.values(): assert_qualitative(child)
        elif isinstance(value,list):
            for child in value: assert_qualitative(child)
    assert_qualitative(feedback)


def test_pre_freeze_window_unapproved_real_method_and_omitted_family_rejected(harness):
    _,family,register,_,_,_ = harness
    ids = family(members=2)
    with pytest.raises(ValueError,match='FUTURE_START'):
        register(ids,not_before=20260926)
    with pytest.raises(ValueError,match='CALIBRATION_NOT_APPROVED'):
        register(ids,profile='REAL_OBSERVED')
    with pytest.raises(ValueError,match='MEMBER_OMITTED'):
        register(ids[:1])


def test_caller_cannot_replace_failed_calibration_with_approved_claim(harness,monkeypatch):
    service,family,register,_,_,_ = harness
    ids = family()
    monkeypatch.setattr(formal,'load_calibration',lambda path:{'method_approved':True,'profile':'SYNTHETIC_TEST'})
    with pytest.raises(ValueError,match='UNREVIEWED_CALIBRATION'):
        register(ids,profile='REAL_OBSERVED')
    assert service._plans()==[]


def test_failed_exposure_keeps_slot_and_lifetime_budget(harness,monkeypatch):
    service,family,register,run,_,_ = harness
    first = register(family())
    def fail(**kwargs):
        raise ValueError('SYNTHETIC_MISSING_CAPTURE')
    monkeypatch.setattr(formal,'build_confirmation_bundle',fail)
    with pytest.raises(ValueError,match='MISSING_CAPTURE'):
        run(first['batch_id'])
    assert service.path(first['batch_id'],'EXPOSURE_STARTED.json').exists()
    assert formal._read(service.path(first['batch_id'],'FAILED.json'))['budget_refunded'] is False
    assert run(first['batch_id'])['status']=='BLOCKED'
    assert register(family(2))['slot']==2
    assert register(family(3))['slot']==3
    with pytest.raises(ValueError,match='BUDGET_EXHAUSTED'):
        register(family(4))


def test_frozen_capture_root_cannot_be_swapped(harness):
    service,family,register,_,_,_ = harness
    batch = register(family())['batch_id']
    with pytest.raises(ValueError,match='FROZEN_CAPTURE_ROOT'):
        service.run(batch,snapshot_root=service.root/'other',snapshot_ids=[],open_snapshot_ids=[],
                    calendar_root=service.path(batch,'calendar'),calendar_id='unknown')
    assert not service.path(batch,'EXPOSURE_STARTED.json').exists()


def test_crash_after_result_before_settlement_recovers_same_account(harness,monkeypatch):
    service,family,register,run,calls,_ = harness
    batch = register(family())['batch_id']
    put = formal._put
    interrupted = []
    def crash(path,payload):
        if path.name.endswith('_SETTLEMENT.json') and not interrupted:
            interrupted.append(True)
            raise KeyboardInterrupt('simulated power loss')
        return put(path,payload)
    monkeypatch.setattr(formal,'_put',crash)
    with pytest.raises(KeyboardInterrupt):
        run(batch)
    assert service.path(batch,'BENCHMARK_BASE_RESULT.json').exists()
    monkeypatch.setattr(formal,'_put',put)
    assert run(batch)['status']=='ASSESSED'
    assert calls.count('BENCHMARK_BASE')==1 and len(calls)==3


def reseal(path, change):
    data = formal._read(path)
    change(data)
    path.write_text(json.dumps({**data,'_integrity':stable_hash(data)}),encoding='utf-8')


@pytest.mark.parametrize('defect', ['statistics','receipt','inputs','evidence'])
def test_rehashed_derivative_tampering_cannot_change_qualification(harness,defect):
    service,family,register,run,_,_ = harness
    batch = register(family())['batch_id']
    run(batch)
    if defect=='statistics':
        reseal(service.path(batch,'STATISTICS.json'),lambda d:d['supported'].update(CANDIDATE_001=True))
        reseal(service.path(batch,'REPORT.json'),lambda d:d['statistics']['supported'].update(CANDIDATE_001=True))
    elif defect=='receipt':
        reseal(service.path(batch,'CANDIDATE_001_BASE_START.json'),lambda d:d['receipt'].update(execution_consumed=False))
    elif defect=='inputs':
        reseal(service.path(batch,'INPUTS.json'),lambda d:d.update(snapshot_ids=['REPLACED']))
    else:
        reseal(service.path(batch,'DATA_EVIDENCE.json'),lambda d:d['evidence'].update(qualification_evidence_eligible=True))
    with pytest.raises(ValueError,match='CONFLICT|CHANGED'):
        service.report(batch)


def test_adjudication_does_not_confuse_rejection_missing_evidence_or_synthetic(harness):
    service,family,register,run,_,_ = harness
    batch = register(family())['batch_id']
    run(batch)
    plan = service.plan(batch)
    results = {key:formal._read(service.path(batch,key+'_RESULT.json')) for key in
               ('BENCHMARK_BASE','CANDIDATE_001_BASE','CANDIDATE_001_STRESS')}
    stats = {'supported':{'CANDIDATE_001':True},'adjusted_p':{'CANDIDATE_001':.001}}
    actual = formal.adjudicate(plan=plan,evidence={'qualification_evidence_eligible':False},
        results=results,statistics=stats,method_approved=False)['CANDIDATE_001']
    assert actual['decision']=='CONTINUE_RESEARCH' and not actual['strategy_qualified']
    results['CANDIDATE_001_STRESS']['metrics']['net_return']=-.1
    actual = formal.adjudicate(plan=plan,evidence={},results=results,statistics=stats,method_approved=False)['CANDIDATE_001']
    assert actual['decision']=='REJECTED' and 'COST_STRESS_FAILED' in actual['reason_codes']
    results.pop('CANDIDATE_001_BASE')
    actual = formal.adjudicate(plan=plan,evidence={},results=results,statistics=stats,method_approved=False)['CANDIDATE_001']
    assert actual['decision']=='CONTINUE_RESEARCH' and 'COMPLETE_ACCOUNT_EVIDENCE_REQUIRED' in actual['reason_codes']


@pytest.mark.parametrize('method_version', [1, 2])
def test_504_day_synthetic_capture_to_public_account_and_report(harness,monkeypatch,tmp_path,method_version):
    """真实证据组装与公共账户执行；档案和校准仍是明确的合成测试边界。"""
    from chanlun_trader.research_factory import formal_account_backend_v1 as account
    from chanlun_trader.research_factory.formal_evidence_v1 import CalendarEvidenceStoreV1, build_confirmation_bundle
    from chanlun_trader.research_factory.forward_snapshot_v1 import SnapshotStoreV1
    service,family,register,_,_,prepared = harness
    ids = family()
    original_load = service.archive.load
    def load(key):
        record = original_load(key)
        record['proposal'] = {'hypothesis':'合成趋势','indicators':['MACD'],'threshold':1,'change_reason':'合成流程验证'}
        return record
    monkeypatch.setattr(service.archive,'load',load)
    for name in ('window_input_identity','prepare_formal_account','run_formal_account','validate_formal_result'):
        if hasattr(account,name):
            monkeypatch.setattr(formal,name,getattr(account,name))
    monkeypatch.setattr(formal,'build_confirmation_bundle',build_confirmation_bundle)
    updates = {}
    if method_version == 2:
        updates['calibration_path'] = synthetic_v2_calibration(monkeypatch, tmp_path)
    batch = register(ids, **updates)['batch_id']
    store = SnapshotStoreV1(service.path(batch,'snapshots'))
    days = prepared['window']['calendar']
    close_ids, open_ids = [], []
    for i, day in enumerate(days):
        stamp = str(pd.Timestamp(str(day)).date())
        price, previous = 10 + i*.01, 10 + max(i-1,0)*.01
        bars = [dict(symbol=symbol,date=day,open=price,high=price,low=price,close=price,
            prev_close=previous,volume=1_000_000.,amount=price*1_000_000,
            price_basis='RAW_CLOSE',amount_unit='CNY') for symbol in prepared['window']['symbols']]
        payload = {'bars':bars,
            'turn':[dict(symbol=symbol,date=day,turn=1.,tradestatus=1) for symbol in prepared['window']['symbols']],
            'states':[dict(symbol=symbol,date=day,listed=True,delisted=False,is_st=False,board='MAIN',suspended=False)
                      for symbol in prepared['window']['symbols']],
            'corporate_actions':[],'corporate_actions_complete':True}
        close_ids.append(store.record_synthetic(phase='CLOSE',market_date=day,payload=payload,
            received_at=stamp+'T15:30:00+08:00')['snapshot_id'])
        if i >= 60:
            opened = deepcopy(payload)
            for row in opened['bars']:
                row['price_basis'] = 'OBSERVED_NOW'
            open_ids.append(store.record_synthetic(phase='OPEN',market_date=day,payload=opened,
                received_at=stamp+'T09:31:00+08:00')['snapshot_id'])
    calendar = CalendarEvidenceStoreV1(service.path(batch,'calendar')).record_synthetic(
        start=days[0],end=days[-1],dates=days,received_at=stamp+'T16:00:00+08:00')
    result = service.run(batch,snapshot_root=store.root,snapshot_ids=close_ids,open_snapshot_ids=open_ids,
        calendar_root=service.path(batch,'calendar'),calendar_id=calendar['calendar_id'])
    assert result['status']=='ASSESSED' and result['account_budget_consumed']==3
    assert not result['strategy_qualified']
    for key in ('BENCHMARK_BASE','CANDIDATE_001_BASE','CANDIDATE_001_STRESS'):
        saved = formal._read(service.path(batch,key+'_RESULT.json'))
        assert len(saved['daily_returns'])==504 and saved['chain']['issues']==[]
        assert saved['fills']
    assert service.report(batch)==result
    if method_version == 2:
        assert result['statistics']['method_hash'] == formal.statistics_v2.METHOD_HASH
        assert 'REAL_RETURN_PROCESS_APPLICABILITY_NOT_ESTABLISHED' in result['decisions']['CANDIDATE_001']['reason_codes']


@pytest.mark.parametrize('gate,verdict,reason', [
    ('all','PAPER_ELIGIBLE',None),
    ('net','REJECTED','NONPOSITIVE_NET_RETURN'),
    ('stress','REJECTED','COST_STRESS_FAILED'),
    ('drawdown','REJECTED','DRAWDOWN_LIMIT_EXCEEDED'),
    ('half','REJECTED','SUBPERIOD_EXCESS_NOT_POSITIVE'),
    ('statistics','CONTINUE_RESEARCH','STATISTICAL_SUPPORT_INSUFFICIENT'),
    ('method','CONTINUE_RESEARCH','FORMAL_STATISTICAL_METHOD_NOT_APPROVED'),
    ('evidence','CONTINUE_RESEARCH','INDEPENDENT_REAL_EXECUTION_EVIDENCE_REQUIRED'),
    ('profile','CONTINUE_RESEARCH','SYNTHETIC_SOURCE_NOT_QUALIFIED'),
    ('missing','CONTINUE_RESEARCH','COMPLETE_ACCOUNT_EVIDENCE_REQUIRED'),
])
def test_pure_conditional_adjudicator_gates(gate,verdict,reason):
    """仅验证条件裁决真值表；这些手工对象不具有service资格或方法校准效力。"""
    member = 'CANDIDATE_001'
    plan = {'profile':'REAL_OBSERVED','family':{member:{'strategy_id':'unit-only','source_profile':'REAL_OBSERVED'}}}
    result = {'metrics':{'net_return':.2,'max_drawdown':-.1},
              'daily_returns':[{'net_return':.001} for _ in range(504)]}
    results = {member+'_BASE':deepcopy(result),member+'_STRESS':deepcopy(result),
               'BENCHMARK_BASE':{'daily_returns':[{'net_return':0.} for _ in range(504)]}}
    stats = {'supported':{member:gate!='statistics'},'adjusted_p':{member:.001}}
    if gate=='net': results[member+'_BASE']['metrics']['net_return']=0.
    if gate=='stress': results[member+'_STRESS']['metrics']['net_return']=0.
    if gate=='drawdown': results[member+'_BASE']['metrics']['max_drawdown']=-.251
    if gate=='half': results[member+'_BASE']['daily_returns'][252:]=[{'net_return':0.}]*252
    if gate=='profile': plan['profile']='SYNTHETIC'
    if gate=='missing': results.pop(member+'_BASE')
    decision = formal.adjudicate(plan=plan,evidence={'qualification_evidence_eligible':gate!='evidence'},
        results=results,statistics=stats,method_approved=gate!='method')[member]
    assert decision['decision']==verdict
    assert decision['strategy_qualified']==(gate=='all')
    if reason:
        assert reason in decision['reason_codes']


def test_readiness_reports_failed_method_without_consuming_budget(harness,monkeypatch,tmp_path):
    service,family,_,_,_,_ = harness
    ids = family()
    monkeypatch.setattr(formal,'load_calibration',lambda path:{'method_approved':False,
        'summary':{'conditions':[{'support_domain':True,'passed':False}]}})
    monkeypatch.setattr(formal,'CALIBRATION_HASH',stable_hash(formal.load_calibration(None)))
    monkeypatch.setattr(service.archive,'review',lambda key:{'historical_account_state':'COMPLETE','metrics':{}})
    report = service.readiness(strategy_ids=ids,calibration_path=tmp_path/'synthetic-calibration.json')
    assert report['confirmation_budget_consumed']==0 and service._plans()==[]
    assert report['failed_support_checks']==1 and not report['strategy_qualified']
    assert report['next_action']=='REDESIGN_AND_PREREGISTER_STATISTICAL_METHOD'
    assert 'FORMAL_STATISTICAL_CALIBRATION_FAILED' in report['decisions'][ids[0]]['reason_codes']


def synthetic_v2_calibration(monkeypatch, tmp_path):
    calibration = {'method_hash': formal.statistics_v2.METHOD_HASH, 'method_approved': True,
                   'summary': {'conditions': []}, 'profile': 'SYNTHETIC_TEST_ONLY'}
    path = tmp_path / 'synthetic-v2-calibration.json'
    formal._put(path, calibration)
    monkeypatch.setattr(formal.statistics_v2, 'load_calibration', lambda path, **kwargs: deepcopy(calibration))
    monkeypatch.setattr(formal, 'V2_CALIBRATION_HASH', stable_hash(calibration))
    return path


def test_v2_dispatch_and_cross_version_lifetime_budget(harness, monkeypatch, tmp_path):
    service, family, register, run, _, _ = harness
    first = register(family())
    path = synthetic_v2_calibration(monkeypatch, tmp_path)
    second = register(family(2), calibration_path=path)
    assert (first['slot'], second['slot']) == (1, 2)
    assert service.plan(first['batch_id'])['method_hash'] == formal.METHOD_HASH
    assert service.plan(second['batch_id'])['method_hash'] == formal.statistics_v2.METHOD_HASH
    result = run(second['batch_id'])
    assert result['statistics']['method_hash'] == formal.statistics_v2.METHOD_HASH
    assert result['statistics']['alpha'] == .015 / 2
    assert not result['strategy_qualified']
    assert service.report(second['batch_id']) == result
    assert register(family(3))['slot'] == 3
    with pytest.raises(ValueError, match='BUDGET_EXHAUSTED'):
        register(family(4), calibration_path=path)


def test_unknown_method_and_unpinned_v2_fail_before_registration(harness, monkeypatch, tmp_path):
    service, family, register, _, _, _ = harness
    ids = family()
    path = tmp_path / 'unknown.json'
    formal._put(path, {'method_hash': 'unknown'})
    with pytest.raises(ValueError, match='UNKNOWN_METHOD'):
        register(ids, calibration_path=path)
    path = synthetic_v2_calibration(monkeypatch, tmp_path)
    monkeypatch.setattr(formal, 'V2_CALIBRATION_HASH', None)
    with pytest.raises(ValueError, match='UNREVIEWED_CALIBRATION'):
        register(ids, calibration_path=path)
    assert service._plans() == []


def test_v2_loader_cannot_return_other_method_or_forged_approval(harness, monkeypatch, tmp_path):
    _, family, register, _, _, _ = harness
    ids = family()
    path = synthetic_v2_calibration(monkeypatch, tmp_path)
    monkeypatch.setattr(formal.statistics_v2, 'load_calibration', lambda path, **kwargs: {
        'method_hash': formal.METHOD_HASH, 'method_approved': True})
    with pytest.raises(ValueError, match='UNREVIEWED_CALIBRATION'):
        register(ids, calibration_path=path)


def test_calibration_success_does_not_mean_real_strategy_qualification(harness, monkeypatch, tmp_path):
    service, family, _, _, _, _ = harness
    ids = family()
    path = synthetic_v2_calibration(monkeypatch, tmp_path)
    monkeypatch.setattr(service.archive, 'review', lambda key: {
        'historical_account_state': 'COMPLETE', 'metrics': {}})
    report = service.readiness(strategy_ids=ids, calibration_path=path)
    assert report['method_approved'] and not report['strategy_qualified']
    assert report['method_hash'] == formal.statistics_v2.METHOD_HASH
    assert report['real_process_applicability'] == 'NOT_ESTABLISHED'
    assert report['confirmation_budget_consumed'] == 0 and service._plans() == []


def test_v2_synthetic_calibration_cannot_open_paper_even_with_positive_statistics():
    member = 'CANDIDATE_001'
    plan = {'method_hash': formal.statistics_v2.METHOD_HASH, 'profile': 'REAL_OBSERVED',
            'family': {member: {'strategy_id': 'unit-only', 'source_profile': 'REAL_OBSERVED'}}}
    profitable = {'metrics': {'net_return': .2, 'max_drawdown': -.1},
                  'daily_returns': [{'net_return': .001}] * 504}
    decision = formal.adjudicate(plan=plan, evidence={'qualification_evidence_eligible': True},
        results={member + '_BASE': profitable, member + '_STRESS': profitable,
                 'BENCHMARK_BASE': {'daily_returns': [{'net_return': 0.}] * 504}},
        statistics={'supported': {member: True}, 'adjusted_p': {member: .0001}},
        method_approved=True)[member]
    assert not decision['strategy_qualified']
    assert decision['reason_codes'] == ['REAL_RETURN_PROCESS_APPLICABILITY_NOT_ESTABLISHED']


def test_version_cannot_silently_reinterpret_frozen_plan(harness, monkeypatch, tmp_path):
    service, family, register, _, _, _ = harness
    path = synthetic_v2_calibration(monkeypatch, tmp_path)
    batch = register(family(), calibration_path=path)['batch_id']
    # 审核身份变化不能让已冻结计划悄悄使用另一份批准报告。
    monkeypatch.setattr(formal, 'V2_CALIBRATION_HASH', formal.CALIBRATION_HASH)
    with pytest.raises(ValueError, match='CALIBRATION_CHANGED'):
        service.plan(batch)
