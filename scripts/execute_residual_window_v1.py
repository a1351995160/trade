"""用户指定固定候选的另一时段核验：冻结、原预算、原账户及一次结算。"""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from run_baostock_account_v1 import ROOT as TRAIN_INPUT,PARENT,SOURCE,active,read,sha,save
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.residual_window_v1 import contract as window_contract,NAME as LEGACY_NAME,START,END,TURNOVER_VERSIONS,SCALE_VERSIONS,TREND_RISK_VERSIONS,STOCK_TREND_ONLY

NAME=os.environ.get('RESEARCH_RESPONSE_WINDOW_CANDIDATE',LEGACY_NAME)
window_contract(NAME)  # 仅显式白名单中的固定候选；不接受任意名称扩读
IS_STOCK_TREND_ONLY=NAME==STOCK_TREND_ONLY
IS_TREND_RISK=NAME in TREND_RISK_VERSIONS
IS_SCALE=NAME in SCALE_VERSIONS
IS_TURNOVER=NAME in TURNOVER_VERSIONS or IS_SCALE
def contract():
    return window_contract(NAME)
from chanlun_trader.research_factory.residual_window_governance_v1 import ResidualWindowGovernanceV1

BASE=TRAIN_INPUT.parent
INPUT=BASE/'weekly-low-vol-window-v1'
OLD=BASE/'monthly-independent-window-v1'
TRAIN=BASE/('train-search-batch-v42' if IS_STOCK_TREND_ONLY else 'train-search-batch-v40' if IS_TREND_RISK else 'train-search-batch-v38' if IS_SCALE else 'train-search-batch-v35' if IS_TURNOVER else 'train-search-batch-v24' if NAME==LEGACY_NAME else 'train-search-batch-v31')
ROOT=BASE/'stock-trend-only-fixed-window-v1' if IS_STOCK_TREND_ONLY else BASE/'trend-risk-fixed-window-v1' if IS_TREND_RISK else BASE/'scale-fixed-window-v1' if IS_SCALE else BASE/'turnover-fixed-window-v1' if IS_TURNOVER else BASE/'residual-fixed-window-v1' if NAME==LEGACY_NAME else BASE/'response-confirmation-window-v1'/NAME
DOC=SOURCE/('docs/STOCK_TREND_ONLY_FIXED_WINDOW_V1.md' if IS_STOCK_TREND_ONLY else 'docs/TREND_RISK_FIXED_WINDOW_V1.md' if IS_TREND_RISK else 'docs/SCALE_FIXED_WINDOW_V1.md' if IS_SCALE else 'docs/TURNOVER_FIXED_WINDOW_V1.md' if IS_TURNOVER else 'docs/RESIDUAL_FIXED_WINDOW_V1.md' if NAME==LEGACY_NAME else 'docs/RESPONSE_CONFIRMATION_WINDOW_V1.md')


def code():
    from run_train_search_batch_v1 import code as research_code
    import run_train_search_batch_v1 as research
    research.BATCH=42 if IS_STOCK_TREND_ONLY else 40 if IS_TREND_RISK else 38 if IS_SCALE else 35 if IS_TURNOVER else 24 if NAME==LEGACY_NAME else 31
    paths=[Path(__file__),SOURCE/'scripts/prepare_residual_window_v1.py',DOC,
        SOURCE/'scripts/prepare_weekly_window_v1.py',SOURCE/'scripts/review_monthly_robustness_v1.py',
        *[SOURCE/'src/chanlun_trader/research_factory'/n for n in
          ['residual_window_v1.py','residual_window_governance_v1.py','weekly_window_v1.py',
           'monthly_window_v1.py','weekly_parquet_v1.py','common.py','mutation_boundary.py']],
        SOURCE/'src/chanlun_trader/data/tdx/windowed_actions_v1.py',
        *([SOURCE/'scripts/prepare_turnover_window_input_v1.py',SOURCE/'docs/TURNOVER_WINDOW_INPUT_V1.md'] if IS_TURNOVER else [])]
    if IS_SCALE:paths.append(SOURCE/'scripts/scale_window_input_reuse_v1.py')
    return {**research_code(),**{str(p.relative_to(SOURCE)):sha(p) for p in paths}}


