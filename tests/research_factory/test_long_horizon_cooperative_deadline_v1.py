"""耗时加载与多个评价子阶段共享真实 worker 截止，不重新获得运行时间。"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import run_strategy_account_v1 as runner
from chanlun_trader.research_factory.universe_account_backend_v2 import SegmentBoundary
from chanlun_trader.research_factory.universe_execution_profile_v1 import CONTINUOUS_PROFILE, SEGMENTED_PROFILE


def clock(monkeypatch):
    value = SimpleNamespace(now=0.)
    monkeypatch.setattr(runner.time, 'monotonic', lambda: value.now)
    monkeypatch.setattr(runner, 'WORKER_STARTED_AT', 0., raising=False)
    return value


def account_worker(tmp_path, monkeypatch, timer, *, startup=0., load_seconds=0., upper=900., continuous=False):
    root = tmp_path / 'account'
    root.mkdir()
    frozen = {'plan_id': 'FROZEN'}
    item = {'factory': 'fixture:strategy', 'loader': 'fixture:loader', 'loader_kwargs': {},
            'backend_options': {'checkpoint_path': str(root / 'CHECKPOINT.json')}}
    job = {'root': str(root), 'plans': {'FIXED': frozen}, 'items': {'FIXED': item},
           'input_identity': 'INPUT', 'resources': {
               'profile_id': CONTINUOUS_PROFILE if continuous else SEGMENTED_PROFILE,
               'purpose': 'ENGINEERING_CONTINUOUS_REFERENCE' if continuous else 'RESEARCH_ACCOUNT'}}
    path = root / 'JOB.json'
    path.write_text(json.dumps(job), encoding='utf-8')
    pending = {'segment_number': 1, 'dispatch_id': 'DISPATCH', 'upper_bound_seconds': upper}
    gov = SimpleNamespace(segment_status=lambda name: {'pending': pending, 'profile': {'memory_mib': 2048}},
                          active_execution=lambda name: {'input_identity': 'INPUT'})
    monkeypatch.setattr(runner, 'validate_sources', lambda value: None)
    monkeypatch.setattr(runner, 'service', lambda value: gov)
    monkeypatch.setattr(runner, 'HANDSHAKE', {'execution': {
        'purpose': 'FIXED', 'segment_number': 1, 'dispatch_id': 'DISPATCH'},
        'memory_mib': 2048, 'wall_seconds': upper - min(10, upper / 2)})
    observed = {'loader_calls': 0, 'account_calls': 0}

    def factory():
        timer.now += startup
        return SimpleNamespace(strategy_id='FIXED', payload={},
                               requirements=SimpleNamespace(fields=[], warmup_sessions=0))

    def loader():
        observed['loader_calls'] += 1
        timer.now += load_seconds
        return {'input_identity': 'INPUT', 'frame': {}, 'actions': []}

    backend = SimpleNamespace(initial_cash=50000, costs={}, window={})
    monkeypatch.setattr(runner, 'resolve', lambda name: {'fixture:strategy': factory, 'fixture:loader': loader}[name])
    monkeypatch.setattr(runner, 'backend_for', lambda strategy, options: backend)
    monkeypatch.setattr(runner, 'prepare', lambda *args: frozen)

    def run(*args, **kwargs):
        observed['account_calls'] += 1
        observed['remaining'] = backend.segment_seconds
        raise SegmentBoundary('TEST_COMPLETE_DAY_BOUNDARY')

    monkeypatch.setattr(runner, 'run', run)
    return path, observed


def test_slow_loader_and_strategy_import_share_the_worker_clock(tmp_path, monkeypatch):
    timer = clock(monkeypatch)
    path, observed = account_worker(tmp_path, monkeypatch, timer, startup=20, load_seconds=570)
    assert runner._long_horizon_worker(path, 'FIXED', 1) == 75
    assert observed == {'loader_calls': 1, 'account_calls': 1, 'remaining': 240.}


def test_short_original_dispatch_does_not_get_a_new_normal_segment(tmp_path, monkeypatch):
    timer = clock(monkeypatch)
    path, observed = account_worker(tmp_path, monkeypatch, timer, startup=1, load_seconds=1, upper=75)
    assert runner._long_horizon_worker(path, 'FIXED', 1) == 75
    assert observed['remaining'] == 3.


def test_public_plan_recheck_is_charged_before_backend_starts(tmp_path, monkeypatch):
    timer = clock(monkeypatch)
    path, _ = account_worker(tmp_path, monkeypatch, timer, startup=20, load_seconds=570)
    remaining = []

    def public_run(strategy, backend, **kwargs):
        timer.now += 30  # 公共入口自己的 prepare，仍在进入引擎前。
        kwargs['active_check']()
        remaining.append(backend.segment_seconds)
        timer.now = 840
        kwargs['active_check']()  # 日内授权检查不成为新的半日截止点。
        raise SegmentBoundary('TEST_COMPLETE_DAY_BOUNDARY')

    monkeypatch.setattr(runner, 'run', public_run)
    assert runner._long_horizon_worker(path, 'FIXED', 1) == 75
    assert remaining == [210.]


def test_completion_tail_rechecks_remaining_before_its_own_audit(tmp_path, monkeypatch):
    timer = clock(monkeypatch)
    path, _ = account_worker(tmp_path, monkeypatch, timer, startup=20, load_seconds=570)
    (path.parent / 'FIXED_RESULT.json').write_text('{}', encoding='utf-8')

    def completion(*args):
        timer.now += 10
        return {}

    def own_prepare(*args, **kwargs):
        timer.now += 20
        return SimpleNamespace(bundle={}, window={})

    observed = []

    def reconstruct(*args, **kwargs):
        observed.append(kwargs['segment_seconds'])
        raise SegmentBoundary('TEST_TAIL_AUDIT_BOUNDARY')

    monkeypatch.setattr(runner, 'completion_tail_result', completion)
    monkeypatch.setattr('chanlun_trader.research_factory.universe_account_inputs_v1._prepare_owned_universe_account_inputs_v1',
                        own_prepare)
    monkeypatch.setattr('chanlun_trader.research_factory.universe_execution_artifacts_v1.hydrated_result', lambda value: value)
    monkeypatch.setattr('chanlun_trader.research_factory.universe_evidence_v1.reconstruct_universe_account', reconstruct)
    assert runner._long_horizon_worker(path, 'FIXED', 1) == 75
    assert observed == [210.]


@pytest.mark.parametrize('startup,load_seconds,loader_calls', [(0, 830, 1), (840, 0, 0)])
def test_elapsed_cutoff_exits_before_new_account_work(tmp_path, monkeypatch, startup, load_seconds, loader_calls):
    timer = clock(monkeypatch)
    path, observed = account_worker(tmp_path, monkeypatch, timer, startup=startup, load_seconds=load_seconds)
    assert runner._long_horizon_worker(path, 'FIXED', 1) == 75
    assert observed['loader_calls'] == loader_calls
    assert observed['account_calls'] == 0
    status = runner.read_json(path.parent / 'FIXED_SEGMENT_000001_STATUS.json')
    assert status['state'] == 'CONTINUE'
    assert status['reason'] == 'UNIVERSE_WORKER_COOPERATIVE_DEADLINE'
    assert not (path.parent / 'FIXED_RESULT.json').exists()


def test_continuous_engineering_reference_keeps_no_cooperative_account_cutoff(tmp_path, monkeypatch):
    timer = clock(monkeypatch)
    path, observed = account_worker(tmp_path, monkeypatch, timer, load_seconds=840, continuous=True)
    assert runner._long_horizon_worker(path, 'FIXED', 1) == 75
    assert observed['remaining'] is None and observed['account_calls'] == 1


@pytest.mark.parametrize('upper', [50., 70.])
def test_final_allowance_with_no_commit_reserve_fails_without_empty_continuation(tmp_path, monkeypatch, upper):
    timer = clock(monkeypatch)
    path, observed = account_worker(tmp_path, monkeypatch, timer, upper=upper)
    with pytest.raises(PermissionError, match='JOB_LONG_HORIZON_COOPERATIVE_WINDOW_EXHAUSTED'):
        runner.long_horizon_worker(path, 'FIXED', 1)
    assert observed['loader_calls'] == observed['account_calls'] == 0
    failure = runner.read_json(path.parent / 'FIXED_SEGMENT_000001_FAILURE.json')
    assert failure['exception_type'] == 'PermissionError' and failure['automatic_retry'] is False
    assert not (path.parent / 'FIXED_SEGMENT_000001_STATUS.json').exists()


def compute_worker(tmp_path, monkeypatch, timer, stage, *, members=('FIXED',), load_seconds=570):
    root = tmp_path / 'account'
    folder = root / ('COMPUTE_' + stage)
    folder.mkdir(parents=True)
    items = {name: {'factory': 'fixture:strategy', 'factory_kwargs': {}, 'loader': 'fixture:loader',
                   'loader_kwargs': {}, 'backend_options': {'window': {}}} for name in members}
    job = {'root': str(root), 'plans': {name: {} for name in members}, 'items': items,
           'input_identity': 'INPUT', 'observation_plan': {}}
    path = root / 'JOB.json'
    path.write_text(json.dumps(job), encoding='utf-8')
    scope = {'job_sha256': runner.sha(path), 'stage': stage, 'authority': {}, 'request': {}}
    (folder / 'SCOPE.json').write_text(json.dumps(scope), encoding='utf-8')
    pending = {'number': 1, 'dispatch_id': 'DISPATCH', 'upper_bound_seconds': 900}
    meter = SimpleNamespace(status=lambda: {'pending': pending}, active=lambda: None,
                            profile={'memory_mib': 2048, 'profile_id': SEGMENTED_PROFILE},
                            binding={'compute_identity': 'COMPUTE'})
    monkeypatch.setattr('chanlun_trader.research_factory.universe_compute_governance_v1.UniverseComputeGovernanceV1',
                        lambda *args: meter)
    monkeypatch.setattr(runner, 'validate_sources', lambda value: None)
    member = members[0] if stage == 'REPORT' else None
    monkeypatch.setattr(runner, 'HANDSHAKE', {'execution': {
        'purpose': stage, 'compute_identity': 'COMPUTE', 'segment_number': 1, 'dispatch_id': 'DISPATCH',
        'scope_sha256': runner.sha(folder / 'SCOPE.json'), 'member': member},
        'memory_mib': 2048, 'wall_seconds': 890})
    for name in members:
        (root / (name + '_RESULT.json')).write_text('{}', encoding='utf-8')
    (root / 'VERIFICATION.json').write_text(json.dumps({
        'job_sha256': runner.sha(path), 'advance_allowed': True, 'items': {
            name: {'result_sha256': runner.sha(root / (name + '_RESULT.json'))} for name in members}}), encoding='utf-8')
    observed = {'loader_calls': 0, 'pending': pending}

    def loader():
        observed['loader_calls'] += 1
        timer.now += load_seconds
        return {'frame': {}}

    strategy = SimpleNamespace(rule_identity='RULE', requirements=SimpleNamespace(fields=[], warmup_sessions=0))
    monkeypatch.setattr(runner, 'resolve', lambda name: {
        'fixture:strategy': lambda: strategy, 'fixture:loader': loader}[name])
    inputs = SimpleNamespace(window={'calendar': [20240102, 20240103], 'account_start': 20240102})
    monkeypatch.setattr('chanlun_trader.research_factory.universe_account_inputs_v1._prepare_owned_universe_account_inputs_v1',
                        lambda *args, **kwargs: inputs)
    monkeypatch.setattr('chanlun_trader.research_factory.universe_execution_artifacts_v1.hydrated_result', lambda value: value)
    return path, member, observed


def test_independent_members_get_one_cold_load_per_worker(tmp_path, monkeypatch):
    timer = clock(monkeypatch)
    path, member, observed = compute_worker(tmp_path, monkeypatch, timer, 'VERIFICATION', members=('FIRST', 'SECOND'))
    remaining = []

    def verify(*args, **kwargs):
        remaining.append(kwargs['segment_seconds'])
        assert kwargs['segment_deadline'] == 830.
        timer.now += 600
        return {'advance_allowed': True}

    monkeypatch.setattr('chanlun_trader.research_factory.research_evidence_v1.verify_job_evidence', verify)
    assert runner._long_horizon_compute_worker(path, 'VERIFICATION', 1, member) == 75
    assert remaining == [830.]
    assert (path.parent / 'COMPUTE_VERIFICATION/VERIFIED_FIRST.json').exists()
    assert not (path.parent / 'COMPUTE_VERIFICATION/VERIFIED_SECOND.json').exists()
    timer.now = 0.
    observed['pending']['number'] = 2
    runner.HANDSHAKE['execution']['segment_number'] = 2
    assert runner._long_horizon_compute_worker(path, 'VERIFICATION', 2, member) == 0
    assert remaining == [830., 830.]


def test_expired_independent_verification_does_not_read_result(tmp_path, monkeypatch):
    timer = clock(monkeypatch)
    path, member, _ = compute_worker(tmp_path, monkeypatch, timer, 'VERIFICATION')
    monkeypatch.setattr(runner, 'validate_sources', lambda value: setattr(timer, 'now', 830.))
    monkeypatch.setattr('chanlun_trader.research_factory.research_evidence_v1.verify_job_evidence',
                        lambda *args, **kwargs: pytest.fail('截止后不能用新的 .01 秒开始核账'))
    assert runner._long_horizon_compute_worker(path, 'VERIFICATION', 1, member) == 75
    assert runner.read_json(path.parent / 'COMPUTE_VERIFICATION/SEGMENT_000001_STATUS.json')['state'] == 'CONTINUE'


def test_report_observations_and_funnel_do_not_reset_remaining_time(tmp_path, monkeypatch):
    timer = clock(monkeypatch)
    path, member, _ = compute_worker(tmp_path, monkeypatch, timer, 'REPORT')
    remaining = []

    def report(*args, **kwargs):
        remaining.append(kwargs['segment_seconds'])
        timer.now += 40
        return {}

    def funnel(*args, **kwargs):
        remaining.append(kwargs['segment_seconds'])
        raise SegmentBoundary('TEST_FUNNEL_COMPLETE_DAY')

    monkeypatch.setattr('chanlun_trader.research_factory.universe_research_report_v2.build_research_reports', report)
    monkeypatch.setattr('chanlun_trader.research_factory.universe_signal_funnel_v1.build_signal_funnel_stream_v1', funnel)
    monkeypatch.setattr('chanlun_trader.research_factory.universe_signal_funnel_v1.funnel_day_packets_v1', lambda *args: [])
    assert runner._long_horizon_compute_worker(path, 'REPORT', 1, member) == 75
    assert remaining == [260., 220.]


@pytest.mark.parametrize('load_seconds,report_seconds', [(830, 0), (570, 260)])
def test_report_deadline_does_not_start_another_substage(tmp_path, monkeypatch, load_seconds, report_seconds):
    timer = clock(monkeypatch)
    path, member, observed = compute_worker(tmp_path, monkeypatch, timer, 'REPORT', load_seconds=load_seconds)
    reports = []

    def report(*args, **kwargs):
        reports.append(kwargs['segment_seconds'])
        timer.now += report_seconds
        return {}

    monkeypatch.setattr('chanlun_trader.research_factory.universe_research_report_v2.build_research_reports', report)
    monkeypatch.setattr('chanlun_trader.research_factory.universe_signal_funnel_v1.build_signal_funnel_stream_v1',
                        lambda *args, **kwargs: pytest.fail('截止后不能开始漏斗阶段'))
    monkeypatch.setattr('chanlun_trader.research_factory.universe_signal_funnel_v1.funnel_day_packets_v1', lambda *args: [])
    assert runner._long_horizon_compute_worker(path, 'REPORT', 1, member) == 75
    assert reports == ([] if load_seconds == 830 else [260.])
    assert observed['loader_calls'] == 1
    assert runner.read_json(path.parent / 'COMPUTE_REPORT/SEGMENT_000001_STATUS.json')['state'] == 'CONTINUE'


@pytest.fixture
def settled_long_job(tmp_path):
    from test_long_horizon_failure_recovery_v1 import approved_job
    _, path, job, _ = approved_job(tmp_path)
    runner.execute_long_horizon_accounts(path)
    return path, next(iter(job['plans']))


@pytest.mark.parametrize('load_seconds', [570, 830])
def test_verifier_strict_loader_and_audit_share_absolute_deadline(settled_long_job, monkeypatch, load_seconds):
    from chanlun_trader.research_factory import strategy_submission_v1 as submission
    from chanlun_trader.research_factory.research_evidence_v1 import verify_job_evidence
    path, name = settled_long_job
    timer = clock(monkeypatch)
    original = submission.load_frozen_qualified_bundle

    def slow_loader(**kwargs):
        value = original(**kwargs)
        timer.now += load_seconds
        return value

    monkeypatch.setattr(submission, 'load_frozen_qualified_bundle', slow_loader)
    observed = []

    def reconstruct(*args, **kwargs):
        observed.append(kwargs['segment_seconds'])
        return {'test_fixture_only': True}

    monkeypatch.setattr('chanlun_trader.research_factory.universe_evidence_v1.reconstruct_universe_account', reconstruct)
    if load_seconds == 830:
        with pytest.raises(SegmentBoundary, match='UNIVERSE_VERIFICATION_COOPERATIVE_DEADLINE'):
            verify_job_evidence(path, name=name, segment_seconds=830., segment_deadline=830.)
        assert observed == []
    else:
        verified = verify_job_evidence(path, name=name, segment_seconds=830., segment_deadline=830.)
        assert verified['status'] == 'PASS', verified
        assert observed == [260.]


def test_existing_verifier_without_absolute_deadline_keeps_its_call_contract(settled_long_job, monkeypatch):
    from chanlun_trader.research_factory.research_evidence_v1 import verify_job_evidence
    path, name = settled_long_job
    observed = []
    monkeypatch.setattr('chanlun_trader.research_factory.universe_evidence_v1.reconstruct_universe_account',
                        lambda *args, **kwargs: observed.append(kwargs['segment_seconds']) or {})
    assert verify_job_evidence(path, name=name, segment_seconds=123.)['status'] == 'PASS'
    assert observed == [123.]
