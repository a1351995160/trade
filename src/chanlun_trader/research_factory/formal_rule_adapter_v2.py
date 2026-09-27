"""将状态规则接到原正式家族账本；保留两股买持基准及适用性阻断。"""
from .bounded_research_v2 import diagnose_rule_result
from .research_rule_strategy_v2 import CAPABILITY, ResearchRuleStrategyV2
from .rule_account_backend_v2 import RuleAccountBackendV2
from .strategy_interface_v1 import prepare, run
from .common import stable_hash


def is_rule(proposal):
    return isinstance(proposal, dict) and proposal.get('version') == CAPABILITY


def execution_scope(manifest, symbols):
    window = manifest.get('window', {})
    binding = {'initial_cash': manifest.get('initial_cash', 1_000_000),
               'max_positions': manifest.get('max_positions', len(window.get('symbols', []))),
               'max_symbol_exposure_bps': manifest.get('max_symbol_exposure_bps', 5000),
               'symbols': sorted(window.get('symbols', []))}
    if (len(symbols) != 2
            or binding['initial_cash'] != 1_000_000
            or binding['max_positions'] != 2
            or binding['max_symbol_exposure_bps'] != 5000
            or binding['symbols'] != sorted(symbols)):
        raise ValueError('FORMAL_RULE_EXECUTION_SCOPE_UNSUPPORTED')
    return binding


def backend(window, costs):
    return RuleAccountBackendV2(window, costs, initial_cash=1_000_000,
                                max_positions=2, max_symbol_exposure_bps=5000,
                                execution_profile='OBSERVED')


def prepare_rule(proposal, *, strategy_id, window, costs):
    return prepare(ResearchRuleStrategyV2(proposal, strategy_id=strategy_id), backend(window, costs))


def run_rule(proposal, *, strategy_id, bundle, window, costs, input_identity, active_check):
    return run(ResearchRuleStrategyV2(proposal, strategy_id=strategy_id), backend(window, costs),
               frame=bundle, actions=bundle['events'], input_identity=input_identity, active_check=active_check)


def account_view(result):
    """只派生评审所需收益视图；不改原结果、不构造旧 chain。"""
    diagnostic = diagnose_rule_result(result, trial_id=result['strategy_plan']['strategy']['strategy_id'])
    if diagnostic['account_state'] != 'COMPLETE' or result['status'] != 'OBSERVED_ACCOUNT_COMPLETED':
        raise ValueError('FORMAL_RULE_ACCOUNT_EVIDENCE_INVALID')
    previous = diagnostic['metrics']['initial_cash']
    returns = []
    for row in result['daily_accounts']:
        returns.append({'date': row['date'], 'net_return': row['equity']/previous-1})
        previous = row['equity']
    return {'metrics': {**result['metrics'], 'max_drawdown': -result['metrics']['max_drawdown']},
            'daily_returns': returns}


def validate_result(result, *, bundle, window, costs, active_check):
    from .formal_account_backend_v1 import window_input_identity
    view = account_view(result)
    expected_days = [day for day in window['calendar'] if day >= window['account_start']]
    if ([row['date'] for row in view['daily_returns']] != expected_days
            or result['input_identity'] != window_input_identity(bundle, window)
            or result['strategy_plan']['backend'] != backend(window, costs).describe()
            or result['profile'] not in ('REAL_OBSERVED', 'SYNTHETIC')):
        raise ValueError('FORMAL_RULE_ACCOUNT_BINDING_CONFLICT')
    # 同一已消费账户的确定性核对，复用原授权，不创建候选或新的确认机会。
    strategy = result['strategy_plan']['strategy']
    replay = run_rule(strategy['parameters']['candidate_payload'], strategy_id=strategy['strategy_id'],
        bundle=bundle, window=window, costs=costs,
        input_identity=result['input_identity'], active_check=active_check)
    if stable_hash(replay) != stable_hash(result):
        raise ValueError('FORMAL_RULE_ACCOUNT_REPLAY_CONFLICT')
    return view
