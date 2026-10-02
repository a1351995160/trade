"""缺口采集的恢复、范围和真实请求计数；全部使用临时队列与 Fake BaoStock。"""
from copy import deepcopy
import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from scripts import collect_universe_gaps_v1 as collector
from scripts import fetch_research_baostock_v1 as acquisition


def queue(tmp_path, *, batch_count=2, requests_per_batch=2):
    batches = []
    for batch in range(batch_count):
        requests = []
        for offset in range(requests_per_batch):
            code = f'300{batch * requests_per_batch + offset + 1:03d}'
            requests.append({'api': 'query_dividend_data', 'file': f'DIVIDEND_{code}.SZ_2024.json',
                'request': {'code': 'sz.' + code, 'year': '2024', 'yearType': 'operate'}})
        batches.append({'batch_id': f'SUPPLEMENT_{batch + 1:05d}',
            'purpose': 'EXPLORATORY_ENGINEERING_ACCEPTANCE',
            'authorization_scope': 'SYNTHETIC_SCOPE', 'max_requests': 9,
            'requests': requests, 'execution_status': 'NOT_EXECUTED'})
    value = {'version': 'UNIVERSE_BAOSTOCK_SUPPLEMENTS_V1',
        'authorization_source': {'origin': 'USER_EXPLICIT_CURRENT_TASK',
            'statement': '合成补采测试授权', 'scope': 'SYNTHETIC_SCOPE'},
        'request_count': batch_count * requests_per_batch, 'batches': batches,
        'automatic_retry': False, 'account_execution_authorized': False,
        'independent_validation_authorized': False}
    path = tmp_path / 'queue.json'
    path.write_text(json.dumps(value), encoding='utf-8')
    return path, value


class FakeClient:
    def __init__(self, *, fail_at=None, login_code='0'):
        self.calls = []
        self.fail_at, self.login_code = fail_at, login_code

    def login(self):
        self.calls.append('LOGIN')
        return SimpleNamespace(error_code=self.login_code, error_msg='synthetic')

    def logout(self):
        self.calls.append('LOGOUT')

    def query_dividend_data(self, **kwargs):
        self.calls.append(kwargs)
        if sum(isinstance(call, dict) for call in self.calls) == self.fail_at:
            raise TimeoutError('synthetic connection interruption')
        return SimpleNamespace(error_code='0', error_msg='success', fields=['code'], next=lambda: False)


@pytest.fixture(autouse=True)
def isolated_timeout(monkeypatch):
    monkeypatch.setattr(collector.socket, 'setdefaulttimeout', lambda value: None)


def test_full_queue_uses_one_session_and_exact_consumed_request_count(tmp_path):
    path, value = queue(tmp_path)
    root, client = tmp_path / 'output', FakeClient()
    result = collector.collect(path, root, client=client)
    assert result['status'] == 'COMPLETED'
    assert result['request_count'] == result['planned_request_count'] == 4
    assert result['completed_batch_count'] == 2 and result['remaining_batch_count'] == 0
    assert client.calls.count('LOGIN') == client.calls.count('LOGOUT') == 1
    assert sum(isinstance(call, dict) for call in client.calls) == 4
    assert (root / 'ACQUISITION_BATCHES.json').read_bytes() == path.read_bytes()
    assert result['authorization_source'] == value['authorization_source']
    assert result['account_execution_authorized'] is False
    assert result['independent_confirmation_eligible'] is False


def test_batch_limit_can_resume_without_resending_successes(tmp_path):
    path, value = queue(tmp_path)
    root, first = tmp_path / 'output', FakeClient()
    result = collector.collect(path, root, client=first, max_batches=1)
    assert result['status'] == 'PAUSED_BY_BATCH_LIMIT' and result['request_count'] == 2
    raw = root / 'batches' / 'SUPPLEMENT_00001' / value['batches'][0]['requests'][0]['file']
    original = raw.read_bytes()
    second = FakeClient()
    resumed = collector.collect(path, root, client=second)
    assert resumed['request_count'] == 4 and resumed['status'] == 'COMPLETED'
    assert sum(isinstance(call, dict) for call in second.calls) == 2
    assert raw.read_bytes() == original
    unused = FakeClient()
    again = collector.collect(path, root, client=unused)
    assert again['request_count'] == 4 and unused.calls == []


