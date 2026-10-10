"""持续研究目标的冻结合同及只读预检；不产生授权、预算或执行事实。"""
from copy import deepcopy
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import re

from .common import stable_hash
from .research_capabilities_v1 import capabilities
from .universe_execution_profile_v1 import SEGMENTED_PROFILE, validate_execution_profile


VERSION = 'CONTINUOUS_RESEARCH_CONTRACT_V1'
STAGES = ('EXPLORATION', 'CONFIRMATION')
SELECTION_POLICY = 'ALL_REGISTERED_TARGETS_THEN_STRATEGY_SIGNALS'
MARKET_FILTER_SCOPE = 'SAME_SYMBOL_ONLY'
FINAL_MINIMUMS = {
    'base_annualized_net_return_minimum': .15,
    'base_maximum_drawdown_maximum': .15,
    'base_complete_round_trip_net_win_rate_minimum': .55,
    'base_complete_round_trip_net_profit_factor_minimum': 1.3,
    'base_complete_round_trips_minimum': 100,
    'stress_total_net_return_strictly_greater_than': 0,
}
REPORT_REQUIREMENTS = ('profit', 'win_rate', 'concentration', 'capital_utilization',
                       'cost_sensitivity', 'subperiods', 'signal_to_fill_funnel')


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _identifier(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,120}', value) is not None


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _day(value):
    _require(isinstance(value, str), 'CONTINUOUS_DATE_INVALID')
    parsed = date.fromisoformat(value)
    _require(parsed.isoformat() == value, 'CONTINUOUS_DATE_INVALID')
    return value


def import_final_criteria(path):
    """仅导入调用者明确指定的标准 JSON；分别记录字节和内容身份。"""
    source = Path(path).resolve(strict=True)
    raw = source.read_bytes()
    criteria = json.loads(raw.decode('utf-8-sig'))
    _validate_final_criteria(criteria)
    return {'criteria': criteria, 'source': {'path': str(source),
        'sha256': hashlib.sha256(raw).hexdigest(), 'content_hash': stable_hash(criteria)}}


def _validate_final_criteria(value):
    _require(isinstance(value, dict), 'CONTINUOUS_FINAL_CRITERIA_INVALID')
    _require(value.get('version') == 'USER_STRATEGY_BUSINESS_ACCEPTANCE_V1'
        and value.get('initial_cash_each_independent_cost_account') == 50000
        and value.get('scope') == 'ALL_REGISTERED_SUPPORTED_TARGETS_THEN_ALL_DATA_QUALIFIED_SYMBOLS'
        and value.get('account_scope') == 'DATA_QUALIFIED'
        and value.get('selection_rule_version') == 'RESEARCH_RULE_STRATEGY_V4',
        'CONTINUOUS_FINAL_SCOPE_INVALID')
    for key, minimum in (('exploration_minimum_actual_sessions', 504),
                         ('independent_validation_minimum_actual_sessions', 252)):
        _require(type(value.get(key)) is int and value[key] >= minimum,
                 'CONTINUOUS_FINAL_SESSION_STANDARD_LOWERED')
    limits = value.get('thresholds_each_stage')
    _require(isinstance(limits, dict) and set(limits) == set(FINAL_MINIMUMS),
             'CONTINUOUS_FINAL_THRESHOLD_FIELDS')
    for key, bound in FINAL_MINIMUMS.items():
        number = limits[key]
        _require(_number(number) and (number <= bound if key.endswith('_maximum') else number >= bound),
                 'CONTINUOUS_FINAL_THRESHOLD_LOWERED')
    _require(type(limits['base_complete_round_trips_minimum']) is int
        and 0 <= limits['base_maximum_drawdown_maximum'] <= 1
        and limits['base_complete_round_trip_net_win_rate_minimum'] <= 1,
        'CONTINUOUS_FINAL_THRESHOLD_INVALID')
    for key in ('account_equity_includes_open_positions',
                'independence_requires_prior_access_review_and_post_freeze_unseen_data',
                'cannot_pool_stage_or_cost_account_profits'):
        _require(value.get(key) is True, 'CONTINUOUS_FINAL_INDEPENDENCE_REQUIRED')
    _require(value.get('formal_statistics') == 'ONLY_IF_CURRENT_METHOD_PROVES_APPLICABLE'
        and value.get('concentration_required') == ['symbol', 'complete_round_trip', 'time_period']
        and value.get('benchmarks') == ['CASH', 'SYSTEM_SUPPORTED_PRICE_REFERENCE_WITH_INVESTABILITY_DISCLOSED'],
        'CONTINUOUS_FINAL_REPORT_POLICY_INVALID')


