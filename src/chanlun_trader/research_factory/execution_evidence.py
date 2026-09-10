"""正式执行结果的微观时序证据。"""
import pandas as pd

from ..engine.security_state import ChinaPriceLimitModel
from ..engine.signal import Side
from ..engine.time_types import EventKind
from .common import stable_hash


def _stamp(value):
    stamp = pd.Timestamp(value)
    return None if pd.isna(stamp) or stamp.tzinfo is None else stamp.tz_convert("Asia/Shanghai")


def audit_microstructure(result, inputs):
    """核对当前caller支持的DAILY/RAW/默认涨跌停模型，不调整任何成交定义。"""
    reasons = []
    if result.context.mode != "DAILY" or result.context.fill_model != "DailyBarFillModel":
        reasons.append("EXECUTION_MODEL_NOT_VERIFIED")
    if inputs["input_diagnostics"]["data_validity"] != "VALIDATED_DAILY_RAW":
        reasons.append("DATA_NOT_VERIFIED")
    if inputs["input_diagnostics"]["pit_validity"] != "EXPLICIT_DUAL_SOURCE_NORMAL_TRADING":
        reasons.append("PIT_NOT_VERIFIED")
    signals = {signal.signal_id: signal for signal in result.signals}
    for signal in result.signals:
        stamp = _stamp(signal.generated_at)
        if stamp is None:
            reasons.append("SIGNAL_TIME_INVALID")
            continue
        day = int(stamp.strftime("%Y%m%d"))
        factors = inputs["factor_values"]
        rows = factors.loc[(factors.date == day) & (factors.symbol == signal.symbol)]
        available = pd.to_datetime(rows.available_at, errors="coerce", utc=True)
        if rows.empty or available.isna().any() or (available > stamp).any():
            reasons.append("SIGNAL_FACTOR_TIME_INVALID")
    prices = ChinaPriceLimitModel()
    open_times = {event.timestamp for event in result.clock.events if event.kind == EventKind.SESSION_OPEN}
    sold = {}
    for trade in result.ledger.trades:
        stamp = _stamp(trade.fill_time)
        order = result.orders.orders.get(trade.order_id)
        if stamp is None or order is None:
            reasons.append("FILL_TIME_OR_ORDER_MISSING")
            continue
        eligible = _stamp(order.eligible_at)
        if eligible is None or stamp < eligible:
            reasons.append("FILL_BEFORE_ELIGIBLE")
        if stamp not in open_times:
            reasons.append("FILL_OUTSIDE_SESSION_OPEN")
        if trade.strategy_id != order.strategy_id or trade.symbol != order.symbol or trade.side != order.side:
            reasons.append("FILL_ORDER_IDENTITY_CONFLICT")
        day = int(stamp.strftime("%Y%m%d"))
        bar = inputs["store"].get_daily_bar(trade.symbol, day, price_mode="raw")
        allowed, reason = (prices.can_buy_at_open if trade.side == Side.BUY else prices.can_sell_at_open)(trade.symbol, stamp, bar)
        if not allowed:
            reasons.append("FILL_NOT_TRADABLE:" + reason)
        if trade.side == Side.BUY:
            signal = signals.get(order.signal_id)
            generated = _stamp(signal.generated_at) if signal else None
            if generated is None or generated >= stamp or generated.normalize() >= stamp.normalize():
                reasons.append("BUY_SIGNAL_TIMING_INVALID")
            elif signal.strategy_id != trade.strategy_id or signal.symbol != trade.symbol:
                reasons.append("BUY_SIGNAL_IDENTITY_CONFLICT")
        else:
            lot = result.ledger.lots.get(trade.lot_id)
            sellable = _stamp(lot.sellable_from) if lot else None
            if lot is None or sellable is None or stamp < sellable:
                reasons.append("SELL_T_PLUS_ONE_INVALID")
            elif lot.strategy_id != trade.strategy_id or lot.symbol != trade.symbol:
                reasons.append("SELL_LOT_OWNERSHIP_CONFLICT")
            else:
                sold[lot.lot_id] = sold.get(lot.lot_id, 0) + trade.quantity
                if sold[lot.lot_id] > lot.quantity:
                    reasons.append("SELL_LOT_QUANTITY_EXCEEDED")
    if result.ledger.check_invariants():
        reasons.append("LEDGER_INVARIANT_INVALID")
    return {"passed": not reasons, "status": "INVALID" if reasons else "CHECKED",
        "reason_codes": sorted(set(reasons)), "signals_checked": len(result.signals),
        "trades_checked": len(result.ledger.trades), "input_identity": inputs["input_diagnostics"]["input_identity"],
        "event_hash": stable_hash(result.event_log.to_records())}
