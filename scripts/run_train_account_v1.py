"""当前批准用途的唯一离线入口；默认只验收，不创建新策略或修改旧预算。"""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import subprocess

SOURCE=Path(__file__).resolve().parents[1]
ROOT=Path('E:/llmwiki/autonomous-strategy-research-v1/execution-data-v1/accounting-v1')
BASE=ROOT.parent/'materialized-v3'
ORIGINAL=Path('E:/llmwiki/autonomous-strategy-research-v1/revised-exploration-v1')
OWNER=ROOT/'owner-export/OWNER_DELIVERY_MANIFEST.json'
APPROVAL=Path('C:/Users/84219/.codex/attachments/9eff65d2-9aed-4ef0-927e-9ee2e93e8a3d/pasted-text.txt')


def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def save(path,value):
    from chanlun_trader.research_factory.exploration_governance import immutable
    immutable(path,json.loads(json.dumps(value,ensure_ascii=False,default=str,allow_nan=False)))


def code_identity():
    files=[Path(__file__),*sorted((SOURCE/'src/chanlun_trader/engine').glob('*.py')),
        *[SOURCE/'src/chanlun_trader/research_factory'/n for n in ['budget.py','exploration_governance.py',
            'train_execution_governance_v1.py','train_input_closure_v1.py','train_account_runner_v1.py','novelty.py']],
        SOURCE/'src/chanlun_trader/research/unified_factor.py',SOURCE/'src/chanlun_trader/research/io_safety.py',
        SOURCE/'src/chanlun_trader/research/pit_tradability.py',
        SOURCE/'src/chanlun_trader/data/tdx/windowed_actions_v1.py',SOURCE/'src/chanlun_trader/data/tdx/execution_input.py',
        SOURCE/'src/chanlun_trader/synthetic_batch_resources.py']
    return {str(p.relative_to(SOURCE)):sha(p) for p in files}


def inspect_inputs():
    manifest=read(BASE/'MANIFEST.json')
    for name,item in manifest['files'].items():
        if Path(name).name!=name or sha(BASE/name)!=item['sha256']:raise ValueError('ORIGINAL_V3_IDENTITY_CONFLICT')
    return {'base_verified':True,'owner_manifest_present':OWNER.is_file(),
        'owner_manifest_path':str(OWNER),'base_manifest_sha256':sha(BASE/'MANIFEST.json')}


def owner_files():
    if not OWNER.is_file():raise FileNotFoundError('OWNER_EXECUTION_DATA_PACKAGE_NOT_DELIVERED')
    manifest=read(OWNER)
    if manifest['version']!='OWNER_EXECUTION_EXPORT_V1' or (manifest['start'],manifest['end'])!=(20220722,20240731):
        raise ValueError('OWNER_EXPORT_SCOPE_CONFLICT')
    att=manifest['physical_window_attestation']
    if not att.get('owner') or att.get('window_enforced_before_export') is not True:
        raise PermissionError('PHYSICAL_WINDOW_OWNER_ATTESTATION_REQUIRED')
    if (att.get('start'),att.get('end'))!=(20220722,20240731):raise PermissionError('OWNER_ATTESTATION_WINDOW_CONFLICT')
    required={'daily_supplement','state_supplement','security_master','units','unit_source_evidence','actions_manifest'}
    if set(manifest['files'])!=required:raise ValueError('OWNER_PACKAGE_FIELDS_MISSING_OR_EXTRA')
    files={}
    for role,item in manifest['files'].items():
        name=item['file']
        if Path(name).name!=name or name.lower().startswith('get_divid_factors'):
            raise PermissionError('OWNER_FILE_SCOPE_CONFLICT')
        path=OWNER.parent/name
        if path.resolve()!=path:raise ValueError('OWNER_FILE_IDENTITY_CONFLICT')
        date_columns={'daily_supplement':'date','state_supplement':'trade_date','security_master':'effective_date'}
        if role in date_columns:verify_parquet_window(path,date_columns[role])
        if sha(path)!=item['sha256']:raise ValueError('OWNER_FILE_IDENTITY_CONFLICT')
        files[role]=path
    return manifest,files


