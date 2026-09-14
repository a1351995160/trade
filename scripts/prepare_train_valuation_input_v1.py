"""原TRAIN固定估值字段准备；无信号、无账户、无自动重试。"""
import argparse
from datetime import datetime,timezone
from importlib.metadata import version
import json
import os
from pathlib import Path
import sys
import time

from run_baostock_account_v1 import ROOT as INPUT,PARENT,SOURCE,active,read,save,sha
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.mutation_boundary import ObjectiveMutationLock

ROOT=INPUT.parent/'train-valuation-input-v1'
RECEIPT=PARENT/'governance/train_valuation_input_v1/confirmation.json'
FIELDS='date,code,peTTM,pbMRQ,turn'
START='2022-07-22';END='2024-07-31'


def code():
    paths=[Path(__file__),SOURCE/'scripts/run_baostock_account_v1.py',
        SOURCE/'src/chanlun_trader/data/minute/baostock_provider.py',
        SOURCE/'src/chanlun_trader/synthetic_batch_resources.py',
        SOURCE/'src/chanlun_trader/research_factory/exploration_governance.py',
        SOURCE/'src/chanlun_trader/research_factory/common.py',
        SOURCE/'src/chanlun_trader/research_factory/mutation_boundary.py',
        SOURCE/'docs/TRAIN_VALUATION_INPUT_V1.md']
    return {str(p):sha(p) for p in paths}


def confirm():
    parent,expiry=active()
    source=INPUT/'READ_PLAN.json';original=read(source)
    symbols=original['symbols']
    if len(symbols)!=5182 or symbols!=sorted(set(symbols)) or original['start']!=START or original['end']!=END:
        raise PermissionError('ORIGINAL_TRAIN_IDENTITY_CONFLICT')
    thread=os.environ.get('CODEX_THREAD_ID')
    if not thread:raise PermissionError('ACTUAL_THREAD_REQUIRED')
    plan={'version':'TRAIN_VALUATION_INPUT_V1','objective_id':parent['plan']['objective_id'],
        'parent_receipt_id':parent['receipt_id'],'symbols':symbols,'start':START,'end':END,
        'fields':FIELDS,'frequency':'d','adjustflag':'3','source_files':{str(source):sha(source)},
        'code':code(),'expires_at':expiry.isoformat(),'wall_seconds':5400,'worker_seconds':900,
        'memory_mib':2048,'numeric_threads':1,'concurrency':1,'first_probe_count':4,
        'provider_version':version('baostock'),'new_performance_allowance':0,
        'historical_publication_time':'UNKNOWN','PIT':'NOT_ATTESTED'}
    authority={'origin':'USER_DELEGATED_RESEARCH_DATA_PREPARATION_NOT_PER_FIELD_APPROVAL',
        'thread_id':thread,'approval_statement':'接着找，不要停下来，停下来的条件就是找到盈利策略',
        'prior_scope_statement':'我批准你，我只有一个诉求，找到能盈利的为止，不需要任何限制，直接开干',
        'interpretation':'AGENT_CHOSEN_FREE_TRAIN_FIELDS; NO_ORDERS_NO_PAID_SOURCE_NO_NEW_WINDOW'}
    with ObjectiveMutationLock.for_resource(RECEIPT):
        if RECEIPT.exists():
            prior=guard()
            if prior['plan']!=plan or prior['authority']!=authority:raise PermissionError('VALUATION_RECEIPT_CONFLICT')
            return prior
        receipt={'plan':plan,'authority':authority,'at':datetime.now(timezone.utc).isoformat(),
            'canonical_budget_path':str(PARENT/'governance/search_budget_registry.json'),
            'canonical_budget_sha256_at_confirmation':sha(PARENT/'governance/search_budget_registry.json')}
        receipt['receipt_id']=stable_hash(receipt);save(RECEIPT,receipt)
        save(ROOT/'READ_PLAN_RECEIPT.json',{'path':str(RECEIPT),'sha256':sha(RECEIPT)})
        return receipt


def guard():
    parent,expiry=active();r=read(RECEIPT)
    if r['receipt_id']!=stable_hash({k:v for k,v in r.items() if k!='receipt_id'}):
        raise PermissionError('VALUATION_RECEIPT_CORRUPT')
    p=r['plan']
    if (p['parent_receipt_id']!=parent['receipt_id'] or p['objective_id']!=parent['plan']['objective_id']
            or p['code']!=code() or p['provider_version']!=version('baostock')):
        raise PermissionError('VALUATION_SOURCE_IDENTITY_CHANGED')
    if (RECEIPT.parent/'revocation.json').exists() or (ROOT/'revocation.json').exists():raise PermissionError('VALUATION_REVOKED')
    if datetime.now(timezone.utc)>=min(expiry,datetime.fromisoformat(p['expires_at'])):raise PermissionError('VALUATION_EXPIRED')
    for path,digest in p['source_files'].items():
        if sha(path)!=digest:raise PermissionError('VALUATION_INPUT_CHANGED')
    return r


def validate_response(payload,symbol):
    import math
    if payload['error_code']!='0':raise ValueError('PROVIDER_ERROR:'+payload['error_code'])
    if payload['fields']!=FIELDS.split(','):raise ValueError('VALUATION_FIELDS_CONFLICT')
    dates=set();missing={f:0 for f in ('peTTM','pbMRQ','turn')}
    for row in payload['rows']:
        if set(row)!=set(FIELDS.split(',')) or row['code']!=symbol[-2:].lower()+'.'+symbol[:6]:raise ValueError('VALUATION_ROW_IDENTITY_CONFLICT')
        d=row['date']
        if not START<=d<=END or d in dates or datetime.strptime(d,'%Y-%m-%d').strftime('%Y-%m-%d')!=d:
            raise ValueError('VALUATION_WINDOW_OR_DATE_CONFLICT')
        dates.add(d)
        for f in missing:
            if row[f]=='':missing[f]+=1;continue
            value=float(row[f])
            if not math.isfinite(value) or (f=='turn' and value<0):raise ValueError('VALUATION_NONFINITE_OR_NEGATIVE_TURN')
    return {'row_count':len(dates),'missing':missing,'status':'SCHEMA_CHECKED_NOT_PIT_ATTESTED'}


