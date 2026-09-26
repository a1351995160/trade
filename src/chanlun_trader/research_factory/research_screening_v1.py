"""从已核账的同日账户派生初筛与定性反馈，不授予统计或交易资格。"""
from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path

from .bounded_research_v1 import _read
from .common import stable_hash
from .context import PerformanceBlindGuard
from .exploration_governance import read_json
from .research_diagnostics_v1 import _account


POLICY = {'version': 'RESEARCH_SCREENING_V1', 'account_sessions': 504,
          'minimum_net_return': 0., 'maximum_drawdown': .25,
          'positive_excess_each_half': True, 'costs': ['BASE', 'STRESS'],
          'qualification': 'EXPLORATORY_ONLY'}
REASONS = {
    'NONPOSITIVE_NET_RETURN': '普通交易成本下账户没有取得正收益，需提出新的机制假设。',
    'COST_STRESS_FAILED': '提高费用与滑点后账户没有取得正收益，需考虑更稳定的信号组合。',
    'COST_SENSITIVITY': '提高费用与滑点后账户收益下降；成本敏感是观察，不是亏损的唯一原因。',
    'DRAWDOWN_LIMIT_EXCEEDED': '至少一种成本情景的回撤超过冻结上限，需检验风险暴露。',
    'SUBPERIOD_EXCESS_NOT_POSITIVE': '至少一个冻结时间段没有跑赢持有基准，需检验机制跨阶段稳定性。',
    'SCREEN_PASSED': '历史初筛通过，但仍需独立确认，不代表策略有效。',
}


def _require(value, code):
    if not value:
        raise ValueError('RESEARCH_SCREEN_' + code)


def account_series(result):
    state, metrics = _account(result)
    _require(state == 'COMPLETE', 'ACCOUNT_EVIDENCE_INVALID')
    chain = result['chain']
    _require(result['status'] == 'RECONCILED_DIAGNOSTIC', 'STATUS_INVALID')
    previous, returns = metrics['initial_cash'], []
    for row in chain['independent_account_checks']:
        _require(previous > 0 and row['equity'] > 0, 'NONPOSITIVE_EQUITY')
        returns.append({'date': row['date'], 'net_return': row['equity'] / previous - 1})
        previous = row['equity']
    _require(result['daily_returns'] == returns, 'DAILY_RETURNS_CONFLICT')
    expected = {'net_return': metrics['net_return'], 'max_drawdown': -metrics['max_drawdown'],
                'total_fees': metrics['total_fees'], 'trade_count': metrics['trade_count']}
    _require(set(result['metrics']) == set(expected) and all(
        not isinstance(result['metrics'][k], bool) and math.isfinite(result['metrics'][k])
        and abs(result['metrics'][k] - v) < 1e-9 for k, v in expected.items()), 'METRICS_CONFLICT')
    return returns


