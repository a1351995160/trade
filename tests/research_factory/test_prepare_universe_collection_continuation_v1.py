"""续采只排入失败与未开始请求，保留原计数和失败批次证据；不访问网络。"""
import json
from types import SimpleNamespace

import pytest

from scripts import collect_universe_gaps_v1 as collector
from scripts import prepare_universe_collection_continuation_v1 as continuation
from test_collect_universe_gaps_v1 import FakeClient, queue


class FailedResponseClient(FakeClient):
    def query_dividend_data(self, **kwargs):
        self.calls.append(kwargs)
        if sum(isinstance(call, dict) for call in self.calls) == 5:
            return SimpleNamespace(error_code='10001001', error_msg='synthetic not logged in',
                fields=[], next=lambda: False)
        return SimpleNamespace(error_code='0', error_msg='success', fields=['code'], next=lambda: False)


@pytest.fixture(autouse=True)
def isolated_timeout(monkeypatch):
    monkeypatch.setattr(collector.socket, 'setdefaulttimeout', lambda value: None)


def blocked_collection(tmp_path):
    path, value = queue(tmp_path, requests_per_batch=3)
    root = tmp_path / 'original'
    with pytest.raises(RuntimeError, match='BAOSTOCK_RESPONSE_FAILED'):
        collector.collect(path, root, client=FailedResponseClient())
    return root, value


def snapshot(root):
    return {path.relative_to(root): path.read_bytes() for path in root.rglob('*') if path.is_file()}


def test_partial_successes_are_reused_and_continuation_keeps_lifetime_request_count(tmp_path):
    root, original = blocked_collection(tmp_path)
    before = snapshot(root)
    output = tmp_path / 'continuation-plan'
    receipt = continuation.prepare_continuation(root, output,
        collection_output_root=tmp_path / 'continued-collection', reason='显式修复已记录的会话登出干扰')
    assert receipt['original_request_count'] == 5
    assert receipt['already_success_count'] == 4
    assert receipt['retry_failed_count'] == receipt['unstarted_count'] == 1
    assert receipt['remaining_request_count'] == 2
    assert snapshot(root) == before
    assert len(receipt['partial_success_witnesses']) == 1
    witness = receipt['partial_success_witnesses'][0]
    assert witness['request_verified'] is True
    assert witness['parent_batch_completed'] is False and witness['parent_status'] == 'BLOCKED'
    retry = receipt['failed_request_retries'][0]
    assert retry['request_verified'] is False and retry['retry_count_in_new_queue'] == 1
    path = output / 'ACQUISITION_BATCHES.json'
    new_queue = json.loads(path.read_text(encoding='utf-8'))
    items = [item for plan in new_queue['batches'] for item in plan['requests']]
    assert items == original['batches'][1]['requests'][1:]
    assert new_queue['authorization_source'] == original['authorization_source']
    assert new_queue['automatic_retry'] is False
    client = FakeClient()
    state = collector.collect(path, tmp_path / 'continued-collection', client=client)
    assert state['status'] == 'COMPLETED'
    assert state['request_count'] == 7
    assert state['prior_request_count'] == 5 and state['session_request_count'] == 2
    assert state['prior_success_count'] == 4
    assert [call for call in client.calls if isinstance(call, dict)] == [item['request'] for item in items]
    assert snapshot(root) == before


@pytest.mark.parametrize('changed', ['raw', 'start'])
def test_changed_partial_success_refuses_before_writing_new_plan(tmp_path, changed):
    root, value = blocked_collection(tmp_path)
    directory = root / 'batches' / value['batches'][1]['batch_id']
    item = value['batches'][1]['requests'][0]
    path = directory / (item['file'] + ('.START.json' if changed == 'start' else ''))
    if changed == 'raw':
        path.write_bytes(path.read_bytes() + b' ')
    else:
        record = json.loads(path.read_text(encoding='utf-8'))
        record['request']['code'] = 'sz.300999'
        path.write_text(json.dumps(record), encoding='utf-8')
    output = tmp_path / 'continuation-plan'
    with pytest.raises(ValueError, match='CONTENT_CHANGED|START_IDENTITY_CONFLICT'):
        continuation.prepare_continuation(root, output,
            collection_output_root=tmp_path / 'continued-collection', reason='合成显式续采')
    assert not output.exists()


