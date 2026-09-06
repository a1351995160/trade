from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import os
import subprocess
import sys

import pytest

from chanlun_trader.research_factory.candidate_generation import CandidateGenerationError, CandidateGenerationManagerV1
from chanlun_trader.research_factory.context import PerformanceBlindGuard, PerformanceLeakError
from chanlun_trader.research_factory.safe_runtime_context import (
    BUDGET_AUTHORITY_AMBIGUOUS,
    SAFE_RUNTIME_CONTEXT_VERSION,
    STALE_RUNTIME_CONTEXT,
    SafeRuntimeContextBuilderV1,
    SafeRuntimeContextError,
    SafeRuntimeContextV1,
)
from tests.research_factory.test_candidate_generation_governance_v1 import _prepare as prepare_candidate_fixture
from tests.research_factory.test_research_evolution_ai_design_v1 import OBJECTIVE_ID, _fixture_root


FIXED_CLOCK = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


def _write_json(root: Path, relative: str, payload: dict) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _builder(root: Path) -> SafeRuntimeContextBuilderV1:
    return SafeRuntimeContextBuilderV1(root, clock=lambda: FIXED_CLOCK)


def test_t01_context_is_objective_scoped_outcome_blind_and_read_only(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    before = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))

    context = _builder(root).build(OBJECTIVE_ID)

    after = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
    assert before == after
    assert context.get("context_version") == SAFE_RUNTIME_CONTEXT_VERSION
    assert context.get("read_only") is True
    assert context.get("outcome_blind") is True
    assert context.get("performance_data_loaded") is False
    assert context["objective"]["objective_id"] == OBJECTIVE_ID
    assert context["candidate"]["candidates"] == []
    assert context["trial"]["trials"] == []
    PerformanceBlindGuard.assert_blind(context.to_dict())


