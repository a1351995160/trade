"""维护者限窗准备全范围TDX数据；只生成来源/缺口证据，不运行策略或账户。"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import sys

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from chanlun_trader.research.guard import ResearchDataAccessGuard
from chanlun_trader.research_factory import board_execution_policy_v1
from chanlun_trader.research_factory.research_universe_v1 import (
    BOARDS, ResearchUniverseV1, _day, identity, normalize_board, scope_board,
)
from chanlun_trader.research_factory.tdx_research_adapter_v1 import (
    compare_cache_to_raw_window, read_tdx_day_window,
)


VERSION = 'FULL_UNIVERSE_DATA_PREPARATION_V1'


def _sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def board_policy_binding_v1():
    """绑定当前执行制度代码；不将代码版本当作历史数据或可见时间证据。"""
    policy = board_execution_policy_v1
    return {'version': policy.VERSION, 'identity': policy.board_policy_identity(),
        'description': policy.describe_board_policy_v1(),
        'implementation_source': 'src/chanlun_trader/research_factory/board_execution_policy_v1.py',
        'implementation_sha256': _sha(policy.__file__),
        'evidence_type': 'EXECUTION_POLICY_CODE_VERSION_ONLY',
        'historical_data_qualification_changed': False}


def _write(path, value):
    with Path(path).open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        stream.write('\n')


def _append(path, value):
    with Path(path).open('a', encoding='utf-8', newline='\n') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False) + '\n')


def _physical_range(path):
    """仅footer日期统计；没有完整统计不读取或复制报价内容。"""
    footer = pq.ParquetFile(path).metadata
    bounds = []
    for index in range(footer.num_row_groups):
        group = footer.row_group(index)
        date = next((group.column(i) for i in range(group.num_columns)
                     if group.column(i).path_in_schema == 'date'), None)
        if date is None or (group.num_rows and (date.statistics is None or not date.statistics.has_min_max)):
            raise ValueError('DATA_PHYSICAL_DATE_METADATA_MISSING')
        if group.num_rows:
            bounds.extend([_day(date.statistics.min), _day(date.statistics.max)])
    if not bounds:
        raise ValueError('DATA_CACHE_EMPTY')
    first, last = min(bounds), max(bounds)
    ResearchDataAccessGuard().check_range(first, last, 'preparation whole cache physical range')
    return first, last, footer.num_rows


def _source_catalog(source_root: Path, tdx_root: Path, unit_path: Path):
    known = [source_root / 'data/research/security_state/normalized/manifest.json',
        source_root / 'data/research/security_state/normalized/security_master_v2/records.json',
        source_root / 'data/research/security_state/raw/trade_calendar.json',
        source_root / 'data/research/pit_security_master.json',
        tdx_root / 'T0002/hq_cache/gbbq', unit_path]
    result = []
    for path in known:
        item = {'path': str(path), 'exists': path.is_file(), 'content_read': False,
                'qualification': 'METADATA_ONLY_NOT_IMPORTED'}
        if path.is_file():
            stat = path.stat()
            item.update(size=stat.st_size, mtime_ns=stat.st_mtime_ns)
        result.append(item)
    normalized = source_root / 'data/research/security_state/normalized'
    for name in ['pit_universe_v2', 'st_state', 'suspension_state']:
        directory = normalized / name
        files = sorted(path.name for path in directory.glob('*.jsonl')) if directory.is_dir() else []
        result.append({'path': str(directory), 'exists': directory.is_dir(), 'content_read': False,
            'file_count': len(files), 'first_file': files[0] if files else None,
            'last_file': files[-1] if files else None,
            'qualification': 'METADATA_ONLY_NOT_IMPORTED'})
    return result


def prepare_full_universe_data_v1(*, source_root, tdx_root, unit_evidence_path,
                                  output_root, verify_raw=True):
    """输出固定历史清单、复制的封存前缓存和逐股缺口；原件始终只读。"""
    source_root, tdx_root, unit_path, out = map(lambda p: Path(p).absolute(),
        (source_root, tdx_root, unit_evidence_path, output_root))
    for path in (source_root, tdx_root, unit_path):
        if path.resolve() != path:
            raise ValueError('PREPARATION_SOURCE_REDIRECTED')
    cache = source_root / 'data/research/daily_all.parquet'
    master_path = source_root / 'data/research/stage3_universe_manifest.json'
    if cache.resolve() != cache or master_path.resolve() != master_path:
        raise ValueError('PREPARATION_SOURCE_REDIRECTED')
    first, last, row_count = _physical_range(cache)
    master = json.loads(master_path.read_text(encoding='utf-8'))
    if (master.get('version') != 'STAGE3_UNIVERSE_MANIFEST_V1' or not isinstance(master.get('symbols'), list)
            or not isinstance(master.get('scope'), dict)):
        raise ValueError('PREPARATION_HISTORICAL_MASTER_INVALID')
    scope = {'start': _day(master['scope']['start']), 'end': _day(master['scope']['end'])}
    ResearchDataAccessGuard().check_range(scope['start'], scope['end'], 'historical master scope')
    if not first <= scope['start'] <= scope['end'] <= last:
        raise ValueError('PREPARATION_MASTER_OUTSIDE_CACHE_WINDOW')
    metadata = {'version': VERSION, 'historical_scope': scope,
        'source_master_sha256': _sha(master_path), 'source_master': str(master_path),
        'cache_physical_scope': {'start': first, 'end': last}, 'cache_rows': row_count,
        'original_cache': str(cache), 'tdx_root': str(tdx_root),
        'raw_verification_requested': bool(verify_raw),
        'independent_confirmation_eligible': False, 'strategy_qualified': False}
    if out.resolve() != out or out.exists():
        raise ValueError('PREPARATION_NEW_OUTPUT_DIRECTORY_REQUIRED')
    out.mkdir(parents=True)
    _write(out / 'PREPARATION_STARTED.json', metadata)
    access = out / 'PHYSICAL_READ_EVENTS.jsonl'
    _append(access, {'event': 'PREPARATION_CACHE_COPY_ATTEMPT', 'source': str(cache),
        'start': first, 'end': last, 'purpose': 'ENGINEERING_DATA_PREPARATION',
        'physical_range_verified_from_footer': True})
    before = cache.stat()
    cache_hash = _sha(cache)
    copied = out / 'daily_all.parquet'
    shutil.copyfile(cache, copied)
    if _sha(copied) != cache_hash:
        raise ValueError('PREPARATION_COPY_HASH_CONFLICT')
    after = cache.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('PREPARATION_SOURCE_CHANGED_DURING_COPY')
    _append(access, {'event': 'PREPARATION_CACHE_COPY_VERIFIED', 'source': str(cache),
        'copy': str(copied), 'sha256': cache_hash, 'start': first, 'end': last})
    data = pd.read_parquet(copied)
    ResearchDataAccessGuard().check_frame(data, 'date')
    if not {'symbol', 'date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'prev_close'} <= set(data):
        raise ValueError('PREPARATION_CACHE_FIELDS_INVALID')
    if data.duplicated(['symbol', 'date']).any():
        raise ValueError('PREPARATION_CACHE_DUPLICATE_KEYS')
    groups = data.groupby('symbol', sort=False)
    cached_symbols = sorted(groups.groups)
    records = [{'symbol': row['symbol'], 'board': normalize_board(row.get('board', 'UNKNOWN')),
        'original_board': row.get('board', 'UNKNOWN'), 'listing_date': row.get('list_date'),
        'delisting_date': row.get('delist_date'), 'source': 'stage3_master'}
        for row in master['symbols'] if scope_board(row['symbol']) in BOARDS]
    universe = ResearchUniverseV1(records,
        [{'symbol': symbol, 'source_id': 'tdx_cache', 'source_sha256': cache_hash}
         for symbol in cached_symbols], master_source='stage3_master:sha256:' + metadata['source_master_sha256'],
        completeness_evidence={'verified': False, 'historical': True, 'includes_delisted': True,
            'boards': list(BOARDS), 'source_hashes': {'stage3_master': metadata['source_master_sha256']},
            'scope': scope})
    snapshot = universe.snapshot()
    targets = set(universe.target_symbols)
    unknown_cached = sorted(set(cached_symbols) - targets)
    _write(out / 'INVENTORY.json', {**snapshot, 'cache_physical_scope': metadata['cache_physical_scope'],
        'historical_scope': scope, 'cached_symbols_outside_historical_master': unknown_cached,
        'cache_content_identity': cache_hash})
    _write(out / 'SOURCE_CATALOG.json', _source_catalog(source_root, tdx_root, unit_path))
    unit = json.loads(unit_path.read_text(encoding='utf-8')) if unit_path.is_file() else {}
    unit_ready = (unit.get('DERIVED_VOLUME_UNIT') == 'SHARES'
        and unit.get('VOLUME_EVIDENCE_LEVEL') == 'DERIVED_AND_PUBLICLY_CORROBORATED'
        and unit.get('NOT_FOR_QUALIFICATION') is True
        and Path(unit.get('source_identity', {}).get('directory', '')).absolute() == tdx_root / 'vipdoc')
    unit_hash = _sha(unit_path) if unit_path.is_file() else None
    _write(out / 'UNIT_SCOPE.json', {'evidence_path': str(unit_path), 'evidence_sha256': unit_hash,
        'source_root_matches': unit_ready, 'evidence_level': unit.get('VOLUME_EVIDENCE_LEVEL', 'UNKNOWN'),
        'original_purpose': unit.get('purpose'), 'NOT_FOR_QUALIFICATION': True,
        'vendor_version_attested': False, 'new_full_universe_account_qualification': False})
    # 来源比对窗口以自然日期键允许集合表达，不将它当作交易所日历。
    dates = [int(value) for value in pd.date_range(str(first), str(last)).strftime('%Y%m%d')]
    origins, gaps, counts = {}, [], Counter()
    for index, symbol in enumerate(universe.target_symbols, start=1):
        item = {'symbol': symbol, 'board': scope_board(symbol), 'target_member': True,
            'cache_present': symbol in groups.groups, 'origin_status': 'UNKNOWN',
            'indicator_qualification': 'UNKNOWN',
            'signal_qualification': 'UNKNOWN', 'account_qualification': 'BLOCKED',
            'reasons': ['SECURITY_STATE_NOT_IMPORTED', 'CORPORATE_ACTION_COVERAGE_NOT_PROVEN',
                        'EXECUTION_REFERENCE_NOT_PROVEN', 'TURN_UNAVAILABLE_IF_REQUIRED']}
        if not item['cache_present']:
            item['reasons'].insert(0, 'CACHE_MISSING')
            counts['CACHE_MISSING'] += 1
        else:
            frame = groups.get_group(symbol)
            finite = np.isfinite(frame[['open', 'high', 'low', 'close', 'volume', 'amount']]).all().all()
            valid = (finite and not (frame[['open', 'high', 'low', 'close']] <= 0).any().any()
                     and not (frame[['volume', 'amount']] < 0).any().any())
            item.update(cache_rows=len(frame), cache_start=int(frame.date.min()), cache_end=int(frame.date.max()),
                        cache_price_activity_valid=bool(valid), derived_prev_close_missing=int(frame.prev_close.isna().sum()))
            if not valid:
                item['reasons'].insert(0, 'CACHE_PRICE_OR_ACTIVITY_INVALID')
            path = tdx_root / 'vipdoc' / symbol[-2:].lower() / 'lday' / (symbol[-2:].lower() + symbol[:6] + '.day')
            if not verify_raw:
                item['reasons'].insert(0, 'RAW_ORIGIN_NOT_CHECKED')
            elif not path.is_file() or path.resolve() != path:
                item['reasons'].insert(0, 'RAW_DAY_SOURCE_MISSING_OR_REDIRECTED')
                counts['RAW_DAY_SOURCE_MISSING'] += 1
            else:
                _append(access, {'event': 'RAW_DAY_WINDOW_READ_ATTEMPT', 'source': str(path),
                    'symbol': symbol, 'start': first, 'end': last, 'buffering': 0,
                    'outside_window_date_keys_only': True})
                try:
                    rows, origin = read_tdx_day_window(path, dates)
                    raw = pd.DataFrame(rows, columns=['date', 'open', 'high', 'low', 'close',
                                                     'amount_encoded', 'volume_encoded'])
                    raw['symbol'] = symbol
                    comparison = compare_cache_to_raw_window(frame, raw)
                    # 日期逐股保留在已有cache中，访问账只存边界和计数。
                    origin.pop('physical_value_dates', None)
                    item.update(origin=origin, comparison=comparison,
                        origin_status='EXACT_RAW_WINDOW_MATCH' if comparison['passed'] else 'RAW_CACHE_CONFLICT')
                    _append(access, {'event': 'RAW_DAY_WINDOW_READ_COMPLETED', 'symbol': symbol,
                        'source': str(path), **origin, 'comparison_passed': comparison['passed']})
                    if comparison['passed']:
                        origins[str(path)] = origin['window_sha256']
                        counts['RAW_ORIGIN_PASS'] += 1
                    else:
                        item['reasons'].insert(0, 'RAW_CACHE_CONFLICT')
                        counts['RAW_ORIGIN_CONFLICT'] += 1
                except (ValueError, OSError) as exc:
                    item['reasons'].insert(0, 'RAW_ORIGIN_READ_FAILED')
                    item['origin_error'] = type(exc).__name__ + ':' + str(exc)
                    counts['RAW_ORIGIN_FAILED'] += 1
            if item['origin_status'] == 'EXACT_RAW_WINDOW_MATCH' and valid and unit_ready:
                item['indicator_qualification'] = 'RAW_PRICE_AND_MODELED_UNIT_READY'
                counts['INDICATOR_SOURCE_READY'] += 1
            else:
                item['indicator_qualification'] = 'UNKNOWN'
        if not unit_ready:
            item['reasons'].append('UNIT_EVIDENCE_SCOPE_NOT_BOUND')
        gaps.append(item)
        _append(out / 'ORIGIN_CHECKS.jsonl', item)
        if index % 250 == 0 or index == len(universe.target_symbols):
            print(f'来源核对：{index}/{len(universe.target_symbols)}，原件一致{counts["RAW_ORIGIN_PASS"]}只。', flush=True)
    _write(out / 'PER_STOCK_GAPS.json', gaps)
    calendar = [_day(value) for value in master.get('calendar_dates', [])]
    if not calendar or calendar != sorted(set(calendar)) or calendar[0] != scope['start'] or calendar[-1] != scope['end']:
        raise ValueError('PREPARATION_INDEPENDENT_CALENDAR_INVALID')
    ResearchDataAccessGuard().check_int_iterable(calendar, 'source-master calendar')
    _write(out / 'calendar.json', calendar)
    calendar_hash = _sha(out / 'calendar.json')
    producer = Path(__file__).resolve().parent / 'm6_build_daily_cache.py'
    if not producer.is_file():
        raise ValueError('PREPARATION_CACHE_PRODUCER_SOURCE_NOT_FOUND')
    evidence = {'provider': 'TDX', 'source_id': 'tdx_cache', 'source_sha256': cache_hash,
        'price_mode': 'RAW', 'volume_unit': 'SHARES' if unit_ready else 'UNKNOWN', 'amount_unit': 'CNY',
        'unit_evidence': {'source': 'TDX_DAY_VOLUME_SEMANTICS_V1', 'sha256': unit_hash,
            'grade': unit.get('VOLUME_EVIDENCE_LEVEL', 'UNKNOWN'), 'NOT_FOR_QUALIFICATION': True,
            'original_purpose': unit.get('purpose'), 'volume_multiplier': 1, 'amount_multiplier': 1},
        'transformation_version': 'M6_BUILD_DAILY_CACHE_V1', 'transformation_sha256': _sha(producer),
        'original_source_hashes': origins, 'prev_close_semantics': 'DERIVED_PREVIOUS_VALID_CLOSE',
        'historical_available_at_verified': False,
        'origin_validation_status': 'COMPLETE_FOR_CACHED_TARGETS' if counts['RAW_ORIGIN_PASS'] == sum(s in groups.groups for s in targets) else 'PARTIAL_OR_UNKNOWN'}
    manifest = {'adapter': 'TDX_FULL_UNIVERSE_V1', 'universe_id': 'HISTORICAL_ALL_SUPPORTED_' + str(scope['start']) + '_' + str(scope['end']),
        'universe_scope': scope, 'start': first, 'end': last,
        'master': {'source': universe.master_source, 'records': records,
                   'completeness_evidence': universe.completeness_evidence},
        'files': {'daily_all.parquet': {'kind': 'DAILY', 'format': 'PARQUET', 'start': first, 'end': last,
            'source_id': 'tdx_cache', 'sha256': cache_hash, 'symbols': cached_symbols, 'evidence': evidence},
            'calendar.json': {'kind': 'CALENDAR', 'format': 'JSON', 'start': calendar[0], 'end': calendar[-1],
                'source_id': 'historical_exchange_calendar', 'sha256': calendar_hash,
                'original_source_hashes': {'stage3_master': metadata['source_master_sha256']}},
            'PER_STOCK_GAPS.json': {'kind': 'SOURCE_QUALIFICATION', 'format': 'JSON',
                'start': first, 'end': last, 'source_id': 'source_qualification',
                'sha256': _sha(out / 'PER_STOCK_GAPS.json')}},
        'corporate_actions_complete': False,
        'account_preparation_blockers': ['SECURITY_STATE_NOT_IMPORTED', 'CORPORATE_ACTION_COVERAGE_NOT_PROVEN',
            'EXECUTION_REFERENCE_NOT_PROVEN'],
        'source_qualification_ref': 'PER_STOCK_GAPS.json', 'historical_independence': 'UNKNOWN'}
    manifest['board_policy_binding'] = board_policy_binding_v1()
    manifest['board_policy_identity'] = manifest['board_policy_binding']['identity']
    _write(out / 'manifest.json', manifest)
    summary = {'version': VERSION, 'preparation_complete': True, 'source_cache_sha256': cache_hash,
        'historical_target_count': snapshot['target_count'], 'cached_symbol_count': len(cached_symbols),
        'target_cached_count': snapshot['cached_target_count'],
        'target_missing_cache_count': counts['CACHE_MISSING'],
        'cached_symbols_outside_historical_master_count': len(unknown_cached),
        'by_board': snapshot['by_board'], 'origin_checks': dict(counts),
        'completeness': snapshot['completeness'], 'account_ready': False,
        'independent_confirmation_eligible': False, 'strategy_qualified': False,
        'units_NOT_FOR_QUALIFICATION': True, 'outputs': str(out),
        'manifest_sha256': _sha(out / 'manifest.json'), 'source_catalog_content_read': False,
        'cache_physical_scope': metadata['cache_physical_scope'], 'historical_scope': scope,
        'board_policy_identity': manifest['board_policy_identity']}
    _write(out / 'PREPARATION_COMPLETE.json', summary)
    return summary


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='维护者准备全范围日线与来源证据，不运行策略。')
    parser.add_argument('--source-root', default='E:/llmwiki/trade-system-contract-port-v1')
    parser.add_argument('--tdx-root', default='E:/new_tdx_mock')
    parser.add_argument('--unit-evidence', default='E:/llmwiki/autonomous-strategy-research-v1/execution-data-v1/degraded-volume-semantics-v1/TDX_DAY_VOLUME_SEMANTICS_V1.json')
    parser.add_argument('--output-root', default=str(Path(__file__).resolve().parents[1] / 'data/full_universe'))
    parser.add_argument('--metadata-only', action='store_true', help='不比对DAY原件，结果明确保持来源未知。')
    parser.add_argument('--json', action='store_true', help='输出机器JSON摘要。')
    args = parser.parse_args()
    result = prepare_full_universe_data_v1(source_root=args.source_root, tdx_root=args.tdx_root,
        unit_evidence_path=args.unit_evidence, output_root=args.output_root, verify_raw=not args.metadata_only)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        from chanlun_trader.presentation import ZhCNPresentation
        print(f'数据准备完成：历史目标{result["historical_target_count"]}只，缓存{result["cached_symbol_count"]}只，'
              f'交集{result["target_cached_count"]}只，缺缓存{result["target_missing_cache_count"]}只。')
        print('账户资格：' + ZhCNPresentation.status_name('BLOCKED', include_code=True)
              + '；尚缺动态状态、公司行动、执行参考价及板块政策。')
        print('来源与缺口文件：' + result['outputs'])


if __name__ == '__main__':
    main()
