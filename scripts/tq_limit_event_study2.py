"""LimitUp/LHB 事件的可交易前瞻收益 + 集中度 + 对照。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
import numpy as np
import pandas as pd
from chanlun_trader.config import load_config
from chanlun_trader.tdx_data import TdxData
from chanlun_trader.industry import load_industry_map
from short_horizon_lab import prep_data, TRAIN, VALIDATION

def tradable_fwd(pre, code, date, h):
    """T+1 open 入场，持有 h 个交易日，T+1+h-1 close 退出。"""
    item = pre.get(code)
    if item is None:
        return np.nan
    dates, open_, high, low, close, vol, amt = item
    pos = int(np.searchsorted(dates, date, side="right") - 1)
    if pos < 0 or int(dates[pos]) != int(date):
        return np.nan
    entry = pos + 1
    exit_ = entry + h - 1
    if exit_ >= len(dates) or open_[entry] <= 0:
        return np.nan
    return close[exit_] / open_[entry] - 1

def main():
    cfg = load_config()
    tdx = TdxData(cfg["tdx"]["vipdoc"], cfg["tdx"]["gbbq"], cfg["tdx"].get("cache_dir"))
    imap = load_industry_map(cfg.get("tdx", {}))
    pre_train = prep_data(cfg, tdx, TRAIN)[3]
    pre_val = prep_data(cfg, tdx, VALIDATION)[3]
    pre_all = dict(pre_train)
    for k, v in pre_val.items():
        pre_all.setdefault(k, v)

    lhb = pd.read_parquet("data/tdx/clean/lhb_events.parquet")
    lim = pd.read_parquet("data/tdx/clean/limit_events.parquet")
    print("loaded", len(lhb), len(lim))

    rows=[]
    for df, kind in [(lhb,"lhb"),(lim,"limit")]:
        for r in df.itertuples():
            code=str(r.code).split('.')[0]
            d=int(r.event_date)
            if not (20220801 <= d <= 20250731):
                continue
            row=dict(kind=kind,date=d,code=code,sector=imap.get(code,'UNK'),
                     net_amount=getattr(r,'net_amount',np.nan),status=getattr(r,'status',np.nan),
                     seal_amount=getattr(r,'seal_amount',np.nan),
                     first_limit_time=getattr(r,'first_limit_time',None),
                     open_count=getattr(r,'open_count',np.nan))
            for h in [1,2,3,5,7,10]:
                row[f"tf{h}"]=tradable_fwd(pre_all,code,d,h)
            rows.append(row)
    ev=pd.DataFrame(rows)
    ev.to_csv("experiments/tq_tradable_event_forward.csv",index=False,encoding="utf-8-sig")

    for kind in ["lhb","limit"]:
        sub=ev[ev.kind==kind]
        print("\n=== tradable",kind,"n=",len(sub))
        for h in [1,2,3,5,7,10]:
            x=sub[f"tf{h}"].dropna()
            if len(x)==0: continue
            print(f"  tf{h}: n={len(x)} mean={x.mean()*100:.3f}% med={x.median()*100:.3f}% win={(x>0).mean()*100:.1f}%")
        mask=(sub.date>=20240924)&(sub.date<=20241008)
        print("  cluster share:",round(mask.mean(),3))
        for h in [1,5,10]:
            x=sub[~mask][f"tf{h}"].dropna()
            print(f"  excl tf{h}: n={len(x)} mean={x.mean()*100:.3f}% med={x.median()*100:.3f}% win={(x>0).mean()*100:.1f}%")

    # LimitUp 分状态
    lim_ev=ev[ev.kind=="limit"].copy()
    for st in [2,1,-1,-2]:
        sub=lim_ev[lim_ev.status==st]
        if len(sub)==0: continue
        x=sub.tf5.dropna()
        print(f"  status {st}: n={len(sub)} tf5 mean={x.mean()*100:.3f}% med={x.median()*100:.3f}% win={(x>0).mean()*100:.1f}%")

    # 集中度（等权 sum of tf5）
    print("\n=== concentration (sum of tf5, equal-weight event) ===")
    for kind in ["lhb","limit"]:
        sub=ev[ev.kind==kind].dropna(subset=['tf5'])
        s=sub.tf5.sum()
        top=sub.tf5.sort_values(ascending=False)
        if s>0:
            for k in [1,3,5,10]:
                print(f"  {kind} top{k} share: {top.head(k).sum()/s:.3f}")
        else:
            print(f"  {kind} sum negative")
        # 最大单日
        dg=sub.groupby('date').tf5.sum().sort_values(ascending=False)
        print(f"  {kind} top1 day share:", dg.iloc[0]/s if s>0 else np.nan, "date", dg.index[0])
        print(f"  {kind} top3 day share:", dg.head(3).sum()/s if s>0 else np.nan)

    # Sector-matched control for limit
    lim_ev=ev[ev.kind=="limit"].dropna(subset=['tf5'])
    all_sec_ret=[]
    for sector,g in lim_ev.groupby('sector'):
        other=lim_ev[lim_ev.sector!=sector].tf5  # 不匹配板块，需用全样本非事件股票
        # 这里用全市场其他板块事件均值作为近似板块对照
        all_sec_ret.append(other.mean())
    print("cross-sector control tf5 mean:", np.mean(all_sec_ret)*100)

if __name__=="__main__":
    main()
