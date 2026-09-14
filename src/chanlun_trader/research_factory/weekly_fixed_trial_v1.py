"""低波动周节奏的退出对照；保留原入场和20session期限，不搜索参数。"""
FIXED='WEEKLY_LOW_VOL_FIXED_HOLD_20'
WITH_MARKET='WEEKLY_LOW_VOL_FIXED_MARKET_HOLD_20'
NAMES=(FIXED,WITH_MARKET)
FORMULAS={
    FIXED:'Same WEEKLY_LOW_VOL_EMA_EXIT entry and ranking, but original FIXED_HOLD20 only; no EMA invalidation exit',
    WITH_MARKET:'Same WEEKLY_LOW_VOL_FIXED entry/exit; new entries only when median eligible original RETURN_5D>0; missing/nonpositive forbids entry, fixed exits unaffected',
}
