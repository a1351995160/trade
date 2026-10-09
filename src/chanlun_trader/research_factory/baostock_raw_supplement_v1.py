"""只读核验新采集器原生 RAW 响应，不改写供应商格式或历史可见性。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .research_universe_v1 import _day, canonical_symbol


VERSION = 'BAOSTOCK_COLLECTOR_RAW_V1'
RAW_FIELDS = {'date', 'code', 'open', 'high', 'low', 'close', 'preclose',
              'volume', 'amount', 'adjustflag', 'tradestatus', 'isST'}
_AVAILABILITY = {'available_at', 'effective_available_at', 'researcher_available_at'}


def collected_raw_source_v1(directory, item, result_row, *, result_sha256):
    """先验证 START、成功响应及完整结果文件，生成可重复消费的身份。"""
    from scripts.collect_universe_gaps_v1 import _file_hash, verify_collected_response_v1
    directory = Path(directory).absolute()
    result_path = directory / 'ACQUISITION_RESULT.json'
    if _file_hash(result_path) != result_sha256:
        raise ValueError('RAW_SUPPLEMENT_RESULT_CHANGED')
    result = json.loads(result_path.read_text(encoding='utf-8'))
    if result_row not in result.get('responses', []):
        raise ValueError('RAW_SUPPLEMENT_RESPONSE_NOT_IN_RESULT')
    witness = verify_collected_response_v1(directory, item, result_row)
    query = item['request']
    if (item['api'] != 'query_history_k_data_plus' or query.get('frequency') != 'd'
            or query.get('adjustflag') != '3'
            or not RAW_FIELDS <= set(query.get('fields', '').split(','))
            or _AVAILABILITY & set(query.get('fields', '').split(','))):
        raise ValueError('RAW_SUPPLEMENT_RAW_DAILY_FIELDS_REQUIRED')
    source = {'format': VERSION, 'symbol': canonical_symbol(query['code']),
        'path': witness['source_path'], 'sha256': witness['source_sha256'],
        'start': _day(query['start_date']), 'end': _day(query['end_date']),
        'row_count': witness['row_count'], 'source_captured_at_utc': witness['received_at_utc'],
        'historical_available_at_verified': False,
        'request_item': item, 'result_row': result_row,
        'result_path': str(result_path), 'result_sha256': result_sha256,
        'started_sha256': witness['start_sha256'], 'request_witness': witness}
    return source


def read_collected_raw_v1(source, plan):
    """返回原生响应的字典行和来源身份；缺/冲突/变更全部拒绝。"""
    if not isinstance(source, dict) or source.get('format') != VERSION:
        raise ValueError('RAW_SUPPLEMENT_FORMAT_REQUIRED')
    path = Path(source['path']).absolute()
    verified = collected_raw_source_v1(path.parent, source['request_item'],
        source['result_row'], result_sha256=source['result_sha256'])
    if source != verified:
        raise ValueError('RAW_SUPPLEMENT_SOURCE_IDENTITY_CHANGED')
    query = source['request_item']['request']
    if (source['symbol'] not in plan['symbols'] or query['start_date'] != plan['start']
            or query['end_date'] != plan['end']):
        raise ValueError('RAW_SUPPLEMENT_FROZEN_WINDOW_CHANGED')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != source['sha256']:
        raise ValueError('RAW_SUPPLEMENT_SOURCE_CHANGED_DURING_READ')
    value = json.loads(raw.decode('utf-8'))
    if not RAW_FIELDS <= set(value['fields']) or _AVAILABILITY & set(value['fields']):
        raise ValueError('RAW_SUPPLEMENT_RESPONSE_FIELDS_INVALID')
    rows, seen = [], set()
    for values in value['raw_rows']:
        row = dict(zip(value['fields'], values))
        day = _day(row['date'])
        if (day in seen or not source['start'] <= day <= source['end']
                or row['code'] != query['code'] or row['adjustflag'] != '3'):
            raise ValueError('RAW_SUPPLEMENT_ROW_IDENTITY_INVALID')
        seen.add(day)
        rows.append(row)
    binding = {**source, 'query_covers_before_first_bar':
        source['start'] < min(seen, default=0)}
    return rows, binding
