"""全范围输入反例：日历、稀疏记录和未知资格不能互相替代。"""
from copy import deepcopy
import hashlib
import inspect
import weakref

import numpy as np
import pandas as pd
import pytest

from chanlun_trader.research_factory.board_execution_policy_v1 import board_policy_identity
from chanlun_trader.research_factory.universe_account_inputs_v1 import (
    _frame_identity, prepare_universe_account_inputs_v1, universe_input_identity_v1,
)
from chanlun_trader.research_factory.common import canonical_json, stable_hash


DAYS = [20240102, 20240103, 20240104, 20240105, 20240108]
SYMBOLS = ["000001.SZ", "600000.SH", "300001.SZ"]


def valid_universe_bundle_v1():
    """只用于合成工程验证，不冒充真实来源资料。"""
    daily, states = [], []
    for symbol in SYMBOLS:
        board = "SH_MAIN" if symbol.startswith("60") else (
            "CHINEXT" if symbol.startswith("30") else "SZ_MAIN")
        for day in DAYS:
            daily.append({"symbol": symbol, "date": day, "open": 10., "high": 11.,
                          "low": 9., "close": 10., "prev_close": 10.,
                          "volume": 100000., "amount": 1000000., "adjustflag": "3"})
            states.append({"symbol": symbol, "trade_date": day, "listed": True,
                "delisted": False, "universe_member": True, "eligibility_status": "ELIGIBLE",
                "st_status": "NORMAL", "suspension_status": "TRADING", "board": board,
                "source": "STATES", "availability_status": "MODELED", "listing_date": 20180102})
    bundle = {"profile": "HISTORICAL_MODELED", "daily": pd.DataFrame(daily),
        "states": pd.DataFrame(states), "turn": pd.DataFrame(columns=["symbol", "date"]),
        "calendar": DAYS, "events": [], "corporate_actions_complete": True,
        "corporate_action_coverage": {"symbols": SYMBOLS, "start": DAYS[0],
            "end": DAYS[-1], "complete": True, "source": "ACTIONS"},
        "source_hashes": {"STATES": "1" * 64, "DAILY": "2" * 64,
                           "ACTIONS": "3" * 64, "CALENDAR": "4" * 64},
        "calendar_source": "CALENDAR", "universe_identity": "5" * 64,
        "source_identity": "6" * 64, "board_policy_identity": board_policy_identity(),
        "price_basis": {"execution": "RAW"}, "historical_availability": "MODELED"}
    window = {"symbols": SYMBOLS, "feature_start": DAYS[0], "account_start": DAYS[2],
              "account_end": DAYS[-1], "calendar": DAYS}
    return bundle, window


def _legacy_frame_identity(frame, keys):
    """优化前的冻结身份算法，作为兼容性参考；不用于生产。"""
    selected = frame.sort_values(keys).reset_index(drop=True)
    digest = hashlib.sha256()
    digest.update(stable_hash([(str(c), str(selected[c].dtype))
                              for c in selected.columns]).encode())
    for name in selected.select_dtypes(include="object"):
        selected[name] = selected[name].map(lambda value: canonical_json(value)
            if isinstance(value, (dict, list, tuple, set, frozenset)) else value)
    digest.update(pd.util.hash_pandas_object(selected, index=False).values.tobytes())
    return digest.hexdigest()


