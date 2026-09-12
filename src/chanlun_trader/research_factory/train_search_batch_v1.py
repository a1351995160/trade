"""事前固定的两机制 TRAIN 探索；复用原账户规则和权威增量服务。"""
import numpy as np
import pandas as pd

from .baostock_account_v1 import CONTRACT as BASE
from .baostock_governance_v1 import BaostockGovernanceV1
from .common import stable_hash
from .technical_train_signals_v1 import FORMULAS as TECHNICAL_FORMULAS, WARMUP


FORMULAS = {
    **TECHNICAL_FORMULAS,
    'MOMENTUM_5': '-RETURN_5D',
    'STABILITY_20': '-1/(1+STD_POPULATION(RETURN_5D[t-19:t]))',
    'MOMENTUM_60': '-(PRODUCT(1+RETURN_5D[t-5*k], k=0..11)-1)',
    'LIQUIDITY_20': '-MEAN(AMOUNT_CNY[t-19:t])',
    'STABILITY_20_HOLD_20': '-1/(1+STD_POPULATION(RETURN_5D[t-19:t]))',
    'LIQUIDITY_20_HOLD_20': '-MEAN(AMOUNT_CNY[t-19:t])',
    'MOMENTUM_60_HOLD_20_MARKET_5': '-(PRODUCT(1+RETURN_5D[t-5*k], k=0..11)-1)',
    'STABILITY_20_HOLD_20_MARKET_5': '-1/(1+STD_POPULATION(RETURN_5D[t-19:t]))',
}


def contract(name):
    if name not in FORMULAS:
        raise ValueError('UNFROZEN_MECHANISM')
    return {**BASE, 'version': 'TRAIN_SEARCH_BATCH_V1_' + name,
            **({'technical_warmup_sessions':WARMUP[name],
                 'technical_price_basis':'SAME_DAY_HFQ_CLOSE_OVER_RAW_CLOSE_SCALED_OHLC',
                 'technical_missing_policy':'RESET_SEGMENT_NO_FILL',
                 'technical_initialization':'EMA_FIRST_CLOSE; KDJ_K_D_50; SEE_FROZEN_SOURCE'}
               if name in TECHNICAL_FORMULAS else {}),
            'holding_sessions':20 if '_HOLD_20' in name else 3,
            **({'market_gate':'MEDIAN_ELIGIBLE_RETURN_5D_GT_0'} if name.endswith('_MARKET_5') else {}),
            'adapter_version': 'TRAIN_SEARCH_BATCH_V1',
            'signal_version': 'TRAIN_SEARCH_BATCH_V1_' + name,
            'factor_id': name, 'signal_formula': FORMULAS[name],
            'purpose': 'USER_DELEGATED_TRAIN_MECHANISM_SEARCH',
            'result_type': 'TRAIN_SEARCH_ACCOUNT_EXPLORATORY',
            'historical_train_exposure': True}


def design(name):
    frozen = contract(name)
    if name in TECHNICAL_FORMULAS:
        return {'candidate_id':stable_hash(frozen),'candidate_hash':stable_hash(frozen),
                'mechanism':name.removesuffix('_HOLD_20'), 'factor_ids':[name.removesuffix('_HOLD_20')],
                'semantic_fingerprint':FORMULAS[name],
                'parameter_fingerprint':{'formula':FORMULAS[name],'warmup':WARMUP[name],
                                         'top_n':3,'holding_sessions':20},'holding_period_days':20}
    base_name = name.removesuffix('_MARKET_5').removesuffix('_HOLD_20')
    mechanisms = {
        'MOMENTUM_5': 'POSITIVE_FIVE_SESSION_PRICE_CHANGE',
        'STABILITY_20': 'LOW_DISPERSION_OF_OVERLAPPING_FIVE_SESSION_PRICE_CHANGES',
        'MOMENTUM_60': 'POSITIVE_SIXTY_SESSION_PRICE_CHANGE',
        'LIQUIDITY_20': 'HIGH_TRAILING_CNY_TURNOVER_LIQUIDITY',
    }
    return {'candidate_id': stable_hash(frozen), 'candidate_hash': stable_hash(frozen),
            'mechanism': mechanisms[base_name],
            'factor_ids': ['AMOUNT' if base_name == 'LIQUIDITY_20' else 'RETURN_5D'],
            'semantic_fingerprint': FORMULAS[name],
            'parameter_fingerprint': {'window': {'MOMENTUM_5':1,'MOMENTUM_60':56}.get(base_name,20),
                                      **({'market_gate':'MEDIAN_ELIGIBLE_RETURN_5D_GT_0'} if name.endswith('_MARKET_5') else {}),
                                      'top_n': 3, 'holding_sessions': frozen['holding_sessions']},
            'holding_period_days': frozen['holding_sessions']}


