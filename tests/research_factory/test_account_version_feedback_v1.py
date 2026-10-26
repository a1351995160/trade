"""仅核版本证明接线；账户计算由真实公共 worker 集成测试另行验证。"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from chanlun_trader.research_factory.business_validation_protocol_v1 import BusinessValidationProtocolV1
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.research_capabilities_v1 import capabilities
from test_business_validation_protocol_v1 import PublicServiceSpy


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ProofFixture:
    def proof(self, name=None):
        value = {'manifest_ref': {'path': 'SYNTHETIC_ONLY', 'sha256': 'a' * 64},
                 'version_id': 'b' * 64, 'original_job_sha256': 'c' * 64,
                 'active_plan_ids': {'BASE': 'd' * 64, 'STRESS': 'e' * 64}}
        return value if name is None else {**value, 'purpose': name}


def versioned_reports(tmp_path, monkeypatch):
    from chanlun_trader.research_factory.account_version_continuation_v1 import AccountVersionContinuationV1
    context = ProofFixture()
    monkeypatch.setattr(AccountVersionContinuationV1, 'from_job',
                        classmethod(lambda cls, job, **kwargs: context))
    public = PublicServiceSpy(tmp_path, sessions=504)
    request = {'rule': capabilities()['examples']['multi_indicator_ranked'],
               'strategy_id': 'SYNTHETIC', 'phase': 'EXPLORATION'}
    preview = public.preview(request)
    task = public.freeze(request, preview['preview_identity'])
    outcome = public.advance(task['task_id'])
    outcome['account_version_continuation'] = context.proof()
    outcome['verification'].update(account_version_continuation=context.proof(),
        items={name: {'account_version_continuation': context.proof(name)} for name in task['plan_ids']})
    root = Path(task['job_path']).parent
    for name, reference in outcome['reports'].items():
        source_path = root / (name + '_RESULT.json')
        source = json.loads(source_path.read_text(encoding='utf-8'))
        source['account_version_continuation'] = context.proof(name)
        write(source_path, source)
        source_hash = digest(source_path)
        write(root / (name + '_SETTLEMENT.json'), {'result_sha256': source_hash})
        final_path = Path(reference['final_report'])
        final = json.loads(final_path.read_text(encoding='utf-8'))
        final['account_version_continuation'] = context.proof(name)
        final['artifacts']['source_result']['sha256'] = source_hash
        final['research']['source_result_sha256'] = source_hash
        write(final_path, final)
        reference['final_report_sha256'] = digest(final_path)
    write(root / 'VERIFICATION.json', outcome['verification'])
    return context, task, outcome


def test_feedback_keeps_original_task_and_propagates_exact_member_version(tmp_path, monkeypatch):
    context, task, outcome = versioned_reports(tmp_path, monkeypatch)
    original = deepcopy(task)
    refs = BusinessValidationProtocolV1.verify_report_refs(task, outcome, minimum_sessions=504)
    assert task == original
    assert {cost: row['account_version_continuation'] for cost, row in refs.items()} == {
        cost: context.proof(cost) for cost in ('BASE', 'STRESS')}


@pytest.mark.parametrize('target', ['public', 'verification', 'member_verification', 'result', 'report'])
def test_feedback_rejects_missing_or_retargeted_version_even_with_updated_file_hashes(
        tmp_path, monkeypatch, target):
    _, task, outcome = versioned_reports(tmp_path, monkeypatch)
    root = Path(task['job_path']).parent
    if target == 'public':
        outcome.pop('account_version_continuation')
    elif target in ('verification', 'member_verification'):
        value = outcome['verification'] if target == 'verification' else outcome['verification']['items']['BASE']
        value['account_version_continuation']['version_id'] = 'f' * 64
        write(root / 'VERIFICATION.json', outcome['verification'])
    else:
        path = root / ('BASE_RESULT.json' if target == 'result' else 'BASE_REPORT.json')
        value = json.loads(path.read_text(encoding='utf-8'))
        value.pop('account_version_continuation')
        write(path, value)
        if target == 'report':
            outcome['reports']['BASE']['final_report_sha256'] = digest(path)
    with pytest.raises(ValueError, match='BUSINESS_VALIDATION_ACCOUNT_VERSION_'):
        BusinessValidationProtocolV1.verify_report_refs(task, outcome, minimum_sessions=504)


def test_unapproved_proof_cannot_enter_legacy_feedback(tmp_path, monkeypatch):
    from chanlun_trader.research_factory.account_version_continuation_v1 import AccountVersionContinuationV1
    _, task, outcome = versioned_reports(tmp_path, monkeypatch)
    monkeypatch.setattr(AccountVersionContinuationV1, 'from_job',
                        classmethod(lambda cls, job, **kwargs: None))
    with pytest.raises(ValueError, match='BUSINESS_VALIDATION_ACCOUNT_VERSION_PROOF_CONFLICT'):
        BusinessValidationProtocolV1.verify_report_refs(task, outcome, minimum_sessions=504)


@pytest.mark.parametrize('cached', [False, True])
def test_continuous_public_result_requires_the_same_approved_version(tmp_path, monkeypatch, cached):
    from chanlun_trader.research_factory import continuous_submission_v1 as continuous
    from scripts import run_strategy_account_v1 as runner
    context, task, outcome = versioned_reports(tmp_path, monkeypatch)
    root = Path(task['job_path']).parent
    task_root = root.parent
    write(task_root / 'TASK.json', task)
    write(root / 'CONFIRMATION.json', {})
    scope = SimpleNamespace(campaign=SimpleNamespace(root=tmp_path))
    service = type('ExistingTask', (), {'root': tmp_path, 'continuous_scope': scope,
        '_task': lambda self, task_id: deepcopy(task)})()

    def authority(actual_service, request, *, calendar_context=None):
        assert actual_service is service and calendar_context is None
        return {'SYNTHETIC_ONLY': True}

    monkeypatch.setattr(continuous, 'continuous_authority', authority)
    monkeypatch.setattr(runner, 'execute_long_horizon_accounts',
                        lambda *args, **kwargs: {'status': 'COMPLETED', 'dispatched_segments': 0})
    def compute(path, authority, request, stage, **kwargs):
        return {'status': 'COMPLETED', 'dispatched_segments': 0,
                'result': outcome['verification'] if stage == 'VERIFICATION' else outcome['reports']}
    monkeypatch.setattr(runner, 'run_long_horizon_compute', compute)
    if cached:
        write(task_root / 'PUBLIC_RESULT.json', {**outcome, 'dispatched_segments': 0})
    result = continuous.advance_submission(service, task['task_id'])
    assert result['account_version_continuation'] == context.proof()
    saved = json.loads((task_root / 'PUBLIC_RESULT.json').read_text(encoding='utf-8'))
    saved['account_version_continuation']['version_id'] = stable_hash('other_version')
    write(task_root / 'PUBLIC_RESULT.json', saved)
    with pytest.raises(PermissionError, match='CONTINUOUS_ACCOUNT_VERSION_RESULT_CONFLICT'):
        continuous.advance_submission(service, task['task_id'])
