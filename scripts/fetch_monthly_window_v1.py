"""一次固定外窗的数据准备；原Provider、只增证据、资源回执可恢复。"""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import time

from acquire_monthly_independent_v1 import ROOT, SOURCE, guard, read, save, sha
from run_baostock_account_v1 import FIELDS
from chanlun_trader.research_factory.common import stable_hash
from monthly_window_resource_extension_v1 import limit as data_limit


def approved():
    parent=guard()
    path=ROOT/'WINDOW_GATE_APPROVAL.json'
    if not path.exists():
        proposal=SOURCE/'docs/MONTHLY_WINDOW_EXECUTION_DECISION_V1.md'
        value={'origin':'USER_EXPLICIT_PLAN_APPROVAL_VIA_CODEX',
            'approval_statement':'批准','thread_id':os.environ['CODEX_THREAD_ID'],
            'release_identity':parent['identity'],'thresholds':{'closed_paths':30,'entry_dates':10,'symbols':2},
            'proposal_sha256':sha(proposal),'expires_at':parent['expires_at'],
            'recorded_at':datetime.now(timezone.utc).isoformat()}
        value['identity']=stable_hash(value)
        save(path,value)
    value=read(path)
    if (value['identity']!=stable_hash({k:v for k,v in value.items() if k!='identity'})
        or value['release_identity']!=parent['identity']
        or value['thresholds']!={'closed_paths':30,'entry_dates':10,'symbols':2}):
        raise PermissionError('WINDOW_GATE_APPROVAL_CHANGED')
    return parent


def check():
    grant=approved()
    expected=read(ROOT/'FETCH_CODE_V4.json')
    for path,digest in expected.items():
        if sha(path)!=digest:
            raise PermissionError('FETCH_SOURCE_CHANGED')
    return grant


def fetch(provider, name, method, query):
    grant=check()
    target=ROOT/'acquisition'/f'{name}.json'
    if target.exists():
        access=read(target.with_suffix('.access.json'))
        value=read(target)
        if sha(target)!=access['sha256'] or value['query']!=query or value['method']!=method or value['error_code']!='0':
            raise PermissionError('EXISTING_RESPONSE_NOT_REUSABLE')
        return value['rows']
    started=target.with_suffix('.started.json')
    if started.exists():
        raise PermissionError('UNSETTLED_QUERY_NO_AUTOMATIC_RETRY')
    calendar=read(ROOT/'CALENDAR_WINDOW.json')
    if method=='query_all_stock':
        if query['day'] not in calendar['sessions']:
            raise PermissionError('MEMBERSHIP_DATE_OUTSIDE_WINDOW')
    elif method in ['query_history_k_data_plus','query_adjust_factor']:
        if query['start_date']!=calendar['warmup_start'] or query['end_date']!='2026-07-31':
            raise PermissionError('PRICE_QUERY_OUTSIDE_WINDOW')
    elif method!='query_stock_basic':
        raise PermissionError('ENDPOINT_NOT_APPROVED')
    save(started,{'method':method,'query':query,'reader':grant['thread_id'],
                  'at':datetime.now(timezone.utc).isoformat(),'release_identity':grant['identity']})
    response=getattr(provider.bs,method)(**query)
    rows=[]
    while str(response.error_code)=='0' and response.next():
        rows.append(dict(zip(response.fields,response.get_row_data())))
    save(target,{'method':method,'query':query,'rows':rows,'fields':list(response.fields),
        'error_code':str(response.error_code),'error_msg':response.error_msg,
        'fetched_at':datetime.now(timezone.utc).isoformat()})
    save(target.with_suffix('.access.json'),{'sha256':sha(target),'row_count':len(rows),
        'reader':grant['thread_id'],'recipient':'PRIVATE_EVALUATION','purpose':'FIXED_WINDOW_INPUT_ONLY'})
    if str(response.error_code)!='0':
        raise RuntimeError(f'PROVIDER_ERROR {name}: {response.error_code} {response.error_msg}')
    return rows


