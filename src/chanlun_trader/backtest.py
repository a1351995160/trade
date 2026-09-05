"""回测引擎（一期 MVP）。

规则：
- 信号日收盘后产生信号，下一根可交易 K 线开盘价成交；
- 买入：缠论一/二/三类买点；卖出：一/二/三类卖点、止损、最大持有期；
- T+1：买入当日不能卖出（本实现中卖出订单最早在买入次一根 K 线执行）；
- 费用：佣金（双边，最低 5 元）、印花税（卖出）、滑点（买价上浮、卖价下浮）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .chan import Signal, analyze_stock, score_signals, top_per_day
from .industry import load_industry_map
from .tdx_data import TdxData, list_a_stocks


@dataclass
class Trade:
    code: str
    signal_type: str
    buy_date: int
    buy_price: float
    shares: int
    sell_date: int
    sell_price: float
    sell_reason: str
    pnl: float
    pnl_pct: float
    holding_days: int


@dataclass
class Holding:
    code: str
    signal_type: str
    buy_date: int
    buy_price: float
    shares: int
    stop_low: float
    peak_close: float = 0.0
    stop_reason: str = "stop_loss"


@dataclass
class Order:
    code: str
    direction: str  # buy / sell
    signal_type: str
    reason: str
    stop_low: float = 0.0


class BacktestRunner:
    def __init__(self, tdx: TdxData, cfg: dict):
        self.tdx = tdx
        self.cfg = cfg
        self.start = int(str(cfg["backtest"]["start"]).replace("-", ""))
        self.end = int(str(cfg["backtest"]["end"]).replace("-", ""))
        self.initial_cash = float(cfg["backtest"].get("initial_cash", 1_000_000))
        self.max_positions = int(cfg["backtest"].get("max_positions", 10))
        self.max_picks_per_day = int(cfg["backtest"].get("max_picks_per_day", 0))
        self.max_per_industry_per_day = int(cfg["backtest"].get("max_per_industry_per_day", 0))
        self.industry_map = load_industry_map(cfg.get("tdx", {}))
        self.commission_rate = float(cfg["backtest"].get("commission_rate", 0.00025))
        self.min_commission = float(cfg["backtest"].get("min_commission", 5.0))
        self.stamp_tax_rate = float(cfg["backtest"].get("stamp_tax_rate", 0.0005))
        self.slippage = float(cfg["backtest"].get("slippage", 0.001))
        self.stop_loss_pct = float(cfg["backtest"].get("stop_loss_pct", 0.03))
        self.max_holding_days = int(cfg["backtest"].get("max_holding_days", 60))
        self.min_list_days = int(cfg["universe"].get("min_list_days", 120))
        self.min_amount_ma20 = float(cfg["universe"].get("min_amount_ma20", 0))
        self.amount_top_n = int(cfg["universe"].get("amount_top_n", 0))
        self.amount_lookback = int(cfg["universe"].get("amount_lookback", 250))
        self.limit_rule = bool(cfg["backtest"].get("limit_rule", True))
        # 二期：大盘环境过滤与移动止损
        idx_cfg = cfg.get("index_filter", {})
        self.index_filter_enabled = bool(idx_cfg.get("enabled", False))
        self.index_ma_period = int(idx_cfg.get("ma_period", 20))
        ts_cfg = cfg.get("trailing_stop", {})
        self.trailing_enabled = bool(ts_cfg.get("enabled", False))
        self.trailing_activate = float(ts_cfg.get("activate_pct", 0.05))
        self.trailing_pct = float(ts_cfg.get("trail_pct", 0.08))

        self.index_close: pd.Series | None = None
        self.index_ma: pd.Series | None = None
        self.open_series: dict[str, pd.Series] = {}
        self.close_series: dict[str, pd.Series] = {}
        self.high_series: dict[str, pd.Series] = {}
        self.low_series: dict[str, pd.Series] = {}
        self.volume_series: dict[str, pd.Series] = {}
        self.raw_open_series: dict[str, pd.Series] = {}
        self.raw_close_series: dict[str, pd.Series] = {}
        self.raw_high_series: dict[str, pd.Series] = {}
        self.raw_low_series: dict[str, pd.Series] = {}
        self.amount_ma20: dict[str, pd.Series] = {}
        self.amount_trail: dict[str, pd.Series] = {}
        self.liquid_codes_by_date: dict[int, set[str]] = {}
        self.signals: list[dict] = []
        self.calendar: list[int] = []

    def _next_bar_date(self, code: str, date: int) -> int | None:
        """返回该股票在 date 之后的第一个交易日；没有则返回 None。"""
        ser = self.open_series[code]
        pos = ser.index.searchsorted(date, side="right")
        if pos < len(ser):
            return int(ser.index[pos])
        return None

    def prepare(self, limit: int | None = None, progress_cb=None) -> None:
        """加载股票池、行情序列，并预计算全部缠论信号。

        progress_cb 为可选回调，签名为 progress_cb(done: int, total: int)。
        """
        # 回测股票池不使用当前 ST/行业/证券主数据快照（无时点数据，存在前视）。
        # 全市场 A 股都会参与信号计算，信号时点再用历史成交额排名过滤。
        stocks = list_a_stocks(self.tdx.vipdoc)
        total = min(len(stocks), limit or len(stocks))
        if limit:
            stocks = stocks[:limit]
        done = 0
        chan_cfg = self.cfg.get("chan", {})
        bi_gap = int(chan_cfg.get("bi_gap", 4))
        fast = int(chan_cfg.get("macd_fast", 12))
        slow = int(chan_cfg.get("macd_slow", 26))
        signal = int(chan_cfg.get("macd_signal", 9))
        buy_candidates: list[Signal] = []
        sell_candidates: list[Signal] = []

        for st in stocks:
            code, market = st["code"], st["market"]
            done += 1
            if progress_cb:
                progress_cb(done, total)
            try:
                df = self.tdx.get_qfq_day(code, market)
            except Exception:
                continue
            if len(df) < self.min_list_days:
                continue
            df = df.sort_values("date").reset_index(drop=True)
            # 未来数据截断：信号计算只用 end 及以前的 K 线，防止尾部合并/滚动指标带入未来。
            df = df[df["date"] <= self.end].reset_index(drop=True)
            if df.empty:
                continue
            df["amount_ma20"] = df["amount"].rolling(20).mean()
            dates = df["date"].astype(np.int64)
            self.open_series[code] = pd.Series(df["qfq_open"].to_numpy(), index=dates)
            self.close_series[code] = pd.Series(df["qfq_close"].to_numpy(), index=dates)
            self.high_series[code] = pd.Series(df["qfq_high"].to_numpy(), index=dates)
            self.low_series[code] = pd.Series(df["qfq_low"].to_numpy(), index=dates)
            self.volume_series[code] = pd.Series(df["volume"].to_numpy(), index=dates)
            self.raw_open_series[code] = pd.Series(df["open"].to_numpy(), index=dates)
            self.raw_close_series[code] = pd.Series(df["close"].to_numpy(), index=dates)
            self.raw_high_series[code] = pd.Series(df["high"].to_numpy(), index=dates)
            self.raw_low_series[code] = pd.Series(df["low"].to_numpy(), index=dates)
            if self.min_amount_ma20 > 0:
                self.amount_ma20[code] = pd.Series(
                    df["amount"].rolling(20).mean().to_numpy(), index=dates
                )
            if self.amount_top_n > 0:
                self.amount_trail[code] = pd.Series(
                    df["amount"].rolling(self.amount_lookback).mean().to_numpy(), index=dates
                )
            _, _, bis, signals, _ = analyze_stock(
                df,
                code=code,
                bi_gap=bi_gap,
                fast=fast,
                slow=slow,
                signal=signal,
                filter_cfg=self.cfg.get("signal_filter"),
            )
            signal_cfg = self.cfg.get("signal_filter", {})
            signals = score_signals(df, signals, bis, score_cfg=signal_cfg.get("score_cfg"))
            for s in signals:
                if s.signal_date < self.start or s.signal_date > self.end:
                    continue
                if s.direction == "buy":
                    if self.min_amount_ma20 > 0:
                        avg = self.amount_ma20[code].get(s.signal_date, np.nan)
                        if avg is None or (isinstance(avg, float) and np.isnan(avg)) or avg < self.min_amount_ma20:
                            continue
                    buy_candidates.append(s)
                else:
                    sell_candidates.append(s)
        # 交易日历：优先用上证指数日线
        bench = self.tdx.get_benchmark("sh000001")
        if bench is not None and len(bench):
            cal = [int(d) for d in bench["date"] if self.start <= int(d) <= self.end]
        else:
            all_dates = set()
            for ser in self.close_series.values():
                all_dates.update(int(d) for d in ser.index if self.start <= int(d) <= self.end)
            cal = sorted(all_dates)
        self.calendar = cal

        # Point-In-Time 股票池：每个交易日只保留过去 amount_lookback 日平均成交额前 amount_top_n 的股票
        if self.amount_top_n > 0 and self.amount_trail:
            amt = pd.DataFrame(self.amount_trail).sort_index()
            cols = amt.columns
            arr = amt.to_numpy()
            for i, date in enumerate(amt.index):
                row = arr[i]
                finite = np.where(np.isfinite(row))[0]
                if len(finite) <= self.amount_top_n:
                    top_idx = finite
                else:
                    k = min(self.amount_top_n, len(finite))
                    part = np.argpartition(row[finite], -k)[-k:]
                    top_idx = finite[part]
                self.liquid_codes_by_date[int(date)] = set(cols[top_idx])
            buy_candidates = [
                s for s in buy_candidates
                if s.code in self.liquid_codes_by_date.get(int(s.signal_date), set())
            ]

        # 全局每日前 N + 行业去重（只对买点生效，卖点全部保留）
        picked_buys = top_per_day(
            buy_candidates,
            self.max_picks_per_day,
            industry_map=self.industry_map,
            max_per_industry=self.max_per_industry_per_day,
        )
        for s in picked_buys:
            exec_date = self._next_bar_date(s.code, s.signal_date)
            if exec_date is None or exec_date > self.end:
                continue
            self.signals.append(
                {
                    "code": s.code,
                    "signal_date": s.signal_date,
                    "exec_date": exec_date,
                    "signal_type": s.signal_type,
                    "direction": s.direction,
                    "price_ref": s.price_ref,
                    "stop_low": s.stop_low,
                    "score": s.score,
                }
            )
        for s in sell_candidates:
            exec_date = self._next_bar_date(s.code, s.signal_date)
            if exec_date is None or exec_date > self.end:
                continue
            self.signals.append(
                {
                    "code": s.code,
                    "signal_date": s.signal_date,
                    "exec_date": exec_date,
                    "signal_type": s.signal_type,
                    "direction": s.direction,
                    "price_ref": s.price_ref,
                    "stop_low": s.stop_low,
                    "score": s.score,
                }
            )
        self.signals.sort(key=lambda x: (int(x["signal_date"]), -float(x.get("score", 0.0)), x["code"]))

        # 二期：大盘环境过滤所需的上证指数均线
        if self.index_filter_enabled:
            bench = self.tdx.get_benchmark("sh000001")
            if bench is not None and len(bench):
                b = bench.sort_values("date").reset_index(drop=True)
                self.index_close = pd.Series(b["close"].to_numpy(), index=b["date"].astype(np.int64))
                self.index_ma = pd.Series(
                    b["close"].rolling(self.index_ma_period).mean().to_numpy(),
                    index=b["date"].astype(np.int64),
                )
            else:
                self.index_filter_enabled = False

    def _index_allows_buy(self, date: int) -> bool:
        """大盘环境过滤：上一交易日上证指数收盘价 >= MA。"""
        if not self.index_filter_enabled or self.index_close is None or self.index_ma is None:
            return True
        pos = self.index_close.index.searchsorted(date, side="right") - 1
        if pos < 0:
            return True
        prev_date = int(self.index_close.index[pos])
        close_val = float(self.index_close.iloc[pos])
        ma_val = float(self.index_ma.iloc[pos])
        return close_val >= ma_val

    def _max_positions_for_date(self, date: int) -> int:
        """当日最大持仓数。子类可覆盖以支持动态 exposure。"""
        return self.max_positions

    def _next_calendar_day(self, date: int) -> int | None:
        if not self.calendar:
            return None
        pos = None
        for i, d in enumerate(self.calendar):
            if int(d) > int(date):
                pos = i
                break
        if pos is None:
            return None
        return int(self.calendar[pos])

    def _buy(self, cash: float, price: float) -> tuple[int, float]:
        """按等权仓位计算可买股数（100 股整数倍）。返回 (股数, 总成本)。"""
        if price <= 0 or cash <= 0:
            return 0, 0.0
        budget = min(cash, self._last_equity / self.max_positions)
        shares = int(budget / price / 100) * 100
        while shares >= 100:
            cost = shares * price + max(shares * price * self.commission_rate, self.min_commission)
            if cost <= cash:
                return shares, cost
            shares -= 100
        return 0, 0.0

    def _sell_proceeds(self, shares: int, price: float) -> float:
        value = shares * price
        commission = max(value * self.commission_rate, self.min_commission)
        stamp = value * self.stamp_tax_rate
        return value - commission - stamp

    def _mark_to_market(self, cash: float, holdings: dict[str, Holding], date: int) -> float:
        total = cash
        for h in holdings.values():
            ser = self.close_series.get(h.code)
            if ser is None:
                total += h.shares * h.buy_price
                continue
            pos = ser.index.searchsorted(date, side="right") - 1
            if pos >= 0:
                total += h.shares * float(ser.iloc[pos])
            else:
                total += h.shares * h.buy_price
        return total

    def run(self, limit: int | None = None, progress_cb=None) -> dict:
        self.prepare(limit, progress_cb=progress_cb)
        pending: dict[int, list[Order]] = {}
        for sig in self.signals:
            pending.setdefault(sig["exec_date"], []).append(
                Order(sig["code"], sig["direction"], sig["signal_type"], sig["signal_type"], sig["stop_low"])
            )

        cash = self.initial_cash
        holdings: dict[str, Holding] = {}
        trades: list[Trade] = []
        self._last_equity = self.initial_cash
        equity_curve: list[tuple[int, float]] = []

        for d in self.calendar:
            self._current_date = int(d)
            # 1. 开盘执行当日到期订单：先卖后买
            retry_sells: list[Order] = []
            for order in pending.get(d, []):
                if order.direction == "sell":
                    h = holdings.get(order.code)
                    if h is None:
                        continue
                    if not self._is_tradable(order.code, d, "sell"):
                        retry_sells.append(order)
                        continue
                    ser = self.open_series.get(order.code)
                    if ser is None or d not in ser.index:
                        retry_sells.append(order)
                        continue
                    sell_price = float(ser.loc[d]) * (1 - self.slippage)
                    proceeds = self._sell_proceeds(h.shares, sell_price)
                    cash += proceeds
                    buy_comm = max(h.shares * h.buy_price * self.commission_rate, self.min_commission)
                    cost_basis = h.shares * h.buy_price + buy_comm
                    pnl = proceeds - cost_basis
                    days = self._holding_days(h.code, h.buy_date, d)
                    trades.append(
                        Trade(
                            code=h.code,
                            signal_type=h.signal_type,
                            buy_date=h.buy_date,
                            buy_price=h.buy_price,
                            shares=h.shares,
                            sell_date=d,
                            sell_price=sell_price,
                            sell_reason=order.reason,
                            pnl=pnl,
                            pnl_pct=(proceeds / cost_basis - 1) if cost_basis else 0.0,
                            holding_days=days,
                        )
                    )
                    del holdings[order.code]
            for order in retry_sells:
                next_d = self._next_bar_date(order.code, d)
                if next_d is not None and next_d <= self.end:
                    pending.setdefault(next_d, []).append(order)
            for order in pending.get(d, []):
                if order.direction == "buy":
                    if order.code in holdings or len(holdings) >= self._max_positions_for_date(d):
                        continue
                    if not self._index_allows_buy(d):
                        continue
                    if not self._is_tradable(order.code, d, "buy"):
                        continue
                    ser = self.open_series.get(order.code)
                    if ser is None or d not in ser.index:
                        continue
                    buy_price = float(ser.loc[d]) * (1 + self.slippage)
                    shares, cost = self._buy(cash, buy_price)
                    if shares <= 0:
                        continue
                    cash -= cost
                    stop_low = order.stop_low * (1 - self.stop_loss_pct)
                    holdings[order.code] = Holding(
                        code=order.code,
                        signal_type=order.signal_type,
                        buy_date=d,
                        buy_price=buy_price,
                        shares=shares,
                        stop_low=stop_low,
                        peak_close=buy_price,
                        stop_reason="stop_loss",
                    )
            # 2. 收盘后：止损、最大持有期检查，生成次日卖出订单
            for code in list(holdings.keys()):
                h = holdings[code]
                ser = self.close_series.get(code)
                if ser is None or d not in ser.index:
                    continue
                close_price = float(ser.loc[d])
                days = self._holding_days(code, h.buy_date, d)
                # 移动止损：先更新持仓期最高收盘价，再视情况抬升止损线
                if self.trailing_enabled:
                    if close_price > h.peak_close:
                        h.peak_close = close_price
                    if h.peak_close >= h.buy_price * (1 + self.trailing_activate):
                        trail = h.peak_close * (1 - self.trailing_pct)
                        if trail > h.stop_low:
                            h.stop_low = trail
                            h.stop_reason = "trailing_stop"
                reason = None
                if close_price <= h.stop_low:
                    reason = h.stop_reason
                elif days >= self.max_holding_days:
                    reason = "max_hold"
                if reason:
                    next_d = self._next_bar_date(code, d)
                    if next_d is not None:
                        pending.setdefault(next_d, [])
                        existing = [o for o in pending[next_d] if o.code == code and o.direction == "sell"]
                        if not existing:
                            pending[next_d].append(Order(code, "sell", h.signal_type, reason))

            # 3. 动态 exposure 减仓：下一交易日目标持仓数少于当前持仓数时，生成减仓卖出单。
            if holdings:
                next_cal = self._next_calendar_day(d)
                if next_cal is not None:
                    target_next = self._max_positions_for_date(next_cal)
                    excess = len(holdings) - target_next
                    if excess > 0:
                        oldest = sorted(holdings.values(), key=lambda h: h.buy_date)[:excess]
                        for h in oldest:
                            next_d = self._next_bar_date(h.code, d)
                            if next_d is None:
                                continue
                            existing = [o for o in pending.get(next_d, []) if o.code == h.code and o.direction == "sell"]
                            if not existing:
                                pending.setdefault(next_d, []).append(Order(h.code, "sell", h.signal_type, "exposure_reduce"))

            # 4. 记录收盘权益
            self._last_equity = self._mark_to_market(cash, holdings, d)
            equity_curve.append((d, self._last_equity))
        # 期末强制平仓（按最后收盘价）
        last_day = self.calendar[-1] if self.calendar else self.end
        for code in list(holdings.keys()):
            h = holdings[code]
            ser = self.close_series.get(code)
            if ser is not None:
                pos = ser.index.searchsorted(last_day, side="right") - 1
                if pos >= 0:
                    sell_price = float(ser.iloc[pos])
                else:
                    sell_price = h.buy_price
            else:
                sell_price = h.buy_price
            proceeds = self._sell_proceeds(h.shares, sell_price)
            cash += proceeds
            buy_comm = max(h.shares * h.buy_price * self.commission_rate, self.min_commission)
            cost_basis = h.shares * h.buy_price + buy_comm
            pnl = proceeds - cost_basis
            days = self._holding_days(code, h.buy_date, last_day)
            trades.append(
                Trade(
                    code=code,
                    signal_type=h.signal_type,
                    buy_date=h.buy_date,
                    buy_price=h.buy_price,
                    shares=h.shares,
                    sell_date=last_day,
                    sell_price=sell_price,
                    sell_reason="end",
                    pnl=pnl,
                    pnl_pct=(proceeds / cost_basis - 1) if cost_basis else 0.0,
                    holding_days=days,
                )
            )
            del holdings[code]
        self._last_equity = cash
        if self.calendar:
            equity_curve.append((self.calendar[-1], cash))

        return {
            "trades": trades,
            "equity_curve": equity_curve,
            "cash": cash,
            "initial_cash": self.initial_cash,
            "calendar": self.calendar,
        }

    def _prev_close(self, code: str, date: int) -> float | None:
        """返回 date 之前最近一个不复权收盘价（用于涨跌停判断）。"""
        ser = self.raw_close_series.get(code)
        if ser is None:
            return None
        pos = ser.index.searchsorted(date, side="right") - 2
        if pos >= 0:
            return float(ser.iloc[pos])
        return None

    def _is_tradable(self, code: str, date: int, direction: str) -> bool:
        """涨跌停/停牌限制：停牌或一字涨跌停时不可成交。

        使用不复权 OHLC 判断涨跌停，避免前复权价格造成误判。
        """
        if not self.limit_rule:
            return True
        if code not in self.raw_open_series or code not in self.raw_high_series or code not in self.raw_low_series:
            return False
        oser = self.raw_open_series[code]
        hser = self.raw_high_series[code]
        lser = self.raw_low_series[code]
        vser = self.volume_series.get(code)
        if date not in oser.index:
            return False
        o = float(oser.loc[date])
        h = float(hser.loc[date])
        l = float(lser.loc[date])
        vol = float(vser.loc[date]) if vser is not None and date in vser.index else 0.0
        if vol <= 0:
            return False  # 停牌或无成交量
        if h <= l:
            prev = self._prev_close(code, date)
            if prev and prev > 0:
                pct = o / prev - 1.0
                if code.startswith(("300", "301", "688", "689")):
                    limit_pct = 0.195
                else:
                    limit_pct = 0.095
                if direction == "buy" and pct >= limit_pct * 0.98:
                    return False  # 一字涨停买不进
                if direction == "sell" and pct <= -limit_pct * 0.98:
                    return False  # 一字跌停卖不出
        return True

    def _holding_days(self, code: str, buy_date: int, current_date: int) -> int:
        ser = self.close_series.get(code)
        if ser is None:
            return 0
        pos_cur = ser.index.searchsorted(current_date, side="right")
        pos_buy = ser.index.searchsorted(buy_date, side="right")
        return max(int(pos_cur - pos_buy), 0)
