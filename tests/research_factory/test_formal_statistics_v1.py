"""检验公式与校准账本完整性；合成断言不授予真实策略资格。"""
from copy import deepcopy
import math

import numpy as np
import pytest

from chanlun_trader.research_factory import formal_statistics_v1 as stats
from chanlun_trader.research_factory.bounded_research_v1 import _put
from chanlun_trader.research_factory.common import stable_hash


def test_geometric_segments_wrap_and_truncate_equal_hand_expansion(monkeypatch):
    class FixedGenerator:
        def __init__(self):
            self.lengths = iter(([2, 3], [9, 9]))
            self.starts = iter(([3, 1], [0, 3]))
        def geometric(self, probability, size):
            assert probability == .5 and size == 2
            return np.array(next(self.lengths))
        def integers(self, low, high, size):
            assert (low, high, size) == (0, 4, 2)
            return np.array(next(self.starts))
    monkeypatch.setattr(np.random, 'default_rng', lambda seed: FixedGenerator())
    values = np.array([[1., 2.], [2., 4.], [3., 6.], [4., 8.]])
    result = stats.stationary_means(values, block=2, draws=2, seed=1)
    np.testing.assert_array_equal(result, [[2., 4.], [3.25, 6.5]])


def test_common_indices_reproducible_and_centering_translation_invariant():
    x = np.arange(12., dtype=float).reshape(6, 2)
    one = stats.stationary_means(x, block=3, draws=80, seed=22)
    two = stats.stationary_means(x, block=3, draws=80, seed=22)
    np.testing.assert_array_equal(one, two)
    np.testing.assert_allclose(one[:, 1] - one[:, 0], 1.)
    np.testing.assert_allclose(stats.stationary_means(x + 100., block=3, draws=80, seed=22), one + 100.)


def test_max_block_pvalues_and_plus_one_are_not_cherry_picked(monkeypatch):
    x = np.column_stack([np.arange(504.) + 1, np.arange(504.) + 2])
    calls = []
    def means(centered, *, block, draws, seed):
        calls.append(block)
        np.testing.assert_allclose(centered.mean(axis=0), 0.)
        sample = np.full((draws, 2), -1000.)
        sample[:block] = 1000.
        return sample
    monkeypatch.setattr(stats, 'stationary_means', means)
    result = stats.family_test({'a': x[:, 0].tolist(), 'b': x[:, 1].tolist()}, alpha=.01)
    assert calls == [10, 20, 40]
    assert result['raw_p'] == {'a': 41 / 4096, 'b': 41 / 4096}
    assert result['adjusted_p'] == {'a': 82 / 4096, 'b': 82 / 4096}
    assert result['supported'] == {'a': False, 'b': False}
    assert result['qualification'] == 'NOT_ASSESSED'


def test_degenerate_and_nonfinite_members_stay_in_frozen_family():
    result = stats.family_test({'zero': [0.] * 504, 'positive': [1.] * 504,
                               'bad': [float('nan')] * 504}, alpha=.0125)
    assert result['family_size'] == 3
    assert result['raw_p'] == {'bad': None, 'positive': None, 'zero': 1.}
    assert not any(result['supported'].values())
    assert result['untestable'] == {'bad': 'NONFINITE_MEMBER_UNTESTABLE', 'positive': 'NONZERO_CONSTANT_UNTESTABLE'}


@pytest.mark.parametrize('data', [{}, {'a': [0.] * 503}, {str(i): [0.] * 504 for i in range(6)}])
def test_incomplete_window_or_family_rejected(data):
    with pytest.raises(ValueError):
        stats.family_test(data, alpha=.01)


@pytest.mark.parametrize('n', [1, 2, 10, 25])
def test_binomial_cdf_matches_independent_combinatorial_formula(n):
    for k in range(n + 1):
        for p in [0., .001, .05, .3, .9, 1.]:
            expected = math.fsum(math.comb(n, i) * p ** i * (1-p) ** (n-i) for i in range(k+1))
            assert stats.binomial_cdf(k, n, p) == pytest.approx(expected, rel=2e-12, abs=2e-14)


