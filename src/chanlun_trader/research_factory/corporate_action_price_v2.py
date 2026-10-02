"""送转与现金分红的同日价格条款；仅转换特征，不改变成交原价。"""
from collections import defaultdict
from fractions import Fraction
import math


def grouped_price_actions_v2(events):
    groups = defaultdict(list)
    seen = set()
    for event in events:
        key = event.get('event_id')
        if not key or key in seen:
            raise ValueError('CAUSAL_PRICE_ACTION_ID_INVALID')
        seen.add(key)
        groups[(event['symbol'], int(event['effective_date']))].append(event)
    result = []
    for (symbol, day), own in sorted(groups.items()):
        records = {int(e['record_date']) for e in own}
        if len(records) != 1:
            raise ValueError('CAUSAL_PRICE_SAME_DAY_RECORD_CONFLICT')
        cash, account_cash, ratio, shares = 0., 0., Fraction(1), 0
        for event in own:
            if event['event_type'] == 'CASH_DIVIDEND':
                value = float(event['terms']['cash_per_share'])
                if not math.isfinite(value) or value <= 0:
                    raise ValueError('CAUSAL_PRICE_CASH_TERMS_INVALID')
                account_cash += value
                price_terms = event.get('price_terms')
                if price_terms is not None:
                    price_value = price_terms.get('cash_per_share')
                    if (isinstance(price_value, bool) or not isinstance(price_value, (float, int))
                            or not math.isfinite(price_value) or price_value < 0
                            or not price_terms.get('source') or not price_terms.get('evidence_quote')):
                        raise ValueError('CAUSAL_PRICE_REFERENCE_TERMS_INVALID')
                    value = price_value
                cash += value
            elif event['event_type'] in {'BONUS', 'CAPITALIZATION'}:
                terms = event['terms']
                n, d = terms['ratio_numerator'], terms['ratio_denominator']
                if (type(n) is not int or type(d) is not int or d <= 0 or n <= d
                        or event['units'] != 'NEW_SHARES_PER_OLD_SHARE'):
                    raise ValueError('CAUSAL_PRICE_SHARE_TERMS_INVALID')
                ratio = Fraction(n, d)
                shares += 1
            else:
                raise ValueError('CAUSAL_PRICE_ACTION_UNSUPPORTED')
        if shares > 1:
            raise ValueError('CAUSAL_PRICE_SAME_DAY_SHARE_RATIO_CONFLICT')
        result.append({'symbol': symbol, 'effective_date': day, 'record_date': records.pop(),
            'cash_per_share': cash, 'account_cash_per_share': account_cash, 'share_ratio': float(ratio),
            'source_published_at': max(str(e['source_published_at']) for e in own),
            'event_ids': sorted(e['event_id'] for e in own)})
    return result


def expected_reference_v2(previous_close, events):
    value = float(previous_close)
    for event in grouped_price_actions_v2(events):
        value = (value - event['cash_per_share']) / event['share_ratio']
        if not math.isfinite(value) or value <= 0:
            raise ValueError('CAUSAL_PRICE_REFERENCE_INVALID')
    return value


def uses_share_actions_v2(events):
    return any(e.get('event_type') in {'BONUS', 'CAPITALIZATION'} for e in events)
