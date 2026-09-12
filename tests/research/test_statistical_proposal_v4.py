"""合成统计实现验证，不能替代适用性校准。"""
import numpy as np
import pytest

from chanlun_trader.research.statistical_proposal_v4 import bartlett_mean_se, studentized_mean_test


def test_hac_matches_independent_autocovariance_formula():
    x = np.random.default_rng(4).normal(size=(2, 252, 3))
    z = x - x.mean(axis=1, keepdims=True)
    variance = (z*z).sum(axis=1)/252
    for lag in range(1, 21):
        variance += 2*(1-lag/21)*(z[:, lag:]*z[:, :-lag]).sum(axis=1)/252
    np.testing.assert_allclose(bartlett_mean_se(x), np.sqrt(variance/252), rtol=1e-12)


def test_common_columns_scale_invariance_and_direction():
    x = np.random.default_rng(7).normal(size=252)
    result = studentized_mean_test(np.column_stack([x, 100*x]), draws=999, seed=12)
    assert result['p_values'][0] == result['p_values'][1]
    assert result == studentized_mean_test(np.column_stack([x, 100*x]), draws=999, seed=12)
    assert studentized_mean_test(x+3, draws=999)['p_values'] == [0.001]


@pytest.mark.parametrize('x', [np.zeros(252), np.full(252, np.nan), np.ones(100)])
def test_invalid_data_is_not_a_success(x):
    with pytest.raises(ValueError):
        studentized_mean_test(x, draws=19)
