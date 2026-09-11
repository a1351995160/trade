"""真实预算与Trial facade生成预登记，再核验缺失、错绑、消费及损坏。"""
import json

import pytest

from chanlun_trader.research_factory.budget import SearchBudgetRegistryV1
from chanlun_trader.research_factory.trial_adapter import ResearchFactoryTrialLedgerFacadeV1
from chanlun_trader.research_factory.execution_evidence import audit_budget_registration


def setup(root, *, register=True):
    budget = SearchBudgetRegistryV1("OBJECTIVE", root / "budget.json")
    budget.register_objective(2)
    budget.register_batch("BATCH", 2)
    budget.register_family("FAMILY", 2)
    reservation = budget.reserve_trial(batch_id="BATCH", family_id="FAMILY", candidate_id="CANDIDATE")
    ledger = ResearchFactoryTrialLedgerFacadeV1(path=root / "ledger.json")
    if register:
        ledger.register_before_performance(trial_id="TRIAL", objective_id="OBJECTIVE", batch_id="BATCH",
            family_id="FAMILY", hypothesis_id="HYPOTHESIS", candidate_id="CANDIDATE", candidate_hash="HASH",
            dataset_hash="DATA", validation_policy_hash="POLICY", engine_hash="ENGINE", seed=1,
            budget_reservation_identity=reservation)
    return ledger, budget, dict(trial_id="TRIAL", reservation_id=reservation, candidate_id="CANDIDATE", candidate_hash="HASH")


def test_actual_preperformance_registration_is_read_only_and_recovery_requires_consumption(tmp_path):
    ledger, budget, identity = setup(tmp_path)
    before = {path: path.read_bytes() for path in tmp_path.iterdir()}
    evidence = audit_budget_registration(ledger, budget, **identity)
    assert evidence["passed"] and evidence["registration_event_hash"]
    assert not audit_budget_registration(ledger, budget, **identity, recovery=True)["passed"]
    assert before == {path: path.read_bytes() for path in tmp_path.iterdir()}
    ledger.mark_performance_accessed("TRIAL")
    budget.consume(identity["reservation_id"])
    assert audit_budget_registration(ledger, budget, **identity, recovery=True)["passed"]
    assert not audit_budget_registration(ledger, budget, **identity)["passed"]


@pytest.mark.parametrize("defect", ["missing", "candidate", "reservation", "released", "damaged", "wrong_batch"])
def test_unverified_registration_never_passes(tmp_path, defect):
    ledger, budget, identity = setup(tmp_path, register=defect != "missing")
    if defect == "candidate":
        identity["candidate_hash"] = "OTHER"
    elif defect == "reservation":
        identity["reservation_id"] = "UNKNOWN"
    elif defect == "released":
        budget.release(identity["reservation_id"])
    elif defect == "damaged":
        path = tmp_path / "ledger.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["events"][0]["performance_accessed"] = True
        path.write_text(json.dumps(value), encoding="utf-8")
        ledger = ResearchFactoryTrialLedgerFacadeV1(path=path)
    elif defect == "wrong_batch":
        from chanlun_trader.research_factory.common import stable_hash
        path = tmp_path / "ledger.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        event = value["events"][0]
        event["batch_id"] = "OTHER_BATCH"
        event["event_hash"] = stable_hash({key: item for key, item in event.items() if key != "event_hash"})
        path.write_text(json.dumps(value), encoding="utf-8")
        ledger = ResearchFactoryTrialLedgerFacadeV1(path=path)
    assert not audit_budget_registration(ledger, budget, **identity)["passed"]
