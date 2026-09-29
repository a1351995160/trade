"""从已核对 Paper 原件生成只读数量计划；不提交订单或改写账本。"""
from __future__ import annotations

import math
import pandas as pd

from ..engine.order import Order
from ..engine.signal import Side
from .common import stable_hash
from .portfolio_execution_v1 import PortfolioExecutionRiskV1, _admitted


def trusted_daily_plan(session):
    header = session.header()
    records = session._records(header, recover=False)
    now = session.now()
    result = {'schema_version': 'TRUSTED_DAILY_PLAN_V1', 'profile': header['profile'],
              'status': 'WAITING_DATA', 'message': '今日不交易：等待完整收盘数据与可核对计划。',
              'intents': [], 'excluded': [], 'portfolio_qualified': False,
              'real_execution_authorized': False, 'header_id': header['header_id'],
              'limits': ['数量按最近收盘价估算，次日开盘须重新核验价格、费用、资格与可成交性。',
                         '共享现金按顺序预留，不预支卖出款；本视图不下单。']}
    if session.path('REVOKED.json').exists():
        return {**result, 'status': 'REVOKED', 'message': '今日不交易：观察授权已撤销。'}
    if not records or records[-1]['snapshot']['phase'] != 'CLOSE' or not records[-1]['plan']:
        return result
    record, plan = records[-1], records[-1]['plan']
    engine = session._recover(header, records)
    admissions = session._admissions(header)
    next_open = pd.Timestamp(str(plan['next_session']), tz='Asia/Shanghai') + pd.Timedelta(hours=9, minutes=30)
    deadline = min(next_open + pd.Timedelta(minutes=header['policy']['open_delay_minutes']),
                   pd.Timestamp(header['policy']['portfolio']['valid_until']))
    result.update(valid_date=plan['next_session'], valid_until=deadline.isoformat(),
                  source_plan_id=plan['plan_id'], record_hash=record['record_hash'],
                  input_identity=plan['input_identity'], admissions=admissions,
                  portfolio_qualification=header.get('portfolio_qualification'))
    if now >= deadline:
        return {**result, 'status': 'EXPIRED', 'message': '今日不交易：计划已过期，等待新的收盘计划。'}
    qualified = all(_admitted(value, header['purpose']) for value in admissions.values())
    result['portfolio_qualified'] = bool(header.get('portfolio_qualification')) and qualified
    if not any(_admitted(value, header['purpose']) for value in admissions.values()):
        return {**result, 'status': 'NO_ADMITTED_STRATEGIES', 'message': '今日不交易：没有当前合格成员。'}
    if header['purpose'] == 'FORMAL_OBSERVATION' and not result['portfolio_qualified']:
        return {**result, 'status': 'WAITING_QUALIFICATION', 'message': '今日不交易：组合资格未满足，不能继承成员资格。'}
    bars = record['snapshot']['payload'].get('bars', [])
    prices = {row['symbol']: float(row['close']) for row in bars}
    if (not set(header['policy']['symbols']) <= prices.keys()
            or any(not math.isfinite(value) or value <= 0 for value in prices.values())):
        return {**result, 'message': '今日不交易：缺少有效收盘价格。'}
    ledger = engine.engine.ledger
    pending = list(engine.engine.order_manager.open_orders())
    risk = PortfolioExecutionRiskV1(ledger, engine.base_risk_config, policy=engine.portfolio,
        fee_model=engine.engine.broker.fee_model, admission_check=lambda key: admissions[key],
        orders_provider=lambda: pending, prices=prices, session_at=next_open)
    result.update(available_cash=ledger.available_cash(), estimated_buy_cost=0.0,
                  excluded=list(plan['excluded']))
    for item in plan['intents']:
        key, symbol, side = item['strategy_id'], item['symbol'], item['side']
        price = prices[symbol]
        if side == 'BUY':
            quantity = risk.buy_quantity(key, symbol, price, next_open, target_weight=item.get('target_weight', 1.0))
            if getattr(engine, 'observation', {}).get('buy_blocked'):
                quantity = 0
        else:
            lots = item.get('exit_lot_ids')
            quantity = (sum(ledger.sellable_lot_quantity(lot, next_open) for lot in lots) if lots
                        else ledger.sellable_quantity(key, symbol, next_open))
        if quantity <= 0:
            result['excluded'].append({**item, 'reason': 'NO_PERMITTED_QUANTITY',
                                      'message': '受资金、资格、权重、整手或T+1限制，无可执行数量。'})
            continue
        cost = quantity * price + engine.engine.broker.fee_model.calc('BUY', quantity, price).total_fee if side == 'BUY' else 0.0
        result['intents'].append({**item, 'quantity': quantity, 'reference_price': price,
            'estimated_cash_required': cost, 'message': '买入数量估算，开盘重检' if side == 'BUY' else '按归属批次及T+1可卖数量退出'})
        result['estimated_buy_cost'] += cost
        pending.append(Order(order_id='PLAN_' + item['intent_id'], strategy_id=key,
            intent_id=item['intent_id'], signal_id='', symbol=symbol, side=Side(side),
            quantity=quantity, created_at=next_open))
    result.update(status='PLANNED' if result['intents'] else 'NO_TRADE',
                  message='仅供核对的下一交易日数量计划。' if result['intents'] else '今日不交易：没有满足约束的交易意图。')
    result['plan_identity'] = stable_hash(result)
    return result