def transform(rows, sessions, name):
    """只在独立日历上组合已核验特征；缺日不压缩，源可见时间取依赖最大值。"""
    if name in TECHNICAL_FORMULAS:
        from .technical_train_signals_v1 import transform as technical_transform
        return technical_transform(rows,sessions,name)
    frozen = contract(name)
    market_gate = name.endswith('_MARKET_5')
    name = name.removesuffix('_MARKET_5').removesuffix('_HOLD_20')
    if sessions != sorted(set(sessions)):
        raise ValueError('INDEPENDENT_CALENDAR_REQUIRED')
    if rows.timestamp.duplicated().any() or not rows.timestamp.isin(sessions).all():
        raise ValueError('FEATURE_SESSION_IDENTITY_CONFLICT')
    if rows.symbol.nunique() != 1:
        raise ValueError('ONE_SYMBOL_REQUIRED')
    aligned = rows.set_index('timestamp').reindex(sessions)
    values = aligned.value.astype(float)
    values = values.where(np.isfinite(values))
    width = {'MOMENTUM_5':1,'MOMENTUM_60':56}.get(name,20)
    if name == 'MOMENTUM_5':
        score = -values
    elif name == 'STABILITY_20':
        score = -1 / (1 + values.rolling(width, min_periods=width).std(ddof=0))
    elif name == 'MOMENTUM_60':
        gross = pd.Series(1.0,index=sessions)
        for lag in range(0,60,5):
            gross *= 1 + values.shift(lag)
        score = (1-gross).where(values.rolling(width,min_periods=width).count().eq(width))
    else:
        amount = pd.to_numeric(aligned.amount,errors='raise')
        amount = amount.where(np.isfinite(amount) & amount.gt(0))
        score = -amount.rolling(width,min_periods=width).mean()
    score = score.where(np.isfinite(score))
    times = pd.to_datetime(aligned.effective_available_at, utc=True)
    seconds = pd.Series([t.timestamp() if pd.notna(t) else np.nan for t in times], index=sessions)
    latest = seconds.rolling(width, min_periods=width).max()
    if market_gate:
        median = pd.to_numeric(aligned.market_median,errors='raise')
        score = score.where(median.gt(0),1.0).where(score.notna() & median.notna())
        market_times = pd.to_datetime(aligned.market_available_at,utc=True)
        market_seconds = pd.Series([t.timestamp() if pd.notna(t) else np.nan
                                    for t in market_times],index=sessions)
        latest = pd.concat([latest,market_seconds],axis=1).max(axis=1).where(latest.notna() & market_seconds.notna())
    out = pd.DataFrame({'symbol': rows.symbol.iloc[0], 'timestamp': sessions,
                        'value': score.to_numpy(),
                        'effective_available_at': pd.to_datetime(latest.to_numpy(), unit='s', utc=True),
                        'signal_version': frozen['signal_version']})
    # 保留完整日期诊断；只有合法可计算日期进入排名。
    out['computable'] = out.value.notna() & out.effective_available_at.notna()
    return out


class TrainSearchGovernanceV1(BaostockGovernanceV1):
    schema_version = 'train-search-batch-v1'
    authorization_origin = 'USER_RESEARCH_DELEGATION_NOT_PER_CANDIDATE_APPROVAL'
    main_purpose = 'TRAIN_SEARCH_MAIN'
    repair_purpose = 'TRAIN_SEARCH_CONFIRMED_REPAIR'

    def __init__(self, root, name):
        super().__init__(root)
        self.contract = contract(name)
        self.receipt_path = self.root / 'governance/train_search_batch_v1' / name / 'confirmation.json'
        self.journal = self.receipt_path.parent / 'exposure_events.jsonl'

    def reserve(self, contract_id, repair=None):
        # 本批没有实现修复执行入口；不因结果差自动重算。
        if repair is not None:
            raise PermissionError('REPAIR_REQUIRES_SEPARATE_CONFIRMED_IMPLEMENTATION')
        return super().reserve(contract_id)
