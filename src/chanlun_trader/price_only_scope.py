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
from dataclasses import dataclass
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
    """返回路径的**身份闭包**（原样 + 绝对化 + 符号链接/junction 解析）。

    空/``None`` 或无法解析 → 返回空集，由调用方 fail closed。
    注意不能把空串交给 ``realpath``（它会返回 cwd，把空路径伪装成合法身份）。
    """
    return _freeze_identity_closure(path)


def is_forbidden_gbbq_path(path) -> bool:
    """判断路径是否指向真实 gbbq 原件或其全量缓存（含身份别名两方向）。

    判定基于**冻结集合**与**查询身份闭包**的交集，检查时不重新读取 cwd。
    两个方向都成立：清单列目标访问别名、清单列别名访问目标本体。
    """
    variants = _identity_variants(path)
    if not variants:
        return True  # 无法解析 → fail closed

    deny = _FROZEN_DENYLIST
    if variants & deny:
        return True

    # 根之下任意 gbbq 类文件（覆盖未逐一列出的同目录文件）
    for variant in variants:
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


def _freeze_identity_closure(path) -> set:
    """把一个受保护路径解析为**身份闭包**（含别名两个方向）。

    需求（PR16-01）：清单列目标、访问别名 → 拒绝；**清单列别名、访问目标本体
    也必须拒绝**。因此冻结时不能只做词法拼接，必须解析该路径的
    真实指向，并把"别名 → 真实目标"与"真实目标 → 别名"两侧都纳入。

    **基准一致性（关键）**：所有解析都以 ``COMPOSITION_ROOT`` 为唯一基准。
    先用组合根把候选锚定为绝对路径，再对该绝对候选做符号链接/父目录别名解析；
    绝不在解析过程中混用 cwd —— 否则同一配置在 cwd 不同时会解析到不同身份
    （已复现的绕过：相对链接按 cwd 解析，目标本体未被识别）。

    - 解析成功：把锚定后的词法身份、``realpath`` 结果与"父目录解析 + 基名"
      都纳入闭包；
    - 解析失败或平台不支持：**不伪装成功**，该候选不产生额外身份
      （调用方仍按词法身份判定），并由 ``_resolve_status`` 如实记录。
    """
    if path is None:
        return set()
    raw = str(path).strip()
    if not raw:
        return set()
    closure: set[str] = set()

    # 步骤一：以组合根锚定为绝对候选（唯一基准，不读 cwd）
    anchored = _to_absolute_normalised(raw)
    if anchored:
        closure.add(anchored)
    relative = _normalise(raw)
    if relative:
        closure.add(relative)

    # 步骤二：对**锚定后的绝对候选**做真实指向解析
    anchor_path = _anchored_path(raw)
    if anchor_path is not None:
        for resolved in _resolve_real_paths(anchor_path):
            if resolved:
                closure.add(resolved)

    # Windows 盘符语义（POSIX 上 realpath 不处理盘符）
    if _DRIVE_RE.match(raw):
        try:
            from pathlib import PureWindowsPath
            win_norm = _normalise(str(PureWindowsPath(raw)))
            if win_norm:
                closure.add(win_norm)
        except (ImportError, ValueError):
            pass
    return closure


def _anchored_path(raw: str) -> Path | None:
    """把候选以组合根锚定为绝对 Path（相对项基于 COMPOSITION_ROOT）。"""
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = COMPOSITION_ROOT / candidate
    return candidate


def _resolve_real_paths(anchor: Path) -> set:
    """对已锚定的绝对路径解析真实身份（符号链接/父目录别名）。

    返回真实指向与"父目录解析 + 基名"两类身份。解析异常返回空集，
    由调用方按词法身份处理（不伪装解析成功）。
    """
    resolved_ids: set = set()
    try:
        real = os.path.realpath(str(anchor))
        real_norm = _to_absolute_normalised(real)
        if real_norm:
            resolved_ids.add(real_norm)
        parent_real = os.path.realpath(str(anchor.parent))
        name = anchor.name
        if name:
            parent_norm = _to_absolute_normalised(str(Path(parent_real) / name))
            if parent_norm:
                resolved_ids.add(parent_norm)
    except (OSError, ValueError):
        return set()
    return resolved_ids


