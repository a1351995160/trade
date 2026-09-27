"""有限工程验收脚本使用同一治理事务恢复；这里全部使用合成原始输入。"""
import json
from copy import deepcopy

import pytest

from scripts import run_rule_multi_symbol_validation_v2 as runner
from test_historical_process_v1 import historical_fixture


def setup_runner(tmp_path, monkeypatch):
    window, bundle = historical_fixture()
    monkeypatch.setattr(runner, 'build_bundle', lambda *args, **kwargs: (deepcopy(window), deepcopy(bundle), {}))
    # 这组测试只测持久事务，源码变更拒绝另由账户/任务入口测试覆盖。
    monkeypatch.setattr(runner, 'source_identity', lambda: 'SYNTHETIC_TRANSACTION_TEST')
    original, calls = runner.run, []
    def tracked(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(runner, 'run', tracked)
    arguments = {'data_root':tmp_path/'data', 'symbols':window['symbols'],
                 'output':tmp_path/'account', 'approval_statement':'明确合成事务验证'}
    return arguments, calls


def test_verified_result_before_settlement_recovers_without_account_rerun(tmp_path, monkeypatch):
    arguments, calls = setup_runner(tmp_path, monkeypatch)
    original = runner.StrategyBatchGovernanceV1.settle
    def interrupted(*args, **kwargs):
        raise RuntimeError('INJECTED_BEFORE_SETTLEMENT')
    monkeypatch.setattr(runner.StrategyBatchGovernanceV1, 'settle', interrupted)
    with pytest.raises(RuntimeError, match='INJECTED'):
        runner.execute(**arguments)
    assert len(calls) == 2
    budget = (arguments['output']/'search_budget_registry.json').read_bytes()
    monkeypatch.setattr(runner.StrategyBatchGovernanceV1, 'settle', original)
    report = runner.execute(**arguments)
    assert report['repeat_identical']
    assert len(calls) == 2
    assert (arguments['output']/'search_budget_registry.json').read_bytes() == budget
    assert runner.execute(**arguments) == report


@pytest.mark.parametrize('fault', ['receipt', 'start', 'budget', 'revocation'])
def test_completed_account_readback_checks_original_governance(tmp_path, monkeypatch, fault):
    arguments, calls = setup_runner(tmp_path, monkeypatch)
    runner.execute(**arguments)
    root = arguments['output']
    if fault == 'revocation':
        (root/'governance'/'REVOKED.json').write_text('{}', encoding='utf-8')
    else:
        path = {'receipt':root/'governance'/'CONFIRMATION.json',
                'start':root/'governance'/'MULTI_SYMBOL_ENGINEERING_START.json',
                'budget':root/'search_budget_registry.json'}[fault]
        value = json.loads(path.read_text(encoding='utf-8'))
        if fault == 'receipt': value['receipt_id'] = 'changed'
        elif fault == 'start': value['counted_before_account_calculation'] = False
        else: value['settled_reservations'] = {}
        path.write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises((ValueError, PermissionError)):
        runner.execute(**arguments)
    assert len(calls) == 2


@pytest.mark.parametrize('symbols', [[], ['000001.SZ','000001.SZ'], ['300001.SZ'], ['600000.SH','bad']])
def test_history_loader_rejects_invalid_universe_before_read(tmp_path, symbols):
    from scripts.run_historical_process_research_v1 import build_bundle
    with pytest.raises(ValueError, match='MAIN_BOARD_SYMBOLS_INVALID'):
        build_bundle(tmp_path/'does-not-exist', symbols=symbols)
