"""配对输入全量通过后，生成同一固定候选的无收益路径和账户输入。"""
import json
import os
from pathlib import Path
from types import SimpleNamespace

from run_baostock_account_v1 import ROOT, BASE, active, read, sha, save, response_directory


def prepare():
    from chanlun_trader.synthetic_batch_resources import worker_resource_handshake
    worker_resource_handshake()
    import pandas as pd
    import pyarrow.parquet as pq
    from chanlun_trader.research_factory.common import stable_hash
    from chanlun_trader.research_factory.baostock_account_v1 import CONTRACT
    from chanlun_trader.research_factory.baostock_price_views_v1 import prepare_symbol
    from chanlun_trader.research_factory.degraded_input_v2 import check_feasibility
    active()
    plan = read(ROOT/'READ_PLAN.json')
    from baostock_alias_v1 import evidence, execution_symbols, verify_states
    alias = evidence(ROOT)
    symbols = execution_symbols(plan['symbols'],alias)
    decision = read(ROOT/'NOVELTY.json')['decision']
    if not decision['allowed']:
        raise PermissionError('NOVELTY_REJECTED')
    for batch in range((len(plan['symbols'])+199)//200):
        revised = ROOT/'quality-ipo-v1'/f'batch-{batch}.json'
        quality = revised if revised.exists() else ROOT/'quality'/f'batch-{batch}.json'
        if batch == 14 and alias:
            quality = ROOT/'quality-alias-v1/batch-14.json'
            if read(quality)['alias_identity'] != alias['identity']:
                raise PermissionError('ALIAS_QUALITY_IDENTITY_CHANGED')
        if not read(quality)['passed']:
            raise PermissionError('INPUT_QUALITY_NOT_READY')
    for path, expected in plan['files'].items():
        if sha(path) != expected:
            raise ValueError('HISTORICAL_INPUT_IDENTITY_CHANGED')
    days = read(BASE/'materialized-v3/CALENDAR.json')['sessions']
    # 前期质量检查已逐物理row group核对该同一哈希的日期范围。
    states = pq.read_table(BASE/'materialized-v3/HISTORICAL_POOL_AND_STATE.parquet',
        columns=['symbol','trade_date','listed','delisted','universe_member','eligibility_status',
                 'st_status','suspension_status','board'], use_threads=False).to_pandas(use_threads=False)
    states['trade_date'] = states.trade_date.astype(str).str.replace('-','').astype(int)
    if alias:
        verify_states(states)
        states = states.loc[states.symbol != '302132.SZ'].copy()
    for key in ['symbol','eligibility_status','st_status','suspension_status','board']:
        states[key] = states[key].astype('category')
    state_groups = states.groupby('symbol',observed=True).indices
    all_raw, all_features, coverage, inputs = [], [], [], {}
    save(ROOT/'SIGNAL_PREPARATION_STARTED.json', {'purpose':'APPROVED_FIXED_SIGNAL_NO_OUTCOME_FEASIBILITY',
        'reader_pid':os.getpid(),'recipient':'EVALUATION_SIDE_ONLY','contract':CONTRACT,
        'novelty_sha256':sha(ROOT/'NOVELTY.json'),'real_signal_information_access':True,
        'no_pnl_or_future_return':True})
    for symbol in symbols:
        active()
        pair = []
        for flag in ['3','1']:
            path = response_directory(symbol, flag, ROOT)/(flag+'.json')
            expected = read(path.with_name(flag+'.access.json'))['sha256']
            if sha(path) != expected:
                raise ValueError('PRICE_RESPONSE_CHANGED')
            inputs[str(path)] = expected
            pair.append(read(path)['rows'])
        if not pair[0]:
            # 同一成员仍在清单和质量证据中；生命周期无行情时不得构造零价格。
            coverage.append({'symbol':symbol,'source_rows':0,'computable_rows':0})
            continue
        original_state = states.iloc[state_groups.get(symbol,[])]
        views = prepare_symbol(symbol,*pair,days,state_rows=original_state.to_dict('records'))
        state = original_state.set_index('trade_date').reindex(days)
        known = state.st_status.isin(['NORMAL','ST']) & state.suspension_status.isin(['TRADING','SUSPENDED'])
        known &= state.eligibility_status.notna() & state.eligibility_status.ne('CONFLICT')
        eligible = (known & state.listed.eq(True) & state.delisted.eq(False) & state.universe_member.eq(True)
            & state.eligibility_status.eq('ELIGIBLE') & state.st_status.eq('NORMAL') & state.suspension_status.eq('TRADING'))
        dependency = known.rolling(6,min_periods=6).sum().eq(6)
        features = views.features
        ready = (eligible & dependency).to_numpy() & features.value.notna().to_numpy()
        ready &= features.timestamp.between(20220801,20240731).to_numpy()
        features = features.loc[ready].copy()
        actual = views.raw.loc[views.raw.code.notna(),
            ['symbol','date','open','high','low','close','volume','amount','prev_close','adjustflag']].copy()
        all_raw.append(actual)
        all_features.append(features)
        coverage.append({'symbol':symbol,'source_rows':len(actual),'computable_rows':int(ready.sum()),
            'state_dependency_unavailable_dates':[d for d,ok in zip(days,dependency) if not ok],
            'price_dependency_unavailable_dates':views.diagnostics.loc[~views.diagnostics.six_sessions_complete,'date'].tolist()})
    daily = pd.concat(all_raw,ignore_index=True)
    factors = pd.concat(all_features,ignore_index=True)
    for frame, keys in [(daily,['symbol','adjustflag']),(factors,['symbol','signal_version'])]:
        for key in keys:
            frame[key] = frame[key].astype('category')
    rule_hash = sha(ROOT/'HFQ_IPO_PREFIX_RULE_V1.json')
    identity = stable_hash({'inputs':inputs,'historical':plan['files'],'contract':CONTRACT,
        'hfq_identity_rule':rule_hash,'security_alias_identity':alias['identity'] if alias else None,
        'hazard_identity':'f2bc6064f9ab495fc854efdc6a4942dd4a87dc7be263de6e13da9be65e8a1927'})
    for name, frame in [('DAILY.parquet',daily),('FEATURES.parquet',factors),('STATES.parquet',states)]:
        path = ROOT/name
        if path.exists():
            raise PermissionError('DERIVED_INPUT_ALREADY_EXISTS_NO_OVERWRITE')
        frame.to_parquet(path,index=False)
    save(ROOT/'INPUT_MANIFEST.json', {'input_identity':identity,'contract_identity':stable_hash(CONTRACT),
        'inputs':inputs,'historical':plan['files'],'coverage':coverage,'hfq_identity_rule':rule_hash,
        'security_alias_identity':alias['identity'] if alias else None,'execution_symbols':symbols,
        'files':{name:sha(ROOT/name) for name in ['DAILY.parquet','FEATURES.parquet','STATES.parquet']}})
    bundle = load_bundle()
    paths = []
    for day, rows in factors.groupby('timestamp'):
        i = days.index(int(day))
        selected = rows[rows.value < 0].sort_values(['value','symbol']).head(3)
        for rank, row in enumerate(selected.itertuples(),1):
            paths.append({'symbol':row.symbol,'signal_session':int(day),'rank':rank,
                'entry_date':days[i+1], 'exit_date':days[i+5] if i+5<len(days) else None})
    save(ROOT/'CANDIDATE_PATHS.json', paths)
    result = check_feasibility(bundle,candidate_paths=paths)
    result['adapter_version'] = 'BAOSTOCK_HFQ_SIGNAL_RAW_PATHS_V1'
    save(ROOT/'FEASIBILITY.json',json.loads(json.dumps(result,default=str,allow_nan=False)))
    save(ROOT/'INPUT_READY.json', {'status':'READY' if result['passed'] else 'NOT_READY',
        'input_identity':identity,'feasibility_passed':result['passed'],
        'raw_hfq_pairing_verified':True,'historical_universe_complete':True,
        'source_identity_verified':True,'novelty_status':'PASSED','novelty_decision':decision,
        'feasibility_sha256':sha(ROOT/'FEASIBILITY.json'),
        'historical_semantic_history_complete':False,'STRICT_TRAIN_INPUT_READY':False})
    print(json.dumps({'feasibility_passed':result['passed'],'counts':result['counts']}))


def load_bundle():
    import pandas as pd
    from chanlun_trader.research_factory.common import stable_hash
    from chanlun_trader.research_factory.baostock_account_v1 import CONTRACT
    from chanlun_trader.data.tdx.windowed_actions_v1 import WindowedCorporateActionDatasetV1
    manifest = read(ROOT/'INPUT_MANIFEST.json')
    if manifest['contract_identity'] != stable_hash(CONTRACT):
        raise ValueError('CONTRACT_IDENTITY_CHANGED')
    if manifest['hfq_identity_rule'] != sha(ROOT/'HFQ_IPO_PREFIX_RULE_V1.json'):
        raise ValueError('HFQ_SEMANTICS_RULE_CHANGED')
    for path, expected in manifest['historical'].items():
        if sha(path) != expected:
            raise ValueError('HISTORICAL_INPUT_CHANGED')
    for name, expected in manifest['files'].items():
        if sha(ROOT/name) != expected:
            raise ValueError('DERIVED_INPUT_CHANGED')
    hazard_path = Path('E:/llmwiki/owner-execution-export-v1/run-v2/staging/gbbq_window.jsonl')
    if sha(hazard_path) != 'f2bc6064f9ab495fc854efdc6a4942dd4a87dc7be263de6e13da9be65e8a1927':
        raise ValueError('ORIGINAL_HAZARD_CHANGED')
    hazards = {}
    with hazard_path.open(encoding='utf-8') as stream:
        for line in stream:
            row = json.loads(line)
            hazards.setdefault(row['symbol'],[]).append(int(row['datetime']))
    if sum(map(len,hazards.values())) != 29990:
        raise ValueError('HAZARD_COUNT_CHANGED')
    from baostock_alias_v1 import evidence, execution_symbols, canonical_hazards
    alias = evidence(ROOT)
    if manifest.get('security_alias_identity') != (alias['identity'] if alias else None):
        raise ValueError('ALIAS_MANIFEST_IDENTITY_CHANGED')
    hazards = canonical_hazards(hazards,alias)
    symbols = execution_symbols(read(ROOT/'READ_PLAN.json')['symbols'],alias)
    actions = WindowedCorporateActionDatasetV1('DEGRADED_HAZARD_GUARDED_NO_EVENT_ACCOUNTING',
        'WindowedCorporateActionDatasetV1',20220722,20240731,(),sha(hazard_path),tuple(symbols),
        'LOCAL_HAZARDS_NOT_COMPLETE_ACTION_ACCOUNTING')
    return SimpleNamespace(daily=pd.read_parquet(ROOT/'DAILY.parquet'),
        states=pd.read_parquet(ROOT/'STATES.parquet'),ready_factors=pd.read_parquet(ROOT/'FEATURES.parquet'),
        calendar=read(BASE/'materialized-v3/CALENDAR.json')['sessions'],hazards=hazards,actions=actions,
        coverage=manifest['coverage'],access=[{'path':p,'sha256':h} for p,h in manifest['inputs'].items()],
        input_identity=manifest['input_identity'],pool_identity=sha(BASE/'degraded-train-v1/FROZEN_UNIVERSE.json'),
        factor_identity=sha(ROOT/'FEATURES.parquet'),calendar_identity=sha(BASE/'materialized-v3/CALENDAR.json'),
        contract_identity=stable_hash(CONTRACT))


if __name__ == '__main__':
    prepare()
