"""完整人工恢复场景中的首次启动截止与收费；不另造277日前缀或运行独立测试。"""
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import sys

import pytest


DOMAIN_IMPORT_CUTOFF_BOOTSTRAP = '''
import builtins
import json
import os
from pathlib import Path
import runpy
import sys
import time

original_argv = sys.argv[1:]
runner_path = Path(original_argv[0]).resolve(strict=True)
job_path = Path(original_argv[original_argv.index('--job') + 1]).resolve(strict=True)
real_import = builtins.__import__
real_monotonic = time.monotonic
fixture_clock = {'offset': 0., 'triggered': False}

def shifted_monotonic():
    return real_monotonic() + fixture_clock['offset']

def cutoff_during_domain_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name == 'chanlun_trader.research_factory.strategy_interface_v1' and not fixture_clock['triggered']:
        handshake = globals['HANDSHAKE']
        started = globals['WORKER_STARTED_AT']
        execution = handshake['execution']
        prefix = execution['purpose'] + '_SEGMENT_' + str(execution['segment_number']).zfill(6)
        marker = {
            'fixture_only': 'CHILD_MONOTONIC_ADVANCE_AT_REAL_DOMAIN_IMPORT',
            'module': name, 'runner_path': str(runner_path), 'job_path': str(job_path),
            'worker_started_at_monotonic': started,
            'deadline_monotonic': started + handshake['wall_seconds'] - 60,
            'offset_seconds': handshake['wall_seconds'],
            'real_monotonic_at_trigger': real_monotonic(),
            'reader_pid': os.getpid(), 'reader_parent_pid': os.getppid(),
            'handshake': handshake,
            'source_owner_price_validation_replaced': False,
            'supervisor_clock_and_limits_changed': False,
        }
        with (job_path.parent / (prefix + '_ARTIFICIAL_IMPORT_CUTOFF.json')).open('x', encoding='utf-8') as stream:
            json.dump(marker, stream, ensure_ascii=False, allow_nan=False)
        fixture_clock['triggered'] = True
        fixture_clock['offset'] = handshake['wall_seconds']
    return real_import(name, globals, locals, fromlist, level)

time.monotonic = shifted_monotonic
builtins.__import__ = cutoff_during_domain_import
sys.argv = original_argv
runpy.run_path(str(runner_path), run_name='__main__')
'''


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _ref(path):
    return {'path': str(path), 'sha256': _sha(path)}


