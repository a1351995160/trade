"""从本地 DAY 原件补建缺失缓存；限窗读取并逐股记录失败，不补造行情。"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from chanlun_trader.research.guard import ResearchDataAccessGuard
from chanlun_trader.research_factory.tdx_research_adapter_v1 import TdxResearchAdapterV1, read_tdx_day_window


def _write(path, value):
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def _sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def prepare_missing_tdx_cache_v1(*, manifest_path, tdx_root, output_name='missing_tdx_v1'):
    path, tdx = Path(manifest_path).absolute(), Path(tdx_root).absolute()
    if path.resolve() != path or tdx.resolve() != tdx or not tdx.is_dir():
        raise ValueError('TDX_CACHE_PREPARATION_PATH_INVALID')
    manifest = json.loads(path.read_text(encoding='utf-8-sig'))
    if manifest.get('adapter') != 'TDX_FULL_UNIVERSE_V1':
        raise ValueError('TDX_MANIFEST_REQUIRED')
    if Path(output_name).name != output_name or output_name in {'', '.', '..'}:
        raise ValueError('TDX_NEW_OUTPUT_NAME_REQUIRED')
    root = path.parent
    output = root / output_name
    if output.exists():
        raise ValueError('TDX_NEW_OUTPUT_DIRECTORY_REQUIRED')
    calendar_name = next(n for n, row in manifest['files'].items() if row['kind'] == 'CALENDAR')
    calendar_metadata = manifest['files'][calendar_name]
    guard = ResearchDataAccessGuard()
    guard.check_range(calendar_metadata['start'], calendar_metadata['end'], 'TDX missing cache calendar')
    if _sha(root / calendar_name) != calendar_metadata['sha256']:
        raise ValueError('DATA_SOURCE_CONTENT_CHANGED:' + calendar_name)
    calendar = json.loads((root / calendar_name).read_text(encoding='utf-8-sig'))
    guard.check_int_iterable(calendar, 'TDX missing cache sessions')
    qualification_name = next(n for n, row in manifest['files'].items() if row['kind'] == 'SOURCE_QUALIFICATION')
    info = manifest['files'][qualification_name]
    guard.check_range(info['start'], info['end'], 'TDX source qualification metadata')
    if _sha(root / qualification_name) != info['sha256']:
        raise ValueError('DATA_SOURCE_CONTENT_CHANGED:' + qualification_name)
    qualification = json.loads((root / qualification_name).read_text(encoding='utf-8-sig'))
    missing = [row for row in qualification if 'CACHE_MISSING' in row['reasons']]
    template = next(row['evidence'] for row in manifest['files'].values() if row['kind'] == 'DAILY')
    output.mkdir()
    frames, records, originals = [], [], {}
    for stock in missing:
        symbol = stock['symbol']
        market = symbol[-2:].lower()
        original = tdx / 'vipdoc' / market / 'lday' / (market + symbol[:6] + '.day')
        record = {'symbol': symbol, 'original': str(original), 'status': 'UNKNOWN'}
        try:
            if original.resolve() != original or not original.is_file():
                raise ValueError('RAW_DAY_SOURCE_MISSING_OR_REDIRECTED')
            rows, origin = read_tdx_day_window(original, calendar)
            if not rows:
                raise ValueError('RAW_DAY_WINDOW_EMPTY')
            frame = pd.DataFrame(rows).rename(columns={'volume_encoded': 'volume', 'amount_encoded': 'amount'})
            frame['symbol'] = symbol
            proof = deepcopy(template)
            proof.update(source_sha256=origin['window_sha256'], source_id='tdx_missing_raw',
                         prev_close_semantics='ABSENT', original_source_hashes={str(original): origin['window_sha256']})
            normalized = TdxResearchAdapterV1().normalize_daily(frame, evidence=proof)
            frames.append(frame)
            originals[str(original)] = origin['window_sha256']
            stock.update(cache_present=True, origin_status='EXACT_RAW_WINDOW_MATCH',
                         indicator_qualification='RAW_PRICE_AND_MODELED_UNIT_READY')
            # 除来源/缓存缺失外的公司行动、状态及参考价缺口不在此处升级。
            stock['reasons'] = [r for r in stock['reasons'] if r not in
                               {'CACHE_MISSING', 'RAW_DAY_SOURCE_MISSING_OR_REDIRECTED'}]
            record.update(status='RAW_WINDOW_CACHE_CREATED', row_count=len(frame),
                          source_window_sha256=origin['window_sha256'], evidence=origin)
            del normalized
        except (ValueError, OSError) as exc:
            record.update(reason=str(exc))
        records.append(record)
    result = deepcopy(manifest)
    if frames:
        cache = output / 'daily.parquet'
        pd.concat(frames, ignore_index=True).to_parquet(cache, index=False)
        proof = deepcopy(template)
        proof.update(source_id='tdx_missing_raw', source_sha256=_sha(cache),
                     original_source_hashes=originals, prev_close_semantics='ABSENT',
                     transformation_version='SCOPED_TDX_DAY_CACHE_V1',
                     transformation_sha256=_sha(Path(__file__)))
        result['files'][cache.relative_to(root).as_posix()] = {'kind': 'DAILY', 'format': 'PARQUET',
            'start': calendar[0], 'end': calendar[-1], 'sha256': _sha(cache),
            'source_id': 'tdx_missing_raw', 'symbols': [r['symbol'] for r in records
                 if r['status'] == 'RAW_WINDOW_CACHE_CREATED'], 'evidence': proof}
    qpath = output / 'source_qualification.json'
    _write(qpath, qualification)
    del result['files'][qualification_name]
    result['files'][qpath.relative_to(root).as_posix()] = {**info, 'sha256': _sha(qpath)}
    target = root / (output_name + '_manifest.json')
    _write(target, result)
    receipt = {'version': 'MISSING_TDX_CACHE_PREPARATION_V1', 'manifest': str(target),
        'requested_count': len(missing), 'created_count': len(frames), 'stocks': records,
        'source_manifest_sha256': _sha(path), 'account_data_ready': False,
        'historical_availability': 'MODELED', 'independent_confirmation_eligible': False}
    _write(output / 'RECEIPT.json', receipt)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest-path', required=True)
    parser.add_argument('--tdx-root', required=True)
    parser.add_argument('--output-name', default='missing_tdx_v1')
    result = prepare_missing_tdx_cache_v1(**vars(parser.parse_args(argv)))
    print(json.dumps({k: result[k] for k in ('manifest', 'requested_count', 'created_count', 'account_data_ready')},
                     ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
