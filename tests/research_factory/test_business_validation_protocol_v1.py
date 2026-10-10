"""合成准入与公共服务接线测试；不声称真实独立账户已执行。"""
from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from chanlun_trader.research_factory.business_validation_protocol_v1 import (
    BusinessValidationProtocolV1, evaluate_business_reports, verified_final_exploration_evidence,
    verify_exploration_binding,
)
from chanlun_trader.research_factory.campaign_confirmation_v1 import CampaignConfirmationV1
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.continuous_research_contract_v1 import (
    FINAL_MINIMUMS, continuous_research_contract,
)
from chanlun_trader.research_factory.forward_snapshot_v1 import (
    SnapshotStoreV1, universe_snapshot_projection, validate_snapshot,
)
from chanlun_trader.research_factory.formal_account_backend_v1 import BASE_COSTS, STRESS_COSTS
from chanlun_trader.research_factory.research_capabilities_v1 import capabilities
from chanlun_trader.research_factory.strategy_submission_v1 import public_rule_factory
from chanlun_trader.research_factory.universe_execution_profile_v1 import SEGMENTED_PROFILE, execution_profile
from chanlun_trader.research_factory.universe_research_report_v2 import default_observation_plan, _business_diagnostics


SYMBOLS = ['000001.SZ', '300001.SZ', '600000.SH']


def days(count, start=date(2027, 1, 1)):
    result = []
    while len(result) < count:
        if start.weekday() < 5:
            result.append(int(start.strftime('%Y%m%d')))
        start += timedelta(days=1)
    return result


def payload(day, symbols=SYMBOLS):
    return {'bars': [{'symbol': symbol, 'date': day, 'open': 10., 'high': 10., 'low': 10.,
        'close': 10., 'prev_close': 10., 'volume': 100., 'amount': 1000.} for symbol in symbols],
        'turn': [{'symbol': symbol, 'date': day, 'turn': 1., 'tradestatus': 1} for symbol in symbols],
        'states': [{'symbol': symbol, 'date': day, 'listed': True, 'delisted': False,
            'is_st': False, 'suspended': False, 'board': 'CHINEXT' if symbol.startswith('30') else 'MAIN'}
            for symbol in symbols], 'corporate_actions_complete': True, 'corporate_actions': []}


class MemorySyntheticSnapshots:
    """显式合成 Store；只给纯投影提供已验证样本，不代表真实采集。"""
    def __init__(self, calendar, warmup=60):
        self.values = {}
        for index, day in enumerate(calendar):
            for phase in ('CLOSE',) if index < warmup else ('OPEN', 'CLOSE'):
                text = str(day)
                received = f'{text[:4]}-{text[4:6]}-{text[6:]}T15:01:00+08:00'
                source = {'synthetic_payload': payload(day)}
                body = {'schema_version': 'FORWARD_MARKET_SNAPSHOT_V1', 'profile': 'SYNTHETIC',
                    'phase': phase, 'market_date': day, 'received_at': received,
                    'provider': 'SYNTHETIC', 'payload': payload(day),
                    'source_responses': source, 'source_response_hash': stable_hash(source)}
                validate_snapshot(body)
                digest = stable_hash(body)
                self.values['SNAP_' + digest] = {**body, 'snapshot_id': 'SNAP_' + digest, 'snapshot_hash': digest}

    def load(self, snapshot_id):
        return deepcopy(self.values[snapshot_id])


@pytest.fixture(scope='module')
def snapshot():
    return capabilities()