def test_failed_response_is_never_read_or_qualified(tmp_path, monkeypatch):
    root, value = blocked_collection(tmp_path)
    directory = root / 'batches' / value['batches'][1]['batch_id']
    item = value['batches'][1]['requests'][1]
    result = json.loads((directory / 'ACQUISITION_RESULT.json').read_text(encoding='utf-8'))
    row = next(row for row in result['responses'] if row['file'] == item['file'])
    monkeypatch.setattr(collector, '_file_hash', lambda path: pytest.fail('失败响应不得读取或哈希'))
    with pytest.raises(ValueError, match='RESPONSE_NOT_SUCCESSFUL'):
        collector.verify_collected_response_v1(directory, item, row)


def test_completed_continuation_cannot_generate_another_retry_plan(tmp_path):
    root, _ = blocked_collection(tmp_path)
    output = tmp_path / 'continuation-plan'
    continuation.prepare_continuation(root, output,
        collection_output_root=tmp_path / 'continued-collection', reason='合成显式续采')
    continued = tmp_path / 'continued-collection'
    collector.collect(output / 'ACQUISITION_BATCHES.json', continued, client=FakeClient())
    with pytest.raises(ValueError, match='ORIGINAL_BLOCKER_REQUIRED'):
        continuation.prepare_continuation(continued, tmp_path / 'another-plan',
            collection_output_root=tmp_path / 'another-collector', reason='再次重试')
    assert not (tmp_path / 'another-plan').exists()


def test_continuation_receipt_change_refuses_before_login(tmp_path):
    root, _ = blocked_collection(tmp_path)
    output = tmp_path / 'continuation-plan'
    continuation.prepare_continuation(root, output,
        collection_output_root=tmp_path / 'continued-collection', reason='合成显式续采')
    receipt = output / 'READONLY_CONTINUATION_RECEIPT.json'
    receipt.write_bytes(receipt.read_bytes() + b' ')
    client = FakeClient()
    with pytest.raises(ValueError, match='CONTINUATION_RECEIPT_CHANGED'):
        collector.collect(output / 'ACQUISITION_BATCHES.json', tmp_path / 'continue', client=client)
    assert client.calls == [] and not (tmp_path / 'continue').exists()


def test_original_blocked_collection_cannot_fork_another_plan_directory(tmp_path):
    root, _ = blocked_collection(tmp_path)
    original = snapshot(root)
    continuation.prepare_continuation(root, tmp_path / 'plan-one',
        collection_output_root=tmp_path / 'collector-one', reason='合成显式续采')
    with pytest.raises(ValueError, match='CONTINUATION_ALREADY_REGISTERED'):
        continuation.prepare_continuation(root, tmp_path / 'plan-two',
            collection_output_root=tmp_path / 'collector-two', reason='第二个分叉')
    assert not (tmp_path / 'plan-two').exists()
    assert snapshot(root) == original


def test_registered_queue_cannot_create_second_collector_before_login_and_same_root_resumes(tmp_path):
    root, _ = blocked_collection(tmp_path)
    output, target = tmp_path / 'plan', tmp_path / 'continued'
    receipt = continuation.prepare_continuation(root, output,
        collection_output_root=target, reason='合成显式续采')
    path = output / 'ACQUISITION_BATCHES.json'
    control = collector.continuation_control_path_v1(receipt)
    original_control = control.read_bytes()
    refused = FakeClient()
    with pytest.raises(ValueError, match='ALREADY_REGISTERED_ELSEWHERE'):
        collector.collect(path, tmp_path / 'different-collector', client=refused)
    assert refused.calls == [] and not (tmp_path / 'different-collector').exists()
    assert collector.collect(path, target, client=FakeClient())['status'] == 'COMPLETED'
    resumed = FakeClient()
    assert collector.collect(path, target, client=resumed)['request_count'] == 7
    assert resumed.calls == [] and control.read_bytes() == original_control


