"""用已物理限窗的 TDX 原包逐项核验空现金送转；不把空值直接解释为零。"""
from __future__ import annotations

import argparse
from collections import defaultdict
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / 'src'))

from chanlun_trader.research.guard import ResearchDataAccessGuard
from scripts.prepare_universe_actions_v1 import SHARE_RATE_POLICY, _day, _load, _sha, _write
from scripts.resolve_universe_action_dates_v1 import _action_source_readonly, _registered_catalog

VERSION = 'UNIVERSE_ACTION_NUMERIC_TERMS_RESOLUTION_V1'
BINDING_VERSION = 'UNIVERSE_TDX_WINDOW_INPUT_BINDING_V1'
VERIFIED = 'VERIFIED_ZERO_CASH_PURE_SHARE_ACTION'
NUMERIC_GAP = 'ACTION_NUMERIC_TERM_UNKNOWN'
# JSON 保留的十股数来自 float32。仅比较两个明确大于零的股份比例，
# 2e-7 相对容差覆盖 float32 表示误差；现金/配股仍要求精确为零。
POSITIVE_SHARE_REL_TOL = 2e-7


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def numeric_row_identity(symbol, row, source):
    return symbol, _day(row['dividOperateDate']), source['sha256'], _hash(row)


def _binding(path):
    physical = _physical_path(path)
    return {'path': str(physical), 'sha256': _sha(physical)}


def _physical_path(path):
    physical = Path(path).absolute()
    if physical.resolve() != physical or not physical.is_file():
        raise ValueError('ACTION_NUMERIC_INPUT_PATH_INVALID')
    return physical


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)) or value == '':
        raise ValueError('ACTION_NUMERIC_EVIDENCE_NUMBER_INVALID')
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError('ACTION_NUMERIC_EVIDENCE_NUMBER_INVALID')
    return number


def _positive_equal(a, b):
    return a > 0 and b > 0 and math.isclose(a, b, rel_tol=POSITIVE_SHARE_REL_TOL, abs_tol=0)


def _tdx_security(row, symbol):
    if type(row.get('market')) is not int or row['market'] not in (0, 1):
        raise ValueError('ACTION_NUMERIC_TDX_SECURITY_IDENTITY_CONFLICT')
    exchange = {0: 'SZ', 1: 'SH'}[row['market']]
    if symbol != str(row.get('code')) + '.' + exchange or row.get('symbol') != symbol:
        raise ValueError('ACTION_NUMERIC_TDX_SECURITY_IDENTITY_CONFLICT')


def _capital_status_rows(rows, symbol, share_rate):
    """GBBQ 的 5/9 四字段为前后股本数量，不是每十股现金/配股条款。"""
    # 伴随股本数量变化与每股比率并非同一条款；这里不推断原因，
    # 不扩大 category1 的比率容差，也不把数量字段误解释为额外权益条款。
    # 扩缩股/未知类别不在这份价格补证的可解释范围内。
    if len(rows) > 1:
        raise ValueError('ACTION_NUMERIC_TDX_CAPITAL_STATUS_DUPLICATE_OR_CONFLICT')
    result = []
    for row in rows:
        if type(row.get('category')) is not int or row['category'] not in (5, 9):
            raise ValueError('ACTION_NUMERIC_TDX_COMPOUND_OR_UNKNOWN_CATEGORY')
        _tdx_security(row, symbol)
        before_float, before_total, after_float, after_total = [_number(row[key]) for key in (
            'hongli_panqianliutong', 'peigujia_qianzongguben',
            'songgu_qianzongguben', 'peigu_houzongguben')]
        if (before_total <= 0 or after_total <= 0
                or before_float > before_total or after_float > after_total):
            raise ValueError('ACTION_NUMERIC_TDX_CAPITAL_STATUS_COUNTS_INVALID')
        result.append({'row': deepcopy(row), 'interpretation': 'BEFORE_AFTER_SHARE_CAPITAL_COUNTS',
            'before_float': before_float, 'before_total': before_total,
            'after_float': after_float, 'after_total': after_total,
            'per_old_share_entitlement_terms': False,
            'capital_change_cause_verified': False, 'share_capital_state_qualified': False,
            'ratio_diagnostic': {'declared_entitlement_ratio': 1 + share_rate,
                'total_capital_ratio': after_total / before_total,
                'total_capital_ratio_difference': after_total / before_total - (1 + share_rate),
                'float_capital_ratio': after_float / before_float if before_float > 0 else None,
                'float_capital_ratio_difference': after_float / before_float - (1 + share_rate)
                    if before_float > 0 else None,
                'same_capital_change_cause_claimed': False}})
    return result


