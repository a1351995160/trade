from __future__ import annotations

import json
from pathlib import Path

import pytest

from chanlun_trader.research_console import ResearchConsoleReadService
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.context import PerformanceBlindGuard
from chanlun_trader.research_factory.objective_reconciliation import AI_DESIGN_AWAITING_CONFIRMATION
from chanlun_trader.research_factory.research_evolution_ai_design import (
    AI_DESIGN_READY,
    AI_RESEARCH_DESIGN_FILENAME,
    AI_RESEARCH_DESIGN_STATE_FILENAME,
    EvolutionAIDesignInputV1,
    ResearchEvolutionAIDesignError,
    ResearchEvolutionAIDesignServiceV1,
)


OBJECTIVE_ID = "OBJECTIVE_EVOLUTION_AI_DESIGN_V1"
PARENT_OBJECTIVE_ID = "OBJECTIVE_EVOLUTION_PARENT_V1"
PROPOSAL_ID = "PROPOSAL_EVOLUTION_AI_DESIGN_V1"


def _write_json(root: Path, relative: str, payload: dict) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _fixture_root(tmp_path: Path) -> Path:
    proposal = {
        "proposal_id": PROPOSAL_ID,
        "parent_objective_id": PARENT_OBJECTIVE_ID,
        "created_objective_id": OBJECTIVE_ID,
        "objective_created": True,
        "governance_state": "OBJECTIVE_CREATION_READY",
        "state_history": ["CREATED", "HUMAN_REVIEW_REQUIRED", "APPROVED", "OBJECTIVE_CREATION_READY"],
        "failed_mechanism": "FAILED_LIQUIDITY_SIGNAL",
        "failed_mechanism_family": "liquidity_acceleration",
        "failure_summary": ["STATISTICAL_FAILURE", "RISK_FAILURE"],
        "avoid_mechanism_family": ["liquidity_acceleration"],
        "suggested_research_directions": ["event_driven", "volatility_structure"],
        "outcome_blind": True,
    }
    proposal["proposal_hash"] = stable_hash(proposal)
    _write_json(tmp_path, "reports/research_evolution/proposals/RESEARCH_EVOLUTION_PROPOSAL.json", proposal)
    _write_json(tmp_path, f"reports/research_evolution/{PARENT_OBJECTIVE_ID}/failure_landscape.json", {
        "schema_version": "research-failure-landscape-v1",
        "objective_id": PARENT_OBJECTIVE_ID,
        "entries": [{
            "candidate_id": "CAND_PARENT_FAILED",
            "candidate_family": "DAILY_CROSS_SECTIONAL",
            "mechanism": "FAILED_LIQUIDITY_SIGNAL",
            "failure_categories": ["STATISTICAL_FAILURE", "RISK_FAILURE"],
            "trial_id": "TRIAL_PARENT_FAILED",
        }],
        "category_totals": {"STATISTICAL_FAILURE": 1, "RISK_FAILURE": 1},
        "read_only": True,
    })
    _write_json(tmp_path, "reports/research_evolution/proposals/MECHANISM_COVERAGE_REGISTRY.json", {
        "schema_version": "mechanism-coverage-registry-v1",
        "coverage_id": "COVERAGE_TEST",
        "covered": ["daily_cross_sectional", "liquidity_acceleration"],
        "unexplored": ["event_driven", "volatility_structure"],
        "source_reports": ["reports/research_evolution/PARENT/research_evolution_report.json"],
        "read_only": True,
        "outcome_blind": True,
    })
    _write_json(tmp_path, "data/research/data_capability.json", {
        "generated_at": "2026-09-04T00:00:00+00:00",
        "datasets": [{
            "dataset_id": "daily_ohlcva_raw",
            "source": "fixture",
            "provider": "fixture-provider",
            "fields": ["date", "open", "high", "low", "close", "volume"],
            "frequency": "DAILY",
            "earliest_date": 20200101,
            "latest_date": 20260903,
            "event_time_semantics": "TRADE_DATE",
            "available_at_semantics": "T_CLOSE",
            "PIT_safe": True,
            "data_version": "fixture-v1",
            "status": "READY",
        }],
    })
    _write_json(tmp_path, f"data/research/research_factory/objectives/{OBJECTIVE_ID}.json", {
        "objective_id": OBJECTIVE_ID,
        "lifecycle_state": "CREATED",
        "parent_proposal_id": PROPOSAL_ID,
        "parent_proposal_hash": proposal["proposal_hash"],
        "allowed_factor_scope": ["EVENT_FLAG", "VOLATILITY_TERM"],
        "risk_constraints": {
            "pit_required": True,
            "no_lookahead": True,
            "t_plus_1": True,
            "price_limit_fail_closed": True,
            "suspension_fail_closed": True,
        },
    })
    _write_json(tmp_path, f"data/research/research_factory/lineage/{OBJECTIVE_ID}.json", {
        "schema_version": "research-proposal-objective-lineage-v1",
        "lineage_id": "LINEAGE_TEST",
        "lineage_status": "IMMUTABLE",
        "objective_id": OBJECTIVE_ID,
        "parent_objective_id": PARENT_OBJECTIVE_ID,
        "proposal_id": PROPOSAL_ID,
        "proposal_hash": proposal["proposal_hash"],
        "research_direction": "event_driven",
        "immutable": True,
        "parent_lineage": [{
            "relation": "GENERATED_FROM_PROPOSAL",
            "parent_type": "ResearchProposal",
            "parent_id": PROPOSAL_ID,
            "parent_hash": proposal["proposal_hash"],
            "immutable": True,
        }],
    })
    return tmp_path


