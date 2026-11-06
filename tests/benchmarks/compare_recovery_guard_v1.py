"""只读计量已自然关闭的 R11 人工 guard；不是 worker、新增日或完整门禁。"""
import argparse
from collections import Counter
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import runpy
import stat
import statistics
import subprocess
import sys
import time
import traceback


ARTIFICIAL_ROOT = Path('E:/t/pr_CALENDAR_FULL_R11_2FD0_EXPIRED_ORIGINAL_20261017T210431104198Z/'
    'test_full_504_day_failed_accou0/r')
CLOSURE = Path('E:/llmwiki/trade-system-contract-port-v1/reports/'
    'continuous_universe_real_acceptance_20261010/initialization_recovery_20261012/'
    'FULL_R11_2FD0_NATURAL_COOPERATIVE_WINDOW_FAILURE_CLOSED_20261019.json')
CLOSURE_SHA256 = 'e553904a5d1fb7e529d82b6fb87326d301c1dff80abc79b93e4c99bd06a6ba76'
HELPER_SHA256 = 'f59b534a6e3d1bfc6bff0092373a721321034eda1542ab320177fd556991ad6c'
STARTUP_SECONDS = 830


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


def pinned_json(item):
    require(sha(item['path']) == item['sha256'], 'BENCHMARK_PIN_CHANGED')
    return read_json(item['path'])


def same_path(left, right):
    return os.path.normcase(str(Path(left).resolve())) == os.path.normcase(str(Path(right).resolve()))


def files_snapshot(root):
    """遍历整个人工根，包括全部侧车；不跟随链接或重解析点。"""
    result = {}
    for parent, directories, files in os.walk(root, followlinks=False):
        for name in directories + files:
            path = Path(parent) / name
            metadata = path.lstat()
            require(not stat.S_ISLNK(metadata.st_mode)
                and not getattr(metadata, 'st_file_attributes', 0) & 0x400,
                'BENCHMARK_FIXTURE_LINK_REJECTED')
            if name in files:
                require(stat.S_ISREG(metadata.st_mode), 'BENCHMARK_FIXTURE_REGULAR_FILE_REQUIRED')
                result[path.relative_to(root).as_posix()] = sha(path)
    return dict(sorted(result.items()))


def source_snapshot(repo):
    return {path.relative_to(repo).as_posix(): sha(path)
        for folder in ('src', 'scripts', 'tests')
        for path in sorted((repo / folder).rglob('*.py'))}


def changed_files(before, after):
    return sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))


def store_json(path, value):
    raw = json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, indent=2) + '\n'
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    require(read_json(path) == value, 'BENCHMARK_OUTPUT_READBACK_CONFLICT')


