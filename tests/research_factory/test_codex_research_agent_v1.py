from __future__ import annotations

import json

import pytest

from chanlun_trader.research_factory import (
    AgentBackendError,
    AgentCallAuditLedgerV1,
    AgentCallBudgetV1,
    AgentGovernancePolicyV1,
    CodexAgentCallEstimatorV1,
    CodexInvocationAdapterV1,
    CodexInvocationResultV1,
    CodexResearchAgentBackendV1,
    CodexResearchPromptV1,
    DEFAULT_RESEARCH_AGENT_BACKEND,
    NoOutcomeResearchContextV1,
    ResearchAgentBackendConfigV1,
    ResearchAgentInputBuilderV1,
    ResearchProposalBatchV1,
    TemplateResearchAgentBackendV1,
)
from chanlun_trader.research_factory.agent_backend import GovernedResearchAgentBackendV1


PROMPT_PATH = "docs/CODEX_RESEARCH_PROMPT_V1.md"


def make_input(**overrides):
    values = {
        "run_id": "RUN_CODEX_TEST",
        "batch_id": "B01",
        "objective": {"objective_id": "OBJ_CODEX_TEST", "mechanism_scope": ["event_reversal"], "holding_horizon": [2, 10]},
        "policy_identity": {"policy_id": "VALIDATION_DECISION_POLICY_V2", "policy_hash": "POLICY"},
        "no_outcome_context": NoOutcomeResearchContextV1(),
        "factor_event_catalog": ({"factor_id": "F1", "pit_status": "PIT_VERIFIED", "data_status": "AVAILABLE"}, {"event_id": "E1", "data_status": "AVAILABLE"}),
        "complexity_constraints": {"factor_count": 2, "event_count": 1, "condition_count": 4, "interaction_depth": 1, "free_parameter_count": 0},
        "proposal_budget_view": {"max_proposals": 2},
    }
    values.update(overrides)
    return ResearchAgentInputBuilderV1().build(**values)


def valid_response(agent_input, prompt, *, proposals=None):
    proposals = proposals if proposals is not None else [{
        "schema_version": "research-proposal-v1",
        "proposal_id": "P1",
        "hypothesis_id": "H1",
        "family_id": "FAMILY_A",
        "mechanism": "event_reversal",
        "economic_rationale": "Crowded event pressure may unwind when participation decays.",
        "factor_dependencies": ["F1"],
        "event_dependencies": ["E1"],
        "expected_holding_horizon": "5_8D",
        "candidate_complexity": {"factor_count": 1, "event_count": 1, "condition_count": 2, "interaction_depth": 1, "free_parameter_count": 0},
        "required_data": ["F1", "E1"],
        "novelty_claim": "The event decay mechanism is distinct from the supplied neighborhood.",
        "observable_conditions": ["E1 is present and F1 indicates decaying participation."],
        "falsification_conditions": ["The mechanism is false when the event sample is sparse or participation does not decay."],
        "provenance": {"source": "FAKE_CODEX"},
    }]
    return {
        "schema_version": "research-proposal-batch-v1",
        "run_id": agent_input.run_id,
        "batch_id": agent_input.batch_id,
        "proposals": proposals,
        "backend_type": "CODEX",
        "backend_version": "CodexResearchAgentBackendV1",
        "proposal_schema_version": "research-proposal-v1",
        "prompt_template_version": prompt.prompt_version,
        "model_id": "fake-model",
        "input_context_hash": agent_input.input_context_hash,
        "generated_at": "2026-08-24T00:00:00+08:00",
        "provenance": {"source": "FAKE_CODEX"},
        **({"empty_reason": "NO_LEGAL_RESEARCH_PROPOSAL"} if not proposals else {}),
    }


class FakeCodexExecutor:
    def __init__(self, response_factory, *, result_kwargs=None):
        self.response_factory = response_factory
        self.result_kwargs = result_kwargs or {}
        self.calls = 0
        self.staging_dirs = []

    def execute(self, request, *, staging_dir, output_schema_path, response_path, timeout_seconds):
        self.calls += 1
        self.staging_dirs.append(staging_dir)
        response = self.response_factory(request)
        return CodexInvocationResultV1(response_text=response, **self.result_kwargs)


