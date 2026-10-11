"""完整家族的一次独立业务验证；账户执行复用公共有界入口。"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import math
from pathlib import Path
import re

from .common import stable_hash
from .continuous_research_contract_v1 import validate_continuous_research_contract
from .exploration_governance import immutable, read_json
from .mutation_boundary import ObjectiveMutationLock
from .research_data_qualification_v1 import audit_data_qualification, read_governance_exposures
from .strategy_submission_v1 import public_rule_factory


VERSION = 'BUSINESS_VALIDATION_PROTOCOL_V1'


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _day(value):
    text = str(value).replace('-', '')
    _require(re.fullmatch(r'\d{8}', text) is not None, 'BUSINESS_VALIDATION_DATE_INVALID')
    return datetime.strptime(text, '%Y%m%d').strftime('%Y-%m-%d')


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def _file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def evaluate_business_reports(contract, reports, *, minimum_sessions):
    """只核对已绑定原件的业务数值；不代替账户核账或独立准入。"""
    checks, metrics, gaps = {}, {}, []
    accounts = {cost: reports.get(cost, {}).get('account', {}) for cost in ('BASE', 'STRESS')}
    def number(value):
        return type(value) in (int, float) and math.isfinite(value)
    for cost, account in accounts.items():
        checks[cost + '_cash'] = account.get('initial_cash') == 50000
        checks[cost + '_sessions'] = (type(account.get('sessions')) is int
            and account['sessions'] >= minimum_sessions)
    base, stress = accounts['BASE'], accounts['STRESS']
    checks['same_sessions'] = base.get('sessions') == stress.get('sessions')
    closed = [row for row in base.get('episodes', []) if row.get('status') == 'CLOSED']
    profits = [row.get('net_profit') for row in closed]
    complete = (bool(profits) and all(number(value) for value in profits)
        and type(base.get('closed_episodes')) is int and base['closed_episodes'] == len(closed))
    checks['complete_net_round_trips'] = complete
    positive = sum(max(0., value) for value in profits) if complete else 0.
    negative = -sum(min(0., value) for value in profits) if complete else 0.
    annual = ((base['final_equity'] / 50000) ** (252 / base['sessions']) - 1
        if checks['BASE_sessions'] and number(base.get('final_equity')) and base['final_equity'] > 0 else None)
    win = sum(value > 0 for value in profits) / len(profits) if complete else None
    checks['reported_win_rate_matches_net_round_trips'] = (number(base.get('closed_episode_win_rate'))
        and win is not None and math.isclose(base['closed_episode_win_rate'], win, abs_tol=1e-12))
    metrics = {'base_annualized_net_return_minimum': annual,
        'base_maximum_drawdown_maximum': base.get('max_drawdown'),
        'base_complete_round_trip_net_win_rate_minimum': win,
        'base_complete_round_trip_net_profit_factor_minimum': positive / negative if negative else None,
        'base_complete_round_trips_minimum': len(closed) if complete else None,
        'stress_total_net_return_strictly_greater_than': stress['final_equity'] / 50000 - 1
            if number(stress.get('final_equity')) else None}
    for key, bound in contract['final_criteria']['thresholds_each_stage'].items():
        value = metrics[key]
        checks[key] = (complete and positive > 0 if key == 'base_complete_round_trip_net_profit_factor_minimum'
            and negative == 0 else number(value) and (value <= bound if key.endswith('_maximum')
            else value > bound if key.endswith('_greater_than') else value >= bound))
    checks['drawdown_range'] = number(base.get('max_drawdown')) and 0 <= base['max_drawdown'] <= 1
    for cost, report in reports.items():
        account, signal = report.get('account', {}), report.get('signal', {})
        diagnostics = account.get('business_diagnostics', {})
        periods, dates = diagnostics.get('subperiods'), diagnostics.get('account_dates', [])
        if (diagnostics.get('version') != 'CONTINUOUS_BUSINESS_DIAGNOSTICS_V1'
                or diagnostics.get('subperiod_policy') != 'FROZEN_ACCOUNT_SESSION_HALVES_CONTINUOUS_EQUITY'
                or diagnostics.get('equity_includes_open_positions') is not True
                or not isinstance(dates, list) or dates != sorted(set(dates))
                or len(dates) != account.get('sessions')
                or not isinstance(periods, list) or len(periods) != 2):
            gaps.append(cost + '_ACCOUNT_SUBPERIOD_EVIDENCE_MISSING')
        else:
            boundary, offset, opening = max(1, len(dates) // 2), 0, 50000
            for index, period in enumerate(periods):
                count = boundary if index == 0 else len(dates) - boundary
                valid = (period.get('period') == ('FIRST_HALF' if index == 0 else 'SECOND_HALF')
                    and period.get('sessions') == count and period.get('account_start') == dates[offset]
                    and period.get('account_end') == dates[offset + count - 1]
                    and all(number(period.get(key)) for key in ('opening_equity', 'closing_equity',
                        'net_profit', 'net_return', 'maximum_drawdown'))
                    and period['opening_equity'] == opening and period['closing_equity'] > 0
                    and 0 <= period['maximum_drawdown'] <= 1
                    and math.isclose(period['net_profit'], period['closing_equity'] - opening, abs_tol=1e-8)
                    and math.isclose(period['net_return'], period['closing_equity'] / opening - 1, abs_tol=1e-12))
                if not valid:
                    gaps.append(cost + '_ACCOUNT_SUBPERIOD_IDENTITY_CONFLICT')
                    break
                opening, offset = period['closing_equity'], offset + count
            if opening != account.get('final_equity'):
                gaps.append(cost + '_ACCOUNT_SUBPERIOD_CLOSING_EQUITY_CONFLICT')
        episodes = [row for row in account.get('episodes', []) if row.get('status') == 'CLOSED']
        if any(not row.get('symbol') or not row.get('end_session') or not number(row.get('net_profit'))
               for row in episodes):
            gaps.append(cost + '_CONCENTRATION_ATTRIBUTION_INCOMPLETE')
        else:
            symbols = {}
            for row in episodes:
                symbols[row['symbol']] = symbols.get(row['symbol'], 0.) + row['net_profit']
            profits = sorted((row['net_profit'] for row in episodes), reverse=True)
            expected = {'symbol': dict(sorted(symbols.items())), 'time_period': periods,
                'complete_round_trip': {'count': len(profits), 'net_profit': sum(profits),
                    'without_best_1_profit': sum(profits[1:]), 'without_best_3_profit': sum(profits[3:]),
                    'policy': 'ATTRIBUTION_NOT_COUNTERFACTUAL_ACCOUNT'}}
            if diagnostics.get('concentration') != expected:
                gaps.append(cost + '_CONCENTRATION_ATTRIBUTION_CONFLICT')
        if diagnostics.get('benchmarks') != {'CASH': {'net_return': 0., 'interest_assumed': 0.},
                'PRICE_REFERENCE': {'policy': 'SYSTEM_SUPPORTED_PRICE_REFERENCE_WITH_INVESTABILITY_DISCLOSED',
                    'metrics_source': 'PUBLIC_FINAL_REPORT_BENCHMARK', 'investable_claim': False}}:
            gaps.append(cost + '_BENCHMARK_DISCLOSURE_CONFLICT')
        if not all(number(account.get(key)) for key in ('average_cash', 'average_equity_less_cash_fraction')):
            gaps.append(cost + '_CAPITAL_UTILIZATION_MISSING')
        if (signal.get('account_independent_denominator') is not True
                or not signal.get('statistics') or not signal.get('limitations')):
            gaps.append(cost + '_PRICE_REFERENCE_DISCLOSURE_MISSING')
    return {'thresholds_passed': all(checks.values()), 'checks': checks, 'metrics': metrics,
        'reporting_complete': not gaps, 'reason_codes': gaps,
        'profit_factor_basis': {'positive_net_profit_sum': positive, 'absolute_negative_net_profit_sum': negative},
        'profit_factor_zero_loss_policy': 'POSITIVE_NET_PROFIT_WITH_ZERO_LOSS_SATISFIES_FINITE_MINIMUM_PF_IS_NULL',
        'business_criteria_met': all(checks.values()) and not gaps}


def verify_exploration_binding(submission, request, contract, *, candidate_identity, batch_id):
    """审计既有委托；未来TRAIN只重核Owner原件，不重新打开行情或刷新读取scope。"""
    from .continuous_submission_v1 import request_scope
    scope = submission.continuous_scope
    binding = scope.resolve(request['research_binding_ref'], for_dispatch=False)
    dataset = next(item for item in submission.provider.catalog()['datasets']
        if item['dataset_id'] == request['dataset_id'])
    expected = request_scope(request, contract, dataset)
    if 'dataset_id' not in contract['scope']['data_routes']['EXPLORATION']:
        expected['data_route_proof'] = binding.get('request', {}).get('data_route_proof', {})
        policy = scope.campaign._authorization()['scope_policy']
        scope._future_train_dataset(policy, expected)
    _require(binding.get('candidate_identity') == candidate_identity
        and binding.get('batch_id') == batch_id and binding.get('phase') == 'EXPLORATION'
        and binding.get('request') == expected,
        'BUSINESS_VALIDATION_FINAL_PROTECTED_BINDING_CONFLICT')
    return binding


def verified_final_exploration_evidence(research, candidate_id, contract):
    """只读重核同规则的最终探索原件；初筛名单本身不构成504日证据。"""
    records = research.confirmation_material()['attempts']
    matches = [row for row in records if row['candidate_id'] == candidate_id]
    _require(len(matches) == 1 and Path(candidate_id).name == candidate_id,
             'BUSINESS_VALIDATION_FINAL_CANDIDATE_CONFLICT')
    record = matches[0]
    promoted_path = Path(research.root) / 'final_exploration' / candidate_id / 'EVIDENCE.json'
    if promoted_path.exists():
        from .final_exploration_queue_v1 import FinalExplorationQueueV1
        promoted = FinalExplorationQueueV1(research).verified_evidence(candidate_id)
        if promoted is None or promoted['status'] != 'FINAL_EXPLORATION_READY':
            return None
        directory = Path(promoted['evidence_path']).parent
        request, task, outcome = (promoted[key] for key in ('request', 'task', 'outcome'))
        source = {'source': 'PROMOTED', 'evidence_identity': promoted['identity'],
            'evidence_path': promoted['evidence_path'], 'evidence_sha256': promoted['evidence_sha256'],
            'final_template_identity': promoted['final_template_identity']}
    else:
        if (record.get('screen') or {}).get('final_exploration_ready') is not True:
            return None
        directory = Path(research.root) / candidate_id.lower()
        if not all((directory / name).is_file() for name in
                   ('REQUEST.json', 'PREVIEW.json', 'TASK.json', 'PUBLIC_RESULT.json', 'SCREEN.json')):
            return None
        screen = read_json(directory / 'SCREEN.json')
        _require(stable_hash(screen) == record['screen']['identity']
            and screen.get('final_exploration_ready') is True,
            'BUSINESS_VALIDATION_INITIAL_FINAL_SCREEN_CHANGED')
        request, reference, outcome = (read_json(directory / name) for name in
            ('REQUEST.json', 'TASK.json', 'PUBLIC_RESULT.json'))
        task = research.submission._task(reference['task_id'])
        if callable(getattr(research, '_screen', None)):
            _require(research._screen(directory, task, outcome, research.config()) == screen,
                     'BUSINESS_VALIDATION_INITIAL_FINAL_SCREEN_CHANGED')
        source = {'source': 'INITIAL', 'evidence_identity': stable_hash(screen),
            'evidence_path': str(directory / 'SCREEN.json'),
            'evidence_sha256': _file_hash(directory / 'SCREEN.json')}
    preview = read_json(directory / 'PREVIEW.json')
    strategy = public_rule_factory(request['rule'], candidate_id)
    _require(request.get('version') == task.get('submission_version') == 'FULL_UNIVERSE_SUBMISSION_V4'
        and request.get('phase') == 'EXPLORATION' and request.get('purpose') == 'EXPLORATORY'
        and strategy.rule_identity == record['rule_identity'] == preview.get('rule_identity')
        and outcome.get('rule_identity') == strategy.rule_identity
        and outcome.get('input_identity') == task['input_identity']
        and preview.get('preview_identity') == task.get('preview_identity')
        and preview['preview_identity'] == stable_hash({key: value for key, value in preview.items()
            if key != 'preview_identity'}), 'BUSINESS_VALIDATION_FINAL_PUBLIC_RULE_CONFLICT')
    canonical_preview = read_json(Path(task['job_path']).parent.parent / 'PREVIEW.json')
    _require(canonical_preview == preview
        and all(preview.get('request', {}).get(key) == value for key, value in request.items()),
        'BUSINESS_VALIDATION_FINAL_PUBLIC_REQUEST_CONFLICT')
    expected_batch = 'FINAL_' + candidate_id if source['source'] == 'PROMOTED' else record['batch_id']
    verify_exploration_binding(research.submission, request, contract,
        candidate_identity=strategy.rule_identity, batch_id=expected_batch)
    job = read_json(task['job_path'])
    _require(all(item.get('factory_kwargs', {}).get('payload') == request['rule']
        for name, item in job['items'].items() if name in task['plan_ids']),
        'BUSINESS_VALIDATION_FINAL_EXECUTED_RULE_CONFLICT')
    minimum = contract['final_criteria']['exploration_minimum_actual_sessions']
    refs = BusinessValidationProtocolV1.verify_report_refs(task, outcome, minimum_sessions=minimum)
    reports = {cost: read_json(item['report_ref']['research_report']) for cost, item in refs.items()}
    assessment = evaluate_business_reports(contract, reports, minimum_sessions=minimum)
    if not assessment['business_criteria_met']:
        return None
    _require(reports['BASE']['account']['business_diagnostics']['account_dates']
        == reports['STRESS']['account']['business_diagnostics']['account_dates'],
        'BUSINESS_VALIDATION_FINAL_ACCOUNT_CALENDAR_CONFLICT')
    return {**source, 'candidate_id': candidate_id, 'source_record_identity': stable_hash(record),
        'rule_identity': strategy.rule_identity, 'task_id': task['task_id'],
        'input_identity': task['input_identity'], 'verified_report_refs': refs}


class BusinessValidationProtocolV1:
    """注入对象均由部署提供；研究模型只能看 design_context() 的安全摘要。"""

    def __init__(self, research, *, submission_service=None, data_admission=None,
                 stage_binder=None, exposure_reader=None):
        self.research = research
        self.root = Path(research.root).resolve() / 'business_validation'
        self.protocol_path = self.root / 'PROTOCOL.json'
        self.submission = submission_service
        self.admit = data_admission
        self.bind_stage = stage_binder
        project = getattr(getattr(research, 'campaign', None), 'root', research.root)
        self.exposures = exposure_reader or (lambda: read_governance_exposures(project))

    def preview(self, *, contract, selected, requests, selection_policy, not_before,
                route='FUTURE_OBSERVED'):
        contract = validate_continuous_research_contract(contract)
        material = self.research.confirmation_material()
        _require(route == 'FUTURE_OBSERVED', 'BUSINESS_VALIDATION_SEALED_ROUTE_NOT_AUTHORIZED')
        family = material['attempts']
        identities = [item['candidate_id'] for item in family]
        _require(len(identities) == len(set(identities)) and family,
                 'BUSINESS_VALIDATION_FAMILY_INVALID')
        _require(isinstance(selected, list) and selected and len(selected) == len(set(selected))
            and set(selected) <= set(material['selected']) and set(selected) <= set(identities),
            'BUSINESS_VALIDATION_SCREENED_SELECTION_REQUIRED')
        policy = selection_policy
        _require(isinstance(policy, dict) and set(policy) == {'version', 'maximum_selected_candidates',
            'maximum_independent_exposures', 'selection_basis', 'result_use'}
            and isinstance(policy['version'], str) and policy['version']
            and type(policy['maximum_selected_candidates']) is int
            and 1 <= len(selected) <= policy['maximum_selected_candidates']
            and type(policy['maximum_independent_exposures']) is int
            and policy['maximum_independent_exposures'] == 1
            and isinstance(policy['selection_basis'], str) and policy['selection_basis']
            and policy['result_use'] == 'DESCRIPTIVE_BUSINESS_CHECK_NO_MAXIMIZATION',
            'BUSINESS_VALIDATION_SELECTION_POLICY_INVALID')
        _require(isinstance(requests, dict) and set(requests) == set(selected),
                 'BUSINESS_VALIDATION_SELECTED_REQUESTS_REQUIRED')
        records = {item['candidate_id']: item for item in family}
        rules, frozen_requests, warmup = {}, {}, 0
        for candidate_id in sorted(selected):
            request = deepcopy(requests[candidate_id])
            _require(isinstance(request, dict) and 'rule' in request,
                     'BUSINESS_VALIDATION_REQUEST_INVALID')
            strategy = public_rule_factory(request['rule'], candidate_id)
            _require(strategy.rule_identity == records[candidate_id].get('rule_identity'),
                     'BUSINESS_VALIDATION_RULE_IDENTITY_CONFLICT')
            _require(request.get('initial_cash') == contract['scope']['initial_cash']
                and request.get('max_positions') == contract['scope']['max_positions']
                and request.get('universe_id') == contract['scope']['universe_id']
                and request.get('costs') == ['BASE', 'STRESS']
                and request.get('account_scope') == 'DATA_QUALIFIED',
                'BUSINESS_VALIDATION_REQUEST_SCOPE_CONFLICT')
            _require(request['rule']['version'] == contract['scope']['rule_version'],
                     'BUSINESS_VALIDATION_RULE_VERSION_CONFLICT')
            _require(set(strategy.parameters['factor_ids']) <= set(contract['scope']['indicator_roles']),
                     'BUSINESS_VALIDATION_INDICATOR_OUTSIDE_SCOPE')
            rules[candidate_id] = strategy.rule_identity
            warmup = max(warmup, strategy.requirements.warmup_sessions)
            frozen_requests[candidate_id] = request
        value = {'version': VERSION, 'contract': contract, 'contract_hash': contract['content_hash'],
            'family': deepcopy(family), 'selected': sorted(selected), 'rules': rules,
            'requests': frozen_requests, 'selection_policy': deepcopy(policy),
            'research_selection_policy': deepcopy(material['selection_policy']),
            'prior_exposure_refs': deepcopy(material['exposures']),
            'route': route, 'not_before': _day(not_before),
            'profile': 'SYNTHETIC' if material.get('profile') == 'SYNTHETIC' else 'REAL_OBSERVED',
            'minimum_account_sessions': contract['final_criteria']['independent_validation_minimum_actual_sessions'],
            'required_warmup_sessions': warmup,
            'formal_method_required_for_business_execution': False,
            'formal_qualification_policy': 'SEPARATE_CANONICAL_ADJUDICATION_REQUIRED',
            'confirmation_results_visible_to_design': False, 'strategy_qualified': False}
        value['final_exploration_evidence_refs'] = {candidate: evidence for candidate in sorted(selected)
            if (evidence := verified_final_exploration_evidence(self.research, candidate, contract)) is not None}
        return {**value, 'preview_identity': stable_hash(value)}

    def freeze(self, *, preview_identity, **kwargs):
        preview = self.preview(**kwargs)
        _require(preview['preview_identity'] == preview_identity,
                 'BUSINESS_VALIDATION_FAMILY_OR_PREVIEW_CHANGED')
        _require(set(preview['final_exploration_evidence_refs']) == set(preview['selected']),
                 'BUSINESS_VALIDATION_FINAL_EXPLORATION_EVIDENCE_REQUIRED')
        with ObjectiveMutationLock.for_resource(self.protocol_path):
            if self.protocol_path.exists():
                prior = self.load()
                _require(prior['preview'] == preview, 'BUSINESS_VALIDATION_ALREADY_FROZEN')
                return prior
            frozen_at = datetime.now(timezone.utc).isoformat()
            _require(preview['not_before'] > _day(frozen_at[:10]),
                     'BUSINESS_VALIDATION_FUTURE_START_REQUIRED')
            value = {'preview': preview, 'frozen_at': frozen_at}
            value['protocol_identity'] = stable_hash(value)
            immutable(self.protocol_path, value)
            return value

    def load(self):
        protocol = read_json(self.protocol_path)
        _require(protocol['protocol_identity'] == stable_hash({key: value for key, value in protocol.items()
            if key != 'protocol_identity'}), 'BUSINESS_VALIDATION_PROTOCOL_CHANGED')
        preview = protocol['preview']
        _require(preview['preview_identity'] == stable_hash({key: value for key, value in preview.items()
            if key != 'preview_identity'}), 'BUSINESS_VALIDATION_PREVIEW_CHANGED')
        validate_continuous_research_contract(preview['contract'])
        material = self.research.confirmation_material()
        current = {item['candidate_id']: item for item in material['attempts']}
        _require(len(current) == len(material['attempts'])
            and material['attempts'][:len(preview['family'])] == preview['family'],
                 'BUSINESS_VALIDATION_FROZEN_FAMILY_CHANGED')
        _require(material['selection_policy'] == preview['research_selection_policy']
            and all(item in material['exposures'] for item in preview['prior_exposure_refs']),
            'BUSINESS_VALIDATION_SELECTION_OR_EXPOSURE_CHANGED')
        expected_profile = 'SYNTHETIC' if material.get('profile') == 'SYNTHETIC' else 'REAL_OBSERVED'
        _require(preview['profile'] == expected_profile, 'BUSINESS_VALIDATION_PROFILE_CHANGED')
        _require(set(preview.get('final_exploration_evidence_refs', {})) == set(preview['selected']),
                 'BUSINESS_VALIDATION_FROZEN_FINAL_EXPLORATION_EVIDENCE_REQUIRED')
        for candidate in preview['selected']:
            _require(verified_final_exploration_evidence(self.research, candidate, preview['contract'])
                == preview.get('final_exploration_evidence_refs', {}).get(candidate),
                'BUSINESS_VALIDATION_FROZEN_FINAL_EXPLORATION_EVIDENCE_CHANGED')
        return protocol

    def _candidate_path(self, candidate_id, filename):
        # Candidate ID 来自冻结家族，路径只用其内容哈希。
        return self.root / 'candidates' / stable_hash(candidate_id) / filename

    def _foreign_exposures(self, exposures):
        own = set()
        for path in list(self.root.glob('candidates/*/TASK.json')) + list(self.root.glob('candidates/*/ACCOUNT_TASK.json')):
            task = read_json(path)
            if callable(getattr(self.submission, '_task', None)):
                try:
                    canonical = self.submission._task(task['task_id'])
                except FileNotFoundError:
                    continue  # 异步准备尚未形成账户，不能虚构本账户的暴露目录。
                _require(canonical.get('task_id') == task['task_id'],
                         'BUSINESS_VALIDATION_CANONICAL_ACCOUNT_TASK_REQUIRED')
                task = canonical
            if task.get('job_path'):
                own.add(str(Path(task['job_path']).parent))
        return [item for item in exposures if str(Path(item.get('evidence_ref', {}).get(
            'receipt_path', '')).parent) not in own]

    def _actual_task(self, candidate, reference=None):
        path = self._candidate_path(candidate, 'ACCOUNT_TASK.json')
        original = reference or read_json(self._candidate_path(candidate, 'TASK.json'))
        if path.exists():
            task = read_json(path)
        elif callable(getattr(self.submission, '_task', None)):
            task = self.submission._task(original['task_id'])
        else:
            task = original
        _require(task.get('task_id') == original['task_id'] and task.get('job_path'),
                 'BUSINESS_VALIDATION_CANONICAL_ACCOUNT_TASK_REQUIRED')
        return task

    def _admission(self, protocol):
        material = self.research.confirmation_material()
        if not set(protocol['preview']['selected']) <= set(material.get('final_exploration_ready', [])):
            return None, ['FINAL_EXPLORATION_EVIDENCE_NOT_READY']
        if not callable(self.admit):
            return None, ['AUTHENTICATED_INDEPENDENT_DATA_NOT_AVAILABLE']
        context = deepcopy(protocol)
        admission_path = self.root / 'ADMISSION.json'
        if admission_path.exists():
            context['frozen_admission'] = read_json(admission_path)
        admission = self.admit(context)
        if not isinstance(admission, dict):
            reason = getattr(self.admit, 'last_waiting_reason', None)
            return None, ['AUTHENTICATED_INDEPENDENT_DATA_NOT_AVAILABLE'] + ([reason] if reason else [])
        reasons = []
        p = protocol['preview']
        projection = admission.get('snapshot_projection', {})
        if (projection.get('version') != 'UNIVERSE_FORWARD_SNAPSHOT_PROJECTION_V1'
                or projection.get('source_authentication') != 'CANONICAL_SNAPSHOT_STORE'
                or not projection.get('snapshot_refs')
                or projection.get('projection_hash') != stable_hash({key: value for key, value in projection.items()
                if key != 'projection_hash'}) or projection.get('profile') != p['profile']
                or projection.get('ready_for_registered_data_preparation') is not True):
            reasons.append('QUALIFIED_POST_FREEZE_FULL_UNIVERSE_SNAPSHOTS_REQUIRED')
        dates = projection.get('complete_account_dates', [])
        if (not isinstance(dates, list) or dates != sorted(set(dates))
                or projection.get('account_sessions') != len(dates)):
            reasons.append('INDEPENDENT_ACCOUNT_CALENDAR_INVALID')
        if len(dates) < p['minimum_account_sessions']:
            reasons.append('INDEPENDENT_ACCOUNT_SESSIONS_INSUFFICIENT')
        if projection.get('warmup_sessions', 0) < p['required_warmup_sessions']:
            reasons.append('INDEPENDENT_WARMUP_INSUFFICIENT')
        if dates and _day(dates[0]) < p['not_before']:
            reasons.append('INDEPENDENT_DATA_BEFORE_NOT_BEFORE')
        if any(datetime.fromisoformat(ref['received_at']) <= datetime.fromisoformat(protocol['frozen_at'])
               for ref in projection.get('snapshot_refs', [])):
            reasons.append('INDEPENDENT_CAPTURE_BEFORE_PROTOCOL_FREEZE')
        for key in ('source_authenticated', 'prior_access_review_passed', 'post_freeze_unseen'):
            if admission.get(key) is not True:
                reasons.append(key.upper() + '_REQUIRED')
        if not _hash(admission.get('prior_access_review_identity')):
            reasons.append('PRIOR_ACCESS_REVIEW_IDENTITY_REQUIRED')
        metadata = admission.get('metadata')
        if not isinstance(metadata, dict):
            reasons.append('REGISTERED_INDEPENDENT_METADATA_REQUIRED')
        else:
            audit = audit_data_qualification([metadata], self._foreign_exposures(self.exposures()))
            if audit['datasets'][0]['historical_independence'] == 'EXPOSED':
                reasons.append('INDEPENDENT_DATA_ALREADY_EXPOSED')
            if audit['datasets'][0]['metadata_account_ready'] is not True:
                reasons.append('INDEPENDENT_DATA_METADATA_INCOMPLETE')
            if sorted(metadata.get('symbols', [])) != projection.get('window', {}).get('symbols'):
                reasons.append('INDEPENDENT_FULL_REGISTERED_SYMBOLS_CONFLICT')
        fields = admission.get('request_fields', {})
        allowed = {'dataset_id', 'feature_start', 'account_start', 'account_end',
                   'execution_profile', 'observation_plan', 'universe_id'}
        if not isinstance(fields, dict) or set(fields) != allowed:
            reasons.append('REGISTERED_INDEPENDENT_REQUEST_FIELDS_REQUIRED')
        elif (fields['dataset_id'] != (metadata or {}).get('dataset_id')
                or fields['universe_id'] != p['contract']['scope']['universe_id']):
            reasons.append('REGISTERED_INDEPENDENT_REQUEST_IDENTITY_CONFLICT')
        else:
            window = projection.get('window', {})
            if any(_day(fields[key]) != _day(window.get(key)) for key in
                   ('feature_start', 'account_start', 'account_end')):
                reasons.append('REGISTERED_INDEPENDENT_WINDOW_CONFLICT')
            route = p['contract']['scope']['data_routes']['CONFIRMATION']
            if route is None:
                reasons.append('INDEPENDENT_DATA_ROUTE_NOT_FROZEN')
            elif (not route['start'] <= _day(fields['feature_start'])
                    or _day(fields['account_end']) > route['end']):
                reasons.append('INDEPENDENT_DATA_OUTSIDE_FROZEN_ROUTE')
            elif 'dataset_id' in route:
                if (fields['dataset_id'], (metadata or {}).get('content_hash')) != (
                        route['dataset_id'], route['dataset_hash']):
                    reasons.append('INDEPENDENT_DATASET_IDENTITY_CONFLICT')
            elif any(admission.get(key) != route[key] for key in
                     ('route_id', 'producer_identity', 'source_ids', 'universe_hash', 'quality_policy')):
                reasons.append('INDEPENDENT_TRUSTED_ROUTE_IDENTITY_CONFLICT')
        if 'frozen_admission' in context and admission != context['frozen_admission']:
            reasons.append('FROZEN_INDEPENDENT_ADMISSION_CHANGED')
        return admission, reasons

    @staticmethod
    def verify_report_repair_refs(task, outcome, *, root, manifest_ref, minimum_sessions):
        """修复报告须先证明专门批准、原失败及单独计费的完成链。"""
        from .report_repair_protocol_v1 import ReportRepairV1
        context=ReportRepairV1(root,manifest_ref).validate(for_dispatch=False)
        record=context.verify_receipt()
        _require(record.get('outcome')==outcome and context.job_path==Path(task['job_path']).absolute(),
                 'BUSINESS_VALIDATION_REPORT_REPAIR_OUTCOME_CONFLICT')
        return BusinessValidationProtocolV1.verify_report_refs(task,outcome,
            minimum_sessions=minimum_sessions,_report_repair=context)

    @staticmethod
    def verify_report_refs(task, outcome, *, minimum_sessions, _report_repair=None):
        """公共核账通过之外，再核对返回的真实报告原件及实际账户日数。"""
        _require(outcome.get('status') == 'ACCOUNT_VERIFIED'
            and outcome.get('verification', {}).get('advance_allowed') is True
            and outcome.get('task_id') == task['task_id']
            and outcome.get('strategy_qualified') is not True,
            'BUSINESS_VALIDATION_PUBLIC_VERIFICATION_REQUIRED')
        path = Path(task['job_path']).absolute()
        report_parent=path.parent
        if _report_repair is not None:
            from .report_repair_protocol_v1 import ReportRepairV1
            _require(type(_report_repair) is ReportRepairV1 and _report_repair.job_path==path,
                     'BUSINESS_VALIDATION_REPORT_REPAIR_CONTEXT_REQUIRED')
            _require(_report_repair.verify_receipt().get('outcome')==outcome,
                     'BUSINESS_VALIDATION_REPORT_REPAIR_RECEIPT_REQUIRED')
            report_parent=_report_repair.output_root
        _require(path.resolve() == path and path.is_file()
            and _file_hash(path) == task['job_sha256'],
            'BUSINESS_VALIDATION_FROZEN_JOB_CHANGED')
        _require(outcome['verification'].get('job_sha256') == task['job_sha256'],
                 'BUSINESS_VALIDATION_VERIFICATION_JOB_CONFLICT')
        _require(read_json(path.parent / 'VERIFICATION.json') == outcome['verification'],
                 'BUSINESS_VALIDATION_VERIFICATION_ORIGINAL_CONFLICT')
        reports = outcome.get('reports')
        _require(isinstance(reports, dict) and len(reports) == 2,
                 'BUSINESS_VALIDATION_DUAL_REPORT_REFERENCES_REQUIRED')
        job = read_json(path)
        _require(set(reports) == set(task.get('plan_ids', {})) == set(job.get('plans', {}))
            and task['plan_ids'] == {name: item['plan_id'] for name, item in job['plans'].items()}
            and job.get('input_identity') == task['input_identity'],
            'BUSINESS_VALIDATION_FROZEN_PLAN_REPORT_CONFLICT')
        costs, normalized = set(), {}
        for name, reference in reports.items():
            cost = 'STRESS' if name.endswith('STRESS') else 'BASE' if name.endswith('BASE') else None
            _require(cost is not None and cost not in costs and isinstance(reference, dict),
                     'BUSINESS_VALIDATION_DUAL_COST_REPORTS_REQUIRED')
            costs.add(cost)
            _require(job.get('items', {}).get(name, {}).get('backend_options', {}).get('costs') == cost,
                     'BUSINESS_VALIDATION_FROZEN_COST_CONFLICT')
            report_path = Path(reference['research_report']).absolute()
            _require(report_path.resolve() == report_path and report_path.parent == report_parent
                and report_path.name == name + '_RESEARCH_REPORT.json',
                     'BUSINESS_VALIDATION_REPORT_PATH_CONFLICT')
            raw = report_path.read_bytes()
            _require(hashlib.sha256(raw).hexdigest() == reference['research_report_sha256'],
                     'BUSINESS_VALIDATION_REPORT_ORIGINAL_CHANGED')
            report = read_json(report_path)
            _require(report.get('report_identity') == stable_hash({key: value for key, value in report.items()
                if key != 'report_identity'}) and report.get('input_identity') == task['input_identity'],
                'BUSINESS_VALIDATION_REPORT_IDENTITY_CONFLICT')
            final_path = Path(reference.get('final_report', '')).absolute()
            _require(final_path.resolve() == final_path and final_path.parent == report_parent
                and final_path.name == name + '_REPORT.json'
                and _file_hash(final_path) == reference.get('final_report_sha256'),
                'BUSINESS_VALIDATION_FINAL_REPORT_ORIGINAL_CHANGED')
            final = read_json(final_path)
            source = final.get('artifacts', {}).get('source_result', {})
            result_path = path.parent / (name + '_RESULT.json')
            _require(Path(source.get('path', '')).absolute() == result_path
                and _file_hash(result_path) == source.get('sha256')
                and read_json(path.parent / (name + '_SETTLEMENT.json')).get('result_sha256') == source['sha256']
                and final.get('research', {}).get('source_result_sha256') == source['sha256']
                and final['research'].get('account_and_signal') == report,
                'BUSINESS_VALIDATION_CANONICAL_RESULT_OR_REPORT_BINDING_CONFLICT')
            benchmark = final.get('benchmark', {})
            _require(benchmark.get('version') == 'UNIVERSE_PRICE_REFERENCE_V1'
                and benchmark.get('input_identity') == task['input_identity']
                and benchmark.get('status') == 'AVAILABLE_NONINVESTABLE'
                and benchmark.get('investable') is False and benchmark.get('formal_qualification') is False
                and isinstance(benchmark.get('metrics'), dict) and benchmark.get('limitations')
                and benchmark.get('cash') == {'net_return': 0., 'initial_cash': 50000.},
                'BUSINESS_VALIDATION_SUPPORTED_PRICE_REFERENCE_DISCLOSURE_REQUIRED')
            account = report.get('account', {})
            _require(type(account.get('sessions')) is int
                and account['sessions'] >= minimum_sessions
                and account.get('initial_cash') == 50000,
                'BUSINESS_VALIDATION_ACTUAL_ACCOUNT_SESSIONS_OR_CASH_INVALID')
            normalized[cost] = {'report_identity': report['report_identity'],
                'sessions': account['sessions'], 'report_ref': deepcopy(reference)}
        _require(costs == {'BASE', 'STRESS'} and normalized['BASE']['sessions'] == normalized['STRESS']['sessions'],
                 'BUSINESS_VALIDATION_COST_ACCOUNT_SESSIONS_CONFLICT')
        return normalized

    def _validate_result(self, task, outcome, protocol):
        return self.verify_report_refs(task, outcome,
            minimum_sessions=protocol['preview']['minimum_account_sessions'])

    def readiness(self):
        protocol = self.load()
        _, reasons = self._admission(protocol)
        if self.submission is None or not callable(getattr(self.submission, 'advance', None)):
            reasons.append('PUBLIC_BOUNDED_SUBMISSION_NOT_AVAILABLE')
        if not callable(self.bind_stage):
            reasons.append('TRUSTED_CONFIRMATION_STAGE_BINDING_NOT_AVAILABLE')
        return {'version': VERSION, 'protocol_identity': protocol['protocol_identity'],
            'status': 'WAITING_DATA' if reasons else 'READY', 'reason_codes': reasons,
            'exploration_may_continue': True, 'formal_qualification': 'SEPARATE_NOT_GRANTED',
            'strategy_qualified': False, 'confirmation_results_visible_to_design': False}

    def design_context(self):
        """设计端只知道队列在等待/运行；独立资料、结果和诊断不返回。"""
        state = self.status()
        return {key: state[key] for key in ('version', 'status', 'exploration_may_continue',
                                            'confirmation_results_visible_to_design')}

    def status(self):
        protocol = self.load()
        states = []
        business = self._business_results(protocol)
        for candidate_id in protocol['preview']['selected']:
            result = self._candidate_path(candidate_id, 'RESULT.json')
            task = self._candidate_path(candidate_id, 'TASK.json')
            states.append('COMPLETE' if result.exists() else 'IN_PROGRESS' if task.exists() else 'WAITING')
        goal = (protocol['preview']['profile'] == 'REAL_OBSERVED'
            and any(row['business_criteria_met'] for row in business.values()))
        state = ('BUSINESS_GOAL_MET' if goal else 'BUSINESS_EVIDENCE_RECORDED'
                 if all(item == 'COMPLETE' for item in states) else 'WAITING_OR_IN_PROGRESS')
        return {'version': VERSION, 'protocol_identity': protocol['protocol_identity'], 'status': state,
            'selected_count': len(states), 'completed_count': states.count('COMPLETE'),
            'exploration_may_continue': True, 'confirmation_results_visible_to_design': False,
            'strategy_qualified': False, 'business_goal_met': goal}

    def _business_results(self, protocol):
        material = self.research.confirmation_material()
        results = {}
        for candidate in protocol['preview']['selected']:
            result_path = self._candidate_path(candidate, 'RESULT.json')
            if not result_path.exists():
                continue
            result = read_json(result_path)
            task = self._actual_task(candidate)
            _require(result.get('protocol_identity') == protocol['protocol_identity']
                and result.get('candidate_id') == candidate and result.get('task_id') == task['task_id']
                and result.get('admission_identity') == stable_hash(read_json(self.root / 'ADMISSION.json')),
                'BUSINESS_VALIDATION_RESULT_BINDING_CONFLICT')
            refs = self._validate_result(task, result['outcome'], protocol)
            _require(refs == result.get('verified_report_refs'), 'BUSINESS_VALIDATION_REPORT_REFS_CHANGED')
            reports = {cost: read_json(item['report_ref']['research_report']) for cost, item in refs.items()}
            assessment = evaluate_business_reports(protocol['preview']['contract'], reports,
                minimum_sessions=protocol['preview']['minimum_account_sessions'])
            final_ready = candidate in material.get('final_exploration_ready', [])
            if not final_ready:
                assessment['reason_codes'].append('FINAL_EXPLORATION_EVIDENCE_NOT_READY')
                assessment['business_criteria_met'] = False
            admission = read_json(self.root / 'ADMISSION.json')
            dates = admission.get('snapshot_projection', {}).get('complete_account_dates')
            if any(report['account'].get('business_diagnostics', {}).get('account_dates') != dates
                   for report in reports.values()):
                assessment['reason_codes'].append('INDEPENDENT_ACTUAL_ACCOUNT_CALENDAR_CONFLICT')
                assessment['business_criteria_met'] = False
            results[candidate] = assessment
        return results

    def advance(self):
        """一次只准备一个公共阶段或调用一次公共 advance；恢复沿用原任务。"""
        with ObjectiveMutationLock.for_resource(self.protocol_path):
            protocol = self.load()
            p = protocol['preview']
            admission, reasons = self._admission(protocol)
            if reasons:
                return {'status': 'WAITING_DATA', 'reason_codes': reasons,
                    'exploration_may_continue': True, 'strategy_qualified': False}
            _require(self.submission is not None and callable(getattr(self.submission, 'advance', None))
                and callable(self.bind_stage), 'BUSINESS_VALIDATION_TRUSTED_PUBLIC_EXECUTION_REQUIRED')
            admission_path = self.root / 'ADMISSION.json'
            immutable(admission_path, admission)
            for candidate_id in p['selected']:
                result_path = self._candidate_path(candidate_id, 'RESULT.json')
                if result_path.exists():
                    continue
                blocked = self._candidate_path(candidate_id, 'BLOCKED.json')
                if blocked.exists():
                    return {'status': 'RECONCILIATION_REQUIRED', 'candidate_id': candidate_id,
                        'reason_codes': ['ORIGINAL_INDEPENDENT_TASK_BLOCKED_NO_RETEST'],
                        'strategy_qualified': False}
                request_path = self._candidate_path(candidate_id, 'REQUEST.json')
                preview_path = self._candidate_path(candidate_id, 'PREVIEW.json')
                task_path = self._candidate_path(candidate_id, 'TASK.json')
                if not request_path.exists():
                    request = {**deepcopy(p['requests'][candidate_id]), **deepcopy(admission['request_fields']),
                        'version': 'FULL_UNIVERSE_SUBMISSION_V4', 'phase': 'CONFIRMATION',
                        'purpose': 'INDEPENDENT_BUSINESS_VALIDATION',
                        'strategy_id': 'BV_' + stable_hash([protocol['protocol_identity'], candidate_id])[:32]}
                    request.pop('research_binding_ref', None)
                    bound = self.bind_stage(request, phase='CONFIRMATION',
                        candidate_identity=p['rules'][candidate_id],
                        batch_id='BV_' + protocol['protocol_identity'][:32])
                    _require(isinstance(bound, dict) and all(bound.get(key) == value for key, value in request.items()
                        if key != 'authorization_ref') and _hash(bound.get('research_binding_ref', {}).get('binding_id')),
                        'BUSINESS_VALIDATION_STAGE_BINDING_SCOPE_CONFLICT')
                    immutable(request_path, bound)
                    return {'status': 'REQUEST_BOUND', 'candidate_id': candidate_id, 'strategy_qualified': False}
                request = read_json(request_path)
                if not preview_path.exists():
                    preview = self.submission.preview(request)
                    _require(preview.get('rule_identity') == p['rules'][candidate_id],
                             'BUSINESS_VALIDATION_PUBLIC_RULE_CHANGED')
                    immutable(preview_path, preview)
                    return {'status': 'PUBLIC_PREVIEWED', 'candidate_id': candidate_id, 'strategy_qualified': False}
                preview = read_json(preview_path)
                if not task_path.exists():
                    task = self.submission.freeze(request, preview['preview_identity'])
                    _require(_hash(task.get('task_id')), 'BUSINESS_VALIDATION_PUBLIC_TASK_ID_REQUIRED')
                    immutable(task_path, task)
                    return {'status': 'PUBLIC_TASK_FROZEN', 'candidate_id': candidate_id, 'strategy_qualified': False}
                task = read_json(task_path)
                outcome = self.submission.advance(task['task_id'])
                if outcome.get('status') == 'ACCOUNT_VERIFIED':
                    task = self._actual_task(candidate_id, task)
                    immutable(self._candidate_path(candidate_id, 'ACCOUNT_TASK.json'), task)
                    original_refs = self._validate_result(task, outcome, protocol)
                    immutable(result_path, {'task_id': task['task_id'], 'outcome': outcome,
                        'protocol_identity': protocol['protocol_identity'], 'candidate_id': candidate_id,
                        'admission_identity': stable_hash(admission),
                        'verified_report_refs': original_refs,
                        'confirmation_results_visible_to_design': False})
                elif outcome.get('status') in ('FAILED', 'EVIDENCE_BLOCKED', 'RECONCILIATION_REQUIRED', 'UNKNOWN'):
                    immutable(self._candidate_path(candidate_id, 'BLOCKED.json'),
                              {'task_id': task['task_id'], 'outcome': outcome})
                return {'status': 'PUBLIC_STAGE_ADVANCED', 'candidate_id': candidate_id,
                    'task_id': task['task_id'], 'public_stage_status': outcome.get('status'),
                    'dispatched_segments': outcome.get('dispatched_segments', 0),
                    'strategy_qualified': False, 'confirmation_results_visible_to_design': False}
            return self.status()

    def human_results(self):
        """仅用户报告路径使用；不得作为设计上下文或下一批反馈。"""
        protocol = self.load()
        reports = {}
        business = self._business_results(protocol)
        for candidate in protocol['preview']['selected']:
            path = self._candidate_path(candidate, 'RESULT.json')
            if path.exists():
                result = read_json(path)
                task = self._actual_task(candidate)
                self._validate_result(task, result['outcome'], protocol)
                reports[candidate] = {**result, 'business_validation': business[candidate]}
        return reports
