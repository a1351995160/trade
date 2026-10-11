"""持续研究的维护者批准边界；执行端只能引用已批准、内容寻址的记录。"""
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re

from .common import stable_hash
from .mutation_boundary import ObjectiveMutationLock

VERSION = 'CONTINUOUS_CAMPAIGN_SCOPE_V1'
STAGES = ('EXPLORATION', 'CONFIRMATION', 'OBSERVATION')
RESOURCES = ('candidate_attempts', 'data_experiments', 'account_jobs', 'model_calls',
             'model_tokens', 'model_cost_microunits', 'verification_jobs', 'wall_seconds')


def _units(value):
    if (not isinstance(value, dict) or set(value) != set(RESOURCES)
            or any(type(item) is not int or item < 0 for item in value.values())):
        raise ValueError('CAMPAIGN_SCOPE_RESOURCE_UNITS_INVALID')
    return deepcopy(value)


def _stage_limits(value, total):
    if not isinstance(value, dict) or not value or set(value) - set(STAGES):
        raise ValueError('CAMPAIGN_SCOPE_STAGES_INVALID')
    checked = {stage: _units(units) for stage, units in value.items()}
    if any(sum(units[name] for units in checked.values()) > total[name] for name in RESOURCES):
        raise ValueError('CAMPAIGN_STAGE_RESERVES_EXCEED_TOTAL')
    return checked