def screen(base, stress, benchmark):
    """固定经济初筛；不以显著性检验筛选同一历史上的赢家。"""
    from .formal_account_backend_v1 import BASE_COSTS, STRESS_COSTS
    series = [account_series(result) for result in (base, stress, benchmark)]
    dates = [r['date'] for r in series[0]]
    _require(len(dates) == POLICY['account_sessions'] and all(
        [r['date'] for r in rows] == dates for rows in series), 'PAIRED_504_DAYS_REQUIRED')
    _require(base['input_identity'] == stress['input_identity'] == benchmark['input_identity'], 'INPUT_CONFLICT')
    _require(base['rule_identity'] == stress['rule_identity'] and base['decisions'] == stress['decisions'],
             'STRESS_RULE_CHANGED')
    for result, costs in ((base, BASE_COSTS), (stress, STRESS_COSTS), (benchmark, BASE_COSTS)):
        _require(result['strategy_plan']['backend']['costs'] == costs, 'COST_MODEL_CHANGED')
        assumptions = result['chain']['execution_assumptions']
        for key, field in [('commission_rate', 'fee_commission_rate'), ('min_commission', 'min_commission'),
                           ('stamp_tax_rate', 'sell_stamp_tax_rate'), ('slippage_bps', 'slippage_fraction')]:
            _require(assumptions.get(field) == costs[key], 'EXECUTED_COST_CONFLICT')
        _require(result['rule_identity'] == result['strategy_plan']['strategy']['parameters']['rule_identity'],
                 'EXECUTED_RULE_CONFLICT')
    _require(benchmark['strategy_plan']['strategy']['parameters']['candidate_payload'] is None,
             'BUY_HOLD_BENCHMARK_REQUIRED')
    excess = [a['net_return'] - b['net_return'] for a, b in zip(series[0], series[2])]
    halves = [sum(excess[:252]), sum(excess[252:])]
    failures = []
    if base['metrics']['net_return'] <= 0:
        failures.append('NONPOSITIVE_NET_RETURN')
    if stress['metrics']['net_return'] <= 0:
        failures.append('COST_STRESS_FAILED')
    if min(base['metrics']['max_drawdown'], stress['metrics']['max_drawdown']) < -.25:
        failures.append('DRAWDOWN_LIMIT_EXCEEDED')
    if min(halves) <= 0:
        failures.append('SUBPERIOD_EXCESS_NOT_POSITIVE')
    observations = ['COST_SENSITIVITY'] if stress['metrics']['net_return'] < base['metrics']['net_return'] else []
    return {'policy': POLICY, 'passed': not failures, 'failure_codes': failures,
            'observation_codes': observations, 'sum_daily_excess_each_half': halves,
            'base': base['metrics'], 'stress': stress['metrics'],
            'result_hashes': {k: stable_hash(v) for k, v in
                              [('BASE', base), ('STRESS', stress), ('BENCHMARK_BASE', benchmark)]},
            'strategy_qualified': False}


def qualitative_feedback(report):
    codes = report['failure_codes'] + report['observation_codes']
    if report['passed']:
        codes.append('SCREEN_PASSED')
    value = {'source_history_hash': stable_hash(report),
             'entries': [{'reason_code': code, 'high_level_reason': REASONS[code]} for code in codes]}
    PerformanceBlindGuard.assert_blind(value)
    return value


def settled_result(root, name, *, plan, input_identity):
    """读取同一账户的开始、结算和结果，拒绝仅提交摘要或未结算结果。"""
    root = Path(root)
    paths = [root / (name + '_RESULT.json'), root / 'governance' / (name + '_START.json'),
             root / 'governance' / (name + '_SETTLEMENT.json'), root / 'governance' / 'CONFIRMATION.json',
             root / 'search_budget_registry.json']
    _require(all(p.resolve() == p.absolute() for p in paths), 'PATH_REDIRECTED')
    result = _read(root / (name + '_RESULT.json'))
    start = read_json(root / 'governance' / (name + '_START.json'))
    settlement = read_json(root / 'governance' / (name + '_SETTLEMENT.json'))
    receipt = read_json(root / 'governance' / 'CONFIRMATION.json')
    _require(receipt['receipt_id'] == stable_hash({k: v for k, v in receipt.items() if k != 'receipt_id'})
             and receipt['strategy_plans'][name] == plan and receipt['input_identity'] == input_identity
             and start['receipt_id'] == receipt['receipt_id'] and start['kind'] == name
             and start['counted_before_account_calculation'] is True, 'START_BINDING_CONFLICT')
    _require(settlement['completed'] is True and settlement['error'] is None
             and all(settlement[k] == v for k, v in start.items())
             and settlement['result_sha256'] == stable_hash(result), 'SETTLEMENT_CONFLICT')
    _require(result['strategy_plan'] == plan and result['input_identity'] == input_identity,
             'RESULT_BINDING_CONFLICT')
    budget = read_json(root / 'search_budget_registry.json')
    _require(budget['settled_reservations'].get(start['reservation']) == 'CONSUMED'
             and start['reservation'] not in budget['active_reservations'], 'BUDGET_NOT_CONSUMED')
    account_series(result)
    return result


