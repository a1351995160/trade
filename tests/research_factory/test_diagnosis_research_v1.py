"""新编排复用真实账户/治理服务的合成集成，与真实市场证据分开。"""
from copy import deepcopy

import pandas as pd
import pytest

from bounded_research_fixture import synthetic_bundle
from test_bounded_real_research_loop_v1 import FakeInvoker, proposal, no_call
from chanlun_trader.research_factory import diagnosis_research_v1 as research
from chanlun_trader.research_factory import research_screening_v1 as screening
from scripts.s1_causal_price_strategy_v1 import Causal51VoteStrategy
from scripts import run_historical_process_research_v1 as historical


def long_bundle():
    # 504账户日，与旧短窗口各自生成，非对真实数据补造。
    days = [int(d.strftime('%Y%m%d')) for d in pd.bdate_range('2021-01-01', periods=584)]
    symbols = ['000001.SZ', '600000.SH']
    window = dict(symbols=symbols, feature_start=days[0], account_start=days[80], account_end=days[-1], calendar=days)
    bars, turns, states = [], [], []
    for symbol in symbols:
        for i, day in enumerate(days):
            price = 10 + i * .001
            bars.append(dict(symbol=symbol, date=day, open=price, high=price * 1.01, low=price * .99,
                close=price, prev_close=10 + max(0, i-1) * .001, volume=1_000_000., amount=price * 1_000_000., adjustflag='3'))
            turns.append(dict(symbol=symbol, date=day, volume=1_000_000., turn=1., tradestatus=1))
            if i >= 80:
                states.append(dict(symbol=symbol, trade_date=day, listed=True, delisted=False, universe_member=True,
                    eligibility_status='ELIGIBLE', st_status='NORMAL', suspension_status='TRADING',
                    board='SZ_MAIN' if symbol.endswith('SZ') else 'SH_MAIN'))
    return window, dict(profile='HISTORICAL_MODELED', daily=pd.DataFrame(bars), turn=pd.DataFrame(turns),
        states=pd.DataFrame(states), calendar=days, events=[], corporate_actions_complete=True,
        source_hashes={'execution_profile': 'HISTORICAL_MODELED', 'fixture': 'SYNTHETIC'},
        open_snapshots=[], close_snapshots=[])


@pytest.fixture
def service(tmp_path, monkeypatch):
    window, bundle = long_bundle()
    monkeypatch.setattr(historical, 'build_bundle', lambda path: (deepcopy(window), deepcopy(bundle), {}))
    seed = {'feedback': [{'entries': [{'reason_code': 'COST_SENSITIVITY', 'high_level_reason': '成本敏感'}]}],
            'previous_designs': [proposal(['RSI'])], 'scope_hash': 'SYNTHETIC_SEED'}
    monkeypatch.setattr(research, 'seed_diagnostics', lambda path: deepcopy(seed))
    short = synthetic_bundle(Causal51VoteStrategy())
    manifest = {'profile': 'SYNTHETIC', 'input_identity': short['input_identity'], 'events': []}
    instance = research.DiagnosisResearchV1.create(tmp_path/'workflow', seed_root=tmp_path/'seed',
        data_root=tmp_path/'data', input_manifest=manifest, approval_statement='合成闭环验证', attempts=1)
    def loader(manifest, strategy):
        return deepcopy(short), ()
    return instance, loader


