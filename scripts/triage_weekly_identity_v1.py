"""仅核对已取得两代码原件；无信号、无收益、不修改冻结输入。"""
from collections import Counter
from datetime import datetime,timezone
from decimal import Decimal
import os
from pathlib import Path
import sys
import time

from acquire_weekly_window_v1 import ROOT,OLD,SOURCE,check,read,save,sha

OUT=ROOT/'identity-triage-v2'
CODES=['sz.300114','sz.302132']


def compare(a,b,ignore=()):
    a={r['date']:r for r in a};b={r['date']:r for r in b}
    differences=[]
    for date in sorted(set(a)&set(b)):
        fields=[]
        for k in (a[date].keys()|b[date].keys())-set(ignore):
            x,y=a[date].get(k),b[date].get(k)
            if x==y:continue
            try:same=Decimal(str(x))==Decimal(str(y))
            except ArithmeticError:same=False
            if not same:fields.append(k)
        if fields:differences.append({'date':date,'fields':sorted(fields)})
    return {'paired':len(set(a)&set(b)),'only_left':sorted(set(a)-set(b)),
        'only_right':sorted(set(b)-set(a)),'different_rows':len(differences),'differences':differences}


def worker():
    from chanlun_trader.synthetic_batch_resources import worker_resource_handshake
    if worker_resource_handshake()['execution']!={'stage':'identity-triage-v1'}:raise PermissionError('CONTEXT')
    grant=check()
    paths=[OLD/'acquisition/basic.json',OLD/'INPUT_MANIFEST.json',OLD/'WINDOW_UNIVERSE.json',
        ROOT/'quality/sz.302132.json',ROOT/'resources/prepare-window.completed.json',
        ROOT/'CALENDAR_WINDOW.json']
    for root in [ROOT,OLD]:
        for code in CODES:
            paths.extend(root/'acquisition/prices'/code/f'{flag}.json' for flag in ['3','1'])
            paths.append(root/'acquisition/actions'/f'{code}.json')
    paths.extend(ROOT/'acquisition/pool'/f'{d}.json' for d in ['2025-02-14','2025-02-17'])
    paths.append(OLD/'acquisition/pool/2025-07-03.json')
    paths.append(SOURCE/'docs/baostock-batch14-identity-resolution-v1.md')
    missing=[str(p) for p in paths if not p.exists()]
    paths=[p for p in paths if p.exists()]
    save(OUT/'ACCESS.json',{'at':datetime.now(timezone.utc).isoformat(),'reader_pid':os.getpid(),
        'reader_thread':grant['thread_id'],'recipient':'REQUESTING_USER_AND_CURRENT_RESEARCH',
        'purpose':'EXACT_TWO_CODE_PRICE_SEMANTICS_TRIAGE_NO_OUTCOME',
        'files':{str(p):sha(p) for p in paths},'missing_exact_paths':missing,'new_price_experiments':0})
    manifest=read(OLD/'INPUT_MANIFEST.json')
    expected={str(Path(p)):h for p,h in manifest['inputs'].items()}
    for p in paths:
        if 'acquisition' in p.parts:
            if sha(p)!=read(p.with_suffix('.access.json'))['sha256']:raise PermissionError('RESPONSE_HASH')
            if p.is_relative_to(OLD) and expected.get(str(p))!=sha(p):raise PermissionError('MANIFEST_HASH')
    joined={};actions={}
    for code in CODES:
        if any(not (root/'acquisition/prices'/code/f'{flag}.json').exists() for root in [ROOT,OLD] for flag in ['3','1']):continue
        for flag in ['3','1']:
            old=read(OLD/'acquisition/prices'/code/f'{flag}.json')
            new=read(ROOT/'acquisition/prices'/code/f'{flag}.json')
            if old['error_code']!='0' or new['error_code']!='0':raise ValueError('PROVIDER_ERROR')
            joined[code,flag]=[r for r in new['rows'] if r['date']<'2025-07-03']+old['rows']
        actions[code]=[r for r in read(ROOT/'acquisition/actions'/f'{code}.json')['rows'] if r['dividOperateDate']<'2025-07-03']+read(OLD/'acquisition/actions'/f'{code}.json')['rows']
    per_code={}
    for code in CODES:
        if (code,'3') not in joined:continue
        raw,hfq=joined[code,'3'],joined[code,'1']
        ranges={}
        for flag in sorted({r['adjustflag'] for r in hfq}):
            dates=[r['date'] for r in hfq if r['adjustflag']==flag]
            ranges[flag]={'count':len(dates),'first':min(dates),'last':max(dates)}
        raw_close=[{k:r[k] for k in ['date','close']} for r in raw]
        hfq_close=[{k:r[k] for k in ['date','close']} for r in hfq]
        per_code[code]={'hfq_flags':ranges,'raw_hfq_close_comparison':compare(raw_close,hfq_close),
            'adjustments':actions[code]}
    both=all((c,'1') in joined for c in CODES)
    mismatches=compare(joined[CODES[0],'1'],joined[CODES[1],'1'],['code','adjustflag']) if both else {'status':'COUNTERPART_NOT_ACQUIRED','different_rows':None}
    prices={code:{r['date']:r for r in joined[code,'1']} for code in per_code}
    samples=[]
    for d in ['2024-10-09','2025-02-14','2025-02-17','2025-07-03']:
        samples.append({'date':d,**{c:{k:prices[c][d][k] for k in ['close','adjustflag']} for c in prices if d in prices[c]},
            'raw_close':{c:next(r['close'] for r in joined[c,'3'] if r['date']==d) for c in prices}})
    quality=read(ROOT/'quality/sz.302132.json')
    result={'type':'EXACT_IDENTITY_AND_PRICE_SEMANTICS_TRIAGE_NO_OUTCOME',
        'raw_cross_code':compare(joined[CODES[0],'3'],joined[CODES[1],'3'],['code']) if both else {'status':'COUNTERPART_NOT_ACQUIRED'},
        'missing_exact_paths':missing,
        'hfq_cross_code_ignoring_labels':mismatches,'per_code':per_code,'boundary_samples':samples,
        'basic':[r for r in read(OLD/'acquisition/basic.json')['rows'] if r['code'] in CODES],
        'both_in_frozen_universe':all(c in read(OLD/'WINDOW_UNIVERSE.json')['codes'] for c in CODES),
        'pool_samples':{p.stem:[r for r in read(p)['rows'] if r['code'] in CODES] for p in paths if p.parent.name=='pool'},
        'failure_count':len(quality['failures']),'failure_reasons':dict(Counter(r['reason'] for r in quality['failures'])),
        'original_failure_preserved':True,'new_price_experiments':0,'account_started':False}
    save(OUT/'RESULT.json',result)
    print({k:result[k] for k in ['raw_cross_code','boundary_samples','basic','pool_samples','failure_count']})
    print({'hfq_cross_code_different_rows':mismatches['different_rows'],
        'per_code_flags':{c:per_code[c]['hfq_flags'] for c in per_code},'actions':actions})


