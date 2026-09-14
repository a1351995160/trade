"""本次用户委托的固定两机制批次；真实输出私有，任何已尝试执行不自动重跑。"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from run_baostock_account_v1 import ROOT as INPUT, PARENT, SOURCE, active, read, sha, save

ROOT = INPUT.parent / 'train-search-batch-v1'
NAMES = ['MOMENTUM_5', 'STABILITY_20']
BATCH = 1
STATEMENT = '我批准你，我只有一个诉求，找到能盈利的为止，不需要任何限制，直接开干'


def code():
    from execute_baostock_account_v1 import code_identity
    return {**code_identity(), **{str(p.relative_to(SOURCE) if p.is_relative_to(SOURCE) else p): sha(p) for p in [
        Path(__file__), SOURCE/'src/chanlun_trader/research_factory/train_search_batch_v1.py',
        SOURCE/'src/chanlun_trader/research_factory/technical_train_signals_v1.py',
        SOURCE/'src/chanlun_trader/chan.py', SOURCE/'docs/TECHNICAL_RESEARCH_SCOPE_V1.md',
        *([SOURCE/f'docs/TRAIN_SEARCH_BATCH_V{BATCH}.md'] if 8<=BATCH<=12 else []),
        *([SOURCE/'src/chanlun_trader/research_factory/technical_followup_signals_v1.py'] if BATCH>=10 else []),
        *([SOURCE/'src/chanlun_trader/research_factory/technical_pattern_signals_v1.py'] if BATCH>=11 else []),
        *([SOURCE/'src/chanlun_trader/research_factory/technical_smoothing_signals_v1.py',
           SOURCE/'scripts/run_train_search_sequence_v1.py',SOURCE/'docs/TRAIN_SEARCH_SEQUENCE_V1.md',
           *([INPUT.parent/'train-search-sequence-v2/PLAN.json'] if BATCH<=15 else [])] if BATCH>=12 else []),
        *([SOURCE/'src/chanlun_trader/research_factory/technical_composite_signals_v1.py',
           SOURCE/'scripts/run_composite_research_v1.py',SOURCE/'docs/TRAIN_SEARCH_BATCH_V16.md'] if BATCH>=16 else []),
        *([SOURCE/'src/chanlun_trader/research_factory/structured_exit_trial_v1.py',
           SOURCE/'scripts/review_failed_composites_v1.py',
           SOURCE/'docs/TRAIN_SEARCH_BATCH_V17.md'] if BATCH>=17 else []),
        *([SOURCE/'src/chanlun_trader/research_factory/weekly_defensive_signals_v1.py',
           SOURCE/'docs/TRAIN_SEARCH_BATCH_V18.md'] if BATCH>=18 else []),
        *([SOURCE/'src/chanlun_trader/research_factory/weekly_fixed_trial_v1.py',
           SOURCE/'docs/TRAIN_SEARCH_BATCH_V19.md'] if BATCH>=19 else []),
        *([SOURCE/'src/chanlun_trader/research_factory/price_volume_patterns_v1.py',
           SOURCE/'docs/TRAIN_SEARCH_BATCH_V20.md'] if BATCH>=20 else []),
        *([SOURCE/'src/chanlun_trader/research_factory/recovery_rotation_signals_v1.py',
           SOURCE/'docs/TRAIN_SEARCH_BATCH_V21.md'] if BATCH>=21 else []),
        *([SOURCE/'src/chanlun_trader/research_factory/volume_flow_signals_v1.py',
           SOURCE/'docs/TRAIN_SEARCH_BATCH_V22.md'] if BATCH>=22 else []),
        *([SOURCE/'src/chanlun_trader/research_factory/market_residual_signals_v1.py',
           SOURCE/'docs/TRAIN_SEARCH_BATCH_V23.md'] if BATCH>=23 else []),
        *([SOURCE/'docs/TRAIN_SEARCH_BATCH_V24.md'] if BATCH>=24 else []),
        *([SOURCE/'docs/TRAIN_SEARCH_BATCH_V25.md'] if BATCH==25 else []),
        *([SOURCE/'docs/TRAIN_SEARCH_BATCH_V26.md',SOURCE/'src/chanlun_trader/research_factory/return_path_signals_v1.py'] if BATCH>=26 else []),
        *([SOURCE/'docs/TRAIN_SEARCH_BATCH_V27.md',SOURCE/'src/chanlun_trader/research_factory/price_impact_signals_v1.py'] if BATCH>=27 else []),
        *([SOURCE/'docs/TRAIN_SEARCH_BATCH_V28.md',SOURCE/'src/chanlun_trader/research_factory/market_sensitivity_signals_v1.py'] if BATCH>=28 else []),
        *([SOURCE/'docs/TRAIN_SEARCH_BATCH_V29.md',SOURCE/'src/chanlun_trader/research_factory/skill_alpha_signals_v1.py',
           *[Path('C:/Users/84219/.codex/skills/joinquant-strategy')/x for x in ('SKILL.md','reference.md','data/Alpha101.md')]] if BATCH>=29 else []),
        *([SOURCE/'docs/TRAIN_SEARCH_BATCH_V30.md',SOURCE/'src/chanlun_trader/research_factory/lag_response_signals_v1.py'] if BATCH>=30 else []),
        *([SOURCE/'docs/TRAIN_SEARCH_BATCH_V31.md',SOURCE/'src/chanlun_trader/research_factory/response_confirmation_signals_v1.py'] if BATCH>=31 else []),
        *([SOURCE/'docs/TRAIN_SEARCH_BATCH_V32.md',SOURCE/'src/chanlun_trader/research_factory/shock_consolidation_signals_v1.py'] if BATCH>=32 else []),
        *([SOURCE/'docs/TRAIN_SEARCH_BATCH_V33.md',SOURCE/'src/chanlun_trader/research_factory/alpha191_pressure_signals_v1.py',Path('C:/Users/84219/.codex/skills/joinquant-strategy/data/Alpha191.md')] if BATCH>=33 else []),
        *([SOURCE/'docs/TRAIN_SEARCH_BATCH_V34.md',SOURCE/'docs/TRAIN_TURNOVER_HYPOTHESES_V1.md',SOURCE/'src/chanlun_trader/research_factory/turnover_regime_signals_v1.py',SOURCE/'scripts/materialize_train_turnover_v1.py',SOURCE/'scripts/prepare_train_valuation_input_v1.py',SOURCE/'scripts/resume_train_valuation_input_v1.py',SOURCE/'docs/TRAIN_VALUATION_RECOVERY_V1.md'] if BATCH>=34 else []),
        *([SOURCE/'docs/TRAIN_SEARCH_BATCH_V35.md',SOURCE/'docs/ASHARE_CALENDAR_MECHANISM_EVIDENCE_V1.md',SOURCE/'src/chanlun_trader/research_factory/calendar_recovery_signals_v1.py'] if BATCH>=35 else []),
        *([SOURCE/'docs/TRAIN_SEARCH_BATCH_V36.md',SOURCE/'src/chanlun_trader/research_factory/affordable_portfolio_signals_v1.py'] if BATCH>=36 else []),
        *([SOURCE/'docs/TRAIN_SEARCH_BATCH_V37.md',SOURCE/'src/chanlun_trader/research_factory/opening_pressure_signals_v1.py'] if BATCH>=37 else []),
        *([SOURCE/'docs/TRAIN_SEARCH_BATCH_V38.md',SOURCE/'src/chanlun_trader/research_factory/scale_proxy_signals_v1.py'] if BATCH>=38 else []),
        *([SOURCE/'docs/TRAIN_SEARCH_BATCH_V39.md',SOURCE/'src/chanlun_trader/research_factory/participation_stability_signals_v1.py'] if BATCH>=39 else []),
        *([SOURCE/'scripts/batch39_input_recovery_v1.py'] if BATCH>=39 else []),
        *([SOURCE/'docs/TRAIN_SEARCH_BATCH_V40.md',SOURCE/'src/chanlun_trader/research_factory/trend_risk_horizon_signals_v1.py'] if BATCH>=40 else []),
        *([SOURCE/'docs/TRAIN_SEARCH_BATCH_V41.md',SOURCE/'src/chanlun_trader/research_factory/breadth_regime_signals_v1.py'] if BATCH>=41 else []),
        *([SOURCE/'docs/TRAIN_SEARCH_BATCH_V42.md',SOURCE/'src/chanlun_trader/research_factory/stock_trend_only_signals_v1.py'] if BATCH>=42 else []),
        *([SOURCE/'docs/TRAIN_SEARCH_BATCH_V43.md',SOURCE/'src/chanlun_trader/research_factory/failed_low_break_signals_v1.py'] if BATCH>=43 else [])]}}


def reconcile_weak_train_followup():
    """训练通过但原成本描述转亏后的明确继续委托，不改原筛选状态。"""
    if BATCH!=26:raise PermissionError('EXPLICIT_WEAK_TRAIN_FOLLOWUP_ONLY')
    prior=INPUT.parent/'train-search-batch-v25'
    if read(prior/'RUNNER_COMPLETED.json')['status']!='SCREEN_PASS_REVIEWED_NOT_QUALIFIED':
        raise PermissionError('PRIOR_TRAIN_REVIEW_REQUIRED')
    index=read(prior/'DELIVERY_STATUS.json')
    for row in index['candidates']:
        for kind in ('result','settlement'):
            if sha(row['evidence'][kind])!=row['evidence'][kind+'_sha256']:
                raise PermissionError('PRIOR_EVIDENCE_CHANGED')
        review=read(prior/row['candidate']/'robustness-review-v1/SUMMARY.json')
        if (review['original_metrics']!=read(row['evidence']['result'])['metrics'] or
                review['static_cost_sensitivity'][-1]['static_return']>0):
            raise PermissionError('PRIOR_COST_LIMITATION_REQUIRED')


def reconcile_external_followup():
    """第24批训练通过后已有跨期结算；新研究不改写原通过状态。"""
    if BATCH!=25:raise PermissionError('EXPLICIT_EXTERNAL_FOLLOWUP_ONLY')
    prior=read(INPUT.parent/'train-search-batch-v24/RUNNER_COMPLETED.json')
    window=INPUT.parent/'residual-fixed-window-v1'
    final=read(window/'FINAL_STATUS.json');settled=read(window/'ACCOUNT_SETTLEMENT.json')
    if prior['status']!='SCREEN_PASS_REVIEWED_NOT_QUALIFIED' or not all(final.get(k) is True for k in
            ('account_started','account_completed','report_completed')) or not settled['completed']:
        raise PermissionError('EXTERNAL_FOLLOWUP_RECONCILIATION_REQUIRED')
    index=read(window/'RESULTS_INDEX.json')
    for name in ('RESULT_SUMMARY.json','ACCOUNT_SETTLEMENT.json'):
        if str(Path(index[name]['path']).resolve())!=str((window/name).resolve()) or sha(window/name)!=index[name]['sha256']:
            raise PermissionError('EXTERNAL_FOLLOWUP_EVIDENCE_CHANGED')
    summary=read(window/'RESULT_SUMMARY.json')
    if summary['original_metrics']['train_net_return']>=0:
        raise PermissionError('EXPECTED_SETTLED_EXTERNAL_FAILURE_REQUIRED')


def reconcile_confirmation_window_failures():
    """保留31训练通过，核对两固定跨期失败后继续不同机制。"""
    if BATCH!=32:raise PermissionError('EXPLICIT_CONFIRMATION_FOLLOWUP_ONLY')
    prior=INPUT.parent/'train-search-batch-v31'
    if read(prior/'RUNNER_COMPLETED.json')['status']!='SCREEN_PASS_REVIEWED_NOT_QUALIFIED':
        raise PermissionError('PRIOR_TRAIN_REVIEW_REQUIRED')
    from chanlun_trader.research_factory.response_confirmation_signals_v1 import NAMES as names
    for name in names:
        window=INPUT.parent/'response-confirmation-window-v1'/name
        final=read(window/'FINAL_STATUS.json')
        if not all(final.get(k) is True for k in ('account_started','account_completed','report_completed')):
            raise PermissionError('CONFIRMATION_WINDOW_INCOMPLETE')
        index=read(window/'RESULTS_INDEX.json')
        for item in ('ACCOUNT_RESULT.json','ACCOUNT_SETTLEMENT.json','RESULT_SUMMARY.json'):
            if Path(index[item]['path']).resolve()!=(window/item).resolve() or sha(window/item)!=index[item]['sha256']:
                raise PermissionError('CONFIRMATION_WINDOW_EVIDENCE_CHANGED')
        metrics=read(window/'ACCOUNT_RESULT.json')['metrics']
        if (not read(window/'ACCOUNT_SETTLEMENT.json')['completed'] or
                read(window/'RESULT_SUMMARY.json')['original_metrics']!=metrics or metrics['train_net_return']>=0):
            raise PermissionError('SETTLED_CONFIRMATION_FAILURE_REQUIRED')


def reconcile_stock_trend_failure():
    if BATCH!=43:raise PermissionError('EXPLICIT_STOCK_TREND_FOLLOWUP_ONLY')
    root=INPUT.parent/'stock-trend-only-fixed-window-v1'
    if read(INPUT.parent/'train-search-batch-v42/RUNNER_COMPLETED.json')['status']!='SCREEN_PASS_REVIEWED_NOT_QUALIFIED':raise PermissionError('PRIOR_TRAIN_REVIEW_REQUIRED')
    if not all(read(root/'FINAL_STATUS.json').get(k) is True for k in ('account_started','account_completed','report_completed')):raise PermissionError('EXTERNAL_INCOMPLETE')
    index=read(root/'RESULTS_INDEX.json')
    for n in ('ACCOUNT_RESULT.json','ACCOUNT_SETTLEMENT.json','RESULT_SUMMARY.json'):
        if Path(index[n]['path']).resolve()!=(root/n).resolve() or sha(root/n)!=index[n]['sha256']:raise PermissionError('EXTERNAL_EVIDENCE_CHANGED')
    result=read(root/'ACCOUNT_RESULT.json')
    if not read(root/'ACCOUNT_SETTLEMENT.json')['completed'] or read(root/'RESULT_SUMMARY.json')['original_metrics']!=result['metrics'] or result['metrics']['train_net_return']>=0:raise PermissionError('SETTLED_STOCK_TREND_FAILURE_REQUIRED')


def reconcile_breadth_cost_limit():
    if BATCH!=42:raise PermissionError('EXPLICIT_BREADTH_FOLLOWUP_ONLY')
    root=INPUT.parent/'train-search-batch-v41'
    if read(root/'RUNNER_COMPLETED.json')['status']!='SCREEN_PASS_REVIEWED_NOT_QUALIFIED':raise PermissionError('PRIOR_REVIEW_REQUIRED')
    for row in read(root/'DELIVERY_STATUS.json')['candidates']:
        for k in ('result','settlement'):
            if sha(row['evidence'][k])!=row['evidence'][k+'_sha256']:raise PermissionError('PRIOR_RESULT_CHANGED')
        if row['screen_passed']:
            summary=read(root/row['candidate']/'robustness-review-v1/SUMMARY.json')
            if summary['original_metrics']!=read(row['evidence']['result'])['metrics'] or summary['static_cost_sensitivity'][-1]['static_return']>0:raise PermissionError('COST_LIMITATION_REQUIRED')


def reconcile_weak_low_vol_window():
    if BATCH!=41:raise PermissionError('EXPLICIT_LOW_VOL_WINDOW_FOLLOWUP_ONLY')
    root=INPUT.parent/'trend-risk-fixed-window-v1'
    if read(INPUT.parent/'train-search-batch-v40/RUNNER_COMPLETED.json')['status']!='SCREEN_PASS_REVIEWED_NOT_QUALIFIED':raise PermissionError('PRIOR_TRAIN_REVIEW_REQUIRED')
    if not all(read(root/'FINAL_STATUS.json').get(k) is True for k in ('account_started','account_completed','report_completed')):raise PermissionError('EXTERNAL_INCOMPLETE')
    index=read(root/'RESULTS_INDEX.json')
    for n in ('ACCOUNT_RESULT.json','ACCOUNT_SETTLEMENT.json','RESULT_SUMMARY.json'):
        if Path(index[n]['path']).resolve()!=(root/n).resolve() or sha(root/n)!=index[n]['sha256']:raise PermissionError('EXTERNAL_EVIDENCE_CHANGED')
    summary=read(root/'RESULT_SUMMARY.json')
    if not read(root/'ACCOUNT_SETTLEMENT.json')['completed'] or summary['original_metrics']!=read(root/'ACCOUNT_RESULT.json')['metrics'] or summary['static_cost_sensitivity'][-1]['static_return']>0:raise PermissionError('SETTLED_COST_LIMITATION_REQUIRED')


def reconcile_batch39_recovery():
    if BATCH!=40:raise PermissionError('EXPLICIT_BATCH39_RECOVERY_FOLLOWUP_ONLY')
    root=INPUT.parent/'train-search-batch-v39/input-recovery-v1'
    r=read(root/'RECOVERY.json')
    for path,digest in r['original_evidence'].items():
        if sha(path)!=digest:raise PermissionError('ORIGINAL_FAILURE_CHANGED')
    if read(root/'RUNNER_COMPLETED.json')['status']!='COMPOSITE_BATCH_COMPLETED_NO_SCREEN_PASS':raise PermissionError('RECOVERY_NOT_SETTLED_NO_PASS')
    for row in read(root/'DELIVERY_STATUS.json')['candidates']:
        for key in ('result','settlement'):
            if sha(row['evidence'][key])!=row['evidence'][key+'_sha256']:raise PermissionError('RECOVERED_RESULT_CHANGED')
        tally=read(row['evidence']['settlement']);settled=[event for event in tally['events'] if event['event']=='SETTLED']
        if row['screen_passed'] is not False or len(settled)!=1 or settled[0]['completed'] is not True or tally['MAIN_BACKTEST_EXPOSURES_USED']!=1 or tally['REPAIR_BACKTEST_EXPOSURES_USED']!=0:raise PermissionError('RECOVERY_NOT_FAILED_COMPLETE')


def reconcile_scale_failure():
    if BATCH!=39:raise PermissionError('EXPLICIT_SCALE_FOLLOWUP_ONLY')
    window=INPUT.parent/'scale-fixed-window-v1'
    if read(INPUT.parent/'train-search-batch-v38/RUNNER_COMPLETED.json')['status']!='SCREEN_PASS_REVIEWED_NOT_QUALIFIED':raise PermissionError('PRIOR_TRAIN_REVIEW_REQUIRED')
    final=read(window/'FINAL_STATUS.json');index=read(window/'RESULTS_INDEX.json')
    if not all(final.get(k) is True for k in ('account_started','account_completed','report_completed')):raise PermissionError('SCALE_WINDOW_INCOMPLETE')
    for name in ('ACCOUNT_RESULT.json','ACCOUNT_SETTLEMENT.json','RESULT_SUMMARY.json'):
        if Path(index[name]['path']).resolve()!=(window/name).resolve() or sha(window/name)!=index[name]['sha256']:raise PermissionError('SCALE_WINDOW_EVIDENCE_CHANGED')
    result=read(window/'ACCOUNT_RESULT.json');summary=read(window/'RESULT_SUMMARY.json')
    if not read(window/'ACCOUNT_SETTLEMENT.json')['completed'] or summary['original_metrics']!=result['metrics'] or result['metrics']['train_net_return']>=0:raise PermissionError('SETTLED_SCALE_FAILURE_REQUIRED')


def reconcile_turnover_feasibility():
    if BATCH!=36:raise PermissionError('EXPLICIT_TURNOVER_FOLLOWUP_ONLY')
    window=INPUT.parent/'turnover-fixed-window-v1'
    prior=read(INPUT.parent/'train-search-batch-v35/RUNNER_COMPLETED.json')
    final=read(window/'FINAL_STATUS.json');ready=read(window/'READY.json')
    if (prior['status']!='SCREEN_PASS_REVIEWED_NOT_QUALIFIED' or final.get('reason')!='FEASIBILITY_NOT_PASSED'
        or final.get('account_started') is not False or final.get('account_completed') is not False
        or (window/'ACCOUNT_EXECUTION.json').exists() or ready['feasibility_passed'] is not False):
        raise PermissionError('TURNOVER_NO_OUTCOME_RECONCILIATION_REQUIRED')
    if sha(window/'FEASIBILITY.json')!=ready['feasibility_sha256']:raise PermissionError('TURNOVER_FEASIBILITY_CHANGED')
    value=read(window/'FEASIBILITY.json')
    if value['no_outcome'] is not True or value['passed'] is not False or value['counts']!={'closed_paths':23,'entry_dates':16,'symbols':19}:
        raise PermissionError('TURNOVER_FIXED_FAILURE_REQUIRED')


def guard():
    parent, expiry = active()
    frozen = read(ROOT/'PREREGISTRATION.json')
    if frozen['code'] != code():
        raise PermissionError('SEARCH_CODE_FREEZE_CHANGED')
    for p, digest in frozen['inputs'].items():
        if sha(p) != digest:
            raise PermissionError('SEARCH_INPUT_IDENTITY_CHANGED')
    if (ROOT/'revocation.json').exists():
        raise PermissionError('SEARCH_DELEGATION_REVOKED')
    return parent, expiry


def freeze():
    from chanlun_trader.research_factory.common import stable_hash
    from chanlun_trader.research_factory.train_search_batch_v1 import contract, design
    from chanlun_trader.research_factory.novelty import CandidateNoveltyGateV2
    if 12<=BATCH<=15:
        from run_train_search_sequence_v1 import authorize
        authorize(BATCH,NAMES)
    if BATCH==16:
        prior=read(INPUT.parent/'train-search-sequence-v2/COMPLETED.json')
        if prior['status']!='FIXED_QUEUE_EXHAUSTED_NO_ROBUST_CANDIDATE':
            raise PermissionError('PREVIOUS_SEQUENCE_REQUIRES_RECONCILIATION')
    if BATCH==43:
        reconcile_stock_trend_failure()
    elif BATCH==42:
        reconcile_breadth_cost_limit()
    elif BATCH==41:
        reconcile_weak_low_vol_window()
    elif BATCH==40:
        reconcile_batch39_recovery()
    elif BATCH==39:
        reconcile_scale_failure()
    elif BATCH==36:
        reconcile_turnover_feasibility()
    elif BATCH==32:
        reconcile_confirmation_window_failures()
    elif BATCH==26:
        reconcile_weak_train_followup()
    elif BATCH==25:
        reconcile_external_followup()
    elif BATCH>=17:
        prior=read(INPUT.parent/f'train-search-batch-v{BATCH-1}/RUNNER_COMPLETED.json')
        if prior['status']!='COMPOSITE_BATCH_COMPLETED_NO_SCREEN_PASS':
            raise PermissionError('COMPOSITE_FAILURE_RECONCILIATION_REQUIRED')
    if (ROOT/'PREREGISTRATION.json').exists():
        guard()
        return
    parent, expiry = active()
    thread = os.environ.get('CODEX_THREAD_ID')
    if not thread:
        raise PermissionError('ACTUAL_THREAD_REQUIRED')
    history = read(INPUT/'NOVELTY.json')
    comparison = [*history['comparison_design_records'], history['candidate']]
    for earlier_batch in range(1,BATCH):
        earlier = read(INPUT.parent/f'train-search-batch-v{earlier_batch}/PREREGISTRATION.json')
        comparison += [design(name) for name in earlier['contracts']]
    decisions = {}
    for name in NAMES:
        decisions[name] = CandidateNoveltyGateV2().evaluate(design(name),
            historical_candidates=comparison,
            same_batch_candidates=[design(other) for other in NAMES if other != name]).to_dict()
    source = {'origin': 'USER_EXPLICIT_PLAN_APPROVAL_VIA_CODEX', 'thread_id': thread,
              'approval_statement': STATEMENT, 'recorded_at': datetime.now(timezone.utc).isoformat(),
              'technical_research_steering': '缠论的知识也用，kdj，macd等指标；不止我说的这些指标，应该还有很多其他指标的，技术指标都用上，不局限于我和你说的',
              'interpretation': 'DELEGATED_RESEARCH; BATCH_SIZE_CHOSEN_BY_AGENT; NO_PER_CANDIDATE_HUMAN_APPROVAL',
              'scope': 'EXISTING_TRAIN_ONLY_NO_TRADING_NO_PAID_DATA'}
    if (ROOT/'APPROVAL_SOURCE.json').exists():
        prior_source=read(ROOT/'APPROVAL_SOURCE.json')
        if any(prior_source.get(key)!=value for key,value in source.items() if key!='recorded_at'):
            raise PermissionError('EXISTING_APPROVAL_SOURCE_CONFLICT')
    else:
        save(ROOT/'APPROVAL_SOURCE.json', source)
    paths = [INPUT/p for p in ['INPUT_MANIFEST.json','INPUT_READY.json','DAILY.parquet',
                              'STATES.parquet','FEATURES.parquet','NOVELTY.json']]
    for earlier_batch in range(1,BATCH):
        paths.append(INPUT.parent/f'train-search-batch-v{earlier_batch}/PREREGISTRATION.json')
    if BATCH>=17:
        learning=INPUT.parent/'train-search-batch-v39/input-recovery-v1/failure-learning-v1' if BATCH==40 else INPUT.parent/f'train-search-batch-v{BATCH-1}/failure-learning-v1'
        paths += [learning/name for name in ['SUMMARY.json','RULE.json']]
    if BATCH==25:
        paths += [INPUT.parent/'residual-fixed-window-v1'/name for name in
            ['FINAL_STATUS.json','ACCOUNT_SETTLEMENT.json','RESULTS_INDEX.json','RESULT_SUMMARY.json','FINAL_DELIVERY_ACCESS.json']]
    if BATCH==26:
        previous=INPUT.parent/'train-search-batch-v25'
        paths += [previous/'RUNNER_COMPLETED.json',previous/'DELIVERY_STATUS.json']
        for name in read(previous/'PREREGISTRATION.json')['contracts']:
            paths += [previous/name/'robustness-review-v1'/p for p in ('RULE.json','SUMMARY.json')]
    if BATCH==32:
        from chanlun_trader.research_factory.response_confirmation_signals_v1 import NAMES as prior_names
        for name in prior_names:
            paths += [INPUT.parent/'response-confirmation-window-v1'/name/p for p in
                ('FINAL_STATUS.json','ACCOUNT_RESULT.json','ACCOUNT_SETTLEMENT.json','RESULTS_INDEX.json','RESULT_SUMMARY.json','FINAL_DELIVERY_ACCESS.json')]
    if BATCH==36:
        paths += [INPUT.parent/'turnover-fixed-window-v1'/p for p in ('FINAL_STATUS.json','READY.json','FEASIBILITY.json','NO_OUTCOME_INDEX.json','FEASIBILITY_REVIEW_ACCESS.json')]
    if BATCH==43:
        paths += [INPUT.parent/'stock-trend-only-fixed-window-v1'/p for p in ('FINAL_STATUS.json','ACCOUNT_RESULT.json','ACCOUNT_SETTLEMENT.json','RESULTS_INDEX.json','RESULT_SUMMARY.json','FINAL_DELIVERY_ACCESS.json')]
    if BATCH>=34:
        supplemental=INPUT.parent/'train-valuation-input-v1/turnover-input-v1'
        ready=read(supplemental/'READY.json')
        if ready['status']!='TURNOVER_DATED_HISTORY_SCHEMA_READY_NOT_QUALIFIED' or sha(supplemental/'TURNOVER.parquet')!=ready['output']['sha256']:
            raise PermissionError('TURNOVER_SUPPLEMENT_NOT_READY_OR_CHANGED')
        paths += [supplemental/p for p in ('READY.json','TURNOVER.parquet','COVERAGE.json','PREREGISTRATION.json')]
    save(ROOT/'PREREGISTRATION.json', {'version':f'TRAIN_SEARCH_BATCH_V{BATCH}',
        'contracts':{name:contract(name) for name in NAMES}, 'novelty':decisions,
        'code':code(), 'inputs':{str(p):sha(p) for p in paths},
        'approval_sha256':sha(ROOT/'APPROVAL_SOURCE.json'),
        'objective_id':parent['plan']['objective_id'], 'expires_at':expiry.isoformat(),
        'batch_main_count':len(NAMES),'reserved_repair_slots':len(NAMES),'worker_seconds':900,'memory_mib':2048,
        'numeric_threads':1,'batch_compute_seconds':5400,
        'exploratory_screen':'COMPLETE_AND_TRAIN_NET_RETURN_GT_0_AND_CLOSED_LOTS_GE_30',
        'qualification':'NOT_FOR_QUALIFICATION; NO_P_Q; NO_VALIDATION_FINAL_TEST',
        'feedback':'ALL_CANDIDATES_BINARY_SCREEN_AND_FAILURE_REASON_ONLY',
        'historical_train_exposure':True,'historical_novelty_semantics_incomplete':True,
        **({'historical_design_learning':f'EXACT_BATCH{BATCH-1}_RESULTS_EXPOSED_WITH_CURRENT_USER_PERMISSION',
            'post_settlement_learning':'ALL_STORED_METRICS_WITH_EXPLICIT_ACCESS_RECORD'} if BATCH>=17 else {}),
        'stability_proxy':'20 overlapping 5-session price changes; NOT daily volatility',
        'selection':'negative score ascending; original Top3/account/fees/hazards; explicit contract holding_sessions',
        'missing':'reindex independent calendar; no fill; retain all-date computability diagnostics',
        'identity':stable_hash([contract(n) for n in NAMES])})


def service(name):
    from chanlun_trader.research_factory.train_search_batch_v1 import TrainSearchGovernanceV1
    return TrainSearchGovernanceV1(PARENT, name)


def bundle(name, preparing=False):
    from prepare_baostock_account_v1 import load_bundle
    from chanlun_trader.research_factory.common import stable_hash
    from chanlun_trader.research_factory.train_search_batch_v1 import contract
    import pandas as pd
    value = load_bundle()
    if not preparing:
        manifest = read(ROOT/name/'INPUT.json')
        if sha(ROOT/name/'FEATURES.parquet') != manifest['features_sha256']:
            raise PermissionError('SEARCH_FEATURES_CHANGED')
        value.ready_factors = pd.read_parquet(ROOT/name/'FEATURES.parquet')
        value.input_identity = manifest['input_identity']
        value.factor_identity = manifest['features_sha256']
    value.contract_identity = stable_hash(contract(name))
    return value


def prepare(name):
    import pandas as pd
    from chanlun_trader.research_factory.common import stable_hash
    from chanlun_trader.research_factory.train_search_batch_v1 import transform, contract
    from chanlun_trader.research_factory.degraded_input_v2 import check_feasibility
    from chanlun_trader.research_factory.technical_train_signals_v1 import FORMULAS as TECHNICAL
    frozen = read(ROOT/'PREREGISTRATION.json')
    if not frozen['novelty'][name]['allowed']:
        save(ROOT/name/'REJECTED.json', frozen['novelty'][name])
        return
    value = bundle(name, preparing=True)
    if contract(name).get('market_gate') or contract(name).get('market_context'):
        market = value.ready_factors.groupby('timestamp',observed=True).agg(
            market_median=('value','median'),market_available_at=('effective_available_at','max'))
        value.ready_factors = value.ready_factors.join(market,on='timestamp')
    from chanlun_trader.research_factory.turnover_regime_signals_v1 import ALL_NAMES as TURNOVER_NAMES, context as turnover_context
    if name in (*TURNOVER_NAMES,'AFFORDABLE_LOW_TURNOVER_WEEKLY_MOMENTUM_HOLD_20'):
        supplemental=INPUT.parent/'train-valuation-input-v1/turnover-input-v1'
        ready=read(supplemental/'READY.json')
        if ready['status']!='TURNOVER_DATED_HISTORY_SCHEMA_READY_NOT_QUALIFIED' or sha(supplemental/'TURNOVER.parquet')!=ready['output']['sha256']:
            raise PermissionError('TURNOVER_SUPPLEMENT_CHANGED')
        turns=pd.read_parquet(supplemental/'TURNOVER.parquet',columns=['symbol','timestamp','turn'])
        value.ready_factors=turnover_context(value.ready_factors,turns,value.calendar)
        save(ROOT/name/'TURNOVER_CONTEXT.json',{'input_identity':ready['input_identity'],'time_model':ready['timing_model'],'financial_fields_used':False,'cohort_min_members':100})
    from chanlun_trader.research_factory.breadth_regime_signals_v1 import NAMES as BREADTH_NAMES, context as breadth_context
    if name in BREADTH_NAMES:value.ready_factors=breadth_context(value.ready_factors,value.calendar)
    from chanlun_trader.research_factory.scale_proxy_signals_v1 import NAMES as SCALE_NAMES, context as scale_context
    if name in SCALE_NAMES:
        supplemental=INPUT.parent/'train-valuation-input-v1/turnover-input-v1'
        ready=read(supplemental/'READY.json')
        if sha(supplemental/'TURNOVER.parquet')!=ready['output']['sha256']:raise PermissionError('SCALE_TURNOVER_INPUT_CHANGED')
        turns=pd.read_parquet(supplemental/'TURNOVER.parquet',columns=['symbol','timestamp','turn'])
        value.ready_factors=scale_context(value.ready_factors,value.daily,turns)
        save(ROOT/name/'SCALE_CONTEXT.json',{'input_identity':ready['input_identity'],'meaning':'DERIVED_SCALE_SORT_PROXY_NOT_VENDOR_CAPITALIZATION','denominator_vintage':'NOT_PIT_ATTESTED','cohort_min_members':100})
    from chanlun_trader.research_factory.alpha191_pressure_signals_v1 import VOLUME as A191_VOLUME
    from chanlun_trader.research_factory.shock_consolidation_signals_v1 import NAMES as SHOCK_NAMES
    from chanlun_trader.research_factory.price_volume_patterns_v1 import NAMES as PRICE_VOLUME_NAMES
    from chanlun_trader.research_factory.volume_flow_signals_v1 import NAMES as FLOW_NAMES
    from chanlun_trader.research_factory.skill_alpha_signals_v1 import DIVERGENCE as ALPHA_VOLUME
    from chanlun_trader.research_factory.participation_stability_signals_v1 import NAMES as PARTICIPATION_NAMES
    if name in (*SHOCK_NAMES,*PRICE_VOLUME_NAMES,*FLOW_NAMES,*PARTICIPATION_NAMES) or name in (ALPHA_VOLUME,A191_VOLUME):
        value.ready_factors=value.ready_factors.merge(value.daily[['symbol','date','volume']],
            left_on=['symbol','timestamp'],right_on=['symbol','date'],how='left',validate='one_to_one')
    from chanlun_trader.research_factory.price_impact_signals_v1 import NAMES as IMPACT_NAMES
    if name.removesuffix('_HOLD_20') == 'LIQUIDITY_20' or name=='SQUEEZE_TREND_TURNOVER_HOLD_20' or name in IMPACT_NAMES:
        value.ready_factors = value.ready_factors.merge(
            value.daily[['symbol','date','amount']],left_on=['symbol','timestamp'],
            right_on=['symbol','date'],how='left',validate='one_to_one')
    save(ROOT/name/'ACCESS.json', {'reader':os.getpid(),'recipient':'EVALUATION_SIDE',
        'purpose':'FROZEN_SIGNAL_AND_NO_OUTCOME_FEASIBILITY','inputs':frozen['inputs'],
        'new_information_access':True,'price_performance_exposure':False})
    groups, diagnostics = [], []
    raw_groups = value.daily.groupby('symbol',observed=True).indices if name in TECHNICAL else {}
    manifest = read(INPUT/'INPUT_MANIFEST.json') if name in TECHNICAL else {}
    for symbol, rows in value.ready_factors.groupby('symbol', observed=True, sort=True):
        if name in TECHNICAL:
            from run_baostock_account_v1 import response_directory
            from chanlun_trader.research_factory.technical_train_signals_v1 import adjusted_rows
            path = response_directory(symbol,'1',INPUT)/'1.json'
            expected = manifest['inputs'].get(str(path))
            if expected is None or sha(path)!=expected:
                raise PermissionError('HFQ_SOURCE_NOT_BOUND_OR_CHANGED')
            save(ROOT/name/'source-access'/f'{symbol}.json',{'path':str(path),'sha256':expected,
                'reader_pid':os.getpid(),'recipient':'EVALUATION_SIDE','purpose':'FROZEN_TECHNICAL_SIGNAL',
                'at':datetime.now(timezone.utc).isoformat()})
            rows = adjusted_rows(rows,value.daily.iloc[raw_groups[symbol]],read(path)['rows'])
        transformed = transform(rows, value.calendar, name)
        diagnostics.append(transformed[['symbol','timestamp','computable']])
        groups.append(transformed.loc[transformed.computable].drop(columns='computable'))
    factors = pd.concat(groups, ignore_index=True)
    directory = ROOT/name
    for frame, filename in [(factors,'FEATURES.parquet'),
                             (pd.concat(diagnostics,ignore_index=True),'COMPUTABILITY.parquet')]:
        if (directory/filename).exists():
            raise PermissionError('NO_DERIVED_INPUT_OVERWRITE')
        frame.to_parquet(directory/filename,index=False)
    feature_hash = sha(directory/'FEATURES.parquet')
    input_id = stable_hash([value.input_identity, feature_hash, contract(name)])
    save(directory/'INPUT.json', {'input_identity':input_id,'features_sha256':feature_hash,
        'parent_input_identity':value.input_identity,'computability_sha256':sha(directory/'COMPUTABILITY.parquet')})
    value.ready_factors = factors
    value.input_identity = input_id
    value.factor_identity = feature_hash
    paths = []
    exit_offset = contract(name)['holding_sessions'] + 2
    for day, rows in factors.groupby('timestamp'):
        i = value.calendar.index(int(day))
        for rank, row in enumerate(rows[rows.value<0].sort_values(['value','symbol']).head(3).itertuples(),1):
            paths.append({'symbol':row.symbol,'signal_session':int(day),'rank':rank,
                'entry_date':value.calendar[i+1],
                'exit_date':value.calendar[i+exit_offset] if i+exit_offset<len(value.calendar) else None})
    feasibility = check_feasibility(value,candidate_paths=paths)
    save(directory/'FEASIBILITY.json',json.loads(json.dumps(feasibility,default=str)))
    ready = {**read(INPUT/'INPUT_READY.json'),'input_identity':input_id,
        'status':'READY' if feasibility['passed'] else 'NOT_READY',
        'feasibility_passed':feasibility['passed'], 'novelty_decision':frozen['novelty'][name],
        'feasibility_sha256':sha(directory/'FEASIBILITY.json')}
    save(directory/'READY.json',ready)


def worker(name, execution_id):
    from chanlun_trader.research_factory.train_search_batch_v1 import contract
    from chanlun_trader.research_factory.degraded_execution_v2 import _run_account
    from chanlun_trader.research_factory.train_account_runner_v1 import TrainingAccountExecutionFailure
    value = bundle(name)
    governance = service(name)
    context = read(ROOT/name/'EXECUTION.json')
    if context['execution_id'] != execution_id:
        raise PermissionError('EXECUTION_IDENTITY_CHANGED')
    def check():
        if (ROOT/'revocation.json').exists():
            raise PermissionError('SEARCH_REVOKED')
        return governance.active()
    check()
    governance.start_exposure(execution_id)
    try:
        result = _run_account(value,(context['commit'],context['dirty']),check,contract(name))
    except TrainingAccountExecutionFailure as exc:
        save(ROOT/name/'ENGINE_FAILURE.json',json.loads(json.dumps(exc.evidence,default=str)))
        raise
    save(ROOT/name/'RESULT.json',json.loads(json.dumps(result,default=str,allow_nan=False)))
    metrics = result['metrics']
    passed = (result['status']=='COMPLETE' and metrics is not None and
              metrics['train_net_return']>0 and metrics['closed_lots']>=30)
    save(ROOT/name/'FEEDBACK.json',{'candidate':name,'status':result['status'],
        'screen_passed':passed,'meaning':'TRAIN_ONLY_EXPLORATORY_NOT_QUALIFIED',
        'result_sha256':sha(ROOT/name/'RESULT.json'),'exact_metrics_access_by_design':False})


def bounded(stage, name, execution_id=None):
    from chanlun_trader.synthetic_batch_resources import run_bounded_worker
    _, expiry = guard()
    used = sum(read(p)['elapsed_seconds'] for p in (ROOT/'resources').glob('*.completed.json')) + read(ROOT/'PREREGISTRATION.json').get('historical_worker_seconds',0)
    limit = min(900,5400-used,(expiry-datetime.now(timezone.utc)).total_seconds())
    if 12<=BATCH<=15:
        from run_train_search_sequence_v1 import remaining
        limit=min(limit,remaining())
    if limit<=0:
        raise PermissionError('BATCH_RESOURCE_EXHAUSTED')
    context = {'stage':stage,'candidate':name,'execution_id':execution_id}
    args = [sys.executable,str(Path(__file__)),'--batch',str(BATCH),'--stage',stage,'--name',name]
    if execution_id:
        args += ['--execution-id',execution_id]
    env = {**os.environ,'PYTHONPATH':str(SOURCE/'src'),'PYTHONDONTWRITEBYTECODE':'1',
           **{k:'1' for k in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']}}
    started = time.monotonic()
    result = run_bounded_worker(args,root=SOURCE,memory_mib=2048,wall_seconds=limit,
        environment=env,execution=context,
        on_started=lambda pid:save(ROOT/'resources'/f'{name}-{stage}.started.json',{'pid':pid,**context}))
    elapsed = time.monotonic()-started
    save(ROOT/'resources'/f'{name}-{stage}.completed.json',{'elapsed_seconds':elapsed,
         **{k:v.decode('utf-8',errors='replace') if isinstance(v,bytes) else v for k,v in result.items()}})
    return result,elapsed


def run():
    from chanlun_trader.research_factory.common import stable_hash
    from chanlun_trader.research_factory.train_search_batch_v1 import contract
    freeze()
    for name in NAMES:
        directory = ROOT/name
        if (directory/'FEEDBACK.json').exists():
            continue
        if not (directory/'READY.json').exists():
            if (ROOT/'resources'/f'{name}-prepare.started.json').exists():
                raise PermissionError('PREPARATION_ATTEMPT_REQUIRES_RECONCILIATION')
            result,_ = bounded('prepare',name)
            if result['returncode'] or (directory/'REJECTED.json').exists():
                continue
        ready = read(directory/'READY.json')
        if not ready['feasibility_passed']:
            continue
        parent,expiry = guard()
        frozen = contract(name)
        plan = {'contracts':{stable_hash(frozen):frozen},'limit':2,'wall_limit':1800,
            'result_type':frozen['result_type'],'input_identity':ready['input_identity'],
            'objective_id':parent['plan']['objective_id'],'expires_at':expiry.isoformat()}
        source = {**read(ROOT/'APPROVAL_SOURCE.json'),'approval_record_sha256':sha(ROOT/'APPROVAL_SOURCE.json'),
                  'delegated_preregistration_sha256':sha(ROOT/'PREREGISTRATION.json')}
        governance = service(name)
        governance.confirm(plan,source,preflight=lambda:ready)
        reservation = governance.reserve(stable_hash(frozen))
        if reservation['status']!='RESERVED':
            raise PermissionError('EXISTING_ATTEMPT_NO_REPLAY')
        eid = reservation['execution_id']
        save(directory/'EXECUTION.json',{'execution_id':eid,
            'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=SOURCE,text=True).strip(),
            'dirty':bool(subprocess.check_output(['git','status','--porcelain'],cwd=SOURCE,text=True).strip())})
        started = time.monotonic()
        completed = False
        try:
            result,_ = bounded('account',name,eid)
            completed = result['returncode']==0 and (directory/'FEEDBACK.json').exists()
        finally:
            governance.settle(eid,time.monotonic()-started,completed)
        save(directory/'SETTLEMENT.json',governance.summary())
    print(json.dumps({name:read(ROOT/name/'FEEDBACK.json') if (ROOT/name/'FEEDBACK.json').exists()
                      else {'status':'NOT_COMPLETED_SEE_RECEIPTS'} for name in NAMES}))


if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--batch',type=int,choices=list(range(1,44)),default=1)
    parser.add_argument('--stage',choices=['prepare','account'])
    parser.add_argument('--name',choices=['MOMENTUM_5','STABILITY_20','MOMENTUM_60','LIQUIDITY_20',
                                        'STABILITY_20_HOLD_20','LIQUIDITY_20_HOLD_20',
                                        'MOMENTUM_60_HOLD_20_MARKET_5','STABILITY_20_HOLD_20_MARKET_5',
                                        'MACD_CROSS_HOLD_20','KDJ_OVERSOLD_CROSS_HOLD_20',
                                        'CHAN_BOTTOM_MACD_HOLD_20','MONTHLY_REVERSAL_HOLD_20',
                                        'HIGH_252_HOLD_20','LOW_MAX_20_HOLD_20',
                                        'BOLL_REENTRY_HOLD_20','DONCHIAN_55_BREAKOUT_HOLD_20',
                                        'RSI_14_RECLAIM_HOLD_20','AROON_25_CROSS_HOLD_20',
                                        'CCI_20_TREND_HOLD_20','KAMA_10_CROSS_HOLD_20',
                                        'RSI2_TREND_200_HOLD_20','BULL_ENGULFING_DOWN_5_HOLD_20',
                                        'SMA_50_200_CROSS_HOLD_20','TRIX_15_ZERO_CROSS_HOLD_20',
                                        'HAMMER_DOWN_5_HOLD_20','THREE_SOLDIERS_HOLD_20',
                                        'ATR_14_UP_BREAKOUT_HOLD_20','INSIDE_BAR_UP_HOLD_20',
                                        'SMA20_PULLBACK_200_HOLD_20','ROC20_ACCEL_HOLD_20',
                                        'ADX_EMA_PULLBACK_HOLD_20','SQUEEZE_TREND_TURNOVER_HOLD_20',
                                        'ADX_EMA_INVALIDATION_HOLD_20','ADX_EMA_INVALIDATION_MARKET_HOLD_20',
                                        'WEEKLY_LOW_VOL_EMA_EXIT_HOLD_20','WEEKLY_MOM_RISK_EMA_EXIT_HOLD_20',
                                        'WEEKLY_LOW_VOL_FIXED_HOLD_20','WEEKLY_LOW_VOL_FIXED_MARKET_HOLD_20',
                                        'RANGE_BREAK_RETEST_VOLUME_HOLD_20','RANGE_SPRING_LOW_VOLUME_HOLD_20',
                                        'RSI2_RECOVERY_SMA5_HOLD_20','SKIP5_MOMENTUM_PULLBACK_HOLD_20',
                                        'OBV20_TREND_RECOVERY_HOLD_20','AD20_CONTRACTION_BREAK_HOLD_20',
                                        'MARKET_PROXY_RESIDUAL_REVERSAL_HOLD_20','MARKET_PROXY_RESIDUAL_PERSISTENCE_HOLD_20',
                                        'MARKET_PROXY_Z_REVERSAL_GATE_HOLD_20','MARKET_PROXY_LOW_RESIDUAL_RISK_GATE_HOLD_20',
                                        'MARKET_PROXY_Z_RECOVERY_EXIT_HOLD_20','MARKET_PROXY_Z_RECOVERY_MARKET_EXIT_HOLD_20',
                                        'WEEKLY_OVERNIGHT_SUPPORT_HOLD_20','WEEKLY_GRADUAL_ADVANCE_HOLD_20','WEEKLY_ILLIQUIDITY_PREMIUM_HOLD_20','WEEKLY_IMPACT_IMPROVEMENT_HOLD_20','WEEKLY_LOW_PROXY_BETA_GATE_HOLD_20','WEEKLY_LOW_DOWNSIDE_PROXY_BETA_GATE_HOLD_20','WEEKLY_ALPHA009_REGIME_GATE_HOLD_3','WEEKLY_ALPHA006_PULLBACK_GATE_HOLD_3','WEEKLY_SELF_REVERSION_FORECAST_HOLD_3','WEEKLY_MARKET_LAG_FORECAST_HOLD_3','RESIDUAL_SELF_REVERSION_CONFIRM_HOLD_20','RESIDUAL_MARKET_LAG_CONFIRM_HOLD_20','LARGE_UP_INSIDE_BREAK_GATE_HOLD_3','LARGE_UP_THREE_DAY_SUPPORT_GATE_HOLD_3','ALPHA003_PRESSURE_RECLAIM_GATE_HOLD_3','ALPHA040_VOLUME_PULLBACK_GATE_HOLD_3','LOW_TURNOVER_WEEKLY_MOMENTUM_HOLD_3','HIGH_TURNOVER_WEEKLY_RECOVERY_HOLD_3','LOW_TURNOVER_WEEKLY_MOMENTUM_HOLD_20','MONTH_START_LOW_RISK_RECOVERY_HOLD_3','AFFORDABLE_LOW_TURNOVER_WEEKLY_MOMENTUM_HOLD_20','AFFORDABLE_WEEKLY_LOW_VOL_MARKET_HOLD_20','WEEKLY_OPENING_SELL_FREQUENCY_HOLD_20','WEEKLY_OPENING_SELL_MAGNITUDE_HOLD_20','WEEKLY_SMALL_SCALE_TREND_HOLD_20','WEEKLY_MID_SCALE_LOW_RISK_HOLD_20','WEEKLY_VOLUME_STABILITY_TREND_HOLD_20','WEEKLY_VOLUME_STABILITY_DOWNSIDE_HOLD_20','WEEKLY_LOW_VOL_TREND60_FIXED_HOLD_20','WEEKLY_LOW_DRAWDOWN_TREND60_FIXED_HOLD_20','WEEKLY_LOW_VOL_BREADTH_PERSISTENCE_HOLD_20','WEEKLY_LOW_VOL_BREADTH_RECOVERY_HOLD_20','WEEKLY_LOW_VOL_STOCK_TREND_ONLY_HOLD_20','FAILED_LOW20_SAME_CLOSE_GATE_HOLD_3','FAILED_LOW20_NEXT_CLOSE_GATE_HOLD_3'])
    parser.add_argument('--execution-id')
    options = parser.parse_args()
    BATCH = options.batch
    if BATCH == 2:
        ROOT = INPUT.parent/'train-search-batch-v2'
        NAMES = ['MOMENTUM_60','LIQUIDITY_20']
    elif BATCH == 3:
        ROOT = INPUT.parent/'train-search-batch-v3'
        NAMES = ['STABILITY_20_HOLD_20','LIQUIDITY_20_HOLD_20']
    elif BATCH == 4:
        ROOT = INPUT.parent/'train-search-batch-v4'
        NAMES = ['MOMENTUM_60_HOLD_20_MARKET_5','STABILITY_20_HOLD_20_MARKET_5']
    elif BATCH in (5,6,7):
        ROOT = INPUT.parent/f'train-search-batch-v{BATCH}'
        NAMES = {5:['MACD_CROSS_HOLD_20','KDJ_OVERSOLD_CROSS_HOLD_20'],
                 6:['CHAN_BOTTOM_MACD_HOLD_20','MONTHLY_REVERSAL_HOLD_20'],
                 7:['HIGH_252_HOLD_20','LOW_MAX_20_HOLD_20']}[BATCH]
    elif BATCH==8:
        ROOT=INPUT.parent/'train-search-batch-v8'
        NAMES=['BOLL_REENTRY_HOLD_20','DONCHIAN_55_BREAKOUT_HOLD_20']
        STATEMENT='按你说的合理顺序来处理，然后继续研究吧'
    elif BATCH==9:
        ROOT=INPUT.parent/'train-search-batch-v9'
        NAMES=['RSI_14_RECLAIM_HOLD_20','AROON_25_CROSS_HOLD_20']
        STATEMENT='那继续找吧，找到能用的为止'
    elif BATCH==10:
        ROOT=INPUT.parent/'train-search-batch-v10'
        NAMES=['CCI_20_TREND_HOLD_20','KAMA_10_CROSS_HOLD_20']
        STATEMENT='那继续找吧，找到能用的为止'
    elif BATCH==11:
        ROOT=INPUT.parent/'train-search-batch-v11'
        NAMES=['RSI2_TREND_200_HOLD_20','BULL_ENGULFING_DOWN_5_HOLD_20']
        STATEMENT='那继续找吧，找到能用的为止'
    elif BATCH==43:
        from chanlun_trader.research_factory.failed_low_break_signals_v1 import NAMES as NEXT_NAMES
        ROOT=INPUT.parent/'train-search-batch-v43'
        NAMES=list(NEXT_NAMES)
        STATEMENT='接着找，不要停下来，停下来的条件就是找到盈利策略'
    elif BATCH==42:
        from chanlun_trader.research_factory.stock_trend_only_signals_v1 import NAMES as NEXT_NAMES
        ROOT=INPUT.parent/'train-search-batch-v42'
        NAMES=list(NEXT_NAMES)
        STATEMENT='接着找，不要停下来，停下来的条件就是找到盈利策略'
    elif BATCH==41:
        from chanlun_trader.research_factory.breadth_regime_signals_v1 import NAMES as NEXT_NAMES
        ROOT=INPUT.parent/'train-search-batch-v41'
        NAMES=list(NEXT_NAMES)
        STATEMENT='接着找，不要停下来，停下来的条件就是找到盈利策略'
    elif BATCH==40:
        from chanlun_trader.research_factory.trend_risk_horizon_signals_v1 import NAMES as NEXT_NAMES
        ROOT=INPUT.parent/'train-search-batch-v40'
        NAMES=list(NEXT_NAMES)
        STATEMENT='接着找，不要停下来，停下来的条件就是找到盈利策略'
    elif BATCH==39:
        from chanlun_trader.research_factory.participation_stability_signals_v1 import NAMES as NEXT_NAMES
        ROOT=INPUT.parent/'train-search-batch-v39'
        NAMES=list(NEXT_NAMES)
        STATEMENT='接着找，不要停下来，停下来的条件就是找到盈利策略'
    elif BATCH==38:
        from chanlun_trader.research_factory.scale_proxy_signals_v1 import NAMES as NEXT_NAMES
        ROOT=INPUT.parent/'train-search-batch-v38'
        NAMES=list(NEXT_NAMES)
        STATEMENT='接着找，不要停下来，停下来的条件就是找到盈利策略'
    elif BATCH==37:
        from chanlun_trader.research_factory.opening_pressure_signals_v1 import NAMES as NEXT_NAMES
        ROOT=INPUT.parent/'train-search-batch-v37'
        NAMES=list(NEXT_NAMES)
        STATEMENT='接着找，不要停下来，停下来的条件就是找到盈利策略'
    elif BATCH==36:
        from chanlun_trader.research_factory.affordable_portfolio_signals_v1 import NAMES as NEXT_NAMES
        ROOT=INPUT.parent/'train-search-batch-v36'
        NAMES=list(NEXT_NAMES)
        STATEMENT='接着找，不要停下来，停下来的条件就是找到盈利策略'
    elif BATCH==35:
        from chanlun_trader.research_factory.turnover_regime_signals_v1 import LOW20
        from chanlun_trader.research_factory.calendar_recovery_signals_v1 import NAME as CALENDAR_NAME
        ROOT=INPUT.parent/'train-search-batch-v35'
        NAMES=[LOW20,CALENDAR_NAME]
        STATEMENT='接着找，不要停下来，停下来的条件就是找到盈利策略'
    elif BATCH==34:
        from chanlun_trader.research_factory.turnover_regime_signals_v1 import NAMES as NEXT_NAMES
        ROOT=INPUT.parent/'train-search-batch-v34'
        NAMES=list(NEXT_NAMES)
        STATEMENT='接着找，不要停下来，停下来的条件就是找到盈利策略'
    elif BATCH==33:
        from chanlun_trader.research_factory.alpha191_pressure_signals_v1 import NAMES as NEXT_NAMES
        ROOT=INPUT.parent/'train-search-batch-v33'
        NAMES=list(NEXT_NAMES)
        STATEMENT='接着找，不要停下来，停下来的条件就是找到盈利策略'
    elif BATCH==32:
        from chanlun_trader.research_factory.shock_consolidation_signals_v1 import NAMES as NEXT_NAMES
        ROOT=INPUT.parent/'train-search-batch-v32'
        NAMES=list(NEXT_NAMES)
        STATEMENT='接着找，不要停下来，停下来的条件就是找到盈利策略'
    elif BATCH==31:
        from chanlun_trader.research_factory.response_confirmation_signals_v1 import NAMES as NEXT_NAMES
        ROOT=INPUT.parent/'train-search-batch-v31'
        NAMES=list(NEXT_NAMES)
        STATEMENT='接着找，不要停下来，停下来的条件就是找到盈利策略'
    elif BATCH==30:
        from chanlun_trader.research_factory.lag_response_signals_v1 import NAMES as NEXT_NAMES
        ROOT=INPUT.parent/'train-search-batch-v30'
        NAMES=list(NEXT_NAMES)
        STATEMENT='接着找，不要停下来，停下来的条件就是找到盈利策略'
    elif BATCH==29:
        from chanlun_trader.research_factory.skill_alpha_signals_v1 import NAMES as NEXT_NAMES
        ROOT=INPUT.parent/'train-search-batch-v29'
        NAMES=list(NEXT_NAMES)
        STATEMENT='继续找，结合我们之前失败吸收的经验还有我们安装的skill'
    elif BATCH==28:
        from chanlun_trader.research_factory.market_sensitivity_signals_v1 import NAMES as NEXT_NAMES
        ROOT=INPUT.parent/'train-search-batch-v28'
        NAMES=list(NEXT_NAMES)
        STATEMENT='那接着找啊，找到能用的啊'
    elif BATCH==27:
        from chanlun_trader.research_factory.price_impact_signals_v1 import NAMES as NEXT_NAMES
        ROOT=INPUT.parent/'train-search-batch-v27'
        NAMES=list(NEXT_NAMES)
        STATEMENT='那接着找啊，找到能用的啊'
    elif BATCH==26:
        from chanlun_trader.research_factory.return_path_signals_v1 import NAMES as NEXT_NAMES
        ROOT=INPUT.parent/'train-search-batch-v26'
        NAMES=list(NEXT_NAMES)
        STATEMENT='不用一直问我，你自己判断，我只要求找到一个真正盈利的；那做啊'
    elif BATCH==25:
        from chanlun_trader.research_factory.market_residual_signals_v1 import EXIT_NAMES as NEXT_NAMES
        ROOT=INPUT.parent/'train-search-batch-v25'
        NAMES=list(NEXT_NAMES)
        STATEMENT='那继续找吧，记得我们之前的规矩，总结经验，或者看看这个策略是不是可以再修一下，如何保住收益，解决这个策略的问题也行'
    elif BATCH==24:
        from chanlun_trader.research_factory.market_residual_signals_v1 import RISK_NAMES as NEXT_NAMES
        ROOT=INPUT.parent/'train-search-batch-v24'
        NAMES=list(NEXT_NAMES)
        STATEMENT='那继续找策略吧，找到能盈利的为止，按照我们之前的思路，我们是在A股'
    elif BATCH==23:
        from chanlun_trader.research_factory.market_residual_signals_v1 import NAMES as NEXT_NAMES
        ROOT=INPUT.parent/'train-search-batch-v23'
        NAMES=list(NEXT_NAMES)
        STATEMENT='那继续找策略吧，找到能盈利的为止，按照我们之前的思路，我们是在A股'
    elif BATCH==22:
        from chanlun_trader.research_factory.volume_flow_signals_v1 import NAMES as NEXT_NAMES
        ROOT=INPUT.parent/'train-search-batch-v22'
        NAMES=list(NEXT_NAMES)
        STATEMENT='那继续找策略吧，找到能盈利的为止，按照我们之前的思路，我们是在A股'
    elif BATCH==21:
        from chanlun_trader.research_factory.recovery_rotation_signals_v1 import NAMES as NEXT_NAMES
        ROOT=INPUT.parent/'train-search-batch-v21'
        NAMES=list(NEXT_NAMES)
        STATEMENT='那继续找策略吧，找到能盈利的为止，按照我们之前的思路，我们是在A股'
    elif BATCH==20:
        from chanlun_trader.research_factory.price_volume_patterns_v1 import NAMES as NEXT_NAMES
        ROOT=INPUT.parent/'train-search-batch-v20'
        NAMES=list(NEXT_NAMES)
        STATEMENT='继续寻找新的策略，按之前的顺序，不要一直按一个作者的思想；我们是A股，也要参照本土交易者；那继续'
    elif BATCH==19:
        from chanlun_trader.research_factory.weekly_fixed_trial_v1 import NAMES as NEXT_NAMES
        ROOT=INPUT.parent/'train-search-batch-v19'
        NAMES=list(NEXT_NAMES)
        STATEMENT='继续去找吧，总结经验；自己构思能使用多个指标结合的策略，然后进行验证'
    elif BATCH==18:
        from chanlun_trader.research_factory.weekly_defensive_signals_v1 import NAMES as NEXT_NAMES
        ROOT=INPUT.parent/'train-search-batch-v18'
        NAMES=list(NEXT_NAMES)
        STATEMENT='继续去找吧，总结经验；结合网上的资料和交易方法，自己构思能使用多个指标结合的策略，然后进行验证'
    elif BATCH==17:
        from chanlun_trader.research_factory.structured_exit_trial_v1 import NAMES as NEXT_NAMES
        ROOT=INPUT.parent/'train-search-batch-v17'
        NAMES=list(NEXT_NAMES)
        STATEMENT='先告诉我为什么没有通过训练筛选，你要从里面吸收经验，然后运用到下一次的思考策略中；请你继续思考吧，找到能通过训练筛选的策略为止'
    elif BATCH==16:
        ROOT=INPUT.parent/'train-search-batch-v16'
        NAMES=['ADX_EMA_PULLBACK_HOLD_20','SQUEEZE_TREND_TURNOVER_HOLD_20']
        STATEMENT='能使用多个指标结合，最主要是需要你自己去探索，放开限制，不要局限，要自己多想想，思考，看看别人的策略都是怎么做的，然后学习，自己去研究如何想出来策略，然后去验证'
    elif 12<=BATCH<=15:
        from run_train_search_sequence_v1 import BATCHES
        ROOT=INPUT.parent/f'train-search-batch-v{BATCH}'
        NAMES=BATCHES[BATCH]
        STATEMENT='那继续找吧，找到能用的为止'
    from batch39_input_recovery_v1 import resolve as recovery_root
    ROOT=recovery_root(ROOT)
    if options.stage:
        if options.name not in NAMES:
            raise PermissionError('CANDIDATE_NOT_IN_BATCH')
        from chanlun_trader.synthetic_batch_resources import worker_resource_handshake
        handshake = worker_resource_handshake()
        expected = {'stage':options.stage,'candidate':options.name,'execution_id':options.execution_id}
        if handshake['execution']!=expected:
            raise PermissionError('WORKER_CONTEXT_CHANGED')
        guard()
        prepare(options.name) if options.stage=='prepare' else worker(options.name,options.execution_id)
    else:
        run()
