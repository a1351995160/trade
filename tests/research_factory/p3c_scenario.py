"""同一临时目标的显式测试驱动；不拥有 planner 或 canonical 状态。"""
from __future__ import annotations

import json
import os
from pathlib import Path

from chanlun_trader.execution_policy import ExecutionPolicy
from chanlun_trader.research.strategy_candidate import StrategyCandidateSpec
from chanlun_trader.research.strategy_semantic import build_semantic_record
from chanlun_trader.research.validation_policy_v2 import default_validation_decision_policy_v2, lock_payload
from chanlun_trader.research_factory.ai_design_approval import AIDesignApprovalServiceV1
from chanlun_trader.research_factory.autonomous_control_plane import AutonomousResearchControlPlaneV1
from chanlun_trader.research_factory.budget import SearchBudgetRegistryV1
from chanlun_trader.research_factory.candidate_generation import CandidateGenerationManagerV1
from chanlun_trader.research_factory.candidate_executable_materialization import CandidateExecutableMaterializationManagerV1
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.durability import DurableFrozenCandidateContractV1
from chanlun_trader.research_factory.research_evolution_ai_design import ResearchEvolutionAIDesignServiceV1, EXECUTABLE_AI_DESIGN_SCHEMA_VERSION
from test_candidate_generation_governance_v1 import OBJECTIVE_ID, _fixture_root, _write_json
from test_candidate_executable_materialization_v1 import _base_candidate

CLOCK = "2026-09-09T00:00:00+00:00"


