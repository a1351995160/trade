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
    return {**code_identity(), **{str(p.relative_to(SOURCE)): sha(p) for p in [
        Path(__file__), SOURCE/'src/chanlun_trader/research_factory/train_search_batch_v1.py',
        SOURCE/'src/chanlun_trader/research_factory/technical_train_signals_v1.py',
        SOURCE/'src/chanlun_trader/chan.py', SOURCE/'docs/TECHNICAL_RESEARCH_SCOPE_V1.md']}}


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
    save(ROOT/'APPROVAL_SOURCE.json', source)
    paths = [INPUT/p for p in ['INPUT_MANIFEST.json','INPUT_READY.json','DAILY.parquet',
                              'STATES.parquet','FEATURES.parquet','NOVELTY.json']]
    for earlier_batch in range(1,BATCH):
        paths.append(INPUT.parent/f'train-search-batch-v{earlier_batch}/PREREGISTRATION.json')
    save(ROOT/'PREREGISTRATION.json', {'version':f'TRAIN_SEARCH_BATCH_V{BATCH}',
        'contracts':{name:contract(name) for name in NAMES}, 'novelty':decisions,
        'code':code(), 'inputs':{str(p):sha(p) for p in paths},
        'approval_sha256':sha(ROOT/'APPROVAL_SOURCE.json'),
        'objective_id':parent['plan']['objective_id'], 'expires_at':expiry.isoformat(),
        'batch_main_count':2,'reserved_repair_slots':2,'worker_seconds':900,'memory_mib':2048,
        'numeric_threads':1,'batch_compute_seconds':5400,
        'exploratory_screen':'COMPLETE_AND_TRAIN_NET_RETURN_GT_0_AND_CLOSED_LOTS_GE_30',
        'qualification':'NOT_FOR_QUALIFICATION; NO_P_Q; NO_VALIDATION_FINAL_TEST',
        'feedback':'ALL_CANDIDATES_BINARY_SCREEN_AND_FAILURE_REASON_ONLY',
        'historical_train_exposure':True,'historical_novelty_semantics_incomplete':True,
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
    if name.endswith('_MARKET_5'):
        market = value.ready_factors.groupby('timestamp',observed=True).agg(
            market_median=('value','median'),market_available_at=('effective_available_at','max'))
        value.ready_factors = value.ready_factors.join(market,on='timestamp')
    if name.removesuffix('_HOLD_20') == 'LIQUIDITY_20':
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
    used = sum(read(p)['elapsed_seconds'] for p in (ROOT/'resources').glob('*.completed.json'))
    limit = min(900,5400-used,(expiry-datetime.now(timezone.utc)).total_seconds())
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
    parser.add_argument('--batch',type=int,choices=[1,2,3,4,5,6,7],default=1)
    parser.add_argument('--stage',choices=['prepare','account'])
    parser.add_argument('--name',choices=['MOMENTUM_5','STABILITY_20','MOMENTUM_60','LIQUIDITY_20',
                                        'STABILITY_20_HOLD_20','LIQUIDITY_20_HOLD_20',
                                        'MOMENTUM_60_HOLD_20_MARKET_5','STABILITY_20_HOLD_20_MARKET_5',
                                        'MACD_CROSS_HOLD_20','KDJ_OVERSOLD_CROSS_HOLD_20',
                                        'CHAN_BOTTOM_MACD_HOLD_20','MONTHLY_REVERSAL_HOLD_20',
                                        'HIGH_252_HOLD_20','LOW_MAX_20_HOLD_20'])
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
