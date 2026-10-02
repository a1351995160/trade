"""数据合格范围由全池 ACCOUNT 证据确定，不依据策略结果缩池。"""
from copy import deepcopy

import pandas as pd
import pytest

from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.universe_account_inputs_v1 import (
    UniverseAccountInputsV1, audit_universe_account_inputs_v1, prepare_universe_account_inputs_v1,
    universe_input_identity_v1,
)
from chanlun_trader.research_factory.universe_qualified_scope_v1 import (
    qualify_universe_bundle, verify_qualified_scope_bundle,
)
from test_universe_account_inputs_v1 import DAYS, SYMBOLS, valid_universe_bundle_v1


def parent_prepared(bundle=None, window=None):
    if bundle is None:
        bundle, window = valid_universe_bundle_v1()
    inputs = prepare_universe_account_inputs_v1(bundle, window, stage="SCAN")
    return {"bundle": inputs.bundle, "window": inputs.window,
            "input_identity": inputs.input_identity,
            "qualification": {"coverage": inputs.coverage, "purpose": "EXPLORATORY"}}


def partial_parent():
    bundle, window = valid_universe_bundle_v1()
    bundle["corporate_actions_complete"] = False
    bundle["corporate_action_coverage"] = [
        {"symbols": [s], "start": DAYS[0], "end": DAYS[-1], "complete": True,
         "coverage_version": "CASH_AND_SHARES_V2", "account_complete": s != "300001.SZ",
         "source": "ACTIONS"} for s in SYMBOLS]
    bundle["events"] = [{"event_id": "unknown:bonus:with:colons", "symbol": "300001.SZ",
                         "event_type": "BONUS", "effective_date": DAYS[2]}]
    return parent_prepared(bundle, window)


def test_nonexecuting_audit_uses_account_semantics_without_weakening_default():
    parent = partial_parent()
    audit = audit_universe_account_inputs_v1(parent["bundle"], parent["window"])
    assert audit["stage"] == "ACCOUNT"
    assert not audit["account_executed"]
    assert any(g["scope"] == "EVENT" and g["symbol"] == "300001.SZ"
               and g["event_id"] == "unknown:bonus:with:colons" for g in audit["typed_gaps"])
    assert not audit["coverage"]["account_data_ready"]
    with pytest.raises(ValueError, match="UNIVERSE_ACCOUNT_INPUT_NOT_READY"):
        prepare_universe_account_inputs_v1(parent["bundle"], parent["window"])


def test_full_pool_projects_all_qualified_and_preserves_source_metadata():
    parent = partial_parent()
    before = {k: v.copy(deep=True) for k, v in parent["bundle"].items() if isinstance(v, pd.DataFrame)}
    child = qualify_universe_bundle(parent)
    receipt = child["scope_receipt"]
    assert child["ready"] is True
    assert receipt["target_symbols"] == sorted(SYMBOLS)
    assert receipt["qualified_symbols"] == ["000001.SZ", "600000.SH"]
    assert [r["symbol"] for r in receipt["excluded"]] == ["300001.SZ"]
    assert receipt["blocking_global_gaps"] == []
    assert child["bundle"]["source_hashes"] == parent["bundle"]["source_hashes"]
    assert child["bundle"]["historical_availability"] == "MODELED"
    assert child["bundle"]["corporate_actions_complete"] is True
    assert parent["bundle"]["corporate_actions_complete"] is False
    assert child["bundle"]["events"] == []
    assert child["qualification"]["independent_confirmation_eligible"] is False
    prepare_universe_account_inputs_v1(child["bundle"], child["window"])
    verify_qualified_scope_bundle(child["bundle"], child["window"], parent_prepared=parent)
    for name, frame in before.items():
        pd.testing.assert_frame_equal(frame, parent["bundle"][name], check_exact=True)


