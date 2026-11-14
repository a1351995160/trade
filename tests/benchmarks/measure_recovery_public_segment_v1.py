"""独占人工 native277/50FAILED 前缀后，只计量一次普通公共恢复段。

这是原期限到期前的代表性计量，不是完整日历门禁；完整门禁仍须真实等待四小时、
原 helper51、双504、双 native、独立核账、报告及反馈的全部原断言。
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib
import inspect
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time
import traceback


TIMEOUT_SECONDS = 18000
CHARGE_FIELDS = ('seconds', 'measured_seconds', 'basis', 'evidence_identity', 'outcome',
    'failed_account_recovery_proof', 'time_extension_proof', 'dispatch_identity')


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def reference(path):
    return {'path': str(path), 'sha256': sha(path)}


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def store(path, value):
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=True, allow_nan=False, sort_keys=True, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    require(read_json(path) == value, 'MEASUREMENT_OUTPUT_READBACK_CONFLICT')


def snapshot(root, folders=None):
    result = {}
    for base in ([root / name for name in folders] if folders else [root]):
        for parent, directories, files in os.walk(base, followlinks=False):
            for name in directories + files:
                path = Path(parent) / name
                metadata = path.lstat()
                require(not stat.S_ISLNK(metadata.st_mode)
                    and not getattr(metadata, 'st_file_attributes', 0) & 0x400,
                    'MEASUREMENT_LINK_REJECTED')
                if name in files and (folders is None or path.suffix == '.py'):
                    require(stat.S_ISREG(metadata.st_mode), 'MEASUREMENT_REGULAR_FILE_REQUIRED')
                    result[path.relative_to(root).as_posix()] = sha(path)
    return dict(sorted(result.items()))


def changed(before, after):
    return sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))


def isolated_container(value):
    path = value.absolute()
    require(path == path.resolve() and path.parent == Path('E:/t').resolve()
        and re.fullmatch(r'recovery_throughput_[A-Za-z0-9_-]+', path.name),
        'MEASUREMENT_EXCLUSIVE_ARTIFICIAL_ROOT_REQUIRED')
    return path


def isolated_root(value):
    path = value.absolute()
    require(path == path.resolve() and path.name == 'r', 'MEASUREMENT_DEPLOYMENT_ROOT_REQUIRED')
    isolated_container(path.parent)
    return path


def git_metadata(repo):
    argv = ['git', '-c', 'gc.auto=0', '-c', 'maintenance.auto=false']
    def read(*arguments):
        return subprocess.check_output([*argv, *arguments], cwd=repo,
            text=True, encoding='utf-8').strip()
    return {'git_directory': read('rev-parse', '--absolute-git-dir'),
        'source_head': read('rev-parse', 'HEAD'), 'source_tree': read('rev-parse', 'HEAD^{tree}')}


def process_identity(pid):
    require(os.name == 'nt' and type(pid) is int and pid > 0, 'MEASUREMENT_WINDOWS_PROCESS_REQUIRED')
    command = ("$p=Get-CimInstance Win32_Process -Filter 'ProcessId=" + str(pid) + "'; "
        "if ($null -eq $p) { exit 1 }; "
        "[pscustomobject]@{pid=[int]$p.ProcessId; parent_pid=[int]$p.ParentProcessId; "
        "created_at=$p.CreationDate.ToUniversalTime().ToString('o'); "
        "command_line=$p.CommandLine; executable=$p.ExecutablePath} | ConvertTo-Json -Compress")
    return json.loads(subprocess.check_output(['powershell.exe', '-NoProfile', '-NonInteractive',
        '-Command', command], text=True, encoding='utf-8'))


def command_argv(command):
    import ctypes
    from ctypes import wintypes
    parse = ctypes.WinDLL('shell32', use_last_error=True).CommandLineToArgvW
    parse.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    parse.restype = ctypes.POINTER(wintypes.LPWSTR)
    free = ctypes.WinDLL('kernel32', use_last_error=True).LocalFree
    free.argtypes = [ctypes.c_void_p]
    free.restype = ctypes.c_void_p
    count = ctypes.c_int()
    values = parse(command, ctypes.byref(count))
    require(bool(values), 'MEASUREMENT_PROCESS_ARGV_REQUIRED')
    try:
        return [values[index] for index in range(count.value)]
    finally:
        free(values)


def require_launcher(claim, argv):
    # Windows venv redirector 可位于实际子解释器和调用方之间；仅接受该精确命令的一跳。
    parent = process_identity(os.getppid())
    observed = [parent]
    if parent['pid'] != claim['pid']:
        require(command_argv(parent['command_line'])[1:] == argv[1:]
            and Path(parent['executable']) == Path(sys.executable), 'MEASUREMENT_REDIRECTOR_CONFLICT')
        parent = process_identity(parent['parent_pid'])
        observed.append(parent)
    require(parent == claim['process'], 'MEASUREMENT_LAUNCHER_IDENTITY_CONFLICT')
    return observed


def launch(args):
    repo = args.repo.resolve(strict=True)
    container = isolated_container(args.root)
    root = container / 'r'
    output = args.output.absolute()
    require(output == output.resolve() and output.parent.is_dir() and not output.exists()
        and not output.is_relative_to(container), 'MEASUREMENT_NEW_EXTERNAL_OUTPUT_REQUIRED')
    require(re.fullmatch(r'[A-Za-z0-9_-]+', args.label), 'MEASUREMENT_LABEL_INVALID')
    require(container.name.startswith('recovery_throughput_' + args.label + '_'),
        'MEASUREMENT_ROOT_LABEL_CONFLICT')
    container.mkdir(exist_ok=False)
    root.mkdir(exist_ok=False)
    store(root / 'MEASUREMENT_CLAIM.json', {'pid': os.getpid(), 'label': args.label,
        'process': process_identity(os.getpid()),
        'root': str(root), 'output': str(output), 'recorded_at': datetime.now(timezone.utc).isoformat(),
        'fixture_only': True, 'automatic_retry': False})
    metadata = git_metadata(repo)
    sources = snapshot(repo, ('src', 'scripts', 'tests'))
    for folder in ('src', 'scripts'):
        shutil.copytree(repo / folder, root / folder,
            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    sys.path.insert(0, str(repo / 'tests/research_factory'))
    fixture = importlib.import_module('test_account_version_public_integration_v1')
    fixture._install_synthetic_capabilities(root)
    copied = snapshot(root, ('src', 'scripts'))
    require({name: digest for name, digest in copied.items() if name != 'src/sitecustomize.py'}
        == {name: digest for name, digest in sources.items() if name.startswith(('src/', 'scripts/'))},
        'MEASUREMENT_SOURCE_COPY_CONFLICT')
    environment = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1',
        'GIT_DIR': metadata['git_directory'], 'GIT_WORK_TREE': str(root), 'GIT_OPTIONAL_LOCKS': '0',
        'GIT_CONFIG_COUNT': '2', 'GIT_CONFIG_KEY_0': 'gc.auto', 'GIT_CONFIG_VALUE_0': '0',
        'GIT_CONFIG_KEY_1': 'maintenance.auto', 'GIT_CONFIG_VALUE_1': 'false',
        'PYTHONPATH': os.pathsep.join((str(root / 'src'), str(root), str(repo / 'tests/research_factory'))),
        **{key: '1' for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS')}}
    environment.pop('CHANLUN_TEST_ISOLATION', None)
    script = Path(__file__).resolve()
    argv = [sys.executable, str(script), '--measure-root', str(root), '--repo', str(repo),
        '--output', str(output), '--label', args.label]
    store(root / 'MEASUREMENT_LAUNCH.json', {'argv': argv, 'root': str(root), 'label': args.label,
        'script_ref': reference(script), 'git_metadata_context': {**metadata, 'work_tree': str(root),
            'optional_locks': '0', 'read_only_metadata': True}, 'pythonpath': environment['PYTHONPATH'],
        'source_sha256': copied, 'selected_repo_source_sha256': sources,
        'source_frozen_before_owner_approval': True, 'timeout_seconds': TIMEOUT_SECONDS,
        'complete_calendar_gate': False, 'real_account_dispatch': False})
    began = time.perf_counter()
    try:
        with (root / 'MEASUREMENT_STDOUT.log').open('wb') as stdout, (root / 'MEASUREMENT_STDERR.log').open('wb') as stderr:
            completed = subprocess.run(argv, cwd=root, env=environment, stdout=stdout,
                stderr=stderr, timeout=TIMEOUT_SECONDS)
        transport = {'status': 'EXITED', 'returncode': completed.returncode, 'natural_exit': True}
    except subprocess.TimeoutExpired as error:
        transport = {'status': 'TIMED_OUT', 'returncode': None, 'natural_exit': False,
            'exception_type': type(error).__name__, 'exception': str(error)}
    transport['elapsed_seconds'] = time.perf_counter() - began
    store(root / 'MEASUREMENT_TRANSPORT_RESULT.json', transport)
    print(json.dumps({'transport': transport, 'output_exists': output.is_file(),
        'output_ref': reference(output) if output.is_file() else None}, ensure_ascii=True), flush=True)
    return 0 if transport.get('returncode') == 0 else 1


class Prepared(Exception):
    """原场景已写真实 WAITING 标记；本计量不执行该标记之后的完整门禁。"""


def measure(args, result):
    root = isolated_root(args.measure_root).resolve(strict=True)
    launch_record = read_json(root / 'MEASUREMENT_LAUNCH.json')
    claim = read_json(root / 'MEASUREMENT_CLAIM.json')
    require(launch_record['argv'] == [sys.executable, *sys.argv]
        and launch_record['root'] == str(root) and launch_record['label'] == args.label
        and claim['output'] == str(args.output),
        'MEASUREMENT_CHILD_LAUNCH_CONFLICT')
    result['observed_launcher_chain'] = require_launcher(claim, launch_record['argv'])
    store(root / 'MEASUREMENT_CHILD_CLAIM.json', {'pid': os.getpid(), 'parent_pid': os.getppid(),
        'recorded_at': datetime.now(timezone.utc).isoformat(), 'automatic_retry': False})
    require(sys.dont_write_bytecode and snapshot(root, ('src', 'scripts')) == launch_record['source_sha256'],
        'MEASUREMENT_FROZEN_SOURCE_REQUIRED')
    repo = args.repo.resolve(strict=True)
    test_sources = {name: digest for name, digest in launch_record['selected_repo_source_sha256'].items()
        if name.startswith('tests/')}
    require(snapshot(repo, ('tests',)) == test_sources, 'MEASUREMENT_ORIGINAL_TEST_SOURCES_CHANGED')
    scenario = importlib.import_module('test_failed_account_recovery_integration_v1')
    require(Path(scenario.__file__).resolve() == repo / 'tests/research_factory/test_failed_account_recovery_integration_v1.py',
        'MEASUREMENT_ORIGINAL_SCENARIO_IMPORT_REQUIRED')
    from scripts import run_strategy_account_v1 as runner
    require(Path(runner.__file__).resolve() == root / 'scripts/run_strategy_account_v1.py',
        'MEASUREMENT_ISOLATED_RUNNER_REQUIRED')
    from chanlun_trader.research_factory.common import stable_hash
    from chanlun_trader.research_factory.universe_execution_artifacts_v1 import ArtifactSequence
    require(scenario.ORIGINAL_CALENDAR_LIFETIME.total_seconds() == 14400,
        'MEASUREMENT_ORIGINAL_FOUR_HOURS_REQUIRED')
    result.update(root=str(root), source_launch_ref=reference(root / 'MEASUREMENT_LAUNCH.json'),
        original_scenario_ref=reference(Path(scenario.__file__).resolve()), phases=[])
    original_phase = scenario.phase
    captured = {}
    began = time.perf_counter()
    last_phase_at = began

    def phase_hook(phase_root, action, **payload):
        nonlocal last_phase_at
        original_phase(phase_root, action, **payload)
        now = time.perf_counter()
        row = {'phase': action, 'elapsed_seconds': now - began,
            'since_previous_phase_seconds': now - last_phase_at,
            'recorded_at': datetime.now(timezone.utc).isoformat(), 'payload': payload}
        path = root / ('MEASUREMENT_PHASE_' + str(len(result['phases']) + 1).zfill(3) + '.json')
        store(path, row)
        result['phases'].append({**row, 'record_ref': reference(path)})
        last_phase_at = now
        if action == 'WAITING_FOR_ORIGINAL_CALENDAR_DEADLINE':
            caller = inspect.currentframe().f_back
            require(caller.f_code is scenario.scenario.__code__, 'MEASUREMENT_ORIGINAL_SCENARIO_FRAME_REQUIRED')
            captured.update(caller.f_locals)
            raise Prepared()

    scenario.phase = phase_hook
    try:
        scenario.scenario(root)
    except Prepared:
        require(captured, 'MEASUREMENT_PREPARED_CONTEXT_REQUIRED')
    finally:
        scenario.phase = original_phase
        result['prefix_setup_elapsed_seconds'] = time.perf_counter() - began
    require(captured and read_json(root / 'RECOVERY_SCENARIO_PHASE.json')['phase']
        == 'WAITING_FOR_ORIGINAL_CALENDAR_DEADLINE', 'MEASUREMENT_ORIGINAL_WAIT_MARKER_REQUIRED')
    campaign, gov = captured['campaign'], captured['gov']
    job_path, job, name = captured['job_path'], captured['job'], captured['base_name']
    checkpoint = captured['checkpoint']
    original = captured['original']
    boundary = captured['boundary']
    account = job_path.parent
    require(name.endswith('_BASE') and account.is_relative_to(root / 'reports'), 'MEASUREMENT_ARTIFICIAL_ACCOUNT_REQUIRED')
    require(len(boundary['artifacts']['days']) == 277 and captured['checkpoint_raw'] == checkpoint.read_bytes(),
        'MEASUREMENT_NATIVE_277_PREFIX_REQUIRED')
    require(datetime.now(timezone.utc) < datetime.fromisoformat(captured['original_expiry']),
        'MEASUREMENT_PRE_EXPIRY_ASSEMBLY_TOO_SLOW')
    result['original_authorization'] = captured['before']['base_authorization']
    result['original_checkpoint_ref'] = reference(checkpoint)
    result['synthetic_bundle_data_sha256'] = snapshot(root / 'synthetic_inputs/data')
    result['original_job_ref'] = reference(job_path)
    result['original_fee_file_refs'] = [reference(path) for path in sorted(original)
        if path.name.endswith(('_CHARGE.json', '_SETTLEMENT.json'))]
    result['original_account_sha256'] = {str(path.relative_to(account)): hashlib.sha256(raw).hexdigest()
        for path, raw in original.items()}
    install_started = time.perf_counter()
    campaign.install_failed_account_recovery(captured['manifest'])
    gov.reconcile_segment_mirrors(name)
    result['install_and_mirror_elapsed_seconds'] = time.perf_counter() - install_started
    context = runner.validate_sources(job)
    context.validate()
    before = campaign._peek_execution_status()
    state_before = gov.segment_status(name)
    operation_id = captured['mapping'][name]
    operation_before = before['operations'][operation_id]
    require(before['authorization'] == captured['before']['authorization']
        and before['grants'] == captured['before']['grants']
        and before['used'] == captured['before']['used']
        and before['reserved']['wall_seconds'] == captured['before']['reserved']['wall_seconds'] + 29660,
        'MEASUREMENT_ORIGINAL_AUTHORIZATION_OR_RESERVATION_CHANGED')
    require(before['authorization']['resource_limits']['wall_seconds'] == 288002
        and state_before['effective_total_seconds'] == 72000
        and state_before['profile']['worker_seconds'] == 900
        and state_before['profile']['memory_mib'] == 2048
        and state_before['profile']['threads'] == state_before['profile']['concurrency'] == 1
        and state_before['charged_seconds'] == scenario.CANONICAL
        and len(state_before['segments']) == 50 and state_before['next_segment_number'] == 51
        and operation_before['status'] == 'FAILED' and operation_before['actual']['wall_seconds'] == 42340,
        'MEASUREMENT_ORIGINAL_LIMITS_OR_FAILED_BOUNDARY_CHANGED')
    require(all(path.read_bytes() == raw for path, raw in original.items()),
        'MEASUREMENT_PREFIX_CHANGED_AT_INSTALL')
    parent_ledger = campaign.budget_path.with_name('run_budget_events.jsonl')
    ledger_before = parent_ledger.read_bytes()
    store(root / 'MEASUREMENT_BEFORE_PUBLIC_ADVANCE.json', {'job_ref': reference(job_path),
        'manifest_ref': captured['manifest'], 'parent_ledger_ref': reference(parent_ledger),
        'child_state': state_before, 'parent_operation': operation_before,
        'ordinary_segment': 51, 'new_day_ordinal': 278, 'deadline_wait_performed': False,
        'clock_or_worker_cutoff_injected': False})
    public_service = captured['restored']()
    require(datetime.now(timezone.utc) < datetime.fromisoformat(captured['original_expiry']),
        'MEASUREMENT_PRE_EXPIRY_PUBLIC_ENTRY_REQUIRED')
    advanced = None
    advance_exception = None
    advance_started = time.perf_counter()
    try:
        advanced = public_service.advance(captured['task']['task_id'])
    except Exception as error:
        advance_exception = error
        result['public_advance_exception'] = {'type': type(error).__name__, 'message': str(error),
            'traceback': traceback.format_exc()}
    finally:
        result['public_advance_elapsed_seconds'] = time.perf_counter() - advance_started
        store(root / 'MEASUREMENT_PUBLIC_ADVANCE.json', {'result': advanced,
            'exception': result.get('public_advance_exception'),
            'elapsed_seconds': result['public_advance_elapsed_seconds'], 'calls': 1})
    prefix = runner.segment_prefix(name, 51)
    sidecars = {path.name: {'ref': reference(path), 'value': read_json(path)}
        for path in sorted(account.glob(prefix + '_*.json'))}
    result['segment_sidecars'] = sidecars
    after_checkpoint = read_json(checkpoint)
    sequence = ArtifactSequence(after_checkpoint['artifacts'], 'account')
    for index in range(len(sequence)):
        sequence.verify_bytes(index)
    result.update(days_before=277, days_after=len(sequence), new_days=len(sequence) - 277,
        last_day=after_checkpoint['last_day'], checkpoint_ref=reference(checkpoint),
        native_prefix_equal=after_checkpoint['artifacts']['days'][:277] == boundary['artifacts']['days'],
        execution_identity_equal=after_checkpoint['execution_identity'] == boundary['execution_identity'],
        changed_original_non_checkpoint_files=[str(path) for path, raw in original.items()
            if path != checkpoint and path.read_bytes() != raw],
        no_segment52=not (account / (runner.segment_prefix(name, 52) + '_DISPATCH.json')).exists())
    result['source_sha256_after'] = snapshot(root, ('src', 'scripts'))
    result['changed_isolated_sources'] = changed(launch_record['source_sha256'], result['source_sha256_after'])
    result['changed_selected_test_sources'] = changed(test_sources, snapshot(repo, ('tests',)))
    require(result['native_prefix_equal'] and result['execution_identity_equal']
        and not result['changed_original_non_checkpoint_files'] and result['no_segment52']
        and not result['changed_isolated_sources'] and not result['changed_selected_test_sources'],
        'MEASUREMENT_ORIGINAL_PREFIX_OR_SOURCE_CHANGED')
    dispatch_path, charge_path, resource_path = [account / (prefix + suffix)
        for suffix in ('_DISPATCH.json', '_CHARGE.json', '_RESOURCE.json')]
    require(all(path.is_file() for path in (dispatch_path, charge_path, resource_path)),
        'MEASUREMENT_ORDINARY_SEGMENT_INCOMPLETE')
    dispatch, charge, resource = map(read_json, (dispatch_path, charge_path, resource_path))
    for suffix in ('_DISPATCH.json', '_CHARGE.json', '_RESOURCE.json', '_WORKER.json',
            '_EXECUTION_VERSION.json', '_STARTUP.json', '_STATUS.json'):
        value = sidecars[prefix + suffix]['value']
        require(value['dispatch_id'] == dispatch['dispatch_id']
            and value['dispatch_identity'] == dispatch['dispatch_identity']
            and value['failed_account_recovery_proof'] == context.recovery.proof(),
            'MEASUREMENT_RECOVERY_SIDECAR_CONFLICT')
    startup = sidecars[prefix + '_STARTUP.json']['value']
    result['startup_validation_complete'] = startup['validation_complete']
    result['startup_stages'] = startup['stages']
    result['startup_completed_stage_seconds'] = [
        {'stage': row['stage'], 'elapsed_seconds': row['ended_at_monotonic'] - row['started_at_monotonic']}
        for row in startup['stages'] if row['completed']]
    result['startup_elapsed_to_last_stage_exit_seconds'] = max(
        row['ended_at_monotonic'] for row in startup['stages']) - startup['worker_started_at_monotonic']
    events = [json.loads(line) for line in parent_ledger.read_text(encoding='utf-8').splitlines() if line]
    matching = [row['payload'] for row in events if row['event_type'] == 'CAMPAIGN_EXECUTION_SEGMENT_CHARGED'
        and row['payload']['operation_id'] == operation_id and row['payload']['segment_number'] == 51]
    require(len(matching) == 1 and all(matching[0][key] == charge[key] for key in CHARGE_FIELDS),
        'MEASUREMENT_PARENT_CHILD_CHARGE_CONFLICT')
    state_after = gov.segment_status(name)
    parent_after = campaign._peek_execution_status()
    operation_after = parent_after['operations'][operation_id]
    require(state_after['segments'][:50] == state_before['segments']
        and operation_after['segments'][:50] == operation_before['segments']
        and len(state_after['segments']) == len(operation_after['segments']) == 51
        and state_after['pending'] is None and operation_after['active_segment'] is None
        and operation_after['status'] == 'FAILED' and operation_after['actual'] == operation_before['actual']
        and parent_ledger.read_bytes().startswith(ledger_before), 'MEASUREMENT_FEE_PREFIX_CHANGED')
    measured = resource['elapsed_wall_seconds']
    require(math.isfinite(measured) and measured > 0 and dispatch['upper_bound_seconds'] == 900
        and charge['measured_seconds'] == measured and charge['evidence_identity'] == sha(resource_path),
        'MEASUREMENT_RESOURCE_CHARGE_CONFLICT')
    result.update(parent_charge_count=1, parent_child_eight_fields_equal=True,
        child_state_after=state_after, parent_operation_after=operation_after,
        parent_ledger_ref=reference(parent_ledger), resource_ref=reference(resource_path),
        charge_ref=reference(charge_path), measured_worker_seconds=measured,
        charged_seconds_per_new_day=charge['seconds'] / result['new_days'] if result['new_days'] > 0 else None,
        public_seconds_per_new_day=result['public_advance_elapsed_seconds'] / result['new_days'] if result['new_days'] > 0 else None,
        semantic_result_identity=stable_hash({'original_days': 277, 'original_segments': 50,
            'native_prefix_equal': result['native_prefix_equal'], 'execution_identity_equal': result['execution_identity_equal'],
            'parent_child_eight_fields_equal': True, 'no_segment52': True}))
    if charge['outcome'] == 'CONTINUE':
        require(parent_after['used'] == before['used'] and parent_after['reserved'] == before['reserved']
            and parent_after['trial_usage'] == before['trial_usage'], 'MEASUREMENT_RESERVED_BUDGET_CHANGED')
    result['imported_isolated_sources'] = {name: reference(Path(module.__file__).resolve())
        for name, module in tuple(sys.modules.items())
        if name.startswith(('chanlun_trader', 'scripts.run_strategy_account_v1'))
        and getattr(module, '__file__', None) and Path(module.__file__).suffix == '.py'}
    require(all(Path(item['path']).is_relative_to(root)
        for item in result['imported_isolated_sources'].values()), 'MEASUREMENT_IMPORTED_SOURCE_OUTSIDE_ROOT')
    if advance_exception is not None:
        raise advance_exception
    require(advanced.get('dispatched_segments') == 1, 'MEASUREMENT_EXACTLY_ONE_PUBLIC_DISPATCH_REQUIRED')
    require(reference(Path(__file__).resolve()) == launch_record['script_ref'],
        'MEASUREMENT_HARNESS_SOURCE_CHANGED')
    result['status'] = 'MEASURED'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--root', type=Path)
    parser.add_argument('--measure-root', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--label', required=True)
    args = parser.parse_args()
    require((args.root is None) != (args.measure_root is None), 'MEASUREMENT_ONE_MODE_REQUIRED')
    if args.root is not None:
        return launch(args)
    result = {'status': 'RUNNING', 'label': args.label, 'recorded_at': datetime.now(timezone.utc).isoformat(),
        'complete_calendar_gate': False, 'real_account_dispatch': False, 'automatic_retry': False,
        'limits': '一次普通人工公共段；未等待原四小时，不替代完整门禁、真实账户或整体 ETA。'}
    try:
        measure(args, result)
    except Exception as error:
        result.update(status='FAILED', exception_type=type(error).__name__, exception=str(error),
            traceback=traceback.format_exc())
    result['completed_at'] = datetime.now(timezone.utc).isoformat()
    store(args.output, result)
    print(json.dumps({'status': result['status'], 'output_ref': reference(args.output)}, ensure_ascii=True), flush=True)
    return 0 if result['status'] == 'MEASURED' else 1


if __name__ == '__main__':
    raise SystemExit(main())
