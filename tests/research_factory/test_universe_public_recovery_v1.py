"""公共恢复验收：只用临时合成三板块数据和临时账户预算。"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import time

import pytest

from chanlun_trader.research_factory.budget import SearchBudgetRegistryV1
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.engine_replay_recovery_v1 import _read_receipt
from chanlun_trader.research_factory.research_evidence_v1 import verify_job_evidence
from chanlun_trader.research_factory.strategy_submission_v1 import load_frozen_bundle
from chanlun_trader.research_factory.universe_evidence_v1 import reconstruct_universe_account
from chanlun_trader.synthetic_batch_resources import run_bounded_worker
from chanlun_trader.research_daemon_state import DaemonInstanceLockV1
from scripts import run_strategy_account_v1 as runner
from test_universe_submission_v1 import public_universe_case


# 限制握手先于 pandas、策略和账户代码导入。故障注入仅在真实原子提交返回之后，
# 不替换输入、撮合、账户计算、预算或检查点写入。
_INTERRUPTED_WORKER = """
from chanlun_trader.synthetic_batch_resources import worker_resource_handshake
handshake = worker_resource_handshake()
import os
import sys
import time
from scripts import run_strategy_account_v1 as runner
runner.HANDSHAKE = handshake
stop_day = int(sys.argv[3])
fault = sys.argv[4]
def after_commit(frame, event, arg):
    if (event == 'return'
            and frame.f_globals.get('__name__') == 'chanlun_trader.research_factory.engine_replay_recovery_v1'
            and frame.f_code.co_name == '_write_receipt'
            and frame.f_locals.get('body', {}).get('last_day') == stop_day):
        if fault == 'TIMEOUT':
            time.sleep(30)
        os._exit(73)
