"""三板块工程夹具，不作为真实数据或盈利证据。"""
from copy import deepcopy

import pandas as pd

from chanlun_trader.research_factory.board_execution_policy_v1 import board_policy_identity
from chanlun_trader.research_factory.universe_account_inputs_v1 import UniverseAccountInputsV1
from test_research_rule_strategy_v2 import payload


SYMBOLS = ['000001.SZ', '600000.SH', '300001.SZ']


def fixture(symbols=None, prices=None, days_count=80):
    symbols = sorted(symbols or SYMBOLS)
    days = [int(d.strftime('%Y%m%d')) for d in pd.bdate_range('2022-01-03', periods=days_count)]
    rows = []
    for symbol in symbols:
        previous = 12.
        for i, day in enumerate(days):
            close = 12. if prices is None or i < 60 else prices[min(i - 60, len(prices) - 1)]
            rows.append({'symbol': symbol, 'date': day, 'open': float(close), 'high': float(close)*1.01,
                         'low': float(close)*.99, 'close': float(close), 'prev_close': previous,
                         'volume': 1_000_000., 'amount': float(close)*1_000_000., 'adjustflag': '3'})
            previous = float(close)
    frame = pd.DataFrame(rows)
    states = pd.DataFrame([{'symbol': symbol, 'effective_date': days[0], 'valid_to': days[-1],
        'listed': True, 'delisted': False, 'universe_member': True, 'eligibility_status': 'ELIGIBLE',
        'st_status': 'NORMAL', 'suspension_status': 'TRADING', 'board':
            'CHINEXT' if symbol.startswith('30') else 'SH_MAIN' if symbol.endswith('SH') else 'SZ_MAIN',
        'listing_date': 20000103, 'listing_date_source': 'synthetic', 'source': 'synthetic',
        'availability_status': 'MODELED'} for symbol in symbols])
    window = {'symbols': symbols, 'calendar': days, 'feature_start': days[0],
              'account_start': days[60], 'account_end': days[-1]}
    bundle = {'profile': 'HISTORICAL_MODELED', 'daily': frame,
        'turn': frame[['symbol', 'date', 'volume']].assign(turn=1.), 'states': states,
        'calendar': days, 'calendar_source': 'synthetic', 'events': [],
        'corporate_actions_complete': True, 'corporate_action_coverage': [
            {'symbols': symbols, 'start': days[0], 'end': days[-1], 'source': 'synthetic',
             'complete': True, 'event_types': ['CASH_DIVIDEND']}],
        'source_hashes': {'synthetic': 'a'*64}, 'universe_identity': 'b'*64, 'source_identity': 'c'*64,
        'board_policy_identity': board_policy_identity(), 'historical_availability': 'MODELED',
        'price_basis': {'execution': 'RAW'}, 'field_sources': {'prev_close': 'synthetic'}}
    prepared = UniverseAccountInputsV1(bundle, window, stage='SCAN')
    return window, prepared.bundle


def proposal(exits=None):
    value = deepcopy(payload())
    value.update(version='RESEARCH_RULE_STRATEGY_V3', indicator_instances=[],
        min_hold_sessions=1, max_hold_sessions=5,
        exits={'execution_mode': 'CLOSE_CONFIRM_NEXT_SESSION_OPEN', 'stop_loss_pct': None,
               'take_profit_pct': None, 'trailing_activate_pct': None, 'trailing_pct': None, **(exits or {})})
    return value
