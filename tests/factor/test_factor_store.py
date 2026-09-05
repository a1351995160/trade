import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import pandas as pd
import pytest

from chanlun_trader.research.factor import FactorDefinition, FactorRegistry, FactorStore, FactorLineage
from chanlun_trader.research.guard import FinalTestAccessViolation


def make_def(fid="F001", ver="v1"):
    return FactorDefinition(
        factor_id=fid, version=ver, name="momentum_5d", family="Momentum",
        description="5日动量", formula="close/close.shift(5)-1", inputs=["daily_qfq_close"],
        lookback=5, frequency="DAILY", available_at_rule="T_CLOSE",
        feature_price_mode="qfq", PIT_safe=True, direction_hint="long",
        created_at="2026-08-18", status="DISCOVERED",
    )


def test_registry_roundtrip(tmp_path):
    reg = FactorRegistry(tmp_path / "reg.json")
    reg.register(make_def())
    reg.save()
    reg2 = FactorRegistry.load(tmp_path / "reg.json")
    f = reg2.get("F001", "v1")
    assert f is not None and f.family == "Momentum"


def test_store_put_query_pit(tmp_path):
    store = FactorStore(tmp_path)
    df = pd.DataFrame({
        "symbol": ["600000.SH", "600000.SH", "000001.SZ"],
        "timestamp": [20250601, 20250602, 20250801],  # 最后一笔越过 FINAL TEST
        "value": [0.1, 0.2, 0.3],
        "available_at": [20250601, 20250602, 20250801],
    })
    with pytest.raises(FinalTestAccessViolation):
        store.put("F001", "v1", df)

    df = df[df["timestamp"] < 20250801]
    p = store.put("F001", "v1", df)
    assert p.exists()
    q = store.query("F001", "v1", symbol="600000.SH", as_of=20250601)
    assert len(q) == 1 and q.iloc[0]["value"] == 0.1
    with pytest.raises(FileExistsError):
        store.put("F001", "v1", df)


def test_lineage_record(tmp_path):
    lin = FactorLineage(tmp_path / "lineage.jsonl")
    lin.record("F001", "v1", ["daily_raw"], "pct_change(5)", experiment_id="E001")
    df = lin.load()
    assert len(df) == 1 and df.iloc[0]["experiment_id"] == "E001"
