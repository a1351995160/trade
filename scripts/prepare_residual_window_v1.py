"""复用已核验RAW/状态与精确绑定双价格原件，生成固定残差特征；无下载。"""
import gc
import json
from pathlib import Path
from types import SimpleNamespace

from execute_residual_window_v1 import ROOT,INPUT,OLD,guard,read,save,sha,contract,NAME,START,END
from chanlun_trader.research_factory.residual_window_v1 import WARMUP_START,TURNOVER_VERSIONS,SCALE_VERSIONS,TREND_RISK_VERSIONS
TECHNICAL_VERSIONS={**TURNOVER_VERSIONS,**SCALE_VERSIONS,**TREND_RISK_VERSIONS}
from chanlun_trader.research_factory.common import stable_hash


def source_manifest():
    value=read(INPUT/'INPUT_MANIFEST.json')
    if value['sessions'][0]!=WARMUP_START or value['sessions'][-1]!=END:
        raise PermissionError('SOURCE_CALENDAR_SCOPE_CHANGED')
    return value


def load_bundle():
    from chanlun_trader.research_factory.weekly_parquet_v1 import compact_read
    from chanlun_trader.data.tdx.windowed_actions_v1 import WindowedCorporateActionDatasetV1
    meta=source_manifest();own=read(ROOT/'INPUT_MANIFEST.json')
    if own['input_identity']!=stable_hash({k:v for k,v in own.items() if k!='input_identity'}):
        raise PermissionError('RESIDUAL_INPUT_MANIFEST_CORRUPT')
    if own['contract']!=contract() or own['source_input_identity']!=meta['input_identity']:
        raise PermissionError('RESIDUAL_INPUT_CONTRACT_CHANGED')
    for name,digest in own['files'].items():
        if sha(ROOT/name)!=digest:raise PermissionError('RESIDUAL_FEATURES_CHANGED')
    for name in ['DAILY.parquet','STATES.parquet','WINDOW_HAZARDS.json']:
        if sha(INPUT/name)!=meta['files'][name]:raise PermissionError('SOURCE_ACCOUNT_INPUT_CHANGED')
    hazard_hash=meta['files']['WINDOW_HAZARDS.json']
    return SimpleNamespace(daily=compact_read(INPUT/'DAILY.parquet'),states=compact_read(INPUT/'STATES.parquet'),
        ready_factors=compact_read(ROOT/'FEATURES.parquet'),calendar=meta['sessions'],
        hazards=read(INPUT/'WINDOW_HAZARDS.json')['by_symbol'],
        actions=WindowedCorporateActionDatasetV1('BAOSTOCK_WINDOW_HAZARD_ONLY_NOT_FULL_ACCOUNTING',
            'WindowedCorporateActionDatasetV1',WARMUP_START,END,(),hazard_hash,tuple(meta['symbols']),
            'PROVIDER_ADJUSTMENT_DATES_NOT_FULL_ACCOUNTING'),
        coverage=meta['coverage'],access=own['inputs'],input_identity=own['input_identity'],
        pool_identity=meta['universe_sha256'],factor_identity=own['files']['FEATURES.parquet'],
        calendar_identity=sha(INPUT/'CALENDAR_WINDOW.json'),contract_identity=stable_hash(contract()))


def eligible_base(symbol,raw,hfq,days,state):
    from chanlun_trader.research_factory.baostock_price_views_v1 import prepare_symbol
    views=prepare_symbol(symbol,raw,hfq,days,state_rows=state.to_dict('records'),window_contract=contract())
    # 与源窗口及TRAIN适格RETURN5定义一致，先形成全体适格基础特征，后算市场。
    known=state.st_status.isin(['NORMAL','ST']) & state.suspension_status.isin(['TRADING','SUSPENDED']) & state.eligibility_status.ne('CONFLICT')
    valid=state.eligibility_status.eq('ELIGIBLE').to_numpy() & known.rolling(6,min_periods=6).sum().eq(6).to_numpy() & views.features.value.notna().to_numpy()
    base=views.features.loc[valid,['symbol','timestamp','value','effective_available_at']].copy()
    if NAME in TECHNICAL_VERSIONS and not base.empty:
        from chanlun_trader.research_factory.technical_train_signals_v1 import adjusted_rows
        adjusted=adjusted_rows(base,views.raw,hfq)
        return adjusted[['symbol','timestamp','value','effective_available_at','signal_open','signal_high','signal_low','signal_close']+(['close'] if NAME in SCALE_VERSIONS or NAME in TREND_RISK_VERSIONS else [])]
    return base