def contract(snapshot):
    final = {'version': 'USER_STRATEGY_BUSINESS_ACCEPTANCE_V1',
        'scope': 'ALL_REGISTERED_SUPPORTED_TARGETS_THEN_ALL_DATA_QUALIFIED_SYMBOLS',
        'account_scope': 'DATA_QUALIFIED', 'selection_rule_version': 'RESEARCH_RULE_STRATEGY_V4',
        'initial_cash_each_independent_cost_account': 50000,
        'exploration_minimum_actual_sessions': 504, 'independent_validation_minimum_actual_sessions': 252,
        'thresholds_each_stage': deepcopy(FINAL_MINIMUMS), 'account_equity_includes_open_positions': True,
        'independence_requires_prior_access_review_and_post_freeze_unseen_data': True,
        'cannot_pool_stage_or_cost_account_profits': True,
        'concentration_required': ['symbol', 'complete_round_trip', 'time_period'],
        'benchmarks': ['CASH', 'SYSTEM_SUPPORTED_PRICE_REFERENCE_WITH_INVESTABILITY_DISCLOSED'],
        'formal_statistics': 'ONLY_IF_CURRENT_METHOD_PROVES_APPLICABLE'}
    scope = {'universe_id': 'full_registered', 'universe_hash': stable_hash(SYMBOLS),
        'boards': ['SZ_MAIN', 'SH_MAIN', 'CHINEXT'], 'account_scope': 'DATA_QUALIFIED',
        'initial_cash': 50000, 'max_positions': 2, 'cost_profiles': {'BASE': BASE_COSTS, 'STRESS': STRESS_COSTS},
        'data_routes': {stage: {'dataset_id': stage.lower(), 'dataset_hash': stable_hash(stage),
            'start': '2020-01-01', 'end': '2028-12-31',
            'purpose': 'TRAIN' if stage == 'EXPLORATION' else 'INDEPENDENT_VALIDATION'}
            for stage in ('EXPLORATION', 'CONFIRMATION')},
        'rule_version': 'RESEARCH_RULE_STRATEGY_V4',
        'indicator_roles': {'MA': ['BUY', 'SELL'], 'RSI': ['BUY'], 'ROLLING_VOLATILITY': ['BUY', 'SCORE']},
        'mechanism_combinations': [['TREND', 'MOMENTUM', 'RANKING']],
        'ranking_variables': {key: deepcopy(snapshot['long_horizon'][key])
            for key in ('score_operators', 'score_directions', 'tie_breaker')},
        'market_filter_scope': 'SAME_SYMBOL_ONLY',
        'selection_policy': 'ALL_REGISTERED_TARGETS_THEN_STRATEGY_SIGNALS',
        'execution_profiles': [execution_profile(SEGMENTED_PROFILE, 252), execution_profile(SEGMENTED_PROFILE, 504)]}
    return continuous_research_contract(objective_id='business_objective', scope=scope,
        exploration_policy={'version': 'SHORT_V1', 'minimum_account_sessions': 252,
            'minimum_complete_round_trips': 10, 'thresholds': {}}, final_criteria=final,
        criteria_source={'path': 'synthetic/FROZEN_CRITERIA.json', 'sha256': stable_hash('file_bytes'),
                         'content_hash': stable_hash(final)}, capabilities_snapshot=snapshot)


def setup_case(tmp_path, snapshot):
    rule = deepcopy(snapshot['examples']['multi_indicator_ranked'])
    identity = public_rule_factory(rule, 'passed').rule_identity
    material = {'attempts': [{'candidate_id': 'failed', 'status': 'REJECTED'},
        {'candidate_id': 'passed', 'batch_id': 'BATCH_SYNTHETIC', 'rule_identity': identity, 'status': 'SCREENED'}],
        'selected': ['passed'], 'final_exploration_ready': ['passed'],
        'selection_policy': {'version': 'FROZEN_RESEARCH_SELECTION_V1'},
        'exposures': [], 'profile': 'SYNTHETIC'}
    research = SimpleNamespace(root=tmp_path, confirmation_material=lambda: deepcopy(material))
    request = {'version': 'FULL_UNIVERSE_SUBMISSION_V4', 'phase': 'EXPLORATION',
        'strategy_id': 'passed', 'rule': rule,
        'universe_id': 'full_registered', 'dataset_id': 'exploration', 'account_scope': 'DATA_QUALIFIED',
        'initial_cash': 50000, 'max_positions': 2, 'max_symbol_exposure_bps': 10000,
        'costs': ['BASE', 'STRESS'], 'benchmark': 'CASH_AND_PRICE_REFERENCE',
        'purpose': 'EXPLORATORY', 'authorization_ref': 'old_exploration_ref',
        'research_binding_ref': {'binding_id': stable_hash('synthetic_initial_binding')},
        'feature_start': 20200101, 'account_start': 20200401, 'account_end': 20220401,
        'execution_profile': execution_profile(SEGMENTED_PROFILE, 504),
        'observation_plan': {}}
    initial_final_evidence(research, material, request, contract(snapshot))
    arguments = {'contract': contract(snapshot), 'selected': ['passed'], 'requests': {'passed': request},
        'selection_policy': {'version': 'INDEPENDENT_SELECTION_V1', 'maximum_selected_candidates': 1,
            'maximum_independent_exposures': 1, 'selection_basis': 'FROZEN_EXPLORATORY_SCREEN',
            'result_use': 'DESCRIPTIVE_BUSINESS_CHECK_NO_MAXIMIZATION'}, 'not_before': '2027-01-01'}
    service = BusinessValidationProtocolV1(research, exposure_reader=lambda: [])
    preview = service.preview(**arguments)
    protocol = service.freeze(**arguments, preview_identity=preview['preview_identity'])
    return service, material, arguments, protocol


