"""51类现有指标经同一个 V3 全范围 scanner：三板块保持相同公式与依赖。"""
from copy import deepcopy

import numpy as np
import pandas as pd
import pytest

from chanlun_trader.research_factory.research_rule_strategy_v3 import ResearchRuleStrategyV3, rule_capabilities
from chanlun_trader.research_factory.universe_account_inputs_v1 import UniverseAccountInputsV1
from chanlun_trader.research_factory.universe_signal_scan_v1 import UniverseSignalScanV1
from test_research_rule_strategy_v3 import node
from universe_test_fixture_v1 import fixture, proposal


CATALOG = rule_capabilities()["indicators"]


def common_three_board_inputs():
    """三只证券有同一段已知合成价格/量能；不证明真实行情资格。"""
    window, bundle = fixture(days_count=240)
    days = window["calendar"]
    axis = np.arange(len(days), dtype=float)
    close = 12 + .012 * axis + .7 * np.sin(axis * .37)
    volume = 1_000_000 * (1.2 + .3 * np.sin(axis * .51))
    raw = pd.DataFrame({"date": days, "open": close * .997,
        "high": close * 1.012, "low": close * .985, "close": close,
        "prev_close": np.r_[close[0], close[:-1]], "volume": volume, "amount": close * volume,
        "adjustflag": "3"})
    bundle["daily"] = pd.concat([raw.assign(symbol=symbol) for symbol in window["symbols"]], ignore_index=True)
    bundle["turn"] = bundle["daily"][["symbol", "date", "volume"]].copy()
    turn_by_day = dict(zip(days, .8 + .2 * np.cos(axis * .31)))
    bundle["turn"]["turn"] = bundle["turn"].date.map(turn_by_day)
    return UniverseAccountInputsV1(bundle, window, stage="SCAN")


def indicator_proposal(item, params=None, outputs=None):
    value = proposal()
    actual = {**item["params"], **(params or {})}
    value["indicator_instances"] = [{"instance_id": "tested", "id": item["id"],
                                      "version": item["version"], "params": actual}]
    comparisons = [node("ge", node("indicator", "tested", output=output, version=item["version"]),
                        node("const", value=0)) for output in (outputs or item["outputs"])]
    while len(comparisons) > 4:
        comparisons = [node("and", *comparisons[start:start + 4]) if len(comparisons[start:start + 4]) > 1
                       else comparisons[start] for start in range(0, len(comparisons), 4)]
    value["buy"] = comparisons[0] if len(comparisons) == 1 else node("and", *comparisons)
    value["sell"] = node("lt", node("field", "close"), node("const", value=0))
    return value


def test_parity_suite_covers_every_existing_indicator_without_new_catalog_items():
    assert len(CATALOG) == 51 and len({item["id"] for item in CATALOG}) == 51
    assert all(item["outputs"] for item in CATALOG)


@pytest.mark.parametrize("item", CATALOG, ids=lambda item: item["id"])
def test_every_registered_indicator_output_and_condition_matches_across_three_boards(item, monkeypatch):
    inputs = common_three_board_inputs()
    rule = ResearchRuleStrategyV3(indicator_proposal(item), strategy_id="THREE_BOARD_" + item["id"])
    calculated = []
    compute = rule.build_feature_matrix
    def record(bars, vendor_turn=None):
        result = compute(bars, vendor_turn)
        calculated.append(result)
        return result
    monkeypatch.setattr(rule, "build_feature_matrix", record)
    scanner = UniverseSignalScanV1(rule, inputs, batch_size=2)
    assert len(calculated) == 3 and set(scanner.conditions) == set(inputs.symbols)
    assert rule.definition["indicators"][0]["params"] == item["params"]
    assert rule.definition["indicators"][0]["version"] == item["version"]
    reference = calculated[0]
    for actual in calculated[1:]:
        pd.testing.assert_frame_equal(actual, reference)
    for output in item["outputs"]:
        column = "tested." + output
        assert column in reference and column + "__ready" in reference
        assert pd.api.types.is_numeric_dtype(reference[column]) or pd.api.types.is_bool_dtype(reference[column])
        assert reference[column + "__ready"].any(), (item["id"], output)
    assert {"tested." + output for output in item["outputs"]} == {
        column for column in reference if column.startswith("tested.") and not column.endswith("__ready")}
    base = scanner.conditions[inputs.symbols[0]]
    assert base.ready.any(), item["id"]
    for symbol in inputs.symbols[1:]:
        pd.testing.assert_frame_equal(scanner.conditions[symbol], base)
    dependencies = set(rule.definition["indicators"][0]["inputs"])
    dependencies |= {"turn" if name == "vendor_turn" else name
                     for name in rule.definition["indicators"][0]["extra_data"]}
    assert set(rule.requirements.fields) == dependencies | {"close"}


