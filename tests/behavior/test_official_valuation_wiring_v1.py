"""正式估值接线验收：必须通过**实际服务调用**证明完整性检查生效。

本文件针对的缺陷是"仅导出 ledger.snapshots 并取最后一条就宣称估值完整"。
正式口径（`official_equity_curve`）要求：

- **独立日历**由调用方显式传入，不从快照反推；
- 应有日历内缺任一天（含**末日**）-> fail-closed 抛错；
- 权益必须为**有限正数**；NaN / Inf -> 抛错；
- 基准端点必须与账户端点完全一致。

测试全部经公共入口 `run_behavior_backtest_v1` 与正式估值模块本身，
不使用 helper 单元测试替公共入口作证。
"""
from __future__ import annotations

import math

import pandas as pd
import pytest

from chanlun_trader.engine.behavior_service_v1 import (
    BehaviorRequestError,
    BehaviorRequestV1,
    run_behavior_backtest_v1,
)
from chanlun_trader.engine.official_valuation import (
    OfficialValuationError,
    diagnostic_curve_without_calendar,
    official_equity_curve,
)
from chanlun_trader.engine.ledger import LedgerSnapshot
from chanlun_trader.engine.time_types import tz_aware

from tests.behavior._fixtures import (
    CAL,
    ENTRY_INDEX,
    ENTRY_OPEN,
    FEE_CONTRACT,
    SYMBOL,
    entry_bars,
)


def _request(calendar=None, **overrides) -> BehaviorRequestV1:
    payload = {
        "calendar": list(calendar or CAL),
        "symbols": [SYMBOL],
        "bars": {SYMBOL: entry_bars({ENTRY_INDEX: ENTRY_OPEN})},
        "entry_conditions": ["ABOVE_ZERO_GOLDEN_CROSS"],
        "initial_cash": 100_000.0,
        "max_positions": 1,
        "max_position_weight": 1.0,
        "exit_rules": {},
        **FEE_CONTRACT,
    }
    payload.update(overrides)
    return BehaviorRequestV1.from_mapping(payload)


# --------------------------------------------------------------------------
# 1. 公共入口的正式估值输出
# --------------------------------------------------------------------------
def test_public_entry_returns_official_valuation_identity():
    result = run_behavior_backtest_v1(_request())
    official = result.official_valuation
    assert official["event"] == "AFTER_CLOSE"
    assert official["start_date"] == CAL[0]
    assert official["end_date"] == CAL[-1]
    assert official["n_days"] == len(CAL)
    assert official["calendar_identity"] != "INFERRED_FROM_SNAPSHOTS"
    # 每个点都必须带完整事件身份。
    for point in official["points"]:
        assert point["event_kind"] == "AFTER_CLOSE"
        assert "timestamp" in point and "event_sequence" in point
        assert math.isfinite(point["equity"]) and point["equity"] > 0


def test_public_entry_equity_curve_matches_official_points():
    """导出的 equity_curve 必须与正式估值逐点一致，而不是快照的原始全集。"""
    result = run_behavior_backtest_v1(_request())
    official_dates = [p["date"] for p in result.official_valuation["points"]]
    curve_dates = [row["date"] for row in result.equity_curve]
    assert curve_dates == official_dates
    assert result.final_equity == pytest.approx(
        result.official_valuation["points"][-1]["equity"], abs=1e-9)
    # 原始快照集远多于正式点（每天 4 个时钟事件）。
    assert len(curve_dates) < len(CAL) * 4


def test_public_entry_daily_equity_is_finite_and_positive():
    result = run_behavior_backtest_v1(_request())
    for row in result.equity_curve:
        assert math.isfinite(row["equity"]) and row["equity"] > 0
        assert math.isfinite(row["cash"])
        assert math.isfinite(row["market_value"])
        assert row["equity"] == pytest.approx(row["cash"] + row["market_value"], abs=1e-6)