def turnover_input_evidence():
    if not IS_TURNOVER:return []
    import prepare_turnover_window_input_v1 as data
    r=data.guard();value=read(data.ROOT/'READY.json')
    reuse=None
    if IS_SCALE:
        import scale_window_input_reuse_v1 as reuse_service
        reuse=reuse_service.guard()
    payload={key:value[key] for key in ('receipt_id','files','request_window','evaluation_window')}
    if (value['status']!='TURNOVER_WINDOW_INPUT_READY_NOT_QUALIFIED'
        or value['input_identity']!=stable_hash(payload) or value['receipt_id']!=r['receipt_id']
        or (not IS_SCALE and (r['candidate']!=NAME or r['source_candidate_hash']!=contract()['source_candidate_contract_hash']))
        or value['request_window']!=[20250704,20260731] or value['evaluation_window']!=[START,END]):
        raise PermissionError('TURNOVER_WINDOW_READY_IDENTITY_CONFLICT')
    expected={'TURNOVER.parquet','COVERAGE.json','MATERIALIZATION_RULE.json'}
    if set(value['files'])!=expected:raise PermissionError('TURNOVER_WINDOW_FILE_SCOPE')
    paths=[data.RECEIPT,data.ROOT/'READY.json']
    if reuse is not None:paths.append(reuse_service.RECEIPT)
    for name,digest in value['files'].items():
        path=data.ROOT/name
        if sha(path)!=digest:raise PermissionError('TURNOVER_WINDOW_MATERIALIZATION_CHANGED')
        paths.append(path)
    import pyarrow.parquet as pq
    parquet=pq.ParquetFile(data.ROOT/'TURNOVER.parquet');position=parquet.schema.names.index('timestamp')
    for group in range(parquet.metadata.num_row_groups):
        stats=parquet.metadata.row_group(group).column(position).statistics
        if stats is None or not 20250704<=int(stats.min)<=int(stats.max)<=END:
            raise PermissionError('TURNOVER_PHYSICAL_WINDOW_UNPROVEN')
    return paths


