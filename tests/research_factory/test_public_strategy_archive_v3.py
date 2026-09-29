"""公共V3档案与持久Paper三类退出、恢复及合成隔离。"""
from copy import deepcopy
import json
import pandas as pd
import pytest
from chanlun_trader.research_factory.public_strategy_archive_v3 import PublicStrategyArchiveV3, archive_for_ids
from chanlun_trader.research_factory.forward_paper_v1 import ForwardPaperSessionV1
from chanlun_trader.research_factory.forward_snapshot_v1 import SnapshotStoreV1
from chanlun_trader.research_factory.research_rule_strategy_v3 import ResearchRuleStrategyV3
from chanlun_trader.research_factory.rule_account_backend_v2 import RuleAccountBackendV2
from chanlun_trader.research_factory.strategy_interface_v1 import prepare, run
from chanlun_trader.research_factory.strategy_submission_v1 import load_frozen_bundle
from test_research_evidence_v1 import evidence, read, rebind_result


def setup(tmp_path, exits=None):
    jobroot = evidence(tmp_path, exits=exits)
    archive = PublicStrategyArchiveV3(tmp_path/'archive')
    frozen = archive.freeze(jobroot/'JOB.json', 'case')
    key = frozen['strategy_id']
    kwargs = frozen['source_evidence']['JOB.json']['items']['case']['loader_kwargs']
    bundle = load_frozen_bundle(**kwargs)['frame']
    window = frozen['frozen_input']['window']
    strategy = ResearchRuleStrategyV3(frozen['proposal'], strategy_id=key)
    backend = RuleAccountBackendV2(window, initial_cash=50000)
    plan = prepare(strategy, backend)
    identity = frozen['result']['input_identity']
    receipt = {'strategy_plans': {key:plan}, 'input_identity':identity,
               'novelty':{key:{'allowed':True}}, 'execution_purpose':key, 'execution_consumed':True}
    result = run(strategy, backend, frame=bundle, actions=[], input_identity=identity, active_check=lambda:receipt)
    days = [day for day in window['calendar'] if day >= window['account_start']]
    next_day = int((pd.Timestamp(str(days[-1]))+pd.offsets.BDay()).strftime('%Y%m%d'))
    clock = [pd.Timestamp(str(days[0]), tz='Asia/Shanghai')+pd.Timedelta(hours=14)]
    warmup = {'bars':bundle['daily'].loc[bundle['daily'].date < days[0]].to_dict('records'),
              'turn':bundle['turn'].loc[bundle['turn'].date < days[0]].to_dict('records'),
              'states':[], 'corporate_actions':[], 'corporate_actions_complete':True, 'source_profile':'SYNTHETIC'}
    policy = {'initial_cash':50000, 'symbols':window['symbols'], 'open_delay_minutes':5, 'close_delay_minutes':120,
        'portfolio':{'policy_id':'PUBLIC_V3_PARITY', 'members':[{'strategy_id':key,
            'rule_identity':frozen['rule_identity'], 'weight_bps':10000, 'priority':0}],
            'purpose':'ENGINEERING_OBSERVATION', 'max_positions':2, 'max_symbol_exposure_bps':5000,
            'max_buy_turnover_bps':10000,
            'valid_until':(pd.Timestamp(str(next_day),tz='Asia/Shanghai')+pd.Timedelta(days=2)).isoformat()}}
    args = dict(archive_root=archive.root, strategy_ids=[key], policy=policy,
                calendar=[*days,next_day], warmup=warmup)
    return archive, frozen, args, clock, bundle, result


def test_public_archive_reopens_and_canonical_method_denies_formal(tmp_path):
    archive, frozen, args, clock, bundle, result = setup(tmp_path)
    key = frozen['strategy_id']
    reopened = PublicStrategyArchiveV3(archive.root)
    assert reopened.load(key) == frozen
    assert reopened.admission(key,purpose='ENGINEERING_OBSERVATION')['allowed']
    assert not reopened.admission(key,purpose='FORMAL_OBSERVATION')['allowed']
    assert not reopened.review(key)['method_support']['applicable']
    assert frozen['source_profile'] == 'SYNTHETIC'
    assert archive_for_ids(archive.root,[key]).load(key) == frozen
    with pytest.raises(ValueError,match='MIXED_VERSIONS'):
        archive_for_ids(archive.root,[key,'BS_'+'0'*64])
    real = deepcopy(args)
    real['warmup']['source_profile'] = 'HISTORICAL_REAL'
    with pytest.raises(PermissionError,match='SYNTHETIC_STRATEGY_NOT_REAL'):
        ForwardPaperSessionV1.create(tmp_path/'real', **real, profile='REAL_OBSERVED')
    formal = deepcopy(args)
    formal['policy']['portfolio']['purpose'] = 'FORMAL_OBSERVATION'
    with pytest.raises(PermissionError,match='NOT_ADMITTED'):
        ForwardPaperSessionV1.create(tmp_path/'formal',**formal,profile='SYNTHETIC',
            purpose='FORMAL_OBSERVATION',clock=lambda:clock[0])
    reopened.revoke(key,'测试撤销')
    assert not reopened.admission(key,purpose='ENGINEERING_OBSERVATION')['allowed']


