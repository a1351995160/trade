"""固定五候选的504日历史建模研究；不授予独立确认或Paper资格。"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from pathlib import Path
import sys
import time

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from chanlun_trader.research_factory.bounded_research_v1 import _put, _read, file_hash
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.etf_account_governance_v1 import StrategyBatchGovernanceV1
from chanlun_trader.research_factory.strategy_qualification_v1 import BoundedStrategyArchiveV1
from chanlun_trader.research_factory.formal_statistics_v2 import family_test, METHOD_HASH
from chanlun_trader.research_factory.historical_process_v1 import (
    historical_input_identity, prepare_historical_account, run_historical_account,
)

SYMBOLS = ['000001.SZ', '600000.SH']
TAX_SOURCE = 'https://www.chinatax.gov.cn/n810341/n810755/c1797427/content.html'


def response(path, api):
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    if (value['provider'] != 'BaoStock' or value['api'] != api or value['error_code'] != '0'
            or value.get('mode', 'HISTORICAL_MODELED' if api == 'query_adjust_factor' else None) != 'HISTORICAL_MODELED'
            or value['historical_available_at_verified'] is not False):
        raise ValueError('HISTORICAL_RESPONSE_SOURCE_INVALID')
    if any(len(row) != len(value['fields']) for row in value['raw_rows']):
        raise ValueError('HISTORICAL_RESPONSE_SHAPE_INVALID')
    digest = hashlib.sha256(json.dumps(value['raw_rows'], ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()
    if digest != value['raw_rows_sha256']:
        raise ValueError('HISTORICAL_RESPONSE_HASH_INVALID')
    return value, pd.DataFrame(value['raw_rows'], columns=value['fields'])


def day(value):
    return int(value.replace('-', ''))


def build_bundle(data_root, *, symbols=None):
    """先固定真实日历，再验原始响应与公司行动，不访问策略表现。"""
    symbols = SYMBOLS if symbols is None else symbols
    if (not isinstance(symbols, (list, tuple)) or not symbols or len(symbols) != len(set(symbols))
            or any(not isinstance(s, str) or not re.fullmatch(r'(00\d{4}\.SZ|60\d{4}\.SH)', s) for s in symbols)):
        raise ValueError('HISTORICAL_MAIN_BOARD_SYMBOLS_INVALID')
    symbols = sorted(symbols)
    raw_calendar, calendar = response(data_root / 'TRADE_DATES.json', 'query_trade_dates')
    expected_dates = {'start_date': '2022-01-01', 'end_date': '2024-07-31'}
    if raw_calendar['request'] != expected_dates:
        raise ValueError('HISTORICAL_CALENDAR_REQUEST_CHANGED')
    natural_days = pd.date_range('2022-01-01', '2024-07-31').strftime('%Y-%m-%d').tolist()
    if (calendar.calendar_date.tolist() != natural_days
            or not calendar.is_trading_day.isin(['0', '1']).all()):
        raise ValueError('HISTORICAL_CALENDAR_RESPONSE_INCOMPLETE')
    dates = [day(x) for x in calendar.loc[calendar.is_trading_day == '1', 'calendar_date']]
    if dates != sorted(set(dates)) or len(dates) < 564 or dates[-1] != 20240731:
        raise ValueError('HISTORICAL_CALENDAR_INCOMPLETE')
    dates = dates[-564:]
    window = dict(symbols=symbols, feature_start=dates[0], account_start=dates[60],
                  account_end=dates[-1], calendar=dates)
    frames, turns, states, events, action_checks = [], [], [], [], []
    sources = {'execution_profile': 'HISTORICAL_MODELED',
               'TRADE_DATES.json': file_hash(data_root / 'TRADE_DATES.json')}
    for symbol in symbols:
        code = ('sz.' if symbol.endswith('SZ') else 'sh.') + symbol[:6]
        name = f'DAILY_{symbol}.json'
        raw, frame = response(data_root / name, 'query_history_k_data_plus')
        if any(raw['request'].get(k) != v for k, v in
               {**expected_dates, 'code': code, 'frequency': 'd', 'adjustflag': '3'}.items()):
            raise ValueError('HISTORICAL_DAILY_REQUEST_CHANGED')
        sources[name] = file_hash(data_root / name)
        frame['date'] = frame.date.map(day)
        frame = frame.loc[frame.date.isin(dates)].copy().sort_values('date')
        if (frame.date.tolist() != dates or not frame.code.eq(code).all()
                or not frame.adjustflag.eq('3').all()
                or not frame.tradestatus.eq('1').all() or not frame.isST.isin(['0', '1']).all()):
            raise ValueError('HISTORICAL_DAILY_COVERAGE_OR_STATE_INVALID')
        for key in ['open', 'high', 'low', 'close', 'preclose', 'volume', 'amount', 'turn']:
            frame[key] = pd.to_numeric(frame[key], errors='raise')
        if not np.isfinite(frame[['open', 'high', 'low', 'close', 'preclose', 'volume', 'amount', 'turn']]).all().all():
            raise ValueError('HISTORICAL_NONFINITE_DATA')
        if (frame.turn < 0).any() or (frame.volume <= 0).any():
            raise ValueError('HISTORICAL_TURN_OR_VOLUME_INVALID')
        frame['symbol'] = symbol
        frame = frame.rename(columns={'preclose': 'prev_close'})
        frames.append(frame[['symbol', 'date', 'open', 'high', 'low', 'close', 'prev_close', 'volume', 'amount', 'adjustflag']])
        turn = frame[['symbol', 'date', 'volume', 'turn']].copy()
        turn['tradestatus'] = 1
        turns.append(turn)
        for row in frame.loc[frame.date >= dates[60]].to_dict('records'):
            states.append(dict(symbol=symbol, trade_date=row['date'], listed=True, delisted=False,
                universe_member=True, eligibility_status='INELIGIBLE' if row['isST'] == '1' else 'ELIGIBLE',
                st_status='ST' if row['isST'] == '1' else 'NORMAL', suspension_status='TRADING',
                board='SZ_MAIN' if symbol.endswith('SZ') else 'SH_MAIN'))
        for year in (2022, 2023, 2024):
            name = f'DIVIDEND_{symbol}_{year}.json'
            raw, actions = response(data_root / name, 'query_dividend_data')
            if raw['request'] != dict(code=code, year=str(year), yearType='report'):
                raise ValueError('HISTORICAL_ACTION_REQUEST_CHANGED')
            sources[name] = file_hash(data_root / name)
            for row in actions.to_dict('records'):
                effective = day(row['dividOperateDate'])
                if not dates[0] <= effective <= dates[-1]:
                    continue
                record = day(row['dividRegistDate'])
                if (row['code'] != code or float(row['dividStocksPs'] or 0) != 0
                        or float(row['dividReserveToStockPs'] or 0) != 0 or row['dividStockMarketDate']
                        or day(row['dividPayDate']) != effective or record not in dates
                        or day(row['dividPlanDate']) > record):
                    raise ValueError('HISTORICAL_NONCASH_OR_UNSUPPORTED_ACTION')
                events.append(dict(event_id=f'{symbol}:{effective}:CASH', symbol=symbol,
                    event_type='CASH_DIVIDEND', record_date=record, effective_date=effective,
                    payment_date=effective, units='CNY_PER_SHARE',
                    source=f'BaoStock:query_dividend_data:{code}:{year}:report:sha256:{sources[name]}',
                    source_published_at=row['dividPlanDate'],
                    terms={'cash_per_share': float(row['dividCashPsBeforeTax']),
                           'tax_rule': {'kind': 'DEFERRED_INDIVIDUAL_2015_101', 'source': TAX_SOURCE}}))
        own = {e['effective_date']: e for e in events if e['symbol'] == symbol}
        if len(own) != len([e for e in events if e['symbol'] == symbol]):
            raise ValueError('HISTORICAL_DUPLICATE_ACTION')
        name = f'ADJUST_{symbol}.json'
        raw, factors = response(data_root / name, 'query_adjust_factor')
        if raw['request'] != {**expected_dates, 'code': code} or not factors.code.eq(code).all():
            raise ValueError('HISTORICAL_ADJUST_REQUEST_CHANGED')
        factor_dates = [day(value) for value in factors.dividOperateDate]
        if (factor_dates != sorted(set(factor_dates))
                or any(not 20220101 <= d <= 20240731 for d in factor_dates)
                or {d for d in factor_dates if dates[0] <= d <= dates[-1]} != set(own)):
            raise ValueError('HISTORICAL_CORPORATE_COVERAGE_CONFLICT')
        sources[name] = file_hash(data_root / name)
        rows = frame.to_dict('records')
        for before, current in zip(rows, rows[1:]):
            action = own.get(current['date'])
            cash = action['terms']['cash_per_share'] if action else 0.
            delta = current['prev_close'] - (before['close'] - cash)
            if abs(delta) > .011 or (action and action['record_date'] != before['date']):
                raise ValueError(f'HISTORICAL_UNEXPLAINED_PRICE_REFERENCE:{symbol}:{current["date"]}')
            if action:
                action_checks.append({'event_id': action['event_id'], 'reference_delta': delta})
    bundle = dict(profile='HISTORICAL_MODELED', daily=pd.concat(frames, ignore_index=True),
        turn=pd.concat(turns, ignore_index=True), states=pd.DataFrame(states), events=events,
        calendar=dates, corporate_actions_complete=True, source_hashes=sources,
        open_snapshots=[], close_snapshots=[])
    return window, bundle, {'cash_action_reference_checks': action_checks,
        'coverage_basis': 'VENDOR_CASH_ACTION_RECORDS_AND_EVERY_RAW_PRECLOSE_TRANSITION',
        'publication_dates': 'VENDOR_ASSERTED_NOT_INDEPENDENTLY_VERIFIED',
        'historical_available_at_verified': False}


def summarize(results, window):
    expected = {'BENCHMARK_BASE'} | {f'CANDIDATE_{i:03d}_{cost}'
                                    for i in range(1, 6) for cost in ('BASE', 'STRESS')}
    if set(results) != expected:
        raise ValueError('RESEARCH_FULL_ELEVEN_ACCOUNT_FAMILY_REQUIRED')
    benchmark = results['BENCHMARK_BASE']
    days = window['calendar'][60:]
    if any([r['date'] for r in result['daily_returns']] != days for result in results.values()):
        raise ValueError('RESEARCH_PAIRED_CALENDAR_MISMATCH')
    excess, candidates = {}, {}
    br = np.array([r['net_return'] for r in benchmark['daily_returns']])
    for i in range(1, 6):
        key = f'CANDIDATE_{i:03d}'
        base, stress = results[key + '_BASE'], results[key + '_STRESS']
        x = np.array([r['net_return'] for r in base['daily_returns']]) - br
        excess[key] = x
        def correlation(lag):
            a, b = x[:-lag], x[lag:]
            return None if min(a.std(), b.std()) == 0 else float(np.corrcoef(a, b)[0, 1])
        candidates[key] = {'base': base['metrics'], 'stress': stress['metrics'],
            'mean_daily_net_excess': float(x.mean()),
            'sum_daily_excess_each_half': [float(part.sum()) for part in np.split(x, 2)],
            'descriptive_autocorrelation': {str(lag): correlation(lag) for lag in (1, 5, 20, 63)},
            'group_standard_deviations': x.reshape(8, 63).std(axis=1, ddof=1).tolist(),
            'strategy_qualified': False}
    return {'status': 'HISTORICAL_PROCESS_RESEARCH_COMPLETED', 'account_count': len(results),
        'account_days_each': len(days), 'benchmark': benchmark['metrics'], 'candidates': candidates,
        'statistics_diagnostic_only': family_test(excess, alpha=.0125), 'method_hash': METHOD_HASH,
        'method_applicability': 'NOT_ESTABLISHED_BY_DESCRIPTIVE_DIAGNOSTICS',
        'independent_evidence': False, 'strategy_qualified': False, 'formal_budget_consumed': 0,
        'interpretation': 'EXPOSED_HISTORICAL_MODEL_RESEARCH_NOT_CONFIRMATION_OR_PAPER'}


def execute(data_root, archive_root, output):
    from chanlun_trader.execution_policy import ExecutionPolicy, validate_research_root
    validate_research_root(output.parent, ExecutionPolicy())
    output.mkdir(exist_ok=True)
    validate_research_root(output, ExecutionPolicy())
    if any(p.resolve() != p for p in (data_root, archive_root, output)):
        raise ValueError('RESEARCH_PATH_REDIRECTED')
    window, bundle, coverage = build_bundle(data_root)
    identity = historical_input_identity(bundle, window)
    archive = BoundedStrategyArchiveV1(archive_root)
    proposals, origins = {}, {}
    for path in sorted(archive_root.glob('*/ARCHIVE.json')):
        item = archive.load(path.parent.name)
        key = item['origin']['candidate_id']
        if key in proposals:
            raise ValueError('RESEARCH_DUPLICATE_CANDIDATE')
        proposals[key] = item['proposal']
        origins[key] = {k: item[k] for k in ('strategy_id', 'archive_hash', 'rule_identity')}
    if set(proposals) != {f'CANDIDATE_{i:03d}' for i in range(1, 6)}:
        raise ValueError('RESEARCH_COMPLETE_FIVE_CANDIDATES_REQUIRED')
    jobs = {'BENCHMARK_BASE': (None, 'BASE')}
    jobs.update({f'{key}_{cost}': (proposals[key], cost) for key in sorted(proposals) for cost in ('BASE', 'STRESS')})
    plans = {name: prepare_historical_account(proposal, strategy_id=name, window=window, costs=cost)
             for name, (proposal, cost) in jobs.items()}
    for key, origin in origins.items():
        if any(plans[key + '_' + cost]['strategy']['parameters']['rule_identity'] != origin['rule_identity']
               for cost in ('BASE', 'STRESS')):
            raise ValueError('RESEARCH_ARCHIVED_RULE_CHANGED')
    scope = dict(window=window, input_identity=identity, origins=origins, plans=plans,
                 coverage=coverage, source_hashes=bundle['source_hashes'],
                 runner_sha256=file_hash(Path(__file__)),
                 purpose='FIXED_EXISTING_RULE_PROCESS_RESEARCH_NOT_NEW_SEARCH', strategy_qualified=False)
    _put(output / 'SCOPE.json', scope)
    governance = StrategyBatchGovernanceV1(output / 'governance', output / 'search_budget_registry.json',
                                         'REAL_PROCESS_504_RESEARCH_20260926', plans)
    if not governance.receipt_path.exists():
        governance.confirm({'origin': 'USER_EXPLICIT_CURRENT_TASK',
            'statement': '继续吧；常规实现、数据核查、回测、修复和验证自主推进长历史账户完整验证',
            'authorization_interpretation': 'CURRENT_TASK_COVERS_FIXED_PROCESS_RESEARCH_NOT_FORMAL_CONFIRMATION',
            'approved_plan_ids': {name: plan['plan_id'] for name, plan in plans.items()},
            'expires_at': (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()},
            {'input_identity': identity, 'novelty': {name: {'allowed': True, 'plan_id': plan['plan_id'],
                'reason': 'EXISTING_FROZEN_RULE_REUSE_FOR_AUTHORIZED_PROCESS_RESEARCH_NOT_NOVEL_CANDIDATE'}
                for name, plan in plans.items()}})
    results = {}
    for name, (proposal, costs) in jobs.items():
        path = output / (name + '_RESULT.json')
        if path.exists():
            result = _read(path)
            settlement = json.loads((governance.root / (name + '_SETTLEMENT.json')).read_text(encoding='utf-8'))
            if not settlement['completed'] or settlement['result_sha256'] != stable_hash(result):
                raise ValueError('RESEARCH_SETTLEMENT_CONFLICT')
            results[name] = result
            continue
        started = time.monotonic()
        governance.start(name)
        try:
            kwargs = dict(strategy_id=name, bundle=bundle, window=window, costs=costs,
                          input_identity=identity, active_check=lambda: governance.active_execution(name))
            result = run_historical_account(proposal, **kwargs)
            repeated = run_historical_account(proposal, **kwargs)
            if stable_hash(result) != stable_hash(repeated):
                raise ValueError('RESEARCH_FULL_REPLAY_MISMATCH')
            _put(path, result)
            governance.settle(name, completed=True, seconds=time.monotonic() - started,
                              result_hash=stable_hash(result))
            results[name] = result
            print(json.dumps({'account': name, 'status': 'REPLAY_IDENTICAL',
                              'days': len(result['daily_returns'])}), flush=True)
        except Exception as error:
            governance.settle(name, completed=False, seconds=time.monotonic() - started,
                              result_hash=None, error=type(error).__name__ + ':' + str(error))
            raise
    summary = summarize(results, window)
    _put(output / 'SUMMARY.json', summary)
    print(json.dumps({k: summary[k] for k in ('status', 'account_count', 'strategy_qualified')}), flush=True)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', required=True, type=Path)
    parser.add_argument('--archive-root', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    execute(args.data_root.absolute(), args.archive_root.absolute(), args.output.absolute())
