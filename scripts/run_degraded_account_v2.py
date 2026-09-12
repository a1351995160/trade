"""已批准的降级用途：一次无收益复核，条件通过后一次原引擎账户执行。"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

SOURCE=Path(__file__).resolve().parents[1]
ROOT=Path('E:/llmwiki/autonomous-strategy-research-v1/execution-data-v1/degraded-account-v2')
ORIGINAL=Path('E:/llmwiki/autonomous-strategy-research-v1/revised-exploration-v1')


def save(name,value):
    from chanlun_trader.research_factory.exploration_governance import immutable
    immutable(ROOT/name,json.loads(json.dumps(value,ensure_ascii=False,allow_nan=False,default=str)))


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    import hashlib
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def code_identity():
    files=[Path(__file__),*sorted((SOURCE/'src/chanlun_trader/engine').glob('*.py'))]
    files += [SOURCE/'src/chanlun_trader/research_factory'/n for n in [
        'degraded_execution_v2.py','degraded_input_v2.py','degraded_governance_v1.py','degraded_train_v1.py','fixed_account_rules.py',
        'train_account_runner_v1.py','train_execution_governance_v1.py','exploration_governance.py','budget.py','novelty.py']]
    files += [SOURCE/'src/chanlun_trader/synthetic_batch_resources.py',SOURCE/'src/chanlun_trader/data/tdx/windowed_actions_v1.py']
    return {str(p.relative_to(SOURCE)):sha(p) for p in files}


def load_ready(repair=None):
    r=read(ROOT/'INPUT_READY.json')
    if repair:
        green=read(repair['green_evidence'])
        if green.get('status')!='PASS' or green.get('affects_input') is not False or green.get('code_hashes')!=code_identity():
            raise PermissionError('REPAIR_CODE_OR_UNCHANGED_INPUT_NOT_PROVEN')
    elif r['code_identity']!=code_identity():
        raise PermissionError('FROZEN_EXECUTION_CODE_CHANGED')
    if sha(ROOT/'FEASIBILITY.json')!=r['feasibility_sha256'] or not read(ROOT/'FEASIBILITY.json')['passed']:
        raise PermissionError('FROZEN_FEASIBILITY_CONFLICT')
    if sha(ROOT/'FIXED_REFERENCE_NOVELTY.json')!=r['novelty_sha256']:
        raise PermissionError('FROZEN_NOVELTY_CONFLICT')
    return r


def worker(mode,execution_id=None):
    from chanlun_trader.synthetic_batch_resources import worker_resource_handshake
    resource=worker_resource_handshake()
    from chanlun_trader.research_factory.degraded_input_v2 import load_inputs,check_feasibility
    from chanlun_trader.research_factory.degraded_execution_v2 import run_degraded_account
    from chanlun_trader.research_factory.degraded_governance_v1 import DegradedGovernanceV1
    from chanlun_trader.research_factory.train_account_runner_v1 import TrainingAccountExecutionFailure
    bundle=load_inputs()
    if mode=='feasibility':
        result=check_feasibility(bundle)
        save('FEASIBILITY.json',result)
        from chanlun_trader.research_factory.novelty import CandidateNoveltyGateV2
        previous=ROOT.parent/'FIXED_STRATEGY_BINDING_NOT_EXECUTABLE.json'
        binding=read(previous)
        if binding['factor']!='RETURN_5D' or binding['capital']!={'lot_size':100,'max_positions':3,'reference_cash':10000.0}:
            raise ValueError('ORIGINAL_REFERENCE_BINDING_CONFLICT')
        reference={'candidate_id':'RETURN_5D_FIXED_REFERENCE','factor_ids':['RETURN_5D'],
            'mechanism':'FIXED_REFERENCE_REPRODUCTION','parameter_fingerprint':{'threshold':0,'top_n':3,'holding_sessions':3}}
        decision=CandidateNoveltyGateV2().evaluate(reference,same_batch_candidates=[reference]).to_dict()
        if not decision['exact_duplicate']:
            raise ValueError('FIXED_REFERENCE_IDENTITY_CONFLICT')
        save('FIXED_REFERENCE_NOVELTY.json',{'original_gate_decision':decision,'previous_binding_sha256':sha(previous),
            'purpose':'USER_AUTHORIZED_FIXED_REFERENCE_ACCOUNTING_REPRODUCTION','new_alpha_claim':False,
            'history_reset':False,'comparison_scope':'exact authorized reference; no fabricated historical candidate'})
        save('INPUT_READY.json',{'status':'READY' if result['passed'] else 'NOT_READY','input_identity':bundle.input_identity,
            'feasibility_passed':result['passed'],'feasibility_sha256':sha(ROOT/'FEASIBILITY.json'),
            'novelty_status':'AUTHORIZED_FIXED_REFERENCE_REPRODUCTION','novelty_sha256':sha(ROOT/'FIXED_REFERENCE_NOVELTY.json'),
            'code_identity':code_identity(),'STRICT_TRAIN_INPUT_READY':False,
            'DEGRADED_TRAIN_EXECUTION_INPUT_READY':result['passed']})
        from collections import Counter
        print(json.dumps({'passed':result['passed'],'counts':result['counts'],'candidate_paths':result['candidate_paths'],
            'reasons':dict(Counter(p['reason'] for p in result['paths']))}))
        return
    service=DegradedGovernanceV1(ORIGINAL);receipt=service.active()
    reserved=next(e for e in service.events() if e.get('execution_id')==execution_id and e['event']=='RESERVED')
    ready=load_ready(reserved.get('repair'))
    context=read(ROOT/'execution'/execution_id/'source_identity.json')
    if resource['execution']!={'execution_id':execution_id,'plan_id':receipt['plan_id'],'source_identity':context}:
        raise PermissionError('RESOURCE_RECEIPT_CONTEXT_CONFLICT')
    if bundle.input_identity!=ready['input_identity'] or bundle.input_identity!=receipt['plan']['input_identity']:
        raise PermissionError('GOVERNED_INPUT_CONFLICT')
    save('execution/'+execution_id+'/input_access.json',{'files':bundle.access,'input_identity':bundle.input_identity,
        'purpose':'DEGRADED_TRAIN_ACCOUNT_MAIN','reader_pid':os.getpid(),'recipient':'REQUESTING_USER'})
    service.start_exposure(execution_id)
    try:
        result=run_degraded_account(bundle,(context['code_commit'],context['dirty_worktree']),service.active)
    except TrainingAccountExecutionFailure as exc:
        save('execution/'+execution_id+'/failed_account.json',exc.evidence)
        raise
    save('execution/'+execution_id+'/result.json',result)
    save('execution/'+execution_id+'/completed.json',{'status':result['status'],
        'result_sha256':sha(ROOT/'execution'/execution_id/'result.json')})
    print(json.dumps({'status':result['status'],'metrics':result['metrics']},default=str))


def bounded(mode,context=None,seconds=900):
    from chanlun_trader.synthetic_batch_resources import run_bounded_worker
    command=[sys.executable,str(Path(__file__)),'--worker',mode]
    relative='feasibility' if context is None else 'execution/'+context['execution_id']
    if context:
        command+=['--execution-id',context['execution_id']]
    environment={**os.environ,'PYTHONPATH':str(SOURCE/'src'),'PYTHONIOENCODING':'utf-8',
        **{k:'1' for k in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']}}
    started=time.monotonic()
    result=run_bounded_worker(command,root=SOURCE,memory_mib=2048,wall_seconds=seconds,
        environment=environment,execution=context or {'purpose':'DEGRADED_FEASIBILITY'},
        on_started=lambda pid:save(relative+'/worker_started.json',{'pid':pid,'wall_seconds':seconds,'memory_mib':2048,
            'numeric_threads':1,'created_at':datetime.now(timezone.utc).isoformat()}))
    save(relative+'/process.json',{'elapsed_seconds':time.monotonic()-started,
        **{k:v.decode('utf-8',errors='replace') if isinstance(v,bytes) else v for k,v in result.items()}})
    print(result['stdout'].decode('utf-8',errors='replace'));print(result['stderr'].decode('utf-8',errors='replace'))
    return result


def execute(feasibility_only=False,repair_proof=None):
    if os.environ.get('CHANLUN_TEST_ISOLATION')=='1':
        raise PermissionError('REAL_INPUT_CANNOT_USE_SYNTHETIC_ISOLATION')
    from chanlun_trader.research_factory.degraded_execution_v2 import CONTRACT
    from chanlun_trader.research_factory.common import stable_hash
    parent=read(ORIGINAL/'governance/confirmation.json')
    expiry=min(datetime.fromisoformat(CONTRACT['expires_at']),datetime.fromisoformat(parent['plan']['expires_at']))
    if datetime.now(timezone.utc)>=expiry:
        raise PermissionError('DEGRADED_APPROVAL_EXPIRED')
    if (ORIGINAL/'governance/revocation.json').exists():
        raise PermissionError('PARENT_REVOKED')
    if not (ROOT/'FEASIBILITY.json').exists():
        save('EXECUTION_FREEZE.json',{'contract':CONTRACT,'code_identity':code_identity(),
            'parent_receipt_id':parent['receipt_id'],'expires_at':expiry.isoformat(),
            'budget_before_sha256':sha(ORIGINAL/'governance/search_budget_registry.json')})
        result=bounded('feasibility',seconds=min(900,(expiry-datetime.now(timezone.utc)).total_seconds()))
        if result['returncode']:
            raise RuntimeError('FEASIBILITY_ENGINEERING_FAILURE_EVIDENCE_PRESERVED')
    result=read(ROOT/'FEASIBILITY.json')
    if not result['passed']:
        print('INSUFFICIENT_EXECUTABLE_EVIDENCE_NO_ACCOUNT_EXPOSURE');return
    if feasibility_only:
        return
    repair=read(repair_proof) if repair_proof else None
    ready=load_ready(repair)
    from chanlun_trader.research_factory.degraded_governance_v1 import DegradedGovernanceV1
    service=DegradedGovernanceV1(ORIGINAL)
    approval={'origin':'USER_EXPLICIT_PLAN_APPROVAL_VIA_CODEX','thread_id':os.environ.get('CODEX_THREAD_ID'),
        'approval_statement':'本消息继续沿用此前已经明确批准的降级探索用途',
        'main_purpose':'DEGRADED_TRAIN_ACCOUNT_MAIN','main_limit':1,
        'repair_purpose':'DEGRADED_TRAIN_ACCOUNT_REPAIR','repair_limit':1,
        'source_kind':'CURRENT_USER_MESSAGE_STRUCTURED_SCOPE_EXTRACTION_NOT_ATTACHMENT',
        'recovery_point':'f7425f87fb925ee958eb647e3f8f81cba3508d19'}
    save('APPROVAL_FACT.json',approval)
    source={**approval,'approval_record_sha256':sha(ROOT/'APPROVAL_FACT.json')}
    plan={'contracts':{stable_hash(CONTRACT):CONTRACT},'limit':2,'wall_limit':1800,
        'result_type':CONTRACT['result_type'],'input_identity':ready['input_identity'],
        'objective_id':parent['plan']['objective_id'],'expires_at':expiry.isoformat()}
    if repair:
        receipt=service.active()
        current=subprocess.check_output(['git','rev-parse','HEAD'],cwd=SOURCE,text=True).strip()
        dirty=subprocess.check_output(['git','status','--porcelain'],cwd=SOURCE,text=True).strip()
        if receipt['plan']!=plan or current!=repair['fix_commit'] or dirty:
            raise PermissionError('REPAIR_REQUIRES_UNCHANGED_PLAN_AND_CLEAN_FIX_COMMIT')
    else:
        receipt=service.confirm(plan,source,preflight=load_ready)
    reservation=service.reserve(stable_hash(CONTRACT),repair)
    if reservation['status']!='RESERVED':
        raise PermissionError('EXISTING_ATTEMPT_RECONCILE_NO_FREE_REPLAY')
    eid=reservation['execution_id'];started=time.monotonic();completed=False
    source_id={'code_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=SOURCE,text=True).strip(),
        'dirty_worktree':bool(subprocess.check_output(['git','status','--porcelain'],cwd=SOURCE,text=True).strip())}
    save('execution/'+eid+'/source_identity.json',source_id)
    try:
        result=bounded('execute',{'execution_id':eid,'plan_id':receipt['plan_id'],'source_identity':source_id},reservation['wall_seconds'])
        completed=result['returncode']==0 and (ROOT/'execution'/eid/'completed.json').exists()
    finally:
        service.settle(eid,time.monotonic()-started,completed)
    save('REPAIR_EXECUTION_SUMMARY.json' if repair else 'EXECUTION_SUMMARY.json',{'execution_id':eid,**service.summary()})
    if not completed:
        raise RuntimeError('ACCOUNT_EXECUTION_NOT_COMPLETED_EVIDENCE_PRESERVED')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--worker',choices=['feasibility','execute'])
    parser.add_argument('--execution-id');parser.add_argument('--feasibility-only',action='store_true')
    parser.add_argument('--repair-proof')
    args=parser.parse_args()
    if args.worker:
        worker(args.worker,args.execution_id)
    else:
        execute(args.feasibility_only,args.repair_proof)
