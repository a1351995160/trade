"""批次委托解析适配：原领域检查不变，每次使用核验当前父授权。"""
from contextlib import contextmanager
from dataclasses import replace

from ..execution_policy import ExecutionPolicy
from .common import stable_hash
from .predictive_authorization import PredictiveGovernanceServiceV1
from .predictive_trial_start import PredictiveTrialStartError, PredictiveTrialStartServiceV1
from .mutation_boundary import ObjectiveMutationLock
from .synthetic_novelty_start import SyntheticNoveltyTrialStartServiceV1


ACTION = "START_PREDICTIVE_TRIAL_1"


class BatchPredictiveGovernanceServiceV1(PredictiveGovernanceServiceV1):
    def __init__(self, batch, identifier, execution_id):
        super().__init__(batch.root)
        self.batch, self.identifier, self.execution_id = batch, identifier, execution_id

    def _record(self, *args):
        record = super()._record(*args)
        record.update(actor="BATCH_DELEGATED", source="synthetic-batch-authorization-v1", authorization_origin="BATCH_DELEGATED",
            batch_delegation={"batch_authorization_id": self.identifier, "execution_id": self.execution_id, "action": ACTION})
        record["decision_id"] = stable_hash({"decision_id": record["decision_id"], "batch_delegation": record["batch_delegation"]})
        record["decision_hash"] = stable_hash({key: value for key, value in record.items() if key != "decision_hash"})
        return record

    def confirm(self, objective_id, body):
        if body.get("decision_type") != "AUTHORIZE_FIRST_PREDICTIVE_TRIAL" or body.get("authorization_id") != self.execution_id:
            raise PermissionError("BATCH_DELEGATION_ACTION_NOT_AUTHORIZED")
        with self.batch.admission(self.identifier, self.execution_id, action=ACTION) as (_, _, binding):
            if objective_id != binding["candidate"]["objective_id"] or body.get("candidate_id") != binding["candidate"]["candidate_id"]:
                raise PermissionError("BATCH_DELEGATION_CANDIDATE_MISMATCH")
            return super().confirm(objective_id, body)


