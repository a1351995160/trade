"""固定TRAIN输入投影；无收益可行性与原账户引擎共用同一数据身份。"""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from .common import stable_hash
from .degraded_train_v1 import capacity, hazard_overlap, feasibility_verdict
from .degraded_execution_v2 import CONTRACT, DegradedStateMasterV1, DegradedPriceLimitV1
from chanlun_trader.data.tdx.windowed_actions_v1 import WindowedCorporateActionDatasetV1

BASE=Path('E:/llmwiki/autonomous-strategy-research-v1/execution-data-v1/materialized-v3')
SEM=BASE.parent/'degraded-volume-semantics-v1'
OLD=BASE.parent/'degraded-train-v1'
HAZARDS=Path('E:/llmwiki/owner-execution-export-v1/run-v2/staging/gbbq_window.jsonl')


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def load_inputs():
    old_index=read(OLD/'RESULTS_INDEX.json')
    for name in ['CANDIDATE_PATHS.json','FROZEN_UNIVERSE.json','FEASIBILITY.json']:
        if sha(OLD/name)!=old_index['files'][name]['sha256']:
            raise ValueError('ORIGINAL_FEASIBILITY_EVIDENCE_CHANGED')
    sem=read(SEM/'TDX_DAY_VOLUME_SEMANTICS_V1.json')
    if sem['DERIVED_VOLUME_UNIT']!='SHARES' or sem['purpose']!='DEGRADED_TRAIN_ACCOUNT_BACKTEST_V1' or not sem['NOT_FOR_QUALIFICATION']:
        raise PermissionError('DERIVED_VOLUME_CONTRACT_NOT_VALID_FOR_THIS_USE')
    for key,name in [('evidence_matrix_sha256','VOLUME_FORMAT_EVIDENCE_MATRIX.json'),
            ('preregistration_sha256','VOLUME_SEMANTICS_PREREGISTRATION.json'),
            ('dimensional_validation_sha256','VOLUME_DIMENSIONAL_VALIDATION.json')]:
        if sha(SEM/name)!=sem[key]:
            raise ValueError('SEMANTICS_EVIDENCE_HASH_CONFLICT')
    manifest=read(BASE/'MANIFEST.json');access=[]

    def frame(name,date_col,cols):
        path=BASE/name
        if sha(path)!=manifest['files'][name]['sha256']:
            raise ValueError('FROZEN_INPUT_HASH_CONFLICT')
        p=pq.ParquetFile(path);idx=p.schema.names.index(date_col)
        for g in range(p.metadata.num_row_groups):
            st=p.metadata.row_group(g).column(idx).statistics
            if st is None or not 20220722<=int(str(st.min).replace('-',''))<=int(str(st.max).replace('-',''))<=20240731:
                raise PermissionError('TRAIN_PHYSICAL_WINDOW_UNPROVEN')
        data=pq.read_table(path,columns=cols,use_threads=False).to_pandas(use_threads=False)
        data[date_col]=data[date_col].astype(str).str.replace('-','').astype(int)
        if data.duplicated(['symbol',date_col]).any():
            raise ValueError('DUPLICATE_INPUT_KEY')
        access.append({'path':str(path),'sha256':sha(path),'columns':cols,'rows':len(data)})
        return data

    days=read(BASE/'CALENDAR.json')['sessions']
    if sha(BASE/'CALENDAR.json')!=manifest['files']['CALENDAR.json']['sha256']:
        raise ValueError('CALENDAR_IDENTITY_CONFLICT')
    universe=read(OLD/'FROZEN_UNIVERSE.json');symbols=universe['symbols']
    if len(symbols)!=5182 or len(universe['missing_source_symbols'])!=146:
        raise ValueError('UNIVERSE_CHANGED')
    daily=frame('DAILY.parquet','date',['symbol','date','open','high','low','close','volume_encoded','amount_encoded','source_published_at'])
    daily=daily.rename(columns={'volume_encoded':'volume','amount_encoded':'amount'})
    factors=frame('RETURN_5D_VALUES.parquet','timestamp',['symbol','timestamp','value'])
    states=frame('HISTORICAL_POOL_AND_STATE.parquet','trade_date',['symbol','trade_date','listed','delisted','universe_member','eligibility_status','st_status','suspension_status','board'])

    def mat(frame,day,value):
        return frame.pivot(index=day,columns='symbol',values=value).reindex(index=days,columns=symbols)

    close=mat(daily,'date','close')
    previous=close.shift(1).stack(future_stack=True).rename('prev_close')
    daily=daily.join(previous,on=['date','symbol'])
    valid=np.logical_and.reduce([np.isfinite(mat(daily,'date',k)) & (mat(daily,'date',k)>0) for k in ['open','high','low','close']])
    states['known']=states.st_status.isin(['NORMAL','ST']) & states.suspension_status.isin(['TRADING','SUSPENDED']) & (states.eligibility_status!='CONFLICT')
    states['eligible']=states.known & states.listed & ~states.delisted & states.universe_member & (states.eligibility_status=='ELIGIBLE') & (states.st_status=='NORMAL') & (states.suspension_status=='TRADING')
    known=mat(states,'trade_date','known').fillna(False).to_numpy(dtype=bool)
    eligible=mat(states,'trade_date','eligible').fillna(False).to_numpy(dtype=bool)
    dependency=pd.DataFrame(valid & known).rolling(6,min_periods=6).sum().eq(6).to_numpy()
    values=mat(factors,'timestamp','value').to_numpy(dtype=float)
    ready=dependency & eligible & np.isfinite(values)
    observed=mat(daily,'date','source_published_at')
    late=pd.DataFrame(np.zeros_like(values))
    for i in range(len(days)):
        for symbol,value in observed.iloc[i].dropna().items():
            if value!='UNKNOWN':
                late.iat[i,symbols.index(symbol)]=pd.Timestamp(value).timestamp()
    observed_max=late.rolling(6,min_periods=1).max().to_numpy()
    ready_rows=[];coverage=[]
    for i,day in enumerate(days):
        if day<20220801:
            continue
        modeled=pd.Timestamp(str(days[i+1])+' 09:30',tz='Asia/Shanghai') if i+1<len(days) else None
        if modeled is not None:
            ready[i] &= observed_max[i]<=modeled.timestamp()
        else:
            ready[i]=False
        selected=np.flatnonzero(ready[i])
        ready_rows.append(pd.DataFrame({'symbol':[symbols[j] for j in selected],'timestamp':day,
            'value':values[i,selected],'effective_available_at':modeled}))
        coverage.append({'date':day,'frozen_universe_count':5182,'observable_count':int(valid[i].sum()),
            'unavailable_count':int((~valid[i]).sum()),'eligible_count':int(ready[i].sum()),
            'ranked_count':int((ready[i] & (values[i]<0)).sum())})
    hazards={};count=0
    if sha(HAZARDS)!='f2bc6064f9ab495fc854efdc6a4942dd4a87dc7be263de6e13da9be65e8a1927':
        raise ValueError('HAZARD_SOURCE_CHANGED')
    with HAZARDS.open(encoding='utf-8') as stream:
        for line in stream:
            row=json.loads(line);hazards.setdefault(row['symbol'],[]).append(int(row['datetime']));count+=1
    if count!=29990:
        raise ValueError('HAZARD_COUNT_CHANGED')
    identity=stable_hash({'manifest':sha(BASE/'MANIFEST.json'),'semantics':sha(SEM/'TDX_DAY_VOLUME_SEMANTICS_V1.json'),
        'universe':sha(OLD/'FROZEN_UNIVERSE.json'),'hazards':sha(HAZARDS),
        'candidate_paths':sha(OLD/'CANDIDATE_PATHS.json'),'contract':CONTRACT})
    # 空事件账本只承载无hazard路径；全部本地记录另由hazard门控，不声明KNOWN_NONE。
    actions=WindowedCorporateActionDatasetV1('DEGRADED_HAZARD_GUARDED_NO_EVENT_ACCOUNTING',
        'WindowedCorporateActionDatasetV1',20220722,20240731,(),sha(HAZARDS),tuple(symbols),'LOCAL_HAZARDS_NOT_COMPLETE_ACTION_ACCOUNTING')
    return SimpleNamespace(daily=daily,states=states.drop(columns=['known','eligible']),
        ready_factors=pd.concat(ready_rows,ignore_index=True),calendar=days,hazards=hazards,actions=actions,
        coverage=coverage,access=access,input_identity=identity,pool_identity=sha(OLD/'FROZEN_UNIVERSE.json'),
        factor_identity=manifest['files']['FACTOR_BINDING.json']['sha256'],calendar_identity=manifest['files']['CALENDAR.json']['sha256'],
        contract_identity=stable_hash(CONTRACT))


