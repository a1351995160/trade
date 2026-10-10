"""固定部署的持续研究控制面；不沿用有限任务的 stages/max_calls 完成语义。"""
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import re

from .campaign_scope_v1 import (CampaignScopeV1, OwnerApprovalStoreV1,
                                bind_campaign_scope, grant_summary, scope_summary)
from .common import stable_hash
from .research_campaign_v1 import ResearchCampaignV1


class ContinuousUniverseLifecycleV1:
    def __init__(self, workspace_root, researches, *, approvals, submission_factory, invoker=None, invoker_factory=None,
                 invoker_deployment_identity=None):
        self.root = Path(workspace_root).absolute()
        if self.root.resolve() != self.root or not isinstance(researches, dict):
            raise ValueError('CONTINUOUS_DEPLOYMENT_INVALID')
        if not isinstance(approvals, OwnerApprovalStoreV1):
            raise ValueError('CONTINUOUS_APPROVAL_STORE_REQUIRED')
        self.approvals, self.submission_factory, self.invoker = approvals, submission_factory, invoker
        self.invoker_factory = invoker_factory  # 每个恢复对象独占策略状态，避免跨任务覆盖预算。
        self.invoker_deployment_identity = deepcopy(invoker_deployment_identity or {})
        self.researches = deepcopy(researches)
        fields = {'campaign_root', 'authorization', 'contract', 'stage_limits', 'template', 'model_limits'}
        for name, value in self.researches.items():
            if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', name)
                    or not isinstance(value, dict) or not fields <= set(value)
                    or set(value) - fields - {'candidates_per_batch', 'confirmation', 'final_exploration_template',
                                             'history_references', 'exploration_admission', 'train_projection_deployment'}):
                raise ValueError('CONTINUOUS_RESEARCH_REGISTRATION_INVALID')
            path = Path(value['campaign_root']).absolute()
            if path.resolve() != path or not path.is_relative_to(self.root):
                raise ValueError('CONTINUOUS_CAMPAIGN_OUTSIDE_WORKSPACE')
            value['campaign_root'] = str(path)
            scope_summary(path, value['authorization'], value['contract'], value['stage_limits'])
            if 'final_exploration_template' in value:
                from .final_exploration_queue_v1 import validate_final_exploration_template
                validate_final_exploration_template(value['final_exploration_template'], value['template'], value['contract'])
            if 'history_references' in value:
                references = value['history_references']
                if (not isinstance(references, list) or any(not isinstance(ref, dict)
                        or set(ref) != {'path', 'sha256', 'source', 'metering'}
                        or ref['source'] not in {'SESSION_AI_MANUAL', 'LEGACY_SYSTEM'}
                        or ref['metering'] not in {'REFERENCED_CANONICAL_LEDGER', 'UNKNOWN'} for ref in references)):
                    raise ValueError('CONTINUOUS_HISTORY_REFERENCES_INVALID')
                for ref in references:
                    _pinned_reference({key: ref[key] for key in ('path', 'sha256')})
            if ('exploration_admission' in value) != ('train_projection_deployment' in value):
                raise ValueError('CONTINUOUS_EXPLORATION_ADMISSION_DEPLOYMENT_REQUIRED')
            if 'exploration_admission' in value:
                _pinned_reference(value['exploration_admission'], owner_approval=True)
                _pinned_reference(value['train_projection_deployment'])
            limits = value['model_limits']
            if (not isinstance(limits, dict) or set(limits) != {
                    'timeout_seconds', 'max_tokens', 'max_cost_microunits', 'context_max_bytes'}
                    or any(type(item) is not int or item < 1 for item in limits.values())):
                raise ValueError('CONTINUOUS_MODEL_LIMITS_INVALID')
            if type(value.get('candidates_per_batch', 1)) is not int or value.get('candidates_per_batch', 1) < 1:
                raise ValueError('CONTINUOUS_CANDIDATE_LIMIT_INVALID')
            if 'confirmation' in value:
                confirmation = value['confirmation']
                if not isinstance(confirmation, dict) or set(confirmation) != {'admission'}:
                    raise ValueError('CONTINUOUS_CONFIRMATION_CONFIG_INVALID')
                _pinned_reference(confirmation['admission'], owner_approval=True)

    def _registration(self, research_id):
        if research_id not in self.researches:
            raise ValueError('CONTINUOUS_RESEARCH_NOT_REGISTERED')
        return self.researches[research_id]

    def _invoker(self):
        return self.invoker_factory() if self.invoker_factory is not None else self.invoker

    def _campaign(self, research_id):
        config = self._registration(research_id)
        return ResearchCampaignV1(config['campaign_root'], config['authorization']['authorization_id'])

    def preview(self, research_id, *, grant=None):
        config = self._registration(research_id)
        summary = (scope_summary(config['campaign_root'], config['authorization'], config['contract'], config['stage_limits'])
                   if grant is None else grant_summary(self._campaign(research_id)._authorization(), grant))
        return {'research_id': research_id, 'summary': summary, 'summary_hash': stable_hash(summary),
                'owner_approval_required': True, 'model_configured': self.invoker is not None or self.invoker_factory is not None,
                'model_readiness': 'DEPLOYED_GATEWAY_EVIDENCE_REQUIRED' if self.invoker is not None or self.invoker_factory is not None
                else 'HARD_BUDGET_UNSUPPORTED', 'dispatched_operations': 0,
                'preflight': self._preflight(research_id)}

    def _preflight(self, research_id):
        """公共只读预检；登记范围不证明实际账户日或资料合格。"""
        import os
        import shutil
        import sys
        from .continuous_research_contract_v1 import continuous_research_preflight
        config = self._registration(research_id)
        campaign = self._campaign(research_id)
        view = campaign.peek_status() if campaign.authorization_path.exists() else None
        allowed = bool(view and not (view.get('paused') or view.get('revoked') or view.get('expired')))
        parent = Path(config['campaign_root'])
        while not parent.exists() and parent != parent.parent:
            parent = parent.parent
        storage = parent.is_dir() and os.access(parent, os.R_OK | os.W_OK) and shutil.disk_usage(parent).free > 0
        readiness = {'model': {'available': self.invoker is not None or self.invoker_factory is not None,
                              'hard_budget_enforced': False},
            'authorization': {stage: allowed for stage in ('EXPLORATION', 'CONFIRMATION')},
            'resources': {stage: bool(view and view.get('stage_remaining', {}).get(stage, {}).get('wall_seconds', 0) > 0)
                          for stage in ('EXPLORATION', 'CONFIRMATION')},
            'execution': os.name == 'nt' or sys.platform.startswith('linux'), 'storage': storage}
        result = continuous_research_preflight(config['contract'], readiness=readiness)
        # 没有canonical账户/资格原件时为UNKNOWN，不能把U1默认0解释成已观察到零日。
        for channel in result['channels'].values():
            channel.update(available_account_sessions=None, available_warmup_sessions=None,
                           actual_sessions_evidence='UNKNOWN')
            channel['waiting_reasons'].append('ACTUAL_ACCOUNT_SESSIONS_NOT_PROVEN')
        for gap in result['final_business_gaps']:
            gap.update(reason='ACTUAL_ACCOUNT_SESSIONS_NOT_PROVEN', available=None, shortfall=None)
        try:
            submission = self.submission_factory(CampaignScopeV1(campaign))
            datasets = submission.provider.catalog()['datasets']
            result['registered_data'] = [{key: deepcopy(row.get(key)) for key in
                ('dataset_id', 'metadata_hash', 'start', 'end', 'universe_id')} for row in datasets]
        except (PermissionError, ValueError, OSError, KeyError) as exc:
            result['registered_data'] = []
            result['registration_waiting_reason'] = str(exc)
        result.update(model_hard_budget_evidence='NOT_QUERIED', storage_writable=storage,
            exploration_may_continue_when_public_prerequisites_ready=True, data_content_read=False,
            dispatched_operations=0)
        return result

    def create(self, research_id, *, approval_ref):
        from .diagnosis_research_v4 import DiagnosisResearchV4
        config = self._registration(research_id)
        authorization = bind_campaign_scope(config['campaign_root'], config['authorization'],
            contract=config['contract'], stage_limits=config['stage_limits'], approvals=self.approvals,
            approval_ref=approval_ref)
        campaign = ResearchCampaignV1.create(config['campaign_root'], authorization)
        submission = self.submission_factory(CampaignScopeV1(campaign))
        service = DiagnosisResearchV4.create(campaign, submission, contract=config['contract'],
            template=config['template'], model_limits=config['model_limits'],
            candidates_per_batch=config.get('candidates_per_batch', 1), invoker=self._invoker(),
            deployment_admission_refs={key: deepcopy(config[key]) for key in
                ('exploration_admission', 'train_projection_deployment') if key in config},
            invoker_deployment_identity=self.invoker_deployment_identity,
            **({'final_exploration_template': config['final_exploration_template']}
               if 'final_exploration_template' in config else {}),
            **({'history_references': config['history_references']} if 'history_references' in config else {}))
        self._confirmation(service, submission, config)
        return {'research_id': research_id, **service.status()}

    def _confirmation(self, service, submission, config):
        from .business_validation_protocol_v1 import BusinessValidationProtocolV1
        if 'exploration_admission' in config:
            from .train_projection_admission_v1 import verify_train_projection_admission
            train_reference = deepcopy(config['exploration_admission'])
            projection = deepcopy(config['train_projection_deployment'])
            contract_hash = config['contract']['content_hash']
            route = deepcopy(config['contract']['scope']['data_routes']['EXPLORATION'])
            submission.train_projection_deployment = projection
            submission.exploration_admission = lambda: verify_train_projection_admission(train_reference,
                approvals=self.approvals, expected_contract_hash=contract_hash, expected_route=route,
                provider=submission.provider, expected_projection_deployment=projection)
        reference = config.get('confirmation', {}).get('admission')
        admission = PinnedIndependentAdmissionV1(self.root, reference, approvals=self.approvals,
            submission=submission, protocol_path=service.root / 'business_validation' / 'PROTOCOL.json') if reference is not None else None
        submission.independent_admission = admission
        submission.independent_protocol_path = service.root / 'business_validation' / 'PROTOCOL.json'
        def independent_authority(authorization_ref):
            parent = deepcopy(submission.authority(authorization_ref))
            if (reference is None or authorization_ref != config['template']['authorization_ref']
                    or submission.trusted_data_deployment is None):
                raise PermissionError('CONTINUOUS_INDEPENDENT_AUTHORITY_NOT_CONFIGURED')
            approved = _pinned_json(reference)
            self.approvals.require(reference['approval_ref'], approved)
            route = approved.get('trusted_data_access')
            if (not isinstance(route, dict) or set(route) != {'authorization_ref', 'recipe_version', 'protocol_binding'}
                    or ('trusted_data_access' in parent and parent['trusted_data_access'] != route)
                    or ('trusted_deployment' in parent and parent['trusted_deployment'] != submission.trusted_data_deployment)):
                raise PermissionError('CONTINUOUS_INDEPENDENT_AUTHORITY_CONFLICT')
            parent.update(trusted_data_access=deepcopy(route), trusted_deployment=deepcopy(submission.trusted_data_deployment))
            return parent
        submission.independent_authority = independent_authority
        service.confirmation = BusinessValidationProtocolV1(service, submission_service=submission,
            stage_binder=submission.bind_research_request, data_admission=admission)

    def _service(self, research_id):
        from .diagnosis_research_v4 import DiagnosisResearchV4
        config = self._registration(research_id)
        campaign = self._campaign(research_id)
        authorization = campaign._authorization()
        base = {key: item for key, item in authorization.items() if key not in {'root', 'scope_policy'}}
        if base != config['authorization']:
            raise PermissionError('CONTINUOUS_DEPLOYMENT_AUTHORIZATION_CHANGED')
        policy = authorization['scope_policy']
        expected = scope_summary(config['campaign_root'], config['authorization'], config['contract'], config['stage_limits'])
        if (policy['summary'] != expected or Path(policy['approval_store']).resolve() != self.approvals.directory):
            raise PermissionError('CONTINUOUS_DEPLOYMENT_SCOPE_CHANGED')
        self.approvals.require(policy['approval_ref'], expected)
        service = DiagnosisResearchV4(campaign, self.submission_factory(CampaignScopeV1(campaign)), invoker=self._invoker())
        self._confirmation(service, service.submission, config)
        frozen = service.config()
        for key in ('contract', 'template', 'model_limits'):
            if frozen.get(key) != config[key]:
                raise PermissionError('CONTINUOUS_DEPLOYMENT_RESEARCH_CHANGED:' + key)
        if frozen.get('final_exploration_template') != config.get('final_exploration_template'):
            raise PermissionError('CONTINUOUS_DEPLOYMENT_RESEARCH_CHANGED:final_exploration_template')
        if frozen.get('history_references', []) != config.get('history_references', []):
            raise PermissionError('CONTINUOUS_DEPLOYMENT_RESEARCH_CHANGED:history_references')
        expected_refs = {key: config[key] for key in ('exploration_admission', 'train_projection_deployment') if key in config}
        if frozen.get('deployment_admission_refs', {}) != expected_refs:
            raise PermissionError('CONTINUOUS_DEPLOYMENT_RESEARCH_CHANGED:deployment_admission_refs')
        if frozen.get('invoker_deployment_identity', {}) != self.invoker_deployment_identity:
            raise PermissionError('CONTINUOUS_DEPLOYMENT_RESEARCH_CHANGED:invoker_deployment_identity')
        if frozen.get('candidates_per_batch', 1) != config.get('candidates_per_batch', 1):
            raise PermissionError('CONTINUOUS_DEPLOYMENT_CANDIDATE_LIMIT_CHANGED')
        return service

    def status(self, research_id):
        config = self._registration(research_id)
        campaign = self._campaign(research_id)
        if not campaign.authorization_path.exists():
            return {'research_id': research_id, 'status': 'OWNER_APPROVAL_REQUIRED', 'started': False,
                    'contract': deepcopy(config['contract']), 'model_readiness': 'HARD_BUDGET_UNSUPPORTED'
                    if self.invoker is None and self.invoker_factory is None else 'DEPLOYED_GATEWAY_EVIDENCE_REQUIRED',
                    'dispatched_operations': 0}
        status = self._service(research_id).status()
        budget = campaign.peek_status()
        return {'research_id': research_id, **status,
                'scope_budget': {key: deepcopy(budget.get(key))
                    for key in ('used', 'reserved', 'remaining', 'stage_remaining')},
                'contract': deepcopy(config['contract'])}

    def inspect(self):
        views = {}
        for research_id in self.researches:
            try:
                views[research_id] = self.status(research_id)
            except (ValueError, OSError, KeyError, PermissionError, RuntimeError) as exc:
                views[research_id] = {'status': 'BLOCKED', 'reason': str(exc), 'research_id': research_id}
        return views

    def perform(self, research_id, action, payload=None):
        payload = {} if payload is None else deepcopy(payload)
        allowed = {'create': {'approval_ref'}, 'start': set(), 'advance': set(),
                   'pause': {'reason'}, 'resume': {'reason'}, 'revoke': {'reason'},
                   'grant': {'grant', 'approval_ref'}, 'handover': set()}
        if action not in allowed or not isinstance(payload, dict) or set(payload) != allowed[action]:
            raise ValueError('CONTINUOUS_ACTION_FIELDS_INVALID')
        if action == 'create':
            return self.create(research_id, approval_ref=payload['approval_ref'])
        service = self._service(research_id)
        if action == 'grant':
            result = self._campaign(research_id).add_grant(payload['grant'], approval_ref=payload['approval_ref'])
        elif action == 'handover':
            result = service.handover(design=True)
        elif action in {'pause', 'resume', 'revoke'}:
            if not isinstance(payload['reason'], str) or not payload['reason'].strip():
                raise ValueError('CONTINUOUS_ACTION_REASON_REQUIRED')
            result = getattr(service, action)(payload['reason'])
        else:
            result = getattr(service, action)()
        return {'research_id': research_id, **result}

    def tick(self):
        result = {}
        for research_id in self.researches:
            try:
                status = self.status(research_id)
                # start 为明确的人为操作；仅创建或读取不会让宿主开始派发。
                if status.get('started') is True and status.get('status') not in {
                        'PAUSED', 'REVOKED', 'GOAL_MET', 'BUSINESS_GOAL_MET', 'COMPLETED', 'OWNER_APPROVAL_REQUIRED'}:
                    result[research_id] = self.perform(research_id, 'advance')
                else:
                    result[research_id] = status
            except (ValueError, OSError, KeyError, PermissionError, RuntimeError) as exc:
                result[research_id] = {'status': 'BLOCKED', 'reason': str(exc)}
        return result


