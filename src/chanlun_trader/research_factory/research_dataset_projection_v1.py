"""可信准备用途下的 TRAIN 物理投影；不授予历史独立性或账户资格。"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from ..research.guard import ResearchDataAccessGuard, TrustedResearchDataAccessAuthorityV1
from .research_universe_v1 import _day, canonical_symbol, identity
from .universe_data_provider_v1 import PROVIDER_VERSION, UniverseDataProviderV1


VERSION = 'RESEARCH_DATASET_PROJECTION_V1'
PURPOSE = 'DATASET_TRAIN_PROJECTION'


def _sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _write(path, value):
    Path(path).write_text(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False), encoding='utf-8')


def _date_columns(metadata, columns):
    if metadata.get('date_columns'):
        return metadata['date_columns']
    if metadata.get('date_column'):
        return [metadata['date_column']]
    if metadata['kind'] == 'STATES':
        return ['effective_date', 'valid_to'] if 'effective_date' in columns else ['trade_date']
    return ['date']


def _parquet_bounds(path, metadata):
    parquet = pq.ParquetFile(path)
    names = _date_columns(metadata, parquet.schema_arrow.names)
    bounds = []
    for index in range(parquet.metadata.num_row_groups):
        group = parquet.metadata.row_group(index)
        columns = {group.column(i).path_in_schema: group.column(i)
                   for i in range(group.num_columns)}
        for name in names:
            if name not in columns:
                raise ValueError('PROJECTION_PHYSICAL_DATE_METADATA_MISSING')
            statistics = columns[name].statistics
            if group.num_rows:
                if statistics is None or not statistics.has_min_max:
                    raise ValueError('PROJECTION_PHYSICAL_DATE_METADATA_MISSING')
                bounds.extend((_day(statistics.min), _day(statistics.max)))
    return (min(bounds), max(bounds)) if bounds else (None, None)


def _date_literal(day, arrow_type):
    if pa.types.is_string(arrow_type) or pa.types.is_large_string(arrow_type):
        text = str(day)
        return f'{text[:4]}-{text[4:6]}-{text[6:]}'
    if not pa.types.is_integer(arrow_type):
        raise ValueError('PROJECTION_DATE_TYPE_UNSUPPORTED')
    return day


def _project_parquet(path, metadata, start, end):
    source = ds.dataset(path, format='parquet')
    names = _date_columns(metadata, source.schema.names)
    interval = metadata['kind'] == 'STATES' and names == ['effective_date', 'valid_to']
    if metadata['kind'] not in {'DAILY', 'REFERENCE_PRICES', 'STATES'}:
        raise ValueError('PROJECTION_PARQUET_KIND_UNSUPPORTED')
    if interval:
        lower = _date_literal(start, source.schema.field('valid_to').type)
        upper = _date_literal(end, source.schema.field('effective_date').type)
        predicate = (ds.field('effective_date') <= upper) & (ds.field('valid_to') >= lower)
    else:
        if len(names) != 1:
            raise ValueError('PROJECTION_DATE_AXIS_UNSUPPORTED')
        name = names[0]
        lower, upper = (_date_literal(day, source.schema.field(name).type) for day in (start, end))
        predicate = (ds.field(name) >= lower) & (ds.field(name) <= upper)
    table = source.to_table(filter=predicate)
    if interval:
        for name, bound, comparator in (('effective_date', start, pc.less), ('valid_to', end, pc.greater)):
            column = table[name]
            limit = pa.scalar(_date_literal(bound, column.type), type=column.type)
            clipped = pc.if_else(comparator(column, limit), limit, column)
            table = table.set_column(table.schema.get_field_index(name), name, clipped)
    return table, names


def _row_dates(row, kind):
    if kind in {'DAILY', 'REFERENCE_PRICES'}:
        keys = ['date']
    elif kind == 'STATES':
        keys = ['effective_date', 'valid_to'] if 'effective_date' in row else ['trade_date']
    elif kind == 'EVENTS':
        keys = ['record_date', 'effective_date'] + [key for key in
                ('payment_date', 'share_credit_date', 'tradable_date') if row.get(key) is not None]
    elif kind == 'CORPORATE_ACTION_COVERAGE':
        keys = ['start', 'end']
    else:
        return []
    days = [_day(row[key]) for key in keys]
    if any(day is None for day in days):
        raise ValueError('PROJECTION_SOURCE_DATE_REQUIRED')
    return days


def _project_json(rows, kind, start, end, symbols):
    if not isinstance(rows, list):
        raise ValueError('PROJECTION_JSON_LIST_REQUIRED')
    if kind == 'CALENDAR':
        days = [_day(day) for day in rows]
        if any(day is None for day in days) or len(set(days)) != len(days):
            raise ValueError('PROJECTION_CALENDAR_INVALID')
        return sorted(day for day in days if start <= day <= end), days
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError('PROJECTION_JSON_ROWS_INVALID')
    if kind == 'SOURCE_QUALIFICATION':
        mapped = [canonical_symbol(row['symbol']) for row in rows]
        if len(set(mapped)) != len(mapped) or set(mapped) != set(symbols):
            raise ValueError('PROJECTION_QUALIFICATION_DENOMINATOR_CONFLICT')
        return deepcopy(rows), []
    result, physical = [], []
    for original in rows:
        row = deepcopy(original)
        days = _row_dates(row, kind)
        physical.extend(days)
        if not days:
            raise ValueError('PROJECTION_JSON_KIND_UNSUPPORTED')
        if kind in {'EVENTS', 'CORPORATE_ACTION_COVERAGE'} or kind == 'STATES' and 'effective_date' in row:
            if min(days) > end or max(days) < start:
                continue
            if kind == 'EVENTS':
                # 保留跨窗权利条款，但不可把未来才发布的行动当成 TRAIN 已知事实。
                for name in ('source_published_at', 'available_at'):
                    if row.get(name) and _day(str(row[name])[:10]) > end:
                        raise ValueError('PROJECTION_EVENT_NOT_AVAILABLE_IN_TRAIN')
                ResearchDataAccessGuard().check_int_iterable(days, 'TRAIN event obligation metadata')
            else:
                left, right = ('effective_date', 'valid_to') if kind == 'STATES' else ('start', 'end')
                row[left], row[right] = max(start, days[0]), min(end, days[1])
        elif not start <= days[0] <= end:
            continue
        result.append(row)
    return result, physical


def validate_projection_registration(root, manifest, manifest_sha256):
    """登记时核对本地回执；普通探索只打开子资料及此元信息。"""
    binding = manifest.get('projection')
    if binding is None:
        return
    if (not isinstance(binding, dict) or binding.get('version') != VERSION
            or binding.get('purpose') != PURPOSE
            or not isinstance(binding.get('projection_id'), str)):
        raise ValueError('PROJECTION_REGISTRATION_INVALID')
    receipt_path = UniverseDataProviderV1._path(root, binding.get('receipt_path'))
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    if (receipt.get('version') != VERSION
            or receipt.get('projection_id') != binding['projection_id']
            or receipt.get('manifest_sha256') != manifest_sha256
            or receipt.get('receipt_id') != identity({key: value for key, value in receipt.items()
                                                     if key != 'receipt_id'})
            or receipt.get('parent_manifest_sha256') != binding.get('parent_manifest_sha256')
            or receipt.get('output_start') != manifest['start']
            or receipt.get('output_end') != manifest['end']):
        raise ValueError('PROJECTION_RECEIPT_BINDING_CONFLICT')
    expected = {row['child_name']: row['child_sha256'] for row in receipt.get('sources', [])}
    if expected != {name: row['sha256'] for name, row in manifest['files'].items()}:
        raise ValueError('PROJECTION_CHILD_IDENTITY_CONFLICT')


class ResearchDatasetProjectionV1:
    """只由可信准备服务使用；父资料永不修改，普通探索不回读父原件。"""

    def __init__(self, provider, trusted_access_authority, access_recorder=None):
        if not isinstance(trusted_access_authority, TrustedResearchDataAccessAuthorityV1):
            raise ValueError('PROJECTION_TRUSTED_AUTHORITY_REQUIRED')
        self.provider, self.authority = provider, trusted_access_authority
        self.record = access_recorder or (lambda event: None)

    def project(self, dataset_id, *, output_root, train_start, train_end,
                authorization_ref, account_start=None):
        start, end = _day(train_start), _day(train_end)
        if start is None or end is None or start >= end:
            raise ValueError('PROJECTION_OUTPUT_RANGE_INVALID')
        ResearchDataAccessGuard().check_range(start, end, 'TRAIN projection output')
        if dataset_id not in self.provider._datasets:
            raise ValueError('DATASET_NOT_REGISTERED')
        root, original, manifest_hash, universe = self.provider._datasets[dataset_id]
        if original['adapter'] != PROVIDER_VERSION:
            raise ValueError('PROJECTION_ADAPTER_UNSUPPORTED')
        manifest = deepcopy(original)
        if start < _day(manifest['start']) or end > _day(manifest['end']):
            raise ValueError('PROJECTION_OUTPUT_NOT_COVERED')
        if account_start is not None:
            account_start = _day(account_start)
            if account_start is None or not start < account_start <= end:
                raise ValueError('PROJECTION_ACCOUNT_RANGE_INVALID')
        output = Path(output_root).absolute()
        if (output.resolve() != output or output.exists() or output.is_relative_to(root)
                or root.is_relative_to(output) or not output.parent.is_dir()):
            raise ValueError('PROJECTION_OUTPUT_PATH_INVALID')
        sources = {name: {'sha256': item['sha256'], 'physical_start': _day(item['start']),
                          'physical_end': _day(item['end'])}
                   for name, item in manifest['files'].items()}
        scope = self.authority.authorize(authorization_ref, purpose=PURPOSE,
            dataset_id=dataset_id, manifest_sha256=manifest_hash, sources=sources,
            output_start=start, output_end=end, recipe_version=VERSION)
        binding = scope.binding
        # 哈希本身是整文件物理读取；全部来源都获准后才能执行任何文件哈希。
        plans, input_bytes = [], 0
        for name, item in manifest['files'].items():
            if item['format'] not in {'PARQUET', 'JSON'}:
                raise ValueError('PROJECTION_SOURCE_FORMAT_UNSUPPORTED')
            path = UniverseDataProviderV1._path(root, name)
            guard = scope.guard(purpose=PURPOSE, dataset_id=dataset_id,
                                manifest_sha256=manifest_hash, source_name=name)
            guard.check_range(_day(item['start']), _day(item['end']), 'declared whole source')
            physical = _parquet_bounds(path, item) if item['format'] == 'PARQUET' else (None, None)
            if physical[0] is not None:
                guard.check_range(*physical, 'actual whole source')
                if physical[0] < _day(item['start']) or physical[1] > _day(item['end']):
                    raise ValueError('DATA_PHYSICAL_RANGE_CONFLICT')
            stat = path.stat()
            input_bytes += stat.st_size
            plans.append((name, item, path, guard, physical, stat))
        metadata = self.provider._metadata_paths[dataset_id]
        if _sha(metadata) != manifest_hash:
            raise ValueError('PROJECTION_PARENT_MANIFEST_CHANGED')
        input_bytes += metadata.stat().st_size
        if input_bytes > binding['max_input_bytes']:
            raise ValueError('PROJECTION_INPUT_BYTE_LIMIT_EXCEEDED')
        projection_id = identity({'version': VERSION, 'dataset_id': dataset_id,
            'manifest_sha256': manifest_hash, 'authorization_id': binding['authorization_id'],
            'output_start': start, 'output_end': end, 'account_start': account_start})
        recipe_hash = _sha(Path(__file__))
        manifest.update(start=start, end=end, files={},
            historical_independence=original.get('historical_independence', 'UNKNOWN'),
            independent_confirmation_eligible=False,
            projection={'version': VERSION, 'purpose': PURPOSE, 'projection_id': projection_id,
                'parent_manifest_sha256': manifest_hash, 'receipt_path': 'PROJECTION_RECEIPT.json'})
        if original.get('universe_scope'):
            left, right = max(start, _day(original['universe_scope']['start'])), min(end, _day(original['universe_scope']['end']))
            if left > right:
                raise ValueError('PROJECTION_HISTORICAL_UNIVERSE_SCOPE_NOT_COVERED')
            manifest['universe_scope'] = {'start': left, 'end': right}
        receipts, calendar, output_bytes = [], [], 0
        with tempfile.TemporaryDirectory(prefix='.train-projection-', dir=output.parent) as temporary:
            staging = Path(temporary)
            for index, (name, item, path, guard, physical, before) in enumerate(plans):
                scope.binding  # 到期后的新文件读取仍拒绝。
                self.record({'event': 'DATA_PROJECTION_SOURCE_READ', 'purpose': PURPOSE,
                    'dataset_id': dataset_id, 'authorization_id': binding['authorization_id'],
                    'source': name, 'sha256': item['sha256'], 'physical_range': list(physical)})
                if _sha(path) != item['sha256']:
                    raise ValueError('DATA_SOURCE_CONTENT_CHANGED:' + name)
                kind = item['kind']
                child_name = f'{index:03d}_{kind.lower()}' + ('.parquet' if item['format'] == 'PARQUET' else '.json')
                child_path = staging / child_name
                if item['format'] == 'PARQUET':
                    value, columns = _project_parquet(path, item, start, end)
                    pq.write_table(value, child_path, compression='zstd')
                    count = value.num_rows
                else:
                    rows = json.loads(path.read_text(encoding='utf-8-sig'))
                    value, days = _project_json(rows, kind, start, end, universe.records)
                    if days:
                        physical = min(days), max(days)
                        guard.check_range(*physical, 'actual JSON whole source')
                        if kind != 'EVENTS' and (physical[0] < _day(item['start']) or physical[1] > _day(item['end'])):
                            raise ValueError('DATA_PHYSICAL_RANGE_CONFLICT')
                    _write(child_path, value)
                    count = len(value)
                    columns = None
                    if kind == 'CALENDAR':
                        calendar.extend(value)
                after = path.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise ValueError('DATA_SOURCE_CHANGED_DURING_READ')
                digest = _sha(child_path)
                fresh = deepcopy(item)
                fresh.update(sha256=digest, start=max(start, _day(item['start'])), end=min(end, _day(item['end'])))
                if fresh['start'] > fresh['end']:
                    fresh.update(start=start, end=end)
                if columns is not None:
                    fresh['date_columns'] = columns
                fresh['projection_parent'] = {'name': name, 'sha256': item['sha256'],
                    'declared_start': _day(item['start']), 'declared_end': _day(item['end']),
                    'physical_start': physical[0], 'physical_end': physical[1]}
                if kind == 'DAILY':
                    evidence = fresh.get('evidence', {})
                    if evidence.get('source_sha256') != item['sha256']:
                        raise ValueError('TDX_SOURCE_IDENTITY_CONFLICT')
                    fresh['evidence'] = {**evidence, 'source_sha256': digest,
                        'original_source_hashes': {**evidence.get('original_source_hashes', {}),
                                                 'projection_parent:' + name: item['sha256']},
                        'transformation_version': VERSION, 'transformation_sha256': recipe_hash,
                        'projection_parent_evidence': deepcopy(evidence)}
                if kind == 'EVENTS':
                    fresh['projection_date_semantics'] = 'RIGHTS_INTERVAL_OVERLAPS_TRAIN_COMPLETE_TERMS'
                manifest['files'][child_name] = fresh
                if kind == 'SOURCE_QUALIFICATION':
                    manifest['source_qualification_ref'] = child_name
                output_bytes += child_path.stat().st_size
                if output_bytes > binding['max_output_bytes']:
                    raise ValueError('PROJECTION_OUTPUT_BYTE_LIMIT_EXCEEDED')
                receipts.append({'parent_name': name, 'parent_sha256': item['sha256'],
                    'parent_physical_start': physical[0], 'parent_physical_end': physical[1],
                    'child_name': child_name, 'child_sha256': digest, 'rows': count,
                    'parent_whole_file_read_for_non_analytical_preparation': True})
            manifest_path = staging / 'manifest_train_v1.json'
            _write(manifest_path, manifest)
            from .research_data_qualification_v1 import continuous_data_dependencies_v1
            dependencies = continuous_data_dependencies_v1(manifest,
                account_sessions=sum(day >= account_start for day in set(calendar)) if account_start else None)
            receipt = {'version': VERSION, 'projection_id': projection_id, 'purpose': PURPOSE,
                'authorization_id': binding['authorization_id'], 'approval_id': binding['approval_id'],
                'dataset_id': dataset_id, 'parent_manifest_sha256': manifest_hash,
                'manifest_sha256': _sha(manifest_path), 'output_start': start, 'output_end': end,
                'recipe_sha256': recipe_hash, 'sources': receipts, 'input_bytes': input_bytes,
                'target_symbols': universe.target_symbols, 'target_count': len(universe.target_symbols),
                'master_record_count': len(universe.records), 'data_dependencies': dependencies,
                'historical_independence': manifest['historical_independence'],
                'independent_confirmation_eligible': False, 'originals_modified': False}
            receipt['receipt_id'] = identity(receipt)
            _write(staging / 'PROJECTION_RECEIPT.json', receipt)
            if sum(path.stat().st_size for path in staging.iterdir()) > binding['max_output_bytes']:
                raise ValueError('PROJECTION_OUTPUT_BYTE_LIMIT_EXCEEDED')
            # 核对真实供应器登记合同后一次发布；失败不会留下可误用的半份登记。
            check = UniverseDataProviderV1({'projection': staging})
            check.register('projection_check', 'projection', manifest_path.name)
            scope.binding
            staging.rename(output)
        return {'version': VERSION, 'projection_id': projection_id,
            'manifest_path': str(output / 'manifest_train_v1.json'),
            'receipt_path': str(output / 'PROJECTION_RECEIPT.json'), 'receipt_id': receipt['receipt_id'],
            'data_dependencies': dependencies, 'independent_confirmation_eligible': False}
