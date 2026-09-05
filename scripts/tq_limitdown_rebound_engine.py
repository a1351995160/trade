"""LimitDown Rebound 事件全引擎初测：T+1 open 买入，持有 5D。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
import pandas as pd
import numpy as np
from chanlun_trader.config import load_config
from chanlun_trader.tdx_data import TdxData
from chanlun_trader.chan import Signal
from short_horizon_lab import prep_data, run_one, TRAIN, VALIDATION

def main():
    cfg=load_config()
    tdx=TdxData(cfg["tdx"]["vipdoc"], cfg["tdx"]["gbbq"], cfg["tdx"].get("cache_dir"))
    ev=pd.read_csv("experiments/tq_tradable_event_forward.csv")
    ev=ev[(ev.kind=="limit") & (ev.status.isin([-1,-2]))].copy()
    ev=ev[ev.date>=20220801].copy()
    for period in [TRAIN, VALIDATION]:
        cal, uni, union, pre = prep_data(cfg, tdx, period)
        sub=ev[(ev.date>=int(period[0].replace("-",""))) & (ev.date<=int(period[1].replace("-","")))]
        sigs=[]
        for r in sub.itertuples():
            code=str(r.code).split(".")[0]
            sigs.append(Signal(code=code, signal_date=int(r.date), direction="buy",
                               signal_type="LIMITDOWN_REBOUND_H5", price_ref=0.0, stop_low=0.0, score=1.0))
        print(f"--- {period} events={len(sigs)}")
        m, avg_hold, med_hold, turn, trades_year, cost_drag, r = run_one(tdx, cfg, cal, uni, union, pre, sigs, period, hold=5, topn=10)
        print("total_return", round(m.get("total_return",0)*100,2), "maxdd", round(m.get("max_drawdown",0)*100,2),
              "sharpe", round(m.get("sharpe",0),2), "win_rate", round(m.get("win_rate",0)*100,1),
              "trades", len(r["trades"]), "avg_hold", round(avg_hold,1), "turn", round(turn,2))
        # concentration
        tr=pd.DataFrame([t.__dict__ for t in r["trades"]])
        if len(tr) and tr.pnl.sum()>0:
            top=tr.pnl.sort_values(ascending=False)
            print("top1 share", round(top.head(1).sum()/tr.pnl.sum(),3),
                  "top3", round(top.head(3).sum()/tr.pnl.sum(),3),
                  "top5", round(top.head(5).sum()/tr.pnl.sum(),3),
                  "top10", round(top.head(10).sum()/tr.pnl.sum(),3))
            g=tr.groupby("buy_date").pnl.sum().sort_values(ascending=False)
            print("top1 day share", round(g.iloc[0]/tr.pnl.sum(),3), "date", g.index[0])
            print("top3 day share", round(g.head(3).sum()/tr.pnl.sum(),3))

if __name__=="__main__":
    main()
