"""事前固定的两机制 TRAIN 探索；复用原账户规则和权威增量服务。"""
import numpy as np
import pandas as pd
from copy import deepcopy

from .baostock_account_v1 import CONTRACT as BASE
from .baostock_governance_v1 import BaostockGovernanceV1
from .common import stable_hash
from .technical_train_signals_v1 import FORMULAS as TECHNICAL_FORMULAS, WARMUP
from .structured_exit_trial_v1 import NAMES as STRUCTURED_NAMES, EXIT_POLICY, WITH_MARKET
from .weekly_defensive_signals_v1 import NAMES as DEFENSIVE_NAMES
from .weekly_fixed_trial_v1 import WITH_MARKET as FIXED_MARKET


from .recovery_rotation_signals_v1 import RECOVERY, EXIT_POLICY as RECOVERY_EXIT

from .response_confirmation_signals_v1 import NAMES as CONFIRM_NAMES
from .lag_response_signals_v1 import NAMES as LAG_NAMES
from .turnover_regime_signals_v1 import ALL_NAMES as TURNOVER_NAMES, LOW20
from .calendar_recovery_signals_v1 import NAME as MONTH_START
from .opening_pressure_signals_v1 import NAMES as OPENING_PRESSURE_NAMES
from .scale_proxy_signals_v1 import NAMES as SCALE_PROXY_NAMES
from .participation_stability_signals_v1 import NAMES as PARTICIPATION_NAMES
from .trend_risk_horizon_signals_v1 import NAMES as TREND_RISK_NAMES
from .breadth_regime_signals_v1 import NAMES as BREADTH_NAMES
from .stock_trend_only_signals_v1 import NAME as STOCK_TREND_ONLY
from .failed_low_break_signals_v1 import NAMES as FAILED_LOW_NAMES
from .low_skew_signals_v1 import NAMES as LOW_SKEW_NAMES
from .affordable_portfolio_signals_v1 import NAMES as AFFORDABLE_NAMES, MOM as AFFORDABLE_MOM, PARENTS as AFFORDABLE_PARENTS, POLICY as AFFORDABLE_POLICY
from .alpha191_pressure_signals_v1 import NAMES as A191_NAMES
from .shock_consolidation_signals_v1 import NAMES as SHOCK_NAMES
from .skill_alpha_signals_v1 import NAMES as SKILL_ALPHA_NAMES
from .market_sensitivity_signals_v1 import NAMES as SENSITIVITY_NAMES
from .market_residual_signals_v1 import FORMULAS as RESIDUAL, WARMUP as RESIDUAL_WARMUP, RISK_NAMES as RESIDUAL_RISK
from .market_residual_signals_v1 import EXIT_NAMES as RESIDUAL_EXITS, EXIT_POLICY as RESIDUAL_EXIT

