"""BT_INDICATOR_REGISTRY_V2 — 统一指标注册表（机器可读契约）。

设计目标
--------
新增一个指标**不应**要求修改 Engine / Broker / Ledger 的指标名白名单。
特有公式放在指标层；交易记账仍由原引擎负责。本模块只做三件事：

1. 声明每个指标的机器可读契约（参数 schema、输入/输出名与类型、单位、
   price_mode、周期、初始化、lookback/warmup、缺失政策、可用时间规则）；
2. 用同一个调用签名把指标实现适配成统一的 ``compute`` 入口；
3. 明确拒绝未注册 / 未认证 / 缺输入 / 非法参数 / 输出名不存在的请求，
   不做 quiet fallback。

与既有体系的关系
----------------
- 单证券时序层（本注册表）与 ``research/unified_factor.py`` 的**多证券面板级**
  DSL 是互补的两层：本层面向"每根 bar 的指标值 + 条件 + 退出"，
  面板层面向横截面因子。二者共享同一批公式语义，不互相替代。
- V1 的 ``MACD_V1`` / ``KDJ_V1`` / ``MA_EMA_CROSS_V1`` 以 ``REUSE_AS_IS`` 方式
  注册（别名指向 V1 实现），保持公式、初值、预热与版本身份不变。

不使用 ``eval`` / ``exec`` / 动态 import / 文件或网络访问。
"""
from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import pandas as pd

from . import indicators_v1 as v1
from . import indicators_v2 as v2
from .indicators_v2 import IndicatorFrameV2, PriceInput, make_price_input

REGISTRY_VERSION = "BT_INDICATOR_REGISTRY_V2"

# 证据状态（分别记录，不能由一个推导另一个）
EVIDENCE_STATUSES = (
    "IMPLEMENTED",
    "FORMULA_VALIDATED",
    "CAUSALITY_VALIDATED",
    "REGISTERED",
    "ENTRYPOINT_WIRED",
    "EXECUTION_BEHAVIOR_VALIDATED",
    "ACCOUNTING_VALIDATED",
    "REAL_DATA_VALIDATED",
    "PROFITABILITY_VALIDATED",
)

# 复用处置
REUSE_AS_IS = "REUSE_AS_IS"
WRAP_AND_REGISTER = "WRAP_AND_REGISTER"
NEW_IN_V2 = "NEW_IN_V2"


class IndicatorRegistryError(ValueError):
    """未注册 / 未认证 / 缺输入 / 非法参数 / 输出名不存在。必须显式失败。"""


@dataclass(frozen=True)
class IndicatorSpec:
    """指标契约。字段缺失用 ``UNKNOWN`` 显式标注，不用默认值掩盖。"""

    indicator_id: str
    version: str
    family: str
    display_name: str
    reuse: str
    outputs: Tuple[str, ...]
    params: Mapping[str, Any]
    inputs: Tuple[str, ...]
    price_mode: str
    frequency: str
    available_at_rule: str
    warmup_bars: int
    lookback: int
    missing_policy: str
    unit: str
    cross_sectional: bool
    requires_extra_data: Tuple[str, ...] = ()
    aliases: Tuple[str, ...] = ()
    formula_note: str = ""
    evidence: Mapping[str, bool] = field(default_factory=dict)
    implementation_path: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "indicator_id": self.indicator_id,
            "version": self.version,
            "family": self.family,
            "display_name": self.display_name,
            "reuse": self.reuse,
            "outputs": list(self.outputs),
            "params": dict(self.params),
            "inputs": list(self.inputs),
            "price_mode": self.price_mode,
            "frequency": self.frequency,
            "available_at_rule": self.available_at_rule,
            "warmup_bars": int(self.warmup_bars),
            "lookback": int(self.lookback),
            "missing_policy": self.missing_policy,
            "unit": self.unit,
            "cross_sectional": bool(self.cross_sectional),
            "requires_extra_data": list(self.requires_extra_data),
            "aliases": list(self.aliases),
            "formula_note": self.formula_note,
            "evidence": {k: bool(self.evidence.get(k, False)) for k in EVIDENCE_STATUSES},
            "implementation_path": self.implementation_path,
        }


@dataclass(frozen=True)
class IndicatorResult:
    """统一计算结果：稳定输出名 + ready + 版本身份 + 契约快照 + **解析后的实际参数**。

    ``resolved_params`` 是本次计算真正使用的参数（已合并默认值）。
    调用方必须读它，不得再从 ``spec.params``（契约默认值）重新推断，
    否则 ``ATR(window=7)`` 会被误判成默认的 14。
    """

    indicator_id: str
    version: str
    index: pd.Index
    frame: IndicatorFrameV2
    spec: IndicatorSpec
    resolved_params: Mapping[str, Any] = field(default_factory=dict)

    def param(self, name: str, default: Any = None) -> Any:
        if name in self.resolved_params:
            return self.resolved_params[name]
        return self.spec.params.get(name, default)

    def output(self, name: str) -> pd.Series:
        if name not in self.spec.outputs:
            raise IndicatorRegistryError(f"UNKNOWN_OUTPUT:{self.indicator_id}:{name}")
        return self.frame.value(name)

    @property
    def output_names(self) -> List[str]:
        return list(self.spec.outputs)

    def ready(self) -> pd.Series:
        return self.frame.ready

    def to_frame(self) -> pd.DataFrame:
        frame = self.frame.to_frame()
        frame["__indicator_id__"] = self.indicator_id
        frame["__indicator_version__"] = self.version
        return frame


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _source_of(fn: Callable) -> str:
    """取函数**真实实现**源码。

    ``_adapter`` 返回的是包装器；只哈希包装器会漏掉真实公式变化。
    因此优先取 ``__wrapped_impl__``（真实实现函数）。
    """
    target = getattr(fn, "__wrapped_impl__", fn)
    try:
        return inspect.getsource(target)
    except (OSError, TypeError):
        return "UNAVAILABLE"


def _impl_hash(fn: Callable) -> str:
    return _sha(_source_of(fn))


