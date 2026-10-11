"""合成覆盖检查：省去重复状态查询时仍保留原缺口与输入身份。"""
from collections import Counter

import pytest

from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.universe_account_inputs_v1 import (
    prepare_universe_account_inputs_v1,
)
from test_universe_account_inputs_v1 import DAYS, SYMBOLS, valid_universe_bundle_v1


CASES = {
    "known": None,
    "midday": "UNIVERSE_STATE_NOT_YET_AVAILABLE",
    "after_close": "UNIVERSE_STATE_NOT_YET_AVAILABLE",
    "unknown_status": "UNIVERSE_STATE_AVAILABILITY_UNKNOWN",
    "unknown_timestamp": "UNIVERSE_STATE_AVAILABILITY_UNKNOWN",
    "invalid_timestamp": "UNIVERSE_STATE_AVAILABILITY_UNKNOWN",
    "naive_timestamp": "UNIVERSE_STATE_NOT_YET_AVAILABLE",
    "source": "UNIVERSE_STATE_SOURCE_UNREGISTERED",
    "board": "UNIVERSE_STATE_BOARD_CONFLICT",
    "lifecycle": "UNIVERSE_STATE_LIFECYCLE_CONFLICT",
}

# 修改前实现对下列小型合成夹具的实际输出，分别冻结完整 coverage 与 input_identity。
LEGACY_IDENTITIES = {
    ('daily', 'known'): (
        '2a10a09578ad41a05e1c8b251de6affe7b646900b3df5a7acb98691973b601cf',
        '2c7734c512a605a1c02da38607b1ad600ed24e69c1d52bfc6bb4ac355b10a4d8'),
    ('daily', 'midday'): (
        '690e4006408984dae1a1e93586902a7de369249563cf8bd54cac77000b110ae3',
        '7065a0b9cd6281d914318fa0428cbc49df2839e25a5ae7099524a26b72e9dba1'),
    ('daily', 'after_close'): (
        '690e4006408984dae1a1e93586902a7de369249563cf8bd54cac77000b110ae3',
        'e058e5c07312d1f6422b62f766bee9dd685e315c6d75da072671624c5a3794a2'),
    ('daily', 'unknown_status'): (
        '66e612d1828fd7fb2998d25e56353eb27e404593701fa828a5c22bb4b947f7d9',
        '5b92932d5b582f32058326e4b171880cac8c203097da421e1a6e9a4d9a98156a'),
    ('daily', 'unknown_timestamp'): (
        '66e612d1828fd7fb2998d25e56353eb27e404593701fa828a5c22bb4b947f7d9',
        '68cc38f3a6313611601a3e9cc0888b973beadbd32832f35175ba0dc5b4564983'),
    ('daily', 'invalid_timestamp'): (
        '66e612d1828fd7fb2998d25e56353eb27e404593701fa828a5c22bb4b947f7d9',
        'c35c389baf3b61309a3ff43c48e5d34c4e0b7e8586125e9528809314ea7d0e63'),
    ('daily', 'naive_timestamp'): (
        '690e4006408984dae1a1e93586902a7de369249563cf8bd54cac77000b110ae3',
        '4aed0f6af8029596879a8d3f8e14b8987915ec0ccafb6e83fadf3df11493363c'),
    ('daily', 'source'): (
        '8a908ed40cc81535990bafc1f44668590c11d529ec1a1127fdcd8e49e5357ccf',
        '8be3f43d61714e2aca9738b6583e4709408c154029054f0bab1b5f12c5444b6c'),
    ('daily', 'board'): (
        'b2d0e82ed5722da58762f469fc55d101183da378a88dc1398d6f456aece812bc',
        '6b28169ee761185d323adeb986c7250922b8cbc4fd47f78a0840f43ff036f663'),
    ('daily', 'lifecycle'): (
        '08b2c7943714bc09625d848cd7d01d288722ae493e77f93d72ea6f287a10bf1d',
        '54b14be6a8ebb9624c0ba7d5a2407d3ad60171b224d846c79f8496a63b4b5ca0'),
    ('interval', 'known'): (
        '2a10a09578ad41a05e1c8b251de6affe7b646900b3df5a7acb98691973b601cf',
        'd5e093d8e6a5236fb5f1b5a99c4bcb1293eef8da2b11b9f624704d0b20230296'),
    ('interval', 'midday'): (
        '05a322edf6201e6d4fe4c6ea1a59cabe1baf85100ee94e81612a7914c71eb147',
        '64af2f20e1b8a8ded0ed29cf704a0e86a8f6cec9bf970cb027a2b7e14d135523'),
    ('interval', 'after_close'): (
        '05a322edf6201e6d4fe4c6ea1a59cabe1baf85100ee94e81612a7914c71eb147',
        '35049aec4374ff0e8f7500aad87410b2df6f138fc220acd86acb50f6d0bbee50'),
    ('interval', 'unknown_status'): (
        '66e612d1828fd7fb2998d25e56353eb27e404593701fa828a5c22bb4b947f7d9',
        '48791931c6f67a1bc149fa8bd592a6cb8a71d5dbc694e96df8e14edef75f9a6e'),
    ('interval', 'unknown_timestamp'): (
        '66e612d1828fd7fb2998d25e56353eb27e404593701fa828a5c22bb4b947f7d9',
        '6adda2095b84e23c369eef235e451ff9ef7ea600347b1db8ecf54e1eb9ab3788'),
    ('interval', 'invalid_timestamp'): (
        '66e612d1828fd7fb2998d25e56353eb27e404593701fa828a5c22bb4b947f7d9',
        '57aee20ae9a154e13abcb03524999763c0bd75ae1a73c10b7617244f417b6d24'),
    ('interval', 'naive_timestamp'): (
        '690e4006408984dae1a1e93586902a7de369249563cf8bd54cac77000b110ae3',
        '2122f61206465d0d835da46a7b8e69c6d50cc2ad7eab72df951c19fd7246622c'),
    ('interval', 'source'): (
        '8a908ed40cc81535990bafc1f44668590c11d529ec1a1127fdcd8e49e5357ccf',
        'fde01b1d5cd44e510ff655336ea4eacd0aa5bad8a6b8ba4bc3efc0f27603ff46'),
    ('interval', 'board'): (
        'b2d0e82ed5722da58762f469fc55d101183da378a88dc1398d6f456aece812bc',
        '0b9f226171cff9eca7499daa23baf8b12ca69523c9dcf72590eb4b51cf87ba24'),
    ('interval', 'lifecycle'): (
        '08b2c7943714bc09625d848cd7d01d288722ae493e77f93d72ea6f287a10bf1d',
        '544ba82a7f73f937f6887ee15c3664e6f15cec656f4d2d3a9a5fdb34d773ceb9'),
}


