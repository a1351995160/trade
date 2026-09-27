"""冻结 V2 方法的有限账户过程诊断，不以样本检验冒充真实分布假设证明。"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import math
from pathlib import Path
import statistics

from .bounded_research_v1 import _put, _read
from .common import stable_hash
from . import formal_statistics_v2 as method
from .research_screening_v1 import account_series, screen, settled_result
from .mutation_boundary import ObjectiveMutationLock


DIAGNOSTIC_POLICY = {
    'version': 'METHOD_APPLICABILITY_DIAGNOSTIC_V1',
    'method_hash': method.METHOD_HASH, 'method_variants': 1, 'account_sessions': 504,
    'family_size': 5, 'settled_accounts': 11, 'new_account_runs': 0,
    'new_model_calls': 0, 'new_confirmation_attempts': 0,
    'result_use': 'EXPOSED_HISTORY_METHOD_DIAGNOSIS_ONLY',
    'failure_action': 'RETAIN_METHOD_APPLICABILITY_BLOCK_AND_CONTINUE_ENGINEERING',
    'assumption_test_acceptance': 'NEVER_INFER_INDEPENDENCE_FROM_NONREJECTION',
}


def _require(value, reason):
    if not value:
        raise ValueError('METHOD_APPLICABILITY_' + reason)


def _root(value):
    path = Path(value).absolute()
    _require(path.resolve() == path, 'PATH_REDIRECTED')
    return path


def _source_hash():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def freeze_diagnostic_scope(preregistration_path, accounts_root):
    """在读取账户收益前冻结有限诊断；已有文件不能改写来另选方法。"""
    root, destination = _root(accounts_root), _root(preregistration_path)
    scope = _read(root / 'SCOPE.json')
    body = {'policy': DIAGNOSTIC_POLICY, 'accounts_root': str(root),
            'scope_hash': stable_hash(scope), 'source_hash': _source_hash()}
    with ObjectiveMutationLock.for_resource(destination):
        if destination.exists():
            existing = _read(destination)
            _require(all(existing.get(key) == value for key, value in body.items()), 'PREREGISTRATION_CONFLICT')
            return existing
        body['frozen_at'] = datetime.now(timezone.utc).isoformat()
        _put(destination, body)
    return body


def reference_t7_upper_tail(value):
    """独立数值路线：t=sqrt(7)tan(theta)，积分cos(theta)^6，复合Simpson。"""
    value = float(value)
    _require(math.isfinite(value), 'REFERENCE_STATISTIC_NONFINITE')
    theta = math.atan(abs(value) / math.sqrt(7))
    intervals = 2048
    step = theta / intervals
    terms = [math.cos(index * step) ** 6 * (1 if index in (0, intervals)
             else 4 if index % 2 else 2) for index in range(intervals + 1)]
    integral = step * math.fsum(terms) / 3
    normalizer = math.gamma(4) / (math.sqrt(math.pi) * math.gamma(3.5))
    tail = min(1., max(0., .5 - normalizer * integral))
    return tail if value >= 0 else 1 - tail


def describe_process(values):
    values = [float(value) for value in values]
    _require(len(values) == 504 and all(math.isfinite(value) for value in values), 'COMPLETE_FINITE_SERIES_REQUIRED')
    mean = statistics.fmean(values)
    centered = [value - mean for value in values]
    variance = statistics.fmean([value * value for value in centered])
    groups = [statistics.fmean(values[start:start + 63]) for start in range(0, 504, 63)]
    def correlation(lag):
        left, right = values[:-lag], values[lag:]
        a, b = statistics.fmean(left), statistics.fmean(right)
        denominator = math.sqrt(math.fsum((x-a)**2 for x in left) * math.fsum((x-b)**2 for x in right))
        return math.fsum((x-a)*(y-b) for x,y in zip(left,right)) / denominator if denominator else None
    return {'sessions': 504, 'mean_daily_net_excess': mean, 'group_means': groups,
            'half_means': [statistics.fmean(values[:252]), statistics.fmean(values[252:])],
            'lag_correlations': {str(lag): correlation(lag) for lag in (1, 5, 21, 63)},
            'variance': variance,
            'standardized_fourth_moment': statistics.fmean([x**4 for x in centered]) / variance**2 if variance else None,
            'zero_fraction': values.count(0.) / len(values),
            'maximum_absolute_daily_excess': max(abs(value) for value in values),
            'assumptions_established': False, 'interpretation': 'DESCRIPTIVE_NOT_A_VALIDITY_TEST'}


def _reference_family(excess):
    raw = {}
    for name, values in excess.items():
        groups = describe_process(values)['group_means']
        sd = statistics.stdev(groups)
        raw[name] = (1. if all(value == 0 for value in values) else None if sd == 0 else
                     reference_t7_upper_tail(math.sqrt(8) * statistics.fmean(groups) / sd))
    adjusted, running = {}, 0.
    ordered = sorted(raw, key=lambda key: (1. if raw[key] is None else raw[key], key))
    for index, name in enumerate(ordered):
        running = max(running, (len(raw)-index) * (1. if raw[name] is None else raw[name]))
        adjusted[name] = None if raw[name] is None else min(1., running)
    return {'raw_p': raw, 'adjusted_p': adjusted}


def diagnose_settled_family(accounts_root, calibration_path, *, preregistration_path):
    """只读已暴露的完整五候选家族；无布尔批准参数，无正式资格副作用。"""
    from .formal_assessment_v1 import _load_method
    root = _root(accounts_root)
    scope = _read(root / 'SCOPE.json')
    preregistration = _read(_root(preregistration_path))
    _require(preregistration.get('policy') == DIAGNOSTIC_POLICY
             and preregistration.get('scope_hash') == stable_hash(scope)
             and preregistration.get('accounts_root') == str(root)
             and preregistration.get('source_hash') == _source_hash(), 'PREREGISTRATION_CONFLICT')
    frozen_at = datetime.fromisoformat(preregistration['frozen_at'])
    _require(frozen_at.tzinfo is not None and frozen_at <= datetime.now(timezone.utc), 'PREREGISTRATION_TIME_INVALID')
    members = [f'CANDIDATE_{i:03d}' for i in range(1, 6)]
    expected = {'BENCHMARK_BASE'} | {f'{name}_{cost}' for name in members for cost in ('BASE', 'STRESS')}
    _require(set(scope['plans']) == expected, 'COMPLETE_FROZEN_FAMILY_REQUIRED')
    calibration = _load_method(calibration_path)
    _require(calibration.get('method_hash') == method.METHOD_HASH, 'V2_METHOD_REQUIRED')
    results = {name: settled_result(root, name, plan=plan, input_identity=scope['input_identity'])
               for name, plan in scope['plans'].items()}
    benchmark = results['BENCHMARK_BASE']
    reference = account_series(benchmark)
    dates = [row['date'] for row in reference]
    _require(len(dates) == 504 and len(set(dates)) == 504 and dates == sorted(dates), 'DATE_SEQUENCE_INVALID')
    excess, descriptions, economics = {}, {}, {}
    for name in members:
        base, stress = results[name + '_BASE'], results[name + '_STRESS']
        economics[name] = screen(base, stress, benchmark)
        for suffix, result in [('BASE', base), ('STRESS', stress)]:
            values = [a['net_return']-b['net_return'] for a,b in zip(account_series(result), reference)]
            descriptions[name + '_' + suffix] = describe_process(values)
            if suffix == 'BASE':
                excess[name] = values
    for result in results.values():
        _require(result['strategy_plan']['backend']['window'] == scope['window'], 'WINDOW_CONFLICT')
        _require(result['chain']['execution_assumptions']['initial_cash'] ==
                 result['strategy_plan']['backend']['initial_cash'], 'CAPITAL_CONFLICT')
    statistics_report = method.family_test(excess, alpha=method.METHOD_SPEC['alpha_budgets'][0] * .5)
    independent = _reference_family(excess)
    for category in ('raw_p', 'adjusted_p'):
        for name in members:
            actual, expected_value = statistics_report[category][name], independent[category][name]
            _require((actual is None and expected_value is None) or
                     (actual is not None and expected_value is not None and abs(actual-expected_value) < 1e-10),
                     'INDEPENDENT_NUMERICAL_CHECK_FAILED')
    body = {'schema_version': 'METHOD_APPLICABILITY_REPORT_V1', 'policy': DIAGNOSTIC_POLICY,
            'preregistration_hash': stable_hash(preregistration), 'scope_hash': stable_hash(scope),
            'method_hash': method.METHOD_HASH, 'calibration_hash': stable_hash(calibration),
            'calibration_summary': calibration['summary'], 'input_identity': scope['input_identity'],
            'window': scope['window'], 'account_dates_hash': stable_hash(dates),
            'execution_scope': {name: {'plan_id': result['strategy_plan']['plan_id'],
                'rule_identity': result['rule_identity'],
                'execution_assumptions': result['chain']['execution_assumptions'],
                'result_hash': stable_hash(result)} for name,result in results.items()},
            'process_diagnostics': descriptions, 'economic_diagnostics': economics,
            'exploratory_statistics': statistics_report, 'independent_numerical_reference': independent,
            'numerical_check': 'PASSED', 'method_calibration_approved': calibration['method_approved'],
            'applicability': 'INSUFFICIENT_EVIDENCE', 'strategy_qualified': False,
            'independent_confirmation_eligible': False,
            'reason_codes': ['REAL_RETURN_PROCESS_APPLICABILITY_NOT_ESTABLISHED',
                             'COMMON_GROUP_EXPECTATION_NOT_ESTABLISHED',
                             'INDEPENDENT_NORMAL_GROUP_MEANS_NOT_ESTABLISHED',
                             'EXPOSED_HISTORY_NOT_INDEPENDENT_CONFIRMATION'],
            'next_action': 'PREREGISTER_AND_VALIDATE_A_JUSTIFIED_NEW_METHOD_OR_RETAIN_BLOCK',
            'interpretation': 'FINITE_DGP_CALIBRATION_AND_DESCRIPTIVE_ACCOUNTS_DO_NOT_PROVE_MARKET_ASSUMPTIONS'}
    return {**body, 'report_hash': stable_hash(body)}