class CountingBackend:
    backend_type = "TEST_EVOLUTION_AI"
    backend_version = "1"

    def __init__(self) -> None:
        self.calls = 0
        self.inputs: list[dict] = []

    def generate(self, payload: dict) -> dict:
        self.calls += 1
        self.inputs.append(payload)
        return {
            "research_hypothesis": "event_driven 机制可以在数据时点一致的约束下形成独立研究假设。",
            "mechanism_family": "event_driven",
            "candidate_design_intention": "定义事件驱动候选的研究设计意图，等待人工确认后再登记。",
            "allowed_factors": ["EVENT_FLAG"],
            "excluded_mechanisms": payload["excluded_mechanisms"],
            "validation_expectation": ["先做 PIT 与样本覆盖预检。", "人工确认前不创建 Candidate。"],
        }


def test_build_input_reads_required_sources_and_stays_outcome_blind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _fixture_root(tmp_path)
    performance_path = root / "reports/research_daemon" / OBJECTIVE_ID / "performance.json"
    performance_path.parent.mkdir(parents=True, exist_ok=True)
    performance_path.write_text('{"performance": 99}', encoding="utf-8")
    original_read_text = Path.read_text

    def guarded_read_text(path: Path, *args, **kwargs):
        if "performance" in str(path).casefold():
            raise AssertionError("AI design must not read performance artifacts")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read_text)
    context = ResearchEvolutionAIDesignServiceV1(root).build_input(OBJECTIVE_ID)
    payload = context.to_dict()
    assert payload["research_evolution_proposal"]["proposal_id"] == PROPOSAL_ID
    assert payload["failure_landscape"]["entries"]
    assert payload["mechanism_coverage_registry"]["covered"]
    assert payload["objective_lineage"]["lineage_id"] == "LINEAGE_TEST"
    assert payload["excluded_mechanisms"]
    assert payload["suggested_research_directions"] == ["event_driven", "volatility_structure"]
    assert payload["available_data_capabilities"]["ready_dataset_ids"] == ["daily_ohlcva_raw"]
    assert payload["performance_data_loaded"] is False
    PerformanceBlindGuard.assert_blind(payload)