def _validate_route(route, stage, universe_hash):
    if route is None:
        _require(stage == 'CONFIRMATION', 'CONTINUOUS_EXPLORATION_ROUTE_REQUIRED')
        return
    registered = {'dataset_id', 'dataset_hash', 'start', 'end', 'purpose'}
    future = {'route_id', 'producer_identity', 'source_ids', 'start', 'end',
              'purpose', 'universe_hash', 'quality_policy'}
    _require(isinstance(route, dict) and set(route) in (registered, future),
             'CONTINUOUS_DATA_ROUTE_FIELDS')
    _require(_day(route['start']) <= _day(route['end'])
        and route['purpose'] == ('TRAIN' if stage == 'EXPLORATION' else 'INDEPENDENT_VALIDATION'),
        'CONTINUOUS_DATA_ROUTE_RANGE_OR_PURPOSE')
    if set(route) == registered:
        _require(_identifier(route['dataset_id']) and _hash(route['dataset_hash']),
                 'CONTINUOUS_DATASET_IDENTITY_INVALID')
    else:
        sources = route['source_ids']
        _require(_identifier(route['route_id']) and _identifier(route['producer_identity'])
            and isinstance(sources, list) and sources and all(_identifier(item) for item in sources)
            and len(set(sources)) == len(sources) and route['universe_hash'] == universe_hash
            and isinstance(route['quality_policy'], dict) and route['quality_policy'],
            'CONTINUOUS_TRUSTED_ROUTE_IDENTITY_INVALID')


