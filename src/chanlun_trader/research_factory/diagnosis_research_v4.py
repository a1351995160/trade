"""一份有限总授权下的持续全池研究；模型提案与独立确认隔离。"""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path

from .bounded_model_v1 import BoundedCodexInvokerV1
from .budget import BudgetExhaustedError
from .common import stable_hash
from .context import PerformanceBlindGuard
from .continuous_research_contract_v1 import validate_continuous_research_contract
from .exploration_governance import immutable, read_json
from .mutation_boundary import ObjectiveMutationLock
from .research_campaign_v1 import ResearchCampaignV1
from .research_rule_strategy_v4 import ResearchRuleStrategyV4, rule_capabilities

VERSION = 'DIAGNOSIS_RESEARCH_V4'


def implementation_identity():
    names = ('diagnosis_research_v4.py', 'continuous_submission_v1.py', 'campaign_scope_v1.py',
        'campaign_model_receipt_v1.py', 'research_campaign_v1.py', 'run_budget.py',
        'budget_gateway_model_v1.py', 'business_validation_protocol_v1.py',
        'final_exploration_queue_v1.py', 'research_dataset_projection_v1.py', 'continuous_storage_v1.py',
        'train_projection_admission_v1.py', 'continuous_universe_lifecycle_v1.py',
        'strategy_submission_v1.py', 'universe_compute_governance_v1.py', 'universe_scan_service_v1.py')
    return {name: _digest(Path(__file__).parent / name) for name in names}


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class DiagnosisResearchV4:
    def __init__(self, campaign, submission, invoker=None, confirmation=None):
        if not isinstance(campaign, ResearchCampaignV1):
            raise ValueError('CONTINUOUS_REGISTERED_CAMPAIGN_REQUIRED')
        self.campaign, self.submission = campaign, submission
        self.invoker = invoker if invoker is not None else BoundedCodexInvokerV1()
        self.confirmation = confirmation
        self.root = campaign.directory / 'diagnosis_v4'

    @classmethod
    def create(cls, campaign, submission, *, contract, template, model_limits,
               candidates_per_batch=1, invoker=None, confirmation=None, final_exploration_template=None,
               history_references=None, deployment_admission_refs=None, invoker_deployment_identity=None):
        service = cls(campaign, submission, invoker, confirmation)
        contract = validate_continuous_research_contract(contract)
        view = campaign.peek_status()
        policy = view['base_authorization'].get('scope_policy')
        if not policy or policy['summary']['contract'] != contract:
            raise PermissionError('CONTINUOUS_OWNER_APPROVED_CONTRACT_REQUIRED')
        from .strategy_submission_v1 import REQUEST_FIELDS
        fields = (REQUEST_FIELDS - {'symbols', 'rule', 'strategy_id'}) | {
            'version', 'universe_id', 'account_scope', 'execution_profile', 'observation_plan', 'phase'}
        if (not isinstance(template, dict) or set(template) != fields
                or template['version'] != 'FULL_UNIVERSE_SUBMISSION_V4'
                or template['phase'] != 'EXPLORATION' or template['purpose'] != 'EXPLORATORY'):
            raise ValueError('CONTINUOUS_TEMPLATE_FIELDS_OR_PHASE_INVALID')
        if (not isinstance(model_limits, dict) or set(model_limits) != {
                'timeout_seconds', 'max_tokens', 'max_cost_microunits', 'context_max_bytes'}
                or any(type(value) is not int or value < 1 for value in model_limits.values())):
            raise ValueError('CONTINUOUS_MODEL_LIMITS_REQUIRED')
        if (type(candidates_per_batch) is not int
                or not 1 <= candidates_per_batch <= view['authorization']['max_candidates_per_batch']):
            raise ValueError('CONTINUOUS_BATCH_SIZE_OUTSIDE_AUTHORITY')
        from .final_exploration_queue_v1 import validate_final_exploration_template
        final_template = validate_final_exploration_template(final_exploration_template, template, contract)
        history = service._history_references(history_references or [])
        refs = deepcopy(deployment_admission_refs or {})
        if refs and set(refs) != {'exploration_admission', 'train_projection_deployment'}:
            raise ValueError('CONTINUOUS_DEPLOYMENT_ADMISSION_REFS_INVALID')
        config = {'version': VERSION, 'contract': contract, 'template': deepcopy(template),
            'model_limits': deepcopy(model_limits), 'candidates_per_batch': candidates_per_batch,
            'final_exploration_template': final_template, 'history_references': history,
            'deployment_admission_refs': refs,
            'invoker_deployment_identity': deepcopy(invoker_deployment_identity or {}),
            'implementation_identity': implementation_identity(),
            'campaign_authorization_hash': stable_hash(view['base_authorization']),
            'selection_policy': 'ALL_INITIAL_SCREEN_PASSED_COMPLETE_FAMILY_FINAL_EVIDENCE_SEPARATE',
            'history_policy': 'DETERMINISTIC_ALL_FAILURE_COUNTS_RECENT_RECORDS_AND_COMPLETE_CHAIN_V1'}
        with ObjectiveMutationLock.for_resource(service.root / 'CONFIG.json'):
            immutable(service.root / 'CONFIG.json', config)
        return service

    def config(self):
        value = read_json(self.root / 'CONFIG.json')
        view = self.campaign.peek_status()
        if (value['version'] != VERSION
                or value['campaign_authorization_hash'] != stable_hash(view['base_authorization'])
                or value.get('implementation_identity') != implementation_identity()):
            raise PermissionError('CONTINUOUS_CONFIG_OR_AUTHORIZATION_CHANGED')
        validate_continuous_research_contract(value['contract'])
        self._history_references(value.get('history_references', []))
        return value

    @staticmethod
    def _history_references(references):
        """保留旧研究原始来源；无旧计量证明时不推算消费，也不补造模型记录。"""
        if not isinstance(references, list):
            raise ValueError('CONTINUOUS_HISTORY_REFERENCES_INVALID')
        for reference in references:
            if (not isinstance(reference, dict) or set(reference) != {'path', 'sha256', 'source', 'metering'}
                    or reference['source'] not in {'SESSION_AI_MANUAL', 'LEGACY_SYSTEM'}
                    or reference['metering'] not in {'REFERENCED_CANONICAL_LEDGER', 'UNKNOWN'}):
                raise ValueError('CONTINUOUS_HISTORY_REFERENCES_INVALID')
            path = Path(reference['path']).absolute()
            if path.resolve() != path or not path.is_file() or _digest(path) != reference['sha256']:
                raise PermissionError('CONTINUOUS_HISTORY_REFERENCE_CHANGED')
        return deepcopy(references)

    def _records(self):
        records = []
        view = self.campaign.peek_status()
        for path in sorted(self.root.glob('candidate_*/RECORD.json')):
            value = read_json(path)
            for name, expected in value['artifacts'].items():
                if Path(name).name != name or _digest(path.parent / name) != expected:
                    raise ValueError('CONTINUOUS_RESEARCH_RECORD_CHANGED')
            operation = view['operations'].get(value['candidate_id'] + '_CANDIDATE', {})
            # 原件已提交、结算尚未提交的恢复边界可被 advance 补齐；读取不能补写。
            if operation.get('status') in ('COMPLETED', 'FAILED') and operation.get('evidence_identity') != stable_hash(value):
                raise ValueError('CONTINUOUS_RECORD_BUDGET_CONFLICT')
            records.append(value)
        return records

    def _control(self):
        path = self.root / 'CONTROL.json'
        return read_json(path) if path.exists() else {'started': False}

    def start(self):
        with ObjectiveMutationLock.for_resource(self.root / 'CONFIG.json'):
            self.config()
            self.campaign._dispatchable(self.campaign.peek_status(), 'EXPLORATION')
            from .bounded_research_v1 import _put
            _put(self.root / 'CONTROL.json', {'started': True})
        return self.status()

    def pause(self, reason='OWNER_PAUSE'):
        self.campaign.pause(reason)
        return self.status()

    def resume(self, reason='OWNER_RESUME'):
        if self.campaign.peek_status().get('revoked'):
            raise PermissionError('CAMPAIGN_REVOKED')
        self.campaign.resume(reason)
        return self.status()

    def revoke(self, reason='OWNER_REVOKE'):
        self.campaign.revoke(reason)
        return self.status()

    def _final_exploration_feedback(self, records):
        """只投影已核验的探索原件；独立验证不参与设计反馈。"""
        from .final_exploration_queue_v1 import FinalExplorationQueueV1
        evidence = FinalExplorationQueueV1(self).evidence()
        sources = {row['candidate_id']: stable_hash(row) for row in records}
        recent, counts = [], Counter()
        for row in evidence:
            if sources.get(row['candidate_id']) != row['source_record_identity']:
                raise ValueError('CONTINUOUS_FINAL_FEEDBACK_SOURCE_RECORD_CONFLICT')
            ready = row['status'] == 'FINAL_EXPLORATION_READY'
            codes = ['FINAL_EXPLORATION_READY' if ready else 'FINAL_EXPLORATION_FAILED']
            if not ready:
                codes.extend('FINAL_EXPLORATION_' + key.upper() + '_NOT_MET'
                    for key, passed in sorted(row['screen']['final_checks'].items()) if passed is False)
            counts.update(codes)
            recent.append({key: row[key] for key in
                ('candidate_id', 'rule_identity', 'source_record_identity', 'screen_identity', 'status')}
                | {'evidence_identity': row['identity'], 'evidence_sha256': row['evidence_sha256'],
                   'feedback_codes': codes})
        return {'version': 'DETERMINISTIC_FINAL_EXPLORATION_FEEDBACK_V1',
            'total_evidence': len(evidence),
            'ready_count': sum(row['status'] == 'FINAL_EXPLORATION_READY' for row in evidence),
            'failed_count': sum(row['status'] == 'FINAL_EXPLORATION_FAILED' for row in evidence),
            'evidence_chain_identity': stable_hash([row['identity'] for row in evidence]),
            'feedback_counts': dict(sorted(counts.items())), 'recent_evidence': recent,
            'omitted_evidence_count': 0}

    def _context(self, config, records, index):
        scope = config['contract']['scope']
        caps = rule_capabilities()
        caps['indicators'] = [item for item in caps['indicators'] if item['id'] in scope['indicator_roles']]
        caps['capability'] = 'RESEARCH_RULE_STRATEGY_V4'
        counts = Counter(code for record in records for code in record['feedback']['codes'])
        recent = [{'candidate_id': row['candidate_id'], 'rule_identity': row['rule_identity'],
            'hypothesis': row['hypothesis'], 'change_reason': row['change_reason'],
            'feedback_codes': row['feedback']['codes']} for row in records]
        history = {'version': 'DETERMINISTIC_HISTORY_V1', 'total_attempts': len(records),
            'record_chain_identity': stable_hash([stable_hash(row) for row in records]),
            'all_rule_identities_hash': stable_hash([row['rule_identity'] for row in records]),
            'feedback_counts': dict(sorted(counts.items())), 'recent_records': recent, 'omitted_record_count': 0}
        history['legacy_references_identity'] = stable_hash(config.get('history_references', []))
        history['legacy_references_count'] = len(config.get('history_references', []))
        history['legacy_metering_policy'] = 'ORIGINAL_REFERENCES_RETAINED_NO_REBILLING_UNKNOWN_NOT_INFERRED'
        final = history['final_exploration'] = self._final_exploration_feedback(records)
        constraints = {key: deepcopy(config['template'][key]) for key in
            ('universe_id', 'dataset_id', 'feature_start', 'account_start', 'account_end',
             'initial_cash', 'max_positions', 'max_symbol_exposure_bps', 'costs', 'purpose')}
        context = {'capabilities': caps, 'constraints': constraints, 'history': history,
            'indicator_roles': scope['indicator_roles'],
            'allocated_mechanisms': scope['mechanism_combinations'][(index - 1) % len(scope['mechanism_combinations'])],
            'coverage_policy': 'ROTATING_FROZEN_MECHANISM_ALLOCATION_NOT_PARAMETER_EXHAUSTION',
            'evidence_boundary': 'EXPLORATION_QUALITATIVE_ONLY_NO_CONFIRMATION_RESULTS_OR_DATA'}
        maximum = config['model_limits']['context_max_bytes']
        while len(json.dumps(context, ensure_ascii=False, sort_keys=True).encode('utf-8')) > maximum and history['recent_records']:
            history['recent_records'].pop(0)
            history['omitted_record_count'] = len(records) - len(history['recent_records'])
        while len(json.dumps(context, ensure_ascii=False, sort_keys=True).encode('utf-8')) > maximum and final['recent_evidence']:
            final['recent_evidence'].pop(0)
            final['omitted_evidence_count'] = final['total_evidence'] - len(final['recent_evidence'])
        if len(json.dumps(context, ensure_ascii=False, sort_keys=True).encode('utf-8')) > maximum:
            raise BudgetExhaustedError('CONTINUOUS_MODEL_CONTEXT_CAPACITY_INSUFFICIENT')
        history['omitted_record_count'] = len(records) - len(history['recent_records'])
        PerformanceBlindGuard.assert_blind(context)
        return context

    def _model_guarantee(self, limits):
        enforce = getattr(self.invoker, 'enforce_budget_limits', None)
        if not callable(enforce):
            raise PermissionError('HARD_BUDGET_UNSUPPORTED')
        value = enforce(max_tokens=limits['max_tokens'], max_cost_microunits=limits['max_cost_microunits'])
        if (not isinstance(value, dict) or value.get('enforced') is not True
                or value.get('max_tokens') != limits['max_tokens']
                or value.get('max_cost_microunits') != limits['max_cost_microunits']
                or not value.get('evidence_identity')):
            raise PermissionError('HARD_BUDGET_UNSUPPORTED')
        return value

    def _model(self, directory, candidate, batch, context, limits):
        operation_id = candidate + '_MODEL'
        bounds = {'model_calls': 1, 'model_tokens': limits['max_tokens'],
            'model_cost_microunits': limits['max_cost_microunits'], 'wall_seconds': limits['timeout_seconds']}
        operation = self.campaign.peek_status()['operations'].get(operation_id)
        if operation is None:
            guarantee = self._model_guarantee(limits)
            immutable(directory / 'MODEL_GUARANTEE.json', guarantee)
            operation = self.campaign.reserve_operation(operation_id=operation_id, batch_id=batch,
                stage='EXPLORATION', kind='MODEL', subject_identity=stable_hash({'context': context, 'candidate': candidate}),
                upper_bounds=bounds, cost_bound_evidence=guarantee['evidence_identity'])
        elif operation['subject_identity'] != stable_hash({'context': context, 'candidate': candidate}):
            raise PermissionError('CONTINUOUS_MODEL_CONTEXT_CHANGED')
        folder = directory / 'model'
        if operation['status'] in ('RUNNING', 'UNKNOWN', 'COMPLETED', 'FAILED'):
            if not callable(getattr(self.invoker, 'can_recover', None)) or not self.invoker.can_recover(folder, stable_hash(context)):
                if operation['status'] in ('RUNNING', 'UNKNOWN'):
                    self.campaign.mark_unknown(operation_id, 'MODEL_RESULT_UNKNOWN_NO_SECOND_PAID_CALL')
                return {'status': 'WAITING_MODEL_RECONCILIATION', 'dispatched_segments': 0}
        else:
            self.campaign.start_operation(operation_id)
        proposal, error = None, None
        try:
            proposal = self.invoker.invoke(context, staging_dir=folder, timeout_seconds=limits['timeout_seconds'])
        except Exception as exc:
            error = type(exc).__name__
        receipt_path = folder / 'INVOCATION.json'
        if not receipt_path.exists():
            self.campaign.mark_unknown(operation_id, 'MODEL_RECEIPT_UNKNOWN_NO_RETRY')
            return {'status': 'WAITING_MODEL_RECONCILIATION', 'dispatched_segments': 0}
        receipt = read_json(receipt_path)
        usage = receipt.get('usage', {})
        if (receipt.get('context_hash') != stable_hash(context)
                or set(usage) != {'input_tokens', 'output_tokens', 'total_tokens', 'cost_microunits', 'model_calls'}
                or any(type(amount) is not int or amount < 0 for amount in usage.values())
                or usage['total_tokens'] != usage['input_tokens'] + usage['output_tokens']):
            self.campaign.mark_unknown(operation_id, 'MODEL_USAGE_RECEIPT_INVALID')
            return {'status': 'WAITING_MODEL_RECONCILIATION', 'dispatched_segments': 0}
        actual = {'model_calls': max(1, usage['model_calls']), 'model_tokens': usage['total_tokens'],
                  'model_cost_microunits': usage['cost_microunits'], 'wall_seconds': limits['timeout_seconds']}
        if any(actual[key] > bounds[key] for key in actual):
            self.campaign.settle_model_overrun(operation_id, receipt_path=receipt_path)
            return {'status': 'RECONCILIATION_REQUIRED', 'dispatched_segments': 0}
        self.campaign.settle_operation(operation_id, actual=actual, outcome='FAILED' if error else 'COMPLETED',
            evidence_identity=stable_hash(receipt))
        if error:
            return self._record(directory, candidate, batch, status='REJECTED', feedback=['MODEL_OUTPUT_INVALID'])
        if receipt.get('response_hash') != stable_hash(proposal):
            raise ValueError('CONTINUOUS_MODEL_RESPONSE_IDENTITY_CONFLICT')
        immutable(directory / 'PROPOSAL.json', proposal)
        return {'status': 'PROPOSED', 'dispatched_segments': 0, 'model_calls': 1}

    def _record(self, directory, candidate, batch, *, status, feedback, rule_identity=None, screen=None):
        path = directory / 'RECORD.json'
        if path.exists():
            record = read_json(path)
        else:
            proposal = read_json(directory / 'PROPOSAL.json') if (directory / 'PROPOSAL.json').exists() else {}
            context = read_json(directory / 'CONTEXT.json')
            recent = context['history']['recent_records']
            record = {'version': VERSION, 'candidate_id': candidate, 'batch_id': batch, 'status': status,
                'rule_identity': rule_identity, 'parent_rule_identity': recent[-1]['rule_identity'] if recent else None,
                'hypothesis': proposal.get('hypothesis'), 'change_reason': proposal.get('change_reason'),
                'mechanisms': context['allocated_mechanisms'], 'feedback': {'codes': feedback}, 'screen': screen,
                'strategy_qualified': False, 'artifacts': {p.name: _digest(p) for p in directory.glob('*.json')
                    if p.name != 'RECORD.json'}, 'interpretation': '探索事实不是普遍有效性或独立验证结论。'}
            immutable(path, record)
        operation_id = candidate + '_CANDIDATE'
        operation = self.campaign.peek_status()['operations'][operation_id]
        if operation['status'] not in ('COMPLETED', 'FAILED'):
            self.campaign.settle_operation(operation_id, actual={'candidate_attempts': 1, 'wall_seconds': 1},
                outcome='COMPLETED' if record['status'] == 'SCREENED' else 'FAILED', evidence_identity=stable_hash(record))
        size = self.config()['candidates_per_batch']
        completed = [row for row in self._records() if row['batch_id'] == batch]
        if len(completed) >= size:
            with self.campaign._lock():
                self.campaign._budget().complete_batch(batch)
        return {'status': record['status'], 'candidate_id': candidate, 'dispatched_segments': 0}

    def _screen(self, directory, task, result, config):
        if result.get('status') != 'ACCOUNT_VERIFIED' or result['verification'].get('advance_allowed') is not True:
            raise ValueError('CONTINUOUS_PUBLIC_EVIDENCE_NOT_VERIFIED')
        from .business_validation_protocol_v1 import BusinessValidationProtocolV1
        BusinessValidationProtocolV1.verify_report_refs(task, result, minimum_sessions=1)
        job = read_json(task['job_path'])
        root = Path(task['job_path']).parent
        reports, full_reports = {}, {}
        for name in sorted(job['plans']):
            report = read_json(root / (name + '_RESEARCH_REPORT.json'))
            if (report.get('report_identity') != stable_hash({key: value for key, value in report.items() if key != 'report_identity'})
                    or report['input_identity'] != task['input_identity']):
                raise ValueError('CONTINUOUS_PUBLIC_REPORT_CHANGED')
            cost = job['items'][name]['backend_options']['costs']
            reports[cost] = report['account']
            full_reports[cost] = report
        if set(reports) != {'BASE', 'STRESS'}:
            raise ValueError('CONTINUOUS_DUAL_COST_ACCOUNTS_REQUIRED')
        base, stress = reports['BASE'], reports['STRESS']
        if base['initial_cash'] != 50000 or stress['initial_cash'] != 50000 or base['sessions'] != stress['sessions']:
            raise ValueError('CONTINUOUS_ACCOUNT_SCOPE_CONFLICT')
        profits = [episode['net_profit'] for episode in base['episodes'] if episode['status'] == 'CLOSED']
        positive, negative = sum(max(0, value) for value in profits), -sum(min(0, value) for value in profits)
        pf = positive / negative if negative else None
        annual = (base['final_equity'] / base['initial_cash']) ** (252 / base['sessions']) - 1
        metrics = {'base_annualized_net_return_minimum': annual,
            'base_maximum_drawdown_maximum': base['max_drawdown'],
            'base_complete_round_trip_net_win_rate_minimum': base['closed_episode_win_rate'],
            'base_complete_round_trip_net_profit_factor_minimum': pf,
            'base_complete_round_trips_minimum': base['closed_episodes'],
            'stress_total_net_return_strictly_greater_than': stress['net_return']}
        def meets(key, bound):
            value = metrics[key]
            if key == 'base_complete_round_trip_net_profit_factor_minimum' and negative == 0:
                return positive > 0
            return (value is not None and (value <= bound if key.endswith('_maximum')
                else value > bound if key.endswith('_greater_than') else value >= bound))
        policy = config['contract']['exploration_policy']
        checks = {'sessions': base['sessions'] >= policy['minimum_account_sessions'],
            'closed_round_trips': base['closed_episodes'] >= policy['minimum_complete_round_trips']}
        checks.update({key: meets(key, bound) for key, bound in policy['thresholds'].items()})
        final = config['contract']['final_criteria']
        final_checks = {key: meets(key, bound) for key, bound in final['thresholds_each_stage'].items()}
        final_checks['sessions'] = base['sessions'] >= final['exploration_minimum_actual_sessions']
        from .business_validation_protocol_v1 import evaluate_business_reports
        business = evaluate_business_reports(config['contract'], full_reports,
            minimum_sessions=final['exploration_minimum_actual_sessions'])
        final_checks['reporting_complete'] = business['reporting_complete']
        return {'passed': all(checks.values()), 'checks': checks, 'metrics': metrics,
                'final_exploration_ready': all(final_checks.values()), 'final_checks': final_checks,
                'source_report_identities': {name: _digest(root / (name + '_RESEARCH_REPORT.json')) for name in job['plans']}}

    def _attempt(self, index, config, records):
        candidate = 'CANDIDATE_' + str(index).zfill(4)
        batch = 'BATCH_' + str((index - 1) // config['candidates_per_batch'] + 1).zfill(4)
        directory = self.root / ('candidate_' + str(index).zfill(4))
        if not (directory / 'PROPOSAL.json').exists() and candidate + '_MODEL' not in self.campaign.peek_status()['operations']:
            self._proposal_preflight(config)
        operation = self.campaign.reserve_operation(operation_id=candidate + '_CANDIDATE', batch_id=batch,
            stage='EXPLORATION', kind='CANDIDATE', subject_identity=stable_hash({'config': config, 'candidate': candidate}),
            upper_bounds={'candidate_attempts': 1, 'wall_seconds': 1}, hypothesis_identity=candidate)
        if operation['status'] == 'RESERVED':
            self.campaign.start_operation(candidate + '_CANDIDATE')
        if (directory / 'RECORD.json').exists():
            return self._record(directory, candidate, batch, status='RECOVERED', feedback=[])
        immutable(directory / 'INTENT.json', {'candidate_id': candidate, 'batch_id': batch,
            'config_identity': stable_hash(config), 'index': index})
        context_path = directory / 'CONTEXT.json'
        if not context_path.exists():
            immutable(context_path, self._context(config, records, index))
        context = read_json(context_path)
        if not (directory / 'PROPOSAL.json').exists():
            return self._model(directory, candidate, batch, context, config['model_limits'])
        proposal = read_json(directory / 'PROPOSAL.json')
        try:
            strategy = ResearchRuleStrategyV4(proposal, strategy_id=candidate)
            from .continuous_submission_v1 import _check_rule_scope
            _check_rule_scope(proposal, config['contract']['scope'])
        except (ValueError, TypeError, KeyError, PermissionError):
            return self._record(directory, candidate, batch, status='REJECTED', feedback=['INVALID_PUBLIC_RULE_OR_SCOPE'])
        try:
            if any(row['rule_identity'] == strategy.rule_identity for row in records):
                return self._record(directory, candidate, batch, status='REJECTED',
                    feedback=['DUPLICATE_RULE'], rule_identity=strategy.rule_identity)
            request_path = directory / 'REQUEST.json'
            if not request_path.exists():
                request = {**config['template'], 'rule': proposal, 'strategy_id': candidate}
                request = self.submission.bind_research_request(request, phase='EXPLORATION',
                    candidate_identity=strategy.rule_identity, batch_id=batch)
                immutable(request_path, request)
            request = read_json(request_path)
            preview = self.submission.preview(request)
        except (ValueError, KeyError, PermissionError) as exc:
            # 资料、部署与权限问题保留本候选；不能吞成策略失败再付费生成下一份。
            return {'status': 'WAITING_PUBLIC_DEPENDENCY', 'waiting_reason': str(exc),
                    'candidate_id': candidate, 'dispatched_segments': 0}
        immutable(directory / 'PREVIEW.json', preview)
        task_path = directory / 'TASK.json'
        if not task_path.exists():
            task = self.submission.freeze(request, preview['preview_identity'])
            immutable(task_path, {'task_id': task['task_id'], 'preview_identity': preview['preview_identity']})
            return {'status': 'PREPARING', 'candidate_id': candidate, 'dispatched_segments': 0}
        task_ref = read_json(task_path)
        result = self.submission.advance(task_ref['task_id'])
        if result['status'] in {'EVIDENCE_BLOCKED', 'RECONCILIATION_REQUIRED'} or result['status'].endswith('FAILED'):
            return {'status': 'RECONCILIATION_REQUIRED', 'candidate_id': candidate, 'dispatched_segments': result.get('dispatched_segments', 0)}
        if result['status'] != 'ACCOUNT_VERIFIED':
            return result
        immutable(directory / 'PUBLIC_RESULT.json', {**result, 'dispatched_segments': 0})
        if result.get('dispatched_segments'):
            return {'status': 'REPORT_COMPLETED', 'candidate_id': candidate, 'dispatched_segments': 1}
        task = self.submission._task(task_ref['task_id'])
        screen = self._screen(directory, task, result, config)
        immutable(directory / 'SCREEN.json', screen)
        feedback = [key.upper() + '_FAILED' for key, passed in screen['checks'].items() if not passed]
        return self._record(directory, candidate, batch, status='SCREENED',
            feedback=feedback or ['INITIAL_SCREEN_PASSED_NOT_QUALIFIED'], rule_identity=strategy.rule_identity,
            screen={'passed': screen['passed'], 'identity': stable_hash(screen),
                    'final_exploration_ready': screen['final_exploration_ready']})

    def _proposal_preflight(self, config):
        """只查登记元信息与固定权限；缺资料时不得继续支付新模型调用。"""
        from .continuous_submission_v1 import _parent_authority, request_scope
        from .universe_data_provider_v1 import service_trusted_scope
        request = config['template']
        parent = _parent_authority(self.submission, request)
        trusted = service_trusted_scope(self.submission, request, parent)
        datasets = self.submission.provider.catalog(trusted_scope=trusted)['datasets']
        dataset = next((row for row in datasets if row['dataset_id'] == request['dataset_id']), None)
        if dataset is None:
            raise PermissionError('CONTINUOUS_DATASET_NOT_REGISTERED')
        frozen = request_scope(request, config['contract'], dataset)
        route = config['contract']['scope']['data_routes']['EXPLORATION']
        if (not route['start'] <= frozen['feature_start'] <= frozen['account_start'] <= frozen['account_end'] <= route['end']
                or ('dataset_id' in route and (frozen['dataset_id'], frozen['dataset_hash']) != (route['dataset_id'], route['dataset_hash']))):
            raise PermissionError('CONTINUOUS_EXPLORATION_DATA_CHANGED')
        if 'dataset_id' not in route:
            from .continuous_submission_v1 import _independent_binding
            _independent_binding(self.submission, request, config['contract'])
        self._model_guarantee(config['model_limits'])

    def _recover_model(self, config, view):
        """停派后仍可获取原调用回执；绝不创建新调用或扩大原预留。"""
        for operation in view['operations'].values():
            if operation['kind'] != 'MODEL' or operation['status'] not in ('RUNNING', 'UNKNOWN', 'COMPLETED', 'FAILED'):
                continue
            if operation.get('resource_overrun'):
                return {'status': 'RECONCILIATION_REQUIRED', 'dispatched_segments': 0}
            candidate = operation['operation_id'].removesuffix('_MODEL')
            directory = self.root / candidate.lower()
            if (directory / 'PROPOSAL.json').exists() or (directory / 'RECORD.json').exists():
                continue
            intent = read_json(directory / 'INTENT.json')
            return self._model(directory, candidate, intent['batch_id'], read_json(directory / 'CONTEXT.json'), config['model_limits'])
        return None

    def _final_exploration(self):
        from .final_exploration_queue_v1 import FinalExplorationQueueV1
        return FinalExplorationQueueV1(self)

    def _reconcile_public(self, view):
        if not any(op['kind'] in {'ACCOUNT', 'DATA', 'VERIFY'} and op['status'] in {'RUNNING', 'UNKNOWN'}
                   for op in view['operations'].values()):
            return None
        from .continuous_submission_v1 import reconcile_submission
        for path in sorted(self.root.glob('**/TASK.json')):
            reference = read_json(path)
            if not reference.get('task_id'):
                continue
            result = reconcile_submission(self.submission, reference['task_id'])
            if result['status'] != 'NO_PUBLIC_RECONCILIATION_PENDING':
                return result
        return None

    def advance(self):
        with ObjectiveMutationLock.for_resource(self.root / 'CONFIG.json'):
            config = self.config()
            if not self._control()['started']:
                return self.status()
            if self.status()['goal_complete']:
                return self.status()
            view = self.campaign.peek_status()
            try:
                recovered = self._recover_model(config, view)
                if recovered is not None:
                    return {**self.status(), 'last_step': recovered, 'dispatched_segments': 0}
                if view.get('revoked') or view['paused'] or view['expired'] or any(
                        op['status'] == 'UNKNOWN' or op.get('active_segment') is not None
                        for op in view['operations'].values()):
                    reconciled = self._reconcile_public(view)
                    if reconciled is not None:
                        return {**self.status(), 'last_step': reconciled, 'dispatched_segments': 0}
                from .continuous_storage_v1 import storage_status
                storage = storage_status(self)
                if storage['status'] != 'READY':
                    return {**self.status(), 'status': 'WAITING_STORAGE', 'storage': storage, 'dispatched_segments': 0}
                records = self._records()
                self._prepare_confirmation(config, records)
                final_queue = self._final_exploration()
                queue_state = final_queue.status()
                final_turn_path = self.root / 'FINAL_TURN.json'
                final_turn = read_json(final_turn_path)['number'] if final_turn_path.exists() else 0
                if queue_state['status'] == 'READY':
                    from .bounded_research_v1 import _put
                    _put(final_turn_path, {'number': final_turn + 1})
                    remaining = view.get('stage_remaining', {}).get('EXPLORATION', view['remaining'])
                    if final_turn % 2 == 1 or remaining['candidate_attempts'] == 0:
                        self.campaign._dispatchable(view, 'EXPLORATION')
                        final_result = final_queue.advance()
                        if not final_result['status'].startswith('WAITING_'):
                            return {**self.status(), 'last_step': final_result,
                                    'dispatched_segments': final_result.get('dispatched_segments', 0)}
                # 确认就绪后每隔一次探索推进获得一次机会；等待不拖停探索。
                if self.confirmation is not None and self.confirmation.protocol_path.exists():
                    turn_path = self.root / 'TURN.json'
                    turn = read_json(turn_path)['number'] if turn_path.exists() else 0
                    from .bounded_research_v1 import _put
                    _put(turn_path, {'number': turn + 1})
                    remaining = view.get('stage_remaining', {}).get('EXPLORATION', view['remaining'])
                    exploration_capacity = (remaining.get('candidate_attempts', 0) > 0
                        or any(item['stage'] == 'EXPLORATION' and item['status'] not in ('COMPLETED', 'FAILED')
                               for item in view['operations'].values()))
                    if turn % 2 == 1 or not exploration_capacity:
                        self.campaign._dispatchable(view, 'CONFIRMATION')
                        result = self.confirmation.advance()
                        if result.get('status') not in {'WAITING', 'WAITING_DATA', 'WAITING_AUTHORIZATION'}:
                            return {**self.status(), 'last_step': result,
                                    'dispatched_segments': result.get('dispatched_segments', 0)}
                self.campaign._dispatchable(view, 'EXPLORATION')
                maximum = min(view['authorization']['resource_limits']['candidate_attempts'],
                    view['authorization']['max_batches'] * config['candidates_per_batch'])
                index = next((number for number in range(1, maximum + 1)
                    if not (self.root / ('candidate_' + str(number).zfill(4)) / 'RECORD.json').exists()
                    or view['operations'].get('CANDIDATE_' + str(number).zfill(4) + '_CANDIDATE', {}).get('status')
                        not in ('COMPLETED', 'FAILED')), None)
                if index is None:
                    return {**self.status(), 'status': 'WAITING_TOTAL_AUTHORIZATION', 'waiting_reason': 'FINITE_TOTAL_ATTEMPTS_USED'}
                result = self._attempt(index, config, records)
                state = self.status()
                if result.get('status', '').startswith('WAITING_'):
                    state.update(status=result['status'], waiting_reason=result.get('waiting_reason'))
                return {**state, 'last_step': result, 'dispatched_segments': result.get('dispatched_segments', 0)}
            except BudgetExhaustedError as exc:
                return {**self.status(), 'status': 'WAITING_TOTAL_AUTHORIZATION', 'waiting_reason': str(exc)}
            except PermissionError as exc:
                return {**self.status(), 'status': 'WAITING_PERMISSION_OR_MODEL', 'waiting_reason': str(exc)}
            except Exception as exc:
                return {**self.status(), 'status': 'RECONCILIATION_REQUIRED', 'waiting_reason': type(exc).__name__ + ':' + str(exc)}

    tick = advance

    def _prepare_confirmation(self, config, records):
        if self.confirmation is None or self.confirmation.protocol_path.exists():
            return
        route = config['contract']['scope']['data_routes']['CONFIRMATION']
        ready = set(self.confirmation_material()['final_exploration_ready'])
        eligible = [row for row in records if row['candidate_id'] in ready]
        if route is None or not eligible:
            return
        tomorrow = (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()
        not_before = max(tomorrow, route['start'])
        if not_before > route['end']:
            return
        # 事前固定先完成且满足全部探索标准的一名候选；不按独立收益再选择。
        chosen = eligible[0]['candidate_id']
        proposal = read_json(self.root / chosen.lower() / 'PROPOSAL.json')
        request = {**deepcopy(config['template']), 'strategy_id': chosen,
                   'rule': deepcopy(proposal)}
        policy = {'version': 'FIRST_FINAL_EXPLORATION_READY_V1', 'maximum_selected_candidates': 1,
            'maximum_independent_exposures': 1, 'selection_basis': 'FIRST_COMPLETED_FINAL_EXPLORATION_READY',
            'result_use': 'DESCRIPTIVE_BUSINESS_CHECK_NO_MAXIMIZATION'}
        arguments = {'contract': config['contract'], 'selected': [chosen], 'requests': {chosen: request},
                     'selection_policy': policy, 'not_before': not_before}
        preview = self.confirmation.preview(**arguments)
        self.confirmation.freeze(preview_identity=preview['preview_identity'], **arguments)

    def status(self):
        config, view, records = self.config(), self.campaign.peek_status(), self._records()
        maximum = min(view['authorization']['resource_limits']['candidate_attempts'],
                      view['authorization']['max_batches'] * config['candidates_per_batch'])
        state, reason = 'READY', None
        if view.get('revoked'):
            state, reason = 'REVOKED', 'CAMPAIGN_REVOKED'
        elif view['paused']:
            state, reason = 'PAUSED', 'CAMPAIGN_PAUSED'
        elif view['expired']:
            state, reason = 'WAITING_TOTAL_AUTHORIZATION', 'CAMPAIGN_EXPIRED'
        elif len(records) >= maximum:
            state, reason = 'WAITING_TOTAL_AUTHORIZATION', 'FINITE_TOTAL_ATTEMPTS_USED'
        elif not self._control()['started']:
            state, reason = 'CREATED', 'EXPLICIT_START_REQUIRED'
        elif not callable(getattr(self.invoker, 'enforce_budget_limits', None)):
            state, reason = 'WAITING_MODEL', 'HARD_BUDGET_UNSUPPORTED'
        elif any(op['status'] == 'UNKNOWN' for op in view['operations'].values()):
            state, reason = 'RECONCILIATION_REQUIRED', 'PERSISTED_UNKNOWN_USAGE'
        final_queue = self._final_exploration().status()
        confirmation = (self.confirmation.status() if self.confirmation is not None and self.confirmation.protocol_path.exists()
                        else {'status': 'WAITING', 'waiting_reasons': ['INDEPENDENT_PROTOCOL_OR_DATA_NOT_READY']})
        goal_complete = confirmation.get('business_goal_met') is True
        if goal_complete:
            state, reason = 'BUSINESS_GOAL_MET', None
        return {'version': VERSION, 'status': state, 'waiting_reason': reason, 'goal_complete': goal_complete,
            'started': self._control()['started'],
            'progress': {'completed_attempts': len(records), 'authorized_attempts': maximum,
                'next_candidate_id': 'CANDIDATE_' + str(len(records) + 1).zfill(4) if len(records) < maximum else None},
            'attempts': records, 'selected': [row['candidate_id'] for row in records if (row.get('screen') or {}).get('passed')],
            'final_exploration_ready': sorted(set(final_queue['ready']) | {row['candidate_id'] for row in records
                if (row.get('screen') or {}).get('final_exploration_ready')}),
            'budget': view, 'channels': {'EXPLORATION': {'status': state, 'waiting_reason': reason},
                                       'FINAL_EXPLORATION': final_queue,
                                       'CONFIRMATION': confirmation},
            'strategy_qualified': False, 'paper_actual_days': 0}

    def confirmation_material(self):
        records = self._records()
        promoted = self._final_exploration().evidence()
        synthetic = any(read_json(path).get('synthetic') is True
                        for path in self.root.glob('candidate_*/model/INVOCATION.json'))
        return {'version': VERSION, 'config': self.config(), 'attempts': records,
            'selected': [row['candidate_id'] for row in records if (row.get('screen') or {}).get('passed')],
            'final_exploration_ready': sorted({row['candidate_id'] for row in records if (row.get('screen') or {}).get('final_exploration_ready')}
                | {row['candidate_id'] for row in promoted if row['screen']['final_exploration_ready']}),
            'final_exploration_evidence': [{key: row[key] for key in ('candidate_id', 'rule_identity',
                'identity', 'evidence_path', 'evidence_sha256', 'task_id', 'input_identity')} for row in promoted],
            'selection_policy': self.config()['selection_policy'], 'profile': 'SYNTHETIC' if synthetic else 'HISTORICAL_MODELED',
            'budget_hash': stable_hash(self.campaign.peek_status()), 'exposures': [], 'strategy_qualified': False}

    def handover(self, design=True):
        state = self.status()
        if not design:
            return {**state, 'contract': self.config()['contract'], 'root': str(self.root)}
        return {'version': VERSION, 'research_root': str(self.root),
            'contract_identity': self.config()['contract']['content_hash'],
            'progress': state['progress'], 'history': [{'candidate_id': row['candidate_id'],
                'rule_identity': row['rule_identity'], 'feedback_codes': row['feedback']['codes'],
                'hypothesis': row['hypothesis'], 'change_reason': row['change_reason']} for row in state['attempts']],
            'independent_results_omitted': True, 'goal_complete': False,
            'resume': '同一受信部署恢复原campaign并调用advance；不能重建新余额。'}
