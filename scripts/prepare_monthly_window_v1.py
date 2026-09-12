"""冻结窗口输入物化；复用双价格校验、技术信号及原NO-OUTCOME路径检查。"""
import json
from types import SimpleNamespace

from fetch_monthly_window_v1 import ROOT, SOURCE, check, read, save, sha
from chanlun_trader.research_factory.monthly_window_v1 import contract, feasibility, START, END
from chanlun_trader.research_factory.common import stable_hash


def response(path):
    if sha(path)!=read(path.with_suffix('.access.json'))['sha256']:
        raise PermissionError('INPUT_RESPONSE_HASH_CHANGED')
    value=read(path)
    if value['error_code']!='0':
        raise PermissionError('INPUT_PROVIDER_ERROR')
    return value


def state_rows(code,basic,raw,pools,days):
    from chanlun_trader.engine.security_state import SecurityState
    symbol=code[3:]+'.'+code[:2].upper()
    ipo=basic['ipoDate'].replace('-','')
    out=basic['outDate'].replace('-','')
    if not ipo or not ipo.isdigit() or (out and not out.isdigit()):
        raise ValueError('LIFECYCLE_SOURCE_UNKNOWN')
    by_day={int(r['date'].replace('-','')):r for r in raw}
    rows=[]
    for day in days:
        price=by_day.get(day,{})
        member=code in pools[day]
        listed=day>=int(ipo)
        delisted=bool(out and day>=int(out))
        st={'0':'NORMAL','1':'ST'}.get(price.get('isST'),'UNKNOWN')
        suspension={'0':'SUSPENDED','1':'TRADING'}.get(price.get('tradestatus'),'UNKNOWN')
        conflict=member and (not listed or delisted or price.get('tradestatus')!=pools[day][code])
        eligible=listed and not delisted and member and st=='NORMAL' and suspension=='TRADING'
        rows.append({'symbol':symbol,'trade_date':day,'listed':listed,'delisted':delisted,
            'universe_member':member,'eligibility_status':'CONFLICT' if conflict else ('ELIGIBLE' if eligible else 'INELIGIBLE'),
            'st_status':st,'suspension_status':suspension,'board':SecurityState.infer_board(symbol)})
    return rows


def load_bundle():
    import pandas as pd
    from chanlun_trader.data.tdx.windowed_actions_v1 import WindowedCorporateActionDatasetV1
    manifest=read(ROOT/'INPUT_MANIFEST.json')
    if manifest['contract']!=contract():
        raise PermissionError('INPUT_CONTRACT_CHANGED')
    for name,digest in manifest['files'].items():
        if sha(ROOT/name)!=digest:
            raise PermissionError('MATERIALIZED_INPUT_CHANGED')
    hazards=read(ROOT/'WINDOW_HAZARDS.json')['by_symbol']
    actions=WindowedCorporateActionDatasetV1('BAOSTOCK_WINDOW_HAZARD_ONLY_NOT_FULL_ACCOUNTING',
        'WindowedCorporateActionDatasetV1',20250703,END,(),sha(ROOT/'WINDOW_HAZARDS.json'),
        tuple(manifest['symbols']),'PROVIDER_ADJUSTMENT_DATES_NOT_FULL_ACCOUNTING')
    return SimpleNamespace(daily=pd.read_parquet(ROOT/'DAILY.parquet'),states=pd.read_parquet(ROOT/'STATES.parquet'),
        ready_factors=pd.read_parquet(ROOT/'FEATURES.parquet'),calendar=manifest['sessions'],hazards=hazards,
        actions=actions,coverage=manifest['coverage'],access=manifest['inputs'],input_identity=manifest['input_identity'],
        pool_identity=manifest['universe_sha256'],factor_identity=manifest['files']['FEATURES.parquet'],
        calendar_identity=sha(ROOT/'CALENDAR_WINDOW.json'),contract_identity=stable_hash(contract()))


