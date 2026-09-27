"""使用原合成 Paper/快照/账本核对生命周期接线，绝不计作真实观察。"""
from copy import deepcopy
from datetime import timedelta
import json

from fastapi.testclient import TestClient
import pytest

from test_forward_paper_v1 import paper_source, paper_case, snapshot
from chanlun_trader.execution_policy import ExecutionPolicy
from chanlun_trader.research_factory.lifecycle_service_v2 import LifecycleServiceV2
from chanlun_trader.webapp import create_app
from scripts.run_strategy_lifecycle_v1 import execute, parser


def configured(tmp_path, source):
    paper, store, clock, _, _ = paper_case(tmp_path, source)
    close = snapshot(store, clock, 'CLOSE', 20240801, 13.95, 14.10)
    binding = {'paper': {'kind': 'PAPER', 'root': str(paper.root), 'snapshot_root': str(store.root),
                         'snapshots': {'FIRST_CLOSE': close['snapshot_id']}},
               'daily': {'kind': 'DAILY_PLAN', 'root': str(paper.root)}}
    service = LifecycleServiceV2(tmp_path, binding, synthetic_clock=lambda: clock[0])
    args = {'job_id': 'paper_job', 'binding_id': 'paper', 'expires_at': (clock[0] + timedelta(hours=1)).isoformat(),
            'max_calls': 1, 'trading_calendar': [20240801], 'stages': [{
                'key': 'FIRST_CLOSE', 'trade_date': 20240801, 'not_before': clock[0].isoformat(),
                'not_after': (clock[0] + timedelta(minutes=5)).isoformat()}]}
    return service, paper, args


def test_task_advances_original_paper_and_repeated_tick_does_not_duplicate(tmp_path, paper_source):
    service, paper, args = configured(tmp_path, paper_source)
    service.create_job(**args)
    service.jobs.start('paper_job')
    result = service.jobs.tick('paper_job')
    assert result['status'] == 'COMPLETED' and result['calls_started'] == 1
    account = paper.status()
    assert account['completed_stages'] == 1 and account['next_plan']['intents']
    assert account['real_observation_days'] == 0 and not account['strategy_qualified']
    assert service.jobs.tick('paper_job') == result and paper.status() == account
    assert service.inspect()['bindings']['daily']['next_plan'] == account['next_plan']
    daily = {**args, 'job_id': 'daily_job', 'binding_id': 'daily'}
    service.create_job(**daily)
    config, _, _ = service.jobs._load('daily_job')
    # 当前已生成的是次日计划，不能把它记作当天所请求的计划。
    waiting = service.readiness(config, daily['stages'][0])
    assert waiting == {'status': 'WAITING_DATA', 'reason': 'DAILY_PLAN_FOR_REQUESTED_SESSION_REQUIRED'}


def test_paper_canonical_commit_recovers_when_adapter_receipt_write_fails(tmp_path, paper_source, monkeypatch):
    service, paper, args = configured(tmp_path, paper_source)
    service.create_job(**args)
    service.jobs.start('paper_job')
    original = service._result
    def crash(*_):
        raise OSError('after canonical paper commit')
    monkeypatch.setattr(service, '_result', crash)
    with pytest.raises(OSError, match='canonical'):
        service.jobs.tick('paper_job')
    assert paper.status()['completed_stages'] == 1
    monkeypatch.setattr(service, '_result', original)
    assert service.jobs.tick('paper_job')['status'] == 'COMPLETED'
    assert paper.status()['completed_stages'] == 1


def test_binding_change_cannot_redirect_frozen_job(tmp_path, paper_source):
    service, _, args = configured(tmp_path, paper_source)
    service.create_job(**args)
    service.jobs.start('paper_job')
    service.bindings['paper']['snapshots'] = {}
    with pytest.raises(ValueError, match='FROZEN_BINDING'):
        service.jobs.tick('paper_job')


def test_inspect_has_no_write_and_missing_service_is_explicit(tmp_path):
    service = LifecycleServiceV2(tmp_path, {'protocol': {'kind': 'VALIDATION_PROTOCOL', 'path': str(tmp_path / 'missing.json')}})
    before = list(tmp_path.rglob('*'))
    result = service.inspect()
    assert result['bindings']['protocol']['status'] == 'BLOCKED'
    assert list(tmp_path.rglob('*')) == before
    assert not result['background_enabled'] and not result['real_execution_authorized']