def verify_parquet_window(path,date_column):
    import pyarrow.parquet as pq
    metadata=pq.ParquetFile(path)
    index=metadata.schema.names.index(date_column)
    for i in range(metadata.metadata.num_row_groups):
        stats=metadata.metadata.row_group(i).column(index).statistics
        if stats is None or not stats.has_min_max:raise PermissionError('DATE_PARTITION_PROOF_MISSING')
        lower=int(str(stats.min).replace('-',''));upper=int(str(stats.max).replace('-',''))
        if not 20220722<=lower<=upper<=20240731:raise PermissionError('OWNER_PARQUET_WINDOW_CONFLICT')


def bounded_frame(path,date_column):
    import pyarrow.parquet as pq
    from chanlun_trader.research.guard import ResearchDataAccessGuard,ResearchDataAccessGuardConfig
    import pandas as pd
    verify_parquet_window(path,date_column)
    frame=pq.read_table(path,use_threads=False).to_pandas(use_threads=False)
    dates=frame[date_column].astype(str).str.replace('-','').astype(int)
    ResearchDataAccessGuard(ResearchDataAccessGuardConfig(research_end=20240731)).check_frame(pd.DataFrame({'date':dates}),'date')
    frame[date_column]=dates
    return frame


def prepare_input():
    inspect_inputs();manifest,files=owner_files()
    from chanlun_trader.research_factory.train_input_closure_v1 import close_frames,merge_state_evidence,merge_daily_evidence
    from chanlun_trader.data.tdx.windowed_actions_v1 import WindowedCorporateActionDatasetV1
    from chanlun_trader.research.unified_factor import UnifiedFactorRegistry
    from chanlun_trader.research_factory.common import stable_hash
    plan=read(ROOT.parent/'INPUT_READ_PLAN_V2.json')
    members={symbol for symbol,_ in plan['warmup_sources']}|set(plan['missing_symbols_carried_forward'])
    actions=WindowedCorporateActionDatasetV1.load(files['actions_manifest'],sha(files['actions_manifest']),members)
    units=read(files['units'])
    if units.get('evidence_sha256')!=sha(files['unit_source_evidence']):raise ValueError('UNIT_EVIDENCE_HASH_CONFLICT')
    daily=merge_daily_evidence(bounded_frame(BASE/'DAILY.parquet','date'),bounded_frame(files['daily_supplement'],'date'))
    time_evidence=manifest.get('legacy_state_time_evidence',{})
    if time_evidence.get('kind')=='MODELED_NEXT_SESSION_OPEN' and (
            time_evidence.get('normalizer_sha256')!=sha(SOURCE/'src/chanlun_trader/research/pit_tradability.py')
            or time_evidence.get('legacy_states_sha256')!=sha(BASE/'HISTORICAL_POOL_AND_STATE.parquet')):
        raise ValueError('LEGACY_STATE_PRODUCER_IDENTITY_CONFLICT')
    states=merge_state_evidence(bounded_frame(BASE/'HISTORICAL_POOL_AND_STATE.parquet','trade_date'),
        bounded_frame(files['state_supplement'],'trade_date'),time_evidence)
    # 冲突修订不能靠“最后一条”覆盖；保留原件，要求有明确证据的独立决定。
    if daily.duplicated(['symbol','date']).any():
        raise ValueError('SOURCE_CORRECTION_CONFLICT_REQUIRES_EVIDENCE_DECISION')
    master=bounded_frame(files['security_master'],'effective_date')
    if not members<=set(master.symbol) or master[['listed','delisted','source']].isna().any().any():
        raise ValueError('SECURITY_MASTER_COVERAGE_UNKNOWN')
    definition=UnifiedFactorRegistry.read(Path(plan['factor_registry'])).get('RETURN_5D')
    if sha(plan['factor_registry'])!=read(BASE/'FACTOR_BINDING.json')['registry_sha256']:
        raise ValueError('ORIGINAL_FACTOR_REGISTRY_CHANGED')
    calendar=read(BASE/'CALENDAR.json')['sessions']
    source_id=stable_hash({'base':sha(BASE/'MANIFEST.json'),'owner':sha(OWNER),'code':code_identity()})
    bundle=close_frames(daily,states,calendar,actions,units,definition,members,source_id)
    from chanlun_trader.research_factory.novelty import CandidateNoveltyGateV2
    reference={'candidate_id':'RETURN_5D_FIXED_REFERENCE','factor_ids':['RETURN_5D'],
        'mechanism':'FIXED_REFERENCE_REPRODUCTION','parameter_fingerprint':{'threshold':0,'top_n':3,'holding_sessions':3}}
    previous_binding=ROOT.parent/'FIXED_STRATEGY_BINDING_NOT_EXECUTABLE.json'
    previous=read(previous_binding)
    if previous['factor']!='RETURN_5D' or previous['capital']!={'lot_size':100,'max_positions':3,'reference_cash':10000.0}:
        raise ValueError('PREVIOUS_REFERENCE_BINDING_CONFLICT')
    decision=CandidateNoveltyGateV2().evaluate(reference,same_batch_candidates=[reference]).to_dict()
    if not decision['exact_duplicate']:raise ValueError('REFERENCE_REPRODUCTION_IDENTITY_CONFLICT')
    # 原新颖性Gate的拒重结论保留；独立用途明确记录为用户指定复现，不声称新Alpha。
    save(ROOT/'FIXED_REFERENCE_NOVELTY.json',{'original_gate_decision':decision,
        'comparison_scope':'exact authorized reference only; historical exposure burden not reset',
        'previous_binding_sha256':sha(previous_binding),'historical_candidate_record_fabricated':False,
        'purpose':'USER_AUTHORIZED_FIXED_REFERENCE_ACCOUNTING_REPRODUCTION','attachment_sha256':sha(APPROVAL)})
    output=ROOT/'closed-input'/bundle.input_identity
    output.mkdir(parents=True,exist_ok=False)
    hashes={}
    for name,frame in [('daily',bundle.daily),('states',bundle.states),('factors',bundle.factors)]:
        target=output/(name+'.parquet');frame.to_parquet(target,index=False);hashes[name]={'path':str(target),'sha256':sha(target)}
    ready={'status':'READY','input_identity':bundle.input_identity,'pool_identity':bundle.pool_identity,
        'factor_identity':bundle.factor_identity,'calendar_identity':bundle.calendar_identity,'contract_identity':bundle.contract_identity,
        'calendar':bundle.calendar,'files':hashes,'actions_manifest':str(files['actions_manifest']),
        'actions_manifest_sha256':sha(files['actions_manifest']),'required_members':sorted(members),
        'source_identity':source_id,'code_hashes':code_identity(),
        'novelty_evidence_sha256':sha(ROOT/'FIXED_REFERENCE_NOVELTY.json'),
        'novelty_status':'AUTHORIZED_FIXED_REFERENCE_REPRODUCTION'}
    save(ROOT/'INPUT_READY.json',ready)
    return ready


