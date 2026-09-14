"""已批准周低波动固定窗口：只补预热，严格重叠对照，不计算绩效。"""
import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import os
from pathlib import Path
import sys
import time

from run_baostock_account_v1 import SOURCE, ROOT as INPUT, active, read, save, sha, FIELDS
from chanlun_trader.research_factory.common import stable_hash

ROOT = INPUT.parent/'weekly-low-vol-window-v1'
OLD = INPUT.parent/'monthly-independent-window-v1'
PLAN_HASH = '3607b309c3ea969c7079b397821442dede8eec6293e72e996f62a1af225710ca'


def register():
    parent, expiry = active()
    plan = ROOT/'PLAN_PROPOSAL_V2.json'
    if sha(plan) != PLAN_HASH:
        raise PermissionError('APPROVED_PLAN_CHANGED')
    value = {'version':'WEEKLY_FIXED_WINDOW_RELEASE_V1', 'plan_sha256':PLAN_HASH,
        'parent_receipt_id':parent['receipt_id'], 'objective_id':parent['plan']['objective_id'],
        'origin':'USER_EXPLICIT_PLAN_APPROVAL_VIA_CODEX',
        'approval_statement':'批准的', 'thread_id':os.environ['CODEX_THREAD_ID'],
        'expires_at':min(expiry,datetime.fromisoformat(read(plan)['expires_at'])).isoformat(),
        'recorded_at':datetime.now(timezone.utc).isoformat(),
        'old_budgets_unchanged':True, 'account_budget_registered':False,
        'purpose':'FIXED_WEEKLY_EXTERNAL_WINDOW_INPUT_AND_ACCOUNT_NOT_QUALIFICATION'}
    value['identity'] = stable_hash(value)
    save(ROOT/'WINDOW_RELEASE.json',value)
    paths = [Path(__file__), SOURCE/'scripts/run_baostock_account_v1.py',
        SOURCE/'src/chanlun_trader/data/minute/baostock_provider.py',
        SOURCE/'src/chanlun_trader/synthetic_batch_resources.py']
    save(ROOT/'ACQUISITION_CODE.json',{str(p):sha(p) for p in paths})
    return check()


def check():
    parent, expiry = active()
    grant = read(ROOT/'WINDOW_RELEASE.json')
    if (sha(ROOT/'PLAN_PROPOSAL_V2.json') != PLAN_HASH
        or grant['plan_sha256'] != PLAN_HASH
        or grant['identity'] != stable_hash({k:v for k,v in grant.items() if k!='identity'})
        or grant['parent_receipt_id'] != parent['receipt_id']):
        raise PermissionError('WEEKLY_WINDOW_RELEASE_CONFLICT')
    if (ROOT/'revocation.json').exists() or datetime.now(timezone.utc)>=min(
            expiry,datetime.fromisoformat(grant['expires_at'])):
        raise PermissionError('WEEKLY_WINDOW_REVOKED_OR_EXPIRED')
    for path, digest in read(ROOT/'ACQUISITION_CODE.json').items():
        if sha(path)!=digest:raise PermissionError('ACQUISITION_CODE_CHANGED')
    plan=read(ROOT/'PLAN_PROPOSAL_V2.json')
    if sha(OLD/'INPUT_MANIFEST.json')!=plan['existing_input']['manifest_sha256']:
        raise PermissionError('OLD_INPUT_IDENTITY_CHANGED')
    return grant


def resolve_calendar(rows, existing):
    dates=[r['calendar_date'] for r in rows]
    if dates!=sorted(set(dates)) or not dates or dates[0]!='2024-08-01' or dates[-1]!='2025-07-31':
        raise ValueError('CALENDAR_METADATA_INCOMPLETE')
    from datetime import date
    if len(dates)!=(date(2025,7,31)-date(2024,8,1)).days+1:
        raise ValueError('CALENDAR_METADATA_GAP')
    if any(r['is_trading_day'] not in ['0','1'] for r in rows):
        raise ValueError('CALENDAR_STATUS_UNKNOWN')
    trading=[r['calendar_date'] for r in rows if r['is_trading_day']=='1']
    warmup=trading[-200:]
    if len(warmup)!=200 or [d for d in trading if d>=existing[0]] != [d for d in existing if d<'2025-08-01']:
        raise ValueError('CALENDAR_BOUNDARY_CONFLICT')
    return {'warmup_start':warmup[0],'sessions':warmup+[d for d in existing if d>='2025-08-01'],
        'missing_sessions':[d for d in warmup if d<existing[0]],
        'overlap_sessions':existing[:5], 'fetch_end':existing[4],
        'evaluation_window':['2025-08-01','2026-07-31'], 'warmup_performance_forbidden':True}


