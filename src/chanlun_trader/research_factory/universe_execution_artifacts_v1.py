"""逐日不可变经济证据；清单与字节双重校验，读取只占一个交易日。"""
from collections.abc import Sequence
import gzip
import hashlib
import json
import os
from pathlib import Path

from .common import canonical_json, stable_hash

VERSION = 'UNIVERSE_EXECUTION_ARTIFACTS_V1'


class DayArtifacts:
    def __init__(self, root, *, identity, days=()):
        self.root = Path(root).absolute()
        if self.root.resolve() != self.root:
            raise ValueError('UNIVERSE_ARTIFACT_ROOT_REDIRECTED')
        self.root.mkdir(parents=True, exist_ok=True)
        self.identity, self.days = identity, list(days)
        self.head = self.days[-1]['chain'] if self.days else stable_hash([VERSION, identity])

    def commit(self, day, payload):
        if self.days and day <= self.days[-1]['date']:
            raise ValueError('UNIVERSE_DAY_ALREADY_COMMITTED')
        raw = canonical_json(payload).encode('utf-8')
        data = gzip.compress(raw, mtime=0)
        name = str(day) + '_' + hashlib.sha256(data).hexdigest() + '.json.gz'
        path = self.root / name
        if path.exists():
            if path.read_bytes() != data:
                raise ValueError('UNIVERSE_ARTIFACT_CHANGED')
        else:
            with path.open('xb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        row = {'date': day, 'file': name, 'sha256': hashlib.sha256(data).hexdigest(),
               'previous': self.head, 'payload_identity': stable_hash(payload)}
        row['chain'] = stable_hash(row)
        self.head = row['chain']
        self.days.append(row)
        return row

    def manifest(self):
        value = {'version': VERSION, 'root': str(self.root), 'identity': self.identity,
                 'days': list(self.days), 'head': self.head}
        return {**value, 'manifest_identity': stable_hash(value)}


class ArtifactSequence(Sequence):
    def __init__(self, manifest, field):
        value = {k: v for k, v in manifest.items() if k != 'manifest_identity'}
        if value.get('version') != VERSION or manifest['manifest_identity'] != stable_hash(value):
            raise ValueError('UNIVERSE_ARTIFACT_MANIFEST_CHANGED')
        head, previous_day = stable_hash([VERSION, value['identity']]), None
        for row in value['days']:
            if (row['previous'] != head or row['chain'] != stable_hash({k: v for k, v in row.items() if k != 'chain'})
                    or previous_day is not None and row['date'] <= previous_day):
                raise ValueError('UNIVERSE_ARTIFACT_CHAIN_CHANGED')
            head, previous_day = row['chain'], row['date']
        if head != value['head']:
            raise ValueError('UNIVERSE_ARTIFACT_HEAD_CHANGED')
        self.manifest, self.field = manifest, field

    def __len__(self):
        return len(self.manifest['days'])

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[i] for i in range(*index.indices(len(self)))]
        return self.read_day(index)[self.field]

    def _path(self, index):
        row = self.manifest['days'][index]
        root = Path(self.manifest['root'])
        path = root / row['file']
        if path.resolve().parent != root or path.name != row['file']:
            raise ValueError('UNIVERSE_ARTIFACT_REDIRECTED')
        return path, row

    def verify_bytes(self, index):
        """恢复已提交前缀只验证真实原件字节；经济内容仍由独立审计重算。"""
        path, row = self._path(index)
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != row['sha256']:
            raise ValueError('UNIVERSE_ARTIFACT_CHANGED')

    def read_day(self, index):
        """核验与报告消费当天内容时，同时核对字节及规范化内容身份。"""
        path, row = self._path(index)
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != row['sha256']:
            raise ValueError('UNIVERSE_ARTIFACT_CHANGED')
        value = json.loads(gzip.decompress(data))
        if stable_hash(value) != row['payload_identity']:
            raise ValueError('UNIVERSE_ARTIFACT_PAYLOAD_CHANGED')
        return value


def hydrated_result(result):
    """新版结果仅包含引用；不把整窗证据物化成 Python 字典列表。"""
    if result.get('result_schema') != 'UNIVERSE_SHARDED_RESULT_V2':
        return result
    value = dict(result)
    for field, source in [('daily_accounts', 'account'), ('scan_days', 'scan'), ('decisions', 'decision')]:
        value[field] = ArtifactSequence(result['artifacts'], source)
    return value