def load_ready(repair=None):
    r=read(ROOT/'INPUT_READY.json')
    if r['status']!='READY':raise PermissionError('INPUT_NOT_READY')
    if repair:
        green=read(repair['green_evidence'])
        if green.get('status')!='PASS' or green.get('affects_input') is not False or green.get('code_hashes')!=code_identity():
            raise PermissionError('REPAIR_REQUIRES_VERIFIED_UNCHANGED_INPUT_AND_FROZEN_CODE')
    elif r['code_hashes']!=code_identity():raise PermissionError('FROZEN_INPUT_CODE_CHANGED')
    for item in r['files'].values():
        if sha(item['path'])!=item['sha256']:raise ValueError('CLOSED_INPUT_CHANGED')
    if sha(r['actions_manifest'])!=r['actions_manifest_sha256']:raise ValueError('ACTION_IDENTITY_CHANGED')
    if sha(ROOT/'FIXED_REFERENCE_NOVELTY.json')!=r['novelty_evidence_sha256']:raise ValueError('NOVELTY_EVIDENCE_CHANGED')
    return r


def worker(mode,execution_id=None):
    from chanlun_trader.synthetic_batch_resources import worker_resource_handshake
    resource=worker_resource_handshake()
    if mode=='prepare':
        if resource['execution']!={'mode':'TRAIN_INPUT_PREPARATION'}:raise PermissionError('RESOURCE_CONTEXT_CONFLICT')
        prepare_input();return
    from chanlun_trader.research_factory.train_execution_governance_v1 import TrainExecutionGovernanceV1
    from chanlun_trader.research_factory.train_input_closure_v1 import ClosedTrainInputV1
    from chanlun_trader.data.tdx.windowed_actions_v1 import WindowedCorporateActionDatasetV1
    from chanlun_trader.research_factory.train_account_runner_v1 import run_fixed_reference,TrainingAccountExecutionFailure
    import pyarrow.parquet as pq
    s=TrainExecutionGovernanceV1(ORIGINAL);receipt=s.active()
    reservation=next(e for e in s.events() if e.get('execution_id')==execution_id and e['event']=='RESERVED')
    r=load_ready(reservation.get('repair'))
    execution_source=read(ROOT/'execution'/execution_id/'source_identity.json')
    if resource['execution']!={'execution_id':execution_id,'plan_id':receipt['plan_id'],'source_identity':execution_source}:
        raise PermissionError('WORKER_RECEIPT_CONTEXT_CONFLICT')
    if receipt['plan']['input_identity']!=r['input_identity']:raise PermissionError('RECEIPT_INPUT_CONFLICT')
    frames={name:pq.read_table(item['path'],use_threads=False).to_pandas(use_threads=False) for name,item in r['files'].items()}
    actions=WindowedCorporateActionDatasetV1.load(r['actions_manifest'],r['actions_manifest_sha256'],set(r['required_members']))
    b=ClosedTrainInputV1(frames['daily'],frames['states'],frames['factors'],r['calendar'],actions,
        r['input_identity'],r['pool_identity'],r['factor_identity'],r['calendar_identity'],r['contract_identity'])
    s.start_exposure(execution_id)
    try:result=run_fixed_reference(b,(execution_source['code_commit'],execution_source['dirty_worktree']),s.active)
    except TrainingAccountExecutionFailure as exc:
        save(ROOT/'execution'/execution_id/'failed_account.json',exc.evidence)
        raise
    save(ROOT/'execution'/execution_id/'result.json',result)
    save(ROOT/'execution'/execution_id/'completed.json',{'status':'COMPLETED','result_sha256':sha(ROOT/'execution'/execution_id/'result.json')})


