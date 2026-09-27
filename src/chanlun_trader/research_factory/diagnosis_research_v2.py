"""诊断 → 有预算的AI候选 → 公共账户 → 成本/分段初筛 → 独立评审。"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import time

from .bounded_research_v1 import _put, _read, source_identity
from .bounded_research_v2 import BoundedResearchSessionV2
from .common import stable_hash
from .etf_account_governance_v1 import StrategyBatchGovernanceV1
from .historical_process_v1 import historical_input_identity
from .rule_account_backend_v2 import rule_input_identity
from .research_screening_v2 import prepare_account, run_account
from .mutation_boundary import MutationBusyError, ObjectiveMutationLock
from .research_screening_v1 import seed_diagnostics, qualitative_feedback
from .research_screening_v2 import POLICY, screen, settled_result, validated_screening
from .strategy_qualification_v1 import BoundedStrategyArchiveV1


from .diagnosis_research_v1 import DiagnosisInvokerV1


class DiagnosisResearchV2:
    def __init__(self, root):
        self.root = Path(root).absolute()
        if self.root.resolve() != self.root:
            raise ValueError('DIAGNOSIS_PATH_REDIRECTED')
        self.session = BoundedResearchSessionV2(self.root / 'search')
        self.archive = BoundedStrategyArchiveV1(self.root / 'archives')

    @classmethod
    def create(cls, root, *, seed_root, data_root, input_manifest, approval_statement, attempts=2):
        from scripts.run_historical_process_research_v1 import build_bundle
        root = Path(root).absolute()
        seed_root, data_root = Path(seed_root).absolute(), Path(data_root).absolute()
        if any(p.resolve() != p for p in (root, seed_root, data_root)):
            raise ValueError('DIAGNOSIS_PATH_REDIRECTED')
        if root.exists() and any(root.iterdir()):
            raise ValueError('DIAGNOSIS_NEW_ROOT_REQUIRED')
        seed = seed_diagnostics(seed_root)
        window, bundle, _ = build_bundle(data_root)
        config = {'version': 'DIAGNOSIS_RESEARCH_V2', 'seed_root': str(seed_root), 'data_root': str(data_root),
                  'seed': seed, 'window': window, 'input_identity': rule_input_identity(bundle, window),
                  'benchmark_input_identity': historical_input_identity(bundle, window),
                  'policy': POLICY, 'source_identity': source_identity(), 'approval_statement': approval_statement,
                  'frozen_at': datetime.now(timezone.utc).isoformat(), 'attempts': attempts,
                  'independent_policy': 'FUTURE_CAPTURE_ONLY_EXISTING_FORMAL_AUTHORITY'}
        if len(window['symbols']) != 2 or len([d for d in window['calendar'] if d >= window['account_start']]) != 504:
            raise ValueError('DIAGNOSIS_V2_TWO_SYMBOL_504_REQUIRED')
        from .formal_rule_adapter_v2 import execution_scope
        execution_scope(input_manifest, window['symbols'])
        if (input_manifest.get('window') != window
                or input_manifest.get('input_identity') != rule_input_identity(bundle, window)):
            raise ValueError('DIAGNOSIS_V2_SEARCH_INPUT_CONFLICT')
        # 配置及两种后端输入身份在首次模型调用前一起冻结。
        manifest = {**input_manifest, 'diagnosis_config_hash': stable_hash(config)}
        BoundedResearchSessionV2.create(root / 'search', objective_id='DIAGNOSIS_' + stable_hash(config)[:24],
            input_manifest=manifest, approval_statement=approval_statement, max_attempts=attempts, wall_seconds=7200)
        _put(root / 'RUN.json', config)
        return cls(root)

    def config(self, *, active=False):
        value = _read(self.root / 'RUN.json')
        scope = self.session.scope(active=active)
        if (value['policy'] != POLICY or scope['input_manifest']['diagnosis_config_hash'] != stable_hash(value)
                or (active and value['source_identity'] != source_identity())):
            raise ValueError('DIAGNOSIS_FROZEN_CONFIG_CHANGED')
        return value

    def _accounts(self, root, plans, jobs, bundle, config):
        if root.resolve() != root or not root.is_relative_to(self.root):
            raise ValueError('DIAGNOSIS_PATH_REDIRECTED')
        identity = config['benchmark_input_identity'] if set(jobs) == {'BENCHMARK_BASE'} else config['input_identity']
        governance = StrategyBatchGovernanceV1(root / 'governance', root / 'search_budget_registry.json',
                                               self.session.scope()['objective_id'], plans)
        if not governance.receipt_path.exists():
            governance.confirm({'origin': 'USER_EXPLICIT_CURRENT_TASK', 'statement': config['approval_statement'],
                'approved_plan_ids': {k: p['plan_id'] for k, p in plans.items()},
                'expires_at': self.session.scope()['expires_at']},
                {'input_identity': identity, 'novelty': {k: {'allowed': True, 'plan_id': p['plan_id'],
                   'reason': 'PREDECLARED_COST_AND_SUBPERIOD_SCREEN'} for k, p in plans.items()}})
        results = {}
        for name, (proposal, cost) in jobs.items():
            if (root / (name + '_RESULT.json')).exists():
                results[name] = settled_result(root, name, plan=plans[name], input_identity=identity)
                continue
            self.config(active=True)
            governance.start(name)
            started = time.monotonic()
            def guard():
                self.config(active=True)
                return governance.active_execution(name)
            try:
                result = run_account(proposal, strategy_id=name, window=config['window'], bundle=bundle,
                    costs=cost, input_identity=identity, active_check=guard)
                _put(root / (name + '_RESULT.json'), result)
                governance.settle(name, completed=True, seconds=time.monotonic() - started, result_hash=stable_hash(result))
            except Exception as error:
                governance.settle(name, completed=False, seconds=time.monotonic() - started,
                                  result_hash=None, error=type(error).__name__ + ':' + str(error))
                raise
            results[name] = settled_result(root, name, plan=plans[name], input_identity=identity)
        return results

    def tick(self, *, loader, invoker):
        from scripts.run_historical_process_research_v1 import build_bundle
        with ObjectiveMutationLock.for_resource(self.root / 'RUN.json'):
            config = self.config(active=True)
            if (self.root / 'screening' / 'SCREENING.json').exists():
                return self.status()
            window, bundle, _ = build_bundle(Path(config['data_root']))
            if (window != config['window'] or rule_input_identity(bundle, window) != config['input_identity']
                    or historical_input_identity(bundle, window) != config['benchmark_input_identity']):
                raise ValueError('DIAGNOSIS_SCREEN_INPUT_CHANGED')
            target = self.root / 'screening'
            if target.resolve() != target:
                raise ValueError('DIAGNOSIS_PATH_REDIRECTED')
            plans = {'BENCHMARK_BASE': prepare_account(None, strategy_id='BENCHMARK_BASE', window=window)}
            _put(target / 'BENCHMARK_PLAN.json', plans)
            benchmark = self._accounts(target, plans, {'BENCHMARK_BASE': (None, 'BASE')}, bundle, config)['BENCHMARK_BASE']
            # 先处理已完成而尚未初筛的候选；恢复不能越过此步继续调用模型。
            feedback = list(config['seed']['feedback'])
            archived = {}
            for index in range(1, config['attempts'] + 1):
                name = f'CANDIDATE_{index:03d}'
                if not self.session.path(name, 'DECISION.json').exists():
                    enhanced = DiagnosisInvokerV1(invoker, feedback=feedback, previous_designs=[])
                    self.session.tick(loader=loader, invoker=enhanced)
                    if not self.session.path(name, 'DECISION.json').exists():
                        return self.status()
                if not self.session.path(name, 'RESULT.json').exists():
                    continue  # 非法或重复规则已由原会话记账，不生成虚假账户。
                item = self.archive.freeze(self.session.root, name)
                archived[name] = item['archive_hash']
                jobs = {name + '_' + cost: (item['proposal'], cost) for cost in ('BASE', 'STRESS')}
                plans = {key: prepare_account(proposal, strategy_id=key, window=window, costs=cost)
                         for key, (proposal, cost) in jobs.items()}
                if (target / name).resolve() != target / name:
                    raise ValueError('DIAGNOSIS_PATH_REDIRECTED')
                _put(target / name / 'PLANS.json', {'archive_hash': item['archive_hash'], 'plans': plans})
                results = self._accounts(target / name, plans, jobs, bundle, config)
                report = screen(results[name + '_BASE'], results[name + '_STRESS'], benchmark, config=config)
                _put(target / name / 'SCREEN.json', report)
                feedback.append(qualitative_feedback(report))
            # 关闭原会话，并一次提交完整家族初筛，避免挑选历史赢家。
            self.session.tick(loader=loader, invoker=invoker)
            scope = {'policy': POLICY, 'archives': archived, 'input_identity': config['input_identity'],
                     'benchmark_input_identity': config['benchmark_input_identity'],
                     'benchmark_plan': _read(target / 'BENCHMARK_PLAN.json')['BENCHMARK_BASE'],
                     'run_config_hash': stable_hash(config)}
            _put(target / 'SCREEN_SCOPE.json', scope)
            reports = {name: _read(target / name / 'SCREEN.json') for name in archived}
            _put(target / 'SCREENING.json', {'policy': POLICY, 'scope_hash': stable_hash(scope), 'reports': reports,
                'selected': sorted(k for k, v in reports.items() if v['passed']), 'strategy_qualified': False})
            return self.status()

    def archives(self):
        return [self.archive.load(p.parent.name) for p in sorted(self.archive.root.glob('*/ARCHIVE.json'))]

    def status(self):
        self.config()
        search = self.session.status()
        if not (self.root / 'screening' / 'SCREENING.json').exists():
            from .exploration_governance import read_json
            rejected = sorted(self.session.root.glob('CANDIDATE_*/model/DIAGNOSIS_REJECTED.json'))
            if rejected:
                return {'status': 'BLOCKED', 'reason': _read(rejected[0])['reason'],
                        'search': search, 'strategy_qualified': False, 'independent_review': 'NOT_SUBMITTED'}
            for start in sorted((self.root / 'screening').glob('**/*_START.json')):
                settlement = start.with_name(start.name.replace('_START.json', '_SETTLEMENT.json'))
                if not settlement.exists() or read_json(settlement)['completed'] is not True:
                    if not settlement.exists():
                        try:
                            ObjectiveMutationLock.for_resource(self.root / 'RUN.json').probe()
                        except MutationBusyError:
                            return {'status': 'SCREENING_RUNNING', 'search': search, 'strategy_qualified': False,
                                    'independent_review': 'NOT_SUBMITTED'}
                    return {'status': 'BLOCKED', 'reason': 'SCREEN_ACCOUNT_UNSETTLED_NO_AUTOMATIC_RETRY',
                            'search': search, 'strategy_qualified': False, 'independent_review': 'NOT_SUBMITTED'}
            return {'status': search['status'], 'search': search, 'strategy_qualified': False,
                    'independent_review': 'NOT_SUBMITTED'}
        screening = validated_screening(self.root / 'screening', self.archives())
        return {'status': 'READY_FOR_INDEPENDENT_REVIEW' if screening['selected'] else 'NO_CANDIDATE_PASSED',
                'screening': screening, 'search': search, 'strategy_qualified': False,
                'independent_review': _read(self.root / 'HANDOFF.json') if (self.root / 'HANDOFF.json').exists() else 'NOT_SUBMITTED'}

    def handoff(self, *, calibration_path, not_before):
        from .formal_assessment_v1 import FormalAssessmentServiceV1
        with ObjectiveMutationLock.for_resource(self.root / 'RUN.json'):
            status = self.status()
            if status['status'] != 'READY_FOR_INDEPENDENT_REVIEW':
                return status
            service = FormalAssessmentServiceV1(self.archive.root)
            if (self.root / 'HANDOFF.json').exists():
                return service.status(_read(self.root / 'HANDOFF.json')['batch_id'])
            # PLAN 已提交而交接回执尚未写出时，只恢复同一批次。
            scope_id = self.session.scope()['scope_id']
            existing = [p for p in service._plans() if p['scope_id'] == scope_id]
            if existing:
                result = service.status(existing[0]['batch_id'])
            else:
                result = service.register(strategy_ids=[a['strategy_id'] for a in self.archives()],
                    symbols=self.config()['window']['symbols'], not_before=not_before,
                    calibration_path=calibration_path, screening_root=self.root / 'screening')
            _put(self.root / 'HANDOFF.json', {'batch_id': result['batch_id'], 'authority_root': str(service.root)})
            return result