def test_qualification_avoids_whole_frame_and_second_owned_column_copies(monkeypatch):
    parent = partial_parent()
    frame_copy, series_copy = pd.DataFrame.copy, pd.Series.copy

    def bounded_frame_copy(frame, deep=True):
        if len(frame) and deep is not False:
            raise AssertionError("qualification copied a whole frame")
        return frame_copy(frame, deep=deep)

    def bounded_column_copy(series, deep=True):
        if len(series) and deep and series.name in {"open", "high", "low", "close", "volume", "amount"}:
            raise AssertionError("qualification recopied an owned data column")
        return series_copy(series, deep=deep)

    monkeypatch.setattr(pd.DataFrame, "copy", bounded_frame_copy)
    monkeypatch.setattr(pd.Series, "copy", bounded_column_copy)
    child = qualify_universe_bundle(parent)
    assert child["ready"] is True
    assert child["scope_receipt"]["qualified_symbols"] == ["000001.SZ", "600000.SH"]


@pytest.mark.parametrize("string_dtype", [object, "string"])
def test_projected_child_buffers_and_public_inputs_remain_mutation_isolated(string_dtype):
    parent = partial_parent()
    for name in ("daily", "turn", "states"):
        parent["bundle"][name]["symbol"] = parent["bundle"][name].symbol.astype(string_dtype)
    parent["input_identity"] = universe_input_identity_v1(parent["bundle"], parent["window"])
    child = qualify_universe_bundle(parent)
    inputs = prepare_universe_account_inputs_v1(child["bundle"], child["window"])
    child_close = child["bundle"]["daily"].close.iloc[0]
    parent["bundle"]["daily"].loc[0, "close"] += 100
    assert child["bundle"]["daily"].close.iloc[0] == child_close
    child["bundle"]["daily"].loc[0, "close"] += 200
    child["bundle"]["states"].loc[0, "st_status"] = "ST"
    assert inputs.daily.close.iloc[0] == child_close
    assert inputs.states.st_status.iloc[0] == "NORMAL"
    assert parent["bundle"]["states"].st_status.iloc[0] == "NORMAL"


def test_column_projection_preserves_the_previous_frozen_identity_and_receipt(monkeypatch):
    from chanlun_trader.research_factory import universe_qualified_scope_v1 as scope
    parent = partial_parent()
    expected = qualify_universe_bundle(parent)
    project = scope._project

    def previous_frame_projection(bundle, window, symbols):
        child, child_window = project(bundle, window, symbols)
        for name in ("daily", "turn", "states"):
            child[name] = bundle[name].loc[bundle[name].symbol.isin(symbols)].copy()
        return child, child_window

    monkeypatch.setattr(scope, "_project", previous_frame_projection)
    previous = qualify_universe_bundle(parent)
    assert expected["input_identity"] == previous["input_identity"]
    assert expected["scope_receipt"] == previous["scope_receipt"]
    for name in ("daily", "turn", "states"):
        pd.testing.assert_frame_equal(expected["bundle"][name], previous["bundle"][name], check_exact=True)


def test_nonexecuting_borrowed_audit_normalizes_without_mutating_caller_frames():
    bundle, window = valid_universe_bundle_v1()
    bundle["daily"] = bundle["daily"].iloc[::-1].copy()
    bundle["daily"]["date"] = bundle["daily"].date.map(lambda day: str(pd.Timestamp(str(day)).date()))
    frames = {name: bundle[name].copy(deep=True) for name in ("daily", "turn", "states")}
    expected = prepare_universe_account_inputs_v1(bundle, window)
    audit = audit_universe_account_inputs_v1(bundle, window)
    assert audit["input_identity"] == expected.input_identity
    assert audit["coverage"] == expected.coverage
    for name, frame in frames.items():
        pd.testing.assert_frame_equal(bundle[name], frame, check_exact=True)


@pytest.mark.parametrize("field,value,reason", [
    ("calendar_source", "UNKNOWN", "UNIVERSE_CALENDAR_SOURCE_UNREGISTERED"),
    ("board_policy_identity", "0" * 64, "UNIVERSE_BOARD_POLICY_IDENTITY_CONFLICT"),
    ("source_identity", None, "UNIVERSE_SOURCE_IDENTITY_MISSING"),
])
def test_common_gaps_block_instead_of_excluding_stocks(field, value, reason):
    bundle, window = valid_universe_bundle_v1()
    bundle[field] = value
    parent = parent_prepared(bundle, window)
    child = qualify_universe_bundle(parent)
    assert child["ready"] is False and child["bundle"] is None
    assert reason in child["scope_receipt"]["blocking_global_gaps"]
    assert child["scope_receipt"]["target_symbols"] == sorted(SYMBOLS)


