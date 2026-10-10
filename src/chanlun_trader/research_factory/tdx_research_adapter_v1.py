"""TDX来源规范化；保留原来源、单位证据和派生昨收语义。"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import re
import struct

import numpy as np
import pandas as pd

from ..research.guard import ResearchDataAccessGuard, TrustedResearchDataAccessScopeV1
from .research_universe_v1 import BOARDS, canonical_symbol, identity, scope_board


VERSION = 'TDX_RESEARCH_ADAPTER_V1'
DAILY_FIELDS = ('open', 'high', 'low', 'close', 'prev_close', 'volume', 'amount', 'turn')
_SHA = re.compile(r'[0-9a-f]{64}')


def read_tdx_day_window(path, sessions) -> tuple[list[dict], dict]:
    """窗口外只读日期键；禁用预取，避免日期探针载入封存OHLC字节。"""
    from ..tdx_data import _DAY_STRUCT
    path = Path(path)
    days = list(sessions)
    if not days or days != sorted(set(days)):
        raise ValueError('TDX_DAY_SESSIONS_INVALID')
    guard = ResearchDataAccessGuard()
    guard.check_int_iterable(days, 'TDX physical window')
    before = path.stat()
    if before.st_size % _DAY_STRUCT.size:
        raise ValueError('TDX_RECORD_SIZE_CONFLICT')
    wanted, rows, digest = set(days), [], hashlib.sha256()
    with path.open('rb', buffering=0) as stream:
        for offset in range(0, before.st_size, _DAY_STRUCT.size):
            stream.seek(offset)
            key = stream.read(4)
            if len(key) != 4:
                raise ValueError('TDX_SOURCE_CHANGED_DURING_READ')
            trade_date = struct.unpack('<I', key)[0]
            if trade_date not in wanted:
                continue
            stream.seek(offset)
            raw = stream.read(_DAY_STRUCT.size)
            if len(raw) != _DAY_STRUCT.size:
                raise ValueError('TDX_SOURCE_CHANGED_DURING_READ')
            digest.update(raw)
            values = _DAY_STRUCT.unpack(raw)
            rows.append({'date': values[0], 'open': values[1] / 100, 'high': values[2] / 100,
                'low': values[3] / 100, 'close': values[4] / 100,
                'amount_encoded': float(values[5]), 'volume_encoded': int(values[6])})
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('TDX_SOURCE_CHANGED_DURING_READ')
    if len({row['date'] for row in rows}) != len(rows):
        raise ValueError('TDX_DUPLICATE_DAY')
    return rows, {'path': str(path), 'size': before.st_size, 'mtime_ns': before.st_mtime_ns,
        'window_sha256': digest.hexdigest(), 'rows': len(rows),
        'requested_start': days[0], 'requested_end': days[-1],
        'physical_value_dates': [row['date'] for row in rows],
        'date_key_probe_only_outside_window': True, 'buffering': 0}


def _digest(value, reason):
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise ValueError(reason)


def validate_tdx_evidence(evidence: dict) -> dict:
    """部署登记资料必须绑定转换和单位证据；READY标签不属于证明。"""
    if not isinstance(evidence, dict) or evidence.get('provider') != 'TDX':
        raise ValueError('TDX_SOURCE_PROVIDER_INVALID')
    if evidence.get('price_mode') != 'RAW':
        raise ValueError('TDX_RAW_PRICE_REQUIRED')
    _digest(evidence.get('source_sha256'), 'TDX_SOURCE_IDENTITY_INVALID')
    if not evidence.get('source_id') or not evidence.get('transformation_version'):
        raise ValueError('TDX_TRANSFORMATION_IDENTITY_REQUIRED')
    _digest(evidence.get('transformation_sha256'), 'TDX_TRANSFORMATION_IDENTITY_INVALID')
    originals = evidence.get('original_source_hashes')
    if not isinstance(originals, dict) or not originals:
        raise ValueError('TDX_ORIGINAL_LINEAGE_REQUIRED')
    for source, digest in originals.items():
        if not isinstance(source, str) or not source:
            raise ValueError('TDX_ORIGINAL_LINEAGE_INVALID')
        _digest(digest, 'TDX_ORIGINAL_LINEAGE_INVALID')
    units = evidence.get('unit_evidence')
    if not isinstance(units, dict) or not units.get('source'):
        raise ValueError('TDX_UNIT_EVIDENCE_REQUIRED')
    _digest(units.get('sha256'), 'TDX_UNIT_EVIDENCE_REQUIRED')
    volume = {'SHARES': 1, 'LOTS': 100}.get(evidence.get('volume_unit'))
    amount = {'CNY': 1, 'WAN_CNY': 10000}.get(evidence.get('amount_unit'))
    if volume is None or amount is None:
        raise ValueError('TDX_UNIT_UNKNOWN')
    if (units.get('volume_multiplier') != volume
            or units.get('amount_multiplier') != amount):
        raise ValueError('TDX_UNIT_CONVERSION_CONFLICT')
    semantics = evidence.get('prev_close_semantics', 'ABSENT')
    if semantics not in {'ABSENT', 'DERIVED_PREVIOUS_VALID_CLOSE', 'VENDOR_REFERENCE'}:
        raise ValueError('TDX_PREVIOUS_CLOSE_SEMANTICS_INVALID')
    if semantics == 'VENDOR_REFERENCE':
        reference = evidence.get('reference_evidence')
        if not isinstance(reference, dict) or not reference.get('source'):
            raise ValueError('TDX_REFERENCE_EVIDENCE_REQUIRED')
        _digest(reference.get('sha256'), 'TDX_REFERENCE_EVIDENCE_REQUIRED')
    # TDX与另源数值一致，不能自动升级历史可见时间。
    if evidence.get('historical_available_at_verified') is True:
        raise ValueError('TDX_HISTORICAL_AVAILABILITY_NOT_CERTIFIED')
    return {'volume_multiplier': volume, 'amount_multiplier': amount,
            'prev_close_semantics': semantics}


class TdxResearchAdapterV1:
    """适用于登记TDX日线原件/缓存的同一规范化流程，三板块一视同仁。"""

    def normalize_daily(self, frame: pd.DataFrame, *, evidence: dict,
                        required_fields=(), trusted_scope=None) -> dict:
        conversion = validate_tdx_evidence(evidence)
        if (not isinstance(required_fields, (tuple, list, set))
                or not all(isinstance(field, str) for field in required_fields)
                or not set(required_fields) <= set(DAILY_FIELDS)):
            raise ValueError('TDX_REQUIRED_FIELD_UNSUPPORTED')
        required = {'symbol', 'date', 'open', 'high', 'low', 'close', 'volume', 'amount'}
        if not isinstance(frame, pd.DataFrame) or not required <= set(frame):
            raise ValueError('TDX_DAILY_FIELDS_MISSING')
        if frame.empty:
            raise ValueError('TDX_DAILY_EMPTY')
        # 只传递声明的行情字段，不把缓存里的标签、收益或任意附加列送入规则。
        columns = ['symbol', 'date', 'open', 'high', 'low', 'close', 'volume', 'amount']
        columns += [name for name in ('prev_close', 'turn') if name in frame]
        daily = frame[columns].copy(deep=True)
        symbols = {s: canonical_symbol(s) for s in daily.symbol.unique()}
        if any(scope_board(s) not in BOARDS for s in symbols.values()):
            raise ValueError('TDX_SYMBOL_OUTSIDE_SUPPORTED_SCOPE')
        daily['symbol'] = daily.symbol.map(symbols)
        try:
            dates = pd.to_numeric(daily.date, errors='raise')
        except (ValueError, TypeError) as exc:
            raise ValueError('TDX_DATE_INVALID') from exc
        if (dates.isna().any() or not np.isfinite(dates).all()
                or (dates % 1 != 0).any()):
            raise ValueError('TDX_DATE_INVALID')
        daily['date'] = dates.astype('int64')
        try:
            pd.to_datetime(daily.date.astype(str), format='%Y%m%d', errors='raise')
        except ValueError as exc:
            raise ValueError('TDX_DATE_INVALID') from exc
        if trusted_scope is None:
            guard = ResearchDataAccessGuard()
        elif isinstance(trusted_scope, TrustedResearchDataAccessScopeV1):
            binding = trusted_scope.binding
            guard = trusted_scope.guard(purpose='INDEPENDENT_CONFIRMATION',
                dataset_id=binding['dataset_id'], manifest_sha256=binding['manifest_sha256'])
        else:
            raise ValueError('TDX_TRUSTED_DATA_SCOPE_REQUIRED')
        guard.check_frame(daily, 'date')
        if daily.duplicated(['symbol', 'date']).any():
            raise ValueError('TDX_DUPLICATE_SYMBOL_DATE')
        numeric = ['open', 'high', 'low', 'close', 'volume', 'amount']
        for key in numeric:
            try:
                daily[key] = pd.to_numeric(daily[key], errors='raise').astype('float64')
            except (ValueError, TypeError) as exc:
                raise ValueError('TDX_NUMERIC_FIELD_INVALID:' + key) from exc
        if not np.isfinite(daily[numeric]).all().all():
            raise ValueError('TDX_NONFINITE_DATA')
        if ((daily[['open', 'high', 'low', 'close']] <= 0).any().any()
                or (daily[['volume', 'amount']] < 0).any().any()
                or (daily.high < daily[['open', 'close', 'low']].max(axis=1)).any()
                or (daily.low > daily[['open', 'close', 'high']].min(axis=1)).any()):
            raise ValueError('TDX_PRICE_OR_ACTIVITY_INVALID')
        daily['volume'] *= conversion['volume_multiplier']
        daily['amount'] *= conversion['amount_multiplier']
        if not np.isfinite(daily[['volume', 'amount']]).all().all():
            raise ValueError('TDX_UNIT_CONVERSION_OVERFLOW')
        semantics = conversion['prev_close_semantics']
        if 'prev_close' in daily:
            try:
                previous = pd.to_numeric(daily.prev_close, errors='raise').astype('float64')
            except (ValueError, TypeError) as exc:
                raise ValueError('TDX_PREVIOUS_CLOSE_INVALID') from exc
            finite = previous.dropna()
            if not np.isfinite(finite).all() or (finite <= 0).any():
                raise ValueError('TDX_PREVIOUS_CLOSE_INVALID')
            if semantics == 'DERIVED_PREVIOUS_VALID_CLOSE':
                if 'derived_previous_valid_close' in daily:
                    raise ValueError('TDX_DERIVED_PREVIOUS_CLOSE_CONFLICT')
                daily['derived_previous_valid_close'] = previous
                daily['prev_close'] = np.nan
            elif semantics == 'VENDOR_REFERENCE':
                daily['prev_close'] = previous
            else:
                raise ValueError('TDX_UNDECLARED_PREVIOUS_CLOSE')
        else:
            if semantics != 'ABSENT':
                raise ValueError('TDX_PREVIOUS_CLOSE_DECLARATION_CONFLICT')
            daily['prev_close'] = np.nan
        reference_ready = daily.prev_close.notna().all()
        if 'prev_close' in required_fields and not reference_ready:
            raise ValueError('DATA_REQUIRED_FIELD_MISSING:prev_close')
        turn_ready = False
        if 'turn' in daily:
            unit = evidence.get('turn_unit')
            proof = evidence.get('turn_evidence')
            if unit not in {'PERCENT', 'FRACTION'} or not isinstance(proof, dict) or not proof.get('source'):
                raise ValueError('TDX_TURN_EVIDENCE_REQUIRED')
            _digest(proof.get('sha256'), 'TDX_TURN_EVIDENCE_REQUIRED')
            try:
                daily['turn'] = pd.to_numeric(daily.turn, errors='raise').astype('float64')
            except (ValueError, TypeError) as exc:
                raise ValueError('TDX_TURN_INVALID') from exc
            valid = daily.turn.dropna()
            if not np.isfinite(valid).all() or (valid < 0).any():
                raise ValueError('TDX_TURN_INVALID')
            if unit == 'FRACTION':
                daily['turn'] *= 100
            turn_ready = daily.turn.notna().all()
        if 'turn' in required_fields and not turn_ready:
            raise ValueError('DATA_REQUIRED_FIELD_MISSING:turn')
        daily = daily.sort_values(['symbol', 'date'], kind='stable').reset_index(drop=True)
        turn = daily[['symbol', 'date', 'volume'] + (['turn'] if 'turn' in daily else [])].copy()
        gaps = [] if reference_ready else ['DATA_EXECUTION_REFERENCE_UNAVAILABLE']
        qualification = {'purpose': 'EXPLORATORY', 'indicator_data_ready': True,
            'signal_price_data_ready': True, 'account_data_ready': False,
            'historical_availability': 'UNKNOWN', 'historical_independence': 'UNKNOWN',
            'independent_confirmation_eligible': False, 'strategy_qualified': False,
            'field_status': {'prices': 'TDX_RAW_REGISTERED', 'volume': 'UNIT_EVIDENCE_BOUND',
                'amount': 'UNIT_EVIDENCE_BOUND', 'prev_close': 'VENDOR_REFERENCE' if reference_ready else 'UNKNOWN',
                'turn': 'SOURCE_RECORDED' if turn_ready else 'UNKNOWN',
                'security_status': 'UNKNOWN', 'action_dates': 'UNKNOWN', 'availability_time': 'UNKNOWN'},
            'price_basis': {'execution': 'RAW', 'indicators': 'RAW_PENDING_ACTION_QUALIFICATION',
                            'cached_prev_close': semantics},
            'unit_evidence_level': evidence['unit_evidence'].get('grade', 'REGISTERED_EVIDENCE_ONLY'),
            'original_lineage_verification': 'REGISTERED_HASH_BINDINGS',
            'gaps': gaps + ['SECURITY_STATE_NOT_QUALIFIED', 'CORPORATE_ACTIONS_NOT_QUALIFIED']}
        source = {'adapter_version': VERSION, 'evidence': deepcopy(evidence),
                  'conversion': conversion, 'rows': len(daily),
                  'symbols': sorted(daily.symbol.unique().tolist())}
        return {'daily': daily, 'turn': turn, 'qualification': qualification,
                'source_identity': identity(source), 'source_evidence': source}


def merge_daily_sources(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """同键多源仅允许明确相等；不按文件顺序抢占冲突报价。"""
    if not frames:
        raise ValueError('TDX_DAILY_EMPTY')
    result = pd.concat(frames, ignore_index=True)
    fields = [field for field in (*DAILY_FIELDS, 'derived_previous_valid_close') if field in result]
    duplicated = result.duplicated(['symbol', 'date'], keep=False)
    if duplicated.any():
        for _, group in result.loc[duplicated].groupby(['symbol', 'date'], sort=False):
            if any(group[field].nunique(dropna=False) > 1 for field in fields):
                raise ValueError('TDX_MULTI_SOURCE_VALUE_CONFLICT')
        result = result.drop_duplicates(['symbol', 'date'], keep='first')
    return result.sort_values(['symbol', 'date'], kind='stable').reset_index(drop=True)


def compare_cache_to_raw_window(cache: pd.DataFrame, raw: pd.DataFrame) -> dict:
    """逐行绑定缓存与限窗DAY原件；单位未证明前仅比较原编码值。"""
    keys = ['symbol', 'date']
    fields = ['open', 'high', 'low', 'close', 'volume', 'amount']
    if not isinstance(cache, pd.DataFrame) or not isinstance(raw, pd.DataFrame):
        raise ValueError('TDX_CACHE_ORIGIN_FRAME_INVALID')
    source = raw.rename(columns={'volume_encoded': 'volume', 'amount_encoded': 'amount'})
    if not set(keys + fields) <= set(cache) or not set(keys + fields) <= set(source):
        raise ValueError('TDX_CACHE_ORIGIN_FIELDS_MISSING')
    if cache.duplicated(keys).any() or source.duplicated(keys).any():
        raise ValueError('TDX_CACHE_ORIGIN_DUPLICATE_KEY')
    joined = cache[keys + fields].merge(source[keys + fields], on=keys, how='outer',
        suffixes=('_cache', '_raw'), indicator=True, validate='one_to_one')
    missing = joined['_merge'] != 'both'
    checks = {}
    for field in fields:
        left, right = joined[f'{field}_cache'], joined[f'{field}_raw']
        finite = np.isfinite(left) & np.isfinite(right)
        checks[field] = bool(finite.all() and (left == right).all())
    return {'version': 'TDX_CACHE_ORIGIN_COMPARE_V1', 'passed': bool(not missing.any() and all(checks.values())),
        'rows': len(joined), 'missing_cache_rows': int((joined['_merge'] == 'right_only').sum()),
        'missing_raw_rows': int((joined['_merge'] == 'left_only').sum()), 'field_checks': checks,
        'comparison_basis': 'EXACT_RAW_ENCODING_VALUES_NOT_UNIT_OR_PIT_PROOF'}