def _codes():
    return {name: _binding(PROJECT_ROOT / name) for name in (
        'scripts/resolve_universe_numeric_terms_v1.py', 'scripts/resolve_universe_action_dates_v1.py',
        'scripts/prepare_universe_actions_v1.py', 'scripts/prepare_universe_actions_v2.py')}


def _tdx_package(binding_path):
    """先核验外部冻结哈希及物理窗声明，再读安全包，不沿引用读原 gbbq。"""
    anchor = _load(binding_path)
    if anchor.get('version') != BINDING_VERSION:
        raise ValueError('ACTION_NUMERIC_TDX_EXTERNAL_BINDING_REQUIRED')
    start, end = _day(anchor.get('start')), _day(anchor.get('end'))
    if start is None or end is None or start > end:
        raise ValueError('ACTION_NUMERIC_TDX_WINDOW_INVALID')
    ResearchDataAccessGuard().check_range(start, end, 'numeric terms TDX frozen physical window')
    # 仅这些角色可作为数值证据。独立 SHA 锚须在调用前已冻结。
    names = {'tdx_manifest': 'actions_manifest.json', 'tdx_window': 'gbbq_window.jsonl',
             'tdx_read_audit': 'GBBQ_READ_AUDIT.json'}
    if set(anchor.get('inputs', {})) != set(names):
        raise ValueError('ACTION_NUMERIC_TDX_BOUND_FILE_ROLES_REQUIRED')
    paths = {}
    for role, basename in names.items():
        expected = anchor['inputs'][role]
        path = _physical_path(expected['path'])
        if path.name != basename or str(path) != expected['path']:
            raise ValueError('ACTION_NUMERIC_TDX_BOUND_SOURCE_CHANGED:' + role)
        paths[role] = path
    if paths['tdx_manifest'].parent != paths['tdx_window'].parent:
        raise ValueError('ACTION_NUMERIC_TDX_PACKAGE_DIRECTORY_CONFLICT')
    for role in ('tdx_manifest', 'tdx_read_audit'):
        if _binding(paths[role]) != anchor['inputs'][role]:
            raise ValueError('ACTION_NUMERIC_TDX_BOUND_SOURCE_CHANGED:' + role)
    manifest, audit = _load(paths['tdx_manifest']), _load(paths['tdx_read_audit'])
    attest = manifest.get('physical_window_attestation', {})
    if (manifest.get('version') != 'WindowedCorporateActionDatasetV1'
            or (manifest.get('start'), manifest.get('end')) != (start, end)
            or (attest.get('start'), attest.get('end')) != (start, end)
            or attest.get('window_enforced_before_export') is not True
            or attest.get('not_derived_from_current_incident') is not True
            or not attest.get('owner') or not attest.get('authorization_sha256')
            or manifest.get('events_file') != 'events.jsonl'
            or audit.get('output_sha256') != anchor['inputs']['tdx_window']['sha256']
            or manifest.get('source_identity') != audit.get('source_sha256')
            or manifest.get('dataset_id') != 'OWNER_GBBQ_' + str(audit.get('source_sha256'))
            or audit.get('parser_version') != 'pytdx.GbbqReader'
            or not audit.get('parser_sha256')):
        raise ValueError('ACTION_NUMERIC_TDX_PHYSICAL_PACKAGE_PROOF_INVALID')
    # SHA 也会读取正文，必须等小元数据的物理限窗声明通过以后才执行。
    if _binding(paths['tdx_window']) != anchor['inputs']['tdx_window']:
        raise ValueError('ACTION_NUMERIC_TDX_BOUND_SOURCE_CHANGED:tdx_window')
    # events 是外部冻结 manifest 绑定的同目录唯一文件，不能沿任意引用扩读。
    paths['events'] = paths['tdx_manifest'].parent / 'events.jsonl'
    if _binding(paths['events'])['sha256'] != manifest.get('events_sha256'):
        raise ValueError('ACTION_NUMERIC_TDX_BOUND_SOURCE_CHANGED:events')
    rows, events = defaultdict(list), defaultdict(list)
    for role, date_field, groups in [('tdx_window', 'datetime', rows),
                                     ('events', 'effective_date', events)]:
        with paths[role].open(encoding='utf-8') as stream:
            for line in stream:
                row = json.loads(line)
                day = _day(row[date_field])
                if day is None or not start <= day <= end:
                    raise ValueError('ACTION_NUMERIC_TDX_ROW_OUTSIDE_PHYSICAL_WINDOW')
                ResearchDataAccessGuard().check_date(day, 'numeric terms TDX physical row')
                if row['symbol'] not in manifest['coverage']['symbols']:
                    raise ValueError('ACTION_NUMERIC_TDX_ROW_UNREGISTERED_SYMBOL')
                groups[row['symbol'], day].append(row)
    return anchor, manifest, audit, rows, events


