"""PIT 历史资格合同回归（语义移植自 V5 已审阅修复）。

覆盖合同：
  A. 历史资格 NORMAL/ST/UNKNOWN/CONFLICT；日度缺失；可用时间与有效期；
     同状态多来源约束合并；事件到期不回退旧 NORMAL；冲突与拒因；
     Table→SecurityMaster→Broker→Ledger 一致性。
  D. sizing 为零保留理由（不自动递补）。

全部使用**合成输入**，不读取真实行情、不访问 data/、不触碰封存期。
"""
import pandas as pd
import pytest

from src.chanlun_trader.engine.asof import MarketDataStore
from src.chanlun_trader.engine.engine import BacktestEngineV2, EngineConfig
from src.chanlun_trader.engine.historical_eligibility import (
    AVAIL_UNKNOWN, EligibilityRecord, HistoricalEligibilityTable)
from src.chanlun_trader.engine.security_state import SecurityMaster
from src.chanlun_trader.engine.signal import ExecutionPolicy, Side, Signal
from src.chanlun_trader.engine.time_types import tz_aware

CAL = [20240801, 20240802, 20240805, 20240806, 20240807]
SYM = "600900.SH"


def _store(symbols=(SYM,)):
    store = MarketDataStore()
    bars = pd.DataFrame(
        [{"date": d, "open": 10.0, "high": 10.2, "low": 9.8, "close": 10.0,
          "volume": 1000000.0, "amount": 10000000.0, "prev_close": 10.0}
         for d in CAL]).set_index("date")
    for s in symbols:
        store.add_daily_raw(s, bars.copy())
        store.add_daily_qfq(s, bars[["open", "high", "low", "close",
                                     "volume", "amount"]].copy())
    return store


