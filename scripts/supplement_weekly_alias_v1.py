"""用户批准的单旧代码补件；不修改已冻结输入或执行账户。"""
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import sys
import time

from acquire_weekly_window_v1 import ROOT, SOURCE, check, read, save, sha, FIELDS
from chanlun_trader.research_factory.common import stable_hash

OUT=ROOT/'alias-supplement-v1'
CODE='sz.300114'
START,END='2024-10-09','2025-02-18'


def queries():
    return {**{f'price-{flag}':('query_history_k_data_plus',{'code':CODE,'fields':FIELDS[flag],
        'start_date':START,'end_date':END,'frequency':'d','adjustflag':flag}) for flag in ['3','1']},
        'actions':('query_adjust_factor',{'code':CODE,'start_date':START,'end_date':END})}


def guard():
    parent=check(); grant=read(OUT/'APPROVAL.json')
    if (grant['identity']!=stable_hash({k:v for k,v in grant.items() if k!='identity'})
        or grant['parent_release_identity']!=parent['identity']
        or grant['queries']!=json.loads(json.dumps(queries()))
        or grant['script_sha256']!=sha(__file__) or (OUT/'revocation.json').exists()):
        raise PermissionError('SUPPLEMENT_AUTHORIZATION_CHANGED')
    return grant


def worker():
    from chanlun_trader.synthetic_batch_resources import worker_resource_handshake
    from chanlun_trader.data.minute.baostock_provider import BaoStock5MinProvider
    if worker_resource_handshake()['execution']!={'stage':'alias-supplement-v1'}:raise PermissionError('CONTEXT')
    provider=BaoStock5MinProvider()
    with provider.session():
        for name,(method,query) in queries().items():
            grant=guard(); target=OUT/f'{name}.json'
            save(OUT/f'{name}.started.json',{'method':method,'query':query,'at':datetime.now(timezone.utc).isoformat(),
                'reader':grant['thread_id'],'approval_identity':grant['identity']})
            result=getattr(provider.bs,method)(**query); rows=[]
            while str(result.error_code)=='0' and result.next():rows.append(dict(zip(result.fields,result.get_row_data())))
            save(target,{'method':method,'query':query,'fields':list(result.fields),'rows':rows,
                'error_code':str(result.error_code),'error_msg':result.error_msg,'fetched_at':datetime.now(timezone.utc).isoformat()})
            save(OUT/f'{name}.access.json',{'sha256':sha(target),'rows':len(rows),'reader':grant['thread_id'],
                'recipient':'REQUESTING_USER_AND_CURRENT_RESEARCH','purpose':'SINGLE_ALIAS_PREWARM_EVIDENCE_NO_OUTCOME'})
            if str(result.error_code)!='0':raise RuntimeError(f'PROVIDER_ERROR:{name}:{result.error_code}')
            field='dividOperateDate' if name=='actions' else 'date'
            if any(r['code']!=CODE or not START<=r[field]<=END for r in rows):raise ValueError('PROVIDER_SCOPE_CONFLICT')
    guard()
    sources={str(OUT/f'{name}.json'):sha(OUT/f'{name}.json') for name in queries()}
    for flag in ['3','1']:
        p=ROOT/'acquisition/prices/sz.302132'/f'{flag}.json'
        if sha(p)!=read(p.with_suffix('.access.json'))['sha256']:raise PermissionError('EXISTING_SOURCE_CHANGED')
        sources[str(p)]=sha(p)
    save(OUT/'COMPARISON_ACCESS.json',{'reader_pid':os.getpid(),'thread_id':grant['thread_id'],
        'at':datetime.now(timezone.utc).isoformat(),'files':sources,'purpose':'RAW_AND_HFQ_ALIAS_BOUNDARY_NO_OUTCOME'})
    from triage_weekly_identity_v1 import compare
    old={flag:read(OUT/f'price-{flag}.json')['rows'] for flag in ['3','1']}
    new={flag:[r for r in read(ROOT/'acquisition/prices/sz.302132'/f'{flag}.json')['rows'] if START<=r['date']<=END] for flag in ['3','1']}
    comparisons={flag:compare(old[flag],new[flag],['code','adjustflag'] if flag=='1' else ['code']) for flag in ['3','1']}
    boundary=[]
    for day in [START,'2025-02-14','2025-02-17',END]:
        item={'date':day}
        for label,side in [('old',old),('new',new)]:
            for flag,rows in side.items():
                row=next((r for r in rows if r['date']==day),None)
                item[f'{label}_{flag}']=None if row is None else {'close':row['close'],'adjustflag':row['adjustflag']}
        boundary.append(item)
    ratios=[]
    raw={r['date']:r for r in old['3']}
    for row in old['1']:
        if row['date'] in raw and Decimal(raw[row['date']]['close'])>0:
            ratios.append({'date':row['date'],'hfq_over_raw':str(Decimal(row['close'])/Decimal(raw[row['date']]['close']))})
    save(OUT/'COMPARISON.json',{'row_counts':{flag:len(rows) for flag,rows in old.items()},
        'old_hfq_flags':sorted({r['adjustflag'] for r in old['1']}), 'cross_code_comparison':comparisons,
        'old_hfq_raw_ratios':ratios,'boundary_samples':boundary,'old_actions':read(OUT/'actions.json')['rows'],
        'original_failure_preserved':True,'input_modified':False,'new_price_experiments':0})
    print(json.dumps({'status':'SUPPLEMENT_AND_COMPARISON_COMPLETE','counts':{f:len(r) for f,r in old.items()},
        'differences':{f:c['different_rows'] for f,c in comparisons.items()}},ensure_ascii=True))


