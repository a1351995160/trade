"""已固定快照的限定接入；历史身份 UNVERIFIED，历史批次入口禁用。"""
from __future__ import annotations
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping
import pandas as pd
from chanlun_trader.engine.asof import MarketDataStore  # noqa: E402
from chanlun_trader.engine.engine import BacktestEngineV2, EngineConfig  # noqa: E402
from chanlun_trader.engine.universe import UniverseService  # noqa: E402
from chanlun_trader.research.data_router import ResearchDataRouter  # noqa: E402
from chanlun_trader.research.guard import RESEARCH_END, ResearchDataAccessGuard
from chanlun_trader.research.strategy_semantic import StrategyCandidateCompilerV2
from chanlun_trader.research.strategy_validation import ValidationGovernanceError, stable_hash



def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def ts_for_date(d: int, hour: int, minute: int = 0, second: int = 0) -> pd.Timestamp:
    text = str(int(d))
    return pd.Timestamp(
        f"{text[:4]}-{text[4:6]}-{text[6:]} {hour:02d}:{minute:02d}:{second:02d}",
        tz="Asia/Shanghai",
    )


def parse_date(value: Any) -> int:
    text = str(value).replace("-", "")[:8]
    return int(text)


class PITStateMap:
    """Load normalized status evidence once and fail closed for unknown rows."""

    def __init__(self, root: Path, calendar: list[int]):
        self.root = root
        self.calendar = set(calendar)
        self.incomplete_symbols: set[str] = set()
        self.st_blocked: set[tuple[str, int]] = set()
        self.susp_blocked: set[tuple[str, int]] = set()
        self.st_unknown = 0
        self.susp_unknown = 0
        self.covered = {"st": set(), "susp": set()}
        self._load("st_state", "NORMAL", self.st_blocked, "st")
        self._load("suspension_state", "TRADING", self.susp_blocked, "susp")

    def _load(self, folder: str, expected: str, blocked: set[tuple[str, int]], kind: str) -> None:
        path_root = self.root / folder
        for path in sorted(path_root.glob("symbol=*.jsonl")):
            symbol = path.stem.removeprefix("symbol=").replace("_", ".")
            dates: set[int] = set()
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    d = parse_date(row.get("effective_from") or row.get("trade_date"))
                    dates.add(d)
                    status = str(row.get("status", "UNKNOWN"))
                    if status != expected:
                        blocked.add((symbol, d))
                        if status == "UNKNOWN":
                            if kind == "st":
                                self.st_unknown += 1
                            else:
                                self.susp_unknown += 1
            if dates != self.calendar:
                self.incomplete_symbols.add(symbol)
            self.covered[kind].update((symbol, d) for d in dates)

    def tradable(self, symbol: str, d: int) -> tuple[bool, str]:
        if symbol in self.incomplete_symbols:
            return False, "PIT_STATUS_ROW_MISSING"
        key = (symbol, int(d))
        if int(d) not in self.calendar or any(key not in source for source in self.covered.values()):
            return False, "PIT_STATUS_ROW_MISSING"
        if key in self.st_blocked:
            return False, "PIT_ST_NOT_NORMAL"
        if key in self.susp_blocked:
            return False, "PIT_SUSPENSION_NOT_TRADING"
        return True, "PIT_STATUS_EXPLICIT_NORMAL_TRADING"


def load_universe_sets(root: Path, dates: list[int], symbols: set[str]) -> dict[int, set[str]]:
    payload = json.loads((root / "data/research/security_state/normalized/security_master_v2/records.json").read_text(encoding="utf-8"))
    master = {str(row["symbol"]): row for row in payload}
    out: dict[int, set[str]] = {}
    for d in dates:
        selected: set[str] = set()
        for symbol in symbols:
            row = master.get(symbol)
            if not row or row.get("exchange") not in {"SH", "SZ"}:
                continue
            list_date = parse_date(row["list_date"]) if row.get("list_date") else 99991231
            delist_date = parse_date(row["delist_date"]) if row.get("delist_date") else 99991231
            if list_date <= d <= delist_date:
                selected.add(symbol)
        out[d] = selected
    return out


def load_market_index(root: Path, guard: ResearchDataAccessGuard, start: int, end: int,
                      *, required: bool = False) -> dict[int, float]:
    # 基准读取尚未接入显式数据合同；可选诊断为 UNKNOWN，必需门禁拒绝。
    if required:
        raise ValueError("R1_BENCHMARK_REQUIRED_NOT_AVAILABLE")
    return {}


