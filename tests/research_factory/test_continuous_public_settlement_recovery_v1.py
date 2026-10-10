"""停派结算的隔离元数据测试；合成死进程回执，不派发实际worker。"""
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from chanlun_trader.research_factory.continuous_submission_v1 import reconcile_submission
from chanlun_trader.research_factory.exploration_governance import immutable, read_json
from chanlun_trader.research_factory.universe_compute_governance_v1 import UniverseComputeGovernanceV1
from test_continuous_submission_v1 import continuous_public_case


def preparation(tmp_path, monkeypatch):
    service, request, campaign, accesses, _ = continuous_public_case(tmp_path)
    preview = service.preview(request)
    task = service.freeze(request, preview['preview_identity'])
    intent = read_json(service.root / 'continuous_intents' / (task['task_id'] + '.json'))
    root = service.root / 'signal-scans' / intent['scan_id']
    scan = read_json(root / 'SCAN_INTENT.json')
    meter = UniverseComputeGovernanceV1(root / 'COMPUTE', scan['compute_authority'],
                                        scan['preview']['request'], 'PREPARATION')
    meter.start()
    dispatch = meter.dispatch()
    immutable(root / 'PREPARE_000001_WORKER.json', {'pid': 2147483647, 'dispatch_id': dispatch['dispatch_id'],
                                                 'fixture': 'SYNTHETIC_DEAD_WORKER_METADATA'})
    from chanlun_trader import synthetic_batch_resources as resources
    from chanlun_trader.research_factory import universe_scan_service_v1 as scans
    from chanlun_trader.research_daemon_state import DaemonInstanceLockV1
    monkeypatch.setattr(resources, 'run_bounded_worker', lambda *a, **k: pytest.fail('结算不得派发worker'))
    monkeypatch.setattr(scans, 'run_bounded_worker', lambda *a, **k: pytest.fail('结算不得派发worker'))
    monkeypatch.setattr(DaemonInstanceLockV1, '_pid_alive', staticmethod(lambda pid: False))
    return service, task, campaign, accesses, root, scan, meter, dispatch


@pytest.mark.parametrize('stop', ['pause', 'expiry', 'revoke'])
def test_stopped_preparation_failure_settles_original_budget_once_with_zero_workers(tmp_path, monkeypatch, stop):
    service, task, campaign, accesses, root, scan, meter, dispatch = preparation(tmp_path, monkeypatch)
    immutable(root / 'PREPARE_000001_FAILURE.json', {'dispatch_id': dispatch['dispatch_id'],
        'scope_identity': scan['intent_identity'], 'fixture': 'SYNTHETIC_DEAD_WORKER_FAILURE'})
    if stop == 'expiry':
        from chanlun_trader.research_factory import research_campaign_v1 as campaigns
        class ExpiredClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.now(tz) + timedelta(hours=2)
        monkeypatch.setattr(campaigns, 'datetime', ExpiredClock)
    else:
        getattr(campaign, stop)('SYNTHETIC_STOP_BEFORE_SETTLEMENT')
    result = reconcile_submission(service, task['task_id'])
    assert result['status'] == 'PUBLIC_RECONCILED' and result['dispatched_segments'] == 0
    state = UniverseComputeGovernanceV1(root / 'COMPUTE', scan['compute_authority'],
        scan['preview']['request'], 'PREPARATION', for_dispatch=False).status()
    assert state['pending'] is None and state['segments'][-1]['charge']['outcome'] == 'FAILED'
    assert state['charged_seconds'] == dispatch['upper_bound_seconds']
    view = campaign.peek_status()
    assert view['used']['data_experiments'] == 1 and view['reserved']['data_experiments'] == 0
    before = deepcopy(view)
    assert reconcile_submission(service, task['task_id'])['status'] == 'NO_PUBLIC_RECONCILIATION_PENDING'
    assert campaign.peek_status() == before and accesses == []
    assert len(list(root.glob('PREPARE_*_WORKER.json'))) == 1


def test_stopped_successful_prepare_segment_charges_only_original_continue(tmp_path, monkeypatch):
    service, task, campaign, accesses, root, scan, meter, dispatch = preparation(tmp_path, monkeypatch)
    immutable(root / 'PARENT' / 'INPUT.json', {'fixture': 'SYNTHETIC_COMMITTED_PREPARE_OUTPUT'})
    immutable(root / 'PREPARE_000001_RESOURCE.json', {'dispatch_id': dispatch['dispatch_id'], 'phase': 'PREPARE',
        'returncode': 75, 'timed_out': False, 'elapsed_wall_seconds': 2.5})
    campaign.pause('SYNTHETIC_PAUSE_AFTER_PREPARE')
    result = reconcile_submission(service, task['task_id'])
    assert result['dispatched_segments'] == 0 and result['result']['complete'] is False
    offline = UniverseComputeGovernanceV1(root / 'COMPUTE', scan['compute_authority'],
        scan['preview']['request'], 'PREPARATION', for_dispatch=False)
    assert offline.status()['charged_seconds'] == 2.5
    before = deepcopy(campaign.peek_status())
    reconcile_submission(service, task['task_id'])
    assert campaign.peek_status() == before and accesses == []
    for operation in (offline.start, offline.dispatch):
        with pytest.raises(PermissionError, match='RECONCILIATION_ONLY'):
            operation()
    assert not (root / 'PREPARE_000002_WORKER.json').exists()


