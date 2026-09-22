"""BT_BEHAVIOR_DAILY_V1 公共服务 — 指标/条件/退出 的显式版本化执行入口。

公共 API（``POST /api/backtest/behavior``）与 CLI（``scripts/run_behavior_backtest_v1.py``）
都调用本模块的 :func:`run_behavior_backtest_v1`，因此二者对同一请求必须给出
相同的语义事件与账户结果。

边界：
- 只读合成输入（内存 / CSV / Parquet），不访问真实行情、封存期或真实研究目录；
- 不写真实研究账本，不创建 Trial / 预算 / 授权；
- 不静默回落到旧 ``BacktestRunner``；请求了不支持的模式即明确报错。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

import pandas as pd

from .asof import MarketDataStore
from .conditions_v1 import (
    CONDITION_CONTRACT_VERSION,
    KDJ_CONDITIONS,
    MACD_CONDITIONS,
    ConditionError,
    KdjParams,
    MacdParams,
    evaluate_kdj_conditions,
    evaluate_macd_conditions,
)
from .daily_exit_v1 import (
    DAILY_EXIT_CONTRACT_VERSION,
    DAILY_EXIT_EXECUTION_MODE,
    DailyExitEvaluatorV1,
    DailyExitRuleSetV1,
    ExitConfigError,
    daily_exit_fn,
)
from .engine import BacktestEngineV2, EngineConfig
from .indicators_v1 import (
    INDICATOR_CONTRACT_VERSION,
    KDJ_IMPLEMENTATION_VERSION,
    MACD_IMPLEMENTATION_VERSION,
    IndicatorInputError,
)
from .signal import ExecutionPolicy, Side, Signal
from .time_types import tz_aware

BEHAVIOR_MODE = "BT_BEHAVIOR_DAILY_V1"
SUPPORTED_MODES = (BEHAVIOR_MODE,)
# 明确保留但不属于本轮验收范围：请求即拒绝，不得静默降级。
RESERVED_MODES = (
    "BT_BEHAVIOR_INTRADAY_V1",
    "BT_BEHAVIOR_TICK_V1",
    "BT_BEHAVIOR_MULTI_TIMEFRAME_V1",
)

PRICE_MODE = "RAW_EXECUTION_QFQ_FEATURE_DECLARED"


class BehaviorRequestError(ValueError):
    """请求非法或请求了未验收能力。必须显式失败。"""


def _timestamp(day: int, hour: int, minute: int) -> pd.Timestamp:
    return tz_aware(day // 10000, (day // 100) % 100, day % 100, hour, minute)


@dataclass
class BehaviorRequestV1:
    """显式版本化请求。所有字段进入最终配置并回显。"""

    mode: str = BEHAVIOR_MODE
    calendar: Sequence[int] = field(default_factory=list)
    symbols: Sequence[str] = field(default_factory=list)
    bars: Mapping[str, Sequence[Mapping[str, Any]]] = field(default_factory=dict)
    dataset_path: Optional[str] = None
    entry_conditions: Sequence[str] = field(default_factory=lambda: ["ABOVE_ZERO_GOLDEN_CROSS"])
    exit_rules: Mapping[str, Any] = field(default_factory=dict)
    macd: Mapping[str, Any] = field(default_factory=lambda: {"fast": 12, "slow": 26, "signal": 9})
    kdj: Mapping[str, Any] = field(default_factory=lambda: {"n": 9, "k_period": 3, "d_period": 3})
    initial_cash: float = 100_000.0
    max_positions: int = 1
    max_position_weight: Optional[float] = None
    commission_rate: float = 0.00025
    min_commission: float = 5.0
    stamp_tax_rate: float = 0.0005
    slippage_bps: float = 0.001
    max_holding_days: int = 0
    strategy_id: str = "BT_BEHAVIOR_V1"
    persist_run_manifest: bool = False

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "BehaviorRequestV1":
        if not isinstance(payload, Mapping):
            raise BehaviorRequestError("REQUEST_NOT_AN_OBJECT")
        known = {f for f in cls.__dataclass_fields__}
        unknown = sorted(set(payload) - known)
        if unknown:
            raise BehaviorRequestError(f"UNKNOWN_REQUEST_FIELD:{','.join(unknown)}")
        return cls(**dict(payload))

    def validate(self) -> None:
        if self.mode in RESERVED_MODES:
            raise BehaviorRequestError(f"UNSUPPORTED_MODE:{self.mode}")
        if self.mode not in SUPPORTED_MODES:
            raise BehaviorRequestError(f"UNKNOWN_MODE:{self.mode}")
        if not self.calendar:
            raise BehaviorRequestError("EMPTY_CALENDAR")
        if list(self.calendar) != sorted(set(int(d) for d in self.calendar)):
            raise BehaviorRequestError("CALENDAR_NOT_STRICTLY_INCREASING")
        if not self.symbols:
            raise BehaviorRequestError("EMPTY_SYMBOL_SET")
        if self.dataset_path and self.bars:
            raise BehaviorRequestError("AMBIGUOUS_DATA_SOURCE")
        if not self.dataset_path and not self.bars:
            raise BehaviorRequestError("NO_DATA_SOURCE")
        for name in self.entry_conditions:
            if name not in MACD_CONDITIONS and name not in KDJ_CONDITIONS:
                raise BehaviorRequestError(f"UNSUPPORTED_CONDITION:{name}")
        if not self.entry_conditions:
            raise BehaviorRequestError("EMPTY_ENTRY_CONDITIONS")


@dataclass
class BehaviorResultV1:
    """显式版本化结果。语义事件与账户逐笔都在这里。"""

    mode: str
    engine_version: str
    indicator_contract: str
    indicator_versions: Dict[str, str]
    condition_contract: str
    exit_contract: str
    exit_execution_mode: str
    price_mode: str
    time_rules: Dict[str, str]
    resolved_config: Dict[str, Any]
    signals: List[dict]
    orders: List[dict]
    fills: List[dict]
    lots: List[dict]
    trades: List[dict]
    rejections: List[dict]
    cash: float
    initial_cash: float
    equity_curve: List[dict]
    final_equity: float
    exit_evaluations: List[dict]
    run_summary: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def semantics(self) -> Dict[str, Any]:
        """只保留业务语义字段，供 API/CLI 一致性比较（丢弃随机 ID）。"""
        def norm_fill(row: Mapping[str, Any]) -> dict:
            return {
                "symbol": row["symbol"], "side": row["side"], "quantity": row["quantity"],
                "price": round(float(row["price"]), 6), "time": row["fill_time"],
                "fee": round(float(row["fee"]), 6),
            }

        def norm_order(row: Mapping[str, Any]) -> dict:
            return {
                "symbol": row["symbol"], "side": row["side"], "quantity": row["quantity"],
                "status": row["status"], "created_at": row["created_at"],
                "reason_code": row["reason_code"],
            }

        def norm_trade(row: Mapping[str, Any]) -> dict:
            return {
                "symbol": row["symbol"], "side": row["side"], "quantity": row["quantity"],
                "price": round(float(row["price"]), 6), "time": row["fill_time"],
                "fee": round(float(row["fee"]), 6),
                "realized_pnl": round(float(row["realized_pnl"]), 6),
            }

        return {
            "mode": self.mode,
            "engine_version": self.engine_version,
            "indicator_contract": self.indicator_contract,
            "exit_contract": self.exit_contract,
            "resolved_config": self.resolved_config,
            "signals": [
                {"symbol": s["symbol"], "signal_id": s["signal_id"], "generated_at": s["generated_at"],
                 "direction": s["direction"], "signal_type": s["signal_type"]}
                for s in self.signals
            ],
            "orders": sorted((norm_order(o) for o in self.orders), key=lambda o: (o["created_at"], o["symbol"], o["side"])),
            "fills": sorted((norm_fill(f) for f in self.fills), key=lambda f: (f["time"], f["symbol"], f["side"])),
            "trades": sorted((norm_trade(t) for t in self.trades), key=lambda t: (t["time"], t["symbol"], t["side"])),
            "rejections": sorted(
                ({"symbol": r["symbol"], "reason": r["reason"], "at": r["at"]} for r in self.rejections),
                key=lambda r: (r["at"], r["symbol"], r["reason"]),
            ),
            "cash": round(float(self.cash), 6),
            "final_equity": round(float(self.final_equity), 6),
            "equity_curve": [
                {"date": row["date"], "cash": round(row["cash"], 6),
                 "market_value": round(row["market_value"], 6), "equity": round(row["equity"], 6)}
                for row in self.equity_curve
            ],
        }


# --------------------------------------------------------------------------
# 数据装载
# --------------------------------------------------------------------------
def _bars_from_mapping(bars: Mapping[str, Sequence[Mapping[str, Any]]]) -> Dict[str, pd.DataFrame]:
    out: Dict[str, pd.DataFrame] = {}
    for symbol, rows in bars.items():
        frame = pd.DataFrame([dict(row) for row in rows])
        missing = {"date", "open", "high", "low", "close", "volume"} - set(frame.columns)
        if missing:
            raise BehaviorRequestError(f"BAR_COLUMNS_MISSING:{symbol}:{','.join(sorted(missing))}")
        frame = frame.sort_values("date").reset_index(drop=True)
        if frame["date"].duplicated().any():
            raise BehaviorRequestError(f"BAR_DATE_DUPLICATED:{symbol}")
        if "amount" not in frame.columns:
            frame["amount"] = frame["close"] * frame["volume"]
        out[str(symbol)] = frame
    return out


def load_bars_from_file(path: str | Path) -> Dict[str, pd.DataFrame]:
    """从项目实际支持的文件入口装载合成行情（CSV / Parquet）。

    文件解析是真实的：调用方不得用内存 dict 绕过文件解析路径。
    """
    target = Path(path)
    if not target.exists():
        raise BehaviorRequestError(f"DATASET_NOT_FOUND:{target.name}")
    if target.suffix.lower() == ".csv":
        frame = pd.read_csv(target)
    elif target.suffix.lower() == ".parquet":
        frame = pd.read_parquet(target)
    else:
        raise BehaviorRequestError(f"UNSUPPORTED_DATASET_FORMAT:{target.suffix}")
    required = {"symbol", "date", "open", "high", "low", "close", "volume"}
    missing = required - set(frame.columns)
    if missing:
        raise BehaviorRequestError(f"DATASET_COLUMNS_MISSING:{','.join(sorted(missing))}")
    if "amount" not in frame.columns:
        frame["amount"] = frame["close"] * frame["volume"]
    out: Dict[str, pd.DataFrame] = {}
    for symbol, group in frame.groupby("symbol", sort=True):
        part = group.sort_values("date").reset_index(drop=True)
        if part["date"].duplicated().any():
            raise BehaviorRequestError(f"BAR_DATE_DUPLICATED:{symbol}")
        out[str(symbol)] = part
    return out


# --------------------------------------------------------------------------
# 条件 -> 信号
# --------------------------------------------------------------------------
def build_entry_signals(
    bars: Mapping[str, pd.DataFrame],
    calendar: Sequence[int],
    conditions: Sequence[str],
    *,
    macd_params: MacdParams,
    kdj_params: KdjParams,
    strategy_id: str,
) -> tuple[List[Signal], Dict[str, Dict[str, dict]]]:
    """在已完成日线上判定入场条件，生成次日开盘执行的 V2 Signal。

    返回 (signals, condition_trace)。condition_trace 记录每根 bar 每个条件的
    布尔结果，作为"哪些条件在何时成立"的可核对证据。
    """
    macd_names = [name for name in conditions if name in MACD_CONDITIONS]
    kdj_names = [name for name in conditions if name in KDJ_CONDITIONS]
    signals: List[Signal] = []
    trace: Dict[str, Dict[str, dict]] = {}
    calendar_set = set(int(d) for d in calendar)

    for symbol in sorted(bars):
        frame = bars[symbol].set_index("date")
        close = pd.to_numeric(frame["close"], errors="coerce")
        high = pd.to_numeric(frame["high"], errors="coerce")
        low = pd.to_numeric(frame["low"], errors="coerce")
        symbol_trace: Dict[str, dict] = {}
        matched = pd.Series(True, index=close.index)
        if macd_names:
            macd_matrix = evaluate_macd_conditions(close, macd_names, params=macd_params)
            for name in macd_names:
                matched &= macd_matrix[name]
            if kdj_names:
                # 混合条件要求两族同时 ready，避免"未 ready 的 False"被误当不成立。
                kdj_matrix = evaluate_kdj_conditions(high, low, close, kdj_names, params=kdj_params)
                for name in kdj_names:
                    matched &= kdj_matrix[name]
        else:
            kdj_matrix = evaluate_kdj_conditions(high, low, close, kdj_names, params=kdj_params)
            for name in kdj_names:
                matched &= kdj_matrix[name]
        for day in close.index:
            if int(day) not in calendar_set:
                continue
            symbol_trace[str(int(day))] = {
                "matched": bool(matched.loc[day]),
            }
        trace[symbol] = symbol_trace
        for day in close.index:
            if int(day) not in calendar_set or not bool(matched.loc[day]):
                continue
            signals.append(Signal(
                strategy_id=strategy_id,
                signal_id=f"{strategy_id}:{symbol}:{int(day)}",
                symbol=symbol,
                generated_at=_timestamp(int(day), 15, 0),
                direction=Side.BUY,
                signal_type="+".join(conditions),
                execution_policy=ExecutionPolicy.NEXT_SESSION_OPEN,
                score=0.0,
            ))
    signals.sort(key=lambda s: (s.generated_at, s.symbol))
    return signals, trace


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------
def run_behavior_backtest_v1(request: BehaviorRequestV1 | Mapping[str, Any]) -> BehaviorResultV1:
    """执行一次 BT_BEHAVIOR_DAILY_V1 回测。

    链路：指标 → 条件 → Signal → OrderIntent → Sizer/Risk → Broker → Fill
    → Ledger → 正式估值。全程使用真实 BacktestEngineV2 组件。
    """
    if isinstance(request, Mapping):
        request = BehaviorRequestV1.from_mapping(request)
    request.validate()

    bars = load_bars_from_file(request.dataset_path) if request.dataset_path else _bars_from_mapping(request.bars)
    missing = [s for s in request.symbols if s not in bars]
    if missing:
        raise BehaviorRequestError(f"SYMBOL_NOT_IN_DATASET:{','.join(sorted(missing))}")
    calendar = [int(d) for d in request.calendar]

    rules = DailyExitRuleSetV1(**dict(request.exit_rules))
    macd_params = MacdParams(**dict(request.macd))
    kdj_params = KdjParams(**dict(request.kdj))

    store = MarketDataStore(feature_price_mode="raw")
    for symbol in request.symbols:
        frame = bars[symbol]
        indexed = frame.set_index("date")[["open", "high", "low", "close", "volume", "amount"]]
        store.add_daily_raw(symbol, indexed.copy())
        # 合成 fixture 的 raw 与 feature 同源；本版本退出判定只用 RAW 口径。
        store.add_daily_qfq(symbol, indexed.copy())

    signals, condition_trace = build_entry_signals(
        {s: bars[s] for s in request.symbols}, calendar, request.entry_conditions,
        macd_params=macd_params, kdj_params=kdj_params, strategy_id=request.strategy_id,
    )

    weight = request.max_position_weight
    if weight is None:
        weight = 1.0 / max(1, int(request.max_positions))
    config = EngineConfig(
        initial_cash=float(request.initial_cash),
        max_positions=int(request.max_positions),
        max_position_weight=float(weight),
        commission_rate=float(request.commission_rate),
        min_commission=float(request.min_commission),
        stamp_tax_rate=float(request.stamp_tax_rate),
        slippage_bps=float(request.slippage_bps),
        mode="DAILY",
        max_holding_days=int(request.max_holding_days),
        enable_index_filter=False,
        index_filter_enabled=False,
        persist_run_manifest=bool(request.persist_run_manifest),
        execution_model_version="BT_BEHAVIOR_DAILY_V1",
    )
    engine = BacktestEngineV2(
        store, calendar, config=config,
        source_identity=("BT_BEHAVIOR_SYNTHETIC_V1", True),
    )
    engine.add_signals(signals)

    evaluator = DailyExitEvaluatorV1(request.strategy_id, request.strategy_id, rules)
    exit_callback = daily_exit_fn(evaluator, store, calendar) if rules.enabled else None
    result = engine.run(exit_fn=exit_callback)

    ledger = result.ledger
    orders = []
    for order in sorted(result.orders.orders.values(), key=lambda o: (o.created_at, o.order_id)):
        orders.append({
            "order_id": order.order_id,
            "symbol": order.symbol,
            "side": order.side.value,
            "quantity": int(order.quantity),
            "filled_quantity": int(order.filled_quantity),
            "status": order.status.value,
            "created_at": str(order.created_at),
            "eligible_at": str(order.eligible_at) if order.eligible_at is not None else None,
            "reason_code": str(order.reason_code),
            "lot_id": order.lot_id,
        })
    rejections = [
        {"symbol": o["symbol"], "reason": o["reason_code"], "at": o["created_at"], "order_id": o["order_id"]}
        for o in orders if o["status"] in {"REJECTED", "EXPIRED"}
    ]
    fills = []
    for trade in ledger.trades:
        fills.append({
            "fill_time": str(trade.fill_time),
            "symbol": trade.symbol,
            "side": trade.side.value,
            "quantity": int(trade.quantity),
            "price": float(trade.price),
            "fee": float(trade.fee),
            "gross_value": float(trade.gross_value),
            "lot_id": trade.lot_id,
            "order_id": trade.order_id,
            "realized_pnl": float(trade.realized_pnl),
            "reality_flag": trade.reality_flag,
        })
    lots = []
    for lot in sorted(ledger.lots.values(), key=lambda item: item.lot_id):
        lots.append({
            "lot_id": lot.lot_id,
            "symbol": lot.symbol,
            "quantity": int(lot.quantity),
            "remaining_quantity": int(lot.remaining_quantity),
            "entry_price": float(lot.entry_price),
            "cost": float(lot.cost),
            "buy_time": str(lot.buy_time),
            "sellable_from": str(lot.sellable_from),
            "entry_session": lot.entry_session,
            "entry_session_index": lot.entry_session_index,
            "exit_state": lot.exit_state,
            "exit_reason": lot.exit_reason,
        })
    equity_curve = [
        {"date": int(snapshot.timestamp.strftime("%Y%m%d")),
         "cash": float(snapshot.cash),
         "market_value": float(snapshot.market_value),
         "equity": float(snapshot.equity)}
        for snapshot in ledger.snapshots
    ]

    return BehaviorResultV1(
        mode=BEHAVIOR_MODE,
        engine_version=result.context.engine_version,
        indicator_contract=INDICATOR_CONTRACT_VERSION,
        indicator_versions={
            "macd": MACD_IMPLEMENTATION_VERSION,
            "kdj": KDJ_IMPLEMENTATION_VERSION,
        },
        condition_contract=CONDITION_CONTRACT_VERSION,
        exit_contract=DAILY_EXIT_CONTRACT_VERSION,
        exit_execution_mode=DAILY_EXIT_EXECUTION_MODE,
        price_mode=PRICE_MODE,
        time_rules={
            "signal_time": "COMPLETED_DAILY_CLOSE_15:00_ASIA_SHANGHAI",
            "earliest_execution": "NEXT_SESSION_OPEN_09:30",
            "timezone": "Asia/Shanghai",
            "intraday_touch": "NOT_SUPPORTED",
            "multi_timeframe": "NOT_SUPPORTED",
        },
        resolved_config={
            "request": {
                "mode": request.mode,
                "symbols": list(request.symbols),
                "entry_conditions": list(request.entry_conditions),
                "macd": macd_params.to_dict(),
                "kdj": kdj_params.to_dict(),
                "exit_rules": rules.to_dict(),
                "initial_cash": float(request.initial_cash),
                "max_positions": int(request.max_positions),
                "max_position_weight": float(weight),
                "commission_rate": float(request.commission_rate),
                "min_commission": float(request.min_commission),
                "stamp_tax_rate": float(request.stamp_tax_rate),
                "slippage_bps": float(request.slippage_bps),
                "max_holding_days": int(request.max_holding_days),
            },
            "engine_config_hash": result.context.config_hash,
            "condition_trace_rows": {sym: len(rows) for sym, rows in condition_trace.items()},
            "conditions_matched": {
                sym: sum(1 for row in rows.values() if row["matched"]) for sym, rows in condition_trace.items()
            },
        },
        signals=[{
            "signal_id": s.signal_id, "symbol": s.symbol, "generated_at": str(s.generated_at),
            "direction": s.direction.value, "signal_type": s.signal_type,
            "execution_policy": s.execution_policy.value,
        } for s in signals],
        orders=orders,
        fills=fills,
        lots=lots,
        trades=[{
            "trade_id": t.trade_id, "symbol": t.symbol, "side": t.side.value,
            "quantity": int(t.quantity), "price": float(t.price), "fee": float(t.fee),
            "fill_time": str(t.fill_time), "lot_id": t.lot_id,
            "realized_pnl": float(t.realized_pnl), "reality_flag": t.reality_flag,
        } for t in ledger.trades],
        rejections=rejections,
        cash=float(ledger.cash),
        initial_cash=float(ledger.initial_cash),
        equity_curve=equity_curve,
        final_equity=float(equity_curve[-1]["equity"]) if equity_curve else float(ledger.initial_cash),
        exit_evaluations=list(evaluator.evaluations),
        run_summary=result.summary(),
    )


def write_result(path: str | Path, result: BehaviorResultV1) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return target


__all__ = [
    "BEHAVIOR_MODE",
    "SUPPORTED_MODES",
    "RESERVED_MODES",
    "BehaviorRequestError",
    "ConditionError",
    "ExitConfigError",
    "IndicatorInputError",
    "BehaviorRequestV1",
    "BehaviorResultV1",
    "run_behavior_backtest_v1",
    "load_bars_from_file",
    "build_entry_signals",
    "write_result",
]