def test_existing_frozen_queue_can_register_without_rewriting_evidence_or_login(tmp_path):
    root, _ = blocked_collection(tmp_path)
    output, target = tmp_path / 'plan', tmp_path / 'continued'
    receipt = continuation.prepare_continuation(root, output,
        collection_output_root=target, reason='合成显式续采')
    control = collector.continuation_control_path_v1(receipt)
    # 模拟本次修复前已生成并冻结的清单，仅测试夹具移除新控制记录。
    control.unlink()
    path = output / 'ACQUISITION_BATCHES.json'
    raw, queued = collector._load_queue(path)
    target.mkdir()
    collector._initialize(target, raw, queued)
    before = (snapshot(root), snapshot(output), snapshot(target))
    client = FakeClient()
    with pytest.raises(ValueError, match='REGISTRATION_REQUIRED'):
        collector.collect(path, target, client=client)
    assert client.calls == []
    registered = collector.register_continuation_v1(path, target, reason='只补登记当前已冻结任务')
    assert registered['collection_root'] == str(target.absolute())
    assert (snapshot(root), snapshot(output), snapshot(target)) == before
    control_before = control.read_bytes()
    assert collector.register_continuation_v1(path, target, reason='同目标只读核验') == registered
    assert control.read_bytes() == control_before
    collector._verify_continuation_registration(raw, queued, target)


class ResponseFailureClient(FakeClient):
    def query_dividend_data(self, **kwargs):
        self.calls.append(kwargs)
        failed = sum(isinstance(call, dict) for call in self.calls) == self.fail_at
        return SimpleNamespace(error_code='10002007' if failed else '0',
            error_msg='synthetic remote disconnection' if failed else 'success',
            fields=[] if failed else ['code'], next=lambda: False)


def test_different_failed_operations_can_continue_with_cumulative_counts_and_no_repeated_success(tmp_path):
    root, original = blocked_collection(tmp_path)
    first_plan, first_target = tmp_path / 'first-plan', tmp_path / 'first-target'
    first_receipt = continuation.prepare_continuation(root, first_plan,
        collection_output_root=first_target, reason='第一项故障的显式续采')
    with pytest.raises(RuntimeError, match='BAOSTOCK_RESPONSE_FAILED'):
        collector.collect(first_plan / 'ACQUISITION_BATCHES.json', first_target,
            client=ResponseFailureClient(fail_at=2))
    before = snapshot(root), snapshot(first_plan), snapshot(first_target)
    second_plan, second_target = tmp_path / 'second-plan', tmp_path / 'second-target'
    receipt = continuation.prepare_continuation(first_target, second_plan,
        collection_output_root=second_target, reason='另一操作出现远端断开，显式续采剩余请求')
    assert (snapshot(root), snapshot(first_plan), snapshot(first_target)) == before
    assert receipt['local_request_count'] == 2 and receipt['local_success_count'] == 1
    assert receipt['original_request_count'] == receipt['cumulative_request_count'] == 7
    assert receipt['already_success_count'] == receipt['cumulative_success_count'] == 5
    assert receipt['root_planned_request_count'] == 6 and receipt['remaining_request_count'] == 1
    assert receipt['chain_generation'] == 2
    assert receipt['parent_receipt_sha256'] == collector._file_hash(first_plan / 'READONLY_CONTINUATION_RECEIPT.json')
    assert sorted(receipt['retry_counts_by_operation'].values()) == [1, 1]
    assert first_receipt['already_success_count'] == 4
    client = FakeClient()
    state = collector.collect(second_plan / 'ACQUISITION_BATCHES.json', second_target, client=client)
    assert state['status'] == 'COMPLETED' and state['request_count'] == 8
    assert state['prior_request_count'] == 7 and state['prior_success_count'] == 5
    assert state['session_request_count'] == 1
    assert [call for call in client.calls if isinstance(call, dict)] == [original['batches'][1]['requests'][2]['request']]
    with pytest.raises(ValueError, match='CONTINUATION_ALREADY_REGISTERED'):
        continuation.prepare_continuation(first_target, tmp_path / 'forked-plan',
            collection_output_root=tmp_path / 'forked-target', reason='不允许分叉')
    assert not (tmp_path / 'forked-plan').exists()


