"""补数完成后按冻结合同继续：原可行性、原增量预算、一次原账户主执行。"""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from acquire_weekly_window_v1 import ROOT, SOURCE, check, read, save, sha, PLAN_HASH
from run_baostock_account_v1 import PARENT,active
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.weekly_window_v1 import contract
from chanlun_trader.research_factory.weekly_window_governance_v1 import WeeklyWindowGovernanceV1


def code():
    from execute_baostock_account_v1 import code_identity
    names=['weekly_window_v1.py','weekly_window_governance_v1.py','monthly_window_v1.py',
        'technical_train_signals_v1.py','train_search_batch_v1.py','weekly_defensive_signals_v1.py',
        'weekly_fixed_trial_v1.py','technical_composite_signals_v1.py','technical_followup_signals_v1.py',
        'technical_pattern_signals_v1.py','technical_smoothing_signals_v1.py','structured_exit_trial_v1.py',
        'price_volume_patterns_v1.py','weekly_alias_v1.py','weekly_parquet_v1.py']
    paths=[SOURCE/'src/chanlun_trader/research_factory'/n for n in names]
    paths += [Path(__file__),SOURCE/'scripts/prepare_weekly_window_v1.py',SOURCE/'scripts/prepare_monthly_window_v1.py',
        SOURCE/'scripts/review_monthly_robustness_v1.py',SOURCE/'scripts/weekly_alias_recovery_v1.py',SOURCE/'scripts/weekly_memory_recovery_v1.py']
    return {**code_identity(),**{str(p.relative_to(SOURCE)):sha(p) for p in paths}}


def freeze():
    grant=check()
    proposal=read(ROOT/'PLAN_PROPOSAL_V2.json')
    if contract()['source_candidate_contract_hash']!=proposal['source_candidate_contract_hash']:
        raise PermissionError('FIXED_SOURCE_CONTRACT_CHANGED')
    from review_monthly_robustness_v1 import RULE
    save(ROOT/'RESULT_REVIEW_RULE.json',{**RULE,'type':'PREDECLARED_EXTERNAL_WINDOW_DESCRIPTIVE_REVIEW',
        'evaluation_window':[20250801,20260731],
        'independence':'FIXED_RULE_NEW_PERIOD; OTHER_CANDIDATE_INPUT_EXPOSURE_RETAINED; NOT_FORMAL_INDEPENDENCE'})
    from weekly_alias_recovery_v1 import recovery
    alias=recovery()
    from weekly_memory_recovery_v1 import memory_recovery
    memory=memory_recovery()
    retry=(ROOT/'MEMORY_HANDSHAKE_RECOVERY_V2.json').exists()
    save(ROOT/('EXECUTION_FREEZE_MEMORY_V2.json' if retry else 'EXECUTION_FREEZE_MEMORY_V1.json' if memory else 'EXECUTION_FREEZE_ALIAS_V1.json' if alias else 'EXECUTION_FREEZE.json'),{'code':code(),'contract':contract(),'release_identity':grant['identity'],
        **({'memory_handshake_sha256':sha(ROOT/'MEMORY_HANDSHAKE_RECOVERY_V2.json')} if retry else {}),
        **({'memory_recovery_sha256':sha(ROOT/'MEMORY_RECOVERY_V1.json')} if memory else {}),
        **({'alias_recovery_sha256':sha(ROOT/'ALIAS_RECOVERY_V1.json')} if alias else {}),
        'review_rule_sha256':sha(ROOT/'RESULT_REVIEW_RULE.json')})


def frozen():
    from weekly_alias_recovery_v1 import recovery
    alias=recovery()
    from weekly_memory_recovery_v1 import memory_recovery
    memory=memory_recovery()
    retry=(ROOT/'MEMORY_HANDSHAKE_RECOVERY_V2.json').exists()
    grant=check(); value=read(ROOT/('EXECUTION_FREEZE_MEMORY_V2.json' if retry else 'EXECUTION_FREEZE_MEMORY_V1.json' if memory else 'EXECUTION_FREEZE_ALIAS_V1.json' if alias else 'EXECUTION_FREEZE.json'))
    if value!={'code':code(),'contract':contract(),'release_identity':grant['identity'],
              'review_rule_sha256':sha(ROOT/'RESULT_REVIEW_RULE.json'),
              **({'memory_handshake_sha256':sha(ROOT/'MEMORY_HANDSHAKE_RECOVERY_V2.json')} if retry else {}),
              **({'memory_recovery_sha256':sha(ROOT/'MEMORY_RECOVERY_V1.json')} if memory else {}),
              **({'alias_recovery_sha256':sha(ROOT/'ALIAS_RECOVERY_V1.json')} if alias else {})}:
        raise PermissionError('WEEKLY_EXECUTION_FREEZE_CHANGED')
    return grant


