"""本任务范围内的数据访问边界：禁止打开真实 gbbq 与全量缓存。

背景（如实记录，不抹掉历史）：
上一轮盘点中，调用 ``pytdx.reader.GbbqReader().get_df()`` 读取了 gbbq **整文件**，
物化 192797 条记录、datetime 达 20260930（超出 RESEARCH_END=20250731）。
该路径绕过 ``research.io_safety`` 审计。本模块为该事故的最小防复发措施。

边界声明（不夸大）：
- 本模块是**进程内路径守卫**，不是 OS 沙箱，不能约束任意恶意 Python。
- 它覆盖本任务实际使用的调用链：``TdxData._load_gbbq`` 与直接 ``GbbqReader``
  调用经同一判定函数。
- 拒绝发生在**真实文件 open 之前**（由测试用真实路径 + 探针证明）。
- 判定按**真实路径身份**，不按文件名：临时目录里的合成 ``gbbq.csv``
  是正常测试夹具，不得被误伤（否则会逼出"用 mock 假装安全"的假证据）。

正常价格路径不受影响：.day / .parquet 读取保持可用（正对照）。
"""
from __future__ import annotations

import os
from pathlib import Path

# 真实 gbbq 原件所在目录（本机实际位置，来自 config.yaml 的 tdx.gbbq）
REAL_GBBQ_ROOT = Path("E:/new_tdx_mock/T0002/hq_cache")
# 真实全量缓存所在目录（config.yaml 的 tdx.cache_dir）
REAL_CACHE_ROOT = Path("data/cache")
# 真实 gbbq 文件基名（仅在这些根之下才按名判定）
GBBQ_BASENAMES = ("gbbq", "gbbq.map", "gbbq.csv")


class ForbiddenDataAccess(RuntimeError):
    """试图打开本任务禁止的数据（真实 gbbq 或其全量缓存）。"""


def _resolve(path) -> Path | None:
    if path is None:
        return None
    try:
        return Path(str(path)).resolve()
    except (OSError, ValueError):
        return None


def _extra_forbidden_paths() -> tuple[Path, ...]:
    """显式追加的禁止路径（分号分隔的绝对路径）。"""
    raw = os.environ.get("CHANLUN_FORBIDDEN_GBBQ_PATHS", "")
    out = []
    for item in raw.split(";"):
        item = item.strip()
        if item:
            resolved = _resolve(item)
            if resolved is not None:
                out.append(resolved)
    return tuple(out)


def _under(child: Path | None, parent: Path) -> bool:
    if child is None:
        return False
    try:
        root = parent.resolve()
    except (OSError, ValueError):
        root = parent
    try:
        child.relative_to(root)
        return True
    except ValueError:
        return False


def is_forbidden_gbbq_path(path) -> bool:
    """判断路径是否指向真实 gbbq 原件或其全量缓存。

    按真实路径身份判定：
    - 落在真实 gbbq 根之下且基名为 gbbq/gbbq.map/gbbq.csv；
    - 落在真实缓存根之下且基名为 gbbq.csv；
    - 命中 ``CHANLUN_FORBIDDEN_GBBQ_PATHS`` 显式清单。
    """
    resolved = _resolve(path)
    if resolved is None:
        return False
    name = resolved.name.lower()
    if name not in GBBQ_BASENAMES:
        return False
    if _under(resolved, REAL_GBBQ_ROOT):
        return True
    if name == "gbbq.csv" and _under(resolved, REAL_CACHE_ROOT):
        return True
    for extra in _extra_forbidden_paths():
        if resolved == extra:
            return True
    return False


def guard_gbbq_path(path, *, label: str = "gbbq") -> None:
    """在打开前拒绝真实 gbbq 路径。

    调用方必须在**任何 open/read 之前**调用本函数。
    """
    if is_forbidden_gbbq_path(path):
        raise ForbiddenDataAccess(
            f"FORBIDDEN_GBBQ_ACCESS:{label}:{path}:"
            f"本任务禁止打开真实 gbbq 原件或全量缓存；"
            f"gbbq 的物理限窗实现属后续独立范围")


def assert_gbbq_read_disabled() -> None:
    """显式断言：本任务未启用 gbbq 读取。"""
    if os.environ.get("CHANLUN_ALLOW_GBBQ_READ") == "1":
        raise ForbiddenDataAccess(
            "GBBQ_READ_NOT_PERMITTED_IN_THIS_TASK:"
            "本任务不接受通过环境变量放开 gbbq 读取")
