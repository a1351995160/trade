"""全范围公共提交扩展：范围由部署登记，策略不能改成少数示例股。"""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import shutil

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from ..research.guard import ResearchDataAccessGuard
from .common import stable_hash
from .exploration_governance import immutable
from .research_data_provider_v1 import day

VERSION = 'FULL_UNIVERSE_SUBMISSION_V1'


def validate_universe_freeze_scopes(config):
    """首次冻结依赖哈希和归档之前，检查已生成大表的物理日期范围。"""
    for item in config['items']:
        options = item.get('backend_options', {})
        if options.get('backend_version') == 'UNIVERSE_ACCOUNT_BACKEND_V1':
            _validate_frozen_item_scope(item, config['input_identity'], options['window'])


def validate_frozen_universe_scopes(job, *, include_archives=False):
    """在公共 runner 或离线审查哈希行情字节之前，先验证物理日期边界。"""
    checked = set()
    for name, plan in job['plans'].items():
        if plan['backend']['backend'] != 'UNIVERSE_ACCOUNT_BACKEND_V1':
            continue
        _validate_frozen_item_scope(job['items'][name], job['input_identity'], plan['backend']['window'],
            source_hashes=job['source_hashes'], archive_root=Path(job['root']) if include_archives else None,
            checked=checked)


def _validate_frozen_item_scope(item, input_identity, window, *, source_hashes=None,
                                archive_root=None, checked=None):
    from .universe_data_provider_v1 import UniverseDataProviderV1
    guard = ResearchDataAccessGuard()
    checked = set() if checked is None else checked
    if item['loader'] != 'chanlun_trader.research_factory.strategy_submission_v1:load_frozen_bundle':
        raise ValueError('UNIVERSE_FROZEN_LOADER_INVALID')
    args = item['loader_kwargs']
    source = Path(args['path']).absolute()
    if source.resolve() != source or not source.is_file():
        raise ValueError('UNIVERSE_FROZEN_SNAPSHOT_REDIRECTED')
    # INPUT 只含结构、来源、公司行动和大表引用；行情在 Parquet 中。
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != args['sha256']:
        raise ValueError('UNIVERSE_FROZEN_SNAPSHOT_CHANGED')
    value = json.loads(raw)
    if (value.get('snapshot_version') != 'UNIVERSE_FROZEN_INPUT_V1'
            or value['input_identity'] != args['input_identity']
            or value['input_identity'] != input_identity or value['window'] != window):
        raise ValueError('UNIVERSE_FROZEN_SCOPE_CONFLICT')
    guard.check_range(window['feature_start'], window['account_end'], 'frozen universe window')
    for event in value['bundle'].get('events', []):
        for key in ('effective_date', 'record_date', 'payment_date', 'share_credit_date', 'tradable_date'):
            if event.get(key) is not None:
                guard.check_range(event[key], event[key], 'frozen universe event')
    if set(value.get('frames', {})) != {'daily', 'turn', 'states'}:
        raise ValueError('UNIVERSE_FROZEN_FRAMES_INVALID')
    for key, info in value['frames'].items():
        columns = info.get('date_columns')
        expected = [['effective_date', 'valid_to'], ['trade_date']] if key == 'states' else [['date']]
        path = Path(info['path']).absolute()
        if (path != source.parent / (key + '.parquet') or path.resolve() != path
                or columns not in expected or info.get('kind') != key.upper()
                or (source_hashes is not None and source_hashes.get(str(path)) != info['sha256'])):
            raise ValueError('UNIVERSE_FROZEN_FRAME_SCOPE_INVALID')
        guard.check_range(info['start'], info['end'], 'universe frozen frame scope')
        paths = [path]
        if archive_root is not None:
            paths.append(archive_root / 'source-archive' / (info['sha256'] + '_' + path.name))
        for physical in paths:
            if physical.resolve() != physical:
                raise ValueError('UNIVERSE_FROZEN_FRAME_REDIRECTED')
            if physical not in checked:
                UniverseDataProviderV1._check_parquet_range(physical, info, guard)
                checked.add(physical)


