"""合成行情 fixture 与独立手算预期值。

所有日期、价格、数量、费率都是**合成工程合同**，不代表任何真实历史行情、
真实税率或策略建议。独立交易日历显式声明，不从自然日推断。

入场条件锚定
------------
``SIGNAL_SHAPE`` 是一段确定性的合成走势，其第 ``SIGNAL_INDEX`` 根（0-based）
恰好是 ``ABOVE_ZERO_GOLDEN_CROSS`` 唯一成立的位置（MACD warmup = slow+signal-1
= 34，故 SIGNAL_INDEX = 33）。该序列末值被归一化为 ``ENTRY_OPEN``，因此：

- 信号日 = CAL[SIGNAL_INDEX]，收盘价 = ENTRY_OPEN；
- 成交日 = CAL[ENTRY_INDEX]，开盘价 = ENTRY_OPEN；
- 成交价 = ENTRY_OPEN * (1 + slippage_bps)（确定性，可手算）。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

import pandas as pd

SYMBOL = "600000.SH"
STRATEGY = "BT_BEHAVIOR_V1"

# MACD_V1 warmup = slow + signal - 1 = 34。
WARMUP_SESSIONS = 34
SIGNAL_INDEX = WARMUP_SESSIONS - 1          # 33：条件唯一成立的位置
ENTRY_INDEX = WARMUP_SESSIONS               # 34：信号日的下一 session
SESSION_COUNT = 46
CAL: List[int] = []

# 成交价锚（信号日收盘 = 成交日开盘）。
ENTRY_OPEN = 10.0

# 合成费率合同（仅用于本轮确定型验收，不作跨历史真实税率声明）。
FEE_CONTRACT = {
    "commission_rate": 0.00025,
    "min_commission": 5.0,
    "stamp_tax_rate": 0.0005,
    "slippage_bps": 0.001,
}

# 手算基准样例（附件第 10 节）：初始 10000，买 100 股 @10 佣金 5；
# 另一合法 session 卖 100 股 @11、卖佣金 5、印花税 0.0005、无滑点。
MANUAL_CASE = {
    "initial_cash": 10000.0,
    "buy_price": 10.0,
    "buy_quantity": 100,
    "buy_commission": 5.0,
    "sell_price": 11.0,
    "sell_commission": 5.0,
    "stamp_tax_rate": 0.0005,
    "cash_after_buy": 8995.0,
    "sell_net_proceeds": 1094.45,
    "final_cash": 10089.45,
    "net_profit": 89.45,
}

# 分段乘数：上涨16 / 回调3 / 上涨3 / 上涨5 / 回调3 / 上涨4（共 34 根）。
_SHAPE_SEGMENTS: Tuple[Tuple[int, float], ...] = (
    (16, 1.008), (3, 0.993), (3, 1.008), (5, 1.004), (3, 0.996), (4, 1.009),
)


def trading_days(count: int, start: str = "2024-09-02") -> List[int]:
    """生成 count 个工作日键（合成交易日历，不声称真实节假日语义）。"""
    out: List[int] = []
    cursor = pd.Timestamp(start)
    while len(out) < count:
        if cursor.weekday() < 5:
            out.append(int(cursor.strftime("%Y%m%d")))
        cursor += pd.Timedelta(days=1)
    return out


CAL = trading_days(SESSION_COUNT)


def signal_shape_closes() -> List[float]:
    """返回 34 根收盘价：第 33 根（末根）恰好为 ``ENTRY_OPEN``。"""
    values: List[float] = []
    price = 8.0
    for count, factor in _SHAPE_SEGMENTS:
        for _ in range(count):
            price *= factor
            values.append(price)
    assert len(values) == WARMUP_SESSIONS, len(values)
    scale = ENTRY_OPEN / values[-1]
    return [round(value * scale, 6) for value in values]


SIGNAL_SHAPE = signal_shape_closes()


def _row(day: int, open_px: float, close_px: float, previous_close: float,
         volume: float, high: Optional[float] = None, low: Optional[float] = None) -> dict:
    return {
        "date": int(day),
        "open": float(open_px),
        "close": float(close_px),
        "high": float(high) if high is not None else max(open_px, close_px) * 1.005,
        "low": float(low) if low is not None else min(open_px, close_px) * 0.995,
        "volume": float(volume),
        "amount": float(close_px) * float(volume),
        "prev_close": float(previous_close),
    }


def entry_bars(
    closes: Optional[Dict[int, float]] = None,
    *,
    opens: Optional[Dict[int, float]] = None,
    highs: Optional[Dict[int, float]] = None,
    lows: Optional[Dict[int, float]] = None,
    volumes: Optional[Dict[int, float]] = None,
    days: Sequence[int] = CAL,
    volume: float = 5_000_000.0,
) -> List[dict]:
    """构造完整日线序列（要求 ``days`` 至少覆盖预热段）。

    - 第 0..33 根使用 ``SIGNAL_SHAPE``（open = 前一根收盘）；
    - 第 34 根起使用 ``closes`` 字典（缺省 ENTRY_OPEN）；第 34 根 open 固定为
      ``ENTRY_OPEN``，其后 open = 前一根收盘（可用 ``opens`` 覆盖以制造跳空）；
    - ``highs`` / ``lows`` / ``volumes`` 可按 index 覆盖。

    注意：V2 在 SESSION_OPEN 成交时用**上一交易日**已知 volume 做容量约束，
    因此要限制 ENTRY_INDEX 当天买入的容量，必须设置 ``SIGNAL_INDEX`` 的 volume。
    """
    if len(days) < WARMUP_SESSIONS:
        raise ValueError("DAYS_SHORTER_THAN_WARMUP: 请使用 flat_bars 构造预热不足场景")
    closes = dict(closes or {})
    opens = dict(opens or {})
    highs = dict(highs or {})
    lows = dict(lows or {})
    volumes = dict(volumes or {})

    rows: List[dict] = []
    previous_close = float(SIGNAL_SHAPE[0])
    for index in range(WARMUP_SESSIONS):
        close = float(SIGNAL_SHAPE[index])
        rows.append(_row(days[index], previous_close, close, previous_close,
                         volumes.get(index, volume)))
        previous_close = close

    for index in range(WARMUP_SESSIONS, len(days)):
        close = float(closes.get(index, ENTRY_OPEN))
        if index in opens:
            open_px = float(opens[index])
        elif index == ENTRY_INDEX:
            open_px = ENTRY_OPEN
        else:
            open_px = float(closes.get(index - 1, ENTRY_OPEN))
        rows.append(_row(days[index], open_px, close, previous_close,
                         volumes.get(index, volume), highs.get(index), lows.get(index)))
        previous_close = close
    return rows


def downtrend_bars(days: Sequence[int] = CAL, *, volume: float = 5_000_000.0) -> List[dict]:
    """单调下跌序列：DIF/DEA 始终在零轴下方，水上条件永不成立。"""
    rows: List[dict] = []
    price = 20.0
    previous_close = price
    for day in days:
        close = round(price, 6)
        rows.append(_row(day, previous_close, close, previous_close, volume))
        previous_close = close
        price *= 0.99
    return rows


def flat_bars(
    days: Sequence[int] = CAL,
    *,
    open_px: float = 10.0,
    close_px: float = 10.0,
    volume: float = 5_000_000.0,
    overrides: Optional[Dict[int, dict]] = None,
) -> List[dict]:
    """等值日线序列；overrides 可逐日覆盖任意字段（用于账本/费用单测）。"""
    rows: List[dict] = []
    previous_close = float(open_px)
    for day in days:
        row = {
            "date": int(day), "open": float(open_px), "close": float(close_px),
            "high": max(open_px, close_px) * 1.01, "low": min(open_px, close_px) * 0.99,
            "volume": float(volume), "amount": float(close_px * volume),
            "prev_close": float(previous_close),
        }
        if overrides and int(day) in overrides:
            row.update(overrides[int(day)])
        rows.append(row)
        previous_close = float(row["close"])
    return rows


def fee(side: str, quantity: int, price: float, contract: Optional[dict] = None) -> dict:
    """独立费用计算（不使用被测的 FeeModel）。"""
    cfg = dict(FEE_CONTRACT)
    cfg.update(contract or {})
    value = quantity * price
    commission = max(value * cfg["commission_rate"], cfg["min_commission"])
    stamp = value * cfg["stamp_tax_rate"] if side == "SELL" else 0.0
    return {
        "commission": round(commission, 4),
        "stamp_tax": round(stamp, 4),
        "total_fee": round(commission + stamp, 4),
        "gross_value": round(value, 4),
    }


def slippage(side: str, reference_price: float, contract: Optional[dict] = None) -> float:
    cfg = dict(FEE_CONTRACT)
    cfg.update(contract or {})
    if side == "BUY":
        return round(reference_price * (1 + cfg["slippage_bps"]), 4)
    return round(reference_price * (1 - cfg["slippage_bps"]), 4)


def manual_account_case() -> dict:
    """独立手算账户链（零滑点、固定费率）预期值。

    买入：gross = 100*10 = 1000，佣金 = max(0.25, 5) = 5，现金 10000-1005 = 8995。
    卖出：gross = 100*11 = 1100，佣金 = max(0.275, 5) = 5，
          印花税 = 1100*0.0005 = 0.55，净回款 = 1100-5-0.55 = 1094.45。
    期末现金 = 8995 + 1094.45 = 10089.45；净利润 = 89.45。
    """
    case = dict(MANUAL_CASE)
    buy = fee("BUY", case["buy_quantity"], case["buy_price"],
              {"commission_rate": 0.0, "min_commission": case["buy_commission"], "slippage_bps": 0.0})
    sell = fee("SELL", case["buy_quantity"], case["sell_price"],
               {"commission_rate": 0.0, "min_commission": case["sell_commission"],
                "stamp_tax_rate": case["stamp_tax_rate"], "slippage_bps": 0.0})
    case["buy_fee"] = buy["total_fee"]
    case["sell_fee"] = sell["total_fee"]
    case["expected_cash_after_buy"] = (
        case["initial_cash"] - case["buy_quantity"] * case["buy_price"] - buy["total_fee"])
    case["expected_sell_proceeds"] = case["buy_quantity"] * case["sell_price"] - sell["total_fee"]
    case["expected_final_cash"] = case["expected_cash_after_buy"] + case["expected_sell_proceeds"]
    case["expected_net_profit"] = case["expected_final_cash"] - case["initial_cash"]
    return case


def to_dataset_csv(path, frames: Dict[str, List[dict]]) -> str:
    """把多证券合成行情写成 CSV（项目实际支持的文件入口）。"""
    rows = []
    for symbol, symbol_bars in frames.items():
        for row in symbol_bars:
            rows.append({"symbol": symbol, **row})
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8")
    return str(path)