def admission(protocol, *, count=252):
    calendar = days(60 + count)
    store = MemorySyntheticSnapshots(calendar)
    projection = universe_snapshot_projection(store, list(store.values), target_symbols=SYMBOLS,
        calendar=calendar, feature_start=calendar[0], account_start=calendar[60], account_end=calendar[-1],
        frozen_at=protocol['frozen_at'], profile='SYNTHETIC')
    metadata = {'dataset_id': 'confirmation', 'content_hash': stable_hash('CONFIRMATION'),
        'source': 'SYNTHETIC_OBSERVATIONS', 'captured_at': '2028-12-31T15:01:00+08:00',
        'fields': ['open', 'close', 'volume', 'amount'],
        'units': {'open': 'CNY', 'close': 'CNY', 'volume': 'SHARES', 'amount': 'CNY'},
        'adjustment': 'RAW', 'state_basis': 'SYNTHETIC_CAPTURE',
        'corporate_action_basis': 'EXPLICIT_COVERAGE', 'availability_basis': 'RECEIVED_AT',
        'symbols': SYMBOLS, 'window': {'start': calendar[0], 'end': calendar[-1]}}
    fields = {'dataset_id': 'confirmation', 'feature_start': calendar[0], 'account_start': calendar[60],
        'account_end': calendar[-1], 'universe_id': 'full_registered',
        'execution_profile': execution_profile(SEGMENTED_PROFILE, count),
        'observation_plan': default_observation_plan({'account_start': calendar[60], 'account_end': calendar[-1]})}
    return {'snapshot_projection': projection, 'source_authenticated': True,
        'prior_access_review_passed': True, 'post_freeze_unseen': True,
        'prior_access_review_identity': stable_hash('synthetic_prior_access_review'),
        'metadata': metadata, 'request_fields': fields}


class PublicServiceSpy:
    """仅检验调用接线；完整公共 worker 回归由公共阶段测试覆盖。"""
    def __init__(self, root, sessions=252):
        self.root, self.calls, self.request, self.final_status = root, [], None, 'ACCOUNT_VERIFIED'
        self.sessions = sessions

    def preview(self, request):
        self.calls.append('preview')
        self.request = deepcopy(request)
        value = {'request': deepcopy(request),
            'rule_identity': public_rule_factory(request['rule'], request['strategy_id']).rule_identity}
        self.preview_value = {**value, 'preview_identity': stable_hash(value)}
        return deepcopy(self.preview_value)

    def freeze(self, request, preview_identity):
        self.calls.append('freeze')
        job = self.root / 'public_task' / 'JOB.json'
        job.parent.mkdir(parents=True, exist_ok=True)
        plans = {cost: stable_hash(cost) for cost in ('BASE', 'STRESS')}
        job.write_text(json.dumps({'request': request, 'input_identity': stable_hash('confirmation_input'),
            'plans': {cost: {'plan_id': value} for cost, value in plans.items()},
            'items': {cost: {'backend_options': {'costs': cost},
                'factory_kwargs': {'payload': request['rule']}} for cost in plans}}), encoding='utf-8')
        self.task = {'task_id': stable_hash([request, preview_identity]), 'job_path': str(job),
            'plan_ids': plans, 'preview_identity': preview_identity,
            'submission_version': 'FULL_UNIVERSE_SUBMISSION_V4',
            'job_sha256': hashlib.sha256(job.read_bytes()).hexdigest(), 'input_identity': stable_hash('confirmation_input')}
        (self.root / 'PREVIEW.json').write_text(json.dumps(self.preview_value), encoding='utf-8')
        return deepcopy(self.task)

    def _task(self, task_id):
        assert task_id == self.task['task_id']
        return deepcopy(self.task)

    def advance(self, task_id):
        self.calls.append(('advance', task_id))
        refs = {}
        for cost in ('BASE', 'STRESS'):
            dates = days(60 + self.sessions)[60:]
            ending = (80000. if self.sessions >= 504 else 60000.) if cost == 'BASE' else 55000.
            episodes = [{'status': 'CLOSED', 'symbol': SYMBOLS[index % len(SYMBOLS)],
                'end_session': dates[-1], 'net_profit': 100. if index < 70 else -50.} for index in range(120)]
            curve = [{'date': day, 'equity': 50000. + (ending - 50000.) * (index + 1) / self.sessions}
                     for index, day in enumerate(dates)]
            report = {'version': 'UNIVERSE_RESEARCH_REPORT_V2', 'input_identity': self.task['input_identity'],
                'account': {'initial_cash': 50000, 'sessions': self.sessions, 'final_equity': ending,
                    'max_drawdown': .1, 'closed_episode_win_rate': 70 / 120, 'closed_episodes': 120,
                    'episodes': episodes, 'average_cash': 20000., 'average_equity_less_cash_fraction': .6,
                    'business_diagnostics': _business_diagnostics(curve, 50000., episodes)},
                'signal': {'account_independent_denominator': True, 'statistics': {'5': {}},
                    'limitations': ['合成价格对照，不是可投资账户。']}, 'strategy_qualified': False}
            report['report_identity'] = stable_hash(report)
            path = self.root / 'public_task' / (cost + '_RESEARCH_REPORT.json')
            path.write_text(json.dumps(report), encoding='utf-8')
            source_path = self.root / 'public_task' / (cost + '_RESULT.json')
            source_path.write_text(json.dumps({'SYNTHETIC_TEST': True, 'cost': cost}), encoding='utf-8')
            digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
            (self.root / 'public_task' / (cost + '_SETTLEMENT.json')).write_text(
                json.dumps({'result_sha256': digest}), encoding='utf-8')
            final = {'artifacts': {'source_result': {'path': str(source_path), 'sha256': digest}},
                'research': {'source_result_sha256': digest, 'account_and_signal': report},
                'benchmark': {'version': 'UNIVERSE_PRICE_REFERENCE_V1',
                    'input_identity': self.task['input_identity'], 'status': 'AVAILABLE_NONINVESTABLE',
                    'investable': False, 'formal_qualification': False, 'metrics': {'net_return': .05},
                    'limitations': ['合成不可投资价格篮子。'], 'cash': {'net_return': 0., 'initial_cash': 50000.}}}
            final_path = self.root / 'public_task' / (cost + '_REPORT.json')
            final_path.write_text(json.dumps(final), encoding='utf-8')
            refs[cost] = {'research_report': str(path), 'final_report': str(final_path),
                'final_report_sha256': hashlib.sha256(final_path.read_bytes()).hexdigest(),
                'research_report_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        result = {'status': self.final_status, 'task_id': task_id,
            'dispatched_segments': 1,
            'input_identity': self.task['input_identity'],
            'rule_identity': public_rule_factory(self.request['rule'], self.request['strategy_id']).rule_identity,
            'verification': {'advance_allowed': True, 'job_sha256': self.task['job_sha256']},
            'reports': refs, 'strategy_qualified': False}
        (self.root / 'public_task' / 'VERIFICATION.json').write_text(json.dumps(result['verification']), encoding='utf-8')
        return result


