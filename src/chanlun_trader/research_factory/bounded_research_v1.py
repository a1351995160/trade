"""公共策略账户的有界探索适配；复用预算、Trial 与策略治理，不授予研究资格。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time

from .budget import SearchBudgetRegistryV1
from .common import stable_hash
from .context import PerformanceBlindGuard
from .exploration_governance import read_json
from .mutation_boundary import MutationBusyError, ObjectiveMutationLock
from ..execution_policy import ExecutionPolicy, validate_research_root
from ..research.strategy_validation import TrialEvent, TrialRegistryV1

ROOT = Path(__file__).resolve().parents[3]
ACTIONS = ('GENERATE', 'MATERIALIZE', 'BACKTEST', 'FEEDBACK')


def _now():
    return datetime.now(timezone.utc)


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def source_identity():
    # 绑定本路径可能调用的引擎、指标及脚本；不能换源码恢复旧试验。
    return stable_hash({p.relative_to(ROOT).as_posix(): file_hash(p)
                        for folder in ('src', 'scripts')
                        for p in sorted((ROOT / folder).rglob('*.py'))})


def _read(path):
    value = read_json(path)
    if value.get('_integrity') != stable_hash({k: v for k, v in value.items() if k != '_integrity'}):
        raise ValueError('BOUNDED_ARTIFACT_CORRUPT')
    return {k: v for k, v in value.items() if k != '_integrity'}


def _put(path, payload):
    """调用者持有任务锁；不可覆盖既有事实，崩溃只留下未提交临时文件。"""
    path = Path(path)
    value = json.loads(json.dumps(payload, ensure_ascii=False, allow_nan=False))
    if path.exists():
        if _read(path) != value:
            raise ValueError('BOUNDED_IMMUTABLE_CONFLICT')
        return value
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump({**value, '_integrity': stable_hash(value)}, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    return value


class _AtomicTrialRegistry(TrialRegistryV1):
    """复用原 Trial 格式与幂等追加，仅将本地持久化提交改为原子替换。"""
    def append(self, event):
        temporary = self.path.with_suffix('.tmp')
        if self.path.exists():
            temporary.write_bytes(self.path.read_bytes())
        elif temporary.exists():
            temporary.write_text(json.dumps(self._payload()), encoding='utf-8')
        TrialRegistryV1(temporary).append(event)
        with temporary.open('r+b') as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)


class BoundedResearchSessionV1:
    """单机、逐候选的公共账户研究入口；状态来自冻结文件和既有权威账本。"""

    def __init__(self, root):
        self.root = Path(root).absolute()
        validate_research_root(self.root, ExecutionPolicy())

    def path(self, *parts):
        path = self.root.joinpath(*parts)
        if path.resolve() != path or not path.resolve().is_relative_to(self.root):
            raise ValueError('BOUNDED_PATH_REDIRECTED')
        return path

    def lock(self):
        return ObjectiveMutationLock.for_resource(self.path('SESSION.json'))

    @classmethod
    def create(cls, root, *, objective_id, input_manifest, approval_statement,
               max_attempts=5, wall_seconds=3600, actions=ACTIONS):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', objective_id):
            raise ValueError('BOUNDED_OBJECTIVE_INVALID')
        if type(max_attempts) is not int or not 1 <= max_attempts <= 5:
            raise ValueError('BOUNDED_ATTEMPT_LIMIT')
        if type(wall_seconds) is not int or not 1 <= wall_seconds <= 7200:
            raise ValueError('BOUNDED_WALL_LIMIT')
        if not isinstance(approval_statement, str) or not approval_statement.strip():
            raise PermissionError('BOUNDED_EXPLICIT_SCOPE_APPROVAL_REQUIRED')
        if set(actions) - set(ACTIONS) or len(actions) != len(set(actions)):
            raise ValueError('BOUNDED_ACTION_SCOPE_INVALID')
        if not input_manifest.get('input_identity') or input_manifest.get('profile') not in ('S1_MODELED_DAILY', 'SYNTHETIC'):
            raise ValueError('BOUNDED_INPUT_PROFILE_INVALID')
        root = Path(root).absolute()
        root.mkdir(parents=True, exist_ok=True)
        service = cls(root)
        with service.lock():
            if service.path('SESSION.json').exists():
                raise PermissionError('BOUNDED_EXISTING_SESSION_RESUME_ONLY')
            if any(root.iterdir()) and any(p.name != 'SESSION.json.mutation.lock' for p in root.iterdir()):
                raise ValueError('BOUNDED_EMPTY_ROOT_REQUIRED')
            now = _now()
            scope = {'version': 'BOUNDED_RESEARCH_V1', 'objective_id': objective_id,
                     'input_manifest': input_manifest, 'max_attempts': max_attempts,
                     'actions': list(actions), 'recorded_at': now.isoformat(),
                     'expires_at': (now + timedelta(seconds=wall_seconds)).isoformat(),
                     'approval_statement': approval_statement, 'source_identity': source_identity(),
                     'purpose': 'EXPLORATION_ONLY', 'qualification': 'NOT_ASSESSED',
                     'historical_publication': 'MODELED_NOT_VERIFIED',
                     'reference': 'FIXED_CAUSAL_51_NOT_SEARCH_CANDIDATE'}
            scope['scope_id'] = stable_hash(scope)
            _put(service.path('SESSION.json'), scope)
            service._budget(scope)
        return service

    def scope(self, *, active=False, action=None):
        scope = _read(self.path('SESSION.json'))
        if scope['scope_id'] != stable_hash({k: v for k, v in scope.items() if k != 'scope_id'}):
            raise ValueError('BOUNDED_SCOPE_CORRUPT')
        if active:
            if self.path('REVOKED.json').exists():
                raise PermissionError('BOUNDED_REVOKED')
            if _now() >= datetime.fromisoformat(scope['expires_at']):
                raise PermissionError('BOUNDED_DEADLINE_REACHED')
            if source_identity() != scope['source_identity']:
                raise PermissionError('BOUNDED_SOURCE_CHANGED')
            if action and action not in scope['actions']:
                raise PermissionError('BOUNDED_WAITING_AUTHORIZATION:' + action)
        return scope

    def _budget(self, scope):
        budget = SearchBudgetRegistryV1(scope['objective_id'], self.path('search_budget_registry.json'))
        budget.register_objective(scope['max_attempts'] + 1)
        budget.register_batch(scope['scope_id'], scope['max_attempts'] + 1)
        budget.register_family('REFERENCE', 1)
        budget.register_family('SEARCH', scope['max_attempts'])
        return budget

    def registry(self):
        registry = _AtomicTrialRegistry(self.path('trial_registry.json'))
        if registry.path.exists():
            raw = read_json(registry.path)
            if raw.get('registry_hash') != stable_hash(raw.get('events', [])):
                raise ValueError('BOUNDED_TRIAL_REGISTRY_CORRUPT')
        return registry

    def _event(self, scope, name, event, *, accessed=False, reasons=()):
        candidate = _read(self.path(name, 'CANDIDATE.json'))
        self.registry().append(TrialEvent(event, name, name, candidate['plan']['plan_id'],
                                         event, None, tuple(reasons), accessed,
                                         created_at=candidate['frozen_at']))

    def authorize_plan(self, name, plans):
        """供既有策略治理调用：验证父任务授权和被冻结的本次候选，非伪造逐项批准。"""
        scope = self.scope(active=True, action='BACKTEST')
        self.scope(active=True, action='MATERIALIZE')
        record = _read(self.path(name, 'CANDIDATE.json'))
        if plans != {name: record['plan']} or record['scope_id'] != scope['scope_id']:
            raise PermissionError('BOUNDED_PLAN_NOT_FROZEN')
        reservation = _read(self.path(name, 'ATTEMPT.json'))['reservation']
        budget = self._budget(scope)
        if budget.snapshot()['settled_reservations'].get(reservation) != 'CONSUMED':
            raise PermissionError('BOUNDED_ATTEMPT_NOT_CHARGED')
        return scope

    def _charge(self, scope, name):
        budget = self._budget(scope)
        family = 'REFERENCE' if name == 'REFERENCE' else 'SEARCH'
        budget.register_candidate(name)
        reservation = budget.reserve_trial(batch_id=scope['scope_id'], family_id=family,
                                           candidate_id=name)
        _put(self.path(name, 'ATTEMPT.json'), {'reservation': reservation, 'scope_id': scope['scope_id']})
        budget.consume(reservation)

    def _freeze(self, scope, name, strategy, backend, *, proposal, parent):
        from .strategy_interface_v1 import prepare
        record = {'scope_id': scope['scope_id'], 'plan': prepare(strategy, backend),
                  'proposal': proposal, 'parent': parent, 'frozen_at': _now().isoformat()}
        _put(self.path(name, 'CANDIDATE.json'), record)
        self._event(scope, name, 'REGISTERED_BEFORE_PERFORMANCE')

    def _execute(self, scope, name, strategy, backend, loader):
        from .etf_account_governance_v1 import StrategyBatchGovernanceV1
        from .strategy_interface_v1 import prepare, run
        candidate = _read(self.path(name, 'CANDIDATE.json'))
        if prepare(strategy, backend) != candidate['plan']:
            raise ValueError('BOUNDED_FROZEN_PLAN_CHANGED')
        self._event(scope, name, 'REGISTERED_BEFORE_PERFORMANCE')
        governance = StrategyBatchGovernanceV1(self.path(name, 'governance'),
            self.path('search_budget_registry.json'), scope['objective_id'], {name: candidate['plan']})
        if self.path(name, 'RESULT.json').exists():
            result = _read(self.path(name, 'RESULT.json'))
            if result['strategy_plan'] != candidate['plan']:
                raise ValueError('BOUNDED_RESULT_PLAN_CONFLICT')
            receipt = read_json(governance.receipt_path)
            start = read_json(governance.root / (name + '_START.json'))
            if (receipt['receipt_id'] != stable_hash({k:v for k,v in receipt.items() if k!='receipt_id'})
                    or receipt['strategy_plans'] != {name:candidate['plan']}
                    or start['receipt_id'] != receipt['receipt_id']
                    or result['input_identity'] != scope['input_manifest']['input_identity']):
                raise ValueError('BOUNDED_RECOVERY_RECEIPT_CONFLICT')
            budget = self._budget(scope)
            budget.reconcile(expected_used={(governance.budget_kind, receipt['receipt_id']+':'+name):1})
            if not governance.root.joinpath(name + '_SETTLEMENT.json').exists():
                governance.settle(name, completed=True, seconds=0, result_hash=stable_hash(result))
            settlement = read_json(governance.root / (name + '_SETTLEMENT.json'))
            if (settlement.get('completed') is not True or settlement.get('result_sha256') != stable_hash(result)
                    or any(settlement.get(k) != start[k] for k in ('receipt_id','kind','reservation'))):
                raise ValueError('BOUNDED_RECOVERY_SETTLEMENT_CONFLICT')
            self._event(scope, name, 'EXPLORATION_COMPLETED', accessed=True)
            return result
        if self.path(name, 'EXECUTION_STARTED.json').exists():
            raise RuntimeError('BOUNDED_INTERRUPTED_ACCOUNT_NO_AUTOMATIC_RETRY')
        self.authorize_plan(name, {name: candidate['plan']})
        if not governance.receipt_path.exists():
            governance.confirm_research_scope(self, name)
        else:
            governance.reconcile_research_scope(self, name)
        governance.start(name)
        _put(self.path(name, 'EXECUTION_STARTED.json'), {'scope_id': scope['scope_id']})
        self._event(scope, name, 'PERFORMANCE_ACCESSED', accessed=True)
        begin = time.monotonic()
        def guard():
            self.scope(active=True, action='BACKTEST')
            return governance.active_execution(name)
        try:
            guard()
            bundle, events = loader(scope['input_manifest'], strategy)
            if bundle['input_identity'] != scope['input_manifest']['input_identity']:
                raise PermissionError('BOUNDED_DATA_CHANGED')
            result = run(strategy, backend, frame=bundle, actions=events,
                         input_identity=bundle['input_identity'], active_check=guard)
            guard()
            result['input_identity'] = bundle['input_identity']
            result['comparison_context'] = {'input_identity': bundle['input_identity']}
            _put(self.path(name, 'RESULT.json'), result)
        except Exception as exc:
            message = str(exc)
            reason_code = message if re.fullmatch(r'(BOUNDED|PUBLIC_ENTRY|STRATEGY)_[A-Z0-9_]+', message) else 'BOUNDED_INTERRUPTED_ACCOUNT_NO_AUTOMATIC_RETRY'
            _put(self.path(name, 'FAILURE.json'), {'error_type': type(exc).__name__,
                 'reason': 'ACCOUNT_OR_DATA_FAILURE', 'reason_code': reason_code, 'automatic_retry': False})
            governance.settle(name, completed=False, seconds=time.monotonic()-begin,
                              result_hash=None, error=type(exc).__name__)
            self._event(scope, name, 'ENGINEERING_BLOCKED', accessed=True)
            raise
        governance.settle(name, completed=True, seconds=time.monotonic()-begin, result_hash=stable_hash(result))
        self._event(scope, name, 'EXPLORATION_COMPLETED', accessed=True)
        return result

    def _diagnose_account(self, scope, name, result, *, benchmark=None):
        from .research_diagnostics_v1 import diagnose
        path = self.path(name, 'DIAGNOSTIC.json')
        if path.exists():
            diagnostic = _read(path)
            if (diagnostic['result_hash'] != stable_hash(result) or diagnostic['trial_id'] != name
                    or diagnostic['diagnostic_hash'] != stable_hash({k:v for k,v in diagnostic.items() if k!='diagnostic_hash'})):
                raise ValueError('BOUNDED_DIAGNOSTIC_IDENTITY_CONFLICT')
        else:
            diagnostic = diagnose(result, trial_id=name, benchmark=benchmark)
            _put(path, diagnostic)
        if diagnostic['account_state'] != 'COMPLETE':
            self._event(scope, name, 'ENGINEERING_BLOCKED', accessed=True,
                        reasons=(diagnostic['account_state'],))
            raise RuntimeError('BOUNDED_' + diagnostic['account_state'] + ':' + name)
        return diagnostic

    def tick(self, *, loader, invoker):
        """每次处理一个参照或候选；已完成动作只核验恢复，不重新调用模型或账户。"""
        from .bounded_candidate_v1 import validate_candidate, candidate_capabilities, BoundedCandidateAccountBackend
        from scripts.s1_causal_price_strategy_v1 import Causal51VoteStrategy, Causal51AccountBackend
        from .research_diagnostics_v1 import feedback_view
        with self.lock():
            if self.path('STOP.json').exists():
                return self.status()
            scope = self.scope(active=True)
            events = tuple(scope['input_manifest'].get('events', []))
            if (not self.path('REFERENCE', 'RESULT.json').exists()
                    or not self.path('REFERENCE','governance','REFERENCE_SETTLEMENT.json').exists()
                    or not self.path('REFERENCE', 'DIAGNOSTIC.json').exists()):
                self.scope(active=True, action='MATERIALIZE')
                self.scope(active=True, action='BACKTEST')
                strategy = Causal51VoteStrategy()
                strategy.strategy_id = 'REFERENCE'
                # 固定参照的策略ID属于公共声明，原规则不变。
                backend = Causal51AccountBackend(events)
                self._charge(scope, 'REFERENCE')
                if not self.path('REFERENCE', 'CANDIDATE.json').exists():
                    self._freeze(scope, 'REFERENCE', strategy, backend, proposal=None, parent=None)
                result = self._execute(scope, 'REFERENCE', strategy, backend, loader)
                self._diagnose_account(scope, 'REFERENCE', result)
                return self.status()
            reference = _read(self.path('REFERENCE', 'RESULT.json'))
            self._diagnose_account(scope, 'REFERENCE', reference)
            completed = [name for name in self._rounds(scope) if self.path(name, 'DECISION.json').exists()]
            if len(completed) == scope['max_attempts']:
                _put(self.path('STOP.json'), {'reason': 'ATTEMPT_BUDGET_EXHAUSTED', 'qualification': 'NOT_ASSESSED'})
                return self.status()
            name = self._rounds(scope)[len(completed)]
            parent = completed[-1] if completed else None
            self.scope(active=True, action='GENERATE')
            self.scope(active=True, action='MATERIALIZE')
            self.scope(active=True, action='BACKTEST')
            self.scope(active=True, action='FEEDBACK')
            self._charge(scope, name)
            history = [_read(self.path(item, 'CANDIDATE.json'))['proposal'] for item in completed
                       if self.path(item, 'CANDIDATE.json').exists()]
            feedback = [_read(self.path(item, 'FEEDBACK.json')) for item in completed
                        if self.path(item, 'FEEDBACK.json').exists()]
            context = {'capabilities': candidate_capabilities(), 'previous_designs': history,
                       'failure_knowledge': feedback, 'parent_candidate_id': parent,
                       'instruction': '提出一个受支持的新假设或有理由的修订。不得改数据、账户、费用。仅返回候选JSON。'}
            PerformanceBlindGuard.assert_blind(context)
            context_hash = stable_hash(context)
            if not self.path(name, 'PROPOSAL.json').exists():
                if self.path(name, 'MODEL_STARTED.json').exists() and not self.path(name, 'model', 'INVOCATION.json').exists():
                    can_recover = getattr(invoker,'can_recover',lambda **kwargs:False)
                    if not can_recover(staging_dir=self.path(name,'model'),context_hash=context_hash):
                        raise RuntimeError('BOUNDED_INTERRUPTED_MODEL_NO_AUTOMATIC_RETRY')
                _put(self.path(name, 'MODEL_STARTED.json'), {'context_hash': context_hash})
                remaining = int((datetime.fromisoformat(scope['expires_at'])-_now()).total_seconds())
                proposal = invoker.invoke(context, staging_dir=self.path(name, 'model'), timeout_seconds=max(1,min(180,remaining)))
                _put(self.path(name, 'PROPOSAL.json'), {'proposal': proposal, 'context_hash': context_hash})
            recorded = _read(self.path(name, 'PROPOSAL.json'))
            if recorded['context_hash'] != context_hash:
                raise ValueError('BOUNDED_MODEL_CONTEXT_CHANGED')
            proposal = recorded['proposal']
            try:
                strategy = validate_candidate(proposal, strategy_id=name)
                prior_rules = [_read(self.path(item, 'CANDIDATE.json'))['plan']['strategy']['parameters']
                               for item in completed if self.path(item, 'CANDIDATE.json').exists()]
                if any(item.get('rule_identity') == strategy.rule_identity for item in prior_rules):
                    raise ValueError('BOUNDED_DUPLICATE_RULE')
                reference_rule = validate_candidate({'hypothesis':'固定参照','change_reason':'固定参照',
                    'indicators':[item['id'] for item in Causal51VoteStrategy().definition['indicators']],
                    'threshold':26},strategy_id='REFERENCE_RULE')
                if reference_rule.rule_identity == strategy.rule_identity:
                    raise ValueError('BOUNDED_DUPLICATE_REFERENCE_RULE')
            except (ValueError, TypeError, KeyError) as exc:
                code = 'DUPLICATE_RULE' if 'DUPLICATE' in str(exc) else 'UNSUPPORTED_CANDIDATE'
                _put(self.path(name, 'FEEDBACK.json'), {'reason': code, 'instruction': '提出受支持且不同于已有规则的假设。'})
                _put(self.path(name, 'DECISION.json'), {'reason': code, 'qualification': 'NOT_ASSESSED'})
                return self.status()
            backend = BoundedCandidateAccountBackend(events)
            if not self.path(name, 'CANDIDATE.json').exists():
                self._freeze(scope, name, strategy, backend, proposal=proposal, parent=parent)
            result = self._execute(scope, name, strategy, backend, loader)
            self.scope(active=True, action='FEEDBACK')
            diagnostic = self._diagnose_account(scope, name, result, benchmark=reference)
            _put(self.path(name, 'FEEDBACK.json'), feedback_view(diagnostic))
            _put(self.path(name, 'DECISION.json'), {'reason': 'EXPLORATION_RECORDED', 'qualification': 'NOT_ASSESSED'})
            return self.status()

    @staticmethod
    def _rounds(scope):
        return [f'CANDIDATE_{i:03d}' for i in range(1, scope['max_attempts']+1)]

    def status(self):
        scope = self.scope()
        rows = []
        for name in ['REFERENCE', *self._rounds(scope)]:
            if not self.path(name, 'ATTEMPT.json').exists():
                continue
            row = {'candidate_id': name, 'account_completed': self.path(name, 'RESULT.json').exists(),
                   'diagnostic_available': self.path(name, 'DIAGNOSTIC.json').exists()}
            if row['account_completed']:
                _read(self.path(name, 'RESULT.json'))
            if self.path(name, 'CANDIDATE.json').exists():
                record = _read(self.path(name, 'CANDIDATE.json'))
                row.update(parent=record['parent'], proposal=record['proposal'], plan_id=record['plan']['plan_id'])
            if self.path(name, 'DECISION.json').exists():
                row.update(_read(self.path(name, 'DECISION.json')))
            rows.append(row)
        reason = _read(self.path('STOP.json'))['reason'] if self.path('STOP.json').exists() else 'IN_PROGRESS'
        blocked = None if reason != 'IN_PROGRESS' else self._blocked_reason(scope)
        return {'objective_id': scope['objective_id'], 'status': 'BLOCKED' if blocked else reason,
                **({'reason': blocked} if blocked else {}), 'candidates': rows,
                'budget': SearchBudgetRegistryV1(scope['objective_id'], self.path('search_budget_registry.json')).snapshot(),
                'qualification': 'NOT_ASSESSED', 'real_observation_days': 0,
                'real_data': scope['input_manifest']['profile'] == 'S1_MODELED_DAILY'}

    def _blocked_reason(self, scope):
        # 只读投影；已落盘结果或模型成功凭据仍可补齐后续提交，不写永久 STOP。
        if self.path('REVOKED.json').exists():
            _read(self.path('REVOKED.json'))
            return 'BOUNDED_REVOKED'
        if _now() >= datetime.fromisoformat(scope['expires_at']):
            return 'BOUNDED_DEADLINE_REACHED'
        if source_identity() != scope['source_identity']:
            return 'BOUNDED_SOURCE_CHANGED'
        missing = [action for action in ACTIONS if action not in scope['actions']]
        if missing:
            return 'BOUNDED_WAITING_AUTHORIZATION:' + missing[0]
        try:
            self.lock().probe()
            running = False
        except MutationBusyError:
            running = True
        for name in ['REFERENCE', *self._rounds(scope)]:
            if self.path(name, 'RESULT.json').exists():
                from .research_diagnostics_v1 import diagnose
                state = diagnose(_read(self.path(name, 'RESULT.json')), trial_id=name)['account_state']
                if state != 'COMPLETE':
                    return 'BOUNDED_' + state + ':' + name
                continue
            if self.path(name, 'FAILURE.json').exists():
                failure = _read(self.path(name, 'FAILURE.json'))
                return failure.get('reason_code', 'BOUNDED_INTERRUPTED_ACCOUNT_NO_AUTOMATIC_RETRY')
            if self.path(name, 'model', 'ERROR.json').exists():
                _read(self.path(name, 'model', 'ERROR.json'))
                return 'BOUNDED_MODEL_RUNTIME_FAILED'
            if not running and (self.path(name, 'EXECUTION_STARTED.json').exists()
                    or self.path(name, 'governance', name + '_START.json').exists()):
                return 'BOUNDED_INTERRUPTED_ACCOUNT_NO_AUTOMATIC_RETRY'
            if (self.path(name, 'model', 'SUCCESS.json').exists()
                    and not self.path(name, 'PROPOSAL.json').exists()):
                success = _read(self.path(name, 'model', 'SUCCESS.json'))
                try:
                    valid_json = isinstance(json.loads(success['response_text']), dict)
                except (KeyError, TypeError, json.JSONDecodeError):
                    valid_json = False
                if not valid_json:
                    return 'BOUNDED_MODEL_INVALID_JSON'
            if (not running and self.path(name, 'MODEL_STARTED.json').exists()
                    and not any(self.path(name, *parts).exists() for parts in (
                        ('PROPOSAL.json',), ('model', 'INVOCATION.json'), ('model', 'SUCCESS.json')))):
                return 'BOUNDED_INTERRUPTED_MODEL_NO_AUTOMATIC_RETRY'
        return None

    def revoke(self, reason):
        # 控制请求不能争用覆盖模型/账户执行的长任务锁。
        with ObjectiveMutationLock.for_resource(self.path('REVOKED.json')):
            _put(self.path('REVOKED.json'), {'reason': reason})

    def run(self, *, loader, invoker):
        for _ in range(self.scope()['max_attempts'] + 2):
            try:
                status = self.tick(loader=loader, invoker=invoker)
            except (PermissionError, RuntimeError, ValueError) as exc:
                # 可恢复的拒绝不伪装完成；不吞掉详情、不再次执行。
                status = self.status()
                return status if status['status'] == 'BLOCKED' else {**status, 'status': 'BLOCKED', 'reason': str(exc)}
            if status['status'] != 'IN_PROGRESS':
                return status
        return self.status()
