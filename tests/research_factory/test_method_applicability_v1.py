"""适用性诊断的合成分支；不得据此声称真实账户适用或策略有效。"""
from copy import deepcopy
import json
import math

import pytest

from chanlun_trader.research_factory import formal_assessment_v1 as formal
from chanlun_trader.research_factory import method_applicability_v1 as applicability
from chanlun_trader.research_factory import formal_statistics_v2 as method
from chanlun_trader.research_factory.common import stable_hash
from test_research_screening_v1 import account


def write_artifact(path, value):
    body = {key: item for key, item in value.items() if key != '_integrity'}
    path.write_text(json.dumps({**body, '_integrity': stable_hash(body)}), encoding='utf-8')


@pytest.mark.parametrize('value', [-100., -7., -1., 0., .01, 1., 2., 5., 20., 100.])
def test_independent_integral_agrees_with_existing_beta_implementation(value):
    assert abs(applicability.reference_t7_upper_tail(value)-method.t7_upper_tail(value)) < 1e-10


@pytest.mark.parametrize('values', [
    [0.] * 504,
    [.001 + .0001 * math.sin(i) for i in range(504)],
    [.001 * math.sin(i / 80) for i in range(504)],
    [0.02 if i % 101 == 0 else -0.0001 for i in range(504)],
    [.001 if i < 252 else -.001 for i in range(504)],
    [math.sin(i) * (.001 if i < 252 else .01) for i in range(504)],
    [-.0002] * 504,
    [.001 * math.sin(i) * (.25 if math.sin(i-1) < 0 else 1.) for i in range(504)],
])
def test_finite_descriptions_never_establish_assumptions(values):
    result = applicability.describe_process(values)
    assert not result['assumptions_established']
    assert len(result['group_means']) == 8
    assert result['interpretation'] == 'DESCRIPTIVE_NOT_A_VALIDITY_TEST'


@pytest.mark.parametrize('values', [[0.] * 503, [float('nan')] * 504, [float('inf')] * 504])
def test_missing_nonfinite_series_rejected(values):
    with pytest.raises(ValueError, match='COMPLETE_FINITE'):
        applicability.describe_process(values)


def synthetic_scope(tmp_path, monkeypatch):
    results = {'BENCHMARK_BASE': account(0., benchmark=True)}
    for member in range(1, 6):
        rates = [.0001 * member + .00005 * math.sin(i / 14) for i in range(504)]
        for suffix in ('BASE', 'STRESS'):
            results[f'CANDIDATE_{member:03d}_{suffix}'] = account(rates, stress=suffix == 'STRESS')
    window = {'symbols': ['000001.SZ', '600000.SH'], 'feature_start': 0, 'account_start': 0, 'account_end': 503}
    for result in results.values():
        plan = result['strategy_plan']
        plan['backend'].update(window=window, initial_cash=1_000_000.)
        plan['plan_id'] = stable_hash(plan)
    scope = {'input_identity': 'SYNTHETIC_INPUT', 'window': window,
             'plans': {name: result['strategy_plan'] for name, result in results.items()}}
    write_artifact(tmp_path / 'SCOPE.json', scope)
    monkeypatch.setattr(applicability, 'settled_result', lambda root, name, **kwargs: results[name])
    monkeypatch.setattr(formal, '_load_method', lambda path: {
        'method_hash': method.METHOD_HASH, 'method_approved': True,
        'summary': {'synthetic_test_fixture': True}})
    prereg = tmp_path / 'PREREGISTRATION.json'
    applicability.freeze_diagnostic_scope(prereg, tmp_path)
    return results, prereg


def test_full_synthetic_numerical_success_cannot_grant_real_applicability(tmp_path, monkeypatch):
    results, prereg = synthetic_scope(tmp_path, monkeypatch)
    before = deepcopy(results)
    report = applicability.diagnose_settled_family(tmp_path, tmp_path / 'calibration.json', preregistration_path=prereg)
    assert report['numerical_check'] == 'PASSED'
    assert report['applicability'] == 'INSUFFICIENT_EVIDENCE'
    assert not report['strategy_qualified']
    assert len(report['execution_scope']) == 11
    assert report['exploratory_statistics']['family_size'] == 5
    assert results == before
    assert report == applicability.diagnose_settled_family(tmp_path, tmp_path / 'calibration.json', preregistration_path=prereg)


def test_edited_preregistration_cannot_enable_method(tmp_path, monkeypatch):
    _, prereg = synthetic_scope(tmp_path, monkeypatch)
    value = json.loads(prereg.read_text(encoding='utf-8'))
    value['policy']['method_variants'] = 2
    write_artifact(prereg, value)
    with pytest.raises(ValueError, match='PREREGISTRATION_CONFLICT'):
        applicability.diagnose_settled_family(tmp_path, 'unused', preregistration_path=prereg)


def test_missing_family_and_changed_capital_rejected(tmp_path, monkeypatch):
    results, prereg = synthetic_scope(tmp_path, monkeypatch)
    results['CANDIDATE_001_BASE']['strategy_plan']['backend']['initial_cash'] = 10_000
    with pytest.raises(ValueError, match='CAPITAL_CONFLICT'):
        applicability.diagnose_settled_family(tmp_path, 'unused', preregistration_path=prereg)
    path = tmp_path / 'SCOPE.json'
    scope = json.loads(path.read_text(encoding='utf-8'))
    del scope['plans']['CANDIDATE_005_STRESS']
    write_artifact(path, scope)
    with pytest.raises(ValueError, match='PREREGISTRATION_CONFLICT'):
        applicability.freeze_diagnostic_scope(prereg, tmp_path)


def test_calibration_error_propagates_without_approval_fallback(tmp_path, monkeypatch):
    _, prereg = synthetic_scope(tmp_path, monkeypatch)
    def fail(path):
        raise ValueError('CALIBRATION_BINDING_CONFLICT')
    monkeypatch.setattr(formal, '_load_method', fail)
    with pytest.raises(ValueError, match='CALIBRATION_BINDING'):
        applicability.diagnose_settled_family(tmp_path, 'unused', preregistration_path=prereg)


def test_different_benchmark_dates_rejected(tmp_path, monkeypatch):
    results, prereg = synthetic_scope(tmp_path, monkeypatch)
    benchmark = results['BENCHMARK_BASE']
    benchmark['daily_returns'][0]['date'] = -1
    benchmark['chain']['independent_account_checks'][0]['date'] = -1
    benchmark['chain']['account_dates'][0] = -1
    with pytest.raises(ValueError):
        applicability.diagnose_settled_family(tmp_path, 'unused', preregistration_path=prereg)
