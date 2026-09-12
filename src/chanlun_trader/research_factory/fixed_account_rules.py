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
        # 新研究变体显式版本化；旧冻结合同仍只接受3 session。
        longer_hold = contract.get('signal_version') in {
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
        }
        supported = {
            'operator': 'LT', 'threshold': 0, 'ranking': 'FACTOR_ASC_SYMBOL_ASC',
            'top_n': 3, 'entry': 'NEXT_SESSION_OPEN', 'holding_sessions': 20 if longer_hold else 3,
            'exit': 'NEXT_SESSION_OPEN',
        }
        if any(contract.get(key) != value for key, value in supported.items()):
            raise ValueError('FIXED_SIGNAL_CONTRACT_UNSUPPORTED')
        self.contract = deepcopy(dict(contract))
        self.strategy_id = str(contract.get('signal_version', 'FIXED_REFERENCE'))
        self.exit_evaluator = PortfolioExitEvaluatorV1(
            str(contract.get('signal_version', 'RETURN_5D_DEGRADED')), self.strategy_id,
            {'exit_type': 'FIXED_HOLD', 'fixed_holding_sessions': contract['holding_sessions']},
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
        if prior is None or not 20220801 <= prior <= 20240731:
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
    ) -> list[PortfolioExitDecision]:
        """复用原退出器；会更新传入lot，预览调用方必须传入账户副本。"""
        ts = self.clock(timestamp, calendar, 15, 30)
        day = int(ts.strftime('%Y%m%d'))
        lots = [lot for key, lot in ledger.lots.items() if key not in unsupported_lots]
        return self.exit_evaluator.evaluate(lots, day, calendar.date_index(day), {}, ts)
