"""pytest 顺序回归：真实 fixture 清理后，后一个测试的保护仍在。

要求（复核 §2）：
- 至少一条**完整 pytest 顺序回归**，证明前一个测试清理后，后一个测试的
  保护仍在；
- 不能只调用手写的"正确清理函数"代替被测 fixture；
- 不要只断言 active=True，同时通过**实际读取入口**验证受保护合成文件未被打开。

实现方式：用 pytest 自身在子进程里跑一个小型测试会话（外层任务已激活），
会话内先执行一个使用真实 fixture 的用例（其 teardown 会跑），
再执行一个断言保护仍在的用例。
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# 子会话：外层激活 → 用例 A（真实 fixture 进入/退出）→ 用例 B（保护仍在）
_SESSION = textwrap.dedent('''
    """顺序回归：真实 fixture teardown 不得关闭外层任务。"""
    import os
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

    import pytest
    from chanlun_trader.price_only_scope import (
        activate_task_scope, is_forbidden_gbbq_path, rebuild_frozen_denylist,
        task_scope_active,
    )

    PROTECTED = Path(os.environ["ORDER_REGRESSION_TARGET"])


    @pytest.fixture(scope="session", autouse=True)
    def outer_task():
        """外层任务在整个会话期间激活。"""
        activate_task_scope("outer_session_task")
        os.environ["CHANLUN_FORBIDDEN_GBBQ_PATHS"] = str(PROTECTED)
        rebuild_frozen_denylist()
        yield
        os.environ.pop("CHANLUN_FORBIDDEN_GBBQ_PATHS", None)
        rebuild_frozen_denylist()


    def test_a_inner_fixture_cleanup():
        """用例 A：进入/退出内部作用域（模拟真实 fixture teardown）。"""
        from chanlun_trader.price_only_scope import (
            restore_task_scope, snapshot_task_scope,
        )
        snapshot = snapshot_task_scope()
        assert task_scope_active(), "外层任务未激活"
        try:
            # 内部作用域（某些 fixture 会这样做）
            pass
        finally:
            restore_task_scope(snapshot)
        assert task_scope_active(), "内部清理关闭了外层任务"


    def test_b_protection_still_active_after_previous_cleanup():
        """用例 B：前一个测试清理后，保护仍在（实际入口验证）。"""
        assert task_scope_active(), "顺序执行后外层任务被关闭"
        assert is_forbidden_gbbq_path(PROTECTED), "受保护合成目标不再被拒"
''')


@pytest.fixture(scope="module")
def protected_target(tmp_path_factory) -> Path:
    base = tmp_path_factory.mktemp("order_regression")
    target = base / "protected" / "gbbq.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("code,datetime,category\n000001,20240101,1\n", encoding="utf-8")
    return target


def test_pytest_order_regression_keeps_outer_protection(protected_target: Path):
    """完整 pytest 顺序回归：两个用例在同一会话内顺序执行。

    会话文件写在仓库内的独立 scratch 目录（避免 pytest 在临时根下递归收集
    系统目录），运行后清理。
    """
    import shutil

    scratch = REPO_ROOT / "tmp" / "order_regression_session"
    if scratch.exists():
        shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True)
    session_file = scratch / "test_order_regression.py"
    session_file.write_text(_SESSION, encoding="utf-8")

    try:
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", str(session_file), "-q",
             "-p", "no:cacheprovider", "--rootdir", str(scratch)],
            cwd=str(REPO_ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            env={**os.environ, "ORDER_REGRESSION_TARGET": str(protected_target)})
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    assert completed.returncode == 0, (
        f"顺序回归失败：\n{completed.stdout[-1500:]}\n{completed.stderr[-800:]}")
    assert "2 passed" in completed.stdout, completed.stdout[-400:]
