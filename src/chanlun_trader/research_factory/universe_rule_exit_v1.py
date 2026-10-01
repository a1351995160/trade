"""全范围分笔退出：稀疏行情与延迟现金红利，不补造停牌价格。"""
from copy import deepcopy
from dataclasses import asdict
import math

from ..engine.daily_exit_v2 import DailyExitEvaluatorV2

PRICE_POLICY = 'RAW_PLUS_ENTITLED_GROSS_CASH_V1'


class UniverseRuleExitV1:
    def __init__(self, strategy_id, rules):
        self.strategy_id = strategy_id
        self.evaluator = DailyExitEvaluatorV2(strategy_id, 'UNIVERSE_EXIT_V1', rules)
        self.trace = []

    def evaluate(self, ledger, store, calendar, day):
        indices = {int(d): i for i, d in enumerate(calendar)}
        result = []
        for lot in sorted(ledger.lots.values(), key=lambda item: item.lot_id):
            if lot.strategy_id != self.strategy_id or not lot.remaining_quantity:
                continue
            raw = store.get_daily_bar(lot.symbol, day, price_mode='raw')
            if raw is None:
                continue
            if not math.isfinite(float(raw['close'])) or raw['close'] <= 0:
                raise ValueError('UNIVERSE_EXIT_RAW_CLOSE_INVALID')
            entitled, event_ids = 0., []
            for event in ledger.events:
                if event['symbol'] == lot.symbol and event['effective_date'] <= day:
                    rights = ledger.dividend_lots.get(event['event_id'], {}).get(lot.lot_id, 0)
                    if rights:
                        entitled += float(event['terms']['cash_per_share'])
                        event_ids.append(event['event_id'])
            adjusted = {**raw, 'close': float(raw['close']) + entitled}
            class PriceView:
                def get_daily_bar(self, symbol, session, price_mode='raw'):
                    if symbol != lot.symbol or session != day or price_mode != 'raw':
                        raise ValueError('UNIVERSE_EXIT_PRICE_SCOPE')
                    return adjusted
            before = len(self.evaluator.evaluations)
            exits = self.evaluator.evaluate([lot], day, indices[day], PriceView(), session_index_of=indices)
            self.trace.append({'date': day, 'symbol': lot.symbol, 'lot_id': lot.lot_id,
                'entry_price': lot.entry_price, 'raw_close': float(raw['close']),
                'comparison_close': adjusted['close'], 'entitled_gross_cash_per_share': entitled,
                'event_ids': event_ids, 'price_policy': PRICE_POLICY,
                'evaluations': deepcopy(self.evaluator.evaluations[before:]),
                'reasons': [item.reason_code for item in exits]})
            result.extend(exits)
        return result

    def state(self):
        return {'price_policy': PRICE_POLICY, 'trailing': {key: asdict(value) for key, value in
            sorted(self.evaluator._v1.trailing.items())}, 'evaluations': deepcopy(self.trace)}