def _run(table, signal_date, initial_cash=10000.0, symbols=(SYM,), symbol=SYM):
    """真实 Table→SecurityMaster→Broker→Ledger 运行。"""
    master = SecurityMaster()
    master.load_from_eligibility(table)
    cfg = EngineConfig(initial_cash=initial_cash, max_positions=1, max_position_weight=1.0,
                       max_holding_days=999, pit_eligibility_enforced=True,
                       persist_run_manifest=False, enable_index_filter=False,
                       index_filter_enabled=False)
    eng = BacktestEngineV2(_store(symbols), CAL, config=cfg, security_master=master,
                           source_identity=("UNKNOWN", True))
    ts = tz_aware(signal_date // 10000, (signal_date // 100) % 100, signal_date % 100, 15, 0)
    eng.add_signal(Signal(strategy_id="T", signal_id="s", symbol=symbol, generated_at=ts,
                          direction=Side.BUY, score=1.0,
                          execution_policy=ExecutionPolicy.NEXT_SESSION_OPEN))
    res = eng.run()
    buys = [t for t in res.ledger.trades if t.side == Side.BUY]
    statuses = [(o.status.value, o.reason_code) for o in res.orders.orders.values()]
    return buys, statuses, res


def _rec(d, status, source, avail, valid_to=None):
    return EligibilityRecord(SYM, d, status, source, available_at=avail, valid_to=valid_to)


def _daily(t, day, status, source="S", avail=None):
    a = avail or ("%04d-%02d-%02dT09:30:00+08:00"
                  % (day // 10000, (day // 100) % 100, day % 100))
    t.add_daily(SYM, day, status, source, available_at=a, researcher_available_at=a)


# ---------------------------------------------------------------- 负向：约束必须生效

def test_st_is_rejected():
    t = HistoricalEligibilityTable()
    for d in (20240801, 20240802):
        _daily(t, d, "ST")
    buys, statuses, _ = _run(t, 20240801)
    assert buys == []
    assert statuses[0] == ("REJECTED", "ST_NOT_ELIGIBLE")


def test_unknown_is_rejected():
    t = HistoricalEligibilityTable()
    for d in (20240801, 20240802):
        _daily(t, d, "UNKNOWN")
    buys, statuses, _ = _run(t, 20240801)
    assert buys == []
    assert statuses[0] == ("REJECTED", "ST_STATUS_UNKNOWN_FAIL_CLOSED")


def test_missing_daily_row_is_unknown():
    """日度观测缺行 -> UNKNOWN（不沿用上一观测）。"""
    t = HistoricalEligibilityTable()
    _daily(t, 20240801, "NORMAL")
    buys, statuses, _ = _run(t, 20240801)   # 成交日 8/2 无观测行
    assert buys == []
    assert statuses[0] == ("REJECTED", "ST_STATUS_UNKNOWN_FAIL_CLOSED")


def test_conflicting_daily_rows_are_conflict():
    """同日 NORMAL/ST/NORMAL -> CONFLICT（拒因必须是 CONFLICT，不是缺行）。"""
    t = HistoricalEligibilityTable()
    for s in ("NORMAL", "ST", "NORMAL"):
        _daily(t, 20240802, s, source="SRC_%s" % s)
    buys, statuses, _ = _run(t, 20240801)
    assert buys == []
    assert statuses[0] == ("REJECTED", "ST_STATUS_CONFLICT_FAIL_CLOSED")


def test_same_day_later_availability_is_not_discarded():
    """同日同状态多来源，一条显式更晚 -> 较晚约束必须生效。"""
    t = HistoricalEligibilityTable()
    t.add(_rec(20240801, "NORMAL", "A_EARLY", "2024-08-01T09:30:00+08:00"))
    t.add(_rec(20240801, "NORMAL", "Z_LATE", "2024-08-05T09:30:00+08:00"))
    t.finalize()
    buys, statuses, _ = _run(t, 20240801)
    assert buys == []
    assert statuses[0] == ("REJECTED", "ST_STATUS_UNKNOWN_FAIL_CLOSED")


def test_same_day_unknown_constraint_is_not_masked():
    """同日同状态多来源，一条 UNKNOWN -> UNKNOWN 优先。"""
    t = HistoricalEligibilityTable()
    t.add(_rec(20240801, "NORMAL", "A_KNOWN", "2024-08-01T09:30:00+08:00"))
    t.add(_rec(20240801, "NORMAL", "Z_UNKNOWN", AVAIL_UNKNOWN))
    t.finalize()
    buys, statuses, _ = _run(t, 20240801)
    assert buys == []
    assert statuses[0] == ("REJECTED", "ST_STATUS_UNKNOWN_FAIL_CLOSED")


def test_later_same_status_expiry_is_not_compacted_away():
    """较晚同状态记录显式到期，不得因状态相同被压缩丢弃。"""
    t = HistoricalEligibilityTable()
    t.add(_rec(20240801, "NORMAL", "SYNTH", "2024-08-01T09:30:00+08:00"))
    t.add(_rec(20240802, "NORMAL", "SYNTH", "2024-08-02T09:30:00+08:00", valid_to=20240802))
    t.finalize()
    assert t.status_on(SYM, 20240805, as_of="2025-01-01T09:30:00+08:00") == "UNKNOWN"
    buys, statuses, _ = _run(t, 20240802)
    assert buys == []
    assert statuses[0] == ("REJECTED", "ST_STATUS_UNKNOWN_FAIL_CLOSED")


def test_expired_latest_does_not_resurrect_old_normal():
    """最新生效事实过期 -> UNKNOWN；不得回退复活更早的 NORMAL。"""
    t = HistoricalEligibilityTable()
    t.add(_rec(20240801, "NORMAL", "SYNTH", "2024-08-01T09:30:00+08:00"))
    t.add(_rec(20240802, "ST", "SYNTH", "2024-08-02T09:30:00+08:00", valid_to=20240802))
    t.finalize()
    master = SecurityMaster()
    master.load_from_eligibility(t)
    st = master.as_of(SYM, tz_aware(2024, 8, 5, 9, 30))
    assert st.st_status == "UNKNOWN", "Master 不得复活被替代的旧 NORMAL"
    buys, statuses, _ = _run(t, 20240802)
    assert buys == []
    assert statuses[0] == ("REJECTED", "ST_STATUS_UNKNOWN_FAIL_CLOSED")


def test_next_session_rule_last_calendar_day_is_unknown():
    """NEXT_SESSION_OPEN 在日历末日无下一 session -> UNKNOWN，不得当无限制。"""
    t = HistoricalEligibilityTable()
    for d in CAL:
        later = [x for x in CAL if x > d]
        res_avail = ("%04d-%02d-%02dT09:30:00+08:00"
                     % (later[0] // 10000, (later[0] // 100) % 100, later[0] % 100)) \
            if later else AVAIL_UNKNOWN
        t.add_daily(SYM, d, "NORMAL", "S",
                    available_at="%04d-%02d-%02dT09:30:00+08:00"
                    % (d // 10000, (d // 100) % 100, d % 100),
                    researcher_available_at=res_avail)
    assert t.availability_of(SYM, CAL[-1]) == AVAIL_UNKNOWN
    buys, statuses, _ = _run(t, 20240806)
    assert buys == []
    assert statuses[0] == ("REJECTED", "ST_STATUS_UNKNOWN_FAIL_CLOSED")


def test_invalid_time_is_unknown_not_assumed_past():
    """非法可用时间 -> UNKNOWN（不默认过去已知）。"""
    t = HistoricalEligibilityTable()
    t.add_daily(SYM, 20240802, "NORMAL", "S",
                available_at="not-a-timestamp", researcher_available_at="not-a-timestamp")
    buys, statuses, _ = _run(t, 20240801)
    assert buys == []
    assert statuses[0] == ("REJECTED", "ST_STATUS_UNKNOWN_FAIL_CLOSED")


# ---------------------------------------------------------------- 正向：防假通过

def test_positive_same_day_known_fills():
    """正对照 1：连续观测且当日已知 -> 正常成交 900 股。"""
    t = HistoricalEligibilityTable()
    for d in (20240801, 20240802):
        _daily(t, d, "NORMAL")
    buys, statuses, _ = _run(t, 20240801)
    assert len(buys) == 1 and buys[0].quantity == 900
    assert statuses[0] == ("FILLED", "ENTRY_SIGNAL")


def test_positive_late_constraint_allows_buy_once_available():
    """正对照 2：显式更晚约束在时间到达后应放行。"""
    t = HistoricalEligibilityTable()
    t.add(_rec(20240801, "NORMAL", "SYNTH", "2024-08-05T09:30:00+08:00"))
    t.finalize()
    buys, _, _ = _run(t, 20240805)
    assert len(buys) == 1


def test_positive_event_persistent_not_expired_fills():
    """正对照 3：事件型未过期 -> 成交。"""
    t = HistoricalEligibilityTable()
    t.add(_rec(20240801, "NORMAL", "SYNTH", "2024-08-01T09:30:00+08:00",
               valid_to=20240805))
    t.finalize()
    buys, _, _ = _run(t, 20240801)
    assert len(buys) == 1


# ---------------------------------------------------------------- 顺序、幂等、合并

@pytest.mark.parametrize("reverse", [False, True])
def test_source_input_order_does_not_change_outcome(reverse):
    """来源/输入顺序不得改变结论（不按来源字母序隐式定优先级）。"""
    recs = [_rec(20240801, "NORMAL", "A_EARLY", "2024-08-01T09:30:00+08:00"),
            _rec(20240801, "NORMAL", "Z_LATE", "2024-08-05T09:30:00+08:00")]
    if reverse:
        recs = list(reversed(recs))
    t = HistoricalEligibilityTable()
    for r in recs:
        t.add(r)
    t.finalize()
    buys, _, _ = _run(t, 20240801)
    assert buys == []


def test_finalize_is_idempotent():
    """finalize 重复调用不得改变结果。"""
    t = HistoricalEligibilityTable()
    t.add(_rec(20240801, "NORMAL", "A", "2024-08-01T09:30:00+08:00"))
    t.add(_rec(20240801, "NORMAL", "Z", "2024-08-05T09:30:00+08:00"))
    t.finalize()
    first = [r.to_dict() for r in t._rows[SYM]]
    t.finalize()
    assert [r.to_dict() for r in t._rows[SYM]] == first


def test_earliest_valid_to_wins_on_merge():
    """多来源合并时有效期取**最早**（最保守）。"""
    t = HistoricalEligibilityTable()
    t.add(_rec(20240801, "NORMAL", "A", "2024-08-01T09:30:00+08:00", valid_to=20240801))
    t.add(_rec(20240801, "NORMAL", "Z", "2024-08-01T09:30:00+08:00", valid_to=20240805))
    t.finalize()
    assert t._rows[SYM][0].valid_to == 20240801


def test_identical_constraints_are_compacted():
    """完整约束等价的连续同状态应压缩（不产生记录膨胀）。"""
    t = HistoricalEligibilityTable()
    t.add(_rec(20240801, "NORMAL", "SYNTH", "2024-08-01T09:30:00+08:00"))
    t.add(_rec(20240802, "NORMAL", "SYNTH", "2024-08-01T09:30:00+08:00"))
    t.finalize()
    assert len(t._rows[SYM]) == 1


# ---------------------------------------------------------------- 板块涨跌幅与卖出

def test_price_limit_is_board_specific_not_blanket_5pct():
    """ST 不得一律 5%：主板 5%，创业板/科创板 20%。"""
    from src.chanlun_trader.engine.security_state import (ChinaPriceLimitModel,
                                                          SecurityState)

    cases = (("600900.SH", "MAIN", 0.05), ("300001.SZ", "CHINEXT", 0.20),
             ("688001.SH", "STAR", 0.20))
    for sym, board, expected in cases:
        m = SecurityMaster()
        m.add_state(SecurityState(symbol=sym, asof_date=20240801, is_st=True,
                                  st_status="ST", board=board))
        pct = ChinaPriceLimitModel(m, pit_enforced=True).limit_pct(
            sym, tz_aware(2024, 8, 1, 9, 30))
        assert abs(pct - expected) < 1e-9, (sym, board, pct, expected)


def test_holding_sell_not_blocked_by_st_entry_rule():
    """已持仓卖出不因入场资格规则被删除（ST 只拦买入，不拦卖出）。"""
    from src.chanlun_trader.engine.security_state import (ChinaPriceLimitModel,
                                                          SecurityState)

    m = SecurityMaster()
    m.add_state(SecurityState(symbol=SYM, asof_date=20240801, is_st=True,
                              st_status="ST", board="MAIN"))
    model = ChinaPriceLimitModel(m, pit_enforced=True)
    bar = {"open": 10.0, "high": 10.2, "low": 9.8, "close": 10.0,
           "volume": 1000000.0, "prev_close": 10.0}
    ts = tz_aware(2024, 8, 1, 9, 30)
    buy_ok, buy_reason = model.can_buy_at_open(SYM, ts, bar)
    sell_ok, sell_reason = model.can_sell_at_open(SYM, ts, bar)
    assert buy_ok is False and buy_reason == "ST_NOT_ELIGIBLE"
    assert sell_ok is True and sell_reason == "OK"


def test_sizing_zero_records_reason_without_fallback():
    """sizing 为零必须留下原因记录，且不自动递补下一名（保持原行为）。"""
    t = HistoricalEligibilityTable()
    for d in (20240801, 20240802):
        _daily(t, d, "NORMAL")
    buys, statuses, res = _run(t, 20240801, initial_cash=1.0)
    assert buys == []
    assert hasattr(res, "sizing_skips")
    assert len(res.sizing_skips) >= 1
    rec = res.sizing_skips[0]
    assert rec["behavior"] == "NO_ORDER_NO_FALLBACK_KEEP_CASH"
    assert rec["reason"] in ("SIZING_ZERO_AT_INTENT", "NO_REFERENCE_PRICE")