def ready():
    frozen(); value=read(ROOT/'READY.json')
    if sha(ROOT/'FEASIBILITY.json')!=value['feasibility_sha256']:raise PermissionError('FEASIBILITY_CHANGED')
    return value


def bounded(stage,eid=None):
    from chanlun_trader.synthetic_batch_resources import run_bounded_worker
    grant=frozen(); directory=ROOT/'resources'
    label=stage+'-alias-v1' if stage=='prepare-window' and (ROOT/'ALIAS_RECOVERY_V1.json').exists() else stage
    if stage=='prepare-window' and (ROOT/'MEMORY_RECOVERY_V1.json').exists():label=stage+'-memory-v1'
    if stage=='prepare-window' and (ROOT/'MEMORY_HANDSHAKE_RECOVERY_V2.json').exists():label=stage+'-memory-v2'
    started=directory/f'{label}.started.json'; completed=directory/f'{label}.completed.json'
    if started.exists() or completed.exists():raise PermissionError('EXISTING_EXECUTION_NO_REPLAY')
    for p in directory.glob('*.started.json'):
        if not p.with_name(p.name.replace('.started.json','.completed.json')).exists():raise PermissionError('UNSETTLED_WORKER')
    used=sum(read(p)['elapsed_seconds'] for p in directory.glob('*.completed.json'))
    limit=min(900,21600-used if stage=='prepare-window' else 5400,
        (datetime.fromisoformat(grant['expires_at'])-datetime.now(timezone.utc)).total_seconds())
    if limit<=0:raise PermissionError('WINDOW_RESOURCE_EXHAUSTED')
    ctx={'stage':stage} if eid is None else {'stage':stage,'execution_id':eid}
    args=[sys.executable,str(Path(__file__)),'--worker',stage]
    if eid:args+=['--execution-id',eid]
    env={**os.environ,'PYTHONPATH':str(SOURCE/'src'),'PYTHONDONTWRITEBYTECODE':'1','PYTHONUNBUFFERED':'1',
        **{k:'1' for k in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']}}
    then=time.monotonic()
    result=run_bounded_worker(args,root=SOURCE,memory_mib=2048,wall_seconds=limit,environment=env,execution=ctx,
        on_started=lambda pid:save(started,{'pid':pid,'at':datetime.now(timezone.utc).isoformat()}))
    save(completed,{'elapsed_seconds':time.monotonic()-then,
        **{k:v.decode('utf-8',errors='replace') if isinstance(v,bytes) else v for k,v in result.items()}})
    return result


def account(eid):
    from chanlun_trader.synthetic_batch_resources import worker_resource_handshake
    from chanlun_trader.research_factory.degraded_execution_v2 import _run_account
    from chanlun_trader.research_factory.train_account_runner_v1 import TrainingAccountExecutionFailure
    from prepare_weekly_window_v1 import load_bundle
    if worker_resource_handshake()['execution']!={'stage':'account-main','execution_id':eid}:raise PermissionError('ACCOUNT_CONTEXT')
    frozen(); service=WeeklyWindowGovernanceV1(PARENT); bundle=load_bundle()
    context=read(ROOT/'ACCOUNT_EXECUTION.json')
    if bundle.input_identity!=ready()['input_identity'] or context['execution_id']!=eid:raise PermissionError('ACCOUNT_IDENTITY')
    def access():
        from weekly_alias_recovery_v1 import recovery
        recovery()
        return service.active()
    access()
    save(ROOT/'ACCOUNT_ACCESS.json',{'reader_pid':os.getpid(),'thread_id':os.environ['CODEX_THREAD_ID'],
        'recipient':'REQUESTING_USER_AND_CURRENT_RESEARCH','purpose':'APPROVED_FIXED_EXTERNAL_ACCOUNT',
        'input_identity':bundle.input_identity,'execution_id':eid,'at':datetime.now(timezone.utc).isoformat()})
    service.start_exposure(eid)
    try:result=_run_account(bundle,(context['commit'],context['dirty']),access,contract())
    except TrainingAccountExecutionFailure as exc:
        save(ROOT/'ENGINE_FAILURE.json',json.loads(json.dumps(exc.evidence,default=str)))
        raise
    result.update(evaluation_window=[20250801,20260731],
        metric_semantics='LEGACY_TRAIN_FIELDS_REFER_ONLY_TO_APPROVED_EXTERNAL_WINDOW_NOT_QUALIFICATION')
    save(ROOT/'ACCOUNT_RESULT.json',json.loads(json.dumps(result,default=str,allow_nan=False)))
    save(ROOT/'ACCOUNT_FEEDBACK.json',{'status':result['status'],'result_sha256':sha(ROOT/'ACCOUNT_RESULT.json'),
        'qualified':False,'original_train_screen_passed':False})


def report():
    from chanlun_trader.synthetic_batch_resources import worker_resource_handshake
    if worker_resource_handshake()['execution']!={'stage':'report'}:raise PermissionError('REPORT_CONTEXT')
    from review_monthly_robustness_v1 import describe
    frozen()
    result=read(ROOT/'ACCOUNT_RESULT.json'); feedback=read(ROOT/'ACCOUNT_FEEDBACK.json')
    if sha(ROOT/'ACCOUNT_RESULT.json')!=feedback['result_sha256']:raise PermissionError('RESULT_IDENTITY_CONFLICT')
    save(ROOT/'RESULT_REVIEW_ACCESS.json',{'reader_pid':os.getpid(),'thread_id':os.environ['CODEX_THREAD_ID'],
        'recipient':'REQUESTING_USER_AND_CURRENT_RESEARCH','purpose':'APPROVED_EXISTING_RESULT_DESCRIPTIVE_REVIEW',
        'result_sha256':sha(ROOT/'ACCOUNT_RESULT.json'),'settlement_sha256':sha(ROOT/'ACCOUNT_SETTLEMENT.json'),
        'rule_sha256':sha(ROOT/'RESULT_REVIEW_RULE.json'),'at':datetime.now(timezone.utc).isoformat(),
        'new_price_experiments':0,'new_information_access':True})
    summary=describe(result,approved_weekly_window=True) if result['status']=='COMPLETE' and result.get('metrics') else {'status':result['status'],'description':'INCOMPLETE_ACCOUNT_NO_COMPLETE_SUMMARY'}
    save(ROOT/'RESULT_SUMMARY.json',summary)
    text=['# 周低波动＋固定退出＋市场门控：固定外窗结果','',
        '2025-08-01至2026-07-31；原始价成交、HFQ仅作信号。以下train命名字段仅指本次获准外窗。',
        '预热期300114/302132按代码生效日映射；2025-02-17信号价格为获准的既有因子延续派生值，接口原件和公司行动hazard保留，不是厂商版本证明。',
        '','```json',json.dumps(result.get('metrics'),ensure_ascii=False,indent=2),'```','',
        '完整信号、委托、成交、拒绝、lot、现金、持仓、费用见ACCOUNT_RESULT.json；成本倍数与盈利集中度描述见RESULT_SUMMARY.json。',
        '原TRAIN仅23笔的失败保留，不合并窗口凑30笔；本窗口实际关闭lot不足30时仍属样本有限。',
        '描述性成本扣减不是重新执行账户，也不是显著性或正式稳健性通过。公司行动仍按hazard拒绝，发布时间为模型假设；固定历史样本和事件拒绝存在选择限制。',
        '本窗口有其他候选输入访问历史，本次结果也已曝光，不再称未见样本。',
        'READY_FOR_REAL_TRIAL=false；R1_FULLY_CLOSED=false；AUTONOMOUS_STRATEGY_GOAL_COMPLETED=false。']
    with (ROOT/'ACCOUNT_REPORT.md').open('x',encoding='utf-8') as stream:stream.write('\n'.join(text)+'\n')
    save(ROOT/'RESULTS_INDEX.json',{name:{'path':str(ROOT/name),'sha256':sha(ROOT/name)} for name in
        ['ACCOUNT_RESULT.json','ACCOUNT_SETTLEMENT.json','RESULT_SUMMARY.json','RESULT_REVIEW_RULE.json','RESULT_REVIEW_ACCESS.json','ACCOUNT_REPORT.md']})


def run():
    from chanlun_trader.research_factory.mutation_boundary import ObjectiveMutationLock
    with ObjectiveMutationLock.for_resource(ROOT/'ACQUISITION_COMPLETED.json'):
        frozen()
        if not (ROOT/'ACQUISITION_COMPLETED.json').exists():raise PermissionError('ACQUISITION_NOT_COMPLETE')
        if not (ROOT/'READY.json').exists():
            result=bounded('prepare-window')
            if result['returncode']:raise RuntimeError('PREPARATION_FAILED_SEE_RECEIPT')
        value=ready()
        if not value['feasibility_passed']:
            save(ROOT/'FINAL_STATUS.json',{'account_started':False,'reason':'FEASIBILITY_NOT_PASSED',
                'READY_FOR_REAL_TRIAL':False,'R1_FULLY_CLOSED':False,'AUTONOMOUS_STRATEGY_GOAL_COMPLETED':False})
            return
        parent,expiry=active(); grant=frozen()
        plan={'contracts':{stable_hash(contract()):contract()},'limit':2,'wall_limit':1800,
            'result_type':contract()['result_type'],'input_identity':value['input_identity'],
            'objective_id':parent['plan']['objective_id'],'expires_at':min(expiry,datetime.fromisoformat(grant['expires_at'])).isoformat()}
        source={**grant,'approval_record_sha256':sha(ROOT/'WINDOW_RELEASE.json'),
            'window_release_sha256':sha(ROOT/'WINDOW_RELEASE.json'),'approved_plan_sha256':PLAN_HASH}
        service=WeeklyWindowGovernanceV1(PARENT)
        service.confirm(plan,source,preflight=ready)
        reservation=service.reserve(stable_hash(contract()))
        if reservation['status']!='RESERVED':raise PermissionError('EXISTING_MAIN_NO_REPLAY')
        eid=reservation['execution_id']
        save(ROOT/'ACCOUNT_EXECUTION.json',{'execution_id':eid,
            'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=SOURCE,text=True).strip(),
            'dirty':bool(subprocess.check_output(['git','status','--porcelain'],cwd=SOURCE,text=True).strip())})
        then=time.monotonic(); completed=False
        try:
            result=bounded('account-main',eid)
            completed=result['returncode']==0 and (ROOT/'ACCOUNT_FEEDBACK.json').exists()
        finally:service.settle(eid,time.monotonic()-then,completed)
        save(ROOT/'ACCOUNT_SETTLEMENT.json',{'completed':completed,**service.summary()})
        report_completed=completed and bounded('report')['returncode']==0
        save(ROOT/'FINAL_STATUS.json',{'account_started':True,'account_completed':completed,
            'report_completed':report_completed,
            'READY_FOR_REAL_TRIAL':False,'R1_FULLY_CLOSED':False,'AUTONOMOUS_STRATEGY_GOAL_COMPLETED':False})


def continue_after_acquisition():
    """只衔接这一次批准的批次，不创建定时任务，不重试任何失败。"""
    from chanlun_trader.research_factory.mutation_boundary import ObjectiveMutationLock
    with ObjectiveMutationLock.for_resource(ROOT/'CONTINUATION_STARTED.json'):
        frozen()
        save(ROOT/'CONTINUATION_STARTED.json',{'pid':os.getpid(),'at':datetime.now(timezone.utc).isoformat(),
            'purpose':'ONE_APPROVED_BATCH_ACQUISITION_THEN_PREPARE_AND_MAIN','automatic_repair':False})
        try:
            while not (ROOT/'ACQUISITION_COMPLETED.json').exists():
                frozen()
                failed=[str(p) for p in (ROOT/'resources').glob('*.completed.json') if read(p)['returncode']!=0]
                if failed:raise RuntimeError('ACQUISITION_FAILURE:'+','.join(failed))
                time.sleep(30)
            run()
        except Exception as exc:
            save(ROOT/'CONTINUATION_FAILURE.json',{'type':type(exc).__name__,'error':str(exc),
                'at':datetime.now(timezone.utc).isoformat(),'automatic_retry':False})
            raise


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--freeze',action='store_true')
    parser.add_argument('--continue-after-acquisition',action='store_true')
    parser.add_argument('--worker',choices=['prepare-window','account-main','report']);parser.add_argument('--execution-id')
    args=parser.parse_args()
    if args.freeze:freeze()
    elif args.continue_after_acquisition:continue_after_acquisition()
    elif args.worker=='prepare-window':
        frozen()
        from prepare_weekly_window_v1 import prepare
        prepare()
    elif args.worker=='report':report()
    elif args.worker:account(args.execution_id)
    else:run()