def coverage_bundle(axis, case):
    bundle, window = valid_universe_bundle_v1()
    window["account_start"] = DAYS[0]
    states = bundle["states"]
    date_column = "trade_date"
    if axis == "interval":
        states = states.groupby("symbol", sort=False).head(1).copy()
        states = states.rename(columns={"trade_date": "effective_date"})
        states["valid_to"] = DAYS[-1]
        date_column = "effective_date"
    if case in {"known", "midday", "after_close", "naive_timestamp"}:
        hour = {"known": "09:00:00", "midday": "12:00:00",
                "after_close": "16:00:00", "naive_timestamp": "09:00:00"}[case]
        suffix = "" if case == "naive_timestamp" else "+08:00"
        states["available_at"] = states[date_column].map(
            lambda day: f"{str(day)[:4]}-{str(day)[4:6]}-{str(day)[6:]}T{hour}{suffix}")
    elif case == "unknown_status":
        states["availability_status"] = "UNKNOWN"
    elif case == "unknown_timestamp":
        states["available_at"] = "UNKNOWN"
    elif case == "invalid_timestamp":
        states["available_at"] = "not-a-timestamp"
    elif case == "source":
        states["source"] = "UNREGISTERED"
    elif case == "board":
        states["board"] = "UNKNOWN"
    elif case == "lifecycle":
        states["delisted"] = True
    bundle["states"] = states
    # pandas 3 默认推断 string；固定夹具旧 object 类型，避免把类型升级混作身份变化。
    for name in ("daily", "states", "turn"):
        frame = bundle[name]
        for column in frame.select_dtypes(include=["object", "string"]).columns:
            frame[column] = frame[column].astype(object)
    return bundle, window


def traced_coverage(prepared, monkeypatch):
    state = prepared.state
    calls = []

    def traced(symbol, day, *, asof=None):
        result = state(symbol, day, asof=asof)
        calls.append((symbol, day, asof, result["state_known"]))
        return result

    monkeypatch.setattr(prepared, "state", traced)
    return prepared._coverage(), calls


