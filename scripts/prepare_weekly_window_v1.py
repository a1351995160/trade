"""周低波动固定窗口输入：旧原件只读、双价格分离、原市场门控及无收益可行性。"""
import json
from pathlib import Path
from types import SimpleNamespace

from acquire_weekly_window_v1 import ROOT, OLD, check, read, save, sha, overlap_check, FIELDS
from prepare_monthly_window_v1 import state_rows, response
from chanlun_trader.research_factory.weekly_window_v1 import contract, NAME, START, END, WARMUP_START
from chanlun_trader.research_factory.common import stable_hash


def joined_rows(old,new,first_existing,date_field):
    # 重叠仅作对照；实际数值继续保留旧版本，绝不覆盖。
    rows=[r for r in new if r[date_field]<first_existing]+old
    dates=[r[date_field] for r in rows]
    if len(dates)!=len(set(dates)):raise ValueError('STITCH_DUPLICATE_DATE')
    return sorted(rows,key=lambda r:r[date_field])


def load_bundle():
    import pandas as pd
    from chanlun_trader.data.tdx.windowed_actions_v1 import WindowedCorporateActionDatasetV1
    from chanlun_trader.research_factory.weekly_parquet_v1 import compact_read
    manifest=read(ROOT/'INPUT_MANIFEST.json')
    if manifest['contract']!=contract():raise PermissionError('WEEKLY_INPUT_CONTRACT_CHANGED')
    for name,digest in manifest['files'].items():
        if sha(ROOT/name)!=digest:raise PermissionError('WEEKLY_MATERIALIZED_INPUT_CHANGED')
    hazard_hash=manifest['files']['WINDOW_HAZARDS.json']
    return SimpleNamespace(daily=compact_read(ROOT/'DAILY.parquet'),states=compact_read(ROOT/'STATES.parquet'),
        ready_factors=compact_read(ROOT/'FEATURES.parquet'),calendar=manifest['sessions'],
        hazards=read(ROOT/'WINDOW_HAZARDS.json')['by_symbol'],
        actions=WindowedCorporateActionDatasetV1('BAOSTOCK_WINDOW_HAZARD_ONLY_NOT_FULL_ACCOUNTING',
            'WindowedCorporateActionDatasetV1',WARMUP_START,END,(),hazard_hash,tuple(manifest['symbols']),
            'PROVIDER_ADJUSTMENT_DATES_NOT_FULL_ACCOUNTING'),
        coverage=manifest['coverage'],access=manifest['inputs'],input_identity=manifest['input_identity'],
        pool_identity=manifest['universe_sha256'],factor_identity=manifest['files']['FEATURES.parquet'],
        calendar_identity=sha(ROOT/'CALENDAR_WINDOW.json'),contract_identity=stable_hash(contract()))


