"""用户明确释放的固定月末候选外窗；先核验接口覆盖，不执行账户。"""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import time

from run_baostock_account_v1 import SOURCE, ROOT as INPUT, active, read, save, sha
from chanlun_trader.research_factory.common import stable_hash

ROOT = INPUT.parent / 'monthly-independent-window-v1'
CANDIDATE = 'MONTHLY_REVERSAL_HOLD_20'
START, END = '2025-08-01', '2026-07-31'


def validate_release(value, parent, now, revoked=False):
    if value['identity'] != stable_hash({k:v for k,v in value.items() if k != 'identity'}):
        raise PermissionError('WINDOW_RELEASE_IDENTITY_CHANGED')
    if (value['candidate'] != CANDIDATE or value['start'] != START or value['end'] != END
            or value['warmup_sessions'] != 21 or value['parent_receipt_id'] != parent['receipt_id']
            or value['data_seconds'] != 10800 or value['account_seconds'] != 5400):
        raise PermissionError('WINDOW_RELEASE_SCOPE_CHANGED')
    if revoked or now >= datetime.fromisoformat(value['expires_at']):
        raise PermissionError('WINDOW_RELEASE_REVOKED_OR_EXPIRED')


def release():
    parent, expiry = active()
    path = ROOT/'WINDOW_RELEASE.json'
    if not path.exists():
        thread = os.environ.get('CODEX_THREAD_ID')
        if not thread:
            raise PermissionError('ACTUAL_READER_REQUIRED')
        proposal = SOURCE/'docs/MONTHLY_ROBUSTNESS_AND_INDEPENDENCE_REVIEW_V1.md'
        candidate = INPUT.parent/'train-search-batch-v6/PREREGISTRATION.json'
        value = {'version':'MONTHLY_FIXED_WINDOW_RELEASE_V1', 'candidate':CANDIDATE,
            'start':START,'end':END,'warmup_sessions':21,
            'data_seconds':10800,'account_seconds':5400,'concurrency':1,
            'worker_seconds':900,'memory_mib':2048,'numeric_threads':1,
            'main_limit':1,'engineering_repair_limit':1,
            'parent_receipt_id':parent['receipt_id'],'expires_at':expiry.isoformat(),
            'origin':'USER_EXPLICIT_PLAN_APPROVAL_VIA_CODEX','thread_id':thread,
            'approval_question':'是否明确允许释放2025-08-01至2026-07-31，仅用于这个固定候选的一次验证？',
            'approval_statement':'允许','recorded_at':datetime.now(timezone.utc).isoformat(),
            'proposal':{'path':str(proposal),'sha256':sha(proposal)},
            'candidate_preregistration':{'path':str(candidate),'sha256':sha(candidate)},
            'old_budget_mutated':False,'account_budget_registered':False,
            'purpose':'FIXED_CANDIDATE_INDEPENDENT_ACCOUNT_VALIDATION_NOT_QUALIFICATION',
            'historical_exposure_preserved':True,'still_sealed_from':'2026-08-01'}
        value['identity'] = stable_hash(value)
        save(path,value)
    value = read(path)
    validate_release(value,parent,datetime.now(timezone.utc),(ROOT/'revocation.json').exists())
    for key in ['proposal','candidate_preregistration']:
        if sha(value[key]['path']) != value[key]['sha256']:
            raise PermissionError('WINDOW_RELEASE_EVIDENCE_CHANGED')
    return value


def freeze():
    value = release()
    path = ROOT/'ACQUISITION_CODE.json'
    code = {str(p):sha(p) for p in [Path(__file__),
        SOURCE/'scripts/run_baostock_account_v1.py',
        SOURCE/'src/chanlun_trader/data/minute/baostock_provider.py']}
    save(path,{'release_identity':value['identity'],'code':code,
               'first_probe_code':'sh.600000','calendar_metadata_start':'2025-07-01',
               'purpose':'PROVIDER_COVERAGE_PROBE_NO_SIGNALS_NO_ACCOUNT'})


def guard():
    value = release()
    for path, expected in read(ROOT/'ACQUISITION_CODE.json')['code'].items():
        if sha(path) != expected:
            raise PermissionError('ACQUISITION_CODE_CHANGED')
    return value


