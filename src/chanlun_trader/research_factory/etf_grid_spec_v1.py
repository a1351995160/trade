"""当前用户确认的ETF参数单一来源；配置不是执行授权。"""
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ETFGridSpecV1:
    version: str = 'ETF_GRID_SPEC_V1'
    symbol: str = '510300.SH'
    train_start: int = 20220801
    train_end: int = 20240731
    warmup_sessions: int = 120
    initial_cash: int = 10000
    core_budget: int = 5000
    grid_budget: int = 4000
    cash_reserve: int = 1000
    grid_quantity: int = 200
    bull_slots: int = 4
    range_slots: int = 2
    minimum_spacing: float = .025
    ma_periods: tuple = (20,60,120)
    atr_period: int = 14
    rsi_period: int = 14
    rsi_buy_pause: int = 75
    reset_band: float = .01
    max_price: float = 5.
    min_daily_amount: int = 500_000_000
    slippage_per_side: float = .001
    minimum_commission: float = 5.
    execution_policy: str = 'NEXT_SESSION_OPEN'
    take_profit: str = 'ADJACENT_LEVEL'
    core_sell_allowed: bool = False
    reset_requires_grid_flat: bool = True

    def as_dict(self):
        value=asdict(self)
        value['ma_periods']=list(self.ma_periods)
        return value


SPEC=ETFGridSpecV1()
