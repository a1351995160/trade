"""固定八候选顺序研究队列；原批次、预算、资源、账户与盲化服务继续执法。"""
from datetime import datetime,timezone
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

from run_baostock_account_v1 import ROOT as INPUT,SOURCE,active,read,save,sha

ROOT=INPUT.parent/'train-search-sequence-v2'
BATCHES={
    12:['SMA_50_200_CROSS_HOLD_20','TRIX_15_ZERO_CROSS_HOLD_20'],
    13:['HAMMER_DOWN_5_HOLD_20','THREE_SOLDIERS_HOLD_20'],
    14:['ATR_14_UP_BREAKOUT_HOLD_20','INSIDE_BAR_UP_HOLD_20'],
    15:['SMA20_PULLBACK_200_HOLD_20','ROC20_ACCEL_HOLD_20'],
}


def validate(plan,parent,expiry):
    from chanlun_trader.research_factory.train_search_batch_v1 import contract
    if (plan['batches']!={str(k):v for k,v in BATCHES.items()} or plan['compute_seconds']!=5400
            or plan['parent_receipt_id']!=parent['receipt_id'] or plan['expires_at']!=expiry.isoformat()
            or plan['contracts']!={name:contract(name) for names in BATCHES.values() for name in names}):
        raise PermissionError('SEQUENCE_SCOPE_CHANGED')
    if ((ROOT/'revocation.json').exists()
            or (INPUT.parent/'train-search-sequence-v1/revocation.json').exists()
            or datetime.now(timezone.utc)>=expiry):
        raise PermissionError('SEQUENCE_REVOKED_OR_EXPIRED')
    for path,digest in plan['code'].items():
        if sha(path)!=digest:raise PermissionError('SEQUENCE_SOURCE_CHANGED')
    for path,digest in plan.get('history',{}).items():
        if sha(path)!=digest:raise PermissionError('SEQUENCE_HISTORY_CHANGED')


def authorize(batch,names):
    parent,expiry=active()
    plan=read(ROOT/'PLAN.json')
    validate(plan,parent,expiry)
    if BATCHES.get(batch)!=names:raise PermissionError('SEQUENCE_BATCH_CHANGED')


def remaining():
    authorize(12,BATCHES[12])
    used=0.
    resource_roots=[INPUT.parent/f'train-search-batch-v{batch}/resources' for batch in BATCHES]
    resource_roots.append(ROOT/'resources')
    resource_roots.append(INPUT.parent/'train-search-sequence-v1/resources')
    for directory in resource_roots:
        for path in directory.glob('*.completed.json'):
            seconds=read(path)['elapsed_seconds']
            if not isinstance(seconds,(int,float)) or not math.isfinite(seconds) or seconds<0:
                raise PermissionError('INVALID_RESOURCE_RECEIPT')
            used+=seconds
    return max(0.,5400-used)