def request(provider, name, method, query, price_start=None):
    grant = guard()
    if query.get('end_date') != END:
        raise PermissionError('WINDOW_END_NOT_AUTHORIZED')
    if price_start is not None and query.get('start_date') != price_start:
        raise PermissionError('PRICE_WARMUP_NOT_AUTHORIZED')
    target = ROOT/'responses'/f'{name}.json'
    if target.exists():
        raise PermissionError('EXISTING_PROBE_NO_REPLAY')
    started = target.with_suffix('.started.json')
    save(started,{'method':method,'query':query,'started_at':datetime.now(timezone.utc).isoformat(),
                 'reader':grant['thread_id'],'release_identity':grant['identity']})
    result = getattr(provider.bs,method)(**query)
    rows=[]
    while str(result.error_code)=='0' and result.next():
        rows.append(dict(zip(result.fields,result.get_row_data())))
    save(target,{'method':method,'query':query,'fields':list(result.fields),'rows':rows,
                 'error_code':str(result.error_code),'error_msg':result.error_msg,
                 'fetched_at':datetime.now(timezone.utc).isoformat()})
    save(target.with_suffix('.access.json'),{'path':str(target),'sha256':sha(target),
        'reader':grant['thread_id'],'recipient':'USER_PRIVATE_EVIDENCE',
        'purpose':'WINDOW_INPUT_COVERAGE_NO_PERFORMANCE','row_count':len(rows),
        'release_identity':grant['identity'],'error_code':str(result.error_code)})
    if str(result.error_code)!='0':
        raise RuntimeError(f'PROVIDER_REJECTED_{name}: {result.error_code} {result.error_msg}')
    return rows


def probe():
    from chanlun_trader.synthetic_batch_resources import worker_resource_handshake
    from chanlun_trader.data.minute.baostock_provider import BaoStock5MinProvider
    if worker_resource_handshake()['execution'] != {'stage':'window-probe'}:
        raise PermissionError('WORKER_CONTEXT_CHANGED')
    provider = BaoStock5MinProvider()
    with provider.session():
        calendar = request(provider,'calendar','query_trade_dates',
                           {'start_date':'2025-07-01','end_date':END})
        sessions = sorted(row['calendar_date'] for row in calendar if row['is_trading_day']=='1')
        prior = [d for d in sessions if d<START]
        if len(prior)<21 or not any(d>=START for d in sessions):
            raise ValueError('INDEPENDENT_CALENDAR_COVERAGE_INSUFFICIENT')
        first=prior[-21]
        save(ROOT/'CALENDAR_WINDOW.json',{'warmup_start':first,'sessions':[d for d in sessions if d>=first],
            'calendar_sha256':sha(ROOT/'responses/calendar.json'),'start':START,'end':END})
        from run_baostock_account_v1 import FIELDS
        summary={}
        for flag in ['3','1']:
            rows=request(provider,f'probe-{flag}','query_history_k_data_plus',
                {'code':'sh.600000','fields':FIELDS[flag],'start_date':first,'end_date':END,
                 'frequency':'d','adjustflag':flag},price_start=first)
            dates=sorted(row['date'] for row in rows)
            summary[flag]={'row_count':len(rows),'first':dates[0] if dates else None,
                          'last':dates[-1] if dates else None,
                          'window_rows':sum(START<=d<=END for d in dates)}
        save(ROOT/'PROVIDER_COVERAGE.json',{'status':'PROBE_COMPLETE','prices':summary,
            'performance_computed':False,'input_ready':False})
        print(summary)


def run():
    from chanlun_trader.synthetic_batch_resources import run_bounded_worker
    freeze()
    grant=guard()
    completed=ROOT/'resources/probe.completed.json'
    started=ROOT/'resources/probe.started.json'
    if started.exists() or completed.exists():
        raise PermissionError('PROBE_ALREADY_ATTEMPTED_SEE_EVIDENCE')
    limit=min(900,(datetime.fromisoformat(grant['expires_at'])-datetime.now(timezone.utc)).total_seconds())
    env={**os.environ,'PYTHONPATH':str(SOURCE/'src'),'PYTHONDONTWRITEBYTECODE':'1',
         **{k:'1' for k in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']}}
    then=time.monotonic()
    result=run_bounded_worker([sys.executable,str(Path(__file__)),'--worker'],root=SOURCE,
        memory_mib=2048,wall_seconds=limit,environment=env,execution={'stage':'window-probe'},
        on_started=lambda pid:save(started,{'pid':pid,'started_at':datetime.now(timezone.utc).isoformat()}))
    save(completed,{'elapsed_seconds':time.monotonic()-then,
        **{k:v.decode('utf-8',errors='replace') if isinstance(v,bytes) else v for k,v in result.items()}})
    print({'returncode':result['returncode'],'receipt':str(completed)})


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--worker',action='store_true')
    args=parser.parse_args()
    probe() if args.worker else run()
