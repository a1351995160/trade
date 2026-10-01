"""只用合成证券身份和手算价格验证板块政策，不读取真实研究数据。"""
from copy import deepcopy

import pandas as pd
import pytest

from chanlun_trader.engine.security_state import ChinaPriceLimitModel, SecurityMaster, SecurityState
from chanlun_trader.research_factory import board_execution_policy_v1 as module
from chanlun_trader.research_factory.board_execution_policy_v1 import (
    BoardExecutionPolicyV1, BoardPriceLimitModelV1, board_policy_identity,
    canonical_board, describe_board_policy_v1,
)

CALENDAR = (20200821, 20200824, 20200825, 20200826, 20200827, 20200828, 20200831)


def state(symbol, board, *, st=False, listing_date=20100104, **extra):
    return {"symbol": symbol, "board": board, "listing_date": listing_date,
            "listing_sessions_before_calendar": 5, "st_status": "ST" if st else "NORMAL",
            "is_st": st, "listed": True, "delisted": False, **extra}


@pytest.mark.parametrize("symbol,source,expected", [
    ("000001.SZ", "SZ_MAIN", "SZ_MAIN"), ("600000.SH", "SH_MAIN", "SH_MAIN"),
    ("300001.SZ", "CHINEXT", "CHINEXT"), ("301001.SZ", "GEM", "CHINEXT"),
])
def test_board_identity_requires_code_exchange_and_source_to_agree(symbol, source, expected):
    assert canonical_board(symbol, source) == expected
    resolved = BoardExecutionPolicyV1(calendar=CALENDAR).resolve(symbol, 20200824, state(symbol, source))
    assert resolved.source_board == source
    assert resolved.board == expected
    assert resolved.lot_size == 100 and resolved.tick_size == "0.01"


@pytest.mark.parametrize("symbol,board,error", [
    ("300001.SZ", "SZ_MAIN", "BOARD_IDENTITY_CONFLICT"),
    ("600000.SH", "SZ_MAIN", "BOARD_IDENTITY_CONFLICT"),
    ("000001.SZ", "MAIN", "BOARD_IDENTITY_UNKNOWN"),
    ("000001.SZ", "UNKNOWN", "BOARD_IDENTITY_UNKNOWN"),
    ("300001.SH", "CHINEXT", "BOARD_SYMBOL_UNSUPPORTED"),
    ("688001.SH", "STAR", "BOARD_SYMBOL_UNSUPPORTED"),
    ("920001.BJ", "BSE", "BOARD_SYMBOL_UNSUPPORTED"),
])
def test_unknown_or_conflicting_board_never_falls_back_to_main(symbol, board, error):
    with pytest.raises(ValueError, match=error):
        canonical_board(symbol, board)


def test_historical_chinext_reform_and_st_are_effective_on_correct_session():
    policy = BoardExecutionPolicyV1(calendar=CALENDAR)
    normal = state("300001.SZ", "CHINEXT")
    risk = state("300001.SZ", "CHINEXT", st=True)
    assert policy.resolve("300001.SZ", 20200821, normal).limit_prices(10) == (11, 9)
    assert policy.resolve("300001.SZ", 20200821, risk).limit_prices(10) == (10.5, 9.5)
    assert policy.resolve("300001.SZ", 20200824, normal).limit_prices(10) == (12, 8)
    assert policy.resolve("300001.SZ", 20200824, risk).limit_prices(10) == (12, 8)
    for symbol, board in (("000001.SZ", "SZ_MAIN"), ("600000.SH", "SH_MAIN")):
        assert policy.resolve(symbol, 20200824, state(symbol, board)).limit_prices(10) == (11, 9)
        assert policy.resolve(symbol, 20200824, state(symbol, board, st=True)).limit_prices(10) == (10.5, 9.5)


def test_chinext_registered_ipo_fifth_and_sixth_exchange_sessions():
    policy = BoardExecutionPolicyV1(calendar=CALENDAR)
    security = state("300999.SZ", "GEM", listing_date=20200824)
    fifth = policy.resolve("300999.SZ", 20200828, security)
    sixth = policy.resolve("300999.SZ", 20200831, security)
    assert fifth.listing_session == 5 and fifth.no_price_limit
    assert fifth.limit_prices(10) == (None, None)
    assert sixth.listing_session == 6 and not sixth.no_price_limit
    assert sixth.limit_prices(10) == (12, 8)