def _identity_frame(case):
    values = {
        "plain_ints": [1, 2, 3, 4], "ints_none": [1, None, 2, 1],
        "plain_bool": [True, False, True, False], "bool_none": [True, None, False, True],
        "nested": [{"b": [2, None], "a": 1}, None, [1, {"x": 2}], (1, 2)],
        "sets": [{2, 1}, frozenset([2, 1]), None, {3}],
        "mixed": [1, "1", None, False], "strings": ["a", None, "b", "a"],
        "dates": [pd.Timestamp("2022-01-01"), None, pd.NaT, pd.Timestamp("2022-01-04")],
        "floats": [1.5, np.nan, None, np.inf],
    }
    frame = pd.DataFrame({"symbol": ["000001.SZ", "000001.SZ", "300001.SZ", "600000.SH"],
        "date": [20220103, 20220104, 20220103, 20220103],
        "value": pd.Series(deepcopy(values.get(case, values["mixed"])), dtype=object)})
    if case == "nullable_int":
        frame["value"] = pd.Series([1, pd.NA, 2, 1], dtype="Int64")
    elif case == "string_dtype":
        frame["value"] = pd.Series(["a", pd.NA, "b", "a"], dtype="string")
    elif case == "string_nan":
        if "na_value" not in inspect.signature(pd.StringDtype).parameters:
            pytest.skip("此 pandas 版本尚不能构造 StringDtype(na_value=np.nan)")
        frame["value"] = pd.Series(["a", None, "b", "a"], dtype=pd.StringDtype(na_value=np.nan))
    elif case in {"arrow_string", "arrow_int"}:
        import pyarrow as pa
        values = ["a", None, "b", "a"] if case == "arrow_string" else [1, None, 2, 1]
        frame["value"] = pd.Series(values, dtype=pd.ArrowDtype(pa.string() if case == "arrow_string" else pa.int64()))
    elif case in {"category_keys", "unordered_category_keys"}:
        frame["symbol"] = pd.Categorical(frame["symbol"],
            categories=["300001.SZ", "000001.SZ", "600000.SH"], ordered=case == "category_keys")
    elif case == "empty":
        return frame.iloc[:0]
    # 非连续且重复的索引不参与已有身份，不能为省内存改变这一语义。
    frame.index = pd.Index(["same", "same", "later", "earlier"], name="original_index")
    return frame


@pytest.mark.parametrize("case", ["plain_ints", "ints_none", "plain_bool", "bool_none",
    "nested", "sets", "mixed", "strings", "dates", "floats", "nullable_int", "string_dtype",
    "string_nan", "arrow_string", "arrow_int", "category_keys", "unordered_category_keys", "empty"])
@pytest.mark.parametrize("shuffled", [False, True])
def test_frame_identity_memory_optimization_preserves_legacy_sha_and_original_values(case, shuffled):
    frame = _identity_frame(case)
    if shuffled and len(frame):
        frame = frame.iloc[[2, 0, 3, 1]]
    original = frame.copy(deep=True)
    original_objects = canonical_json(frame.to_dict("records"))
    expected = _legacy_frame_identity(frame, ["symbol", "date"])
    assert _frame_identity(frame, ["symbol", "date"]) == expected
    pd.testing.assert_frame_equal(frame, original, check_exact=True)
    assert canonical_json(frame.to_dict("records")) == original_objects


@pytest.mark.parametrize("category_symbols", [False, True])
def test_frame_normalization_keeps_legacy_sort_dtype_and_an_independent_result(category_symbols):
    bundle, window = valid_universe_bundle_v1()
    prepared = prepare_universe_account_inputs_v1(bundle, window)
    value = bundle["daily"].iloc[::-1].copy()
    value["date"] = value.date.map(lambda d: pd.Timestamp(str(d)).strftime("%Y-%m-%d"))
    if category_symbols:
        value["symbol"] = pd.Categorical(value.symbol,
            categories=["300001.SZ", "000001.SZ", "600000.SH"], ordered=True)
    original = value.copy(deep=True)
    expected = value.copy()
    converted = {v: int(v.replace("-", "")) for v in value.date.unique()}
    expected["date"] = expected.date.map(converted)
    expected = expected.sort_values(["symbol", "date"]).reset_index(drop=True)
    normalized = prepared._frame(value, "date", "daily")
    pd.testing.assert_frame_equal(normalized, expected, check_exact=True)
    normalized.loc[0, "close"] = 999.
    pd.testing.assert_frame_equal(value, original, check_exact=True)
    prepared.assert_unchanged()