def test_false_summary_without_explicit_incomplete_proof_remains_blocking():
    bundle, window = valid_universe_bundle_v1()
    bundle["corporate_actions_complete"] = False
    result = qualify_universe_bundle(parent_prepared(bundle, window))
    assert result["ready"] is False
    assert "UNIVERSE_CORPORATE_ACTIONS_INCOMPLETE" in result["scope_receipt"]["blocking_global_gaps"]


def test_source_bound_local_gaps_and_actual_rule_fields_determine_partition():
    bundle, window = valid_universe_bundle_v1()
    bundle["daily"] = bundle["daily"].loc[~(bundle["daily"].symbol.eq("300001.SZ")
                                           & bundle["daily"].date.eq(DAYS[2]))]
    parent = parent_prepared(bundle, window)
    child = qualify_universe_bundle(parent)
    assert child["scope_receipt"]["qualified_symbols"] == ["000001.SZ", "600000.SH"]
    assert "UNIVERSE_TRADING_BAR_MISSING" in child["scope_receipt"]["excluded"][0]["reasons"]
    blocked = qualify_universe_bundle(parent, required_fields=("turn",), warmup_bars=3)
    assert blocked["ready"] is False
    assert blocked["scope_receipt"]["qualified_symbols"] == []
    assert "UNIVERSE_QUALIFIED_SCOPE_EMPTY" in blocked["scope_receipt"]["blocking_global_gaps"]


def test_receipt_is_bound_to_input_identity_and_unknown_extra_fields_rejected():
    parent = partial_parent()
    child = qualify_universe_bundle(parent)
    before = child["input_identity"]
    changed = deepcopy(child["bundle"])
    changed["qualified_scope"]["excluded"][0]["reasons"].append("FAKE")
    assert universe_input_identity_v1(changed, child["window"]) != before
    with pytest.raises(ValueError, match="QUALIFIED_SCOPE"):
        verify_qualified_scope_bundle(changed, child["window"], parent_prepared=parent)


def test_rehashed_omission_of_a_qualified_symbol_cannot_forge_derivation():
    parent = partial_parent()
    child = qualify_universe_bundle(parent)
    bundle, window = deepcopy(child["bundle"]), deepcopy(child["window"])
    window["symbols"] = ["000001.SZ"]
    for name in ("daily", "states", "turn"):
        bundle[name] = bundle[name].loc[bundle[name].symbol.isin(window["symbols"])]
    for name in ("listing_dates", "listing_date_sources"):
        bundle[name] = {s: v for s, v in bundle[name].items() if s in window["symbols"]}
    bundle["corporate_action_coverage"] = [r for r in bundle["corporate_action_coverage"]
                                          if set(r["symbols"]) <= set(window["symbols"])]
    receipt = bundle["qualified_scope"]
    receipt["qualified_symbols"] = window["symbols"]
    fake_gap = {"scope": "SYMBOL", "symbol": "600000.SH", "reason": "FORGED",
                "count": 1, "first_date": DAYS[0], "last_date": DAYS[0], "example_dates": DAYS[:1]}
    receipt["account_audit"]["typed_gaps"].append(fake_gap)
    receipt["account_audit"]["audit_identity"] = stable_hash(
        {k: v for k, v in receipt["account_audit"].items() if k != "audit_identity"})
    receipt["excluded"].append({"symbol": "600000.SH", "reasons": ["FORGED"], "gaps": [fake_gap]})
    receipt["excluded"].sort(key=lambda r: r["symbol"])
    receipt["projected_input_identity"] = universe_input_identity_v1(
        {k: v for k, v in bundle.items() if k != "qualified_scope"}, window)
    receipt["scope_identity"] = stable_hash({k: v for k, v in receipt.items() if k != "scope_identity"})
    # 攻击者可以重算全部自报哈希；只有可信父输入的重算能证明派生正确。
    verify_qualified_scope_bundle(bundle, window)
    with pytest.raises(ValueError, match="QUALIFIED_SCOPE"):
        verify_qualified_scope_bundle(bundle, window, parent_prepared=parent)