def _formula_hash(fn: Callable, dependencies: Sequence[str] = (),
                  registry: Optional["IndicatorRegistry"] = None,
                  seen: Optional[set] = None) -> str:
    """公式指纹：**递归**绑定真实实现源码 + 依赖的 version + 依赖的源码指纹。

    只拼接依赖名称不足以证明依赖未变；必须把依赖的实现内容纳入。
    ``seen`` 防止循环依赖导致无限递归。
    """
    seen = set(seen or ())
    parts = [_source_of(fn)]
    for dependency in sorted(dependencies):
        if dependency in seen:
            continue
        seen.add(dependency)
        parts.append(f"dep:{dependency}")
        if registry is not None:
            spec = None
            for candidate in registry.specs():
                if candidate.indicator_id == dependency:
                    spec = candidate
                    break
            if spec is not None:
                parts.append(f"version:{spec.version}")
                dep_fn = registry._impls.get((spec.indicator_id, spec.version))
                if dep_fn is not None:
                    parts.append(_source_of(dep_fn))
                    parts.append(_formula_hash(
                        dep_fn, registry._dependencies_of(dependency), registry, seen))
    return _sha("\n".join(parts))


# --------------------------------------------------------------------------
# 实例身份
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class IndicatorInstanceKey:
    """指标**实例**的稳定身份。

    区分 id / version / 参数 / 输入 / 价格域 / 周期。同一请求里
    ``MA(5)`` 与 ``MA(20)`` 是两个不同实例，不得共用同一个结果槽。
    """

    indicator_id: str
    version: str
    params: Tuple[Tuple[str, str], ...]
    inputs: Tuple[str, ...]
    price_mode: str
    frequency: str

    @staticmethod
    def build(spec: "IndicatorSpec", params: Mapping[str, Any]) -> "IndicatorInstanceKey":
        canonical = tuple(sorted((str(k), _canonical_param(v)) for k, v in params.items()))
        return IndicatorInstanceKey(
            indicator_id=spec.indicator_id, version=spec.version, params=canonical,
            inputs=tuple(spec.inputs), price_mode=spec.price_mode, frequency=spec.frequency,
        )

    def as_string(self) -> str:
        return _sha(_canonical_json({
            "indicator_id": self.indicator_id, "version": self.version,
            "params": list(self.params), "inputs": list(self.inputs),
            "price_mode": self.price_mode, "frequency": self.frequency,
        }))