def fetch(start,end):
    from chanlun_trader.synthetic_batch_resources import worker_resource_handshake
    from chanlun_trader.data.minute.baostock_provider import BaoStock5MinProvider
    context=worker_resource_handshake()['execution']
    if context!={'purpose':'TRAIN_VALUATION_INPUT_V1','start':start,'end':end}:raise PermissionError('VALUATION_WORKER_CONTEXT_CHANGED')
    plan=guard()['plan'];provider=BaoStock5MinProvider()
    if not 0<=start<end<=len(plan['symbols']):raise PermissionError('VALUATION_SLICE_INVALID')
    if (start==0 and end!=4) or (start>0 and not (ROOT/'PROBE_ACCEPTED.json').exists()):raise PermissionError('PROBE_REQUIRED')
    with provider.session():
        for symbol in plan['symbols'][start:end]:
            guard();folder=ROOT/'responses'/symbol
            target=folder/'response.json';access=folder/'access.json';started=folder/'started.json'
            query={'code':symbol[-2:].lower()+'.'+symbol[:6],'fields':FIELDS,'start_date':START,'end_date':END,'frequency':'d','adjustflag':'3'}
            if started.exists():
                if target.exists() and access.exists() and sha(target)==read(access)['sha256'] and read(started)['query']==query:
                    validate_response(read(target),symbol);continue
                raise PermissionError('VALUATION_UNRECONCILED_ATTEMPT_NO_AUTO_RETRY:'+symbol)
            save(started,{'query':query,'reader_pid':os.getpid(),'at':datetime.now(timezone.utc).isoformat()})
            response=provider.bs.query_history_k_data_plus(**query);rows=[]
            while response.error_code=='0' and response.next():rows.append(dict(zip(response.fields,response.get_row_data())))
            payload={'query':query,'fields':response.fields,'rows':rows,'error_code':response.error_code,'error_msg':response.error_msg}
            save(target,payload)
            save(access,{'path':str(target),'sha256':sha(target),'reader_pid':os.getpid(),'recipient':'REQUESTING_USER_AND_INPUT_EVALUATION','purpose':'TRAIN_VALUATION_INPUT_NO_SIGNAL_NO_PERFORMANCE','at':datetime.now(timezone.utc).isoformat()})
            quality=validate_response(payload,symbol);save(folder/'quality.json',quality)
            print(json.dumps({'symbol':symbol,**quality}),flush=True)


def run(probe=False):
    from chanlun_trader.synthetic_batch_resources import run_bounded_worker
    receipt=confirm();plan=receipt['plan']
    if not probe and not (ROOT/'PROBE_ACCEPTED.json').exists():raise PermissionError('VALUATION_PROBE_NOT_ACCEPTED')
    chunks=[(0,4)] if probe else [(i,min(i+50,len(plan['symbols']))) for i in range(4,len(plan['symbols']),50)]
    for start,end in chunks:
        guard();label=f'fetch-{start}-{end}';beg=ROOT/'resources'/f'{label}.started.json';done=ROOT/'resources'/f'{label}.completed.json'
        if beg.exists():
            if done.exists() and read(done)['returncode']==0:continue
            raise PermissionError('VALUATION_WORKER_ALREADY_ATTEMPTED:'+label)
        used=sum(read(p)['elapsed_seconds'] for p in (ROOT/'resources').glob('*.completed.json'))
        seconds=min(120 if probe else 900,5400-used,(datetime.fromisoformat(plan['expires_at'])-datetime.now(timezone.utc)).total_seconds())
        if seconds<=0:raise PermissionError('VALUATION_RESOURCE_EXHAUSTED')
        env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':str(SOURCE/'src'),**{k:'1' for k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS')}}
        t=time.monotonic()
        result=run_bounded_worker([sys.executable,str(Path(__file__)),'--worker',str(start),str(end)],root=SOURCE,memory_mib=2048,wall_seconds=seconds,environment=env,execution={'purpose':'TRAIN_VALUATION_INPUT_V1','start':start,'end':end},on_started=lambda pid:save(beg,{'pid':pid,'at':datetime.now(timezone.utc).isoformat()}))
        save(done,{'elapsed_seconds':time.monotonic()-t,**{k:v.decode('utf-8',errors='replace') if isinstance(v,bytes) else v for k,v in result.items()}})
        if result['returncode']:raise RuntimeError('VALUATION_FETCH_FAILED_SEE_RECEIPT:'+label)
    if probe:
        files={}
        for symbol in plan['symbols'][:4]:
            p=ROOT/'responses'/symbol/'response.json';quality=validate_response(read(p),symbol)
            if not quality['row_count']:raise ValueError('VALUATION_PROBE_EMPTY')
            files[str(p)]=sha(p)
        save(ROOT/'PROBE_ACCEPTED.json',{'files':files,'meaning':'SCHEMA_ONLY_NO_PIT_OR_STRATEGY_CLAIM','at':datetime.now(timezone.utc).isoformat()})
    else:save(ROOT/'FETCH_COMPLETED.json',{'symbols':len(plan['symbols']),'at':datetime.now(timezone.utc).isoformat(),'account_started':False})


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--probe',action='store_true');parser.add_argument('--worker',nargs=2,type=int)
    args=parser.parse_args()
    fetch(*args.worker) if args.worker else run(args.probe)