def bounded(mode,execution=None,seconds=900):
    from chanlun_trader.synthetic_batch_resources import run_bounded_worker
    token=execution or {'mode':'TRAIN_INPUT_PREPARATION'}
    command=[sys.executable,str(Path(__file__).resolve()),'--worker',mode]
    if execution:command+=['--execution-id',execution['execution_id']]
    environment=dict(os.environ,PYTHONPATH=str(SOURCE/'src')+os.pathsep+str(SOURCE),
        OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1')
    audit=ROOT/'execution'/execution['execution_id'] if execution else ROOT/'preparation'
    return run_bounded_worker(command,root=SOURCE,memory_mib=2048,wall_seconds=seconds,
        on_started=lambda pid:save(audit/'worker_started.json',{'pid':pid,'started_at':datetime.now(timezone.utc).isoformat(),
            'memory_mib':2048,'wall_seconds':seconds,'numerical_threads':1,'execution':token}),
        environment=environment,execution=token)


def execute(repair_proof=None):
    if os.environ.get('CHANLUN_TEST_ISOLATION')=='1':raise PermissionError('REAL_EXECUTION_CANNOT_USE_TEST_ISOLATION')
    parent=read(ORIGINAL/'governance/confirmation.json')
    remaining=(min(datetime.fromisoformat(parent['plan']['expires_at']),
        datetime.fromisoformat('2026-09-14T10:05:03+08:00'))-datetime.now(timezone.utc)).total_seconds()
    if remaining<=0:raise PermissionError('TRAIN_EXECUTION_APPROVAL_EXPIRED')
    inspect_inputs()
    if not OWNER.exists():raise FileNotFoundError('OWNER_EXECUTION_DATA_PACKAGE_NOT_DELIVERED')
    if not (ROOT/'INPUT_READY.json').exists():
        result=bounded('prepare',seconds=min(900,remaining))
        save(ROOT/'preparation_process.json',{k:v.decode(errors='replace') if isinstance(v,bytes) else v for k,v in result.items()})
        if result['returncode']:raise RuntimeError('INPUT_PREPARATION_FAILED_DETAILS_ARCHIVED')
    repair=read(repair_proof) if repair_proof else None
    r=load_ready(repair)
    from chanlun_trader.research_factory.train_execution_governance_v1 import TrainExecutionGovernanceV1
    from chanlun_trader.research_factory.train_account_runner_v1 import FIXED_CONTRACT
    from chanlun_trader.research_factory.common import stable_hash
    s=TrainExecutionGovernanceV1(ORIGINAL);parent=read(ORIGINAL/'governance/confirmation.json')
    source=read(ROOT/'APPROVAL_FACT.json')
    if source['attachment_sha256']!=sha(APPROVAL):raise PermissionError('USER_APPROVAL_ATTACHMENT_CHANGED')
    source['code_commit']=subprocess.run(['git','rev-parse','HEAD'],cwd=SOURCE,check=True,capture_output=True,text=True).stdout.strip()
    source['dirty_worktree']=bool(subprocess.run(['git','status','--porcelain'],cwd=SOURCE,check=True,capture_output=True,text=True).stdout.strip())
    contract=stable_hash(FIXED_CONTRACT)
    plan={'contracts':{contract:FIXED_CONTRACT},'limit':2,'wall_limit':1800,
        'result_type':'TRAIN_EXECUTION_BACKTEST_EXPLORATORY','input_identity':r['input_identity'],
        'objective_id':parent['plan']['objective_id'],'expires_at':parent['plan']['expires_at']}
    if repair:
        if source['code_commit']!=repair['fix_commit'] or source['dirty_worktree']:
            raise PermissionError('REPAIR_COMMIT_NOT_CURRENT_CLEAN_SOURCE')
        receipt=s.active()
        if receipt['plan']!=plan:raise PermissionError('REPAIR_CHANGED_FROZEN_PLAN')
    else:receipt=s.confirm(plan,source,preflight=load_ready)
    reservation=s.reserve(contract,repair)
    if reservation['status']!='RESERVED':raise PermissionError('ALREADY_ATTEMPTED_RECONCILE_NO_FREE_RERUN')
    eid=reservation['execution_id'];started=time.monotonic();completed=False
    execution_source={'code_commit':source['code_commit'],'dirty_worktree':source['dirty_worktree']}
    save(ROOT/'execution'/eid/'source_identity.json',execution_source)
    try:
        result=bounded('execute',{'execution_id':eid,'plan_id':receipt['plan_id'],'source_identity':execution_source},reservation['wall_seconds'])
        save(ROOT/'execution'/eid/'process.json',{k:v.decode(errors='replace') if isinstance(v,bytes) else v for k,v in result.items()})
        completed=result['returncode']==0 and (ROOT/'execution'/eid/'completed.json').exists()
    finally:s.settle(eid,time.monotonic()-started,completed)
    save(ROOT/'EXECUTION_SUMMARY.json',s.summary())


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--worker',choices=['prepare','execute'])
    parser.add_argument('--execution-id');parser.add_argument('--execute-approved',action='store_true')
    parser.add_argument('--repair-proof');parser.add_argument('--recovery-status',action='store_true')
    options=parser.parse_args()
    if options.worker:worker(options.worker,options.execution_id)
    elif options.recovery_status:
        from chanlun_trader.research_factory.train_execution_governance_v1 import TrainExecutionGovernanceV1
        service=TrainExecutionGovernanceV1(ORIGINAL)
        print(json.dumps(service.summary() if service.receipt_path.exists() else {'status':'NO_EXECUTION_RECEIPT_NO_REPLAY'},ensure_ascii=False))
    elif options.execute_approved:execute(options.repair_proof)
    else:print(json.dumps(inspect_inputs(),ensure_ascii=False))
