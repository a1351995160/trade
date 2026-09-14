"""本次明确获准时点映射及单日派生适配的恢复证据；不重置额度。"""
import os
from datetime import datetime,timezone
from pathlib import Path
from acquire_weekly_window_v1 import ROOT,SOURCE,check,read,save,sha
from chanlun_trader.research_factory.common import stable_hash

PATH=ROOT/'ALIAS_RECOVERY_V1.json'


def recovery():
    grant=check()
    if not PATH.exists():return None
    value=read(PATH)
    if value['identity']!=stable_hash({k:v for k,v in value.items() if k!='identity'}) or value['release_identity']!=grant['identity']:
        raise PermissionError('ALIAS_RECOVERY_IDENTITY_CHANGED')
    if (ROOT/'alias-recovery-v1/revocation.json').exists():raise PermissionError('ALIAS_RECOVERY_REVOKED')
    for p,digest in value['preserved_files'].items():
        if sha(p)!=digest:raise PermissionError('ALIAS_RECOVERY_EVIDENCE_CHANGED')
    return value


def register():
    grant=check()
    if PATH.exists():raise PermissionError('RECOVERY_ALREADY_REGISTERED')
    if (ROOT/'ACCOUNT_EXECUTION.json').exists():raise PermissionError('ACCOUNT_ALREADY_RESERVED')
    failure=ROOT/'resources/prepare-window.completed.json'
    value=read(failure)
    if value['returncode']!=1 or 'WEEKLY_INPUT_QUALITY_FAILED:sz.302132' not in value['stderr']:
        raise PermissionError('EXPECTED_FAILURE_NOT_FOUND')
    old=read(ROOT/'EXECUTION_FREEZE.json')
    old_code={str(Path(k)):v for k,v in old['code'].items()}
    for name in ['prepare_weekly_window_v1.py','execute_weekly_window_v1.py']:
        if sha(ROOT/'alias-recovery-v1/original-code'/name)!=old_code[str(Path('scripts')/name)]:
            raise PermissionError('ARCHIVED_EXECUTION_SOURCE_CONFLICT')
    files=[failure,ROOT/'EXECUTION_FREEZE.json',ROOT/'CONTINUATION_FAILURE.json',ROOT/'quality/sz.302132.json',
        ROOT/'alias-supplement-v1/APPROVAL.json',ROOT/'alias-supplement-v1/COMPARISON_V2.json',
        SOURCE/'docs/WEEKLY_302132_IDENTITY_TRIAGE_V1.md',SOURCE/'docs/WEEKLY_ALIAS_SUPPLEMENT_V1.md']
    files += [ROOT/'alias-supplement-v1'/n for n in ['price-3.json','price-1.json','actions.json']]
    value={'origin':'USER_EXPLICIT_ALIAS_MAPPING_AND_DERIVED_DAY_APPROVAL',
        'approval_statement':'处理代码时点映射和这一天的复权衔接，解决所有的问题，继续验证这个',
        'thread_id':os.environ['CODEX_THREAD_ID'],'at':datetime.now(timezone.utc).isoformat(),
        'release_identity':grant['identity'],'expires_at':grant['expires_at'],
        'preserved_files':{str(p):sha(p) for p in files},
        'scope':'300114 before20250217;302132 from20250217; prior factor5.975678 carry on transition date only',
        'raw_unchanged':True,'historical_consumption_retained':True,'additional_exposure_budget':0,
        'original_main_not_started':True,'formal_qualification':False}
    value['identity']=stable_hash(value);save(PATH,value)


if __name__=='__main__':register()
