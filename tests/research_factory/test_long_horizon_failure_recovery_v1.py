"""真实受限进程与宿主尾部崩溃；失败不得变成免费重试或成功。"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

import pytest

from chanlun_trader.research_factory.budget import BudgetExhaustedError
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.research_campaign_v1 import ResearchCampaignV1
from chanlun_trader.research_factory.universe_execution_profile_v1 import segment_charge, worker_wall_seconds
from test_long_horizon_public_submission_v1 import long_public_case
from test_universe_compute_governance_v1 import meter
from test_universe_campaign_segment_mirror_v1 import account_campaign
from test_universe_execution_profile_v1 import campaign_config, profile


class HostCrash(BaseException):
    pass


def approved_job(tmp_path):
    from scripts import run_strategy_account_v1 as runner
    service, request, authority, _ = long_public_case(tmp_path)
    preview = service.preview(request)
    frozen = service.freeze(request, preview['preview_identity'])
    service.approve(frozen['task_id'], preview['preview_identity'])
    job = runner.read_json(frozen['job_path'])
    return runner, frozen['job_path'], job, authority


def test_failed_known_overrun_is_recorded_without_expanding_success_bound(tmp_path):
    assert worker_wall_seconds(900) == 890
    assert worker_wall_seconds(.5) == .25
    with pytest.raises(PermissionError):
        segment_charge(901, 900, 'actual', outcome='COMPLETED')
    amount, basis = segment_charge(901, 900, 'actual', outcome='FAILED')
    assert amount == 901 and basis == 'MEASURED_FAILED_RESOURCE_BOUND_VIOLATION'
    gov, campaign = account_campaign(tmp_path)
    gov.start_segment('FIXED')
    gov.end_segment('FIXED', 1, seconds=901, evidence_identity='actual', outcome='FAILED')
    assert gov.segment_status('FIXED')['dispatch_overrun'] == 1
    gov.settle('FIXED', completed=False, seconds=901, result_hash=None, error='timeout')
    view = campaign.status()
    assert view['operations']['account']['actual']['wall_seconds'] == 901
    assert view['operations']['account']['status'] == 'FAILED' and view['paused']
    with pytest.raises(PermissionError):
        gov.start_segment('FIXED')


def test_parent_global_debt_keeps_exact_actual_and_terminal_pause(tmp_path):
    resource = profile()
    config = campaign_config(resource)
    config['resource_limits']['wall_seconds'] = resource['total_seconds']
    campaign = ResearchCampaignV1.create(tmp_path, config)
    campaign.reserve_operation(operation_id='op', batch_id='batch', stage='EXPLORATION', kind='ACCOUNT',
        subject_identity='fixed', upper_bounds={'account_jobs': 1, 'wall_seconds': 14400}, execution_profile=resource)
    campaign.start_operation('op')
    campaign.start_execution_segment('op', segment_number=1, profile=resource, upper_bound_seconds=900)
    campaign.end_execution_segment('op', segment_number=1, seconds=14401, evidence_identity='host', outcome='FAILED')
    campaign.settle_operation('op', actual={'account_jobs': 1, 'wall_seconds': 14401}, outcome='FAILED', evidence_identity='host')
    view = campaign.status()
    assert view['used']['wall_seconds'] == 14401
    assert view['authorization']['resource_limits']['wall_seconds'] == 14400
    assert view['resource_overrun']['wall_seconds'] == 1
    assert view['operations']['op']['resource_overrun']['wall_seconds'] == 1
    assert view['paused'] and view['operations']['op']['status'] == 'FAILED'
    campaign.resume('test cannot erase debt')
    assert campaign.status()['paused']


def test_parent_pause_does_not_block_account_or_compute_old_dispatch_charge(tmp_path):
    (tmp_path/'account_case').mkdir()
    gov, campaign = account_campaign(tmp_path/'account_case')
    gov.start_segment('FIXED')
    campaign.pause('worker already dispatched')
    gov.end_segment('FIXED', 1, seconds=10, evidence_identity='host', outcome='FAILED')
    gov.settle('FIXED', completed=False, seconds=10, result_hash=None, error='failed')
    assert campaign.status()['operations']['account']['status'] == 'FAILED'
    from test_universe_campaign_segment_mirror_v1 import meter as make_meter
    first = make_meter(tmp_path/'compute_case')
    auth = deepcopy(first.authority)
    config = campaign_config(first.profile)
    config['objective_id'] = 'FIXED'
    (tmp_path/'compute_parent').mkdir()
    parent = ResearchCampaignV1.create(tmp_path/'compute_parent', config)
    auth['account_authorization'] = {'campaign_ref': {'root': str(parent.root), 'authorization_id': parent.authorization_id}}
    compute = meter(tmp_path/'compute_case', authority=auth)
    compute.start(); dispatched = compute.dispatch(); parent.pause('already dispatched')
    compute.charge(dispatched['number'], seconds=12, evidence_identity='host', outcome='FAILED')
    assert compute.status()['pending'] is None
    assert parent.status()['operations'][compute.campaign_operation['operation_id']]['status'] == 'FAILED'


def test_same_authority_alias_cannot_reopen_compute_quota(tmp_path):
    from chanlun_trader.research_factory.universe_compute_governance_v1 import UniverseComputeGovernanceV1
    first = meter(tmp_path); first.start()
    request = {**first.request, 'authorization_ref': 'different_registered_alias'}
    second = UniverseComputeGovernanceV1(tmp_path/'other', first.authority, request, 'REPORT')
    assert second.budget_key == first.budget_key
    with pytest.raises(BudgetExhaustedError):
        second.start()


def test_real_timeout_and_host_cleanup_overrun_close_failed_segment(tmp_path, monkeypatch):
    import chanlun_trader.synthetic_batch_resources as resources
    import chanlun_trader.research_factory.universe_execution_profile_v1 as profiles
    runner, path, job, _ = approved_job(tmp_path)
    real_run = resources.run_bounded_worker
    monkeypatch.setattr(profiles, 'segment_allowance', lambda *args, **kwargs: .5)
    observed = {}
    def timeout_worker(command, **kwargs):
        assert kwargs['wall_seconds'] == .25
        result = real_run([sys.executable, '-c', 'import time; time.sleep(10)'], **kwargs)
        time.sleep(.6)  # 真实宿主收尾耗时，不能裁剪收费。
        observed.update(result)
        return result
    monkeypatch.setattr(resources, 'run_bounded_worker', timeout_worker)
    with pytest.raises(RuntimeError, match='WORKER_FAILED_NO_RETRY'):
        runner.execute_long_horizon_accounts(path)
    name = next(iter(job['plans']))
    state = runner.service(job).segment_status(name)
    assert observed['timed_out'] and state['pending'] is None
    assert state['charged_seconds'] > .5
    assert state['segments'][-1]['charge']['basis'] == 'MEASURED_FAILED_RESOURCE_BOUND_VIOLATION'
    settlement = runner.read_json(Path(job['root'])/(name+'_SETTLEMENT.json'))
    assert settlement['completed'] is False and settlement['wall_seconds'] == state['charged_seconds']


def test_original_result_survives_host_crash_then_bounded_tail_completion(tmp_path, monkeypatch):
    from chanlun_trader.research_factory.universe_execution_recovery_v1 import resume_universe_job
    runner, path, job, _ = approved_job(tmp_path)
    name = next(iter(job['plans'])); root = Path(job['root']); original_save = runner.save
    def crash_on_resource(target, value):
        if Path(target).name == name+'_SEGMENT_000001_RESOURCE.json':
            raise HostCrash('RESULT and STATUS committed; host receipt not committed')
        return original_save(target, value)
    with monkeypatch.context() as patch:
        patch.setattr(runner, 'save', crash_on_resource)
        with pytest.raises(HostCrash):
            runner.execute_long_horizon_accounts(path)
    original = (root/(name+'_RESULT.json')).read_bytes()
    snapshot = Path(job['items'][name]['backend_options']['checkpoint_path']).read_bytes()
    resume_universe_job(path)
    assert (root/(name+'_RESULT.json')).read_bytes() == original
    assert Path(job['items'][name]['backend_options']['checkpoint_path']).read_bytes() == snapshot
    state = runner.service(job).segment_status(name)
    assert [row['charge']['outcome'] for row in state['segments']] == ['CONTINUE', 'COMPLETED']
    assert state['segments'][0]['charge']['basis'] == 'UNKNOWN_CHARGED_DISPATCH_UPPER_BOUND'
    assert state['segments'][1]['charge']['basis'] == 'MEASURED_ACTIVE_WALL_SECONDS'
    assert len(list(root.glob(name+'_START.json'))) == 1


def test_known_worker_failure_without_host_resource_closes_no_retry(tmp_path, monkeypatch):
    from chanlun_trader.research_factory.universe_execution_recovery_v1 import resume_universe_job
    runner, path, job, _ = approved_job(tmp_path)
    root = Path(job['root']); name = next(iter(job['plans'])); gov = runner.service(job)
    gov.start(name)
    # 派发后撤销使实际子进程在读取行情前失败，仍必须形成带 dispatch 的失败原件。
    original = gov.start_segment
    def revoke_after_dispatch(member, *args, **kwargs):
        result = original(member, *args, **kwargs)
        runner.save(root/'REVOKED.json', {'reason': 'synthetic revocation after dispatch'})
        return result
    original_save = runner.save
    def host_crash(target, value):
        if Path(target).name == name+'_SEGMENT_000001_RESOURCE.json':
            raise HostCrash('known worker failure exists, host resource absent')
        return original_save(target, value)
    with monkeypatch.context() as patch:
        patch.setattr(type(gov), 'start_segment', lambda self, *args, **kwargs: revoke_after_dispatch(*args, **kwargs))
        patch.setattr(runner, 'save', host_crash)
        with pytest.raises(HostCrash):
            runner.execute_long_horizon_accounts(path, recover=True)
    failure = runner.read_json(root/(name+'_SEGMENT_000001_FAILURE.json'))
    assert failure['dispatch_id'] == gov.segment_status(name)['pending']['dispatch_id']
    with pytest.raises(PermissionError, match='KNOWN_FAILURE_NO_RETRY'):
        resume_universe_job(path)
    state = gov.segment_status(name)
    assert state['pending'] is None and len(state['segments']) == 1
    assert state['segments'][0]['charge']['outcome'] == 'FAILED'
    assert state['segments'][0]['charge']['basis'] == 'FAILED_UNMEASURED_CHARGED_DISPATCH_UPPER_BOUND'
    assert runner.read_json(root/(name+'_SETTLEMENT.json'))['completed'] is False


def test_expired_approval_still_charges_original_dead_worker_before_refusing_new_dispatch(tmp_path, monkeypatch):
    from chanlun_trader.research_factory.universe_execution_recovery_v1 import resume_universe_job
    import chanlun_trader.research_factory.etf_account_governance_v1 as governance
    runner, path, job, _ = approved_job(tmp_path)
    name = next(iter(job['plans'])); root = Path(job['root']); original_save = runner.save
    def host_crash(target, value):
        if Path(target).name == name+'_SEGMENT_000001_RESOURCE.json': raise HostCrash()
        return original_save(target, value)
    with monkeypatch.context() as patch:
        patch.setattr(runner, 'save', host_crash)
        with pytest.raises(HostCrash): runner.execute_long_horizon_accounts(path)
    class ExpiredClock(datetime):
        @classmethod
        def now(cls, tz=None): return datetime(2050, 1, 1, tzinfo=timezone.utc)
    with monkeypatch.context() as patch:
        patch.setattr(governance, 'datetime', ExpiredClock)
        # 公共 RESUME 控制记录只允许结清原用途，不自行派发过期 worker。
        runner.control_job(path, 'RESUME')
        with pytest.raises(PermissionError, match='EXPIRED'):
            resume_universe_job(path)
    state = runner.service(job).segment_status(name)
    assert state['pending'] is None and len(state['segments']) == 1
    assert state['charged_seconds'] == state['segments'][0]['dispatch']['upper_bound_seconds']
    assert (root/(name+'_SEGMENT_000001_RESUME.json')).is_file()
    assert not (root/(name+'_SEGMENT_000002_DISPATCH.json')).exists()


def test_report_failure_without_resource_is_failed_offline_not_retried(tmp_path, monkeypatch):
    from chanlun_trader.research_factory.universe_compute_governance_v1 import UniverseComputeGovernanceV1
    runner, path, job, authority = approved_job(tmp_path)
    root = Path(job['root']); folder = root/'COMPUTE_REPORT'; original_save = runner.save
    # 没有核验回执的实际 REPORT worker 必须拒绝，不能在回测前擅自读标签。
    request = {'authorization_ref': 'test', 'execution_profile': job['resources']}
    def host_crash(target, value):
        if Path(target).name == 'SEGMENT_000001_RESOURCE.json': raise HostCrash()
        return original_save(target, value)
    with monkeypatch.context() as patch:
        patch.setattr(runner, 'save', host_crash)
        with pytest.raises(HostCrash): runner.run_long_horizon_compute(path, authority, request, 'REPORT')
    failure = runner.read_json(folder/'SEGMENT_000001_FAILURE.json')
    assert failure['scope_identity'] == runner.sha(folder/'SCOPE.json')
    # Windows Job 关闭后 reader 的退出可能稍迟于 launcher 返回；先证明实际退出。
    from chanlun_trader.research_daemon_state import DaemonInstanceLockV1
    pids = (runner.read_json(folder/'SEGMENT_000001_WORKER.json')['pid'],
            runner.read_json(folder/'SEGMENT_000001_ACCESS.json')['reader_pid'])
    deadline = time.monotonic() + 5
    while any(DaemonInstanceLockV1._pid_alive(pid) for pid in pids) and time.monotonic() < deadline:
        time.sleep(.05)
    assert not any(DaemonInstanceLockV1._pid_alive(pid) for pid in pids), pids
    runner.save(folder/'REVOKED.json', {'reason': 'offline closing must remain allowed'})
    with pytest.raises(PermissionError, match='KNOWN_FAILURE_NO_RETRY'):
        runner.reconcile_long_horizon_compute(path, 'REPORT')
    meter = UniverseComputeGovernanceV1(folder, authority, request, 'REPORT')
    state = meter.status()
    assert state['pending'] is None and state['segments'][-1]['charge']['outcome'] == 'FAILED'
    assert state['segments'][-1]['charge']['basis'] == 'FAILED_UNMEASURED_CHARGED_DISPATCH_UPPER_BOUND'
    with pytest.raises(PermissionError): runner.run_long_horizon_compute(path, authority, request, 'REPORT')
    assert len(meter.status()['segments']) == 1


def test_preparation_failure_without_resource_is_archived_offline_no_retry(tmp_path, monkeypatch):
    import chanlun_trader.research_factory.universe_scan_service_v1 as scans
    from chanlun_trader.research_factory.universe_compute_governance_v1 import UniverseComputeGovernanceV1
    service, request, _, _ = long_public_case(tmp_path)
    preview = service.preview(request)
    original_dispatch = UniverseComputeGovernanceV1.dispatch; original_immutable = scans.immutable
    def revoke_after_dispatch(self):
        dispatch = original_dispatch(self)
        original_immutable(self.root/'REVOKED.json', {'reason': 'revoked after original dispatch'})
        return dispatch
    def host_crash(target, value):
        if Path(target).name == 'PREPARE_000001_RESOURCE.json': raise HostCrash()
        return original_immutable(target, value)
    with monkeypatch.context() as patch:
        patch.setattr(UniverseComputeGovernanceV1, 'dispatch', revoke_after_dispatch)
        patch.setattr(scans, 'immutable', host_crash)
        with pytest.raises(HostCrash): service.scan(request, preview['preview_identity'])
    root = next((service.root/'signal-scans').iterdir()); intent = scans.read_json(root/'SCAN_INTENT.json')
    failure = scans.read_json(root/'PREPARE_000001_FAILURE.json')
    assert failure['scope_identity'] == intent['intent_identity']
    result = scans.reconcile_long_preparation(root)
    assert result['status'] == 'SCAN_BLOCKED' and (root/'SCAN_RECEIPT.json').is_file()
    meter = UniverseComputeGovernanceV1(root/'COMPUTE', intent['compute_authority'], intent['preview']['request'], 'PREPARATION')
    state = meter.status()
    assert state['pending'] is None and state['segments'][-1]['charge']['outcome'] == 'FAILED'
    assert state['segments'][-1]['charge']['basis'] == 'FAILED_UNMEASURED_CHARGED_DISPATCH_UPPER_BOUND'
    assert service.scan(request, preview['preview_identity'])['recorded_only']
    assert len(meter.status()['segments']) == 1


def deployment_case(tmp_path, *, extra=None):
    from scripts.lifecycle_deployment_v2 import build_submission_service
    _, request, authority, _ = long_public_case(tmp_path)
    if extra: authority.update(extra)
    auth_path = tmp_path/'authority.json'; auth_path.write_text(json.dumps(authority), encoding='utf-8')
    config = {'version': 'FULL_UNIVERSE_DEPLOYMENT_V1', 'roots': {'data': 'data'},
        'datasets': [{'dataset_id': 'sample', 'root_id': 'data', 'manifest_path': 'manifest.json'}],
        'authorizations': {'test': {'path': 'authority.json', 'sha256': hashlib.sha256(auth_path.read_bytes()).hexdigest()}},
        'output_root': 'public'}
    service = build_submission_service(tmp_path, config)
    return service, request, config


def test_deployment_registered_reader_and_cli_freeze_accept_new_scope(tmp_path, capsys):
    from scripts.run_trusted_research_v1 import main
    from chanlun_trader.research_factory.universe_execution_profile_v1 import CONTINUOUS_PROFILE, ENGINEERING_PURPOSE, execution_profile
    engineering = execution_profile(CONTINUOUS_PROFILE, 20, ENGINEERING_PURPOSE)
    service, request, config = deployment_case(tmp_path, extra={'engineering_authorization': ENGINEERING_PURPOSE})
    assert service.authority('test')['engineering_authorization'] == ENGINEERING_PURPOSE
    preview = service.preview(request)
    config_path = tmp_path/'deployment.json'; config_path.write_text(json.dumps(config), encoding='utf-8')
    request_path = tmp_path/'request.json'; request_path.write_text(json.dumps(request), encoding='utf-8')
    code = main(['freeze', '--workspace-root', str(tmp_path), '--deployment', str(config_path),
        '--request', str(request_path), '--preview-identity', preview['preview_identity']])
    assert code == 0
    frozen = json.loads(capsys.readouterr().out)
    assert frozen['task_id'] and Path(frozen['job_path']).is_file()
    request['execution_profile'] = engineering
    with pytest.raises(PermissionError):
        service.preview(request)


def test_deployment_reader_still_rejects_unknown_authority_fields(tmp_path):
    service, _, _ = deployment_case(tmp_path, extra={'unregistered_fast_mode': True})
    with pytest.raises(PermissionError, match='AUTHORITY_FIELDS_INVALID'):
        service.authority('test')
