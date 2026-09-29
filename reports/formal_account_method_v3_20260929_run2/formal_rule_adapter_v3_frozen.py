"""V3 显式账户范围适配；方法证据与账户执行分离。"""
from copy import deepcopy
from .common import stable_hash
from .research_rule_strategy_v3 import CAPABILITY, ResearchRuleStrategyV3
from .rule_account_backend_v2 import RuleAccountBackendV2
from .strategy_interface_v1 import prepare, run

METHOD_ID = 'FORMAL_ACCOUNT_METHOD_V3_T7_SCOPE_STUDY'


def execution_scope(manifest, symbols=None):
    value = deepcopy(manifest)
    pool = value.get('symbols', value.get('window', {}).get('symbols'))
    if (not isinstance(pool, list) or not pool or len(set(pool)) != len(pool)
            or any(not isinstance(s, str) for s in pool)
            or (symbols is not None and sorted(symbols) != sorted(pool))):
        raise ValueError('FORMAL_V3_POOL_INVALID')
    if (type(value.get('initial_cash')) not in (int, float) or value['initial_cash'] != 50000
            or type(value.get('max_positions')) is not int or not 1 <= value['max_positions'] <= len(pool)
            or type(value.get('max_symbol_exposure_bps')) is not int
            or not 1 <= value['max_symbol_exposure_bps'] <= 10000):
        raise ValueError('FORMAL_V3_EXECUTION_SCOPE_UNSUPPORTED')
    return {'initial_cash': 50000, 'symbols': sorted(pool), 'max_positions': value['max_positions'],
            'max_symbol_exposure_bps': value['max_symbol_exposure_bps'],
            'capability': CAPABILITY, 'sessions': value.get('sessions'),
            'benchmark': value.get('benchmark'), 'holding_process': value.get('holding_process')}


def method_resolver(scope):
    """当前没有经过发布批准的真实范围；调用方不能提交批准布尔值。"""
    return {'applicable': False, 'method_id': METHOD_ID, 'evidence_hash': None,
            'scope_hash': stable_hash(scope), 'profile': 'METHOD_RESEARCH_ONLY',
            'reason_codes': ['REAL_RETURN_PROCESS_APPLICABILITY_NOT_ESTABLISHED'],
            'decision': 'UNSUPPORTED', 'strategy_qualified': False}


def backend(window, costs, scope, *, execution_profile='OBSERVED'):
    binding = execution_scope(scope, window['symbols'])
    return RuleAccountBackendV2(window, costs, initial_cash=binding['initial_cash'],
        max_positions=binding['max_positions'], max_symbol_exposure_bps=binding['max_symbol_exposure_bps'],
        execution_profile=execution_profile)


def prepare_rule(proposal, *, strategy_id, window, costs, scope, execution_profile='OBSERVED'):
    return prepare(ResearchRuleStrategyV3(proposal, strategy_id=strategy_id),
                   backend(window, costs, scope, execution_profile=execution_profile))


def run_rule(proposal, *, strategy_id, bundle, window, costs, scope, input_identity, active_check,
             execution_profile='OBSERVED'):
    return run(ResearchRuleStrategyV3(proposal, strategy_id=strategy_id),
        backend(window, costs, scope, execution_profile=execution_profile), frame=bundle,
        actions=bundle['events'], input_identity=input_identity, active_check=active_check)


def require_publication(scope, *, profile):
    decision = method_resolver(scope)
    if profile != 'REAL_OBSERVED' or not decision['applicable']:
        raise PermissionError('FORMAL_V3_METHOD_PUBLICATION_NOT_APPROVED')
    return decision