def _canonical_param(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


# --------------------------------------------------------------------------
# 适配器：把各家族实现包装成统一签名
# --------------------------------------------------------------------------


def _adapter(fn: Callable, output_names: Sequence[str]) -> Callable[..., IndicatorFrameV2]:
    """统一适配：``fn(data: PriceInput, **params) -> IndicatorFrameV2``。

    校验输出名与声明一致；不一致即注册表错误（防止声明与实现漂移）。
    保留 ``__wrapped_impl__`` 指向真实实现，使实现指纹取自真实公式而非包装器。
    """
    def compute(data: PriceInput, **params) -> IndicatorFrameV2:
        frame = fn(data, **params)
        actual = tuple(frame.output_names)
        if actual != tuple(output_names):
            raise IndicatorRegistryError(
                f"OUTPUT_CONTRACT_MISMATCH:{fn.__name__}:declared={tuple(output_names)}:actual={actual}")
        return frame

    compute.__wrapped_impl__ = fn      # type: ignore[attr-defined]
    return compute


def _v1_macd(data: PriceInput, *, fast: int = 12, slow: int = 26, signal: int = 9, **_ignored) -> IndicatorFrameV2:
    """V1 MACD_V1 的注册适配（REUSE_AS_IS：公式与预热完全沿用 V1）。"""
    close = pd.Series(data.close, index=data.index)
    frame = v1.macd_v1(close, fast=fast, slow=slow, signal=signal)
    return IndicatorFrameV2(
        indicator_id="MACD", version=frame.version, index=frame.index,
        columns={name: frame.value(name) for name in ("ema_fast", "ema_slow", "dif", "dea", "hist")},
        ready=frame.ready, segment=frame.segment, warmup_bars=slow + signal - 1,
    )


def _v1_kdj(data: PriceInput, *, n: int = 9, k_period: int = 3, d_period: int = 3,
            **_ignored) -> IndicatorFrameV2:
    """V1 KDJ_V1 的注册适配（REUSE_AS_IS）。"""
    if data.high is None or data.low is None:
        raise IndicatorRegistryError("DATA_DEPENDENCY_NOT_MET:KDJ:high_low")
    frame = v1.kdj_v1(
        pd.Series(data.high, index=data.index),
        pd.Series(data.low, index=data.index),
        pd.Series(data.close, index=data.index),
        n=n, k_period=k_period, d_period=d_period,
    )
    return IndicatorFrameV2(
        indicator_id="KDJ", version=frame.version, index=frame.index,
        columns={name: frame.value(name) for name in ("rsv", "k", "d", "j")},
        ready=frame.ready, segment=frame.segment, warmup_bars=n,
    )


# --------------------------------------------------------------------------
# 注册表
# --------------------------------------------------------------------------


class IndicatorRegistry:
    """指标注册表。默认载入 V1 复用项 + V2 新增项。"""

    def __init__(self, specs: Optional[Iterable[IndicatorSpec]] = None):
        self._specs: Dict[Tuple[str, str], IndicatorSpec] = {}
        self._impls: Dict[Tuple[str, str], Callable] = {}
        self._dependencies: Dict[str, Tuple[str, ...]] = {}
        if specs is not None:
            for spec in specs:
                self._specs[(spec.indicator_id, spec.version)] = spec

    # -- 查询 -------------------------------------------------------------
    def get(self, indicator_id: str, version: Optional[str] = None) -> IndicatorSpec:
        if version:
            spec = self._specs.get((indicator_id, version))
            if spec is None:
                raise IndicatorRegistryError(f"UNKNOWN_INDICATOR:{indicator_id}@{version}")
            return spec
        matches = [spec for (iid, _), spec in self._specs.items() if iid == indicator_id]
        if not matches:
            raise IndicatorRegistryError(f"UNKNOWN_INDICATOR:{indicator_id}")
        return max(matches, key=lambda spec: spec.version)

    def resolve(self, name: str) -> IndicatorSpec:
        """按 id 或别名解析；未注册即报错，不静默回退到另一指标。"""
        try:
            return self.get(name)
        except IndicatorRegistryError:
            pass
        for spec in self._specs.values():
            if name in spec.aliases:
                return spec
        raise IndicatorRegistryError(f"UNKNOWN_INDICATOR_OR_ALIAS:{name}")

    def specs(self) -> List[IndicatorSpec]:
        return [self._specs[key] for key in sorted(self._specs)]

    def families(self) -> List[str]:
        return sorted({spec.family for spec in self._specs.values()})

    def by_family(self, family: str) -> List[IndicatorSpec]:
        return [spec for spec in self.specs() if spec.family == family]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "registry_version": REGISTRY_VERSION,
            "indicators": [spec.to_dict() for spec in self.specs()],
            "implementation_hashes": {
                f"{iid}@{version}": _impl_hash(fn) for (iid, version), fn in sorted(self._impls.items())
            },
            # 公式指纹包含真实实现源码 + 依赖指标源码；改实现或改语义即改变指纹。
            "formula_hashes": {
                f"{iid}@{version}": _formula_hash(
                    fn, self._dependencies_of(iid), self)
                for (iid, version), fn in sorted(self._impls.items())
            },
            "dependencies": {
                iid: sorted(self._dependencies_of(iid)) for iid in sorted({k[0] for k in self._impls})
            },
        }

    def _dependencies_of(self, indicator_id: str) -> List[str]:
        """该指标依赖的其他指标 id（用于公式指纹与依赖图校验）。"""
        return sorted(self._dependencies.get(indicator_id, ()))

    def formula_hash(self, indicator_id: str, version: Optional[str] = None) -> str:
        spec = self.get(indicator_id, version) if version else self.resolve(indicator_id)
        fn = self._impls.get((spec.indicator_id, spec.version))
        if fn is None:
            raise IndicatorRegistryError(f"INDICATOR_NOT_IMPLEMENTED:{spec.indicator_id}")
        return _formula_hash(fn, self._dependencies_of(spec.indicator_id), self)

    def write(self, path) -> str:
        from pathlib import Path

        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return str(target)

    # -- 计算 -------------------------------------------------------------
    def compute(
        self,
        indicator_id: str,
        close: pd.Series,
        *,
        version: Optional[str] = None,
        high: Optional[pd.Series] = None,
        low: Optional[pd.Series] = None,
        open_: Optional[pd.Series] = None,
        volume: Optional[pd.Series] = None,
        amount: Optional[pd.Series] = None,
        prev_close: Optional[pd.Series] = None,
        params: Optional[Mapping[str, Any]] = None,
        extra_data: Optional[Mapping[str, pd.Series]] = None,
    ) -> IndicatorResult:
        """统一的公共计算入口。

        - 未注册的 id/别名 -> ``UNKNOWN_INDICATOR_OR_ALIAS``；
        - 未知参数 -> ``UNKNOWN_PARAMETER``（不静默忽略）；
        - 参数类型/范围非法 -> ``INVALID_PARAMETER``；
        - 缺依赖字段 -> ``DATA_DEPENDENCY_NOT_MET``；
        - 声明需要额外数据但未提供 -> ``DATA_DEPENDENCY_NOT_MET``（不用替代值补齐）。
        """
        spec = self.get(indicator_id, version) if version else self.resolve(indicator_id)
        key = (spec.indicator_id, spec.version)
        impl = self._impls.get(key)
        if impl is None:
            raise IndicatorRegistryError(f"INDICATOR_NOT_IMPLEMENTED:{spec.indicator_id}@{spec.version}")

        supplied = dict(params or {})
        unknown = sorted(set(supplied) - set(spec.params))
        if unknown:
            raise IndicatorRegistryError(
                f"UNKNOWN_PARAMETER:{spec.indicator_id}:{','.join(unknown)}")
        resolved = {name: supplied.get(name, default) for name, default in spec.params.items()}
        _validate_params(spec, resolved)

        data = make_price_input(
            close, high=high, low=low, open_=open_, volume=volume,
            amount=amount, prev_close=prev_close,
        )
        _require_inputs(spec, data)

        extras = dict(extra_data or {})
        for name in spec.requires_extra_data:
            if name not in extras or extras[name] is None:
                raise IndicatorRegistryError(f"DATA_DEPENDENCY_NOT_MET:{spec.indicator_id}:{name}")
            if not extras[name].index.equals(data.index):
                raise IndicatorRegistryError(f"INDEX_MISMATCH:{spec.indicator_id}:{name}")

        kwargs = dict(resolved)
        kwargs.update({name: extras[name] for name in spec.requires_extra_data})
        frame = impl(data, **kwargs)
        return IndicatorResult(
            indicator_id=spec.indicator_id, version=spec.version,
            index=frame.index, frame=frame, spec=spec,
            resolved_params=dict(resolved),
        )

    def resolve_instance(self, indicator_id: str, *, version: Optional[str] = None,
                         params: Optional[Mapping[str, Any]] = None) -> Tuple[IndicatorSpec, IndicatorInstanceKey]:
        """解析并返回 (spec, 实例身份)。未知版本/参数在此阶段即拒绝。"""
        spec = self.get(indicator_id, version) if version else self.resolve(indicator_id)
        supplied = dict(params or {})
        unknown = sorted(set(supplied) - set(spec.params))
        if unknown:
            raise IndicatorRegistryError(
                f"UNKNOWN_PARAMETER:{spec.indicator_id}:{','.join(unknown)}")
        resolved = {name: supplied.get(name, default) for name, default in spec.params.items()}
        _validate_params(spec, resolved)
        return spec, IndicatorInstanceKey.build(spec, resolved)

    # -- 注册 -------------------------------------------------------------
    def register(self, spec: IndicatorSpec, impl: Callable,
                 dependencies: Sequence[str] = ()) -> None:
        self._specs[(spec.indicator_id, spec.version)] = spec
        self._impls[(spec.indicator_id, spec.version)] = impl
        if dependencies:
            self._dependencies[spec.indicator_id] = tuple(dependencies)


