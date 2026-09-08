import json
from pathlib import Path

import pytest

from chanlun_trader.research_factory.budget import SearchBudgetRegistryV1
from chanlun_trader.research_factory.context import PerformanceBlindGuard
from chanlun_trader.research_factory.governance_execution import (
    GovernanceExecutionError,
    ResearchGovernanceExecutionServiceV1,
)


OBJECTIVE_ID = "SYNTHETIC_TERMINAL_OBJECTIVE_V1"


def _write_json(root: Path, relative: str, payload: dict) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _fixture(tmp_path: Path) -> tuple[ResearchGovernanceExecutionServiceV1, dict[str, bytes]]:
    objective = {
        "schema_version": "research-objective-v1",
        "objective_id": OBJECTIVE_ID,
        "lifecycle_state": "GOVERNANCE_DECISION_REQUIRED",
        "research_universe": ["SH", "SZ"],
        "capital_reference": 10000,
        "holding_horizon": [2, 10],
        "preferred_horizon": [5, 8],
        "mechanism_scope": ["event_continuation", "event_reversal"],
        "allowed_factor_scope": ["FACTOR_SENTIMENT", "FACTOR_EVENT"],
        "max_batches": 2,
        "max_total_trials": 12,
        "risk_constraints": {"final_test_access": "DISABLED", "real_order_execution": "DISABLED"},
        "research_priority": ["event", "sentiment"],
        "seed": 20260824,
    }
    objective_path = _write_json(tmp_path, f"data/research/research_factory/objectives/{OBJECTIVE_ID}.json", objective)
    _write_json(tmp_path, f"reports/research_orchestrator_v2/{OBJECTIVE_ID}/orchestrator_checkpoint.json", {"objective_id": OBJECTIVE_ID, "state": "GOVERNANCE_DECISION_REQUIRED", "terminal_reason": "SYNTHETIC_TERMINAL"})
    decision = {
        "schema_version": "research-governance-decision-required-v1",
        "decision_id": "GOVERNANCE_SYNTHETIC_V1",
        "objective_id": OBJECTIVE_ID,
        "allowed_choices": [{"choice": action} for action in ("STOP_RESEARCH", "START_PROMISING_FOLLOWUP_OBJECTIVE", "START_NEW_MECHANISM_OBJECTIVE")],
        "current_terminal_summary": {"budget": {"source": f"data/research/research_factory/batches/{OBJECTIVE_ID}_B01/search_budget_registry.json"}},
    }
    decision_path = _write_json(tmp_path, "reports/RESEARCH_GOVERNANCE_DECISION_REQUIRED.json", decision)
    budget_path = tmp_path / f"data/research/research_factory/batches/{OBJECTIVE_ID}_B01/search_budget_registry.json"
    budget = SearchBudgetRegistryV1(OBJECTIVE_ID, budget_path)
    budget.register_objective(12)
    budget.register_batch(f"{OBJECTIVE_ID}_B01", 12)
    budget.register_family("event_continuation", 6)
    budget.register_family("event_reversal", 6)
    effective_registry_path = _write_json(tmp_path, "reports/CURRENT_EFFECTIVE_STRATEGY_REGISTRY_V1.json", {"records": [
        {"candidate_id": "SYNTHETIC_PROMISING_A", "candidate_hash": "HASH_PROMISING_A", "family_id": "event_continuation", "current_effective_classification": "PROMISING"},
        {"candidate_id": "SYNTHETIC_PROMISING_B", "candidate_hash": "HASH_PROMISING_B", "family_id": "event_reversal", "current_effective_classification": "PROMISING"},
    ]})
    _write_json(tmp_path, "data/research/strategy_candidate_registry/registry.json", {"records": [
        {"candidate": {"candidate_id": "SYNTHETIC_PROMISING_A", "candidate_hash": "HASH_PROMISING_A", "mechanism": "event_continuation", "factor_bindings": [{"factor_id": "FACTOR_EVENT"}], "signal_logic": {"pipeline": ["event", "sentiment"]}, "selection_rule": {"type": "TOP_N", "top_n": 3}, "exit_rule": {"type": "FIXED_HOLD", "fixed_hold_rule": "5 sessions"}, "holding_period_days": 5}},
        {"candidate": {"candidate_id": "SYNTHETIC_PROMISING_B", "candidate_hash": "HASH_PROMISING_B", "mechanism": "event_reversal", "factor_bindings": [{"factor_id": "FACTOR_SENTIMENT"}], "signal_logic": {"pipeline": ["event"]}, "selection_rule": {"type": "TOP_N", "top_n": 2}, "exit_rule": {"type": "FIXED_HOLD", "fixed_hold_rule": "3 sessions"}, "holding_period_days": 3}},
    ]})
    _write_json(tmp_path, "data/research/unified_factor_registry/registry.json", {"factors": [
        {"factor_id": "FACTOR_NEW_LIQUIDITY", "family": "LIQUIDITY", "implementation_status": "EXECUTABLE", "pit_status": "PIT_VERIFIED", "a_share_compatibility": "NATIVE_COMPATIBLE"},
        {"factor_id": "FACTOR_NEW_CROSS_SECTIONAL", "family": "CROSS_SECTIONAL", "implementation_status": "EXECUTABLE", "pit_status": "PIT_VERIFIED", "a_share_compatibility": "NATIVE_COMPATIBLE"},
        {"factor_id": "FACTOR_SPEC_ONLY", "implementation_status": "SPEC_ONLY", "pit_status": "PIT_REVIEW_REQUIRED", "a_share_compatibility": "PIT_REVIEW_REQUIRED"},
    ]})
    _write_json(tmp_path, "reports/CODEX_GUIDED_PILOT_MECHANISM_COVERAGE_V1.json", {
        "review_scope": {"outcome_values_used_for_coverage": False},
        "mechanism_area_decision": {"STILL_OPEN_FOR_NEW_MECHANISMS": ["LHB/flow event mechanisms", "cross-sectional or sector-relative mechanisms"]},
    })
    tracked = {str(path): path.read_bytes() for path in (objective_path, decision_path, budget_path, effective_registry_path)}
    candidate_registry = tmp_path / "data/research/strategy_candidate_registry/registry.json"
    tracked[str(candidate_registry)] = candidate_registry.read_bytes()
    return ResearchGovernanceExecutionServiceV1(tmp_path), tracked