def _validate_scope(scope, snapshot):
    required = {'universe_id', 'universe_hash', 'boards', 'account_scope', 'initial_cash',
        'max_positions', 'cost_profiles', 'data_routes', 'rule_version', 'indicator_roles',
        'mechanism_combinations', 'ranking_variables', 'market_filter_scope',
        'execution_profiles', 'selection_policy'}
    _require(isinstance(scope, dict) and set(scope) == required, 'CONTINUOUS_SCOPE_FIELDS')
    _require(_identifier(scope['universe_id']) and _hash(scope['universe_hash']),
             'CONTINUOUS_UNIVERSE_IDENTITY_INVALID')
    supported = {item['id'] for item in snapshot['full_universe']['boards']}
    boards = scope['boards']
    _require(isinstance(boards, list) and len(boards) == len(set(boards))
        and set(boards) == supported and scope['selection_policy'] == SELECTION_POLICY
        and scope['account_scope'] == 'DATA_QUALIFIED', 'CONTINUOUS_FULL_REGISTERED_SCOPE_REQUIRED')
    _require(_number(scope['initial_cash']) and scope['initial_cash'] == 50000,
             'CONTINUOUS_INITIAL_CASH_REQUIRED')
    _require(type(scope['max_positions']) is int and scope['max_positions'] > 0,
             'CONTINUOUS_MAX_POSITIONS_INVALID')
    from .formal_account_backend_v1 import BASE_COSTS, STRESS_COSTS
    _require(scope['cost_profiles'] == {'BASE': BASE_COSTS, 'STRESS': STRESS_COSTS},
             'CONTINUOUS_COST_PROFILES_UNSUPPORTED')
    _require(isinstance(scope['data_routes'], dict) and set(scope['data_routes']) == set(STAGES),
             'CONTINUOUS_DATA_STAGES_REQUIRED')
    for stage, route in scope['data_routes'].items():
        _validate_route(route, stage, scope['universe_hash'])
    long_horizon = snapshot['long_horizon']
    _require(scope['rule_version'] == 'RESEARCH_RULE_STRATEGY_V4'
        and scope['rule_version'] in long_horizon['rule_versions'], 'CONTINUOUS_RULE_VERSION_UNSUPPORTED')
    roles = scope['indicator_roles']
    ids = {item['id'] for item in snapshot['rules']['indicators']}
    _require(isinstance(roles, dict) and len(roles) >= 2 and set(roles) <= ids,
             'CONTINUOUS_MULTI_INDICATOR_SCOPE_REQUIRED')
    for allowed in roles.values():
        _require(isinstance(allowed, list) and allowed and len(allowed) == len(set(allowed))
            and set(allowed) <= {'BUY', 'SELL', 'SCORE'}, 'CONTINUOUS_INDICATOR_ROLES_INVALID')
    combinations = scope['mechanism_combinations']
    _require(isinstance(combinations, list) and combinations
        and all(isinstance(group, list) and group and all(_identifier(item) for item in group)
                and len(group) == len(set(group)) for group in combinations),
        'CONTINUOUS_MECHANISM_COMBINATIONS_REQUIRED')
    ranking = scope['ranking_variables']
    _require(isinstance(ranking, dict)
        and set(ranking) == {'score_operators', 'score_directions', 'tie_breaker'},
        'CONTINUOUS_RANKING_FIELDS')
    for key in ('score_operators', 'score_directions'):
        _require(isinstance(ranking[key], list) and ranking[key]
            and len(ranking[key]) == len(set(ranking[key]))
            and set(ranking[key]) <= set(long_horizon[key]), 'CONTINUOUS_RANKING_UNSUPPORTED')
    _require(ranking['tie_breaker'] == long_horizon['tie_breaker']
        and scope['market_filter_scope'] == MARKET_FILTER_SCOPE,
        'CONTINUOUS_MARKET_OR_TIE_BREAKER_UNSUPPORTED')
    profiles = scope['execution_profiles']
    _require(isinstance(profiles, list) and profiles, 'CONTINUOUS_EXECUTION_PROFILES_REQUIRED')
    for profile in profiles:
        checked = validate_execution_profile(profile)
        _require(checked['profile_id'] == SEGMENTED_PROFILE,
                 'CONTINUOUS_RESEARCH_SEGMENTED_PROFILE_REQUIRED')
    _require(len({item['profile_hash'] for item in profiles}) == len(profiles),
             'CONTINUOUS_EXECUTION_PROFILES_DUPLICATED')


def continuous_research_contract(*, objective_id, scope, exploration_policy, final_criteria,
                                 criteria_source, capabilities_snapshot=None):
    """返回内容寻址合同；保存和批准仍由已有可信服务负责。"""
    snapshot = capabilities() if capabilities_snapshot is None else capabilities_snapshot
    _require(_identifier(objective_id), 'CONTINUOUS_OBJECTIVE_ID_INVALID')
    _validate_scope(scope, snapshot)
    _validate_final_criteria(final_criteria)
    source = criteria_source
    _require(isinstance(source, dict) and set(source) == {'path', 'sha256', 'content_hash'}
        and isinstance(source['path'], str) and source['path'] and _hash(source['sha256'])
        and source['content_hash'] == stable_hash(final_criteria), 'CONTINUOUS_CRITERIA_SOURCE_CONFLICT')
    policy = exploration_policy
    _require(isinstance(policy, dict)
        and set(policy) == {'version', 'minimum_account_sessions', 'minimum_complete_round_trips', 'thresholds'}
        and _identifier(policy['version']) and type(policy['minimum_account_sessions']) is int
        and 1 <= policy['minimum_account_sessions'] <= 504
        and type(policy['minimum_complete_round_trips']) is int and policy['minimum_complete_round_trips'] >= 0
        and isinstance(policy['thresholds'], dict) and set(policy['thresholds']) <= set(FINAL_MINIMUMS)
        and all(_number(value) for value in policy['thresholds'].values()),
        'CONTINUOUS_EXPLORATION_POLICY_INVALID')
    value = {'schema_version': VERSION, 'objective_id': objective_id, 'scope': deepcopy(scope),
        'exploration_policy': deepcopy(policy), 'final_criteria': deepcopy(final_criteria),
        'criteria_source': deepcopy(source), 'capabilities_fingerprint': snapshot['fingerprint'],
        'report_requirements': list(REPORT_REQUIREMENTS),
        'universe_claim': 'REGISTERED_SCOPE_NOT_COMPLETE_HISTORICAL_MARKET',
        'account_day_policy': 'EXECUTED_ACCOUNT_SESSIONS_EXCLUDING_WARMUP_INCLUDING_FLAT_DAYS',
        'formal_qualification_policy': 'SEPARATE_CANONICAL_ADJUDICATION_REQUIRED',
        'paper_policy': 'SEPARATE_OBSERVED_PAPER_EVIDENCE_REQUIRED'}
    value['content_hash'] = stable_hash(value)
    return value


