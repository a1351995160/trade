"""公共路由装配与真实人工Owner费用结清；不代替完整合成或55pins认证。"""
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from chanlun_trader.research_factory import continuous_submission_v1 as public
from chanlun_trader.research_factory import universe_execution_recovery_v1 as reconciliation
from chanlun_trader.research_factory.account_version_continuation_v1 import AccountVersionContinuationV1
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.run_budget import effective_execution_status
from scripts import run_account_version_session_resume_v1 as session_runner
from scripts import run_strategy_account_v1 as runner
from test_account_purpose_time_extension_v1 import extension_case, pin, write
from test_account_version_session_resume_v1 import original_children, route_session
from test_failed_account_recovery_capability_v1 import recovery_case
from test_failed_account_recovery_governance_v1 import assert_originals, ready


@pytest.mark.parametrize('new_settlement', [False, True])
def test_public_reconcile_selects_independent_recovery_even_with_old_failed_settlement(
        tmp_path, monkeypatch, new_settlement):
    # 只替换服务装配与对账执行器；被测为真实公共函数及路径选择，不声称Owner验证。
    task_id = stable_hash('ARTIFICIAL_RECOVERY_PUBLIC_ROUTING')
    folder = tmp_path / task_id; account = folder / 'account'; account.mkdir(parents=True)
    job = {'root': str(account), 'plans': {'BASE': {}, 'STRESS': {}}}
    path = account / 'JOB.json'; write(path, job)
    preview = {'request': {'phase': 'EXPLORATION', 'research_binding_ref': {'fixture_only': True}}}
    preview['preview_identity'] = stable_hash(preview); write(folder / 'PREVIEW.json', preview)
    task = {'task_id': task_id, 'job_path': str(path), 'job_sha256': pin(path)['sha256'],
        'preview_identity': preview['preview_identity']}; write(folder / 'TASK.json', task)
    write(account / 'BASE_START.json', {'fixture_only': True})
    write(account / 'BASE_SETTLEMENT.json', {'completed': False, 'fixture_only': True})
    write(account / 'BASE_RECOVERY_START.json', {'fixture_only': True})
    if new_settlement:
        write(account / 'BASE_RECOVERY_SETTLEMENT.json', {'completed': False, 'fixture_only': True})
    originals = {p: p.read_bytes() for p in folder.rglob('*.json')}
    context = SimpleNamespace(recovery=SimpleNamespace(original_failed_account='BASE'))
    monkeypatch.setattr(runner, 'account_version_context', lambda job: context)
    monkeypatch.setattr(runner, 'reconcile_account_version_prefix', lambda job: context)
    monkeypatch.setattr(runner, 'validate_sources', lambda job: context)
    calls = []
    monkeypatch.setattr(reconciliation, 'resume_long_horizon_job',
        lambda actual_path, actual_job, **kwargs: calls.append((actual_path, actual_job, kwargs)))
    service = SimpleNamespace(root=tmp_path, _task=lambda requested: task,
        continuous_scope=SimpleNamespace(resolve=lambda ref, **kwargs: {'phase': 'EXPLORATION'}))
    value = public.reconcile_submission(service, task_id)
    assert value['dispatched_segments'] == 0
    assert value['status'] == ('NO_PUBLIC_RECONCILIATION_PENDING' if new_settlement else 'PUBLIC_RECONCILED')
    assert calls == ([] if new_settlement else [(path, job, {'reconcile_only': True})])
    assert all(p.read_bytes() == raw for p, raw in originals.items())