@pytest.mark.parametrize("axis", ["daily", "interval"])
def test_known_open_state_does_not_repeat_the_same_state_query_at_close(axis, monkeypatch):
    bundle, window = coverage_bundle(axis, "known")
    prepared = prepare_universe_account_inputs_v1(bundle, window)
    coverage, calls = traced_coverage(prepared, monkeypatch)
    assert coverage == prepared.coverage
    assert len(calls) == len(SYMBOLS) * len(DAYS)
    assert all(asof is None and known for _, _, asof, known in calls)


@pytest.mark.parametrize("axis", ["daily", "interval"])
@pytest.mark.parametrize("case,reason", CASES.items())
def test_coverage_optimization_keeps_original_gaps_and_frozen_input_identity(
        axis, case, reason, monkeypatch):
    bundle, window = coverage_bundle(axis, case)
    prepared = prepare_universe_account_inputs_v1(bundle, window, stage="SCAN")
    coverage, calls = traced_coverage(prepared, monkeypatch)
    assert coverage == prepared.coverage
    assert (stable_hash(coverage), prepared.input_identity) == LEGACY_IDENTITIES[axis, case]
    opens = {(symbol, day): known for symbol, day, asof, known in calls if asof is None}
    closes = Counter((symbol, day) for symbol, day, asof, _ in calls if asof is not None)
    assert len(opens) == len(SYMBOLS) * len(DAYS)
    assert closes == Counter({key: 1 for key, known in opens.items() if not known})
    assert coverage["global_gaps"] == []
    if reason is None:
        assert coverage["gaps"] == []
        assert coverage["account_data_ready"] is True
    else:
        expected_days = 1 if axis == "interval" and case in {"midday", "after_close"} else len(DAYS)
        assert coverage["gaps"] == [
            {"symbol": symbol, "reason": reason, "count": expected_days,
             "first_date": DAYS[0], "last_date": DAYS[expected_days - 1],
             "example_dates": DAYS[:min(expected_days, 3)]}
            for symbol in sorted(SYMBOLS)
        ]
        assert coverage["account_data_ready"] is False
        with pytest.raises(ValueError, match=reason):
            prepare_universe_account_inputs_v1(bundle, window)


@pytest.mark.parametrize("axis", ["daily", "interval"])
@pytest.mark.parametrize("field", ["available_at", "effective_available_at", "researcher_available_at"])
def test_midday_visibility_keeps_the_open_gap_even_when_close_is_known(axis, field):
    bundle, window = coverage_bundle(axis, "midday")
    if field != "available_at":
        bundle["states"] = bundle["states"].rename(columns={"available_at": field})
    prepared = prepare_universe_account_inputs_v1(bundle, window, stage="SCAN")
    assert prepared.state(SYMBOLS[0], DAYS[0])["state_known"] is False
    assert prepared.state(SYMBOLS[0], DAYS[0],
                          asof=prepared._close_times[DAYS[0]])["state_known"] is True
    assert prepared.coverage["gaps"][0]["reason"] == "UNIVERSE_STATE_NOT_YET_AVAILABLE"


@pytest.mark.parametrize("axis", ["daily", "interval"])
def test_unknown_open_rechecks_and_preserves_a_different_close_gap(axis, monkeypatch):
    bundle, window = coverage_bundle(axis, "midday")
    bundle["states"]["researcher_available_at"] = "not-a-timestamp"
    prepared = prepare_universe_account_inputs_v1(bundle, window, stage="SCAN")
    coverage, calls = traced_coverage(prepared, monkeypatch)
    assert len(calls) == 2 * len(SYMBOLS) * len(DAYS)
    assert coverage == prepared.coverage
    gaps = {(gap["symbol"], gap["reason"]): gap["count"] for gap in coverage["gaps"]}
    for symbol in SYMBOLS:
        assert gaps[symbol, "UNIVERSE_STATE_NOT_YET_AVAILABLE"] == (
            len(DAYS) if axis == "daily" else 1)
        assert gaps[symbol, "UNIVERSE_STATE_AVAILABILITY_UNKNOWN"] == len(DAYS)
    assert len(gaps) == 2 * len(SYMBOLS)
    assert coverage["account_data_ready"] is False