def _confirmation(preview: dict, *, key: str = "synthetic-confirmation") -> dict:
    payload = {
        "confirmed": True,
        "decision_id": preview["decision_id"],
        "preview_hash": preview["preview_hash"],
        "confirmation_token": preview["confirmation_token"],
        "governance_action": preview["governance_action"],
        "execution_mode": preview["execution_mode"],
        "idempotency_key": key,
    }
    if preview.get("selected_parent_candidate_id"):
        payload["selected_parent_candidate_id"] = preview["selected_parent_candidate_id"]
        payload["selected_parent_candidate_hash"] = preview["selected_parent_candidate_hash"]
    return payload


def _promising_preview(
    service: ResearchGovernanceExecutionServiceV1,
    candidate_id: str = "SYNTHETIC_PROMISING_A",
    candidate_hash: str = "HASH_PROMISING_A",
) -> dict:
    return service.preview(
        OBJECTIVE_ID,
        "START_PROMISING_FOLLOWUP_OBJECTIVE",
        selected_parent_candidate_id=candidate_id,
        selected_parent_candidate_hash=candidate_hash,
    )


def _assert_no_outcome(value: object) -> None:
    PerformanceBlindGuard.assert_blind(value)


def test_preview_is_deterministic_and_loads_runtime_promising_parents(tmp_path: Path) -> None:
    service, _ = _fixture(tmp_path)
    first = _promising_preview(service)
    second = _promising_preview(service)
    assert first["preview_hash"] == second["preview_hash"]
    assert first["proposed_new_objective_id"] == second["proposed_new_objective_id"]
    assert first["selected_parent_candidate_id"] == "SYNTHETIC_PROMISING_A"
    assert first["selected_parent_candidate_hash"] == "HASH_PROMISING_A"
    assert [item["parent_id"] for item in first["parent_lineage"] if item["parent_type"] == "Candidate"] == ["SYNTHETIC_PROMISING_A"]
    assert first["parent_candidate_identity_refs"] == [
        {"candidate_id": "SYNTHETIC_PROMISING_A", "candidate_hash": "HASH_PROMISING_A", "family_id": "event_continuation", "mechanism": "event_continuation"},
    ]
    assert set(first["parent_candidate_identity_refs"][0]) == {"candidate_id", "candidate_hash", "family_id", "mechanism"}
    assert first["parent_candidate_identity_refs"] == second["parent_candidate_identity_refs"]
    _assert_no_outcome(first["parent_candidate_identity_refs"])
    assert all(name not in json.dumps(first["parent_candidate_identity_refs"]).casefold() for name in ("return", "pnl", "profit_factor", "win_rate", "drawdown"))
    assert first["budget_proposal"]["proposed_total_predictive_budget"] == 4
    assert first["budget_proposal"]["candidate_slots"] == 1
    assert first["budget_proposal"]["candidate_generation_policy"] == "ONE_SHOT"
    assert first["budget_proposal"]["candidate_variants_allowed"] is False
    assert "上限而非必须消费" in first["budget_proposal"]["reason_zh"]
    assert "不允许据此生成 4 个候选" in first["budget_proposal"]["reason_zh"]
    assert first["budget_proposal"]["parent_budget_inherited"] is False
    assert first["candidate_eligibility"]["candidate_generation_policy"] == "ONE_SHOT"
    assert first["candidate_eligibility"]["candidate_variants_allowed"] is False
    assert first["multiple_testing_family"]["hypothesis_slots"] == 1
    assert first["mechanism_scope"] == ["event_continuation"]
    assert set(first["no_outcome_policy"]["context_preview"]["mechanism_history"][0]) == {"candidate_id", "candidate_hash", "family_id", "mechanism"}
    _assert_no_outcome(first["no_outcome_policy"]["context_preview"])
    assert all(name not in json.dumps(first["no_outcome_policy"]["context_preview"]).casefold() for name in ("return", "pnl", "profit_factor", "win_rate", "drawdown"))