def make_stack(tmp_path, *, response_factory=None, estimator=None, policy=None, executor=None):
    agent_input = make_input()
    prompt = CodexResearchPromptV1.from_document(PROMPT_PATH)
    executor = executor or FakeCodexExecutor(lambda request: json.dumps(valid_response(agent_input, prompt)))
    adapter = CodexInvocationAdapterV1(root=tmp_path, executor=executor)
    backend = CodexResearchAgentBackendV1(adapter=adapter, prompt=prompt, estimator=estimator or CodexAgentCallEstimatorV1(max_output_tokens=256))
    policy = policy or AgentGovernancePolicyV1(max_agent_calls=2, max_agent_tokens=5000, model_route="CODEX", retry_limit=0)
    budget = AgentCallBudgetV1(tmp_path / "budget.json", policy)
    audit = AgentCallAuditLedgerV1(tmp_path / "audit.json")
    governed = GovernedResearchAgentBackendV1(backend, policy=policy, budget=budget, audit=audit)
    return agent_input, prompt, executor, backend, governed, budget, audit


def test_valid_codex_proposal_persists_request_raw_and_parsed_artifacts(tmp_path):
    agent_input, prompt, executor, _, governed, budget, audit = make_stack(tmp_path)
    result = governed.invoke(agent_input, run_id=agent_input.run_id, batch_id=agent_input.batch_id, agent_call_id="CALL_VALID")

    assert isinstance(result, ResearchProposalBatchV1)
    assert result.backend_type == "CODEX"
    artifact_dir = executor.staging_dirs[0]
    assert (artifact_dir / "codex_request.json").exists()
    assert (artifact_dir / "codex_response_raw.txt").exists()
    assert (artifact_dir / "research_proposal_batch.json").exists()
    request = json.loads((artifact_dir / "codex_request.json").read_text(encoding="utf-8"))
    assert request["allowed_capabilities"] == ["read_sanitized_design_input", "write_temporary_model_output", "write_proposal_artifact", "write_agent_audit_artifact"]
    assert request["resource_governance_reservation"]["status"] == "RESERVED"
    assert budget.calls_completed == 1
    assert audit.records()[0]["backend"] == "CODEX"


@pytest.mark.parametrize(
    "mutate",
    (
        lambda payload: "not-json",
        lambda payload: {key: value for key, value in payload.items() if key != "proposals"},
        lambda payload: {**payload, "unknown_field": True},
        lambda payload: {**payload, "expected_return": 0.1},
        lambda payload: {**payload, "proposals": [{**payload["proposals"][0], "classification": "REJECTED"}]},
        lambda payload: {**payload, "proposals": [{**payload["proposals"][0], "recommendation": "BUY"}]},
    ),
)
def test_malformed_or_untrusted_codex_output_is_rejected(tmp_path, mutate):
    agent_input = make_input()
    prompt = CodexResearchPromptV1.from_document(PROMPT_PATH)
    valid = valid_response(agent_input, prompt)
    mutated = mutate(valid)
    raw = mutated if isinstance(mutated, str) else json.dumps(mutated)
    executor = FakeCodexExecutor(lambda request: raw)
    _, _, _, _, governed, budget, audit = make_stack(tmp_path, executor=executor)

    with pytest.raises(AgentBackendError, match="CODEX_(INVALID_RESPONSE|SCHEMA_VIOLATION)"):
        governed.invoke(agent_input, run_id="RUN_CODEX_TEST", batch_id="B01", agent_call_id="CALL_BAD")
    assert executor.calls == 1
    assert budget.calls_completed == 0
    assert audit.records()[-1]["status"] == "FAILED"


def test_outcome_leak_is_rejected_before_codex_executor(tmp_path):
    executor = FakeCodexExecutor(lambda request: "{}")
    _, _, _, _, governed, _, _ = make_stack(tmp_path, executor=executor)
    with pytest.raises(Exception):
        make_input(failure_knowledge={"nested": {"return": 1}})
    assert executor.calls == 0


def test_timeout_is_engineering_error_with_bounded_retry(tmp_path):
    agent_input = make_input()
    prompt = CodexResearchPromptV1.from_document(PROMPT_PATH)
    executor = FakeCodexExecutor(lambda request: "", result_kwargs={"timed_out": True, "exit_code": -1})
    policy = AgentGovernancePolicyV1(max_agent_calls=1, max_agent_tokens=5000, model_route="CODEX", retry_limit=1, timeout_seconds=1)
    _, _, executor, _, governed, budget, audit = make_stack(tmp_path, executor=executor, policy=policy)
    with pytest.raises(AgentBackendError, match="CODEX_TIMEOUT"):
        governed.invoke(agent_input, run_id="RUN_CODEX_TEST", batch_id="B01", agent_call_id="CALL_TIMEOUT")
    assert executor.calls == 2
    assert budget.calls_completed == 0
    assert all(record["status"] == "FAILED" for record in audit.records())