@pytest.mark.parametrize("symbol,board", [("001999.SZ", "SZ_MAIN"), ("601999.SH", "SH_MAIN")])
def test_main_registered_ipo_fifth_sixth_and_legacy_asymmetric_day(symbol, board):
    calendar = (20230407, 20230410, 20230411, 20230412, 20230413, 20230414, 20230417)
    policy = BoardExecutionPolicyV1(calendar=calendar)
    security = state(symbol, board, listing_date=20230410)
    assert policy.resolve(symbol, 20230414, security).no_price_limit
    assert policy.resolve(symbol, 20230417, security).limit_prices(10) == (11, 9)
    old_ipo = policy.resolve(symbol, 20230407, state(symbol, board, listing_date=20230407))
    assert old_ipo.price_reference == "ISSUE_PRICE"
    assert old_ipo.limit_prices(10) == (14.4, 6.4)


def test_decimal_half_up_does_not_use_python_bankers_rounding():
    policy = BoardExecutionPolicyV1(calendar=(20230410,))
    resolved = policy.resolve("600000.SH", 20230410, state("600000.SH", "SH_MAIN", st=True))
    # 10.10 * 1.05 = 10.605，官方四舍五入为10.61；低价边界至少移动一个tick。
    assert resolved.limit_prices(10.1) == (10.61, 9.6)
    assert resolved.limit_prices(.01) == (.02, .01)


@pytest.mark.parametrize("changes,error", [
    ({"listing_date": None}, "BOARD_LISTING_DATE_REQUIRED"),
    ({"st_status": "UNKNOWN"}, "BOARD_ST_STATUS_UNKNOWN"),
    ({"is_st": True}, "BOARD_ST_STATUS_CONFLICT"),
    ({"special_trading_status": "RELISTING"}, "BOARD_SPECIAL_TRADING_SESSION_UNSUPPORTED"),
    ({"listing_date": 20200825}, "BOARD_BEFORE_LISTING"),
])
def test_missing_lifecycle_and_unsupported_special_sessions_fail_closed(changes, error):
    with pytest.raises(ValueError, match=error):
        BoardExecutionPolicyV1(calendar=CALENDAR).resolve("300001.SZ", 20200824,
            state("300001.SZ", "CHINEXT", **changes))


def test_lifecycle_and_calendar_conflict_are_not_repaired():
    policy = BoardExecutionPolicyV1(calendar=CALENDAR, listing_dates={"300001.SZ": 20200824})
    with pytest.raises(ValueError, match="BOARD_LISTING_DATE_CONFLICT"):
        policy.resolve("300001.SZ", 20200824, state("300001.SZ", "CHINEXT", listing_date=20200821))
    with pytest.raises(ValueError, match="BOARD_TRADE_DATE_NOT_IN_CALENDAR"):
        policy.resolve("300001.SZ", 20200823, state("300001.SZ", "CHINEXT"))
    with pytest.raises(ValueError, match="BOARD_POLICY_DATE_UNSUPPORTED"):
        BoardExecutionPolicyV1(calendar=(20250801,)).resolve("300001.SZ", 20250801,
            state("300001.SZ", "CHINEXT"))
    with pytest.raises(ValueError, match="BOARD_CALENDAR_INVALID"):
        BoardExecutionPolicyV1(calendar=(20200824, 20200821))


def test_frozen_calendar_can_prove_special_period_is_over_without_guessing_age():
    security = state("300001.SZ", "CHINEXT")
    del security["listing_sessions_before_calendar"]
    policy = BoardExecutionPolicyV1(calendar=CALENDAR)
    with pytest.raises(ValueError, match="BOARD_LISTING_SESSION_HISTORY_REQUIRED"):
        policy.resolve("300001.SZ", 20200824, security)
    assert not policy.resolve("300001.SZ", 20200827, security).no_price_limit


def model(*, st=False, suspended=False, listing_date=20100104, board="CHINEXT"):
    master = SecurityMaster()
    security = SecurityState("300001.SZ", 20200821, board=board, is_st=st,
        st_status="ST" if st else "NORMAL", suspended=suspended,
        available_at="2020-08-21T09:00:00+08:00")
    security.listing_date = listing_date
    security.listing_sessions_before_calendar = 5
    master.add_state(security)
    return BoardPriceLimitModelV1(master, calendar=CALENDAR)