def freeze():
    parent,expiry=active()
    source=read(TRAIN/'PREREGISTRATION.json')['contracts'][NAME]
    if stable_hash(source)!=contract()['source_candidate_contract_hash']:
        raise PermissionError('TRAIN_CANDIDATE_RULE_CHANGED')
    if read(TRAIN/NAME/'FEEDBACK.json')['screen_passed'] is not True:
        raise PermissionError('SOURCE_TRAIN_SCREEN_NOT_PASSED')
    meta=read(INPUT/'INPUT_MANIFEST.json');release=read(INPUT/'WINDOW_RELEASE.json')
    if stable_hash({'inputs':meta['inputs'],'files':meta['files'],'contract':meta['contract'],
                    'release':release['identity']})!=meta['input_identity']:
        raise PermissionError('SOURCE_INPUT_MANIFEST_IDENTITY_CONFLICT')
    if read(INPUT/'READY.json')['input_identity']!=meta['input_identity']:
        raise PermissionError('SOURCE_INPUT_READY_CONFLICT')
    evidence=[DOC,INPUT/'INPUT_MANIFEST.json',INPUT/'READY.json',INPUT/'WINDOW_RELEASE.json',
        INPUT/'CALENDAR_WINDOW.json',OLD/'CALENDAR_WINDOW.json',TRAIN/'PREREGISTRATION.json',
        TRAIN/NAME/'FEEDBACK.json',TRAIN/NAME/'SETTLEMENT.json',
        TRAIN/NAME/'robustness-review-v1/SUMMARY.json']
    for name in ['DAILY.parquet','STATES.parquet','WINDOW_HAZARDS.json',
                 'alias-recovery-v1/CANONICAL_PRICE_ROWS.json','alias-recovery-v1/DERIVED_PRICE_EVIDENCE.json']:
        p=INPUT/name
        if sha(p)!=meta['files'][name]:raise PermissionError('SOURCE_MATERIALIZATION_CHANGED')
        evidence.append(p)
    evidence+=turnover_input_evidence()
    import pyarrow.parquet as pq
    for name,column in [('DAILY.parquet','date'),('STATES.parquet','trade_date')]:
        parquet=pq.ParquetFile(INPUT/name);position=parquet.schema.names.index(column)
        for group in range(parquet.metadata.num_row_groups):
            stats=parquet.metadata.row_group(group).column(position).statistics
            if stats is None or not 20241009<=int(stats.min)<=int(stats.max)<=END:
                raise PermissionError('PHYSICAL_INPUT_WINDOW_UNPROVEN')
    grant={'origin':'USER_EXPLICIT_PLAN_APPROVAL_VIA_CODEX','thread_id':os.environ['CODEX_THREAD_ID'],
        'approval_statement':('优先核验这个候选的独立窗口表现，保留现有规则，不继续追着训练结果调参。' if NAME==LEGACY_NAME else '接着找，不要停下来，停下来的条件就是找到盈利策略；沿用先前固定规则跨期核验委托'),
        'interpretation':'USER_DELEGATED_FIXED_CANDIDATE_CHECK; AGENT_SELECTED_EXISTING_DISJOINT_PERIOD; NOT_PER_FIELD_OR_PER_CANDIDATE_HUMAN_APPROVAL',
        'candidate':NAME,'source_candidate_hash':stable_hash(source),'objective_id':parent['plan']['objective_id'],
        'parent_receipt_id':parent['receipt_id'],'expires_at':expiry.isoformat(),
        'source_input_identity':meta['input_identity'],'signal_window':[START,END],
        'historical_exposure':'OTHER_CANDIDATE_RESULTS_AND_INPUTS_ALREADY_EXPOSED_NOT_BLIND_HOLDOUT',
        'main_limit':1,'repair_limit':1,'automatic_repair':False,'total_worker_seconds':5400,
        'worker_seconds':900,'memory_mib':2048,'numeric_threads':1,'concurrency':1,
        'at':datetime.now(timezone.utc).isoformat()}
    save(ROOT/'WINDOW_RELEASE.json',grant)
    from review_monthly_robustness_v1 import RULE
    save(ROOT/'RESULT_REVIEW_RULE.json',{**RULE,'evaluation_window':[START,END],
        'independence':'DISJOINT_PERIOD_WITH_PRIOR_EXPOSURE_NOT_BLIND_HOLDOUT',
        'no_new_threshold':'Report original metrics, 30lot sample indicator and existing descriptive flags; no qualification'})
    frozen={'contract':contract(),'code':code(),'release_sha256':sha(ROOT/'WINDOW_RELEASE.json'),
        'review_rule_sha256':sha(ROOT/'RESULT_REVIEW_RULE.json'),
        'inputs':{str(p):sha(p) for p in evidence}}
    save(ROOT/'EXECUTION_FREEZE.json',frozen)
    archived={}
    for name,digest in frozen['code'].items():
        p=Path(name)
        if not p.is_absolute():p=SOURCE/p
        if not p.is_relative_to(SOURCE):continue
        target=ROOT/'source-archive-v1'/p.relative_to(SOURCE)
        target.parent.mkdir(parents=True,exist_ok=True)
        data=p.read_bytes()
        if sha(p)!=digest:raise PermissionError('SOURCE_CHANGED_DURING_ARCHIVE')
        with target.open('xb') as f:f.write(data)
        archived[str(p)]={'archive':str(target),'sha256':digest}
    save(ROOT/'source-archive-v1/INDEX.json',archived)


