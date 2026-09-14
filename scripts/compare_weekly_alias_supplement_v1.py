"""只读已成功补件；空价格显式记录，不重发接口请求。"""
from datetime import datetime,timezone
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import sys
import time
from supplement_weekly_alias_v1 import OUT,ROOT,SOURCE,guard,read,save,sha,START,END
from triage_weekly_identity_v1 import compare


def number(value):
    try:
        n=Decimal(value)
        return n if n.is_finite() and n>0 else None
    except (InvalidOperation,TypeError):return None


def worker():
    from chanlun_trader.synthetic_batch_resources import worker_resource_handshake
    if worker_resource_handshake()['execution']!={'stage':'alias-comparison-v2'}:raise PermissionError('CONTEXT')
    grant=guard()
    files=[OUT/'price-3.json',OUT/'price-1.json',OUT/'actions.json']
    files += [ROOT/'acquisition/prices/sz.302132'/f'{flag}.json' for flag in ['3','1']]
    for p in files:
        if sha(p)!=read(p.with_suffix('.access.json'))['sha256'] or read(p)['error_code']!='0':raise PermissionError('SOURCE_IDENTITY')
    save(OUT/'COMPARISON_V2_ACCESS.json',{'reader_pid':os.getpid(),'thread_id':grant['thread_id'],
        'at':datetime.now(timezone.utc).isoformat(),'files':{str(p):sha(p) for p in files},
        'purpose':'SUPPLEMENT_IDENTITY_NO_SIGNAL_NO_OUTCOME','recipient':'REQUESTING_USER_AND_CURRENT_RESEARCH'})
    old={f:read(OUT/f'price-{f}.json')['rows'] for f in ['3','1']}
    new={f:[r for r in read(ROOT/'acquisition/prices/sz.302132'/f'{f}.json')['rows'] if START<=r['date']<=END] for f in ['3','1']}
    raw={r['date']:r for r in old['3']}; ratios=[]; missing=[]
    for row in old['1']:
        r=raw.get(row['date'],{});a,b=number(row['close']),number(r.get('close'))
        if a is None or b is None:
            missing.append({'date':row['date'],'hfq_close':row['close'],'raw_close':r.get('close'),
                'tradestatus':r.get('tradestatus'),'reason':'NO_POSITIVE_FINITE_PRICE_RATIO_UNDEFINED'})
        else:ratios.append({'date':row['date'],'hfq_over_raw':str(a/b)})
    details=[]
    for date in [START,'2025-02-14','2025-02-17',END]:
        detail={'date':date}
        for name,side in [('old',old),('new',new)]:
            for f in ['3','1']:
                row=next((r for r in side[f] if r['date']==date),None)
                detail[f'{name}_{f}']=row
        details.append(detail)
    results={'raw_before_change':compare([r for r in old['3'] if r['date']<'2025-02-17'],
        [r for r in new['3'] if r['date']<'2025-02-17'],['code']),
        'hfq_all_dates':compare(old['1'],new['1'],['code','adjustflag']),
        'old_hfq_flags':sorted({r['adjustflag'] for r in old['1']}),'old_hfq_over_raw_ratios':ratios,
        'uncomputable_preserved':missing,'boundary_rows':details,'old_action_rows':read(OUT/'actions.json')['rows'],
        'empty_actions_not_known_none':True,'new_price_experiments':0,'input_modified':False}
    save(OUT/'COMPARISON_V2.json',results)
    print(json.dumps({'raw_before_change':results['raw_before_change'],'ratios':sorted({r['hfq_over_raw'] for r in ratios}),
        'uncomputable':missing,'boundary':details[-2:]},ensure_ascii=True))


if __name__=='__main__':
    if '--worker' in sys.argv:worker()
    else:
        from chanlun_trader.synthetic_batch_resources import run_bounded_worker
        grant=guard();started=ROOT/'resources/alias-comparison-v2.started.json';done=ROOT/'resources/alias-comparison-v2.completed.json'
        if started.exists() or done.exists():raise PermissionError('NO_REPLAY')
        used=sum(read(p)['elapsed_seconds'] for p in (ROOT/'resources').glob('*.completed.json'))
        limit=min(900,21600-used,(datetime.fromisoformat(grant['expires_at'])-datetime.now(timezone.utc)).total_seconds())
        if limit<=0:raise PermissionError('RESOURCE_OR_EXPIRY')
        save(OUT/'COMPARISON_V2_RULE.json',{'script_sha256':sha(__file__),
            'rule':'Compare all authorized existing dates; empty/invalid ratios remain undefined, no relabel or source overwrite',
            'original_failure_receipt_sha256':sha(ROOT/'resources/alias-supplement-v1.completed.json')})
        env={**os.environ,'PYTHONPATH':str(SOURCE/'src'),'PYTHONIOENCODING':'utf-8','PYTHONDONTWRITEBYTECODE':'1',
            **{k:'1' for k in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']}}
        then=time.monotonic()
        result=run_bounded_worker([sys.executable,str(Path(__file__)),'--worker'],root=SOURCE,memory_mib=2048,
            wall_seconds=limit,environment=env,execution={'stage':'alias-comparison-v2'},
            on_started=lambda pid:save(started,{'pid':pid,'at':datetime.now(timezone.utc).isoformat()}))
        receipt={'elapsed_seconds':time.monotonic()-then,**{k:v.decode('utf-8',errors='replace') if isinstance(v,bytes) else v for k,v in result.items()}}
        save(done,receipt);print(json.dumps(receipt,ensure_ascii=True))