def test_cp_closed_forms_and_exact_tail_inversion():
    delta, n = .05 / 81, 2048
    assert stats.cp_upper(0, n, delta) == pytest.approx(1 - delta ** (1/n))
    assert stats.cp_upper(n, n, delta) == 1.
    assert stats.cp_upper(9, 10, .05) == pytest.approx(.95 ** .1)
    for k in [1, 7, 35]:
        bound = stats.cp_upper(k, n, delta)
        # CDF再用独立comb公式核验，不使用被测CDF实现。
        direct = math.fsum(math.comb(n, i) * bound ** i * (1-bound) ** (n-i) for i in range(k+1))
        assert direct == pytest.approx(delta, rel=1e-10)


def test_dgp_seed_streams_independent_and_each_replicate_fresh():
    for condition in (*stats.SUPPORT, *stats.STRESS):
        x, data, boot = stats.generate_dgp(condition, 0)
        assert x.shape == (504, 5) and np.isfinite(x).all()
        assert data != boot
        np.testing.assert_array_equal(x, stats.generate_dgp(condition, 0)[0])
        assert not np.array_equal(x, stats.generate_dgp(condition, 1)[0])


def fake_records(monkeypatch):
    monkeypatch.setitem(stats.CALIBRATION_SPEC, 'replicates', 2)
    records = []
    for ci, condition in enumerate((*stats.SUPPORT, *stats.STRESS)):
        for rep in range(2):
            variants = {name: [1.] * 5 for name in stats.FAMILIES}
            if condition == 'IID':
                variants.update({f'POWER_SR_{sr:g}': [.000244140625] * 5
                                 for sr in stats.CALIBRATION_SPEC['power_iid_annual_sharpe']})
            records.append({'condition': condition, 'replicate': rep,
                'data_seed': [20260926, ci, rep, 0], 'bootstrap_seed': [20260926, ci, rep, 1],
                'data_hash': '0' * 64, 'raw_p': variants})
    return records


def test_low_replicates_are_not_excused_and_missing_or_duplicate_records_fail(monkeypatch):
    records = fake_records(monkeypatch)
    result = stats.summarize_records(records)
    assert result['support_passed'] is False
    assert all(row['passed'] is None for row in result['conditions'] if not row['support_domain'])
    assert all(row['family_any_rejection_rate'] == 1. for row in result['power'])
    with pytest.raises(ValueError, match='INCOMPLETE'):
        stats.summarize_records(records[:-1])
    with pytest.raises(ValueError, match='MEMBERSHIP'):
        stats.summarize_records([*records, records[0]])


def test_loader_recomputes_summary_and_checks_frozen_sources(tmp_path, monkeypatch):
    records = fake_records(monkeypatch)
    prereg = {'spec': deepcopy(stats.CALIBRATION_SPEC), 'sources': stats.source_hashes()}
    _put(tmp_path / 'PREREGISTRATION.json', prereg)
    report = {'preregistration_hash': stable_hash(prereg), 'method_hash': stats.METHOD_HASH,
              'records': records, 'records_hash': stable_hash(records), 'summary': stats.summarize_records(records)}
    path = tmp_path / 'CALIBRATION.json'
    _put(path, report)
    assert stats.load_calibration(path)['method_approved'] is False
    path.unlink()
    report['summary']['support_passed'] = True
    _put(path, report)
    with pytest.raises(ValueError, match='SUMMARY_CONFLICT'):
        stats.load_calibration(path)
    monkeypatch.setattr(stats, 'source_hashes', lambda: {'changed': 'source'})
    with pytest.raises(ValueError, match='BINDING_CONFLICT'):
        stats.load_calibration(path)
