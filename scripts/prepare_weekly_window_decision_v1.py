"""只读实际合同与窗口元数据，生成具体待确认决定；不授权、不读行情行。"""
import os
from datetime import datetime,timezone

from run_baostock_account_v1 import ROOT as INPUT,PARENT,read,save,sha,active
from chanlun_trader.research_factory.common import stable_hash

NAME='WEEKLY_LOW_VOL_FIXED_MARKET_HOLD_20'
WINDOW=INPUT.parent/'monthly-independent-window-v1'
ROOT=INPUT.parent/'weekly-low-vol-window-v1'


def date_capacity(sessions,warmup_count):
    """只做日历上限：每周最后session、固定退出仍需要之后22个session。"""
    import pandas as pd
    if sessions!=sorted(set(sessions)):raise ValueError('INDEPENDENT_CALENDAR_REQUIRED')
    weeks=[pd.Timestamp(str(d)).isocalendar()[:2] for d in sessions]
    return sum(20250801<=d<=20260731 and i+22<len(sessions) and
               i>=warmup_count-1 and weeks[i]!=weeks[i+1]
               for i,d in enumerate(sessions[:-1]))


def run():
    parent,expiry=active()
    thread=os.environ.get('CODEX_THREAD_ID')
    if not thread:raise PermissionError('ACTUAL_THREAD_REQUIRED')
    paths={
        'source_prereg':INPUT.parent/'train-search-batch-v19/PREREGISTRATION.json',
        'source_settlement':INPUT.parent/f'train-search-batch-v19/{NAME}/SETTLEMENT.json',
        'source_receipt':PARENT/f'governance/train_search_batch_v1/{NAME}/confirmation.json',
        'window_release':WINDOW/'WINDOW_RELEASE.json',
        'calendar':WINDOW/'CALENDAR_WINDOW.json',
        'window_manifest':WINDOW/'INPUT_MANIFEST.json',
        'window_ready':WINDOW/'READY.json',
    }
    save(ROOT/'METADATA_ACCESS_V2.json',{'reader_pid':os.getpid(),'reader_thread':thread,
        'recipient':'REQUESTING_USER','purpose':'EXACT_INPUT_SCOPE_AND_DECISION_PREPARATION',
        'at':datetime.now(timezone.utc).isoformat(),
        'files':{k:{'path':str(p),'sha256':sha(p)} for k,p in paths.items()},
        'price_rows_read':False,'new_price_experiments':0})
    data={k:read(p) for k,p in paths.items()}
    frozen=data['source_prereg']['contracts'][NAME]
    receipt=data['source_receipt']
    if stable_hash(frozen) not in receipt['plan']['contracts']:raise PermissionError('SOURCE_CONTRACT_CONFLICT')
    manifest=data['window_manifest'];days=manifest['sessions']
    if days!=[int(d.replace('-','')) for d in data['calendar']['sessions']]:raise PermissionError('CALENDAR_CONFLICT')
    missing=200-sum(d<20250801 for d in days)
    plan={'status':'AWAITING_EXACT_WINDOW_AND_INCREMENT_CONFIRMATION_NOT_EXECUTABLE',
        'version':'WEEKLY_LOW_VOL_FIXED_WINDOW_DECISION_V2','candidate':NAME,
        'source_candidate_contract':frozen,'source_candidate_contract_hash':stable_hash(frozen),
        'objective_id':parent['plan']['objective_id'],'parent_receipt_id':parent['receipt_id'],
        'evaluation_window':[20250801,20260731],
        'warmup':{'sessions_before_start':200,'calendar_metadata_query_bounds':[20240801,20250731],
            'price_start':'Resolve exact first of last200 trading sessions before20250801 from independent calendar; no guessed date',
            'purpose':'INITIALIZATION_ONLY_NO_ACCOUNT_TRADING_OR_PERFORMANCE',
            'existing_sessions_before_start':200-missing,'missing_sessions':missing},
        'existing_input':{'root':str(WINDOW),'manifest_sha256':sha(paths['window_manifest']),
            'input_identity':manifest['input_identity'],'symbols_count':len(manifest['symbols']),
            'materialized_files':manifest['files'],'sessions_count':len(days),
            'old_candidate':data['window_release']['candidate'],'old_grant_not_transferred':True},
        'provider':'EXISTING_BAOSTOCK_PROVIDER_FREE_API_ONLY',
        'acquisition':'Reuse hash-verified window RAW/HFQ, calendar, historical pools/states and adjustment hazards. Acquire missing warmup via query_trade_dates, query_all_stock, query_history_k_data_plus RAW/HFQ and query_adjust_factor for fixed existing window universe, plus the first5 already stored sessions as boundary overlap only. Preserve old responses; require overlap continuity, conflicts stop stitching; no paid data.',
        'warmup_overlap_sessions':days[:5],
        'feasibility_thresholds':{'closed_paths':30,'entry_dates':20,'symbols':2},
        'calendar_only_existing_input_usable_date_upper_bound':date_capacity(days,200),
        'calendar_only_full_warmup_usable_date_upper_bound':date_capacity(days,1),
        'account_scope':'Same frozen strategy, initial10000, Top3, fixed20, original fees/slippage/participation/T+1/FIFO/limits/hazards; only explicit window adapter may differ',
        'new_main_limit':1,'new_confirmed_repair_limit':1,'automatic_repair':False,
        'canonical_increment_registration_required':True,'old_budgets_unchanged':True,
        'data_compute_seconds':21600,'account_compute_seconds':5400,'worker_seconds':900,
        'memory_mib':2048,'concurrency':1,'numeric_threads':1,'expires_at':expiry.isoformat(),
        'output':'EXPLORATORY_FIXED_RULE_EXTERNAL_WINDOW_ACCOUNT_NOT_QUALIFIED',
        'sample_rule':'Report actual closed lots; <30 remains sample limited. Do not combine with TRAIN to fake original pass; keep original TRAIN screen false.',
        'prior_window_exposure':'Other candidate input and no-outcome feasibility already accessed; do not claim globally untouched window or formal statistical independence',
        'information_access':'Exact fixed result and same predeclared descriptive diagnostics to requesting user; record current research-session access; no outcome-driven parameter revision within this plan',
        'still_sealed_from':20260801,'no_legacy_validation_performance':True,
        'price_trials_started':False,'budget_registered':False,
        'READY_FOR_REAL_TRIAL':False,'R1_FULLY_CLOSED':False,'AUTONOMOUS_STRATEGY_GOAL_COMPLETED':False}
    save(ROOT/'PLAN_PROPOSAL_V2.json',plan)
    print({'proposal':str(ROOT/'PLAN_PROPOSAL_V2.json'),'sha256':sha(ROOT/'PLAN_PROPOSAL_V2.json'),
        'symbols':len(manifest['symbols']),'warmup_missing':missing,
        'existing_date_upper_bound':plan['calendar_only_existing_input_usable_date_upper_bound'],
        'full_warmup_date_upper_bound':plan['calendar_only_full_warmup_usable_date_upper_bound']})


if __name__=='__main__':run()
