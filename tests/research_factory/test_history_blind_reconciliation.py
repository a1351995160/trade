"""历史投影只保留身份与消费，缺失不等于零。"""
import json
from pathlib import Path

from chanlun_trader.research_factory.history_blind_reconciliation import blind_fields, reconcile_history
from chanlun_trader.research_factory.budget import SearchBudgetRegistryV1


def test_nested_outcomes_and_free_text_are_not_returned():
    result = blind_fields({"objective_id": "O1", "status": "RESEARCH_PASSED",
        "events": [{"trial_id": "T1", "performance_accessed": True,
                    "p_value": 0.00123, "net_return": 9.987, "error_message": "profit 999"}],
        "notes": "private result", "contracts": {"C": {"candidate_id": "C", "win_rate": 0.9}}})
    assert result == {"objective_id": "O1", "events": [{"trial_id": "T1", "performance_accessed": True}],
                      "contracts": [{"candidate_id": "C"}]}


def test_real_budget_reader_no_write_and_unknown_history(tmp_path):
    path = tmp_path / "batches/B/search_budget_registry.json"
    budget = SearchBudgetRegistryV1("O", path)
    budget.register_objective(2)
    before = path.read_bytes()
    result = reconcile_history(tmp_path)
    assert path.read_bytes() == before
    assert result["global_remaining_budget"] is None
    assert result["validation_exposure_total"] is None
    assert result["budgets"][0]["buckets"][0]["remaining"] == 2
    assert result["budgets"][0]["trial_registry_present"] is False
    assert result["missing_by_batch"]


def test_missing_budget_count_not_defaulted_to_zero(tmp_path):
    path = tmp_path / "batches/B/search_budget_registry.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"objective_id": "O", "buckets": [{"kind": "objective", "key": "O", "limit": 2}]}))
    result = reconcile_history(tmp_path)
    assert result["budgets"] == []
    assert result["conflicts"][0]["code"] == "SCHEMA_OR_READER_REJECTED"