def test_t02_same_frozen_canonical_facts_have_same_identity_and_hash(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    first = _builder(root).build(OBJECTIVE_ID)
    second = _builder(root).build(OBJECTIVE_ID)

    assert first.context_id == second.context_id
    assert first.context_hash == second.context_hash
    assert first.to_dict() == second.to_dict()


def test_t03_trial_performance_accessed_is_explicitly_allowed_but_values_are_blocked() -> None:
    safe = {
        "identity": {},
        "objective": {},
        "trial": {"trials": [{"trial_id": "TRIAL_1", "performance_accessed": True}]},
        "outcome_blind": True,
        "performance_data_loaded": False,
    }
    SafeRuntimeContextV1.from_dict(safe)
    with pytest.raises(SafeRuntimeContextError) as error:
        SafeRuntimeContextV1.from_dict({**safe, "trial": {"trials": [{"return": 0.2}]}})
    assert error.value.code == "OUTCOME_FIELD_BLOCKED"


def test_t04_ambiguous_budget_authority_fails_closed(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    for batch_id in ("B01", "B02"):
        _write_json(root, f"data/research/research_factory/batches/{OBJECTIVE_ID}_{batch_id}/search_budget_registry.json", {
            "objective_id": OBJECTIVE_ID,
            "used": 0,
            "reserved": 0,
            "total": 4,
        })

    with pytest.raises(SafeRuntimeContextError) as error:
        _builder(root).build(OBJECTIVE_ID)
    assert error.value.code == BUDGET_AUTHORITY_AMBIGUOUS


def test_t05_data_manifest_change_makes_context_stale(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    context = _builder(root).build(OBJECTIVE_ID)
    data_path = root / "data/research/data_capability.json"
    payload = json.loads(data_path.read_text(encoding="utf-8"))
    payload["datasets"][0]["data_version"] = "fixture-v2"
    _write_json(root, "data/research/data_capability.json", payload)

    with pytest.raises(SafeRuntimeContextError) as error:
        _builder(root).validate_context_freshness(context)
    assert error.value.code == STALE_RUNTIME_CONTEXT
    assert "data_manifest_hash" in error.value.details["changed_components"]


def test_t06_budget_change_makes_context_stale(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    budget_path = _write_json(root, f"data/research/research_factory/batches/{OBJECTIVE_ID}_B01/search_budget_registry.json", {
        "objective_id": OBJECTIVE_ID,
        "used": 0,
        "reserved": 0,
        "total": 4,
    })
    context = _builder(root).build(OBJECTIVE_ID)
    payload = json.loads(budget_path.read_text(encoding="utf-8"))
    payload["used"] = 1
    _write_json(root, budget_path.relative_to(root).as_posix(), payload)

    with pytest.raises(SafeRuntimeContextError) as error:
        _builder(root).validate_context_freshness(context)
    assert error.value.code == STALE_RUNTIME_CONTEXT
    assert "budget_hash" in error.value.details["changed_components"]


def test_t07_candidate_proposal_binds_safe_context_and_rejects_stale_context(tmp_path: Path) -> None:
    root = prepare_candidate_fixture(tmp_path)
    manager = CandidateGenerationManagerV1(root, clock=lambda: FIXED_CLOCK.isoformat())
    proposal = manager.generate_proposal("OBJECTIVE_CANDIDATE_GENERATION_GOVERNANCE_V1")

    assert proposal["source_context_id"].startswith("SAFE_RUNTIME_CONTEXT_")
    assert proposal["source_context_hash"] == proposal["input_context_hash"]
    assert proposal["source_budget_authority_status"] == "UNIQUE_CANONICAL"
    PerformanceBlindGuard.assert_blind(proposal)
    data_path = root / "data/research/data_capability.json"
    payload = json.loads(data_path.read_text(encoding="utf-8"))
    payload["datasets"][0]["data_version"] = "fixture-stale"
    _write_json(root, "data/research/data_capability.json", payload)

    with pytest.raises(CandidateGenerationError) as error:
        manager.get_proposal(proposal["proposal_id"])
    assert error.value.code == STALE_RUNTIME_CONTEXT


def test_t08_cli_returns_machine_json_without_invoking_ai(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path.cwd() / "src")
    completed = subprocess.run(
        [sys.executable, "-m", "chanlun_trader.research_factory.safe_runtime_context", "--root", str(root), "--objective-id", OBJECTIVE_ID, "--json"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    payload = json.loads(completed.stdout)
    assert payload["context_version"] == SAFE_RUNTIME_CONTEXT_VERSION
    assert payload["objective"]["objective_id"] == OBJECTIVE_ID
    assert payload["performance_data_loaded"] is False


def test_t09_console_exposes_safe_context_as_read_model(tmp_path: Path) -> None:
    from chanlun_trader.research_console import ResearchConsoleReadService

    root = _fixture_root(tmp_path)
    payload = ResearchConsoleReadService(root, clock=lambda: FIXED_CLOCK).get_safe_runtime_context(OBJECTIVE_ID)

    assert payload["identity"]["context_hash"]
    assert payload["objective"]["objective_id"] == OBJECTIVE_ID
    assert payload["read_only"] is True
    PerformanceBlindGuard.assert_blind(payload)


def test_t10_materialization_gate_reuses_candidate_safe_context(tmp_path: Path) -> None:
    root = prepare_candidate_fixture(tmp_path)
    manager = CandidateGenerationManagerV1(root, clock=lambda: FIXED_CLOCK.isoformat())
    proposal = manager.generate_proposal("OBJECTIVE_CANDIDATE_GENERATION_GOVERNANCE_V1")
    input_payload = json.loads((root / "reports/research_candidates/proposals/OBJECTIVE_CANDIDATE_GENERATION_GOVERNANCE_V1/CANDIDATE_PROPOSAL_INPUT.json").read_text(encoding="utf-8"))

    assert input_payload["source_context_id"] == proposal["source_context_id"]
    assert input_payload["source_context_hash"] == proposal["source_context_hash"]
    assert input_payload["runtime_budget"]["authority_status"] == "UNIQUE_CANONICAL"


def test_t11_canonical_conflict_blocks_context_before_projection_or_capability_reads(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    _write_json(root, f"data/research/research_factory/candidates/{OBJECTIVE_ID}/CANDIDATE_REGISTRY.json", {
        "objective_id": OBJECTIVE_ID,
        "candidates": [{"candidate_id": "C1", "candidate_hash": "H1"}],
    })
    _write_json(root, "data/research/research_factory/batches/B01/durable_frozen_candidate_contracts.json", {
        "contracts": [{
            "candidate_id": "C1",
            "candidate_hash": "H2",
            "policy_identity": {"objective_id": OBJECTIVE_ID},
        }],
    })

    with pytest.raises(SafeRuntimeContextError) as error:
        _builder(root).build(OBJECTIVE_ID)
    assert error.value.code == "CONTEXT_BUILD_BLOCKED"


def test_t12_projection_drift_is_health_metadata_and_canonical_budget_wins(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    _write_json(root, f"data/research/research_factory/batches/{OBJECTIVE_ID}_B01/search_budget_registry.json", {
        "objective_id": OBJECTIVE_ID,
        "total": 4,
        "used": 2,
        "reserved": 0,
        "remaining": 2,
    })
    _write_json(root, f"reports/research_daemon/{OBJECTIVE_ID}/daemon_checkpoint.json", {
        "objective_id": OBJECTIVE_ID,
        "current_state": "READY",
        "budget_view": {"objective_id": OBJECTIVE_ID, "total": 4, "used": 0, "reserved": 0, "remaining": 4},
    })

    context = _builder(root).build(OBJECTIVE_ID)

    assert context["budget"]["used"] == 2
    assert context["runtime_health"]["runtime_projection"] is True
    assert context["runtime_health"]["projection_drift_detected"] is True


def test_t13_objective_effective_state_change_makes_context_stale(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    context = _builder(root).build(OBJECTIVE_ID)
    _write_json(root, f"reports/research_evolution/ai_design/{OBJECTIVE_ID}/AI_RESEARCH_DESIGN_PROPOSAL.json", {
        "objective_id": OBJECTIVE_ID,
        "design_id": "D1",
        "design_hash": "DH1",
        "status": "AI_DESIGN_READY",
    })
    _write_json(root, f"reports/research_evolution/ai_design/{OBJECTIVE_ID}/AI_RESEARCH_DESIGN_STATE.json", {
        "objective_id": OBJECTIVE_ID,
        "design_id": "D1",
        "design_hash": "DH1",
        "status": "AI_DESIGN_READY",
        "requires_human_confirmation": True,
    })

    with pytest.raises(SafeRuntimeContextError) as error:
        _builder(root).validate_context_freshness(context)
    assert error.value.code == STALE_RUNTIME_CONTEXT
    assert "effective_state_hash" in error.value.details["changed_components"]
