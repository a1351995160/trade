from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from chanlun_trader.execution_policy import ExecutionPolicy
from chanlun_trader.webapp import create_app


class Submission:
    def __init__(self, root):
        self.root = root
        self.provider = SimpleNamespace(catalog=lambda: {'datasets': []})
        self.calls = []
    def preview(self, request):
        self.calls.append(('preview', request))
        return {'preview_identity': 'frozen-preview'}
    def freeze(self, request, preview_identity):
        self.calls.append(('freeze', request))
        if preview_identity != 'frozen-preview':
            raise ValueError('SUBMISSION_PREVIEW_CHANGED')
        return {'task_id': 'task'}
    def start(self, task_id):
        raise PermissionError('ORIGINAL_GOVERNANCE_REQUIRED')
    def diagnose(self, request, preview_identity):
        self.calls.append(('diagnose', request, preview_identity))
        if preview_identity != 'frozen-preview':
            raise ValueError('SUBMISSION_PREVIEW_CHANGED')
        return {'status': 'DATA_GAPS', 'account_executed': False, 'strategy_qualified': False}
    def resume(self, task_id):
        self.calls.append(('resume', task_id))
        raise PermissionError('ORIGINAL_GOVERNANCE_REQUIRED')
    def status(self, task_id):
        return {'status': 'FROZEN', 'task_id': task_id}


def test_readonly_and_explicit_real_scope(tmp_path):
    service = Submission(tmp_path)
    readonly = TestClient(create_app(tmp_path, submission_service=service))
    assert readonly.get('/api/research-submission').json()['actions_allowed'] is False
    assert readonly.post('/api/research-submission/freeze', json={'request': {}, 'preview_identity': 'frozen-preview'}).status_code == 403
    assert not service.calls
    policy = ExecutionPolicy(mode='GOVERNED', allow_trusted_research=True)
    assert policy.trusted_research_allowed and not policy.governance_allowed
    client = TestClient(create_app(tmp_path, policy, submission_service=service))
    assert client.post('/api/research-submission/preview', json={'request': {}}).status_code == 200
    assert client.post('/api/research-submission/freeze', json={'request': {}, 'preview_identity': 'old'}).status_code == 409
    assert client.post('/api/research-submission/start', json={'task_id': 'task'}).status_code == 403
    # 狭窄的新权限不开放旧治理写入口。
    assert client.post('/api/research-engineering/advance', json={}).status_code == 403


def test_remote_request_and_unknown_fields_rejected(tmp_path):
    service = Submission(tmp_path)
    policy = ExecutionPolicy(mode='GOVERNED', allow_trusted_research=True)
    app = create_app(tmp_path, policy, submission_service=service)
    remote = TestClient(app, client=('203.0.113.8', 4567))
    assert remote.post('/api/research-submission/preview', json={'request': {}}).status_code == 403
    local = TestClient(app)
    assert local.post('/api/research-submission/freeze', json={'request': {}, 'preview_identity': 'frozen-preview', 'factory': 'evil:run'}).status_code == 400
    assert not service.calls


def test_synthetic_governance_does_not_authorize_public_data_execution(tmp_path):
    service = Submission(tmp_path)
    policy = ExecutionPolicy(mode='GOVERNED', workspace_kind='SYNTHETIC')
    assert policy.governance_allowed and not policy.trusted_research_allowed
    client = TestClient(create_app(tmp_path, policy, submission_service=service))
    assert client.get('/api/research-submission').json()['actions_allowed'] is False
    for action, payload in [('freeze', {'request': {}, 'preview_identity': 'frozen-preview'}),
                            ('approve', {'task_id': 'task', 'preview_identity': 'frozen-preview'}),
                            ('start', {'task_id': 'task'})]:
        assert client.post('/api/research-submission/' + action, json=payload).status_code == 403
    assert not service.calls


def test_catalog_publication_is_display_only_and_keeps_core_unchanged(tmp_path, monkeypatch):
    from chanlun_trader.research_factory import research_capabilities_v1 as catalog
    service = Submission(tmp_path)
    client = TestClient(create_app(tmp_path, submission_service=service))
    monkeypatch.setattr(catalog, 'published_acceptance', lambda core: {'status': 'NOT_ACCEPTED', 'feature_ids': [], 'strategy_qualified': False})
    before = client.get('/api/research-submission').json()
    publication = {'status': 'PUBLISHED_METADATA_VERIFIED', 'feature_ids': ['indicator_rules'],
                   'case_count': 6, 'account_count': 18, 'initial_cash': 50000, 'strategy_qualified': False}
    monkeypatch.setattr(catalog, 'published_acceptance', lambda core: publication)
    after = client.get('/api/research-submission').json()
    assert after['capabilities'] == before['capabilities']
    assert after['publication'] == publication
    assert after['actions_allowed'] is False and not service.calls


@pytest.mark.parametrize('action,payload', [
    ('diagnose', {'request': {}, 'preview_identity': 'frozen-preview'}),
    ('resume', {'task_id': 'task'}),
])
@pytest.mark.parametrize('policy', [ExecutionPolicy(), ExecutionPolicy(mode='GOVERNED', workspace_kind='SYNTHETIC')])
def test_universe_diagnosis_and_resume_need_explicit_trusted_scope(tmp_path, action, payload, policy):
    service = Submission(tmp_path)
    client = TestClient(create_app(tmp_path, policy, submission_service=service))
    assert client.post('/api/research-submission/' + action, json=payload).status_code == 403
    assert service.calls == []