def overlap_check(old, new, days, fields):
    """只比较共同冻结日期，数值等价容许文本尾零；没有数值容差或重标度。"""
    a=[r for r in old if r['date'] in days]; b=[r for r in new if r['date'] in days]
    if len({r['date'] for r in a})!=len(a) or len({r['date'] for r in b})!=len(b):
        raise ValueError('OVERLAP_DUPLICATE_DATE')
    a={r['date']:r for r in a}; b={r['date']:r for r in b}
    if set(a)!=set(b):raise ValueError('OVERLAP_ROW_COVERAGE_CONFLICT')
    for day in a:
        for field in fields.split(','):
            x,y=a[day][field],b[day][field]
            if x==y:continue
            try:equal=Decimal(x).is_finite() and Decimal(y).is_finite() and Decimal(x)==Decimal(y)
            except InvalidOperation:equal=False
            if not equal:raise ValueError(f'OVERLAP_VALUE_CONFLICT:{day}:{field}')
    return {'status':'EXACT_NUMERIC_MATCH' if a else 'NO_ROWS_IN_EITHER_SOURCE', 'rows':len(a)}


def request(provider, name, method, query):
    grant=check()
    if method=='query_trade_dates':
        if query!={'start_date':'2024-08-01','end_date':'2025-07-31'}:raise PermissionError('CALENDAR_SCOPE')
    else:
        cal=read(ROOT/'CALENDAR_WINDOW.json')
        if method=='query_all_stock':
            if query.get('day') not in cal['missing_sessions']:raise PermissionError('POOL_SCOPE')
        elif method in ['query_history_k_data_plus','query_adjust_factor']:
            codes=read(OLD/'WINDOW_UNIVERSE.json')['codes']
            if query.get('code') not in codes or query.get('start_date')!=cal['warmup_start'] or query.get('end_date')!=cal['fetch_end']:
                raise PermissionError('WARMUP_PRICE_SCOPE')
            if method=='query_history_k_data_plus' and (query.get('adjustflag') not in FIELDS
                    or query.get('fields')!=FIELDS[query['adjustflag']] or query.get('frequency')!='d'):
                raise PermissionError('PRICE_FIELDS_SCOPE')
        else:raise PermissionError('ENDPOINT_SCOPE')
    target=ROOT/'acquisition'/f'{name}.json'
    if target.exists():
        value=read(target)
        if sha(target)!=read(target.with_suffix('.access.json'))['sha256'] or value['method']!=method or value['query']!=query or value['error_code']!='0':
            raise PermissionError('RESPONSE_NOT_REUSABLE')
        return value['rows']
    save(target.with_suffix('.started.json'),{'method':method,'query':query,'reader':grant['thread_id'],
        'release_identity':grant['identity'],'at':datetime.now(timezone.utc).isoformat()})
    result=getattr(provider.bs,method)(**query)
    rows=[]
    while str(result.error_code)=='0' and result.next():rows.append(dict(zip(result.fields,result.get_row_data())))
    save(target,{'method':method,'query':query,'rows':rows,'fields':list(result.fields),
        'error_code':str(result.error_code),'error_msg':result.error_msg,'fetched_at':datetime.now(timezone.utc).isoformat()})
    save(target.with_suffix('.access.json'),{'sha256':sha(target),'reader':grant['thread_id'],
        'recipient':'PRIVATE_RESEARCH_INPUT','purpose':'FIXED_PREWARM_NO_PERFORMANCE','rows':len(rows)})
    if str(result.error_code)!='0':raise RuntimeError(f'PROVIDER_ERROR:{result.error_code}:{result.error_msg}')
    if method in ['query_history_k_data_plus','query_adjust_factor']:
        field='date' if method=='query_history_k_data_plus' else 'dividOperateDate'
        if any(not query['start_date']<=r[field]<=query['end_date'] or r['code']!=query['code'] for r in rows):
            raise ValueError('PROVIDER_RETURNED_OUTSIDE_SCOPE')
    return rows


