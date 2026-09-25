"""认证模式下拦截真实执行端；合成领域调用单独计数。"""
import os
import hashlib
import subprocess
from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def _record_price_only_junit_source(request):
    """把实际测试进程的源码身份写入 JUnit testsuite properties。"""
    if (os.environ.get("CHANLUN_PRICE_ONLY_TASK_SCOPE") != "1"
            or not request.config.option.xmlpath):
        return
    root = Path(__file__).resolve().parents[1]

    def git(*args):
        return subprocess.run(["git", *args], cwd=str(root), capture_output=True,
                              text=True, check=True).stdout

    head = git("rev-parse", "HEAD").strip()
    tree = git("ls-tree", "-r", "--full-tree", "HEAD", "--", "src", "scripts",
               "tests", ".github/workflows")
    changed = git("status", "--porcelain", "--", "src", "scripts", "tests",
                  ".github/workflows").strip()
    record = request.getfixturevalue("record_testsuite_property")
    record("price_only_source_head", head)
    record("price_only_code_test_tree_sha256", hashlib.sha256(tree.encode()).hexdigest())
    record("price_only_source_status",
           "UNCOMMITTED_SOURCE_CHANGES" if changed else "COMMITTED_SOURCE")


def pytest_configure(config):
    # price-only 验收任务作用域：在**组合根**显式激活（不由报告文件推断）。
    # 生产 TdxData._load_gbbq 与真实测试分类消费同一作用域，保证政策一致。
    # 激活时禁止打开真实 gbbq；退出（deactivate）后恢复原有权限，
    # 不创造真实数据授权。
    if os.environ.get("CHANLUN_PRICE_ONLY_TASK_SCOPE") == "1":
        from chanlun_trader.price_only_scope import activate_task_scope

        activate_task_scope("price_only_indicator_validation_v1")

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
