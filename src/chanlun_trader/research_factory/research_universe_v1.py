"""全范围历史证券清单与数据覆盖；目录盘点不授予内容访问权限。"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from typing import Iterable, Mapping

from ..research.guard import ResearchDataAccessGuard


BOARDS = ('SZ_MAIN', 'SH_MAIN', 'CHINEXT')
_BOARD_ALIASES = {'GEM': 'CHINEXT', 'CHINEXT': 'CHINEXT',
                  'SZ_MAIN': 'SZ_MAIN', 'SH_MAIN': 'SH_MAIN',
                  'STAR': 'STAR', 'BSE': 'BSE'}
_SKIP_DIRECTORIES = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', 'tests', 'fixtures',
                     'source-archive', 'test_tmp', '.pytest_cache', 'SteamLibrary'}


def canonical_symbol(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError('UNIVERSE_SYMBOL_INVALID')
    value = value.upper()
    if re.fullmatch(r'(?:SZ|SH)\.\d{6}', value):
        value = value[3:] + '.' + value[:2]
    if not re.fullmatch(r'\d{6}\.(?:SZ|SH|BJ)', value):
        raise ValueError('UNIVERSE_SYMBOL_INVALID')
    return value


def scope_board(value: str) -> str:
    """代码范围仅用于对账；它不能证明历史上市或证券状态。"""
    symbol = canonical_symbol(value)
    if symbol.endswith('.SZ') and symbol.startswith('00'):
        return 'SZ_MAIN'
    if symbol.endswith('.SH') and symbol.startswith('60'):
        return 'SH_MAIN'
    if symbol.endswith('.SZ') and symbol.startswith('30'):
        return 'CHINEXT'
    return 'OUT_OF_SCOPE'


def normalize_board(value: str) -> str:
    return _BOARD_ALIASES.get(str(value).upper(), 'UNKNOWN')


def identity(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def _day(value) -> int | None:
    if value is None or value == '':
        return None
    text = str(value).replace('-', '')
    if not re.fullmatch(r'\d{8}', text):
        raise ValueError('UNIVERSE_DATE_INVALID')
    from datetime import datetime
    try:
        datetime.strptime(text, '%Y%m%d')
    except ValueError as exc:
        raise ValueError('UNIVERSE_DATE_INVALID') from exc
    return int(text)


def inventory_research_paths(roots: Iterable[str | Path]) -> dict:
    """只枚举文件名/stat；不读取行情、标签、收益或封存内容。"""
    resolved, skipped, entries = {}, [], {}
    for root in roots:
        path = Path(root).absolute()
        if not path.exists() or not path.is_dir():
            skipped.append({'root': str(path), 'reason': 'ROOT_NOT_FOUND'})
            continue
        if path.is_symlink() or path.resolve() != path:
            skipped.append({'root': str(path), 'reason': 'ROOT_REDIRECTED'})
            continue
        resolved[str(path).casefold()] = path
    # 父目录覆盖子目录，避免同一树重复遍历。
    unique = []
    for path in sorted(resolved.values(), key=lambda p: (len(p.parts), str(p).casefold())):
        if not any(path.is_relative_to(parent) for parent in unique):
            unique.append(path)
    import os
    def failed(error):
        skipped.append({'root': str(error.filename), 'reason': 'METADATA_ACCESS_FAILED'})
    def redirected(path):
        if path.is_symlink():
            return True
        is_junction = getattr(path, 'is_junction', None)
        if is_junction is not None:
            return is_junction()
        # Python 3.11 Windows没有is_junction；lstat重解析标记避免遍历别名。
        return bool(getattr(path.lstat(), 'st_file_attributes', 0) & 0x400)
    def child_allowed(directory, name):
        if name in _SKIP_DIRECTORIES:
            return False
        path = Path(directory) / name
        try:
            return not redirected(path)
        except OSError:
            skipped.append({'root': str(path), 'reason': 'METADATA_ACCESS_FAILED'})
            return False
    for root in unique:
        for directory, children, files in os.walk(root, followlinks=False, onerror=failed):
            children[:] = sorted(name for name in children if child_allowed(directory, name))
            for name in sorted(files):
                path = Path(directory) / name
                lower = name.lower()
                suffix = path.suffix.lower()
                if suffix not in {'.parquet', '.day', '.lc5', '.json', '.csv'} and lower != 'gbbq':
                    continue
                if suffix in {'.json', '.csv'} and not any(word in lower for word in
                        ('daily', 'manifest', 'security', 'calendar', 'trade_dates', 'dividend',
                         'adjust', 'coverage', 'capability', 'turn', 'state', 'listing', 'universe', 'event')):
                    continue
                try:
                    if redirected(path):
                        skipped.append({'root': str(path), 'reason': 'SOURCE_REDIRECTED'})
                        continue
                    stat = path.stat()
                except OSError:
                    skipped.append({'root': str(path), 'reason': 'METADATA_ACCESS_FAILED'})
                    continue
                prohibited = any(word in lower for word in ('label', 'return', 'factor_table'))
                entries[str(path).casefold()] = {'path': str(path), 'size': stat.st_size,
                    'mtime_ns': stat.st_mtime_ns, 'format': suffix.lstrip('.') or 'gbbq',
                    'content_read': False, 'signal_input_allowed': not prohibited,
                    'reason': 'OUTCOME_SOURCE_METADATA_ONLY' if prohibited else 'CONTENT_NOT_QUALIFIED'}
    items = [entries[key] for key in sorted(entries)]
    return {'schema_version': 'RESEARCH_PATH_INVENTORY_V1', 'content_read': False,
            'roots': [str(path) for path in unique], 'files': items, 'skipped': skipped,
            'inventory_identity': identity(items)}


class ResearchUniverseV1:
    """历史目标清单与缓存覆盖分开；缺失证券始终留在目标分母。"""

    def __init__(self, master_records: Iterable[Mapping], cache_records: Iterable[Mapping] = (),
                 *, master_source: str, completeness_evidence: Mapping | None = None):
        if not isinstance(master_source, str) or not master_source:
            raise ValueError('UNIVERSE_MASTER_SOURCE_REQUIRED')
        self.master_source = master_source
        self.completeness_evidence = deepcopy(dict(completeness_evidence or {}))
        records = {}
        for raw in master_records:
            row = deepcopy(dict(raw))
            symbol = canonical_symbol(row['symbol'])
            row['symbol'] = symbol
            row['original_board'] = row.get('board', 'UNKNOWN')
            row['board'] = normalize_board(row.get('board', 'UNKNOWN'))
            row['scope_board'] = scope_board(symbol)
            row['listing_date'] = _day(row.get('listing_date', row.get('listed_date', row.get('list_date'))))
            row['delisting_date'] = _day(row.get('delisting_date', row.get('delisted_date', row.get('delist_date'))))
            if row['listing_date'] and row['delisting_date'] and row['listing_date'] >= row['delisting_date']:
                raise ValueError('UNIVERSE_LIFECYCLE_CONFLICT')
            if symbol in records and records[symbol] != row:
                raise ValueError('UNIVERSE_MASTER_CONFLICT:' + symbol)
            records[symbol] = row
        if not records:
            raise ValueError('UNIVERSE_MASTER_EMPTY')
        self.records = records
        self.cache = {}
        for raw in cache_records:
            row = deepcopy(dict(raw))
            symbol = canonical_symbol(row['symbol'])
            row['symbol'] = symbol
            # 相同原件的不同根别名不会虚增覆盖。
            source = row.get('source_sha256') or row.get('source_id') or identity(row)
            self.cache.setdefault(symbol, {})[str(source)] = row
        self.universe_identity = identity({'version': 'RESEARCH_UNIVERSE_V1',
            'master_source': master_source, 'records': [records[s] for s in sorted(records)],
            'completeness_evidence': self.completeness_evidence})
        self.coverage_identity = identity({s: list(sorted(rows.values(), key=identity))
                                          for s, rows in sorted(self.cache.items())})

    @property
    def target_symbols(self) -> list[str]:
        # 归属未知或冲突仍在目标中，准备时须拒绝；不能据此缩小分母。
        return sorted(s for s in self.records if scope_board(s) in BOARDS)

    def snapshot(self) -> dict:
        targets = self.target_symbols
        target_set = set(targets)
        by_board = {board: {'target_count': 0, 'cached_count': 0, 'identity_unknown_count': 0,
                            'identity_conflict_count': 0} for board in BOARDS}
        rows = []
        for symbol in sorted(set(self.records) | set(self.cache)):
            master = self.records.get(symbol)
            board = scope_board(symbol)
            status = 'OUT_OF_SCOPE' if board not in BOARDS else ('MASTER_MISSING' if master is None else
                'BOARD_UNKNOWN' if master['board'] == 'UNKNOWN' else
                'BOARD_CONFLICT' if master['board'] != board else
                'CACHE_FOUND' if symbol in self.cache else 'CACHE_MISSING')
            if symbol in target_set:
                item = by_board[board]
                item['target_count'] += 1
                item['cached_count'] += int(symbol in self.cache)
                item['identity_unknown_count'] += int(status == 'BOARD_UNKNOWN')
                item['identity_conflict_count'] += int(status == 'BOARD_CONFLICT')
            rows.append({'symbol': symbol, 'board': board, 'master_present': master is not None,
                'cache_present': symbol in self.cache, 'status': status,
                'data_qualification': 'CONTENT_NOT_VALIDATED',
                'st_status': 'UNKNOWN', 'suspension_status': 'UNKNOWN'})
        proof = self.completeness_evidence
        source_hashes = proof.get('source_hashes')
        bound_sources = (isinstance(source_hashes, dict) and bool(source_hashes)
            and all(isinstance(key, str) and key and isinstance(value, str)
                    and re.fullmatch(r'[0-9a-f]{64}', value) for key, value in source_hashes.items()))
        verified = (proof.get('verified') is True and proof.get('includes_delisted') is True
                    and proof.get('historical') is True and bound_sources
                    and set(proof.get('boards', [])) == set(BOARDS))
        return {'schema_version': 'RESEARCH_UNIVERSE_V1', 'universe_identity': self.universe_identity,
            'coverage_identity': self.coverage_identity, 'master_source': self.master_source,
            'target_symbols': targets, 'target_count': len(targets),
            'cached_target_count': sum(s in self.cache for s in targets),
            'cached_symbol_count': len(self.cache), 'by_board': by_board, 'securities': rows,
            'completeness': 'HISTORICAL_MASTER_VERIFIED' if verified else 'UNIVERSE_COMPLETENESS_UNKNOWN',
            'completeness_evidence': deepcopy(proof), 'content_read': False,
            'historical_independence': 'UNKNOWN', 'independent_confirmation_eligible': False}

    def qualification_at(self, trade_date: int) -> list[dict]:
        date = _day(trade_date)
        if date is None:
            raise ValueError('UNIVERSE_DATE_INVALID')
        ResearchDataAccessGuard().check_date(date, 'universe qualification')
        result = []
        for symbol in self.target_symbols:
            row = self.records[symbol]
            start, end = row['listing_date'], row['delisting_date']
            if row['board'] == 'UNKNOWN':
                status = 'BOARD_UNKNOWN'
            elif row['board'] != row['scope_board']:
                status = 'BOARD_CONFLICT'
            elif start is None:
                status = 'LIFECYCLE_UNKNOWN'
            elif date < start:
                status = 'NOT_LISTED'
            elif end is not None and date >= end:
                status = 'DELISTED'
            else:
                status = 'LISTED_STATUS_REQUIRES_DAILY_EVIDENCE'
            result.append({'symbol': symbol, 'trade_date': date, 'board': row['board'],
                'lifecycle_status': status, 'st_status': 'UNKNOWN', 'suspension_status': 'UNKNOWN',
                'scan_eligible': False, 'execution_eligible': False,
                'reason': status if status != 'LISTED_STATUS_REQUIRES_DAILY_EVIDENCE' else 'STATE_NOT_QUALIFIED',
                'source': self.master_source})
        return result

    def coverage_queue(self, completed: Iterable[str] = (), *, batch_size: int = 100) -> list[list[str]]:
        if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size <= 0:
            raise ValueError('UNIVERSE_BATCH_SIZE_INVALID')
        completed = [canonical_symbol(s) for s in completed]
        if not set(completed) <= set(self.target_symbols):
            raise ValueError('UNIVERSE_CHECKPOINT_OUTSIDE_TARGET')
        completed_set = set(completed)
        remaining = [s for s in self.target_symbols if s not in completed_set]
        return [remaining[i:i + batch_size] for i in range(0, len(remaining), batch_size)]

    def chinese_summary(self) -> str:
        value = self.snapshot()
        from ..presentation import ZhCNPresentation
        status = ZhCNPresentation.state_name(value['completeness'])
        counts = '、'.join(f'{board}：{value["by_board"][board]["target_count"]}只' for board in BOARDS)
        return (f'目标股票{value["target_count"]}只（{counts}），已找到缓存'
                f'{value["cached_target_count"]}只。内容尚未验证；历史证券清单完整性：'
                f'{status}。缺缓存与未知状态不能视为没有买卖信号。')
