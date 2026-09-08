"""认证模式下拦截真实执行端；合成领域调用单独计数。"""
import os

import pytest


def pytest_configure(config):
    if os.environ.get("CHANLUN_TEST_ISOLATION") != "1":
        return
    from chanlun_trader.research_factory.predictive_executor import CanonicalPredictiveExecutorV1
    from chanlun_trader.research_factory.real_sample_feasibility import RealSampleFeasibilityProviderV1
    from chanlun_trader.research_factory.autonomous_orchestrator_v2 import CodexExecBatchInvokerV2
    from chanlun_trader.research_factory.research_evolution_ai_design import TemplateEvolutionAIDesignBackendV1
    from chanlun_trader.research_factory.ai_design_approval import AIDesignApprovalServiceV1

    counts = {"forbidden_predictive": 0, "forbidden_structural": 0, "forbidden_ai": 0, "synthetic_template_calls": 0, "governance_approval_attempts": 0, "governance_confirmation_attempts": 0}
    patch = pytest.MonkeyPatch()

    def wrap(cls, method, counter, forbidden=False):
        original = getattr(cls, method)

        def counted(*args, **kwargs):
            counts[counter] += 1
            if forbidden:
                raise AssertionError(counter)
            return original(*args, **kwargs)

        patch.setattr(cls, method, counted)

    wrap(CanonicalPredictiveExecutorV1, "execute", "forbidden_predictive", True)
    wrap(RealSampleFeasibilityProviderV1, "build", "forbidden_structural", True)
    wrap(CodexExecBatchInvokerV2, "invoke", "forbidden_ai", True)
    wrap(TemplateEvolutionAIDesignBackendV1, "generate", "synthetic_template_calls")
    wrap(AIDesignApprovalServiceV1, "approve", "governance_approval_attempts")
    wrap(AIDesignApprovalServiceV1, "confirm", "governance_confirmation_attempts")
    config._p3a_probes = counts, patch


def pytest_sessionfinish(session, exitstatus):
    probes = getattr(session.config, "_p3a_probes", None)
    if probes is None:
        return
    counts, patch = probes
    patch.undo()
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    reporter.write_line("P3A_EXECUTOR_PROBES=" + str(counts))
    if any(value for key, value in counts.items() if key.startswith("forbidden_")):
        session.exitstatus = 1
