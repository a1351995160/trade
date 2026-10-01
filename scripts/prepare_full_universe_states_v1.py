"""维护者将有日期分区的历史状态接入全范围；不回填历史可见时间。"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

import pyarrow as pa
import pyarrow.parquet as pq

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / 'src'))

from chanlun_trader.research.guard import ResearchDataAccessGuard
from chanlun_trader.research_factory.research_universe_v1 import (
    BOARDS, _day, canonical_symbol, normalize_board, scope_board,
)
from scripts.prepare_full_universe_data_v1 import _append, _sha, _write, board_policy_binding_v1


VERSION = 'FULL_UNIVERSE_STATE_PREPARATION_V1'
STATE_SOURCE = 'normalized_pit_state_full_universe'
_SCHEMA = pa.schema([
    ('symbol', pa.string()), ('trade_date', pa.int64()),
    ('listed', pa.bool_()), ('delisted', pa.bool_()), ('universe_member', pa.bool_()),
    ('eligibility_status', pa.string()), ('st_status', pa.string()),
    ('suspension_status', pa.string()), ('board', pa.string()),
    ('source_board', pa.string()), ('source', pa.string()),
    ('available_at', pa.string()), ('availability_status', pa.string()),
    ('source_exists', pa.bool_()), ('source_listed', pa.bool_()), ('source_delisted', pa.bool_()),
    ('listing_date', pa.int64()), ('delisting_date', pa.int64()),
    ('listing_date_source', pa.string()), ('lifecycle_original_source', pa.string()),
    ('lifecycle_original_sha256', pa.string()), ('master_observed_at', pa.string()),
    ('source_record_time', pa.string()), ('source_availability_policy', pa.string()),
    ('source_lineage_json', pa.string()), ('source_reason_codes_json', pa.string()),
    ('state_partition_path', pa.string()), ('state_partition_sha256', pa.string()),
])


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _bool(value):
    # 缺值保持未知；不能把 None、0 或字符串变为正常状态。
    return value if type(value) is bool else None


def _safe_file(path):
    if path.resolve() != path or not path.is_file():
        raise ValueError('STATE_SOURCE_MISSING_OR_REDIRECTED:' + str(path))
    return path


def _load_bound_json(path):
    _safe_file(path)
    before = path.stat()
    raw = path.read_bytes()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('STATE_SOURCE_CHANGED_DURING_READ')
    return json.loads(raw.decode('utf-8-sig')), hashlib.sha256(raw).hexdigest()


def _availability(value, day):
    """只用于统计；原字段不改写，模型时点不升级为真实发布证据。"""
    try:
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is None:
            raise ValueError()
        opening = datetime.fromisoformat(f'{str(day)[:4]}-{str(day)[4:6]}-{str(day)[6:]}T09:30:00+08:00')
        closing = opening.replace(hour=15, minute=0)
        return 'AVAILABLE_BY_OPEN' if stamp <= opening else (
            'AVAILABLE_BY_CLOSE' if stamp <= closing else 'NOT_AVAILABLE_BY_CLOSE')
    except (TypeError, ValueError):
        return 'AVAILABILITY_UNKNOWN'


def _read_partition(path, day, targets, masters, master_sha, metadata, access):
    """整日分区须先通过范围守卫；不读取其它日期或逐股全时期状态文件。"""
    ResearchDataAccessGuard().check_date(day, 'normalized state partition filename')
    _safe_file(path)
    before = path.stat()
    _append(access, {'event': 'STATE_PARTITION_READ_ATTEMPT', 'path': str(path),
        'physical_date_partition': day, 'purpose': 'ENGINEERING_STATE_PREPARATION'})
    digest, rows, seen, physical_rows = hashlib.sha256(), [], set(), 0
    with path.open('rb') as stream:
        for line in stream:
            digest.update(line)
            if not line.strip():
                continue
            raw = json.loads(line)
            if not isinstance(raw, dict) or _day(raw.get('trade_date')) != day:
                raise ValueError('STATE_PARTITION_DATE_CONFLICT')
            symbol = canonical_symbol(raw['symbol'])
            if symbol in seen:
                raise ValueError('STATE_PARTITION_DUPLICATE_SYMBOL:' + symbol)
            seen.add(symbol)
            physical_rows += 1
            if symbol not in targets:
                continue
            master = masters.get(symbol, {})
            listed, delisted = _bool(raw.get('listed')), _bool(raw.get('delisted'))
            current_listed = listed and not delisted if listed is not None and delisted is not None else None
            listing_date = _day(master.get('list_date'))
            rows.append({'symbol': symbol, 'trade_date': day,
                'listed': current_listed, 'delisted': delisted,
                'universe_member': _bool(raw.get('universe_member')),
                'eligibility_status': raw.get('eligibility_status', 'UNKNOWN'),
                'st_status': raw.get('st_status', 'UNKNOWN'),
                'suspension_status': raw.get('suspension_status', 'UNKNOWN'),
                'board': normalize_board(raw.get('board', 'UNKNOWN')),
                'source_board': str(raw.get('board', 'UNKNOWN')), 'source': STATE_SOURCE,
                'available_at': raw.get('available_at'), 'availability_status': 'MODELED',
                'source_exists': _bool(raw.get('exists')), 'source_listed': listed,
                'source_delisted': delisted, 'listing_date': listing_date,
                'delisting_date': _day(master.get('delist_date')),
                'listing_date_source': STATE_SOURCE if listing_date is not None else None,
                'lifecycle_original_source': master.get('source'),
                'lifecycle_original_sha256': master_sha,
                'master_observed_at': master.get('source_record_time'),
                'source_record_time': metadata.get('build_timestamp'),
                'source_availability_policy': metadata.get('pit_policy', {}).get('available_at_rule'),
                'source_lineage_json': _json(raw.get('source_lineage', {})),
                'source_reason_codes_json': _json(raw.get('reason_codes', [])),
                'state_partition_path': str(path), 'state_partition_sha256': None})
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('STATE_SOURCE_CHANGED_DURING_READ')
    # 整分区（包括空白行）的SHA；不能把被选择子集称为原件全文。
    source_sha = digest.hexdigest()
    for row in rows:
        row['state_partition_sha256'] = source_sha
    audit = {'path': str(path), 'trade_date': day, 'sha256': source_sha,
        'physical_rows': physical_rows, 'target_rows': len(rows)}
    _append(access, {'event': 'STATE_PARTITION_READ_COMPLETED', **audit})
    return rows, audit


def prepare_full_universe_states_v1(*, source_root, prepared_root,
                                    manifest_name='manifest_v2.json', output_name='states_v1'):
    source, prepared = Path(source_root).absolute(), Path(prepared_root).absolute()
    if source.resolve() != source or prepared.resolve() != prepared:
        raise ValueError('STATE_PREPARATION_ROOT_REDIRECTED')
    if (Path(manifest_name).name != manifest_name or Path(output_name).name != output_name
            or not output_name or output_name in {'.', '..'}):
        raise ValueError('STATE_PREPARATION_PATH_INVALID')
    manifest_path = _safe_file(prepared / manifest_name)
    manifest, manifest_sha = _load_bound_json(manifest_path)
    if manifest.get('adapter') != 'TDX_FULL_UNIVERSE_V1':
        raise ValueError('STATE_PREPARATION_ADAPTER_INVALID')
    if any(item.get('kind') == 'STATES' for item in manifest['files'].values()):
        raise ValueError('STATE_PREPARATION_EXISTING_STATES_NOT_REPLACED')
    scope = manifest.get('universe_scope', {})
    start, end = _day(scope.get('start')), _day(scope.get('end'))
    guard = ResearchDataAccessGuard()
    if start is None or end is None:
        raise ValueError('STATE_PREPARATION_HISTORICAL_SCOPE_REQUIRED')
    guard.check_range(start, end, 'full universe state preparation scope')
    normalized = source / 'data/research/security_state/normalized'
    source_manifest_path = _safe_file(normalized / 'manifest.json')
    metadata, source_manifest_sha = _load_bound_json(source_manifest_path)
    if (metadata.get('dataset_version') != 'PIT_UNIVERSE_DATASET_V2'
            or metadata.get('pit_policy', {}).get('current_status_backfill_used') != 'NO'
            or metadata.get('pit_policy', {}).get('missing_bar_auto_suspension_used') != 'NO'
            or metadata.get('pit_policy', {}).get('available_at_rule') != 'NEXT_SESSION_OPEN'):
        raise ValueError('STATE_PREPARATION_SOURCE_POLICY_UNKNOWN')
    lo, hi = _day(metadata.get('coverage_start')), _day(metadata.get('coverage_end'))
    if lo is None or hi is None or not lo <= start <= end <= hi:
        raise ValueError('STATE_PREPARATION_SOURCE_SCOPE_NOT_COVERED')
    # 仅访问请求内的日期分区；manifest可以包含更长的声明范围，不读取其余状态。
    calendars = [name for name, item in manifest['files'].items() if item.get('kind') == 'CALENDAR']
    if len(calendars) != 1:
        raise ValueError('STATE_PREPARATION_CALENDAR_SOURCE_UNKNOWN')
    calendar_path = _safe_file(prepared / calendars[0])
    calendar_metadata = manifest['files'][calendars[0]]
    guard.check_range(_day(calendar_metadata['start']), _day(calendar_metadata['end']),
                      'full universe state calendar physical range')
    raw_calendar, calendar_sha = _load_bound_json(calendar_path)
    if calendar_sha != calendar_metadata['sha256']:
        raise ValueError('STATE_PREPARATION_CALENDAR_CONTENT_CHANGED')
    dates = [_day(day) for day in raw_calendar]
    if (not dates or dates != sorted(set(dates)) or dates[0] != start or dates[-1] != end):
        raise ValueError('STATE_PREPARATION_CALENDAR_SCOPE_CONFLICT')
    guard.check_int_iterable(dates, 'full universe state calendar')
    targets = {canonical_symbol(row['symbol']) for row in manifest['master']['records']
               if scope_board(row['symbol']) in BOARDS}
    if not targets:
        raise ValueError('STATE_PREPARATION_TARGET_EMPTY')
    gaps_path = manifest.get('source_qualification_ref', 'PER_STOCK_GAPS.json')
    qualification_path = _safe_file(prepared / gaps_path)
    original_gaps, qualification_sha = _load_bound_json(qualification_path)
    registered_qualification = manifest['files'].get(gaps_path)
    if (not registered_qualification or registered_qualification.get('kind') != 'SOURCE_QUALIFICATION'
            or registered_qualification.get('sha256') != qualification_sha):
        raise ValueError('STATE_SOURCE_QUALIFICATION_CONTENT_CHANGED_OR_UNREGISTERED')
    source_map = {canonical_symbol(row['symbol']): row for row in original_gaps}
    if len(source_map) != len(original_gaps) or set(source_map) != targets:
        raise ValueError('STATE_SOURCE_QUALIFICATION_TARGET_MISMATCH')
    master_path = _safe_file(normalized / 'security_master_v2/records.json')
    raw_masters, master_sha = _load_bound_json(master_path)
    masters = {}
    for row in raw_masters:
        symbol = canonical_symbol(row['symbol'])
        if symbol in masters:
            raise ValueError('STATE_MASTER_DUPLICATE_SYMBOL')
        masters[symbol] = row
    out, new_manifest_path = prepared / output_name, prepared / 'manifest_v3.json'
    if out.exists() or out.resolve() != out or new_manifest_path.exists():
        raise ValueError('STATE_PREPARATION_NEW_OUTPUT_REQUIRED')
    out.mkdir()
    _write(out / 'PREPARATION_STARTED.json', {'version': VERSION, 'scope': scope,
        'previous_manifest_sha256': manifest_sha, 'source_master_sha256': master_sha,
        'source_manifest_sha256': source_manifest_sha, 'historical_target_count': len(targets),
        'state_time_policy': 'ORIGINAL_NEXT_SESSION_OPEN_RETAINED_MODELED',
        'current_status_backfill_used': False, 'corporate_actions_complete': False})
    _write(out / 'MASTER_LINEAGE.json', [{'symbol': s, 'present': s in masters,
        'listing_date': masters.get(s, {}).get('list_date'),
        'delisting_date': masters.get(s, {}).get('delist_date'),
        'board': masters.get(s, {}).get('board'), 'source': masters.get(s, {}).get('source'),
        'source_record_time': masters.get(s, {}).get('source_record_time'),
        'evidence_quality': masters.get(s, {}).get('evidence_quality'),
        'original_source_sha256': master_sha} for s in sorted(targets)])
    per_stock = {symbol: Counter() for symbol in targets}
    total, partitions, missing = Counter(), [], []
    output_states, access = out / 'states.parquet', out / 'PHYSICAL_READ_EVENTS.jsonl'
    with pq.ParquetWriter(output_states, _SCHEMA, compression='zstd') as writer:
        for index, day in enumerate(dates, start=1):
            text = str(day)
            path = normalized / 'pit_universe_v2' / f'trade_date={text[:4]}-{text[4:6]}-{text[6:]}.jsonl'
            guard.check_date(day, 'full universe state partition plan')
            if not path.is_file():
                missing.append({'trade_date': day, 'path': str(path)})
                continue
            rows, audit = _read_partition(path, day, targets, masters, master_sha, metadata, access)
            partitions.append(audit)
            availability_cache = {}
            for row in rows:
                symbol, counts = row['symbol'], per_stock[row['symbol']]
                counts['rows'] += 1
                counts['st_known'] += int(row['st_status'] in {'NORMAL', 'ST'})
                counts['suspension_known'] += int(row['suspension_status'] in {'TRADING', 'SUSPENDED'})
                counts['member_known'] += int(type(row['universe_member']) is bool)
                counts['lifecycle_known'] += int(row['listed'] is not None and row['delisted'] is not None)
                counts['board_matches'] += int(row['board'] == scope_board(symbol))
                counts['source_active'] += int(row['listed'] is True and row['delisted'] is False)
                counts['modeled_rows'] += 1
                stamp = row['available_at']
                if stamp not in availability_cache:
                    availability_cache[stamp] = _availability(stamp, day)
                result = availability_cache[stamp]
                counts[result] += 1
                total[result] += 1
                total['rows'] += 1
            writer.write_table(pa.Table.from_pylist(rows, schema=_SCHEMA))
            if index % 50 == 0 or index == len(dates):
                print(f'状态原件核对：{index}/{len(dates)}个交易日，已保存{total["rows"]}条目标状态。', flush=True)
    state_sha = _sha(output_states)
    new_gaps, board_counts = [], {board: Counter() for board in BOARDS}
    for symbol in sorted(targets):
        counts = per_stock[symbol]
        row = deepcopy(source_map[symbol])
        row.setdefault('indicator_qualification', 'UNKNOWN')
        row['state_coverage'] = {**dict(counts), 'expected_rows': len(dates),
                                 'missing_rows': len(dates) - counts['rows']}
        row['reasons'] = [r for r in row['reasons'] if r != 'SECURITY_STATE_NOT_IMPORTED']
        if counts['rows'] < len(dates):
            row['reasons'].append('STATE_ROWS_MISSING')
        if counts['NOT_AVAILABLE_BY_CLOSE']:
            row['reasons'].append('STATE_NOT_AVAILABLE_BY_DECISION_TIME')
        if counts['AVAILABILITY_UNKNOWN'] or counts['st_known'] < counts['rows'] or counts['suspension_known'] < counts['rows']:
            row['reasons'].append('STATE_FIELDS_OR_AVAILABILITY_UNKNOWN')
        row['reasons'] = sorted(set(row['reasons']))
        new_gaps.append(row)
        own = board_counts[scope_board(symbol)]
        own['target_count'] += 1
        own['state_rows'] += counts['rows']
        own['state_missing_rows'] += len(dates) - counts['rows']
        own['state_known_st_rows'] += counts['st_known']
        own['state_known_suspension_rows'] += counts['suspension_known']
        own['state_model_not_available_by_close_rows'] += counts['NOT_AVAILABLE_BY_CLOSE']
        own['symbols_with_all_state_rows'] += int(counts['rows'] == len(dates))
        own['source_unknown_count'] += int(row['indicator_qualification'] != 'RAW_PRICE_AND_MODELED_UNIT_READY')
    _write(out / 'PER_STOCK_GAPS.json', new_gaps)
    _write(out / 'SOURCE_PARTITIONS.json', partitions)
    summary = {'version': VERSION, 'state_preparation_complete': True,
        'historical_target_count': len(targets), 'sessions': len(dates), 'rows': total['rows'],
        'expected_rows': len(targets) * len(dates), 'missing_rows': len(targets) * len(dates) - total['rows'],
        'missing_partitions': missing, 'by_board': {b: dict(v) for b, v in board_counts.items()},
        'availability': {k: total[k] for k in ('AVAILABLE_BY_OPEN', 'AVAILABLE_BY_CLOSE',
                                              'NOT_AVAILABLE_BY_CLOSE', 'AVAILABILITY_UNKNOWN')},
        'availability_status': 'MODELED', 'real_historical_release_evidence': False,
        'source_manifest_sha256': source_manifest_sha, 'source_master_sha256': master_sha,
        'states_sha256': state_sha, 'account_ready': False,
        'corporate_actions_complete': False, 'independent_confirmation_eligible': False,
        'strategy_qualified': False, 'scope': scope,
        'source_lifecycle_translation': 'listed=source_listed AND NOT source_delisted; originals retained',
        'interval_st_suspension_files': 'METADATA_ONLY; states from bounded daily PIT partitions',
        'output_root': str(out)}
    _write(out / 'PREPARATION_COMPLETE.json', summary)
    new = deepcopy(manifest)
    new['files'][output_name + '/states.parquet'] = {'kind': 'STATES', 'format': 'PARQUET',
        'start': start, 'end': end, 'date_column': 'trade_date', 'source_id': STATE_SOURCE,
        'sha256': state_sha, 'original_source_hashes': {row['path']: row['sha256'] for row in partitions},
        'source_manifest_sha256': summary['source_manifest_sha256'], 'source_master_sha256': master_sha,
        'availability_status': 'MODELED', 'pit_available_at_rule': 'NEXT_SESSION_OPEN'}
    old_qualification_files = [name for name, item in new['files'].items()
                              if item.get('kind') == 'SOURCE_QUALIFICATION']
    for name in old_qualification_files:
        del new['files'][name]
    qualification_name = output_name + '/PER_STOCK_GAPS.json'
    new['files'][qualification_name] = {'kind': 'SOURCE_QUALIFICATION', 'format': 'JSON',
        'start': manifest['start'], 'end': manifest['end'], 'source_id': 'source_qualification',
        'sha256': _sha(out / 'PER_STOCK_GAPS.json')}
    new['source_qualification_ref'] = qualification_name
    new['listing_dates'] = {s: _day(masters[s]['list_date']) for s in sorted(targets)
                            if s in masters and masters[s].get('list_date')}
    new['listing_date_sources'] = {s: [STATE_SOURCE] for s in new['listing_dates']}
    state_blockers = []
    if total['NOT_AVAILABLE_BY_CLOSE']:
        state_blockers.append('STATE_NOT_AVAILABLE_BY_DECISION_TIME')
    if any('STATE_FIELDS_OR_AVAILABILITY_UNKNOWN' in row['reasons'] for row in new_gaps):
        state_blockers.append('STATE_FIELDS_OR_AVAILABILITY_UNKNOWN')
    if summary['missing_rows']:
        state_blockers.append('STATE_ROWS_MISSING')
    new['account_preparation_blockers'] = sorted(set([
        r for r in new.get('account_preparation_blockers', [])
        if r not in {'SECURITY_STATE_NOT_IMPORTED', 'BOARD_POLICY_NOT_BOUND'}] + state_blockers))
    new['board_policy_binding'] = board_policy_binding_v1()
    new['board_policy_identity'] = new['board_policy_binding']['identity']
    new['state_preparation_evidence'] = {'summary_path': output_name + '/PREPARATION_COMPLETE.json',
        'summary_sha256': _sha(out / 'PREPARATION_COMPLETE.json'), 'version': VERSION}
    _write(new_manifest_path, new)
    return {**summary, 'manifest_path': str(new_manifest_path), 'manifest_sha256': _sha(new_manifest_path),
            'board_policy_identity': new['board_policy_identity']}


def bind_prepared_board_policy_v1(*, prepared_root, manifest_name='manifest_v3.json'):
    """仅生成新登记，不读取行情/状态内容、不覆盖旧清单，也不提升数据资格。"""
    prepared = Path(prepared_root).absolute()
    if prepared.resolve() != prepared or Path(manifest_name).name != manifest_name:
        raise ValueError('POLICY_BINDING_PATH_INVALID')
    target, evidence_path = prepared / 'manifest_v4.json', prepared / 'IDENTITY_BINDING.json'
    if target.exists() or evidence_path.exists():
        raise ValueError('POLICY_BINDING_NEW_OUTPUT_REQUIRED')
    previous, previous_sha = _load_bound_json(_safe_file(prepared / manifest_name))
    if previous.get('adapter') != 'TDX_FULL_UNIVERSE_V1':
        raise ValueError('POLICY_BINDING_ADAPTER_INVALID')
    guard = ResearchDataAccessGuard()
    guard.check_range(_day(previous['start']), _day(previous['end']), 'policy binding registered cache scope')
    scope = previous['universe_scope']
    guard.check_range(_day(scope['start']), _day(scope['end']), 'policy binding historical target scope')
    binding = board_policy_binding_v1()
    blockers = [r for r in previous.get('account_preparation_blockers', []) if r != 'BOARD_POLICY_NOT_BOUND']
    evidence = {'version': 'FULL_UNIVERSE_POLICY_BINDING_V1',
        'operation': 'EXECUTION_POLICY_IDENTITY_BINDING_ONLY',
        'previous_manifest': manifest_name, 'previous_manifest_sha256': previous_sha,
        'new_manifest': target.name, 'board_policy_binding': binding,
        'registered_data_source_hashes': {name: item['sha256'] for name, item in previous['files'].items()},
        'source_hashes_semantics': 'PREVIOUS_REGISTRATION_PRESERVED; DATA_CONTENT_NOT_REHASHED',
        'source_qualification_ref': previous.get('source_qualification_ref'),
        'corporate_actions_complete': previous.get('corporate_actions_complete'),
        'account_preparation_blockers': blockers, 'data_content_read': False,
        'existing_market_and_state_files_modified': False, 'historical_availability_upgraded': False,
        'data_qualification_reassessment_performed': False,
        'note': '只补登记当前执行制度版本和源码身份；原行情、状态时点、缺口、公司行动及单位资格保持原值。'}
    new = deepcopy(previous)
    new['board_policy_identity'] = binding['identity']
    new['board_policy_binding'] = binding
    new['account_preparation_blockers'] = blockers
    _write(evidence_path, evidence)
    new['policy_binding_evidence'] = {'path': evidence_path.name, 'sha256': _sha(evidence_path)}
    _write(target, new)
    return {'manifest_path': str(target), 'manifest_sha256': _sha(target),
        'identity_binding_path': str(evidence_path), 'identity_binding_sha256': _sha(evidence_path),
        'previous_manifest_sha256': previous_sha, 'board_policy_identity': binding['identity'],
        'account_preparation_blockers': blockers, 'data_qualification_changed': False}


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='接入有来源的历史证券状态，保留模型可见时间与缺口。')
    parser.add_argument('--source-root', default='E:/llmwiki/trade-system-contract-port-v1')
    parser.add_argument('--prepared-root', default=str(PROJECT_ROOT / 'data/full_universe'))
    parser.add_argument('--manifest-name', default='manifest_v2.json')
    parser.add_argument('--bind-policy-only', action='store_true',
                        help='仅给现有清单绑定当前执行制度，输出manifest_v4与IDENTITY_BINDING，不重读行情。')
    args = parser.parse_args()
    if args.bind_policy_only:
        result = bind_prepared_board_policy_v1(prepared_root=args.prepared_root,
                                               manifest_name=args.manifest_name)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return
    result = prepare_full_universe_states_v1(source_root=args.source_root, prepared_root=args.prepared_root,
                                            manifest_name=args.manifest_name)
    print(f'状态接入完成：{result["historical_target_count"]}只目标股票，{result["rows"]}条状态，缺{result["missing_rows"]}条。')
    print(f'原模型时点在当日收盘后：{result["availability"]["NOT_AVAILABLE_BY_CLOSE"]}条；未取得真实历史发布证据。')
    print('账户资格仍被数据条件阻断；清单：' + result['manifest_path'])


if __name__ == '__main__':
    main()