def test_active_prepare_worker_remains_unsettled_and_is_never_repeated(tmp_path, monkeypatch):
    service, task, campaign, _, root, _, _, _ = preparation(tmp_path, monkeypatch)
    from chanlun_trader.research_daemon_state import DaemonInstanceLockV1
    monkeypatch.setattr(DaemonInstanceLockV1, '_pid_alive', staticmethod(lambda pid: True))
    campaign.revoke('SYNTHETIC_ACTIVE_WORKER_STOP')
    before = deepcopy(campaign.peek_status())
    with pytest.raises(PermissionError, match='WORKER_STILL_ACTIVE'):
        reconcile_submission(service, task['task_id'])
    assert campaign.peek_status() == before and not (root / 'PREPARE_000002_WORKER.json').exists()


@pytest.mark.parametrize('stage,member', [('VERIFICATION', None), ('REPORT', 'BASE')])
def test_stopped_compute_success_settles_original_stage_without_next_member(tmp_path, monkeypatch, stage, member):
    """仅计算结算 primitive；合成宿主 job 不声明为正式账户或行情原件。"""
    from scripts import run_strategy_account_v1 as runner
    from chanlun_trader.research_factory.continuous_submission_v1 import continuous_authority
    from chanlun_trader.research_daemon_state import DaemonInstanceLockV1
    from chanlun_trader import synthetic_batch_resources as resources
    service, request, campaign, accesses, _ = continuous_public_case(tmp_path)
    request = service.preview(request)['request']
    authority = continuous_authority(service, request)
    root = tmp_path / 'SYNTHETIC_COMPUTE_METADATA_ONLY'
    # 这是宿主结算隔离测试，未构造或冒充正式 UNIVERSE_ACCOUNT_BACKEND_V2 账户。
    plans = {name: {'runtime': {}, 'backend': {'backend': 'SYNTHETIC_METADATA_ONLY'}}
             for name in ('BASE', 'STRESS')}
    job = {'root': str(root), 'plans': plans, 'items': {name: {} for name in plans},
           'source_hashes': {str(Path(__file__).resolve()): runner.sha(__file__)}}
    path = root / 'JOB.json'
    immutable(path, job)
    folder = root / ('COMPUTE_' + stage)
    immutable(folder / 'SCOPE.json', {'job_sha256': runner.sha(path), 'stage': stage,
        'authority': authority, 'request': request})
    meter = UniverseComputeGovernanceV1(folder, authority, request, stage)
    meter.start(); dispatch = meter.dispatch()
    immutable(folder / 'SEGMENT_000001_WORKER.json', {'pid': 2147483647,
        'dispatch_id': dispatch['dispatch_id'], 'fixture': 'SYNTHETIC_DEAD_WORKER_METADATA'})
    output = folder / ('RESULT_' + member + '.json' if member else 'RESULT.json')
    immutable(output, {'fixture': 'SYNTHETIC_COMMITTED_COMPUTE_OUTPUT'})
    immutable(folder / 'SEGMENT_000001_STATUS.json', {'state': 'COMPLETED', 'member': member,
        'dispatch_id': dispatch['dispatch_id'], 'result_sha256': runner.sha(output)})
    immutable(folder / 'SEGMENT_000001_RESOURCE.json', {'dispatch_id': dispatch['dispatch_id'],
        'member': member, 'returncode': 0, 'timed_out': False, 'elapsed_wall_seconds': 3.25})
    monkeypatch.setattr(resources, 'run_bounded_worker', lambda *a, **k: pytest.fail('结算不得派发worker'))
    monkeypatch.setattr(DaemonInstanceLockV1, '_pid_alive', staticmethod(lambda pid: False))
    campaign.revoke('SYNTHETIC_COMPUTE_STOP')
    runner.reconcile_long_horizon_compute(path, stage)
    offline = UniverseComputeGovernanceV1(folder, authority, request, stage, for_dispatch=False)
    expected = 'COMPLETED' if stage == 'VERIFICATION' else 'CONTINUE'
    assert offline.status()['segments'][-1]['charge']['outcome'] == expected
    assert offline.status()['charged_seconds'] == 3.25
    before = deepcopy(campaign.peek_status())
    runner.reconcile_long_horizon_compute(path, stage)
    assert campaign.peek_status() == before and accesses == []
    assert not (folder / 'SEGMENT_000002_WORKER.json').exists()


