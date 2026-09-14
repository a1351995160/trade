"""固定探索账户的共享信号规则；不读取文件、不创建订单或授予运行许可。"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np
import pandas as pd

from ..engine.ledger import PortfolioLedger
from ..engine.portfolio_exit import PortfolioExitDecision, PortfolioExitEvaluatorV1
from ..engine.signal import ExecutionPolicy, Side, Signal
from ..engine.time_types import TradingCalendar
from .degraded_train_v1 import hazard_overlap
from .structured_exit_trial_v1 import NAMES as STRUCTURED_NAMES, EXIT_POLICY
from .weekly_defensive_signals_v1 import NAMES as DEFENSIVE_NAMES
from .weekly_fixed_trial_v1 import NAMES as FIXED_DEFENSIVE_NAMES
from .price_volume_patterns_v1 import NAMES as PRICE_VOLUME_NAMES


from .recovery_rotation_signals_v1 import NAMES as RECOVERY_NAMES, RECOVERY, EXIT_POLICY as RECOVERY_EXIT

from .volume_flow_signals_v1 import NAMES as FLOW_NAMES

from .market_residual_signals_v1 import NAMES as RESIDUAL_NAMES, RISK_NAMES as RESIDUAL_RISK
from .market_residual_signals_v1 import EXIT_NAMES as RESIDUAL_EXITS, EXIT_POLICY as RESIDUAL_EXIT
from .return_path_signals_v1 import NAMES as RETURN_PATH_NAMES
from .price_impact_signals_v1 import NAMES as PRICE_IMPACT_NAMES
from .market_sensitivity_signals_v1 import NAMES as SENSITIVITY_NAMES
from .response_confirmation_signals_v1 import NAMES as CONFIRM_NAMES

@dataclass
class FixedAccountEntryDecision:
    status: str
    reason: str
    ranking: dict[str, Any] | None
    checks: list[dict[str, Any]]
    signals: list[Signal]


class FixedAccountRules:
    """只承接现有固定合同；新策略不能靠更换字段偷偷复用本入口。"""

    def __init__(self, contract: Mapping[str, Any]):
        self.structure_exit=contract.get('signal_version') in {'TRAIN_SEARCH_BATCH_V1_'+name for name in (*STRUCTURED_NAMES,*DEFENSIVE_NAMES)}
        recovery=contract.get('signal_version')=='TRAIN_SEARCH_BATCH_V1_'+RECOVERY
        residual_exit=contract.get('signal_version') in {'TRAIN_SEARCH_BATCH_V1_'+name for name in RESIDUAL_EXITS}
        self.structure_exit=self.structure_exit or recovery or residual_exit
        expected_exit=RESIDUAL_EXIT if residual_exit else RECOVERY_EXIT if recovery else EXIT_POLICY
        self.exit_factor=expected_exit['factor_conditions'][0]['factor_id']
        if self.structure_exit and (contract.get('exit_policy')!=expected_exit or
                contract.get('exit_input_timing')!='PRIOR_SESSION_ASOF_CLOSE_NEXT_OPEN_ORDER'):
            raise ValueError('STRUCTURED_EXIT_CONTRACT_CONFLICT')
        # 新研究变体显式版本化；旧冻结合同仍只接受3 session。
        longer_hold = contract.get('signal_version') in {
            *{'TRAIN_SEARCH_BATCH_V1_'+name for name in RETURN_PATH_NAMES},
            *{'TRAIN_SEARCH_BATCH_V1_'+name for name in PRICE_IMPACT_NAMES},
            *{'TRAIN_SEARCH_BATCH_V1_'+name for name in SENSITIVITY_NAMES},
            *{'TRAIN_SEARCH_BATCH_V1_'+name for name in CONFIRM_NAMES},
            'TRAIN_SEARCH_BATCH_V1_LOW_TURNOVER_WEEKLY_MOMENTUM_HOLD_20',
            'TRAIN_SEARCH_BATCH_V1_WEEKLY_LOW_VOL_STOCK_TREND_ONLY_HOLD_20',
            'TRAIN_SEARCH_BATCH_V1_WEEKLY_LOW_TOTAL_SKEW_TREND_GATE_HOLD_20',
            'TRAIN_SEARCH_BATCH_V1_WEEKLY_LOW_VOL_BREADTH_PERSISTENCE_HOLD_20',
            'TRAIN_SEARCH_BATCH_V1_WEEKLY_LOW_VOL_BREADTH_RECOVERY_HOLD_20',
            'TRAIN_SEARCH_BATCH_V1_WEEKLY_LOW_VOL_TREND60_FIXED_HOLD_20',
            'TRAIN_SEARCH_BATCH_V1_WEEKLY_LOW_DRAWDOWN_TREND60_FIXED_HOLD_20',
            'TRAIN_SEARCH_BATCH_V1_WEEKLY_VOLUME_STABILITY_TREND_HOLD_20',
            'TRAIN_SEARCH_BATCH_V1_WEEKLY_VOLUME_STABILITY_DOWNSIDE_HOLD_20',
            'TRAIN_SEARCH_BATCH_V1_WEEKLY_SMALL_SCALE_TREND_HOLD_20',
            'TRAIN_SEARCH_BATCH_V1_WEEKLY_MID_SCALE_LOW_RISK_HOLD_20',
            'TRAIN_SEARCH_BATCH_V1_WEEKLY_OPENING_SELL_FREQUENCY_HOLD_20',
            'TRAIN_SEARCH_BATCH_V1_WEEKLY_OPENING_SELL_MAGNITUDE_HOLD_20',
            'TRAIN_SEARCH_BATCH_V1_AFFORDABLE_LOW_TURNOVER_WEEKLY_MOMENTUM_HOLD_20',
            'TRAIN_SEARCH_BATCH_V1_AFFORDABLE_WEEKLY_LOW_VOL_MARKET_HOLD_20',
            'TRAIN_SEARCH_BATCH_V1_STABILITY_20_HOLD_20',
            'TRAIN_SEARCH_BATCH_V1_LIQUIDITY_20_HOLD_20',
            'TRAIN_SEARCH_BATCH_V1_MOMENTUM_60_HOLD_20_MARKET_5',
            'TRAIN_SEARCH_BATCH_V1_STABILITY_20_HOLD_20_MARKET_5',
            'TRAIN_SEARCH_BATCH_V1_MACD_CROSS_HOLD_20',
            'TRAIN_SEARCH_BATCH_V1_KDJ_OVERSOLD_CROSS_HOLD_20',
            'TRAIN_SEARCH_BATCH_V1_CHAN_BOTTOM_MACD_HOLD_20',
            'TRAIN_SEARCH_BATCH_V1_MONTHLY_REVERSAL_HOLD_20',
            'TRAIN_SEARCH_BATCH_V1_HIGH_252_HOLD_20',
            'TRAIN_SEARCH_BATCH_V1_LOW_MAX_20_HOLD_20',
            'TRAIN_SEARCH_BATCH_V1_BOLL_REENTRY_HOLD_20',
            'TRAIN_SEARCH_BATCH_V1_DONCHIAN_55_BREAKOUT_HOLD_20',
            'TRAIN_SEARCH_BATCH_V1_RSI_14_RECLAIM_HOLD_20',
            'TRAIN_SEARCH_BATCH_V1_AROON_25_CROSS_HOLD_20',
            'TRAIN_SEARCH_BATCH_V1_CCI_20_TREND_HOLD_20',
            'TRAIN_SEARCH_BATCH_V1_KAMA_10_CROSS_HOLD_20',
            'TRAIN_SEARCH_BATCH_V1_RSI2_TREND_200_HOLD_20',
            'TRAIN_SEARCH_BATCH_V1_BULL_ENGULFING_DOWN_5_HOLD_20',
            'TRAIN_SEARCH_BATCH_V1_SMA_50_200_CROSS_HOLD_20',
            'TRAIN_SEARCH_BATCH_V1_TRIX_15_ZERO_CROSS_HOLD_20',
            'TRAIN_SEARCH_BATCH_V1_HAMMER_DOWN_5_HOLD_20',
            'TRAIN_SEARCH_BATCH_V1_THREE_SOLDIERS_HOLD_20',
            'TRAIN_SEARCH_BATCH_V1_ATR_14_UP_BREAKOUT_HOLD_20',
            'TRAIN_SEARCH_BATCH_V1_INSIDE_BAR_UP_HOLD_20',
            'TRAIN_SEARCH_BATCH_V1_SMA20_PULLBACK_200_HOLD_20',
            'TRAIN_SEARCH_BATCH_V1_ROC20_ACCEL_HOLD_20',
            'TRAIN_SEARCH_BATCH_V1_ADX_EMA_PULLBACK_HOLD_20',
            'TRAIN_SEARCH_BATCH_V1_SQUEEZE_TREND_TURNOVER_HOLD_20',
            *{'TRAIN_SEARCH_BATCH_V1_'+name for name in STRUCTURED_NAMES},
            *{'TRAIN_SEARCH_BATCH_V1_'+name for name in DEFENSIVE_NAMES},
            *{'TRAIN_SEARCH_BATCH_V1_'+name for name in FIXED_DEFENSIVE_NAMES},
            *{'TRAIN_SEARCH_BATCH_V1_'+name for name in PRICE_VOLUME_NAMES},
            *{'TRAIN_SEARCH_BATCH_V1_'+name for name in RECOVERY_NAMES},
            *{'TRAIN_SEARCH_BATCH_V1_'+name for name in FLOW_NAMES},
            *{'TRAIN_SEARCH_BATCH_V1_'+name for name in (*RESIDUAL_NAMES,*RESIDUAL_RISK,*RESIDUAL_EXITS)},
        }
        supported = {
            'operator': 'LT', 'threshold': 0, 'ranking': 'FACTOR_ASC_SYMBOL_ASC',
            'top_n': 3, 'entry': 'NEXT_SESSION_OPEN', 'holding_sessions': 20 if longer_hold else 3,
            'exit': 'NEXT_SESSION_OPEN',
        }
        if any(contract.get(key) != value for key, value in supported.items()):
            raise ValueError('FIXED_SIGNAL_CONTRACT_UNSUPPORTED')
        self.contract = deepcopy(dict(contract))
        from .monthly_window_v1 import signal_window
        self.signal_window = signal_window(self.contract)
        self.strategy_id = str(contract.get('signal_version', 'FIXED_REFERENCE'))
        self.exit_evaluator = PortfolioExitEvaluatorV1(
            str(contract.get('signal_version', 'RETURN_5D_DEGRADED')), self.strategy_id,
            deepcopy(expected_exit) if self.structure_exit else {'exit_type': 'FIXED_HOLD', 'fixed_holding_sessions': contract['holding_sessions']},
        )

    @staticmethod
    def clock(timestamp: Any, calendar: TradingCalendar, hour: int, minute: int) -> pd.Timestamp:
        ts = pd.Timestamp(timestamp)
        if pd.isna(ts) or ts.tzinfo is None:
            raise ValueError('FIXED_SIGNAL_TIME_AWARE_REQUIRED')
        ts = ts.tz_convert('Asia/Shanghai')
        day = int(ts.strftime('%Y%m%d'))
        if not calendar.contains(day):
            raise ValueError('FIXED_SIGNAL_SESSION_REQUIRED')
        if ts != ts.normalize() + pd.Timedelta(hours=hour, minutes=minute):
            raise ValueError('FIXED_SIGNAL_PHASE_TIME_MISMATCH')
        return ts

    def entries(
        self, rows: pd.DataFrame | None, calendar: TradingCalendar, ledger: PortfolioLedger,
        hazards: Mapping[str, list[int]], unsupported_lots: Mapping[str, Any], timestamp: Any,
    ) -> FixedAccountEntryDecision:
        """开盘已处理原有订单后的选股；数据由调用方完成资格/来源校验。"""
        ts = self.clock(timestamp, calendar, 9, 30)
        day = int(ts.strftime('%Y%m%d'))
        prior = calendar.prev_day(day)
        if unsupported_lots:
            return FixedAccountEntryDecision('NOT_READY', 'ACCOUNTING_UNSUPPORTED_CORPORATE_ACTION', None, [], [])
        if prior is None or not self.signal_window[0] <= prior <= self.signal_window[1]:
            return FixedAccountEntryDecision('NO_SIGNAL', 'OUTSIDE_TRAIN_SIGNAL_WINDOW', None, [], [])
        if rows is None:
            return FixedAccountEntryDecision('NOT_READY', 'FACTOR_SLICE_MISSING', None, [], [])
        required = {'symbol', 'timestamp', 'value', 'effective_available_at'}
        if not required <= set(rows.columns):
            raise ValueError('FIXED_SIGNAL_FIELDS_MISSING')
        if (rows.symbol.isna().any() or rows.symbol.duplicated().any()
                or not rows.timestamp.eq(prior).all()):
            raise ValueError('FIXED_SIGNAL_ROW_IDENTITY_INVALID')
        if not all(isinstance(symbol, str) and symbol for symbol in rows.symbol):
            raise ValueError('FIXED_SIGNAL_SYMBOL_INVALID')
        values = pd.to_numeric(rows.value, errors='raise')
        if not np.isfinite(values).all():
            raise ValueError('FIXED_SIGNAL_VALUE_INVALID')
        visible = []
        for value in rows.effective_available_at:
            parsed = pd.Timestamp(value)
            if pd.isna(parsed) or parsed.tzinfo is None:
                raise ValueError('FIXED_SIGNAL_AVAILABLE_AT_INVALID')
            visible.append(parsed)
        prepared = rows.assign(value=values, effective_available_at=visible)
        selected = []
        for row in prepared.sort_values(['value', 'symbol']).itertuples():
            if ledger.total_quantity(row.symbol):
                continue
            if row.value < self.contract['threshold'] and row.effective_available_at <= ts:
                selected.append(row)
            if len(selected) == self.contract['top_n']:
                break
        ranking = {'source_session': prior, 'decision_time': str(ts),
            'top3': [{'symbol': r.symbol, 'factor': r.value, 'rank': i + 1} for i, r in enumerate(selected)]}
        checks, signals = [], []
        for rank, row in enumerate(selected, 1):
            exit_day = calendar.next_day(day, self.contract['holding_sessions'] + 1)
            hits = hazard_overlap(hazards.get(row.symbol, ()), prior, exit_day) if exit_day else []
            reason = ('END_OF_TRAIN_NO_COMPLETE_CLOSURE_PATH' if exit_day is None else
                'ENTRY_REJECT_CORPORATE_ACTION_UNSUPPORTED' if hits else 'OK')
            checks.append({'symbol': row.symbol, 'source_session': prior, 'decision_time': str(ts),
                'earliest_exit_session': exit_day, 'rank': rank, 'hazard_dates': hits, 'reason': reason})
            if reason == 'OK':
                signals.append(Signal(self.strategy_id, f'DEGRADED:{prior}:{row.symbol}', row.symbol, ts,
                    Side.BUY, score=-row.value, execution_policy=ExecutionPolicy.NEXT_SESSION_OPEN,
                    metadata={'source_session': prior, 'rank': rank, 'availability': 'MODELED_NEXT_SESSION_OPEN'}))
        return FixedAccountEntryDecision('SIGNALS' if signals else 'NO_SIGNAL',
            'QUALIFIED' if signals else 'NO_ENTRY_AFTER_FIXED_RULES', ranking, checks, signals)

    def exits(
        self, calendar: TradingCalendar, ledger: PortfolioLedger,
        unsupported_lots: Mapping[str, Any], timestamp: Any,
        factor_rows: pd.DataFrame | None = None,
    ) -> list[PortfolioExitDecision]:
        """复用原退出器；会更新传入lot，预览调用方必须传入账户副本。"""
        ts = self.clock(timestamp, calendar, 15, 30)
        day = int(ts.strftime('%Y%m%d'))
        lots = [lot for key, lot in ledger.lots.items() if key not in unsupported_lots]
        state={}
        if self.structure_exit and factor_rows is not None and not factor_rows.empty:
            required={'symbol','timestamp','exit_invalidated','exit_available_at','signal_version'}
            if not required<=set(factor_rows) or factor_rows.symbol.duplicated().any():
                raise ValueError('STRUCTURED_EXIT_FIELDS_OR_IDENTITY_INVALID')
            if (not factor_rows.timestamp.eq(calendar.prev_day(day)).all() or
                    not factor_rows.signal_version.eq(self.strategy_id).all()):
                raise ValueError('STRUCTURED_EXIT_PRIOR_SESSION_VERSION_REQUIRED')
            for row in factor_rows.itertuples():
                if pd.isna(row.exit_invalidated) or pd.isna(row.exit_available_at):continue
                if row.exit_invalidated not in (0.,1.):raise ValueError('STRUCTURED_EXIT_VALUE_INVALID')
                known=pd.Timestamp(row.exit_available_at)
                if known.tzinfo is None:raise ValueError('STRUCTURED_EXIT_AWARE_TIME_REQUIRED')
                if known<=ts:
                    state[row.symbol]={'values':{self.exit_factor:float(row.exit_invalidated)},'available_at':known}
        return self.exit_evaluator.evaluate(lots, day, calendar.date_index(day), state, ts)
