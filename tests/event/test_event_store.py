import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import pandas as pd
import pytest

from chanlun_trader.research.event import EventDefinition, EventRegistry, EventStore
from chanlun_trader.research.guard import FinalTestAccessViolation


def test_event_store_pit(tmp_path):
    reg = EventRegistry(tmp_path / "reg.json")
    reg.register(EventDefinition(
        event_id="E001", version="v1", name="limit_up", family="LimitUp",
        description="涨停事件", event_time_semantics="TRADE_DATE",
        available_at_semantics="T_CLOSE", PIT_safe=True, inputs=["GP15"],
        created_at="2026-08-18", status="DISCOVERED",
    ))
    reg.save()

    store = EventStore(tmp_path)
    df = pd.DataFrame({
        "symbol": ["600000.SH", "600000.SH"],
        "event_time": [20250601, 20250801],
        "available_at": [20250601, 20250801],
        "event_type": ["ENTER", "ENTER"],
    })
    with pytest.raises(FinalTestAccessViolation):
        store.put("E001", "v1", df)
    df = df[df["event_time"] < 20250801]
    p = store.put("E001", "v1", df)
    assert p.exists()
    q = store.query("E001", "v1", as_of=20250602)
    assert len(q) == 1
