"""独立进程读取人工 BASE504/STRESS1 的结清夹具；不派发、不读取真实价格。"""
from contextlib import nullcontext
from copy import deepcopy
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

from chanlun_trader.account_startup_boundary_v1 import AccountStartupBoundaryV1
from chanlun_trader.research_factory import continuous_research_contract_v1 as contracts
from chanlun_trader.research_factory import failed_account_recovery_v1 as recovery


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artificial-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--label', required=True)
    parser.add_argument('--expect-revision', action='store_true')
    args = parser.parse_args()
    root = args.artificial_root
    assert root.is_absolute() and root.name.startswith('revision')
    assert not args.output.resolve().is_relative_to(root.resolve())
    manifests = [p for p in (root / 'reports/tasks').glob('*/failed-account-recoveries/*/MANIFEST.json')
        if p.parent.name != 'governance-source-revision']
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text(encoding='utf-8'))
    summary = manifest['summary']
    assert summary['root'] == str(root) and summary['original_failed_account'] == 'ARTIFICIAL_BASE'
    assert set(summary['accounts']) == {'ARTIFICIAL_BASE', 'ARTIFICIAL_STRESS'}
    assert summary['accounts']['ARTIFICIAL_BASE']['checkpoint']['processed_days'] == 277
    assert summary['accounts']['ARTIFICIAL_BASE']['segment_count'] == 50
    account = Path(summary['original']['job']['path']).parent
    job = json.loads(Path(summary['original']['job']['path']).read_text(encoding='utf-8'))
    for name, days, segments in (('ARTIFICIAL_BASE', 504, 51), ('ARTIFICIAL_STRESS', 1, 1)):
        cp = json.loads(Path(job['items'][name]['backend_options']['checkpoint_path']).read_text(encoding='utf-8'))
        assert len(cp['artifacts']['days']) == days
        assert len(list(account.glob(name + '_SEGMENT_*_CHARGE.json'))) == segments
        assert not (account / (name + '_SEGMENT_' + str(segments + 1).zfill(6) + '_DISPATCH.json')).exists()
    before = {str(p): digest(p) for p in account.rglob('*') if p.is_file()}
    current = recovery.bridges.capabilities()
    current['full_universe']['source_hashes']['universe_account_inputs_v1.py'] = digest(root / recovery.bridges.BUSINESS_PATH)
    recovery.bridges._fingerprint(current)
    assert current['fingerprint'] == summary['live_capabilities_fingerprint']
    # 原单元夹具的人工业务投影；实际 Owner、来源、账本与日链校验全部执行。
    contracts.capabilities = lambda: deepcopy(current)
    recovery.bridges.capabilities = lambda: deepcopy(current)
    reference = {'path': str(manifests[0]), 'sha256': digest(manifests[0])}
    rows = []
    identities = set()
    for traced in (False, True):
        for repeat in range(3):
            boundary = AccountStartupBoundaryV1(time.monotonic() + 830) if traced else None
            began = time.perf_counter()
            with boundary.scope() if boundary else nullcontext():
                context = recovery.FailedAccountRecoveryV1.read(root, reference)
            seconds = time.perf_counter() - began
            assert (getattr(context, 'governance_revision', None) is not None) is args.expect_revision
            identity = recovery.stable_hash({'summary': context.summary_value, 'proof': context.proof(),
                'expires_at': context.execution_expires_at, 'approval_ref': context.approval_ref})
            identities.add(identity)
            rows.append({'traced': traced, 'repeat': repeat + 1, 'elapsed_seconds': seconds, 'identity': identity})
    assert len(identities) == 1
    assert before == {str(p): digest(p) for p in account.rglob('*') if p.is_file()}
    modules = {name: {'path': str(Path(module.__file__).resolve()), 'sha256': digest(Path(module.__file__))}
        for name, module in tuple(sys.modules.items()) if name.startswith('chanlun_trader')
        and getattr(module, '__file__', None) and Path(module.__file__).suffix == '.py'}
    value = {'label': args.label, 'fixture_only': 'ARTIFICIAL_CONTRACT_STATE_504_AND_1_NOT_WORKER',
        'manifest_ref': reference, 'root': str(root), 'revision_enabled': args.expect_revision, 'rows': rows,
        'identity': identities.pop(), 'median_seconds': {str(mode): statistics.median(
            row['elapsed_seconds'] for row in rows if row['traced'] is mode) for mode in (False, True)},
        'changed_account_files': [], 'dispatched_segments': 0, 'imported_sources': modules,
        'limits': 'Recovery read including revision overhead; not ordinary segment, actual economic throughput or complete gate.'}
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=True, indent=2)
        stream.write('\n')


if __name__ == '__main__':
    main()
