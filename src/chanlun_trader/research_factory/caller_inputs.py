"""正式调用方的窄输入合同；仅准备已有 DAILY/RAW 缓存，不授予执行许可。"""
from __future__ import annotations

import json
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

from chanlun_trader.engine.time_types import ensure_aware
from chanlun_trader.research.unified_factor import UnifiedFactorRegistry
from .common import stable_hash
from .data_readiness import OHLCVA, derive_requirements, validate_daily_values
from .source_dependencies import SOURCE_ROOT


def prepare_inputs(root, policy, record, corrected, factor_cache, contract):
    if contract is None or contract.reconstruct_candidate().to_dict() != record.to_dict():
        raise ValueError("R1_CALLER_CONTRACT_IDENTITY_MISMATCH")
    payload = contract.provider_candidate_payload()
    if (payload["price_mode"] != "RAW" or set(payload["required_frequency"]) != {"DAILY"}
            or contract.event_ids):
        raise ValueError("R1_CALLER_UNSUPPORTED_CONTRACT")
    # 多因子行级时间尚无逐依赖来源证明；不发明聚合时间规则。
    if len(contract.factor_ids) != 1:
        raise ValueError("R1_CALLER_SHARED_FACTOR_TIME_NOT_VERIFIED")
    registry_identity = contract.factor_event_registry_identities.get("factor_registry", {})
    if "sha256" in registry_identity:
        registry_path = root / str(registry_identity.get("path", ""))
        if (not registry_path.resolve().is_relative_to(root) or registry_path.resolve().is_relative_to(SOURCE_ROOT)
                or hashlib.sha256(registry_path.read_bytes()).hexdigest() != registry_identity["sha256"]):
            raise ValueError("R1_CALLER_REGISTRY_IDENTITY_MISMATCH")
        registry = UnifiedFactorRegistry.read(registry_path)
        if "hash" in registry_identity and registry_identity["hash"] != stable_hash(registry.to_dict()):
            raise ValueError("R1_CALLER_REGISTRY_IDENTITY_MISMATCH")
    else:
        registry = UnifiedFactorRegistry.read(root / "data/research/factor_library_v1/registry.json")
        if registry_identity.get("hash") != stable_hash(registry.to_dict()):
            raise ValueError("R1_CALLER_REGISTRY_IDENTITY_MISMATCH")
    requirements = derive_requirements(contract, policy, registry)
    if requirements["missing"]:
        raise ValueError("R1_CALLER_FACTOR_DEFINITION_MISSING")
    for definition in requirements["factor_definitions"].values():
        if (definition["implementation_status"] != "EXECUTABLE" or definition["required_frequency"] != "DAILY"
                or definition["feature_price_mode"] != "RAW" or definition["available_at_rule"] != "T_CLOSE"):
            raise ValueError("R1_CALLER_FACTOR_CONTRACT_NOT_VERIFIED")
    if any(item.get("mode") == "HARD_GATE" for item in record.signal_predicate.regime_conditions):
        raise ValueError("R1_BENCHMARK_REQUIRED_NOT_AVAILABLE")
    period = contract.research_period_identity
    start, end = int(period["start"]), int(period["end"])
    if not policy.research_start <= start <= end <= policy.research_end:
        raise ValueError("R1_CALLER_POLICY_WINDOW_CONFLICT")
    guard = corrected.legacy.ResearchDataAccessGuard()
    guard.check_range(start, end)
    calendar_path = root / "data/research/security_state/raw/trade_calendar.json"
    calendar = json.loads(calendar_path.read_text(encoding="utf-8"))["trade_dates"]
    if (not calendar or calendar != sorted(set(calendar)) or start not in calendar or end not in calendar
            or any(type(day) is not int for day in calendar)):
        raise ValueError("R1_CALLER_CALENDAR_CONFLICT")
    guard.check_int_iterable(calendar)
    for day in calendar:
        pd.Timestamp(str(day))
    warmup = requirements["warmup_bars"]
    if warmup is None or calendar.index(start) < warmup:
        raise ValueError("R1_CALLER_WARMUP_MISSING")
    all_dates = calendar[calendar.index(start) - warmup:calendar.index(end) + 1]
    exec_calendar = calendar[calendar.index(start):calendar.index(end) + 1]
    router = corrected.legacy.ResearchDataRouter(root)
    reads = []
    router.reader.audit_sink = reads.append
    daily = router.read_daily(all_dates[0], end, columns=["date", "symbol", *OHLCVA,
        "prev_close", "volume_unit", "amount_unit", "price_mode", "available_at"])
    if daily.empty or daily.duplicated(["date", "symbol"]).any():
        raise ValueError("R1_CALLER_EMPTY_OR_DUPLICATE_DAILY")
    try:
        validate_daily_values(daily, [*OHLCVA, "prev_close"])
        if (daily.prev_close <= 0).any():
            raise ValueError("NON_POSITIVE_PREV_CLOSE")
    except ValueError as exc:
        raise ValueError("R1_CALLER_DAILY:" + str(exc)) from exc
    _validate_times(daily, "DAILY", require_close=True)
    master_path = root / "data/research/security_state/normalized/security_master_v2/records.json"
    master = json.loads(master_path.read_text(encoding="utf-8"))
    symbols = [row["symbol"] for row in master]
    if not symbols or len(symbols) != len(set(symbols)) or any(row["exchange"] not in {"SH", "SZ"} for row in master):
        raise ValueError("R1_CALLER_SECURITY_MASTER_CONFLICT")
    universe = corrected.legacy.load_universe_sets(root, all_dates, set(symbols))
    expected = {(day, symbol) for day in all_dates for symbol in universe[day]}
    if set(zip(daily.date, daily.symbol)) != expected:
        raise ValueError("R1_CALLER_DAILY_UNIVERSE_CALENDAR_COVERAGE")
    status_map = corrected.legacy.PITStateMap(root / "data/research/security_state/normalized", exec_calendar)
    if any(not status_map.tradable(symbol, day)[0] for day in exec_calendar for symbol in universe[day]):
        raise ValueError("R1_CALLER_PIT_NOT_EXPLICIT_NORMAL_TRADING")
    factor_cache = Path(factor_cache).resolve()
    if not factor_cache.is_relative_to(root) or factor_cache.is_relative_to(SOURCE_ROOT):
        raise ValueError("R1_CALLER_FACTOR_PATH_CONFLICT")
    import pyarrow.parquet as parquet

    required = {"date", "symbol", "volume", "available_at", *contract.factor_ids}
    if not required.issubset(parquet.read_schema(factor_cache).names):
        raise ValueError("R1_CALLER_FACTOR_CACHE_EVIDENCE_MISSING")
    factors = router.reader.read_parquet(factor_cache, columns=sorted(required),
        date_column="date", start_date=start, end_date=end)
    if factors.duplicated(["date", "symbol"]).any():
        raise ValueError("R1_CALLER_DUPLICATE_FACTORS")
    if set(zip(factors.date, factors.symbol)) != {(day, symbol) for day in exec_calendar for symbol in universe[day]}:
        raise ValueError("R1_CALLER_FACTOR_COVERAGE")
    if not np.isfinite(factors[["volume", *contract.factor_ids]].apply(pd.to_numeric, errors="coerce").to_numpy()).all():
        raise ValueError("R1_CALLER_INVALID_FACTOR_VALUES")
    _validate_times(factors, "FACTOR")
    joined = factors.merge(daily[["date", "symbol", "volume"]], on=["date", "symbol"], validate="one_to_one")
    if not joined.volume_x.eq(joined.volume_y).all():
        raise ValueError("R1_CALLER_FACTOR_DAILY_VOLUME_CONFLICT")
    index_close = corrected.legacy.load_market_index(root, guard, all_dates[0], end)
    input_identity = stable_hash({"contract": contract.content_hash, "policy": policy.to_dict(),
        "registry": registry.to_dict(), "calendar": calendar, "master": master,
        "daily": daily.to_dict("records"), "factors": factors.to_dict("records"),
        "pit": {kind: sorted(rows) for kind, rows in status_map.covered.items()}})
    return {"factor_values": factors, "store": corrected.legacy.build_store(daily),
        "exec_calendar": exec_calendar, "universe": universe, "status_map": status_map,
        "regimes": corrected.legacy.market_regimes(index_close), "events": {}, "index_close": index_close,
        "event_ids": [], "input_diagnostics": {"data_validity": "VALIDATED_DAILY_RAW",
            "input_identity": input_identity,
            "pit_validity": "EXPLICIT_DUAL_SOURCE_NORMAL_TRADING", "benchmark": "UNKNOWN",
            "reads": reads, "warmup_start": all_dates[0], "execution_window": [start, end],
            "contract_hash": contract.content_hash, "calendar_hash": stable_hash(calendar),
            "universe_hash": stable_hash({day: sorted(values) for day, values in universe.items()}),
            "factor_available_at_source": str(factor_cache), "factor_registry_hash": stable_hash(registry.to_dict())}}


def _validate_times(frame, kind, *, require_close=False):
    for row in frame.itertuples(index=False):
        try:
            if pd.isna(row.available_at):
                raise ValueError("missing")
            stamp = ensure_aware(row.available_at)
            if pd.isna(stamp):
                raise ValueError("missing")
            if require_close and stamp > ensure_aware(str(row.date) + " 15:00:00"):
                raise ValueError("late daily bar")
        except (ValueError, TypeError, OverflowError) as exc:
            raise ValueError("R1_CALLER_INVALID_" + kind + "_AVAILABLE_AT") from exc
