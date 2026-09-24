"""通达信本地数据读取与复权。

一期只读日线（.day）和除权除息文件（gbbq）。
"""
from __future__ import annotations

import os
import struct
from pathlib import Path

import numpy as np
import pandas as pd

# .day 每条记录 32 字节：日期、开、高、低、收（均为 uint32，价格单位：分）
# 成交额 float32，成交量 uint32，保留 4 字节
_DAY_STRUCT = struct.Struct("<IIIIIfI4s")


def read_day_file(path: str | os.PathLike) -> pd.DataFrame:
    """读取通达信 .day 日线文件，返回标准 OHLCV DataFrame。"""
    path = str(path)
    with open(path, "rb") as f:
        content = f.read()
    n = len(content) // 32
    if n == 0:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "amount"])
    recs = np.frombuffer(content[: n * 32], dtype=np.uint8).reshape(n, 32)
    date = recs[:, 0:4].copy().view(np.uint32).reshape(-1)
    open_ = recs[:, 4:8].copy().view(np.uint32).reshape(-1)
    high = recs[:, 8:12].copy().view(np.uint32).reshape(-1)
    low = recs[:, 12:16].copy().view(np.uint32).reshape(-1)
    close = recs[:, 16:20].copy().view(np.uint32).reshape(-1)
    amount = recs[:, 20:24].copy().view(np.float32).reshape(-1)
    volume = recs[:, 24:28].copy().view(np.uint32).reshape(-1)
    df = pd.DataFrame(
        {
            "date": date.astype(np.int64),
            "open": open_.astype(np.float64) / 100.0,
            "high": high.astype(np.float64) / 100.0,
            "low": low.astype(np.float64) / 100.0,
            "close": close.astype(np.float64) / 100.0,
            "volume": volume.astype(np.int64),
            "amount": amount.astype(np.float64),
        }
    )
    df = df.sort_values("date").reset_index(drop=True)
    return df


def rank_stocks_by_amount(
    stocks: list[dict],
    top_n: int,
    lookback: int = 250,
    mode: str = "initial",
    min_first_date: int = 0,
) -> list[dict]:
    """按日线 amount 字段排名，返回成交额前 top_n 的股票。

    mode="initial" 使用每只股票文件前 lookback 条（用于无前视偏差的历史回测），
    mode="recent" 使用最后 lookback 条（用于当前实盘选股）。
    min_first_date > 0 时，跳过首根 K 线日期不早于该日期的股票，避免把回测起点之后才上市的股票选入股票池。
    """
    if top_n <= 0 or not stocks:
        return stocks
    ranked: list[tuple[float, dict]] = []
    for st in stocks:
        try:
            data = open(st["path"], "rb").read()
        except OSError:
            continue
        n = len(data) // 32
        if n <= 0:
            continue
        if min_first_date:
            first_date = struct.unpack_from("<I", data, 0)[0]
            if first_date >= min_first_date:
                continue
        cnt = min(n, lookback)
        start = (n - cnt) if mode == "recent" else 0
        total = 0.0
        for j in range(cnt):
            total += struct.unpack_from("<f", data, (start + j) * 32 + 20)[0]
        ranked.append((total / cnt, st))
    ranked.sort(key=lambda x: x[0], reverse=True)
    return [st for _, st in ranked[:top_n]]


def list_a_stocks(vipdoc: str | os.PathLike) -> list[dict]:
    """扫描 vipdoc 下的日线文件，返回沪深 A 股列表（不含指数、B 股、基金）。"""
    vipdoc = Path(vipdoc)
    stocks = []
    for market_dir, market in (("sh", 1), ("sz", 0)):
        lday = vipdoc / market_dir / "lday"
        if not lday.exists():
            continue
        for f in sorted(lday.glob("*.day")):
            code = f.name[2:8]
            if not code.isdigit():
                continue
            # 沪市 A 股：6 开头（排除 688/689 科创板）；深市 A 股：000/001/002/003 或 300/301 开头
            if market == 1 and code.startswith("6") and not code.startswith(("688", "689")):
                stocks.append({"code": code, "market": market, "path": str(f)})
            elif market == 0 and (
                code.startswith(("000", "001", "002", "003", "300", "301"))
            ):
                stocks.append({"code": code, "market": market, "path": str(f)})
    return stocks