def test_input_normalization_and_identity_leave_every_original_bundle_frame_unchanged():
    bundle, window = valid_universe_bundle_v1()
    for name in ("daily", "states", "turn"):
        bundle[name] = bundle[name].iloc[::-1]
    bundle["states"]["source_context"] = pd.Series(
        [{"declared": True, "detail": [1, None]}] * len(bundle["states"]),
        index=bundle["states"].index, dtype=object)
    frames = {name: frame.copy(deep=True) for name, frame in bundle.items() if isinstance(frame, pd.DataFrame)}
    objects = {name: canonical_json(frame.to_dict("records")) for name, frame in frames.items()}
    prepared = prepare_universe_account_inputs_v1(bundle, window)
    expected_frames = {"daily": _legacy_frame_identity(prepared.daily, ["symbol", "date"]),
        "turn": _legacy_frame_identity(prepared.turn, ["symbol", "date"]),
        "states": _legacy_frame_identity(prepared.states, ["symbol", "trade_date"])}
    assert all(_frame_identity(prepared.bundle[name], ["symbol", "trade_date" if name == "states" else "date"])
               == expected for name, expected in expected_frames.items())
    prepared.assert_unchanged()
    for name, before in frames.items():
        pd.testing.assert_frame_equal(bundle[name], before, check_exact=True)
        assert canonical_json(bundle[name].to_dict("records")) == objects[name]


def test_fragmented_frame_copies_columns_independently_without_whole_frame_deep_copy(monkeypatch):
    bundle, window = valid_universe_bundle_v1()
    prepared = prepare_universe_account_inputs_v1(bundle, window)
    value = pd.DataFrame({"symbol": ["000001.SZ", "000001.SZ"], "date": DAYS[:2]})
    for number in range(19):
        value["extra_" + str(number)] = pd.Series([True, None], dtype=object)
    # 重复的非键列必须按位置完整保留，不能改用dict物化而丢掉一列。
    value = pd.concat([value, value[["extra_0"]]], axis=1)
    original = value.copy(deep=True)
    copy_frame = pd.DataFrame.copy
    def guard(frame, deep=True):
        if deep is True and len(frame) and len(frame.columns) >= 19:
            pytest.fail("逐列独立复制不应再深拷贝整张对象表")
        return copy_frame(frame, deep=deep)
    with monkeypatch.context() as boundary:
        boundary.setattr(pd.DataFrame, "copy", guard)
        normalized = prepared._frame(value, "date", "daily")
    pd.testing.assert_frame_equal(normalized, original, check_exact=True)
    assert normalized.columns.tolist() == value.columns.tolist()
    for index in range(len(value.columns)):
        assert not np.shares_memory(normalized.iloc[:, index].to_numpy(), value.iloc[:, index].to_numpy())
        normalized.iloc[0, index] = 20240103 if index == 1 else "changed"
    pd.testing.assert_frame_equal(value, original, check_exact=True)


def test_frame_identity_asks_old_dtype_selector_only_for_zero_row_metadata(monkeypatch):
    frame = _identity_frame("nested")
    expected = _legacy_frame_identity(frame, ["symbol", "date"])
    select = pd.DataFrame.select_dtypes
    calls = []
    def metadata_only(selected, *args, **kwargs):
        calls.append(selected.shape)
        assert len(selected) == 0, "获取类型列名不能复制所有对象行"
        return select(selected, *args, **kwargs)
    monkeypatch.setattr(pd.DataFrame, "select_dtypes", metadata_only)
    assert _frame_identity(frame, ["symbol", "date"]) == expected
    assert calls == [(0, len(frame.columns))]


