"""已确证物化内存错误的最小恢复；保留原限制、失败与消耗。"""
from datetime import datetime,timezone
from pathlib import Path
import os
import sys
from acquire_weekly_window_v1 import ROOT,SOURCE,read,save,sha
from weekly_alias_recovery_v1 import recovery
from chanlun_trader.research_factory.common import stable_hash

PATH=ROOT/'MEMORY_RECOVERY_V1.json'


def memory_recovery():
    if not PATH.exists():return None
    alias=recovery(); value=read(PATH)
    if not alias or value['alias_identity']!=alias['identity'] or value['identity']!=stable_hash({k:v for k,v in value.items() if k!='identity'}):
        raise PermissionError('MEMORY_RECOVERY_IDENTITY_CHANGED')
    for p,digest in value['preserved_files'].items():
        if sha(p)!=digest:raise PermissionError('MEMORY_FAILURE_EVIDENCE_CHANGED')
    retry=ROOT/'MEMORY_HANDSHAKE_RECOVERY_V2.json'
    if retry.exists():
        item=read(retry)
        if item['parent_identity']!=value['identity'] or item['identity']!=stable_hash({k:v for k,v in item.items() if k!='identity'}):
            raise PermissionError('MEMORY_RETRY_IDENTITY_CHANGED')
        for p,digest in item['preserved_files'].items():
            if sha(p)!=digest:raise PermissionError('MEMORY_RETRY_EVIDENCE_CHANGED')
    return value


def register():
    alias=recovery()
    if not alias or PATH.exists() or (ROOT/'ACCOUNT_EXECUTION.json').exists():raise PermissionError('RECOVERY_STATE_CONFLICT')
    failed=ROOT/'resources/prepare-window-alias-v1.completed.json'
    result=read(failed)
    if result['returncode']!=1 or '_ArrayMemoryError' not in result['stderr'] or result['timed_out']:
        raise PermissionError('EXPECTED_MEMORY_ERROR_NOT_FOUND')
    frozen=ROOT/'EXECUTION_FREEZE_ALIAS_V1.json'; code={str(Path(k)):v for k,v in read(frozen)['code'].items()}
    paths=[failed,frozen,ROOT/'ALIAS_RECOVERY_V1.json',ROOT/'quality-alias-v1/sz.302132.json']
    for name in ['prepare_weekly_window_v1.py','execute_weekly_window_v1.py']:
        p=ROOT/'memory-recovery-v1/original-code'/name
        if sha(p)!=code[str(Path('scripts')/name)]:raise PermissionError('OLD_SOURCE_HASH_CONFLICT')
        paths.append(p)
    value={'alias_identity':alias['identity'],'origin':'USER_REQUEST_FIX_CONFIRMED_MEMORY_FAILURE',
        'approval_statement':'解决问题','thread_id':os.environ['CODEX_THREAD_ID'],'at':datetime.now(timezone.utc).isoformat(),
        'expires_at':alias['expires_at'],'scope':'STREAM_IDENTICAL_ROWS_NO_STRATEGY_OR_STATISTICAL_CHANGE',
        'memory_mib':2048,'worker_seconds':900,'additional_budget':0,
        'preserved_files':{str(p):sha(p) for p in paths}}
    value['identity']=stable_hash(value);save(PATH,value)


def register_handshake_fix():
    import pyarrow.parquet as pq
    parent=memory_recovery(); target=ROOT/'MEMORY_HANDSHAKE_RECOVERY_V2.json'
    if not parent or target.exists():raise PermissionError('RETRY_REGISTRATION_STATE')
    failure=ROOT/'resources/prepare-window-memory-v1.completed.json'
    result=read(failure)
    if result['returncode']!=1 or 'worker_resource_handshake' not in result['stderr'] or 'JSONDecodeError' not in result['stderr']:
        raise PermissionError('EXPECTED_HANDSHAKE_FAILURE_MISSING')
    archive=ROOT/'memory-recovery-v1/empty-handshake-output';archive.mkdir(exist_ok=True)
    files=[failure,ROOT/'EXECUTION_FREEZE_MEMORY_V1.json']
    empty_paths=[ROOT/n for n in ['DAILY.parquet','STATES.parquet','COMPUTABILITY.parquet']]
    # 所有目标先证明是本次失败产生的零行文件，再进行单文件保留移动。
    for p in empty_paths:
        if (p.resolve().parent!=ROOT.resolve() or not (archive/p.name).resolve().is_relative_to(ROOT.resolve())
            or pq.ParquetFile(p).metadata.num_rows!=0 or (archive/p.name).exists()):
            raise PermissionError('PARTIAL_OUTPUT_NOT_EMPTY_OR_ARCHIVE_CONFLICT')
    for p in empty_paths:
        dest=archive/p.name;p.rename(dest);files.append(dest)
    value={'parent_identity':parent['identity'],'reason':'CONFIRMED_DUPLICATE_SINGLE_USE_RESOURCE_HANDSHAKE',
        'at':datetime.now(timezone.utc).isoformat(),'preserved_files':{str(p):sha(p) for p in files},
        'additional_budget':0,'original_limits_preserved':True}
    value['identity']=stable_hash(value);save(target,value)


if __name__=='__main__':register_handshake_fix() if '--handshake-fix' in sys.argv else register()
