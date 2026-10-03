"""CLI 与 UI 共用固定历史数据装配；不从配置加载代码。"""
from pathlib import Path


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


def build_submission_service(workspace_root, config):
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
            or set(config) - {'roots','datasets','authorizations','output_root','version'}
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
                'account_authorization','execution_profiles','compute_authorization','engineering_authorization'}:
            raise PermissionError('SUBMISSION_AUTHORITY_FIELDS_INVALID')
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
        roots = {key: str(internal(value)) for key, value in config['roots'].items()}
        provider = UniverseDataProviderV1(roots, record_access)
    else:
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
    return StrategySubmissionV1(provider,resolve_authority,output / 'tasks')
