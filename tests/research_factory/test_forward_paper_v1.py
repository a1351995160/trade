"""前瞻Paper阶段与同一账户的合成验收；不伪造真实观察天数。"""
from copy import deepcopy
import json

import pandas as pd
import pytest

from test_bounded_real_research_loop_v1 import FakeInvoker, make_session
from chanlun_trader.research_factory.forward_paper_v1 import ForwardPaperSessionV1
from chanlun_trader.research_factory.forward_snapshot_v1 import SnapshotStoreV1
from chanlun_trader.research_factory.strategy_qualification_v1 import BoundedStrategyArchiveV1


@pytest.fixture(scope='module')
def paper_source(tmp_path_factory):
    session, loader, _ = make_session(tmp_path_factory.mktemp('paper-source'), attempts=2)
    status=session.run(loader=loader,invoker=FakeInvoker())
    assert status['status']=='ATTEMPT_BUDGET_EXHAUSTED'
    return session.root


def paper_case(tmp_path, source, *, members=1):
    archive=BoundedStrategyArchiveV1(tmp_path/'archive')
    entries=[archive.freeze(source,f'CANDIDATE_{i:03d}') for i in range(1,members+1)]
    symbols=['000001.SZ','600000.SH']
    bars,turn=[],[]
    days=pd.bdate_range(end='2024-07-31',periods=80)
    for symbol in symbols:
        previous=10.0
        for i,date in enumerate(days):
            close=10.0+i*0.05
            day=int(date.strftime('%Y%m%d'))
            bars.append({'symbol':symbol,'date':day,'open':close,'high':close+0.1,
                         'low':close-0.1,'close':close,'prev_close':previous,'volume':1_000_000.,'amount':close*1_000_000})
            turn.append({'symbol':symbol,'date':day,'turn':1.0+i*0.01,'tradestatus':1})
            previous=close
    warmup={'bars':bars,'turn':turn,'states':[],'corporate_actions':[],
            'corporate_actions_complete':True,'source_profile':'SYNTHETIC'}
    policy={'initial_cash':100000.,'symbols':symbols,'open_delay_minutes':5,'close_delay_minutes':120,
            'portfolio':{'policy_id':'SYNTHETIC_PAPER','members':[
                {'strategy_id':row['strategy_id'],'rule_identity':row['rule_identity'],
                 'weight_bps':10000//members,'priority':i} for i,row in enumerate(entries)],
                'purpose':'ENGINEERING_OBSERVATION','max_positions':4,
                'max_symbol_exposure_bps':5000,'max_buy_turnover_bps':8000,
                'valid_until':'2024-09-01T00:00:00+08:00'}}
    clock=[pd.Timestamp('2024-08-01T14:00:00+08:00')]
    kwargs={'archive_root':archive.root,'strategy_ids':[row['strategy_id'] for row in entries],
            'policy':policy,'calendar':[20240801,20240802,20240805,20240806],
            'warmup':warmup,'profile':'SYNTHETIC','clock':lambda:clock[0]}
    session=ForwardPaperSessionV1.create(tmp_path/'paper',**kwargs)
    return session,SnapshotStoreV1(tmp_path/'snapshots'),clock,kwargs,archive


def snapshot(store,clock,phase,day,previous,price,*,actions=None,complete=True):
    clock[0]=pd.Timestamp(str(day),tz='Asia/Shanghai')+pd.Timedelta(
        hours=9,minutes=31) if phase=='OPEN' else pd.Timestamp(str(day),tz='Asia/Shanghai')+pd.Timedelta(hours=15,minutes=10)
    payload={'bars':[],'turn':[],'states':[],'corporate_actions':actions or [],'corporate_actions_complete':complete}
    for symbol in ('000001.SZ','600000.SH'):
        payload['bars'].append({'symbol':symbol,'date':day,'open':price,'high':price,
            'low':price,'close':price,'prev_close':previous,'volume':1_000_000.,'amount':price*1_000_000})
        payload['turn'].append({'symbol':symbol,'date':day,'turn':2.0,'tradestatus':1})
        payload['states'].append({'symbol':symbol,'date':day,'listed':True,'delisted':False,
            'is_st':False,'board':'MAIN','suspended':False})
    return store.record_synthetic(phase=phase,market_date=day,payload=payload,received_at=clock[0].isoformat())


def ingest(session,store,value):
    return session.ingest(store.root,value['snapshot_id'])


def test_forward_close_open_close_uses_frozen_plan_and_real_account(tmp_path,paper_source):
    session,store,clock,_,_=paper_case(tmp_path,paper_source,members=2)
    assert session.status()['completed_stages']==0
    close=snapshot(store,clock,'CLOSE',20240801,13.95,14.10)
    initial=ingest(session,store,close)
    assert initial['state']['economic']['trades']==[]
    assert initial['next_plan']['intents']
    assert all(item['target_weight']==0.5 for item in initial['next_plan']['intents'] if item['side']=='BUY')
    assert initial['completed_simulated_days']==0
    assert ingest(session,store,close)==initial
    opened=snapshot(store,clock,'OPEN',20240802,14.10,14.15)
    result=ingest(session,store,opened)
    trades=result['state']['economic']['trades']
    assert trades
    assert all(pd.Timestamp(item['fill_time'])>=pd.Timestamp(opened['received_at']) for item in trades)
    assert set(item['strategy_id'] for item in trades)<=set(session.header()['strategies'])
    assert all(order['metadata']['target_weight']==0.5 for order in result['state']['economic']['orders'].values()
               if order['side']=='BUY')
    closed=snapshot(store,clock,'CLOSE',20240802,14.10,14.20)
    result=ingest(session,store,closed)
    assert result['completed_simulated_days']==1
    assert result['real_observation_days']==result['engineering_observation_days']==result['qualified_observation_days']==0
    assert result['state']['invariant_errors']==[]
    assert result['strategy_qualified'] is False
    assert result['next_plan']['next_session']==20240805
    recreated=ForwardPaperSessionV1(session.root,clock=lambda:clock[0])
    assert recreated.status()==result
    assert ingest(recreated,store,closed)==result
    monday=snapshot(store,clock,'OPEN',20240805,14.20,14.25)
    assert ingest(recreated,store,monday)['completed_stages']==4


@pytest.mark.parametrize('fault',['late','gap','revision','action','unknown_actions','reference_change'])
def test_bad_snapshot_does_not_change_committed_account(tmp_path,paper_source,fault):
    session,store,clock,_,_=paper_case(tmp_path,paper_source)
    first=snapshot(store,clock,'CLOSE',20240801,13.95,14.10)
    ingest(session,store,first)
    before=session.status()
    if fault=='revision':
        value=snapshot(store,clock,'CLOSE',20240801,13.95,14.11)
    elif fault=='gap':
        value=snapshot(store,clock,'OPEN',20240805,14.10,14.15)
    else:
        value=snapshot(store,clock,'OPEN',20240802,13.0 if fault=='reference_change' else 14.10,14.15,
            actions=[{'event_type':'SPLIT'}] if fault=='action' else None,complete=fault!='unknown_actions')
        if fault=='late':clock[0]+=pd.Timedelta(minutes=20)
    with pytest.raises(ValueError):ingest(session,store,value)
    if fault in ('action','unknown_actions'):
        after=session.status()
        assert after['status']=='HALTED'
        assert after['state']==before['state'] and after['completed_stages']==before['completed_stages']
    else:
        assert session.status()==before


def test_no_formal_or_real_promotion_from_synthetic_archive(tmp_path,paper_source):
    session,_,_,kwargs,_=paper_case(tmp_path,paper_source)
    formal=deepcopy({k:v for k,v in kwargs.items() if k!='clock'})
    formal['clock']=kwargs['clock']
    formal['purpose']='FORMAL_OBSERVATION'
    formal['policy']['portfolio']['purpose']='FORMAL_OBSERVATION'
    with pytest.raises(PermissionError,match='NOT_ADMITTED'):
        ForwardPaperSessionV1.create(tmp_path/'formal',**formal)
    real=deepcopy({k:v for k,v in kwargs.items() if k!='clock'})
    real['profile']='REAL_OBSERVED'
    real['warmup']['source_profile']='HISTORICAL_REAL'
    with pytest.raises(PermissionError,match='SYNTHETIC_STRATEGY_NOT_REAL'):
        ForwardPaperSessionV1.create(tmp_path/'real',**real)
    real['clock']=kwargs['clock']
    with pytest.raises(ValueError,match='PROFILE_INVALID'):
        ForwardPaperSessionV1.create(tmp_path/'clock_override',**real)


def test_revocation_and_deleted_committed_stage_are_not_repaired(tmp_path,paper_source):
    session,store,clock,_,_=paper_case(tmp_path,paper_source)
    ingest(session,store,snapshot(store,clock,'CLOSE',20240801,13.95,14.10))
    first=session.revoke('停止观察')
    assert session.revoke('重复停止')==first
    value=snapshot(store,clock,'OPEN',20240802,14.10,14.15)
    with pytest.raises(PermissionError,match='REVOKED'):ingest(session,store,value)
    session.path('REVOKED.json').unlink()
    with pytest.raises(ValueError,match='COMMITTED_HISTORY'):session.status()


def test_stage_or_source_change_blocks_replay(tmp_path,paper_source,monkeypatch):
    session,store,clock,_,_=paper_case(tmp_path,paper_source)
    ingest(session,store,snapshot(store,clock,'CLOSE',20240801,13.95,14.10))
    from chanlun_trader.research_factory import forward_paper_v1 as module
    monkeypatch.setattr(module,'source_identity',lambda:'CHANGED')
    value=snapshot(store,clock,'OPEN',20240802,14.10,14.15)
    with pytest.raises(PermissionError,match='SOURCE_CHANGED'):ingest(session,store,value)
    path=session.path('stages','00000000.json')
    raw=json.loads(path.read_text(encoding='utf-8'))
    raw['state']['equity']=999
    path.write_text(json.dumps(raw),encoding='utf-8')
    with pytest.raises(ValueError,match='CORRUPT'):session.status()


def test_revocation_during_recovery_is_seen_before_open(tmp_path,paper_source,monkeypatch):
    session,store,clock,_,archive=paper_case(tmp_path,paper_source)
    ingest(session,store,snapshot(store,clock,'CLOSE',20240801,13.95,14.10))
    original=session._recover
    sid=next(iter(session.header()['strategies']))
    def restore_then_revoke(*args):
        engine=original(*args)
        archive.revoke(sid,'恢复期间撤销')
        return engine
    monkeypatch.setattr(session,'_recover',restore_then_revoke)
    opened=snapshot(store,clock,'OPEN',20240802,14.10,14.15)
    result=ingest(session,store,opened)
    assert result['state']['economic']['trades']==[]
    from chanlun_trader.research_factory.bounded_research_v1 import _read
    assert _read(session.path('stages','00000001.json'))['admissions'][sid]['allowed'] is False


def test_fill_time_authority_revocation_is_recorded_for_deterministic_recovery(tmp_path,paper_source,monkeypatch):
    session,store,clock,_,archive=paper_case(tmp_path,paper_source)
    ingest(session,store,snapshot(store,clock,'CLOSE',20240801,13.95,14.10))
    sid=next(iter(session.header()['strategies']))
    original=BoundedStrategyArchiveV1.admission
    calls=[0]
    def revoke_at_fill(service,strategy_id,*,purpose):
        calls[0]+=1
        # 1=恢复后的当前准入，2=风险初始化，3/4=两意图数量，5=首笔实际成交前。
        if calls[0]==5:archive.revoke(sid,'实际成交前撤销')
        return original(service,strategy_id,purpose=purpose)
    monkeypatch.setattr(BoundedStrategyArchiveV1,'admission',revoke_at_fill)
    result=ingest(session,store,snapshot(store,clock,'OPEN',20240802,14.10,14.15))
    assert calls[0]>=5 and result['state']['economic']['trades']==[]
    orders=result['state']['economic']['orders']
    assert orders and all(row['status']=='REJECTED' for row in orders.values())
    from chanlun_trader.research_factory.bounded_research_v1 import _read
    trace=_read(session.path('stages','00000001.json'))['admission_trace']
    assert any(item['admission']['allowed'] for item in trace)
    assert any(not item['admission']['allowed'] for item in trace)
    monkeypatch.setattr(BoundedStrategyArchiveV1,'admission',original)
    resumed=ForwardPaperSessionV1(session.root,clock=lambda:clock[0])
    status=ingest(resumed,store,snapshot(store,clock,'CLOSE',20240802,14.10,14.20))
    assert status['state']['economic']['trades']==[]
    assert status['completed_stages']==3


@pytest.mark.parametrize('automatic',[False,True])
def test_new_revocation_commit_can_recover_but_deleted_fact_cannot(tmp_path,paper_source,monkeypatch,automatic):
    session,store,clock,_,_=paper_case(tmp_path,paper_source)
    ingest(session,store,snapshot(store,clock,'CLOSE',20240801,13.95,14.10))
    from chanlun_trader.research_factory import forward_paper_v1 as module
    original=module._atomic_write
    def fail_head(path,value):
        if path.name=='HEAD.json':raise OSError('SIMULATED_REVOCATION_HEAD_INTERRUPTION')
        return original(path,value)
    monkeypatch.setattr(module,'_atomic_write',fail_head)
    with pytest.raises(OSError,match='REVOCATION_HEAD'):
        if automatic:
            ingest(session,store,snapshot(store,clock,'OPEN',20240802,14.10,14.15,complete=False))
        else:
            session.revoke('用户停止')
    monkeypatch.setattr(module,'_atomic_write',original)
    resumed=ForwardPaperSessionV1(session.root,clock=lambda:clock[0])
    status=resumed.revoke('重复停止只恢复原撤销')
    assert status['status']==('HALTED' if automatic else 'REVOKED')
    assert status['completed_stages']==1
    resumed.path('REVOKED.json').unlink()
    with pytest.raises(ValueError,match='COMMITTED_HISTORY'):
        resumed.revoke('不允许恢复删除的撤销')


def test_recovery_crossing_open_deadline_cannot_use_old_processing_time(tmp_path,paper_source,monkeypatch):
    session,store,clock,_,_=paper_case(tmp_path,paper_source)
    ingest(session,store,snapshot(store,clock,'CLOSE',20240801,13.95,14.10))
    original=session._recover
    def slow_restore(*args):
        engine=original(*args)
        clock[0]+=pd.Timedelta(minutes=10)
        return engine
    monkeypatch.setattr(session,'_recover',slow_restore)
    opened=snapshot(store,clock,'OPEN',20240802,14.10,14.15)
    with pytest.raises(ValueError,match='LATE_OR_EARLY'):
        ingest(session,store,opened)
    assert session.status()['completed_stages']==1
    assert session.status()['state']['economic']['trades']==[]


def test_authority_read_error_during_fill_never_commits_incomplete_trace(tmp_path,paper_source,monkeypatch):
    session,store,clock,_,_=paper_case(tmp_path,paper_source)
    ingest(session,store,snapshot(store,clock,'CLOSE',20240801,13.95,14.10))
    original=BoundedStrategyArchiveV1.admission
    calls=[0]
    def fail_at_fill(service,strategy_id,*,purpose):
        calls[0]+=1
        if calls[0]==5:raise ValueError('SIMULATED_AUTHORITY_CORRUPTION')
        return original(service,strategy_id,purpose=purpose)
    monkeypatch.setattr(BoundedStrategyArchiveV1,'admission',fail_at_fill)
    opened=snapshot(store,clock,'OPEN',20240802,14.10,14.15)
    with pytest.raises(PermissionError,match='AUTHORITY_UNAVAILABLE'):
        ingest(session,store,opened)
    assert session.status()['completed_stages']==1
    assert not session.path('stages','00000001.json').exists()
    monkeypatch.setattr(BoundedStrategyArchiveV1,'admission',original)
    result=ingest(session,store,opened)
    assert result['completed_stages']==2 and result['state']['economic']['trades']