if __name__=='__main__':
    if '--worker' in sys.argv:worker()
    else:
        from chanlun_trader.synthetic_batch_resources import run_bounded_worker
        grant=check(); done=ROOT/'resources/identity-triage-v2.completed.json';started=ROOT/'resources/identity-triage-v2.started.json'
        if done.exists() or started.exists():raise PermissionError('TRIAGE_NO_REPLAY')
        used=sum(read(p)['elapsed_seconds'] for p in (ROOT/'resources').glob('*.completed.json'))
        limit=min(900,21600-used,(datetime.fromisoformat(grant['expires_at'])-datetime.now(timezone.utc)).total_seconds())
        if limit<=0:raise PermissionError('RESOURCE_OR_EXPIRY')
        save(OUT/'RULE.json',{'code_sha256':sha(__file__),'codes':CODES,
            'scope':[20241009,20260731],'comparison':'All existing rows; exact decimal equivalence; no dropped dates or labels changed',
            'purpose':'DIAGNOSIS_ONLY_NO_INPUT_OR_BUDGET_MUTATION'})
        env={**os.environ,'PYTHONPATH':str(SOURCE/'src'),'PYTHONDONTWRITEBYTECODE':'1',
            **{k:'1' for k in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']}}
        then=time.monotonic()
        result=run_bounded_worker([sys.executable,str(Path(__file__)),'--worker'],root=SOURCE,memory_mib=2048,
            wall_seconds=limit,environment=env,execution={'stage':'identity-triage-v1'},
            on_started=lambda pid:save(started,{'pid':pid,'at':datetime.now(timezone.utc).isoformat()}))
        save(done,{'elapsed_seconds':time.monotonic()-then,**{k:v.decode('utf-8',errors='replace') if isinstance(v,bytes) else v for k,v in result.items()}})
        print(read(done))
