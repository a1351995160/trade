"""CLI 与 UI 共用固定历史数据装配；不从配置加载代码。"""
from pathlib import Path
from copy import deepcopy


def qualified_research_loader(root):
    root = Path(root).absolute()
    def qualified_loader(manifest, strategy):
        from scripts.run_historical_process_research_v1 import build_bundle
        from chanlun_trader.research_factory.rule_account_backend_v2 import rule_input_identity, rule_window
        data_root = Path(manifest['data_root']).absolute()
        if (data_root.resolve() != data_root or '..' in data_root.parts or not data_root.is_relative_to(root)
                or manifest.get('profile') != 'HISTORICAL_MODELED'
                or manifest.get('candidate_capability') != 'RESEARCH_RULE_STRATEGY_V2'):
            raise ValueError('LIFECYCLE_QUALIFIED_DATA_SCOPE_INVALID')
        window, bundle, _ = build_bundle(data_root, symbols=manifest['window']['symbols'])
        identity = rule_input_identity(bundle, window)
        if rule_window(window) != rule_window(manifest['window']) or identity != manifest['input_identity']:
            raise ValueError('LIFECYCLE_QUALIFIED_INPUT_CHANGED')
        bundle['input_identity'] = identity
        return bundle, tuple(bundle['events'])
    return qualified_loader