@pytest.mark.parametrize('stop', ['deadline', 'revocation', 'account_revocation'])
def test_paid_recovery_continue_is_settled_at_dispatch_stop_without_replaying_original_failure(
        recovery_case, monkeypatch, stop):
    case = recovery_case; name = 'ARTIFICIAL_BASE'; owner = ready(case)
    context = AccountVersionContinuationV1.from_job(case['job'])
    dispatch = case['gov'].start_segment(name); context.record_dispatch(name, dispatch)
    prefix = name + '_SEGMENT_000051'
    resource = write(case['account'] / (prefix + '_RESOURCE.json'), {
        'fixture_only': 'ARTIFICIAL_PAID_CONTINUE_NOT_REAL_WORKER_MEASUREMENT',
        'dispatch_id': dispatch['dispatch_id'], 'dispatch_identity': dispatch['dispatch_identity'],
        'elapsed_wall_seconds': .2, 'time_extension_proof': case['extension'].proof(),
        'failed_account_recovery_proof': owner.proof(), 'account_version_continuation': context.proof(name)})
    case['gov'].end_segment(name, 51, seconds=.2, evidence_identity=resource['sha256'],
        dispatch_identity=dispatch['dispatch_identity'])
    # 夹具是人工经济计划；保留完整Owner/源码/父子链校验，未执行价格加载。
    monkeypatch.setattr(runner, 'validate_sources',
        lambda job: AccountVersionContinuationV1.from_job(job).validate_sources(job))
    if stop == 'deadline':
        when = datetime.fromisoformat(owner.summary_value['expires_at']) - timedelta(seconds=60)
        class Deadline(datetime):
            @classmethod
            def now(cls, tz=None):
                return when
        monkeypatch.setattr(reconciliation, 'datetime', Deadline)
    elif stop == 'revocation':
        case['campaign'].revoke('artificial Owner revocation after paid segment')
    else:
        case['gov'].revoke('artificial account-only revocation after paid segment')
        assert not case['campaign'].peek_status()['revoked']
    job_path = case['account'] / 'JOB.json'
    result = reconciliation.resume_long_horizon_job(job_path, case['job'], reconcile_only=True)
    assert result['dispatched_segments'] == 0
    current = case['campaign'].peek_status(); operation = current['operations']['BASE']
    state = case['gov'].segment_status(name)
    settled = json.loads((case['account'] / (name + '_RECOVERY_SETTLEMENT.json')).read_text(encoding='utf-8'))
    assert settled['completed'] is False and settled['wall_seconds'] == state['charged_seconds']
    assert operation['status'] == 'FAILED' and effective_execution_status(operation) == 'FAILED'
    assert operation['actual']['wall_seconds'] == 42340
    assert current['used']['wall_seconds'] == math.ceil(state['charged_seconds'])
    assert current['reserved']['wall_seconds'] == 72000
    assert state['segments'][-1]['charge']['outcome'] == 'CONTINUE'
    assert state['segments'][-2]['charge']['outcome'] == 'FAILED'
    assert not (case['account'] / (name + '_SEGMENT_000052_DISPATCH.json')).exists()
    assert_originals(case)


def test_public_error_reconciles_stopped_recovery_once_before_waiting(tmp_path, monkeypatch):
    # 会话装配单元与上面的真实人工Owner费用测试分离。
    session, calls = route_session(tmp_path, monkeypatch)
    original_children(session, base='FAILED', stress='RESERVED')
    row = session['campaign'].operations['BASE_ACCOUNT']; row['failed_account_recovery'] = {'status': 'RUNNING'}
    context = session['context']; context.recovery = SimpleNamespace(original_failed_account='BASE_ACCOUNT')
    context.recovery_installed = True; context.job = {'fixture_only': True}
    start = tmp_path / 'RECOVERY_START.json'; settlement = tmp_path / 'RECOVERY_SETTLEMENT.json'
    write(start, {'fixture_only': True})
    monkeypatch.setattr(runner, 'account_execution_paths', lambda job, name: {'start': start, 'settlement': settlement})
    reconciled = []
    def close(path, job, *, reconcile_only):
        assert reconcile_only is True
        reconciled.append(path); row['failed_account_recovery']['status'] = 'FAILED'
        write(settlement, {'completed': False, 'fixture_only': True})
        return {'dispatched_segments': 0}
    monkeypatch.setattr(reconciliation, 'resume_long_horizon_job', close)
    def expired(task_id):
        calls.append(task_id)
        raise PermissionError('ARTIFICIAL_DISPATCH_WINDOW_EXHAUSTED')
    session['submission'].advance = expired
    result = session_runner.run_session(tmp_path / 'unused.json')
    assert result['status'] == 'WAITING_ORIGINAL_OPERATION_RECONCILIATION'
    assert row['status'] == 'FAILED' and row['failed_account_recovery']['status'] == 'FAILED'
    assert len(calls) == len(reconciled) == 1
    before = {p: p.read_bytes() for p in (session['audit'] / 'events').glob('*.json')}
    assert session_runner.run_session(tmp_path / 'unused.json')['status'] == result['status']
    assert len(calls) == len(reconciled) == 1
    assert all(p.read_bytes() == raw for p, raw in before.items())