def initial_final_evidence(research, material, request, frozen_contract):
    """合成504日原件，专供独立协议单位测试；不会充当真实行情验收。"""
    source = PublicServiceSpy(Path(research.root) / 'initial', sessions=504)
    preview = source.preview(request)
    task = source.freeze(request, preview['preview_identity'])
    outcome = source.advance(task['task_id'])
    screen = {'passed': True, 'final_exploration_ready': True}
    material['attempts'][1]['screen'] = {**screen, 'identity': stable_hash(screen)}
    directory = Path(research.root) / 'passed'
    directory.mkdir()
    for name, value in {'REQUEST': request, 'PREVIEW': preview, 'TASK': task,
                        'PUBLIC_RESULT': outcome, 'SCREEN': screen}.items():
        (directory / (name + '.json')).write_text(json.dumps(value), encoding='utf-8')
    research.submission = source
    from chanlun_trader.research_factory.continuous_submission_v1 import request_scope
    dataset = {'dataset_id': request['dataset_id'], 'universe_identity': frozen_contract['scope']['universe_hash'],
               'metadata_hash': frozen_contract['scope']['data_routes']['EXPLORATION']['dataset_hash']}
    source.provider = SimpleNamespace(catalog=lambda: {'datasets': [dataset]})
    source.continuous_scope = SimpleNamespace(resolve=lambda ref, **kwargs: {
        'candidate_identity': material['attempts'][1]['rule_identity'],
        'batch_id': material['attempts'][1]['batch_id'], 'phase': 'EXPLORATION',
        'request': request_scope(request, frozen_contract, dataset)})


def wire(service, protocol, tmp_path):
    evidence = admission(protocol)
    service.admit = lambda context: deepcopy(context.get('frozen_admission', evidence))
    service.bind_stage = lambda request, **kwargs: {**request, 'authorization_ref': 'trusted_confirmation_ref',
        'research_binding_ref': {'binding_id': stable_hash([request, kwargs])}}
    service.submission = PublicServiceSpy(tmp_path)
    return evidence


def test_full_family_is_frozen_and_later_exploration_may_append(tmp_path, snapshot):
    service, material, _, _ = setup_case(tmp_path, snapshot)
    assert len(service.load()['preview']['family']) == 2
    material['attempts'].append({'candidate_id': 'later', 'status': 'REJECTED'})
    assert len(service.load()['preview']['family']) == 2
    material['attempts'][0]['status'] = 'OMITTED'
    with pytest.raises(ValueError, match='FROZEN_FAMILY_CHANGED'):
        service.load()