def prepare():
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq
    from chanlun_trader.research_factory.weekly_parquet_v1 import compact_read,SCHEMAS
    from chanlun_trader.research_factory.market_residual_signals_v1 import transform
    if NAME in TECHNICAL_VERSIONS:
        from chanlun_trader.research_factory.technical_train_signals_v1 import transform
    from chanlun_trader.research_factory.degraded_input_v2 import check_feasibility
    from prepare_weekly_window_v1 import joined_rows
    grant=guard();meta=source_manifest();days=meta['sessions']
    for name in ('FEATURES.parquet','COMPUTABILITY.parquet','INPUT_MANIFEST.json'):
        if (ROOT/name).exists():raise PermissionError('NO_MATERIALIZED_OVERWRITE')
    expected={str(Path(p)):digest for p,digest in meta['inputs'].items()}
    access={}

    def source(path):
        key=str(path)
        if key not in expected or sha(path)!=expected[key]:raise PermissionError('EXACT_SOURCE_IDENTITY_CHANGED:'+key)
        access[key]=expected[key]
        value=read(path)
        if value.get('error_code')!='0':raise PermissionError('SOURCE_RESPONSE_NOT_SUCCESS')
        return value['rows']

    states=compact_read(INPUT/'STATES.parquet')
    groups=states.groupby('symbol',observed=True).indices
    first=read(OLD/'CALENDAR_WINDOW.json')['sessions'][0]
    parts=[]
    save(ROOT/'PREPARE_ACCESS.json',{'reader_pid':__import__('os').getpid(),'reader_thread':grant['thread_id'],
        'recipient':'PRIVATE_EVALUATION','purpose':'FROZEN_FEATURE_AND_NO_OUTCOME_FEASIBILITY',
        'source_manifest_sha256':sha(INPUT/'INPUT_MANIFEST.json'),'source_input_identity':meta['input_identity'],
        'input_scope':[WARMUP_START,END],'signal_scope':[START,END],'performance_computed':False})
    for i,symbol in enumerate(meta['symbols']):
        state=states.iloc[groups[symbol]].sort_values('trade_date').reset_index(drop=True)
        if state.trade_date.astype(str).str.replace('-','').astype(int).tolist()!=days:
            raise PermissionError('STATE_CALENDAR_ALIGNMENT_CHANGED')
        code=symbol[-2:].lower()+'.'+symbol[:6]
        if code=='sz.302132':
            path=INPUT/'alias-recovery-v1/CANONICAL_PRICE_ROWS.json'
            if sha(path)!=meta['files']['alias-recovery-v1/CANONICAL_PRICE_ROWS.json']:
                raise PermissionError('CANONICAL_ALIAS_INPUT_CHANGED')
            pair=read(path);raw,hfq=pair['raw'],pair['hfq'];access[str(path)]=sha(path)
        else:
            pair=[]
            for flag in ('3','1'):
                old_path=OLD/'responses'/f'probe-{flag}.json' if code=='sh.600000' else OLD/'acquisition/prices'/code/f'{flag}.json'
                pair.append(joined_rows(source(old_path),source(INPUT/'acquisition/prices'/code/f'{flag}.json'),first,'date'))
            raw,hfq=pair
        if raw:parts.append(eligible_base(symbol,raw,hfq,days,state))
        if (i+1)%100==0 or i+1==len(meta['symbols']):
            save(ROOT/'progress'/f'base-{i+1:05d}.json',{'stage':'eligible-base','done':i+1,'total':len(meta['symbols'])})
    del states,groups
    if not parts:raise ValueError('NO_ELIGIBLE_BASE_ROWS')
    base=pd.concat(parts,ignore_index=True);del parts
    market=base.groupby('timestamp',observed=True).agg(market_median=('value','median'),market_available_at=('effective_available_at','max'))
    base=base.join(market,on='timestamp')
    if NAME in {**TURNOVER_VERSIONS,**SCALE_VERSIONS} and not base.empty:
        import prepare_turnover_window_input_v1 as turnover_input
        from chanlun_trader.research_factory.turnover_regime_signals_v1 import context
        supplemental=turnover_input.ROOT/'TURNOVER.parquet'
        access[str(supplemental)]=sha(supplemental)
        turns=pd.read_parquet(supplemental,columns=['symbol','timestamp','turn'])
        if NAME in SCALE_VERSIONS:
            from chanlun_trader.research_factory.scale_proxy_signals_v1 import context as scale_context
            daily=compact_read(INPUT/'DAILY.parquet')
            access[str(INPUT/'DAILY.parquet')]=sha(INPUT/'DAILY.parquet')
            base=scale_context(base,daily,turns)
            del daily
        else:base=context(base,turns,days)
        del turns
    factors=[];total=base.symbol.nunique()
    with (ROOT/'COMPUTABILITY.parquet').open('xb') as handle:
        with pq.ParquetWriter(handle,SCHEMAS['COMPUTABILITY.parquet']) as writer:
            for i,(symbol,rows) in enumerate(base.groupby('symbol',observed=True,sort=True)):
                transformed=transform(rows,days,NAME)
                diagnostic=transformed.loc[transformed.timestamp.between(START,END),['symbol','timestamp','computable']]
                writer.write_table(pa.Table.from_pandas(diagnostic,schema=SCHEMAS['COMPUTABILITY.parquet'],preserve_index=False))
                factors.append(transformed.loc[transformed.computable&transformed.timestamp.between(START,END)].drop(columns='computable'))
                if (i+1)%100==0 or i+1==total:
                    save(ROOT/'progress'/f'factor-{i+1:05d}.json',{'stage':'technical-factor' if NAME in TECHNICAL_VERSIONS else 'residual-factor','done':i+1,'total':total})
    del base,market
    frame=pd.concat(factors,ignore_index=True) if factors else pd.DataFrame({
        'symbol':pd.Series(dtype='str'),'timestamp':pd.Series(dtype='int64'),
        'value':pd.Series(dtype='float64'),'effective_available_at':pd.Series(dtype='datetime64[ns, UTC]'),
        'signal_version':pd.Series(dtype='str')})
    del factors
    for col in ('symbol','signal_version'):frame[col]=frame[col].astype('category')
    frame.to_parquet(ROOT/'FEATURES.parquet',index=False)
    files={n:sha(ROOT/n) for n in ('FEATURES.parquet','COMPUTABILITY.parquet')}
    payload={'source_input_identity':meta['input_identity'],'source_manifest_sha256':sha(INPUT/'INPUT_MANIFEST.json'),
        'inputs':access,'files':files,'contract':contract(),'release_sha256':sha(ROOT/'WINDOW_RELEASE.json')}
    save(ROOT/'INPUT_MANIFEST.json',{**payload,'input_identity':stable_hash(payload)})
    save(ROOT/'PREPARE_SOURCE_INDEX.json',{'files':access,'source_count':len(access),'source_manifest_sha256':sha(INPUT/'INPUT_MANIFEST.json')})
    paths=[];positions={day:i for i,day in enumerate(days)}
    for day,rows in frame.groupby('timestamp',observed=True):
        i=positions[int(day)]
        if i+1>=len(days):continue
        for rank,row in enumerate(rows[rows.value<0].sort_values(['value','symbol']).head(3).itertuples(),1):
            paths.append({'symbol':row.symbol,'signal_session':int(day),'rank':rank,'entry_date':days[i+1],
                'exit_date':days[i+22] if i+22<len(days) else None})
    del frame;gc.collect()
    result=check_feasibility(load_bundle(),candidate_paths=paths)
    save(ROOT/'FEASIBILITY.json',json.loads(json.dumps(result,default=str,allow_nan=False)))
    save(ROOT/'READY.json',{'status':'READY' if result['passed'] else 'NOT_READY',
        'input_identity':stable_hash(payload),'feasibility_passed':result['passed'],
        'feasibility_sha256':sha(ROOT/'FEASIBILITY.json'),
        'novelty_status':'AUTHORIZED_FIXED_REFERENCE_REPRODUCTION',
        'source_candidate_hash':contract()['source_candidate_contract_hash'],
        'historical_exposure_preserved':True,'STRICT_TRAIN_INPUT_READY':False})
    print({'feasibility_passed':result['passed'],'counts':result['counts']},flush=True)
