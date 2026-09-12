"""输入与无收益门槛通过后，使用原权威组件执行一次固定账户。"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from run_baostock_account_v1 import ROOT, BASE, SOURCE, PARENT, active, read, sha, save


def code_identity():
    files = [*sorted((SOURCE/'src/chanlun_trader/engine').glob('*.py'))]
    files += [SOURCE/'src/chanlun_trader/research_factory'/n for n in [
        'baostock_input_v1.py','baostock_price_views_v1.py','baostock_account_v1.py',
        'baostock_governance_v1.py','degraded_input_v2.py','degraded_execution_v2.py','fixed_account_rules.py',
        'degraded_governance_v1.py','train_execution_governance_v1.py','exploration_governance.py',
        'budget.py','novelty.py','degraded_train_v1.py','train_account_runner_v1.py']]
    files += [SOURCE/'scripts'/n for n in ['run_baostock_account_v1.py',
        'prepare_baostock_account_v1.py','execute_baostock_account_v1.py','baostock_alias_v1.py']]
    files += [SOURCE/'src/chanlun_trader/synthetic_batch_resources.py']
    return {str(p.relative_to(SOURCE)):sha(p) for p in files}


def resource_used():
    # 仅本轮自己写出的资源回执，不扫描历史报告或数值结果。
    return sum(read(p).get('elapsed_seconds',0) for p in (ROOT/'resources').glob('*.json'))


def total_resource_limit():
    from run_baostock_account_v1 import resume_revision
    return resume_revision()['total_seconds'] if (ROOT/'ACQUISITION_RESUME_V1.json').exists() else 5400


def bounded(script, label, execution, arguments=(), maximum=900):
    from datetime import datetime, timezone
    from chanlun_trader.synthetic_batch_resources import run_bounded_worker
    _,expiry = active()
    limit = min(maximum,total_resource_limit()-resource_used(),(expiry-datetime.now(timezone.utc)).total_seconds())
    if limit <= 0:
        raise PermissionError('TOTAL_PLAN_RESOURCE_LIMIT_REACHED')
    env = {**os.environ,'PYTHONPATH':str(SOURCE/'src'),'PYTHONIOENCODING':'utf-8',
        **{k:'1' for k in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']}}
    started = time.monotonic()
    result = run_bounded_worker([sys.executable,str(SOURCE/'scripts'/script),*arguments],root=SOURCE,
        memory_mib=2048,wall_seconds=limit,environment=env,execution=execution,
        on_started=lambda pid:save(ROOT/'resources'/(label+'.started.json'),
            {'pid':pid,'wall_seconds':limit,'memory_mib':2048,'numeric_threads':1}))
    elapsed = time.monotonic()-started
    save(ROOT/'resources'/(label+'.json'),{'elapsed_seconds':elapsed,
        **{k:v.decode('utf-8',errors='replace') if isinstance(v,bytes) else v for k,v in result.items()}})
    return result,elapsed


def load_ready():
    ready = read(ROOT/'INPUT_READY.json')
    if ready['feasibility_sha256'] != sha(ROOT/'FEASIBILITY.json'):
        raise PermissionError('FEASIBILITY_IDENTITY_CHANGED')
    if read(ROOT/'CODE_FREEZE.json')['code'] != code_identity():
        raise PermissionError('EXECUTION_CODE_CHANGED')
    return ready


def worker(execution_id):
    from chanlun_trader.synthetic_batch_resources import worker_resource_handshake
    resource = worker_resource_handshake()
    from chanlun_trader.research_factory.baostock_governance_v1 import BaostockGovernanceV1
    from chanlun_trader.research_factory.baostock_account_v1 import run_baostock_account
    from chanlun_trader.research_factory.train_account_runner_v1 import TrainingAccountExecutionFailure
    from prepare_baostock_account_v1 import load_bundle
    service = BaostockGovernanceV1(PARENT)
    receipt = service.active()
    ready = load_ready()
    bundle = load_bundle()
    context = read(ROOT/'execution'/execution_id/'source_identity.json')
    if resource['execution'] != {'execution_id':execution_id,'plan_id':receipt['plan_id'],'source_identity':context}:
        raise PermissionError('RESOURCE_CONTEXT_CHANGED')
    if bundle.input_identity != ready['input_identity']:
        raise PermissionError('INPUT_IDENTITY_CHANGED')
    save(ROOT/'execution'/execution_id/'access.json', {'purpose':'BAOSTOCK_ACCOUNT_MAIN',
        'reader_pid':os.getpid(),'recipient':'REQUESTING_USER','input_identity':bundle.input_identity})
    service.start_exposure(execution_id)
    try:
        result = run_baostock_account(bundle,(context['code_commit'],context['dirty_worktree']),service.active)
    except TrainingAccountExecutionFailure as exc:
        save(ROOT/'execution'/execution_id/'failed_account.json',json.loads(json.dumps(exc.evidence,default=str)))
        raise
    path = ROOT/'execution'/execution_id/'result.json'
    save(path,json.loads(json.dumps(result,default=str,allow_nan=False)))
    save(path.with_name('completed.json'),{'status':result['status'],'result_sha256':sha(path)})


def execute():
    from chanlun_trader.research_factory.baostock_account_v1 import CONTRACT
    from chanlun_trader.research_factory.baostock_governance_v1 import BaostockGovernanceV1
    from chanlun_trader.research_factory.common import stable_hash
    from run_baostock_account_v1 import acquire
    approved = read(BASE/'baostock-account-preparation-v2/CONFIRMATION_PACKAGE.json')
    if approved['contract'] != CONTRACT:
        raise PermissionError('APPROVED_FIXED_CONTRACT_CHANGED')
    if (ROOT/'ACQUISITION_RESUME_V1.json').exists():
        from run_baostock_account_v1 import resume_acquire
        resume_acquire()
    else:
        acquire()
    parent,expiry = active()
    save(ROOT/'CODE_FREEZE.json',{'code':code_identity(),'contract':CONTRACT})
    if not (ROOT/'INPUT_READY.json').exists():
        result,_ = bounded('prepare_baostock_account_v1.py','prepare',
            {'purpose':'FIXED_SIGNAL_NO_OUTCOME_FEASIBILITY','approval_sha256':sha(ROOT/'APPROVAL_FACT.json')})
        if result['returncode']:
            raise RuntimeError('INPUT_PREPARATION_FAILED_EVIDENCE_PRESERVED')
    ready = load_ready()
    if not ready['feasibility_passed']:
        raise PermissionError('FEASIBILITY_NOT_PASSED_NO_ACCOUNT_EXPOSURE')
    # 没有足够的剩余资源时，不创建一个无法启动的执行预留。
    if resource_used() >= total_resource_limit():
        raise PermissionError('TOTAL_PLAN_RESOURCE_LIMIT_REACHED')
    plan = {'contracts':{stable_hash(CONTRACT):CONTRACT},'limit':2,'wall_limit':1800,
        'result_type':CONTRACT['result_type'],'input_identity':ready['input_identity'],
        'objective_id':parent['plan']['objective_id'],'expires_at':expiry.isoformat()}
    source = {**read(ROOT/'APPROVAL_FACT.json'),'approval_record_sha256':sha(ROOT/'APPROVAL_FACT.json')}
    service = BaostockGovernanceV1(PARENT)
    receipt = service.confirm(plan,source,preflight=load_ready)
    reservation = service.reserve(stable_hash(CONTRACT))
    if reservation['status'] != 'RESERVED':
        raise PermissionError('EXISTING_ACCOUNT_ATTEMPT_RECONCILE_NO_REPLAY')
    eid = reservation['execution_id']
    context = {'code_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=SOURCE,text=True).strip(),
        'dirty_worktree':bool(subprocess.check_output(['git','status','--porcelain'],cwd=SOURCE,text=True).strip())}
    save(ROOT/'execution'/eid/'source_identity.json',context)
    started = time.monotonic()
    complete = False
    try:
        result,_ = bounded('execute_baostock_account_v1.py','account-main',
            {'execution_id':eid,'plan_id':receipt['plan_id'],'source_identity':context},
            ['--worker',eid],reservation['wall_seconds'])
        complete = result['returncode'] == 0 and (ROOT/'execution'/eid/'completed.json').exists()
    finally:
        service.settle(eid,time.monotonic()-started,complete)
    save(ROOT/'EXECUTION_SUMMARY.json',{'execution_id':eid,'completed':complete,**service.summary()})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker')
    args = parser.parse_args()
    worker(args.worker) if args.worker else execute()