def test_missing_independent_data_waits_without_public_dispatch_or_method_dependency(tmp_path, snapshot):
    service, _, _, _ = setup_case(tmp_path, snapshot)
    before = sorted(str(path) for path in tmp_path.rglob('*'))
    assert service.advance()['status'] == 'WAITING_DATA'
    state = service.readiness()
    assert state['exploration_may_continue'] is True
    assert state['formal_qualification'] == 'SEPARATE_NOT_GRANTED'
    assert sorted(str(path) for path in tmp_path.rglob('*')) == before


def test_public_preview_freeze_advance_and_same_task_recovery(tmp_path, snapshot):
    service, _, _, protocol = setup_case(tmp_path, snapshot)
    wire(service, protocol, tmp_path)
    assert service.readiness()['status'] == 'READY'
    assert service.advance()['status'] == 'REQUEST_BOUND'
    assert service.submission.calls == []
    assert service.advance()['status'] == 'PUBLIC_PREVIEWED'
    assert service.advance()['status'] == 'PUBLIC_TASK_FROZEN'
    result = service.advance()
    assert result['public_stage_status'] == 'ACCOUNT_VERIFIED'
    assert result['dispatched_segments'] == 1
    assert service.submission.calls == ['preview', 'freeze', ('advance', service.submission.task['task_id'])]
    assert service.advance()['status'] == 'BUSINESS_EVIDENCE_RECORDED'
    assert len(service.submission.calls) == 3
    assert set(service.human_results()) == {'passed'}
    context = service.design_context()
    assert context['confirmation_results_visible_to_design'] is False
    assert 'reports' not in context and 'metadata' not in context and 'protocol_identity' not in context
    assert 'confirmation_input' not in json.dumps(context)


@pytest.mark.parametrize('terminal', ['COMPLETED', 'FAILED', 'UNKNOWN'])
def test_intermediate_completion_and_failure_never_create_new_validation_task(tmp_path, snapshot, terminal):
    service, _, _, protocol = setup_case(tmp_path, snapshot)
    wire(service, protocol, tmp_path)
    service.submission.final_status = terminal
    for _ in range(4):
        service.advance()
    assert service.human_results() == {}
    count = len(service.submission.calls)
    if terminal in ('FAILED', 'UNKNOWN'):
        assert service.advance()['status'] == 'RECONCILIATION_REQUIRED'
        assert len(service.submission.calls) == count
    assert service.submission.calls.count('freeze') == 1


def test_251_days_and_known_exposure_wait_while_exploration_remains_available(tmp_path, snapshot):
    service, _, _, protocol = setup_case(tmp_path, snapshot)
    short = admission(protocol, count=251)
    service.admit = lambda context: short
    assert 'INDEPENDENT_ACCOUNT_SESSIONS_INSUFFICIENT' in service.readiness()['reason_codes']
    complete = admission(protocol)
    service.admit = lambda context: complete
    service.exposures = lambda: [{'content_hash': complete['metadata']['content_hash'],
        'window': complete['metadata']['window'], 'symbols': SYMBOLS,
        'purpose': 'ACCOUNT_STARTED', 'evidence_ref': {'receipt_path': str(tmp_path / 'foreign' / 'CONFIRMATION.json')}}]
    result = service.advance()
    assert 'INDEPENDENT_DATA_ALREADY_EXPOSED' in result['reason_codes']
    assert result['exploration_may_continue'] is True


def test_selection_rule_mutation_and_sealed_route_are_rejected(tmp_path, snapshot):
    service, _, arguments, _ = setup_case(tmp_path, snapshot)
    changed = deepcopy(arguments)
    changed['requests']['passed']['rule']['selection']['direction'] = 'DESCENDING'
    with pytest.raises(ValueError, match='RULE_IDENTITY_CONFLICT'):
        service.preview(**changed)
    changed = deepcopy(arguments)
    changed['selection_policy']['maximum_independent_exposures'] = 2
    with pytest.raises(ValueError, match='SELECTION_POLICY_INVALID'):
        service.preview(**changed)
    with pytest.raises(ValueError, match='SEALED_ROUTE_NOT_AUTHORIZED'):
        service.preview(**arguments, route='SEALED_HISTORICAL')


def test_report_original_tamper_is_not_accepted_as_business_evidence(tmp_path, snapshot):
    service, _, _, protocol = setup_case(tmp_path, snapshot)
    wire(service, protocol, tmp_path)
    for _ in range(4):
        service.advance()
    report = tmp_path / 'public_task' / 'BASE_RESEARCH_REPORT.json'
    report.write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='REPORT_ORIGINAL_CHANGED'):
        service.human_results()


