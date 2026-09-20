"""sizing_skips 按 run 隔离回归（PR13-03）。

要求：
  - 同一实例相同高价信号连续跑两次，各结果都只有一条本次跳过记录；
  - 下一次无信号，导出零跳过；
  - 上一份 EngineResult 的证据不被清空或修改；
  - 正常买入与旧 PIT 拒因仍通过。

全部合成输入，不读真实行情。
"""
import pandas as pd

from src.chanlun_trader.engine.asof import MarketDataStore
from src.chanlun_trader.engine.engine import BacktestEngineV2, EngineConfig
from src.chanlun_trader.engine.historical_eligibility import HistoricalEligibilityTable
from src.chanlun_trader.engine.security_state import SecurityMaster
from src.chanlun_trader.engine.signal import ExecutionPolicy, Side, Signal
from src.chanlun_trader.engine.time_types import tz_aware

CAL = [20240801, 20240802, 20240805]
SYM = "600900.SH"


def _engine(initial_cash=1.0):
    """本金 1 元 -> 买不起 1 手 -> sizing 为零，产生跳过记录。"""
    store = MarketDataStore()
    bars = pd.DataFrame(
        [{"date": d, "open": 10.0, "high": 10.2, "low": 9.8, "close": 10.0,
          "volume": 1000000.0, "amount": 10000000.0, "prev_close": 10.0}
         for d in CAL]).set_index("date")
    store.add_daily_raw(SYM, bars.copy())
    store.add_daily_qfq(SYM, bars[["open", "high", "low", "close",
                                   "volume", "amount"]].copy())
    t = HistoricalEligibilityTable()
    for d in (20240801, 20240802):
        a = "%04d-%02d-%02dT09:30:00+08:00" % (d // 10000, (d // 100) % 100, d % 100)
        t.add_daily(SYM, d, "NORMAL", "S", available_at=a, researcher_available_at=a)
    m = SecurityMaster()
    m.load_from_eligibility(t)
    cfg = EngineConfig(initial_cash=initial_cash, max_positions=1, max_position_weight=1.0,
                       max_holding_days=999, pit_eligibility_enforced=True,
                       persist_run_manifest=False, enable_index_filter=False,
                       index_filter_enabled=False)
    return BacktestEngineV2(store, CAL, config=cfg, security_master=m,
                            source_identity=("UNKNOWN", True))


def _signal(sig_id="s"):
    return Signal(strategy_id="T", signal_id=sig_id, symbol=SYM,
                  generated_at=tz_aware(2024, 8, 1, 15, 0), direction=Side.BUY,
                  score=1.0, execution_policy=ExecutionPolicy.NEXT_SESSION_OPEN)


def _events_skipped(res):
    return len([e for e in res.event_log._events
                if getattr(e, "event_type", "") == "SIZING_SKIPPED"])


def test_sizing_skips_are_isolated_per_run():
    """同一实例连续两次运行**同一份信号输入**，各结果各只有一条本次跳过记录。

    注意：`signals` 是引擎的输入队列，跨 run 保留；要复现"同输入再跑一次"，
    必须把输入重置为同一份，否则第二次会处理累积的多个信号（那是输入不同，
    不是记录累积）。
    """
    eng = _engine()
    eng.signals = [_signal("s1")]
    r1 = eng.run()
    assert len(r1.sizing_skips) == 1
    assert _events_skipped(r1) == 1

    eng.signals = [_signal("s1")]     # 同一份输入再跑一次
    r2 = eng.run()
    assert len(r2.sizing_skips) == 1, "第二次 run 不得带入第一次的记录"
    assert _events_skipped(r2) == 1


def test_previous_result_evidence_not_mutated():
    """上一份 EngineResult 的证据不得被后续 run 清空或修改。"""
    eng = _engine()
    eng.signals = [_signal("s1")]
    r1 = eng.run()
    snapshot = list(r1.sizing_skips)
    assert len(snapshot) == 1

    eng.signals = [_signal("s1")]
    eng.run()
    assert r1.sizing_skips == snapshot, "旧结果不得被后续 run 修改"
    assert len(r1.sizing_skips) == 1


def test_run_without_signals_exports_zero_skips():
    """下一次无信号 -> 导出零跳过。"""
    eng = _engine()
    eng.signals = [_signal("s1")]
    eng.run()

    eng.signals = []
    r2 = eng.run()
    assert len(r2.sizing_skips) == 0
    assert _events_skipped(r2) == 0


def test_normal_buy_still_fills_and_records_no_skip():
    """正常买入仍通过，且不产生跳过记录（防全拒假通过）。"""
    eng = _engine(initial_cash=10000.0)
    eng.add_signal(_signal("s1"))
    r = eng.run()
    buys = [t for t in r.ledger.trades if t.side == Side.BUY]
    assert len(buys) == 1
    assert len(r.sizing_skips) == 0


def test_pit_rejection_still_has_correct_reason():
    """旧 PIT 拒因仍正确（ST -> ST_NOT_ELIGIBLE），且不产生 sizing 跳过。"""
    store = MarketDataStore()
    bars = pd.DataFrame(
        [{"date": d, "open": 10.0, "high": 10.2, "low": 9.8, "close": 10.0,
          "volume": 1000000.0, "amount": 10000000.0, "prev_close": 10.0}
         for d in CAL]).set_index("date")
    store.add_daily_raw(SYM, bars.copy())
    store.add_daily_qfq(SYM, bars[["open", "high", "low", "close",
                                   "volume", "amount"]].copy())
    t = HistoricalEligibilityTable()
    for d in (20240801, 20240802):
        a = "%04d-%02d-%02dT09:30:00+08:00" % (d // 10000, (d // 100) % 100, d % 100)
        t.add_daily(SYM, d, "ST", "S", available_at=a, researcher_available_at=a)
    m = SecurityMaster()
    m.load_from_eligibility(t)
    cfg = EngineConfig(initial_cash=10000.0, max_positions=1, max_position_weight=1.0,
                       max_holding_days=999, pit_eligibility_enforced=True,
                       persist_run_manifest=False, enable_index_filter=False,
                       index_filter_enabled=False)
    eng = BacktestEngineV2(store, CAL, config=cfg, security_master=m,
                           source_identity=("UNKNOWN", True))
    eng.add_signal(_signal("s1"))
    r = eng.run()
    buys = [t for t in r.ledger.trades if t.side == Side.BUY]
    assert buys == []
    statuses = [(o.status.value, o.reason_code) for o in r.orders.orders.values()]
    assert statuses[0] == ("REJECTED", "ST_NOT_ELIGIBLE")
    assert len(r.sizing_skips) == 0