@pytest.mark.parametrize('action,payload', [
    ('diagnose', {'request': {}, 'preview_identity': 'frozen-preview'}),
    ('resume', {'task_id': 'task'}),
])
def test_universe_diagnosis_and_resume_reject_remote_operator(tmp_path, action, payload):
    service = Submission(tmp_path)
    policy = ExecutionPolicy(mode='GOVERNED', allow_trusted_research=True)
    client = TestClient(create_app(tmp_path, policy, submission_service=service), client=('203.0.113.8', 4567))
    assert client.post('/api/research-submission/' + action, json=payload).status_code == 403
    assert service.calls == []


@pytest.mark.parametrize('action,payload', [
    ('diagnose', {'request': {}}),
    ('diagnose', {'request': {}, 'preview_identity': 'frozen-preview', 'backend': 'evil:run'}),
    ('resume', {'task_id': 'task', 'new_budget': 100}),
    ('resume', {'task_id': 'task', 'preview_identity': 'frozen-preview'}),
])
def test_universe_actions_cannot_inject_scope_or_worker_fields(tmp_path, action, payload):
    service = Submission(tmp_path)
    policy = ExecutionPolicy(mode='GOVERNED', allow_trusted_research=True)
    client = TestClient(create_app(tmp_path, policy, submission_service=service))
    assert client.post('/api/research-submission/' + action, json=payload).status_code == 400
    assert service.calls == []


def test_trusted_universe_diagnosis_routes_preview_identity_and_preserves_levels(tmp_path):
    service = Submission(tmp_path)
    policy = ExecutionPolicy(mode='GOVERNED', allow_trusted_research=True)
    client = TestClient(create_app(tmp_path, policy, submission_service=service))
    request = {'version': 'FULL_UNIVERSE_SUBMISSION_V1', 'initial_cash': 50000}
    response = client.post('/api/research-submission/diagnose', json={'request': request, 'preview_identity': 'frozen-preview'})
    assert response.status_code == 200
    assert response.json() == {'status': 'DATA_GAPS', 'account_executed': False, 'strategy_qualified': False}
    assert service.calls == [('diagnose', request, 'frozen-preview')]
    stale = client.post('/api/research-submission/diagnose', json={'request': request, 'preview_identity': 'old'})
    assert stale.status_code == 409
    assert stale.json()['detail']['reason'] == 'SUBMISSION_PREVIEW_CHANGED'
    assert client.post('/api/research-engineering/advance', json={}).status_code == 403


def test_trusted_resume_routes_only_original_task_and_cannot_bypass_governance(tmp_path, monkeypatch):
    service = Submission(tmp_path)
    policy = ExecutionPolicy(mode='GOVERNED', allow_trusted_research=True)
    client = TestClient(create_app(tmp_path, policy, submission_service=service))
    blocked = client.post('/api/research-submission/resume', json={'task_id': 'task'})
    assert blocked.status_code == 403
    assert blocked.json()['detail']['reason'] == 'ORIGINAL_GOVERNANCE_REQUIRED'
    assert service.calls == [('resume', 'task')]
    def resumed(task_id):
        service.calls.append(('resume-authorized', task_id))
        return {'task_id': task_id, 'status': 'ACCOUNT_VERIFIED', 'strategy_qualified': False}
    monkeypatch.setattr(service, 'resume', resumed)
    complete = client.post('/api/research-submission/resume', json={'task_id': 'task'})
    assert complete.status_code == 200
    assert complete.json() == {'task_id': 'task', 'status': 'ACCOUNT_VERIFIED', 'strategy_qualified': False}
    assert service.calls[-1] == ('resume-authorized', 'task')
    assert client.post('/api/research-engineering/advance', json={}).status_code == 403


@pytest.mark.parametrize('error,status', [
    (PermissionError('UNIVERSE_RESUME_WORKER_STILL_ACTIVE'), 403),
    (PermissionError('STRATEGY_EXPIRED'), 403),
    (ValueError('SUBMISSION_FROZEN_JOB_CHANGED'), 409),
    (RuntimeError('UNIVERSE_RESUME_FAILED_NO_AUTOMATIC_RETRY'), 409),
])
def test_resume_rejections_are_structured_and_do_not_grant_retry(tmp_path, monkeypatch, error, status):
    service = Submission(tmp_path)
    def rejected(task_id):
        service.calls.append(('resume-rejected', task_id))
        raise error
    monkeypatch.setattr(service, 'resume', rejected)
    policy = ExecutionPolicy(mode='GOVERNED', allow_trusted_research=True)
    client = TestClient(create_app(tmp_path, policy, submission_service=service), raise_server_exceptions=False)
    response = client.post('/api/research-submission/resume', json={'task_id': 'task'})
    assert response.status_code == status
    assert response.json()['detail']['reason'] == str(error)
    assert service.calls == [('resume-rejected', 'task')]