def prepare():
    from chanlun_trader.synthetic_batch_resources import worker_resource_handshake
    if worker_resource_handshake()['execution']!={'stage':'prepare-window'}:
        raise PermissionError('PREPARE_CONTEXT_CHANGED')
    import pandas as pd
    from chanlun_trader.research_factory.baostock_input_v1 import verify_pair
    from chanlun_trader.research_factory.baostock_price_views_v1 import prepare_symbol
    from chanlun_trader.research_factory.technical_train_signals_v1 import adjusted_rows, transform
    from chanlun_trader.research_factory.degraded_input_v2 import check_feasibility
    grant=check()
    for name in ['DAILY.parquet','STATES.parquet','FEATURES.parquet','INPUT_MANIFEST.json']:
        if (ROOT/name).exists():
            raise PermissionError('PREPARATION_ALREADY_MATERIALIZED_NO_OVERWRITE')
    window=read(ROOT/'CALENDAR_WINDOW.json')
    days=[int(d.replace('-','')) for d in window['sessions']]
    universe=read(ROOT/'WINDOW_UNIVERSE.json')
    basic_path=ROOT/'acquisition/basic.json'
    if sha(basic_path)!=universe['basic_sha256']:
        raise PermissionError('BASIC_SOURCE_CHANGED')
    basics={r['code']:r for r in response(basic_path)['rows']}
    pools={}
    inputs={str(basic_path):sha(basic_path)}
    for date,digest in universe['pool_hashes'].items():
        path=ROOT/'acquisition/pool'/f'{date}.json'
        if sha(path)!=digest:
            raise PermissionError('POOL_SOURCE_CHANGED')
        rows=response(path)['rows']
        if len({r['code'] for r in rows})!=len(rows):
            raise ValueError('POOL_DUPLICATE_IDENTITY')
        pools[int(date.replace('-',''))]={r['code']:r['tradeStatus'] for r in rows}
        inputs[str(path)]=digest
    save(ROOT/'PREPARE_ACCESS.json',{'reader':grant['thread_id'],'purpose':'FIXED_SIGNAL_NO_OUTCOME_FEASIBILITY',
        'recipient':'EVALUATION_SIDE','release_identity':grant['identity'],'contract':contract(),
        'source_publication':'UNKNOWN_MODELED_NEXT_OPEN','unit_evidence':'BAOSTOCK_SAME_PROVIDER_API_SHARES_CNY',
        'exact_performance_computed':False})
    all_raw=[];all_states=[];all_features=[];coverage=[];hazards={}
    for code in universe['codes']:
        check()
        symbol=code[3:]+'.'+code[:2].upper()
        paths=[ROOT/'responses'/f'probe-{flag}.json' if code=='sh.600000'
            else ROOT/'acquisition/prices'/code/f'{flag}.json' for flag in ['3','1']]
        pair=[response(path) for path in paths]
        for path in paths:inputs[str(path)]=sha(path)
        states=state_rows(code,basics[code],pair[0]['rows'],pools,days)
        quality=verify_pair(symbol,*pair,days,states)
        save(ROOT/'quality'/f'{code}.json',quality)
        if not quality['passed']:
            raise ValueError(f'WINDOW_INPUT_QUALITY_FAILED:{code}')
        action_path=ROOT/'acquisition/actions'/f'{code}.json'
        action=response(action_path)
        inputs[str(action_path)]=sha(action_path)
        dates=[]
        for row in action['rows']:
            day=int(row['dividOperateDate'].replace('-',''))
            if row['code']!=code or not days[0]<=day<=days[-1]:
                raise ValueError('ADJUSTMENT_IDENTITY_OR_WINDOW_CONFLICT')
            dates.append(day)
        hazards[symbol]=sorted(set(dates))
        state=pd.DataFrame(states)
        for col in ['symbol','eligibility_status','st_status','suspension_status','board']:
            state[col]=state[col].astype('category')
        all_states.append(state)
        if not pair[0]['rows']:
            coverage.append({'symbol':symbol,'raw_rows':0,'computable_rows':0})
            continue
        views=prepare_symbol(symbol,pair[0]['rows'],pair[1]['rows'],days,state_rows=states,window_contract=contract())
        eligible=state.eligibility_status.eq('ELIGIBLE').to_numpy()
        known=(state.st_status.isin(['NORMAL','ST']) & state.suspension_status.isin(['TRADING','SUSPENDED'])
            & state.eligibility_status.ne('CONFLICT'))
        # 沿用原技术候选输入前置6-session有效性；月末指标自身仍需21个连续session。
        ready=eligible & known.rolling(6,min_periods=6).sum().eq(6).to_numpy() & views.features.value.notna().to_numpy()
        rows=adjusted_rows(views.features.loc[ready].copy(),views.raw,pair[1]['rows'])
        if len(rows):
            transformed=transform(rows,days,'MONTHLY_REVERSAL_HOLD_20')
            factors=transformed.loc[transformed.computable & transformed.timestamp.between(START,END)].drop(columns='computable')
            all_features.append(factors)
        else:factors=[]
        raw=views.raw.loc[views.raw.code.notna(),['symbol','date','open','high','low','close','volume','amount','prev_close','adjustflag']]
        all_raw.append(raw)
        coverage.append({'symbol':symbol,'raw_rows':len(raw),'computable_rows':len(factors),
            'suspended_rows_preserved':quality['suspended_rows_preserved'],'hazard_dates':len(dates)})
    daily=pd.concat(all_raw,ignore_index=True)
    states=pd.concat(all_states,ignore_index=True)
    features=pd.concat(all_features,ignore_index=True)
    for frame in [daily,states,features]:
        for col in set(['symbol','signal_version','eligibility_status','st_status','suspension_status','board','adjustflag']).intersection(frame.columns):
            frame[col]=frame[col].astype('category')
    for name,frame in [('DAILY.parquet',daily),('STATES.parquet',states),('FEATURES.parquet',features)]:
        frame.to_parquet(ROOT/name,index=False)
    save(ROOT/'WINDOW_HAZARDS.json',{'by_symbol':hazards,'count':sum(map(len,hazards.values())),
        'meaning':'PROVIDER_ADJUSTMENT_DATES_UNSUPPORTED_ACCOUNTING_HAZARDS_NOT_KNOWN_NONE'})
    files={n:sha(ROOT/n) for n in ['DAILY.parquet','STATES.parquet','FEATURES.parquet','WINDOW_HAZARDS.json']}
    identity=stable_hash({'inputs':inputs,'files':files,'contract':contract(),'release':grant['identity']})
    save(ROOT/'INPUT_MANIFEST.json',{'input_identity':identity,'contract':contract(),'inputs':inputs,'files':files,
        'sessions':days,'symbols':sorted(hazards),'coverage':coverage,'universe_sha256':sha(ROOT/'WINDOW_UNIVERSE.json')})
    paths=[]
    for day,rows in features.groupby('timestamp',observed=True):
        i=days.index(int(day))
        if i+1>=len(days):continue
        for rank,row in enumerate(rows[rows.value<0].sort_values(['value','symbol']).head(3).itertuples(),1):
            paths.append({'symbol':row.symbol,'signal_session':int(day),'rank':rank,'entry_date':days[i+1],
                'exit_date':days[i+22] if i+22<len(days) else None})
    result=check_feasibility(load_bundle(),candidate_paths=paths)
    result.update(feasibility(result['paths'],contract()))
    result.update(type='MONTHLY_WINDOW_FEASIBILITY_V1',hazard_records=sum(map(len,hazards.values())),
                  adapter_version='BAOSTOCK_INDEPENDENT_WINDOW_V1')
    save(ROOT/'FEASIBILITY.json',json.loads(json.dumps(result,default=str,allow_nan=False)))
    save(ROOT/'READY.json',{'status':'READY' if result['passed'] else 'NOT_READY','input_identity':identity,
        'feasibility_passed':result['passed'],'feasibility_sha256':sha(ROOT/'FEASIBILITY.json'),
        'novelty_status':'AUTHORIZED_FIXED_REFERENCE_REPRODUCTION','source_candidate_hash':contract()['source_candidate_contract_hash'],
        'historical_exposure_preserved':True,'STRICT_TRAIN_INPUT_READY':False})
    print({'feasibility_passed':result['passed'],'counts':result['counts']})


if __name__=='__main__':
    prepare()
