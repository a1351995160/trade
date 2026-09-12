"""事前固定的两机制 TRAIN 探索；复用原账户规则和权威增量服务。"""
import numpy as np
import pandas as pd

from .baostock_account_v1 import CONTRACT as BASE
from .baostock_governance_v1 import BaostockGovernanceV1
from .common import stable_hash


FORMULAS = {
    'MOMENTUM_5': '-RETURN_5D',
    'STABILITY_20': '-1/(1+STD_POPULATION(RETURN_5D[t-19:t]))',
}


def contract(name):
    if name not in FORMULAS:
        raise ValueError('UNFROZEN_MECHANISM')
    return {**BASE, 'version': 'TRAIN_SEARCH_BATCH_V1_' + name,
            'adapter_version': 'TRAIN_SEARCH_BATCH_V1',
            'signal_version': 'TRAIN_SEARCH_BATCH_V1_' + name,
            'factor_id': name, 'signal_formula': FORMULAS[name],
            'purpose': 'USER_DELEGATED_TRAIN_MECHANISM_SEARCH',
            'result_type': 'TRAIN_SEARCH_ACCOUNT_EXPLORATORY',
            'historical_train_exposure': True}


def design(name):
    frozen = contract(name)
    return {'candidate_id': stable_hash(frozen), 'candidate_hash': stable_hash(frozen),
            'mechanism': 'POSITIVE_FIVE_SESSION_PRICE_CHANGE' if name == 'MOMENTUM_5'
                         else 'LOW_DISPERSION_OF_OVERLAPPING_FIVE_SESSION_PRICE_CHANGES',
            'factor_ids': ['RETURN_5D'], 'semantic_fingerprint': FORMULAS[name],
            'parameter_fingerprint': {'window': 1 if name == 'MOMENTUM_5' else 20,
                                      'top_n': 3, 'holding_sessions': 3},
            'holding_period_days': 3}


def transform(rows, sessions, name):
    """只在独立日历上组合已核验特征；缺日不压缩，源可见时间取依赖最大值。"""
    frozen = contract(name)
    if sessions != sorted(set(sessions)):
        raise ValueError('INDEPENDENT_CALENDAR_REQUIRED')
    if rows.timestamp.duplicated().any() or not rows.timestamp.isin(sessions).all():
        raise ValueError('FEATURE_SESSION_IDENTITY_CONFLICT')
    if rows.symbol.nunique() != 1:
        raise ValueError('ONE_SYMBOL_REQUIRED')
    aligned = rows.set_index('timestamp').reindex(sessions)
    values = aligned.value.astype(float)
    values = values.where(np.isfinite(values))
    width = 1 if name == 'MOMENTUM_5' else 20
    score = -values if width == 1 else -1 / (1 + values.rolling(width, min_periods=width).std(ddof=0))
    times = pd.to_datetime(aligned.effective_available_at, utc=True)
    seconds = pd.Series([t.timestamp() if pd.notna(t) else np.nan for t in times], index=sessions)
    latest = seconds.rolling(width, min_periods=width).max()
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