class ReadMetrics:
    """原函数仍逐次执行；计时为包含子调用的时间，不能相加当作 CPU 时间。"""

    def __init__(self, secure):
        self.secure = secure
        self.counts = Counter()
        self.seconds = Counter()
        self.opened = Counter()
        self.walk_depth = 0

    @contextmanager
    def install(self):
        original_reader = self.secure._regular_reader
        original_walk = self.secure._reject_link_components
        original_metadata = getattr(self.secure, '_component_metadata', None)
        original_lstat = Path.lstat
        owner = self

        def lstat_call(path, *args, **kwargs):
            began = time.perf_counter()
            try:
                return original_lstat(path, *args, **kwargs)
            finally:
                elapsed = time.perf_counter() - began
                owner.counts['path_lstat_calls'] += 1
                owner.seconds['path_lstat_seconds'] += elapsed
                if owner.walk_depth:
                    owner.counts['component_lstat_calls'] += 1
                    owner.seconds['component_lstat_seconds'] += elapsed

        def walk_call(path):
            owner.counts['component_walk_calls'] += 1
            owner.walk_depth += 1
            began = time.perf_counter()
            try:
                return original_walk(path)
            finally:
                owner.seconds['component_walk_seconds'] += time.perf_counter() - began
                owner.walk_depth -= 1

        def metadata_call(path):
            owner.counts['component_metadata_queries'] += 1
            began = time.perf_counter()
            try:
                return original_metadata(path)
            finally:
                owner.seconds['component_metadata_seconds'] += time.perf_counter() - began

        class CountedStream:
            def __init__(self, stream):
                self.stream = stream

            def _read(self, method, *args, **kwargs):
                began = time.perf_counter()
                try:
                    value = getattr(self.stream, method)(*args, **kwargs)
                    owner.counts['read_bytes'] += value if method == 'readinto' else len(value)
                    return value
                finally:
                    owner.counts['read_calls'] += 1
                    owner.seconds['read_seconds'] += time.perf_counter() - began

            def read(self, *args, **kwargs):
                return self._read('read', *args, **kwargs)

            def readline(self, *args, **kwargs):
                return self._read('readline', *args, **kwargs)

            def readinto(self, *args, **kwargs):
                return self._read('readinto', *args, **kwargs)

            def __iter__(self):
                return self

            def __next__(self):
                value = self.readline()
                if not value:
                    raise StopIteration
                return value

            def __getattr__(self, name):
                return getattr(self.stream, name)

        @contextmanager
        def reader_call(value, **kwargs):
            owner.counts['regular_reader_attempts'] += 1
            began = time.perf_counter()
            with original_reader(value, **kwargs) as stream:
                owner.seconds['secure_open_seconds'] += time.perf_counter() - began
                owner.counts['regular_reader_opens'] += 1
                owner.opened[str(value)] += 1
                yield CountedStream(stream)

        Path.lstat = lstat_call
        self.secure._reject_link_components = walk_call
        self.secure._regular_reader = reader_call
        if original_metadata is not None:
            self.secure._component_metadata = metadata_call
        try:
            yield self
        finally:
            self.secure._regular_reader = original_reader
            self.secure._reject_link_components = original_walk
            Path.lstat = original_lstat
            if original_metadata is not None:
                self.secure._component_metadata = original_metadata

    def snapshot(self):
        return {**dict(self.counts), **dict(self.seconds), 'unique_open_paths': len(self.opened),
            'fresh_component_queries': self.counts.get('component_metadata_queries',
                self.counts['component_lstat_calls']),
            'most_opened_paths': [{'path': path, 'count': count}
                for path, count in self.opened.most_common(12)]}


def context_identity(context, stable_hash):
    require(context is not None, 'BENCHMARK_CONTINUATION_CONTEXT_REQUIRED')
    return stable_hash({'summary': context.summary_value, 'manifest_ref': context.scope_reference,
        'approval_ref': context.approval_ref, 'proof': context.proof(), 'job': context.job,
        'recovery_installed': context.recovery_installed,
        'time_extension_installed': context.time_extension_installed})


def install_artificial_catalog(root):
    """执行原人工提供者；只将其动态来源根绑定原夹具，不改任何工件或许可。"""
    helper = root / 'src/sitecustomize.py'
    require(sha(helper) == HELPER_SHA256, 'BENCHMARK_SYNTHETIC_HELPER_CHANGED')
    namespace = runpy.run_path(str(helper), run_name='_readonly_r11_synthetic_capabilities')
    provider = namespace['synthetic_capabilities']
    provider.__globals__['source_root'] = root / 'src/chanlun_trader/research_factory'
    return {'helper_ref': reference(helper), 'provider': 'ORIGINAL_DYNAMIC_SYNTHETIC_CAPABILITIES',
        'source_root': str(provider.__globals__['source_root']), 'permission_or_result_projection': False}


