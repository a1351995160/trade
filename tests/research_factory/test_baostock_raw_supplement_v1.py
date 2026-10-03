import json

import pytest

from chanlun_trader.research_factory.baostock_raw_supplement_v1 import (
    RAW_FIELDS, collected_raw_source_v1, read_collected_raw_v1,
)
from scripts import collect_universe_gaps_v1 as collector
from scripts.resolve_universe_action_dates_v1 import _market_source_readonly
from tests.research_factory.test_universe_actions_v1 import FakeClient, Response, sha, write


class RawClient(FakeClient):
    def query_history_k_data_plus(self, **query):
        fields = query['fields'].split(',')
        row = {'date': query['start_date'], 'code': query['code'], 'open': '10', 'high': '11',
            'low': '9', 'close': '10', 'preclose': '10', 'volume': '1000', 'amount': '10000',
            'adjustflag': '3', 'tradestatus': '1', 'isST': '0'}
        return Response(fields, [[row[f] for f in fields]])


def raw_fixture(tmp_path):
    item = {'file': 'RAW_000001.SZ.json', 'api': 'query_history_k_data_plus',
        'request': {'code': 'sz.000001', 'fields': ','.join(sorted(RAW_FIELDS)),
            'frequency': 'd', 'adjustflag': '3', 'start_date': '2023-01-03', 'end_date': '2023-01-09'}}
    batch = {'batch_id': 'RAW_1', 'purpose': 'EXPLORATORY_ENGINEERING_ACCEPTANCE',
        'authorization_scope': 'SYNTHETIC', 'max_requests': 9, 'requests': [item]}
    queue = {'version': 'UNIVERSE_BAOSTOCK_SUPPLEMENTS_V1', 'request_count': 1,
        'authorization_source': {'origin': 'USER_EXPLICIT_CURRENT_TASK',
            'statement': '合成测试', 'scope': 'SYNTHETIC'}, 'batches': [batch]}
    path = tmp_path / 'queue.json'
    write(path, queue)
    root = tmp_path / 'acquisition'
    collector.collect(path, root, client=RawClient({}))
    directory = root / 'batches/RAW_1'
    result = json.loads((directory / 'ACQUISITION_RESULT.json').read_text(encoding='utf-8'))
    source = collected_raw_source_v1(directory, item, result['responses'][0],
        result_sha256=sha(directory / 'ACQUISITION_RESULT.json'))
    plan = {'symbols': ['000001.SZ'], 'start': '2023-01-03', 'end': '2023-01-09'}
    return source, plan


def test_native_collector_raw_is_read_without_fake_legacy_rewriting(tmp_path):
    source, plan = raw_fixture(tmp_path)
    before = open(source['path'], 'rb').read()
    rows, binding = read_collected_raw_v1(source, plan)
    assert rows[0]['preclose'] == '10'
    assert binding['format'] == 'BAOSTOCK_COLLECTOR_RAW_V1'
    assert binding['historical_available_at_verified'] is False
    assert _market_source_readonly(source, plan) == (rows, binding)
    assert open(source['path'], 'rb').read() == before
    assert json.loads(before)['api'] == 'query_history_k_data_plus'
    assert 'query' not in json.loads(before)


@pytest.mark.parametrize('mutation', ['source_identity', 'raw_bytes', 'result_bytes', 'frozen_window'])
def test_raw_or_receipt_identity_changes_fail_closed(tmp_path, mutation):
    source, plan = raw_fixture(tmp_path)
    if mutation == 'source_identity':
        source['row_count'] += 1
    elif mutation == 'raw_bytes':
        with open(source['path'], 'ab') as stream:
            stream.write(b' ')
    elif mutation == 'result_bytes':
        with open(source['result_path'], 'ab') as stream:
            stream.write(b' ')
    else:
        plan['start'] = '2023-01-04'
    with pytest.raises(ValueError):
        read_collected_raw_v1(source, plan)
