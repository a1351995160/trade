"""TQ 专业数据 Event Study：LHB / LimitUp / Sentiment 的短期前瞻收益。"""
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

RESEARCH_START, RESEARCH_END = 20220801, 20250731


def fwd_ret(pre_dict, code, date, h):
    """local pre dict -> forward h-day close-to-close return."""
    item = pre_dict.get(code)
    if item is None:
        return np.nan
    dates, open_, high, low, close, vol, amt = item
    pos = int(np.searchsorted(dates, date, side="right") - 1)
    if pos < 0 or int(dates[pos]) != int(date):
        return np.nan
    j = pos + h
    if j >= len(dates) or int(dates[j]) != int(date) + h * 2:  # rough: allow any later trading day? no
        pass
    if j >= len(dates):
        return np.nan
    if close[pos] > 0:
        return close[j] / close[pos] - 1
    return np.nan


def fwd_ret_td(pre_dict, code, date, h, cal_map):
    """按交易日历取 h 个交易日后。cal_map: date->pos in sorted trading days. """
    item = pre_dict.get(code)
    if item is None:
        return np.nan
    dates, open_, high, low, close, vol, amt = item
    pos = int(np.searchsorted(dates, date, side="right") - 1)
    if pos < 0 or int(dates[pos]) != int(date):
        return np.nan
    # 代码停牌？从代码自身序列取 pos+h（约等于交易日）
    j = pos + h
    if j >= len(dates):
        return np.nan
    if close[pos] > 0:
        return close[j] / close[pos] - 1
    return np.nan


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
    sent = pd.read_parquet("data/tdx/clean/market_sentiment.parquet")
    print("loaded", len(lhb), len(lim), len(sent))

    # 计算前瞻收益
    rows = []
    for df, kind in [(lhb, "lhb"), (lim, "limit")]:
        for r in df.itertuples():
            code = str(r.code).split('.')[0]
            d = int(r.event_date)
            if d < RESEARCH_START or d > RESEARCH_END:
                continue
            row = dict(kind=kind, date=d, code=code, sector=imap.get(code.split(".")[0], "UNK"),
                       net_amount=getattr(r, "net_amount", np.nan),
                       status=getattr(r, "status", np.nan),
                       seal_amount=getattr(r, "seal_amount", np.nan))
            for h in [1, 2, 3, 5, 7, 10]:
                row[f"fwd{h}"] = fwd_ret_td(pre_all, code, d, h, None)
            rows.append(row)
    ev = pd.DataFrame(rows)
    ev.to_csv("experiments/tq_event_study_forward.csv", index=False, encoding="utf-8-sig")
    print("event-study rows:", len(ev))

    # 汇总
    for kind in ["lhb", "limit"]:
        sub = ev[ev.kind == kind]
        print("\n===", kind, "n=", len(sub))
        for h in [1, 2, 3, 5, 7, 10]:
            x = sub[f"fwd{h}"].dropna()
            if len(x) == 0:
                continue
            print(f"  fwd{h}: n={len(x)} mean={x.mean()*100:.3f}% med={x.median()*100:.3f}% win={(x>0).mean()*100:.1f}%")
        # exclude 20240924-20241008
        mask = (sub.date >= 20240924) & (sub.date <= 20241008)
        print("  cluster share:", round(mask.mean(), 3))
        for h in [1, 5, 10]:
            x = sub[~mask][f"fwd{h}"].dropna()
            if len(x):
                print(f"  excl-cluster fwd{h}: n={len(x)} mean={x.mean()*100:.3f}% win={(x>0).mean()*100:.1f}%")

    # 随机对照：从 pre_all 中每个有数据日期的股票随机抽，近似市场基准
    rng = np.random.default_rng(42)
    ctrl = []
    all_codes = list(pre_all.keys())
    for kind in ["lhb", "limit"]:
        sub = ev[ev.kind == kind]
        for _ in range(len(sub)):
            code = all_codes[rng.integers(0, len(all_codes))]
            item = pre_all[code]
            dates = item[0]
            # 随机选一个研究区间内的交易日
            pos = rng.integers(25, len(dates))
            d = int(dates[pos])
            if d < RESEARCH_START or d > RESEARCH_END:
                continue
            row = dict(kind=kind + "_random", date=d, code=code)
            for h in [1, 2, 3, 5, 7, 10]:
                if pos + h < len(dates):
                    row[f"fwd{h}"] = item[4][pos + h] / item[4][pos] - 1
                else:
                    row[f"fwd{h}"] = np.nan
            ctrl.append(row)
    ctrl = pd.DataFrame(ctrl)
    for kind in ["lhb", "limit"]:
        sub = ctrl[ctrl.kind == kind + "_random"]
        print("\nrandom control", kind, "n=", len(sub))
        for h in [1, 5, 10]:
            x = sub[f"fwd{h}"].dropna()
            print(f"  fwd{h}: mean={x.mean()*100:.3f}% win={(x>0).mean()*100:.1f}%")
    ctrl.to_csv("experiments/tq_event_study_random_control.csv", index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
