"""本任务范围内的数据访问边界：禁止打开真实 gbbq 原件与全量缓存。

背景（如实记录，不抹掉历史）：
上一轮盘点中，调用 ``pytdx.reader.GbbqReader().get_df()`` 读取了 gbbq **整文件**，
物化 192797 条记录、datetime 达 20260930（超出 RESEARCH_END=20250731）。
该路径绕过 ``research.io_safety`` 审计。本模块为该事故的最小防复发措施。

设计要点（对应 PR16-01 复核要求）：
- 判定基于**可信组合根的绝对身份**，不依赖字符串前缀匹配：
  相对引用与绝对引用指向同一文件时必须**一致拒绝**；
- 解析 ``..``、``.``、符号链接/junction 等身份别名后再比较；
- 显式禁止清单**不受文件名白名单限制**（先查清单，再做白名单）；
- Windows 路径语义与 POSIX 分离：盘符路径按 Windows 规则规范化，
  不使用 ``Path.resolve()``（在 Linux 上会把 ``E:/...`` 当相对路径），
  也不简单 lowercase 掉整个路径的语义；
- 不支持的路径形式（如 foreign drive、无法解析的别名）**显式拒绝**，fail closed。

边界声明（不夸大）：
- 本模块是**进程内路径守卫**，不是 OS 沙箱，不能约束任意恶意 Python；
- 不实现 gbbq 的日期索引/解码/物理限窗（属后续独立范围）；
- 覆盖本任务实际使用的调用链：``TdxData._load_gbbq`` 与直接 ``GbbqReader``。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

# 真实 gbbq 原件所在目录（来自 config.yaml 的 tdx.gbbq）
REAL_GBBQ_ROOT = Path("E:/new_tdx_mock/T0002/hq_cache")
# 真实全量缓存所在目录（来自 config.yaml 的 tdx.cache_dir）。
# 该根是**相对项目根**的；比较前必须解析为绝对路径，否则解析后的绝对路径
# 无法与相对根做前缀比较（会导致漏判）。
REAL_CACHE_ROOT = Path("data/cache")
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
# 真实 gbbq 文件基名
GBBQ_BASENAMES = ("gbbq", "gbbq.map", "gbbq.csv")

_DRIVE_RE = re.compile(r"^[a-zA-Z]:")
_EXTENDED_RE = re.compile(r"^[\\/]{2}[?.][\\/]")


class ForbiddenDataAccess(RuntimeError):
    """试图打开本任务禁止的数据（真实 gbbq 或其全量缓存）。"""


def _strip_extended_prefix(text: str) -> str:
    """去掉 Windows 扩展长度前缀 ``\\\\?\\`` / ``\\\\.\\``。"""
    while _EXTENDED_RE.match(text):
        text = text[4:]
    return text


def _normalise(path) -> str | None:
    """把路径规范化为**可比身份字符串**（不依赖 os.path 语义）。

    规则：
    - 统一分隔符为 ``/``，去掉扩展长度前缀；
    - 折叠 ``.`` 与 ``..``（在字符串层面解析，无需文件存在）；
    - 盘符统一大写、其余部分保留原大小写（Windows 不敏感 → 比较时再小写）；
    - 相对路径保持相对（**不**用 cwd 拼接，避免 cwd 漂移导致漏判）。
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


def _normalise_root(root) -> tuple[str, ...]:
    """把根规范化为**全部可比变体**（相对写法 + 项目根下的绝对写法）。

    返回元组：相对引用与绝对引用必须**一致拒绝**，因此两个变体都要参与比较。
    """
    variants = set()
    normalised = _normalise(root)
    if normalised:
        variants.add(normalised)
    try:
        path = Path(root)
        if not path.is_absolute():
            absolute = _PROJECT_ROOT / path
            absolute_norm = _normalise(str(absolute))
            if absolute_norm:
                variants.add(absolute_norm)
            resolved_norm = _normalise(os.path.realpath(str(absolute)))
            if resolved_norm:
                variants.add(resolved_norm)
        else:
            resolved_norm = _normalise(os.path.realpath(str(path)))
            if resolved_norm:
                variants.add(resolved_norm)
    except (OSError, ValueError):
        pass
    return tuple(sorted(variants))


def _is_under_any(child: str, parents: tuple) -> bool:
    return any(_is_under(child, parent) for parent in parents)


def _is_under(child: str, parent: str) -> bool:
    if child is None or parent is None:
        return False
    return child == parent or child.startswith(parent + "/")


def _identity_variants(path) -> set:
    """返回该路径的**身份别名集合**（原样 + 解析符号链接后的真实路径）。

    空/``None`` 或无法解析的输入返回**空集**，由调用方 fail closed。
    注意不能把空串交给 ``realpath``——它会返回 cwd，从而把空路径伪装成
    一个合法身份。
    """
    if path is None:
        return set()
    raw_text = str(path).strip()
    if not raw_text:
        return set()
    variants = set()
    normalised = _normalise(raw_text)
    if normalised:
        variants.add(normalised)
    try:
        resolved = os.path.realpath(raw_text)
        resolved_norm = _normalise(resolved)
        if resolved_norm:
            variants.add(resolved_norm)
        # Windows 盘符语义（POSIX 上 realpath 不处理盘符）
        if _DRIVE_RE.match(raw_text):
            try:
                from pathlib import PureWindowsPath
                win_norm = _normalise(str(PureWindowsPath(raw_text)))
                if win_norm:
                    variants.add(win_norm)
            except (ImportError, ValueError):
                pass
    except (OSError, ValueError):
        pass
    return variants


def _extra_forbidden_paths() -> tuple[str, ...]:
    """显式禁止清单（分号分隔），规范化后返回。

    该清单**不受文件名白名单限制**——先查清单，再判断 gbbq 基名。
    """
    raw = os.environ.get("CHANLUN_FORBIDDEN_GBBQ_PATHS", "")
    out = []
    for item in raw.split(";"):
        item = item.strip()
        if not item:
            continue
        for variant in _identity_variants(item):
            out.append(variant)
    return tuple(out)


def is_forbidden_gbbq_path(path) -> bool:
    """判断路径是否指向真实 gbbq 原件或其全量缓存（含身份别名）。

    判定顺序（fail closed）：
    1. 显式禁止清单命中（不受 basename 限制）；
    2. 真实 gbbq 根之下且基名为 gbbq/gbbq.map/gbbq.csv；
    3. 真实缓存根之下的 gbbq.csv；
    4. 无法识别的路径形式（空、扩展前缀解析后为空）视为**不安全** → 拒绝。
    """
    variants = _identity_variants(path)
    if not variants:
        return True  # 无法解析 → fail closed

    explicit = set(_extra_forbidden_paths())
    if explicit and (variants & explicit):
        return True

    gbbq_roots = _normalise_root(REAL_GBBQ_ROOT)
    cache_roots = _normalise_root(REAL_CACHE_ROOT)
    for variant in variants:
        name = variant.rsplit("/", 1)[-1]
        if name not in GBBQ_BASENAMES:
            continue
        if _is_under_any(variant, gbbq_roots):
            return True
        if name == "gbbq.csv" and _is_under_any(variant, cache_roots):
            return True
    return False


def guard_gbbq_path(path, *, label: str = "gbbq") -> None:
    """在打开前拒绝真实 gbbq 路径。调用方必须在**任何 open/read 之前**调用。"""
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