def seed_diagnostics(root):
    """验证上轮完整五候选，保留失败者，不从摘要挑赢家。"""
    root = Path(root)
    scope = _read(root / 'SCOPE.json')
    expected = {'BENCHMARK_BASE'} | {f'CANDIDATE_{i:03d}_{c}' for i in range(1, 6) for c in ('BASE', 'STRESS')}
    _require(set(scope['plans']) == expected, 'SEED_COMPLETE_FAMILY_REQUIRED')
    results = {name: settled_result(root, name, plan=plan, input_identity=scope['input_identity'])
               for name, plan in scope['plans'].items()}
    reports = {f'CANDIDATE_{i:03d}': screen(results[f'CANDIDATE_{i:03d}_BASE'],
               results[f'CANDIDATE_{i:03d}_STRESS'], results['BENCHMARK_BASE']) for i in range(1, 6)}
    return {'scope_hash': stable_hash(scope), 'reports': reports,
            'feedback': [qualitative_feedback(v) for v in reports.values()],
            'previous_designs': [scope['plans'][f'CANDIDATE_{i:03d}_BASE']['strategy']['parameters']['candidate_payload']
                                 for i in range(1, 6)]}


def validated_screening(root, archives):
    """正式入口从已核账证据重算晋级集合，不接受调用方通过名单。"""
    root = Path(root).absolute()
    _require(root.resolve() == root, 'PATH_REDIRECTED')
    scope = _read(root / 'SCREEN_SCOPE.json')
    _require(scope['policy'] == POLICY, 'POLICY_CHANGED')
    members = {a['origin']['candidate_id']: a for a in archives}
    _require(len(members) == len(archives), 'DUPLICATE_FAMILY_MEMBER')
    _require(scope['archives'] == {k: a['archive_hash'] for k, a in members.items()}, 'FAMILY_CHANGED')
    config = _read(root.parent / 'RUN.json')
    _require(config['policy'] == POLICY and stable_hash(config) == scope['run_config_hash']
             and config['input_identity'] == scope['input_identity'], 'PREDECLARATION_CHANGED')
    for archived in archives:
        session = archived['evidence']['session']
        _require(session['input_manifest'].get('diagnosis_config_hash') == stable_hash(config)
                 and Path(archived['origin']['source_root']) == root.parent / 'search'
                 and datetime.fromisoformat(config['frozen_at']) <= datetime.fromisoformat(session['recorded_at'])
                 <= datetime.fromisoformat(archived['evidence']['candidate']['frozen_at']), 'PREDECLARATION_REQUIRED')
    reports = {}
    benchmark = settled_result(root, 'BENCHMARK_BASE', plan=scope['benchmark_plan'], input_identity=scope['input_identity'])
    for name, archived in members.items():
        frozen = _read(root / name / 'PLANS.json')
        _require(frozen['archive_hash'] == archived['archive_hash'], 'ARCHIVE_CHANGED')
        results = {}
        for cost in ('BASE', 'STRESS'):
            key = name + '_' + cost
            plan = frozen['plans'][key]
            _require(plan['strategy']['parameters']['rule_identity'] == archived['rule_identity']
                     and plan['strategy']['parameters']['candidate_payload'] == archived['proposal']
                     and plan['backend']['window'] == config['window'], 'RULE_CHANGED')
            results[cost] = settled_result(root / name, key, plan=plan, input_identity=scope['input_identity'])
        reports[name] = screen(results['BASE'], results['STRESS'], benchmark)
        _require(reports[name] == _read(root / name / 'SCREEN.json'), 'DECISION_CHANGED')
    report = {'policy': POLICY, 'scope_hash': stable_hash(scope), 'reports': reports,
              'selected': sorted(k for k, v in reports.items() if v['passed']), 'strategy_qualified': False}
    _require(report == _read(root / 'SCREENING.json'), 'SUMMARY_CHANGED')
    return report