def _resolve_cache_dir(cache_dir: str | None) -> Path | None:
    """在输入边界把缓存目录解析为**绝对路径**（唯一身份）。

    相对路径按项目组合根解释（与守卫的锚定基准一致），因此守卫、``exists``
    与实际读取消费的是同一个对象，不会出现"守卫按组合根、读取按 cwd"的双重解释。
    """
    if not cache_dir:
        return None
    from chanlun_trader.price_only_scope import resolve_input_path

    return resolve_input_path(cache_dir)


class TdxData:
    """通达信本地数据源：日线读取 + 前复权。"""

    def __init__(self, vipdoc: str, gbbq_path: str, cache_dir: str | None = None):
        self.vipdoc = Path(vipdoc)
        self.gbbq_path = gbbq_path
        # 缓存目录在**输入边界**解析一次：守卫、exists 与实际读取必须消费
        # 同一个已解析绝对对象。若保留相对形式，守卫会按组合根锚定，
        # 而 pd.read_csv 会按 cwd 打开 —— 同一哨兵被两种身份解释，
        # 形成"守卫拒绝 A、真实读取 B"的绕过（已复现）。
        self.cache_dir = _resolve_cache_dir(cache_dir)
        self._gbbq_df: pd.DataFrame | None = None
        self._day_cache: dict[str, pd.DataFrame] = {}
        self._qfq_cache: dict[str, pd.DataFrame] = {}

    def _load_gbbq(self) -> pd.DataFrame:
        # 本任务作用域激活时禁止打开真实 gbbq 原件与全量缓存：
        # 在**任何 open 之前**拒绝。守卫覆盖直接 _load_gbbq 与经 TdxData 构造
        # 的间接调用；作用域未激活时不限制（既有语义不被永久改变）。
        #
        # 关键：守卫、exists 与读取消费**同一个已解析绝对对象**。
        # 原件路径同样在输入边界解析一次，避免"guard A 再 read B"。
        from .price_only_scope import (
            assert_gbbq_read_disabled,
            controlled_gbbq_reader,
            guard_gbbq_path,
            resolve_input_path,
            task_scope_active,
        )

        gbbq_file = resolve_input_path(self.gbbq_path)
        cache_file = (self.cache_dir / "gbbq.csv") if self.cache_dir else None

        if task_scope_active():
            assert_gbbq_read_disabled()
            guard_gbbq_path(gbbq_file, label="TdxData.gbbq_path")
            if cache_file is not None:
                guard_gbbq_path(cache_file, label="TdxData.gbbq_cache")
        if self._gbbq_df is not None:
            return self._gbbq_df
        if cache_file is not None and cache_file.exists():
            # 读取消费与守卫相同的已解析对象
            self._gbbq_df = pd.read_csv(cache_file, dtype={"code": str})
        else:
            # 直接 vendor 调用必须经受控 wrapper：本任务禁止绕过同一政策。
            self._gbbq_df = controlled_gbbq_reader(gbbq_file)
            if cache_file is not None:
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                self._gbbq_df.to_csv(cache_file, index=False)
        return self._gbbq_df

    def get_day(self, code: str, market: int,
                start_date: int = 0, end_date: int | None = None) -> pd.DataFrame:
        """读取不复权日线，按代码缓存。

        `end_date` 给出时使用**有界读取**（复用 research.io_safety 的
        `read_day_file_range`），只物化窗口内记录，不把窗口外 OHLC 载入内存；
        此路径**不写入**全量缓存，避免把窗口切片当全量。
        未给出 `end_date` 时保持既有整体读取与缓存行为（向后兼容）。
        """
        if end_date is not None:
            from chanlun_trader.research.io_safety import read_day_file_range
            path = self.vipdoc / ("sh" if market == 1 else "sz") / "lday" / \
                f"{'sh' if market == 1 else 'sz'}{code}.day"
            if not path.exists():
                return pd.DataFrame(columns=["date", "open", "high", "low", "close",
                                             "volume", "amount"])
            return read_day_file_range(str(path), int(start_date), int(end_date))
        key = f"{market}_{code}"
        if key not in self._day_cache:
            path = self.vipdoc / ("sh" if market == 1 else "sz") / "lday" / f"{'sh' if market==1 else 'sz'}{code}.day"
            self._day_cache[key] = read_day_file(path)
        return self._day_cache[key]

    def get_qfq_day(self, code: str, market: int,
                    start_date: int = 0, end_date: int | None = None) -> pd.DataFrame:
        """读取前复权日线。返回 DataFrame 含 qfq_open/qfq_high/qfq_low/qfq_close。

        `end_date` 给出时使用**有界路径**：
          - RAW 通过 `get_day(..., end_date=...)` 只物化窗口内记录；
          - **只应用 ex_date <= end_date 的公司行动**，因此不会用窗口之后才发生的
            除权回改窗口内价格；
          - **不写入** `_qfq_cache` 全量缓存。

        语义差异提示：无界路径是**全样本 qfq**，有界路径是**窗口末封口 qfq**；
        两者在窗口内**价格水平**可能相差一个常数因子（比率/收益率不变），
        该差异未做前缀不变性认证，不得声称二者等价。
        """
        from chanlun_trader.research.guard import (UnsafeLegacyQfqAccessError,
                                                    is_research_context)
        if is_research_context():
            raise UnsafeLegacyQfqAccessError(
                "get_qfq_day is legacy full-sample qfq; use get_qfq_day_pit(as_of=...)"
            )
        bounded = end_date is not None
        key = f"{market}_{code}"
        if not bounded and key in self._qfq_cache:
            return self._qfq_cache[key]
        raw = self.get_day(code, market, start_date=start_date, end_date=end_date)
        if raw.empty:
            if not bounded:
                self._qfq_cache[key] = raw.copy()
            return raw.copy()
        gbbq = self._load_gbbq()
        events = gbbq[(gbbq["code"] == code) & (gbbq["category"] == 1)].sort_values("datetime")
        if bounded:
            events = events[events["datetime"].astype("int64") <= int(end_date)]
        df = raw.copy()
        df["qfq_open"] = df["open"]
        df["qfq_high"] = df["high"]
        df["qfq_low"] = df["low"]
        df["qfq_close"] = df["close"]
        if not events.empty:
            for _, ev in events.iterrows():
                d = int(ev["datetime"])
                prev = df[df["date"] < d]
                if prev.empty:
                    continue
                pc = float(prev.iloc[-1]["close"])
                if pc <= 0:
                    continue
                hongli = float(ev["hongli_panqianliutong"]) / 10.0  # 每股现金红利
                peigujia = float(ev["peigujia_qianzongguben"])  # 配股价（元/股）
                songgu = float(ev["songgu_qianzongguben"]) / 10.0  # 每股送转
                peigu = float(ev["peigu_houzongguben"]) / 10.0  # 每股配股
                adj = (pc - hongli + peigujia * peigu) / (pc * (1 + songgu + peigu))
                if adj <= 0:
                    continue
                mask = df["date"] < d
                for col in ("qfq_open", "qfq_high", "qfq_low", "qfq_close"):
                    df.loc[mask, col] = df.loc[mask, col] * adj
        if not bounded:
            self._qfq_cache[key] = df
        return df

    def get_qfq_day_pit(self, code: str, market: int, as_of: int) -> pd.DataFrame:
        """PIT-safe 前复权日线：只使用 ex_date <= as_of 的除权事件。

        与 get_qfq_day（全样本 qfq）不同，此方法保证 as_of 之后发生的
        Corporate Action 不会改变 as_of 之前的价格。研究用途默认使用本方法。
        """
        from chanlun_trader.research.qfq import qfq_columns_asof

        raw = self.get_day(code, market)
        if raw.empty:
            return raw.copy()
        gbbq = self._load_gbbq()
        g = gbbq[gbbq["code"] == code].copy()
        return qfq_columns_asof(raw, g, as_of)

    def get_benchmark(self, code: str = "sh000300",
                      start_date: int = 0, end_date: int | None = None) -> pd.DataFrame | None:
        """读取指数日线作为基准（不复权，指数无除权问题）。

        `end_date` 给出时使用**有界读取**，只物化窗口内记录，
        不把窗口外 OHLC 载入内存。未给出时保持既有整体读取行为。
        """
        # code 形如 sh000300 / sz399006
        market_dir, code_part = code[:2], code[2:]
        path = self.vipdoc / market_dir / "lday" / f"{code}.day"
        if not path.exists():
            return None
        if end_date is not None:
            from chanlun_trader.research.io_safety import read_day_file_range
            return read_day_file_range(str(path), int(start_date), int(end_date))
        return read_day_file(path)


