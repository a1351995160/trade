"""下跌后实体吞没的事前固定定义；无外部数据访问。"""
def bullish_engulfing(prices):
    c=prices.close;o=prices.open
    event=(c.shift(1)<o.shift(1))&(c>o)&(o<=c.shift(1))&(c>=o.shift(1))
    event&=(c.shift(1)<c.shift(6))
    return (-(c-o)/c).where(event,1.)