def _validate_params(spec: IndicatorSpec, resolved: Mapping[str, Any]) -> None:
    for name, value in resolved.items():
        if value is None:
            continue
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            if name in {"ddof"}:
                if int(value) not in (0, 1):
                    raise IndicatorRegistryError(f"INVALID_PARAMETER:{spec.indicator_id}:{name}")
                continue
            if name in {"multiplier", "step", "max_step", "weight"}:
                if not float(value) > 0:
                    raise IndicatorRegistryError(f"INVALID_PARAMETER:{spec.indicator_id}:{name}")
                continue
            if float(value) < 0:
                raise IndicatorRegistryError(f"INVALID_PARAMETER:{spec.indicator_id}:{name}")
            if name in {"window", "fast", "slow", "signal", "n", "k_period", "d_period",
                        "atr_window", "shift", "annualize"} and float(value) != int(value):
                raise IndicatorRegistryError(f"INVALID_PARAMETER:{spec.indicator_id}:{name}")


def _require_inputs(spec: IndicatorSpec, data: PriceInput) -> None:
    available = {
        "close": data.close is not None,
        "high": data.high is not None,
        "low": data.low is not None,
        "open": data.open_price is not None,
        "volume": data.volume is not None,
        "amount": data.amount is not None,
        "prev_close": data.prev_close is not None,
    }
    for name in spec.inputs:
        if not available.get(name, False):
            raise IndicatorRegistryError(f"DATA_DEPENDENCY_NOT_MET:{spec.indicator_id}:{name}")


# --------------------------------------------------------------------------
# 默认注册表内容
# --------------------------------------------------------------------------

_OHLCV = ("close", "high", "low", "volume")
_OHLC = ("close", "high", "low")
_OHLCV_AMOUNT = ("close", "high", "low", "volume", "amount")
_OHLC_OPEN = ("close", "high", "low", "open")


def _spec(
    indicator_id: str, version: str, family: str, display_name: str, reuse: str,
    outputs: Sequence[str], params: Mapping[str, Any], inputs: Sequence[str],
    *, price_mode: str = "CALLER_DECLARED", frequency: str = "DAILY",
    available_at_rule: str = "COMPLETED_BAR_CLOSE", warmup_bars: int = 1,
    lookback: int = 0, missing_policy: str = "NAN_PROPAGATES_SEGMENT_BREAK",
    unit: str = "PRICE", cross_sectional: bool = False,
    requires_extra_data: Sequence[str] = (), aliases: Sequence[str] = (),
    formula_note: str = "", implementation_path: str = "",
    evidence: Optional[Mapping[str, bool]] = None,
) -> IndicatorSpec:
    return IndicatorSpec(
        indicator_id=indicator_id, version=version, family=family, display_name=display_name,
        reuse=reuse, outputs=tuple(outputs), params=dict(params), inputs=tuple(inputs),
        price_mode=price_mode, frequency=frequency, available_at_rule=available_at_rule,
        warmup_bars=int(warmup_bars), lookback=int(lookback), missing_policy=missing_policy,
        unit=unit, cross_sectional=bool(cross_sectional),
        requires_extra_data=tuple(requires_extra_data), aliases=tuple(aliases),
        formula_note=formula_note, implementation_path=implementation_path,
        evidence=dict(evidence or {}),
    )


