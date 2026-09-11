"""先安装 OS 资源上限，再导入正式领域服务的合成批次 worker。"""
from .synthetic_batch_resources import worker_resource_handshake


def main():
    config = worker_resource_handshake()
    execute(config["execution"])


def execute(context):
    # 身份通过数据通道传入，不能成为解释器或进程启动选项。
    if (not isinstance(context, dict) or set(context) != {"root", "batch_authorization_id", "execution_id"}
            or any(type(value) is not str or not value for value in context.values())):
        raise ValueError("BATCH_WORKER_CONTEXT_INVALID")
    from .execution_policy import ExecutionPolicy
    from .research_factory.paper_replay import _immutable
    from .research_factory.synthetic_batch import SyntheticBatchServiceV1
    from .research_factory.structural_entry import StructuralEntryServiceV1
    from .research_factory.synthetic_batch_delegation import BatchPredictiveGovernanceServiceV1, BatchPredictiveTrialStartServiceV1

    root, identifier, execution_id = context["root"], context["batch_authorization_id"], context["execution_id"]
    batch = SyntheticBatchServiceV1(root, ExecutionPolicy("GOVERNED", "SYNTHETIC"))
    batch.register_worker(identifier, execution_id)
    active = batch.inspect(identifier)["state"]["active_execution"]
    action = active["action"]
    binding = batch.begin_action(identifier, execution_id, action)
    member = binding["candidate"]
    objective_id = member["objective_id"]
    if action == "RUN_STRUCTURAL_PREFLIGHT":
        result = StructuralEntryServiceV1(root).start(objective_id, candidate_id=member["candidate_id"], confirmed=True)
        completed = result["status"] == "PASS"
        status = result["status"]
    else:
        governance = BatchPredictiveGovernanceServiceV1(batch, identifier, execution_id)
        choice = "AUTHORIZE_FIRST_PREDICTIVE_TRIAL"
        preview = governance.preview(objective_id, choice)
        governance.confirm(objective_id, {"confirmed": True, "decision_type": choice,
            "candidate_id": member["candidate_id"], "candidate_hash": binding["candidate_hash"],
            "authorization_id": execution_id, "preview_hash": preview["preview_hash"],
            "confirmation_token": preview["confirmation_token"]})
        start = BatchPredictiveTrialStartServiceV1(batch, identifier, execution_id, member)
        preview = start.preview(objective_id)
        start.confirm(objective_id, {"confirmed": True, "action": action, "start_intent_id": execution_id,
            "candidate_id": member["candidate_id"], "candidate_hash": binding["candidate_hash"],
            "preview_hash": preview["preview_hash"], "confirmation_token": preview["confirmation_token"]})
        start._run_intent(objective_id, execution_id)
        result = start._load_intents(objective_id)[execution_id]
        completed = result["stage"] == "TRIAL_COMPLETED"
        status = result["stage"]
    outcome = batch._record({"execution_id": execution_id, "authorization_origin": "BATCH_DELEGATED",
        "batch_authorization_id": identifier, "action": action, "completed": completed, "status": status})
    _immutable(batch._directory(identifier) / "executions" / execution_id / "outcome.json", outcome)
    print("SYNTHETIC_BATCH_ACTION=" + status, flush=True)


if __name__ == "__main__":
    main()
