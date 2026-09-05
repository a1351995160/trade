import sys, csv, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
import numpy as np
from chanlun_trader.config import load_config
from chanlun_trader.tdx_data import TdxData
from short_horizon_lab import prep_data, run_one, sig_tight_breakout, TRAIN, VALIDATION

cfg=load_config(); tdx=TdxData(cfg['tdx']['vipdoc'],cfg['tdx']['gbbq'],cfg['tdx'].get('cache_dir'))
print('prep TRAIN',flush=True)
cal,uni,union,pre=prep_data(cfg,tdx,TRAIN)
print('prep VAL',flush=True)
calv,univ,unionv,prev=prep_data(cfg,tdx,VALIDATION)

BASE=dict(range_lb=10,range_max=0.04,vol_mult=2.0,hold=5)
def sig_for(period):
    c,u,un,p=(cal,uni,union,pre) if period==TRAIN else (calv,univ,unionv,prev)
    return c,u,un,p,sig_tight_breakout(c,u,p,**BASE)
def delay_sigs(sigs, cal, n):
    idx={d:i for i,d in enumerate(cal)}
    out=[]
    for s in sigs:
        i=idx.get(int(s.signal_date))
        if i is None or i+n>=len(cal): continue
        s.signal_date=int(cal[i+n])
        out.append(s)
    return out

