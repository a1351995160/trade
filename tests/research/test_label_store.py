import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import pandas as pd

from chanlun_trader.research.label import LabelStore


def test_label_compute(tmp_path):
    store = LabelStore(tmp_path)
    df = pd.DataFrame({
        "date": [20250601, 20250602, 20250603],
        "close": [10.0, 11.0, 12.1],
    })
    labels = store.compute_forward_returns(df)
    # 1d: 11/10-1=0.1; 2d: 12.1/10-1=0.21
    r1 = labels[(labels["timestamp"] == 20250601) & (labels["horizon"] == 1)]
    r2 = labels[(labels["timestamp"] == 20250601) & (labels["horizon"] == 2)]
    assert abs(r1.iloc[0]["future_return"] - 0.1) < 1e-12
    assert abs(r2.iloc[0]["future_return"] - 0.21) < 1e-12
    p = store.save("600000.SH", labels)
    assert p.exists()
    q = store.load("600000.SH", as_of=20250602)
    assert len(q) == 1  # 只有 T=20250601 的 1d label 已可观测
    q = store.load("600000.SH", as_of=20250603)
    assert len(q) == 3
