"""预登记固定窗口的近似日净超额均值检验；本模块不授予策略资格。"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

import numpy as np

from .bounded_research_v1 import _read
from .common import stable_hash
from .holm_family_v1 import adjust_family


METHOD_SPEC = {
    'version': 'FORMAL_STATISTICS_V1', 'sessions': 504, 'max_family': 5,
    'blocks': [10, 20, 40], 'bootstrap_draws': 4095, 'seed': 20260926,
    'statistic': 'MEAN_DAILY_NET_SIMPLE_EXCESS_RETURN',
    'null': 'STATIONARY_UNCONDITIONAL_EXPECTED_DAILY_EXCESS_LE_ZERO',
    'bootstrap': 'CENTERED_STATIONARY_COMMON_TIME_INDICES',
    'pvalue': 'MAX_OVER_BLOCKS_OF_GREATER_EQUAL_PLUS_ONE', 'adjustment': 'HOLM',
    'alpha_budgets': [.025, .015, .010], 'test_alpha_fraction': .5,
    'constant_rule': 'ZERO_P1_NONZERO_UNTESTABLE',
    'assumptions': ['WEAK_STATIONARITY', 'SHORT_MEMORY', 'FINITE_HIGHER_MOMENTS'],
    'interpretation': 'APPROXIMATE_CONDITIONAL_ON_ASSUMPTIONS_NOT_FINITE_SAMPLE_GUARANTEE',
}
SUPPORT = ('IID', 'SKEW', 'AR_NEG03', 'AR03', 'AR06', 'MA10', 'COMMON_SHOCK', 'T5', 'GARCH')
STRESS = ('AR09', 'T3', 'MEAN_BREAK', 'TREND')
FAMILIES = ('ALL_NULL', 'ONE_TRUE_NULL', 'THREE_TRUE_NULLS')
CALIBRATION_SPEC = {
    'version': 'FORMAL_STATISTICS_CALIBRATION_V1', 'method': METHOD_SPEC,
    'replicates': 2048, 'family_size': 5, 'seed': 20260926, 'burn_in': 1024,
    'support': list(SUPPORT), 'stress': list(STRESS), 'families': list(FAMILIES),
    'support_checks': len(SUPPORT) * len(FAMILIES) * 3,
    'simultaneous_error': .05, 'acceptance': 'EVERY_SIMULTANEOUS_CP_UPPER_LE_BATCH_BUDGET',
    'dgp': {'IID': 'NORMAL_0_1', 'SKEW': '(CHI_SQUARE_3-3)/SQRT_6',
            'AR_NEG03': -.3, 'AR03': .3, 'AR06': .6, 'AR09': .9,
            'MA10': 'SUM_LAST_10_NORMAL/SQRT_10', 'COMMON_SHOCK': 'SQRT_.75_COMMON+SQRT_.25_PRIVATE',
            'T5': 'T5*SQRT_3/5', 'T3': 'T3/SQRT_3',
            'GARCH': {'omega': .05, 'alpha': .05, 'beta': .90, 'initial_variance': 1.},
            'MEAN_BREAK': 'NORMAL+FIRST_HALF_.5_SECOND_HALF_MINUS_.5',
            'TREND': 'NORMAL+LINEAR_MINUS_.5_TO_.5'},
    'mixed_alternative_daily_mean': 2 / math.sqrt(252),
    'power_iid_annual_sharpe': [.5, 1., 2.],
    'randomness': 'SEED_SEQUENCE_MASTER_CONDITION_REPLICATE_STREAM_INDEPENDENT_PER_REPLICATE',
    'scope': 'FINITE_FIXED_DGP_DIAGNOSTIC_NOT_GENERAL_VALIDITY_PROOF',
}
METHOD_HASH = stable_hash(METHOD_SPEC)


def stationary_means(values, *, block, draws, seed):
    """几何连续段的循环前缀和；等价于逐日概率1/block重启的stationary bootstrap。"""
    values = np.asarray(values, dtype=float)
    if values.ndim != 2 or not np.isfinite(values).all() or min(values.shape) < 1:
        raise ValueError('FORMAL_BOOTSTRAP_INPUT_INVALID')
    if type(block) is not int or block < 1 or type(draws) is not int or draws < 1:
        raise ValueError('FORMAL_BOOTSTRAP_DIMENSIONS_INVALID')
    n, members = values.shape
    rng = np.random.default_rng(seed)
    prefix = np.vstack([np.zeros((1, members)), np.cumsum(np.tile(values, (2, 1)), axis=0)])
    totals = np.zeros((draws, members))
    remaining = np.full(draws, n, dtype=np.int64)
    active = np.arange(draws)
    while len(active):
        lengths = np.minimum(rng.geometric(1 / block, size=len(active)), remaining[active])
        starts = rng.integers(0, n, size=len(active))
        totals[active] += prefix[starts + lengths] - prefix[starts]
        remaining[active] -= lengths
        active = active[remaining[active] > 0]
    return totals / n


def _pvalues_for_shifts(x, *, seed, shifts):
    """平移只改变观测均值；共享同一次居中抽样评估预登记校准备择。"""
    observed = x.mean(axis=0)
    centered = x - observed
    constant = np.ptp(x, axis=0) == 0
    answer = {key: np.zeros(x.shape[1]) for key in shifts}
    for block in ([] if constant.all() else METHOD_SPEC['blocks']):
        null = stationary_means(centered, block=block, draws=METHOD_SPEC['bootstrap_draws'],
                                seed=np.random.SeedSequence([*seed, block]))
        for key, shift in shifts.items():
            p = (1 + np.sum(null >= observed + shift, axis=0)) / (len(null) + 1)
            answer[key] = np.maximum(answer[key], p)
    for key, shift in shifts.items():
        answer[key][constant & ((observed + shift) == 0)] = 1.
        answer[key][constant & ((observed + shift) != 0)] = np.nan
    return answer


def family_test(excess, *, alpha, seed=20260926):
    """alpha是实际检验水平；调用方负责批次预算、独立窗口和资格裁决。"""
    if not isinstance(excess, dict) or not 1 <= len(excess) <= METHOD_SPEC['max_family']:
        raise ValueError('FORMAL_FROZEN_FAMILY_REQUIRED')
    if any(not isinstance(key, str) or not key for key in excess):
        raise ValueError('FORMAL_MEMBER_ID_INVALID')
    if isinstance(alpha, bool) or not isinstance(alpha, (int, float)) or not 0 < alpha < 1:
        raise ValueError('FORMAL_ALPHA_INVALID')
    if type(seed) is not int or seed < 0:
        raise ValueError('FORMAL_SEED_INVALID')
    members = sorted(excess)
    columns, invalid = [], {}
    for key in members:
        values = np.asarray(excess[key], dtype=float)
        if values.shape != (METHOD_SPEC['sessions'],):
            raise ValueError('FORMAL_COMPLETE_504_SESSION_WINDOW_REQUIRED')
        if not np.isfinite(values).all():
            invalid[key] = 'NONFINITE_MEMBER_UNTESTABLE'
            values = np.zeros_like(values)
        elif np.ptp(values) == 0 and values[0] != 0:
            invalid[key] = 'NONZERO_CONSTANT_UNTESTABLE'
        columns.append(values)
    x = np.column_stack(columns)
    calculated = _pvalues_for_shifts(x, seed=[seed], shifts={'NULL': np.zeros(len(members))})['NULL']
    raw = {key: None if key in invalid or not math.isfinite(calculated[i]) else float(calculated[i])
           for i, key in enumerate(members)}
    corrected = adjust_family(raw, members)
    return {'schema_version': 'FORMAL_FAMILY_TEST_V1', 'method_hash': METHOD_HASH,
            'raw_p': raw, 'adjusted_p': corrected['adjusted_p'], 'family_size': len(members),
            'supported': {key: p is not None and p <= alpha for key, p in corrected['adjusted_p'].items()},
            'untestable': invalid, 'alpha': alpha, 'seed': seed, 'qualification': 'NOT_ASSESSED',
            'interpretation': METHOD_SPEC['interpretation']}


def binomial_cdf(k, n, p):
    if type(n) is not int or n < 1 or type(k) is not int or not 0 <= k <= n or not 0 <= p <= 1:
        raise ValueError('BINOMIAL_INPUT_INVALID')
    if p == 0 or k == n:
        return 1.
    if p == 1:
        return 0.
    terms = [math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1)
             + i * math.log(p) + (n - i) * math.log1p(-p) for i in range(k + 1)]
    peak = max(terms)
    return min(1., math.exp(peak) * math.fsum(math.exp(value - peak) for value in terms))


def cp_upper(rejections: int, replicates: int, tail_probability: float) -> float:
    """精确二项分布反演的单侧Clopper–Pearson上界，无scipy依赖。"""
    if type(rejections) is not int:
        raise ValueError('CP_INPUT_INVALID')
    if type(replicates) is not int:
        raise ValueError('CP_INPUT_INVALID')
    if replicates < 1:
        raise ValueError('CP_INPUT_INVALID')
    if not 0 <= rejections <= replicates:
        raise ValueError('CP_INPUT_INVALID')
    bounds = (0 < tail_probability, tail_probability < 1)
    if not all(bounds):
        raise ValueError('CP_INPUT_INVALID')
    if rejections == replicates:
        return 1.
    if rejections == 0:
        return -math.expm1(math.log(tail_probability) / replicates)
    low, high = 0., 1.
    for _ in range(64):
        middle = (low + high) / 2
        if binomial_cdf(rejections, replicates, middle) > tail_probability:
            low = middle
        else:
            high = middle
    return high


def generate_dgp(condition, replicate):
    conditions = (*SUPPORT, *STRESS)
    ci = conditions.index(condition)
    data_seed = [CALIBRATION_SPEC['seed'], ci, replicate, 0]
    bootstrap_seed = [CALIBRATION_SPEC['seed'], ci, replicate, 1]
    rng = np.random.default_rng(np.random.SeedSequence(data_seed))
    n, burn, m = METHOD_SPEC['sessions'], CALIBRATION_SPEC['burn_in'], 5
    x = rng.normal(size=(n + burn, m))
    if condition == 'SKEW':
        x = (rng.chisquare(3, size=x.shape) - 3) / math.sqrt(6)
    elif condition.startswith('AR'):
        rho = CALIBRATION_SPEC['dgp'][condition]
        for i in range(1, len(x)):
            x[i] = rho * x[i - 1] + math.sqrt(1 - rho * rho) * x[i]
    elif condition == 'MA10':
        sums = np.vstack([np.zeros((1, m)), np.cumsum(x, axis=0)])
        x = (sums[10:] - sums[:-10]) / math.sqrt(10)
    elif condition == 'COMMON_SHOCK':
        x = math.sqrt(.75) * rng.normal(size=(len(x), 1)) + .5 * x
    elif condition in ('T5', 'T3'):
        df = 5 if condition == 'T5' else 3
        x = rng.standard_t(df, size=x.shape) * math.sqrt((df - 2) / df)
    elif condition == 'GARCH':
        variance = np.ones(m)
        previous = x[0].copy()
        for i in range(1, len(x)):
            variance = .05 + .05 * previous * previous + .90 * variance
            x[i] *= np.sqrt(variance)
            previous = x[i].copy()
    x = x[-n:]
    if condition == 'MEAN_BREAK':
        x += np.where(np.arange(n) < n // 2, .5, -.5)[:, None]
    elif condition == 'TREND':
        x += np.linspace(-.5, .5, n)[:, None]
    return x, data_seed, bootstrap_seed


def calibration_record(condition, replicate):
    x, data_seed, bootstrap_seed = generate_dgp(condition, replicate)
    delta = CALIBRATION_SPEC['mixed_alternative_daily_mean']
    shifts = {'ALL_NULL': np.zeros(5), 'ONE_TRUE_NULL': np.array([0., delta, delta, delta, delta]),
              'THREE_TRUE_NULLS': np.array([0., 0., 0., delta, delta])}
    if condition == 'IID':
        shifts.update({f'POWER_SR_{sr:g}': np.full(5, sr / math.sqrt(252))
                       for sr in CALIBRATION_SPEC['power_iid_annual_sharpe']})
    pvalues = _pvalues_for_shifts(x, seed=bootstrap_seed, shifts=shifts)
    return {'condition': condition, 'replicate': replicate, 'data_seed': data_seed,
            'bootstrap_seed': bootstrap_seed, 'data_hash': hashlib.sha256(x.tobytes()).hexdigest(),
            'raw_p': {key: value.tolist() for key, value in pvalues.items()}}


def summarize_records(records):
    expected = {(condition, replicate) for condition in (*SUPPORT, *STRESS)
                for replicate in range(CALIBRATION_SPEC['replicates'])}
    seen, counts, power = set(), {}, {}
    for record in records:
        key = (record['condition'], record['replicate'])
        if key not in expected or key in seen:
            raise ValueError('CALIBRATION_RECORD_MEMBERSHIP_CONFLICT')
        seen.add(key)
        condition, replicate = key
        ci = (*SUPPORT, *STRESS).index(condition)
        if (record['data_seed'] != [CALIBRATION_SPEC['seed'], ci, replicate, 0]
                or record['bootstrap_seed'] != [CALIBRATION_SPEC['seed'], ci, replicate, 1]):
            raise ValueError('CALIBRATION_SEED_CONFLICT')
        if (not isinstance(record.get('data_hash'), str) or len(record['data_hash']) != 64
                or any(c not in '0123456789abcdef' for c in record['data_hash'])):
            raise ValueError('CALIBRATION_DATA_HASH_INVALID')
        variants = set(FAMILIES)
        if condition == 'IID':
            variants.update(f'POWER_SR_{sr:g}' for sr in CALIBRATION_SPEC['power_iid_annual_sharpe'])
        if set(record['raw_p']) != variants:
            raise ValueError('CALIBRATION_VARIANT_CONFLICT')
        for variant, raw in record['raw_p'].items():
            if len(raw) != 5 or any(isinstance(p, bool) or not isinstance(p, (float, int))
                                    or not math.isfinite(p) or not 1 / 4096 <= p <= 1
                                    or p * 4096 != int(p * 4096) for p in raw):
                raise ValueError('CALIBRATION_PVALUE_INVALID')
            adjusted = list(adjust_family(dict(enumerate(raw)), tuple(range(5)))['adjusted_p'].values())
            for slot, budget in enumerate(METHOD_SPEC['alpha_budgets']):
                rejected = [p <= budget * METHOD_SPEC['test_alpha_fraction'] for p in adjusted]
                if variant.startswith('POWER_'):
                    entry = power.setdefault((variant, slot), [0, 0])
                    entry[0] += int(any(rejected))
                    entry[1] += sum(rejected)
                else:
                    true_count = {'ALL_NULL': 5, 'ONE_TRUE_NULL': 1, 'THREE_TRUE_NULLS': 3}[variant]
                    index = (condition, variant, slot)
                    counts[index] = counts.get(index, 0) + int(any(rejected[:true_count]))
    if seen != expected:
        raise ValueError('CALIBRATION_INCOMPLETE_RECORDS')
    rows = []
    n = CALIBRATION_SPEC['replicates']
    tail = CALIBRATION_SPEC['simultaneous_error'] / CALIBRATION_SPEC['support_checks']
    for (condition, family, slot), count in sorted(counts.items()):
        upper = cp_upper(count, n, tail)
        budget = METHOD_SPEC['alpha_budgets'][slot]
        rows.append({'condition': condition, 'family': family, 'slot': slot + 1,
                     'replicates': n, 'false_family_rejections': count, 'fwer': count / n,
                     'cp_upper': upper, 'alpha_budget': budget, 'test_alpha': budget / 2,
                     'support_domain': condition in SUPPORT,
                     'passed': upper <= budget if condition in SUPPORT else None})
    return {'support_passed': all(row['passed'] for row in rows if row['support_domain']),
            'conditions': rows, 'power': [{'scenario': variant, 'slot': slot + 1,
                'family_any_rejection_rate': counts[0] / n, 'mean_member_rejection_rate': counts[1] / (n * 5)}
                for (variant, slot), counts in sorted(power.items())],
            'scope': CALIBRATION_SPEC['scope'], 'real_strategy_qualification': False}


def source_hashes():
    root = Path(__file__).resolve().parents[3]
    paths = ('src/chanlun_trader/research_factory/formal_statistics_v1.py',
             'src/chanlun_trader/research_factory/holm_family_v1.py',
             'src/chanlun_trader/research_factory/common.py',
             'scripts/calibrate_formal_statistics_v1.py',
             'tests/research_factory/test_formal_statistics_v1.py')
    return {path: hashlib.sha256((root / path).read_bytes()).hexdigest() for path in paths}


def load_calibration(path):
    path = Path(path)
    if path.resolve() != path.absolute():
        raise ValueError('CALIBRATION_REDIRECTED_PATH')
    report = _read(path)
    prereg = _read(path.parent / 'PREREGISTRATION.json')
    if (prereg['spec'] != CALIBRATION_SPEC or prereg['sources'] != source_hashes()
            or report['preregistration_hash'] != stable_hash(prereg)
            or report['method_hash'] != METHOD_HASH
            or report['records_hash'] != stable_hash(report['records'])):
        raise ValueError('CALIBRATION_BINDING_CONFLICT')
    summary = summarize_records(report['records'])
    if report['summary'] != summary:
        raise ValueError('CALIBRATION_SUMMARY_CONFLICT')
    return {**report, 'method_approved': summary['support_passed'],
            'interpretation': 'FIXED_DGP_CALIBRATION_ONLY_ASSUMPTIONS_STILL_REQUIRED'}
