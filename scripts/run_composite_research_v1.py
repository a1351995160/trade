"""第16批组合机制：原研究入口执行后自动盲化交付和既有结果检查。"""
from datetime import datetime,timezone
import os
import subprocess
import sys
import time
import argparse

from run_baostock_account_v1 import ROOT as INPUT,SOURCE,active,read,save

ROOT=INPUT.parent/'train-search-batch-v16'
BATCH=16


def artifact(arguments,label):
    from chanlun_trader.synthetic_batch_resources import run_bounded_worker
    import run_train_search_batch_v1 as batch
    batch.BATCH=BATCH;batch.ROOT=ROOT
    batch.guard()
    _,expiry=active()
    used=sum(read(p)['elapsed_seconds'] for p in (ROOT/'resources').glob('*.completed.json'))+read(ROOT/'PREREGISTRATION.json').get('historical_worker_seconds',0)
    seconds=min(900,5400-used,(expiry-datetime.now(timezone.utc)).total_seconds())
    if seconds<=0:raise PermissionError('COMPOSITE_RESOURCE_EXHAUSTED')
    env={**os.environ,'PYTHONPATH':str(SOURCE/'src'),'PYTHONDONTWRITEBYTECODE':'1',
        **{k:'1' for k in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']}}
    started=time.monotonic()
    result=run_bounded_worker([sys.executable,*arguments],root=SOURCE,memory_mib=2048,
        wall_seconds=seconds,environment=env,execution={'artifact':label},
        on_started=lambda pid:save(ROOT/'resources'/f'{label}.started.json',{'pid':pid}))
    save(ROOT/'resources'/f'{label}.completed.json',{'elapsed_seconds':time.monotonic()-started,
        **{k:v.decode('utf-8',errors='replace') if isinstance(v,bytes) else v for k,v in result.items()}})
    if result['returncode']:raise RuntimeError(f'COMPOSITE_ARTIFACT_FAILED:{label}')


def run():
    active()
    prior_path=(INPUT.parent/'train-search-sequence-v2/COMPLETED.json' if BATCH==16 else
                INPUT.parent/f'train-search-batch-v{BATCH-1}/RUNNER_COMPLETED.json')
    expected=('FIXED_QUEUE_EXHAUSTED_NO_ROBUST_CANDIDATE' if BATCH==16 else 'COMPOSITE_BATCH_COMPLETED_NO_SCREEN_PASS')
    prior=read(prior_path)
    if BATCH==43:
        import run_train_search_batch_v1 as batch
        batch.BATCH=BATCH
        batch.reconcile_stock_trend_failure()
    elif BATCH==42:
        import run_train_search_batch_v1 as batch
        batch.BATCH=BATCH
        batch.reconcile_breadth_cost_limit()
    elif BATCH==41:
        import run_train_search_batch_v1 as batch
        batch.BATCH=BATCH
        batch.reconcile_weak_low_vol_window()
    elif BATCH==40:
        import run_train_search_batch_v1 as batch
        batch.BATCH=BATCH
        batch.reconcile_batch39_recovery()
    elif BATCH==39:
        import run_train_search_batch_v1 as batch
        batch.BATCH=BATCH
        batch.reconcile_scale_failure()
    elif BATCH==36:
        import run_train_search_batch_v1 as batch
        batch.BATCH=BATCH
        batch.reconcile_turnover_feasibility()
    elif BATCH==32:
        import run_train_search_batch_v1 as batch
        batch.BATCH=BATCH
        batch.reconcile_confirmation_window_failures()
    elif BATCH==26:
        import run_train_search_batch_v1 as batch
        batch.BATCH=BATCH
        batch.reconcile_weak_train_followup()
    elif BATCH==25:
        import run_train_search_batch_v1 as batch
        batch.BATCH=BATCH
        batch.reconcile_external_followup()
    elif prior['status']!=expected:
        raise PermissionError('PREVIOUS_SEQUENCE_REQUIRES_RECONCILIATION')
    if (ROOT/'RUNNER_STARTED.json').exists():raise PermissionError('NO_AUTOMATIC_COMPOSITE_REPLAY')
    save(ROOT/'RUNNER_STARTED.json',{'pid':os.getpid(),'at':datetime.now(timezone.utc).isoformat()})
    status='ENGINEERING_FAILURE_NEEDS_TRIAGE'
    try:
        with (ROOT/'batch.stdout.log').open('xb') as out,(ROOT/'batch.stderr.log').open('xb') as err:
            result=subprocess.run([sys.executable,'scripts/run_train_search_batch_v1.py','--batch',str(BATCH)],
                cwd=SOURCE,stdout=out,stderr=err,check=False)
        save(ROOT/'BATCH_EXIT.json',{'returncode':result.returncode,'at':datetime.now(timezone.utc).isoformat()})
        if result.returncode:raise RuntimeError('COMPOSITE_BATCH_FAILED')
        artifact(['scripts/report_train_search_batch_v8.py','--batch',str(BATCH)],'delivery')
        if BATCH>=17:
            artifact(['scripts/review_failed_composites_v1.py','--batch',str(BATCH)],'failure-learning')
        delivery=read(ROOT/'DELIVERY_STATUS.json')
        status='COMPOSITE_BATCH_COMPLETED_NO_SCREEN_PASS'
        for row in delivery['candidates']:
            if any(r['returncode'] for r in row['resources']):
                status='ENGINEERING_FAILURE_NEEDS_TRIAGE';break
            if row['screen_passed'] is True:
                name=row['candidate']
                artifact(['scripts/review_followup_robustness_v1.py','--batch',str(BATCH),'--name',name],f'review-{name}')
                status='SCREEN_PASS_REVIEWED_NOT_QUALIFIED'
    except Exception:
        status='ENGINEERING_FAILURE_NEEDS_TRIAGE'
        raise
    finally:
        save(ROOT/'RUNNER_COMPLETED.json',{'status':status,'at':datetime.now(timezone.utc).isoformat(),
            'READY_FOR_REAL_TRIAL':False,'R1_FULLY_CLOSED':False,'AUTONOMOUS_STRATEGY_GOAL_COMPLETED':False})


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--batch',type=int,choices=[16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43],default=16)
    BATCH=parser.parse_args().batch
    ROOT=INPUT.parent/f'train-search-batch-v{BATCH}'
    from batch39_input_recovery_v1 import resolve
    ROOT=resolve(ROOT)
    run()
