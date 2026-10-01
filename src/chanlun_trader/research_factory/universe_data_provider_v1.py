"""维护者登记的全范围TDX提供器；策略请求不能指定路径或缩小名单。"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from ..research.guard import ResearchDataAccessGuard
from .research_universe_v1 import ResearchUniverseV1, _day, canonical_symbol, identity
from .tdx_research_adapter_v1 import DAILY_FIELDS, TdxResearchAdapterV1, merge_daily_sources, read_tdx_day_window


PROVIDER_VERSION = 'TDX_FULL_UNIVERSE_V1'
_KINDS = {'DAILY', 'REFERENCE_PRICES', 'STATES', 'EVENTS', 'CORPORATE_ACTION_COVERAGE',
          'CALENDAR', 'SOURCE_QUALIFICATION'}
_FORBIDDEN = ('label', 'return', 'factor_table')


class UniverseDataProviderV1:
    """只支持探索性研究；内容哈希与登记不代表独立验证或正式资格。"""

    def __init__(self, registered_roots: dict, access_recorder=None):
        self.record = access_recorder or (lambda event: None)
        self.roots, self._datasets = {}, {}
        self._metadata_paths = {}
        for key, value in registered_roots.items():
            path = Path(value).absolute()
            if path.resolve() != path or not path.is_dir():
                raise ValueError('DATA_ROOT_INVALID')
            self.roots[key] = path

    @staticmethod
    def _path(root: Path, relative: str) -> Path:
        if not isinstance(relative, str):
            raise ValueError('DATA_PATH_OUTSIDE_ROOT')
        value = Path(relative)
        windows_value = PureWindowsPath(relative)
        if value.anchor or windows_value.anchor or '..' in value.parts or '..' in windows_value.parts:
            raise ValueError('DATA_PATH_OUTSIDE_ROOT')
        path = root / value
        if not path.is_relative_to(root):
            raise ValueError('DATA_PATH_OUTSIDE_ROOT')
        if path.resolve() != path or not path.is_file():
            raise ValueError('DATA_PATH_INVALID_OR_REDIRECTED')
        return path

    def register(self, dataset_id: str, root_id: str, manifest_path: str):
        self.register_manifest(dataset_id, root_id, self._path(self.roots[root_id], manifest_path))

    def register_manifest(self, dataset_id: str, root_id: str, manifest_path):
        if dataset_id in self._datasets or not isinstance(dataset_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', dataset_id):
            raise ValueError('DATASET_ID_INVALID_OR_DUPLICATE')
        if root_id not in self.roots:
            raise ValueError('DATA_ROOT_NOT_REGISTERED')
        metadata = Path(manifest_path).absolute()
        if metadata.resolve() != metadata or not metadata.is_file():
            raise ValueError('DATA_MANIFEST_PATH_INVALID_OR_REDIRECTED')
        raw = metadata.read_bytes()
        manifest = json.loads(raw.decode('utf-8-sig'))
        if manifest.get('adapter') != PROVIDER_VERSION:
            raise ValueError('DATA_ADAPTER_UNSUPPORTED')
        files, master = manifest.get('files'), manifest.get('master')
        if (not isinstance(files, dict) or not files or not isinstance(master, dict)
                or not manifest.get('universe_id')
                or _day(manifest.get('start')) is None or _day(manifest.get('end')) is None
                or _day(manifest['start']) > _day(manifest['end'])):
            raise ValueError('DATA_MANIFEST_INVALID')
        cache_records = []
        for name, item in files.items():
            self._path(self.roots[root_id], name)
            if any(word in name.lower() for word in _FORBIDDEN):
                raise ValueError('DATA_OUTCOME_SOURCE_FORBIDDEN')
            if (not isinstance(item, dict) or item.get('kind') not in _KINDS
                    or item.get('format') not in {'PARQUET', 'JSON', 'TDX_DAY_WINDOW'}
                    or not re.fullmatch(r'[0-9a-f]{64}', item.get('sha256', ''))
                    or not item.get('source_id')
                    or _day(item.get('start')) is None or _day(item.get('end')) is None
                    or _day(item['start']) > _day(item['end'])):
                raise ValueError('DATA_SOURCE_IDENTITY_INVALID')
            if item['kind'] == 'DAILY':
                cached_symbols = item.get('symbols') or ([item['symbol']] if item.get('symbol') else None)
                if not isinstance(cached_symbols, list) or not cached_symbols:
                    raise ValueError('DATA_CACHE_SYMBOL_METADATA_REQUIRED')
                for symbol in cached_symbols:
                    cache_records.append({'symbol': symbol,
                        'source_sha256': item['sha256'], 'start': item['start'], 'end': item['end']})
        universe = ResearchUniverseV1(master.get('records', []), cache_records,
            master_source=master.get('source'), completeness_evidence=master.get('completeness_evidence'))
        if not universe.target_symbols:
            raise ValueError('DATA_UNIVERSE_EMPTY')
        scope = manifest.get('universe_scope')
        if scope is not None and (not isinstance(scope, dict)
                or _day(scope.get('start')) is None or _day(scope.get('end')) is None
                or _day(scope['start']) > _day(scope['end'])):
            raise ValueError('DATA_UNIVERSE_SCOPE_INVALID')
        self._datasets[dataset_id] = (self.roots[root_id], deepcopy(manifest),
                                     hashlib.sha256(raw).hexdigest(), universe)
        self._metadata_paths[dataset_id] = metadata

    def catalog(self):
        rows = []
        for key, (_, manifest, digest, universe) in sorted(self._datasets.items()):
            snapshot = universe.snapshot()
            rows.append({'dataset_id': key, 'universe_id': manifest['universe_id'],
                'symbols': snapshot['target_symbols'], 'target_symbols': snapshot['target_symbols'],
                'target_count': snapshot['target_count'], 'by_board': snapshot['by_board'],
                'completeness': snapshot['completeness'], 'universe_identity': universe.universe_identity,
                'universe_scope': deepcopy(manifest.get('universe_scope')),
                'start': manifest['start'], 'end': manifest['end'], 'adapter': PROVIDER_VERSION,
                'metadata_hash': digest, 'historical_independence': 'UNKNOWN',
                'independent_confirmation_eligible': False, 'data_qualification': 'CONTENT_NOT_VALIDATED',
                'limitations': ['历史清单、状态、公司行动与单位需要分别核验；登记不代表账户可执行。']})
        return {'schema_version': PROVIDER_VERSION, 'content_read': False, 'datasets': rows}

    def prepare(self, dataset_id, *, feature_start, account_start, account_end,
                purpose='EXPLORATORY', required_fields=(), authorization=None,
                stage='ACCOUNT', symbols=None, universe_id=None):
        prepared, _ = self._prepare_with_inputs(dataset_id,
            feature_start=feature_start, account_start=account_start, account_end=account_end,
            purpose=purpose, required_fields=required_fields, normalization_fields=required_fields,
            authorization=authorization, stage=stage, symbols=symbols, universe_id=universe_id)
        return prepared

    def _prepare_with_inputs(self, dataset_id, *, feature_start, account_start, account_end,
                             purpose='EXPLORATORY', required_fields=(), normalization_fields=(),
                             warmup_bars=0, authorization=None, stage='ACCOUNT', symbols=None,
                             universe_id=None):
        """受信 worker 复用本次验证对象；不把对象或缓存加入公开 prepared 字典。

        normalization_fields 保留原件规范化的硬校验；required_fields/warmup_bars
        在首次输入认证中检查实际策略，SCAN 的可选字段缺口仍保持 UNKNOWN。
        """
        if purpose != 'EXPLORATORY':
            raise ValueError('DATA_PURPOSE_NOT_QUALIFIED')
        if not isinstance(dataset_id, str) or dataset_id not in self._datasets:
            raise ValueError('DATASET_NOT_REGISTERED')
        for fields in (required_fields, normalization_fields):
            if (not isinstance(fields, (list, tuple, set))
                    or not all(isinstance(field, str) for field in fields)
                    or not set(fields) <= set(DAILY_FIELDS)):
                raise ValueError('DATA_FIELD_UNSUPPORTED')
        if type(warmup_bars) is not int or warmup_bars < 0:
            raise ValueError('UNIVERSE_REQUIRED_FIELDS_INVALID')
        root, manifest, manifest_hash, universe = self._datasets[dataset_id]
        target = universe.target_symbols
        if symbols is not None and (not isinstance(symbols, (list, tuple))
                or len(symbols) != len(target) or set(symbols) != set(target)):
            raise ValueError('DATA_FULL_UNIVERSE_REQUIRED')
        if universe_id is not None and universe_id != manifest['universe_id']:
            raise ValueError('DATA_UNIVERSE_IDENTITY_MISMATCH')
        if stage not in {'ACCOUNT', 'SIGNAL', 'SCAN'}:
            raise ValueError('DATA_STAGE_UNSUPPORTED')
        start, account, end = map(_day, (feature_start, account_start, account_end))
        if any(value is None for value in (start, account, end)) or not start < account < end:
            raise ValueError('DATA_WINDOW_OR_WARMUP_INVALID')
        guard = ResearchDataAccessGuard()
        guard.check_range(start, end, 'full universe request')
        if (not isinstance(authorization, dict) or authorization.get('purpose') != purpose
                or dataset_id not in authorization.get('dataset_ids', [])
                or not authorization.get('authorization_id')
                or not authorization.get('start') or not authorization.get('end')
                or start < _day(authorization['start']) or end > _day(authorization['end'])):
            raise ValueError('DATA_ACCESS_NOT_AUTHORIZED')
        if start < _day(manifest['start']) or end > _day(manifest['end']):
            raise ValueError('DATA_WINDOW_NOT_COVERED')
        scope = manifest.get('universe_scope')
        if scope is not None and (account < _day(scope['start']) or end > _day(scope['end'])):
            raise ValueError('DATA_HISTORICAL_UNIVERSE_SCOPE_NOT_COVERED')
        states_by_symbol = {row['symbol']: row for row in universe.snapshot()['securities']}
        if any(states_by_symbol[s]['status'] in {'BOARD_UNKNOWN', 'BOARD_CONFLICT'} for s in target):
            raise ValueError('DATA_UNIVERSE_BOARD_NOT_QUALIFIED')
        frames, references, states, events, coverage, calendars, hashes, source_identities = [], [], [], [], [], [], {}, []
        calendar_sources = []
        source_qualification = None
        # 先逐文件检查全物理范围；一个缺授权时不先消费其它报价文件。
        for name, metadata in manifest['files'].items():
            lo, hi = _day(metadata['start']), _day(metadata['end'])
            guard.check_range(lo, hi, 'full universe whole source')
            if lo < _day(authorization['start']) or hi > _day(authorization['end']):
                raise ValueError('DATA_WHOLE_SOURCE_NOT_AUTHORIZED')
            if metadata['format'] == 'PARQUET':
                self._check_parquet_range(self._path(root, name), metadata, guard)
        for name, metadata in manifest['files'].items():
            value = self._read(root, name, metadata, dataset_id, authorization)
            digest = metadata['sha256']
            if metadata['source_id'] in hashes and hashes[metadata['source_id']] != digest:
                raise ValueError('DATA_SOURCE_IDENTITY_CONFLICT')
            hashes[metadata['source_id']] = digest
            hashes[name] = digest
            kind = metadata['kind']
            if kind == 'DAILY':
                if not isinstance(value, pd.DataFrame):
                    raise ValueError('DATA_DAILY_FORMAT_INVALID')
                evidence = deepcopy(metadata.get('evidence', {}))
                if evidence.get('source_sha256') != digest:
                    raise ValueError('TDX_SOURCE_IDENTITY_CONFLICT')
                if not {'symbol', 'date'} <= set(value):
                    raise ValueError('TDX_DAILY_FIELDS_MISSING')
                # 原件已通过整文件范围/授权/SHA；先验证完整日期轴，再缩短计算视图。
                try:
                    raw_days = value.date.unique()
                    numeric_days = pd.to_numeric(pd.Series(raw_days), errors='raise')
                    normalized_days = {}
                    for original, number in zip(raw_days, numeric_days):
                        integer = int(number)
                        if integer != number:
                            raise ValueError()
                        normalized_days[original] = _day(integer)
                except (ValueError, TypeError, OverflowError) as exc:
                    raise ValueError('TDX_DATE_INVALID') from exc
                guard.check_int_iterable(normalized_days.values(), 'full universe original daily date axis')
                value['date'] = value.date.map(normalized_days)
                value['symbol'] = value.symbol.map({s: canonical_symbol(s) for s in value.symbol.unique()})
                columns = ['symbol', 'date'] + [field for field in DAILY_FIELDS if field in value]
                value = value.loc[value.symbol.isin(target) & value.date.between(start, end)]
                value = value[columns]
                if value.empty:
                    del value
                    continue
                result = TdxResearchAdapterV1().normalize_daily(value, evidence=evidence,
                    required_fields=[f for f in normalization_fields if f != 'prev_close'])
                frames.append(result['daily'])
                source_identities.append(result['source_identity'])
                # normalize_daily也返回turn副本；本入口只保留daily，最终统一构造turn。
                del result
            elif kind == 'REFERENCE_PRICES':
                references.append(value)
            elif kind == 'STATES':
                if isinstance(value, pd.DataFrame) and 'symbol' in value:
                    value['symbol'] = value.symbol.map({s: canonical_symbol(s) for s in value.symbol.unique()})
                    selected = value.symbol.isin(target)
                    if 'trade_date' in value:
                        state_days = {d: _day(d) for d in value.trade_date.unique()}
                        if any(d is None for d in state_days.values()):
                            raise ValueError('UNIVERSE_DATE_INVALID')
                        guard.check_int_iterable(state_days.values(), 'full universe original state date axis')
                        value['trade_date'] = value.trade_date.map(state_days)
                        selected &= value.trade_date.between(start, end)
                    elif {'effective_date', 'valid_to'} <= set(value):
                        for column in ('effective_date', 'valid_to'):
                            state_days = {d: _day(d) for d in value[column].unique()}
                            if any(d is None for d in state_days.values()):
                                raise ValueError('UNIVERSE_DATE_INVALID')
                            guard.check_int_iterable(state_days.values(), 'full universe original state interval axis')
                            value[column] = value[column].map(state_days)
                        selected &= value.effective_date.le(end) & value.valid_to.ge(start)
                    if not selected.all():
                        value = value.loc[selected]
                    del selected
                states.append(value)
            elif kind == 'EVENTS':
                events.extend(value.to_dict('records') if isinstance(value, pd.DataFrame) else value)
            elif kind == 'CORPORATE_ACTION_COVERAGE':
                coverage.extend(value.to_dict('records') if isinstance(value, pd.DataFrame) else value)
            elif kind == 'CALENDAR':
                calendar_sources.append(metadata['source_id'])
                if isinstance(value, pd.DataFrame):
                    calendars.extend(value['date'].tolist())
                else:
                    calendars.extend(value)
            elif kind == 'SOURCE_QUALIFICATION':
                rows = value.to_dict('records') if isinstance(value, pd.DataFrame) else value
                if not isinstance(rows, list) or any(not isinstance(row, dict) or 'symbol' not in row for row in rows):
                    raise ValueError('DATA_SOURCE_QUALIFICATION_INVALID')
                mapped = {canonical_symbol(row['symbol']): deepcopy(row) for row in rows}
                if len(mapped) != len(rows) or set(mapped) != set(target) or source_qualification is not None:
                    raise ValueError('DATA_SOURCE_QUALIFICATION_TARGET_MISMATCH')
                source_qualification = mapped
                del rows, mapped
            del value
        daily = frames.pop() if len(frames) == 1 else merge_daily_sources(frames) if frames else pd.DataFrame(
            columns=['symbol', 'date', *[field for field in DAILY_FIELDS if field != 'turn']])
        frames.clear()
        if references:
            reference = pd.concat(references, ignore_index=True)
            if (not {'symbol', 'date', 'prev_close', 'source'} <= set(reference)
                    or reference.duplicated(['symbol', 'date']).any()
                    or not reference.source.isin(hashes).all()):
                raise ValueError('DATA_REFERENCE_SOURCE_INVALID')
            reference = reference.rename(columns={'prev_close': 'source_prev_close'})
            daily = daily.merge(reference[['symbol', 'date', 'source_prev_close']],
                                how='left', on=['symbol', 'date'], validate='one_to_one')
            known = daily.prev_close.notna() & daily.source_prev_close.notna()
            if (daily.loc[known, 'prev_close'] != daily.loc[known, 'source_prev_close']).any():
                raise ValueError('DATA_REFERENCE_SOURCE_CONFLICT')
            daily['prev_close'] = daily.source_prev_close.combine_first(daily.prev_close)
            daily = daily.drop(columns='source_prev_close')
            references.clear()
            del reference, known
        dates = [_day(d) for d in calendars]
        if not dates or dates != sorted(set(dates)):
            raise ValueError('DATA_CALENDAR_INVALID')
        guard.check_int_iterable(dates, 'full universe calendar')
        calendar = [d for d in dates if start <= d <= end]
        if (not calendar or calendar[0] != start or calendar[-1] != end or account not in calendar):
            raise ValueError('DATA_WINDOW_OR_WARMUP_INVALID')
        selected = daily.date.isin(calendar)
        if not selected.all():
            daily = daily.loc[selected]
        daily.reset_index(drop=True, inplace=True)
        del selected
        state_frame = states.pop() if len(states) == 1 else pd.concat(states, ignore_index=True) if states else pd.DataFrame(columns=['symbol', 'trade_date'])
        states.clear()
        if 'symbol' in state_frame:
            selected = state_frame.symbol.isin(target)
            if not selected.all():
                state_frame = state_frame.loc[selected]
            del selected
        if 'trade_date' in state_frame:
            selected = state_frame.trade_date.isin(calendar)
            if not selected.all():
                state_frame = state_frame.loc[selected]
            del selected
        elif {'effective_date', 'valid_to'} <= set(state_frame):
            selected = state_frame.effective_date.le(end) & state_frame.valid_to.ge(start)
            if not selected.all():
                state_frame = state_frame.loc[selected]
            del selected
        state_frame.reset_index(drop=True, inplace=True)
        window = {'symbols': target, 'feature_start': start, 'account_start': account,
                  'account_end': end, 'calendar': calendar}
        bundle = {'profile': 'HISTORICAL_MODELED', 'daily': daily,
            'turn': daily[['symbol', 'date', 'volume'] + (['turn'] if 'turn' in daily else [])].copy(),
            'states': state_frame,
            'events': events, 'calendar': calendar, 'corporate_action_coverage': coverage,
            'source_hashes': hashes, 'universe_identity': universe.universe_identity,
            'source_identity': identity({'manifest_hash': manifest_hash, 'sources': source_identities,
                                         'source_hashes': hashes}),
            'board_policy_identity': manifest.get('board_policy_identity'),
            'universe_snapshot': universe.snapshot(),
            'calendar_source': calendar_sources[0] if len(calendar_sources) == 1 else 'CALENDAR_SOURCE_SET',
            'price_basis': {'execution': 'RAW'}}
        if len(calendar_sources) > 1:
            hashes['CALENDAR_SOURCE_SET'] = identity({key: hashes[key] for key in calendar_sources})
        for key in ('listing_dates', 'listing_date_sources', 'board_policy_evidence'):
            if key in manifest:
                bundle[key] = deepcopy(manifest[key])
        if isinstance(manifest.get('corporate_actions_complete'), bool):
            bundle['corporate_actions_complete'] = manifest['corporate_actions_complete']
        if source_qualification is not None:
            bundle['source_qualification'] = source_qualification
        # 动态输入认证负责状态、参考价、公司行动、稀疏日历与板块政策；不走旧矩形V2。
        from .universe_account_inputs_v1 import prepare_universe_account_inputs_v1
        inputs = prepare_universe_account_inputs_v1(bundle, window, stage=stage,
            required_fields=required_fields, warmup_bars=warmup_bars)
        prepared_bundle = inputs.bundle
        qualification = {'purpose': purpose, 'account_data_ready': inputs.coverage.get('account_data_ready') is True,
            'data_stage': stage, 'historical_availability': 'MODELED',
            'historical_independence': 'UNKNOWN', 'independent_confirmation_eligible': False,
            'strategy_qualified': False, 'manifest_hash': manifest_hash,
            'source_hashes': hashes, 'coverage': inputs.coverage,
            'universe_completeness': universe.snapshot()['completeness'],
            'price_basis': {'execution': 'RAW', 'indicators': 'CAUSAL_ACTION_TRANSFORM_REQUIRED'},
            'limitations': ['数据通过不代表策略有效；历史可见性未取得独立发布证据。']}
        self.record({'event': 'DATA_BUNDLE_PREPARED', 'dataset_id': dataset_id,
            'authorization_id': authorization['authorization_id'], 'purpose': purpose,
            'input_identity': inputs.input_identity, 'source_hashes': hashes, 'window': window})
        prepared = {'window': window, 'bundle': prepared_bundle, 'qualification': qualification,
                    'input_identity': inputs.input_identity}
        return prepared, inputs

    @staticmethod
    def _check_parquet_range(path: Path, metadata: dict, guard: ResearchDataAccessGuard):
        """只读footer日期统计，在OHLC内容和全文件哈希之前检查真实范围。"""
        footer = pq.ParquetFile(path).metadata
        date_columns = metadata.get('date_columns') or [metadata.get('date_column',
            'trade_date' if metadata['kind'] == 'STATES' else 'date')]
        bounds = []
        for group_number in range(footer.num_row_groups):
            group = footer.row_group(group_number)
            columns = {group.column(i).path_in_schema: group.column(i) for i in range(group.num_columns)}
            for name in date_columns:
                if name not in columns:
                    raise ValueError('DATA_PHYSICAL_DATE_METADATA_MISSING')
                statistics = columns[name].statistics
                if group.num_rows and (statistics is None or not statistics.has_min_max):
                    raise ValueError('DATA_PHYSICAL_DATE_METADATA_MISSING')
                if group.num_rows:
                    bounds.extend([_day(statistics.min), _day(statistics.max)])
        if bounds:
            lo, hi = min(bounds), max(bounds)
            guard.check_range(lo, hi, 'full universe parquet physical range')
            if lo < _day(metadata['start']) or hi > _day(metadata['end']):
                raise ValueError('DATA_PHYSICAL_RANGE_CONFLICT')

    def _read(self, root, name, metadata, dataset_id, authorization):
        path = self._path(root, name)
        self.record({'event': 'DATA_CONTENT_READ_ATTEMPT', 'dataset_id': dataset_id,
            'source': name, 'expected_sha256': metadata['sha256'], 'purpose': authorization['purpose'],
            'authorization_id': authorization['authorization_id'], 'start': _day(metadata['start']),
            'end': _day(metadata['end']), 'format': metadata['format']})
        before = path.stat()
        if metadata['format'] == 'TDX_DAY_WINDOW':
            sessions = metadata.get('sessions')
            if (metadata['kind'] != 'DAILY' or not metadata.get('symbol') or not sessions
                    or any(not _day(metadata['start']) <= _day(d) <= _day(metadata['end']) for d in sessions)):
                raise ValueError('TDX_DAY_WINDOW_INVALID')
            rows, evidence = read_tdx_day_window(path, sessions)
            if evidence['window_sha256'] != metadata['sha256']:
                raise ValueError('DATA_SOURCE_CONTENT_CHANGED:' + name)
            value = pd.DataFrame(rows).rename(columns={'volume_encoded': 'volume', 'amount_encoded': 'amount'})
            value['symbol'] = metadata['symbol']
            return value
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != metadata['sha256']:
            raise ValueError('DATA_SOURCE_CONTENT_CHANGED:' + name)
        if metadata['format'] == 'PARQUET':
            # 保留原件声明的 object/None；原生 Arrow 字符串共享对象表示，避免重复展开长来源字段。
            value = self._read_parquet(path, preserve_pandas_objects=True)
        else:
            value = json.loads(path.read_text(encoding='utf-8-sig'))
            if not isinstance(value, list):
                raise ValueError('DATA_NORMALIZED_JSON_LIST_REQUIRED')
            if metadata['kind'] in {'DAILY', 'STATES', 'REFERENCE_PRICES'}:
                value = pd.DataFrame(value)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError('DATA_SOURCE_CHANGED_DURING_READ')
        return value

    @staticmethod
    def _read_parquet(path, *, preserve_pandas_objects=False):
        """逐批展开原件；保真模式保留 object 及原生 Arrow 字符串的值和空值。"""
        parquet = pq.ParquetFile(path)
        parts, strings = [], {}
        source_pandas_metadata = parquet.schema_arrow.pandas_metadata
        pandas_metadata = source_pandas_metadata or {}
        object_columns = {column.get('field_name', column.get('name'))
                          for column in pandas_metadata.get('columns', [])
                          if column.get('numpy_type') == 'object'}
        if source_pandas_metadata is None:
            # 原生 Arrow 没有声明 Pandas dtype；字符串用共享 Python str/None 保留其语义。
            # 已声明的 str/string 扩展列仍遵循 Pandas 元数据，不转成 object。
            object_columns.update(field.name for field in parquet.schema_arrow
                                  if pa.types.is_string(field.type) or pa.types.is_large_string(field.type))

        def to_frame(batch):
            frame = batch.to_pandas(deduplicate_objects=True, use_threads=False)
            if preserve_pandas_objects:
                for name in object_columns.intersection(frame.columns):
                    # Pandas 3 标准恢复会把 object 字符串升级为 str 并把 None 转为 nan。
                    # 对象以原 Arrow 空值/结构重建，仍只展开当前批次。
                    values = batch.column(batch.schema.get_field_index(name)).to_pylist()
                    frame[name] = pd.Series(values, index=frame.index, dtype='object')
            for name in frame:
                dtype = frame[name].dtype
                if not (pd.api.types.is_object_dtype(dtype)
                        or isinstance(dtype, pd.StringDtype) and dtype.storage == 'python'):
                    continue
                values = frame[name].to_numpy(copy=True)
                for i, value in enumerate(values):
                    if isinstance(value, str):
                        values[i] = strings.setdefault(value, value)
                # 保留本批恢复的 dtype；Arrow 字符串扩展数组不转成 object。
                frame[name] = pd.Series(values, index=frame.index, dtype=dtype)
            return frame

        for batch in parquet.iter_batches(batch_size=8192, use_threads=False):
            parts.append(to_frame(batch))
            del batch
        if not parts:
            return to_frame(pa.Table.from_batches([], schema=parquet.schema_arrow))
        if len(parts) == 1:
            return parts[0]
        index_columns = pandas_metadata.get('index_columns', [])
        result = pd.concat(parts, ignore_index=not any(isinstance(column, str) for column in index_columns))
        # RangeIndex 只在元数据中保存；每批转换会产生局部 RangeIndex，合并后恢复原轴。
        if len(index_columns) == 1 and isinstance(index_columns[0], dict) and index_columns[0].get('kind') == 'range':
            index = index_columns[0]
            result.index = pd.RangeIndex(index['start'], index['stop'], index['step'], name=index.get('name'))
        return result
