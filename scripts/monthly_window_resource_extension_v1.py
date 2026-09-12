"""用户追加数据准备时间；原授权、消费、账户次数及截止不改写。"""
from datetime import datetime, timezone
import os

from acquire_monthly_independent_v1 import ROOT, guard, read, save, sha
from chanlun_trader.research_factory.common import stable_hash


def validate(value,grant,now):
    if value['identity']!=stable_hash({k:v for k,v in value.items() if k!='identity'}):
        raise PermissionError('DATA_EXTENSION_IDENTITY_CHANGED')
    if (value['parent_release_identity']!=grant['identity'] or value['old_data_seconds']!=10800
        or value['additional_data_seconds']!=10800 or value['total_data_seconds']!=21600
        or value['account_seconds']!=grant['account_seconds']
        or value['expires_at']!=grant['expires_at'] or value['main_limit']!=1 or value['repair_limit']!=1):
        raise PermissionError('DATA_EXTENSION_SCOPE_CHANGED')
    if now>=datetime.fromisoformat(value['expires_at']):
        raise PermissionError('DATA_EXTENSION_EXPIRED')
    return value['total_data_seconds']


def register():
    grant=guard()
    path=ROOT/'DATA_TIME_EXTENSION_V1.json'
    if not path.exists():
        records=sorted((ROOT/'resources').glob('*.completed.json'))
        value={'origin':'USER_EXPLICIT_RESOURCE_EXTENSION_VIA_CODEX',
            'approval_statement':'那你加点时间吧，都不够时间了','thread_id':os.environ['CODEX_THREAD_ID'],
            'parent_release_identity':grant['identity'],'old_data_seconds':10800,
            'additional_data_seconds':10800,'total_data_seconds':21600,
            'account_seconds':grant['account_seconds'],'main_limit':1,'repair_limit':1,
            'expires_at':grant['expires_at'],'recorded_at':datetime.now(timezone.utc).isoformat(),
            'selection_reason':'Recent 50-symbol worker about 65 seconds; remaining acquisition about two hours; include materialization margin',
            'old_consumption_reset':False,'old_grant_mutated':False,
            'settled_seconds_at_extension':sum(read(p)['elapsed_seconds'] for p in records),
            'resource_receipts':{str(p):sha(p) for p in records}}
        value['identity']=stable_hash(value)
        save(path,value)
    return limit()


def limit():
    grant=guard()
    value=read(ROOT/'DATA_TIME_EXTENSION_V1.json')
    return validate(value,grant,datetime.now(timezone.utc))
