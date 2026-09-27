"""V2 正式路由及真实引擎合成账户；这些用例不产生真实策略资格。"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pandas as pd
import pytest

from chanlun_trader.research_factory import formal_assessment_v1 as formal
from chanlun_trader.research_factory import formal_rule_adapter_v2 as adapter
from chanlun_trader.research_factory.formal_account_backend_v1 import window_input_identity
from test_formal_account_backend_v1 import fixture
from test_research_rule_strategy_v2 import payload
from test_formal_assessment_v1 import harness


def observed_fixture():
    window, bundle = fixture()
    bundle['profile'] = 'SYNTHETIC'
    for snap in bundle['open_snapshots']:
        for row in snap['payload']['states']:
            row['date'] = snap['market_date']
    for snap in bundle['close_snapshots']:
        day = snap['market_date']
        snap['payload']['turn'] = bundle['turn'].loc[bundle['turn'].date == day].to_dict('records')
        states = bundle['states'].loc[bundle['states'].trade_date == day].to_dict('records')
        if not states:
            states = [{'symbol':symbol, 'date':day, 'listed':True, 'delisted':False,
                       'st_status':'NORMAL', 'suspension_status':'TRADING'} for symbol in window['symbols']]
        for row in states:
            row.update(date=day, is_st=row['st_status']=='ST', suspended=row['suspension_status']!='TRADING')
        snap['payload']['states'] = states
    return window, bundle


def test_observed_stateful_account_public_entry_replay_and_tamper():
    window, bundle = observed_fixture()
    identity = window_input_identity(bundle, window)
    plan = adapter.prepare_rule(payload(), strategy_id='rule', window=window, costs='BASE')
    receipt = {'strategy_plans': {'rule': plan}, 'input_identity': identity,
               'novelty': {'rule': {'allowed':True}}, 'execution_purpose':'rule','execution_consumed':True}
    result = adapter.run_rule(payload(), strategy_id='rule', window=window, costs='BASE',
        bundle=bundle, input_identity=identity, active_check=lambda:receipt)
    assert result['status'] == 'OBSERVED_ACCOUNT_COMPLETED'
    assert result['profile'] == 'SYNTHETIC'
    assert 'chain' not in result
    view = adapter.validate_result(result, bundle=bundle, window=window, costs='BASE', active_check=lambda:receipt)
    assert len(view['daily_returns']) == 30
    assert view['metrics']['max_drawdown'] <= 0
    changed = deepcopy(result)
    changed['daily_accounts'][-1]['positions'] = []
    with pytest.raises(ValueError, match='REPLAY_CONFLICT'):
        adapter.validate_result(changed, bundle=bundle, window=window, costs='BASE', active_check=lambda:receipt)


def rule_archives(service, monkeypatch, symbols):
    original = service.archive.load
    def load(key):
        value = original(key)
        value['proposal'] = payload()
        value['evidence']['session']['input_manifest'] = {'candidate_capability':'RESEARCH_RULE_STRATEGY_V2',
            'window':{'symbols':symbols}, 'initial_cash':1_000_000,'max_positions':2,'max_symbol_exposure_bps':5000}
        return value
    monkeypatch.setattr(service.archive, 'load', load)


def test_rule_scope_keeps_old_benchmark_and_shared_authority(harness, monkeypatch):
    service, family, register, _, _, prepared = harness
    ids = family()
    rule_archives(service, monkeypatch, prepared['window']['symbols'])
    with pytest.raises(ValueError, match='SCREENING_REQUIRED'):
        register(ids)
    # 此既有 harness 只验证登记状态机；完整真实初筛见本文件的 504 日测试。
    monkeypatch.setattr(formal, '_screening', lambda root, archives: {'selected':['CANDIDATE_001']})
    from pathlib import Path
    screening_root = Path(service.archive.load(ids[0])['origin']['source_root']).parent/'screening'
    status = register(ids, screening_root=screening_root)
    plan = service.plan(status['batch_id'])
    assert plan['strategy_capability'] == 'RESEARCH_RULE_STRATEGY_V2'
    assert plan['policy']['benchmark'] == 'EQUAL_WEIGHT_BUY_AND_HOLD'
    assert plan['execution_scope']['initial_cash'] == 1_000_000
    assert plan['slot'] == 1 and plan['account_budget'] == 3
    with pytest.raises(ValueError, match='FAMILY_ALREADY_REGISTERED'):
        register(ids, screening_root=screening_root)


def test_new_pool_or_capital_not_silently_certified(harness, monkeypatch):
    service, family, register, _, _, prepared = harness
    ids = family()
    rule_archives(service, monkeypatch, ['000001.SZ','600000.SH','000002.SZ'])
    with pytest.raises(ValueError, match='EXECUTION_SCOPE_UNSUPPORTED'):
        register(ids)
    assert service._plans() == []


def test_rule_assessment_cannot_pass_even_with_positive_test_statistics():
    rows = [{'date':day,'net_return':.001} for day in range(504)]
    benchmark = {'daily_returns':[{'date':day,'net_return':0.} for day in range(504)],
                 'metrics':{'net_return':0.,'max_drawdown':0.}}
    candidate = {'daily_returns':rows,'metrics':{'net_return':.5,'max_drawdown':-.1}}
    plan = {'family':{'candidate':{'strategy_id':'id','source_profile':'HISTORICAL_MODELED'}},
            'profile':'REAL_OBSERVED','strategy_capability':'RESEARCH_RULE_STRATEGY_V2',
            'method_hash':formal.statistics_v2.METHOD_HASH}
    result = formal.adjudicate(plan=plan, evidence={'qualification_evidence_eligible':True},
        results={'candidate_BASE':candidate,'candidate_STRESS':candidate,'BENCHMARK_BASE':benchmark},
        statistics={'supported':{'candidate':True},'adjusted_p':{'candidate':.001}},method_approved=True)
    assert result['candidate']['decision'] == 'CONTINUE_RESEARCH'
    assert 'RULE_V2_METHOD_SCOPE_NOT_ESTABLISHED' in result['candidate']['reason_codes']
    assert 'REAL_RETURN_PROCESS_APPLICABILITY_NOT_ESTABLISHED' in result['candidate']['reason_codes']
    assert not result['candidate']['strategy_qualified']


def test_full_frozen_rule_family_future_snapshots_504_accounts_and_readback(tmp_path, monkeypatch, completed_rule_diagnosis):
    """来源、档案、快照、预算和账户均走真实服务；仅模型及校准身份为显式合成。"""
    from test_bounded_rule_loop_v2 import session_fixture, SyntheticInvoker
    from chanlun_trader.research_factory.common import stable_hash
    from chanlun_trader.research_factory.forward_snapshot_v1 import SnapshotStoreV1
    from chanlun_trader.research_factory.formal_evidence_v1 import CalendarEvidenceStoreV1
    from chanlun_trader.research_factory.strategy_qualification_v1 import BoundedStrategyArchiveV1
    workflow, loader, invoker, screening_status = completed_rule_diagnosis
    archives = workflow.archive
    archived = workflow.archives()
    calibration = {'method_hash':formal.statistics_v2.METHOD_HASH, 'method_approved':False,
                   'profile':'SYNTHETIC_CALIBRATION_FIXTURE', 'summary':{}}
    monkeypatch.setattr(formal, '_load_method', lambda path:deepcopy(calibration))
    monkeypatch.setattr(formal, 'V2_CALIBRATION_HASH', stable_hash(calibration))
    service = formal.FormalAssessmentServiceV1(archives.root)
    first = pd.Timestamp(datetime.now(timezone.utc) + timedelta(days=2)).tz_convert('Asia/Shanghai').normalize()
    days = [int(day.strftime('%Y%m%d')) for day in pd.bdate_range(first, periods=564)]
    symbols = ['000001.SZ','600000.SH']
    with pytest.raises(ValueError, match='SCREENING_REQUIRED'):
        service.register(strategy_ids=[a['strategy_id'] for a in archived],symbols=symbols,not_before=days[0],
            calibration_path=tmp_path/'synthetic-calibration.json',profile='SYNTHETIC')
    status = service.register(strategy_ids=[a['strategy_id'] for a in archived],symbols=symbols,not_before=days[0],
        calibration_path=tmp_path/'synthetic-calibration.json',profile='SYNTHETIC',screening_root=workflow.root/'screening')
    batch = status['batch_id']
    store = SnapshotStoreV1(service.path(batch,'snapshots'))
    closes, opens = [], []
    coverage_root = service.path(batch,'coverage')
    coverage_root.mkdir()
    for index, day in enumerate(days):
        for phase in (['CLOSE'] if index < 60 else ['OPEN','CLOSE']):
            stamp = pd.Timestamp(str(day),tz='Asia/Shanghai') + pd.Timedelta(hours=9 if phase=='OPEN' else 15,minutes=31)
            price, previous = 10.+index*.002, 10.+max(0,index-1)*.002
            data = {'bars':[{'symbol':symbol,'date':day,'open':price,'high':price,'low':price,
                'close':price,'prev_close':previous,'volume':1_000_000.,'amount':price*1_000_000.,
                'price_basis':'OBSERVED_NOW' if phase=='OPEN' else 'RAW_CLOSE','amount_unit':'CNY'} for symbol in symbols],
                'turn':[{'symbol':symbol,'date':day,'turn':1.,'tradestatus':1} for symbol in symbols],
                'states':[{'symbol':symbol,'date':day,'listed':True,'delisted':False,'is_st':False,
                           'board':'MAIN','suspended':False} for symbol in symbols],
                'corporate_actions':[],'corporate_actions_complete':True,'corporate_action_envelopes':[]}
            coverage = {'profile':'SYNTHETIC','provider':'SYNTHETIC_FULL_SERVICE_TEST','received_at':stamp.isoformat(),
                        'market_date':day,'symbols':symbols,'complete':True,'envelope_hashes':[]}
            path = coverage_root/f'{day}_{phase}.json'
            raw = json.dumps(coverage,sort_keys=True).encode()
            path.write_bytes(raw)
            data['corporate_action_coverage'] = {**coverage,'source_ref':str(path),
                                                'source_sha256':hashlib.sha256(raw).hexdigest()}
            captured = store.record_synthetic(phase=phase,market_date=day,payload=data,received_at=stamp.isoformat())
            (opens if phase=='OPEN' else closes).append(captured['snapshot_id'])
    calendar = CalendarEvidenceStoreV1(service.path(batch,'calendar')).record_synthetic(start=days[0],end=days[-1],
        dates=days,received_at=(pd.Timestamp(str(days[-1]),tz='Asia/Shanghai')+pd.Timedelta(hours=16)).isoformat())
    plan_bytes = service.path(batch,'PLAN.json').read_bytes()
    report = service.run(batch,snapshot_root=store.root,snapshot_ids=closes,open_snapshot_ids=opens,
        calendar_root=service.path(batch,'calendar'),calendar_id=calendar['calendar_id'])
    assert report['status'] == 'ASSESSED' and not report['strategy_qualified']
    assert report['account_budget_consumed'] == 3
    assert set(report['decisions']) == {'CANDIDATE_001','CANDIDATE_002'}
    reasons = report['decisions']['CANDIDATE_002']['reason_codes']
    assert 'RULE_V2_METHOD_SCOPE_NOT_ESTABLISHED' in reasons
    assert 'REAL_RETURN_PROCESS_APPLICABILITY_NOT_ESTABLISHED' in reasons
    assert service.report(batch) == report
    assert service.path(batch,'PLAN.json').read_bytes() == plan_bytes
    assert len(service._plans()) == 1


@pytest.mark.parametrize('field,value', [
    ('initial_cash', 999999), ('max_positions', 3),
    ('max_symbol_exposure_bps', 10000),
    ('window', {'symbols': ['000001.SZ', '000002.SZ']}),
])
def test_execution_scope_rejects_each_changed_account_binding(field, value):
    symbols = ['000001.SZ', '600000.SH']
    manifest = {'window': {'symbols': symbols}}
    assert adapter.execution_scope(manifest, symbols)['symbols'] == symbols
    manifest[field] = value
    with pytest.raises(ValueError, match='EXECUTION_SCOPE_UNSUPPORTED'):
        adapter.execution_scope(manifest, symbols)
