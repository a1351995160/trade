"""新建 synthetic Objective 的事前执行身份；复用原创建事务，不授予启动权。"""
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path

from ..execution_policy import validate_research_root
from ..research.event import EventRegistry
from ..research.unified_factor import UnifiedFactorRegistry, UNIFIED_SCHEMA_VERSION
from ..research.validation_policy_v2 import load_validation_decision_policy_v2
from .common import stable_hash
from .mutation_boundary import ObjectiveMutationLock
from .research_proposal_governance import (
    ResearchProposalGovernanceServiceV1,
    _without_dynamic_preview_fields,
)


SCHEMA = "objective-creation-preview-v2"
POLICY_PATH = "data/research/strategy_validation/validation_decision_policy_v2.json"


class ResearchProposalGovernanceServiceV2(ResearchProposalGovernanceServiceV1):
    preview_schema_version = SCHEMA
    receipt_schema_version = "research-proposal-objective-creation-receipt-v2"

    def __init__(self, root, *, execution_policy, clock=None, crash_at=None):
        super().__init__(root, clock=clock, crash_at=crash_at)
        self.execution_policy = execution_policy
        self._authorize()

    def _authorize(self):
        if os.environ.get("CHANLUN_TEST_ISOLATION") != "1" or not self.execution_policy.governance_allowed:
            raise PermissionError("OBJECTIVE_V2_SYNTHETIC_GOVERNANCE_REQUIRED")
        validate_research_root(self.root, self.execution_policy)

    def _source(self, relative):
        if not isinstance(relative, str) or not relative or "\\" in relative:
            raise ValueError("OBJECTIVE_BINDING_SOURCE_INVALID")
        path = self.root / relative
        if Path(relative).is_absolute() or ".." in Path(relative).parts or path.resolve() != path or not path.is_file():
            raise ValueError("OBJECTIVE_BINDING_SOURCE_INVALID")
        return path

    def _validate_binding(self, binding):
        self._authorize()
        if not isinstance(binding, dict) or set(binding) != {"policy_identity", "research_period_identity", "factor_event_registry_identities"}:
            raise ValueError("OBJECTIVE_EXECUTION_BINDING_REQUIRED")
        policy_path = self._source(POLICY_PATH)
        policy, policy_hash = load_validation_decision_policy_v2(policy_path)
        if binding["policy_identity"] != {"policy_id": policy.policy_id, "version": policy.policy_version, "hash": policy_hash}:
            raise ValueError("OBJECTIVE_POLICY_BINDING_MISMATCH")
        period = binding["research_period_identity"]
        if not isinstance(period, dict) or set(period) != {"id", "start", "end"} or not isinstance(period["id"], str) or not period["id"]:
            raise ValueError("OBJECTIVE_RESEARCH_PERIOD_INVALID")
        for field in ("start", "end"):
            if type(period[field]) is not int:
                raise ValueError("OBJECTIVE_RESEARCH_PERIOD_INVALID")
            datetime.strptime(str(period[field]), "%Y%m%d")
        if not policy.research_start <= period["start"] <= period["end"] <= policy.research_end:
            raise ValueError("OBJECTIVE_POLICY_WINDOW_CONFLICT")
        registries = binding["factor_event_registry_identities"]
        if not isinstance(registries, dict) or set(registries) != {"factor_registry", "event_registry"}:
            raise ValueError("OBJECTIVE_REGISTRY_BINDINGS_REQUIRED")
        sources = [policy_path, policy_path.with_name("validation_decision_policy_v2.lock.json")]
        for kind, identity in sorted(registries.items()):
            if not isinstance(identity, dict) or set(identity) != {"path", "sha256"}:
                raise ValueError("OBJECTIVE_REGISTRY_FILE_IDENTITY_REQUIRED")
            path = self._source(identity["path"])
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != identity["sha256"]:
                raise ValueError("OBJECTIVE_REGISTRY_IDENTITY_CHANGED")
            payload = json.loads(raw)
            key = "factors" if kind == "factor_registry" else "events"
            if not isinstance(payload, dict) or not isinstance(payload.get(key), list):
                raise ValueError("OBJECTIVE_REGISTRY_SCHEMA_INVALID")
            id_key = "factor_id" if kind == "factor_registry" else "event_id"
            if any(not isinstance(item, dict) or any(not isinstance(item.get(field), str) or not item[field]
                    for field in (id_key, "version")) for item in payload[key]):
                raise ValueError("OBJECTIVE_REGISTRY_IDENTITY_INVALID")
            if kind == "factor_registry":
                if payload.get("schema_version") != UNIFIED_SCHEMA_VERSION:
                    raise ValueError("OBJECTIVE_REGISTRY_SCHEMA_INVALID")
                registry = UnifiedFactorRegistry.read(path)
            else:
                registry = EventRegistry.load(path)
            # 原 reader 的映射不能静默吞掉重复身份。
            if len(registry.items()) != len(payload[key]):
                raise ValueError("OBJECTIVE_REGISTRY_IDENTITY_CONFLICT")
            sources.append(path)
        return {"execution_binding": deepcopy(binding), "workspace": str(self.root),
            "domain": "SYNTHETIC_TEST_ONLY", "source_evidence": [
                {"path": path.relative_to(self.root).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                for path in sorted(sources)]}

    def review(self, proposal_id, action, reviewer=None, *, execution_binding, **kwargs):
        with self._mutex, ObjectiveMutationLock(self.root, "objective-creation:" + proposal_id):
            evidence = self._validate_binding(execution_binding)
            if self._preview_path(proposal_id).exists():
                if self._load_preview(proposal_id)["binding_evidence"] != evidence:
                    raise ValueError("OBJECTIVE_EXECUTION_BINDING_CHANGED")
            self._requested_binding = evidence
            try:
                return super().review(proposal_id, action, reviewer, **kwargs)
            finally:
                del self._requested_binding

    review_proposal = review

    def _build_preview(self, proposal, *, direction=None, generated_at=None):
        preview = super()._build_preview(proposal, direction=direction, generated_at=generated_at)
        evidence = self._requested_binding
        identity_hash = stable_hash({"version": SCHEMA, "base_objective_identity_hash": preview["objective_identity_hash"],
            "binding_evidence": evidence})
        preview.update(schema_version=SCHEMA, binding_evidence=evidence, objective_identity_hash=identity_hash,
            target_objective_id="RESEARCH_OBJECTIVE_EVOLUTION_V2_" + identity_hash[:24].upper(),
            test_confirmation=True)
        preview["preview_hash"] = stable_hash(_without_dynamic_preview_fields(preview))
        preview["confirmation_token"] = stable_hash({"contract": SCHEMA, "preview_hash": preview["preview_hash"]})[:40]
        return preview

    def _load_preview(self, proposal_id):
        preview = super()._load_preview(proposal_id)
        evidence = preview["binding_evidence"]
        if evidence["workspace"] != str(self.root) or evidence["domain"] != "SYNTHETIC_TEST_ONLY":
            raise ValueError("OBJECTIVE_WORKSPACE_BINDING_CONFLICT")
        return preview

    def _objective_payload(self, preview, *, created_at):
        objective = super()._objective_payload(preview, created_at=created_at)
        binding = deepcopy(preview["binding_evidence"]["execution_binding"])
        policy = binding["policy_identity"]
        binding["policy_identity"] = {"objective_id": objective["objective_id"], "policy_id": policy["policy_id"],
            "policy_version": policy["version"], "policy_hash": policy["hash"]}
        objective.update(binding)
        objective.update(schema_version="research-objective-v2", batch_id=objective["objective_id"] + "_B01",
            execution_binding_hash=stable_hash(preview["binding_evidence"]), execution_binding_version=SCHEMA)
        return objective

    def confirm(self, proposal_id, payload):
        self._authorize()
        if payload.get("test_confirmation") is not True:
            raise ValueError("OBJECTIVE_SYNTHETIC_TEST_CONFIRMATION_REQUIRED")
        with self._mutex, ObjectiveMutationLock(self.root, "objective-creation:" + proposal_id), ExitStack() as stack:
            preview = self._load_preview(proposal_id)
            evidence = preview["binding_evidence"]
            for source in evidence["source_evidence"]:
                stack.enter_context(ObjectiveMutationLock.for_resource(self._source(source["path"])))
            if self._validate_binding(evidence["execution_binding"]) != evidence:
                raise ValueError("OBJECTIVE_BINDING_EVIDENCE_CHANGED")
            return super().confirm(proposal_id, payload)

    confirm_objective_creation = confirm
    create_objective_after_confirmation = confirm

    def recover(self, proposal_id, execution_id=None):
        self._authorize()
        # 已确认事务只按原协议完成持久化，不产生任何研究启动许可。
        with self._mutex, ObjectiveMutationLock(self.root, "objective-creation:" + proposal_id):
            return super().recover(proposal_id, execution_id)