def test_snapshot_projection_keeps_partial_pool_and_company_action_gaps(tmp_path):
    store = SnapshotStoreV1(tmp_path)
    day = 20270104
    body = payload(day, SYMBOLS[:1])
    body['corporate_actions_complete'] = False
    body['corporate_actions'] = [{'symbol': SYMBOLS[0], 'date': day, 'type': 'UNRESOLVED_TDX_ACTION'}]
    snap = store.record_synthetic(phase='CLOSE', market_date=day, payload=body,
                                  received_at='2027-01-04T15:01:00+08:00')
    projection = universe_snapshot_projection(store, [snap['snapshot_id']], target_symbols=SYMBOLS,
        calendar=[day, 20270105], feature_start=day, account_start=20270105, account_end=20270105,
        frozen_at='2026-10-10T10:00:00+08:00', profile='SYNTHETIC')
    assert projection['target_count'] == 3
    assert projection['ready_for_registered_data_preparation'] is False
    assert projection['account_sessions'] == 0
    assert projection['corporate_actions'] == body['corporate_actions']
    assert projection['gaps'] and projection['independence_granted'] is False
    with pytest.raises(ValueError, match='PROFILE_CONFLICT'):
        universe_snapshot_projection(store, [snap['snapshot_id']], target_symbols=SYMBOLS,
            calendar=[day, 20270105], feature_start=day, account_start=20270105, account_end=20270105,
            frozen_at='2026-10-10T10:00:00+08:00')


def test_business_connector_leaves_old_formal_504_day_contract_unchanged(tmp_path, snapshot):
    from chanlun_trader.research_factory.validation_protocol_v2 import validation_requirements
    service, _, _, _ = setup_case(tmp_path, snapshot)
    prior = CampaignConfirmationV1(service.research, method_resolver=lambda scope: {'applicable': False})
    assert isinstance(prior.business_protocol(exposure_reader=lambda: []), BusinessValidationProtocolV1)
    assert validation_requirements('FUTURE_OBSERVED')['account_sessions'] == 504
    assert validation_requirements('FUTURE_OBSERVED')['method_support_required'] is True


def test_original_metrics_can_meet_business_criteria_but_synthetic_never_completes_goal(tmp_path, snapshot):
    service, _, _, protocol = setup_case(tmp_path, snapshot)
    wire(service, protocol, tmp_path)
    for _ in range(4):
        service.advance()
    assessment = service.human_results()['passed']['business_validation']
    assert assessment['thresholds_passed'] is True
    assert assessment['reporting_complete'] is True
    assert assessment['business_criteria_met'] is True
    assert service.status()['business_goal_met'] is False
    assert 'metrics' not in service.design_context()


def test_net_profit_factor_zero_loss_boundary_and_reporting_gaps(tmp_path, snapshot):
    service, _, arguments, protocol = setup_case(tmp_path, snapshot)
    wire(service, protocol, tmp_path)
    for _ in range(4):
        service.advance()
    refs = service.human_results()['passed']['verified_report_refs']
    reports = {cost: json.loads(Path(row['report_ref']['research_report']).read_text(encoding='utf-8'))
               for cost, row in refs.items()}
    for episode in reports['BASE']['account']['episodes']:
        episode['net_profit'] = 1.
    reports['BASE']['account']['closed_episode_win_rate'] = 1.
    check = evaluate_business_reports(arguments['contract'], reports, minimum_sessions=252)
    assert check['metrics']['base_complete_round_trip_net_profit_factor_minimum'] is None
    assert check['checks']['base_complete_round_trip_net_profit_factor_minimum'] is True
    assert check['reporting_complete'] is False  # 改利润而不改原件归因不能通过。
    for episode in reports['BASE']['account']['episodes']:
        episode['net_profit'] = 0.
    check = evaluate_business_reports(arguments['contract'], reports, minimum_sessions=252)
    assert check['checks']['base_complete_round_trip_net_profit_factor_minimum'] is False


def test_final_exploration_shortfall_blocks_independent_admission_before_data_use(tmp_path, snapshot):
    service, material, _, protocol = setup_case(tmp_path, snapshot)
    wire(service, protocol, tmp_path)
    service.admit = lambda _: pytest.fail('不足504账户日时不得读取独立资料')
    material['final_exploration_ready'] = []
    assert service.advance()['reason_codes'] == ['FINAL_EXPLORATION_EVIDENCE_NOT_READY']


