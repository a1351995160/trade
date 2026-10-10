"""TRAIN 隔离和可信读取合同；所有原件均为临时合成资料。"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from chanlun_trader.research.guard import (
    FinalTestAccessViolation, ResearchDataAccessGuard,
    TrustedResearchDataAccessAuthorityV1, TrustedResearchDataAccessScopeV1,
    configured_access_authority, guard_from_frozen,
)
from chanlun_trader.research_factory import research_dataset_projection_v1 as projection
from chanlun_trader.research_factory.research_data_qualification_v1 import continuous_data_dependencies_v1
from chanlun_trader.research_factory.universe_data_provider_v1 import UniverseDataProviderV1
from test_universe_submission_v1 import public_universe_case


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    Path(path).write_text(json.dumps(value), encoding='utf-8')


def case(tmp_path, change=None):
    service, request, _, _ = public_universe_case(tmp_path)
    root, manifest, _, _ = service.provider._datasets['sample']
    manifest = deepcopy(manifest)
    days = json.loads((root / 'calendar.json').read_text(encoding='utf-8'))
    daily = pd.read_parquet(root / 'daily.parquet')
    reference = daily[['symbol', 'date', 'prev_close']].copy()
    reference['source'] = 'reference'
    reference.to_parquet(root / 'reference.parquet', index=False)
    qualifications = [{'symbol': row['symbol'], 'indicator_qualification': 'RAW_PRICE_AND_MODELED_UNIT_READY',
        'origin_status': 'EXACT_RAW_WINDOW_MATCH', 'reasons': [], 'source': 'daily'}
        for row in manifest['master']['records']]
    write(root / 'qualification.json', qualifications)
    write(root / 'events.json', [])
    for name, kind, form, source in (
            ('reference.parquet', 'REFERENCE_PRICES', 'PARQUET', 'reference'),
            ('qualification.json', 'SOURCE_QUALIFICATION', 'JSON', 'qualification'),
            ('events.json', 'EVENTS', 'JSON', 'events')):
        manifest['files'][name] = {'kind': kind, 'format': form, 'source_id': source,
            'sha256': sha(root / name), 'start': days[0], 'end': days[-1]}
    manifest['universe_scope'] = {'start': days[0], 'end': days[-1]}
    if change:
        change(root, manifest, days)
    write(root / 'parent_manifest.json', manifest)
    provider = UniverseDataProviderV1({'synthetic': root})
    provider.register('parent', 'synthetic', 'parent_manifest.json')
    parent_hash = provider._datasets['parent'][2]
    grant = {'schema_version': 'TRUSTED_RESEARCH_DATA_ACCESS_AUTHORIZATION_V1',
        'authorization_id': 'SYNTHETIC_PREPARATION', 'approval_id': 'SYNTHETIC_OWNER_APPROVAL',
        'purpose': projection.PURPOSE, 'dataset_id': 'parent', 'manifest_sha256': parent_hash,
        'sources': {name: {'sha256': item['sha256'], 'physical_start': item['start'],
                           'physical_end': item['end']} for name, item in manifest['files'].items()},
        'output_start': days[10], 'output_end': days[70], 'recipe_version': projection.VERSION,
        'max_input_bytes': 10_000_000, 'max_output_bytes': 10_000_000,
        'expires_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}
    authority = TrustedResearchDataAccessAuthorityV1(lambda ref: deepcopy(grant) if ref == 'deployed-ref' else None)
    recorder = []
    service = projection.ResearchDatasetProjectionV1(provider, authority, recorder.append)
    arguments = {'output_root': tmp_path / 'train', 'train_start': days[10], 'train_end': days[70],
                 'authorization_ref': 'deployed-ref', 'account_start': days[60]}
    return service, provider, root, manifest, days, grant, arguments, recorder


def prepare(result, days, *, stage='ACCOUNT'):
    root = Path(result['manifest_path']).parent
    provider = UniverseDataProviderV1({'train': root})
    provider.register('train', 'train', Path(result['manifest_path']).name)
    return provider.prepare('train', feature_start=days[10], account_start=days[60], account_end=days[70],
        stage=stage, required_fields=['close'], authorization={'authorization_id': 'SYNTHETIC_TRAIN_USE',
            'purpose': 'EXPLORATORY', 'dataset_ids': ['train'], 'start': days[10], 'end': days[70]})


def test_physical_train_child_is_provider_ready_and_never_reopens_parent(tmp_path, monkeypatch):
    service, _, root, original, days, _, arguments, accesses = case(tmp_path)
    before = {path.name: path.read_bytes() for path in root.iterdir()}
    result = service.project('parent', **arguments)
    manifest = json.loads(Path(result['manifest_path']).read_text(encoding='utf-8'))
    assert manifest['master'] == original['master']
    assert manifest['universe_id'] == original['universe_id']
    assert manifest['universe_scope'] == {'start': days[10], 'end': days[70]}
    assert len(accesses) == len(original['files'])
    assert all(path.read_bytes() == before[path.name] for path in root.iterdir())
    for name, item in manifest['files'].items():
        if item['kind'] in {'DAILY', 'REFERENCE_PRICES'}:
            frame = pd.read_parquet(Path(result['manifest_path']).parent / name)
            assert frame.date.min() == days[10] and frame.date.max() == days[70]
        if item['kind'] == 'DAILY':
            parent_evidence = original['files'][item['projection_parent']['name']]['evidence']
            assert item['evidence']['unit_evidence'] == parent_evidence['unit_evidence']
            assert item['evidence']['projection_parent_evidence'] == parent_evidence
            assert item['symbols'] == original['files'][item['projection_parent']['name']]['symbols']
    original_open = Path.open
    def forbid_parent(path, *args, **kwargs):
        assert not path.is_relative_to(root), '普通探索不可重新打开父资料'
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', forbid_parent)
    prepared = prepare(result, days)
    assert prepared['qualification']['account_data_ready']
    assert len(prepared['window']['symbols']) == 3
    assert prepared['qualification']['historical_independence'] == 'UNKNOWN'
    assert not prepared['qualification']['independent_confirmation_eligible']
    dependencies = result['data_dependencies']
    assert dependencies['exploration_504']['observed_calendar_account_sessions'] == 11
    assert dependencies['exploration_504']['missing_account_sessions'] == 493
    assert not dependencies['independent_252']['ready']


@pytest.mark.parametrize('failure', ['physical_range', 'hash_binding', 'purpose', 'reference', 'expired'])
def test_unapproved_whole_source_rejected_before_hash_or_content(tmp_path, monkeypatch, failure):
    service, _, _, _, days, grant, arguments, accesses = case(tmp_path)
    if failure == 'physical_range':
        grant['sources']['daily.parquet']['physical_end'] = days[70]
    elif failure == 'hash_binding':
        grant['sources']['daily.parquet']['sha256'] = 'f' * 64
    elif failure == 'purpose':
        grant['purpose'] = 'EXPLORATORY'
    elif failure == 'reference':
        arguments['authorization_ref'] = 'unregistered-ref'
    else:
        grant['expires_at'] = '2000-01-01T00:00:00+00:00'
    monkeypatch.setattr(projection, '_sha', lambda *args: pytest.fail('未授权不得全文件哈希'))
    monkeypatch.setattr(projection, '_parquet_bounds', lambda *args: pytest.fail('声明范围未获准不探测原件'))
    with pytest.raises(ValueError, match='TRUSTED_DATA_'):
        service.project('parent', **arguments)
    assert not arguments['output_root'].exists() and accesses == []


def test_forged_parent_date_rejected_from_footer_before_price_hash(tmp_path, monkeypatch):
    def change(root, manifest, days):
        manifest['files']['daily.parquet']['end'] = days[70]
    service, _, _, _, _, _, arguments, _ = case(tmp_path, change)
    monkeypatch.setattr(projection, '_sha', lambda *args: pytest.fail('越权实际物理范围不得哈希'))
    with pytest.raises(FinalTestAccessViolation, match='SCOPE_RANGE'):
        service.project('parent', **arguments)
    assert not arguments['output_root'].exists()


@pytest.mark.parametrize('kind', ['source', 'manifest'])
def test_changed_parent_source_or_manifest_rejected_without_publication(tmp_path, kind):
    service, _, root, _, _, _, arguments, _ = case(tmp_path)
    if kind == 'source':
        path = root / 'daily.parquet'
        frame = pd.read_parquet(path)
        frame.loc[0, 'amount'] += 1
        frame.to_parquet(path, index=False)
    else:
        path = root / 'parent_manifest.json'
        path.write_text(path.read_text(encoding='utf-8') + ' ', encoding='utf-8')
    with pytest.raises(ValueError, match='CHANGED'):
        service.project('parent', **arguments)
    assert not arguments['output_root'].exists()


@pytest.mark.parametrize('kind', ['source', 'manifest', 'receipt'])
def test_child_source_manifest_or_receipt_tampering_is_detected(tmp_path, kind):
    service, _, _, _, days, _, arguments, _ = case(tmp_path)
    result = service.project('parent', **arguments)
    manifest_path = Path(result['manifest_path'])
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if kind == 'source':
        name = next(name for name, row in manifest['files'].items() if row['kind'] == 'DAILY')
        path = manifest_path.parent / name
        frame = pd.read_parquet(path)
        frame.loc[0, 'amount'] += 1
        frame.to_parquet(path, index=False)
    elif kind == 'manifest':
        manifest['start'] = days[9]
        write(manifest_path, manifest)
    else:
        receipt_path = Path(result['receipt_path'])
        receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
        receipt['target_count'] = 1
        write(receipt_path, receipt)
    with pytest.raises(ValueError, match='CONTENT_CHANGED|BINDING_CONFLICT'):
        prepare(result, days)


def test_intersecting_suspension_and_late_state_preserve_semantics_and_denominator(tmp_path):
    def change(root, manifest, days):
        frame = pd.read_parquet(root / 'states.parquet')
        frame.loc[frame.symbol.eq('000001.SZ'), 'suspension_status'] = 'SUSPENDED'
        frame.loc[frame.symbol.eq('300001.SZ'), 'available_at'] = '2022-12-30T09:30:00+08:00'
        frame.to_parquet(root / 'states.parquet', index=False)
        manifest['files']['states.parquet']['sha256'] = sha(root / 'states.parquet')
    service, _, _, _, days, _, arguments, _ = case(tmp_path, change)
    result = service.project('parent', **arguments)
    prepared = prepare(result, days, stage='SCAN')
    states = prepared['bundle']['states']
    assert set(states.effective_date) == {days[10]} and set(states.valid_to) == {days[70]}
    assert states.loc[states.symbol.eq('000001.SZ'), 'suspension_status'].item() == 'SUSPENDED'
    assert states.loc[states.symbol.eq('300001.SZ'), 'available_at'].item() == '2022-12-30T09:30:00+08:00'
    assert prepared['qualification']['coverage']['target_symbol_count'] == 3
    assert not prepared['qualification']['account_data_ready']
    assert any(row['symbol'] == '300001.SZ' and row['reason'] == 'UNIVERSE_STATE_NOT_YET_AVAILABLE'
               for row in prepared['qualification']['coverage']['gaps'])


def test_cross_boundary_dividend_keeps_full_rights_without_future_prices(tmp_path):
    def change(root, manifest, days):
        write(root / 'events.json', [{'event_id': 'CROSS_BOUNDARY', 'symbol': '000001.SZ',
            'event_type': 'CASH_DIVIDEND', 'record_date': days[69], 'effective_date': days[71],
            'payment_date': days[75], 'source': 'events', 'source_published_at': str(days[68]),
            'units': 'CNY_PER_SHARE', 'terms': {'cash_per_share': .01,
                'tax_rule': {'kind': 'DEFERRED_INDIVIDUAL_2015_101', 'source': 'SYNTHETIC_TAX'}}}])
        manifest['files']['events.json']['sha256'] = sha(root / 'events.json')
    service, _, _, _, days, _, arguments, _ = case(tmp_path, change)
    result = service.project('parent', **arguments)
    prepared = prepare(result, days)
    assert prepared['qualification']['account_data_ready']
    event = prepared['bundle']['events'][0]
    assert event['effective_date'] == days[71] and event['payment_date'] == days[75]
    assert prepared['bundle']['daily'].date.max() == days[70]
    assert event['source_published_at'] == str(days[68])


@pytest.mark.parametrize('failure', ['sealed', 'not_published'])
def test_sealed_or_future_unknown_event_terms_do_not_enter_train(tmp_path, failure):
    def change(root, manifest, days):
        write(root / 'events.json', [{'event_id': 'DENIED_TERMS', 'symbol': '000001.SZ',
            'event_type': 'CASH_DIVIDEND', 'record_date': days[69], 'effective_date': days[71],
            'payment_date': 20250801 if failure == 'sealed' else days[75],
            'source': 'events', 'source_published_at': str(days[72] if failure == 'not_published' else days[68])}])
        manifest['files']['events.json']['sha256'] = sha(root / 'events.json')
    service, _, _, _, _, _, arguments, _ = case(tmp_path, change)
    with pytest.raises((FinalTestAccessViolation, ValueError), match='FINAL_TEST|NOT_AVAILABLE_IN_TRAIN'):
        service.project('parent', **arguments)
    assert not arguments['output_root'].exists()


@pytest.mark.parametrize('resource', ['max_input_bytes', 'max_output_bytes'])
def test_resource_limit_does_not_publish_half_dataset(tmp_path, resource):
    service, _, _, _, _, grant, arguments, _ = case(tmp_path)
    grant[resource] = 1
    with pytest.raises(ValueError, match='BYTE_LIMIT_EXCEEDED'):
        service.project('parent', **arguments)
    assert not arguments['output_root'].exists()


def test_price_only_registration_cannot_extend_historical_account_scope():
    report = continuous_data_dependencies_v1({'files': {'prices': {'kind': 'DAILY',
        'start': 20200101, 'end': 20250731}}, 'universe_scope': {'start': 20220902, 'end': 20241231}},
        account_sessions=462)
    assert report['exploration_504']['missing_account_sessions'] == 42
    assert report['exploration_504']['historical_universe_scope']['start'] == 20220902
    assert set(report['missing_categories']) == {'STATES', 'REFERENCE_PRICES', 'EVENTS',
        'CALENDAR', 'CORPORATE_ACTION_COVERAGE', 'SOURCE_QUALIFICATION'}
    assert not report['account_data_ready'] and not report['independent_252']['ready']


def test_scope_cannot_be_constructed_from_dict_and_default_sealed_lock_remains(tmp_path):
    _, _, _, _, _, grant, _, _ = case(tmp_path)
    with pytest.raises(ValueError, match='ISSUER_REQUIRED'):
        TrustedResearchDataAccessScopeV1(grant)
    with pytest.raises(ValueError, match='TRUSTED_AUTHORITY'):
        projection.ResearchDatasetProjectionV1(None, True)
    with pytest.raises(FinalTestAccessViolation):
        ResearchDataAccessGuard().check_date(20250801)


def test_independent_scope_requires_protocol_and_cannot_cross_use(tmp_path):
    _, _, _, _, _, grant, _, _ = case(tmp_path)
    grant.update(purpose='INDEPENDENT_CONFIRMATION', output_start=20250801, output_end=20250805,
        recipe_version='INDEPENDENT_DATA_V1')
    for source in grant['sources'].values():
        source.update(physical_start=20250801, physical_end=20250805)
    authority = TrustedResearchDataAccessAuthorityV1(lambda ref: deepcopy(grant))
    request = {key: grant[key] for key in ('purpose', 'dataset_id', 'manifest_sha256', 'sources',
        'output_start', 'output_end', 'recipe_version')}
    with pytest.raises(ValueError, match='INDEPENDENT_PROTOCOL'):
        authority.authorize('deployed-ref', **request)
    protocol = {'protocol_id': 'FROZEN_FAMILY', 'protocol_sha256': 'a' * 64,
        'independent_evidence_id': 'SYNTHETIC_REAL_OBSERVATIONS', 'independent_evidence_sha256': 'b' * 64}
    grant.update(protocol)
    scope = authority.authorize('deployed-ref', **request, protocol_binding=protocol)
    guard = scope.guard(purpose='INDEPENDENT_CONFIRMATION', dataset_id=grant['dataset_id'],
                        manifest_sha256=grant['manifest_sha256'])
    guard.check_range(20250801, 20250805)
    with pytest.raises(FinalTestAccessViolation, match='SCOPE_RANGE'):
        guard.check_date(20250806)
    with pytest.raises(ValueError, match='PURPOSE_CONFLICT'):
        scope.guard(purpose='EXPLORATORY', dataset_id=grant['dataset_id'],
                    manifest_sha256=grant['manifest_sha256'])
    with pytest.raises(FinalTestAccessViolation):
        ResearchDataAccessGuard().check_date(20250801)


def independent_case(tmp_path, *, configured=False, cross_rights=False):
    def future(root, manifest, days):
        future_days = [int(day.strftime('%Y%m%d')) for day in pd.bdate_range('2025-08-01', periods=len(days))]
        mapping = dict(zip(days, future_days))
        for name in ('daily.parquet', 'reference.parquet', 'states.parquet'):
            frame = pd.read_parquet(root / name)
            for column in ('date', 'effective_date', 'valid_to'):
                if column in frame:
                    frame[column] = frame[column].map(mapping)
            frame.to_parquet(root / name, index=False)
            manifest['files'][name]['sha256'] = sha(root / name)
        manifest['files']['daily.parquet']['evidence']['source_sha256'] = sha(root / 'daily.parquet')
        write(root / 'calendar.json', future_days)
        rows = json.loads((root / 'actions.json').read_text(encoding='utf-8'))
        for row in rows:
            row.update(start=future_days[0], end=future_days[-1])
        write(root / 'actions.json', rows)
        if cross_rights:
            write(root / 'events.json', [{'event_id': 'FUTURE_CROSS_RIGHTS', 'symbol': '000001.SZ',
                'event_type': 'CASH_DIVIDEND', 'record_date': future_days[69], 'effective_date': future_days[71],
                'payment_date': future_days[75], 'source': 'events', 'source_published_at': str(future_days[68]),
                'units': 'CNY_PER_SHARE', 'terms': {'cash_per_share': .01,
                    'tax_rule': {'kind': 'DEFERRED_INDIVIDUAL_2015_101', 'source': 'SYNTHETIC_TAX'}}}])
        for name, item in manifest['files'].items():
            item.update(start=future_days[0], end=future_days[-1], sha256=sha(root / name))
        manifest.update(start=future_days[0], end=future_days[-1],
                        universe_scope={'start': future_days[0], 'end': future_days[-1]})
        days[:] = future_days
    _, provider, root, manifest, days, grant, _, _ = case(tmp_path, future)
    protocol = {'protocol_id': 'SYNTHETIC_FROZEN_FAMILY', 'protocol_sha256': 'a' * 64,
        'independent_evidence_id': 'SYNTHETIC_OBSERVATIONS', 'independent_evidence_sha256': 'b' * 64}
    grant.update(protocol, purpose='INDEPENDENT_CONFIRMATION', recipe_version='INDEPENDENT_DATA_V1')
    deployment = None
    if configured:
        from chanlun_trader.research_factory.campaign_scope_v1 import OwnerApprovalStoreV1
        owner = object()
        approvals = OwnerApprovalStoreV1(tmp_path / 'owner-store', owner_capability=owner)
        reference = approvals.approve(grant, approver='SYNTHETIC_OWNER', capability=owner)
        authorization_path = tmp_path / 'registered-data-authorization.json'
        write(authorization_path, grant)
        config_path = tmp_path / 'protected-deployment.json'
        write(config_path, {'schema_version': 'TRUSTED_RESEARCH_DATA_DEPLOYMENT_V1',
            'owner_approval_store_root': str(approvals.directory),
            'records': {'deployed-ref': {'path': str(authorization_path), 'sha256': sha(authorization_path),
                                       'approval_reference': reference}}})
        deployment = {'path': str(config_path), 'sha256': sha(config_path)}
        authority = configured_access_authority(config_path, expected_sha256=deployment['sha256'])
    else:
        authority = TrustedResearchDataAccessAuthorityV1(lambda ref: deepcopy(grant) if ref == 'deployed-ref' else None)
    provider.trusted_access_authority = authority
    scope = provider.authorize_independent_scope('parent', authorization_ref='deployed-ref',
        feature_start=days[10], account_end=days[70], recipe_version='INDEPENDENT_DATA_V1',
        protocol_binding=protocol)
    args = {'feature_start': days[10], 'account_start': days[60], 'account_end': days[70],
            'purpose': 'INDEPENDENT_BUSINESS_VALIDATION', 'required_fields': ['close'], 'trusted_scope': scope}
    return provider, args, days, grant, scope, deployment


def test_future_provider_requires_opaque_protocol_scope_and_explicit_purpose(tmp_path):
    provider, args, days, _, scope, _ = independent_case(tmp_path)
    assert provider.catalog()['datasets'][0]['independent_confirmation_eligible'] is False
    assert provider.catalog(trusted_scope=scope)['datasets'][0]['authorized_data_purpose'] == 'INDEPENDENT_BUSINESS_VALIDATION'
    ordinary = {**args, 'purpose': 'EXPLORATORY', 'trusted_scope': None,
        'authorization': {'authorization_id': 'self-declared', 'purpose': 'EXPLORATORY',
            'dataset_ids': ['parent'], 'start': days[0], 'end': days[-1]}}
    with pytest.raises(FinalTestAccessViolation):
        provider.prepare('parent', **ordinary)
    for fake in (None, True, scope.frozen_binding):
        with pytest.raises(ValueError, match='TRUSTED_SCOPE'):
            provider.prepare('parent', **{**args, 'trusted_scope': fake})
    prepared = provider.prepare('parent', **args)
    assert prepared['qualification']['account_data_ready']
    assert prepared['qualification']['purpose'] == 'INDEPENDENT_BUSINESS_VALIDATION'
    assert prepared['qualification']['data_phase'] == 'CONFIRMATION'
    assert prepared['bundle']['daily'].date.min() == days[10]
    assert prepared['bundle']['daily'].date.max() == days[70]
    assert prepared['trusted_data_access'] == scope.frozen_binding


@pytest.mark.parametrize('configured', [False, True])
def test_future_scope_crosses_freeze_adopt_restore_only_with_same_trusted_identity(tmp_path, configured):
    from chanlun_trader.research_factory.universe_submission_v1 import (
        freeze_universe_bundle, adopt_frozen_universe_bundle, restore_universe_bundle,
    )
    provider, args, days, _, scope, deployment = independent_case(tmp_path, configured=configured, cross_rights=True)
    prepared = provider.prepare('parent', **args)
    folder = tmp_path / 'frozen'
    folder.mkdir()
    trusted = {'expected_deployment': deployment} if configured else {'trusted_scope': scope}
    frozen, _ = freeze_universe_bundle(prepared, folder, **trusted)
    value = json.loads(frozen.read_text(encoding='utf-8'))
    with pytest.raises(ValueError, match='DEPLOYMENT_EXPECTATION_REQUIRED'):
        restore_universe_bundle(value, frozen)
    restored = restore_universe_bundle(value, frozen, **trusted)
    assert restored['input_identity'] == prepared['input_identity']
    assert restored['actions'][0]['payment_date'] == days[75] > days[70]
    assert restored['frame']['daily'].date.max() == days[70]
    target = tmp_path / 'adopted'
    target.mkdir()
    adopted, adopted_path, _ = adopt_frozen_universe_bundle(frozen, target,
        input_identity=prepared['input_identity'], snapshot_sha256=sha(frozen), **trusted)
    assert restore_universe_bundle(adopted, adopted_path, **trusted)['input_identity'] == prepared['input_identity']
    assert adopted['trusted_data_access'] == value['trusted_data_access']
    if configured:
        fake = {**deployment, 'sha256': 'f' * 64}
        with pytest.raises(ValueError, match='DEPLOYMENT_EXPECTATION_REQUIRED'):
            guard_from_frozen(value, expected_deployment=fake)


def test_serialized_reference_is_not_capability_and_revoked_scope_fails_closed(tmp_path):
    provider, args, _, grant, scope, _ = independent_case(tmp_path)
    reference = scope.reference
    request = scope.frozen_binding['expected_bindings']
    restored = provider.trusted_access_authority.restore(reference, **request)
    assert restored.reference == reference
    with pytest.raises(ValueError, match='REFERENCE_BINDING_CONFLICT'):
        provider.trusted_access_authority.restore({**reference, 'authorization_id': 'other'}, **request)
    grant['approval_id'] = 'REVOKED_OR_CHANGED'
    with pytest.raises(ValueError, match='REVOKED_OR_CHANGED'):
        provider.prepare('parent', **args)


def test_owner_approval_and_deployment_pin_checked_before_any_future_content(tmp_path, monkeypatch):
    provider, args, _, _, _, deployment = independent_case(tmp_path, configured=True)
    config = json.loads(Path(deployment['path']).read_text(encoding='utf-8'))
    record = config['records']['deployed-ref']
    owner_record = Path(config['owner_approval_store_root']) / (record['approval_reference']['approval_id'] + '.json')
    owner_record.unlink()
    monkeypatch.setattr(provider, '_read', lambda *args: pytest.fail('无批准不得读取行情'))
    with pytest.raises(PermissionError, match='OWNER_APPROVAL_NOT_REGISTERED'):
        provider.prepare('parent', **args)


def test_registered_worker_restores_only_deployment_route_and_qualified_scope_keeps_it(tmp_path):
    from types import SimpleNamespace
    from chanlun_trader.research_factory.universe_data_provider_v1 import service_trusted_scope
    from chanlun_trader.research_factory.universe_scan_service_v1 import _registered_worker_provider
    from chanlun_trader.research_factory.universe_qualified_scope_v1 import qualify_universe_bundle
    from chanlun_trader.research_factory.universe_submission_v1 import freeze_universe_bundle, restore_universe_bundle
    provider, args, days, _, scope, deployment = independent_case(tmp_path, configured=True)
    protocol = scope.frozen_binding['expected_bindings']['protocol_binding']
    request = {'version': 'FULL_UNIVERSE_SUBMISSION_V4', 'phase': 'CONFIRMATION',
        'purpose': args['purpose'], 'dataset_id': 'parent', 'feature_start': days[10], 'account_end': days[70]}
    authority = {'trusted_deployment': deployment, 'trusted_data_access': {
        'authorization_ref': 'deployed-ref', 'recipe_version': 'INDEPENDENT_DATA_V1', 'protocol_binding': protocol}}
    service = SimpleNamespace(provider=provider, trusted_data_deployment=deployment)
    restored_scope = service_trusted_scope(service, request, authority)
    assert restored_scope.frozen_binding == scope.frozen_binding
    for forged in ({}, {**authority, 'trusted_deployment': {**deployment, 'sha256': 'f' * 64}}):
        with pytest.raises(ValueError, match='DEPLOYMENT_OR_ROUTE_REQUIRED'):
            service_trusted_scope(service, {**request, 'trusted_data_access': authority['trusted_data_access']}, forged)
    root = provider._datasets['parent'][0]
    intent = {'authority': {}, 'compute_authority': authority,
        'registration': {'root': str(root), 'original_metadata_path': str(provider._metadata_paths['parent'])}}
    worker_provider, worker_scope, worker_deployment = _registered_worker_provider(intent, request)
    prepared = worker_provider.prepare('parent', **{**args, 'trusted_scope': worker_scope})
    qualified = qualify_universe_bundle(prepared, required_fields=['close'])
    assert qualified['ready']
    assert qualified['trusted_data_access'] == prepared['trusted_data_access']
    folder = tmp_path / 'qualified'
    folder.mkdir()
    frozen, _ = freeze_universe_bundle(qualified, folder, expected_deployment=worker_deployment)
    value = json.loads(frozen.read_text(encoding='utf-8'))
    restored = restore_universe_bundle(value, frozen, expected_deployment=worker_deployment)
    assert restored['input_identity'] == qualified['input_identity']
    assert restored['frame']['daily'].date.max() == days[70]


def test_independent_events_use_own_physical_authorization_and_ignore_unrelated_rights(tmp_path):
    provider, args, days, grant, scope, _ = independent_case(tmp_path, cross_rights=True)
    protocol = scope.frozen_binding['expected_bindings']['protocol_binding']
    source_guard = scope.guard(purpose='INDEPENDENT_CONFIRMATION', dataset_id='parent',
        manifest_sha256=grant['manifest_sha256'], source_name='events.json')
    with pytest.raises(FinalTestAccessViolation):
        source_guard.check_event_metadata({'effective_date': days[70], 'payment_date': 20261231})
    # 未选择的历史权利仍验证整源范围，但不能阻断当前输出窗。
    root, manifest, _, _ = provider._datasets['parent']
    rows = json.loads((root / 'events.json').read_text(encoding='utf-8'))
    rows.append({**rows[0], 'event_id': 'UNRELATED_EARLIER_RIGHTS',
                 'record_date': days[0], 'effective_date': days[1], 'payment_date': days[2],
                 'source_published_at': str(days[0])})
    write(root / 'events.json', rows)
    manifest['files']['events.json']['sha256'] = sha(root / 'events.json')
    write(root / 'parent_manifest.json', manifest)
    new_provider = UniverseDataProviderV1({'synthetic': root})
    new_provider.register('parent', 'synthetic', 'parent_manifest.json')
    grant['manifest_sha256'] = new_provider._datasets['parent'][2]
    grant['sources']['events.json']['sha256'] = sha(root / 'events.json')
    new_provider.trusted_access_authority = TrustedResearchDataAccessAuthorityV1(lambda ref: deepcopy(grant))
    new_scope = new_provider.authorize_independent_scope('parent', authorization_ref='deployed-ref',
        feature_start=days[10], account_end=days[70], recipe_version='INDEPENDENT_DATA_V1',
        protocol_binding=protocol)
    prepared = new_provider.prepare('parent', **{**args, 'trusted_scope': new_scope})
    assert [row['event_id'] for row in prepared['bundle']['events']] == ['FUTURE_CROSS_RIGHTS']


def renew_scope(provider, args, grant, *, output_limit):
    grant['max_output_bytes'] = output_limit
    scope = provider.authorize_independent_scope('parent', authorization_ref='deployed-ref',
        feature_start=args['feature_start'], account_end=args['account_end'],
        recipe_version='INDEPENDENT_DATA_V1', protocol_binding={key: grant[key] for key in (
            'protocol_id', 'protocol_sha256', 'independent_evidence_id', 'independent_evidence_sha256')})
    return scope, provider.prepare('parent', **{**args, 'trusted_scope': scope})


@pytest.mark.parametrize('delta', [0, -1])
def test_independent_freeze_enforces_exact_published_bytes_including_utf8_input(tmp_path, delta):
    from chanlun_trader.research_factory.universe_submission_v1 import freeze_universe_bundle
    provider, args, _, grant, scope, _ = independent_case(tmp_path)
    probe = tmp_path / '基准目录'
    probe.mkdir()
    freeze_universe_bundle(provider.prepare('parent', **args), probe, trusted_scope=scope)
    required = sum(path.stat().st_size for path in probe.iterdir())
    scope, prepared = renew_scope(provider, args, grant, output_limit=required + delta)
    output = tmp_path / '正式目录'
    output.mkdir()
    if delta:
        with pytest.raises(ValueError, match='TRUSTED_DATA_OUTPUT_BYTE_LIMIT_EXCEEDED'):
            freeze_universe_bundle(prepared, output, trusted_scope=scope)
        assert list(output.iterdir()) == []
    else:
        freeze_universe_bundle(prepared, output, trusted_scope=scope)
        assert sum(path.stat().st_size for path in output.iterdir()) == required


def test_independent_adopt_counts_target_input_path_bytes_and_rejects_before_copy(tmp_path, monkeypatch):
    from chanlun_trader.research_factory import universe_submission_v1 as frozen
    provider, args, _, grant, scope, _ = independent_case(tmp_path)
    probe = tmp_path / 'p'
    probe.mkdir()
    frozen.freeze_universe_bundle(provider.prepare('parent', **args), probe, trusted_scope=scope)
    required = sum(path.stat().st_size for path in probe.iterdir())
    scope, prepared = renew_scope(provider, args, grant, output_limit=required)
    source = tmp_path / 's'
    source.mkdir()
    input_path, _ = frozen.freeze_universe_bundle(prepared, source, trusted_scope=scope)
    target = tmp_path / 't'
    target.mkdir()
    _, adopted_path, _ = frozen.adopt_frozen_universe_bundle(input_path, target,
        input_identity=prepared['input_identity'], snapshot_sha256=sha(input_path), trusted_scope=scope)
    assert sum(path.stat().st_size for path in target.iterdir()) == required
    value = json.loads(adopted_path.read_text(encoding='utf-8'))
    assert frozen.restore_universe_bundle(value, adopted_path, trusted_scope=scope)['input_identity'] == prepared['input_identity']
    too_long = tmp_path / 'adoption-path-needs-more-metadata-bytes'
    too_long.mkdir()
    monkeypatch.setattr(frozen.shutil, 'copyfile', lambda *a: pytest.fail('超额目标不得复制行情'))
    with pytest.raises(ValueError, match='TRUSTED_DATA_OUTPUT_BYTE_LIMIT_EXCEEDED'):
        frozen.adopt_frozen_universe_bundle(input_path, too_long,
            input_identity=prepared['input_identity'], snapshot_sha256=sha(input_path), trusted_scope=scope)
    assert list(too_long.iterdir()) == [] and input_path.is_file()
