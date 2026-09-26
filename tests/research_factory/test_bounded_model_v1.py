"""模型只读无工具调用与已完成请求恢复。"""
import json
from pathlib import Path

import pytest

from chanlun_trader.research_factory import bounded_model_v1 as model
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.codex_backend import (
    CodexInvocationResultV1, build_codex_command,
)


PROPOSAL = {'hypothesis': '趋势延续', 'indicators': ['MA'], 'threshold': 1, 'change_reason': '检验假设'}


def test_design_command_disables_tools_without_changing_legacy(tmp_path):
    args = dict(staging_dir=tmp_path, output_schema_path=tmp_path/'schema.json',
                response_path=tmp_path/'response.txt')
    command = build_codex_command('codex.exe', design_only=True, **args)
    assert '--approve-for-me' not in command
    assert command[command.index('--sandbox') + 1] == 'read-only'
    assert '--output-schema' in command
    disabled = {command[i+1] for i, item in enumerate(command) if item == '--disable'}
    assert {'shell_tool', 'unified_exec', 'multi_agent', 'code_mode', 'workspace_dependencies',
            'apps', 'plugins', 'browser_use', 'computer_use'} <= disabled
    assert {'mcp_servers={}', 'web_search="disabled"', 'tools.view_image=false'} <= set(command)
    old = build_codex_command('codex.exe', **args)
    assert '--approve-for-me' in old and '--sandbox' not in old and '--output-schema' not in old


def test_success_checkpoint_recovers_without_model_and_staging_is_separate(tmp_path, monkeypatch):
    calls = []
    class Executor:
        def __init__(self, executable, *, design_only):
            assert design_only is True
        def execute(self, request, **kwargs):
            isolated = kwargs['staging_dir'].resolve()
            assert not isolated.is_relative_to(tmp_path.resolve())
            assert not tmp_path.resolve().is_relative_to(isolated)
            assert list(isolated.iterdir()) == [kwargs['output_schema_path']]
            calls.append(request.prompt)
            return CodexInvocationResultV1(response_text=json.dumps(PROPOSAL),
                                          stdout='{"type":"turn.completed"}\n')
    monkeypatch.setattr(model, 'SubprocessCodexExecutorV1', Executor)
    monkeypatch.setattr(model, 'discover_codex_executable', lambda: 'codex.exe')
    invoker = model.BoundedCodexInvokerV1()
    original_put = model._put
    def interrupt(path, payload):
        if Path(path).name == 'INVOCATION.json':
            raise RuntimeError('injected crash')
        return original_put(path, payload)
    monkeypatch.setattr(model, '_put', interrupt)
    with pytest.raises(RuntimeError, match='injected crash'):
        invoker.invoke({}, staging_dir=tmp_path/'model', timeout_seconds=1)
    assert invoker.can_recover(tmp_path/'model', stable_hash({}))
    monkeypatch.setattr(model, '_put', original_put)
    assert invoker.invoke({}, staging_dir=tmp_path/'model', timeout_seconds=1) == PROPOSAL
    assert invoker.invoke({}, staging_dir=tmp_path/'model', timeout_seconds=1) == PROPOSAL
    assert len(calls) == 1
    assert not invoker.can_recover(tmp_path/'model', 'another-context')


@pytest.mark.parametrize('event', [
    {'type':'item.completed','item':{'type':'command_execution'}},
    {'type':'item.started','item':{'type':'mcp_tool_call'}},
    {'type':'item.completed','item':{'type':'web_search'}},
    {'type':'new_unknown_event'},
])
def test_tools_and_unknown_events_rejected(event):
    with pytest.raises(RuntimeError, match='BOUNDED_MODEL_'):
        model.BoundedCodexInvokerV1._assert_no_tools(json.dumps(event)+'\n{"type":"turn.completed"}')


def test_unfinished_request_is_not_called_again(tmp_path, monkeypatch):
    model._put(tmp_path/'REQUEST.json', {'context':{}, 'context_hash':stable_hash({})})
    (tmp_path/'RESPONSE.txt').write_text(json.dumps(PROPOSAL), encoding='utf-8')
    monkeypatch.setattr(model, 'discover_codex_executable', lambda: 'codex.exe')
    invoker = model.BoundedCodexInvokerV1()
    assert not invoker.can_recover(tmp_path, stable_hash({}))
    with pytest.raises(RuntimeError, match='UNFINISHED'):
        invoker.invoke({}, staging_dir=tmp_path, timeout_seconds=1)


def test_runtime_failure_keeps_redacted_diagnostic(tmp_path, monkeypatch):
    secret = 'bounded-test-secret-value'
    monkeypatch.setenv('BOUNDED_TEST_TOKEN', secret)
    monkeypatch.setattr(model, 'discover_codex_executable', lambda: 'codex.exe')
    class Executor:
        def __init__(self, *args, **kwargs):
            pass
        def execute(self, *args, **kwargs):
            return CodexInvocationResultV1(exit_code=1, stderr='provider failed: ' + secret)
    monkeypatch.setattr(model, 'SubprocessCodexExecutorV1', Executor)
    with pytest.raises(RuntimeError, match='RUNTIME_FAILED'):
        model.BoundedCodexInvokerV1().invoke({}, staging_dir=tmp_path, timeout_seconds=1)
    error = model._read(tmp_path/'ERROR.json')
    assert error['exit_code'] == 1
    assert 'provider failed' in error['diagnostic'] and secret not in error['diagnostic']