def test_parent_change_and_wrong_rule_requirements_invalidate_receipt():
    parent = partial_parent()
    child = qualify_universe_bundle(parent, warmup_bars=2)
    other = deepcopy(parent)
    other["bundle"]["source_identity"] = "9" * 64
    other["input_identity"] = universe_input_identity_v1(other["bundle"], other["window"])
    with pytest.raises(ValueError, match="QUALIFIED_SCOPE"):
        verify_qualified_scope_bundle(child["bundle"], child["window"], parent_prepared=other)
    with pytest.raises(ValueError, match="QUALIFIED_SCOPE"):
        verify_qualified_scope_bundle(child["bundle"], child["window"], parent_prepared=parent,
                                      required_fields=("turn",))


def test_strategy_results_do_not_select_scope_and_no_nested_derivation():
    parent = partial_parent()
    child = qualify_universe_bundle(parent)
    other = deepcopy(parent)
    other["qualification"]["profit"] = -999999
    other["signals"] = [{"symbol": "300001.SZ", "result": "TRUE"}]
    assert qualify_universe_bundle(other)["scope_receipt"] == child["scope_receipt"]
    with pytest.raises(ValueError, match="QUALIFIED_SCOPE"):
        qualify_universe_bundle(child)


def test_malformed_event_identity_is_engineering_failure_not_stock_exclusion():
    bundle, window = valid_universe_bundle_v1()
    bundle["events"] = [{"event_id": "WRONG", "symbol": "999999.SZ", "event_type": "RIGHTS"}]
    with pytest.raises(ValueError, match="UNIVERSE_ACTION_ID_OR_SYMBOL_INVALID"):
        parent_prepared(bundle, window)


def test_unknown_future_global_reason_is_fail_closed(monkeypatch):
    parent = partial_parent()
    coverage = UniverseAccountInputsV1._coverage
    def new_check(inputs):
        result = coverage(inputs)
        result["global_gaps"].append("UNIVERSE_FUTURE_COMMON_RULE_UNKNOWN")
        result["account_data_ready"] = False
        return result
    monkeypatch.setattr(UniverseAccountInputsV1, "_coverage", new_check)
    result = qualify_universe_bundle(parent)
    assert result["ready"] is False
    assert "UNIVERSE_FUTURE_COMMON_RULE_UNKNOWN" in result["scope_receipt"]["blocking_global_gaps"]


def test_validated_unsupported_event_is_excluded_by_symbol_not_id_text():
    bundle, window = valid_universe_bundle_v1()
    bundle["events"] = [{"event_id": "600000.SH:looks-like-other-stock", "symbol": "300001.SZ",
                         "event_type": "RIGHTS", "source": "ACTIONS"}]
    result = qualify_universe_bundle(parent_prepared(bundle, window))
    assert result["ready"] is True
    assert [r["symbol"] for r in result["scope_receipt"]["excluded"]] == ["300001.SZ"]
    assert result["scope_receipt"]["qualified_symbols"] == ["000001.SZ", "600000.SH"]


def test_false_broad_proof_does_not_erase_complete_own_intervals():
    bundle, window = valid_universe_bundle_v1()
    bundle["corporate_actions_complete"] = False
    bundle["corporate_action_coverage"] = [
        {"symbols": SYMBOLS, "start": DAYS[0], "end": DAYS[-1], "complete": False, "source": "ACTIONS"},
        {"symbols": ["000001.SZ", "600000.SH"], "start": DAYS[0], "end": DAYS[-1],
         "complete": True, "source": "ACTIONS"}]
    result = qualify_universe_bundle(parent_prepared(bundle, window))
    assert result["ready"] is True
    assert result["bundle"]["corporate_action_coverage"][0]["complete"] is False
    assert result["scope_receipt"]["qualified_symbols"] == ["000001.SZ", "600000.SH"]