class Scenario:
    def __init__(self, root: Path, objective_id: str = OBJECTIVE_ID):
        self.root = root
        self.objective_id = objective_id
        self.plane = AutonomousResearchControlPlaneV1(root, execution_policy=ExecutionPolicy("GOVERNED", "SYNTHETIC"))

    def event(self, name: str, **details):
        with (self.root / "p3c-test-events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"event": name, **details}, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def initialize(self):
        _fixture_root(self.root)
        if self.objective_id != OBJECTIVE_ID:
            # 初始资料身份重绑定只发生于任何设计/批准之前。
            for path in list(self.root.rglob("*.json")):
                value = path.read_text(encoding="utf-8").replace(OBJECTIVE_ID, self.objective_id)
                destination = path.with_name(path.name.replace(OBJECTIVE_ID, self.objective_id))
                destination.write_text(value, encoding="utf-8")
                if destination != path:
                    path.unlink()
            parent = self.root / "reports/research_evolution/proposals/RESEARCH_EVOLUTION_PROPOSAL.json"
            proposal = json.loads(parent.read_bytes())
            proposal["proposal_hash"] = stable_hash({k: v for k, v in proposal.items() if k != "proposal_hash"})
            _write_json(self.root, parent.relative_to(self.root).as_posix(), proposal)
            for folder in ("objectives", "lineage"):
                path = self.root / f"data/research/research_factory/{folder}/{self.objective_id}.json"
                value = json.loads(path.read_bytes())
                old = value.get("parent_proposal_hash") or value["proposal_hash"]
                path.write_text(json.dumps(value).replace(old, proposal["proposal_hash"]), encoding="utf-8")
        objective_path = self.root / f"data/research/research_factory/objectives/{self.objective_id}.json"
        objective = json.loads(objective_path.read_bytes())
        objective.update({
            "batch_id": self.objective_id + "_B01",
            "factor_event_registry_identities": {"factor_registry": {"id": "P3C_FACTORS"}, "event_registry": {"id": "P3C_EVENTS"}},
            "research_period_identity": {"id": "P3C_PERIOD", "start": 20200101, "end": 20251231},
            "policy_identity": {"objective_id": self.objective_id, "policy_id": "P3C_POLICY"},
        })
        _write_json(self.root, objective_path.relative_to(self.root).as_posix(), objective)
        budget = SearchBudgetRegistryV1(self.objective_id, self.budget_path)
        budget.register_objective(4)
        budget.register_batch(objective["batch_id"], 4)
        budget.register_family(objective["multiple_testing_family_id"], 4)
        policy_path = self.root / "data/research/strategy_validation/validation_decision_policy_v2.json"
        policy = default_validation_decision_policy_v2()
        _write_json(self.root, policy_path.relative_to(self.root).as_posix(), policy.to_dict())
        _write_json(self.root, policy_path.with_suffix(".lock.json").relative_to(self.root).as_posix(), lock_payload(policy, policy_path))
        self.event("initialization", files=len(list(self.root.rglob("*.json"))), budget_registrations=3)
        return self

    @property
    def budget_path(self):
        return self.root / f"data/research/research_factory/batches/{self.objective_id}_B01/search_budget_registry.json"

    @property
    def proposal(self):
        return json.loads((self.root / f"reports/research_candidates/proposals/{self.objective_id}/CANDIDATE_PROPOSAL.json").read_bytes())

    def design_input(self):
        objective = json.loads((self.root / f"data/research/research_factory/objectives/{self.objective_id}.json").read_bytes())
        seed = json.loads(json.dumps(_base_candidate().to_dict()).replace("RETURN_5D", "VOLUME_ACCEL"))
        seed.update(candidate_id="CAND_P3C_" + stable_hash(self.objective_id)[:12].upper() + "_V1", parent_hypothesis_id="HYP_P3C_" + stable_hash(self.objective_id)[:12].upper(), created_at=CLOCK)
        candidate = StrategyCandidateSpec.create(seed)
        hypothesis = {"hypothesis_id": candidate.parent_hypothesis_id, "hypothesis_fingerprint": stable_hash({"objective": self.objective_id, "mechanism": "momentum"}), "hypothesis_type": "CONTINUATION", "market_regime": [], "research_hypothesis": "合成动量结构验证", "mechanism_family": "momentum"}
        record = build_semantic_record(candidate, hypothesis)
        contract = DurableFrozenCandidateContractV1.from_semantic_record(record, hypothesis,
            factor_event_registry_identities=objective["factor_event_registry_identities"], research_period_identity=objective["research_period_identity"], policy_identity=objective["policy_identity"],
            source_provenance={"objective_id": self.objective_id, "batch_id": objective["batch_id"], "source": "synthetic"}, created_frozen_timestamp=CLOCK)
        return {"schema_version": EXECUTABLE_AI_DESIGN_SCHEMA_VERSION, "research_hypothesis": hypothesis["research_hypothesis"], "mechanism_family": "momentum", "candidate_design_intention": "合成结构验证", "allowed_factors": ["VOLUME_ACCEL"], "excluded_mechanisms": ["liquidity_acceleration", "AMOUNT_ACCEL+ILLIQUIDITY"], "validation_expectation": ["仅检查结构与身份"], "durable_contract": contract.to_dict(), "hypothesis": hypothesis}

    def design(self, payload=None):
        def backend(context):
            self.event("synthetic_design_call")
            generated = payload if payload is not None else self.design_input()
            generated["excluded_mechanisms"] = context["excluded_mechanisms"]
            return generated
        return ResearchEvolutionAIDesignServiceV1(self.root, backend=backend, clock=lambda: CLOCK).generate_design(self.objective_id)

    def approve(self):
        self.event("human_design_approval_attempt")
        return AIDesignApprovalServiceV1(self.root).approve(self.objective_id, "p3c-reviewer", idempotency_key="P3C_APPROVAL")

    def freeze(self):
        manager = CandidateGenerationManagerV1(self.root)
        proposal = self.proposal
        self.event("human_review_attempt")
        manager.review(proposal["proposal_id"], "approve", reviewer="p3c-reviewer", expected_proposal_hash=proposal["proposal_hash"])
        preview = manager.get_freeze_preview(proposal["proposal_id"])
        self.event("human_freeze_attempt")
        return manager.freeze(proposal["proposal_id"], {"confirmed": True, "reviewer": "p3c-reviewer", "proposal_hash": proposal["proposal_hash"], "candidate_hash": preview["candidate_hash"]})

    def confirm(self):
        directory = self.root / f"reports/research_candidates/proposals/{self.objective_id}"
        preview = json.loads((directory / "EXECUTABLE_MATERIALIZATION_PREVIEW.json").read_bytes())
        self.event("human_materialization_attempt")
        return CandidateExecutableMaterializationManagerV1(self.root).confirm(self.objective_id, self.proposal["proposal_id"], {"confirmed": True, "reviewer": "p3c-reviewer", "preview_hash": preview["preview_hash"], "idempotency_key": "P3C_MATERIALIZATION"})

    def ready(self):
        self.design()
        self.approve()
        result = self.plane.tick(self.objective_id)
        assert result["execution"]["execution_status"] == "COMPLETED", result["execution"]
        self.freeze()
        result = self.plane.tick(self.objective_id)
        assert result["execution"]["execution_status"] == "COMPLETED", result
        self.confirm()
        return self

    def structural(self, status="PASS", **kwargs):
        from chanlun_trader.research_daemon import CanonicalResearchRuntime, StructuralResult
        from chanlun_trader.research_factory.structural_entry import StructuralEntryServiceV1
        runtime = CanonicalResearchRuntime(self.root, objective_id=self.objective_id)

        def provider(candidate):
            self.event("synthetic_structural_provider", candidate_id=candidate.candidate_id)
            samples = [{"sample_id": index, "available": True} for index in range(3)]
            reference = f"reports/p3c-synthetic/{self.objective_id}.json"
            _write_json(self.root, reference, {"objective_id": self.objective_id, "candidate_id": candidate.candidate_id, "candidate_hash": candidate.candidate_hash, "samples": samples})
            count = sum(item["available"] for item in samples)
            return StructuralResult(status, "P3C_SYNTHETIC_STRUCTURE", (reference,), "P3C_STRUCTURAL_CHECKPOINT", 0,
                {"v1_result": {"candidate_id": candidate.candidate_id, "candidate_hash": candidate.candidate_hash, "lower_bound_count": count, "upper_bound_count": count, "minimum_required_count": 1, "outcome_blind": True, "performance_data_loaded": False}, "lower_bound_integrity": {"status": "PASS", "failure_codes": []}})

        runtime.structural_preflight = provider
        self.event("human_structural_attempt")
        return StructuralEntryServiceV1(self.root, runtime=runtime).start(self.objective_id, **{"confirmed": True, "candidate_id": self.proposal["durable_contract"]["candidate_id"], **kwargs})

    def authorize(self, choice="AUTHORIZE_FIRST_PREDICTIVE_TRIAL"):
        from chanlun_trader.research_factory.predictive_authorization import PredictiveGovernanceServiceV1
        service = PredictiveGovernanceServiceV1(self.root)
        readiness = service.readiness(self.objective_id)
        assert readiness["available"], readiness
        preview = service.preview(self.objective_id, choice)
        contract = self.proposal["durable_contract"]
        self.event("human_predictive_authorization_attempt")
        return service.confirm(self.objective_id, {"confirmed": True, "decision_type": choice, "candidate_id": contract["candidate_id"], "candidate_hash": contract["candidate_hash"], "authorization_id": "P3C_AUTH", "preview_hash": preview["preview_hash"], "confirmation_token": preview["confirmation_token"]})
