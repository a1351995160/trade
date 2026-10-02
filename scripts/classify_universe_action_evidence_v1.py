"""分类限定 GBBQ 包内的日期证据，生成补公告队列；不裁决或物化事件。"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import re
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / 'src'))

from chanlun_trader.presentation import ZhCNPresentation
from chanlun_trader.research.guard import ResearchDataAccessGuard


VERSION = 'UNIVERSE_ACTION_EVIDENCE_CLASSIFICATION_V1'
BINDINGS_VERSION = 'UNIVERSE_ACTION_EVIDENCE_INPUT_BINDINGS_V1'
QUEUE_VERSION = 'TARGETED_ACTION_DATE_ANNOUNCEMENT_QUEUE_V1'
WINDOW_START, WINDOW_END = 20220722, 20240731
NEARBY_CALENDAR_DAYS = 7
INPUT_ROLES = ('queue', 'receipt', 'price_gaps', 'gbbq_window',
               'actions_manifest', 'gbbq_read_audit')
RAW_NUMERIC_FIELDS = ('hongli_panqianliutong', 'peigujia_qianzongguben',
                      'songgu_qianzongguben', 'peigu_houzongguben')
RAW_FIELDS = ('market', 'code', 'datetime', 'category', *RAW_NUMERIC_FIELDS, 'symbol')


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _content_hash(value):
    return _hash(json.dumps(value, ensure_ascii=False, sort_keys=True,
                            separators=(',', ':'), allow_nan=False).encode('utf-8'))


def _path(value):
    path = Path(value).absolute()
    if path.resolve() != path or not path.is_file():
        raise ValueError('ACTION_CLASSIFICATION_CANONICAL_INPUT_REQUIRED')
    return path


def _read_bound(path, expected, role):
    if not isinstance(expected, str) or re.fullmatch(r'[a-f0-9]{64}', expected) is None:
        raise ValueError('ACTION_CLASSIFICATION_INPUT_SHA_REQUIRED:' + role)
    raw = path.read_bytes()
    if _hash(raw) != expected:
        raise ValueError('ACTION_CLASSIFICATION_INPUT_CHANGED:' + role)
    return raw


def _day(value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError('ACTION_CLASSIFICATION_DATE_INVALID')
    try:
        datetime.strptime(str(value), '%Y%m%d')
    except ValueError as exc:
        raise ValueError('ACTION_CLASSIFICATION_DATE_INVALID') from exc
    return value


def _symbol(value):
    if not isinstance(value, str) or re.fullmatch(r'\d{6}\.(SZ|SH)', value) is None:
        raise ValueError('ACTION_CLASSIFICATION_SYMBOL_INVALID')
    return value


def _key(row):
    return _symbol(row['symbol']), _day(row['effective_date'])


def _nearby_window(day, start, end):
    value = datetime.strptime(str(day), '%Y%m%d')
    lo = int((value - timedelta(days=NEARBY_CALENDAR_DAYS)).strftime('%Y%m%d'))
    hi = int((value + timedelta(days=NEARBY_CALENDAR_DAYS)).strftime('%Y%m%d'))
    return {'start': max(start, lo), 'end': min(end, hi)}


def _validate_package(manifest, audit, expected_window, window, manifest_path):
    start, end = _day(manifest.get('start')), _day(manifest.get('end'))
    attestation = manifest.get('physical_window_attestation', {})
    if (manifest.get('version') != 'WindowedCorporateActionDatasetV1'
            or not WINDOW_START <= start <= end <= WINDOW_END
            or not isinstance(attestation, dict)
            or attestation.get('start') != start or attestation.get('end') != end
            or attestation.get('window_enforced_before_export') is not True
            or attestation.get('not_derived_from_current_incident') is not True
            or attestation.get('owner') != 'USER_AUTHORIZED_LOCAL_DATA_OWNER'
            or re.fullmatch(r'[a-f0-9]{64}', str(attestation.get('authorization_sha256'))) is None):
        raise ValueError('ACTION_CLASSIFICATION_BOUNDED_PACKAGE_REQUIRED')
    ResearchDataAccessGuard().check_range(start, end, 'classification whole GBBQ export')
    if window.name != 'gbbq_window.jsonl' or window.parent != manifest_path.parent:
        raise ValueError('ACTION_CLASSIFICATION_WINDOW_EXPORT_REQUIRED')
    source = manifest.get('source_identity')
    if (re.fullmatch(r'[a-f0-9]{64}', str(source)) is None
            or audit.get('source_sha256') != source
            or manifest.get('dataset_id') != 'OWNER_GBBQ_' + source
            or audit.get('output_sha256') != expected_window
            or audit.get('parser_version') != 'pytdx.GbbqReader'
            or re.fullmatch(r'[a-f0-9]{64}', str(audit.get('parser_sha256'))) is None
            or not isinstance(audit.get('window_records'), int)
            or isinstance(audit['window_records'], bool) or audit['window_records'] < 0):
        raise ValueError('ACTION_CLASSIFICATION_PACKAGE_IDENTITY_CONFLICT')
    return start, end


def _validate_targets(queue, receipt, gaps, start, end):
    requests = queue.get('requests')
    if (queue.get('version') != 'TARGETED_UNKNOWN_DATE_EVIDENCE_QUEUE_V2'
            or not isinstance(requests, list) or not requests
            or queue.get('request_count') != len(requests)
            or queue.get('original_30_unknown_statuses_preserved') is not True
            or queue.get('unbounded_search_authorized') is not False
            or queue.get('automatic_execution') is not False
            or queue.get('automatic_retry') is not False
            or receipt.get('version') != 'UNIVERSE_ACTION_DATE_RESOLUTION_V1'
            or not isinstance(gaps, list)):
        raise ValueError('ACTION_CLASSIFICATION_ORIGINAL_QUEUE_REQUIRED')
    targets = {}
    for request in requests:
        key = _key(request)
        window = request.get('announcement_publication_window', {})
        lo, hi = _day(window.get('start')), _day(window.get('end'))
        related = _day(request.get('related_action_date'))
        if (key in targets or not start <= key[1] <= end
                or not start <= related <= key[1]
                or not lo <= hi <= key[1]
                or request.get('status') != 'NOT_EXECUTED'
                or request.get('automatic_retry') is not False
                or request.get('final_test_guard_required_before_any_hash_or_body_read') is not True
                or request.get('maximum_metadata_pages') != 1
                or request.get('maximum_selected_original_documents') != 2):
            raise ValueError('ACTION_CLASSIFICATION_TARGET_SCOPE_CONFLICT')
        ResearchDataAccessGuard().check_range(lo, key[1], 'classification target evidence')
        targets[key] = request
    unknown = {}
    for record in receipt.get('resolutions', []):
        if record.get('status') != 'UNKNOWN':
            continue
        key = _key(record)
        if key in unknown or _key(record.get('original_gap', {})) != key:
            raise ValueError('ACTION_CLASSIFICATION_RECEIPT_TARGET_CONFLICT')
        unknown[key] = record
    date_gaps = {}
    for gap in gaps:
        if gap.get('reason') != 'ADJUST_AND_ACTION_DATES_CONFLICT':
            continue
        key = _key(gap)
        if key in date_gaps or gap.get('status') != 'UNKNOWN':
            raise ValueError('ACTION_CLASSIFICATION_PRICE_GAP_TARGET_CONFLICT')
        date_gaps[key] = gap
    if set(targets) != set(unknown) or set(targets) != set(date_gaps):
        raise ValueError('ACTION_CLASSIFICATION_ORIGINAL_UNKNOWN_SET_CHANGED')
    for key, request in targets.items():
        record = unknown[key]
        if (record.get('interpretation') != request.get('reason')
                or record['original_gap'] != date_gaps[key]
                or record.get('historical_available_at_verified') is not False
                or record.get('global_no_other_event_claim') is not False):
            raise ValueError('ACTION_CLASSIFICATION_RECEIPT_EVIDENCE_CONFLICT')
        for source in record.get('adjust_sources', []):
            for name in ('raw_row', 'previous_raw_row'):
                row = source.get(name)
                if row is not None and row.get('code') != key[0][-2:].lower() + '.' + key[0][:6]:
                    raise ValueError('ACTION_CLASSIFICATION_RECEIPT_STOCK_CONFLICT')
            row = source.get('raw_row')
            if row and int(str(row.get('dividOperateDate')).replace('-', '')) != key[1]:
                raise ValueError('ACTION_CLASSIFICATION_RECEIPT_DATE_CONFLICT')
        if (request['related_action_date'] != key[1]
                and record.get('factor_and_market_comparison', {}).get('previous_factor_date')
                != request['related_action_date']):
            raise ValueError('ACTION_CLASSIFICATION_RELATED_DATE_CONFLICT')
    return targets, unknown


def _rows(raw, start, end, expected_count):
    result = defaultdict(list)
    count = 0
    for line_number, line in enumerate(raw.decode('utf-8-sig').splitlines(), start=1):
        if not line.strip():
            raise ValueError('ACTION_CLASSIFICATION_GBBQ_EMPTY_LINE')
        row = json.loads(line)
        if not isinstance(row, dict) or not set(RAW_FIELDS) <= set(row):
            raise ValueError('ACTION_CLASSIFICATION_GBBQ_FIELDS_INVALID')
        symbol, day = _symbol(row['symbol']), _day(row['datetime'])
        if not start <= day <= end:
            raise ValueError('ACTION_CLASSIFICATION_GBBQ_ROW_OUTSIDE_WINDOW')
        ResearchDataAccessGuard().check_date(day, 'classification GBBQ row')
        market = 0 if symbol.endswith('.SZ') else 1
        if (type(row['market']) is not int or row['market'] != market
                or row['code'] != symbol[:6] or type(row['category']) is not int
                or not 1 <= row['category'] <= 255):
            raise ValueError('ACTION_CLASSIFICATION_GBBQ_STOCK_OR_CATEGORY_CONFLICT')
        if any(type(row[name]) not in (int, float) or not math.isfinite(row[name])
               for name in RAW_NUMERIC_FIELDS):
            raise ValueError('ACTION_CLASSIFICATION_GBBQ_NUMBER_INVALID')
        result[symbol].append({'line_number': line_number,
                              'row_sha256': _content_hash(row), 'raw_fields': row})
        count += 1
    # OWNER 原审计计数在 required_members 筛选之前产生；output_sha256 才绑定导出字节。
    if count > expected_count:
        raise ValueError('ACTION_CLASSIFICATION_GBBQ_RECORD_COUNT_CONFLICT')
    return result, count


def _announcement_suggestions(categories, exact, nearby):
    kinds, keywords, requirements = [], [], []
    relevant = exact or nearby
    if 15 in categories:
        kinds.extend(['RESTRUCTURING_IMPLEMENTATION_NOTICE', 'CAPITAL_CHANGE_AND_EX_PRICE_NOTICE'])
        keywords.extend(['重整', '资本公积金转增', '除权', '股本', '参考价格'])
        requirements.extend(['EFFECTIVE_DATE_AND_RECORD_DATE', 'SHARE_ALLOCATION_AND_CAPITAL_CHANGE',
                             'EX_PRICE_FORMULA_AND_REFERENCE_PRICE', 'ACCOUNT_CREDIT_AND_TAX_TERMS'])
    if 1 in categories:
        kinds.append('PROFIT_DISTRIBUTION_IMPLEMENTATION_NOTICE')
        keywords.extend(['权益分派', '实施公告', '除权除息'])
        requirements.extend(['EXACT_ACTION_DATE_AND_AMOUNTS', 'RECORD_PAYMENT_AND_LISTING_DATES'])
        if any(row['raw_fields']['songgu_qianzongguben'] != 0 for row in relevant):
            keywords.extend(['送股', '转增', '新增股份上市'])
            requirements.extend(['BONUS_VERSUS_CAPITALIZATION_ORIGIN', 'ACCOUNT_CREDIT_AND_TAX_TERMS'])
        if any(row['raw_fields']['peigu_houzongguben'] != 0 for row in relevant):
            kinds.append('RIGHTS_ISSUE_IMPLEMENTATION_NOTICE')
            keywords.extend(['配股', '配股缴款', '上市'])
            requirements.append('RIGHTS_SUBSCRIPTION_AND_LISTING_TERMS')
    if categories - {1, 15}:
        kinds.append('CAPITAL_CHANGE_IMPLEMENTATION_NOTICE')
        keywords.extend(['股本变动', '实施公告', '除权', '参考价格'])
        requirements.extend(['TDX_CATEGORY_FIELD_SEMANTICS', 'CAPITAL_CHANGE_AND_PRICE_EFFECT'])
    if not exact:
        kinds.append('PROVIDER_FACTOR_FIELD_SEMANTICS_EVIDENCE')
        requirements.extend(['ADJUST_FACTOR_ROW_VERSUS_ACTUAL_EVENT', 'BOUNDED_COVERAGE_COMPLETENESS'])
        if not nearby:
            kinds.append('EXACT_ACTION_OR_CAPITAL_CHANGE_IMPLEMENTATION_NOTICE')
            keywords.extend(['权益分派', '股本变动', '实施公告', '除权'])
    return {'announcement_types': list(dict.fromkeys(kinds)),
            'search_keywords': list(dict.fromkeys(keywords)),
            'required_proofs': list(dict.fromkeys(requirements))}


def _classify(request, record, rows, start, end):
    symbol, day = _key(request)
    nearby_window = _nearby_window(day, start, end)
    exact, nearby = [], []
    for original in rows:
        row = dict(original)
        row_day = row['raw_fields']['datetime']
        if row_day == day:
            row['relation'] = 'EXACT_EFFECTIVE_DATE'
            exact.append(row)
        elif nearby_window['start'] <= row_day <= nearby_window['end']:
            row['relation'] = ('RELATED_ACTION_DATE' if row_day == request['related_action_date']
                               else 'NEARBY_DATE')
            nearby.append(row)
    categories = {row['raw_fields']['category'] for row in exact}
    near_categories = {row['raw_fields']['category'] for row in nearby}
    differences, missing_capabilities = [], []
    if 1 in categories:
        differences.append('EXACT_TDX_CATEGORY_1_RECORD_WITH_BAOSTOCK_DATE_GAP')
        missing_capabilities.append('CROSS_PROVIDER_EVENT_TERMS_NOT_QUALIFIED')
    if 15 in categories:
        differences.append('EXACT_TDX_CATEGORY_15_READJUSTMENT_REQUIRES_IMPLEMENTATION_EVIDENCE')
        missing_capabilities.append('CATEGORY_15_EVENT_MAPPING_NOT_IMPLEMENTED')
    if categories - {1, 15}:
        differences.append('EXACT_TDX_OTHER_CATEGORY_REQUIRES_SEMANTIC_MAPPING')
        missing_capabilities.append('OTHER_TDX_CATEGORY_MAPPING_NOT_IMPLEMENTED')
    if not exact:
        differences.append('NEARBY_TDX_RECORD_WITH_UNEXPLAINED_FACTOR_ROW' if nearby
                           else 'NO_TARGET_RECORD_IN_BOUNDED_PACKAGE')
        missing_capabilities.extend(['NON_EVENT_FACTOR_INTERPRETATION_UNPROVEN',
                                     'BOUNDED_SOURCE_COMPLETENESS_UNPROVEN'])
    suggestions = _announcement_suggestions(categories or near_categories, exact, nearby)
    return {'symbol': symbol, 'effective_date': day, 'status': 'UNKNOWN',
        'original_reason': request['reason'], 'original_request': request,
        'original_receipt_record_sha256': _content_hash(record),
        'difference_classifications': differences, 'implementation_capability_gaps': missing_capabilities,
        'exact_record_present': bool(exact), 'exact_categories': sorted(categories),
        'nearby_categories': sorted(near_categories), 'nearby_calendar_window': nearby_window,
        'gbbq_exact_rows': exact, 'gbbq_nearby_rows': nearby,
        'suggested_evidence': suggestions,
        'record_presence_implies_qualification': False,
        'price_event_model_status': 'UNKNOWN', 'share_ratio': None, 'tax_rule': 'UNKNOWN',
        'share_credit_date': None, 'tradable_date': None, 'account_terms_status': 'UNKNOWN',
        'verified_non_event': False, 'global_no_other_event_claim': False,
        'historical_available_at_verified': False, 'independent_confirmation_eligible': False}


def classify_universe_action_evidence_v1(*, queue, receipt, price_gaps, gbbq_window,
        actions_manifest, gbbq_read_audit, output_dir, expected_sha256):
    """所有输入显式绑定；只写新目录，旧队列与旧回执始终保持原字节。"""
    guard = ResearchDataAccessGuard()
    guard.check_range(WINDOW_START, WINDOW_END, 'approved action classification window')
    values = locals()
    paths = {role: _path(values[role]) for role in INPUT_ROLES}
    if set(expected_sha256) != set(INPUT_ROLES) or len(set(paths.values())) != len(INPUT_ROLES):
        raise ValueError('ACTION_CLASSIFICATION_EXACT_INPUT_BINDINGS_REQUIRED')
    out = Path(output_dir).absolute()
    if out.resolve() != out or out.exists():
        raise ValueError('ACTION_CLASSIFICATION_NEW_OUTPUT_REQUIRED')
    manifest = json.loads(_read_bound(paths['actions_manifest'], expected_sha256['actions_manifest'],
                                      'actions_manifest'))
    audit = json.loads(_read_bound(paths['gbbq_read_audit'], expected_sha256['gbbq_read_audit'],
                                   'gbbq_read_audit'))
    start, end = _validate_package(manifest, audit, expected_sha256['gbbq_window'],
                                  paths['gbbq_window'], paths['actions_manifest'])
    loaded = {role: json.loads(_read_bound(paths[role], expected_sha256[role], role))
              for role in ('queue', 'receipt', 'price_gaps')}
    targets, unknown = _validate_targets(loaded['queue'], loaded['receipt'], loaded['price_gaps'],
                                         start, end)
    rows, row_count = _rows(_read_bound(paths['gbbq_window'], expected_sha256['gbbq_window'],
                                       'gbbq_window'), start, end, audit['window_records'])
    classifications = [_classify(request, unknown[key], rows.get(key[0], []), start, end)
                       for key, request in targets.items()]
    counts = Counter(kind for item in classifications for kind in item['difference_classifications'])
    source_files = [Path(__file__).resolve(), PROJECT_ROOT / 'src/chanlun_trader/research/guard.py',
                    PROJECT_ROOT / 'src/chanlun_trader/presentation.py']
    bindings = {role: {'path': str(paths[role]), 'sha256': expected_sha256[role]}
                for role in INPUT_ROLES}
    summary = {'target_count': len(classifications), 'unknown_count': len(classifications),
        'exact_record_target_count': sum(item['exact_record_present'] for item in classifications),
        'exact_category_target_counts': {str(category): sum(category in item['exact_categories']
             for item in classifications) for category in sorted({category for item in classifications
                                                                 for category in item['exact_categories']})},
        'classification_counts': dict(sorted(counts.items())), 'package_row_count': row_count,
        'original_audit_window_record_count': audit['window_records'],
        'resolved_count': 0, 'original_queue_modified': False, 'original_receipt_modified': False,
        'events_materialized': False, 'account_executed': False, 'automatic_execution': False}
    result = {'version': VERSION, 'inputs': bindings,
        'source_identity': {'files': {path.relative_to(PROJECT_ROOT).as_posix(): _hash(path.read_bytes())
                                     for path in source_files}},
        'package_identity': {'dataset_id': manifest['dataset_id'],
            'source_sha256': manifest['source_identity'], 'physical_start': start, 'physical_end': end,
            'coverage_complete': manifest.get('coverage', {}).get('complete'),
            'accounting_supported': manifest.get('coverage', {}).get('accounting_supported'),
            'window_sha256': expected_sha256['gbbq_window']},
        'summary': summary, 'classifications': classifications,
        'original_unknown_statuses_preserved': True, 'global_no_other_event_claim': False}
    followup = []
    for item in classifications:
        request = item['original_request']
        followup.append({**request, 'status': 'NOT_EXECUTED', 'classification_status': 'UNKNOWN',
            'event_categories': ['TDX_CATEGORY_' + str(category) for category in
                                 item['exact_categories'] or item['nearby_categories']],
            'exact_record_present': item['exact_record_present'],
            'difference_classifications': item['difference_classifications'],
            'implementation_capability_gaps': item['implementation_capability_gaps'],
            **item['suggested_evidence'], 'classification_record_sha256': _content_hash(item),
            'original_request_sha256': _content_hash(request),
            'gbbq_related_line_numbers': [row['line_number'] for row in
                                        item['gbbq_exact_rows'] + item['gbbq_nearby_rows']],
            'record_presence_implies_qualification': False})
    evidence_queue = {'version': QUEUE_VERSION, 'inputs': bindings,
        'classification_sha256': _content_hash(result), 'request_count': len(followup),
        'requests': followup, 'original_unknown_statuses_preserved': True,
        'total_selected_original_document_limit': loaded['queue']['total_selected_original_document_limit'],
        'unbounded_search_authorized': False, 'automatic_execution': False, 'automatic_retry': False}
    # 写出前复验所有已绑定文件，拒绝读取过程中发生的身份漂移。
    for role in INPUT_ROLES:
        _read_bound(paths[role], expected_sha256[role], role)
    out.mkdir(parents=True, exist_ok=False)
    for name, value in [('ACTION_DATE_EVIDENCE_CLASSIFICATION.json', result),
                        ('TARGETED_ACTION_DATE_ANNOUNCEMENT_QUEUE.json', evidence_queue)]:
        with (out / name).open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                                    allow_nan=False) + '\n')
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description='分类有限窗口公司行动日期证据并保留未知裁决')
    parser.add_argument('--input-bindings', required=True, help='六个明确输入的路径及 SHA-256 绑定文件')
    parser.add_argument('--output-dir', required=True, help='尚不存在的输出目录')
    parser.add_argument('--json', action='store_true', help='输出 machine JSON 摘要')
    args = parser.parse_args(argv)
    value = json.loads(_path(args.input_bindings).read_text(encoding='utf-8-sig'))
    if value.get('version') != BINDINGS_VERSION or set(value.get('inputs', {})) != set(INPUT_ROLES):
        raise ValueError('ACTION_CLASSIFICATION_INPUT_BINDINGS_VERSION_INVALID')
    result = classify_universe_action_evidence_v1(
        **{role: value['inputs'][role]['path'] for role in INPUT_ROLES}, output_dir=args.output_dir,
        expected_sha256={role: value['inputs'][role]['sha256'] for role in INPUT_ROLES})
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print('日期证据分类已完成。')
        print(f"目标 {result['target_count']} 项，保留未知 {result['unknown_count']} 项；"
              f"同日记录 {result['exact_record_target_count']} 项。")
        print('裁决状态：' + ZhCNPresentation.status_name('UNKNOWN', include_code=True))
    return result


if __name__ == '__main__':
    main()
