"""Sector Event Study：基于日线 Top500 PIT Universe 的板块集体启动事件研究。

输出 experiments/sector_event_study.csv。
"""
import sys, csv
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
import numpy as np
import pandas as pd
from chanlun_trader.config import load_config
from chanlun_trader.tdx_data import TdxData
from chanlun_trader.industry import load_industry_map
from short_horizon_lab import prep_data, TRAIN, VALIDATION

cfg = load_config()
tdx = TdxData(cfg['tdx']['vipdoc'], cfg['tdx']['gbbq'], cfg['tdx'].get('cache_dir'))
imap = load_industry_map(cfg.get('tdx', {}))


def build_sector_state(cal, uni, pre):
    """逐日计算板块状态。每天返回 (sector_state_dict, date_pos_map, date_to_codes) 合并在一个 dict。"""
    states = []
    date_members = {}
    for d in cal:
        members = uni.get(int(d))
        if not members:
            states.append({})
            date_members[int(d)] = []
            continue
        recs = []
        member_codes = []
        for code in members:
            item = pre.get(code)
            if item is None:
                continue
            dates, open_, high, low, close, vol, amt = item
            pos = int(np.searchsorted(dates, d, side="right") - 1)
            if pos < 25 or int(dates[pos]) != int(d):
                continue
            ind = imap.get(code, 'UNK')
            ret1 = close[pos] / close[pos - 1] - 1 if close[pos - 1] > 0 else np.nan
            ret5 = close[pos] / close[pos - 5] - 1 if close[pos - 5] > 0 else np.nan
            hi10 = float(np.max(high[pos - 10:pos]))
            is_break = 1.0 if close[pos] >= hi10 else 0.0
            amt_ma20 = float(np.mean(amt[pos - 19:pos + 1]))
            recs.append((code, ind, ret1, ret5, is_break, amt[pos], amt_ma20, close[pos], open_[pos], high[pos], low[pos]))
            member_codes.append(code)
        date_members[int(d)] = member_codes
        sec = {}
        for ind in {r[1] for r in recs}:
            rr = [r for r in recs if r[1] == ind]
            n = len(rr)
            if n < 3:
                continue
            up = float(np.mean([1.0 if r[2] > 0 else 0.0 for r in rr]))
            ret1_m = float(np.mean([r[2] for r in rr]))
            ret5_m = float(np.mean([r[3] for r in rr]))
            br = float(np.mean([r[4] for r in rr]))
            amt_tot = float(np.sum([r[5] for r in rr]))
            amt_ma_tot = float(np.sum([r[6] for r in rr]))
            vol_exp = amt_tot / amt_ma_tot if amt_ma_tot > 0 else np.nan
            sync = max(up, 1.0 - up)
            lu = float(np.mean([1.0 if (r[7] >= r[9] * 0.995 and r[2] >= 0.095) else 0.0 for r in rr]))
            sec[ind] = dict(n=n, up=up, ret1=ret1_m, ret5=ret5_m, br=br,
                            vol_exp=vol_exp, sync=sync, lu=lu,
                            codes=[r[0] for r in rr])
        states.append(sec)
    return states, date_members


def fwd_sector_return(period_cal, pre, date_members, i, sector, codes, h):
    """事件日 i 收盘 -> i+h 收盘，板块成员等权平均累计收益。"""
    d_now = int(period_cal[i])
    j = i + h
    if j >= len(period_cal):
        return np.nan
    d_fwd = int(period_cal[j])
    rets = []
    for code in codes:
        item = pre.get(code)
        if item is None:
            continue
        dates, open_, high, low, close, vol, amt = item
        p0 = int(np.searchsorted(dates, d_now, side="right") - 1)
        p1 = int(np.searchsorted(dates, d_fwd, side="right") - 1)
        if p0 < 0 or p1 <= p0 or int(dates[p0]) != d_now or int(dates[p1]) != d_fwd:
            continue
        if close[p0] > 0:
            rets.append(close[p1] / close[p0] - 1)
    return float(np.mean(rets)) if rets else np.nan


def main():
    events = []
    for period in [TRAIN, VALIDATION]:
        cal, uni, union, pre = prep_data(cfg, tdx, period)
        states, date_members = build_sector_state(cal, uni, pre)
        for i, d in enumerate(cal):
            st = states[i]
            for ind, row in st.items():
                if np.isnan(row['vol_exp']):
                    continue
                is_event = row['up'] >= 0.6 and row['br'] >= 0.2 and row['vol_exp'] >= 1.5 and row['sync'] >= 0.6
                fwds = {}
                for h in [1, 2, 3, 5, 10]:
                    fwds[h] = fwd_sector_return(cal, pre, date_members, i, ind, row['codes'], h)
                events.append(dict(period=period[0][:4], date=int(d), sector=ind, n=row['n'],
                                   up=row['up'], ret1=row['ret1'], ret5=row['ret5'], br=row['br'],
                                   vol_exp=row['vol_exp'], sync=row['sync'], lu=row['lu'],
                                   is_event=int(is_event), fwd1=fwds[1], fwd2=fwds[2], fwd3=fwds[3],
                                   fwd5=fwds[5], fwd10=fwds[10]))
    out = Path('experiments/sector_event_study.csv')
    with open(out, 'w', newline='', encoding='utf-8-sig') as f:
        cols = ['period', 'date', 'sector', 'n', 'up', 'ret1', 'ret5', 'br', 'vol_exp', 'sync', 'lu',
                'is_event', 'fwd1', 'fwd2', 'fwd3', 'fwd5', 'fwd10']
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for e in events:
            w.writerow(e)
    df = pd.DataFrame(events)
    ev = df[df['is_event'] == 1]
    non = df[df['is_event'] == 0]
    print('total sector-days', len(df), 'events', len(ev), 'non', len(non))
    for h in ['fwd1', 'fwd2', 'fwd3', 'fwd5', 'fwd10']:
        e = ev[h].dropna(); n = non[h].dropna()
        if len(e) and len(n):
            print(f"{h}: event n={len(e)} mean={e.mean()*100:.3f}% median={e.median()*100:.3f}% win={(e>0).mean()*100:.1f}% | non mean={n.mean()*100:.3f}% win={(n>0).mean()*100:.1f}%")
    print('event sector counts:')
    print(ev.groupby('sector').size().sort_values(ascending=False).head(12))
    print('event date counts:')
    print(ev.groupby('date').size().sort_values(ascending=False).head(10))


if __name__ == '__main__':
    main()
