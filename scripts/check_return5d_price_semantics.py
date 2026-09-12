"""已冻结RETURN_5D定义的版本化解释和合成公式校验，不改变caller许可。"""
from dataclasses import replace
import json
from pathlib import Path

import numpy as np
import pandas as pd

from chanlun_trader.research.unified_factor import AsOfDataView, FactorCompiler, UnifiedFactorRegistry


def check(definition):
    expected = {"op": "pct_change", "args": [{"op": "field", "field": "close"}], "window": 5, "min_periods": 6}
    if (definition.factor_id != "RETURN_5D" or definition.feature_price_mode != "RETURN_ONLY"
            or definition.operator_graph != expected or definition.required_fields != ["close"]
            or definition.required_frequency != "DAILY" or definition.available_at_rule != "T_CLOSE"):
        raise ValueError("EXACT_RETURN5D_DEFINITION_REQUIRED")
    dates = [20220720, 20220721, 20220722, 20220725, 20220726, 20220727, 20220728, 20220729]
    frame = pd.DataFrame([{"date": day, "symbol": symbol, "close": value}
        for symbol, prices in [("600000.SH", [10,11,12,13,14,15,16,17]),
                               ("000001.SZ", [20,20,20,20,20,10,10,10])]
        for day, value in zip(dates, prices)])
    view = AsOfDataView(frame, "SYNTHETIC_FORMULA_ONLY", "SYNTHETIC", as_of=dates[-1])
    executor = FactorCompiler()
    original = executor.execute(definition, view)
    # 只在内存合成对照更换解释标签，绝不写registry或正式合同。
    control = executor.execute(replace(definition, feature_price_mode="RAW"), view)
    pd.testing.assert_frame_equal(original, control)
    panel = view.panel()
    reference = panel.close / panel.groupby(level="symbol").close.shift(5) - 1
    np.testing.assert_allclose(original.value, reference, equal_nan=True)
    return {"schema_version": "return5d-price-semantics-interpretation-v2",
        "definition_unchanged": True, "original_feature_price_mode": "RETURN_ONLY",
        "compiler_reads": "close without adjustment", "output_semantics": "dimensionless raw-close ratio minus one",
        "execution_price_semantics": "separate RAW bar contract; not established by factor label",
        "synthetic_formula_equivalence": True, "synthetic_split_example_value": -0.5,
        "corporate_action_invariance": False, "generated_available_at": "integer date key; not observed historical timestamp",
        "caller_compatibility": "CONDITIONAL_SEMANTIC_COMPATIBILITY_ONLY",
        "production_adapter_activated": False, "ready_for_real_trial": False,
        "required_before_activation": ["explicit raw input binding", "original definition hash",
            "historical availability evidence or approved assumption", "company-action contract", "programme confirmation"]}


if __name__ == "__main__":
    registry = UnifiedFactorRegistry.read(Path("E:/llmwiki/chanlun-trading-system/data/research/factor_library_v1/registry.json"))
    print(json.dumps(check(registry.get("RETURN_5D")), ensure_ascii=False, indent=2))
