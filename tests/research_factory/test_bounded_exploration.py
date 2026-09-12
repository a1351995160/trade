"""只用自包含合成价格，验证已批准探索的计算和记账边界。"""
from datetime import datetime, timedelta, timezone
import json

import pandas as pd
import pytest

from chanlun_trader.research_factory.budget import SearchBudgetRegistryV1, BudgetExhaustedError
from chanlun_trader.research_factory.context import PerformanceBlindGuard
from chanlun_trader.research_factory.exploration_governance import ExplorationGovernanceServiceV1, read_json
from chanlun_trader.research_factory.exploration_relation import calculate
from scripts import run_bounded_exploration as entry


def prices(n=65):
    return pd.DataFrame([{'date':t,'symbol':s,'close':100+t*direction,
                          'high':101+t*direction,'low':99+t*direction}
                         for t in range(n) for s,direction in [('up',1),('down',-1)]])


@pytest.mark.parametrize('protocol,start', [('A',5),('B',19)])
@pytest.mark.parametrize('placebo', [False,True])
def test_exact_formula_calendar_warmup_and_tail(protocol,start,placebo):
    rows=calculate(prices(),list(range(65)),protocol,placebo,3)
    first=start+(20 if placebo else 0)
    assert len(rows)==65
    assert all(r['mean_difference'] is None for r in rows[:first])
    row=rows[first]
    assert row['group1_count']==row['group2_count']==1
    assert row['not_computable_count']==1
    assert row['absent_source_members']==1
    up=3/(100+first); down=-3/(100-first)
    assert row['mean_difference']==pytest.approx(down-up if protocol=='A' else up-down)
    assert all(r['mean_difference'] is None and 'LABEL_BEYOND_TRAIN' in r['reasons'] for r in rows[-3:])


@pytest.mark.parametrize('protocol,session', [('A',10),('B',22)])
def test_calendar_gap_not_compressed_and_placebo_is_past(protocol,session):
    frame=prices().query('not (symbol == "down" and date == @session)')
    rows=calculate(frame,list(range(65)),protocol,True,2)
    assert rows[session+20]['status']=='NOT_COMPUTABLE'
    assert rows[session+20]['history_not_computable_count']>=1
    assert rows[session+20]['mean_difference'] is None


def test_label_gap_is_not_filled():
    frame=prices().query('not (symbol == "down" and date == 12)')
    row=calculate(frame,list(range(65)),'A',False,2)[10]
    assert row['label_not_computable_count']==1
    assert row['mean_difference'] is None


def test_zero_range_empty_group_and_illegal_prices():
    frame=prices().astype({'close':float,'high':float,'low':float})
    frame.loc[frame.symbol=='down',['close','high','low']]=50
    rows=calculate(frame,list(range(65)),'B',False,2)
    assert rows[25]['zero_range_count']==1
    assert rows[25]['group2_mean'] is None
    assert rows[25]['mean_difference'] is None
    frame.loc[(frame.symbol=='up') & (frame.date==25),'close']=float('inf')
    row=calculate(frame,list(range(65)),'B',False,2)[25]
    assert row['invalid_price_count']==1
    assert row['not_computable_count']==2
    json.dumps(row,allow_nan=False)


@pytest.mark.parametrize('case,error',[('duplicate','DUPLICATE'),('calendar','CALENDAR_INVALID'),('outside','DATE_OUTSIDE'),('universe','UNIVERSE')])
def test_input_rejections(case,error):
    frame=prices(); calendar=list(range(65)); universe=2
    if case=='duplicate': frame=pd.concat([frame,frame.iloc[:1]])
    if case=='calendar': calendar=calendar[::-1]
    if case=='outside': calendar=calendar[:-1]
    if case=='universe': universe=1
    with pytest.raises(ValueError,match=error): calculate(frame,calendar,'A',False,universe)