def validate_continuous_research_contract(value, capabilities_snapshot=None):
    _require(isinstance(value, dict) and value.get('schema_version') == VERSION,
             'CONTINUOUS_CONTRACT_VERSION_INVALID')
    _require(value.get('content_hash') == stable_hash({key: item for key, item in value.items()
                                                   if key != 'content_hash'}),
             'CONTINUOUS_CONTRACT_IDENTITY_CONFLICT')
    snapshot = capabilities() if capabilities_snapshot is None else capabilities_snapshot
    _require(value.get('capabilities_fingerprint') == snapshot['fingerprint'],
             'CONTINUOUS_CAPABILITIES_CHANGED_NEW_VERSION_REQUIRED')
    expected = continuous_research_contract(objective_id=value['objective_id'], scope=value['scope'],
        exploration_policy=value['exploration_policy'], final_criteria=value['final_criteria'],
        criteria_source=value['criteria_source'], capabilities_snapshot=snapshot)
    _require(value == expected, 'CONTINUOUS_CONTRACT_FIELDS_CHANGED')
    return expected


def account_window_sessions(calendar, *, feature_start, account_start, account_end):
    """从显式冻结交易日历计数；不能用自然日或持股天数替代。"""
    for item in (feature_start, account_start, account_end):
        _day(item)
    _require(feature_start < account_start <= account_end, 'CONTINUOUS_ACCOUNT_WINDOW_INVALID')
    _require(isinstance(calendar, list) and calendar == sorted(set(calendar))
        and all(_day(day) for day in calendar), 'CONTINUOUS_CALENDAR_INVALID')
    return {'warmup_sessions': sum(feature_start <= day < account_start for day in calendar),
            'account_sessions': sum(account_start <= day <= account_end for day in calendar)}


