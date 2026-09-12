"""用户明确批准的四协议探索入口；终端只输出盲化状态。"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
import sys
import time

from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.exploration_governance import ExplorationGovernanceServiceV1, immutable, read_json
from chanlun_trader.research_factory.novelty import CandidateNoveltyGateV2, design_safe_candidate
from chanlun_trader.research_factory.context import PerformanceBlindGuard

SOURCE=Path(__file__).resolve().parents[1]
BASE=Path('E:/llmwiki/autonomous-strategy-research-v1/blocker-resolution-v2')
OUT=Path('E:/llmwiki/autonomous-strategy-research-v1/revised-exploration-v1')
SNAPSHOT=BASE/'train-source-v1/TRAIN_INPUT_SNAPSHOT_DIAGNOSTIC.parquet'
EXPECTED='741690ba3b068610908e49465b8731ef45a6638391e3db1d3b84ebe1b645ad72'
ATTACHMENT=Path('C:/Users/84219/Downloads/CODEX_APPROVE_AND_EXECUTE_BOUNDED_EXPLORATION_V1.md')
CALENDAR=Path('E:/llmwiki/chanlun-trading-system/data/research/security_state/raw/trade_calendar.json')
METADATA=[BASE/'train-source-v1/SELECTED_SOURCE_PLAN.json',BASE/'train-source-v1/DATA_PROVENANCE_AND_TIME_EVIDENCE.json',
          BASE/'HISTORY_BUDGET_BLIND_RECONCILIATION_FINAL.json',
          BASE/'exact-evidence-v1/HISTORICAL_AUTHORITY_BLIND_RECONCILIATION_FINAL.json']


def file_hash(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024),b''): digest.update(block)
    return digest.hexdigest()


def plain_path(path):
    path=Path(path).absolute()
    if path.resolve()!=path: raise ValueError('EXPLORATION_PATH_REDIRECTED')
    return path


def code_hashes():
    files=['scripts/run_bounded_exploration.py','src/chanlun_trader/research_factory/exploration_relation.py',
           'src/chanlun_trader/research_factory/exploration_governance.py','src/chanlun_trader/research_factory/budget.py',
           'src/chanlun_trader/synthetic_batch_resources.py','src/chanlun_trader/research_factory/novelty.py',
           'src/chanlun_trader/research_factory/context.py','src/chanlun_trader/research_factory/common.py',
           'src/chanlun_trader/research_factory/mutation_boundary.py']
    return {str(SOURCE/p):file_hash(SOURCE/p) for p in files}


def contracts():
    result={}
    for protocol in ('A','B'):
        for placebo in (False,True):
            name=protocol+('_placebo' if placebo else '_main')
            item={'protocol':protocol,'placebo':placebo,'signal_lag_sessions':20 if placebo else 0,
                  'x':'C(t)/C(t-5)-1' if protocol=='A' else '(C(t)-min(L(t-19:t)))/(max(H(t-19:t))-min(L(t-19:t)))',
                  'group1':'x<0' if protocol=='A' else 'x>=0.8','y':'C(t+3)/C(t)-1',
                  'calendar_indexed':True,'missing':'NOT_COMPUTABLE_NO_FILL','label_complete_sessions':4,
                  'signal_complete_sessions':6 if protocol=='A' else 20,
                  'result_type':'EXPLORATORY_RAW_PRICE_RELATION','flags':['NOT_TRADABLE','NOT_FOR_QUALIFICATION']}
            result[name]={**item,'contract_hash':stable_hash(item)}
    return result


def prepare():
    plain_path(OUT)
    if OUT.is_relative_to(SOURCE) or OUT.is_relative_to(SNAPSHOT.parent) or SOURCE.is_relative_to(OUT) or SNAPSHOT.is_relative_to(OUT):
        raise ValueError('OUTPUT_OVERLAPS_INPUT_OR_CODE')
    OUT.mkdir(parents=True,exist_ok=True)
    if (OUT/'governance/confirmation.json').exists():
        receipt=read_json(OUT/'governance/confirmation.json')
        if receipt['plan']['code_hashes']!=code_hashes(): raise PermissionError('FROZEN_CODE_CHANGED')
        return receipt
    for path in [SNAPSHOT,CALENDAR,ATTACHMENT,*METADATA]: plain_path(path)
    immutable(OUT/'input_read_plan.json',{'snapshot':str(SNAPSHOT),'expected_sha256':EXPECTED,
        'fields':['symbol','date','close','high','low'],'calendar':str(CALENDAR),'metadata':list(map(str,METADATA)),
        'no_reference_following':True,'window':[20220801,20240731]})
    if file_hash(SNAPSHOT)!=EXPECTED: raise ValueError('SNAPSHOT_HASH_MISMATCH')
    import pyarrow.parquet as pq
    header=pq.ParquetFile(SNAPSHOT)
    if not {'symbol','date','close','high','low'} <= set(header.schema_arrow.names) or header.metadata.num_rows!=2370849:
        raise ValueError('SNAPSHOT_SCHEMA_OR_ROW_COUNT_MISMATCH')
    cal=read_json(CALENDAR)
    dates=[int(str(x).replace('-','')) for x in cal['trade_dates'] if 20220801<=int(str(x).replace('-',''))<=20240731]
    if dates!=sorted(set(dates)) or len(dates)!=486 or dates[0]!=20220801 or dates[-1]!=20240731:
        raise ValueError('TRAIN_CALENDAR_MISMATCH')
    selected=read_json(METADATA[0]); coverage=read_json(METADATA[1])
    if len(selected['selected'])!=5036 or len(selected['missing_symbols'])!=146 or coverage['pit_member_symbols']!=5182:
        raise ValueError('COVERAGE_MISMATCH')
    observed={item[0] for item in selected['selected']}
    missing=set(selected['missing_symbols'])
    if len(observed)!=5036 or len(missing)!=146 or observed & missing:
        raise ValueError('COVERAGE_MEMBERSHIP_CONFLICT')
    historical={}
    def collect(value):
        if isinstance(value,dict):
            if value.get('candidate_id') and value.get('candidate_hash'):
                safe=design_safe_candidate(value)
                historical[(safe['candidate_id'],safe['candidate_hash'])]=safe
            for item in value.values():
                if isinstance(item,(dict,list)): collect(item)
        elif isinstance(value,list):
            for item in value: collect(item)
    for p in METADATA[2:]: collect(read_json(p))
    if not historical: raise PermissionError('NOVELTY_COMPARISON_SET_MISSING')
    fixed=contracts(); peers=[]; decisions={}
    for name,contract in fixed.items():
        design={'candidate_id':name,'candidate_hash':contract['contract_hash'],
                'mechanism':contract['x'],'semantic_fingerprint':stable_hash({k:contract[k] for k in ('x','y','signal_lag_sessions')}),
                'parameter_fingerprint':{'threshold':0 if contract['protocol']=='A' else .8,'signal_lag':contract['signal_lag_sessions']},
                'factor_ids':['RETURN_5D'] if contract['protocol']=='A' else [],'event_ids':[],
                'holding_period_days':3}
        decisions[name]=CandidateNoveltyGateV2().evaluate(design,historical_candidates=historical.values(),same_batch_candidates=peers).to_dict()
        peers.append(design)
    immutable(OUT/'novelty.json',{'policy':'CandidateNoveltyGateV2','decisions':decisions,
        'comparison_design_records':list(historical.values()),'comparison_scope':'Existing authorized blind projections; identities and available design fields, not complete global semantic history',
        'history_incomplete':True,'unknown_fields_not_invented':True,'formal_novelty_certified':False})
    plan={'objective_id':'RESEARCH_OBJECTIVE_SHORT_HORIZON_A_SHARE_V1','contracts':fixed,
          'proposal_path':str(SOURCE/'docs/REVISED_RESEARCH_DECISION_PROPOSAL_V1.md'),
          'proposal_sha256':file_hash(SOURCE/'docs/REVISED_RESEARCH_DECISION_PROPOSAL_V1.md'),
          'code_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=SOURCE,text=True).strip(),
          'proposal_commit':'e1fa914ee7f05a3cc2cce6797738ba7c50ee181b',
          'recovery_commit':'8cc3ac0ba79158263995c6f5e14418f8b023cd59',
          'input_path':str(SNAPSHOT),'input_sha256':EXPECTED,'rows':2370849,'calendar':dates,
          'metadata_hashes':{str(p):file_hash(p) for p in [CALENDAR,*METADATA]},
          'universe_count':5182,'observed_sources':5036,'missing_sources':146,
          'observed_symbol_set_hash':stable_hash(sorted(observed)),
          'limit':6,'planned_limit':4,'repair_limit':2,'wall_limit':5400,'memory_mib':2048,
          'expires_at':'2026-09-14T10:05:03+08:00','code_hashes':code_hashes(),
          'result_type':'EXPLORATORY_RAW_PRICE_RELATION','old_budget_used':12,'old_budget_remaining':0,
          'old_objective_budget_conflict':[8,12],'history_identities':[16,19],
          'old_validation_access_claim':23,'old_factor_claim':18,'global_history':'UNKNOWN',
          'novelty_hash':file_hash(OUT/'novelty.json')}
    source={'origin':'USER_EXPLICIT_PLAN_APPROVAL_VIA_CODEX','thread_id':'01a08e36-0c4d-72a1-a4de-76d4d15be09c',
            'attachment_path':str(ATTACHMENT),'attachment_sha256':file_hash(ATTACHMENT),
            'approval_statement':'用户明确批准REVISED_RESEARCH_DECISION_PROPOSAL_V1两协议受限探索；四次main/placebo加最多两次确证修复重算，总6次、90分钟及必要治理适配。',
            'approval_message_timestamp':None,'timestamp_basis':'Message timestamp unavailable; service recorded_at is recording time, not fabricated user signature or click time'}
    for original,expected in plan['code_hashes'].items():
        target=OUT/'frozen_source'/Path(original).relative_to(SOURCE)
        target.parent.mkdir(parents=True,exist_ok=True)
        if not target.exists():
            with target.open('xb') as stream: stream.write(Path(original).read_bytes())
        if file_hash(target)!=expected: raise ValueError('FROZEN_SOURCE_ARCHIVE_CONFLICT')
    return ExplorationGovernanceServiceV1(OUT).confirm(plan,source)


def validate_frame(frame, plan):
    if not {'symbol','date','close','high','low'}<=set(frame.columns):
        raise ValueError('SNAPSHOT_REQUIRED_FIELD_MISSING')
    if len(frame)!=plan['rows'] or frame.symbol.nunique()!=plan['observed_sources']:
        raise ValueError('ACTUAL_INPUT_COVERAGE_MISMATCH')
    if frame.duplicated(['date','symbol']).any(): raise ValueError('DUPLICATE_SECURITY_SESSION')
    if not set(frame.date).issubset(plan['calendar']): raise ValueError('INPUT_WINDOW_MISMATCH')
    if frame.symbol.isna().any(): raise ValueError('MISSING_SYMBOL')
    if stable_hash(sorted(frame.symbol.unique()))!=plan['observed_symbol_set_hash']:
        raise ValueError('ACTUAL_INPUT_MEMBERSHIP_MISMATCH')


def worker(execution, contract_id):
    from chanlun_trader.synthetic_batch_resources import worker_resource_handshake
    config=worker_resource_handshake()
    service=ExplorationGovernanceServiceV1(OUT)
    try:
        receipt=service.active(); plan=receipt['plan']
        if config['execution']!={'execution_id':execution,'contract_id':contract_id,'plan_id':receipt['plan_id']}:
            raise PermissionError('WORKER_CONTEXT_MISMATCH')
        if code_hashes()!=plan['code_hashes']: raise PermissionError('FROZEN_CODE_CHANGED')
        if file_hash(OUT/'novelty.json')!=plan['novelty_hash'] or not read_json(OUT/'novelty.json')['decisions'][contract_id]['allowed']:
            raise PermissionError('NOVELTY_REJECTED')
        for p,h in plan['metadata_hashes'].items():
            plain_path(p)
            if file_hash(p)!=h: raise ValueError('INPUT_METADATA_CHANGED')
        plain_path(SNAPSHOT)
        if file_hash(SNAPSHOT)!=plan['input_sha256']: raise ValueError('SNAPSHOT_HASH_MISMATCH')
        import pyarrow.parquet as pq
        import pyarrow as pa
        pa.set_cpu_count(1)
        pa.set_io_thread_count(1)
        frame=pq.read_table(SNAPSHOT,columns=['symbol','date','close','high','low'],use_threads=False).to_pandas(use_threads=False)
        validate_frame(frame,plan)
        contract=plan['contracts'][contract_id]
        service.start_exposure(execution)
        from chanlun_trader.research_factory.exploration_relation import calculate
        rows=calculate(frame,plan['calendar'],contract['protocol'],contract['placebo'],plan['universe_count'],service.active)
        if [row['date'] for row in rows]!=plan['calendar'] or any(
                row['group1_count']+row['group2_count']+row['not_computable_count']!=plan['universe_count'] for row in rows):
            raise ValueError('EXPLORATION_RESULT_COVERAGE_INVALID')
        result={'schema_version':'exploratory-raw-price-relation-v1','type':'EXPLORATORY_RAW_PRICE_RELATION','flags':['NOT_TRADABLE','NOT_FOR_QUALIFICATION'],
                'plan_id':receipt['plan_id'],'contract':contract,'input_sha256':plan['input_sha256'],
                'execution_id':execution,'rows':rows,'history_incomplete':True,'formal_trial':False}
        target=OUT/'evaluation'/contract_id/execution/'result.json'
        immutable(target,result)
        immutable(OUT/'execution'/execution/'completed.json',{'status':'COMPLETED','artifact_path':str(target),'sha256':file_hash(target)})
        print(json.dumps({'status':'COMPLETED','execution_id':execution}),flush=True)
    except Exception as exc:
        # 不把数组、收益或原始异常消息返回设计端。
        code=str(exc) if re.fullmatch('[A-Z_]{3,100}',str(exc)) else 'EVALUATOR_ERROR_DETAILS_ARCHIVED'
        immutable(OUT/'evaluation'/contract_id/execution/'error_detail.json',{'exception_type':type(exc).__name__,'message':str(exc)})
        error={'status':'FAILED','exception_type':type(exc).__name__,'error_code':code}
        immutable(OUT/'execution'/execution/'error.json',error)
        print(json.dumps(error),flush=True)
        raise SystemExit(1)


def execute():
    from chanlun_trader.synthetic_batch_resources import run_bounded_worker
    if os.environ.get('CHANLUN_TEST_ISOLATION')=='1':
        raise PermissionError('REAL_EXPLORATION_CANNOT_USE_TEST_ISOLATION')
    receipt=prepare(); service=ExplorationGovernanceServiceV1(OUT)
    decisions=read_json(OUT/'novelty.json')['decisions']
    for name in receipt['plan']['contracts']:
        service.active()
        if not decisions[name]['allowed']:
            immutable(OUT/'rejections'/(name+'.json'),{'status':'NOVELTY_REJECTED','decision':decisions[name]})
            continue
        reservation=service.reserve(name)
        if reservation['status']=='ALREADY_ATTEMPTED':
            if not any(e['event']=='SETTLED' for e in reservation['events']):
                raise PermissionError('UNSETTLED_EXPLORATION_REQUIRES_RECONCILIATION')
            continue
        eid=reservation['execution_id']
        environment=dict(os.environ,PYTHONPATH=str(SOURCE/'src')+os.pathsep+str(SOURCE),
                         OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1')
        begin=time.monotonic()
        completed=False
        try:
            result=run_bounded_worker([sys.executable,str(Path(__file__).resolve()),'--worker',eid,name],
                root=SOURCE,memory_mib=2048,wall_seconds=reservation['wall_seconds'],
                on_started=lambda pid: immutable(OUT/'execution'/eid/'process.json',
                    {'pid':pid,'plan_id':receipt['plan_id'],'memory_mib':2048,'wall_seconds_limit':reservation['wall_seconds'],
                     'numerical_threads':1,'resource_mechanism':'WindowsMemoryJob' if os.name=='nt' else 'worker_resource_handshake'}),
                environment=environment,execution={'execution_id':eid,'contract_id':name,'plan_id':receipt['plan_id']})
            completed=result['returncode']==0 and (OUT/'execution'/eid/'completed.json').exists()
            immutable(OUT/'evaluation'/name/eid/'worker_output.json',
                      {key:result[key].decode('utf-8',errors='replace') for key in ('stdout','stderr')})
            immutable(OUT/'execution'/eid/'process_exit.json',{'returncode':result['returncode'],'timed_out':result['timed_out']})
        finally:
            service.settle(eid,time.monotonic()-begin,completed)
        print(json.dumps({'contract':name,'status':'COMPLETED' if completed else 'FAILED_SETTLED'}),flush=True)
    summary=service.summary()
    PerformanceBlindGuard.assert_blind(summary)
    immutable(OUT/'summary.json',summary)
    print(json.dumps(summary),flush=True)


if __name__=='__main__':
    args=argparse.ArgumentParser()
    args.add_argument('--worker',nargs=2)
    args.add_argument('--execute-approved-plan',action='store_true')
    options=args.parse_args()
    if options.worker: worker(*options.worker)
    elif options.execute_approved_plan: execute()
    else: raise SystemExit('Explicit approved-plan entry required')