def test_known_cash_dates_and_tax_sources_survive_projection_exactly():
    bundle, window = valid_universe_bundle_v1()
    event = {"event_id": "CASH_SOURCE", "symbol": "000001.SZ", "event_type": "CASH_DIVIDEND",
        "record_date": DAYS[2], "effective_date": DAYS[3], "payment_date": DAYS[-1],
        "source": "ACTIONS", "source_published_at": "2024-01-02", "units": "CNY_PER_SHARE",
        "terms": {"cash_per_share": .5, "tax_rule": {"kind": "DEFERRED_INDIVIDUAL_2015_101",
            "source": "https://www.chinatax.gov.cn/n810341/n810755/c1797427/content.html"}}}
    bundle["events"] = [event, {"event_id": "UNSUPPORTED", "symbol": "300001.SZ", "event_type": "RIGHTS"}]
    own = bundle["daily"].symbol.eq("000001.SZ") & bundle["daily"].date.ge(DAYS[3])
    bundle["daily"].loc[own, ["open", "high", "low", "close", "prev_close"]] -= .5
    parent = parent_prepared(bundle, window)
    result = qualify_universe_bundle(parent)
    assert result["bundle"]["events"] == [event]
    assert parent["bundle"]["events"][0] == event


def conflicting_share_parent(effective=20240102):
    from test_universe_corporate_accounting_v2 import share_event
    bundle, window = valid_universe_bundle_v1()
    event = share_event()
    event.update(event_id="FIRST", source="ACTIONS", source_published_at="2023-12-25",
                 record_date=20231226, effective_date=effective,
                 share_credit_date=20240103, tradable_date=20240104)
    second = deepcopy(event)
    second["event_id"] = "SECOND"
    bundle["events"] = [event, second]
    return parent_prepared(bundle, window)


@pytest.mark.parametrize("effective", [20231227, 20240102])
def test_same_day_share_group_is_rejected_even_before_first_reference_bar(effective):
    parent = conflicting_share_parent(effective)
    audit = audit_universe_account_inputs_v1(parent["bundle"], parent["window"])
    assert any(g["scope"] == "SYMBOL" and g["symbol"] == "300001.SZ"
               and g["event_ids"] == ["FIRST", "SECOND"] for g in audit["typed_gaps"])
    with pytest.raises(ValueError, match="UNIVERSE_CORPORATE_ACTION_GROUP_UNSUPPORTED"):
        prepare_universe_account_inputs_v1(parent["bundle"], parent["window"])
    child = qualify_universe_bundle(parent)
    assert child["ready"] is True
    assert child["scope_receipt"]["qualified_symbols"] == ["000001.SZ", "600000.SH"]
    assert [g["symbol"] for g in child["scope_receipt"]["excluded"]] == ["300001.SZ"]


def test_combined_ledger_is_checked_even_when_price_group_accepts(monkeypatch):
    from chanlun_trader.research_factory import corporate_action_price_v2
    original = corporate_action_price_v2.grouped_price_actions_v2
    def price_group(events):
        return [] if len(events) > 1 else original(events)
    monkeypatch.setattr(corporate_action_price_v2, "grouped_price_actions_v2", price_group)
    result = qualify_universe_bundle(conflicting_share_parent())
    assert result["ready"] is True
    gap = result["scope_receipt"]["excluded"][0]["gaps"][0]
    assert gap["symbol"] == "300001.SZ"
    assert gap["detail"] == "SAME_DAY_SHARE_ACTIONS_REQUIRE_COMBINED_TERMS"


def test_unrecognized_group_validation_error_blocks_all_instead_of_localizing(monkeypatch):
    from chanlun_trader.research_factory import corporate_action_price_v2
    original = corporate_action_price_v2.grouped_price_actions_v2
    def price_group(events):
        if len(events) > 1:
            raise ValueError("NEW_MODEL_COMMON_FAILURE")
        return original(events)
    monkeypatch.setattr(corporate_action_price_v2, "grouped_price_actions_v2", price_group)
    result = qualify_universe_bundle(conflicting_share_parent())
    assert result["ready"] is False and result["bundle"] is None
    assert "UNIVERSE_CORPORATE_ACTION_GROUP_VALIDATION_UNKNOWN" in result["scope_receipt"]["blocking_global_gaps"]
    assert result["scope_receipt"]["excluded"] == []
