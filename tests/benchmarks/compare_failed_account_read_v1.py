"""对已生成的人工277日/50FAILED夹具做只读对照；不安装、不派发或续费。"""
import argparse
from collections import Counter
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

from chanlun_trader.account_startup_boundary_v1 import AccountStartupBoundaryV1
from chanlun_trader.research_factory import continuous_research_contract_v1 as contracts
from chanlun_trader.research_factory import failed_account_recovery_v1 as recovery
from chanlun_trader.research_factory import secure_file_reference_v1 as secure


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def account_files(root):
    return {str(path.relative_to(root)): digest(path) for path in root.rglob('*') if path.is_file()}


def benchmark_output(root, output):
    target = output.resolve()
    assert not target.is_relative_to(root.resolve(strict=True)), 'BENCHMARK_OUTPUT_INSIDE_FIXTURE'
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artificial-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--label', required=True)
    parser.add_argument('--repeat', type=int, default=3)
    args = parser.parse_args()
    root = args.artificial_root
    assert root.is_absolute() and root.name.startswith('fresh-recovery-readers')
    output = benchmark_output(root, args.output)
    assert 1 <= args.repeat <= 5
    manifests = list((root / 'reports/tasks').glob('*/failed-account-recoveries/*/MANIFEST.json'))
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text(encoding='utf-8'))
    summary = manifest['summary']
    assert summary['root'] == str(root)
    assert summary['original_failed_account'] == 'ARTIFICIAL_BASE'
    assert set(summary['accounts']) == {'ARTIFICIAL_BASE', 'ARTIFICIAL_STRESS'}
    assert summary['accounts']['ARTIFICIAL_BASE']['checkpoint']['processed_days'] == 277
    assert summary['accounts']['ARTIFICIAL_BASE']['segment_count'] == 50
    account = Path(summary['original']['job']['path']).parent
    assert account.is_relative_to(root / 'reports')
    assert not (account / 'ARTIFICIAL_BASE_SEGMENT_000051_DISPATCH.json').exists()
    before = account_files(account)
    reference = {'path': str(manifests[0]), 'sha256': digest(manifests[0])}
    current = recovery.bridges.capabilities()
    current['full_universe']['source_hashes']['universe_account_inputs_v1.py'] = digest(
        root / recovery.bridges.BUSINESS_PATH)
    recovery.bridges._fingerprint(current)
    assert current['fingerprint'] == summary['live_capabilities_fingerprint']
    # 复用原extension_case的人工业务投影；入口仍完整认证Owner、来源及所有原件。
    contracts.capabilities = lambda: deepcopy(current)
    recovery.bridges.capabilities = lambda: deepcopy(current)
    original_reader = secure._regular_reader
    original_walk = secure._reject_link_components
    opened = Counter()
    walks = []

    def counted_reader(value, **kwargs):
        opened[str(value)] += 1
        return original_reader(value, **kwargs)

    def counted_walk(path):
        walks.append(str(path))
        return original_walk(path)

    secure._regular_reader = counted_reader
    secure._reject_link_components = counted_walk
    rows = []
    identities = set()
    for traced in (False, True):
        for repeat in range(args.repeat):
            opened.clear()
            walks.clear()
            boundary = AccountStartupBoundaryV1(time.monotonic() + 830) if traced else None
            began = time.perf_counter()
            with boundary.scope() if boundary is not None else nullcontext():
                context = recovery.FailedAccountRecoveryV1.read(root, reference)
            elapsed = time.perf_counter() - began
            identity = recovery.stable_hash({'summary': context.summary_value, 'proof': context.proof(),
                'expires_at': context.execution_expires_at, 'approval_ref': context.approval_ref})
            identities.add(identity)
            row = {'traced': traced, 'repeat': repeat + 1, 'elapsed_seconds': elapsed,
                'regular_reader_opens': sum(opened.values()), 'unique_paths': len(opened),
                'full_component_walks': len(walks), 'result_identity': identity}
            rows.append(row)
            print(json.dumps(row, ensure_ascii=True), flush=True)
    assert len(identities) == 1
    assert account_files(account) == before
    assert not (account / 'ARTIFICIAL_BASE_SEGMENT_000051_DISPATCH.json').exists()
    modules = {}
    for name, module in tuple(sys.modules.items()):
        path = getattr(module, '__file__', None)
        if name.startswith('chanlun_trader') and path and Path(path).suffix == '.py':
            modules[name] = {'path': path, 'sha256': digest(Path(path))}
    result = {'recorded_at': datetime.now(timezone.utc).isoformat(), 'label': args.label,
        'benchmark_script_ref': {'path': str(Path(__file__).resolve()), 'sha256': digest(Path(__file__))},
        'fixture_only': 'ARTIFICIAL_277_DAYS_50_FAILED_OWNER_READ_NO_WORKER',
        'artificial_root': str(root), 'manifest_ref': reference,
        'result_identity': identities.pop(), 'rows': rows,
        'median_seconds': {str(traced): statistics.median(row['elapsed_seconds'] for row in rows
            if row['traced'] is traced) for traced in (False, True)},
        'changed_account_files': [], 'dispatched_segments': 0, 'imported_sources': modules,
        'limits': 'Read-only governance benchmark; not full504, pure strategy CPU time or overall ETA.'}
    with output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=True, indent=2)
        stream.write('\n')


if __name__ == '__main__':
    main()