def guard():
    active();frozen=read(ROOT/'EXECUTION_FREEZE.json');grant=read(ROOT/'WINDOW_RELEASE.json')
    turnover_input_evidence()
    if (ROOT/'revocation.json').exists():raise PermissionError('WINDOW_REVOKED')
    if datetime.now(timezone.utc)>=datetime.fromisoformat(grant['expires_at']):raise PermissionError('WINDOW_EXPIRED')
    if frozen['code']!=code() or frozen['contract']!=contract():raise PermissionError('WINDOW_CODE_OR_CONTRACT_CHANGED')
    if sha(ROOT/'WINDOW_RELEASE.json')!=frozen['release_sha256'] or sha(ROOT/'RESULT_REVIEW_RULE.json')!=frozen['review_rule_sha256']:
        raise PermissionError('WINDOW_RELEASE_OR_REVIEW_CHANGED')
    for p,digest in frozen['inputs'].items():
        if sha(p)!=digest:raise PermissionError('WINDOW_BOUND_INPUT_CHANGED:'+p)
    return grant


def ready():
    guard();value=read(ROOT/'READY.json')
    if sha(ROOT/'FEASIBILITY.json')!=value['feasibility_sha256']:raise PermissionError('FEASIBILITY_CHANGED')
    return value


def bounded(stage,eid=None):
    from chanlun_trader.synthetic_batch_resources import run_bounded_worker
    grant=guard();directory=ROOT/'resources'
    for p in directory.glob('*.started.json'):
        if not p.with_name(p.name.replace('.started.json','.completed.json')).exists():raise PermissionError('UNSETTLED_WORKER')
    start=directory/f'{stage}.started.json';end=directory/f'{stage}.completed.json'
    if start.exists() or end.exists():raise PermissionError('NO_AUTOMATIC_REPLAY')
    used=sum(read(p)['elapsed_seconds'] for p in directory.glob('*.completed.json'))
    limit=min(900,5400-used,(datetime.fromisoformat(grant['expires_at'])-datetime.now(timezone.utc)).total_seconds())
    if limit<=0:raise PermissionError('WINDOW_RESOURCE_EXHAUSTED')
    context={'stage':stage,'execution_id':eid,**({'candidate':NAME} if NAME!=LEGACY_NAME else {})}
    args=[sys.executable,str(Path(__file__)),'--worker',stage]
    if eid:args+=['--execution-id',eid]
    env={**os.environ,'PYTHONPATH':str(SOURCE/'src'),'PYTHONDONTWRITEBYTECODE':'1','PYTHONUNBUFFERED':'1',
        **{k:'1' for k in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']}}
    then=time.monotonic()
    result=run_bounded_worker(args,root=SOURCE,memory_mib=2048,wall_seconds=limit,environment=env,execution=context,
        on_started=lambda pid:save(start,{'pid':pid,'at':datetime.now(timezone.utc).isoformat(),**context}))
    save(end,{'elapsed_seconds':time.monotonic()-then,
        **{k:v.decode('utf-8',errors='replace') if isinstance(v,bytes) else v for k,v in result.items()}})
    return result


def account(eid):
    from prepare_residual_window_v1 import load_bundle
    from chanlun_trader.research_factory.degraded_execution_v2 import _run_account
    from chanlun_trader.research_factory.train_account_runner_v1 import TrainingAccountExecutionFailure
    value=load_bundle();context=read(ROOT/'ACCOUNT_EXECUTION.json');service=ResidualWindowGovernanceV1(PARENT,NAME)
    if context['execution_id']!=eid or value.input_identity!=ready()['input_identity']:raise PermissionError('ACCOUNT_IDENTITY_CONFLICT')
    service.active()
    save(ROOT/'ACCOUNT_ACCESS.json',{'reader_pid':os.getpid(),'thread':os.environ['CODEX_THREAD_ID'],
        'recipient':'REQUESTING_USER_AND_CURRENT_RESEARCH','purpose':'FIXED_EXTERNAL_ACCOUNT_CHECK',
        'input_identity':value.input_identity,'execution_id':eid,'at':datetime.now(timezone.utc).isoformat()})
    service.start_exposure(eid)
    try:result=_run_account(value,(context['commit'],context['dirty']),service.active,contract())
    except TrainingAccountExecutionFailure as exc:
        save(ROOT/'ENGINE_FAILURE.json',json.loads(json.dumps(exc.evidence,default=str)));raise
    result.update(evaluation_window=[START,END],metric_semantics='LEGACY_TRAIN_FIELDS_REFER_TO_DISJOINT_EXTERNAL_PERIOD_NOT_QUALIFICATION')
    save(ROOT/'ACCOUNT_RESULT.json',json.loads(json.dumps(result,default=str,allow_nan=False)))
    save(ROOT/'ACCOUNT_FEEDBACK.json',{'status':result['status'],'result_sha256':sha(ROOT/'ACCOUNT_RESULT.json'),
        'source_train_screen_passed':True,'qualified':False,'blind_holdout':False})


def report():
    from review_monthly_robustness_v1 import describe
    result=read(ROOT/'ACCOUNT_RESULT.json')
    if sha(ROOT/'ACCOUNT_RESULT.json')!=read(ROOT/'ACCOUNT_FEEDBACK.json')['result_sha256']:
        raise PermissionError('RESULT_CHANGED')
    settlement=read(ROOT/'ACCOUNT_SETTLEMENT.json')
    if not settlement['completed']:raise PermissionError('COMPLETE_SETTLEMENT_REQUIRED')
    save(ROOT/'RESULT_REVIEW_ACCESS.json',{'reader_pid':os.getpid(),'recipient':'REQUESTING_USER_AND_CURRENT_RESEARCH',
        'purpose':'EXISTING_EXTERNAL_ACCOUNT_DESCRIPTIVE_REVIEW','new_information_access':True,'new_price_experiments':0,
        'result_sha256':sha(ROOT/'ACCOUNT_RESULT.json'),'settlement_sha256':sha(ROOT/'ACCOUNT_SETTLEMENT.json'),
        'rule_sha256':sha(ROOT/'RESULT_REVIEW_RULE.json'),'at':datetime.now(timezone.utc).isoformat()})
    summary=describe(result,approved_weekly_window=True) if result['status']=='COMPLETE' and result.get('metrics') else {'status':result['status']}
    save(ROOT/'RESULT_SUMMARY.json',summary)
    lines=['# '+NAME+'：另一时段账户核验','',
        '2025-08-01至2026-07-31，与TRAIN不重叠。窗口已有其他候选输入和绩效曝光，不是完全盲化样本外；当前规则和费用未调。',
        ('原RAW成交与HFQ低波动趋势信号分开；' if IS_TREND_RISK else '原RAW成交与HFQ技术信号分开，换手字段仅用于固定分组，保留流通分母版本未知；' if IS_TURNOVER else '原RAW成交与HFQ五日变化分开；')+'复用既有代码时点映射和单日派生衔接证据，发布时间仍为模型假设，公司行动仍为hazard拒绝，非完整会计条款。',
        '', '```json',json.dumps(result.get('metrics'),ensure_ascii=False,indent=2),'```','',
        '完整信号、订单、成交、拒绝、lot、现金、持仓和费用在ACCOUNT_RESULT.json。所有观察月份/年份、静态成本和原交易集中度见RESULT_SUMMARY.json。',
        '关闭lot不足30时保留样本有限，亏损如实报告；不拼接训练交易凑门槛，不重新选择日期或改参数。',
        '描述性检查不是正式统计资格。READY_FOR_REAL_TRIAL=false；R1_FULLY_CLOSED=false；AUTONOMOUS_STRATEGY_GOAL_COMPLETED=false。']
    with (ROOT/'ACCOUNT_REPORT.md').open('x',encoding='utf-8') as f:f.write('\n'.join(lines)+'\n')
    save(ROOT/'RESULTS_INDEX.json',{n:{'path':str(ROOT/n),'sha256':sha(ROOT/n)} for n in
        ['ACCOUNT_RESULT.json','ACCOUNT_SETTLEMENT.json','ACCOUNT_REPORT.md','RESULT_SUMMARY.json','RESULT_REVIEW_RULE.json','RESULT_REVIEW_ACCESS.json']})


def run():
    from chanlun_trader.research_factory.mutation_boundary import ObjectiveMutationLock
    with ObjectiveMutationLock.for_resource(ROOT/'RUNNER_STARTED.json'):
        guard();save(ROOT/'RUNNER_STARTED.json',{'pid':os.getpid(),'at':datetime.now(timezone.utc).isoformat()})
        state={'account_started':False,'account_completed':False,'report_completed':False,
            'READY_FOR_REAL_TRIAL':False,'R1_FULLY_CLOSED':False,'AUTONOMOUS_STRATEGY_GOAL_COMPLETED':False}
        try:
            if bounded('prepare')['returncode']:raise RuntimeError('PREPARATION_FAILED_SEE_RECEIPT')
            value=ready()
            if not value['feasibility_passed']:
                state['reason']='FEASIBILITY_NOT_PASSED';return
            parent,expiry=active();grant=guard()
            plan={'contracts':{stable_hash(contract()):contract()},'limit':2,'wall_limit':1800,
                'result_type':contract()['result_type'],'input_identity':value['input_identity'],
                'objective_id':parent['plan']['objective_id'],'expires_at':expiry.isoformat()}
            source={**grant,'approval_record_sha256':sha(ROOT/'WINDOW_RELEASE.json'),
                'window_release_sha256':sha(ROOT/'WINDOW_RELEASE.json'),'approved_plan_sha256':sha(DOC)}
            service=ResidualWindowGovernanceV1(PARENT,NAME);service.confirm(plan,source,preflight=ready)
            reserve=service.reserve(stable_hash(contract()))
            if reserve['status']!='RESERVED':raise PermissionError('EXISTING_MAIN_NO_REPLAY')
            eid=reserve['execution_id']
            save(ROOT/'ACCOUNT_EXECUTION.json',{'execution_id':eid,
                'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=SOURCE,text=True).strip(),
                'dirty':bool(subprocess.check_output(['git','status','--porcelain'],cwd=SOURCE,text=True).strip())})
            then=time.monotonic();complete=False
            try:
                result=bounded('account-main',eid)
                complete=result['returncode']==0 and (ROOT/'ACCOUNT_FEEDBACK.json').exists()
            finally:service.settle(eid,time.monotonic()-then,complete)
            tally=service.summary()
            save(ROOT/'ACCOUNT_SETTLEMENT.json',{'completed':complete,**tally})
            state.update(account_started=tally['MAIN_BACKTEST_EXPOSURES_USED']>0,account_completed=complete)
            if not complete:raise RuntimeError('ACCOUNT_FAILED_SEE_RECEIPT')
            state['report_completed']=bounded('report')['returncode']==0
            if not state['report_completed']:raise RuntimeError('REPORT_FAILED_SEE_RECEIPT')
        except Exception as exc:
            state.update(error_type=type(exc).__name__,error=str(exc));raise
        finally:save(ROOT/'FINAL_STATUS.json',state)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--freeze',action='store_true')
    parser.add_argument('--worker',choices=['prepare','account-main','report']);parser.add_argument('--execution-id')
    args=parser.parse_args()
    if args.freeze:freeze()
    elif args.worker:
        from chanlun_trader.synthetic_batch_resources import worker_resource_handshake
        if worker_resource_handshake()['execution']!={'stage':args.worker,'execution_id':args.execution_id,**({'candidate':NAME} if NAME!=LEGACY_NAME else {})}:
            raise PermissionError('WORKER_CONTEXT_CHANGED')
        guard()
        if args.worker=='prepare':
            from prepare_residual_window_v1 import prepare
            prepare()
        elif args.worker=='account-main':account(args.execution_id)
        else:report()
    else:run()