outp=Path('experiments/tb_j8_robustness_ledger.csv')
wh=not outp.exists()
with open(outp,'a',newline='',encoding='utf-8-sig') as f:
    w=csv.writer(f)
    if wh: w.writerow(['test','period','ret','dd','sharpe','pf','win_rate','avg_hold','turnover','n_trades','n_sigs'])
    periods=[(TRAIN,cal,uni,union,pre),(VALIDATION,calv,univ,unionv,prev)]
    # base
    for period,c,u,un,p in periods:
        c,u,un,p,sigs=sig_for(period); m,av,med,turn,ty,cost,_=run_one(tdx,cfg,c,u,un,p,sigs,period,BASE['hold'])
        w.writerow(['base',period[0],round(m['total_return'],4),round(m['max_drawdown'],4),round(m['sharpe'],2),round(m['profit_factor'],2),round(m['win_rate'],3),round(av,1),round(turn,1),m['trade_count'],len(sigs)])
        print('base',period,round(m['total_return'],4),round(m['max_drawdown'],4),round(m['sharpe'],2),round(m['profit_factor'],2),flush=True)
    # cost & slip & delay
    for period,c,u,un,p in periods:
        c,u,un,p,sigs=sig_for(period)
        for cm in [1.5,2.0,3.0]:
            m,av,med,turn,ty,cost,_=run_one(tdx,cfg,c,u,un,p,sigs,period,BASE['hold'],cost_mult=cm)
            w.writerow([f'cost_x{cm}',period[0],round(m['total_return'],4),round(m['max_drawdown'],4),round(m['sharpe'],2),round(m['profit_factor'],2),round(m['win_rate'],3),round(av,1),round(turn,1),m['trade_count'],len(sigs)])
            print(f'cost_x{cm}',period,round(m['total_return'],4),round(m['max_drawdown'],4),round(m['sharpe'],2),round(m['profit_factor'],2),flush=True)
        for sm in [2.0,3.0]:
            m,av,med,turn,ty,cost,_=run_one(tdx,cfg,c,u,un,p,sigs,period,BASE['hold'],slip_mult=sm)
            w.writerow([f'slip_x{sm}',period[0],round(m['total_return'],4),round(m['max_drawdown'],4),round(m['sharpe'],2),round(m['profit_factor'],2),round(m['win_rate'],3),round(av,1),round(turn,1),m['trade_count'],len(sigs)])
            print(f'slip_x{sm}',period,round(m['total_return'],4),round(m['max_drawdown'],4),round(m['sharpe'],2),round(m['profit_factor'],2),flush=True)
        for dly in [1,2]:
            s2=delay_sigs(sigs,c,dly)
            m,av,med,turn,ty,cost,_=run_one(tdx,cfg,c,u,un,p,s2,period,BASE['hold'])
            w.writerow([f'delay_{dly}',period[0],round(m['total_return'],4),round(m['max_drawdown'],4),round(m['sharpe'],2),round(m['profit_factor'],2),round(m['win_rate'],3),round(av,1),round(turn,1),m['trade_count'],len(s2)])
            print(f'delay_{dly}',period,round(m['total_return'],4),round(m['max_drawdown'],4),round(m['sharpe'],2),round(m['profit_factor'],2),flush=True)
    # parameter perturbation TRAIN
    _,_,_,_,base_sigs=sig_for(TRAIN)
    for label,kw,hold in [
        ('range_lb=8',dict(range_lb=8,range_max=0.04,vol_mult=2.0,hold=5),5),
        ('range_lb=12',dict(range_lb=12,range_max=0.04,vol_mult=2.0,hold=5),5),
        ('range_max=0.03',dict(range_lb=10,range_max=0.03,vol_mult=2.0,hold=5),5),
        ('range_max=0.05',dict(range_lb=10,range_max=0.05,vol_mult=2.0,hold=5),5),
        ('vol_mult=1.6',dict(range_lb=10,range_max=0.04,vol_mult=1.6,hold=5),5),
        ('vol_mult=2.4',dict(range_lb=10,range_max=0.04,vol_mult=2.4,hold=5),5),
        ('hold=4',dict(range_lb=10,range_max=0.04,vol_mult=2.0,hold=4),4),
        ('hold=6',dict(range_lb=10,range_max=0.04,vol_mult=2.0,hold=6),6),
    ]:
        s2=sig_tight_breakout(cal,uni,pre,**kw)
        m,av,med,turn,ty,cost,_=run_one(tdx,cfg,cal,uni,union,pre,s2,TRAIN,hold)
        w.writerow([label,'TRAIN',round(m['total_return'],4),round(m['max_drawdown'],4),round(m['sharpe'],2),round(m['profit_factor'],2),round(m['win_rate'],3),round(av,1),round(turn,1),m['trade_count'],len(s2)])
        print(label,round(m['total_return'],4),round(m['max_drawdown'],4),round(m['sharpe'],2),round(m['profit_factor'],2),flush=True)
    # universe stress TRAIN (top300/800 need separate build)
    for topn in [300,800]:
        from decomp_lib import build_universe_data
        from run_lowvol_qfq_confirm import build_universe_qfq
        calu,uniu,unionu,dfsu,_=build_universe_qfq(tdx,cfg,TRAIN[0],TRAIN[1],top_n=topn)
        preu={}
        for code,df in dfsu.items():
            if df.empty or 'qfq_close' not in df.columns: continue
            preu[code]=(df['date'].astype(np.int64).to_numpy(), df['qfq_open'].to_numpy(dtype=float), df['qfq_high'].to_numpy(dtype=float), df['qfq_low'].to_numpy(dtype=float), df['qfq_close'].to_numpy(dtype=float), df['volume'].to_numpy(dtype=float), df['amount'].to_numpy(dtype=float))
        s2=sig_tight_breakout(calu,uniu,preu,**BASE)
        m,av,med,turn,ty,cost,_=run_one(tdx,cfg,calu,uniu,unionu,preu,s2,TRAIN,BASE['hold'])
        w.writerow([f'universe_top{topn}','TRAIN',round(m['total_return'],4),round(m['max_drawdown'],4),round(m['sharpe'],2),round(m['profit_factor'],2),round(m['win_rate'],3),round(av,1),round(turn,1),m['trade_count'],len(s2)])
        print(f'universe_top{topn}',round(m['total_return'],4),round(m['max_drawdown'],4),round(m['sharpe'],2),round(m['profit_factor'],2),flush=True)
    # random signal skip 10% (2 seeds)
    rng=np.random.default_rng(42)
    for seed,frac in [(42,0.1),(7,0.05)]:
        s2=[s for s in base_sigs if rng.random()>frac]
        m,av,med,turn,ty,cost,_=run_one(tdx,cfg,cal,uni,union,pre,s2,TRAIN,BASE['hold'])
        w.writerow([f'skip_{int(frac*100)}pct_seed{seed}','TRAIN',round(m['total_return'],4),round(m['max_drawdown'],4),round(m['sharpe'],2),round(m['profit_factor'],2),round(m['win_rate'],3),round(av,1),round(turn,1),m['trade_count'],len(s2)])
        print(f'skip_{int(frac*100)}pct_seed{seed}',round(m['total_return'],4),round(m['max_drawdown'],4),round(m['sharpe'],2),round(m['profit_factor'],2),flush=True)
print('ROBUSTNESS DONE',flush=True)
