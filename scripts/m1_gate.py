"""M1 FACTOR / EVENT PLATFORM GATE。"""
import sys, json
from pathlib import Path
sys.path.insert(0, "src")
from chanlun_trader.research.factor import FactorRegistry, FactorStore, FactorLineage, FactorDefinition
from chanlun_trader.research.event import EventRegistry, EventStore, EventDefinition
from chanlun_trader.research.label import LabelStore
from chanlun_trader.research.screener import ScreenerRule, DynamicGroup

import pandas as pd

def main():
    checks = {}
    # FactorRegistry
    fr = FactorRegistry.load(Path("data/research/factor_registry/registry.json"))
    checks["factor_registry_items"] = len(fr.items())
    fs = FactorStore(Path("data/research/factor_store"))
    checks["factor_store_files"] = len(list(fs.root.glob("*.parquet"))) if fs.root.exists() else 0
    # EventRegistry
    er = EventRegistry.load(Path("data/research/event_registry/registry.json"))
    checks["event_registry_items"] = len(er.items())
    es = EventStore(Path("data/research/event_store"))
    checks["event_store_files"] = len(list(es.root.glob("*.parquet"))) if es.root.exists() else 0
    # LabelStore
    ls = LabelStore(Path("data/research/label_store"))
    checks["label_files"] = len(list(ls.root.glob("*.parquet"))) if ls.root.exists() else 0
    # DynamicGroup demo
    rule = ScreenerRule(rule_id="R1", name="demo", description="")
    dg = DynamicGroup("G1", rule)
    ev = dg.events_for_day(["A"], pd.Series({"A": True}))
    checks["dynamic_group_enter"] = ev.iloc[0]["event_type"]
    m1_pass = checks["factor_store_files"] >= 1 and checks["label_files"] >= 1
    checks["M1_PASS"] = m1_pass
    out = Path("reports/M1_GATE.json")
    out.write_text(json.dumps(checks, indent=2), encoding="utf-8")
    print(json.dumps(checks, indent=2))
    print("M1_PASS" if m1_pass else "M1_FAIL")

if __name__ == "__main__":
    main()
