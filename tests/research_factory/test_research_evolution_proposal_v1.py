import json
from pathlib import Path

from chanlun_trader.research_factory.context import PerformanceBlindGuard
from chanlun_trader.research_factory.research_evolution_proposal import (
    HUMAN_REVIEW_REQUIRED,
    MechanismCoverageRegistryV1,
    ResearchEvolutionProposalManager,
)
from chanlun_trader.research_console import ResearchConsoleReadService


OBJECTIVE_ID = "OBJECTIVE_PROPOSAL_TEST_V1"
CANDIDATE_ID = "CANDIDATE_AMOUNT_ACCEL_TEST_V1"
TRIAL_ID = "OBJECTIVE_PROPOSAL_TEST_V1_B01_CANDIDATE_AMOUNT_ACCEL_TEST_V1_T001"


def _source_report() -> dict:
    return {
        "schema_version": "research-evolution-report-v1",
        "report_id": f"{OBJECTIVE_ID}__{CANDIDATE_ID}__{TRIAL_ID}",
        "report_hash": "failure-report-hash",
        "lineage": {
            "objective_id": OBJECTIVE_ID,
            "candidate_id": CANDIDATE_ID,
            "trial_id": TRIAL_ID,
            "candidate_contract_ref": "data/research/research_factory/candidate.json",
        },
        "candidate": {
            "candidate_family": "DAILY_CROSS_SECTIONAL",
            "mechanism": "AMOUNT_ACCEL+ILLIQUIDITY",
            "factor_ids": ["AMOUNT_ACCEL", "ILLIQUIDITY"],
        },
        "failure_analysis": {
            "primary_categories": ["STATISTICAL_FAILURE", "RISK_FAILURE"],
            "findings": [{"observed": {"net_return": -0.2, "p_value": 0.9}}],
        },
    }


def _landscape() -> dict:
    return {
        "schema_version": "research-failure-landscape-v1",
        "objective_id": OBJECTIVE_ID,
        "entries": [{
            "candidate_id": CANDIDATE_ID,
            "trial_id": TRIAL_ID,
            "candidate_family": "DAILY_CROSS_SECTIONAL",
            "mechanism": "AMOUNT_ACCEL+ILLIQUIDITY",
            "failure_categories": ["STATISTICAL_FAILURE", "RISK_FAILURE"],
        }],
    }


def _candidate_contract() -> dict:
    return {
        "candidate_id": CANDIDATE_ID,
        "factor_family": "liquidity_acceleration",
        "mechanism_identity": {"key": "AMOUNT_ACCEL+ILLIQUIDITY"},
        "research_lineage": {
            "objective_id": OBJECTIVE_ID,
            "candidate_id": CANDIDATE_ID,
            "trial_id": TRIAL_ID,
            "batch_id": f"{OBJECTIVE_ID}_B01",
        },
    }


def test_proposal_contains_bridge_fields_and_lineage(tmp_path: Path) -> None:
    proposal = ResearchEvolutionProposalManager(tmp_path).generate_proposal(
        _source_report(), _landscape(), _candidate_contract(),
    )

    assert proposal["proposal_id"]
    assert proposal["parent_objective_id"] == OBJECTIVE_ID
    assert proposal["source_failure_report"] == _source_report()["report_id"]
    assert proposal["failed_mechanism"] == "AMOUNT_ACCEL+ILLIQUIDITY"
    assert proposal["failure_summary"] == ["STATISTICAL_FAILURE", "RISK_FAILURE"]
    assert "liquidity_acceleration" in proposal["avoid_mechanism_family"]
    assert proposal["suggested_research_directions"] == ["event_driven", "volatility_structure", "capital_flow"]
    assert proposal["human_approval_required"] is True
    assert proposal["governance_state"] == HUMAN_REVIEW_REQUIRED
    assert proposal["state_history"][-1] == HUMAN_REVIEW_REQUIRED
    assert proposal["lineage"]["trial_id"] == TRIAL_ID


def test_proposal_is_outcome_blind(tmp_path: Path) -> None:
    proposal = ResearchEvolutionProposalManager(tmp_path).generate_proposal(
        _source_report(), _landscape(), _candidate_contract(),
    )

    PerformanceBlindGuard.assert_blind(proposal)
    serialized = json.dumps(proposal, ensure_ascii=False).casefold()
    for forbidden in ("net_return", "profit_factor", "win_rate", "drawdown", "trade_pnl", "p_value", "adjusted_p"):
        assert forbidden not in serialized
    assert proposal["outcome_blind"] is True


