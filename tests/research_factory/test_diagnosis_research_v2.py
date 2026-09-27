"""真实公共账户的合成504日诊断编排；不把合成结果当成策略有效证据。"""
from copy import deepcopy
import math

import pytest

from test_diagnosis_research_v1 import long_bundle
from test_research_rule_strategy_v2 import payload, node
from test_bounded_rule_loop_v2 import SyntheticInvoker
from chanlun_trader.research_factory import diagnosis_research_v2 as research
from chanlun_trader.research_factory import research_screening_v2 as screening
from chanlun_trader.research_factory.bounded_research_v1 import _read
from chanlun_trader.research_factory.research_rule_strategy_v2 import CAPABILITY
from chanlun_trader.research_factory.rule_account_backend_v2 import rule_input_identity
from chanlun_trader.research_factory.research_screening_v1 import REASONS
from scripts import run_historical_process_research_v1 as historical


def market_bundle():
    window, bundle = long_bundle()
    days = window['calendar']
    prices = {day: 12 + 2*math.sin(i*2*math.pi/84) for i, day in enumerate(days)}
    for i, row in bundle['daily'].iterrows():
        price = prices[int(row.date)]
        index = days.index(int(row.date))
        bundle['daily'].loc[i, ['open','high','low','close','prev_close','amount']] = [
            price, price*1.01, price*.99, price, prices[days[max(0,index-1)]], price*1_000_000]
    return window, bundle


def trend_payload():
    value = payload()
    value.update(buy=node('gt', node('field','close'), node('indicator','MA',output='ma',version='MA_ARITHMETIC_V1')),
                 sell=node('lt',node('field','close'),node('indicator','MA',output='ma',version='MA_ARITHMETIC_V1')),
                 max_hold_sessions=100, cooldown_sessions=0, target_weight=.5)
    return value


def seed():
    return {'feedback': [{'source_history_hash':'a'*64,'entries':[
        {'reason_code':'COST_SENSITIVITY','high_level_reason':REASONS['COST_SENSITIVITY']}]}],
        'previous_designs': [], 'scope_hash':'SYNTHETIC_SEED'}


def create_service(root, monkeypatch, *, attempts=1):
    window, bundle = market_bundle()
    monkeypatch.setattr(historical, 'build_bundle', lambda path: (deepcopy(window),deepcopy(bundle),{}))
    monkeypatch.setattr(research, 'seed_diagnostics', lambda path: deepcopy(seed()))
    manifest = {'profile':'SYNTHETIC','candidate_capability':CAPABILITY,'window':window,
                'input_identity':rule_input_identity(bundle,window)}
    service = research.DiagnosisResearchV2.create(root,seed_root=root.parent/'seed',data_root=root.parent/'data',
        input_manifest=manifest,approval_statement='合成504日完整家族测试',attempts=attempts)
    def loader(manifest,strategy):
        frame = deepcopy(bundle)
        frame['input_identity'] = manifest['input_identity']
        return frame, deepcopy(bundle['events'])
    return service, loader



def test_diagnosis_family_feedback_repeat_and_tamper(completed_rule_diagnosis, monkeypatch):
    service, loader, invoker, status = completed_rule_diagnosis
    assert set(status['screening']['reports']) == {'CANDIDATE_001','CANDIDATE_002'}
    codes = [entry.get('reason_code') for item in invoker.contexts[1]['failure_knowledge']
             for entry in item.get('entries',[])]
    assert 'NONPOSITIVE_NET_RETURN' in codes and 'SUBPERIOD_EXCESS_NOT_POSITIVE' in codes
    assert service.config()['input_identity'] != service.config()['benchmark_input_identity']
    before = service.session.path('search_budget_registry.json').read_bytes()
    def no_call(*a,**k):
        pytest.fail('已结算研究不得重新调用模型或账户')
    monkeypatch.setattr(research,'run_account',no_call)
    assert service.tick(loader=no_call,invoker=None)['screening'] == status['screening']
    assert before == service.session.path('search_budget_registry.json').read_bytes()
    root = service.root/'screening'
    base = _read(root/'CANDIDATE_002'/'CANDIDATE_002_BASE_RESULT.json')
    stress = _read(root/'CANDIDATE_002'/'CANDIDATE_002_STRESS_RESULT.json')
    benchmark = _read(root/'BENCHMARK_BASE_RESULT.json')
    bad = deepcopy(base)
    bad['daily_accounts'].pop()
    with pytest.raises(ValueError,match='ACCOUNT_EVIDENCE'):
        screening.screen(bad,stress,benchmark,config=service.config())
    config = deepcopy(service.config())
    config['benchmark_input_identity'] = config['input_identity']
    with pytest.raises(ValueError,match='INPUT_CONFLICT'):
        screening.screen(base,stress,benchmark,config=config)
    with pytest.raises(ValueError,match='FAMILY_CHANGED'):
        screening.validated_screening(root, service.archives()[1:])


def test_rule_diagnosis_revocation_prevents_work(tmp_path, monkeypatch):
    service, loader = create_service(tmp_path/'workflow',monkeypatch)
    service.session.revoke('停止')
    with pytest.raises(PermissionError,match='REVOKED'):
        service.tick(loader=loader,invoker=None)
    assert not (service.root/'screening').exists()


def test_cli_loader_binds_input_for_original_execute(tmp_path, monkeypatch):
    from scripts import run_diagnosis_research_v2 as cli
    window, bundle = market_bundle()
    monkeypatch.setattr(cli, 'build_bundle', lambda path: (deepcopy(window),deepcopy(bundle),{}))
    manifest = cli.freeze_manifest(tmp_path)
    frame, events = cli.load_rules(manifest, None)
    assert frame['input_identity'] == manifest['input_identity'] == rule_input_identity(frame, window)
    assert events == bundle['events']
    manifest['input_identity'] = 'changed'
    with pytest.raises(ValueError, match='INPUT_CHANGED'):
        cli.load_rules(manifest, None)
