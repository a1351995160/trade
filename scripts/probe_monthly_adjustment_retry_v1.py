"""用户批准的一次通信复试；独立回执，不覆盖原件或自动恢复批次。"""
import faulthandler
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import time

from fetch_monthly_window_v1 import ROOT, SOURCE, check, read, save, sha
from monthly_window_resource_extension_v1 import limit

PROBE=ROOT/'communication-retry-v1'
CONTEXT={'purpose':'ONE_USER_APPROVED_COMMUNICATION_RETRY','code':'sz.002803'}


def stage(name):
    save(PROBE/(name+'.json'),{'at':datetime.now(timezone.utc).isoformat(),'pid':os.getpid()})


def worker():
    from chanlun_trader.synthetic_batch_resources import worker_resource_handshake
    from chanlun_trader.data.minute.baostock_provider import BaoStock5MinProvider
    if worker_resource_handshake()['execution']!=CONTEXT:
        raise PermissionError('COMMUNICATION_CONTEXT_CHANGED')
    grant=check()
    approved=read(PROBE/'APPROVAL.json')
    if sha(Path(__file__))!=approved['script_sha256']:
        raise PermissionError('PROBE_SOURCE_CHANGED')
    original=Path(approved['original_request'])
    if sha(original)!=approved['original_request_sha256']:
        raise PermissionError('ORIGINAL_REQUEST_CHANGED')
    query=read(original)['query']
    if query!={'code':'sz.002803','start_date':'2025-07-03','end_date':'2026-07-31'}:
        raise PermissionError('QUERY_SCOPE_CHANGED')
    with (PROBE/'STACK.log').open('x',encoding='utf-8') as trace:
        faulthandler.dump_traceback_later(30,repeat=True,file=trace)
        try:
            provider=BaoStock5MinProvider()
            stage('LOGIN_STARTED')
            with provider.session():
                stage('LOGIN_COMPLETED')
                response=provider.bs.query_adjust_factor(**query)
                stage('INITIAL_RESPONSE_RETURNED')
                rows=[]
                while str(response.error_code)=='0' and response.next():
                    rows.append(dict(zip(response.fields,response.get_row_data())))
                stage('ROWS_COMPLETED')
                save(PROBE/'RESPONSE.json',{'method':'query_adjust_factor','query':query,'fields':list(response.fields),
                    'rows':rows,'error_code':str(response.error_code),'error_msg':response.error_msg,
                    'fetched_at':datetime.now(timezone.utc).isoformat()})
                save(PROBE/'ACCESS.json',{'reader':grant['thread_id'],'recipient':'PRIVATE_INPUT_EVALUATION',
                    'sha256':sha(PROBE/'RESPONSE.json'),'row_count':len(rows),'purpose':CONTEXT['purpose']})
            stage('LOGOUT_COMPLETED')
            print({'error_code':str(response.error_code),'rows':len(rows),'response_archived':True},flush=True)
        finally:
            faulthandler.cancel_dump_traceback_later()


def run():
    from chanlun_trader.synthetic_batch_resources import run_bounded_worker
    grant=check()
    if PROBE.exists():
        raise PermissionError('ONE_ATTEMPT_ONLY_SEE_EXISTING_EVIDENCE')
    original=ROOT/'acquisition/actions/sz.002803.started.json'
    used=sum(read(p)['elapsed_seconds'] for p in (ROOT/'resources').glob('*.completed.json'))
    seconds=min(90,limit()-used,(datetime.fromisoformat(grant['expires_at'])-datetime.now(timezone.utc)).total_seconds())
    if seconds<=0:raise PermissionError('PROBE_RESOURCE_EXHAUSTED')
    save(PROBE/'APPROVAL.json',{'approval_statement':'再试试，看看通信','reader':os.environ['CODEX_THREAD_ID'],
        'release_identity':grant['identity'],'original_request':str(original),'original_request_sha256':sha(original),
        'script_sha256':sha(Path(__file__)),'maximum_worker_seconds':seconds,'at':datetime.now(timezone.utc).isoformat(),
        'no_batch_restart':True,'no_performance_exposure':True})
    env={**os.environ,'PYTHONPATH':str(SOURCE/'src'),'PYTHONDONTWRITEBYTECODE':'1','PYTHONUNBUFFERED':'1',
        **{k:'1' for k in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']}}
    start=time.monotonic()
    result=run_bounded_worker([sys.executable,str(Path(__file__)),'--worker'],root=SOURCE,
        memory_mib=2048,wall_seconds=seconds,environment=env,execution=CONTEXT,
        on_started=lambda pid:save(ROOT/'resources/communication-probe-v1.started.json',{'pid':pid,'context':CONTEXT}))
    save(ROOT/'resources/communication-probe-v1.completed.json',{'elapsed_seconds':time.monotonic()-start,
        **{k:v.decode('utf-8',errors='replace') if isinstance(v,bytes) else v for k,v in result.items()}})
    print({'returncode':result['returncode'],'timed_out':result['timed_out'],
        'response_exists':(PROBE/'RESPONSE.json').exists()})


if __name__=='__main__':
    worker() if sys.argv[1:]==['--worker'] else run()