def check_feasibility(bundle, candidate_paths=None):
    """只检查静态候选路径的开闭条件，不创建账本、不计算价格差。"""
    reality=DegradedPriceLimitV1(DegradedStateMasterV1(bundle.states))
    daily=bundle.daily.set_index(['symbol','date'])
    days=bundle.calendar;paths=[]
    # 延续原1443条事前候选，包含最后15条；不按容量/hazard另选替补。
    originals=read(OLD/'CANDIDATE_PATHS.json') if candidate_paths is None else candidate_paths
    for old in originals:
        symbol=old['symbol'];signal=old['signal_session'];entry=old['entry_date'];exit_day=old['exit_date']
        row={k:old[k] for k in ['symbol','signal_session','rank','entry_date','exit_date']}
        if exit_day is None:
            row['reason']='END_OF_TRAIN_NO_COMPLETE_CLOSURE_PATH';paths.append(row);continue
        hits=hazard_overlap(bundle.hazards.get(symbol,()),signal,exit_day)
        row['hazard_dates']=hits
        entry_bar=daily.loc[(symbol,entry)].to_dict() if (symbol,entry) in daily.index else None
        exit_bar=daily.loc[(symbol,exit_day)].to_dict() if (symbol,exit_day) in daily.index else None
        requested=int((10000/3)/(entry_bar['open']*1.001)/100)*100 if entry_bar else 0
        caps=[]
        for day in [entry,exit_day]:
            prior_volume=0
            for prev in reversed(days[:days.index(day)]):
                if (symbol,prev) in daily.index and daily.loc[(symbol,prev),'volume']>0:
                    prior_volume=float(daily.loc[(symbol,prev),'volume']);break
            caps.append(capacity(prior_volume,requested,schema_proven=True))
        row['entry_capacity'],row['exit_capacity']=caps
        buy_qty=min(requested,caps[0]['accepted_qty'])//100*100
        row['entry_quantity']=buy_qty
        buy_ok,buy_reason=reality.can_buy_at_open(symbol,pd.Timestamp(str(entry)+' 09:30',tz='Asia/Shanghai'),entry_bar)
        sell_ok,sell_reason=reality.can_sell_at_open(symbol,pd.Timestamp(str(exit_day)+' 09:30',tz='Asia/Shanghai'),exit_bar)
        row.update(entry_reality=buy_reason,exit_reality=sell_reason)
        if hits:reason='ENTRY_REJECT_CORPORATE_ACTION_UNSUPPORTED'
        elif not buy_ok:reason=buy_reason
        elif buy_qty<=0:reason='CAPACITY_OR_LOT_SIZING_ZERO'
        elif not sell_ok:reason='EXIT_'+sell_reason
        elif caps[1]['accepted_qty']<buy_qty:reason='EXIT_CAPACITY_NO_COMPLETE_CLOSE'
        else:reason='COMPLETE_CLOSURE_PATH'
        row['reason']=reason;paths.append(row)
    return {**feasibility_verdict(paths),'type':'DEGRADED_EXECUTION_FEASIBILITY_V1',
        'adapter_version':'VOLUME_PROVEN_V2','input_identity':bundle.input_identity,'no_outcome':True,
        'candidate_paths':len(paths),'hazard_records':29990,'paths':paths,'coverage':bundle.coverage,
        'access':bundle.access,'parent_failure_preserved':str(OLD/'FEASIBILITY.json')}
