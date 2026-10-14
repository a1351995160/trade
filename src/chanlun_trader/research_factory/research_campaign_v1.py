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
from .run_budget import (AutonomousRunBudgetV2, effective_operation_upper_bounds,
                         effective_execution_total_seconds, effective_segment_allowance)

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
        optional = {'execution_profiles', 'engineering_authorization', 'scope_policy', 'storage_limits'}
        if (not isinstance(authorization, dict) or not required <= set(authorization)
                or set(authorization) - required - optional):
            raise ValueError('CAMPAIGN_AUTHORIZATION_FIELDS')
        if 'storage_limits' in authorization and 'scope_policy' not in authorization:
            raise ValueError('CONTINUOUS_STORAGE_SCOPE_REQUIRED')
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
        from .campaign_scope_v1 import validate_scope_policy
        validate_scope_policy(service.root, normalized)
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
        from .campaign_scope_v1 import validate_scope_policy
        validate_scope_policy(self.root, value)
        return value

    def _budget(self, create=False, readonly=False):
        authorization = self._authorization()
        if not create and not self.budget_path.with_name('run_budget_events.jsonl').exists():
            raise BudgetLedgerMismatchError('CAMPAIGN_EVENT_HISTORY_MISSING')
        budget = AutonomousRunBudgetV2(run_id=self.authorization_id, objective_id=authorization['objective_id'],
            path=self.budget_path, policy_hash=stable_hash(authorization), readonly=readonly,
            **{key: authorization[key] for key in LIMIT_KEYS})
        view = budget.campaign_view()
        if not create and view['base_authorization'] != authorization:
            raise BudgetLedgerMismatchError('CAMPAIGN_AUTHORIZATION_EVENT_CONFLICT')
        for operation in (() if readonly else view['operations'].values()):
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
        if view.get('revoked'):
            raise PermissionError('CAMPAIGN_REVOKED')
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
            view = budget.campaign_view()
            return {**view, 'trial_usage': budget.usage_state.to_dict(),
                    'batch_count': budget.batch_count_used,
                    'expired': datetime.now(timezone.utc) >= _timestamp(view['authorization']['expires_at'])}

    def _peek_execution_status(self):
        "只读guard严格核父trial镜像；缺口交专门对账，不隐式补记消费。"
        from .run_budget import trial_budget_identity
        budget = self._budget(readonly=True)
        view = budget.campaign_view()
        usage = budget.usage_state.to_dict()
        for operation in view['operations'].values():
            if operation['kind'] not in ('ACCOUNT', 'DATA'):
                continue
            name = operation['operation_id']
            expected = {'batch_id': operation['batch_id'], 'candidate_id': operation['subject_identity'],
                        'candidate_hash': operation['subject_identity'], 'family_id': operation['kind']}
            identity = trial_budget_identity(run_id=self.authorization_id, trial_id=name,
                batch_id=expected['batch_id'], candidate_id=expected['candidate_id'],
                candidate_hash=expected['candidate_hash'])
            prior = budget._trial_identities.get(name, {})
            if (prior.get('reservation_id') != identity or prior.get('trial_id') != name
                    or any(prior.get(key) != value for key, value in expected.items())):
                raise BudgetLedgerMismatchError('CAMPAIGN_EXECUTION_TRIAL_IDENTITY_CONFLICT')
            used = name in usage['performance_accessed_trial_ids']
            completed = name in usage['completed_trial_ids']
            if ((operation['status'] == 'RESERVED' and name not in usage['reserved_trial_ids'])
                    or (operation['status'] in ('RUNNING', 'UNKNOWN', 'COMPLETED', 'FAILED') and not used)
                    or (operation['status'] in ('COMPLETED', 'FAILED') and not completed)):
                raise BudgetLedgerMismatchError('CAMPAIGN_EXECUTION_TRIAL_MIRROR_REQUIRED')
        return {**view, 'trial_usage': usage, 'batch_count': budget.batch_count_used,
                'expired': datetime.now(timezone.utc) >= _timestamp(view['authorization']['expires_at'])}

    def peek_status(self):
        """只读重放；不修快照、不修镜像、不创建锁文件。"""
        budget = self._budget(readonly=True)
        view = budget.campaign_view()
        return {**view, 'trial_usage': budget.usage_state.to_dict(), 'batch_count': budget.batch_count_used,
                'expired': datetime.now(timezone.utc) >= _timestamp(view['authorization']['expires_at'])}

    def add_grant(self, grant, *, approval_ref):
        from .campaign_scope_v1 import grant_summary, OwnerApprovalStoreV1, replay_grant
        with self._lock():
            budget = self._budget()
            view = budget.campaign_view()
            base = self._authorization()
            if not base.get('scope_policy') or view.get('revoked'):
                raise PermissionError('CONTINUOUS_ACTIVE_SCOPE_REQUIRED')
            summary = grant_summary(base, grant)
            OwnerApprovalStoreV1(base['scope_policy']['approval_store']).require(approval_ref, summary)
            item = {'grant': deepcopy(grant), 'approval_ref': deepcopy(approval_ref)}
            existing = view['grants'].get(grant['grant_id'])
            if existing is not None:
                if existing != item:
                    raise PermissionError('CAMPAIGN_GRANT_IDENTITY_CONFLICT')
                return deepcopy(existing)
            replay_grant(base, view['authorization'], item)
            budget.campaign_event('CAMPAIGN_GRANT_ADDED', item)
            return deepcopy(item)

    def revoke(self, reason):
        if not isinstance(reason, str) or not reason:
            raise ValueError('CAMPAIGN_REVOKE_REASON_REQUIRED')
        with self._lock():
            budget = self._budget()
            if not budget.campaign_view().get('revoked'):
                budget.campaign_event('CAMPAIGN_REVOKED', {'reason': reason})

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
            if view.get('stage_remaining') and any(amount > view['stage_remaining'][stage][name] for name, amount in units.items()):
                raise BudgetExhaustedError('CAMPAIGN_STAGE_RESOURCE_LIMIT')
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

    def install_account_purpose_time_extension(self, manifest_ref):
        """经原 Owner 许可一次原子预留两原用途；不增 grant、用途或总预算。"""
        from .account_purpose_time_extension_v1 import resolve_time_extension_manifest
        with self._lock():
            budget=self._budget(); view=budget.campaign_view()
            resolved=resolve_time_extension_manifest(manifest_ref, root=self.root,
                authorization_id=self.authorization_id, objective_id=view['authorization']['objective_id'],
                for_dispatch=True)
            proof=resolved['proof']
            targets=[view['operations'].get(value, {}) for value in resolved['operation_ids'].values()]
            installed=[operation.get('account_purpose_time_extension') for operation in targets]
            if any(value is not None for value in installed):
                if len(targets) == 2 and all(value == proof for value in installed):
                    return {operation['operation_id']:deepcopy(operation) for operation in targets}
                raise PermissionError('CAMPAIGN_ACCOUNT_TIME_EXTENSION_ALREADY_INSTALLED')
            if view.get('revoked'):
                raise PermissionError('CAMPAIGN_REVOKED')
            if view['stages'].get('EXPLORATION', {}).get('status') == 'WAITING':
                raise PermissionError('CAMPAIGN_STAGE_WAITING')
            # 首装还要求冻结时的实时 CP、控制暂停头和账本边界完全未变。
            verified=resolve_time_extension_manifest(manifest_ref, root=self.root,
                authorization_id=self.authorization_id, objective_id=view['authorization']['objective_id'],
                for_install=True)
            if verified['proof'] != proof:
                raise PermissionError('CAMPAIGN_ACCOUNT_TIME_EXTENSION_PROOF_CONFLICT')
            budget.campaign_event('CAMPAIGN_ACCOUNT_PURPOSE_TIME_EXTENDED',
                {'proof':proof, 'installed_at':datetime.now(timezone.utc).isoformat()})
            current=budget.campaign_view()
            return {value:deepcopy(current['operations'][value]) for value in resolved['operation_ids'].values()}

    def effective_execution_limit(self, operation_id, *, for_dispatch=False):
        """由同一权威事件重放的有效限时；原 profile/hash 不变。"""
        view=self._budget(readonly=True).campaign_view(); operation=view['operations'][operation_id]
        proof=operation.get('account_purpose_time_extension')
        if for_dispatch:
            self._dispatchable(view, operation['stage'])
            if proof is not None:
                from .account_purpose_time_extension_v1 import resolve_time_extension_manifest
                resolved=resolve_time_extension_manifest(proof['manifest_ref'], root=self.root,
                    authorization_id=self.authorization_id, objective_id=view['authorization']['objective_id'],
                    for_dispatch=True)
                if resolved['proof'] != proof:
                    raise PermissionError('CAMPAIGN_ACCOUNT_TIME_EXTENSION_PROOF_CONFLICT')
        return {'profile':deepcopy(operation['execution_profile']),
            'effective_total_seconds':effective_execution_total_seconds(operation),
            'time_extension_proof':deepcopy(proof), 'boundary':deepcopy(operation.get('time_extension_boundary')),
            'parent_segments':deepcopy(operation.get('segments', []))}

    def start_execution_segment(self, operation_id, *, segment_number, profile=None, upper_bound_seconds=None):
        """继续原 operation；不建立第二个 Trial 或第二份账户额度。"""
        from .universe_execution_profile_v1 import validate_execution_profile
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
            proof=operation.get('account_purpose_time_extension')
            if proof is not None:
                from .account_purpose_time_extension_v1 import resolve_time_extension_manifest
                resolved=resolve_time_extension_manifest(proof['manifest_ref'], root=self.root,
                    authorization_id=self.authorization_id, objective_id=view['authorization']['objective_id'],
                    for_dispatch=True)
                if resolved['proof'] != proof:
                    raise PermissionError('CAMPAIGN_ACCOUNT_TIME_EXTENSION_PROOF_CONFLICT')
            now=datetime.now(timezone.utc)
            allowed=effective_segment_allowance(value,operation.get('active_wall_seconds',0),
                effective_total_seconds=effective_execution_total_seconds(operation), time_extension_proof=proof,
                seconds_to_expiry=(_timestamp(view['authorization']['expires_at'])-now).total_seconds())
            upper=allowed if upper_bound_seconds is None else upper_bound_seconds
            if type(upper) not in (int,float) or not 0 < upper <= allowed or (proof is not None and upper <= 70):
                raise PermissionError('CAMPAIGN_EXECUTION_SEGMENT_BOUND_CONFLICT')
            item={'operation_id':operation_id,'segment_number':segment_number,'profile_hash':value['profile_hash'],
                  'upper_bound_seconds':upper,'dispatched_at':now.isoformat()}
            if proof is not None:item['time_extension_proof']=deepcopy(proof)
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
            if active.get('time_extension_proof') is not None:
                item['time_extension_proof']=deepcopy(active['time_extension_proof'])
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
            if operation.get('account_purpose_time_extension') is not None:
                item['time_extension_proof']=deepcopy(operation['account_purpose_time_extension'])
            bounds=effective_operation_upper_bounds(operation)
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
            if any(amount > bounds[name] for name, amount in units.items()):
                segments=operation.get('segments',[])
                if (operation.get('execution_profile') is not None and outcome=='FAILED' and segments
                        and segments[-1].get('outcome')=='FAILED'
                        and segments[-1].get('basis')=='MEASURED_FAILED_RESOURCE_BOUND_VIOLATION'
                        and units['wall_seconds']==max(1,math.ceil(operation['active_wall_seconds']))
                        and all(units[name]<=bounds[name] for name in units if name!='wall_seconds')):
                    item['resource_overrun']={name:max(0,units[name]-bounds[name]) for name in units}
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

    def settle_model_overrun(self, operation_id, *, receipt_path):
        """已收到的模型账单超限仍记真实债务；这不扩大授权也不允许再次调用。"""
        from .campaign_model_receipt_v1 import model_receipt_proof
        path = Path(receipt_path).absolute()
        with self._lock():
            budget = self._budget()
            operations = budget.campaign_view()['operations']
            operation = operations[operation_id]
            usage, digest = model_receipt_proof(self.directory, operation, path, operations)
            actual = self._units({'model_calls': max(1, usage['model_calls']), 'model_tokens': usage['total_tokens'],
                'model_cost_microunits': usage['cost_microunits'], 'wall_seconds': operation['upper_bounds']['wall_seconds']})
            item = {'operation_id': operation_id, 'actual': actual, 'outcome': 'FAILED',
                'evidence_identity': digest, 'usage_receipt': str(path),
                'resource_overrun': {name: max(0, actual[name] - operation['upper_bounds'][name]) for name in actual}}
            if operation['status'] == 'FAILED' and operation.get('usage_receipt') == str(path):
                if any(operation.get(key) != value for key, value in item.items()):
                    raise BudgetLedgerMismatchError('CAMPAIGN_MODEL_OVERRUN_SETTLEMENT_CHANGED')
                return deepcopy(operation)
            if operation['status'] not in ('RUNNING', 'UNKNOWN') or not any(item['resource_overrun'].values()):
                raise PermissionError('CAMPAIGN_MODEL_OVERRUN_STATE_INVALID')
            budget.campaign_event('CAMPAIGN_MODEL_RESOURCE_OVERRUN_SETTLED', item)
            budget.campaign_event('CAMPAIGN_PAUSED', {'reason': 'MODEL_HARD_BUDGET_VIOLATION', 'operation_id': operation_id})
            return deepcopy(budget.campaign_view()['operations'][operation_id])
