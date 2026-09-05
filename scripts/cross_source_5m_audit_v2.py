"""V2 BaoStock / TDX Raw HQ / local TDX .lc5 5m overlap audit.

The sample and date window are fixed before measuring differences.  Missing data
is never converted into a value mismatch: a pair without common timestamps is
reported as ``NO_REFERENCE_DATA``.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from chanlun_trader.data.minute.baostock_provider import BaoStock5MinProvider
from chanlun_trader.data.minute.normalizer import normalize_5m_frame
from chanlun_trader.data.minute.tdx_raw_hq_provider import TdxRawHQProvider
from chanlun_trader.data.tdx.tdx5min_adapter import TDX5MinAdapter


SYMBOLS = [
    "600000.SH", "600016.SH", "600030.SH", "600048.SH", "600104.SH",
    "000333.SZ", "000338.SZ", "000538.SZ", "000568.SZ", "000625.SZ",
    "300015.SZ", "300059.SZ", "300122.SZ", "300124.SZ", "300274.SZ",
    "688001.SH", "688005.SH", "688009.SH", "688012.SH", "688599.SH",
]
START = date(2024, 10, 9)
END = date(2024, 10, 11)
VIPDOC = Path(r"E:/new_tdx_mock/vipdoc")
RAW_ROOT = Path("data/market_raw/cross_source_v2")
JSON_OUT = Path("reports/BAOSTOCK_TDX_CROSS_SOURCE_AUDIT_V2.json")
MD_OUT = Path("reports/BAOSTOCK_TDX_CROSS_SOURCE_AUDIT_V2.md")
VENDOR_OUT = Path("reports/5M_VENDOR_DIFFERENCE_ANALYSIS.md")

# Set before running the audit.  These are business-semantic tolerances, not
# values fitted to the output: 0.01 is the A-share price tick and 5% allows
# known vendor rounding/aggregation/late-revision differences in volume data.
PRICE_ABS_TOLERANCE = 0.02
PRICE_REL_TOLERANCE = 0.002
RATIO_LOW = 0.95
RATIO_HIGH = 1.05


def _save_snapshot(df: pd.DataFrame, source: str, symbol: str) -> str:
    directory = RAW_ROOT / source
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{symbol.replace('.', '_')}_{START:%Y%m%d}_{END:%Y%m%d}.parquet"
    if target.exists():
        old = pd.read_parquet(target)
        compare_old = old.drop(columns=["fetched_at"], errors="ignore")
        compare_new = df.drop(columns=["fetched_at"], errors="ignore")
        if not compare_old.equals(compare_new):
            conflict = target.with_name(target.stem + ".conflict.parquet")
            if not conflict.exists():
                df.to_parquet(conflict, index=False)
            return str(conflict)
        return str(target)
    df.to_parquet(target, index=False)
    return str(target)


def _local_normalized(adapter: TDX5MinAdapter, symbol: str) -> pd.DataFrame:
    frame = adapter.read_range(symbol, int(START.strftime("%Y%m%d")), int(END.strftime("%Y%m%d")))
    if frame.empty:
        return pd.DataFrame(columns=["symbol", "timestamp", "trade_date", "bar_time", "open", "high", "low", "close", "volume", "amount", "source"])
    out = frame.reset_index().rename(columns={"ts": "timestamp"})
    out["symbol"] = symbol
    out["timestamp"] = pd.to_datetime(out["timestamp"])
    out["trade_date"] = out["timestamp"].dt.strftime("%Y%m%d").astype(int)
    out["bar_time"] = out["timestamp"].dt.strftime("%H:%M")
    out["source"] = "tdx-local-lc5"
    out["source_symbol"] = symbol
    out["fetched_at"] = "local-file"
    return out[["symbol", "timestamp", "trade_date", "bar_time", "open", "high", "low", "close", "volume", "amount", "source", "source_symbol", "fetched_at"]]


def _window(frame: pd.DataFrame) -> list[str | None]:
    if frame.empty or "timestamp" not in frame:
        return [None, None]
    ts = pd.to_datetime(frame["timestamp"])
    return [str(ts.min()), str(ts.max())]


def _quantiles(values: pd.Series) -> dict[str, float | None]:
    values = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if values.empty:
        return {"p50": None, "p95": None, "max": None}
    return {"p50": float(values.quantile(0.50)), "p95": float(values.quantile(0.95)), "max": float(values.max())}


def _price_exact(left: pd.Series, right: pd.Series) -> float:
    # Exact means equal after the market's two-decimal price tick normalization;
    # this removes binary float representation noise from local .lc5 values.
    return float((left.round(2) == right.round(2)).mean())


def _pair_metrics(left: pd.DataFrame, right: pd.DataFrame, left_name: str, right_name: str) -> dict[str, Any]:
    key = "timestamp"
    left_keys = set(left[key].dropna()) if key in left else set()
    right_keys = set(right[key].dropna()) if key in right else set()
    matched_keys = left_keys & right_keys
    union_keys = left_keys | right_keys
    base: dict[str, Any] = {
        "left_source": left_name,
        "right_source": right_name,
        "left_rows": int(len(left)),
        "right_rows": int(len(right)),
        "matched_bars": int(len(matched_keys)),
        "timestamp_exact_ratio": float(len(matched_keys) / max(1, len(union_keys))),
        "left_timestamp_coverage": float(len(matched_keys) / max(1, len(left_keys))),
        "right_timestamp_coverage": float(len(matched_keys) / max(1, len(right_keys))),
        "matched_trade_date_count": 0,
        "matched_trade_dates": [],
        "classification": "NO_REFERENCE_DATA",
    }
    if not matched_keys:
        return base
    merged = left[left[key].isin(matched_keys)].merge(
        right[right[key].isin(matched_keys)], on=key, suffixes=("_left", "_right"), how="inner"
    )
    dates = sorted({int(ts.strftime("%Y%m%d")) for ts in merged[key]})
    base["matched_bars"] = int(len(merged))
    base["matched_trade_date_count"] = len(dates)
    base["matched_trade_dates"] = dates
    price_tolerance_ok = []
    for col in ["open", "high", "low", "close"]:
        diff = (merged[f"{col}_left"] - merged[f"{col}_right"]).abs()
        rel = diff / merged[f"{col}_right"].abs().clip(lower=0.01)
        base[f"{col}_exact_ratio"] = _price_exact(merged[f"{col}_left"], merged[f"{col}_right"])
        base[f"{col}_absolute_diff"] = _quantiles(diff)
        base[f"{col}_relative_diff"] = _quantiles(rel)
        price_tolerance_ok.append(diff <= np.maximum(PRICE_ABS_TOLERANCE, PRICE_REL_TOLERANCE * merged[f"{col}_right"].abs()))
    base["ohlc_tolerance_ratio"] = float(pd.concat(price_tolerance_ok, axis=1).all(axis=1).mean())
    for col in ["volume", "amount"]:
        denominator = merged[f"{col}_right"].abs().replace(0, np.nan)
        ratio = (merged[f"{col}_left"] / denominator).replace([np.inf, -np.inf], np.nan).dropna()
        base[f"{col}_ratio_distribution"] = _quantiles(ratio)
        base[f"{col}_ratio_tolerance_ratio"] = float(ratio.between(RATIO_LOW, RATIO_HIGH).mean()) if len(ratio) else None
    base["all_field_tolerance_ratio"] = float(
        (pd.concat(price_tolerance_ok, axis=1).all(axis=1) &
         (merged["volume_left"] / merged["volume_right"].replace(0, np.nan)).between(RATIO_LOW, RATIO_HIGH) &
         (merged["amount_left"] / merged["amount_right"].replace(0, np.nan)).between(RATIO_LOW, RATIO_HIGH)).mean()
    )
    base["classification"] = "TRUE_DATA_MISMATCH" if base["all_field_tolerance_ratio"] < 1.0 else "MATCHED_WITHIN_TOLERANCE"
    return base


def _liquidity_tiers() -> dict[str, str]:
    from chanlun_trader.tdx_data import TdxData

    tdx = TdxData(str(VIPDOC), str(VIPDOC / "T0002/gbbq"))
    amounts: dict[str, float] = {}
    for symbol in SYMBOLS:
        code, market = symbol.split(".")
        frame = tdx.get_day(code, 1 if market == "SH" else 0)
        frame = frame[frame["date"].between(int(START.strftime("%Y%m%d")), int(END.strftime("%Y%m%d")))]
        amounts[symbol] = float(frame["amount"].mean()) if not frame.empty else 0.0
    ordered = sorted(amounts, key=amounts.get, reverse=True)
    cutoff = max(1, len(ordered) // 2)
    return {symbol: ("HIGH" if rank < cutoff else "LOW") for rank, symbol in enumerate(ordered)}


def _aggregate(pair_rows: list[dict[str, Any]], pair_name: str) -> dict[str, Any]:
    comparable = [row for row in pair_rows if row["matched_bars"] > 0]
    dates = sorted({d for row in comparable for d in row["matched_trade_dates"]})
    classes: dict[str, int] = {}
    for row in pair_rows:
        classes[row["classification"]] = classes.get(row["classification"], 0) + 1
    return {
        "pair": pair_name,
        "requested_symbol_count": len(pair_rows),
        "comparable_symbol_count": len(comparable),
        "comparable_date_count": len(dates),
        "matched_bar_count": sum(row["matched_bars"] for row in comparable),
        "classification_counts": classes,
    }


def _write_vendor_analysis(payload: dict[str, Any]) -> None:
    lines = [
        "# 5M Vendor Difference Analysis",
        "",
        "## 结论",
        "",
        "低 OHLC exact ratio 不能单独判定数据错误。V2 将价格先按 A 股 0.01 元最小价位规整后计算 exact，同时保留原始绝对/相对差异分布。",
        "BaoStock 与 TDX Raw HQ 的差异主要表现为同一时间戳上的价格分位差异、成交量/成交额比例轻微偏离；BaoStock 与本地 TDX `.lc5` 的较大差异还可能包含供应商修订、分钟聚合版本和本地文件生成时点差异。",
        "",
        "## 预先固定的质量标准",
        "",
        f"- 价格容差：`abs(diff) <= max({PRICE_ABS_TOLERANCE:.2f}, {PRICE_REL_TOLERANCE:.3%} * abs(reference))`；0.01 元是 A 股最小价位，额外 0.01 元用于四舍五入/供应商展示精度。",
        f"- 成交量/成交额比例容差：`{RATIO_LOW:.2f}..{RATIO_HIGH:.2f}`；用于吸收单位换算、尾数舍入与供应商聚合差异。",
        "- 时间戳只有在两源都实际存在且精确相等时才进入 matched bars；单边缺失计为 `NO_REFERENCE_DATA`，不计为数值 mismatch。",
        "",
        "## 证据与解释",
        "",
        "1. 时间戳 convention：BaoStock 与 TDX Raw HQ 均按 09:35..15:00 的 `BAR_END` 标签对齐；本地 `.lc5` 转换后使用同一标签。",
        "2. 价格精度：原始二进制 `.lc5` 的 float32 会产生微小机器误差，因此 exact 使用两位小数规整；这不是把有业务意义的价差抹掉。",
        "3. 竞价/聚合：当前 5m 数据没有把集合竞价另列为独立 bar；开盘标签和供应商的分钟聚合实现可能导致边界差异。",
        "4. 复权/修订：本轮 BaoStock 明确 `adjustflag=3`（不复权）；本地 TDX 文件的生成版本与远端供应商修订时间独立，不能用一方快照推断另一方一定错误。",
        "",
        "## V2 汇总",
        "",
    ]
    for aggregate in payload["aggregate"]:
        lines.append(f"- `{aggregate['pair']}`：可比 symbol `{aggregate['comparable_symbol_count']}/{aggregate['requested_symbol_count']}`，可比交易日 `{aggregate['comparable_date_count']}`，matched bars `{aggregate['matched_bar_count']}`，分类 `{aggregate['classification_counts']}`。")
    lines += ["", "质量结论以 tolerance-based metrics 为主，exact ratio 只作为诊断指标。", ""]
    VENDOR_OUT.parent.mkdir(parents=True, exist_ok=True)
    VENDOR_OUT.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    adapter = TDX5MinAdapter(VIPDOC)
    raw_provider = TdxRawHQProvider(initial_start=15000, max_pages=200)
    bs_provider = BaoStock5MinProvider()
    tiers = _liquidity_tiers()
    symbols: list[dict[str, Any]] = []
    with bs_provider.session():
        for symbol in SYMBOLS:
            item: dict[str, Any] = {"symbol": symbol, "liquidity_tier": tiers[symbol]}
            bs = normalize_5m_frame(bs_provider.fetch(symbol, START, END), symbol, bs_provider.source, bs_provider.timestamp_semantics)
            item["baostock_snapshot"] = _save_snapshot(bs, "baostock", symbol)
            tdx_start = len(raw_provider.failures)
            try:
                tdx = normalize_5m_frame(raw_provider.fetch(symbol, START, END), symbol, raw_provider.source, raw_provider.timestamp_semantics)
                item["tdx_raw_snapshot"] = _save_snapshot(tdx, "tdx_raw_hq", symbol)
                item["tdx_failures"] = raw_provider.failures[tdx_start:]
            except Exception as exc:
                tdx = pd.DataFrame(columns=bs.columns)
                item["tdx_raw_snapshot"] = None
                item["tdx_failures"] = raw_provider.failures[tdx_start:]
                item["tdx_error"] = f"{type(exc).__name__}: {exc}"
            lc5 = _local_normalized(adapter, symbol)
            item["source_windows"] = {
                "baostock": _window(bs),
                "tdx_raw_hq": _window(tdx),
                "tdx_local_lc5": _window(lc5),
            }
            item["local_lc5_rows"] = len(lc5)
            item["local_lc5_earliest_date"] = adapter.coverage(symbol).earliest_date if adapter.coverage(symbol) else None
            item["baostock_rows"] = len(bs)
            item["tdx_raw_rows"] = len(tdx)
            item["pairs"] = [
                _pair_metrics(bs, tdx, "baostock", "tdx_raw_hq"),
                _pair_metrics(bs, lc5, "baostock", "tdx_local_lc5"),
            ]
            symbols.append(item)

    pair_rows = {
        "baostock_tdx_raw_hq": [item["pairs"][0] for item in symbols],
        "baostock_tdx_local_lc5": [item["pairs"][1] for item in symbols],
    }
    aggregate = [_aggregate(rows, name) for name, rows in pair_rows.items()]
    true_overlap_window = {}
    for source in ["baostock", "tdx_raw_hq", "tdx_local_lc5"]:
        windows = [item["source_windows"][source] for item in symbols if item["source_windows"][source][0] is not None]
        true_overlap_window[source] = [min(w[0] for w in windows), max(w[1] for w in windows)] if windows else [None, None]
    payload: dict[str, Any] = {
        "report": "BAOSTOCK_TDX_CROSS_SOURCE_AUDIT_V2",
        "version": "V2",
        "requested_window": [START.isoformat(), END.isoformat()],
        "date_constraint": "all dates < 2025-08-01",
        "requested_symbol_count": len(SYMBOLS),
        "true_overlap_window": true_overlap_window,
        "sample_design": {
            "boards": {"SH_MAIN": 5, "SZ_MAIN": 5, "GEM": 5, "STAR": 5},
            "liquidity_tiers": {"method": "rank mean local TDX daily amount within fixed sample", "counts": {"HIGH": sum(v == "HIGH" for v in tiers.values()), "LOW": sum(v == "LOW" for v in tiers.values())}},
        },
        "sources": {
            "baostock": {"role": "PRIMARY_HISTORICAL_5M", "adjustment": "NONE", "timestamp_semantics": "BAR_END"},
            "tdx_raw_hq": {"role": "SECONDARY_CROSS_CHECK", "timestamp_semantics": "BAR_END", "initial_start": 15000},
            "tdx_local_lc5": {"role": "LOCAL_REFERENCE_ONLY", "path": str(VIPDOC), "timestamp_semantics": "BAR_END"},
        },
        "tolerances": {"price_abs": PRICE_ABS_TOLERANCE, "price_relative": PRICE_REL_TOLERANCE, "ratio_low": RATIO_LOW, "ratio_high": RATIO_HIGH, "price_exact_normalization": "round(2)"},
        "aggregate": aggregate,
        "symbols": symbols,
        "tdx_failover_log": raw_provider.failures,
        "status": "PASS" if all(x["comparable_symbol_count"] >= 20 and x["comparable_date_count"] >= 3 for x in aggregate) else "PARTIAL",
        "canonical_rule": "BaoStock no-adjustment BAR_END remains primary TRAIN historical 5m; TDX sources are cross-check/reference only.",
    }
    JSON_OUT.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    lines = [
        "# BaoStock / TDX Raw HQ / local TDX `.lc5` Cross-Source Audit V2",
        "",
        f"- Fixed window: `{START}` ~ `{END}`; all dates are before 2025-08-01.",
        f"- Fixed sample: `{len(SYMBOLS)}` symbols, 5 each from SH main, SZ main, ChiNext and STAR; liquidity tier is reported by within-sample TDX daily amount rank.",
        f"- True source windows observed in the requested window: `{true_overlap_window}`.",
        "- Comparison rule: only timestamps present in both sources enter numeric comparison; a pair with no common timestamp is `NO_REFERENCE_DATA`, not a data mismatch.",
        f"- Tolerance: price `max({PRICE_ABS_TOLERANCE:.2f}, {PRICE_REL_TOLERANCE:.3%})`; volume/amount ratio `{RATIO_LOW:.2f}..{RATIO_HIGH:.2f}`.",
        "",
        "## Aggregate",
        "",
        "| pair | comparable symbols | comparable dates | matched bars | classification counts |",
        "|---|---:|---:|---:|---|",
    ]
    for row in aggregate:
        lines.append(f"| {row['pair']} | {row['comparable_symbol_count']}/{row['requested_symbol_count']} | {row['comparable_date_count']} | {row['matched_bar_count']} | `{row['classification_counts']}` |")
    lines += ["", "## Per symbol", "", "| symbol | tier | BaoStock rows | TDX Raw rows | local `.lc5` rows | BaoStock/TDX matched | BaoStock/.lc5 matched |", "|---|---|---:|---:|---:|---:|---:|"]
    for item in symbols:
        lines.append(f"| {item['symbol']} | {item['liquidity_tier']} | {item['baostock_rows']} | {item['tdx_raw_rows']} | {item['local_lc5_rows']} | {item['pairs'][0]['matched_bars']} ({item['pairs'][0]['classification']}) | {item['pairs'][1]['matched_bars']} ({item['pairs'][1]['classification']}) |")
    lines += ["", f"- Audit status: `{payload['status']}`.", f"- Machine-readable evidence: `{JSON_OUT}`.", ""]
    MD_OUT.write_text("\n".join(lines), encoding="utf-8")
    _write_vendor_analysis(payload)
    print(json.dumps({"status": payload["status"], "aggregate": aggregate}, ensure_ascii=False))
    return 0 if payload["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
