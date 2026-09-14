"""第39批已确证、未曝光的字段接线修复；原失败不可覆盖。"""
import os
from pathlib import Path
from datetime import datetime,timezone
from run_baostock_account_v1 import active,read,save,sha,PARENT,SOURCE,ROOT as INPUT
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.mutation_boundary import ObjectiveMutationLock

ORIGINAL=INPUT.parent/'train-search-batch-v39'
ROOT=ORIGINAL/'input-recovery-v1'


def resolve(base):
    base=Path(base)
    if os.environ.get('RESEARCH_BATCH39_INPUT_RECOVERY')!='1':return base
    if base!=ORIGINAL:raise PermissionError('RECOVERY_ONLY_BATCH39')
    active();r=read(ROOT/'RECOVERY.json')
    if r['identity']!=stable_hash({k:v for k,v in r.items() if k!='identity'}):raise PermissionError('RECOVERY_IDENTITY_CHANGED')
    if (ROOT/'revocation.json').exists():raise PermissionError('RECOVERY_REVOKED')
    for path,digest in r['original_evidence'].items():
        if sha(path)!=digest:raise PermissionError('ORIGINAL_FAILURE_CHANGED')
    return ROOT


def confirm():
    import run_train_search_batch_v1 as batch
    from chanlun_trader.research_factory.train_search_batch_v1 import contract,TrainSearchGovernanceV1
    active();frozen=read(ORIGINAL/'PREREGISTRATION.json')
    if ROOT.exists():raise PermissionError('NO_RECOVERY_REPLAY')
    if read(ORIGINAL/'RUNNER_COMPLETED.json')['status']!='ENGINEERING_FAILURE_NEEDS_TRIAGE':raise PermissionError('ORIGINAL_FAILURE_REQUIRED')
    paths=[ORIGINAL/n for n in ('PREREGISTRATION.json','APPROVAL_SOURCE.json','RUNNER_COMPLETED.json','DELIVERY_STATUS.json')]
    for name,value in frozen['contracts'].items():
        if value!=contract(name):raise PermissionError('STRATEGY_CHANGED_NOT_INPUT_REPAIR')
        service=TrainSearchGovernanceV1(PARENT,name)
        if service.receipt_path.exists() or service.journal.exists():raise PermissionError('GOVERNANCE_HISTORY_REQUIRES_SETTLEMENT_REPAIR')
        if any((ORIGINAL/name/n).exists() for n in ('EXECUTION.json','RESULT.json','FEATURES.parquet','READY.json')):raise PermissionError('NO_OUTCOME_PREREQUISITE_FAILED')
        p=ORIGINAL/'resources'/f'{name}-prepare.completed.json';value=read(p)
        if value['returncode']==0 or "has no attribute 'volume'" not in value['stderr']:raise PermissionError('EXACT_CONFIRMED_ERROR_REQUIRED')
    starts=list((ORIGINAL/'resources').glob('*.started.json'))
    for p in starts:
        end=p.with_name(p.name.replace('.started.json','.completed.json'))
        if not end.exists():raise PermissionError('UNSETTLED_ORIGINAL_WORKER')
        paths.extend([p,end])
    historical=sum(read(p)['elapsed_seconds'] for p in (ORIGINAL/'resources').glob('*.completed.json'))
    receipt={'version':'BATCH39_INPUT_RECOVERY_V1','original_evidence':{str(p):sha(p) for p in paths},
        'confirmed_error':'MISSING_RAW_VOLUME_JOIN_FOR_TWO_NEW_NAMES','affected':'PREPARATION_ONLY_NO_PRICE_PERFORMANCE',
        'historical_worker_seconds':historical,'new_performance_allowance':0,
        'authority':'USER_AUTHORIZED_MINIMAL_CONFIRMED_ENGINEERING_REPAIR','thread':os.environ['CODEX_THREAD_ID'],
        'at':datetime.now(timezone.utc).isoformat()}
    receipt['identity']=stable_hash(receipt)
    with ObjectiveMutationLock.for_resource(ROOT/'RECOVERY.json'):save(ROOT/'RECOVERY.json',receipt)
    save(ROOT/'APPROVAL_SOURCE.json',read(ORIGINAL/'APPROVAL_SOURCE.json'))
    batch.BATCH=39;batch.ROOT=ROOT;batch.NAMES=list(frozen['contracts'])
    updated={**frozen,'code':batch.code(),'historical_worker_seconds':historical,
        'inputs':{**frozen['inputs'],**receipt['original_evidence'],str(ROOT/'RECOVERY.json'):sha(ROOT/'RECOVERY.json')},
        'recovery_identity':receipt['identity']}
    save(ROOT/'PREREGISTRATION.json',updated)
    archive={};skills=Path('C:/Users/84219/.codex/skills')
    for name,digest in updated['code'].items():
        p=Path(name);p=p if p.is_absolute() else SOURCE/p
        rel=p.relative_to(SOURCE) if p.is_relative_to(SOURCE) else Path('external-skills')/p.relative_to(skills)
        dest=ROOT/'source-archive-v1'/rel;dest.parent.mkdir(parents=True,exist_ok=True)
        if sha(p)!=digest:raise PermissionError('SOURCE_CHANGED_DURING_RECOVERY')
        with dest.open('xb') as stream:stream.write(p.read_bytes())
        archive[str(p)]={'path':str(dest),'sha256':digest}
    save(ROOT/'source-archive-v1/INDEX.json',archive)
    return receipt