def worker(stage,batch):
    from chanlun_trader.synthetic_batch_resources import worker_resource_handshake
    from chanlun_trader.data.minute.baostock_provider import BaoStock5MinProvider
    if worker_resource_handshake()['execution']!={'stage':stage,'batch':batch}:raise PermissionError('WORKER_IDENTITY')
    grant=check()
    provider=BaoStock5MinProvider()
    with provider.session():
        if stage=='calendar':
            rows=request(provider,'calendar','query_trade_dates',{'start_date':'2024-08-01','end_date':'2025-07-31'})
            cal=resolve_calendar(rows,read(OLD/'CALENDAR_WINDOW.json')['sessions'])
            cal['release_identity']=grant['identity']
            cal['source_sha256']=sha(ROOT/'acquisition/calendar.json')
            save(ROOT/'CALENDAR_WINDOW.json',cal)
        elif stage=='metadata':
            for day in read(ROOT/'CALENDAR_WINDOW.json')['missing_sessions'][batch*10:(batch+1)*10]:
                request(provider,f'pool/{day}','query_all_stock',{'day':day})
        else:
            cal=read(ROOT/'CALENDAR_WINDOW.json')
            manifest=read(OLD/'INPUT_MANIFEST.json')
            inputs={str(Path(p)):h for p,h in manifest['inputs'].items()}
            codes=read(OLD/'WINDOW_UNIVERSE.json')['codes']
            expected=sorted(s[3:]+'.'+s[:2].upper() for s in codes)
            if expected!=sorted(manifest['symbols']):raise PermissionError('FIXED_UNIVERSE_CONFLICT')
            chosen=['sh.600000'] if stage=='probe' else codes[batch*50:(batch+1)*50]
            for code in chosen:
                out=ROOT/'overlap'/f'{code}.json'
                if out.exists():continue
                evidence={}; accesses={}
                for flag in ['3','1']:
                    rows=request(provider,f'prices/{code}/{flag}','query_history_k_data_plus',
                        {'code':code,'fields':FIELDS[flag],'start_date':cal['warmup_start'],
                         'end_date':cal['fetch_end'],'frequency':'d','adjustflag':flag})
                    old=OLD/'responses'/f'probe-{flag}.json' if code=='sh.600000' else OLD/'acquisition/prices'/code/f'{flag}.json'
                    if str(old) not in inputs or sha(old)!=inputs[str(old)]:raise PermissionError('ORIGINAL_RESPONSE_HASH_CONFLICT')
                    accesses[str(old)]=sha(old)
                    evidence[flag]=overlap_check(read(old)['rows'],rows,cal['overlap_sessions'],FIELDS[flag])
                request(provider,f'actions/{code}','query_adjust_factor',{'code':code,
                    'start_date':cal['warmup_start'],'end_date':cal['fetch_end']})
                save(out,{'code':code,'checks':evidence,'original_files':accesses,
                    'reader':grant['thread_id'],'purpose':'BOUNDARY_VINTAGE_CHECK_NO_PERFORMANCE'})
    print({'completed':stage,'batch':batch},flush=True)


def bounded(stage,batch):
    from chanlun_trader.synthetic_batch_resources import run_bounded_worker
    grant=check(); directory=ROOT/'resources'; label=f'{stage}-{batch}'
    completed=directory/f'{label}.completed.json'; started=directory/f'{label}.started.json'
    if completed.exists():
        if read(completed)['returncode']!=0:raise PermissionError('FAILED_WORKER_REQUIRES_DIAGNOSIS')
        return
    for p in directory.glob('*.started.json'):
        if not p.with_name(p.name.replace('.started.json','.completed.json')).exists():
            raise PermissionError('UNSETTLED_WORKER_NO_CONCURRENT_EXECUTION')
    used=sum(read(p)['elapsed_seconds'] for p in directory.glob('*.completed.json'))
    limit=min(900,21600-used,(datetime.fromisoformat(grant['expires_at'])-datetime.now(timezone.utc)).total_seconds())
    if limit<=0:raise PermissionError('DATA_RESOURCE_EXHAUSTED')
    env={**os.environ,'PYTHONPATH':str(SOURCE/'src'),'PYTHONDONTWRITEBYTECODE':'1','PYTHONUNBUFFERED':'1',
        **{k:'1' for k in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']}}
    then=time.monotonic()
    result=run_bounded_worker([sys.executable,str(Path(__file__)),'--worker',stage,'--batch',str(batch)],
        root=SOURCE,memory_mib=2048,wall_seconds=limit,environment=env,execution={'stage':stage,'batch':batch},
        on_started=lambda pid:save(started,{'pid':pid,'at':datetime.now(timezone.utc).isoformat()}))
    save(completed,{'elapsed_seconds':time.monotonic()-then,
        **{k:v.decode('utf-8',errors='replace') if isinstance(v,bytes) else v for k,v in result.items()}})
    print({'stage':stage,'batch':batch,'returncode':result['returncode']},flush=True)
    if result['returncode']:raise RuntimeError('WORKER_FAILED_SEE_RECEIPT')


def run():
    from chanlun_trader.research_factory.mutation_boundary import ObjectiveMutationLock
    with ObjectiveMutationLock.for_resource(ROOT/'ACQUISITION_COMPLETED.json'):
        bounded('calendar',0)
        bounded('probe',0)
        for batch in range((len(read(ROOT/'CALENDAR_WINDOW.json')['missing_sessions'])+9)//10):bounded('metadata',batch)
        for batch in range((len(read(OLD/'WINDOW_UNIVERSE.json')['codes'])+49)//50):bounded('prices',batch)
        save(ROOT/'ACQUISITION_COMPLETED.json',{'status':'RESPONSES_AND_OVERLAP_COMPLETE_NOT_ACCOUNT_READY',
            'at':datetime.now(timezone.utc).isoformat(),'price_experiments':0})


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--register',action='store_true')
    parser.add_argument('--worker',choices=['calendar','probe','metadata','prices'])
    parser.add_argument('--batch',type=int,default=0)
    args=parser.parse_args()
    if args.register:print(register())
    elif args.worker:worker(args.worker,args.batch)
    else:run()