def _pinned_reference(reference, *, owner_approval=False):
    fields = {'path', 'sha256'} | ({'approval_ref'} if owner_approval else set())
    if not isinstance(reference, dict) or set(reference) != fields:
        raise ValueError('CONTINUOUS_ARTIFACT_REFERENCE_INVALID')
    path = Path(reference['path'])
    if (not path.is_absolute() or path.resolve() != path
            or not isinstance(reference['sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', reference['sha256'])):
        raise ValueError('CONTINUOUS_ARTIFACT_PIN_INVALID')
    return path


def _pinned_json(reference):
    path = _pinned_reference(reference, owner_approval='approval_ref' in reference)
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != reference['sha256']:
        raise PermissionError('CONTINUOUS_ARTIFACT_IDENTITY_CHANGED')
    return json.loads(raw)


class PinnedIndependentAdmissionV1:
    """advance 组合真实快照、受保护读取 scope 和维护者独立审核原件。

    元数据审核本身不授予独立性。可用路径还需要维护者真实完成先前访问及
    登记资料来源比对，并把完整审核原件通过受保护 Owner 渠道批准。
    """
    def __init__(self, workspace_root, reference, *, approvals, submission, protocol_path):
        _pinned_reference(reference, owner_approval=True)
        self.root, self.reference = Path(workspace_root), deepcopy(reference)
        self.approvals, self.submission = approvals, submission
        self.protocol_path = Path(protocol_path)
        self.last_waiting_reason = None

    def __call__(self, protocol):
        self.last_waiting_reason = None
        try:
            return self._load(protocol)
        except FileNotFoundError:
            self.last_waiting_reason = 'PINNED_INDEPENDENT_ARTIFACT_NOT_DEPLOYED'
            return None

    def _load(self, protocol):
        from .forward_snapshot_v1 import SnapshotStoreV1, universe_snapshot_projection
        value = _pinned_json(self.reference)
        self.approvals.require(self.reference['approval_ref'], value)
        fields = {'schema_version', 'snapshot_store_root', 'snapshot_ids', 'calendar',
                  'request_fields', 'qualification', 'trusted_data_access'}
        if (not isinstance(value, dict) or not fields <= set(value) or set(value) - fields - {'prior_access_review'}
                or value['schema_version'] != 'PINNED_INDEPENDENT_ADMISSION_V1'):
            raise ValueError('CONTINUOUS_ADMISSION_SCHEMA_INVALID')
        request = value['request_fields']
        if not isinstance(request, dict) or set(request) != {'dataset_id', 'feature_start', 'account_start',
                'account_end', 'execution_profile', 'observation_plan', 'universe_id'}:
            raise ValueError('CONTINUOUS_ADMISSION_REQUEST_FIELDS_INVALID')
        route = value['trusted_data_access']
        if not isinstance(route, dict) or set(route) != {'authorization_ref', 'recipe_version', 'protocol_binding'}:
            raise ValueError('CONTINUOUS_ADMISSION_DATA_SCOPE_INVALID')
        binding = route['protocol_binding']
        # 协议身份由已冻结原件提供；data scope 必须绑定同一协议和审核原件字节。
        protocol_raw = self.protocol_path.read_bytes()
        if (json.loads(protocol_raw) != {key: item for key, item in protocol.items() if key != 'frozen_admission'}
                or binding.get('protocol_id') != protocol['protocol_identity']
                or binding.get('protocol_sha256') != hashlib.sha256(protocol_raw).hexdigest()
                or binding.get('independent_evidence_sha256') != value.get('prior_access_review', value['qualification'])['sha256']):
            raise PermissionError('CONTINUOUS_ADMISSION_PROTOCOL_BINDING_CONFLICT')
        scope = self.submission.provider.authorize_independent_scope(request['dataset_id'],
            authorization_ref=route['authorization_ref'], feature_start=request['feature_start'],
            account_end=request['account_end'], recipe_version=route['recipe_version'], protocol_binding=binding)
        catalog = self.submission.provider.catalog(trusted_scope=scope)
        registered = next((row for row in catalog['datasets'] if row['dataset_id'] == request['dataset_id']), None)
        if registered is None:
            raise PermissionError('CONTINUOUS_ADMISSION_DATASET_NOT_REGISTERED')
        store_root = Path(value['snapshot_store_root'])
        if not store_root.is_absolute() or store_root.resolve() != store_root or not store_root.is_relative_to(self.root):
            raise ValueError('CONTINUOUS_SNAPSHOT_STORE_OUTSIDE_WORKSPACE')
        projection = universe_snapshot_projection(SnapshotStoreV1(store_root), value['snapshot_ids'],
            target_symbols=registered['target_symbols'], calendar=value['calendar'],
            feature_start=request['feature_start'], account_start=request['account_start'],
            account_end=request['account_end'], frozen_at=protocol['frozen_at'], profile=protocol['preview']['profile'])
        report = _pinned_json(value['qualification'])
        if (report.get('schema_version') != 'RESEARCH_DATA_QUALIFICATION_V1'
                or report.get('authority') != 'READ_ONLY_METADATA_PROJECTION'
                or report.get('report_hash') != stable_hash({key: item for key, item in report.items() if key != 'report_hash'})):
            raise PermissionError('CONTINUOUS_CANONICAL_QUALIFICATION_REQUIRED')
        rows = [row for row in report['datasets'] if row['dataset_id'] == request['dataset_id']]
        if len(rows) != 1 or rows[0]['metadata'].get('content_hash') != registered['metadata_hash']:
            raise PermissionError('CONTINUOUS_QUALIFICATION_DATASET_CONFLICT')
        # 默认保留元数据服务的明确边界；自报 boolean 不在固定 schema 内。
        admission = {'snapshot_projection': projection, 'metadata': deepcopy(rows[0]['metadata']),
            'request_fields': deepcopy(request), 'source_authenticated': False,
            'prior_access_review_passed': False, 'post_freeze_unseen': False,
            'prior_access_review_identity': report['report_hash'],
            'admission_config_ref': deepcopy(self.reference), 'prior_access_review_ref': None,
            'waiting_reason': 'CANONICAL_INDEPENDENCE_AUTHENTICATION_UNAVAILABLE'}
        if 'prior_access_review' in value:
            reference = value['prior_access_review']
            _pinned_reference(reference, owner_approval=True)
            review = _pinned_json(reference)
            self.approvals.require(reference['approval_ref'], review)
            required = {'schema_version', 'protocol_identity', 'protocol_sha256', 'dataset_id',
                'manifest_sha256', 'snapshot_projection_hash', 'snapshot_refs_identity',
                'qualification_report_hash', 'exposure_records_hash', 'review_method',
                'review_outcome', 'reviewed_at', 'evidence_refs', 'trusted_route'}
            frozen_route = protocol['preview']['contract']['scope']['data_routes']['CONFIRMATION']
            expected = {'schema_version': 'CANONICAL_INDEPENDENT_DATA_REVIEW_V1',
                'protocol_identity': protocol['protocol_identity'],
                'protocol_sha256': hashlib.sha256(protocol_raw).hexdigest(),
                'dataset_id': request['dataset_id'], 'manifest_sha256': registered['metadata_hash'],
                'snapshot_projection_hash': projection['projection_hash'],
                'snapshot_refs_identity': stable_hash(projection['snapshot_refs']),
                'qualification_report_hash': report['report_hash'],
                'exposure_records_hash': report['exposure_records_hash'],
                'trusted_route': frozen_route,
                'review_method': 'OWNER_PRIOR_ACCESS_REVIEW_AND_REGISTERED_SOURCE_PROVENANCE_V1',
                'review_outcome': 'APPROVED_FUTURE_UNSEEN'}
            if (not isinstance(review, dict) or set(review) != required
                    or any(review.get(key) != item for key, item in expected.items())
                    or not isinstance(review.get('trusted_route'), dict)
                    or binding.get('independent_evidence_id') != stable_hash(review)
                    or rows[0]['historical_independence'] != 'UNKNOWN'
                    or rows[0]['metadata_account_ready'] is not True
                    or not projection['ready_for_registered_data_preparation']):
                raise PermissionError('CONTINUOUS_INDEPENDENT_REVIEW_BINDING_CONFLICT')
            from datetime import datetime
            reviewed = datetime.fromisoformat(review['reviewed_at'])
            frozen = datetime.fromisoformat(protocol['frozen_at'])
            if reviewed.tzinfo is None or reviewed < frozen or any(
                    reviewed < datetime.fromisoformat(ref['received_at']) for ref in projection['snapshot_refs']):
                raise PermissionError('CONTINUOUS_INDEPENDENT_REVIEW_TIME_CONFLICT')
            if not isinstance(review['evidence_refs'], list) or not review['evidence_refs']:
                raise PermissionError('CONTINUOUS_SOURCE_PROVENANCE_EVIDENCE_REQUIRED')
            for evidence in review['evidence_refs']:
                path = _pinned_reference(evidence)
                if not path.is_relative_to(self.root) or hashlib.sha256(path.read_bytes()).hexdigest() != evidence['sha256']:
                    raise PermissionError('CONTINUOUS_SOURCE_PROVENANCE_EVIDENCE_CHANGED')
            admission.update(source_authenticated=(projection['source_authentication'] == 'CANONICAL_SNAPSHOT_STORE'
                and projection['profile'] == 'REAL_OBSERVED'),
                prior_access_review_passed=True,
                post_freeze_unseen=all(datetime.fromisoformat(ref['received_at']) > frozen for ref in projection['snapshot_refs']),
                prior_access_review_identity=stable_hash(review), prior_access_review_ref=deepcopy(reference))
            admission.pop('waiting_reason')
            for key in ('route_id', 'producer_identity', 'source_ids', 'universe_hash', 'quality_policy'):
                if key in review['trusted_route']:
                    admission[key] = deepcopy(review['trusted_route'][key])
        return admission
