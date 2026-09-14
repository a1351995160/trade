"""固定赢家跨期日换手；原Provider/资源/Objective，价格绩效额度为0。"""
import argparse
from datetime import datetime,timezone
from importlib.metadata import version
import os
from pathlib import Path
import sys
import time

from run_baostock_account_v1 import ROOT as TRAIN_INPUT,PARENT,SOURCE,active,read,save,sha
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.mutation_boundary import ObjectiveMutationLock

INPUT=TRAIN_INPUT.parent/'weekly-low-vol-window-v1'
TRAIN=TRAIN_INPUT.parent/'train-search-batch-v35'
ROOT=TRAIN_INPUT.parent/'turnover-fixed-window-v1/turnover-input-v1'
RECEIPT=PARENT/'governance/turnover_window_input_v1/confirmation.json'
NAME='LOW_TURNOVER_WEEKLY_MOMENTUM_HOLD_20'
FIELDS='date,code,turn'
START,END='2025-07-04','2026-07-31'


def code():
    paths=[Path(__file__),SOURCE/'docs/TURNOVER_WINDOW_INPUT_V1.md',SOURCE/'scripts/run_baostock_account_v1.py',
        SOURCE/'src/chanlun_trader/data/minute/baostock_provider.py',SOURCE/'src/chanlun_trader/synthetic_batch_resources.py',
        SOURCE/'src/chanlun_trader/research_factory/common.py',SOURCE/'src/chanlun_trader/research_factory/mutation_boundary.py']
    return {str(p):sha(p) for p in paths}


def confirm():
    if RECEIPT.exists():return guard()
    from chanlun_trader.research_factory.train_search_batch_v1 import contract
    parent,expiry=active();meta=read(INPUT/'INPUT_MANIFEST.json');release=read(INPUT/'WINDOW_RELEASE.json')
    if stable_hash({'inputs':meta['inputs'],'files':meta['files'],'contract':meta['contract'],'release':release['identity']})!=meta['input_identity']:raise PermissionError('WINDOW_SOURCE_IDENTITY_CONFLICT')
    days=meta['sessions'];i=days.index(20250801)
    calendar=[int(str(day).replace('-','')) for day in read(INPUT/'CALENDAR_WINDOW.json')['sessions']]
    if calendar!=days:raise PermissionError('WINDOW_CALENDAR_MANIFEST_MISMATCH')
    if days!=sorted(set(days)) or days[i-20]!=20250704 or days[-1]!=20260731:raise PermissionError('WINDOW_TURNOVER_CALENDAR_CONFLICT')
    symbols=meta['symbols']
    if len(symbols)!=5235 or symbols!=sorted(set(symbols)):raise PermissionError('WINDOW_TURNOVER_UNIVERSE_CONFLICT')
    frozen=read(TRAIN/'PREREGISTRATION.json')['contracts'][NAME]
    if frozen!=contract(NAME) or read(TRAIN/NAME/'FEEDBACK.json')['screen_passed'] is not True:raise PermissionError('FIXED_TRAIN_WINNER_REQUIRED')
    thread=os.environ.get('CODEX_THREAD_ID')
    if not thread:raise PermissionError('ACTUAL_THREAD_REQUIRED')
    paths=[INPUT/'INPUT_MANIFEST.json',INPUT/'CALENDAR_WINDOW.json',INPUT/'WINDOW_RELEASE.json',TRAIN/'PREREGISTRATION.json',TRAIN/NAME/'FEEDBACK.json',TRAIN/NAME/'SETTLEMENT.json',TRAIN/NAME/'robustness-review-v1/SUMMARY.json']
    receipt={'version':'TURNOVER_WINDOW_INPUT_V1','parent_receipt_id':parent['receipt_id'],'objective_id':parent['plan']['objective_id'],
        'candidate':NAME,'source_candidate_hash':stable_hash(frozen),'source_input_identity':meta['input_identity'],
        'symbols':symbols,'sessions':days[i-20:],'start':START,'end':END,'evaluation_window':[20250801,20260731],
        'warmup_performance_forbidden':True,'fields':FIELDS,'frequency':'d','adjustflag':'3','provider_version':version('baostock'),
        'wall_seconds':9000,'worker_seconds':900,'memory_mib':2048,'numeric_threads':1,'concurrency':1,'performance_allowance':0,
        'expires_at':expiry.isoformat(),'code':code(),'inputs':{str(p):sha(p) for p in paths},
        'authority':{'origin':'USER_DELEGATED_FIXED_WINNER_DATA_PREPARATION_AGENT_CHOSEN_RESOURCE','thread_id':thread,
            'statement':'接着找，不要停下来，停下来的条件就是找到盈利策略','prior_scope':'我批准你，我只有一个诉求，找到能盈利的为止，不需要任何限制，直接开干'},
        'historical_publication_at':'UNKNOWN','denominator_vintage':'NOT_PIT_ATTESTED','historical_window_exposure_preserved':True,
        'canonical_budget_path':str(PARENT/'governance/search_budget_registry.json'),'canonical_budget_sha256':sha(PARENT/'governance/search_budget_registry.json'),
        'at':datetime.now(timezone.utc).isoformat()}
    receipt['receipt_id']=stable_hash(receipt)
    with ObjectiveMutationLock.for_resource(RECEIPT):save(RECEIPT,receipt)
    save(ROOT/'RECEIPT_INDEX.json',{'path':str(RECEIPT),'sha256':sha(RECEIPT)})
    files={}
    for path,digest in receipt['code'].items():
        p=Path(path);target=ROOT/'source-archive-v1'/p.relative_to(SOURCE);target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('xb') as stream:stream.write(p.read_bytes())
        files[path]={'archive':str(target),'sha256':digest}
    save(ROOT/'source-archive-v1/INDEX.json',files)
    return receipt


