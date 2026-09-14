"""当前用户委托下的精确已有输入复用；不取数、不授予绩效额度。"""
from datetime import datetime,timezone
import os
from pathlib import Path
from run_baostock_account_v1 import PARENT,SOURCE,active,read,save,sha
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.mutation_boundary import ObjectiveMutationLock
from chanlun_trader.research_factory.scale_proxy_signals_v1 import SMALL
from chanlun_trader.research_factory.train_search_batch_v1 import contract
import prepare_turnover_window_input_v1 as data

RECEIPT=PARENT/'governance/scale_window_input_reuse_v1/confirmation.json'
TRAIN=data.TRAIN.parent/'train-search-batch-v38'


def evidence():
    old=data.guard();ready=read(data.ROOT/'READY.json')
    payload={k:ready[k] for k in ('receipt_id','files','request_window','evaluation_window')}
    if (ready['status']!='TURNOVER_WINDOW_INPUT_READY_NOT_QUALIFIED'
        or ready['input_identity']!=stable_hash(payload) or ready['receipt_id']!=old['receipt_id']
        or ready['request_window']!=[20250704,20260731] or ready['evaluation_window']!=[20250801,20260731]
        or set(ready['files'])!={'TURNOVER.parquet','COVERAGE.json','MATERIALIZATION_RULE.json'}):
        raise PermissionError('SCALE_REUSE_INPUT_IDENTITY_CONFLICT')
    for name,digest in ready['files'].items():
        if sha(data.ROOT/name)!=digest:raise PermissionError('SCALE_REUSE_FILE_CHANGED')
    frozen=read(TRAIN/'PREREGISTRATION.json')['contracts'][SMALL]
    if frozen!=contract(SMALL) or read(TRAIN/SMALL/'FEEDBACK.json')['screen_passed'] is not True:
        raise PermissionError('SCALE_REUSE_TRAIN_CONTRACT_CONFLICT')
    paths=[data.RECEIPT,data.ROOT/'READY.json',*[data.ROOT/n for n in ready['files']],
           TRAIN/'PREREGISTRATION.json',TRAIN/SMALL/'FEEDBACK.json',TRAIN/SMALL/'SETTLEMENT.json',
           Path(__file__),SOURCE/'docs/SCALE_FIXED_WINDOW_V1.md']
    return old,ready,{str(p):sha(p) for p in paths}


def confirm():
    if RECEIPT.exists():return guard()
    parent,expiry=active();old,ready,files=evidence()
    thread=os.environ.get('CODEX_THREAD_ID')
    if not thread:raise PermissionError('ACTUAL_THREAD_REQUIRED')
    r={'version':'SCALE_WINDOW_INPUT_REUSE_V1','candidate':SMALL,
       'source_candidate_hash':stable_hash(contract(SMALL)),
       'objective_id':parent['plan']['objective_id'],'parent_receipt_id':parent['receipt_id'],
       'original_data_receipt_id':old['receipt_id'],'input_identity':ready['input_identity'],
       'inputs':files,'expires_at':min(expiry,datetime.fromisoformat(old['expires_at'])).isoformat(),
       'authority':'USER_DELEGATED_RESEARCH_AGENT_SELECTED_FIXED_WINNER_NOT_INDIVIDUAL_HUMAN_APPROVAL',
       'statement':'接着找，不要停下来，停下来的条件就是找到盈利策略',
       'reader_thread':thread,'recipient':'CURRENT_RESEARCH_AND_REQUESTING_USER',
       'purpose':'EXISTING_TURNOVER_INPUT_REUSE_FOR_FIXED_SCALE_EXTERNAL_CHECK',
       'new_queries':0,'performance_allowance':0,'historical_exposure_preserved':True,
       'at':datetime.now(timezone.utc).isoformat()}
    r['receipt_id']=stable_hash(r)
    with ObjectiveMutationLock.for_resource(RECEIPT):save(RECEIPT,r)
    return guard()


def guard():
    parent,expiry=active();old,ready,files=evidence();r=read(RECEIPT)
    if (RECEIPT.parent/'revocation.json').exists():raise PermissionError('SCALE_REUSE_REVOKED')
    if datetime.now(timezone.utc)>=min(expiry,datetime.fromisoformat(r['expires_at'])):
        raise PermissionError('SCALE_REUSE_EXPIRED')
    if (r['receipt_id']!=stable_hash({k:v for k,v in r.items() if k!='receipt_id'})
        or r['parent_receipt_id']!=parent['receipt_id'] or r['objective_id']!=parent['plan']['objective_id']
        or r['candidate']!=SMALL or r['source_candidate_hash']!=stable_hash(contract(SMALL))
        or r['original_data_receipt_id']!=old['receipt_id'] or r['input_identity']!=ready['input_identity']
        or r['inputs']!=files or r['new_queries']!=0 or r['performance_allowance']!=0):
        raise PermissionError('SCALE_REUSE_RECEIPT_CONFLICT')
    return r