class BatchPredictiveTrialStartServiceV1(SyntheticNoveltyTrialStartServiceV1):
    def __init__(self, batch, identifier, execution_id, member):
        self.batch, self.identifier, self.execution_id = batch, identifier, execution_id
        self.delegation = {"batch_authorization_id": identifier, "execution_id": execution_id, "action": ACTION}
        self._settlement_read = False
        super().__init__(batch.root, batch.policy, member["novelty_confirmation"], auto_run=False)

    def _validate_authorization_record(self, record):
        if (record.get("authorization_origin") != "BATCH_DELEGATED" or record.get("batch_delegation") != self.delegation
                or record.get("decision_hash") != stable_hash({k: v for k, v in record.items() if k != "decision_hash"})):
            raise PermissionError("BATCH_DELEGATED_AUTHORIZATION_INVALID")
        try:
            if self._settlement_read:
                binding = self.batch.historical_execution(self.identifier, self.execution_id)[1]
            else:
                with self.batch.admission(self.identifier, self.execution_id, action=ACTION) as (_, _, binding):
                    pass
            if record.get("candidate_id") != binding["candidate"]["candidate_id"] or record.get("candidate_hash") != binding["candidate_hash"]:
                raise PermissionError("BATCH_DELEGATION_CANDIDATE_MISMATCH")
        except (PermissionError, ValueError) as exc:
            raise PredictiveTrialStartError("BATCH_PARENT_AUTHORIZATION_INVALID", str(exc)) from exc

    def _reconcile_runner_failure(self, *args, **kwargs):
        # 仅原失败结算读取历史委托；不能复用此模式 preview/confirm 或启动 runner。
        self._settlement_read = True
        try:
            return super()._reconcile_runner_failure(*args, **kwargs)
        finally:
            self._settlement_read = False

    def settle_after_exit(self, objective_id):
        with ObjectiveMutationLock(self.root, "objective:" + objective_id):
            self.batch.historical_execution(self.identifier, self.execution_id)
            intents = self._load_intents(objective_id)
            intent = intents.get(self.execution_id)
            if intent is None:
                return {"status": "NO_START_INTENT", "completed": False}
            existing = self._trial_records(objective_id).get(str(intent["trial_id"]))
            if existing and existing.get("status") == "COMPLETED" and intent.get("stage") == "TRIAL_COMPLETED":
                return {"status": "TRIAL_COMPLETED", "completed": True}
            self._reconcile_runner_failure(objective_id, intent,
                error_code="SYNTHETIC_BATCH_WORKER_EXIT", error_message="受限 worker 已退出；只按原协议结算，不自动重跑。")
            record = self._trial_records(objective_id).get(str(intent["trial_id"]))
            if record is None:
                return {"status": "SETTLEMENT_BLOCKED", "completed": False}
            updated = self._sync_terminal_intent(objective_id, intents, intent, record)
            return {"status": updated["stage"], "completed": updated["stage"] == "TRIAL_COMPLETED"}

    def _preview_plan(self, snapshot):
        return {**super()._preview_plan(snapshot), "batch_delegation": self.delegation, "authorization_origin": "BATCH_DELEGATED"}

    def _put_intent(self, objective_id, intents, intent):
        return super()._put_intent(objective_id, intents, {**intent, "batch_delegation": self.delegation,
            "authorization_origin": "BATCH_DELEGATED"})

    def _load_intents(self, objective_id):
        intents = super()._load_intents(objective_id)
        if any(item.get("batch_delegation") != self.delegation or item.get("authorization_origin") != "BATCH_DELEGATED" for item in intents.values()):
            raise PermissionError("BATCH_START_INTENT_BINDING_CONFLICT")
        return intents

    def _candidate_work(self, snapshot, intent):
        candidate = super()._candidate_work(snapshot, intent)
        return replace(candidate, metadata={**candidate.metadata, "batch_delegation": self.delegation})

    def confirm(self, objective_id, body):
        with self.batch.admission(self.identifier, self.execution_id, action=ACTION):
            return super().confirm(objective_id, body)

    def confirm_resume(self, objective_id, body):
        raise PermissionError("BATCH_RETRY_NOT_AUTHORIZED")

    def recover(self, *args, **kwargs):
        raise PermissionError("BATCH_USE_SETTLEMENT_ONLY_RECOVERY")


@contextmanager
def batch_performance_boundary(root, objective_id, candidate):
    delegation = candidate.metadata.get("batch_delegation")
    if delegation is None:
        intents = PredictiveTrialStartServiceV1(root, auto_run=False)._load_intents(objective_id)
        if any(item.get("candidate_id") == candidate.candidate_id and item.get("authorization_origin") == "BATCH_DELEGATED"
                for item in intents.values()):
            raise PermissionError("BATCH_CANONICAL_DELEGATION_METADATA_REQUIRED")
        yield
        return
    from .synthetic_batch import SyntheticBatchServiceV1
    batch = SyntheticBatchServiceV1(root, ExecutionPolicy("GOVERNED", "SYNTHETIC"))
    with batch.admission(delegation["batch_authorization_id"], delegation["execution_id"], action=ACTION) as (_, _, binding):
        if binding["candidate"]["objective_id"] != objective_id or binding["candidate"]["candidate_id"] != candidate.candidate_id:
            raise PermissionError("BATCH_PERFORMANCE_CANDIDATE_MISMATCH")
        # 当前实际启动意图必须携带相同父批准与委托；普通 metadata 不能创造许可。
        service = BatchPredictiveTrialStartServiceV1(batch, delegation["batch_authorization_id"], delegation["execution_id"], binding["candidate"])
        intent = service._load_intents(objective_id).get(candidate.metadata.get("start_intent_id"))
        if intent is None or intent.get("trial_id") != candidate.metadata.get("trial_id"):
            raise PermissionError("BATCH_CANONICAL_START_INTENT_REQUIRED")
        yield
