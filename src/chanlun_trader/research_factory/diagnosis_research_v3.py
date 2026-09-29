"""公共账户入口之上的连续研究；模型只提案，证据门和预算决定后续动作。"""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import math
import time
import os
import sys
import shutil

from .bounded_model_v1 import BoundedCodexInvokerV1
from .budget import BudgetExhaustedError
from .common import stable_hash
from .context import PerformanceBlindGuard
from .exploration_governance import immutable, read_json
from .mutation_boundary import ObjectiveMutationLock
from .research_campaign_v1 import ResearchCampaignV1
from .research_evidence_v1 import verify_job_evidence
from .research_rule_strategy_v3 import CAPABILITY, ResearchRuleStrategyV3
from .strategy_submission_v1 import REQUEST_FIELDS

VERSION = 'DIAGNOSIS_RESEARCH_V3'
SCREEN_VERSION = 'PUBLIC_ACCOUNT_INITIAL_SCREEN_V1'
SCREEN_FIELDS = {'version', 'min_base_net_return', 'min_stress_net_return', 'max_drawdown',
                 'min_trade_count', 'min_subperiod_net_return', 'require_positive_excess'}


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _screen_policy(value):
    if not isinstance(value, dict) or set(value) != SCREEN_FIELDS or value['version'] != SCREEN_VERSION:
        raise ValueError('DIAGNOSIS_SCREEN_POLICY_INVALID')
    for key in ('min_base_net_return', 'min_stress_net_return', 'min_subperiod_net_return'):
        if type(value[key]) not in (int, float) or not math.isfinite(value[key]) or not -1 <= value[key] <= 10:
            raise ValueError('DIAGNOSIS_SCREEN_THRESHOLD_INVALID')
    if (type(value['max_drawdown']) not in (int, float) or not 0 <= value['max_drawdown'] <= 1
            or type(value['min_trade_count']) is not int or value['min_trade_count'] < 1
            or type(value['require_positive_excess']) is not bool):
        raise ValueError('DIAGNOSIS_SCREEN_THRESHOLD_INVALID')
    return deepcopy(value)


