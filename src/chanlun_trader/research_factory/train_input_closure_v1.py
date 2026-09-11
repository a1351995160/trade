"""限定TRAIN闭包：原输入加所有者补件，日期/单位/状态/行动证据缺失则拒绝。"""
from dataclasses import dataclass
from pathlib import Path
import hashlib

import numpy as np
import pandas as pd

from chanlun_trader.data.tdx.windowed_actions_v1 import WindowedCorporateActionDatasetV1
from chanlun_trader.research.unified_factor import AsOfDataView,FactorCompiler
from .common import stable_hash
from .train_account_runner_v1 import FIXED_CONTRACT


def file_hash(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def merge_daily_evidence(original,supplement):
    """补充缺价格日或来源给出的参考价/发布时间，不静默更换已有OHLC量额。"""
    keys=['symbol','date']
    if supplement.duplicated(keys).any():raise ValueError('DUPLICATE_DAILY_SUPPLEMENT')
    left=original.copy().set_index(keys);right=supplement.set_index(keys)
    immutable_fields=['open','high','low','close','volume_encoded','amount_encoded']
    for key,row in right.iterrows():
        if key not in left.index:
            left.loc[key,right.columns]=row
            continue
        for field in immutable_fields:
            if field in row and pd.notna(row[field]) and row[field]!=left.loc[key,field]:
                raise ValueError('EXISTING_DAILY_PRICE_CONFLICT')
        for field in ['exchange_reference_price','source_published_at']:
            if field not in row or pd.isna(row[field]):continue
            if field in left and pd.notna(left.loc[key,field]) and left.loc[key,field]!=row[field]:
                raise ValueError('DAILY_SOURCE_EVIDENCE_CONFLICT')
            left.loc[key,field]=row[field]
    return left.reset_index()


def merge_state_evidence(original,supplement,legacy_time_evidence):
    """新来源补证只叠加事实和时间；不覆盖原字段或冲突的状态事实。"""
    original=original.copy();supplement=supplement.copy()
    for frame in (original,supplement):
        frame['trade_date']=frame.trade_date.astype(str).str.replace('-','').astype(int)
        if frame.duplicated(['symbol','trade_date']).any():raise ValueError('DUPLICATE_STATE_EVIDENCE')
    keys=['symbol','trade_date'];left=original.set_index(keys);right=supplement.set_index(keys)
    modeled=legacy_time_evidence.get('kind')=='MODELED_NEXT_SESSION_OPEN'
    if modeled and (not legacy_time_evidence.get('owner_attestation') or not legacy_time_evidence.get('normalizer_sha256')):
        raise ValueError('LEGACY_MODELED_TIME_PROOF_MISSING')
    facts=['universe_member','listed','delisted','board','st_status','suspension_status','eligibility_status']
    shared=left.index.intersection(right.index)
    evidence=right.loc[shared]
    if not left.loc[shared,facts].equals(evidence[facts]):
        # 列类型可能由Parquet批次不同；逐值相等才是身份条件。
        if not left.loc[shared,facts].eq(evidence[facts]).all().all():
            raise ValueError('HISTORICAL_STATE_FACT_CONFLICT')
    if len(shared):
        if not {'observed_available_at','observed_time_source'}<=set(evidence.columns) or evidence[['observed_available_at','observed_time_source']].isna().any().any() or evidence.observed_time_source.isin(['','UNKNOWN']).any():
            raise ValueError('OBSERVED_STATE_TIME_SOURCE_MISSING')
        if any(pd.Timestamp(value).tzinfo is None for value in evidence.observed_available_at.unique()):
            raise ValueError('OBSERVED_STATE_TIME_MUST_BE_AWARE')
        actual=pd.to_datetime(evidence.observed_available_at,utc=True,format='ISO8601')
        effective=actual
        if not modeled:
            original_time=pd.to_datetime(left.loc[shared,'available_at'],utc=True,format='ISO8601')
            effective=actual.where(actual>=original_time,original_time)
        for field,values in [('observed_available_at',evidence.observed_available_at),
                ('observed_time_source',evidence.observed_time_source),('effective_state_available_at',effective.astype(str))]:
            if field not in left:left[field]=pd.Series(index=left.index,dtype='str')
            left.loc[shared,field]=values
    return pd.concat([left,right.loc[~right.index.isin(left.index)]]).reset_index()


@dataclass
class ClosedTrainInputV1:
    daily: pd.DataFrame
    states: pd.DataFrame
    factors: pd.DataFrame
    calendar: list
    actions: WindowedCorporateActionDatasetV1
    input_identity: str
    pool_identity: str
    factor_identity: str
    calendar_identity: str
    contract_identity: str


def close_frames(daily,states,calendar,actions,units,definition,required_members,source_identity):
    """只做规范化和向后因子；不计算未来标签、策略收益或订单。"""
    if calendar!=sorted(set(calendar)) or calendar[0]<20220722 or calendar[-1]>20240731:
        raise ValueError('INPUT_CALENDAR_WINDOW_INVALID')
    if daily.duplicated(['symbol','date']).any():raise ValueError('INPUT_DAILY_IDENTITY_CONFLICT')
    states=states.copy();states['trade_date']=states.trade_date.astype(str).str.replace('-','').astype(int)
    for column in ['universe_member','listed','delisted']:
        if not states[column].map(lambda value:isinstance(value,(bool,np.bool_))).all():
            raise ValueError('STATE_BOOLEAN_EVIDENCE_UNKNOWN')
        states[column]=states[column].astype(bool)
    if states.duplicated(['symbol','trade_date']).any():raise ValueError('INPUT_STATE_IDENTITY_CONFLICT')
    if not set(daily.date)<=set(calendar) or not set(states.trade_date)<=set(calendar):
        raise ValueError('INPUT_OUTSIDE_CALENDAR')
    members=set(states.loc[states.universe_member,'symbol'])
    if members!=set(required_members):raise ValueError('ORIGINAL_POOL_COVERAGE_NOT_CLOSED')
    if set(actions.coverage_symbols)!=members:raise ValueError('ACTION_MEMBER_COVERAGE_CONFLICT')
    if units.get('source_identity') in {None,'','UNKNOWN'} or not units.get('evidence_sha256') or units.get('price_mode')!='RAW':
        raise ValueError('UNIT_SOURCE_EVIDENCE_MISSING')
    volume_scale={'SHARES':1,'LOTS_100_SHARES':100}.get(units.get('volume_unit'))
    amount_scale={'CNY':1,'TEN_THOUSAND_CNY':10000}.get(units.get('amount_unit'))
    if volume_scale is None or amount_scale is None:raise ValueError('ENCODED_UNITS_UNKNOWN')
    expected={'op':'pct_change','args':[{'op':'field','field':'close'}],'window':5,'min_periods':6}
    if definition.operator_graph!=expected or definition.feature_price_mode!='RETURN_ONLY':
        raise ValueError('FIXED_FACTOR_DEFINITION_CONFLICT')
    daily=daily.copy();daily['volume']=daily.volume_encoded.astype(float)*volume_scale
    daily['amount']=daily.amount_encoded.astype(float)*amount_scale
    daily['modeled_available_at']=pd.to_datetime(daily.date.astype(str)).dt.tz_localize('Asia/Shanghai')+pd.Timedelta(hours=15,microseconds=1)
    daily['effective_available_at']=daily.modeled_available_at.dt.tz_convert('UTC')
    for field in ['source_published_at','available_at']:
        if field not in daily:continue
        known=daily[field].notna() & daily[field].ne('UNKNOWN')
        if any(pd.Timestamp(value).tzinfo is None for value in daily.loc[known,field].unique()):
            raise ValueError('TIME_EVIDENCE_MUST_BE_AWARE')
        observed=pd.to_datetime(daily.loc[known,field],utc=True,format='ISO8601')
        current=daily.loc[known,'effective_available_at']
        daily.loc[known,'effective_available_at']=current.where(current>=observed,observed)
    # 状态只接受原始字段，不把UNKNOWN归入可交易，也不靠模型提前已存时间。
    needed=['listed','delisted','board','eligibility_status','st_status','suspension_status','available_at']
    active=states[states.universe_member]
    if active[needed].isna().any().any() or (~active.st_status.isin(['NORMAL','ST'])).any() or (~active.suspension_status.isin(['TRADING','SUSPENDED'])).any():
        raise ValueError('STATE_EVIDENCE_UNKNOWN')
    if (~active.eligibility_status.isin(['ELIGIBLE','INELIGIBLE'])).any():raise ValueError('STATE_ELIGIBILITY_CONFLICT')
    state_time=active['effective_state_available_at'].fillna(active.available_at) if 'effective_state_available_at' in active else active.available_at
    if any(pd.Timestamp(value).tzinfo is None for value in state_time.unique()):
        raise ValueError('STATE_TIME_SOURCE_MUST_BE_AWARE')
    observed=pd.to_datetime(state_time,utc=True,format='ISO8601')
    opening=pd.to_datetime(active.trade_date.astype(str)).dt.tz_localize('Asia/Shanghai')+pd.Timedelta(hours=9,minutes=30)
    if (observed.to_numpy()>opening.dt.tz_convert('UTC').to_numpy()).any():
        raise ValueError('STATE_PIT_TIMING_NOT_CLOSED')
    grid=pd.MultiIndex.from_product([sorted(members),calendar],names=['symbol','date'])
    state_index=states.set_index(['symbol','trade_date']).reindex(grid)
    if state_index.universe_member.isna().any():raise ValueError('SECURITY_DAY_STATE_COVERAGE_MISSING')
    panel=daily.set_index(['symbol','date']).reindex(grid).reset_index()
    missing=panel[['open','high','low','close','volume','amount']].isna().any(axis=1).to_numpy()
    # 缺价格保留；只有明确非成员/停牌才能作为无Bar日，未知绝不推断。
    permitted=(~state_index.universe_member)|(state_index.suspension_status=='SUSPENDED')
    if (missing & ~permitted.to_numpy()).any():raise ValueError('MEMBER_PRICE_COVERAGE_MISSING')
    present=panel.loc[~missing]
    if not np.isfinite(present[['open','high','low','close','volume','amount']].astype(float)).all().all() or (present[['open','high','low','close']]<=0).any().any() or (present.high<present[['open','close','low']].max(axis=1)).any() or (present.low>present[['open','close','high']].min(axis=1)).any():
        raise ValueError('OHLC_SOURCE_CONFLICT')
    if (present[['volume','amount']]<0).any().any():raise ValueError('NEGATIVE_VOLUME_OR_AMOUNT')
    panel['input_computability']=np.where(missing,'NO_BAR_WITH_STATE_EVIDENCE','BAR_PRESENT')
    results=[]
    for symbol,group in panel.groupby('symbol',sort=True):
        group=group.copy();group['close']=group.close.astype(float)
        values=FactorCompiler().execute(definition,AsOfDataView(group[['date','symbol','close']],source_identity,'ORIGINAL_POOL',as_of=calendar[-1]))
        valid=(np.isfinite(group.close)&(group.close>0)).rolling(6,min_periods=6).sum().eq(6).to_numpy()
        values.loc[~valid,'value']=np.nan
        times=pd.to_datetime(group.effective_available_at,utc=True).astype('datetime64[ns, UTC]')
        stamps=times.astype('int64').to_numpy()
        maxima=np.full(len(stamps),np.iinfo(np.int64).min,dtype=np.int64)
        if len(stamps)>=6:
            windows=np.lib.stride_tricks.sliding_window_view(stamps,6)
            maxima[5:]=windows.max(axis=1)
            maxima[5:][(windows==np.iinfo(np.int64).min).any(axis=1)]=np.iinfo(np.int64).min
        values['effective_available_at']=pd.to_datetime(maxima,unit='ns',utc=True)
        results.append(values)
    factors=pd.concat(results,ignore_index=True)
    panel['prev_close']=panel.groupby('symbol').close.shift(1)
    for event in actions.events:
        key=(panel.symbol==event['symbol'])&(panel.date==event['effective_date'])
        if key.any():
            if 'exchange_reference_price' not in panel or panel.loc[key,'exchange_reference_price'].isna().any():
                raise ValueError('CORPORATE_ACTION_EXCHANGE_REFERENCE_PRICE_MISSING')
            panel.loc[key,'prev_close']=panel.loc[key,'exchange_reference_price']
    identities={'source':source_identity,'calendar':stable_hash(calendar),'pool':stable_hash(sorted(members)),
        'factor':stable_hash(definition.to_dict()) if hasattr(definition,'to_dict') else stable_hash(vars(definition)),
        'actions':actions.manifest_sha256,'units':units,'contract':FIXED_CONTRACT}
    return ClosedTrainInputV1(panel,states,factors,calendar,actions,stable_hash(identities),
        identities['pool'],identities['factor'],identities['calendar'],stable_hash(FIXED_CONTRACT))
