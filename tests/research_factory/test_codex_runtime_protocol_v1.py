from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from chanlun_trader.research_factory import codex_backend
from chanlun_trader.research_factory.autonomous_orchestrator_v2 import CanonicalResearchStateReaderV2
from chanlun_trader.research_factory.budget import SearchBudgetRegistryV1
from chanlun_trader.research_factory.objective import ResearchObjectiveV1


def test_discover_codex_executable_prefers_direct_cli_over_stale_wrapper(tmp_path, monkeypatch):
    wrapper = tmp_path / "codex.cmd"
    wrapper.write_text("", encoding="utf-8")
    bundled = wrapper.parent / "node_modules" / "@openai" / "codex-win32-x64" / "vendor" / "x86_64-pc-windows-msvc" / "bin" / "codex.exe"
    bundled.parent.mkdir(parents=True)
    bundled.write_text("stale", encoding="utf-8")
    direct = tmp_path / "new" / "codex.exe"
    direct.parent.mkdir()
    direct.write_text("current", encoding="utf-8")

    monkeypatch.delenv("CODEX_EXECUTABLE", raising=False)
    monkeypatch.delenv("CODEX_CLI_PATH", raising=False)
    monkeypatch.setattr(codex_backend.shutil, "which", lambda name: {
        "codex.cmd": str(wrapper),
        "codex.exe": str(direct),
        "codex.ps1": None,
    }.get(name))

    assert codex_backend.discover_codex_executable() == str(direct)


def test_codex_command_is_noninteractive_and_staging_scoped(tmp_path):
    command = codex_backend.build_codex_command(
        "codex.exe",
        staging_dir=tmp_path,
        output_schema_path=tmp_path / "schema.json",
        response_path=tmp_path / "result.json",
    )

    assert "--ignore-user-config" in command
    assert "--approve-for-me" in command
    assert "--sandbox" not in command
    assert "--output-last-message" in command
    for feature in ("apps", "plugins", "browser_use", "computer_use", "shell_snapshot"):
        assert command[command.index("--disable", command.index(feature) - 1) + 1] == feature
    assert "--ask-for-approval" not in command
    assert "--output-schema" not in command


def test_executor_records_activity_and_scrubs_desktop_context(tmp_path, monkeypatch):
    captured = {}

    class FakeProcess:
        pid = 1234
        returncode = 0

        def communicate(self, prompt, timeout):
            captured["prompt"] = prompt
            captured["timeout"] = timeout
            return '{"type":"thread.started"}\n', ""

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(codex_backend.subprocess, "Popen", fake_popen)
    monkeypatch.setenv("CODEX_SESSION_ID", "desktop-session")
    monkeypatch.setenv("CODEX_THREAD_ID", "desktop-thread")
    monkeypatch.setenv("CODEX_APP_TOOLS_PIPE_PATH", "desktop-pipe")

    result = codex_backend.SubprocessCodexExecutorV1("codex.exe").execute(
        SimpleNamespace(prompt="probe"),
        staging_dir=tmp_path,
        output_schema_path=tmp_path / "schema.json",
        response_path=tmp_path / "result.json",
        timeout_seconds=3,
    )

    assert result.exit_code == 0
    assert result.process_id == 1234
    assert result.started_at
    assert result.process_exit_at
    assert result.last_progress_at
    assert "CODEX_SESSION_ID" not in captured["kwargs"]["env"]
    assert "CODEX_THREAD_ID" not in captured["kwargs"]["env"]
    assert "CODEX_APP_TOOLS_PIPE_PATH" not in captured["kwargs"]["env"]


def test_canonical_reader_does_not_recount_last_completed_candidate(tmp_path):
    objective_id = "OBJECTIVE_CANONICAL_REMAINING"
    objective_path = tmp_path / "data/research/research_factory/objectives" / f"{objective_id}.json"
    objective_path.parent.mkdir(parents=True)
    objective_path.write_text(json.dumps(ResearchObjectiveV1.default(objective_id).to_dict()), encoding="utf-8")

    budget_path = tmp_path / "data/research/research_factory/batches/B01/search_budget_registry.json"
    registry = SearchBudgetRegistryV1(objective_id, budget_path)
    registry.register_objective(4)
    contract_path = budget_path.parent / "durable_frozen_candidate_contracts.json"
    contract_path.write_text(json.dumps({"contracts": [{"candidate_id": "CANDIDATE_001", "candidate_hash": "HASH_001", "policy_identity": {"objective_id": objective_id}, "mechanism": "event_reversal", "factor_ids": [], "event_ids": [], "holding_period_days": 5}]}), encoding="utf-8")
    daemon_path = tmp_path / "reports/research_daemon" / objective_id / "daemon_checkpoint.json"
    daemon_path.parent.mkdir(parents=True)
    daemon_path.write_text(json.dumps({"current_state": "READY", "last_completed_candidate": {"candidate_id": "CANDIDATE_001"}, "budget_view": {"registry_path": "data/research/research_factory/batches/B01/search_budget_registry.json"}}), encoding="utf-8")
    status_path = daemon_path.parent / "daemon_status.json"
    status_path.write_text(json.dumps({"remaining_frozen_candidates": 1}), encoding="utf-8")

    snapshot = CanonicalResearchStateReaderV2(tmp_path, objective_id).snapshot()

    assert snapshot.candidates[0]["candidate_id"] == "CANDIDATE_001"
    assert snapshot.remaining_frozen_candidates == 0

    daemon_path.write_text(json.dumps({"current_state": "READY", "budget_view": {"registry_path": "data/research/research_factory/batches/B01/search_budget_registry.json"}}), encoding="utf-8")
    (daemon_path.parent / "daemon_events.jsonl").write_text(
        "\n".join(
            json.dumps({"candidate": "CANDIDATE_001", "new_state": state})
            for state in ("STRUCTURAL_PENDING", "STRUCTURAL_UNKNOWN", "CANDIDATE_COMPLETE")
        )
        + "\n",
        encoding="utf-8",
    )

    event_snapshot = CanonicalResearchStateReaderV2(tmp_path, objective_id).snapshot()

    assert event_snapshot.remaining_frozen_candidates == 0


def test_canonical_reader_ignores_unrelated_report_ledgers(tmp_path):
    objective_id = "OBJECTIVE_TRIAL_SOURCE_SCOPE"
    objective_path = tmp_path / "data/research/research_factory/objectives" / f"{objective_id}.json"
    objective_path.parent.mkdir(parents=True)
    objective_path.write_text(json.dumps(ResearchObjectiveV1.default(objective_id).to_dict()), encoding="utf-8")

    budget_path = tmp_path / "data/research/research_factory/batches/B01/search_budget_registry.json"
    registry = SearchBudgetRegistryV1(objective_id, budget_path)
    registry.register_objective(2)
    unrelated = tmp_path / "reports/archive/history/factory_trial_ledger.json"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text("not a canonical ledger for this objective", encoding="utf-8")

    snapshot = CanonicalResearchStateReaderV2(tmp_path, objective_id).snapshot()

    assert snapshot.trials == ()