def _resolve_one(gap, sources, tdx_rows, tdx_events, audit, data_sha256):
    symbol, day = gap['symbol'], gap['effective_date']
    own = [(source, row, binding) for source, rows, binding in sources for row in rows
           if _day(row['dividOperateDate']) == day]
    # 同日多条款不能用一个十股数代替；年度原件重复的完全相同行可保留全部来源。
    if not own or len({_hash(row) for _, row, _ in own}) != 1:
        raise ValueError('ACTION_NUMERIC_ORIGINAL_ROWS_MISSING_OR_CONFLICT')
    row = own[0][1]
    if row['dividCashPsBeforeTax'] != '' or row['dividPayDate'] != '':
        raise ValueError('ACTION_NUMERIC_BLANK_CASH_PURE_SHARES_REQUIRED')
    rates = [_number(0 if row[key] == '' else row[key])
             for key in ('dividStocksPs', 'dividReserveToStockPs')]
    share_rate = sum(rates)
    if share_rate <= 0:
        raise ValueError('ACTION_NUMERIC_POSITIVE_SHARE_RATE_REQUIRED')
    same_day = tdx_rows.get((symbol, day), [])
    raw = [row for row in same_day if type(row.get('category')) is int and row['category'] == 1]
    if len(raw) != 1:
        raise ValueError('ACTION_NUMERIC_TDX_EXACT_SINGLE_CATEGORY_ONE_REQUIRED')
    companions = _capital_status_rows([row for row in same_day if row is not raw[0]], symbol, share_rate)
    raw = raw[0]
    _tdx_security(raw, symbol)
    cash, rights_price, shares, rights = [_number(raw[key]) for key in (
        'hongli_panqianliutong', 'peigujia_qianzongguben',
        'songgu_qianzongguben', 'peigu_houzongguben')]
    if cash != 0 or rights_price != 0 or rights != 0:
        raise ValueError('ACTION_NUMERIC_TDX_NONZERO_CASH_OR_RIGHTS')
    if not _positive_equal(share_rate, shares / 10):
        raise ValueError('ACTION_NUMERIC_TDX_SHARE_RATIO_CONFLICT')
    events = tdx_events.get((symbol, day), [])
    if len(events) != 1 or events[0].get('event_type') != 'BONUS':
        raise ValueError('ACTION_NUMERIC_TDX_EXACT_BONUS_EVENT_REQUIRED')
    event = events[0]
    terms = event.get('terms', {})
    event_id = str(event.get('event_id', ''))
    parts = event_id.split(':')
    if (len(parts) != 3 or parts[0] != data_sha256 or not parts[1].isdigit() or parts[2] != 'BONUS'
            or event.get('source') != 'TDX_GBBQ_OWNER_EXPORT:' + audit['source_sha256']
            or event.get('units') != 'NEW_SHARES_PER_OLD_SHARE'
            or not _positive_equal(_number(terms.get('raw_shares_per_ten')), shares)
            or type(terms.get('ratio_numerator')) is not int
            or type(terms.get('ratio_denominator')) is not int
            or terms['ratio_denominator'] <= 0
            or not _positive_equal(terms['ratio_numerator'] / terms['ratio_denominator'] - 1, share_rate)):
        raise ValueError('ACTION_NUMERIC_TDX_EVENT_SOURCE_OR_TERMS_CONFLICT')
    return {'symbol': symbol, 'effective_date': day, 'original_gap': deepcopy(gap),
        'status': VERIFIED, 'interpretation': 'TDX_EXPLICIT_ZERO_CASH_AND_MATCHED_SHARE_RATE',
        'original_sources': [{'binding': binding, 'original_row': row,
                              'row_identity': list(numeric_row_identity(symbol, row, source))}
                             for source, row, binding in own],
        'tdx_row': raw, 'tdx_event': event, 'tdx_capital_status_rows': companions,
        'optional_share_rate_interpretation_policy': SHARE_RATE_POLICY,
        'cash_per_share': 0,
        'positive_share_comparison': {'baostock_share_rate': share_rate,
            'tdx_share_rate': shares / 10, 'relative_tolerance': POSITIVE_SHARE_REL_TOL,
            'absolute_tolerance': 0, 'zero_fields_use_exact_comparison': True},
        'historical_available_at_verified': False, 'account_terms_qualified': False,
        'share_capital_state_qualified': False,
        'tax_rule': 'UNKNOWN', 'share_credit_date': None,
        'independent_confirmation_eligible': False}


