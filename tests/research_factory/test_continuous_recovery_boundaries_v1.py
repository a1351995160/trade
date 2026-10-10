"""只用隔离原件验证停派恢复、基础设施等待和回执归属。"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.exploration_governance import immutable, read_json
from test_diagnosis_research_v4 import research_case


@pytest.mark.parametrize('stop', ['pause', 'revoke', 'expiry'])
def test_original_paid_receipt_can_settle_after_stop_without_new_model_or_worker(tmp_path, monkeypatch, stop):
    research, model, accesses, _ = research_case(tmp_path, unknown=True)
    research.start()
    research.advance()
    directory = research.root / 'candidate_0001'
    context = read_json(directory / 'CONTEXT.json')
    proposal = model.rules[0]
    immutable(directory / 'model' / 'INVOCATION.json', {'synthetic': True,
        'context_hash': stable_hash(context), 'response_hash': stable_hash(proposal),
        'usage': {'input_tokens': 1, 'output_tokens': 1, 'total_tokens': 2, 'cost_microunits': 1, 'model_calls': 1}})
    immutable(directory / 'model' / 'RESPONSE.json', proposal)
    model.enforce_budget_limits = lambda **kwargs: pytest.fail('recovery must use original budget proof')
    if stop == 'pause':
        research.pause()
    elif stop == 'revoke':
        research.revoke()
    else:
        from chanlun_trader.research_factory import research_campaign_v1 as campaigns
        class FutureClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.now(timezone.utc) + timedelta(hours=2)
        monkeypatch.setattr(campaigns, 'datetime', FutureClock)
    result = research.advance()
    assert result['dispatched_segments'] == 0 and model.calls == 1 and accesses == []
    assert read_json(directory / 'PROPOSAL.json') == proposal
    view = research.campaign.peek_status()
    assert view['used']['model_calls'] == 1 and view['reserved']['model_calls'] == 0
    research.advance()
    assert model.calls == 1 and accesses == [] and not (directory / 'TASK.json').exists()


def test_missing_registered_data_blocks_before_new_paid_proposal(tmp_path, monkeypatch):
    research, model, accesses, _ = research_case(tmp_path)
    monkeypatch.setattr(research.submission.provider, 'catalog', lambda **kwargs: {'datasets': []})
    research.start()
    result = research.advance()
    assert result['waiting_reason'] == 'CONTINUOUS_DATASET_NOT_REGISTERED'
    assert model.calls == 0 and accesses == []
    assert not research.campaign.peek_status()['operations']


def test_infrastructure_preview_failure_preserves_proposal_instead_of_burning_next_candidate(tmp_path, monkeypatch):
    research, model, accesses, _ = research_case(tmp_path)
    research.start()
    research.advance()
    def missing(request):
        raise PermissionError('DATA_SOURCE_CHANGED')
    monkeypatch.setattr(research.submission, 'preview', missing)
    for _ in range(2):
        result = research.advance()
        assert result['status'] == 'WAITING_PUBLIC_DEPENDENCY'
    assert model.calls == 1 and accesses == [] and research.status()['attempts'] == []
    assert not (research.root / 'candidate_0002').exists()


def test_storage_boundary_preserves_artifacts_and_blocks_new_dispatch(tmp_path, monkeypatch):
    research, model, accesses, _ = research_case(tmp_path)
    from chanlun_trader.research_factory import continuous_storage_v1 as storage
    monkeypatch.setattr(storage.shutil, 'disk_usage', lambda root: SimpleNamespace(free=1))
    research.start()
    result = research.advance()
    assert result['status'] == 'WAITING_STORAGE' and result['dispatched_segments'] == 0
    assert model.calls == 0 and accesses == []
    assert (research.root / 'CONFIG.json').is_file()


def test_overrun_receipt_cannot_charge_other_candidate_or_changed_context(tmp_path):
    from chanlun_trader.research_factory.campaign_model_receipt_v1 import model_receipt_proof
    root = tmp_path / 'diagnosis_v4' / 'candidate_0001'
    context = {'synthetic': True}
    guarantee = {'enforced': True, 'max_tokens': 100, 'max_cost_microunits': 100, 'evidence_identity': 'bound'}
    immutable(root / 'CONTEXT.json', context)
    immutable(root / 'INTENT.json', {'candidate_id': 'CANDIDATE_0001', 'batch_id': 'BATCH_0001'})
    immutable(root / 'MODEL_GUARANTEE.json', guarantee)
    receipt = root / 'model' / 'INVOCATION.json'
    immutable(receipt, {'context_hash': stable_hash(context),
        'usage': {'input_tokens': 1, 'output_tokens': 1, 'total_tokens': 2, 'cost_microunits': 200, 'model_calls': 1}})
    operation = {'operation_id': 'CANDIDATE_0001_MODEL', 'batch_id': 'BATCH_0001', 'kind': 'MODEL',
        'subject_identity': stable_hash({'context': context, 'candidate': 'CANDIDATE_0001'}),
        'cost_bound_evidence': 'bound', 'upper_bounds': {'model_tokens': 100, 'model_cost_microunits': 100}}
    usage, digest = model_receipt_proof(tmp_path, operation, receipt, {operation['operation_id']: operation})
    assert usage['cost_microunits'] == 200
    bad = deepcopy(operation)
    bad['operation_id'] = 'CANDIDATE_0002_MODEL'
    with pytest.raises(PermissionError, match='BINDING'):
        model_receipt_proof(tmp_path, bad, receipt, {bad['operation_id']: bad})
    bad = deepcopy(operation)
    bad['subject_identity'] = stable_hash('other context')
    with pytest.raises(PermissionError, match='BINDING'):
        model_receipt_proof(tmp_path, bad, receipt, {bad['operation_id']: bad})
    other = {'operation_id': 'CANDIDATE_0002_MODEL', 'evidence_identity': digest}
    with pytest.raises(PermissionError, match='BINDING'):
        model_receipt_proof(tmp_path, operation, receipt, {operation['operation_id']: operation, other['operation_id']: other})


def test_recovery_does_not_silently_resign_changed_implementation(tmp_path, monkeypatch):
    research, _, _, _ = research_case(tmp_path)
    from chanlun_trader.research_factory import diagnosis_research_v4 as controller
    monkeypatch.setattr(controller, 'implementation_identity', lambda: {'changed': True})
    with pytest.raises(PermissionError, match='CONFIG_OR_AUTHORIZATION_CHANGED'):
        research.status()