def service(tmp_path):
    svc=ExplorationGovernanceServiceV1(tmp_path/'exploration')
    plan={'objective_id':'old-objective','contracts':entry.contracts(),'limit':6,'wall_limit':5400,
          'result_type':'EXPLORATORY_RAW_PRICE_RELATION',
          'expires_at':(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()}
    source={'origin':'USER_EXPLICIT_PLAN_APPROVAL_VIA_CODEX','thread_id':'synthetic-test',
            'attachment_sha256':'test-only','approval_statement':'SYNTHETIC_TEST_ONLY'}
    receipt=svc.confirm(plan,source)
    return svc,receipt


def test_confirmation_is_idempotent_and_bound(tmp_path):
    svc,receipt=service(tmp_path)
    before=svc.receipt_path.read_bytes()
    assert svc.confirm(receipt['plan'],receipt['source'])==receipt
    assert svc.receipt_path.read_bytes()==before
    with pytest.raises(PermissionError,match='EXPLICIT_APPROVAL'): svc.confirm(receipt['plan'],{})
    with pytest.raises(ValueError,match='CONFIRMATION_CONFLICT'):
        svc.confirm({**receipt['plan'],'objective_id':'other'},receipt['source'])


def test_increment_preserves_old_budget_and_has_four_plus_two_slots(tmp_path):
    old=SearchBudgetRegistryV1('old-objective',tmp_path/'old.json')
    old.register_objective(12)
    old.consume(old.reserve('objective','old-objective',12))
    previous=(tmp_path/'old.json').read_bytes()
    svc,receipt=service(tmp_path)
    for name in receipt['plan']['contracts']:
        attempt=svc.reserve(name); eid=attempt['execution_id']
        svc.start_exposure(eid); svc.settle(eid,1,True)
        assert svc.reserve(name)['status']=='ALREADY_ATTEMPTED'
    for number in range(2):
        red=tmp_path/f'red{number}'; red.write_text('synthetic failure')
        green=tmp_path/f'green{number}'; green.write_text('synthetic regression')
        repair={'fix_commit':str(number)*40,'red_evidence':str(red),'green_evidence':str(green),'affected_contract':'A_main'}
        attempt=svc.reserve('A_main',repair)
        svc.start_exposure(attempt['execution_id']); svc.settle(attempt['execution_id'],1,True)
    repair['fix_commit']='a'*40
    with pytest.raises(BudgetExhaustedError): svc.reserve('A_main',repair)
    assert svc.summary()['exposures_charged']==6
    assert svc.summary()['repair_exposures_used']==2
    assert (tmp_path/'old.json').read_bytes()==previous
    assert old.used('objective','old-objective')==12
    PerformanceBlindGuard.assert_blind(svc.summary())


@pytest.mark.parametrize('started',[False,True])
def test_failure_settlement_and_no_free_repeat(tmp_path,started):
    svc,_=service(tmp_path)
    attempt=svc.reserve('A_main'); eid=attempt['execution_id']
    if started: svc.start_exposure(eid)
    svc.settle(eid,2,False); before=svc.journal.read_bytes()
    svc.settle(eid,999,True)
    assert svc.journal.read_bytes()==before
    assert svc.summary()['exposures_charged']==int(started)
    assert svc.reserve('A_main')['status']=='ALREADY_ATTEMPTED'


def test_unsettled_attempt_blocks_concurrent_start(tmp_path):
    svc,_=service(tmp_path)
    svc.reserve('A_main')
    with pytest.raises(PermissionError,match='UNSETTLED'): svc.reserve('B_main')


@pytest.mark.parametrize('action',['revoke','expire','corrupt'])
def test_revocation_expiry_integrity(tmp_path,action,monkeypatch):
    svc,receipt=service(tmp_path)
    if action=='revoke': svc.revoke('test')
    elif action=='expire':
        class FutureClock:
            @staticmethod
            def now(tz): return datetime.now(tz)+timedelta(days=2)
            fromisoformat=staticmethod(datetime.fromisoformat)
        monkeypatch.setattr('chanlun_trader.research_factory.exploration_governance.datetime',FutureClock)
    else:
        receipt['plan']['limit']=600
        svc.receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(PermissionError): svc.reserve('A_main')


def test_time_exhaustion_and_repair_evidence_required(tmp_path):
    svc,_=service(tmp_path)
    with pytest.raises(PermissionError,match='CONFIRMED_REPAIR'): svc.reserve('A_main',{'bad':True})
    attempt=svc.reserve('A_main'); svc.start_exposure(attempt['execution_id'])
    svc.settle(attempt['execution_id'],5400,False)
    with pytest.raises(PermissionError,match='TIME_EXHAUSTED'): svc.reserve('B_main')


def test_live_revocation_stops_calculation():
    def revoked(): raise PermissionError('REVOKED')
    with pytest.raises(PermissionError,match='REVOKED'):
        calculate(prices(),list(range(65)),'A',False,2,revoked)


def test_real_entry_rejects_test_mode_before_read_or_reservation(monkeypatch):
    monkeypatch.setenv('CHANLUN_TEST_ISOLATION','1')
    monkeypatch.setattr(entry,'prepare',lambda: pytest.fail('must not read real inputs'))
    with pytest.raises(PermissionError,match='CANNOT_USE_TEST_ISOLATION'): entry.execute()


def test_path_redirect_rejected(tmp_path,monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(Path,'resolve',lambda self: tmp_path/'other')
    with pytest.raises(ValueError,match='PATH_REDIRECTED'): entry.plain_path(tmp_path/'input')


def test_frozen_placebo_is_distinct_and_no_result_keys_in_contract():
    fixed=entry.contracts()
    assert len({c['contract_hash'] for c in fixed.values()})==4
    assert fixed['A_placebo']['signal_lag_sessions']==20
    PerformanceBlindGuard.assert_blind(fixed)


@pytest.mark.parametrize('case',['valid','field','window','member','duplicate'])
def test_snapshot_mapping_and_approved_identity(case):
    frame=prices()
    plan={'rows':len(frame),'observed_sources':2,'calendar':list(range(65)),
          'observed_symbol_set_hash':entry.stable_hash(['down','up'])}
    if case=='field': frame=frame.drop(columns='high')
    if case=='window': frame.loc[0,'date']=99
    if case=='member': frame.loc[frame.symbol=='up','symbol']='other'
    if case=='duplicate': frame.iloc[0]=frame.iloc[2]
    if case=='valid': entry.validate_frame(frame,plan)
    else:
        with pytest.raises(ValueError): entry.validate_frame(frame,plan)
