"""现金分红后的因果后复权特征；订单与账户仍使用未复权行情。"""
from __future__ import annotations

import math

import pandas as pd


PRICE_FIELDS = ("open", "high", "low", "close", "prev_close", "amount")


def causal_hfq_bars(raw: pd.DataFrame, events: tuple[dict, ...]) -> tuple[pd.DataFrame, list[dict]]:
    """只在除息日及之后改变特征，避免今天的因子改写过去的决策。"""
    if raw.empty or raw["date"].duplicated().any() or len(set(raw["symbol"])) != 1:
        raise ValueError("CAUSAL_PRICE_RAW_SCOPE_INVALID")
    bars = raw.sort_values("date").copy()
    if not bars["adjustflag"].astype(str).eq("3").all():
        raise ValueError("CAUSAL_PRICE_REQUIRES_RAW_BARS")
    symbol = str(bars.iloc[0]["symbol"])
    factors = pd.Series(1.0, index=bars.index)
    cumulative = 1.0
    evidence = []
    relevant = sorted((event for event in events if event["symbol"] == symbol),
                      key=lambda event: event["effective_date"])
    if len({event["effective_date"] for event in relevant}) != len(relevant):
        raise ValueError("CAUSAL_PRICE_DUPLICATE_EX_DATE")
    by_day = {int(row.date): row for row in bars.itertuples()}
    days = list(by_day)
    for event in relevant:
        if event["event_type"] != "CASH_DIVIDEND":
            raise ValueError("CAUSAL_PRICE_ACTION_UNSUPPORTED")
        day = int(event["effective_date"])
        if day not in by_day or days.index(day) == 0:
            raise ValueError("CAUSAL_PRICE_EX_DATE_MISSING")
        previous_day = days[days.index(day) - 1]
        if int(event["record_date"]) != previous_day:
            raise ValueError("CAUSAL_PRICE_RECORD_DATE_MISMATCH")
        if int(str(event["source_published_at"]).replace("-", "")[:8]) > previous_day:
            raise ValueError("CAUSAL_PRICE_ACTION_NOT_KNOWN_AT_RECORD")
        previous_close = float(by_day[previous_day].close)
        ex_reference = float(by_day[day].prev_close)
        cash = float(event["terms"]["cash_per_share"])
        if (not all(math.isfinite(value) and value > 0 for value in
                    (previous_close, ex_reference, cash))
                or abs(ex_reference - (previous_close - cash)) > 0.011):
            raise ValueError("CAUSAL_PRICE_DIVIDEND_REFERENCE_CONFLICT")
        step = previous_close / ex_reference
        cumulative *= step
        factors.loc[bars["date"].ge(day)] = cumulative
        evidence.append({"event_id": event["event_id"], "record_date": previous_day,
                         "ex_date": day, "raw_previous_close": previous_close,
                         "raw_ex_reference": ex_reference, "cash_per_share": cash,
                         "step_factor": step, "cumulative_factor": cumulative,
                         "source_published_at": event["source_published_at"]})
    for field in PRICE_FIELDS:
        bars[field] = pd.to_numeric(bars[field], errors="raise") * factors
    return bars, evidence


def causal_hfq_bars_v2(raw: pd.DataFrame, events: tuple[dict, ...]) -> tuple[pd.DataFrame, list[dict]]:
    """支持同日多笔现金与一个总送转比例，旧V1价格语义保持不变。"""
    from .corporate_action_price_v2 import grouped_price_actions_v2
    if raw.empty or raw['date'].duplicated().any() or len(set(raw['symbol'])) != 1:
        raise ValueError('CAUSAL_PRICE_RAW_SCOPE_INVALID')
    bars = raw.sort_values('date').copy()
    if not bars['adjustflag'].astype(str).eq('3').all():
        raise ValueError('CAUSAL_PRICE_REQUIRES_RAW_BARS')
    symbol = str(bars.iloc[0]['symbol'])
    prices = pd.Series(1., index=bars.index)
    quantities = pd.Series(1., index=bars.index)
    by_day = {int(row.date): row for row in bars.itertuples()}
    days = list(by_day)
    indices = {day: i for i, day in enumerate(days)}
    cumulative, shares, evidence = 1., 1., []
    for event in grouped_price_actions_v2(e for e in events if e['symbol'] == symbol):
        day = event['effective_date']
        if day not in indices or indices[day] == 0:
            raise ValueError('CAUSAL_PRICE_EX_DATE_MISSING')
        previous_day = days[indices[day] - 1]
        if event['record_date'] != previous_day:
            raise ValueError('CAUSAL_PRICE_RECORD_DATE_MISMATCH')
        if int(event['source_published_at'].replace('-', '')[:8]) > previous_day:
            raise ValueError('CAUSAL_PRICE_ACTION_NOT_KNOWN_AT_RECORD')
        before, reference = float(by_day[previous_day].close), float(by_day[day].prev_close)
        cash, ratio = event['cash_per_share'], event['share_ratio']
        expected = (before - cash) / ratio
        if (not all(math.isfinite(v) and v > 0 for v in (before, reference, expected))
                or abs(reference - expected) > .011):
            raise ValueError('CAUSAL_PRICE_DIVIDEND_REFERENCE_CONFLICT')
        step = before / reference
        cumulative *= step
        shares *= ratio
        mask = bars['date'].ge(day)
        prices.loc[mask], quantities.loc[mask] = cumulative, shares
        evidence.append({**event, 'ex_date': day, 'raw_previous_close': before,
            'raw_ex_reference': reference, 'expected_ex_reference': expected,
            'step_factor': step, 'cumulative_factor': cumulative,
            'cumulative_share_ratio': shares, 'price_policy': 'CAUSAL_CASH_AND_SHARES_V2'})
    for field in ('open', 'high', 'low', 'close', 'prev_close'):
        bars[field] = pd.to_numeric(bars[field], errors='raise') * prices
    bars['volume'] = pd.to_numeric(bars['volume'], errors='raise') / quantities
    bars['amount'] = pd.to_numeric(bars['amount'], errors='raise') * prices / quantities
    return bars, evidence
