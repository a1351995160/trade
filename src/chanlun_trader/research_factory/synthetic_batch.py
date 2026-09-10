"""明确候选清单的 synthetic 批次批准；额度仍由原预算和 TrialLedger 决定。"""
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import uuid

from pydantic import AwareDatetime, Field, StrictInt, StrictStr

from ..execution_policy import ExecutionPolicy, validate_research_root
from ..synthetic_batch_resources import run_bounded_worker
from .batch_scope_request import RequestModel, BatchResourceLimitsV1
from .budget import SearchBudgetRegistryV1
from .candidate_executable_materialization import inspect_materialization_confirmation, inspect_materialization_preview
from .common import stable_hash
from .durability import _atomic_write
from .mutation_boundary import ObjectiveMutationLock
from .objective_execution_binding import ResearchProposalGovernanceServiceV2
from .paper_replay import _immutable
from .structural_entry import _candidate_from_contract_path
from .synthetic_novelty import SyntheticNoveltyBindingServiceV1


SCHEMA = "synthetic-batch-authorization-v1"
ACTIONS = ("RUN_STRUCTURAL_PREFLIGHT", "START_PREDICTIVE_TRIAL_1")


class SyntheticBatchLimitsV1(BatchResourceLimitsV1):
    concurrency: StrictInt = Field(ge=1, le=1)
    retries: StrictInt = Field(ge=0, le=0)


class BatchCandidateV1(RequestModel):
    objective_id: StrictStr
    candidate_id: StrictStr
    contract_hash: StrictStr
    novelty_confirmation: StrictStr


class SyntheticBatchRequestV1(RequestModel):
    schema_version: StrictStr
    candidates: list[BatchCandidateV1] = Field(min_length=1)
    actions: list[StrictStr] = Field(min_length=1)
    model: StrictStr
    limits: SyntheticBatchLimitsV1
    effective_at: AwareDatetime
    expires_at: AwareDatetime