def _data_readiness(contract, stage, data):
    reasons, counts = [], {'warmup_sessions': 0, 'account_sessions': 0}
    route = contract['scope']['data_routes'][stage]
    if not route or not isinstance(data, dict):
        return ['DATA_NOT_REGISTERED'], counts
    required = {'dataset_id', 'dataset_hash', 'feature_start', 'account_start', 'account_end',
                'calendar', 'required_warmup_sessions', 'target_count', 'qualified_count'}
    if not required <= set(data):
        return ['DATA_METADATA_INCOMPLETE'], counts
    _require(_identifier(data['dataset_id']) and _hash(data['dataset_hash']),
             'CONTINUOUS_DATASET_IDENTITY_INVALID')
    counts = account_window_sessions(data['calendar'], feature_start=data['feature_start'],
        account_start=data['account_start'], account_end=data['account_end'])
    if not route['start'] <= data['feature_start'] or data['account_end'] > route['end']:
        reasons.append('DATA_WINDOW_OUT_OF_SCOPE')
    if 'dataset_id' in route:
        if (data['dataset_id'], data['dataset_hash']) != (route['dataset_id'], route['dataset_hash']):
            reasons.append('DATASET_IDENTITY_CONFLICT')
    elif any(data.get(key) != route[key] for key in
             ('route_id', 'producer_identity', 'source_ids', 'universe_hash', 'quality_policy')):
        reasons.append('TRUSTED_DATA_ROUTE_IDENTITY_CONFLICT')
    _require(type(data['required_warmup_sessions']) is int and data['required_warmup_sessions'] >= 0,
             'CONTINUOUS_WARMUP_INVALID')
    if counts['warmup_sessions'] < data['required_warmup_sessions']:
        reasons.append('WARMUP_INSUFFICIENT')
    _require(type(data['target_count']) is int and type(data['qualified_count']) is int
        and 0 <= data['qualified_count'] <= data['target_count'], 'CONTINUOUS_TARGET_COUNTS_INVALID')
    if data['target_count'] == 0 or data['qualified_count'] == 0:
        reasons.append('NO_DATA_QUALIFIED_TARGETS')
    minimum = (contract['exploration_policy']['minimum_account_sessions'] if stage == 'EXPLORATION'
               else contract['final_criteria']['independent_validation_minimum_actual_sessions'])
    if counts['account_sessions'] < minimum:
        reasons.append('ACCOUNT_SESSIONS_INSUFFICIENT')
    if stage == 'CONFIRMATION':
        for key, reason in (('independent', 'INDEPENDENCE_NOT_PROVEN'),
                            ('post_freeze_unseen', 'POST_FREEZE_UNSEEN_NOT_PROVEN'),
                            ('family_frozen', 'FAMILY_NOT_FROZEN')):
            if data.get(key) is not True:
                reasons.append(reason)
        if data.get('exposed') is not False:
            reasons.append('DATA_EXPOSED_OR_UNKNOWN')
    return reasons, counts


def continuous_research_preflight(contract, *, exploration_data=None, confirmation_data=None,
                                  readiness=None):
    """汇总已有元数据；READY 只表示预检条件，不能代替派发前权限核验。"""
    value = validate_continuous_research_contract(contract)
    ready = readiness or {}
    channels, gaps = {}, []
    model = ready.get('model', {})
    model_reasons = []
    if model.get('available') is not True:
        model_reasons.append('MODEL_UNAVAILABLE')
    if model.get('hard_budget_enforced') is not True:
        model_reasons.append('REAL_MODEL_BUDGET_UNSUPPORTED')
    for stage, data in (('EXPLORATION', exploration_data), ('CONFIRMATION', confirmation_data)):
        reasons, counts = _data_readiness(value, stage, data)
        if stage == 'CONFIRMATION' and ready.get('final_exploration_evidence_ready') is not True:
            reasons.append('FINAL_EXPLORATION_EVIDENCE_NOT_READY')
        for key, reason in (('authorization', 'AUTHORIZATION_NOT_READY'),
                            ('resources', 'STAGE_RESOURCES_NOT_READY')):
            if ready.get(key, {}).get(stage) is not True:
                reasons.append(reason)
        for key, reason in (('execution', 'EXECUTION_NOT_READY'), ('storage', 'STORAGE_NOT_READY')):
            if ready.get(key) is not True:
                reasons.append(reason)
        deterministic = not reasons
        proposals = deterministic and not model_reasons and stage == 'EXPLORATION'
        runnable = deterministic and (stage == 'CONFIRMATION' or proposals
                                     or ready.get('frozen_exploration_work') is True)
        channels[stage] = {'status': 'READY' if runnable else 'WAITING', 'can_run': runnable,
            'can_execute_deterministic': deterministic, 'can_propose': proposals,
            'waiting_reasons': reasons + (model_reasons if stage == 'EXPLORATION' and not runnable else []),
            'available_account_sessions': counts['account_sessions'],
            'available_warmup_sessions': counts['warmup_sessions']}
        final_minimum = value['final_criteria'][('exploration_minimum_actual_sessions'
            if stage == 'EXPLORATION' else 'independent_validation_minimum_actual_sessions')]
        if counts['account_sessions'] < final_minimum:
            gaps.append({'stage': stage, 'reason': 'FINAL_ACCOUNT_SESSIONS_INSUFFICIENT',
                         'required': final_minimum, 'available': counts['account_sessions'],
                         'shortfall': final_minimum - counts['account_sessions']})
    return {'schema_version': 'CONTINUOUS_RESEARCH_PREFLIGHT_V1', 'contract_hash': value['content_hash'],
        'channels': channels, 'model_waiting_reasons': model_reasons, 'final_business_gaps': gaps,
        'final_business_status': 'NOT_EVALUATED',
        'formal_qualification': 'NOT_EVALUATED' if ready.get('formal_method_applicable') is True else 'WAITING_METHOD',
        'strategy_qualified': False, 'read_only': True, 'authorization_granted': False}


