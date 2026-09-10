"""从已声明比较来源读取可选候选和批次历史，不发现其他研究目录。"""
from .common import stable_hash
from .budget import SearchBudgetRegistryV1
from .synthetic_batch import SyntheticBatchRequestV1
from .synthetic_novelty import SyntheticNoveltyBindingServiceV1


def batch_console_context(service):
    result = {"schema_version": "synthetic-batch-console-v1", "workspace": str(service.root),
        "actions_allowed": service.policy.governance_allowed, "candidates": [], "batches": [], "issues": [],
        "request_schema": SyntheticBatchRequestV1.model_json_schema(), "real_data_status": "NOT_VERIFIED",
        "ready_for_real_trial": False, "real_observation_days": 0}
    if service.directory.exists():
        for directory in sorted(service.directory.iterdir()):
            if not directory.is_dir():
                continue
            try:
                view = service.view(directory.name)
                result["batches"].append({"batch_authorization_id": directory.name, "status": view["status"],
                    "candidate_count": len(view["preview"]["request"]["candidates"]),
                    "completed_actions": len((view["state"] or {}).get("completed_actions", [])),
                    "expires_at": view["preview"]["request"]["expires_at"]})
            except (ValueError, OSError) as exc:
                result["batches"].append({"batch_authorization_id": directory.name, "status": "BLOCKED", "reason": str(exc)})
    if not result["actions_allowed"]:
        return result
    try:
        novelty = SyntheticNoveltyBindingServiceV1(service.root)
        scope = novelty._scope()
        result["comparison_sources"] = scope["sources"]
        contracts = {}
        for reference in scope["sources"]:
            _, members = novelty._registry(reference)
            for contract in members:
                key = (contract.candidate_id, contract.content_hash)
                entry = contracts.setdefault(key, {"contract": contract, "sources": []})
                entry["sources"].append(reference)
        for entry in contracts.values():
            contract = entry["contract"]
            member = {"objective_id": contract.policy_identity.get("objective_id"),
                "candidate_id": contract.candidate_id, "contract_hash": contract.content_hash}
            option = {"member": member, "sources": entry["sources"], "status": "BLOCKED"}
            try:
                # 当前完整快照的确定性 ID；不能退回较旧的成功确认。
                member["novelty_confirmation"] = stable_hash(novelty.snapshot(contract.candidate_id, contract.content_hash))
                binding = service._binding(member)
                budget = SearchBudgetRegistryV1(member["objective_id"], service.root / binding["budget_ref"]).snapshot()
                if not budget["buckets"] or any(row["remaining"] < 1 for row in budget["buckets"]):
                    raise ValueError("BATCH_CANONICAL_BUDGET_EXHAUSTED")
                option.update(status="READY", research_period=binding["research_period_identity"],
                    policy=binding["policy_identity"], budget_limits=binding["budget_limits"])
            except (ValueError, OSError, KeyError) as exc:
                option["reason"] = str(exc)
            result["candidates"].append(option)
    except (ValueError, OSError) as exc:
        result["issues"].append(str(exc))
    return result
