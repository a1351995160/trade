"""使用实际正式新意图检查跨入口协议，不替代权限、比较集或执行器。"""
from dataclasses import replace
import json
from pathlib import Path
import sys

from chanlun_trader.execution_policy import ExecutionPolicy
from chanlun_trader.research_factory.predictive_trial_start import PredictiveTrialStartServiceV1
from chanlun_trader.research_factory.synthetic_novelty_start import SyntheticNoveltyTrialStartServiceV1
from chanlun_trader.research_factory.synthetic_novelty import canonical_novelty_boundary
from chanlun_trader.research_factory.predictive_executor import CanonicalPredictiveExecutorV1

root = Path(sys.argv[1])
creation = json.loads((root / "r2-formal-objective-creation-evidence.json").read_bytes())["receipt"]
objective = creation["objective_id"]
request = json.loads((root / "r2-test-start-request.json").read_bytes())
intent_path = root / f"reports/research_daemon/{objective}/predictive_trial_start_intents.json"
intent = json.loads(intent_path.read_bytes())["intents"][request["start_intent_id"]]
legacy = PredictiveTrialStartServiceV1(root, auto_run=False)
if len(sys.argv) > 2 and sys.argv[2] == "legacy":
    assert "synthetic_flow_version" not in intent and "synthetic_novelty_confirmation" not in intent
    budget_path = root / creation["budget_registry_ref"]
    before = budget_path.read_bytes()
    snapshot = legacy._snapshot(objective, allow_inflight=True)
    candidate = legacy._candidate_work(snapshot, intent)
    with canonical_novelty_boundary(root, objective, candidate, snapshot.candidate) as evidence:
        assert evidence == {"passed": True}
    receipt = legacy.confirm(objective, request)
    restored = legacy.recover(objective)
    assert receipt["idempotent"] and restored["status"] == "RECOVERED"
    assert budget_path.read_bytes() == before
    print(json.dumps({"legacy_compatible": True, "budget_unchanged": True}))
    sys.exit(0)
modern = SyntheticNoveltyTrialStartServiceV1(root, ExecutionPolicy("GOVERNED", "SYNTHETIC"), intent["synthetic_novelty_confirmation"], auto_run=False)
snapshot = modern._snapshot(objective, allow_inflight=True)
candidate = modern._candidate_work(snapshot, intent)
counts = {"engine": 0, "performance": 0}
def observe(frame, event, arg):
    if event == "call":
        pair = (frame.f_globals.get("__name__"), frame.f_code.co_name)
        if pair == ("chanlun_trader.engine.engine", "run"):
            counts["engine"] += 1
        if pair == ("chanlun_trader.research_factory.trial_adapter", "mark_performance_accessed"):
            counts["performance"] += 1
results = {}
def check(name, operation):
    try:
        results[name] = {"blocked": False, "result": operation()}
    except (ValueError, RuntimeError, PermissionError) as exc:
        results[name] = {"blocked": True, "error": str(exc)}
def boundary(value):
    with canonical_novelty_boundary(root, objective, value, snapshot.candidate) as evidence:
        return evidence
sys.setprofile(observe)
try:
    check("proper_new_boundary", lambda: boundary(candidate))
    for keys in (("synthetic_flow_version",), ("synthetic_novelty_confirmation",),
                 ("synthetic_flow_version", "synthetic_novelty_confirmation"),
                 ("synthetic_flow_version", "synthetic_novelty_confirmation", "start_intent_id")):
        stripped = replace(candidate, metadata={key: value for key, value in candidate.metadata.items() if key not in keys})
        check("missing_" + "+".join(keys), lambda: boundary(stripped))
    check("legacy_candidate_boundary", lambda: boundary(legacy._candidate_work(snapshot, intent)))
    source = root / snapshot.contract_ref
    original = source.read_bytes()
    source.write_bytes(original + b"\n")
    check("stale_new_boundary", lambda: boundary(candidate))
    check("stale_legacy_boundary", lambda: boundary(legacy._candidate_work(snapshot, intent)))
    # 保留变化的来源；不为了旧入口测试恢复它或补写确认。
    check("legacy_confirm", lambda: legacy.confirm(objective, request))
    check("legacy_recover", lambda: legacy.recover(objective))
    stripped = replace(candidate, metadata={key: value for key, value in candidate.metadata.items()
        if key not in ("synthetic_flow_version", "synthetic_novelty_confirmation")})
    check("actual_executor_missing_protocol", lambda: CanonicalPredictiveExecutorV1(root, objective).execute(stripped))
finally:
    sys.setprofile(None)
budget = json.loads((root / creation["budget_registry_ref"]).read_bytes())
print(json.dumps({"results": results, "counts": counts,
    "budget": [{"used": item["used"], "reserved": item["reserved"]} for item in budget["buckets"]]}, default=str))