def default_registry() -> IndicatorRegistry:
    """构造默认注册表：V1 复用项 + V2 新增项。

    实现 hash 在 ``to_dict()`` 中由源码计算；此处只登记实现与契约。
    """
    registry = IndicatorRegistry()
    v1_path = "src/chanlun_trader/engine/indicators_v1.py"
    v2_path = "src/chanlun_trader/engine/indicators_v2.py"

    # ---------------- V1 复用项（REUSE_AS_IS） ----------------
    registry.register(
        _spec("MACD", "MACD_V1", "均线与趋势", "MACD（V1 口径）", REUSE_AS_IS,
              ["ema_fast", "ema_slow", "dif", "dea", "hist"],
              {"fast": 12, "slow": 26, "signal": 9}, ("close",),
              warmup_bars=34, lookback=34, unit="PRICE",
              formula_note="HIST = 2*(DIF-DEA)；EMA 段内首值=首个合格 close；warmup=slow+signal-1",
              implementation_path=v1_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "CAUSALITY_VALIDATED": True,
                        "REGISTERED": True, "ENTRYPOINT_WIRED": True,
                        "EXECUTION_BEHAVIOR_VALIDATED": True, "ACCOUNTING_VALIDATED": True}),
        _adapter(_v1_macd, ["ema_fast", "ema_slow", "dif", "dea", "hist"]),
    )
    registry.register(
        _spec("KDJ", "KDJ_V1", "动量与震荡", "KDJ(9,3,3)（V1 口径）", REUSE_AS_IS,
              ["rsv", "k", "d", "j"],
              {"n": 9, "k_period": 3, "d_period": 3}, _OHLC,
              warmup_bars=9, lookback=9, unit="INDEX_0_100",
              formula_note="段首 K=D=50；HHV==LLV 结束该段；J=3K-2D 不裁剪",
              implementation_path=v1_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "CAUSALITY_VALIDATED": True,
                        "REGISTERED": True, "ENTRYPOINT_WIRED": True,
                        "EXECUTION_BEHAVIOR_VALIDATED": True, "ACCOUNTING_VALIDATED": True}),
        _adapter(_v1_kdj, ["rsv", "k", "d", "j"]),
    )

    # ---------------- 家族一：均线与趋势 ----------------
    registry.register(
        _spec("MA", "MA_ARITHMETIC_V1", "均线与趋势", "算术移动平均", NEW_IN_V2,
              ["ma"], {"window": 20, "price": "close"}, ("close",),
              warmup_bars=20, lookback=20, aliases=("MA", "SMA", "MOVING_AVERAGE"),
              formula_note="算术均值；窗口含当前 bar；窗口内全部合格才输出",
              implementation_path=v2_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "REGISTERED": True}),
        _adapter(v2.ma_arithmetic, ["ma"]),
    )
    registry.register(
        _spec("SMA_TDX", "SMA_TDX_V1", "均线与趋势", "通达信 SMA(X,N,M)", NEW_IN_V2,
              ["sma_tdx"], {"window": 9, "weight": 1, "price": "close"}, ("close",),
              warmup_bars=9, lookback=9,
              formula_note="Y=(M*X+(N-M)*Y_prev)/N；与算术移动平均是不同公式",
              implementation_path=v2_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "REGISTERED": True}),
        _adapter(v2.sma_tdx, ["sma_tdx"]),
    )
    registry.register(
        _spec("EMA", "EMA_V1", "均线与趋势", "指数移动平均", NEW_IN_V2,
              ["ema"], {"window": 12, "price": "close"}, ("close",),
              warmup_bars=12, lookback=12,
              formula_note="alpha=2/(N+1)；段内首值=首个合格输入值",
              implementation_path=v2_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "REGISTERED": True}),
        _adapter(v2.ema, ["ema"]),
    )
    registry.register(
        _spec("WMA", "WMA_V1", "均线与趋势", "线性加权移动平均", NEW_IN_V2,
              ["wma"], {"window": 20, "price": "close"}, ("close",),
              warmup_bars=20, lookback=20,
              formula_note="权重 1..N，最近 bar 权重最大",
              implementation_path=v2_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "REGISTERED": True}),
        _adapter(v2.wma, ["wma"]),
    )
    registry.register(
        _spec("RMA", "RMA_WILDER_V1", "均线与趋势", "Wilder 平滑（RMA）", NEW_IN_V2,
              ["rma"], {"window": 14, "price": "close"}, ("close",),
              warmup_bars=14, lookback=14, aliases=("RMA", "WILDER"),
              formula_note="alpha=1/N；段首=前 N 根算术均值；与 EMA 是不同公式",
              implementation_path=v2_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "REGISTERED": True}),
        _adapter(v2.rma_wilder, ["rma"]),
    )
    registry.register(
        _spec("DEMA", "DEMA_V1", "均线与趋势", "双重指数移动平均", NEW_IN_V2,
              ["dema", "ema1", "ema2"], {"window": 20, "price": "close"}, ("close",),
              warmup_bars=39, lookback=39,
              formula_note="DEMA = 2*EMA - EMA(EMA)",
              implementation_path=v2_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "REGISTERED": True}),
        _adapter(v2.dema, ["dema", "ema1", "ema2"]),
    )
    registry.register(
        _spec("TEMA", "TEMA_V1", "均线与趋势", "三重指数移动平均", NEW_IN_V2,
              ["tema", "ema1", "ema2", "ema3"], {"window": 20, "price": "close"}, ("close",),
              warmup_bars=58, lookback=58,
              formula_note="TEMA = 3*E1 - 3*E2 + E3",
              implementation_path=v2_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "REGISTERED": True}),
        _adapter(v2.tema, ["tema", "ema1", "ema2", "ema3"]),
    )
    registry.register(
        _spec("MACD_HIST_RAW", "MACD_HIST_RAW_V1", "均线与趋势", "MACD 柱（DIF-DEA 方言）", NEW_IN_V2,
              ["dif", "dea", "hist_raw"], {"fast": 12, "slow": 26, "signal": 9}, ("close",),
              warmup_bars=34, lookback=34, unit="PRICE",
              formula_note="HIST_RAW = DIF - DEA（不含 2 倍系数）；与 MACD_V1 的 2*(DIF-DEA) 并存",
              implementation_path=v2_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "REGISTERED": True}),
        _adapter(v2.macd_hist_raw, ["dif", "dea", "hist_raw"]),
    )
    registry.register(
        _spec("DMI", "DMI_ADX_V1", "均线与趋势", "DMI / ADX", NEW_IN_V2,
              ["plus_di", "minus_di", "adx", "atr"], {"window": 14}, _OHLC,
              warmup_bars=28, lookback=28, unit="INDEX_0_100",
              formula_note="Wilder 原始口径：TR/+DM/-DM 用 RMA(N) 平滑；ADX=RMA(DX,N)",
              implementation_path=v2_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "REGISTERED": True}),
        _adapter(v2.dmi_adx, ["plus_di", "minus_di", "adx", "atr"]),
    )
    registry.register(
        _spec("SAR", "SAR_V1", "均线与趋势", "抛物线 SAR", NEW_IN_V2,
              ["sar", "sar_trend"], {"step": 0.02, "max_step": 0.2}, _OHLC,
              warmup_bars=2, lookback=2, unit="PRICE",
              formula_note="AF 从 step 起、创新极值递增、上限 max_step；含价格约束与反转",
              implementation_path=v2_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "REGISTERED": True}),
        _adapter(v2.sar, ["sar", "sar_trend"]),
    )

    # ---------------- 家族二：动量与震荡 ----------------
    registry.register(
        _spec("RSI", "RSI_V1", "动量与震荡", "Wilder RSI", NEW_IN_V2,
              ["rsi"], {"window": 14, "price": "close"}, ("close",),
              warmup_bars=15, lookback=15, unit="INDEX_0_100",
              formula_note="首值=前 N 个变动均值；Wilder 递推；无涨跌->50，仅涨->100，仅跌->0",
              implementation_path=v2_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "REGISTERED": True}),
        _adapter(v2.rsi, ["rsi"]),
    )
    registry.register(
        _spec("CCI", "CCI_V1", "动量与震荡", "CCI", NEW_IN_V2,
              ["cci", "typical_price"], {"window": 14}, _OHLC,
              warmup_bars=14, lookback=14,
              formula_note="(TP-SMA(TP,N))/(0.015*平均绝对偏差)，TP=(H+L+C)/3",
              implementation_path=v2_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "REGISTERED": True}),
        _adapter(v2.cci, ["cci", "typical_price"]),
    )
    registry.register(
        _spec("WILLIAMS_R", "WILLIAMS_R_V1", "动量与震荡", "Williams %R", NEW_IN_V2,
              ["wr", "hhv", "llv"], {"window": 14}, _OHLC,
              warmup_bars=14, lookback=14, unit="INDEX_0_100",
              aliases=("WR",), formula_note="0~100 表达：(HHV(H,N)-C)/(HHV-LLV)*100，不返回负值形式",
              implementation_path=v2_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "REGISTERED": True}),
        _adapter(v2.williams_r, ["wr", "hhv", "llv"]),
    )
    registry.register(
        _spec("ROC", "ROC_V1", "动量与震荡", "变动率 ROC", NEW_IN_V2,
              ["roc", "ref_close"], {"window": 12, "price": "close"}, ("close",),
              warmup_bars=13, lookback=13, unit="PERCENT",
              formula_note="(C/REF(C,N)-1)*100",
              implementation_path=v2_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "REGISTERED": True}),
        _adapter(v2.roc, ["roc", "ref_close"]),
    )
    registry.register(
        _spec("MTM", "MTM_V1", "动量与震荡", "动量 MTM/MOM", NEW_IN_V2,
              ["mtm", "ref_close"], {"window": 12, "price": "close"}, ("close",),
              warmup_bars=13, lookback=13, unit="PRICE",
              aliases=("MOM",), formula_note="C - REF(C,N)，价格单位",
              implementation_path=v2_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "REGISTERED": True}),
        _adapter(v2.mtm, ["mtm", "ref_close"]),
    )
    registry.register(
        _spec("BIAS", "BIAS_V1", "动量与震荡", "乖离率 BIAS", NEW_IN_V2,
              ["bias", "ma"], {"window": 6, "price": "close"}, ("close",),
              warmup_bars=6, lookback=6, unit="PERCENT",
              formula_note="(C-MA(C,N))/MA(C,N)*100",
              implementation_path=v2_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "REGISTERED": True}),
        _adapter(v2.bias, ["bias", "ma"]),
    )
    registry.register(
        _spec("TRIX", "TRIX_V1", "动量与震荡", "TRIX", NEW_IN_V2,
              ["trix", "trix_ma"], {"window": 12, "signal": 9, "price": "close"}, ("close",),
              warmup_bars=45, lookback=45, unit="PERCENT",
              formula_note="三重 EMA 的单期百分比变动；信号线为 M 期滚动均值",
              implementation_path=v2_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "REGISTERED": True}),
        _adapter(v2.trix, ["trix", "trix_ma"]),
    )
    registry.register(
        _spec("PSY", "PSY_V1", "动量与震荡", "心理线 PSY", NEW_IN_V2,
              ["psy"], {"window": 12, "price": "close"}, ("close",),
              warmup_bars=13, lookback=13, unit="PERCENT",
              formula_note="N 根中上涨根数占比(%)",
              implementation_path=v2_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "REGISTERED": True}),
        _adapter(v2.psy, ["psy"]),
    )

    # ---------------- 家族三：波动与通道 ----------------
    registry.register(
        _spec("TRUE_RANGE", "TRUE_RANGE_V1", "波动与通道", "真实波幅 TR", NEW_IN_V2,
              ["tr"], {}, _OHLC, warmup_bars=1, lookback=1, unit="PRICE",
              aliases=("TR",), formula_note="max(H-L, |H-PC|, |L-PC|)",
              implementation_path=v2_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "REGISTERED": True}),
        _adapter(v2.true_range, ["tr"]),
    )
    registry.register(
        _spec("ATR", "ATR_V1", "波动与通道", "ATR", NEW_IN_V2,
              ["atr", "tr"], {"window": 14}, _OHLC, warmup_bars=14, lookback=14, unit="PRICE",
              formula_note="TR 的 Wilder 平滑；ATR = smoothed_sum / N",
              implementation_path=v2_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "REGISTERED": True}),
        _adapter(v2.atr, ["atr", "tr"]),
    )
    registry.register(
        _spec("NATR", "NATR_V1", "波动与通道", "NATR（ATR 百分比）", NEW_IN_V2,
              ["natr", "atr"], {"window": 14}, _OHLC, warmup_bars=14, lookback=14, unit="PERCENT",
              formula_note="ATR/C*100",
              implementation_path=v2_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "REGISTERED": True}),
        _adapter(v2.natr, ["natr", "atr"]),
    )
    registry.register(
        _spec("ROLLING_VOLATILITY", "ROLLING_VOLATILITY_V1", "波动与通道", "滚动波动率（收益率）", NEW_IN_V2,
              ["volatility", "return"], {"window": 20, "price": "close", "annualize": 0, "ddof": 1}, ("close",),
              warmup_bars=21, lookback=21, unit="RATIO",
              formula_note="基于收益率的标准差；annualize=0 为单期，252 为年化；与 BOLL 带宽口径不同",
              implementation_path=v2_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "REGISTERED": True}),
        _adapter(v2.rolling_volatility, ["volatility", "return"]),
    )
    registry.register(
        _spec("BOLLINGER", "BOLLINGER_V1", "波动与通道", "布林带 BOLL", NEW_IN_V2,
              ["middle", "upper", "lower", "bandwidth", "percent_b", "std"],
              {"window": 20, "multiplier": 2.0, "ddof": 0, "price": "close"}, ("close",),
              warmup_bars=20, lookback=20, unit="PRICE",
              aliases=("BOLL", "BBANDS"),
              formula_note="中轨=算术均线；上下轨=中轨±k*STD；ddof 默认 0（总体），可显式切样本",
              implementation_path=v2_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "REGISTERED": True}),
        _adapter(v2.bollinger, ["middle", "upper", "lower", "bandwidth", "percent_b", "std"]),
    )
    registry.register(
        _spec("DONCHIAN", "DONCHIAN_V1", "波动与通道", "Donchian 通道", NEW_IN_V2,
              ["upper", "lower", "middle"], {"window": 20, "shift": 0}, _OHLC,
              warmup_bars=20, lookback=20, unit="PRICE",
              formula_note="upper=HHV(H,N)，lower=LLV(L,N)；shift=1 表示参考前 N 根（不含当根）",
              implementation_path=v2_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "REGISTERED": True}),
        _adapter(v2.donchian, ["upper", "lower", "middle"]),
    )
    registry.register(
        _spec("KELTNER", "KELTNER_V1", "波动与通道", "Keltner 通道", NEW_IN_V2,
              ["upper", "middle", "lower", "atr"],
              {"window": 20, "atr_window": 10, "multiplier": 2.0}, _OHLC,
              warmup_bars=20, lookback=20, unit="PRICE",
              formula_note="中轨=EMA(close)；上下轨=中轨±k*ATR",
              implementation_path=v2_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "REGISTERED": True}),
        _adapter(v2.keltner, ["upper", "middle", "lower", "atr"]),
    )

    # ---------------- 家族四：量价与资金代理 ----------------
    registry.register(
        _spec("VOLUME_MA", "VOLUME_MA_V1", "量价与资金代理", "成交量均线", NEW_IN_V2,
              ["volume_ma"], {"window": 20}, ("volume",), warmup_bars=20, lookback=20,
              unit="SHARES", formula_note="成交量算术均线",
              implementation_path=v2_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "REGISTERED": True}),
        _adapter(v2.volume_ma, ["volume_ma"]),
    )
    registry.register(
        _spec("AMOUNT_MA", "AMOUNT_MA_V1", "量价与资金代理", "成交额均线", NEW_IN_V2,
              ["amount_ma"], {"window": 20}, ("amount",), warmup_bars=20, lookback=20,
              unit="CURRENCY", formula_note="成交额算术均线；与成交量是不同单位",
              implementation_path=v2_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "REGISTERED": True}),
        _adapter(v2.amount_ma, ["amount_ma"]),
    )
    registry.register(
        _spec("RVOL_INCL_CURRENT", "RVOL_INCL_CURRENT_V1", "量价与资金代理",
              "相对量（分母含当前 bar）", NEW_IN_V2,
              ["rvol", "volume_ma"], {"window": 20}, ("volume",), warmup_bars=20, lookback=20,
              unit="RATIO", formula_note="V / MA(V,N)，分母含当前 bar",
              implementation_path=v2_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "REGISTERED": True}),
        _adapter(v2.rvol_incl_current, ["rvol", "volume_ma"]),
    )
    registry.register(
        _spec("RVOL_PRIOR", "RVOL_PRIOR_V1", "量价与资金代理",
              "相对量（分母为前 N 根）", NEW_IN_V2,
              ["rvol", "volume_ma_prior"], {"window": 20}, ("volume",), warmup_bars=21, lookback=21,
              unit="RATIO", formula_note="V / MA(V[t-N..t-1])，分母不含当前 bar；与 RVOL_INCL_CURRENT 不同口径",
              implementation_path=v2_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "REGISTERED": True}),
        _adapter(v2.rvol_prior, ["rvol", "volume_ma_prior"]),
    )
    registry.register(
        _spec("OBV", "OBV_V1", "量价与资金代理", "OBV", NEW_IN_V2,
              ["obv"], {}, ("close", "volume"), warmup_bars=1, lookback=1, unit="SHARES",
              formula_note="OBV_t = OBV_{t-1} + sign(C_t-C_{t-1})*V_t；段首为 0",
              implementation_path=v2_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "REGISTERED": True}),
        _adapter(v2.obv, ["obv"]),
    )
    registry.register(
        _spec("MFI", "MFI_V1", "量价与资金代理", "资金流量指标 MFI", NEW_IN_V2,
              ["mfi", "typical_price"], {"window": 14}, _OHLCV, warmup_bars=15, lookback=15,
              unit="INDEX_0_100",
              formula_note="基于典型价与成交量的公式代理，不是实际主力净买卖",
              implementation_path=v2_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "REGISTERED": True}),
        _adapter(v2.mfi, ["mfi", "typical_price"]),
    )
    registry.register(
        _spec("ACCUMULATION_DISTRIBUTION", "ACCUMULATION_DISTRIBUTION_V1", "量价与资金代理",
              "A/D 线", NEW_IN_V2,
              ["ad_line", "money_flow_volume"], {}, _OHLCV, warmup_bars=1, lookback=1, unit="SHARES",
              aliases=("AD",), formula_note="累积 ((C-L)-(H-C))/(H-L)*V；窗口内高低相同当根贡献 0",
              implementation_path=v2_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "REGISTERED": True}),
        _adapter(v2.accumulation_distribution, ["ad_line", "money_flow_volume"]),
    )
    registry.register(
        _spec("CHAIKIN_MONEY_FLOW", "CHAIKIN_MONEY_FLOW_V1", "量价与资金代理", "CMF", NEW_IN_V2,
              ["cmf", "mfv_sum", "volume_sum"], {"window": 20}, _OHLCV, warmup_bars=20, lookback=20,
              unit="RATIO", formula_note="sum(MFV,N)/sum(V,N)；成交量合计为零 -> NaN",
              implementation_path=v2_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "REGISTERED": True}),
        _adapter(v2.chaikin_money_flow, ["cmf", "mfv_sum", "volume_sum"]),
    )
    registry.register(
        _spec("PVT", "PVT_V1", "量价与资金代理", "PVT", NEW_IN_V2,
              ["pvt"], {}, ("close", "volume"), warmup_bars=1, lookback=1, unit="SHARES",
              formula_note="累积 pct_change(C)*V",
              implementation_path=v2_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "REGISTERED": True}),
        _adapter(v2.pvt, ["pvt"]),
    )
    registry.register(
        _spec("VWAP_SESSION_PROXY", "VWAP_SESSION_PROXY_V1", "量价与资金代理",
              "session 成交均价代理（amount/volume）", NEW_IN_V2,
              ["vwap_session_proxy"], {}, ("amount", "volume"), warmup_bars=1, lookback=1,
              unit="PRICE",
              formula_note="amount/volume；不是真实 VWAP，与 HLC3、滚动 VWAP 三者不同名",
              implementation_path=v2_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "REGISTERED": True}),
        _adapter(v2.vwap_session_proxy, ["vwap_session_proxy"]),
    )
    registry.register(
        _spec("HLC3", "HLC3_V1", "量价与资金代理", "HLC3 典型价代理", NEW_IN_V2,
              ["hlc3"], {}, _OHLC, warmup_bars=1, lookback=1, unit="PRICE",
              formula_note="(H+L+C)/3；与成交均价、VWAP 是不同口径",
              implementation_path=v2_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "REGISTERED": True}),
        _adapter(v2.hlc3, ["hlc3"]),
    )
    registry.register(
        _spec("ROLLING_VWAP", "ROLLING_VWAP_V1", "量价与资金代理", "滚动 VWAP", NEW_IN_V2,
              ["rolling_vwap"], {"window": 20}, ("amount", "volume"), warmup_bars=20, lookback=20,
              unit="PRICE", formula_note="sum(amount,N)/sum(volume,N)",
              implementation_path=v2_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "REGISTERED": True}),
        _adapter(v2.rolling_vwap, ["rolling_vwap"]),
    )
    registry.register(
        _spec("TURNOVER_RATE", "TURNOVER_RATE_V1", "量价与资金代理", "换手率", NEW_IN_V2,
              ["turnover_rate"], {"window": 1}, ("volume",), warmup_bars=1, lookback=1,
              unit="PERCENT", requires_extra_data=("float_shares",),
              formula_note="sum(volume,N)/流通股本*100；缺历史流通股本时明确拒绝",
              implementation_path=v2_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "REGISTERED": True}),
        _adapter(v2.turnover_rate, ["turnover_rate"]),
    )

    # ---------------- 家族五：价格结构 ----------------
    registry.register(
        _spec("PRICE_EXTREMES", "PRICE_EXTREMES_V1", "价格结构", "区间高低点", NEW_IN_V2,
              ["hhv", "llv"], {"window": 20}, _OHLC, warmup_bars=20, lookback=20, unit="PRICE",
              aliases=("HHV", "LLV"), formula_note="HHV(H,N)/LLV(L,N)，窗口含当前 bar",
              implementation_path=v2_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "REGISTERED": True}),
        _adapter(v2.price_extremes, ["hhv", "llv"]),
    )
    registry.register(
        _spec("PRIOR_BREAKOUT", "PRIOR_BREAKOUT_V1", "价格结构", "前 N 根突破", NEW_IN_V2,
              ["prior_high", "prior_low", "break_up", "break_down"], {"window": 20}, _OHLC,
              warmup_bars=21, lookback=21, unit="PRICE",
              formula_note="参考前 N 根（不含当根）的区间高低；当根突破可表达且无前视",
              implementation_path=v2_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "REGISTERED": True}),
        _adapter(v2.prior_breakout, ["prior_high", "prior_low", "break_up", "break_down"]),
    )
    registry.register(
        _spec("DRAWDOWN_FROM_PEAK", "DRAWDOWN_FROM_PEAK_V1", "价格结构", "自窗口峰值回撤", NEW_IN_V2,
              ["drawdown", "peak"], {"window": 60, "price": "close"}, ("close",),
              warmup_bars=60, lookback=60, unit="RATIO",
              formula_note="C/HHV(C,N)-1（负值或 0）",
              implementation_path=v2_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "REGISTERED": True}),
        _adapter(v2.drawdown_from_peak, ["drawdown", "peak"]),
    )
    registry.register(
        _spec("BAR_SHAPE", "BAR_SHAPE_V1", "价格结构", "K 线形态比例", NEW_IN_V2,
              ["body_ratio", "upper_shadow_ratio", "lower_shadow_ratio", "range_pct", "gap_pct"],
              {"window": 20}, _OHLC_OPEN, warmup_bars=1, lookback=1, unit="RATIO",
              formula_note="实体/上下影线比例相对当根振幅归一；振幅为 0 时比例为 0",
              implementation_path=v2_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "REGISTERED": True}),
        _adapter(v2.bar_shape, ["body_ratio", "upper_shadow_ratio", "lower_shadow_ratio",
                                "range_pct", "gap_pct"]),
    )
    registry.register(
        _spec("STREAK", "STREAK_V1", "价格结构", "连续上涨/下跌根数", NEW_IN_V2,
              ["up_streak", "down_streak"], {"price": "close"}, ("close",),
              warmup_bars=1, lookback=1, unit="BARS",
              formula_note="平盘重置为 0；结构时刻=当根收盘，不回填",
              implementation_path=v2_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "REGISTERED": True}),
        _adapter(v2.streak, ["up_streak", "down_streak"]),
    )

    # ---------------- 家族六：统计与截面组合 ----------------
    registry.register(
        _spec("HISTORICAL_RETURN", "HISTORICAL_RETURN_V1", "统计与截面组合", "历史收益", NEW_IN_V2,
              ["return", "ref_close"], {"window": 20, "price": "close", "log": False}, ("close",),
              warmup_bars=21, lookback=21, unit="RATIO",
              formula_note="C/REF(C,N)-1；log=True 时为对数收益",
              implementation_path=v2_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "REGISTERED": True}),
        _adapter(v2.historical_return, ["return", "ref_close"]),
    )
    registry.register(
        _spec("ROLLING_SLOPE", "ROLLING_SLOPE_V1", "统计与截面组合", "线性斜率", NEW_IN_V2,
              ["slope"], {"window": 20, "price": "close"}, ("close",),
              warmup_bars=20, lookback=20, unit="PRICE_PER_BAR",
              formula_note="窗口内价格对时间的最小二乘斜率",
              implementation_path=v2_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "REGISTERED": True}),
        _adapter(v2.rolling_slope, ["slope"]),
    )
    registry.register(
        _spec("TIME_SERIES_ZSCORE", "TIME_SERIES_ZSCORE_V1", "统计与截面组合",
              "时序标准化 z-score", NEW_IN_V2,
              ["ts_zscore", "mean", "std"], {"window": 60, "price": "close", "ddof": 1}, ("close",),
              warmup_bars=60, lookback=60, unit="RATIO",
              formula_note="(C-MA(C,N))/STD(C,N)；这是**时序**标准化，与截面 rank/zscore 不同口径",
              implementation_path=v2_path,
              evidence={"IMPLEMENTED": True, "FORMULA_VALIDATED": True, "REGISTERED": True}),
        _adapter(v2.time_series_zscore, ["ts_zscore", "mean", "std"]),
    )

    return registry


def registered_indicator_ids(registry: Optional[IndicatorRegistry] = None) -> List[str]:
    reg = registry or default_registry()
    return [spec.indicator_id for spec in reg.specs()]