def run(args, result):
    root = args.artificial_root.resolve(strict=True)
    require(same_path(root, ARTIFICIAL_ROOT), 'BENCHMARK_ONLY_CLOSED_R11_ARTIFICIAL_ROOT')
    require(sys.dont_write_bytecode, 'BENCHMARK_REQUIRES_PYTHONDONTWRITEBYTECODE_1')
    result['artificial_root'] = str(root)
    result['closure_ref'] = {'path': str(CLOSURE), 'sha256': CLOSURE_SHA256}
    closure = pinned_json(result['closure_ref'])
    require(closure['natural_exit'] is True and closure['finally_read_closed'] is True
        and closure['returncode'] == 1 and closure['base_days'] == 327 and closure['no_segment88'] is True
        and closure['old8_and_revision_residuals_exited'] is True, 'BENCHMARK_NATURAL_CLOSURE_REQUIRED')
    transport_ref = closure['scenario_transport_ref']
    require(same_path(transport_ref['path'], root / 'SCENARIO_TRANSPORT_RESULT.json'),
        'BENCHMARK_TRANSPORT_ROOT_CONFLICT')
    transport = pinned_json(transport_ref)
    require(transport['status'] == 'EXITED' and transport['returncode'] == 1,
        'BENCHMARK_SCENARIO_NOT_NATURALLY_CLOSED')
    result['fixture_files_before'] = before = files_snapshot(root)
    launch = read_json(root / 'SCENARIO_LAUNCH.json')
    require(same_path(launch['root'], root) and launch['real_market_or_performance_acceptance'] is False,
        'BENCHMARK_ARTIFICIAL_SCENARIO_REQUIRED')
    result['scenario_launch_ref'] = reference(root / 'SCENARIO_LAUNCH.json')
    result['fixture_source_sha256'] = {name: value for name, value in before.items()
        if name.startswith(('src/', 'scripts/')) and name.endswith('.py')}
    launch_sources = {Path(name).as_posix(): value for name, value in launch['source_sha256'].items()}
    result['fixture_source_differences_from_original_launch'] = changed_files(
        launch_sources, result['fixture_source_sha256'])
    jobs = list((root / 'reports/public').glob('*/account/JOB.json'))
    require(len(jobs) == 1, 'BENCHMARK_SINGLE_ARTIFICIAL_JOB_REQUIRED')
    job = read_json(jobs[0])
    require(same_path(job['root'], jobs[0].parent) and job['plans']
        and set(job['plans']) == set(job['items'])
        and all(name.startswith('FULL_FIXTURE_') for name in job['plans']),
        'BENCHMARK_FULL_FIXTURE_PURPOSES_REQUIRED')
    require(not any(jobs[0].parent.glob('*_SEGMENT_000088_DISPATCH.json')),
        'BENCHMARK_CLOSED_PREFIX_CHANGED')
    manifests = list(jobs[0].parent.parent.glob('account-version-continuations/*/MANIFEST.json'))
    require(len(manifests) == 1, 'BENCHMARK_SINGLE_CONTINUATION_REQUIRED')
    summary = read_json(manifests[0])['summary']
    require(same_path(summary['root'], root) and same_path(summary['original']['job']['path'], jobs[0])
        and same_path(summary['approval_store'], root.parent / 'protected_synthetic_owner'),
        'BENCHMARK_ONLY_ARTIFICIAL_OWNER_REQUIRED')
    result['job_ref'] = reference(jobs[0])
    result['continuation_ref'] = manifest_ref = reference(manifests[0])

    from chanlun_trader.account_startup_boundary_v1 import AccountStartupBoundaryV1
    from chanlun_trader.research_factory import secure_file_reference_v1 as secure
    from chanlun_trader.research_factory.account_version_continuation_v1 import AccountVersionContinuationV1
    from chanlun_trader.research_factory.common import stable_hash
    from chanlun_trader.research_factory.research_campaign_v1 import ResearchCampaignV1
    repo = Path(secure.__file__).resolve().parents[3]
    result['imported_repo'] = str(repo)
    result['source_sha256_before'] = sources_before = source_snapshot(repo)
    git = ['git', '-c', 'gc.auto=0', '-c', 'maintenance.auto=false']
    result['git_head'] = subprocess.check_output(git + ['rev-parse', 'HEAD'], cwd=repo,
        text=True, encoding='utf-8').strip()
    result['git_tree'] = subprocess.check_output(git + ['rev-parse', 'HEAD^{tree}'], cwd=repo,
        text=True, encoding='utf-8').strip()
    result['synthetic_capability_projection'] = install_artificial_catalog(root)
    runner = importlib.import_module('scripts.run_strategy_account_v1')
    require(Path(runner.__file__).resolve().is_relative_to(repo), 'BENCHMARK_RUNNER_IMPORT_SOURCE_CONFLICT')
    campaign = ResearchCampaignV1(root, summary['authorization_id'])
    operations = (
        ('campaign.peek_status', campaign.peek_status, stable_hash),
        ('runner.validate_sources', lambda: runner.validate_sources(job),
            lambda value: context_identity(value, stable_hash)),
        ('context.validate', lambda: AccountVersionContinuationV1(root, manifest_ref).validate(for_dispatch=False),
            lambda value: context_identity(value, stable_hash)),
    )
    identities = {}
    try:
        for traced in (False, True):
            for repetition in range(1, args.repeat + 1):
                for operation, call, identify in operations:
                    row = {'operation': operation, 'traced': traced, 'repeat': repetition,
                        'status': 'RUNNING', 'recorded_at': datetime.now(timezone.utc).isoformat()}
                    boundary = AccountStartupBoundaryV1(time.monotonic() + STARTUP_SECONDS) if traced else None
                    metrics = ReadMetrics(secure)
                    began = time.perf_counter()
                    try:
                        with metrics.install(), boundary.scope() if boundary is not None else nullcontext():
                            value = call()
                        row['elapsed_seconds'] = time.perf_counter() - began
                        row['result_identity'] = identify(value)
                        expected = identities.setdefault(operation, row['result_identity'])
                        require(row['result_identity'] == expected, 'BENCHMARK_RESULT_IDENTITY_CHANGED')
                        row['status'] = 'PASSED'
                    except Exception as exc:
                        row.update(status='FAILED', elapsed_seconds=time.perf_counter() - began,
                            exception_type=type(exc).__name__, exception=str(exc), traceback=traceback.format_exc())
                        raise
                    finally:
                        row['secure_io'] = metrics.snapshot()
                        if boundary is not None:
                            snapshot = boundary.snapshot()
                            snapshot.pop('deadline_monotonic', None)
                            row['startup_boundary'] = snapshot
                        index = len(result['rows']) + 1
                        row_path = args.output.with_name(args.output.name + '.row-' + str(index).zfill(3) + '.json')
                        store_json(row_path, row)
                        result['rows'].append({**row, 'row_ref': reference(row_path)})
                        print(json.dumps(row, ensure_ascii=True), flush=True)
    finally:
        after = files_snapshot(root)
        result['fixture_files_after'] = after
        result['changed_fixture_files'] = changed_files(before, after)
        result['new_dispatch_files'] = sorted(name for name in after.keys() - before.keys()
            if name.endswith('_DISPATCH.json'))
        result['new_charge_files'] = sorted(name for name in after.keys() - before.keys()
            if name.endswith('_CHARGE.json'))
        result['source_sha256_after'] = sources_after = source_snapshot(repo)
        result['changed_imported_repo_sources'] = changed_files(sources_before, sources_after)
        result['imported_sources'] = {name: reference(Path(module.__file__).resolve())
            for name, module in tuple(sys.modules.items())
            if name.startswith(('chanlun_trader', 'scripts.run_strategy_account_v1'))
            and getattr(module, '__file__', None) and Path(module.__file__).suffix == '.py'}
    require(not result['changed_fixture_files'] and not result['changed_imported_repo_sources'],
        'BENCHMARK_READ_ONLY_OR_SOURCE_CHANGED')
    require(identities['runner.validate_sources'] == identities['context.validate'],
        'BENCHMARK_PUBLIC_CONTEXT_IDENTITY_CONFLICT')
    result['result_identities'] = identities
    result['median_seconds'] = {operation: {str(traced): statistics.median(row['elapsed_seconds']
        for row in result['rows'] if row['operation'] == operation and row['traced'] is traced)
        for traced in (False, True)} for operation, _, _ in operations}
    result['status'] = 'PASSED'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artificial-root', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--label', required=True)
    parser.add_argument('--repeat', type=int, choices=range(1, 4), default=1)
    args = parser.parse_args()
    args.output = args.output.resolve()
    require(not args.output.is_relative_to(ARTIFICIAL_ROOT.resolve()), 'BENCHMARK_OUTPUT_INSIDE_FIXTURE')
    require(args.output.parent.is_dir() and not args.output.exists(), 'BENCHMARK_NEW_OUTPUT_REQUIRED')
    result = {'status': 'RUNNING', 'label': args.label, 'rows': [],
        'recorded_at': datetime.now(timezone.utc).isoformat(),
        'benchmark_script_ref': reference(Path(__file__).resolve()), 'startup_seconds': STARTUP_SECONDS,
        'limits': '只读 guard 计量；不是完整启动、提交、新增日、worker 预算可行性或完整门禁。'
            'I/O 计时含子调用与计数器开销，不相加为纯 CPU 时间；未执行账户、安装或派发。'}
    try:
        run(args, result)
    except Exception as exc:
        result.update(status='FAILED', exception_type=type(exc).__name__, exception=str(exc),
            traceback=traceback.format_exc())
    result['completed_at'] = datetime.now(timezone.utc).isoformat()
    store_json(args.output, result)
    print(json.dumps({'status': result['status'], 'output_ref': reference(args.output),
        'rows': len(result['rows'])}, ensure_ascii=True), flush=True)
    return 0 if result['status'] == 'PASSED' else 1


if __name__ == '__main__':
    raise SystemExit(main())