def _prepare(chunks):
    import pandas as pd
    from chanlun_trader.research_factory.baostock_input_v1 import verify_pair
    from chanlun_trader.research_factory.baostock_price_views_v1 import prepare_symbol
    from chanlun_trader.research_factory.technical_train_signals_v1 import adjusted_rows, transform
    from chanlun_trader.research_factory.degraded_input_v2 import check_feasibility
    grant=check(); cal=read(ROOT/'CALENDAR_WINDOW.json'); plan=read(ROOT/'PLAN_PROPOSAL_V2.json')
    from weekly_alias_recovery_v1 import recovery
    alias=recovery() if (ROOT/'ALIAS_RECOVERY_V1.json').exists() else None
    if (int(cal['warmup_start'].replace('-',''))!=WARMUP_START
        or plan['source_candidate_contract_hash']!=contract()['source_candidate_contract_hash']):
        raise PermissionError('SOURCE_OR_CALENDAR_CONTRACT_CHANGED')
    if not (ROOT/'ACQUISITION_COMPLETED.json').exists():raise PermissionError('ACQUISITION_NOT_COMPLETE')
    old_manifest=read(OLD/'INPUT_MANIFEST.json')
    expected={str(Path(p)):h for p,h in old_manifest['inputs'].items()}
    inputs={}
    save(ROOT/('PREPARE_ACCESS_ALIAS_V1.json' if alias else 'PREPARE_ACCESS.json'),{'reader':grant['thread_id'],'recipient':'PRIVATE_EVALUATION',
        'purpose':'APPROVED_FIXED_SIGNAL_AND_NO_OUTCOME_FEASIBILITY','release_identity':grant['identity'],
        'input_scope':[WARMUP_START,END],'signal_scope':[START,END],
        'warmup_performance_forbidden':True,'exact_performance_computed':False})

    def source(path,legacy=False):
        if legacy and (str(path) not in expected or sha(path)!=expected[str(path)]):
            raise PermissionError('OLD_SOURCE_NOT_BOUND_OR_CHANGED')
        value=response(path); inputs[str(path)]=sha(path)
        return value

    basics={r['code']:r for r in source(OLD/'acquisition/basic.json',True)['rows']}
    days=[int(d.replace('-','')) for d in cal['sessions']]
    first_existing=read(OLD/'CALENDAR_WINDOW.json')['sessions'][0]
    pools={}
    for date in cal['sessions']:
        legacy=date>=first_existing
        rows=source((OLD if legacy else ROOT)/'acquisition/pool'/f'{date}.json',legacy)['rows']
        if len({r['code'] for r in rows})!=len(rows):raise ValueError('POOL_DUPLICATE_IDENTITY')
        pools[int(date.replace('-',''))]={r['code']:r['tradeStatus'] for r in rows}
    codes=read(OLD/'WINDOW_UNIVERSE.json')['codes']
    if sorted(c[3:]+'.'+c[:2].upper() for c in codes)!=sorted(old_manifest['symbols']):
        raise PermissionError('UNIVERSE_CHANGED')
    all_base=[];coverage=[];hazards={}
    for code in codes:
        check()
        symbol=code[3:]+'.'+code[:2].upper(); pair=[]
        for flag in ['3','1']:
            path=OLD/'responses'/f'probe-{flag}.json' if code=='sh.600000' else OLD/'acquisition/prices'/code/f'{flag}.json'
            old=source(path,True); new=source(ROOT/'acquisition/prices'/code/f'{flag}.json')
            overlap_check(old['rows'],new['rows'],cal['overlap_sessions'],FIELDS[flag])
            pair.append({**old,'rows':joined_rows(old['rows'],new['rows'],first_existing,'date')})
        state_pools=pools
        if alias and code=='sz.302132':
            recovery()
            from chanlun_trader.research_factory.weekly_alias_v1 import adapt,canonical_pools
            prior_raw=source(ROOT/'alias-supplement-v1/price-3.json')['rows']
            prior_hfq=source(ROOT/'alias-supplement-v1/price-1.json')['rows']
            source(ROOT/'alias-supplement-v1/actions.json')
            adapted_raw,adapted_hfq,evidence=adapt(pair[0]['rows'],pair[1]['rows'],prior_raw,prior_hfq)
            pair=[{**pair[0],'rows':adapted_raw},{**pair[1],'rows':adapted_hfq}]
            state_pools=canonical_pools(pools)
            save(ROOT/'alias-recovery-v1/DERIVED_PRICE_EVIDENCE.json',evidence)
            save(ROOT/'alias-recovery-v1/CANONICAL_PRICE_ROWS.json',{'raw':adapted_raw,'hfq':adapted_hfq})
        states=state_rows(code,basics[code],pair[0]['rows'],state_pools,days)
        quality=verify_pair(symbol,*pair,days,states)
        save(ROOT/('quality-alias-v1' if alias and code=='sz.302132' else 'quality')/f'{code}.json',quality)
        if not quality['passed']:raise ValueError(f'WEEKLY_INPUT_QUALITY_FAILED:{code}')
        old_action=source(OLD/'acquisition/actions'/f'{code}.json',True)['rows']
        new_action=source(ROOT/'acquisition/actions'/f'{code}.json')['rows']
        oa=[r for r in old_action if r['dividOperateDate']<=cal['fetch_end']]
        na=[r for r in new_action if r['dividOperateDate']>=first_existing]
        if sorted(oa,key=stable_hash)!=sorted(na,key=stable_hash):raise ValueError(f'ADJUSTMENT_OVERLAP_CONFLICT:{code}')
        dates=[]
        for row in joined_rows(old_action,new_action,first_existing,'dividOperateDate'):
            day=int(row['dividOperateDate'].replace('-',''))
            if row['code']!=code or not WARMUP_START<=day<=END:raise ValueError('ACTION_SCOPE_CONFLICT')
            dates.append(day)
        hazards[symbol]=sorted(dates)
        state=pd.DataFrame(states)
        for col in ['symbol','eligibility_status','st_status','suspension_status','board']:state[col]=state[col].astype('category')
        chunks.write('STATES.parquet',state)
        if not pair[0]['rows']:
            coverage.append({'symbol':symbol,'raw_rows':0,'signal_input_ready_rows':0})
            continue
        views=prepare_symbol(symbol,pair[0]['rows'],pair[1]['rows'],days,state_rows=states,window_contract=contract())
        known=state.st_status.isin(['NORMAL','ST']) & state.suspension_status.isin(['TRADING','SUSPENDED']) & state.eligibility_status.ne('CONFLICT')
        ready=state.eligibility_status.eq('ELIGIBLE').to_numpy() & known.rolling(6,min_periods=6).sum().eq(6).to_numpy() & views.features.value.notna().to_numpy()
        if ready.any():
            rows=adjusted_rows(views.features.loc[ready].copy(),views.raw,pair[1]['rows'])
            all_base.append(rows[['symbol','timestamp','value','effective_available_at','signal_open','signal_high','signal_low','signal_close']])
        chunks.write('DAILY.parquet',views.raw.loc[views.raw.code.notna(),['symbol','date','open','high','low','close','volume','amount','prev_close','adjustflag']])
        coverage.append({'symbol':symbol,'raw_rows':len(pair[0]['rows']),'signal_input_ready_rows':int(ready.sum()),
            'suspended_rows_preserved':quality['suspended_rows_preserved'],'hazard_dates':len(dates)})
    # 原市场门控使用全体适格RETURN5，不能使用低波动筛选后的样本重算市场。
    del pools,state_pools,basics
    features=[]
    if all_base:
        base=pd.concat(all_base,ignore_index=True)
        del all_base
        market=base.groupby('timestamp',observed=True).agg(market_median=('value','median'),market_available_at=('effective_available_at','max'))
        base=base.join(market,on='timestamp')
        for symbol,rows in base.groupby('symbol',observed=True,sort=True):
            transformed=transform(rows,days,NAME)
            chunks.write('COMPUTABILITY.parquet',transformed.loc[transformed.timestamp.between(START,END),['symbol','timestamp','computable']])
            features.append(transformed.loc[transformed.computable & transformed.timestamp.between(START,END)].drop(columns='computable'))
        del base,market
    empty=pd.DataFrame({'symbol':pd.Series(dtype='str'),'timestamp':pd.Series(dtype='int64'),
        'value':pd.Series(dtype='float64'),'effective_available_at':pd.Series(dtype='datetime64[ns, UTC]'),'signal_version':pd.Series(dtype='str')})
    factors=pd.concat(features,ignore_index=True) if features else empty
    del features
    for col in ['symbol','signal_version']:factors[col]=factors[col].astype('category')
    factors.to_parquet(ROOT/'FEATURES.parquet',index=False)
    chunks.close()
    save(ROOT/'STREAMED_ROW_COUNTS_V1.json',chunks.rows)
    save(ROOT/'WINDOW_HAZARDS.json',{'by_symbol':hazards,'count':sum(map(len,hazards.values())),
        'meaning':'PROVIDER_ADJUSTMENT_DATES_UNSUPPORTED_ACCOUNTING_NOT_KNOWN_NONE'})
    files={n:sha(ROOT/n) for n in ['DAILY.parquet','STATES.parquet','FEATURES.parquet','COMPUTABILITY.parquet','WINDOW_HAZARDS.json']}
    if alias:
        inputs[str(ROOT/'ALIAS_RECOVERY_V1.json')]=sha(ROOT/'ALIAS_RECOVERY_V1.json')
        files.update({n:sha(ROOT/n) for n in ['alias-recovery-v1/DERIVED_PRICE_EVIDENCE.json','alias-recovery-v1/CANONICAL_PRICE_ROWS.json']})
    identity=stable_hash({'inputs':inputs,'files':files,'contract':contract(),'release':grant['identity']})
    save(ROOT/'INPUT_MANIFEST.json',{'input_identity':identity,'contract':contract(),'inputs':inputs,'files':files,
        'sessions':days,'symbols':sorted(hazards),'coverage':coverage,'universe_sha256':sha(OLD/'WINDOW_UNIVERSE.json')})
    paths=[]
    for day,rows in factors.groupby('timestamp',observed=True):
        i=days.index(int(day))
        if i+1>=len(days):continue
        for rank,row in enumerate(rows[rows.value<0].sort_values(['value','symbol']).head(3).itertuples(),1):
            paths.append({'symbol':row.symbol,'signal_session':int(day),'rank':rank,'entry_date':days[i+1],
                'exit_date':days[i+22] if i+22<len(days) else None})
    del factors
    import gc
    gc.collect()
    result=check_feasibility(load_bundle(),candidate_paths=paths)
    result.update(type='WEEKLY_FIXED_WINDOW_FEASIBILITY_V1',hazard_records=sum(map(len,hazards.values())))
    save(ROOT/'FEASIBILITY.json',json.loads(json.dumps(result,default=str,allow_nan=False)))
    save(ROOT/'READY.json',{'status':'READY' if result['passed'] else 'NOT_READY','input_identity':identity,
        'feasibility_passed':result['passed'],'feasibility_sha256':sha(ROOT/'FEASIBILITY.json'),
        'novelty_status':'AUTHORIZED_FIXED_REFERENCE_REPRODUCTION','source_candidate_hash':contract()['source_candidate_contract_hash'],
        'historical_exposure_preserved':True,'STRICT_TRAIN_INPUT_READY':False})
    print({'feasibility_passed':result['passed'],'counts':result['counts']},flush=True)


def prepare():
    from chanlun_trader.synthetic_batch_resources import worker_resource_handshake
    from chanlun_trader.research_factory.weekly_parquet_v1 import InputChunks
    if worker_resource_handshake()['execution']!={'stage':'prepare-window'}:raise PermissionError('PREPARE_CONTEXT')
    check()
    for name in ['DAILY.parquet','STATES.parquet','FEATURES.parquet','COMPUTABILITY.parquet','INPUT_MANIFEST.json']:
        if (ROOT/name).exists():raise PermissionError('NO_MATERIALIZED_OVERWRITE')
    with InputChunks(ROOT) as chunks:
        return _prepare(chunks)