def _resolutions(catalog, gaps, metadata, package):
    anchor, _, audit, tdx_rows, tdx_events = package
    start, end = anchor['start'], anchor['end']
    selected = [g for g in gaps if g.get('reason') == NUMERIC_GAP]
    masters = {r['symbol'] for r in metadata['master']['records']}
    keys = [(g['symbol'], g['effective_date']) for g in selected]
    if len(set(keys)) != len(keys):
        raise ValueError('ACTION_NUMERIC_DUPLICATE_GAP_IDENTITY')
    relevant = set()
    for symbol, day in keys:
        ResearchDataAccessGuard().check_date(day, 'numeric terms selected gap')
        if symbol not in masters or not start <= day <= end:
            raise ValueError('ACTION_NUMERIC_GAP_OUTSIDE_REGISTERED_WINDOW')
        relevant.add(symbol)
    sources = defaultdict(list)
    for source in catalog['sources']:
        if source['symbol'] in relevant and source['kind'] == 'DIVIDEND':
            rows, binding = _action_source_readonly(source)
            if binding.get('request_verified') is not True:
                raise ValueError('ACTION_NUMERIC_REGISTERED_REQUEST_NOT_VERIFIED')
            sources[source['symbol']].append((source, rows, binding))
    result = []
    for gap in selected:
        try:
            row = _resolve_one(gap, sources[gap['symbol']], tdx_rows, tdx_events, audit,
                               anchor['inputs']['tdx_window']['sha256'])
        except (ValueError, KeyError, TypeError) as exc:
            row = {'symbol': gap['symbol'], 'effective_date': gap['effective_date'],
                   'original_gap': deepcopy(gap), 'status': 'UNKNOWN', 'reason': str(exc)}
        result.append(row)
    return result