def test_new_mechanism_preview_uses_open_mechanism_coverage_and_executable_pit_factors(tmp_path: Path) -> None:
    service, _ = _fixture(tmp_path)
    preview = service.preview(OBJECTIVE_ID, "START_NEW_MECHANISM_OBJECTIVE", execution_mode="CREATE_ONLY")

    assert preview["mechanism_scope"] == ["cross-sectional or sector-relative mechanisms"]
    assert preview["allowed_factor_scope"] == ["FACTOR_NEW_CROSS_SECTIONAL", "FACTOR_NEW_LIQUIDITY"]
    assert preview["no_outcome_policy"]["context_preview"]["factor_capability_summary"] == [
        {"factor_id": "FACTOR_NEW_CROSS_SECTIONAL", "available": True},
        {"factor_id": "FACTOR_NEW_LIQUIDITY", "available": True},
    ]
    assert preview["candidate_eligibility"]["candidate_generation_policy"] == "PREREGISTERED"
    assert preview["candidate_eligibility"]["candidate_variants_allowed"] is True
    _assert_no_outcome(preview["no_outcome_policy"]["context_preview"])


def test_parent_candidate_identity_refs_are_hash_pinned_and_other_actions_are_empty(tmp_path: Path) -> None:
    service, _ = _fixture(tmp_path)
    promising = _promising_preview(service)
    registry_path = tmp_path / "reports/CURRENT_EFFECTIVE_STRATEGY_REGISTRY_V1.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["records"][0]["candidate_hash"] = "HASH_PROMISING_A_CHANGED"
    registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    changed = _promising_preview(service, candidate_hash="HASH_PROMISING_A_CHANGED")
    assert changed["parent_candidate_identity_refs"][0]["candidate_hash"] == "HASH_PROMISING_A_CHANGED"
    assert changed["preview_hash"] != promising["preview_hash"]
    with pytest.raises(GovernanceExecutionError) as error:
        service.confirm(OBJECTIVE_ID, _confirmation(promising))
    assert error.value.code == "STALE_GOVERNANCE_PREVIEW"

    for action in ("STOP_RESEARCH", "START_NEW_MECHANISM_OBJECTIVE"):
        preview = service.preview(OBJECTIVE_ID, action, execution_mode="CREATE_ONLY")
        assert preview["parent_candidate_identity_refs"] == []
        assert preview["selected_parent_candidate_id"] is None
        assert preview["selected_parent_candidate_hash"] is None