@pytest.mark.parametrize("shuffled", [False, True])
def test_frame_identity_streams_all_column_types_in_legacy_order(shuffled):
    cases = ["plain_ints", "ints_none", "plain_bool", "bool_none", "nested", "sets",
        "mixed", "strings", "dates", "floats", "nullable_int", "string_dtype",
        "arrow_string", "arrow_int"]
    frame = _identity_frame("plain_ints").drop(columns="value")
    for case in cases:
        frame[case] = _identity_frame(case).value.array
    frame["nullable_bool"] = pd.Series([True, pd.NA, False, True], dtype="boolean").array
    frame["nullable_float"] = pd.Series([1.5, pd.NA, 2.5, np.nan], dtype="Float64").array
    frame["tz_dates"] = pd.to_datetime(["2022-01-01", None, "2022-01-03", "2022-01-04"], utc=True)
    frame["category_values"] = pd.Categorical(["a", None, "b", "a"], categories=["b", "a"], ordered=True)
    if shuffled:
        frame = frame.iloc[[3, 1, 2, 0]]
    before = frame.copy(deep=True)
    expected = _legacy_frame_identity(frame, ["symbol", "date"])
    assert _frame_identity(frame, ["symbol", "date"]) == expected
    # 列次序是既有身份的一部分，不能为了分组哈希而重排列。
    reversed_columns = frame[frame.columns[::-1]]
    assert _frame_identity(reversed_columns, ["symbol", "date"]) == _legacy_frame_identity(
        reversed_columns, ["symbol", "date"])
    assert _frame_identity(reversed_columns, ["symbol", "date"]) != expected
    pd.testing.assert_frame_equal(frame, before, check_exact=True)


def test_frame_identity_releases_mapped_columns_and_never_materializes_a_mapped_frame(monkeypatch):
    frame = _identity_frame("nested")
    for number in range(19):
        frame["context_" + str(number)] = pd.Series(
            [{"value": [number, None]}, None, (number, 2), {3, number}],
            index=frame.index, dtype=object)
    expected = _legacy_frame_identity(frame, ["symbol", "date"])
    original = canonical_json(frame.to_dict("records"))
    references, hashed_names = [], []
    old_map, old_hash = pd.Series.map, pd.util.hash_pandas_object

    def tracked_map(series, *args, **kwargs):
        assert not any(reference() is not None for reference in references), \
            "逐列处理不能保留前一列已映射的整列数组"
        mapped = old_map(series, *args, **kwargs)
        references.append(weakref.ref(mapped._values))
        return mapped

    def series_only(value, *args, **kwargs):
        assert isinstance(value, pd.Series), "哈希只应接收当前单列"
        assert kwargs.get("index") is False
        hashed_names.append(value.name)
        return old_hash(value, *args, **kwargs)

    def no_frame_assignment(*args, **kwargs):
        pytest.fail("身份计算不能把映射结果写回整张临时表")

    with monkeypatch.context() as boundary:
        boundary.setattr(pd.Series, "map", tracked_map)
        boundary.setattr(pd.util, "hash_pandas_object", series_only)
        boundary.setattr(pd.DataFrame, "__setitem__", no_frame_assignment)
        actual = _frame_identity(frame, ["symbol", "date"])
    assert actual == expected
    assert hashed_names == frame.columns.tolist()
    assert references and not any(reference() is not None for reference in references)
    assert canonical_json(frame.to_dict("records")) == original


def test_frame_identity_updates_digest_from_a_buffer_without_hash_bytes_copy(monkeypatch):
    frame = _identity_frame("mixed")
    expected = _legacy_frame_identity(frame, ["symbol", "date"])
    updates, old_sha = [], hashlib.sha256

    class RecordingDigest:
        def __init__(self, *args, **kwargs):
            self.digest = old_sha(*args, **kwargs)

        def update(self, value):
            updates.append(type(value))
            self.digest.update(value)

        def hexdigest(self):
            return self.digest.hexdigest()

    monkeypatch.setattr(hashlib, "sha256", RecordingDigest)
    assert _frame_identity(frame, ["symbol", "date"]) == expected
    assert updates[-1] is memoryview


