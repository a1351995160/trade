"""固定 Owner 入场证明：绑定 TRAIN 投影元信息，不授予账户资格或独立性。"""
from copy import deepcopy
import hashlib
from pathlib import Path, PureWindowsPath
import re

from ..research.guard import ResearchDataAccessGuard, configured_access_authority
from .campaign_scope_v1 import OwnerApprovalStoreV1
from . import research_dataset_projection_v1 as projection
from .research_universe_v1 import _day, identity
from .universe_data_provider_v1 import UniverseDataProviderV1
from .secure_file_reference_v1 import read_pinned_json


VERSION = 'VERIFIED_TRAIN_PROJECTION_ADMISSION_V1'
OWNER_VERSION = 'OWNER_TRAIN_PROJECTION_ADMISSION_V1'


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value) is not None


def _pinned_json(reference, label):
    return read_pinned_json(reference, error_code='TRAIN_ADMISSION_REFERENCE_INVALID:' + label)


def _iso_day(value):
    day = _day(value)
    text = str(day)
    if not isinstance(value, str) or value != f'{text[:4]}-{text[4:6]}-{text[6:]}':
        raise ValueError('TRAIN_ADMISSION_DATE_INVALID')
    return day


def _projection_authority(deployment, parent_id, parent_hash):
    config = _pinned_json(deployment, 'projection deployment')
    if (not isinstance(config, dict) or set(config) != {
            'schema_version', 'trusted_data_deployment', 'registered_parents'}
            or config['schema_version'] != 'TRUSTED_TRAIN_PROJECTION_DEPLOYMENT_V1'
            or not isinstance(config['registered_parents'], dict)):
        raise ValueError('TRAIN_ADMISSION_PROJECTION_DEPLOYMENT_INVALID')
    parent = config['registered_parents'].get(parent_id)
    if (not isinstance(parent, dict) or set(parent) != {'root', 'manifest_path', 'manifest_sha256'}
            or parent['manifest_sha256'] != parent_hash or not isinstance(parent['root'], str)
            or not Path(parent['root']).is_absolute() or Path(parent['root']).resolve() != Path(parent['root'])):
        raise ValueError('TRAIN_ADMISSION_PARENT_REGISTRATION_CONFLICT')
    name = parent['manifest_path']
    if (not isinstance(name, str) or Path(name).suffix.lower() != '.json'
            or Path(name).anchor or PureWindowsPath(name).anchor
            or '..' in Path(name).parts or '..' in PureWindowsPath(name).parts):
        raise ValueError('TRAIN_ADMISSION_PARENT_REGISTRATION_INVALID')
    # 仅核对固定登记，不能为复核投影而重新打开父 manifest 或父行情。
    data = config['trusted_data_deployment']
    if not isinstance(data, dict) or set(data) != {'path', 'sha256'}:
        raise ValueError('TRAIN_ADMISSION_DATA_DEPLOYMENT_INVALID')
    _pinned_json(data, 'data deployment')
    return configured_access_authority(data['path'], expected_sha256=data['sha256'])


