"""明确来源范围内的合成新颖性绑定；不创建 Trial 或预算权限。"""
from contextlib import ExitStack, contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

from ..execution_policy import ExecutionPolicy, validate_research_root
from .common import stable_hash
from .durability import (DurableFrozenCandidateContractV1,
    FROZEN_CANDIDATE_CONTRACT_REGISTRY_SCHEMA, canonical_frozen_contract_identity_hash, _atomic_write)
from .mutation_boundary import ObjectiveMutationLock
from .novelty import CandidateNoveltyGateV2
from .paper_replay import _immutable


SCHEMA = "synthetic-novelty-binding-v1"


def _design(contract):
    record = contract.reconstruct_candidate()
    candidate = record.candidate
    # 与原批次 Gate 的输入字段一致，不读取结论、绩效或使用资格。
    return {"candidate_id": candidate.candidate_id, "candidate_hash": record.preregistration_hash,
        "mechanism": candidate.mechanism, "factor_ids": [str(item["factor_id"]) for item in candidate.factor_bindings],
        "event_ids": list(candidate.signal_logic.get("event_dependencies", ())),
        "holding_period_days": int(candidate.holding_period), "semantic_fingerprint": record.semantic_fingerprint,
        "parameter_fingerprint": getattr(candidate, "parameter_fingerprint", None)}


