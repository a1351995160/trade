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
from .secure_file_reference_v1 import checked_directory_path, checked_file_path, validated_reference_path


def resume_universe_job(path):
    from scripts import run_strategy_account_v1 as runner
    from ..synthetic_batch_resources import run_bounded_worker
    job = runner.read_json(path)
    runner.validate_sources(job)
    root = Path(job['root'])
    if root.resolve() != root or root != Path(path).resolve().parent:
        raise PermissionError('UNIVERSE_RESUME_ROOT_CONFLICT')
    if 'profile_id' in job['resources']:
        return resume_long_horizon_job(path,job)
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
            env['PYTHONPATH'] = os.pathsep.join((str(runner.REPO / 'src'),str(runner.REPO),env.get('PYTHONPATH', '')))
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


def resume_long_horizon_job(path,job,*,reconcile_only=False):
    """新语义只扣已运行段；未知崩溃扣该段上界，停机等待不免费重置。"""
    from scripts import run_strategy_account_v1 as runner
    path = checked_file_path(path, error_code='UNIVERSE_RESUME_ROOT_CONFLICT')
    root = checked_directory_path(job['root'], error_code='UNIVERSE_RESUME_ROOT_CONFLICT')
    if root != path.parent:
        raise PermissionError('UNIVERSE_RESUME_ROOT_CONFLICT')
    checkpoints, feature_bindings = {}, {}
    for name in job['plans']:
        start = validated_reference_path(root / (name + '_START.json'), root=root,
            error_code='UNIVERSE_RESUME_ROOT_CONFLICT')
        if start.parent != root:
            raise PermissionError('UNIVERSE_RESUME_ROOT_CONFLICT')
        checkpoint = validated_reference_path(job['items'][name]['backend_options']['checkpoint_path'], root=root,
            error_code='UNIVERSE_RESUME_CHECKPOINT_PATH_CONFLICT')
        feature_binding = validated_reference_path(root /
            (job['plans'][name]['strategy']['strategy_id'] + '_FEATURES') / 'PREPARATION_BINDING.json', root=root,
            error_code='UNIVERSE_RESUME_CHECKPOINT_PATH_CONFLICT')
        if checkpoint.parent != root or feature_binding.parent.parent != root:
            raise PermissionError('UNIVERSE_RESUME_CHECKPOINT_PATH_CONFLICT')
        checkpoints[name], feature_bindings[name] = checkpoint, feature_binding
    runner.validate_sources(job)
    if (job['resources']['purpose'] != 'RESEARCH_ACCOUNT'
            or any(p['backend']['backend'] != 'UNIVERSE_ACCOUNT_BACKEND_V2' for p in job['plans'].values())):
        raise PermissionError('UNIVERSE_LONG_HORIZON_RESUME_VERSION_REQUIRED')
    gov=runner.service(job)
    with ObjectiveMutationLock.for_resource(root/'RESUME.lock'):
        for name in job['plans']:
            if not (root/(name+'_START.json')).exists():
                continue
            if (root/(name+'_SETTLEMENT.json')).exists():
                runner.validated_settlement(job,name)
                continue
            gov.reconcile_segment_mirrors(name)
            gov.dispatched_execution(name); state=gov.segment_status(name)
            pending=state['pending']
            if pending is None:
                if state['segments'] and state['segments'][-1]['charge']['outcome'] in ('COMPLETED','FAILED'):
                    last=state['segments'][-1];prefix=runner.segment_prefix(name,last['dispatch']['segment_number'])
                    outcome=last['charge']['outcome'];output=root/(name+'_RESULT.json')
                    resource_path=root/(prefix+'_RESOURCE.json');failure_path=root/(prefix+'_FAILURE.json')
                    evidence=resource_path if resource_path.exists() else failure_path
                    if not evidence.is_file() or runner.sha(evidence)!=last['charge']['evidence_identity']:
                        raise PermissionError('UNIVERSE_RESUME_CHARGED_EVIDENCE_CHANGED')
                    if outcome=='COMPLETED':
                        recorded=runner.read_json(root/(prefix+'_STATUS.json'))
                        if (not output.is_file() or recorded.get('state')!='COMPLETED'
                                or recorded.get('dispatch_id')!=last['dispatch']['dispatch_id']
                                or recorded.get('result_sha256')!=runner.sha(output)):
                            raise PermissionError('UNIVERSE_RESUME_COMPLETION_NOT_PROVEN')
                    for suffix in ('_WORKER.json','_INPUT_ACCESS.json'):
                        source=root/(prefix+suffix)
                        if source.exists():runner.save(root/(name+suffix),runner.read_json(source))
                    summary_path=root/(name+'_RESOURCE.json')
                    if not summary_path.exists():
                        resource=runner.read_json(resource_path) if resource_path.exists() else {}
                        runner.save(summary_path,{**resource,'elapsed_wall_seconds':state['charged_seconds'],
                            'segment_count':len(state['segments']),'active_metering':True,
                            'segments':[row['charge']['charge_id'] for row in state['segments']]})
                    elif runner.read_json(summary_path).get('elapsed_wall_seconds')!=state['charged_seconds']:
                        raise PermissionError('UNIVERSE_RESUME_CUMULATIVE_RESOURCE_CONFLICT')
                    gov.settle(name,completed=outcome=='COMPLETED',seconds=state['charged_seconds'],
                        result_hash=runner.sha(output) if output.exists() else None,
                        error=None if outcome=='COMPLETED' else 'KNOWN_WORKER_FAILURE')
                continue
            number=pending['segment_number'];prefix=runner.segment_prefix(name,number)
            worker_path=root/(prefix+'_WORKER.json');access_path=root/(prefix+'_INPUT_ACCESS.json')
            if not worker_path.exists():
                proof_path=root/(prefix+'_MIRROR_REPAIR.json')
                proof=runner.read_json(proof_path) if proof_path.exists() else {}
                if proof.get('worker_absent') is True and proof.get('dispatch_id')==pending['dispatch_id']:
                    gov.end_segment(name,number,outcome='CONTINUE')
                    runner.save(root/(prefix+'_RESUME.json'),{'dispatch_id':pending['dispatch_id'],
                        'charge_basis':'UNKNOWN_UPPER_BOUND','mirror_repair_sha256':runner.sha(proof_path),
                        'budget_reused':True,'execution_not_launched':True})
                    continue
                raise PermissionError('UNIVERSE_RESUME_WORKER_START_UNKNOWN')
            worker=runner.read_json(worker_path)
            readers=[worker['pid']]
            if access_path.exists():readers.append(runner.read_json(access_path)['reader_pid'])
            if any(DaemonInstanceLockV1._pid_alive(int(pid)) for pid in readers):
                raise PermissionError('UNIVERSE_RESUME_WORKER_STILL_ACTIVE')
            if worker.get('dispatch_id')!=pending['dispatch_id']:
                raise PermissionError('UNIVERSE_RESUME_WORKER_DISPATCH_CONFLICT')
            failure_path=root/(prefix+'_FAILURE.json')
            known_failure=False
            if failure_path.exists():
                failure=runner.read_json(failure_path)
                if failure.get('dispatch_id')!=pending['dispatch_id'] or failure.get('scope_identity')!=runner.sha(path):
                    raise PermissionError('UNIVERSE_RESUME_FAILURE_DISPATCH_CONFLICT')
                known_failure=True
            checkpoint, feature_binding = checkpoints[name], feature_bindings[name]
            if checkpoint.exists():
                value=runner.read_json(checkpoint)
                from .universe_execution_state_v2 import VERSION
                if value.get('version') != VERSION or value.get('state_identity') != stable_hash(
                        {k:v for k,v in value.items() if k!='state_identity'}):
                    raise PermissionError('UNIVERSE_RESUME_FULL_STATE_CHANGED')
            elif not feature_binding.is_file() and not known_failure:
                raise PermissionError('UNIVERSE_RESUME_NO_COMMITTED_PROGRESS')
            resource_path=root/(prefix+'_RESOURCE.json');status_path=root/(prefix+'_STATUS.json')
            resource=runner.read_json(resource_path) if resource_path.exists() else None
            status=runner.read_json(status_path) if status_path.exists() else {}
            output=root/(name+'_RESULT.json')
            measured=None;evidence=runner.sha(failure_path) if known_failure else None
            outcome='FAILED' if known_failure else 'CONTINUE'
            if resource is not None:
                if resource.get('dispatch_id') != pending['dispatch_id']:
                    raise PermissionError('UNIVERSE_RESUME_RESOURCE_DISPATCH_CONFLICT')
                measured=resource.get('elapsed_wall_seconds');evidence=runner.sha(resource_path)
                if (not known_failure and measured<=pending['upper_bound_seconds']
                        and resource.get('returncode')==0 and not resource.get('timed_out') and output.exists()
                        and status.get('dispatch_id')==pending['dispatch_id']
                        and status.get('state')=='COMPLETED' and status.get('result_sha256')==runner.sha(output)):
                    outcome='COMPLETED'
                elif not (not known_failure and measured<=pending['upper_bound_seconds']
                          and resource.get('returncode')==75 and not resource.get('timed_out')
                          and status.get('dispatch_id')==pending['dispatch_id'] and status.get('state')=='CONTINUE'):
                    outcome='FAILED'
            gov.end_segment(name,number,seconds=measured,evidence_identity=evidence,outcome=outcome)
            runner.save(root/(prefix+'_RESUME.json'),{'dispatch_id':pending['dispatch_id'],
                'state_identity':runner.sha(checkpoint) if checkpoint.exists() else
                    runner.sha(feature_binding) if feature_binding.is_file() else None,
                'charge_basis':'MEASURED' if measured is not None else 'UNKNOWN_UPPER_BOUND',
                'budget_reused':True,'resumed_at':datetime.now(timezone.utc).isoformat()})
            if outcome in ('COMPLETED','FAILED'):
                total=gov.segment_status(name)['charged_seconds']
                for suffix in ('_WORKER.json','_INPUT_ACCESS.json'):
                    source=root/(prefix+suffix)
                    if source.exists():runner.save(root/(name+suffix),runner.read_json(source))
                segments=gov.segment_status(name)['segments']
                summary={**(resource or {}),'elapsed_wall_seconds':total,'resumed':True,'original_budget_reused':True,
                    'segment_count':len(segments),'active_metering':True,
                    'segments':[row['charge']['charge_id'] for row in segments]}
                runner.save(root/(name+'_RESOURCE.json'),summary)
                gov.settle(name,completed=outcome=='COMPLETED',seconds=total,
                    result_hash=runner.sha(output) if output.exists() else None,error=None if outcome=='COMPLETED' else 'KNOWN_WORKER_FAILURE')
                if outcome=='FAILED':
                    raise PermissionError('UNIVERSE_KNOWN_FAILURE_NO_RETRY')
    if reconcile_only:
        return {'status':'RECONCILED','dispatched_segments':0,'items':runner.status(path)['items']}
    return runner.execute_accounts(path,recover=True)
