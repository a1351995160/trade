from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import pytest
from chanlun_trader.research_factory.campaign_confirmation_v1 import CampaignConfirmationV1


def setup_case(tmp_path):
    material = {'attempts': [{'candidate_id': 'failed', 'status': 'FAILED'}, {'candidate_id': 'passed', 'status': 'SCREENED'}],
                'selected': ['passed'], 'selection_policy': {'version': 'frozen-screen'}, 'exposures': [{'dataset_id': 'history'}],
                'budget_hash': 'original-budget', 'method_scope': {'initial_cash': 50000}, 'profile': 'SYNTHETIC'}
    service = CampaignConfirmationV1(SimpleNamespace(root=tmp_path, confirmation_material=lambda: deepcopy(material)))
    future = int((datetime.now(timezone.utc) + timedelta(days=10)).strftime('%Y%m%d'))
    return service, material, future


def test_complete_family_and_missing_method_waits(tmp_path):
    service, material, future = setup_case(tmp_path)
    preview = service.preview(selected=['passed'], not_before=future)
    assert len(preview['family']) == 2
    assert preview['status'] == 'WAITING_METHOD_APPLICABILITY'
    service.freeze(selected=['passed'], not_before=future, preview_identity=preview['preview_identity'])
    assert service.readiness()['strategy_qualified'] is False
    material['attempts'].append({'candidate_id': 'later', 'status': 'FAILED'})
    assert len(service.load()['preview']['family']) == 2
    material['attempts'][0]['status'] = 'OMITTED'
    with pytest.raises(ValueError, match='FAMILY_CHANGED'):
        service.load()


def test_selection_and_sealed_history_cannot_bypass(tmp_path):
    service, _, future = setup_case(tmp_path)
    with pytest.raises(ValueError, match='SCREENED_SELECTION'):
        service.preview(selected=['failed'], not_before=future)
    with pytest.raises(ValueError, match='SEALED_HISTORICAL'):
        service.preview(selected=['passed'], not_before=future, route='SEALED_HISTORICAL')


def test_material_change_after_preview_rejected(tmp_path):
    service, material, future = setup_case(tmp_path)
    preview = service.preview(selected=['passed'], not_before=future)
    material['exposures'].append({'dataset_id': 'another-view'})
    with pytest.raises(ValueError, match='PREVIEW_CHANGED'):
        service.freeze(selected=['passed'], not_before=future, preview_identity=preview['preview_identity'])


def test_published_method_study_blocks_actual_scope(tmp_path):
    service, _, future = setup_case(tmp_path)
    preview = service.preview(selected=['passed'], not_before=future)
    assert preview['method_support']['decision'] == 'UNSUPPORTED'
    assert preview['method_support']['method_id'] == 'FORMAL_ACCOUNT_METHOD_V3_T7_SCOPE_STUDY'
    assert preview['strategy_qualified'] is False


def test_controlled_method_scope_success_never_grants_qualification(tmp_path):
    from chanlun_trader.research_factory.common import stable_hash
    service, material, future = setup_case(tmp_path)
    service.method_resolver = lambda scope: {'applicable': True, 'method_id': 'TEST_ONLY',
        'evidence_hash': 'a' * 64, 'scope_hash': stable_hash(scope), 'profile': 'SYNTHETIC'}
    preview = service.preview(selected=['passed'], not_before=future)
    service.freeze(selected=['passed'], not_before=future, preview_identity=preview['preview_identity'])
    assert service.readiness()['status'] == 'WAITING_DATA'
    assert service.readiness()['strategy_qualified'] is False
    material['profile'] = 'REAL_OBSERVED'
    with pytest.raises(ValueError, match='SELECTION_OR_SCOPE_CHANGED'):
        service.load()


def test_exposure_removal_and_false_method_binding_rejected(tmp_path):
    service, material, future = setup_case(tmp_path)
    service.method_resolver = lambda scope: {'applicable': True, 'method_id': 'TEST',
        'evidence_hash': 'a' * 64, 'scope_hash': 'wrong', 'profile': 'SYNTHETIC'}
    with pytest.raises(ValueError, match='EVIDENCE_SCOPE_CONFLICT'):
        service.preview(selected=['passed'], not_before=future)
    service.method_resolver = lambda scope: {'applicable': False}
    preview = service.preview(selected=['passed'], not_before=future)
    service.freeze(selected=['passed'], not_before=future, preview_identity=preview['preview_identity'])
    material['exposures'].clear()
    with pytest.raises(ValueError, match='SELECTION_OR_SCOPE_CHANGED'):
        service.load()
