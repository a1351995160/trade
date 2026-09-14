"""一次明确对账后的固定字段续取；不改原失败、90分钟回执或行情数据。"""
import argparse
from datetime import datetime,timezone
import os
from pathlib import Path
import sys
import time

import prepare_train_valuation_input_v1 as source
from run_baostock_account_v1 import read,save,sha,SOURCE
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.mutation_boundary import ObjectiveMutationLock

ROOT=source.ROOT/'recovery-v1'
RECEIPT=source.RECEIPT.parent/'recovery-v1.json'


def query(symbol):
    return {'code':symbol[-2:].lower()+'.'+symbol[:6],'fields':source.FIELDS,'start_date':source.START,'end_date':source.END,'frequency':'d','adjustflag':'3'}


def reconcile():
    parent=source.guard();evidence={str(source.RECEIPT):sha(source.RECEIPT)};used=0;failed=[]
    if (source.ROOT/'FETCH_COMPLETED.json').exists():raise PermissionError('FETCH_ALREADY_COMPLETE')
    for p in (source.ROOT/'resources').glob('*.started.json'):
        done=p.with_name(p.name.replace('.started.','.completed.'))
        if not done.exists():raise PermissionError('OLD_WORKER_UNSETTLED')
        evidence[str(p)]=sha(p);evidence[str(done)]=sha(done)
        item=read(done);used+=item['elapsed_seconds']
        if item['returncode']!=0:failed.append(str(done))
    if not failed and used<5400:raise PermissionError('NO_FAILED_OR_EXHAUSTED_FETCH_TO_RECOVER')
    remaining=[];interrupted=[]
    for symbol in parent['plan']['symbols']:
        folder=source.ROOT/'responses'/symbol
        paths={k:folder/f'{k}.json' for k in ('started','response','access','quality')}
        if paths['quality'].exists():
            if not all(p.exists() for p in paths.values()):raise PermissionError('RECOVERY_COMPLETE_RECORD_INCONSISTENT')
            if read(paths['started'])['query']!=query(symbol) or sha(paths['response'])!=read(paths['access'])['sha256']:raise PermissionError('RECOVERY_SOURCE_CONFLICT')
            if source.validate_response(read(paths['response']),symbol)!=read(paths['quality']):raise PermissionError('RECOVERY_QUALITY_CONFLICT')
            evidence.update({str(p):sha(p) for p in paths.values()})
            continue
        if paths['response'].exists() or paths['access'].exists():raise PermissionError('PARTIAL_SAVED_RESPONSE_REQUIRES_EXACT_TRIAGE')
        remaining.append(symbol)
        if paths['started'].exists():
            if read(paths['started'])['query']!=query(symbol):raise PermissionError('RECOVERY_QUERY_CONFLICT')
            interrupted.append(symbol);evidence[str(paths['started'])]=sha(paths['started'])
    if not remaining or len(interrupted)>1:raise PermissionError('RECOVERY_FIXED_SCOPE_CONFLICT')
    return parent,{'evidence':evidence,'old_used_seconds':used,'old_failures':failed,'remaining':remaining,'interrupted':interrupted}


def confirm():
    if RECEIPT.exists():return guard()
    parent,state=reconcile()
    thread=os.environ.get('CODEX_THREAD_ID')
    if not thread:raise PermissionError('ACTUAL_THREAD_REQUIRED')
    receipt={'version':'TRAIN_VALUATION_INPUT_RECOVERY_V1','source_receipt_id':parent['receipt_id'],
        'objective_id':parent['plan']['objective_id'],'expires_at':parent['plan']['expires_at'],
        'authority':{'origin':'USER_CONTINUING_RESEARCH_DELEGATION_AGENT_RESOURCE_AMENDMENT','thread_id':thread,
            'statement':'接着找，不要停下来，停下来的条件就是找到盈利策略','prior_scope':'我批准你，我只有一个诉求，找到能盈利的为止，不需要任何限制，直接开干'},
        'additional_data_seconds':3600,'worker_seconds':900,'memory_mib':2048,'concurrency':1,'numeric_threads':1,
        'performance_allowance':0,'retries_per_interrupted_read':1,'state':state,
        'code':{str(Path(__file__)):sha(Path(__file__)),str(SOURCE/'docs/TRAIN_VALUATION_RECOVERY_V1.md'):sha(SOURCE/'docs/TRAIN_VALUATION_RECOVERY_V1.md')},
        'at':datetime.now(timezone.utc).isoformat()}
    receipt['receipt_id']=stable_hash(receipt)
    with ObjectiveMutationLock.for_resource(RECEIPT):save(RECEIPT,receipt)
    save(ROOT/'RECEIPT_INDEX.json',{'path':str(RECEIPT),'sha256':sha(RECEIPT)})
    for path in receipt['code']:
        out=ROOT/'source-archive-v1'/Path(path).name;out.parent.mkdir(parents=True,exist_ok=True)
        with out.open('xb') as stream:stream.write(Path(path).read_bytes())
    return receipt


def guard():
    parent=source.guard();r=read(RECEIPT)
    if r['receipt_id']!=stable_hash({k:v for k,v in r.items() if k!='receipt_id'}) or r['source_receipt_id']!=parent['receipt_id']:raise PermissionError('RECOVERY_RECEIPT_CONFLICT')
    if (ROOT/'revocation.json').exists() or (RECEIPT.parent/'recovery-v1.revocation.json').exists():raise PermissionError('RECOVERY_REVOKED')
    if datetime.now(timezone.utc)>=datetime.fromisoformat(r['expires_at']):raise PermissionError('RECOVERY_EXPIRED')
    for path,digest in {**r['code'],**r['state']['evidence']}.items():
        if sha(path)!=digest:raise PermissionError('RECOVERY_BOUND_IDENTITY_CHANGED')
    return r


