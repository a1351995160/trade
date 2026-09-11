"""独立降级合同及无收益可行性；不修改严格输入标准。"""
import math

from .train_account_runner_v1 import FIXED_CONTRACT


CONTRACT = {**FIXED_CONTRACT, 'version': 'DEGRADED_TRAIN_ACCOUNT_BACKTEST_V1',
    'result_type': 'DEGRADED_EXPLORATORY_TRAIN_RESULT',
    'purpose': 'FIXED_REFERENCE_DATA_ACQUISITION_DECISION',
    'labels': ['NOT_FOR_QUALIFICATION', 'INCOMPLETE_UNIVERSE',
               'MODELED_AVAILABILITY', 'CONSERVATIVE_EXECUTION'],
    'availability': 'MODELED_NEXT_SESSION_OPEN',
    'volume_candidates': ['SHARES', 'LOTS_100_SHARES'],
    'capacity': 'INTERSECTION_H1_H2_LAST_PRIOR_TRADABLE_VOLUME',
    'hazards': 'ALL_LOCAL_RECORDS_SIGNAL_THROUGH_EARLIEST_EXIT_INCLUSIVE',
    'thresholds': {'closed_paths': 30, 'entry_dates': 20, 'symbols': 2},
    'feasibility_sizing': 'INITIAL_CASH_THIRD_STATIC_NO_LEDGER_NO_OUTCOME',
    'feasibility_closure': 'EARLIEST_EXIT_FULL_QUANTITY_NO_RETRY_ASSUMED',
    'feasibility_is_not_actual_account_trades': True,
    'expires_at': '2026-09-14T10:05:03+08:00'}


def capacity(encoded, requested, *, schema_proven):
    usable = math.isfinite(encoded) and encoded >= 0 and encoded == int(encoded)
    if not usable:
        return {'encoded_volume': encoded if math.isfinite(encoded) else None,
            'H1_volume_shares': None, 'H2_volume_shares': None, 'H1_cap': None,
            'H2_cap': None, 'conservative_cap': None, 'requested_qty': requested,
            'accepted_qty': 0, 'reason': 'DEGRADED_VOLUME_MODEL_UNUSABLE'}
    # 整数除法落实floor，避免二进制浮点在边界改变容量。
    h1, h2 = int(encoded), int(encoded) * 100
    cap = min(h1 // 10, h2 // 10)
    return {'encoded_volume': encoded, 'H1_volume_shares': h1, 'H2_volume_shares': h2,
        'H1_cap': h1 // 10, 'H2_cap': h2 // 10, 'conservative_cap': cap,
        'requested_qty': requested, 'accepted_qty': min(requested, cap) if schema_proven else 0,
        'reason': ('OK' if cap >= requested else 'PARTICIPATION_LIMIT') if schema_proven
                  else 'DEGRADED_VOLUME_MODEL_UNUSABLE'}


def hazard_overlap(dates, signal_day, exit_day):
    return [d for d in dates if signal_day <= d <= exit_day]


def feasibility_verdict(paths):
    closed = [p for p in paths if p['reason'] == 'COMPLETE_CLOSURE_PATH']
    counts = {'closed_paths': len(closed), 'entry_dates': len({p['entry_date'] for p in closed}),
              'symbols': len({p['symbol'] for p in closed})}
    passed = all(counts[k] >= minimum for k, minimum in CONTRACT['thresholds'].items())
    return {'counts': counts, 'thresholds': CONTRACT['thresholds'], 'passed': passed,
        'classification': 'FEASIBLE_NO_OUTCOME' if passed else 'INSUFFICIENT_EXECUTABLE_EVIDENCE'}