def _expiry(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError('CAMPAIGN_TIMEZONE_REQUIRED')
    return result


def _write_once(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True) + '\n'
    try:
        with path.open('x', encoding='utf-8') as output:
            output.write(encoded)
    except FileExistsError:
        if json.loads(path.read_text(encoding='utf-8')) != value:
            raise PermissionError('OWNER_RECORD_IDENTITY_CONFLICT')


class OwnerApprovalStoreV1:
    """目录必须由部署维护者保护；普通运行服务不持有 owner capability。

    不把客户端的 confirmed、审批文字或文件路径当作批准。这里依赖单机受保护
    文件系统及部署注入的对象能力，不宣称能抵御拥有同一文件系统管理员权限的人。
    """

    def __init__(self, directory, *, owner_capability=None):
        self.directory = Path(directory).resolve()
        self._owner_capability = owner_capability

    def approve(self, summary, *, approver, capability):
        if (self._owner_capability is None or capability is not self._owner_capability
                or not isinstance(approver, str) or not approver):
            raise PermissionError('OWNER_APPROVAL_REQUIRED')
        if not isinstance(summary, dict) or not summary:
            raise ValueError('OWNER_APPROVAL_SUMMARY_REQUIRED')
        record = {'schema_version': 'OWNER_RESEARCH_APPROVAL_V1', 'summary': deepcopy(summary),
                  'summary_hash': stable_hash(summary), 'approver': approver,
                  'store_root': os.path.normcase(str(self.directory))}
        record['approval_id'] = stable_hash(record)
        with ObjectiveMutationLock.for_resource(self.directory / 'approvals'):
            _write_once(self.directory / (record['approval_id'] + '.json'), record)
        return {'approval_id': record['approval_id'], 'summary_hash': record['summary_hash']}

    def require(self, reference, summary):
        if (not isinstance(reference, dict) or set(reference) != {'approval_id', 'summary_hash'}
                or not re.fullmatch(r'[a-f0-9]{64}', str(reference['approval_id']))
                or reference['summary_hash'] != stable_hash(summary)):
            raise PermissionError('OWNER_APPROVAL_BINDING_INVALID')
        path = self.directory / (reference['approval_id'] + '.json')
        if not path.is_file() or path.is_symlink():
            raise PermissionError('OWNER_APPROVAL_NOT_REGISTERED')
        record = json.loads(path.read_text(encoding='utf-8'))
        if (record.get('approval_id') != reference['approval_id']
                or stable_hash({key: item for key, item in record.items() if key != 'approval_id'}) != reference['approval_id']
                or record.get('summary') != summary or record.get('summary_hash') != stable_hash(summary)
                or record.get('store_root') != os.path.normcase(str(self.directory))):
            raise PermissionError('OWNER_APPROVAL_IDENTITY_CONFLICT')
        return deepcopy(record)


def scope_summary(root, authorization, contract, stage_limits, *, capabilities_snapshot=None):
    from .continuous_research_contract_v1 import validate_continuous_research_contract
    contract = validate_continuous_research_contract(contract, capabilities_snapshot=capabilities_snapshot)
    value = deepcopy(authorization)
    value.pop('scope_policy', None)
    value.pop('root', None)
    if value['objective_id'] != contract['objective_id']:
        raise ValueError('CAMPAIGN_OBJECTIVE_SCOPE_CONFLICT')
    total = _units(value['resource_limits'])
    limits = _stage_limits(stage_limits, total)
    if set(limits) != set(value['stages']):
        raise ValueError('CAMPAIGN_SCOPE_STAGE_LIMITS_REQUIRED')
    if value.get('execution_profiles') != contract['scope']['execution_profiles']:
        raise ValueError('CAMPAIGN_SCOPE_EXECUTION_PROFILES_CONFLICT')
    storage = value.get('storage_limits', {'maximum_artifact_bytes': 17179869184,
                                         'minimum_free_bytes': 2147483648})
    if (not isinstance(storage, dict) or set(storage) != {'maximum_artifact_bytes', 'minimum_free_bytes'}
            or any(type(amount) is not int or amount < 1 for amount in storage.values())):
        raise ValueError('CAMPAIGN_STORAGE_LIMITS_REQUIRED')
    return {'kind': 'CONTINUOUS_RESEARCH_TOTAL_GRANT', 'version': VERSION,
            'root': os.path.normcase(str(Path(root).resolve(strict=True))),
            'authorization': value, 'contract': contract, 'stage_limits': limits, 'storage_limits': deepcopy(storage)}


def bind_campaign_scope(root, authorization, *, contract, stage_limits, approvals, approval_ref):
    summary = scope_summary(root, authorization, contract, stage_limits)
    approvals.require(approval_ref, summary)
    policy = {'schema_version': VERSION, 'summary': summary,
              'approval_store': str(approvals.directory), 'approval_ref': deepcopy(approval_ref)}
    policy['scope_hash'] = stable_hash(policy)
    return {**deepcopy(authorization), 'scope_policy': policy}


def validate_scope_policy(root, authorization):
    policy = authorization.get('scope_policy')
    if policy is None:
        return None
    if (policy.get('schema_version') != VERSION
            or policy.get('scope_hash') != stable_hash({key: item for key, item in policy.items() if key != 'scope_hash'})):
        raise PermissionError('CAMPAIGN_SCOPE_IDENTITY_CONFLICT')
    try:
        summary = scope_summary(root, authorization, policy['summary']['contract'], policy['summary']['stage_limits'])
    except ValueError as exc:
        if str(exc) != 'CONTINUOUS_CAPABILITIES_CHANGED_NEW_VERSION_REQUIRED':
            raise
        from .report_repair_capability_bridge_v1 import ReportRepairCapabilityBridgeV1
        bridge = ReportRepairCapabilityBridgeV1.read(root, authorization)
        summary = scope_summary(root, authorization, policy['summary']['contract'], policy['summary']['stage_limits'],
            capabilities_snapshot=bridge['capabilities_snapshot'])
    if policy['summary'] != summary:
        raise PermissionError('CAMPAIGN_SCOPE_AUTHORIZATION_CONFLICT')
    OwnerApprovalStoreV1(policy['approval_store']).require(policy['approval_ref'], summary)
    return deepcopy(policy)


def grant_summary(base, grant):
    """增量不重写原合同/用途；只追加明确批准的有限资源和期限。"""
    fields = {'grant_id', 'base_authorization_hash', 'resource_limits_delta', 'stage_limits_delta',
              'max_batches_delta', 'max_total_predictive_trials_delta', 'expires_at'}
    if not isinstance(grant, dict) or set(grant) != fields:
        raise ValueError('CAMPAIGN_GRANT_FIELDS_INVALID')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', str(grant['grant_id'])):
        raise ValueError('CAMPAIGN_GRANT_ID_INVALID')
    if grant['base_authorization_hash'] != stable_hash(base):
        raise PermissionError('CAMPAIGN_GRANT_PARENT_CONFLICT')
    delta = _units(grant['resource_limits_delta'])
    stage_delta = _stage_limits(grant['stage_limits_delta'], delta)
    if set(stage_delta) != set(base['stages']):
        raise ValueError('CAMPAIGN_GRANT_STAGE_CONFLICT')
    if any(type(grant[key]) is not int or grant[key] < 0
           for key in ('max_batches_delta', 'max_total_predictive_trials_delta')):
        raise ValueError('CAMPAIGN_GRANT_LIMIT_INVALID')
    _expiry(grant['expires_at'])
    return {'kind': 'CONTINUOUS_RESEARCH_INCREMENT', 'grant': deepcopy(grant),
            'original_scope_hash': base['scope_policy']['scope_hash'], 'root': base['root']}


def replay_grant(base, effective, item):
    grant = item['grant']
    summary = grant_summary(base, grant)
    policy = base['scope_policy']
    OwnerApprovalStoreV1(policy['approval_store']).require(item['approval_ref'], summary)
    if _expiry(grant['expires_at']) < _expiry(effective['expires_at']):
        raise PermissionError('CAMPAIGN_GRANT_EXPIRY_REGRESSION')
    result = deepcopy(effective)
    for name, value in grant['resource_limits_delta'].items():
        result['resource_limits'][name] += value
    for name in ('max_batches', 'max_total_predictive_trials'):
        result[name] += grant[name + '_delta']
    result['expires_at'] = grant['expires_at']
    for stage, units in grant['stage_limits_delta'].items():
        for name, value in units.items():
            result['scope_policy']['summary']['stage_limits'][stage][name] += value
    return result


def stage_remaining(view):
    policy = view['authorization'].get('scope_policy')
    if not policy:
        return None
    result = deepcopy(policy['summary']['stage_limits'])
    for operation in view['operations'].values():
        for name, amount in operation.get('actual' if operation['status'] in ('COMPLETED', 'FAILED') else 'upper_bounds', {}).items():
            result[operation['stage']][name] -= amount
    return result


class CampaignScopeV1:
    def __init__(self, campaign):
        self.campaign = campaign

    def delegate(self, *, batch_id, candidate_identity, phase, request, expires_at):
        """服务端产生委托引用；账户输入/规则仍由公共入口冻结并再核对。"""
        from .research_campaign_v1 import _identifier
        _identifier(batch_id)
        if not re.fullmatch(r'[a-f0-9]{64}', str(candidate_identity)):
            raise ValueError('CAMPAIGN_CANDIDATE_IDENTITY_INVALID')
        with self.campaign._lock():
            budget = self.campaign._budget()
            view = budget.campaign_view()
            self.campaign._dispatchable(view, phase)
            base = self.campaign._authorization()
            policy = validate_scope_policy(self.campaign.root, base)
            if policy is None:
                raise PermissionError('CONTINUOUS_CAMPAIGN_SCOPE_REQUIRED')
            scope = policy['summary']['contract']['scope']
            if phase not in ('EXPLORATION', 'CONFIRMATION'):
                raise PermissionError('CAMPAIGN_DELEGATION_PHASE_INVALID')
            route = scope['data_routes'][phase]
            if not route:
                raise PermissionError('CAMPAIGN_DATA_ROUTE_NOT_READY')
            if (request.get('initial_cash') != scope['initial_cash']
                    or request.get('max_positions') != scope['max_positions']
                    or request.get('universe_id') != scope['universe_id']
                    or request.get('universe_hash') != scope['universe_hash']
                    or request.get('rule_version') != scope['rule_version']
                    or request.get('phase') != phase
                    or request.get('cost_profiles') != scope['cost_profiles']
                    or not route['start'] <= request.get('feature_start', '') <= request.get('account_start', '') <= request.get('account_end', '') <= route['end']
                    or request.get('execution_profiles') != scope['execution_profiles']
                    or ('dataset_id' in route and (request.get('dataset_id') != route['dataset_id']
                        or request.get('dataset_hash') != route['dataset_hash']))):
                raise PermissionError('CAMPAIGN_CHILD_SCOPE_CONFLICT')
            if phase == 'CONFIRMATION' or 'dataset_id' not in route:
                self._future_dataset(policy, request, candidate_identity)
            if not datetime.now(timezone.utc) < _expiry(expires_at) <= _expiry(view['authorization']['expires_at']):
                raise PermissionError('CAMPAIGN_CHILD_EXPIRY_CONFLICT')
            binding = {'schema_version': 'CONTINUOUS_RESEARCH_BINDING_V1',
                'campaign_root': str(self.campaign.root), 'authorization_id': self.campaign.authorization_id,
                'scope_hash': policy['scope_hash'], 'batch_id': batch_id,
                'candidate_identity': candidate_identity, 'phase': phase,
                'request': deepcopy(request), 'expires_at': expires_at}
            binding['binding_id'] = stable_hash(binding)
            path = self.campaign.directory / 'delegations' / (binding['binding_id'] + '.json')
            _write_once(path, binding)
            return {'binding_id': binding['binding_id']}

    def _future_dataset(self, policy, request, candidate_identity):
        from .exploration_governance import read_json
        from .research_data_provider_v1 import day
        import hashlib
        def iso(value):
            normalized = str(day(value))
            return normalized[:4] + '-' + normalized[4:6] + '-' + normalized[6:]
        if request['phase'] == 'EXPLORATION':
            return self._future_train_dataset(policy, request)
        root = self.campaign.directory / 'diagnosis_v4' / 'business_validation'
        protocol_path, admission_path = root / 'PROTOCOL.json', root / 'ADMISSION.json'
        proof = request.get('data_route_proof', {})
        if (set(proof) != {'protocol_sha256', 'admission_sha256'}
                or any(path.resolve() != path or not path.is_file() for path in (protocol_path, admission_path))
                or hashlib.sha256(protocol_path.read_bytes()).hexdigest() != proof['protocol_sha256']
                or hashlib.sha256(admission_path.read_bytes()).hexdigest() != proof['admission_sha256']):
            raise PermissionError('CAMPAIGN_FUTURE_DATA_PROOF_INVALID')
        protocol, admission = read_json(protocol_path), read_json(admission_path)
        preview = protocol['preview']
        route = policy['summary']['contract']['scope']['data_routes'][request['phase']]
        if (request['phase'] != 'CONFIRMATION' or preview['contract'] != policy['summary']['contract']
                or candidate_identity not in [preview['rules'][name] for name in preview['selected']]
                or ('dataset_id' not in route and any(admission.get(key) != route[key] for key in
                    ('route_id', 'producer_identity', 'source_ids', 'universe_hash', 'quality_policy')))
                or admission.get('metadata', {}).get('dataset_id') != request['dataset_id']
                or admission['metadata'].get('content_hash') != request['dataset_hash']):
            raise PermissionError('CAMPAIGN_FUTURE_DATA_ROUTE_CONFLICT')
        approvals = OwnerApprovalStoreV1(policy['approval_store'])
        for key in ('admission_config_ref', 'prior_access_review_ref'):
            reference = admission.get(key)
            if not isinstance(reference, dict) or set(reference) != {'path', 'sha256', 'approval_ref'}:
                raise PermissionError('CAMPAIGN_FUTURE_DATA_OWNER_PROOF_REQUIRED')
            path = Path(reference['path']).absolute()
            if path.resolve() != path or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != reference['sha256']:
                raise PermissionError('CAMPAIGN_FUTURE_DATA_OWNER_PROOF_CHANGED')
            value = read_json(path)
            approvals.require(reference['approval_ref'], value)
            if key == 'admission_config_ref' and (
                    value.get('schema_version') != 'PINNED_INDEPENDENT_ADMISSION_V1'
                    or value.get('request_fields', {}).get('dataset_id') != request['dataset_id']
                    or value.get('request_fields', {}).get('universe_id') != request['universe_id']
                    or any(iso(value['request_fields'].get(key)) != request.get(key) for key in
                           ('feature_start', 'account_start', 'account_end'))
                    or not isinstance(value.get('trusted_data_access'), dict)
                    or not value.get('snapshot_ids') or not value.get('snapshot_store_root')):
                raise PermissionError('CAMPAIGN_FUTURE_ADMISSION_CONFIG_CONFLICT')
            if key == 'prior_access_review_ref' and (value.get('trusted_route') != route
                    or value.get('dataset_id') != request['dataset_id']
                    or value.get('manifest_sha256') != request['dataset_hash']
                    or value.get('protocol_identity') != protocol['protocol_identity']
                    or value.get('protocol_sha256') != proof['protocol_sha256']):
                raise PermissionError('CAMPAIGN_FUTURE_DATA_REVIEW_CONFLICT')

    def _future_train_dataset(self, policy, request):
        import hashlib
        from .exploration_governance import read_json
        proof = request.get('data_route_proof', {})
        reference = proof.get('admission_reference', {})
        if (proof.get('schema_version') != 'VERIFIED_TRAIN_PROJECTION_ADMISSION_V1'
                or not isinstance(reference, dict) or set(reference) != {'path', 'sha256', 'approval_ref'}):
            raise PermissionError('CAMPAIGN_TRAIN_PROJECTION_PROOF_REQUIRED')
        path = Path(reference['path']).absolute()
        if path.resolve() != path or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != reference['sha256']:
            raise PermissionError('CAMPAIGN_TRAIN_PROJECTION_PROOF_CHANGED')
        value = read_json(path)
        OwnerApprovalStoreV1(policy['approval_store']).require(reference['approval_ref'], value)
        contract = policy['summary']['contract']
        if (value.get('schema_version') != 'OWNER_TRAIN_PROJECTION_ADMISSION_V1'
                or value.get('research_contract_hash') != contract['content_hash']
                or value.get('trusted_route') != contract['scope']['data_routes']['EXPLORATION']
                or (value.get('dataset_id'), value.get('manifest_sha256')) != (request['dataset_id'], request['dataset_hash'])
                or not value.get('window', {}).get('feature_start', '') <= request['feature_start']
                    <= request['account_start'] <= request['account_end'] <= value.get('window', {}).get('account_end', '')
                or request['account_start'] < value.get('window', {}).get('account_start', '')
                or proof.get('dataset_id') != request['dataset_id'] or proof.get('dataset_hash') != request['dataset_hash']
                or proof.get('independent_confirmation_eligible') is not False):
            raise PermissionError('CAMPAIGN_TRAIN_PROJECTION_ROUTE_CONFLICT')

    def resolve(self, reference, *, for_dispatch=True):
        if (not isinstance(reference, dict) or set(reference) != {'binding_id'}
                or not re.fullmatch(r'[a-f0-9]{64}', str(reference['binding_id']))):
            raise PermissionError('CAMPAIGN_BINDING_REFERENCE_INVALID')
        path = self.campaign.directory / 'delegations' / (reference['binding_id'] + '.json')
        if not path.is_file() or path.is_symlink():
            raise PermissionError('CAMPAIGN_BINDING_NOT_REGISTERED')
        value = json.loads(path.read_text(encoding='utf-8'))
        if (value['binding_id'] != stable_hash({key: item for key, item in value.items() if key != 'binding_id'})
                or value['authorization_id'] != self.campaign.authorization_id
                or Path(value['campaign_root']).resolve() != self.campaign.root):
            raise PermissionError('CAMPAIGN_BINDING_IDENTITY_CONFLICT')
        view = self.campaign.peek_status()
        if for_dispatch:
            self.campaign._dispatchable(view, value['phase'])
        if for_dispatch and datetime.now(timezone.utc) >= _expiry(value['expires_at']):
            raise PermissionError('CAMPAIGN_CHILD_EXPIRED')
        if value['scope_hash'] != view['base_authorization']['scope_policy']['scope_hash']:
            raise PermissionError('CAMPAIGN_BINDING_SCOPE_CHANGED')
        return deepcopy(value)
