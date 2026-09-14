"""确证空信号错误后的单次输入准备恢复；不重取数据、不消耗账户修复次数。"""
from datetime import datetime, timezone
import os

from fetch_monthly_window_v1 import ROOT, check, read, save, sha


def run():
    grant=check()
    receipt=ROOT/'resources/prepare-window.completed.json'
    failure=read(receipt)
    if (failure['returncode']==0 or failure['timed_out']
            or 'IndexError: single positional indexer is out-of-bounds' not in failure['stderr']
            or 'adjusted_rows' not in failure['stderr']):
        raise PermissionError('EXPECTED_EMPTY_SIGNAL_FAILURE_NOT_FOUND')
    for name in ['DAILY.parquet','STATES.parquet','FEATURES.parquet','INPUT_MANIFEST.json',
                 'READY.json','ACCOUNT_EXECUTION.json','PREPARATION_EMPTY_FIX_V1.json']:
        if (ROOT/name).exists():raise PermissionError('RECOVERY_REQUIRES_UNMATERIALIZED_UNEXPOSED_INPUT')
    preserved=[receipt,ROOT/'resources/prepare-window.started.json',ROOT/'EXECUTION_FREEZE_V4.json',
               ROOT/'PREPARE_ACCESS.json',ROOT/'PIPELINE_COMPLETED_V4.json']
    preserved+=list((ROOT/'quality').glob('*.json'))
    save(ROOT/'PREPARATION_EMPTY_FIX_V1.json',{
        'approval_statement':'修复问题','release_identity':grant['identity'],
        'reader':os.environ['CODEX_THREAD_ID'],'at':datetime.now(timezone.utc).isoformat(),
        'preserved_evidence':{str(p):sha(p) for p in preserved},
        'fix':'GUARD_EMPTY_READY_ROWS_AND_PRESERVE_RAW_STATES_COVERAGE',
        'synthetic_validation':'2 RED cases reproduced; 28 relevant tests GREEN',
        'account_exposures':0,'account_repair_exposures':0,'data_limit_unchanged':True})
    save(ROOT/'PIPELINE_STARTED_EMPTY_FIX_V1.json',{'pid':os.getpid(),'started_at':datetime.now(timezone.utc).isoformat()})
    exit_code=1
    try:
        from execute_monthly_window_v1 import run as execute
        execute()
        exit_code=0
    finally:
        save(ROOT/'PIPELINE_COMPLETED_EMPTY_FIX_V1.json',{
            'exit_code':exit_code,'finished_at':datetime.now(timezone.utc).isoformat()})


if __name__=='__main__':
    run()