def interrupted_worker(symbol):
    from chanlun_trader.synthetic_batch_resources import worker_resource_handshake
    from chanlun_trader.data.minute.baostock_provider import BaoStock5MinProvider
    r=guard()
    if symbol not in r['state']['interrupted'] or worker_resource_handshake()['execution']!={'purpose':'VALUATION_INTERRUPTED_READ_ONCE','symbol':symbol}:raise PermissionError('RECOVERY_WORKER_SCOPE_CONFLICT')
    folder=source.ROOT/'responses'/symbol
    if any((folder/f'{x}.json').exists() for x in ('response','access','quality')):raise PermissionError('RECOVERY_WOULD_OVERWRITE_RESPONSE')
    attempt=ROOT/f'{symbol}.attempt.json'
    if attempt.exists():raise PermissionError('INTERRUPTED_READ_ALREADY_RETRIED')
    save(attempt,{'query':query(symbol),'reader_pid':os.getpid(),'receipt_id':r['receipt_id'],'at':datetime.now(timezone.utc).isoformat()})
    provider=BaoStock5MinProvider()
    with provider.session():
        response=provider.bs.query_history_k_data_plus(**query(symbol));rows=[]
        while response.error_code=='0' and response.next():rows.append(dict(zip(response.fields,response.get_row_data())))
        payload={'query':query(symbol),'fields':response.fields,'rows':rows,'error_code':response.error_code,'error_msg':response.error_msg}
        target=folder/'response.json';save(target,payload)
        save(folder/'access.json',{'path':str(target),'sha256':sha(target),'reader_pid':os.getpid(),'recipient':'REQUESTING_USER_AND_INPUT_EVALUATION','purpose':'EXPLICIT_RECONCILED_READ_RECOVERY_NO_PERFORMANCE','recovery_receipt_id':r['receipt_id'],'at':datetime.now(timezone.utc).isoformat()})
        save(folder/'quality.json',source.validate_response(payload,symbol))


def remaining_seconds():
    r=guard()
    used=sum(read(p)['elapsed_seconds'] for p in (ROOT/'resources').glob('*.completed.json'))
    return min(900,r['additional_data_seconds']-used,(datetime.fromisoformat(r['expires_at'])-datetime.now(timezone.utc)).total_seconds())


def run():
    from chanlun_trader.synthetic_batch_resources import run_bounded_worker
    r=confirm();plan=source.guard()['plan'];indices={s:i for i,s in enumerate(plan['symbols'])}
    jobs=[(f'interrupted-{s}',[str(Path(__file__)),'--interrupted',s],{'purpose':'VALUATION_INTERRUPTED_READ_ONCE','symbol':s}) for s in r['state']['interrupted']]
    fresh=[indices[s] for s in r['state']['remaining'] if s not in r['state']['interrupted']]
    for offset in range(0,len(fresh),50):
        chunk=fresh[offset:offset+50]
        if chunk!=list(range(chunk[0],chunk[-1]+1)):raise PermissionError('NONCONTIGUOUS_FRESH_RANGE')
        start,end=chunk[0],chunk[-1]+1
        jobs.append((f'fetch-{start}-{end}',[str(Path(source.__file__)),'--worker',str(start),str(end)],{'purpose':'TRAIN_VALUATION_INPUT_V1','start':start,'end':end}))
    for label,args,execution in jobs:
        guard();beg=ROOT/'resources'/f'{label}.started.json';done=ROOT/'resources'/f'{label}.completed.json'
        if beg.exists():raise PermissionError('NO_AUTOMATIC_RECOVERY_REPLAY')
        seconds=remaining_seconds()
        if seconds<=0:raise PermissionError('RECOVERY_DATA_RESOURCE_EXHAUSTED')
        env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':str(SOURCE/'src'),**{k:'1' for k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS')}}
        started=time.monotonic()
        result=run_bounded_worker([sys.executable,*args],root=SOURCE,memory_mib=2048,wall_seconds=seconds,environment=env,execution=execution,on_started=lambda pid:save(beg,{'pid':pid,'at':datetime.now(timezone.utc).isoformat()}))
        save(done,{'elapsed_seconds':time.monotonic()-started,**{k:v.decode('utf-8',errors='replace') if isinstance(v,bytes) else v for k,v in result.items()}})
        if result['returncode']:raise RuntimeError('RECOVERY_WORKER_FAILED:'+label)
    guard()
    for symbol in plan['symbols']:
        folder=source.ROOT/'responses'/symbol
        if sha(folder/'response.json')!=read(folder/'access.json')['sha256'] or source.validate_response(read(folder/'response.json'),symbol)!=read(folder/'quality.json'):raise PermissionError('RECOVERY_FINAL_IDENTITY_CONFLICT')
    save(ROOT/'COMPLETED.json',{'symbols':len(plan['symbols']),'at':datetime.now(timezone.utc).isoformat(),'source_receipt_id':r['source_receipt_id'],'recovery_receipt_id':r['receipt_id']})
    save(source.ROOT/'FETCH_COMPLETED.json',{'symbols':len(plan['symbols']),'at':datetime.now(timezone.utc).isoformat(),'account_started':False,'completion_producer':'EXPLICIT_RECOVERY_V1','recovery_receipt_id':r['receipt_id'],'recovery_completion_sha256':sha(ROOT/'COMPLETED.json')})


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--interrupted');args=parser.parse_args()
    interrupted_worker(args.interrupted) if args.interrupted else run()