def build_submission_service(workspace_root, config, *, continuous_scope=None):
    """维护者登记的数据目录与既有数据授权引用；HTTP 请求不能提供此配置。

    config={roots,datasets,authorizations,output_root}；authorizations 每项为
    {path,sha256}，引用人工已登记的不可变授权文件。此处不签发账户批准。
    """
    from datetime import datetime, timezone
    import hashlib
    import json
    import uuid
    from chanlun_trader.research_factory.exploration_governance import immutable
    from chanlun_trader.research_factory.research_data_provider_v1 import ResearchDataProviderV1
    from chanlun_trader.research_factory.strategy_submission_v1 import StrategySubmissionV1
    root = Path(workspace_root).absolute()
    if (root.resolve() != root or not isinstance(config,dict)
            or set(config) - {'roots','datasets','authorizations','output_root','version','trusted_data_deployment'}
            or not {'roots','datasets','authorizations','output_root'} <= set(config)
            or config.get('version') not in (None, 'FULL_UNIVERSE_DEPLOYMENT_V1')):
        raise ValueError('SUBMISSION_DEPLOYMENT_CONFIG_INVALID')
    def internal(value):
        path = Path(value)
        if not path.is_absolute():
            path = root / path
        if path.resolve() != path or not path.is_relative_to(root):
            raise ValueError('SUBMISSION_DEPLOYMENT_PATH_OUTSIDE_WORKSPACE')
        return path
    output = internal(config['output_root'])
    registrations = config['authorizations']
    if not isinstance(registrations,dict):
        raise ValueError('SUBMISSION_REGISTERED_AUTHORITIES_REQUIRED')
    for value in registrations.values():
        if not isinstance(value,dict) or set(value) != {'path','sha256'}:
            raise ValueError('SUBMISSION_AUTHORITY_REFERENCE_INVALID')
        internal(value['path'])
    def resolve_authority(reference):
        if reference not in registrations:
            raise PermissionError('SUBMISSION_AUTHORITY_NOT_REGISTERED')
        registered = registrations[reference]
        raw = internal(registered['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != registered['sha256']:
            raise PermissionError('SUBMISSION_REGISTERED_AUTHORITY_CHANGED')
        value = json.loads(raw)
        required = {'objective_id','budget_path','data_authorization','source','expires_at'}
        if not required <= set(value) or set(value) - required - {
                'account_authorization','execution_profiles','compute_authorization','engineering_authorization',
                'trusted_deployment','trusted_data_access'}:
            raise PermissionError('SUBMISSION_AUTHORITY_FIELDS_INVALID')
        if ('trusted_deployment' in value or 'trusted_data_access' in value):
            if (not {'trusted_deployment', 'trusted_data_access'} <= set(value)
                    or value['trusted_deployment'] != config.get('trusted_data_deployment')):
                raise PermissionError('SUBMISSION_TRUSTED_DATA_DEPLOYMENT_CONFLICT')
            route = value['trusted_data_access']
            if not isinstance(route, dict) or set(route) != {'authorization_ref', 'recipe_version', 'protocol_binding'}:
                raise PermissionError('SUBMISSION_TRUSTED_DATA_ROUTE_INVALID')
        source = value['source']
        if (source.get('origin') != 'USER_EXPLICIT_CURRENT_TASK' or not source.get('statement')):
            raise PermissionError('SUBMISSION_EXISTING_USER_AUTHORITY_REQUIRED')
        expiry = datetime.fromisoformat(value['expires_at'])
        if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
            raise PermissionError('SUBMISSION_AUTHORITY_EXPIRED')
        value['budget_path'] = str(internal(value['budget_path']))
        permit = value.get('account_authorization', {})
        if 'campaign_ref' in permit:
            reference = permit['campaign_ref']
            if not isinstance(reference,dict) or set(reference)!={'root','authorization_id'}:
                raise PermissionError('SUBMISSION_CAMPAIGN_REFERENCE_INVALID')
            reference['root'] = str(internal(reference['root']))
        return value
    def record_access(event):
        record = {**event,'recorded_at':datetime.now(timezone.utc).isoformat()}
        immutable(output / 'data-access' / (uuid.uuid4().hex + '.json'),record)
    if config.get('version') == 'FULL_UNIVERSE_DEPLOYMENT_V1':
        from chanlun_trader.research_factory.universe_data_provider_v1 import UniverseDataProviderV1
        from chanlun_trader.research.guard import configured_access_authority
        roots = {key: str(internal(value)) for key, value in config['roots'].items()}
        deployment = config.get('trusted_data_deployment')
        trusted_authority = None
        if deployment is not None:
            if not isinstance(deployment, dict) or set(deployment) != {'path', 'sha256'}:
                raise ValueError('TRUSTED_DATA_DEPLOYMENT_REFERENCE_INVALID')
            path = Path(deployment['path'])
            if not path.is_absolute() or path.resolve() != path:
                raise ValueError('TRUSTED_DATA_DEPLOYMENT_PATH_INVALID')
            trusted_authority = configured_access_authority(path, expected_sha256=deployment['sha256'])
        provider = UniverseDataProviderV1(roots, record_access, trusted_access_authority=trusted_authority)
    else:
        if config.get('trusted_data_deployment') is not None:
            raise ValueError('TRUSTED_DATA_DEPLOYMENT_REQUIRES_UNIVERSE_PROVIDER')
        provider = ResearchDataProviderV1(config['roots'],record_access)
    if not isinstance(config['datasets'],list):
        raise ValueError('SUBMISSION_DATASETS_REQUIRED')
    for item in config['datasets']:
        if not isinstance(item,dict) or set(item) != {'dataset_id','root_id','manifest_path'}:
            raise ValueError('SUBMISSION_DATASET_REGISTRATION_INVALID')
        manifest = Path(item['manifest_path'])
        if manifest.is_absolute():
            provider.register_manifest(item['dataset_id'],item['root_id'],internal(manifest))
        else:
            provider.register(**item)
    return StrategySubmissionV1(provider,resolve_authority,output / 'tasks',
        continuous_scope=continuous_scope, trusted_data_deployment=config.get('trusted_data_deployment'))


def build_lifecycle_service(workspace_root, config):
    """UI、CLI 与宿主共用的固定部署；配置不能指定 Python 模块或执行 URL。"""
    import os
    from chanlun_trader.research_factory.lifecycle_service_v2 import LifecycleServiceV2
    from chanlun_trader.research_factory.campaign_scope_v1 import OwnerApprovalStoreV1
    from chanlun_trader.research_factory.continuous_universe_lifecycle_v1 import ContinuousUniverseLifecycleV1
    if not isinstance(config, dict):
        raise ValueError('LIFECYCLE_DEPLOYMENT_CONFIG_FIELDS')
    config = deepcopy(config)
    # 原 CLI 曾直接接受 binding map，保留该入口。
    if 'bindings' not in config and 'continuous' not in config:
        return LifecycleServiceV2(workspace_root, config, research_loader=qualified_research_loader(workspace_root))
    if set(config) - {'bindings', 'real_binding_ids', 'continuous'}:
        raise ValueError('LIFECYCLE_DEPLOYMENT_CONFIG_FIELDS')
    continuous = None
    if 'continuous' in config:
        deployment = config['continuous']
        if (not isinstance(deployment, dict) or not {'submission', 'owner_approval', 'researches'} <= set(deployment)
                or set(deployment) - {'submission', 'owner_approval', 'researches', 'model_gateway'}):
            raise ValueError('CONTINUOUS_DEPLOYMENT_FIELDS_INVALID')
        owner = _owner_configuration(deployment)
        invoker_factory = None
        invoker_identity = {'kind': 'CODEX_HARD_BUDGET_UNSUPPORTED'}
        if 'model_gateway' in deployment:
            from chanlun_trader.research_factory.budget_gateway_model_v1 import TrustedBudgetGatewayInvokerV1
            gateway = deployment['model_gateway']
            if not isinstance(gateway, dict) or set(gateway) != {
                    'endpoint', 'model_id', 'trusted_contract_sha256', 'bearer_token_env'}:
                raise ValueError('CONTINUOUS_GATEWAY_CONFIG_INVALID')
            _environment_name(gateway['bearer_token_env'])
            invoker_identity = {'kind': 'TRUSTED_MODEL_BUDGET_GATEWAY_V1', **{key: gateway[key]
                for key in ('endpoint', 'model_id', 'trusted_contract_sha256')}}
            token = os.environ.get(gateway['bearer_token_env'])
            TrustedBudgetGatewayInvokerV1(endpoint=gateway['endpoint'], model_id=gateway['model_id'],
                trusted_contract_sha256=gateway['trusted_contract_sha256'],
                bearer_token=token or 'UNCONFIGURED_VALIDATION_ONLY')  # 仅校验固定配置，不连接。
            if token:
                def invoker_factory():
                    return TrustedBudgetGatewayInvokerV1(endpoint=gateway['endpoint'], model_id=gateway['model_id'],
                        trusted_contract_sha256=gateway['trusted_contract_sha256'], bearer_token=token)
        continuous = ContinuousUniverseLifecycleV1(workspace_root, deployment['researches'],
            approvals=OwnerApprovalStoreV1(owner['store_root']), invoker_factory=invoker_factory,
            invoker_deployment_identity=invoker_identity,
            submission_factory=lambda scope: build_submission_service(workspace_root, deployment['submission'], continuous_scope=scope))
    return LifecycleServiceV2(workspace_root, config.get('bindings', {}),
        research_loader=qualified_research_loader(workspace_root), real_binding_ids=config.get('real_binding_ids', ()),
        continuous=continuous)


def _environment_name(value):
    import re
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}', value):
        raise ValueError('DEPLOYMENT_ENVIRONMENT_NAME_INVALID')


def _owner_configuration(deployment):
    import re
    owner = deployment.get('owner_approval')
    if not isinstance(owner, dict) or set(owner) != {'store_root', 'token_env', 'token_sha256'}:
        raise ValueError('CONTINUOUS_OWNER_CONFIG_INVALID')
    path = Path(owner['store_root'])
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError('CONTINUOUS_OWNER_STORE_PATH_INVALID')
    _environment_name(owner['token_env'])
    if not isinstance(owner['token_sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', owner['token_sha256']):
        raise ValueError('CONTINUOUS_OWNER_TOKEN_PIN_INVALID')
    return owner


def approve_continuous_scope(workspace_root, config, research_id, *, approver, grant=None):
    """仅受保护 Owner CLI 调用；普通宿主与 HTTP 不获得写审批的对象能力。"""
    import hashlib
    import hmac
    import os
    from chanlun_trader.research_factory.campaign_scope_v1 import OwnerApprovalStoreV1
    deployment = config.get('continuous', {})
    owner = _owner_configuration(deployment)
    token = os.environ.get(owner['token_env'])
    if not token or not hmac.compare_digest(hashlib.sha256(token.encode('utf-8')).hexdigest(), owner['token_sha256']):
        raise PermissionError('CONTINUOUS_OWNER_CHANNEL_REQUIRED')
    service = build_lifecycle_service(workspace_root, config)
    preview = service.continuous.preview(research_id, grant=grant)
    capability = object()
    store = OwnerApprovalStoreV1(owner['store_root'], owner_capability=capability)
    reference = store.approve(preview['summary'], approver=approver, capability=capability)
    return {'research_id': research_id, 'approval_ref': reference, 'summary_hash': preview['summary_hash'],
            'dispatched_operations': 0}


def approve_continuous_evidence(config, research_id, evidence, *, approver):
    """维护者登记真实审核/资料原件；审批本身不生成市场资料或真实性证明。"""
    import hashlib
    import hmac
    import os
    from chanlun_trader.research_factory.campaign_scope_v1 import OwnerApprovalStoreV1
    deployment = config.get('continuous', {})
    owner = _owner_configuration(deployment)
    token = os.environ.get(owner['token_env'])
    if not token or not hmac.compare_digest(hashlib.sha256(token.encode('utf-8')).hexdigest(), owner['token_sha256']):
        raise PermissionError('CONTINUOUS_OWNER_CHANNEL_REQUIRED')
    if research_id not in deployment.get('researches', {}):
        raise ValueError('CONTINUOUS_RESEARCH_NOT_REGISTERED')
    allowed = {'PINNED_INDEPENDENT_ADMISSION_V1', 'CANONICAL_INDEPENDENT_DATA_REVIEW_V1',
               'TRUSTED_RESEARCH_DATA_ACCESS_AUTHORIZATION_V1', 'OWNER_TRAIN_PROJECTION_ADMISSION_V1'}
    if not isinstance(evidence, dict) or evidence.get('schema_version') not in allowed:
        raise ValueError('CONTINUOUS_OWNER_EVIDENCE_SCHEMA_INVALID')
    capability = object()
    store = OwnerApprovalStoreV1(owner['store_root'], owner_capability=capability)
    return {'research_id': research_id, 'approval_ref': store.approve(evidence, approver=approver, capability=capability),
            'dispatched_operations': 0}


def load_continuous_research(workspace_root, config, research_id):
    """只恢复同一已批准对象；验收工具不能借此创建授权或替换部署。"""
    service = build_lifecycle_service(workspace_root, config)
    if service.continuous is None:
        raise ValueError('CONTINUOUS_DEPLOYMENT_REQUIRED')
    return service.continuous._service(research_id)