def _build_frozen_denylist() -> frozenset:
    """建立**冻结**的禁止身份集合（含别名闭包）。

    - 真实 gbbq 根与真实缓存根下的 gbbq 类文件（按基名 + 根身份闭包）；
    - 显式清单 ``CHANLUN_FORBIDDEN_GBBQ_PATHS``：每项解析为身份闭包。
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
        frozen |= _freeze_identity_closure(item)
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


def guard_gbbq_path(path, *, label: str = "gbbq") -> None:
    """在打开前拒绝真实 gbbq 路径。调用方必须在**任何 open/read 之前**调用。"""
    if is_forbidden_gbbq_path(path):
        raise ForbiddenDataAccess(
            f"FORBIDDEN_GBBQ_ACCESS:{label}:{path}:"
            f"本任务禁止打开真实 gbbq 原件或全量缓存；"
            f"gbbq 的物理限窗实现属后续独立范围")


def controlled_gbbq_reader(path):
    """本任务**受控的直接 vendor 入口**。

    直接 ``GbbqReader().get_df(path)`` 是原始事故路径。本任务不禁止该 vendor
    的存在，但要求项目内所有直接调用都经本包装器，从而在**打开/解码之前**
    先经过同一政策。

    返回 vendor 解码后的 DataFrame。若本任务作用域激活且路径受保护，
    则抛出 ``ForbiddenDataAccess``——此时 vendor 的 ``get_df`` **不会被执行**。
    """
    if task_scope_active():
        assert_gbbq_read_disabled()
        guard_gbbq_path(path, label="controlled_gbbq_reader")
    from pytdx.reader import GbbqReader

    return GbbqReader().get_df(str(path))


# 受控读取政策：明确区分"受控的直接调用"与"裸 vendor 调用"。
# 裸 vendor 调用不属于本任务的受控能力，标为不支持；不得列为已关闭。
CONTROLLED_READER_POLICY = {
    "controlled": "controlled_gbbq_reader",
    "bare_vendor": "UNSUPPORTED_NOT_PROTECTED",
    "note": "裸 GbbqReader().get_df() 不属于本任务受控能力，不支持且未认证；"
            "项目内直接调用点必须经 controlled_gbbq_reader。"
            "本任务不实现 OS 沙箱，不约束任意恶意 Python。",
}


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


@dataclass(frozen=True)
class TaskScopeSnapshot:
    """任务作用域的**完整政策快照**。

    保存的不只是 ``active`` 布尔值，还包含该作用域实际持有的
    ``reason`` 与**受保护身份集合**（冻结禁止集合）。内部 fixture 在退出时
    必须用 ``restore_task_scope(snapshot)`` 恢复整个政策，而不是无条件
    ``deactivate`` —— 后者会关闭仍在执行的外层任务。
    """

    active: bool
    reason: str
    denylist: frozenset
    forbidden_env: str | None


def snapshot_task_scope() -> TaskScopeSnapshot:
    """保存进入前的完整政策（供内部 fixture 恢复）。"""
    return TaskScopeSnapshot(
        active=bool(_TASK_SCOPE["active"]),
        reason=str(_TASK_SCOPE["reason"]),
        denylist=_FROZEN_DENYLIST,
        forbidden_env=os.environ.get("CHANLUN_FORBIDDEN_GBBQ_PATHS"),
    )


def restore_task_scope(snapshot: TaskScopeSnapshot) -> None:
    """恢复进入前的完整政策（active / reason / 受保护身份 / 环境清单）。

    没有改动的全局状态不另造副本：``denylist`` 直接复用快照中的冻结集合。
    """
    global _FROZEN_DENYLIST
    _TASK_SCOPE["active"] = bool(snapshot.active)
    _TASK_SCOPE["reason"] = str(snapshot.reason)
    _FROZEN_DENYLIST = snapshot.denylist
    if snapshot.forbidden_env is None:
        os.environ.pop("CHANLUN_FORBIDDEN_GBBQ_PATHS", None)
    else:
        os.environ["CHANLUN_FORBIDDEN_GBBQ_PATHS"] = snapshot.forbidden_env


def assert_gbbq_read_disabled() -> None:
    """显式断言：本任务未启用 gbbq 读取。

    环境变量至多**追加**约束（见 ``CHANLUN_FORBIDDEN_GBBQ_PATHS``），
    不得解除已建立的禁止身份；也不接受 allow 类环境变量放开。
    """
    if os.environ.get("CHANLUN_ALLOW_GBBQ_READ") == "1":
        raise ForbiddenDataAccess(
            "GBBQ_READ_NOT_PERMITTED_IN_THIS_TASK:"
            "本任务不接受通过环境变量放开 gbbq 读取")
