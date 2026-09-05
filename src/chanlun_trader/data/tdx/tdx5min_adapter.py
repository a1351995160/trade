"""TDX 5 分钟线本地 Adapter。

真实 .lc5 文件在 vipdoc/{sh,sz}/fzline/ 下，格式 <HHfffffII（float 价格）。
本 Adapter 负责：
- 符号扫描与覆盖审计
- 读取 .lc5 并转换为 Asia/Shanghai tz-aware Timestamp 索引
- 5m -> Daily 聚合与 .day 日线对账
- 日期硬锁：研究侧使用 ResearchDataAccessGuard，禁止 >= 2025-08-01。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from chanlun_trader.tdx_data import read_lc5_file
from chanlun_trader.research.io_safety import read_lc5_file_range, _lc5_date_at, LC5_RECORD_SIZE
from chanlun_trader.engine.time_types import tz_aware
from chanlun_trader.research.guard import ResearchDataAccessGuard


@dataclass
class FiveMinCoverage:
    symbol: str
    earliest_date: int
    latest_date: int
    bars: int
    missing_dates: int


RESEARCH_END_5M = 2025_07_31


class TDX5MinAdapter:
    """本地 5 分钟 K 线适配器（真实 TDX 文件，非合成）。"""

    def __init__(self, vipdoc: str | Path, guard: ResearchDataAccessGuard | None = None):
        self.vipdoc = Path(vipdoc)
        self.guard = guard or ResearchDataAccessGuard()
        self._cache: dict[str, pd.DataFrame] = {}

    def path_for(self, symbol: str) -> Path:
        """symbol: 600000.SH / 000001.SZ。"""
        code, mkt = symbol.split(".")
        prefix = "sh" if mkt == "SH" else "sz"
        return self.vipdoc / prefix / "fzline" / f"{prefix}{code}.lc5"

    def exists(self, symbol: str) -> bool:
        return self.path_for(symbol).exists()

    def available_symbols(self, limit: int = 0) -> list[str]:
        out = []
        for prefix, suffix in (("sh", "SH"), ("sz", "SZ")):
            d = self.vipdoc / prefix / "fzline"
            if not d.exists():
                continue
            for f in sorted(d.glob(f"{prefix}*.lc5")):
                code = f.name[2:8]
                if not code.isdigit():
                    continue
                # A 股股票：sh6 / sz0 / sz3
                if prefix == "sh" and not code.startswith("6"):
                    continue
                if prefix == "sz" and not code.startswith(("0", "3")):
                    continue
                out.append(f"{code}.{suffix}")
        return out[:limit] if limit else out

    def read(self, symbol: str, start_date: int = 0, end_date: int = RESEARCH_END_5M) -> pd.DataFrame:
        """安全读取单只 5m K 线：只 materialize [start_date, end_date] 的 K 线记录。"""
        if symbol in self._cache:
            return self._cache[symbol]
        path = self.path_for(symbol)
        if not path.exists():
            return pd.DataFrame()
        self.guard.check_range(start_date, end_date, f"5m {symbol}")
        df = read_lc5_file_range(str(path), start_date=start_date, end_date=end_date, guard=self.guard)
        if df.empty:
            self._cache[symbol] = df
            return df
        df = df.copy()
        # 组装 Asia/Shanghai 时间戳
        df["ts"] = [
            tz_aware(
                int(d) // 10000,
                (int(d) // 100) % 100,
                int(d) % 100,
                int(m) // 100,
                int(m) % 100,
            )
            for d, m in zip(df["date"], df["minute"])
        ]
        df = df.drop(columns=["date", "minute"]).set_index("ts").sort_index()
        # 去重（保留最后一条）
        df = df[~df.index.duplicated(keep="last")]
        self._cache[symbol] = df
        return df

    def read_range(self, symbol: str, start_date: int = 0, end_date: int = 99_999_999) -> pd.DataFrame:
        self.guard.check_range(start_date, end_date, f"5m {symbol}")
        df = self.read(symbol)
        if df.empty:
            return df
        dkey = df.index.strftime("%Y%m%d").astype(int)
        return df[(dkey >= start_date) & (dkey <= end_date)]

    def coverage(self, symbol: str) -> FiveMinCoverage | None:
        """安全覆盖审计：只读首条记录日期，绝不读取未来 K 线。"""
        path = self.path_for(symbol)
        if not path.exists():
            return None
        try:
            first_date = _lc5_date_at(str(path), 0)
        except Exception:
            return None
        return FiveMinCoverage(
            symbol=symbol,
            earliest_date=first_date,
            latest_date=None,
            bars=0,
            missing_dates=0,
        )

    def aggregate_daily(self, symbol: str, start_date: int = 0, end_date: int = 99_999_999) -> pd.DataFrame:
        """5m -> Daily 聚合。"""
        df = self.read_range(symbol, start_date, end_date)
        if df.empty:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "amount"])
        dkey = df.index.strftime("%Y%m%d").astype(int)
        return (
            df.groupby(dkey)
            .agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                 close=("close", "last"), volume=("volume", "sum"), amount=("amount", "sum"))
            .reset_index()
            .rename(columns={"index": "date"})
        )

    def reconcile(self, symbol: str, daily_df: pd.DataFrame, date: int) -> dict:
        """5m 聚合与 .day 日线对账（单日）。"""
        agg = self.aggregate_daily(symbol, date, date)
        row = daily_df[daily_df["date"] == date]
        if agg.empty or row.empty:
            return {"symbol": symbol, "date": date, "ok": False, "reason": "missing side", "agg": None, "day": None}
        a = agg.iloc[0]
        r = row.iloc[0]
        tol = 0.02
        ok = (
            abs(a["open"] - r["open"]) <= tol
            and abs(a["high"] - r["high"]) <= tol
            and abs(a["low"] - r["low"]) <= tol
            and abs(a["close"] - r["close"]) <= tol
        )
        vol_ratio = a["volume"] / r["volume"] if r["volume"] else 0
        amt_ratio = a["amount"] / r["amount"] if r["amount"] else 0
        return {
            "symbol": symbol, "date": int(date), "ok": ok,
            "reason": "" if ok else "price mismatch",
            "agg": a.to_dict(), "day": r.to_dict(),
            "volume_ratio": float(vol_ratio), "amount_ratio": float(amt_ratio),
        }
