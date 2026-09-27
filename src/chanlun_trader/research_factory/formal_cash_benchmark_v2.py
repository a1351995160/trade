"""有现金分红的原买持基准核验；不改旧无事件验证器或基准交易定义。"""
from .common import stable_hash
from .formal_account_backend_v1 import (
    FormalAccountBackendV1, prepare_formal_account, run_formal_account, window_input_identity,
)


def _require(condition, reason):
    if not condition:
        raise ValueError('FORMAL_CASH_BENCHMARK_' + reason)


def _trajectory(result):
    chain = result['chain']
    return {'decisions': result['decisions'], 'orders': chain['orders'], 'trades': chain['trades'],
        'fills': result['fills'], 'sizing_skips': chain['sizing_skips'],
        'independent_account_checks': chain['independent_account_checks'], 'ledger': result['ledger'],
        'daily_returns': result['daily_returns'], 'metrics': result['metrics'],
        'report_metrics': result['report']['metrics'], 'valuation': chain['official_valuation'],
        'corporate_account': chain['corporate_account'], 'assumptions': chain['execution_assumptions'],
        'issues': chain['issues'], 'status': result['status'], 'chain_status': chain['status'],
        'n_trades': chain['n_trades'], 'n_orders': chain['n_orders'],
        'n_account_days': chain['n_account_days'], 'account_dates': chain['account_dates']}


def validate_cash_benchmark(result, *, bundle, window, costs='BASE', active_check):
    """复用同一已消费授权，重放真正等权买持及原独立公司行动核账。

    不调用研究治理、不建立新候选或确认机会。缺少原授权时拒绝；不能自行
    制造 receipt。比较完整确定性轨迹，不比较运行耗时等非经济元数据。
    """
    _require(bool(bundle.get('events')), 'EVENT_REQUIRED')
    _require(callable(active_check), 'EXISTING_AUTHORIZATION_REQUIRED')
    backend = FormalAccountBackendV1(window, costs)
    backend._validate_bundle(bundle)
    backend._observed_sessions(bundle)
    try:
        plan = result['strategy_plan']
        strategy = plan['strategy']
        strategy_id = strategy['strategy_id']
        _require(strategy['parameters']['candidate_payload'] is None, 'BUY_AND_HOLD_ONLY')
        expected_plan = prepare_formal_account(None, strategy_id=strategy_id, window=window, costs=costs)
        _require(plan == expected_plan, 'PLAN_CONFLICT')
        _require(result['rule_identity'] == strategy['parameters']['rule_identity'], 'RULE_IDENTITY_CONFLICT')
        input_identity = window_input_identity(bundle, window)
        _require(result['input_identity'] == input_identity, 'INPUT_IDENTITY_CONFLICT')
        _require(result['status'] == result['chain']['status'] == 'RECONCILED_DIAGNOSTIC'
                 and result['chain']['issues'] == [], 'ACCOUNT_NOT_RECONCILED')
        expected_events = [event for event in bundle['events'] if event['record_date'] >= window['account_start']]
        _require(result['chain']['corporate_account']['events'] == expected_events, 'EVENT_SCOPE_CONFLICT')
        saved = _trajectory(result)
        # 原 run_chain 按成交及原始条款独立重建现金、红利、税与逐日权益。
        # 再比较保存报告，避免只检查其内部现金自洽却漏掉被删除的完整交易回合。
        repeated = run_formal_account(None, strategy_id=strategy_id, bundle=bundle, window=window,
            costs=costs, input_identity=input_identity, active_check=active_check)
        _require(stable_hash(saved) == stable_hash(_trajectory(repeated)), 'FROZEN_TRAJECTORY_CONFLICT')
        return {'status': 'VERIFIED', 'verifier': 'CASH_BUY_AND_HOLD_SAVED_RESULT_V2',
                'input_identity': input_identity, 'account_days': len(result['daily_returns']),
                'trade_count': len(result['fills']), 'strategy_qualified': False}
    except (KeyError, TypeError, IndexError, AttributeError) as error:
        raise ValueError('FORMAL_CASH_BENCHMARK_STRUCTURE_INVALID') from error
