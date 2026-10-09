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


def causal_hfq_bars_v3(raw: pd.DataFrame, events: tuple[dict, ...], calendar, state,
                       *, calendar_source=None, source_hashes=None) -> tuple[pd.DataFrame, list[dict]]:
    """按来源证明停牌区段，在首个真实复牌 bar 应用期间的除权条款。"""
    from bisect import bisect_left
    from .corporate_action_price_v2 import grouped_price_actions_v2

    if raw.empty or raw['date'].duplicated().any() or len(set(raw['symbol'])) != 1:
        raise ValueError('CAUSAL_PRICE_RAW_SCOPE_INVALID')
    full = raw.sort_values('date').copy()
    if not full['adjustflag'].astype(str).eq('3').all():
        raise ValueError('CAUSAL_PRICE_REQUIRES_RAW_BARS')
    days = tuple(calendar)
    if (not days or list(days) != sorted(set(days))
            or any(isinstance(day, bool) or str(day) != str(int(day)) for day in days)
            or not set(full['date']) <= set(days)):
        raise ValueError('CAUSAL_PRICE_CALENDAR_INVALID')
    session = {int(day): index for index, day in enumerate(days)}
    symbol = str(full.iloc[0]['symbol'])
    activity = pd.to_numeric(full['volume'], errors='raise')
    if not activity.map(lambda value: math.isfinite(value) and value >= 0).all():
        raise ValueError('CAUSAL_PRICE_ACTIVITY_INVALID')
    bars = full.loc[activity.gt(0)].copy()
    if bars.empty:
        return bars, []
    relevant = tuple(event for event in events if event['symbol'] == symbol)
    actions = grouped_price_actions_v2(relevant)
    by_day = {int(row.date): row for row in bars.itertuples()}
    bar_days = list(by_day)
    grouped = {}
    sparse = False
    for event in actions:
        day, record = event['effective_date'], event['record_date']
        if day not in session or record not in session:
            raise ValueError('CAUSAL_PRICE_ACTION_DATE_OUTSIDE_CALENDAR')
        if session[day] == 0 or days[session[day] - 1] != record:
            raise ValueError('CAUSAL_PRICE_RECORD_DATE_MISMATCH')
        if int(event['source_published_at'].replace('-', '')[:8]) > record:
            raise ValueError('CAUSAL_PRICE_ACTION_NOT_KNOWN_AT_RECORD')
        index = bisect_left(bar_days, day)
        if index == 0 or index == len(bar_days):
            raise ValueError('CAUSAL_PRICE_EX_DATE_MISSING')
        before, resume = bar_days[index - 1], bar_days[index]
        sparse |= resume != day or before != record
        grouped.setdefault((before, resume), []).append(event)
    if not sparse:
        # 正常区段保留旧扫描的价格和 preparation 序列。
        transform = causal_hfq_bars_v2 if (len(actions) != len(relevant) or any(
            event.get('price_version') == 'CASH_AND_SHARES_V2'
            or event['event_type'] in {'BONUS', 'CAPITALIZATION'} for event in relevant)) else causal_hfq_bars
        return transform(bars, relevant)

    def registered(source):
        return (isinstance(source, str) and bool(source) and source not in {'UNKNOWN', 'MODELED'}
                and isinstance(source_hashes, dict) and (source in source_hashes
                or any(digest in source for digest in source_hashes.values())))

    if not registered(calendar_source):
        raise ValueError('CAUSAL_PRICE_CALENDAR_SOURCE_UNVERIFIED')
    raw_days = {int(row.date): row for row in full.itertuples()}
    prices = pd.Series(1., index=bars.index)
    quantities = pd.Series(1., index=bars.index)
    cumulative, shares, evidence = 1., 1., []
    for (before_day, resume_day), own in sorted(grouped.items()):
        suspension = []
        for day in days[session[before_day] + 1:session[resume_day]]:
            status = state(symbol, day)
            source = status.get('source', status.get('state_source'))
            if (status.get('state_known') is not True or status.get('suspension_status') != 'SUSPENDED'
                    or status.get('listed') is not True or status.get('delisted') is not False
                    or not registered(source)):
                raise ValueError('CAUSAL_PRICE_SUSPENSION_NOT_PROVEN')
            suspension.append({'date': int(day), 'state_known': True, 'listed': True,
                'delisted': False, 'suspension_status': 'SUSPENDED', 'source': source,
                'historical_availability': status.get('historical_availability'),
                'state_effective_date': status.get('effective_date'),
                'state_valid_to': status.get('valid_to'), 'raw_record_present': day in raw_days})
        before = float(by_day[before_day].close)
        reference = float(by_day[resume_day].prev_close)
        expected, steps = before, []
        for event in own:
            previous = expected
            expected = (previous - event['cash_per_share']) / event['share_ratio']
            if not all(math.isfinite(value) and value > 0 for value in (previous, expected)):
                raise ValueError('CAUSAL_PRICE_DIVIDEND_REFERENCE_CONFLICT')
            steps.append((event, previous, expected))
        if (not math.isfinite(reference) or reference <= 0 or abs(reference - expected) > .011):
            raise ValueError('CAUSAL_PRICE_DIVIDEND_REFERENCE_CONFLICT')
        for index, (event, previous, expected_step) in enumerate(steps):
            # 中间步骤使用条款参考，末步使用真实复牌官方昨收，保留舍入容差。
            applied_reference = reference if index == len(steps) - 1 else expected_step
            step = previous / applied_reference
            cumulative *= step
            shares *= event['share_ratio']
            if not all(math.isfinite(value) and value > 0 for value in (cumulative, shares)):
                raise ValueError('CAUSAL_PRICE_DIVIDEND_REFERENCE_CONFLICT')
            evidence.append({**event, 'ex_date': event['effective_date'],
                'reference_date': before_day, 'resume_date': resume_day,
                'raw_previous_close': before, 'raw_ex_reference': reference,
                'expected_before_reference': previous, 'expected_ex_reference': expected_step,
                'applied_reference': applied_reference, 'step_factor': step,
                'cumulative_factor': cumulative, 'cumulative_share_ratio': shares,
                'calendar_source': calendar_source, 'suspension_sessions': suspension,
                'price_policy': 'CAUSAL_SUSPENDED_CASH_AND_SHARES_V3'})
        mask = bars['date'].ge(resume_day)
        prices.loc[mask], quantities.loc[mask] = cumulative, shares
    for field in ('open', 'high', 'low', 'close', 'prev_close'):
        bars[field] = pd.to_numeric(bars[field], errors='raise') * prices
    bars['volume'] = pd.to_numeric(bars['volume'], errors='raise') / quantities
    bars['amount'] = pd.to_numeric(bars['amount'], errors='raise') * prices / quantities
    return bars, evidence
