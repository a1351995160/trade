"""V3动态turn依赖贯通数据、真实账户与Paper输入门；旧V2保持严格。"""
from copy import deepcopy
import pytest

from chanlun_trader.research_factory.research_rule_strategy_v3 import ResearchRuleStrategyV3
from chanlun_trader.research_factory.rule_account_backend_v2 import RuleAccountBackendV2, rule_input_identity
from chanlun_trader.research_factory.research_benchmark_v1 import FullPoolBuyHoldStrategyV1, FullPoolBuyHoldBackendV1, benchmark_payload
from chanlun_trader.research_factory.strategy_interface_v1 import prepare, run
from chanlun_trader.research_factory.forward_paper_v1 import _data_check
from test_research_data_provider_v1 import dataset, provider, rewrite_response
from test_research_rule_strategy_v3 import payload
from test_rule_account_backend_v2 import execute


def missing_turn(tmp_path):
    args = dataset(tmp_path)
    def remove(value):
        index = value['fields'].index('turn')
        value['fields'].pop(index)
        for row in value['raw_rows']:
            row.pop(index)
    for symbol in args['symbols']:
        rewrite_response(tmp_path, 'DAILY_' + symbol + '.json', remove)
    return args


def account(strategy, backend, prepared):
    plan = prepare(strategy, backend)
    identity = prepared['input_identity']
    name = strategy.strategy_id
    receipt = {'strategy_plans': {name: plan}, 'input_identity': identity,
        'novelty': {name: {'allowed': True}}, 'execution_purpose': name, 'execution_consumed': True}
    return run(strategy, backend, frame=prepared['bundle'], actions=prepared['bundle']['events'],
               input_identity=identity, active_check=lambda: receipt)


def test_missing_turn_stays_unknown_and_v3_and_benchmark_accounts_run(tmp_path):
    args = missing_turn(tmp_path)
    strategy = ResearchRuleStrategyV3(payload(), strategy_id='NO_TURN')
    prepared = provider(tmp_path, []).prepare('sample', **args, required_fields=strategy.requirements.fields)
    assert prepared['qualification']['field_status']['turn'] == 'UNKNOWN'
    assert set(prepared['bundle']['turn']) == {'symbol', 'date', 'volume', 'tradestatus'}
    assert account(strategy, RuleAccountBackendV2(prepared['window']), prepared)['reconciliation']['passed']
    benchmark = FullPoolBuyHoldStrategyV1(benchmark_payload(args['symbols']), strategy_id='CONTROL')
    assert account(benchmark, FullPoolBuyHoldBackendV1(prepared['window']), prepared)['reconciliation']['passed']
    with pytest.raises(ValueError, match='REQUIRED_FIELD_MISSING_OR_INVALID:turn'):
        execute(prepared['window'], prepared['bundle'])


def test_declared_turn_and_legacy_provider_require_missing_field(tmp_path):
    args = missing_turn(tmp_path)
    for required in ({'required_fields': ('turn',)}, {}):
        with pytest.raises(ValueError, match='DATA_REQUIRED_FIELD_MISSING:turn'):
            provider(tmp_path, []).prepare('sample', **args, **required)


def test_optional_turn_keeps_rectangular_and_security_state_checks(tmp_path):
    args = missing_turn(tmp_path)
    prepared = provider(tmp_path, []).prepare('sample', **args, required_fields=('close',))
    strategy = ResearchRuleStrategyV3(payload(), strategy_id='NO_TURN')
    for change, code in [('row', 'FRAME_COVERAGE_INVALID:turn'), ('state', 'SUSPENDED_FEATURES_UNSUPPORTED')]:
        broken = deepcopy(prepared)
        if change == 'row':
            broken['bundle']['turn'] = broken['bundle']['turn'].iloc[1:]
        else:
            broken['bundle']['turn'].loc[0, 'tradestatus'] = 0
        broken['input_identity'] = rule_input_identity(broken['bundle'], broken['window'])
        with pytest.raises(ValueError, match=code):
            account(strategy, RuleAccountBackendV2(broken['window']), broken)


def test_paper_optional_turn_never_fills_zero_and_keeps_coverage_gate(tmp_path):
    args = missing_turn(tmp_path)
    prepared = provider(tmp_path, []).prepare('sample', **args, required_fields=('close',))
    data = {'bars': prepared['bundle']['daily'].to_dict('records'),
            'turn': prepared['bundle']['turn'].to_dict('records'), 'corporate_actions_complete': True,
            'corporate_actions': []}
    assert _data_check(data, args['symbols'], warmup=True, require_turn=False)
    assert all('turn' not in row for row in data['turn'])
    with pytest.raises(ValueError, match='TURN_INVALID'):
        _data_check(data, args['symbols'], warmup=True)
    data['turn'].pop()
    with pytest.raises(ValueError, match='TURN_COVERAGE'):
        _data_check(data, args['symbols'], warmup=True, require_turn=False)


def test_v3_actual_turn_indicator_is_rejected_before_task_freeze(tmp_path):
    from chanlun_trader.research_factory.research_rule_strategy_v3 import rule_capabilities
    from chanlun_trader.research_factory.strategy_submission_v1 import StrategySubmissionV1
    from test_strategy_submission_v1 import request
    from test_research_rule_strategy_v3 import node
    args = missing_turn(tmp_path)
    from scripts.probe_all_indicator_strategy_v1 import pilot_registry
    registry = pilot_registry()
    spec = next(item for item in rule_capabilities()['indicators']
                if 'vendor_turn' in registry.get(item['id'], item['version']).requires_extra_data)
    rule = payload()
    rule['indicator_instances'] = [{'instance_id': 'turn_factor', 'id': spec['id'], 'version': spec['version'], 'params': spec['params']}]
    rule['buy'] = node('gt', node('indicator', 'turn_factor', output=spec['outputs'][0], version=spec['version']), node('const', value=0))
    strategy = ResearchRuleStrategyV3(rule, strategy_id='TURN_NEEDED')
    assert 'turn' in strategy.requirements.fields
    auth = {'objective_id': 'TURN_TEST', 'budget_path': str(tmp_path / 'budget.json'), 'data_authorization': args['authorization']}
    service = StrategySubmissionV1(provider(tmp_path, []), lambda ref: auth, tmp_path / 'tasks')
    submitted = request()
    submitted['rule'] = rule
    for key in ('feature_start', 'account_start', 'account_end'):
        submitted[key] = args[key]
    preview = service.preview(submitted)
    with pytest.raises(ValueError, match='DATA_REQUIRED_FIELD_MISSING:turn'):
        service.freeze(submitted, preview['preview_identity'])
    assert not list((tmp_path / 'tasks').glob('*/account/JOB.json'))


def test_partially_missing_pool_turn_remains_unknown_without_nan_imputation(tmp_path):
    args = dataset(tmp_path)
    def remove(value):
        index = value['fields'].index('turn')
        value['fields'].pop(index)
        for row in value['raw_rows']:
            row.pop(index)
    rewrite_response(tmp_path, 'DAILY_000003.SZ.json', remove)
    prepared = provider(tmp_path, []).prepare('sample', **args, required_fields=('close',))
    assert 'turn' not in prepared['bundle']['turn']
    assert not prepared['bundle']['turn'].isna().any().any()
    assert prepared['qualification']['field_status']['turn'] == 'UNKNOWN'