FORMULAS = {
    **RESIDUAL,
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
            **({'signal_day_affordability':deepcopy(AFFORDABLE_POLICY),'skewness_definition':'TOTAL_DAILY_RETURN_POPULATION_MOMENT_20_NOT_IDIOSYNCRATIC'} if name in LOW_SKEW_NAMES else {}),
            **({'signal_day_affordability':deepcopy(AFFORDABLE_POLICY),'method_adaptation':'FAILED_20_LOW_EOD_CONFIRMATION_NOT_INTRADAY_TURTLE_SOUP'} if name in FAILED_LOW_NAMES else {}),
            **({'parent_candidate':'WEEKLY_LOW_VOL_TREND60_FIXED_HOLD_20','registered_ablation':'NO_AGGREGATE_MARKET_ENTRY_GATE','signal_day_affordability':deepcopy(AFFORDABLE_POLICY)} if name==STOCK_TREND_ONLY else {}),
            **({'parent_candidate':'WEEKLY_LOW_VOL_TREND60_FIXED_HOLD_20','market_context':'ELIGIBLE_RETURN5_POSITIVE_FRACTION_100_MIN_MEMBERS','signal_day_affordability':deepcopy(AFFORDABLE_POLICY)} if name in BREADTH_NAMES else {}),
            **({'parent_candidate':'AFFORDABLE_WEEKLY_LOW_VOL_MARKET_HOLD_20','registered_horizon_variant':'SMA200_TO_SMA60_NOT_ENGINEERING_REPAIR','signal_day_affordability':deepcopy(AFFORDABLE_POLICY)} if name in TREND_RISK_NAMES else {}),
            **({'signal_day_affordability':deepcopy(AFFORDABLE_POLICY),'volume_semantics':'RAW_SHARE_VOLUME_CV_FIXED_20'} if name in PARTICIPATION_NAMES else {}),
            **({'scale_proxy':'RAW_CLOSE_TIMES_RAW_VOLUME_DIV_VENDOR_TURN','scale_evidence':'DERIVED_SCALE_SORT_PROXY_NOT_VENDOR_CAPITALIZATION','signal_day_affordability':deepcopy(AFFORDABLE_POLICY)} if name in SCALE_PROXY_NAMES else {}),
            **({'parent_candidate':'WEEKLY_OVERNIGHT_SUPPORT_HOLD_20','signal_day_affordability':deepcopy(AFFORDABLE_POLICY)} if name in OPENING_PRESSURE_NAMES else {}),
            **({'parent_candidate':AFFORDABLE_PARENTS[name],'signal_day_affordability':deepcopy(AFFORDABLE_POLICY)} if name in AFFORDABLE_NAMES else {}),
            **({'parent_candidate':'LOW_TURNOVER_WEEKLY_MOMENTUM_HOLD_3'} if name==LOW20 else {}),
            **({'parent_candidate':'MONTHLY_REVERSAL_HOLD_20'} if name==MONTH_START else {}),
            **({'supplemental_input':'TRAIN_TURNOVER_INPUT_V1','turnover_timing_model':'MODELED_SESSION_1800_FOR_TURNOVER_NOT_FINANCIAL_PUBLICATION','turnover_denominator_vintage':'NOT_PIT_ATTESTED','turnover_min_peers':100} if name in (*TURNOVER_NAMES,AFFORDABLE_MOM,*SCALE_PROXY_NAMES) else {}),
            **({'exit_policy':deepcopy(EXIT_POLICY),'exit_input_timing':'PRIOR_SESSION_ASOF_CLOSE_NEXT_OPEN_ORDER'} if name in (*STRUCTURED_NAMES,*DEFENSIVE_NAMES) else {}),
            **({'exit_policy':deepcopy(RECOVERY_EXIT),'exit_input_timing':'PRIOR_SESSION_ASOF_CLOSE_NEXT_OPEN_ORDER'} if name==RECOVERY else {}),
            **({'exit_policy':deepcopy(RESIDUAL_EXIT),'exit_input_timing':'PRIOR_SESSION_ASOF_CLOSE_NEXT_OPEN_ORDER'} if name in RESIDUAL_EXITS else {}),
            **({'market_gate':'MEDIAN_ELIGIBLE_RETURN_5D_GT_0'} if name in (*RESIDUAL_RISK,*RESIDUAL_EXITS,*SENSITIVITY_NAMES,*SKILL_ALPHA_NAMES,*LAG_NAMES,*CONFIRM_NAMES,*SHOCK_NAMES,*A191_NAMES,*TURNOVER_NAMES,*AFFORDABLE_NAMES,*OPENING_PRESSURE_NAMES,*SCALE_PROXY_NAMES,*PARTICIPATION_NAMES,*TREND_RISK_NAMES,*FAILED_LOW_NAMES,*LOW_SKEW_NAMES) else {}),
            **({'market_context':'MEDIAN_ELIGIBLE_RETURN_5D','residual_fit_sessions':60,'residual_warmup_sessions':RESIDUAL_WARMUP[name]} if name in RESIDUAL else {}),
            **({'technical_warmup_sessions':WARMUP[name],
                 'technical_price_basis':'SAME_DAY_HFQ_CLOSE_OVER_RAW_CLOSE_SCALED_OHLC',
                 'technical_missing_policy':'RESET_SEGMENT_NO_FILL',
                 'technical_initialization':'EMA_FIRST_CLOSE; KDJ_K_D_50; SEE_FROZEN_SOURCE'}
               if name in TECHNICAL_FORMULAS else {}),
            'holding_sessions':20 if '_HOLD_20' in name else 3,
            **({'market_gate':'MEDIAN_ELIGIBLE_RETURN_5D_GT_0'} if name.endswith('_MARKET_5') or name in (WITH_MARKET,FIXED_MARKET) else {}),
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
                                         'top_n':3,'holding_sessions':frozen['holding_sessions']},'holding_period_days':frozen['holding_sessions']}
    if name in RESIDUAL:
        return {'candidate_id':stable_hash(frozen),'candidate_hash':stable_hash(frozen),
            'mechanism':name.removesuffix('_HOLD_20'),'factor_ids':['RETURN_5D','MEDIAN_ELIGIBLE_RETURN_5D'],
            'semantic_fingerprint':RESIDUAL[name],
            'parameter_fingerprint':{'fit_sessions':60,'warmup':RESIDUAL_WARMUP[name],'formula':RESIDUAL[name],'top_n':3,'holding_sessions':frozen['holding_sessions']},
            'holding_period_days':frozen['holding_sessions']}
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
    if name in RESIDUAL:
        from .market_residual_signals_v1 import transform as residual_transform
        return residual_transform(rows,sessions,name)
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
