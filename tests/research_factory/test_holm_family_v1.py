import pytest
from chanlun_trader.research_factory.holm_family_v1 import adjust_family


def test_hand_calculated_monotonic_adjustment_and_original_order():
    result=adjust_family(dict(a=.01,b=.04,c=.03,d=.2),('a','b','c','d'))
    assert result['adjusted_p']==pytest.approx(dict(a=.04,b=.09,c=.09,d=.2))


def test_unavailable_member_does_not_shrink_family():
    result=adjust_family(dict(a=.01,b=None,c=.02,d=.4),('a','b','c','d'))
    assert result['family_size']==4 and result['adjusted_p']['b'] is None
    assert result['adjusted_p']['a']==.04 and result['adjusted_p']['d']==.8
    assert result['qualification']=='NOT_ASSESSED'


@pytest.mark.parametrize('bad',[float('nan'),float('inf'),-.1,1.1,True])
def test_invalid_input_rejected(bad):
    with pytest.raises(ValueError):adjust_family({'a':bad},('a',))


def test_membership_and_ties():
    with pytest.raises(ValueError):adjust_family({'a':.1},('a','b'))
    with pytest.raises(ValueError):adjust_family({'a':.1},('a','a'))
    assert adjust_family({'a':.02,'b':.02},('a','b'))['adjusted_p']==dict(a=.04,b=.04)


def test_centered_bootstrap_hand_tail_and_degenerate():
    import numpy as np
    from chanlun_trader.research_factory.holm_family_v1 import mean_null_pvalues,stationary_weights
    x=np.array([[1.,0.,1.],[3.,0.,1.]])
    weights=np.array([[1.,0.],[0.,1.],[.5,.5]])
    p=mean_null_pvalues(x,weights)
    assert p[0]==.25 and p[1]==1 and np.isnan(p[2])
    weights=stationary_weights(10,100,20,42)
    assert np.allclose(weights.sum(axis=1),1)
    assert np.array_equal(weights,stationary_weights(10,100,20,42))
    # 所有零假设成立时Holm至少拒绝一项，等价于最小p<=alpha/4。
    for ps in ([.01,.5,.9,.8],[.02,.03,.04,.9]):
        adj=adjust_family(dict(zip('abcd',ps)),tuple('abcd'))['adjusted_p']
        assert (min(adj.values())<=.05)==(min(ps)<=.05/4)