def test_forbidden_ai_output_is_rejected_before_persisting_design(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    service = ResearchEvolutionAIDesignServiceV1(root)
    with pytest.raises(ResearchEvolutionAIDesignError) as error:
        service.generate_design(OBJECTIVE_ID, ai_output={
            "research_hypothesis": "invalid",
            "mechanism_family": "event_driven",
            "candidate_design_intention": "invalid",
            "allowed_factors": [],
            "excluded_mechanisms": [],
            "validation_expectation": ["invalid"],
            "performance": {"win_rate": 0.9},
        })
    assert error.value.code == "OUTCOME_FIELD_BLOCKED"
    assert not (root / "reports/research_evolution/ai_design" / OBJECTIVE_ID / AI_RESEARCH_DESIGN_FILENAME).exists()


def test_chinese_outcome_alias_is_rejected_too(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    with pytest.raises(ResearchEvolutionAIDesignError) as error:
        ResearchEvolutionAIDesignServiceV1(root).generate_design(OBJECTIVE_ID, ai_output={
            "research_hypothesis": "invalid",
            "mechanism_family": "event_driven",
            "candidate_design_intention": "invalid",
            "allowed_factors": [],
            "excluded_mechanisms": [],
            "validation_expectation": ["invalid"],
            "胜率": 0.5,
        })
    assert error.value.code == "OUTCOME_FIELD_BLOCKED"


def test_generate_stops_at_ai_design_ready_without_candidate_trial_or_budget_side_effects(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    budget_path = _write_json(root, f"data/research/research_factory/batches/{OBJECTIVE_ID}_B01/search_budget_registry.json", {
        "objective_id": OBJECTIVE_ID,
        "used": 0,
        "reserved": 0,
        "total": 4,
    })
    budget_before = budget_path.read_bytes()
    backend = CountingBackend()
    design = ResearchEvolutionAIDesignServiceV1(root, backend=backend).generate_design(OBJECTIVE_ID)
    design_dir = root / "reports/research_evolution/ai_design" / OBJECTIVE_ID
    assert design["status"] == AI_DESIGN_READY
    assert design["governance"]["next_action"] == "HUMAN_CONFIRM_AI_RESEARCH_DESIGN"
    assert design["governance"]["automatic_candidate_created"] is False
    assert design["governance"]["automatic_trial_started"] is False
    assert design["governance"]["budget_consumed"] is False
    assert backend.calls == 1
    assert (design_dir / AI_RESEARCH_DESIGN_FILENAME).exists()
    assert (design_dir / AI_RESEARCH_DESIGN_STATE_FILENAME).exists()
    assert not (root / "data/research/strategy_candidate_registry").exists()
    assert not (root / "reports/research_daemon" / OBJECTIVE_ID).exists()
    assert not (root / "reports/research_factory" / OBJECTIVE_ID).exists()
    assert budget_path.read_bytes() == budget_before
    PerformanceBlindGuard.assert_blind(design)


def test_generate_is_exactly_once_across_same_service_and_restart(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    backend = CountingBackend()
    first = ResearchEvolutionAIDesignServiceV1(root, backend=backend).generate_design(OBJECTIVE_ID)
    second = ResearchEvolutionAIDesignServiceV1(root, backend=backend).generate_design(OBJECTIVE_ID)
    assert first["design_id"] == second["design_id"]
    assert second["idempotent"] is True
    assert backend.calls == 1
    assert len(list((root / "reports/research_evolution/ai_design" / OBJECTIVE_ID).glob("AI_RESEARCH_DESIGN_PROPOSAL.json"))) == 1


def test_restart_recovers_output_written_before_state_without_second_ai_call(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    backend = CountingBackend()
    with pytest.raises(RuntimeError, match="after AI design output"):
        ResearchEvolutionAIDesignServiceV1(root, backend=backend, crash_at="after_output").generate_design(OBJECTIVE_ID)
    state_path = root / "reports/research_evolution/ai_design" / OBJECTIVE_ID / AI_RESEARCH_DESIGN_STATE_FILENAME
    assert not state_path.exists()
    recovered = ResearchEvolutionAIDesignServiceV1(root, backend=backend).recover(OBJECTIVE_ID)
    assert recovered["status"] == AI_DESIGN_READY
    assert recovered["recovered"] is True
    assert state_path.exists()
    assert backend.calls == 1


def test_lineage_and_console_view_are_objective_scoped_and_read_only(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    service = ResearchEvolutionAIDesignServiceV1(root)
    before = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
    view_before = ResearchConsoleReadService(root).get_evolution_ai_design(OBJECTIVE_ID).to_dict()
    after = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
    assert before == after
    assert view_before["status"] == "NEED_AI_RESEARCH_DESIGN"
    assert view_before["available"] is False
    design = service.generate_design(OBJECTIVE_ID)
    view_after = ResearchConsoleReadService(root).get_evolution_ai_design(OBJECTIVE_ID).to_dict()
    assert view_after["status"] == AI_DESIGN_AWAITING_CONFIRMATION
    assert view_after["approval"]["approval_status"] == "PENDING"
    assert view_after["design"]["lineage"]["objective_id"] == OBJECTIVE_ID
    assert view_after["design"]["lineage"]["parent_proposal_id"] == PROPOSAL_ID
    assert view_after["design"]["parent_proposal_hash"] == view_after["input"]["research_evolution_proposal"]["proposal_hash"]
    assert view_after["design"]["design_id"] == design["design_id"]
    assert view_after["outcome_blind"] is True
    assert view_after["performance_data_loaded"] is False
    assert view_after["outcome_fields_available"] is False
    PerformanceBlindGuard.assert_blind(view_after)


def test_input_dataclass_rejects_forbidden_field() -> None:
    with pytest.raises(ResearchEvolutionAIDesignError, match="结果字段"):
        EvolutionAIDesignInputV1(
            objective_id=OBJECTIVE_ID,
            research_evolution_proposal={"proposal_id": PROPOSAL_ID},
            failure_landscape={},
            mechanism_coverage_registry={},
            objective_lineage={},
            constraints={"performance": True},
        )


def test_web_console_exposes_objective_scoped_ai_design_read_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    root = _fixture_root(tmp_path)
    import chanlun_trader.webapp as webapp
    application = webapp.create_app(tmp_path, webapp.ExecutionPolicy("GOVERNED", "SYNTHETIC"))

    monkeypatch.setattr(application.state.services, "research_console_service", ResearchConsoleReadService(root))
    with TestClient(application) as client:
        response = client.get(f"/api/research-console/{OBJECTIVE_ID}/evolution/ai-design")
    assert response.status_code == 200
    assert response.json()["status"] == "NEED_AI_RESEARCH_DESIGN"
    assert response.json()["input"]["research_evolution_proposal"]["proposal_id"] == PROPOSAL_ID