def guard():
    parent,expiry=active();r=read(RECEIPT)
    if r['receipt_id']!=stable_hash({k:v for k,v in r.items() if k!='receipt_id'}) or r['parent_receipt_id']!=parent['receipt_id']:raise PermissionError('WINDOW_TURNOVER_RECEIPT_CONFLICT')
    if (ROOT/'revocation.json').exists() or (RECEIPT.parent/'revocation.json').exists():raise PermissionError('WINDOW_TURNOVER_REVOKED')
    if datetime.now(timezone.utc)>=min(expiry,datetime.fromisoformat(r['expires_at'])):raise PermissionError('WINDOW_TURNOVER_EXPIRED')
    if r['code']!=code() or r['provider_version']!=version('baostock'):raise PermissionError('WINDOW_TURNOVER_CODE_CHANGED')
    for path,digest in r['inputs'].items():
        if sha(path)!=digest:raise PermissionError('WINDOW_TURNOVER_BOUND_INPUT_CHANGED')
    return r


def query(symbol):
    return {'code':symbol[-2:].lower()+'.'+symbol[:6],'fields':FIELDS,'start_date':START,'end_date':END,'frequency':'d','adjustflag':'3'}


def validate(payload,symbol,sessions):
    import math
    if payload['error_code']!='0' or payload['fields']!=FIELDS.split(',') or payload['query']!=query(symbol):raise ValueError('TURNOVER_WINDOW_RESPONSE_CONFLICT')
    observed=set();missing=0;allowed=set(sessions)
    for row in payload['rows']:
        if set(row)!=set(FIELDS.split(',')) or row['code']!=query(symbol)['code']:raise ValueError('TURNOVER_WINDOW_ROW_IDENTITY')
        day=int(datetime.strptime(row['date'],'%Y-%m-%d').strftime('%Y%m%d'))
        if day not in allowed or day in observed:raise ValueError('TURNOVER_WINDOW_DATE_OR_DUPLICATE')
        observed.add(day)
        if row['turn']=='':missing+=1;continue
        value=float(row['turn'])
        if not math.isfinite(value) or value<0:raise ValueError('TURNOVER_WINDOW_VALUE_INVALID')
    return {'rows':len(observed),'empty_turn':missing,'status':'SCHEMA_ONLY_NOT_PIT_ATTESTED'}


