"""确定性历史回测的原子事件回执与中断后重放恢复。"""
from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import tempfile

from .common import stable_hash
from chanlun_trader.engine.time_types import EventKind


VERSION = "ENGINE_REPLAY_RECOVERY_V1"


class ReplayInterrupted(RuntimeError):
    pass


def economic_state(engine) -> dict:
    ledger = engine.ledger
    material = {
        "cash": ledger.cash, "reserved_cash": ledger.reserved_cash,
        "positions": {key: asdict(value) for key, value in sorted(ledger.positions.items())},
        "lots": {key: asdict(value) for key, value in sorted(ledger.lots.items())},
        "trades": [asdict(value) for value in ledger.trades],
        "snapshots": [asdict(value) for value in ledger.snapshots],
        "orders": {key: asdict(value) for key, value in sorted(engine.order_manager.orders.items())},
        "open_order_ids": sorted(engine.order_manager._open_ids),
        "events": engine.event_log.to_records(),
        "ledger_counters": [ledger._pos_counter, ledger._lot_counter, ledger._trade_counter],
        "engine_counters": [engine._signal_seq, engine._intent_seq, engine._target_seq],
        "order_counters": [engine.order_manager._seq, engine.order_manager._id_counter],
    }
    for name in ("action_income", "dividend_tax_withheld", "entitlements",
                 "receivables", "action_audit", "dividend_lots", "account_history"):
        if hasattr(ledger, name):
            material[name] = getattr(ledger, name)
        elif hasattr(engine, name):
            material[name] = getattr(engine, name)
    for name in ("applied", "payments"):
        if hasattr(ledger, name):
            material[name] = sorted(getattr(ledger, name))
    return material


def _read_receipt(path: Path, input_identity: str) -> dict | None:
    if not path.exists():
        return None
    receipt = json.loads(path.read_text(encoding="utf-8"))
    body = {key: value for key, value in receipt.items() if key != "receipt_hash"}
    if (receipt.get("receipt_hash") != stable_hash(body) or receipt.get("version") != VERSION
            or receipt.get("input_identity") != input_identity):
        raise ValueError("ENGINE_REPLAY_RECEIPT_OR_INPUT_CONFLICT")
    return receipt


def _write_receipt(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({**body, "receipt_hash": stable_hash(body)},
                         ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    descriptor, temporary = tempfile.mkstemp(prefix=".replay_", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def run_recoverable(engine, checkpoint_path: Path, *, input_identity: str,
                    stop_after_date: int | None = None):
    """重放并核对前缀，随后继续；仅支持无外部副作用的预置 Signal 回测。"""
    if not input_identity or engine.fill_hook is not None:
        raise ValueError("ENGINE_REPLAY_REQUIRES_PURE_PRELOADED_SIGNALS")
    path = Path(checkpoint_path)
    previous = _read_receipt(path, input_identity)
    engine._build()
    pending = sorted(engine.signals, key=lambda signal:
                     (signal.generated_at, -signal.score, signal.signal_id))
    prefix_verified = previous is None
    for index, event in enumerate(engine.clock.events):
        engine._process_clock_event(event, pending)
        if event.kind != EventKind.AFTER_CLOSE:
            continue
        state_hash = stable_hash(economic_state(engine))
        if previous is not None and index == previous["event_index"]:
            if (state_hash != previous["state_hash"]
                    or str(event.timestamp) != previous["event_timestamp"]):
                raise ValueError("ENGINE_REPLAY_PREFIX_DIVERGED")
            prefix_verified = True
        if not prefix_verified:
            continue
        body = {"version": VERSION, "input_identity": input_identity,
                "event_index": index, "event_timestamp": str(event.timestamp),
                "state_hash": state_hash, "complete": False}
        _write_receipt(path, body)
        if stop_after_date == event.date:
            raise ReplayInterrupted(f"ENGINE_REPLAY_INTERRUPTED_AFTER:{event.date}")
    if not prefix_verified:
        raise ValueError("ENGINE_REPLAY_CHECKPOINT_OUTSIDE_CLOCK")
    result = engine._finish_run()
    final_hash = stable_hash(economic_state(engine))
    if previous is not None and previous["complete"] and final_hash != previous["final_hash"]:
        raise ValueError("ENGINE_REPLAY_FINAL_DIVERGED")
    body = {"version": VERSION, "input_identity": input_identity,
            "event_index": len(engine.clock.events) - 1,
            "event_timestamp": str(engine.clock.events[-1].timestamp),
            "state_hash": state_hash, "complete": True, "final_hash": final_hash}
    _write_receipt(path, body)
    return result