def test_same_operation_stops_after_original_attempt_and_three_explicit_retries(tmp_path):
    path, _ = queue(tmp_path, batch_count=1, requests_per_batch=1)
    current = tmp_path / 'original'
    with pytest.raises(RuntimeError, match='BAOSTOCK_RESPONSE_FAILED'):
        collector.collect(path, current, client=ResponseFailureClient(fail_at=1))
    for number in range(1, 4):
        plan, target = tmp_path / f'plan-{number}', tmp_path / f'target-{number}'
        receipt = continuation.prepare_continuation(current, plan,
            collection_output_root=target, reason=f'第{number}次显式重试')
        assert receipt['original_request_count'] == number
        assert receipt['already_success_count'] == 0
        assert list(receipt['retry_counts_by_operation'].values()) == [number]
        with pytest.raises(RuntimeError, match='BAOSTOCK_RESPONSE_FAILED'):
            collector.collect(plan / 'ACQUISITION_BATCHES.json', target,
                client=ResponseFailureClient(fail_at=1))
        assert json.loads((target / 'COLLECTION_STATE.json').read_text(encoding='utf-8'))['request_count'] == number + 1
        current = target
    before = snapshot(current)
    with pytest.raises(ValueError, match='CONTINUATION_OPERATION_RETRY_LIMIT'):
        continuation.prepare_continuation(current, tmp_path / 'plan-4',
            collection_output_root=tmp_path / 'target-4', reason='第四次必须拒绝')
    assert snapshot(current) == before and not (tmp_path / 'plan-4').exists()


def test_changed_ancestor_success_is_rejected_before_child_plan_or_new_login(tmp_path):
    root, original = blocked_collection(tmp_path)
    first_plan, first_target = tmp_path / 'first-plan', tmp_path / 'first-target'
    continuation.prepare_continuation(root, first_plan,
        collection_output_root=first_target, reason='第一项故障的显式续采')
    with pytest.raises(RuntimeError, match='BAOSTOCK_RESPONSE_FAILED'):
        collector.collect(first_plan / 'ACQUISITION_BATCHES.json', first_target,
            client=ResponseFailureClient(fail_at=2))
    ancestor_raw = root / 'batches' / original['batches'][1]['batch_id'] / original['batches'][1]['requests'][0]['file']
    ancestor_raw.write_bytes(ancestor_raw.read_bytes() + b' ')
    with pytest.raises(ValueError, match='RESPONSE_CONTENT_CHANGED'):
        continuation.prepare_continuation(first_target, tmp_path / 'next-plan',
            collection_output_root=tmp_path / 'next-target', reason='先核验完整父链')
    client = FakeClient()
    with pytest.raises(ValueError, match='RESPONSE_CONTENT_CHANGED'):
        collector.collect(first_plan / 'ACQUISITION_BATCHES.json', first_target, client=client)
    assert client.calls == [] and not (tmp_path / 'next-plan').exists()


def test_existing_first_generation_receipt_without_new_chain_fields_remains_usable(tmp_path):
    root, _ = blocked_collection(tmp_path)
    plan, target = tmp_path / 'legacy-plan', tmp_path / 'legacy-target'
    receipt = continuation.prepare_continuation(root, plan,
        collection_output_root=target, reason='构造兼容性夹具')
    control = collector.continuation_control_path_v1(receipt)
    # 仅合成夹具还原升级前元数据，真实旧回执无需修改。
    for key in ('local_request_count', 'local_success_count', 'cumulative_request_count',
            'cumulative_success_count', 'root_planned_request_count', 'parent_receipt_path',
            'parent_receipt_sha256', 'chain_generation', 'explicit_operation_retry_limit',
            'retry_limit_semantics', 'retry_counts_by_operation'):
        receipt.pop(key)
    for failure in receipt['failed_request_retries']:
        failure.pop('operation_explicit_retry_number')
    receipt_path = plan / 'READONLY_CONTINUATION_RECEIPT.json'
    receipt_path.write_bytes(collector._canonical(receipt))
    queue_path = plan / 'ACQUISITION_BATCHES.json'
    value = json.loads(queue_path.read_text(encoding='utf-8'))
    value['continuation']['receipt_sha256'] = collector._file_hash(receipt_path)
    queue_path.write_bytes(collector._canonical(value))
    control.unlink()
    collector.register_continuation_v1(queue_path, target, reason='登记升级前夹具')
    legacy_bytes = snapshot(plan)
    with pytest.raises(RuntimeError, match='BAOSTOCK_RESPONSE_FAILED'):
        collector.collect(queue_path, target, client=ResponseFailureClient(fail_at=2))
    new_receipt = continuation.prepare_continuation(target, tmp_path / 'next-plan',
        collection_output_root=tmp_path / 'next-target', reason='旧回执父链的显式续采')
    assert snapshot(plan) == legacy_bytes
    assert new_receipt['original_request_count'] == 7 and new_receipt['already_success_count'] == 5
    assert new_receipt['root_planned_request_count'] == 6
    assert sorted(new_receipt['retry_counts_by_operation'].values()) == [1, 1]