def read_lc5_file(path: str | os.PathLike) -> pd.DataFrame:
    """读取通达信 .lc5（5 分钟线）文件。

    返回 DataFrame：date(YYYYMMDD)、minute(HHMM)、open/high/low/close、amount、volume。
    文件格式与 pytdx TdxMinBarReader 一致：<HHfffffII。
    open/high/low/close/amount 均为 float32，volume 为 uint32。
    """
    path = str(path)
    with open(path, "rb") as f:
        content = f.read()
    record_size = 32
    n = len(content) // record_size
    if n == 0:
        return pd.DataFrame(columns=["date", "minute", "open", "high", "low", "close", "amount", "volume"])
    recs = np.frombuffer(content[: n * record_size], dtype=np.uint8).reshape(n, record_size)
    date_code = recs[:, 0:2].copy().view(np.uint16).reshape(-1)
    minute_code = recs[:, 2:4].copy().view(np.uint16).reshape(-1)
    open_ = recs[:, 4:8].copy().view(np.float32).reshape(-1)
    high = recs[:, 8:12].copy().view(np.float32).reshape(-1)
    low = recs[:, 12:16].copy().view(np.float32).reshape(-1)
    close = recs[:, 16:20].copy().view(np.float32).reshape(-1)
    amount = recs[:, 20:24].copy().view(np.float32).reshape(-1)
    volume = recs[:, 24:28].copy().view(np.uint32).reshape(-1)

    def _parse_date(code: int) -> int:
        year = code // 2048 + 2004
        month = (code % 2048) // 100
        day = (code % 2048) % 100
        return year * 10000 + month * 100 + day

    def _parse_time(code: int) -> int:
        return (code // 60) * 100 + (code % 60)

    df = pd.DataFrame(
        {
            "date": np.array([_parse_date(int(c)) for c in date_code], dtype=np.int64),
            "minute": np.array([_parse_time(int(c)) for c in minute_code], dtype=np.int64),
            "open": open_.astype(np.float64),
            "high": high.astype(np.float64),
            "low": low.astype(np.float64),
            "close": close.astype(np.float64),
            "amount": amount.astype(np.float64),
            "volume": volume.astype(np.int64),
        }
    )
    return df.sort_values(["date", "minute"]).reset_index(drop=True)


def aggregate_minutes(df: pd.DataFrame, period: int = 30) -> pd.DataFrame:
    """把分钟线聚合成 N 分钟线。

    bucket = minute // period，同一 bucket 内开=首、高=max、低=min、收=尾，量额求和。
    """
    if df.empty:
        return df.copy()
    out = df.copy()
    # minute 形如 HHMM，先转成当日分钟序数（0 表示 00:00）
    out["minutes_of_day"] = (out["minute"] // 100) * 60 + (out["minute"] % 100)
    out["bucket"] = out["minutes_of_day"] // period
    agg = (
        out.groupby(["date", "bucket"], sort=True)
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
            amount=("amount", "sum"),
        )
        .reset_index()
    )
    start_min = agg["bucket"] * period
    agg["minute"] = (start_min // 60) * 100 + (start_min % 60)
    agg = agg.drop(columns=["bucket"])
    return agg
