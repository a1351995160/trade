"""把已登记 BaoStock 原件复制并登记到全范围接口；不运行策略或升级资格。"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from chanlun_trader.research.guard import ResearchDataAccessGuard
from chanlun_trader.research_factory.board_execution_policy_v1 import board_policy_identity
from chanlun_trader.research_factory.research_data_provider_v1 import day
from chanlun_trader.research_factory.research_universe_v1 import identity
from chanlun_trader.research_factory.universe_data_provider_v1 import BAOSTOCK_PROVIDER_VERSION
from chanlun_trader.research_factory.baostock_universe_adapter_v1 import (
    _response_header, guard_baostock_response_header_v1,
)


def _write(path, value):
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def prepare_baostock_universe_data_v1(*, source_root, source_manifest, master_manifest,
                                     output_root, universe_id, feature_start, account_end):
    """整文件范围先守卫，再核验复制；原登记和原始响应始终保留。"""
    source, metadata, master_path, output = map(lambda p: Path(p).absolute(),
        (source_root, source_manifest, master_manifest, output_root))
    for path in (source, metadata, master_path, output):
        if path.resolve() != path:
            raise ValueError('BAOSTOCK_PREPARATION_PATH_REDIRECTED')
    if not source.is_dir() or not metadata.is_file() or not master_path.is_file() or output.exists():
        raise ValueError('BAOSTOCK_PREPARATION_SOURCE_OR_NEW_OUTPUT_REQUIRED')
    start, end = day(feature_start), day(account_end)
    guard = ResearchDataAccessGuard()
    guard.check_range(start, end, 'BaoStock interface preparation')
    raw_manifest, raw_master = metadata.read_bytes(), master_path.read_bytes()
    legacy, master = json.loads(raw_manifest), json.loads(raw_master)
    if legacy.get('adapter') != 'BAOSTOCK_RESPONSE_JSON_V1':
        raise ValueError('BAOSTOCK_LEGACY_MANIFEST_REQUIRED')
    symbols = sorted(legacy['symbols'])
    records = {row['symbol']: row for row in master['master']['records']}
    if not set(symbols) <= set(records):
        raise ValueError('BAOSTOCK_MASTER_SYMBOL_NOT_COVERED')
    selected, files, annual_types = {}, {}, set()
    for name, row in legacy['files'].items():
        kind = ('CALENDAR' if name == 'TRADE_DATES.json' else 'DAILY' if name.startswith('DAILY_')
                else 'ADJUST' if name.startswith('ADJUST_') else 'EVENTS' if name.startswith('DIVIDEND_') else None)
        if kind is None:
            raise ValueError('BAOSTOCK_SOURCE_KIND_UNKNOWN:' + name)
        if kind == 'EVENTS' and not start // 10000 <= int(name.rsplit('_', 1)[1][:-5]) <= end // 10000:
            continue
        guard.check_range(day(row['start']), day(row['end']), 'BaoStock whole original source')
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('DATA_PATH_OUTSIDE_ROOT')
        path = source / relative
        if path.resolve() != path or not path.is_file():
            raise ValueError('BAOSTOCK_ORIGINAL_MISSING_OR_REDIRECTED:' + name)
        # 旧年度登记只写查询年份；新登记按原查询的实际口径声明整件范围。
        header = _response_header(path)
        query = header.get('request', {})
        if kind == 'EVENTS':
            year = int(query['year'])
            year_type = query.get('yearType')
            actual_start, actual_end = year * 10000 + 101, (
                year + (year_type == 'report')) * 10000 + 1231
            annual_types.add(year_type)
        else:
            actual_start, actual_end = day(query['start_date']), day(query['end_date'])
        declared = {**deepcopy(row), 'kind': kind,
            'start': min(day(row['start']), actual_start),
            'end': max(day(row['end']), actual_end)}
        guard_baostock_response_header_v1(path, declared, expected_api={
            'CALENDAR': 'query_trade_dates', 'DAILY': 'query_history_k_data_plus',
            'ADJUST': 'query_adjust_factor', 'EVENTS': 'query_dividend_data'}[kind])
        if (day(row['start']), day(row['end'])) != (declared['start'], declared['end']):
            declared['original_declared_range'] = {'start': row['start'], 'end': row['end']}
        selected[name] = path
        files[name] = {**declared, 'format': 'BAOSTOCK_RESPONSE_JSON', 'source_id': name}
        if kind == 'DAILY':
            files[name]['symbols'] = [name[len('DAILY_'):-5]]
    # 在产生任何副本前，检查应有的年度记录（空响应也必须有原件）。
    for symbol in symbols:
        for name in [f'DAILY_{symbol}.json', f'ADJUST_{symbol}.json', *(
            f'DIVIDEND_{symbol}_{year}.json' for year in range(start // 10000, end // 10000 + 1))]:
            if name not in selected:
                raise ValueError('DATA_SOURCE_NOT_REGISTERED:' + name)
    if 'TRADE_DATES.json' not in selected:
        raise ValueError('DATA_CALENDAR_NOT_REGISTERED')
    if len(annual_types) != 1:
        raise ValueError('BAOSTOCK_DIVIDEND_YEAR_TYPE_CONFLICT')
    output.mkdir(parents=True)
    receipt = {'version': 'BAOSTOCK_UNIVERSE_PREPARATION_V1', 'source_root': str(source),
        'source_manifest': str(metadata), 'source_manifest_sha256': hashlib.sha256(raw_manifest).hexdigest(),
        'master_manifest': str(master_path), 'master_manifest_sha256': hashlib.sha256(raw_master).hexdigest(),
        'copied_sources': [], 'historical_availability': 'MODELED',
        'independent_confirmation_eligible': False, 'strategy_qualified': False}
    _write(output / 'PREPARATION_STARTED.json', receipt)
    for name, path in selected.items():
        before = path.stat()
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != files[name]['sha256']:
            raise ValueError('DATA_SOURCE_CONTENT_CHANGED:' + name)
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        with target.open('rb') as stream:
            copied_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
        after = path.stat()
        if copied_hash != digest or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError('BAOSTOCK_COPY_SOURCE_CHANGED:' + name)
        receipt['copied_sources'].append({'original': str(path), 'copy': str(target), 'sha256': digest})
    own_records = [deepcopy(records[s]) for s in symbols]
    dates = {s: records[s]['listing_date'] for s in symbols if records[s].get('listing_date')}
    manifest = {'adapter': BAOSTOCK_PROVIDER_VERSION, 'universe_id': universe_id,
        'start': start, 'end': end, 'files': files, 'corporate_actions_complete': True,
        'dividend_year_type': next(iter(annual_types)),
        'master': {'records': own_records, 'source': master['master']['source'],
            'completeness_evidence': {'verified': False, 'historical': True,
                'includes_delisted': False, 'scope': {'start': start, 'end': end},
                'description': '明确登记的工程对照范围；不是全市场完整性证明。'}},
        'listing_dates': dates, 'listing_date_sources': {s: 'registered_manifest' for s in dates},
        'listing_metadata_origin': {'path': str(master_path), 'sha256': receipt['master_manifest_sha256'],
                                    'source': master['master']['source']},
        'board_policy_identity': board_policy_identity(),
        'historical_availability': 'MODELED', 'preparation_identity': identity(receipt)}
    _write(output / 'manifest.json', manifest)
    _write(output / 'PREPARATION_COMPLETE.json', receipt)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source-root', 'source-manifest', 'master-manifest', 'output-root', 'universe-id'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--feature-start', type=int, required=True)
    parser.add_argument('--account-end', type=int, required=True)
    args = parser.parse_args(argv)
    result = prepare_baostock_universe_data_v1(**vars(args))
    print(json.dumps({'status': 'ORIGINALS_REGISTERED_REQUIRES_CONTENT_VALIDATION',
                      'universe_id': result['universe_id'], 'target_count': len(result['master']['records'])},
                     ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
