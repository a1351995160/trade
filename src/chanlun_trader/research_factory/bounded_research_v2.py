"""有持仓状态的规则研究循环；复用原预算、试验、执行与恢复，不改旧冻结路径。"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
import re
import math
import json

from .bounded_research_v1 import ACTIONS, BoundedResearchSessionV1, _now, _put, _read, source_identity
from .bounded_candidate_v1 import candidate_capabilities, validate_candidate
from .common import stable_hash
from .context import PerformanceBlindGuard
from .research_rule_strategy_v2 import CAPABILITY
from .mutation_boundary import MutationBusyError


def _seed_feedback(manifest):
    from .research_screening_v1 import REASONS
    records = manifest.get('seed_failure_knowledge', [])
    if not isinstance(records, list) or len(records) > 5:
        raise ValueError('BOUNDED_RULE_SEED_FEEDBACK_INVALID')
    for record in records:
        if (not isinstance(record, dict) or set(record) != {'source_history_hash', 'entries'}
                or not isinstance(record['source_history_hash'], str)
                or not re.fullmatch(r'[0-9a-f]{64}', record['source_history_hash'])
                or not isinstance(record['entries'], list)):
            raise ValueError('BOUNDED_RULE_SEED_FEEDBACK_INVALID')
        for entry in record['entries']:
            if (not isinstance(entry, dict) or set(entry) != {'reason_code', 'high_level_reason'}
                    or entry['reason_code'] not in REASONS
                    or entry['high_level_reason'] != REASONS[entry['reason_code']]):
                raise ValueError('BOUNDED_RULE_SEED_FEEDBACK_INVALID')
    PerformanceBlindGuard.assert_blind(records)
    return deepcopy(records)


def diagnose_rule_result(result, *, trial_id, benchmark=None):
    """直接读取 V2 每日账户证据，不伪造旧引擎 chain。"""
    state, metrics, reasons, observations = 'ACCOUNT_INCOMPLETE', {}, [], []
    try:
        rows, fills = result['daily_accounts'], result['fills']
        initial = result['strategy_plan']['backend']['initial_cash']
        if type(initial) not in (int, float) or not math.isfinite(initial) or initial <= 0:
            raise ValueError('invalid capital')
        dates = [row['date'] for row in rows]
        window = result['strategy_plan']['backend']['window']
        expected_dates = [day for day in window['calendar'] if window['account_start'] <= day <= window['account_end']]
        if (not rows or dates != sorted(set(dates)) or result['status'] not in
                ('HISTORICAL_MODELED_ACCOUNT_COMPLETED', 'OBSERVED_ACCOUNT_COMPLETED')
                or dates != expected_dates or result['reconciliation'] != {'passed': True, 'days': len(rows)}):
            raise ValueError('incomplete reconciliation')
        for row in rows:
            if not all(type(row[key]) in (int, float) and math.isfinite(row[key])
                       for key in ('cash', 'equity', 'cash_difference', 'equity_difference')):
                raise ValueError('invalid numeric evidence')
            if (row['passed'] is not True or abs(row['cash_difference']) > .02
                    or abs(row['equity_difference']) > .02 or row['equity'] <= 0):
                state = 'ACCOUNT_UNRECONCILED'
                raise ValueError('unreconciled account')
        fees = [fill['fee'] for fill in fills]
        if any(type(fee) not in (int, float) or not math.isfinite(fee) or fee < 0 for fee in fees):
            raise ValueError('invalid fees')
        peak, drawdown = initial, 0.
        for row in rows:
            peak = max(peak, row['equity'])
            drawdown = max(drawdown, 1-row['equity']/peak)
        expected = {'net_return': rows[-1]['equity']/initial-1, 'max_drawdown': drawdown,
                    'total_fees': sum(fees), 'trade_count': len(fills)}
        if set(result['metrics']) != set(expected) or any(
                type(result['metrics'][key]) not in (int, float) or not math.isfinite(result['metrics'][key])
                or abs(result['metrics'][key]-value) > 1e-9 for key,value in expected.items()):
            raise ValueError('metrics conflict')
        metrics = {**expected, 'initial_cash': initial, 'ending_equity': rows[-1]['equity'],
                   'account_days': len(rows)}
        state = 'COMPLETE'
        if not fills:
            reasons.append('NO_TRADES')
        if expected['net_return'] < 0:
            reasons.append('EXPLORATORY_LOSS')
            if rows[-1]['equity']-initial+sum(fees) > 0:
                reasons.append('COST_ERASES_ACCOUNT_GAIN')
        if len(rows) < 252 or len(fills) < 30:
            reasons.append('LIMITED_SAMPLE')
        previous = initial
        daily = []
        for row in rows:
            daily.append(row['equity']/previous-1)
            previous = row['equity']
        if len(rows) >= 2 and min(sum(daily[:len(daily)//2]), sum(daily[len(daily)//2:])) <= 0:
            observations.append('NONPOSITIVE_SUBPERIOD_NET_RETURN')
    except (KeyError, TypeError, ValueError, OverflowError):
        reasons, metrics = [state], {}
    reasons.append('INDEPENDENT_CONFIRMATION_REQUIRED')
    comparison = {'status': 'NOT_RUN'}
    if benchmark is not None:
        reference = diagnose_rule_result(benchmark, trial_id='REFERENCE')
        same_days = ([row['date'] for row in result.get('daily_accounts', [])]
                     == [row['date'] for row in benchmark.get('daily_accounts', [])])
        comparable = (state == reference['account_state'] == 'COMPLETE' and same_days
                      and result.get('input_identity') == benchmark.get('input_identity')
                      and result['strategy_plan']['backend'] == benchmark['strategy_plan']['backend'])
        comparison = {'status': 'AVAILABLE' if comparable else 'NOT_COMPARABLE',
                      'source_result_hash': stable_hash(benchmark), 'reference_kind': 'FIXED_RULE_NOT_BUY_HOLD'}
        if comparable:
            comparison['net_return_difference'] = metrics['net_return']-reference['metrics']['net_return']
    report = {'schema_version': 'RESEARCH_RULE_DIAGNOSTICS_V2', 'trial_id': trial_id,
              'result_hash': stable_hash(result), 'account_state': state, 'metrics': metrics,
              'reason_codes': reasons, 'process_observations': observations, 'benchmark': comparison,
              'qualification': 'NOT_ASSESSED', 'execution_authorization_granted': False,
              'evidence_paths': ['daily_accounts', 'fills', 'reconciliation', 'strategy_plan'],
              'limitations': ['历史研究及描述性诊断不能证明策略有效；新规则尚无正式方法适用证据。']}
    return {**report, 'diagnostic_hash': stable_hash(report)}


def rule_feedback_view(diagnostic):
    from .research_diagnostics_v1 import feedback_view
    from .failure_adapter import FailureKnowledgeViewV1
    base = feedback_view(diagnostic)
    entries = list(base['entries'])
    if 'NONPOSITIVE_SUBPERIOD_NET_RETURN' in diagnostic.get('process_observations', []):
        entries.append({'category': 'ROBUSTNESS_FAILURE', 'mechanism': 'ACCOUNT_EXPLORATION',
                        'high_level_reason': '至少一个冻结半段没有取得正净收益，需研究规则的阶段稳定性。',
                        'reason_code': 'NONPOSITIVE_SUBPERIOD_NET_RETURN',
                        'constraints': ['preserve_frozen_research_scope']})
    return FailureKnowledgeViewV1(failure_view_version='1.0.0', entries=tuple(entries),
                                   source_history_hash=diagnostic['diagnostic_hash']).to_dict()


def fixed_reference_payload():
    def comparison(operator):
        return {'op': operator, 'args': [
            {'op': 'field', 'args': ['close'], 'params': {}},
            {'op': 'const', 'args': [], 'params': {'value': 0}}], 'params': {}}
    return {'version': CAPABILITY, 'hypothesis': '固定持仓规则参照，不是买入持有基准或合格策略。',
            'change_reason': '固定参照不参与候选搜索。', 'buy': comparison('gt'),
            'sell': comparison('lt'), 'market_filter': None, 'min_hold_sessions': 0,
            'max_hold_sessions': 252, 'cooldown_sessions': 0, 'target_weight': .5}


class BoundedResearchSessionV2(BoundedResearchSessionV1):
    @classmethod
    def create(cls, root, *, objective_id, input_manifest, approval_statement,
               max_attempts=5, wall_seconds=3600, actions=ACTIONS):
        if not isinstance(objective_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', objective_id):
            raise ValueError('BOUNDED_OBJECTIVE_INVALID')
        if type(max_attempts) is not int or not 1 <= max_attempts <= 5:
            raise ValueError('BOUNDED_ATTEMPT_LIMIT')
        if type(wall_seconds) is not int or not 1 <= wall_seconds <= 7200:
            raise ValueError('BOUNDED_WALL_LIMIT')
        if not isinstance(approval_statement, str) or not approval_statement.strip():
            raise PermissionError('BOUNDED_EXPLICIT_SCOPE_APPROVAL_REQUIRED')
        if set(actions) - set(ACTIONS) or len(actions) != len(set(actions)):
            raise ValueError('BOUNDED_ACTION_SCOPE_INVALID')
        if (not input_manifest.get('input_identity') or input_manifest.get('candidate_capability') != CAPABILITY
                or input_manifest.get('profile') not in ('HISTORICAL_MODELED', 'SYNTHETIC')):
            raise ValueError('BOUNDED_RULE_INPUT_PROFILE_INVALID')
        cls._backend(input_manifest)  # 在创建预算前检查窗口和执行参数。
        _seed_feedback(input_manifest)
        root = Path(root).absolute()
        root.mkdir(parents=True, exist_ok=True)
        service = cls(root)
        with service.lock():
            if service.path('SESSION.json').exists():
                raise PermissionError('BOUNDED_EXISTING_SESSION_RESUME_ONLY')
            if any(p.name != 'SESSION.json.mutation.lock' for p in root.iterdir()):
                raise ValueError('BOUNDED_EMPTY_ROOT_REQUIRED')
            now = _now()
            scope = {'version': 'BOUNDED_RESEARCH_V2', 'objective_id': objective_id,
                     'input_manifest': deepcopy(input_manifest), 'max_attempts': max_attempts,
                     'actions': list(actions), 'recorded_at': now.isoformat(),
                     'expires_at': (now + timedelta(seconds=wall_seconds)).isoformat(),
                     'approval_statement': approval_statement, 'source_identity': source_identity(),
                     'purpose': 'EXPLORATION_ONLY', 'qualification': 'NOT_ASSESSED',
                     'historical_publication': 'MODELED_NOT_VERIFIED',
                     'reference': 'FIXED_RULE_V2_NOT_BUY_HOLD_NOT_SEARCH_CANDIDATE',
                     'reference_payload': fixed_reference_payload()}
            scope['scope_id'] = stable_hash(scope)
            _put(service.path('SESSION.json'), scope)
            service._budget(scope)
        return service

    @staticmethod
    def _backend(manifest):
        from .rule_account_backend_v2 import RuleAccountBackendV2
        return RuleAccountBackendV2(manifest['window'], initial_cash=manifest.get('initial_cash', 1_000_000),
                                    max_positions=manifest.get('max_positions'),
                                    max_symbol_exposure_bps=manifest.get('max_symbol_exposure_bps'))

    def scope(self, *, active=False, action=None):
        scope = super().scope(active=active, action=action)
        if (scope.get('version') != 'BOUNDED_RESEARCH_V2'
                or scope['input_manifest'].get('candidate_capability') != CAPABILITY):
            raise ValueError('BOUNDED_RULE_SESSION_REQUIRED')
        return scope

    def status(self):
        report = super().status()
        report['real_data'] = self.scope()['input_manifest']['profile'] == 'HISTORICAL_MODELED'
        report['candidate_capability'] = CAPABILITY
        report['formal_method_applicability'] = 'NOT_ESTABLISHED_FOR_RULE_V2'
        return report

    def _diagnose_account(self, scope, name, result, *, benchmark=None):
        path = self.path(name, 'DIAGNOSTIC.json')
        diagnostic = diagnose_rule_result(result, trial_id=name, benchmark=benchmark)
        _put(path, diagnostic)
        if diagnostic['account_state'] != 'COMPLETE':
            self._event(scope, name, 'ENGINEERING_BLOCKED', accessed=True, reasons=(diagnostic['account_state'],))
            raise RuntimeError('BOUNDED_' + diagnostic['account_state'] + ':' + name)
        return diagnostic

    def _blocked_reason(self, scope):
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
                state = diagnose_rule_result(_read(self.path(name, 'RESULT.json')), trial_id=name)['account_state']
                if state != 'COMPLETE':
                    return 'BOUNDED_' + state + ':' + name
                continue
            if self.path(name, 'FAILURE.json').exists():
                return _read(self.path(name, 'FAILURE.json')).get('reason_code', 'BOUNDED_INTERRUPTED_ACCOUNT_NO_AUTOMATIC_RETRY')
            if self.path(name, 'model', 'ERROR.json').exists():
                _read(self.path(name, 'model', 'ERROR.json'))
                return 'BOUNDED_MODEL_RUNTIME_FAILED'
            if not running and (self.path(name, 'EXECUTION_STARTED.json').exists()
                    or self.path(name, 'governance', name + '_START.json').exists()):
                return 'BOUNDED_INTERRUPTED_ACCOUNT_NO_AUTOMATIC_RETRY'
            if self.path(name, 'model', 'SUCCESS.json').exists() and not self.path(name, 'PROPOSAL.json').exists():
                try:
                    valid = isinstance(json.loads(_read(self.path(name, 'model', 'SUCCESS.json'))['response_text']), dict)
                except (KeyError, TypeError, json.JSONDecodeError):
                    valid = False
                if not valid:
                    return 'BOUNDED_MODEL_INVALID_JSON'
            if not running and self.path(name, 'MODEL_STARTED.json').exists() and not any(
                    self.path(name, *parts).exists() for parts in
                    [('PROPOSAL.json',), ('model', 'INVOCATION.json'), ('model', 'SUCCESS.json')]):
                return 'BOUNDED_INTERRUPTED_MODEL_NO_AUTOMATIC_RETRY'
        return None

    def tick(self, *, loader, invoker):
        with self.lock():
            if self.path('STOP.json').exists():
                return self.status()
            scope = self.scope(active=True)
            backend = self._backend(scope['input_manifest'])
            if (not self.path('REFERENCE', 'RESULT.json').exists()
                    or not self.path('REFERENCE', 'governance', 'REFERENCE_SETTLEMENT.json').exists()
                    or not self.path('REFERENCE', 'DIAGNOSTIC.json').exists()):
                self.scope(active=True, action='MATERIALIZE')
                self.scope(active=True, action='BACKTEST')
                reference_strategy = validate_candidate(scope['reference_payload'], strategy_id='REFERENCE', capability=CAPABILITY)
                self._charge(scope, 'REFERENCE')
                if not self.path('REFERENCE', 'CANDIDATE.json').exists():
                    self._freeze(scope, 'REFERENCE', reference_strategy, backend, proposal=None, parent=None)
                result = self._execute(scope, 'REFERENCE', reference_strategy, backend, loader)
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
            for action in ACTIONS:
                self.scope(active=True, action=action)
            history = [_read(self.path(item, 'CANDIDATE.json'))['proposal'] for item in completed
                       if self.path(item, 'CANDIDATE.json').exists()]
            feedback = [*_seed_feedback(scope['input_manifest']),
                        *[_read(self.path(item, 'FEEDBACK.json')) for item in completed if self.path(item, 'FEEDBACK.json').exists()]]
            capabilities = candidate_capabilities(capability=CAPABILITY)
            capabilities['execution_scope'] = {key: deepcopy(scope['input_manifest'].get(key))
                                                for key in ('window', 'initial_cash', 'max_positions', 'max_symbol_exposure_bps')}
            # 仅披露证券与边界，不将完整历史日历传给模型。
            capabilities['execution_scope']['window'] = {k: v for k, v in scope['input_manifest']['window'].items() if k != 'calendar'}
            context = {'capabilities': capabilities, 'previous_designs': history,
                       'failure_knowledge': feedback, 'parent_candidate_id': parent,
                       'instruction': '根据费用和分段失败原因修订受支持规则；买卖条件、持有期和冷却期可变，账户和数据不可变。'}
            PerformanceBlindGuard.assert_blind(context)
            context_hash = stable_hash(context)
            if (not self.path(name, 'PROPOSAL.json').exists()
                    and self.path(name, 'MODEL_STARTED.json').exists()
                    and not self.path(name, 'model', 'INVOCATION.json').exists()
                    and not getattr(invoker, 'can_recover', lambda **kwargs: False)(
                        staging_dir=self.path(name, 'model'), context_hash=context_hash)):
                raise RuntimeError('BOUNDED_INTERRUPTED_MODEL_NO_AUTOMATIC_RETRY')
            self._charge(scope, name)
            if not self.path(name, 'PROPOSAL.json').exists():
                _put(self.path(name, 'MODEL_STARTED.json'), {'context_hash': context_hash})
                remaining = int((datetime.fromisoformat(scope['expires_at'])-_now()).total_seconds())
                proposal = invoker.invoke(context, staging_dir=self.path(name, 'model'), timeout_seconds=max(1, min(180, remaining)))
                _put(self.path(name, 'PROPOSAL.json'), {'proposal': proposal, 'context_hash': context_hash})
            recorded = _read(self.path(name, 'PROPOSAL.json'))
            if recorded['context_hash'] != context_hash:
                raise ValueError('BOUNDED_MODEL_CONTEXT_CHANGED')
            proposal = recorded['proposal']
            try:
                strategy = validate_candidate(proposal, strategy_id=name, capability=CAPABILITY)
                prior_rules = [_read(self.path(item, 'CANDIDATE.json'))['plan']['strategy']['parameters']['rule_identity']
                               for item in completed if self.path(item, 'CANDIDATE.json').exists()]
                reference_rule = validate_candidate(scope['reference_payload'], strategy_id='REFERENCE', capability=CAPABILITY)
                if strategy.rule_identity in [*prior_rules, reference_rule.rule_identity]:
                    raise ValueError('BOUNDED_DUPLICATE_RULE')
            except (ValueError, TypeError, KeyError) as exc:
                code = 'DUPLICATE_RULE' if 'DUPLICATE' in str(exc) else 'UNSUPPORTED_CANDIDATE'
                _put(self.path(name, 'FEEDBACK.json'), {'reason': code, 'instruction': '提出受支持且不同于已有规则的假设。'})
                _put(self.path(name, 'DECISION.json'), {'reason': code, 'qualification': 'NOT_ASSESSED'})
                return self.status()
            if not self.path(name, 'CANDIDATE.json').exists():
                self._freeze(scope, name, strategy, backend, proposal=proposal, parent=parent)
            result = self._execute(scope, name, strategy, backend, loader)
            self.scope(active=True, action='FEEDBACK')
            diagnostic = self._diagnose_account(scope, name, result, benchmark=reference)
            _put(self.path(name, 'FEEDBACK.json'), rule_feedback_view(diagnostic))
            _put(self.path(name, 'DECISION.json'), {'reason': 'EXPLORATION_RECORDED', 'qualification': 'NOT_ASSESSED'})
            return self.status()
