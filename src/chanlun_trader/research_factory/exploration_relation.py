"""仅探索编码价格关系；没有订单、资金、收益资格或统计推断。"""
import numpy as np
import pandas as pd


def calculate(frame, calendar, protocol, placebo, universe_count, check_active=lambda: None):
    if protocol not in {'A', 'B'} or type(placebo) is not bool:
        raise ValueError('EXPLORATION_CONTRACT_INVALID')
    if frame.duplicated(['date', 'symbol']).any():
        raise ValueError('DUPLICATE_SECURITY_SESSION')
    if len(calendar) != len(set(calendar)) or sorted(calendar) != list(calendar):
        raise ValueError('CALENDAR_INVALID')
    if not set(frame.date).issubset(calendar):
        raise ValueError('DATE_OUTSIDE_FROZEN_CALENDAR')
    symbols = sorted(frame.symbol.unique())
    if len(symbols) > universe_count:
        raise ValueError('UNIVERSE_COVERAGE_CONFLICT')
    panels = {field: frame.pivot(index='date', columns='symbol', values=field).reindex(index=calendar, columns=symbols)
              for field in ('close', 'high', 'low')}
    c, h, l = (panels[field] for field in ('close', 'high', 'low'))
    absent = c.isna() | h.isna() | l.isna()
    illegal = (~np.isfinite(c) | ~np.isfinite(h) | ~np.isfinite(l) | (c<=0) | (l<=0) | (h<=0) | (h<l) | (c<l) | (c>h)) & ~absent
    valid = ~absent & ~illegal
    c, h, l = (p.where(valid) for p in (c,h,l))
    check_active()
    # 标记完整日历窗口，不能把缺口压缩成证券的相邻行。
    if protocol == 'A':
        history = valid.rolling(6, min_periods=6).sum().eq(6)
        zero_range = pd.DataFrame(False, index=c.index, columns=c.columns)
        x = (c/c.shift(5)-1).where(history)
    else:
        history = valid.rolling(20, min_periods=20).sum().eq(20)
        lower, upper = l.rolling(20,min_periods=20).min(), h.rolling(20,min_periods=20).max()
        zero_range = (upper-lower).eq(0) & history
        x = ((c-lower)/(upper-lower).where(~zero_range)).where(history)
    if placebo:
        x = x.shift(20)
        history = history.shift(20, fill_value=False)
        zero_range = zero_range.shift(20, fill_value=False)
    label_valid = valid & valid.shift(-1,fill_value=False) & valid.shift(-2,fill_value=False) & valid.shift(-3,fill_value=False)
    y = (c.shift(-3)/c-1).where(label_valid)
    usable = np.isfinite(x) & np.isfinite(y)
    group1 = x.lt(0) if protocol == 'A' else x.ge(.8)
    rows=[]
    for i, date in enumerate(calendar):
        check_active()
        first = usable.iloc[i] & group1.iloc[i]
        second = usable.iloc[i] & ~group1.iloc[i]
        n1,n2 = int(first.sum()),int(second.sum())
        a = float(y.iloc[i][first].mean()) if n1 else None
        b = float(y.iloc[i][second].mean()) if n2 else None
        reasons=[]
        if not n1: reasons.append('EMPTY_GROUP_1')
        if not n2: reasons.append('EMPTY_GROUP_2')
        if i >= len(calendar)-3: reasons.append('LABEL_BEYOND_TRAIN')
        rows.append(dict(date=int(date),group1_count=n1,group2_count=n2,
                         not_computable_count=universe_count-n1-n2,
                         absent_source_members=universe_count-len(symbols),
                         missing_price_count=int(absent.iloc[i].sum()),
                         invalid_price_count=int(illegal.iloc[i].sum()),
                         history_not_computable_count=int((~history.iloc[i]).sum()),
                         zero_range_count=int(zero_range.iloc[i].sum()),
                         label_not_computable_count=int((~label_valid.iloc[i]).sum()),
                         group1_mean=a,group2_mean=b,mean_difference=a-b if n1 and n2 else None,
                         status='COMPUTABLE' if n1 and n2 else 'NOT_COMPUTABLE', reasons=reasons))
    return rows
