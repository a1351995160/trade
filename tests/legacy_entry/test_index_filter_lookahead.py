"""旧入口 _index_allows_buy 开盘前视缺陷的复现与修复验证（红→绿证据）。

反例构造：
    截至 d 日开盘，**所有可知输入完全相同**（历史指数收盘、个股行情、
    信号、日历、参数），只修改 **d 日当天的指数收盘值**（未来数据）。
    若开盘决策因此改变，则证明该实现使用了 d 日未来收盘。

测试同时给出正常成交正对照，避免"让所有买入都失败"式通过。
"""
from __future__ import annotations

import pandas as pd
import pytest

from chanlun_trader.backtest import BacktestRunner

CAL = [20250102, 20250103, 20250106, 20250107, 20250108]
SYMBOL = "600000"

# 严格早于 CAL[1] 的已完成指数交易日（整数键；此处不要求真实交易日历）。
PRIOR_DAYS = [20241226, 20241227, 20241230, 20241231, 20250101, 20250102]


def _base_index(prior_close: float = 10.0) -> dict[int, float]:
    return {day: prior_close for day in PRIOR_DAYS}


def _index_frame(day_to_close: dict[int, float]) -> pd.DataFrame:
    days = sorted(day_to_close)
    values = [day_to_close[d] for d in days]
    return pd.DataFrame({
        "date": days, "open": values, "high": values, "low": values,
        "close": values, "volume": [1_000_000.0] * len(days),
        "amount": [1e7] * len(days),
    })


class _FakeTdx:
    """最小 TdxData 替身：只提供 BacktestRunner 实际读取的接口。"""

    def __init__(self, stock: pd.DataFrame):
        self.vipdoc = "SYNTHETIC"
        self._stock = stock

    def get_qfq_day(self, code: str, market: int) -> pd.DataFrame:
        return self._stock.copy()

    def get_benchmark(self, code: str = "sh000001", start_date: int = 0, end_date=None):
        return None


class _IndexFilterRunner(BacktestRunner):
    """保留原 run()/成交/费用/账本链，只注入确定性行情与信号。

    只覆盖 prepare()，run() 使用仓库原实现（含被测的 _index_allows_buy）。
    """

    def __init__(self, tdx, cfg, *, index_closes: dict[int, float]):
        super().__init__(tdx, cfg)
        self._index_closes = dict(index_closes)

    def prepare(self, limit=None, progress_cb=None) -> None:
        stock = self.tdx.get_qfq_day(SYMBOL, 1).sort_values("date").reset_index(drop=True)
        dates = stock["date"].astype("int64")
        for name, column in (
            ("open_series", "qfq_open"), ("close_series", "qfq_close"),
            ("high_series", "qfq_high"), ("low_series", "qfq_low"),
            ("raw_open_series", "open"), ("raw_close_series", "close"),
            ("raw_high_series", "high"), ("raw_low_series", "low"),
        ):
            getattr(self, name)[SYMBOL] = pd.Series(stock[column].to_numpy(), index=dates)
        self.volume_series[SYMBOL] = pd.Series(stock["volume"].to_numpy(), index=dates)
        self.calendar = list(CAL)
        self.signals = [{
            "code": SYMBOL,
            "signal_date": CAL[0],
            "exec_date": CAL[1],
            "signal_type": "B1",
            "direction": "buy",
            "price_ref": float(stock["qfq_close"].iloc[0]),
            "stop_low": float(stock["qfq_low"].iloc[0]),
            "score": 1.0,
        }]
        if self.index_filter_enabled:
            index = _index_frame(self._index_closes)
            key = index["date"].astype("int64")
            self.index_close = pd.Series(index["close"].to_numpy(), index=key)
            self.index_ma = pd.Series(
                index["close"].rolling(self.index_ma_period).mean().to_numpy(), index=key,
            )


def _config(*, ma_period: int = 3, enabled: bool = True) -> dict:
    return {
        "backtest": {
            "start": "2025-01-02", "end": "2025-01-08",
            "initial_cash": 100_000.0, "max_positions": 1,
            "commission_rate": 0.00025, "min_commission": 5.0,
            "stamp_tax_rate": 0.0005, "slippage": 0.001,
            "stop_loss_pct": 0.03, "max_holding_days": 0, "limit_rule": True,
        },
        "universe": {"min_list_days": 1},
        "index_filter": {"enabled": enabled, "ma_period": ma_period},
        "tdx": {},
    }