def test_duplicate_mechanism_variant_is_not_suggested(tmp_path: Path) -> None:
    coverage = {
        "covered": ["liquidity_acceleration"],
        "unexplored": ["AMOUNT_ACCEL_VARIANT", "liquidity_acceleration_fast", "volatility_structure", "capital_flow"],
    }
    proposal = ResearchEvolutionProposalManager(tmp_path).generate_proposal(
        _source_report(), _landscape(), _candidate_contract(), coverage,
    )

    assert "AMOUNT_ACCEL_VARIANT" not in proposal["suggested_research_directions"]
    assert "liquidity_acceleration_fast" not in proposal["suggested_research_directions"]
    assert all("amount_accel" not in direction and "liquidity_acceleration" not in direction for direction in proposal["suggested_research_directions"])


def test_governance_boundary_writes_only_derived_proposal_artifacts(tmp_path: Path) -> None:
    proposal = ResearchEvolutionProposalManager(tmp_path).generate_proposal(
        _source_report(), _landscape(), _candidate_contract(),
    )

    assert proposal["governance"] == {
        "automatic_candidate_created": False,
        "automatic_objective_created": False,
        "automatic_trial_started": False,
        "budget_consumed": False,
        "codex_called": False,
        "current_state": HUMAN_REVIEW_REQUIRED,
        "human_approval_required": True,
        "next_action": "HUMAN_REVIEW",
        "next_allowed_states": ["APPROVED", "CREATE_NEW_OBJECTIVE"],
        "stops_before_new_objective": True,
    }
    assert not (tmp_path / "data/research/research_factory/objectives").exists()
    assert not (tmp_path / "data/research/research_factory/candidates").exists()
    assert not (tmp_path / "reports/research_daemon").exists()
    assert all(path.is_relative_to(tmp_path / "reports/research_evolution/proposals") for path in (tmp_path / "reports/research_evolution/proposals").rglob("*"))


def test_registry_is_outcome_blind_and_idempotent(tmp_path: Path) -> None:
    manager = ResearchEvolutionProposalManager(tmp_path)
    first = manager.generate_proposal(_source_report(), _landscape(), _candidate_contract())
    proposal_path = tmp_path / "reports/research_evolution/proposals/RESEARCH_EVOLUTION_PROPOSAL.json"
    coverage_path = tmp_path / "reports/research_evolution/proposals/MECHANISM_COVERAGE_REGISTRY.json"
    before = {path: path.read_bytes() for path in (proposal_path, coverage_path)}

    second = manager.generate_proposal(_source_report(), _landscape(), _candidate_contract())

    assert second == first
    assert {path: path.read_bytes() for path in (proposal_path, coverage_path)} == before
    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    PerformanceBlindGuard.assert_blind(coverage)
    assert "liquidity_acceleration" in coverage["covered"]
    assert len(MechanismCoverageRegistryV1(tmp_path).load()["source_reports"]) == 1


def test_console_reads_generated_proposal_without_generating_or_approving(tmp_path: Path) -> None:
    objective_path = tmp_path / "data/research/research_factory/objectives" / f"{OBJECTIVE_ID}.json"
    objective_path.parent.mkdir(parents=True, exist_ok=True)
    objective_path.write_text(json.dumps({"objective_id": OBJECTIVE_ID}) + "\n", encoding="utf-8")
    ResearchEvolutionProposalManager(tmp_path).generate_proposal(_source_report(), _landscape(), _candidate_contract())

    view = ResearchConsoleReadService(tmp_path).get_evolution_proposals(OBJECTIVE_ID)

    payload = view.to_dict()
    assert payload["available"] is True
    assert payload["proposal"]["governance_state"] == HUMAN_REVIEW_REQUIRED
    assert payload["proposal"]["human_approval_required"] is True
    assert payload["coverage"]["covered"] == ["liquidity_acceleration"]
    assert not (tmp_path / "data/research/research_factory/candidates").exists()
    assert not (tmp_path / "reports/research_daemon").exists()