class DiagnosisResearchV3:
    def __init__(self, campaign, submission, invoker=None):
        if not isinstance(campaign, ResearchCampaignV1):
            raise ValueError('DIAGNOSIS_REGISTERED_CAMPAIGN_REQUIRED')
        self.campaign, self.submission = campaign, submission
        self.invoker = invoker if invoker is not None else BoundedCodexInvokerV1()
        self.root = campaign.directory / 'diagnosis_v3'

    @classmethod
    def create(cls, campaign, submission, *, template, screen_policy, model_limits, candidates_per_batch=1, invoker=None):
        service = cls(campaign, submission, invoker)
        if not isinstance(template, dict) or set(template) != REQUEST_FIELDS - {'rule', 'strategy_id'}:
            raise ValueError('DIAGNOSIS_TEMPLATE_FIELDS')
        if template['purpose'] != 'EXPLORATORY' or template['benchmark'] != 'FULL_POOL_BUY_HOLD':
            raise ValueError('DIAGNOSIS_EXPLORATION_AND_FULL_POOL_BENCHMARK_REQUIRED')
        authorization = campaign.status()['authorization']
        if (type(candidates_per_batch) is not int or not 1 <= candidates_per_batch <= authorization['max_candidates_per_batch']
                or 'EXPLORATION' not in authorization['stages']):
            raise ValueError('DIAGNOSIS_BATCH_SCOPE_INVALID')
        required = {'timeout_seconds', 'max_tokens', 'max_cost_microunits', 'cost_bound_evidence', 'account_wall_seconds'}
        if not isinstance(model_limits, dict) or set(model_limits) != required:
            raise ValueError('DIAGNOSIS_MODEL_LIMITS_REQUIRED')
        for key in required - {'cost_bound_evidence'}:
            if type(model_limits[key]) is not int or model_limits[key] < 1:
                raise ValueError('DIAGNOSIS_MODEL_LIMIT_INVALID')
        if not isinstance(model_limits['cost_bound_evidence'], str) or not model_limits['cost_bound_evidence']:
            raise ValueError('DIAGNOSIS_MODEL_COST_BOUND_UNAVAILABLE')
        guarantee = service._model_guarantee(model_limits)
        config = {'version': VERSION, 'model_budget_guarantee': guarantee, 'template': deepcopy(template), 'screen_policy': _screen_policy(screen_policy),
                  'model_limits': deepcopy(model_limits), 'candidates_per_batch': candidates_per_batch,
                  'campaign_authorization_hash': stable_hash(authorization),
                  'selection_policy': 'ALL_SCREEN_PASSED_WITH_FULL_FAMILY_NO_FORMAL_QUALIFICATION'}
        with ObjectiveMutationLock.for_resource(service.root / 'CONFIG.json'):
            immutable(service.root / 'CONFIG.json', config)
        return service

    def _model_guarantee(self, limits):
        # 只有维护者注入的可信适配器能安装并证明供应商限制；字符串不是费用保障。
        enforce = getattr(self.invoker, 'enforce_budget_limits', None)
        if not callable(enforce):
            raise PermissionError('DIAGNOSIS_MODEL_HARD_BUDGET_UNVERIFIED')
        receipt = enforce(max_tokens=limits['max_tokens'], max_cost_microunits=limits['max_cost_microunits'])
        if (not isinstance(receipt, dict) or receipt.get('enforced') is not True
                or receipt.get('max_tokens') != limits['max_tokens']
                or receipt.get('max_cost_microunits') != limits['max_cost_microunits']
                or not isinstance(receipt.get('provider'), str) or not receipt['provider']
                or not isinstance(receipt.get('evidence_identity'), str) or not receipt['evidence_identity']):
            raise PermissionError('DIAGNOSIS_MODEL_HARD_BUDGET_UNVERIFIED')
        return deepcopy(receipt)

    def config(self):
        value = read_json(self.root / 'CONFIG.json')
        if value['version'] != VERSION or value['campaign_authorization_hash'] != stable_hash(self.campaign.status()['authorization']):
            raise ValueError('DIAGNOSIS_CAMPAIGN_SCOPE_CHANGED')
        _screen_policy(value['screen_policy'])
        return value

    def _op(self, identifier):
        return self.campaign.status()['operations'].get(identifier)

    def _reserve(self, identifier, batch, kind, subject, bounds, **kwargs):
        return self.campaign.reserve_operation(operation_id=identifier, batch_id=batch, stage='EXPLORATION',
            kind=kind, subject_identity=subject, upper_bounds=bounds, **kwargs)

    def _settle(self, identifier, actual, evidence, outcome='COMPLETED'):
        return self.campaign.settle_operation(identifier, actual=actual, outcome=outcome, evidence_identity=evidence)

    def _context(self, config, earlier):
        snapshot = deepcopy(self.submission.capabilities())
        # 模型只获该任务探索资料的元信息，不获得其他目录、确认窗口或账户明细。
        snapshot['data'] = {key: deepcopy(config['template'][key]) for key in
                            ('dataset_id', 'symbols', 'feature_start', 'account_start', 'account_end', 'purpose')}
        rules = {**snapshot['rules'], 'capability': CAPABILITY, 'public_entry': snapshot}
        context = {'capabilities': rules, 'capability_fingerprint': snapshot['fingerprint'],
                   'constraints': deepcopy(config['template']),
                   'previous_designs': [{'rule_identity': row.get('rule_identity'), 'hypothesis': row.get('hypothesis'),
                                        'change_reason': row.get('change_reason')} for row in earlier],
                   'failure_classes': [row['feedback'] for row in earlier],
                   'evidence_boundary': 'EXPLORATORY_QUALITATIVE_FEEDBACK_ONLY_NO_CONFIRMATION_DATA'}
        PerformanceBlindGuard.assert_blind(context)
        return context

    def _model(self, directory, name, batch, context, limits):
        identifier = name + '_MODEL'
        if self._model_guarantee(limits) != self.config()['model_budget_guarantee']:
            raise PermissionError('DIAGNOSIS_MODEL_BUDGET_GUARANTEE_CHANGED')
        bounds = {'model_calls': 1, 'model_tokens': limits['max_tokens'],
                  'model_cost_microunits': limits['max_cost_microunits'], 'wall_seconds': limits['timeout_seconds']}
        operation = self._reserve(identifier, batch, 'MODEL', stable_hash(context), bounds,
                                  cost_bound_evidence=limits['cost_bound_evidence'])
        model = directory / 'model'
        recover = self.invoker.can_recover(model, stable_hash(context))
        if operation['status'] in ('RUNNING', 'UNKNOWN', 'COMPLETED', 'FAILED') and not recover:
            if operation['status'] in ('RUNNING', 'UNKNOWN'):
                self.campaign.mark_unknown(identifier, 'MODEL_REQUEST_RESULT_UNKNOWN_NO_RETRY')
            return None
        if operation['status'] == 'RESERVED':
            self.campaign.start_operation(identifier)
        try:
            proposal = self.invoker.invoke(context, staging_dir=model, timeout_seconds=limits['timeout_seconds'])
        except Exception:
            if self._op(identifier)['status'] in ('RUNNING', 'UNKNOWN'):
                self.campaign.mark_unknown(identifier, 'MODEL_INVOCATION_REQUIRES_RECEIPT_RECONCILIATION')
            raise
        receipt = read_json(model / 'INVOCATION.json')
        if receipt.get('context_hash') != stable_hash(context) or receipt.get('response_hash') != stable_hash(proposal):
            raise ValueError('DIAGNOSIS_MODEL_RECEIPT_CONFLICT')
        # 当前适配器没有可验证token/费用明细时保守结算整个可信预留上界，不编造零消费。
        self._settle(identifier, bounds, stable_hash(receipt))
        return proposal

    def _record(self, directory, name, batch, *, status, feedback, proposal=None, rule_identity=None, screen=None):
        previous = sorted(self.root.glob('candidate_*/RECORD.json'))
        parent = read_json(previous[-1]).get('rule_identity') if previous else None
        record = {'version': VERSION, 'candidate_id': name, 'batch_id': batch, 'status': status,
                  'rule_identity': rule_identity, 'parent_rule_identity': parent,
                  'hypothesis': proposal.get('hypothesis') if isinstance(proposal, dict) else None,
                  'change_reason': proposal.get('change_reason') if isinstance(proposal, dict) else None,
                  'feedback': feedback, 'screen': screen, 'strategy_qualified': False,
                  'falsification_policy': self.config()['screen_policy'],
                  'interpretation': '本样本初筛事实不证明机制普遍成立或失效。',
                  'artifacts': {p.name: _digest(p) for p in directory.glob('*.json') if p.name != 'RECORD.json'}}
        immutable(directory / 'RECORD.json', record)
        candidate = name + '_CANDIDATE'
        if self._op(candidate)['status'] not in ('COMPLETED', 'FAILED'):
            self._settle(candidate, {'candidate_attempts': 1, 'wall_seconds': 1}, stable_hash(record),
                         outcome='COMPLETED' if status == 'SCREENED' else 'FAILED')
        return record

    def _bounded(self, directory, kind, payload, seconds):
        from ..synthetic_batch_resources import run_bounded_worker
        request = {'kind': kind, **payload}
        path = directory / (kind + '_WORKER.json')
        result_path = path.with_name(path.stem + '_RESULT.json')
        resource_path = path.with_name(path.stem + '_RESOURCE.json')
        identity = stable_hash(request)
        if path.exists():
            if read_json(path) != request:
                raise ValueError('DIAGNOSIS_WORKER_REQUEST_CHANGED')
            if not resource_path.exists():
                raise RuntimeError('DIAGNOSIS_WORKER_UNKNOWN_NO_REDISPATCH')
        else:
            immutable(path, request)
            repo = Path(__file__).resolve().parents[3]
            env = {**os.environ, 'PYTHONPATH': str(repo / 'src') + os.pathsep + str(repo),
                   'PYTHONDONTWRITEBYTECODE': '1',
                   **{key: '1' for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS')}}
            # Git for Windows 的 cmd 包装器会再生成一层进程；使用同安装的实际二进制，
            # 避免Git包装器多一层；DATA只额外预留一个进程，VERIFY仍保持双进程限制。
            git = shutil.which('git')
            if os.name == 'nt' and git:
                direct_git = Path(git).parent.parent / 'mingw64' / 'bin' / 'git.exe'
                if direct_git.is_file():
                    env['PATH'] = str(direct_git.parent) + os.pathsep + env.get('PATH', '')
            started = time.monotonic()
            resource = run_bounded_worker([sys.executable, str(Path(__file__).with_name('diagnosis_worker_v3.py')), str(path), identity],
                root=repo, memory_mib=2048, wall_seconds=seconds, environment=env,
                process_limit=3 if kind == 'DATA' else 2,
                execution={'request_hash': identity},
                on_started=lambda pid: immutable(path.with_name(path.stem + '_START.json'), {'pid': pid, 'request_hash': identity}))
            resource['elapsed_wall_seconds'] = time.monotonic() - started
            resource['wall_limit_seconds'] = seconds
            immutable(resource_path, {key: value.decode('utf-8', errors='replace') if isinstance(value, bytes) else value
                                      for key, value in resource.items()})
        resource = read_json(resource_path)
        if resource['returncode'] != 0 or resource['timed_out'] or not result_path.exists():
            raise RuntimeError('DIAGNOSIS_WORKER_FAILED_NO_RETRY')
        output = read_json(result_path)
        if output['request_hash'] != identity:
            raise ValueError('DIAGNOSIS_WORKER_RESULT_CONFLICT')
        return output['result'], max(1, math.ceil(resource['elapsed_wall_seconds']))

    def _task(self, directory, request, preview, seconds):
        usage_path = directory / 'DATA_USAGE.json'
        if (directory / 'TASK.json').exists():
            task = read_json(directory / 'TASK.json')
            self.submission._task(task['task_id'])
            if not usage_path.exists():
                raise RuntimeError('DIAGNOSIS_DATA_USAGE_RECONCILIATION_REQUIRED')
            return task
        from .research_data_provider_v1 import ResearchDataProviderV1
        if type(self.submission.provider) is not ResearchDataProviderV1:
            raise ValueError('DIAGNOSIS_BOUNDED_DATA_PROVIDER_REQUIRED')
        provider = self.submission.provider
        task, elapsed = self._bounded(directory, 'DATA', {
            'request': request, 'preview_identity': preview['preview_identity'],
            'authority': self.submission.authority(request['authorization_ref']),
            'submission_root': str(self.submission.root), 'capabilities': self.submission.capabilities(),
            'roots': {key: str(value) for key, value in provider.roots.items()},
            'datasets': {key: [str(value[0]), value[1], value[2]] for key, value in provider._datasets.items()}}, seconds)
        immutable(usage_path, {'wall_seconds': elapsed, 'resource_enforced': True})
        immutable(directory / 'TASK.json', task)
        return task

    def _screen(self, task, name, policy):
        job_path = Path(task['job_path'])
        job = read_json(job_path)
        checks = {key: verify_job_evidence(job_path, name=key) for key in job['plans']}
        if not all(check.get('advance_allowed') is True for check in checks.values()):
            raise ValueError('DIAGNOSIS_ACCOUNT_EVIDENCE_NOT_VERIFIED')
        audits = {suffix: checks[name + '_' + suffix]['account_audit'] for suffix in ('BASE', 'STRESS', 'BENCHMARK')}
        base, stress, benchmark = (audits[key]['metrics'] for key in ('BASE', 'STRESS', 'BENCHMARK'))
        daily = audits['BASE']['daily_accounts']
        mid = len(daily) // 2
        if mid < 1:
            raise ValueError('DIAGNOSIS_SUBPERIOD_SAMPLE_INSUFFICIENT')
        cash = job['plans'][name + '_BASE']['backend']['initial_cash']
        periods = [daily[mid-1]['equity']/cash-1, daily[-1]['equity']/daily[mid-1]['equity']-1]
        gates = {'base_positive': base['net_return'] >= policy['min_base_net_return'],
                 'stress_resilient': stress['net_return'] >= policy['min_stress_net_return'],
                 'drawdown_within_limit': base['max_drawdown'] <= policy['max_drawdown'],
                 'sample_size': base['trade_count'] >= policy['min_trade_count'],
                 'subperiod_consistency': all(value >= policy['min_subperiod_net_return'] for value in periods),
                 'benchmark_excess': not policy['require_positive_excess'] or base['net_return'] > benchmark['net_return']}
        return {'version': SCREEN_VERSION, 'policy': policy, 'checks': gates, 'passed': all(gates.values()),
                'base': base, 'stress': stress, 'benchmark': benchmark, 'subperiod_net_returns': periods,
                'qualification': 'HISTORICAL_INITIAL_SCREEN_ONLY',
                'evidence': {key: {'plan_id': value['plan_id'], 'result_sha256': value['result_sha256'],
                                  'input_identity': value['input_identity']} for key, value in checks.items()}}

    def _attempt(self, index, config, earlier):
        name = f'CANDIDATE_{index:04d}'
        directory = self.root / f'candidate_{index:04d}'
        batch = f'BATCH_{(index - 1) // config["candidates_per_batch"] + 1:04d}'
        if (directory / 'RECORD.json').exists():
            record = read_json(directory / 'RECORD.json')
            for filename, digest in record['artifacts'].items():
                if _digest(directory / filename) != digest:
                    raise ValueError('DIAGNOSIS_RESEARCH_RECORD_CHANGED')
            operation = self._op(name + '_CANDIDATE')
            if operation['status'] in ('RUNNING', 'UNKNOWN'):
                self._settle(name + '_CANDIDATE', {'candidate_attempts': 1, 'wall_seconds': 1}, stable_hash(record),
                             outcome='COMPLETED' if record['status'] == 'SCREENED' else 'FAILED')
            elif operation.get('evidence_identity') != stable_hash(record):
                raise ValueError('DIAGNOSIS_RECORD_BUDGET_CONFLICT')
            return record
        self._reserve(name + '_CANDIDATE', batch, 'CANDIDATE', name, {'candidate_attempts': 1, 'wall_seconds': 1})
        if self._op(name + '_CANDIDATE')['status'] == 'RESERVED':
            self.campaign.start_operation(name + '_CANDIDATE')
        if (directory / 'CONTEXT.json').exists():
            context = read_json(directory / 'CONTEXT.json')
        else:
            context = self._context(config, earlier)
            immutable(directory / 'CONTEXT.json', context)
        proposal = self._model(directory, name, batch, context, config['model_limits'])
        if proposal is None:
            return {'status': 'WAITING_MODEL_RECONCILIATION', 'candidate_id': name}
        immutable(directory / 'PROPOSAL.json', proposal)
        try:
            strategy = ResearchRuleStrategyV3(proposal, strategy_id=name)
            if any(row.get('rule_identity') == strategy.rule_identity for row in earlier):
                return self._record(directory, name, batch, status='REJECTED', feedback={'codes': ['DUPLICATE_RULE']},
                                    proposal=proposal, rule_identity=strategy.rule_identity)
            if context['capability_fingerprint'] != self.submission.capabilities()['fingerprint']:
                return self._record(directory, name, batch, status='REJECTED', feedback={'codes': ['CAPABILITY_CHANGED']},
                                    proposal=proposal, rule_identity=strategy.rule_identity)
            request = {**config['template'], 'rule': proposal, 'strategy_id': name}
            preview = self.submission.preview(request)
        except (ValueError, TypeError, KeyError):
            return self._record(directory, name, batch, status='REJECTED', feedback={'codes': ['INVALID_PUBLIC_RULE']}, proposal=proposal)
        data_op = name + '_DATA'
        self._reserve(data_op, batch, 'DATA', stable_hash({'preview': preview['preview_identity'], 'candidate': name}),
                      {'data_experiments': 1, 'wall_seconds': config['model_limits']['account_wall_seconds']})
        if self._op(data_op)['status'] == 'RESERVED':
            self.campaign.start_operation(data_op)
        data_usage_path = directory / 'DATA_USAGE.json'
        task = self._task(directory, request, preview, config['model_limits']['account_wall_seconds'])
        self._settle(data_op, {'data_experiments': 1, 'wall_seconds': read_json(data_usage_path)['wall_seconds']}, stable_hash(task))
        job_path = Path(task['job_path'])
        job = read_json(job_path)
        mapping = {}
        for number, (plan_name, plan) in enumerate(sorted(job['plans'].items())):
            op = f'{name}_ACCOUNT_{number}'
            self._reserve(op, batch, 'ACCOUNT', plan['plan_id'],
                          {'account_jobs': 1, 'wall_seconds': config['model_limits']['account_wall_seconds']})
            mapping[plan_name] = op
        binding = {'task_id': task['task_id'], 'job_path': str(job_path), 'job_sha256': _digest(job_path),
                   'input_identity': job['input_identity'], 'operation_ids': mapping}
        immutable(directory / 'ACCOUNT_BINDINGS.json', binding)
        if (job_path.parent / 'CONFIRMATION.json').exists():
            from scripts.run_strategy_account_v1 import service as governance_service
            receipt = governance_service(job).active()
            source = receipt['source']
            if (source.get('origin') != 'CAMPAIGN_V1' or source.get('operation_ids') != mapping
                    or source.get('authorization_id') != self.campaign.authorization_id
                    or Path(source.get('campaign_root', '')).resolve() != self.campaign.root):
                raise ValueError('DIAGNOSIS_EXISTING_APPROVAL_SCOPE_CONFLICT')
        else:
            self.submission.approve(task['task_id'], preview['preview_identity'], operation_ids=mapping)
        for number, plan_name in enumerate(sorted(job['plans'])):
            verify_op = f'{name}_VERIFY_{number}'
            self._reserve(verify_op, batch, 'VERIFY', job['plans'][plan_name]['plan_id'],
                          {'verification_jobs': 1, 'wall_seconds': config['model_limits']['account_wall_seconds']})
            if self._op(verify_op)['status'] == 'RESERVED':
                self.campaign.start_operation(verify_op)
        # 复用公共入口的同一固定账户执行阶段；核验和报告在下方受限 worker 内完成。
        from scripts.run_strategy_account_v1 import execute_accounts
        execute_accounts(job_path, recover=True)
        screen_path, usage_path = directory / 'SCREEN.json', directory / 'VERIFY_USAGE.json'
        if screen_path.exists() and usage_path.exists():
            screen = read_json(screen_path)
        else:
            screen, elapsed = self._bounded(directory, 'VERIFY', {'task': task, 'name': name,
                'policy': config['screen_policy']}, config['model_limits']['account_wall_seconds'])
            immutable(usage_path, {'wall_seconds': elapsed, 'resource_enforced': True,
                                   'measurement_scope': 'ALL_JOB_EVIDENCE_CHECKS'})
            immutable(screen_path, screen)
        for number, plan_name in enumerate(sorted(job['plans'])):
            start = read_json(job_path.parent / (plan_name + '_START.json'))
            settlement = read_json(job_path.parent / (plan_name + '_SETTLEMENT.json'))
            if (settlement['completed'] is not True or start['reservation'] != settlement['reservation']
                    or self._op(mapping[plan_name])['status'] not in ('RUNNING', 'COMPLETED')):
                raise ValueError('DIAGNOSIS_ACCOUNT_OPERATION_BINDING_CONFLICT')
            seconds = max(1, math.ceil(settlement['wall_seconds']))
            self._settle(mapping[plan_name], {'account_jobs': 1, 'wall_seconds': seconds}, stable_hash(settlement))
            self._settle(f'{name}_VERIFY_{number}', {'verification_jobs': 1, 'wall_seconds': read_json(usage_path)['wall_seconds']}, stable_hash(screen['evidence'][plan_name]))
        immutable(directory / 'SCREEN.json', screen)
        codes = [key.upper() for key, passed in screen['checks'].items() if not passed]
        return self._record(directory, name, batch, status='SCREENED', feedback={'codes': codes or ['INITIAL_SCREEN_PASSED_NOT_QUALIFIED']},
                            proposal=proposal, rule_identity=strategy.rule_identity, screen={'passed': screen['passed'], 'identity': stable_hash(screen)})

    def run(self):
        return self._run(one_candidate=False)

    def tick(self):
        """宿主一次至多推进一个未完成候选，让出后续队列的调度机会。"""
        return self._run(one_candidate=True)

    def _run(self, *, one_candidate):
        with ObjectiveMutationLock.for_resource(self.root / 'CONFIG.json'):
            config = self.config()
            authority = self.campaign.status()['authorization']
            maximum = min(authority['resource_limits']['candidate_attempts'], authority['max_batches'] * config['candidates_per_batch'])
            earlier = []
            for index in range(1, maximum + 1):
                completed = (self.root / f'candidate_{index:04d}' / 'RECORD.json').exists()
                try:
                    result = self._attempt(index, config, earlier)
                except BudgetExhaustedError as error:
                    return {**self.status(), 'stop_reason': str(error), 'status': 'BUDGET_STOPPED'}
                except PermissionError as error:
                    return {**self.status(), 'stop_reason': str(error), 'status': 'PAUSED_OR_SCOPE_WAITING'}
                except Exception as error:
                    return {**self.status(), 'stop_reason': type(error).__name__ + ':' + str(error), 'status': 'RECONCILIATION_REQUIRED'}
                if result['status'].startswith('WAITING'):
                    return {**self.status(), 'stop_reason': result['status'], 'status': result['status']}
                earlier.append(result)
                if one_candidate and not completed:
                    return {**self.status(), 'progressed_candidate_id': result['candidate_id']}
            return {**self.status(), 'status': 'AUTHORIZED_ATTEMPTS_COMPLETE', 'stop_reason': 'FROZEN_TOTAL_ATTEMPT_LIMIT'}

    def status(self):
        rows = [read_json(path) for path in sorted(self.root.glob('candidate_*/RECORD.json'))]
        view = self.campaign.status()
        config = self.config()
        authority = view['authorization']
        maximum = min(authority['resource_limits']['candidate_attempts'], authority['max_batches'] * config['candidates_per_batch'])
        completed = {row['candidate_id'] for row in rows}
        pending = [f'CANDIDATE_{index:04d}' for index in range(1, maximum + 1)
                   if f'CANDIDATE_{index:04d}' not in completed]
        state, reason = 'READY', 'AUTHORIZED_CANDIDATE_PENDING'
        if not pending:
            state, reason = 'AUTHORIZED_ATTEMPTS_COMPLETE', 'FROZEN_TOTAL_ATTEMPT_LIMIT'
        elif view['paused']:
            state, reason = 'PAUSED_OR_SCOPE_WAITING', 'CAMPAIGN_PAUSED'
        elif view['expired']:
            state, reason = 'BUDGET_STOPPED', 'CAMPAIGN_EXPIRED'
        elif not callable(getattr(self.invoker, 'enforce_budget_limits', None)):
            state, reason = 'PAUSED_OR_SCOPE_WAITING', 'DIAGNOSIS_MODEL_HARD_BUDGET_UNVERIFIED'
        elif view['stages'].get('EXPLORATION', {}).get('status') == 'WAITING':
            state, reason = 'PAUSED_OR_SCOPE_WAITING', 'CAMPAIGN_STAGE_WAITING'
        elif any(op['kind'] == 'MODEL' and op['status'] == 'UNKNOWN' for op in view['operations'].values()):
            state, reason = 'WAITING_MODEL_RECONCILIATION', 'MODEL_RESULT_UNKNOWN_NO_RETRY'
        elif any(read_json(path).get('timed_out') or read_json(path).get('returncode') != 0
                 for path in self.root.glob('candidate_*/*_WORKER_RESOURCE.json')):
            state, reason = 'RECONCILIATION_REQUIRED', 'WORKER_FAILED_NO_RETRY'
        elif any(op['status'] == 'UNKNOWN' for op in view['operations'].values()):
            state, reason = 'RECONCILIATION_REQUIRED', 'OPERATION_RESULT_UNKNOWN'
        elif any(op['status'] == 'RUNNING' for op in view['operations'].values()):
            state, reason = 'IN_PROGRESS', 'PERSISTED_OPERATION_STARTED'
        return {'version': VERSION, 'status': state, 'stop_reason': reason,
                'progress': {'completed_attempts': len(rows), 'authorized_attempts': maximum,
                             'remaining_attempts': len(pending), 'next_candidate_id': pending[0] if pending else None},
                'attempts': rows, 'selected': [row['candidate_id'] for row in rows if row.get('screen') and row['screen']['passed']],
                'strategy_qualified': False, 'independent_validation': 'NOT_RUN', 'budget': view}

    def confirmation_material(self):
        config = self.config()
        records, exposures = [], []
        for directory in sorted(self.root.glob('candidate_*')):
            path = directory / 'RECORD.json'
            record = read_json(path) if path.exists() else {'candidate_id': directory.name.upper(), 'status': 'INCOMPLETE_REQUIRES_RECONCILIATION'}
            for filename, digest in record.get('artifacts', {}).items():
                if _digest(directory / filename) != digest:
                    raise ValueError('DIAGNOSIS_RESEARCH_RECORD_CHANGED')
            if path.exists():
                operation = self._op(record['candidate_id'] + '_CANDIDATE')
                if operation is None or operation.get('evidence_identity') != stable_hash(record):
                    raise ValueError('DIAGNOSIS_RECORD_BUDGET_CONFLICT')
            records.append(record)
            if (directory / 'TASK.json').exists():
                task = read_json(directory / 'TASK.json')
                exposures.append({'task_id': task['task_id'], 'input_identity': task['input_identity'],
                                  'scope': deepcopy(config['template'])})
        view = self.campaign.status()
        synthetic = any(read_json(path).get('synthetic') is True for path in self.root.glob('candidate_*/model/INVOCATION.json'))
        method_scope = {key: deepcopy(config['template'][key]) for key in ('initial_cash', 'symbols', 'max_positions', 'max_symbol_exposure_bps', 'costs')}
        method_scope.update(return_process='NET_DAILY_ACCOUNT_EQUITY', screen_policy=config['screen_policy'],
                            selection_policy=config['selection_policy'], source_protocol=VERSION)
        return {'version': VERSION, 'config': config, 'attempts': records,
                'method_scope': method_scope, 'profile': 'SYNTHETIC' if synthetic else 'HISTORICAL_MODELED',
                'selected': [row['candidate_id'] for row in records if row.get('screen') and row['screen']['passed']],
                'selection_policy': config['selection_policy'], 'exposures': exposures,
                'budget_hash': stable_hash(view), 'strategy_qualified': False}
