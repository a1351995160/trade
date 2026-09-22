"""V2 退出规则与完整账户链验收。

覆盖：V1 四类复用（兼容性）、V2 ATR 距离/跟踪、指标条件退出、反向信号退出、
明确版本结构价退出；以及 T+1、停牌、跌停、跳空、容量、期末未成交、正式估值。

不 mock 引擎、撮合、账本、退出求值器或被测指标。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from chanlun_trader.engine.asof import MarketDataStore
from chanlun_trader.engine.behavior_service_v2 import (
    BEHAVIOR_MODE_V2,
    BehaviorRequestError,
    BehaviorRequestV2,
    parse_expression,
    run_behavior_backtest_v2,
)
from chanlun_trader.engine.conditions_v2 import (
    ConditionContext,
    ConditionEvaluator,
    ind,
    lit,
    op,
)
from chanlun_trader.engine.daily_exit_v1 import ExitConfigError
from chanlun_trader.engine.daily_exit_v2 import (
    AtrDistanceSpec,
    DailyExitEvaluatorV2,
    DailyExitRuleSetV2,
    SUPPORTED_EXIT_TYPES_V2,
    UNSUPPORTED_EXIT_TYPES_V2,
    V2_ADDITIONAL_EXIT_TYPES,
)
from chanlun_trader.engine.indicator_registry_v2 import default_registry
from chanlun_trader.engine.position import PositionLot
from chanlun_trader.engine.time_types import tz_aware

SYMBOL = "600000.SH"
CAL = [20250102, 20250103, 20250106, 20250107, 20250108, 20250109, 20250110, 20250113]


def _store(closes: dict, *, volumes: dict = None, highs: dict = None, lows: dict = None):
    rows = []
    previous = None
    for day in CAL:
        close = float(closes.get(day, 10.0))
        row = {
            "date": day, "open": close, "close": close,
            "high": float((highs or {}).get(day, close * 1.01)),
            "low": float((lows or {}).get(day, close * 0.99)),
            "volume": float((volumes or {}).get(day, 1_000_000.0)),
            "amount": close * float((volumes or {}).get(day, 1_000_000.0)),
            "prev_close": float(previous if previous is not None else close),
        }
        rows.append(row)
        previous = close
    store = MarketDataStore(feature_price_mode="raw")
    store.add_daily_raw(SYMBOL, pd.DataFrame(rows).set_index("date"))
    return store


def _lot(lot_id: str = "lot-1", *, entry_price: float = 10.0) -> PositionLot:
    return PositionLot(
        lot_id=lot_id, position_id=f"pos-{lot_id}", symbol=SYMBOL, strategy_id="S",
        buy_time=tz_aware(2025, 1, 3, 9, 30), quantity=1000, remaining_quantity=1000,
        cost=entry_price * 1000, sellable_from=tz_aware(2025, 1, 4, 9, 30),
        entry_session=CAL[1], entry_session_index=1, entry_price=entry_price,
    )


_INDEX_OF = {day: index for index, day in enumerate(CAL)}


# --------------------------------------------------------------------------
# 1. 契约：支持与不支持的类型
# --------------------------------------------------------------------------
def test_supported_exit_types_include_v1_and_v2():
    assert set(SUPPORTED_EXIT_TYPES_V2) >= {
        "FIXED_COST_STOP", "FIXED_TAKE_PROFIT", "TRAILING_CLOSE_STOP", "FIXED_HOLD",
    }
    assert set(SUPPORTED_EXIT_TYPES_V2) >= set(V2_ADDITIONAL_EXIT_TYPES)


@pytest.mark.parametrize("exit_type", list(UNSUPPORTED_EXIT_TYPES_V2))
def test_intraday_and_tick_exits_still_rejected(exit_type):
    with pytest.raises(ExitConfigError):
        DailyExitRuleSetV2(exit_types=(exit_type,))
    with pytest.raises(ExitConfigError):
        DailyExitRuleSetV2(requested_unsupported=(exit_type,))


def test_structure_stop_requires_declared_usable_scale():
    """RAW 与 QFQ 不能直接比较；未声明可用尺度即拒绝，不伪造完整账户结果。"""
    with pytest.raises(ExitConfigError) as excinfo:
        DailyExitRuleSetV2(structure_stop_price=9.0)
    assert "STRUCTURE_STOP_SCALE_NOT_USABLE" in str(excinfo.value)
    with pytest.raises(ExitConfigError) as excinfo:
        DailyExitRuleSetV2(structure_stop_price=9.0,
                           structure_stop_scale="QFQ_WITH_CONVERSION_EVIDENCE")
    assert "STRUCTURE_STOP_CONVERSION_NOT_AVAILABLE" in str(excinfo.value)
    # RAW 尺度可用
    rules = DailyExitRuleSetV2(structure_stop_price=9.0, structure_stop_scale="RAW")
    assert rules.structure_stop_price == 9.0


def test_atr_spec_validation():
    with pytest.raises(ExitConfigError):
        AtrDistanceSpec(multiple=0.0)
    with pytest.raises(ExitConfigError):
        AtrDistanceSpec(multiple=2.0, atr_window=0)
    with pytest.raises(ExitConfigError):
        AtrDistanceSpec(multiple=2.0, anchor="SOMETHING")


def test_v1_rules_delegate_preserves_semantics():
    """V2 规则集中的 V1 部分必须原样委托，不改变公式与触发时点。"""
    rules = DailyExitRuleSetV2(stop_loss_pct=0.03, take_profit_pct=0.10,
                               trailing_activate_pct=0.05, trailing_pct=0.08,
                               fixed_holding_sessions=20)
    v1 = rules.v1_rules
    assert v1.stop_loss_pct == 0.03
    assert v1.take_profit_pct == 0.10
    assert v1.trailing_activate_pct == 0.05
    assert v1.trailing_pct == 0.08
    assert v1.fixed_holding_sessions == 20
    contract = rules.to_dict()
    assert contract["v1_contract_version"] == "BT_DAILY_EXIT_V1"
    assert contract["contract_version"] == "BT_DAILY_EXIT_V2"
    assert contract["cost_anchor"] == "ACTUAL_LOT_ENTRY_FILL_PRICE_EXCLUDING_COMMISSION"
    assert contract["earliest_execution"] == "NEXT_SESSION_OPEN"


# --------------------------------------------------------------------------
# 2. V1 兼容性：四类基础退出行为不变
# --------------------------------------------------------------------------
def test_v1_cost_stop_still_uses_actual_fill_price():
    store = _store({CAL[2]: 9.2})
    evaluator = DailyExitEvaluatorV2("C", "C", DailyExitRuleSetV2(stop_loss_pct=0.03))
    lot = _lot(entry_price=10.0)
    decisions = evaluator.evaluate([lot], CAL[2], 2, store, session_index_of=_INDEX_OF)
    assert len(decisions) == 1
    assert decisions[0].reason_code == "EXIT_FIXED_COST_STOP"
    record = [e for e in evaluator.evaluations if e.get("state") == "EXIT_DUE"][0]
    assert record["anchor"] == pytest.approx(10.0)
    assert record["close"] == pytest.approx(9.2)
    assert record["close"] <= 10.0 * 0.97


def test_v1_take_profit_and_fixed_hold_still_work():
    store = _store({CAL[2]: 11.5})
    tp = DailyExitEvaluatorV2("C", "C", DailyExitRuleSetV2(take_profit_pct=0.10))
    assert tp.evaluate([_lot()], CAL[2], 2, store, session_index_of=_INDEX_OF)[0].reason_code == \
        "EXIT_FIXED_TAKE_PROFIT"
    hold = DailyExitEvaluatorV2("C", "C", DailyExitRuleSetV2(fixed_holding_sessions=2))
    store_flat = _store({})
    assert hold.evaluate([_lot()], CAL[3], 3, store_flat, session_index_of=_INDEX_OF)[0].reason_code == \
        "EXIT_FIXED_HOLD"


def test_v1_trailing_stop_still_only_tightens():
    store = _store({CAL[2]: 11.0, CAL[3]: 12.0, CAL[4]: 12.5})
    evaluator = DailyExitEvaluatorV2(
        "C", "C", DailyExitRuleSetV2(trailing_activate_pct=0.05, trailing_pct=0.08))
    lot = _lot()
    for index, day in enumerate(CAL[2:5], start=2):
        evaluator.evaluate([lot], day, index, store, session_index_of=_INDEX_OF)
    lines = [e["trailing_line"] for e in evaluator.evaluations if e.get("trailing_activated")]
    assert lines == sorted(lines), "V1 移动线出现放松"


# --------------------------------------------------------------------------
# 3. V2 ATR 距离止损与 ATR 跟踪
# --------------------------------------------------------------------------
def test_atr_distance_stop_uses_entry_atr_in_execution_scale():
    """ATR 线 = 成交价 - multiple * 入场前已可用 ATR。"""
    store = _store({CAL[2]: 9.2})
    rules = DailyExitRuleSetV2(atr_distance=AtrDistanceSpec(multiple=1.5, atr_window=14))
    evaluator = DailyExitEvaluatorV2("C", "C", rules)
    evaluator.register_entry_atr("lot-1", 0.5)
    lot = _lot(entry_price=10.0)
    decisions = evaluator.evaluate([lot], CAL[2], 2, store, session_index_of=_INDEX_OF)
    assert decisions[0].reason_code == "EXIT_ATR_DISTANCE_STOP"
    record = [e for e in evaluator.evaluations if e.get("state") == "EXIT_DUE"][0]
    assert record["atr_distance_line"] == pytest.approx(10.0 - 1.5 * 0.5)
    assert record["entry_atr"] == pytest.approx(0.5)


def test_atr_distance_does_not_trigger_when_close_above_line():
    store = _store({CAL[2]: 9.5})     # 线 = 9.25，收盘 9.5 高于线
    rules = DailyExitRuleSetV2(atr_distance=AtrDistanceSpec(multiple=1.5))
    evaluator = DailyExitEvaluatorV2("C", "C", rules)
    evaluator.register_entry_atr("lot-1", 0.5)
    decisions = evaluator.evaluate([_lot()], CAL[2], 2, store, session_index_of=_INDEX_OF)
    assert decisions == []
    assert [e for e in evaluator.evaluations if e.get("state") == "OPEN"]


def test_atr_stop_requires_entry_atr_and_refuses_missing():
    """缺少入场 ATR 锚且无可用 ATR 序列时，不得用替代值补齐。"""
    store = _store({CAL[2]: 8.0})
    rules = DailyExitRuleSetV2(atr_distance=AtrDistanceSpec(multiple=1.5))
    evaluator = DailyExitEvaluatorV2("C", "C", rules)
    decisions = evaluator.evaluate([_lot()], CAL[2], 2, store, session_index_of=_INDEX_OF)
    assert decisions == [], "缺少 ATR 锚却触发了 ATR 退出"
    record = [e for e in evaluator.evaluations if e.get("state") == "OPEN"][0]
    assert "atr_distance_line" not in record


def test_atr_trailing_tightens_only_and_keeps_peak():
    store = _store({CAL[2]: 11.0, CAL[3]: 12.0, CAL[4]: 11.5})
    rules = DailyExitRuleSetV2(atr_trailing=AtrDistanceSpec(multiple=2.0, anchor="DYNAMIC_CURRENT"))
    evaluator = DailyExitEvaluatorV2("C", "C", rules)
    evaluator.register_entry_atr("lot-1", 0.5)
    atr_series = {SYMBOL: pd.Series({day: 0.5 for day in CAL})}
    lot = _lot()
    for index, day in enumerate(CAL[2:5], start=2):
        evaluator.evaluate([lot], day, index, store, session_index_of=_INDEX_OF,
                           atr_series=atr_series)
    lines = [e["atr_trailing_line"] for e in evaluator.evaluations if "atr_trailing_line" in e]
    assert lines == sorted(lines), "ATR 跟踪线出现放松"
    # 峰值 12.0 - 2*0.5 = 11.0；收盘 11.5 > 11.0 不触发
    assert lines[-1] == pytest.approx(11.0)


def test_atr_trailing_triggers_on_close_below_line():
    store = _store({CAL[2]: 11.0, CAL[3]: 12.0, CAL[4]: 10.5})
    rules = DailyExitRuleSetV2(atr_trailing=AtrDistanceSpec(multiple=2.0, anchor="DYNAMIC_CURRENT"))
    evaluator = DailyExitEvaluatorV2("C", "C", rules)
    evaluator.register_entry_atr("lot-1", 0.5)
    atr_series = {SYMBOL: pd.Series({day: 0.5 for day in CAL})}
    lot = _lot()
    # 先按 session 顺序推进以建立峰值（11.0 -> 12.0）
    for index, day in enumerate(CAL[2:4], start=2):
        evaluator.evaluate([lot], day, index, store, session_index_of=_INDEX_OF,
                           atr_series=atr_series)
    # 峰值 12.0 - 2*0.5 = 11.0；CAL[4] 收 10.5 <= 11.0 -> 触发
    decisions = evaluator.evaluate([lot], CAL[4], 4, store, session_index_of=_INDEX_OF,
                                   atr_series=atr_series)
    assert decisions and decisions[0].reason_code == "EXIT_ATR_TRAILING_STOP"


# --------------------------------------------------------------------------
# 4. 指标条件退出 / 反向信号退出（三值：只有 TRUE 触发）
# --------------------------------------------------------------------------
def _condition_setup(values: dict):
    days = CAL
    index = pd.Index(days, name="date")
    return ConditionContext(indicator_values={k: pd.Series(v, index=index) for k, v in values.items()},
                            fields={}, index=index), ConditionEvaluator()


def test_indicator_condition_exit_triggers_only_on_true():
    store = _store({CAL[2]: 10.0, CAL[3]: 10.0, CAL[4]: 10.0})
    # 条件：X.v > 5。CAL[2] 为 1（FALSE），CAL[3] 为 NaN（UNKNOWN），CAL[4] 为 10（TRUE）
    values = [1.0, 1.0, 1.0, np.nan, 10.0, 10.0, 10.0, 10.0]
    context, evaluator_cond = _condition_setup({"X.v": values})
    rules = DailyExitRuleSetV2(
        indicator_condition_exit=op("gt", ind("X", "v"), lit(5.0)),
        exit_types=("INDICATOR_CONDITION_EXIT",),
    )
    evaluator = DailyExitEvaluatorV2("C", "C", rules, condition_evaluator=evaluator_cond)
    lot = _lot()
    # FALSE 不触发
    assert evaluator.evaluate([lot], CAL[2], 2, store, session_index_of=_INDEX_OF,
                              condition_context=context) == []
    # UNKNOWN 不触发
    assert evaluator.evaluate([lot], CAL[3], 3, store, session_index_of=_INDEX_OF,
                              condition_context=context) == []
    # TRUE 触发
    decisions = evaluator.evaluate([lot], CAL[4], 4, store, session_index_of=_INDEX_OF,
                                   condition_context=context)
    assert decisions and decisions[0].reason_code == "EXIT_INDICATOR_CONDITION"


def test_reverse_signal_exit_triggers_only_on_true():
    store = _store({CAL[2]: 10.0, CAL[3]: 10.0})
    values = [1.0, 1.0, 0.0, 1.0, 1.0, 1.0, 1.0, 1.0]
    context, evaluator_cond = _condition_setup({"R.v": values})
    rules = DailyExitRuleSetV2(
        reverse_signal_exit=op("gt", ind("R", "v"), lit(0.5)),
        exit_types=("REVERSE_SIGNAL_EXIT",),
    )
    evaluator = DailyExitEvaluatorV2("C", "C", rules, condition_evaluator=evaluator_cond)
    lot = _lot()
    assert evaluator.evaluate([lot], CAL[2], 2, store, session_index_of=_INDEX_OF,
                              condition_context=context) == []
    decisions = evaluator.evaluate([lot], CAL[3], 3, store, session_index_of=_INDEX_OF,
                                   condition_context=context)
    assert decisions and decisions[0].reason_code == "EXIT_REVERSE_SIGNAL"


def test_condition_exit_without_evaluator_fails_closed():
    store = _store({CAL[2]: 10.0})
    rules = DailyExitRuleSetV2(
        indicator_condition_exit=op("gt", ind("X", "v"), lit(5.0)),
        exit_types=("INDICATOR_CONDITION_EXIT",),
    )
    evaluator = DailyExitEvaluatorV2("C", "C", rules)   # 未注入求值器
    context, _ = _condition_setup({"X.v": [10.0] * len(CAL)})
    with pytest.raises(ExitConfigError):
        evaluator.evaluate([_lot()], CAL[2], 2, store, session_index_of=_INDEX_OF,
                           condition_context=context)


# --------------------------------------------------------------------------
# 5. 多原因命中：保留全部原因 + 固定优先级 + 单条记录
# --------------------------------------------------------------------------
def test_multiple_hits_preserve_all_reasons_and_single_record():
    store = _store({CAL[2]: 8.0})
    rules = DailyExitRuleSetV2(
        stop_loss_pct=0.03, take_profit_pct=0.10,
        atr_distance=AtrDistanceSpec(multiple=1.5),
        structure_stop_price=9.0, structure_stop_scale="RAW",
        fixed_holding_sessions=1,
    )
    evaluator = DailyExitEvaluatorV2("C", "C", rules)
    evaluator.register_entry_atr("lot-1", 0.5)
    decisions = evaluator.evaluate([_lot()], CAL[2], 2, store, session_index_of=_INDEX_OF)
    assert len(decisions) == 1
    triggered = decisions[0].factor_values["triggered_reasons"]
    assert "EXIT_FIXED_COST_STOP" in triggered
    assert "EXIT_ATR_DISTANCE_STOP" in triggered
    assert "EXIT_STRUCTURE_PRICE_STOP" in triggered
    assert "EXIT_FIXED_HOLD" in triggered
    # 主展示原因按固定优先级：成本止损优先
    assert decisions[0].reason_code == "EXIT_FIXED_COST_STOP"
    # 同一 lot 同一 session 只应有一条评估记录（不得重复计数）
    records = [e for e in evaluator.evaluations
               if e.get("lot_id") == "lot-1" and e.get("trade_session") == CAL[2]]
    assert len(records) == 1


def test_exit_intent_not_revoked_by_later_price_recovery():
    store = _store({CAL[2]: 9.0, CAL[3]: 12.0, CAL[4]: 12.0})
    rules = DailyExitRuleSetV2(stop_loss_pct=0.03)
    evaluator = DailyExitEvaluatorV2("C", "C", rules)
    lot = _lot()
    first = evaluator.evaluate([lot], CAL[2], 2, store, session_index_of=_INDEX_OF)
    assert first and lot.exit_state == "EXIT_DUE"
    # 反弹不得撤销已成立的退出意图
    second = evaluator.evaluate([lot], CAL[3], 3, store, session_index_of=_INDEX_OF)
    assert second and second[0].state in {"EXIT_DUE", "SELL_PENDING"}
    assert lot.exit_state in {"EXIT_DUE", "SELL_PENDING"}


# --------------------------------------------------------------------------
# 6. 完整账户链（经公共服务）
# --------------------------------------------------------------------------
def _chain_bars(closes: list, *, volumes: list = None, highs=None, lows=None) -> list:
    n = len(closes)
    volumes = volumes or [5_000_000.0] * n
    rows = []
    for i, close in enumerate(closes):
        rows.append({
            "date": CAL[i % len(CAL)] if n <= len(CAL) else i,
            "open": close, "close": close,
            "high": float(highs[i]) if highs else close * 1.01,
            "low": float(lows[i]) if lows else close * 0.99,
            "volume": float(volumes[i]),
            "amount": close * float(volumes[i]),
        })
    return rows


def test_full_chain_positive_control_with_v2_condition():
    """正例：表达式条件驱动真实成交与账本变化。"""
    days = [int((pd.Timestamp("2024-09-02") + pd.Timedelta(days=i)).strftime("%Y%m%d"))
            for i in range(200)]
    days = [d for d in days if pd.Timestamp(str(d)).weekday() < 5][:130]
    closes = [10.0 + 0.06 * i + 0.8 * np.sin(i / 7) for i in range(len(days))]
    volumes = [1_000_000.0 * (1 + 0.5 * np.sin(i / 4)) for i in range(len(days))]
    bars = [{"date": days[i], "open": closes[i] * 0.999, "high": closes[i] * 1.02,
             "low": closes[i] * 0.98, "close": closes[i], "volume": volumes[i],
             "amount": closes[i] * volumes[i]} for i in range(len(days))]

    result = run_behavior_backtest_v2({
        "calendar": days, "symbols": [SYMBOL], "bars": {SYMBOL: bars},
        "indicators": [{"indicator_id": "RSI", "params": {"window": 14}}],
        "entry_condition": {
            "op": "and",
            "args": [
                {"op": "gt", "args": [{"op": "indicator", "args": ["RSI"],
                                       "params": {"output": "rsi"}},
                                      {"op": "const", "params": {"value": 40}}]},
                {"op": "gt", "args": [{"op": "field", "args": ["close"]},
                                      {"op": "const", "params": {"value": 0}}]},
            ],
        },
        "exit_rules": {"stop_loss_pct": 0.05, "fixed_holding_sessions": 10},
        "initial_cash": 100_000.0, "max_positions": 1, "max_position_weight": 1.0,
    })
    assert result["mode"] == BEHAVIOR_MODE_V2
    assert result["signals"], "正例必须产生信号"
    buys = [f for f in result["fills"] if f["side"] == "BUY"]
    sells = [f for f in result["fills"] if f["side"] == "SELL"]
    assert buys, "正例必须真实成交"
    assert sells, "正例必须有真实退出成交"
    assert result["cash"] != 100_000.0
    trace = result["resolved_config"]["condition_trace"][SYMBOL]
    assert trace["condition_true"] > 0
    assert trace["condition_unknown"] > 0     # 预热期 UNKNOWN
    # 正式估值
    assert result["official_valuation"]["event"] == "AFTER_CLOSE"
    assert result["official_valuation"]["n_days"] == len(days)


def test_full_chain_negative_control_impossible_condition():
    """反例：不可满足的条件不得成交，且保留正对照。"""
    days = [int((pd.Timestamp("2024-09-02") + pd.Timedelta(days=i)).strftime("%Y%m%d"))
            for i in range(200)]
    days = [d for d in days if pd.Timestamp(str(d)).weekday() < 5][:60]
    closes = [10.0 + 0.05 * i for i in range(len(days))]
    bars = [{"date": days[i], "open": closes[i], "high": closes[i] * 1.01,
             "low": closes[i] * 0.99, "close": closes[i], "volume": 5_000_000.0,
             "amount": closes[i] * 5_000_000.0} for i in range(len(days))]
    result = run_behavior_backtest_v2({
        "calendar": days, "symbols": [SYMBOL], "bars": {SYMBOL: bars},
        "entry_condition": {"op": "gt", "args": [
            {"op": "field", "args": ["close"]},
            {"op": "const", "params": {"value": 1e9}}]},
        "indicators": [{"indicator_id": "MA", "params": {"window": 5}}],
        "exit_rules": {},
        "initial_cash": 100_000.0, "max_positions": 1, "max_position_weight": 1.0,
    })
    assert result["signals"] == []
    assert result["fills"] == []
    assert result["cash"] == pytest.approx(100_000.0)
    trace = result["resolved_config"]["condition_trace"][SYMBOL]
    assert trace["condition_true"] == 0


def test_full_chain_custom_fixture_reaches_account_chain():
    """自定义组合 fixture 无需修改引擎即可进入账户链。"""
    days = [int((pd.Timestamp("2024-09-02") + pd.Timedelta(days=i)).strftime("%Y%m%d"))
            for i in range(220)]
    days = [d for d in days if pd.Timestamp(str(d)).weekday() < 5][:130]
    # 横盘后突破并放量，使突破条件真的成立
    closes = [10.0 + 0.02 * np.sin(i / 3) for i in range(80)]
    closes += [closes[-1] + 0.5 * (i - 79) for i in range(80, len(days))]
    volumes = [1_000_000.0] * 80 + [4_000_000.0] * (len(days) - 80)
    bars = [{"date": days[i], "open": closes[i], "high": closes[i] * 1.005,
             "low": closes[i] * 0.995, "close": closes[i], "volume": volumes[i],
             "amount": closes[i] * volumes[i]} for i in range(len(days))]

    result = run_behavior_backtest_v2({
        "calendar": days, "symbols": [SYMBOL], "bars": {SYMBOL: bars},
        "named_entry_condition": "BREAKOUT_WITH_VOLUME_CONFIRMATION",
        "exit_rules": {"stop_loss_pct": 0.06, "fixed_holding_sessions": 8},
        "initial_cash": 100_000.0, "max_positions": 1, "max_position_weight": 1.0,
    })
    assert result["signals"], "自定义突破条件应产生信号"
    assert [f for f in result["fills"] if f["side"] == "BUY"], "自定义条件必须能真实成交"
    # 依赖指标自动补齐
    outputs = result["resolved_config"]["indicator_outputs"][SYMBOL]
    assert "VOLUME_BREAKOUT_SCORE" in outputs


def test_full_chain_rejects_unknown_condition_and_mode():
    days = [20250102, 20250103]
    bars = [{"date": d, "open": 10.0, "high": 10.1, "low": 9.9, "close": 10.0,
             "volume": 1_000_000.0, "amount": 10_000_000.0} for d in days]
    with pytest.raises(BehaviorRequestError):
        run_behavior_backtest_v2({
            "calendar": days, "symbols": [SYMBOL], "bars": {SYMBOL: bars},
            "named_entry_condition": "NOT_A_FIXTURE", "exit_rules": {}})
    with pytest.raises(BehaviorRequestError):
        run_behavior_backtest_v2({
            "mode": "BT_BEHAVIOR_TICK_V1",
            "calendar": days, "symbols": [SYMBOL], "bars": {SYMBOL: bars},
            "entry_condition": {"op": "gt", "args": [
                {"op": "field", "args": ["close"]}, {"op": "const", "params": {"value": 1.0}}]},
            "exit_rules": {}})
    with pytest.raises(BehaviorRequestError):
        run_behavior_backtest_v2({
            "calendar": days, "symbols": [SYMBOL], "bars": {SYMBOL: bars},
            "entry_condition": {"op": "eval", "args": []}, "exit_rules": {}})


def test_full_chain_requires_calendar_coverage():
    """缺整日（含末日）必须被发现，不得静默缩短端点。"""
    days = [20250102, 20250103, 20250106]
    bars = [{"date": d, "open": 10.0, "high": 10.1, "low": 9.9, "close": 10.0,
             "volume": 1_000_000.0, "amount": 10_000_000.0} for d in days[:2]]
    with pytest.raises(BehaviorRequestError) as excinfo:
        run_behavior_backtest_v2({
            "calendar": days, "symbols": [SYMBOL], "bars": {SYMBOL: bars},
            "entry_condition": {"op": "gt", "args": [
                {"op": "field", "args": ["close"]}, {"op": "const", "params": {"value": 1.0}}]},
            "exit_rules": {}})
    assert "CALENDAR_NOT_COVERED_BY_DATA" in str(excinfo.value)
    assert "INCLUDES END SESSION" in str(excinfo.value)


def test_parse_expression_rejects_string_code():
    """表达式解析只接受显式结构，不接受字符串代码。"""
    with pytest.raises(BehaviorRequestError):
        parse_expression("close > 10")
    with pytest.raises(BehaviorRequestError):
        parse_expression({"op": "os_system", "args": []})
    # 合法结构
    node = parse_expression({"op": "gt", "args": [
        {"op": "field", "args": ["close"]}, {"op": "const", "params": {"value": 10.0}}]})
    assert node.op == "gt"