@pytest.mark.parametrize('stop', ['pause', 'expiry', 'revoke'])
@pytest.mark.parametrize('host_charged', [False, True])
def test_account_reconcile_only_closes_original_failure_once_without_execution(tmp_path, monkeypatch, stop, host_charged):
    """账户恢复单元测试：真实消费账本，合成冻结计划和死 worker 元数据。"""
    from scripts import run_strategy_account_v1 as runner
    from chanlun_trader.research_factory.common import stable_hash
    from chanlun_trader.research_factory.etf_account_governance_v1 import StrategyBatchGovernanceV1
    from chanlun_trader.research_factory.research_campaign_v1 import ResearchCampaignV1
    from chanlun_trader.research_factory.universe_execution_recovery_v1 import resume_long_horizon_job
    from chanlun_trader.research_daemon_state import DaemonInstanceLockV1
    from test_universe_execution_profile_v1 import campaign_config, profile
    resource = profile()
    (tmp_path / 'parent').mkdir()
    campaign = ResearchCampaignV1.create(tmp_path / 'parent', campaign_config(resource))
    root = tmp_path / 'account'
    item = {'execution_profile': resource,
            'backend_options': {'checkpoint_path': str(root / 'FIXED_STATE.json')}}
    plan = {'strategy': {'strategy_id': 'FIXED'}, 'runtime': item,
            'backend': {'backend': 'UNIVERSE_ACCOUNT_BACKEND_V2'}}
    plan['plan_id'] = stable_hash(plan)
    campaign.reserve_operation(operation_id='account', batch_id='batch', stage='EXPLORATION', kind='ACCOUNT',
        subject_identity=plan['plan_id'], upper_bounds={'account_jobs': 1, 'wall_seconds': resource['total_seconds']},
        execution_profile=resource)
    job = {'root': str(root), 'plans': {'FIXED': plan}, 'items': {'FIXED': item}, 'resources': resource,
           'budget_path': str(tmp_path / 'budget.json'), 'objective_id': 'OBJECTIVE', 'input_identity': 'SYNTHETIC'}
    path = root / 'JOB.json'; immutable(path, job)
    frozen_sha = runner.sha(path)
    # 此测试只隔离恢复和预算；正式 source/资格原件由公共 V4 worker 集成测试核对。
    def synthetic_frozen_source_check(value):
        assert value == job and runner.sha(path) == frozen_sha
    monkeypatch.setattr(runner, 'validate_sources', synthetic_frozen_source_check)
    monkeypatch.setattr(runner, 'execute_accounts', lambda *a, **k: pytest.fail('reconcile_only 不得执行账户'))
    monkeypatch.setattr(DaemonInstanceLockV1, '_pid_alive', staticmethod(lambda pid: False))
    gov = StrategyBatchGovernanceV1(root, job['budget_path'], job['objective_id'], job['plans'])
    gov.confirm_campaign_scope(campaign, {'FIXED': 'account'}, {'input_identity': 'SYNTHETIC',
        'novelty': {'FIXED': {'allowed': True, 'plan_id': plan['plan_id']}}})
    gov.start('FIXED'); dispatch = gov.start_segment('FIXED')
    prefix = root / 'FIXED_SEGMENT_000001'
    immutable(str(prefix) + '_WORKER.json', {'pid': 2147483647, 'dispatch_id': dispatch['dispatch_id']})
    failure = Path(str(prefix) + '_FAILURE.json')
    immutable(failure, {'dispatch_id': dispatch['dispatch_id'], 'scope_identity': frozen_sha,
        'fixture': 'SYNTHETIC_KNOWN_WORKER_FAILURE'})
    if host_charged:
        gov.end_segment('FIXED', 1, evidence_identity=runner.sha(failure), outcome='FAILED')
    if stop == 'expiry':
        from chanlun_trader.research_factory import research_campaign_v1 as campaigns
        class ExpiredClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2050, 1, 1, tzinfo=tz)
        monkeypatch.setattr(campaigns, 'datetime', ExpiredClock)
    else:
        getattr(campaign, stop)('SYNTHETIC_ACCOUNT_STOP')
    try:
        result = resume_long_horizon_job(path, job, reconcile_only=True)
        assert host_charged and result['dispatched_segments'] == 0
    except PermissionError as exc:
        assert not host_charged and 'KNOWN_FAILURE_NO_RETRY' in str(exc)
    before = deepcopy(campaign.peek_status())
    assert before['used']['account_jobs'] == 1 and before['reserved']['account_jobs'] == 0
    assert before['used']['wall_seconds'] == dispatch['upper_bound_seconds']
    assert read_json(root / 'FIXED_SETTLEMENT.json')['completed'] is False
    with pytest.raises(PermissionError, match='FAILED_OR_SETTLEMENT_CONFLICT'):
        resume_long_horizon_job(path, job, reconcile_only=True)
    assert campaign.peek_status() == before
    assert not (root / 'FIXED_SEGMENT_000002_DISPATCH.json').exists()
