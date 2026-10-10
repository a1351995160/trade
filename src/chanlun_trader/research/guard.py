"""Research data access guard: hard lock on FINAL TEST data.

FINAL_TEST_START = 2025-08-01. 未获用户明确授权前，研究代码禁止读取
>= 2025-08-01 的任何数据（Daily / 5m / 龙虎榜 / 涨停 / 资金流 / 财务 / 行情等）。
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from copy import deepcopy
import hashlib
import hmac
import json
from pathlib import Path
import secrets
from typing import Any, Iterable

FINAL_TEST_START = 2025_08_01
RESEARCH_END = 2025_07_31


class FinalTestAccessViolation(RuntimeError):
    """研究代码试图访问 FINAL TEST（2025-08-01+）数据。"""

    def __init__(self, msg: str = ""):
        super().__init__(
            msg
            or f"FINAL_TEST_ACCESS_VIOLATION: research data access >= {FINAL_TEST_START} is sealed."
        )


class UnsafeLegacyQfqAccessError(RuntimeError):
    """正式研究上下文禁止使用 full-sample legacy V1 qfq。"""


_RESEARCH_CONTEXT: ContextVar[bool] = ContextVar("research_context", default=False)


@contextmanager
def research_context():
    token = _RESEARCH_CONTEXT.set(True)
    try:
        yield
    finally:
        _RESEARCH_CONTEXT.reset(token)


def is_research_context() -> bool:
    return _RESEARCH_CONTEXT.get()


@dataclass(frozen=True)
class ResearchDataAccessGuardConfig:
    """Guard 配置。final_test_start/research_end 默认使用项目硬锁。"""

    final_test_start: int = FINAL_TEST_START
    research_end: int = RESEARCH_END
    allow_override: bool = False  # 只有用户明确授权 FINAL TEST 时才可设为 True


class ResearchDataAccessGuard:
    """所有研究数据访问的强制闸门。

    规则：
    - date < FINAL_TEST_START 且 <= research_end：放行。
    - date >= FINAL_TEST_START：raise FinalTestAccessViolation。
    - date > research_end（但 < final_test_start 不可能，两者相等边界）: 放行到 research_end。
    """

    def __init__(self, config: ResearchDataAccessGuardConfig | None = None):
        self.config = config or ResearchDataAccessGuardConfig()

    # ---- date guards ----
    def check_date(self, date: int, label: str = "") -> None:
        if date >= self.config.final_test_start:
            raise FinalTestAccessViolation(
                f"date={date} {label} >= FINAL_TEST_START={self.config.final_test_start}"
            )
        if date > self.config.research_end:
            raise FinalTestAccessViolation(
                f"date={date} {label} > RESEARCH_END={self.config.research_end}"
            )

    def check_range(self, start_date: int, end_date: int, label: str = "") -> None:
        self.check_date(start_date, label + " start")
        self.check_date(end_date, label + " end")

    # ---- dataframe guard ----
    def check_frame(self, df: Any, date_column: str = "date") -> None:
        """检查 DataFrame 的日期列没有 FINAL TEST 数据。"""
        if df is None:
            return
        if date_column not in df.columns:
            return
        dates = df[date_column]
        bad = dates[dates >= self.config.final_test_start]
        if len(bad):
            raise FinalTestAccessViolation(
                f"DataFrame contains {len(bad)} rows with {date_column} >= FINAL_TEST_START "
                f"(max={int(bad.max())})"
            )

    # ---- iterable guard ----
    def check_int_iterable(self, dates: Iterable[int], label: str = "") -> None:
        for d in dates:
            self.check_date(int(d), label)

    def check_event_metadata(self, event):
        for key in ('effective_date', 'record_date', 'payment_date', 'share_credit_date', 'tradable_date'):
            if event.get(key) is not None:
                self.check_date(int(str(event[key]).replace('-', '')), 'frozen event ' + key)

    def check_output_bytes(self, size, label=''):
        """默认守卫不授予范围，也不引入独立授权才有的输出额度。"""
        return None

    @property
    def research_end(self) -> int:
        return self.config.research_end

    @property
    def final_test_start(self) -> int:
        return self.config.final_test_start


class TrustedResearchDataAccessAuthorityV1:
    """部署注入的授权解析器；请求字典和普通客户端不能签发 scope。

    resolver 只解析部署已登记的批准引用，并负责核验 owner 批准、撤销及
    独立协议真实性。本类再次约束读取用途、文件身份、范围、配方与额度。
    Python 进程本身不是安全沙箱；禁止向设计模型暴露本 authority。
    """

    def __init__(self, resolver, *, deployment_identity=None):
        if not callable(resolver):
            raise ValueError('TRUSTED_DATA_RESOLVER_REQUIRED')
        self._resolver = resolver
        self._key = secrets.token_bytes(32)
        self._deployment_identity = deepcopy(deployment_identity)

    def authorize(self, reference, *, purpose, dataset_id, manifest_sha256,
                  sources, output_start, output_end, recipe_version,
                  protocol_binding=None):
        if not isinstance(reference, str) or not reference:
            raise ValueError('TRUSTED_DATA_AUTHORIZATION_REFERENCE_REQUIRED')
        grant = self._resolver(reference)
        if (not isinstance(grant, dict)
                or grant.get('schema_version') != 'TRUSTED_RESEARCH_DATA_ACCESS_AUTHORIZATION_V1'
                or any(not isinstance(grant.get(key), str) or not grant[key]
                       for key in ('authorization_id', 'approval_id'))
                or purpose not in {'DATASET_TRAIN_PROJECTION', 'INDEPENDENT_CONFIRMATION'}):
            raise ValueError('TRUSTED_DATA_AUTHORIZATION_INVALID')
        expected = {'purpose': purpose, 'dataset_id': dataset_id,
                    'manifest_sha256': manifest_sha256, 'recipe_version': recipe_version,
                    'output_start': output_start, 'output_end': output_end}
        if any(grant.get(key) != value for key, value in expected.items()):
            raise ValueError('TRUSTED_DATA_AUTHORIZATION_BINDING_CONFLICT')
        if (not isinstance(manifest_sha256, str) or len(manifest_sha256) != 64
                or any(c not in '0123456789abcdef' for c in manifest_sha256)
                or not dataset_id or not recipe_version):
            raise ValueError('TRUSTED_DATA_AUTHORIZATION_BINDING_INVALID')
        _trusted_day(output_start)
        _trusted_day(output_end)
        if output_start > output_end:
            raise ValueError('TRUSTED_DATA_OUTPUT_RANGE_INVALID')
        if (not isinstance(sources, dict) or not sources
                or not isinstance(grant.get('sources'), dict)
                or set(grant['sources']) != set(sources)):
            raise ValueError('TRUSTED_DATA_SOURCE_BINDING_CONFLICT')
        for name, source in sources.items():
            approved = grant['sources'][name]
            if (not isinstance(source, dict) or not isinstance(approved, dict)
                    or source.get('sha256') != approved.get('sha256')
                    or not isinstance(source.get('sha256'), str)
                    or len(source['sha256']) != 64
                    or any(c not in '0123456789abcdef' for c in source['sha256'])):
                raise ValueError('TRUSTED_DATA_SOURCE_BINDING_CONFLICT')
            for record in (source, approved):
                _trusted_day(record.get('physical_start'))
                _trusted_day(record.get('physical_end'))
                if record['physical_start'] > record['physical_end']:
                    raise ValueError('TRUSTED_DATA_SOURCE_RANGE_INVALID')
            if (source['physical_start'] < approved['physical_start']
                    or source['physical_end'] > approved['physical_end']):
                raise ValueError('TRUSTED_DATA_WHOLE_SOURCE_NOT_AUTHORIZED')
        if (output_start < min(row['physical_start'] for row in sources.values())
                or output_end > max(row['physical_end'] for row in sources.values())):
            raise ValueError('TRUSTED_DATA_OUTPUT_NOT_COVERED')
        if any(type(grant.get(key)) is not int or grant[key] <= 0
               for key in ('max_input_bytes', 'max_output_bytes')):
            raise ValueError('TRUSTED_DATA_RESOURCE_LIMIT_REQUIRED')
        if purpose == 'INDEPENDENT_CONFIRMATION':
            keys = ('protocol_id', 'protocol_sha256', 'independent_evidence_id',
                    'independent_evidence_sha256')
            if (not isinstance(protocol_binding, dict)
                    or any(not protocol_binding.get(key)
                           or grant.get(key) != protocol_binding[key] for key in keys)
                    or any(not isinstance(grant[key], str) or len(grant[key]) != 64
                           or any(c not in '0123456789abcdef' for c in grant[key])
                           for key in ('protocol_sha256', 'independent_evidence_sha256'))):
                raise ValueError('TRUSTED_DATA_INDEPENDENT_PROTOCOL_REQUIRED')
        payload = json.dumps(grant, sort_keys=True, separators=(',', ':'), allow_nan=False)
        request = {**expected, 'sources': deepcopy(sources)}
        if protocol_binding is not None:
            request['protocol_binding'] = deepcopy(protocol_binding)
        request_json = json.dumps(request, sort_keys=True, separators=(',', ':'), allow_nan=False)
        scope = object.__new__(TrustedResearchDataAccessScopeV1)
        object.__setattr__(scope, '_issuer', self)
        object.__setattr__(scope, '_reference', reference)
        object.__setattr__(scope, '_payload', payload)
        object.__setattr__(scope, '_request', request_json)
        object.__setattr__(scope, '_signature', self._sign(payload + '\0' + request_json))
        scope._validated()
        return scope

    def _sign(self, payload):
        return hmac.new(self._key, payload.encode('utf-8'), hashlib.sha256).hexdigest()

    def restore(self, reference, **expected_bindings):
        """固定 builder 用已冻结的预期绑定恢复；引用本身不是读取能力。"""
        if (not isinstance(reference, dict)
                or set(reference) != {'schema_version', 'authorization_ref',
                                      'authorization_id', 'approved_binding_sha256'}
                or reference['schema_version'] != 'TRUSTED_RESEARCH_DATA_ACCESS_REFERENCE_V1'):
            raise ValueError('TRUSTED_DATA_REFERENCE_INVALID')
        scope = self.authorize(reference['authorization_ref'], **expected_bindings)
        if scope.reference != reference:
            raise ValueError('TRUSTED_DATA_REFERENCE_BINDING_CONFLICT')
        return scope


def _trusted_day(value):
    if type(value) is not int:
        raise ValueError('TRUSTED_DATA_DATE_INVALID')
    try:
        datetime.strptime(str(value), '%Y%m%d')
    except ValueError as exc:
        raise ValueError('TRUSTED_DATA_DATE_INVALID') from exc
    if len(str(value)) != 8:
        raise ValueError('TRUSTED_DATA_DATE_INVALID')
    return value


@dataclass(frozen=True, init=False)
class TrustedResearchDataAccessScopeV1:
    """authority 签发的用途能力；不能用用户字典或 bool 代替。"""

    _issuer: TrustedResearchDataAccessAuthorityV1
    _reference: str
    _payload: str
    _request: str
    _signature: str

    def __init__(self, *args, **kwargs):
        raise ValueError('TRUSTED_DATA_SCOPE_ISSUER_REQUIRED')

    def _validated(self):
        if (not isinstance(self._issuer, TrustedResearchDataAccessAuthorityV1)
                or not hmac.compare_digest(self._signature, self._issuer._sign(self._payload + '\0' + self._request))):
            raise ValueError('TRUSTED_DATA_SCOPE_INVALID')
        value = json.loads(self._payload)
        current = self._issuer._resolver(self._reference)
        if (not isinstance(current, dict) or json.dumps(current, sort_keys=True,
                separators=(',', ':'), allow_nan=False) != self._payload):
            raise ValueError('TRUSTED_DATA_AUTHORIZATION_REVOKED_OR_CHANGED')
        try:
            expires = datetime.fromisoformat(value['expires_at'])
            if expires.tzinfo is None or expires <= datetime.now(timezone.utc):
                raise ValueError()
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError('TRUSTED_DATA_AUTHORIZATION_EXPIRED') from exc
        return value

    @property
    def binding(self):
        return self._validated()

    @property
    def reference(self):
        binding = self._validated()
        return {'schema_version': 'TRUSTED_RESEARCH_DATA_ACCESS_REFERENCE_V1',
            'authorization_ref': self._reference, 'authorization_id': binding['authorization_id'],
            'approved_binding_sha256': hashlib.sha256(self._payload.encode('utf-8')).hexdigest()}

    @property
    def frozen_binding(self):
        self._validated()
        value = {'reference': self.reference, 'expected_bindings': json.loads(self._request)}
        if self._issuer._deployment_identity is not None:
            value['deployment_identity'] = deepcopy(self._issuer._deployment_identity)
        return value

    def __reduce__(self):
        raise TypeError('TRUSTED_DATA_SCOPE_SERIALIZE_REFERENCE_ONLY')

    def guard(self, *, purpose, dataset_id, manifest_sha256, source_name=None):
        binding = self._validated()
        if (binding['purpose'] != purpose or binding['dataset_id'] != dataset_id
                or binding['manifest_sha256'] != manifest_sha256):
            raise ValueError('TRUSTED_DATA_SCOPE_PURPOSE_CONFLICT')
        if source_name is not None and source_name not in binding['sources']:
            raise ValueError('TRUSTED_DATA_SCOPE_SOURCE_CONFLICT')
        return _ScopedResearchDataAccessGuard(self, source_name)


class _ScopedResearchDataAccessGuard(ResearchDataAccessGuard):
    def __init__(self, scope, source_name):
        super().__init__()
        self._scope, self._source_name = scope, source_name

    def check_date(self, date, label=''):
        binding = self._scope._validated()
        if self._source_name is None:
            lower, upper = binding['output_start'], binding['output_end']
        else:
            source = binding['sources'][self._source_name]
            lower, upper = source['physical_start'], source['physical_end']
        if not lower <= _trusted_day(date) <= upper:
            raise FinalTestAccessViolation(f'TRUSTED_DATA_SCOPE_RANGE_VIOLATION: {label} date={date}')

    def check_frame(self, df, date_column='date'):
        if df is not None and date_column in df:
            self.check_int_iterable(df[date_column].unique(), date_column)

    def check_output_bytes(self, size, label=''):
        binding = self._scope._validated()
        if type(size) is not int or size < 0:
            raise ValueError('TRUSTED_DATA_OUTPUT_BYTE_COUNT_INVALID')
        if size > binding['max_output_bytes']:
            raise ValueError('TRUSTED_DATA_OUTPUT_BYTE_LIMIT_EXCEEDED:' + label)

    def check_event_metadata(self, event):
        binding = self._scope._validated()
        days = [_trusted_day(int(str(event[key]).replace('-', ''))) for key in
                ('effective_date', 'record_date', 'payment_date', 'share_credit_date', 'tradable_date')
                if event.get(key) is not None]
        sources = ([binding['sources'][self._source_name]] if self._source_name is not None
                   else binding['sources'].values())
        if (not days or min(days) > binding['output_end'] or max(days) < binding['output_start']
                or any(not any(source['physical_start'] <= day <= source['physical_end']
                               for source in sources) for day in days)):
            raise FinalTestAccessViolation('TRUSTED_DATA_EVENT_METADATA_RANGE_VIOLATION')
        for key in ('source_published_at', 'available_at'):
            if event.get(key) and int(str(event[key])[:10].replace('-', '')) > binding['output_end']:
                raise FinalTestAccessViolation('TRUSTED_DATA_EVENT_NOT_AVAILABLE')


def configured_access_authority(deployment_config_path, *, expected_sha256):
    """固定 schema 的维护者登记解析器；配置哈希来自可信部署/job，非 INPUT。

    授权记录逐份绑定完整文件 SHA 和受保护 OwnerApprovalStore 回执，不执行
    配置指定模块或 URL。每次重新解析时再次核对配置、记录及 owner 批准。
    """
    from ..research_factory.secure_file_reference_v1 import (
        checked_directory_path, read_pinned_json, validated_reference_path,
    )
    path = validated_reference_path(deployment_config_path,
        error_code='TRUSTED_DATA_DEPLOYMENT_PATH_INVALID')

    def load_config():
        value = read_pinned_json({'path': str(path), 'sha256': expected_sha256},
            error_code='TRUSTED_DATA_DEPLOYMENT_IDENTITY_CONFLICT')
        if (not isinstance(value, dict) or set(value) != {'schema_version', 'owner_approval_store_root', 'records'}
                or value['schema_version'] != 'TRUSTED_RESEARCH_DATA_DEPLOYMENT_V1'
                or not isinstance(value['records'], dict)):
            raise ValueError('TRUSTED_DATA_DEPLOYMENT_SCHEMA_INVALID')
        checked_directory_path(value['owner_approval_store_root'],
            error_code='TRUSTED_DATA_APPROVAL_STORE_INVALID')
        return value

    load_config()

    def resolve(reference):
        config = load_config()
        record = config['records'].get(reference)
        if (not isinstance(record, dict) or set(record) != {'path', 'sha256', 'approval_reference'}):
            raise ValueError('TRUSTED_DATA_REFERENCE_NOT_REGISTERED')
        value = read_pinned_json({key: record[key] for key in ('path', 'sha256')},
            error_code='TRUSTED_DATA_AUTHORIZATION_IDENTITY_CONFLICT')
        from ..research_factory.campaign_scope_v1 import OwnerApprovalStoreV1
        approval = OwnerApprovalStoreV1(config['owner_approval_store_root']).require(record['approval_reference'], value)
        # 回执身份来自受保护批准记录；授权文件无需自引用其尚未生成的批准哈希。
        return {**value, 'approval_id': approval['approval_id']}

    return TrustedResearchDataAccessAuthorityV1(resolve,
        deployment_identity={'path': str(path), 'sha256': expected_sha256})


def guard_from_frozen(value, *, trusted_scope=None, expected_deployment=None,
                      expected_dataset_id=None, expected_manifest_sha256=None,
                      expected_protocol_binding=None):
    """冻结/执行共用恢复闸门；没有外部可信部署身份时未来读取仍被拒绝。"""
    frozen = value.get('trusted_data_access')
    if frozen is None:
        if trusted_scope is not None:
            raise ValueError('TRUSTED_DATA_FROZEN_BINDING_REQUIRED')
        return ResearchDataAccessGuard()
    if not isinstance(frozen, dict) or not {'reference', 'expected_bindings'} <= set(frozen):
        raise ValueError('TRUSTED_DATA_FROZEN_BINDING_INVALID')
    if expected_deployment is not None and frozen.get('deployment_identity') != expected_deployment:
        raise ValueError('TRUSTED_DATA_DEPLOYMENT_EXPECTATION_REQUIRED')
    request = frozen['expected_bindings']
    if not isinstance(request, dict) or request.get('purpose') != 'INDEPENDENT_CONFIRMATION':
        raise ValueError('TRUSTED_DATA_FROZEN_PURPOSE_INVALID')
    for key, expected in (('dataset_id', expected_dataset_id),
                          ('manifest_sha256', expected_manifest_sha256),
                          ('protocol_binding', expected_protocol_binding)):
        if expected is not None and request.get(key) != expected:
            raise ValueError('TRUSTED_DATA_FROZEN_EXPECTATION_CONFLICT')
    if trusted_scope is None:
        if (not isinstance(expected_deployment, dict) or set(expected_deployment) != {'path', 'sha256'}
                or frozen.get('deployment_identity') != expected_deployment):
            raise ValueError('TRUSTED_DATA_DEPLOYMENT_EXPECTATION_REQUIRED')
        authority = configured_access_authority(expected_deployment['path'],
                                               expected_sha256=expected_deployment['sha256'])
        trusted_scope = authority.restore(frozen['reference'], **request)
    if (not isinstance(trusted_scope, TrustedResearchDataAccessScopeV1)
            or trusted_scope.frozen_binding != frozen):
        raise ValueError('TRUSTED_DATA_FROZEN_SCOPE_CONFLICT')
    guard = trusted_scope.guard(purpose='INDEPENDENT_CONFIRMATION',
        dataset_id=request['dataset_id'], manifest_sha256=request['manifest_sha256'])
    window = value.get('window')
    if window is not None:
        guard.check_range(window['feature_start'], window['account_end'], 'trusted frozen window')
    return guard