def invoke(arguments,label):
    if label.startswith(('report-','review-')):
        from chanlun_trader.synthetic_batch_resources import run_bounded_worker
        _,expiry=active()
        seconds=min(900,remaining(),(expiry-datetime.now(timezone.utc)).total_seconds())
        if seconds<=0:raise PermissionError('SEQUENCE_RESOURCE_EXHAUSTED')
        env={**os.environ,'PYTHONPATH':str(SOURCE/'src'),'PYTHONDONTWRITEBYTECODE':'1',
             **{k:'1' for k in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']}}
        start=time.monotonic()
        result=run_bounded_worker([sys.executable,*arguments],root=SOURCE,memory_mib=2048,
            wall_seconds=seconds,environment=env,execution={'sequence_artifact':label},
            on_started=lambda pid:save(ROOT/'resources'/f'{label}.started.json',{'pid':pid}))
        save(ROOT/'resources'/f'{label}.completed.json',{'elapsed_seconds':time.monotonic()-start,
            **{k:v.decode('utf-8',errors='replace') if isinstance(v,bytes) else v for k,v in result.items()}})
        if result['returncode']:raise RuntimeError(f'SEQUENCE_ARTIFACT_FAILED:{label}')
        return
    with (ROOT/f'{label}.stdout.log').open('xb') as out, (ROOT/f'{label}.stderr.log').open('xb') as err:
        completed=subprocess.run([sys.executable,*arguments],cwd=SOURCE,stdout=out,stderr=err,check=False)
    save(ROOT/f'{label}.completed.json',{'returncode':completed.returncode,
        'finished_at':datetime.now(timezone.utc).isoformat()})
    if completed.returncode:raise RuntimeError(f'SEQUENCE_STAGE_FAILED:{label}')


def run():
    from chanlun_trader.research_factory.train_search_batch_v1 import contract
    from execute_baostock_account_v1 import code_identity
    parent,expiry=active()
    if (ROOT/'PLAN.json').exists():raise PermissionError('SEQUENCE_EXISTS_RECONCILE_NO_AUTOMATIC_REPLAY')
    for batch in BATCHES:
        if (INPUT.parent/f'train-search-batch-v{batch}/PREREGISTRATION.json').exists():
            raise PermissionError('SEQUENCE_CANDIDATE_ALREADY_FROZEN')
    paths=[SOURCE/'scripts'/name for name in ['run_train_search_batch_v1.py',
        'run_train_search_sequence_v1.py','report_train_search_batch_v8.py','review_followup_robustness_v1.py',
        'review_monthly_robustness_v1.py']]
    paths += [SOURCE/'src/chanlun_trader/research_factory'/name for name in [
        'technical_smoothing_signals_v1.py','technical_train_signals_v1.py',
        'technical_followup_signals_v1.py','technical_pattern_signals_v1.py','fixed_account_rules.py']]
    paths += [SOURCE/'docs/TRAIN_SEARCH_SEQUENCE_V1.md',SOURCE/'docs/TRAIN_SEARCH_BATCH_V12.md']
    history={str(INPUT.parent/f'train-search-batch-v{b}/PREREGISTRATION.json'):
             sha(INPUT.parent/f'train-search-batch-v{b}/PREREGISTRATION.json') for b in range(1,12)}
    old_root=INPUT.parent/'train-search-sequence-v1'
    old_plan=read(old_root/'PLAN.json')
    if (read(old_root/'COMPLETED.json')['status']!='ENGINEERING_FAILURE_NEEDS_TRIAGE'
            or read(old_root/'batch-12.completed.json')['returncode']!=1
            or old_plan['contracts']!={name:contract(name) for names in BATCHES.values() for name in names}
            or old_plan['parent_receipt_id']!=parent['receipt_id']
            or old_plan['expires_at']!=expiry.isoformat()):
        raise PermissionError('SEQUENCE_RECOVERY_IDENTITY_CONFLICT')
    for relative in ['PLAN.json','STARTED.json','COMPLETED.json','batch-12.completed.json','batch-12.stderr.log']:
        path=old_root/relative
        history[str(path)]=sha(path)
    approval=INPUT.parent/'train-search-batch-v12/APPROVAL_SOURCE.json'
    history[str(approval)]=sha(approval)
    save(ROOT/'PLAN.json',{'approval_statement':'那继续找吧，找到能用的为止',
        'authorization_origin':'USER_CONTINUING_RESEARCH_DELEGATION_NOT_PER_CANDIDATE_APPROVAL',
        'reader':os.environ['CODEX_THREAD_ID'],'parent_receipt_id':parent['receipt_id'],
        'objective_id':parent['plan']['objective_id'],'expires_at':expiry.isoformat(),
        'batches':{str(k):v for k,v in BATCHES.items()},'compute_seconds':5400,
        'contracts':{name:contract(name) for names in BATCHES.values() for name in names},
        'code':{**{str(SOURCE/p):digest for p,digest in code_identity().items()},
                **{str(p):sha(p) for p in paths}},'history':history,
        'old_consumption_unchanged':True,'main_slots_max':8,'repair_slots_restricted_max':8,
        'recovery':{'prior_root':str(old_root),'reason':'EXTERNAL_PLAN_RELATIVE_PATH_ERROR_BEFORE_PREREGISTRATION',
                    'formulas_and_budget_unchanged':True,'prior_resource_receipts_counted':True},
        'created_at':datetime.now(timezone.utc).isoformat()})
    save(ROOT/'STARTED.json',{'pid':os.getpid(),'at':datetime.now(timezone.utc).isoformat()})
    status='ENGINEERING_FAILURE_NEEDS_TRIAGE';promising=[]
    try:
        for batch,names in BATCHES.items():
            authorize(batch,names)
            if remaining()<=0:
                status='COMPUTE_LIMIT_REACHED';break
            save(ROOT/f'BATCH_{batch}_STARTED.json',{'batch':batch,'at':datetime.now(timezone.utc).isoformat()})
            invoke(['scripts/run_train_search_batch_v1.py','--batch',str(batch)],f'batch-{batch}')
            invoke(['scripts/report_train_search_batch_v8.py','--batch',str(batch)],f'report-{batch}')
            delivery=read(INPUT.parent/f'train-search-batch-v{batch}/DELIVERY_STATUS.json')
            if any(any(r['returncode'] for r in c['resources']) for c in delivery['candidates']):
                status='ENGINEERING_FAILURE_NEEDS_TRIAGE';break
            for candidate in delivery['candidates']:
                if candidate['screen_passed'] is True:
                    name=candidate['candidate']
                    invoke(['scripts/review_followup_robustness_v1.py','--batch',str(batch),'--name',name],f'review-{name}')
                    feedback=read(INPUT.parent/f'train-search-batch-v{batch}'/name/'robustness-review-v1/DESIGN_FEEDBACK.json')
                    if not feedback['flags']:promising.append({'batch':batch,'candidate':name})
            if promising:
                status='PROMISING_REQUIRES_INDEPENDENT_PLAN';break
        else:status='FIXED_QUEUE_EXHAUSTED_NO_ROBUST_CANDIDATE'
    except PermissionError:
        status='AUTHORIZATION_OR_RESOURCE_STOP'
        raise
    finally:
        save(ROOT/'COMPLETED.json',{'status':status,'promising':promising,
            'finished_at':datetime.now(timezone.utc).isoformat(),
            'READY_FOR_REAL_TRIAL':False,'R1_FULLY_CLOSED':False,'AUTONOMOUS_STRATEGY_GOAL_COMPLETED':False})
    print(json.dumps({'status':status,'promising':promising},ensure_ascii=False))


if __name__=='__main__':run()