def test_opening_limit_boundaries_ignore_unobservable_day_high_low_and_volume():
    engine = model()
    ts = pd.Timestamp("2020-08-24T09:30:00+08:00")
    bar = {"open": 12, "prev_close": 10, "high": 12, "low": 8, "volume": 999999}
    assert engine.can_buy_at_open("300001.SZ", ts, bar) == (False, "LIMIT_UP_OPEN_DAILY_CONSERVATIVE")
    assert engine.can_sell_at_open("300001.SZ", ts, bar) == (True, "OK")
    altered = {**bar, "high": 9000, "low": .01, "volume": 0}
    assert engine.can_buy_at_open("300001.SZ", ts, altered) == engine.can_buy_at_open("300001.SZ", ts, bar)
    assert engine.can_buy_at_open("300001.SZ", ts, {**bar, "open": 11.99}) == (True, "OK")
    down = {**bar, "open": 8}
    assert engine.can_sell_at_open("300001.SZ", ts, down) == (False, "LIMIT_DOWN_OPEN_DAILY_CONSERVATIVE")
    assert engine.can_buy_at_open("300001.SZ", ts, down) == (True, "OK")
    with pytest.raises(ValueError, match="BOARD_OPEN_OUTSIDE_PRICE_LIMIT"):
        engine.can_buy_at_open("300001.SZ", ts, {**bar, "open": 12.01})


def test_st_exit_remains_possible_but_suspension_and_unknown_identity_block():
    ts = pd.Timestamp("2020-08-24T09:30:00+08:00")
    bar = {"open": 10, "prev_close": 10}
    risk = model(st=True)
    assert risk.can_buy_at_open("300001.SZ", ts, bar) == (False, "ST_NOT_ELIGIBLE")
    assert risk.can_sell_at_open("300001.SZ", ts, bar) == (True, "OK")
    assert model(suspended=True).can_sell_at_open("300001.SZ", ts, bar) == (False, "SUSPENDED")
    with pytest.raises(ValueError, match="BOARD_IDENTITY_CONFLICT"):
        model(board="SZ_MAIN").can_sell_at_open("300001.SZ", ts, bar)
    with pytest.raises(ValueError, match="UNSUPPORTED_LEGACY_IPO_OPENING_DAY"):
        model(listing_date=20200821).can_buy_at_open("300001.SZ",
            pd.Timestamp("2020-08-21T09:30:00+08:00"), bar)


def test_source_and_semantic_identity_are_immutable_to_callers_but_change_when_source_changes(monkeypatch):
    original = board_policy_identity()
    exposed = describe_board_policy_v1()
    assert all(len(row["sha256"]) == 64 and row["url"].startswith("https://") for row in exposed["sources"])
    exposed["sources"][0]["sha256"] = "0" * 64
    assert board_policy_identity() == original
    replacements = deepcopy(module._SOURCES)
    replacements[0]["sha256"] = "0" * 64
    monkeypatch.setattr(module, "_SOURCES", replacements)
    assert board_policy_identity() != original


def test_old_v2_policy_keeps_prior_behavior_and_new_model_requires_pit():
    ts = pd.Timestamp("2020-08-21T09:30:00+08:00")
    # 新历史政策不能悄悄修写旧V2；旧模型在改革前仍沿用原固定20%的语义。
    assert ChinaPriceLimitModel().limit_pct("300001.SZ", ts) == .20
    assert model().limit_pct("300001.SZ", ts) == .10
    with pytest.raises(ValueError, match="BOARD_POLICY_PIT_REQUIRED"):
        BoardPriceLimitModelV1(SecurityMaster(), calendar=CALENDAR, pit_enforced=False)


def test_pre_2023_shanghai_st_extreme_price_uses_official_absolute_tick():
    policy = BoardExecutionPolicyV1(calendar=CALENDAR)
    resolved = policy.resolve("600000.SH", 20200824, state("600000.SH", "SH_MAIN", st=True))
    # 0.09元 * 5% 不足0.01；旧沪市风险警示制度也明确绝对上下各一分钱。
    assert resolved.limit_prices(.09) == (.10, .08)
    assert "SSE_ST_MIN_TICK_2013" in resolved.source_ids
