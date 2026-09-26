import pandas as pd
import pytest

from chanlun_trader.research_factory.bounded_candidate_v1 import (
    candidate_capabilities, validate_candidate,
)
from chanlun_trader.research_factory.strategy_interface_v1 import Context, backend_for, prepare, run
from bounded_research_fixture import synthetic_bundle


def payload(ids=None, threshold=1):
    return {"hypothesis": "趋势延续", "indicators": ids or ["MACD"],
            "threshold": threshold, "change_reason": "首次提出"}


def test_rules_are_canonical_and_only_supported_fields_are_accepted():
    ids = [item["id"] for item in candidate_capabilities()["indicators"]][:2]
    first = validate_candidate(payload(ids), strategy_id="first")
    second = validate_candidate({**payload(ids[::-1]), "hypothesis": "另一种解释"}, strategy_id="second")
    assert first.rule_identity == second.rule_identity
    assert prepare(first, backend_for(first, {"events": ()}))["plan_id"] != prepare(
        second, backend_for(second, {"events": ()}))["plan_id"]
    for invalid in ({**payload(ids), "code": "print(1)"}, payload(["UNKNOWN"]),
                    payload([ids[0], ids[0]]), payload(ids, True), payload(ids, 0), payload(ids, 3)):
        with pytest.raises(ValueError):
            validate_candidate(invalid, strategy_id="candidate")
    first.parameters["target_weight_per_symbol"] = 1
    with pytest.raises(ValueError, match="RULE_CHANGED"):
        prepare(first, backend_for(first, {"events": ()}))


def test_selected_indicators_and_threshold_change_decisions():
    ids = [item["id"] for item in candidate_capabilities()["indicators"]][:2]
    history = pd.DataFrame({f"{ids[0]}__value": [1., 2.], f"{ids[1]}__value": [2., 1.],
                            f"{ids[0]}__ready": [True, True], f"{ids[1]}__ready": [True, True]},
                           index=[20240102, 20240103])
    context = Context(history, (20240102, 20240103), 1, {}, {})
    one = validate_candidate(payload(ids, 1), strategy_id="one")
    two = validate_candidate(payload(ids, 2), strategy_id="two")
    assert one.on_close(context).reason == "BUY"
    assert two.on_close(context).reason == "SELL"
    assert one.rule_identity != two.rule_identity
    with pytest.raises(ValueError, match="PREFIX_ONLY"):
        one.on_close(Context(history, (20240101, 20240103), 1, {}, {}))


def test_public_entry_preserves_direct_account_and_binds_candidate_identity():
    results = []
    for threshold in (1, 2):
        strategy = validate_candidate(payload(["MACD", "RSI"], threshold), strategy_id=f"candidate_{threshold}")
        bundle = synthetic_bundle(strategy)
        backend = backend_for(strategy, {"events": ()})
        plan = prepare(strategy, backend)
        receipt = {"strategy_plans": {strategy.strategy_id: plan},
                   "input_identity": bundle["input_identity"],
                   "novelty": {strategy.strategy_id: {"allowed": True}},
                   "execution_purpose": strategy.strategy_id, "execution_consumed": True}
        actual = run(strategy, backend, frame=bundle, actions=(), input_identity=bundle["input_identity"],
                     active_check=lambda: receipt)
        direct = backend.run(strategy, bundle, (), lambda: None)
        for key in ("decisions", "submitted_decisions", "chain", "fills", "ledger"):
            assert actual[key] == direct[key]
        assert actual["strategy_plan"]["strategy"]["strategy_id"] == strategy.strategy_id
        assert actual["strategy_plan"]["strategy"]["parameters"]["rule_identity"] == strategy.rule_identity
        results.append(actual)
        bundle["daily"].loc[0, "close"] += 1
        with pytest.raises(PermissionError, match="INPUT_CONTENT_CHANGED"):
            run(strategy, backend, frame=bundle, actions=(), input_identity=bundle["input_identity"],
                active_check=lambda: receipt)
    assert results[0]["decisions"] != results[1]["decisions"]
