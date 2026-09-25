"""因果价格、来源资格和跨进程引擎重放的边界测试。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pandas as pd
import pytest

from chanlun_trader.engine.asof import MarketDataStore
from chanlun_trader.engine.engine import BacktestEngineV2, EngineConfig
from chanlun_trader.engine.signal import Side, Signal
from chanlun_trader.research_factory.causal_dividend_features_v1 import causal_hfq_bars
from chanlun_trader.research_factory.engine_replay_recovery_v1 import (
    economic_state, run_recoverable,
)
from chanlun_trader.research_factory.source_availability_v1 import (
    availability_at_decision, s1_source_qualification,
)
from chanlun_trader.research_factory.common import stable_hash


def _event(published="2024-06-06"):
    return {"event_id": "DIV-1", "symbol": "000001.SZ", "event_type": "CASH_DIVIDEND",
            "record_date": 20240613, "effective_date": 20240614,
            "source_published_at": published,
            "terms": {"cash_per_share": 0.719}}


def test_causal_dividend_prices_leave_past_unchanged_and_align_ex_reference():
    raw = pd.DataFrame([
        {"symbol": "000001.SZ", "date": 20240613, "open": 10.89,
         "high": 10.93, "low": 10.80, "close": 10.80, "prev_close": 10.88,
         "volume": 120_000, "amount": 1_300_000, "adjustflag": "3"},
        {"symbol": "000001.SZ", "date": 20240614, "open": 10.15,
         "high": 10.23, "low": 9.99, "close": 10.18, "prev_close": 10.08,
         "volume": 160_000, "amount": 1_650_000, "adjustflag": "3"},
    ])
    feature, evidence = causal_hfq_bars(raw, (_event(),))
    assert feature.iloc[0]["close"] == raw.iloc[0]["close"]
    assert feature.iloc[0]["amount"] == raw.iloc[0]["amount"]
    assert feature.iloc[1]["prev_close"] == pytest.approx(feature.iloc[0]["close"])
    assert feature.iloc[1]["close"] == pytest.approx(10.18 * 10.80 / 10.08)
    assert feature.iloc[1]["volume"] == raw.iloc[1]["volume"]
    assert evidence[0]["ex_date"] == 20240614
    before, _ = causal_hfq_bars(raw.iloc[:1], ())
    pd.testing.assert_frame_equal(feature.iloc[:1], before)
    with pytest.raises(ValueError, match="NOT_KNOWN_AT_RECORD"):
        causal_hfq_bars(raw, (_event("2024-06-14"),))
    changed = raw.copy()
    changed.loc[1, "prev_close"] = 10.50
    with pytest.raises(ValueError, match="REFERENCE_CONFLICT"):
        causal_hfq_bars(changed, (_event(),))


def test_source_qualification_requires_observed_timestamps_for_every_row():
    manifest = {"fetched_at_utc": "2024-06-14T07:10:00+00:00",
                "historical_available_at_verified": True}
    daily = pd.DataFrame([{"date": 20240614,
                           "source_published_at": "2024-06-14T15:05:00+08:00",
                           "availability_evidence_kind": "CONTEMPORANEOUS_CAPTURE"}])
    turn = daily.copy()
    states = pd.DataFrame([{"trade_date": 20240613, "source_lineage": "PROVIDER",
                            "source_published_at": "2024-06-13T15:05:00+08:00",
                            "availability_evidence_kind": "PROVIDER_PUBLISHED_AT"}])
    qualified = s1_source_qualification(manifest, daily, turn, states,
                                        account_end_date=20240614)
    assert qualified["strict_pit_status"] == "VERIFIED"
    assert availability_at_decision(decision_at="2024-06-14T15:30:00+08:00",
                                    observed_at="2024-06-14T16:00:00+08:00",
                                    evidence_kind="PROVIDER_PUBLISHED_AT") is False
    assert s1_source_qualification(manifest, daily.drop(columns="source_published_at"),
                                   turn, states, account_end_date=20240614)[
                                       "strict_pit_status"] == "BLOCKED"
    assert s1_source_qualification(manifest, daily,
                                   turn.assign(availability_evidence_kind="MODELED"),
                                   states, account_end_date=20240614)[
                                       "strict_pit_status"] == "BLOCKED"


def _engine(close_on_third=10.5, close_on_second=10.2):
    dates = [20240102, 20240103, 20240104]
    store = MarketDataStore()
    store.add_daily_raw("000001.SZ", pd.DataFrame({
        "open": [10.0, 10.1, 10.4], "high": [10.2, 10.3, close_on_third + 0.1],
        "low": [9.9, 10.0, 10.3], "close": [10.1, close_on_second, close_on_third],
        "volume": [100_000] * 3, "amount": [1_000_000] * 3,
        "prev_close": [10.0, 10.1, 10.2],
    }, index=dates))
    config = EngineConfig(initial_cash=100_000, max_positions=1, max_position_weight=0.5,
                          start_date=dates[0], end_date=dates[-1],
                          enable_index_filter=False, index_filter_enabled=False,
                          persist_run_manifest=False)
    engine = BacktestEngineV2(store, dates, config=config, seed=0)
    engine.add_signal(Signal(strategy_id="TEST", signal_id="BUY-1",
                             symbol="000001.SZ",
                             generated_at=pd.Timestamp("2024-01-02 15:30", tz="Asia/Shanghai"),
                             direction=Side.BUY))
    return engine


def test_replay_checkpoint_survives_process_exit_and_rejects_divergence(tmp_path):
    checkpoint = tmp_path / "checkpoint.json"
    source = '''
import os, sys
import pandas as pd
from chanlun_trader.engine.asof import MarketDataStore
from chanlun_trader.engine.engine import BacktestEngineV2, EngineConfig
from chanlun_trader.engine.signal import Side, Signal
from chanlun_trader.research_factory.engine_replay_recovery_v1 import run_recoverable, ReplayInterrupted
dates=[20240102,20240103,20240104]
store=MarketDataStore()
store.add_daily_raw("000001.SZ", pd.DataFrame({"open":[10.0,10.1,10.4],"high":[10.2,10.3,10.6],"low":[9.9,10.0,10.3],"close":[10.1,10.2,10.5],"volume":[100000]*3,"amount":[1000000]*3,"prev_close":[10.0,10.1,10.2]},index=dates))
config=EngineConfig(initial_cash=100000,max_positions=1,max_position_weight=.5,start_date=dates[0],end_date=dates[-1],enable_index_filter=False,index_filter_enabled=False,persist_run_manifest=False)
engine=BacktestEngineV2(store,dates,config=config,seed=0)
engine.add_signal(Signal(strategy_id="TEST",signal_id="BUY-1",symbol="000001.SZ",generated_at=pd.Timestamp("2024-01-02 15:30",tz="Asia/Shanghai"),direction=Side.BUY))
try:
    run_recoverable(engine,sys.argv[1],input_identity="BOUND_INPUT",stop_after_date=20240103)
except ReplayInterrupted:
    os._exit(17)
'''
    repo = Path(__file__).resolve().parents[2]
    environment = {**os.environ, "PYTHONPATH": str(repo / "src") + os.pathsep +
                   os.environ.get("PYTHONPATH", "")}
    interrupted = subprocess.run([sys.executable, "-c", source, str(checkpoint)],
                                 cwd=repo, env=environment, capture_output=True, text=True)
    assert interrupted.returncode == 17, interrupted.stderr
    receipt = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert receipt["event_timestamp"].startswith("2024-01-03")
    continuous = _engine()
    continuous.run()
    resumed = _engine()
    run_recoverable(resumed, checkpoint, input_identity="BOUND_INPUT")
    assert stable_hash(economic_state(resumed)) == stable_hash(economic_state(continuous))
    assert len(resumed.ledger.trades) == len(continuous.ledger.trades) == 1
    with pytest.raises(ValueError, match="RECEIPT_OR_INPUT_CONFLICT"):
        run_recoverable(_engine(), checkpoint, input_identity="CHANGED_INPUT")
    with pytest.raises(ValueError, match="PREFIX_DIVERGED"):
        run_recoverable(_engine(close_on_second=11.0), checkpoint,
                        input_identity="BOUND_INPUT")
    receipt["state_hash"] = "tampered"
    checkpoint.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(ValueError, match="RECEIPT_OR_INPUT_CONFLICT"):
        run_recoverable(_engine(), checkpoint, input_identity="BOUND_INPUT")