# --------------------------------------------------------------------------
# 2. 独立日历：缺失整日必须被发现
# --------------------------------------------------------------------------
def test_missing_whole_session_is_detected_through_public_entry():
    """移除中间一整个 session 的行情：公共入口必须拒绝，而不是静默跳过。

    引擎对缺 bar 的 session 仍会发出快照并静默结转权益，因此该缺陷无法由
    事后估值发现，必须在入口按声明日历做数据覆盖校验（fail-closed）。
    """
    bars = [row for row in entry_bars({ENTRY_INDEX: ENTRY_OPEN}) if row["date"] != CAL[10]]
    request = BehaviorRequestV1.from_mapping({
        "calendar": list(CAL),          # 应有日历仍声明全部 session
        "symbols": [SYMBOL],
        "bars": {SYMBOL: bars},
        "entry_conditions": ["ABOVE_ZERO_GOLDEN_CROSS"],
        "initial_cash": 100_000.0, "max_positions": 1, "max_position_weight": 1.0,
        "exit_rules": {}, **FEE_CONTRACT,
    })
    with pytest.raises(BehaviorRequestError) as excinfo:
        run_behavior_backtest_v1(request)
    message = str(excinfo.value)
    assert message.startswith("CALENDAR_NOT_COVERED_BY_DATA")
    assert str(CAL[10]) in message


def test_missing_end_session_is_detected_and_not_silently_shortened():
    """末日整日丢失：必须被发现，不得把 end_date 悄悄缩短。"""
    bars = [row for row in entry_bars({ENTRY_INDEX: ENTRY_OPEN}) if row["date"] != CAL[-1]]
    request = BehaviorRequestV1.from_mapping({
        "calendar": list(CAL),
        "symbols": [SYMBOL],
        "bars": {SYMBOL: bars},
        "entry_conditions": ["ABOVE_ZERO_GOLDEN_CROSS"],
        "initial_cash": 100_000.0, "max_positions": 1, "max_position_weight": 1.0,
        "exit_rules": {}, **FEE_CONTRACT,
    })
    with pytest.raises(BehaviorRequestError) as excinfo:
        run_behavior_backtest_v1(request)
    assert "INCLUDES END SESSION" in str(excinfo.value)


def test_valuation_layer_detects_missing_session_in_snapshots():
    """估值层自身也要能发现缺整日：不依赖入口校验。

    构造声明 3 个 session、快照只覆盖 2 个（缺中间与末日）的输入。
    """
    snapshots = [
        LedgerSnapshot(timestamp=tz_aware(2025, 1, 2, 15, 30), cash=100.0,
                       market_value=0.0, equity=100.0, realized_pnl=0.0,
                       unrealized_pnl=0.0, positions=0, turnover=0.0),
        LedgerSnapshot(timestamp=tz_aware(2025, 1, 6, 15, 30), cash=100.0,
                       market_value=0.0, equity=100.0, realized_pnl=0.0,
                       unrealized_pnl=0.0, positions=0, turnover=0.0),
    ]
    with pytest.raises(OfficialValuationError) as excinfo:
        official_equity_curve(snapshots, calendar=[20250102, 20250103, 20250106])
    message = str(excinfo.value)
    assert "missing" in message
    assert "20250103" in message
    # 缺失的是中间日，不是末日：不得误报为末日缺失。
    assert "INCLUDES END SESSION" not in message


def test_valuation_layer_detects_missing_end_session():
    snapshots = [
        LedgerSnapshot(timestamp=tz_aware(2025, 1, 2, 15, 30), cash=100.0,
                       market_value=0.0, equity=100.0, realized_pnl=0.0,
                       unrealized_pnl=0.0, positions=0, turnover=0.0),
    ]
    with pytest.raises(OfficialValuationError) as excinfo:
        official_equity_curve(snapshots, calendar=[20250102, 20250103])
    assert "INCLUDES END SESSION" in str(excinfo.value)


# --------------------------------------------------------------------------
# 3. NaN / Inf 权益必须被发现
# --------------------------------------------------------------------------
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_equity_is_rejected(bad):
    """非有限权益 -> 抛错，不得接受。"""
    snapshots = [
        LedgerSnapshot(timestamp=tz_aware(2025, 1, 2, 15, 30), cash=100.0,
                       market_value=0.0, equity=100.0, realized_pnl=0.0,
                       unrealized_pnl=0.0, positions=0, turnover=0.0),
        LedgerSnapshot(timestamp=tz_aware(2025, 1, 3, 15, 30), cash=100.0,
                       market_value=0.0, equity=bad, realized_pnl=0.0,
                       unrealized_pnl=0.0, positions=0, turnover=0.0),
    ]
    with pytest.raises(OfficialValuationError):
        official_equity_curve(snapshots, calendar=[20250102, 20250103])