def _stage_business(contract, stage, evidence):
    if not isinstance(evidence, dict):
        return {'passed': False, 'failure_codes': ['REAL_ACCOUNT_EVIDENCE_MISSING']}
    failures = []
    if evidence.get('evidence_kind') != 'REAL_ACCOUNT' or not _hash(evidence.get('rule_identity')):
        failures.append('REAL_ACCOUNT_IDENTITY_REQUIRED')
    if (not set(REPORT_REQUIREMENTS) <= set(evidence.get('report_sections', []))
            or evidence.get('concentration_dimensions') != contract['final_criteria']['concentration_required']
            or evidence.get('benchmarks') != contract['final_criteria']['benchmarks']
            or evidence.get('equity_includes_open_positions') is not True):
        failures.append('FINAL_BUSINESS_REPORT_INCOMPLETE')
    window_start, window_end = evidence.get('account_start'), evidence.get('account_end')
    if (not isinstance(window_start, str) or not isinstance(window_end, str)
            or _day(window_start) > _day(window_end) or not _hash(evidence.get('input_identity'))):
        failures.append('FROZEN_ACCOUNT_WINDOW_REQUIRED')
        window_start = window_end = ''
    accounts = evidence.get('accounts', {})
    route = contract['scope']['data_routes'][stage]
    if route is None or not _identifier(evidence.get('dataset_id')) or not _hash(evidence.get('dataset_hash')):
        failures.append('FROZEN_DATASET_IDENTITY_REQUIRED')
    elif 'dataset_id' in route:
        if (evidence['dataset_id'], evidence['dataset_hash']) != (route['dataset_id'], route['dataset_hash']):
            failures.append('FROZEN_DATASET_IDENTITY_CONFLICT')
    elif any(evidence.get(key) != route[key] for key in
             ('route_id', 'producer_identity', 'source_ids', 'universe_hash', 'quality_policy')):
        failures.append('TRUSTED_DATA_ROUTE_IDENTITY_CONFLICT')
    dates = []
    minimum = contract['final_criteria'][('exploration_minimum_actual_sessions'
        if stage == 'EXPLORATION' else 'independent_validation_minimum_actual_sessions')]
    limits = contract['final_criteria']['thresholds_each_stage']
    checks = {
        'BASE': [('annualized_net_return', 'base_annualized_net_return_minimum', 'MIN'),
                 ('maximum_drawdown', 'base_maximum_drawdown_maximum', 'MAX'),
                 ('complete_round_trip_net_win_rate', 'base_complete_round_trip_net_win_rate_minimum', 'MIN'),
                 ('complete_round_trip_net_profit_factor', 'base_complete_round_trip_net_profit_factor_minimum', 'MIN'),
                 ('complete_round_trips', 'base_complete_round_trips_minimum', 'MIN')],
        'STRESS': [('total_net_return', 'stress_total_net_return_strictly_greater_than', 'STRICT_MIN')],
    }
    for cost in ('BASE', 'STRESS'):
        account = accounts.get(cost, {})
        days = account.get('account_dates', [])
        _require(isinstance(days, list) and days == sorted(set(days))
            and all(_day(day) for day in days), 'CONTINUOUS_ACTUAL_ACCOUNT_CALENDAR_INVALID')
        dates.append(days)
        if len(days) < minimum:
            failures.append(cost + '_ACTUAL_ACCOUNT_SESSIONS_INSUFFICIENT')
        if (route is None or any(not route['start'] <= day <= route['end']
                                or not window_start <= day <= window_end for day in days)):
            failures.append(cost + '_ACCOUNT_DATES_OUT_OF_SCOPE')
        if (account.get('initial_cash') != 50000 or account.get('audit_passed') is not True
                or not _hash(account.get('evidence_identity'))
                or account.get('input_identity') != evidence.get('input_identity')
                or account.get('rule_identity') != evidence.get('rule_identity')
                or account.get('cost_profile') != contract['scope']['cost_profiles'][cost]):
            failures.append(cost + '_AUDITED_ACCOUNT_REQUIRED')
        for metric, threshold, comparison in checks[cost]:
            actual, bound = account.get(metric), limits[threshold]
            passed = _number(actual) and (actual <= bound if comparison == 'MAX'
                else actual > bound if comparison == 'STRICT_MIN' else actual >= bound)
            if metric == 'complete_round_trip_net_profit_factor' and actual is None:
                basis = account.get('complete_round_trip_net_profit_factor_basis', {})
                passed = (isinstance(basis, dict) and _number(basis.get('positive_net_profit_sum'))
                    and basis['positive_net_profit_sum'] > 0
                    and _number(basis.get('absolute_negative_net_profit_sum'))
                    and basis['absolute_negative_net_profit_sum'] == 0)
            if metric == 'complete_round_trips':
                passed = passed and type(actual) is int
            if metric in ('maximum_drawdown', 'complete_round_trip_net_win_rate'):
                passed = passed and 0 <= actual <= 1
            if not passed:
                failures.append(cost + '_' + metric.upper() + '_FAILED')
    if dates[0] != dates[1]:
        failures.append('COST_ACCOUNT_CALENDARS_DIFFER')
    if accounts.get('BASE', {}).get('evidence_identity') == accounts.get('STRESS', {}).get('evidence_identity'):
        failures.append('INDEPENDENT_COST_ACCOUNT_EVIDENCE_REQUIRED')
    if stage == 'CONFIRMATION':
        independence = evidence.get('independence', {})
        if (any(independence.get(key) is not True for key in
                ('prior_access_review_passed', 'post_freeze_unseen', 'family_frozen'))
                or not _hash(independence.get('exposure_identity'))):
            failures.append('INDEPENDENT_EVIDENCE_NOT_PROVEN')
    return {'passed': not failures, 'failure_codes': failures,
            'actual_account_sessions': {cost: len(days) for cost, days in zip(('BASE', 'STRESS'), dates)}}


