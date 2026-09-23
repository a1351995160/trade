"""本任务范围内的数据访问边界：冻结禁止身份，禁止打开真实 gbbq 与全量缓存。

背景（如实记录，不抹掉历史）：
更早一轮盘点中，调用 ``pytdx.reader.GbbqReader().get_df()`` 读取了 gbbq **整文件**，
物化 192797 条记录、datetime 达 20260930（超出 RESEARCH_END=20250731）。
该路径绕过 ``research.io_safety`` 审计。

设计要点（对应 PR16-01 / PR16-02 复核要求）：

1. **冻结禁止身份**：禁止集合在**任务组合根**一次性解析为绝对身份并冻结；
   检查时**不重新读取 cwd** 解释相对清单。否则同一绝对路径会随 cwd 变化
   在"拒绝"与"真实读取"之间翻转（已复现的反例）。
2. **原件与缓存可在外部数据工作区**：不把源码根等同数据根。
3. **环境变量至多追加约束**：不得解除或改变已经建立的禁止身份。
4. **一致身份**：普通相对/绝对/``..``、符号链接、Windows 扩展前缀、
   任意 basename 显式清单都按同一身份判定。
5. **fail closed**：无法解析的路径形式拒绝；未测试的别名形式（硬链接、
   junction 等）如实标注，不声称已覆盖。

边界声明（不夸大）：
- 本模块是**进程内路径守卫**，不是 OS 沙箱，不能约束任意恶意 Python；
- 不实现 gbbq 的日期索引/解码/物理限窗（属后续独立范围）。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

# 真实 gbbq 原件所在目录（来自 config.yaml 的 tdx.gbbq）
REAL_GBBQ_ROOT = Path("E:/new_tdx_mock/T0002/hq_cache")
# 真实全量缓存所在目录（来自 config.yaml 的 tdx.cache_dir）
REAL_CACHE_ROOT = Path("data/cache")
# 真实 gbbq 文件基名
GBBQ_BASENAMES = ("gbbq", "gbbq.map", "gbbq.csv")

# 组合根：相对配置项的解析基准（**冻结**，不随 cwd 变化）
COMPOSITION_ROOT = Path(__file__).resolve().parents[2]

_DRIVE_RE = re.compile(r"^[a-zA-Z]:")
_EXTENDED_RE = re.compile(r"^[\\/]{2}[?.][\\/]")

# 任务作用域：默认**未激活**，由组合根显式激活。
_TASK_SCOPE: dict = {"active": False, "reason": ""}


class ForbiddenDataAccess(RuntimeError):
    """试图打开本任务禁止的数据（真实 gbbq 或其全量缓存）。"""


# --------------------------------------------------------------------------
# 路径规范化（平台无关身份字符串）
# --------------------------------------------------------------------------
def _strip_extended_prefix(text: str) -> str:
    """去掉 Windows 扩展长度前缀 ``\\\\?\\`` / ``\\\\.\\``。"""
    while _EXTENDED_RE.match(text):
        text = text[4:]
    return text


def _normalise(path) -> str | None:
    """把路径规范化为**可比身份字符串**（不依赖 os.path 语义）。

    - 统一分隔符、去扩展前缀、字符串层面折叠 ``.`` 与 ``..``；
    - 盘符统一大写、其余小写（Windows 大小写不敏感）；
    - 相对路径**保持相对**（不在此处拼接 cwd，避免 cwd 漂移导致漏判）。
    """
    if path is None:
        return None
    text = str(path).replace("\\", "/").strip()
    if not text:
        return None
    text = _strip_extended_prefix(text)
    if not text:
        return None

    drive = ""
    rest = text
    match = _DRIVE_RE.match(text)
    if match:
        drive = text[:2].upper()
        rest = text[2:]
        if not rest.startswith("/"):
            rest = "/" + rest

    parts: list[str] = []
    for token in rest.split("/"):
        if token in ("", "."):
            continue
        if token == "..":
            if parts and parts[-1] != "..":
                parts.pop()
            else:
                parts.append("..")
            continue
        parts.append(token)
    body = "/".join(parts)
    if drive:
        return f"{drive}/{body}".lower()
    return body.lower()


def _to_absolute_normalised(path) -> str | None:
    """把路径解析为**绝对**身份字符串（相对项以 COMPOSITION_ROOT 为基准）。

    这是"冻结"的关键：相对配置项在**建立禁止集合时**一次性解析，
    之后检查不再依赖 cwd。
    """
    if path is None:
        return None
    raw = str(path).strip()
    if not raw:
        return None
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = COMPOSITION_ROOT / candidate
    normalised = _normalise(str(candidate))
    return normalised


def _identity_variants(path) -> set:
    """返回路径的**身份别名集合**（绝对规范化 + 符号链接解析结果）。

    空/``None`` 或无法解析 → 返回空集，由调用方 fail closed。
    注意不能把空串交给 ``realpath``（它会返回 cwd，把空路径伪装成合法身份）。
    """
    if path is None:
        return set()
    raw_text = str(path).strip()
    if not raw_text:
        return set()
    variants = set()

    absolute_norm = _to_absolute_normalised(raw_text)
    if absolute_norm:
        variants.add(absolute_norm)
    relative_norm = _normalise(raw_text)
    if relative_norm:
        variants.add(relative_norm)

    try:
        resolved = os.path.realpath(str(Path(raw_text)))
        resolved_norm = _normalise(resolved)
        if resolved_norm:
            variants.add(resolved_norm)
    except (OSError, ValueError):
        pass

    if _DRIVE_RE.match(raw_text):
        try:
            from pathlib import PureWindowsPath
            win_norm = _normalise(str(PureWindowsPath(raw_text)))
            if win_norm:
                variants.add(win_norm)
        except (ImportError, ValueError):
            pass
    return variants


# --------------------------------------------------------------------------
# 冻结的禁止集合（建立一次，之后只读）
# --------------------------------------------------------------------------
def _root_identities(root: Path) -> tuple[str, ...]:
    """把根解析为全部可比身份（相对写法 + 组合根下的绝对写法）。"""
    variants = set()
    relative = _normalise(str(root))
    if relative:
        variants.add(relative)
    absolute = _to_absolute_normalised(str(root))
    if absolute:
        variants.add(absolute)
    try:
        resolved = _normalise(os.path.realpath(str(
            root if root.is_absolute() else COMPOSITION_ROOT / root)))
        if resolved:
            variants.add(resolved)
    except (OSError, ValueError):
        pass
    return tuple(sorted(variants))


def _build_frozen_denylist() -> frozenset:
    """建立**冻结**的禁止身份集合。

    - 真实 gbbq 根与真实缓存根下的 gbbq 类文件（按基名 + 根身份）；
    - 显式清单 ``CHANLUN_FORBIDDEN_GBBQ_PATHS``：相对项以 COMPOSITION_ROOT
      为基准一次性解析为绝对身份。
    """
    frozen: set[str] = set()

    for root in (REAL_GBBQ_ROOT, REAL_CACHE_ROOT):
        for root_id in _root_identities(root):
            for basename in GBBQ_BASENAMES:
                frozen.add(f"{root_id}/{basename}")

    raw = os.environ.get("CHANLUN_FORBIDDEN_GBBQ_PATHS", "")
    for item in raw.split(";"):
        item = item.strip()
        if not item:
            continue
        absolute = _to_absolute_normalised(item)
        if absolute:
            frozen.add(absolute)
        relative = _normalise(item)
        if relative:
            frozen.add(relative)
    return frozenset(frozen)


_FROZEN_DENYLIST: frozenset = _build_frozen_denylist()


def rebuild_frozen_denylist() -> frozenset:
    """**组合根初始化**时调用：重新解析并冻结禁止身份。

    这是唯一允许改变禁止集合的入口。环境变量至多**追加**约束：
    真实 gbbq 根与真实缓存根的身份**始终**保留，不会被环境变量移除。
    """
    global _FROZEN_DENYLIST
    _FROZEN_DENYLIST = _build_frozen_denylist()
    return _FROZEN_DENYLIST


def frozen_denylist() -> frozenset:
    """返回冻结的禁止身份集合（只读；用于测试与审计）。"""
    return _FROZEN_DENYLIST


def is_forbidden_gbbq_path(path) -> bool:
    """判断路径是否指向真实 gbbq 原件或其全量缓存（含身份别名）。

    判定基于**冻结集合**，检查时不重新读取 cwd。
    """
    variants = _identity_variants(path)
    if not variants:
        return True  # 无法解析 → fail closed

    deny = _FROZEN_DENYLIST
    for variant in variants:
        if variant in deny:
            return True
        # 根之下任意 gbbq 类文件（覆盖未逐一列出的同目录文件）
        name = variant.rsplit("/", 1)[-1]
        if name not in GBBQ_BASENAMES:
            continue
        for root_id in _root_identities(REAL_GBBQ_ROOT):
            if variant == f"{root_id}/{name}":
                return True
        if name == "gbbq.csv":
            for root_id in _root_identities(REAL_CACHE_ROOT):
                if variant == f"{root_id}/{name}":
                    return True
    return False


def guard_gbbq_path(path, *, label: str = "gbbq") -> None:
    """在打开前拒绝真实 gbbq 路径。调用方必须在**任何 open/read 之前**调用。"""
    if is_forbidden_gbbq_path(path):
        raise ForbiddenDataAccess(
            f"FORBIDDEN_GBBQ_ACCESS:{label}:{path}:"
            f"本任务禁止打开真实 gbbq 原件或全量缓存；"
            f"gbbq 的物理限窗实现属后续独立范围")


def task_scope_active() -> bool:
    """本任务作用域是否激活。

    由**显式激活**驱动（组合根调用 ``activate_task_scope()``），
    不由报告文件存在性推断——报告提交后会长期存在，不能当"正在执行本任务"的判据。

    生产 ``TdxData._load_gbbq`` 与真实测试分类都消费本函数，
    保证运行时政策一致。
    """
    return bool(_TASK_SCOPE["active"])


def activate_task_scope(reason: str = "price_only_validation_task") -> None:
    """在组合根显式激活本任务作用域（限制本任务行为）。"""
    _TASK_SCOPE["active"] = True
    _TASK_SCOPE["reason"] = reason


def deactivate_task_scope() -> None:
    """退出本任务作用域，恢复原有权限（**不创造**真实数据授权）。"""
    _TASK_SCOPE["active"] = False
    _TASK_SCOPE["reason"] = ""


def task_scope_reason() -> str:
    return str(_TASK_SCOPE["reason"])


def assert_gbbq_read_disabled() -> None:
    """显式断言：本任务未启用 gbbq 读取。

    环境变量至多**追加**约束（见 ``CHANLUN_FORBIDDEN_GBBQ_PATHS``），
    不得解除已建立的禁止身份；也不接受 allow 类环境变量放开。
    """
    if os.environ.get("CHANLUN_ALLOW_GBBQ_READ") == "1":
        raise ForbiddenDataAccess(
            "GBBQ_READ_NOT_PERMITTED_IN_THIS_TASK:"
            "本任务不接受通过环境变量放开 gbbq 读取")
