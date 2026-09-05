"""V2 BacktestEngineV2 — 混合事件驱动执行内核。

从 Signal 之后全部走事件驱动。研究层批量特征计算不变。
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

import pandas as pd

from .asof import AsOfDataView, MarketDataStore
from .broker import BrokerSimulator
from .event_log import BacktestEventLog
from .events import BacktestEvent
from .fee import ChinaAStockFeeModel
from .fill import DailyBarFillModel, FiveMinuteFillModel
from .ledger import PortfolioLedger
from .order import Order, TimeInForce
from .order_manager import OrderManager
from .portfolio_exit import PortfolioExitDecision
from .risk import RiskConfig, RiskManager
from .security_state import ChinaPriceLimitModel, SecurityMaster, SuspensionModel
from .signal import ExecutionPolicy, OrderIntent, PortfolioTarget, Signal, Side, TargetType
from .sizing import FixedSlotSizer, LotSizeModel, PositionSizer
from .slippage import FixedBpsSlippage
from .time_types import EventKind, TradingCalendar, TradingClock, ensure_aware, date_key
from ..research.run_manifest import build_run_manifest, stable_hash, write_immutable_run_manifest


@dataclass
class EngineConfig:
    initial_cash: float = 1_000_000.0
    max_positions: int = 10
    max_position_weight: float = 0.10
    commission_rate: float = 0.00025
    min_commission: float = 5.0
    stamp_tax_rate: float = 0.0005
    slippage_bps: float = 0.001
    lot_size: int = 100
    corporate_action_guard: Any = None
    max_holding_days: int = 60
    mode: str = "DAILY"          # DAILY | 5MIN
    partial_fill: bool = True
    fill_model: Any = None          # 可选自定义 FillModel（默认按 mode 选择）
    feature_price_mode: str = "qfq"
    start_date: Optional[int] = None
    end_date: Optional[int] = None
    enable_index_filter: bool = True
    index_filter_enabled: bool = True
    strategy_hash: str = ""
    data_manifest_hash: str = ""
    universe_version_hash: str = ""
    feature_version_hash: str = ""
    label_version_hash: str = ""
    execution_model_version: str = "engine-v2"
    calendar_version: str = ""
    persist_run_manifest: bool = True
    run_manifest_root: str = "data/research/runs"


@dataclass
class BacktestRunContext:
    engine_version: str = "2.0.0"
    data_version: str = "local"
    strategy_version: str = ""
    config_hash: str = ""
    random_seed: int = 0
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    universe_version: str = ""
    fee_model: str = "ChinaAStockFeeModel"
    slippage_model: str = "FixedBpsSlippage"
    fill_model: str = "DailyBarFillModel"
    risk_model: str = "RiskManagerV2"
    mode: str = "DAILY"
    code_commit: str = "UNKNOWN"
    dirty_worktree: bool = False
    strategy_hash: str = ""
    data_manifest_hash: str = "UNSPECIFIED"
    universe_version_hash: str = "UNSPECIFIED"
    feature_version_hash: str = "UNSPECIFIED"
    label_version_hash: str = "UNSPECIFIED"
    execution_model_version: str = "UNSPECIFIED"
    fee_model_version: str = "UNSPECIFIED"
    slippage_model_version: str = "UNSPECIFIED"
    calendar_version: str = "UNSPECIFIED"
    manifest_hash: str = ""
    reproducibility_status: str = "PARTIAL"

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class BacktestEngineV2:
    """混合事件驱动内核。"""

    def __init__(self, store: MarketDataStore, calendar_days: Iterable[int],
                 config: Optional[EngineConfig] = None,
                 index_closes: Optional[Dict[int, float]] = None,
                 universe: Optional["UniverseService"] = None,
                 security_master: Optional[SecurityMaster] = None,
                 seed: int = 42):
        self.store = store
        self.config = config or EngineConfig()
        self.calendar = TradingCalendar(list(calendar_days))
        self.index_closes: Dict[int, float] = dict(index_closes or {})
        self.index_series = pd.Series(self.index_closes).sort_index() if self.index_closes else pd.Series(dtype=float)
        self.universe = universe
        self.security_master = security_master or SecurityMaster()
        self.seed = seed
        self.signals: List[Signal] = []
        self._signal_seq = 0
        self._intent_seq = 0
        self._target_seq = 0
        # built in run()
        self.clock: Optional[TradingClock] = None
        self.ledger: Optional[PortfolioLedger] = None
        self.event_log: Optional[BacktestEventLog] = None
        self.order_manager: Optional[OrderManager] = None
        self.broker: Optional[BrokerSimulator] = None
        self.risk: Optional[RiskManager] = None
        self.sizer: Optional[PositionSizer] = None
        self.lot_model: Optional[LotSizeModel] = None
        self.context: Optional[BacktestRunContext] = None
        self.result: Optional[EngineResult] = None
        self._exit_order_lots: Dict[str, str] = {}
        self._pending_exit_lots: Dict[str, str] = {}

    # ---------- setup ----------
    def add_signal(self, sig: Signal):
        self.signals.append(sig)

    def add_signals(self, sigs: Iterable[Signal]):
        self.signals.extend(sigs)

    def _build(self):
        self.clock = TradingClock(self.calendar, mode=self.config.mode)
        self.ledger = PortfolioLedger(initial_cash=self.config.initial_cash)
        self.ledger.corporate_action_guard = self.config.corporate_action_guard
        self.event_log = BacktestEventLog()
        self.order_manager = OrderManager(self.event_log)
        fee = ChinaAStockFeeModel(
            commission_rate=self.config.commission_rate,
            min_commission=self.config.min_commission,
            stamp_tax_rate=self.config.stamp_tax_rate,
        )
        slip = FixedBpsSlippage(self.config.slippage_bps)
        self.slippage = slip
        if self.config.fill_model is not None:
            fill_model = self.config.fill_model
        elif self.config.mode == "5MIN":
            fill_model = FiveMinuteFillModel()
        else:
            fill_model = DailyBarFillModel()
        risk_cfg = RiskConfig(
            max_positions=self.config.max_positions,
            max_position_weight=self.config.max_position_weight,
        )
        self.risk = RiskManager(self.ledger, risk_cfg, universe=self.universe)
        self.broker = BrokerSimulator(
            store=self.store, clock=self.clock, ledger=self.ledger, order_manager=self.order_manager,
            fee_model=fee, slippage_model=slip, fill_model=fill_model,
            price_limit_model=ChinaPriceLimitModel(self.security_master),
            suspension_model=SuspensionModel(),
            risk_manager=self.risk, lot_size=self.config.lot_size,
            partial_fill=self.config.partial_fill,
            index_ok_fn=self._index_ok,
        )
        self.sizer = FixedSlotSizer(max_positions=self.config.max_positions)
        self.lot_model = LotSizeModel(buy_unit=self.config.lot_size, sell_unit=1)
        strategy_hash = self.config.strategy_hash or stable_hash([
            {"strategy_id": s.strategy_id, "signal_id": s.signal_id,
             "symbol": s.symbol, "generated_at": str(s.generated_at),
             "direction": s.direction.value} for s in self.signals
        ])
        data_manifest_hash = self.config.data_manifest_hash
        manifest_path = Path("data/market_raw/baostock/5m/manifest.jsonl")
        if not data_manifest_hash and manifest_path.exists():
            data_manifest_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        universe_version_hash = self.config.universe_version_hash or stable_hash(sorted(self.store.symbols()))
        feature_version_hash = self.config.feature_version_hash or stable_hash({"feature_price_mode": self.config.feature_price_mode})
        label_version_hash = self.config.label_version_hash or stable_hash("label-semantics-registry-v1")
        calendar_version = self.config.calendar_version or stable_hash(self.calendar.trading_days)
        run_manifest = build_run_manifest(
            config_hash=self._config_hash(), strategy_hash=strategy_hash,
            data_manifest_hash=data_manifest_hash or "MISSING_DATA_MANIFEST",
            universe_version_hash=universe_version_hash,
            feature_version_hash=feature_version_hash,
            label_version_hash=label_version_hash,
            execution_model_version=self.config.execution_model_version,
            fee_model_version=type(fee).__name__,
            slippage_model_version=type(slip).__name__,
            calendar_version=calendar_version,
        )
        self.context = BacktestRunContext(
            config_hash=self._config_hash(),
            random_seed=self.seed,
            start_time=str(self.clock.events[0].timestamp) if self.clock.events else None,
            end_time=str(self.clock.events[-1].timestamp) if self.clock.events else None,
            fee_model=type(fee).__name__,
            slippage_model=type(slip).__name__,
            fill_model=type(fill_model).__name__,
            mode=self.config.mode,
            code_commit=run_manifest["code_commit"],
            dirty_worktree=run_manifest["dirty_worktree"],
            strategy_hash=run_manifest["strategy_hash"],
            data_manifest_hash=run_manifest["data_manifest_hash"],
            universe_version_hash=run_manifest["universe_version_hash"],
            feature_version_hash=run_manifest["feature_version_hash"],
            label_version_hash=run_manifest["label_version_hash"],
            execution_model_version=run_manifest["execution_model_version"],
            fee_model_version=run_manifest["fee_model_version"],
            slippage_model_version=run_manifest["slippage_model_version"],
            calendar_version=run_manifest["calendar_version"],
            manifest_hash=run_manifest["manifest_hash"],
            reproducibility_status=run_manifest["reproducibility_status"],
        )
        if self.config.persist_run_manifest:
            write_immutable_run_manifest(
                Path(self.config.run_manifest_root) / f"{run_manifest['manifest_hash']}.json",
                run_manifest,
            )
        self._signal_seq = 0
        self._intent_seq = 0
        self._target_seq = 0
        self._exit_order_lots = {}
        self._pending_exit_lots = {}

    def _config_hash(self) -> str:
        raw = json.dumps(self.config.__dict__, sort_keys=True, default=str)
        return hashlib.sha1(raw.encode()).hexdigest()[:12]

    def _index_ok(self, d: int) -> bool:
        """P0 修复：只能使用严格早于 d 的指数收盘价。"""
        if not self.config.enable_index_filter or not self.config.index_filter_enabled:
            return True
        if self.index_series.empty:
            return True
        pos = self.index_series.index.searchsorted(int(d), side="left") - 1
        if pos < 0:
            return True
        prev_close = float(self.index_series.iloc[pos])
        ma = float(self.index_series.iloc[: pos + 1].tail(20).mean())
        return prev_close >= ma

    def _next_ids(self, prefix: str) -> str:
        self._signal_seq += 1
        return f"{prefix}-{self._signal_seq:06d}"

    # ---------- mark to market ----------
    def _mark_to_market(self, ts: pd.Timestamp, kind: EventKind):
        ts = ensure_aware(ts)
        d = date_key(ts)
        price_field = "close"
        if kind == EventKind.SESSION_OPEN:
            price_field = "open"
        elif kind in (EventKind.BAR_CLOSE, EventKind.SESSION_CLOSE, EventKind.AFTER_CLOSE):
            price_field = "close"
        for sym in set(p.symbol for p in self.ledger.positions.values() if p.quantity > 0):
            if self.config.mode == "5MIN":
                # Intraday valuation may only use the latest minute bar visible at ts.
                bar = self.store.get_minute_bar_at_or_before(sym, ts)
                if bar is not None and float(bar.get("close", 0.0)) > 0:
                    self.ledger.mark_to_market(sym, float(bar["close"]), ts)
                continue
            bar = self.store.get_daily_bar(sym, d, price_mode="raw")
            if bar is not None:
                px = float(bar.get(price_field, float(bar.get("close", 0.0))))
                if px > 0:
                    self.ledger.mark_to_market(sym, px, ts)

    # ---------- signal -> intent -> order ----------
    def _signal_to_intent(self, sig: Signal) -> Optional[OrderIntent]:
        self._intent_seq += 1
        if sig.direction == Side.BUY:
            return OrderIntent(
                intent_id=f"intent-{self._intent_seq:06d}",
                signal_id=sig.signal_id,
                strategy_id=sig.strategy_id,
                symbol=sig.symbol,
                side=Side.BUY,
                target_type=TargetType.WEIGHT,
                target_value=1.0 / max(1, self.config.max_positions),
                created_at=sig.generated_at,
                execution_policy=sig.execution_policy,
                metadata=sig.metadata,
            )
        return OrderIntent(
            intent_id=f"intent-{self._intent_seq:06d}",
            signal_id=sig.signal_id,
            strategy_id=sig.strategy_id,
            symbol=sig.symbol,
            side=Side.SELL,
            target_type=TargetType.CLEAR,
            target_value=0.0,
            created_at=sig.generated_at,
            execution_policy=sig.execution_policy,
            metadata=sig.metadata,
        )

    def _submit_intent(self, intent: OrderIntent, ts: pd.Timestamp):
        ts = ensure_aware(ts)
        position_id = None
        lot_id = str(intent.metadata.get("lot_id")) if intent.metadata.get("lot_id") else None
        reason_code = str(intent.metadata.get("reason_code", "ENTRY_SIGNAL" if intent.side == Side.BUY else "OTHER"))
        if intent.side == Side.BUY:
            # 用当前日线开盘价/收盘价做 sizing 参考；成交前还会 risk + fill 校验
            d = date_key(ts)
            bar = self.store.get_daily_bar(intent.symbol, d, price_mode="raw")
            ref = float(bar["open"]) if bar else 0.0
            if ref <= 0:
                return None
            target = PortfolioTarget(
                strategy_id=intent.strategy_id, symbol=intent.symbol,
                target_type=intent.target_type, target_value=intent.target_value,
                generated_at=ts, reason="signal", priority=intent.priority,
            )
            ref_slipped = self.slippage.apply("BUY", ref)
            qty = self.sizer.size_buy(target, self.ledger, ref_slipped, self.lot_model,
                                      max_positions=self.config.max_positions)
            if qty <= 0:
                return None
        else:
            if lot_id:
                lot = self.ledger.lots.get(lot_id)
                if lot is None or lot.remaining_quantity <= 0:
                    return None
                qty = int(intent.metadata.get("quantity", lot.remaining_quantity))
                qty = min(qty, lot.remaining_quantity)
                position_id = lot.position_id
            else:
                qty = self.ledger.position_qty(intent.strategy_id, intent.symbol)
                if qty <= 0:
                    return None
                pos = self.ledger.get_position(intent.strategy_id, intent.symbol)
                position_id = pos.position_id if pos else None
            if qty <= 0:
                return None
        order = Order(
            order_id="",
            strategy_id=intent.strategy_id,
            intent_id=intent.intent_id,
            signal_id=intent.signal_id,
            symbol=intent.symbol,
            side=intent.side,
            quantity=qty,
            created_at=ts,
            status=None,  # type: ignore
            time_in_force=TimeInForce.DAY,
            filled_quantity=0,
            remaining_quantity=qty,
            reason=intent.signal_id,
            priority=intent.priority,
            position_id=position_id,
            lot_id=lot_id,
            reason_code=reason_code,
            metadata=dict(intent.metadata),
        )
        self.order_manager.create_order(order, ts)
        # The engine owns the full lifecycle: CREATED -> SUBMITTED -> ACCEPTED.
        self.order_manager.submit(order, ts)
        return order

    def _sync_lot_contract_fields(self):
        """Attach the shared market-session clock to every actual FIFO lot."""
        for lot in self.ledger.lots.values():
            entry_session = date_key(lot.buy_time)
            try:
                entry_index = self.calendar.date_index(entry_session)
            except KeyError:
                continue
            lot.entry_session = entry_session
            lot.entry_session_index = entry_index
            next_session = self.calendar.next_day(entry_session)
            if next_session is not None:
                lot.sellable_from_session = next_session
                lot.sellable_from_session_index = entry_index + 1
            if lot.entry_price <= 0 and lot.quantity > 0:
                lot.entry_price = float(lot.cost / lot.quantity)

    def _submit_exit_decision(self, decision: PortfolioExitDecision, ts: pd.Timestamp):
        if decision.state == "SELL_PENDING":
            return None
        lot = self.ledger.lots.get(decision.lot_id)
        if lot is None or lot.remaining_quantity <= 0:
            return None
        intent = OrderIntent(
            intent_id=f"intent-exit-{self._intent_seq + 1:06d}",
            signal_id=f"EXIT:{decision.candidate_id}:{decision.lot_id}:{decision.trade_session}",
            strategy_id=decision.portfolio_id,
            symbol=decision.symbol,
            side=Side.SELL,
            target_type=TargetType.QUANTITY,
            target_value=float(lot.remaining_quantity),
            created_at=ts,
            execution_policy=ExecutionPolicy.NEXT_SESSION_OPEN,
            metadata={
                "lot_id": decision.lot_id,
                "quantity": int(lot.remaining_quantity),
                "reason_code": decision.reason_code,
                "exit_state": decision.state,
                "candidate_id": decision.candidate_id,
            },
        )
        self.event_log.log(BacktestEvent(
            event_id="",
            timestamp=ts,
            event_type="EXIT_DECISION",
            strategy_id=decision.portfolio_id,
            symbol=decision.symbol,
            signal_id=f"EXIT:{decision.candidate_id}:{decision.lot_id}:{decision.trade_session}",
            lot_id=decision.lot_id,
            payload={
                "candidate_id": decision.candidate_id,
                "state": decision.state,
                "reason_code": decision.reason_code,
                "trade_session": decision.trade_session,
                "trade_session_index": decision.trade_session_index,
            },
        ))
        order = self._submit_intent(intent, ts)
        if order is not None:
            lot.exit_state = "EXIT_DUE"
            lot.exit_reason = decision.reason_code
            self._exit_order_lots[order.order_id] = decision.lot_id
        return order

    def _refresh_exit_states(self):
        for order_id, lot_id in list(self._exit_order_lots.items()):
            order = self.order_manager.orders.get(order_id)
            lot = self.ledger.lots.get(lot_id)
            if order is None or lot is None:
                continue
            if order.status.value == "FILLED":
                if lot.remaining_quantity <= 0:
                    lot.exit_state = "CLOSED"
                    self._pending_exit_lots.pop(lot_id, None)
                else:
                    lot.exit_state = "PARTIALLY_FILLED"
                    self._pending_exit_lots[lot_id] = "PARTIAL_FILL_RETRY"
            elif order.status.value in {"REJECTED", "EXPIRED", "CANCELLED"} and lot.remaining_quantity > 0:
                lot.exit_state = "SELL_PENDING"
                lot.exit_reason = order.reason_code or "BROKER_REJECTION"
                self._pending_exit_lots[lot_id] = lot.exit_reason

    def _retry_pending_exits(self, ts: pd.Timestamp):
        """Create the next-session retry without requiring a new alpha signal."""
        for lot_id in sorted(self._pending_exit_lots):
            lot = self.ledger.lots.get(lot_id)
            if lot is None or lot.remaining_quantity <= 0 or self.order_manager.has_open_sell_for_lot(lot_id):
                continue
            intent = OrderIntent(
                intent_id=f"intent-retry-{self._intent_seq + 1:06d}",
                signal_id=f"RETRY_EXIT:{lot_id}:{date_key(ts)}",
                strategy_id=lot.strategy_id,
                symbol=lot.symbol,
                side=Side.SELL,
                target_type=TargetType.QUANTITY,
                target_value=float(lot.remaining_quantity),
                created_at=ts,
                execution_policy=ExecutionPolicy.NEXT_SESSION_OPEN,
                metadata={
                    "lot_id": lot_id,
                    "quantity": int(lot.remaining_quantity),
                    "reason_code": "SELL_RETRY",
                    "exit_state": "SELL_PENDING",
                },
            )
            order = self._submit_intent(intent, ts)
            if order is not None:
                self._exit_order_lots[order.order_id] = lot_id

    def _submit_exit_decisions(self, decisions: Iterable[PortfolioExitDecision], ts: pd.Timestamp):
        for decision in decisions:
            if decision.state == "SELL_PENDING":
                continue
            if self.order_manager.has_open_sell_for_lot(decision.lot_id):
                continue
            self._submit_exit_decision(decision, ts)

    def _holding_days(self, buy_date: int, current_date: int) -> int:
        try:
            return self.calendar.date_index(int(current_date)) - self.calendar.date_index(int(buy_date))
        except KeyError:
            return 0

    def _submit_forced_exits(self, ts: pd.Timestamp):
        if self.config.max_holding_days <= 0:
            return
        d = date_key(ts)
        for pos in list(self.ledger.positions.values()):
            if pos.quantity <= 0 or pos.opened_at is None:
                continue
            if self._holding_days(date_key(pos.opened_at), d) < self.config.max_holding_days:
                continue
            if self.order_manager.has_open_sell_for_position(pos.position_id):
                continue
            intent = OrderIntent(
                intent_id=f"intent-exit-{self._intent_seq:06d}",
                signal_id=f"EXIT:{pos.position_id}",
                strategy_id=pos.strategy_id,
                symbol=pos.symbol,
                side=Side.SELL,
                target_type=TargetType.CLEAR,
                target_value=0.0,
                created_at=ts,
                execution_policy=ExecutionPolicy.NEXT_SESSION_OPEN,
                metadata={"reason": "max_hold"},
            )
            self._intent_seq += 1
            self._submit_intent(intent, ts)

    # ---------- run ----------
    def run(
        self,
        strategy_fn: Optional[Callable[[AsOfDataView, pd.Timestamp, int], List[Signal]]] = None,
        exit_fn: Optional[Callable[[AsOfDataView, pd.Timestamp, int, PortfolioLedger], Iterable[PortfolioExitDecision]]] = None,
    ) -> "EngineResult":
        self._build()
        pending_signals = sorted(self.signals, key=lambda s: (s.generated_at, -s.score, s.signal_id))
        for ev in self.clock.events:
            ts = ev.timestamp
            self._sync_lot_contract_fields()
            # 1. Market State Update
            self._mark_to_market(ts, ev.kind)
            # 2. Pending Order Fill
            self.broker.process_orders(ev.kind, ts)
            self._sync_lot_contract_fields()
            self._refresh_exit_states()
            # 3. Data Visibility Update & Strategy Signal
            if (strategy_fn is not None or exit_fn is not None) and ev.kind in (EventKind.BAR_CLOSE, EventKind.AFTER_CLOSE):
                view = AsOfDataView(self.store, ts, feature_price_mode=self.config.feature_price_mode)
                if exit_fn is not None:
                    self._submit_exit_decisions(exit_fn(view, ts, date_key(ts), self.ledger), ts)
                if strategy_fn is None:
                    generated_signals = []
                else:
                    generated_signals = strategy_fn(view, ts, date_key(ts))
                for sig in generated_signals:
                    self.add_signal(sig)
                    pending_signals.append(sig)
                pending_signals.sort(key=lambda s: (s.generated_at, -s.score, s.signal_id))
            # 3.5 Forced exits (max_holding_days) at BAR_CLOSE, before new buy signals
            if ev.kind == EventKind.BAR_CLOSE:
                self._submit_forced_exits(ts)
                self._retry_pending_exits(ts)
            # 4. Signal -> Intent -> Order for signals whose generated_at <= now
            while pending_signals and ensure_aware(pending_signals[0].generated_at) <= ts:
                sig = pending_signals.pop(0)
                intent = self._signal_to_intent(sig)
                if intent is not None:
                    self._submit_intent(intent, ts)
                    self.event_log.log(BacktestEvent(
                        event_id="", timestamp=ts, event_type="SIGNAL_SUBMITTED",
                        strategy_id=sig.strategy_id, symbol=sig.symbol,
                        signal_id=sig.signal_id, intent_id=intent.intent_id,
                    ))
            # 5. Ledger Mark
            self.ledger.snapshot(ts)
        # end of session: expire DAY orders at last event
        for order in list(self.order_manager.open_orders()):
            if order.time_in_force == TimeInForce.DAY:
                self.order_manager.expire(order, self.clock.events[-1].timestamp, "end of backtest")
        # DAY orders can be expired by the end-of-sample cleanup itself.  Refresh
        # portfolio exit states after that cleanup so an unfilled due SELL is
        # explicitly represented as SELL_PENDING in final evidence.
        self._sync_lot_contract_fields()
        self._refresh_exit_states()
        self.ledger.snapshot(self.clock.events[-1].timestamp)
        self.result = EngineResult(
            ledger=self.ledger, event_log=self.event_log, orders=self.order_manager,
            clock=self.clock, context=self.context, signals=self.signals,
        )
        return self.result


@dataclass
class EngineResult:
    ledger: PortfolioLedger
    event_log: BacktestEventLog
    orders: OrderManager
    clock: TradingClock
    context: BacktestRunContext
    signals: List[Signal] = field(default_factory=list)

    @property
    def trades(self) -> List:
        return self.ledger.trades

    @property
    def equity_curve(self) -> List:
        return self.ledger.snapshots

    def summary(self) -> dict:
        snaps = self.ledger.snapshots
        return {
            "engine_version": self.context.engine_version,
            "mode": self.context.mode,
            "initial_cash": self.ledger.initial_cash,
            "final_equity": snaps[-1].equity if snaps else self.ledger.initial_cash,
            "total_return": (snaps[-1].equity / self.ledger.initial_cash - 1) if snaps else 0.0,
            "n_trades": len(self.ledger.valid_trades),
            "unsupported_trade_count": self.ledger.unsupported_trade_count,
            "run_certification": self.ledger.certification_status,
            "realized_pnl": self.ledger.realized_pnl,
            "total_fees": self.ledger.total_fees,
            "turnover": self.ledger.turnover,
            "n_events": len(self.event_log),
        }