def run():
    from chanlun_trader.synthetic_batch_resources import run_bounded_worker
    from chanlun_trader.research_factory.mutation_boundary import ObjectiveMutationLock
    with ObjectiveMutationLock.for_resource(ROOT/'ACQUISITION_COMPLETED.json'):
        parent=check()
        for p in (ROOT/'resources').glob('*.started.json'):
            if not p.with_name(p.name.replace('.started.json','.completed.json')).exists():raise PermissionError('UNSETTLED_WORKER')
        value={'origin':'USER_EXPLICIT_SINGLE_CODE_SUPPLEMENT_APPROVAL','approval_statement':'补一下吧',
            'thread_id':os.environ['CODEX_THREAD_ID'],'parent_release_identity':parent['identity'],
            'queries':queries(),'at':datetime.now(timezone.utc).isoformat(),'expires_at':parent['expires_at'],
            'script_sha256':sha(__file__),'additional_exposure_budget':0,'no_automatic_account_execution':True}
        value['identity']=stable_hash(value);save(OUT/'APPROVAL.json',value)
        used=sum(read(p)['elapsed_seconds'] for p in (ROOT/'resources').glob('*.completed.json'))
        limit=min(900,21600-used,(datetime.fromisoformat(parent['expires_at'])-datetime.now(timezone.utc)).total_seconds())
        if limit<=0:raise PermissionError('DATA_BUDGET_OR_EXPIRY')
        env={**os.environ,'PYTHONPATH':str(SOURCE/'src'),'PYTHONIOENCODING':'utf-8','PYTHONDONTWRITEBYTECODE':'1',
            **{k:'1' for k in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']}}
        then=time.monotonic()
        result=run_bounded_worker([sys.executable,str(Path(__file__)),'--worker'],root=SOURCE,memory_mib=2048,
            wall_seconds=limit,environment=env,execution={'stage':'alias-supplement-v1'},
            on_started=lambda pid:save(ROOT/'resources/alias-supplement-v1.started.json',{'pid':pid,'at':datetime.now(timezone.utc).isoformat()}))
        receipt={'elapsed_seconds':time.monotonic()-then,**{k:v.decode('utf-8',errors='replace') if isinstance(v,bytes) else v for k,v in result.items()}}
        save(ROOT/'resources/alias-supplement-v1.completed.json',receipt)
        print(json.dumps(receipt,ensure_ascii=True))


if __name__=='__main__':
    worker() if '--worker' in sys.argv else run()
