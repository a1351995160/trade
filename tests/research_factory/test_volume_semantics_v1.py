import numpy as np
from chanlun_trader.research_factory.volume_semantics_v1 import RULES, summarize, decide


def groups_for(scale):
    n=100000
    volume=np.ones(n)*1000
    amount=volume*10*scale
    lo=np.ones(n)*9;hi=np.ones(n)*11
    group={h:summarize(amount,volume,lo,hi,s) for h,s in RULES['hypotheses'].items()}
    return {key:group for key in RULES['groups_must_agree']}


def test_units_are_identified_by_dimension_not_order_counts():
    assert decide(groups_for(1))=='SHARES'
    assert decide(groups_for(100))=='LOTS_100_SHARES'
    assert decide(groups_for(10))=='UNKNOWN'


def test_market_reversal_and_small_fixed_sample_block():
    groups=groups_for(1)
    groups['SH']=groups_for(100)['SH']
    assert decide(groups)=='UNKNOWN'
    groups=groups_for(1)
    groups['600000.SH']={h:{**r,'n':99} for h,r in groups['600000.SH'].items()}
    assert decide(groups)=='UNKNOWN'


def test_eps_is_fixed_and_exact_ohlc_separate():
    r=summarize(np.array([100.05]),np.array([10.]),np.array([10.]),np.array([10.]),1)
    assert r['ohlc_exact']==0 and r['ohlc_eps']==1
    r=summarize(np.array([100.2]),np.array([10.]),np.array([10.]),np.array([10.]),1)
    assert r['ohlc_eps']==0