def test_promising_preview_selection_fails_closed(tmp_path: Path) -> None:
    service, _ = _fixture(tmp_path)
    with pytest.raises(GovernanceExecutionError) as missing:
        service.preview(OBJECTIVE_ID, "START_PROMISING_FOLLOWUP_OBJECTIVE")
    assert missing.value.code == "PARENT_CANDIDATE_SELECTION_REQUIRED"

    with pytest.raises(GovernanceExecutionError) as unknown:
        _promising_preview(service, candidate_id="UNKNOWN", candidate_hash="HASH_UNKNOWN")
    assert unknown.value.code == "PARENT_CANDIDATE_NOT_PROMISING"

    with pytest.raises(GovernanceExecutionError) as mismatched:
        _promising_preview(service, candidate_hash="WRONG_HASH")
    assert mismatched.value.code == "PARENT_CANDIDATE_HASH_MISMATCH"


def test_different_parent_selection_changes_preview_objective_and_family_identity(tmp_path: Path) -> None:
    service, _ = _fixture(tmp_path)
    first = _promising_preview(service)
    second = _promising_preview(service, candidate_id="SYNTHETIC_PROMISING_B", candidate_hash="HASH_PROMISING_B")

    assert first["preview_hash"] != second["preview_hash"]
    assert first["proposed_new_objective_id"] != second["proposed_new_objective_id"]
    assert first["multiple_testing_family"]["family_id"] != second["multiple_testing_family"]["family_id"]
    assert first["budget_proposal"]["selected_parent_candidate"] != second["budget_proposal"]["selected_parent_candidate"]
    assert second["parent_candidate_identity_refs"] == [
        {"candidate_id": "SYNTHETIC_PROMISING_B", "candidate_hash": "HASH_PROMISING_B", "family_id": "event_reversal", "mechanism": "event_reversal"}
    ]


def test_catalog_returns_stable_safe_eligible_parent_candidates(tmp_path: Path) -> None:
    service, _ = _fixture(tmp_path)
    catalog = service.catalog(OBJECTIVE_ID)
    assert catalog["eligible_parent_candidates"] == [
        {"candidate_id": "SYNTHETIC_PROMISING_A", "candidate_hash": "HASH_PROMISING_A", "family_id": "event_continuation", "mechanism": "event_continuation"},
        {"candidate_id": "SYNTHETIC_PROMISING_B", "candidate_hash": "HASH_PROMISING_B", "family_id": "event_reversal", "mechanism": "event_reversal"},
    ]
    assert all(set(item) == {"candidate_id", "candidate_hash", "family_id", "mechanism"} for item in catalog["eligible_parent_candidates"])

    registry_path = tmp_path / "reports/CURRENT_EFFECTIVE_STRATEGY_REGISTRY_V1.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    for record in registry["records"]:
        record["current_effective_classification"] = "REJECTED"
    registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert service.catalog(OBJECTIVE_ID)["eligible_parent_candidates"] == []


