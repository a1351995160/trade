"""新进程查询和重放完成记录；不从完成回执创建新执行许可。"""
import json
from pathlib import Path
import sys

from chanlun_trader.execution_policy import ExecutionPolicy
from chanlun_trader.research_factory.synthetic_novelty_start import SyntheticNoveltyTrialStartServiceV1


root = Path(sys.argv[1])
creation = json.loads((root / "r2-formal-objective-creation-evidence.json").read_bytes())["receipt"]
objective_id = creation["objective_id"]
request = json.loads((root / "r2-test-start-request.json").read_bytes())
intents = json.loads((root / f"reports/research_daemon/{objective_id}/predictive_trial_start_intents.json").read_bytes())
intent = intents["intents"][request["start_intent_id"]]
service = SyntheticNoveltyTrialStartServiceV1(root, ExecutionPolicy("GOVERNED", "SYNTHETIC"), intent["synthetic_novelty_confirmation"], auto_run=False)
calls = {"engine": 0, "performance": 0}
def observe(frame, event, arg):
    if event != "call":
        return
    name = (frame.f_globals.get("__name__"), frame.f_code.co_name)
    if name == ("chanlun_trader.engine.engine", "run"):
        calls["engine"] += 1
    if name == ("chanlun_trader.research_factory.trial_adapter", "mark_performance_accessed"):
        calls["performance"] += 1
sys.setprofile(observe)
try:
    if len(sys.argv) > 2 and sys.argv[2] == "interrupted":
        service.recover(objective_id)
        service._run_intent(objective_id, request["start_intent_id"])
        failed = service._load_intents(objective_id)[request["start_intent_id"]]
        assert failed["stage"] == "TRIAL_FAILED", failed
        preview = service.resume_preview(objective_id)
        service.confirm_resume(objective_id, {**request, "action": "RESUME_PREDICTIVE_TRIAL_1",
            "trial_id": intent["trial_id"], "preview_hash": preview["preview_hash"],
            "confirmation_token": preview["confirmation_token"]})
        service._run_intent(objective_id, request["start_intent_id"])
        final = service._load_intents(objective_id)[request["start_intent_id"]]
        assert final["stage"] == "TRIAL_COMPLETED", final
        budget = json.loads((root / creation["budget_registry_ref"]).read_bytes())
        assert all(item["used"] == 1 and item["reserved"] == 0 for item in budget["buckets"])
        print(json.dumps({"recovery_completed": True, "same_trial": final["trial_id"] == intent["trial_id"], "calls": calls}), flush=True)
        sys.exit(0)
    before = {path: path.read_bytes() for path in (root / creation["budget_registry_ref"],
        (root / creation["budget_registry_ref"]).with_name("factory_trial_ledger.json"))}
    receipt = service.confirm(objective_id, request)
    recovered = service.recover(objective_id)
    assert receipt["idempotent"] is True
    assert calls == {"engine": 0, "performance": 0}
    assert all(path.read_bytes() == data for path, data in before.items())
    print(json.dumps({"replay_idempotent": True, "canonical_files_unchanged": True, "calls": calls}), flush=True)
finally:
    sys.setprofile(None)
