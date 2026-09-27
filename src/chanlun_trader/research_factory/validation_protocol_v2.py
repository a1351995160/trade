"""独立验证准备协议；冻结用途和来源引用，不创造资格或新的确认额度。"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re

import pandas as pd

from .bounded_research_v1 import _put, _read
from .common import stable_hash
from .mutation_boundary import ObjectiveMutationLock


VERSION = 'VALIDATION_PROTOCOL_V2'
ROUTES = ('FUTURE_OBSERVED', 'SEALED_HISTORICAL')


def _root(value):
    path = Path(value).absolute()
    if path.resolve() != path or '..' in path.parts:
        raise ValueError('VALIDATION_PROTOCOL_PATH_REDIRECTED')
    return path


def _date(value):
    if type(value) is not int or len(str(value)) != 8:
        raise ValueError('VALIDATION_PROTOCOL_DATE_INVALID')
    stamp = pd.Timestamp(str(value))
    if stamp.strftime('%Y%m%d') != str(value):
        raise ValueError('VALIDATION_PROTOCOL_DATE_INVALID')
    return value


def validation_requirements(route):
    if route not in ROUTES:
        raise ValueError('VALIDATION_PROTOCOL_ROUTE_INVALID')
    return {
        'warmup_sessions': 60, 'account_sessions': 504,
        'close_snapshots_required': 564, 'open_snapshots_required': 504,
        'required_fields': ['RAW_OHLCV_AMOUNT', 'VENDOR_TURNOVER', 'SECURITY_STATE',
                            'EXCHANGE_CALENDAR', 'CORPORATE_ACTION_COVERAGE'],
        'historical_warmup_permitted': False,
        'execution': 'SIMULATED_OBSERVED_OPEN_WITH_COSTS',
        'method_support_required': True,
        'missing_stage_rule': 'STOP_NO_RETROSPECTIVE_FILL',
        'corporate_action_rule': 'REQUIRE_SUPPORTED_COMPLETE_EVENT_TERMS',
        'route_executable': route == 'FUTURE_OBSERVED',
        'independence': ('CAPTURE_AFTER_CANDIDATE_AND_PROTOCOL_FREEZE' if route == 'FUTURE_OBSERVED'
                         else 'SEALED_ACCESS_AND_METHOD_SUPPORT_REQUIRED'),
    }


def _archives(archive_root, strategy_ids):
    from .strategy_qualification_v1 import BoundedStrategyArchiveV1
    if (not isinstance(strategy_ids, list) or not strategy_ids or len(strategy_ids) > 5
            or len(strategy_ids) != len(set(strategy_ids))):
        raise ValueError('VALIDATION_PROTOCOL_FAMILY_INVALID')
    store = BoundedStrategyArchiveV1(archive_root)
    records = [store.load(key) for key in sorted(strategy_ids)]
    roots = {a['origin']['source_root'] for a in records}
    scopes = {a['origin']['scope_id'] for a in records}
    if len(roots) != 1 or len(scopes) != 1:
        raise ValueError('VALIDATION_PROTOCOL_ONE_FAMILY_REQUIRED')
    origin = _root(next(iter(roots)))
    scope = records[0]['evidence']['session']
    candidates = [f'CANDIDATE_{i:03d}' for i in range(1, scope['max_attempts'] + 1)]
    if not all((origin / key / 'DECISION.json').is_file() for key in candidates):
        raise ValueError('VALIDATION_PROTOCOL_FAMILY_NOT_CLOSED')
    available = {key for key in candidates if (origin / key / 'RESULT.json').is_file()}
    if available != {a['origin']['candidate_id'] for a in records}:
        raise ValueError('VALIDATION_PROTOCOL_FAMILY_MEMBER_OMITTED')
    return origin, next(iter(scopes)), records


def preview_protocol(*, archive_root, strategy_ids, symbols, not_before,
                     route='FUTURE_OBSERVED', metadata_report=None):
    """只读准备；元数据报告是诊断附件，绝不替代采集和正式证据认证。"""
    requirements = validation_requirements(route)
    origin, scope, archives = _archives(archive_root, strategy_ids)
    if (not isinstance(symbols, list) or not symbols or len(set(symbols)) != len(symbols)
            or any(not isinstance(s, str) or not re.fullmatch(r'(00\d{4}\.SZ|60\d{4}\.SH)', s)
                   for s in symbols)):
        raise ValueError('VALIDATION_PROTOCOL_SYMBOLS_INVALID')
    _date(not_before)
    reasons = ['INDEPENDENT_CAPTURE_REQUIRED', 'METHOD_APPLICABILITY_REQUIRED']
    if route == 'SEALED_HISTORICAL':
        reasons = ['SEALED_HISTORY_AUTHORITY_NOT_ESTABLISHED', 'SEALED_HISTORY_METHOD_NOT_SUPPORTED']
    if len(symbols) != 2:
        reasons.append('CURRENT_FORMAL_ACCOUNT_SCOPE_REQUIRES_TWO_SYMBOLS')
    metadata_ref = None
    if metadata_report is not None:
        if (not isinstance(metadata_report, dict)
                or metadata_report.get('authority') != 'READ_ONLY_METADATA_PROJECTION'
                or not isinstance(metadata_report.get('datasets'), list)):
            raise ValueError('VALIDATION_PROTOCOL_METADATA_PROJECTION_REQUIRED')
        # 调用方能够重算摘要，所以这只绑定附件，而不是授予独立资格。
        metadata_ref = stable_hash(metadata_report)
        if any(d.get('historical_independence') == 'EXPOSED' for d in metadata_report['datasets']):
            reasons.append('REFERENCED_HISTORY_ALREADY_EXPOSED')
    payload = {
        'version': VERSION, 'archive_root': str(_root(archive_root)),
        'source_root': str(origin), 'scope_id': scope, 'route': route,
        'symbols': sorted(symbols), 'not_before': not_before,
        'family': {a['strategy_id']: {key: a[key] for key in ('archive_hash', 'rule_identity')}
                   for a in archives},
        'requirements': requirements, 'metadata_report_hash': metadata_ref,
        'reason_codes': reasons, 'strategy_qualified': False,
        'confirmation_budget_consumed': 0,
        'status': 'PREPARATION_ONLY' if route == 'FUTURE_OBSERVED' else 'UNSUPPORTED_ROUTE',
    }
    return {**payload, 'preview_hash': stable_hash(payload)}


def freeze_protocol(**kwargs):
    preview = preview_protocol(**kwargs)
    origin = _root(preview['source_root'])
    path = origin / 'VALIDATION_PROTOCOL_V2.json'
    with ObjectiveMutationLock.for_resource(path):
        if path.exists():
            existing = load_protocol(path)
            if existing['preview'] != preview:
                raise ValueError('VALIDATION_PROTOCOL_ALREADY_FROZEN')
            return existing
        now = datetime.now(timezone.utc)
        if preview['not_before'] <= int(pd.Timestamp(now).tz_convert('Asia/Shanghai').strftime('%Y%m%d')):
            raise ValueError('VALIDATION_PROTOCOL_FUTURE_START_REQUIRED')
        payload = {'preview': preview, 'frozen_at': now.isoformat()}
        payload['protocol_id'] = 'VP_' + stable_hash(payload)
        _put(path, payload)
        return payload


def load_protocol(path):
    path = _root(path)
    value = _read(path)
    preview = value['preview']
    if (value['protocol_id'] != 'VP_' + stable_hash({k: v for k, v in value.items() if k != 'protocol_id'})
            or preview['preview_hash'] != stable_hash({k: v for k, v in preview.items() if k != 'preview_hash'})
            or preview['version'] != VERSION
            or preview['requirements'] != validation_requirements(preview['route'])
            or path != _root(preview['source_root']) / 'VALIDATION_PROTOCOL_V2.json'):
        raise ValueError('VALIDATION_PROTOCOL_IDENTITY_CONFLICT')
    frozen = pd.Timestamp(value['frozen_at'])
    if frozen.tzinfo is None or preview['not_before'] <= int(frozen.tz_convert('Asia/Shanghai').strftime('%Y%m%d')):
        raise ValueError('VALIDATION_PROTOCOL_FREEZE_TIME_INVALID')
    origin, scope, archives = _archives(preview['archive_root'], list(preview['family']))
    current = {a['strategy_id']: {key: a[key] for key in ('archive_hash', 'rule_identity')} for a in archives}
    if str(origin) != preview['source_root'] or scope != preview['scope_id'] or current != preview['family']:
        raise ValueError('VALIDATION_PROTOCOL_ARCHIVE_CHANGED')
    return value


def bind_formal_protocol(path, *, archive_root, strategy_ids, symbols, not_before):
    """正式服务仍独立检查档案、方法和预算；本绑定不能代替任何准入检查。"""
    value = load_protocol(path)
    p = value['preview']
    if (p['route'] != 'FUTURE_OBSERVED' or p['archive_root'] != str(_root(archive_root))
            or set(p['family']) != set(strategy_ids) or p['symbols'] != sorted(symbols)
            or p['not_before'] != not_before):
        raise ValueError('VALIDATION_PROTOCOL_FORMAL_SCOPE_CONFLICT')
    return {'path': str(_root(path)), 'protocol_id': value['protocol_id'],
            'protocol_hash': stable_hash(value)}