def test_preview_ignores_only_budget_read_time_and_keeps_budget_changes(tmp_path, paper_source, monkeypatch):
    service, _, args = configured(tmp_path, paper_source)
    original = service.inspect
    budget = {'schema_version': 'search-budget-registry-v1', 'updated_at': 'first',
              'buckets': [{'used': 0, 'reserved': 0}], 'active_reservations': {}}
    monkeypatch.setattr(service, 'inspect', lambda: {**original(), 'research_budget': deepcopy(budget)})
    policy = ExecutionPolicy(mode='GOVERNED', workspace_kind='SYNTHETIC')
    first = service.action_preview('create', args)
    budget['updated_at'] = 'later'
    assert service.action_preview('create', args)['preview_hash'] == first['preview_hash']
    budget['buckets'][0]['used'] = 1
    with pytest.raises(ValueError, match='PREVIEW_CHANGED'):
        service.perform(policy, action='create', payload=args, preview_hash=first['preview_hash'], confirmed=True)
    current = service.action_preview('create', args)
    budget['updated_at'] = 'later-again'
    assert service.perform(policy, action='create', payload=args,
                           preview_hash=current['preview_hash'], confirmed=True)['status'] == 'CREATED'


def test_http_default_readonly_and_governed_preview_confirmation(tmp_path, paper_source):
    service, paper, args = configured(tmp_path, paper_source)
    readonly = TestClient(create_app(tmp_path, ExecutionPolicy(), lifecycle_service=service))
    assert readonly.get('/api/research-lifecycle').status_code == 200
    assert readonly.post('/api/research-lifecycle/preview', json={'action': 'create', 'payload': args}).status_code == 403
    policy = ExecutionPolicy(mode='GOVERNED', workspace_kind='SYNTHETIC')
    client = TestClient(create_app(tmp_path, policy, lifecycle_service=service))
    def perform(action, payload):
        preview = client.post('/api/research-lifecycle/preview', json={'action': action, 'payload': payload})
        assert preview.status_code == 200, preview.text
        return client.post('/api/research-lifecycle/action', json={'action': action, 'payload': payload,
                          'preview_hash': preview.json()['preview_hash'], 'confirmed': True})
    assert perform('create', args).status_code == 200
    assert perform('start', {'job_id': 'paper_job'}).status_code == 200
    assert perform('tick', {'job_id': 'paper_job'}).json()['status'] == 'COMPLETED'
    assert paper.status()['completed_stages'] == 1
    assert client.get('/api/research-lifecycle').json()['jobs']['paper_job']['status'] == 'COMPLETED'


def test_http_stale_preview_and_false_confirmation_rejected(tmp_path, paper_source):
    service, _, args = configured(tmp_path, paper_source)
    service.create_job(**args)
    policy = ExecutionPolicy(mode='GOVERNED', workspace_kind='SYNTHETIC')
    payload = {'job_id': 'paper_job'}
    preview = service.action_preview('start', payload)
    with pytest.raises(PermissionError):
        service.perform(policy, action='start', payload=payload, preview_hash=preview['preview_hash'], confirmed=False)
    service.jobs.pause('paper_job')
    with pytest.raises(ValueError, match='PREVIEW_CHANGED'):
        service.perform(policy, action='start', payload=payload, preview_hash=preview['preview_hash'], confirmed=True)


def test_cli_preview_uses_same_service_and_does_not_create_jobs(tmp_path):
    bindings = tmp_path / 'bindings.json'
    bindings.write_text(json.dumps({}), encoding='utf-8')
    args = parser().parse_args(['lifecycle-preview', '--workspace-root', str(tmp_path), '--bindings', str(bindings)])
    assert execute(args) == LifecycleServiceV2(tmp_path, {}).inspect()
    assert not (tmp_path / 'lifecycle_jobs').exists()


def test_paths_and_executable_bindings_rejected(tmp_path):
    with pytest.raises(ValueError, match='OUTSIDE_WORKSPACE'):
        LifecycleServiceV2(tmp_path, {'bad': {'kind': 'DAILY_PLAN', 'root': str(tmp_path.parent / 'elsewhere')}})
    with pytest.raises(ValueError, match='FIELDS'):
        LifecycleServiceV2(tmp_path, {'bad': {'kind': 'RESEARCH', 'root': str(tmp_path), 'command': 'python code'}})
    with pytest.raises(ValueError, match='ROOT_CONFLICT'):
        create_app(tmp_path, lifecycle_service=LifecycleServiceV2(tmp_path / 'other', {}))
