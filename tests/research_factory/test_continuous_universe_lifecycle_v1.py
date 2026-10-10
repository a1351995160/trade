"""隔离部署与公共控制测试；不调用收费模型或实际行情资料。"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from scripts.lifecycle_deployment_v2 import (
    approve_continuous_evidence, approve_continuous_scope, build_lifecycle_service, load_continuous_research,
)
from chanlun_trader.research_factory.campaign_scope_v1 import OwnerApprovalStoreV1
from chanlun_trader.research_factory.run_budget import AutonomousRunBudgetV2
from chanlun_trader.research_factory.research_capabilities_v1 import capabilities
from chanlun_trader.research_factory.trusted_research_host_v1 import TrustedResearchHostV1
from chanlun_trader.research_factory.universe_execution_profile_v1 import SEGMENTED_PROFILE, execution_profile
from chanlun_trader.research_factory.universe_research_report_v2 import default_observation_plan
from test_continuous_research_contract_v1 import build


def deployment(root):
    contract = build(capabilities())
    units = {name: 10000 for name in AutonomousRunBudgetV2.resource_names}
    stages = {stage: {name: value // 2 for name, value in units.items()}
              for stage in ('EXPLORATION', 'CONFIRMATION')}
    authorization = {'authorization_id': 'continuous_fixture', 'objective_id': contract['objective_id'],
        'resource_limits': units, 'stages': list(stages),
        'expires_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        'max_batches': 12, 'max_total_predictive_trials': 24, 'max_trials_per_batch': 2,
        'max_hypotheses_per_batch': 2, 'max_candidates_per_batch': 2,
        'execution_profiles': contract['scope']['execution_profiles']}
    template = {'version': 'FULL_UNIVERSE_SUBMISSION_V4', 'phase': 'EXPLORATION',
        'dataset_id': 'fixture_not_read', 'universe_id': contract['scope']['universe_id'],
        'account_scope': 'DATA_QUALIFIED', 'feature_start': '2020-01-01',
        'account_start': '2021-01-01', 'account_end': '2021-12-31', 'initial_cash': 50000,
        'max_positions': 5, 'max_symbol_exposure_bps': 10000, 'costs': ['BASE', 'STRESS'],
        'benchmark': None, 'purpose': 'EXPLORATORY', 'authorization_ref': 'registered_parent',
        'execution_profile': execution_profile(SEGMENTED_PROFILE, 252)}
    template['observation_plan'] = default_observation_plan(template)
    return {'bindings': {}, 'continuous': {
        'submission': {'version': 'FULL_UNIVERSE_DEPLOYMENT_V1', 'roots': {}, 'datasets': [],
                       'authorizations': {}, 'output_root': 'execution'},
        'owner_approval': {'store_root': str(root / 'owner'), 'token_env': 'FIXTURE_OWNER_TOKEN',
                           'token_sha256': hashlib.sha256(b'isolated-fixture-token').hexdigest()},
        'researches': {'fixed': {'campaign_root': str(root), 'authorization': authorization,
            'contract': contract, 'stage_limits': stages, 'template': template,
            'model_limits': {'timeout_seconds': 1, 'max_tokens': 100, 'max_cost_microunits': 100,
                             'context_max_bytes': 200000}, 'candidates_per_batch': 1}}}}


def approved(root, monkeypatch):
    config = deployment(root)
    monkeypatch.setenv('FIXTURE_OWNER_TOKEN', 'isolated-fixture-token')
    receipt = approve_continuous_scope(root, config, 'fixed', approver='isolated_fixture_owner')
    controller = build_lifecycle_service(root, config).continuous
    controller.create('fixed', approval_ref=receipt['approval_ref'])
    return config, controller, receipt


def files(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*') if path.is_file()}


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('trusted', [False, True])
def test_continuous_permissions_match_deployed_feature(tmp_path, enabled, trusted):
    from chanlun_trader.execution_policy import ExecutionPolicy
    config = deployment(tmp_path) if enabled else {'bindings': {}}
    service = build_lifecycle_service(tmp_path, config)
    assert (service.continuous is not None) is enabled
    policy = ExecutionPolicy(mode='GOVERNED', workspace_kind='EXTERNAL', allow_trusted_research=trusted)
    expected = {'create_binding_ids': [], 'job_ids': []}
    if enabled:
        expected['continuous_ids'] = ['fixed'] if trusted else []
    before = files(tmp_path)
    assert service.operation_permissions(policy) == expected
    assert files(tmp_path) == before


def test_no_owner_capability_no_authorization_and_read_only_status(tmp_path, monkeypatch):
    config = deployment(tmp_path)
    monkeypatch.delenv('FIXTURE_OWNER_TOKEN', raising=False)
    service = build_lifecycle_service(tmp_path, config)
    before = files(tmp_path)
    assert service.continuous.status('fixed')['status'] == 'OWNER_APPROVAL_REQUIRED'
    assert service.continuous.preview('fixed')['model_readiness'] == 'HARD_BUDGET_UNSUPPORTED'
    preflight = service.continuous.preview('fixed')['preflight']
    assert preflight['schema_version'] == 'CONTINUOUS_RESEARCH_PREFLIGHT_V1'
    assert preflight['data_content_read'] is False and preflight['dispatched_operations'] == 0
    assert all(row['available_account_sessions'] is None for row in preflight['channels'].values())
    assert preflight['model_hard_budget_evidence'] == 'NOT_QUERIED'
    assert files(tmp_path) == before
    with pytest.raises(PermissionError, match='OWNER_CHANNEL_REQUIRED'):
        approve_continuous_scope(tmp_path, config, 'fixed', approver='caller')
    with pytest.raises(ValueError, match='ACTION_FIELDS'):
        service.continuous.perform('fixed', 'create', {'confirmed': True})
    with pytest.raises(PermissionError, match='OWNER_APPROVAL_REQUIRED'):
        service.continuous.approvals.approve({'confirmed': True}, approver='caller', capability=object())
    assert files(tmp_path) == before


def test_same_builder_restores_fixed_config_and_default_model_fails_closed(tmp_path, monkeypatch):
    config, controller, _ = approved(tmp_path, monkeypatch)
    research = load_continuous_research(tmp_path, config, 'fixed')
    assert research.config()['candidates_per_batch'] == 1
    assert research.config()['invoker_deployment_identity'] == {'kind': 'CODEX_HARD_BUDGET_UNSUPPORTED'}
    assert research.campaign.peek_status()['authorization']['max_batches'] == 12
    before = files(tmp_path)
    assert controller.status('fixed')['goal_complete'] is False
    research.handover(design=True)
    assert files(tmp_path) == before
    controller.perform('fixed', 'start')
    state = controller.status('fixed')
    assert state['status'] == 'WAITING_MODEL'
    assert state['waiting_reason'] == 'HARD_BUDGET_UNSUPPORTED'
    result = controller.perform('fixed', 'advance')
    assert result['goal_complete'] is False
    assert research.campaign.peek_status()['used']['model_calls'] == 0
    assert not list(tmp_path.rglob('INVOCATION.json'))
    assert not list(tmp_path.rglob('*_START.json'))
    assert not list(tmp_path.rglob('MODEL_GUARANTEE.json'))
    changed = deepcopy(config)
    changed['continuous']['researches']['fixed']['model_limits']['max_tokens'] += 1
    with pytest.raises(PermissionError, match='RESEARCH_CHANGED:model_limits'):
        load_continuous_research(tmp_path, changed, 'fixed')


def test_fixed_train_references_and_existing_history_restore_without_train_artifact_reads(tmp_path, monkeypatch):
    config = deployment(tmp_path)
    registration = config['continuous']['researches']['fixed']
    history = tmp_path / 'SYNTHETIC_HISTORICAL_METADATA.json'
    history.write_text('{"fixture":"HISTORICAL_METADATA_ONLY"}', encoding='utf-8')
    registration['history_references'] = [{'path': str(history),
        'sha256': hashlib.sha256(history.read_bytes()).hexdigest(), 'source': 'SESSION_AI_MANUAL', 'metering': 'UNKNOWN'}]
    registration['exploration_admission'] = {'path': str(tmp_path / 'not_deployed_train_admission.json'),
        'sha256': '4' * 64, 'approval_ref': {'approval_id': '5' * 64, 'summary_hash': '6' * 64}}
    registration['train_projection_deployment'] = {'path': str(tmp_path / 'not_deployed_train_route.json'),
        'sha256': '7' * 64}
    monkeypatch.setenv('FIXTURE_OWNER_TOKEN', 'isolated-fixture-token')
    receipt = approve_continuous_scope(tmp_path, config, 'fixed', approver='isolated_owner')
    controller = build_lifecycle_service(tmp_path, config).continuous
    controller.create('fixed', approval_ref=receipt['approval_ref'])
    research = load_continuous_research(tmp_path, config, 'fixed')
    assert research.config()['history_references'] == registration['history_references']
    assert research.config()['deployment_admission_refs'] == {key: registration[key]
        for key in ('exploration_admission', 'train_projection_deployment')}
    assert callable(research.submission.exploration_admission)
    before = files(tmp_path)
    controller.preview('fixed'); controller.status('fixed'); research.handover(design=True)
    assert files(tmp_path) == before
    for field in ('history_references', 'exploration_admission', 'train_projection_deployment'):
        changed = deepcopy(config)
        target = changed['continuous']['researches']['fixed'][field]
        (target[0] if isinstance(target, list) else target)['sha256'] = '8' * 64
        with pytest.raises(PermissionError, match='RESEARCH_CHANGED'):
            load_continuous_research(tmp_path, changed, 'fixed')
def test_owner_reference_is_content_bound_and_cannot_start_unregistered_id(tmp_path, monkeypatch):
    config = deployment(tmp_path)
    monkeypatch.setenv('FIXTURE_OWNER_TOKEN', 'wrong')
    with pytest.raises(PermissionError, match='OWNER_CHANNEL_REQUIRED'):
        approve_continuous_scope(tmp_path, config, 'fixed', approver='fixture')
    monkeypatch.setenv('FIXTURE_OWNER_TOKEN', 'isolated-fixture-token')
    receipt = approve_continuous_scope(tmp_path, config, 'fixed', approver='fixture')
    changed = deepcopy(config)
    changed['continuous']['researches']['fixed']['authorization']['resource_limits']['model_tokens'] += 1
    with pytest.raises(PermissionError, match='OWNER_APPROVAL_BINDING'):
        build_lifecycle_service(tmp_path, changed).continuous.create('fixed', approval_ref=receipt['approval_ref'])
    with pytest.raises(ValueError, match='NOT_REGISTERED'):
        build_lifecycle_service(tmp_path, config).continuous.perform('caller_chosen', 'start')


def test_host_never_spontaneously_starts_created_research_and_pause_is_sticky(tmp_path, monkeypatch):
    config, controller, _ = approved(tmp_path, monkeypatch)
    service = build_lifecycle_service(tmp_path, config)
    host = TrustedResearchHostV1(service)
    before = files(tmp_path)
    assert host.tick()['continuous:fixed']['started'] is False
    assert files(tmp_path) == before
    controller.perform('fixed', 'start')
    controller.perform('fixed', 'pause', {'reason': 'fixture_pause'})
    before = files(tmp_path)
    assert host.tick()['continuous:fixed']['status'] == 'PAUSED'
    assert files(tmp_path) == before
    controller.perform('fixed', 'revoke', {'reason': 'fixture_revoke'})
    with pytest.raises(PermissionError, match='REVOKED'):
        controller.perform('fixed', 'resume', {'reason': 'cannot_revive'})


def test_confirmation_reference_is_not_dereferenced_by_status_or_handover(tmp_path, monkeypatch):
    config = deployment(tmp_path)
    config['continuous']['researches']['fixed']['confirmation'] = {'admission': {
        'path': str(tmp_path / 'not_deployed_admission.json'), 'sha256': '0' * 64,
        'approval_ref': {'approval_id': '1' * 64, 'summary_hash': '2' * 64}}}
    monkeypatch.setenv('FIXTURE_OWNER_TOKEN', 'isolated-fixture-token')
    receipt = approve_continuous_scope(tmp_path, config, 'fixed', approver='fixture')
    service = build_lifecycle_service(tmp_path, config)
    service.continuous.create('fixed', approval_ref=receipt['approval_ref'])
    before = files(tmp_path)
    assert service.continuous.status('fixed')['goal_complete'] is False
    assert service.continuous.perform('fixed', 'handover')['independent_results_omitted'] is True
    assert files(tmp_path) == before


def test_owner_artifact_channel_does_not_accept_arbitrary_boolean_approvals(tmp_path, monkeypatch):
    config = deployment(tmp_path)
    monkeypatch.delenv('FIXTURE_OWNER_TOKEN', raising=False)
    with pytest.raises(PermissionError, match='OWNER_CHANNEL_REQUIRED'):
        approve_continuous_evidence(config, 'fixed', {'schema_version': 'PINNED_INDEPENDENT_ADMISSION_V1'}, approver='fixture')
    monkeypatch.setenv('FIXTURE_OWNER_TOKEN', 'isolated-fixture-token')
    with pytest.raises(ValueError, match='EVIDENCE_SCHEMA_INVALID'):
        approve_continuous_evidence(config, 'fixed', {'confirmed': True, 'source_authenticated': True}, approver='fixture')


def test_admission_rejects_self_reported_authentication_even_after_owner_file_registration(tmp_path):
    from types import SimpleNamespace
    from chanlun_trader.research_factory.continuous_universe_lifecycle_v1 import PinnedIndependentAdmissionV1
    capability = object()
    store = OwnerApprovalStoreV1(tmp_path / 'owner', owner_capability=capability)
    value = {'schema_version': 'PINNED_INDEPENDENT_ADMISSION_V1',
        'snapshot_store_root': str(tmp_path / 'snapshots'), 'snapshot_ids': [], 'calendar': [],
        'request_fields': {}, 'qualification': {}, 'trusted_data_access': {}, 'source_authenticated': True}
    raw = json.dumps(value).encode()
    path = tmp_path / 'admission.json'
    path.write_bytes(raw)
    ref = {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(),
        'approval_ref': store.approve(value, approver='isolated_owner', capability=capability)}
    loader = PinnedIndependentAdmissionV1(tmp_path, ref, approvals=OwnerApprovalStoreV1(store.directory),
        submission=SimpleNamespace(), protocol_path=tmp_path / 'missing_protocol.json')
    with pytest.raises(ValueError, match='ADMISSION_SCHEMA_INVALID'):
        loader({})
    missing_ref = {**ref, 'path': str(tmp_path / 'not_deployed.json')}
    missing = PinnedIndependentAdmissionV1(tmp_path, missing_ref, approvals=OwnerApprovalStoreV1(store.directory),
        submission=SimpleNamespace(), protocol_path=tmp_path / 'missing_protocol.json')
    assert missing({}) is None
    assert missing.last_waiting_reason == 'PINNED_INDEPENDENT_ARTIFACT_NOT_DEPLOYED'


def test_pinned_admission_rechecks_actual_store_and_synthetic_is_never_real_source(tmp_path):
    from types import SimpleNamespace
    from chanlun_trader.research_factory.common import stable_hash
    from chanlun_trader.research_factory.continuous_universe_lifecycle_v1 import PinnedIndependentAdmissionV1
    from chanlun_trader.research_factory.forward_snapshot_v1 import SnapshotStoreV1, universe_snapshot_projection
    from chanlun_trader.research_factory.research_data_qualification_v1 import audit_data_qualification
    from test_business_validation_protocol_v1 import SYMBOLS, days, payload
    calendar = days(2)
    snapshots = SnapshotStoreV1(tmp_path / 'snapshots')
    ids = []
    for index, day in enumerate(calendar):
        for phase in ('CLOSE',) if index == 0 else ('OPEN', 'CLOSE'):
            text = str(day)
            value = snapshots.record_synthetic(phase=phase, market_date=day, payload=payload(day),
                received_at=f'{text[:4]}-{text[4:6]}-{text[6:]}T15:01:00+08:00')
            ids.append(value['snapshot_id'])
    contract = build(capabilities())
    protocol = {'protocol_identity': stable_hash('synthetic_protocol'), 'frozen_at': '2026-10-10T00:00:00+00:00',
                'preview': {'profile': 'SYNTHETIC', 'contract': contract}}
    protocol_path = tmp_path / 'PROTOCOL.json'
    protocol_path.write_text(json.dumps(protocol), encoding='utf-8')
    projection = universe_snapshot_projection(snapshots, ids, target_symbols=SYMBOLS, calendar=calendar,
        feature_start=calendar[0], account_start=calendar[1], account_end=calendar[1],
        frozen_at=protocol['frozen_at'], profile='SYNTHETIC')
    assert projection['ready_for_registered_data_preparation'] is True
    digest = stable_hash('synthetic_registered_manifest')
    metadata = {'dataset_id': 'confirmation', 'content_hash': digest, 'source': 'SYNTHETIC',
        'captured_at': '2027-01-05T15:01:00+08:00', 'fields': ['open', 'close'],
        'units': {'open': 'CNY', 'close': 'CNY'}, 'adjustment': 'RAW', 'state_basis': 'SYNTHETIC',
        'corporate_action_basis': 'EXPLICIT', 'availability_basis': 'RECEIVED_AT',
        'symbols': SYMBOLS, 'window': {'start': calendar[0], 'end': calendar[-1]}}
    qualification = audit_data_qualification([metadata], [])
    capability = object()
    owner = OwnerApprovalStoreV1(tmp_path / 'owner', owner_capability=capability)
    def pin(name, value, approve=False):
        path = tmp_path / name
        raw = json.dumps(value).encode()
        path.write_bytes(raw)
        ref = {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest()}
        if approve:
            ref['approval_ref'] = owner.approve(value, approver='isolated_owner', capability=capability)
        return ref
    qualification_ref = pin('QUALIFICATION.json', qualification)
    review = {'schema_version': 'CANONICAL_INDEPENDENT_DATA_REVIEW_V1',
        'protocol_identity': protocol['protocol_identity'], 'protocol_sha256': hashlib.sha256(protocol_path.read_bytes()).hexdigest(),
        'dataset_id': 'confirmation', 'manifest_sha256': digest,
        'snapshot_projection_hash': projection['projection_hash'], 'snapshot_refs_identity': stable_hash(projection['snapshot_refs']),
        'qualification_report_hash': qualification['report_hash'], 'exposure_records_hash': qualification['exposure_records_hash'],
        'review_method': 'OWNER_PRIOR_ACCESS_REVIEW_AND_REGISTERED_SOURCE_PROVENANCE_V1',
        'review_outcome': 'APPROVED_FUTURE_UNSEEN', 'reviewed_at': '2027-01-05T16:00:00+08:00',
        'evidence_refs': [qualification_ref], 'trusted_route': contract['scope']['data_routes']['CONFIRMATION']}
    review_ref = pin('REVIEW.json', review, True)
    fields = {'dataset_id': 'confirmation', 'feature_start': calendar[0], 'account_start': calendar[1],
        'account_end': calendar[1], 'execution_profile': {}, 'observation_plan': {},
        'universe_id': contract['scope']['universe_id']}
    admission = {'schema_version': 'PINNED_INDEPENDENT_ADMISSION_V1', 'snapshot_store_root': str(snapshots.root),
        'snapshot_ids': ids, 'calendar': calendar, 'request_fields': fields, 'qualification': qualification_ref,
        'prior_access_review': review_ref, 'trusted_data_access': {'authorization_ref': 'isolated', 'recipe_version': 'fixture',
        'protocol_binding': {'protocol_id': protocol['protocol_identity'], 'protocol_sha256': review['protocol_sha256'],
            'independent_evidence_id': stable_hash(review), 'independent_evidence_sha256': review_ref['sha256']}}}
    reference = pin('ADMISSION_CONFIG.json', admission, True)
    # 唯一替身为登记提供器接口；实际scope授权另有专用测试，此处不冒充它。
    provider = SimpleNamespace(authorize_independent_scope=lambda *args, **kwargs: object(),
        catalog=lambda **kwargs: {'datasets': [{'dataset_id': 'confirmation', 'metadata_hash': digest,
                                               'target_symbols': SYMBOLS}]})
    loader = PinnedIndependentAdmissionV1(tmp_path, reference, approvals=OwnerApprovalStoreV1(owner.directory),
        submission=SimpleNamespace(provider=provider), protocol_path=protocol_path)
    loaded = loader(protocol)
    assert loaded['snapshot_projection']['projection_hash'] == projection['projection_hash']
    assert loaded['source_authenticated'] is False
    assert loaded['admission_config_ref'] == reference and loaded['prior_access_review_ref'] == review_ref
    snapshots._path(ids[-1]).write_text('{}', encoding='utf-8')
    with pytest.raises((KeyError, ValueError)):
        loader(protocol)


def test_ui_cli_and_http_use_same_registered_controller(tmp_path, monkeypatch, capsys):
    from fastapi.testclient import TestClient
    from chanlun_trader.execution_policy import ExecutionPolicy
    from chanlun_trader.webapp import create_app
    from scripts.run_trusted_research_v1 import main
    from scripts.run_ui import lifecycle_service
    config, _, _ = approved(tmp_path, monkeypatch)
    filename = tmp_path / 'deployment.json'
    filename.write_text(json.dumps(config), encoding='utf-8')
    ui = lifecycle_service(tmp_path, filename)
    assert main(['continuous-status', '--workspace-root', str(tmp_path), '--deployment', str(filename),
                 '--research-id', 'fixed']) == 0
    assert json.loads(capsys.readouterr().out)['research_id'] == 'fixed'
    client = TestClient(create_app(tmp_path, ExecutionPolicy(mode='READ_ONLY'), lifecycle_service=ui))
    before = files(tmp_path)
    assert client.get('/api/research-lifecycle/continuous/fixed').json()['started'] is False
    assert client.get('/api/research-lifecycle/continuous/fixed/handover').json()['independent_results_omitted'] is True
    assert client.post('/api/research-lifecycle/continuous/action', json={
        'research_id': 'fixed', 'action': 'start', 'payload': {}}).status_code == 403
    assert files(tmp_path) == before


def test_pinned_external_owner_artifact_is_digest_bound_without_workspace_restriction(tmp_path):
    from chanlun_trader.research_factory.continuous_universe_lifecycle_v1 import _pinned_json
    external = tmp_path / 'external_owner' / 'DEPLOYMENT.json'
    external.parent.mkdir()
    raw = b'{"schema_version":"OWNER_DEPLOYMENT_FIXTURE"}'
    external.write_bytes(raw)
    reference = {'path': str(external), 'sha256': hashlib.sha256(raw).hexdigest(),
                 'approval_ref': {'approval_id': '1' * 64, 'summary_hash': '2' * 64}}
    before = files(tmp_path)
    assert _pinned_json(reference) == {'schema_version': 'OWNER_DEPLOYMENT_FIXTURE'}
    assert files(tmp_path) == before


@pytest.mark.parametrize('failure', ['missing', 'directory', 'changed', 'invalid_json'])
def test_pinned_artifact_read_failures_use_one_error_without_path_disclosure(tmp_path, failure):
    from chanlun_trader.research_factory.continuous_universe_lifecycle_v1 import _pinned_json
    path = tmp_path / 'PRIVATE_ARTIFACT.json'
    raw = b'not JSON' if failure == 'invalid_json' else b'{}'
    if failure == 'directory':
        path.mkdir()
    elif failure != 'missing':
        path.write_bytes(raw)
    reference = {'path': str(path), 'sha256': 'f' * 64 if failure == 'changed' else hashlib.sha256(raw).hexdigest()}
    before = files(tmp_path)
    with pytest.raises(ValueError) as error:
        _pinned_json(reference)
    assert str(error.value) == 'CONTINUOUS_ARTIFACT_IDENTITY_CHANGED'
    assert str(path) not in str(error.value)
    assert files(tmp_path) == before


@pytest.mark.parametrize('alias', ['parent', 'device', 'stream'])
def test_pinned_reference_rejects_alias_before_reading_any_file(tmp_path, monkeypatch, alias):
    from chanlun_trader.research_factory.continuous_universe_lifecycle_v1 import _pinned_reference
    from pathlib import Path
    paths = {'parent': str(tmp_path / 'not_deployed' / '..' / 'OWNER.json'),
             'device': str(tmp_path / 'NUL.json'), 'stream': str(tmp_path / 'OWNER.json:private')}
    def forbidden_read(*args, **kwargs):
        raise AssertionError('invalid reference must not be opened')
    monkeypatch.setattr(Path, 'read_bytes', forbidden_read)
    with pytest.raises(ValueError, match='CONTINUOUS_ARTIFACT_PIN_INVALID'):
        _pinned_reference({'path': paths[alias], 'sha256': 'f' * 64})


@pytest.mark.parametrize('location', ['outside', 'missing', 'parent_alias'])
def test_snapshot_store_boundary_is_checked_before_data_scope_authorization(tmp_path, location):
    from types import SimpleNamespace
    from chanlun_trader.research_factory.continuous_universe_lifecycle_v1 import PinnedIndependentAdmissionV1
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    external = tmp_path / 'external_store'
    external.mkdir()
    targets = {'outside': external, 'missing': workspace / 'not_deployed_store',
               'parent_alias': workspace / 'not_deployed' / '..' / 'store'}
    value = {'schema_version': 'PINNED_INDEPENDENT_ADMISSION_V1', 'snapshot_store_root': str(targets[location]),
        'snapshot_ids': [], 'calendar': [], 'request_fields': {}, 'qualification': {}, 'trusted_data_access': {}}
    path = tmp_path / 'external_owner_admission.json'
    raw = json.dumps(value).encode()
    path.write_bytes(raw)
    capability = object()
    owner = OwnerApprovalStoreV1(tmp_path / 'external_owner_store', owner_capability=capability)
    reference = {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(),
        'approval_ref': owner.approve(value, approver='isolated_owner', capability=capability)}
    data_scope_calls = []
    provider = SimpleNamespace(authorize_independent_scope=lambda *args, **kwargs: data_scope_calls.append(kwargs))
    admission = PinnedIndependentAdmissionV1(workspace, reference, approvals=OwnerApprovalStoreV1(owner.directory),
        submission=SimpleNamespace(provider=provider), protocol_path=workspace / 'PROTOCOL.json')
    before = files(tmp_path)
    with pytest.raises(ValueError) as error:
        admission({})
    assert str(error.value) == 'CONTINUOUS_SNAPSHOT_STORE_OUTSIDE_WORKSPACE'
    assert str(targets[location]) not in str(error.value)
    assert data_scope_calls == []
    assert files(tmp_path) == before