def fetch(start,end):
    from chanlun_trader.synthetic_batch_resources import worker_resource_handshake
    from chanlun_trader.data.minute.baostock_provider import BaoStock5MinProvider
    r=guard()
    if worker_resource_handshake()['execution']!={'purpose':'TURNOVER_WINDOW_INPUT_V1','start':start,'end':end}:raise PermissionError('TURNOVER_WINDOW_WORKER_SCOPE')
    if not 0<=start<end<=len(r['symbols']) or (start==0 and end!=4) or (start>0 and not (ROOT/'PROBE_ACCEPTED.json').exists()):raise PermissionError('TURNOVER_WINDOW_PROBE_OR_RANGE')
    provider=BaoStock5MinProvider()
    with provider.session():
        for symbol in r['symbols'][start:end]:
            guard();folder=ROOT/'responses'/symbol
            if (folder/'started.json').exists():raise PermissionError('NO_AUTOMATIC_WINDOW_READ_REPLAY')
            save(folder/'started.json',{'query':query(symbol),'reader_pid':os.getpid(),'at':datetime.now(timezone.utc).isoformat()})
            response=provider.bs.query_history_k_data_plus(**query(symbol));rows=[]
            while response.error_code=='0' and response.next():rows.append(dict(zip(response.fields,response.get_row_data())))
            payload={'query':query(symbol),'error_code':response.error_code,'error_msg':response.error_msg,'fields':response.fields,'rows':rows}
            target=folder/'response.json';save(target,payload)
            save(folder/'access.json',{'path':str(target),'sha256':sha(target),'reader_pid':os.getpid(),'recipient':'INPUT_EVALUATION_AND_REQUESTING_USER','purpose':'FIXED_WINDOW_TURNOVER_NO_PRICE_PERFORMANCE','at':datetime.now(timezone.utc).isoformat()})
            save(folder/'quality.json',validate(payload,symbol,r['sessions']))


def aligned(payload,symbol,sessions):
    import pandas as pd
    validate(payload,symbol,sessions)
    values={int(row['date'].replace('-','')):row['turn'] for row in payload['rows']}
    out=pd.DataFrame({'symbol':symbol,'timestamp':sessions})
    out['source_record_present']=out.timestamp.isin(values)
    out['turn']=pd.to_numeric(out.timestamp.map(values).replace('',None),errors='raise')
    out['missing_reason']='PRESENT'
    out.loc[~out.source_record_present,'missing_reason']='SOURCE_ROW_NOT_RETURNED'
    out.loc[out.source_record_present&out.turn.isna(),'missing_reason']='SOURCE_TURN_FIELD_EMPTY'
    return out


def materialize():
    import pandas as pd
    from chanlun_trader.synthetic_batch_resources import worker_resource_handshake
    r=guard()
    if worker_resource_handshake()['execution']!={'purpose':'TURNOVER_WINDOW_INPUT_V1','stage':'materialize'} or not (ROOT/'FETCH_COMPLETED.json').exists():raise PermissionError('TURNOVER_WINDOW_MATERIALIZE_SCOPE')
    inputs={str(RECEIPT):sha(RECEIPT),str(ROOT/'FETCH_COMPLETED.json'):sha(ROOT/'FETCH_COMPLETED.json')}
    for symbol in r['symbols']:
        folder=ROOT/'responses'/symbol;p=folder/'response.json'
        if sha(p)!=read(folder/'access.json')['sha256']:raise PermissionError('TURNOVER_WINDOW_SOURCE_HASH_CONFLICT')
        inputs.update({str(x):sha(x) for x in (p,folder/'access.json')})
    save(ROOT/'MATERIALIZATION_RULE.json',{'inputs':inputs,'all_requested_sessions_retained':True,'zero_not_missing':True,'no_filling':True,'no_price_performance':True,'timing_model':'MODELED_SESSION_1800_FOR_TURNOVER_NOT_FINANCIAL_PUBLICATION'})
    save(ROOT/'MATERIALIZATION_ACCESS.json',{'reader_pid':os.getpid(),'recipient':'INPUT_EVALUATION','purpose':'DATED_TURNOVER_ALIGNMENT','at':datetime.now(timezone.utc).isoformat(),'inputs':inputs})
    parts=[];coverage=[]
    for symbol in r['symbols']:
        frame=aligned(read(ROOT/'responses'/symbol/'response.json'),symbol,r['sessions']);parts.append(frame)
        coverage.append({'symbol':symbol,'source_rows':int(frame.source_record_present.sum()),'known_turn':int(frame.turn.notna().sum()),'missing':{str(k):int(v) for k,v in frame.missing_reason.value_counts().items()}})
    frame=pd.concat(parts,ignore_index=True)
    for c in ('symbol','missing_reason'):frame[c]=frame[c].astype('category')
    path=ROOT/'TURNOVER.parquet'
    if path.exists():raise PermissionError('NO_TURNOVER_WINDOW_OVERWRITE')
    frame.to_parquet(path,index=False)
    save(ROOT/'COVERAGE.json',{'symbols':coverage,'sessions':len(r['sessions']),'rows':len(frame),'known_turn':int(frame.turn.notna().sum()),'missing':{str(k):int(v) for k,v in frame.missing_reason.value_counts().items()}})
    payload={'receipt_id':r['receipt_id'],'files':{n:sha(ROOT/n) for n in ('TURNOVER.parquet','COVERAGE.json','MATERIALIZATION_RULE.json')},'request_window':[int(START.replace('-','')),int(END.replace('-',''))],'evaluation_window':r['evaluation_window']}
    save(ROOT/'READY.json',{**payload,'input_identity':stable_hash(payload),'status':'TURNOVER_WINDOW_INPUT_READY_NOT_QUALIFIED','historical_publication_at':'UNKNOWN','denominator_vintage':'NOT_PIT_ATTESTED','READY_FOR_REAL_TRIAL':False,'R1_FULLY_CLOSED':False,'AUTONOMOUS_STRATEGY_GOAL_COMPLETED':False})


