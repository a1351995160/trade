import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import numpy as np
import pandas as pd

from chanlun_trader.research.evaluation import FactorEvaluator
from chanlun_trader.research.event_study import EventStudy


def make_factor_labels(n_days=30, n_symbols=100, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    lab_rows = []
    dates = [20250601 + i for i in range(n_days)]  # 简化：不需要真实日历
    for di, d in enumerate(dates):
        for s in range(n_symbols):
            sym = f"{s:06d}.SH"
            f = rng.normal()
            # 构造正向 IC：未来收益 = 0.2 * f + noise
            noise = rng.normal(scale=0.5)
            rows.append({"symbol": sym, "timestamp": d, "value": f})
            for h in (1, 5, 10):
                if di + h < n_days:
                    lab_rows.append({"symbol": sym, "timestamp": d, "horizon": h,
                                     "future_return": 0.2 * f + noise})
    return pd.DataFrame(rows), pd.DataFrame(lab_rows)


def test_factor_evaluator_positive_ic():
    factor_df, labels = make_factor_labels()
    ev = FactorEvaluator()
    r = ev.evaluate("F001", "v1", factor_df, labels, horizon=5)
    assert r.n_obs > 100
    assert r.ic_mean is not None and r.ic_mean > 0.1
    assert r.rank_ic_mean is not None and r.rank_ic_mean > 0.1
    assert r.q5_mean > r.q1_mean
    assert r.top_decile_mean > 0


def test_event_study_positive_drift():
    ev = EventStudy()
    dates = [20250601 + i for i in range(20)]
    events = pd.DataFrame({"symbol": ["A"] * 10, "event_time": dates[:10]})
    labels = pd.DataFrame([
        {"symbol": "A", "timestamp": d, "horizon": 5, "future_return": 0.08 + i * 0.001}
        for i, d in enumerate(dates[:10])
    ])
    r = ev.run("E001", "v1", events, labels, horizon=5)
    assert r.n == 10 and r.mean > 0.07 and r.win_rate == 1.0
    assert r.mae <= r.mfe