class SyntheticNoveltyBindingServiceV1:
    def __init__(self, root):
        self.root = Path(root)
        if os.environ.get("CHANLUN_TEST_ISOLATION") != "1":
            raise PermissionError("NOVELTY_IMPORT_ISOLATION_REQUIRED")
        validate_research_root(self.root, ExecutionPolicy("READ_ONLY", "SYNTHETIC"))
        self.directory = self.root / "reports/synthetic_novelty_v1"
        self.resource = self.directory / "source-scope"
        self.workspace_identity = self.root / "reports/synthetic_novelty_workspace.json"

    def _authorize(self, policy):
        if not policy.governance_allowed:
            raise PermissionError("EXECUTION_POLICY_READ_ONLY")
        validate_research_root(self.root, policy)

    def _path(self, value):
        if not isinstance(value, str) or not value or "\\" in value:
            raise ValueError("NOVELTY_RELATIVE_SOURCE_REQUIRED")
        relative = Path(value)
        if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != value:
            raise ValueError("NOVELTY_RELATIVE_SOURCE_REQUIRED")
        path = self.root / relative
        if path.resolve() != path or not path.is_file():
            raise ValueError("NOVELTY_SOURCE_MISSING_OR_LINKED")
        return path

    def _read(self, path):
        validate_research_root(self.root, ExecutionPolicy("READ_ONLY", "SYNTHETIC"))
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("schema_version") != SCHEMA:
            raise ValueError("NOVELTY_EVIDENCE_SCHEMA_INVALID")
        if value.get("workspace") != str(self.root) or value.get("domain") != "SYNTHETIC_TEST_ONLY":
            raise ValueError("NOVELTY_WORKSPACE_BINDING_CONFLICT")
        if value.get("evidence_hash") != stable_hash({k: v for k, v in value.items() if k != "evidence_hash"}):
            raise ValueError("NOVELTY_EVIDENCE_HASH_INVALID")
        if path != self.workspace_identity and value.get("workspace_id") != self._read(self.workspace_identity).get("workspace_id"):
            raise ValueError("NOVELTY_WORKSPACE_IDENTITY_CHANGED")
        return value

    def _save(self, path, value):
        result = {"schema_version": SCHEMA, "workspace": str(self.root), "domain": "SYNTHETIC_TEST_ONLY", **value}
        if path != self.workspace_identity:
            result["workspace_id"] = self._read(self.workspace_identity)["workspace_id"]
        result["evidence_hash"] = stable_hash(result)
        _immutable(path, result)
        return result

    def _scope(self):
        paths = sorted((self.directory / "scopes").glob("*.json"))
        if not paths:
            raise ValueError("NOVELTY_SOURCES_NOT_CONFIGURED")
        previous = None
        for index, path in enumerate(paths):
            value = self._read(path)
            if path.name != f"{index:08d}.json" or value.get("revision") != index:
                raise ValueError("NOVELTY_SCOPE_SEQUENCE_INVALID")
            sources = value.get("sources")
            if not isinstance(sources, list) or not sources or sources != sorted(set(sources)):
                raise ValueError("NOVELTY_SCOPE_INVALID")
            if value.get("previous_hash") != (previous["evidence_hash"] if previous else None):
                raise ValueError("NOVELTY_SCOPE_HISTORY_INVALID")
            if previous and not set(previous["sources"]).issubset(sources):
                raise ValueError("NOVELTY_SCOPE_SHRINK_FORBIDDEN")
            previous = value
        if self._read(self.workspace_identity).get("scope_head") != previous["evidence_hash"]:
            raise ValueError("NOVELTY_SCOPE_HEAD_CONFLICT")
        return previous

    def declare_sources(self, policy, sources):
        """部署入口声明真实 registry 路径；不接受成员数据，不提供执行授权。只允许扩充原范围。"""
        self._authorize(policy)
        if not isinstance(sources, list) or not sources or any(not isinstance(x, str) for x in sources):
            raise ValueError("NOVELTY_SOURCES_REQUIRED")
        paths = sorted(set(sources))
        if len(paths) != len(sources):
            raise ValueError("NOVELTY_DUPLICATE_SOURCE_PATH")
        with ObjectiveMutationLock.for_resource(self.resource):
            initialized = self.workspace_identity.exists()
            if not initialized and any((self.directory / name).exists() for name in ("scopes", "previews", "confirmations")):
                raise ValueError("NOVELTY_WORKSPACE_IDENTITY_MISSING")
            prior = self._scope() if initialized else None
            if prior and not set(prior["sources"]).issubset(paths):
                raise ValueError("NOVELTY_SCOPE_SHRINK_FORBIDDEN")
            for path in paths:
                self._registry(path)
            if not initialized:
                self._save(self.workspace_identity, {"workspace_id": uuid.uuid4().hex})
            if prior and prior["sources"] == paths:
                return prior
            revision = prior["revision"] + 1 if prior else 0
            result = self._save(self.directory / "scopes" / f"{revision:08d}.json",
                {"revision": revision, "previous_hash": prior["evidence_hash"] if prior else None,
                 "sources": paths, "execution_authorized": False})
            identity = self._read(self.workspace_identity)
            identity["scope_head"] = result["evidence_hash"]
            identity["evidence_hash"] = stable_hash({k: v for k, v in identity.items() if k != "evidence_hash"})
            _atomic_write(self.workspace_identity, identity)
            return result

    def _registry(self, reference):
        path = self._path(reference)
        raw = path.read_bytes()
        value = json.loads(raw)
        if not isinstance(value, dict) or set(value) != {"schema_version", "registry_hash", "contracts"}:
            raise ValueError("NOVELTY_REGISTRY_SCHEMA_INVALID")
        if value["schema_version"] != FROZEN_CANDIDATE_CONTRACT_REGISTRY_SCHEMA or not isinstance(value["contracts"], list):
            raise ValueError("NOVELTY_REGISTRY_SCHEMA_INVALID")
        if value["registry_hash"] != stable_hash(value["contracts"]):
            raise ValueError("NOVELTY_REGISTRY_HASH_INVALID")
        contracts = []
        for item in value["contracts"]:
            contract = DurableFrozenCandidateContractV1.from_dict(item)
            if contract.to_dict() != item:
                raise ValueError("NOVELTY_CONTRACT_CONTENT_INVALID")
            contracts.append(contract)
        return {"path": reference, "sha256": hashlib.sha256(raw).hexdigest(),
                "schema_version": value["schema_version"], "registry_hash": value["registry_hash"]}, contracts

    def snapshot(self, candidate_id, contract_hash):
        validate_research_root(self.root, ExecutionPolicy("READ_ONLY", "SYNTHETIC"))
        scope = self._scope()
        members = {}
        sources = []
        count = 0
        for reference in scope["sources"]:
            evidence, contracts = self._registry(reference)
            sources.append(evidence)
            for ordinal, contract in enumerate(contracts):
                count += 1
                item = {"candidate_id": contract.candidate_id, "candidate_hash": contract.candidate_hash,
                    "contract_hash": contract.content_hash,
                    "canonical_identity": canonical_frozen_contract_identity_hash(contract),
                    "design": _design(contract)}
                key = contract.candidate_id
                if key in members and {k: v for k, v in members[key].items() if k != "origins"} != item:
                    raise ValueError("NOVELTY_MEMBER_IDENTITY_CONFLICT")
                members.setdefault(key, {**item, "origins": []})["origins"].append({"source": reference, "ordinal": ordinal})
        current = members.get(candidate_id)
        if current is None or current["contract_hash"] != contract_hash:
            raise ValueError("NOVELTY_EXACT_SELF_NOT_FOUND")
        comparison = [members[key] for key in sorted(members) if key != candidate_id]
        return {"schema_version": SCHEMA, "workspace": str(self.root), "domain": "SYNTHETIC_TEST_ONLY",
            "workspace_id": scope["workspace_id"],
            "scope": scope, "sources": sources, "source_count": len(sources),
            "member_occurrences_before_exclusion": count, "canonical_members_before_exclusion": len(members),
            "excluded_self": current, "members": comparison, "comparison_member_count": len(comparison),
            "members_hash": stable_hash(comparison), "candidate_id": candidate_id, "contract_hash": contract_hash}

    def preview(self, policy, candidate_id, contract_hash):
        self._authorize(policy)
        snapshot = self.snapshot(candidate_id, contract_hash)
        identifier = stable_hash(snapshot)
        saved = self._save(self.directory / "previews" / (identifier + ".json"), {"snapshot": snapshot})
        return {"schema_version": SCHEMA, "preview_id": identifier, "preview": saved,
                "confirmation_required": True, "execution_authorized": False}

    def _identifier(self, value):
        if not isinstance(value, str) or not re.fullmatch("[0-9a-f]{64}", value):
            raise ValueError("NOVELTY_PREVIEW_ID_INVALID")
        return value

    def historical_confirmation(self, identifier):
        identifier = self._identifier(identifier)
        preview = self._read(self.directory / "previews" / (identifier + ".json"))
        receipt = self._read(self.directory / "confirmations" / (identifier + ".json"))
        if stable_hash(preview["snapshot"]) != identifier or receipt.get("preview_hash") != preview["evidence_hash"] or receipt.get("preview_id") != identifier:
            raise ValueError("NOVELTY_CONFIRMATION_BINDING_INVALID")
        if receipt.get("test_confirmation") is not True or receipt.get("execution_authorized") is not False:
            raise ValueError("NOVELTY_CONFIRMATION_TYPE_INVALID")
        return preview, receipt

    def confirm(self, policy, body):
        self._authorize(policy)
        if body.get("confirmed") is not True:
            raise ValueError("NOVELTY_EXPLICIT_CONFIRMATION_REQUIRED")
        identifier = self._identifier(body.get("preview_id"))
        preview = self._read(self.directory / "previews" / (identifier + ".json"))
        snapshot = preview["snapshot"]
        if stable_hash(snapshot) != identifier:
            raise ValueError("NOVELTY_PREVIEW_IDENTITY_INVALID")
        with self._locked_sources(snapshot):
            if self.snapshot(snapshot["candidate_id"], snapshot["contract_hash"]) != snapshot:
                raise ValueError("NOVELTY_PREVIEW_STALE")
            return self._save(self.directory / "confirmations" / (identifier + ".json"),
                {"preview_id": identifier, "preview_hash": preview["evidence_hash"],
                 "test_confirmation": True, "execution_authorized": False})

    @contextmanager
    def _locked_sources(self, snapshot):
        with ExitStack() as locks:
            locks.enter_context(ObjectiveMutationLock.for_resource(self.resource))
            for path in snapshot["scope"]["sources"]:
                locks.enter_context(ObjectiveMutationLock.for_resource(self._path(path)))
            yield

    @contextmanager
    def performance_boundary(self, identifier, candidate_id, contract_hash):
        """仅供调用方在同一短临界区完成其原有准入标记；不调用或替代其他门禁。"""
        preview, _ = self.historical_confirmation(identifier)
        snapshot = preview["snapshot"]
        if (snapshot["candidate_id"], snapshot["contract_hash"]) != (candidate_id, contract_hash):
            raise ValueError("NOVELTY_CANDIDATE_BINDING_CONFLICT")
        with self._locked_sources(snapshot):
            if self.snapshot(candidate_id, contract_hash) != snapshot:
                raise ValueError("NOVELTY_CONFIRMED_SNAPSHOT_STALE")
            decision = CandidateNoveltyGateV2().evaluate(snapshot["excluded_self"]["design"],
                historical_candidates=[item["design"] for item in snapshot["members"]])
            if not decision.allowed:
                raise ValueError("NOVELTY_GATE_REJECTED:" + decision.reason)
            yield {**decision.to_dict(), "passed": decision.allowed, "binding_id": identifier,
                   "scope_hash": snapshot["scope"]["evidence_hash"], "global_novelty_verified": False}


