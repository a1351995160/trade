"""真实子进程强制退出后的研究恢复；仅使用合成数据与合成模型。"""
import os
from pathlib import Path
import subprocess
import sys

from chanlun_trader.research_factory import bounded_research_v1 as research
from chanlun_trader.research_factory.common import stable_hash


def _budget_identity(status):
    budget = status["budget"]
    return stable_hash({key: budget[key] for key in (
        "buckets", "active_reservations", "settled_reservations", "reservation_counter")})


def _worker(mode, parent_directory):
    from test_bounded_real_research_loop_v1 import FakeInvoker, make_session, no_call
    from chanlun_trader.research_factory import research_diagnostics_v1 as diagnostics

    parent = Path(parent_directory)
    root = parent / "bounded"
    if mode == "interrupt":
        session, loader, _ = make_session(parent, attempts=1)
        invoker = FakeInvoker()
        original_diagnose = diagnostics.diagnose

        def die_after_result(*args, **kwargs):
            if kwargs.get("trial_id") != "CANDIDATE_001":
                return original_diagnose(*args, **kwargs)
            result = session.path("CANDIDATE_001", "RESULT.json")
            assert result.exists()
            assert not session.path("CANDIDATE_001", "DIAGNOSTIC.json").exists()
            diagnostics.diagnose = original_diagnose
            research._put(parent / "INTERRUPTION.json", {
                "pid": os.getpid(), "candidate_result_sha256": research.file_hash(result),
                "budget_identity": _budget_identity(session.status()), "model_calls": invoker.calls,
            })
            # 故意不执行 finally，不依赖对象销毁释放锁或刷新账户状态。
            os._exit(17)

        diagnostics.diagnose = die_after_result
        session.run(loader=loader, invoker=invoker)
        raise AssertionError("必须在候选账户完成后强制退出")

    assert mode == "resume"
    from chanlun_trader.research_factory.bounded_candidate_v1 import BoundedCandidateAccountBackend
    from scripts.s1_causal_price_strategy_v1 import Causal51AccountBackend
    # 不仅不加载数据，也禁止任何账户后端再次运行。
    BoundedCandidateAccountBackend.run = no_call
    Causal51AccountBackend.run = no_call
    invoker = FakeInvoker()
    invoker.invoke = no_call
    session = research.BoundedResearchSessionV1(root)
    status = session.run(loader=no_call, invoker=invoker)
    research._put(parent / "RECOVERY.json", {
        "pid": os.getpid(), "status": status,
        "candidate_result_sha256": research.file_hash(session.path("CANDIDATE_001", "RESULT.json")),
        "budget_identity": _budget_identity(status), "model_calls": invoker.calls,
    })


def test_process_exit_after_account_resumes_without_model_account_or_budget_repeat(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    environment = {**os.environ, "PYTHONPATH": os.pathsep.join((
        str(repo / "src"), str(repo), str(repo / "tests/research_factory"),
        os.environ.get("PYTHONPATH", ""))), "PYTHONUTF8": "1"}
    command = "from test_bounded_process_recovery_v1 import _worker; import sys; _worker(sys.argv[1], sys.argv[2])"
    interrupted = subprocess.run([sys.executable, "-c", command, "interrupt", str(tmp_path)],
                                 cwd=repo, env=environment, capture_output=True, text=True,
                                 encoding="utf-8", timeout=180)
    assert interrupted.returncode == 17, interrupted.stdout + interrupted.stderr
    before = research._read(tmp_path / "INTERRUPTION.json")
    assert before["model_calls"] == 1
    assert not (tmp_path / "bounded/CANDIDATE_001/DECISION.json").exists()

    resumed = subprocess.run([sys.executable, "-c", command, "resume", str(tmp_path)],
                             cwd=repo, env=environment, capture_output=True, text=True,
                             encoding="utf-8", timeout=180)
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    after = research._read(tmp_path / "RECOVERY.json")
    assert after["pid"] != before["pid"]
    assert after["model_calls"] == 0
    assert after["candidate_result_sha256"] == before["candidate_result_sha256"]
    assert after["budget_identity"] == before["budget_identity"]
    assert after["status"]["status"] == "ATTEMPT_BUDGET_EXHAUSTED"
    assert after["status"]["qualification"] == "NOT_ASSESSED"
    objective = [row for row in after["status"]["budget"]["buckets"] if row["kind"] == "objective"]
    assert len(objective) == 1 and objective[0]["used"] == 2
    assert all(row["account_completed"] for row in after["status"]["candidates"])
    assert (tmp_path / "bounded/CANDIDATE_001/DIAGNOSTIC.json").exists()