def market_regimes(index_close: Mapping[int, float]) -> dict[int, str]:
    dates = sorted(index_close)
    close = pd.Series({int(d): float(index_close[d]) for d in dates}).sort_index()
    ma60 = close.rolling(60, min_periods=20).mean()
    return {int(d): ("UNKNOWN" if pd.isna(ma60.loc[d]) else ("BULL" if close.loc[d] >= ma60.loc[d] else "BEAR")) for d in dates}


def build_store(daily: pd.DataFrame) -> MarketDataStore:
    store = MarketDataStore(feature_price_mode="raw")
    fields = ["open", "high", "low", "close", "volume", "amount", "prev_close"]
    for symbol, group in daily.groupby("symbol", sort=False):
        store.add_daily_raw(str(symbol), group.set_index("date")[fields].sort_index())
    return store


def load_events(root: Path, router: ResearchDataRouter, event_ids: set[str]) -> dict[tuple[int, str], dict[str, dict[str, Any]]]:
    result: dict[tuple[int, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    for event_id in sorted(event_ids):
        path = root / "data/research/event_store_repaired" / f"{event_id}_v2.parquet"
        if not path.exists():
            raise ValidationGovernanceError(f"missing repaired event store: {event_id}")
        frame = router.reader.read_parquet(
            path,
            columns=["symbol", "event_time", "event_id", "available_at_ts"],
            date_column="event_time",
            start_date=20220801,
            end_date=RESEARCH_END,
        )
        for row in frame.to_dict("records"):
            d = int(row["event_time"])
            event = {
                "event_id": str(row["event_id"]),
                "event_time": d,
                "available_at_ts": str(row["available_at_ts"]),
            }
            result[(d, str(row["symbol"]))][event["event_id"]] = event
    return result


def make_engine(store: MarketDataStore, calendar: list[int], universe: Mapping[int, Iterable[str]],
                index_close: Mapping[int, float], policy: Any, candidate_hash: str,
                initial_cash: float | None = None, fee_mult: float = 1.0,
                stamp_mult: float = 1.0, slip_mult: float = 1.0) -> BacktestEngineV2:
    service = UniverseService()
    service.load_pit_sets({int(d): set(values) for d, values in universe.items() if int(d) in set(calendar)})
    config = EngineConfig(
        initial_cash=float(policy.initial_cash if initial_cash is None else initial_cash),
        max_positions=int(policy.max_positions),
        max_position_weight=1.0 / int(policy.max_positions),
        commission_rate=float(policy.commission_rate) * fee_mult,
        min_commission=float(policy.min_commission) if fee_mult >= 1.0 else 0.0,
        stamp_tax_rate=float(policy.stamp_tax_rate) * stamp_mult,
        slippage_bps=float(policy.slippage_bps) * slip_mult,
        lot_size=int(policy.lot_size),
        max_holding_days=0,
        mode="DAILY",
        feature_price_mode="raw",
        start_date=min(calendar),
        end_date=max(calendar),
        enable_index_filter=False,
        index_filter_enabled=False,
        strategy_hash=candidate_hash,
        data_manifest_hash=stable_hash({symbol: frame.to_dict(orient="split")
            for symbol, frame in sorted(store.daily_raw.items())}),
        universe_version_hash=stable_hash({int(d): sorted(values) for d, values in universe.items()}),
        execution_model_version=policy.transaction_model,
        persist_run_manifest=False,
    )
    return BacktestEngineV2(store=store, calendar_days=calendar, config=config,
                            index_closes=dict(index_close), universe=service, seed=42,
                            source_identity=("UNKNOWN", True))


def factor_row_values(row: Any, factor_ids: list[str]) -> dict[str, float]:
    return {factor_id: float(getattr(row, factor_id)) for factor_id in factor_ids}


def run(*args, **kwargs):
    raise RuntimeError("HISTORICAL_EXECUTION_DISABLED:run")


def main(*args, **kwargs):
    raise RuntimeError("HISTORICAL_EXECUTION_DISABLED:main")


def run_candidate(*args, **kwargs):
    raise RuntimeError("HISTORICAL_EXECUTION_DISABLED:run_candidate")


def verify_freezes(*args, **kwargs):
    raise RuntimeError("HISTORICAL_EXECUTION_DISABLED:verify_freezes")


if __name__ == "__main__":
    raise SystemExit(main())
