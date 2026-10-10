"""只读数据资格投影；既不授予策略资格，也不另建暴露或预算账本。"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from .common import stable_hash


def _day(value: Any) -> str:
    text = str(value).replace('-', '')
    if not re.fullmatch(r'\d{8}', text):
        raise ValueError('DATA_QUALIFICATION_DATE_INVALID')
    return datetime.strptime(text, '%Y%m%d').strftime('%Y-%m-%d')


def _window(value: dict) -> tuple[str, str] | None:
    try:
        start = _day(value.get('start', value.get('feature_start', value.get('account_start'))))
        end = _day(value.get('end', value.get('account_end')))
    except (ValueError, TypeError, AttributeError):
        return None
    return (start, end) if start <= end else None


def _symbols(value: dict) -> set[str]:
    window = value.get('window')
    symbols = value.get('symbols', window.get('symbols', []) if isinstance(window, dict) else [])
    return {str(symbol).upper() for symbol in symbols} if isinstance(symbols, list) else set()


def _known(value: Any) -> bool:
    return bool(value) and str(value).upper() not in ('UNKNOWN', 'UNVERIFIED', 'NONE')


def _matches(manifest: dict, exposure: dict) -> list[str]:
    reasons = [key.upper() + '_MATCH' for key in ('content_hash', 'input_identity')
               if _known(manifest.get(key)) and manifest.get(key) == exposure.get(key)]
    left, right = _window(manifest.get('window', {})), _window(exposure.get('window', {}))
    if left and right and _symbols(manifest) & _symbols(exposure):
        if max(left[0], right[0]) <= min(left[1], right[1]):
            reasons.append('RELATED_SECURITY_WINDOW_EXPOSED')
    return reasons


def audit_data_qualification(manifests: list[dict], exposure_records: list[dict]) -> dict:
    """审计显式元数据，不读取价格。未匹配到暴露不等于已证明独立。

    metadata_*_ready 只表示字段声明齐备，绝不能替代数据加载器验证。
    调用方必须保留原供应商/账户/快照校验，不能用本报告放行正式评审。
    """
    ids = [row.get('dataset_id') for row in manifests]
    if any(not isinstance(value, str) or not value for value in ids) or len(set(ids)) != len(ids):
        raise ValueError('DATA_QUALIFICATION_DATASET_ID_INVALID')
    rows = []
    for original in manifests:
        manifest = deepcopy(original)
        reasons = []
        for key in ('source', 'captured_at', 'fields', 'units', 'adjustment',
                    'state_basis', 'corporate_action_basis', 'availability_basis'):
            if not _known(manifest.get(key)):
                reasons.append('MISSING_' + key.upper())
        if not _symbols(manifest):
            reasons.append('MISSING_SYMBOLS')
        if not _window(manifest.get('window', {})):
            reasons.append('INVALID_OR_MISSING_WINDOW')
        fields, units = manifest.get('fields', []), manifest.get('units', {})
        if not isinstance(fields, list) or not all(isinstance(field, str) for field in fields):
            reasons.append('INVALID_FIELDS')
        elif not isinstance(units, dict) or any(not _known(units.get(field)) for field in fields):
            reasons.append('FIELD_UNITS_INCOMPLETE')
        if manifest.get('captured_at'):
            try:
                stamp = datetime.fromisoformat(manifest['captured_at'])
                if stamp.tzinfo is None:
                    raise ValueError('timezone required')
            except (TypeError, ValueError):
                reasons.append('CAPTURE_TIME_UNVERIFIED')
        matched = []
        for index, exposure in enumerate(exposure_records):
            matches = _matches(manifest, exposure)
            if matches:
                matched.append({'exposure_index': index, 'reasons': matches,
                                'evidence_ref': deepcopy(exposure.get('evidence_ref')),
                                'purpose': exposure.get('purpose', 'UNKNOWN')})
        metadata_complete = not reasons
        research_ready = not any(reason in reasons for reason in (
            'MISSING_SOURCE', 'MISSING_FIELDS', 'INVALID_FIELDS', 'MISSING_UNITS',
            'FIELD_UNITS_INCOMPLETE', 'MISSING_ADJUSTMENT', 'MISSING_SYMBOLS',
            'INVALID_OR_MISSING_WINDOW'))
        # 声明完整仍不是数据真实性认证；这里只是可供下一层核验的候选。
        rows.append({'dataset_id': manifest['dataset_id'], 'metadata': manifest,
                     'metadata_hash': stable_hash(manifest), 'metadata_complete': metadata_complete,
                     'metadata_research_ready': research_ready,
                     'metadata_account_ready': metadata_complete,
                     'historical_independence': 'EXPOSED' if matched else 'UNKNOWN',
                     'independent_confirmation_eligible': False, 'source_authenticated': False,
                     'reasons': reasons + (['KNOWN_OR_POSSIBLE_EXPOSURE'] if matched else
                                          ['INDEPENDENCE_NOT_ESTABLISHED']),
                     'exposure_matches': matched})
    report = {'schema_version': 'RESEARCH_DATA_QUALIFICATION_V1',
              'authority': 'READ_ONLY_METADATA_PROJECTION', 'datasets': rows,
              'exposure_records_hash': stable_hash(exposure_records),
              'independent_confirmation_eligible': False,
              'limitations': ['METADATA_NOT_SOURCE_AUTHENTICATION',
                              'ABSENCE_OF_EXPOSURE_RECORD_IS_NOT_INDEPENDENCE',
                              'NO_MARKET_VALUES_READ_NO_BUDGET_MUTATION']}
    return {**report, 'report_hash': stable_hash(report)}


def read_governance_exposures(root: str | Path) -> list[dict]:
    """读取现有 StrategyBatchGovernanceV1 回执/START，绝不读 RESULT。

    START 在计算前落盘，因此已开始但中断也属于可能暴露；未开始的批准也
    保守列入审计，不能证明从未通过其他途径读过样本。预算原文件只作引用。
    """
    root = Path(root).absolute()
    if root.resolve() != root or not root.is_dir():
        raise ValueError('DATA_QUALIFICATION_ROOT_INVALID')
    records = []
    for path in sorted(root.rglob('CONFIRMATION.json')):
        if path.resolve() != path:
            raise ValueError('DATA_QUALIFICATION_REDIRECTED')
        raw = path.read_bytes()
        receipt = json.loads(raw)
        if 'strategy_plans' not in receipt:
            continue
        if receipt.get('receipt_id') != stable_hash({k: v for k, v in receipt.items() if k != 'receipt_id'}):
            raise ValueError('DATA_QUALIFICATION_RECEIPT_HASH_CONFLICT')
        for name, plan in receipt['strategy_plans'].items():
            if not re.fullmatch(r'[A-Za-z0-9_-]+', name):
                raise ValueError('DATA_QUALIFICATION_PLAN_NAME_INVALID')
            if plan.get('plan_id') != stable_hash({k: v for k, v in plan.items() if k != 'plan_id'}):
                raise ValueError('DATA_QUALIFICATION_PLAN_HASH_CONFLICT')
            start_path = path.parent / (name + '_START.json')
            start_hash = None
            if start_path.exists():
                if start_path.resolve() != start_path:
                    raise ValueError('DATA_QUALIFICATION_REDIRECTED')
                start_raw = start_path.read_bytes()
                start = json.loads(start_raw)
                if start.get('receipt_id') != receipt['receipt_id'] or start.get('kind') != name:
                    raise ValueError('DATA_QUALIFICATION_START_BINDING_CONFLICT')
                start_hash = hashlib.sha256(start_raw).hexdigest()
            window = plan.get('backend', {}).get('window', {})
            records.append({'input_identity': receipt.get('input_identity'),
                            'symbols': window.get('symbols', []), 'window': window,
                            'purpose': 'ACCOUNT_STARTED' if start_hash else 'ACCOUNT_AUTHORIZED_POSSIBLE_EXPOSURE',
                            'evidence_ref': {'receipt_path': str(path),
                                'receipt_sha256': hashlib.sha256(raw).hexdigest(),
                                'receipt_id': receipt['receipt_id'], 'plan_id': plan.get('plan_id'),
                                'start_path': str(start_path) if start_hash else None,
                                'start_sha256': start_hash, 'budget_path': receipt.get('budget_path'),
                                'objective_id': receipt.get('objective_id')}})
    return records


def inventory_metadata(roots: list[str | Path]) -> dict:
    """仅列直接子项属性，不读文件内容；缺失目录和访问失败明确保留。"""
    inventories = []
    for value in roots:
        path = Path(value).absolute()
        entry = {'root': str(path), 'entries': [], 'status': 'AVAILABLE'}
        try:
            if path.resolve() != path:
                raise ValueError('REDIRECTED_ROOT')
            for item in sorted(path.iterdir()):
                if item.resolve() != item:
                    entry['entries'].append({'name': item.name, 'status': 'REDIRECTED_NOT_OPENED'})
                    continue
                stat = item.stat()
                entry['entries'].append({'name': item.name, 'is_directory': item.is_dir(),
                                         'size_bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns})
        except (OSError, ValueError) as exc:
            entry.update(status='UNAVAILABLE', error_type=type(exc).__name__)
        inventories.append(entry)
    return {'schema_version': 'DATA_DIRECTORY_METADATA_V1', 'roots': inventories,
            'content_read': False, 'data_qualification': 'UNKNOWN'}


def continuous_data_dependencies_v1(manifest: dict, *, account_sessions=None) -> dict:
    """列出长度与配套依赖；元数据/日数不授予更早账户或独立验证资格。

    account_sessions 仅供可信投影程序填入子日历的实际账户日数。未知日数不
    用行情范围或工作日估算补造；独立观察须由独立协议另行认证。
    """
    if account_sessions is not None and (type(account_sessions) is not int or account_sessions < 0):
        raise ValueError('DATA_DEPENDENCY_ACCOUNT_SESSIONS_INVALID')
    files = manifest.get('files', {})
    if not isinstance(files, dict):
        raise ValueError('DATA_DEPENDENCY_MANIFEST_INVALID')
    categories = []
    for kind in ('DAILY', 'STATES', 'REFERENCE_PRICES', 'EVENTS', 'CALENDAR',
                 'CORPORATE_ACTION_COVERAGE', 'SOURCE_QUALIFICATION'):
        items = [row for row in files.values() if row.get('kind') == kind]
        categories.append({'kind': kind, 'source_count': len(items),
            'declared_ranges': [{'start': row.get('start'), 'end': row.get('end'),
                                 'source_id': row.get('source_id')} for row in items],
            'status': 'REQUIRES_CONTENT_QUALIFICATION' if items else 'MISSING'})
    missing = [row['kind'] for row in categories if row['status'] == 'MISSING']
    return {'schema_version': 'CONTINUOUS_DATA_DEPENDENCIES_V1',
        'exploration_504': {'required_account_sessions': 504,
            'observed_calendar_account_sessions': account_sessions,
            'missing_account_sessions': max(0, 504 - account_sessions) if account_sessions is not None else None,
            'status': ('SESSION_COUNT_UNKNOWN' if account_sessions is None else
                       'ACCOUNT_SESSIONS_INSUFFICIENT' if account_sessions < 504 else
                       'REQUIRES_ACCOUNT_AND_WARMUP_QUALIFICATION'),
            'earlier_data_requires': ['PRICES', 'STATES', 'REFERENCE_PRICES', 'EVENTS',
                'CALENDAR', 'CORPORATE_ACTION_COVERAGE', 'HISTORICAL_UNIVERSE_SCOPE', 'RULE_WARMUP'],
            'historical_universe_scope': deepcopy(manifest.get('universe_scope')),
            'ready': False},
        'independent_252': {'required_observed_sessions': 252, 'observed_sessions': None,
            'status': 'AUTHENTICATED_INDEPENDENT_OBSERVATIONS_NOT_ESTABLISHED', 'ready': False,
            'requires': ['FROZEN_FAMILY_AND_SELECTION', 'TRUSTED_INDEPENDENT_PROTOCOL',
                'EXPLICIT_DATA_AUTHORIZATION', 'REAL_OBSERVED_PUBLICATION_AND_ACCESS_EVIDENCE',
                '252_ACTUAL_ACCOUNT_SESSIONS', 'NO_DESIGN_EXPOSURE']},
        'categories': categories, 'missing_categories': missing,
        'account_data_ready': False, 'independent_confirmation_eligible': False,
        'limitations': ['PHYSICAL_TRAIN_PROJECTION_DOES_NOT_CREATE_INDEPENDENCE',
                       'PRICE_ONLY_EXTENSION_DOES_NOT_EXTEND_ACCOUNT_QUALIFICATION']}
