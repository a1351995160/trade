"""通过真实registry服务登记和退役；只读投影不制造策略使用资格。"""
import pytest

from test_engineering_workbench import workbench, GOVERNED
from test_daily_plan import timestamp
from chanlun_trader.research_factory.predictive_executor import CanonicalPredictiveExecutorV1
from chanlun_trader.research_factory.strategy_adapter import ResearchStrategyRegistryFacadeV1
from chanlun_trader.research_factory.strategy_admission import inspect_strategy_admission


def register(service, *, mismatch=False):
    source = next(iter(service.sources.values()))
    contract = source["contract"]
    caller = CanonicalPredictiveExecutorV1(source["root"], contract.policy_identity["objective_id"])
    path = caller._canonical_paths(caller._budget_path())["strategy_registry"]
    registry = ResearchStrategyRegistryFacadeV1(path=path)
    registry.register(contract.candidate_id, contract.candidate_hash, source["record"].candidate.mechanism,
        durable_contract_hash="WRONG" if mismatch else contract.content_hash)
    return source, registry


def test_readonly_registry_projection_never_turns_research_state_into_permission(tmp_path):
    service = workbench(tmp_path)
    source = next(iter(service.sources.values()))
    assert inspect_strategy_admission(source)["research_state"] == "NOT_REGISTERED"
    source, registry = register(service)
    before = registry.path.read_bytes()
    result = inspect_strategy_admission(source)
    assert result["research_state"] == "DRAFT"
    assert result["usage_qualified"] is False and result["reason"] == "WAITING_USAGE_AUTHORIZATION"
    assert result == inspect_strategy_admission(source)
    assert registry.path.read_bytes() == before
    assert not service.output_root.exists()


@pytest.mark.parametrize("defect", ["retired", "invalidated", "mismatch"])
def test_registry_changes_invalidate_context_plan_and_block_further_replay(tmp_path, defect):
    service = workbench(tmp_path)
    source, registry = register(service, mismatch=defect == "mismatch")
    candidate = source["contract"].candidate_id
    day = timestamp(source["inputs"]["exec_calendar"][0])
    before = service.inspect()["context_hash"]
    if defect != "mismatch":
        plan = service.preview(day)
        if defect == "retired":
            registry.transition(candidate, "VALIDATION_BLOCKED", evidence_ref="synthetic-test-blocked")
        registry.transition(candidate, "RETIRED" if defect == "retired" else "INVALIDATED", evidence_ref="synthetic-test-retirement")
        assert service.inspect()["context_hash"] != before
        assert service.preview(day)["plan_id"] != plan["plan_id"]
    current = inspect_strategy_admission(source)
    assert current["preview_blocked"] and current["usage_qualified"] is False
    assert service.preview(day)["source_plans"] == []
    with pytest.raises(ValueError, match="WORKBENCH_STRATEGY_RETIRED_INVALIDATED_OR_CHANGED"):
        service.advance(GOVERNED, {"confirmed": True, "context_hash": service.inspect()["context_hash"],
            "candidate_id": candidate, "event_count": 1})
    assert not list(service.output_root.rglob("header.json"))
