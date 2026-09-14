"""两条固定ETF模型账户，使用原资源worker与权威预算；不自动重试。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
from datetime import datetime, timezone

HANDSHAKE=None
if __name__=='__main__' and any(x in sys.argv for x in ('--worker','--family-worker','--calibration-worker')):
    from chanlun_trader.synthetic_batch_resources import worker_resource_handshake
    HANDSHAKE=worker_resource_handshake()

from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.exploration_governance import immutable, read_json
from chanlun_trader.research_factory.etf_account_governance_v1 import ETFAccountGovernanceV1, KINDS
from chanlun_trader.research_factory.etf_grid_account_v1 import CONTRACT, run_account
from chanlun_trader.research_factory.budget import SearchBudgetRegistryV1
from chanlun_trader.research_factory.strategy_report_v1 import build_report, render_markdown

SOURCE=Path(__file__).resolve().parents[1]
BASE=Path('E:/llmwiki/autonomous-strategy-research-v1/execution-data-v1/etf-grid-train-v1')
ROOT=BASE/'account-model-v1';INPUT=BASE/'input-probe-v1'
BUDGET=Path('E:/llmwiki/autonomous-strategy-research-v1/revised-exploration-v1/governance/search_budget_registry.json')
OBJECTIVE='RESEARCH_OBJECTIVE_SHORT_HORIZON_A_SHARE_V1'
GOV=ETFAccountGovernanceV1(ROOT,BUDGET,OBJECTIVE)


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path,value):
    immutable(path,json.loads(json.dumps(value,ensure_ascii=False,default=str,allow_nan=False)))


def actions():
    output=[]
    for year,record,ex,pay,cash,url in [
        (2023,20230113,20230116,20230119,.064,'https://www.sse.com.cn/disclosure/fund/announcement/c/new/2023-01-09/510300_20230109_0ED5.pdf'),
        (2024,20240117,20240118,20240123,.069,'https://www.sse.com.cn/disclosure/fund/announcement/c/new/2024-01-11/510300_20240111_QQFV.pdf')]:
        output.append({'event_id':f'SSE_510300_CASH_{year}','symbol':'510300.SH','event_type':'CASH_DIVIDEND',
            'record_date':record,'effective_date':ex,'payment_date':pay,'source':url,'units':'CNY_PER_SHARE',
            'terms':{'cash_per_share':cash,'tax_rule':{'kind':'EXPLICIT_NET','source':url}}})
    return output


def freeze():
    if ROOT.exists():raise PermissionError('ETF_RUN_EXISTS_RECONCILE_NO_REPLAY')
    identity=read_json(INPUT/'VOLUME_DECODED_INPUT_IDENTITY_V1.json')
    if sha(identity['path'])!=identity['sha256']:raise ValueError('ETF_INPUT_HASH_CONFLICT')
    pre=read_json(INPUT/'VOLUME_DECODED_PREFLIGHT_V1.json')
    if pre['status']!='MODEL_BASIC_INPUT_CHECKS_PASSED':raise PermissionError('ETF_INPUT_NOT_READY')
    modules=['common','budget','mutation_boundary','exploration_governance','etf_grid_spec_v1',
        'etf_grid_rules_v1','etf_grid_account_v1','etf_account_governance_v1','strategy_report_v1']
    paths=list((SOURCE/'src/chanlun_trader/engine').glob('*.py'))
    paths += [SOURCE/f'src/chanlun_trader/research_factory/{name}.py' for name in modules]
    paths += [Path(__file__),SOURCE/'src/chanlun_trader/synthetic_batch_resources.py']
    manifest={str(p.relative_to(SOURCE)):sha(p) for p in sorted(set(paths))}
    save(ROOT/'PREREGISTRATION.json',{'contract':CONTRACT,'contracts':{
        'GRID_MAIN':{'mode':'CORE_PLUS_GRID','contract_hash':stable_hash(CONTRACT)},
        'BUY_HOLD_BENCHMARK':{'mode':'9000_BUY_HOLD_PLUS_1000_CASH','contract_hash':stable_hash(CONTRACT)}},
        'input':identity,'actions':actions(),'source_manifest':manifest,
        'source_manifest_hash':stable_hash(manifest),'real_exposures_planned':2,'repair_exposures':0,
        'price_exposure_window':[20220801,20240731],'warmup_only_sessions':120,
        'limitations':['RAW指标含除息跳变；原价成交加现金分红，不使用复权价成交。',
        '仅纳入已取得官方条款的两次现金分红；不声称完整公司行动覆盖认证。',
        '历史盘口和实际发布时间未核验；日线收盘后可见、次开盘模型成交。',
        '保留原日线保守涨跌停与前日成交量容量模型。',
        '其他费用0.001%是用户成本假设；佣金率沿用0.00025。']})
    for rel in manifest:
        dest=ROOT/'source-archive'/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(SOURCE/rel,dest)
    old=SearchBudgetRegistryV1(OBJECTIVE,BUDGET).snapshot()
    save(ROOT/'PREVIOUS_BUDGET_SNAPSHOT.json',old)
    GOV.confirm({'origin':'USER_EXPLICIT_CURRENT_TASK','statement':'先完成当前ETF账户验证',
        'spread_model_reply':'允许按上述模型假设回测','reader':'CODEX_CURRENT_TASK',
        'scope':'CURRENT_510300_GRID_AND_MATCHED_COST_BUY_HOLD_ONLY'},
        {**identity,'source_manifest_hash':stable_hash(manifest)})


def worker(kind):
    import pandas as pd
    if HANDSHAKE is None or HANDSHAKE['execution']!={'purpose':kind}:
        raise PermissionError('ETF_WORKER_CONTEXT_REQUIRED')
    receipt=GOV.active();reg=read_json(ROOT/'PREREGISTRATION.json')
    read_json(ROOT/(kind+'_START.json'))
    for rel,digest in reg['source_manifest'].items():
        if sha(SOURCE/rel)!=digest:raise PermissionError('ETF_FROZEN_SOURCE_CHANGED')
    if sha(reg['input']['path'])!=reg['input']['sha256']:raise PermissionError('ETF_INPUT_CHANGED')
    if receipt['inputs']['source_manifest_hash']!=reg['source_manifest_hash']:raise PermissionError('ETF_RECEIPT_SOURCE_CONFLICT')
    frame=pd.read_parquet(reg['input']['path'])
    calendar=read_json(INPUT/'CALENDAR.json')
    expected=calendar['warmup']+calendar['train']
    if list(map(int,frame.date))!=expected:raise PermissionError('ETF_CALENDAR_CONFLICT')
    save(ROOT/(kind+'_INPUT_ACCESS.json'),{'reader_pid':os.getpid(),'purpose':kind,
        'path':reg['input']['path'],'sha256':reg['input']['sha256'],'rows':len(frame),
        'read_at':datetime.now(timezone.utc).isoformat(),'result_recipient':'USER_CURRENT_TASK'})
    result=run_account(frame,reg['actions'],benchmark=kind=='BUY_HOLD_BENCHMARK',active_check=GOV.active)
    save(ROOT/(kind+'_RESULT.json'),result)


def execute():
    from chanlun_trader.synthetic_batch_resources import run_bounded_worker
    freeze()
    for kind in KINDS:
        receipt=GOV.active();GOV.start(kind)
        seconds=min(900,(datetime.fromisoformat(receipt['expires_at'])-datetime.now(timezone.utc)).total_seconds())
        env={**os.environ,'PYTHONPATH':str(SOURCE/'src'),'PYTHONDONTWRITEBYTECODE':'1',
             **{k:'1' for k in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']}}
        env.pop('CHANLUN_TEST_ISOLATION',None)
        start=time.monotonic()
        result=run_bounded_worker([sys.executable,str(Path(__file__)),'--worker',kind],root=SOURCE,
            memory_mib=2048,wall_seconds=seconds,environment=env,execution={'purpose':kind},
            on_started=lambda pid:save(ROOT/(kind+'_WORKER.json'),{'pid':pid,'seconds':seconds,'memory_mib':2048}))
        save(ROOT/(kind+'_RESOURCE.json'),{k:v.decode('utf-8',errors='replace') if isinstance(v,bytes) else v for k,v in result.items()})
        path=ROOT/(kind+'_RESULT.json');complete=result['returncode']==0 and path.exists()
        GOV.settle(kind,completed=complete,seconds=time.monotonic()-start,result_hash=sha(path) if path.exists() else None,
                   error=None if complete else 'WORKER_FAILED_SEE_RESOURCE')
        if not complete:raise RuntimeError('ETF_WORKER_FAILED_NO_AUTOMATIC_RETRY')
    reg=read_json(ROOT/'PREREGISTRATION.json')
    outputs={kind:read_json(ROOT/(kind+'_RESULT.json')) for kind in KINDS}
    for kind,out in outputs.items():
        report=build_report(strategy=CONTRACT,stage='ACCOUNT_BACKTEST',status='MODEL_ACCOUNT_COMPLETED',reasons=[],
            metrics=out['metrics'],artifacts={'trades':str(ROOT/(kind+'_RESULT.json'))+'#fills',
            'ledger':str(ROOT/(kind+'_RESULT.json'))+'#ledgers',
            'source_result':{'path':str(ROOT/(kind+'_RESULT.json')),'sha256':sha(ROOT/(kind+'_RESULT.json'))}},
            benchmark={'status':'AVAILABLE','metrics':outputs['BUY_HOLD_BENCHMARK']['metrics']},limitations=reg['limitations'])
        save(ROOT/(kind+'_REPORT.json'),report)
        with (ROOT/(kind+'_REPORT.md')).open('x',encoding='utf-8') as f:f.write(render_markdown(report))
    before=read_json(ROOT/'PREVIOUS_BUDGET_SNAPSHOT.json')
    after=SearchBudgetRegistryV1(OBJECTIVE,BUDGET).snapshot()
    old={(b['kind'],b['key']):b for b in before['buckets']}
    current={(b['kind'],b['key']):b for b in after['buckets']}
    if any(current[k]!=v for k,v in old.items()):raise ValueError('OLD_BUDGET_CHANGED')
    save(ROOT/'FINAL_ACCESS_AND_RECONCILIATION.json',{'old_buckets_unchanged':True,'new_main_exposures':2,
        'new_repair_exposures':0,'reader':'CODEX_CURRENT_TASK','recipient':'USER_CURRENT_TASK',
        'result_hashes':{k:sha(ROOT/(k+'_RESULT.json')) for k in KINDS},
        'accessed_at':datetime.now(timezone.utc).isoformat(),'AUTONOMOUS_STRATEGY_GOAL_COMPLETED':False,
        'READY_FOR_REAL_TRIAL':False,'R1_FULLY_CLOSED':False})
    print(json.dumps({k:v['metrics'] for k,v in outputs.items()},ensure_ascii=False))


def family_novelty():
    """仅解析固定历史合同，不读取结果/曲线，不启动账户。"""
    from chanlun_trader.research_factory.etf_trend_risk_hypothesis_v1 import FAMILY,contract
    from chanlun_trader.research_factory.train_search_batch_v1 import design,contract as old_contract
    from chanlun_trader.research_factory.novelty import CandidateNoveltyGateV2
    root=BASE/'family-v1';history=[];sources={}
    paths=[BASE.parent/'baostock-account-v1/NOVELTY.json']
    paths += [BASE.parent/f'train-search-batch-v{i}/PREREGISTRATION.json' for i in range(1,44)]
    paths += [ROOT/'PREREGISTRATION.json']
    save(root/'NOVELTY_READ_MANIFEST.json',{'paths':[str(p) for p in paths],
        'purpose':'DESIGN_ONLY_CANONICAL_NOVELTY','no_result_fields_exported':True})
    for path in paths:
        value=read_json(path);sources[str(path)]=sha(path)
        if path.name=='NOVELTY.json':
            history.extend(value['comparison_design_records']);history.append(value['candidate'])
        elif path==ROOT/'PREREGISTRATION.json':
            history.append({'candidate_id':'ARCHIVED_ETF_GRID_ACCOUNT_V1','candidate_hash':stable_hash(value['contract']),
                'mechanism':'CORE_PLUS_GRID','factor_ids':['MA20','MA60','MA120','ATR14','RSI14'],
                'semantic_fingerprint':value['contract'],'parameter_fingerprint':value['contract']})
        else:
            for name,frozen in value['contracts'].items():
                if frozen!=old_contract(name):raise PermissionError('HISTORICAL_CONTRACT_DRIFT:'+name)
                history.append(design(name))
    factors=[['MOMENTUM63','SIGMA20','ATR14'],['RETURN2','SIGMA20','MA60','MA5','ATR14'],
        ['SIGMA20','SIGMA20_MEDIAN60','HIGH20','LOW10','ATR14'],['CALENDAR_MONTH_TURN','SIGMA20','ATR14']]
    candidates=[]
    for name,ids in zip(FAMILY,factors):
        spec=contract(name)
        candidates.append({'candidate_id':name,'candidate_hash':stable_hash(spec),'mechanism':spec['rule_definition'],
            'factor_ids':ids,'semantic_fingerprint':spec['rule_definition'],'parameter_fingerprint':spec})
    decisions={c['candidate_id']:CandidateNoveltyGateV2().evaluate(c,historical_candidates=history,
        same_batch_candidates=[x for x in candidates if x['candidate_id']!=c['candidate_id']]).to_dict() for c in candidates}
    save(root/'NOVELTY_DECISIONS.json',{'decisions':decisions,'historical_records':len(history),'sources':sources,
        'historical_comparison_hash':stable_hash(history),'candidates':candidates,
        'limitation':'CANONICAL_HASH_AND_PARAMETER_GATE_NOT_PROOF_OF_ECONOMIC_INDEPENDENCE',
        'related_history_preserved':True,'execution_authorized':False})
    save(root/'CONTRACTS.json',{name:contract(name) for name in FAMILY})
    print(json.dumps({'historical_records':len(history),'decisions':{k:v['reason'] for k,v in decisions.items()}},ensure_ascii=False))


def family_environment():
    env={**os.environ,'PYTHONPATH':str(SOURCE/'src'),'PYTHONDONTWRITEBYTECODE':'1',
         **{k:'1' for k in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']}}
    env.pop('CHANLUN_TEST_ISOLATION',None)
    return env


def calibration_worker():
    from chanlun_trader.research_factory.holm_family_v1 import CALIBRATION_SPEC,synthetic_calibration
    root=BASE/'family-v1';frozen=read_json(root/'CALIBRATION_PREREGISTRATION.json')
    if HANDSHAKE is None or HANDSHAKE['execution']!={'purpose':'ETF_FAMILY_SYNTHETIC_CALIBRATION'}:
        raise PermissionError('CALIBRATION_WORKER_CONTEXT_REQUIRED')
    if frozen['spec']!=CALIBRATION_SPEC or frozen['source_sha256']!=sha(SOURCE/'src/chanlun_trader/research_factory/holm_family_v1.py'):
        raise PermissionError('CALIBRATION_FREEZE_CONFLICT')
    save(root/'CALIBRATION_RESULT.json',synthetic_calibration())


def calibrate_family():
    from chanlun_trader.research_factory.holm_family_v1 import CALIBRATION_SPEC
    from chanlun_trader.synthetic_batch_resources import run_bounded_worker
    root=BASE/'family-v1'
    save(root/'CALIBRATION_PREREGISTRATION.json',{'spec':CALIBRATION_SPEC,
        'source_sha256':sha(SOURCE/'src/chanlun_trader/research_factory/holm_family_v1.py'),
        'frozen_at':datetime.now(timezone.utc).isoformat(),'real_data_read':False,
        'resource':{'wall_seconds':900,'memory_mib':2048,'threads':1},
        'failure_action':'PRESERVE_FAILURE_NO_REAL_PVALUES_EXPLORATORY_ACCOUNTS_STILL_ALLOWED'})
    result=run_bounded_worker([sys.executable,str(Path(__file__)),'--calibration-worker'],root=SOURCE,
        memory_mib=2048,wall_seconds=900,environment=family_environment(),
        execution={'purpose':'ETF_FAMILY_SYNTHETIC_CALIBRATION'},
        on_started=lambda pid:save(root/'CALIBRATION_WORKER.json',{'pid':pid}))
    save(root/'CALIBRATION_RESOURCE.json',{k:v.decode('utf-8',errors='replace') if isinstance(v,bytes) else v for k,v in result.items()})
    if result['returncode']!=0:raise RuntimeError('CALIBRATION_WORKER_FAILED_NO_RETRY')
    out=read_json(root/'CALIBRATION_RESULT.json')
    print(json.dumps({'calibration_passed':out['passed'],'conditions':{k:{x:v[x] for x in ('raw_fwer','wilson_upper','passed')} for k,v in out['conditions'].items()}}))


def family_service():
    from chanlun_trader.research_factory.etf_account_governance_v1 import ETFFamilyGovernanceV1
    return ETFFamilyGovernanceV1(BASE/'family-v1/account-v1',BUDGET,OBJECTIVE)


def freeze_family():
    from chanlun_trader.research_factory.etf_trend_risk_hypothesis_v1 import FAMILY,BENCHMARK,contract
    family=BASE/'family-v1';root=family/'account-v1'
    if root.exists():raise PermissionError('FAMILY_RUN_EXISTS_RECONCILE_NO_REPLAY')
    inputs=read_json(INPUT/'VOLUME_DECODED_INPUT_IDENTITY_V1.json')
    if sha(inputs['path'])!=inputs['sha256']:raise PermissionError('ETF_INPUT_HASH_CONFLICT')
    if read_json(INPUT/'VOLUME_DECODED_PREFLIGHT_V1.json')['status']!='MODEL_BASIC_INPUT_CHECKS_PASSED':
        raise PermissionError('ETF_INPUT_NOT_READY')
    contracts={k:contract(k) for k in (*FAMILY,BENCHMARK)}
    if read_json(family/'CONTRACTS.json')!={k:contracts[k] for k in FAMILY}:
        raise PermissionError('FAMILY_FROZEN_CONTRACT_CHANGED')
    novelty=read_json(family/'NOVELTY_DECISIONS.json')
    for candidate in novelty['candidates']:
        if candidate['candidate_hash']!=stable_hash(contracts[candidate['candidate_id']]):
            raise PermissionError('NOVELTY_CONTRACT_CONFLICT')
    for path,digest in novelty['sources'].items():
        if sha(path)!=digest:raise PermissionError('NOVELTY_HISTORY_CHANGED')
    # 沿用已冻结源码依赖清单，加入本批规则/检验模块；读取源码不扫描研究数据。
    prior=read_json(ROOT/'PREREGISTRATION.json')
    rels=set(prior['source_manifest'])|{
        'src/chanlun_trader/research_factory/etf_trend_risk_hypothesis_v1.py',
        'src/chanlun_trader/research_factory/holm_family_v1.py'}
    manifest={rel:sha(SOURCE/rel) for rel in sorted(rels)}
    frozen={'contracts':contracts,'input':inputs,'actions':prior['actions'],
        'source_manifest':manifest,'source_manifest_hash':stable_hash(manifest),
        'calendar_sha256':sha(INPUT/'CALENDAR.json'),'novelty_sha256':sha(family/'NOVELTY_DECISIONS.json'),
        'calibration_sha256':sha(family/'CALIBRATION_RESULT.json'),
        'limitations':prior['limitations']+['TRAIN已多次曝光，仅开发筛选；不计算真实p值或授予资格。',
            '日历为已独立冻结的指数日期代理，非交易所历史日历认证；末月不足完整定位不造信号。',
            '基准风险预算相同，但实际市场暴露不同；少亏不等于alpha。'],
        'report_rule':'ALL_FIVE_FULL_DATES_NET_EQUITY_DD_FEES_FILLS_CLOSED_LOTS_EXPOSURE_NO_PARAMETER_SELECTION',
        'screen':'NET_RETURN_GT_0_AND_AT_LEAST_30_CLOSED_LOTS_AND_NO_ENGINE_FAILURE',
        'confirmation':'UNAVAILABLE_EXPOSED_TRAIN_ONLY','real_pvalues':False,'repair_allowance':0,
        'frozen_at':datetime.now(timezone.utc).isoformat()}
    save(root/'PREREGISTRATION.json',frozen)
    for rel in manifest:
        dest=root/'source-archive'/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(SOURCE/rel,dest)
    save(root/'PREVIOUS_BUDGET_SNAPSHOT.json',SearchBudgetRegistryV1(OBJECTIVE,BUDGET).snapshot())
    family_service().confirm({'origin':'USER_EXPLICIT_CURRENT_TASK','statement':'你直接走完为止，告诉我结果就行了',
        'scope':'FIXED_FOUR_CANDIDATES_AND_SHARED_BENCHMARK_FIVE_TRAIN_ACCOUNTS',
        'recipient':'USER_CURRENT_TASK','not_unlimited_search':True},
        {'contracts':contracts,'input_identity':inputs,'basic_input_checks':inputs['basic_input_checks'],
         'source_manifest_hash':stable_hash(manifest),'novelty':novelty['decisions']})


def family_worker(kind):
    import pandas as pd
    from chanlun_trader.research_factory.etf_grid_account_v1 import run_policy_account
    service=family_service();root=service.root
    if HANDSHAKE is None or HANDSHAKE['execution']!={'purpose':kind}:
        raise PermissionError('ETF_WORKER_CONTEXT_REQUIRED')
    receipt=service.active();reg=read_json(root/'PREREGISTRATION.json')
    start=read_json(root/(kind+'_START.json'))
    if start['receipt_id']!=receipt['receipt_id']:raise PermissionError('FAMILY_START_CONFLICT')
    for rel,digest in reg['source_manifest'].items():
        if sha(SOURCE/rel)!=digest:raise PermissionError('ETF_FROZEN_SOURCE_CHANGED')
    if sha(reg['input']['path'])!=reg['input']['sha256'] or sha(INPUT/'CALENDAR.json')!=reg['calendar_sha256']:
        raise PermissionError('FAMILY_INPUT_CHANGED')
    save(root/(kind+'_INPUT_ACCESS.json'),{'reader_pid':os.getpid(),'purpose':kind,'input':reg['input'],
        'read_at':datetime.now(timezone.utc).isoformat(),'result_recipient':'USER_CURRENT_TASK'})
    frame=pd.read_parquet(reg['input']['path']);calendar=read_json(INPUT/'CALENDAR.json')
    if list(map(int,frame.date))!=calendar['warmup']+calendar['train']:
        raise PermissionError('FAMILY_CALENDAR_CONFLICT')
    result=run_policy_account(frame,reg['actions'],candidate=kind,input_identity=reg['input'],active_check=service.active)
    save(root/(kind+'_RESULT.json'),result)


def execute_family():
    from chanlun_trader.synthetic_batch_resources import run_bounded_worker
    freeze_family();service=family_service();root=service.root
    for kind in service.kinds:
        receipt=service.active();service.start(kind)
        seconds=min(900,(datetime.fromisoformat(receipt['expires_at'])-datetime.now(timezone.utc)).total_seconds())
        start=time.monotonic()
        result=run_bounded_worker([sys.executable,str(Path(__file__)),'--family-worker',kind],root=SOURCE,
            memory_mib=2048,wall_seconds=seconds,environment=family_environment(),execution={'purpose':kind},
            on_started=lambda pid:save(root/(kind+'_WORKER.json'),{'pid':pid,'seconds':seconds,'memory_mib':2048}))
        save(root/(kind+'_RESOURCE.json'),{k:v.decode('utf-8',errors='replace') if isinstance(v,bytes) else v for k,v in result.items()})
        path=root/(kind+'_RESULT.json');complete=result['returncode']==0 and path.exists()
        service.settle(kind,completed=complete,seconds=time.monotonic()-start,result_hash=sha(path) if path.exists() else None,
            error=None if complete else 'WORKER_FAILED_SEE_RESOURCE')
        if not complete:raise RuntimeError('FAMILY_WORKER_FAILED_NO_AUTOMATIC_RETRY')
    summarize_family()


def summarize_family():
    import numpy as np
    from collections import Counter
    from chanlun_trader.research_factory.etf_trend_risk_hypothesis_v1 import FAMILY,BENCHMARK
    service=family_service();root=service.root;summary={};identities={};dates=None
    for name in service.kinds:
        path=root/(name+'_RESULT.json');result=read_json(path);digest=sha(path)
        settlement=read_json(root/(name+'_SETTLEMENT.json'))
        if settlement['result_sha256']!=digest or not settlement['completed']:raise ValueError('FAMILY_SETTLEMENT_CONFLICT')
        actual=[r['date'] for r in result['daily']]
        if dates is not None and dates!=actual:raise ValueError('NOT_COMPARABLE_DATES_NO_INTERSECTION')
        dates=actual
        equity=np.array([10000]+[r['equity'] for r in result['daily']]);returns=equity[1:]/equity[:-1]-1
        lots=result['ledger']['state']['lots']
        if isinstance(lots,dict):lots=list(lots.values())
        closed=sum(lot['remaining_quantity']==0 for lot in lots)
        summary[name]={**result['metrics'],'closed_lots':closed,'ending_equity':float(equity[-1]),
            'ending_quantity':result['daily'][-1]['quantity'],'sessions':len(actual),
            'mean_weight':float(np.mean([r['weight'] for r in result['daily']])),
            'annualized_model_volatility':float(np.std(returns,ddof=1)*np.sqrt(252)),
            'decision_counts':dict(Counter(d['reason'] for d in result['decisions'])),
            'order_status_counts':dict(Counter(o['status'] for o in result['orders'])),
            'rejection_reasons':dict(Counter(e['message'] for e in result['order_events'] if 'REJECTED' in str(e['order_status']) or 'EXPIRED' in str(e['order_status']))),
            'screen_passed':bool(result['metrics']['net_return']>0 and closed>=30),
            'formal_qualified':False,'pvalue':None,'holm_pvalue':None}
        identities[name]={'path':str(path),'sha256':digest,'settlement':str(root/(name+'_SETTLEMENT.json'))}
    before=read_json(root/'PREVIOUS_BUDGET_SNAPSHOT.json')
    after=SearchBudgetRegistryV1(OBJECTIVE,BUDGET).snapshot()
    current={(b['kind'],b['key']):b for b in after['buckets']}
    if any(current[(b['kind'],b['key'])]!=b for b in before['buckets']):raise ValueError('OLD_BUDGET_CHANGED')
    save(root/'SUMMARY.json',{'results':summary,'identities':identities,'comparison':'SAME_DATES_DIFFERENT_REALIZED_EXPOSURES',
        'reader':'CODEX_CURRENT_TASK','recipient':'USER_CURRENT_TASK','accessed_at':datetime.now(timezone.utc).isoformat(),
        'new_main_exposures':5,'repair_exposures':0,'old_buckets_unchanged':True,'formal_trial_count':0,
        'READY_FOR_REAL_TRIAL':False,'R1_FULLY_CLOSED':False,'AUTONOMOUS_STRATEGY_GOAL_COMPLETED':False})
    print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--worker',choices=KINDS)
    parser.add_argument('--family-novelty',action='store_true')
    parser.add_argument('--calibrate-family',action='store_true')
    parser.add_argument('--calibration-worker',action='store_true')
    parser.add_argument('--family-run',action='store_true')
    parser.add_argument('--family-worker',choices=family_service().kinds)
    parser.add_argument('--family-summary',action='store_true')
    args=parser.parse_args()
    if sum(bool(v) for v in vars(args).values())>1:parser.error('exclusive modes')
    if args.calibrate_family:calibrate_family()
    elif args.calibration_worker:calibration_worker()
    elif args.family_run:execute_family()
    elif args.family_worker:family_worker(args.family_worker)
    elif args.family_summary:summarize_family()
    elif args.family_novelty:family_novelty()
    elif args.worker:worker(args.worker)
    else:execute()