def test_listing_projection_preserves_null_float_dates_source_priority_and_full_state_fields(monkeypatch):
    bundle, window = valid_universe_bundle_v1()
    bundle["source_hashes"]["PRIMARY"] = "a" * 64
    states = bundle["states"]
    states["listing_date"] = states.listing_date.astype(float)
    states.loc[states.trade_date.eq(DAYS[0]), "listing_date"] = np.nan
    states["listing_date_source"] = "PRIMARY"
    states["state_source"] = "STATES"
    states["ignored_context"] = [{"unknown": None, "flags": [True, False]} for _ in range(len(states))]
    original = canonical_json(states.to_dict("records"))
    groupby, observed = pd.DataFrame.groupby, []
    def record(frame, *args, **kwargs):
        if "listing_date" in frame:
            observed.append(frame.columns.tolist())
            assert frame.columns.tolist() == ["symbol", "listing_date", "listing_date_source"]
        return groupby(frame, *args, **kwargs)
    monkeypatch.setattr(pd.DataFrame, "groupby", record)
    prepared = prepare_universe_account_inputs_v1(bundle, window)
    assert observed and prepared.listing_dates == {symbol: 20180102 for symbol in SYMBOLS}
    assert prepared.listing_date_sources == {symbol: ["PRIMARY"] for symbol in SYMBOLS}
    assert prepared.states.columns.tolist() == states.columns.tolist()
    assert prepared.states.ignored_context.tolist() == states.ignored_context.tolist()
    assert canonical_json(states.to_dict("records")) == original
    assert _frame_identity(prepared.states, ["symbol", "trade_date"]) == _legacy_frame_identity(prepared.states, ["symbol", "trade_date"])


def test_valid_bar_date_projection_keeps_zero_volume_policy_and_original_daily_fields(monkeypatch):
    bundle, window = valid_universe_bundle_v1()
    daily = bundle["daily"]
    own = daily.symbol.eq("300001.SZ") & daily.date.eq(DAYS[0])
    daily.loc[own, ["volume", "amount"]] = 0.
    daily["unused_price_context"] = [None] * len(daily)
    groupby, observed = pd.DataFrame.groupby, []
    def record(frame, *args, **kwargs):
        if set(frame.columns) == {"symbol", "date"}:
            observed.append(frame.columns.tolist())
        return groupby(frame, *args, **kwargs)
    monkeypatch.setattr(pd.DataFrame, "groupby", record)
    prepared = prepare_universe_account_inputs_v1(bundle, window, stage="SCAN")
    assert observed == [["symbol", "date"]]
    assert prepared._bar_dates["300001.SZ"] == tuple(DAYS[1:])
    assert prepared.daily.columns.tolist() == daily.columns.tolist()
    assert prepared.bar("300001.SZ", DAYS[0])["volume"] == 0.
    assert prepared.bar("300001.SZ", DAYS[0])["unused_price_context"] is None


def test_three_boards_share_explicit_calendar_and_keep_modeled_status():
    bundle, window = valid_universe_bundle_v1()
    prepared = prepare_universe_account_inputs_v1(bundle, window)
    assert prepared.coverage["account_data_ready"] is True
    assert prepared.coverage["target_symbol_count"] == 3
    assert prepared.coverage["historical_availability"] == "MODELED"
    assert prepared.coverage["independent_confirmation_eligible"] is False
    assert prepared.state("300001.SZ", DAYS[2])["board"] == "CHINEXT"
    assert prepared.listing_dates == {s: 20180102 for s in SYMBOLS}
    assert prepared.session_index(DAYS[-1]) == 4
    prepared.assert_unchanged()


def test_ipo_before_listing_has_no_bar_and_warmup_is_stock_specific():
    bundle, window = valid_universe_bundle_v1()
    own = bundle["states"].symbol.eq("300001.SZ")
    bundle["states"].loc[own, "listing_date"] = DAYS[2]
    before = own & bundle["states"].trade_date.lt(DAYS[2])
    bundle["states"].loc[before, ["listed", "universe_member"]] = False
    bundle["states"].loc[before, "suspension_status"] = "NOT_LISTED"
    bundle["states"].loc[before, "eligibility_status"] = "INELIGIBLE"
    bundle["daily"] = bundle["daily"].loc[~(bundle["daily"].symbol.eq("300001.SZ")
                                                    & bundle["daily"].date.lt(DAYS[2]))]
    prepared = prepare_universe_account_inputs_v1(bundle, window, warmup_bars=3)
    assert prepared.scan_status("300001.SZ", DAYS[0])["status"] == "NOT_LISTED"
    assert prepared.bar("300001.SZ", DAYS[0]) is None
    assert prepared.scan_status("300001.SZ", DAYS[2])["status"] == "WARMUP_INSUFFICIENT"
    assert prepared.scan_status("000001.SZ", DAYS[2])["signal_ready"] is True
    assert prepared.scan_status("300001.SZ", DAYS[-1])["signal_ready"] is True


