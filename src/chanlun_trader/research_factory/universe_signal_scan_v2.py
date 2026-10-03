"""完整证券历史逐只计算，数值缓存只读映射；不在日期段重置递归指标。"""
import hashlib
import json
from pathlib import Path
import time
from types import SimpleNamespace

import numpy as np

from .common import stable_hash
from .exploration_governance import immutable
from .universe_signal_scan_v1 import UniverseSignalScanV1

VERSION = 'UNIVERSE_SIGNAL_SCAN_V2'


class UniverseSignalScanV2:
    def __init__(self, strategy, inputs, *, root, batch_size=128, progress=None, deadline=None):
        self.strategy, self.inputs = strategy, inputs
        self.root = Path(root).absolute()
        if self.root.resolve() != self.root:
            raise ValueError('UNIVERSE_SCAN_CACHE_REDIRECTED')
        self.root.mkdir(parents=True, exist_ok=True)
        symbols, days = inputs.window['symbols'], inputs.window['calendar']
        self.symbol_index = {s: i for i, s in enumerate(symbols)}
        self.day_index = {d: i for i, d in enumerate(days)}
        self.columns = ['buy', 'sell', 'market_filter', 'ready']
        if hasattr(strategy, 'selection'):
            self.columns += ['score', 'score_ready', 'condition_ready']
        from .strategy_interface_v1 import describe
        sources = dict(describe(strategy)['source_hashes'])
        for name in ('universe_signal_scan_v1.py', 'universe_signal_scan_v2.py', 'causal_dividend_features_v1.py',
                     'corporate_action_price_v2.py', 'common.py'):
            path = Path(__file__).with_name(name)
            sources[str(path.resolve())] = hashlib.sha256(path.read_bytes()).hexdigest()
        identity = stable_hash([VERSION, inputs.input_identity, strategy.rule_identity, self.columns, sources])
        manifest_path, array_path = self.root / 'MANIFEST.json', self.root / 'CONDITIONS.npy'
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            with array_path.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            if manifest['cache_identity'] != identity or digest != manifest['sha256']:
                raise ValueError('UNIVERSE_SCAN_CACHE_CHANGED')
            self.preparation = manifest['preparation']
        else:
            # 未提交缓存永不被当作已完成资料复用。
            binding_path = self.root / 'PREPARATION_BINDING.json'
            if array_path.exists():
                if not binding_path.exists() or json.loads(binding_path.read_text(encoding='utf-8')) != {'identity': identity}:
                    raise ValueError('UNIVERSE_SCAN_INCOMPLETE_BINDING_CONFLICT')
                values = np.load(array_path, mmap_mode='r+')
            else:
                values = np.lib.format.open_memmap(array_path, mode='w+', dtype='float64',
                    shape=(len(symbols), len(days), len(self.columns)))
                values[:] = np.nan
                values.flush()
                immutable(binding_path, {'identity': identity})
            groups = inputs.bundle['daily'].groupby('symbol', sort=False).indices
            turns = inputs.bundle['turn'].groupby('symbol', sort=False).indices
            self.preparation = []
            for i, symbol in enumerate(symbols):
                receipt = self.root / (symbol + '.json')
                if receipt.exists():
                    row = json.loads(receipt.read_text(encoding='utf-8'))
                    if row['identity'] != identity or row['array_hash'] != hashlib.sha256(values[i].tobytes()).hexdigest():
                        raise ValueError('UNIVERSE_SCAN_PREPARATION_CHANGED')
                    self.preparation.extend(row['preparation'])
                    continue
                bundle = dict(inputs.bundle)
                bundle['daily'] = inputs.bundle['daily'].iloc[groups.get(symbol, [])]
                bundle['turn'] = inputs.bundle['turn'].iloc[turns.get(symbol, [])]
                proxy = SimpleNamespace(bundle=bundle, input_identity=inputs.input_identity,
                                        window={**inputs.window, 'symbols': [symbol]})
                scanner = UniverseSignalScanV1(strategy, proxy, batch_size=batch_size, _price_context=inputs)
                frame = scanner.conditions[symbol]
                for day, row in frame.iterrows():
                    if int(day) in self.day_index:
                        values[i, self.day_index[int(day)], :] = [row.get(k, np.nan) for k in self.columns]
                self.preparation.extend(scanner.preparation)
                values.flush()
                immutable(receipt, {'identity': identity, 'preparation': scanner.preparation,
                    'array_hash': hashlib.sha256(values[i].tobytes()).hexdigest()})
                if progress:
                    progress({'phase': 'FEATURES', 'processed': i + 1, 'target': len(symbols)})
                if deadline is not None and time.monotonic() >= deadline:
                    from .universe_account_backend_v2 import SegmentBoundary
                    values._mmap.close()
                    raise SegmentBoundary('UNIVERSE_FEATURE_NEXT_SEGMENT', phase='FEATURES', checkpoint_path=str(binding_path))
            values.flush()
            values._mmap.close()
            del values
            with array_path.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            immutable(manifest_path, {'version': VERSION, 'cache_identity': identity,
                'sha256': digest, 'preparation': self.preparation})
        self.values = np.load(array_path, mmap_mode='r')
        # 与独立重算绑定，不用缓存字节hash作为条件的真实性。
        self.identity = stable_hash({'version': VERSION, 'input_identity': inputs.input_identity,
            'rule_identity': strategy.rule_identity, 'preparation': self.preparation})

    def at(self, symbol, day):
        row = self.values[self.symbol_index[symbol], self.day_index[day]]
        if np.isnan(row[:4]).all():
            value = {'buy': None, 'sell': None, 'market_filter': None, 'ready': False, 'reason': 'NO_COMPLETED_BAR'}
        else:
            value = {k: None if np.isnan(row[i]) else bool(row[i]) for i, k in enumerate(self.columns[:3])}
            value.update(ready=bool(row[3]) if np.isfinite(row[3]) else False, reason='COMPUTED')
        if len(self.columns) > 4:
            value.update(score=float(row[4]) if np.isfinite(row[4]) else None,
                score_ready=bool(row[5]) if np.isfinite(row[5]) else False, condition_ready=value['ready'])
        return value
