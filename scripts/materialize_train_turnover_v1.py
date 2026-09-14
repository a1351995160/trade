"""固定已取得记录到日历对齐换手快照；无价格表现计算。"""
import argparse
from datetime import datetime,timezone
import os
from pathlib import Path
import sys
import time

import prepare_train_valuation_input_v1 as source
from run_baostock_account_v1 import BASE,read,save,sha,SOURCE,ROOT as INPUT
from chanlun_trader.research_factory.common import stable_hash

ROOT=source.ROOT/'turnover-input-v1'
CALENDAR=BASE/'materialized-v3/CALENDAR.json'


def freeze():
    receipt=source.guard()
    if not (source.ROOT/'FETCH_COMPLETED.json').exists():raise PermissionError('VALUATION_FETCH_NOT_COMPLETED')
    manifest=read(INPUT/'INPUT_MANIFEST.json')
    if manifest['historical'][str(CALENDAR)]!=sha(CALENDAR):raise PermissionError('TURNOVER_CALENDAR_CHANGED')
    inputs={str(p):sha(p) for p in (source.RECEIPT,source.ROOT/'FETCH_COMPLETED.json',CALENDAR,INPUT/'INPUT_MANIFEST.json')}
    recovery=read(source.ROOT/'FETCH_COMPLETED.json').get('recovery_receipt_id')
    if recovery:
        import resume_train_valuation_input_v1 as resume
        if resume.guard()['receipt_id']!=recovery:raise PermissionError('TURNOVER_RECOVERY_IDENTITY_CONFLICT')
        inputs.update({str(p):sha(p) for p in (resume.RECEIPT,resume.ROOT/'COMPLETED.json')})
    for symbol in receipt['plan']['symbols']:
        folder=source.ROOT/'responses'/symbol;p=folder/'response.json'
        if sha(p)!=read(folder/'access.json')['sha256']:raise PermissionError('TURNOVER_SOURCE_HASH_CONFLICT:'+symbol)
        inputs[str(p)]=sha(p);inputs[str(folder/'access.json')]=sha(folder/'access.json')
    prereg={'schema':'TRAIN_TURNOVER_INPUT_V1','source_receipt_id':receipt['receipt_id'],
        'inputs':inputs,'code_sha256':sha(Path(__file__)),'retained_fields':['symbol','timestamp','turn','source_record_present','missing_reason'],
        'financial_fields_eligible':False,'time_model':'MODELED_SESSION_1800_FOR_TURNOVER_NOT_FINANCIAL_PUBLICATION',
        'historical_publication_at':'UNKNOWN','denominator_vintage':'NOT_PIT_ATTESTED',
        'qualification':'NOT_FOR_QUALIFICATION','new_price_experiments':0}
    save(ROOT/'PREREGISTRATION.json',prereg)
    archive=ROOT/'source-archive-v1/materialize_train_turnover_v1.py'
    archive.parent.mkdir(parents=True,exist_ok=True)
    if archive.exists():
        if sha(archive)!=prereg['code_sha256']:raise PermissionError('TURNOVER_SOURCE_ARCHIVE_CONFLICT')
    else:
        with archive.open('xb') as stream:stream.write(Path(__file__).read_bytes())
    return prereg


def guard():
    source.guard();p=read(ROOT/'PREREGISTRATION.json')
    if read(source.ROOT/'FETCH_COMPLETED.json').get('recovery_receipt_id'):
        import resume_train_valuation_input_v1 as resume
        resume.guard()
    if sha(Path(__file__))!=p['code_sha256']:raise PermissionError('TURNOVER_MATERIALIZER_CHANGED')
    for path,digest in p['inputs'].items():
        if sha(path)!=digest:raise PermissionError('TURNOVER_BOUND_SOURCE_CHANGED')
    return p


def aligned(payload,symbol,sessions):
    import pandas as pd
    source.validate_response(payload,symbol)
    rows=payload['rows']
    raw={int(row['date'].replace('-','')):row['turn'] for row in rows}
    if not set(raw)<=set(sessions):raise ValueError('TURNOVER_NON_SESSION_ROW')
    frame=pd.DataFrame({'symbol':symbol,'timestamp':sessions})
    frame['source_record_present']=frame.timestamp.isin(raw)
    frame['turn']=pd.to_numeric(frame.timestamp.map(raw).replace('',None),errors='raise')
    frame['missing_reason']='PRESENT'
    frame.loc[~frame.source_record_present,'missing_reason']='SOURCE_ROW_NOT_RETURNED'
    frame.loc[frame.source_record_present&frame.turn.isna(),'missing_reason']='SOURCE_TURN_FIELD_EMPTY'
    return frame


