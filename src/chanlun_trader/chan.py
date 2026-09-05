"""缠论计算引擎（一期 MVP）。

规则口径：
- 日线、前复权；
- 先做 K 线包含合并，再识别顶底分型；
- 新笔：顶底分型交替，且分型中间索引差 >= bi_gap（默认 4，对应至少 1 根独立 K 线）；
- 中枢：连续三笔重叠区间（笔中枢），取 ZG/ZD；
- 背驰：比较同向相邻笔的 MACD 柱面积；
- 买卖点：一/二/三类买卖点（简化版）。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


def add_macd(df: pd.DataFrame, close_col: str = "close", fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """在 df 上追加 dif、dea、macd 三列（macd 为常见软件口径：2*(dif-dea)）。"""
    out = df.copy()
    ema_fast = out[close_col].ewm(span=fast, adjust=False).mean()
    ema_slow = out[close_col].ewm(span=slow, adjust=False).mean()
    out["dif"] = ema_fast - ema_slow
    out["dea"] = out["dif"].ewm(span=signal, adjust=False).mean()
    out["macd"] = 2 * (out["dif"] - out["dea"])
    return out


@dataclass
class FX:
    """分型。idx 为合并后 K 线索引，kind 为 top/bottom。"""

    idx: int
    kind: str
    high: float
    low: float


@dataclass
class BI:
    """笔。方向 up=底分型到顶分型，down=顶分型到底分型。"""

    idx: int
    start_fx_idx: int
    end_fx_idx: int
    direction: str
    start_date: int
    end_date: int
    confirm_date: int
    high: float
    low: float
    orig_start_idx: int
    orig_end_idx: int
    macd_area: float = 0.0


@dataclass
class Signal:
    """缠论买卖点信号。"""

    code: str
    signal_date: int
    signal_type: str
    direction: str
    bi_idx: int
    price_ref: float
    stop_low: float
    score: float = 0.0


@dataclass
class Segment:
    """简化线段：由连续笔合并而成的高一级走势。"""

    idx: int
    start_bi: int
    end_bi: int
    direction: str
    high: float
    low: float
    start_date: int
    end_date: int


def merge_containment(df: pd.DataFrame) -> pd.DataFrame:
    """K 线包含关系合并。

    返回合并后的 DataFrame：date 取合并组最后一根的日期，open/close 取组内首/尾，
    high/low 按方向规则合并，orig_idx_end 记录合并组最后一根在原始 df 中的索引。
    """
    n = len(df)
    if n == 0:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "orig_idx_end"])

    rows = df.reset_index(drop=True)
    merged: list[dict] = []
    cur = {
        "date": int(rows.iloc[0]["date"]),
        "open": float(rows.iloc[0]["open"]),
        "high": float(rows.iloc[0]["high"]),
        "low": float(rows.iloc[0]["low"]),
        "close": float(rows.iloc[0]["close"]),
        "orig_idx_end": 0,
        "orig_idx_start": 0,
    }
    merged.append(cur)
    i = 1
    while i < n:
        h1, l1 = cur["high"], cur["low"]
        h2, l2 = float(rows.iloc[i]["high"]), float(rows.iloc[i]["low"])
        contains = (h1 >= h2 and l1 <= l2) or (h2 >= h1 and l2 <= l1)
        if not contains:
            cur = {
                "date": int(rows.iloc[i]["date"]),
                "open": float(rows.iloc[i]["open"]),
                "high": h2,
                "low": l2,
                "close": float(rows.iloc[i]["close"]),
                "orig_idx_end": i,
                "orig_idx_start": i,
            }
            merged.append(cur)
            i += 1
            continue
        if len(merged) >= 2:
            prev = merged[-2]
            direction = "up" if cur["high"] > prev["high"] and cur["low"] > prev["low"] else "down"
        else:
            direction = "unknown"
        if direction == "up":
            cur["high"] = max(h1, h2)
            cur["low"] = max(l1, l2)
        elif direction == "down":
            cur["high"] = min(h1, h2)
            cur["low"] = min(l1, l2)
        else:
            cur["high"] = max(h1, h2)
            cur["low"] = min(l1, l2)
        cur["date"] = int(rows.iloc[i]["date"])
        cur["close"] = float(rows.iloc[i]["close"])
        cur["orig_idx_end"] = i
        i += 1
    return pd.DataFrame(merged)


def detect_fx(merged: pd.DataFrame) -> list[FX]:
    """识别顶底分型，只保留由后续 K 线确认的分型（i+1 < n）。"""
    n = len(merged)
    fx_list: list[FX] = []
    if n < 3:
        return fx_list
    high = merged["high"].to_numpy(dtype=float)
    low = merged["low"].to_numpy(dtype=float)
    for i in range(1, n - 1):
        if high[i] > high[i - 1] and high[i] > high[i + 1] and low[i] > low[i - 1] and low[i] > low[i + 1]:
            fx_list.append(FX(i, "top", float(high[i]), float(low[i])))
        elif low[i] < low[i - 1] and low[i] < low[i + 1] and high[i] < high[i - 1] and high[i] < high[i + 1]:
            fx_list.append(FX(i, "bottom", float(high[i]), float(low[i])))
    return fx_list


def detect_bi(merged: pd.DataFrame, fx_list: list[FX], bi_gap: int = 4) -> list[BI]:
    """从分型序列构建笔。"""
    bis: list[BI] = []
    if not fx_list:
        return bis

    def make_bi(a: FX, b: FX, idx: int) -> BI:
        direction = "up" if b.kind == "top" else "down"
        seg = merged.iloc[a.idx : b.idx + 1]
        high = float(seg["high"].max())
        low = float(seg["low"].min())
        confirm_idx = min(b.idx + 1, len(merged) - 1)
        return BI(
            idx=idx,
            start_fx_idx=a.idx,
            end_fx_idx=b.idx,
            direction=direction,
            start_date=int(merged.iloc[a.idx]["date"]),
            end_date=int(merged.iloc[b.idx]["date"]),
            confirm_date=int(merged.iloc[confirm_idx]["date"]),
            high=high,
            low=low,
            orig_start_idx=int(merged.iloc[a.idx]["orig_idx_end"]),
            orig_end_idx=int(merged.iloc[b.idx]["orig_idx_end"]),
        )

    pending = fx_list[0]
    for fx in fx_list[1:]:
        if fx.kind == pending.kind:
            if fx.kind == "top" and fx.high >= pending.high:
                pending = fx
            elif fx.kind == "bottom" and fx.low <= pending.low:
                pending = fx
            continue
        if fx.idx - pending.idx >= bi_gap:
            bis.append(make_bi(pending, fx, len(bis)))
            pending = fx
    return bis


def bi_macd_areas(bis: list[BI], macd_series: pd.Series) -> None:
    """为每笔填充 MACD 柱面积（原始 K 线 macd 列求和）。"""
    values = macd_series.to_numpy(dtype=float)
    n = len(values)
    for bi in bis:
        s = min(max(int(bi.orig_start_idx), 0), n - 1)
        e = min(max(int(bi.orig_end_idx), 0), n - 1)
        bi.macd_area = float(values[s : e + 1].sum())


def zhongshu_at(bis: list[BI], k: int) -> tuple[float, float] | None:
    """返回以第 k 笔为当前笔时，紧邻其前的三笔（k-3,k-2,k-1）构成的笔中枢 (ZD, ZG)。"""
    if k < 3:
        return None
    a, b, c = bis[k - 3], bis[k - 2], bis[k - 1]
    zg = min(a.high, b.high, c.high)
    zd = max(a.low, b.low, c.low)
    if zg > zd:
        return zd, zg
    return None


def detect_signals(
    df: pd.DataFrame,
    merged: pd.DataFrame,
    bis: list[BI],
    macd_series: pd.Series,
    segments: list[Segment] | None = None,
    filter_cfg: dict | None = None,
) -> list[Signal]:
    """在一只股票的全历史 K 线上识别一二三类买卖点。

    filter_cfg 为可选过滤条件，用于提高买点确认强度。
    """
    signals: list[Signal] = []
    b1_bi_idx: set[int] = set()
    s1_bi_idx: set[int] = set()
    b3_zs_keys: set[tuple[float, float]] = set()
    n = len(df)
    if n == 0:
        return signals
    filter_cfg = filter_cfg or {}

    close_series = df["close"]
    date_series = df["date"]

    def _bi_avg_volume(bi: BI) -> float:
        if "volume" not in df.columns:
            return 0.0
        a, b = bi.orig_start_idx, bi.orig_end_idx
        if b < a:
            return 0.0
        seg = df["volume"].iloc[a : b + 1]
        return float(seg.mean()) if len(seg) else 0.0

    def _has_recent_down_segment(k: int, lookback: int) -> bool:
        if not segments:
            return True
        return any(sg.direction == "down" and k - lookback <= sg.end_bi <= k - 1 for sg in segments)

    def _trend_ok(pos: int) -> bool:
        """均线多头过滤：收盘价 > 快线 > 慢线（用历史数据计算，不用未来）。"""
        fast = int(filter_cfg.get("trend_ma_fast", 0))
        slow = int(filter_cfg.get("trend_ma_slow", 0))
        if not fast or not slow or fast >= slow:
            return True
        if fast >= len(close_series) or slow >= len(close_series):
            return True
        ma_fast = float(close_series.rolling(fast).mean().iloc[pos])
        ma_slow = float(close_series.rolling(slow).mean().iloc[pos])
        return float(close_series.iloc[pos]) > ma_fast > ma_slow

    for k in range(len(bis)):
        bi = bis[k]
        sig_date = int(bi.confirm_date)
        pos = date_series.searchsorted(sig_date, side="right") - 1
        if pos < 0 or pos >= n:
            continue
        price_ref = float(close_series.iloc[pos])

        if bi.direction == "down":
            # 一类买点：下跌背驰，中枢下方新低
            zs = zhongshu_at(bis, k)
            if zs is not None:
                prev_down = bis[k - 2]
                zd, zg = zs
                if prev_down.direction == "down":
                    area_cur = abs(bi.macd_area)
                    area_prev = abs(prev_down.macd_area)
                    ratio_max = float(filter_cfg.get("b1_macd_ratio_max", 1.0))
                    if bi.low < zd and bi.low < prev_down.low and 0 < area_cur < area_prev * ratio_max:
                        # 提高确认强度：确认 K 线收阳（收盘价高于前一根收盘价）
                        if filter_cfg.get("b1_confirm_up") and pos > 0:
                            if close_series.iloc[pos] <= close_series.iloc[pos - 1]:
                                continue
                        # 线段方向过滤：一买前最近 N 笔内应有一个已完成的向下线段
                        lookback = int(filter_cfg.get("b1_segment_lookback", 4))
                        if filter_cfg.get("b1_require_down_segment") and not _has_recent_down_segment(k, lookback):
                            continue
                        # B1 防狼术：要求 MACD 黄白线 DIF 站上 0 轴
                        if filter_cfg.get("b1_require_dif_positive") and pos >= 0:
                            if float(df["dif"].iloc[pos]) <= 0:
                                continue
                        # 均线多头过滤（可选，对全部买点生效）
                        if not _trend_ok(pos):
                            continue
                        signals.append(Signal("", sig_date, "B1", "buy", k, price_ref, bi.low))
                        b1_bi_idx.add(k)
            # 二类买点：一买后回调不破新低
            if k >= 2 and (k - 2) in b1_bi_idx:
                prev_down = bis[k - 2]
                if bi.low > prev_down.low:
                    # 提高确认强度：回调笔不破前一笔（一买后的上升笔）中点
                    if filter_cfg.get("b2_hold_mid") and k >= 1:
                        up_bi = bis[k - 1]
                        mid = (up_bi.high + up_bi.low) / 2
                        if bi.low <= mid:
                            continue
                    if not _trend_ok(pos):
                        continue
                    signals.append(Signal("", sig_date, "B2", "buy", k, price_ref, bi.low))
            # 三类买点：突破中枢后回抽不跌回
            if k >= 4:
                up_bi = bis[k - 1]
                zs_before = zhongshu_at(bis, k - 1)
                if zs_before is not None and up_bi.direction == "up":
                    zd, zg = zs_before
                    if up_bi.high > zg and bi.low > zg:
                        # 提高确认强度：回抽低点需高于中枢上沿一定比例（默认 0，即必须高于 ZG）
                        margin = float(filter_cfg.get("b3_zg_margin", 0.0))
                        if bi.low > zg * (1 + margin):
                            # 突破笔必须放量：突破笔均量 >= 前一笔均量 * 阈值
                            breakout_ratio = float(filter_cfg.get("b3_breakout_vol_ratio", 0.0))
                            if breakout_ratio > 0 and k >= 2:
                                prev_down_bi = bis[k - 2]
                                if prev_down_bi.direction == "down":
                                    if _bi_avg_volume(up_bi) < _bi_avg_volume(prev_down_bi) * breakout_ratio:
                                        continue
                            # 回抽笔缩量：回抽笔均量 <= 突破笔均量 * 阈值（阈值 < 1 表示缩量）
                            pullback_ratio = float(filter_cfg.get("b3_pullback_vol_ratio", 0.0))
                            if pullback_ratio > 0:
                                if _bi_avg_volume(bi) > _bi_avg_volume(up_bi) * pullback_ratio:
                                    continue
                            # 防狼术（第 103 课）：三买要求 MACD 黄白线 DIF 站上 0 轴
                            if filter_cfg.get("b3_require_dif_positive") and pos >= 0:
                                if float(df["dif"].iloc[pos]) <= 0:
                                    continue
                            # 第三类买点必须是第一次回试：同一中枢只允许第一个成功 B3
                            if filter_cfg.get("b3_first_pullback"):
                                zs_key = (round(zg, 4), round(zd, 4))
                                if zs_key in b3_zs_keys:
                                    continue
                                b3_zs_keys.add(zs_key)
                            if not _trend_ok(pos):
                                continue
                            signals.append(Signal("", sig_date, "B3", "buy", k, price_ref, bi.low))
        else:
            # 一类卖点：上涨背驰，中枢上方新高
            zs = zhongshu_at(bis, k)
            if zs is not None:
                prev_up = bis[k - 2]
                zd, zg = zs
                if prev_up.direction == "up":
                    area_cur = abs(bi.macd_area)
                    area_prev = abs(prev_up.macd_area)
                    if bi.high > zg and bi.high > prev_up.high and 0 < area_cur < area_prev:
                        signals.append(Signal("", sig_date, "S1", "sell", k, price_ref, bi.high))
                        s1_bi_idx.add(k)
            # 二类卖点：一卖后反弹不创新高
            if k >= 2 and (k - 2) in s1_bi_idx:
                prev_up = bis[k - 2]
                if bi.high < prev_up.high:
                    signals.append(Signal("", sig_date, "S2", "sell", k, price_ref, bi.high))
            # 三类卖点：跌破中枢后回抽不升回
            if k >= 4:
                down_bi = bis[k - 1]
                zs_before = zhongshu_at(bis, k - 1)
                if zs_before is not None and down_bi.direction == "down":
                    zd, zg = zs_before
                    if down_bi.low < zd and bi.high < zd:
                        signals.append(Signal("", sig_date, "S3", "sell", k, price_ref, bi.high))
    return signals


def analyze_stock(
    df: pd.DataFrame,
    code: str = "",
    bi_gap: int = 4,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
    filter_cfg: dict | None = None,
):
    """一键计算一只股票的 MACD、合并 K 线、笔、线段、信号。"""
    df = df.copy()
    # 信号计算统一使用不复权价格，避免全样本前复权把未来除权信息带入指标。
    close_col = "close"
    df = add_macd(df, close_col, fast, slow, signal)
    merged = merge_containment(df)
    fx_list = detect_fx(merged)
    bis = detect_bi(merged, fx_list, bi_gap)
    macd_series = df["macd"]
    bi_macd_areas(bis, macd_series)
    segments = detect_segments(bis)
    signals = detect_signals(df, merged, bis, macd_series, segments=segments, filter_cfg=filter_cfg)
    for s in signals:
        s.code = code
    return df, merged, bis, signals, segments


def detect_segments(bis: list[BI]) -> list[Segment]:
    """简化线段识别（二期，用于高一级走势辅助判断）。

    规则：以第一笔方向作为首段方向；上涨段中，若某回调笔低点跌破前一个回调笔
    低点，则上涨段在前一上升笔结束；下跌段对称。至少三笔才构成一段。
    """
    if len(bis) < 3:
        return []

    def make_seg(start: int, end: int, direction: str, idx: int) -> Segment:
        seg_bis = bis[start : end + 1]
        high = max(b.high for b in seg_bis)
        low = min(b.low for b in seg_bis)
        return Segment(
            idx=idx,
            start_bi=start,
            end_bi=end,
            direction=direction,
            high=high,
            low=low,
            start_date=bis[start].start_date,
            end_date=bis[end].end_date,
        )

    segs: list[Segment] = []
    direction = bis[0].direction
    start = 0
    last_pullback_low = None
    last_rally_high = None
    i = 1
    while i < len(bis):
        if direction == "up":
            if bis[i].direction == "down":
                if last_pullback_low is None:
                    last_pullback_low = bis[i].low
                elif bis[i].low < last_pullback_low:
                    end = i - 1
                    if end - start + 1 >= 3:
                        segs.append(make_seg(start, end, "up", len(segs)))
                    start = end
                    direction = "down"
                    last_pullback_low = None
                    last_rally_high = bis[i].high
                    continue
                else:
                    last_pullback_low = bis[i].low
        else:
            if bis[i].direction == "up":
                if last_rally_high is None:
                    last_rally_high = bis[i].high
                elif bis[i].high > last_rally_high:
                    end = i - 1
                    if end - start + 1 >= 3:
                        segs.append(make_seg(start, end, "down", len(segs)))
                    start = end
                    direction = "up"
                    last_rally_high = None
                    last_pullback_low = bis[i].low
                    continue
                else:
                    last_rally_high = bis[i].high
        i += 1
    if len(bis) - 1 - start + 1 >= 3:
        segs.append(make_seg(start, len(bis) - 1, direction, len(segs)))
    return segs


def score_signals(
    df: pd.DataFrame,
    signals: list[Signal],
    bis: list[BI],
    *,
    score_cfg: dict | None = None,
) -> list[Signal]:
    """给买点信号打分，用于按日截取强度最高的信号。

    权重口径：
    - 类型基础分：B1=3.0、B2=2.0、B3=1.0（一买 > 二买 > 三买）；
    - 一买背驰强度：按 `1 - 当前笔MACD面积/前一笔MACD面积` 线性映射到 0~2 分；
    - 距中枢位置：一买看跌破 ZD 的深度、二买看回踩 ZD 上方的距离、三买看回抽距 ZG 的距离，各映射到 0~1 分；
    - 二、三买重合（最强二买）：二买低点同时高于 ZG 时加分；
    - 三买突破量能：突破笔均量相对前一笔均量越大，加分越多；
    - 成交额：按 20 日均成交额相对 5 亿元封顶，映射到 0~0.5 分。
    """
    score_cfg = score_cfg or {}
    type_base = score_cfg.get("type_base", {"B1": 3.0, "B2": 2.0, "B3": 1.0})
    amount_cap = float(score_cfg.get("amount_cap", 5e8))

    if "amount_ma20" not in df.columns:
        df["amount_ma20"] = 0.0

    def _avg_vol(bi: BI) -> float:
        if "volume" not in df.columns:
            return 0.0
        a, b = bi.orig_start_idx, bi.orig_end_idx
        if b < a:
            return 0.0
        seg = df["volume"].iloc[a : b + 1]
        return float(seg.mean()) if len(seg) else 0.0
    date_to_amount = dict(zip(df["date"], df["amount_ma20"]))
    date_to_pos = {int(d): i for i, d in enumerate(df["date"])}

    for s in signals:
        if s.direction != "buy":
            continue
        k = s.bi_idx
        if k < 0 or k >= len(bis):
            continue
        bi = bis[k]
        score = float(type_base.get(s.signal_type, 0.0))

        # 一买背驰强度
        if s.signal_type == "B1" and k >= 2:
            prev_down = bis[k - 2]
            if prev_down.direction == "down" and abs(prev_down.macd_area) > 0:
                ratio = abs(bi.macd_area) / abs(prev_down.macd_area)
                score += max(0.0, min(1.0, 1.0 - ratio)) * float(score_cfg.get("divergence_weight", 2.0))

        # 距中枢位置
        zs = zhongshu_at(bis, k)
        if zs is not None:
            zd, zg = zs
            if s.signal_type == "B1" and bi.low < zd:
                score += min(1.0, (zd - bi.low) / zd) * float(score_cfg.get("zs_weight", 1.0))
            elif s.signal_type == "B2" and bi.low > zd:
                score += min(1.0, (bi.low - zd) / zd) * float(score_cfg.get("zs_weight", 1.0))
                # 最强二买：二、三类买点重合（回调不破 ZG）
                if bi.low > zg:
                    score += float(score_cfg.get("b2_b3_merge_bonus", 1.0))
            elif s.signal_type == "B3" and bi.low > zg:
                score += min(1.0, (bi.low - zg) / zg) * float(score_cfg.get("zs_weight", 1.0))
                # 三买突破量能加分
                if k >= 2:
                    up_bi = bis[k - 1]
                    prev_down_bi = bis[k - 2]
                    if up_bi.direction == "up" and prev_down_bi.direction == "down" and _avg_vol(prev_down_bi) > 0:
                        vol_ratio = _avg_vol(up_bi) / _avg_vol(prev_down_bi)
                        score += min(1.0, vol_ratio / 2.0) * float(score_cfg.get("b3_vol_strength_weight", 0.5))

        # 成交额
        pos = date_to_pos.get(int(s.signal_date))
        if pos is None and int(s.signal_date) in date_to_pos:
            pos = date_to_pos[int(s.signal_date)]
        amount = date_to_amount.get(int(s.signal_date), 0.0)
        if pos is None and s.signal_date in date_to_amount:
            amount = date_to_amount[s.signal_date]
        if amount > 0:
            score += min(1.0, amount / amount_cap) * float(score_cfg.get("amount_weight", 0.5))

        s.score = round(score, 4)
    return signals


def top_per_day(
    signals: list[Signal],
    limit: int,
    *,
    industry_map: dict[str, str] | None = None,
    max_per_industry: int = 0,
) -> list[Signal]:
    """按交易日分组，每天只保留分数最高的前 limit 个买点信号。

    industry_map 为 {股票代码: 行业代码}；max_per_industry > 0 时，每天同一行业
    最多保留该数量的信号（行业去重 / 同板块拥挤度控制）。
    """
    if limit is None or limit <= 0:
        if not industry_map or max_per_industry <= 0:
            return signals
        limit = 0  # 仅做行业去重，不限制每日总数
    by_date: dict[int, list[Signal]] = {}
    for s in signals:
        if s.direction == "buy":
            by_date.setdefault(int(s.signal_date), []).append(s)
    picked: list[Signal] = []
    for _date, items in sorted(by_date.items()):
        items.sort(key=lambda x: x.score, reverse=True)
        used_industry: dict[str, int] = {}
        count = 0
        for s in items:
            industry = industry_map.get(s.code, "") if industry_map else ""
            if max_per_industry > 0:
                if used_industry.get(industry, 0) >= max_per_industry:
                    continue
            if limit > 0 and count >= limit:
                continue
            picked.append(s)
            count += 1
            if industry:
                used_industry[industry] = used_industry.get(industry, 0) + 1
    picked.sort(key=lambda x: (int(x.signal_date), -x.score))
    return picked
