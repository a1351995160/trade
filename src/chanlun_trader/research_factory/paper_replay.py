"""有界Paper工程回放；仅操作显式提供的输入与隔离模拟账本，不授予真实运行权限。"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path

from ..engine.time_types import EventKind
from .common import canonical_json, stable_hash
from .daily_plan import preview_daily_plan
from .source_dependencies import SOURCE_ROOT, load_corrected_module


def _immutable(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(canonical_json(value) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        if path.read_text(encoding="utf-8") != canonical_json(value) + "\n":
            raise ValueError("PAPER_IMMUTABLE_CONFLICT")


def read_paper_archive(output_root: str | Path) -> dict:
    """只读核对持久摘要，供页面使用；不构造引擎或执行恢复。"""
    root = Path(output_root)
    if not root.is_absolute() or root.resolve() != root:
        raise ValueError("PAPER_EXPLICIT_ROOT_REQUIRED")
    if not (root / "header.json").exists():
        if (root / "events").exists():
            raise ValueError("PAPER_HEADER_MISSING")
        return {"status": "NO_SESSION", "time_basis": "SIMULATED_TIME", "real_observation_days": 0}
    def read(path):
        if path.resolve() != path:
            raise ValueError("PAPER_ARCHIVE_LINKED_FILE")
        return json.loads(path.read_text(encoding="utf-8"))
    header = read(root / "header.json")
    if (header.get("schema_version") != "paper-engineering-replay-v1"
            or header.get("real_execution_authorized") is not False
            or header.get("time_basis") != "SIMULATED_TIME" or header.get("real_observation_days") != 0):
        raise ValueError("PAPER_HEADER_INVALID")
    previous, latest, plan = stable_hash(header), None, None
    paths = sorted((root / "events").glob("*.json"))
    if len(paths) > header["total_events"]:
        raise ValueError("PAPER_EVENT_COUNT_CONFLICT")
    for index, path in enumerate(paths):
        item = read(path)
        if path.name != f"{index:08d}.json" or item.get("index") != index:
            raise ValueError("PAPER_EVENT_SEQUENCE_GAP")
        if (item.get("previous_hash") != previous or item.get("state_hash") != stable_hash(item.get("state"))
                or item.get("record_hash") != stable_hash({key: value for key, value in item.items() if key != "record_hash"})):
            raise ValueError("PAPER_ARCHIVE_HASH_CONFLICT")
        previous, latest = item["record_hash"], item
        if item.get("plan") is not None:
            plan = item["plan"]
    return {"status": "HISTORICAL_REPLAY", "header": header, "completed_events": len(paths),
            "total_events": header["total_events"], "last_record_hash": previous,
            "state": latest["state"] if latest else None, "last_plan": plan,
            "time_basis": "SIMULATED_TIME", "real_observation_days": 0, "real_execution_authorized": False}


class PaperReplaySessionV1:
    """逐事件重放持久记录；这里的Paper账户不属于真实账户或研究准入权威。"""
    def __init__(self, input_root, output_root, record, contract, policy, inputs):
        self.input_root, self.output_root = Path(input_root), Path(output_root)
        for path in (self.input_root, self.output_root):
            if not path.is_absolute() or path.resolve() != path:
                raise ValueError("PAPER_EXPLICIT_ROOT_REQUIRED")
        if (self.output_root.is_relative_to(self.input_root) or self.input_root.is_relative_to(self.output_root)
                or self.output_root.is_relative_to(SOURCE_ROOT) or SOURCE_ROOT.is_relative_to(self.output_root)):
            raise ValueError("PAPER_SEPARATE_OUTPUT_REQUIRED")
        if (contract.reconstruct_candidate().to_dict() != record.to_dict()
                or contract.content_hash != inputs["input_diagnostics"]["contract_hash"]):
            raise ValueError("PAPER_CONTRACT_IDENTITY_CONFLICT")
        self.record, self.contract, self.policy, self.inputs = record, contract, policy, inputs
        corrected = load_corrected_module()
        self.engine, self.strategy_fn, self.exit_fn, _, _, _ = corrected.prepare_corrected_run(record, inputs, policy)
        self.engine._build()
        self.pending_signals = []
        self.completed_events = 0
        self.recovery_required = False
        self.last_plan = None
        self.header = {"schema_version": "paper-engineering-replay-v1", "candidate_id": contract.candidate_id,
            "contract_hash": contract.content_hash, "policy_hash": stable_hash(policy.to_dict()),
            "total_events": len(self.engine.clock.events),
            "input_identity": inputs["input_diagnostics"]["input_identity"],
            "source_identity": {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in {
                "corrected": Path(corrected.__file__), "helper": Path(corrected.legacy.__file__),
                "daily_plan": Path(__file__).with_name("daily_plan.py"), "paper_replay": Path(__file__),
                "strategy_semantic": SOURCE_ROOT / "src/chanlun_trader/research/strategy_semantic.py",
                **{str(path.relative_to(SOURCE_ROOT)).replace("\\", "/"): path
                   for path in (SOURCE_ROOT / "src/chanlun_trader/engine").glob("*.py")},
            }.items()}, "time_basis": "SIMULATED_TIME", "real_observation_days": 0,
            "real_execution_authorized": False, "strategy_usage_qualified": False}
        self.session_id = "PAPER_" + stable_hash(self.header)
        self.previous_hash = stable_hash(self.header)
        header_path = self.output_root / "header.json"
        if header_path.exists():
            if json.loads(header_path.read_text(encoding="utf-8")) != self.header:
                raise ValueError("PAPER_INPUT_OR_SOURCE_CHANGED")
            self._recover()
        elif (self.output_root / "events").exists():
            raise ValueError("PAPER_HEADER_MISSING")

    def _state(self) -> dict:
        ledger = self.engine.ledger
        return {"cash": ledger.cash, "reserved_cash": ledger.reserved_cash, "fees": ledger.total_fees,
            "positions": {key: asdict(value) for key, value in ledger.positions.items()},
            "lots": {key: asdict(value) for key, value in ledger.lots.items()},
            "orders": {key: asdict(value) for key, value in self.engine.order_manager.orders.items()},
            "trades": [asdict(value) for value in ledger.trades],
            "event_hash": stable_hash(self.engine.event_log.to_records()),
            "invariant_errors": ledger.check_invariants()}

    def _step(self) -> dict:
        index = self.completed_events
        event = self.engine.clock.events[index]
        self.engine._process_clock_event(event, self.pending_signals, self.strategy_fn, self.exit_fn)
        plan = None
        if event.kind == EventKind.BAR_CLOSE:
            plan = preview_daily_plan(self.record, self.contract, self.policy, self.inputs,
                                      self.engine.ledger, plan_at=event.timestamp)
            self.last_plan = plan
        if index + 1 == len(self.engine.clock.events):
            self.engine._finish_run()
        state = self._state()
        if state["invariant_errors"]:
            raise ValueError("PAPER_LEDGER_INVARIANT_FAILED")
        record = {"index": index, "event_kind": event.kind.value, "timestamp": str(event.timestamp),
            "previous_hash": self.previous_hash, "state_hash": stable_hash(state), "state": state, "plan": plan}
        record["record_hash"] = stable_hash(record)
        self.completed_events += 1
        self.previous_hash = record["record_hash"]
        return record

    def _recover(self) -> None:
        paths = sorted((self.output_root / "events").glob("*.json"))
        if len(paths) > len(self.engine.clock.events):
            raise ValueError("PAPER_EVENT_COUNT_CONFLICT")
        for index, path in enumerate(paths):
            if path.name != f"{index:08d}.json":
                raise ValueError("PAPER_EVENT_SEQUENCE_GAP")
            expected = json.loads(path.read_text(encoding="utf-8"))
            actual = json.loads(canonical_json(self._step()))
            if expected != actual:
                raise ValueError("PAPER_REPLAY_RECONCILIATION_FAILED")

    def advance(self, event_count: int) -> dict:
        """event_count是累计目标，重复请求不重复处理，不按新ID重试。"""
        if self.recovery_required:
            raise ValueError("PAPER_RECOVERY_REQUIRED")
        if type(event_count) is not int or not self.completed_events <= event_count <= len(self.engine.clock.events):
            raise ValueError("PAPER_EVENT_RANGE_INVALID")
        _immutable(self.output_root / "header.json", self.header)
        while self.completed_events < event_count:
            try:
                record = self._step()
                _immutable(self.output_root / "events" / f"{record['index']:08d}.json", record)
            except Exception:
                self.recovery_required = True
                raise
        return self.status()

    def status(self) -> dict:
        return {"session_id": self.session_id, "completed_events": self.completed_events,
            "total_events": len(self.engine.clock.events), "last_record_hash": self.previous_hash,
            "state": self._state(), "last_plan": self.last_plan, "time_basis": "SIMULATED_TIME",
            "real_observation_days": 0, "real_execution_authorized": False,
            "status": "RECOVERY_REQUIRED" if self.recovery_required else "REPLAY_COMPLETE" if self.completed_events == len(self.engine.clock.events) else "PAUSED"}