def worker():
    import pandas as pd
    from chanlun_trader.synthetic_batch_resources import worker_resource_handshake
    if worker_resource_handshake()['execution']!={'purpose':'MATERIALIZE_TRAIN_TURNOVER_V1','code_sha256':sha(Path(__file__))}:raise PermissionError('TURNOVER_WORKER_CONTEXT_CHANGED')
    freeze();p=guard();receipt=source.guard();sessions=read(CALENDAR)['sessions']
    save(ROOT/'ACCESS.json',{'reader_pid':os.getpid(),'recipient':'INPUT_EVALUATION','purpose':'CALENDAR_ALIGNED_TURNOVER_INPUT_NO_OUTCOME','inputs':p['inputs'],'at':datetime.now(timezone.utc).isoformat()})
    parts=[];coverage=[]
    for symbol in receipt['plan']['symbols']:
        path=source.ROOT/'responses'/symbol/'response.json'
        part=aligned(read(path),symbol,sessions)
        coverage.append({'symbol':symbol,'source_rows':int(part.source_record_present.sum()),'known_turn':int(part.turn.notna().sum()),'missing_reasons':{str(k):int(v) for k,v in part.missing_reason.value_counts().items()}})
        parts.append(part)
    frame=pd.concat(parts,ignore_index=True)
    frame['symbol']=frame.symbol.astype('category');frame['missing_reason']=frame.missing_reason.astype('category')
    path=ROOT/'TURNOVER.parquet'
    if path.exists():raise FileExistsError('TURNOVER_OUTPUT_ALREADY_EXISTS')
    frame.to_parquet(path,index=False)
    save(ROOT/'COVERAGE.json',{'symbols':coverage,'calendar_sessions':len(sessions),'total_rows':len(frame),'known_turn_rows':int(frame.turn.notna().sum()),'missing_reasons':{str(k):int(v) for k,v in frame.missing_reason.value_counts().items()}})
    output={'path':str(path),'sha256':sha(path),'coverage_sha256':sha(ROOT/'COVERAGE.json')}
    identity=stable_hash({'source_receipt':receipt['receipt_id'],'preregistration_sha256':sha(ROOT/'PREREGISTRATION.json'),'output':output})
    save(ROOT/'READY.json',{'status':'TURNOVER_DATED_HISTORY_SCHEMA_READY_NOT_QUALIFIED','input_identity':identity,'output':output,
        'timing_model':p['time_model'],'historical_publication_at':'UNKNOWN','denominator_vintage':'NOT_PIT_ATTESTED','financial_fields_eligible':False,
        'STRICT_TRAIN_INPUT_READY':False,'READY_FOR_REAL_TRIAL':False,'R1_FULLY_CLOSED':False,'AUTONOMOUS_STRATEGY_GOAL_COMPLETED':False})


def run():
    from chanlun_trader.synthetic_batch_resources import run_bounded_worker
    plan=source.guard()['plan']
    if not (source.ROOT/'FETCH_COMPLETED.json').exists():raise PermissionError('VALUATION_FETCH_NOT_COMPLETED')
    resource_root=source.ROOT/'resources'
    recovery=read(source.ROOT/'FETCH_COMPLETED.json').get('recovery_receipt_id')
    if recovery:
        import resume_train_valuation_input_v1 as resume
        if resume.guard()['receipt_id']!=recovery:raise PermissionError('TURNOVER_RECOVERY_IDENTITY_CONFLICT')
        resource_root=resume.ROOT/'resources'
    began=resource_root/'materialize-turnover.started.json';done=resource_root/'materialize-turnover.completed.json'
    if began.exists():raise PermissionError('NO_AUTOMATIC_TURNOVER_MATERIALIZATION_REPLAY')
    used=sum(read(p)['elapsed_seconds'] for p in (source.ROOT/'resources').glob('*.completed.json'))
    seconds=min(900,5400-used,(datetime.fromisoformat(plan['expires_at'])-datetime.now(timezone.utc)).total_seconds())
    if recovery:seconds=resume.remaining_seconds()
    if seconds<=0:raise PermissionError('TURNOVER_DATA_RESOURCE_EXHAUSTED')
    env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':str(SOURCE/'src'),**{k:'1' for k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS')}}
    start=time.monotonic()
    result=run_bounded_worker([sys.executable,str(Path(__file__)),'--worker'],root=SOURCE,memory_mib=2048,wall_seconds=seconds,environment=env,execution={'purpose':'MATERIALIZE_TRAIN_TURNOVER_V1','code_sha256':sha(Path(__file__))},on_started=lambda pid:save(began,{'pid':pid,'at':datetime.now(timezone.utc).isoformat()}))
    save(done,{'elapsed_seconds':time.monotonic()-start,**{k:v.decode('utf-8',errors='replace') if isinstance(v,bytes) else v for k,v in result.items()}})
    if result['returncode']:raise RuntimeError('TURNOVER_MATERIALIZATION_FAILED_SEE_RECEIPT')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--worker',action='store_true');args=parser.parse_args()
    worker() if args.worker else run()