def preview_universe(service, request):
    from .strategy_submission_v1 import REQUEST_FIELDS, public_rule_factory
    fields = (REQUEST_FIELDS - {'symbols'}) | {'version', 'universe_id'}
    if not isinstance(request, dict) or set(request) != fields or request['version'] != VERSION:
        raise ValueError('UNIVERSE_SUBMISSION_REQUEST_FIELDS_INVALID')
    request = deepcopy(request)
    if (not isinstance(request['dataset_id'], str) or not re.fullmatch(r'[A-Za-z0-9_-]+', request['dataset_id'])
            or not isinstance(request['universe_id'], str) or not request['universe_id']):
        raise ValueError('UNIVERSE_SUBMISSION_REFERENCE_INVALID')
    strategy = public_rule_factory(request['rule'], request['strategy_id'])
    for cost in ('BASE', 'STRESS'):
        public_rule_factory(request['rule'], request['strategy_id'] + '_' + cost)
    if request['purpose'] != 'EXPLORATORY' or request['costs'] != ['BASE', 'STRESS']:
        raise ValueError('UNIVERSE_SUBMISSION_EXPLORATORY_COST_SCENARIOS_REQUIRED')
    if request['benchmark'] not in {'NONE', 'CASH_AND_PRICE_REFERENCE'}:
        raise ValueError('UNIVERSE_SUBMISSION_BENCHMARK_INVALID')
    if type(request['initial_cash']) not in (int, float) or not math.isfinite(request['initial_cash']) or not 0 < request['initial_cash'] < 1e12:
        raise ValueError('SUBMISSION_CASH_INVALID')
    if not isinstance(request['authorization_ref'], str) or not request['authorization_ref']:
        raise ValueError('SUBMISSION_AUTHORIZATION_REFERENCE_REQUIRED')
    for key in ('feature_start', 'account_start', 'account_end'):
        request[key] = day(request[key])
    if not request['feature_start'] < request['account_start'] < request['account_end']:
        raise ValueError('SUBMISSION_WINDOW_INVALID')
    ResearchDataAccessGuard().check_range(request['feature_start'], request['account_end'])
    datasets = {item['dataset_id']: item for item in service.provider.catalog()['datasets']}
    data = datasets.get(request['dataset_id'])
    from .universe_data_provider_v1 import PROVIDER_ADAPTERS
    if data is None or data.get('adapter') not in PROVIDER_ADAPTERS or data.get('universe_id') != request['universe_id']:
        raise ValueError('UNIVERSE_SUBMISSION_DATASET_NOT_REGISTERED')
    symbols = sorted(data['target_symbols'])
    if not symbols or len(set(symbols)) != len(symbols):
        raise ValueError('UNIVERSE_SUBMISSION_TARGET_INVALID')
    if not day(data['start']) <= request['feature_start'] or request['account_end'] > day(data['end']):
        raise ValueError('SUBMISSION_DATA_WINDOW_NOT_COVERED')
    if type(request['max_positions']) is not int or not 1 <= request['max_positions'] <= len(symbols):
        raise ValueError('SUBMISSION_MAX_POSITIONS_INVALID')
    if type(request['max_symbol_exposure_bps']) is not int or not 1 <= request['max_symbol_exposure_bps'] <= 10000:
        raise ValueError('SUBMISSION_EXPOSURE_INVALID')
    request['symbols'] = symbols
    value = {'request': request, 'rule_identity': strategy.rule_identity, 'actual_rule': strategy.definition,
        'data_metadata': data, 'capabilities': service.capabilities(),
        'required_fields': list(strategy.requirements.fields), 'required_warmup_bars': strategy.requirements.warmup_sessions,
        'status': 'PREVIEW_ONLY_CONTENT_AND_AUTHORIZATION_NOT_CHECKED',
        'coverage': {'target_count': len(symbols), 'by_board': data['by_board'],
                     'completeness': data['completeness'], 'data_qualification': 'CONTENT_NOT_VALIDATED'},
        'limitations': ['全部登记目标共用一个账户；本预览不读取行情或授予运行权限。',
            '现金基准和期初等权价格对照分别报告，价格对照不可投资且不参与资格升级。',
            '旧发布验收不覆盖本版本；清单、各板块及真实数据需要各自验收。']}
    return {**value, 'preview_identity': stable_hash(value)}


def _schema_in_columns(frame):
    """完整列推断沿用 Arrow，临时数组使用局部系统池并逐列释放。"""
    fields = []
    pool = pa.system_memory_pool()
    for name, series in frame.items():
        array = pa.array(series, from_pandas=True, memory_pool=pool)
        fields.append(pa.field(str(name), array.type))
        del array
    schema = pa.schema(fields)
    # 完整列类型与原 pandas dtype 构成相同元数据；零行不展开长来源值。
    metadata = pa.Table.from_pandas(frame.iloc[:0], schema=schema,
                                  preserve_index=False).schema.metadata
    return schema.with_metadata({b'pandas': metadata[b'pandas']})


def _write_frame_in_batches(frame, path):
    """按完整列推断类型，逐批写入；不同时展开全部来源长字符串。"""
    schema = _schema_in_columns(frame)
    with pq.ParquetWriter(path, schema) as writer:
        if frame.empty:
            writer.write_table(pa.Table.from_pandas(frame, schema=schema, preserve_index=False))
        for start in range(0, len(frame), 8192):
            table = pa.Table.from_pandas(frame.iloc[start:start + 8192],
                                       schema=schema, preserve_index=False)
            writer.write_table(table)
            del table


