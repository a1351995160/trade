"""状态规则的固定经济初筛；双输入身份同源冻结，保留完整失败家族。"""
from datetime import datetime
from pathlib import Path

from .bounded_research_v1 import _read
from .bounded_research_v2 import diagnose_rule_result
from .common import stable_hash
from .exploration_governance import read_json
from .research_screening_v1 import POLICY, _require, account_series
from .rule_account_backend_v2 import RuleAccountBackendV2
from .research_rule_strategy_v2 import ResearchRuleStrategyV2
from .strategy_interface_v1 import prepare, run


def prepare_account(proposal, *, strategy_id, window, costs='BASE'):
    if proposal is None:
        from .historical_process_v1 import prepare_historical_account
        return prepare_historical_account(None, strategy_id=strategy_id, window=window, costs=costs)
    return prepare(ResearchRuleStrategyV2(proposal, strategy_id=strategy_id), RuleAccountBackendV2(window, costs))


def run_account(proposal, *, strategy_id, bundle, window, costs, input_identity, active_check):
    if proposal is None:
        from .historical_process_v1 import run_historical_account
        return run_historical_account(None, strategy_id=strategy_id, bundle=bundle, window=window,
            costs=costs, input_identity=input_identity, active_check=active_check)
    return run(ResearchRuleStrategyV2(proposal, strategy_id=strategy_id), RuleAccountBackendV2(window, costs),
        frame=bundle, actions=bundle['events'], input_identity=input_identity, active_check=active_check)


def rule_series(result):
    diagnostic = diagnose_rule_result(result, trial_id=result['strategy_plan']['strategy']['strategy_id'])
    _require(diagnostic['account_state'] == 'COMPLETE'
             and result['status'] == 'HISTORICAL_MODELED_ACCOUNT_COMPLETED', 'RULE_ACCOUNT_EVIDENCE_INVALID')
    previous, returns = diagnostic['metrics']['initial_cash'], []
    for row in result['daily_accounts']:
        returns.append({'date': row['date'], 'net_return': row['equity']/previous-1})
        previous = row['equity']
    return returns


def screen(base, stress, benchmark, *, config):
    from .formal_account_backend_v1 import BASE_COSTS, STRESS_COSTS
    rows = [rule_series(base), rule_series(stress), account_series(benchmark)]
    dates = [r['date'] for r in rows[0]]
    window = config['window']
    _require(len(window['symbols']) == 2 and len(dates) == POLICY['account_sessions']
             and dates == [d for d in window['calendar'] if window['account_start'] <= d <= window['account_end']]
             and all([r['date'] for r in series] == dates for series in rows), 'PAIRED_504_DAYS_REQUIRED')
    _require(base['input_identity'] == stress['input_identity'] == config['input_identity']
             and benchmark['input_identity'] == config['benchmark_input_identity'], 'INPUT_CONFLICT')
    _require(base['strategy_plan']['strategy']['parameters']['rule_identity']
             == stress['strategy_plan']['strategy']['parameters']['rule_identity'], 'STRESS_RULE_CHANGED')
    for result, costs in ((base, BASE_COSTS), (stress, STRESS_COSTS)):
        plan = result['strategy_plan']
        strategy = plan['strategy']
        _require(plan == prepare_account(strategy['parameters']['candidate_payload'],
            strategy_id=strategy['strategy_id'], window=window,
            costs='BASE' if costs == BASE_COSTS else 'STRESS'), 'RULE_PLAN_CHANGED')
    bp = benchmark['strategy_plan']
    _require(bp == prepare_account(None, strategy_id=bp['strategy']['strategy_id'], window=window),
             'BUY_HOLD_BENCHMARK_REQUIRED')
    excess = [a['net_return'] - b['net_return'] for a, b in zip(rows[0], rows[2])]
    halves = [sum(excess[:252]), sum(excess[252:])]
    failures = []
    if base['metrics']['net_return'] <= 0:
        failures.append('NONPOSITIVE_NET_RETURN')
    if stress['metrics']['net_return'] <= 0:
        failures.append('COST_STRESS_FAILED')
    if max(base['metrics']['max_drawdown'], stress['metrics']['max_drawdown']) > POLICY['maximum_drawdown']:
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
    if result['strategy_plan']['backend'].get('backend') == 'RULE_ACCOUNT_BACKEND_V2':
        rule_series(result)
    else:
        account_series(result)
    return result



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
    _require(config.get('version') == 'DIAGNOSIS_RESEARCH_V2'
             and config['policy'] == POLICY and stable_hash(config) == scope['run_config_hash']
             and config['input_identity'] == scope['input_identity']
             and config['benchmark_input_identity'] == scope['benchmark_input_identity'], 'PREDECLARATION_CHANGED')
    for archived in archives:
        session = archived['evidence']['session']
        _require(session['input_manifest'].get('diagnosis_config_hash') == stable_hash(config)
                 and Path(archived['origin']['source_root']) == root.parent / 'search'
                 and datetime.fromisoformat(config['frozen_at']) <= datetime.fromisoformat(session['recorded_at'])
                 <= datetime.fromisoformat(archived['evidence']['candidate']['frozen_at']), 'PREDECLARATION_REQUIRED')
    reports = {}
    benchmark = settled_result(root, 'BENCHMARK_BASE', plan=scope['benchmark_plan'], input_identity=scope['benchmark_input_identity'])
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
        reports[name] = screen(results['BASE'], results['STRESS'], benchmark, config=config)
        _require(reports[name] == _read(root / name / 'SCREEN.json'), 'DECISION_CHANGED')
    report = {'policy': POLICY, 'scope_hash': stable_hash(scope), 'reports': reports,
              'selected': sorted(k for k, v in reports.items() if v['passed']), 'strategy_qualified': False}
    _require(report == _read(root / 'SCREENING.json'), 'SUMMARY_CHANGED')
    return report