sys.setprofile(after_commit)
runner.worker(sys.argv[1], sys.argv[2])
"""


def approved_case(tmp_path):
    service, request, authority, accesses = public_universe_case(tmp_path)
    preview = service.preview(request)
    permit = {key: preview['request'][key] for key in (
        'initial_cash', 'symbols', 'feature_start', 'account_start', 'account_end',
        'max_positions', 'max_symbol_exposure_bps', 'costs', 'benchmark')}
    authority['account_authorization'] = {**permit, 'purpose': 'FROZEN_PUBLIC_ACCOUNT_PLANS',
        'rule_identity': preview['rule_identity'], 'max_account_jobs': 2}
    task = service.freeze(request, preview['preview_identity'])
    approval = service.approval_preview(task['task_id'])
    assert approval['plan_ids'] == task['plan_ids']
    service.approve(task['task_id'], approval['preview_identity'])
    path = Path(task['job_path'])
    job = runner.read_json(path)
    assert len(job['plans']) == 2
    return {'service': service, 'request': request, 'authority': authority, 'accesses': accesses,
            'task': task, 'path': path, 'job': job, 'name': list(job['plans'])[-1], 'root': path.parent}


def budget_head(case):
    job = case['job']
    return SearchBudgetRegistryV1(job['objective_id'], job['budget_path']).head_hash


def bounded_worker(case, name, *, command=None, wall_seconds=45):
    job, root = case['job'], case['root']
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1',
        **{key: '1' for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS')}}
    env['PYTHONPATH'] = str(runner.REPO / 'src') + os.pathsep + env.get('PYTHONPATH', '')
    env.pop('CHANLUN_TEST_ISOLATION', None)
    begin = time.monotonic()
    resource = run_bounded_worker(command or [sys.executable, str(Path(runner.__file__).resolve()),
        '--worker', name, '--job', str(case['path'])], root=runner.REPO,
        memory_mib=job['resources']['memory_mib'], wall_seconds=wall_seconds,
        environment=env, execution={'purpose': name},
        on_started=lambda pid: runner.save(root / (name + '_WORKER.json'), {'pid': pid, 'purpose': name}))
    resource['elapsed_wall_seconds'] = time.monotonic() - begin
    runner.save(root / (name + '_RESOURCE.json'), {key: value.decode('utf-8', errors='replace')
        if isinstance(value, bytes) else value for key, value in resource.items()})
    return resource


def interrupted_case(tmp_path, *, fault='EXIT'):
    case = approved_case(tmp_path)
    job, name, root = case['job'], case['name'], case['root']
    governance = runner.service(job)
    for earlier in list(job['plans'])[:-1]:
        governance.start(earlier)
        resource = bounded_worker(case, earlier)
        assert resource['returncode'] == 0 and resource['timed_out'] is False, resource
        governance.settle(earlier, completed=True, seconds=resource['elapsed_wall_seconds'],
            result_hash=runner.sha(root / (earlier + '_RESULT.json')))
    start = governance.start(name)
    budget = SearchBudgetRegistryV1(job['objective_id'], job['budget_path']).snapshot()
    assert len(budget['settled_reservations']) == 2
    assert set(budget['settled_reservations'].values()) == {'CONSUMED'}
    assert len(budget['buckets']) == 2
    assert all(bucket['used'] == 1 and bucket['reserved'] == 0 for bucket in budget['buckets'])
    window = job['plans'][name]['backend']['window']
    account_days = [day for day in window['calendar'] if day >= window['account_start']]
    stop_day = account_days[3]
    resource = bounded_worker(case, name, command=[sys.executable, '-c', _INTERRUPTED_WORKER,
        str(case['path']), name, str(stop_day), fault], wall_seconds=8 if fault == 'TIMEOUT' else 45)
    assert resource['timed_out'] is (fault == 'TIMEOUT'), resource
    if fault == 'EXIT':
        assert resource['returncode'] == 73, resource
    if os.name == 'nt':
        assert resource['windows_job_bound'] is True
    access = runner.read_json(root / (name + '_INPUT_ACCESS.json'))
    assert access['purpose'] == name and access['input_identity'] == job['input_identity']
    if os.name == 'nt':
        assert access['windows_job_verified'] is True
    # Job 关闭后子进程终止可能稍迟于 launcher 返回；实际死亡后才提交恢复请求。
    deadline = time.monotonic() + 5
    pids = (resource['launcher_pid'], access['reader_pid'])
    while any(DaemonInstanceLockV1._pid_alive(pid) for pid in pids) and time.monotonic() < deadline:
        time.sleep(.05)
    assert not any(DaemonInstanceLockV1._pid_alive(pid) for pid in pids), pids
    checkpoint_path = Path(job['items'][name]['backend_options']['checkpoint_path'])
    checkpoint = _read_receipt(checkpoint_path, root, job['input_identity'])
    assert checkpoint['last_day'] == stop_day and checkpoint['budget_reused'] is True
    assert not (root / (name + '_RESULT.json')).exists()
    assert not (root / (name + '_SETTLEMENT.json')).exists()
    case.update(start=start, checkpoint=checkpoint, checkpoint_path=checkpoint_path, resource=resource)
    case['start_bytes'] = (root / (name + '_START.json')).read_bytes()
    case['all_start_bytes'] = {path.name: path.read_bytes() for path in root.glob('*_START.json')}
    case['budget_bytes'] = Path(job['budget_path']).read_bytes()
    case['budget_head'] = budget_head(case)
    return case


def assert_original_consumption(case):
    assert (case['root'] / (case['name'] + '_START.json')).read_bytes() == case['start_bytes']
    assert Path(case['job']['budget_path']).read_bytes() == case['budget_bytes']
    assert budget_head(case) == case['budget_head']
    assert {path.name: path.read_bytes() for path in case['root'].glob('*_START.json')} == case['all_start_bytes']


def no_new_worker(monkeypatch):
    from chanlun_trader import synthetic_batch_resources
    def forbidden(*args, **kwargs):
        pytest.fail('拒绝恢复时不可重新启动受限工作进程')
    monkeypatch.setattr(synthetic_batch_resources, 'run_bounded_worker', forbidden)


def test_public_resume_reuses_real_consumed_start_and_matches_continuous_account(tmp_path):
    continuous = approved_case(tmp_path / 'continuous')
    complete = continuous['service'].start(continuous['task']['task_id'])
    assert complete['status'] == 'ACCOUNT_VERIFIED', complete
    case = interrupted_case(tmp_path / 'interrupted')
    resumed = case['service'].resume(case['task']['task_id'])
    assert resumed['status'] == 'ACCOUNT_VERIFIED', resumed
    assert resumed['strategy_qualified'] is False and resumed['verification']['advance_allowed'] is True
    assert_original_consumption(case)
    resource = runner.read_json(case['root'] / (case['name'] + '_RESOURCE.json'))
    assert resource['returncode'] == 0 and resource['timed_out'] is False
    assert resource['resumed'] is True and resource['original_budget_reused'] is True
    evidence = runner.read_json(resource['resume_evidence'])
    assert evidence['start_identity'] == stable_hash(case['start'])
    assert evidence['checkpoint_identity'] == case['checkpoint']['receipt_hash']
    assert 0 < evidence['remaining_wall_seconds'] < case['job']['resources']['worker_seconds']
    assert evidence['spent_before_resume'] > 0 and evidence['budget_reused'] is True
    archive = Path(resource['resume_evidence']).parent
    assert runner.read_json(archive / (case['name'] + '_RESOURCE.json'))['returncode'] == 73
    result = runner.read_json(case['root'] / (case['name'] + '_RESULT.json'))
    reference = runner.read_json(continuous['root'] / (continuous['name'] + '_RESULT.json'))
    assert result['fills'] and result['metrics']['total_fees'] > 0
    for key in ('fills', 'daily_accounts', 'metrics', 'scan_days', 'final_account_checkpoint'):
        assert result[key] == reference[key], key
    verification = verify_job_evidence(case['path'], name=case['name'])
    assert verification['status'] == 'PASS', verification
    assert verification['advance_allowed'] is True
    assert verification['evidence_layers']['formal_qualification'] is False
    loaded = load_frozen_bundle(**case['job']['items'][case['name']]['loader_kwargs'])
    plan = case['job']['plans'][case['name']]
    audit = reconstruct_universe_account(loaded['frame'], plan['backend']['window'], result,
        initial_cash=plan['backend']['initial_cash'], costs=plan['backend']['costs'],
        strategy_id=case['name'], rule=case['request']['rule'])
    assert audit['daily_accounts'] == result['daily_accounts']
    assert audit['metrics'] == result['metrics'] and audit['strategy_qualified'] is False
    assert_original_consumption(case)


def test_public_resume_refuses_live_pid_without_new_worker_or_consumption(tmp_path, monkeypatch):
    case = interrupted_case(tmp_path)
    worker_path = case['root'] / (case['name'] + '_WORKER.json')
    worker = runner.read_json(worker_path)
    worker['pid'] = os.getpid()  # 真正仍存活的本测试进程；有意篡改临时反例。
    worker_path.write_text(json.dumps(worker), encoding='utf-8')
    no_new_worker(monkeypatch)
    with pytest.raises(PermissionError, match='UNIVERSE_RESUME_WORKER_STILL_ACTIVE'):
        case['service'].resume(case['task']['task_id'])
    assert_original_consumption(case)


def test_public_resume_refuses_expired_original_authorization(tmp_path, monkeypatch):
    case = interrupted_case(tmp_path)
    from chanlun_trader.research_factory import etf_account_governance_v1
    expires = datetime.fromisoformat(runner.read_json(case['root'] / 'CONFIRMATION.json')['expires_at'])
    class ExpiredClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return expires + timedelta(seconds=1)
    monkeypatch.setattr(etf_account_governance_v1, 'datetime', ExpiredClock)
    no_new_worker(monkeypatch)
    with pytest.raises(PermissionError, match='STRATEGY_EXPIRED'):
        case['service'].resume(case['task']['task_id'])
    assert_original_consumption(case)


def test_public_resume_does_not_reset_original_wall_time_after_downtime(tmp_path, monkeypatch):
    case = interrupted_case(tmp_path)
    from chanlun_trader.research_factory import universe_execution_recovery_v1
    start_time = datetime.fromisoformat(case['start']['started_at'])
    exhausted = start_time + timedelta(seconds=case['job']['resources']['worker_seconds'] + 1)
    class ExhaustedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return exhausted
    monkeypatch.setattr(universe_execution_recovery_v1, 'datetime', ExhaustedClock)
    no_new_worker(monkeypatch)
    with pytest.raises(PermissionError, match='UNIVERSE_RESUME_ORIGINAL_TIME_BOUND_EXHAUSTED'):
        case['service'].resume(case['task']['task_id'])
    assert_original_consumption(case)


def test_public_resume_refuses_changed_frozen_input_before_execution(tmp_path, monkeypatch):
    case = interrupted_case(tmp_path)
    source = Path(case['job']['items'][case['name']]['loader_kwargs']['path'])
    source.write_bytes(source.read_bytes() + b'\n')  # 语义未变仍不可改冻结原件身份。
    no_new_worker(monkeypatch)
    with pytest.raises(ValueError, match='UNIVERSE_FROZEN_SNAPSHOT_CHANGED'):
        case['service'].resume(case['task']['task_id'])
    assert_original_consumption(case)


@pytest.mark.parametrize('fault', ['EXIT', 'TIMEOUT'])
def test_public_resume_refuses_already_settled_failure_including_real_worker_timeout(tmp_path, monkeypatch, fault):
    case = interrupted_case(tmp_path, fault=fault)
    runner.service(case['job']).settle(case['name'], completed=False,
        seconds=case['resource']['elapsed_wall_seconds'], result_hash=None,
        error='SYNTHETIC_INTERRUPTION' if fault == 'EXIT' else 'SYNTHETIC_RESOURCE_TIMEOUT')
    settlement = case['root'] / (case['name'] + '_SETTLEMENT.json')
    before = settlement.read_bytes()
    no_new_worker(monkeypatch)
    with pytest.raises(PermissionError, match='JOB_FAILED_OR_SETTLEMENT_CONFLICT_NO_REPLAY'):
        case['service'].resume(case['task']['task_id'])
    assert settlement.read_bytes() == before
    assert_original_consumption(case)
