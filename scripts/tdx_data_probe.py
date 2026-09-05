"""TDX 数据层探测脚本：只访问 TRAIN+VALIDATION（<=2025-07-31），不触碰 FINAL TEST。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from chanlun_trader.data.tdx import TQClient, TDXProviders
from chanlun_trader.data.tdx.manifest import load_manifest

TRAIN_START, TRAIN_END = "20220801", "20240731"
VAL_START, VAL_END = "20240801", "20250731"
RESEARCH_START, RESEARCH_END = "20220801", "20250731"

CODES = ["600519.SH", "000001.SZ", "300750.SZ", "002031.SZ", "300526.SZ",
         "601318.SH", "000858.SZ", "002594.SZ", "688981.SH", "300059.SZ"]

def main():
    providers = TDXProviders(TQClient())
    print("health:", providers.health_check().get("ErrorId"))
    # 1) market sentiment full research range
    df = providers.sentiment.get_sentiment(RESEARCH_START, RESEARCH_END)
    print("sentiment rows:", len(df), "date range:", df["date"].min(), df["date"].max())
    # 2) LHB sample batch
    ev = providers.lhb.get_events_batch(CODES, RESEARCH_START, RESEARCH_END)
    print("lhb sample events:", len(ev))
    if ev:
        e0 = ev[0]
        print("  first:", e0.code, e0.event_date, e0.net_amount)
    # 3) limit sample batch
    le = providers.limit.get_events_batch(CODES, RESEARCH_START, RESEARCH_END)
    print("limit sample events:", len(le))
    if le:
        print("  first:", le[0].code, le[0].event_date, le[0].status)
    # 4) finance PIT sample
    for code in CODES[:3]:
        snap = providers.finance.get_snapshot(code, 20250131)
        print("finance", code, snap.report_period if snap else None, snap.announce_date if snap else None,
              list(snap.field_values.keys())[:4] if snap else None)
    m = load_manifest()
    print("manifest rows:", len(m))
    print(m.tail(3).to_string())

if __name__ == "__main__":
    main()
