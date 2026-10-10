"""TRAIN 正式路由的元信息入场；合成 fixture 不代表业务验收。"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pandas as pd
import pytest

from chanlun_trader.research_factory.campaign_scope_v1 import OwnerApprovalStoreV1
from chanlun_trader.research_factory.research_universe_v1 import identity
from chanlun_trader.research_factory.train_projection_admission_v1 import verify_train_projection_admission
from chanlun_trader.research_factory.universe_data_provider_v1 import UniverseDataProviderV1
from scripts.project_research_train_v1 import build_train_projection
from test_research_dataset_projection_v1 import prepare, sha, write
from test_train_projection_cli_v1 import deployment


def iso(day):
    text = str(day)
    return f'{text[:4]}-{text[4:6]}-{text[6:]}'


def admission_case(tmp_path):
    path, config, days, args, approvals, data_approval_ref = deployment(tmp_path)
    result = build_train_projection(path, sha(path), 'parent').project('parent', **args)
    child = Path(result['manifest_path']).parent
    provider = UniverseDataProviderV1({'train': child})
    provider.register('train', 'train', Path(result['manifest_path']).name)
    _, manifest, digest, universe = provider._datasets['train']
    route = {'route_id': 'OFFICIAL_TRAIN', 'producer_identity': 'FIXED_TRAIN_PROJECTOR',
        'source_ids': sorted({row['source_id'] for row in manifest['files'].values()}),
        'start': iso(days[10]), 'end': iso(days[70]), 'purpose': 'TRAIN',
        'universe_hash': universe.universe_identity,
        'quality_policy': {'account_audit': 'BOUNDED_PROVIDER_ACCOUNT_AUDIT'}}
    summary = {'schema_version': 'OWNER_TRAIN_PROJECTION_ADMISSION_V1',
        'research_contract_hash': 'a' * 64, 'trusted_route': route, 'dataset_id': 'train',
        'manifest_sha256': digest, 'receipt_sha256': sha(result['receipt_path']),
        'projection_deployment': {'path': str(path), 'sha256': sha(path)},
        'authorization_ref': 'deployed-ref',
        'window': {'feature_start': iso(days[10]), 'account_start': iso(days[60]), 'account_end': iso(days[70])}}
    arguments = {'approvals': OwnerApprovalStoreV1(approvals.directory),
        'expected_contract_hash': summary['research_contract_hash'], 'expected_route': deepcopy(route),
        'provider': provider, 'expected_projection_deployment': deepcopy(summary['projection_deployment'])}
    reference = publish_admission(tmp_path, approvals, summary)
    return reference, arguments, summary, approvals, result, config, data_approval_ref, days


def publish_admission(tmp_path, approvals, summary):
    reference = approvals.approve(summary, approver='SYNTHETIC_OWNER', capability=approvals._owner_capability)
    path = tmp_path / 'admission.json'
    write(path, summary)
    return {'path': str(path), 'sha256': sha(path), 'approval_ref': reference}


def update_receipt(result, mutate):
    path = Path(result['receipt_path'])
    receipt = json.loads(path.read_text(encoding='utf-8'))
    mutate(receipt)
    receipt['receipt_id'] = identity({key: value for key, value in receipt.items() if key != 'receipt_id'})
    write(path, receipt)


def test_verified_owner_projection_proof_never_opens_parent_or_child_market(tmp_path, monkeypatch):
    ref, args, summary, _, result, config, _, days = admission_case(tmp_path)
    parent = Path(config['registered_parents']['parent']['root'])
    child = Path(result['manifest_path']).parent
    ordinary_open = Path.open
    def metadata_only(path, *a, **kw):
        assert not path.is_relative_to(parent), '入场不能回开父资料'
        if path.is_relative_to(child):
            assert path.name in {'manifest_train_v1.json', 'PROJECTION_RECEIPT.json'}, '资格留给 bounded worker'
        return ordinary_open(path, *a, **kw)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'open', metadata_only)
        proof = verify_train_projection_admission(ref, **args)
        assert proof == verify_train_projection_admission(ref, **args)
    assert proof['schema_version'] == 'VERIFIED_TRAIN_PROJECTION_ADMISSION_V1'
    assert proof['dataset_hash'] == summary['manifest_sha256']
    assert proof['request_fields'] == {'dataset_id': 'train', **summary['window'], 'purpose': 'EXPLORATORY'}
    assert proof['target_count'] == 3 and proof['data_content_read'] is False
    assert proof['account_data_ready'] is False and proof['historical_independence'] == 'UNKNOWN'
    assert proof['independent_confirmation_eligible'] is False
    assert proof['proof_id'] == identity({key: value for key, value in proof.items() if key != 'proof_id'})
    assert prepare(result, days)['qualification']['account_data_ready']


@pytest.mark.parametrize('changed', ['contract', 'route', 'deployment', 'bool', 'self_approved', 'extra_module'])
def test_request_data_cannot_replace_fixed_owner_bindings(tmp_path, changed):
    ref, args, summary, approvals, _, _, _, _ = admission_case(tmp_path)
    if changed == 'contract':
        args['expected_contract_hash'] = 'b' * 64
    elif changed == 'route':
        args['expected_route']['producer_identity'] = 'ARBITRARY_PRODUCER'
    elif changed == 'deployment':
        args['expected_projection_deployment']['sha256'] = 'b' * 64
    elif changed == 'bool':
        ref = True
    elif changed == 'self_approved':
        ref['approval_ref'] = {'approval_id': 'f' * 64, 'summary_hash': 'f' * 64}
    else:
        summary['resolver_module'] = 'arbitrary:grant'
        ref = publish_admission(tmp_path, approvals, summary)
    with pytest.raises((ValueError, PermissionError)):
        verify_train_projection_admission(ref, **args)


@pytest.mark.parametrize('changed', ['admission', 'projection_deployment', 'data_deployment', 'authorization',
                                    'child_manifest', 'receipt'])
def test_every_fixed_byte_pin_is_rechecked(tmp_path, changed):
    ref, args, _, _, result, config, _, _ = admission_case(tmp_path)
    paths = {'admission': ref['path'], 'projection_deployment': args['expected_projection_deployment']['path'],
        'data_deployment': config['trusted_data_deployment']['path'],
        'child_manifest': result['manifest_path'], 'receipt': result['receipt_path']}
    data_config = json.loads(Path(config['trusted_data_deployment']['path']).read_text(encoding='utf-8'))
    paths['authorization'] = data_config['records']['deployed-ref']['path']
    path = Path(paths[changed])
    path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(ValueError):
        verify_train_projection_admission(ref, **args)


@pytest.mark.parametrize('which', ['admission', 'preparation'])
def test_original_owner_and_admission_owner_revocation_both_deny(tmp_path, which):
    ref, args, _, approvals, _, _, data_ref, _ = admission_case(tmp_path)
    approval = ref['approval_ref'] if which == 'admission' else data_ref
    (approvals.directory / (approval['approval_id'] + '.json')).unlink()
    with pytest.raises(PermissionError, match='OWNER_APPROVAL_NOT_REGISTERED'):
        verify_train_projection_admission(ref, **args)


@pytest.mark.parametrize('changed', ['recipe', 'denominator', 'authorization_id', 'independence', 'input_quota'])
def test_even_owner_pinned_receipt_must_match_actual_projection_and_grant(tmp_path, changed):
    ref, args, summary, approvals, result, _, _, _ = admission_case(tmp_path)
    def mutate(receipt):
        if changed == 'recipe':
            receipt['recipe_sha256'] = 'b' * 64
        elif changed == 'denominator':
            receipt['target_count'] = 2
            receipt['target_symbols'] = receipt['target_symbols'][:2]
        elif changed == 'authorization_id':
            receipt['authorization_id'] = 'OTHER_AUTHORIZATION'
        elif changed == 'independence':
            receipt['independent_confirmation_eligible'] = True
        else:
            receipt['input_bytes'] = 99_000_000
    update_receipt(result, mutate)
    summary['receipt_sha256'] = sha(result['receipt_path'])
    ref = publish_admission(tmp_path, approvals, summary)
    with pytest.raises(ValueError):
        verify_train_projection_admission(ref, **args)


@pytest.mark.parametrize('changed', ['source_ids', 'universe', 'account_start', 'future_purpose'])
def test_owner_route_still_must_match_real_full_universe_sources_and_original_window(tmp_path, changed):
    _, args, summary, approvals, _, _, _, days = admission_case(tmp_path)
    if changed == 'source_ids':
        summary['trusted_route']['source_ids'].pop()
    elif changed == 'universe':
        summary['trusted_route']['universe_hash'] = 'b' * 64
    elif changed == 'account_start':
        summary['window']['account_start'] = iso(days[61])
    else:
        summary['trusted_route']['purpose'] = 'INDEPENDENT_VALIDATION'
    args['expected_route'] = deepcopy(summary['trusted_route'])
    ref = publish_admission(tmp_path, approvals, summary)
    with pytest.raises(ValueError):
        verify_train_projection_admission(ref, **args)


def test_metadata_proof_does_not_bypass_actual_provider_content_qualification(tmp_path):
    ref, args, _, _, result, _, _, days = admission_case(tmp_path)
    root, manifest, _, _ = args['provider']._datasets['train']
    price_name = next(name for name, row in manifest['files'].items() if row['kind'] == 'DAILY')
    price = pd.read_parquet(root / price_name)
    price.loc[price.index[0], 'close'] += .01
    price.to_parquet(root / price_name, index=False)
    proof = verify_train_projection_admission(ref, **args)
    assert not proof['account_data_ready']
    assert proof['qualification_required'] == 'BOUNDED_PROVIDER_ACCOUNT_AUDIT'
    with pytest.raises(ValueError, match='DATA_SOURCE_CONTENT_CHANGED'):
        prepare(result, days)


def test_owner_pinned_manifest_cannot_rewrite_inherited_source_units(tmp_path):
    _, args, summary, approvals, result, _, _, _ = admission_case(tmp_path)
    manifest_path = Path(result['manifest_path'])
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    price = next(row for row in manifest['files'].values() if row['kind'] == 'DAILY')
    price['evidence']['unit_evidence']['volume_unit'] = 'FORGED_UNIT'
    write(manifest_path, manifest)
    update_receipt(result, lambda receipt: receipt.update(manifest_sha256=sha(manifest_path)))
    summary.update(manifest_sha256=sha(manifest_path), receipt_sha256=sha(result['receipt_path']))
    ref = publish_admission(tmp_path, approvals, summary)
    provider = UniverseDataProviderV1({'train': manifest_path.parent})
    provider.register('train', 'train', manifest_path.name)
    args['provider'] = provider
    with pytest.raises(ValueError, match='SOURCE_UNIT_PROVENANCE_CONFLICT'):
        verify_train_projection_admission(ref, **args)


def test_admission_checks_actual_child_output_byte_limit_using_stat(tmp_path):
    ref, args, _, _, _, _, _, _ = admission_case(tmp_path)
    root, manifest, _, _ = args['provider']._datasets['train']
    price_name = next(name for name, row in manifest['files'].items() if row['kind'] == 'DAILY')
    with (root / price_name).open('r+b') as output:
        output.truncate(10_000_001)
    with pytest.raises(ValueError, match='OUTPUT_BYTE_LIMIT_EXCEEDED'):
        verify_train_projection_admission(ref, **args)


def test_missing_admission_has_uniform_error_and_private_waitable_cause(tmp_path):
    ref, args, _, _, _, _, _, _ = admission_case(tmp_path)
    Path(ref['path']).unlink()
    with pytest.raises(ValueError, match='^TRAIN_ADMISSION_REFERENCE_INVALID:admission$') as error:
        verify_train_projection_admission(ref, **args)
    assert isinstance(error.value.__cause__, FileNotFoundError)


def test_actual_verified_projection_proof_delegates_legal_child_window_only(tmp_path):
    from chanlun_trader.research_factory.campaign_scope_v1 import (
        CampaignScopeV1, bind_campaign_scope, scope_summary,
    )
    from chanlun_trader.research_factory.research_campaign_v1 import ResearchCampaignV1
    from chanlun_trader.research_factory.research_capabilities_v1 import capabilities
    from chanlun_trader.research_factory.run_budget import AutonomousRunBudgetV2
    from test_continuous_research_contract_v1 import build, scope

    _, args, summary, approvals, _, _, _, days = admission_case(tmp_path)
    snapshot = capabilities()
    frozen_scope = scope(snapshot, confirmation=False)
    manifest = args['provider']._datasets['train'][1]
    frozen_scope.update(universe_id=manifest['universe_id'],
                        universe_hash=summary['trusted_route']['universe_hash'])
    frozen_scope['data_routes']['EXPLORATION'] = deepcopy(summary['trusted_route'])
    contract = build(snapshot, frozen_scope=frozen_scope)
    summary['research_contract_hash'] = contract['content_hash']
    args['expected_contract_hash'] = contract['content_hash']
    reference = publish_admission(tmp_path, approvals, summary)
    proof = verify_train_projection_admission(reference, **args)
    root = tmp_path / 'campaign'
    root.mkdir()
    units = {name: 10000 for name in AutonomousRunBudgetV2.resource_names}
    stages = {stage: {name: amount // 2 for name, amount in units.items()}
              for stage in ('EXPLORATION', 'CONFIRMATION')}
    authorization = {'authorization_id': 'SYNTHETIC_PROJECTED_TRAIN',
        'objective_id': contract['objective_id'], 'resource_limits': units, 'stages': list(stages),
        'expires_at': (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
        'max_batches': 2, 'max_total_predictive_trials': 2, 'max_trials_per_batch': 2,
        'max_hypotheses_per_batch': 2, 'max_candidates_per_batch': 2,
        'execution_profiles': contract['scope']['execution_profiles']}
    campaign_summary = scope_summary(root, authorization, contract, stages)
    campaign_ref = approvals.approve(campaign_summary, approver='SYNTHETIC_OWNER',
                                     capability=approvals._owner_capability)
    authorization = bind_campaign_scope(root, authorization, contract=contract, stage_limits=stages,
        approvals=approvals, approval_ref=campaign_ref)
    campaign = ResearchCampaignV1.create(root, authorization)
    service = CampaignScopeV1(campaign)
    request = {key: deepcopy(contract['scope'][key]) for key in ('initial_cash', 'max_positions',
        'universe_id', 'universe_hash', 'rule_version', 'cost_profiles', 'execution_profiles')}
    request.update(proof['request_fields'], phase='EXPLORATION', dataset_hash=proof['dataset_hash'],
        feature_start=iso(days[20]), account_start=iso(days[65]), account_end=iso(days[69]),
        data_route_proof=proof)
    before = deepcopy(campaign.peek_status()['used'])
    arguments = {'batch_id': 'SYNTHETIC_PROJECTED_BATCH', 'candidate_identity': identity('candidate'),
                 'phase': 'EXPLORATION', 'expires_at': authorization['expires_at']}
    delegated = service.delegate(request=request, **arguments)
    assert service.resolve(delegated)['request'] == request
    assert campaign.peek_status()['used'] == before
    for key, outside in (('feature_start', days[9]), ('account_start', days[59]), ('account_end', days[71])):
        changed = deepcopy(request)
        changed[key] = iso(outside)
        with pytest.raises(PermissionError):
            service.delegate(request=changed, **arguments)
    arbitrary = publish_admission(tmp_path, approvals, {'schema_version': 'OWNER_APPROVED_JSON',
        'dataset_id': 'train', 'manifest_sha256': proof['dataset_hash'], 'confirmed': True})
    changed = deepcopy(request)
    changed['data_route_proof']['admission_reference'] = arbitrary
    with pytest.raises(PermissionError, match='TRAIN_PROJECTION_ROUTE_CONFLICT'):
        service.delegate(request=changed, **arguments)