def _write(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _save_supervisor_observation(folder, resource):
    # 在公共入口解析回执或断言前保留原始输出，实际 RESOURCE 仍由公共 runner 生成。
    metadata = {key: value for key, value in resource.items() if key not in ('stdout', 'stderr')}
    for channel in ('stdout', 'stderr'):
        raw = resource[channel]
        path = folder / ('SUPERVISOR_' + channel.upper() + '.bin')
        with path.open('xb') as stream:
            stream.write(raw)
        metadata[channel] = {**_ref(path), 'size_bytes': len(raw)}
    _write(folder / 'SUPERVISOR_RESOURCE.json', metadata)


def assert_public_startup_cutoff_charge(runner, service, job_path, task_id):
    """真实公共advance派发51并计量；返回后普通完整编排从52继续第278日。"""
    from chanlun_trader import synthetic_batch_resources as resources
    from chanlun_trader.research_factory.failed_account_recovery_v1 import CANONICAL_SECONDS
    from chanlun_trader.research_factory.run_budget import effective_execution_status
    from chanlun_trader.research_factory.universe_execution_profile_v1 import worker_wall_seconds

    job_path = Path(job_path).resolve(strict=True)
    job = runner.read_json(job_path)
    account = job_path.parent
    context = runner.validate_sources(job)
    assert context is not None and context.recovery_installed and not context.mirrors_pending()
    name = context.recovery.original_failed_account
    proof = context.recovery.proof()
    version = context.proof(name)
    gov = runner.service(job)
    state_before = gov.segment_status(name)
    campaign = context.campaign
    parent_before = campaign._peek_execution_status()
    receipt = _read(account / 'CONFIRMATION.json')
    mapping = receipt['source']['operation_ids']
    operation_id = mapping[name]
    operation_before = parent_before['operations'][operation_id]
    checkpoint = Path(job['items'][name]['backend_options']['checkpoint_path']).resolve(strict=True)
    checkpoint_before = _read(checkpoint)
    assert len(checkpoint_before['artifacts']['days']) == 277
    assert len(state_before['segments']) == len(operation_before['segments']) == 50
    assert state_before['segments'][-1]['charge']['outcome'] == 'FAILED'
    assert state_before['charged_seconds'] == operation_before['active_wall_seconds'] == CANONICAL_SECONDS
    assert state_before['next_segment_number'] == 51 and state_before['pending'] is None
    assert operation_before['status'] == 'FAILED' and effective_execution_status(operation_before) == 'RUNNING'
    assert operation_before['actual']['wall_seconds'] == 42340
    assert state_before['profile']['worker_seconds'] == 900
    assert state_before['profile']['memory_mib'] == 2048
    assert state_before['profile']['threads'] == state_before['profile']['concurrency'] == 1
    assert not (account / (name + '_RECOVERY_START.json')).exists()
    assert not (account / (name + '_SEGMENT_000051_DISPATCH.json')).exists()

    original_files = {path: _sha(path) for path in account.rglob('*')
        if path.is_file() and not path.name.endswith('.lock')}
    parent_ledger = campaign.budget_path.with_name('run_budget_events.jsonl')
    ledger_before = parent_ledger.read_bytes()
    folder = Path(runner.REPO) / 'reports' / 'synthetic_failed_recovery_startup_charge'
    folder.mkdir(parents=True)
    bootstrap = folder / 'domain_import_cutoff_bootstrap.py'
    with bootstrap.open('x', encoding='utf-8') as stream:
        stream.write(DOMAIN_IMPORT_CUTOFF_BOOTSTRAP)
    _write(folder / 'BEFORE.json', {
        'fixture_only': 'FULL_SYNTHETIC_RECOVERY_ORIGINAL_277_DAY_PREFIX_REUSED',
        'job_ref': _ref(job_path), 'checkpoint_ref': _ref(checkpoint),
        'original_account_files': [_ref(path) for path in sorted(original_files)],
        'parent_ledger_ref': _ref(parent_ledger), 'parent_operation': deepcopy(operation_before),
        'child_state': deepcopy(state_before), 'failed_account_recovery_proof': proof,
        'expected_first_recovery_segment': 51, 'expected_next_economic_day_ordinal': 278,
    })

    real_run_bounded_worker = resources.run_bounded_worker
    launches = []

    def actual_import_cutoff_worker(command, **options):
        # 只在测试编排中加runpy引导器；原已部署入口、OS限制、scope及宿主计量全部沿用。
        assert not launches
        assert command == [sys.executable, str(Path(runner.__file__).resolve()), '--worker', name,
            '--job', str(job_path), '--segment', '51']
        assert Path(options['root']).resolve() == Path(runner.REPO).resolve()
        assert options['memory_mib'] == 2048 and options['wall_seconds'] == worker_wall_seconds(900)
        assert options['measure_peak_memory'] is True
        assert all(options['environment'][key] == '1' for key in
            ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'))
        execution = options['execution']
        assert execution['purpose'] == name and execution['segment_number'] == 51
        assert execution['failed_account_recovery_proof'] == proof
        assert execution['account_version_continuation'] == version
        bounded_command = [command[0], str(bootstrap), *command[1:]]
        launch = {'original_argv': command, 'bounded_argv': bounded_command,
            'worker_source_ref': _ref(Path(runner.__file__).resolve()), 'bootstrap_ref': _ref(bootstrap),
            'memory_mib': options['memory_mib'], 'wall_seconds': options['wall_seconds'],
            'execution': deepcopy(execution), 'source_owner_price_validation_replaced': False,
            'host_monotonic_and_resource_result_replaced': False}
        launches.append(launch)
        _write(folder / 'LAUNCH.json', launch)
        resource = real_run_bounded_worker(bounded_command, **options)
        _save_supervisor_observation(folder, resource)
        return resource

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(resources, 'run_bounded_worker', actual_import_cutoff_worker)
        advanced = service.advance(task_id)
    assert resources.run_bounded_worker is real_run_bounded_worker
    _write(folder / 'PUBLIC_ADVANCE.json', advanced)
    assert advanced['status'] == 'ACCOUNTS_CONTINUE' and advanced['dispatched_segments'] == 1
    assert advanced['next_purpose'] == name and len(launches) == 1

    prefix = runner.segment_prefix(name, 51)
    paths = {key: account / (prefix + suffix) for key, suffix in (
        ('dispatch', '_DISPATCH.json'), ('charge', '_CHARGE.json'), ('resource', '_RESOURCE.json'),
        ('worker', '_WORKER.json'), ('version', '_EXECUTION_VERSION.json'),
        ('startup', '_STARTUP.json'), ('status', '_STATUS.json'),
        ('enter', '_STARTUP_00_ENTER.json'), ('exit', '_STARTUP_00_EXIT.json'),
        ('trigger', '_ARTIFICIAL_IMPORT_CUTOFF.json'))}
    records = {key: _read(path) for key, path in paths.items()}
    resource = records['resource']
    charge = records['charge']
    dispatch = records['dispatch']
    measured = resource['elapsed_wall_seconds']
    assert resource['returncode'] == 75 and resource['timed_out'] is False
    assert math.isfinite(measured) and 0 < measured <= dispatch['upper_bound_seconds'] == 900
    assert charge['outcome'] == 'CONTINUE' and charge['basis'] == 'MEASURED_ACTIVE_WALL_SECONDS'
    assert charge['seconds'] == charge['measured_seconds'] == measured
    assert charge['evidence_identity'] == _sha(paths['resource'])
    assert records['status']['state'] == 'CONTINUE' and records['status']['phase'] == 'STARTUP'
    assert records['status']['last_day'] is records['status']['state_identity'] is None
    assert records['status']['checkpoint_sha256'] is None
    assert records['startup']['validation_complete'] is False
    assert records['startup']['interrupted_stage'] == 'DOMAIN_IMPORTS'
    assert records['startup']['native_call_upper_bound_proven'] is False
    assert records['startup']['stages'][0]['stage'] == 'DOMAIN_IMPORTS'
    assert records['startup']['stages'][0]['completed'] is False
    assert records['enter']['stage'] == records['exit']['stage'] == 'DOMAIN_IMPORTS'
    assert records['enter']['completed'] is records['exit']['completed'] is False
    assert records['trigger']['handshake']['execution'] == launches[0]['execution']
    assert records['trigger']['handshake']['memory_mib'] == 2048
    assert records['trigger']['handshake']['wall_seconds'] == launches[0]['wall_seconds']
    assert records['startup']['deadline_monotonic'] == records['trigger']['deadline_monotonic']
    assert records['startup']['deadline_monotonic'] == (
        records['startup']['worker_started_at_monotonic'] + launches[0]['wall_seconds'] - 60)
    for key in ('dispatch', 'charge', 'resource', 'worker', 'version', 'startup', 'status', 'enter', 'exit'):
        assert records[key]['dispatch_id'] == dispatch['dispatch_id']
        assert records[key]['dispatch_identity'] == dispatch['dispatch_identity']
        assert records[key]['failed_account_recovery_proof'] == proof
    for key in ('resource', 'worker', 'version', 'startup', 'status', 'enter', 'exit'):
        assert records[key]['account_version_continuation'] == version
    for key in ('dispatch', 'charge', 'resource', 'worker', 'startup', 'status', 'enter', 'exit'):
        assert records[key]['time_extension_proof'] == context.time_extension.proof()
    assert records['startup']['reader_pid'] == records['trigger']['reader_pid']
    assert records['startup']['launcher_pid'] == resource['launcher_pid'] == records['worker']['pid']
    assert (records['startup']['reader_pid'] == records['worker']['pid']
        or records['startup']['reader_parent_pid'] == records['worker']['pid'])
    if os.name == 'nt':
        assert resource['windows_job_bound'] is True and records['startup']['windows_job_verified'] is True
        assert resource['peak_memory_mib'] is not None
    assert not (account / (prefix + '_FAILURE.json')).exists()
    assert not (account / (prefix + '_INPUT_ACCESS.json')).exists()
    assert not (account / (name + '_RESULT.json')).exists()
    assert not (account / (name + '_RECOVERY_SETTLEMENT.json')).exists()
    assert not (account / (name + '_SEGMENT_000052_DISPATCH.json')).exists()

    state_after = gov.segment_status(name)
    parent_after = campaign._peek_execution_status()
    operation_after = parent_after['operations'][operation_id]
    assert state_after['segments'][:50] == state_before['segments']
    assert operation_after['segments'][:50] == operation_before['segments']
    assert len(state_after['segments']) == len(operation_after['segments']) == 51
    assert state_after['pending'] is None and operation_after['active_segment'] is None
    assert state_after['next_segment_number'] == 52
    assert state_after['charged_seconds'] == operation_after['active_wall_seconds'] == CANONICAL_SECONDS + measured
    parent_charge = operation_after['segments'][-1]
    for key in ('seconds', 'measured_seconds', 'basis', 'evidence_identity', 'outcome',
            'failed_account_recovery_proof', 'time_extension_proof', 'dispatch_identity'):
        assert parent_charge[key] == charge[key]
    assert parent_charge['segment_number'] == 51
    assert operation_after['status'] == 'FAILED' and effective_execution_status(operation_after) == 'RUNNING'
    assert operation_after['actual'] == operation_before['actual']
    assert operation_after['failed_account_recovery']['base_charged_seconds'] == CANONICAL_SECONDS
    assert operation_after['failed_account_recovery'].get('settlement') is None
    # CONTINUE仍在原整数预留内；canonical实测费用已追加，结清时才投影ceil增量。
    assert parent_after['used'] == parent_before['used'] and parent_after['reserved'] == parent_before['reserved']
    assert parent_after['trial_usage'] == parent_before['trial_usage']
    for other_name, other_operation in mapping.items():
        if other_name != name:
            assert parent_after['operations'][other_operation] == parent_before['operations'][other_operation]
    assert parent_ledger.read_bytes().startswith(ledger_before)
    assert all(_sha(path) == digest for path, digest in original_files.items())
    assert _read(checkpoint) == checkpoint_before and len(_read(checkpoint)['artifacts']['days']) == 277

    # 引导器已撤去；普通fresh检查重读新父子链和源码，不派发、不沿用未完成启动结论。
    ledger_after_charge = parent_ledger.read_bytes()
    fresh = runner.validate_sources(runner.read_json(job_path))
    assert fresh is not context and fresh.recovery.proof() == proof
    fresh.validate(for_dispatch=True, name=name)
    fresh.validate_dispatch(name, 51)
    assert parent_ledger.read_bytes() == ledger_after_charge
    assert gov.segment_status(name) == state_after
    assert not (account / (name + '_SEGMENT_000052_DISPATCH.json')).exists()
    assert all(_sha(path) == digest for path, digest in original_files.items())

    result = {'status': 'PASSED', 'fixture_only': 'ACTUAL_PUBLIC_STARTUP_CUTOFF_WITH_CHILD_ARTIFICIAL_CLOCK',
        'job_ref': _ref(job_path), 'checkpoint_ref': _ref(checkpoint),
        'first_recovery_segment': 51, 'next_segment_number': 52,
        'expected_first_economic_segment': 52, 'next_economic_day_ordinal': 278,
        'preserved_days': 277, 'new_economic_days': 0, 'preserved_original_segments': 50,
        'original_failed_status_preserved': True, 'original_account_file_bytes_preserved': True,
        'old_canonical_seconds': CANONICAL_SECONDS, 'measured_new_seconds': measured,
        'canonical_seconds_after': state_after['charged_seconds'],
        'integer_delta_on_eventual_settlement': math.ceil(state_after['charged_seconds']) - 42340,
        'fee_basis': charge['basis'], 'outcome': charge['outcome'],
        'parent_child_charge_equal': True, 'pending_original_reservation_preserved': True,
        'public_advance_ref': _ref(folder / 'PUBLIC_ADVANCE.json'),
        'launch_ref': _ref(folder / 'LAUNCH.json'), 'bootstrap_ref': _ref(bootstrap),
        'raw_supervisor_resource_ref': _ref(folder / 'SUPERVISOR_RESOURCE.json'),
        'resource_ref': _ref(paths['resource']), 'charge_ref': _ref(paths['charge']),
        'startup_ref': _ref(paths['startup']), 'status_ref': _ref(paths['status']),
        'trigger_ref': _ref(paths['trigger']), 'failed_account_recovery_proof': proof,
        'account_version_continuation': version, 'dispatch_identity': dispatch['dispatch_identity'],
        'ordinary_fresh_validation_after_cutoff': True, 'segment_52_dispatched_by_helper': False,
        'real_owner_or_market_or_performance_acceptance': False}
    evidence_path = folder / 'STARTUP_CHARGE_RESULT.json'
    _write(evidence_path, result)
    return {**result, 'startup_charge_evidence_ref': _ref(evidence_path)}