def test_new_mechanism_preview_uses_performance_blind_open_scope_and_executable_factor_catalog(tmp_path: Path) -> None:
    service, _ = _fixture(tmp_path)
    _write_json(tmp_path, "reports/CODEX_GUIDED_PILOT_MECHANISM_COVERAGE_V1.json", {
        "review_scope": {"outcome_values_used_for_coverage": False},
        "mechanism_area_decision": {"STILL_OPEN_FOR_NEW_MECHANISMS": ["LHB/flow event mechanisms", "cross-sectional mechanisms"]},
    })
    _write_json(tmp_path, "data/research/unified_factor_registry/registry.json", {"factors": [
        {"factor_id": "FACTOR_EXEC_A", "family": "CROSS_SECTIONAL", "implementation_status": "EXECUTABLE", "pit_status": "PIT_VERIFIED", "a_share_compatibility": "NATIVE_COMPATIBLE"},
        {"factor_id": "FACTOR_DEFERRED", "implementation_status": "DEFERRED", "pit_status": "PIT_VERIFIED", "a_share_compatibility": "NATIVE_COMPATIBLE"},
        {"factor_id": "FACTOR_UNSAFE", "implementation_status": "EXECUTABLE", "pit_status": "PIT_REVIEW_REQUIRED", "a_share_compatibility": "NATIVE_COMPATIBLE"},
    ]})

    preview = service.preview(OBJECTIVE_ID, "START_NEW_MECHANISM_OBJECTIVE")

    assert preview["mechanism_scope"] == ["cross-sectional mechanisms"]
    assert preview["allowed_factor_scope"] == ["FACTOR_EXEC_A"]
    assert preview["proposed_new_objective_identity"]["semantic_inputs"]["allowed_factor_scope"] == ["FACTOR_EXEC_A"]
    assert preview["no_outcome_policy"]["context_preview"]["factor_capability_summary"] == [{"factor_id": "FACTOR_EXEC_A", "available": True}]
    _assert_no_outcome(preview["no_outcome_policy"]["context_preview"])


def test_objective_scoped_orchestrator_governance_artifact_is_executable(tmp_path: Path) -> None:
    service, _ = _fixture(tmp_path)
    global_path = tmp_path / "reports/RESEARCH_GOVERNANCE_DECISION_REQUIRED.json"
    objective_path = tmp_path / f"reports/research_orchestrator_v2/{OBJECTIVE_ID}/governance_decision_required.json"
    objective_path.write_bytes(global_path.read_bytes())
    global_path.unlink()

    catalog = service.catalog(OBJECTIVE_ID)

    assert {item["action"] for item in catalog["choices"]} == {
        "STOP_RESEARCH",
        "START_PROMISING_FOLLOWUP_OBJECTIVE",
        "START_NEW_MECHANISM_OBJECTIVE",
    }


def test_confirm_creates_independent_objective_and_is_exactly_once(tmp_path: Path) -> None:
    service, tracked = _fixture(tmp_path)
    preview = _promising_preview(service)
    receipt = service.confirm(OBJECTIVE_ID, _confirmation(preview))
    new_id = preview["proposed_new_objective_id"]
    assert receipt["idempotent"] is False
    assert receipt["new_objective_id"] == new_id
    assert json.loads((tmp_path / f"data/research/research_factory/objectives/{new_id}.json").read_text(encoding="utf-8"))["lifecycle_state"] == "READY"
    batch_plan = json.loads((tmp_path / f"data/research/research_factory/batches/{new_id}_B01/batch_plan.json").read_text(encoding="utf-8"))
    assert batch_plan["max_candidates"] == batch_plan["max_hypotheses"] == 1
    assert batch_plan["max_performance_trials"] == 4
    assert (tmp_path / f"data/research/research_factory/multiple_testing/{new_id}/{preview['multiple_testing_family']['family_id']}.json").exists()
    context_path = tmp_path / f"data/research/research_factory/no_outcome_context/{new_id}.json"
    context = json.loads(context_path.read_text(encoding="utf-8"))
    _assert_no_outcome(context)
    graph = json.loads((tmp_path / f"data/research/research_factory/artifact_graph/{new_id}.json").read_text(encoding="utf-8"))
    assert len(graph["edges"]) == len({(item["source_id"], item["edge_type"], item["target_id"]) for item in graph["edges"]})
    assert json.loads((tmp_path / f"reports/research_orchestrator_v2/{new_id}/activation_eligibility.json").read_text(encoding="utf-8"))["target_state"] == "NEED_AI_RESEARCH_DESIGN"
    for filename, original in tracked.items():
        assert Path(filename).read_bytes() == original
    repeated = service.confirm(OBJECTIVE_ID, _confirmation(preview, key="different-retry-key"))
    assert repeated["idempotent"] is True
    assert repeated["execution_id"] == receipt["execution_id"]
    assert len(list((tmp_path / "reports/research_governance_execution" / OBJECTIVE_ID / receipt["execution_id"]).glob("receipt.json"))) == 1


