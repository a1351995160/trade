"""方法原型的确定性与错误输入边界，不测试真实策略显著性。"""
import numpy as np
import pytest

from chanlun_trader.research.statistical_proposal_v3 import (
    anchored_folds, by_adjust, centered_mean_test, lagged_placebo,
    purged_training_mask, stationary_indices,
)


def test_common_indices_and_centering():
    x=np.random.default_rng(18).normal(size=240)
    pair=np.column_stack([x,x])
    a=centered_mean_test(pair,draws=199,seed=7)
    assert a==centered_mean_test(pair,draws=199,seed=7)
    assert a['p_values'][0]==a['p_values'][1]
    assert a['null_centered'] and a['common_time_indices']
    shifted=centered_mean_test(pair+3,draws=199,seed=7)
    assert shifted['p_values']==[0.005,0.005]


def test_stationary_blocks_continue_and_wrap():
    indices=stationary_indices(240,500,20,8)
    continued=((indices[:,1:]-indices[:,:-1])%240==1).mean()
    assert 0.94<continued<0.96
    assert indices.min()>=0 and indices.max()<240


@pytest.mark.parametrize('values',[np.zeros(240),np.full(240,np.nan),np.ones(100)])
def test_invalid_or_degenerate_series_cannot_pass(values):
    with pytest.raises(ValueError):
        centered_mean_test(values,draws=9)


def test_complete_by_family_and_known_adjustment():
    np.testing.assert_allclose(by_adjust([0.01,0.04,0.8],3),[0.055,0.11,1.0])
    with pytest.raises(ValueError,match='COMPLETE_FROZEN_FAMILY'):
        by_adjust([0.01],3)
    with pytest.raises(ValueError):
        by_adjust([0.01,float('nan')],2)


def test_actual_delayed_exit_purged_and_missing_end_blocked():
    assert anchored_folds()[0] == {'train':[0,126], 'embargo':[126,146], 'evaluation':[146,209]}
    # 信号很早但实际持有跨入隔离区，仍须剔出该训练标签；不是删绩效行。
    assert purged_training_mask([10,100,125], [135,110,126],146).tolist()==[False,True,False]
    with pytest.raises(ValueError):
        purged_training_mask([10],[float('nan')],146)


def test_placebo_never_moves_future_spike_backward():
    signal=np.zeros((240,2)); signal[-1,0]=999
    placebo=lagged_placebo(signal)
    assert np.isnan(placebo[:20]).all()
    assert np.nanmax(placebo)==0
    signal[0,1]=42
    assert lagged_placebo(signal)[20,1]==42
