"""两证券固定范围的合成输入，不能作为真实数据验收。"""
import numpy as np
import pandas as pd

from scripts.probe_all_indicator_strategy_v1 import SYMBOLS
from scripts.s1_public_entry_strategy_v1 import input_identity


def synthetic_bundle(strategy, events=()) -> dict:
    dates = pd.bdate_range("2023-10-09", "2024-07-31")
    bars, turns, states, historical = [], [], [], []
    for symbol in SYMBOLS:
        previous_close = 10.0
        for i, date in enumerate(dates):
            day = int(date.strftime("%Y%m%d"))
            close = 10 + i * 0.02 + 0.7 * np.sin(i / 6)
            volume = 1_000_000 + 100_000 * np.sin(i / 5)
            bars.append({"symbol": symbol, "date": day, "open": close * 0.995,
                         "high": close * 1.02, "low": close * 0.98,
                         "close": close, "volume": volume, "amount": close * volume,
                         "prev_close": previous_close, "adjustflag": "3"})
            turns.append({"symbol": symbol, "date": day, "volume": volume,
                          "turn": 0.5 + i * 0.002 + 0.1 * np.sin(i / 5), "tradestatus": 1})
            previous_close = close
            if day >= 20240131:
                state = {"symbol": symbol, "trade_date": day, "listed": True,
                         "delisted": False, "universe_member": True,
                         "eligibility_status": "ELIGIBLE", "st_status": "NORMAL",
                         "suspension_status": "TRADING",
                         "board": "SZ_MAIN" if symbol.endswith("SZ") else "SH_MAIN"}
                next_open = date + pd.offsets.BDay(1)
                historical.append({**state, "available_at": f"{next_open:%Y-%m-%d}T09:30:00+08:00",
                                   "source_lineage": "SYNTHETIC"})
                if day >= 20240201:
                    states.append(state)
    bundle = {"daily": pd.DataFrame(bars), "turn": pd.DataFrame(turns),
              "states": pd.DataFrame(states), "historical": pd.DataFrame(historical),
              "source_hashes": {key: key for key in (
                  "daily_sha256", "turn_sha256", "states_sha256",
                  "historical_states_sha256", "corporate_actions_sha256")},
              "account_end_date": 20240731}
    bundle["input_identity"] = input_identity(bundle, events, strategy.definition["catalog_sha256"])
    return bundle
