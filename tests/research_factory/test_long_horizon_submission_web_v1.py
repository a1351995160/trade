"""长期入口沿用本机及可信研究权限；GET 仅展示登记资源规格。"""
import pytest
from fastapi.testclient import TestClient

from chanlun_trader.execution_policy import ExecutionPolicy
from chanlun_trader.research_factory.universe_execution_profile_v1 import validate_execution_profile
from chanlun_trader.webapp import create_app
from test_strategy_submission_web_v1 import Submission


class LongSubmission(Submission):
    def pause(self, task_id):
        self.calls.append(('pause', task_id))
        return {'task_id': task_id, 'status': 'PAUSE_REQUESTED', 'strategy_qualified': False}


def test_catalog_resources_are_read_only_registered_and_exclude_engineering_reference(tmp_path):
    service = LongSubmission(tmp_path)
    before = {path: path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    client = TestClient(create_app(tmp_path, submission_service=service))
    response = client.get('/api/research-submission')
    assert response.status_code == 200
    profiles = response.json()['execution_profiles']
    assert set(profiles) == {'252', '504'}
    for profile in profiles.values():
        assert profile == validate_execution_profile(profile)
        assert profile['profile_id'] == 'LONG_HORIZON_SEGMENTED_V1'
        assert profile['purpose'] == 'RESEARCH_ACCOUNT'
    assert service.calls == []
    assert before == {path: path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}


@pytest.mark.parametrize('policy', [ExecutionPolicy(), ExecutionPolicy(mode='GOVERNED', workspace_kind='SYNTHETIC')])
def test_pause_cannot_get_privilege_from_synthetic_or_readonly_mode(tmp_path, policy):
    service = LongSubmission(tmp_path)
    client = TestClient(create_app(tmp_path, policy, submission_service=service))
    assert client.post('/api/research-submission/pause', json={'task_id': 'long'}).status_code == 403
    assert service.calls == []


def test_pause_routes_original_task_only_and_rejects_scope_injection_and_remote_request(tmp_path):
    service = LongSubmission(tmp_path)
    policy = ExecutionPolicy(mode='GOVERNED', allow_trusted_research=True)
    app = create_app(tmp_path, policy, submission_service=service)
    client = TestClient(app)
    assert client.post('/api/research-submission/pause', json={'task_id': 'long', 'new_budget': 100}).status_code == 400
    remote = TestClient(app, client=('203.0.113.8', 1234))
    assert remote.post('/api/research-submission/pause', json={'task_id': 'long'}).status_code == 403
    assert service.calls == []
    response = client.post('/api/research-submission/pause', json={'task_id': 'long'})
    assert response.status_code == 200
    assert response.json() == {'task_id': 'long', 'status': 'PAUSE_REQUESTED', 'strategy_qualified': False}
    assert service.calls == [('pause', 'long')]
