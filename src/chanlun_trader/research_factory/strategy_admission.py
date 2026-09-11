"""读取既有策略注册事实；研究状态不提供计划或Paper使用资格。"""
from pathlib import Path
from hashlib import sha256

from ..execution_policy import ExecutionPolicy, validate_research_root
from .predictive_executor import CanonicalPredictiveExecutorV1
from .strategy_adapter import ResearchStrategyRegistryFacadeV1


def inspect_strategy_admission(source):
    root = Path(source["root"])
    validate_research_root(root, ExecutionPolicy("READ_ONLY", "SYNTHETIC"))
    contract = source["contract"]
    caller = CanonicalPredictiveExecutorV1(root, contract.policy_identity["objective_id"])
    result = {"candidate_id": contract.candidate_id, "contract_hash": contract.content_hash,
        "research_state": "NOT_REGISTERED", "promotion_state": "DISABLED",
        "usage_qualified": False, "reason": "WAITING_USAGE_AUTHORIZATION",
        "preview_blocked": False, "registry_ref": None, "registry_identity": None}
    try:
        path = caller._canonical_paths(caller._budget_path())["strategy_registry"]
    except FileNotFoundError:
        result["reason"] = "CANONICAL_REGISTRY_BINDING_MISSING"
        return result
    result["registry_ref"] = path.relative_to(root).as_posix()
    if not path.exists():
        result["reason"] = "STRATEGY_NOT_REGISTERED"
        return result
    registry = ResearchStrategyRegistryFacadeV1(path=path)
    result["registry_identity"] = sha256(path.read_bytes()).hexdigest()
    record = next((item for item in registry.records() if item.candidate_id == contract.candidate_id), None)
    if record is None:
        result["reason"] = "STRATEGY_NOT_REGISTERED"
        return result
    result.update(research_state=record.research_state, promotion_state=record.promotion_state)
    if record.candidate_hash != contract.candidate_hash or record.durable_contract_hash != contract.content_hash:
        result.update(reason="REGISTRY_CONTRACT_MISMATCH", preview_blocked=True)
    elif record.research_state in {"RETIRED", "INVALIDATED"} or record.invalidated_evidence:
        result.update(reason="STRATEGY_RETIRED_OR_INVALIDATED", preview_blocked=True)
    return result
