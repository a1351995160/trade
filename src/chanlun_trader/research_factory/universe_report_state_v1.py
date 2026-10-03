"""报告自身的日边界状态和不可变明细，不能作为交易或核账答案。"""
import hashlib
import json
import math
import os
from pathlib import Path
import time

import numpy as np

from .common import canonical_json, stable_hash
from .universe_execution_artifacts_v1 import ArtifactSequence, DayArtifacts


VERSION = 'UNIVERSE_OWN_REPORT_STATE_V1'


def _native(value):
    """自身报告codec保留数值类型，禁止numpy计数被通用JSON默认函数写成字符串。"""
    if isinstance(value, np.generic):
        return _native(value.item())
    if isinstance(value, dict):
        return {key: _native(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_native(item) for item in value]
    if value is None or type(value) in (str, bool, int, float):
        return value
    raise ValueError('REPORT_OWN_STATE_TYPE_UNSUPPORTED')


def source_identity(names):
    return {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in names}


def deadline(segment_seconds):
    if segment_seconds is None:
        return None
    if (type(segment_seconds) not in (int, float) or not math.isfinite(segment_seconds)
            or segment_seconds < 0):
        raise ValueError('REPORT_SEGMENT_SECONDS_INVALID')
    return time.monotonic() + segment_seconds


def at_boundary(limit, message):
    if limit is not None and time.monotonic() >= limit:
        from .universe_account_backend_v2 import SegmentBoundary
        boundary = SegmentBoundary(message)
        boundary.phase = 'REPORT'
        raise boundary


def verify_artifact_bytes(manifest):
    """已经逐日核证过的前缀仍须匹配真实字节，复用时不重新解压整窗。"""
    ArtifactSequence(manifest, 'events')
    root = Path(manifest['root'])
    if root.resolve() != root.absolute():
        raise ValueError('REPORT_ARTIFACT_ROOT_REDIRECTED')
    for row in manifest['days']:
        path = root / row['file']
        if path.resolve().parent != root or path.name != row['file']:
            raise ValueError('REPORT_ARTIFACT_REDIRECTED')
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != row['sha256']:
            raise ValueError('REPORT_ARTIFACT_BYTES_CHANGED')


def iter_report_details(manifest):
    """展示或导出时读取明细；研究续跑本身不再次发出已提交行。"""
    reader = ArtifactSequence(manifest, 'events')
    for index in range(len(reader)):
        yield from reader[index]


class OwnReportState:
    def __init__(self, path, *, kind, binding, initial, source_artifacts=None):
        self.path = Path(path).absolute()
        if self.path.resolve() != self.path:
            raise ValueError('REPORT_CHECKPOINT_REDIRECTED')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.root = self.path.parent / (self.path.stem + '_DETAILS')
        self.identity = stable_hash({'version': VERSION, 'kind': kind, 'binding': binding})
        self.state, self.complete, self.output = initial, False, None
        manifest = None
        if self.path.exists():
            saved = json.loads(self.path.read_text(encoding='utf-8'))
            if saved.get('checkpoint_hash') != stable_hash({k: v for k, v in saved.items() if k != 'checkpoint_hash'}):
                raise ValueError('REPORT_CHECKPOINT_CHANGED')
            if (saved.get('version') != VERSION or saved.get('identity') != self.identity
                    or saved.get('origin') != 'OWN_REPORT_AGGREGATION_ONLY'):
                raise ValueError('REPORT_CHECKPOINT_IDENTITY_CONFLICT')
            manifest = saved['details_manifest']
            if manifest['identity'] != self.identity or Path(manifest['root']) != self.root:
                raise ValueError('REPORT_DETAILS_IDENTITY_CONFLICT')
            verify_artifact_bytes(manifest)
            if source_artifacts is not None:
                verify_artifact_bytes(source_artifacts)
            self.state, self.complete, self.output = saved['state'], saved['complete'], saved['output']
        self.details = DayArtifacts(self.root, identity=self.identity,
                                    days=manifest['days'] if manifest else ())
        self._quarantine_uncommitted()
        if manifest is None:
            self.save()

    def _quarantine_uncommitted(self):
        committed = {row['file'] for row in self.details.days}
        for path in self.root.glob('*.json.gz'):
            if path.name in committed:
                continue
            if path.resolve().parent != self.root:
                raise ValueError('REPORT_UNCOMMITTED_TAIL_REDIRECTED')
            quarantine = self.root / 'UNCOMMITTED'
            quarantine.mkdir(exist_ok=True)
            if quarantine.resolve().parent != self.root:
                raise ValueError('REPORT_QUARANTINE_REDIRECTED')
            target = quarantine / (path.name + '.' + str(time.time_ns()))
            path.rename(target)

    def save(self):
        body = {'version': VERSION, 'identity': self.identity, 'origin': 'OWN_REPORT_AGGREGATION_ONLY',
                'state': self.state, 'complete': self.complete, 'output': self.output,
                'details_manifest': self.details.manifest()}
        body = _native(body)
        value = {**body, 'checkpoint_hash': stable_hash(body)}
        temporary = self.path.with_name(self.path.name + '.tmp')
        if temporary.resolve() != temporary:
            raise ValueError('REPORT_CHECKPOINT_TEMP_REDIRECTED')
        with temporary.open('w', encoding='utf-8', newline='\n') as stream:
            stream.write(canonical_json(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)

    def commit(self, day, events):
        self.details.commit(day, {'events': _native(events)})
        self.save()

    def finish(self, output):
        self.output, self.complete = _native(output), True
        self.save()
        return self.output