def test_confirmed_halt_can_be_sparse_but_missing_trading_bar_blocks_account():
    bundle, window = valid_universe_bundle_v1()
    bundle["daily"] = bundle["daily"].loc[~(bundle["daily"].symbol.eq("300001.SZ")
                                                    & bundle["daily"].date.eq(DAYS[2]))]
    scan = prepare_universe_account_inputs_v1(bundle, window, stage="SCAN")
    assert scan.scan_status("300001.SZ", DAYS[2])["gap_reasons"] == ["UNIVERSE_TRADING_BAR_MISSING"]
    with pytest.raises(ValueError, match="UNIVERSE_TRADING_BAR_MISSING"):
        prepare_universe_account_inputs_v1(bundle, window)
    bundle["states"].loc[bundle["states"].symbol.eq("300001.SZ")
        & bundle["states"].trade_date.eq(DAYS[2]), "suspension_status"] = "SUSPENDED"
    ready = prepare_universe_account_inputs_v1(bundle, window)
    assert ready.scan_status("300001.SZ", DAYS[2])["status"] == "SUSPENDED"
    assert ready.scan_status("300001.SZ", DAYS[2])["gap_reasons"] == []
    assert ready.session_index(DAYS[-1]) == 4
    assert len(ready.daily.loc[ready.daily.symbol.eq("300001.SZ")]) == 4


def test_actual_zero_volume_is_distinct_from_no_record():
    bundle, window = valid_universe_bundle_v1()
    bundle["daily"].loc[bundle["daily"].symbol.eq("300001.SZ")
        & bundle["daily"].date.eq(DAYS[2]), ["volume", "amount"]] = 0.
    prepared = prepare_universe_account_inputs_v1(bundle, window)
    status = prepared.scan_status("300001.SZ", DAYS[2])
    assert status["bar_present"] is True
    assert status["reported_zero_volume"] is True
    assert status["entry_eligible"] is False
    assert status["gap_reasons"] == []


@pytest.mark.parametrize("field,value,reason", [
    ("st_status", "UNKNOWN", "UNIVERSE_STATE_UNKNOWN"),
    ("suspension_status", "UNKNOWN", "UNIVERSE_STATE_UNKNOWN"),
    ("board", "SZ_MAIN", "UNIVERSE_STATE_BOARD_CONFLICT"),
    ("source", "UNKNOWN", "UNIVERSE_STATE_SOURCE_UNREGISTERED"),
    ("availability_status", "UNKNOWN", "UNIVERSE_STATE_AVAILABILITY_UNKNOWN"),
])
def test_unknown_or_conflicting_state_never_defaults_to_normal(field, value, reason):
    bundle, window = valid_universe_bundle_v1()
    bundle["states"].loc[bundle["states"].symbol.eq("300001.SZ"), field] = value
    scanned = prepare_universe_account_inputs_v1(bundle, window, stage="SCAN")
    assert scanned.state("300001.SZ", DAYS[2])["state_known"] is False
    assert scanned.scan_status("300001.SZ", DAYS[2])["entry_eligible"] is False
    with pytest.raises(ValueError, match=reason):
        prepare_universe_account_inputs_v1(bundle, window)


def test_st_and_removed_universe_members_still_have_account_management_inputs():
    bundle, window = valid_universe_bundle_v1()
    rows = bundle["states"].symbol.eq("300001.SZ") & bundle["states"].trade_date.ge(DAYS[2])
    bundle["states"].loc[rows, "st_status"] = "ST"
    bundle["states"].loc[rows, "eligibility_status"] = "INELIGIBLE"
    bundle["states"].loc[rows, "universe_member"] = False
    prepared = prepare_universe_account_inputs_v1(bundle, window)
    assert prepared.scan_status("300001.SZ", DAYS[2])["entry_eligible"] is False
    assert prepared.bar("300001.SZ", DAYS[2]) is not None
    assert prepared.state("300001.SZ", DAYS[2])["state_known"] is True
    assert "300001.SZ" in prepared.symbols