@pytest.mark.parametrize('mutation', ['missing_start', 'wrong_request', 'invalid_raw', 'wrong_rows_hash'])
def test_recovery_requires_original_request_and_supplier_response_even_with_matching_hash(tmp_path, mutation):
    path, value = queue(tmp_path, batch_count=1, requests_per_batch=1)
    root = tmp_path / 'output'
    collector.collect(path, root, client=FakeClient())
    plan = value['batches'][0]
    directory = root / 'batches' / plan['batch_id']
    response_path = directory / plan['requests'][0]['file']
    if mutation == 'missing_start':
        (directory / (response_path.name + '.START.json')).unlink()
    else:
        raw = json.loads(response_path.read_text(encoding='utf-8'))
        if mutation == 'wrong_request':
            raw['request']['code'] = 'sz.300999'
        elif mutation == 'wrong_rows_hash':
            raw['raw_rows_sha256'] = '0' * 64
        if mutation == 'invalid_raw':
            response_path.write_text('not a BaoStock response', encoding='utf-8')
        else:
            response_path.write_text(json.dumps(raw), encoding='utf-8')
        result_path = directory / 'ACQUISITION_RESULT.json'
        result = json.loads(result_path.read_text(encoding='utf-8'))
        result['responses'][0]['sha256'] = collector._file_hash(response_path)
        result_path.write_text(json.dumps(result), encoding='utf-8')
    with pytest.raises((ValueError, FileNotFoundError)):
        collector._verify_success(root, plan)


def test_changed_queue_or_successful_raw_rejected_before_new_login(tmp_path):
    path, value = queue(tmp_path)
    root = tmp_path / 'output'
    collector.collect(path, root, client=FakeClient(), max_batches=1)
    original = path.read_bytes()
    value['authorization_source']['statement'] = 'changed scope'
    path.write_text(json.dumps(value), encoding='utf-8')
    client = FakeClient()
    with pytest.raises(ValueError, match='FROZEN_QUEUE_OR_SCOPE_CHANGED'):
        collector.collect(path, root, client=client)
    assert client.calls == []
    path.write_bytes(original)
    raw = root / 'batches' / 'SUPPLEMENT_00001' / value['batches'][0]['requests'][0]['file']
    with raw.open('ab') as stream:
        stream.write(b' ')
    with pytest.raises(ValueError, match='RESPONSE_CONTENT_CHANGED'):
        collector.collect(path, root, client=client)
    assert client.calls == []


def test_partial_failed_batch_preserves_attempts_and_will_not_retry(tmp_path):
    path, value = queue(tmp_path)
    root, client = tmp_path / 'output', FakeClient(fail_at=2)
    with pytest.raises(TimeoutError):
        collector.collect(path, root, client=client)
    state = json.loads((root / 'COLLECTION_STATE.json').read_text(encoding='utf-8'))
    assert state['status'] == 'BLOCKED' and state['request_count'] == 2
    directory = root / 'batches' / 'SUPPLEMENT_00001'
    assert (directory / value['batches'][0]['requests'][0]['file']).is_file()
    assert not (directory / value['batches'][0]['requests'][1]['file']).exists()
    assert client.calls[-1] == 'LOGOUT'
    original = (root / 'COLLECTION_JOURNAL.jsonl').read_bytes()
    retry = FakeClient()
    with pytest.raises(RuntimeError, match='NO_AUTOMATIC_RETRY'):
        collector.collect(path, root, client=retry)
    assert retry.calls == [] and (root / 'COLLECTION_JOURNAL.jsonl').read_bytes() == original
    assert json.loads((root / 'COLLECTION_STATE.json').read_text(encoding='utf-8'))['request_count'] == 2


def test_completed_batch_after_checkpoint_interruption_recovers_by_bound_sources(tmp_path, monkeypatch):
    path, _ = queue(tmp_path, batch_count=1)
    root, client = tmp_path / 'output', FakeClient()
    original = collector._append_event
    def interrupt(root, frozen, events, event, **values):
        if event == 'BATCH_COMPLETED':
            raise KeyboardInterrupt('synthetic checkpoint interruption')
        return original(root, frozen, events, event, **values)
    monkeypatch.setattr(collector, '_append_event', interrupt)
    with pytest.raises(KeyboardInterrupt):
        collector.collect(path, root, client=client)
    assert sum(isinstance(call, dict) for call in client.calls) == 2
    monkeypatch.setattr(collector, '_append_event', original)
    resumed_client = FakeClient()
    state = collector.collect(path, root, client=resumed_client)
    assert state['status'] == 'COMPLETED' and state['request_count'] == 2
    assert resumed_client.calls == []
    journal = [json.loads(line) for line in (root / 'COLLECTION_JOURNAL.jsonl').read_text(encoding='utf-8').splitlines()]
    completed = next(row for row in journal if row['event'] == 'BATCH_COMPLETED')
    assert completed['recovered'] is True and completed['responses']