def test_async_public_freeze_recovers_canonical_account_task_without_refreezing(tmp_path, snapshot):
    service, _, _, protocol = setup_case(tmp_path, snapshot)
    evidence = wire(service, protocol, tmp_path)
    original = service.submission.freeze
    def asynchronous(request, preview_identity):
        task = original(request, preview_identity)
        return {'task_id': task['task_id'], 'status': 'PREPARING'}
    service.submission.freeze = asynchronous
    service.submission._task = lambda task_id: deepcopy(service.submission.task)
    for _ in range(3):
        service.advance()
    # 公共批准先于账户完成；本地TASK仍是PREPARING时也要识别原任务的合法暴露。
    own = {'content_hash': evidence['metadata']['content_hash'], 'window': evidence['metadata']['window'],
        'symbols': SYMBOLS, 'purpose': 'ACCOUNT_AUTHORIZED_POSSIBLE_EXPOSURE',
        'evidence_ref': {'receipt_path': str(tmp_path / 'public_task' / 'CONFIRMATION.json')}}
    service.exposures = lambda: [own]
    assert service.advance()['public_stage_status'] == 'ACCOUNT_VERIFIED'
    assert service.human_results()['passed']['business_validation']['thresholds_passed'] is True
    assert service.submission.calls.count('freeze') == 1
    assert service._candidate_path('passed', 'ACCOUNT_TASK.json').exists()
    service.exposures = lambda: [own, {**own,
        'evidence_ref': {'receipt_path': str(tmp_path / 'foreign_task' / 'CONFIRMATION.json')}}]
    assert 'INDEPENDENT_DATA_ALREADY_EXPOSED' in service.advance()['reason_codes']