def bounded(label,args,execution,probe=False):
    from chanlun_trader.synthetic_batch_resources import run_bounded_worker
    r=guard();beg=ROOT/'resources'/f'{label}.started.json';done=ROOT/'resources'/f'{label}.completed.json'
    if beg.exists():raise PermissionError('NO_AUTOMATIC_WINDOW_WORKER_REPLAY')
    used=sum(read(p)['elapsed_seconds'] for p in (ROOT/'resources').glob('*.completed.json'))
    seconds=min(120 if probe else 900,r['wall_seconds']-used,(datetime.fromisoformat(r['expires_at'])-datetime.now(timezone.utc)).total_seconds())
    if seconds<=0:raise PermissionError('WINDOW_TURNOVER_RESOURCE_EXHAUSTED')
    env={**os.environ,'PYTHONPATH':str(SOURCE/'src'),'PYTHONDONTWRITEBYTECODE':'1',**{k:'1' for k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS')}}
    start=time.monotonic()
    result=run_bounded_worker([sys.executable,str(Path(__file__)),*args],root=SOURCE,memory_mib=2048,wall_seconds=seconds,environment=env,execution=execution,on_started=lambda pid:save(beg,{'pid':pid,'at':datetime.now(timezone.utc).isoformat()}))
    save(done,{'elapsed_seconds':time.monotonic()-start,**{k:v.decode('utf-8',errors='replace') if isinstance(v,bytes) else v for k,v in result.items()}})
    if result['returncode']:raise RuntimeError('WINDOW_TURNOVER_WORKER_FAILED:'+label)


def run(probe=False):
    r=confirm()
    if not probe and not (ROOT/'PROBE_ACCEPTED.json').exists():raise PermissionError('WINDOW_TURNOVER_PROBE_REQUIRED')
    chunks=[(0,4)] if probe else [(i,min(i+50,len(r['symbols']))) for i in range(4,len(r['symbols']),50)]
    for start,end in chunks:bounded(f'fetch-{start}-{end}',['--fetch',str(start),str(end)],{'purpose':'TURNOVER_WINDOW_INPUT_V1','start':start,'end':end},probe)
    if probe:
        files={}
        for symbol in r['symbols'][:4]:
            path=ROOT/'responses'/symbol/'response.json'
            if not validate(read(path),symbol,r['sessions'])['rows']:raise ValueError('WINDOW_TURNOVER_PROBE_EMPTY')
            files[str(path)]=sha(path)
        save(ROOT/'PROBE_ACCEPTED.json',{'files':files,'meaning':'SCHEMA_ONLY_NOT_PIT_ATTESTED'})
    else:
        save(ROOT/'FETCH_COMPLETED.json',{'receipt_id':r['receipt_id'],'symbols':len(r['symbols']),'at':datetime.now(timezone.utc).isoformat()})
        bounded('materialize',['--materialize'],{'purpose':'TURNOVER_WINDOW_INPUT_V1','stage':'materialize'})


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--probe',action='store_true');parser.add_argument('--fetch',nargs=2,type=int);parser.add_argument('--materialize',action='store_true');args=parser.parse_args()
    if args.fetch:fetch(*args.fetch)
    elif args.materialize:materialize()
    else:run(args.probe)
