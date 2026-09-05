"""自定义策略回测运行器。

复用冻结版 BacktestRunner 的成交循环（T+1、涨跌停/停牌、费用滑点、止损/移动止损/最大持有），
但允许外部传入任意策略信号，跳过缠论计算，用于 Broad Search。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .backtest import BacktestRunner, Signal
from .tdx_data import TdxData, list_a_stocks


class CustomBacktestRunner(BacktestRunner):
    """外部信号 + 冻结成交引擎。

    signals: list[Signal]，direction 为 buy/sell；stop_low 为买入后固定止损参考价。
    universe_codes: 参与 PIT 股票池排序的代码集合，默认全市场。
    """

    def __init__(self, tdx: TdxData, cfg: dict, signals: list[Signal], universe_codes: list[str] | None = None):
        super().__init__(tdx, cfg)
        self.custom_signals = signals
        self.universe_codes = universe_codes
        self.tie_seed = int(cfg.get("backtest", {}).get("random_tie_break_seed", 42))
        self._raw_day_cache: dict[str, pd.DataFrame] = {}
        self._amount_ma20_series: dict[str, pd.Series] = {}

    def _load_raw_day(self, code: str, market: int) -> pd.DataFrame:
        key = code
        if key not in self._raw_day_cache:
            df = self.tdx.get_day(code, market)
            if not df.empty:
                df = df.sort_values("date").reset_index(drop=True)
            self._raw_day_cache[key] = df
        return self._raw_day_cache[key]

    def prepare(self, limit: int | None = None, progress_cb=None) -> None:
        codes = self.universe_codes
        if codes is None:
            stocks = list_a_stocks(self.tdx.vipdoc)
            codes = [s["code"] for s in stocks]

        # 交易日历
        bench = self.tdx.get_benchmark("sh000001")
        if bench is not None and len(bench):
            b = bench.sort_values("date").reset_index(drop=True)
            self.calendar = [int(d) for d in b["date"] if self.start <= int(d) <= self.end]
            self.index_close = pd.Series(b["close"].to_numpy(), index=b["date"].astype(np.int64))
            self.index_ma = pd.Series(
                b["close"].rolling(self.index_ma_period).mean().to_numpy(),
                index=b["date"].astype(np.int64),
            )
        else:
            self.calendar = []
            self.index_close = pd.Series(dtype=float)
            self.index_ma = pd.Series(dtype=float)

        # Point-In-Time 股票池：优先使用调用方传入的预计算 Universe，否则按日滚动 amount_lookback 成交额排名。
        precomputed = self.cfg.get("__universe_sets__")
        if precomputed is not None and self.amount_top_n > 0:
            self.liquid_codes_by_date = {int(d): set(v) for d, v in precomputed.items()}
        elif self.amount_top_n > 0:
            amount_trail: dict[str, pd.Series] = {}
            for code in codes:
                stock = next((s for s in list_a_stocks(self.tdx.vipdoc) if s["code"] == code), None)
                if stock is None:
                    continue
                raw = self._load_raw_day(code, stock["market"])
                if len(raw) < self.min_list_days:
                    continue
                dates = raw["date"].astype(np.int64)
                amount_trail[code] = pd.Series(
                    raw["amount"].rolling(self.amount_lookback).mean().to_numpy(), index=dates
                )
            if amount_trail:
                amt = pd.DataFrame(amount_trail).sort_index()
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

        code_market: dict[str, int] = {s["code"]: s["market"] for s in list_a_stocks(self.tdx.vipdoc)}

        # 先加载信号涉及的 qfq 行情（_next_bar_date 依赖 open_series）
        all_signal_codes = {s.code for s in self.custom_signals if s.code in code_market}
        for code in all_signal_codes:
            market = code_market[code]
            df = self.tdx.get_qfq_day(code, market)
            if df.empty:
                continue
            df = df.sort_values("date").reset_index(drop=True)
            df = df[df["date"] <= self.end].reset_index(drop=True)
            if df.empty:
                continue
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

        # 过滤信号
        keep: list[Signal] = []
        for s in self.custom_signals:
            if s.signal_date < self.start or s.signal_date > self.end:
                continue
            if s.code not in code_market:
                continue
            if s.direction == "buy":
                if self.amount_top_n > 0 and s.code not in self.liquid_codes_by_date.get(int(s.signal_date), set()):
                    continue
                if self.min_amount_ma20 > 0:
                    avg = self._amount_ma20_at(s.code, code_market[s.code], s.signal_date)
                    if avg is None or avg < self.min_amount_ma20:
                        continue
            exec_date = self._next_bar_date(s.code, s.signal_date)
            if exec_date is None or exec_date > self.end:
                continue
            s.score = float(getattr(s, "score", 0.0))
            keep.append(s)

        import random as _random

        def _tie_key(x: Signal) -> float:
            return _random.Random(f"{self.tie_seed}:{x.code}:{x.signal_date}").random()

        keep.sort(key=lambda x: (int(x.signal_date), -float(getattr(x, "score", 0.0)), _tie_key(x)))
        self.signals = [
            {
                "signal_date": s.signal_date,
                "exec_date": self._next_bar_date(s.code, s.signal_date),
                "code": s.code,
                "direction": s.direction,
                "signal_type": s.signal_type,
                "price_ref": s.price_ref,
                "stop_low": s.stop_low,
                "score": float(getattr(s, "score", 0.0)),
            }
            for s in keep
        ]

    def _amount_ma20_at(self, code: str, market: int, date: int) -> float | None:
        if code not in self._amount_ma20_series:
            raw = self._load_raw_day(code, market)
            if raw.empty:
                self._amount_ma20_series[code] = pd.Series(dtype=float)
            else:
                dates = raw["date"].astype(np.int64)
                self._amount_ma20_series[code] = pd.Series(
                    raw["amount"].rolling(20).mean().to_numpy(), index=dates
                )
        ser = self._amount_ma20_series.get(code)
        if ser is None or len(ser) == 0:
            return None
        pos = ser.index.searchsorted(date, side="right") - 1
        if pos < 0:
            return None
        val = ser.iloc[pos]
        return None if (isinstance(val, float) and np.isnan(val)) else float(val)