def _stock_frame() -> pd.DataFrame:
    rows = []
    for day in CAL:
        rows.append({
            "date": day, "open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2,
            "qfq_open": 10.0, "qfq_high": 10.5, "qfq_low": 9.8, "qfq_close": 10.2,
            "volume": 1_000_000.0, "amount": 1e7,
        })
    return pd.DataFrame(rows)


def _run(index_closes: dict[int, float], **cfg_kwargs):
    runner = _IndexFilterRunner(_FakeTdx(_stock_frame()), _config(**cfg_kwargs), index_closes=index_closes)
    return runner, runner.run()


def _bought_on(day: int, index_closes: dict[int, float], **cfg_kwargs) -> bool:
    _, result = _run(index_closes, **cfg_kwargs)
    return any(int(t.buy_date) == day for t in result["trades"])


# --------------------------------------------------------------------------
# 1. 核心反例：只改 d 日未来指数收盘，开盘决策不得改变
# --------------------------------------------------------------------------
def test_open_decision_ignores_same_day_index_close():
    """只改 CAL[1] 当天（未来）指数收盘，CAL[1] 开盘买入决策必须相同。"""
    with_high_same_day = _base_index()
    with_high_same_day[CAL[1]] = 100.0     # 当天暴涨（未来）
    with_low_same_day = _base_index()
    with_low_same_day[CAL[1]] = 1.0        # 当天暴跌（未来）

    assert _bought_on(CAL[1], with_high_same_day) == _bought_on(CAL[1], with_low_same_day), (
        "CAL[1] 开盘决策被当天未来指数收盘改变：原实现读取了 d 日收盘（前视）"
    )


def test_open_decision_uses_prior_index_close():
    """正对照：CAL[1] 开盘决策确实由严格早于它的已完成收盘决定。"""
    strong_prior = _base_index(10.0)
    strong_prior[CAL[1]] = 1.0              # 当天暴跌也不应影响开盘决策
    weak_prior = {day: 10.0 for day in PRIOR_DAYS[:-1]}
    weak_prior[PRIOR_DAYS[-1]] = 1.0        # 前一交易日收盘走弱
    weak_prior[CAL[1]] = 100.0              # 当天暴涨也不应影响开盘决策

    assert _bought_on(CAL[1], strong_prior) is True
    assert _bought_on(CAL[1], weak_prior) is False


# --------------------------------------------------------------------------
# 2. 边界负例
# --------------------------------------------------------------------------
def test_missing_prior_index_close_is_fail_closed():
    """d 之前没有任何已知指数收盘时不得放行（fail-closed）。"""
    runner = _IndexFilterRunner(_FakeTdx(_stock_frame()), _config(), index_closes={})
    runner.prepare()
    assert runner._index_allows_buy(CAL[1]) is False


def test_index_ma_warmup_insufficient_blocks():
    """均线预热不足（已知历史不足 ma_period）时必须阻塞，不得默认放行。"""
    index_closes = {PRIOR_DAYS[-1]: 100.0, CAL[1]: 1.0}
    runner = _IndexFilterRunner(_FakeTdx(_stock_frame()), _config(ma_period=5), index_closes=index_closes)
    runner.prepare()
    assert runner._index_allows_buy(CAL[1]) is False


def test_last_session_uses_previous_close_not_its_own():
    """最后交易日：仍只能使用前一交易日收盘，不得读取自身收盘。"""
    index_closes = _base_index(100.0)
    for day in CAL[:4]:
        index_closes[day] = 100.0
    index_closes[CAL[4]] = 1.0
    runner = _IndexFilterRunner(_FakeTdx(_stock_frame()), _config(), index_closes=index_closes)
    runner.prepare()
    assert runner._index_allows_buy(CAL[4]) is True


def test_index_filter_disabled_is_unaffected():
    """关闭大盘过滤时行为不受该参数影响（不得因修复而改变）。"""
    index_closes = {PRIOR_DAYS[-1]: 1.0, CAL[1]: 1.0}
    runner = _IndexFilterRunner(_FakeTdx(_stock_frame()), _config(enabled=False), index_closes=index_closes)
    runner.prepare()
    assert runner._index_allows_buy(CAL[1]) is True


# --------------------------------------------------------------------------
# 3. 正常成交正对照
# --------------------------------------------------------------------------
def test_positive_control_real_fill_still_happens():
    """大盘环境允许时必须真实成交，不是"全部拒买"式通过。"""
    index_closes = _base_index(10.0)
    index_closes[CAL[1]] = 100.0
    _, result = _run(index_closes)
    buys = [t for t in result["trades"] if t.buy_date == CAL[1]]
    assert len(buys) == 1
    assert buys[0].buy_price == pytest.approx(10.0 * 1.001, abs=1e-9)
    assert buys[0].shares == 9900
