"""失败学习后的新合同，保留原入场，只研究失效退出及市场门控。"""
BASE='ADX_EMA_PULLBACK_HOLD_20'
EXIT_ONLY='ADX_EMA_INVALIDATION_HOLD_20'
WITH_MARKET='ADX_EMA_INVALIDATION_MARKET_HOLD_20'
NAMES=(EXIT_ONLY,WITH_MARKET)
EXIT_POLICY={'exit_type':'STRUCTURE_INVALIDATION','fixed_holding_sessions':20,
    'factor_conditions':[{'factor_id':'CLOSE_BELOW_EMA20','operator':'EQ','value':1.}], 'logic':'OR'}
FORMULAS={
    EXIT_ONLY:'Original ADX_EMA_PULLBACK entry; exit when visible PRIOR_SESSION HFQ C<EMA20, or original fixed20 due; evaluate at15:30, sell next eligible open',
    WITH_MARKET:'Same ADX_EMA_INVALIDATION entry/exit; new entries only if median eligible original RETURN_5D>0; market gate never blocks exits',
}
