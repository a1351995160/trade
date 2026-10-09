"""总授权下的分阶段研究队列，所有消费来自 run_budget 的同一事件流。"""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import json
import math
import os
import re

from .budget import BudgetExhaustedError, BudgetLedgerMismatchError
from .common import stable_hash
from .mutation_boundary import ObjectiveMutationLock
from .run_budget import AutonomousRunBudgetV2

STAGES = ('EXPLORATION', 'CONFIRMATION', 'OBSERVATION')
KINDS = {'CANDIDATE': 'candidate_attempts', 'DATA': 'data_experiments', 'ACCOUNT': 'account_jobs',
         'MODEL': 'model_calls', 'VERIFY': 'verification_jobs'}
LIMIT_KEYS = ('max_batches', 'max_total_predictive_trials', 'max_trials_per_batch',
              'max_hypotheses_per_batch', 'max_candidates_per_batch')


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', value):
        raise ValueError('CAMPAIGN_IDENTIFIER_INVALID')
    return value


def _timestamp(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError('CAMPAIGN_TIMEZONE_REQUIRED')
    return result


class ResearchCampaignV1:
    """root 为维护者配置的项目根；外部候选不能自带账本路径。"""

    def __init__(self, root, authorization_id):
        self.root = Path(root).resolve(strict=True)
        self.authorization_id = _identifier(authorization_id)
        self.directory = self.root / 'reports' / 'research_campaigns' / stable_hash(authorization_id)
        self.authorization_path = self.directory / 'authorization.json'
        self.budget_path = self.directory / 'run_budget.json'

    @classmethod
    def create(cls, root, authorization):
        required = {'authorization_id', 'objective_id', 'resource_limits', 'stages', 'expires_at', *LIMIT_KEYS}
        optional = {'execution_profiles', 'engineering_authorization'}
        if (not isinstance(authorization, dict) or not required <= set(authorization)
                or set(authorization) - required - optional):
            raise ValueError('CAMPAIGN_AUTHORIZATION_FIELDS')
        if 'engineering_authorization' in authorization and 'execution_profiles' not in authorization:
            raise ValueError('CAMPAIGN_EXECUTION_PROFILE_AUTHORIZATION_REQUIRED')
        if 'execution_profiles' in authorization:
            from .universe_execution_profile_v1 import validate_execution_profile, CONTINUOUS_PROFILE, ENGINEERING_PURPOSE
            profiles=authorization['execution_profiles']
            if not isinstance(profiles,list) or not profiles:
                raise ValueError('CAMPAIGN_EXECUTION_PROFILES_INVALID')
            values=[validate_execution_profile(profile) for profile in profiles]
            if len({value['profile_hash'] for value in values}) != len(values):
                raise ValueError('CAMPAIGN_EXECUTION_PROFILES_DUPLICATED')
            engineering=authorization.get('engineering_authorization')
            if engineering is not None and engineering != ENGINEERING_PURPOSE:
                raise PermissionError('CAMPAIGN_ENGINEERING_AUTHORIZATION_INVALID')
            if any(value['profile_id'] == CONTINUOUS_PROFILE for value in values) and engineering != ENGINEERING_PURPOSE:
                raise PermissionError('ENGINEERING_CONTINUOUS_REFERENCE_AUTHORIZATION_REQUIRED')
        service = cls(root, authorization['authorization_id'])
        _identifier(authorization['objective_id'])
        if (not isinstance(authorization['stages'], list) or not authorization['stages']
                or len(set(authorization['stages'])) != len(authorization['stages'])
                or not set(authorization['stages']) <= set(STAGES)):
            raise ValueError('CAMPAIGN_STAGES_INVALID')
        for key in LIMIT_KEYS:
            if type(authorization[key]) is not int or authorization[key] < 1:
                raise ValueError('CAMPAIGN_LIMIT_INVALID')
        service._units(authorization['resource_limits'], complete=True)
        _timestamp(authorization['expires_at'])
        normalized = {**deepcopy(authorization), 'root': os.path.normcase(str(service.root))}
        with service._lock():
            if service.authorization_path.exists():
                if service._authorization() != normalized:
                    raise BudgetLedgerMismatchError('CAMPAIGN_AUTHORIZATION_CONFLICT')
                service._budget()
                return service
            service.directory.mkdir(parents=True, exist_ok=True)
            if service.budget_path.exists() or service.budget_path.with_name('run_budget_events.jsonl').exists():
                raise BudgetLedgerMismatchError('CAMPAIGN_ORPHAN_BUDGET')
            service.authorization_path.write_text(json.dumps(normalized, ensure_ascii=False, sort_keys=True), encoding='utf-8')
            budget = service._budget(create=True)
            budget.campaign_event('CAMPAIGN_AUTHORIZED', normalized)
        return service

    def _lock(self):
        return ObjectiveMutationLock(self.root, 'campaign:' + self.authorization_id)

    def _authorization(self):
        if not self.authorization_path.exists():
            raise ValueError('CAMPAIGN_NOT_REGISTERED')
        value = json.loads(self.authorization_path.read_text(encoding='utf-8'))
        if value.get('root') != os.path.normcase(str(self.root)) or value.get('authorization_id') != self.authorization_id:
            raise BudgetLedgerMismatchError('CAMPAIGN_REGISTERED_ROOT_CONFLICT')
        return value

    def _budget(self, create=False):
        authorization = self._authorization()
        if not create and not self.budget_path.with_name('run_budget_events.jsonl').exists():
            raise BudgetLedgerMismatchError('CAMPAIGN_EVENT_HISTORY_MISSING')
        budget = AutonomousRunBudgetV2(run_id=self.authorization_id, objective_id=authorization['objective_id'],
            path=self.budget_path, policy_hash=stable_hash(authorization), **{key: authorization[key] for key in LIMIT_KEYS})
        if not create and budget.campaign_view()['authorization'] != authorization:
            raise BudgetLedgerMismatchError('CAMPAIGN_AUTHORIZATION_EVENT_CONFLICT')
        view = budget.campaign_view()
        for operation in view['operations'].values():
            if operation['kind'] in ('ACCOUNT', 'DATA'):
                budget.reserve_trial(trial_id=operation['operation_id'], batch_id=operation['batch_id'],
                    candidate_id=operation['subject_identity'], candidate_hash=operation['subject_identity'], family_id=operation['kind'])
                if operation['status'] in ('RUNNING', 'UNKNOWN', 'COMPLETED', 'FAILED'):
                    budget.mark_performance_started(operation['operation_id'])
                if operation['status'] in ('COMPLETED', 'FAILED'):
                    budget.complete_trial(operation['operation_id'])
        return budget

    @staticmethod
    def _units(value, complete=False):
        names = set(AutonomousRunBudgetV2.resource_names)
        if not isinstance(value, dict) or set(value) - names or (complete and set(value) != names):
            raise ValueError('CAMPAIGN_RESOURCE_FIELDS')
        if any(type(amount) is not int or amount < 0 for amount in value.values()):
            raise ValueError('CAMPAIGN_RESOURCE_UNITS_INVALID')
        return {name: value.get(name, 0) for name in names}

    @staticmethod
    def _dispatchable(view, stage):
        if view['paused']:
            raise PermissionError('CAMPAIGN_PAUSED')
        if datetime.now(timezone.utc) >= _timestamp(view['authorization']['expires_at']):
            raise BudgetExhaustedError('CAMPAIGN_EXPIRED')
        if stage not in view['authorization']['stages']:
            raise PermissionError('CAMPAIGN_STAGE_NOT_AUTHORIZED')
        if view['stages'].get(stage, {}).get('status') == 'WAITING':
            raise PermissionError('CAMPAIGN_STAGE_WAITING')

    def status(self):
        with self._lock():
            budget = self._budget()
            return {**budget.campaign_view(), 'trial_usage': budget.usage_state.to_dict(),
                    'batch_count': budget.batch_count_used,
                    'expired': datetime.now(timezone.utc) >= _timestamp(self._authorization()['expires_at'])}

    def set_stage(self, stage, status, reason):
        if stage not in STAGES or status not in ('READY', 'WAITING') or not isinstance(reason, str) or not reason:
            raise ValueError('CAMPAIGN_STAGE_STATE_INVALID')
        with self._lock():
            budget = self._budget()
            if stage not in self._authorization()['stages']:
                raise PermissionError('CAMPAIGN_STAGE_NOT_AUTHORIZED')
            budget.campaign_event('CAMPAIGN_STAGE_CHANGED', {'stage': stage, 'status': status, 'reason': reason,
                                  'sequence': len(budget._events)})

    def pause(self, reason):
        self._pause(True, reason)

    def resume(self, reason):
        self._pause(False, reason)

    def _pause(self, paused, reason):
        if not isinstance(reason, str) or not reason:
            raise ValueError('CAMPAIGN_PAUSE_REASON_REQUIRED')
        with self._lock():
            budget = self._budget()
            budget.campaign_event('CAMPAIGN_PAUSED' if paused else 'CAMPAIGN_RESUMED',
                                  {'reason': reason, 'sequence': len(budget._events)})

    def reserve_operation(self, *, operation_id, batch_id, stage, kind, subject_identity, upper_bounds,
                          cost_bound_evidence=None, hypothesis_identity=None, execution_profile=None):
        _identifier(operation_id)
        _identifier(batch_id)
        if kind not in KINDS or not isinstance(subject_identity, str) or not subject_identity:
            raise ValueError('CAMPAIGN_OPERATION_INVALID')
        hypothesis_identity = hypothesis_identity or subject_identity
        if not isinstance(hypothesis_identity, str) or not hypothesis_identity:
            raise ValueError('CAMPAIGN_HYPOTHESIS_IDENTITY_REQUIRED')
        units = self._units(upper_bounds)
        if units[KINDS[kind]] != 1 or units['wall_seconds'] < 1:
            raise ValueError('CAMPAIGN_OPERATION_UPPER_BOUND_REQUIRED')
        if kind == 'MODEL' and (units['model_tokens'] < 1 or units['model_cost_microunits'] < 1
                                or not isinstance(cost_bound_evidence, str) or not cost_bound_evidence):
            raise ValueError('MODEL_ENFORCED_COST_BOUND_EVIDENCE_REQUIRED')
        item = {'operation_id': operation_id, 'batch_id': batch_id, 'stage': stage, 'kind': kind,
                'subject_identity': subject_identity, 'upper_bounds': units, 'cost_bound_evidence': cost_bound_evidence,
                'hypothesis_identity': hypothesis_identity}
        if execution_profile is not None:
            from .universe_execution_profile_v1 import validate_execution_profile
            profile=validate_execution_profile(execution_profile)
            if kind not in ('ACCOUNT','DATA','VERIFY') or units['wall_seconds'] != profile['total_seconds']:
                raise ValueError('CAMPAIGN_EXECUTION_PROFILE_BOUND_CONFLICT')
            purposes={'ACCOUNT':{'RESEARCH_ACCOUNT','ENGINEERING_CONTINUOUS_REFERENCE'},
                      'DATA':{'RESEARCH_PREPARATION'},'VERIFY':{'RESEARCH_VERIFICATION','RESEARCH_REPORT'}}
            if profile['purpose'] not in purposes[kind]:
                raise PermissionError('CAMPAIGN_EXECUTION_PURPOSE_CONFLICT')
            item['execution_profile']=profile
        with self._lock():
            budget = self._budget()
            view = budget.campaign_view()
            if execution_profile is not None and profile not in view['authorization'].get('execution_profiles',[]):
                raise PermissionError('CAMPAIGN_EXECUTION_PROFILE_NOT_AUTHORIZED')
            existing = view['operations'].get(operation_id)
            if existing:
                if any(existing[key] != value for key, value in item.items()):
                    raise BudgetLedgerMismatchError('CAMPAIGN_OPERATION_IDENTITY_CONFLICT')
                return deepcopy(existing)
            self._dispatchable(view, stage)
            if kind in ('ACCOUNT', 'DATA', 'CANDIDATE') and any(
                    old['kind'] == kind and old['subject_identity'] == subject_identity for old in view['operations'].values()):
                raise BudgetLedgerMismatchError('CAMPAIGN_DUPLICATE_SUBJECT')
            if any(amount > view['remaining'][name] for name, amount in units.items()):
                raise BudgetExhaustedError('CAMPAIGN_RESOURCE_LIMIT')
            prior = [old for old in view['operations'].values() if old['batch_id'] == batch_id]
            hypotheses = {old['hypothesis_identity'] for old in prior if old['kind'] == 'CANDIDATE'}
            if kind == 'CANDIDATE' and hypothesis_identity not in hypotheses and len(hypotheses) >= budget.max_hypotheses_per_batch:
                raise BudgetExhaustedError('CAMPAIGN_BATCH_HYPOTHESIS_LIMIT')
            if kind == 'CANDIDATE' and sum(old['kind'] == kind for old in prior) >= budget.max_candidates_per_batch:
                raise BudgetExhaustedError('CAMPAIGN_BATCH_CANDIDATE_LIMIT')
            if kind in ('ACCOUNT', 'DATA'):
                if sum(old['kind'] in ('ACCOUNT', 'DATA') for old in prior) >= budget.max_trials_per_batch:
                    raise BudgetExhaustedError('MAX_TRIALS_PER_BATCH')
                if budget.remaining_predictive_trials < 1:
                    raise BudgetExhaustedError('TOTAL_TRIAL_BUDGET_EXHAUSTED')
            budget.start_batch(batch_id)
            budget.campaign_event('CAMPAIGN_OPERATION_RESERVED', item)
            if kind in ('ACCOUNT', 'DATA'):
                budget.reserve_trial(trial_id=operation_id, batch_id=batch_id, candidate_id=subject_identity,
                                     candidate_hash=subject_identity, family_id=kind)
            return deepcopy(budget.campaign_view()['operations'][operation_id])

    def start_execution_segment(self, operation_id, *, segment_number, profile=None, upper_bound_seconds=None):
        """继续原 operation；不建立第二个 Trial 或第二份账户额度。"""
        from .universe_execution_profile_v1 import validate_execution_profile, segment_allowance
        with self._lock():
            budget=self._budget(); view=budget.campaign_view(); operation=view['operations'][operation_id]
            self._dispatchable(view,operation['stage'])
            frozen=operation.get('execution_profile')
            if frozen is None or operation['status'] != 'RUNNING':
                raise PermissionError('CAMPAIGN_EXECUTION_CONTINUATION_NOT_AUTHORIZED')
            value=validate_execution_profile(frozen)
            if profile is not None and validate_execution_profile(profile) != value:
                raise PermissionError('CAMPAIGN_EXECUTION_PROFILE_CONFLICT')
            segments=operation.get('segments',[])
            if operation.get('active_segment') is not None or (segments and segments[-1].get('outcome') in ('COMPLETED','FAILED')):
                raise PermissionError('CAMPAIGN_EXECUTION_SEGMENT_ALREADY_DISPATCHED_OR_TERMINAL')
            if type(segment_number) is not int or segment_number != len(segments)+1:
                raise PermissionError('CAMPAIGN_EXECUTION_SEGMENT_NUMBER_CONFLICT')
            now=datetime.now(timezone.utc)
            allowed=segment_allowance(value,operation.get('active_wall_seconds',0),
                seconds_to_expiry=(_timestamp(view['authorization']['expires_at'])-now).total_seconds())
            upper=allowed if upper_bound_seconds is None else upper_bound_seconds
            if type(upper) not in (int,float) or not 0 < upper <= allowed:
                raise PermissionError('CAMPAIGN_EXECUTION_SEGMENT_BOUND_CONFLICT')
            item={'operation_id':operation_id,'segment_number':segment_number,'profile_hash':value['profile_hash'],
                  'upper_bound_seconds':upper,'dispatched_at':now.isoformat()}
            budget.campaign_event('CAMPAIGN_EXECUTION_SEGMENT_DISPATCHED',item)
            return deepcopy(budget.campaign_view()['operations'][operation_id]['active_segment'])

    def end_execution_segment(self, operation_id, *, segment_number, seconds=None,
                              evidence_identity=None, outcome='CONTINUE'):
        from .universe_execution_profile_v1 import segment_charge
        if outcome not in ('CONTINUE','PAUSED','COMPLETED','FAILED'):
            raise ValueError('CAMPAIGN_EXECUTION_SEGMENT_OUTCOME_INVALID')
        with self._lock():
            budget=self._budget(); operation=budget.campaign_view()['operations'][operation_id]
            active=operation.get('active_segment')
            if operation['status'] not in ('RUNNING','UNKNOWN') or active is None or active['segment_number'] != segment_number:
                raise PermissionError('CAMPAIGN_EXECUTION_SEGMENT_NOT_ACTIVE')
            amount,basis=segment_charge(seconds,active['upper_bound_seconds'],evidence_identity,outcome=outcome)
            item={'operation_id':operation_id,'segment_number':segment_number,'measured_seconds':seconds,
                  'seconds':amount,'basis':basis,'evidence_identity':evidence_identity,'outcome':outcome,
                  'charged_at':datetime.now(timezone.utc).isoformat()}
            budget.campaign_event('CAMPAIGN_EXECUTION_SEGMENT_CHARGED',item)
            if basis=='MEASURED_FAILED_RESOURCE_BOUND_VIOLATION':
                budget.campaign_event('CAMPAIGN_PAUSED',{'reason':'DISPATCH_RESOURCE_BOUND_VIOLATION',
                    'operation_id':operation_id,'sequence':len(budget._events)})
            return deepcopy(budget.campaign_view()['operations'][operation_id]['segments'][-1])

    def start_operation(self, operation_id):
        with self._lock():
            budget = self._budget()
            view = budget.campaign_view()
            operation = view['operations'][operation_id]
            self._dispatchable(view, operation['stage'])
            if operation['status'] != 'RESERVED':
                raise PermissionError('CAMPAIGN_OPERATION_ALREADY_DISPATCHED')
            budget.campaign_event('CAMPAIGN_OPERATION_STARTED', {'operation_id': operation_id,
                                  'started_at': datetime.now(timezone.utc).isoformat()})
            if operation['kind'] in ('ACCOUNT', 'DATA'):
                budget.reserve_trial(trial_id=operation_id, batch_id=operation['batch_id'],
                    candidate_id=operation['subject_identity'], candidate_hash=operation['subject_identity'], family_id=operation['kind'])
                budget.mark_performance_started(operation_id)
            return deepcopy(budget.campaign_view()['operations'][operation_id])

    def mark_unknown(self, operation_id, reason):
        with self._lock():
            budget = self._budget()
            operation = budget.campaign_view()['operations'][operation_id]
            if operation['status'] not in ('RUNNING', 'UNKNOWN') or not isinstance(reason, str) or not reason:
                raise ValueError('CAMPAIGN_UNKNOWN_STATE_INVALID')
            budget.campaign_event('CAMPAIGN_OPERATION_UNKNOWN', {'operation_id': operation_id, 'reason': reason})

    def settle_operation(self, operation_id, *, actual, outcome, evidence_identity):
        units = self._units(actual)
        if outcome not in ('COMPLETED', 'FAILED') or not isinstance(evidence_identity, str) or not evidence_identity:
            raise ValueError('CAMPAIGN_SETTLEMENT_EVIDENCE_REQUIRED')
        with self._lock():
            budget = self._budget()
            operation = budget.campaign_view()['operations'][operation_id]
            item = {'operation_id': operation_id, 'actual': units, 'outcome': outcome, 'evidence_identity': evidence_identity}
            if operation['status'] in ('COMPLETED', 'FAILED'):
                if any(operation[key] != value for key, value in item.items()):
                    raise BudgetLedgerMismatchError('CAMPAIGN_SETTLEMENT_CONFLICT')
                return deepcopy(operation)
            if operation['status'] not in ('RUNNING', 'UNKNOWN'):
                raise ValueError('CAMPAIGN_OPERATION_NOT_STARTED')
            if operation.get('execution_profile') is not None:
                if operation.get('active_segment') is not None:
                    raise PermissionError('CAMPAIGN_EXECUTION_SEGMENT_NOT_SETTLED')
                if units['wall_seconds'] < operation.get('active_wall_seconds',0):
                    raise ValueError('CAMPAIGN_EXECUTION_ACTIVE_USAGE_CANNOT_BE_ERASED')
            if units[KINDS[operation['kind']]] != 1 or units['wall_seconds'] < 1:
                raise ValueError('CAMPAIGN_CONSUMPTION_CANNOT_BE_ERASED')
            if any(amount > operation['upper_bounds'][name] for name, amount in units.items()):
                segments=operation.get('segments',[])
                if (operation.get('execution_profile') is not None and outcome=='FAILED' and segments
                        and segments[-1].get('outcome')=='FAILED'
                        and segments[-1].get('basis')=='MEASURED_FAILED_RESOURCE_BOUND_VIOLATION'
                        and units['wall_seconds']==max(1,math.ceil(operation['active_wall_seconds']))
                        and all(units[name]<=operation['upper_bounds'][name] for name in units if name!='wall_seconds')):
                    item['resource_overrun']={name:max(0,units[name]-operation['upper_bounds'][name]) for name in units}
                    if operation['kind'] in ('ACCOUNT','DATA'):budget.complete_trial(operation_id)
                    budget.campaign_event('CAMPAIGN_OPERATION_RESOURCE_OVERRUN_SETTLED',item)
                    budget.campaign_event('CAMPAIGN_PAUSED',{'reason':'ACTUAL_RESOURCE_OVERRUN',
                        'operation_id':operation_id,'sequence':len(budget._events)})
                    return deepcopy(budget.campaign_view()['operations'][operation_id])
                budget.campaign_event('CAMPAIGN_OPERATION_UNKNOWN', {'operation_id': operation_id, 'reason': 'REPORTED_USAGE_EXCEEDS_BOUND'})
                budget.campaign_event('CAMPAIGN_PAUSED', {'reason': 'REPORTED_USAGE_EXCEEDS_BOUND', 'sequence': len(budget._events)})
                raise BudgetLedgerMismatchError('CAMPAIGN_USAGE_EXCEEDS_RESERVED_BOUND')
            if operation['kind'] in ('ACCOUNT', 'DATA'):
                budget.complete_trial(operation_id)
            budget.campaign_event('CAMPAIGN_OPERATION_SETTLED', item)
            return deepcopy(budget.campaign_view()['operations'][operation_id])