@pytest.mark.parametrize("change", ["hash", "state"])
def test_confirm_rejects_stale_selected_parent_and_keeps_unselected_candidate_unchanged(tmp_path: Path, change: str) -> None:
    service, _ = _fixture(tmp_path)
    preview = _promising_preview(service)
    registry_path = tmp_path / "reports/CURRENT_EFFECTIVE_STRATEGY_REGISTRY_V1.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    unselected_before = dict(registry["records"][1])
    if change == "hash":
        registry["records"][0]["candidate_hash"] = "HASH_PROMISING_A_STALE"
    else:
        registry["records"][0]["current_effective_classification"] = "REJECTED"
    registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(GovernanceExecutionError) as error:
        service.confirm(OBJECTIVE_ID, _confirmation(preview))
    assert error.value.code == "STALE_GOVERNANCE_PREVIEW"
    after = json.loads(registry_path.read_text(encoding="utf-8"))
    assert after["records"][1] == unselected_before


def test_stale_preview_fails_closed(tmp_path: Path) -> None:
    service, _ = _fixture(tmp_path)
    preview = service.preview(OBJECTIVE_ID, "START_NEW_MECHANISM_OBJECTIVE")
    path = tmp_path / f"data/research/research_factory/objectives/{OBJECTIVE_ID}.json"
    changed = json.loads(path.read_text(encoding="utf-8"))
    changed["mechanism_scope"].append("new_scope")
    path.write_text(json.dumps(changed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(GovernanceExecutionError) as error:
        service.confirm(OBJECTIVE_ID, _confirmation(preview))
    assert error.value.code == "STALE_GOVERNANCE_PREVIEW"
    assert str(error.value) == "研究状态已经变化，请重新确认下一轮研究方案。"


def test_stop_research_records_only_the_governance_result(tmp_path: Path) -> None:
    service, _ = _fixture(tmp_path)
    preview = service.preview(OBJECTIVE_ID, "STOP_RESEARCH", execution_mode="CREATE_ONLY")
    receipt = service.confirm(OBJECTIVE_ID, _confirmation(preview))
    assert receipt["result_state"] == "STOP_RESEARCH_RECORDED"
    assert receipt["new_objective_id"] is None
    assert list((tmp_path / "data/research/research_factory/objectives").glob("*.json")) == [tmp_path / f"data/research/research_factory/objectives/{OBJECTIVE_ID}.json"]


def test_web_governance_writes_are_localhost_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    import chanlun_trader.webapp as webapp
    application = webapp.create_app(tmp_path, webapp.ExecutionPolicy("GOVERNED", "SYNTHETIC"))

    service, _ = _fixture(tmp_path)
    monkeypatch.setattr(application.state.services, "governance_execution_service", service)
    with TestClient(application, client=("10.10.10.10", 4321)) as remote_client:
        response = remote_client.post(f"/api/research-console/{OBJECTIVE_ID}/governance/preview", json={"action": "STOP_RESEARCH", "execution_mode": "CREATE_ONLY"})
        assert response.status_code == 403
    with TestClient(application) as local_client:
        catalog = local_client.get(f"/api/research-console/{OBJECTIVE_ID}/governance/preview")
        assert catalog.status_code == 200
        assert catalog.json()["eligible_parent_candidates"][0] == {"candidate_id": "SYNTHETIC_PROMISING_A", "candidate_hash": "HASH_PROMISING_A", "family_id": "event_continuation", "mechanism": "event_continuation"}
        response = local_client.post(f"/api/research-console/{OBJECTIVE_ID}/governance/preview", json={"action": "START_PROMISING_FOLLOWUP_OBJECTIVE", "execution_mode": "CREATE_ONLY", "selected_parent_candidate_id": "SYNTHETIC_PROMISING_B", "selected_parent_candidate_hash": "HASH_PROMISING_B"})
        assert response.status_code == 200
        assert response.json()["selected_parent_candidate_id"] == "SYNTHETIC_PROMISING_B"
        query_response = local_client.get(f"/api/research-console/{OBJECTIVE_ID}/governance/preview", params={"action": "START_PROMISING_FOLLOWUP_OBJECTIVE", "execution_mode": "CREATE_ONLY", "selected_parent_candidate_id": "SYNTHETIC_PROMISING_A", "selected_parent_candidate_hash": "HASH_PROMISING_A"})
        assert query_response.status_code == 200
        assert query_response.json()["selected_parent_candidate_id"] == "SYNTHETIC_PROMISING_A"


def test_web_create_and_activate_starts_canonical_orchestrator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    import chanlun_trader.webapp as webapp
    application = webapp.create_app(tmp_path, webapp.ExecutionPolicy("GOVERNED", "SYNTHETIC", allow_process_start=True))

    service, _ = _fixture(tmp_path)
    preview = _promising_preview(service)
    calls: list[tuple[str, str]] = []

    class FakeLauncher:
        def start(self, objective_id: str, *, execution_id: str) -> dict[str, object]:
            calls.append((objective_id, execution_id))
            return {"status": "STARTED", "objective_id": objective_id, "execution_id": execution_id, "process_identity": {"pid": 1234}}

    monkeypatch.setattr(application.state.services, "governance_execution_service", service)
    monkeypatch.setattr(application.state.services, "orchestrator_process_launcher", FakeLauncher())
    with TestClient(application) as client:
        response = client.post(f"/api/research-console/{OBJECTIVE_ID}/governance/confirm", json=_confirmation(preview))

    assert response.status_code == 200
    payload = response.json()
    assert payload["orchestrator_activation"]["status"] == "STARTED"
    assert calls == [(preview["proposed_new_objective_id"], payload["execution_id"])]


def test_governance_receipt_compatibility_url_returns_canonical_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    import chanlun_trader.webapp as webapp
    application = webapp.create_app(tmp_path, webapp.ExecutionPolicy("GOVERNED", "SYNTHETIC"))

    service, _ = _fixture(tmp_path)
    preview = _promising_preview(service)
    receipt = service.confirm(OBJECTIVE_ID, _confirmation(preview))
    monkeypatch.setattr(application.state.services, "governance_execution_service", service)

    with TestClient(application) as client:
        canonical = client.get(f"/api/research-console/{OBJECTIVE_ID}/governance/execution/{receipt['execution_id']}")
        compatibility = client.get(f"/api/research-console/{OBJECTIVE_ID}/governance/execution/{receipt['execution_id']}/receipt.json")

    assert canonical.status_code == compatibility.status_code == 200
    assert compatibility.json() == canonical.json()
    assert canonical.json()["execution_id"] == receipt["execution_id"]


@pytest.mark.parametrize("crash_at", ["after_preview_confirmation", "after_objective_write", "after_budget_write", "after_lineage_write", "before_execution_receipt", "after_receipt"])
def test_crash_recovery_converges_to_one_receipt(tmp_path: Path, crash_at: str) -> None:
    service, _ = _fixture(tmp_path)
    preview = service.preview(OBJECTIVE_ID, "START_NEW_MECHANISM_OBJECTIVE")
    service.crash_at = crash_at
    with pytest.raises(RuntimeError, match="SYNTHETIC_GOVERNANCE_CRASH"):
        service.confirm(OBJECTIVE_ID, _confirmation(preview))
    recovered = ResearchGovernanceExecutionServiceV1(tmp_path).confirm(OBJECTIVE_ID, _confirmation(preview, key="recovery-key"))
    assert recovered["idempotent"] is True
    assert (tmp_path / f"data/research/research_factory/objectives/{preview['proposed_new_objective_id']}.json").exists()
    assert (tmp_path / f"reports/research_governance_execution/{OBJECTIVE_ID}/{recovered['execution_id']}/receipt.json").exists()
