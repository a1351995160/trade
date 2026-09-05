"""M0 DATA FOUNDATION — 数据能力审计、Final Test Guard、QFQ PIT Safety、5m Adapter。"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

import pandas as pd
import json

from chanlun_trader.config import load_config
from chanlun_trader.tdx_data import TdxData, list_a_stocks, read_day_file
from chanlun_trader.data.tdx.tdx5min_adapter import TDX5MinAdapter
from chanlun_trader.research.capability import build_local_mock_registry
from chanlun_trader.research.guard import ResearchDataAccessGuard, FinalTestAccessViolation
from chanlun_trader.research.qfq import qfq_pit_acceptance


def main() -> None:
    cfg = load_config()
    vipdoc = cfg["tdx"]["vipdoc"]
    gbbq_path = cfg["tdx"]["gbbq"]
    guard = ResearchDataAccessGuard()

    # 1) Final Test Guard self test
    guard_ok = True
    try:
        guard.check_date(2025_08_01)
        guard_ok = False
    except FinalTestAccessViolation:
        pass

    # 2) 5m adapter audit
    t0 = time.time()
    adapter = TDX5MinAdapter(vipdoc, guard=guard)
    syms_5m = adapter.available_symbols()
    has_5m = len(syms_5m) > 0
    cov_5m = None
    if has_5m:
        for s in syms_5m[:20]:
            c = adapter.coverage(s)
            if c is not None and c.bars > 0:
                cov_5m = c
                break
    t_5m = time.time() - t0

    # 3) QFQ PIT acceptance（抽 20 只有最多除权事件的股票）
    tdx = TdxData(vipdoc, gbbq_path, cache_dir=cfg["tdx"].get("cache_dir"))
    gbbq = tdx._load_gbbq()
    stocks = list_a_stocks(vipdoc)
    cm = {s["code"]: s["market"] for s in stocks}
    code_counts = gbbq.groupby("code")["datetime"].nunique().sort_values(ascending=False)
    qfq_ok = qfq_fail = 0
    qfq_fail_details = []
    t0 = time.time()
    for code in code_counts.index[:20]:
        mkt = cm.get(code)
        if mkt is None:
            continue
        raw = tdx.get_day(code, mkt)
        if raw.empty or len(raw) < 50:
            continue
        dates = raw["date"].tolist()
        t1 = dates[len(dates) // 2]
        t2 = dates[-1]
        res = qfq_pit_acceptance(tdx, code, mkt, t1, t2)
        if res.ok:
            qfq_ok += 1
        else:
            qfq_fail += 1
            qfq_fail_details.append({"code": code, "reason": res.reason})
    t_qfq = time.time() - t0

    # 4) Registry
    reg = build_local_mock_registry(vipdoc, gbbq_path, has_5m=has_5m)
    reg_path = reg.save()

    # 5) Daily coverage sample
    daily_cap = reg.get("daily_ohlcva_raw")
    n_stocks = len(stocks)
    idx = tdx.get_benchmark("sh000300")
    idx_rows = 0 if idx is None else len(idx)
    idx_min = 0 if idx is None or idx.empty else int(idx["date"].min())
    idx_max = 0 if idx is None or idx.empty else int(idx["date"].max())

    result = {
        "M0": "DATA_FOUNDATION",
        "FINAL_TEST_GUARD_OK": guard_ok,
        "RESEARCH_END": 2025_07_31,
        "REAL_TDX_5M_ADAPTER": "READY" if has_5m else "WAITING",
        "SYMBOLS_5M": len(syms_5m),
        "COVERAGE_5M": None if cov_5m is None else {
            "symbol": cov_5m.symbol, "earliest_date": cov_5m.earliest_date,
            "latest_date": cov_5m.latest_date, "bars": cov_5m.bars,
        },
        "QFQ_PIT_ACCEPTANCE": {"ok": qfq_ok, "fail": qfq_fail, "fail_details": qfq_fail_details,
                               "elapsed_sec": round(t_qfq, 2)},
        "DAILY": {
            "symbols": n_stocks,
            "earliest_date": daily_cap.earliest_date,
            "latest_date": daily_cap.latest_date,
            "benchmark": {"rows": idx_rows, "min": idx_min, "max": idx_max},
        },
        "REGISTRY_PATH": str(reg_path),
        "ELAPSED_5M_SCAN_SEC": round(t_5m, 2),
    }

    out = Path("reports/M0_DATA_FOUNDATION.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    # 6) Markdown registry
    md = ["# DATA CAPABILITY REGISTRY", "",
          f"- Generated: {pd.Timestamp.now()}", f"- RESEARCH_END: 2025-07-31 (FINAL TEST SEALED)",
          f"- REAL_TDX_5M_ADAPTER: {result['REAL_TDX_5M_ADAPTER']}", ""]
    md.append("| dataset_id | status | freq | earliest | latest | PIT_safe | event_time | available_at | fields |")
    md.append("|---|---|---|---|---|---|---|---|---|")
    for c in reg.items():
        md.append(f"| {c.dataset_id} | {c.status} | {c.frequency} | {c.earliest_date} | {c.latest_date} | {c.PIT_safe} | {c.event_time_semantics} | {c.available_at_semantics} | {','.join(c.fields[:6])} |")
    md.append("")
    md.append("## 5-minute")
    if has_5m:
        md.append(f"- symbols={len(syms_5m)}, sample={result['COVERAGE_5M']}")
    else:
        md.append("- WAITING_FOR_DATA")
    md.append("")
    md.append("## QFQ PIT Acceptance")
    md.append(f"- ok={qfq_ok}, fail={qfq_fail}")
    if qfq_fail_details:
        md.append(f"- failures={qfq_fail_details}")
    md.append("")
    md.append("## Rules")
    md.append("- 研究代码禁止读取 >= 2025-08-01 的数据（FinalTestAccessViolation）。")
    md.append("- 正式研究必须使用 qfq_columns_asof(..., as_of=decision_date)，禁止全样本 get_qfq_day。")
    md.append("- status 非 READY 的数据集不得进入正式研究。")
    Path("docs/DATA_CAPABILITY_REGISTRY.md").write_text("\n".join(md), encoding="utf-8")

    print("M0_DATA_FOUNDATION_RESULT")
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    print("M0_PASS" if guard_ok and qfq_ok >= 5 and daily_cap.status == "READY" else "M0_FAIL")


if __name__ == "__main__":
    main()
