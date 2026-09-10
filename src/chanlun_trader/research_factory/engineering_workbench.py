"""显式装配的合成工程工作台；不读取默认研究根，不拥有研究批准权。"""
from __future__ import annotations

from pathlib import Path

from ..execution_policy import ExecutionPolicy, validate_research_root
from .common import stable_hash
from .daily_plan import DailyPlanArchiveV1, account_identity
from .mutation_boundary import ObjectiveMutationLock
from .paper_replay import PaperReplaySessionV1, read_paper_archive, _immutable
from .portfolio_plan import preview_portfolio_plan
from .source_dependencies import SOURCE_ROOT
from .strategy_admission import inspect_strategy_admission


class EngineeringWorkbenchV1:
    def __init__(self, root, sources, ledger, portfolio_policy, output_root):
        self.root = Path(root)
        self.sources = sources
        self.ledger = ledger
        self.portfolio_policy = portfolio_policy
        self.output_root = Path(output_root)
        if (not self.output_root.is_absolute() or self.output_root.resolve() != self.output_root
                or self.output_root.is_relative_to(self.root) or self.root.is_relative_to(self.output_root)
                or self.output_root.is_relative_to(SOURCE_ROOT) or SOURCE_ROOT.is_relative_to(self.output_root)):
            raise ValueError("WORKBENCH_SEPARATE_OUTPUT_REQUIRED")
        for source in sources.values():
            if not Path(source["root"]).resolve().is_relative_to(self.root.resolve()):
                raise ValueError("WORKBENCH_INPUT_ROOT_CONFLICT")

    def _replay_root(self, candidate_id):
        if not isinstance(candidate_id, str) or candidate_id not in self.sources:
            raise ValueError("WORKBENCH_CANDIDATE_UNKNOWN")
        return self.output_root / "paper" / stable_hash(candidate_id)

    def inspect(self):
        replays = {key: read_paper_archive(self._replay_root(key)) for key in self.sources}
        result = {"status": "SYNTHETIC_ENGINEERING", "source_mode": "SYNTHETIC",
            "frozen_input_count": len(self.sources), "qualified_strategy_count": 0,
            "usage_status": "WAITING_QUALIFIED_STRATEGIES", "real_execution_authorized": False,
            "real_observation_days": 0, "time_basis": "SIMULATED_TIME",
            "account": {"cash": self.ledger.cash, "reserved_cash": self.ledger.reserved_cash,
                "equity": self.ledger.current_equity(), "identity": account_identity(self.ledger)},
            "portfolio_policy": self.portfolio_policy.model_dump(mode="json"),
            "sources": [{"candidate_id": key, "contract_hash": value["contract"].content_hash,
                "input_identity": value["inputs"]["input_diagnostics"]["input_identity"],
                "sessions": value["inputs"]["exec_calendar"]} for key, value in self.sources.items()],
            "paper": replays,
            "strategy_admission": {key: inspect_strategy_admission(value) for key, value in self.sources.items()},
            "limitations": ["RESEARCH_PREVIEWS_ONLY", "INDEPENDENT_PAPER_ACCOUNTS_NOT_AGGREGATED", "NO_REAL_OBSERVATION"]}
        result["context_hash"] = stable_hash(result)
        return result

    def preview(self, plan_at):
        admission = {key: inspect_strategy_admission(value) for key, value in self.sources.items()}
        sources = {key: value for key, value in self.sources.items() if not admission[key]["preview_blocked"]}
        result = preview_portfolio_plan(self.portfolio_policy, sources, self.ledger, plan_at=plan_at)
        result["strategy_admission"] = admission
        result["plan_id"] = "PORTFOLIO_PLAN_" + stable_hash({key: value for key, value in result.items() if key != "plan_id"})
        return result

    def _confirm(self, policy, payload):
        if not policy.governance_allowed:
            raise PermissionError("EXECUTION_POLICY_READ_ONLY")
        validate_research_root(self.root, policy)
        if self.output_root.resolve() != self.output_root:
            raise ValueError("WORKBENCH_OUTPUT_ROOT_CHANGED")
        if self.output_root.exists():
            validate_research_root(self.output_root, policy)
        if payload.get("confirmed") is not True:
            raise ValueError("WORKBENCH_CONFIRMATION_REQUIRED")
        if payload.get("context_hash") != self.inspect()["context_hash"]:
            raise ValueError("WORKBENCH_CONTEXT_STALE")

    def publish(self, policy: ExecutionPolicy, payload):
        self._confirm(policy, payload)
        with ObjectiveMutationLock.for_resource(self.output_root / "workbench"):
            self._confirm(policy, payload)
            plan = self.preview(payload.get("plan_at"))
            for item in plan["source_plans"]:
                DailyPlanArchiveV1(self.output_root / "daily").publish(item)
            _immutable(self.output_root / "portfolio" / (plan["plan_id"] + ".json"), plan)
            return {"status": "ARCHIVED_RESEARCH_PREVIEWS", "plan": plan, "execution_authorized": False}

    def advance(self, policy: ExecutionPolicy, payload):
        # 这是明确合成输入的逐事件工程测试，不调用Trial或CP执行服务。
        self._confirm(policy, payload)
        candidate_id = payload.get("candidate_id")
        root = self._replay_root(candidate_id)
        with ObjectiveMutationLock.for_resource(self.output_root / "workbench"):
            self._confirm(policy, payload)
            source = self.sources[candidate_id]
            if inspect_strategy_admission(source)["preview_blocked"]:
                raise ValueError("WORKBENCH_STRATEGY_RETIRED_INVALIDATED_OR_CHANGED")
            replay = PaperReplaySessionV1(source["root"], root, source["record"], source["contract"], source["policy"], source["inputs"])
            return replay.advance(payload.get("event_count"))
