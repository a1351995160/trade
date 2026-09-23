"""防止项目调用点绕过受控 reader 的实际检查。

要求（复核 §3）：若裸 vendor 调用不属于受控能力，必须明确标为不支持，
在**本任务调用点禁止使用**，并提供防止项目调用点绕过的实际检查。

本模块静态扫描项目源码中的 `GbbqReader` 直接调用点：
- 唯一允许的直接调用位于受控 wrapper 内部（`price_only_scope.py`）；
- 其它任何 `GbbqReader().get_df(...)` 直接调用都会使本测试失败。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "chanlun_trader"

# 允许出现受控 vendor 调用的文件（wrapper 自身）
ALLOWED_VENDOR_FILES = {"price_only_scope.py"}


def _vendor_call_sites() -> list[tuple[str, int]]:
    """返回 (文件, 行号) 列表：直接调用 GbbqReader 的位置。"""
    sites: list[tuple[str, int]] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            # 形如 GbbqReader().get_df(...) 或 X.get_df(...) 且 X 由 GbbqReader 构造
            if isinstance(func, ast.Attribute) and func.attr == "get_df":
                value = func.value
                if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) \
                        and value.func.id == "GbbqReader":
                    sites.append((path.name, node.lineno))
            # 形如 reader.get_df(...)，其中 reader 来自 GbbqReader()
            if isinstance(func, ast.Attribute) and func.attr == "get_df":
                value = func.value
                if isinstance(value, ast.Name):
                    sites.append((path.name, node.lineno))
    return sites


def test_only_controlled_wrapper_calls_vendor():
    """除受控 wrapper 外，项目源码不得有直接 vendor 调用点。"""
    sites = _vendor_call_sites()
    offenders = [s for s in sites if s[0] not in ALLOWED_VENDOR_FILES]
    assert not offenders, (
        "发现未经受控 wrapper 的直接 vendor 调用点："
        f"{offenders}；应改用 controlled_gbbq_reader 或接入同一政策")


def test_tdx_data_does_not_call_vendor_directly():
    """tdx_data.py 必须经受控政策，不得裸调 vendor。"""
    path = SRC_ROOT / "tdx_data.py"
    source = path.read_text(encoding="utf-8")
    # 允许 import 与守卫调用；不允许 GbbqReader().get_df(...) 直接出现
    assert "GbbqReader().get_df(" not in source, \
        "tdx_data.py 仍直接调用 vendor，未经受控政策"
    assert "guard_gbbq_path" in source or "controlled_gbbq_reader" in source, \
        "tdx_data.py 未接入受控政策"


def test_controlled_reader_policy_is_declared():
    """受控/裸调用的政策必须显式声明。"""
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from chanlun_trader.price_only_scope import CONTROLLED_READER_POLICY

    assert CONTROLLED_READER_POLICY["bare_vendor"] == "UNSUPPORTED_NOT_PROTECTED"
    assert CONTROLLED_READER_POLICY["controlled"] == "controlled_gbbq_reader"