@contextmanager
def canonical_novelty_boundary(root, objective_id, candidate, contract):
    """新绑定只能由实际新版启动意图传入；旧路径不冒称已有新颖性证据。"""
    metadata = getattr(candidate, "metadata", {}) or {}
    identifier = metadata.get("synthetic_novelty_confirmation")
    from .predictive_trial_start import PredictiveTrialStartServiceV1
    intents = PredictiveTrialStartServiceV1(root, auto_run=False)._read_intents(objective_id)
    intent = intents.get(metadata.get("start_intent_id"))
    canonical_new = any(item.get("candidate_id") == candidate.candidate_id and any(
        item.get(key) is not None for key in ("synthetic_flow_version", "synthetic_novelty_confirmation"))
        for item in intents.values())
    if identifier is None and metadata.get("synthetic_flow_version") is None:
        if canonical_new:
            raise ValueError("NOVELTY_CANONICAL_PROTOCOL_METADATA_REQUIRED")
        yield {"passed": True}  # 保留旧流程原语义；该路径不在新绑定认证范围。
        return
    if not intent or any((intent.get("synthetic_flow_version") != SCHEMA,
        intent.get("intent_hash") != stable_hash({k: v for k, v in intent.items() if k != "intent_hash"}),
        metadata.get("synthetic_flow_version") != SCHEMA,
        intent.get("synthetic_novelty_confirmation") != identifier,
        intent.get("test_confirmation") is not True,
        intent.get("candidate_id") != candidate.candidate_id,
        intent.get("candidate_hash") != candidate.candidate_hash,
        intent.get("trial_id") != metadata.get("trial_id"))):
        raise ValueError("NOVELTY_ACTUAL_START_INTENT_REQUIRED")
    service = SyntheticNoveltyBindingServiceV1(root)
    with service.performance_boundary(identifier, candidate.candidate_id, contract.content_hash) as evidence:
        yield evidence