def verify_train_projection_admission(reference, *, approvals, expected_contract_hash,
                                      expected_route, provider, expected_projection_deployment):
    """可信 builder 延迟调用；reference 和两个 expected 值必须来自固定部署。

    只读取已批准的 admission、部署、授权、子 manifest/receipt 和当前配方源码。
    行情内容及正式账户资格由原 bounded provider worker 另行校验；此证明不能
    替代其来源哈希、交易日、预热、状态、权利元信息和单位检查。
    """
    if (not isinstance(approvals, OwnerApprovalStoreV1)
            or not isinstance(provider, UniverseDataProviderV1)
            or not _hash(expected_contract_hash)
            or not isinstance(reference, dict) or set(reference) != {'path', 'sha256', 'approval_ref'}):
        raise ValueError('TRAIN_ADMISSION_TRUSTED_BUILDER_REQUIRED')
    value = _pinned_json({key: reference[key] for key in ('path', 'sha256')}, 'admission')
    approvals.require(reference['approval_ref'], value)
    fields = {'schema_version', 'research_contract_hash', 'trusted_route', 'dataset_id',
              'manifest_sha256', 'receipt_sha256', 'projection_deployment', 'authorization_ref', 'window'}
    if (not isinstance(value, dict) or set(value) != fields or value['schema_version'] != OWNER_VERSION
            or value['research_contract_hash'] != expected_contract_hash
            or value['trusted_route'] != expected_route
            or value['projection_deployment'] != expected_projection_deployment
            or not _hash(value['manifest_sha256']) or not _hash(value['receipt_sha256'])
            or not isinstance(value['authorization_ref'], str) or not value['authorization_ref']):
        raise ValueError('TRAIN_ADMISSION_OWNER_BINDING_CONFLICT')
    route = value['trusted_route']
    if (not isinstance(route, dict) or set(route) != {'route_id', 'producer_identity', 'source_ids',
            'start', 'end', 'purpose', 'universe_hash', 'quality_policy'}
            or route['purpose'] != 'TRAIN' or not _hash(route['universe_hash'])
            or any(not isinstance(route[key], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', route[key])
                   for key in ('route_id', 'producer_identity'))
            or not isinstance(route['source_ids'], list) or not route['source_ids']
            or any(not isinstance(item, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', item)
                   for item in route['source_ids'])
            or len(set(route['source_ids'])) != len(route['source_ids'])
            or not isinstance(route['quality_policy'], dict) or not route['quality_policy']):
        raise ValueError('TRAIN_ADMISSION_TRAIN_ROUTE_REQUIRED')
    window = value['window']
    if not isinstance(window, dict) or set(window) != {'feature_start', 'account_start', 'account_end'}:
        raise ValueError('TRAIN_ADMISSION_WINDOW_INVALID')
    start, account, end = (_iso_day(window[key]) for key in ('feature_start', 'account_start', 'account_end'))
    if not _iso_day(route['start']) <= start < account < end <= _iso_day(route['end']):
        raise ValueError('TRAIN_ADMISSION_WINDOW_NOT_COVERED')
    ResearchDataAccessGuard().check_range(start, end, 'TRAIN admission')
    dataset_id = value['dataset_id']
    if not isinstance(dataset_id, str) or dataset_id not in provider._datasets:
        raise ValueError('TRAIN_ADMISSION_CHILD_NOT_REGISTERED')
    root, manifest, digest, universe = provider._datasets[dataset_id]
    metadata = provider._metadata_paths[dataset_id]
    if (digest != value['manifest_sha256']
            or hashlib.sha256(metadata.read_bytes()).hexdigest() != digest):
        raise ValueError('TRAIN_ADMISSION_CHILD_MANIFEST_CHANGED')
    projection.validate_projection_registration(root, manifest, digest)
    binding = manifest.get('projection')
    if not isinstance(binding, dict):
        raise ValueError('TRAIN_ADMISSION_OFFICIAL_PROJECTION_REQUIRED')
    receipt_path = provider._path(root, binding['receipt_path'])
    receipt = _pinned_json({'path': str(receipt_path), 'sha256': value['receipt_sha256']}, 'projection receipt')
    recipe_hash = hashlib.sha256(Path(projection.__file__).read_bytes()).hexdigest()
    if (receipt.get('purpose') != projection.PURPOSE or receipt.get('recipe_sha256') != recipe_hash
            or receipt.get('originals_modified') is not False
            or receipt.get('independent_confirmation_eligible') is not False
            or (receipt['output_start'], receipt['output_end']) != (start, end)
            or universe.universe_identity != route['universe_hash']
            or receipt.get('target_symbols') != universe.target_symbols
            or receipt.get('target_count') != len(universe.target_symbols)
            or receipt.get('master_record_count') != len(universe.records)
            or set(route['source_ids']) != {row['source_id'] for row in manifest['files'].values()}):
        raise ValueError('TRAIN_ADMISSION_PROJECTION_IDENTITY_CONFLICT')
    historical_scope = manifest.get('universe_scope')
    if historical_scope is not None and (account < _day(historical_scope['start'])
            or end > _day(historical_scope['end'])):
        raise ValueError('TRAIN_ADMISSION_HISTORICAL_UNIVERSE_NOT_COVERED')
    rows = receipt['sources']
    if (len(rows) != len(manifest['files'])
            or len({row['parent_name'] for row in rows}) != len(rows)
            or len({row['child_name'] for row in rows}) != len(rows)):
        raise ValueError('TRAIN_ADMISSION_SOURCE_DENOMINATOR_CONFLICT')
    sources = {}
    for row in rows:
        child = manifest['files'][row['child_name']]
        parent = child.get('projection_parent')
        if (not isinstance(parent, dict) or parent.get('name') != row['parent_name']
                or parent.get('sha256') != row['parent_sha256']
                or parent.get('physical_start') != row['parent_physical_start']
                or parent.get('physical_end') != row['parent_physical_end']
                or row.get('parent_whole_file_read_for_non_analytical_preparation') is not True):
            raise ValueError('TRAIN_ADMISSION_PARENT_SOURCE_CONFLICT')
        if child['kind'] == 'DAILY':
            evidence = child.get('evidence', {})
            original = evidence.get('projection_parent_evidence')
            if (not isinstance(original, dict) or original.get('source_sha256') != parent['sha256']
                    or evidence != {**original, 'source_sha256': row['child_sha256'],
                        'original_source_hashes': {**original.get('original_source_hashes', {}),
                            'projection_parent:' + parent['name']: parent['sha256']},
                        'transformation_version': projection.VERSION, 'transformation_sha256': recipe_hash,
                        'projection_parent_evidence': original}):
                raise ValueError('TRAIN_ADMISSION_SOURCE_UNIT_PROVENANCE_CONFLICT')
        sources[row['parent_name']] = {'sha256': parent['sha256'],
            'physical_start': parent['declared_start'], 'physical_end': parent['declared_end']}
    authority = _projection_authority(expected_projection_deployment,
                                     receipt['dataset_id'], receipt['parent_manifest_sha256'])
    scope = authority.authorize(value['authorization_ref'], purpose=projection.PURPOSE,
        dataset_id=receipt['dataset_id'], manifest_sha256=receipt['parent_manifest_sha256'],
        sources=sources, output_start=start, output_end=end, recipe_version=projection.VERSION)
    grant = scope.binding
    if (grant['authorization_id'] != receipt.get('authorization_id')
            or grant['approval_id'] != receipt.get('approval_id')
            or receipt['projection_id'] != identity({'version': projection.VERSION,
                'dataset_id': receipt['dataset_id'], 'manifest_sha256': receipt['parent_manifest_sha256'],
                'authorization_id': grant['authorization_id'], 'output_start': start,
                'output_end': end, 'account_start': account})
            or type(receipt.get('input_bytes')) is not int
            or not 0 <= receipt['input_bytes'] <= grant['max_input_bytes']):
        raise ValueError('TRAIN_ADMISSION_PREPARATION_GRANT_CONFLICT')
    for row in rows:
        bounds = row['parent_physical_start'], row['parent_physical_end']
        if bounds != (None, None):
            scope.guard(purpose=projection.PURPOSE, dataset_id=receipt['dataset_id'],
                manifest_sha256=receipt['parent_manifest_sha256'], source_name=row['parent_name']).check_range(
                    *bounds, 'TRAIN admission recorded physical range')
    published = [metadata, receipt_path] + [provider._path(root, name) for name in manifest['files']]
    if sum(path.stat().st_size for path in set(published)) > grant['max_output_bytes']:
        raise ValueError('TRAIN_ADMISSION_OUTPUT_BYTE_LIMIT_EXCEEDED')
    proof = {'schema_version': VERSION, 'admission_reference': deepcopy(reference),
        'research_contract_hash': expected_contract_hash, 'trusted_route': deepcopy(route),
        'dataset_id': dataset_id, 'dataset_hash': digest,
        'request_fields': {'dataset_id': dataset_id, **deepcopy(window), 'purpose': 'EXPLORATORY'},
        'projection_id': receipt['projection_id'], 'projection_receipt_sha256': value['receipt_sha256'],
        'projection_recipe_sha256': recipe_hash, 'projection_deployment': deepcopy(expected_projection_deployment),
        'projection_authorization_reference': scope.reference,
        'universe_identity': universe.universe_identity, 'target_count': len(universe.target_symbols),
        'data_content_read': False, 'account_data_ready': False,
        'qualification_required': 'BOUNDED_PROVIDER_ACCOUNT_AUDIT',
        'historical_independence': 'UNKNOWN', 'independent_confirmation_eligible': False}
    proof['proof_id'] = identity(proof)
    return proof