def test_intervals_are_indexed_with_explicit_end_and_never_carried_past_end():
    bundle, window = valid_universe_bundle_v1()
    states = bundle["states"].groupby("symbol", sort=False).head(1).copy()
    states = states.rename(columns={"trade_date": "effective_date"})
    states["valid_to"] = DAYS[-1]
    bundle["states"] = states
    prepared = prepare_universe_account_inputs_v1(bundle, window)
    assert len(prepared.states) == 3
    assert prepared.state("300001.SZ", DAYS[-1])["state_known"] is True
    states.loc[states.symbol.eq("300001.SZ"), "valid_to"] = DAYS[2]
    scanned = prepare_universe_account_inputs_v1(bundle, window, stage="SCAN")
    assert scanned.state("300001.SZ", DAYS[-1])["reason"] == "UNIVERSE_STATE_INTERVAL_EXPIRED"
    with pytest.raises(ValueError, match="INTERVAL_EXPIRED"):
        prepare_universe_account_inputs_v1(bundle, window)


def test_late_state_cannot_revive_earlier_normal_record():
    bundle, window = valid_universe_bundle_v1()
    bundle["states"]["available_at"] = "2024-01-09T09:30:00+08:00"
    scanned = prepare_universe_account_inputs_v1(bundle, window, stage="SCAN")
    assert scanned.state("300001.SZ", DAYS[2])["reason"] == "UNIVERSE_STATE_NOT_YET_AVAILABLE"
    with pytest.raises(ValueError, match="NOT_YET_AVAILABLE"):
        prepare_universe_account_inputs_v1(bundle, window)


def test_unknown_reference_can_prepare_signals_but_never_an_account():
    bundle, window = valid_universe_bundle_v1()
    bundle["daily"]["derived_previous_valid_close"] = bundle["daily"].prev_close
    bundle["daily"]["prev_close"] = float("nan")
    scanned = prepare_universe_account_inputs_v1(bundle, window, stage="SIGNAL")
    assert scanned.scan_status("300001.SZ", DAYS[2])["signal_ready"] is True
    assert scanned.coverage["account_data_ready"] is False
    assert scanned.bar("300001.SZ", DAYS[2])["derived_previous_valid_close"] == 10.
    with pytest.raises(ValueError, match="RAW_FIELD_INVALID:prev_close"):
        prepare_universe_account_inputs_v1(bundle, window)


def test_turn_is_required_only_if_the_rule_actually_uses_it():
    bundle, window = valid_universe_bundle_v1()
    prepare_universe_account_inputs_v1(bundle, window)
    with pytest.raises(ValueError, match="UNIVERSE_REQUIRED_TURN_MISSING"):
        prepare_universe_account_inputs_v1(bundle, window, required_fields=("turn",))


def test_cash_dividend_keeps_delayed_payment_and_explains_raw_reference():
    bundle, window = valid_universe_bundle_v1()
    event = {"event_id": "CASH_DELAYED", "symbol": "300001.SZ", "event_type": "CASH_DIVIDEND",
        "record_date": DAYS[2], "effective_date": DAYS[3], "payment_date": DAYS[-1],
        "source": "ACTIONS", "source_published_at": "2024-01-02", "units": "CNY_PER_SHARE",
        "terms": {"cash_per_share": .5, "tax_rule": {"kind": "DEFERRED_INDIVIDUAL_2015_101",
            "source": "https://www.chinatax.gov.cn/n810341/n810755/c1797427/content.html"}}}
    bundle["events"] = [event]
    changed = bundle["daily"].symbol.eq("300001.SZ") & bundle["daily"].date.ge(DAYS[3])
    bundle["daily"].loc[changed, ["open", "high", "low", "close", "prev_close"]] -= .5
    prepared = prepare_universe_account_inputs_v1(bundle, window)
    assert prepared.events[0]["payment_date"] == DAYS[-1]
    assert prepared.events[0]["payment_date"] > prepared.events[0]["effective_date"]
    assert prepared.bar("300001.SZ", DAYS[3])["prev_close"] == 9.5