class SyntheticBatchServiceV1:
    def __init__(self, root, policy, *, clock=None):
        self.root = Path(root)
        self.policy = policy
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        if os.environ.get("CHANLUN_TEST_ISOLATION") != "1" or policy.workspace_kind != "SYNTHETIC":
            raise PermissionError("BATCH_SYNTHETIC_GOVERNANCE_REQUIRED")
        validate_research_root(self.root, policy)
        self.root = self.root.resolve()
        self.directory = self.root / "reports/synthetic_batches_v1"

    def _authorize(self):
        if os.environ.get("CHANLUN_TEST_ISOLATION") != "1" or not self.policy.governance_allowed:
            raise PermissionError("BATCH_SYNTHETIC_GOVERNANCE_REQUIRED")
        validate_research_root(self.root, self.policy)

    def _id(self, value):
        if not isinstance(value, str) or value in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
            raise ValueError("BATCH_IDENTITY_INVALID")
        return value

    def _directory(self, identifier):
        return self.directory / self._id(identifier)

    def _read(self, path):
        value = json.loads(path.read_bytes())
        if (value.get("schema_version") != SCHEMA or value.get("workspace") != str(self.root)
                or value.get("domain") != "SYNTHETIC_TEST_ONLY"
                or value.get("hash") != stable_hash({k: v for k, v in value.items() if k != "hash"})):
            raise ValueError("BATCH_CANONICAL_RECORD_INVALID")
        return value

    def _record(self, value):
        result = {"schema_version": SCHEMA, "workspace": str(self.root), "domain": "SYNTHETIC_TEST_ONLY", **value}
        result["hash"] = stable_hash(result)
        return result

    def _state(self, identifier, *, recover_tail=False):
        directory = self._directory(identifier)
        head = self._read(directory / "head.json")
        events = sorted((directory / "events").glob("*.json"))
        previous = None
        committed = None
        for index, path in enumerate(events):
            event = self._read(path)
            if (path.name != f"{index:08d}.json" or event["revision"] != index
                    or event["previous_hash"] != (previous["hash"] if previous else None)):
                raise ValueError("BATCH_HISTORY_INVALID")
            previous = event
            if index == head["revision"]:
                committed = event
        if (recover_tail and previous is not None and committed is not None
                and head["event_hash"] == committed["hash"] and previous["revision"] == head["revision"] + 1):
            receipt = self._read(directory / "confirmation.json")
            preview = self._read(directory / "preview.json")
            if previous["confirmation_hash"] != receipt["hash"] or receipt["preview_hash"] != preview["hash"]:
                raise ValueError("BATCH_APPROVAL_BINDING_CONFLICT")
            # 只前滚已经落盘且紧接原 head 的一个完整事件；缺失/截断历史不回退。
            head = self._record({"event_hash": previous["hash"], "revision": previous["revision"]})
            _atomic_write(directory / "head.json", head)
        if previous is None or head["event_hash"] != previous["hash"] or head["revision"] != previous["revision"]:
            raise ValueError("BATCH_HISTORY_HEAD_CONFLICT")
        return previous

    def _append(self, identifier, previous, **changes):
        value = {k: v for k, v in (previous or {}).items() if k not in {"hash", "revision", "previous_hash"}}
        event = self._record({**value, **changes, "revision": previous["revision"] + 1 if previous else 0,
            "previous_hash": previous["hash"] if previous else None, "recorded_at": self.clock().isoformat()})
        directory = self._directory(identifier)
        _immutable(directory / "events" / f"{event['revision']:08d}.json", event)
        _atomic_write(directory / "head.json", self._record({"event_hash": event["hash"], "revision": event["revision"]}))
        return event

    def _binding(self, member):
        objective_id = self._id(member["objective_id"])
        candidate_id = self._id(member["candidate_id"])
        candidate, contract, _ = _candidate_from_contract_path(self.root, objective_id, candidate_id)
        if contract.content_hash != member["contract_hash"]:
            raise ValueError("BATCH_FROZEN_CONTRACT_CHANGED")
        objective_path = self.root / f"data/research/research_factory/objectives/{objective_id}.json"
        objective = json.loads(objective_path.read_bytes())
        governance = ResearchProposalGovernanceServiceV2(self.root, execution_policy=self.policy)
        preview = governance._load_preview(objective["parent_proposal_id"])
        if governance._validate_binding(preview["binding_evidence"]["execution_binding"]) != preview["binding_evidence"]:
            raise ValueError("BATCH_OBJECTIVE_EXECUTION_BINDING_CHANGED")
        if governance._objective_payload(preview, created_at=objective["created_at"]) != objective:
            raise ValueError("BATCH_OBJECTIVE_CANONICAL_CONFLICT")
        creation = json.loads(governance._receipt_path(objective["parent_proposal_id"]).read_bytes())
        if creation["objective_id"] != objective_id or creation["preview_hash"] != preview["preview_hash"]:
            raise ValueError("BATCH_OBJECTIVE_CREATION_RECEIPT_CONFLICT")
        execution_id = governance._execution_id(objective["parent_proposal_id"], preview)
        journal_path = governance._transaction_path(objective["parent_proposal_id"], execution_id)
        journal = json.loads(journal_path.read_bytes())
        if (creation.get("schema_version") != governance.receipt_schema_version or creation.get("execution_id") != execution_id
                or journal.get("status") != "COMPLETED" or journal.get("receipt_payload") != creation
                or journal["receipt"]["sha256"] != hashlib.sha256(governance._receipt_path(objective["parent_proposal_id"]).read_bytes()).hexdigest()):
            raise ValueError("BATCH_OBJECTIVE_CREATION_TRANSACTION_INVALID")
        family_path = self.root / creation["multiple_testing_family_ref"]
        family_entries = [entry for entry in journal["files"] if entry["target"] == creation["multiple_testing_family_ref"]]
        if len(family_entries) != 1 or family_entries[0]["sha256"] != hashlib.sha256(family_path.read_bytes()).hexdigest():
            raise ValueError("BATCH_ORIGINAL_STATISTICAL_FAMILY_CHANGED")
        proposal_dir = self.root / f"reports/research_candidates/proposals/{objective_id}"
        materialization = json.loads((proposal_dir / "EXECUTABLE_MATERIALIZATION_PREVIEW.json").read_bytes())
        receipt = json.loads((proposal_dir / "EXECUTABLE_MATERIALIZATION_CONFIRMATION.json").read_bytes())
        check = inspect_materialization_confirmation(receipt, preview=materialization, contract=contract, objective_id=objective_id)
        if not inspect_materialization_preview(materialization)["valid"] or not check["valid"] or not check["identity_match"]:
            raise ValueError("BATCH_MATERIALIZATION_CONFIRMATION_INVALID")
        novelty = SyntheticNoveltyBindingServiceV1(self.root)
        with novelty.performance_boundary(member["novelty_confirmation"], candidate_id, contract.content_hash):
            novelty_preview, novelty_receipt = novelty.historical_confirmation(member["novelty_confirmation"])
        paths = [objective_path, governance._receipt_path(objective["parent_proposal_id"]), journal_path,
            proposal_dir / "EXECUTABLE_MATERIALIZATION_PREVIEW.json", proposal_dir / "EXECUTABLE_MATERIALIZATION_CONFIRMATION.json",
            family_path,
            self.root / "data/research/daily_all.parquet",
            self.root / "data/research/strategy_validation/phase4_rerun_v2_factor_values.parquet",
            self.root / "data/research/security_state/normalized/manifest.json",
            self.root / "data/research/security_state/raw/trade_calendar.json",
            self.root / "data/research/data_routing/routing_policy.json",
            self.root / "data/research/universe_policies/A_SHARE_RESEARCH_UNIVERSE_POLICY_V2.json"]
        paths.extend(self.root / source["path"] for source in preview["binding_evidence"]["source_evidence"])
        normalized = self.root / "data/research/security_state/normalized"
        paths.append(normalized / "security_master_v2/records.json")
        for folder in ("st_state", "suspension_state"):
            records = sorted((normalized / folder).glob("*.jsonl"))
            if not records:
                raise ValueError("BATCH_NORMALIZED_STATE_REQUIRED")
            paths.extend(records)
        paths = sorted(set(paths))
        if any(path.resolve() != path or not path.is_file() for path in paths):
            raise ValueError("BATCH_DATA_SOURCE_INVALID")
        import pyarrow.parquet as parquet
        fields = {path.relative_to(self.root).as_posix(): parquet.read_schema(path).names for path in paths if path.suffix == ".parquet"}
        budget = SearchBudgetRegistryV1(objective_id, self.root / creation["budget_registry_ref"]).snapshot()
        return {"candidate": member, "candidate_hash": contract.candidate_hash,
            "objective_identity_hash": objective["objective_identity_hash"], "execution_binding_hash": objective["execution_binding_hash"],
            "policy_identity": contract.policy_identity, "research_period_identity": contract.research_period_identity,
            "registry_identities": contract.factor_event_registry_identities, "data_fields": fields,
            "budget_ref": creation["budget_registry_ref"], "family_id": objective["multiple_testing_family_id"],
            "budget_limits": [{key: row[key] for key in ("kind", "key", "limit")} for row in budget["buckets"]],
            "batch_id": objective["batch_id"], "novelty_snapshot_hash": novelty_preview["evidence_hash"],
            "novelty_receipt_hash": novelty_receipt["evidence_hash"],
            "sources": [{"path": path.relative_to(self.root).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in paths]}

    def request(self, body):
        self._authorize()
        request = SyntheticBatchRequestV1.model_validate(body)
        if (request.schema_version != SCHEMA or request.model != "NONE"
                or any((request.limits.model_calls, request.limits.tokens, request.limits.cost_minor_units))
                or request.actions != [action for action in ACTIONS if action in request.actions]):
            raise ValueError("BATCH_UNSUPPORTED_EXECUTION_CONFIGURATION")
        if request.effective_at >= request.expires_at or request.expires_at <= self.clock():
            raise ValueError("BATCH_INVALID_VALIDITY_WINDOW")
        members = [item.model_dump() for item in request.candidates]
        if len({item["candidate_id"] for item in members}) != len(members):
            raise ValueError("BATCH_DUPLICATE_CANDIDATE")
        bindings = [self._binding(member) for member in members]
        if request.limits.candidates < len(members) or request.limits.batches < len({item["batch_id"] for item in bindings}):
            raise ValueError("BATCH_SCOPE_LIMIT_EXCEEDED")
        if "START_PREDICTIVE_TRIAL_1" in request.actions and request.limits.trials < len(members):
            raise ValueError("BATCH_TRIAL_LIMIT_EXCEEDED")
        for binding in bindings:
            snapshot = SearchBudgetRegistryV1(binding["candidate"]["objective_id"], self.root / binding["budget_ref"]).snapshot()
            if "START_PREDICTIVE_TRIAL_1" in request.actions and any(row["remaining"] < 1 for row in snapshot["buckets"]):
                raise ValueError("BATCH_CANONICAL_BUDGET_EXHAUSTED")
        identifier = uuid.uuid4().hex
        preview = self._record({"batch_authorization_id": identifier, "request": request.model_dump(mode="json"),
            "bindings": bindings, "created_at": self.clock().isoformat(), "execution_authorized": False,
            "confirmation_kind": "SYNTHETIC_TEST_HUMAN", "supported_concurrency": 1, "supported_retries": 0,
            "control_rules": {"pause": "NO_NEW_ACTIONS", "stop": "TERMINAL_NO_NEW_ACTIONS",
                "revoke": "TERMINAL_NO_NEW_ACTIONS", "expiry": "NO_NEW_ACTIONS",
                "inflight": "ORIGINAL_DOMAIN_SETTLEMENT", "recovery": "SETTLEMENT_ONLY_NO_RETRY",
                "wall_clock": "FROM_FIRST_CLAIM_INCLUDES_PAUSES", "budget": "ADDITIONAL_CAP_ORIGINAL_LEDGER_REQUIRED"}})
        _immutable(self._directory(identifier) / "preview.json", preview)
        return preview

    def confirm(self, identifier, body):
        self._authorize()
        if body.get("confirmed") is not True or body.get("test_confirmation") is not True:
            raise ValueError("BATCH_ACTUAL_TEST_CONFIRMATION_REQUIRED")
        with ObjectiveMutationLock.for_resource(self._directory(identifier)):
            preview = self._read(self._directory(identifier) / "preview.json")
            if body.get("preview_hash") != preview["hash"]:
                raise ValueError("BATCH_PREVIEW_CHANGED")
            if (self._directory(identifier) / "confirmation.json").exists():
                return self.inspect(identifier)
            if datetime.fromisoformat(preview["request"]["expires_at"]) <= self.clock():
                raise ValueError("BATCH_CONFIRMATION_EXPIRED")
            if [self._binding(item["candidate"]) for item in preview["bindings"]] != preview["bindings"]:
                raise ValueError("BATCH_BINDING_CHANGED")
            if "START_PREDICTIVE_TRIAL_1" in preview["request"]["actions"]:
                for binding in preview["bindings"]:
                    snapshot = SearchBudgetRegistryV1(binding["candidate"]["objective_id"], self.root / binding["budget_ref"]).snapshot()
                    if not snapshot["buckets"] or any(row["remaining"] < 1 for row in snapshot["buckets"]):
                        raise ValueError("BATCH_CANONICAL_BUDGET_EXHAUSTED")
            if datetime.fromisoformat(preview["request"]["expires_at"]) <= self.clock():
                raise ValueError("BATCH_CONFIRMATION_EXPIRED")
            receipt = self._record({"batch_authorization_id": identifier, "preview_hash": preview["hash"],
                "test_confirmation": True, "confirmed_at": self.clock().isoformat(), "authorization_origin": "SYNTHETIC_TEST_HUMAN"})
            _immutable(self._directory(identifier) / "confirmation.json", receipt)
            self._append(identifier, None, status="ACTIVE", confirmation_hash=receipt["hash"], active_execution=None,
                completed_actions=[], failed_actions=[], first_started_at=None)
            return self.inspect(identifier)

    def inspect(self, identifier):
        preview = self._read(self._directory(identifier) / "preview.json")
        receipt = self._read(self._directory(identifier) / "confirmation.json")
        state = self._state(identifier)
        if receipt["preview_hash"] != preview["hash"] or state["confirmation_hash"] != receipt["hash"] or receipt["test_confirmation"] is not True:
            raise ValueError("BATCH_APPROVAL_BINDING_CONFLICT")
        now = self.clock()
        request = preview["request"]
        status = state["status"]
        if status in {"ACTIVE", "PAUSED"} and now >= datetime.fromisoformat(request["expires_at"]):
            status = "EXPIRED"
        if status == "ACTIVE" and now < datetime.fromisoformat(request["effective_at"]):
            status = "NOT_YET_EFFECTIVE"
        if status in {"ACTIVE", "PAUSED"} and state["first_started_at"] and (now - datetime.fromisoformat(state["first_started_at"])).total_seconds() >= request["limits"]["wall_seconds"]:
            status = "TIME_LIMIT_REACHED"
        return {"preview": preview, "receipt": receipt, "state": state, "status": status,
            "execution_authorized": status == "ACTIVE"}

    def view(self, identifier):
        """只读预览或历史状态；GET 不生成批准、不恢复事件。"""
        if (self._directory(identifier) / "confirmation.json").exists():
            return self.inspect(identifier)
        return {"preview": self._read(self._directory(identifier) / "preview.json"), "receipt": None,
            "state": None, "status": "REQUESTED", "execution_authorized": False}

    def control(self, identifier, action, body):
        self._authorize()
        if body.get("confirmed") is not True or body.get("test_confirmation") is not True:
            raise ValueError("BATCH_ACTUAL_TEST_CONFIRMATION_REQUIRED")
        targets = {"pause": "PAUSED", "stop": "STOPPED", "revoke": "REVOKED", "resume": "ACTIVE"}
        if action not in targets:
            raise ValueError("BATCH_CONTROL_ACTION_INVALID")
        with ObjectiveMutationLock.for_resource(self._directory(identifier)):
            current = self.inspect(identifier)
            allowed = {"pause": {"ACTIVE", "NOT_YET_EFFECTIVE"}, "resume": {"PAUSED"},
                "stop": {"ACTIVE", "PAUSED", "NOT_YET_EFFECTIVE", "EXPIRED", "TIME_LIMIT_REACHED"},
                "revoke": {"ACTIVE", "PAUSED", "NOT_YET_EFFECTIVE", "EXPIRED", "TIME_LIMIT_REACHED"}}
            if current["status"] not in allowed[action]:
                raise ValueError("BATCH_CONTROL_STATE_INVALID")
            self._append(identifier, current["state"], status=targets[action], control_action=action)
            return self.inspect(identifier)

    def _claim(self, identifier):
        self._authorize()
        with ObjectiveMutationLock.for_resource(self._directory(identifier)):
            current = self.inspect(identifier)
            state, preview = current["state"], current["preview"]
            if not current["execution_authorized"]:
                raise PermissionError("BATCH_PARENT_AUTHORIZATION_NOT_ACTIVE")
            if state["active_execution"]:
                raise PermissionError("BATCH_CONCURRENCY_LIMIT_REACHED")
            for index, binding in enumerate(preview["bindings"]):
                for action in preview["request"]["actions"]:
                    execution_id = stable_hash({"parent_confirmation": current["receipt"]["hash"],
                        "candidate": binding["candidate"], "action": action})
                    if any(row["execution_id"] == execution_id for row in state["completed_actions"]):
                        continue
                    if any(row["execution_id"] == execution_id for row in state["failed_actions"]):
                        raise PermissionError("BATCH_RETRY_NOT_AUTHORIZED")
                    if self._binding(binding["candidate"]) != binding:
                        raise ValueError("BATCH_CONFIRMED_BINDING_CHANGED")
                    if action == "START_PREDICTIVE_TRIAL_1":
                        budget = SearchBudgetRegistryV1(binding["candidate"]["objective_id"], self.root / binding["budget_ref"]).snapshot()
                        if not budget["buckets"] or any(row["remaining"] < 1 for row in budget["buckets"]):
                            raise PermissionError("BATCH_CANONICAL_BUDGET_EXHAUSTED")
                    active = {"execution_id": execution_id, "member_index": index, "action": action,
                        "controller_pid": os.getpid(), "launcher_pid": None, "worker_pid": None,
                        "authorization_origin": "BATCH_DELEGATED", "action_started_at": None}
                    self._append(identifier, state, active_execution=active,
                        first_started_at=state["first_started_at"] or self.clock().isoformat())
                    return active
            self._append(identifier, state, status="COMPLETED")
            return None

    def _launcher_started(self, identifier, execution_id, pid):
        with ObjectiveMutationLock.for_resource(self._directory(identifier)):
            current = self.inspect(identifier)
            active = current["state"]["active_execution"]
            if not active or active["execution_id"] != execution_id or active["controller_pid"] != os.getpid():
                raise PermissionError("BATCH_LAUNCH_IDENTITY_CONFLICT")
            if not current["execution_authorized"]:
                raise PermissionError("BATCH_PARENT_AUTHORIZATION_NOT_ACTIVE")
            self._append(identifier, current["state"], active_execution={**active, "launcher_pid": pid})

    def register_worker(self, identifier, execution_id):
        with ObjectiveMutationLock.for_resource(self._directory(identifier)):
            current = self.inspect(identifier)
            active = current["state"]["active_execution"]
            if (not current["execution_authorized"] or not active or active["execution_id"] != execution_id
                    or not active["launcher_pid"] or active["worker_pid"] is not None
                    or not ((os.getpid() == active["launcher_pid"] and os.getppid() == active["controller_pid"])
                            or (os.name == "nt" and os.getppid() == active["launcher_pid"]))):
                raise PermissionError("BATCH_BOUNDED_WORKER_IDENTITY_REQUIRED")
            self._append(identifier, current["state"], active_execution={**active, "worker_pid": os.getpid()})

    def begin_action(self, identifier, execution_id, action):
        # 此事件是受限动作的启动线性化点；计算不持有批次大锁，性能访问另行复核。
        with self.admission(identifier, execution_id, action=action) as (current, active, binding):
            if active["action_started_at"] is not None:
                raise PermissionError("BATCH_ACTION_ALREADY_STARTED")
            self._append(identifier, current["state"], active_execution={**active, "action_started_at": self.clock().isoformat()})
            return binding

    def historical_execution(self, identifier, execution_id):
        current = self.inspect(identifier)
        state = current["state"]
        rows = [*state["completed_actions"], *state["failed_actions"]]
        if state["active_execution"]:
            rows.append(state["active_execution"])
        matches = [row for row in rows if row["execution_id"] == execution_id]
        if len(matches) != 1:
            raise ValueError("BATCH_HISTORICAL_EXECUTION_CONFLICT")
        row = matches[0]
        return row, current["preview"]["bindings"][row["member_index"]]

    def _settle_domain_exit(self, identifier, active):
        if active["action"] != "START_PREDICTIVE_TRIAL_1":
            from .structural_entry import StructuralEntryServiceV1
            _, binding = self.historical_execution(identifier, active["execution_id"])
            member = binding["candidate"]
            with ObjectiveMutationLock(self.root, "objective:" + member["objective_id"]):
                service = StructuralEntryServiceV1(self.root)
                result = service._existing_terminal(member["objective_id"], member["candidate_id"])
                if result is not None:
                    # 原服务在已有终态分支复核 canonical identity，不能只信任结果 JSON 的 PASS 文本。
                    result = service.start(member["objective_id"], candidate_id=member["candidate_id"], confirmed=True)
            return {"status": result["status"] if result else "NO_STRUCTURAL_TERMINAL", "completed": bool(result and result["status"] == "PASS")}
        from .synthetic_batch_delegation import BatchPredictiveTrialStartServiceV1
        _, binding = self.historical_execution(identifier, active["execution_id"])
        service = BatchPredictiveTrialStartServiceV1(self, identifier, active["execution_id"], binding["candidate"])
        return service.settle_after_exit(binding["candidate"]["objective_id"])

    def recover(self, identifier):
        """只结算已退出的动作；不派生新许可，不自动重跑已开始的动作。"""
        self._authorize()
        from .orchestrator_launcher import OrchestratorProcessLauncherV1
        with ObjectiveMutationLock.for_resource(self._directory(identifier)):
            self._state(identifier, recover_tail=True)
            current = self.inspect(identifier)
            state = current["state"]
            active = state["active_execution"]
            if active is None:
                return current
            if any(pid and OrchestratorProcessLauncherV1._pid_alive(pid)
                    for pid in (active["controller_pid"], active["launcher_pid"], active["worker_pid"])):
                raise PermissionError("BATCH_EXECUTION_PROCESS_STILL_ALIVE")
            settlement = self._settle_domain_exit(identifier, active)
            key = "completed_actions" if settlement["completed"] else "failed_actions"
            status = state["status"] if state["status"] != "ACTIVE" or settlement["completed"] else "BLOCKED"
            self._append(identifier, state, **{key: [*state[key], {**active, "settlement": settlement,
                "recovered_at": self.clock().isoformat()}]}, active_execution=None, status=status)
            return self.inspect(identifier)

    def execute_next(self, identifier):
        self._authorize()
        active = self._claim(identifier)
        if active is None:
            return self.inspect(identifier)
        current = self.inspect(identifier)
        request = current["preview"]["request"]
        now = self.clock()
        remaining = min((datetime.fromisoformat(request["expires_at"]) - now).total_seconds(),
            request["limits"]["wall_seconds"] - (now - datetime.fromisoformat(current["state"]["first_started_at"])).total_seconds())
        execution_id = active["execution_id"]
        environment = dict(os.environ)
        # 工作区是数据根，不能依赖从该目录查找 Python 包。
        package_root = str(Path(__file__).resolve().parents[2])
        environment["PYTHONPATH"] = os.pathsep.join([package_root, *sys.path])
        environment.update(OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", NUMEXPR_NUM_THREADS="1")
        try:
            result = run_bounded_worker([sys.executable, "-m", "chanlun_trader.synthetic_batch_worker"], root=self.root,
                memory_mib=request["limits"]["memory_mib"], wall_seconds=remaining,
                execution={"root": str(self.root), "batch_authorization_id": identifier, "execution_id": execution_id},
                on_started=lambda pid: self._launcher_started(identifier, execution_id, pid), environment=environment)
        except Exception as exc:
            # 资源安装或控制竞争失败后，launcher 已在 finally 终止 worker；仍须结算原领域事实。
            result = {"returncode": None, "stdout": b"", "stderr": b"", "timed_out": False,
                "launch_error": {"type": type(exc).__name__, "message": str(exc)}}
        directory = self._directory(identifier) / "executions" / execution_id
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "stdout.bin").write_bytes(result["stdout"])
        (directory / "stderr.bin").write_bytes(result["stderr"])
        settlement = self._settle_domain_exit(identifier, active)
        with ObjectiveMutationLock.for_resource(self._directory(identifier)):
            current = self.inspect(identifier)
            state = current["state"]
            if state["active_execution"]["execution_id"] != execution_id:
                raise ValueError("BATCH_EXECUTION_SETTLEMENT_CONFLICT")
            outcome_path = directory / "outcome.json"
            outcome = self._read(outcome_path) if outcome_path.exists() else None
            succeeded = settlement["completed"]
            if outcome is not None and outcome["execution_id"] != execution_id:
                raise ValueError("BATCH_WORKER_OUTCOME_IDENTITY_CONFLICT")
            row = {**state["active_execution"], "returncode": result["returncode"], "timed_out": result["timed_out"],
                "launch_error": result.get("launch_error"), "outcome_hash": outcome["hash"] if outcome else None,
                "settlement": settlement, "settled_at": self.clock().isoformat()}
            key = "completed_actions" if succeeded else "failed_actions"
            status = state["status"] if succeeded or state["status"] != "ACTIVE" else "BLOCKED"
            self._append(identifier, state, **{key: [*state[key], row]}, active_execution=None, status=status)
        return self.inspect(identifier)

    def run(self, identifier):
        while True:
            current = self.inspect(identifier)
            if not current["execution_authorized"]:
                return current
            current = self.execute_next(identifier)
            if current["status"] != "ACTIVE":
                return current

    @contextmanager
    def admission(self, identifier, execution_id, *, action):
        self._authorize()
        with ObjectiveMutationLock.for_resource(self._directory(identifier)), ExitStack() as locks:
            current = self.inspect(identifier)
            active = current["state"]["active_execution"]
            if (not current["execution_authorized"] or not active or active["execution_id"] != execution_id
                    or active["action"] != action or active.get("worker_pid") != os.getpid()):
                raise PermissionError("BATCH_PARENT_AUTHORIZATION_NOT_ACTIVE")
            binding = current["preview"]["bindings"][active["member_index"]]
            for source in sorted(binding["sources"], key=lambda item: item["path"]):
                locks.enter_context(ObjectiveMutationLock.for_resource(self.root / source["path"]))
            if self._binding(binding["candidate"]) != binding:
                raise ValueError("BATCH_CONFIRMED_BINDING_CHANGED")
            if not self.inspect(identifier)["execution_authorized"]:
                raise PermissionError("BATCH_PARENT_AUTHORIZATION_NOT_ACTIVE")
            yield current, active, binding