@pytest.mark.parametrize("indicator", ["MA", "ROLLING_VOLATILITY"])
def test_supported_window_parameter_is_actual_same_formula_on_all_boards(indicator, monkeypatch):
    item = next(row for row in CATALOG if row["id"] == indicator)
    inputs = common_three_board_inputs()
    rule = ResearchRuleStrategyV3(indicator_proposal(item, {"window": 100}), strategy_id="CUSTOM_" + indicator)
    actual = []
    original = rule.build_feature_matrix
    def record(bars, vendor_turn=None):
        value = original(bars, vendor_turn)
        actual.append(value)
        return value
    monkeypatch.setattr(rule, "build_feature_matrix", record)
    UniverseSignalScanV1(rule, inputs)
    assert rule.definition["indicators"][0]["params"]["window"] == 100
    output = "tested." + item["outputs"][0]
    expected = (actual[0].close.rolling(100).mean() if indicator == "MA"
                else actual[0].close.pct_change().rolling(100).std(ddof=1))
    pd.testing.assert_series_equal(actual[0][output], expected, check_names=False)
    for matrix in actual[1:]:
        pd.testing.assert_frame_equal(matrix, actual[0])


def test_turn_is_real_dependency_not_synthesized_from_volume_or_board():
    item = next(row for row in CATALOG if row["id"] == "TURNOVER_RATE")
    inputs = common_three_board_inputs()
    rule = ResearchRuleStrategyV3(indicator_proposal(item), strategy_id="TURN_DEPENDENCY")
    assert "turn" in rule.requirements.fields
    missing = deepcopy(inputs.bundle)
    missing["turn"] = missing["turn"].drop(columns="turn")
    with pytest.raises(ValueError, match="DATA_DEPENDENCY_NOT_MET"):
        UniverseSignalScanV1(rule, UniverseAccountInputsV1(missing, inputs.window, stage="SCAN"))
    bars = inputs.bundle["daily"].loc[inputs.bundle["daily"].symbol.eq("300001.SZ")].set_index("date")
    with pytest.raises(ValueError, match="DATA_DEPENDENCY_NOT_MET"):
        rule.build_feature_matrix(bars)


@pytest.mark.parametrize("symbol", ["000001.SZ", "600000.SH", "300001.SZ"])
def test_required_field_and_missing_warmup_remain_strict_for_every_board(symbol):
    item = next(row for row in CATALOG if row["id"] == "ATR")
    inputs = common_three_board_inputs()
    rule = ResearchRuleStrategyV3(indicator_proposal(item), strategy_id="ATR_FIELDS")
    bars = inputs.bundle["daily"].loc[inputs.bundle["daily"].symbol.eq(symbol)].set_index("date")
    with pytest.raises(ValueError, match="RULE_FEATURE_BARS_INVALID"):
        rule.build_feature_matrix(bars.drop(columns="high"))
    matrix = rule.build_feature_matrix(bars.iloc[:2])
    assert not matrix["tested." + item["outputs"][0] + "__ready"].any()


@pytest.mark.parametrize("change", ["version", "output", "params", "rank"])
def test_v3_contract_rejects_unregistered_versions_outputs_parameters_and_ranking(change):
    item = next(row for row in CATALOG if row["id"] == "RSI")
    value = indicator_proposal(item)
    if change == "version":
        value["indicator_instances"][0]["version"] = "INVENTED_VERSION"
    elif change == "output":
        value = indicator_proposal(item, outputs=["invented_output"])
    elif change == "params":
        value["indicator_instances"][0]["params"]["window"] = 100
    else:
        value["buy"]["op"] = "volatility_rank"
    with pytest.raises(ValueError, match="RULE_"):
        ResearchRuleStrategyV3(value, strategy_id="NO_EXTRA_LANGUAGE")
