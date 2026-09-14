"""回调恢复与避开近期涨幅的相对选择；仅固定第21批规则。"""
RECOVERY='RSI2_RECOVERY_SMA5_HOLD_20'
ROTATION='SKIP5_MOMENTUM_PULLBACK_HOLD_20'
NAMES=(RECOVERY,ROTATION)
FORMULAS={
    RECOVERY:'C>SMA200 and C<SMA5 and Wilder RSI2<5; score=RSI2-5; exit visible prior-session C>SMA5 or original fixed20 due',
    ROTATION:'C>SMA60>SMA60.shift(5), C<SMA5, C.shift(5)/C.shift(65)>1; score=1-C.shift(5)/C.shift(65); fixed20 exit',
}
WARMUP={RECOVERY:200,ROTATION:66}
EXIT_POLICY={'exit_type':'STRUCTURE_INVALIDATION','fixed_holding_sessions':20,
    'factor_conditions':[{'factor_id':'CLOSE_ABOVE_SMA5','operator':'EQ','value':1.}], 'logic':'OR'}


def chosen(prices,name,rsi):
    c=prices.close
    sma5=c.rolling(5,min_periods=5).mean()
    if name==RECOVERY:
        value=rsi(c,period=2)
        event=(c>c.rolling(200,min_periods=200).mean())&(c<sma5)&(value<5)
        return (value-5).where(event,1.)
    if name==ROTATION:
        trend=c.rolling(60,min_periods=60).mean()
        strength=c.shift(5)/c.shift(65)
        event=(c>trend)&(trend>trend.shift(5))&(c<sma5)&(strength>1)
        return (1-strength).where(event,1.)
    raise ValueError('UNFROZEN_RECOVERY_ROTATION_SIGNAL')