def evaluate_final_business_goal(contract, *, exploration=None, confirmation=None,
                                 formal_qualification=None, paper=None):
    """投影各阶段独立的业务核对；不调用正式裁决，也不授予 Paper 资格。"""
    value = validate_continuous_research_contract(contract)
    stages = {stage: _stage_business(value, stage, evidence) for stage, evidence in
              (('EXPLORATION', exploration), ('CONFIRMATION', confirmation))}
    same_rule = (isinstance(exploration, dict) and isinstance(confirmation, dict)
                 and exploration.get('rule_identity') == confirmation.get('rule_identity'))
    if not same_rule:
        stages['CONFIRMATION']['passed'] = False
        stages['CONFIRMATION']['failure_codes'].append('EXPLORATION_CONFIRMATION_RULE_MISMATCH')
    if (isinstance(exploration, dict) and isinstance(confirmation, dict)
            and exploration.get('input_identity') == confirmation.get('input_identity')):
        stages['CONFIRMATION']['passed'] = False
        stages['CONFIRMATION']['failure_codes'].append('INDEPENDENT_INPUT_ALREADY_USED')
    passed = all(stage['passed'] for stage in stages.values())
    return {'contract_hash': value['content_hash'], 'stages': stages,
        'status': 'BUSINESS_GOAL_MET' if passed else 'BUSINESS_GOAL_NOT_MET',
        'business_goal_met': passed, 'formal_qualification': deepcopy(formal_qualification),
        'paper': deepcopy(paper), 'strategy_qualified': False,
        'qualification_decision_made': False, 'pooled_profits_used': False}
