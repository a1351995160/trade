"""人工 worker 启动与独立接续路由；真实资源子进程不读取价格或 Owner。"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from chanlun_trader.account_startup_boundary_v1 import (
    AccountStartupBoundaryV1, StartupBoundaryExceeded, startup_checkpoint,
    startup_file_sha256, startup_read_bytes,
)
from scripts import run_strategy_account_v1 as runner
from test_long_horizon_cooperative_deadline_v1 import account_worker, clock
from test_account_version_continuation_v1 import case, install, resume


@pytest.mark.parametrize('stage', ['VALIDATE_SOURCES', 'LOADER', 'FIRST_GUARD'])
def test_deep_python_startup_is_interrupted_before_economic_entry(tmp_path, monkeypatch, stage):
    timer = clock(monkeypatch)
    path, observed = account_worker(tmp_path, monkeypatch, timer)
    observed['economic_calls'] = 0

    def costly_validation():
        # 没有显式 checkpoint 的深层 Python 循环；不能仅在函数返回后检查。
        while True:
            timer.now += 1

    if stage == 'VALIDATE_SOURCES':
        monkeypatch.setattr(runner, 'validate_sources', lambda job: costly_validation())
    elif stage == 'LOADER':
        original_resolve = runner.resolve
        monkeypatch.setattr(runner, 'resolve', lambda name:
            costly_validation if name == 'fixture:loader' else original_resolve(name))
    else:
        governance = runner.service(runner.read_json(path))
        calls = 0

        def active(name):
            assert name == 'FIXED'
            nonlocal calls
            calls += 1
            if calls > 1:
                costly_validation()
            return {'input_identity': 'INPUT'}

        governance.active_execution = active

    def public_entry(*args, **kwargs):
        kwargs['active_check']()
        observed['economic_calls'] += 1
        pytest.fail('完整启动检查未完成时不得进入经济计算')

    monkeypatch.setattr(runner, 'run', public_entry)
    previous_trace = sys.gettrace()
    assert runner._long_horizon_worker(path, 'FIXED', 1) == 75
    assert sys.gettrace() is previous_trace
    assert observed['economic_calls'] == 0
    status = runner.read_json(path.parent / 'FIXED_SEGMENT_000001_STATUS.json')
    startup = runner.read_json(path.parent / 'FIXED_SEGMENT_000001_STARTUP.json')
    assert status['state'] == 'CONTINUE' and status['phase'] == 'STARTUP'
    assert startup['validation_complete'] is False
    assert startup['native_call_upper_bound_proven'] is False
    assert any(not row['completed'] for row in startup['stages'])
    assert not (path.parent / 'FIXED_RESULT.json').exists()


def test_permission_failure_is_preserved_and_not_relabelled_as_cutoff(tmp_path, monkeypatch):
    timer = clock(monkeypatch)
    path, observed = account_worker(tmp_path, monkeypatch, timer)

    def denied(job):
        raise PermissionError('ARTIFICIAL_SOURCE_CHANGED')

    monkeypatch.setattr(runner, 'validate_sources', denied)
    with pytest.raises(PermissionError, match='ARTIFICIAL_SOURCE_CHANGED'):
        runner.long_horizon_worker(path, 'FIXED', 1)
    failure = runner.read_json(path.parent / 'FIXED_SEGMENT_000001_FAILURE.json')
    assert failure['exception_type'] == 'PermissionError' and failure['automatic_retry'] is False
    assert observed['loader_calls'] == observed['account_calls'] == 0
    assert not (path.parent / 'FIXED_SEGMENT_000001_STATUS.json').exists()


def test_completed_startup_restores_trace_and_does_not_interrupt_daily_work(monkeypatch):
    timer = clock(monkeypatch)
    import chanlun_trader.account_startup_boundary_v1 as startup_module
    monkeypatch.setattr(startup_module.time, 'monotonic', lambda: timer.now)
    previous_trace = sys.gettrace()
    boundary = AccountStartupBoundaryV1(10.)
    with boundary.scope(), boundary.stage_scope('ARTIFICIAL_COMPLETE_VALIDATION'):
        timer.now = 9.
        boundary.completed()
        assert sys.gettrace() is previous_trace
        timer.now = 100.
        startup_checkpoint('ARTIFICIAL_COMPLETE_DAY')
    assert boundary.snapshot()['validation_complete'] is True
    assert boundary.snapshot()['stages'][0]['ended_at_monotonic'] == 9.


@pytest.mark.parametrize('outcome', ['CUTOFF', 'COMPLETE', 'ERROR'])
def test_current_caller_frame_restores_local_trace_and_context(monkeypatch, outcome):
    import chanlun_trader.account_startup_boundary_v1 as startup_module
    timer = clock(monkeypatch)
    previous_context = startup_module._ACTIVE.get()
    previous_global = sys.gettrace()
    frame = sys._getframe()
    previous_local = frame.f_trace
    previous_lines = frame.f_trace_lines
    observed = []

    def existing_trace(current, event, arg):
        if current is frame:
            observed.append(event)
        return existing_trace

    boundary = AccountStartupBoundaryV1(10.)
    caught = None
    try:
        sys.settrace(existing_trace)
        frame.f_trace = existing_trace
        frame.f_trace_lines = False
        try:
            with boundary.scope(), boundary.stage_scope('ARTIFICIAL_CURRENT_FRAME'):
                assert frame.f_trace_lines is True
                if outcome == 'CUTOFF':
                    # 只执行已有调用帧，不调用深层函数来间接触发截止。
                    while True:
                        timer.now += 1.
                elif outcome == 'COMPLETE':
                    boundary.completed()
                    assert sys.gettrace() is existing_trace
                    assert frame.f_trace is existing_trace and frame.f_trace_lines is False
                    timer.now = 100.
                    startup_checkpoint('ARTIFICIAL_DAILY_WORK')
                else:
                    raise PermissionError('ARTIFICIAL_CURRENT_FRAME_DENIED')
        except (StartupBoundaryExceeded, PermissionError) as exc:
            caught = exc
        assert sys.gettrace() is existing_trace
        assert frame.f_trace is existing_trace and frame.f_trace_lines is False
        assert startup_module._ACTIVE.get() is previous_context
        assert boundary._frame_traces == boundary._frame_trace_lines == {}
        if outcome == 'CUTOFF':
            assert isinstance(caught, StartupBoundaryExceeded)
            assert caught.stage == 'ARTIFICIAL_CURRENT_FRAME' and timer.now >= boundary.deadline
            assert 'line' in observed and boundary.validation_complete is False
        elif outcome == 'COMPLETE':
            assert caught is None and boundary.validation_complete is True
        else:
            assert isinstance(caught, PermissionError)
            assert str(caught) == 'ARTIFICIAL_CURRENT_FRAME_DENIED'
            assert boundary.validation_complete is False
    finally:
        sys.settrace(previous_global)
        frame.f_trace = previous_local
        frame.f_trace_lines = previous_lines


@pytest.mark.parametrize('local_trace', [None, 'CALLBACK'])
@pytest.mark.parametrize('trace_lines', [False, True])
def test_completed_startup_restores_paused_generator_local_trace(monkeypatch, local_trace, trace_lines):
    from contextlib import contextmanager
    timer = clock(monkeypatch)
    boundary = AccountStartupBoundaryV1(10.)
    old_global = sys.gettrace()

    def previous_callback(frame, event, arg):
        return previous_callback

    expected = previous_callback if local_trace == 'CALLBACK' else None

    @contextmanager
    def suspended_startup():
        frame = sys._getframe()
        frame.f_trace = expected
        frame.f_trace_lines = trace_lines
        with boundary.scope(), boundary.stage_scope('ARTIFICIAL_GENERATOR_STARTUP'):
            yield frame
        assert frame.f_trace is expected
        assert frame.f_trace_lines is trace_lines

    try:
        sys.settrace(None)
        with suspended_startup() as paused:
            assert paused.f_trace is not expected
            boundary.completed()
            # yield is a trace return event; the suspended caller must still be restored.
            assert paused.f_trace is expected
            assert paused.f_trace_lines is trace_lines
            assert sys.gettrace() is None
            timer.now = 100.
            startup_checkpoint('ARTIFICIAL_DAILY_WORK_AFTER_GENERATOR_YIELD')
        assert boundary._frame_traces == boundary._frame_trace_lines == {}
        assert boundary.validation_complete is True
    finally:
        sys.settrace(old_global)


def test_startup_diagnostics_do_not_grant_new_time_before_economic_entry(monkeypatch):
    timer = clock(monkeypatch)

    def slow_diagnostic(index, row, event):
        if event == 'VALIDATED':
            timer.now = 11.

    previous_trace = sys.gettrace()
    boundary = AccountStartupBoundaryV1(10., on_stage=slow_diagnostic)
    with pytest.raises(StartupBoundaryExceeded):
        with boundary.scope(), boundary.stage_scope('ARTIFICIAL_LAST_GUARD'):
            boundary.completed()
    assert boundary.validation_complete is False
    assert sys.gettrace() is previous_trace


def test_chunked_reads_preserve_all_bytes_and_digest(tmp_path, monkeypatch):
    import hashlib
    import chanlun_trader.account_startup_boundary_v1 as startup_module
    target = tmp_path / 'artificial-source.bin'
    raw = bytes(range(256)) * 8193
    target.write_bytes(raw)
    monkeypatch.setattr(startup_module.time, 'monotonic', lambda: 0.)
    with AccountStartupBoundaryV1(1.).scope():
        assert startup_read_bytes(target) == raw
        assert startup_file_sha256(target) == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize('reader', [startup_read_bytes, startup_file_sha256])
def test_startup_reader_rejects_lexical_traversal_before_filesystem_probe(tmp_path, monkeypatch, reader):
    root = tmp_path.resolve()
    allowed = root / 'allowed'
    allowed.mkdir()
    secret = root / 'private.json'
    secret.write_bytes(b'{"artificial_private":true}')
    traversed = allowed / '..' / secret.name
    original_lstat = Path.lstat
    probed = []

    def forbidden_probe(path, *args, **kwargs):
        probed.append(path)
        raise AssertionError('lexical traversal must be rejected before any lstat')

    monkeypatch.setattr(Path, 'lstat', forbidden_probe)
    with pytest.raises(ValueError, match='ACCOUNT_STARTUP_FILE_INVALID'):
        reader(traversed)
    assert probed == []
    monkeypatch.setattr(Path, 'lstat', original_lstat)
    expected = secret.read_bytes()
    if reader is startup_read_bytes:
        assert reader(secret) == expected
    else:
        assert reader(secret) == hashlib.sha256(expected).hexdigest()


def test_expired_boundary_never_starts_a_new_read(tmp_path, monkeypatch):
    import chanlun_trader.account_startup_boundary_v1 as startup_module
    monkeypatch.setattr(startup_module.time, 'monotonic', lambda: 2.)
    # 路径不存在；正确行为必须先在启动检查点退出，而非开始文件读取。
    with pytest.raises(StartupBoundaryExceeded):
        with AccountStartupBoundaryV1(1.).scope():
            startup_read_bytes(tmp_path / 'must-not-be-opened')


@pytest.mark.parametrize('reader', ['read_pinned_json', 'file_sha256'])
def test_real_secure_file_reader_propagates_internal_startup_cutoff(tmp_path, monkeypatch, reader):
    from chanlun_trader.research_factory import secure_file_reference_v1 as secure
    root = tmp_path.resolve()
    target = root / 'artificial-pinned.json'
    target.write_text('{"artificial":true}', encoding='utf-8')
    reference = {'path': str(target), 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}
    timer = clock(monkeypatch)
    reached = []
    boundary = AccountStartupBoundaryV1(1.)

    def cutoff_inside_path_check(frame):
        if frame.f_code is secure._reject_link_components.__code__:
            reached.append(frame.f_code.co_name)
            timer.now = 2.
            startup_checkpoint('INSIDE_REAL_SECURE_READER')

    boundary.before_call = cutoff_inside_path_check
    previous_trace = sys.gettrace()
    with pytest.raises(StartupBoundaryExceeded) as caught:
        with boundary.scope(), boundary.stage_scope('ARTIFICIAL_PINNED_READ'):
            if reader == 'read_pinned_json':
                secure.read_pinned_json(reference, root=root, error_code='ARTIFICIAL_REFERENCE_CHANGED')
            else:
                secure.file_sha256(target, root=root, error_code='ARTIFICIAL_REFERENCE_CHANGED')
    assert reached == ['_reject_link_components']
    assert caught.value.stage == 'ARTIFICIAL_PINNED_READ'
    assert not isinstance(caught.value, (OSError, RuntimeError, TypeError, ValueError))
    assert caught.value.__cause__ is None
    assert sys.gettrace() is previous_trace


@pytest.mark.parametrize('reader,fault', [
    ('read_pinned_json', 'missing'), ('read_pinned_json', 'outside'),
    ('read_pinned_json', 'digest'), ('read_pinned_json', 'json'),
    ('file_sha256', 'missing'), ('file_sha256', 'outside'), ('file_sha256', 'directory'),
])
def test_real_secure_reader_still_rejects_file_errors_before_deadline(tmp_path, monkeypatch, reader, fault):
    from chanlun_trader.research_factory import secure_file_reference_v1 as secure
    root = (tmp_path / 'allowed').resolve(); root.mkdir()
    target = root / 'artificial-pinned.json'
    target.write_text('{"artificial":true}', encoding='utf-8')
    if fault == 'outside':
        target = tmp_path.resolve() / 'outside.json'
        target.write_text('{"artificial":true}', encoding='utf-8')
    elif fault == 'directory':
        target = root / 'directory'; target.mkdir()
    elif fault == 'json':
        target.write_text('{invalid', encoding='utf-8')
    digest = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else '0' * 64
    if fault == 'missing':
        target = root / 'missing.json'
    elif fault == 'digest':
        digest = '0' * 64
    reference = {'path': str(target), 'sha256': digest}
    clock(monkeypatch)
    with pytest.raises(ValueError, match='^ARTIFICIAL_REFERENCE_CHANGED$'):
        with AccountStartupBoundaryV1(1.).scope():
            if reader == 'read_pinned_json':
                secure.read_pinned_json(reference, root=root, error_code='ARTIFICIAL_REFERENCE_CHANGED')
            else:
                secure.file_sha256(target, root=root, error_code='ARTIFICIAL_REFERENCE_CHANGED')


DOMAIN_IMPORT_BOOTSTRAP = '''
import builtins
import json
from pathlib import Path
import runpy
import sys
import time
runner_path, job_path, mode = sys.argv[1:]
root = Path(job_path).resolve().parent
real_import = builtins.__import__
clock = {'now': 0.}
def controlled_domain_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name == 'chanlun_trader.research_factory.strategy_interface_v1':
        (root / 'DOMAIN_IMPORT_ATTEMPTED.json').write_text(json.dumps({
            'module': name, 'fixture_only': True}), encoding='utf-8')
        if mode == 'IMPORT_CUTOFF':
            clock['now'] = 2.
        elif mode == 'IMPORT_ERROR':
            raise ImportError('ARTIFICIAL_REAL_DOMAIN_IMPORT_FAILURE')
    return real_import(name, globals, locals, fromlist, level)
if mode != 'ENTRY_CUTOFF':
    time.monotonic = lambda: clock['now']
builtins.__import__ = controlled_domain_import
sys.argv = [runner_path, '--worker', 'ARTIFICIAL_BASE', '--job', job_path, '--segment', '51']
runpy.run_path(runner_path, run_name='__main__')
'''


def save_resource_observation(root, resource, started_pids):
    """断言前保留宿主完整资源回执和原始输出，失败时仍能定位真实退出原因。"""
    metadata = {key: value for key, value in resource.items() if key not in ('stdout', 'stderr')}
    metadata['started_pids'] = list(started_pids)
    for channel in ('stdout', 'stderr'):
        raw = resource[channel]
        path = root / ('SUPERVISOR_' + channel.upper() + '.bin')
        path.write_bytes(raw)
        metadata[channel] = {'path': str(path), 'size_bytes': len(raw),
            'sha256': hashlib.sha256(raw).hexdigest()}
    (root / 'SUPERVISOR_RESOURCE.json').write_text(json.dumps(metadata, ensure_ascii=False, allow_nan=False),
        encoding='utf-8')


@pytest.mark.parametrize('mode', ['ENTRY_CUTOFF', 'IMPORT_CUTOFF', 'IMPORT_ERROR'])
def test_actual_script_domain_import_cutoff_is_continuation_but_import_error_is_failed(tmp_path, mode):
    from chanlun_trader.synthetic_batch_resources import run_bounded_worker
    if os.name != 'nt' and not sys.platform.startswith('linux'):
        pytest.skip('实际资源限制只支持 Windows 和 Linux')
    root = tmp_path.resolve()
    job_path = root / 'JOB.json'
    job_path.write_text(json.dumps({'fixture_only': 'NO_OWNER_NO_PRICES_NO_ECONOMIC_ENTRY'}), encoding='utf-8')
    entry = root / 'domain_import_bootstrap.py'
    entry.write_text(DOMAIN_IMPORT_BOOTSTRAP, encoding='utf-8')
    proof = {'manifest_ref': {'path': str(root / 'ARTIFICIAL_MANIFEST.json'), 'sha256': '1' * 64},
        'recovery_id': '2' * 64}
    version = {'version_id': 'ARTIFICIAL_VERSION', 'failed_account_recovery': proof}
    execution = {'purpose': 'ARTIFICIAL_BASE', 'segment_number': 51, 'dispatch_id': 'ARTIFICIAL_DISPATCH_51',
        'dispatch_identity': '3' * 64, 'failed_account_recovery_proof': proof,
        'account_version_continuation': version}
    started = []
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1',
        'PYTHONPATH': str(runner.REPO / 'src') + os.pathsep + str(runner.REPO),
        **{key: '1' for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS')}}
    # 真实握手/OS资源边界；内部截止用人工时钟触发，生产900秒规格不变。
    wall_seconds = 1.5 if mode == 'ENTRY_CUTOFF' else 61.
    resource = run_bounded_worker([sys.executable, str(entry), str(Path(runner.__file__).resolve()), str(job_path), mode],
        root=runner.REPO, memory_mib=2048, wall_seconds=wall_seconds, on_started=started.append,
        environment=env, execution=execution, measure_peak_memory=True)
    save_resource_observation(root, resource, started)
    prefix = 'ARTIFICIAL_BASE_SEGMENT_000051'
    assert len(started) == 1 and not resource['timed_out']
    entered = runner.read_json(root / (prefix + '_STARTUP_00_ENTER.json'))
    assert entered['stage'] == 'DOMAIN_IMPORTS' and entered['completed'] is False
    assert (root / 'DOMAIN_IMPORT_ATTEMPTED.json').exists() is (mode != 'ENTRY_CUTOFF')
    if mode == 'IMPORT_ERROR':
        assert resource['returncode'] == 1
        assert b'ARTIFICIAL_REAL_DOMAIN_IMPORT_FAILURE' in resource['stderr']
        assert not (root / (prefix + '_STATUS.json')).exists()
        assert not (root / (prefix + '_STARTUP.json')).exists()
    else:
        assert resource['returncode'] == 75
        startup = runner.read_json(root / (prefix + '_STARTUP.json'))
        status = runner.read_json(root / (prefix + '_STATUS.json'))
        assert startup['validation_complete'] is False and startup['interrupted_stage'] == 'DOMAIN_IMPORTS'
        assert startup['stages'][0]['completed'] is False
        assert status['state'] == 'CONTINUE' and status['phase'] == 'STARTUP'
        assert status['reason'] == 'UNIVERSE_WORKER_COOPERATIVE_DEADLINE'
        assert status['last_day'] is status['state_identity'] is status['checkpoint_sha256'] is None
        assert startup['deadline_monotonic'] == startup['worker_started_at_monotonic'] + wall_seconds - 60
        assert startup['reader_pid'] == started[0] or startup['reader_parent_pid'] == started[0] == startup['launcher_pid']
        for value in (entered, startup, status):
            assert value['dispatch_id'] == execution['dispatch_id']
            assert value['dispatch_identity'] == execution['dispatch_identity']
            assert value['failed_account_recovery_proof'] == proof
            assert value['account_version_continuation'] == version
        if os.name == 'nt':
            assert resource['windows_job_bound'] and startup['windows_job_verified']
    assert not (root / 'ARTIFICIAL_BASE_RESULT.json').exists()
    assert not (root / (prefix + '_INPUT_ACCESS.json')).exists()


RESOURCE_WORKER = '''
import json
from pathlib import Path
import sys
import time
started = time.monotonic()
from chanlun_trader.synthetic_batch_resources import worker_resource_handshake
handshake = worker_resource_handshake()
from chanlun_trader.account_startup_boundary_v1 import (
    AccountStartupBoundaryV1, StartupBoundaryExceeded, startup_boundary_active)
root = Path(sys.argv[1])
mode = handshake['execution']['mode']
def write(name, value):
    (root / name).write_text(json.dumps(value), encoding='utf-8')
def phase(index, row, event):
    write('PHASE_' + event + '.json', {'row': row, 'handshake': handshake, 'started': started})
# The 0.2/1-second allowance targets this artificial stage. The unchanged
# 1.5-second supervisor window still includes interpreter imports and handshake.
stage_started = time.monotonic()
boundary = AccountStartupBoundaryV1(stage_started + handshake['execution']['cutoff_seconds'], on_stage=phase)
try:
    with boundary.scope(), boundary.stage_scope('ARTIFICIAL_RESOURCE_STARTUP'):
        if mode == 'PYTHON':
            value = 0
            while True:
                value += 1
        elif mode == 'NATIVE':
            write('NATIVE_ENTRY.json', {'entered_at_monotonic': time.monotonic(),
                'deadline_monotonic': stage_started + handshake['execution']['cutoff_seconds'],
                'cooperative_seconds': handshake['execution']['cutoff_seconds'],
                'whole_worker_started_at_monotonic': started})
            time.sleep(10)
        elif mode == 'MEMORY':
            value = bytearray(512 * 1024 * 1024)
        elif mode == 'COMPLETE':
            boundary.completed()
            assert sys.gettrace() is None
            write('ECONOMIC_ENTRY.json', {'artificial': True, 'validation_complete': True})
    write('STATUS.json', {'state': 'COMPLETED', 'startup': boundary.snapshot(),
        'trace_restored': sys.gettrace() is None, 'startup_active': startup_boundary_active()})
except StartupBoundaryExceeded:
    write('STATUS.json', {'state': 'CONTINUE', 'startup': boundary.snapshot(),
        'trace_restored': sys.gettrace() is None, 'startup_active': startup_boundary_active()})
    sys.exit(75)
except MemoryError:
    write('FAILURE.json', {'exception_type': 'MemoryError', 'automatic_retry': False})
    sys.exit(1)
'''


@pytest.mark.parametrize('mode', ['PYTHON', 'COMPLETE', 'NATIVE', 'MEMORY'])
def test_actual_resource_handshake_preserves_cutoff_timeout_and_memory_failure(tmp_path, mode):
    from chanlun_trader.synthetic_batch_resources import run_bounded_worker
    if os.name != 'nt' and not sys.platform.startswith('linux'):
        pytest.skip('实际资源限制只支持 Windows 和 Linux')
    entry = tmp_path / 'artificial_startup_worker.py'
    entry.write_text(RESOURCE_WORKER, encoding='utf-8')
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1',
        'PYTHONPATH': str(runner.REPO / 'src') + os.pathsep + str(runner.REPO),
        **{key: '1' for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS')}}
    started_pids = []
    resource = run_bounded_worker([sys.executable, str(entry), str(tmp_path)], root=runner.REPO,
        memory_mib=128, wall_seconds=1.5, on_started=started_pids.append, environment=env,
        execution={'mode': mode, 'cutoff_seconds': .2 if mode in ('PYTHON', 'NATIVE') else 1.}, measure_peak_memory=True)
    save_resource_observation(tmp_path, resource, started_pids)
    assert len(started_pids) == 1
    entered = runner.read_json(tmp_path / 'PHASE_ENTER.json')
    assert entered['handshake']['memory_mib'] == 128
    if os.name == 'nt':
        assert resource['windows_job_bound'] and entered['handshake']['windows_job_verified']
        assert resource['peak_memory_mib'] is not None
    if mode == 'PYTHON':
        assert resource['returncode'] == 75 and not resource['timed_out']
        status = runner.read_json(tmp_path / 'STATUS.json')
        assert status['state'] == 'CONTINUE' and status['startup']['validation_complete'] is False
        assert status['trace_restored'] is True and status['startup_active'] is False
        assert runner.read_json(tmp_path / 'PHASE_EXIT.json')['row']['completed'] is False
        assert not (tmp_path / 'ECONOMIC_ENTRY.json').exists()
    elif mode == 'COMPLETE':
        assert resource['returncode'] == 0 and not resource['timed_out']
        assert runner.read_json(tmp_path / 'ECONOMIC_ENTRY.json')['validation_complete'] is True
        status = runner.read_json(tmp_path / 'STATUS.json')
        assert status['trace_restored'] is True and status['startup_active'] is False
    elif mode == 'NATIVE':
        # Python 检查点不能伪造原生调用上界，真实硬超时不能改标 CONTINUE。
        assert resource['timed_out'] and resource['returncode'] != 75
        entered_native = runner.read_json(tmp_path / 'NATIVE_ENTRY.json')
        assert entered_native['cooperative_seconds'] == .2
        assert entered_native['entered_at_monotonic'] < entered_native['deadline_monotonic']
        assert not (tmp_path / 'STATUS.json').exists()
    else:
        assert resource['returncode'] != 75 and not resource['timed_out']
        assert not (tmp_path / 'ECONOMIC_ENTRY.json').exists()
        assert not (tmp_path / 'STATUS.json').exists()


def test_artificial_public_account_runs_full_startup_chain_before_economics(tmp_path):
    from test_long_horizon_failure_recovery_v1 import approved_job
    actual_runner, path, job, _ = approved_job(tmp_path)
    # 真实公共 loader / Owner 替代的合法合成批准 / guard / OS worker；仅人工价格。
    actual_runner.execute_long_horizon_accounts(path, step=True)
    name = next(iter(job['plans']))
    root = Path(job['root'])
    startup = actual_runner.read_json(root / (name + '_SEGMENT_000001_STARTUP.json'))
    assert startup['validation_complete'] is True
    assert all(row['completed'] for row in startup['stages'])
    assert startup['interruptibility'] == 'PYTHON_CHECKPOINTS_NATIVE_CALLS_SUBJECT_TO_SUPERVISOR'
    assert actual_runner.read_json(root / (name + '_SEGMENT_000001_STARTUP_00_EXIT.json'))['completed'] is True
    status = actual_runner.read_json(root / (name + '_SEGMENT_000001_STATUS.json'))
    assert status['state'] == 'COMPLETED'
    result = actual_runner.read_json(root / (name + '_RESULT.json'))
    assert len(result['artifacts']['days']) == 20


def recovery_routing_context():
    """仅测试内部路由的已验证调用约定；不替代 Owner manifest 验证或签发。"""
    proof = {'manifest_ref': {'path': 'ARTIFICIAL_MANIFEST', 'sha256': '1' * 64}, 'recovery_id': '2' * 64}
    version = {'version_id': 'ARTIFICIAL_VERSION', 'failed_account_recovery': proof}
    recovery = SimpleNamespace(original_failed_account='ARTIFICIAL_BASE', proof=lambda: deepcopy(proof))
    context = SimpleNamespace(recovery=recovery, proof=lambda name: deepcopy(version))
    return context, proof, version


def test_only_failed_purpose_uses_independent_recovery_aggregate_paths(tmp_path):
    context, _, _ = recovery_routing_context()
    job = {'root': str(tmp_path), 'plans': {'ARTIFICIAL_BASE': {}, 'ARTIFICIAL_STRESS': {}}}
    base = runner._account_execution_paths(job, 'ARTIFICIAL_BASE', context)
    stress = runner._account_execution_paths(job, 'ARTIFICIAL_STRESS', context)
    ordinary = runner._account_execution_paths(job, 'ARTIFICIAL_BASE', None)
    assert base['settlement'].name == 'ARTIFICIAL_BASE_RECOVERY_SETTLEMENT.json'
    assert base['resource'].name == 'ARTIFICIAL_BASE_RECOVERY_RESOURCE.json'
    assert stress['settlement'].name == 'ARTIFICIAL_STRESS_SETTLEMENT.json'
    assert ordinary['settlement'].name == 'ARTIFICIAL_BASE_SETTLEMENT.json'


@pytest.mark.parametrize('suffix', ['_WORKER.json', '_RESOURCE.json', '_INPUT_ACCESS.json',
    '_STATUS.json', '_FAILURE.json', '_STARTUP.json', '_STARTUP_01_ENTER.json'])
def test_each_new_segment_sidecar_must_carry_the_same_recovery_proof(tmp_path, suffix):
    context, proof, version = recovery_routing_context()
    dispatch = {'segment_number': 51, 'dispatch_id': 'ARTIFICIAL_DISPATCH_51',
        'dispatch_identity': '4' * 64, 'failed_account_recovery_proof': proof}
    prefix = 'ARTIFICIAL_BASE_SEGMENT_000051'
    value = {'dispatch_id': dispatch['dispatch_id'], 'failed_account_recovery_proof': proof,
        'dispatch_identity': dispatch['dispatch_identity'], 'account_version_continuation': version, 'validation_complete': False}
    runner.save(tmp_path / (prefix + '_WORKER.json'), value)
    runner.save(tmp_path / (prefix + '_STARTUP.json'), value)
    runner.save(tmp_path / (prefix + '_STATUS.json'), value)
    target = tmp_path / (prefix + suffix)
    target.write_text(json.dumps({**value, 'failed_account_recovery_proof': {**proof, 'recovery_id': '3' * 64}}), encoding='utf-8')
    with pytest.raises(PermissionError, match='SEGMENT_PROOF_CONFLICT'):
        runner._validate_recovery_segment_sidecars(tmp_path, prefix, dispatch, context, 'ARTIFICIAL_BASE', require_startup=True)


def test_plain_failed_settlement_cannot_be_reopened_by_a_recovery_named_file(tmp_path):
    from chanlun_trader.research_factory.common import stable_hash
    job = {'root': str(tmp_path), 'plans': {'ARTIFICIAL_BASE': {}}}
    receipt = {'strategy_plans': job['plans']}
    receipt['receipt_id'] = stable_hash(receipt)
    start = {'kind': 'ARTIFICIAL_BASE', 'receipt_id': receipt['receipt_id']}
    runner.save(tmp_path / 'CONFIRMATION.json', receipt)
    runner.save(tmp_path / 'ARTIFICIAL_BASE_START.json', start)
    runner.save(tmp_path / 'ARTIFICIAL_BASE_SETTLEMENT.json', {**start, 'completed': False, 'error': 'ORIGINAL_FAILED', 'wall_seconds': 10.})
    runner.save(tmp_path / 'ARTIFICIAL_BASE_RESOURCE.json', {'returncode': 1, 'timed_out': True})
    runner.save(tmp_path / 'ARTIFICIAL_BASE_RECOVERY_SETTLEMENT.json', {**start, 'completed': True, 'wall_seconds': 1.})
    with pytest.raises(PermissionError, match='FAILED_OR_SETTLEMENT_CONFLICT'):
        runner.validated_settlement(job, 'ARTIFICIAL_BASE')


def test_short_plain_frames_share_the_same_64_line_deadline_counter(monkeypatch):
    import chanlun_trader.account_startup_boundary_v1 as startup_module
    timer = clock(monkeypatch)
    monkeypatch.setattr(startup_module.time, 'monotonic', lambda: timer.now)
    frame = sys._getframe()
    old_global, old_local, old_lines = sys.gettrace(), frame.f_trace, frame.f_trace_lines
    # 外层测试不计行；每次调用一个不同代码文件中的单行普通帧。
    monkeypatch.setattr(startup_module, '_TRACE_CONTROL_FILES',
        startup_module._TRACE_CONTROL_FILES | {frame.f_code.co_filename})
    namespace = {}
    exec(compile('def one_line():\n    return 1\n', '<artificial-ordinary-startup>', 'exec'), namespace)
    one_line = namespace['one_line']
    boundary = AccountStartupBoundaryV1(10.)
    try:
        sys.settrace(None)
        frame.f_trace = None
        with pytest.raises(StartupBoundaryExceeded) as caught:
            with boundary.scope():
                boundary.stage = 'ARTIFICIAL_SHORT_FRAMES'
                for _ in range(63):
                    assert one_line() == 1
                assert boundary._trace_lines == 63
                timer.now = 10.
                one_line()
        assert caught.value.stage == 'ARTIFICIAL_SHORT_FRAMES'
        assert boundary.checkpoint_stage == 'PYTHON_LINE_CHECKPOINT'
        assert boundary._trace_lines == 0
        assert boundary.validation_complete is False
        assert sys.gettrace() is None and frame.f_trace is None
        assert frame.f_trace_lines is old_lines
        assert boundary._frame_traces == boundary._frame_trace_lines == {}
    finally:
        sys.settrace(old_global)
        frame.f_trace = old_local
        frame.f_trace_lines = old_lines


@pytest.mark.parametrize('previous_trace', [None, 'CALLBACK'])
def test_completed_startup_restores_active_plain_frames_and_external_trace(monkeypatch, previous_trace):
    import chanlun_trader.account_startup_boundary_v1 as startup_module
    timer = clock(monkeypatch)
    monkeypatch.setattr(startup_module.time, 'monotonic', lambda: timer.now)
    caller = sys._getframe()
    old_global, old_local, old_lines = sys.gettrace(), caller.f_trace, caller.f_trace_lines
    old_context = startup_module._ACTIVE.get()
    observed = []

    def existing_trace(frame, event, arg):
        if frame.f_code.co_name in ('ordinary', 'finish_inside'):
            observed.append((frame.f_code.co_name, event))
        return existing_trace

    expected = existing_trace if previous_trace == 'CALLBACK' else None
    boundary = AccountStartupBoundaryV1(10.)

    def ordinary():
        outer = sys._getframe()
        outer_lines = outer.f_trace_lines
        selected = boundary._plain_trace_callback if expected is None else boundary._trace_callback
        assert outer.f_trace is selected

        def finish_inside():
            inner = sys._getframe()
            inner_lines = inner.f_trace_lines
            assert inner.f_trace is selected
            boundary.completed()
            assert sys.gettrace() is expected
            assert outer.f_trace is inner.f_trace is expected
            assert outer.f_trace_lines is outer_lines and inner.f_trace_lines is inner_lines
            timer.now = 100.
            startup_checkpoint('ARTIFICIAL_DAILY_WORK_AFTER_PLAIN_FRAME')
            return 'DONE'

        return finish_inside()

    try:
        sys.settrace(expected)
        caller.f_trace = expected
        with boundary.scope():
            assert ordinary() == 'DONE'
        assert sys.gettrace() is expected and caller.f_trace is expected
        assert caller.f_trace_lines is old_lines
        assert startup_module._ACTIVE.get() is old_context
        assert boundary._frame_traces == boundary._frame_trace_lines == {}
        assert boundary.validation_complete is True
        if expected is not None:
            assert ('ordinary', 'call') in observed and ('finish_inside', 'line') in observed
    finally:
        sys.settrace(old_global)
        caller.f_trace = old_local
        caller.f_trace_lines = old_lines


def test_before_call_completion_does_not_reinstall_plain_trace_at_economic_entry(monkeypatch):
    import chanlun_trader.account_startup_boundary_v1 as startup_module
    timer = clock(monkeypatch)
    monkeypatch.setattr(startup_module.time, 'monotonic', lambda: timer.now)
    caller = sys._getframe()
    old_global, old_local, old_lines = sys.gettrace(), caller.f_trace, caller.f_trace_lines
    boundary = AccountStartupBoundaryV1(10.)
    observed = []

    def economic_entry():
        assert sys.gettrace() is None and sys._getframe().f_trace is None
        timer.now = 100.
        startup_checkpoint('ARTIFICIAL_ECONOMIC_DAY_AFTER_GUARD')
        observed.append('ECONOMIC')

    def before_call(frame):
        if frame.f_code is economic_entry.__code__:
            observed.append('GUARD')
            boundary.completed()

    def ordinary_dispatch():
        assert sys._getframe().f_trace is boundary._plain_trace_callback
        economic_entry()
        assert sys._getframe().f_trace is None

    boundary.before_call = before_call
    try:
        sys.settrace(None)
        caller.f_trace = None
        with boundary.scope():
            ordinary_dispatch()
        assert observed == ['GUARD', 'ECONOMIC']
        assert boundary.validation_complete is True
        assert sys.gettrace() is None and caller.f_trace is None
        assert caller.f_trace_lines is old_lines
        assert boundary._frame_traces == boundary._frame_trace_lines == {}
    finally:
        sys.settrace(old_global)
        caller.f_trace = old_local
        caller.f_trace_lines = old_lines


def test_emitting_diagnostic_does_not_consume_plain_frame_line_budget(monkeypatch):
    import chanlun_trader.account_startup_boundary_v1 as startup_module
    timer = clock(monkeypatch)
    monkeypatch.setattr(startup_module.time, 'monotonic', lambda: timer.now)
    caller = sys._getframe()
    old_global, old_local, old_lines = sys.gettrace(), caller.f_trace, caller.f_trace_lines
    observed = []

    def diagnostic(index, row, event):
        if event == 'ENTER':
            assert sys._getframe().f_trace is boundary._plain_trace_callback
            before = boundary._trace_lines
            timer.now = 11.
            total = 0
            for value in range(256):
                total += value
            observed.append((before, boundary._trace_lines, total))

    boundary = AccountStartupBoundaryV1(10., on_stage=diagnostic)
    try:
        sys.settrace(None)
        caller.f_trace = None
        with pytest.raises(StartupBoundaryExceeded) as caught:
            with boundary.scope(), boundary.stage_scope('ARTIFICIAL_EMITTING_DIAGNOSTIC'):
                pytest.fail('诊断耗时仍属于同一绝对截止，不能重新进入启动工作')
        assert observed and observed[0][0] == observed[0][1]
        assert observed[0][2] == sum(range(256))
        assert caught.value.stage == 'ARTIFICIAL_EMITTING_DIAGNOSTIC'
        assert boundary.validation_complete is False
        assert sys.gettrace() is None and caller.f_trace is None
        assert caller.f_trace_lines is old_lines
        assert boundary._frame_traces == boundary._frame_trace_lines == {}
    finally:
        sys.settrace(old_global)
        caller.f_trace = old_local
        caller.f_trace_lines = old_lines


@pytest.fixture
def runner_source_case(case, monkeypatch):
    install(case)
    resume(case)
    from chanlun_trader.research_factory import universe_submission_v1 as submission
    # 沿用已签发V1/6段历史，只装配人工经济scope；runner/from_job/Owner/来源/费用保持真实。
    # 完整未替换经济scope与worker链由显式双504合成另行验证。
    monkeypatch.setattr(submission, 'validate_frozen_universe_scopes', lambda job: None)
    return {**case, 'account': Path(case['job']['root'])}


@pytest.mark.parametrize('case', ['RUNNER_VALIDATION'], indirect=True)
def test_runner_validation_is_once_fresh_and_rejects_changes(runner_source_case, monkeypatch):
    from chanlun_trader.research_factory.account_version_continuation_v1 import AccountVersionContinuationV1
    case = runner_source_case
    original = AccountVersionContinuationV1.validate
    validations = []

    def observed(context, *args, **kwargs):
        validations.append(context)
        return original(context, *args, **kwargs)

    monkeypatch.setattr(AccountVersionContinuationV1, 'validate', observed)
    before = {path: path.read_bytes() for path in case['account'].rglob('*') if path.is_file()}
    first = runner.validate_sources(case['job'])
    assert validations == [first]
    second = runner.validate_sources(case['job'])
    assert first is not second and validations == [first, second]
    assert all(path.read_bytes() == raw for path, raw in before.items())
    first.validate(for_dispatch=True, name='SYNTHETIC_BASE')
    # 本单元只测入口委托；原277日/FAILED50/费用完整路径仍由原CI与完整合成验证。
    for changed in ('source', 'charge'):
        if changed == 'source':
            target = Path(case['bridge_summary']['source_replacements'][0]['path'])
        else:
            target = case['account'] / 'SYNTHETIC_BASE_SEGMENT_000006_CHARGE.json'
        raw = target.read_bytes()
        target.write_bytes(raw + b'\n')
        tampered = {path: path.read_bytes() for path in case['account'].rglob('*') if path.is_file()}
        try:
            with pytest.raises((PermissionError, ValueError)):
                runner.validate_sources(case['job'])
            with pytest.raises((PermissionError, ValueError)):
                first.validate_sources(case['job'])
            with pytest.raises((PermissionError, ValueError)):
                first.validate(for_dispatch=True, name='SYNTHETIC_BASE')
            assert all(path.read_bytes() == content for path, content in tampered.items()), changed
            assert not (case['account'] / 'SYNTHETIC_BASE_SEGMENT_000007_DISPATCH.json').exists()
        finally:
            target.write_bytes(raw)
    changed_job = deepcopy(case['job'])
    changed_job['input_identity'] = 'ARTIFICIAL_CHANGED_JOB'
    with pytest.raises(PermissionError, match='JOB_CHANGED'):
        runner.validate_sources(changed_job)
    with pytest.raises(PermissionError, match='JOB_ACCOUNT_VERSION_REPORT_REPAIR_CONFLICT'):
        runner.validate_sources(case['job'], _report_repair=object())
    assert all(path.read_bytes() == raw for path, raw in before.items())
    assert not (case['account'] / 'SYNTHETIC_BASE_SEGMENT_000007_DISPATCH.json').exists()


def test_runner_legacy_source_branch_still_reads_actual_bytes(tmp_path):
    target = tmp_path / 'artificial-legacy-source.py'
    target.write_bytes(b'artificial legacy source\n')
    item = {}
    job = {'root': str(tmp_path), 'items': {'LEGACY': item},
        'plans': {'LEGACY': {'runtime': item, 'backend': {'backend': 'ARTIFICIAL'}}},
        'source_hashes': {str(target): hashlib.sha256(target.read_bytes()).hexdigest()}}
    assert runner.validate_sources(job) is None
    target.write_bytes(target.read_bytes() + b'changed\n')
    with pytest.raises(PermissionError, match='JOB_FROZEN_SOURCE_CHANGED'):
        runner.validate_sources(job)


def late_guard_worker(tmp_path, monkeypatch, *, target, action, start=828.):
    """只替换经济函数和授权材料，保留真实 worker 的首次经济入口/截止路径。"""
    from chanlun_trader import account_startup_boundary_v1 as startup_module
    from chanlun_trader.research_factory import universe_account_backend_v2 as backend_module
    timer = clock(monkeypatch)
    path, observed = account_worker(tmp_path, monkeypatch, timer)
    job = runner.read_json(path)
    governance = runner.service(job)
    checkpoint = path.parent / 'CHECKPOINT.json'
    backend = object.__new__(backend_module.UniverseAccountBackendV2)
    expected_trace = sys.gettrace()
    observed.update(committed=False, slow_tail=False, after_guard=False, late_steps=[])

    def step(stage):
        if not observed['committed']:
            return
        observed['late_steps'].append(stage)
        if stage != target:
            return
        if action == 'DENIED':
            raise PermissionError('ARTIFICIAL_LATE_' + stage + '_DENIED')
        if action == 'SLOW':
            # 有限循环使旧实现产生真实红基线，避免用挂死作为测试断言。
            for _ in range(1000):
                timer.now += 1
            observed['slow_tail'] = True

    def active(name):
        assert name == 'FIXED'
        step('GOVERNANCE')
        return {'input_identity': 'INPUT'}

    def paused(*args):
        step('PAUSE')
        return False

    def project(receipt, name):
        assert name == 'FIXED'
        step('PROJECTION')
        return receipt

    def validate_dispatch(name, number):
        assert (name, number) == ('FIXED', 1)

    version = SimpleNamespace(recovery=None, validate_dispatch=validate_dispatch,
        active_item=lambda name: job['items'][name],
        active_plan=lambda name: job['plans'][name], proof=lambda name: {'fixture': 'LATE_GUARD'},
        project_receipt=project)
    governance.active_execution = active
    monkeypatch.setattr(runner, 'validate_sources', lambda value: version)
    monkeypatch.setattr(runner, '_execution_paused', paused)
    monkeypatch.setattr(runner, 'backend_for', lambda *args: backend)

    def economic_entry():
        assert sys.gettrace() is expected_trace
        assert startup_module.startup_boundary_active() is False
        runner.save(checkpoint, {'last_day': 278, 'state_identity': 'ARTIFICIAL_COMMITTED_DAY'})
        observed['checkpoint_bytes'] = checkpoint.read_bytes()
        observed['committed'] = True

    def backend_run(self, strategy, bundle, actions, guard):
        guard()
        backend_module.build_portfolio_plan()
        timer.now = start
        guard()  # 与 V2 的完整日 checkpoint 提交之后的治理调用对应。
        observed['after_guard'] = True
        assert sys.gettrace() is expected_trace
        assert startup_module.startup_boundary_active() is False
        raise runner_segment_boundary('TEST_COMPLETE_DAY_BOUNDARY')

    from chanlun_trader.research_factory.universe_account_backend_v2 import SegmentBoundary as runner_segment_boundary
    monkeypatch.setattr(backend_module, 'build_portfolio_plan', economic_entry)
    monkeypatch.setattr(backend_module.UniverseAccountBackendV2, 'run', backend_run)

    def public_run(strategy, selected_backend, **kwargs):
        kwargs['active_check']()
        return selected_backend.run(strategy, None, [], kwargs['active_check'])

    monkeypatch.setattr(runner, 'run', public_run)
    return path, timer, observed, checkpoint


@pytest.mark.parametrize('target', ['GOVERNANCE', 'PAUSE', 'PROJECTION'])
@pytest.mark.parametrize('start', [828., 835.])
def test_late_v2_guard_uses_original_deadline_before_returning(tmp_path, monkeypatch, target, start):
    from chanlun_trader import account_startup_boundary_v1 as startup_module
    path, timer, observed, checkpoint = late_guard_worker(
        tmp_path, monkeypatch, target=target, action='SLOW', start=start)
    previous_trace = sys.gettrace()
    assert runner._long_horizon_worker(path, 'FIXED', 1) == 75
    assert observed['committed'] is True
    assert observed['slow_tail'] is observed['after_guard'] is False
    assert timer.now < 890.
    assert checkpoint.read_bytes() == observed['checkpoint_bytes']
    status = runner.read_json(path.parent / 'FIXED_SEGMENT_000001_STATUS.json')
    startup = runner.read_json(path.parent / 'FIXED_SEGMENT_000001_STARTUP.json')
    cutoff = runner.read_json(path.parent / 'FIXED_SEGMENT_000001_STARTUP_ACTIVE_GUARD_CUTOFF.json')
    assert status['state'] == 'CONTINUE' and status['phase'] == 'ACCOUNT'
    assert status['reason'] == 'UNIVERSE_WORKER_COOPERATIVE_DEADLINE'
    assert startup['validation_complete'] is cutoff['startup_validation_complete'] is True
    assert cutoff['deadline_monotonic'] == 830.
    assert not (path.parent / 'FIXED_SEGMENT_000001_FAILURE.json').exists()
    assert sys.gettrace() is previous_trace and startup_module._ACTIVE.get() is None


@pytest.mark.parametrize('target', ['GOVERNANCE', 'PAUSE', 'PROJECTION'])
def test_late_v2_guard_permission_failure_stays_failed(tmp_path, monkeypatch, target):
    from chanlun_trader import account_startup_boundary_v1 as startup_module
    path, _, observed, checkpoint = late_guard_worker(
        tmp_path, monkeypatch, target=target, action='DENIED')
    previous_trace = sys.gettrace()
    with pytest.raises(PermissionError, match='ARTIFICIAL_LATE_' + target + '_DENIED'):
        runner._long_horizon_worker(path, 'FIXED', 1)
    assert checkpoint.read_bytes() == observed['checkpoint_bytes']
    assert observed['after_guard'] is False
    startup = runner.read_json(path.parent / 'FIXED_SEGMENT_000001_STARTUP.json')
    assert startup['validation_complete'] is True
    assert not (path.parent / 'FIXED_SEGMENT_000001_STATUS.json').exists()
    assert not (path.parent / 'FIXED_SEGMENT_000001_STARTUP_ACTIVE_GUARD_CUTOFF.json').exists()
    assert sys.gettrace() is previous_trace and startup_module._ACTIVE.get() is None


@pytest.mark.parametrize('external', [False, True])
def test_late_v2_guard_restores_trace_before_economic_execution(tmp_path, monkeypatch, external):
    from chanlun_trader import account_startup_boundary_v1 as startup_module
    caller = sys._getframe()
    old_global, old_local, old_lines = sys.gettrace(), caller.f_trace, caller.f_trace_lines

    def previous(frame, event, arg):
        return previous

    selected_trace = previous if external else None
    try:
        sys.settrace(selected_trace)
        caller.f_trace = selected_trace
        path, _, observed, checkpoint = late_guard_worker(
            tmp_path, monkeypatch, target='PROJECTION', action='PASS')
        assert runner._long_horizon_worker(path, 'FIXED', 1) == 75
        assert observed['late_steps'] == ['GOVERNANCE', 'PAUSE', 'PROJECTION']
        assert observed['after_guard'] is True
        assert checkpoint.read_bytes() == observed['checkpoint_bytes']
        assert sys.gettrace() is selected_trace and caller.f_trace is selected_trace
        assert caller.f_trace_lines is old_lines and startup_module._ACTIVE.get() is None
    finally:
        sys.settrace(old_global)
        caller.f_trace = old_local
        caller.f_trace_lines = old_lines
