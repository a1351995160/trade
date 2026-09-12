"""输入通过后的一次固定外窗账户；不自动修复重跑，不按结果改规则。"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from fetch_monthly_window_v1 import ROOT, SOURCE, check, read, save, sha
from run_baostock_account_v1 import PARENT, active
from chanlun_trader.research_factory.monthly_window_v1 import contract
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.monthly_window_governance_v1 import MonthlyWindowGovernanceV1


def code():
    from execute_baostock_account_v1 import code_identity
    paths=[Path(__file__),SOURCE/'scripts/prepare_monthly_window_v1.py',SOURCE/'scripts/continue_monthly_window_v1.ps1']
    paths += [SOURCE/'src/chanlun_trader/research_factory'/n for n in [
        'monthly_window_v1.py','monthly_window_governance_v1.py','technical_train_signals_v1.py','train_search_batch_v1.py']]
    return {**code_identity(),**{str(p.relative_to(SOURCE)):sha(p) for p in paths}}


def frozen():
    grant=check()
    value=read(ROOT/'EXECUTION_FREEZE_V2.json')
    if value['code']!=code() or value['contract']!=contract() or value['release_identity']!=grant['identity']:
        raise PermissionError('WINDOW_EXECUTION_FREEZE_CHANGED')
    return grant


def bounded(stage,execution_id=None):
    from chanlun_trader.synthetic_batch_resources import run_bounded_worker
    grant=frozen()
    started=ROOT/'resources'/f'{stage}.started.json'
    completed=ROOT/'resources'/f'{stage}.completed.json'
    if started.exists() or completed.exists():
        raise PermissionError('EXISTING_EXECUTION_REQUIRES_RECONCILIATION_NO_REPLAY')
    used=sum(read(p)['elapsed_seconds'] for p in (ROOT/'resources').glob('*.completed.json'))
    # 物化属于数据资源，账户另有90分钟；本入口仅一次900秒主执行。
    left=10800-used if stage=='prepare-window' else 5400
    limit=min(900,left,(datetime.fromisoformat(grant['expires_at'])-datetime.now(timezone.utc)).total_seconds())
    if limit<=0:raise PermissionError('WINDOW_RESOURCE_EXHAUSTED')
    args=[sys.executable,str(Path(__file__)),'--worker',stage]
    if execution_id:args+=['--execution-id',execution_id]
    env={**os.environ,'PYTHONPATH':str(SOURCE/'src'),'PYTHONDONTWRITEBYTECODE':'1',
        **{k:'1' for k in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']}}
    context={'stage':stage} if stage=='prepare-window' else {'stage':stage,'execution_id':execution_id}
    then=time.monotonic()
    result=run_bounded_worker(args,root=SOURCE,memory_mib=2048,wall_seconds=limit,environment=env,execution=context,
        on_started=lambda pid:save(started,{'pid':pid,'at':datetime.now(timezone.utc).isoformat()}))
    save(completed,{'elapsed_seconds':time.monotonic()-then,
        **{k:v.decode('utf-8',errors='replace') if isinstance(v,bytes) else v for k,v in result.items()}})
    return result


def ready():
    frozen()
    value=read(ROOT/'READY.json')
    if sha(ROOT/'FEASIBILITY.json')!=value['feasibility_sha256']:
        raise PermissionError('FEASIBILITY_CHANGED')
    return value


def account(execution_id):
    from chanlun_trader.synthetic_batch_resources import worker_resource_handshake
    from chanlun_trader.research_factory.degraded_execution_v2 import _run_account
    from chanlun_trader.research_factory.train_account_runner_v1 import TrainingAccountExecutionFailure
    from prepare_monthly_window_v1 import load_bundle
    if worker_resource_handshake()['execution']!={'stage':'account-main','execution_id':execution_id}:
        raise PermissionError('ACCOUNT_WORKER_CONTEXT_CHANGED')
    frozen()
    service=MonthlyWindowGovernanceV1(PARENT)
    bundle=load_bundle()
    if bundle.input_identity!=ready()['input_identity']:
        raise PermissionError('ACCOUNT_INPUT_IDENTITY_CHANGED')
    context=read(ROOT/'ACCOUNT_EXECUTION.json')
    if context['execution_id']!=execution_id:raise PermissionError('EXECUTION_ID_CHANGED')
    def access():
        check()
        return service.active()
    access()
    save(ROOT/'ACCOUNT_ACCESS.json',{'reader_pid':os.getpid(),'recipient':'REQUESTING_USER',
        'purpose':'FIXED_MONTHLY_WINDOW_ACCOUNT_MAIN','input_identity':bundle.input_identity,
        'contract_hash':stable_hash(contract()),'execution_id':execution_id})
    service.start_exposure(execution_id)
    try:
        result=_run_account(bundle,(context['commit'],context['dirty']),access,contract())
    except TrainingAccountExecutionFailure as exc:
        save(ROOT/'ENGINE_FAILURE.json',json.loads(json.dumps(exc.evidence,default=str)))
        raise
    # 原引擎字段名保留，仅增加窗口口径，避免称作新的训练/正式资格结果。
    result['evaluation_window']=[20250801,20260731]
    result['metric_semantics']='LEGACY_TRAIN_NAMED_FIELDS_REFER_TO_APPROVED_INDEPENDENT_WINDOW'
    save(ROOT/'ACCOUNT_RESULT.json',json.loads(json.dumps(result,default=str,allow_nan=False)))
    metrics=result['metrics']
    save(ROOT/'ACCOUNT_FEEDBACK.json',{'status':result['status'],'result_sha256':sha(ROOT/'ACCOUNT_RESULT.json'),
        'net_positive':bool(metrics and metrics['train_net_return']>0),
        'qualified':False,'design_exact_performance_access':False})
    report=ROOT/'ACCOUNT_REPORT.md'
    with report.open('x',encoding='utf-8') as stream:
        stream.write('# 固定月末反转：独立外窗账户结果\n\n')
        stream.write('窗口：2025-08-01至2026-07-31。原始价格成交，HFQ仅用于信号；不代表正式资格。\n\n')
        stream.write('状态：'+result['status']+'。以下是原账户保存的指标；train命名字段在此仅指获准外窗。\n\n')
        stream.write('```json\n'+json.dumps(metrics,ensure_ascii=False,indent=2)+'\n```\n\n')
        stream.write('完整信号、委托、成交、拒绝、费用、lot、现金及持仓：ACCOUNT_RESULT.json。\n\n')
        stream.write('历史发布时间未知；采用下一开盘模型。公司行动使用调整日期hazard拒绝，缺完整会计条款；存在条件选择偏差。\n\n')
        stream.write('本窗口已经发生结果曝光，不再是未使用的最终测试集。不按本次结果修改规则。\n\n')
        stream.write('READY_FOR_REAL_TRIAL=false；R1_FULLY_CLOSED=false；AUTONOMOUS_STRATEGY_GOAL_COMPLETED=false。\n')
    save(ROOT/'ACCOUNT_REPORT_INDEX.json',{'report_sha256':sha(report),'result_sha256':sha(ROOT/'ACCOUNT_RESULT.json'),
        'reader':'EVALUATION_WORKER','reader_pid':os.getpid(),'recipient':'REQUESTING_USER','purpose':'APPROVED_WINDOW_REPORT'})


def run():
    grant=check()
    if not (ROOT/'WINDOW_UNIVERSE.json').exists():raise PermissionError('ACQUISITION_NOT_COMPLETE')
    count=read(ROOT/'WINDOW_UNIVERSE.json')['count']
    for batch in range((count+49)//50):
        if read(ROOT/'resources'/f'prices-v2-{batch}.completed.json')['returncode']!=0:
            raise PermissionError('PRICE_BATCH_NOT_COMPLETE')
    save(ROOT/'EXECUTION_FREEZE_V2.json',{'code':code(),'contract':contract(),'release_identity':grant['identity']})
    if not (ROOT/'READY.json').exists():
        result=bounded('prepare-window')
        if result['returncode']:raise RuntimeError('WINDOW_PREPARATION_FAILED_SEE_RECEIPT')
    value=ready()
    if not value['feasibility_passed']:
        print({'account_started':False,'reason':'FEASIBILITY_NOT_PASSED'})
        return
    parent,expiry=active()
    plan={'contracts':{stable_hash(contract()):contract()},'limit':2,'wall_limit':1800,
        'result_type':contract()['result_type'],'input_identity':value['input_identity'],
        'objective_id':parent['plan']['objective_id'],'expires_at':expiry.isoformat()}
    source={**read(ROOT/'WINDOW_GATE_APPROVAL.json'),
        'approval_record_sha256':sha(ROOT/'WINDOW_GATE_APPROVAL.json'),
        'window_release_sha256':sha(ROOT/'WINDOW_RELEASE.json'),
        'window_gate_approval_sha256':sha(ROOT/'WINDOW_GATE_APPROVAL.json')}
    service=MonthlyWindowGovernanceV1(PARENT)
    service.confirm(plan,source,preflight=ready)
    reservation=service.reserve(stable_hash(contract()))
    if reservation['status']!='RESERVED':raise PermissionError('EXISTING_ACCOUNT_ATTEMPT_NO_REPLAY')
    eid=reservation['execution_id']
    save(ROOT/'ACCOUNT_EXECUTION.json',{'execution_id':eid,
        'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=SOURCE,text=True).strip(),
        'dirty':bool(subprocess.check_output(['git','status','--porcelain'],cwd=SOURCE,text=True).strip())})
    then=time.monotonic();completed=False
    try:
        result=bounded('account-main',eid)
        completed=result['returncode']==0 and (ROOT/'ACCOUNT_FEEDBACK.json').exists()
    finally:service.settle(eid,time.monotonic()-then,completed)
    save(ROOT/'ACCOUNT_SETTLEMENT.json',{'completed':completed,**service.summary()})
    print({'account_completed':completed,'receipt':str(ROOT/'ACCOUNT_SETTLEMENT.json')})


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--worker',choices=['prepare-window','account-main'])
    parser.add_argument('--execution-id')
    args=parser.parse_args()
    if args.worker=='prepare-window':
        frozen()
        from prepare_monthly_window_v1 import prepare
        prepare()
    elif args.worker:account(args.execution_id)
    else:run()