def test_unexplained_ex_reference_and_non_cash_actions_are_blocking():
    bundle, window = valid_universe_bundle_v1()
    bundle["daily"].loc[bundle["daily"].symbol.eq("300001.SZ")
        & bundle["daily"].date.eq(DAYS[2]), "prev_close"] = 8.
    with pytest.raises(ValueError, match="UNEXPLAINED_PRICE_REFERENCE"):
        prepare_universe_account_inputs_v1(bundle, window)
    bundle, window = valid_universe_bundle_v1()
    bundle["events"] = [{"event_id": "BONUS_UNKNOWN", "symbol": "300001.SZ",
                         "event_type": "BONUS", "effective_date": DAYS[2]}]
    with pytest.raises(ValueError, match="UNSUPPORTED_ACTION:BONUS_UNKNOWN"):
        prepare_universe_account_inputs_v1(bundle, window)


def test_delisted_target_is_not_deleted_to_make_account_look_complete():
    bundle, window = valid_universe_bundle_v1()
    own = bundle["states"].symbol.eq("300001.SZ") & bundle["states"].trade_date.ge(DAYS[2])
    bundle["states"].loc[own, "listed"] = False
    bundle["states"].loc[own, "delisted"] = True
    bundle["states"].loc[own, "suspension_status"] = "DELISTED"
    bundle["daily"] = bundle["daily"].loc[~(bundle["daily"].symbol.eq("300001.SZ")
                                                    & bundle["daily"].date.ge(DAYS[2]))]
    scanned = prepare_universe_account_inputs_v1(bundle, window, stage="SCAN")
    assert scanned.coverage["target_symbol_count"] == 3
    with pytest.raises(ValueError, match="DELISTING_SETTLEMENT_UNSUPPORTED"):
        prepare_universe_account_inputs_v1(bundle, window)


def test_complete_boolean_without_registered_event_coverage_is_not_evidence():
    bundle, window = valid_universe_bundle_v1()
    bundle["corporate_action_coverage"]["source"] = "UNVERIFIED"
    with pytest.raises(ValueError, match="CORPORATE_COVERAGE_INVALID"):
        prepare_universe_account_inputs_v1(bundle, window)
    bundle, window = valid_universe_bundle_v1()
    bundle["corporate_action_coverage"]["end"] = DAYS[2]
    with pytest.raises(ValueError, match="CORPORATE_COVERAGE_GAP"):
        prepare_universe_account_inputs_v1(bundle, window)


def test_source_board_listing_and_price_mutations_change_identity():
    bundle, window = valid_universe_bundle_v1()
    prepared = prepare_universe_account_inputs_v1(bundle, window)
    identity = prepared.input_identity
    copied = deepcopy(prepared.bundle)
    copied["listing_dates"]["300001.SZ"] = 20180103
    assert universe_input_identity_v1(copied, window) != identity
    with pytest.raises(ValueError, match="LISTING_DATE_CONFLICT"):
        prepare_universe_account_inputs_v1(copied, window)
    prepared.bundle["daily"].loc[0, "close"] = 10.5
    with pytest.raises(ValueError, match="INPUT_IDENTITY_CHANGED"):
        prepared.assert_unchanged()


def test_old_main_board_or_wrong_policy_hash_cannot_be_used_for_new_scope():
    bundle, window = valid_universe_bundle_v1()
    bundle["board_policy_identity"] = "a" * 64
    with pytest.raises(ValueError, match="BOARD_POLICY_IDENTITY_CONFLICT"):
        prepare_universe_account_inputs_v1(bundle, window)


def test_empty_states_supports_only_explicit_gap_diagnostics():
    bundle, window = valid_universe_bundle_v1()
    bundle["states"] = pd.DataFrame(columns=["symbol", "trade_date"])
    prepared = prepare_universe_account_inputs_v1(bundle, window, stage="SCAN")
    assert prepared.coverage["account_data_ready"] is False
    assert prepared.coverage["target_symbol_count"] == 3
    with pytest.raises(ValueError, match="STATE_MISSING"):
        prepare_universe_account_inputs_v1(bundle, window)
