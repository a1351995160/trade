"""显式恢复未结算的全范围回测；沿用原 START、预算和时间上限。"""
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import time

from ..research_daemon_state import DaemonInstanceLockV1
from .common import stable_hash
from .engine_replay_recovery_v1 import _read_receipt
from .mutation_boundary import ObjectiveMutationLock


def resume_universe_job(path):
    from scripts import run_strategy_account_v1 as runner
    from ..synthetic_batch_resources import run_bounded_worker
    job = runner.read_json(path)
    runner.validate_sources(job)
    root = Path(job['root'])
    if root.resolve() != root or root != Path(path).resolve().parent:
        raise PermissionError('UNIVERSE_RESUME_ROOT_CONFLICT')
    if any(p['backend']['backend'] != 'UNIVERSE_ACCOUNT_BACKEND_V1' for p in job['plans'].values()):
        raise PermissionError('UNIVERSE_RESUME_VERSION_REQUIRED')
    with ObjectiveMutationLock.for_resource(root / 'RESUME.lock'):
        for name in job['plans']:
            start_path = root / (name + '_START.json')
            if not start_path.exists() or (root / (name + '_SETTLEMENT.json')).exists():
                continue
            if (root / (name + '_RESULT.json')).exists():
                runner.reconcile_account(path, name)
                continue
            gov = runner.service(job)
            receipt = gov.active_execution(name)
            start = runner.read_json(start_path)
            worker_path = root / (name + '_WORKER.json')
            if not worker_path.exists():
                raise PermissionError('UNIVERSE_RESUME_WORKER_START_UNKNOWN')
            worker = runner.read_json(worker_path)
            readers = [worker['pid']]
            access_path = root / (name + '_INPUT_ACCESS.json')
            if access_path.exists():
                readers.append(runner.read_json(access_path)['reader_pid'])
            if any(DaemonInstanceLockV1._pid_alive(int(pid)) for pid in readers):
                raise PermissionError('UNIVERSE_RESUME_WORKER_STILL_ACTIVE')
            checkpoint = Path(job['items'][name]['backend_options']['checkpoint_path'])
            if checkpoint.parent != root or checkpoint.resolve() != checkpoint:
                raise PermissionError('UNIVERSE_RESUME_CHECKPOINT_PATH_CONFLICT')
            committed = _read_receipt(checkpoint, root, job['input_identity'])
            if not committed:
                raise PermissionError('UNIVERSE_RESUME_NO_DAILY_COMMIT')
            # 包括主机停顿时间，保守扣减，不因重启重新得到900秒。
            spent = (datetime.now(timezone.utc) - datetime.fromisoformat(start['started_at'])).total_seconds()
            bounds = job['resources']
            remaining = min(bounds['worker_seconds'] - spent,
                (datetime.fromisoformat(receipt['expires_at']) - datetime.now(timezone.utc)).total_seconds())
            if receipt['source'].get('origin') == 'CAMPAIGN_V1':
                from .etf_account_governance_v1 import validate_campaign_source
                campaign = validate_campaign_source(receipt['source'], job['plans'], job['objective_id'])
                operation = campaign.status()['operations'][receipt['source']['operation_ids'][name]]
                remaining = min(remaining, operation['upper_bounds']['wall_seconds'] - spent)
            if spent < 0 or remaining <= 0:
                raise PermissionError('UNIVERSE_RESUME_ORIGINAL_TIME_BOUND_EXHAUSTED')
            record = {'name': name, 'start_identity': stable_hash(start),
                'checkpoint_identity': committed['receipt_hash'], 'spent_before_resume': spent,
                'remaining_wall_seconds': remaining, 'budget_reused': True}
            archive = root / 'resume-attempts' / (name + '_' + stable_hash(record))
            archive.mkdir(parents=True)
            runner.save(archive / 'RESUME.json', record)
            for suffix in ('_WORKER.json', '_INPUT_ACCESS.json', '_RESOURCE.json', '_FAILURE.json'):
                old_path = root / (name + suffix)
                if old_path.exists():
                    if old_path.resolve() != old_path:
                        raise PermissionError('UNIVERSE_RESUME_EVIDENCE_REDIRECTED')
                    old_path.replace(archive / old_path.name)
            env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1',
                   **{key: '1' for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS')}}
            env['PYTHONPATH'] = str(runner.REPO / 'src') + os.pathsep + env.get('PYTHONPATH', '')
            env.pop('CHANLUN_TEST_ISOLATION', None)
            begin = time.monotonic()
            try:
                resource = run_bounded_worker([sys.executable, str(Path(runner.__file__).resolve()),
                    '--worker', name, '--job', str(Path(path).resolve())], root=runner.REPO,
                    memory_mib=bounds['memory_mib'], wall_seconds=remaining, environment=env,
                    execution={'purpose': name}, on_started=lambda pid: runner.save(worker_path, {'pid': pid, 'purpose': name}))
            except Exception as exc:
                resource = {'returncode': None, 'timed_out': False, 'error_type': type(exc).__name__, 'error': str(exc)}
            elapsed = spent + time.monotonic() - begin
            resource.update(elapsed_wall_seconds=elapsed, resumed=True, original_budget_reused=True,
                            resume_evidence=str(archive / 'RESUME.json'))
            runner.save(root / (name + '_RESOURCE.json'), {key: value.decode('utf-8', errors='replace')
                if isinstance(value, bytes) else value for key, value in resource.items()})
            output = root / (name + '_RESULT.json')
            complete = resource['returncode'] == 0 and not resource.get('timed_out') and output.exists()
            gov.settle(name, completed=complete, seconds=elapsed,
                result_hash=runner.sha(output) if output.exists() else None,
                error=None if complete else 'UNIVERSE_RESUME_WORKER_FAILED')
            if not complete:
                raise RuntimeError('UNIVERSE_RESUME_FAILED_NO_AUTOMATIC_RETRY')
    return runner.execute_accounts(path, recover=True)
