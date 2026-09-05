"""构建 TDX 事件数据库：LHB + LimitUp + MarketSentiment 的 clean parquet。

只覆盖研究区间 2022-08-01 ~ 2025-07-31，严格不触碰 FINAL TEST。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
import pandas as pd
from chanlun_trader.config import load_config
from chanlun_trader.data.tdx import TQClient, TDXProviders
from chanlun_trader.tdx_data import TdxData
from short_horizon_lab import prep_data, TRAIN, VALIDATION

RESEARCH_START, RESEARCH_END = "20220801", "20250731"


def to_tq_code(code: str) -> str:
    if code.startswith(("6", "5", "9")):
        return code + ".SH"
    if code.startswith(("4", "8")):
        return code + ".BJ"
    return code + ".SZ"


def main():
    cfg = load_config()
    tdx = TdxData(cfg["tdx"]["vipdoc"], cfg["tdx"]["gbbq"], cfg["tdx"].get("cache_dir"))
    codes = set()
    for period in [TRAIN, VALIDATION]:
        cal, uni, union, pre = prep_data(cfg, tdx, period)
        codes.update(union)
    tq_codes = [to_tq_code(c) for c in sorted(codes)]
    print("universe codes:", len(tq_codes))
    providers = TDXProviders(TQClient())

    # 1) market sentiment
    sent = providers.sentiment.get_sentiment(RESEARCH_START, RESEARCH_END)
    print("sentiment rows:", len(sent))

    # 2) LHB events
    lhb = providers.lhb.get_events_batch(tq_codes, RESEARCH_START, RESEARCH_END)
    df_lhb = pd.DataFrame([e.__dict__ for e in lhb])
    if not df_lhb.empty:
        path = Path("data/tdx/clean/lhb_events.parquet")
        path.parent.mkdir(parents=True, exist_ok=True)
        df_lhb.to_parquet(path, index=False)
        print("lhb events:", len(df_lhb), "->", path)

    # 3) LimitUp events
    lim = providers.limit.get_events_batch(tq_codes, RESEARCH_START, RESEARCH_END)
    df_lim = pd.DataFrame([e.__dict__ for e in lim])
    if not df_lim.empty:
        path = Path("data/tdx/clean/limit_events.parquet")
        df_lim.to_parquet(path, index=False)
        print("limit events:", len(df_lim), "->", path)


if __name__ == "__main__":
    main()