def worker(stage, batch):
    from chanlun_trader.synthetic_batch_resources import worker_resource_handshake
    from chanlun_trader.data.minute.baostock_provider import BaoStock5MinProvider
    if worker_resource_handshake()['execution']!={'stage':stage,'batch':batch}:
        raise PermissionError('WORKER_IDENTITY_CHANGED')
    check()
    window=read(ROOT/'CALENDAR_WINDOW.json')
    provider=BaoStock5MinProvider()
    with provider.session():
        if stage=='metadata':
            basic=fetch(provider,'basic','query_stock_basic',{})
            # 只冻结本接口明确标为股票的身份，不用当前status替代历史状态。
            stocks={row['code']:row for row in basic if row['type']=='1'}
            observed=set()
            for day in window['sessions'][batch*10:(batch+1)*10]:
                members=fetch(provider,f'pool/{day}','query_all_stock',{'day':day})
                observed.update(row['code'] for row in members)
            if batch!=(len(window['sessions'])-1)//10:
                print({'metadata_batch':batch,'sessions_completed':min(10,len(window['sessions'])-batch*10)})
                return
            for day in window['sessions']:
                observed.update(row['code'] for row in read(ROOT/'acquisition/pool'/f'{day}.json')['rows'])
            unknown=sorted(code for code in observed if code not in {r['code'] for r in basic})
            if unknown:
                save(ROOT/'UNKNOWN_POOL_IDENTITIES.json',{'codes':unknown})
                raise ValueError('HISTORICAL_POOL_IDENTITIES_NOT_IN_BASIC')
            symbols=sorted(observed.intersection(stocks))
            save(ROOT/'WINDOW_UNIVERSE.json',{'codes':symbols,'count':len(symbols),
                'rule':'UNION_OF_DAILY_HISTORICAL_POOL_INTERSECT_PROVIDER_STOCK_TYPE_1',
                'current_status_not_used':True,'basic_sha256':sha(ROOT/'acquisition/basic.json'),
                'pool_hashes':{d:sha(ROOT/'acquisition/pool'/f'{d}.json') for d in window['sessions']}})
            print({'universe_count':len(symbols)})
        else:
            codes=read(ROOT/'WINDOW_UNIVERSE.json')['codes'][batch*50:(batch+1)*50]
            for code in codes:
                for flag in ['3','1']:
                    query={'code':code,'fields':FIELDS[flag],'start_date':window['warmup_start'],
                           'end_date':'2026-07-31','frequency':'d','adjustflag':flag}
                    if code=='sh.600000':
                        original=ROOT/'responses'/f'probe-{flag}.json'
                        if sha(original)!=read(original.with_suffix('.access.json'))['sha256'] or read(original)['query']!=query:
                            raise PermissionError('PROBE_IDENTITY_CHANGED')
                    else:
                        fetch(provider,f'prices/{code}/{flag}','query_history_k_data_plus',query)
                fetch(provider,f'actions/{code}','query_adjust_factor',{'code':code,
                    'start_date':window['warmup_start'],'end_date':'2026-07-31'})
            print({'batch':batch,'symbols_completed':len(codes)})


def bounded(stage,batch):
    from chanlun_trader.synthetic_batch_resources import run_bounded_worker
    grant=check()
    prior=ROOT/'resources'/f'{stage}-v3-{batch}.completed.json'
    if prior.exists():
        if read(prior)['returncode']==0:
            return
        recovery=read(ROOT/'FETCH_V4_HANDOFF.json')
        if recovery['failed_workers'].get(str(prior))!=sha(prior):
            raise PermissionError('PREVIOUS_V3_FAILURE_NOT_RECONCILED')
    previous=ROOT/'resources'/f'{stage}-v2-{batch}.completed.json'
    if previous.exists():
        if read(previous)['returncode']==0:
            return
        handoff=read(ROOT/'FETCH_V3_HANDOFF.json')
        if handoff['failed_workers'].get(str(previous))!=sha(previous):
            raise PermissionError('PREVIOUS_FAILURE_NOT_RECONCILED')
    label=f'{stage}-v4-{batch}'
    started=ROOT/'resources'/f'{label}.started.json'
    completed=ROOT/'resources'/f'{label}.completed.json'
    if completed.exists():
        if read(completed)['returncode']!=0:
            raise PermissionError('FAILED_WORKER_REQUIRES_RECONCILIATION')
        return
    if started.exists():
        raise PermissionError('UNSETTLED_WORKER_REQUIRES_RECONCILIATION')
    used=sum(read(p)['elapsed_seconds'] for p in (ROOT/'resources').glob('*.completed.json'))
    limit=min(900,data_limit()-used,(datetime.fromisoformat(grant['expires_at'])-datetime.now(timezone.utc)).total_seconds())
    if limit<=0:
        raise PermissionError('INPUT_RESOURCE_EXHAUSTED')
    env={**os.environ,'PYTHONPATH':str(SOURCE/'src'),'PYTHONDONTWRITEBYTECODE':'1','PYTHONUNBUFFERED':'1',
         **{k:'1' for k in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']}}
    then=time.monotonic()
    result=run_bounded_worker([sys.executable,str(Path(__file__)),'--worker',stage,'--batch',str(batch)],
        root=SOURCE,memory_mib=2048,wall_seconds=limit,environment=env,execution={'stage':stage,'batch':batch},
        on_started=lambda pid:save(started,{'pid':pid,'at':datetime.now(timezone.utc).isoformat()}))
    save(completed,{'elapsed_seconds':time.monotonic()-then,
        **{k:v.decode('utf-8',errors='replace') if isinstance(v,bytes) else v for k,v in result.items()}})
    print({'stage':stage,'batch':batch,'returncode':result['returncode']},flush=True)
    if result['returncode']:
        raise RuntimeError('ACQUISITION_WORKER_FAILED_SEE_RECEIPT')


def run(metadata_only=False):
    approved()
    data_limit()
    save(ROOT/'FETCH_CODE_V4.json',{str(p):sha(p) for p in [Path(__file__),SOURCE/'scripts/acquire_monthly_independent_v1.py',
        SOURCE/'scripts/monthly_window_resource_extension_v1.py', ROOT/'FETCH_V4_HANDOFF.json']})
    for batch in range((len(read(ROOT/'CALENDAR_WINDOW.json')['sessions'])+9)//10):
        bounded('metadata',batch)
    if not metadata_only:
        for batch in range((read(ROOT/'WINDOW_UNIVERSE.json')['count']+49)//50):
            bounded('prices',batch)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--worker',choices=['metadata','prices'])
    parser.add_argument('--batch',type=int,default=0)
    parser.add_argument('--metadata-only',action='store_true')
    args=parser.parse_args()
    worker(args.worker,args.batch) if args.worker else run(args.metadata_only)