def test_nonzero_exit_is_engineering_error(tmp_path):
    executor = FakeCodexExecutor(lambda request: "", result_kwargs={"exit_code": 7, "stderr": "runtime failed"})
    _, _, executor, _, governed, budget, _ = make_stack(tmp_path, executor=executor)
    with pytest.raises(AgentBackendError, match="CODEX_NONZERO_EXIT"):
        governed.invoke(make_input(), run_id="RUN_CODEX_TEST", batch_id="B01", agent_call_id="CALL_EXIT")
    assert executor.calls == 1
    assert budget.calls_completed == 0


def test_resource_governance_blocks_before_executor(tmp_path):
    executor = FakeCodexExecutor(lambda request: "{}")
    policy = AgentGovernancePolicyV1(max_agent_calls=1, max_agent_tokens=1, model_route="CODEX", retry_limit=0)
    _, _, executor, _, governed, budget, _ = make_stack(tmp_path, executor=executor, policy=policy)
    with pytest.raises(AgentBackendError, match="AGENT_TOKEN_BUDGET_EXHAUSTED"):
        governed.invoke(make_input(), run_id="RUN_CODEX_TEST", batch_id="B01", agent_call_id="CALL_BUDGET")
    assert executor.calls == 0
    assert budget.calls_reserved == 0


def test_proposal_replay_reuses_artifact_without_second_codex_call(tmp_path):
    agent_input, _, executor, backend, governed, budget, audit = make_stack(tmp_path)
    first = governed.invoke(agent_input, run_id="RUN_CODEX_TEST", batch_id="B01", agent_call_id="CALL_REPLAY")
    second = governed.invoke(agent_input, run_id="RUN_CODEX_TEST", batch_id="B01", agent_call_id="CALL_REPLAY")
    assert first.proposal_batch_hash == second.proposal_batch_hash
    assert executor.calls == 1
    assert any(record["status"] == "REPLAYED" for record in audit.records())
    assert budget.calls_completed == 1


def test_empty_legal_batch_is_distinguished_from_backend_failure(tmp_path):
    agent_input = make_input()
    prompt = CodexResearchPromptV1.from_document(PROMPT_PATH)
    executor = FakeCodexExecutor(lambda request: json.dumps(valid_response(agent_input, prompt, proposals=[])))
    _, _, executor, _, governed, budget, _ = make_stack(tmp_path, executor=executor)
    result = governed.invoke(agent_input, run_id="RUN_CODEX_TEST", batch_id="B01", agent_call_id="CALL_EMPTY")
    assert result.proposals == ()
    assert result.empty_reason == "NO_LEGAL_RESEARCH_PROPOSAL"
    assert budget.calls_completed == 1


def test_missing_factor_is_blocked_before_factory_trials(tmp_path):
    agent_input = make_input()
    prompt = CodexResearchPromptV1.from_document(PROMPT_PATH)
    payload = valid_response(agent_input, prompt)
    payload["proposals"][0]["factor_dependencies"] = ["NOT_LEGAL"]
    executor = FakeCodexExecutor(lambda request: json.dumps(payload))
    _, _, executor, _, governed, budget, _ = make_stack(tmp_path, executor=executor)
    with pytest.raises(AgentBackendError, match="FACTOR_RESEARCH_PROPOSAL_REQUIRED"):
        governed.invoke(agent_input, run_id="RUN_CODEX_TEST", batch_id="B01", agent_call_id="CALL_FACTOR")
    assert executor.calls == 1
    assert budget.calls_completed == 0


def test_default_backend_and_activation_flag_remain_safe():
    assert DEFAULT_RESEARCH_AGENT_BACKEND == "TEMPLATE"
    assert ResearchAgentBackendConfigV1() == ResearchAgentBackendConfigV1("TEMPLATE", False)
    assert TemplateResearchAgentBackendV1.backend_type == "TEMPLATE"
    with pytest.raises(AgentBackendError, match="CODEX_RESEARCH_AGENT_DISABLED"):
        ResearchAgentBackendConfigV1("CODEX", False).assert_allowed()
