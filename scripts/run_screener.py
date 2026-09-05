"""运行选股扫描，输出当前全市场买点信号。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd

from chanlun_trader.config import load_config
from chanlun_trader.screener import scan_all
from chanlun_trader.tdx_data import TdxData


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    cfg = load_config()
    tdx = TdxData(cfg["tdx"]["vipdoc"], cfg["tdx"]["gbbq"], cfg["tdx"].get("cache_dir"))
    limit = None
    if len(sys.argv) > 1:
        limit = int(sys.argv[1])
    df = scan_all(tdx, cfg, limit=limit)
    out_dir = Path(cfg["report"]["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "signals_latest.csv"
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"扫描完成，共 {len(df)} 个买点信号，输出：{out}")
    if not df.empty:
        print(df.groupby("signal_type").size().to_string())


if __name__ == "__main__":
    main()
