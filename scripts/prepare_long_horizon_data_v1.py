"""为完整登记池扩展工程资料；继承原件、重新绑定证据，不继承策略资格。"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path, PureWindowsPath
import shutil
import sys

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / 'src'))
from chanlun_trader.research.guard import ResearchDataAccessGuard
from chanlun_trader.research_factory.baostock_raw_supplement_v1 import (
    collected_raw_source_v1, read_collected_raw_v1,
)
from chanlun_trader.research_factory.research_universe_v1 import _day, canonical_symbol
from chanlun_trader.research_factory.tdx_research_adapter_v1 import read_tdx_day_window
from chanlun_trader.research_factory.universe_data_provider_v1 import UniverseDataProviderV1
from scripts import collect_universe_gaps_v1 as collector
from scripts.prepare_full_universe_data_v1 import board_policy_binding_v1
from scripts.prepare_universe_supplements_v1 import (
    _REFERENCE_SCHEMA, _STATE_SCHEMA, _append, _load, _sha, _state, _tdx_prices,
    _write, REFERENCE_SOURCE, STATE_POLICY, STATE_SOURCE,
)

VERSION = 'LONG_HORIZON_DATA_PREPARATION_V1'


def inherited_path_v1(path, digest, legacy_root, relocations):
    """相对声明仅按显式主目录解析；绝对旧声明仅作同名项目内同字节迁移。"""
    if not isinstance(path, (str, Path)) or not str(path):
        raise ValueError('INHERITED_SOURCE_PATH_INVALID:' + str(path))
    text = str(path).replace('\\', '/')
    windows = PureWindowsPath(text)
    if ('..' in windows.parts or windows.drive.startswith('\\\\')
            or windows.drive and not windows.is_absolute()):
        raise ValueError('INHERITED_SOURCE_PATH_INVALID:' + str(path))
    normalized = Path(text)
    if windows.root and not windows.drive and (str(path).startswith('\\') or not normalized.is_absolute()):
        raise ValueError('INHERITED_SOURCE_PATH_INVALID:' + str(path))
    legacy = Path(legacy_root).absolute()
    if legacy.resolve() != legacy:
        raise ValueError('INHERITED_SOURCE_ROOT_REDIRECTED:' + str(legacy_root))
    if normalized.is_absolute() or windows.is_absolute():
        # 外平台Windows旧引用不应被误解成当前目录里的相对路径。
        candidate = normalized.absolute() if normalized.is_absolute() else None
        if candidate is None or not candidate.is_file():
            positions = [i for i, part in enumerate(windows.parts) if part == PROJECT_ROOT.name]
            if not positions:
                raise ValueError('INHERITED_SOURCE_MISSING:' + str(path))
            candidate = legacy.joinpath(*windows.parts[positions[-1]+1:])
            if not candidate.is_relative_to(legacy):
                raise ValueError('INHERITED_SOURCE_PATH_INVALID:' + str(path))
    else:
        candidate = legacy.joinpath(*windows.parts)
        if not candidate.is_relative_to(legacy):
            raise ValueError('INHERITED_SOURCE_PATH_INVALID:' + str(path))
    if candidate.resolve() != candidate or not candidate.is_file() or _sha(candidate) != digest:
        raise ValueError('INHERITED_SOURCE_MISSING_OR_CHANGED:' + str(path))
    relocations.append({'original_path': str(path), 'retained_path': str(candidate),
        'original_sha256': digest, 'retained_sha256': digest, 'same_content_verified': True})
    return candidate

def relocate_declarations_v1(value, legacy_root, relocations):
    """只在新声明里改变绑定位置；供应商原件、公告正文和历史声明原样保留。"""
    if isinstance(value, list):
        return [relocate_declarations_v1(v, legacy_root, relocations) for v in value]
    if not isinstance(value, dict):
        return deepcopy(value)
    result = {k: relocate_declarations_v1(v, legacy_root, relocations) for k, v in value.items()}
    if isinstance(value.get('path'), str) and isinstance(value.get('sha256'), str):
        result['path'] = str(inherited_path_v1(value['path'], value['sha256'], legacy_root, relocations))
    return result


def _new_collection_sources(acquisition_root):
    root = Path(acquisition_root).absolute()
    try:
        view = collector.verified_collection_successes_v1(root, require_complete=True)
    except ValueError as exc:
        if str(exc) == 'COLLECTOR_COLLECTION_NOT_COMPLETE':
            raise ValueError('LONG_DATA_COLLECTION_NOT_COMPLETE') from exc
        raise
    sources = {}
    for reference in view['sources']:
        item = reference['request_item']
        if item['api'] != 'query_history_k_data_plus':
            continue
        directory = Path(reference['collection_root']) / 'batches' / reference['batch_id']
        source = collected_raw_source_v1(directory, item, reference['result_row'],
            result_sha256=reference['result_sha256'])
        if source['symbol'] in sources:
            raise ValueError('LONG_DATA_DUPLICATE_RAW_SECURITY')
        sources[source['symbol']] = source
    leaf = view['collections'][-1]
    return sources, {key: leaf[key] for key in (
        'queue_sha256', 'frozen_collection_sha256', 'journal_sha256')} | {
        'completed_batches': leaf['completed_batch_count'], 'raw_source_count': len(sources),
        'collections': view['collections'], 'successful_requests': view['sources'],
        'request_count': view['request_count'], 'success_count': view['success_count'],
        'root_planned_request_count': view['root_planned_request_count'],
        'generation': view['generation'], 'retry_counts_by_operation': view['retry_counts_by_operation']}


def _calendar(calendar_path, feature_start, account_start, account_end):
    source = _load(calendar_path)
    if (source.get('source') != 'baostock.query_trade_dates'
            or not isinstance(source.get('trade_dates'), list)):
        raise ValueError('LONG_DATA_INDEPENDENT_CALENDAR_REQUIRED')
    bounds = source['query_date_range']
    guard = ResearchDataAccessGuard()
    guard.check_range(_day(bounds['start']), _day(bounds['end']), 'long data calendar whole source')
    days = [_day(d) for d in source['trade_dates']]
    guard.check_int_iterable(days, 'long data source calendar')
    if days != sorted(set(days)):
        raise ValueError('LONG_DATA_CALENDAR_INVALID')
    selected = [d for d in days if feature_start <= d <= account_end]
    if not selected or selected[0] != feature_start or selected[-1] != account_end or account_start not in selected:
        raise ValueError('LONG_DATA_CALENDAR_WINDOW_INVALID')
    return selected


def _copy_daily(base_path, metadata, output, calendar, relocations):
    """主缓存原样复制；旧限窗补建缓存从相同本地DAY重新物化新窗口。"""
    files = {}
    # 与公开Provider相同：资格来自登记文件kind；历史hint不授予原件读取权限。
    registered = [(name, entry) for name, entry in metadata['files'].items()
        if entry['kind'] == 'SOURCE_QUALIFICATION']
    if len(registered) != 1:
        raise ValueError('LONG_DATA_SINGLE_REGISTERED_QUALIFICATION_REQUIRED')
    qualification_name, qmeta = registered[0]
    if qmeta['format'] != 'JSON':
        raise ValueError('LONG_DATA_QUALIFICATION_FORMAT_INVALID')
    ResearchDataAccessGuard().check_range(qmeta['start'], qmeta['end'], 'long data whole inherited qualification')
    qpath = UniverseDataProviderV1._path(base_path.parent, qualification_name)
    if _sha(qpath) != qmeta['sha256']:
        raise ValueError('LONG_DATA_QUALIFICATION_CHANGED')
    qualifications = _load(qpath)
    if (not isinstance(qualifications, list)
            or any(not isinstance(row, dict) or 'symbol' not in row for row in qualifications)):
        raise ValueError('LONG_DATA_QUALIFICATION_INVALID')
    qualified_symbols = {canonical_symbol(row['symbol']) for row in qualifications}
    targets = {canonical_symbol(row['symbol']) for row in metadata['master']['records']}
    if len(qualified_symbols) != len(qualifications) or qualified_symbols != targets:
        raise ValueError('LONG_DATA_QUALIFICATION_TARGET_MISMATCH')
    _write(output / 'SOURCE_QUALIFICATION_SELECTION.json', {'version': VERSION,
        'selection_policy': 'UNIQUE_REGISTERED_SOURCE_QUALIFICATION_KIND',
        'original_manifest': str(base_path), 'original_manifest_sha256': _sha(base_path),
        'declared_hint': metadata.get('source_qualification_ref'), 'declared_hint_read': False,
        'registered_ref': qualification_name, 'registered_kind': qmeta['kind'],
        'original_path': str(qpath), 'original_sha256': qmeta['sha256'],
        'registered_sha256_verified': True, 'target_count': len(targets), 'strategy_qualified': False})
    for index, (name, entry) in enumerate(metadata['files'].items()):
        if entry['kind'] != 'DAILY':
            continue
        guard = ResearchDataAccessGuard()
        guard.check_range(entry['start'], entry['end'], 'long data whole inherited daily')
        source = UniverseDataProviderV1._path(base_path.parent, name)
        UniverseDataProviderV1._check_parquet_range(source, entry, guard)
        if _sha(source) != entry['sha256']:
            raise ValueError('LONG_DATA_DAILY_SOURCE_CHANGED')
        fresh = deepcopy(entry)
        target = output / f'daily_{index}.parquet'
        if entry['start'] <= calendar[0] and entry['end'] >= calendar[-1]:
            shutil.copyfile(source, target)
            if _sha(target) != entry['sha256']:
                raise ValueError('LONG_DATA_DAILY_COPY_CHANGED')
            relocations.append({'original_path': str(source), 'retained_path': str(target),
                'original_sha256': entry['sha256'], 'retained_sha256': entry['sha256'],
                'same_content_verified': True})
        else:
            originals = entry['evidence'].get('original_source_hashes', {})
            by_symbol = {}
            for original in originals:
                name_part = Path(original).stem
                if len(name_part) == 8:
                    by_symbol[canonical_symbol(name_part[:2] + '.' + name_part[2:])] = original
            frames, new_hashes = [], {}
            for symbol in entry['symbols']:
                original = Path(by_symbol[symbol]).absolute()
                if original.resolve() != original or not original.is_file():
                    raise ValueError('LONG_DATA_LOCAL_DAY_SOURCE_MISSING:' + symbol)
                rows, origin = read_tdx_day_window(original, calendar)
                if not rows:
                    continue
                frame = pd.DataFrame(rows).rename(columns={
                    'volume_encoded': 'volume', 'amount_encoded': 'amount'})
                frame['symbol'] = symbol
                frames.append(frame)
                new_hashes[str(original)] = origin['window_sha256']
            if not frames:
                continue
            pd.concat(frames, ignore_index=True).to_parquet(target, index=False)
            fresh.update(start=calendar[0], end=calendar[-1], sha256=_sha(target),
                symbols=sorted(set(s for frame in frames for s in frame.symbol)))
            fresh['evidence'].update(source_sha256=fresh['sha256'],
                original_source_hashes=new_hashes, prev_close_semantics='ABSENT',
                transformation_version=VERSION, transformation_sha256=_sha(Path(__file__)))
        files[target.name] = fresh
    return files, qualifications


def _register_legacy_actions(catalog_path, output, legacy_root, relocations):
    catalog = _load(catalog_path)
    grouped = defaultdict(dict)
    for source in catalog['sources']:
        path = inherited_path_v1(source['path'], source['sha256'], legacy_root, relocations)
        grouped[path.parent][path.name] = {'start': source['physical_start'],
            'end': source['physical_end'], 'sha256': source['sha256']}
    roots, datasets = {}, []
    for i, (directory, files) in enumerate(sorted(grouped.items(), key=lambda p: str(p[0]))):
        root_id = f'legacy_{i}'
        path = output / f'legacy_registration_{i}.json'
        _write(path, {'files': files})
        roots[root_id] = str(directory)
        datasets.append({'root_id': root_id, 'manifest_path': str(path)})
    target = output / 'LEGACY_ACTION_CATALOG.json'
    _write(target, {'roots': roots, 'datasets': datasets,
        'original_source_catalog': {'path': str(Path(catalog_path).absolute()),
                                    'sha256': _sha(catalog_path)}})
    return target


def _merge_legacy_action_catalogs(base_catalog, additional, output):
    """只在新登记中接入补充原件；同身份去重，查询或路径身份冲突即阻断。"""
    if not additional:
        return base_catalog
    from scripts.prepare_universe_actions_v1 import _legacy_sources, _read_response
    paths = [additional] if isinstance(additional, (str, Path)) else additional
    merged = _load(base_catalog)
    base_rows, _ = _legacy_sources(base_catalog)
    operations, physical_paths = defaultdict(set), {}

    def identity(row):
        return collector._hash({'path': str(row['path']), 'sha256': row['sha256'],
            'api': row['api'], 'request': row['request'],
            'physical_metadata': row['physical_metadata']})

    for row in base_rows:
        signature = identity(row)
        operations[collector._hash({'api': row['api'], 'request': row['request']})].add(signature)
        physical_paths[str(row['path'])] = signature
    catalogs, seen_catalogs, additions = [], set(), []
    for source in paths:
        path = Path(source).absolute()
        digest = _sha(path)
        if (str(path), digest) in seen_catalogs:
            continue
        seen_catalogs.add((str(path), digest))
        declaration = _load(path)
        catalogs.append({'path': str(path), 'sha256': digest, 'datasets': [
            {'path': str(Path(dataset['manifest_path']).absolute()),
             'sha256': _sha(Path(dataset['manifest_path']))} for dataset in declaration.get('datasets', [])]})
        rows, skipped = _legacy_sources(path)
        if skipped:
            raise ValueError('LONG_DATA_ADDITIONAL_LEGACY_SCOPE_UNAVAILABLE')
        for row in rows:
            _read_response(row, output / 'ADDITIONAL_LEGACY_READ_EVENTS.jsonl')
            operation = collector._hash({'api': row['api'], 'request': row['request']})
            signature, physical = identity(row), str(row['path'])
            if (operations[operation] and operations[operation] != {signature}
                    or physical in physical_paths and physical_paths[physical] != signature):
                raise ValueError('LONG_DATA_ADDITIONAL_LEGACY_IDENTITY_CONFLICT')
            if signature in operations[operation]:
                continue
            operations[operation].add(signature)
            physical_paths[physical] = signature
            additions.append(row)
    for catalog in catalogs:
        for reference in [catalog, *catalog['datasets']]:
            if _sha(Path(reference['path'])) != reference['sha256']:
                raise ValueError('LONG_DATA_ADDITIONAL_LEGACY_REGISTRATION_CHANGED')
    grouped = defaultdict(dict)
    for row in additions:
        grouped[row['path'].parent][row['path'].name] = row['physical_metadata']
    for index, (directory, files) in enumerate(sorted(grouped.items(), key=lambda pair: str(pair[0]))):
        root_id = f'additional_legacy_{index}'
        registration = output / f'additional_legacy_registration_{index}.json'
        _write(registration, {'files': files})
        merged['roots'][root_id] = str(directory)
        merged['datasets'].append({'root_id': root_id, 'manifest_path': str(registration)})
    merged['additional_legacy_catalogs'] = catalogs
    target = output / 'LEGACY_ACTION_CATALOG_EXTENDED.json'
    _write(target, merged)
    return target


def _rebind_declaration(binding, legacy_root, output, name, relocations):
    source = inherited_path_v1(binding['path'], binding['sha256'], legacy_root, relocations)
    original = _load(source)
    value = relocate_declarations_v1(original, legacy_root, relocations)
    path = output / name
    _write(path, value)
    return path


def prepare_long_horizon_data_v1(*, base_manifest, acquisition_root, output_root,
        legacy_root, feature_start=20220902, account_start=20221205, account_end=20241231,
        additional_legacy_catalog=None):
    """固定全目标登记；缺证据仍进入缺口/排除表，不因结果好坏缩池。"""
    base_path, output, legacy = (Path(p).absolute() for p in
        (base_manifest, output_root, legacy_root))
    if output.resolve() != output or output.exists():
        raise ValueError('LONG_DATA_NEW_OUTPUT_REQUIRED')
    base = _load(base_path)
    base_sha256 = _sha(base_path)
    if base.get('adapter') != 'TDX_FULL_UNIVERSE_V1':
        raise ValueError('LONG_DATA_TDX_MANIFEST_REQUIRED')
    guard = ResearchDataAccessGuard()
    guard.check_range(feature_start, account_end, 'long data requested window')
    masters = {r['symbol']: r for r in base['master']['records']}
    calendar_path = legacy / 'data/research/security_state/raw/trade_calendar.json'
    calendar = _calendar(calendar_path, feature_start, account_start, account_end)
    calendar_set = set(calendar)
    sources, collection = _new_collection_sources(acquisition_root)
    if set(sources) != set(masters):
        raise ValueError('LONG_DATA_FULL_REGISTERED_POOL_REQUIRED')
    start_text, end_text = (str(pd.Timestamp(str(d)).date()) for d in (feature_start, account_end))
    plan = {'symbols': sorted(masters), 'start': start_text, 'end': end_text,
        'historical_available_at_verified': False, 'historical_availability': 'MODELED',
        'state_policy': STATE_POLICY, 'provider_format': 'BAOSTOCK_COLLECTOR_RAW_V1'}
    output.mkdir(parents=True)
    relocations = []
    _write(output / 'PREPARATION_STARTED.json', {'version': VERSION,
        'base_manifest': str(base_path), 'base_manifest_sha256': base_sha256,
        'full_target_count': len(masters), 'window': {'feature_start': feature_start,
            'account_start': account_start, 'account_end': account_end}, 'collection': collection,
        'historical_available_at_verified': False, 'strategy_qualified': False})
    files, qualifications = _copy_daily(base_path, base, output, calendar, relocations)
    _write(output / 'calendar.json', calendar)
    files['calendar.json'] = {'kind': 'CALENDAR', 'format': 'JSON',
        'start': calendar[0], 'end': calendar[-1], 'source_id': 'long_exchange_calendar',
        'sha256': _sha(output / 'calendar.json'),
        'original_source_hashes': {str(calendar_path): _sha(calendar_path)}}
    manifest = deepcopy(base)
    manifest.update(files=files, universe_scope={'start': feature_start, 'end': account_end},
        universe_id=f'REGISTERED_POOL_LONG_{feature_start}_{account_end}', corporate_actions_complete=False)
    # 原 master 的完整性范围不扩大；记录同一登记名单的工程扩窗。
    manifest['long_horizon_registration'] = {'version': VERSION,
        'original_universe_id': base['universe_id'], 'original_manifest_sha256': base_sha256,
        'target_list_unchanged': True, 'target_count': len(masters),
        'historical_market_completeness_extended': False, 'independent_confirmation_eligible': False}
    provisional = output / 'manifest_prices.json'
    _write(provisional, manifest)
    prices = _tdx_prices(provisional, manifest, feature_start, account_end)
    state_path, reference_path = output / 'states.parquet', output / 'reference_prices.parquet'
    source_catalog_path = output / 'RAW_SOURCE_CATALOG.json'
    plan_path = output / 'RAW_READ_PLAN.json'
    _write(plan_path, plan)
    counters, gaps = Counter(), []
    with pq.ParquetWriter(state_path, _STATE_SCHEMA, compression='zstd') as states_writer, \
            pq.ParquetWriter(reference_path, _REFERENCE_SCHEMA, compression='zstd') as refs_writer:
        for symbol in sorted(masters):
            rows, binding = read_collected_raw_v1(sources[symbol], plan)
            states, refs = [], []
            paired = prices.get(symbol)
            for row in rows:
                day = _day(row['date'])
                if day not in calendar_set:
                    raise ValueError('LONG_DATA_RAW_DATE_NOT_IN_EXCHANGE_CALENDAR')
                states.append(_state(row, symbol, masters[symbol], binding, base_sha256))
                if paired is None or day not in paired.index:
                    continue
                source_bar = paired.loc[day]
                try:
                    match = all(math.isfinite(float(row[f])) and math.isclose(float(row[f]),
                        float(source_bar[f]), rel_tol=0, abs_tol=1e-8) for f in ('open', 'high', 'low', 'close'))
                    previous = float(row['preclose'])
                    if not math.isfinite(previous) or previous <= 0:
                        match = False
                except (ValueError, TypeError):
                    match = False
                if not match:
                    gaps.append({'symbol': symbol, 'date': day, 'reason': 'TDX_BAOSTOCK_RAW_OHLC_OR_PRECLOSE_CONFLICT'})
                    continue
                refs.append({'symbol': symbol, 'date': day, 'prev_close': previous,
                    'source': REFERENCE_SOURCE, 'original_response_path': binding['path'],
                    'original_response_sha256': binding['sha256'],
                    'pairing_basis': 'RAW_OHLC_MATCH_ABS_TOL_1E_8'})
            if states:
                states_writer.write_table(pa.Table.from_pylist(states, schema=_STATE_SCHEMA))
            if refs:
                refs_writer.write_table(pa.Table.from_pylist(refs, schema=_REFERENCE_SCHEMA))
            counters.update(raw_sources=1, state_rows=len(states), reference_rows=len(refs))
    _write(source_catalog_path, {'read_plan_path': str(plan_path), 'read_plan_sha256': _sha(plan_path),
        'responses': [sources[s] for s in sorted(sources)], 'version': VERSION,
        'historical_availability': 'MODELED', 'historical_available_at_verified': False})
    for path, kind, source in ((state_path, 'STATES', STATE_SOURCE),
                              (reference_path, 'REFERENCE_PRICES', REFERENCE_SOURCE)):
        manifest['files'][path.name] = {'kind': kind, 'format': 'PARQUET',
            'start': feature_start, 'end': account_end, 'source_id': source, 'sha256': _sha(path),
            'historical_availability': 'MODELED', 'evidence': {'source_catalog_path': source_catalog_path.name,
                'source_catalog_sha256': _sha(source_catalog_path), 'state_policy': STATE_POLICY}}
    _write(output / 'SOURCE_QUALIFICATION.json', qualifications)
    manifest['source_qualification_ref'] = 'SOURCE_QUALIFICATION.json'
    manifest['files']['SOURCE_QUALIFICATION.json'] = {'kind': 'SOURCE_QUALIFICATION', 'format': 'JSON',
        'start': manifest['start'], 'end': manifest['end'], 'source_id': 'source_qualification',
        'sha256': _sha(output / 'SOURCE_QUALIFICATION.json'),
        'evidence': {'selection_receipt_path': 'SOURCE_QUALIFICATION_SELECTION.json',
            'selection_receipt_sha256': _sha(output / 'SOURCE_QUALIFICATION_SELECTION.json')}}
    _write(output / 'RAW_PAIRING_GAPS.json', gaps)
    binding = board_policy_binding_v1()
    manifest['board_policy_binding'], manifest['board_policy_identity'] = binding, binding['identity']
    registration = output / 'manifest_sources.json'
    _write(registration, manifest)
    legacy_catalog = _register_legacy_actions(base_path.parent /
        base['corporate_action_preparation']['source_catalog'], output, legacy, relocations)
    legacy_catalog = _merge_legacy_action_catalogs(legacy_catalog, additional_legacy_catalog, output)
    from scripts.prepare_universe_actions_v1 import prepare_universe_actions_v1
    action_v1 = prepare_universe_actions_v1(manifest=registration, acquisition_root=acquisition_root,
        output_dir=output / 'actions_v1', legacy_catalog=legacy_catalog,
        feature_start=feature_start, account_end=account_end, manifest_name='manifest_actions_stage1.json')
    stage1 = Path(action_v1['new_manifest'])
    old_catalog = _load(base_path.parent / base['corporate_action_preparation']['source_catalog'])
    documents = {}
    for key, filename in [('share_terms', 'SHARE_TERMS_RELOCATED.json'),
                          ('cash_components', 'CASH_COMPONENTS_RELOCATED.json')]:
        if old_catalog.get(key):
            documents[key] = _rebind_declaration(old_catalog[key], legacy, output, filename, relocations)
    from scripts.resolve_universe_action_dates_v1 import resolve_universe_action_dates_v1
    date_result = resolve_universe_action_dates_v1(source_catalog=output / 'actions_v1/SOURCE_CATALOG.json',
        gaps=output / 'actions_v1/ACTION_GAPS.json', manifest=stage1,
        output_dir=output / 'date_resolution', market_catalog=source_catalog_path,
        feature_start=feature_start, account_end=account_end)
    numeric_receipt = None
    if old_catalog.get('numeric_terms'):
        original_numeric = inherited_path_v1(old_catalog['numeric_terms']['path'],
            old_catalog['numeric_terms']['sha256'], legacy, relocations)
        numeric = _load(original_numeric)
        tdx_binding = _rebind_declaration(numeric['tdx_binding'], legacy, output,
            'TDX_INPUT_BINDING_RELOCATED.json', relocations)
        tdx_window = _load(tdx_binding)
        supported = [g for g in _load(output / 'actions_v1/ACTION_GAPS.json')
            if g.get('reason') == 'ACTION_NUMERIC_TERM_UNKNOWN'
            and tdx_window['start'] <= g.get('effective_date', 0) <= tdx_window['end']]
        # 仅重核原物理包确实覆盖的数值；其它缺口仍保留在完整 ACTION_GAPS。
        numeric_gaps = output / 'NUMERIC_GAPS_WITHIN_RETAINED_TDX_WINDOW.json'
        _write(numeric_gaps, supported)
        from scripts.resolve_universe_numeric_terms_v1 import resolve_universe_numeric_terms_v1
        result = resolve_universe_numeric_terms_v1(source_catalog=output / 'actions_v1/SOURCE_CATALOG.json',
            numeric_gaps=numeric_gaps, manifest=stage1, tdx_binding=tdx_binding,
            output_dir=output / 'numeric_resolution')
        numeric_receipt = result['receipt_path']
    from scripts.prepare_universe_actions_v2 import prepare_universe_actions_v2
    final = prepare_universe_actions_v2(manifest=stage1,
        source_catalog=output / 'actions_v1/SOURCE_CATALOG.json',
        action_gaps=output / 'actions_v1/ACTION_GAPS.json', output_dir=output / 'actions_v2',
        manifest_name='manifest_long_horizon_v1.json',
        action_resolutions=date_result['receipt'], share_terms=documents.get('share_terms'),
        cash_components=documents.get('cash_components'), numeric_terms=numeric_receipt,
        feature_start=feature_start, account_end=account_end)
    _write(output / 'SOURCE_RELOCATION_RECEIPT.json', {'version': VERSION,
        'previous_manifest_sha256': _sha(base_path), 'relocations': relocations,
        'new_declarations_only': True, 'originals_modified': False,
        'historical_available_at_verified': False, 'strategy_qualified': False})
    summary = {'version': VERSION, 'target_count': len(masters), 'target_list_unchanged': True,
        'calendar_sessions': len(calendar), 'account_sessions': sum(d >= account_start for d in calendar),
        **dict(counters), 'raw_pairing_conflicts': len(gaps), 'corporate_actions': final,
        'manifest_path': final['new_manifest'], 'manifest_sha256': final['new_manifest_sha256'],
        'historical_availability': 'MODELED', 'strategy_qualified': False,
        'account_execution_performed': False, 'independent_confirmation_eligible': False,
        'source_qualification_selection': _load(output / 'SOURCE_QUALIFICATION_SELECTION.json')}
    _write(output / 'PREPARATION_COMPLETE.json', summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('base-manifest', 'acquisition-root', 'output-root', 'legacy-root'):
        parser.add_argument('--' + key, required=True)
    parser.add_argument('--additional-legacy-catalog', action='append')
    for key, default in [('feature-start', 20220902), ('account-start', 20221205), ('account-end', 20241231)]:
        parser.add_argument('--' + key, type=int, default=default)
    result = prepare_long_horizon_data_v1(**vars(parser.parse_args()))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
