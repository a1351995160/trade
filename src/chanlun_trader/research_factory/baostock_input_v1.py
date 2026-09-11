"""双价格输入的无信号检查；保留日期缺失和状态冲突。"""
import math
from decimal import Decimal


def verified_ipo_prefix(raw, hfq, state_rows):
    """按官方上市首日因子1及昨收/前收递推，证明原值相同的IPO前缀。"""
    listed = sorted((int(str(r['trade_date']).replace('-','')),r['listed']) for r in state_rows)
    first = next((d for d,value in listed if value is True),None)
    if first is None or not any(d < first and value is False for d,value in listed):
        return []
    prices = sorted(raw,key=lambda r:r['date'])
    adjusted = sorted(hfq,key=lambda r:r['date'])
    if not prices or not adjusted or prices[0]['date'] != adjusted[0]['date']:
        return []
    if int(prices[0]['date'].replace('-','')) != first:
        return []
    by_day = {r['date']:r for r in prices}
    dates = []
    previous = None
    for row in adjusted:
        if row.get('adjustflag') != '3':
            break
        source = by_day.get(row['date'])
        if source is None or row.get('code') != source.get('code'):
            return []
        try:
            close, preclose = Decimal(source['close']), Decimal(source['preclose'])
            if not close.is_finite() or close <= 0 or Decimal(row['close']) != close:
                return []
            if previous is not None and preclose != previous:
                return []
        except (ValueError, TypeError, ArithmeticError):
            return []
        dates.append(row['date'])
        previous = close
    return dates


def verify_pair(symbol, raw, hfq, sessions, state_rows):
    code = symbol[-2:].lower()+'.'+symbol[:6]
    failures = []
    baseline = verified_ipo_prefix(raw['rows'],hfq['rows'],state_rows)

    def index(response, flag, fields):
        if response['error_code'] != '0':
            failures.append({'reason':'PROVIDER_ERROR','flag':flag,'error':response['error_code']})
        result = {}
        for row in response['rows']:
            day = int(row['date'].replace('-',''))
            if (day not in sessions or day in result or row.get('code') != code or
                    (row.get('adjustflag') != flag and not
                     (flag == '1' and row.get('adjustflag') == '3' and row['date'] in baseline))
                    or not set(fields) <= row.keys()):
                failures.append({'reason':'ROW_IDENTITY_OR_SCHEMA_CONFLICT','flag':flag,'date':day})
            result[day] = row
        return result

    raw_days = index(raw,'3',['open','high','low','close','preclose','volume','amount','tradestatus','isST'])
    hfq_days = index(hfq,'1',['close'])
    for day in sorted(set(raw_days) ^ set(hfq_days)):
        failures.append({'reason':'RAW_HFQ_DATE_UNPAIRED','date':day})
    suspended = 0
    for day, row in raw_days.items():
        try:
            prices = [float(row[k]) for k in ['open','high','low','close','preclose']]
            if not all(math.isfinite(p) and p > 0 for p in prices):
                raise ValueError('NON_POSITIVE_OR_NON_FINITE_PRICE')
            if prices[1] < max(prices[:4]) or prices[2] > min(prices[:4]):
                raise ValueError('OHLC_ORDER')
            adjusted = float(hfq_days[day]['close'])
            if not math.isfinite(adjusted) or adjusted <= 0:
                raise ValueError('HFQ_PRICE_INVALID')
            if row['tradestatus'] == '0':
                suspended += 1
            elif row['tradestatus'] == '1':
                volume, amount = float(row['volume']), float(row['amount'])
                if not (math.isfinite(volume) and volume > 0 and volume == int(volume)
                        and math.isfinite(amount) and amount > 0):
                    raise ValueError('TRADING_VOLUME_OR_AMOUNT_INVALID')
            else:
                raise ValueError('TRADING_STATUS_UNKNOWN')
            if row['isST'] not in {'0','1'}:
                raise ValueError('ST_STATUS_UNKNOWN')
        except (ValueError, KeyError, TypeError) as exc:
            failures.append({'reason':'REQUIRED_PRICE_OR_ACTIVITY_INVALID','date':day,'detail':str(exc)})
    eligible_days = 0
    for row in state_rows:
        day = int(str(row['trade_date']).replace('-',''))
        lifecycle = row['listed'] and not row['delisted'] and row['universe_member']
        if not lifecycle:
            continue
        eligible_days += row['eligibility_status'] == 'ELIGIBLE'
        if day not in raw_days:
            failures.append({'reason':'HISTORICAL_LIFECYCLE_PRICE_MISSING','date':day})
            continue
        source = raw_days[day]
        for field, value, mapping in [('st_status',source.get('isST'),{'0':'NORMAL','1':'ST'}),
                ('suspension_status',source.get('tradestatus'),{'0':'SUSPENDED','1':'TRADING'})]:
            if row[field] in mapping.values() and row[field] != mapping.get(value):
                failures.append({'reason':'HISTORICAL_STATE_CONFLICT','date':day,'field':field,
                    'historical':row[field],'provider':value})
    return {'symbol':symbol,'raw_rows':len(raw_days),'hfq_rows':len(hfq_days),
        'suspended_rows_preserved':suspended,'historical_eligible_days':int(eligible_days),
        'verified_ipo_hfq_identity_dates':baseline,'raw_flags_preserved':True,
        'failures':failures,'passed':not failures,'no_signal_or_outcome':True}