def test_full_public_account_screening_and_repeat_no_model_or_account(service, monkeypatch):
    instance, loader = service
    invoker = FakeInvoker([proposal(['MA'])])
    result = instance.tick(loader=loader, invoker=invoker)
    assert result['strategy_qualified'] is False
    result = instance.tick(loader=loader, invoker=invoker)
    assert result['status'] == 'NO_CANDIDATE_PASSED'
    assert len(result['screening']['reports']) == 1
    assert invoker.calls == 1
    assert invoker.contexts[0]['failure_knowledge'][0]['entries'][0]['reason_code'] == 'COST_SENSITIVITY'
    assert invoker.contexts[0]['previous_designs'] == [proposal(['RSI'])]
    budget = instance.session.path('search_budget_registry.json').read_bytes()
    monkeypatch.setattr(research, 'run_historical_account', no_call)
    invoker.invoke = no_call
    repeated = instance.tick(loader=no_call, invoker=invoker)
    assert repeated['screening'] == result['screening']
    assert repeated['status'] == result['status']
    assert instance.session.path('search_budget_registry.json').read_bytes() == budget
    assert instance.handoff(calibration_path='unused', not_before=20270101)['screening'] == result['screening']
    assert not (instance.root / 'formal-assessment-authority-v1').exists()
    target = instance.root / 'screening' / 'CANDIDATE_001' / 'SCREEN.json'
    target.write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='CORRUPT'):
        instance.status()


def test_revocation_prevents_any_work(service):
    instance, _ = service
    instance.session.revoke('停止')
    with pytest.raises(PermissionError, match='REVOKED'):
        instance.tick(loader=no_call, invoker=FakeInvoker())
    assert not (instance.root/'screening').exists()


def test_invoker_recovery_binds_both_original_and_diagnostic_context(tmp_path):
    class Invoker:
        def invoke(self, context, **kwargs):
            self.expected = research.stable_hash(context)
            return proposal()
        def can_recover(self, *, staging_dir, context_hash):
            return context_hash == self.expected
    delegate = Invoker()
    wrapper = research.DiagnosisInvokerV1(delegate, feedback=[], previous_designs=[])
    context = {'failure_knowledge': [], 'previous_designs': [], 'instruction': ''}
    wrapper.invoke(context, staging_dir=tmp_path, timeout_seconds=1)
    assert wrapper.can_recover(tmp_path, research.stable_hash(context))
    assert not wrapper.can_recover(tmp_path, 'other')
    wrapper.feedback.append({'reason_code': 'CHANGED'})
    assert not wrapper.can_recover(tmp_path, research.stable_hash(context))


def test_seed_rule_duplicate_is_not_a_new_candidate(tmp_path):
    delegate = FakeInvoker([proposal(['RSI'])])
    wrapper = research.DiagnosisInvokerV1(delegate, feedback=[], previous_designs=[proposal(['RSI'])])
    context = {'failure_knowledge': [], 'previous_designs': [], 'instruction': ''}
    with pytest.raises(RuntimeError, match='DUPLICATE_SEED_RULE'):
        wrapper.invoke(context, staging_dir=tmp_path, timeout_seconds=1)
    assert delegate.calls == 1


def test_interrupted_screen_blocks_without_repeating_account(service, monkeypatch):
    instance, loader = service
    def fail(*args, **kwargs):
        raise RuntimeError('SYNTHETIC_ACCOUNT_FAILURE')
    monkeypatch.setattr(research, 'run_historical_account', fail)
    with pytest.raises(RuntimeError, match='SYNTHETIC_ACCOUNT_FAILURE'):
        instance.tick(loader=loader, invoker=FakeInvoker())
    assert instance.status()['status'] == 'BLOCKED'
    assert instance.status()['reason'] == 'SCREEN_ACCOUNT_UNSETTLED_NO_AUTOMATIC_RETRY'
    monkeypatch.setattr(research, 'run_historical_account', no_call)
    with pytest.raises(PermissionError, match='ALREADY_ATTEMPTED'):
        instance.tick(loader=no_call, invoker=FakeInvoker())


def test_running_account_is_not_reported_as_interrupted(service, monkeypatch):
    instance, loader = service
    def inspect_then_stop(*args, **kwargs):
        assert instance.status()['status'] == 'SCREENING_RUNNING'
        raise RuntimeError('SYNTHETIC_STOP_AFTER_INSPECT')
    monkeypatch.setattr(research, 'run_historical_account', inspect_then_stop)
    with pytest.raises(RuntimeError, match='STOP_AFTER_INSPECT'):
        instance.tick(loader=loader, invoker=FakeInvoker())
    assert instance.status()['status'] == 'BLOCKED'