def resolve_universe_numeric_terms_v1(*, source_catalog, numeric_gaps, manifest, tdx_binding, output_dir):
    paths = {'input_catalog': source_catalog, 'input_gaps': numeric_gaps, 'manifest': manifest,
             'tdx_binding': tdx_binding}
    inputs = {key: _binding(path) for key, path in paths.items()}
    catalog, gaps, metadata = map(_load, (source_catalog, numeric_gaps, manifest))
    _registered_catalog(Path(manifest).absolute(), metadata, source_catalog, 'CORPORATE_ACTION_COVERAGE')
    package = _tdx_package(tdx_binding)
    resolutions = _resolutions(catalog, gaps, metadata, package)
    output = Path(output_dir).absolute()
    if output.resolve() != output or output.exists():
        raise ValueError('ACTION_NUMERIC_NEW_OUTPUT_REQUIRED')
    receipt = {'version': VERSION, **inputs, 'algorithm_sources': _codes(),
        'window': {key: package[0][key] for key in ('start', 'end')}, 'tdx_package': package[0],
        'tdx_parser_recorded_sha256': package[2]['parser_sha256'], 'resolutions': resolutions,
        'account_terms_qualified': False, 'historical_available_at_verified': False}
    output.mkdir(parents=True)
    path = output / 'NUMERIC_TERMS_RESOLUTION_RECEIPT.json'
    _write(path, receipt)
    return {'version': VERSION, 'resolution_count': len(resolutions),
        'verified_count': sum(row['status'] == VERIFIED for row in resolutions),
        'unknown_count': sum(row['status'] != VERIFIED for row in resolutions),
        'receipt_path': str(path), 'receipt_sha256': _sha(path), 'account_terms_qualified': False}


def verify_numeric_terms_resolution_receipt_v1(receipt_path, *, source_catalog, manifest):
    """重读原件和物理限定包重算，不信任收据里单独写下的零或 VERIFIED。"""
    receipt = _load(receipt_path)
    if receipt.get('version') != VERSION or receipt.get('algorithm_sources') != _codes():
        raise ValueError('ACTION_NUMERIC_RESOLUTION_ALGORITHM_SOURCE_CHANGED')
    for key, path in {'input_catalog': source_catalog, 'manifest': manifest}.items():
        if receipt.get(key) != _binding(path):
            raise ValueError('ACTION_NUMERIC_RESOLUTION_INPUT_CHANGED:' + key)
    for key in ('input_gaps', 'tdx_binding'):
        if receipt[key] != _binding(receipt[key]['path']):
            raise ValueError('ACTION_NUMERIC_RESOLUTION_INPUT_CHANGED:' + key)
    catalog, gaps, metadata = map(_load, (source_catalog, receipt['input_gaps']['path'], manifest))
    _registered_catalog(Path(manifest).absolute(), metadata, source_catalog, 'CORPORATE_ACTION_COVERAGE')
    package = _tdx_package(receipt['tdx_binding']['path'])
    if (receipt.get('tdx_package') != package[0]
            or receipt.get('window') != {key: package[0][key] for key in ('start', 'end')}
            or receipt.get('tdx_parser_recorded_sha256') != package[2]['parser_sha256']
            or receipt.get('account_terms_qualified') is not False
            or receipt.get('historical_available_at_verified') is not False):
        raise ValueError('ACTION_NUMERIC_RESOLUTION_PACKAGE_OR_SCOPE_CHANGED')
    resolutions = _resolutions(catalog, gaps, metadata, package)
    if receipt.get('resolutions') != resolutions:
        raise ValueError('ACTION_NUMERIC_RESOLUTION_PREDICATE_NOT_REPRODUCED')
    accepted = {}
    for record in resolutions:
        if record['status'] == VERIFIED:
            for source in record['original_sources']:
                accepted[tuple(source['row_identity'])] = record
    return accepted


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('source-catalog', 'numeric-gaps', 'manifest', 'tdx-binding', 'output-dir'):
        parser.add_argument('--' + key, required=True)
    print(json.dumps(resolve_universe_numeric_terms_v1(**vars(parser.parse_args())),
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
