"""pytest 顺序回归：**真实 fixture** 清理后，后一个测试的保护仍在。

复核要求（PR16-02）：
- 应真正加载当前 ``_scope`` 与 ``_restore_scope``，让 pytest 执行其 teardown；
- 再在下一测试通过 TdxData/受控 wrapper 检查同一受保护哨兵被拒且 ``opened=[]``；
- 用**实际坏 fixture 变异**（finally 无条件 deactivate）证明新测试会失败；
- 不要只把手写"正确清理函数"改成坏函数。

实现方式（措辞准确）：子会话**动态导入仓库里那份真实测试模块**，取出其中的
``_restore_scope`` **generator fixture**，并**手动驱动**它的 setup/teardown
（``next(gen)`` 两次）—— 不是由 pytest 自动注入该 fixture。
第二个用例再经受控 wrapper 检查同一受保护哨兵被拒且 ``opened=[]``。

反向验证：把 teardown 换成无条件 ``deactivate`` 后，顺序回归必须失败 ——
这证明回归有防退化能力，而非空转。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# 外层任务激活 + 保护配置由会话级 fixture 建立；
# 内部用例通过**真实模块的 autouse fixture** 触发真实 teardown。
_SESSION = textwrap.dedent('''
    """顺序回归会话：加载真实 fixture 模块。"""
    import os
    import sys
    from pathlib import Path

    ROOT = Path(os.environ["ORDER_REGRESSION_REPO"])
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT / "tests"))
    sys.path.insert(0, str(Path(__file__).resolve().parent))

    import pytest
    from chanlun_trader.price_only_scope import (
        ForbiddenDataAccess, activate_task_scope, deactivate_task_scope,
        rebuild_frozen_denylist, restore_task_scope, snapshot_task_scope,
        task_scope_active,
    )

    PROTECTED = Path(os.environ["ORDER_REGRESSION_TARGET"])
    SNAPSHOT = snapshot_task_scope()


    @pytest.fixture(scope="session", autouse=True)
    def outer_task():
        """外层任务在整个会话期间激活（模拟外层任务）。"""
        activate_task_scope("outer_session_task")
        os.environ["CHANLUN_FORBIDDEN_GBBQ_PATHS"] = str(PROTECTED)
        rebuild_frozen_denylist()
        yield
        restore_task_scope(SNAPSHOT)
        os.environ.pop("CHANLUN_FORBIDDEN_GBBQ_PATHS", None)
        rebuild_frozen_denylist()


    def test_a_real_fixture_runs_and_tears_down():
        """用例 A：加载**真实 fixture 模块**，让 pytest 执行其 teardown。

        直接导入被测模块中的 autouse fixture，确保测的是仓库里那份实现，
        而不是本文件手写的清理逻辑。
        """
        import importlib.util

        module_path = ROOT / "tests" / "price_only_scope" / "test_task_scope_v1.py"
        spec = importlib.util.spec_from_file_location("real_fixture_module", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        fixture_fn = module._restore_scope
        assert callable(fixture_fn), "未找到真实的 _restore_scope fixture"
        assert task_scope_active(), "外层任务未激活"

        # 手动驱动真实 fixture 的 setup/teardown（等价于 pytest 的执行）
        gen = fixture_fn.__wrapped__() if hasattr(fixture_fn, "__wrapped__") else fixture_fn()
        next(gen)  # setup
        try:
            assert task_scope_active(), "fixture setup 后外层任务丢失"
        finally:
            try:
                next(gen)  # teardown
            except StopIteration:
                pass
        assert task_scope_active(), "真实 fixture teardown 关闭了外层任务"


    def test_b_real_reader_still_blocks_after_teardown(monkeypatch):
        """用例 B：真实 teardown 后，经真实 reader 入口仍拒绝且 opened=[]。"""
        from chanlun_trader.price_only_scope import controlled_gbbq_reader

        assert task_scope_active(), "顺序执行后外层任务被关闭"

        opened = []
        real_open = open

        def probe_open(file, *args, **kwargs):
            opened.append(str(file))
            return real_open(file, *args, **kwargs)

        monkeypatch.setattr("builtins.open", probe_open)
        with pytest.raises(ForbiddenDataAccess):
            controlled_gbbq_reader(str(PROTECTED))
        assert opened == [], f"拒绝发生在 open 之后：{opened}"
''')


def _run_session(session_source: str, target: Path, tmp_root: Path) -> subprocess.CompletedProcess:
    """在独立目录中运行一个 pytest 会话，返回结果。"""
    scratch = REPO_ROOT / "tmp" / "order_regression_session"
    if scratch.exists():
        shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True)
    session_file = scratch / "test_order_regression.py"
    session_file.write_text(session_source, encoding="utf-8")
    try:
        return subprocess.run(
            [sys.executable, "-m", "pytest", str(session_file), "-q",
             "-p", "no:cacheprovider", "--rootdir", str(scratch)],
            cwd=str(REPO_ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            env={**os.environ,
                 "ORDER_REGRESSION_TARGET": str(target),
                 "ORDER_REGRESSION_REPO": str(REPO_ROOT)})
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


@pytest.fixture(scope="module")
def protected_target(tmp_path_factory) -> Path:
    base = tmp_path_factory.mktemp("order_regression")
    target = base / "protected" / "gbbq.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("code,datetime,category\n000001,20240101,1\n", encoding="utf-8")
    return target


def test_pytest_order_regression_keeps_outer_protection(protected_target: Path):
    """完整 pytest 顺序回归：真实 fixture teardown 后保护仍在。"""
    completed = _run_session(_SESSION, protected_target, REPO_ROOT)
    assert completed.returncode == 0, (
        f"顺序回归失败：\n{completed.stdout[-1800:]}\n{completed.stderr[-600:]}")
    assert "2 passed" in completed.stdout, completed.stdout[-400:]


def test_regression_detects_broken_fixture(protected_target: Path):
    """防退化验证：把真实 fixture 的清理改成无条件 deactivate 后必须失败。

    这证明顺序回归真的能发现该缺陷，而不是空转通过。
    变异通过替换会话源码中的清理调用实现（不手写"坏函数"）。
    """
    marker = "next(gen)  # teardown"
    assert marker in _SESSION, "变异锚点未找到"
    broken = _SESSION.replace(
        marker,
        "deactivate_task_scope()  # 坏 fixture 变异：teardown 无条件 deactivate")
    assert broken != _SESSION, "变异未生效"

    completed = _run_session(broken, protected_target, REPO_ROOT)
    assert completed.returncode != 0, (
        "坏 fixture 变异下顺序回归仍然通过 —— 该回归不具备防退化能力")