def test_duplicate_operations_and_batch_cap_are_rejected_before_output(tmp_path):
    path, value = queue(tmp_path)
    value['batches'][1]['requests'][0] = deepcopy(value['batches'][0]['requests'][0])
    path.write_text(json.dumps(value), encoding='utf-8')
    root, client = tmp_path / 'output', FakeClient()
    with pytest.raises(ValueError, match='DUPLICATE_OPERATION'):
        collector.collect(path, root, client=client)
    assert client.calls == [] and not root.exists()
    path, value = queue(tmp_path, batch_count=1, requests_per_batch=10)
    with pytest.raises(ValueError, match='ACQUISITION_REQUEST_CAP'):
        collector.collect(path, root, client=client)
    assert not root.exists()


def test_login_failure_is_retained_with_zero_requests_and_no_auto_retry(tmp_path):
    path, _ = queue(tmp_path)
    root, client = tmp_path / 'output', FakeClient(login_code='10002007')
    with pytest.raises(RuntimeError, match='BAOSTOCK_LOGIN_FAILED'):
        collector.collect(path, root, client=client)
    state = json.loads((root / 'COLLECTION_STATE.json').read_text(encoding='utf-8'))
    assert state['request_count'] == 0 and state['status'] == 'BLOCKED'
    assert client.calls == ['LOGIN']
    retry = FakeClient()
    with pytest.raises(RuntimeError, match='NO_AUTOMATIC_RETRY'):
        collector.collect(path, root, client=retry)
    assert retry.calls == []


def test_journal_scope_tampering_is_not_treated_as_free_resume(tmp_path):
    path, _ = queue(tmp_path)
    root = tmp_path / 'output'
    collector.collect(path, root, client=FakeClient(), max_batches=1)
    journal = root / 'COLLECTION_JOURNAL.jsonl'
    rows = [json.loads(line) for line in journal.read_text(encoding='utf-8').splitlines()]
    rows[1]['plan_hash'] = '0' * 64
    journal.write_text('\n'.join(json.dumps(row) for row in rows) + '\n', encoding='utf-8')
    client = FakeClient()
    with pytest.raises(ValueError, match='JOURNAL_IDENTITY_CONFLICT'):
        collector.collect(path, root, client=client)
    assert client.calls == []


def test_connected_fetch_leaves_login_logout_and_progress_to_collector(tmp_path, capsys):
    _, value = queue(tmp_path, batch_count=1)
    plan_path = tmp_path / 'plan.json'
    plan_path.write_text(json.dumps(value['batches'][0]), encoding='utf-8')
    client = FakeClient()
    acquisition.execute(plan_path, tmp_path / 'batch', connected_client=client)
    assert all(isinstance(call, dict) for call in client.calls)
    assert capsys.readouterr().out == ''
    result = json.loads((tmp_path / 'batch' / 'ACQUISITION_RESULT.json').read_text(encoding='utf-8'))
    assert result['request_count'] == 2 and result['completed'] is True


def test_redirected_successful_batch_is_rejected_before_resume(tmp_path):
    path, _ = queue(tmp_path)
    root = tmp_path / 'output'
    collector.collect(path, root, client=FakeClient(), max_batches=1)
    original = root / 'batches' / 'SUPPLEMENT_00001'
    moved = tmp_path / 'moved-batch'
    original.rename(moved)
    if os.name == 'nt':
        # Junction 不需要 symlink 管理员权限；目标和链接均为本测试临时目录。
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(original), str(moved)],
                       check=True, capture_output=True)
    else:
        original.symlink_to(moved, target_is_directory=True)
    try:
        client = FakeClient()
        with pytest.raises(ValueError, match='BATCH_PATH_REDIRECTED'):
            collector.collect(path, root, client=client)
        assert client.calls == []
    finally:
        os.rmdir(original) if os.name == 'nt' else original.unlink()
