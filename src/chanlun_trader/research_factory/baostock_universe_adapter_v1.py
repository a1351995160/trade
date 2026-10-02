"""将已登记 BaoStock 原件接入全范围输入；复用旧校验，保留历史建模边界。"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

import pandas as pd

from ..research.guard import ResearchDataAccessGuard
from .research_data_provider_v1 import _baostock_bundle, day


ADAPTER_VERSION = 'BAOSTOCK_FULL_UNIVERSE_V1'
_SYMBOL_PATTERN = r'(?:00\d{4}\.SZ|60\d{4}\.SH|30\d{4}\.SZ)'
_TIMING_FIELDS = ('available_at', 'effective_available_at', 'researcher_available_at')
_KINDS = {
    'query_trade_dates': 'CALENDAR',
    'query_history_k_data_plus': 'DAILY',
    'query_dividend_data': 'EVENTS',
    'query_adjust_factor': 'ADJUST',
}
_HEADER_KEYS = {'provider', 'api', 'request', 'requested_at_utc', 'received_at_utc',
                'historical_available_at_verified', 'mode', 'fields', 'error_code',
                'error_msg', 'raw_rows_sha256'}


def _response_header(path):
    """无缓冲逐字节读元数据，遇 raw_rows 键即停，不预读行情数组。"""
    header, pending, count = {}, None, 0
    with Path(path).open('rb', buffering=0) as stream:
        def byte():
            nonlocal pending, count
            if pending is not None:
                result, pending = pending, None
                return result
            result = stream.read(1)
            count += len(result)
            if count > 65536 or not result:
                raise ValueError('DATA_VENDOR_HEADER_INCOMPLETE_OR_TOO_LARGE')
            return result

        def nonspace():
            result = byte()
            while result in b' \t\r\n':
                result = byte()
            return result

        def value(first):
            nonlocal pending
            raw = bytearray(first)
            depth = 1 if first in (b'{', b'[') else 0
            quoted, escaped = first == b'"', False
            if not quoted and not depth:
                while True:
                    token = byte()
                    if token in (b',', b'}'):
                        pending = token
                        break
                    raw.extend(token)
            else:
                while True:
                    token = byte()
                    raw.extend(token)
                    if quoted:
                        if escaped:
                            escaped = False
                        elif token == b'\\':
                            escaped = True
                        elif token == b'"':
                            quoted = False
                            if depth == 0:
                                break
                    elif token == b'"':
                        quoted = True
                    elif token in (b'{', b'['):
                        depth += 1
                    elif token in (b'}', b']'):
                        depth -= 1
                        if depth == 0:
                            break
            try:
                return json.loads(raw.decode('utf-8'))
            except (ValueError, UnicodeDecodeError):
                raise ValueError('DATA_VENDOR_HEADER_INVALID') from None

        first = nonspace()
        if first == b'\xef':
            if byte() != b'\xbb' or byte() != b'\xbf':
                raise ValueError('DATA_VENDOR_HEADER_INVALID')
            first = nonspace()
        if first != b'{':
            raise ValueError('DATA_VENDOR_HEADER_INVALID')
        while True:
            if nonspace() != b'"':
                raise ValueError('DATA_VENDOR_HEADER_INVALID')
            key = value(b'"')
            if key == 'raw_rows':
                return header
            if key not in _HEADER_KEYS or key in header:
                raise ValueError('DATA_VENDOR_HEADER_FIELD_INVALID')
            if nonspace() != b':':
                raise ValueError('DATA_VENDOR_HEADER_INVALID')
            header[key] = value(nonspace())
            if nonspace() != b',':
                raise ValueError('DATA_VENDOR_HEADER_INCOMPLETE_OR_TOO_LARGE')


def guard_baostock_response_header_v1(path, metadata, *, authorization=None,
                                      expected_api=None, dividend_year_type=None):
    """在整文件读取/哈希之前核对实际查询范围；不能靠虚假的 manifest 绕封存。"""
    guard = ResearchDataAccessGuard()
    lo, hi = day(metadata['start']), day(metadata['end'])
    guard.check_range(lo, hi, 'BaoStock declared whole source')
    header = _response_header(path)
    api, request = header.get('api'), header.get('request')
    if (header.get('provider') != 'BaoStock' or api not in _KINDS
            or expected_api is not None and api != expected_api
            or metadata.get('kind', _KINDS[api]) != _KINDS[api]
            or not isinstance(request, dict) or header.get('error_code') != '0'
            or header.get('historical_available_at_verified') is not False):
        raise ValueError('DATA_VENDOR_RESPONSE_HEADER_INVALID')
    if api != 'query_trade_dates':
        code = request.get('code')
        if not isinstance(code, str) or re.fullmatch(r'(?:sz\.(?:00|30)\d{4}|sh\.60\d{4})', code) is None:
            raise ValueError('DATA_VENDOR_RESPONSE_HEADER_SYMBOL_INVALID')
        symbol = code[3:] + ('.SZ' if code.startswith('sz.') else '.SH')
        declared = metadata.get('symbols') or ([metadata['symbol']] if metadata.get('symbol') else [])
        if declared and symbol not in declared:
            raise ValueError('DATA_VENDOR_RESPONSE_HEADER_SYMBOL_CONFLICT')
    if api == 'query_dividend_data':
        year_type = request.get('yearType')
        if (year_type not in {'report', 'operate'}
                or dividend_year_type is not None and year_type != dividend_year_type
                or re.fullmatch(r'[0-9]{4}', str(request.get('year', ''))) is None):
            raise ValueError('DATA_DIVIDEND_YEAR_TYPE_INVALID')
        year = int(request['year'])
        actual_start, actual_end = year * 10000 + 101, (
            year + (year_type == 'report')) * 10000 + 1231
    else:
        actual_start, actual_end = day(request['start_date']), day(request['end_date'])
    guard.check_range(actual_start, actual_end, 'BaoStock actual query header')
    if actual_start > actual_end or actual_start < lo or actual_end > hi:
        raise ValueError('DATA_QUERY_SCOPE_NOT_COVERED_BY_MANIFEST')
    if authorization is not None and (actual_start < day(authorization['start'])
                                     or actual_end > day(authorization['end'])):
        raise ValueError('DATA_WHOLE_SOURCE_NOT_AUTHORIZED')
    return {'header': header, 'request_scope': {'start': actual_start, 'end': actual_end}}


def prepare_baostock_universe_v1(root, manifest, *, dataset_id, symbols, feature_start,
                                account_start, account_end, required_fields,
                                authorization, access_recorder):
    """返回已校验 window/bundle/checks；股票池、上市来源由正式 Provider 绑定。

    先检查整文件登记范围和授权，再记录访问并读取原件。公司行动覆盖来自
    同一批原件的股息、复权事件日期与每个未复权 preclose 转换校验；空股息
    响应只有与其他证据一致时才表示该窗口无行动。未知上市日期不在这里补猜，
    历史可见性保持 MODELED；原件已有具体时间则逐字保留供新入口继续检查。
    """
    if not callable(access_recorder):
        raise ValueError('DATA_ACCESS_RECORDER_REQUIRED')
    root = Path(root).absolute()
    if root.resolve() != root or not root.is_dir():
        raise ValueError('DATA_ROOT_INVALID')
    if (not isinstance(manifest, dict) or manifest.get('adapter') != ADAPTER_VERSION
            or not isinstance(manifest.get('files'), dict) or not manifest['files']):
        raise ValueError('DATA_MANIFEST_INVALID')
    if (not isinstance(symbols, (list, tuple)) or not symbols
            or any(not isinstance(s, str) or re.fullmatch(_SYMBOL_PATTERN, s) is None
                   for s in symbols) or len(set(symbols)) != len(symbols)):
        raise ValueError('DATA_SYMBOLS_INVALID')
    start, account, end = map(day, (feature_start, account_start, account_end))
    guard = ResearchDataAccessGuard()
    guard.check_range(start, end, 'BaoStock full universe request')
    if (not isinstance(authorization, dict)
            or authorization.get('purpose') != 'EXPLORATORY'
            or not authorization.get('authorization_id')
            or dataset_id not in authorization.get('dataset_ids', [])
            or not authorization.get('start') or not authorization.get('end')
            or start < day(authorization['start']) or end > day(authorization['end'])):
        raise ValueError('DATA_ACCESS_NOT_AUTHORIZED')
    if start < day(manifest['start']) or end > day(manifest['end']):
        raise ValueError('DATA_WINDOW_NOT_COVERED')
    dividend_year_type = manifest.get('dividend_year_type', 'report')
    if dividend_year_type not in {'report', 'operate'}:
        raise ValueError('DATA_DIVIDEND_YEAR_TYPE_INVALID')
    if (not isinstance(required_fields, (list, tuple, set))
            or not all(isinstance(field, str) for field in required_fields)
            or not set(required_fields) <= {
                'open', 'high', 'low', 'close', 'prev_close', 'volume', 'amount', 'turn'}):
        raise ValueError('DATA_FIELD_UNSUPPORTED')

    cache, hashes, timing = {}, {}, {}

    def read(path):
        relative = path.relative_to(root).as_posix()
        if relative not in manifest['files']:
            raise ValueError('DATA_SOURCE_NOT_REGISTERED:' + relative)
        metadata = manifest['files'][relative]
        lo, hi = day(metadata['start']), day(metadata['end'])
        guard.check_range(lo, hi, 'BaoStock full universe whole source')
        if lo < day(authorization['start']) or hi > day(authorization['end']):
            raise ValueError('DATA_WHOLE_SOURCE_NOT_AUTHORIZED')
        if relative not in cache:
            safe = root / relative
            if ('..' in Path(relative).parts or safe.resolve() != safe or not safe.is_file()):
                raise ValueError('DATA_PATH_INVALID_OR_REDIRECTED')
            digest = metadata.get('sha256')
            if not isinstance(digest, str) or re.fullmatch(r'[0-9a-f]{64}', digest) is None:
                raise ValueError('DATA_SOURCE_IDENTITY_INVALID')
            source_id = metadata.get('source_id')
            if not isinstance(source_id, str) or not source_id:
                raise ValueError('DATA_SOURCE_IDENTITY_INVALID')
            guard_baostock_response_header_v1(safe, metadata, authorization=authorization,
                dividend_year_type=dividend_year_type if metadata['kind'] == 'EVENTS' else None)
            access_recorder({'event': 'DATA_CONTENT_READ_ATTEMPT', 'dataset_id': dataset_id,
                'source': relative, 'expected_sha256': digest, 'purpose': 'EXPLORATORY',
                'authorization_id': authorization['authorization_id'], 'start': lo, 'end': hi,
                'adapter_version': ADAPTER_VERSION})
            raw = safe.read_bytes()
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError('DATA_SOURCE_CONTENT_CHANGED:' + relative)
            if relative in hashes and hashes[relative] != digest:
                raise ValueError('DATA_SOURCE_IDENTITY_CONFLICT')
            cache[relative], hashes[relative] = raw, digest
            if source_id in hashes and hashes[source_id] != digest:
                raise ValueError('DATA_SOURCE_IDENTITY_CONFLICT')
            hashes[source_id] = digest
        return cache[relative]

    def response(path, api):
        relative = path.relative_to(root).as_posix()
        metadata = manifest['files'].get(relative)
        if metadata is None:
            raise ValueError('DATA_SOURCE_NOT_REGISTERED:' + relative)
        if (metadata.get('format') != 'BAOSTOCK_RESPONSE_JSON'
                or metadata.get('kind') != _KINDS[api]):
            raise ValueError('DATA_VENDOR_FORMAT_INVALID:' + relative)
        value = json.loads(read(path).decode('utf-8-sig'))
        if (not isinstance(value, dict) or value.get('provider') != 'BaoStock'
                or value.get('api') != api or value.get('error_code') != '0'
                or value.get('historical_available_at_verified') is not False):
            raise ValueError('DATA_VENDOR_RESPONSE_INVALID')
        if (not isinstance(value.get('fields'), list) or not value['fields']
                or len(set(value['fields'])) != len(value['fields'])
                or not isinstance(value.get('raw_rows'), list)
                or any(not isinstance(row, list) or len(row) != len(value['fields'])
                       for row in value['raw_rows'])):
            raise ValueError('DATA_VENDOR_RESPONSE_SHAPE_INVALID')
        digest = hashlib.sha256(json.dumps(value['raw_rows'], ensure_ascii=False,
            separators=(',', ':')).encode()).hexdigest()
        if digest != value.get('raw_rows_sha256'):
            raise ValueError('DATA_VENDOR_ROWS_HASH_INVALID')
        frame = pd.DataFrame(value['raw_rows'], columns=value['fields'])
        request = value['request']
        for field in frame:
            if field not in {'date', 'calendar_date'} and not field.endswith('Date'):
                continue
            actual_dates = [day(d) for d in frame[field] if d not in (None, '')]
            guard.check_int_iterable(actual_dates, 'BaoStock actual whole response dates')
            if any(d < day(metadata['start']) or d > day(metadata['end'])
                   or d < day(authorization['start']) or d > day(authorization['end'])
                   for d in actual_dates):
                raise ValueError('DATA_RESPONSE_DATE_OUTSIDE_SOURCE_SCOPE')
            if api in {'query_trade_dates', 'query_history_k_data_plus', 'query_adjust_factor'} and (
                    field in {'date', 'calendar_date', 'dividOperateDate'} and any(
                    d < day(request['start_date']) or d > day(request['end_date']) for d in actual_dates)):
                raise ValueError('DATA_RESPONSE_DATE_OUTSIDE_QUERY_SCOPE')
        if api == 'query_dividend_data' and dividend_year_type == 'operate' and not frame.empty:
            request_year = int(value['request']['year'])
            if ('dividOperateDate' not in frame or any(day(effective) // 10000 != request_year
                                                     for effective in frame.dividOperateDate)):
                raise ValueError('DATA_DIVIDEND_OPERATION_YEAR_CONFLICT')
        if api == 'query_history_k_data_plus' and 'date' in frame:
            # 这里只保留原字段；全部行情、状态和行动验证通过后才绑定到 bundle。
            columns = [key for key in _TIMING_FIELDS if key in frame]
            if columns:
                timing[relative] = frame[['date', *columns]].copy()
        # 该原件的哈希已验证；不随股票数累积原始 JSON 字节副本。
        cache.pop(relative, None)
        return value, frame

    def file_hash(path):
        relative = path.relative_to(root).as_posix()
        if relative not in hashes:
            read(path)
        return hashes[relative]

    window, bundle, checks = _baostock_bundle(root, symbols=symbols,
        feature_start=start, account_start=account, account_end=end,
        response=response, file_hash=file_hash,
        required_fields=required_fields, symbol_pattern=_SYMBOL_PATTERN,
        include_warmup_states=True, dividend_year_type=dividend_year_type,
        preserve_partial_turn=True)
    # profile 是模式标记，不能混进新入口的 SHA-256 来源表。
    bundle['source_hashes'] = deepcopy(hashes)
    bundle['calendar_source'] = manifest['files']['TRADE_DATES.json']['source_id']
    bundle['historical_availability'] = 'MODELED'
    bundle['price_basis'] = {'execution': 'RAW', 'indicators': 'CAUSAL_CASH_ACTION_TRANSFORM'}
    bundle['field_sources'] = {'prev_close': {
        symbol: manifest['files'][f'DAILY_{symbol}.json']['source_id'] for symbol in symbols}}
    states = bundle['states']
    states['source'] = states.symbol.map({symbol: manifest['files'][
        f'DAILY_{symbol}.json']['source_id'] for symbol in symbols})
    states['availability_status'] = 'MODELED'
    states['board'] = states.symbol.map({symbol: 'SH_MAIN' if symbol.startswith('60') else
        'CHINEXT' if symbol.startswith('30') else 'SZ_MAIN' for symbol in symbols})
    for field in _TIMING_FIELDS:
        values = {}
        for symbol in symbols:
            own = timing.get(f'DAILY_{symbol}.json')
            if own is not None and field in own:
                values.update({(symbol, day(row['date'])): row[field]
                    for row in own[['date', field]].to_dict('records')
                    if start <= day(row['date']) <= end})
        if values:
            states[field] = [values.get((row.symbol, row.trade_date))
                             for row in states.itertuples(index=False)]
    bundle['corporate_action_coverage'] = [{
        'symbols': [symbol], 'start': start, 'end': end, 'complete': True,
        'event_types': ['CASH_DIVIDEND'],
        'source': manifest['files'][f'ADJUST_{symbol}.json']['source_id'],
        'coverage_basis': checks['coverage_basis'],
        'evidence_sources': [manifest['files'][name]['source_id'] for name in (
            f'DAILY_{symbol}.json', f'ADJUST_{symbol}.json',
            *(f'DIVIDEND_{symbol}_{year}.json'
              for year in range(start // 10000, end // 10000 + 1)))],
    } for symbol in window['symbols']]
    return {'window': window, 'bundle': bundle, 'checks': checks}
