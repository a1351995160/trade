"""选股模块：扫描股票池，输出缠论买点信号。"""
from __future__ import annotations

import pandas as pd

from .chan import analyze_stock, score_signals, top_per_day
from .industry import eligible_codes, load_industry_map
from .tdx_data import TdxData, list_a_stocks, rank_stocks_by_amount


def build_universe(tdx: TdxData, min_list_days: int = 120, stocks: list[dict] | None = None) -> list[dict]:
    """返回满足最少上市天数的股票列表（只做基本过滤，ST/流动性在信号时点过滤）。"""
    if stocks is None:
        stocks = list_a_stocks(tdx.vipdoc)
    result = []
    for st in stocks:
        try:
            df = tdx.get_qfq_day(st["code"], st["market"])
        except Exception:
            continue
        if len(df) >= min_list_days:
            result.append(st)
    return result


def scan_stock(tdx: TdxData, stock: dict, cfg: dict) -> list[dict]:
    """扫描单只股票，返回买点信号列表（dict 形式）。"""
    code = stock["code"]
    market = stock["market"]
    try:
        df = tdx.get_qfq_day(code, market)
    except Exception:
        return []
    if len(df) < cfg["universe"].get("min_list_days", 120):
        return []
    chan_cfg = cfg.get("chan", {})
    df["amount_ma20"] = df["amount"].rolling(20).mean()
    _, _, bis, signals, _ = analyze_stock(
        df,
        code=code,
        bi_gap=chan_cfg.get("bi_gap", 4),
        fast=chan_cfg.get("macd_fast", 12),
        slow=chan_cfg.get("macd_slow", 26),
        signal=chan_cfg.get("macd_signal", 9),
        filter_cfg=cfg.get("signal_filter"),
    )
    signal_cfg = cfg.get("signal_filter", {})
    signals = score_signals(df, signals, bis, score_cfg=signal_cfg.get("score_cfg"))
    # 流动性过滤：20 日均成交额
    min_amount = cfg["universe"].get("min_amount_ma20", 0)
    amount_map = dict(zip(df["date"], df["amount_ma20"])) if min_amount and min_amount > 0 else {}
    out = []
    for s in signals:
        if s.direction != "buy":
            continue
        if amount_map:
            avg = amount_map.get(s.signal_date, None)
            if avg is None or avg < min_amount:
                continue
        out.append(
            {
                "code": code,
                "market": market,
                "signal_date": s.signal_date,
                "signal_type": s.signal_type,
                "price_ref": s.price_ref,
                "stop_low": s.stop_low,
                "score": s.score,
            }
        )
    return out


def scan_all(tdx: TdxData, cfg: dict, limit: int | None = None, progress_cb=None) -> pd.DataFrame:
    """扫描全市场，返回买点信号 DataFrame。limit 用于测试时只扫前 N 只。

    先汇总全部信号，再按“每日前 N + 行业去重”截取。
    progress_cb 为可选回调，签名为 progress_cb(done: int, total: int)。
    """
    stocks = list_a_stocks(tdx.vipdoc)
    eligible = eligible_codes(cfg.get("tdx", {}))
    if eligible is not None:
        stocks = [st for st in stocks if st["code"] in eligible]
    uni = cfg.get("universe", {})
    top_n = int(uni.get("amount_top_n", 0))
    if top_n > 0:
        stocks = rank_stocks_by_amount(
            stocks, top_n, int(uni.get("amount_lookback", 250)), str(uni.get("amount_rank_mode", "initial"))
        )
    stocks = build_universe(tdx, cfg["universe"].get("min_list_days", 120), stocks)
    total = min(len(stocks), limit or len(stocks))
    if limit:
        stocks = stocks[:limit]
    rows = []
    for i, st in enumerate(stocks):
        sigs = scan_stock(tdx, st, cfg)
        rows.extend(sigs)
        if progress_cb:
            progress_cb(i + 1, total)
    if not rows:
        return pd.DataFrame()
    signal_cfg = cfg.get("signal_filter", {})
    industry_map = load_industry_map(cfg.get("tdx", {}))
    rows = _top_rows_per_day(
        rows,
        int(signal_cfg.get("max_picks_per_day", 0)),
        industry_map,
        int(signal_cfg.get("max_per_industry_per_day", 0)),
    )
    return pd.DataFrame(rows)


def _top_rows_per_day(rows: list[dict], limit: int, industry_map: dict[str, str], max_per_industry: int) -> list[dict]:
    """对 dict 形式的买点信号做每日前 N + 行业去重。"""
    if limit <= 0 and max_per_industry <= 0:
        return sorted(rows, key=lambda r: (r["signal_date"], -r["score"]))
    by_date: dict[int, list[dict]] = {}
    for r in rows:
        by_date.setdefault(int(r["signal_date"]), []).append(r)
    picked: list[dict] = []
    for _date, items in sorted(by_date.items()):
        items.sort(key=lambda r: r["score"], reverse=True)
        used: dict[str, int] = {}
        count = 0
        for r in items:
            industry = industry_map.get(str(r["code"]), "")
            if max_per_industry > 0 and used.get(industry, 0) >= max_per_industry:
                continue
            if limit > 0 and count >= limit:
                continue
            picked.append(r)
            count += 1
            if industry:
                used[industry] = used.get(industry, 0) + 1
    picked.sort(key=lambda r: (r["signal_date"], -r["score"]))
    return picked