def freeze_universe_bundle(prepared, root):
    """大表保存为冻结 Parquet，避免 JSON 字典复制全市场行。"""
    snapshot = {key: deepcopy(value) for key, value in prepared.items() if key != 'bundle'}
    snapshot['snapshot_version'] = 'UNIVERSE_FROZEN_INPUT_V1'
    snapshot['bundle'] = {key: deepcopy(value) for key, value in prepared['bundle'].items()
                          if key not in {'daily', 'turn', 'states'}}
    snapshot['frames'] = {}
    for key in ('daily', 'turn', 'states'):
        frame = prepared['bundle'][key]
        date_columns = (['effective_date', 'valid_to'] if 'effective_date' in frame
                        else ['trade_date'] if key == 'states' else ['date'])
        start = min(int(frame[name].min()) for name in date_columns) if len(frame) else prepared['window']['feature_start']
        end = max(int(frame[name].max()) for name in date_columns) if len(frame) else prepared['window']['account_end']
        ResearchDataAccessGuard().check_range(start, end, 'universe frozen frame')
        path = root / (key + '.parquet')
        if path.exists() or path.resolve() != path:
            raise ValueError('UNIVERSE_FROZEN_FRAME_ALREADY_EXISTS_OR_REDIRECTED')
        _write_frame_in_batches(frame, path)
        info = {'path': str(path), 'rows': len(frame), 'kind': key.upper(),
                'date_columns': date_columns, 'start': start, 'end': end}
        from .universe_data_provider_v1 import UniverseDataProviderV1
        UniverseDataProviderV1._check_parquet_range(path, info, ResearchDataAccessGuard())
        with path.open('rb') as stream:
            info['sha256'] = hashlib.file_digest(stream, 'sha256').hexdigest()
        snapshot['frames'][key] = info
    immutable(root / 'INPUT.json', snapshot)
    return root / 'INPUT.json', [item['path'] for item in snapshot['frames'].values()]


def adopt_frozen_universe_bundle(source, root, *, input_identity, snapshot_sha256):
    """复用受限准备进程的冻结大表；主进程只读元数据并流式复制。"""
    from .universe_data_provider_v1 import UniverseDataProviderV1
    source, root = Path(source).absolute(), Path(root).absolute()
    if source.resolve() != source or root.resolve() != root:
        raise ValueError('UNIVERSE_FROZEN_SNAPSHOT_REDIRECTED')
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != snapshot_sha256:
        raise ValueError('UNIVERSE_FROZEN_SNAPSHOT_CHANGED')
    snapshot = json.loads(raw)
    item = {'loader': 'chanlun_trader.research_factory.strategy_submission_v1:load_frozen_bundle',
        'loader_kwargs': {'path': str(source), 'sha256': snapshot_sha256,
                          'input_identity': input_identity}}
    _validate_frozen_item_scope(item, input_identity, snapshot['window'])
    for key, info in snapshot['frames'].items():
        path = root / (key + '.parquet')
        if path.exists() or path.resolve() != path:
            raise ValueError('UNIVERSE_FROZEN_FRAME_ALREADY_EXISTS_OR_REDIRECTED')
        shutil.copyfile(info['path'], path)
        # 复制之后再检查物理范围，仍先于任何大表内容哈希。
        UniverseDataProviderV1._check_parquet_range(path, info, ResearchDataAccessGuard())
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != info['sha256']:
            raise ValueError('UNIVERSE_FROZEN_FRAME_CHANGED')
        info['path'] = str(path)
    immutable(root / 'INPUT.json', snapshot)
    return snapshot, root / 'INPUT.json', [item['path'] for item in snapshot['frames'].values()]


def restore_universe_bundle(value, input_path):
    from .universe_account_inputs_v1 import universe_input_identity_v1
    from .universe_data_provider_v1 import UniverseDataProviderV1
    guard = ResearchDataAccessGuard()
    window = value['window']
    guard.check_range(window['feature_start'], window['account_end'], 'frozen universe window')
    folder = input_path.parent
    bundle = deepcopy(value['bundle'])
    if set(value.get('frames', {})) != {'daily', 'turn', 'states'}:
        raise ValueError('UNIVERSE_FROZEN_FRAMES_INVALID')
    for key, info in value['frames'].items():
        path = Path(info['path']).absolute()
        if path != folder / (key + '.parquet') or path.resolve() != path:
            raise ValueError('UNIVERSE_FROZEN_FRAME_REDIRECTED')
        guard.check_range(info['start'], info['end'], 'universe frozen frame scope')
        UniverseDataProviderV1._check_parquet_range(path, info, guard)
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != info['sha256']:
            raise ValueError('UNIVERSE_FROZEN_FRAME_CHANGED')
        # 冻结身份包含原 dtype 与缺值；Pandas 3 默认的字符串推断不能改写它们。
        bundle[key] = UniverseDataProviderV1._read_parquet(path, preserve_pandas_objects=True)
        if len(bundle[key]) != info['rows']:
            raise ValueError('UNIVERSE_FROZEN_FRAME_ROWS_CHANGED')
    if universe_input_identity_v1(bundle, value['window']) != value['input_identity']:
        raise ValueError('UNIVERSE_FROZEN_INPUT_CHANGED')
    return {'frame': bundle, 'actions': bundle['events'], 'input_identity': value['input_identity']}
