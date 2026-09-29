from types import SimpleNamespace
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
