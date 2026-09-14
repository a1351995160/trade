"""将已获准通信复试映射到原请求；保留失败及独立读取证据。"""
from datetime import datetime, timezone
from pathlib import Path

from acquire_monthly_independent_v1 import ROOT, SOURCE, guard, read, save, sha


def verify(original, response, access, response_hash):
    if (original['method'] != response['method'] or original['query'] != response['query']
            or response['error_code'] != '0' or access['sha256'] != response_hash
            or access['row_count'] != len(response['rows'])):
        raise PermissionError('RETRY_RESPONSE_IDENTITY_CONFLICT')
    query = original['query']
    for row in response['rows']:
        if (row['code'] != query['code']
                or not query['start_date'] <= row['dividOperateDate'] <= query['end_date']):
            raise PermissionError('RETRY_ROW_OUTSIDE_QUERY')


def run():
    grant = guard()
    probe = ROOT/'communication-retry-v1'
    original = ROOT/'acquisition/actions/sz.002803.started.json'
    target = original.with_name('sz.002803.json')
    approval = read(probe/'APPROVAL.json')
    if (approval['original_request_sha256'] != sha(original)
            or approval['release_identity'] != grant['identity']):
        raise PermissionError('ORIGINAL_REQUEST_CHANGED')
    receipt = ROOT/'resources/communication-probe-v1.completed.json'
    if read(receipt)['returncode'] != 0 or read(receipt)['timed_out']:
        raise PermissionError('RETRY_NOT_SUCCESSFUL')
    response = probe/'RESPONSE.json'
    access = probe/'ACCESS.json'
    verify(read(original), read(response), read(access), sha(response))
    if target.exists() or (ROOT/'FETCH_V4_HANDOFF.json').exists():
        raise PermissionError('RECOVERY_ALREADY_STARTED_NO_REPLAY')
    failed = ROOT/'resources/prices-v3-72.completed.json'
    if not read(failed)['timed_out'] or read(failed)['returncode'] == 0:
        raise PermissionError('EXPECTED_TIMEOUT_NOT_FOUND')
    # 保存同一响应字节，不调用接口，不改时间、不生成新的价格结果。
    with target.open('xb') as stream:
        stream.write(response.read_bytes())
    save(target.with_suffix('.access.json'), {**read(access),
        'reused_from': str(response), 'reconciliation_reader': grant['thread_id'],
        'reconciliation_purpose': 'USER_APPROVED_RESUME_EXISTING_ACQUISITION'})
    save(ROOT/'FETCH_V4_HANDOFF.json', {
        'approval_statement': '恢复流程啊，我说怎么没有变化，这个进度',
        'release_identity': grant['identity'], 'at': datetime.now(timezone.utc).isoformat(),
        'failed_workers': {str(failed): sha(failed)},
        'original_request_sha256': sha(original), 'response_sha256': sha(response),
        'access_sha256': sha(access), 'retry_receipt_sha256': sha(receipt),
        'reused_target': str(target), 'reused_target_sha256': sha(target),
        'script_sha256': sha(Path(__file__)), 'old_failures_preserved': True,
        'new_exposures': 0, 'budget_unchanged': True})
    print({'reconciled': True, 'rows': len(read(response)['rows']), 'network_requests': 0})


if __name__ == '__main__':
    run()
