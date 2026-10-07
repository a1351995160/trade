"""全范围公开对照：现金与不可投资的期初等权价格篮子。"""
from copy import copy
import time

from .common import stable_hash
from .universe_account_inputs_v1 import UniverseAccountInputsV1, prepare_universe_account_inputs_v1


VERSION = 'UNIVERSE_PRICE_REFERENCE_V1'


def universe_price_reference(bundle, window, *, initial_cash):
    """名单在评价前一收盘冻结，理论碎股、无费用/税、不再平衡。

    此对照不创建订单和账户，不作为正式评审基准。缺成分数据不删权重。
    """
    inputs = prepare_universe_account_inputs_v1(bundle, window, stage='SCAN')
    return _universe_price_reference_from_inputs(inputs, initial_cash=initial_cash)


def _universe_price_reference_from_inputs(inputs, *, initial_cash, deadline=None):
    """受限报告复用已认证输入；计算口径与公共价格对照相同。"""
    if type(inputs) is not UniverseAccountInputsV1:
        raise ValueError('PRICE_REFERENCE_PREPARED_INPUT_REQUIRED')
    days = inputs.calendar
    first = days.index(inputs.window['account_start'])
    if first == 0:
        raise ValueError('PRICE_REFERENCE_PRIOR_SESSION_REQUIRED')
    previous = days[first - 1]
    # 价格篮子沿用原SCAN默认条件，不继承策略指标的字段/预热要求。
    selection = copy(inputs)
    selection.required_fields, selection.warmup_bars = frozenset(), 0
    members = [symbol for symbol in inputs.symbols if selection.scan_status(symbol, previous)['entry_eligible']]
    common = {'version': VERSION, 'input_identity': inputs.input_identity,
        'target_count': len(inputs.symbols), 'initial_members': members,
        'initial_members_identity': stable_hash(members), 'initial_selection_date': previous,
        'investable': False, 'account_reconciled': False, 'formal_qualification': False,
        'new_listings_added': False, 'fees_and_taxes': 'OMITTED_THEORETICAL_PRICE_REFERENCE',
        'cash': {'net_return': 0., 'initial_cash': float(initial_cash)},
        'limitations': ['允许理论碎股；不收费用和税，不再平衡，不是可投资账户。',
                       '期初名单与动态策略扫描范围不同；现金分红实际到账后计入，不再投资。']}
    if not members or not inputs.coverage['account_data_ready']:
        return {**common, 'status': 'UNAVAILABLE', 'daily': None, 'metrics': None,
                'reasons': inputs.coverage['global_gaps'] + sorted({row['reason'] for row in inputs.coverage['gaps']})
                           or ['PRICE_REFERENCE_EMPTY_INITIAL_BASKET']}
    weight = 1. / len(members)
    quantity, marks, cash = {}, {}, {s: weight for s in members}
    entitlements, paid, credited = {}, set(), set()
    daily, stale = [], []
    initial_lot_cost = sum(float(inputs.bar(s, days[first])['open']) * 100
                           for s in members if inputs.bar(s, days[first]) is not None)
    # 仅价格/整手的必要条件，仍不是有费用和交易约束的可执行账户。
    whole_pool_lots_feasible = (len(members) == len(inputs.symbols)
        and all(inputs.bar(s, days[first]) is not None
                and float(inputs.bar(s, days[first])['open']) * 100 <= initial_cash / len(members)
                for s in members))
    for day in days[first:]:
        if deadline is not None and time.monotonic() >= deadline:
            from .universe_account_backend_v2 import SegmentBoundary
            raise SegmentBoundary('UNIVERSE_PRICE_REFERENCE_COOPERATIVE_DEADLINE')
        for symbol in members:
            state, bar = inputs.state(symbol, day), inputs.bar(symbol, day)
            if not state['state_known'] or state['delisted']:
                return {**common, 'status': 'UNAVAILABLE', 'daily': None, 'metrics': None,
                        'reasons': ['PRICE_REFERENCE_COMPONENT_UNRESOLVED:' + symbol]}
            if state['suspension_status'] == 'TRADING':
                if bar is None:
                    return {**common, 'status': 'UNAVAILABLE', 'daily': None, 'metrics': None,
                            'reasons': ['PRICE_REFERENCE_COMPONENT_PRICE_MISSING:' + symbol]}
                if symbol not in quantity:
                    quantity[symbol] = cash[symbol] / float(bar['open'])
                    cash[symbol] = 0.
                marks[symbol] = float(bar['close'])
            elif symbol in quantity:
                stale.append({'date': day, 'symbol': symbol, 'status': 'STALE_VERIFIED_SUSPENSION'})
        for event in inputs.events:
            symbol = event['symbol']
            if symbol not in members:
                continue
            key = event['event_id']
            if day == event['record_date']:
                entitlements[key] = quantity.get(symbol, 0.)
            if event['event_type'] in {'BONUS', 'CAPITALIZATION'}:
                if key in entitlements and key not in credited and day >= event['effective_date']:
                    terms = event['terms']
                    ratio = terms['ratio_numerator'] / terms['ratio_denominator']
                    quantity[symbol] = quantity.get(symbol, 0.) + entitlements[key] * (ratio - 1)
                    credited.add(key)
                continue
            if key in entitlements and key not in paid and day >= event['payment_date']:
                cash[symbol] += entitlements.get(key, 0.) * event['terms']['cash_per_share']
                paid.add(key)
        value = sum(cash[s] + quantity.get(s, 0.) * marks.get(s, 0.) for s in members)
        daily.append({'date': day, 'relative_value': value, 'net_return': value - 1.})
    peak, drawdown = 1., 0.
    for row in daily:
        peak = max(peak, row['relative_value'])
        drawdown = max(drawdown, 1 - row['relative_value'] / peak)
    return {**common, 'status': 'AVAILABLE_NONINVESTABLE', 'reasons': [], 'daily': daily,
        'stale_valuations': stale, 'whole_pool_minimum_lot_cost': initial_lot_cost,
        'whole_pool_lot_allocation_feasible': whole_pool_lots_feasible,
        'metrics': {'net_return': daily[-1]['net_return'], 'max_drawdown': drawdown}}
