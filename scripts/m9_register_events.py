"""Register M6 event definitions and store from clean TQ parquet."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "src")

import pandas as pd

from chanlun_trader.research.event import EventDefinition, EventRegistry, EventStore
from chanlun_trader.research.guard import ResearchDataAccessGuard

def main():
    reg = EventRegistry(Path("data/research/event_registry/registry.json"))
    store = EventStore(Path("data/research/event_store"))
    guard = ResearchDataAccessGuard()
    defs = [
        ("E_LHB_NETPOS", "龙虎榜净买入为正", "LHB", "lhb_events.net_amount>0"),
        ("E_LHB_INSTPOS", "龙虎榜机构净买入为正", "LHB", "inst_buy-inst_sell>0"),
        ("E_LHB_BROKER", "龙虎榜券商净买入为正", "LHB", "broker_buy-broker_sell>0"),
        ("E_LHB_HGT", "龙虎榜港股通净买入为正", "LHB", "hgt_buy-hgt_sell>0"),
        ("E_LHB_REPEAT", "20日内重复上龙虎榜", "LHB", "event_date - prev_event_date <= 20"),
        ("E_LIMITUP", "涨停事件", "LimitUp", "status==1"),
        ("E_FAILEDLIMIT", "炸板事件", "LimitUp", "status==2"),
        ("E_LIMITUP_SEAL", "涨停且封单金额高于中位数", "LimitUp", "status==1 & max_seal_amount>median"),
        ("E_LIMITUP_OPENCNT", "涨停且开板次数>0", "LimitUp", "status==1 & open_count>0"),
        ("E_CONSEC_LIMIT", "5日内二次涨停", "LimitUp", "event_date - prev <= 5"),
        ("E_LIMITUP_SENT", "高情绪日涨停", "LimitUp", "limit_up_pct>0.7"),
    ]
    lhb = pd.read_parquet("data/tdx/clean/lhb_events.parquet")
    lim = pd.read_parquet("data/tdx/clean/limit_events.parquet")
    lhb_sorted = lhb.sort_values(["code", "event_date"])
    lhb_sorted["prev_date"] = lhb_sorted.groupby("code")["event_date"].shift(1)
    lim_sorted = lim.sort_values(["code", "event_date"])
    lim_sorted["prev_date"] = lim_sorted.groupby("code")["event_date"].shift(1)
    sent = pd.read_parquet("data/tdx/clean/market_sentiment.parquet")
    sent_piv = sent[sent["pos"] == 0].pivot(index="date", columns="semantic", values="value")
    sent_piv["limit_up_pct"] = sent_piv["limit_up"].rank(pct=True)
    lim = lim.merge(sent_piv[["limit_up_pct"]], left_on="event_date", right_index=True, how="left")

    frames = {
        "E_LHB_NETPOS": lhb[lhb["net_amount"] > 0],
        "E_LHB_INSTPOS": lhb[(lhb["inst_buy_amount"] - lhb["inst_sell_amount"]) > 0],
        "E_LHB_BROKER": lhb[(lhb["broker_buy_amount"] - lhb["broker_sell_amount"]) > 0],
        "E_LHB_HGT": lhb[(lhb["hgt_buy_amount"] - lhb["hgt_sell_amount"]) > 0],
        "E_LHB_REPEAT": lhb_sorted[lhb_sorted["event_date"] - lhb_sorted["prev_date"] <= 20],
        "E_LIMITUP": lim[lim["status"] == 1],
        "E_FAILEDLIMIT": lim[lim["status"] == 2],
        "E_LIMITUP_SEAL": lim[(lim["status"] == 1) & (lim["max_seal_amount"] > lim[lim["status"] == 1]["max_seal_amount"].median())],
        "E_LIMITUP_OPENCNT": lim[(lim["status"] == 1) & (lim["open_count"] > 0)],
        "E_CONSEC_LIMIT": lim_sorted[(lim_sorted["status"] == 1) & (lim_sorted["event_date"] - lim_sorted["prev_date"] <= 5)],
        "E_LIMITUP_SENT": lim[(lim["status"] == 1) & (lim["limit_up_pct"] > 0.7)],
    }
    for eid, desc, family, formula in defs:
        reg.register(EventDefinition(
            event_id=eid, version="v1", name=eid, family=family, description=desc,
            event_time_semantics="TRADE_DATE", available_at_semantics="T_CLOSE",
            PIT_safe=True, inputs=["tdx_clean_parquet"], created_at="2026-08-18", status="PROMISING",
        ))
        df = frames[eid].copy()
        df = df[df["event_date"] >= 20220801]
        out = pd.DataFrame({
            "symbol": df["code"],
            "event_time": df["event_date"],
            "available_at": df["available_at"],
            "event_type": "ENTER",
            "payload_json": "{}",
        })
        store.put(eid, "v1", out, allow_overwrite=True)
    reg.save()
    print("registered events:", len(defs))

if __name__ == "__main__":
    main()