def test_non_positive_equity_is_rejected():
    snapshots = [
        LedgerSnapshot(timestamp=tz_aware(2025, 1, 2, 15, 30), cash=0.0,
                       market_value=0.0, equity=0.0, realized_pnl=0.0,
                       unrealized_pnl=0.0, positions=0, turnover=0.0),
    ]
    with pytest.raises(OfficialValuationError):
        official_equity_curve(snapshots, calendar=[20250102])


def test_non_numeric_equity_is_rejected():
    snapshots = [
        LedgerSnapshot(timestamp=tz_aware(2025, 1, 2, 15, 30), cash=1.0,
                       market_value=0.0, equity="not-a-number", realized_pnl=0.0,
                       unrealized_pnl=0.0, positions=0, turnover=0.0),
    ]
    with pytest.raises(OfficialValuationError):
        official_equity_curve(snapshots, calendar=[20250102])


# --------------------------------------------------------------------------
# 4. 独立日历是硬要求：无日历不得冒充正式结论
# --------------------------------------------------------------------------
def test_official_valuation_requires_explicit_calendar():
    snapshots = [
        LedgerSnapshot(timestamp=tz_aware(2025, 1, 2, 15, 30), cash=100.0,
                       market_value=0.0, equity=100.0, realized_pnl=0.0,
                       unrealized_pnl=0.0, positions=0, turnover=0.0),
    ]
    with pytest.raises(OfficialValuationError) as excinfo:
        official_equity_curve(snapshots, calendar=None)
    assert "explicit run calendar" in str(excinfo.value)


def test_diagnostic_curve_is_explicitly_non_official():
    """无日历的对照输出必须明确标记为非正式，不得冒充正式结论。"""
    snapshots = [
        LedgerSnapshot(timestamp=tz_aware(2025, 1, 2, 15, 30), cash=100.0,
                       market_value=0.0, equity=100.0, realized_pnl=0.0,
                       unrealized_pnl=0.0, positions=0, turnover=0.0),
    ]
    diagnostic = diagnostic_curve_without_calendar(snapshots)
    assert diagnostic["official"] is False
    assert diagnostic["status"] == "NON_OFFICIAL_DIAGNOSTIC"


# --------------------------------------------------------------------------
# 5. 同一 session 多事件取结算终态（不按 equity 大小挑选）
# --------------------------------------------------------------------------
def test_same_session_multiple_events_uses_settlement_terminal_state():
    snapshots = [
        LedgerSnapshot(timestamp=tz_aware(2025, 1, 2, 15, 30), cash=100.0,
                       market_value=0.0, equity=100.0, realized_pnl=0.0,
                       unrealized_pnl=0.0, positions=0, turnover=0.0),
        # 同日更晚插入的结算终态：权益更低，但必须取它（序号更大），
        # 而不是按 equity 大小挑选。
        LedgerSnapshot(timestamp=tz_aware(2025, 1, 2, 15, 30), cash=90.0,
                       market_value=0.0, equity=90.0, realized_pnl=0.0,
                       unrealized_pnl=0.0, positions=0, turnover=0.0),
    ]
    official = official_equity_curve(snapshots, calendar=[20250102])
    assert official.equity[0] == pytest.approx(90.0)
    assert official.points[0].event_sequence == 2


def test_endpoint_mismatch_is_rejected():
    snapshots = [
        LedgerSnapshot(timestamp=tz_aware(2025, 1, 2, 15, 30), cash=100.0,
                       market_value=0.0, equity=100.0, realized_pnl=0.0,
                       unrealized_pnl=0.0, positions=0, turnover=0.0),
    ]
    with pytest.raises(OfficialValuationError):
        official_equity_curve(snapshots, calendar=[20250102, 20250103],
                              start_date=20250102, end_date=20250103)