def test_incoherent_public_result_cannot_be_archived(tmp_path):
    jobroot = evidence(tmp_path)
    result = read(jobroot/'case_RESULT.json')
    result['daily_accounts'][0]['equity'] += 1
    rebind_result(jobroot,result)
    with pytest.raises(ValueError,match='EVIDENCE_NOT_VERIFIED'):
        PublicStrategyArchiveV3(tmp_path/'archive').freeze(jobroot/'JOB.json','case')


@pytest.mark.parametrize('exits', [dict(stop_loss_pct=.002), dict(take_profit_pct=.002),
    dict(trailing_activate_pct=.001,trailing_pct=.002)])
def test_public_v3_paper_matches_exits_daily_and_recovers_uncommitted_open(tmp_path,monkeypatch,exits):
    archive,frozen,args,clock,bundle,result=setup(tmp_path,exits)
    paper=ForwardPaperSessionV1.create(tmp_path/'paper',**args,profile='SYNTHETIC',clock=lambda:clock[0])
    snapshots=SnapshotStoreV1(tmp_path/'snapshots')
    days=args['calendar'][:-1]
    prior={row['symbol']:row for row in args['warmup']['bars']}
    for index,day in enumerate(days):
        rows=bundle['daily'].loc[bundle['daily'].date==day].to_dict('records')
        for phase in (['CLOSE'] if index==0 else ['OPEN','CLOSE']):
            clock[0]=pd.Timestamp(str(day),tz='Asia/Shanghai')+pd.Timedelta(hours=9 if phase=='OPEN' else 15,minutes=30)
            bars=deepcopy(rows)
            if phase=='OPEN':
                for row in bars:
                    row.update(high=row['open'],low=row['open'],close=row['open'],
                        volume=prior[row['symbol']]['volume'],amount=prior[row['symbol']]['amount'])
            value=snapshots.record_synthetic(phase=phase,market_date=day,received_at=clock[0].isoformat(),
                payload={'bars':bars,'turn':bundle['turn'].loc[bundle['turn'].date==day].to_dict('records'),
                    'states':[{'symbol':s,'date':day,'listed':True,'delisted':False,'is_st':False,
                               'board':'MAIN','suspended':False} for s in args['policy']['symbols']],
                    'corporate_actions':[],'corporate_actions_complete':True})
            if index==2 and phase=='OPEN':
                import chanlun_trader.research_factory.forward_paper_v1 as module
                original=module._atomic_write
                def crash(path,body):
                    if path==paper.path('HEAD.json'):
                        raise RuntimeError('CRASH_AFTER_STAGE_BEFORE_HEAD')
                    return original(path,body)
                monkeypatch.setattr(module,'_atomic_write',crash)
                with pytest.raises(RuntimeError,match='CRASH_AFTER_STAGE'):
                    paper.ingest(snapshots.root,value['snapshot_id'])
                monkeypatch.setattr(module,'_atomic_write',original)
                paper=ForwardPaperSessionV1(paper.root,clock=lambda:clock[0])
            current=paper.ingest(snapshots.root,value['snapshot_id'])
            assert paper.ingest(snapshots.root,value['snapshot_id'])==current
        assert abs(current['state']['economic']['cash']-result['daily_accounts'][index]['cash'])<=.02
        assert current['state']['invariant_errors']==[]
        prior={row['symbol']:row for row in rows}
    fields=('strategy_id','symbol','side','quantity','price','fee','gross_value','fill_time')
    project=lambda rows:[{k:row[k] for k in fields} for row in rows]
    assert project(current['state']['economic']['trades'])==project(result['fills'])
    assert any(row['side']=='SELL' for row in result['fills'])
    assert current['state']['rule_exit_states']==result['final_account_checkpoint']['rule_exit_states']
    assert current['real_observation_days']==current['qualified_observation_days']==0
    assert not current['strategy_qualified']


def test_public_paper_source_change_still_rejects(tmp_path, monkeypatch):
    import chanlun_trader.research_factory.forward_paper_v1 as module
    archive,frozen,args,clock,bundle,result=setup(tmp_path)
    paper=ForwardPaperSessionV1.create(tmp_path/'paper',**args,profile='SYNTHETIC',clock=lambda:clock[0])
    monkeypatch.setattr(module,'source_identity',lambda:'changed')
    with pytest.raises(PermissionError,match='SOURCE_CHANGED'):
        paper.ingest(tmp_path/'absent','unused')


def test_public_archive_head_write_interruption_recovers_same_fact(tmp_path,monkeypatch):
    root=evidence(tmp_path)
    archive=PublicStrategyArchiveV3(tmp_path/'archive')
    original=archive._commit_head
    monkeypatch.setattr(archive,'_commit_head',lambda *args: (_ for _ in ()).throw(RuntimeError('head crash')))
    with pytest.raises(RuntimeError,match='head crash'):
        archive.freeze(root/'JOB.json','case')
    monkeypatch.setattr(archive,'_commit_head',original)
    frozen=archive.freeze(root/'JOB.json','case')
    assert archive.admission(frozen['strategy_id'],purpose='ENGINEERING_OBSERVATION')['allowed']
    assert len(list(archive.root.glob('PS_*')))==1