def test_final_selection_requires_original_reports_and_freezes_their_identity(tmp_path, snapshot):
    service, material, arguments, protocol = setup_case(tmp_path, snapshot)
    evidence = protocol['preview']['final_exploration_evidence_refs']['passed']
    assert evidence['source'] == 'INITIAL'
    assert {item['sessions'] for item in evidence['verified_report_refs'].values()} == {504}
    before = {str(path): path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    assert service.load() == protocol
    assert before == {str(path): path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    material['attempts'][1]['screen']['final_exploration_ready'] = False
    preview = service.preview(**arguments)
    assert preview['final_exploration_evidence_refs'] == {}
    with pytest.raises(ValueError, match='FINAL_EXPLORATION_EVIDENCE_REQUIRED'):
        service.freeze(**arguments, preview_identity=preview['preview_identity'])


def test_promoted_queue_evidence_replaces_short_screen_without_changing_record(tmp_path, snapshot, monkeypatch):
    """隔离可信队列接口的接线，不把模拟队列声明当作真实验收。"""
    from chanlun_trader.research_factory.final_exploration_queue_v1 import FinalExplorationQueueV1
    service, material, arguments, _ = setup_case(tmp_path, snapshot)
    material['attempts'][1]['screen']['final_exploration_ready'] = False
    original_record = deepcopy(material['attempts'][1])
    source = service.research.submission
    directory = tmp_path / 'final_exploration' / 'passed'
    directory.mkdir(parents=True)
    (directory / 'PREVIEW.json').write_bytes((tmp_path / 'passed' / 'PREVIEW.json').read_bytes())
    evidence_path = directory / 'EVIDENCE.json'
    evidence_path.write_text('{"SYNTHETIC_QUEUE_INTERFACE":true}', encoding='utf-8')
    promoted = {'status': 'FINAL_EXPLORATION_READY', 'candidate_id': 'passed',
        'identity': stable_hash('synthetic_promoted'), 'final_template_identity': stable_hash('final_template'),
        'request': arguments['requests']['passed'], 'task': source.task,
        'outcome': json.loads((tmp_path / 'passed' / 'PUBLIC_RESULT.json').read_text(encoding='utf-8')),
        'evidence_path': str(evidence_path), 'evidence_sha256': hashlib.sha256(evidence_path.read_bytes()).hexdigest()}
    monkeypatch.setattr(FinalExplorationQueueV1, 'verified_evidence', lambda _, candidate: deepcopy(promoted))
    original_resolver = source.continuous_scope.resolve
    source.continuous_scope.resolve = lambda *args, **kwargs: {
        **original_resolver(*args, **kwargs), 'batch_id': 'FINAL_passed'}
    result = verified_final_exploration_evidence(service.research, 'passed', arguments['contract'])
    assert result['source'] == 'PROMOTED'
    assert result['source_record_identity'] == stable_hash(original_record)
    assert result['task_id'] == source.task['task_id']
    assert material['attempts'][1] == original_record
    promoted['status'] = 'FINAL_EXPLORATION_FAILED'
    assert verified_final_exploration_evidence(service.research, 'passed', arguments['contract']) is None
    promoted['status'] = 'FINAL_EXPLORATION_READY'
    (source.root / 'public_task' / 'BASE_RESEARCH_REPORT.json').write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='REPORT_ORIGINAL_CHANGED'):
        verified_final_exploration_evidence(service.research, 'passed', arguments['contract'])


def test_future_train_final_binding_rechecks_owner_metadata_without_reopening_data(tmp_path, snapshot):
    service, material, arguments, _ = setup_case(tmp_path, snapshot)
    source, request = service.research.submission, arguments['requests']['passed']
    frozen = deepcopy(arguments['contract'])
    frozen['scope']['data_routes']['EXPLORATION'] = {'route_id': 'synthetic_future_train'}
    original_resolver = source.continuous_scope.resolve
    proof = {'schema_version': 'VERIFIED_TRAIN_PROJECTION_ADMISSION_V1',
             'independent_confirmation_eligible': False, 'synthetic_metadata_interface': True}
    source.continuous_scope.resolve = lambda *args, **kwargs: {
        **original_resolver(*args, **kwargs),
        'request': {**original_resolver(*args, **kwargs)['request'], 'data_route_proof': proof}}
    policy = {'synthetic_owner_metadata_only': True}
    source.continuous_scope.campaign = SimpleNamespace(_authorization=lambda: {'scope_policy': policy})
    calls = []
    def verify_owner(actual_policy, summary):
        assert actual_policy == policy and summary['data_route_proof'] == proof
        calls.append('owner_metadata')
    source.continuous_scope._future_train_dataset = verify_owner
    identity = material['attempts'][1]['rule_identity']
    result = verify_exploration_binding(source, request, frozen,
        candidate_identity=identity, batch_id='BATCH_SYNTHETIC')
    assert result['request']['data_route_proof'] == proof and calls == ['owner_metadata']
    def owner_changed(*_):
        raise PermissionError('CAMPAIGN_TRAIN_PROJECTION_PROOF_CHANGED')
    source.continuous_scope._future_train_dataset = owner_changed
    with pytest.raises(PermissionError, match='TRAIN_PROJECTION_PROOF_CHANGED'):
        verify_exploration_binding(source, request, frozen,
            candidate_identity=identity, batch_id='BATCH_SYNTHETIC')


def test_registered_confirmation_binding_requires_persisted_admission_and_selected_rule(tmp_path, snapshot):
    """元数据分支回归；不创建授权、资料scope或公共账户任务。"""
    from chanlun_trader.research_factory.campaign_scope_v1 import CampaignScopeV1
    from chanlun_trader.research_factory.continuous_submission_v1 import _independent_binding
    frozen = contract(snapshot)
    selected_identity = stable_hash('selected_rule')
    root = tmp_path / 'campaign'
    protocol_path = root / 'diagnosis_v4' / 'business_validation' / 'PROTOCOL.json'
    protocol_path.parent.mkdir(parents=True)
    protocol = {'protocol_identity': stable_hash('synthetic_protocol'),
        'preview': {'contract': frozen, 'selected': ['selected'], 'rules': {'selected': selected_identity}}}
    admitted = {'source_authenticated': True, 'prior_access_review_passed': True, 'post_freeze_unseen': True,
        'metadata': {'dataset_id': 'confirmation',
                     'content_hash': frozen['scope']['data_routes']['CONFIRMATION']['dataset_hash']}}
    protocol_path.write_text(json.dumps(protocol), encoding='utf-8')
    (protocol_path.parent / 'ADMISSION.json').write_text(json.dumps(admitted), encoding='utf-8')
    calls = []
    def fixed_admission(context):
        calls.append(context)
        return deepcopy(admitted)
    service = SimpleNamespace(independent_admission=fixed_admission, independent_protocol_path=protocol_path,
        continuous_scope=SimpleNamespace(campaign=SimpleNamespace(directory=root)))
    proof = _independent_binding(service, {'phase': 'CONFIRMATION'}, frozen)
    assert len(calls) == 1 and calls[0]['frozen_admission'] == admitted
    assert proof['data_route_proof']['protocol_sha256'] == hashlib.sha256(protocol_path.read_bytes()).hexdigest()
    service.independent_admission = lambda _: {**admitted, 'source_authenticated': False}
    with pytest.raises(PermissionError, match='FUTURE_ADMISSION_CHANGED'):
        _independent_binding(service, {'phase': 'CONFIRMATION'}, frozen)
    scope = CampaignScopeV1(SimpleNamespace(directory=root))
    summary = {'phase': 'CONFIRMATION', 'dataset_id': 'confirmation',
        'dataset_hash': admitted['metadata']['content_hash'], 'data_route_proof': proof['data_route_proof']}
    policy = {'summary': {'contract': frozen}}
    with pytest.raises(PermissionError, match='FUTURE_DATA_ROUTE_CONFLICT'):
        scope._future_dataset(policy, summary, stable_hash('unselected_rule'))
    with pytest.raises(PermissionError, match='FUTURE_DATA_OWNER_PROOF_REQUIRED'):
        scope._future_dataset({**policy, 'approval_store': str(tmp_path / 'unused_owner')}, summary, selected_identity)
